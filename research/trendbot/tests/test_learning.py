"""Core Learning Architecture: rules from the ledger, guardrails, veto, lessons, files."""

import json

import pytest

from research.trendbot.config import StrategyConfig
from research.trendbot.learning import (
    LearnedRule,
    LearningBook,
    LearningFilter,
    LearningSettings,
    derive_rules,
    expected_outcome,
    lesson_for,
    select_active,
    validate_rule,
)
from research.trendbot.models import HOUR_MS, FeatureRow, SignalCheck, Trade
from research.trendbot.synthetic import make_world


CFG = StrategyConfig()


def _t(tid, vol, r, pair="BTC/USDT", hour=4.0, rsi=60.0):
    reason = "TP" if r > 0 else "SL"
    ts = 1_700_000_000_000 + tid * 10 * HOUR_MS
    return Trade(
        tid,
        pair,
        "base",
        ts - 4 * HOUR_MS,
        ts,
        100.0,
        95.0,
        110.0,
        1.0,
        5.0,
        1.0,
        "pivot",
        ts + 8 * HOUR_MS,
        100 + 5 * r,
        reason,
        0.0,
        5 * r,
        r,
        {
            "vol_ratio": vol,
            "rsi": rsi,
            "ema_gap_pct": 0.5,
            "dist_regime_pct": 3.0,
            "hour_utc": hour,
        },
    )


def _book_trades():
    weak = [_t(i, 1.55 + i * 0.001, -1.0) for i in range(1, 11)]
    weak[0] = _t(1, 1.55, 2.0)  # one weak-volume winner
    strong = [_t(20 + i, 2.6 + i * 0.05, 2.0 if i % 3 else -1.0) for i in range(12)]
    return weak + strong


def _row(vol, hour=4):
    return FeatureRow(0, 0, 100.0, 99.0, 98.0, 90.0, 60.0, 10.0, vol, 0.5, 3.0, hour)


CHECK = SignalCheck("BTC/USDT", 0, ())


def test_rule_is_learned_from_losing_low_volume_trades():
    rules = derive_rules(_book_trades(), LearningSettings())
    vol = [r for r in rules if r.feature == "vol_ratio" and r.op == "below"]
    assert vol, [r.text for r in rules]
    r = vol[0]
    assert r.avg_r < -0.25 and r.upper95 < 0 and r.n >= 8
    assert "Avoid volume below" in r.text and "trades lost" in r.text
    assert not [x for x in rules if x.feature == "vol_ratio" and x.op == "above"]


def test_too_few_trades_learn_nothing():
    assert derive_rules(_book_trades()[:6], LearningSettings()) == []


def test_select_active_respects_limits_and_operator_overrides():
    closed = _book_trades()
    s = LearningSettings(validate_on_history=False)
    rules = derive_rules(closed, s)
    active = [r for r in select_active(rules, closed, s, {}, None, CFG) if r.status == "active"]
    assert 1 <= len(active) <= s.max_active
    rid = active[0].id
    again = select_active(derive_rules(closed, s), closed, s, {rid: "disabled"}, None, CFG)
    assert next(r for r in again if r.id == rid).status == "disabled"
    tight = LearningSettings(validate_on_history=False, max_blocked_share=0.1)
    capped = select_active(derive_rules(closed, tight), closed, tight, {}, None, CFG)
    assert all(r.status != "active" for r in capped)


def test_filter_vetoes_only_matching_signals_and_advisory_never_blocks():
    rule = LearnedRule(
        "L1:vol_ratio:below:1.8",
        "vol_ratio",
        "below",
        1.8,
        "Avoid low volume.",
        10,
        1,
        -0.7,
        -0.3,
        [1],
        status="active",
    )
    ok, _, why = LearningFilter([rule])("BTC/USDT", _row(1.6), CHECK)
    assert not ok and "L1:vol_ratio" in why
    assert LearningFilter([rule])("BTC/USDT", _row(2.4), CHECK)[0]
    ok, _, why = LearningFilter([rule], enforce=False)("BTC/USDT", _row(1.6), CHECK)
    assert ok and why.startswith("advisory")
    assert LearningFilter.rule_id == "L_learned_rule"


def test_session_and_pair_rules_match():
    session = LearnedRule("s", "session", "in", (0, 8), "", 8, 0, -1, -0.5, [])
    assert session.matches("X", {"hour_utc": 4.0}) and not session.matches("X", {"hour_utc": 12})
    pair = LearnedRule("p", "pair", "is", "BNB/USDT", "", 8, 0, -1, -0.5, [])
    assert pair.matches("BNB/USDT", {}) and not pair.matches("BTC/USDT", {})


def test_lessons_are_plain_english():
    closed = _book_trades()
    loss = lesson_for(closed[3], closed[10:])
    assert loss.startswith("Trade #4 BTC/USDT was stopped out") and "volume" in loss
    win = lesson_for(closed[12], closed[10:])
    assert "reached its target" in win


def test_expected_outcome_uses_similar_trades():
    exp = expected_outcome("BTC/USDT", {}, _book_trades(), CFG)
    assert exp["at_target_r"] == 2.0 and exp["at_stop_r"] == -1.0
    assert exp["similar_trades"] == 22 and exp["breakeven_win_rate"] == pytest.approx(
        1 / 3, abs=1e-4
    )


def test_book_writes_ledger_and_learnings_and_reloads_overrides(tmp_path):
    book = LearningBook(tmp_path, CFG, LearningSettings(validate_on_history=False))
    trades = _book_trades()
    book.relearn(trades, None)
    ctx = {"1": {"why": ["trend: ok", "volume: 1.55x"], "expected": {"at_target_r": 2.0}}}
    book.set_override(book.rules[0].id, "disabled")
    book.write(trades, ctx)
    ledger = json.loads((tmp_path / "ledger.json").read_text())
    assert len(ledger) == len(trades)
    first = ledger[0]
    assert first["why_triggered"] == ["trend: ok", "volume: 1.55x"]
    assert first["lesson"] and first["signal_time"].endswith("Z")
    md = (tmp_path / "learnings.md").read_text()
    assert "## Learned rules" in md and "## Lessons" in md
    again = LearningBook(tmp_path, CFG, LearningSettings(validate_on_history=False))
    assert again.overrides == book.overrides and again.rules
    with pytest.raises(ValueError):
        book.set_override("x", "maybe")


def test_validation_backtests_both_halves_of_the_history():
    data, _ = make_world("planted", seed=2, years=1.2)
    rule = LearnedRule("L1", "vol_ratio", "below", 1.7, "", 10, 1, -0.7, -0.3, [])
    out = validate_rule(rule, data, CFG)
    assert set(out) >= {"ok", "older", "newer", "reason"}
    assert out["older"]["rule_n"] <= out["older"]["base_n"] + 5
    assert validate_rule(rule, {}, CFG)["ok"] is False
