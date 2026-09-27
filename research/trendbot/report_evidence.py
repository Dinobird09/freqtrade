"""REPORT.md sections 7-12: the evidence behind the verdict and the adoption path.

Split out of ``report`` (which renders sections 1-6, assembles the document and re-exports
every name of this module): decision counts per rule (7), the invariant audit of every
backtest including the D8 gate backtests (8), the journal-rule audits at the end of TRAIN
and of TEST (9), the journal / review-pack links (10), the adoption records with their check
status (11) and the risk disclaimer (12). Nothing here runs a backtest.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from .adoption import BACKTEST, HUMAN_REVIEW, STAGES, WALK_FORWARD, config_fingerprint
from .config import RULE_IDS
from .journal import ms_to_iso
from .journal_rules import Adaptation, audit
from .metrics import M_CANDIDATES
from .models import HOUR_MS
from .report_tables import (
    DEFAULT_CFG,
    DISCLAIMER,
    cell,
    check_cell,
    insample_journal_path,
    journal_paths,
    rel,
    renumbered_test,
    section_heading,
    sr,
)
from .walkforward import BASE, ML_VARIANT, WalkForwardResult


if TYPE_CHECKING:  # pragma: no cover - typing only
    from .backtester import BacktestResult
    from .run_research import AdoptionStatus, Research


MAX_VIOLATIONS_SHOWN = 20


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
        *section_heading(7),
        "Signal candles denied per rule (the FIRST failing rule is counted, so the R1-R4 rows "
        "include every candle that was simply not a signal). Exits are never gated. TRAIN is "
        "the full TRAIN backtest (in-sample for the ML layer).",
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


def n_backtests(research: Research) -> int:
    """Backtests the invariant audit covered: TRAIN + TEST per variant that ran, plus every
    D8 gate backtest."""
    results = research.all_results()
    gates = sum(1 for r in results if r.ran and r.gate is not None and r.gate.ran)
    return 2 * sum(1 for r in results if r.ran) + gates


def section_invariants(research: Research) -> list[str]:
    total = research.n_violations()
    lines = [
        *section_heading(8),
        "`invariants.check_invariants` independently re-derives every mandatory rule "
        "(R1-R9, 2:1 minimum, sizing from the stop, exits on the first touch, no data after "
        "the window) from each trade list and the candles. A fitted layer's TRAIN column "
        "also covers its D8 out-of-sample gate backtest.",
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
        lines.append(f"Result: CLEAN. 0 violations in {n_backtests(research)} backtests.")
        return lines
    lines.append(f"Result: **{total} VIOLATIONS** (the pipeline is broken; do not use it):")
    shown = [
        f"- `{v}` {w}: {msg}"
        for v, (tr, te) in research.violations.items()
        for w, msgs in (("TRAIN", tr), ("TEST", te))
        for msg in msgs
    ]
    return lines + shown[:MAX_VIOLATIONS_SHOWN]


AUDIT_HEADER = (
    "| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action "
    "| explanation | evidence (journal trade ids) |"
)


def journal_audits(
    r: WalkForwardResult, data_end: int
) -> list[tuple[str, int, float, list[Adaptation]]]:
    """``(window, audit time, equity, adaptations)`` for the TRAIN and the TEST journal.

    TRAIN is the journal the record binds (:attr:`WalkForwardResult.label_train`: the D8
    gate for a fitted layer). Backtest journals record the OPEN of the exit candle, so the
    audit counts every exit from ``exit_ts + cfg.timeframe_ms`` (CONTRACT.md v2 A2), exactly
    like the backtester's circuit breakers.
    """
    cfg = r.cfg or DEFAULT_CFG
    tf = cfg.timeframe_ms
    train = r.label_train
    train_eq, test_eq = train.final_equity, r.test.final_equity
    return [
        ("TRAIN", r.split_ts, train_eq, audit(train.trades, cfg, r.split_ts, train_eq, tf)),
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
        *section_heading(9),
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
        *section_heading(10),
        f"Trade journals (`journal.write_journal`, one CSV per window) for the {len(journals)} "
        "pre-registered candidates that ran, the variants whose TRAIN and TEST columns this "
        "report shows in full. The other discovery variants appear only as one context row "
        "each in section 4, so no journal is kept for them (the same command regenerates "
        "them). TEST trade ids are offset past the TRAIN ids, so ids are unique across a "
        "variant's two journals and its review pack. The TRAIN journal is the judged TRAIN "
        "window (for a fitted layer its D8 out-of-sample gate; its in-sample full-TRAIN "
        "journal is kept as context and bound by no record).",
        "",
        "| variant | TRAIN journal (judged) | TEST journal | in-sample full-TRAIN journal "
        "(context) |",
        "|---|---|---|---|",
    ]
    for variant, (a, b) in journals.items():
        extra = insample_journal_path(out_dir, variant)
        ctx = f"[{rel(extra, out_dir)}]({rel(extra, out_dir)})" if extra.is_file() else "-"
        lines.append(
            f"| `{variant}` | [{rel(a, out_dir)}]({rel(a, out_dir)}) | "
            f"[{rel(b, out_dir)}]({rel(b, out_dir)}) | {ctx} |"
        )
    lines += ["", "Human review packs (`review_sheet.write_review_pack`, TRAIN and TEST):", ""]
    for variant, paths in reviews.items():
        md, csv_path = rel(paths["md"], out_dir), rel(paths["csv"], out_dir)
        lines.append(f"- `{variant}`: [{md}]({md}) and [{csv_path}]({csv_path})")
    no_pack = [v for v in journals if v not in reviews]
    if no_pack:
        names = ", ".join(f"`{v}`" for v in no_pack)
        lines.append(
            f"- no pack for {names}: a test-only config (CONTRACT v4 D1, `ADOPT_test_only` "
            "blocks it from HUMAN_REVIEW on), so a review pack could never be used; its "
            "journals above hold every trade."
        )
    return lines


# ---------------------------------------------------------------------------- section 11
def adoption_table(statuses: Sequence[AdoptionStatus]) -> list[str]:
    lines = [
        "| variant | walk-forward label (TRAIN + TEST) | TRAIN n (judged) | TRAIN avg R (judged) "
        "| TEST n | TEST avg R | dd_ok | provenance | config sha256 (first 16) | ML model sha256 "
        f"(first 16) | `check --stage {WALK_FORWARD}` | `check --stage {HUMAN_REVIEW}` |",
        "|---|---|---:|---:|---:|---:|---|---|---|---|---|---|",
    ]
    for st in statuses:
        r = st.result
        cfg_fp = config_fingerprint(r.cfg or DEFAULT_CFG)[:16]
        model = st.model_fingerprint[:16] if st.model_fingerprint else "none (no ML layer)"
        a, b = r.label_train_summary, r.test_summary
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
            f"The ML layer `{ML_VARIANT}` has no adoption record in this run (it could not be "
            "fitted, or the run is test-only), so there is no model to fingerprint."
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
        "the recorded one, and any `+ml` record without one. Its record's TRAIN journal is the "
        "D8 out-of-sample gate window.",
    ]


def _no_records_lines(research: Research | None) -> list[str]:
    stressed = research is not None and research.cfg.is_test_only
    why = (
        "every config of this run is test-only (stop-fill stress, CONTRACT v4 D4)"
        if stressed
        else "no pre-registered candidate of this run is adoptable"
    )
    return [
        f"**No adoption record was written: {why}.** Test-only and context variants are not "
        f"adoptable: they get no promotable record, and `adoption check` blocks any record for "
        f"one (`ADOPT_test_only` from {HUMAN_REVIEW} on, `ADOPT_holdout` 'context variant')."
    ]


def section_adoption(
    statuses: Sequence[AdoptionStatus] | None,
    out_dir: Path,
    synthetic: bool = False,
    research: Research | None = None,
) -> list[str]:
    lines = [
        *section_heading(11),
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
    if not statuses:
        return [*lines, *_no_records_lines(research)]
    if synthetic:
        lines += [
            "**This run used SYNTHETIC data: its records only demonstrate the mechanism. A "
            "synthetic result can never justify adopting a variant: `adoption check --stage "
            f"{HUMAN_REVIEW}` is BLOCKED by `ADOPT_provenance` for every record below, whatever "
            "its label.**",
            "",
        ]
    lines += [
        "Every record is bound to its evidence (CONTRACT.md v3 C3, v4 D1-D3): `provenance` "
        "(`real` only with a hash-matching `manifest.json`, else `unverified-csv`; synthetic "
        "`synthetic:<world>:<seed>`), `data_files` (sha256 per candle file), the manifest "
        "path and sha256, `events_file`; `backtest.report_sha256` (this REPORT.md, the record "
        "being rewritten after the report so the hash matches); both walk-forward journals "
        "with their sha256, `min_train` / `min_test`, `train_avg_r` and `label_params` (the "
        f"closed D1 schema: seed, n_boot, m = {M_CANDIDATES}, alpha, max_dd_pct, "
        "train_dd_p95_pct, mtm_max_dd_pct), from which `adoption check` recomputes the label, "
        "n, avg R and dd_ok; `ledger_path` (the D3 holdout ledger, section 6); and "
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
    return [*section_heading(12), DISCLAIMER]
