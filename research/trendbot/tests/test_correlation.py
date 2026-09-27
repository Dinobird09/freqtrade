import math
import random

import pytest

from research.trendbot.config import ConfigError, PairRisk, StrategyConfig
from research.trendbot.correlation import RULE, CorrelationGuard
from research.trendbot.sizing import max_risk_pct


CFG = StrategyConfig()
GUARD = CorrelationGuard(CFG)
BTC, ETH, BNB = "BTC/USDT", "ETH/USDT", "BNB/USDT"


def _check(pair, risk, open_risk, guard=GUARD):
    dec, allowed = guard.check(pair, risk, open_risk)
    assert dec.rule == RULE
    assert dec.reason.endswith(".")
    if not dec.allowed:
        assert allowed == 0.0
    return dec, allowed


def test_nothing_open_eth_allowed_at_full_size():
    dec, allowed = _check(ETH, 1.0, {})
    assert dec.allowed and allowed == 1.0


def test_btc_open_at_full_size_denies_eth():
    dec, allowed = _check(ETH, 1.0, {BTC: 1.0})
    assert not dec.allowed and allowed == 0.0
    assert "budget" in dec.reason and BTC in dec.reason


def test_bnb_open_denies_btc_and_eth():
    for pair in (BTC, ETH):
        dec, _ = _check(pair, 1.0, {BNB: 0.5})
        assert not dec.allowed
        assert "exclusive" in dec.reason and BNB in dec.reason


def test_btc_or_eth_open_denies_bnb_even_with_budget_left():
    for open_pair in (BTC, ETH):
        dec, _ = _check(BNB, 0.5, {open_pair: 0.25})  # 0.75% budget would be left
        assert not dec.allowed
        assert "exclusive" in dec.reason and open_pair in dec.reason


def test_nothing_open_bnb_allowed_at_its_cap():
    dec, allowed = _check(BNB, max_risk_pct(BNB, CFG), {})
    assert dec.allowed and allowed == 0.5


def test_no_pyramiding_on_an_open_pair_or_same_base():
    dec, _ = _check(BTC, 0.25, {BTC: 0.25})
    assert not dec.allowed and "pyramiding" in dec.reason
    dec, _ = _check(BTC, 0.25, {"BTC/USDC": 0.25})
    assert not dec.allowed and "BTC/USDC" in dec.reason
    dec, _ = _check(BTC, 0.5, {BTC: 0.0})  # zero planned risk is still an open position
    assert not dec.allowed


def test_partial_budget_shrinks_the_request():
    dec, allowed = _check(ETH, 1.0, {BTC: 0.6})
    assert dec.allowed
    assert allowed == pytest.approx(0.4, abs=1e-12)
    assert "reduced" in dec.reason and "requested 1%" in dec.reason


def test_remaining_below_minimum_is_skipped_not_shrunk():
    dec, allowed = _check(ETH, 1.0, {BTC: 0.8})  # 0.2% left < 0.25% minimum
    assert not dec.allowed and allowed == 0.0
    assert "minimum" in dec.reason
    dec, allowed = _check(ETH, 1.0, {BTC: 0.75})  # exactly the minimum is allowed
    assert dec.allowed and allowed == pytest.approx(0.25, abs=1e-12)
    dec, allowed = _check(ETH, 0.2, {})  # a request below the minimum is also skipped
    assert not dec.allowed


def test_pairs_outside_cluster_pass_and_do_not_use_budget():
    dec, allowed = _check("SOL/USDT", 0.7, {BTC: 1.0})
    assert dec.allowed and allowed == 0.7
    dec, allowed = _check("SOL/USDT", 0.7, {BNB: 0.5})
    assert dec.allowed and allowed == 0.7
    dec, allowed = _check(ETH, 1.0, {"SOL/USDT": 1.0})
    assert dec.allowed and allowed == 1.0
    dec, _ = _check(BNB, 0.5, {"SOL/USDT": 1.0})
    assert dec.allowed
    dec, _ = _check("SOL/USDT", 0.7, {"SOL/USDT": 0.7})  # still no pyramiding
    assert not dec.allowed


def test_two_full_size_positions_are_impossible_by_config():
    # "BTC/ETH caps 0.5 with a 1.0 cluster budget" would allow two full-size longs, so the
    # validated config refuses it: the cluster budget may not exceed the largest single cap.
    half = {"BTC": PairRisk(0.5, 0.25), "ETH": PairRisk(0.5, 0.25), "BNB": PairRisk(0.5, 0.65)}
    with pytest.raises(ConfigError, match="cluster"):
        CFG.with_changes(pair_risk=half, cluster_risk_budget_pct=1.0)
    legal = CFG.with_changes(pair_risk=half, cluster_risk_budget_pct=0.5)
    guard = CorrelationGuard(legal)
    dec, allowed = _check(BTC, 0.5, {}, guard)
    assert dec.allowed and allowed == 0.5
    dec, _ = _check(ETH, 0.5, {BTC: 0.5}, guard)
    assert not dec.allowed


def test_btc_and_eth_may_share_the_budget_at_half_size_but_not_a_third():
    cfg = CFG.with_changes(correlated_cluster=("BTC", "ETH", "BNB", "SOL"))
    guard = CorrelationGuard(cfg)
    dec, allowed = _check(BTC, 0.5, {}, guard)
    assert dec.allowed and allowed == 0.5
    dec, allowed = _check(ETH, 0.5, {BTC: 0.5}, guard)
    assert dec.allowed and allowed == 0.5
    both = {BTC: 0.5, ETH: 0.5}
    for third, risk in (("SOL/USDT", 0.5), (BNB, 0.5)):
        dec, allowed = _check(third, risk, both, guard)
        assert not dec.allowed and allowed == 0.0


def test_open_risk_order_does_not_change_the_decision():
    a = GUARD.check(ETH, 1.0, {BTC: 0.3, "SOL/USDT": 0.4})
    b = GUARD.check(ETH, 1.0, {"SOL/USDT": 0.4, BTC: 0.3})
    assert a == b


@pytest.mark.parametrize("bad", [0.0, -0.5, math.nan, math.inf])
def test_invalid_requested_risk_raises(bad):
    with pytest.raises(ValueError):
        GUARD.check(BTC, bad, {})


@pytest.mark.parametrize("bad", [-0.1, math.nan])
def test_invalid_open_risk_raises(bad):
    with pytest.raises(ValueError):
        GUARD.check(BTC, 1.0, {ETH: bad})


def _cluster_invariants(open_risk, cfg):
    cluster = [p for p in open_risk if p.split("/")[0] in cfg.correlated_cluster]
    total = math.fsum(open_risk[p] for p in cluster)
    assert total <= cfg.cluster_risk_budget_pct + 1e-9
    if len(cluster) > 1:
        assert BNB not in cluster  # BNB never stacks
        # never full-size longs on more than one cluster pair at a time
        full = [p for p in cluster if open_risk[p] >= max_risk_pct(p, cfg) - 1e-12]
        assert not full, open_risk


@pytest.mark.parametrize("seed", range(5))
@pytest.mark.parametrize("min_trade", [0.25, 0.1])
def test_random_open_close_sequences_respect_the_cap(seed, min_trade):
    cfg = CFG.with_changes(min_trade_risk_pct=min_trade)
    guard = CorrelationGuard(cfg)
    rng = random.Random(seed)
    open_risk: dict[str, float] = {}
    stacked = 0
    for _ in range(3_000):
        if open_risk and rng.random() < 0.35:
            open_risk.pop(rng.choice(sorted(open_risk)))
            continue
        pair = rng.choice((BTC, ETH, BNB))
        cap = max_risk_pct(pair, cfg)
        requested = rng.choice((cap, rng.uniform(0.05, cap)))
        dec, allowed = guard.check(pair, requested, open_risk)
        if dec.allowed:
            assert 0 < allowed <= requested
            assert allowed >= min_trade - 1e-12
            open_risk[pair] = allowed
        _cluster_invariants(open_risk, cfg)
        stacked += len(open_risk) > 1
    assert stacked > 0  # the sequence really exercised concurrent BTC+ETH positions
