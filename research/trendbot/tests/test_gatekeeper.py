"""gatekeeper.py: the single entry-decision path (order of rules, timing, sizing, parity)."""

from __future__ import annotations

from dataclasses import replace
from functools import cache

import pytest

from research.trendbot.backtester import run_backtest, settle
from research.trendbot.circuit_breakers import CircuitBreakers
from research.trendbot.config import StrategyConfig
from research.trendbot.gatekeeper import MIN_NOTIONAL, EntryDecision, Gatekeeper
from research.trendbot.indicators import compute_features
from research.trendbot.models import EXIT_SL, StopPlan, Trade
from research.trendbot.synthetic import make_world
from research.trendbot.tests.bt_helpers import TF, Scenario, data_of, news, slot, ts, unit_loss


CFG = StrategyConfig()
SIG = slot(0)
FEE = CFG.fee_rate


def btc_signal(cfg: StrategyConfig = CFG) -> tuple[Scenario, list]:
    s = Scenario().spike(SIG)
    return s, compute_features(s.candles, cfg)


def losing_trade(pair: str, exit_ts: int, trade_id: int = 1, pnl: float = -100.0) -> Trade:
    return Trade(
        trade_id=trade_id,
        pair=pair,
        variant="base",
        signal_ts=exit_ts - 2 * TF,
        entry_ts=exit_ts - TF,
        entry_price=100.0,
        stop=98.0,
        target=104.0,
        qty=50.0,
        risk_amount=100.0,
        risk_pct=1.0,
        exit_ts=exit_ts,
        exit_price=98.0,
        exit_reason=EXIT_SL,
        pnl=pnl,
        r_multiple=pnl / 100.0,
    )


def evaluate(gk: Gatekeeper, s: Scenario, rows, i: int = SIG, **kw) -> EntryDecision:
    kw.setdefault("open_risk", {})
    kw.setdefault("equity", CFG.starting_capital)
    return gk.evaluate(s.pair, s.candles, rows, i, **kw)


# ---------------------------------------------------------------------------- happy path
def test_allowed_decision_carries_stop_risk_and_timing():
    s, rows = btc_signal()
    dec = evaluate(Gatekeeper(CFG), s, rows)
    assert dec.allowed and dec.rule == "R7_position_risk"
    assert dec.decision_ts == s.candles[SIG].ts + TF == s.candles[SIG + 1].ts
    assert dec.check.passed and dec.check.ts == s.candles[SIG].ts
    assert dec.stop_plan is not None and dec.stop_plan.stop == s.stop(SIG, CFG)
    assert (dec.risk_pct, dec.pair_cap, dec.correlation_allowed, dec.guard_mult) == (1, 1, 1, 1)
    assert "risk 1%" in dec.reason


@pytest.mark.parametrize(
    ("i", "change", "rule"),
    [
        (SIG, {"ema_fast": 1e9}, "R1_trend"),
        (SIG + 3, {}, "R2_momentum"),  # this phase of the zig-zag has RSI ~71.5
        (SIG + 1, {}, "R3_volume"),  # no volume spike
        (SIG, {"ema_regime": 1e9}, "R4_regime"),
    ],
)
def test_first_failed_gate_maps_to_its_rule_id(i, change, rule):
    s = Scenario().spike(SIG, SIG + 3)
    rows = compute_features(s.candles, CFG)
    rows[i] = replace(rows[i], **change)
    dec = evaluate(Gatekeeper(CFG), s, rows, i)
    assert (dec.allowed, dec.rule, dec.risk_pct) == (False, rule, 0.0)
    assert len(dec.check.gates) == 4  # every gate is still evaluated for the audit trail


def test_rules_are_applied_in_the_documented_order():
    s, rows = btc_signal()
    decision_ts = s.candles[SIG].ts + TF
    halted = CircuitBreakers(CFG, TF)  # backtest convention: exits count from the candle close
    halted.on_trade_closed(losing_trade("ETH/USDT", decision_ts - TF, pnl=-400.0))
    benched = CircuitBreakers(CFG, TF)
    for k in range(3):
        benched.on_trade_closed(losing_trade("BTC/USDT", decision_ts - (3 - k) * TF, k + 1))

    def rule(events=(), breakers=None, open_risk=None, entry_filter=None, rows_=rows):
        gk = Gatekeeper(CFG, events, breakers)
        dec = gk.evaluate(
            s.pair, s.candles, rows_, SIG, open_risk or {}, 10_000.0, (), entry_filter
        )
        return dec.rule

    event = [news(SIG, 1.0)]
    deny_all = lambda pair, row, check: (False, 0.1, "filter says no")  # noqa: E731
    assert rule(event, halted, {"BTC/USDT": 1.0}) == "R5_news_blackout"
    assert rule((), halted, {"BTC/USDT": 1.0}) == "R9_circuit_breaker"
    assert (
        "halted"
        in Gatekeeper(CFG, (), halted).evaluate(s.pair, s.candles, rows, SIG, {}, 10_000.0).reason
    )
    assert rule((), benched, {"ETH/USDT": 1.0}) == "R9_circuit_breaker"
    assert rule((), None, {"ETH/USDT": 1.0}) == "R6_correlation_cap"
    tight = replace(CFG, min_stop_distance_pct=9.0)
    gk = Gatekeeper(tight)
    assert gk.evaluate(s.pair, s.candles, rows, SIG, {}, 1e4, (), deny_all).rule == (
        "R8_structure_stop"
    )
    dec = Gatekeeper(CFG).evaluate(s.pair, s.candles, rows, SIG, {}, 1e4, (), deny_all)
    assert (dec.rule, dec.ml_prob, dec.reason) == ("L_ml_filter", 0.1, "filter says no")


def test_entry_filter_can_only_remove_and_reports_its_probability():
    s, rows = btc_signal()
    seen = []

    def keep(pair, row, check):
        seen.append((pair, row.ts, check.passed))
        return True, 0.62, "p=0.62 above break-even"

    dec = Gatekeeper(CFG).evaluate(s.pair, s.candles, rows, SIG, {}, 1e4, (), keep)
    assert dec.allowed and dec.ml_prob == 0.62 and dec.risk_pct == 1.0
    assert seen == [("BTC/USDT", s.candles[SIG].ts, True)]


# ---------------------------------------------------------------------------- R6/R7 sizing
def test_risk_is_min_of_cap_and_budget_times_guard():
    cfg = StrategyConfig(expectancy_guard=True, guard_window=1)
    eth = Scenario("ETH/USDT", start=50.0).spike(SIG)
    rows = compute_features(eth.candles, cfg)
    decision_ts = eth.candles[SIG].ts + TF
    gk = Gatekeeper(cfg)
    dec = gk.evaluate(eth.pair, eth.candles, rows, SIG, {"BTC/USDT": 0.5}, 1e4)
    assert (dec.allowed, dec.correlation_allowed, dec.risk_pct) == (True, 0.5, 0.5)
    # A losing ETH trade closed BEFORE the decision halves the risk ...
    before = [losing_trade("ETH/USDT", decision_ts - TF)]
    dec = gk.evaluate(eth.pair, eth.candles, rows, SIG, {"BTC/USDT": 0.5}, 1e4, before)
    assert (dec.guard_mult, dec.risk_pct) == (0.5, 0.25)
    # ... but one whose exit candle opens AT the decision time is not known yet.
    at = [losing_trade("ETH/USDT", decision_ts)]
    assert gk.guard_multiplier("ETH/USDT", decision_ts, at) == 1.0
    assert Gatekeeper(CFG).guard_multiplier("ETH/USDT", decision_ts, before) == 1.0  # guard off
    bnb = Scenario("BNB/USDT", start=10.0).spike(SIG)
    dec = Gatekeeper(CFG).evaluate(
        bnb.pair, bnb.candles, compute_features(bnb.candles, CFG), SIG, {}, 1e4
    )
    assert dec.allowed and dec.risk_pct == dec.pair_cap == 0.5


def test_plan_fill_applies_the_a1_cost_aware_arithmetic_by_hand():
    """CONTRACT v2 A1 with E = 100, S = 98, f = 0.1 %, s = 0.05 %, equity 10,000, risk 1 %:
    S_x = 98 * 0.9995 = 97.951; L_u = (100 - 97.951) + 0.1 + 0.097951 = 2.246951;
    qty = 100 / 2.246951 = 44.504753330...; T = (100.1 + 2 * 2.246951) / 0.999
    = 104.593902 / 0.999 = 104.698600600...; price RR (T - E) / (E - S) = 2.3493003 > 2."""
    s, rows = btc_signal()
    gk = Gatekeeper(CFG)
    stop_plan = StopPlan(98.0, 98.0 / 0.9975, 0.25, "pivot")
    dec = replace(evaluate(gk, s, rows), stop_plan=stop_plan)
    plan = gk.plan_fill("BTC/USDT", dec, 100.0 / 1.0005, 100.0, 10_000.0, 10_000.0)
    assert plan.ok and (plan.entry, plan.stop) == (100.0, 98.0)
    sz = plan.sizing
    assert sz.qty == pytest.approx(44.50475333017943, rel=1e-12)
    assert sz.risk_amount == pytest.approx(100.0, rel=1e-12)  # the ALL-IN loss at the stop
    assert sz.risk_pct == pytest.approx(1.0, rel=1e-12) and sz.capped_by is None
    assert sz.stop_distance == 2.0 and sz.notional == pytest.approx(4450.475333017943, rel=1e-12)
    assert plan.target == pytest.approx(104.6986006006006, rel=1e-12)
    assert (plan.target - 100.0) / 2.0 == pytest.approx(2.3493003003003, rel=1e-12)
    # Settled with the backtester's own fee model: TP nets +200 = +2R, a clean stop -100 = -1R.
    _, win = settle(sz.qty, 100.0, plan.target, FEE)
    _, loss = settle(sz.qty, 100.0, 97.951, FEE)
    assert win == pytest.approx(200.0, rel=1e-12) and loss == pytest.approx(-100.0, rel=1e-12)
    assert win / sz.risk_amount == pytest.approx(2.0, abs=1e-12)
    assert loss / sz.risk_amount == pytest.approx(-1.0, abs=1e-12)
    assert "2R net of fees and slippage, 2.349:1 in price" in plan.reason


def test_plan_fill_rechecks_the_fill_and_sizes_from_the_stop():
    s, rows = btc_signal()
    gk = Gatekeeper(CFG)
    dec = evaluate(gk, s, rows)
    stop = dec.stop_plan.stop
    fill = s.candles[SIG + 1].open * 1.0005
    lu = unit_loss(fill, stop, CFG)
    plan = gk.plan_fill(s.pair, dec, s.candles[SIG + 1].open, fill, 10_000.0, 10_000.0)
    assert plan.ok and plan.entry == fill and plan.stop == stop
    assert plan.target == pytest.approx((fill * (1 + FEE) + 2 * lu) / (1 - FEE), rel=1e-15)
    assert plan.target == pytest.approx(s.target(SIG, CFG), rel=1e-15)
    assert plan.sizing.qty == pytest.approx(100.0 / lu, rel=1e-12)
    assert plan.sizing.risk_amount == pytest.approx(plan.sizing.qty * lu, rel=1e-12)
    assert plan.sizing.risk_pct == pytest.approx(1.0) and plan.sizing.capped_by is None
    gapped = gk.plan_fill(s.pair, dec, stop, stop * 1.0005, 1e4, 1e4)
    assert (gapped.ok, gapped.rule) == (False, "R8_structure_stop")
    assert "gapped through stop" in gapped.reason
    far = gk.plan_fill(s.pair, dec, stop * 1.2, stop * 1.2, 1e4, 1e4)
    assert (far.ok, far.rule) == (False, "R8_structure_stop") and "actual fill" in far.reason
    # Free cash binds: quantity shrinks, the all-in risk is recomputed and only goes down,
    # and the (per-unit) target is unchanged, so a TP is still exactly +2R.
    small = gk.plan_fill(s.pair, dec, s.candles[SIG + 1].open, fill, 10_000.0, 1_000.0)
    assert small.ok and small.sizing.capped_by == "free_cash"
    assert small.sizing.notional * (1 + FEE) <= 1_000.0
    assert small.sizing.risk_amount == pytest.approx(small.sizing.qty * lu, rel=1e-12)
    assert small.sizing.risk_pct < plan.sizing.risk_pct and small.target == plan.target
    _, win = settle(small.sizing.qty, fill, small.target, FEE)
    assert win / small.sizing.risk_amount == pytest.approx(2.0, abs=1e-12)
    broke = gk.plan_fill(s.pair, dec, fill, fill, 10_000.0, MIN_NOTIONAL / 2)
    assert (broke.ok, broke.rule) == (False, "X_capital")
    denied = replace(dec, allowed=False)
    with pytest.raises(ValueError, match="allowed"):
        gk.plan_fill(s.pair, denied, fill, fill, 1e4, 1e4)


def test_breakers_and_guard_share_one_exit_offset():
    """A2: one offset for R9 and the guard; by default exits count from the candle CLOSE."""
    gk = Gatekeeper(CFG)
    assert gk.exit_time_uncertainty_ms == gk.breakers.exit_time_uncertainty_ms == TF
    live = Gatekeeper(CFG, breakers=CircuitBreakers(CFG))  # live journal: real fill times
    assert live.exit_time_uncertainty_ms == 0
    assert Gatekeeper(CFG, exit_time_uncertainty_ms=0).breakers.exit_time_uncertainty_ms == 0
    with pytest.raises(ValueError, match="same time"):
        Gatekeeper(CFG, breakers=CircuitBreakers(CFG), exit_time_uncertainty_ms=TF)
    with pytest.raises(ValueError, match="exit_time_uncertainty_ms"):
        Gatekeeper(CFG, exit_time_uncertainty_ms=-1)
    # The guard knows a trade once exit_ts + offset <= decision_ts, exactly like R9.
    cfg = StrategyConfig(expectancy_guard=True, guard_window=1)
    d = ts(SIG + 1)
    backtest, live = Gatekeeper(cfg), Gatekeeper(cfg, exit_time_uncertainty_ms=0)
    assert backtest.guard_multiplier("ETH/USDT", d, [losing_trade("ETH/USDT", d - TF)]) == 0.5
    assert backtest.guard_multiplier("ETH/USDT", d, [losing_trade("ETH/USDT", d - TF + 1)]) == 1
    assert live.guard_multiplier("ETH/USDT", d, [losing_trade("ETH/USDT", d)]) == 0.5
    assert live.guard_multiplier("ETH/USDT", d, [losing_trade("ETH/USDT", d + 1)]) == 1.0


# ---------------------------------------------------------------------------- timing
@cache
def _world():
    data, events = make_world("planted", 4, years=1.5)
    return data, events


def test_decisions_never_depend_on_later_candles():
    """What a live bot sees (candles up to the one that just closed) gives the same verdict."""
    data, events = _world()
    candles = data["ETH/USDT"]
    rows = compute_features(candles, CFG)
    gk = Gatekeeper(CFG, events)
    compared = allowed = 0
    for i in range(210, len(candles) - 1, 23):
        full = gk.evaluate("ETH/USDT", candles, rows, i, {}, 1e4)
        live_candles = candles[: i + 1]
        live = gk.evaluate(
            "ETH/USDT", live_candles, compute_features(live_candles, CFG), i, {}, 1e4
        )
        assert live == full
        compared += 1
        allowed += full.allowed
    assert compared > 30 and allowed > 0


def test_signal_level_evaluation_ignores_portfolio_state():
    s, rows = btc_signal()
    halted = CircuitBreakers(CFG)
    halted.on_trade_closed(losing_trade("ETH/USDT", s.candles[SIG].ts, pnl=-900.0))
    gk = Gatekeeper(CFG, (), halted)
    assert evaluate(gk, s, rows, open_risk={"ETH/USDT": 1.0}).rule == "R9_circuit_breaker"
    sig = gk.evaluate_signal(s.pair, s.candles, rows, SIG)
    assert sig.allowed and sig.risk_pct == 1.0 and sig.stop_plan is not None
    assert Gatekeeper(CFG, [news(SIG, -2.0)]).evaluate_signal(
        s.pair, s.candles, rows, SIG
    ).rule == ("R5_news_blackout")


def test_bad_inputs_are_rejected():
    s, rows = btc_signal()
    gk = Gatekeeper(CFG)
    with pytest.raises(ValueError, match="out of range"):
        gk.evaluate(s.pair, s.candles, rows, len(s.candles), {}, 1e4)
    with pytest.raises(ValueError, match="rows"):
        gk.evaluate(s.pair, s.candles, rows[1:] + rows[:1], SIG, {}, 1e4)


def test_journal_restart_reproduces_the_backtest_decision():
    """Live parity: a gatekeeper rebuilt from the journal (as after a bot restart) denies the
    same signal, for the same reason, as the backtest did."""
    bnb = Scenario("BNB/USDT", start=10.0)
    for k in range(3):
        bnb.spike(slot(k)).stop_out(slot(k), CFG)
    bnb.spike(slot(3))
    res = run_backtest(data_of(bnb), CFG)
    [(when, pair, rule, reason)] = [d for d in res.decision_log if d[2] == "R9_circuit_breaker"]
    decision_ts = when + TF
    journal = [t for t in res.trades if t.exit_ts + TF <= decision_ts]  # certain by then (A2)
    # A backtest journal records exit_ts = exit candle open: replay it with the timeframe.
    gk = Gatekeeper(CFG, breakers=CircuitBreakers.from_journal(journal, CFG, TF))
    assert gk.exit_time_uncertainty_ms == TF
    rows = compute_features(bnb.candles[: slot(3) + 1], CFG)
    equity = CFG.starting_capital + sum(t.pnl for t in journal)
    dec = gk.evaluate(pair, bnb.candles[: slot(3) + 1], rows, slot(3), {}, equity, journal)
    assert (dec.rule, dec.reason) == (rule, reason)
    assert when == ts(slot(3))
    # Replaying it with the LIVE convention would end the bench one candle too early.
    live = Gatekeeper(CFG, breakers=CircuitBreakers.from_journal(journal, CFG))
    live_dec = live.evaluate(pair, bnb.candles[: slot(3) + 1], rows, slot(3), {}, equity, journal)
    assert live_dec.rule == rule and live_dec.reason != reason
