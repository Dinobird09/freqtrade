import csv
import dataclasses
import inspect
import json
import math
import statistics
from pathlib import Path
from types import MappingProxyType

import pytest

from research.trendbot import metrics
from research.trendbot.adoption import (
    ADOPT_BACKTEST,
    ADOPT_FINGERPRINT,
    ADOPT_HUMAN_REVIEW,
    ADOPT_LIVE,
    ADOPT_PROVENANCE,
    ADOPT_RECORD,
    ADOPT_TESTNET,
    ADOPT_WALK_FORWARD,
    ADOPTION_RULE_IDS,
    BACKTEST,
    DD_CAP_PCT,
    HUMAN_REVIEW,
    LIVE,
    STAGE_RULES,
    STAGE_SECTIONS,
    STAGES,
    TESTNET,
    TESTNET_EXCHANGE,
    WALK_FORWARD,
    AdoptionBlocked,
    AdoptionRecord,
    BacktestStage,
    FileRef,
    HumanReviewStage,
    LiveStage,
    TestnetStage,
    WalkForwardStage,
    canonical_config_json,
    check_promotion,
    config_fingerprint,
    config_from_overrides,
    empty_record,
    load_record,
    main,
    recompute_walk_forward,
    require_stage,
    route_label_params,
    save_record,
    sha256_file,
    unverified_notes,
)
from research.trendbot.config import RULE_IDS, ConfigError, PairRisk, StrategyConfig
from research.trendbot.journal import import_external, iso_to_ms, write_journal
from research.trendbot.models import DAY_MS, EXIT_SL, EXIT_TP, HOUR_MS, Decision, Trade
from research.trendbot.review_sheet import (
    REVIEW_OK_BLANK,
    REVIEW_OK_NO,
    REVIEW_OK_YES,
    auto_flags,
    write_review_pack,
)


CFG = StrategyConfig()
NOW_ISO = "2024-07-01T00:00:00Z"
NOW = iso_to_ms(NOW_ISO)
TESTNET_START = iso_to_ms("2024-04-01T00:00:00Z")
SPLIT_ISO = "2023-01-01T00:00:00Z"
SPLIT = iso_to_ms(SPLIT_ISO)
TRAIN_START = iso_to_ms("2021-01-01T00:00:00Z")
N_TRAIN, N_TEST = 32, 30
N_ALL = N_TRAIN + N_TEST

# Evidence layout, relative to the record's directory (the CLI's base_dir).
DATA_FILE = "data/BTC_USDT-4h.csv"
EVENTS = "data/events.csv"
BT_REPORT = "bt/REPORT.md"
WF_REPORT = "wf/REPORT.md"
TRAIN_JOURNAL = "wf/journals/train.csv"
TEST_JOURNAL = "wf/journals/test.csv"
REVIEW_DIR = "review"
REVIEW_CSV = "review/trades_review.csv"
JOURNAL = "testnet/trades.csv"
TESTNET_EVENTS = "testnet/events.csv"


# ---------------------------------------------------------------------------- fixtures
def a1_levels(entry: float, stop: float, cfg: StrategyConfig = CFG) -> tuple[float, float]:
    """(all-in loss per unit L_u, target T) exactly as CONTRACT.md v2 A1 defines them."""
    f, s = cfg.fee_rate, cfg.slippage_pct / 100
    stop_x = stop * (1 - s)
    loss_per_unit = (entry - stop_x) + f * entry + f * stop_x
    return loss_per_unit, (entry * (1 + f) + cfg.reward_risk * loss_per_unit) / (1 - f)


def closed_trade(
    trade_id: int,
    signal_ts: int,
    win: bool,
    *,
    variant: str = "base",
    cfg: StrategyConfig = CFG,
    pair: str = "BTC/USDT",
    hold_h: int = 12,
    **changes: object,
) -> Trade:
    """A clean long with A1 cost-aware size and target: a TP is exactly +reward_risk R, an
    SL (filled at stop * (1 - slippage)) exactly -1R; risk ~0.1% of equity."""
    entry, stop, qty = 100.0, 95.0, 2.0
    loss_per_unit, target = a1_levels(entry, stop, cfg)
    risk_amount = qty * loss_per_unit
    exit_price = target if win else stop * (1 - cfg.slippage_pct / 100)
    fees = cfg.fee_rate * qty * (entry + exit_price)
    pnl = qty * (exit_price - entry) - fees
    t = Trade(
        trade_id=trade_id,
        pair=pair,
        variant=variant,
        signal_ts=signal_ts,
        entry_ts=signal_ts + 4 * HOUR_MS,
        entry_price=entry,
        stop=stop,
        target=target,
        qty=qty,
        risk_amount=risk_amount,
        risk_pct=risk_amount / cfg.starting_capital * 100,
        stop_method="pivot",
        exit_ts=signal_ts + hold_h * HOUR_MS,
        exit_price=exit_price,
        exit_reason=EXIT_TP if win else EXIT_SL,
        fees=fees,
        pnl=pnl,
        r_multiple=pnl / risk_amount,
    )
    return dataclasses.replace(t, **changes)


def make_testnet_trade(
    k: int, variant: str = "base", cfg: StrategyConfig = CFG, **changes: object
) -> Trade:
    """A clean BTC take-profit entered k days into the testnet window."""
    return closed_trade(
        k + 1, TESTNET_START + k * DAY_MS, True, variant=variant, cfg=cfg, **changes
    )


def wf_trades(variant: str = "base", cfg: StrategyConfig = CFG) -> tuple[list[Trade], list[Trade]]:
    """A strong edge in both windows (TRAIN 3 TP : 1 SL, TEST 4 TP : 1 SL), clearly ROBUST."""
    train = [
        closed_trade(i + 1, TRAIN_START + i * 3 * DAY_MS, i % 4 != 3, variant=variant, cfg=cfg)
        for i in range(N_TRAIN)
    ]
    test = [
        closed_trade(
            N_TRAIN + i + 1, SPLIT + DAY_MS + i * 3 * DAY_MS, i % 5 != 4, variant=variant, cfg=cfg
        )
        for i in range(N_TEST)
    ]
    return train, test


def weak_test_trades(variant: str = "base") -> list[Trade]:
    """30 TEST trades, 11 TP / 19 SL: mean +0.1R, positive but not distinguishable from 0."""
    return [
        closed_trade(
            N_TRAIN + i + 1, SPLIT + DAY_MS + i * 3 * DAY_MS, (i * 11) % 30 < 11, variant=variant
        )
        for i in range(N_TEST)
    ]


def mean_r(trades: list[Trade]) -> float:
    return statistics.fmean(t.r_multiple for t in trades if t.r_multiple is not None)


def fill_review(path: Path, verdicts: dict[tuple[str, int], str] | None = None) -> None:
    """Enter the reviewer's verdicts into a trades_review.csv (default: every row Y)."""
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    header = rows[0]
    ok, window, trade_id = (header.index(c) for c in ("reviewer_ok", "window", "trade_id"))
    for row in rows[1:]:
        row[ok] = (verdicts or {}).get((row[window], int(row[trade_id])), REVIEW_OK_YES)
    with path.open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh, lineterminator="\n").writerows(rows)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def build_evidence(
    root: Path,
    cfg: StrategyConfig = CFG,
    variant: str = "base",
    *,
    prefix: str = "",
    train: list[Trade] | None = None,
    test: list[Trade] | None = None,
    testnet: list[Trade] | None = None,
    verdicts: dict[tuple[str, int], str] | None = None,
) -> AdoptionRecord:
    """Write every evidence file under ``root / prefix`` and return the complete record
    (real provenance, every file hash-bound, typed walk-forward numbers from the journals,
    an all-Y review pack unless ``verdicts`` say otherwise, 14 days of clean testnet)."""
    d = root / prefix if prefix else root

    def rel(path: str) -> str:
        return f"{prefix}/{path}" if prefix else path

    def sha(path: str) -> str:
        return sha256_file(d / path)

    default_train, default_test = wf_trades(variant, cfg)
    train = default_train if train is None else train
    test = default_test if test is None else test
    testnet = (
        [make_testnet_trade(k, variant, cfg) for k in range(3)] if testnet is None else testnet
    )
    write_text(d / DATA_FILE, "ts,open,high,low,close,volume\n0,1,1,1,1,1\n")
    write_text(d / EVENTS, "time_utc,scope,impact,kind,note\n")
    write_text(d / BT_REPORT, "# backtest report\n")
    write_text(d / WF_REPORT, "# walk-forward report\n")
    write_journal(train, d / TRAIN_JOURNAL)
    write_journal(test, d / TEST_JOURNAL)
    pack = write_review_pack(train + test, d / REVIEW_DIR, "fixture", cfg, split_ts=SPLIT)
    fill_review(pack["csv"], verdicts)
    write_journal(testnet, d / JOURNAL)
    n = len(train) + len(test)
    return AdoptionRecord(
        variant=variant,
        config_fingerprint=config_fingerprint(cfg),
        provenance="real",
        data_files={rel(DATA_FILE): sha(DATA_FILE)},
        events_file=FileRef(rel(EVENTS), sha(EVENTS)),
        backtest=BacktestStage(
            report_path=rel(BT_REPORT),
            completed_utc="2024-03-01T00:00:00Z",
            report_sha256=sha(BT_REPORT),
        ),
        walk_forward=WalkForwardStage(
            label="ROBUST",
            dd_ok=True,
            split_utc=SPLIT_ISO,
            train_n=len(train),
            test_n=len(test),
            test_avg_r=mean_r(test) if test else None,
            report_path=rel(WF_REPORT),
            train_journal_path=rel(TRAIN_JOURNAL),
            train_journal_sha256=sha(TRAIN_JOURNAL),
            test_journal_path=rel(TEST_JOURNAL),
            test_journal_sha256=sha(TEST_JOURNAL),
            min_train=30,
            min_test=30,
            train_avg_r=mean_r(train) if train else None,
            label_params={},
        ),
        human_review=HumanReviewStage(
            reviewer="Jane Doe",
            date_utc="2024-03-10T00:00:00Z",
            trades_reviewed=n,
            trades_total=n,
            approved=True,
            notes="checked every row against the charts",
            review_path=rel(REVIEW_CSV),
            review_sha256=sha(REVIEW_CSV),
        ),
        testnet=TestnetStage(
            exchange=TESTNET_EXCHANGE,
            start_utc="2024-04-01T00:00:00Z",
            end_utc="2024-04-15T00:00:00Z",
            trades=len(testnet),
            rule_violations=0,
            journal_path=rel(JOURNAL),
            notes="",
        ),
    )


@pytest.fixture(scope="module")
def base(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One evidence directory shared by the tests that do not modify files."""
    return tmp_path_factory.mktemp("evidence")


_BUILT: dict[tuple[str, str, str], AdoptionRecord] = {}


def valid_record(base: Path, cfg: StrategyConfig = CFG, variant: str = "base") -> AdoptionRecord:
    """Every stage complete with hash-bound evidence; testnet lasts EXACTLY 14 days."""
    fp = config_fingerprint(cfg)
    key = (str(base), variant, fp)
    if key not in _BUILT:
        prefix = f"{variant.replace('+', '_plus_')}_{fp[:12]}"
        _BUILT[key] = build_evidence(base, cfg, variant, prefix=prefix)
    return _BUILT[key]


def check(
    base: Path,
    record: AdoptionRecord,
    stage: str,
    cfg: StrategyConfig = CFG,
    model_fingerprint: str | None = None,
) -> list[Decision]:
    return check_promotion(record, stage, cfg, NOW, base, model_fingerprint)


def mutate(record: AdoptionRecord, section: str, **changes: object) -> AdoptionRecord:
    return dataclasses.replace(
        record, **{section: dataclasses.replace(getattr(record, section), **changes)}
    )


def assert_one_sentence(text: str) -> None:
    assert text.endswith("."), text
    assert ". " not in text and "\n" not in text, text


def blocking_rules(decisions: list[Decision]) -> set[str]:
    """Check the Decision invariants and return the set of rule ids."""
    for d in decisions:
        assert isinstance(d, Decision)
        assert d.allowed is False
        assert d.rule in ADOPTION_RULE_IDS, d.rule
        assert_one_sentence(d.reason)
    return {d.rule for d in decisions}


def reasons(decisions: list[Decision]) -> str:
    return " | ".join(d.reason for d in decisions)


def rehash(record: AdoptionRecord, root: Path, section: str, path_field: str) -> AdoptionRecord:
    """Record the CURRENT sha256 of a (deliberately edited) evidence file."""
    sha_field = path_field.replace("_path", "_sha256")
    path = getattr(getattr(record, section), path_field)
    return mutate(record, section, **{sha_field: sha256_file(root / path)})


LATER_THAN_WF = (HUMAN_REVIEW, TESTNET, LIVE)
LATER_THAN_REVIEW = (TESTNET, LIVE)


def test_fixture_trades_are_clean_under_the_review_flags():
    for win in (True, False):
        t = closed_trade(1, TESTNET_START, win)
        assert auto_flags(t, CFG) == []
        assert math.isclose(t.r_multiple, CFG.reward_risk if win else -1.0, rel_tol=1e-12)


def test_fixture_walk_forward_is_robust_under_the_current_metrics():
    train, test = wf_trades()
    verdict = recompute_walk_forward(train, test, CFG, 30, 30, {})
    assert verdict.label == "ROBUST" and verdict.dd_ok is True
    weak = recompute_walk_forward(train, weak_test_trades(), CFG, 30, 30, {})
    assert weak.label == "UNTESTED" and weak.test.avg_r > 0


# ---------------------------------------------------------------------------- fingerprint
def test_fingerprint_is_sha256_hex_and_deterministic():
    fp = config_fingerprint(CFG)
    assert len(fp) == 64 and int(fp, 16) >= 0
    assert fp == config_fingerprint(StrategyConfig())


def test_canonical_json_covers_every_field_with_sorted_keys():
    text = canonical_config_json(CFG)
    data = json.loads(text)
    assert set(data) == {f.name for f in dataclasses.fields(StrategyConfig)}
    assert list(data) == sorted(data)
    assert data["pair_risk"]["BNB"] == {"max_risk_pct": 0.5, "stop_buffer_pct": 0.65}


def test_fingerprint_ignores_representation_only_differences():
    assert config_fingerprint(StrategyConfig(reward_risk=2)) == config_fingerprint(CFG)
    plain = dict(CFG.pair_risk)
    assert config_fingerprint(StrategyConfig(pair_risk=plain)) == config_fingerprint(CFG)
    proxied = MappingProxyType(dict(reversed(list(plain.items()))))
    assert config_fingerprint(StrategyConfig(pair_risk=proxied)) == config_fingerprint(CFG)


@pytest.mark.parametrize(
    "changes",
    [
        {"reward_risk": 2.5},
        {"vol_mult": 2.0},
        {"rsi_min": 55.0},
        {"regime_filter": False},
        {"expectancy_guard": True},
        {"weekly_loss_limit_pct": 2.0},
        {"fee_rate": 0.00075},
        {"starting_capital": 5_000.0},
        {"news_blackout_hours": 3.0},
    ],
)
def test_fingerprint_changes_with_any_field(changes):
    assert config_fingerprint(CFG.with_changes(**changes)) != config_fingerprint(CFG)


def test_fingerprint_includes_pair_risk():
    risk = dict(CFG.pair_risk)
    risk["BNB"] = PairRisk(max_risk_pct=0.5, stop_buffer_pct=0.7)
    assert config_fingerprint(StrategyConfig(pair_risk=risk)) != config_fingerprint(CFG)
    risk["BNB"] = PairRisk(max_risk_pct=0.4, stop_buffer_pct=0.65)
    assert config_fingerprint(StrategyConfig(pair_risk=risk)) != config_fingerprint(CFG)


def test_sha256_file_hashes_the_bytes(tmp_path: Path):
    path = tmp_path / "f.txt"
    path.write_bytes(b"hello\n")
    assert sha256_file(path) == "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"


# ---------------------------------------------------------------------------- config overrides
def test_config_from_overrides_applies_legal_tightenings():
    cfg = config_from_overrides(
        {
            "reward_risk": 3,
            "vol_mult": 2.0,
            "bnb_event_kinds": ["bnb_burn", "launchpool", "other"],
            "pair_risk": {
                "BTC": {"max_risk_pct": 0.5, "stop_buffer_pct": 0.25},
                "ETH": {"max_risk_pct": 1.0, "stop_buffer_pct": 0.25},
                "BNB": {"max_risk_pct": 0.5, "stop_buffer_pct": 0.8},
            },
        }
    )
    assert cfg.reward_risk == 3.0 and isinstance(cfg.reward_risk, float)
    assert cfg.bnb_event_kinds == ("bnb_burn", "launchpool", "other")
    assert cfg.pair_risk["BNB"] == PairRisk(0.5, 0.8)
    assert config_from_overrides({}) == CFG


@pytest.mark.parametrize(
    "overrides",
    [
        {"reward_risk": 1.5},  # loosens the 2:1 minimum
        {"rsi_max": 75.0},  # loosens R2
        {"pair_risk": {"BNB": {"max_risk_pct": 1.0, "stop_buffer_pct": 0.65}}},  # R7 BNB cap
        {"not_a_field": 1},
        {"regime_filter": "false"},  # a string is not a bool
        {"vol_mult": True},  # a bool is not a number
        {"consecutive_sl_limit": 2.5},  # int field
        {"pair_risk": {"BTC": {"max_risk_pct": 1.0}}},  # incomplete PairRisk
        {"pair_risk": [1, 2]},
        {"correlated_cluster": "BTC,ETH,BNB"},
        {"bnb_event_kinds": []},  # drops the R5 BNB burn/launchpool blackout
        {"bnb_event_kinds": ["bnb_burn"]},  # drops launchpool
    ],
)
def test_config_from_overrides_rejects_bad_or_loosening_values(overrides):
    with pytest.raises(ConfigError):
        config_from_overrides(overrides)


def test_config_from_overrides_rejects_empty_bnb_event_kinds():
    with pytest.raises(ConfigError, match="bnb_event_kinds must include"):
        config_from_overrides({"bnb_event_kinds": []})


# ---------------------------------------------------------------------------- record JSON
def test_record_round_trip(tmp_path: Path, base: Path):
    for record in (
        valid_record(base),
        empty_record("base"),
        mutate(valid_record(base), "live", enabled_utc=NOW_ISO),
        mutate(valid_record(base), "walk_forward", label_params={"max_dd_pct": 15, "x": "y"}),
        dataclasses.replace(valid_record(base), events_file=None, data_files={}),
    ):
        path = tmp_path / "sub" / "rec.json"
        save_record(record, path)
        assert load_record(path) == record


def test_saved_record_has_every_c3_field(tmp_path: Path, base: Path):
    path = tmp_path / "rec.json"
    save_record(valid_record(base), path)
    data = json.loads(path.read_text())
    assert data["provenance"] == "real"
    assert list(data["data_files"].values()) == [sha256_file(base / next(iter(data["data_files"])))]
    assert set(data["events_file"]) == {"path", "sha256"}
    assert "report_sha256" in data["backtest"]
    assert {
        "train_journal_path",
        "train_journal_sha256",
        "test_journal_path",
        "test_journal_sha256",
        "min_train",
        "min_test",
        "train_avg_r",
        "label_params",
    } <= set(data["walk_forward"])
    assert {"review_path", "review_sha256"} <= set(data["human_review"])
    assert {"events_path", "events_sha256"} <= set(data["testnet"])


def test_empty_record_uses_default_fingerprint_and_empty_stages():
    record = empty_record("rr2.5_vol2")
    assert record.variant == "rr2.5_vol2"
    assert record.config_fingerprint == config_fingerprint(StrategyConfig())
    assert record.backtest == BacktestStage() and record.live == LiveStage()
    assert record.provenance is None and record.data_files is None and record.events_file is None
    cfg = CFG.with_changes(reward_risk=2.5)
    assert empty_record("x", cfg).config_fingerprint == config_fingerprint(cfg)


def test_load_record_missing_sections_are_empty_and_ints_become_floats(tmp_path: Path):
    path = tmp_path / "rec.json"
    path.write_text(
        json.dumps(
            {"variant": "base", "config_fingerprint": "ab", "walk_forward": {"test_avg_r": 1}}
        )
    )
    record = load_record(path)
    assert record.testnet == TestnetStage()
    assert record.walk_forward.test_avg_r == 1.0
    assert isinstance(record.walk_forward.test_avg_r, float)


def test_record_without_c3_fields_loads_but_is_blocked_after_walk_forward(
    tmp_path: Path, base: Path
):
    """Backward compatibility: a pre-C3 record loads unchanged, promotion then needs C3."""
    path = tmp_path / "rec.json"
    save_record(valid_record(base), path)
    data = json.loads(path.read_text())
    for key in ("provenance", "data_files", "events_file", "model_fingerprint"):
        del data[key]
    del data["backtest"]["report_sha256"]
    for key in ("train_journal_sha256", "test_journal_sha256", "label_params", "min_train"):
        del data["walk_forward"][key]
    path.write_text(json.dumps(data))
    old = load_record(path)
    assert old.provenance is None and old.walk_forward.label_params is None
    rules = blocking_rules(check(base, old, HUMAN_REVIEW))
    assert rules == {ADOPT_PROVENANCE, ADOPT_BACKTEST, ADOPT_WALK_FORWARD}


@pytest.mark.parametrize(
    "text, match",
    [
        ('{"variant": "b", "config_fingerprint": "x", "extra": 1}', "unknown keys"),
        (
            '{"variant": "b", "config_fingerprint": "x", "human_review": {"trades_reviwed": 9}}',
            "unknown keys",
        ),
        (
            '{"variant": "b", "config_fingerprint": "x", "human_review": {"approved": "yes"}}',
            "approved must be true/false",
        ),
        (
            '{"variant": "b", "config_fingerprint": "x", "human_review": {"trades_total": 99.0}}',
            "must be an integer",
        ),
        (
            '{"variant": "b", "config_fingerprint": "x", "testnet": {"rule_violations": false}}',
            "must be an integer",
        ),
        (
            '{"variant": "b", "config_fingerprint": "x", "walk_forward": {"test_avg_r": NaN}}',
            "must be a number",
        ),
        ('{"variant": "b", "config_fingerprint": "x", "testnet": [1]}', "JSON object or null"),
        ('{"variant": "b", "variant": "c", "config_fingerprint": "x"}', "duplicate key"),
        ('{"config_fingerprint": "x"}', "variant must be a string"),
        ("[1, 2]", "must be a JSON object"),
        ("{not json", "not valid JSON"),
        ('{"variant": "b", "config_fingerprint": "x", "provenance": 1}', "provenance must be"),
        ('{"variant": "b", "config_fingerprint": "x", "data_files": ["a"]}', "data_files must be"),
        ('{"variant": "b", "config_fingerprint": "x", "data_files": {"a": 1}}', "data_files.a"),
        (
            '{"variant": "b", "config_fingerprint": "x", "events_file": {"file": "e.csv"}}',
            "unknown keys",
        ),
        (
            '{"variant": "b", "config_fingerprint": "x", "walk_forward": {"label_params": 3}}',
            "label_params must be a JSON object",
        ),
        (
            '{"variant": "b", "config_fingerprint": "x", '
            '"walk_forward": {"label_params": {"m": [4]}}}',
            "label_params.m must be",
        ),
        (
            '{"variant": "b", "config_fingerprint": "x", '
            '"walk_forward": {"label_params": {"m": NaN}}}',
            "label_params.m must be",
        ),
        (
            '{"variant": "b", "config_fingerprint": "x", "backtest": {"report_sha256": 5}}',
            "report_sha256 must be a string",
        ),
    ],
)
def test_load_record_rejects_malformed_files(tmp_path: Path, text: str, match: str):
    path = tmp_path / "rec.json"
    path.write_text(text)
    with pytest.raises(ValueError, match=match):
        load_record(path)


# ---------------------------------------------------------------------------- promotion: pass
@pytest.mark.parametrize("stage", STAGES)
def test_fully_valid_real_record_with_an_all_y_pack_passes_every_stage(base: Path, stage: str):
    assert check(base, valid_record(base), stage) == []


def test_testnet_of_exactly_14_days_passes_to_live(base: Path):
    record = valid_record(base)
    start, end = iso_to_ms(record.testnet.start_utc), iso_to_ms(record.testnet.end_utc)
    assert end - start == 14 * DAY_MS
    assert check(base, record, LIVE) == []
    require_stage(record, LIVE, CFG, NOW, base)  # does not raise


def test_empty_record_may_only_start_the_backtest(base: Path):
    record = empty_record("base")
    assert check(base, record, BACKTEST) == []
    decisions = check(base, record, WALK_FORWARD)
    assert blocking_rules(decisions) == {ADOPT_BACKTEST} and len(decisions) == 1
    decisions = check(base, record, LIVE)
    assert [d.rule for d in decisions] == [
        ADOPT_PROVENANCE,  # provenance missing
        ADOPT_PROVENANCE,  # no data file hashes
        ADOPT_BACKTEST,
        ADOPT_WALK_FORWARD,
        ADOPT_HUMAN_REVIEW,
        ADOPT_TESTNET,
    ]


def test_base_dir_none_resolves_evidence_against_the_working_directory(
    base: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    record = valid_record(base)
    monkeypatch.chdir(base)
    assert check_promotion(record, LIVE, CFG, NOW) == []
    monkeypatch.chdir(tmp_path)  # evidence is ALWAYS checked: elsewhere nothing is found
    decisions = check_promotion(record, LIVE, CFG, NOW)
    assert "does not exist" in reasons(decisions)
    assert blocking_rules(decisions) >= {ADOPT_PROVENANCE, ADOPT_BACKTEST, ADOPT_WALK_FORWARD}


# ---------------------------------------------------------------------------- skipping stages
@pytest.mark.parametrize("skipped", STAGES[:-1])
def test_skipping_any_stage_blocks_every_later_stage(base: Path, skipped: str):
    section = STAGE_SECTIONS[skipped]
    good = valid_record(base)
    record = dataclasses.replace(good, **{section: type(getattr(good, section))()})
    for target in STAGES[STAGES.index(skipped) + 1 :]:
        decisions = check(base, record, target)
        assert blocking_rules(decisions) == {STAGE_RULES[skipped]}, target
        assert len(decisions) == 1, decisions
        assert "no recorded result" in decisions[0].reason
    earlier = STAGES[STAGES.index(skipped)]
    assert check(base, record, earlier) == []


def test_skipping_human_review_to_testnet_blocks(base: Path):
    record = dataclasses.replace(valid_record(base), human_review=HumanReviewStage())
    decisions = check(base, record, TESTNET)
    assert blocking_rules(decisions) == {ADOPT_HUMAN_REVIEW}
    assert "HUMAN_REVIEW" in decisions[0].reason and "TESTNET" in decisions[0].reason


# ---------------------------------------------------------------------------- blocking conditions
BLOCKING_CASES = [
    # (id, section, changes, rule)
    ("backtest_no_report", "backtest", {"report_path": None}, ADOPT_BACKTEST),
    ("backtest_blank_report", "backtest", {"report_path": "  "}, ADOPT_BACKTEST),
    ("backtest_no_report_hash", "backtest", {"report_sha256": None}, ADOPT_BACKTEST),
    ("backtest_bad_report_hash", "backtest", {"report_sha256": "abc"}, ADOPT_BACKTEST),
    ("backtest_wrong_report_hash", "backtest", {"report_sha256": "0" * 64}, ADOPT_BACKTEST),
    ("backtest_no_time", "backtest", {"completed_utc": None}, ADOPT_BACKTEST),
    ("backtest_bad_time", "backtest", {"completed_utc": "last tuesday"}, ADOPT_BACKTEST),
    ("wf_train_only", "walk_forward", {"label": "TRAIN-ONLY"}, ADOPT_WALK_FORWARD),
    ("wf_untested", "walk_forward", {"label": "UNTESTED"}, ADOPT_WALK_FORWARD),
    ("wf_no_edge", "walk_forward", {"label": "NO-EDGE"}, ADOPT_WALK_FORWARD),
    ("wf_label_case", "walk_forward", {"label": "robust"}, ADOPT_WALK_FORWARD),
    ("wf_no_label", "walk_forward", {"label": None}, ADOPT_WALK_FORWARD),
    ("wf_dd_failed", "walk_forward", {"dd_ok": False}, ADOPT_WALK_FORWARD),
    ("wf_dd_missing", "walk_forward", {"dd_ok": None}, ADOPT_WALK_FORWARD),
    ("wf_zero_avg_r", "walk_forward", {"test_avg_r": 0.0}, ADOPT_WALK_FORWARD),
    ("wf_negative_avg_r", "walk_forward", {"test_avg_r": -0.1}, ADOPT_WALK_FORWARD),
    ("wf_no_train", "walk_forward", {"train_n": 0}, ADOPT_WALK_FORWARD),
    ("wf_no_test", "walk_forward", {"test_n": None}, ADOPT_WALK_FORWARD),
    ("wf_no_split", "walk_forward", {"split_utc": None}, ADOPT_WALK_FORWARD),
    ("wf_no_report", "walk_forward", {"report_path": ""}, ADOPT_WALK_FORWARD),
    ("wf_no_train_journal", "walk_forward", {"train_journal_path": None}, ADOPT_WALK_FORWARD),
    ("wf_no_test_hash", "walk_forward", {"test_journal_sha256": None}, ADOPT_WALK_FORWARD),
    ("wf_no_min_train", "walk_forward", {"min_train": None}, ADOPT_WALK_FORWARD),
    ("wf_zero_min_test", "walk_forward", {"min_test": 0}, ADOPT_WALK_FORWARD),
    ("wf_no_train_avg_r", "walk_forward", {"train_avg_r": None}, ADOPT_WALK_FORWARD),
    ("wf_no_label_params", "walk_forward", {"label_params": None}, ADOPT_WALK_FORWARD),
    ("review_short", "human_review", {"trades_reviewed": N_ALL - 1}, ADOPT_HUMAN_REVIEW),
    ("review_over", "human_review", {"trades_reviewed": N_ALL + 1}, ADOPT_HUMAN_REVIEW),
    ("review_count_missing", "human_review", {"trades_reviewed": None}, ADOPT_HUMAN_REVIEW),
    (
        "review_zero_trades",
        "human_review",
        {"trades_reviewed": 0, "trades_total": 0},
        ADOPT_HUMAN_REVIEW,
    ),
    (
        "review_not_all_wf_trades",
        "human_review",
        {"trades_reviewed": N_ALL - 2, "trades_total": N_ALL - 2},
        ADOPT_HUMAN_REVIEW,
    ),
    ("review_not_approved", "human_review", {"approved": False}, ADOPT_HUMAN_REVIEW),
    ("review_approval_missing", "human_review", {"approved": None}, ADOPT_HUMAN_REVIEW),
    ("review_no_reviewer", "human_review", {"reviewer": None}, ADOPT_HUMAN_REVIEW),
    ("review_blank_reviewer", "human_review", {"reviewer": "   "}, ADOPT_HUMAN_REVIEW),
    (
        "review_before_backtest",
        "human_review",
        {"date_utc": "2024-02-01T00:00:00Z"},
        ADOPT_HUMAN_REVIEW,
    ),
    ("review_no_pack", "human_review", {"review_path": None}, ADOPT_HUMAN_REVIEW),
    ("review_no_pack_hash", "human_review", {"review_sha256": None}, ADOPT_HUMAN_REVIEW),
    ("review_wrong_pack_hash", "human_review", {"review_sha256": "f" * 64}, ADOPT_HUMAN_REVIEW),
    ("testnet_13_days", "testnet", {"end_utc": "2024-04-14T00:00:00Z"}, ADOPT_TESTNET),
    ("testnet_1ms_short", "testnet", {"end_utc": "2024-04-14T23:59:59.999Z"}, ADOPT_TESTNET),
    ("testnet_real_exchange", "testnet", {"exchange": "binance"}, ADOPT_TESTNET),
    ("testnet_other_testnet", "testnet", {"exchange": "coinbase-sandbox"}, ADOPT_TESTNET),
    ("testnet_no_exchange", "testnet", {"exchange": None}, ADOPT_TESTNET),
    (
        "testnet_not_finished",
        "testnet",
        {"end_utc": "2024-07-02T00:00:00Z"},  # after NOW
        ADOPT_TESTNET,
    ),
    ("testnet_one_violation", "testnet", {"rule_violations": 1}, ADOPT_TESTNET),
    ("testnet_violations_unrecorded", "testnet", {"rule_violations": None}, ADOPT_TESTNET),
    ("testnet_no_journal", "testnet", {"journal_path": None}, ADOPT_TESTNET),
    ("testnet_blank_journal", "testnet", {"journal_path": ""}, ADOPT_TESTNET),
    ("testnet_no_trades", "testnet", {"trades": 0}, ADOPT_TESTNET),
    (
        "testnet_before_review",
        "testnet",
        {"start_utc": "2024-03-05T00:00:00Z", "end_utc": "2024-03-25T00:00:00Z"},
        ADOPT_TESTNET,
    ),
    ("testnet_bad_start", "testnet", {"start_utc": "2024-04-31T00:00:00Z"}, ADOPT_TESTNET),
    ("testnet_no_end", "testnet", {"end_utc": None}, ADOPT_TESTNET),
    ("testnet_events_without_hash", "testnet", {"events_path": "e.csv"}, ADOPT_TESTNET),
]


@pytest.mark.parametrize(
    "section, changes, rule", [c[1:] for c in BLOCKING_CASES], ids=[c[0] for c in BLOCKING_CASES]
)
def test_each_blocking_condition_blocks_individually(base: Path, section, changes, rule):
    record = mutate(valid_record(base), section, **changes)
    stage = next(s for s, sec in STAGE_SECTIONS.items() if sec == section)
    for target in STAGES[STAGES.index(stage) + 1 :]:
        decisions = check(base, record, target)
        assert blocking_rules(decisions) == {rule}, (target, decisions)
    assert check(base, record, stage) == []  # the stage itself may still start


def test_reasons_name_the_problem(base: Path):
    def reason(section, **changes):
        return reasons(check(base, mutate(valid_record(base), section, **changes), LIVE))

    assert f"{N_ALL - 1} of {N_ALL}" in reason("human_review", trades_reviewed=N_ALL - 1)
    assert "TRAIN-ONLY" in reason("walk_forward", label="TRAIN-ONLY")
    assert "13.00 days" in reason("testnet", end_utc="2024-04-14T00:00:00Z")
    assert "'binance'" in reason("testnet", exchange="binance")
    assert f"train {N_TRAIN} + test {N_TEST}" in reason(
        "human_review", trades_reviewed=N_ALL - 2, trades_total=N_ALL - 2
    )
    assert "not the evidence that was recorded" in reason("backtest", report_sha256="0" * 64)


def test_future_times_block_the_stage_that_owns_them(base: Path):
    late_review = mutate(valid_record(base), "human_review", date_utc="2024-08-01T00:00:00Z")
    decisions = check(base, late_review, TESTNET)
    assert blocking_rules(decisions) == {ADOPT_HUMAN_REVIEW}
    # at LIVE the testnet additionally started before that (future) sign-off
    decisions = check(base, late_review, LIVE)
    assert blocking_rules(decisions) == {ADOPT_HUMAN_REVIEW, ADOPT_TESTNET}
    late_backtest = mutate(valid_record(base), "backtest", completed_utc="2024-08-01T00:00:00Z")
    decisions = check(base, late_backtest, WALK_FORWARD)
    assert blocking_rules(decisions) == {ADOPT_BACKTEST}


def test_several_problems_are_all_reported(base: Path):
    record = mutate(valid_record(base), "human_review", trades_reviewed=N_ALL - 1, approved=False)
    record = mutate(record, "testnet", end_utc="2024-04-10T00:00:00Z", rule_violations=2)
    decisions = check(base, record, LIVE)
    assert blocking_rules(decisions) == {ADOPT_HUMAN_REVIEW, ADOPT_TESTNET}
    assert len(decisions) == 4


# ---------------------------------------------------------------------------- fingerprint / live
@pytest.mark.parametrize("stage", STAGES)
def test_fingerprint_mismatch_blocks_every_stage(base: Path, stage: str):
    tested = CFG.with_changes(reward_risk=2.5)
    record = valid_record(base, tested)
    assert check(base, record, stage, tested) == []
    decisions = check(base, record, stage, CFG)  # promoting a different config
    assert blocking_rules(decisions) == {ADOPT_FINGERPRINT}
    assert "not the one that was tested" in decisions[0].reason


def test_missing_fingerprint_or_variant_blocks(base: Path):
    record = dataclasses.replace(valid_record(base), config_fingerprint="")
    assert blocking_rules(check(base, record, BACKTEST)) == {ADOPT_FINGERPRINT}
    record = dataclasses.replace(valid_record(base), variant=" ")
    assert ADOPT_RECORD in blocking_rules(check(base, record, LIVE))


def test_test_only_config_may_reach_testnet_but_never_live(base: Path):
    regime_off = CFG.with_changes(regime_filter=False)
    record = valid_record(base, regime_off)
    assert check(base, record, TESTNET, regime_off) == []
    decisions = check(base, record, LIVE, regime_off)
    assert blocking_rules(decisions) == {ADOPT_LIVE}
    assert "R4" in decisions[0].reason


def test_live_enabled_before_testnet_ended_blocks(base: Path):
    early = mutate(valid_record(base), "live", enabled_utc="2024-04-10T00:00:00Z")
    assert blocking_rules(check(base, early, LIVE)) == {ADOPT_LIVE}
    garbage = mutate(valid_record(base), "live", enabled_utc="soon")
    assert blocking_rules(check(base, garbage, LIVE)) == {ADOPT_LIVE}
    later = mutate(valid_record(base), "live", enabled_utc="2024-04-16T00:00:00Z")
    assert check(base, later, LIVE) == []


def test_unknown_stage_raises(base: Path):
    with pytest.raises(ValueError, match="unknown adoption stage"):
        check(base, valid_record(base), "PAPER")
    with pytest.raises(ValueError):
        check(base, valid_record(base), "live")
    with pytest.raises(ValueError, match="unknown adoption stage"):
        unverified_notes(valid_record(base), "PAPER")


def test_require_stage_raises_with_every_decision(base: Path):
    record = mutate(valid_record(base), "testnet", end_utc="2024-04-14T00:00:00Z")
    with pytest.raises(AdoptionBlocked) as info:
        require_stage(record, LIVE, CFG, NOW, base)
    assert info.value.stage == LIVE
    assert [d.rule for d in info.value.decisions] == [ADOPT_TESTNET]
    assert "ADOPT_testnet" in str(info.value)


def test_rule_ids_cover_every_stage():
    assert set(STAGE_RULES) == set(STAGES)
    assert set(STAGE_RULES.values()) | {
        ADOPT_RECORD,
        ADOPT_FINGERPRINT,
        ADOPT_PROVENANCE,
    } == set(ADOPTION_RULE_IDS)


def test_adoption_rule_ids_agree_with_config_rule_ids():
    adopt_ids = {k: v for k, v in RULE_IDS.items() if k.startswith("ADOPT_")}
    assert dict(ADOPTION_RULE_IDS) == adopt_ids
    assert list(ADOPTION_RULE_IDS) == list(adopt_ids)  # same order as the config listing
    assert ADOPT_PROVENANCE in RULE_IDS


# ---------------------------------------------------------------------------- C3: cycle-1 forgery
def forged_cycle1_record(root: Path) -> Path:
    """The record that passed LIVE in gate cycle 1: typed ROBUST on 1 + 1 trades, a 6-byte
    'hello' report, a 2-of-2 'review', and a clean testnet journal. No C3 evidence at all."""
    (root / "REPORT.md").write_bytes(b"hello\n")
    write_journal([make_testnet_trade(k) for k in range(3)], root / "trades.csv")
    data = {
        "variant": "base",
        "config_fingerprint": config_fingerprint(CFG),
        "model_fingerprint": None,
        "backtest": {"report_path": "REPORT.md", "completed_utc": "2024-03-01T00:00:00Z"},
        "walk_forward": {
            "label": "ROBUST",
            "dd_ok": True,
            "split_utc": "2023-01-01T00:00:00Z",
            "train_n": 1,
            "test_n": 1,
            "test_avg_r": 0.01,
            "report_path": "REPORT.md",
        },
        "human_review": {
            "reviewer": "me",
            "date_utc": "2024-03-10T00:00:00Z",
            "trades_reviewed": 2,
            "trades_total": 2,
            "approved": True,
            "notes": None,
        },
        "testnet": {
            "exchange": "binance-testnet",
            "start_utc": "2024-04-01T00:00:00Z",
            "end_utc": "2024-04-15T00:00:00Z",
            "trades": 3,
            "rule_violations": 0,
            "journal_path": "trades.csv",
            "notes": None,
        },
        "live": {"enabled_utc": None},
    }
    path = root / "adoption_base.json"
    path.write_text(json.dumps(data, indent=2))
    return path


def test_cycle1_forged_record_is_blocked(tmp_path: Path, capsys):
    path = forged_cycle1_record(tmp_path)
    assert (tmp_path / "REPORT.md").stat().st_size == 6
    record = load_record(path)
    for stage in LATER_THAN_WF:
        decisions = check(tmp_path, record, stage)
        rules = blocking_rules(decisions)
        assert {ADOPT_PROVENANCE, ADOPT_BACKTEST, ADOPT_WALK_FORWARD} <= rules, stage
        text = reasons(decisions)
        assert "report_sha256 is missing" in text
        assert "provenance is missing" in text
        assert "at least 30 trades in each window" in text
        assert "train_journal_path is missing" in text
        if stage != HUMAN_REVIEW:
            assert ADOPT_HUMAN_REVIEW in rules and "review_path is missing" in text
    assert main(["check", "--record", str(path), "--stage", LIVE, "--now", NOW_ISO]) == 1
    assert capsys.readouterr().out.startswith("BLOCKED")


def test_forgery_with_hashes_and_one_trade_windows_is_still_blocked(tmp_path: Path):
    """Even with every file hash-bound and min_train = min_test = 1 (so metrics.label itself
    says ROBUST), ROBUST on 1 + 1 trades is refused."""
    train = [closed_trade(1, TRAIN_START, True)]
    test = [closed_trade(2, SPLIT + DAY_MS, True)]
    record = build_evidence(tmp_path, train=train, test=test)
    record = mutate(record, "walk_forward", min_train=1, min_test=1)
    assert recompute_walk_forward(train, test, CFG, 1, 1, {}).label == "ROBUST"
    for stage in LATER_THAN_WF:
        decisions = check(tmp_path, record, stage)
        assert blocking_rules(decisions) == {ADOPT_WALK_FORWARD}, stage
        assert "needs at least 30 trades in each window" in reasons(decisions)


# ---------------------------------------------------------------------------- C3: provenance
@pytest.mark.parametrize(
    "provenance, needle",
    [
        ("synthetic:planted:1", "never evidence about real markets"),
        ("SYNTHETIC:null:7", "never evidence about real markets"),
        (None, "provenance is missing"),
        ("  ", "provenance is missing"),
        ("real-ish", "only 'real'"),
    ],
)
def test_synthetic_or_missing_provenance_blocks_after_walk_forward(
    base: Path, provenance: str | None, needle: str
):
    record = dataclasses.replace(valid_record(base), provenance=provenance)
    assert check(base, record, WALK_FORWARD) == []  # synthetic worlds may be walk-forwarded
    for stage in LATER_THAN_WF:
        decisions = check(base, record, stage)
        assert blocking_rules(decisions) == {ADOPT_PROVENANCE}, stage
        assert len(decisions) == 1 and needle in decisions[0].reason


@pytest.mark.parametrize("data_files", [None, {}])
def test_real_provenance_needs_data_file_hashes(base: Path, data_files):
    record = dataclasses.replace(valid_record(base), data_files=data_files)
    assert check(base, record, WALK_FORWARD) == []
    for stage in LATER_THAN_WF:
        decisions = check(base, record, stage)
        assert blocking_rules(decisions) == {ADOPT_PROVENANCE}
        assert "data_files is missing or empty" in decisions[0].reason


def test_data_and_events_file_hashes_are_checked_from_walk_forward_on(tmp_path: Path):
    record = build_evidence(tmp_path)
    (tmp_path / DATA_FILE).write_text("ts,open,high,low,close,volume\n0,2,2,2,2,2\n")
    for stage in (WALK_FORWARD, *LATER_THAN_WF):
        decisions = check(tmp_path, record, stage)
        assert blocking_rules(decisions) == {ADOPT_PROVENANCE}
        assert "not the evidence that was recorded" in decisions[0].reason
    assert check(tmp_path, record, BACKTEST) == []
    record = build_evidence(tmp_path)
    (tmp_path / EVENTS).unlink()
    decisions = check(tmp_path, record, WALK_FORWARD)
    assert blocking_rules(decisions) == {ADOPT_PROVENANCE}
    assert "events_file.path" in decisions[0].reason and "does not exist" in decisions[0].reason
    bad = dataclasses.replace(record, events_file=FileRef(EVENTS, None))
    assert "events_file.sha256 is missing" in reasons(check(tmp_path, bad, WALK_FORWARD))


# ---------------------------------------------------------------------------- C3: report hash
def test_report_hash_mismatch_blocks(tmp_path: Path):
    record = build_evidence(tmp_path)
    (tmp_path / BT_REPORT).write_text("# backtest report (edited after recording)\n")
    for stage in (WALK_FORWARD, *LATER_THAN_WF):
        decisions = check(tmp_path, record, stage)
        assert blocking_rules(decisions) == {ADOPT_BACKTEST}, stage
        assert "backtest.report_path" in decisions[0].reason
        assert "not the evidence that was recorded" in decisions[0].reason


# ---------------------------------------------------------------------------- C3: journals
@pytest.mark.parametrize("journal", [TRAIN_JOURNAL, TEST_JOURNAL])
def test_journal_hash_mismatch_blocks(tmp_path: Path, journal: str):
    record = build_evidence(tmp_path)
    train, test = wf_trades()
    doctored = [dataclasses.replace(t, r_multiple=2.0, exit_reason=EXIT_TP) for t in train]
    write_journal(doctored if journal == TRAIN_JOURNAL else test[:-1], tmp_path / journal)
    for stage in LATER_THAN_WF:
        decisions = check(tmp_path, record, stage)
        assert blocking_rules(decisions) == {ADOPT_WALK_FORWARD}, stage
        assert len(decisions) == 1
        assert "not the evidence that was recorded" in decisions[0].reason
        assert journal.split("/")[-1].split(".")[0] + "_journal_path" in decisions[0].reason


def test_typed_label_differs_from_recomputed_blocks(tmp_path: Path):
    record = build_evidence(tmp_path, test=weak_test_trades())  # typed ROBUST, evidence UNTESTED
    assert record.walk_forward.label == "ROBUST" and record.walk_forward.test_avg_r > 0
    for stage in LATER_THAN_WF:
        decisions = check(tmp_path, record, stage)
        assert blocking_rules(decisions) == {ADOPT_WALK_FORWARD}, stage
        assert len(decisions) == 1
        assert "walk_forward.label is 'ROBUST' but metrics.label" in decisions[0].reason
        assert "does not match its evidence" in decisions[0].reason
    honest = mutate(record, "walk_forward", label="UNTESTED")
    assert "only ROBUST may proceed" in reasons(check(tmp_path, honest, HUMAN_REVIEW))


@pytest.mark.parametrize(
    "changes, needle",
    [
        ({"test_avg_r": 1.2}, "walk_forward.test_avg_r is 1.2 but the journal gives"),
        ({"train_avg_r": 5.0}, "walk_forward.train_avg_r is 5.0 but the journal gives"),
        ({"test_n": N_TEST + 1}, f"test_n is {N_TEST + 1} but the journal holds {N_TEST}"),
        ({"train_n": 40}, f"train_n is 40 but the journal holds {N_TRAIN}"),
        ({"label_params": {"max_dd_pct": 0.01}}, "walk_forward.dd_ok is True but"),
        ({"min_test": 31}, "walk_forward.label is 'ROBUST' but metrics.label"),
    ],
)
def test_typed_numbers_must_match_the_recomputed_ones(base: Path, changes, needle):
    record = mutate(valid_record(base), "walk_forward", **changes)
    decisions = check(base, record, HUMAN_REVIEW)
    assert blocking_rules(decisions) == {ADOPT_WALK_FORWARD}
    assert needle in reasons(decisions)
    assert ADOPT_WALK_FORWARD in blocking_rules(check(base, record, LIVE))


def test_avg_r_within_tolerance_passes(base: Path):
    record = valid_record(base)
    nudged = mutate(record, "walk_forward", test_avg_r=record.walk_forward.test_avg_r + 1e-10)
    assert check(base, nudged, LIVE) == []


@pytest.mark.parametrize(
    "params, needle",
    [
        ({"not_a_param": 1}, "is not a parameter of metrics.summarize"),
        ({"max_dd_pct": 25.0}, f"must be in (0, {DD_CAP_PCT:g}]"),
        ({"max_dd_pct": True}, "must be in (0, 20]"),
        ({"min_train": 1}, "may not set 'min_train'"),
        ({"starting_equity": 1.0}, "may not set 'starting_equity'"),
    ],
)
def test_bad_label_params_block(base: Path, params, needle):
    record = mutate(valid_record(base), "walk_forward", label_params=params)
    decisions = check(base, record, HUMAN_REVIEW)
    assert blocking_rules(decisions) == {ADOPT_WALK_FORWARD}
    assert len(decisions) == 1 and needle in decisions[0].reason
    assert "could not be recomputed" in decisions[0].reason


def test_label_params_are_routed_by_signature(base: Path):
    routed = route_label_params({})
    assert routed == {"summarize": {}, "label": {}, "dd_check": {"max_dd_pct": DD_CAP_PCT}}
    params = inspect.signature(metrics.summarize).parameters
    optional = sorted(n for n, p in params.items() if p.default is not inspect.Parameter.empty)
    assert optional, "metrics.summarize has optional keyword parameters to route"
    key = optional[0]
    routed = route_label_params({key: params[key].default, "max_dd_pct": 15})
    assert routed["summarize"] == {key: params[key].default}
    assert routed["dd_check"] == {"max_dd_pct": 15}
    # a legal parameter set passes the whole path
    record = mutate(valid_record(base), "walk_forward", label_params={"max_dd_pct": 15.0})
    assert check(base, record, LIVE) == []


@pytest.mark.parametrize(
    "train_change, needle",
    [
        ({"signal_ts": SPLIT + DAY_MS}, "wrong side of walk_forward.split_utc"),
        ({"variant": "rr3"}, "are not of variant 'base'"),
        ({"exit_ts": None, "r_multiple": None}, "without an exit or R"),
        ({"trade_id": 2}, "repeats trade ids #2"),
    ],
)
def test_walk_forward_journal_content_is_checked(tmp_path: Path, train_change, needle):
    record = build_evidence(tmp_path)
    train, _test = wf_trades()
    train[0] = dataclasses.replace(train[0], **train_change)
    write_journal(train, tmp_path / TRAIN_JOURNAL)  # re-recorded, so the hash matches
    record = rehash(record, tmp_path, "walk_forward", "train_journal_path")
    decisions = check(tmp_path, record, HUMAN_REVIEW)
    assert ADOPT_WALK_FORWARD in blocking_rules(decisions)
    assert needle in reasons(decisions)


def test_unreadable_walk_forward_journal_blocks(tmp_path: Path):
    record = build_evidence(tmp_path)
    (tmp_path / TEST_JOURNAL).write_text("not,a,journal\n1,2,3\n")
    record = rehash(record, tmp_path, "walk_forward", "test_journal_path")
    decisions = check(tmp_path, record, HUMAN_REVIEW)
    assert blocking_rules(decisions) == {ADOPT_WALK_FORWARD}
    assert "could not be read as a trade journal" in reasons(decisions)


# ---------------------------------------------------------------------------- C3: review pack
def test_missing_review_pack_blocks(tmp_path: Path):
    record = build_evidence(tmp_path)
    (tmp_path / REVIEW_CSV).unlink()
    for stage in LATER_THAN_REVIEW:
        decisions = check(tmp_path, record, stage)
        assert blocking_rules(decisions) == {ADOPT_HUMAN_REVIEW}, stage
        assert "human_review.review_path" in decisions[0].reason
        assert "does not exist" in decisions[0].reason
    assert check(tmp_path, record, HUMAN_REVIEW) == []


def test_review_pack_hash_mismatch_blocks(tmp_path: Path):
    record = build_evidence(tmp_path)
    fill_review(tmp_path / REVIEW_CSV, {("TEST", N_TRAIN + 1): REVIEW_OK_NO})  # after sign-off
    for stage in LATER_THAN_REVIEW:
        decisions = check(tmp_path, record, stage)
        assert blocking_rules(decisions) == {ADOPT_HUMAN_REVIEW}
        assert len(decisions) == 1 and "not the evidence that was recorded" in decisions[0].reason


def test_blank_reviewer_ok_row_blocks(tmp_path: Path):
    record = build_evidence(tmp_path, verdicts={("TRAIN", 5): REVIEW_OK_BLANK})
    for stage in LATER_THAN_REVIEW:
        decisions = check(tmp_path, record, stage)
        assert blocking_rules(decisions) == {ADOPT_HUMAN_REVIEW}, stage
        assert len(decisions) == 1
        assert "1 review rows (TRAIN #5) have no reviewer_ok verdict" in decisions[0].reason


def test_one_rejected_row_blocks_and_states_the_policy(tmp_path: Path):
    record = build_evidence(tmp_path, verdicts={("TEST", N_TRAIN + 3): REVIEW_OK_NO})
    assert record.human_review.approved is True  # an "approved" flag cannot outvote an N
    for stage in LATER_THAN_REVIEW:
        decisions = check(tmp_path, record, stage)
        assert blocking_rules(decisions) == {ADOPT_HUMAN_REVIEW}, stage
        assert len(decisions) == 1
        reason = decisions[0].reason
        assert f"rejected 1 trades (TEST #{N_TRAIN + 3})" in reason
        assert "restart the adoption path at BACKTEST" in reason


def test_unparseable_review_pack_blocks(tmp_path: Path):
    record = build_evidence(tmp_path)
    fill_review(tmp_path / REVIEW_CSV, {("TRAIN", 1): "yes"})
    record = rehash(record, tmp_path, "human_review", "review_path")
    decisions = check(tmp_path, record, TESTNET)
    assert blocking_rules(decisions) == {ADOPT_HUMAN_REVIEW}
    assert "could not be read as a review pack" in decisions[0].reason


def test_review_pack_must_describe_the_journal_trades(tmp_path: Path):
    train, test = wf_trades()
    record = build_evidence(tmp_path, train=train, test=test)
    # a pack of other trades: one TEST trade dropped, one TRAIN trade on another pair
    other = [dataclasses.replace(train[0], pair="ETH/USDT"), *train[1:], *test[:-1]]
    write_review_pack(other, tmp_path / REVIEW_DIR, "other", CFG, split_ts=SPLIT)
    fill_review(tmp_path / REVIEW_CSV)
    record = rehash(record, tmp_path, "human_review", "review_path")
    decisions = check(tmp_path, record, TESTNET)
    assert blocking_rules(decisions) == {ADOPT_HUMAN_REVIEW}
    text = reasons(decisions)
    assert f"has {N_ALL - 1} rows but the walk-forward produced {N_ALL} trades" in text
    assert f"no row for walk-forward trades TEST #{N_ALL}" in text
    assert "review rows TRAIN #1 name a different pair or signal time" in text


def test_review_pack_with_extra_rows_blocks(tmp_path: Path):
    train, test = wf_trades()
    record = build_evidence(tmp_path, train=train, test=test)
    extra = closed_trade(999, SPLIT + 400 * DAY_MS, True)
    write_review_pack([*train, *test, extra], tmp_path / REVIEW_DIR, "extra", CFG, split_ts=SPLIT)
    fill_review(tmp_path / REVIEW_CSV)
    record = rehash(record, tmp_path, "human_review", "review_path")
    text = reasons(check(tmp_path, record, TESTNET))
    assert "has rows TEST #999 that are not trades of the walk-forward journals" in text


# ---------------------------------------------------------------------------- testnet evidence
def test_absolute_evidence_paths_are_used_as_is(tmp_path: Path):
    record = build_evidence(tmp_path)
    elsewhere = tmp_path / "elsewhere"

    def absolute(value: str) -> str:
        return str(tmp_path / value)

    rec = dataclasses.replace(
        record,
        data_files={absolute(k): v for k, v in record.data_files.items()},
        events_file=FileRef(absolute(record.events_file.path), record.events_file.sha256),
    )
    rec = mutate(rec, "backtest", report_path=absolute(BT_REPORT))
    rec = mutate(
        rec,
        "walk_forward",
        report_path=absolute(WF_REPORT),
        train_journal_path=absolute(TRAIN_JOURNAL),
        test_journal_path=absolute(TEST_JOURNAL),
    )
    rec = mutate(rec, "human_review", review_path=absolute(REVIEW_CSV))
    partly = rec
    rec = mutate(rec, "testnet", journal_path=absolute(JOURNAL))
    assert check_promotion(rec, LIVE, CFG, NOW, elsewhere) == []
    decisions = check_promotion(partly, LIVE, CFG, NOW, elsewhere)  # one path still relative
    assert blocking_rules(decisions) == {ADOPT_TESTNET}


@pytest.mark.parametrize(
    "missing, rule",
    [
        (BT_REPORT, ADOPT_BACKTEST),
        (WF_REPORT, ADOPT_WALK_FORWARD),
        (TRAIN_JOURNAL, ADOPT_WALK_FORWARD),
        (TEST_JOURNAL, ADOPT_WALK_FORWARD),
        (REVIEW_CSV, ADOPT_HUMAN_REVIEW),
        (JOURNAL, ADOPT_TESTNET),
        (DATA_FILE, ADOPT_PROVENANCE),
        (EVENTS, ADOPT_PROVENANCE),
    ],
)
def test_missing_evidence_file_blocks(tmp_path: Path, missing: str, rule: str):
    record = build_evidence(tmp_path)
    (tmp_path / missing).unlink()
    decisions = check(tmp_path, record, LIVE)
    assert blocking_rules(decisions) == {rule}
    assert len(decisions) == 1 and "does not exist" in decisions[0].reason


@pytest.mark.parametrize(
    "trades, needle",
    [
        ([make_testnet_trade(0), make_testnet_trade(1)], "holds 2 trades"),
        (
            [
                make_testnet_trade(0),
                make_testnet_trade(1),
                make_testnet_trade(-1),
            ],  # entered before start
            "outside the testnet window",
        ),
        (
            [make_testnet_trade(0), make_testnet_trade(1), make_testnet_trade(2, variant="rr3")],
            "not of variant",
        ),
        (
            [make_testnet_trade(0), make_testnet_trade(1), make_testnet_trade(2, risk_pct=2.0)],
            "risk_above_cap",
        ),
        (
            [make_testnet_trade(0), make_testnet_trade(1), make_testnet_trade(2, target=105.0)],
            "rr_below_min",
        ),
        (
            [
                make_testnet_trade(0),
                make_testnet_trade(1),
                make_testnet_trade(2, entry_ts=TESTNET_START + 2 * DAY_MS),
            ],
            "lookahead",
        ),
    ],
)
def test_testnet_journal_is_cross_checked(tmp_path: Path, trades: list[Trade], needle: str):
    record = mutate(build_evidence(tmp_path, testnet=trades), "testnet", trades=3)
    decisions = check(tmp_path, record, LIVE)
    assert blocking_rules(decisions) == {ADOPT_TESTNET}
    assert any(needle in d.reason for d in decisions), decisions


def test_unreadable_testnet_journal_blocks(tmp_path: Path):
    record = build_evidence(tmp_path)
    (tmp_path / JOURNAL).write_text("not,a,journal\n1,2,3\n")
    decisions = check(tmp_path, record, LIVE)
    assert blocking_rules(decisions) == {ADOPT_TESTNET}
    assert "could not be read" in decisions[0].reason


def r6_r9_testnet_trades() -> list[Trade]:
    """3 consecutive BTC stop-losses, a BTC re-entry 12h after the third (inside the 24h
    bench), and an ETH trade overlapping a full-size BTC position (R6)."""
    day = TESTNET_START
    return [
        closed_trade(1, day, False),
        closed_trade(2, day + DAY_MS, False),
        closed_trade(3, day + 2 * DAY_MS, False),  # exits day 2 12:00 -> bench until day 3 12:00
        closed_trade(4, day + 2 * DAY_MS + 20 * HOUR_MS, True),  # decided day 3 00:00: benched
        closed_trade(5, day + 5 * DAY_MS, True),  # BTC open day 5 04:00 .. 12:00
        closed_trade(6, day + 5 * DAY_MS + 4 * HOUR_MS, True, pair="ETH/USDT"),  # overlaps #5
    ]


def test_testnet_r6_r9_violations_block_live(tmp_path: Path, capsys):
    trades = r6_r9_testnet_trades()
    assert all(auto_flags(t, CFG) == [] for t in trades)  # every trade is fine on its own
    record = build_evidence(tmp_path, testnet=trades)
    assert check(tmp_path, record, TESTNET) == []  # testnet itself may run
    decisions = check(tmp_path, record, LIVE)
    assert blocking_rules(decisions) == {ADOPT_TESTNET}
    assert len(decisions) == 1
    reason = decisions[0].reason
    assert "exit offset 0" in reason
    assert "while BTC/USDT was benched" in reason and "(R9)" in reason
    assert "#5 BTC/USDT and #6 ETH/USDT overlap" in reason and "(R6)" in reason
    assert "R5 (news blackout) was not checked" in reason and "not journal-verifiable" in reason
    path = tmp_path / "rec.json"
    save_record(record, path)
    assert main(["check", "--record", str(path), "--stage", LIVE, "--now", NOW_ISO]) == 1
    out = capsys.readouterr().out
    assert out.startswith("BLOCKED") and "[ADOPT_testnet]" in out and "(R6)" in out


def test_converted_bot_journal_feeds_the_testnet_audit(tmp_path: Path):
    """journal.import_external output is what the TESTNET stage audits (made-up bot format;
    a real mapping must be written for the bot's real columns)."""
    loss_per_unit, target = a1_levels(100.0, 95.0)
    rows = ["id,sym,opened,px,sl,tp,size,closed,out,why,equity"]
    for k, sym in enumerate(("BTCUSDT", "BTCUSDT", "ETHUSDT")):
        opened = TESTNET_START + k * DAY_MS + 4 * HOUR_MS
        rows.append(
            f"{k + 1},{sym},{opened},100.0,95.0,{target!r},2.0,{opened + 8 * HOUR_MS},"
            f"{target!r},tp,10000"
        )
    bot = tmp_path / "bot.csv"
    write_text(bot, "\n".join(rows) + "\n")
    columns = ["trade_id", "pair", "entry_ts", "entry_price", "stop", "target", "qty"]
    columns += ["exit_ts", "exit_price", "exit_reason", "equity"]
    mapping = dict(zip(rows[0].split(","), columns, strict=True))
    trades = import_external(
        bot, mapping, time_format="ms", exit_reason_map={"tp": "TP"}, defaults={"variant": "base"}
    )
    assert math.isclose(trades[0].risk_amount, 2.0 * loss_per_unit, rel_tol=1e-12)
    record = build_evidence(tmp_path, testnet=trades)
    assert check(tmp_path, record, LIVE) == []
    overlapping = [trades[0], dataclasses.replace(trades[2], signal_ts=trades[0].signal_ts)]
    write_journal(overlapping, tmp_path / JOURNAL)
    decisions = check(tmp_path, mutate(record, "testnet", trades=2), LIVE)
    assert blocking_rules(decisions) == {ADOPT_TESTNET} and "(R6)" in decisions[0].reason


def write_testnet_events(root: Path, rows: list[str]) -> str:
    path = root / TESTNET_EVENTS
    write_text(path, "time_utc,scope,impact,kind,note\n" + "".join(f"{r}\n" for r in rows))
    return sha256_file(path)


def test_testnet_r5_is_checked_when_the_calendar_is_recorded(tmp_path: Path):
    record = build_evidence(tmp_path)
    # the first testnet trade is decided at 04:00; a high-impact event at 05:00 blocks it
    sha = write_testnet_events(tmp_path, ["2024-04-01T05:00:00Z,ALL,high,macro,CPI"])
    with_events = mutate(record, "testnet", events_path=TESTNET_EVENTS, events_sha256=sha)
    decisions = check(tmp_path, with_events, LIVE)
    assert blocking_rules(decisions) == {ADOPT_TESTNET}
    assert "(R5)" in decisions[0].reason and "R5 checked against testnet.events_path" in (
        decisions[0].reason
    )
    assert not any("R5" in n for n in unverified_notes(with_events, LIVE))
    sha = write_testnet_events(tmp_path, ["2024-05-20T12:00:00Z,ALL,high,macro,later"])
    clean = mutate(record, "testnet", events_path=TESTNET_EVENTS, events_sha256=sha)
    assert check(tmp_path, clean, LIVE) == []
    (tmp_path / TESTNET_EVENTS).write_text("time_utc,scope,impact,kind,note\n")
    decisions = check(tmp_path, clean, LIVE)  # calendar edited after recording
    assert blocking_rules(decisions) == {ADOPT_TESTNET}
    assert "not the evidence that was recorded" in decisions[0].reason


def test_unreadable_testnet_calendar_blocks(tmp_path: Path):
    record = build_evidence(tmp_path)
    write_text(tmp_path / TESTNET_EVENTS, "when,what\nx,y\n")
    sha = sha256_file(tmp_path / TESTNET_EVENTS)
    record = mutate(record, "testnet", events_path=TESTNET_EVENTS, events_sha256=sha)
    decisions = check(tmp_path, record, LIVE)
    assert blocking_rules(decisions) == {ADOPT_TESTNET}
    assert "could not be read as a news calendar" in decisions[0].reason


def test_unverified_notes_say_what_is_not_checked(base: Path):
    record = valid_record(base)
    assert unverified_notes(record, BACKTEST) == unverified_notes(record, WALK_FORWARD) == []
    review = " ".join(unverified_notes(record, HUMAN_REVIEW))
    assert "do not prove the journals were produced" in review and "mark-to-market" in review
    live = unverified_notes(record, LIVE)
    assert any("R5 (news blackout) was not checked" in n for n in live)
    assert any("not journal-verifiable" in n for n in live)
    assert any("R8 structure stops" in n for n in live)
    for note in live:
        assert_one_sentence(note)


# ---------------------------------------------------------------------------- CLI
def run_check(record_path: Path, stage: str = LIVE, *extra: str) -> int:
    return main(["check", "--record", str(record_path), "--stage", stage, "--now", NOW_ISO, *extra])


def test_cli_init_writes_empty_record_with_default_fingerprint(tmp_path: Path, capsys):
    out = tmp_path / "rec.json"
    assert main(["init", "--variant", "base", "--out", str(out)]) == 0
    assert load_record(out) == empty_record("base")
    assert load_record(out).config_fingerprint == config_fingerprint(StrategyConfig())
    assert "next stage: BACKTEST" in capsys.readouterr().out


def test_cli_init_refuses_to_overwrite(tmp_path: Path, base: Path, capsys):
    out = tmp_path / "rec.json"
    save_record(valid_record(base), out)
    assert main(["init", "--variant", "base", "--out", str(out)]) == 1
    assert "refusing to overwrite" in capsys.readouterr().err
    assert load_record(out) == valid_record(base)
    assert main(["init", "--variant", "base", "--out", str(out), "--force"]) == 0
    assert load_record(out) == empty_record("base")
    assert main(["init", "--variant", " ", "--out", str(tmp_path / "x.json")]) == 1


def test_cli_check_pass_exit_0_and_prints_what_is_not_verified(tmp_path: Path, capsys):
    path = tmp_path / "rec.json"
    save_record(build_evidence(tmp_path), path)
    assert run_check(path) == 0
    out = capsys.readouterr().out
    assert out.startswith("PASS") and "LIVE" in out
    assert "Not verified by this check" in out
    assert "R5 (news blackout) was not checked" in out and "not journal-verifiable" in out
    assert run_check(path, "testnet") == 0  # stage names are case-insensitive on the CLI
    assert capsys.readouterr().out.startswith("PASS")


def test_cli_check_prints_each_blocking_reason_exit_1(tmp_path: Path, capsys):
    path = tmp_path / "rec.json"
    record = mutate(build_evidence(tmp_path), "human_review", trades_reviewed=N_ALL - 1)
    save_record(mutate(record, "testnet", end_utc="2024-04-14T00:00:00Z"), path)
    assert run_check(path) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("BLOCKED")
    blocking = [ln for ln in lines if ln.startswith("- [")]
    assert len(blocking) == 2
    assert blocking[0].startswith(f"- [{ADOPT_HUMAN_REVIEW}]")
    assert f"{N_ALL - 1} of {N_ALL}" in blocking[0]
    assert blocking[1].startswith(f"- [{ADOPT_TESTNET}]") and "13.00 days" in blocking[1]


def test_cli_check_checks_evidence_files_next_to_the_record(tmp_path: Path, base: Path, capsys):
    path = tmp_path / "rec.json"
    save_record(valid_record(base), path)  # no evidence files next to this copy
    assert run_check(path) == 1
    out = capsys.readouterr().out
    # data file, events file, bt report, wf report, 2 journals, review pack, testnet journal
    assert out.count("does not exist") == 8


def test_cli_init_then_check(tmp_path: Path, capsys):
    path = tmp_path / "rec.json"
    assert main(["init", "--variant", "base", "--out", str(path)]) == 0
    assert run_check(path, BACKTEST) == 0
    assert run_check(path, WALK_FORWARD) == 1
    out = capsys.readouterr().out
    assert f"[{ADOPT_BACKTEST}]" in out


def test_cli_config_overrides_and_fingerprint(tmp_path: Path, capsys):
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps({"reward_risk": 2.5}))
    path = tmp_path / "rec.json"
    assert main(["init", "--variant", "rr2.5", "--out", str(path), "--config", str(cfg_path)]) == 0
    tested = CFG.with_changes(reward_risk=2.5)
    assert load_record(path).config_fingerprint == config_fingerprint(tested)
    capsys.readouterr()
    assert run_check(path, BACKTEST, "--config", str(cfg_path)) == 0
    assert run_check(path, BACKTEST) == 1  # default config is not the tested one
    assert f"[{ADOPT_FINGERPRINT}]" in capsys.readouterr().out
    assert main(["fingerprint", "--config", str(cfg_path)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == [canonical_config_json(tested), config_fingerprint(tested)]


def test_cli_hash_prints_sha256_of_each_file(tmp_path: Path, capsys):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_bytes(b"hello\n")
    b.write_bytes(b"")
    assert main(["hash", str(a), str(b)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == [f"{sha256_file(a)}  {a}", f"{sha256_file(b)}  {b}"]
    assert main(["hash", str(tmp_path / "missing.txt")]) == 1
    assert "error:" in capsys.readouterr().err


def test_cli_errors(tmp_path: Path, base: Path, capsys):
    assert run_check(tmp_path / "missing.json") == 1
    assert "error:" in capsys.readouterr().err
    loose = tmp_path / "loose.json"
    loose.write_text(json.dumps({"reward_risk": 1.5}))
    path = tmp_path / "rec.json"
    save_record(valid_record(base), path)
    assert run_check(path, LIVE, "--config", str(loose)) == 1
    assert "reward_risk must be >= 2.0" in capsys.readouterr().err
    with pytest.raises(SystemExit) as info:
        main(["check", "--record", str(path), "--stage", "LIVE", "--now", "tomorrow"])
    assert info.value.code == 2
    with pytest.raises(SystemExit) as info:
        main(["check", "--record", str(path), "--stage", "PAPER"])
    assert info.value.code == 2


# ---------------------------------------------------------------------------- v2 A3 ML model
MODEL_FP = "ab" * 32  # a sha256 hex digest (as MLFilter.fingerprint() returns)
OTHER_FP = "cd" * 32


def ml_record(base: Path, model_fp: str | None = MODEL_FP, variant: str = "base+ml"):
    return dataclasses.replace(valid_record(base, variant=variant), model_fingerprint=model_fp)


def test_model_fingerprint_round_trips_and_is_optional_in_old_records(tmp_path: Path, base: Path):
    path = tmp_path / "rec.json"
    for record in (ml_record(base), empty_record("base+ml", CFG, MODEL_FP), valid_record(base)):
        save_record(record, path)
        assert load_record(path) == record
    save_record(valid_record(base), path)
    assert json.loads(path.read_text())["model_fingerprint"] is None
    # a record written before v2 A3 has no model_fingerprint key at all: still loads
    data = json.loads(path.read_text())
    del data["model_fingerprint"]
    path.write_text(json.dumps(data))
    old = load_record(path)
    assert old.model_fingerprint is None and old == valid_record(base)
    assert check(base, old, LIVE) == []
    assert empty_record("base+ml", model_fingerprint=MODEL_FP).model_fingerprint == MODEL_FP


@pytest.mark.parametrize("bad", ["5", "true", "[]", '{"a": 1}'])
def test_load_record_rejects_non_string_model_fingerprint(tmp_path: Path, bad: str):
    path = tmp_path / "rec.json"
    path.write_text(f'{{"variant": "b", "config_fingerprint": "x", "model_fingerprint": {bad}}}')
    with pytest.raises(ValueError, match="model_fingerprint must be a string or null"):
        load_record(path)


@pytest.mark.parametrize("stage", STAGES)
def test_matching_model_fingerprint_passes_every_stage(base: Path, stage: str):
    assert check(base, ml_record(base), stage, model_fingerprint=MODEL_FP) == []
    require_stage(ml_record(base), stage, CFG, NOW, base, MODEL_FP)  # no raise


def model_block(base: Path, record: AdoptionRecord, stage: str, supplied: str | None) -> str:
    decisions = check(base, record, stage, model_fingerprint=supplied)
    assert blocking_rules(decisions) == {ADOPT_FINGERPRINT}, decisions
    assert len(decisions) == 1
    return decisions[0].reason


@pytest.mark.parametrize("stage", STAGES)
def test_model_fingerprint_mismatch_blocks_every_stage(base: Path, stage: str):
    reason = model_block(base, ml_record(base), stage, OTHER_FP)
    assert "not the one that was tested" in reason
    assert MODEL_FP[:16] in reason and OTHER_FP[:16] in reason
    upper = model_block(base, ml_record(base), stage, MODEL_FP.upper())
    assert "not the one that was tested" in upper


@pytest.mark.parametrize("stage", STAGES)
def test_recorded_model_but_none_supplied_blocks_every_stage(base: Path, stage: str):
    assert "cannot be verified" in model_block(base, ml_record(base), stage, None)
    # a recorded model pins the variant even when its id has no "+ml" marker
    plain = ml_record(base, variant="base")
    assert "cannot be verified" in model_block(base, plain, stage, None)


@pytest.mark.parametrize("variant", ["base+ml", "rr2.5_vol2+ml", "base+ML"])
@pytest.mark.parametrize("stage", STAGES)
def test_ml_variant_without_recorded_model_fingerprint_blocks(base: Path, stage: str, variant):
    record = ml_record(base, model_fp=None, variant=variant)
    for supplied in (None, MODEL_FP):  # supplying one now cannot repair the record
        reason = model_block(base, record, stage, supplied)
        assert "has no model_fingerprint" in reason and "+ml" in reason


def test_model_fingerprint_supplied_for_a_record_without_one_blocks(base: Path):
    reason = model_block(base, valid_record(base), LIVE, MODEL_FP)
    assert "never tested under this record" in reason
    assert check(base, valid_record(base), LIVE, model_fingerprint=None) == []


@pytest.mark.parametrize("recorded", ["", "   ", "abc", "zz" * 32, MODEL_FP + "0"])
def test_malformed_recorded_model_fingerprint_blocks_even_if_supplied_equal(
    base: Path, recorded: str
):
    reason = model_block(base, ml_record(base, recorded), LIVE, recorded)
    assert "is not a sha256 hex digest" in reason


def test_model_and_config_mismatch_are_both_reported(base: Path):
    record = dataclasses.replace(
        valid_record(base, CFG.with_changes(reward_risk=2.5), "rr2.5+ml"),
        model_fingerprint=MODEL_FP,
    )
    decisions = check(base, record, LIVE, model_fingerprint=OTHER_FP)
    assert blocking_rules(decisions) == {ADOPT_FINGERPRINT} and len(decisions) == 2
    with pytest.raises(AdoptionBlocked) as info:
        require_stage(record, LIVE, CFG, NOW, base, OTHER_FP)
    assert len(info.value.decisions) == 2


def test_cli_model_fingerprint_check(tmp_path: Path, capsys):
    path = tmp_path / "rec.json"
    record = build_evidence(tmp_path, variant="base+ml")
    save_record(dataclasses.replace(record, model_fingerprint=MODEL_FP), path)
    assert run_check(path, LIVE, "--model-fingerprint", MODEL_FP) == 0
    out = capsys.readouterr().out
    assert out.startswith("PASS") and f"model {MODEL_FP[:16]}" in out
    assert run_check(path, LIVE, "--model-fingerprint", f"  {MODEL_FP}\n") == 0  # stripped
    capsys.readouterr()
    assert run_check(path, LIVE) == 1
    out = capsys.readouterr().out
    assert out.startswith("BLOCKED") and f"[{ADOPT_FINGERPRINT}]" in out
    assert "cannot be verified" in out
    assert run_check(path, LIVE, "--model-fingerprint", OTHER_FP) == 1
    assert "not the one that was tested" in capsys.readouterr().out


def test_cli_init_records_the_model_fingerprint(tmp_path: Path, capsys):
    path = tmp_path / "rec.json"
    argv = ["init", "--variant", "base+ml", "--out", str(path)]
    assert main(argv) == 1  # a +ml variant needs its model fingerprint
    assert "--model-fingerprint is required" in capsys.readouterr().err
    assert not path.exists()
    assert main([*argv, "--model-fingerprint", "not-a-hash"]) == 1
    assert "not a sha256 hex digest" in capsys.readouterr().err
    assert main([*argv, "--model-fingerprint", MODEL_FP]) == 0
    assert MODEL_FP in capsys.readouterr().out
    assert load_record(path) == empty_record("base+ml", CFG, MODEL_FP)
    assert run_check(path, BACKTEST, "--model-fingerprint", MODEL_FP) == 0
    assert run_check(path, BACKTEST) == 1
    assert main(["init", "--variant", "base", "--out", str(tmp_path / "b.json")]) == 0
    assert load_record(tmp_path / "b.json").model_fingerprint is None
