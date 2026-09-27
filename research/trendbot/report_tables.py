"""Shared wording and table primitives of every rendered research document.

``report.py`` (REPORT.md) and ``report_calibration.py`` (CALIBRATION.md, POWER.md) both build
on this module, so a rule is worded once: the ground-truth statements of the synthetic
worlds, the win-rate statement, the layer (C1) wording, the drawdown rule with its D7
rationale, the multiplicity rule (C4), the cost lines (A1/A4), the adoption-path block and
the disclaimer. It also holds the metric table that puts TRAIN and TEST side by side, and
small formatting helpers. Nothing here runs a backtest.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path

from .config import StrategyConfig
from .metrics import (
    DD_CAP_PCT,
    DD_CAP_RATIONALE,
    DD_CAP_STREAK_P,
    ROBUST_MIN_N,
    Summary,
    lb_confidence,
)
from .ml_filter import MLFilter
from .models import Trade
from .walkforward import GUARD_VARIANT, ML_VARIANT, WalkForwardResult


DEFAULT_CFG = StrategyConfig()

SECTION_TITLES = (
    "1. No win rate is promised or targeted",
    "2. Data provenance",
    "3. Baseline: TRAIN vs TEST",
    "4. Strategy discovery: TRAIN vs TEST",
    "5. Entry layers (ML filter, expectancy guard): TRAIN vs TEST",
    "6. Verdict",
    "7. Why signals were rejected (decision counts per rule)",
    "8. Invariant audit of every backtest",
    "9. Journal rules at the end of TRAIN and at the end of TEST",
    "10. Journals and human review packs",
    "11. Adoption path and current stage",
    "12. Risk disclaimer",
)


def section_heading(i: int) -> list[str]:
    """``## <title of REPORT.md section i>`` and a blank line."""
    return [f"## {SECTION_TITLES[i - 1]}", ""]


WORLD_TRUTH: Mapping[str, tuple[str, str]] = {
    "null": (
        "martingale prices (zero drift): no entry/exit rule has an edge before costs, so every "
        "strategy has negative expectancy after fees and slippage.",
        "nothing should be ROBUST; the expected labels are NO-EDGE, TRAIN-ONLY or UNTESTED, "
        "and a ROBUST label here is a false positive.",
    ),
    "planted": (
        "after every up-closing volume-spike candle the next 12 candles get extra positive "
        "drift (0.7 sigma per candle) over the WHOLE history; the drift is offset elsewhere, "
        "so buy-and-hold gains nothing from it.",
        "the base rules (which require a volume spike) should show positive expectancy on "
        "TRAIN and TEST; ROBUST is the correct label when the TEST sample is large enough.",
    ),
    "decay": (
        "the planted effect exists only BEFORE the 70% split; the TEST period is exactly "
        "the null world.",
        "a positive TRAIN expectancy that does not survive TEST: TRAIN-ONLY (or UNTESTED when "
        "the TEST noise is positive but indistinguishable from zero); ROBUST is a false "
        "positive.",
    ),
    "hour_edge": (
        "the planted effect exists only for spike candles closing 12:00-20:00 UTC; spikes "
        "closing at other hours trigger nothing.",
        "the hour-blind base is diluted (weak or no edge); an hour-aware filter fitted on "
        "TRAIN has something real to learn and should raise TEST expectancy versus the base.",
    ),
    "zero_edge": (
        "the planted mechanism at 0.15 sigma per candle: a real, positive PRE-cost edge that "
        "fees and slippage eat, so the base strategy's expected NET R is ~0 in both windows "
        "(the H0 boundary).",
        "nothing should be ROBUST: an edge of ~0R after costs is not worth trading, so every "
        "ROBUST label here is a false positive at the boundary of the null hypothesis.",
    ),
}

WIN_RATE_STATEMENT = (
    "This report does NOT promise, target or optimise a win rate, and nothing in this package "
    "selects on one. A win rate is meaningless without the reward:risk it was earned at: with "
    "the mandatory minimum of 2:1 a strategy breaks even at about 33% winners before costs, "
    "while a 90% win rate with a 1:9 payoff still loses money. A backtest win rate near 90% is "
    "evidence of curve-fitting or look-ahead, not of skill. The target metric is POSITIVE "
    "EXPECTANCY (average R per trade, net of fees and slippage) that survives a 70/30 "
    "chronological walk-forward with controlled drawdown, with every trade planned at "
    "reward:risk >= 2:1. Win rate appears below only as context and is labelled 'context "
    "only' wherever it is shown."
)

DISCLAIMER = (
    "This is a research and testing tool, not financial advice and not a recommendation to "
    "trade. Backtests and synthetic worlds are simplified models; past or simulated results "
    "do not predict future results. Crypto trading can result in the total loss of the "
    "capital used."
)

COINBASE_FEE_NOTE = (
    "Coinbase Advanced Trade taker fees at low volume tiers are several times Binance's, so "
    "a Coinbase run must pass the account's real tier with `--fee-rate` (a fraction of "
    "notional per side: 0.006 = 0.6%) and `--exchange-id coinbase`; the default costs would "
    "understate them."
)

LAYER_WORDING = (
    "An entry layer can only VETO an entry that passed every mandatory rule; it never approves "
    "an entry a rule denies. A veto can free R6 budget or change R9 state, admitting other "
    "rule-compliant trades, so a layer's journal is not a subset of the base journal: both "
    "directions are counted below, matched by (pair, signal time)."
)

GATE_WORDING = (
    "A fitted layer's TRAIN gate is out of sample (CONTRACT v4 D8): its purged TRAIN "
    "candidates are split 70/30 in time order, the layer is fitted on the first 70% (purged at "
    "the inner boundary) and that inner filter is backtested on the rest of TRAIN. That "
    "out-of-sample TRAIN window is what the label judges (NO-EDGE gate, trade counts, the "
    "TRAIN drawdown bootstrap) and what the adoption record binds as the TRAIN journal; the "
    "in-sample full-TRAIN figure is context only. The model applied to TEST is still fitted "
    "on ALL purged TRAIN candidates."
)

ADOPTION_PATH_LINES = (
    "## Adoption path",
    "",
    "The only path to real money (enforced by `adoption.py`; no step can be skipped): "
    "BACKTEST -> WALK_FORWARD (ROBUST + drawdown check) -> HUMAN_REVIEW of EVERY trade -> "
    ">= 14 days on Binance testnet with zero rule violations -> LIVE. A synthetic result never "
    "gets past WALK_FORWARD: `adoption check` blocks every record whose provenance is "
    "`synthetic:<world>:<seed>` with `ADOPT_provenance`. A calibration writes no adoption "
    "records; it measures how often the labels are right when the truth is known.",
)

DD_RULE_SHORT = (
    f"dd_ok = TEST mark-to-market max drawdown <= min({DD_CAP_PCT:g}%, 95th percentile of the "
    "max drawdown of TRAIN trade sequences bootstrapped at the TEST length)"
)

DD_RULE = (
    "Drawdown (CONTRACT.md v3 C5, cap revised by v4 D7): every result reports the realised "
    "(closed-trade) and the mark-to-market (open positions valued at each 4H close) max "
    f"drawdown; dd_ok = TEST mark-to-market max drawdown <= min({DD_CAP_PCT:g}%, the 95th "
    "percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length, "
    "in percent of equity at the risk each trade took). Why the cap: "
    f"{DD_CAP_RATIONALE}."
)


def multiplicity_rule(m: int, alpha: float) -> str:
    """One sentence stating the C4 label rule with the numbers used."""
    conf = lb_confidence(m, alpha)
    return (
        f"ROBUST requires at least {ROBUST_MIN_N} trades in each window, TRAIN and TEST avg R "
        f"> 0, and a one-sided {conf:.2%} lower bound of the TEST mean above zero for BOTH an "
        f"iid and a calendar-month block bootstrap (the more conservative is used): alpha "
        f"{alpha:g} is split over the m = {m} pre-registered candidates (base, "
        f"discovery-selected, {ML_VARIANT}, {GUARD_VARIANT}), so when none has an edge the "
        f"chance that ANY is called ROBUST is at most about {alpha:.0%}; a positive TEST mean "
        "that fails only the bound is UNTESTED (positive but not distinguishable from zero "
        "after multiplicity correction); the rule was fixed in advance and is never tuned on "
        "TEST outcomes."
    )


# ---------------------------------------------------------------------------- formatting
def sr(x: float | None, digits: int = 3) -> str:
    """Signed number (``+0.000`` for zero, ``n/a`` for None / non-finite)."""
    if x is None or not math.isfinite(x):
        return "n/a"
    text = f"{x:+.{digits}f}"
    return "+" + text[1:] if float(text) == 0.0 else text


def pct(x: float | None) -> str:
    return "n/a" if x is None or not math.isfinite(x) else f"{x:.2f}%"


def ci_text(s: Summary) -> str:
    """iid bootstrap 90% CI of avg R, ``n/a`` without trades."""
    return f"[{sr(s.ci90_low)}, {sr(s.ci90_high)}]" if s.n else "n/a"


def block_ci_text(s: Summary) -> str:
    if not s.n:
        return "n/a"
    return f"[{sr(s.block_ci90_low)}, {sr(s.block_ci90_high)}] ({s.n_blocks} months)"


def lbs_text(s: Summary) -> str:
    """``iid / block`` one-sided lower bounds at 1 - alpha/m."""
    return f"{sr(s.iid_lb)} / {sr(s.block_lb)}" if s.n else "n/a"


def pf_text(x: float | None) -> str:
    return "n/a (no losses)" if x is None else f"{x:.2f}"


def exits_text(s: Summary) -> str:
    c = s.exit_counts
    return f"{c.get('SL', 0)} / {c.get('TP', 0)} / {c.get('END', 0)}"


def cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def safe_name(variant: str) -> str:
    """File-name form of a variant id (``base+ml`` -> ``base_plus_ml``)."""
    return variant.replace("+", "_plus_").replace("/", "_")


def journal_paths(out_dir: Path, variant: str) -> tuple[Path, Path]:
    """The (TRAIN, TEST) journals an adoption record binds (TRAIN = the judged window)."""
    safe = safe_name(variant)
    return out_dir / "journals" / f"{safe}_train.csv", out_dir / "journals" / f"{safe}_test.csv"


def insample_journal_path(out_dir: Path, variant: str) -> Path:
    """A fitted layer's in-sample full-TRAIN journal (context only, not bound by a record)."""
    return out_dir / "journals" / f"{safe_name(variant)}_train_insample.csv"


def rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def fitted_model(r: WalkForwardResult) -> MLFilter | None:
    """The fitted ``MLFilter`` behind a walk-forward's entry filter, if there is one."""
    ml = getattr(r.entry_filter, "ml", None)
    return ml if isinstance(ml, MLFilter) else None


def renumbered_test(r: WalkForwardResult) -> list[Trade]:
    """TEST trades with ids offset past the judged TRAIN journal's ids (unique per variant)."""
    offset = max((t.trade_id for t in r.label_train.trades), default=0)
    return [replace(t, trade_id=t.trade_id + offset) for t in r.test.trades]


def check_cell(reasons: Sequence[str]) -> str:
    if not reasons:
        return "PASS"
    rules = sorted({r.split("]", 1)[0].lstrip("[") for r in reasons})
    return "BLOCKED by " + ", ".join(rules)


# ---------------------------------------------------------------------------- metric table
Column = tuple[str, Summary, float | None]  # (header, summary, mark-to-market max DD %)


def columns(r: WalkForwardResult, name: str = "") -> list[Column]:
    """The TRAIN and TEST columns of one walk-forward (header prefix ``name``).

    A fitted layer gets THREE: its in-sample full-TRAIN column (context), its D8
    out-of-sample TRAIN gate (the TRAIN the label judges) and TEST.
    """
    prefix = f"{name} " if name else ""
    cols: list[Column] = [
        (f"{prefix}{r.train_tag}", r.train_summary, r.train_mtm_dd_pct if r.ran else None)
    ]
    if r.gate is not None:
        gate_dd = r.gate.mtm_dd_pct if r.gate.ran else None
        cols.append((f"{prefix}{r.gate_tag}", r.gate.summary, gate_dd))
    cols.append((f"{prefix}TEST", r.test_summary, r.test_mtm_dd_pct if r.ran else None))
    return cols


_METRIC_ROWS: tuple[tuple[str, object], ...] = (
    ("trades (n)", lambda s, _d: str(s.n)),
    ("avg R (expectancy, the target metric)", lambda s, _d: sr(s.avg_r)),
    ("iid bootstrap 90% CI of avg R", lambda s, _d: ci_text(s)),
    ("calendar-month block bootstrap 90% CI of avg R", lambda s, _d: block_ci_text(s)),
    (
        "one-sided lower bound of avg R at 1 - alpha/m, iid / block",
        lambda s, _d: f"{lbs_text(s)} ({s.lb_confidence:.2%})" if s.n else "n/a",
    ),
    ("adjusted lower bound used by the label (the smaller)", lambda s, _d: sr(s.adj_lb)),
    ("t-stat of avg R", lambda s, _d: sr(s.t_stat, 2)),
    ("total R", lambda s, _d: sr(s.total_r, 2)),
    ("profit factor", lambda s, _d: pf_text(s.profit_factor)),
    ("max drawdown % realised (closed trades)", lambda s, _d: pct(s.max_dd_pct)),
    ("max drawdown % mark-to-market (4H closes)", lambda _s, d: pct(d)),
    ("max drawdown R (closed trades)", lambda s, _d: f"{s.max_dd_r:.2f}"),
    (
        "win rate (context only, never a target)",
        lambda s, _d: f"{s.win_rate:.1%}" if s.n else "n/a",
    ),
    ("exits SL / TP / END", lambda s, _d: exits_text(s)),
    ("avg hold (h)", lambda s, _d: f"{s.avg_hold_h:.1f}"),
)


def metric_table(cols: Sequence[Column]) -> list[str]:
    """Rows = metrics, one column per ``(header, Summary, MTM max DD %)``."""
    lines = [
        "| metric | " + " | ".join(h for h, _, _ in cols) + " |",
        "|---|" + "---:|" * len(cols),
    ]
    for name, fmt in _METRIC_ROWS:
        cells = [fmt(s, d) for _, s, d in cols]  # type: ignore[operator]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return lines


def dd_rule_line(r: WalkForwardResult) -> str:
    """The C5/D7 drawdown rule, its verdict with both TEST drawdowns and the limit, and the
    judged TRAIN window's drawdowns for comparison, for one walk-forward."""
    ok = "passed" if r.dd_ok else "FAILED"
    rule = (
        f"Drawdown check (CONTRACT v3 C5 / v4 D7; {DD_RULE_SHORT}; the {DD_CAP_PCT:g}% cap is "
        f"about {DD_CAP_PCT:g} consecutive full-size losses, a streak of probability "
        f"(2/3)^{DD_CAP_PCT:g} = {DD_CAP_STREAK_P:.2%} at the 2:1 break-even win probability "
        "p* = 1/3)"
    )
    if r.max_dd_pct < DD_CAP_PCT:
        rule += f", tightened for this run to {r.max_dd_pct:g}%"
    if not r.ran:
        return f"{rule}: {ok}. {r.dd_reason}"
    train = (
        f"TRAIN max drawdown: realised {pct(r.train_summary.max_dd_pct)}, mark-to-market "
        f"{pct(r.train_mtm_dd_pct)}"
    )
    if r.gate is not None:
        train = (
            f"out-of-sample gate max drawdown: realised {pct(r.label_train_summary.max_dd_pct)}, "
            f"mark-to-market {pct(r.label_train_mtm_dd_pct)} (in-sample {train})"
        )
    return f"{rule}: **{ok}**. {r.dd_reason} For comparison, {train}."


def label_lines(r: WalkForwardResult) -> list[str]:
    return [f"**Label: {r.label}.** {r.label_reason}", "", dd_rule_line(r)]


# ---------------------------------------------------------------------------- costs
def _fee_cost_r(trades: Sequence[Trade]) -> tuple[float, float] | None:
    """(mean fees per trade in R, median stop distance in % of entry) of closed trades."""
    closed = [t for t in trades if t.is_closed and t.risk_amount > 0 and t.entry_price > 0]
    if not closed:
        return None
    fees_r = statistics.fmean(t.fees / t.risk_amount for t in closed)
    stop_pct = statistics.median((t.entry_price - t.stop) / t.entry_price * 100 for t in closed)
    return fees_r, stop_pct


def _default_fee(cfg: StrategyConfig) -> bool:
    return cfg.fee_rate == DEFAULT_CFG.fee_rate


def stress_line(cfg: StrategyConfig) -> str | None:
    """The D4 stop-fill stress statement, or None for the default touch fill model."""
    k = cfg.stop_fill_wick_k
    if k <= 0:
        return None
    return (
        f"- **Stop-fill STRESS model (CONTRACT v4 D4), k = {k:g}:** every non-gap stop fills at "
        f"stop - {k:g} x (stop - exit-candle low), then slippage, instead of at the stop, so a "
        "stopped trade can lose MORE than 1R even without a gap (sizing still plans the loss at "
        "the stop). Stress configs are test-only (`is_test_only`): nothing in this run is "
        "adoptable, gets an adoption record or counts as a holdout look."
    )


def cost_lines(cfg: StrategyConfig, trades: Sequence[Trade] = ()) -> list[str]:
    """The cost assumptions of a run (CONTRACT.md v2 A1/A4, v4 D4), for the provenance
    section."""
    default = " (the default: Binance spot taker, no discounts)" if _default_fee(cfg) else ""
    lines = [
        f"- **Costs used in every backtest:** fee {cfg.fee_rate:.3%} of notional per side"
        f"{default} (`--fee-rate {cfg.fee_rate:g}`), charged on the entry AND on the exit; "
        f"slippage {cfg.slippage_pct:g}% (`--slippage-pct {cfg.slippage_pct:g}`) against the "
        "trade on market fills (entries and stop exits; take-profit limit exits get none); "
        f"exchange `{cfg.exchange_id}` (`--exchange-id`; `EXCHANGE:{cfg.exchange_id}` news "
        f"events block every pair). Starting capital {cfg.starting_capital:,.0f} per window.",
        "- Cost-aware sizing and targets (CONTRACT.md v2 A1): the planned risk is the ALL-IN "
        "loss at the stop (the stop fill after slippage plus both fees), so a clean stop is "
        f"exactly -1R and the target is placed so that a take-profit nets exactly "
        f"+{cfg.reward_risk:g}R after fees; the target's price distance is therefore more than "
        f"{cfg.reward_risk:g}x the stop distance. Only a gap through the stop loses more than 1R"
        + (" under the touch fill model." if cfg.stop_fill_wick_k <= 0 else "."),
        f"- {COINBASE_FEE_NOTE}",
    ]
    stress = stress_line(cfg)
    if stress is not None:
        lines.append(stress)
    if cfg.exchange_id != DEFAULT_CFG.exchange_id and _default_fee(cfg):
        lines.append(
            f"- **WARNING: exchange `{cfg.exchange_id}` was run with the Binance default fee "
            f"{cfg.fee_rate:.3%} per side.** Unless that is the account's real tier, every "
            "number in this report is optimistic."
        )
    measured = _fee_cost_r(trades)
    if measured is not None:
        fees_r, stop_pct = measured
        lines.append(
            f"- Measured on the baseline's TRAIN and TEST trades: fees alone cost "
            f"{fees_r:.2f}R per trade on average (median stop distance {stop_pct:.2f}% of the "
            "entry price), so the expectancy below is only as good as the fee rate above."
        )
    return lines
