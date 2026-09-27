"""backtester.py: no look-ahead, every mandatory rule end-to-end, and the execution model."""

from __future__ import annotations

import hashlib
import json
import math
import random
import time
from collections import Counter
from dataclasses import asdict, replace
from functools import cache

import pytest

from research.trendbot import backtester as bt
from research.trendbot.backtester import (
    enumerate_candidates,
    exit_on_candle,
    run_backtest,
    simulate_exit,
)
from research.trendbot.config import StrategyConfig
from research.trendbot.gatekeeper import Gatekeeper
from research.trendbot.invariants import blocked_open_trades, check_invariants
from research.trendbot.journal import ms_to_iso
from research.trendbot.models import DAY_MS, EXIT_END, EXIT_SL, EXIT_TP, HOUR_MS, Candle
from research.trendbot.structure import find_stop
from research.trendbot.synthetic import make_world
from research.trendbot.tests.bt_helpers import (
    TF,
    Scenario,
    bench_scenario,
    data_of,
    news,
    slot,
    ts,
    unit_loss,
)


CFG = StrategyConfig()
SLIP = CFG.slippage_pct / 100
FEE = CFG.fee_rate


@cache
def synth(world: str = "planted", seed: int = 3, years: float = 3.0):
    return make_world(world, seed, years=years)


def comparable(res: bt.BacktestResult) -> tuple:
    return (res.trades, res.decisions, res.decision_log, res.equity_curve, res.final_equity)


def denials(res: bt.BacktestResult, rule: str) -> list[tuple[int, str, str, str]]:
    return [d for d in res.decision_log if d[2] == rule]


def signal_idx(t) -> int:
    return (t.signal_ts - ts(0)) // TF


# ---------------------------------------------------------------------------- no look-ahead
@pytest.mark.parametrize("frac", [0.45, 0.93])
def test_end_ts_is_identical_to_truncated_data(frac):
    data, events = synth(years=2.0)
    candles = data["BTC/USDT"]
    end = candles[int(frac * len(candles))].ts + 1234  # also off-grid
    start = candles[len(candles) // 5].ts
    cut = {p: [c for c in cs if c.ts < end] for p, cs in data.items()}
    for s in (None, start):
        a = run_backtest(data, CFG, events, start_ts=s, end_ts=end)
        b = run_backtest(cut, CFG, events, start_ts=s)
        assert a.trades, "scenario must trade"
        assert comparable(a) == comparable(b)
        assert all(t.exit_ts < end and t.signal_ts < end for t in a.trades)
        if s is not None:
            assert all(t.signal_ts >= s for t in a.trades)
        assert a.window == (s, end)


def _perturb(candles: list[Candle], cut_ts: int, rng: random.Random) -> list[Candle]:
    out, price = [], None
    for c in candles:
        if c.ts < cut_ts:
            out.append(c)
            continue
        price = price or c.open
        o = price * math.exp(rng.gauss(0, 0.02))
        cl = o * math.exp(rng.gauss(0, 0.03))
        hi, lo = max(o, cl) * (1 + rng.random() * 0.03), min(o, cl) * (1 - rng.random() * 0.03)
        out.append(Candle(c.ts, o, hi, lo, cl, c.volume * rng.uniform(0.1, 6.0)))
        price = cl
    return out


@pytest.mark.parametrize("seed", [1, 2])
def test_perturbing_the_future_never_changes_the_past(seed):
    data, events = synth(years=2.0)
    n = len(data["BTC/USDT"])
    cut = data["BTC/USDT"][n // 2].ts
    rng = random.Random(seed)
    other = {p: _perturb(list(cs), cut, rng) for p, cs in data.items()}
    base, pert = run_backtest(data, CFG, events), run_backtest(other, CFG, events)

    def past(res):
        return [t for t in res.trades if t.signal_ts < cut and t.exit_ts < cut]

    assert past(base) and past(base) == past(pert)
    assert [e for e in base.equity_curve if e[0] < cut] == [
        e for e in pert.equity_curve if e[0] < cut
    ]
    early = cut - TF  # the fill of the last signal before `cut` reads candle `cut`
    assert [d for d in base.decision_log if d[0] < early] == [
        d for d in pert.decision_log if d[0] < early
    ]
    assert base.trades != pert.trades  # the perturbation did change the future


def test_enumerate_candidates_purges_unresolved_and_never_reads_the_future():
    data, events = synth(years=2.0)
    full = enumerate_candidates(data, CFG, events)
    straddler = full[len(full) // 2]
    end = straddler.exit_ts  # this candidate resolves AT end_ts: it must be dropped
    cut = {p: [c for c in cs if c.ts < end] for p, cs in data.items()}
    early = enumerate_candidates(data, CFG, events, end_ts=end)
    assert early and early == enumerate_candidates(cut, CFG, events)
    assert all(c.exit_ts < end and c.exit_reason in (EXIT_SL, EXIT_TP) for c in early)
    assert straddler not in early
    # Exactly the full-history candidates that resolved before `end`: nothing else is kept,
    # and a signal whose outcome needed data at/after `end` is DROPPED, not truncated.
    assert early == [c for c in full if c.signal_ts < end and c.exit_ts < end]
    start = early[len(early) // 3].signal_ts
    windowed = enumerate_candidates(data, CFG, events, start_ts=start, end_ts=end)
    assert windowed == [c for c in early if c.signal_ts >= start]


def test_candidates_use_the_backtest_exit_model_exactly():
    data, events = synth()
    res = run_backtest(data, CFG, events)
    cands = {(c.pair, c.signal_ts): c for c in enumerate_candidates(data, CFG, events)}
    matched = 0
    for t in res.trades:
        c = cands.get((t.pair, t.signal_ts))
        if c is None or t.exit_reason == EXIT_END:
            continue
        matched += 1
        assert (c.entry_ts, c.exit_ts, c.exit_reason) == (t.entry_ts, t.exit_ts, t.exit_reason)
        assert c.r_multiple == pytest.approx(t.r_multiple, rel=1e-12, abs=1e-12)
        assert c.features == t.features
    assert matched >= 0.9 * len(res.trades)
    assert len(cands) > len(res.trades)  # portfolio rules removed some candidates


# ---------------------------------------------------------------------------- R5 news
def test_news_blackout_blocks_entries_end_to_end():
    i = slot(0)
    btc = Scenario().spike(i)
    blocked = run_backtest(data_of(btc), CFG, [news(i, -1.5)])
    assert not blocked.trades
    [(when, pair, _, why)] = denials(blocked, "R5_news_blackout")
    assert (when, pair) == (ts(i), "BTC/USDT") and "macro" in why
    assert blocked.news_calendar_loaded
    allowed = run_backtest(data_of(btc), CFG, [news(i, 2.5)])
    assert [signal_idx(t) for t in allowed.trades] == [i]
    # BNB: a medium-impact burn 20h away blocks BNB only; BTC is unaffected.
    bnb = Scenario("BNB/USDT", start=10.0).spike(i)
    burn = [news(i, 20, scope="BNB", kind="bnb_burn", impact="medium")]
    assert not run_backtest(data_of(bnb), CFG, burn).trades
    assert run_backtest(data_of(btc), CFG, burn).trades
    assert not run_backtest(data_of(btc), CFG, []).news_calendar_loaded


# ---------------------------------------------------------------------------- R9 breakers
def test_three_stop_losses_bench_the_pair_for_24h():
    bnb = Scenario("BNB/USDT", start=10.0)
    for k in range(3):
        bnb.spike(slot(k)).stop_out(slot(k), CFG)
    # slot 3 decides 16h after the 3rd SL's exit candle closes, slot 4 after 40h
    bnb.spike(slot(3), slot(4))
    btc = Scenario().spike(slot(3)).take_profit(slot(3), CFG)  # other pairs are unaffected
    res = run_backtest(data_of(bnb, btc), CFG)
    bnb_trades = [t for t in res.trades if t.pair == "BNB/USDT"]
    assert [t.exit_reason for t in bnb_trades[:3]] == [EXIT_SL] * 3
    [(when, pair, _, why)] = denials(res, "R9_circuit_breaker")
    assert (when, pair) == (ts(slot(3)), "BNB/USDT") and "consecutive stop-losses" in why
    assert [signal_idx(t) for t in bnb_trades] == [slot(0), slot(1), slot(2), slot(4)]
    assert [signal_idx(t) for t in res.trades if t.pair == "BTC/USDT"] == [slot(3)]
    # A2: the bench runs 24h from the exit candle CLOSE, not from its recorded open.
    until = bnb_trades[2].exit_ts + TF + 24 * HOUR_MS
    assert any(f"benched until {ms_to_iso(until)}" in msg for _, msg in res.breaker_log)
    assert check_invariants(res, data_of(bnb, btc), (), CFG) == []


def test_bench_lasts_24h_from_the_exit_candle_close():
    """A2 end to end: the third SL fills somewhere inside the candle opening at
    ``ts(slot(2) + 1)``. The signal decided exactly 24h after that OPEN is still benched
    (only 20h after the candle's close, when the fill is certain); 24h after the close the
    pair trades again (the next signal, 44h after the close, is taken)."""
    bnb = bench_scenario(CFG)
    res = run_backtest(data_of(bnb), CFG)
    trades = res.trades
    assert [t.exit_reason for t in trades[:3]] == [EXIT_SL] * 3
    third = trades[2]
    assert third.exit_ts == third.entry_ts == ts(slot(2) + 1)  # stopped on its fill candle
    [(when, _, _, why)] = denials(res, "R9_circuit_breaker")
    decided = when + TF
    assert when == ts(slot(3)) and decided == third.exit_ts + 24 * HOUR_MS
    assert f"until {ms_to_iso(third.exit_ts + TF + 24 * HOUR_MS)}" in why
    assert "can have filled" in why
    assert [signal_idx(t) for t in trades] == [slot(0), slot(1), slot(2), slot(4)]
    assert trades[3].signal_ts + TF - (third.exit_ts + TF) >= 24 * HOUR_MS
    assert check_invariants(res, data_of(bnb), (), CFG) == []


def test_seven_day_loss_limit_halts_every_pair_then_lifts():
    btc, eth, bnb = Scenario(), Scenario("ETH/USDT", start=50.0), Scenario("BNB/USDT", 330, 10)
    btc.spike(slot(0)).stop_out(slot(0), CFG)
    eth.spike(slot(1)).stop_out(slot(1), CFG)
    btc.spike(slot(2)).stop_out(slot(2), CFG)  # BTC streak is only 2: no bench
    eth.spike(slot(3))
    bnb.spike(slot(4))
    # A2: the first loss counts from its exit candle CLOSE, ts(slot(0) + 3), for 7 days.
    first_certain = ts(slot(0) + 3)
    k_lift = next(k for k in range(5, 20) if ts(slot(k)) + TF >= first_certain + 7 * DAY_MS)
    eth.spike(slot(k_lift))
    data = data_of(btc, eth, bnb)
    res = run_backtest(data, CFG)
    losses = [t for t in res.trades if t.exit_reason == EXIT_SL]
    assert [t.r_multiple for t in losses] == pytest.approx([-1.0] * 3, abs=1e-12)
    # Three clean -1R stops at 1 % each (-100, -99, -98.01) exceed 3 % of the REALIZED equity.
    total = sum(t.pnl for t in losses)
    assert total == pytest.approx(-297.01, rel=1e-9)
    assert total < -0.03 * (CFG.starting_capital + total)
    halted = denials(res, "R9_circuit_breaker")
    assert [(signal_idx_ts(d[0]), d[1]) for d in halted] == [
        (slot(3), "ETH/USDT"),
        (slot(4), "BNB/USDT"),
    ]
    assert all("halted" in d[3] for d in halted)
    assert signal_idx(res.trades[-1]) == slot(k_lift) and res.trades[-1].pair == "ETH/USDT"
    assert check_invariants(res, data, (), CFG) == []


def signal_idx_ts(when: int) -> int:
    return (when - ts(0)) // TF


def test_exits_are_processed_while_the_halt_is_active():
    """A trade open when the halt trips still exits (TP) during the halt."""
    cfg = StrategyConfig(expectancy_guard=True, guard_window=1, weekly_loss_limit_pct=1.5)
    btc, eth = Scenario(), Scenario("ETH/USDT", start=50.0)
    btc.spike(slot(0)).stop_out(slot(0), cfg)  # BTC guard on: next BTC trade at 0.5 %
    btc.spike(slot(1)).stop_out(slot(1), cfg, j=slot(1) + 3)
    eth.spike(slot(1)).take_profit(slot(1), cfg, j=slot(2) + 2)  # opens alongside, 0.5 %
    eth.spike(slot(2))  # while ETH is open AND the halt is on: R9 comes first
    btc.spike(slot(2))
    data = data_of(btc, eth)
    res = run_backtest(data, cfg)
    by_pair = {(t.pair, signal_idx(t)): t for t in res.trades}
    t_btc, t_eth = by_pair[("BTC/USDT", slot(1))], by_pair[("ETH/USDT", slot(1))]
    assert t_btc.risk_pct == pytest.approx(0.5) and t_eth.risk_pct == pytest.approx(0.5)
    halt_ts = t_btc.exit_ts
    assert any("halted" in msg and when == halt_ts for when, msg in res.breaker_log)
    assert t_eth.entry_ts < halt_ts < t_eth.exit_ts and t_eth.exit_reason == EXIT_TP
    assert {d[1] for d in denials(res, "R9_circuit_breaker")} == {"BTC/USDT", "ETH/USDT"}
    assert blocked_open_trades(res) == [t_eth]
    assert check_invariants(res, data, (), cfg) == []


# ---------------------------------------------------------------------------- R6 correlation
def test_correlation_cap_denies_a_second_cluster_entry():
    btc, eth = Scenario(), Scenario("ETH/USDT", start=50.0)
    btc.spike(slot(0))
    eth.spike(slot(0), slot(1))  # same candle as BTC, then while BTC is still open
    res = run_backtest(data_of(btc, eth), CFG)
    assert [(t.pair, signal_idx(t)) for t in res.trades] == [("BTC/USDT", slot(0))]
    r6 = denials(res, "R6_correlation_cap")
    assert [(signal_idx_ts(d[0]), d[1]) for d in r6] == [
        (slot(0), "ETH/USDT"),
        (slot(1), "ETH/USDT"),
    ]
    assert "budget" in r6[0][3]


def test_bnb_never_stacks_with_another_cluster_position():
    btc, bnb = Scenario(), Scenario("BNB/USDT", start=10.0)
    btc.spike(slot(0))
    bnb.spike(slot(1))
    res = run_backtest(data_of(btc, bnb), CFG)
    assert [t.pair for t in res.trades] == ["BTC/USDT"]
    assert "exclusive" in denials(res, "R6_correlation_cap")[0][3]
    btc2, bnb2 = Scenario(), Scenario("BNB/USDT", start=10.0)
    bnb2.spike(slot(0))
    btc2.spike(slot(1))
    res2 = run_backtest(data_of(btc2, bnb2), CFG)
    assert [t.pair for t in res2.trades] == ["BNB/USDT"]
    assert "exclusive cluster position BNB/USDT" in denials(res2, "R6_correlation_cap")[0][3]


# ---------------------------------------------------------------------------- R7 / R8
def test_risk_caps_and_size_derived_from_the_stop():
    i = slot(0)
    btc, bnb = Scenario().spike(i), Scenario("BNB/USDT", start=10.0).spike(slot(8))
    res = run_backtest(data_of(btc, bnb), CFG)
    by_pair = {t.pair: t for t in res.trades}
    for pair, cap in (("BTC/USDT", 1.0), ("BNB/USDT", 0.5)):
        t = by_pair[pair]
        equity = CFG.starting_capital + sum(o.pnl for o in res.trades if o.exit_ts < t.entry_ts)
        lu = unit_loss(t.entry_price, t.stop, CFG)  # A1: all-in loss per unit at the stop
        assert lu > t.entry_price - t.stop  # fees and stop slippage make each unit riskier
        assert t.risk_pct == pytest.approx(cap, rel=1e-12)
        assert t.qty == pytest.approx(equity * cap / 100 / lu, rel=1e-12)
        assert t.risk_amount == pytest.approx(t.qty * lu, rel=1e-12)
        assert t.risk_amount == pytest.approx(equity * cap / 100, rel=1e-12)


def test_every_trade_has_rr_at_least_2_and_stop_below_structure():
    data, events = synth()
    for cfg in (CFG, CFG.with_changes(reward_risk=3.0)):
        res = run_backtest(data, cfg, events)
        assert len(res.trades) > 30
        for t in res.trades:
            # A1: the minimum holds NET of both fees and the stop slippage ...
            lu = unit_loss(t.entry_price, t.stop, cfg)
            net_win = (t.target - t.entry_price) - FEE * (t.entry_price + t.target)
            assert net_win / lu == pytest.approx(cfg.reward_risk, rel=1e-12)
            # ... so the price distance ratio is strictly larger than reward_risk.
            price_rr = (t.target - t.entry_price) / (t.entry_price - t.stop)
            assert price_rr > cfg.reward_risk >= 2.0
            candles = data[t.pair]
            i = next(k for k, c in enumerate(candles) if c.ts == t.signal_ts)
            plan, _ = find_stop(candles[: i + 1], i, t.pair, cfg)
            assert plan is not None and plan.stop == t.stop and plan.method == t.stop_method
            assert t.stop < plan.structure_level < candles[i].close
            buffer = cfg.risk_for(t.pair).stop_buffer_pct
            assert t.stop == pytest.approx(plan.structure_level * (1 - buffer / 100), rel=1e-15)
        assert check_invariants(res, data, events, cfg) == []


def test_free_cash_caps_the_second_position():
    """Tight stops + the guard's half risk: two positions may overlap, but the second one's
    notional is shrunk to the free cash (its risk only goes DOWN). BTC re-enters at 0.5 %
    behind the wick of its earlier stop-out (~1 % stop, ~40 % of equity); ETH's ~0.53 %
    stop (all-in ~0.78 % per unit) would need ~64 % of equity at 0.5 % risk."""
    cfg = StrategyConfig(expectancy_guard=True, guard_window=1)
    btc, eth = Scenario(amp=0.1), Scenario("ETH/USDT", start=50.0, amp=0.1)
    btc.spike(slot(0)).stop_out(slot(0), cfg)
    btc.spike(slot(1))
    eth.spike(slot(1))
    data = data_of(btc, eth)
    res = run_backtest(data, cfg)
    first, second = sorted(
        (t for t in res.trades if signal_idx(t) == slot(1)), key=lambda t: t.pair
    )
    assert first.pair == "BTC/USDT" and first.risk_pct == pytest.approx(0.5)
    assert "free_cash" in second.notes and second.risk_pct < 0.5
    assert second.risk_amount == pytest.approx(
        second.qty * unit_loss(second.entry_price, second.stop, cfg)
    )
    equity = CFG.starting_capital + res.trades[0].pnl
    cost = lambda t: t.qty * t.entry_price * (1 + cfg.fee_rate)  # noqa: E731
    assert cost(first) + cost(second) <= equity * (1 + 1e-12)
    assert check_invariants(res, data, (), cfg) == []


# ---------------------------------------------------------------------------- execution model
def _one_trade(scn: Scenario, cfg: StrategyConfig = CFG):
    res = run_backtest(data_of(scn), cfg)
    assert len(res.trades) == 1, res.decision_log[-3:]
    assert check_invariants(res, data_of(scn), (), cfg) == []
    return res.trades[0]


def test_gap_through_stop_fills_at_the_open_minus_slippage():
    i = slot(0)
    s = Scenario().spike(i)
    gap = s.stop(i, CFG) * 0.97
    s.gap_open(i + 3, gap)
    t = _one_trade(s)
    assert (t.exit_reason, t.exit_ts) == (EXIT_SL, ts(i + 3))
    x = gap * (1 - SLIP)
    assert t.exit_price == pytest.approx(x, rel=1e-15)
    # The only way to lose more than the planned all-in 1R: price loss to the gap + fees.
    lu = unit_loss(t.entry_price, t.stop, CFG)
    expected = -((t.entry_price - x) + FEE * (t.entry_price + x)) / lu
    assert t.r_multiple == pytest.approx(expected, rel=1e-12) and t.r_multiple < -1.0


def test_candle_touching_stop_and_target_is_a_stop_loss():
    i = slot(0)
    s = Scenario().spike(i)
    s.stop_out(i, CFG, j=i + 2).take_profit(i, CFG, j=i + 2)
    t = _one_trade(s)
    assert (t.exit_reason, t.exit_ts) == (EXIT_SL, ts(i + 2))
    assert t.exit_price == pytest.approx(t.stop * (1 - SLIP), rel=1e-15)
    assert t.r_multiple == pytest.approx(-1.0, abs=1e-12)  # a clean stop is exactly -1R


def test_take_profit_is_a_limit_fill_without_slippage():
    i = slot(0)
    s = Scenario().spike(i).take_profit(i, CFG, j=i + 3)
    t = _one_trade(s)
    assert (t.exit_reason, t.exit_ts, t.exit_price) == (EXIT_TP, ts(i + 3), t.target)
    assert t.r_multiple == pytest.approx(2.0, abs=1e-12)  # a take-profit is exactly +2R net


def test_fee_and_r_arithmetic_by_hand():
    """A1 by hand from the candles: E = next open * 1.0005, S_x = S * 0.9995,
    L_u = (E - S_x) + 0.001 * (E + S_x), qty = 100 / L_u, T = (1.001 E + 2 L_u) / 0.999.
    A take-profit then nets exactly +200 = +2R and a clean stop exactly -100 = -1R."""
    i = slot(0)
    s = Scenario().spike(i).take_profit(i, CFG)
    t = _one_trade(s)
    entry = s.candles[i + 1].open * 1.0005
    stop = s.stop(i, CFG)
    stop_x = stop * 0.9995
    lu = (entry - stop_x) + 0.001 * entry + 0.001 * stop_x
    qty = 10_000 * 0.01 / lu
    target = (entry * 1.001 + 2 * lu) / 0.999
    fees = 0.001 * qty * entry + 0.001 * qty * target
    pnl = qty * (target - entry) - fees
    assert (t.entry_price, t.stop, t.target) == pytest.approx((entry, stop, target), rel=1e-14)
    assert (t.qty, t.risk_amount) == pytest.approx((qty, qty * lu), rel=1e-12)
    assert t.risk_amount == pytest.approx(100.0, rel=1e-12) and t.risk_pct == pytest.approx(1.0)
    assert t.fees == pytest.approx(fees, rel=1e-12) and t.pnl == pytest.approx(pnl, rel=1e-12)
    assert t.pnl == pytest.approx(200.0, rel=1e-12)
    assert t.r_multiple == pytest.approx(2.0, abs=1e-12)
    # The costs moved into the size and the target: the target sits further than 2x the stop.
    assert t.target - entry == pytest.approx(2 * (0.001 * entry + lu) / 0.999, rel=1e-12)
    sl = _one_trade(Scenario().spike(i).stop_out(i, CFG))
    loss = sl.qty * (stop_x - sl.entry_price) - FEE * sl.qty * (sl.entry_price + stop_x)
    assert sl.exit_price == pytest.approx(stop_x, rel=1e-15)
    assert sl.pnl == pytest.approx(loss, rel=1e-12) and sl.pnl == pytest.approx(-100.0, rel=1e-12)
    assert sl.r_multiple == pytest.approx(-1.0, abs=1e-12)


def test_outcomes_in_r_are_exact_on_a_synthetic_run():
    """A1 across a whole run: every clean stop is -1R and every take-profit +reward_risk R
    (to 1e-9), forced closes lie strictly between, and nothing loses more than 1R (synthetic
    candles open at the previous close, so no stop is ever gapped through)."""
    data, events = synth()
    for cfg in (CFG, CFG.with_changes(reward_risk=3.0)):
        res = run_backtest(data, cfg, events)
        kinds: Counter[str] = Counter()
        for t in res.trades:
            kinds[t.exit_reason] += 1
            if t.exit_reason == EXIT_TP:
                assert t.r_multiple == pytest.approx(cfg.reward_risk, abs=1e-9)
            elif t.exit_reason == EXIT_SL:
                assert t.exit_price == pytest.approx(t.stop * (1 - SLIP), rel=1e-15)
                assert t.r_multiple == pytest.approx(-1.0, abs=1e-9)
            else:
                assert -1.0 < t.r_multiple < cfg.reward_risk
        assert kinds[EXIT_TP] >= 10 and kinds[EXIT_SL] >= 10, kinds


def test_entry_that_gaps_through_the_stop_is_skipped():
    i = slot(0)
    s = Scenario().spike(i)
    s.gap_open(i + 1, s.stop(i, CFG) * 0.995)
    res = run_backtest(data_of(s), CFG)
    assert not res.trades
    [(_, _, rule, why)] = [d for d in res.decision_log if d[0] == ts(i)]
    assert rule == "R8_structure_stop" and "gapped through stop" in why


# A gaps=True world in which both gap paths occur (found by scanning seeds 1-40 of 3-year
# planted worlds: seeds 12, 29 and 31 have both; a fill-candle gap below the stop is rare).
GAP_WORLD = ("planted", 29, 3.0)


@cache
def gapped_run():
    world, seed, years = GAP_WORLD
    data, events = make_world(world, seed, years=years, gaps=True)
    return data, events, run_backtest(data, CFG, events)


def test_gaps_world_exercises_gap_through_exits_and_the_entry_skip():
    """End to end on synthetic.make_world(gaps=True): a stop gapped through at the open fills
    at open * (1 - slippage) for worse than -1R, a fill candle opening below the stop skips
    the entry (R8 "gapped through stop"), and the independent audit stays clean."""
    data, events, res = gapped_run()
    index = {p: {c.ts: k for k, c in enumerate(cs)} for p, cs in data.items()}
    gap_exits = [
        t
        for t in res.trades
        if t.exit_reason == EXIT_SL and data[t.pair][index[t.pair][t.exit_ts]].open <= t.stop
    ]
    assert gap_exits, "no stop was gapped through"
    for t in gap_exits:
        c = data[t.pair][index[t.pair][t.exit_ts]]
        assert c.open != data[t.pair][index[t.pair][t.exit_ts] - 1].close  # a real gap
        assert t.exit_price == pytest.approx(c.open * (1 - SLIP), rel=1e-15)
        assert t.r_multiple < -1.0
    skips = [d for d in denials(res, "R8_structure_stop") if "gapped through stop" in d[3]]
    assert skips, "no entry was skipped for a fill below the stop"
    for signal_ts, pair, _rule, _why in skips:
        i = index[pair][signal_ts]
        plan, why = find_stop(data[pair], i, pair, CFG)
        assert plan is not None, why
        fill = data[pair][i + 1]
        assert fill.ts == signal_ts + TF and fill.open <= plan.stop
        assert not any(t.pair == pair and t.signal_ts == signal_ts for t in res.trades)
    assert check_invariants(res, data, events, CFG) == []


@pytest.mark.parametrize("mult", [1.001, 1.2])
def test_stop_distance_is_rechecked_against_the_actual_fill(mult):
    i = slot(0)
    s = Scenario().spike(i)
    stop = s.stop(i, CFG)
    s.gap_open(i + 1, stop * mult)  # 0.15 % (too tight) or ~15 % (too wide) from the fill
    res = run_backtest(data_of(s), CFG)
    assert not res.trades
    [(_, _, rule, why)] = [d for d in res.decision_log if d[0] == ts(i)]
    assert rule == "R8_structure_stop" and "actual fill" in why


def test_open_trade_is_force_closed_at_the_last_close_before_end_ts():
    i = slot(0)
    s = Scenario().spike(i)
    end = ts(i + 5)
    res = run_backtest(data_of(s), CFG, end_ts=end)
    [t] = res.trades
    assert (t.exit_reason, t.exit_ts) == (EXIT_END, ts(i + 4))
    assert t.exit_price == pytest.approx(s.candles[i + 4].close * (1 - SLIP), rel=1e-15)
    assert check_invariants(res, data_of(s), (), CFG) == []


def test_data_gap_at_the_fill_skips_the_entry():
    i = slot(0)
    s = Scenario().spike(i)
    del s.candles[i + 1]
    res = run_backtest(data_of(s), CFG)
    assert not res.trades
    [(_, _, rule, why)] = [d for d in res.decision_log if d[0] == ts(i)]
    assert rule == "X_capital" and "data gap" in why


def test_exit_model_primitives():
    c = Candle(0, 100.0, 112.0, 89.0, 105.0, 1.0)
    assert exit_on_candle(c, 90.0, 110.0, 0.001) == (90.0 * 0.999, EXIT_SL)  # both touched
    assert exit_on_candle(c, 95.0, 110.0, 0.001) == (95.0 * 0.999, EXIT_SL)
    assert exit_on_candle(c, 101.0, 110.0, 0.001) == (100.0 * 0.999, EXIT_SL)  # opened below
    assert exit_on_candle(c, 88.0, 110.0, 0.001) == (110.0, EXIT_TP)
    assert exit_on_candle(c, 88.0, 113.0, 0.001) is None
    assert exit_on_candle(replace(c, open=87.0, low=86.0), 88.0, 113.0, 0.0) == (87.0, EXIT_SL)
    path = [c, replace(c, high=120.0)]
    assert simulate_exit(path, 0, 100.0, 80.0, 115.0, CFG) == (1, 115.0, EXIT_TP)
    assert simulate_exit(path, 0, 100.0, 80.0, 115.0, CFG, last_idx=0) is None
    with pytest.raises(ValueError):
        simulate_exit(path, 0, 100.0, 101.0, 115.0, CFG)


# ---------------------------------------------------------------------------- D4 stop-fill stress
def test_wick_parameter_of_the_exit_primitive():
    c = Candle(0, 100.0, 112.0, 89.0, 105.0, 1.0)
    for k in (0.0, 0.5, 1.0):
        # A non-gap stop fills a share k of the way from the stop down to the candle low.
        assert exit_on_candle(c, 95.0, 110.0, 0.001, k) == ((95.0 - k * 6.0) * 0.999, EXIT_SL)
        assert exit_on_candle(c, 101.0, 110.0, 0.001, k) == (100.0 * 0.999, EXIT_SL)  # gap
        assert exit_on_candle(c, 88.0, 110.0, 0.001, k) == (110.0, EXIT_TP)  # TP untouched
    assert exit_on_candle(c, 95.0, 110.0, 0.001, 0.0) == exit_on_candle(c, 95.0, 110.0, 0.001)


@pytest.mark.parametrize("k", [0.5, 1.0])
def test_stop_fill_stress_moves_only_the_non_gap_stop_fill(k):
    """D4: with stop_fill_wick_k = k a clean stop fills at (S - k(S - low)) * (1 - s), so it
    loses MORE than the planned 1R; entry, size, stop and target are unchanged, gap-through
    stops and take-profits are unchanged, and the ML labels use the same model."""
    cfg = CFG.with_changes(stop_fill_wick_k=k)
    assert cfg.is_test_only  # a stress config can never be adopted (D1)
    i = slot(0)
    s = Scenario().spike(i)
    stop = s.stop(i, CFG)
    low = stop * 0.99
    s.low(i + 2, low)
    base, stressed = _one_trade(s), _one_trade(s, cfg)
    assert (stressed.entry_price, stressed.qty, stressed.stop, stressed.target) == (
        base.entry_price,
        base.qty,
        base.stop,
        base.target,
    )
    assert base.exit_price == pytest.approx(stop * (1 - SLIP), rel=1e-15)
    fill = (stop - k * (stop - low)) * (1 - SLIP)
    assert (stressed.exit_reason, stressed.exit_ts) == (EXIT_SL, ts(i + 2))
    assert stressed.exit_price == pytest.approx(fill, rel=1e-15)
    lu = unit_loss(stressed.entry_price, stop, CFG)
    by_hand = -((stressed.entry_price - fill) + FEE * (stressed.entry_price + fill)) / lu
    assert stressed.r_multiple == pytest.approx(by_hand, rel=1e-12)
    assert base.r_multiple == pytest.approx(-1.0, abs=1e-12) and stressed.r_multiple < -1.0
    [cand] = enumerate_candidates(data_of(s), cfg)
    assert cand.r_multiple == pytest.approx(stressed.r_multiple, rel=1e-12)
    # A gap through the stop and a take-profit are the same trade under the stress.
    gap = Scenario().spike(i).gap_open(i + 3, stop * 0.97)
    tp = Scenario().spike(i).take_profit(i, CFG, j=i + 3)
    for scn in (gap, tp):
        assert _one_trade(scn, cfg) == _one_trade(scn)


# The default (k = 0) engine must stay byte-identical. These digests were computed from the
# committed engine BEFORE the D4 stress / D5 live-adapter changes (cycle-3 baseline); a
# legitimate model change elsewhere (indicators, sizing, news, breakers, synthetic worlds)
# changes them too and must then be re-pinned deliberately.
DEFAULT_RUN_DIGESTS = {
    ("planted", 1, 6.0, False): (
        166,
        "c9cf97880fe04c0dc09f756b5f176814311abaf0c498c8295d55301ade1cade4",
        "4bde8937443b4af7e0d9554ff78e52d27379907ba1258e8697c53bf8431ce53d",
        16736.4283577954,
    ),
    ("zero_edge", 2, 3.0, True): (
        74,
        "f4dade9cc333bcefa2ba12088d703a5e626c4f45b54d977758661a44ffa5a7a6",
        "9934cd5a046faa1154dacedc4fec7c7792f4c93e091007bd49698f23672b7aef",
        10395.177984134729,
    ),
}


def _digest(rows) -> str:
    blob = json.dumps([asdict(r) for r in rows], sort_keys=True, allow_nan=False)
    return hashlib.sha256(blob.encode()).hexdigest()


@pytest.mark.parametrize("key", sorted(DEFAULT_RUN_DIGESTS))
def test_default_run_is_byte_identical_to_the_pinned_digest(key):
    world, seed, years, gaps = key
    n, trades_sha, candidates_sha, final_equity = DEFAULT_RUN_DIGESTS[key]
    assert StrategyConfig(stop_fill_wick_k=0.0) == CFG  # k = 0 IS the default config
    data, events = make_world(world, seed, years=years, gaps=gaps)
    res = run_backtest(data, CFG, events)
    assert (len(res.trades), _digest(res.trades), res.final_equity) == (
        n,
        trades_sha,
        final_equity,
    )
    assert _digest(enumerate_candidates(data, CFG, events)) == candidates_sha
    if years <= 3:  # ... and the stress really is a different model on the same world
        stressed = run_backtest(data, CFG.with_changes(stop_fill_wick_k=0.5), events)
        assert len(stressed.trades) > 30 and _digest(stressed.trades) != trades_sha


# ---------------------------------------------------------------------------- plumbing
def test_every_signal_candle_goes_through_the_gatekeeper(monkeypatch):
    data, events = synth(years=1.5)
    calls: list[tuple[str, int]] = []
    real = Gatekeeper.evaluate

    def spy(self, pair, candles, rows, i, *args, **kwargs):
        calls.append((pair, candles[i].ts))
        return real(self, pair, candles, rows, i, *args, **kwargs)

    monkeypatch.setattr(Gatekeeper, "evaluate", spy)
    res = run_backtest(data, CFG, events)
    expected = sorted((p, c.ts) for p, cs in data.items() for c in cs)
    assert sorted(calls) == expected and res.signals_evaluated == len(expected)
    # Every signal candle is either a logged denial or a trade, never silently dropped.
    denied = {(pair, when) for when, pair, _, _ in res.decision_log}
    taken = {(t.pair, t.signal_ts) for t in res.trades}
    assert denied | taken == set(expected) and not denied & taken
    assert sum(res.decisions.values()) == len(res.decision_log)


def test_results_are_ordered_and_consistent():
    data, events = synth()
    res = run_backtest(data, CFG, events, variant="unit")
    exits = [(t.exit_ts, t.pair) for t in res.trades]
    assert exits == sorted(exits) and all(t.variant == "unit" for t in res.trades)
    assert [e for e, _ in res.equity_curve] == [t.exit_ts for t in res.trades]
    assert res.final_equity == pytest.approx(
        CFG.starting_capital + math.fsum(t.pnl for t in res.trades), rel=1e-12
    )
    assert all(
        set(t.features) == {"rsi", "vol_ratio", "ema_gap_pct", "dist_regime_pct", "hour_utc"}
        for t in res.trades
    )
    assert run_backtest(data, CFG, events, variant="unit").trades == res.trades  # deterministic


def test_six_years_three_pairs_runs_fast():
    data, events = synth(years=6.0)
    t0 = time.perf_counter()
    res = run_backtest(data, CFG, events)
    elapsed = time.perf_counter() - t0
    assert len(res.trades) > 100
    assert elapsed < 6.0, f"{elapsed:.2f}s"  # ~1.1 s on the reference sandbox
    assert check_invariants(res, data, events, CFG) == []
