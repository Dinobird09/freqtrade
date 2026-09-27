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

Output is deterministic: stable row order, fixed-decimal number formatting, no timestamps
of the run itself. The summary reports expectancy (avg R) only; win rate is deliberately
not shown because it is never a target.
"""

from __future__ import annotations

import argparse
import csv
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NamedTuple

from .config import RULE_IDS, ConfigError, StrategyConfig
from .journal import FEATURE_PREFIX, iso_to_ms, ms_to_iso, read_journal
from .models import EXIT_END, EXIT_SL, EXIT_TP, HOUR_MS, Trade
from .news import parse_time_utc
from .sizing import loss_per_unit, stop_fill_price


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
TAIL_COLUMNS: tuple[str, ...] = ("ml_prob", "notes", "auto_flags", "reviewer_ok", "reviewer_note")
REVIEWER_COLUMNS = ("reviewer_ok", "reviewer_note")
FLAG_SEPARATOR = "; "

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


def auto_flags(trade: Trade, cfg: StrategyConfig) -> list[str]:
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
    model cannot produce. Returns ``[]`` for a clean trade.
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
    return flags


# ---------------------------------------------------------------------------- CSV rows
def review_header(trades: Sequence[Trade]) -> tuple[str, ...]:
    """CSV header: fixed head, ``f_<feature>`` columns (base set + extras, sorted), tail."""
    keys = set(BASE_FEATURES)
    for t in trades:
        keys.update(t.features)
    return HEAD_COLUMNS + tuple(FEATURE_PREFIX + k for k in sorted(keys)) + TAIL_COLUMNS


def review_row(
    trade: Trade, cfg: StrategyConfig, split_ts: int | None, flags: Sequence[str] | None = None
) -> dict[str, str]:
    """All review cells of one trade as text (feature cells keyed ``f_<name>``)."""
    if flags is None:
        flags = auto_flags(trade, cfg)
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
            "- **TEST-ONLY config: the R4 regime filter is OFF. This variant can never be "
            "adopted.**"
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
def write_review_pack(
    trades: Sequence[Trade],
    out_dir: str | Path,
    title: str,
    cfg: StrategyConfig,
    split_ts: int | None = None,
    decision_counts: Mapping[str, int] | None = None,
) -> dict[str, Path]:
    """Write ``trades_review.csv`` and ``trades_review.md`` into ``out_dir`` (created).

    Returns ``{"csv": path, "md": path}``. Rows are in entry order; ``split_ts`` labels
    trades TRAIN/TEST by signal time (else ALL); ``decision_counts`` (rule id -> denied
    signal candles, e.g. ``BacktestResult.decisions``) adds a denial table when given.
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
    flag_lists = [auto_flags(t, cfg) for t in ordered]
    rows = [review_row(t, cfg, split_ts, f) for t, f in zip(ordered, flag_lists, strict=True)]
    windows = [WINDOW_ALL] if split_ts is None else [WINDOW_TRAIN, WINDOW_TEST]
    flagged = sum(1 for f in flag_lists if f)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / CSV_NAME
    md_path = out / MD_NAME
    _write_csv(csv_path, review_header(ordered), rows)

    sections = [
        _header_lines(title, ordered, windows, cfg, split_ts, flagged),
        _summary_lines(list(zip(ordered, flag_lists, strict=True)), windows, split_ts),
        _trade_table_lines(rows, flag_lists),
        _flagged_lines(ordered, rows, flag_lists),
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


def _header_problem(header: tuple[str, ...]) -> str | None:
    """Why ``header`` is not one :func:`review_header` can write, or ``None`` if it is."""
    n_head, n_tail = len(HEAD_COLUMNS), len(TAIL_COLUMNS)
    if len(header) < n_head + n_tail:
        return f"it has {len(header)} columns, fewer than the {n_head + n_tail} fixed ones"
    if header[:n_head] != HEAD_COLUMNS:
        return f"the first {n_head} columns must be {','.join(HEAD_COLUMNS)}"
    if header[-n_tail:] != TAIL_COLUMNS:
        return f"the last {n_tail} columns must be {','.join(TAIL_COLUMNS)}"
    features = header[n_head:-n_tail]
    if any(not c.startswith(FEATURE_PREFIX) or c == FEATURE_PREFIX for c in features):
        return f"the columns between hold_h and ml_prob must all be {FEATURE_PREFIX}<feature>"
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


def load_review(path: str | Path) -> list[ReviewRow]:
    """Read a (filled-in) ``trades_review.csv`` back, in file order, for the adoption check.

    Validates that the header is one :func:`write_review_pack` writes (fixed head columns,
    sorted ``f_<feature>`` columns, fixed tail), that every row has one cell per column, a
    window in :data:`WINDOWS` (``ALL`` never mixed with ``TRAIN``/``TEST``), an integer
    ``trade_id``, a pair, an offset-aware ``signal_time_utc`` and a ``reviewer_ok`` of
    ``Y``, ``N`` or blank (surrounding whitespace ignored), and that the ``(window,
    trade_id)`` keys are unique. Raises ``ValueError`` naming the file and line number of
    the first problem. Blank lines are skipped; a UTF-8 BOM (spreadsheet export) is fine.
    Whether every verdict is ``Y`` is for the caller (``adoption.check_promotion``) to judge.
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


def main(argv: list[str] | None = None) -> int:
    """CLI: build a review pack from a trade journal CSV written by ``journal.write_journal``."""
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
    args = parser.parse_args(argv)
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
        cfg = StrategyConfig().with_changes(**changes)
    except ConfigError as exc:
        parser.error(str(exc))
    try:
        split = None if args.split is None else iso_to_ms(args.split)
    except ValueError as exc:
        parser.error(f"--split: {exc}")
    try:
        trades = read_journal(args.journal)
    except (OSError, ValueError) as exc:
        parser.error(f"--journal: {exc}")
    title = args.title or f"Trade review: {Path(args.journal).name}"
    try:
        paths = write_review_pack(trades, args.out_dir, title, cfg, split_ts=split)
    except ValueError as exc:
        parser.error(f"--journal: {exc}")
    flagged = sum(1 for t in trades if auto_flags(t, cfg))
    print(f"{len(trades)} trades ({flagged} flagged) -> {paths['csv']} and {paths['md']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
