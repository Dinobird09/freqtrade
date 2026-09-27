"""Human review pack: the trade-by-trade list a person must inspect before any adoption.

Adoption step 3 requires a human to review the ACTUAL trades, not just summary statistics.
:func:`write_review_pack` writes two files into ``out_dir``:

- ``trades_review.csv``: one row per trade in entry order (``entry_ts``, then ``trade_id``)
  with the numbers a reviewer needs to check a trade by hand (planned RR by price AND net
  of costs, the pair's risk cap, notional, hold time), the trade's features, the machine
  sanity flags, and EMPTY ``reviewer_ok`` (Y/N) and ``reviewer_note`` columns for the
  reviewer's verdict.
- ``trades_review.md``: header and counts, the statement that EVERY row must be inspected,
  a per-window summary (TRAIN | TEST side by side when a walk-forward split is given), the
  table of all trades, the flagged trades, the rule-denial counts and a sign-off block.

:func:`auto_flags` are sanity checks that HELP the reviewer; they never replace the review.
An unflagged trade is not an approved trade. Each flag is ``"<code>: <detail>"`` where
``<code>`` is one of :data:`FLAG_CODES`.

Cost-aware risk (CONTRACT.md v2 A1): ``risk_amount = qty * L_u`` with the all-in loss per
unit ``L_u = (entry - S_x) + fee*entry + fee*S_x`` and ``S_x = stop * (1 - slippage)``, and
the target nets exactly ``reward_risk`` times that risk. The sheet therefore reports two
planned reward:risk numbers:

- ``planned_rr_price = (target - entry) / (entry - stop)`` (chart distances), and
- ``planned_rr_net = (qty*(target - entry) - fee*qty*(entry + target)) / risk_amount``
  (what a TP actually earns in R after both fees).

A trade is flagged if EITHER is below ``cfg.reward_risk`` (the 2:1 minimum holds net of
costs). A clean stop-out is exactly -1R, so losses worse than -1.05R (gap-through or
slippage beyond the cost model) and wins above ``reward_risk + 0.01`` R are flagged.

Window labels: with ``split_ts`` a trade is ``TRAIN`` if ``signal_ts < split_ts`` and
``TEST`` otherwise (entries are windowed by their signal candle, like the backtester);
without a split every trade is ``ALL``.

Row key (CONTRACT.md v3 C3): a row of ``trades_review.csv`` is identified by
``(window, trade_id)`` (:data:`REVIEW_KEY`). The key is unique within a pack:
:func:`write_review_pack` raises ``ValueError`` before writing anything if two trades share
it (``run_research`` offsets TEST ids past the TRAIN ids, so its ids are even unique across
windows). The adoption check reads the filled-in sheet back with :func:`load_review` and
matches the key set against the TRAIN and TEST journals (the TRAIN journal supplies the
``TRAIN`` keys, the TEST journal the ``TEST`` keys). ``load_review`` validates the header
written here and every ``reviewer_ok`` cell: ``Y`` (approved), ``N`` (rejected) or blank
(not reviewed yet); anything else (``y``, ``yes``, ``OK`` ...) is rejected with its line
number, so a verdict is never guessed.

Trade context from the candles (CONTRACT.md v4; ``write_review_pack(..., data=...)``, CLI
``--data-dir`` or ``--synthetic``). With the candles the trades were generated on, every
row also gets the numbers a reviewer needs to judge the trade against the chart. ``R`` below
is the planned all-in risk per unit, ``risk_amount / qty`` (``L_u`` under A1, the same R as
``r_multiple``); excursions are price distances, fees not included:

- ``mae_r = max(0, entry - lowest low) / R`` and ``mfe_r = max(0, highest high - entry) / R``
  over the fill candle through the exit candle, both inclusive. The order of high and low
  inside a candle is unknown, so the exit candle's extremes may come after the exit.
- ``stop_ref_time_utc`` / ``stop_ref_low`` / ``stop_ref_bar``: the candle whose low the R8
  stop sits behind, re-derived at the signal candle with ``structure.latest_confirmed_pivot``
  (``pivot``) or, without a confirmed pivot, the lowest low of the last
  ``cfg.fallback_lookback`` candles (``lookback_low``, oldest on ties), exactly as
  ``structure.find_stop`` picks it. ``stop_ref_bar`` is that candle's index relative to the
  signal candle (0 = the signal candle, -5 = five candles earlier; the ``bar`` column of the
  context CSV). ``structure.find_stop`` is re-run and must reproduce the recorded stop.
- ``exit_low`` and ``wick_depth_r = (stop - exit_low) / R`` for SL exits: how far the exit
  candle traded below the stop.
- ``context_csv`` / ``context_sha256``: ``context/<window>_<trade_id>.csv`` (relative to the
  pack) with the OHLCV candles from :data:`CONTEXT_BEFORE` (30) candles before the fill
  candle through the exit candle, extended further back when needed so the structure candle
  (and, for a pivot, its ``k`` left neighbours) is included. Columns :data:`CONTEXT_CSV_COLUMNS`;
  ``marks`` tags the structure, signal, entry and exit candles. :func:`load_review` checks
  every referenced file against its sha256, so the hash binding of ``trades_review.csv``
  also covers its context files.

Context flags (only with data): ``context_missing`` (no candle at the signal, fill or exit
time), ``context_mismatch`` (the candles contradict the recorded trade: a data gap at the
fill, an earlier candle already at the stop or target, an exit candle that never reached its
level), ``stop_mismatch`` (``structure.find_stop`` at the signal candle does not reproduce
the recorded stop or method) and ``deep_wick_stop``: a non-gap stop-out whose exit-candle
low sits more than :data:`DEEP_WICK_STOP_DISTANCES` (0.5) stop distances ``entry - stop``
below the stop. The touch fill at ``stop * (1 - slippage)`` is optimistic there; the flag
states the extra loss under the CONTRACT v4 D4 wick fill for k = 0.5 and 1. Without data the
context columns are blank, no ``context/`` files exist and the ``.md`` says so.

Header versions: the context columns sit between the ``f_<feature>`` columns and
``ml_prob``. :func:`load_review` also accepts a pack written before they existed (no context
columns at all); a header with only some of them, or in another order, is rejected.

Output is deterministic: stable row order, fixed-decimal number formatting, no timestamps
of the run itself. The summary reports expectancy (avg R) only; win rate is deliberately
not shown because it is never a target.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import math
import re
from bisect import bisect_left
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import NamedTuple

from .config import RULE_IDS, ConfigError, StrategyConfig
from .data import load_dataset
from .journal import FEATURE_PREFIX, iso_to_ms, ms_to_iso, read_journal
from .models import DAY_MS, EXIT_END, EXIT_SL, EXIT_TP, HOUR_MS, Candle, Trade
from .news import parse_time_utc
from .sizing import loss_per_unit, stop_fill_price
from .structure import find_stop, latest_confirmed_pivot
from .synthetic import WORLDS, make_world


CSV_NAME = "trades_review.csv"
MD_NAME = "trades_review.md"

WINDOW_TRAIN = "TRAIN"
WINDOW_TEST = "TEST"
WINDOW_ALL = "ALL"
WINDOWS = (WINDOW_TRAIN, WINDOW_TEST, WINDOW_ALL)

# The unique key of a review row (C3), and the verdicts a reviewer may enter.
REVIEW_KEY = ("window", "trade_id")
REVIEW_OK_YES = "Y"
REVIEW_OK_NO = "N"
REVIEW_OK_BLANK = ""  # not reviewed yet
REVIEW_OK_VALUES = (REVIEW_OK_YES, REVIEW_OK_NO, REVIEW_OK_BLANK)

# Keys of FeatureRow.ml_features(), sorted like the journal's f_<name> columns. They are
# always present so the header is stable; extra feature keys on trades are merged in sorted.
BASE_FEATURES = ("dist_regime_pct", "ema_gap_pct", "hour_utc", "rsi", "vol_ratio")

HEAD_COLUMNS: tuple[str, ...] = (
    "trade_id",
    "window",
    "pair",
    "variant",
    "signal_time_utc",
    "entry_time_utc",
    "exit_time_utc",
    "entry",
    "stop",
    "target",
    "stop_method",
    "planned_rr_price",
    "planned_rr_net",
    "risk_pct",
    "pair_cap_pct",
    "qty",
    "notional",
    "risk_amount",
    "exit_price",
    "exit_reason",
    "fees",
    "pnl",
    "r_multiple",
    "hold_h",
)
# Trade-context columns (CONTRACT.md v4), between the f_<feature> columns and the tail. They
# are always written; without candle data every cell is blank.
CONTEXT_COLUMNS: tuple[str, ...] = (
    "mae_r",
    "mfe_r",
    "stop_ref_time_utc",
    "stop_ref_low",
    "stop_ref_bar",
    "exit_low",
    "wick_depth_r",
    "context_csv",
    "context_sha256",
)
TAIL_COLUMNS: tuple[str, ...] = ("ml_prob", "notes", "auto_flags", "reviewer_ok", "reviewer_note")
REVIEWER_COLUMNS = ("reviewer_ok", "reviewer_note")
FLAG_SEPARATOR = "; "

# Per-trade OHLC context files: out_dir/context/<window>_<trade_id>.csv.
CONTEXT_DIR = "context"
CONTEXT_BEFORE = 30  # candles before the fill candle (the signal candle is the last of them)
CONTEXT_CSV_COLUMNS: tuple[str, ...] = (
    "bar",  # index relative to the signal candle (signal 0, fill 1, structure <= 0)
    "index",  # index in the pair's candle series as supplied
    "time_utc",
    "ts",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "marks",  # stop_ref_pivot | stop_ref_lookback_low, signal, entry, exit_<reason>
)
_CONTEXT_FILE_RE = re.compile(r"(TRAIN|TEST|ALL)_[0-9]+\.csv")
# A stop-out whose exit-candle low is more than this many stop distances (entry - stop) below
# the stop is flagged deep_wick_stop: there the touch fill model is optimistic (v4 D4).
DEEP_WICK_STOP_DISTANCES = 0.5
WICK_STRESS_KS = (0.5, 1.0)  # the D4 stress levels W5 runs, quoted in the flag

# Outcome thresholds (in R). With cost-aware sizing (A1) a clean SL fill at
# stop*(1-slippage) is exactly -1R and a TP is exactly +reward_risk R, so anything outside
# these narrow bands needs a human look.
LOSS_OUTLIER_R = -1.05  # worse than this: gap-through or slippage beyond the cost model
WIN_SLACK_R = 0.01  # a win above reward_risk + this is impossible under the exit model
GAP_SLACK_R = 0.05  # a loss this far beyond a normal cost-model stop-out is a gap/slippage
_REL_TOL = 1e-6
_ABS_TOL = 1e-9

FLAG_NON_FINITE = "non_finite"
FLAG_BAD_LEVELS = "bad_levels"
FLAG_RR_BELOW = "rr_below_min"
FLAG_RR_ABOVE = "rr_above_config"
FLAG_SIZE = "size_mismatch"
FLAG_PAIR = "pair_not_configured"
FLAG_RISK_CAP = "risk_above_cap"
FLAG_LOOKAHEAD = "lookahead"
FLAG_EXIT_BEFORE_ENTRY = "exit_before_entry"
FLAG_ZERO_HOLD = "zero_hold"
FLAG_MISSING_EXIT = "missing_exit"
FLAG_WINDOW_END = "window_end"
FLAG_LOSS_OUTLIER = "loss_outlier"
FLAG_WIN_TOO_LARGE = "win_too_large"
FLAG_R_MISMATCH = "r_mismatch"
FLAG_EXIT_MODEL = "exit_model_mismatch"
# Context flags: only produced when candles are supplied (see the module docstring).
FLAG_CONTEXT_MISSING = "context_missing"
FLAG_CONTEXT_MISMATCH = "context_mismatch"
FLAG_STOP_MISMATCH = "stop_mismatch"
FLAG_DEEP_WICK = "deep_wick_stop"
CONTEXT_FLAG_CODES: tuple[str, ...] = (
    FLAG_CONTEXT_MISSING,
    FLAG_CONTEXT_MISMATCH,
    FLAG_STOP_MISMATCH,
    FLAG_DEEP_WICK,
)
FLAG_CODES: tuple[str, ...] = (
    FLAG_NON_FINITE,
    FLAG_BAD_LEVELS,
    FLAG_RR_BELOW,
    FLAG_RR_ABOVE,
    FLAG_SIZE,
    FLAG_PAIR,
    FLAG_RISK_CAP,
    FLAG_LOOKAHEAD,
    FLAG_EXIT_BEFORE_ENTRY,
    FLAG_ZERO_HOLD,
    FLAG_MISSING_EXIT,
    FLAG_WINDOW_END,
    FLAG_LOSS_OUTLIER,
    FLAG_WIN_TOO_LARGE,
    FLAG_R_MISMATCH,
    FLAG_EXIT_MODEL,
    *CONTEXT_FLAG_CODES,
)

_EXIT_FIELDS = ("exit_ts", "exit_price", "exit_reason", "pnl", "r_multiple")
_NUMERIC_FIELDS = (
    "entry_price",
    "stop",
    "target",
    "qty",
    "risk_amount",
    "risk_pct",
    "exit_price",
    "fees",
    "pnl",
    "r_multiple",
)


# ---------------------------------------------------------------------------- small helpers
def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=_REL_TOL, abs_tol=_ABS_TOL)


def _num(value: float | None, digits: int) -> str:
    """Fixed-decimal text; ``None`` -> ``""``; never prints a negative zero."""
    if value is None:
        return ""
    x = float(value)
    if not math.isfinite(x):
        return repr(x)
    text = f"{x:.{digits}f}"
    if text.startswith("-") and float(text) == 0.0:
        text = text[1:]
    return text


def _signed(value: float | None, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    text = f"{float(value):+.{digits}f}"
    return "+" + text[1:] if float(text) == 0.0 else text


def _iso(ts: int | None) -> str:
    return "" if ts is None else ms_to_iso(ts)


def _md_cell(value: object) -> str:
    return str(value).replace("\r", " ").replace("\n", " ").replace("|", "\\|")


def flag_code(flag: str) -> str:
    """``"loss_outlier: ..." -> "loss_outlier"``."""
    return flag.split(":", 1)[0]


def planned_rr(trade: Trade) -> float | None:
    """PRICE reward:risk ``(target - entry) / (entry - stop)``, or ``None`` if the stop is not
    below the entry."""
    risk = trade.entry_price - trade.stop
    if not (math.isfinite(risk) and risk > 0):
        return None
    return (trade.target - trade.entry_price) / risk


def planned_net_rr(trade: Trade, cfg: StrategyConfig) -> float | None:
    """NET reward:risk of a TP fill at the target (A1), in R of the planned all-in risk:
    ``(qty*(target - entry) - fee*qty*(entry + target)) / risk_amount``.

    ``None`` if ``qty`` or ``risk_amount`` is not positive or a value is not finite.
    """
    qty, risk_amount = trade.qty, trade.risk_amount
    if not (math.isfinite(qty) and qty > 0 and math.isfinite(risk_amount) and risk_amount > 0):
        return None
    entry, target = trade.entry_price, trade.target
    net = qty * (target - entry) - cfg.fee_rate * qty * (entry + target)
    return net / risk_amount if math.isfinite(net) else None


def planned_risk_amount(trade: Trade, cfg: StrategyConfig) -> float:
    """The all-in risk the trade's qty implies under A1: ``qty * L_u`` (see module doc)."""
    return trade.qty * loss_per_unit(trade.entry_price, trade.stop, cfg)


def hold_hours(trade: Trade) -> float | None:
    """``(exit_ts - entry_ts)`` in hours (same definition as ``metrics.avg_hold_h``)."""
    if trade.exit_ts is None:
        return None
    return (trade.exit_ts - trade.entry_ts) / HOUR_MS


def expected_sl_r(trade: Trade, cfg: StrategyConfig) -> float | None:
    """R of a NORMAL stop-out under the cost model: fill at ``stop * (1 - slippage)``, fees
    ``fee_rate`` on both sides. Exactly -1R for a cost-aware sized trade (A1); worse than
    -1R only if ``risk_amount`` left the costs out (legacy price-only sizing).
    ``None`` if the trade has no positive planned risk."""
    if not (trade.risk_amount > 0 and trade.qty > 0):
        return None
    fill = stop_fill_price(trade.stop, cfg)
    fees = cfg.fee_rate * trade.qty * (trade.entry_price + fill)
    return (trade.qty * (fill - trade.entry_price) - fees) / trade.risk_amount


def pair_cap_pct(pair: str, cfg: StrategyConfig) -> float | None:
    """The pair's configured per-trade risk cap in percent, ``None`` if not tradable."""
    try:
        return cfg.risk_for(pair).max_risk_pct
    except ConfigError:
        return None


def window_of(trade: Trade, split_ts: int | None) -> str:
    """``TRAIN`` (signal before ``split_ts``), ``TEST`` (at or after it) or ``ALL``."""
    if split_ts is None:
        return WINDOW_ALL
    return WINDOW_TRAIN if trade.signal_ts < split_ts else WINDOW_TEST


def entry_order(trades: Sequence[Trade]) -> list[Trade]:
    """Trades sorted by entry time, ties broken by ``trade_id``, pair, variant."""
    return sorted(trades, key=lambda t: (t.entry_ts, t.trade_id, t.pair, t.variant))


def review_key(trade: Trade, split_ts: int | None) -> tuple[str, int]:
    """The row key ``(window, trade_id)`` of ``trade`` in a pack split at ``split_ts``."""
    return window_of(trade, split_ts), trade.trade_id


def duplicate_keys(trades: Sequence[Trade], split_ts: int | None) -> list[tuple[str, int]]:
    """Sorted ``(window, trade_id)`` keys shared by more than one trade (``[]`` if unique)."""
    counts = Counter(review_key(t, split_ts) for t in trades)
    return sorted(k for k, n in counts.items() if n > 1)


# ---------------------------------------------------------------------------- auto flags
def _non_finite_flags(trade: Trade) -> list[str]:
    bad = [
        name
        for name in _NUMERIC_FIELDS
        if getattr(trade, name) is not None and not math.isfinite(float(getattr(trade, name)))
    ]
    return [f"{FLAG_NON_FINITE}: non-finite {', '.join(bad)}"] if bad else []


def _below(rr: float | None, required: float) -> bool:
    return rr is not None and rr < required and not _close(rr, required)


def _level_flags(trade: Trade, cfg: StrategyConfig) -> list[str]:
    entry, stop, target = trade.entry_price, trade.stop, trade.target
    problems = []
    if stop >= entry:
        problems.append(f"stop {_num(stop, 6)} >= entry {_num(entry, 6)}")
    if target <= entry:
        problems.append(f"target {_num(target, 6)} <= entry {_num(entry, 6)}")
    if problems:
        return [f"{FLAG_BAD_LEVELS}: {' and '.join(problems)} (not a valid long)"]
    return []


def _rr_flags(trade: Trade, cfg: StrategyConfig) -> list[str]:
    """2:1 minimum by price AND net of costs; a net RR above the config is a wrong config."""
    required = cfg.reward_risk
    price_rr, net_rr = planned_rr(trade), planned_net_rr(trade, cfg)
    below = [
        f"{name} RR {_num(rr, 4)}"
        for name, rr in (("net", net_rr), ("price", price_rr))
        if _below(rr, required)
    ]
    if below:
        return [
            f"{FLAG_RR_BELOW}: planned {' and '.join(below)} < required {_num(required, 2)} "
            "(the minimum must hold net of fees and slippage)"
        ]
    if net_rr is not None and net_rr > required and not _close(net_rr, required):
        return [
            f"{FLAG_RR_ABOVE}: planned net RR {_num(net_rr, 4)} != configured "
            f"{_num(required, 2)} (target not derived from this config and its costs)"
        ]
    return []


def _size_flags(trade: Trade, cfg: StrategyConfig) -> list[str]:
    if not trade.stop < trade.entry_price:
        return []
    want = planned_risk_amount(trade, cfg)
    if _close(trade.risk_amount, want):
        return []
    return [
        f"{FLAG_SIZE}: risk_amount {_num(trade.risk_amount, 4)} != qty*all-in loss per unit "
        f"{_num(want, 4)} (size not derived from the stop distance and costs)"
    ]


def _risk_flags(trade: Trade, cfg: StrategyConfig) -> list[str]:
    cap = pair_cap_pct(trade.pair, cfg)
    if cap is None:
        return [f"{FLAG_PAIR}: {trade.pair} has no configured risk cap (not a tradable pair)"]
    flags: list[str] = []
    if trade.risk_pct > cap and not _close(trade.risk_pct, cap):
        flags.append(
            f"{FLAG_RISK_CAP}: risk {_num(trade.risk_pct, 4)}% > {trade.pair} cap {_num(cap, 2)}%"
        )
    if trade.risk_pct <= 0 or trade.risk_amount <= 0:
        flags.append(
            f"{FLAG_SIZE}: non-positive planned risk (risk_pct {_num(trade.risk_pct, 4)}, "
            f"risk_amount {_num(trade.risk_amount, 4)})"
        )
    return flags


def _time_flags(trade: Trade, cfg: StrategyConfig) -> list[str]:
    flags: list[str] = []
    signal_close = trade.signal_ts + cfg.timeframe_ms
    if trade.entry_ts < signal_close:
        flags.append(
            f"{FLAG_LOOKAHEAD}: entry {_iso(trade.entry_ts)} before the signal candle closed "
            f"{_iso(signal_close)}"
        )
    if trade.exit_ts is not None and trade.exit_ts < trade.entry_ts:
        flags.append(
            f"{FLAG_EXIT_BEFORE_ENTRY}: exit {_iso(trade.exit_ts)} before entry "
            f"{_iso(trade.entry_ts)} (hold {_num(hold_hours(trade), 2)}h)"
        )
    elif trade.exit_ts is not None and trade.exit_ts == trade.entry_ts:
        flags.append(
            f"{FLAG_ZERO_HOLD}: hold time 0h, exit in the fill candle "
            "(intrabar order assumed, check the chart)"
        )
    return flags


def _missing_exit_flags(trade: Trade) -> list[str]:
    missing = [name for name in _EXIT_FIELDS if getattr(trade, name) is None]
    if not missing:
        return []
    if len(missing) == len(_EXIT_FIELDS):
        return [f"{FLAG_MISSING_EXIT}: no exit recorded (trade still open or exit lost)"]
    return [f"{FLAG_MISSING_EXIT}: exit incomplete, missing {', '.join(missing)}"]


def _loss_outlier_flag(trade: Trade, cfg: StrategyConfig, r: float) -> str:
    """Name the likely cause: a gap-through or slippage beyond the cost model (a cost-aware
    stop-out is exactly -1R), or a ``risk_amount`` that left the costs out (then EVERY
    stop-out of that trade is worse than -1.05R, i.e. realised risk exceeds planned risk)."""
    expected = expected_sl_r(trade, cfg)
    head = f"{FLAG_LOSS_OUTLIER}: loss {_signed(r)}R worse than {_signed(LOSS_OUTLIER_R, 2)}R"
    if expected is None:
        return f"{head} (gap-through or slippage beyond the cost model)"
    if r < expected - GAP_SLACK_R or expected >= LOSS_OUTLIER_R:
        return (
            f"{head} (a normal stop-out would be {_signed(expected)}R: gap-through or slippage "
            "beyond the cost model)"
        )
    return (
        f"{head} (a normal stop-out already costs {_signed(expected)}R: risk_amount leaves out "
        "fees and slippage, so the size was not cost-aware)"
    )


def _r_flags(trade: Trade, cfg: StrategyConfig) -> list[str]:
    r = trade.r_multiple
    if r is None:
        return []
    flags: list[str] = []
    if r < LOSS_OUTLIER_R:
        flags.append(_loss_outlier_flag(trade, cfg, r))
    win_cap = cfg.reward_risk + WIN_SLACK_R
    if r > win_cap:
        flags.append(
            f"{FLAG_WIN_TOO_LARGE}: win {_signed(r)}R above {_signed(win_cap, 2)}R, impossible "
            "under the exit model (bug)"
        )
    if trade.pnl is not None and trade.risk_amount > 0:
        implied = trade.pnl / trade.risk_amount
        if not _close(r, implied):
            flags.append(
                f"{FLAG_R_MISMATCH}: r_multiple {_signed(r, 4)} != pnl/risk_amount "
                f"{_signed(implied, 4)}"
            )
    return flags


def _exit_model_flags(trade: Trade) -> list[str]:
    reason, price, r = trade.exit_reason, trade.exit_price, trade.r_multiple
    if reason is None or reason == EXIT_END:
        return []
    if reason not in (EXIT_SL, EXIT_TP):
        return [f"{FLAG_EXIT_MODEL}: unknown exit reason {reason!r}"]
    flags: list[str] = []
    if reason == EXIT_SL:
        if r is not None and r > 0:
            flags.append(f"{FLAG_EXIT_MODEL}: SL exit with positive R {_signed(r)}")
        if price is not None and price > trade.stop and not _close(price, trade.stop):
            flags.append(
                f"{FLAG_EXIT_MODEL}: SL filled at {_num(price, 6)} above the stop "
                f"{_num(trade.stop, 6)}"
            )
        return flags
    if r is not None and r <= 0:
        flags.append(f"{FLAG_EXIT_MODEL}: TP exit with non-positive R {_signed(r)}")
    if price is not None and not _close(price, trade.target):
        flags.append(
            f"{FLAG_EXIT_MODEL}: TP filled at {_num(price, 6)}, not at the target limit "
            f"{_num(trade.target, 6)}"
        )
    return flags


def auto_flags(trade: Trade, cfg: StrategyConfig, context: TradeContext | None = None) -> list[str]:
    """Machine sanity checks for one trade, each ``"<code>: <one-line detail>"``.

    They HELP the human reviewer and never replace the review. ``cfg`` must be the config
    the trades were generated with (the RR checks use ``cfg.reward_risk``). Checks, in order:
    non-finite numbers; stop >= entry or target <= entry; planned net OR price RR below
    ``cfg.reward_risk`` (or, a sign the wrong config or costs were passed, net RR above it);
    ``risk_amount`` not equal to ``qty * L_u`` (cost-aware size, A1); unknown pair;
    ``risk_pct`` above the pair cap; entry before the signal candle closed (look-ahead); exit
    before entry; zero hold time (exit in the fill candle); missing or incomplete exit;
    window-end forced exit (``END``); loss worse than -1.05R; win above
    ``reward_risk + 0.01`` R; ``r_multiple != pnl / risk_amount``; SL/TP outcomes the exit
    model cannot produce; then, only when a :class:`TradeContext` is given, the context
    flags of :func:`context_flags`. Returns ``[]`` for a clean trade.
    """
    flags = _non_finite_flags(trade)
    flags += _level_flags(trade, cfg)
    flags += _rr_flags(trade, cfg)
    flags += _size_flags(trade, cfg)
    flags += _risk_flags(trade, cfg)
    flags += _time_flags(trade, cfg)
    flags += _missing_exit_flags(trade)
    if trade.exit_reason == EXIT_END:
        flags.append(
            f"{FLAG_WINDOW_END}: window-end forced exit (window-boundary artifact, not an "
            "SL/TP outcome)"
        )
    flags += _r_flags(trade, cfg)
    flags += _exit_model_flags(trade)
    if context is not None:
        flags += context_flags(trade, context, cfg)
    return flags


# ---------------------------------------------------------------------------- trade context
@dataclass(frozen=True, slots=True)
class TradeContext:
    """What the candles say about one trade, re-derived by :func:`trade_context`.

    Indices are positions in the pair's candle series as supplied. When ``missing`` is set
    the candles could not be lined up with the trade and every other field is empty.
    """

    missing: str | None = None  # why there is no context (flag context_missing)
    signal_index: int = 0
    fill_index: int = 0
    exit_index: int | None = None  # None for an open trade
    first_index: int = 0  # series index of rows[0]
    rows: tuple[Candle, ...] = ()  # the context CSV candles
    ref_index: int | None = None  # the structure candle whose low the R8 stop sits behind
    ref_method: str = ""  # "pivot" | "lookback_low", as structure.find_stop picks it
    rederived_stop: float | None = None  # structure.find_stop at the signal candle
    stop_reason: str = ""  # find_stop's reason (or why it could not run)
    mae_r: float | None = None
    mfe_r: float | None = None
    exit_open: float | None = None  # SL exits only
    exit_low: float | None = None  # SL exits only
    wick_depth_r: float | None = None  # (stop - exit_low) / R, SL exits only
    wick_depth_sd: float | None = None  # (stop - exit_low) / (entry - stop), SL exits only
    mismatches: tuple[str, ...] = ()  # flag context_mismatch, one entry per problem

    @property
    def available(self) -> bool:
        return self.missing is None

    @property
    def ref_candle(self) -> Candle | None:
        if self.ref_index is None or not self.first_index <= self.ref_index:
            return None
        pos = self.ref_index - self.first_index
        return self.rows[pos] if pos < len(self.rows) else None


class _StopRef(NamedTuple):
    index: int | None
    method: str
    stop: float | None
    reason: str


def risk_unit(trade: Trade) -> float | None:
    """The planned all-in risk per unit ``risk_amount / qty`` (``L_u`` under A1): one R of
    MAE, MFE and wick depth. ``None`` unless both are positive and finite."""
    qty, risk_amount = trade.qty, trade.risk_amount
    if not (math.isfinite(qty) and qty > 0 and math.isfinite(risk_amount) and risk_amount > 0):
        return None
    return risk_amount / qty


def wick_fill_extra_r(trade: Trade, exit_low: float, k: float, cfg: StrategyConfig) -> float:
    """Extra loss in R if a non-gap stop filled at ``stop - k*(stop - exit_low)`` (the CONTRACT
    v4 D4 wick fill) instead of at the stop, both minus slippage and the exit fee:
    ``k * (stop - exit_low) * (1 - slippage) * (1 - fee_rate) * qty / risk_amount``."""
    slip, fee = cfg.slippage_pct / 100.0, cfg.fee_rate
    extra = k * (trade.stop - exit_low) * (1.0 - slip) * (1.0 - fee) * trade.qty
    return extra / trade.risk_amount


def _index_at(times: Sequence[int], ts: int) -> int | None:
    k = bisect_left(times, ts)
    return k if k < len(times) and times[k] == ts else None


def _locate(trade: Trade, times: Sequence[int]) -> tuple[int, int, int | None] | str:
    """``(signal, fill, exit)`` series indices of the trade's candles, or why not."""
    signal = _index_at(times, trade.signal_ts)
    fill = _index_at(times, trade.entry_ts)
    exit_idx = None if trade.exit_ts is None else _index_at(times, trade.exit_ts)
    absent = [
        f"{name} candle {_iso(ts)}"
        for name, ts, idx in (
            ("signal", trade.signal_ts, signal),
            ("fill", trade.entry_ts, fill),
            ("exit", trade.exit_ts, exit_idx),
        )
        if ts is not None and idx is None
    ]
    if signal is None or fill is None or absent:
        return f"no {trade.pair} candle in the supplied data for the {' and the '.join(absent)}"
    return signal, fill, exit_idx


def _stop_reference(candles: Sequence[Candle], i: int, pair: str, cfg: StrategyConfig) -> _StopRef:
    """Re-run R8 at signal candle ``i`` and name the candle the stop sits behind."""
    try:
        plan, reason = find_stop(candles, i, pair, cfg)
    except ValueError as exc:  # ConfigError (unconfigured pair) is a ValueError
        return _StopRef(None, "", None, str(exc))
    j = latest_confirmed_pivot(candles, i, cfg)
    method = "pivot"
    if j is None:  # structure.find_stop's fallback: lowest low, oldest on ties
        method = "lookback_low"
        lo = max(0, i - cfg.fallback_lookback + 1)
        j = min(range(lo, i + 1), key=lambda t: candles[t].low)
    if plan is None:
        return _StopRef(j, method, None, reason)
    if plan.method != method or candles[j].low != plan.structure_level:
        return _StopRef(None, plan.method, plan.stop, reason)  # cannot name the candle
    return _StopRef(j, method, plan.stop, reason)


def _touch(c: Candle, stop: float, target: float) -> str | None:
    """Which level candle ``c`` reaches under the exit model (the stop first)."""
    if c.open <= stop or c.low <= stop:
        return "stop"
    if c.high >= target:
        return "target"
    return None


def _exit_candle_mismatch(c: Candle, trade: Trade) -> list[str]:
    hit, reason = _touch(c, trade.stop, trade.target), trade.exit_reason
    where = f"the {reason} exit candle {_iso(c.ts)}"
    if reason == EXIT_SL and hit != "stop":
        return [f"{where} (low {_num(c.low, 6)}) never reached the stop {_num(trade.stop, 6)}"]
    if reason == EXIT_TP and hit == "stop":
        return [
            f"{where} also reached the stop {_num(trade.stop, 6)} (low {_num(c.low, 6)}), "
            "which the exit model fills first"
        ]
    if reason == EXIT_TP and hit is None:
        return [
            f"{where} (high {_num(c.high, 6)}) never reached the target {_num(trade.target, 6)}"
        ]
    if reason == EXIT_END and hit is not None:
        return [f"{where} reached the {hit}, so the exit model would have closed it there"]
    return []


def _context_mismatches(
    candles: Sequence[Candle], signal: int, fill: int, exit_idx: int | None, trade: Trade
) -> list[str]:
    out: list[str] = []
    if fill != signal + 1:
        out.append(
            f"the fill candle {_iso(trade.entry_ts)} is not the candle right after the signal "
            f"candle {_iso(trade.signal_ts)} (a data gap, or not the candles of this trade)"
        )
    if exit_idx is None or exit_idx < fill:
        return out
    for j in range(fill, exit_idx):
        hit = _touch(candles[j], trade.stop, trade.target)
        if hit is not None:
            out.append(
                f"the candle {_iso(candles[j].ts)} already reached the {hit} before the recorded "
                f"exit {_iso(trade.exit_ts)}"
            )
            break
    return out + _exit_candle_mismatch(candles[exit_idx], trade)


def _excursions(
    candles: Sequence[Candle], fill: int, exit_idx: int | None, entry: float, unit: float | None
) -> tuple[float | None, float | None]:
    if exit_idx is None or unit is None or exit_idx < fill:
        return None, None
    span = candles[fill : exit_idx + 1]
    low, high = min(c.low for c in span), max(c.high for c in span)
    return max(0.0, entry - low) / unit, max(0.0, high - entry) / unit


def _wick(c: Candle, trade: Trade, unit: float | None) -> dict[str, float | None]:
    depth = trade.stop - c.low
    dist = trade.entry_price - trade.stop
    return {
        "exit_open": c.open,
        "exit_low": c.low,
        "wick_depth_r": None if unit is None else depth / unit,
        "wick_depth_sd": depth / dist if math.isfinite(dist) and dist > 0 else None,
    }


def trade_context(
    trade: Trade,
    candles: Sequence[Candle] | None,
    cfg: StrategyConfig,
    times: Sequence[int] | None = None,
) -> TradeContext:
    """Line ``trade`` up with its pair's ``candles`` and re-derive what a reviewer checks.

    ``candles`` must be the pair's series the trade was generated on (sorted by ``ts``; the
    walk-forward's full series is fine because ``find_stop`` only reads up to the signal
    candle). ``times`` (``[c.ts for c in candles]``) may be passed to avoid rebuilding it per
    trade. See the module docstring for every field's definition.
    """
    if not candles:
        return TradeContext(missing=f"no {trade.pair} candles in the supplied data")
    if times is None:
        times = [c.ts for c in candles]
    located = _locate(trade, times)
    if isinstance(located, str):
        return TradeContext(missing=located)
    signal, fill, exit_idx = located
    ref = _stop_reference(candles, signal, trade.pair, cfg)
    unit = risk_unit(trade)
    start = fill - CONTEXT_BEFORE
    if ref.index is not None:
        start = min(start, ref.index - (cfg.swing_pivot_k if ref.method == "pivot" else 0))
    start = max(0, start)
    last = fill if exit_idx is None else max(fill, exit_idx)
    mae, mfe = _excursions(candles, fill, exit_idx, trade.entry_price, unit)
    wick: dict[str, float | None] = {}
    if trade.exit_reason == EXIT_SL and exit_idx is not None:
        wick = _wick(candles[exit_idx], trade, unit)
    return TradeContext(
        signal_index=signal,
        fill_index=fill,
        exit_index=exit_idx,
        first_index=start,
        rows=tuple(candles[start : last + 1]),
        ref_index=ref.index,
        ref_method=ref.method,
        rederived_stop=ref.stop,
        stop_reason=ref.reason,
        mae_r=mae,
        mfe_r=mfe,
        mismatches=tuple(_context_mismatches(candles, signal, fill, exit_idx, trade)),
        **wick,
    )


def trade_contexts(
    trades: Sequence[Trade], data: Mapping[str, Sequence[Candle]], cfg: StrategyConfig
) -> list[TradeContext]:
    """:func:`trade_context` for every trade, looking each pair up in ``data`` once."""
    times = {pair: [c.ts for c in data[pair]] for pair in {t.pair for t in trades} if pair in data}
    return [trade_context(t, data.get(t.pair), cfg, times.get(t.pair)) for t in trades]


def _flag_text(text: str) -> str:
    """Keep a flag detail on one line and free of the CSV/Markdown separators."""
    return " ".join(text.replace(";", ",").replace("|", "/").split())


def _stop_flags(trade: Trade, ctx: TradeContext) -> list[str]:
    head = f"{FLAG_STOP_MISMATCH}: "
    if ctx.rederived_stop is None:
        return [
            head + "structure.find_stop at the signal candle gives no stop for this trade "
            f"({_flag_text(ctx.stop_reason)})"
        ]
    if not _close(ctx.rederived_stop, trade.stop):
        return [
            head + f"recorded stop {_num(trade.stop, 6)} != {_num(ctx.rederived_stop, 6)} "
            f"re-derived by structure.find_stop at the signal candle "
            f"({_flag_text(ctx.stop_reason)})"
        ]
    if trade.stop_method and ctx.ref_method and trade.stop_method != ctx.ref_method:
        return [
            head + f"recorded stop method {trade.stop_method!r} != {ctx.ref_method!r} "
            "re-derived at the signal candle"
        ]
    return []


def _deep_wick_flags(trade: Trade, ctx: TradeContext, cfg: StrategyConfig) -> list[str]:
    depth, low = ctx.wick_depth_sd, ctx.exit_low
    if trade.exit_reason != EXIT_SL or depth is None or low is None:
        return []
    if depth <= DEEP_WICK_STOP_DISTANCES or (
        ctx.exit_open is not None and ctx.exit_open <= trade.stop
    ):
        return []  # shallow, or a gap-through (filled at the open, not by the touch model)
    wick_r = "" if ctx.wick_depth_r is None else f" ({_num(ctx.wick_depth_r, 3)}R)"
    extra = ""
    if trade.risk_amount > 0:
        costs = ", ".join(
            f"k={k:g} {_num(wick_fill_extra_r(trade, low, k, cfg), 3)}R" for k in WICK_STRESS_KS
        )
        extra = f", extra loss under the CONTRACT v4 D4 wick fill: {costs}"
    return [
        f"{FLAG_DEEP_WICK}: exit-candle low {_num(low, 6)} is {_num(depth, 3)} stop distances"
        f"{wick_r} below the stop {_num(trade.stop, 6)}, so the touch fill at the stop is "
        f"optimistic here{extra}"
    ]


def context_flags(trade: Trade, ctx: TradeContext, cfg: StrategyConfig) -> list[str]:
    """Flags only the candles can raise: ``context_missing``, ``context_mismatch``,
    ``stop_mismatch`` and ``deep_wick_stop`` (see the module docstring)."""
    if ctx.missing is not None:
        return [f"{FLAG_CONTEXT_MISSING}: {_flag_text(ctx.missing)}"]
    flags = [f"{FLAG_CONTEXT_MISMATCH}: {_flag_text(m)}" for m in ctx.mismatches]
    return flags + _stop_flags(trade, ctx) + _deep_wick_flags(trade, ctx, cfg)


def context_rel_path(window: str, trade_id: int) -> str:
    """``"context/<window>_<trade_id>.csv"``: a context file's path relative to the pack."""
    return f"{CONTEXT_DIR}/{window}_{trade_id}.csv"


def _marks(idx: int, ctx: TradeContext, trade: Trade) -> str:
    marks = []
    if idx == ctx.ref_index:
        marks.append(f"stop_ref_{ctx.ref_method}")
    if idx == ctx.signal_index:
        marks.append("signal")
    if idx == ctx.fill_index:
        marks.append("entry")
    if idx == ctx.exit_index:
        marks.append(f"exit_{trade.exit_reason}")
    return " ".join(marks)


def context_csv_text(trade: Trade, ctx: TradeContext) -> str:
    """The context CSV of one trade (:data:`CONTEXT_CSV_COLUMNS`, one row per candle)."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(CONTEXT_CSV_COLUMNS)
    for pos, c in enumerate(ctx.rows):
        idx = ctx.first_index + pos
        writer.writerow(
            [
                idx - ctx.signal_index,
                idx,
                ms_to_iso(c.ts),
                c.ts,
                _num(c.open, 8),
                _num(c.high, 8),
                _num(c.low, 8),
                _num(c.close, 8),
                _num(c.volume, 6),
                _marks(idx, ctx, trade),
            ]
        )
    return buf.getvalue()


def _context_cells(ctx: TradeContext | None) -> dict[str, str]:
    if ctx is None or ctx.missing is not None:
        return {}
    ref = ctx.ref_candle
    return {
        "mae_r": _num(ctx.mae_r, 4),
        "mfe_r": _num(ctx.mfe_r, 4),
        "stop_ref_time_utc": "" if ref is None else _iso(ref.ts),
        "stop_ref_low": "" if ref is None else _num(ref.low, 6),
        "stop_ref_bar": "" if ctx.ref_index is None else str(ctx.ref_index - ctx.signal_index),
        "exit_low": _num(ctx.exit_low, 6),
        "wick_depth_r": _num(ctx.wick_depth_r, 4),
    }


# ---------------------------------------------------------------------------- CSV rows
def review_header(trades: Sequence[Trade]) -> tuple[str, ...]:
    """CSV header: fixed head, ``f_<feature>`` columns (base set + extras, sorted), the
    trade-context columns, tail."""
    keys = set(BASE_FEATURES)
    for t in trades:
        keys.update(t.features)
    features = tuple(FEATURE_PREFIX + k for k in sorted(keys))
    return HEAD_COLUMNS + features + CONTEXT_COLUMNS + TAIL_COLUMNS


def review_row(
    trade: Trade,
    cfg: StrategyConfig,
    split_ts: int | None,
    flags: Sequence[str] | None = None,
    context: TradeContext | None = None,
    context_file: tuple[str, str] = ("", ""),
) -> dict[str, str]:
    """All review cells of one trade as text (feature cells keyed ``f_<name>``).

    ``context`` fills the MAE / MFE / stop-structure / wick cells (blank without it or when
    it is ``missing``); ``context_file`` is ``(context_csv, context_sha256)``.
    """
    if flags is None:
        flags = auto_flags(trade, cfg, context)
    row = {
        "trade_id": str(trade.trade_id),
        "window": window_of(trade, split_ts),
        "pair": trade.pair,
        "variant": trade.variant,
        "signal_time_utc": _iso(trade.signal_ts),
        "entry_time_utc": _iso(trade.entry_ts),
        "exit_time_utc": _iso(trade.exit_ts),
        "entry": _num(trade.entry_price, 6),
        "stop": _num(trade.stop, 6),
        "target": _num(trade.target, 6),
        "stop_method": trade.stop_method,
        "planned_rr_price": _num(planned_rr(trade), 4),
        "planned_rr_net": _num(planned_net_rr(trade, cfg), 4),
        "risk_pct": _num(trade.risk_pct, 4),
        "pair_cap_pct": _num(pair_cap_pct(trade.pair, cfg), 4),
        "qty": _num(trade.qty, 8),
        "notional": _num(trade.qty * trade.entry_price, 4),
        "risk_amount": _num(trade.risk_amount, 4),
        "exit_price": _num(trade.exit_price, 6),
        "exit_reason": trade.exit_reason or "",
        "fees": _num(trade.fees, 4),
        "pnl": _num(trade.pnl, 4),
        "r_multiple": _num(trade.r_multiple, 4),
        "hold_h": _num(hold_hours(trade), 2),
        "ml_prob": _num(trade.ml_prob, 4),
        "notes": trade.notes,
        "auto_flags": FLAG_SEPARATOR.join(flags),
        "reviewer_ok": "",
        "reviewer_note": "",
    }
    row.update(dict.fromkeys(CONTEXT_COLUMNS, ""))
    row.update(_context_cells(context))
    row["context_csv"], row["context_sha256"] = context_file
    for key, value in trade.features.items():
        row[FEATURE_PREFIX + key] = _num(value, 6)
    return row


def _write_csv(path: Path, header: Sequence[str], rows: Sequence[Mapping[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(header)
        for row in rows:
            writer.writerow([row.get(col, "") for col in header])


# ---------------------------------------------------------------------------- markdown
_MD_TRADE_COLUMNS = (
    ("id", "trade_id"),
    ("window", "window"),
    ("pair", "pair"),
    ("variant", "variant"),
    ("signal UTC", "signal_time_utc"),
    ("entry UTC", "entry_time_utc"),
    ("exit UTC", "exit_time_utc"),
    ("entry", "entry"),
    ("stop", "stop"),
    ("target", "target"),
    ("stop method", "stop_method"),
    ("RR price", "planned_rr_price"),
    ("RR net", "planned_rr_net"),
    ("risk %", "risk_pct"),
    ("cap %", "pair_cap_pct"),
    ("exit", "exit_price"),
    ("reason", "exit_reason"),
    ("R", "r_multiple"),
    ("hold h", "hold_h"),
    ("ml p", "ml_prob"),
)


def _count_text(counter: Counter[str]) -> str:
    return ", ".join(f"{key} {counter[key]}" for key in sorted(counter)) or "none"


def _test_only_why(cfg: StrategyConfig) -> str:
    why = []
    if not cfg.regime_filter:
        why.append("the R4 regime filter is OFF")
    if cfg.stop_fill_wick_k > 0:
        why.append(
            f"stop fills are stressed with stop_fill_wick_k={cfg.stop_fill_wick_k:g} "
            "(CONTRACT v4 D4)"
        )
    return " and ".join(why) or "a research-only variant"


def _header_lines(
    title: str,
    trades: Sequence[Trade],
    windows: Sequence[str],
    cfg: StrategyConfig,
    split_ts: int | None,
    flagged: int,
) -> list[str]:
    n = len(trades)
    by_window = Counter(window_of(t, split_ts) for t in trades)
    closed = sum(1 for t in trades if t.exit_ts is not None)
    caps = ", ".join(
        f"{b} {_num(cfg.pair_risk[b].max_risk_pct, 2)}%" for b in sorted(cfg.pair_risk)
    )
    lines = [
        f"# {_md_cell(title)}",
        "",
        "**Human review pack.** The reviewer must inspect EVERY row of the trade table below "
        f"(all {n} trades, also in `{CSV_NAME}`) before signing off: summary statistics alone "
        "are not a review. Automated flags only point at suspicious rows; an unflagged trade "
        "is not an approved trade. Record a verdict for every trade in the `reviewer_ok` "
        "(exactly Y or N; any other value is rejected when the sheet is read back) and "
        f"`reviewer_note` columns of `{CSV_NAME}`.",
        "",
        f"- Trades: {n} ({', '.join(f'{w} {by_window[w]}' for w in windows)})",
        f"- Closed trades: {closed}; open or missing exit: {n - closed}",
        f"- Flagged trades: {flagged} of {n}",
        f"- Pairs: {_md_cell(_count_text(Counter(t.pair for t in trades)))}",
        f"- Variants: {_md_cell(_count_text(Counter(t.variant for t in trades)))}",
    ]
    if split_ts is None:
        lines.append("- Walk-forward split: none (every trade is labelled ALL)")
    else:
        lines.append(
            f"- Walk-forward split: {ms_to_iso(split_ts)} (TRAIN = signal candle before it, "
            "TEST = at or after it)"
        )
    lines.append(
        f"- Config: `{cfg.variant_id()}`, reward:risk {_num(cfg.reward_risk, 2)} (net of costs), "
        f"risk caps {caps}"
    )
    lines.append(
        f"- Costs: fee {_num(cfg.fee_rate * 100, 4)}% per side, slippage "
        f"{_num(cfg.slippage_pct, 4)}% on market fills (entry and stop), exchange "
        f"`{_md_cell(cfg.exchange_id)}`. Risk is the ALL-IN loss at the stop, so a clean "
        "stop-out is -1R and a TP is +reward:risk R; `RR price` is the chart-distance ratio, "
        "`RR net` what a TP earns after both fees."
    )
    if cfg.is_test_only:
        lines.append(
            f"- **TEST-ONLY config: {_test_only_why(cfg)}. This variant can never be adopted.**"
        )
    matched = (
        "matched against the journal by this key"
        if split_ts is None
        else "matched against the TRAIN and TEST journals by this key"
    )
    lines.append(f"- Row key: (window, trade_id), unique in this pack; `{CSV_NAME}` is {matched}")
    shared = sorted(k for k, v in Counter(t.trade_id for t in trades).items() if v > 1)
    if shared:
        lines.append(
            f"- Note: trade ids {', '.join(map(str, shared))} appear in more than one window; "
            "always quote the window with the id"
        )
    return lines


def _window_stats(flagged_trades: Sequence[tuple[Trade, list[str]]]) -> dict[str, str]:
    trades = [t for t, _ in flagged_trades]
    rs = [float(t.r_multiple) for t in trades if t.r_multiple is not None]
    reasons = Counter(t.exit_reason for t in trades)
    total = math.fsum(rs)
    return {
        "trades": str(len(trades)),
        "closed with R": str(len(rs)),
        "avg R (expectancy)": _signed(total / len(rs)) if rs else "n/a",
        "total R": _signed(total) if rs else "n/a",
        "exits SL / TP / END": f"{reasons[EXIT_SL]} / {reasons[EXIT_TP]} / {reasons[EXIT_END]}",
        "flagged": str(sum(1 for _, flags in flagged_trades if flags)),
    }


def _summary_lines(
    flagged_trades: Sequence[tuple[Trade, list[str]]],
    windows: Sequence[str],
    split_ts: int | None,
) -> list[str]:
    stats = {
        w: _window_stats([tf for tf in flagged_trades if window_of(tf[0], split_ts) == w])
        for w in windows
    }
    lines = [
        "## Summary per window",
        "",
        "avg R is the expectancy per closed trade (the target metric), net of fees and "
        "slippage. Win rate is deliberately not shown: it is never a target.",
        "",
        "| metric | " + " | ".join(windows) + " |",
        "|---|" + "---:|" * len(windows),
    ]
    for metric in stats[windows[0]]:
        lines.append(f"| {metric} | " + " | ".join(stats[w][metric] for w in windows) + " |")
    return lines


def _trade_table_lines(
    rows: Sequence[Mapping[str, str]], flag_lists: Sequence[list[str]]
) -> list[str]:
    lines = [
        f"## All trades ({len(rows)})",
        "",
        "| " + " | ".join(label for label, _ in _MD_TRADE_COLUMNS) + " | flags |",
        "|" + "---|" * (len(_MD_TRADE_COLUMNS) + 1),
    ]
    for row, flags in zip(rows, flag_lists, strict=True):
        cells = [_md_cell(row[key]) for _, key in _MD_TRADE_COLUMNS]
        codes = ", ".join(dict.fromkeys(flag_code(f) for f in flags))
        lines.append("| " + " | ".join(cells) + f" | {codes} |")
    if not rows:
        lines.append("")
        lines.append("No trades.")
    return lines


def _flagged_lines(
    trades: Sequence[Trade],
    rows: Sequence[Mapping[str, str]],
    flag_lists: Sequence[list[str]],
) -> list[str]:
    items = [(t, r, f) for t, r, f in zip(trades, rows, flag_lists, strict=True) if f]
    lines = [f"## Flagged trades ({len(items)})", ""]
    if not items:
        lines.append("None. Unflagged trades still require the full row-by-row review.")
    for trade, row, flags in items:
        lines.append(
            f"- **#{trade.trade_id}** {_md_cell(trade.pair)} ({row['window']}, entry "
            f"{row['entry_time_utc']}): {_md_cell(FLAG_SEPARATOR.join(flags))}"
        )
    return lines


CONTEXT_UNAVAILABLE = (
    "Candle context unavailable: no candles were supplied (`write_review_pack(..., data=...)`, "
    "or the CLI's `--data-dir` / `--synthetic`), so the MAE, MFE, stop-structure and wick "
    f"columns of `{CSV_NAME}` are blank and no `{CONTEXT_DIR}/` files were written. Judge each "
    "trade on a chart of its pair from 30 candles before the entry through the exit, and check "
    "the stop against the swing low it claims to sit behind."
)


def _context_summary_line(
    contexts: Sequence[TradeContext] | None, flag_lists: Sequence[list[str]], source: str
) -> str:
    if contexts is None:
        return "- Candle context: unavailable (no candles supplied, see the trade context section)"
    found = sum(1 for c in contexts if c.available)
    codes = Counter(flag_code(f) for flags in flag_lists for f in flags)
    counts = ", ".join(f"{code} {codes[code]}" for code in CONTEXT_FLAG_CODES)
    return (
        f"- Candle context: {_md_cell(source)}; found for {found} of {len(contexts)} trades; "
        f"{counts}"
    )


def _context_table_row(row: Mapping[str, str], ctx: TradeContext) -> str:
    if ctx.missing is not None:
        cells = [row["trade_id"], row["window"], row["pair"], row["exit_reason"], row["r_multiple"]]
        return "| " + " | ".join(map(_md_cell, cells)) + " | | | | | missing |"
    ref = "n/a"
    if row["stop_ref_bar"]:
        ref = (
            f"{ctx.ref_method} bar {row['stop_ref_bar']} ({row['stop_ref_time_utc']}, "
            f"low {row['stop_ref_low']})"
        )
    link = f"[{row['context_csv']}]({row['context_csv']})"
    cells = [
        row["trade_id"],
        row["window"],
        row["pair"],
        row["exit_reason"],
        row["r_multiple"],
        row["mae_r"],
        row["mfe_r"],
        ref,
        row["wick_depth_r"],
    ]
    return "| " + " | ".join(map(_md_cell, cells)) + f" | {link} |"


def _context_lines(
    rows: Sequence[Mapping[str, str]], contexts: Sequence[TradeContext] | None, source: str
) -> list[str]:
    lines = ["## Trade context (candles)", ""]
    if contexts is None:
        return [*lines, CONTEXT_UNAVAILABLE]
    lines += [
        f"Candles: {_md_cell(source)}. R is the planned all-in risk per unit (risk_amount / qty, "
        "the R of `r_multiple`); excursions are price distances without fees.",
        "",
        "- MAE R = max(0, entry - lowest low) / R and MFE R = max(0, highest high - entry) / R, "
        "over the fill candle through the exit candle (intrabar order is unknown: the exit "
        "candle's extremes may come after the exit).",
        "- stop ref: the candle whose low the R8 stop sits behind, re-derived with "
        "`structure.find_stop` at the signal candle (pivot = latest confirmed pivot low, "
        "lookback_low = lowest low of the last "
        "fallback_lookback candles); bar = candles relative to the signal candle (0).",
        "- wick R (SL exits) = (stop - exit-candle low) / R. `deep_wick_stop` marks a non-gap "
        f"stop-out whose exit-candle low is more than {DEEP_WICK_STOP_DISTANCES:g} stop "
        "distances (entry - stop) below the stop: the touch fill at the stop is optimistic "
        "there (CONTRACT v4 D4).",
        f"- context: {CONTEXT_BEFORE} candles before the fill candle (further back if needed to "
        "show the structure candle) through the exit candle, sha256 in `context_sha256`; "
        "`marks` names the structure, signal, entry and exit candles.",
        "",
        "| id | window | pair | reason | R | MAE R | MFE R | stop ref | wick R | context |",
        "|---|---|---|---|---:|---:|---:|---|---:|---|",
    ]
    lines += [_context_table_row(r, c) for r, c in zip(rows, contexts, strict=True)]
    if not rows:
        lines += ["", "No trades."]
    return lines


def _decision_lines(decision_counts: Mapping[str, int]) -> list[str]:
    lines = [
        "## Rule denials",
        "",
        "Signal candles denied by each rule (the first failing rule is counted), for context "
        "on how often the mandatory rules said no.",
        "",
    ]
    if not decision_counts:
        lines.append("No denials recorded.")
        return lines
    lines += ["| rule | denied | meaning |", "|---|---:|---|"]
    for rule, count in sorted(decision_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        meaning = RULE_IDS.get(rule, "(unknown rule id)")
        lines.append(f"| {_md_cell(rule)} | {int(count)} | {_md_cell(meaning)} |")
    return lines


def _signoff_lines(n: int, flagged: int) -> list[str]:
    blank = "______________________________"
    return [
        "## Reviewer sign-off",
        "",
        "Complete by hand. APPROVE only if every row was inspected and every flag is explained "
        f"in `reviewer_note` of `{CSV_NAME}`.",
        "",
        f"- Reviewer name: {blank}",
        f"- Date (UTC): {blank}",
        f"- Trades reviewed: ______ of {n}",
        f"- Flagged trades explained: ______ of {flagged}",
        "- Decision: [ ] APPROVE    [ ] REJECT",
        f"- Notes: {blank}{blank}",
        f"- Follow-ups required before adoption: {blank}",
    ]


# ---------------------------------------------------------------------------- public API
def _context_files(
    ordered: Sequence[Trade], contexts: Sequence[TradeContext] | None, split_ts: int | None
) -> tuple[list[tuple[str, str]], dict[str, bytes]]:
    """Per trade ``(context_csv, context_sha256)`` and the file bytes by relative path."""
    if contexts is None:
        return [("", "")] * len(ordered), {}
    refs: list[tuple[str, str]] = []
    files: dict[str, bytes] = {}
    for trade, ctx in zip(ordered, contexts, strict=True):
        if ctx.missing is not None:
            refs.append(("", ""))
            continue
        rel = context_rel_path(window_of(trade, split_ts), trade.trade_id)
        body = context_csv_text(trade, ctx).encode("utf-8")
        files[rel] = body
        refs.append((rel, hashlib.sha256(body).hexdigest()))
    return refs, files


def _write_context_files(out: Path, files: Mapping[str, bytes]) -> None:
    """Write the context files and remove stale ``<window>_<id>.csv`` ones of earlier runs."""
    folder = out / CONTEXT_DIR
    if folder.is_dir():
        keep = {PurePosixPath(rel).name for rel in files}
        for old in folder.iterdir():
            if old.is_file() and _CONTEXT_FILE_RE.fullmatch(old.name) and old.name not in keep:
                old.unlink()
    if files:
        folder.mkdir(exist_ok=True)
        for rel, body in files.items():
            (out / rel).write_bytes(body)
    elif folder.is_dir() and not any(folder.iterdir()):
        folder.rmdir()


def write_review_pack(
    trades: Sequence[Trade],
    out_dir: str | Path,
    title: str,
    cfg: StrategyConfig,
    split_ts: int | None = None,
    decision_counts: Mapping[str, int] | None = None,
    data: Mapping[str, Sequence[Candle]] | None = None,
    context_source: str | None = None,
) -> dict[str, Path]:
    """Write ``trades_review.csv`` and ``trades_review.md`` into ``out_dir`` (created).

    Returns ``{"csv": path, "md": path}``. Rows are in entry order; ``split_ts`` labels
    trades TRAIN/TEST by signal time (else ALL); ``decision_counts`` (rule id -> denied
    signal candles, e.g. ``BacktestResult.decisions``) adds a denial table when given.

    ``data`` (pair -> the candles the trades were generated on, e.g. the walk-forward's full
    series) adds the trade context: MAE / MFE / stop structure / wick cells, the context
    flags and one ``context/<window>_<trade_id>.csv`` per trade (see the module docstring).
    ``context_source`` says in the ``.md`` where those candles came from. Without ``data``
    the context cells are blank and the ``.md`` says the context is unavailable. Stale
    ``context/<window>_<id>.csv`` files of an earlier pack in ``out_dir`` are removed.

    Raises ``ValueError`` (and writes nothing) if two trades share a ``(window, trade_id)``
    key.
    """
    ordered = entry_order(list(trades))
    dup = duplicate_keys(ordered, split_ts)
    if dup:
        shown = ", ".join(f"({w}, {i})" for w, i in dup)
        raise ValueError(
            f"duplicate review keys (window, trade_id): {shown}; every row of a review pack "
            "must be identifiable by its key, so renumber the trades (e.g. offset the TEST "
            "ids past the TRAIN ids)"
        )
    contexts = None if data is None else trade_contexts(ordered, data, cfg)
    ctx_list: Sequence[TradeContext | None] = contexts or [None] * len(ordered)
    refs, files = _context_files(ordered, contexts, split_ts)
    flag_lists = [auto_flags(t, cfg, c) for t, c in zip(ordered, ctx_list, strict=True)]
    rows = [
        review_row(t, cfg, split_ts, f, c, ref)
        for t, f, c, ref in zip(ordered, flag_lists, ctx_list, refs, strict=True)
    ]
    windows = [WINDOW_ALL] if split_ts is None else [WINDOW_TRAIN, WINDOW_TEST]
    flagged = sum(1 for f in flag_lists if f)
    source = context_source or "candles supplied by the caller"

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / CSV_NAME
    md_path = out / MD_NAME
    _write_context_files(out, files)
    _write_csv(csv_path, review_header(ordered), rows)

    header = _header_lines(title, ordered, windows, cfg, split_ts, flagged)
    header.append(_context_summary_line(contexts, flag_lists, source))
    sections = [
        header,
        _summary_lines(list(zip(ordered, flag_lists, strict=True)), windows, split_ts),
        _trade_table_lines(rows, flag_lists),
        _flagged_lines(ordered, rows, flag_lists),
        _context_lines(rows, contexts, source),
    ]
    if decision_counts is not None:
        sections.append(_decision_lines(decision_counts))
    sections.append(_signoff_lines(len(ordered), flagged))
    text = "\n\n".join("\n".join(s) for s in sections) + "\n"
    md_path.write_text(text, encoding="utf-8")
    return {"csv": csv_path, "md": md_path}


# ---------------------------------------------------------------------------- reading back
class ReviewRow(NamedTuple):
    """One row of a filled-in ``trades_review.csv``, as read back by :func:`load_review`."""

    window: str  # TRAIN | TEST | ALL
    trade_id: int
    pair: str
    signal_ts: int  # epoch ms of the signal candle open (from signal_time_utc)
    reviewer_ok: str  # "Y" (approved), "N" (rejected) or "" (not reviewed yet)
    reviewer_note: str

    @property
    def key(self) -> tuple[str, int]:
        """``(window, trade_id)``: the unique key of the row within its pack."""
        return self.window, self.trade_id


_TRADE_ID_RE = re.compile(r"[0-9]+")
_LOAD_COLUMNS = ("window", "trade_id", "pair", "signal_time_utc", *REVIEWER_COLUMNS)


def _split_context(middle: tuple[str, ...]) -> tuple[tuple[str, ...], str | None]:
    """``(feature columns, problem)`` of the columns between ``hold_h`` and ``ml_prob``.

    A current header ends them with :data:`CONTEXT_COLUMNS`; a pack written before those
    columns existed has none of them (still accepted). Anything in between is rejected.
    """
    n_ctx = len(CONTEXT_COLUMNS)
    if middle[-n_ctx:] == CONTEXT_COLUMNS:
        return middle[:-n_ctx], None
    if any(c in CONTEXT_COLUMNS for c in middle):
        return middle, (
            f"the trade-context columns must be exactly {','.join(CONTEXT_COLUMNS)} in this "
            "order, right before ml_prob (or all absent in a pack written before them)"
        )
    return middle, None


def _header_problem(header: tuple[str, ...]) -> str | None:
    """Why ``header`` is not one :func:`review_header` can write (or wrote before the
    trade-context columns existed), or ``None`` if it is."""
    n_head, n_tail = len(HEAD_COLUMNS), len(TAIL_COLUMNS)
    if len(header) < n_head + n_tail:
        return f"it has {len(header)} columns, fewer than the {n_head + n_tail} fixed ones"
    if header[:n_head] != HEAD_COLUMNS:
        return f"the first {n_head} columns must be {','.join(HEAD_COLUMNS)}"
    if header[-n_tail:] != TAIL_COLUMNS:
        return f"the last {n_tail} columns must be {','.join(TAIL_COLUMNS)}"
    features, problem = _split_context(header[n_head:-n_tail])
    if problem is not None:
        return problem
    if any(not c.startswith(FEATURE_PREFIX) or c == FEATURE_PREFIX for c in features):
        return (
            "the columns between hold_h and the trade-context columns (ml_prob in a pack "
            f"without them) must all be {FEATURE_PREFIX}<feature>"
        )
    if list(features) != sorted(set(features)):
        return f"the {FEATURE_PREFIX}<feature> columns must be unique and sorted"
    missing = [FEATURE_PREFIX + k for k in BASE_FEATURES if FEATURE_PREFIX + k not in features]
    if missing:
        return f"the feature columns {','.join(missing)} are missing"
    return None


def _parse_review_row(cells: Mapping[str, str]) -> ReviewRow:
    window = cells["window"].strip()
    if window not in WINDOWS:
        raise ValueError(f"window {window!r} is not one of {', '.join(WINDOWS)}")
    trade_id = cells["trade_id"].strip()
    if not _TRADE_ID_RE.fullmatch(trade_id):
        raise ValueError(f"trade_id {trade_id!r} is not a non-negative integer")
    pair = cells["pair"].strip()
    if not pair:
        raise ValueError("pair is empty")
    signal_ts = parse_time_utc(cells["signal_time_utc"], "signal_time_utc")
    ok = cells["reviewer_ok"].strip()
    if ok not in REVIEW_OK_VALUES:
        raise ValueError(
            f"reviewer_ok {ok!r} must be {REVIEW_OK_YES} (approved), {REVIEW_OK_NO} (rejected) "
            "or blank (not reviewed yet)"
        )
    return ReviewRow(window, int(trade_id), pair, signal_ts, ok, cells["reviewer_note"].strip())


def _context_file_problem(row: ReviewRow, cells: Mapping[str, str], base: Path) -> str | None:
    """Why the row's ``context_csv`` / ``context_sha256`` do not check out, or ``None``."""
    rel, digest = cells["context_csv"].strip(), cells["context_sha256"].strip()
    if not rel and not digest:
        return None  # no candle context in this pack (or for this trade)
    want = context_rel_path(row.window, row.trade_id)
    if rel != want or not digest:
        return (
            f"context_csv {rel!r} / context_sha256 {digest!r}: expected {want!r} with its "
            "sha256 (or both blank)"
        )
    path = base / PurePosixPath(rel)
    if not path.is_file():
        return f"context file {path} is missing (keep the whole review pack directory together)"
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        return f"context file {path} does not match its context_sha256 (changed after writing)"
    return None


def load_review(path: str | Path) -> list[ReviewRow]:
    """Read a (filled-in) ``trades_review.csv`` back, in file order, for the adoption check.

    Validates that the header is one :func:`write_review_pack` writes (fixed head columns,
    sorted ``f_<feature>`` columns, the trade-context columns, fixed tail; a pack written
    before the trade-context columns existed is accepted too), that every row has one cell
    per column, a window in :data:`WINDOWS` (``ALL`` never mixed with ``TRAIN``/``TEST``), an
    integer ``trade_id``, a pair, an offset-aware ``signal_time_utc`` and a ``reviewer_ok`` of
    ``Y``, ``N`` or blank (surrounding whitespace ignored), that the ``(window, trade_id)``
    keys are unique, and that every referenced context file is
    ``context/<window>_<trade_id>.csv`` next to the sheet with a matching sha256 (so the
    sheet's hash binds its context files). Raises ``ValueError`` naming the file and line
    number of the first problem. Blank lines are skipped; a UTF-8 BOM (spreadsheet export)
    is fine. Whether every verdict is ``Y`` is for the caller (``adoption.check_promotion``)
    to judge.
    """
    p = Path(path)
    rows: list[ReviewRow] = []
    first_line: dict[tuple[str, int], int] = {}
    split_pack: bool | None = None  # True: TRAIN/TEST rows; False: ALL rows
    with p.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        raw = next(reader, None)
        if raw is None:
            raise ValueError(f"{p}: line 1: empty file, expected a {CSV_NAME} header")
        header = tuple(h.strip() for h in raw)
        problem = _header_problem(header)
        if problem is not None:
            raise ValueError(f"{p}: line 1: not a {CSV_NAME} header: {problem}")
        has_context = CONTEXT_COLUMNS[0] in header
        for fields in reader:
            where = f"{p}: line {reader.line_num}"
            if not any(f.strip() for f in fields):
                continue
            if len(fields) != len(header):
                raise ValueError(f"{where}: expected {len(header)} cells, got {len(fields)}")
            cells = dict(zip(header, fields, strict=True))
            try:
                row = _parse_review_row({c: cells[c] for c in _LOAD_COLUMNS})
            except ValueError as exc:
                raise ValueError(f"{where}: {exc}") from None
            problem = _context_file_problem(row, cells, p.parent) if has_context else None
            if problem is not None:
                raise ValueError(f"{where}: {problem}")
            if row.key in first_line:
                raise ValueError(
                    f"{where}: duplicate key (window, trade_id) = ({row.window}, "
                    f"{row.trade_id}), first seen on line {first_line[row.key]}"
                )
            is_split = row.window != WINDOW_ALL
            if split_pack is not None and is_split != split_pack:
                raise ValueError(
                    f"{where}: window {row.window} mixed with "
                    f"{'TRAIN/TEST' if split_pack else 'ALL'} rows (a pack is either split "
                    "or not)"
                )
            split_pack = is_split
            first_line[row.key] = reader.line_num
            rows.append(row)
    return rows


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m research.trendbot.review_sheet",
        description="Write the human trade-by-trade review pack for a trade journal CSV.",
    )
    parser.add_argument("--journal", required=True, help="journal CSV (journal.write_journal)")
    parser.add_argument("--out-dir", required=True, help="directory for the review pack")
    parser.add_argument("--title", default=None, help="title of the review pack")
    parser.add_argument(
        "--split", default=None, help="walk-forward split, ISO-8601 UTC or ms (TRAIN before it)"
    )
    parser.add_argument(
        "--reward-risk",
        type=float,
        default=None,
        help="reward:risk the trades were generated with (default: the config default)",
    )
    parser.add_argument(
        "--fee-rate",
        type=float,
        default=None,
        help="fee per side (fraction) the trades were generated with (default: config default)",
    )
    parser.add_argument(
        "--slippage-pct",
        type=float,
        default=None,
        help="slippage in percent the trades were generated with (default: config default)",
    )
    src = parser.add_mutually_exclusive_group()
    src.add_argument(
        "--data-dir",
        default=None,
        help="candle files <PAIR>-<tf>.csv the trades were generated on (data.load_dataset); "
        "adds MAE/MFE, the stop structure, the wick depth and context/ CSVs per trade",
    )
    src.add_argument(
        "--synthetic",
        choices=WORLDS,
        default=None,
        help="synthetic world the trades were generated on (synthetic.make_world), same use",
    )
    parser.add_argument("--seed", type=int, default=None, help="synthetic seed (default 1)")
    parser.add_argument("--years", type=float, default=None, help="synthetic years (default 6)")
    return parser


def timeframe_label(timeframe_ms: int) -> str:
    """``4h`` style label of a timeframe (the candle file suffix of ``data.load_dataset``)."""
    for unit_ms, unit in ((DAY_MS, "d"), (HOUR_MS, "h"), (60_000, "m")):
        if timeframe_ms % unit_ms == 0:
            return f"{timeframe_ms // unit_ms}{unit}"
    raise ValueError(f"timeframe {timeframe_ms} ms is not a whole number of minutes")


def _cli_config(args: argparse.Namespace, parser: argparse.ArgumentParser) -> StrategyConfig:
    changes = {
        name: value
        for name, value in (
            ("reward_risk", args.reward_risk),
            ("fee_rate", args.fee_rate),
            ("slippage_pct", args.slippage_pct),
        )
        if value is not None
    }
    try:
        return StrategyConfig().with_changes(**changes)
    except ConfigError as exc:
        parser.error(str(exc))


def _cli_candles(
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
    trades: Sequence[Trade],
    cfg: StrategyConfig,
) -> tuple[dict[str, list[Candle]] | None, str | None]:
    """``(data, source)`` for the trade context from ``--data-dir`` / ``--synthetic``."""
    if args.synthetic is None:
        if args.seed is not None or args.years is not None:
            parser.error("--seed and --years only apply with --synthetic")
        if args.data_dir is None:
            return None, None
        tf = timeframe_label(cfg.timeframe_ms)
        pairs = sorted({t.pair for t in trades})
        try:
            data = load_dataset(args.data_dir, pairs, tf)
        except (OSError, ValueError) as exc:
            parser.error(f"--data-dir: {exc}")
        return data, f"{tf} candle files in {args.data_dir} (data.load_dataset)"
    seed = 1 if args.seed is None else args.seed
    years = 6.0 if args.years is None else args.years
    try:
        data, _events = make_world(args.synthetic, seed, years)
    except ValueError as exc:
        parser.error(f"--synthetic: {exc}")
    source = (
        f"synthetic world {args.synthetic} seed {seed}, {years:g} years (synthetic.make_world; "
        "verification only, never evidence about real markets)"
    )
    return data, source


def main(argv: list[str] | None = None) -> int:
    """CLI: build a review pack from a trade journal CSV written by ``journal.write_journal``.

    ``--data-dir DIR`` (the candle files) or ``--synthetic WORLD [--seed N] [--years Y]``
    supplies the candles for the per-trade context; without either the context is blank.
    """
    parser = _parser()
    args = parser.parse_args(argv)
    cfg = _cli_config(args, parser)
    try:
        split = None if args.split is None else iso_to_ms(args.split)
    except ValueError as exc:
        parser.error(f"--split: {exc}")
    try:
        trades = read_journal(args.journal)
    except (OSError, ValueError) as exc:
        parser.error(f"--journal: {exc}")
    data, source = _cli_candles(args, parser, trades, cfg)
    title = args.title or f"Trade review: {Path(args.journal).name}"
    try:
        paths = write_review_pack(
            trades, args.out_dir, title, cfg, split_ts=split, data=data, context_source=source
        )
    except ValueError as exc:
        parser.error(f"--journal: {exc}")
    contexts = None if data is None else trade_contexts(trades, data, cfg)
    flagged = sum(
        1
        for i, t in enumerate(trades)
        if auto_flags(t, cfg, None if contexts is None else contexts[i])
    )
    context = "" if data is None else f", candle context from {source}"
    print(f"{len(trades)} trades ({flagged} flagged{context}) -> {paths['csv']} and {paths['md']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
