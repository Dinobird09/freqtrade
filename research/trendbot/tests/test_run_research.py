"""run_research + report: one command writes REPORT.md (12 sections), journals, packs,
evidence-bound adoption records, the model file and run.log; calibration and power curves."""

from __future__ import annotations

import csv
import json
import math
import pickle
import re
import shlex
from pathlib import Path
from types import SimpleNamespace

import pytest

from research.trendbot import journal_rules, report_calibration, report_evidence, report_tables
from research.trendbot import report as rp
from research.trendbot import run_research as rr
from research.trendbot.adoption import (
    HUMAN_REVIEW,
    WALK_FORWARD,
    check_promotion,
    config_fingerprint,
    load_config,
    load_record,
    recompute_walk_forward,
    sha256_file,
)
from research.trendbot.adoption import main as adoption_main
from research.trendbot.backtester import enumerate_candidates
from research.trendbot.config import PairRisk, StrategyConfig
from research.trendbot.data import save_candles_csv
from research.trendbot.fetch_data import manifest_entry, write_manifest
from research.trendbot.journal import iso_to_ms, ms_to_iso, read_journal
from research.trendbot.ledger import read_ledger, series_sha256
from research.trendbot.ml_filter import MLFilter
from research.trendbot.models import EXIT_SL, EXIT_TP, HOUR_MS, Trade
from research.trendbot.synthetic import make_world
from research.trendbot.walkforward import trade_keys


NOW = "2026-01-01T00:00:00Z"
EVENTS_EXAMPLE = Path(__file__).resolve().parents[1] / "events_example.csv"
NULL_ARGV = ["--synthetic", "null", "--seed", "1", "--years", "3", "--now", NOW]
CANDIDATE_SAFE = ("base", "base_plus_ml", "base_plus_guard")


_CAPTURED: dict[str, rr.Research] = {}


@pytest.fixture(scope="module")
def null_run(tmp_path_factory) -> tuple[Path, int, str]:
    """One end-to-end CLI run; the Research it built is kept for the ``null_research`` tests."""
    out = tmp_path_factory.mktemp("null_s1")
    real = rr.run_pipeline

    def spy(*args, **kwargs):  # type: ignore[no-untyped-def]
        _CAPTURED["null"] = real(*args, **kwargs)
        return _CAPTURED["null"]

    rr.run_pipeline = spy  # type: ignore[assignment]
    try:
        code = rr.main([*NULL_ARGV, "--out-dir", str(out)])
    finally:
        rr.run_pipeline = real  # type: ignore[assignment]
    return out, code, (out / rr.REPORT_NAME).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def null_research(null_run) -> rr.Research:
    return _CAPTURED["null"]


def _sections(text: str) -> list[str]:
    return re.findall(r"^## (.+)$", text, flags=re.MULTILINE)


def _body(text: str, number: int) -> str:
    """Text of section ``number`` (up to the next ``## `` heading)."""
    start = text.index(f"## {rp.SECTION_TITLES[number - 1]}")
    nxt = text.find("\n## ", start + 3)
    return text[start : nxt if nxt != -1 else len(text)]


def _tables(text: str) -> list[list[str]]:
    tables, current = [], []
    for line in text.splitlines():
        if line.startswith("|"):
            current.append(line)
        elif current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    return tables


def _cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


def _selected(text: str) -> str:
    found = re.search(r"discovery-selected `([^`]+)`", _body(text, 6))
    assert found is not None
    return found.group(1)


# ---------------------------------------------------------------------------- report
def test_report_has_all_twelve_sections_in_order(null_run) -> None:
    _out, code, text = null_run
    assert code == 0
    assert _sections(text) == list(rp.SECTION_TITLES)
    assert len(rp.SECTION_TITLES) == 12 and rr.SECTION_TITLES == rp.SECTION_TITLES


def test_null_world_reports_no_robust_result(null_run) -> None:
    _out, _code, text = null_run
    verdict = _body(text, 6)
    lines = [line for line in verdict.splitlines() if line.startswith(rp.NO_ROBUST)]
    assert len(lines) == 1 and "ROBUST:" not in verdict
    assert "TRAIN" in lines[0] and "TEST" in lines[0]
    assert "m = 4" in verdict and "98.75%" in verdict
    assert "calendar-month block bootstrap" in verdict


def test_up_front_win_rate_statement_and_disclaimer(null_run) -> None:
    _out, _code, text = null_run
    first = _body(text, 1)
    assert "NOT promise, target or optimise a win rate" in first
    assert "near 90%" in first and "curve-fitting or look-ahead" in first
    assert "POSITIVE EXPECTANCY" in first and "2:1" in first
    last = _body(text, 12)
    assert "not financial advice" in last and "total loss" in last


def test_provenance_states_synthetic_truth(null_run) -> None:
    _out, _code, text = null_run
    prov = _body(text, 2)
    assert "SYNTHETIC" in prov and "seed 1" in prov and "`synthetic:null:1`" in prov
    assert "not evidence about real markets" in prov
    assert "Ground truth" in prov and "martingale" in prov
    assert "News calendar loaded: yes" in prov
    assert "4000 bootstrap resamples" in prov and "m = 4" in prov


def test_every_table_header_labels_train_and_test(null_run) -> None:
    _out, _code, text = null_run
    tables = _tables(text)
    assert len(tables) >= 12
    for table in tables:
        header = table[0]
        assert "TRAIN" in header and "TEST" in header, header
    for number in (2, 3, 4, 5, 6, 7, 8, 9, 10, 11):
        assert _tables(_body(text, number)), f"section {number} has no table"
    assert "win rate (context only, never a target)" in _body(text, 3)
    assert "in-sample" in _body(text, 5)


def test_win_rate_is_only_ever_context(null_run) -> None:
    _out, _code, text = null_run
    mentions = [
        line
        for line in text.splitlines()
        if re.search("win rate", line, flags=re.IGNORECASE) and not line.startswith("#")
    ]
    assert len(mentions) >= 3
    for line in mentions:
        assert "context only" in line, line


def test_every_result_reports_both_drawdowns_and_the_rule(null_run, null_research) -> None:
    _out, _code, text = null_run
    base = _body(text, 3)
    assert "max drawdown % realised (closed trades)" in base
    assert "max drawdown % mark-to-market (4H closes)" in base
    rule = "dd_ok = TEST mark-to-market max drawdown <= min(15%, 95th percentile"
    assert rule in base and rule in _body(text, 4) and _body(text, 5).count(rule) == 2
    # D7: the cap and its rationale are stated in the report's DD rule text
    assert rp.DD_RULE in _body(text, 2) and "(2/3)^15 = 0.23%" in rp.DD_RULE
    assert "15 consecutive full-size losses" in base and "(2/3)^15 = 0.23%" in base
    r = null_research.base
    assert f"mark-to-market max drawdown {r.test_mtm_dd_pct:.2f}%" in base
    assert f"realised closed-trade {r.test_summary.max_dd_pct:.2f}%" in base
    assert f"the limit {r.dd_limit_pct:.2f}%" in base and r.dd_limit_pct <= 15.0
    assert f"TRAIN max drawdown: realised {r.train_summary.max_dd_pct:.2f}%" in base
    assert "TEST max DD % realised / MTM" in _body(text, 4)
    assert "20%" not in base


def test_provenance_prints_default_costs(null_run) -> None:
    _out, _code, text = null_run
    prov = _body(text, 2)
    assert "fee 0.100% of notional per side (the default: Binance spot taker" in prov
    assert "`--fee-rate 0.001`" in prov and "slippage 0.05% (`--slippage-pct 0.05`)" in prov
    assert "exchange `binance`" in prov and "charged on the entry AND on the exit" in prov
    assert "Coinbase Advanced Trade taker fees at low volume tiers are several times " in prov
    assert "pass the account's real tier with `--fee-rate`" in prov
    assert "a clean stop is exactly -1R" in prov and "nets exactly +2R after fees" in prov
    assert re.search(r"fees alone cost \d\.\d\dR per trade", prov)
    assert "WARNING" not in prov
    table = _tables(prov)[0]
    assert "TRAIN candles (closed by the split)" in table[0] and len(table) == 2 + 3
    for row in table[2:]:
        total, n_train, n_test = (int(c) for c in row.split("|")[2:5])
        assert n_train + n_test == total and n_train > 2 * n_test


def test_discovery_section_marks_selection_k_and_context_labels(null_run, null_research) -> None:
    _out, _code, text = null_run
    disc = _body(text, 4)
    assert "K = 13 variants tried" in disc
    assert disc.count("**SELECTED**") == 1
    assert disc.count("TEST-ONLY (regime OFF, never selectable)") == 1
    assert "turn TEST into TRAIN" in disc
    assert disc.index("selection recorded") < disc.index("TEST backtests")
    rows = [_cells(row) for row in _tables(disc)[0][2:]]
    assert len(rows) == 13
    selected = null_research.discovery.selection.variant
    for cells in rows:
        variant, status, lab = cells[0].strip("`"), cells[1], cells[-1]
        result = null_research.discovery.result(variant)
        if variant == selected:
            assert status == "**SELECTED**" and lab == result.label  # judged: bare label
        else:
            assert lab == f"context: {result.label}, not judged", cells


def test_layer_section_uses_the_c1_wording(null_run) -> None:
    _out, _code, text = null_run
    layers = _body(text, 5)
    assert rp.LAYER_WORDING in layers
    assert "can only VETO an entry that passed every mandatory rule" in layers
    assert "a veto can free R6 budget or change R9 state".lower() in layers.lower()
    assert "only REMOVE" not in text and "removed" not in layers
    assert "p* =" in layers and "standardised coefficient" in layers
    assert "LightGBM" in layers and "NOT justified" in layers
    assert layers.count("**Label:") == 2  # base+ml and base+guard


def test_layer_counts_are_recomputed_from_the_written_journals(null_run, null_research) -> None:
    """C1: vetoed, base-only and layer-only counts per window match the journals on disk."""
    out, _code, text = null_run
    table = next(t for t in _tables(_body(text, 5)) if "signals vetoed" in t[0])
    rows = {(_cells(r)[0].strip("`"), _cells(r)[1]): _cells(r) for r in table[2:]}
    assert set(rows) == {
        (v, w) for v in (rr.ML_VARIANT, rr.GUARD_VARIANT) for w in ("TRAIN", "TEST")
    }
    for variant, safe in ((rr.ML_VARIANT, "base_plus_ml"), (rr.GUARD_VARIANT, "base_plus_guard")):
        layer_r = null_research.ml if variant == rr.ML_VARIANT else null_research.guard
        for window, suffix in (("TRAIN", "train"), ("TEST", "test")):
            base_keys = trade_keys(read_journal(out / "journals" / f"base_{suffix}.csv"))
            # C1 compares full-TRAIN journals: the ML layer's is its in-sample context journal
            ml_train = variant == rr.ML_VARIANT and window == "TRAIN"
            name = f"{safe}_train_insample.csv" if ml_train else f"{safe}_{suffix}.csv"
            lay_keys = trade_keys(read_journal(out / "journals" / name))
            cells = rows[(variant, window)]
            bt = layer_r.train if window == "TRAIN" else layer_r.test
            rule = "L_ml_filter" if variant == rr.ML_VARIANT else "L_expectancy_guard"
            assert int(cells[2]) == bt.decisions[rule]
            assert int(cells[4]) == len(base_keys - lay_keys)
            assert int(cells[5]) == len(lay_keys - base_keys)
            assert int(cells[6]) == len(base_keys & lay_keys)
            assert (int(cells[7]), int(cells[8])) == (len(base_keys), len(lay_keys))
    ml_train = rows[(rr.ML_VARIANT, "TRAIN")]
    assert int(ml_train[2]) > 0  # the fitted filter did veto rule-passing signals


def test_invariants_clean_and_journal_audit_present(null_run, null_research) -> None:
    _out, _code, text = null_run
    gate = null_research.ml.gate
    n = 32 + (1 if gate is not None and gate.ran else 0)  # 16 variants x 2 (+ the D8 gate)
    assert rp.n_backtests(null_research) == n
    assert f"Result: CLEAN. 0 violations in {n} backtests." in _body(text, 8)
    audit = _body(text, 9)
    assert "journal_rules.audit" in audit and "baseline `base`" in audit
    assert "expectancy guard `base+guard`" in audit
    assert "exit_ts + 4h" in audit and "exit_time_uncertainty_ms = cfg.timeframe_ms" in audit
    windows = [row.split("|")[1].strip() for t in _tables(audit) for row in t[2:]]
    assert any(w.startswith("TRAIN at ") for w in windows)
    assert any(w.startswith("TEST at ") for w in windows)


def test_journal_audit_row_is_reproducible_with_the_cli(null_run, capsys) -> None:
    out, _code, text = null_run
    audit = _body(text, 9)
    command = re.search(r"`(python -m research\.trendbot\.journal_rules [^`]+)`", audit)
    assert command is not None
    argv = shlex.split(command.group(1))[3:]
    assert "--backtest-journal" in argv and str(out) in argv[argv.index("--journal") + 1]
    assert journal_rules.main(argv) == 0
    printed = capsys.readouterr().out
    base_table = _tables(audit[audit.index("baseline `base`") :])[0]
    test_rows = [row for row in base_table[2:] if row.split("|")[1].strip().startswith("TEST")]
    assert test_rows
    for row in test_rows:
        explanation = row.split("|")[5].strip()
        if explanation == "No active adaptations.":
            assert "No active adaptations." in printed
        else:
            assert explanation in printed


def _sl(trade_id: int, exit_ts: int) -> Trade:
    return Trade(
        trade_id=trade_id,
        pair="BTC/USDT",
        variant="base",
        signal_ts=exit_ts - 12 * HOUR_MS,
        entry_ts=exit_ts - 8 * HOUR_MS,
        entry_price=100.0,
        stop=98.0,
        target=104.5,
        qty=1.0,
        risk_amount=2.3,
        risk_pct=0.02,
        exit_ts=exit_ts,
        exit_price=97.95,
        exit_reason=EXIT_SL,
        fees=0.2,
        pnl=-2.3,
        r_multiple=-1.0,
    )


def test_journal_audits_count_exits_from_the_candle_close() -> None:
    """CONTRACT v2 A2: backtest exits count from exit_ts + timeframe, so the bench is later."""
    cfg = StrategyConfig()
    tf = cfg.timeframe_ms
    split = iso_to_ms("2024-01-01T00:00:00Z")
    end = split + 60 * tf
    last_exit = end - tf  # exit filled somewhere inside the last candle
    test_trades = [_sl(1, last_exit - 8 * tf), _sl(2, last_exit - 4 * tf), _sl(3, last_exit)]
    train = SimpleNamespace(trades=[], final_equity=10_000.0)
    fake = SimpleNamespace(
        cfg=cfg,
        split_ts=split,
        train=train,
        label_train=train,
        test=SimpleNamespace(trades=test_trades, final_equity=9_993.1),
    )
    (w1, at1, _eq1, train_acts), (w2, at2, eq2, test_acts) = rp.journal_audits(fake, end)  # type: ignore[arg-type]
    assert (w1, at1, train_acts) == ("TRAIN", split, [])
    assert (w2, at2, eq2) == ("TEST", end, 9_993.1)
    benches = [a for a in test_acts if a.scope == "BTC/USDT"]
    assert len(benches) == 1 and benches[0].evidence_trade_ids == (1, 2, 3)
    until = last_exit + tf + 24 * HOUR_MS  # 24h from the CLOSE of the exit candle
    assert benches[0].action == f"no entries until {ms_to_iso(until)}"
    live = journal_rules.audit(test_trades, cfg, end, 9_993.1)  # live convention: 0
    assert live[0].action == f"no entries until {ms_to_iso(last_exit + 24 * HOUR_MS)}"


# ---------------------------------------------------------------------------- outputs
def test_output_files_journals_and_packs(null_run) -> None:
    out, _code, text = null_run
    links = _body(text, 10)
    for rel in re.findall(r"\]\(([^)]+)\)", links):
        assert (out / rel).is_file(), rel
    base_train = read_journal(out / "journals" / "base_train.csv")
    base_test = read_journal(out / "journals" / "base_test.csv")
    ids = [t.trade_id for t in base_train + base_test]
    assert len(ids) == len(set(ids)), "TEST ids are offset past the TRAIN ids"
    with (out / "review_base" / "trades_review.csv").open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == len(ids)
    assert {r["window"] for r in rows} == {"TRAIN", "TEST"}


def test_results_stay_lean_journals_only_for_the_candidates(null_run) -> None:
    out, _code, text = null_run
    selected = _selected(text)
    safe = [*CANDIDATE_SAFE, selected]
    expected = {f"{v}_{w}.csv" for v in safe for w in ("train", "test")}
    expected.add("base_plus_ml_train_insample.csv")  # D8: the in-sample context journal
    assert {p.name for p in (out / "journals").iterdir()} == expected
    assert sorted(p.name for p in out.glob("review_*")) == sorted(
        [
            "review_base",
            "review_base_plus_ml",
            "review_base_plus_guard",
            f"review_selected_{selected}",
        ]
    )
    records = {p.name for p in out.glob("adoption_*.json")}
    assert records == {f"adoption_{v}.json" for v in safe}
    assert "no journal is kept for them" in _body(text, 10)


def test_adoption_records_are_bound_to_their_evidence(null_run, null_research) -> None:
    """C3: provenance, report / journal / review hashes, label parameters, all consistent."""
    out, _code, _text = null_run
    report_sha = sha256_file(out / rr.REPORT_NAME)
    by_variant = {r.variant: r for _n, r in null_research.candidates()}
    for path in sorted(out.glob("adoption_*.json")):
        rec = load_record(path)
        r = by_variant[rec.variant]
        assert rec.provenance == "synthetic:null:1"
        assert rec.data_files is None and rec.events_file is None
        assert rec.backtest.report_path == rr.REPORT_NAME and rec.backtest.completed_utc == NOW
        assert rec.backtest.report_sha256 == report_sha  # the FINAL report
        wf = rec.walk_forward
        for window in ("train", "test"):
            jpath = out / getattr(wf, f"{window}_journal_path")
            assert getattr(wf, f"{window}_journal_sha256") == sha256_file(jpath)
        assert (wf.min_train, wf.min_test) == (30, 30)
        assert wf.label_params == r.label_params()
        # TRAIN = the judged window (the D8 out-of-sample gate for base+ml)
        judged = r.label_train_summary
        assert (wf.label, wf.dd_ok, wf.train_n, wf.test_n) == (
            r.label,
            r.dd_ok,
            judged.n,
            r.test_summary.n,
        )
        assert (wf.train_avg_r, wf.test_avg_r) == (judged.avg_r, r.test_summary.avg_r)
        assert len(read_journal(out / wf.train_journal_path)) == judged.n
        hr = rec.human_review
        assert hr.review_sha256 == sha256_file(out / hr.review_path)
        assert hr.review_path.endswith("trades_review.csv")
        # the typed numbers are exactly what adoption recomputes from the journals
        config = out / f"config_{rp.safe_name(rec.variant)}.json"
        cfg = load_config(config if config.is_file() else None)
        assert rec.config_fingerprint == config_fingerprint(cfg)
        v = recompute_walk_forward(
            read_journal(out / wf.train_journal_path),
            read_journal(out / wf.test_journal_path),
            cfg,
            wf.min_train,
            wf.min_test,
            wf.label_params,
        )
        assert (v.label, v.dd_ok, v.train.n, v.test.n) == (
            wf.label,
            wf.dd_ok,
            wf.train_n,
            wf.test_n,
        )


def _check(
    out: Path, variant: str, stage: str, capsys, model: str | None = None
) -> tuple[int, str]:
    argv = ["check", "--record", str(out / f"adoption_{rp.safe_name(variant)}.json")]
    argv += ["--stage", stage, "--now", NOW]
    config = out / f"config_{rp.safe_name(variant)}.json"
    if config.is_file():
        argv += ["--config", str(config)]
    if model is not None:
        argv += ["--model-fingerprint", model]
    code = adoption_main(argv)
    return code, capsys.readouterr().out


def test_adoption_check_on_written_synthetic_records(null_run, null_research, capsys) -> None:
    """HUMAN_REVIEW is blocked by ADOPT_provenance; WALK_FORWARD passes; ADOPT_walk_forward
    appears exactly when the label / dd_ok say it must; no typed number mismatches."""
    out, _code, text = null_run
    for _name, r in null_research.candidates():
        if not r.ran:
            continue
        ml = rp.fitted_model(r)
        model = ml.fingerprint() if ml is not None else None
        code, printed = _check(out, r.variant, WALK_FORWARD, capsys, model)
        assert code == 0 and printed.startswith("PASS"), printed
        code, printed = _check(out, r.variant, HUMAN_REVIEW, capsys, model)
        assert code == 1 and "[ADOPT_provenance]" in printed
        assert "synthetic world only verifies the harness" in printed
        should_block_wf = r.label != "ROBUST" or not r.dd_ok
        assert ("[ADOPT_walk_forward]" in printed) == should_block_wf, printed
        assert "does not match its evidence" not in printed
        assert "ADOPT_fingerprint" not in printed
    adoption = _body(text, 11)
    assert "Stage reached by this run: **WALK_FORWARD**" in adoption
    assert "Next: **HUMAN_REVIEW**" in adoption
    assert "BLOCKED by `ADOPT_provenance`" in adoption
    assert "Binance testnet" in adoption and ">= 2 weeks" in adoption
    table = next(t for t in _tables(adoption) if "walk-forward label (TRAIN + TEST)" in t[0])
    assert "TRAIN avg R" in table[0] and "TEST avg R" in table[0]
    for row in table[2:]:
        cells = _cells(row)
        assert cells[-2] == "PASS" and cells[-1].startswith("BLOCKED by ADOPT_provenance")


def test_adoption_check_commands_in_the_report_run(null_run, capsys) -> None:
    _out, _code, text = null_run
    commands = re.findall(r"Check: `(python -m research\.trendbot\.adoption check [^`]+)`", text)
    assert len(commands) == 2 * 4  # 4 records x (WALK_FORWARD, HUMAN_REVIEW)
    for command in commands:
        argv = shlex.split(command)[3:]
        code = adoption_main([*argv, "--now", NOW])
        printed = capsys.readouterr().out
        if "--stage WALK_FORWARD" in command:
            assert code == 0, printed
        else:
            assert code == 1 and "ADOPT_provenance" in printed
        assert "ADOPT_fingerprint" not in printed  # config and model match what was tested


def test_ml_adoption_record_pins_the_fitted_model(null_run) -> None:
    out, _code, text = null_run
    rec = load_record(out / "adoption_base_plus_ml.json")
    assert rec.variant == rr.ML_VARIANT
    model_file = out / "model_base_plus_ml.json"
    loaded = MLFilter.from_json(model_file.read_text("utf-8"), rec.model_fingerprint)
    fp = loaded.fingerprint()
    assert rec.model_fingerprint == fp and json.loads(model_file.read_text())["fingerprint"] == fp
    assert f"`{fp}`" in _body(text, 5)
    # the same TRAIN candidates always refit to exactly the recorded model
    ds = rr.load_synthetic("null", 1, 3.0)
    split = rr.split_ts(ds.data, rr.TRAIN_FRAC, StrategyConfig().timeframe_ms)
    cands = enumerate_candidates(ds.data, StrategyConfig(), ds.events, end_ts=split)
    refit = MLFilter.fit(cands)
    assert refit.fingerprint() == fp and refit.to_json() == model_file.read_text("utf-8")
    now = iso_to_ms(NOW)
    assert check_promotion(rec, WALK_FORWARD, StrategyConfig(), now, out, fp) == []
    for wrong in (None, "0" * 64, MLFilter.fit(cands[:-1]).fingerprint()):
        rules = {
            d.rule for d in check_promotion(rec, WALK_FORWARD, StrategyConfig(), now, out, wrong)
        }
        assert "ADOPT_fingerprint" in rules, wrong
    adoption = _body(text, 11)
    assert (
        "Refitting the model" in adoption and "restarts the adoption path at BACKTEST" in adoption
    )
    assert f"--model-fingerprint {fp}" in adoption and "MLFilter.from_json" in adoption
    assert "`base+ml` | " in adoption  # row of the adoption table


def test_guard_is_a_preregistered_candidate_with_its_own_record(null_run, null_research) -> None:
    out, _code, text = null_run
    guard = null_research.guard
    assert guard.variant == rr.GUARD_VARIANT and guard.cfg.expectancy_guard
    assert json.loads((out / "config_base_plus_guard.json").read_text()) == {
        "expectancy_guard": True
    }
    rec = load_record(out / "adoption_base_plus_guard.json")
    assert rec.config_fingerprint == config_fingerprint(guard.cfg)
    assert rec.config_fingerprint != config_fingerprint(StrategyConfig())
    assert all(t.variant == rr.GUARD_VARIANT for t in guard.train.trades + guard.test.trades)
    assert "expectancy guard `base+guard`" in _body(text, 6)
    assert [name for name, _ in null_research.candidates()][-1] == "expectancy guard `base+guard`"


# ---------------------------------------------------------------------------- console + log
def test_run_log_holds_the_exact_command_and_the_console_summary(null_run) -> None:
    out, code, _text = null_run
    log = (out / rr.RUN_LOG).read_text(encoding="utf-8").splitlines()
    assert log[0] == f"$ {rr.PROG} {shlex.join([*NULL_ARGV, '--out-dir', str(out)])}"
    assert log[1].startswith("costs: fee 0.100% per side")
    assert log[-1] == f"exit code: {code}"
    candidate_lines = [line for line in log if line.startswith(("baseline", "ML layer"))]
    assert len(candidate_lines) == 2
    for line in candidate_lines + [next(x for x in log if x.startswith(rp.NO_ROBUST))]:
        assert "TRAIN" in line and "TEST" in line, line
    assert sum(1 for line in log if line.startswith("adoption adoption_")) == 4
    assert any(line.startswith("report: ") for line in log)


def test_console_and_verdict_lines_show_train_beside_test(null_research) -> None:
    for line in rp.console_lines(null_research):
        assert re.search(r"TRAIN n=\d+, avg [+-]\d\.\d{3}R \| TEST n=\d+, avg", line), line
    verdict = rr.verdict_line(null_research)
    assert verdict.startswith(rp.NO_ROBUST)
    for name, r in null_research.candidates():
        assert f"{name} {r.label} (TRAIN" in verdict and "| TEST" in verdict


def test_verdict_lists_robust_candidates_only(null_research) -> None:
    research = null_research
    saved = [(r, r.label) for _n, r in research.candidates()] + [
        (r, r.label) for r in research.discovery.results
    ]
    try:
        selected = research.discovery.selection.variant
        for r in research.discovery.results:  # non-selected grid variants never count
            r.label = "NO-EDGE" if r.variant == selected else "ROBUST"
        for r in (research.base, research.ml, research.guard):
            r.label = "NO-EDGE"
        assert rr.verdict_line(research).startswith(rp.NO_ROBUST)
        research.base.label = "ROBUST"
        line = rr.verdict_line(research)
        assert line.startswith("ROBUST: baseline `base` (TRAIN avg") and line.endswith(".")
        assert "| TEST avg" in line and "98.75% lower bound" in line
        assert "ML layer" not in line and "discovery-selected" not in line
    finally:
        for r, lab in saved:
            r.label = lab


# ---------------------------------------------------------------------------- real-data path
def _write_files(tmp_path: Path) -> Path:
    data, _events = make_world("planted", 4, years=2.0)
    d = tmp_path / "data"
    for pair, candles in data.items():
        save_candles_csv(candles, d / f"{pair.replace('/', '_')}-4h.csv")
    return d


COST_ARGS = ["--fee-rate", "0.002", "--slippage-pct", "0.1", "--exchange-id", "Coinbase"]


def test_data_dir_without_events_flags_r5_and_costs_reach_every_trade(
    tmp_path: Path, capsys
) -> None:
    d = _write_files(tmp_path)
    out = tmp_path / "out"
    assert rr.main(["--data-dir", str(d), "--out-dir", str(out), "--now", NOW, *COST_ARGS]) == 0
    assert "costs: fee 0.200% per side, slippage 0.1% on market fills, exchange coinbase" in (
        capsys.readouterr().out
    )
    text = (out / rr.REPORT_NAME).read_text(encoding="utf-8")
    prov = _body(text, 2)
    assert "News calendar loaded: **NO**" in prov
    assert "R5 (news blackout) could NOT be exercised historically" in prov
    assert "| BTC/USDT |" in prov and "gaps" in prov
    assert "SYNTHETIC" not in prov
    assert _sections(text) == list(rp.SECTION_TITLES)
    assert "fee 0.200% of notional per side (`--fee-rate 0.002`)" in prov
    assert "slippage 0.1% (`--slippage-pct 0.1`)" in prov and "exchange `coinbase`" in prov
    assert "Binance spot taker" not in prov and "WARNING" not in prov
    assert "Result: CLEAN." in _body(text, 8)  # invariants re-derived with the same costs
    trades = read_journal(out / "journals" / "base_train.csv")
    trades += read_journal(out / "journals" / "base_test.csv")
    assert trades
    for t in trades:
        legs = t.qty * t.entry_price + t.qty * t.exit_price  # type: ignore[operator]
        assert t.fees == pytest.approx(0.002 * legs, rel=1e-9)
        if t.exit_reason == EXIT_SL and t.exit_price >= t.stop * (1 - 0.001) - 1e-9:  # type: ignore[operator]
            assert t.r_multiple == pytest.approx(-1.0, abs=1e-9)
        if t.exit_reason == EXIT_TP:
            assert t.r_multiple == pytest.approx(2.0, abs=1e-9)
    overrides = json.loads((out / "config_base.json").read_text(encoding="utf-8"))
    assert overrides == {"exchange_id": "coinbase", "fee_rate": 0.002, "slippage_pct": 0.1}
    rec = load_record(out / "adoption_base.json")
    cfg = load_config(out / "config_base.json")
    assert rec.config_fingerprint == config_fingerprint(cfg)
    assert rec.config_fingerprint != config_fingerprint(StrategyConfig())
    assert "--config" in _body(text, 11)
    # D2: no manifest.json next to the files, so the provenance is "unverified-csv" and the
    # records are blocked past WALK_FORWARD; every candle file is still hash-bound
    assert rec.provenance == "unverified-csv" and rec.events_file is None
    assert "**`unverified-csv`**" in prov and "no manifest.json" in prov
    assert "cannot authenticate an exchange download" in prov
    assert rec.data_files is not None and len(rec.data_files) == 3
    for path, sha in rec.data_files.items():
        assert not Path(path).is_absolute() and sha256_file(out / path) == sha
    blocked = check_promotion(rec, HUMAN_REVIEW, cfg, iso_to_ms(NOW), out)
    assert "ADOPT_provenance" in {d.rule for d in blocked}
    assert check_promotion(rec, WALK_FORWARD, cfg, iso_to_ms(NOW), out) == []
    # D3: the real-data ledger defaults to <data-dir>/.test_looks.jsonl
    looks = read_ledger(d / ".test_looks.jsonl")
    records = sorted(out.glob("adoption_*.json"))
    assert len(looks) == len(records) >= 3  # one line per adoptable candidate with a TEST look
    shas = {sha256_file(d / f"{p.replace('/', '_')}-4h.csv") for p in rr.PAIRS}
    assert all({w.file_sha256 for w in look.pairs} == shas for look in looks)
    assert not (out / "test_looks.jsonl").exists()


def test_data_dir_with_events_binds_the_calendar(tmp_path: Path) -> None:
    d = _write_files(tmp_path)
    ds = rr.load_files(d, events_path=EVENTS_EXAMPLE)
    assert ds.kind == "files" and ds.events and ds.provenance_id == "unverified-csv"
    assert any("News calendar loaded: yes" in line for line in ds.provenance)
    files, events = rr._data_evidence(ds, tmp_path / "out")
    assert files is not None and len(files) == 3
    assert events is not None and events.sha256 == sha256_file(EVENTS_EXAMPLE)
    assert (tmp_path / "out" / events.path).resolve() == EVENTS_EXAMPLE.resolve()


def test_cost_lines_warn_when_a_non_binance_run_keeps_the_binance_fee() -> None:
    lines = " ".join(rp.cost_lines(StrategyConfig(exchange_id="coinbase")))
    assert "WARNING: exchange `coinbase` was run with the Binance default fee" in lines
    assert "WARNING" not in " ".join(
        rp.cost_lines(StrategyConfig(exchange_id="coinbase", fee_rate=0.006))
    )


def test_cost_flags_default_to_the_config_defaults() -> None:
    args = rr._parser().parse_args(["--synthetic", "null", "--out-dir", "o"])
    assert rr.config_from_args(args) == StrategyConfig()
    args = rr._parser().parse_args(["--synthetic", "null", "--out-dir", "o", *COST_ARGS])
    cfg = rr.config_from_args(args)
    assert (cfg.fee_rate, cfg.slippage_pct, cfg.exchange_id) == (0.002, 0.1, "coinbase")
    assert rr.cost_overrides(cfg) == (
        ("fee_rate", 0.002),
        ("slippage_pct", 0.1),
        ("exchange_id", "coinbase"),
    )
    assert rr.cost_overrides(StrategyConfig()) == () and rr.cost_overrides(None) == ()


def test_config_overrides_round_trip_through_adoption_load_config(tmp_path: Path) -> None:
    assert rr.config_overrides(StrategyConfig()) == {}
    cfg = StrategyConfig(
        fee_rate=0.006,
        slippage_pct=0.2,
        exchange_id="coinbase",
        reward_risk=3.0,
        vol_mult=2.0,
        rsi_min=55.0,
        regime_filter=True,
        expectancy_guard=True,
        pair_risk={
            "BTC": PairRisk(0.8, 0.3),
            "ETH": PairRisk(1.0, 0.25),
            "BNB": PairRisk(0.5, 0.7),
        },
        cluster_risk_budget_pct=1.0,
    )
    path = tmp_path / "c.json"
    path.write_text(json.dumps(rr.config_overrides(cfg)), encoding="utf-8")
    assert config_fingerprint(load_config(path)) == config_fingerprint(cfg)


# ---------------------------------------------------------------------------- calibration
def test_calibration_jobs_carry_the_costs_and_strength(monkeypatch) -> None:
    job = ("planted", 1, 0.5, rr.cost_overrides(StrategyConfig(fee_rate=0.002)), 0.3)
    assert pickle.loads(pickle.dumps(job)) == job  # noqa: S301 - own data; process-pool safe
    seen: list[object] = []

    class Stop(Exception):
        pass

    def fake_pipeline(data, events, cfg=None, **_kw):  # type: ignore[no-untyped-def]
        seen.append(cfg)
        raise Stop

    real_load = rr.load_synthetic

    def fake_load(world, seed, years, pairs=rr.PAIRS, effect_strength=None):  # type: ignore[no-untyped-def]
        seen.append(effect_strength)
        return real_load(world, seed, years, pairs, effect_strength)

    monkeypatch.setattr(rr, "run_pipeline", fake_pipeline)
    monkeypatch.setattr(rr, "load_synthetic", fake_load)
    with pytest.raises(Stop):
        rr.calibrate_one(job)
    assert seen[0] == 0.3
    assert seen[1].fee_rate == 0.002 and seen[1].slippage_pct == StrategyConfig().slippage_pct  # type: ignore[union-attr]


def test_calibration_writes_label_frequencies(tmp_path: Path) -> None:
    out = tmp_path / "cal"
    args = ["--synthetic", "decay", "--calibrate-seeds", "2", "--years", "1.5"]
    argv = [*args, "--workers", "1", "--out-dir", str(out), "--fee-rate", "0.0012"]
    assert rr.main(argv) == 0
    text = (out / rr.CALIBRATION_NAME).read_text(encoding="utf-8")
    assert "## Label frequencies" in text and "FALSE-POSITIVE rate" in text
    assert "not evidence about real markets" in text
    assert "fee 0.120% of notional per side (`--fee-rate 0.0012`)" in text
    assert "Baseline labelled TRAIN-ONLY:" in text and "UNTESTED:" in text
    assert "90% Wilson CI" in text and "reached the TEST gate" in text
    assert "ROBUST given the TEST gate was reached" in text
    assert rp.DD_RULE in text and "mean TEST max DD % realised / MTM / limit" in text
    for table in _tables(text):
        assert "TRAIN" in table[0] and "TEST" in table[0], table[0]
    per_seed = next(t for t in _tables(text) if t[0].startswith("| seed |"))[0]
    for column in ("selected TRAIN n", "selected TEST n", "ML TRAIN n", "guard TRAIN n"):
        assert column in per_seed
    with (out / rr.CALIBRATION_CSV).open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0]) == list(rr.CAL_FIELDS)
    assert [r["seed"] for r in rows] == ["1", "2"] and {r["world"] for r in rows} == {"decay"}
    assert all(r["violations"] == "0" for r in rows)
    # a 1.5-year history is too short for the ML layer: the InsufficientData path
    assert all(r["ml_label"] == "UNTESTED" and r["ml_fit_error"] for r in rows)
    assert all(r["ml_fingerprint"] == "" and r["ml_top_feature"] == "" for r in rows)
    log = (out / rr.RUN_LOG).read_text(encoding="utf-8").splitlines()
    assert log[0] == f"$ {rr.PROG} {shlex.join(argv)}" and log[-1] == "exit code: 0"


def test_calibration_ends_with_the_adoption_path_then_the_disclaimer(null_research) -> None:
    rows = [rr.cal_row(1, null_research, "null")]
    text = rp.render_calibration("null", 3.0, rows)
    headings = _sections(text)
    assert headings[-2:] == ["Adoption path", "Risk disclaimer"]
    tail = text[text.index("## Adoption path") :]
    assert "ADOPT_provenance" in tail and "synthetic:<world>:<seed>" in tail
    assert text.rstrip().endswith(rp.DISCLAIMER)


def test_calibration_row_describes_every_candidate(null_run, null_research) -> None:
    research = null_research
    row = rr.cal_row(1, research, "null")
    assert list(row) == list(rr.CAL_FIELDS)
    ml = rp.fitted_model(research.ml)
    assert ml is not None and row["ml_fingerprint"] == ml.fingerprint()
    coefs = dict(ml.explain())
    assert row["ml_hour_sin"] == round(coefs["hour_sin"], 4)
    assert row["ml_top_feature"] == max(coefs, key=lambda k: abs(coefs[k]))
    assert row["ml_train_avg_r"] == round(research.ml.label_train_summary.avg_r, 4)  # D8 gate
    assert row["ml_train_insample_avg_r"] == round(research.ml.train_summary.avg_r, 4)
    assert row["ml_train_insample_n"] == research.ml.train_summary.n
    assert row["guard_test_n"] == research.guard.test_summary.n
    assert row["base_reached_gate"] == research.base.reached_test_gate
    diffs = {d.window: d for d in research.layer_diffs()[rr.ML_VARIANT]}
    assert row["ml_train_vetoed"] == diffs["TRAIN"].vetoed
    assert row["ml_test_layer_only"] == diffs["TEST"].layer_only
    line = rp.ml_hour_line([row])
    assert line is not None and "1 fitted seeds" in line
    assert math.isfinite(float(line.split("= ")[-1].split()[0]))


def test_wilson_interval() -> None:
    lo, hi = rp.wilson(0, 20)
    assert lo == 0.0 and 0.0 < hi < 0.2
    lo, hi = rp.wilson(10, 20)
    assert lo < 0.5 < hi
    assert rp.wilson(0, 0) == (0.0, 1.0)
    # 90% two-sided: z = 1.6449; p = 1/2, n = 100 -> 0.5 +/- 0.0818
    lo, hi = rp.wilson(50, 100)
    assert lo == pytest.approx(0.4185, abs=1e-3) and hi == pytest.approx(0.5815, abs=1e-3)
    assert rp.rate(3, 20).startswith("3/20 = 15% (90% Wilson CI ")


# ---------------------------------------------------------------------------- power curve
def test_minimum_detectable_effect_interpolates_between_strengths() -> None:
    points = [(0.2, 0.0, 0.0, 0, 20), (0.4, 0.2, 0.4, 8, 20), (0.6, 0.4, 0.9, 18, 20)]
    assert "~+0.240R" in rp.mde(points, 0.5) and "strength ~0.44" in rp.mde(points, 0.5)
    assert "~+0.360R" in rp.mde(points, 0.8)
    assert rp.mde(points, 0.95).startswith("not reached")
    assert rp.mde([(0.2, 0.1, 0.6, 12, 20), (0.4, 0.3, 0.9, 18, 20)], 0.5).startswith("at or below")


def test_power_curve_cli_writes_power_md(tmp_path: Path) -> None:
    out = tmp_path / "power"
    argv = ["--synthetic", "planted", "--calibrate-seeds", "1", "--years", "1.0"]
    argv += ["--power-strengths", "0.3", "0.9", "--workers", "1", "--out-dir", str(out)]
    assert rr.main(argv) == 0
    text = (out / rr.POWER_NAME).read_text(encoding="utf-8")
    assert "## Minimum detectable effect" in text and "50% power:" in text
    assert "80% power:" in text and "labelled UNTESTED on real data however real it is" in text
    assert rp.DD_RULE in text and "realised / MTM / limit, base" in text
    # wording: an average of observed estimates, never "the true" expectancy
    assert "mean observed TEST avg R over seeds" in text and "true mean" not in text.lower()
    assert "every strength ran seed 1" in text
    table = next(t for t in _tables(text) if "effect strength" in t[0])
    assert "TRAIN" in table[0] and "TEST" in table[0] and len(table) == 2 + 2
    assert _sections(text)[-2:] == ["Adoption path", "Risk disclaimer"]
    with (out / rr.POWER_CSV).open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["effect_strength"] for r in rows] == ["0.3", "0.9"]
    assert (out / rr.RUN_LOG).read_text().startswith(f"$ {rr.PROG} {shlex.join(argv)}")


# ---------------------------------------------------------------------------- CLI
@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help_prints_and_exits_zero(flag: str, capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        rr.main([flag])
    assert exc.value.code == 0
    printed = capsys.readouterr().out
    assert "--fee-rate" in printed and "0.10%" in printed and "0.6%" in printed
    assert "--power-strengths" in printed


@pytest.mark.parametrize(
    "argv",
    [
        ["--synthetic", "null", "--events", "x.csv", "--out-dir", "o"],
        ["--data-dir", "d", "--calibrate-seeds", "3", "--out-dir", "o"],
        ["--synthetic", "null", "--calibrate-seeds", "0", "--out-dir", "o"],
        ["--synthetic", "nope", "--out-dir", "o"],
        ["--synthetic", "null", "--timeframe", "1h", "--out-dir", "o"],
        ["--out-dir", "o"],
        ["--synthetic", "null", "--fee-rate", "0.0001", "--out-dir", "o"],  # below the floor
        ["--synthetic", "null", "--fee-rate", "0.6", "--out-dir", "o"],  # a percent, not a fraction
        ["--synthetic", "null", "--fee-rate", "nan", "--out-dir", "o"],
        ["--synthetic", "null", "--fee-rate", "-0.001", "--out-dir", "o"],
        ["--synthetic", "null", "--slippage-pct", "0", "--out-dir", "o"],
        ["--synthetic", "null", "--slippage-pct", "inf", "--out-dir", "o"],
        ["--synthetic", "null", "--slippage-pct", "50", "--out-dir", "o"],
        ["--synthetic", "null", "--exchange-id", "", "--out-dir", "o"],
        ["--synthetic", "null", "--exchange-id", "bin ance", "--out-dir", "o"],
        ["--synthetic", "null", "--calibrate-seeds", "2", "--fee-rate", "0", "--out-dir", "o"],
        ["--synthetic", "planted", "--power-strengths", "0.3", "0.6", "--out-dir", "o"],
        ["--synthetic", "planted", "--effect-strength", "0.3", "--out-dir", "o"],
        ["--synthetic", "null", "--seed-offset", "5", "--out-dir", "o"],  # single run
        ["--synthetic", "null", "--calibrate-seeds", "2", "--seed-offset", "-1", "--out-dir", "o"],
        ["--synthetic", "null", "--stop-fill-wick-k", "0", "--out-dir", "o"],  # 0 = no stress
        ["--synthetic", "null", "--stop-fill-wick-k", "1.5", "--out-dir", "o"],
        ["--synthetic", "null", "--stop-fill-wick-k", "nan", "--out-dir", "o"],
        ["--synthetic", "null", "--calibrate-seeds", "2", "--ledger", "x", "--out-dir", "o"],
        [
            "--synthetic",
            "planted",
            "--calibrate-seeds",
            "2",
            "--power-strengths",
            "0.3",
            "--out-dir",
            "o",
        ],
        [
            "--synthetic",
            "planted",
            "--calibrate-seeds",
            "2",
            "--power-strengths",
            "0.3",
            "0.6",
            "--effect-strength",
            "0.3",
            "--out-dir",
            "o",
        ],
    ],
)
def test_cli_usage_errors(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        rr.main(argv)
    assert exc.value.code == 2


def test_missing_data_dir_is_a_clean_error(tmp_path: Path, capsys) -> None:
    code = rr.main(["--data-dir", str(tmp_path / "none"), "--out-dir", str(tmp_path / "o")])
    assert code == 2
    assert "fetch_data" in capsys.readouterr().err


# ---------------------------------------------------------------------------- D3 holdout ledger
def test_synthetic_run_appends_one_ledger_line_per_adoptable_look(null_run, null_research) -> None:
    out, _code, text = null_run
    looks = read_ledger(out / "test_looks.jsonl")  # D3 default for synthetic data
    targets = rr.adoption_targets(null_research)
    assert [look.variant for look in looks] == [r.variant for r in targets]
    assert len(looks) == len(list(out.glob("adoption_*.json"))) == 4
    ds = rr.load_synthetic("null", 1, 3.0)
    tf = StrategyConfig().timeframe_ms
    for look, r in zip(looks, targets, strict=True):
        ml = rp.fitted_model(r)
        assert look.config_fingerprint == config_fingerprint(r.cfg)
        assert look.model_fingerprint == (ml.fingerprint() if ml is not None else None)
        assert look.split_utc == ms_to_iso(null_research.split) and look.run_utc == NOW
        assert list(look.argv) == [*NULL_ARGV, "--out-dir", str(out)]
        assert [w.pair for w in look.pairs] == sorted(rr.PAIRS)
        for w in look.pairs:
            assert w.file_sha256 == series_sha256(ds.data[w.pair])
            assert (w.test_start_ts, w.test_end_ts) == (
                null_research.split,
                ds.data[w.pair][-1].ts + tf,
            )
    # context discovery variants never get a line
    context = {r.variant for r in null_research.discovery.results} - {r.variant for r in targets}
    assert context and not context & {look.variant for look in looks}
    verdict = _body(text, 6)
    assert "Holdout ledger (CONTRACT v4 D3): `test_looks.jsonl`" in verdict
    table = next(t for t in _tables(verdict) if "distinct TEST looks" in t[0])
    assert len(table) == 2 + 3
    for row in table[2:]:
        cells = _cells(row)
        assert (cells[3], cells[4], cells[5]) == ("4", "4", "within the limit")
    log = (out / rr.RUN_LOG).read_text(encoding="utf-8")
    assert "holdout ledger " in log and "BTC/USDT 4, ETH/USDT 4" in log


def test_records_carry_the_ledger_path(null_run) -> None:
    out, _code, _text = null_run
    for path in out.glob("adoption_*.json"):
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["ledger_path"] == "test_looks.jsonl", path  # relative to the record
        assert raw["manifest"] is None  # synthetic data has no manifest
        assert load_record(path).ledger_path == "test_looks.jsonl"
    ds = rr.load_synthetic("null", 1, 1.0)
    assert rr.evidence_fields(ds, out, out / "test_looks.jsonl") == {
        "ledger_path": "test_looks.jsonl",
        "manifest": None,
    }


def test_rerun_is_not_a_new_look_but_a_revision_is(tmp_path: Path, capsys) -> None:
    ledger = tmp_path / "shared.jsonl"
    argv = ["--synthetic", "null", "--seed", "2", "--years", "1.5", "--now", NOW]
    argv += ["--ledger", str(ledger)]

    def run(name: str, *extra: str) -> tuple[str, str]:
        assert rr.main([*argv, *extra, "--out-dir", str(tmp_path / name)]) == 0
        text = (tmp_path / name / rr.REPORT_NAME).read_text(encoding="utf-8")
        return _body(text, 6), capsys.readouterr().out

    verdict, _ = run("a")
    first = len(read_ledger(ledger))
    assert first >= 2 and not (tmp_path / "a" / "test_looks.jsonl").exists()
    assert f"| {first} | 4 | within the limit |" in verdict
    verdict, _ = run("b")  # identical candidates: lines appended, not a new look
    assert len(read_ledger(ledger)) == 2 * first
    assert f"| {first} | 4 | within the limit |" in verdict
    # new costs = new config fingerprints = new candidates on the SAME TEST window
    run("c", "--fee-rate", "0.002")
    verdict, printed = run("d", "--fee-rate", "0.003")
    n = 3 * first
    assert (
        len({(look.config_fingerprint, look.model_fingerprint) for look in read_ledger(ledger)})
        == n
    )
    assert n > 4 and f"| {n} | 4 | **OVER the limit (blocked)** |" in verdict
    assert "OVER the limit" in printed


# ---------------------------------------------------------------------------- D2 provenance
def test_manifest_verified_data_dir_is_real(tmp_path: Path) -> None:
    data, _events = make_world("planted", 4, years=1.5)
    d = tmp_path / "data"
    entries = []
    run = {
        "exchange_id": "binance",
        "ccxt_version": "4.3.0",
        "timeframe": "4h",
        "since_ms": 0,
        "until_ms": None,
        "fetched_at_utc": "2026-01-01T00:00:00Z",
    }
    for pair, candles in data.items():
        path = d / f"{pair.replace('/', '_')}-4h.csv"
        save_candles_csv(candles, path)
        entries.append(manifest_entry(path, pair, candles, "4h", run))
    manifest = write_manifest(d, run, list(data), entries)
    ds = rr.load_files(d)
    assert ds.provenance_id == "real"
    assert ds.manifest_path == manifest and ds.manifest_sha256 == sha256_file(manifest)
    assert any("**`real`**" in line and "matching sha256" in line for line in ds.provenance)
    out = tmp_path / "out"
    assert rr.main(["--data-dir", str(d), "--out-dir", str(out), "--now", NOW]) == 0
    for path in out.glob("adoption_*.json"):
        rec = load_record(path)
        assert rec.provenance == "real"
        raw = json.loads(path.read_text(encoding="utf-8"))
        ref = raw["manifest"]  # D2: the manifest path and sha256 are bound into every record
        assert sha256_file(out / ref["path"]) == ref["sha256"] == ds.manifest_sha256
        assert (out / raw["ledger_path"]).resolve() == (d / ".test_looks.jsonl").resolve()
    # tampering with one file after download makes the directory unverified
    btc = d / "BTC_USDT-4h.csv"
    btc.write_text(btc.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert rr.load_files(d).provenance_id == "unverified-csv"


# ---------------------------------------------------------------------------- D4 stress
def test_stop_fill_stress_run_is_test_only_with_the_k0_reference(tmp_path: Path, capsys) -> None:
    out = tmp_path / "stress"
    argv = ["--synthetic", "planted", "--seed", "3", "--years", "2", "--now", NOW]
    assert rr.main([*argv, "--stop-fill-wick-k", "0.5", "--out-dir", str(out)]) == 0
    capsys.readouterr()
    text = (out / rr.REPORT_NAME).read_text(encoding="utf-8")
    assert text.startswith("# Walk-forward research report: synthetic world `planted` seed 3, ")
    assert "STOP-FILL STRESS k = 0.5 (test-only)" in text.splitlines()[0]
    assert _sections(text) == list(rp.SECTION_TITLES)
    assert "Stop-fill STRESS model (CONTRACT v4 D4), k = 0.5" in _body(text, 2)
    assert list(out.glob("adoption_*.json")) == []  # test-only: no promotable record
    assert "No adoption record was written" in _body(text, 11)
    # ... and no human review pack (HUMAN_REVIEW is unreachable), but every journal is kept
    assert list(out.glob("review_*")) == []
    assert list((out / "journals").glob("base_*.csv"))
    assert "no pack for `base`" in _body(text, 10) and "ADOPT_test_only" in _body(text, 10)
    verdict = _body(text, 6)
    table = next(t for t in _tables(verdict) if "label changed" in t[0])
    assert "TRAIN" in table[0] and "TEST" in table[0] and len(table) == 2 + 4
    for row in table[2:]:
        cells = _cells(row)
        changed = "yes" if cells[9] != cells[10] else "no"
        assert cells[-1] in ("yes", "no") and cells[-1] == changed
        assert cells[2] in ("yes", "no", "-")
    # the k = 0 reference is a real TEST look of the adoptable candidates, so it is ledgered;
    # the stressed configs never are
    looks = read_ledger(out / "test_looks.jsonl")
    assert looks and all("wick" not in look.variant for look in looks)
    stressed = config_fingerprint(StrategyConfig(stop_fill_wick_k=0.5))
    assert stressed not in {look.config_fingerprint for look in looks}
    assert config_fingerprint(StrategyConfig()) in {look.config_fingerprint for look in looks}
    base_row = _cells(table[2])
    assert base_row[2] == "yes"  # the baseline is one config under two fill models
    for shift_col in (5, 8):  # judged TRAIN and TEST R shift = stressed - touch fill
        assert math.isclose(
            float(base_row[shift_col]),
            float(base_row[shift_col - 1].split()[0]) - float(base_row[shift_col - 2].split()[0]),
            abs_tol=2e-3,
        )


def test_stress_flag_reaches_the_config() -> None:
    args = rr._parser().parse_args(["--synthetic", "null", "--out-dir", "o"])
    assert rr.config_from_args(args).stop_fill_wick_k == 0.0
    args = rr._parser().parse_args(
        ["--synthetic", "null", "--out-dir", "o", "--stop-fill-wick-k", "1"]
    )
    cfg = rr.config_from_args(args)
    assert cfg.stop_fill_wick_k == 1.0 and cfg.is_test_only
    assert rr.cost_overrides(cfg) == (("stop_fill_wick_k", 1.0),)  # calibrations carry it


# ---------------------------------------------------------------------------- seeds
def test_seed_offset_runs_a_disjoint_seed_range(tmp_path: Path) -> None:
    assert list(rr.calibration_seeds(3, 10)) == [11, 12, 13]
    assert list(rr.calibration_seeds(2)) == [1, 2]
    with pytest.raises(ValueError):
        rr.calibration_seeds(0)
    out = tmp_path / "cal"
    argv = ["--synthetic", "decay", "--calibrate-seeds", "1", "--years", "1.0"]
    argv += ["--seed-offset", "100", "--workers", "1", "--out-dir", str(out)]
    assert rr.main(argv) == 0
    with (out / rr.CALIBRATION_CSV).open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["seed"] for r in rows] == ["101"]
    text = (out / rr.CALIBRATION_NAME).read_text(encoding="utf-8")
    assert text.startswith("# Calibration: synthetic world `decay`, seed 101")
    assert rp.SEED_NOTE in text
    assert rp.seed_range([{"seed": 3}, {"seed": 1}, {"seed": 2}]) == "seeds 1-3"
    assert rp.seed_range([{"seed": 1}, {"seed": 5}]) == "seeds 1, 5"


# ---------------------------------------------------------------------------- D8 in the report
def test_ml_layer_is_reported_and_recorded_on_its_out_of_sample_gate(
    null_run, null_research
) -> None:
    out, _code, text = null_run
    ml = null_research.ml
    gate = ml.gate
    assert gate is not None
    layers = _body(text, 5)
    assert rp.GATE_WORDING in layers
    table = next(t for t in _tables(layers) if t[0].startswith("| metric |"))
    assert "base+ml TRAIN (in-sample)" in table[0]
    assert "base+ml TRAIN (out-of-sample inner split)" in table[0]
    if gate.ran:
        assert "**Out-of-sample TRAIN gate (CONTRACT v4 D8):** the" in layers
    else:
        assert "Out-of-sample TRAIN gate (CONTRACT v4 D8): not evaluable" in layers
    # the record's TRAIN journal and review pack hold the gate trades, TEST offset past them
    train = read_journal(out / "journals" / "base_plus_ml_train.csv")
    insample = read_journal(out / "journals" / "base_plus_ml_train_insample.csv")
    assert [t.signal_ts for t in train] == [t.signal_ts for t in ml.label_train.trades]
    assert [t.signal_ts for t in insample] == [t.signal_ts for t in ml.train.trades]
    with (out / "review_base_plus_ml" / "trades_review.csv").open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == ml.label_train_summary.n + ml.test_summary.n
    assert "in-sample full-TRAIN journal (context)" in _body(text, 10)
    assert "_train_insample.csv" in _body(text, 10)
    # section 6 judges the gate
    cand = next(t for t in _tables(_body(text, 6)) if "TRAIN n (judged)" in t[0])
    ml_row = next(_cells(r) for r in cand[2:] if "base+ml" in r)
    assert int(ml_row[1]) == ml.label_train_summary.n


# ---------------------------------------------------------------------------- report split
def test_report_is_split_with_re_exports() -> None:
    for name in rp.__all__:
        assert hasattr(rp, name), name
    assert rp.render_calibration is report_calibration.render_calibration
    assert rp.render_power is report_calibration.render_power
    assert rp.metric_table is report_tables.metric_table and rp.DD_RULE is report_tables.DD_RULE
    assert rr.verdict_line is rp.verdict_line
    assert rp.section_adoption is report_evidence.section_adoption
    for module in (rp, report_calibration, report_evidence, report_tables):
        lines = Path(module.__file__).read_text(encoding="utf-8").count("\n")
        assert lines < 1000, (module.__name__, lines)
