import math
import random
from decimal import Decimal

import pytest

from research.trendbot.config import (
    MIN_FEE_RATE,
    MIN_SLIPPAGE_PCT,
    ConfigError,
    PairRisk,
    StrategyConfig,
)
from research.trendbot.sizing import (
    ABSOLUTE_MAX_RISK_PCT,
    cost_aware_target,
    loss_per_unit,
    max_risk_pct,
    size_for_pair,
    size_position,
    stop_fill_price,
)


CFG = StrategyConfig()  # fee_rate 0.001 per side, slippage 0.05 %
# Realistic cost grid: the config floor, Binance taker, a Coinbase-like low tier.
COST_CFGS = [
    CFG.with_changes(fee_rate=MIN_FEE_RATE, slippage_pct=MIN_SLIPPAGE_PCT),
    CFG,
    CFG.with_changes(fee_rate=0.006, slippage_pct=0.2),
]


def _cost(res, entry, cfg=CFG):
    return res.qty * entry * (1 + cfg.fee_rate)


def _unit_loss_by_hand(entry, stop, cfg):
    """A1 written out independently of sizing.py: price loss + entry fee + exit fee."""
    stop_x = stop * (1 - cfg.slippage_pct / 100)
    return (entry - stop_x) + cfg.fee_rate * entry + cfg.fee_rate * stop_x


def _stop_fill_pnl(res, entry, stop, cfg=CFG):
    """Net P&L of a fill exactly at stop*(1-slip), paying the fee on entry AND exit."""
    stop_x = stop * (1 - cfg.slippage_pct / 100)
    fees = cfg.fee_rate * res.qty * entry + cfg.fee_rate * res.qty * stop_x
    return res.qty * (stop_x - entry) - fees


# ---------------------------------------------------------------------------- A1 arithmetic
def test_basic_size_is_cost_aware_hand_arithmetic():
    # equity 10,000, entry 100, stop 95, risk 1 %, fee 0.1 %/side, slippage 0.05 %:
    #   S_x   = 95 * (1 - 0.0005)                  = 94.9525
    #   L_u   = (100 - 94.9525) + 0.1 + 0.0949525 = 5.0475 + 0.1949525 = 5.2424525
    #   qty   = 10,000 * 1 / 100 / 5.2424525       = 19.0750416908...
    #   risk  = qty * L_u                          = 100.00 (exactly 1 % of equity)
    assert (CFG.fee_rate, CFG.slippage_pct) == (0.001, 0.05)
    res = size_position(10_000.0, 100.0, 95.0, 1.0, CFG)
    assert stop_fill_price(95.0, CFG) == pytest.approx(94.9525, rel=1e-15)
    assert loss_per_unit(100.0, 95.0, CFG) == pytest.approx(5.2424525, rel=1e-14)
    assert res.qty == pytest.approx(19.0750416908880, rel=1e-12)
    assert res.qty == pytest.approx(100.0 / 5.2424525, rel=1e-14)
    assert res.stop_distance == 5.0  # stays entry - stop
    assert res.risk_amount == pytest.approx(100.0, rel=1e-12)
    assert res.risk_pct == pytest.approx(1.0, rel=1e-12)
    assert res.notional == pytest.approx(res.qty * 100.0, rel=1e-15)
    assert res.capped_by is None
    # The old price-only sizing (100 / 5 = 20 units) would lose 104.85 after costs.
    assert res.qty < 20.0


def test_stop_fill_with_both_fees_loses_exactly_risk_pct():
    equity, entry, stop, risk = 10_000.0, 100.0, 95.0, 1.0
    res = size_position(equity, entry, stop, risk, CFG)
    # Exact decimal hand arithmetic of the same trade (no binary rounding at all):
    stop_x = Decimal("95") * (1 - Decimal("0.05") / 100)
    assert stop_x == Decimal("94.9525")
    unit = (Decimal("100") - stop_x) + Decimal("0.001") * 100 + Decimal("0.001") * stop_x
    assert unit == Decimal("5.2424525")
    qty = Decimal("10000") * Decimal("1") / 100 / unit
    loss = qty * (Decimal("100") - stop_x) + Decimal("0.001") * qty * (100 + stop_x)
    assert abs(loss - Decimal("100")) < Decimal("1e-20")  # exactly 1 % of 10,000
    assert res.qty == pytest.approx(float(qty), rel=1e-14)
    # The float implementation, filled at stop*(1-slip) with both fees paid:
    pnl = _stop_fill_pnl(res, entry, stop)
    assert pnl == pytest.approx(-100.0, rel=1e-12)
    assert pnl == pytest.approx(-res.risk_amount, rel=1e-12)
    assert pnl / res.risk_amount == pytest.approx(-1.0, abs=1e-12)  # a clean stop is -1R
    assert -pnl / equity * 100 == pytest.approx(risk, rel=1e-12)


def test_bnb_loses_half_of_btc_for_the_same_geometry():
    equity, entry, stop = 25_000.0, 400.0, 388.0
    btc = size_for_pair("BTC/USDT", equity, entry, stop, max_risk_pct("BTC/USDT", CFG), CFG)
    bnb = size_for_pair("BNB/USDT", equity, entry, stop, max_risk_pct("BNB/USDT", CFG), CFG)
    assert btc.capped_by is None and bnb.capped_by is None
    btc_loss = -_stop_fill_pnl(btc, entry, stop)
    bnb_loss = -_stop_fill_pnl(bnb, entry, stop)
    assert btc_loss == pytest.approx(250.0, rel=1e-12)  # 1 % of 25,000 after all costs
    assert bnb_loss == pytest.approx(125.0, rel=1e-12)  # 0.5 % of 25,000 after all costs
    assert bnb_loss == pytest.approx(btc_loss / 2, rel=1e-12)
    assert bnb.qty == pytest.approx(btc.qty / 2, rel=1e-12)
    assert bnb.risk_amount == pytest.approx(btc.risk_amount / 2, rel=1e-12)
    assert bnb.stop_distance == btc.stop_distance == 12.0


def test_wider_stop_means_smaller_position_same_all_in_risk():
    near = size_position(10_000.0, 100.0, 98.0, 1.0, CFG)
    far = size_position(10_000.0, 100.0, 96.0, 1.0, CFG)
    assert far.qty < near.qty
    # qty scales with 1 / L_u, not 1 / (entry - stop): fees are a per-unit add-on, so twice
    # the stop distance is slightly LESS than twice the all-in loss per unit.
    ratio = _unit_loss_by_hand(100.0, 98.0, CFG) / _unit_loss_by_hand(100.0, 96.0, CFG)
    assert far.qty / near.qty == pytest.approx(ratio, rel=1e-12)
    assert far.qty > near.qty / 2
    assert far.risk_amount == pytest.approx(near.risk_amount, rel=1e-12)
    assert _stop_fill_pnl(far, 100.0, 96.0) == pytest.approx(-100.0, rel=1e-12)
    assert _stop_fill_pnl(near, 100.0, 98.0) == pytest.approx(-100.0, rel=1e-12)


@pytest.mark.parametrize("cfg", COST_CFGS, ids=["floor", "binance", "coinbase"])
def test_uncapped_grid_loses_exactly_the_requested_risk_after_costs(cfg):
    rng = random.Random(1234)
    for _ in range(2_000):
        equity = rng.uniform(100.0, 1_000_000.0)
        entry = rng.uniform(0.5, 100_000.0)
        risk_pct = rng.uniform(0.05, 1.0)
        # L_u > entry - stop, so this stop distance guarantees qty*entry*(1+fee) <= equity.
        min_frac = risk_pct / 100 * (1 + cfg.fee_rate) * 1.001
        stop = entry * (1 - rng.uniform(min_frac, 0.10))
        res = size_position(equity, entry, stop, risk_pct, cfg)
        assert res.capped_by is None
        want = equity * risk_pct / 100
        unit = _unit_loss_by_hand(entry, stop, cfg)
        assert math.isclose(res.risk_amount, want, rel_tol=1e-9)
        assert math.isclose(res.risk_amount, res.qty * unit, rel_tol=1e-12)
        assert math.isclose(res.risk_pct, risk_pct, rel_tol=1e-9)
        assert res.stop_distance == entry - stop
        assert math.isclose(_stop_fill_pnl(res, entry, stop, cfg), -want, rel_tol=1e-9)
        assert _cost(res, entry, cfg) <= equity


@pytest.mark.parametrize("rr", [2.0, 2.5, 3.0])
@pytest.mark.parametrize("cfg", COST_CFGS, ids=["floor", "binance", "coinbase"])
def test_cost_aware_target_nets_exactly_reward_risk(cfg, rr):
    equity, entry, stop = 10_000.0, 100.0, 97.0
    res = size_position(equity, entry, stop, 1.0, cfg)
    target = cost_aware_target(entry, stop, cfg, rr)
    net_win = res.qty * (target - entry) - cfg.fee_rate * res.qty * (entry + target)
    assert net_win / res.risk_amount == pytest.approx(rr, rel=1e-12)
    assert (target - entry) / (entry - stop) > rr  # price RR is automatically above rr
    assert cost_aware_target(entry, stop, cfg.with_changes(reward_risk=rr)) == target


# ---------------------------------------------------------------------------- caps
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


def test_tight_stop_is_notional_capped_and_risk_is_lower_never_higher():
    # S_x = 99.9 * 0.9995 = 99.85005; L_u = 0.14995 + 0.1 + 0.09985005 = 0.34980005, so the
    # uncapped qty 100 / 0.34980005 = 285.88 units (28,588 notional) would need leverage.
    equity, entry, stop, risk = 10_000.0, 100.0, 99.9, 1.0
    res = size_position(equity, entry, stop, risk, CFG)
    assert res.capped_by == "notional"
    assert _cost(res, entry) <= equity
    assert res.qty == pytest.approx(equity / (entry * (1 + CFG.fee_rate)), rel=1e-12)
    # risk_amount is recomputed from the SHRUNK qty with the all-in loss per unit.
    assert res.risk_amount == pytest.approx(res.qty * 0.34980005, rel=1e-12)
    assert res.risk_amount == pytest.approx(-_stop_fill_pnl(res, entry, stop), rel=1e-12)
    assert res.risk_amount < equity * risk / 100
    assert res.risk_pct < risk
    assert res.stop_distance == pytest.approx(0.1, rel=1e-12)


@pytest.mark.parametrize("cfg", COST_CFGS, ids=["floor", "binance", "coinbase"])
def test_notional_cap_never_exceeds_equity_over_random_tight_stops(cfg):
    rng = random.Random(99)
    for _ in range(2_000):
        equity = rng.uniform(10.0, 1e7)
        entry = rng.uniform(0.01, 1e5)
        stop = entry * (1 - rng.uniform(1e-5, 0.009))
        risk = rng.uniform(0.1, 1.0)
        res = size_position(equity, entry, stop, risk, cfg)
        assert _cost(res, entry, cfg) <= equity
        assert res.risk_amount <= equity * risk / 100 * (1 + 1e-12)
        assert math.isclose(
            res.risk_amount, res.qty * _unit_loss_by_hand(entry, stop, cfg), rel_tol=1e-12
        )
        if res.capped_by == "notional":
            assert res.risk_pct < risk


def test_zero_cost_configs_are_rejected_so_sizing_always_carries_costs():
    with pytest.raises(ConfigError):
        CFG.with_changes(fee_rate=0.0)
    with pytest.raises(ConfigError):
        CFG.with_changes(slippage_pct=0.0)


@pytest.mark.parametrize("cfg", COST_CFGS[:2], ids=["floor", "binance"])
def test_notional_boundary_with_realistic_costs(cfg):
    # Uncapped iff qty*E*(1+f) <= equity  <=>  risk/100 * E * (1+f) <= L_u. Solving
    # L_u = E*(1+f) - S*(1-s)*(1-f) for S gives the boundary stop:
    equity, entry, risk = 10_000.0, 100.0, 1.0
    f, s = cfg.fee_rate, cfg.slippage_pct / 100
    boundary = entry * (1 + f) * (1 - risk / 100) / ((1 - s) * (1 - f))
    assert boundary < entry
    assert math.isclose(
        _unit_loss_by_hand(entry, boundary, cfg), risk / 100 * entry * (1 + f), rel_tol=1e-9
    )
    # A hair wider than the boundary: fits without capping and uses (almost) all equity.
    fits = size_position(equity, entry, boundary * (1 - 1e-9), risk, cfg)
    assert fits.capped_by is None
    assert _cost(fits, entry, cfg) <= equity
    assert _cost(fits, entry, cfg) == pytest.approx(equity, rel=1e-6)
    assert fits.risk_amount == pytest.approx(equity * risk / 100, rel=1e-9)
    # A hair tighter: capped, never above equity, and the risk is continuous at the edge
    # (slightly LOWER than requested, never higher).
    capped = size_position(equity, entry, boundary * (1 + 1e-9), risk, cfg)
    assert capped.capped_by == "notional"
    assert _cost(capped, entry, cfg) <= equity
    assert capped.risk_amount < equity * risk / 100
    assert capped.risk_amount == pytest.approx(equity * risk / 100, rel=1e-6)


def test_high_costs_alone_exceed_one_pct_so_no_stop_needs_leverage():
    # Coinbase-like costs (0.6 %/side, 0.2 % slippage): L_u > E*(2f + s) = 1.4 % of E, above
    # the 1 % * (1 + f) boundary, so even a 1e-6 stop is sized without any notional cap.
    cfg = COST_CFGS[2]
    equity, entry, risk = 10_000.0, 100.0, 1.0
    f, s = cfg.fee_rate, cfg.slippage_pct / 100
    assert entry * (1 + f) * (1 - risk / 100) / ((1 - s) * (1 - f)) >= entry
    tiny = size_position(equity, entry, entry * (1 - 1e-6), risk, cfg)
    assert tiny.capped_by is None
    assert _cost(tiny, entry, cfg) <= equity
    assert tiny.risk_amount == pytest.approx(equity * risk / 100, rel=1e-9)
    assert _stop_fill_pnl(tiny, entry, entry * (1 - 1e-6), cfg) == pytest.approx(-100.0, rel=1e-9)


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
    assert _stop_fill_pnl(res, 300.0, 297.0) == pytest.approx(-50.0, rel=1e-9)
    with pytest.raises(ValueError, match="BNB/USDT"):
        size_for_pair("BNB/USDT", 10_000.0, 300.0, 297.0, 0.6, CFG)
    btc = size_for_pair("BTC/USDT", 10_000.0, 60_000.0, 58_800.0, 1.0, CFG)
    assert btc.risk_amount == pytest.approx(100.0, rel=1e-9)
    assert _stop_fill_pnl(btc, 60_000.0, 58_800.0) == pytest.approx(-100.0, rel=1e-9)


def test_deterministic():
    a = size_position(12_345.6, 43_210.0, 42_000.0, 0.8, CFG)
    b = size_position(12_345.6, 43_210.0, 42_000.0, 0.8, CFG)
    assert a == b
