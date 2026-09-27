import inspect
import random

import pytest

from research.trendbot import circuit_breakers
from research.trendbot.circuit_breakers import CircuitBreakers
from research.trendbot.config import RULE_IDS, StrategyConfig
from research.trendbot.journal import ms_to_iso, read_journal, write_journal
from research.trendbot.models import DAY_MS, EXIT_END, EXIT_SL, EXIT_TP, HOUR_MS, Trade


T0 = 1_704_067_200_000  # 2024-01-01T00:00:00Z
EQUITY = 10_000.0  # halt threshold with the default 3% limit: -300
PAIRS = ("BTC/USDT", "ETH/USDT", "BNB/USDT")
BENCH_MS = 24 * HOUR_MS
WINDOW_MS = 7 * DAY_MS


def closed(
    trade_id: int, pair: str, exit_ts: int, reason: str = EXIT_SL, pnl: float = -10.0
) -> Trade:
    return Trade(
        trade_id=trade_id,
        pair=pair,
        variant="base",
        signal_ts=exit_ts - 8 * HOUR_MS,
        entry_ts=exit_ts - 4 * HOUR_MS,
        entry_price=100.0,
        stop=95.0,
        target=110.0,
        qty=1.0,
        risk_amount=10.0,
        risk_pct=0.1,
        exit_ts=exit_ts,
        exit_price=95.0 if reason == EXIT_SL else 110.0,
        exit_reason=reason,
        pnl=pnl,
        r_multiple=pnl / 10.0,
    )


def one_sentence(text: str) -> bool:
    return text.endswith(".") and ". " not in text and "\n" not in text


def feed(cb: CircuitBreakers, trades) -> list[str]:
    out: list[str] = []
    for t in trades:
        out.extend(cb.on_trade_closed(t))
    return out


# ---------------------------------------------------------------------------- consecutive SL
def test_three_consecutive_stop_losses_bench_the_pair_for_24h():
    cfg = StrategyConfig()
    cb = CircuitBreakers(cfg)
    t3 = T0 + 8 * HOUR_MS
    assert feed(cb, [closed(1, "BTC/USDT", T0), closed(2, "BTC/USDT", T0 + 4 * HOUR_MS)]) == []
    assert cb.can_enter("BTC/USDT", t3, EQUITY).allowed
    messages = cb.on_trade_closed(closed(3, "BTC/USDT", t3))
    assert len(messages) == 1 and one_sentence(messages[0])
    assert "BTC/USDT" in messages[0] and "#1, #2, #3" in messages[0]
    assert ms_to_iso(t3 + BENCH_MS) in messages[0]
    assert cb.streak("BTC/USDT") == 0  # reset after benching

    at_start = cb.can_enter("BTC/USDT", t3, EQUITY)  # inclusive of the bench start
    assert not at_start.allowed
    assert at_start.rule == "R9_circuit_breaker" and at_start.rule in RULE_IDS
    assert one_sentence(at_start.reason)
    assert "#1, #2, #3" in at_start.reason and "limit 3" in at_start.reason
    assert ms_to_iso(t3 + BENCH_MS) in at_start.reason
    assert not cb.can_enter("BTC/USDT", t3 + BENCH_MS - 1, EQUITY).allowed
    assert cb.can_enter("BTC/USDT", t3 + BENCH_MS, EQUITY).allowed  # exclusive at the end
    assert cb.can_enter("BTC/USDT", t3 - 1, EQUITY).allowed  # not benched before its start
    for other in ("ETH/USDT", "BNB/USDT"):
        assert cb.can_enter(other, t3, EQUITY).allowed
    bench = cb.active_bench("BTC/USDT", t3)
    assert bench is not None and bench.trade_ids == (1, 2, 3)
    assert [b.pair for b in cb.benches(t3)] == ["BTC/USDT"]


@pytest.mark.parametrize("reset_reason", [EXIT_TP, EXIT_END, "OTHER"])
def test_any_other_exit_resets_the_streak(reset_reason):
    cb = CircuitBreakers(StrategyConfig())
    trades = [
        closed(1, "ETH/USDT", T0),
        closed(2, "ETH/USDT", T0 + 1 * HOUR_MS),
        closed(3, "ETH/USDT", T0 + 2 * HOUR_MS, reset_reason, pnl=5.0),
        closed(4, "ETH/USDT", T0 + 3 * HOUR_MS),
        closed(5, "ETH/USDT", T0 + 4 * HOUR_MS),
    ]
    assert feed(cb, trades) == []
    assert cb.streak("ETH/USDT") == 2
    assert cb.can_enter("ETH/USDT", T0 + 4 * HOUR_MS, EQUITY).allowed


def test_streak_restarts_after_a_bench():
    cb = CircuitBreakers(StrategyConfig())
    feed(cb, [closed(i, "BNB/USDT", T0 + i * HOUR_MS) for i in (1, 2, 3)])
    assert cb.streak("BNB/USDT") == 0
    after = T0 + 3 * HOUR_MS + BENCH_MS
    assert feed(cb, [closed(4, "BNB/USDT", after)]) == []
    assert cb.streak("BNB/USDT") == 1
    assert cb.can_enter("BNB/USDT", after, EQUITY).allowed


def test_streaks_are_per_pair():
    cb = CircuitBreakers(StrategyConfig())
    order = ["BTC/USDT", "ETH/USDT", "BTC/USDT", "ETH/USDT", "BTC/USDT"]
    messages = feed(cb, [closed(i, p, T0 + i * HOUR_MS) for i, p in enumerate(order, 1)])
    assert len(messages) == 1 and "BTC/USDT" in messages[0]
    ts = T0 + 5 * HOUR_MS
    assert not cb.can_enter("BTC/USDT", ts, EQUITY).allowed
    assert cb.can_enter("ETH/USDT", ts, EQUITY).allowed
    assert cb.streak("ETH/USDT") == 2


def test_stricter_limit_is_respected():
    cb = CircuitBreakers(StrategyConfig(consecutive_sl_limit=1))
    assert len(cb.on_trade_closed(closed(1, "BTC/USDT", T0))) == 1
    assert not cb.can_enter("BTC/USDT", T0, EQUITY).allowed


# ---------------------------------------------------------------------------- trailing loss
def test_weekly_loss_halts_all_pairs_and_recovers_when_losses_age_out():
    cb = CircuitBreakers(StrategyConfig())
    t_b = T0 + DAY_MS
    feed(
        cb,
        [
            closed(1, "BTC/USDT", T0, EXIT_END, pnl=-200.0),
            closed(2, "ETH/USDT", t_b, EXIT_SL, pnl=-150.0),
        ],
    )
    for pair in PAIRS:
        d = cb.can_enter(pair, t_b, EQUITY)
        assert not d.allowed and d.rule == "R9_circuit_breaker"
        assert one_sentence(d.reason)
        assert "-350.00" in d.reason and "-300.00" in d.reason and "#1, #2" in d.reason
    assert cb.window_pnl(t_b) == (-350.0, (1, 2))
    assert not cb.can_enter("BNB/USDT", T0 + WINDOW_MS - 1, EQUITY).allowed
    # at T0 + 7d, trade #1 (exit_ts == ts - window) is outside (ts - window, ts]
    recovered = cb.can_enter("BNB/USDT", T0 + WINDOW_MS, EQUITY)
    assert recovered.allowed and one_sentence(recovered.reason)
    assert cb.window_pnl(T0 + WINDOW_MS) == (-150.0, (2,))
    assert cb.halt_lifts_at(t_b, EQUITY) == T0 + WINDOW_MS
    assert cb.halt_lifts_at(T0 + WINDOW_MS, EQUITY) is None


def test_halt_threshold_is_strict_and_scales_with_equity():
    cb = CircuitBreakers(StrategyConfig())
    feed(cb, [closed(1, "BTC/USDT", T0, EXIT_END, pnl=-300.0)])
    assert cb.halt_threshold(EQUITY) == -300.0
    assert cb.can_enter("BTC/USDT", T0, EQUITY).allowed  # exactly at the limit: allowed
    assert not cb.can_enter("BTC/USDT", T0, 9_999.0).allowed
    assert cb.can_enter("BTC/USDT", T0, 20_000.0).allowed


def test_window_counts_only_trades_closed_at_or_before_ts():
    cb = CircuitBreakers(StrategyConfig())
    feed(cb, [closed(1, "BTC/USDT", T0, EXIT_END, pnl=-500.0)])
    assert cb.can_enter("ETH/USDT", T0 - 1, EQUITY).allowed
    assert not cb.can_enter("ETH/USDT", T0, EQUITY).allowed


def test_gains_offset_losses_in_the_window():
    cb = CircuitBreakers(StrategyConfig())
    feed(
        cb,
        [
            closed(1, "BTC/USDT", T0, EXIT_END, pnl=-400.0),
            closed(2, "ETH/USDT", T0 + HOUR_MS, EXIT_TP, pnl=250.0),
        ],
    )
    assert not cb.can_enter("BTC/USDT", T0, EQUITY).allowed
    assert cb.can_enter("BTC/USDT", T0 + HOUR_MS, EQUITY).allowed


def test_on_trade_closed_reports_halt_trip_once_when_equity_given():
    cb = CircuitBreakers(StrategyConfig())
    assert cb.on_trade_closed(closed(1, "BTC/USDT", T0, EXIT_END, -200.0), EQUITY) == []
    tripped = cb.on_trade_closed(closed(2, "ETH/USDT", T0 + HOUR_MS, EXIT_END, -200.0), EQUITY)
    assert len(tripped) == 1 and one_sentence(tripped[0]) and "-400.00" in tripped[0]
    again = cb.on_trade_closed(closed(3, "BNB/USDT", T0 + 2 * HOUR_MS, EXIT_END, -1.0), EQUITY)
    assert again == []  # already halted: nothing new tripped
    silent = CircuitBreakers(StrategyConfig())
    assert feed(silent, [closed(1, "BTC/USDT", T0, EXIT_END, -900.0)]) == []  # no equity given
    assert not silent.can_enter("BTC/USDT", T0, EQUITY).allowed  # still enforced


# ---------------------------------------------------------------------------- exits
def test_there_is_no_exit_related_api():
    forbidden = ("exit", "sell", "liquidat", "flatten", "close_position")
    public = [n for n in dir(CircuitBreakers) if callable(getattr(CircuitBreakers, n))]
    assert [n for n in public if any(w in n.lower() for w in forbidden)] == []
    assert "exits are never paused" in (CircuitBreakers.__doc__ or "").lower()
    assert "exits are never paused" in (circuit_breakers.__doc__ or "").lower()
    params = inspect.signature(CircuitBreakers.can_enter).parameters
    assert list(params) == ["self", "pair", "ts", "equity"]


def test_exits_are_processed_while_benched_and_halted():
    cb = CircuitBreakers(StrategyConfig())
    feed(cb, [closed(i, "BTC/USDT", T0 + i * HOUR_MS, pnl=-120.0) for i in (1, 2, 3)])
    ts = T0 + 3 * HOUR_MS
    assert not cb.can_enter("BTC/USDT", ts, EQUITY).allowed  # benched
    assert not cb.can_enter("ETH/USDT", ts, EQUITY).allowed  # halted (-360 < -300)
    # A position still open on another pair closes while everything is gated: always accepted.
    cb.on_trade_closed(closed(4, "ETH/USDT", ts + HOUR_MS, EXIT_TP, pnl=400.0))
    assert cb.window_pnl(ts + HOUR_MS) == (40.0, (1, 2, 3, 4))
    assert cb.can_enter("ETH/USDT", ts + HOUR_MS, EQUITY).allowed
    assert not cb.can_enter("BTC/USDT", ts + HOUR_MS, EQUITY).allowed  # bench still applies


def test_open_trade_is_rejected():
    cb = CircuitBreakers(StrategyConfig())
    t = closed(1, "BTC/USDT", T0)
    t.exit_ts = None
    with pytest.raises(ValueError, match="not closed"):
        cb.on_trade_closed(t)


# ---------------------------------------------------------------------------- replay
def scenario() -> list[Trade]:
    rng = random.Random(11)
    trades: list[Trade] = []
    ts = T0
    for tid in range(1, 121):
        ts += rng.choice((1, 2, 3, 6, 12, 30)) * HOUR_MS
        pair = rng.choice(PAIRS)
        reason = rng.choice((EXIT_SL, EXIT_SL, EXIT_SL, EXIT_TP, EXIT_END))
        pnl = -rng.uniform(20, 140) if reason == EXIT_SL else rng.uniform(-50, 220)
        trades.append(closed(tid, pair, ts, reason, pnl))
    return trades


def probe_times(trades: list[Trade]) -> list[int]:
    out: list[int] = []
    for t in trades[::7]:
        out += [t.exit_ts, t.exit_ts + HOUR_MS, t.exit_ts + BENCH_MS, t.exit_ts + WINDOW_MS]
    return sorted(set(out))


def decisions(cb: CircuitBreakers, ts: int):
    return [cb.can_enter(p, ts, EQUITY) for p in PAIRS]


def test_from_journal_reproduces_live_state(tmp_path):
    trades = scenario()
    cfg = StrategyConfig()
    live = CircuitBreakers(cfg)
    live_log: list[tuple[int, list]] = []
    probes = probe_times(trades)
    i = 0
    for ts in probes:  # live: interleave closes and entry checks chronologically
        while i < len(trades) and trades[i].exit_ts <= ts:
            live.on_trade_closed(trades[i])
            i += 1
        live_log.append((ts, decisions(live, ts)))
    for t in trades[i:]:
        live.on_trade_closed(t)
    tripped = [d for _ts, ds in live_log for d in ds if not d.allowed]
    assert any(d.reason.startswith("All entries are halted") for d in tripped)  # the halt
    assert any("consecutive stop-losses" in d.reason for d in tripped)  # and the bench

    shuffled = list(trades)
    random.Random(3).shuffle(shuffled)
    still_open = closed(999, "ETH/USDT", T0)
    still_open.exit_ts = None
    replay = CircuitBreakers.from_journal([*shuffled, still_open], cfg)
    path = tmp_path / "trades.csv"
    write_journal([*shuffled, still_open], path)
    reloaded = CircuitBreakers.from_journal(read_journal(path), cfg)

    for ts, expected in live_log:
        assert decisions(replay, ts) == expected
        assert decisions(reloaded, ts) == expected
        # no look-ahead: only trades closed by ts matter
        known = CircuitBreakers.from_journal([t for t in trades if t.exit_ts <= ts], cfg)
        assert decisions(known, ts) == expected
    for pair in PAIRS:
        assert replay.streak(pair) == live.streak(pair)


# ---------------------------------------------------------------------------- v2 A2 exit timing
TF = StrategyConfig().timeframe_ms  # 4h: a backtest exit_ts is the OPEN of the exit candle


def three_sl_candles(pair: str = "BTC/USDT") -> tuple[list[Trade], int]:
    """Three stop-losses recorded backtest-style (exit_ts = exit candle open) + the last open."""
    opens = [T0, T0 + TF, T0 + 2 * TF]
    return [closed(i, pair, ts) for i, ts in enumerate(opens, 1)], opens[-1]


def test_backtest_convention_bench_lasts_24h_of_real_time_from_the_candle_close():
    trades, last_open = three_sl_candles()
    candle_close = last_open + TF  # the stop-loss is certain only here
    cb = CircuitBreakers(StrategyConfig(), exit_time_uncertainty_ms=TF)
    assert cb.exit_time_uncertainty_ms == TF
    messages = feed(cb, trades)
    assert len(messages) == 1 and ms_to_iso(candle_close + BENCH_MS) in messages[0]
    bench = cb.active_bench("BTC/USDT", candle_close)
    assert bench is not None
    assert (bench.start_ts, bench.until_ts) == (candle_close, candle_close + BENCH_MS)
    # the decision at the exit candle's OPEN cannot know the stop-loss yet (no look-ahead)
    assert cb.can_enter("BTC/USDT", last_open, EQUITY).allowed
    denied = cb.can_enter("BTC/USDT", candle_close, EQUITY)
    assert not denied.allowed and one_sentence(denied.reason)
    assert f"the stop-loss recorded at {ms_to_iso(last_open)} can have filled" in denied.reason
    assert not cb.can_enter("BTC/USDT", candle_close + BENCH_MS - 1, EQUITY).allowed
    assert cb.can_enter("BTC/USDT", candle_close + BENCH_MS, EQUITY).allowed
    # wherever inside the exit candle the stop really filled, >= 24h of real time is benched
    for fill in (last_open, last_open + HOUR_MS, candle_close - 1):
        assert bench.until_ts - fill >= BENCH_MS
        assert all(bench.covers(ts) for ts in range(candle_close, fill + BENCH_MS, HOUR_MS))
    # on the 4h decision grid: six denied closes (t+4h .. t+24h), allowed again at t+28h
    grid = [candle_close + k * TF for k in range(8)]
    allowed = [cb.can_enter("BTC/USDT", ts, EQUITY).allowed for ts in grid]
    assert allowed == [False] * 6 + [True] * 2


def test_live_default_on_a_backtest_journal_would_bench_only_20h_from_the_candle_close():
    # Why A2 exists: without the offset, a backtest bench starts at the exit candle OPEN.
    trades, last_open = three_sl_candles()
    cb = CircuitBreakers(StrategyConfig())
    feed(cb, trades)
    bench = cb.active_bench("BTC/USDT", last_open)
    assert bench is not None and bench.until_ts - (last_open + TF) == BENCH_MS - TF
    assert not cb.can_enter("BTC/USDT", last_open, EQUITY).allowed  # look-ahead into the candle


def test_backtest_convention_loss_window_counts_from_the_candle_close():
    cb = CircuitBreakers(StrategyConfig(), TF)
    feed(cb, [closed(1, "BTC/USDT", T0, EXIT_END, pnl=-500.0)])
    certain = T0 + TF
    assert cb.window_pnl(T0) == (0.0, ())
    assert cb.can_enter("ETH/USDT", T0, EQUITY).allowed
    assert cb.can_enter("ETH/USDT", certain - 1, EQUITY).allowed
    halted = cb.can_enter("ETH/USDT", certain, EQUITY)
    assert not halted.allowed and one_sentence(halted.reason)
    assert "recorded time + 4h" in halted.reason and "#1" in halted.reason
    assert cb.window_pnl(certain) == (-500.0, (1,))
    assert not cb.can_enter("BNB/USDT", certain + WINDOW_MS - 1, EQUITY).allowed
    assert cb.can_enter("BNB/USDT", certain + WINDOW_MS, EQUITY).allowed
    assert cb.halt_lifts_at(certain, EQUITY) == certain + WINDOW_MS


def test_default_uncertainty_is_zero_and_unchanged_for_live_journals():
    for fn in (CircuitBreakers, CircuitBreakers.from_journal):
        param = inspect.signature(fn).parameters["exit_time_uncertainty_ms"]
        assert param.default == 0
    trades = scenario()
    cfg = StrategyConfig()
    default, explicit = CircuitBreakers.from_journal(trades, cfg), CircuitBreakers(cfg, 0)
    feed(explicit, sorted(trades, key=lambda t: (t.exit_ts, t.trade_id)))
    assert default.exit_time_uncertainty_ms == 0
    for ts in probe_times(trades):
        assert decisions(default, ts) == decisions(explicit, ts)  # identical reasons too
        assert all("can have filled" not in d.reason for d in decisions(default, ts))
        assert all("recorded time +" not in d.reason for d in decisions(default, ts))


def test_uncertainty_shifts_every_decision_by_exactly_the_offset():
    trades = scenario()
    cfg = StrategyConfig()
    live = CircuitBreakers.from_journal(trades, cfg)
    backtest = CircuitBreakers.from_journal(trades, cfg, exit_time_uncertainty_ms=TF)
    probes = probe_times(trades)
    blocked = 0
    for ts in probes:
        expected = [d.allowed for d in decisions(live, ts)]
        assert [d.allowed for d in decisions(backtest, ts + TF)] == expected
        assert backtest.window_pnl(ts + TF) == live.window_pnl(ts)
        blocked += expected.count(False)
    assert blocked > 0  # the scenario really exercises both breakers
    for pair in PAIRS:
        assert backtest.streak(pair) == live.streak(pair)


def test_from_journal_with_uncertainty_reproduces_the_incremental_state(tmp_path):
    trades = scenario()
    cfg = StrategyConfig()
    incremental = CircuitBreakers(cfg, TF)
    log: list[tuple[int, list]] = []
    i = 0
    for ts in probe_times(trades):  # a trade is fed once it is CERTAIN (t_e <= ts)
        while i < len(trades) and trades[i].exit_ts + TF <= ts:
            incremental.on_trade_closed(trades[i])
            i += 1
        log.append((ts, decisions(incremental, ts)))
    path = tmp_path / "backtest_trades.csv"
    write_journal(trades, path)
    replay = CircuitBreakers.from_journal(read_journal(path), cfg, TF)
    for ts, expected in log:
        assert decisions(replay, ts) == expected


@pytest.mark.parametrize("bad", [-1, True, 1.5, "4h", None])
def test_invalid_uncertainty_is_rejected(bad):
    with pytest.raises(ValueError, match="exit_time_uncertainty_ms"):
        CircuitBreakers(StrategyConfig(), bad)
    with pytest.raises(ValueError, match="exit_time_uncertainty_ms"):
        CircuitBreakers.from_journal([], StrategyConfig(), bad)


def test_effective_exit_ts_helper():
    t = closed(1, "BTC/USDT", T0)
    assert circuit_breakers.effective_exit_ts(t) == T0
    assert circuit_breakers.effective_exit_ts(t, TF) == T0 + TF
    t.exit_ts = None
    assert circuit_breakers.effective_exit_ts(t, TF) is None
