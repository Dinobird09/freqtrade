"""One command: 70/30 walk-forward research report, trade journals and human review packs.

Usage (from the repo root)::

    # real candles (downloaded on a networked machine with fetch_data + ccxt)
    python -m research.trendbot.run_research --data-dir research/data \
        [--events events.csv] [--pairs BTC/USDT ETH/USDT BNB/USDT] --out-dir research/results/real
    # synthetic world with KNOWN ground truth (verification of the methodology only)
    python -m research.trendbot.run_research --synthetic planted --seed 1 [--years 6] \
        --out-dir research/results/synthetic_planted_s1
    # calibration: label frequencies over seeds 1..N of a synthetic world
    python -m research.trendbot.run_research --synthetic null --calibrate-seeds 20 \
        --out-dir research/results/calibration_null [--workers 4]

What one run does (every step on the same chronological 70/30 split):

1. baseline: the default (mandate) config, TRAIN then TEST;
2. discovery (``strategy_discovery.discover``): 12 legal tightenings plus the ONE test-only
   regime-OFF variant, one variant selected on TRAIN t-stat before any TEST backtest ran;
3. ML layer (``ml_filter``): a logistic filter on the baseline rules, fitted on purged TRAIN
   candidates only and applied unchanged to TEST (``UNTESTED`` if it cannot be fitted);
4. ``invariants.check_invariants`` on every backtest; ``journal_rules.audit`` at the end of
   TEST; a journal per variant and window; review packs for the baseline and the selected
   variant; an adoption record per adoptable candidate (stage reached: WALK_FORWARD).

The verdict only considers the three PRE-REGISTERED candidates (baseline, TRAIN-selected
variant, ML layer). Picking any other grid variant because of its TEST numbers would be
selection on TEST, so their TEST labels are context only.

``REPORT.md`` sections, in order: (1) no win rate is promised or targeted, (2) data
provenance, (3) baseline TRAIN | TEST, (4) discovery TRAIN | TEST, (5) ML filter, (6) verdict,
(7) decision counts per rule, (8) invariant audit, (9) journal rules at the end of TEST,
(10) journals and review packs, (11) adoption path, (12) risk disclaimer.

Exit code 0, or 1 if any backtest violated an invariant (the report must be clean), 2 on a
usage or data error.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import sys
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path

from .adoption import (
    HUMAN_REVIEW,
    STAGES,
    WALK_FORWARD,
    BacktestStage,
    WalkForwardStage,
    check_promotion,
    empty_record,
    save_record,
)
from .backtester import BacktestResult
from .config import RULE_IDS, StrategyConfig
from .data import gap_report, load_dataset, timeframe_to_ms
from .invariants import check_invariants
from .journal import iso_to_ms, ms_to_iso, write_journal
from .journal_rules import audit, render_markdown
from .metrics import LABELS, Summary
from .ml_filter import FEATURES, make_factory
from .models import Candle, NewsEvent, Trade
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
    "9. Journal rules at the end of TEST",
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
    "reward:risk >= 2:1. Win rate appears below only as context."
)

DISCLAIMER = (
    "This is a research and testing tool, not financial advice and not a recommendation to "
    "trade. Backtests and synthetic worlds are simplified models; past or simulated results "
    "do not predict future results. Crypto trading can result in the total loss of the "
    "capital used."
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


def load_files(
    data_dir: str | Path,
    pairs: Sequence[str] = PAIRS,
    events_path: str | Path | None = None,
    timeframe: str = "4h",
) -> Dataset:
    """Real candle files (``data.load_dataset``) plus the optional news calendar CSV."""
    data = load_dataset(data_dir, pairs, timeframe)
    tf_ms = timeframe_to_ms(timeframe)
    events = load_events(events_path) if events_path else []
    lines = [
        f"- Source: candle CSV files in `{data_dir}` ({timeframe}), loaded with "
        "`data.load_dataset` (strict validation: sorted, no duplicates, sane OHLC).",
        "",
        "| pair | candles | first candle (UTC) | last candle (UTC) | gaps |",
        "|---|---:|---|---|---|",
    ]
    for pair, candles in data.items():
        lines.append(
            f"| {pair} | {len(candles)} | {ms_to_iso(candles[0].ts)} | "
            f"{ms_to_iso(candles[-1].ts)} | {_gap_text(candles, tf_ms)} |"
        )
    lines.append("")
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


def section_provenance(ds: Dataset, research: Research, data_end: int) -> list[str]:
    lines = [*_section(2), *ds.provenance]
    lines += [
        f"- Walk-forward split: {ms_to_iso(research.split)} = 70% of the common time range of "
        f"all pairs (TRAIN = signal candles before it, TEST = at or after it, up to "
        f"{ms_to_iso(data_end)}). TEST starts with fresh equity and fresh circuit breakers; "
        "indicators warm up on earlier candles only.",
        f"- Costs: fee {research.cfg.fee_rate:.2%} per side, slippage "
        f"{research.cfg.slippage_pct:g}% on market fills; starting capital "
        f"{research.cfg.starting_capital:,.0f} per window.",
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
def _ml_model_lines(r: WalkForwardResult) -> list[str]:
    ml = getattr(r.entry_filter, "ml", None)
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
        "",
        "| feature | standardised coefficient (log-odds per TRAIN s.d.) |",
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


def section_journal_rules(research: Research, data_end: int) -> list[str]:
    lines = [
        *_section(9),
        f"`journal_rules.audit` over each TEST journal at {ms_to_iso(data_end)} (the end of "
        "TEST) with the TEST final equity: every adaptation the live bot would be applying "
        "right now because of past trades, with the journal rows that caused it.",
        "",
    ]
    for name, r in research.candidates():
        if not r.ran:
            lines += [f"**{name}:** not run ({r.layer} could not be fitted).", ""]
            continue
        acts = audit(_renumbered_test(r), r.cfg, data_end, r.test.final_equity)
        lines += [
            f"**{name}** (TEST final equity {r.test.final_equity:,.2f}):",
            "",
            render_markdown(acts),
            "",
        ]
    return lines


# ---------------------------------------------------------------------------- outputs
def _journal_paths(out_dir: Path, variant: str) -> tuple[Path, Path]:
    safe = variant.replace("+", "_plus_").replace("/", "_")
    return out_dir / "journals" / f"{safe}_train.csv", out_dir / "journals" / f"{safe}_test.csv"


def write_journals(research: Research, out_dir: Path) -> dict[str, tuple[Path, Path]]:
    out: dict[str, tuple[Path, Path]] = {}
    for r in research.all_results():
        if not r.ran:
            continue
        train_path, test_path = _journal_paths(out_dir, r.variant)
        train_path.parent.mkdir(parents=True, exist_ok=True)
        write_journal(r.train.trades, train_path)
        write_journal(_renumbered_test(r), test_path)
        out[r.variant] = (train_path, test_path)
    return out


def _review_targets(research: Research) -> list[tuple[str, str, WalkForwardResult]]:
    targets = [("review_base", f"Trade review: baseline `{BASE}`", research.base)]
    sel = research.discovery.selected()
    if sel is not None:
        targets.append(
            (f"review_selected_{sel.variant}", f"Trade review: selected `{sel.variant}`", sel)
        )
    return targets


def write_reviews(research: Research, out_dir: Path) -> dict[str, dict[str, Path]]:
    out: dict[str, dict[str, Path]] = {}
    for folder, title, r in _review_targets(research):
        trades = [*r.train.trades, *_renumbered_test(r)]
        decisions = Counter(r.train.decisions) + Counter(r.test.decisions)
        out[r.variant] = write_review_pack(
            trades, out_dir / folder, title, r.cfg, split_ts=r.split_ts, decision_counts=decisions
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
        "Trade journals (`journal.write_journal`, one CSV per variant and window). TEST trade "
        "ids are offset past the TRAIN ids, so ids are unique across a variant's two journals "
        "and its review pack.",
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


def _adoption_targets(research: Research) -> list[WalkForwardResult]:
    targets = [research.base]
    sel = research.discovery.selected()
    if sel is not None and sel.variant != BASE:
        targets.append(sel)
    return targets


def write_adoption(research: Research, out_dir: Path, now_ms: int) -> list[AdoptionStatus]:
    """Record BACKTEST + WALK_FORWARD for each adoptable candidate, then check HUMAN_REVIEW.

    ``out_dir / REPORT_NAME`` must already exist (it is the evidence file the check audits).
    """
    out: list[AdoptionStatus] = []
    for r in _adoption_targets(research):
        rec = empty_record(r.variant, r.cfg)
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
        path = out_dir / f"adoption_{r.variant}.json"
        save_record(rec, path)
        decisions = check_promotion(rec, HUMAN_REVIEW, r.cfg, now_ms, base_dir=out_dir)
        out.append(AdoptionStatus(r.variant, path, [f"[{d.rule}] {d.reason}" for d in decisions]))
    return out


def section_adoption(
    statuses: Sequence[AdoptionStatus], out_dir: Path, synthetic: bool = False
) -> list[str]:
    lines = [
        *_section(11),
        "The only path to real money (enforced by `adoption.py`; no step can be skipped and "
        "the promoted config must match the tested config's sha256 fingerprint):",
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
    for st in statuses:
        rel = _rel(st.path, out_dir)
        if st.blocking:
            lines.append(
                f"- `{st.variant}`: record [{rel}]({rel}); promotion to {HUMAN_REVIEW} is "
                f"BLOCKED: " + " ".join(st.blocking)
            )
        else:
            lines.append(
                f"- `{st.variant}`: record [{rel}]({rel}); promotion to {HUMAN_REVIEW} is "
                "allowed: a named person must now review every trade in its review pack, then "
                "fill `human_review` in the record before any testnet run."
            )
    lines += [
        "",
        f"The ML layer `{ML_VARIANT}` has no adoption record: its fitted coefficients are not "
        "part of `StrategyConfig`, so the config fingerprint cannot pin them. The regime-OFF "
        "variant is test-only and can never be adopted.",
        "",
        "Check a record with `python -m research.trendbot.adoption check --record "
        f"{out_dir.as_posix()}/adoption_{BASE}.json --stage {HUMAN_REVIEW}`.",
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
        section_journal_rules(research, data_end),
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
    "base_train_avg_r",
    "base_test_avg_r",
    "base_test_n",
    "base_dd_ok",
    "selected",
    "selected_label",
    "selected_test_avg_r",
    "selected_test_n",
    "ml_label",
    "ml_test_avg_r",
    "ml_test_n",
    "ml_fit_error",
    "verdict_robust",
    "violations",
)


def _cal_row(seed: int, research: Research) -> dict[str, object]:
    b, m, s = research.base, research.ml, research.discovery.selected()
    return {
        "seed": seed,
        "base_label": b.label,
        "base_train_avg_r": round(b.train_summary.avg_r, 4),
        "base_test_avg_r": round(b.test_summary.avg_r, 4),
        "base_test_n": b.test_summary.n,
        "base_dd_ok": b.dd_ok,
        "selected": s.variant if s else "",
        "selected_label": s.label if s else "NONE",
        "selected_test_avg_r": round(s.test_summary.avg_r, 4) if s else "",
        "selected_test_n": s.test_summary.n if s else "",
        "ml_label": m.label,
        "ml_test_avg_r": round(m.test_summary.avg_r, 4) if m.ran else "",
        "ml_test_n": m.test_summary.n if m.ran else "",
        "ml_fit_error": m.fit_error or "",
        "verdict_robust": bool(research.robust()),
        "violations": research.n_violations(),
    }


def calibrate_one(job: tuple[str, int, float]) -> dict[str, object]:
    """One calibration seed (top-level so a process pool can pickle it)."""
    world, seed, years = job
    ds = load_synthetic(world, seed, years)
    return _cal_row(seed, run_pipeline(ds.data, ds.events))


def calibrate(world: str, n_seeds: int, years: float, workers: int) -> list[dict[str, object]]:
    jobs = [(world, seed, years) for seed in range(1, n_seeds + 1)]
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


def _world_findings(world: str, rows: Sequence[Mapping[str, object]]) -> list[str]:
    n = len(rows)
    robust = sum(1 for r in rows if r["verdict_robust"])
    base_robust = sum(1 for r in rows if r["base_label"] == "ROBUST")
    meaning = _ROBUST_MEANING.get(world, "")
    lines = [
        f"- Verdict ROBUST (any pre-registered candidate){meaning}: {_rate(robust, n)}.",
        f"- Baseline labelled ROBUST{meaning}: {_rate(base_robust, n)}.",
    ]
    for key, name in (("selected_label", "Discovery-selected"), ("ml_label", "ML layer")):
        k = sum(1 for r in rows if r[key] == "ROBUST")
        lines.append(f"- {name} labelled ROBUST{meaning}: {_rate(k, n)}.")
    if world == "decay":
        caught = sum(1 for r in rows if r["base_label"] in ("TRAIN-ONLY", "UNTESTED"))
        lines.append(f"- Baseline labelled TRAIN-ONLY or UNTESTED: {_rate(caught, n)}.")
    both = [r for r in rows if r["ml_test_avg_r"] != ""]
    helped = sum(1 for r in both if float(r["ml_test_avg_r"]) > float(r["base_test_avg_r"]))
    unfit = n - len(both)
    if unfit:
        lines.append(
            f"- ML layer NOT fitted (InsufficientData, labelled UNTESTED, no fall-back) in "
            f"{unfit}/{n} seeds."
        )
    lines.append(
        f"- ML layer fitted in {len(both)}/{n} seeds; its TEST avg R beat the baseline's in "
        f"{helped}/{len(both)} of those (mean TEST avg R: base "
        f"{_mean([r['base_test_avg_r'] for r in both])}, ML "
        f"{_mean([r['ml_test_avg_r'] for r in both])})."
    )
    viol = sum(int(r["violations"]) for r in rows)  # type: ignore[call-overload]
    lines.append(f"- Invariant violations over all backtests of all seeds: {viol}.")
    return lines


def render_calibration(world: str, years: float, rows: Sequence[Mapping[str, object]]) -> str:
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
        "real markets.**",
        "",
        f"- Ground truth: {truth}",
        f"- What the ground truth predicts: {prediction}",
        "",
        "## Label frequencies",
        "",
        "| label | baseline | discovery-selected | ML layer |",
        "|---|---:|---:|---:|",
    ]
    for lab in (*LABELS, "NONE"):
        counts = [sum(1 for r in rows if r[k] == lab) for k in _CAL_LABEL_KEYS]
        if lab == "NONE" and not any(counts):
            continue
        lines.append(f"| {lab} | " + " | ".join(f"{c}/{n}" for c in counts) + " |")
    lines += ["", "## Findings", "", *_world_findings(world, rows), ""]
    lines += _per_seed_lines(rows)
    lines += ["", "## Risk disclaimer", "", DISCLAIMER]
    return "\n".join(lines) + "\n"


_CAL_LABEL_KEYS = ("base_label", "selected_label", "ml_label")


def _per_seed_lines(rows: Sequence[Mapping[str, object]]) -> list[str]:
    lines = [
        "## Per seed (TRAIN | TEST avg R side by side for the baseline)",
        "",
        "| seed | base TRAIN avg R | base TEST avg R | base TEST n | base label | selected | "
        "selected TEST avg R | selected label | ML TEST avg R | ML TEST n | ML label | verdict "
        "ROBUST | violations |",
        "|---:|---:|---:|---:|---|---|---:|---|---:|---:|---|---|---:|",
    ]
    for r in rows:
        sel_r = r["selected_test_avg_r"]
        ml_r = r["ml_test_avg_r"]
        lines.append(
            f"| {r['seed']} | {_sr(float(r['base_train_avg_r']))} | "  # type: ignore[arg-type]
            f"{_sr(float(r['base_test_avg_r']))} | {r['base_test_n']} | {r['base_label']} | "  # type: ignore[arg-type]
            f"`{r['selected'] or '-'}` | {_sr(float(sel_r)) if sel_r != '' else 'n/a'} | "  # type: ignore[arg-type]
            f"{r['selected_label']} | {_sr(float(ml_r)) if ml_r != '' else 'n/a'} | "  # type: ignore[arg-type]
            f"{r['ml_test_n'] if ml_r != '' else 'n/a'} | {r['ml_label']} | "
            f"{'yes' if r['verdict_robust'] else 'no'} | {r['violations']} |"
        )
    return lines


def write_calibration(
    world: str, years: float, rows: Sequence[Mapping[str, object]], out_dir: Path
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / CALIBRATION_CSV).open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CAL_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    path = out_dir / CALIBRATION_NAME
    path.write_text(render_calibration(world, years, rows), encoding="utf-8")
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
    return p


def _check_args(p: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.synthetic and args.events:
        p.error("--events is only used with --data-dir (synthetic worlds bring their own)")
    if args.calibrate_seeds is not None and not args.synthetic:
        p.error("--calibrate-seeds needs --synthetic WORLD (ground truth is required)")
    if args.calibrate_seeds is not None and args.calibrate_seeds < 1:
        p.error("--calibrate-seeds must be >= 1")
    if args.timeframe != "4h":
        p.error("the mandate is a 4H strategy: --timeframe must be 4h")


def _now_ms(text: str | None) -> int:
    if text is None:
        return time.time_ns() // 1_000_000_000 * 1000  # whole seconds
    return iso_to_ms(text)


def _print_summary(research: Research, report: Path) -> None:
    for name, r in research.candidates():
        s = r.test_summary
        print(f"{name}: {r.label} (TEST n={s.n}, avg {s.avg_r:+.3f}R)")
    print(verdict_line(research))
    print(f"invariant violations: {research.n_violations()}")
    print(f"report: {report}")


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    _check_args(parser, args)
    out_dir = Path(args.out_dir)
    if args.calibrate_seeds is not None:
        rows = calibrate(args.synthetic, args.calibrate_seeds, args.years, args.workers)
        path = write_calibration(args.synthetic, args.years, rows, out_dir)
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
    research = run_pipeline(ds.data, ds.events)
    report = write_outputs(ds, research, out_dir, now_ms)
    _print_summary(research, report)
    return 1 if research.n_violations() else 0


if __name__ == "__main__":
    raise SystemExit(main())
