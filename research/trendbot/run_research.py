"""One command: 70/30 walk-forward research report, trade journals and human review packs.

Usage (from the repo root)::

    # real candles (downloaded on a networked machine with fetch_data + ccxt)
    python -m research.trendbot.run_research --data-dir research/data \
        [--events events.csv] [--pairs BTC/USDT ETH/USDT BNB/USDT] --out-dir research/results/real \
        [--fee-rate 0.001] [--slippage-pct 0.05] [--exchange-id binance] [--now ISO]
    # synthetic world with KNOWN ground truth (verification of the methodology only)
    python -m research.trendbot.run_research --synthetic planted --seed 1 [--years 6] \
        --out-dir research/results/synthetic_planted_s1
    # calibration: label frequencies over seeds 1..N of a synthetic world
    python -m research.trendbot.run_research --synthetic null --calibrate-seeds 20 \
        --out-dir research/results/calibration_null [--workers 4] [--effect-strength X]
    # power curve: seeds 1..N at several planted effect strengths (sigma units)
    python -m research.trendbot.run_research --synthetic planted --calibrate-seeds 20 \
        --power-strengths 0.3 0.45 0.6 0.8 1.0 --out-dir research/results/power_planted

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
3. ML layer ``base+ml`` (``ml_filter``): a logistic filter on the baseline rules, fitted on
   purged TRAIN candidates only and applied unchanged to TEST (``UNTESTED`` if it cannot be
   fitted);
4. expectancy guard ``base+guard``: the baseline with ``expectancy_guard=True``;
5. ``invariants.check_invariants`` on every backtest; ``journal_rules.audit`` of the TRAIN
   journal at the split and of the TEST journal at the end of TEST; TRAIN and TEST journals
   plus a human review pack for each pre-registered candidate; an adoption record per
   adoptable candidate (stage reached: WALK_FORWARD) bound to its evidence (CONTRACT.md v3
   C3: provenance, data file hashes, report / journal / review pack hashes, label
   parameters), the ML one carrying the fitted model's fingerprint next to its
   ``MLFilter.to_json()`` file; ``run.log`` (the exact command and the console summary).

The verdict only considers the m = 4 PRE-REGISTERED candidates (baseline, TRAIN-selected
variant, ML layer, expectancy guard), each labelled with the multiplicity-corrected rule of
``metrics.label`` (C4). Picking any other grid variant because of its TEST numbers would be
selection on TEST, so their labels are context only. Rendering lives in ``report.py``.

``REPORT.md`` sections, in order: (1) no win rate is promised or targeted, (2) data
provenance and costs, (3) baseline TRAIN | TEST, (4) discovery TRAIN | TEST, (5) entry layers
(ML filter, expectancy guard), (6) verdict, (7) decision counts per rule, (8) invariant audit,
(9) journal rules at the end of TRAIN and of TEST, (10) journals and review packs, (11)
adoption path, (12) risk disclaimer.

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
import shlex
import sys
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field, fields
from pathlib import Path

from . import report
from .adoption import (
    HUMAN_REVIEW,
    WALK_FORWARD,
    AdoptionRecord,
    BacktestStage,
    FileRef,
    HumanReviewStage,
    WalkForwardStage,
    check_promotion,
    config_fingerprint,
    load_config,
    save_record,
    sha256_file,
)
from .config import ConfigError, StrategyConfig
from .data import load_dataset, pair_filename
from .invariants import check_invariants
from .journal import iso_to_ms, ms_to_iso, write_journal
from .ml_filter import MLFilter, make_factory
from .models import Candle, NewsEvent
from .news import load_events
from .report import NO_ROBUST, fitted_model, journal_paths, rel, renumbered_test, safe_name
from .review_sheet import write_review_pack
from .strategy_discovery import DiscoveryResult, discover
from .synthetic import WORLDS, generate_world
from .walkforward import (
    BASE,
    DEFAULT_MAX_DD_PCT,
    DEFAULT_STATS,
    GUARD_LAYER,
    GUARD_VARIANT,
    MIN_TEST,
    MIN_TRAIN,
    ML_LAYER,
    ML_VARIANT,
    TRAIN_FRAC,
    LayerDiff,
    Stats,
    WalkForwardResult,
    layer_diffs,
    split_ts,
    walk_forward,
)


__all__ = ["NO_ROBUST", "Research", "main", "run_pipeline", "verdict_line"]

PROG = "python -m research.trendbot.run_research"
PAIRS = ("BTC/USDT", "ETH/USDT", "BNB/USDT")
REPORT_NAME = "REPORT.md"
CALIBRATION_NAME = "CALIBRATION.md"
CALIBRATION_CSV = "calibration_runs.csv"
POWER_NAME = "POWER.md"
POWER_CSV = "power_runs.csv"
RUN_LOG = "run.log"
PROVENANCE_REAL = "real"

verdict_line = report.verdict_line
SECTION_TITLES = report.SECTION_TITLES

# ---------------------------------------------------------------------------- costs
DEFAULT_CFG = StrategyConfig()
# CLI sanity ceilings on top of StrategyConfig's floors: a larger value is almost surely a
# percent typed where a fraction was expected (--fee-rate 0.6 meaning 0.6%).
MAX_FEE_RATE = 0.02
MAX_SLIPPAGE_PCT = 5.0
_EXCHANGE_ID_RE = re.compile(r"[a-z][a-z0-9_]*")
COST_FLAGS = ("fee_rate", "slippage_pct", "exchange_id")  # the StrategyConfig fields they set


# ---------------------------------------------------------------------------- datasets
@dataclass
class Dataset:
    data: dict[str, list[Candle]]
    events: list[NewsEvent]
    kind: str  # "synthetic" | "files"
    provenance: list[str]  # markdown lines for section 2
    world: str | None = None
    seed: int | None = None
    provenance_id: str = PROVENANCE_REAL  # "real" or "synthetic:<world>:<seed>" (C3)
    data_files: list[Path] = field(default_factory=list)  # candle files (real data only)
    events_path: Path | None = None  # news calendar file, if one was loaded


def _event_counts(events: Sequence[NewsEvent]) -> str:
    counts = Counter(f"{e.impact} {e.kind}" for e in events)
    return ", ".join(f"{k} {counts[k]}" for k in sorted(counts)) or "none"


def load_synthetic(
    world: str,
    seed: int,
    years: float = 6.0,
    pairs: Sequence[str] = PAIRS,
    effect_strength: float | None = None,
) -> Dataset:
    """A synthetic world plus provenance lines that state its ground truth."""
    w = generate_world(world, seed, years, pairs, TRAIN_FRAC, effect_strength)
    truth, prediction = report.WORLD_TRUTH[world]
    first = next(iter(w.candles.values()))
    strength = "" if effect_strength is None else f", effect strength {w.effect_strength:g} sigma"
    lines = [
        f"- Source: **SYNTHETIC** world `{world}`, seed {seed}{strength}, {years:g} years of 4H "
        f"candles ({len(first)} per pair) from {ms_to_iso(first[0].ts)}, pairs "
        f"{', '.join(pairs)} (`synthetic.generate_world`); adoption provenance "
        f"`synthetic:{world}:{seed}`.",
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
    return Dataset(
        w.candles, list(w.events), "synthetic", lines, world, seed, f"synthetic:{world}:{seed}"
    )


def load_files(
    data_dir: str | Path,
    pairs: Sequence[str] = PAIRS,
    events_path: str | Path | None = None,
    timeframe: str = "4h",
) -> Dataset:
    """Real candle files (``data.load_dataset``) plus the optional news calendar CSV."""
    data = load_dataset(data_dir, pairs, timeframe)
    events = load_events(events_path) if events_path else []
    files = [Path(data_dir) / pair_filename(p, timeframe) for p in pairs]
    lines = [
        f"- Source: candle CSV files in `{data_dir}` ({timeframe}), loaded with "
        "`data.load_dataset` (strict validation: sorted, no duplicates, sane OHLC). "
        "Per-pair coverage and gaps are in the table below. Each file's sha256 is bound into "
        "the adoption records (`data_files`, provenance `real`).",
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
    ev_path = Path(events_path) if events_path else None
    return Dataset(data, events, "files", lines, data_files=files, events_path=ev_path)


# ---------------------------------------------------------------------------- pipeline
@dataclass
class Research:
    cfg: StrategyConfig
    split: int
    base: WalkForwardResult
    discovery: DiscoveryResult
    ml: WalkForwardResult
    guard: WalkForwardResult
    violations: dict[str, tuple[list[str], list[str]]]  # variant -> (TRAIN, TEST)

    def all_results(self) -> list[WalkForwardResult]:
        return [self.base, *self.discovery.results, self.ml, self.guard]

    def candidates(self) -> list[tuple[str, WalkForwardResult]]:
        """The m = 4 PRE-REGISTERED candidates: baseline, TRAIN-selected, ML layer, guard."""
        out = [(f"baseline `{BASE}`", self.base)]
        sel = self.discovery.selected()
        if sel is not None:
            out.append((f"discovery-selected `{sel.variant}`", sel))
        out.append((f"ML layer `{ML_VARIANT}`", self.ml))
        out.append((f"expectancy guard `{GUARD_VARIANT}`", self.guard))
        return out

    def robust(self) -> list[tuple[str, WalkForwardResult]]:
        return [(name, r) for name, r in self.candidates() if r.label == "ROBUST"]

    def n_violations(self) -> int:
        return sum(len(a) + len(b) for a, b in self.violations.values())

    def layer_diffs(self) -> dict[str, list[LayerDiff]]:
        """C1 counts per window for each layer variant versus the base journal."""
        return {
            ML_VARIANT: layer_diffs(self.base, self.ml, ML_LAYER),
            GUARD_VARIANT: layer_diffs(self.base, self.guard, GUARD_LAYER),
        }


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
    stats: Stats = DEFAULT_STATS,
) -> Research:
    """Baseline, discovery, ML and guard walk-forwards on one shared split, plus audits."""
    cfg = cfg or StrategyConfig()
    evs = list(events)
    split = split_ts(data, TRAIN_FRAC, cfg.timeframe_ms)
    common = {"split": split, "min_train": min_train, "min_test": min_test, "stats": stats}
    base = walk_forward(data, cfg, evs, TRAIN_FRAC, None, BASE, max_dd_pct, **common)  # type: ignore[arg-type]
    disc = discover(
        data,
        cfg,
        evs,
        TRAIN_FRAC,
        min_train,
        max_dd_pct=max_dd_pct,
        min_test=min_test,
        split=split,
        stats=stats,
    )
    ml = walk_forward(data, cfg, evs, TRAIN_FRAC, make_factory(), ML_VARIANT, max_dd_pct, **common)  # type: ignore[arg-type]
    guard_cfg = cfg.with_changes(expectancy_guard=True)
    guard = walk_forward(
        data, guard_cfg, evs, TRAIN_FRAC, None, GUARD_VARIANT, max_dd_pct, **common
    )  # type: ignore[arg-type]
    research = Research(cfg, split, base, disc, ml, guard, {})
    for r in research.all_results():
        research.violations[r.variant] = _audit(r, data, evs)
    return research


# ---------------------------------------------------------------------------- outputs
def output_targets(research: Research) -> list[tuple[str, WalkForwardResult]]:
    """The pre-registered candidates that ran: the only variants with journals and packs."""
    return [(name, r) for name, r in research.candidates() if r.ran]


def write_journals(research: Research, out_dir: Path) -> dict[str, tuple[Path, Path]]:
    out: dict[str, tuple[Path, Path]] = {}
    for _name, r in output_targets(research):
        train_path, test_path = journal_paths(out_dir, r.variant)
        train_path.parent.mkdir(parents=True, exist_ok=True)
        write_journal(r.train.trades, train_path)
        write_journal(renumbered_test(r), test_path)
        out[r.variant] = (train_path, test_path)
    return out


def review_folder(research: Research, r: WalkForwardResult) -> str:
    if r is research.base or r is research.ml or r is research.guard:
        return f"review_{safe_name(r.variant)}"
    return f"review_selected_{safe_name(r.variant)}"


def write_reviews(research: Research, out_dir: Path) -> dict[str, dict[str, Path]]:
    out: dict[str, dict[str, Path]] = {}
    for name, r in output_targets(research):
        trades = [*r.train.trades, *renumbered_test(r)]
        decisions = Counter(r.train.decisions) + Counter(r.test.decisions)
        out[r.variant] = write_review_pack(
            trades,
            out_dir / review_folder(research, r),
            f"Trade review: {name}",
            r.cfg or DEFAULT_CFG,
            split_ts=r.split_ts,
            decision_counts=decisions,
        )
    return out


@dataclass
class AdoptionStatus:
    variant: str
    path: Path
    result: WalkForwardResult
    provenance: str
    checks: dict[str, list[str]]  # stage -> "[rule] reason" per blocking decision
    record: AdoptionRecord
    config_path: Path | None = None  # overrides JSON for `adoption check --config`
    model_path: Path | None = None  # MLFilter.to_json() file of the ML variant
    model_fingerprint: str | None = None

    @property
    def blocking(self) -> list[str]:
        """Blocking reasons for promotion to HUMAN_REVIEW (empty = allowed)."""
        return self.checks[HUMAN_REVIEW]


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


def adoption_targets(research: Research) -> list[WalkForwardResult]:
    """Baseline, TRAIN-selected variant, the ML layer (if it was fitted) and the guard."""
    targets = [research.base]
    sel = research.discovery.selected()
    if sel is not None:
        targets.append(sel)
    if research.ml.ran and fitted_model(research.ml) is not None:
        targets.append(research.ml)
    targets.append(research.guard)
    return targets


def _write_config(cfg: StrategyConfig, out_dir: Path, variant: str) -> Path | None:
    overrides = config_overrides(cfg)
    if not overrides:
        return None
    path = out_dir / f"config_{safe_name(variant)}.json"
    path.write_text(json.dumps(overrides, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _write_model(ml: MLFilter, out_dir: Path, variant: str) -> Path:
    """``MLFilter.to_json()`` next to the ML adoption record (the live bot's model file)."""
    path = out_dir / f"model_{safe_name(variant)}.json"
    path.write_text(ml.to_json(), encoding="utf-8")
    return path


def _relpath(path: Path, out_dir: Path) -> str:
    """``path`` relative to the record's directory (``out_dir``), else absolute."""
    try:
        return Path(os.path.relpath(path.resolve(), out_dir.resolve())).as_posix()
    except ValueError:  # e.g. another drive on Windows
        return path.resolve().as_posix()


def _data_evidence(ds: Dataset, out_dir: Path) -> tuple[dict[str, str] | None, FileRef | None]:
    files = {_relpath(p, out_dir): sha256_file(p) for p in ds.data_files} or None
    events = None
    if ds.events_path is not None:
        events = FileRef(_relpath(ds.events_path, out_dir), sha256_file(ds.events_path))
    return files, events


@dataclass
class _Target:
    result: WalkForwardResult
    config_path: Path | None
    model_path: Path | None
    model_fingerprint: str | None


def build_record(
    t: _Target,
    ds: Dataset,
    out_dir: Path,
    now_ms: int,
    journals: Mapping[str, tuple[Path, Path]],
    reviews: Mapping[str, Mapping[str, Path]],
    report_sha256: str,
) -> AdoptionRecord:
    """The adoption record of one candidate, bound to its evidence (CONTRACT.md v3 C3)."""
    r = t.result
    cfg = r.cfg or DEFAULT_CFG
    train_path, test_path = journals[r.variant]
    review_csv = reviews[r.variant]["csv"]
    data_files, events_file = _data_evidence(ds, out_dir)
    return AdoptionRecord(
        variant=r.variant,
        config_fingerprint=config_fingerprint(cfg),
        model_fingerprint=t.model_fingerprint,
        provenance=ds.provenance_id,
        data_files=data_files,
        events_file=events_file,
        backtest=BacktestStage(
            report_path=REPORT_NAME,
            completed_utc=ms_to_iso(now_ms),
            report_sha256=report_sha256,
        ),
        walk_forward=WalkForwardStage(
            label=r.label,
            dd_ok=r.dd_ok,
            split_utc=ms_to_iso(r.split_ts),
            train_n=r.train_summary.n,
            test_n=r.test_summary.n,
            test_avg_r=r.test_summary.avg_r,
            report_path=REPORT_NAME,
            train_journal_path=rel(train_path, out_dir),
            train_journal_sha256=sha256_file(train_path),
            test_journal_path=rel(test_path, out_dir),
            test_journal_sha256=sha256_file(test_path),
            min_train=r.min_train,
            min_test=r.min_test,
            train_avg_r=r.train_summary.avg_r,
            label_params=r.label_params(),
        ),
        human_review=HumanReviewStage(
            review_path=rel(review_csv, out_dir), review_sha256=sha256_file(review_csv)
        ),
    )


def _prepare_targets(research: Research, out_dir: Path) -> list[_Target]:
    out = []
    for r in adoption_targets(research):
        ml = fitted_model(r)
        model_path = _write_model(ml, out_dir, r.variant) if ml is not None else None
        config_path = _write_config(r.cfg or DEFAULT_CFG, out_dir, r.variant)
        out.append(_Target(r, config_path, model_path, ml.fingerprint() if ml else None))
    return out


def record_and_check(
    targets: Sequence[_Target],
    ds: Dataset,
    out_dir: Path,
    now_ms: int,
    journals: Mapping[str, tuple[Path, Path]],
    reviews: Mapping[str, Mapping[str, Path]],
) -> list[AdoptionStatus]:
    """(Re)write every record with the CURRENT report hash, then run ``check_promotion`` at
    WALK_FORWARD and HUMAN_REVIEW exactly as the CLI would (config from the overrides file,
    evidence resolved against ``out_dir``, the model fingerprint supplied)."""
    report_sha = sha256_file(out_dir / REPORT_NAME)
    out = []
    for t in targets:
        rec = build_record(t, ds, out_dir, now_ms, journals, reviews, report_sha)
        path = out_dir / f"adoption_{safe_name(t.result.variant)}.json"
        save_record(rec, path)
        cfg = load_config(t.config_path)
        checks = {
            stage: [
                f"[{d.rule}] {d.reason}"
                for d in check_promotion(rec, stage, cfg, now_ms, out_dir, t.model_fingerprint)
            ]
            for stage in (WALK_FORWARD, HUMAN_REVIEW)
        }
        out.append(
            AdoptionStatus(
                t.result.variant,
                path,
                t.result,
                ds.provenance_id,
                checks,
                rec,
                t.config_path,
                t.model_path,
                t.model_fingerprint,
            )
        )
    return out


def write_outputs(
    ds: Dataset, research: Research, out_dir: Path, now_ms: int
) -> tuple[Path, list[AdoptionStatus]]:
    """Journals, review packs, model/config files, adoption records and ``REPORT.md``.

    Returns the report path and the adoption status of every record written.

    The records bind the report's sha256 and the report shows the records' check status,
    so: a provisional report is written, the records are checked against it, the final
    report (with that status) is written, and the records are rewritten with the final
    report's hash and re-checked. The two checks must agree (``RuntimeError`` otherwise).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    journals = write_journals(research, out_dir)
    reviews = write_reviews(research, out_dir)
    path = out_dir / REPORT_NAME
    render = report.render_report
    path.write_text(render(ds, research, out_dir, journals, reviews, None), encoding="utf-8")
    targets = _prepare_targets(research, out_dir)
    statuses = record_and_check(targets, ds, out_dir, now_ms, journals, reviews)
    path.write_text(render(ds, research, out_dir, journals, reviews, statuses), encoding="utf-8")
    final = record_and_check(targets, ds, out_dir, now_ms, journals, reviews)
    if [s.checks for s in final] != [s.checks for s in statuses]:
        raise RuntimeError("adoption check status changed after the final report was written")
    return path, final


# ---------------------------------------------------------------------------- calibration
_CAND_FIELDS = (
    "label",
    "train_n",
    "train_avg_r",
    "test_n",
    "test_avg_r",
    "test_total_r",
    "test_iid_lb",
    "test_block_lb",
    "test_adj_lb",
    "reached_gate",
    "dd_ok",
    "test_mtm_dd_pct",
    "test_realised_dd_pct",
    "dd_limit_pct",
)
_DIFF_FIELDS = ("vetoed", "reduced", "base_only", "layer_only")
CAL_FIELDS: tuple[str, ...] = (
    "world",
    "effect_strength",
    "seed",
    "selected",
    *(f"{key}_{f}" for key, _ in report.CANDIDATES for f in _CAND_FIELDS),
    *(f"{lay}_{w}_{f}" for lay in ("ml", "guard") for w in ("train", "test") for f in _DIFF_FIELDS),
    "ml_fit_error",
    "ml_hour_sin",
    "ml_hour_cos",
    "ml_top_feature",
    "ml_fingerprint",
    "verdict_robust",
    "violations",
)


def _cand_fields(key: str, r: WalkForwardResult | None) -> dict[str, object]:
    if r is None:
        return {f"{key}_{f}": ("NONE" if f == "label" else "") for f in _CAND_FIELDS}
    if not r.ran:
        blank = {f"{key}_{f}": "" for f in _CAND_FIELDS}
        return {**blank, f"{key}_label": r.label, f"{key}_reached_gate": False}
    a, b = r.train_summary, r.test_summary
    values: dict[str, object] = {
        "label": r.label,
        "train_n": a.n,
        "train_avg_r": round(a.avg_r, 4),
        "test_n": b.n,
        "test_avg_r": round(b.avg_r, 4),
        "test_total_r": round(b.total_r, 4),
        "test_iid_lb": round(b.iid_lb, 4),
        "test_block_lb": round(b.block_lb, 4),
        "test_adj_lb": round(b.adj_lb, 4),
        "reached_gate": r.reached_test_gate,
        "dd_ok": r.dd_ok,
        "test_mtm_dd_pct": round(r.test_mtm_dd_pct, 3),
        "test_realised_dd_pct": round(b.max_dd_pct, 3),
        "dd_limit_pct": round(r.dd_limit_pct, 3),
    }
    return {f"{key}_{k}": v for k, v in values.items()}


def _diff_fields(research: Research) -> dict[str, object]:
    out: dict[str, object] = {}
    for variant, lay in ((ML_VARIANT, "ml"), (GUARD_VARIANT, "guard")):
        diffs = {d.window.lower(): d for d in research.layer_diffs()[variant]}
        for w in ("train", "test"):
            d = diffs.get(w)
            vals = (
                ("", "", "", "")
                if d is None
                else (d.vetoed, d.reduced_risk, d.base_only, d.layer_only)
            )
            out.update({f"{lay}_{w}_{f}": v for f, v in zip(_DIFF_FIELDS, vals, strict=True)})
    return out


def _ml_cal_fields(m: WalkForwardResult) -> dict[str, object]:
    ml = fitted_model(m) if m.ran else None
    base = {"ml_fit_error": m.fit_error or ""}
    if ml is None:
        empty = {k: "" for k in ("ml_hour_sin", "ml_hour_cos", "ml_top_feature", "ml_fingerprint")}
        return {**base, **empty}
    coefs = dict(ml.explain())
    return {
        **base,
        "ml_hour_sin": round(coefs["hour_sin"], 4),
        "ml_hour_cos": round(coefs["hour_cos"], 4),
        "ml_top_feature": max(coefs, key=lambda k: abs(coefs[k])),
        "ml_fingerprint": ml.fingerprint(),
    }


def cal_row(
    seed: int, research: Research, world: str = "", effect_strength: float | None = None
) -> dict[str, object]:
    sel = research.discovery.selected()
    row: dict[str, object] = {
        "world": world,
        "effect_strength": "" if effect_strength is None else effect_strength,
        "seed": seed,
        "selected": sel.variant if sel else "",
        **_cand_fields("base", research.base),
        **_cand_fields("selected", sel),
        **_cand_fields("ml", research.ml),
        **_cand_fields("guard", research.guard),
        **_diff_fields(research),
        **_ml_cal_fields(research.ml),
        "verdict_robust": bool(research.robust()),
        "violations": research.n_violations(),
    }
    return {k: row[k] for k in CAL_FIELDS}


CalJob = tuple[str, int, float, tuple[tuple[str, object], ...], float | None]


def cost_overrides(cfg: StrategyConfig | None) -> tuple[tuple[str, object], ...]:
    """The cost fields of ``cfg`` that differ from the defaults (picklable, for workers)."""
    if cfg is None:
        return ()
    return tuple(
        (k, getattr(cfg, k)) for k in COST_FLAGS if getattr(cfg, k) != getattr(DEFAULT_CFG, k)
    )


def calibrate_one(job: CalJob) -> dict[str, object]:
    """One calibration seed (top-level so a process pool can pickle it)."""
    world, seed, years, overrides, strength = job
    cfg = DEFAULT_CFG.with_changes(**dict(overrides))
    ds = load_synthetic(world, seed, years, effect_strength=strength)
    return cal_row(seed, run_pipeline(ds.data, ds.events, cfg), world, strength)


def _run_jobs(jobs: Sequence[CalJob], workers: int) -> list[dict[str, object]]:
    if workers <= 1:
        return [calibrate_one(j) for j in jobs]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(calibrate_one, jobs))


def calibrate(
    world: str,
    n_seeds: int,
    years: float,
    workers: int,
    cfg: StrategyConfig | None = None,
    effect_strength: float | None = None,
) -> list[dict[str, object]]:
    overrides = cost_overrides(cfg)
    jobs = [(world, s, years, overrides, effect_strength) for s in range(1, n_seeds + 1)]
    return _run_jobs(jobs, workers)


def power_curve(
    world: str,
    strengths: Sequence[float],
    n_seeds: int,
    years: float,
    workers: int,
    cfg: StrategyConfig | None = None,
) -> dict[float, list[dict[str, object]]]:
    """Seeds 1..n_seeds at every strength (one process pool for all jobs)."""
    overrides = cost_overrides(cfg)
    jobs = [(world, s, years, overrides, x) for x in strengths for s in range(1, n_seeds + 1)]
    rows = _run_jobs(jobs, workers)
    return {x: [r for r in rows if r["effect_strength"] == x] for x in strengths}


def _write_rows(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CAL_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_calibration(
    world: str,
    years: float,
    rows: Sequence[Mapping[str, object]],
    out_dir: Path,
    cfg: StrategyConfig | None = None,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_rows(out_dir / CALIBRATION_CSV, rows)
    path = out_dir / CALIBRATION_NAME
    path.write_text(report.render_calibration(world, years, rows, cfg), encoding="utf-8")
    return path


def write_power(
    world: str,
    years: float,
    by_strength: Mapping[float, Sequence[Mapping[str, object]]],
    out_dir: Path,
    cfg: StrategyConfig | None = None,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_rows(out_dir / POWER_CSV, [r for x in sorted(by_strength) for r in by_strength[x]])
    path = out_dir / POWER_NAME
    path.write_text(report.render_power(world, years, by_strength, cfg), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------- CLI
def _h(text: str) -> str:
    """argparse help text with every literal ``%`` escaped (argparse %-formats help)."""
    return text.replace("%", "%%")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog=PROG,
        description=_h("70/30 walk-forward research report (expectancy, never win rate)."),
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--data-dir", help=_h("directory of <PAIR>-<tf>.csv candle files"))
    src.add_argument("--synthetic", choices=WORLDS, help=_h("synthetic world with known truth"))
    p.add_argument("--events", help=_h("news calendar CSV (with --data-dir)"))
    p.add_argument("--pairs", nargs="+", default=list(PAIRS), help=_h("pairs to trade"))
    p.add_argument("--timeframe", default="4h", help=_h("candle timeframe of the files (4h)"))
    p.add_argument("--seed", type=int, default=1, help=_h("synthetic seed (default 1)"))
    p.add_argument("--years", type=float, default=6.0, help=_h("synthetic years (default 6)"))
    p.add_argument("--out-dir", required=True, help=_h("output directory"))
    p.add_argument(
        "--calibrate-seeds",
        type=int,
        default=None,
        help=_h("run seeds 1..N, write CALIBRATION.md (or POWER.md with --power-strengths)"),
    )
    p.add_argument(
        "--effect-strength",
        type=float,
        default=None,
        help=_h("planted drift in sigma units per candle for a calibration (world default)"),
    )
    p.add_argument(
        "--power-strengths",
        type=float,
        nargs="+",
        default=None,
        help=_h("power curve: run --calibrate-seeds seeds at each of these effect strengths"),
    )
    p.add_argument(
        "--workers",
        type=int,
        default=min(4, os.cpu_count() or 1),
        help=_h("calibration processes"),
    )
    p.add_argument("--now", default=None, help=_h("ISO-8601 time stamped on the adoption records"))
    p.add_argument(
        "--fee-rate",
        type=float,
        default=None,
        help=_h(
            f"fee per side as a FRACTION of notional (default {DEFAULT_CFG.fee_rate:g} = "
            f"{DEFAULT_CFG.fee_rate:.2%}, Binance spot taker; at least 0.0005). Coinbase "
            "Advanced Trade taker fees at low tiers are several times Binance's: pass the real "
            "tier, e.g. 0.006 for 0.6%"
        ),
    )
    p.add_argument(
        "--slippage-pct",
        type=float,
        default=None,
        help=_h(
            f"adverse slippage on market fills in PERCENT (default {DEFAULT_CFG.slippage_pct:g}"
            "%; at least 0.01%)"
        ),
    )
    p.add_argument(
        "--exchange-id",
        default=None,
        help=_h(
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
    strengths = args.power_strengths
    if (strengths is not None or args.effect_strength is not None) and args.calibrate_seeds is None:
        p.error("--power-strengths / --effect-strength need --calibrate-seeds N")
    if strengths is not None and args.effect_strength is not None:
        p.error("use either --power-strengths or --effect-strength, not both")
    if strengths is not None and (len(set(strengths)) != len(strengths) or len(strengths) < 2):
        p.error("--power-strengths needs at least two distinct strengths")
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


class _Console:
    """Prints each line and keeps it for ``run.log``."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def __call__(self, text: str) -> None:
        print(text)
        self.lines.append(text)


def write_run_log(out_dir: Path, argv: Sequence[str], lines: Sequence[str], code: int) -> Path:
    """``run.log``: the exact command (argv), the console summary and the exit code."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / RUN_LOG
    text = [f"$ {PROG} {shlex.join(argv)}", *lines, f"exit code: {code}"]
    path.write_text("\n".join(text) + "\n", encoding="utf-8")
    return path


def summary_lines(research: Research, report_path: Path, statuses: Sequence[str] = ()) -> list[str]:
    return [
        cost_summary(research.cfg),
        *report.console_lines(research),
        verdict_line(research),
        f"invariant violations: {research.n_violations()}",
        *statuses,
        f"report: {report_path}",
    ]


def status_lines(statuses: Sequence[AdoptionStatus]) -> list[str]:
    """One console line per adoption record: its check status at both stages."""
    return [
        f"adoption {st.path.name} ({st.provenance}, label {st.result.label}): check --stage "
        f"{WALK_FORWARD} {report.check_cell(st.checks[WALK_FORWARD])}; --stage {HUMAN_REVIEW} "
        f"{report.check_cell(st.checks[HUMAN_REVIEW])}"
        for st in statuses
    ]


def _calibration_main(args: argparse.Namespace, cfg: StrategyConfig, log: _Console) -> int:
    out_dir = Path(args.out_dir)
    log(cost_summary(cfg))
    if args.power_strengths is not None:
        by = power_curve(
            args.synthetic,
            args.power_strengths,
            args.calibrate_seeds,
            args.years,
            args.workers,
            cfg,
        )
        path = write_power(args.synthetic, args.years, by, out_dir, cfg)
        for line in report.power_table(by)[2:]:
            log(line)
        rows = [r for v in by.values() for r in v]
        log(
            f"power curve of {args.synthetic} over {len(by)} strengths x "
            f"{args.calibrate_seeds} seeds -> {path}"
        )
    else:
        rows = calibrate(
            args.synthetic,
            args.calibrate_seeds,
            args.years,
            args.workers,
            cfg,
            args.effect_strength,
        )
        path = write_calibration(args.synthetic, args.years, rows, out_dir, cfg)
        for line in report.calibration_console_lines(rows):
            log(line)
        for line in report.world_findings(args.synthetic, rows)[:1]:
            log(line)
        log(f"calibration of {args.synthetic} over {len(rows)} seeds -> {path}")
    return 1 if any(int(r["violations"]) for r in rows) else 0  # type: ignore[call-overload]


def main(argv: list[str] | None = None) -> int:
    argv_list = list(sys.argv[1:] if argv is None else argv)
    parser = _parser()
    args = parser.parse_args(argv_list)
    cfg = _check_args(parser, args)
    out_dir = Path(args.out_dir)
    log = _Console()
    if args.calibrate_seeds is not None:
        code = _calibration_main(args, cfg, log)
        write_run_log(out_dir, argv_list, log.lines, code)
        return code
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
    path, statuses = write_outputs(ds, research, out_dir, now_ms)
    for line in summary_lines(research, path, status_lines(statuses)):
        log(line)
    code = 1 if research.n_violations() else 0
    write_run_log(out_dir, argv_list, log.lines, code)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
