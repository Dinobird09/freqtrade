import dataclasses
import json
import math
from pathlib import Path
from types import MappingProxyType

import pytest

from research.trendbot.adoption import (
    ADOPT_BACKTEST,
    ADOPT_FINGERPRINT,
    ADOPT_HUMAN_REVIEW,
    ADOPT_LIVE,
    ADOPT_RECORD,
    ADOPT_TESTNET,
    ADOPT_WALK_FORWARD,
    ADOPTION_RULE_IDS,
    LIVE,
    STAGE_RULES,
    STAGE_SECTIONS,
    STAGES,
    TESTNET,
    TESTNET_EXCHANGE,
    AdoptionBlocked,
    AdoptionRecord,
    BacktestStage,
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
    require_stage,
    save_record,
)
from research.trendbot.config import RULE_IDS, ConfigError, PairRisk, StrategyConfig
from research.trendbot.journal import iso_to_ms, write_journal
from research.trendbot.models import DAY_MS, EXIT_TP, HOUR_MS, Decision, Trade
from research.trendbot.review_sheet import auto_flags


CFG = StrategyConfig()
NOW_ISO = "2024-07-01T00:00:00Z"
NOW = iso_to_ms(NOW_ISO)
TESTNET_START = iso_to_ms("2024-04-01T00:00:00Z")

BT_REPORT = "bt/REPORT.md"
WF_REPORT = "wf/REPORT.md"
JOURNAL = "testnet/trades.csv"


def valid_record(cfg: StrategyConfig = CFG, variant: str = "base") -> AdoptionRecord:
    """Every stage complete; testnet lasts EXACTLY 14 days (the minimum)."""
    return AdoptionRecord(
        variant=variant,
        config_fingerprint=config_fingerprint(cfg),
        backtest=BacktestStage(report_path=BT_REPORT, completed_utc="2024-03-01T00:00:00Z"),
        walk_forward=WalkForwardStage(
            label="ROBUST",
            dd_ok=True,
            split_utc="2023-01-01T00:00:00Z",
            train_n=70,
            test_n=30,
            test_avg_r=0.21,
            report_path=WF_REPORT,
        ),
        human_review=HumanReviewStage(
            reviewer="Jane Doe",
            date_utc="2024-03-10T00:00:00Z",
            trades_reviewed=100,
            trades_total=100,
            approved=True,
            notes="checked every row against the charts",
        ),
        testnet=TestnetStage(
            exchange=TESTNET_EXCHANGE,
            start_utc="2024-04-01T00:00:00Z",
            end_utc="2024-04-15T00:00:00Z",
            trades=3,
            rule_violations=0,
            journal_path=JOURNAL,
            notes="",
        ),
    )


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


def a1_levels(entry: float, stop: float, cfg: StrategyConfig = CFG) -> tuple[float, float]:
    """(all-in loss per unit L_u, target T) exactly as CONTRACT.md v2 A1 defines them."""
    f, s = cfg.fee_rate, cfg.slippage_pct / 100
    stop_x = stop * (1 - s)
    loss_per_unit = (entry - stop_x) + f * entry + f * stop_x
    return loss_per_unit, (entry * (1 + f) + cfg.reward_risk * loss_per_unit) / (1 - f)


def make_testnet_trade(k: int, variant: str = "base", **changes: object) -> Trade:
    """A clean BTC take-profit (A1 cost-aware size and target, net RR exactly 2, risk 0.1%
    <= cap) entered k days into the testnet window."""
    signal = TESTNET_START + k * DAY_MS
    entry, stop, qty = 100.0, 95.0, 2.0
    loss_per_unit, target = a1_levels(entry, stop)
    risk_amount = qty * loss_per_unit
    fees = CFG.fee_rate * qty * (entry + target)
    pnl = qty * (target - entry) - fees  # == reward_risk * risk_amount by construction
    t = Trade(
        trade_id=k + 1,
        pair="BTC/USDT",
        variant=variant,
        signal_ts=signal,
        entry_ts=signal + 4 * HOUR_MS,
        entry_price=entry,
        stop=stop,
        target=target,
        qty=qty,
        risk_amount=risk_amount,
        risk_pct=0.1,
        stop_method="pivot",
        exit_ts=signal + 12 * HOUR_MS,
        exit_price=target,
        exit_reason=EXIT_TP,
        fees=fees,
        pnl=pnl,
        r_multiple=pnl / risk_amount,
    )
    return dataclasses.replace(t, **changes)


def write_evidence(base: Path, trades: list[Trade] | None = None) -> None:
    for rel in (BT_REPORT, WF_REPORT):
        (base / rel).parent.mkdir(parents=True, exist_ok=True)
        (base / rel).write_text("# report\n", encoding="utf-8")
    write_journal(
        trades if trades is not None else [make_testnet_trade(k) for k in range(3)], base / JOURNAL
    )


def test_testnet_fixture_trade_is_clean_under_the_review_flags():
    t = make_testnet_trade(0)
    assert auto_flags(t, CFG) == []
    assert math.isclose(t.r_multiple, CFG.reward_risk, rel_tol=1e-12)


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
    ],
)
def test_config_from_overrides_rejects_bad_or_loosening_values(overrides):
    with pytest.raises(ConfigError):
        config_from_overrides(overrides)


# ---------------------------------------------------------------------------- record JSON
def test_record_round_trip(tmp_path: Path):
    for record in (
        valid_record(),
        empty_record("base"),
        mutate(valid_record(), "live", enabled_utc=NOW_ISO),
    ):
        path = tmp_path / "sub" / "rec.json"
        save_record(record, path)
        assert load_record(path) == record


def test_empty_record_uses_default_fingerprint_and_empty_stages():
    record = empty_record("rr2.5_vol2")
    assert record.variant == "rr2.5_vol2"
    assert record.config_fingerprint == config_fingerprint(StrategyConfig())
    assert record.backtest == BacktestStage() and record.live == LiveStage()
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
    ],
)
def test_load_record_rejects_malformed_files(tmp_path: Path, text: str, match: str):
    path = tmp_path / "rec.json"
    path.write_text(text)
    with pytest.raises(ValueError, match=match):
        load_record(path)


# ---------------------------------------------------------------------------- promotion: pass
@pytest.mark.parametrize("stage", STAGES)
def test_fully_valid_record_passes_every_stage(stage: str):
    assert check_promotion(valid_record(), stage, CFG, NOW) == []


def test_testnet_of_exactly_14_days_passes_to_live():
    record = valid_record()
    start, end = iso_to_ms(record.testnet.start_utc), iso_to_ms(record.testnet.end_utc)
    assert end - start == 14 * DAY_MS
    assert check_promotion(record, LIVE, CFG, NOW) == []
    require_stage(record, LIVE, CFG, NOW)  # does not raise


def test_empty_record_may_only_start_the_backtest():
    record = empty_record("base")
    assert check_promotion(record, "BACKTEST", CFG, NOW) == []
    decisions = check_promotion(record, "WALK_FORWARD", CFG, NOW)
    assert blocking_rules(decisions) == {ADOPT_BACKTEST} and len(decisions) == 1
    decisions = check_promotion(record, LIVE, CFG, NOW)
    assert [d.rule for d in decisions] == [
        ADOPT_BACKTEST,
        ADOPT_WALK_FORWARD,
        ADOPT_HUMAN_REVIEW,
        ADOPT_TESTNET,
    ]


# ---------------------------------------------------------------------------- skipping stages
@pytest.mark.parametrize("skipped", STAGES[:-1])
def test_skipping_any_stage_blocks_every_later_stage(skipped: str):
    section = STAGE_SECTIONS[skipped]
    record = dataclasses.replace(
        valid_record(), **{section: type(getattr(valid_record(), section))()}
    )
    for target in STAGES[STAGES.index(skipped) + 1 :]:
        decisions = check_promotion(record, target, CFG, NOW)
        assert blocking_rules(decisions) == {STAGE_RULES[skipped]}, target
        assert len(decisions) == 1
        assert "no recorded result" in decisions[0].reason
    earlier = STAGES[STAGES.index(skipped)]
    assert check_promotion(record, earlier, CFG, NOW) == []


def test_skipping_human_review_to_testnet_blocks():
    record = dataclasses.replace(valid_record(), human_review=HumanReviewStage())
    decisions = check_promotion(record, TESTNET, CFG, NOW)
    assert blocking_rules(decisions) == {ADOPT_HUMAN_REVIEW}
    assert "HUMAN_REVIEW" in decisions[0].reason and "TESTNET" in decisions[0].reason


# ---------------------------------------------------------------------------- blocking conditions
BLOCKING_CASES = [
    # (id, section, changes, rule)
    ("backtest_no_report", "backtest", {"report_path": None}, ADOPT_BACKTEST),
    ("backtest_blank_report", "backtest", {"report_path": "  "}, ADOPT_BACKTEST),
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
    ("review_99_of_100", "human_review", {"trades_reviewed": 99}, ADOPT_HUMAN_REVIEW),
    ("review_101_of_100", "human_review", {"trades_reviewed": 101}, ADOPT_HUMAN_REVIEW),
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
        {"trades_reviewed": 90, "trades_total": 90},
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
]


@pytest.mark.parametrize(
    "section, changes, rule", [c[1:] for c in BLOCKING_CASES], ids=[c[0] for c in BLOCKING_CASES]
)
def test_each_blocking_condition_blocks_individually(section, changes, rule):
    record = mutate(valid_record(), section, **changes)
    stage = next(s for s, sec in STAGE_SECTIONS.items() if sec == section)
    for target in STAGES[STAGES.index(stage) + 1 :]:
        decisions = check_promotion(record, target, CFG, NOW)
        assert blocking_rules(decisions) == {rule}, (target, decisions)
        assert len(decisions) == 1, decisions
    assert check_promotion(record, stage, CFG, NOW) == []  # the stage itself may still start


def test_reasons_name_the_problem():
    def reason(section, **changes):
        (d,) = check_promotion(mutate(valid_record(), section, **changes), LIVE, CFG, NOW)
        return d.reason

    assert "99 of 100" in reason("human_review", trades_reviewed=99)
    assert "TRAIN-ONLY" in reason("walk_forward", label="TRAIN-ONLY")
    assert "13.00 days" in reason("testnet", end_utc="2024-04-14T00:00:00Z")
    assert "'binance'" in reason("testnet", exchange="binance")
    assert "train 70 + test 30" in reason("human_review", trades_reviewed=90, trades_total=90)


def test_future_times_block_the_stage_that_owns_them():
    late_review = mutate(valid_record(), "human_review", date_utc="2024-08-01T00:00:00Z")
    decisions = check_promotion(late_review, TESTNET, CFG, NOW)
    assert blocking_rules(decisions) == {ADOPT_HUMAN_REVIEW}
    # at LIVE the testnet additionally started before that (future) sign-off
    decisions = check_promotion(late_review, LIVE, CFG, NOW)
    assert blocking_rules(decisions) == {ADOPT_HUMAN_REVIEW, ADOPT_TESTNET}
    late_backtest = mutate(valid_record(), "backtest", completed_utc="2024-08-01T00:00:00Z")
    decisions = check_promotion(late_backtest, "WALK_FORWARD", CFG, NOW)
    assert blocking_rules(decisions) == {ADOPT_BACKTEST}


def test_several_problems_are_all_reported():
    record = mutate(valid_record(), "human_review", trades_reviewed=99, approved=False)
    record = mutate(record, "testnet", end_utc="2024-04-10T00:00:00Z", rule_violations=2)
    decisions = check_promotion(record, LIVE, CFG, NOW)
    assert blocking_rules(decisions) == {ADOPT_HUMAN_REVIEW, ADOPT_TESTNET}
    assert len(decisions) == 4


# ---------------------------------------------------------------------------- fingerprint / live
@pytest.mark.parametrize("stage", STAGES)
def test_fingerprint_mismatch_blocks_every_stage(stage: str):
    tested = CFG.with_changes(reward_risk=2.5)
    record = valid_record(tested)
    assert check_promotion(record, stage, tested, NOW) == []
    decisions = check_promotion(record, stage, CFG, NOW)  # promoting a different config
    assert blocking_rules(decisions) == {ADOPT_FINGERPRINT}
    assert "not the one that was tested" in decisions[0].reason


def test_missing_fingerprint_or_variant_blocks():
    record = dataclasses.replace(valid_record(), config_fingerprint="")
    assert blocking_rules(check_promotion(record, "BACKTEST", CFG, NOW)) == {ADOPT_FINGERPRINT}
    record = dataclasses.replace(valid_record(), variant=" ")
    assert blocking_rules(check_promotion(record, LIVE, CFG, NOW)) == {ADOPT_RECORD}


def test_test_only_config_may_reach_testnet_but_never_live():
    regime_off = CFG.with_changes(regime_filter=False)
    record = valid_record(regime_off)
    assert check_promotion(record, TESTNET, regime_off, NOW) == []
    decisions = check_promotion(record, LIVE, regime_off, NOW)
    assert blocking_rules(decisions) == {ADOPT_LIVE}
    assert "R4" in decisions[0].reason


def test_live_enabled_before_testnet_ended_blocks():
    early = mutate(valid_record(), "live", enabled_utc="2024-04-10T00:00:00Z")
    assert blocking_rules(check_promotion(early, LIVE, CFG, NOW)) == {ADOPT_LIVE}
    garbage = mutate(valid_record(), "live", enabled_utc="soon")
    assert blocking_rules(check_promotion(garbage, LIVE, CFG, NOW)) == {ADOPT_LIVE}
    later = mutate(valid_record(), "live", enabled_utc="2024-04-16T00:00:00Z")
    assert check_promotion(later, LIVE, CFG, NOW) == []


def test_unknown_stage_raises():
    with pytest.raises(ValueError, match="unknown adoption stage"):
        check_promotion(valid_record(), "PAPER", CFG, NOW)
    with pytest.raises(ValueError):
        check_promotion(valid_record(), "live", CFG, NOW)


def test_require_stage_raises_with_every_decision():
    record = mutate(valid_record(), "testnet", end_utc="2024-04-14T00:00:00Z")
    with pytest.raises(AdoptionBlocked) as info:
        require_stage(record, LIVE, CFG, NOW)
    assert info.value.stage == LIVE
    assert [d.rule for d in info.value.decisions] == [ADOPT_TESTNET]
    assert "ADOPT_testnet" in str(info.value)


def test_rule_ids_cover_every_stage():
    assert set(STAGE_RULES) == set(STAGES)
    assert set(STAGE_RULES.values()) | {ADOPT_RECORD, ADOPT_FINGERPRINT} == set(ADOPTION_RULE_IDS)


# ---------------------------------------------------------------------------- evidence files
def test_evidence_files_present_pass(tmp_path: Path):
    write_evidence(tmp_path)
    assert check_promotion(valid_record(), LIVE, CFG, NOW, base_dir=tmp_path) == []


def test_absolute_evidence_paths_are_used_as_is(tmp_path: Path):
    write_evidence(tmp_path)
    record = mutate(valid_record(), "backtest", report_path=str(tmp_path / BT_REPORT))
    assert check_promotion(record, LIVE, CFG, NOW, base_dir=tmp_path / "elsewhere") != []
    record = mutate(record, "walk_forward", report_path=str(tmp_path / WF_REPORT))
    record = mutate(record, "testnet", journal_path=str(tmp_path / JOURNAL))
    assert check_promotion(record, LIVE, CFG, NOW, base_dir=tmp_path / "elsewhere") == []


@pytest.mark.parametrize(
    "missing, rule",
    [(BT_REPORT, ADOPT_BACKTEST), (WF_REPORT, ADOPT_WALK_FORWARD), (JOURNAL, ADOPT_TESTNET)],
)
def test_missing_evidence_file_blocks(tmp_path: Path, missing: str, rule: str):
    write_evidence(tmp_path)
    (tmp_path / missing).unlink()
    decisions = check_promotion(valid_record(), LIVE, CFG, NOW, base_dir=tmp_path)
    assert blocking_rules(decisions) == {rule}
    assert "does not exist" in decisions[0].reason


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
    write_evidence(tmp_path, trades)
    decisions = check_promotion(valid_record(), LIVE, CFG, NOW, base_dir=tmp_path)
    assert blocking_rules(decisions) == {ADOPT_TESTNET}
    assert any(needle in d.reason for d in decisions), decisions


def test_unreadable_testnet_journal_blocks(tmp_path: Path):
    write_evidence(tmp_path)
    (tmp_path / JOURNAL).write_text("not,a,journal\n1,2,3\n")
    decisions = check_promotion(valid_record(), LIVE, CFG, NOW, base_dir=tmp_path)
    assert blocking_rules(decisions) == {ADOPT_TESTNET}
    assert "could not be read" in decisions[0].reason


# ---------------------------------------------------------------------------- CLI
def run_check(record_path: Path, stage: str = LIVE, *extra: str) -> int:
    return main(["check", "--record", str(record_path), "--stage", stage, "--now", NOW_ISO, *extra])


def test_cli_init_writes_empty_record_with_default_fingerprint(tmp_path: Path, capsys):
    out = tmp_path / "rec.json"
    assert main(["init", "--variant", "base", "--out", str(out)]) == 0
    assert load_record(out) == empty_record("base")
    assert load_record(out).config_fingerprint == config_fingerprint(StrategyConfig())
    assert "next stage: BACKTEST" in capsys.readouterr().out


def test_cli_init_refuses_to_overwrite(tmp_path: Path, capsys):
    out = tmp_path / "rec.json"
    save_record(valid_record(), out)
    assert main(["init", "--variant", "base", "--out", str(out)]) == 1
    assert "refusing to overwrite" in capsys.readouterr().err
    assert load_record(out) == valid_record()
    assert main(["init", "--variant", "base", "--out", str(out), "--force"]) == 0
    assert load_record(out) == empty_record("base")
    assert main(["init", "--variant", " ", "--out", str(tmp_path / "x.json")]) == 1


def test_cli_check_pass_exit_0(tmp_path: Path, capsys):
    write_evidence(tmp_path)
    path = tmp_path / "rec.json"
    save_record(valid_record(), path)
    assert run_check(path) == 0
    out = capsys.readouterr().out
    assert out.startswith("PASS") and "LIVE" in out
    assert run_check(path, "testnet") == 0  # stage names are case-insensitive on the CLI
    assert capsys.readouterr().out.startswith("PASS")


def test_cli_check_prints_each_blocking_reason_exit_1(tmp_path: Path, capsys):
    write_evidence(tmp_path)
    path = tmp_path / "rec.json"
    record = mutate(valid_record(), "human_review", trades_reviewed=99)
    save_record(mutate(record, "testnet", end_utc="2024-04-14T00:00:00Z"), path)
    assert run_check(path) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("BLOCKED")
    reasons = [ln for ln in lines if ln.startswith("- [")]
    assert len(reasons) == 2
    assert reasons[0].startswith(f"- [{ADOPT_HUMAN_REVIEW}]") and "99 of 100" in reasons[0]
    assert reasons[1].startswith(f"- [{ADOPT_TESTNET}]") and "13.00 days" in reasons[1]


def test_cli_check_checks_evidence_files_next_to_the_record(tmp_path: Path, capsys):
    path = tmp_path / "rec.json"
    save_record(valid_record(), path)  # no evidence files written
    assert run_check(path) == 1
    out = capsys.readouterr().out
    assert out.count("does not exist") == 3


def test_cli_init_then_check(tmp_path: Path, capsys):
    path = tmp_path / "rec.json"
    assert main(["init", "--variant", "base", "--out", str(path)]) == 0
    assert run_check(path, "BACKTEST") == 0
    assert run_check(path, "WALK_FORWARD") == 1
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
    assert run_check(path, "BACKTEST", "--config", str(cfg_path)) == 0
    assert run_check(path, "BACKTEST") == 1  # default config is not the tested one
    assert f"[{ADOPT_FINGERPRINT}]" in capsys.readouterr().out
    assert main(["fingerprint", "--config", str(cfg_path)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == [canonical_config_json(tested), config_fingerprint(tested)]


def test_cli_errors(tmp_path: Path, capsys):
    assert run_check(tmp_path / "missing.json") == 1
    assert "error:" in capsys.readouterr().err
    loose = tmp_path / "loose.json"
    loose.write_text(json.dumps({"reward_risk": 1.5}))
    path = tmp_path / "rec.json"
    save_record(valid_record(), path)
    assert run_check(path, LIVE, "--config", str(loose)) == 1
    assert "reward_risk must be >= 2.0" in capsys.readouterr().err
    with pytest.raises(SystemExit) as info:
        main(["check", "--record", str(path), "--stage", "LIVE", "--now", "tomorrow"])
    assert info.value.code == 2
    with pytest.raises(SystemExit) as info:
        main(["check", "--record", str(path), "--stage", "PAPER"])
    assert info.value.code == 2


def test_adoption_rule_ids_agree_with_config_rule_ids():
    adopt_ids = {k: v for k, v in RULE_IDS.items() if k.startswith("ADOPT_")}
    assert dict(ADOPTION_RULE_IDS) == adopt_ids
    assert list(ADOPTION_RULE_IDS) == list(adopt_ids)  # same order as the config listing


# ---------------------------------------------------------------------------- v2 A3 ML model
MODEL_FP = "ab" * 32  # a sha256 hex digest (as MLFilter.fingerprint() returns)
OTHER_FP = "cd" * 32


def ml_record(model_fp: str | None = MODEL_FP, variant: str = "base+ml") -> AdoptionRecord:
    return dataclasses.replace(valid_record(variant=variant), model_fingerprint=model_fp)


def test_model_fingerprint_round_trips_and_is_optional_in_old_records(tmp_path: Path):
    path = tmp_path / "rec.json"
    for record in (ml_record(), empty_record("base+ml", CFG, MODEL_FP), valid_record()):
        save_record(record, path)
        assert load_record(path) == record
    save_record(valid_record(), path)
    assert json.loads(path.read_text())["model_fingerprint"] is None
    # a record written before v2 A3 has no model_fingerprint key at all: still loads
    data = json.loads(path.read_text())
    del data["model_fingerprint"]
    path.write_text(json.dumps(data))
    old = load_record(path)
    assert old.model_fingerprint is None and old == valid_record()
    assert check_promotion(old, LIVE, CFG, NOW) == []
    assert empty_record("base+ml", model_fingerprint=MODEL_FP).model_fingerprint == MODEL_FP


@pytest.mark.parametrize("bad", ["5", "true", "[]", '{"a": 1}'])
def test_load_record_rejects_non_string_model_fingerprint(tmp_path: Path, bad: str):
    path = tmp_path / "rec.json"
    path.write_text(f'{{"variant": "b", "config_fingerprint": "x", "model_fingerprint": {bad}}}')
    with pytest.raises(ValueError, match="model_fingerprint must be a string or null"):
        load_record(path)


@pytest.mark.parametrize("stage", STAGES)
def test_matching_model_fingerprint_passes_every_stage(stage: str):
    assert check_promotion(ml_record(), stage, CFG, NOW, model_fingerprint=MODEL_FP) == []
    require_stage(ml_record(), stage, CFG, NOW, model_fingerprint=MODEL_FP)  # no raise


def model_block(record: AdoptionRecord, stage: str, supplied: str | None) -> str:
    decisions = check_promotion(record, stage, CFG, NOW, model_fingerprint=supplied)
    assert blocking_rules(decisions) == {ADOPT_FINGERPRINT}, decisions
    assert len(decisions) == 1
    return decisions[0].reason


@pytest.mark.parametrize("stage", STAGES)
def test_model_fingerprint_mismatch_blocks_every_stage(stage: str):
    reason = model_block(ml_record(), stage, OTHER_FP)
    assert "not the one that was tested" in reason
    assert MODEL_FP[:16] in reason and OTHER_FP[:16] in reason
    assert "not the one that was tested" in model_block(ml_record(), stage, MODEL_FP.upper())


@pytest.mark.parametrize("stage", STAGES)
def test_recorded_model_but_none_supplied_blocks_every_stage(stage: str):
    assert "cannot be verified" in model_block(ml_record(), stage, None)
    # a recorded model pins the variant even when its id has no "+ml" marker
    assert "cannot be verified" in model_block(ml_record(variant="base"), stage, None)


@pytest.mark.parametrize("variant", ["base+ml", "rr2.5_vol2+ml", "base+ML"])
@pytest.mark.parametrize("stage", STAGES)
def test_ml_variant_without_recorded_model_fingerprint_blocks(stage: str, variant: str):
    record = ml_record(model_fp=None, variant=variant)
    for supplied in (None, MODEL_FP):  # supplying one now cannot repair the record
        reason = model_block(record, stage, supplied)
        assert "has no model_fingerprint" in reason and "+ml" in reason


def test_model_fingerprint_supplied_for_a_record_without_one_blocks():
    reason = model_block(valid_record(), LIVE, MODEL_FP)
    assert "never tested under this record" in reason
    assert check_promotion(valid_record(), LIVE, CFG, NOW, model_fingerprint=None) == []


@pytest.mark.parametrize("recorded", ["", "   ", "abc", "zz" * 32, MODEL_FP + "0"])
def test_malformed_recorded_model_fingerprint_blocks_even_if_supplied_equal(recorded: str):
    reason = model_block(ml_record(recorded), LIVE, recorded)
    assert "is not a sha256 hex digest" in reason


def test_model_and_config_mismatch_are_both_reported():
    record = dataclasses.replace(
        valid_record(CFG.with_changes(reward_risk=2.5), "rr2.5+ml"), model_fingerprint=MODEL_FP
    )
    decisions = check_promotion(record, LIVE, CFG, NOW, model_fingerprint=OTHER_FP)
    assert blocking_rules(decisions) == {ADOPT_FINGERPRINT} and len(decisions) == 2
    with pytest.raises(AdoptionBlocked) as info:
        require_stage(record, LIVE, CFG, NOW, model_fingerprint=OTHER_FP)
    assert len(info.value.decisions) == 2


def test_cli_model_fingerprint_check(tmp_path: Path, capsys):
    write_evidence(tmp_path, [make_testnet_trade(k, variant="base+ml") for k in range(3)])
    path = tmp_path / "rec.json"
    save_record(ml_record(), path)
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
    assert run_check(path, "BACKTEST", "--model-fingerprint", MODEL_FP) == 0
    assert run_check(path, "BACKTEST") == 1
    assert main(["init", "--variant", "base", "--out", str(tmp_path / "b.json")]) == 0
    assert load_record(tmp_path / "b.json").model_fingerprint is None
