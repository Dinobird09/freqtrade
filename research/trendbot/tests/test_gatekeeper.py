"""gatekeeper.py: the single entry-decision path (order of rules, timing, sizing, parity),
and the LiveSession adapter with its whole-run parity against run_backtest (CONTRACT v4 D5)."""

from __future__ import annotations

from dataclasses import replace
from functools import cache

import pytest

from research.trendbot.backtester import run_backtest, settle
from research.trendbot.circuit_breakers import CircuitBreakers
from research.trendbot.config import StrategyConfig
from research.trendbot.gatekeeper import (
    MIN_NOTIONAL,
    DecisionRecord,
    EntryDecision,
    Gatekeeper,
    LiveSession,
    reserved_risk_of,
)
from research.trendbot.indicators import compute_features
from research.trendbot.journal import read_journal, write_journal
from research.trendbot.models import DAY_MS, EXIT_SL, EXIT_TP, StopPlan, Trade
from research.trendbot.synthetic import make_world
from research.trendbot.tests.bt_helpers import (
    TF,
    Prefix,
    Scenario,
    data_of,
    drive_live_session,
    news,
    slot,
    ts,
    unit_loss,
)


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


def test_entry_filter_can_only_veto_and_reports_its_probability():
    """C1: the layer is consulted only after every mandatory rule passed; it can veto that
    entry (see the order test above) but never approve one a rule denied, and a keep leaves
    the R7 risk untouched."""
    s, rows = btc_signal()
    seen = []

    def keep(pair, row, check):
        seen.append((pair, row.ts, check.passed))
        return True, 0.62, "p=0.62 above break-even"

    dec = Gatekeeper(CFG).evaluate(s.pair, s.candles, rows, SIG, {}, 1e4, (), keep)
    assert dec.allowed and dec.ml_prob == 0.62 and dec.risk_pct == 1.0
    assert seen == [("BTC/USDT", s.candles[SIG].ts, True)]
    # A signal a mandatory rule denies never reaches the layer, so it cannot be approved.
    denied = Gatekeeper(CFG).evaluate(s.pair, s.candles, rows, SIG + 1, {}, 1e4, (), keep)
    assert (denied.allowed, denied.rule) == (False, "R3_volume") and len(seen) == 1


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


# ---------------------------------------------------------------------------- LiveSession
def _session_with_signal(cfg: StrategyConfig = CFG, **kw) -> tuple[LiveSession, Scenario]:
    s = Scenario().spike(SIG, slot(1))
    return LiveSession(cfg, **kw), s


def test_prefix_view_hides_every_later_candle():
    s = Scenario()
    view = Prefix(s.candles, SIG + 1)
    assert len(view) == SIG + 1 and view[-1] == s.candles[SIG] and view[0] == s.candles[0]
    assert view[SIG - 2 :] == s.candles[SIG - 2 : SIG + 1]
    assert view[SIG - 1 : SIG + 50] == s.candles[SIG - 1 : SIG + 1]  # clipped at the prefix
    with pytest.raises(IndexError):
        view[SIG + 1]
    assert list(view) == s.candles[: SIG + 1]


def test_live_session_takes_the_decision_on_the_candle_that_just_closed():
    session, s = _session_with_signal()
    dec = session.on_candle_close(s.pair, s.candles[: SIG + 1])  # features computed inside
    assert dec.allowed and dec.decision_ts == s.candles[SIG + 1].ts
    assert session.decision_log == [
        DecisionRecord(s.pair, s.candles[SIG].ts, True, dec.rule, dec.reason)
    ]
    fill = s.candles[SIG + 1].open * 1.0005
    trade = session.on_fill(s.pair, dec, s.candles[SIG + 1].open, fill)
    # The same trade the backtester records for this scenario, field for field.
    [expected] = [t for t in run_backtest(data_of(s), CFG).trades if t.signal_ts == ts(SIG)]
    assert replace(trade, notes=expected.notes) == replace(
        expected,
        exit_ts=None,
        exit_price=None,
        exit_reason=None,
        fees=0.0,
        pnl=None,
        r_multiple=None,
    )
    assert trade.notes == expected.notes and trade.notes.startswith("reserved 1% ")
    assert session.open_risk == {s.pair: 1.0} and session.reservations() == {1: 1.0}
    cost = trade.qty * trade.entry_price * (1 + FEE)
    assert session.free_cash() == pytest.approx(CFG.starting_capital - cost, rel=1e-12)
    # The pair's next signal, while the position is open, is denied by R6 (no pyramiding).
    again = session.on_candle_close(s.pair, s.candles[: slot(1) + 1])
    assert (again.allowed, again.rule) == (False, "R6_correlation_cap")


def test_live_session_accepts_actual_fills_and_exits():
    """Replay injection: the journal's own id, fill time, (smaller) filled qty and fees."""
    session, s = _session_with_signal(starting_equity=20_000.0)
    dec = session.on_candle_close(s.pair, s.candles[: SIG + 1])
    market = s.candles[SIG + 1].open
    fill = market * 1.0003  # the exchange's real fill, not the modelled slippage
    plan = session.gatekeeper.plan_fill(s.pair, dec, market, fill, 20_000.0, 20_000.0)
    qty = plan.sizing.qty * 0.5  # e.g. a partial fill
    trade = session.on_fill(
        s.pair, dec, market, fill, qty=qty, trade_id=77, fill_ts=dec.decision_ts + 60_000
    )
    assert (trade.trade_id, trade.entry_ts, trade.qty) == (
        77,
        dec.decision_ts + 60_000,
        qty,
    )
    lu = unit_loss(fill, trade.stop, CFG)
    assert trade.risk_amount == pytest.approx(qty * lu, rel=1e-12)
    assert trade.risk_pct == pytest.approx(0.5, rel=1e-9)  # half the planned 1 %
    assert "size capped by actual_fill_qty" in trade.notes
    assert session.open_risk == {s.pair: 1.0}  # the R6 reservation stays what R6 granted
    closed = session.on_exit(77, dec.decision_ts + TF + 5, trade.stop * 0.999, EXIT_SL, fees=1.25)
    assert closed is trade and closed.fees == 1.25
    assert closed.pnl == pytest.approx(qty * (trade.stop * 0.999 - fill) - 1.25, rel=1e-12)
    assert closed.r_multiple == closed.pnl / closed.risk_amount
    assert session.equity == 20_000.0 + closed.pnl and session.open_positions == {}
    assert session.equity_curve == [(dec.decision_ts + TF + 5, session.equity)]
    assert session.journal_trades() == [closed]


def test_refused_and_skipped_fills_become_denials():
    session, s = _session_with_signal()
    dec = session.on_candle_close(s.pair, s.candles[: SIG + 1])
    stop = dec.stop_plan.stop
    assert session.on_fill(s.pair, dec, stop, stop * 1.0005) is None  # gapped through the stop
    assert session.last_fill_plan.rule == "R8_structure_stop"
    [row] = session.decision_log
    assert (row.allowed, row.rule) == (False, "R8_structure_stop")
    assert "gapped through stop" in row.reason and session.open_positions == {}
    dec = session.on_candle_close(s.pair, s.candles[: slot(1) + 1])
    session.on_fill_skipped(s.pair, dec, "exchange rejected the order")
    assert session.decision_log[-1] == DecisionRecord(
        s.pair, ts(slot(1)), False, "X_capital", "exchange rejected the order"
    )


def test_live_session_protocol_is_enforced():
    session, s = _session_with_signal()
    dec = session.on_candle_close(s.pair, s.candles[: SIG + 1])
    with pytest.raises(ValueError, match="resolve the pending decision for BTC/USDT"):
        session.on_candle_close("ETH/USDT", Scenario("ETH/USDT").candles[: SIG + 1])
    with pytest.raises(ValueError, match="just returned"):
        session.on_fill(s.pair, replace(dec), 100.0, 100.0)  # an equal copy is not the decision
    market = s.candles[SIG + 1].open
    with pytest.raises(ValueError, match="outside the fill candle"):
        session.on_fill(s.pair, dec, market, market, fill_ts=dec.decision_ts + TF)
    with pytest.raises(ValueError, match="never larger"):
        session.on_fill(s.pair, dec, market, market, qty=1e9)
    trade = session.on_fill(s.pair, dec, market, market * 1.0005)  # still pending: now fine
    with pytest.raises(ValueError, match="not after"):
        session.on_candle_close(s.pair, s.candles[: SIG + 1])
    with pytest.raises(ValueError, match="not an open position"):
        session.on_exit(999, ts(SIG + 3), 100.0, EXIT_SL)
    with pytest.raises(ValueError, match="exit reason"):
        session.on_exit(trade.trade_id, ts(SIG + 3), 100.0, "STOP")
    with pytest.raises(ValueError, match="before its entry"):
        session.on_exit(trade.trade_id, trade.entry_ts - 1, 100.0, EXIT_SL)
    with pytest.raises(ValueError, match="starting_equity"):
        LiveSession(CFG, starting_equity=0.0)
    with pytest.raises(ValueError, match="exit_time_uncertainty_ms"):
        LiveSession(CFG, exit_time_uncertainty_ms=-1)


def _journal_row(trade_id, pair, exit_ts=None, pnl=None, reason=EXIT_SL, notes="", risk=1.0):
    """A journal row; ``exit_ts=None`` makes it an OPEN position filled at ts(8)."""
    t = losing_trade(pair, exit_ts if exit_ts is not None else ts(9), trade_id, pnl or -100.0)
    t.notes, t.risk_pct = notes, risk
    if exit_ts is None:
        t.exit_ts = t.exit_price = t.exit_reason = t.pnl = t.r_multiple = None
    else:
        t.exit_reason = reason
    return t


def test_restart_rebuilds_positions_reservations_equity_and_breakers():
    """A live journal (real fill times, offset 0): three BNB stop-losses, a win, two open."""
    rows = [
        _journal_row(1, "BNB/USDT", ts(10), -50.0),
        _journal_row(2, "BNB/USDT", ts(12), -50.0),
        _journal_row(3, "BTC/USDT", ts(13), 300.0, reason=EXIT_TP),
        _journal_row(4, "BNB/USDT", ts(14) + 7, -50.0),
        _journal_row(5, "ETH/USDT", notes="reserved 0.25% (cap 1%, R6 1%, guard x0.5); ..."),
        _journal_row(6, "BTC/USDT", notes="imported row without a reservation"),
    ]
    session = LiveSession(CFG, (), rows, 20_000.0)
    assert session.equity == 20_000.0 - 50.0 - 50.0 + 300.0 - 50.0
    assert [t.trade_id for t in session.closed] == [1, 2, 3, 4]
    assert session.equity_curve[-1] == (ts(14) + 7, session.equity)
    assert session.open_risk == {"ETH/USDT": 0.25, "BTC/USDT": 1.0}  # notes, else the cap
    assert reserved_risk_of(rows[5], CFG) == 1.0
    bench = session.breakers.active_bench("BNB/USDT", ts(14) + 7)
    assert bench is not None and bench.until_ts == ts(14) + 7 + DAY_MS  # 24h from the real fill
    assert session.free_cash() == pytest.approx(session.equity - 2 * 50.0 * 100.0 * (1 + FEE))
    exact = LiveSession(CFG, (), rows, 20_000.0, reserved_risk={6: 0.4})
    assert exact.open_risk["BTC/USDT"] == 0.4 and exact._next_id == 7
    # The session works on copies: closing a position never mutates the caller's journal rows.
    session.on_exit(5, ts(20), 99.0, EXIT_SL)
    assert rows[4].exit_ts is None and session.journal_trades()[-1].trade_id == 6
    bad = [
        [rows[0], replace(rows[1], trade_id=1)],
        [replace(rows[0], pnl=None)],
        [rows[4], replace(rows[4], trade_id=9)],
    ]
    for journal, match in zip(bad, ("duplicate", "all set", "more than one open"), strict=True):
        with pytest.raises(ValueError, match=match):
            LiveSession(CFG, (), journal)


def test_exits_are_never_gated_by_the_session():
    """Halted and benched, the session still processes every exit it is told about."""
    rows = [_journal_row(k, "ETH/USDT", ts(10 + k), -150.0) for k in range(1, 4)]
    rows.append(_journal_row(4, "BTC/USDT"))
    session = LiveSession(CFG, (), rows)  # -450 in a day on 10k: 7-day halt, ETH benched
    assert not session.breakers.can_enter("BTC/USDT", ts(14), session.equity).allowed
    closed = session.on_exit(4, ts(14), 101.0, EXIT_TP)
    assert closed.exit_reason == EXIT_TP and session.open_positions == {}


# ---------------------------------------------------------------------------- D5 parity
# 6-year worlds picked on the BACKTEST alone because they exercise the stateful paths a
# restart must rebuild: R9 benches / halts, notional-capped sizes (R6 reservation above the
# realized risk), and (guard case) the expectancy guard plus gap-through entry skips.
PARITY_CASES = {
    "base": ("null", 3, False, StrategyConfig()),
    "guard+gaps": ("null", 4, True, StrategyConfig(expectancy_guard=True, guard_window=5)),
}


@cache
def _parity(name: str):
    world, seed, gaps, cfg = PARITY_CASES[name]
    data, events = make_world(world, seed, years=6.0, gaps=gaps)
    return data, events, cfg, run_backtest(data, cfg, events)


def _restart_points(data, res) -> list[tuple[int, bool]]:
    stamps = sorted({c.ts for cs in data.values() for c in cs})
    points = [(stamps[len(stamps) // 2], True)]  # mid-run, with a position open
    points += [(when, False) for when, _msg in res.breaker_log]  # right after every trip
    points += [(t.signal_ts, True) for t in res.trades if "capped" in t.notes]  # capped open
    return points


def _key(t: Trade) -> tuple:
    return (t.trade_id, t.pair, t.signal_ts, t.entry_price, t.exit_ts, t.exit_price, t.r_multiple)


@pytest.mark.parametrize("name", sorted(PARITY_CASES))
def test_live_session_reproduces_six_years_of_backtest_across_restarts(name, tmp_path):
    """D5: LiveSession driven candle by candle (only ever seeing the closed candles), with the
    session REBUILT from the written journal at several points, yields exactly the
    backtest's trades (ids, prices, R, every field), denials, breaker trips and equity."""
    data, events, cfg, res = _parity(name)
    points = _restart_points(data, res)
    run = drive_live_session(
        data,
        cfg,
        events,
        exit_time_uncertainty_ms=TF,  # backtest convention: exit_ts = exit candle open
        restarts=points,
        journal_path=tmp_path / "journal.csv",
    )
    assert [_key(t) for t in run.trades] == [_key(t) for t in res.trades]
    assert run.trades == res.trades
    assert run.denials() == res.decision_log
    assert run.breaker_log == res.breaker_log
    assert run.equity_curve == res.equity_curve and run.final_equity == res.final_equity
    # What the run covered (so the equality is not vacuous).
    candles = data["BTC/USDT"]
    assert candles[-1].ts - candles[0].ts >= 6 * 365 * DAY_MS - TF and len(res.trades) > 100
    assert len(run.restarts) >= 3 and any(still for _, still in run.restarts)
    r9 = [when for when, _p, rule, _r in res.decision_log if rule == "R9_circuit_breaker"]
    assert r9 and any(r < d <= r + 7 * DAY_MS for r, _ in run.restarts for d in r9)
    capped = [t for _, still in run.restarts for t in still if "capped" in t.notes]
    if name == "base":
        assert capped and all(reserved_risk_of(t, cfg) > t.risk_pct for t in capped)
    else:
        assert any(t.notes.find("guard x0.5") > 0 for t in res.trades)
        assert res.decisions["R8_structure_stop"] > 0
    # The features handed over as a Prefix are exactly those of the prefix (causal).
    rows = compute_features(candles, cfg)
    btc = [t for t in res.trades if t.pair == "BTC/USDT"]
    index = {c.ts: k for k, c in enumerate(candles)}
    for t in btc[:: max(1, len(btc) // 6)]:
        i = index[t.signal_ts]
        assert compute_features(candles[: i + 1], cfg)[i] == rows[i]


def test_live_time_convention_takes_the_same_decisions(tmp_path):
    """Offset 0 with exits journaled at a real (mid-candle) fill time: every exit becomes
    known at the same candle close as in the backtest, so the decisions are identical and
    only the recorded exit times differ; restart included."""
    data, events, cfg, res = _parity("base")
    stamps = sorted({c.ts for cs in data.values() for c in cs})
    run = drive_live_session(
        data,
        cfg,
        events,
        exit_time_uncertainty_ms=0,
        exit_fill_ms=TF // 2,
        restarts=[(stamps[len(stamps) // 3], True)],
        journal_path=tmp_path / "live.csv",
    )
    assert run.trades == [replace(t, exit_ts=t.exit_ts + TF // 2) for t in res.trades]
    assert [d[:3] for d in run.denials()] == [d[:3] for d in res.decision_log]
    assert run.final_equity == res.final_equity and len(run.restarts) == 1
    write_journal(run.session.journal_trades(), tmp_path / "final.csv")
    assert read_journal(tmp_path / "final.csv") == run.trades
    assert all(t.exit_reason in (EXIT_SL, EXIT_TP, "END") for t in run.trades)
