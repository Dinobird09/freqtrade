"""run_research: one command writes REPORT.md (12 sections), journals, packs, adoption records."""

from __future__ import annotations

import csv
import re
from dataclasses import replace
from pathlib import Path

import pytest

from research.trendbot import run_research as rr
from research.trendbot.adoption import HUMAN_REVIEW, check_promotion, load_record
from research.trendbot.config import StrategyConfig
from research.trendbot.data import save_candles_csv
from research.trendbot.journal import iso_to_ms, read_journal
from research.trendbot.synthetic import make_world


NOW = "2026-01-01T00:00:00Z"
EVENTS_EXAMPLE = Path(__file__).resolve().parents[1] / "events_example.csv"


@pytest.fixture(scope="module")
def null_run(tmp_path_factory) -> tuple[Path, int, str]:
    out = tmp_path_factory.mktemp("null_s1")
    code = rr.main(
        ["--synthetic", "null", "--seed", "1", "--years", "3", "--out-dir", str(out), "--now", NOW]
    )
    return out, code, (out / rr.REPORT_NAME).read_text(encoding="utf-8")


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


def test_every_results_table_has_train_and_test_columns(null_run) -> None:
    _out, _code, text = null_run
    checked = 0
    for number in (3, 4, 5, 7, 8):
        for table in _tables(_body(text, number)):
            header = table[0]
            if "feature" in header:  # the ML coefficient table is not a results table
                continue
            assert "TRAIN" in header and "TEST" in header, header
            checked += 1
    assert checked >= 5
    assert "win rate (context only, never a target)" in _body(text, 3)
    assert "in-sample" in _body(text, 5)


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


def test_data_dir_without_events_flags_r5(tmp_path: Path) -> None:
    d = _write_files(tmp_path)
    out = tmp_path / "out"
    assert rr.main(["--data-dir", str(d), "--out-dir", str(out), "--now", NOW]) == 0
    text = (out / rr.REPORT_NAME).read_text(encoding="utf-8")
    prov = _body(text, 2)
    assert "News calendar loaded: **NO**" in prov
    assert "R5 (news blackout) could NOT be exercised historically" in prov
    assert "| BTC/USDT |" in prov and "gaps" in prov
    assert "SYNTHETIC" not in prov
    assert _sections(text) == list(rr.SECTION_TITLES)


def test_data_dir_with_events(tmp_path: Path) -> None:
    ds = rr.load_files(_write_files(tmp_path), events_path=EVENTS_EXAMPLE)
    assert ds.kind == "files" and ds.events
    assert any("News calendar loaded: yes" in line for line in ds.provenance)


# ---------------------------------------------------------------------------- calibration
def test_calibration_writes_label_frequencies(tmp_path: Path) -> None:
    out = tmp_path / "cal"
    args = ["--synthetic", "decay", "--calibrate-seeds", "2", "--years", "1.5"]
    assert rr.main([*args, "--workers", "1", "--out-dir", str(out)]) == 0
    text = (out / rr.CALIBRATION_NAME).read_text(encoding="utf-8")
    assert "## Label frequencies" in text and "FALSE-POSITIVE rate" in text
    assert "not evidence about real markets" in text
    with (out / rr.CALIBRATION_CSV).open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["seed"] for r in rows] == ["1", "2"]
    assert all(r["violations"] == "0" for r in rows)
    # a 1.5-year history is too short for the ML layer: the InsufficientData path
    assert all(r["ml_label"] == "UNTESTED" and r["ml_fit_error"] for r in rows)


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
