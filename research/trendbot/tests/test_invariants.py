"""invariants.py: clean on real runs, and it catches every kind of rule violation.

Two kinds of evidence: (1) a clean backtest of every synthetic world has no violations,
(2) corrupting a single trade, or switching a rule OFF inside the engine, is reported with
the violated rule named, so the audit is not trivially passing.
"""

from __future__ import annotations

import copy
import json
import random
import re
from dataclasses import replace
from functools import cache
from pathlib import Path

import pytest

from research.trendbot import backtester as bt
from research.trendbot import gatekeeper as gk_mod
from research.trendbot.backtester import run_backtest
from research.trendbot.circuit_breakers import CircuitBreakers
from research.trendbot.config import ConfigError, StrategyConfig
from research.trendbot.correlation import CorrelationGuard
from research.trendbot.data import pair_filename, save_candles_csv
from research.trendbot.invariants import (
    benches,
    blocked_open_trades,
    check_invariants,
    cross_trade_violations,
    live_journal_violations,
    load_cli_config,
    main,
    result_from_journal,
)
from research.trendbot.journal import read_journal, write_journal
from research.trendbot.models import (
    DAY_MS,
    EXIT_SL,
    EXIT_TP,
    HOUR_MS,
    Decision,
    NewsEvent,
    SizingResult,
    StopPlan,
    Trade,
)
from research.trendbot.news import NewsCalendar
from research.trendbot.synthetic import WORLDS, make_world
from research.trendbot.tests.bt_helpers import (
    TF,
    Scenario,
    bench_scenario,
    data_of,
    drive_live_session,
    news,
    slot,
    ts,
    unit_loss,
)


CFG = StrategyConfig()


@cache
def world(name: str, seed: int = 7, years: float = 3.0):
    return make_world(name, seed, years=years)


@cache
def clean_run():
    data, events = world("planted")
    return data, events, run_backtest(data, CFG, events)


# ---------------------------------------------------------------------------- clean runs
@pytest.mark.parametrize("name", WORLDS)
def test_full_synthetic_runs_are_clean(name):
    data, events = world(name)
    res = run_backtest(data, CFG, events)
    assert len(res.trades) > 30
    assert check_invariants(res, data, events, CFG) == []
    assert blocked_open_trades(res) == []  # one position at a time under the default config


@pytest.mark.parametrize(
    "cfg",
    [
        StrategyConfig(expectancy_guard=True, guard_window=5),
        StrategyConfig(reward_risk=3.0, vol_mult=2.0, rsi_min=55.0),
        StrategyConfig(regime_filter=False),
    ],
    ids=["guard", "tightened", "regime-off"],
)
def test_variant_and_windowed_runs_are_clean(cfg):
    data, events = world("hour_edge")
    candles = data["BTC/USDT"]
    start, end = candles[len(candles) // 4].ts, candles[3 * len(candles) // 4].ts
    for window in ((None, None), (start, end)):
        res = run_backtest(data, cfg, events, start_ts=window[0], end_ts=window[1])
        assert res.trades
        assert check_invariants(res, data, events, cfg) == []


# ---------------------------------------------------------------------------- corruptions
def _corrupt(fn):
    data, events, res = clean_run()
    bad = replace(res, trades=copy.deepcopy(res.trades))
    extra_events = fn(bad, data) or []
    return check_invariants(bad, data, list(events) + extra_events, CFG)


def _first(bad, pair=None):
    return next(t for t in bad.trades if pair is None or t.pair == pair)


def _resettle(t, exit_price):
    """Move a trade's exit price and recompute fees, pnl and R consistently."""
    t.exit_price = exit_price
    t.fees = CFG.fee_rate * t.qty * (t.entry_price + exit_price)
    t.pnl = t.qty * (exit_price - t.entry_price) - t.fees
    t.r_multiple = t.pnl / t.risk_amount


def _stop_above_structure(bad, data):
    t = _first(bad)
    t.stop *= 1.004
    t.risk_amount = t.qty * unit_loss(t.entry_price, t.stop, CFG)


def _risk_above_cap(bad, data):
    _first(bad, "BNB/USDT").risk_pct = 0.75


def _rr_below_2(bad, data):
    t = _first(bad)
    t.target = t.entry_price + 1.5 * (t.entry_price - t.stop)


def _net_rr_below_2(bad, data):
    t = _first(bad)
    t.target = t.entry_price + 2 * (t.entry_price - t.stop)  # the v1 price-only target


def _risk_amount_price_only(bad, data):
    t = _first(bad)
    t.risk_amount = t.qty * (t.entry_price - t.stop)  # v1: ignores fees and stop slippage


def _loss_beyond_1r_without_gap(bad, data):
    t = next(t for t in bad.trades if t.exit_reason == EXIT_SL)
    _resettle(t, t.exit_price * 0.995)


def _take_profit_above_rr(bad, data):
    t = next(t for t in bad.trades if t.exit_reason == EXIT_TP)
    t.target *= 1.001
    _resettle(t, t.target)


def _fill_not_next_open(bad, data):
    _first(bad).entry_price *= 1.002


def _exit_delayed(bad, data):
    t = next(t for t in bad.trades if t.exit_reason == EXIT_SL)
    t.exit_ts += 2 * TF


def _not_closed(bad, data):
    t = _first(bad)
    t.exit_ts = t.pnl = None


def _fees_halved(bad, data):
    _first(bad).fees *= 0.5


def _pyramid(bad, data):
    t = copy.deepcopy(_first(bad, "BTC/USDT"))
    t.trade_id = 10_000
    bad.trades.append(t)


def _bnb_stacks(bad, data):
    t = copy.deepcopy(_first(bad, "BTC/USDT"))
    t.trade_id, t.pair = 10_001, "BNB/USDT"
    bad.trades.append(t)


def _news_inside_window(bad, data):
    t = bad.trades[5]
    return [NewsEvent(t.signal_ts + TF + 3_600_000, "ALL", "high", "macro", "planted in test")]


def _signal_not_a_signal(bad, data):
    t = _first(bad)
    t.signal_ts -= 3 * TF


def _future_data(bad, data):
    t = bad.trades[len(bad.trades) // 2]
    bad.window = (None, t.exit_ts)


def _big_loss_before_entry(bad, data):
    prev, victim = bad.trades[9], bad.trades[10]
    prev.pnl = -0.05 * CFG.starting_capital  # a 5 % loss the halt must react to ...
    victim.signal_ts, victim.entry_ts = prev.exit_ts, prev.exit_ts + TF  # ... right after it


def _bench_ignored(bad, data):
    pair = bad.trades[0].pair
    mine = [t for t in bad.trades if t.pair == pair]
    for t in mine[:3]:
        t.exit_reason = EXIT_SL
    mine[3].signal_ts, mine[3].entry_ts = mine[2].exit_ts, mine[2].exit_ts + TF


def _bench_from_the_candle_open(bad, data):
    """The next entry decided exactly 24h after the 3rd SL's exit candle OPEN (v1 timing)."""
    pair = bad.trades[0].pair
    mine = [t for t in bad.trades if t.pair == pair]
    for t in mine[:3]:
        t.exit_reason = EXIT_SL
    decided = mine[2].exit_ts + 24 * HOUR_MS
    mine[3].signal_ts, mine[3].entry_ts = decided - TF, decided


@pytest.mark.parametrize(
    ("corruption", "expected"),
    [
        (_stop_above_structure, "(R8)"),
        (_risk_above_cap, "cap (R7)"),
        (_rr_below_2, "price reward:risk"),
        (_net_rr_below_2, "net reward:risk"),
        (_risk_amount_price_only, "all-in loss per unit"),
        (_loss_beyond_1r_without_gap, "below -1R without a gap"),
        (_take_profit_above_rr, "take-profit made"),
        (_fill_not_next_open, "next open plus slippage"),
        (_exit_delayed, "exit delayed"),
        (_not_closed, "is not closed"),
        (_fees_halved, "fee on both legs"),
        (_pyramid, "no pyramiding"),
        (_bnb_stacks, "BNB never stacks"),
        (_news_inside_window, "(R5)"),
        (_signal_not_a_signal, "fills at"),
        (_future_data, "at/after end_ts"),
        (_big_loss_before_entry, "halt limit"),
        (_bench_ignored, "benched"),
        (_bench_from_the_candle_open, "benched too briefly"),
    ],
)
def test_corrupted_trade_is_caught(corruption, expected):
    violations = _corrupt(corruption)
    assert any(expected in v for v in violations), violations[:5]


def test_benches_are_recomputed_from_streaks():
    res = clean_run()[2]
    trades = copy.deepcopy(res.trades)
    btc = [t for t in trades if t.pair == "BTC/USDT"][:4]
    for t in btc[:3]:
        t.exit_reason = EXIT_SL
    btc[3].exit_reason = "TP"
    found = benches(trades, CFG)["BTC/USDT"]
    # A2: from the exit candle CLOSE (when the stop is certain), for 24h.
    assert found[0] == (btc[2].exit_ts + TF, btc[2].exit_ts + TF + 24 * HOUR_MS)


# ---------------------------------------------------------------------------- engine sabotage
def _bench_scenario():
    bnb = Scenario("BNB/USDT", start=10.0)
    for k in range(3):
        bnb.spike(slot(k)).stop_out(slot(k), CFG)
    bnb.spike(slot(3))
    return data_of(bnb)


def _halt_scenario():
    btc, eth = Scenario(), Scenario("ETH/USDT", start=50.0)
    btc.spike(slot(0)).stop_out(slot(0), CFG)
    eth.spike(slot(1)).stop_out(slot(1), CFG)
    btc.spike(slot(2)).stop_out(slot(2), CFG)
    eth.spike(slot(3))
    return data_of(btc, eth)


def test_disabled_news_rule_is_caught(monkeypatch):
    data = data_of(Scenario().spike(slot(0)))
    events = [news(slot(0), 0.5)]
    assert check_invariants(run_backtest(data, CFG, events), data, events, CFG) == []
    monkeypatch.setattr(NewsCalendar, "check", lambda self, pair, ts: Decision(True, "R5", "off"))
    res = run_backtest(data, CFG, events)
    assert any("(R5)" in v for v in check_invariants(res, data, events, CFG))


@pytest.mark.parametrize(
    ("scenario", "expected"), [(_bench_scenario, "benched"), (_halt_scenario, "halt limit")]
)
def test_disabled_circuit_breaker_is_caught(monkeypatch, scenario, expected):
    data = scenario()
    assert check_invariants(run_backtest(data, CFG), data, (), CFG) == []
    monkeypatch.setattr(
        CircuitBreakers, "can_enter", lambda self, pair, ts, equity: Decision(True, "R9", "off")
    )
    res = run_backtest(data, CFG)
    assert any(expected in v for v in check_invariants(res, data, (), CFG))


def test_disabled_correlation_cap_is_caught(monkeypatch):
    data = data_of(Scenario().spike(slot(0)), Scenario("BNB/USDT", start=10.0).spike(slot(0)))
    assert check_invariants(run_backtest(data, CFG), data, (), CFG) == []
    monkeypatch.setattr(
        CorrelationGuard,
        "check",
        lambda self, pair, req, open_risk: (Decision(True, "R6", ""), req),
    )
    violations = check_invariants(run_backtest(data, CFG), data, (), CFG)
    assert any("BNB never stacks" in v for v in violations)


def test_price_only_target_is_caught(monkeypatch):
    """If the engine used the v1 target E + 2 (E - S), fees would eat into the 2:1."""
    data = data_of(Scenario().spike(slot(0)).take_profit(slot(0), CFG))
    assert check_invariants(run_backtest(data, CFG), data, (), CFG) == []
    monkeypatch.setattr(
        gk_mod, "cost_aware_target", lambda e, s, cfg: e + cfg.reward_risk * (e - s)
    )
    violations = check_invariants(run_backtest(data, CFG), data, (), CFG)
    assert any("net reward:risk" in v for v in violations)
    assert any("take-profit made" in v for v in violations)


def test_price_only_sizing_is_caught(monkeypatch):
    """If the engine sized from the price distance only, a clean stop would lose > 1R."""
    data = data_of(Scenario().spike(slot(0)).stop_out(slot(0), CFG))
    assert check_invariants(run_backtest(data, CFG), data, (), CFG) == []

    def v1_size(pair, equity, entry, stop, risk_pct, cfg):
        qty = equity * risk_pct / 100 / (entry - stop)
        return SizingResult(qty, qty * (entry - stop), risk_pct, entry - stop, qty * entry, None)

    monkeypatch.setattr(gk_mod, "size_for_pair", v1_size)
    violations = check_invariants(run_backtest(data, CFG), data, (), CFG)
    assert any("all-in loss per unit" in v for v in violations)
    assert any("clean stop-loss made" in v for v in violations)
    assert any("below -1R without a gap" in v for v in violations)


def test_bench_counted_from_the_exit_candle_open_is_caught(monkeypatch):
    """A2: an engine that counted the bench from the recorded exit_ts (the exit candle OPEN)
    re-enters 24h after that open, only 20h after the stop is certain; the audit flags it."""
    data = data_of(bench_scenario(CFG))
    assert check_invariants(run_backtest(data, CFG), data, (), CFG) == []

    class OpenTimed(gk_mod.Gatekeeper):
        def __init__(self, cfg, events=(), breakers=None, exit_time_uncertainty_ms=None):
            super().__init__(cfg, events, breakers, exit_time_uncertainty_ms=0)

    monkeypatch.setattr(bt, "Gatekeeper", OpenTimed)
    violations = check_invariants(run_backtest(data, CFG), data, (), CFG)
    assert any("benched too briefly" in v and "20h after the exit" in v for v in violations)
    assert any("while BNB/USDT was benched" in v for v in violations)


def test_stop_not_behind_structure_is_caught(monkeypatch):
    data = data_of(Scenario().spike(slot(0)))
    real = gk_mod.find_stop

    def arbitrary_stop(candles, i, pair, cfg):
        plan, why = real(candles, i, pair, cfg)
        return replace(plan, stop=candles[i].close * 0.98), why  # not tied to any low

    monkeypatch.setattr(gk_mod, "find_stop", arbitrary_stop)
    violations = check_invariants(run_backtest(data, CFG), data, (), CFG)
    assert any("(R8)" in v for v in violations)
    assert isinstance(real(data["BTC/USDT"], slot(0), "BTC/USDT", CFG)[0], StopPlan)


def test_paused_exits_are_caught(monkeypatch):
    """If the engine paused exits while entries are halted, the audit reports the delay."""
    cfg = StrategyConfig(expectancy_guard=True, guard_window=1, weekly_loss_limit_pct=1.5)
    btc, eth = Scenario(), Scenario("ETH/USDT", start=50.0)
    btc.spike(slot(0)).stop_out(slot(0), cfg)
    btc.spike(slot(1)).stop_out(slot(1), cfg, j=slot(1) + 3)
    eth.spike(slot(1)).take_profit(slot(1), cfg, j=slot(2) + 2)
    data = data_of(btc, eth)
    res = run_backtest(data, cfg)
    assert check_invariants(res, data, (), cfg) == [] and blocked_open_trades(res)
    real = bt._Simulation.process_exit

    def paused(self, pair, s, j):
        if not self.gk.breakers.can_enter(pair, s.candles[j].ts, self.equity).allowed:
            return None  # sabotage: no exits while halted
        return real(self, pair, s, j)

    monkeypatch.setattr(bt._Simulation, "process_exit", paused)
    sabotaged = run_backtest(data, cfg)
    assert any("exit delayed" in v for v in check_invariants(sabotaged, data, (), cfg))


# ---------------------------------------------------------------------------- R5 (C2)
def test_unscheduled_news_blocks_only_after_it_happened_end_to_end():
    """C2 end to end: a regulatory headline 1h AFTER the decision was unknowable then, so the
    engine enters and the audit agrees; the same headline 1h BEFORE, or a scheduled macro
    print 1h after, blocks the entry and an entry there would be flagged."""
    data = data_of(Scenario().spike(slot(0)))
    after = [news(slot(0), 1.0, kind="regulatory")]
    res = run_backtest(data, CFG, after)
    assert len(res.trades) == 1 and check_invariants(res, data, after, CFG) == []
    clean = run_backtest(data, CFG)
    for blocking in (news(slot(0), -1.0, kind="regulatory"), news(slot(0), 1.0, kind="macro")):
        assert not run_backtest(data, CFG, [blocking]).trades
        violations = check_invariants(clean, data, [blocking], CFG)
        assert any("(R5)" in v for v in violations), violations


def _probe(pair: str, decided: int, trade_id: int = 1) -> Trade:
    """A closed journal row whose entry was decided at ``decided`` (cross-trade checks only)."""
    return Trade(
        trade_id=trade_id,
        pair=pair,
        variant="base",
        signal_ts=decided - TF,
        entry_ts=decided,
        entry_price=100.0,
        stop=98.0,
        target=105.0,
        qty=1.0,
        risk_amount=2.5,
        risk_pct=0.5 if pair.startswith("BNB") else 1.0,
        exit_ts=decided + 6 * HOUR_MS,
        exit_price=105.0,
        exit_reason=EXIT_TP,
        pnl=4.0,
        r_multiple=1.6,
    )


def test_r5_recomputation_agrees_with_the_news_calendar():
    """Two independent implementations of R5 (news.NewsCalendar in the engine, the audit's
    own C2 arithmetic) agree on random events, including explicit known_from_ts values."""
    rng = random.Random(5)
    t0 = ts(0)
    kinds = ("macro", "regulatory", "legal", "unlock", "bnb_burn", "launchpool", "other")
    scopes = ("ALL", "BTC", "BNB", "ETH", "EXCHANGE:binance", "EXCHANGE:coinbase")
    events = []
    for _ in range(60):
        at = t0 + rng.randrange(0, 20 * DAY_MS, 15 * 60_000)
        known = rng.choice((None, None, at - rng.randrange(0, 30 * HOUR_MS, 3_600_000)))
        impact = rng.choice(("high", "high", "medium", "low"))
        ev = NewsEvent(at, rng.choice(scopes), impact, rng.choice(kinds), "x", known)
        events.append(ev)
    cal = NewsCalendar(events, CFG)
    checked = blocked = 0
    for pair in ("BTC/USDT", "ETH/USDT", "BNB/USDT"):
        for k in range(0, 21 * 24 * 4):  # every 15 minutes over 21 days
            decided = t0 + k * 15 * 60_000
            audit = cross_trade_violations([_probe(pair, decided)], CFG, events)
            engine_blocks = not cal.check(pair, decided).allowed
            assert engine_blocks == any("(R5)" in v for v in audit), (pair, decided)
            checked += 1
            blocked += engine_blocks
    assert 0.05 * checked < blocked < 0.95 * checked  # both outcomes are exercised


# ---------------------------------------------------------------------------- cross-trade (live)
def _live(pair, decided_h, exit_h, reason=EXIT_TP, pnl=10.0, trade_id=None, risk=None):
    """A testnet journal row with REAL fill times: decided/exit in hours after T0."""
    t = _probe(pair, ts(0) + round(decided_h * HOUR_MS), trade_id or 0)
    t.exit_ts = ts(0) + round(exit_h * HOUR_MS)
    t.exit_reason, t.pnl = reason, pnl
    t.risk_pct = t.risk_pct if risk is None else risk
    return t


def _journal(*rows):
    out = []
    for k, t in enumerate(rows, start=1):
        t.trade_id = k
        out.append(t)
    return out


def test_cross_trade_checks_are_clean_on_real_runs():
    _data, events, res = clean_run()
    assert cross_trade_violations(res.trades, CFG, events, exit_time_uncertainty_ms=TF) == []
    assert cross_trade_violations(res.trades, CFG, None, exit_time_uncertainty_ms=TF) == []


def test_live_bench_uses_real_exit_times():
    """Offset 0 (testnet fills): the bench runs 24h from the actual 3rd stop-loss fill."""
    sls = [_live("BTC/USDT", 4 * k, 4 * k + 2, EXIT_SL, -10.0) for k in range(3)]  # last SL at 10h
    early = _journal(*copy.deepcopy(sls), _live("BTC/USDT", 10 + 23, 40))
    late = _journal(*copy.deepcopy(sls), _live("BTC/USDT", 10 + 25, 40))
    assert any("benched" in v for v in cross_trade_violations(early, CFG))
    assert cross_trade_violations(late, CFG) == []
    # The same rows read as a BACKTEST journal (exit certain one candle later) are benched.
    assert any("benched" in v for v in cross_trade_violations(late, CFG, None, TF))
    assert benches(late, CFG, 0)["BTC/USDT"] == [(ts(0) + 10 * HOUR_MS, ts(0) + 34 * HOUR_MS)]


def test_live_r6_overlaps_and_budget():
    bnb_stack = _journal(_live("BTC/USDT", 0, 20), _live("BNB/USDT", 8, 12))
    assert any("BNB never stacks" in v for v in cross_trade_violations(bnb_stack, CFG))
    full_size = _journal(_live("BTC/USDT", 0, 20), _live("ETH/USDT", 8, 30))
    msgs = cross_trade_violations(full_size, CFG)
    assert any("one full-size position uses the whole budget" in v for v in msgs)
    assert any("exceeds the shared 1% budget" in v for v in msgs)
    pyramid = _journal(_live("ETH/USDT", 0, 20), _live("ETH/USDT", 12, 30))
    assert any("no pyramiding" in v for v in cross_trade_violations(pyramid, CFG))
    # An exit at exactly the next decision frees the budget (exits are processed first).
    sequential = _journal(_live("BTC/USDT", 0, 8), _live("ETH/USDT", 8, 30))
    assert cross_trade_violations(sequential, CFG) == []
    # Under the guard two half-size cluster positions may overlap within the budget ...
    guard = StrategyConfig(expectancy_guard=True)
    halves = _journal(_live("BTC/USDT", 0, 20, risk=0.5), _live("ETH/USDT", 8, 30, risk=0.5))
    assert cross_trade_violations(halves, guard) == []
    # ... but never above it.
    over = _journal(_live("BTC/USDT", 0, 20, risk=0.6), _live("ETH/USDT", 8, 30, risk=0.6))
    assert any("exceeds the shared" in v for v in cross_trade_violations(over, guard))
    # A position that never closed keeps its budget.
    still_open = _live("BTC/USDT", 0, 4)
    still_open.exit_ts = still_open.pnl = None
    stuck = _journal(still_open, _live("ETH/USDT", 100, 110))
    assert any("whole budget" in v for v in cross_trade_violations(stuck, CFG))


def test_live_weekly_halt_and_starting_equity():
    loss = _live("BTC/USDT", 0, 4, EXIT_SL, pnl=-400.0)  # 4 % of 10k in one day
    rows = _journal(loss, _live("ETH/USDT", 24, 30))
    assert any("halt limit" in v for v in cross_trade_violations(rows, CFG))
    # A 20k testnet account: the same loss is 2 %, below the 3 % limit.
    assert cross_trade_violations(rows, CFG, starting_equity=20_000.0) == []
    # Seven days (plus a candle) after the loss the halt has lifted.
    later = _journal(copy.deepcopy(loss), _live("ETH/USDT", 4 + 7 * 24 + 4, 200))
    assert cross_trade_violations(later, CFG) == []


def test_cross_trade_r5_only_when_events_are_given():
    row = _journal(_live("BTC/USDT", 8, 12))
    macro = NewsEvent(ts(0) + 9 * HOUR_MS, "ALL", "high", "macro", "CPI")
    assert cross_trade_violations(row, CFG) == []
    assert any("(R5)" in v for v in cross_trade_violations(row, CFG, [macro]))
    surprise = NewsEvent(ts(0) + 9 * HOUR_MS, "ALL", "high", "regulatory", "ban")
    assert cross_trade_violations(row, CFG, [surprise]) == []  # unknowable at the decision
    before = NewsEvent(ts(0) + 7 * HOUR_MS, "ALL", "high", "regulatory", "ban")
    assert any("(R5)" in v for v in cross_trade_violations(row, CFG, [before]))
    # An explicit known_from narrows a scheduled window: announced only 30 min before.
    late_notice = replace(macro, known_from_ts=ts(0) + 8 * HOUR_MS + 30 * 60_000)
    assert cross_trade_violations(row, CFG, [late_notice]) == []


@pytest.mark.parametrize("bad", [-1, True, 1.5])
def test_exit_offset_is_validated(bad):
    with pytest.raises(ValueError, match="exit_time_uncertainty_ms"):
        cross_trade_violations([], CFG, None, bad)


# ---------------------------------------------------------------------------- CLI
def test_cli_audits_a_journal_and_flags_a_tampered_one(tmp_path, capsys):
    data, events, res = clean_run()
    for pair, candles in data.items():
        save_candles_csv(candles, tmp_path / "data" / pair_filename(pair))
    events_csv = tmp_path / "events.csv"
    events_csv.write_text(
        "time_utc,scope,impact,kind,note\n"
        + "".join(f"{e.ts},{e.scope},{e.impact},{e.kind},{e.note}\n" for e in events)
    )
    journal = tmp_path / "trades.csv"
    write_journal(res.trades, journal)
    argv = ["--journal", str(journal), "--data-dir", str(tmp_path / "data")]
    assert main([*argv, "--events", str(events_csv)]) == 0
    assert "0 violation(s)" in capsys.readouterr().out
    tampered = copy.deepcopy(res.trades)
    tampered[3].risk_pct = 2.0
    write_journal(tampered, journal)
    assert main(argv) == 1
    assert "(R7)" in capsys.readouterr().out
    rebuilt = result_from_journal(read_journal(journal), CFG)
    assert rebuilt.final_equity == pytest.approx(res.final_equity, rel=1e-12)


def _variant_journal(root: Path, **changes) -> list[str]:
    """Backtest of a synthetic world under a non-default config, journaled with its candles."""
    cfg = CFG.with_changes(**changes)
    data, events = world("planted", seed=3, years=2.0)
    res = run_backtest(data, cfg, events)
    assert res.trades and check_invariants(res, data, events, cfg) == []
    for pair, candles in data.items():
        save_candles_csv(candles, root / "data" / pair_filename(pair))
    write_journal(res.trades, root / "trades.csv")
    return ["--journal", str(root / "trades.csv"), "--data-dir", str(root / "data")]


def test_cli_config_audits_a_discovery_variant_journal(tmp_path, capsys):
    """An rr2.5 journal: take-profits are +2.5R by design, false violations under defaults."""
    argv = _variant_journal(tmp_path, reward_risk=2.5)
    assert main(argv) == 1
    assert "not exactly +2R" in capsys.readouterr().out
    cfg_file = tmp_path / "rr25.json"
    cfg_file.write_text(json.dumps({"reward_risk": 2.5}))
    assert main([*argv, "--config", str(cfg_file)]) == 0
    out = capsys.readouterr().out
    assert "0 violation(s)" in out and "rr2.5_vol1.5_rsi50-70" in out


def test_cli_fee_rate_audits_a_high_fee_journal(tmp_path, capsys):
    """A 0.6 %/side fee journal (e.g. a low Coinbase tier) audits clean with its real costs."""
    argv = _variant_journal(tmp_path, fee_rate=0.006)
    assert main(argv) == 1
    assert "all-in loss per unit" in capsys.readouterr().out
    assert main([*argv, "--fee-rate", "0.006"]) == 0
    assert "fee_rate 0.006/side" in capsys.readouterr().out
    # The flags override the JSON file, and both combine.
    cfg_file = tmp_path / "cfg.json"
    cfg_file.write_text(json.dumps({"fee_rate": 0.001, "slippage_pct": 0.05}))
    assert main([*argv, "--config", str(cfg_file), "--fee-rate", "0.006"]) == 0


def test_cli_slippage_flag(tmp_path, capsys):
    argv = _variant_journal(tmp_path, slippage_pct=0.2)
    assert main(argv) == 1
    capsys.readouterr()
    assert main([*argv, "--slippage-pct", "0.2"]) == 0


@pytest.mark.parametrize(
    "overrides",
    [{"reward_risk": 1.5}, {"fee_rate": 0.0}, {"no_such_field": 1}, [1, 2]],
    ids=["loosened-rr", "zero-fee", "unknown-field", "not-an-object"],
)
def test_cli_rejects_an_illegal_config(tmp_path, capsys, overrides):
    cfg_file = tmp_path / "bad.json"
    cfg_file.write_text(json.dumps(overrides))
    with pytest.raises(SystemExit) as err:
        main(["--synthetic", "null", "--years", "0.5", "--config", str(cfg_file)])
    assert err.value.code == 2
    assert "invalid --config" in capsys.readouterr().err
    with pytest.raises(ConfigError):
        load_cli_config(str(cfg_file))


def test_load_cli_config_defaults_and_overrides(tmp_path):
    assert load_cli_config(None) == StrategyConfig()
    cfg_file = tmp_path / "c.json"
    cfg_file.write_text(json.dumps({"reward_risk": 3, "vol_mult": 2.0}))
    cfg = load_cli_config(str(cfg_file), fee_rate=0.002)
    assert (cfg.reward_risk, cfg.vol_mult, cfg.fee_rate) == (3.0, 2.0, 0.002)


def test_cli_runs_and_audits_a_synthetic_world(capsys):
    assert main(["--synthetic", "decay", "--seed", "2", "--years", "1.5"]) == 0
    assert "synthetic decay seed 2" in capsys.readouterr().out
    assert main(["--synthetic", "zero_edge", "--seed", "2", "--years", "1.5", "--gaps"]) == 0
    assert "synthetic zero_edge seed 2 (gaps)" in capsys.readouterr().out


# ---------------------------------------------------------------------------- D4 stop-fill stress
@cache
def stressed_run(k: float = 0.5):
    data, events = world("planted")
    cfg = CFG.with_changes(stop_fill_wick_k=k)
    return data, events, cfg, run_backtest(data, cfg, events)


def _gap(t, data) -> bool:
    return next(c for c in data[t.pair] if c.ts == t.exit_ts).open <= t.stop


def test_stop_fill_stress_run_is_clean_only_under_its_own_config():
    data, events, cfg, res = stressed_run()
    clean_sl = [t for t in res.trades if t.exit_reason == EXIT_SL and not _gap(t, data)]
    assert len(clean_sl) > 20 and all(t.r_multiple < -1.0 for t in clean_sl)
    assert all(abs(t.r_multiple - 2.0) < 1e-9 for t in res.trades if t.exit_reason == EXIT_TP)
    assert check_invariants(res, data, events, cfg) == []
    # Audited as a default (k = 0) run, the same trades are violations: r < -1 on non-gap
    # stops is accepted ONLY when the config says the stress model was used.
    msgs = check_invariants(res, data, events, CFG)
    assert any("below -1R without a gap" in m for m in msgs)
    assert any("implied by the exit model" in m for m in msgs)


def test_stressed_stop_reported_at_the_touch_price_is_caught():
    data, events, cfg, res = stressed_run()
    bad = replace(res, trades=copy.deepcopy(res.trades))
    t = next(t for t in bad.trades if t.exit_reason == EXIT_SL and not _gap(t, data))
    _resettle(t, t.stop * (1 - CFG.slippage_pct / 100) * 1.001)  # better than even the touch
    msgs = check_invariants(bad, data, events, cfg)
    assert any("implied by the exit model" in m for m in msgs)
    assert any("stressed stop-loss made" in m and "(D4)" in m for m in msgs)


# ---------------------------------------------------------------------------- D5 live journals
@cache
def live_run():
    """A LiveSession journal with REAL (mid-candle) exit times: offset 0, as on testnet."""
    data, events = world("planted")
    run = drive_live_session(data, CFG, events, exit_time_uncertainty_ms=0, exit_fill_ms=TF // 2)
    return data, events, run.trades


def test_live_session_journal_is_clean_in_live_mode():
    data, events, trades = live_run()
    assert len(trades) > 30
    assert live_journal_violations(trades, data, CFG, events) == []
    assert live_journal_violations(trades, data, CFG) == []  # R5 skipped without events
    # Read as a BACKTEST journal it is not: its exit times are not candle opens.
    assert check_invariants(result_from_journal(trades, CFG), data, events, CFG) != []


def test_open_positions_are_legal_until_their_stop_is_reached():
    data, events, trades = live_run()
    rows = copy.deepcopy(trades)
    last = rows[-1]
    exit_candle = last.exit_ts - TF // 2
    last.exit_ts = last.exit_price = last.exit_reason = last.pnl = last.r_multiple = None
    last.fees = 0.0
    cut = {p: [c for c in cs if c.ts < exit_candle] for p, cs in data.items()}
    assert live_journal_violations(rows, cut, CFG, events) == []
    # With the candle that reached its stop / target supplied, a still-open trade is a
    # paused exit.
    msgs = live_journal_violations(rows, data, CFG, events)
    assert any("exit delayed" in m and "is still open" in m for m in msgs), msgs


def _live_signal_shifted(rows, data, events):
    t = rows[3]
    t.signal_ts -= 3 * TF
    t.entry_ts -= 3 * TF


def _live_before_the_close(rows, data, events):
    rows[4].entry_ts -= 60_000


def _live_stale(rows, data, events):
    rows[4].entry_ts += TF


def _live_stop_above_structure(rows, data, events):
    t = rows[2]
    t.stop *= 1.004
    t.risk_amount = t.qty * unit_loss(t.entry_price, t.stop, CFG)


def _live_exit_delayed(rows, data, events):
    t = next(t for t in rows if t.exit_reason == EXIT_SL)
    t.exit_ts += 2 * TF


def _live_oversized(rows, data, events):
    t = rows[5]  # 1.5x the size while still claiming 1 %: only the equity check sees it
    for name in ("qty", "risk_amount", "fees", "pnl"):
        setattr(t, name, getattr(t, name) * 1.5)


def _live_bnb_stacks(rows, data, events):
    t = copy.deepcopy(next(t for t in rows if t.pair == "BTC/USDT"))
    t.trade_id, t.pair = 10_001, "BNB/USDT"
    rows.append(t)


def _bench_rows(rows, after_open_h: float):
    """Make the first pair's first three trades stop-losses and move its 4th entry to the
    candle close ``after_open_h`` hours after the OPEN of the 3rd stop-loss's exit candle
    (its real fill is half a candle later)."""
    mine = [t for t in rows if t.pair == rows[0].pair]
    for t in mine[:3]:
        t.exit_reason = EXIT_SL
    decided = mine[2].exit_ts - TF // 2 + round(after_open_h * HOUR_MS)
    mine[3].signal_ts, mine[3].entry_ts = decided - TF, decided
    return mine[3]


def _live_bench_ignored(rows, data, events):
    _bench_rows(rows, 24)  # 24h after the exit candle opened = only 22h after the real fill


def _live_news(rows, data, events):
    t = rows[6]
    events.append(NewsEvent(t.signal_ts + TF + HOUR_MS, "ALL", "high", "macro", "planted"))


def _live_missing_candle(rows, data, events):
    t = rows[7]
    data[t.pair] = [c for c in data[t.pair] if c.ts != t.signal_ts]


def _live_half_closed(rows, data, events):
    rows[8].pnl = None


@pytest.mark.parametrize(
    ("corruption", "pattern"),
    [
        (_live_signal_shifted, r"R1 trend|\(R[2348]\)"),
        (_live_before_the_close, "before its signal candle closed"),
        (_live_stale, "stale signal"),
        (_live_stop_above_structure, r"\(R8\)"),
        (_live_exit_delayed, "exit delayed"),
        (_live_oversized, r"of the realized equity .* above the 1% cap \(R7\)"),
        (_live_bnb_stacks, "BNB never stacks"),
        (_live_bench_ignored, "benched"),
        (_live_news, r"\(R5\)"),
        (_live_missing_candle, "no signal candle in the supplied data"),
        (_live_half_closed, "half-closed"),
    ],
)
def test_live_mode_catches_each_violation(corruption, pattern):
    data, events, trades = live_run()
    rows, data, events = copy.deepcopy(trades), dict(data), list(events)
    corruption(rows, data, events)
    msgs = live_journal_violations(rows, data, CFG, events)
    assert any(re.search(pattern, m) for m in msgs), msgs[:5]


def test_live_mode_bench_runs_24h_from_the_real_fill():
    """Offset 0: the bench runs 24h from the REAL fill of the 3rd stop-loss. A decision 22h
    after it is benched; one 26h after it is legal in live mode, although the same rows read
    with the backtest offset (exit certain a candle later) would still be benched."""
    data, events, trades = live_run()
    early = copy.deepcopy(trades)
    victim = _bench_rows(early, 24)
    msgs = live_journal_violations(early, data, CFG, events)
    assert any(f"#{victim.trade_id} " in m and "benched" in m for m in msgs), msgs
    late = copy.deepcopy(trades)
    victim = _bench_rows(late, 24 + TF / HOUR_MS)
    assert [m for m in live_journal_violations(late, data, CFG, events) if "(R9)" in m] == []
    backtest_offset = cross_trade_violations(late, CFG, None, TF)
    assert any(f"#{victim.trade_id} " in m and "benched" in m for m in backtest_offset)


def _live_files(root: Path, trades, events) -> list[str]:
    data, _events, _trades = live_run()
    for pair, candles in data.items():
        save_candles_csv(candles, root / "data" / pair_filename(pair))
    (root / "events.csv").write_text(
        "time_utc,scope,impact,kind,note\n"
        + "".join(f"{e.ts},{e.scope},{e.impact},{e.kind},{e.note}\n" for e in events)
    )
    write_journal(trades, root / "testnet.csv")
    return ["--live-journal", str(root / "testnet.csv"), "--data-dir", str(root / "data")]


def test_cli_live_journal_mode(tmp_path, capsys):
    _data, events, trades = live_run()
    argv = _live_files(tmp_path, trades, events)
    assert main(argv) == 0
    out = capsys.readouterr().out
    assert "exit offset 0" in out and "R5 NOT checked" in out and "0 violation(s)" in out
    assert main([*argv, "--events", str(tmp_path / "events.csv")]) == 0
    assert f"R5 checked against {len(events)} event(s)" in capsys.readouterr().out
    # The same journal on a 5,000 account: every full-size trade risked 2 % of it (R7).
    assert main([*argv, "--starting-equity", "5000"]) == 1
    assert "(R7)" in capsys.readouterr().out
    tampered = copy.deepcopy(trades)
    tampered[2].stop *= 1.004
    tampered[2].risk_amount = tampered[2].qty * unit_loss(
        tampered[2].entry_price, tampered[2].stop, CFG
    )
    write_journal(tampered, tmp_path / "testnet.csv")
    assert main(argv) == 1
    assert "(R8)" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(["--live-journal", str(tmp_path / "testnet.csv")])  # --data-dir is required
