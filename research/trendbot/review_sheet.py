"""Human review pack: the trade-by-trade list a person must inspect before any adoption.

Adoption step 3 requires a human to review the ACTUAL trades, not just summary statistics.
:func:`write_review_pack` writes two files into ``out_dir``:

- ``trades_review.csv``: one row per trade in entry order (``entry_ts``, then ``trade_id``)
  with the numbers a reviewer needs to check a trade by hand (planned RR, the pair's risk
  cap, notional, hold time), the trade's features, the machine sanity flags, and EMPTY
  ``reviewer_ok`` (Y/N) and ``reviewer_note`` columns for the reviewer's verdict.
- ``trades_review.md``: header and counts, the statement that EVERY row must be inspected,
  a per-window summary (TRAIN | TEST side by side when a walk-forward split is given), the
  table of all trades, the flagged trades, the rule-denial counts and a sign-off block.

:func:`auto_flags` are sanity checks that HELP the reviewer; they never replace the review.
An unflagged trade is not an approved trade. Each flag is ``"<code>: <detail>"`` where
``<code>`` is one of :data:`FLAG_CODES`.

Window labels: with ``split_ts`` a trade is ``TRAIN`` if ``signal_ts < split_ts`` and
``TEST`` otherwise (entries are windowed by their signal candle, like the backtester);
without a split every trade is ``ALL``.

Output is deterministic: stable row order, fixed-decimal number formatting, no timestamps
of the run itself. The summary reports expectancy (avg R) only; win rate is deliberately
not shown because it is never a target.
"""

from __future__ import annotations

import argparse
import csv
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

from .config import RULE_IDS, ConfigError, StrategyConfig
from .journal import FEATURE_PREFIX, iso_to_ms, ms_to_iso, read_journal
from .models import EXIT_END, EXIT_SL, EXIT_TP, HOUR_MS, Trade


CSV_NAME = "trades_review.csv"
MD_NAME = "trades_review.md"

WINDOW_TRAIN = "TRAIN"
WINDOW_TEST = "TEST"
WINDOW_ALL = "ALL"

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
    "planned_rr",
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

# Outcome thresholds (in R). Under the exit model an SL loses about 1R plus costs and a TP
# wins reward_risk R minus costs, so anything outside these bands needs a human look.
LOSS_OUTLIER_R = -1.25
WIN_SLACK_R = 0.25
GAP_SLACK_R = 0.25  # a loss this far beyond a normal cost-model stop-out is a gap/slippage
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
    """(target - entry) / (entry - stop), or ``None`` if the stop is not below the entry."""
    risk = trade.entry_price - trade.stop
    if not (math.isfinite(risk) and risk > 0):
        return None
    return (trade.target - trade.entry_price) / risk


def hold_hours(trade: Trade) -> float | None:
    """``(exit_ts - entry_ts)`` in hours (same definition as ``metrics.avg_hold_h``)."""
    if trade.exit_ts is None:
        return None
    return (trade.exit_ts - trade.entry_ts) / HOUR_MS


def expected_sl_r(trade: Trade, cfg: StrategyConfig) -> float | None:
    """R of a NORMAL stop-out under the cost model: fill at ``stop * (1 - slippage)``, fees
    ``fee_rate`` on both sides. Tight stops make this worse than -1R (costs vs distance).
    ``None`` if the trade has no positive planned risk."""
    if not (trade.risk_amount > 0 and trade.qty > 0):
        return None
    fill = trade.stop * (1 - cfg.slippage_pct / 100.0)
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


# ---------------------------------------------------------------------------- auto flags
def _non_finite_flags(trade: Trade) -> list[str]:
    bad = [
        name
        for name in _NUMERIC_FIELDS
        if getattr(trade, name) is not None and not math.isfinite(float(getattr(trade, name)))
    ]
    return [f"{FLAG_NON_FINITE}: non-finite {', '.join(bad)}"] if bad else []


def _level_flags(trade: Trade, cfg: StrategyConfig) -> list[str]:
    flags: list[str] = []
    entry, stop, target = trade.entry_price, trade.stop, trade.target
    problems = []
    if stop >= entry:
        problems.append(f"stop {_num(stop, 6)} >= entry {_num(entry, 6)}")
    if target <= entry:
        problems.append(f"target {_num(target, 6)} <= entry {_num(entry, 6)}")
    if problems:
        flags.append(f"{FLAG_BAD_LEVELS}: {' and '.join(problems)} (not a valid long)")
    rr = planned_rr(trade)
    if rr is not None and not _close(rr, cfg.reward_risk):
        if rr < cfg.reward_risk:
            flags.append(
                f"{FLAG_RR_BELOW}: planned RR {_num(rr, 4)} < required {_num(cfg.reward_risk, 2)}"
            )
        else:
            flags.append(
                f"{FLAG_RR_ABOVE}: planned RR {_num(rr, 4)} != configured "
                f"{_num(cfg.reward_risk, 2)} (target not derived from this config)"
            )
    if stop < entry and not _close(trade.risk_amount, trade.qty * (entry - stop)):
        flags.append(
            f"{FLAG_SIZE}: risk_amount {_num(trade.risk_amount, 4)} != qty*(entry-stop) "
            f"{_num(trade.qty * (entry - stop), 4)} (size not derived from the stop distance)"
        )
    return flags


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
    """Name the likely cause: a gap/slippage outlier, or costs that are large versus a tight
    stop (then EVERY stop-out of that trade is worse than -1.25R, i.e. realised risk exceeds
    the planned risk)."""
    expected = expected_sl_r(trade, cfg)
    head = f"{FLAG_LOSS_OUTLIER}: loss {_signed(r)}R worse than {_signed(LOSS_OUTLIER_R, 2)}R"
    if expected is None:
        return f"{head} (gap-through or slippage outlier)"
    if r < expected - GAP_SLACK_R or expected >= LOSS_OUTLIER_R:
        return f"{head} (a normal stop-out would be {_signed(expected)}R: gap-through or slippage)"
    return (
        f"{head} (a normal stop-out already costs {_signed(expected)}R: fees and slippage are "
        "large versus the stop distance)"
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
    non-finite numbers; stop >= entry or target <= entry; planned RR below (or, a sign the
    wrong config was passed, above) ``cfg.reward_risk``; ``risk_amount`` not equal to
    ``qty * (entry - stop)``; unknown pair; ``risk_pct`` above the pair cap; entry before the
    signal candle closed (look-ahead); exit before entry; zero hold time (exit in the fill
    candle); missing or incomplete exit; window-end forced exit (``END``); loss worse than
    -1.25R; win above ``reward_risk + 0.25`` R; ``r_multiple != pnl / risk_amount``; SL/TP
    outcomes the exit model cannot produce. Returns ``[]`` for a clean trade.
    """
    flags = _non_finite_flags(trade)
    flags += _level_flags(trade, cfg)
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
        "planned_rr": _num(planned_rr(trade), 4),
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
    ("RR", "planned_rr"),
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
        "is not an approved trade. Record a verdict for every trade in the `reviewer_ok` (Y/N) "
        f"and `reviewer_note` columns of `{CSV_NAME}`.",
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
        f"- Config: `{cfg.variant_id()}`, reward:risk {_num(cfg.reward_risk, 2)}, risk caps {caps}"
    )
    if cfg.is_test_only:
        lines.append(
            "- **TEST-ONLY config: the R4 regime filter is OFF. This variant can never be "
            "adopted.**"
        )
    dup = sorted(k for k, v in Counter(t.trade_id for t in trades).items() if v > 1)
    if dup:
        lines.append(f"- **WARNING: duplicate trade ids {', '.join(map(str, dup))}**")
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
    """
    ordered = entry_order(list(trades))
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
    args = parser.parse_args(argv)
    try:
        cfg = StrategyConfig()
        if args.reward_risk is not None:
            cfg = cfg.with_changes(reward_risk=args.reward_risk)
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
    paths = write_review_pack(trades, args.out_dir, title, cfg, split_ts=split)
    flagged = sum(1 for t in trades if auto_flags(t, cfg))
    print(f"{len(trades)} trades ({flagged} flagged) -> {paths['csv']} and {paths['md']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
