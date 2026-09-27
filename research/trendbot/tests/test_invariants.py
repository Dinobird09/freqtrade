"""invariants.py: clean on real runs, and it catches every kind of rule violation.

Two kinds of evidence: (1) a clean backtest of every synthetic world has no violations,
(2) corrupting a single trade, or switching a rule OFF inside the engine, is reported with
the violated rule named, so the audit is not trivially passing.
"""

from __future__ import annotations

import copy
from dataclasses import replace
from functools import cache

import pytest

from research.trendbot import backtester as bt
from research.trendbot import gatekeeper as gk_mod
from research.trendbot.backtester import run_backtest
from research.trendbot.circuit_breakers import CircuitBreakers
from research.trendbot.config import StrategyConfig
from research.trendbot.correlation import CorrelationGuard
from research.trendbot.data import pair_filename, save_candles_csv
from research.trendbot.invariants import (
    benches,
    blocked_open_trades,
    check_invariants,
    main,
    result_from_journal,
)
from research.trendbot.journal import read_journal, write_journal
from research.trendbot.models import EXIT_SL, Decision, NewsEvent, StopPlan
from research.trendbot.news import NewsCalendar
from research.trendbot.synthetic import WORLDS, make_world
from research.trendbot.tests.bt_helpers import TF, Scenario, data_of, news, slot


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


def _stop_above_structure(bad, data):
    t = _first(bad)
    t.stop *= 1.004
    t.risk_amount = t.qty * (t.entry_price - t.stop)


def _risk_above_cap(bad, data):
    _first(bad, "BNB/USDT").risk_pct = 0.75


def _rr_below_2(bad, data):
    t = _first(bad)
    t.target = t.entry_price + 1.5 * (t.entry_price - t.stop)


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


@pytest.mark.parametrize(
    ("corruption", "expected"),
    [
        (_stop_above_structure, "(R8)"),
        (_risk_above_cap, "cap (R7)"),
        (_rr_below_2, "reward:risk"),
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
    assert found[0] == (btc[2].exit_ts, btc[2].exit_ts + 24 * 3_600_000)


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


def test_cli_runs_and_audits_a_synthetic_world(capsys):
    assert main(["--synthetic", "decay", "--seed", "2", "--years", "1.5"]) == 0
    assert "synthetic decay seed 2" in capsys.readouterr().out
