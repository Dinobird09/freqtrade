"""run_research: one command writes REPORT.md (12 sections), journals, packs, adoption records."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import pickle
import re
import shlex
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from research.trendbot import journal_rules
from research.trendbot import run_research as rr
from research.trendbot.adoption import (
    HUMAN_REVIEW,
    check_promotion,
    config_fingerprint,
    load_config,
    load_record,
)
from research.trendbot.adoption import main as adoption_main
from research.trendbot.backtester import enumerate_candidates
from research.trendbot.config import PairRisk, StrategyConfig
from research.trendbot.data import save_candles_csv
from research.trendbot.journal import iso_to_ms, ms_to_iso, read_journal
from research.trendbot.ml_filter import MLFilter
from research.trendbot.models import EXIT_SL, EXIT_TP, HOUR_MS, Trade
from research.trendbot.synthetic import make_world


NOW = "2026-01-01T00:00:00Z"
EVENTS_EXAMPLE = Path(__file__).resolve().parents[1] / "events_example.csv"


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
        code = rr.main(
            ["--synthetic", "null", "--seed", "1", "--years", "3", "--out-dir", str(out)]
            + ["--now", NOW]
        )
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
    start = text.index(f"## {rr.SECTION_TITLES[number - 1]}")
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


# ---------------------------------------------------------------------------- report
def test_report_has_all_twelve_sections_in_order(null_run) -> None:
    _out, code, text = null_run
    assert code == 0
    assert _sections(text) == list(rr.SECTION_TITLES)
    assert len(rr.SECTION_TITLES) == 12


def test_null_world_reports_no_robust_result(null_run) -> None:
    _out, _code, text = null_run
    verdict = _body(text, 6)
    assert rr.NO_ROBUST in verdict.splitlines()
    assert "ROBUST:" not in verdict


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
    assert "SYNTHETIC" in prov and "seed 1" in prov
    assert "not evidence about real markets" in prov
    assert "Ground truth" in prov and "martingale" in prov
    assert "News calendar loaded: yes" in prov


def test_every_table_header_labels_train_and_test(null_run) -> None:
    _out, _code, text = null_run
    tables = _tables(text)
    assert len(tables) >= 12
    for table in tables:
        header = table[0]
        assert "TRAIN" in header and "TEST" in header, header
    for number in (2, 3, 4, 5, 7, 8, 9, 10, 11):
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
    assert "TRAIN candles (before the split)" in table[0] and len(table) == 2 + 3
    for row in table[2:]:
        total, n_train, n_test = (int(c) for c in row.split("|")[2:5])
        assert n_train + n_test == total and n_train > 2 * n_test


def test_discovery_section_marks_selection_k_and_test_only(null_run) -> None:
    _out, _code, text = null_run
    disc = _body(text, 4)
    assert "K = 13 variants tried" in disc
    assert disc.count("**SELECTED**") == 1
    assert disc.count("TEST-ONLY (regime OFF, never selectable)") == 1
    assert "turn TEST into TRAIN" in disc
    assert disc.index("selection recorded") < disc.index("TEST backtests")


def test_ml_section_explains_the_model(null_run) -> None:
    _out, _code, text = null_run
    ml = _body(text, 5)
    assert "p* =" in ml and "standardised coefficient" in ml
    assert "LightGBM" in ml and "NOT justified" in ml
    assert "TRAIN candidates" in ml
    assert "**Label:" in ml


def test_invariants_clean_and_journal_audit_present(null_run) -> None:
    _out, _code, text = null_run
    assert "Result: CLEAN. 0 violations in 30 backtests." in _body(text, 8)
    audit = _body(text, 9)
    assert "journal_rules.audit" in audit and "baseline `base`" in audit
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
    fake = SimpleNamespace(
        cfg=cfg,
        split_ts=split,
        train=SimpleNamespace(trades=[], final_equity=10_000.0),
        test=SimpleNamespace(trades=test_trades, final_equity=9_993.1),
    )
    (w1, at1, _eq1, train_acts), (w2, at2, eq2, test_acts) = rr.journal_audits(fake, end)
    assert (w1, at1, train_acts) == ("TRAIN", split, [])
    assert (w2, at2, eq2) == ("TEST", end, 9_993.1)
    benches = [a for a in test_acts if a.scope == "BTC/USDT"]
    assert len(benches) == 1 and benches[0].evidence_trade_ids == (1, 2, 3)
    until = last_exit + tf + 24 * HOUR_MS  # 24h from the CLOSE of the exit candle
    assert benches[0].action == f"no entries until {ms_to_iso(until)}"
    live = journal_rules.audit(test_trades, cfg, end, 9_993.1)  # live convention: 0
    assert live[0].action == f"no entries until {ms_to_iso(last_exit + 24 * HOUR_MS)}"


def test_output_files_journals_packs_and_adoption(null_run) -> None:
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
    rec = load_record(out / "adoption_base.json")
    assert rec.walk_forward.label in ("NO-EDGE", "TRAIN-ONLY", "UNTESTED")
    assert rec.walk_forward.train_n == len(base_train)
    assert rec.walk_forward.test_n == len(base_test)
    assert rec.backtest.completed_utc == NOW
    blocked = check_promotion(rec, HUMAN_REVIEW, StrategyConfig(), iso_to_ms(NOW), out)
    assert blocked and all(d.rule == "ADOPT_walk_forward" for d in blocked)
    adoption = _body(text, 11)
    assert "Stage reached by this run: **WALK_FORWARD**" in adoption
    assert "Next: **HUMAN_REVIEW**" in adoption and "BLOCKED" in adoption
    assert "Binance testnet" in adoption and ">= 2 weeks" in adoption


def test_results_stay_lean_journals_only_for_the_candidates(null_run) -> None:
    out, _code, text = null_run
    selected = re.search(r"discovery-selected `([^`]+)`", _body(text, 6))
    assert selected is not None
    safe = [rr.BASE, selected.group(1), "base_plus_ml"]
    expected = {f"{v}_{w}.csv" for v in safe for w in ("train", "test")}
    assert {p.name for p in (out / "journals").iterdir()} == expected
    assert sorted(p.name for p in out.glob("review_*")) == sorted(
        ["review_base", "review_base_plus_ml", f"review_selected_{selected.group(1)}"]
    )
    assert "no journal is kept for them" in _body(text, 10)


def test_ml_adoption_record_pins_the_fitted_model(null_run) -> None:
    out, _code, text = null_run
    rec = load_record(out / "adoption_base_plus_ml.json")
    assert rec.variant == rr.ML_VARIANT
    model_file = out / "model_base_plus_ml.json"
    fp = hashlib.sha256(model_file.read_bytes()).hexdigest()
    assert rec.model_fingerprint == fp
    assert "Model fingerprint (sha256 of features" in _body(text, 5) and fp in _body(text, 5)
    # The same TRAIN candidates always refit to exactly the recorded model.
    ds = rr.load_synthetic("null", 1, 3.0)
    split = rr.split_ts(ds.data, rr.TRAIN_FRAC, StrategyConfig().timeframe_ms)
    cands = enumerate_candidates(ds.data, StrategyConfig(), ds.events, end_ts=split)
    refit = MLFilter.fit(cands)
    assert refit.fingerprint() == fp and refit.canonical_json() == model_file.read_text("utf-8")
    now = iso_to_ms(NOW)
    ok = check_promotion(rec, HUMAN_REVIEW, StrategyConfig(), now, out, fp)
    assert ok and {d.rule for d in ok} == {"ADOPT_walk_forward"}
    for wrong in (None, "0" * 64, MLFilter.fit(cands[:-1]).fingerprint()):
        rules = {
            d.rule for d in check_promotion(rec, HUMAN_REVIEW, StrategyConfig(), now, out, wrong)
        }
        assert "ADOPT_fingerprint" in rules, wrong
    adoption = _body(text, 11)
    assert (
        "Refitting the model" in adoption and "restarts the adoption path at BACKTEST" in adoption
    )
    assert f"--model-fingerprint {fp}" in adoption
    assert "`base+ml` | " in adoption  # row of the adoption table


def test_adoption_check_commands_in_the_report_run(null_run, capsys) -> None:
    _out, _code, text = null_run
    commands = re.findall(r"Check: `(python -m research\.trendbot\.adoption check [^`]+)`", text)
    assert len(commands) == 3
    for command in commands:
        argv = shlex.split(command)[3:]
        assert adoption_main([*argv, "--now", NOW]) == 1  # null world: blocked, but ...
        printed = capsys.readouterr().out
        assert "ADOPT_walk_forward" in printed
        assert "ADOPT_fingerprint" not in printed  # ... config and model match what was tested


# ---------------------------------------------------------------------------- verdict
def test_verdict_lists_robust_candidates_only() -> None:
    data, events = make_world("null", 2, years=2.0)
    research = rr.run_pipeline(data, events, min_train=5, min_test=5)
    assert rr.verdict_line(research) == rr.NO_ROBUST or research.robust()
    selected = research.discovery.selection.variant
    assert selected is not None
    # non-selected grid variants never enter the verdict, whatever their TEST label
    for r in research.discovery.results:
        r.label = "NO-EDGE" if r.variant == selected else "ROBUST"
    research.base = replace(research.base, label="NO-EDGE")
    research.ml = replace(research.ml, label="NO-EDGE")
    assert rr.verdict_line(research) == rr.NO_ROBUST
    research.base = replace(research.base, label="ROBUST")
    line = rr.verdict_line(research)
    assert line.startswith("ROBUST: baseline `base`") and line.endswith(".")
    assert "ML layer" not in line and "discovery-selected" not in line


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
    assert _sections(text) == list(rr.SECTION_TITLES)
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


def test_cost_lines_warn_when_a_non_binance_run_keeps_the_binance_fee() -> None:
    lines = " ".join(rr.cost_lines(StrategyConfig(exchange_id="coinbase")))
    assert "WARNING: exchange `coinbase` was run with the Binance default fee" in lines
    assert "WARNING" not in " ".join(
        rr.cost_lines(StrategyConfig(exchange_id="coinbase", fee_rate=0.006))
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


def test_calibration_jobs_carry_the_costs(monkeypatch) -> None:
    job = ("null", 1, 0.5, rr.cost_overrides(StrategyConfig(fee_rate=0.002)))
    assert pickle.loads(pickle.dumps(job)) == job  # noqa: S301 - own data; process-pool safe
    seen: list[StrategyConfig] = []

    class Stop(Exception):
        pass

    def fake_pipeline(data, events, cfg=None, **_kw):  # type: ignore[no-untyped-def]
        seen.append(cfg)
        raise Stop

    monkeypatch.setattr(rr, "run_pipeline", fake_pipeline)
    with pytest.raises(Stop):
        rr.calibrate_one(job)
    assert seen[0].fee_rate == 0.002 and seen[0].slippage_pct == StrategyConfig().slippage_pct


def test_data_dir_with_events(tmp_path: Path) -> None:
    ds = rr.load_files(_write_files(tmp_path), events_path=EVENTS_EXAMPLE)
    assert ds.kind == "files" and ds.events
    assert any("News calendar loaded: yes" in line for line in ds.provenance)


# ---------------------------------------------------------------------------- calibration
def test_calibration_writes_label_frequencies(tmp_path: Path) -> None:
    out = tmp_path / "cal"
    args = ["--synthetic", "decay", "--calibrate-seeds", "2", "--years", "1.5"]
    assert rr.main([*args, "--workers", "1", "--out-dir", str(out), "--fee-rate", "0.0012"]) == 0
    text = (out / rr.CALIBRATION_NAME).read_text(encoding="utf-8")
    assert "## Label frequencies" in text and "FALSE-POSITIVE rate" in text
    assert "not evidence about real markets" in text
    assert "fee 0.120% of notional per side (`--fee-rate 0.0012`)" in text
    assert "Baseline labelled TRAIN-ONLY:" in text and "UNTESTED:" in text
    for table in _tables(text):
        assert "TRAIN" in table[0] and "TEST" in table[0], table[0]
    with (out / rr.CALIBRATION_CSV).open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0]) == list(rr.CAL_FIELDS)
    assert [r["seed"] for r in rows] == ["1", "2"]
    assert all(r["violations"] == "0" for r in rows)
    # a 1.5-year history is too short for the ML layer: the InsufficientData path
    assert all(r["ml_label"] == "UNTESTED" and r["ml_fit_error"] for r in rows)
    assert all(r["ml_fingerprint"] == "" and r["ml_top_feature"] == "" for r in rows)


def test_calibration_ml_columns_describe_the_fitted_model(null_research) -> None:
    research = null_research
    row = rr._cal_row(1, research)
    ml = rr.fitted_model(research.ml)
    assert ml is not None and row["ml_fingerprint"] == ml.fingerprint()
    coefs = dict(ml.explain())
    assert row["ml_hour_sin"] == round(coefs["hour_sin"], 4)
    assert row["ml_top_feature"] == max(coefs, key=lambda k: abs(coefs[k]))
    assert row["ml_train_avg_r_in_sample"] == round(research.ml.train_summary.avg_r, 4)
    line = rr._ml_hour_line([row])
    assert line is not None and "1 fitted seeds" in line
    assert math.isfinite(float(line.split("= ")[-1].split()[0]))


def test_wilson_interval() -> None:
    lo, hi = rr.wilson(0, 20)
    assert lo == 0.0 and 0.0 < hi < 0.2
    lo, hi = rr.wilson(10, 20)
    assert lo < 0.5 < hi
    assert rr.wilson(0, 0) == (0.0, 1.0)


# ---------------------------------------------------------------------------- CLI errors
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
