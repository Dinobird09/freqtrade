"""Markdown rendering of REPORT.md (the 12 sections of one research run).

Split out of ``run_research`` (which runs the pipeline, writes the evidence files and owns
the CLI). Nothing here runs a backtest: every function formats results that were already
computed, plus the journal-rule audit that section 9 shows. The module is split along its
natural seams:

- ``report_tables``: shared wording (ground truth, win-rate statement, layer and D8 gate
  wording, the C5/D7 drawdown rule with its rationale, the C4 multiplicity rule, costs,
  adoption path, disclaimer), the TRAIN | TEST metric table and formatting helpers;
- ``report_calibration``: CALIBRATION.md and POWER.md;
- ``report_evidence``: REPORT.md sections 7-12 (decision counts, invariant audit, journal
  rules, journal / review-pack links, adoption records, disclaimer);
- this module: REPORT.md sections 1-6 (win-rate statement, provenance, baseline, discovery,
  layers, verdict with the D3 holdout ledger and the D4 stress comparison), the document
  assembly, the console summary, and re-exports of the other three so ``report.<name>``
  keeps working for every caller.

Conventions every rendered table follows: each header names its TRAIN and TEST columns (or a
TRAIN/TEST window column), win rate only ever appears labelled "context only", and the
statistics are the ones of CONTRACT.md v3/v4: expectancy with multiplicity-adjusted iid and
calendar-month block bootstrap lower bounds (C4), realised and mark-to-market drawdowns
with the TRAIN-bootstrap limit capped at 15 % (C5/D7), layer vetoes counted in both
directions (C1), a fitted layer judged on its out-of-sample TRAIN gate (D8), and the holdout
ledger's distinct-look counts (D3).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from .adoption import HUMAN_REVIEW
from .data import gap_report
from .journal import ms_to_iso
from .metrics import ROBUST_MIN_N
from .ml_filter import FEATURES
from .models import Candle
from .report_calibration import (
    CANDIDATES,
    HOUR_TERMS,
    OBS,
    PER_SEED_HEADER,
    SEED_NOTE,
    WILSON_Z90,
    calibration_console_lines,
    candidate_rate_table,
    count,
    label_frequency_table,
    mde,
    mean_ci,
    mean_text,
    ml_hour_line,
    per_seed_lines,
    power_points,
    power_table,
    rate,
    render_calibration,
    render_power,
    seed_range,
    wilson,
    world_findings,
)
from .report_evidence import (
    AUDIT_HEADER,
    MAX_VIOLATIONS_SHOWN,
    adoption_table,
    check_command,
    journal_audits,
    n_backtests,
    section_adoption,
    section_decisions,
    section_disclaimer,
    section_invariants,
    section_journal_rules,
    section_links,
)
from .report_tables import (
    ADOPTION_PATH_LINES,
    COINBASE_FEE_NOTE,
    DD_RULE,
    DD_RULE_SHORT,
    DEFAULT_CFG,
    DISCLAIMER,
    GATE_WORDING,
    LAYER_WORDING,
    SECTION_TITLES,
    WIN_RATE_STATEMENT,
    WORLD_TRUTH,
    Column,
    block_ci_text,
    cell,
    check_cell,
    ci_text,
    columns,
    cost_lines,
    dd_rule_line,
    exits_text,
    fitted_model,
    insample_journal_path,
    journal_paths,
    label_lines,
    lbs_text,
    metric_table,
    multiplicity_rule,
    pct,
    pf_text,
    rel,
    renumbered_test,
    safe_name,
    section_heading,
    sr,
    stress_line,
)
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
    from .ledger import HoldoutStatus
    from .run_research import AdoptionStatus, Dataset, Research


__all__ = [
    "ADOPTION_PATH_LINES",
    "AUDIT_HEADER",
    "CANDIDATES",
    "COINBASE_FEE_NOTE",
    "DD_RULE",
    "DD_RULE_SHORT",
    "DEFAULT_CFG",
    "DISCLAIMER",
    "GATE_WORDING",
    "HOUR_TERMS",
    "LAYER_DIFF_HEADER",
    "LAYER_WORDING",
    "MAX_VIOLATIONS_SHOWN",
    "NO_ROBUST",
    "OBS",
    "PER_SEED_HEADER",
    "SECTION_TITLES",
    "SEED_NOTE",
    "WILSON_Z90",
    "WIN_RATE_STATEMENT",
    "WORLD_TRUTH",
    "Column",
    "adoption_table",
    "block_ci_text",
    "calibration_console_lines",
    "candidate_rate_table",
    "candidate_table",
    "candle_table",
    "cell",
    "check_cell",
    "check_command",
    "ci_text",
    "columns",
    "console_lines",
    "cost_lines",
    "count",
    "dd_rule_line",
    "discovery_label",
    "exits_text",
    "fitted_model",
    "holdout_lines",
    "insample_journal_path",
    "journal_audits",
    "journal_paths",
    "label_frequency_table",
    "label_lines",
    "layer_diff_rows",
    "lbs_text",
    "mde",
    "mean_ci",
    "mean_text",
    "metric_table",
    "ml_hour_line",
    "multiplicity_rule",
    "n_backtests",
    "pct",
    "per_seed_lines",
    "pf_text",
    "power_points",
    "power_table",
    "rate",
    "rel",
    "render_calibration",
    "render_power",
    "render_report",
    "renumbered_test",
    "safe_name",
    "section_adoption",
    "section_baseline",
    "section_decisions",
    "section_disclaimer",
    "section_discovery",
    "section_heading",
    "section_invariants",
    "section_journal_rules",
    "section_layers",
    "section_links",
    "section_provenance",
    "section_verdict",
    "section_win_rate",
    "seed_range",
    "sr",
    "stress_comparison_table",
    "stress_line",
    "verdict_line",
    "wilson",
    "world_findings",
]
NO_ROBUST = "No robust result found."


def _window(r: WalkForwardResult, train: bool, end_ts: int) -> str:
    if train:
        return f"start .. {ms_to_iso(r.split_ts)}"
    return f"{ms_to_iso(r.split_ts)} .. {ms_to_iso(end_ts)}"


# ---------------------------------------------------------------------------- sections 1-3
def section_win_rate() -> list[str]:
    return [*section_heading(1), WIN_RATE_STATEMENT]


def _gap_text(candles: Sequence[Candle], tf_ms: int) -> str:
    gaps = gap_report(candles, tf_ms)
    if not gaps:
        return "0"
    shown = "; ".join(f"{ms_to_iso(a)} -> {ms_to_iso(b)}" for a, b in gaps[:5])
    more = f" (+{len(gaps) - 5} more)" if len(gaps) > 5 else ""
    return f"{len(gaps)}: {shown}{more}"


def candle_table(data: Mapping[str, Sequence[Candle]], split: int, tf_ms: int) -> list[str]:
    """Per-pair candle counts on each side of the split (TRAIN = closed by the split), date
    range and gaps."""
    lines = [
        "| pair | candles | TRAIN candles (closed by the split) | TEST candles (from the split) "
        "| first candle (UTC) | last candle (UTC) | gaps |",
        "|---|---:|---:|---:|---|---|---|",
    ]
    for pair, candles in data.items():
        n_train = sum(1 for c in candles if c.ts + tf_ms <= split)
        n_test = sum(1 for c in candles if c.ts >= split)
        lines.append(
            f"| {pair} | {len(candles)} | {n_train} | {n_test} | "
            f"{ms_to_iso(candles[0].ts)} | {ms_to_iso(candles[-1].ts)} | "
            f"{_gap_text(candles, tf_ms)} |"
        )
    return lines


def section_provenance(ds: Dataset, research: Research, data_end: int) -> list[str]:
    base = research.base
    stats = base.stats
    return [
        *section_heading(2),
        *ds.provenance,
        f"- Walk-forward split: {ms_to_iso(research.split)} = 70% of the common time range of "
        f"all pairs (TRAIN = candles that CLOSED by it, TEST = signal candles at or after it, "
        f"up to {ms_to_iso(data_end)}). TEST starts with fresh equity and fresh circuit "
        "breakers; indicators warm up on earlier candles only.",
        f"- Statistics (CONTRACT.md v3 C4/C5, v4 D1/D7): {stats.n_boot} bootstrap resamples "
        f"(seed {stats.seed}) for the iid and the calendar-month block bootstrap; m = "
        f"{stats.m} pre-registered candidates, alpha {stats.alpha:g}. "
        + multiplicity_rule(stats.m, stats.alpha),
        f"- {DD_RULE}",
        "",
        *candle_table(ds.data, research.split, research.cfg.timeframe_ms),
        "",
        *cost_lines(research.cfg, [*base.train.trades, *base.test.trades]),
    ]


def section_baseline(research: Research, data_end: int) -> list[str]:
    r = research.base
    return [
        *section_heading(3),
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
        *section_heading(4),
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
        "would be selection on TEST. Context variants are not adoptable: they get no adoption "
        "record and no holdout-ledger line.",
        "",
        "| variant | status | TRAIN n | TRAIN avg R | TRAIN 90% CI | TRAIN t | TEST n | "
        "TEST avg R | TEST 90% CI | TEST adjusted LB | TEST max DD % realised / MTM | label |",
        "|---|---|---:|---:|---|---:|---:|---:|---|---:|---|---|",
    ]
    for r in disc.results:
        a, b = r.train_summary, r.test_summary
        lines.append(
            f"| `{r.variant}` | {_disc_status(r, research)} | {a.n} | {sr(a.avg_r)} | "
            f"{ci_text(a)} | {sr(a.t_stat, 2)} | {b.n} | {sr(b.avg_r)} | {ci_text(b)} | "
            f"{sr(b.adj_lb)} | {pct(b.max_dd_pct)} / {pct(r.test_mtm_dd_pct)} | "
            f"{discovery_label(r, research)} |"
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
def _gate_lines(r: WalkForwardResult) -> list[str]:
    """The D8 out-of-sample TRAIN gate of a fitted layer."""
    g = r.gate
    if g is None:
        return []
    if not g.ran:
        return [
            f"- **Out-of-sample TRAIN gate (CONTRACT v4 D8): not evaluable** ({g.fit_error}). "
            "With no out-of-sample TRAIN trades the label is UNTESTED; the in-sample full-TRAIN "
            "numbers are context only and cannot stand in for it."
        ]
    a = g.summary
    inner = getattr(g.entry_filter, "ml", None)
    fp = f"; inner model fingerprint `{inner.fingerprint()[:16]}`" if inner is not None else ""
    return [
        f"- **Out-of-sample TRAIN gate (CONTRACT v4 D8):** the {g.n_candidates} purged TRAIN "
        f"candidates were split 70/30 in time order at {ms_to_iso(g.inner_split_ts)}; the "
        f"inner model was fitted on {g.n_fit} candidates ({g.n_purged} purged: signalled "
        f"before the inner boundary but resolved after it){fp}, and backtested from "
        f"{ms_to_iso(g.inner_split_ts)} to the split: n={a.n}, avg {sr(a.avg_r)}R. That window "
        "is the TRAIN the label judges and the TRAIN journal the adoption record binds; the "
        f"in-sample full-TRAIN figure (n={r.train_summary.n}, avg {sr(r.train_summary.avg_r)}R) "
        "is context only.",
    ]


def _ml_model_lines(r: WalkForwardResult) -> list[str]:
    ml = fitted_model(r)
    if ml is None:
        return ["The fitted filter exposes no model details."]
    lines = [
        ml.describe(),
        "",
        f"- TRAIN candidates enumerated (purged: outcome resolved by the split): "
        f"{r.n_train_candidates}; used for the final fit: {ml.train_n} ({ml.train_skipped} "
        f"skipped for missing features); TRAIN candidate win share {ml.train_base_rate:.1%} "
        "(context only).",
        *_gate_lines(r),
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
        gate = ""
        if r.gate is not None:
            g = r.gate.summary
            gate = f"; out-of-sample gate {sr(g.avg_r)} (n {g.n})" if r.gate.ran else ""
        out.append(
            f"- `{r.variant}` vs `{BASE}`: TRAIN avg R {sr(b.train_summary.avg_r)} -> "
            f"{sr(r.train_summary.avg_r)} (n {b.train_summary.n} -> {r.train_summary.n}"
            f"{', in-sample' if r.train_in_sample else ''}{gate}) | TEST avg R "
            f"{sr(b.test_summary.avg_r)} -> {sr(r.test_summary.avg_r)} (n {b.test_summary.n} -> "
            f"{r.test_summary.n})."
        )
    return out


def section_layers(research: Research) -> list[str]:
    r, g, b = research.ml, research.guard, research.base
    g_cfg = g.cfg or DEFAULT_CFG
    lines = [
        *section_heading(5),
        f"Two pre-registered layers on the baseline rules. `{ML_VARIANT}` adds `{ML_LAYER}`, "
        f"a logistic regression on {len(FEATURES)} features ({', '.join(FEATURES)}), fitted "
        "ONLY on purged TRAIN candidates and applied unchanged to TEST, so its full-TRAIN "
        f"numbers are IN-SAMPLE. `{GUARD_VARIANT}` switches on `{GUARD_LAYER}` "
        f"(`expectancy_guard=True`: a pair's risk is multiplied by {g_cfg.guard_risk_mult:g} "
        f"while its last {g_cfg.guard_window} closed trades average below 0R); it is a fixed "
        "rule over the journal, nothing is fitted, and it needs the same walk-forward evidence "
        "as any other variant.",
        "",
        LAYER_WORDING,
        "",
        GATE_WORDING,
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
    shows each named candidate's judged TRAIN and TEST expectancy side by side."""
    robust = research.robust()
    if robust:
        parts = []
        for name, r in robust:
            a, s = r.label_train_summary, r.test_summary
            dd = "drawdown check passed" if r.dd_ok else "drawdown check FAILED"
            parts.append(
                f"{name} (TRAIN avg {sr(a.avg_r)}R, n={a.n} | TEST avg {sr(s.avg_r)}R, n={s.n}, "
                f"{s.lb_confidence:.2%} lower bound iid {sr(s.iid_lb)}R / block "
                f"{sr(s.block_lb)}R; {dd})"
            )
        return "ROBUST: " + "; ".join(parts) + "."
    parts = [
        f"{name} {r.label} (TRAIN {sr(r.label_train_summary.avg_r)}R "
        f"n={r.label_train_summary.n} | TEST {sr(r.test_summary.avg_r)}R n={r.test_summary.n})"
        for name, r in research.candidates()
    ]
    return f"{NO_ROBUST} Pre-registered candidates, TRAIN | TEST: " + "; ".join(parts) + "."


def candidate_table(research: Research) -> list[str]:
    lines = [
        "| candidate | TRAIN n (judged) | TRAIN avg R (judged) | TEST n | TEST avg R | TEST iid "
        "/ block LB (1 - alpha/m) | reached the TEST gate | label | TEST MTM max DD vs limit | "
        "dd_ok |",
        "|---|---:|---:|---:|---:|---|---|---|---|---|",
    ]
    for name, r in research.candidates():
        a, b = r.label_train_summary, r.test_summary
        gate = "yes" if r.reached_test_gate else "no"
        dd = f"{pct(r.test_mtm_dd_pct)} vs {pct(r.dd_limit_pct)}" if r.ran else "not run"
        lines.append(
            f"| {name} | {a.n} | {sr(a.avg_r)} | {b.n} | {sr(b.avg_r)} | {lbs_text(b)} | {gate} "
            f"| {r.label} | {dd} | {'yes' if r.dd_ok else 'no'} |"
        )
    return lines


def holdout_lines(holdout: HoldoutStatus | None, out_dir: Path | None = None) -> list[str]:
    """D3: where the ledger is, what this run appended, and the per-pair distinct looks."""
    if holdout is None:
        return ["Holdout ledger (CONTRACT v4 D3): not recorded for this report."]
    where = holdout.path.as_posix()
    if out_dir is not None:
        try:
            where = rel(holdout.path.resolve(), out_dir.resolve())
        except ValueError:
            pass
    lines = [
        f"Holdout ledger (CONTRACT v4 D3): `{where}` (append-only JSON lines). This run "
        f"appended {holdout.appended} line(s), one per adoptable pre-registered candidate that "
        "got a TEST backtest (context discovery variants and test-only configs are never "
        "recorded). A look is a DISTINCT (config fingerprint, model fingerprint), so re-running "
        "identical candidates is not a new look. `adoption check` blocks (`ADOPT_holdout`) from "
        f"{HUMAN_REVIEW} on when a pair's count exceeds m = {holdout.limit}. After a reviewer N "
        "or any other revision, a variant needs a TEST window starting at or after the latest "
        "test_end_ts of every earlier look; it is never re-run on the same TEST window.",
        "",
        "| pair | TRAIN window (UTC) | TEST window (UTC) | cumulative distinct TEST looks on "
        "this TEST window | limit m | status |",
        "|---|---|---|---:|---:|---|",
    ]
    for w in holdout.windows:
        n = holdout.counts.get(w.pair, 0)
        status = "within the limit" if n <= holdout.limit else "**OVER the limit (blocked)**"
        lines.append(
            f"| {w.pair} | start .. {ms_to_iso(w.test_start_ts)} | {ms_to_iso(w.test_start_ts)} "
            f".. {ms_to_iso(w.test_end_ts)} | {n} | {holdout.limit} | {status} |"
        )
    return lines


def _role_results(research: Research) -> dict[str, WalkForwardResult | None]:
    return {
        "base": research.base,
        "selected": research.discovery.selected(),
        "ml": research.ml,
        "guard": research.guard,
    }


def _stress_cells(r: WalkForwardResult | None) -> tuple[str, str, str]:
    if r is None:
        return "-", "-", "none selected"
    a, b = r.label_train_summary, r.test_summary
    if not r.ran:
        return "not run", "not run", r.label
    return f"{sr(a.avg_r)} (n {a.n})", f"{sr(b.avg_r)} (n {b.n})", r.label


def _same_but_k(a: WalkForwardResult, b: WalkForwardResult) -> bool:
    """True if ``b`` is ``a``'s config with only the D4 stop-fill k changed."""
    ca, cb = a.cfg or DEFAULT_CFG, b.cfg or DEFAULT_CFG
    return ca.with_changes(stop_fill_wick_k=0.0) == cb.with_changes(stop_fill_wick_k=0.0)


def _stress_shifts(a: WalkForwardResult | None, b: WalkForwardResult | None) -> tuple[str, str]:
    """(judged TRAIN, TEST) avg R shift, stressed minus touch fill."""
    if a is None or b is None or not (a.ran and b.ran):
        return "n/a", "n/a"
    return (
        sr(b.label_train_summary.avg_r - a.label_train_summary.avg_r),
        sr(b.test_summary.avg_r - a.test_summary.avg_r),
    )


def stress_comparison_table(research: Research, reference: Research) -> list[str]:
    """D4: each pre-registered candidate under the touch fill (k = 0) vs this run's stress,
    judged TRAIN and TEST side by side, with the avg R shift (stressed minus k = 0) and any
    label change. ``same config`` is "no" when the stressed discovery selected another grid
    variant, so that row compares two configs, not one config under two fill models."""
    k = research.cfg.stop_fill_wick_k
    lines = [
        f"| candidate | variant k=0 / k={k:g} | same config apart from k | judged TRAIN avg R "
        f"k=0 | judged TRAIN avg R k={k:g} | TRAIN R shift | TEST avg R k=0 | TEST avg R "
        f"k={k:g} | TEST R shift | label k=0 | label k={k:g} | label changed |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---|---|---|",
    ]
    ref, cur = _role_results(reference), _role_results(research)
    for key, name in CANDIDATES:
        a, b = ref[key], cur[key]
        ta, ea, la = _stress_cells(a)
        tb, eb, lb = _stress_cells(b)
        d_train, d_test = _stress_shifts(a, b)
        same = "-" if a is None or b is None else ("yes" if _same_but_k(a, b) else "no")
        va = a.variant if a is not None else "-"
        vb = b.variant if b is not None else "-"
        lines.append(
            f"| {name} | `{va}` / `{vb}` | {same} | {ta} | {tb} | {d_train} | {ea} | {eb} | "
            f"{d_test} | {la} | {lb} | {'yes' if la != lb else 'no'} |"
        )
    return lines


def _stress_lines(research: Research) -> list[str]:
    reference = research.reference
    if reference is None or research.cfg.stop_fill_wick_k <= 0:
        return []
    k = research.cfg.stop_fill_wick_k
    return [
        "",
        f"**Stop-fill stress (CONTRACT v4 D4), k = {k:g} versus the touch fill model k = 0**, "
        "both on this data and split (the k = 0 run is the reference; TRAIN is the judged "
        "TRAIN window). Every stressed config is test-only, so this table measures how "
        "optimistic the touch fill is, nothing here is adoptable:",
        "",
        *stress_comparison_table(research, reference),
    ]


def section_verdict(research: Research, out_dir: Path | None = None) -> list[str]:
    names = ", ".join(name for name, _ in research.candidates())
    stats = research.base.stats
    missing = ""
    if research.discovery.selected() is None:
        missing = " (discovery selected nothing, so only three candidates had a look)"
    return [
        *section_heading(6),
        f"Candidates judged (pre-registered, one TEST look each, m = {stats.m}){missing}: "
        f"{names}. Other grid variants are excluded because choosing among them by TEST "
        f"numbers would be selection on TEST. {multiplicity_rule(stats.m, stats.alpha)} The "
        f"TEST gate is reached when the judged TRAIN avg R > 0 and both windows hold >= "
        f"{ROBUST_MIN_N} trades; the drawdown check is reported beside the label. The judged "
        f"TRAIN of `{ML_VARIANT}` is its out-of-sample gate (CONTRACT v4 D8, section 5).",
        "",
        *candidate_table(research),
        "",
        verdict_line(research),
        "",
        *holdout_lines(research.holdout, out_dir),
        *_stress_lines(research),
    ]


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
    if research.cfg.stop_fill_wick_k > 0:
        title += f", STOP-FILL STRESS k = {research.cfg.stop_fill_wick_k:g} (test-only)"
    sections = [
        [f"# Walk-forward research report: {title}"],
        section_win_rate(),
        section_provenance(ds, research, data_end),
        section_baseline(research, data_end),
        section_discovery(research),
        section_layers(research),
        section_verdict(research, out_dir),
        section_decisions(research),
        section_invariants(research),
        section_journal_rules(research, data_end, out_dir),
        section_links(research, out_dir, journals, reviews),
        section_adoption(adoption_statuses, out_dir, ds.kind == "synthetic", research),
        section_disclaimer(),
    ]
    return "\n\n".join("\n".join(s) for s in sections) + "\n"


def console_lines(research: Research) -> list[str]:
    """The console summary (also written to run.log): judged TRAIN beside TEST per candidate."""
    lines = []
    for name, r in research.candidates():
        a, b = r.label_train_summary, r.test_summary
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
