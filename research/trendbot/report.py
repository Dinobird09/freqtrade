"""Markdown rendering of the research outputs: REPORT.md, CALIBRATION.md and POWER.md.

Split out of ``run_research`` (which runs the pipeline, writes the evidence files and owns
the CLI). Nothing here runs a backtest: every function formats results that were already
computed, plus the journal-rule audit that section 9 shows. The text constants (section
titles, ground-truth statements, the win-rate statement, the adoption-path block and the
disclaimer) live here so the report and the calibration share one wording.

Conventions every rendered table follows: each header names its TRAIN and TEST columns (or a
TRAIN/TEST window column), win rate only ever appears labelled "context only", and the
statistics are the ones of CONTRACT.md v3: expectancy with multiplicity-adjusted iid and
calendar-month block bootstrap lower bounds (C4), realised and mark-to-market drawdowns
with the TRAIN-bootstrap limit (C5), and layer vetoes counted in both directions (C1).
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import replace
from itertools import pairwise
from pathlib import Path
from typing import TYPE_CHECKING

from .adoption import BACKTEST, HUMAN_REVIEW, STAGES, WALK_FORWARD, config_fingerprint
from .config import RULE_IDS, StrategyConfig
from .data import gap_report
from .journal import ms_to_iso
from .journal_rules import Adaptation, audit
from .metrics import DD_CAP_PCT, LABELS, ROBUST_MIN_N, Summary, lb_confidence
from .ml_filter import FEATURES, MLFilter
from .models import HOUR_MS, Candle, Trade
from .walkforward import (
    BASE,
    GUARD_LAYER,
    GUARD_VARIANT,
    ML_LAYER,
    ML_VARIANT,
    LayerDiff,
    WalkForwardResult,
)


if TYPE_CHECKING:  # pragma: no cover - typing only (run_research imports this module)
    from .backtester import BacktestResult
    from .run_research import AdoptionStatus, Dataset, Research


NO_ROBUST = "No robust result found."
MAX_VIOLATIONS_SHOWN = 20
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


DD_RULE = (
    "Drawdown (CONTRACT.md v3 C5): every result reports the realised (closed-trade) and the "
    "mark-to-market (open positions valued at each 4H close) max drawdown; dd_ok = TEST "
    "mark-to-market max drawdown <= min(20%, the 95th percentile of the max drawdown of TRAIN "
    "trade sequences bootstrapped at the TEST length, in percent of equity at the risk each "
    "trade took)."
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


def _ci(s: Summary) -> str:
    return f"[{sr(s.ci90_low)}, {sr(s.ci90_high)}]" if s.n else "n/a"


def _block_ci(s: Summary) -> str:
    if not s.n:
        return "n/a"
    return f"[{sr(s.block_ci90_low)}, {sr(s.block_ci90_high)}] ({s.n_blocks} months)"


def _lbs(s: Summary) -> str:
    return f"{sr(s.iid_lb)} / {sr(s.block_lb)}" if s.n else "n/a"


def _pf(x: float | None) -> str:
    return "n/a (no losses)" if x is None else f"{x:.2f}"


def _exits(s: Summary) -> str:
    c = s.exit_counts
    return f"{c.get('SL', 0)} / {c.get('TP', 0)} / {c.get('END', 0)}"


def cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def safe_name(variant: str) -> str:
    """File-name form of a variant id (``base+ml`` -> ``base_plus_ml``)."""
    return variant.replace("+", "_plus_").replace("/", "_")


def journal_paths(out_dir: Path, variant: str) -> tuple[Path, Path]:
    safe = safe_name(variant)
    return out_dir / "journals" / f"{safe}_train.csv", out_dir / "journals" / f"{safe}_test.csv"


def rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _window(r: WalkForwardResult, train: bool, end_ts: int) -> str:
    if train:
        return f"start .. {ms_to_iso(r.split_ts)}"
    return f"{ms_to_iso(r.split_ts)} .. {ms_to_iso(end_ts)}"


# ---------------------------------------------------------------------------- metric table
Column = tuple[str, Summary, float | None]  # (header, summary, mark-to-market max DD %)


def columns(r: WalkForwardResult, name: str = "") -> list[Column]:
    """The TRAIN and TEST columns of one walk-forward (header prefix ``name``)."""
    prefix = f"{name} " if name else ""
    return [
        (f"{prefix}{r.train_tag}", r.train_summary, r.train_mtm_dd_pct if r.ran else None),
        (f"{prefix}TEST", r.test_summary, r.test_mtm_dd_pct if r.ran else None),
    ]


_METRIC_ROWS: tuple[tuple[str, object], ...] = (
    ("trades (n)", lambda s, _d: str(s.n)),
    ("avg R (expectancy, the target metric)", lambda s, _d: sr(s.avg_r)),
    ("iid bootstrap 90% CI of avg R", lambda s, _d: _ci(s)),
    ("calendar-month block bootstrap 90% CI of avg R", lambda s, _d: _block_ci(s)),
    (
        "one-sided lower bound of avg R at 1 - alpha/m, iid / block",
        lambda s, _d: f"{_lbs(s)} ({s.lb_confidence:.2%})" if s.n else "n/a",
    ),
    ("adjusted lower bound used by the label (the smaller)", lambda s, _d: sr(s.adj_lb)),
    ("t-stat of avg R", lambda s, _d: sr(s.t_stat, 2)),
    ("total R", lambda s, _d: sr(s.total_r, 2)),
    ("profit factor", lambda s, _d: _pf(s.profit_factor)),
    ("max drawdown % realised (closed trades)", lambda s, _d: pct(s.max_dd_pct)),
    ("max drawdown % mark-to-market (4H closes)", lambda _s, d: pct(d)),
    ("max drawdown R (closed trades)", lambda s, _d: f"{s.max_dd_r:.2f}"),
    (
        "win rate (context only, never a target)",
        lambda s, _d: f"{s.win_rate:.1%}" if s.n else "n/a",
    ),
    ("exits SL / TP / END", lambda s, _d: _exits(s)),
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
    """The C5 drawdown rule, its verdict with both TEST drawdowns and the limit, and the
    TRAIN drawdowns for comparison, for one walk-forward."""
    ok = "passed" if r.dd_ok else "FAILED"
    rule = (
        "Drawdown check (CONTRACT v3 C5; dd_ok = TEST mark-to-market max drawdown <= "
        f"min({min(DD_CAP_PCT, r.max_dd_pct):g}%, 95th percentile of the max drawdown of TRAIN "
        "trade sequences bootstrapped at the TEST length))"
    )
    if not r.ran:
        return f"{rule}: {ok}. {r.dd_reason}"
    return (
        f"{rule}: **{ok}**. {r.dd_reason} For comparison, TRAIN max drawdown: realised "
        f"{pct(r.train_summary.max_dd_pct)}, mark-to-market {pct(r.train_mtm_dd_pct)}."
    )


def label_lines(r: WalkForwardResult) -> list[str]:
    return [f"**Label: {r.label}.** {r.label_reason}", "", dd_rule_line(r)]


# ---------------------------------------------------------------------------- sections 1-3
def _section(i: int) -> list[str]:
    return [f"## {SECTION_TITLES[i - 1]}", ""]


def section_win_rate() -> list[str]:
    return [*_section(1), WIN_RATE_STATEMENT]


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


def cost_lines(cfg: StrategyConfig, trades: Sequence[Trade] = ()) -> list[str]:
    """The cost assumptions of a run (CONTRACT.md v2 A1/A4), for the provenance section."""
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
        f"{cfg.reward_risk:g}x the stop distance. Only a gap through the stop loses more than 1R.",
        f"- {COINBASE_FEE_NOTE}",
    ]
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


def _gap_text(candles: Sequence[Candle], tf_ms: int) -> str:
    gaps = gap_report(candles, tf_ms)
    if not gaps:
        return "0"
    shown = "; ".join(f"{ms_to_iso(a)} -> {ms_to_iso(b)}" for a, b in gaps[:5])
    more = f" (+{len(gaps) - 5} more)" if len(gaps) > 5 else ""
    return f"{len(gaps)}: {shown}{more}"


def candle_table(data: Mapping[str, Sequence[Candle]], split: int, tf_ms: int) -> list[str]:
    """Per-pair candle counts on each side of the split, date range and gaps."""
    lines = [
        "| pair | candles | TRAIN candles (before the split) | TEST candles (from the split) "
        "| first candle (UTC) | last candle (UTC) | gaps |",
        "|---|---:|---:|---:|---|---|---|",
    ]
    for pair, candles in data.items():
        n_train = sum(1 for c in candles if c.ts < split)
        lines.append(
            f"| {pair} | {len(candles)} | {n_train} | {len(candles) - n_train} | "
            f"{ms_to_iso(candles[0].ts)} | {ms_to_iso(candles[-1].ts)} | "
            f"{_gap_text(candles, tf_ms)} |"
        )
    return lines


def section_provenance(ds: Dataset, research: Research, data_end: int) -> list[str]:
    base = research.base
    stats = base.stats
    return [
        *_section(2),
        *ds.provenance,
        f"- Walk-forward split: {ms_to_iso(research.split)} = 70% of the common time range of "
        f"all pairs (TRAIN = signal candles before it, TEST = at or after it, up to "
        f"{ms_to_iso(data_end)}). TEST starts with fresh equity and fresh circuit breakers; "
        "indicators warm up on earlier candles only.",
        f"- Statistics (CONTRACT.md v3 C4/C5): {stats.n_boot} bootstrap resamples (seed "
        f"{stats.seed}) for the iid and the calendar-month block bootstrap; m = {stats.m} "
        f"pre-registered candidates, alpha {stats.alpha:g}. "
        + multiplicity_rule(stats.m, stats.alpha),
        "",
        *candle_table(ds.data, research.split, research.cfg.timeframe_ms),
        "",
        *cost_lines(research.cfg, [*base.train.trades, *base.test.trades]),
    ]


def section_baseline(research: Research, data_end: int) -> list[str]:
    r = research.base
    return [
        *_section(3),
        f"Default mandate config `{r.cfg.variant_id() if r.cfg else BASE}` (variant `{BASE}`). "
        f"TRAIN window {_window(r, True, data_end)}; TEST window {_window(r, False, data_end)}.",
        "",
        *metric_table(columns(r)),
        "",
        *label_lines(r),
    ]


# ---------------------------------------------------------------------------- section 4
def _disc_status(r: WalkForwardResult, research: Research) -> str:
    disc = research.discovery
    if r.variant in disc.test_only:
        return "TEST-ONLY (regime OFF, never selectable)"
    if r.variant == disc.selection.variant:
        return "**SELECTED**"
    if r.variant in disc.selection.eligible:
        return "selectable"
    return "not eligible (too few TRAIN trades)"


def discovery_label(r: WalkForwardResult, research: Research) -> str:
    """The selected variant's label is judged (bare); every other one is context only."""
    if research.discovery.judged(r.variant):
        return r.label
    return f"context: {r.label}, not judged"


def section_discovery(research: Research) -> list[str]:
    disc = research.discovery
    sel = disc.selection
    lines = [
        *_section(4),
        f"K = {sel.k_tried} variants tried: {sel.k_eligible_by_design} legal tightenings "
        "(reward:risk {2, 2.5, 3} x volume multiple {1.5, 2} x RSI window {50-70, 55-70}) plus "
        "1 explicit test-only variant with the R4 regime filter OFF, which is reported but can "
        "never be selected or adopted.",
        "",
        f"Selection rule: highest TRAIN t-stat among selectable variants with >= "
        f"{sel.min_train} TRAIN trades. {sel.reason}",
        "",
        "Order of operations actually run: " + " -> ".join(disc.stage_log) + ".",
        "",
        "Only the selected variant is a pre-registered candidate (one of the m TEST looks of "
        "section 6). Every other row, including the regime-OFF test variant, is shown as "
        "`context: <label>, not judged`: picking one of them because of its TEST numbers "
        "would be selection on TEST.",
        "",
        "| variant | status | TRAIN n | TRAIN avg R | TRAIN 90% CI | TRAIN t | TEST n | "
        "TEST avg R | TEST 90% CI | TEST adjusted LB | TEST max DD % realised / MTM | label |",
        "|---|---|---:|---:|---|---:|---:|---:|---|---:|---|---|",
    ]
    for r in disc.results:
        a, b = r.train_summary, r.test_summary
        lines.append(
            f"| `{r.variant}` | {_disc_status(r, research)} | {a.n} | {sr(a.avg_r)} | {_ci(a)} | "
            f"{sr(a.t_stat, 2)} | {b.n} | {sr(b.avg_r)} | {_ci(b)} | {sr(b.adj_lb)} | "
            f"{pct(b.max_dd_pct)} / {pct(r.test_mtm_dd_pct)} | {discovery_label(r, research)} |"
        )
    lines += ["", f"**{disc.note}**", ""]
    return lines + _selected_lines(research)


def _selected_lines(research: Research) -> list[str]:
    disc = research.discovery
    sel = disc.selected()
    if sel is None:
        return ["No variant was selected, so discovery contributes no candidate to the verdict."]
    return [
        f"The TRAIN column of the selected variant `{sel.variant}` is biased upward by the "
        f"selection itself (it is the best of {disc.selection.k_eligible_by_design}); only its "
        "TEST column is an out-of-sample test.",
        "",
        *label_lines(sel),
    ]


# ---------------------------------------------------------------------------- section 5
def fitted_model(r: WalkForwardResult) -> MLFilter | None:
    """The fitted ``MLFilter`` behind a walk-forward's entry filter, if there is one."""
    ml = getattr(r.entry_filter, "ml", None)
    return ml if isinstance(ml, MLFilter) else None


def _ml_model_lines(r: WalkForwardResult) -> list[str]:
    ml = fitted_model(r)
    if ml is None:
        return ["The fitted filter exposes no model details."]
    lines = [
        ml.describe(),
        "",
        f"- TRAIN candidates enumerated (purged: outcome resolved before the split): "
        f"{r.n_train_candidates}; used for the fit: {ml.train_n} ({ml.train_skipped} skipped "
        f"for missing features); TRAIN candidate win share {ml.train_base_rate:.1%} "
        "(context only).",
        f"- Threshold p* = break-even win probability from the TRAIN avg win "
        f"{sr(ml.avg_win_r, 2)}R and avg loss {sr(ml.avg_loss_r, 2)}R: "
        f"p* = {ml.threshold:.3f} (no threshold search).",
        f"- L2 penalty {ml.l2:g} (fixed, not tuned); intercept {sr(ml.model.intercept)}.",
        f"- Model fingerprint (sha256 of the canonical JSON of features, TRAIN means/stds, "
        f"intercept, coefficients, threshold and l2): `{ml.fingerprint()}`. It is pinned in the "
        "ML adoption record (section 11); `MLFilter.from_json` re-verifies it when the live "
        "bot loads the model file.",
        "",
        "| feature | standardised coefficient, fitted on TRAIN only and applied unchanged to "
        "TEST (log-odds per TRAIN s.d.) |",
        "|---|---:|",
    ]
    lines += [f"| `{name}` | {sr(w)} |" for name, w in ml.explain()]
    return lines


LAYER_DIFF_HEADER = (
    "| layer | window (TRAIN / TEST) | signals vetoed | entries at reduced risk (guard x < 1) "
    "| base trades absent from the layer journal | layer trades absent from the base journal "
    "| trades in both | base trades | layer trades |"
)


def layer_diff_rows(name: str, diffs: Sequence[LayerDiff]) -> list[str]:
    return [
        f"| `{name}` | {d.window} | {d.vetoed} | {d.reduced_risk} | {d.base_only} | "
        f"{d.layer_only} | {d.shared} | {d.base_n} | {d.layer_n} |"
        for d in diffs
    ]


def _layer_diff_table(research: Research) -> list[str]:
    lines = [LAYER_DIFF_HEADER, "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for variant, diffs in research.layer_diffs().items():
        if diffs:
            lines += layer_diff_rows(variant, diffs)
        else:
            lines.append(f"| `{variant}` | TRAIN / TEST | not run | - | - | - | - | - | - |")
    return lines


def _layer_effect_lines(research: Research) -> list[str]:
    b = research.base
    out = []
    for r in (research.ml, research.guard):
        if not r.ran:
            continue
        out.append(
            f"- `{r.variant}` vs `{BASE}`: TRAIN avg R {sr(b.train_summary.avg_r)} -> "
            f"{sr(r.train_summary.avg_r)} (n {b.train_summary.n} -> {r.train_summary.n}"
            f"{', in-sample' if r.train_in_sample else ''}) | TEST avg R "
            f"{sr(b.test_summary.avg_r)} -> {sr(r.test_summary.avg_r)} (n {b.test_summary.n} -> "
            f"{r.test_summary.n})."
        )
    return out


def section_layers(research: Research) -> list[str]:
    r, g, b = research.ml, research.guard, research.base
    g_cfg = g.cfg or DEFAULT_CFG
    lines = [
        *_section(5),
        f"Two pre-registered layers on the baseline rules. `{ML_VARIANT}` adds `{ML_LAYER}`, "
        f"a logistic regression on {len(FEATURES)} features ({', '.join(FEATURES)}), fitted "
        "ONLY on purged TRAIN candidates and applied unchanged to TEST, so its TRAIN numbers "
        f"are IN-SAMPLE. `{GUARD_VARIANT}` switches on `{GUARD_LAYER}` (`expectancy_guard=True`: "
        f"a pair's risk is multiplied by {g_cfg.guard_risk_mult:g} while its last "
        f"{g_cfg.guard_window} closed trades average below 0R); it is a fixed rule "
        "over the journal, nothing is fitted, and it needs the same walk-forward evidence as "
        "any other variant.",
        "",
        LAYER_WORDING,
        "",
        *_layer_diff_table(research),
        "",
        *_layer_effect_lines(research),
        "",
    ]
    if r.ran:
        lines += [*_ml_model_lines(r), ""]
    else:
        lines += [
            f"**The ML layer could not be fitted:** {r.fit_error}",
            "",
            "No backtest was run for it and there is no silent fall-back to the unfiltered "
            "rules (those are the baseline in section 3).",
            "",
        ]
    cols = [
        *columns(b, "base"),
        *(columns(r, ML_VARIANT) if r.ran else []),
        *columns(g, GUARD_VARIANT),
    ]
    order = [c for c in cols if "TEST" not in c[0]] + [c for c in cols if "TEST" in c[0]]
    lines += [*metric_table(order), ""]
    lines += [f"`{ML_VARIANT}`:", "", *label_lines(r), ""]
    lines += [f"`{GUARD_VARIANT}`:", "", *label_lines(g), ""]
    lines.append(
        "LightGBM, other gradient boosting and neural networks are NOT justified at this sample "
        f"size ({r.n_train_candidates} TRAIN candidates, {len(FEATURES)} features): a flexible "
        "model would memorise noise, so only a fixed-penalty logistic regression with a "
        "break-even threshold is allowed."
    )
    return lines


# ---------------------------------------------------------------------------- section 6
def verdict_line(research: Research) -> str:
    """Starts with exactly ``NO_ROBUST`` when no pre-registered candidate is ROBUST; always
    shows each named candidate's TRAIN and TEST expectancy side by side."""
    robust = research.robust()
    if robust:
        parts = []
        for name, r in robust:
            a, s = r.train_summary, r.test_summary
            dd = "drawdown check passed" if r.dd_ok else "drawdown check FAILED"
            parts.append(
                f"{name} (TRAIN avg {sr(a.avg_r)}R, n={a.n} | TEST avg {sr(s.avg_r)}R, n={s.n}, "
                f"{s.lb_confidence:.2%} lower bound iid {sr(s.iid_lb)}R / block "
                f"{sr(s.block_lb)}R; {dd})"
            )
        return "ROBUST: " + "; ".join(parts) + "."
    parts = [
        f"{name} {r.label} (TRAIN {sr(r.train_summary.avg_r)}R n={r.train_summary.n} | TEST "
        f"{sr(r.test_summary.avg_r)}R n={r.test_summary.n})"
        for name, r in research.candidates()
    ]
    return f"{NO_ROBUST} Pre-registered candidates, TRAIN | TEST: " + "; ".join(parts) + "."


def candidate_table(research: Research) -> list[str]:
    lines = [
        "| candidate | TRAIN n | TRAIN avg R | TEST n | TEST avg R | TEST iid / block LB "
        "(1 - alpha/m) | reached the TEST gate | label | TEST MTM max DD vs limit | dd_ok |",
        "|---|---:|---:|---:|---:|---|---|---|---|---|",
    ]
    for name, r in research.candidates():
        a, b = r.train_summary, r.test_summary
        gate = "yes" if r.reached_test_gate else "no"
        dd = f"{pct(r.test_mtm_dd_pct)} vs {pct(r.dd_limit_pct)}" if r.ran else "not run"
        lines.append(
            f"| {name} | {a.n} | {sr(a.avg_r)} | {b.n} | {sr(b.avg_r)} | {_lbs(b)} | {gate} | "
            f"{r.label} | {dd} | {'yes' if r.dd_ok else 'no'} |"
        )
    return lines


def section_verdict(research: Research) -> list[str]:
    names = ", ".join(name for name, _ in research.candidates())
    stats = research.base.stats
    missing = ""
    if research.discovery.selected() is None:
        missing = " (discovery selected nothing, so only three candidates had a look)"
    return [
        *_section(6),
        f"Candidates judged (pre-registered, one TEST look each, m = {stats.m}){missing}: "
        f"{names}. Other grid variants are excluded because choosing among them by TEST "
        f"numbers would be selection on TEST. {multiplicity_rule(stats.m, stats.alpha)} The "
        f"TEST gate is reached when TRAIN avg R > 0 and both windows hold >= {ROBUST_MIN_N} "
        "trades; the drawdown check is reported beside the label.",
        "",
        *candidate_table(research),
        "",
        verdict_line(research),
    ]


# ---------------------------------------------------------------------------- sections 7-9
def _decision_columns(research: Research) -> list[tuple[str, WalkForwardResult, bool]]:
    out: list[tuple[str, WalkForwardResult, bool]] = []
    for _name, r in research.candidates():
        out += [(f"{r.variant} TRAIN", r, True), (f"{r.variant} TEST", r, False)]
    return out


def _bt(r: WalkForwardResult, train: bool) -> BacktestResult:
    return r.train if train else r.test


def section_decisions(research: Research) -> list[str]:
    cols = _decision_columns(research)
    lines = [
        *_section(7),
        "Signal candles denied per rule (the FIRST failing rule is counted, so the R1-R4 rows "
        "include every candle that was simply not a signal). Exits are never gated.",
        "",
        "| rule | " + " | ".join(h for h, _, _ in cols) + " | meaning |",
        "|---|" + "---:|" * len(cols) + "---|",
    ]
    for rule, meaning in RULE_IDS.items():
        if rule.startswith("ADOPT_"):
            continue
        cells = [str(_bt(r, t).decisions.get(rule, 0)) for _, r, t in cols]
        lines.append(f"| `{rule}` | " + " | ".join(cells) + f" | {cell(meaning)} |")
    evaluated = [str(_bt(r, t).signals_evaluated) for _, r, t in cols]
    taken = [str(len(_bt(r, t).trades)) for _, r, t in cols]
    lines.append("| candles evaluated | " + " | ".join(evaluated) + " | |")
    lines.append("| trades taken | " + " | ".join(taken) + " | |")
    return lines


def section_invariants(research: Research) -> list[str]:
    total = research.n_violations()
    lines = [
        *_section(8),
        "`invariants.check_invariants` independently re-derives every mandatory rule "
        "(R1-R9, 2:1 minimum, sizing from the stop, exits on the first touch, no data after "
        "the window) from each trade list and the candles.",
        "",
        "| backtest | TRAIN violations | TEST violations |",
        "|---|---:|---:|",
    ]
    by_variant = {r.variant: r for r in research.all_results()}
    for variant, (tr, te) in research.violations.items():
        ran = by_variant[variant].ran
        a, b = (str(len(tr)), str(len(te))) if ran else ("not run", "not run")
        lines.append(f"| `{variant}` | {a} | {b} |")
    lines.append("")
    n_ran = 2 * sum(1 for r in by_variant.values() if r.ran)
    if total == 0:
        lines.append(f"Result: CLEAN. 0 violations in {n_ran} backtests.")
        return lines
    lines.append(f"Result: **{total} VIOLATIONS** (the pipeline is broken; do not use it):")
    shown = [
        f"- `{v}` {w}: {msg}"
        for v, (tr, te) in research.violations.items()
        for w, msgs in (("TRAIN", tr), ("TEST", te))
        for msg in msgs
    ]
    return lines + shown[:MAX_VIOLATIONS_SHOWN]


def renumbered_test(r: WalkForwardResult) -> list[Trade]:
    """TEST trades with ids offset past the TRAIN ids (unique within a variant)."""
    offset = max((t.trade_id for t in r.train.trades), default=0)
    return [replace(t, trade_id=t.trade_id + offset) for t in r.test.trades]


AUDIT_HEADER = (
    "| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action "
    "| explanation | evidence (journal trade ids) |"
)


def journal_audits(
    r: WalkForwardResult, data_end: int
) -> list[tuple[str, int, float, list[Adaptation]]]:
    """``(window, audit time, equity, adaptations)`` for the TRAIN and the TEST journal.

    Backtest journals record the OPEN of the exit candle, so the audit counts every exit
    from ``exit_ts + cfg.timeframe_ms`` (CONTRACT.md v2 A2), exactly like the backtester's
    circuit breakers.
    """
    cfg = r.cfg or DEFAULT_CFG
    tf = cfg.timeframe_ms
    train_eq, test_eq = r.train.final_equity, r.test.final_equity
    return [
        ("TRAIN", r.split_ts, train_eq, audit(r.train.trades, cfg, r.split_ts, train_eq, tf)),
        ("TEST", data_end, test_eq, audit(renumbered_test(r), cfg, data_end, test_eq, tf)),
    ]


def _audit_rows(window: str, at: int, equity: float, acts: Sequence[Adaptation]) -> list[str]:
    where = f"{window} at {ms_to_iso(at)} (equity {equity:,.2f})"
    if not acts:
        return [f"| {where} | - | - | none | No active adaptations. | - |"]
    rows = []
    for a in acts:
        evidence = ", ".join(f"#{i}" for i in a.evidence_trade_ids) or "-"
        cells = (where, a.rule, a.scope, a.action, a.explanation, evidence)
        rows.append("| " + " | ".join(cell(c) for c in cells) + " |")
    return rows


def section_journal_rules(research: Research, data_end: int, out_dir: Path) -> list[str]:
    tf_h = research.cfg.timeframe_ms / HOUR_MS
    lines = [
        *_section(9),
        "`journal_rules.audit` over each pre-registered candidate's TRAIN journal at the split "
        f"and its TEST journal at {ms_to_iso(data_end)} (the end of TEST), each with that "
        "window's final equity: every adaptation the live bot would be applying at that moment "
        "because of past trades, with the journal rows that caused it. Backtest journals record "
        f"the OPEN of the exit candle, so each exit counts from exit_ts + {tf_h:g}h, when the "
        "exit is certain (`exit_time_uncertainty_ms = cfg.timeframe_ms`, CONTRACT.md v2 A2); a "
        "bench therefore lasts at least 24h of real time. A live journal records real fill "
        "times and uses 0.",
        "",
    ]
    for name, r in research.candidates():
        if not r.ran:
            lines += [f"**{name}:** not run ({r.layer} could not be fitted).", ""]
            continue
        lines += [f"**{name}:**", "", AUDIT_HEADER, "|---|---|---|---|---|---|"]
        for window, at, equity, acts in journal_audits(r, data_end):
            lines += _audit_rows(window, at, equity, acts)
        lines.append("")
    base_test = journal_paths(out_dir, BASE)[1].as_posix()
    lines.append(
        f"Reproduce the baseline TEST row with `python -m research.trendbot.journal_rules "
        f"--journal {base_test} --equity {research.base.test.final_equity:.2f} "
        f"--now {ms_to_iso(data_end)} --backtest-journal`."
    )
    return lines


# ---------------------------------------------------------------------------- section 10
def section_links(
    research: Research,
    out_dir: Path,
    journals: Mapping[str, tuple[Path, Path]],
    reviews: Mapping[str, Mapping[str, Path]],
) -> list[str]:
    lines = [
        *_section(10),
        f"Trade journals (`journal.write_journal`, one CSV per window) for the {len(journals)} "
        "pre-registered candidates that ran, the variants whose TRAIN and TEST columns this "
        "report shows in full. The other discovery variants appear only as one context row "
        "each in section 4, so no journal is kept for them (the same command regenerates "
        "them). TEST trade ids are offset past the TRAIN ids, so ids are unique across a "
        "variant's two journals and its review pack.",
        "",
        "| variant | TRAIN journal | TEST journal |",
        "|---|---|---|",
    ]
    for variant, (a, b) in journals.items():
        lines.append(
            f"| `{variant}` | [{rel(a, out_dir)}]({rel(a, out_dir)}) | "
            f"[{rel(b, out_dir)}]({rel(b, out_dir)}) |"
        )
    lines += ["", "Human review packs (`review_sheet.write_review_pack`, TRAIN and TEST):", ""]
    for variant, paths in reviews.items():
        md, csv_path = rel(paths["md"], out_dir), rel(paths["csv"], out_dir)
        lines.append(f"- `{variant}`: [{md}]({md}) and [{csv_path}]({csv_path})")
    return lines


# ---------------------------------------------------------------------------- section 11
def check_cell(reasons: Sequence[str]) -> str:
    if not reasons:
        return "PASS"
    rules = sorted({r.split("]", 1)[0].lstrip("[") for r in reasons})
    return "BLOCKED by " + ", ".join(rules)


def adoption_table(statuses: Sequence[AdoptionStatus]) -> list[str]:
    lines = [
        "| variant | walk-forward label (TRAIN + TEST) | TRAIN n | TRAIN avg R | TEST n | "
        "TEST avg R | dd_ok | provenance | config sha256 (first 16) | ML model sha256 (first "
        f"16) | `check --stage {WALK_FORWARD}` | `check --stage {HUMAN_REVIEW}` |",
        "|---|---|---:|---:|---:|---:|---|---|---|---|---|---|",
    ]
    for st in statuses:
        r = st.result
        cfg_fp = config_fingerprint(r.cfg or DEFAULT_CFG)[:16]
        model = st.model_fingerprint[:16] if st.model_fingerprint else "none (no ML layer)"
        a, b = r.train_summary, r.test_summary
        lines.append(
            f"| `{st.variant}` | {r.label} | {a.n} | {sr(a.avg_r)} | {b.n} | {sr(b.avg_r)} | "
            f"{'yes' if r.dd_ok else 'no'} | `{st.provenance}` | `{cfg_fp}` | `{model}` | "
            f"{check_cell(st.checks[WALK_FORWARD])} | {check_cell(st.checks[HUMAN_REVIEW])} |"
        )
    return lines


def check_command(st: AdoptionStatus, out_dir: Path, stage: str = HUMAN_REVIEW) -> str:
    """The exact ``adoption check`` command line for a record written by this run."""
    parts = [
        "python -m research.trendbot.adoption check",
        f"--record {(out_dir / rel(st.path, out_dir)).as_posix()}",
        f"--stage {stage}",
    ]
    if st.config_path is not None:
        parts.append(f"--config {(out_dir / rel(st.config_path, out_dir)).as_posix()}")
    if st.model_fingerprint is not None:
        parts.append(f"--model-fingerprint {st.model_fingerprint}")
    return " ".join(parts)


def _status_lines(statuses: Sequence[AdoptionStatus], out_dir: Path) -> list[str]:
    lines: list[str] = []
    for st in statuses:
        x = rel(st.path, out_dir)
        files = [f"record [{x}]({x})"]
        for extra in (st.config_path, st.model_path):
            if extra is not None:
                y = rel(extra, out_dir)
                files.append(f"[{y}]({y})")
        lines.append(f"- `{st.variant}`: {', '.join(files)}.")
        for stage in (WALK_FORWARD, HUMAN_REVIEW):
            reasons = st.checks[stage]
            verdict = "PASS" if not reasons else "BLOCKED: " + " ".join(reasons)
            lines.append(f"  - `--stage {stage}`: {verdict}")
            lines.append(f"    Check: `{check_command(st, out_dir, stage)}`")
    return lines


def _ml_adoption_lines(statuses: Sequence[AdoptionStatus], out_dir: Path) -> list[str]:
    ml_status = next((st for st in statuses if st.variant == ML_VARIANT), None)
    if ml_status is None or ml_status.model_path is None:
        return [
            f"The ML layer `{ML_VARIANT}` could not be fitted, so there is no model to "
            "fingerprint and no adoption record for it."
        ]
    model = rel(ml_status.model_path, out_dir)
    return [
        f"The ML layer `{ML_VARIANT}` is adopted as a (config, model) pair: its record pins "
        "the config fingerprint AND the fitted model's fingerprint "
        f"`{ml_status.model_fingerprint}` (`MLFilter.fingerprint()`). The model itself is "
        f"[{model}]({model}) (`MLFilter.to_json()`), which the live bot loads with "
        "`MLFilter.from_json(text, expected_fingerprint=<the record's model_fingerprint>)`: "
        "loading raises if the file does not hash to that fingerprint. **Refitting the model "
        "(new TRAIN data, a later split, a different l2) changes the fingerprint, so a "
        f"refitted model is a NEW candidate and restarts the adoption path at {BACKTEST}.** "
        "`adoption check` blocks (`ADOPT_fingerprint`) a model whose fingerprint differs from "
        "the recorded one, and any `+ml` record without one.",
    ]


def section_adoption(
    statuses: Sequence[AdoptionStatus] | None, out_dir: Path, synthetic: bool = False
) -> list[str]:
    lines = [
        *_section(11),
        "The only path to real money (enforced by `adoption.py`; no step can be skipped, the "
        "promoted config must match the tested config's sha256 fingerprint, and an ML layer's "
        "model must match the tested model's fingerprint):",
        "",
        "backtest -> walk-forward (ROBUST + drawdown check) -> human review of EVERY trade -> "
        ">= 2 weeks on Binance testnet with zero rule violations -> live.",
        "",
        f"Stages: {' -> '.join(STAGES)}. Stage reached by this run: **{WALK_FORWARD}** "
        f"(recorded). Next: **{HUMAN_REVIEW}**.",
        "",
    ]
    if statuses is None:
        return [*lines, "(being written)"]
    if synthetic:
        lines += [
            "**This run used SYNTHETIC data: its records only demonstrate the mechanism. A "
            "synthetic result can never justify adopting a variant: `adoption check --stage "
            f"{HUMAN_REVIEW}` is BLOCKED by `ADOPT_provenance` for every record below, whatever "
            "its label.**",
            "",
        ]
    lines += [
        "Every record is bound to its evidence (CONTRACT.md v3 C3): `provenance`, `data_files` "
        "(sha256 per candle file) and `events_file`; `backtest.report_sha256` (this REPORT.md, "
        "the record being rewritten after the report so the hash matches); both walk-forward "
        "journals with their sha256, `min_train` / `min_test`, `train_avg_r` and `label_params` "
        "(m, alpha, n_boot, seed, and the C5 drawdown inputs `max_dd_pct`, `train_dd_p95_pct` "
        "and `mtm_max_dd_pct`, which need the candles and are recorded as measured), from "
        "which `adoption check` recomputes the label, n, avg R and dd_ok; and "
        "`human_review.review_path` / `review_sha256` (the blank review pack; after filling in "
        "`reviewer_ok`, re-hash it with `python -m research.trendbot.adoption hash`). The "
        "check status below was computed on the records as written.",
        "",
        *adoption_table(statuses),
        "",
        *_status_lines(statuses, out_dir),
        "",
        *_ml_adoption_lines(statuses, out_dir),
        "",
        "A `config_<variant>.json` file (the `StrategyConfig` overrides versus the defaults, "
        "e.g. costs, discovery parameters or `expectancy_guard`) is written whenever the tested "
        "config is not the default one; the check needs it as `--config`. The regime-OFF "
        "variant is test-only and can never be adopted.",
    ]
    return lines


def section_disclaimer() -> list[str]:
    return [*_section(12), DISCLAIMER]


def render_report(
    ds: Dataset,
    research: Research,
    out_dir: Path,
    journals: Mapping[str, tuple[Path, Path]],
    reviews: Mapping[str, Mapping[str, Path]],
    adoption_statuses: Sequence[AdoptionStatus] | None,
) -> str:
    data_end = max(c[-1].ts + research.cfg.timeframe_ms for c in ds.data.values())
    title = (
        f"synthetic world `{ds.world}` seed {ds.seed}" if ds.kind == "synthetic" else "real data"
    )
    sections = [
        [f"# Walk-forward research report: {title}"],
        section_win_rate(),
        section_provenance(ds, research, data_end),
        section_baseline(research, data_end),
        section_discovery(research),
        section_layers(research),
        section_verdict(research),
        section_decisions(research),
        section_invariants(research),
        section_journal_rules(research, data_end, out_dir),
        section_links(research, out_dir, journals, reviews),
        section_adoption(adoption_statuses, out_dir, ds.kind == "synthetic"),
        section_disclaimer(),
    ]
    return "\n\n".join("\n".join(s) for s in sections) + "\n"


def console_lines(research: Research) -> list[str]:
    """The console summary (also written to run.log): TRAIN beside TEST per candidate."""
    lines = []
    for name, r in research.candidates():
        a, b = r.train_summary, r.test_summary
        dd = (
            f"TEST MTM max DD {pct(r.test_mtm_dd_pct)} vs limit {pct(r.dd_limit_pct)}"
            if r.ran
            else "not run"
        )
        lines.append(
            f"{name}: {r.label} (TRAIN n={a.n}, avg {sr(a.avg_r)}R | TEST n={b.n}, avg "
            f"{sr(b.avg_r)}R, adjusted LB {sr(b.adj_lb)}R; {dd}, dd_ok {r.dd_ok})"
        )
    return lines


# ---------------------------------------------------------------------------- calibration
CANDIDATES: tuple[tuple[str, str], ...] = (
    ("base", "baseline"),
    ("selected", "discovery-selected"),
    ("ml", "ML layer"),
    ("guard", "expectancy guard"),
)
HOUR_TERMS = ("hour_sin", "hour_cos")
WILSON_Z90 = 1.6448536269514722  # two-sided 90 %


def wilson(k: int, n: int, z: float = WILSON_Z90) -> tuple[float, float]:
    """Wilson score interval of a binomial proportion (default 90%)."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def rate(k: int, n: int) -> str:
    """``k/n = p% (90% Wilson CI lo%-hi%)``."""
    if not n:
        return "0/0 (n/a)"
    lo, hi = wilson(k, n)
    return f"{k}/{n} = {k / n:.0%} (90% Wilson CI {lo:.0%}-{hi:.0%})"


def _nums(values: Sequence[object]) -> list[float]:
    return [float(v) for v in values if v != "" and v is not None]  # type: ignore[arg-type]


def mean_text(values: Sequence[object], digits: int = 3) -> str:
    nums = _nums(values)
    return sr(math.fsum(nums) / len(nums), digits) if nums else "n/a"


def mean_ci(values: Sequence[object]) -> tuple[float, float, float] | None:
    """Mean over seeds and its normal-theory 90% interval (SE across seeds)."""
    nums = _nums(values)
    if not nums:
        return None
    mu = statistics.fmean(nums)
    se = statistics.stdev(nums) / math.sqrt(len(nums)) if len(nums) > 1 else 0.0
    return mu, mu - WILSON_Z90 * se, mu + WILSON_Z90 * se


def _mean_n(values: Sequence[object]) -> str:
    nums = _nums(values)
    return f"{statistics.fmean(nums):.1f}" if nums else "n/a"


def count(rows: Sequence[Mapping[str, object]], key: str, *values: object) -> int:
    return sum(1 for r in rows if r[key] in values)


def _truthy(value: object) -> bool:
    return value is True or value == "True" or value == "true"


_ROBUST_MEANING = {
    "null": " = FALSE-POSITIVE rate (no edge exists)",
    "zero_edge": " = FALSE-POSITIVE rate at the H0 boundary (net edge ~0)",
    "decay": " = FALSE-POSITIVE rate (no edge exists in TEST)",
    "planted": " = DETECTION rate (a real edge exists in TRAIN and TEST)",
    "hour_edge": " (a real but hour-specific edge exists)",
}


def _dd_means(rows: Sequence[Mapping[str, object]], key: str) -> str:
    """``mean realised / mean MTM / mean limit`` TEST max drawdown (%) over ``rows``."""
    parts = []
    for field in ("test_realised_dd_pct", "test_mtm_dd_pct", "dd_limit_pct"):
        nums = _nums([r[f"{key}_{field}"] for r in rows])
        parts.append(f"{statistics.fmean(nums):.2f}" if nums else "n/a")
    return " / ".join(parts)


def candidate_rate_table(rows: Sequence[Mapping[str, object]]) -> list[str]:
    """Per candidate: TEST-gate reach, ROBUST rates and TRAIN | TEST means (Wilson 90%)."""
    n = len(rows)
    lines = [
        "| candidate | seeds that reached the TEST gate (TRAIN avg R > 0, TRAIN n >= 30, TEST "
        "n >= 30) | ROBUST, all seeds | ROBUST given the TEST gate was reached | mean TRAIN "
        "avg R | mean TEST avg R | mean TRAIN n | mean TEST n | mean TEST max DD % realised / "
        "MTM / limit | dd_ok (TEST MTM within limit) |",
        "|---|---|---|---|---:|---:|---:|---:|---|---|",
    ]
    for key, name in CANDIDATES:
        reached = [r for r in rows if _truthy(r[f"{key}_reached_gate"])]
        robust = count(rows, f"{key}_label", "ROBUST")
        robust_reached = count(reached, f"{key}_label", "ROBUST")
        ran = [r for r in rows if r[f"{key}_test_n"] != ""]
        dd_ok = sum(1 for r in ran if _truthy(r[f"{key}_dd_ok"]))
        lines.append(
            f"| {name} | {rate(len(reached), n)} | {rate(robust, n)} | "
            f"{rate(robust_reached, len(reached))} | "
            f"{mean_text([r[f'{key}_train_avg_r'] for r in rows])} | "
            f"{mean_text([r[f'{key}_test_avg_r'] for r in rows])} | "
            f"{_mean_n([r[f'{key}_train_n'] for r in rows])} | "
            f"{_mean_n([r[f'{key}_test_n'] for r in rows])} | {_dd_means(ran, key)} | "
            f"{rate(dd_ok, len(ran))} |"
        )
    return lines


def _ml_hour_line(rows: Sequence[Mapping[str, object]]) -> str | None:
    fitted = [r for r in rows if r["ml_top_feature"] != ""]
    if not fitted:
        return None
    top_hour = count(fitted, "ml_top_feature", *HOUR_TERMS)
    mags = [
        math.hypot(float(r["ml_hour_sin"]), float(r["ml_hour_cos"]))  # type: ignore[arg-type]
        for r in fitted
    ]
    return (
        f"- ML coefficients (standardised, fitted on TRAIN only): the largest-magnitude "
        f"coefficient was an hour term (hour_sin/hour_cos) in {top_hour}/{len(fitted)} fitted "
        f"seeds; mean hour-term magnitude sqrt(hour_sin^2 + hour_cos^2) = "
        f"{statistics.fmean(mags):.3f} log-odds per TRAIN s.d."
    )


def _layer_effect(rows: Sequence[Mapping[str, object]], key: str, name: str) -> list[str]:
    ran = [r for r in rows if r[f"{key}_test_n"] != ""]
    n = len(rows)
    if not ran:
        return [f"- {name}: not run in any of the {n} seeds."]
    helped = sum(
        1
        for r in ran
        if float(r[f"{key}_test_avg_r"]) > float(r["base_test_avg_r"])  # type: ignore[arg-type]
    )

    def m(field: str) -> str:
        return _mean_n([r[field] for r in ran])

    return [
        f"- {name} ran in {len(ran)}/{n} seeds. Mean avg R, base -> layer: TRAIN "
        f"{mean_text([r['base_train_avg_r'] for r in ran])} -> "
        f"{mean_text([r[f'{key}_train_avg_r'] for r in ran])}"
        f"{' (in-sample)' if key == 'ml' else ''} | TEST "
        f"{mean_text([r['base_test_avg_r'] for r in ran])} -> "
        f"{mean_text([r[f'{key}_test_avg_r'] for r in ran])}; its TEST avg R beat the base in "
        f"{rate(helped, len(ran))} of those seeds.",
        f"  Per seed, mean counts TRAIN | TEST: signals vetoed {m(f'{key}_train_vetoed')} | "
        f"{m(f'{key}_test_vetoed')}; entries at reduced risk {m(f'{key}_train_reduced')} | "
        f"{m(f'{key}_test_reduced')}; base trades absent from the layer journal "
        f"{m(f'{key}_train_base_only')} | {m(f'{key}_test_base_only')}; layer trades absent "
        f"from the base journal {m(f'{key}_train_layer_only')} | {m(f'{key}_test_layer_only')}.",
    ]


def calibration_console_lines(rows: Sequence[Mapping[str, object]]) -> list[str]:
    """One console line per candidate: mean TRAIN | TEST avg R and n, gate reach, ROBUST."""
    n = len(rows)
    out = []
    for key, name in CANDIDATES:
        reached = sum(1 for r in rows if _truthy(r[f"{key}_reached_gate"]))
        out.append(
            f"{name}: mean TRAIN avg R {mean_text([r[f'{key}_train_avg_r'] for r in rows])} "
            f"(n {_mean_n([r[f'{key}_train_n'] for r in rows])}) | TEST avg R "
            f"{mean_text([r[f'{key}_test_avg_r'] for r in rows])} (n "
            f"{_mean_n([r[f'{key}_test_n'] for r in rows])}); reached the TEST gate "
            f"{rate(reached, n)}; ROBUST {rate(count(rows, f'{key}_label', 'ROBUST'), n)}"
        )
    return out


def world_findings(world: str, rows: Sequence[Mapping[str, object]]) -> list[str]:
    n = len(rows)
    robust = sum(1 for r in rows if _truthy(r["verdict_robust"]))
    meaning = _ROBUST_MEANING.get(world, "")
    lines = [
        f"- Verdict ROBUST (any of the 4 pre-registered candidates, the family-wise rate)"
        f"{meaning}: {rate(robust, n)}.",
    ]
    for key, name in CANDIDATES:
        lines.append(
            f"- {name} labelled ROBUST{meaning}: {rate(count(rows, f'{key}_label', 'ROBUST'), n)}."
        )
    if world == "decay":
        t_only = count(rows, "base_label", "TRAIN-ONLY")
        untested = count(rows, "base_label", "UNTESTED")
        lines += [
            f"- Baseline labelled TRAIN-ONLY: {rate(t_only, n)}; UNTESTED: {rate(untested, n)}.",
            f"- Baseline labelled TRAIN-ONLY or UNTESTED (the decay is caught or at least not "
            f"passed): {rate(t_only + untested, n)}.",
        ]
    not_fitted = sum(1 for r in rows if r["ml_fit_error"])
    if not_fitted:
        lines.append(
            f"- ML layer NOT fitted (InsufficientData, labelled UNTESTED, no fall-back) in "
            f"{not_fitted}/{n} seeds."
        )
    lines += _layer_effect(rows, "ml", "ML layer (`base+ml`)")
    lines += _layer_effect(rows, "guard", "Expectancy guard (`base+guard`)")
    hour = _ml_hour_line(rows)
    if hour is not None:
        lines.append(hour)
    viol = sum(int(r["violations"]) for r in rows)  # type: ignore[call-overload]
    lines.append(f"- Invariant violations over all backtests of all seeds: {viol}.")
    return lines


def _num(value: object, digits: int = 3) -> str:
    return sr(float(value), digits) if value not in ("", None) else "n/a"  # type: ignore[arg-type]


def _n(value: object) -> str:
    return "n/a" if value in ("", None) else str(value)


PER_SEED_HEADER = (
    "| seed | base TRAIN n | base TRAIN avg R | base TEST n | base TEST avg R | base TEST adj LB "
    "| base label | selected | selected TRAIN n | selected TRAIN avg R | selected TEST n | "
    "selected TEST avg R | selected label | ML TRAIN n | ML TRAIN avg R (in-sample) | ML TEST n "
    "| ML TEST avg R | ML label | guard TRAIN n | guard TRAIN avg R | guard TEST n | guard TEST "
    "avg R | guard label | verdict ROBUST | violations |"
)


def per_seed_lines(rows: Sequence[Mapping[str, object]]) -> list[str]:
    lines = [
        "## Per seed (TRAIN and TEST side by side)",
        "",
        PER_SEED_HEADER,
        "|---:|---:|---:|---:|---:|---:|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---|"
        "---:|---:|---:|---:|---|---|---:|",
    ]
    for r in rows:
        cells = [
            str(r["seed"]),
            _n(r["base_train_n"]),
            _num(r["base_train_avg_r"]),
            _n(r["base_test_n"]),
            _num(r["base_test_avg_r"]),
            _num(r["base_test_adj_lb"]),
            str(r["base_label"]),
            f"`{r['selected'] or '-'}`",
            _n(r["selected_train_n"]),
            _num(r["selected_train_avg_r"]),
            _n(r["selected_test_n"]),
            _num(r["selected_test_avg_r"]),
            str(r["selected_label"]),
            _n(r["ml_train_n"]),
            _num(r["ml_train_avg_r"]),
            _n(r["ml_test_n"]),
            _num(r["ml_test_avg_r"]),
            str(r["ml_label"]),
            _n(r["guard_train_n"]),
            _num(r["guard_train_avg_r"]),
            _n(r["guard_test_n"]),
            _num(r["guard_test_avg_r"]),
            str(r["guard_label"]),
            "yes" if _truthy(r["verdict_robust"]) else "no",
            str(r["violations"]),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def label_frequency_table(rows: Sequence[Mapping[str, object]]) -> list[str]:
    n = len(rows)
    lines = [
        "| walk-forward label (TRAIN + TEST) | "
        + " | ".join(name for _, name in CANDIDATES)
        + " |",
        "|---|" + "---:|" * len(CANDIDATES),
    ]
    for lab in (*LABELS, "NONE"):
        counts = [count(rows, f"{key}_label", lab) for key, _ in CANDIDATES]
        if lab == "NONE" and not any(counts):
            continue
        lines.append(f"| {lab} | " + " | ".join(f"{c}/{n}" for c in counts) + " |")
    return lines


def _calibration_intro(world: str, years: float, n: int, strength: object) -> list[str]:
    truth, prediction = WORLD_TRUTH[world]
    strength_text = "" if strength in ("", None) else f", effect strength {strength} sigma"
    if strength_text:
        truth += f" (This calibration overrides the strength: {strength} sigma per candle.)"
    return [
        f"# Calibration: synthetic world `{world}`{strength_text}, seeds 1-{n}",
        "",
        WIN_RATE_STATEMENT,
        "",
        f"Each seed is a full run of the pipeline ({years:g} years of 4H candles, 70/30 "
        "walk-forward): baseline, discovery (K = 13 variants, one selected on TRAIN), the ML "
        "layer and the expectancy guard, i.e. m = 4 pre-registered TEST looks per seed. "
        "**Synthetic results are verification of the methodology, not evidence about real "
        "markets.** Each label below combines that candidate's TRAIN and TEST windows "
        f"(`metrics.label`). {multiplicity_rule(4, 0.05)}",
        "",
        f"- Ground truth: {truth}",
        f"- What the ground truth predicts: {prediction}",
    ]


def render_calibration(
    world: str,
    years: float,
    rows: Sequence[Mapping[str, object]],
    cfg: StrategyConfig | None = None,
) -> str:
    n = len(rows)
    strength = rows[0].get("effect_strength", "") if rows else ""
    lines = [
        *_calibration_intro(world, years, n, strength),
        *cost_lines(cfg or DEFAULT_CFG),
        "",
        "## Label frequencies",
        "",
        *label_frequency_table(rows),
        "",
        "## Ground truth vs labels (rates with 90% Wilson intervals)",
        "",
        *world_findings(world, rows),
        "",
        DD_RULE,
        "",
        *candidate_rate_table(rows),
        "",
        *per_seed_lines(rows),
        "",
        *ADOPTION_PATH_LINES,
        "",
        "## Risk disclaimer",
        "",
        DISCLAIMER,
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------- power curve
def power_points(
    by_strength: Mapping[float, Sequence[Mapping[str, object]]], key: str = "base"
) -> list[tuple[float, float, float, int, int]]:
    """``(strength, mean TEST avg R over seeds, detection rate, k ROBUST, n seeds)``."""
    out = []
    for strength in sorted(by_strength):
        rows = by_strength[strength]
        stats = mean_ci([r[f"{key}_test_avg_r"] for r in rows])
        k = count(rows, f"{key}_label", "ROBUST")
        out.append((strength, stats[0] if stats else math.nan, k / len(rows), k, len(rows)))
    return out


def mde(points: Sequence[tuple[float, float, float, int, int]], power: float) -> str:
    """Minimum detectable effect at ``power``, read along INCREASING strength: the mean TEST
    expectancy (and strength) where the detection rate first reaches ``power``, linearly
    interpolated between the two tested strengths around the crossing."""
    pts = sorted(points)
    if not pts:
        return "not computable (no strengths)"
    first = pts[0]
    if first[2] >= power:
        return (
            f"at or below the smallest tested effect (strength {first[0]:g} sigma, mean TEST "
            f"expectancy {sr(first[1])}R, detection {first[2]:.0%})"
        )
    for a, b in pairwise(pts):
        if a[2] < power <= b[2]:
            f = (power - a[2]) / (b[2] - a[2])
            e, x = a[1] + f * (b[1] - a[1]), a[0] + f * (b[0] - a[0])
            return (
                f"~{sr(e)}R mean TEST expectancy per trade (strength ~{x:.2f} sigma, "
                f"interpolated between {a[0]:g} and {b[0]:g} sigma)"
            )
    best = max(pts, key=lambda p: (p[2], -p[0]))
    return (
        f"not reached at any tested strength (highest detection {best[2]:.0%} at "
        f"{best[0]:g} sigma, mean TEST expectancy {sr(best[1])}R)"
    )


def power_table(by_strength: Mapping[float, Sequence[Mapping[str, object]]]) -> list[str]:
    lines = [
        "| effect strength (sigma per candle) | seeds | true mean TEST expectancy, base (mean "
        "over seeds [90% CI]) | pooled TEST R per trade, base | mean TRAIN avg R, base | mean "
        "TEST n, base | mean TEST max DD % realised / MTM / limit, base | base dd_ok | base "
        "ROBUST = detection rate | base reached the TEST gate | ROBUST given the gate | any of the "
        "4 candidates ROBUST |",
        "|---:|---:|---|---:|---:|---:|---|---|---|---|---|---|",
    ]
    for strength in sorted(by_strength):
        rows = by_strength[strength]
        n = len(rows)
        stats = mean_ci([r["base_test_avg_r"] for r in rows])
        tr = f"{sr(stats[0])} [{sr(stats[1])}, {sr(stats[2])}]" if stats else "n/a"
        tot_n = sum(int(r["base_test_n"]) for r in rows if r["base_test_n"] != "")  # type: ignore[call-overload]
        tot_r = math.fsum(float(r["base_test_total_r"]) for r in rows)  # type: ignore[arg-type]
        pooled = sr(tot_r / tot_n) if tot_n else "n/a"
        reached = [r for r in rows if _truthy(r["base_reached_gate"])]
        k = count(rows, "base_label", "ROBUST")
        k_reached = count(reached, "base_label", "ROBUST")
        any_robust = sum(1 for r in rows if _truthy(r["verdict_robust"]))
        lines.append(
            f"| {strength:g} | {n} | {tr} | {pooled} | "
            f"{mean_text([r['base_train_avg_r'] for r in rows])} | "
            f"{_mean_n([r['base_test_n'] for r in rows])} | {_dd_means(rows, 'base')} | "
            f"{rate(sum(1 for r in rows if _truthy(r['base_dd_ok'])), n)} | {rate(k, n)} | "
            f"{rate(len(reached), n)} | {rate(k_reached, len(reached))} | {rate(any_robust, n)} |"
        )
    return lines


def render_power(
    world: str,
    years: float,
    by_strength: Mapping[float, Sequence[Mapping[str, object]]],
    cfg: StrategyConfig | None = None,
) -> str:
    points = power_points(by_strength)
    seeds = sorted({len(v) for v in by_strength.values()})
    mde50, mde80 = mde(points, 0.5), mde(points, 0.8)
    lines = [
        f"# Power curve: synthetic world `{world}` at {len(by_strength)} effect strengths",
        "",
        WIN_RATE_STATEMENT,
        "",
        f"Each cell is {', '.join(str(s) for s in seeds)} seeds of the full pipeline ({years:g} "
        "years of 4H candles, 70/30 walk-forward, the 4 pre-registered candidates) with the "
        f"planted drift set by `effect_strength` (sigma units per candle). "
        f"{multiplicity_rule(4, 0.05)} **Synthetic results are verification of the methodology, "
        "not evidence about real markets.**",
        "",
        "The detection rate is the share of seeds whose BASELINE is labelled ROBUST, plotted "
        "against the true mean TEST expectancy of the baseline at that strength (the mean over "
        "seeds of each run's TEST avg R, i.e. what one run expects to see). Rates carry 90% "
        "Wilson intervals.",
        "",
        *cost_lines(cfg or DEFAULT_CFG),
        "",
        DD_RULE,
        "",
        *power_table(by_strength),
        "",
        "## Minimum detectable effect (baseline, this sample size)",
        "",
        f"- 50% power: {mde50}.",
        f"- 80% power: {mde80}.",
        "- Read along increasing strength with linear interpolation between tested strengths; "
        "each detection rate is itself an estimate (see its Wilson interval), so the MDE is "
        "approximate. A stronger drift also pushes RSI above 70 more often, so R2 admits fewer "
        "signals and the TEST trade count (mean TEST n column) can fall below the 30 that "
        "ROBUST needs: detection can drop again at high strengths, and the MDE holds only for "
        "TEST samples of about the size shown.",
        "- **An edge below the MDE will be labelled UNTESTED on real data however real it is:** "
        "with ~6 years of 4H data and a 30% TEST window the baseline takes only about the TEST "
        "trade counts shown above, and the multiplicity-corrected, block-bootstrapped lower "
        "bound cannot separate a smaller edge from zero. UNTESTED means 'not shown', never "
        "'shown to be absent'.",
        "",
        *ADOPTION_PATH_LINES,
        "",
        "## Risk disclaimer",
        "",
        DISCLAIMER,
    ]
    return "\n".join(lines) + "\n"
