"""StrategyConfig must reject every loosening of a mandatory rule (orchestrator-owned)."""

import pytest

from research.trendbot.config import (
    MANDATE_MAX_RISK_PCT,
    RULE_IDS,
    ConfigError,
    PairRisk,
    StrategyConfig,
)


BASE = StrategyConfig()


@pytest.mark.parametrize(
    "changes",
    [
        {"ema_fast": 8},
        {"ema_slow": 20},
        {"ema_regime": 100},
        {"rsi_period": 10},
        {"rsi_min": 49.9},
        {"rsi_max": 70.1},
        {"rsi_min": 60.0, "rsi_max": 60.0},
        {"vol_lookback": 10},
        {"vol_mult": 1.49},
        {"reward_risk": 1.99},
        {"cluster_risk_budget_pct": 1.01},
        {"correlated_cluster": ("BTC", "ETH")},
        {"exclusive_bases": ()},
        {"news_blackout_hours": 1.99},
        {"bnb_event_blackout_hours": 23.9},
        {"consecutive_sl_limit": 4},
        {"consecutive_sl_limit": 0},
        {"bench_hours": 23.9},
        {"loss_window_days": 6.9},
        {"weekly_loss_limit_pct": 0.0},
        {"weekly_loss_limit_pct": 10.01},
        {"allow_leverage": True},
        {"fee_rate": 0.0},
        {"fee_rate": 0.0004},
        {"slippage_pct": 0.0},
        {"min_stop_distance_pct": 0.0},
        {"max_stop_distance_pct": 30.0},
        {"min_stop_distance_pct": 5.0, "max_stop_distance_pct": 5.0},
    ],
)
def test_loosening_is_rejected(changes):
    with pytest.raises(ConfigError):
        BASE.with_changes(**changes)


@pytest.mark.parametrize("base,cap", sorted(MANDATE_MAX_RISK_PCT.items()))
def test_pair_risk_caps(base, cap):
    pr = dict(BASE.pair_risk)
    pr[base] = PairRisk(max_risk_pct=cap * 1.01, stop_buffer_pct=pr[base].stop_buffer_pct)
    with pytest.raises(ConfigError):
        BASE.with_changes(pair_risk=pr)


@pytest.mark.parametrize("buffer", [0.49, 0.81, 0.0])
def test_bnb_buffer_range(buffer):
    pr = dict(BASE.pair_risk)
    pr["BNB"] = PairRisk(max_risk_pct=0.5, stop_buffer_pct=buffer)
    with pytest.raises(ConfigError):
        BASE.with_changes(pair_risk=pr)


def test_unknown_pair_has_no_cap():
    pr = dict(BASE.pair_risk)
    pr["SOL"] = PairRisk(max_risk_pct=1.0, stop_buffer_pct=0.3)
    with pytest.raises(ConfigError):
        BASE.with_changes(pair_risk=pr)


@pytest.mark.parametrize(
    "changes",
    [
        {"reward_risk": 3.0},
        {"vol_mult": 2.0},
        {"rsi_min": 55.0},
        {"news_blackout_hours": 4.0},
        {"bench_hours": 48.0},
        {"consecutive_sl_limit": 2},
        {"fee_rate": 0.006},
        {"weekly_loss_limit_pct": 2.0},
    ],
)
def test_tightening_is_allowed(changes):
    cfg = BASE.with_changes(**changes)
    assert not cfg.is_test_only


def test_regime_off_is_flagged_test_only():
    cfg = BASE.with_changes(regime_filter=False)
    assert cfg.is_test_only
    assert "regimeOFF" in cfg.variant_id()


def test_defaults_match_mandate():
    assert BASE.reward_risk >= 2.0
    assert BASE.risk_for("BTC/USDT").max_risk_pct == 1.0
    assert BASE.risk_for("ETH/USDT").max_risk_pct == 1.0
    assert BASE.risk_for("BNB/USDT").max_risk_pct == 0.5
    assert 0.5 <= BASE.risk_for("BNB/USDT").stop_buffer_pct <= 0.8
    assert (BASE.rsi_min, BASE.rsi_max, BASE.vol_mult) == (50.0, 70.0, 1.5)
    assert BASE.regime_filter
    assert (BASE.consecutive_sl_limit, BASE.bench_hours, BASE.loss_window_days) == (3, 24.0, 7.0)


def test_rule_ids_cover_every_mandatory_rule():
    for rid in (
        "R1_trend",
        "R2_momentum",
        "R3_volume",
        "R4_regime",
        "R5_news_blackout",
        "R6_correlation_cap",
        "R7_position_risk",
        "R8_structure_stop",
        "R9_circuit_breaker",
    ):
        assert rid in RULE_IDS
