import math
import random

import pytest

from research.trendbot.config import ConfigError, PairRisk, StrategyConfig
from research.trendbot.sizing import (
    ABSOLUTE_MAX_RISK_PCT,
    max_risk_pct,
    size_for_pair,
    size_position,
)


CFG = StrategyConfig()


def _cost(res, entry, cfg=CFG):
    return res.qty * entry * (1 + cfg.fee_rate)


def test_basic_size_is_derived_from_stop_distance():
    res = size_position(10_000.0, 100.0, 95.0, 1.0, CFG)
    assert res.qty == pytest.approx(20.0, rel=1e-12)
    assert res.stop_distance == 5.0
    assert res.risk_amount == pytest.approx(100.0, rel=1e-12)
    assert res.risk_pct == pytest.approx(1.0, rel=1e-12)
    assert res.notional == pytest.approx(2_000.0, rel=1e-12)
    assert res.capped_by is None


def test_wider_stop_means_smaller_position_same_risk():
    near = size_position(10_000.0, 100.0, 98.0, 1.0, CFG)
    far = size_position(10_000.0, 100.0, 96.0, 1.0, CFG)
    assert far.qty == pytest.approx(near.qty / 2, rel=1e-12)
    assert far.risk_amount == pytest.approx(near.risk_amount, rel=1e-12)


def test_risk_amount_equals_requested_risk_for_uncapped_grid():
    rng = random.Random(1234)
    checked = 0
    for _ in range(2_000):
        equity = rng.uniform(100.0, 1_000_000.0)
        entry = rng.uniform(0.5, 100_000.0)
        risk_pct = rng.uniform(0.05, 1.0)
        # Uncapped iff the stop distance fraction >= risk_pct/100 * (1 + fee_rate).
        min_frac = risk_pct / 100 * (1 + CFG.fee_rate) * 1.001
        stop = entry * (1 - rng.uniform(min_frac, 0.10))
        res = size_position(equity, entry, stop, risk_pct, CFG)
        assert res.capped_by is None
        want = equity * risk_pct / 100
        assert math.isclose(res.risk_amount, want, rel_tol=1e-9)
        assert math.isclose(res.risk_amount, res.qty * (entry - stop), rel_tol=1e-12)
        assert math.isclose(res.risk_pct, risk_pct, rel_tol=1e-9)
        checked += 1
    assert checked == 2_000


def test_max_risk_pct_mandated_caps():
    assert max_risk_pct("BTC/USDT", CFG) == 1.0
    assert max_risk_pct("ETH/USDT", CFG) == 1.0
    assert max_risk_pct("BNB/USDT", CFG) == 0.5


def test_max_risk_pct_follows_tightened_config_and_rejects_unknown_pair():
    tight = CFG.with_changes(
        pair_risk={
            "BTC": PairRisk(0.75, 0.25),
            "ETH": PairRisk(1.0, 0.25),
            "BNB": PairRisk(0.25, 0.65),
        }
    )
    assert max_risk_pct("BTC/USDT", tight) == 0.75
    assert max_risk_pct("BNB/USDT", tight) == 0.25
    with pytest.raises(ConfigError):
        max_risk_pct("SOL/USDT", CFG)


def test_bnb_cap_risks_half_of_btc_for_same_stop_distance():
    equity, entry, stop = 25_000.0, 400.0, 388.0
    btc = size_position(equity, entry, stop, max_risk_pct("BTC/USDT", CFG), CFG)
    bnb = size_position(equity, entry, stop, max_risk_pct("BNB/USDT", CFG), CFG)
    assert btc.capped_by is None and bnb.capped_by is None
    assert bnb.risk_amount == pytest.approx(btc.risk_amount / 2, rel=1e-12)
    assert bnb.qty == pytest.approx(btc.qty / 2, rel=1e-12)
    assert bnb.risk_amount == pytest.approx(equity * 0.5 / 100, rel=1e-9)


def test_tight_stop_is_notional_capped_and_risk_is_lower_never_higher():
    equity, entry, stop, risk = 10_000.0, 100.0, 99.9, 1.0  # 0.1% stop -> 100% notional
    res = size_position(equity, entry, stop, risk, CFG)
    assert res.capped_by == "notional"
    assert _cost(res, entry) <= equity
    assert res.qty == pytest.approx(equity / (entry * (1 + CFG.fee_rate)), rel=1e-12)
    assert res.risk_amount < equity * risk / 100
    assert res.risk_pct < risk
    assert res.risk_amount == pytest.approx(res.qty * (entry - stop), rel=1e-12)


def test_notional_cap_never_exceeds_equity_over_random_tight_stops():
    rng = random.Random(99)
    for _ in range(2_000):
        equity = rng.uniform(10.0, 1e7)
        entry = rng.uniform(0.01, 1e5)
        stop = entry * (1 - rng.uniform(1e-5, 0.009))
        risk = rng.uniform(0.1, 1.0)
        res = size_position(equity, entry, stop, risk, CFG)
        assert _cost(res, entry) <= equity
        assert res.risk_amount <= equity * risk / 100 * (1 + 1e-12)
        if res.capped_by == "notional":
            assert res.risk_pct < risk


def test_notional_boundary_exactly_fitting_is_not_capped():
    no_fee = CFG.with_changes(fee_rate=0.0)
    res = size_position(10_000.0, 100.0, 99.0, 1.0, no_fee)  # qty 100 -> notional == equity
    assert res.capped_by is None
    assert res.notional == pytest.approx(10_000.0, rel=1e-12)
    # The same trade with the default taker fee no longer fits and must be capped.
    capped = size_position(10_000.0, 100.0, 99.0, 1.0, CFG)
    assert capped.capped_by == "notional"
    assert _cost(capped, 100.0) <= 10_000.0


@pytest.mark.parametrize(
    ("equity", "entry", "stop", "risk"),
    [
        (10_000.0, 100.0, 100.0, 1.0),  # stop == entry
        (10_000.0, 100.0, 101.0, 1.0),  # stop above entry
        (0.0, 100.0, 95.0, 1.0),  # equity == 0
        (-5.0, 100.0, 95.0, 1.0),  # negative equity
        (10_000.0, 100.0, 95.0, 0.0),  # risk == 0
        (10_000.0, 100.0, 95.0, -0.5),  # negative risk
        (10_000.0, 100.0, 0.0, 1.0),  # stop at zero
        (10_000.0, float("nan"), 95.0, 1.0),
        (float("inf"), 100.0, 95.0, 1.0),
        (10_000.0, 100.0, 95.0, 1.01),  # above the largest mandated cap
    ],
)
def test_invalid_inputs_raise(equity, entry, stop, risk):
    with pytest.raises(ValueError):
        size_position(equity, entry, stop, risk, CFG)


def test_absolute_ceiling_is_the_largest_mandated_cap():
    assert ABSOLUTE_MAX_RISK_PCT == 1.0
    res = size_position(10_000.0, 100.0, 95.0, 1.0, CFG)  # exactly at the ceiling is allowed
    assert res.risk_pct == pytest.approx(1.0, rel=1e-12)


def test_size_for_pair_enforces_the_pair_cap():
    res = size_for_pair("BNB/USDT", 10_000.0, 300.0, 297.0, 0.5, CFG)
    assert res.risk_amount == pytest.approx(50.0, rel=1e-9)
    with pytest.raises(ValueError, match="BNB/USDT"):
        size_for_pair("BNB/USDT", 10_000.0, 300.0, 297.0, 0.6, CFG)
    btc = size_for_pair("BTC/USDT", 10_000.0, 60_000.0, 58_800.0, 1.0, CFG)
    assert btc.risk_amount == pytest.approx(100.0, rel=1e-9)


def test_deterministic():
    a = size_position(12_345.6, 43_210.0, 42_000.0, 0.8, CFG)
    b = size_position(12_345.6, 43_210.0, 42_000.0, 0.8, CFG)
    assert a == b
