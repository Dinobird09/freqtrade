"""One command: 70/30 walk-forward research report, trade journals and human review packs.

Usage (from the repo root)::

    # real candles (downloaded on a networked machine with fetch_data + ccxt)
    python -m research.trendbot.run_research --data-dir research/data \
        [--events events.csv] [--pairs BTC/USDT ETH/USDT BNB/USDT] --out-dir research/results/real \
        [--fee-rate 0.001] [--slippage-pct 0.05] [--exchange-id binance]
    # synthetic world with KNOWN ground truth (verification of the methodology only)
    python -m research.trendbot.run_research --synthetic planted --seed 1 [--years 6] \
        --out-dir research/results/synthetic_planted_s1
    # calibration: label frequencies over seeds 1..N of a synthetic world
    python -m research.trendbot.run_research --synthetic null --calibrate-seeds 20 \
        --out-dir research/results/calibration_null [--workers 4]

Costs (CONTRACT.md v2 A1/A4): ``--fee-rate`` (fraction of notional per side, default 0.001 =
Binance spot taker), ``--slippage-pct`` (percent on market fills, default 0.05) and
``--exchange-id`` (default ``binance``; ``EXCHANGE:<id>`` news events apply to every pair) are
validated by ``StrategyConfig`` (which refuses unrealistically low costs) and printed in the
report. Coinbase Advanced Trade taker fees at low volume tiers are several times Binance's:
pass the real tier.

What one run does (every step on the same chronological 70/30 split):

1. baseline: the default (mandate) config, TRAIN then TEST;
2. discovery (``strategy_discovery.discover``): 12 legal tightenings plus the ONE test-only
   regime-OFF variant, one variant selected on TRAIN t-stat before any TEST backtest ran;
3. ML layer (``ml_filter``): a logistic filter on the baseline rules, fitted on purged TRAIN
   candidates only and applied unchanged to TEST (``UNTESTED`` if it cannot be fitted);
4. ``invariants.check_invariants`` on every backtest; ``journal_rules.audit`` of the TRAIN
   journal at the split and of the TEST journal at the end of TEST (backtest convention:
   ``exit_time_uncertainty_ms = cfg.timeframe_ms``); TRAIN and TEST journals plus a human
   review pack for each of the three pre-registered candidates; an adoption record per
   adoptable candidate (stage reached: WALK_FORWARD), the ML one carrying the fitted model's
   ``MLFilter.fingerprint()``, each with the config overrides JSON its check needs.

The verdict only considers the three PRE-REGISTERED candidates (baseline, TRAIN-selected
variant, ML layer). Picking any other grid variant because of its TEST numbers would be
selection on TEST, so their TEST labels are context only.

``REPORT.md`` sections, in order: (1) no win rate is promised or targeted, (2) data
provenance and costs, (3) baseline TRAIN | TEST, (4) discovery TRAIN | TEST, (5) ML filter,
(6) verdict, (7) decision counts per rule, (8) invariant audit, (9) journal rules at the end of
TRAIN and of TEST, (10) journals and review packs, (11) adoption path, (12) risk disclaimer.
Every table header names its TRAIN and TEST columns (or a TRAIN/TEST window column), and win
rate only ever appears labelled "context only".

Exit code 0, or 1 if any backtest violated an invariant (the report must be clean), 2 on a
usage or data error.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import statistics
import sys
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, fields, replace
from pathlib import Path

from .adoption import (
    BACKTEST,
    HUMAN_REVIEW,
    STAGES,
    WALK_FORWARD,
    BacktestStage,
    WalkForwardStage,
    check_promotion,
    config_fingerprint,
    empty_record,
    load_config,
    save_record,
)
from .backtester import BacktestResult
from .config import RULE_IDS, ConfigError, StrategyConfig
from .data import gap_report, load_dataset
from .invariants import check_invariants
from .journal import iso_to_ms, ms_to_iso, write_journal
from .journal_rules import Adaptation, audit
from .metrics import LABELS, Summary
from .ml_filter import FEATURES, MLFilter, make_factory
from .models import HOUR_MS, Candle, NewsEvent, Trade
from .news import load_events
from .review_sheet import write_review_pack
from .strategy_discovery import DiscoveryResult, discover
from .synthetic import WORLDS, generate_world
from .walkforward import (
    DEFAULT_MAX_DD_PCT,
    MIN_TEST,
    MIN_TRAIN,
    TRAIN_FRAC,
    WalkForwardResult,
    split_ts,
    walk_forward,
)


PAIRS = ("BTC/USDT", "ETH/USDT", "BNB/USDT")
REPORT_NAME = "REPORT.md"
CALIBRATION_NAME = "CALIBRATION.md"
CALIBRATION_CSV = "calibration_runs.csv"
NO_ROBUST = "No robust result found."
BASE = "base"
ML_VARIANT = "base+ml"
MAX_VIOLATIONS_SHOWN = 20

SECTION_TITLES = (
    "1. No win rate is promised or targeted",
    "2. Data provenance",
    "3. Baseline: TRAIN vs TEST",
    "4. Strategy discovery: TRAIN vs TEST",
    "5. ML filter layer: TRAIN (in-sample) vs TEST",
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

# ---------------------------------------------------------------------------- costs
DEFAULT_CFG = StrategyConfig()
# CLI sanity ceilings on top of StrategyConfig's floors: a larger value is almost surely a
# percent typed where a fraction was expected (--fee-rate 0.6 meaning 0.6%).
MAX_FEE_RATE = 0.02
MAX_SLIPPAGE_PCT = 5.0
_EXCHANGE_ID_RE = re.compile(r"[a-z][a-z0-9_]*")
COST_FLAGS = ("fee_rate", "slippage_pct", "exchange_id")  # the StrategyConfig fields they set

COINBASE_FEE_NOTE = (
    "Coinbase Advanced Trade taker fees at low volume tiers are several times Binance's, so "
    "a Coinbase run must pass the account's real tier with `--fee-rate` (a fraction of "
    "notional per side: 0.006 = 0.6%) and `--exchange-id coinbase`; the default costs would "
    "understate them."
)


# ---------------------------------------------------------------------------- datasets
@dataclass
class Dataset:
    data: dict[str, list[Candle]]
    events: list[NewsEvent]
    kind: str  # "synthetic" | "files"
    provenance: list[str]  # markdown lines for section 2
    world: str | None = None
    seed: int | None = None


def _event_counts(events: Sequence[NewsEvent]) -> str:
    counts = Counter(f"{e.impact} {e.kind}" for e in events)
    return ", ".join(f"{k} {counts[k]}" for k in sorted(counts)) or "none"


def load_synthetic(
    world: str, seed: int, years: float = 6.0, pairs: Sequence[str] = PAIRS
) -> Dataset:
    """A synthetic world plus provenance lines that state its ground truth."""
    w = generate_world(world, seed, years, pairs, split_frac=TRAIN_FRAC)
    truth, prediction = WORLD_TRUTH[world]
    n = len(next(iter(w.candles.values())))
    lines = [
        f"- Source: **SYNTHETIC** world `{world}`, seed {seed}, {years:g} years of 4H candles "
        f"({n} per pair) from {ms_to_iso(next(iter(w.candles.values()))[0].ts)}, pairs "
        f"{', '.join(pairs)} (`synthetic.generate_world`).",
        f"- Ground truth: {truth}",
        f"- What the ground truth predicts: {prediction}",
        "- Qualifying triggers planted per pair (before / after the 70% split): "
        + ", ".join(
            f"{p} {sum(1 for t in tr.triggers if t < w.split_idx)} / "
            f"{sum(1 for t in tr.triggers if t >= w.split_idx)}"
            for p, tr in w.truth.items()
        )
        + ".",
        "- **Synthetic results are verification of the methodology, not evidence about real "
        "markets.** No real BTC/ETH/BNB data was used in this run.",
        f"- News calendar loaded: yes (synthetic, {len(w.events)} events: "
        f"{_event_counts(w.events)}). The events have no price impact; they exercise R5 only.",
    ]
    return Dataset(w.candles, list(w.events), "synthetic", lines, world, seed)


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


def load_files(
    data_dir: str | Path,
    pairs: Sequence[str] = PAIRS,
    events_path: str | Path | None = None,
    timeframe: str = "4h",
) -> Dataset:
    """Real candle files (``data.load_dataset``) plus the optional news calendar CSV."""
    data = load_dataset(data_dir, pairs, timeframe)
    events = load_events(events_path) if events_path else []
    lines = [
        f"- Source: candle CSV files in `{data_dir}` ({timeframe}), loaded with "
        "`data.load_dataset` (strict validation: sorted, no duplicates, sane OHLC). "
        "Per-pair coverage and gaps are in the table below.",
    ]
    if events:
        lines.append(
            f"- News calendar loaded: yes, `{events_path}` ({len(events)} events: "
            f"{_event_counts(events)})."
        )
    else:
        lines.append(
            "- News calendar loaded: **NO**. R5 (news blackout) could NOT be exercised "
            "historically: the backtest may have entered around high-impact news that the "
            "live bot would skip. Pass `--events events.csv` to test it."
        )
    return Dataset(data, events, "files", lines)


# ---------------------------------------------------------------------------- pipeline
@dataclass
class Research:
    cfg: StrategyConfig
    split: int
    base: WalkForwardResult
    discovery: DiscoveryResult
    ml: WalkForwardResult
    violations: dict[str, tuple[list[str], list[str]]]  # variant -> (TRAIN, TEST)

    def all_results(self) -> list[WalkForwardResult]:
        return [self.base, *self.discovery.results, self.ml]

    def candidates(self) -> list[tuple[str, WalkForwardResult]]:
        """The PRE-REGISTERED verdict candidates: baseline, TRAIN-selected, ML layer."""
        out = [(f"baseline `{BASE}`", self.base)]
        sel = self.discovery.selected()
        if sel is not None:
            out.append((f"discovery-selected `{sel.variant}`", sel))
        out.append((f"ML layer `{ML_VARIANT}`", self.ml))
        return out

    def robust(self) -> list[tuple[str, WalkForwardResult]]:
        return [(name, r) for name, r in self.candidates() if r.label == "ROBUST"]

    def n_violations(self) -> int:
        return sum(len(a) + len(b) for a, b in self.violations.values())


def _audit(
    r: WalkForwardResult, data: Mapping[str, Sequence[Candle]], events: Sequence[NewsEvent]
) -> tuple[list[str], list[str]]:
    if not r.ran:
        return [], []
    return (
        check_invariants(r.train, data, events, r.cfg),
        check_invariants(r.test, data, events, r.cfg),
    )


def run_pipeline(
    data: Mapping[str, Sequence[Candle]],
    events: Sequence[NewsEvent] = (),
    cfg: StrategyConfig | None = None,
    min_train: int = MIN_TRAIN,
    min_test: int = MIN_TEST,
    max_dd_pct: float = DEFAULT_MAX_DD_PCT,
) -> Research:
    """Baseline, discovery and ML walk-forwards on one shared split, plus invariant audits."""
    cfg = cfg or StrategyConfig()
    evs = list(events)
    split = split_ts(data, TRAIN_FRAC, cfg.timeframe_ms)
    common = {"split": split, "min_train": min_train, "min_test": min_test}
    base = walk_forward(data, cfg, evs, TRAIN_FRAC, None, BASE, max_dd_pct, **common)
    disc = discover(
        data, cfg, evs, TRAIN_FRAC, min_train, max_dd_pct=max_dd_pct, min_test=min_test, split=split
    )
    ml = walk_forward(data, cfg, evs, TRAIN_FRAC, make_factory(), ML_VARIANT, max_dd_pct, **common)
    research = Research(cfg, split, base, disc, ml, {})
    for r in research.all_results():
        research.violations[r.variant] = _audit(r, data, evs)
    return research


def verdict_line(research: Research) -> str:
    """Exactly ``NO_ROBUST`` when no pre-registered candidate is ROBUST."""
    robust = research.robust()
    if not robust:
        return NO_ROBUST
    parts = []
    for name, r in robust:
        s = r.test_summary
        dd = "drawdown check passed" if r.dd_ok else "drawdown check FAILED"
        parts.append(
            f"{name} (TEST avg {s.avg_r:+.3f}R, 90% CI [{s.ci90_low:+.3f}, {s.ci90_high:+.3f}]R, "
            f"n={s.n}; {dd})"
        )
    return "ROBUST: " + "; ".join(parts) + "."


# ---------------------------------------------------------------------------- formatting
def _sr(x: float | None, digits: int = 3) -> str:
    if x is None or not math.isfinite(x):
        return "n/a"
    text = f"{x:+.{digits}f}"
    return "+" + text[1:] if float(text) == 0.0 else text


def _ci(s: Summary) -> str:
    return f"[{_sr(s.ci90_low)}, {_sr(s.ci90_high)}]" if s.n else "n/a"


def _pf(x: float | None) -> str:
    return "n/a (no losses)" if x is None else f"{x:.2f}"


def _exits(s: Summary) -> str:
    c = s.exit_counts
    return f"{c.get('SL', 0)} / {c.get('TP', 0)} / {c.get('END', 0)}"


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def _window(r: WalkForwardResult, train: bool, end_ts: int) -> str:
    if train:
        return f"start .. {ms_to_iso(r.split_ts)}"
    return f"{ms_to_iso(r.split_ts)} .. {ms_to_iso(end_ts)}"


_METRIC_ROWS: tuple[tuple[str, object], ...] = (
    ("trades (n)", lambda s: str(s.n)),
    ("avg R (expectancy, the target metric)", lambda s: _sr(s.avg_r)),
    ("90% bootstrap CI of avg R", _ci),
    ("t-stat of avg R", lambda s: _sr(s.t_stat, 2)),
    ("total R", lambda s: _sr(s.total_r, 2)),
    ("profit factor", lambda s: _pf(s.profit_factor)),
    ("max drawdown %", lambda s: f"{s.max_dd_pct:.2f}%"),
    ("max drawdown R", lambda s: f"{s.max_dd_r:.2f}"),
    ("win rate (context only, never a target)", lambda s: f"{s.win_rate:.1%}" if s.n else "n/a"),
    ("exits SL / TP / END", _exits),
    ("avg hold (h)", lambda s: f"{s.avg_hold_h:.1f}"),
)


def metric_table(columns: Sequence[tuple[str, Summary]]) -> list[str]:
    """Rows = metrics, one column per ``(header, Summary)``."""
    lines = [
        "| metric | " + " | ".join(h for h, _ in columns) + " |",
        "|---|" + "---:|" * len(columns),
    ]
    for name, fmt in _METRIC_ROWS:
        cells = [fmt(s) for _, s in columns]  # type: ignore[operator]
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    return lines


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


def _default_fee(cfg: StrategyConfig) -> bool:
    return cfg.fee_rate == DEFAULT_CFG.fee_rate


def section_provenance(ds: Dataset, research: Research, data_end: int) -> list[str]:
    base = research.base
    lines = [*_section(2), *ds.provenance]
    lines += [
        f"- Walk-forward split: {ms_to_iso(research.split)} = 70% of the common time range of "
        f"all pairs (TRAIN = signal candles before it, TEST = at or after it, up to "
        f"{ms_to_iso(data_end)}). TEST starts with fresh equity and fresh circuit breakers; "
        "indicators warm up on earlier candles only.",
        "",
        *candle_table(ds.data, research.split, research.cfg.timeframe_ms),
        "",
        *cost_lines(research.cfg, [*base.train.trades, *base.test.trades]),
    ]
    return lines


def _label_lines(r: WalkForwardResult) -> list[str]:
    ok = "passed" if r.dd_ok else "FAILED"
    return [
        f"**Label: {r.label}.** {r.label_reason}",
        "",
        f"Drawdown check (TEST max drawdown <= {r.max_dd_pct:g}%): {ok}. {r.dd_reason}",
    ]


def section_baseline(research: Research, data_end: int) -> list[str]:
    r = research.base
    lines = [
        *_section(3),
        f"Default mandate config `{r.cfg.variant_id() if r.cfg else BASE}` (variant `{BASE}`). "
        f"TRAIN window {_window(r, True, data_end)}; TEST window {_window(r, False, data_end)}.",
        "",
        *metric_table([("TRAIN", r.train_summary), ("TEST", r.test_summary)]),
        "",
        *_label_lines(r),
    ]
    return lines


# ---------------------------------------------------------------------------- section 4
def _disc_status(r: WalkForwardResult, disc: DiscoveryResult) -> str:
    if r.variant in disc.test_only:
        return "TEST-ONLY (regime OFF, never selectable)"
    if r.variant == disc.selection.variant:
        return "**SELECTED**"
    if r.variant in disc.selection.eligible:
        return "selectable"
    return "not eligible (too few TRAIN trades)"


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
        "| variant | status | TRAIN n | TRAIN avg R | TRAIN 90% CI | TRAIN t | TEST n | "
        "TEST avg R | TEST 90% CI | TEST t | TEST max DD % | label |",
        "|---|---|---:|---:|---|---:|---:|---:|---|---:|---:|---|",
    ]
    for r in disc.results:
        a, b = r.train_summary, r.test_summary
        lines.append(
            f"| `{r.variant}` | {_disc_status(r, disc)} | {a.n} | {_sr(a.avg_r)} | {_ci(a)} | "
            f"{_sr(a.t_stat, 2)} | {b.n} | {_sr(b.avg_r)} | {_ci(b)} | {_sr(b.t_stat, 2)} | "
            f"{b.max_dd_pct:.2f}% | {r.label} |"
        )
    lines += ["", f"**{disc.note}**", ""]
    lines += _selected_lines(disc)
    return lines


def _selected_lines(disc: DiscoveryResult) -> list[str]:
    sel = disc.selected()
    if sel is None:
        return ["No variant was selected, so discovery contributes no candidate to the verdict."]
    return [
        f"The TRAIN column of the selected variant `{sel.variant}` is biased upward by the "
        f"selection itself (it is the best of {disc.selection.k_eligible_by_design}); only its "
        "TEST column is an out-of-sample test. The labels of the other variants are context "
        "only and do not enter the verdict.",
        "",
        *_label_lines(sel),
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
        f"{_sr(ml.avg_win_r, 2)}R and avg loss {_sr(ml.avg_loss_r, 2)}R: "
        f"p* = {ml.threshold:.3f} (no threshold search).",
        f"- L2 penalty {ml.l2:g} (fixed, not tuned); intercept {_sr(ml.model.intercept)}.",
        f"- Model fingerprint (sha256 of features, TRAIN means/stds, intercept, coefficients, "
        f"threshold and l2): `{ml.fingerprint()}`. It is pinned in the ML adoption record "
        "(section 11).",
        "",
        "| feature | standardised coefficient, fitted on TRAIN only and applied unchanged to "
        "TEST (log-odds per TRAIN s.d.) |",
        "|---|---:|",
    ]
    lines += [f"| `{name}` | {_sr(w)} |" for name, w in ml.explain()]
    return lines


def section_ml(research: Research) -> list[str]:
    r, b = research.ml, research.base
    lines = [
        *_section(5),
        f"Variant `{ML_VARIANT}`: the baseline rules plus the `L_ml_filter` layer, a logistic "
        f"regression on {len(FEATURES)} features ({', '.join(FEATURES)}). It can only REMOVE "
        "trades that passed every mandatory rule. It is fitted ONLY on TRAIN candidates and "
        "applied unchanged to TEST, so its TRAIN numbers are IN-SAMPLE.",
        "",
    ]
    if not r.ran:
        lines += [
            f"**The ML layer could not be fitted:** {r.fit_error}",
            "",
            "No backtest was run for it and there is no silent fall-back to the unfiltered "
            "rules (those are the baseline in section 3).",
            "",
        ]
    else:
        lines += _ml_model_lines(r)
        removed = r.test.decisions.get("L_ml_filter", 0)
        lines += [
            "",
            *metric_table(
                [
                    ("base TRAIN", b.train_summary),
                    (f"{ML_VARIANT} TRAIN (in-sample)", r.train_summary),
                    ("base TEST", b.test_summary),
                    (f"{ML_VARIANT} TEST", r.test_summary),
                ]
            ),
            "",
            f"On TEST the filter removed {removed} signals that had passed every mandatory "
            f"rule; TEST expectancy {_sr(b.test_summary.avg_r)}R (base) -> "
            f"{_sr(r.test_summary.avg_r)}R (with the filter).",
            "",
        ]
    lines += _label_lines(r)
    lines += [
        "",
        "LightGBM, other gradient boosting and neural networks are NOT justified at this sample "
        f"size ({r.n_train_candidates} TRAIN candidates, {len(FEATURES)} features): a flexible "
        "model would memorise noise, so only a fixed-penalty logistic regression with a "
        "break-even threshold is allowed.",
    ]
    return lines


# ---------------------------------------------------------------------------- sections 6-9
def section_verdict(research: Research) -> list[str]:
    names = ", ".join(name for name, _ in research.candidates())
    return [
        *_section(6),
        f"Candidates judged (pre-registered, one TEST look each): {names}. Other grid "
        "variants are excluded because choosing among them by TEST numbers would be "
        "selection on TEST. The verdict requires the ROBUST label (positive TRAIN and TEST "
        "expectancy with the TEST 90% CI above zero) and reports the drawdown check.",
        "",
        verdict_line(research),
    ]


def _decision_columns(research: Research) -> list[tuple[str, WalkForwardResult, bool]]:
    cols = [(BASE, research.base)]
    sel = research.discovery.selected()
    if sel is not None:
        cols.append((f"selected {sel.variant}", sel))
    cols.append((ML_VARIANT, research.ml))
    out: list[tuple[str, WalkForwardResult, bool]] = []
    for name, r in cols:
        out += [(f"{name} TRAIN", r, True), (f"{name} TEST", r, False)]
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
        cells = [str(_bt(r, t).decisions.get(rule, 0)) for _, r, t in cols]
        lines.append(f"| `{rule}` | " + " | ".join(cells) + f" | {_cell(meaning)} |")
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
    if total == 0:
        lines.append(f"Result: CLEAN. 0 violations in {2 * len(research.violations)} backtests.")
        return lines
    lines.append(f"Result: **{total} VIOLATIONS** (the pipeline is broken; do not use it):")
    shown = [
        f"- `{v}` {w}: {msg}"
        for v, (tr, te) in research.violations.items()
        for w, msgs in (("TRAIN", tr), ("TEST", te))
        for msg in msgs
    ]
    return lines + shown[:MAX_VIOLATIONS_SHOWN]


def _renumbered_test(r: WalkForwardResult) -> list[Trade]:
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
        ("TEST", data_end, test_eq, audit(_renumbered_test(r), cfg, data_end, test_eq, tf)),
    ]


def _audit_rows(window: str, at: int, equity: float, acts: Sequence[Adaptation]) -> list[str]:
    where = f"{window} at {ms_to_iso(at)} (equity {equity:,.2f})"
    if not acts:
        return [f"| {where} | - | - | none | No active adaptations. | - |"]
    rows = []
    for a in acts:
        evidence = ", ".join(f"#{i}" for i in a.evidence_trade_ids) or "-"
        cells = (where, a.rule, a.scope, a.action, a.explanation, evidence)
        rows.append("| " + " | ".join(_cell(c) for c in cells) + " |")
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
    base_test = _journal_paths(out_dir, BASE)[1].as_posix()
    lines.append(
        f"Reproduce the baseline TEST row with `python -m research.trendbot.journal_rules "
        f"--journal {base_test} --equity {research.base.test.final_equity:.2f} "
        f"--now {ms_to_iso(data_end)} --backtest-journal`."
    )
    return lines


# ---------------------------------------------------------------------------- outputs
def _safe(variant: str) -> str:
    """File-name form of a variant id (``base+ml`` -> ``base_plus_ml``)."""
    return variant.replace("+", "_plus_").replace("/", "_")


def _journal_paths(out_dir: Path, variant: str) -> tuple[Path, Path]:
    safe = _safe(variant)
    return out_dir / "journals" / f"{safe}_train.csv", out_dir / "journals" / f"{safe}_test.csv"


def output_targets(research: Research) -> list[tuple[str, WalkForwardResult]]:
    """The pre-registered candidates that ran: the only variants with journals and packs.

    These are exactly the variants whose TRAIN and TEST columns the report shows in full
    (sections 3, 5 and 7); the other grid variants are one context row each in section 4.
    """
    return [(name, r) for name, r in research.candidates() if r.ran]


def write_journals(research: Research, out_dir: Path) -> dict[str, tuple[Path, Path]]:
    out: dict[str, tuple[Path, Path]] = {}
    for _name, r in output_targets(research):
        train_path, test_path = _journal_paths(out_dir, r.variant)
        train_path.parent.mkdir(parents=True, exist_ok=True)
        write_journal(r.train.trades, train_path)
        write_journal(_renumbered_test(r), test_path)
        out[r.variant] = (train_path, test_path)
    return out


def _review_folder(research: Research, r: WalkForwardResult) -> str:
    if r is research.base:
        return "review_base"
    if r is research.ml:
        return f"review_{_safe(r.variant)}"
    return f"review_selected_{_safe(r.variant)}"


def write_reviews(research: Research, out_dir: Path) -> dict[str, dict[str, Path]]:
    out: dict[str, dict[str, Path]] = {}
    for name, r in output_targets(research):
        trades = [*r.train.trades, *_renumbered_test(r)]
        decisions = Counter(r.train.decisions) + Counter(r.test.decisions)
        out[r.variant] = write_review_pack(
            trades,
            out_dir / _review_folder(research, r),
            f"Trade review: {name}",
            r.cfg or DEFAULT_CFG,
            split_ts=r.split_ts,
            decision_counts=decisions,
        )
    return out


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def section_links(
    research: Research,
    out_dir: Path,
    journals: Mapping[str, tuple[Path, Path]],
    reviews: Mapping[str, Mapping[str, Path]],
) -> list[str]:
    lines = [
        *_section(10),
        "Trade journals (`journal.write_journal`, one CSV per window) for the three "
        "pre-registered candidates, the variants whose TRAIN and TEST columns this report "
        "shows in full. The other discovery variants appear only as one context row each in "
        "section 4, so no journal is kept for them (the same command regenerates them). TEST "
        "trade ids are offset past the TRAIN ids, so ids are unique across a variant's two "
        "journals and its review pack.",
        "",
        "| variant | TRAIN journal | TEST journal |",
        "|---|---|---|",
    ]
    for variant, (a, b) in journals.items():
        lines.append(
            f"| `{variant}` | [{_rel(a, out_dir)}]({_rel(a, out_dir)}) | "
            f"[{_rel(b, out_dir)}]({_rel(b, out_dir)}) |"
        )
    lines += ["", "Human review packs (`review_sheet.write_review_pack`, TRAIN and TEST):", ""]
    for variant, paths in reviews.items():
        md, csv_path = _rel(paths["md"], out_dir), _rel(paths["csv"], out_dir)
        lines.append(f"- `{variant}`: [{md}]({md}) and [{csv_path}]({csv_path})")
    return lines


@dataclass
class AdoptionStatus:
    variant: str
    path: Path
    blocking: list[str]  # one "[rule] reason" per blocking decision (empty = allowed)
    result: WalkForwardResult | None = None
    config_path: Path | None = None  # overrides JSON for `adoption check --config`
    model_path: Path | None = None  # canonical model JSON (its sha256 is the fingerprint)
    model_fingerprint: str | None = None


def config_overrides(cfg: StrategyConfig) -> dict[str, object]:
    """JSON overrides (``adoption.load_config`` format) that rebuild ``cfg`` from defaults."""
    out: dict[str, object] = {}
    for f in fields(StrategyConfig):
        value, default = getattr(cfg, f.name), getattr(DEFAULT_CFG, f.name)
        if f.name == "pair_risk":
            if dict(value) != dict(default):
                out[f.name] = {
                    b: {"max_risk_pct": pr.max_risk_pct, "stop_buffer_pct": pr.stop_buffer_pct}
                    for b, pr in value.items()
                }
        elif value != default:
            out[f.name] = list(value) if isinstance(value, tuple) else value
    return out


def _adoption_targets(research: Research) -> list[WalkForwardResult]:
    """Baseline, TRAIN-selected variant and (if it was fitted) the ML layer."""
    targets = [research.base]
    sel = research.discovery.selected()
    if sel is not None and sel.variant != BASE:
        targets.append(sel)
    if research.ml.ran and fitted_model(research.ml) is not None:
        targets.append(research.ml)
    return targets


def _write_config(cfg: StrategyConfig, out_dir: Path, variant: str) -> Path | None:
    overrides = config_overrides(cfg)
    if not overrides:
        return None
    path = out_dir / f"config_{_safe(variant)}.json"
    path.write_text(json.dumps(overrides, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _write_model(ml: MLFilter, out_dir: Path, variant: str) -> Path:
    """The canonical model JSON, byte for byte what the fingerprint hashes (no newline)."""
    path = out_dir / f"model_{_safe(variant)}.json"
    path.write_text(ml.canonical_json(), encoding="utf-8")
    return path


def write_adoption(research: Research, out_dir: Path, now_ms: int) -> list[AdoptionStatus]:
    """Record BACKTEST + WALK_FORWARD for each adoptable candidate, then check HUMAN_REVIEW.

    The ML record carries the fitted model's fingerprint (CONTRACT.md v2 A3). Each check
    uses the config rebuilt from the written overrides file, exactly as the CLI would, so a
    wrong overrides file shows up as an ``ADOPT_fingerprint`` block in the report.
    ``out_dir / REPORT_NAME`` must already exist (it is the evidence file the check audits).
    """
    out: list[AdoptionStatus] = []
    for r in _adoption_targets(research):
        cfg = r.cfg or DEFAULT_CFG
        ml = fitted_model(r)
        model_fp = ml.fingerprint() if ml is not None else None
        rec = empty_record(r.variant, cfg, model_fp)
        rec = replace(
            rec,
            backtest=BacktestStage(report_path=REPORT_NAME, completed_utc=ms_to_iso(now_ms)),
            walk_forward=WalkForwardStage(
                label=r.label,
                dd_ok=r.dd_ok,
                split_utc=ms_to_iso(r.split_ts),
                train_n=r.train_summary.n,
                test_n=r.test_summary.n,
                test_avg_r=r.test_summary.avg_r,
                report_path=REPORT_NAME,
            ),
        )
        path = out_dir / f"adoption_{_safe(r.variant)}.json"
        save_record(rec, path)
        config_path = _write_config(cfg, out_dir, r.variant)
        model_path = _write_model(ml, out_dir, r.variant) if ml is not None else None
        decisions = check_promotion(
            rec, HUMAN_REVIEW, load_config(config_path), now_ms, out_dir, model_fp
        )
        blocking = [f"[{d.rule}] {d.reason}" for d in decisions]
        out.append(AdoptionStatus(r.variant, path, blocking, r, config_path, model_path, model_fp))
    return out


def check_command(st: AdoptionStatus, out_dir: Path, stage: str = HUMAN_REVIEW) -> str:
    """The exact ``adoption check`` command line for a record written by this run."""
    parts = [
        "python -m research.trendbot.adoption check",
        f"--record {(out_dir / _rel(st.path, out_dir)).as_posix()}",
        f"--stage {stage}",
    ]
    if st.config_path is not None:
        parts.append(f"--config {(out_dir / _rel(st.config_path, out_dir)).as_posix()}")
    if st.model_fingerprint is not None:
        parts.append(f"--model-fingerprint {st.model_fingerprint}")
    return " ".join(parts)


def _adoption_table(statuses: Sequence[AdoptionStatus]) -> list[str]:
    lines = [
        "| variant | walk-forward label (TRAIN + TEST) | TRAIN n | TEST n | TEST avg R | "
        "config sha256 (first 16) | ML model sha256 (first 16) | promotion to HUMAN_REVIEW |",
        "|---|---|---:|---:|---:|---|---|---|",
    ]
    for st in statuses:
        r = st.result
        if r is None:
            continue
        cfg_fp = config_fingerprint(r.cfg or DEFAULT_CFG)[:16]
        model = st.model_fingerprint[:16] if st.model_fingerprint else "none (no ML layer)"
        verdict = "BLOCKED" if st.blocking else "allowed"
        lines.append(
            f"| `{st.variant}` | {r.label} | {r.train_summary.n} | {r.test_summary.n} | "
            f"{_sr(r.test_summary.avg_r)} | `{cfg_fp}` | `{model}` | {verdict} |"
        )
    return lines


def _status_lines(statuses: Sequence[AdoptionStatus], out_dir: Path) -> list[str]:
    lines: list[str] = []
    for st in statuses:
        rel = _rel(st.path, out_dir)
        files = [f"record [{rel}]({rel})"]
        for extra in (st.config_path, st.model_path):
            if extra is not None:
                x = _rel(extra, out_dir)
                files.append(f"[{x}]({x})")
        head = f"- `{st.variant}`: {', '.join(files)}; promotion to {HUMAN_REVIEW} is "
        if st.blocking:
            lines.append(head + "BLOCKED: " + " ".join(st.blocking))
        else:
            lines.append(
                head + "allowed: a named person must now review every trade in its review "
                "pack, then fill `human_review` in the record before any testnet run."
            )
        lines.append(f"  Check: `{check_command(st, out_dir)}`")
    return lines


def _ml_adoption_lines(statuses: Sequence[AdoptionStatus], out_dir: Path) -> list[str]:
    ml_status = next((st for st in statuses if st.variant == ML_VARIANT), None)
    if ml_status is None or ml_status.model_path is None:
        return [
            f"The ML layer `{ML_VARIANT}` could not be fitted, so there is no model to "
            "fingerprint and no adoption record for it."
        ]
    model = _rel(ml_status.model_path, out_dir)
    return [
        f"The ML layer `{ML_VARIANT}` is adopted as a (config, model) pair: its record pins "
        "the config fingerprint AND the fitted model's fingerprint "
        f"`{ml_status.model_fingerprint}` (`MLFilter.fingerprint()`, the sha256 of "
        f"[{model}]({model})). **Refitting the model (new TRAIN data, a later split, a "
        "different l2) changes the fingerprint, so a refitted model is a NEW candidate and "
        f"restarts the adoption path at {BACKTEST}.** `adoption check` blocks "
        "(`ADOPT_fingerprint`) a model whose fingerprint differs from the recorded one, and "
        "any `+ml` record without one; the live bot must pass the fingerprint of the model it "
        "actually runs.",
    ]


def section_adoption(
    statuses: Sequence[AdoptionStatus], out_dir: Path, synthetic: bool = False
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
    if synthetic:
        lines += [
            "**This run used SYNTHETIC data: its records only demonstrate the mechanism. A "
            "synthetic result can never justify adopting a variant; the path must be run on "
            "real candles from the backtest stage.**",
            "",
        ]
    lines += [*_adoption_table(statuses), "", *_status_lines(statuses, out_dir), ""]
    lines += [*_ml_adoption_lines(statuses, out_dir), ""]
    lines.append(
        "A `config_<variant>.json` file (the `StrategyConfig` overrides versus the defaults, "
        "e.g. costs or discovery parameters) is written whenever the tested config is not the "
        "default one; the check needs it as `--config`. The regime-OFF variant is test-only "
        "and can never be adopted."
    )
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
    adoption = (
        section_adoption(adoption_statuses, out_dir, ds.kind == "synthetic")
        if adoption_statuses is not None
        else [*_section(11), "(being written)"]
    )
    sections = [
        [f"# Walk-forward research report: {title}"],
        section_win_rate(),
        section_provenance(ds, research, data_end),
        section_baseline(research, data_end),
        section_discovery(research),
        section_ml(research),
        section_verdict(research),
        section_decisions(research),
        section_invariants(research),
        section_journal_rules(research, data_end, out_dir),
        section_links(research, out_dir, journals, reviews),
        adoption,
        section_disclaimer(),
    ]
    return "\n\n".join("\n".join(s) for s in sections) + "\n"


def write_outputs(ds: Dataset, research: Research, out_dir: Path, now_ms: int) -> Path:
    """Journals, review packs, adoption records and ``REPORT.md``; returns the report path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    journals = write_journals(research, out_dir)
    reviews = write_reviews(research, out_dir)
    report = out_dir / REPORT_NAME
    report.write_text(render_report(ds, research, out_dir, journals, reviews, None), "utf-8")
    statuses = write_adoption(research, out_dir, now_ms)
    report.write_text(render_report(ds, research, out_dir, journals, reviews, statuses), "utf-8")
    return report


# ---------------------------------------------------------------------------- calibration
CAL_FIELDS = (
    "seed",
    "base_label",
    "base_train_n",
    "base_train_avg_r",
    "base_test_n",
    "base_test_avg_r",
    "base_test_ci90_low",
    "base_dd_ok",
    "selected",
    "selected_label",
    "selected_train_avg_r",
    "selected_test_avg_r",
    "selected_test_n",
    "ml_label",
    "ml_train_avg_r_in_sample",
    "ml_test_avg_r",
    "ml_test_n",
    "ml_fit_error",
    "ml_hour_sin",
    "ml_hour_cos",
    "ml_top_feature",
    "ml_fingerprint",
    "verdict_robust",
    "violations",
)
HOUR_TERMS = ("hour_sin", "hour_cos")


def _ml_cal_fields(m: WalkForwardResult) -> dict[str, object]:
    ml = fitted_model(m) if m.ran else None
    if ml is None:
        return {k: "" for k in ("ml_hour_sin", "ml_hour_cos", "ml_top_feature", "ml_fingerprint")}
    coefs = dict(ml.explain())
    return {
        "ml_hour_sin": round(coefs["hour_sin"], 4),
        "ml_hour_cos": round(coefs["hour_cos"], 4),
        "ml_top_feature": max(coefs, key=lambda k: abs(coefs[k])),
        "ml_fingerprint": ml.fingerprint(),
    }


def _cal_row(seed: int, research: Research) -> dict[str, object]:
    b, m, s = research.base, research.ml, research.discovery.selected()
    return {
        "seed": seed,
        "base_label": b.label,
        "base_train_n": b.train_summary.n,
        "base_train_avg_r": round(b.train_summary.avg_r, 4),
        "base_test_n": b.test_summary.n,
        "base_test_avg_r": round(b.test_summary.avg_r, 4),
        "base_test_ci90_low": round(b.test_summary.ci90_low, 4),
        "base_dd_ok": b.dd_ok,
        "selected": s.variant if s else "",
        "selected_label": s.label if s else "NONE",
        "selected_train_avg_r": round(s.train_summary.avg_r, 4) if s else "",
        "selected_test_avg_r": round(s.test_summary.avg_r, 4) if s else "",
        "selected_test_n": s.test_summary.n if s else "",
        "ml_label": m.label,
        "ml_train_avg_r_in_sample": round(m.train_summary.avg_r, 4) if m.ran else "",
        "ml_test_avg_r": round(m.test_summary.avg_r, 4) if m.ran else "",
        "ml_test_n": m.test_summary.n if m.ran else "",
        "ml_fit_error": m.fit_error or "",
        **_ml_cal_fields(m),
        "verdict_robust": bool(research.robust()),
        "violations": research.n_violations(),
    }


CalJob = tuple[str, int, float, tuple[tuple[str, object], ...]]


def cost_overrides(cfg: StrategyConfig | None) -> tuple[tuple[str, object], ...]:
    """The cost fields of ``cfg`` that differ from the defaults (picklable, for workers)."""
    if cfg is None:
        return ()
    return tuple(
        (k, getattr(cfg, k)) for k in COST_FLAGS if getattr(cfg, k) != getattr(DEFAULT_CFG, k)
    )


def calibrate_one(job: CalJob) -> dict[str, object]:
    """One calibration seed (top-level so a process pool can pickle it)."""
    world, seed, years, overrides = job
    cfg = DEFAULT_CFG.with_changes(**dict(overrides))
    ds = load_synthetic(world, seed, years)
    return _cal_row(seed, run_pipeline(ds.data, ds.events, cfg))


def calibrate(
    world: str, n_seeds: int, years: float, workers: int, cfg: StrategyConfig | None = None
) -> list[dict[str, object]]:
    overrides = cost_overrides(cfg)
    jobs: list[CalJob] = [(world, seed, years, overrides) for seed in range(1, n_seeds + 1)]
    if workers <= 1:
        return [calibrate_one(j) for j in jobs]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(calibrate_one, jobs))


def wilson(k: int, n: int, z: float = 1.645) -> tuple[float, float]:
    """Wilson score interval of a binomial proportion (default 90%)."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def _rate(k: int, n: int) -> str:
    lo, hi = wilson(k, n)
    return f"{k}/{n} = {k / n:.0%} (90% Wilson CI {lo:.0%}-{hi:.0%})" if n else "n/a"


def _mean(values: Sequence[object]) -> str:
    nums = [float(v) for v in values if v != ""]  # type: ignore[arg-type]
    return _sr(math.fsum(nums) / len(nums)) if nums else "n/a"


_ROBUST_MEANING = {
    "null": " = FALSE-POSITIVE rate (no edge exists)",
    "decay": " = FALSE-POSITIVE rate (no edge exists in TEST)",
    "planted": " = DETECTION rate (a real edge exists in TRAIN and TEST)",
    "hour_edge": " (a real but hour-specific edge exists)",
}


def _count(rows: Sequence[Mapping[str, object]], key: str, *values: str) -> int:
    return sum(1 for r in rows if r[key] in values)


def _ml_hour_line(rows: Sequence[Mapping[str, object]]) -> str | None:
    fitted = [r for r in rows if r["ml_top_feature"] != ""]
    if not fitted:
        return None
    top_hour = _count(fitted, "ml_top_feature", *HOUR_TERMS)
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


def _world_findings(world: str, rows: Sequence[Mapping[str, object]]) -> list[str]:
    n = len(rows)
    robust = sum(1 for r in rows if r["verdict_robust"])
    meaning = _ROBUST_MEANING.get(world, "")
    lines = [
        f"- Verdict ROBUST (any of the 3 pre-registered candidates){meaning}: {_rate(robust, n)}.",
        f"- Baseline labelled ROBUST{meaning}: {_rate(_count(rows, 'base_label', 'ROBUST'), n)}.",
    ]
    for key, name in (("selected_label", "Discovery-selected"), ("ml_label", "ML layer")):
        lines.append(f"- {name} labelled ROBUST{meaning}: {_rate(_count(rows, key, 'ROBUST'), n)}.")
    lines.append(
        f"- Baseline mean avg R: TRAIN {_mean([r['base_train_avg_r'] for r in rows])} vs TEST "
        f"{_mean([r['base_test_avg_r'] for r in rows])}."
    )
    if world == "decay":
        t_only = _count(rows, "base_label", "TRAIN-ONLY")
        untested = _count(rows, "base_label", "UNTESTED")
        lines += [
            f"- Baseline labelled TRAIN-ONLY: {_rate(t_only, n)}; UNTESTED: {_rate(untested, n)}.",
            f"- Baseline labelled TRAIN-ONLY or UNTESTED (the decay is caught or at least not "
            f"passed): {_rate(t_only + untested, n)}.",
        ]
    both = [r for r in rows if r["ml_test_avg_r"] != ""]
    helped = sum(1 for r in both if float(r["ml_test_avg_r"]) > float(r["base_test_avg_r"]))  # type: ignore[arg-type]
    if n - len(both):
        lines.append(
            f"- ML layer NOT fitted (InsufficientData, labelled UNTESTED, no fall-back) in "
            f"{n - len(both)}/{n} seeds."
        )
    lines.append(
        f"- ML layer fitted in {len(both)}/{n} seeds; its TEST avg R beat the baseline's in "
        f"{helped}/{len(both)} of those (mean TEST avg R: base "
        f"{_mean([r['base_test_avg_r'] for r in both])}, ML "
        f"{_mean([r['ml_test_avg_r'] for r in both])}; mean TEST n: base "
        f"{_mean_n([r['base_test_n'] for r in both])}, ML "
        f"{_mean_n([r['ml_test_n'] for r in both])})."
    )
    hour = _ml_hour_line(rows)
    if hour is not None:
        lines.append(hour)
    viol = sum(int(r["violations"]) for r in rows)  # type: ignore[call-overload]
    lines.append(f"- Invariant violations over all backtests of all seeds: {viol}.")
    return lines


def _mean_n(values: Sequence[object]) -> str:
    nums = [float(v) for v in values if v != ""]  # type: ignore[arg-type]
    return f"{statistics.fmean(nums):.1f}" if nums else "n/a"


def render_calibration(
    world: str,
    years: float,
    rows: Sequence[Mapping[str, object]],
    cfg: StrategyConfig | None = None,
) -> str:
    truth, prediction = WORLD_TRUTH[world]
    n = len(rows)
    lines = [
        f"# Calibration: synthetic world `{world}`, seeds 1-{n}",
        "",
        WIN_RATE_STATEMENT,
        "",
        f"Each seed is a full run of the pipeline ({years:g} years of 4H candles, 70/30 "
        "walk-forward): baseline, discovery (K = 13 variants, selected on TRAIN) and the ML "
        "layer. **Synthetic results are verification of the methodology, not evidence about "
        "real markets.** Each label below combines that candidate's TRAIN and TEST windows "
        "(`metrics.label`); the verdict takes up to three looks at TEST per seed.",
        "",
        f"- Ground truth: {truth}",
        f"- What the ground truth predicts: {prediction}",
        *cost_lines(cfg or DEFAULT_CFG),
        "",
        "## Label frequencies",
        "",
        "| walk-forward label (TRAIN + TEST) | baseline | discovery-selected | ML layer |",
        "|---|---:|---:|---:|",
    ]
    for lab in (*LABELS, "NONE"):
        counts = [_count(rows, k, lab) for k in _CAL_LABEL_KEYS]
        if lab == "NONE" and not any(counts):
            continue
        lines.append(f"| {lab} | " + " | ".join(f"{c}/{n}" for c in counts) + " |")
    lines += ["", "## Findings", "", *_world_findings(world, rows), ""]
    lines += _per_seed_lines(rows)
    lines += ["", "## Risk disclaimer", "", DISCLAIMER]
    return "\n".join(lines) + "\n"


_CAL_LABEL_KEYS = ("base_label", "selected_label", "ml_label")


def _num(value: object, digits: int = 3) -> str:
    return _sr(float(value), digits) if value != "" else "n/a"  # type: ignore[arg-type]


def _per_seed_lines(rows: Sequence[Mapping[str, object]]) -> list[str]:
    lines = [
        "## Per seed (TRAIN and TEST side by side)",
        "",
        "| seed | base TRAIN n | base TRAIN avg R | base TEST n | base TEST avg R | base TEST "
        "90% CI low | base label | selected | selected TRAIN avg R | selected TEST avg R | "
        "selected label | ML TRAIN avg R (in-sample) | ML TEST avg R | ML TEST n | ML label | "
        "verdict ROBUST | violations |",
        "|---:|---:|---:|---:|---:|---:|---|---|---:|---:|---|---:|---:|---:|---|---|---:|",
    ]
    for r in rows:
        ml_n = r["ml_test_n"] if r["ml_test_avg_r"] != "" else "n/a"
        lines.append(
            f"| {r['seed']} | {r['base_train_n']} | {_num(r['base_train_avg_r'])} | "
            f"{r['base_test_n']} | {_num(r['base_test_avg_r'])} | "
            f"{_num(r['base_test_ci90_low'])} | {r['base_label']} | `{r['selected'] or '-'}` | "
            f"{_num(r['selected_train_avg_r'])} | {_num(r['selected_test_avg_r'])} | "
            f"{r['selected_label']} | {_num(r['ml_train_avg_r_in_sample'])} | "
            f"{_num(r['ml_test_avg_r'])} | {ml_n} | {r['ml_label']} | "
            f"{'yes' if r['verdict_robust'] else 'no'} | {r['violations']} |"
        )
    return lines


def write_calibration(
    world: str,
    years: float,
    rows: Sequence[Mapping[str, object]],
    out_dir: Path,
    cfg: StrategyConfig | None = None,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / CALIBRATION_CSV).open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CAL_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    path = out_dir / CALIBRATION_NAME
    path.write_text(render_calibration(world, years, rows, cfg), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------- CLI
def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m research.trendbot.run_research",
        description="70/30 walk-forward research report (expectancy, never win rate).",
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--data-dir", help="directory of <PAIR>-<tf>.csv candle files")
    src.add_argument("--synthetic", choices=WORLDS, help="synthetic world with known truth")
    p.add_argument("--events", help="news calendar CSV (with --data-dir)")
    p.add_argument("--pairs", nargs="+", default=list(PAIRS), help="pairs to trade")
    p.add_argument("--timeframe", default="4h", help="candle timeframe of the files (4h)")
    p.add_argument("--seed", type=int, default=1, help="synthetic seed (default 1)")
    p.add_argument("--years", type=float, default=6.0, help="synthetic years (default 6)")
    p.add_argument("--out-dir", required=True, help="output directory")
    p.add_argument(
        "--calibrate-seeds", type=int, default=None, help="run seeds 1..N, write CALIBRATION.md"
    )
    p.add_argument(
        "--workers", type=int, default=min(4, os.cpu_count() or 1), help="calibration processes"
    )
    p.add_argument("--now", default=None, help="ISO-8601 time stamped on the adoption records")
    p.add_argument(
        "--fee-rate",
        type=float,
        default=None,
        help=(
            f"fee per side as a FRACTION of notional (default {DEFAULT_CFG.fee_rate:g} = "
            f"{DEFAULT_CFG.fee_rate:.2%}, Binance spot taker; at least 0.0005). Coinbase "
            "Advanced Trade taker fees at low tiers are several times Binance's: pass the real "
            "tier, e.g. 0.006 for 0.6%%"
        ),
    )
    p.add_argument(
        "--slippage-pct",
        type=float,
        default=None,
        help=(
            f"adverse slippage on market fills in PERCENT (default {DEFAULT_CFG.slippage_pct:g}; "
            "at least 0.01)"
        ),
    )
    p.add_argument(
        "--exchange-id",
        default=None,
        help=(
            f"ccxt id of the exchange traded (default {DEFAULT_CFG.exchange_id}); news events "
            "scoped EXCHANGE:<id> block every pair"
        ),
    )
    return p


def config_from_args(args: argparse.Namespace) -> StrategyConfig:
    """``StrategyConfig`` with the cost flags applied, validated; raises ``ConfigError``.

    ``StrategyConfig`` enforces the realistic-cost floors; this adds the checks it cannot
    make: finite numbers, and ceilings that catch a percent typed as a fraction.
    """
    changes: dict[str, object] = {}
    if args.fee_rate is not None:
        if not (math.isfinite(args.fee_rate) and args.fee_rate <= MAX_FEE_RATE):
            raise ConfigError(
                f"--fee-rate {args.fee_rate!r} is not a plausible fraction of notional per side "
                f"(at most {MAX_FEE_RATE:g}; 0.006 means 0.6%)"
            )
        changes["fee_rate"] = args.fee_rate
    if args.slippage_pct is not None:
        if not (math.isfinite(args.slippage_pct) and args.slippage_pct <= MAX_SLIPPAGE_PCT):
            raise ConfigError(
                f"--slippage-pct {args.slippage_pct!r} is not a plausible percent "
                f"(at most {MAX_SLIPPAGE_PCT:g})"
            )
        changes["slippage_pct"] = args.slippage_pct
    if args.exchange_id is not None:
        exchange = args.exchange_id.strip().lower()
        if not _EXCHANGE_ID_RE.fullmatch(exchange):
            raise ConfigError(
                f"--exchange-id {args.exchange_id!r} is not a ccxt exchange id (e.g. binance)"
            )
        changes["exchange_id"] = exchange
    return DEFAULT_CFG.with_changes(**changes)


def _check_args(p: argparse.ArgumentParser, args: argparse.Namespace) -> StrategyConfig:
    if args.synthetic and args.events:
        p.error("--events is only used with --data-dir (synthetic worlds bring their own)")
    if args.calibrate_seeds is not None and not args.synthetic:
        p.error("--calibrate-seeds needs --synthetic WORLD (ground truth is required)")
    if args.calibrate_seeds is not None and args.calibrate_seeds < 1:
        p.error("--calibrate-seeds must be >= 1")
    if args.timeframe != "4h":
        p.error("the mandate is a 4H strategy: --timeframe must be 4h")
    try:
        return config_from_args(args)
    except ConfigError as exc:
        p.error(str(exc))
        raise  # unreachable: p.error exits


def _now_ms(text: str | None) -> int:
    if text is None:
        return time.time_ns() // 1_000_000_000 * 1000  # whole seconds
    return iso_to_ms(text)


def cost_summary(cfg: StrategyConfig) -> str:
    return (
        f"costs: fee {cfg.fee_rate:.3%} per side, slippage {cfg.slippage_pct:g}% on market "
        f"fills, exchange {cfg.exchange_id}"
    )


def _print_summary(research: Research, report: Path) -> None:
    print(cost_summary(research.cfg))
    for name, r in research.candidates():
        s = r.test_summary
        print(f"{name}: {r.label} (TEST n={s.n}, avg {s.avg_r:+.3f}R)")
    print(verdict_line(research))
    print(f"invariant violations: {research.n_violations()}")
    print(f"report: {report}")


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    cfg = _check_args(parser, args)
    out_dir = Path(args.out_dir)
    if args.calibrate_seeds is not None:
        rows = calibrate(args.synthetic, args.calibrate_seeds, args.years, args.workers, cfg)
        path = write_calibration(args.synthetic, args.years, rows, out_dir, cfg)
        print(cost_summary(cfg))
        print(f"calibration of {args.synthetic} over {len(rows)} seeds -> {path}")
        return 1 if any(int(r["violations"]) for r in rows) else 0  # type: ignore[call-overload]
    try:
        now_ms = _now_ms(args.now)
        if args.synthetic:
            ds = load_synthetic(args.synthetic, args.seed, args.years, tuple(args.pairs))
        else:
            ds = load_files(args.data_dir, tuple(args.pairs), args.events, args.timeframe)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    research = run_pipeline(ds.data, ds.events, cfg)
    report = write_outputs(ds, research, out_dir, now_ms)
    _print_summary(research, report)
    return 1 if research.n_violations() else 0


if __name__ == "__main__":
    raise SystemExit(main())
