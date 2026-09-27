from pathlib import Path

import pytest

from research.trendbot.config import RULE_IDS, StrategyConfig
from research.trendbot.journal import ms_to_iso, write_journal
from research.trendbot.journal_rules import (
    GUARD_ADOPTION_NOTE,
    Adaptation,
    audit,
    main,
    render_markdown,
    risk_multiplier,
)
from research.trendbot.models import DAY_MS, EXIT_END, EXIT_SL, EXIT_TP, HOUR_MS, Trade


T0 = 1_704_067_200_000  # 2024-01-01T00:00:00Z
EQUITY = 10_000.0
GUARD_ON = StrategyConfig(expectancy_guard=True)


def trade(
    trade_id: int,
    pair: str,
    exit_ts: int | None,
    reason: str = EXIT_SL,
    pnl: float = -10.0,
    r: float | None = None,
) -> Trade:
    """A closed trade (risk 10 quote, so r = pnl / 10 unless given), or an open one."""
    t = Trade(
        trade_id=trade_id,
        pair=pair,
        variant="base",
        signal_ts=(exit_ts or T0) - 8 * HOUR_MS,
        entry_ts=(exit_ts or T0) - 4 * HOUR_MS,
        entry_price=100.0,
        stop=95.0,
        target=110.0,
        qty=2.0,
        risk_amount=10.0,
        risk_pct=0.1,
    )
    if exit_ts is not None:
        t.exit_ts, t.exit_price, t.exit_reason = exit_ts, 100.0, reason
        t.pnl, t.r_multiple = pnl, pnl / 10.0 if r is None else r
    return t


def spaced_history(pair: str, rs: list[float], start_id: int = 1) -> list[Trade]:
    """Closed trades 8 days apart, alternating SL/END so no streak, bench or halt is active."""
    out = []
    for k, r in enumerate(rs):
        reason = EXIT_SL if k % 2 == 0 else EXIT_END
        out.append(trade(start_id + k, pair, T0 + k * 8 * DAY_MS, reason, pnl=10.0 * r, r=r))
    return out


def assert_one_sentence(text: str) -> None:
    assert text.endswith("."), text
    assert ". " not in text, text
    assert "\n" not in text and "? " not in text and "! " not in text, text


def three_sl_btc() -> list[Trade]:
    return [trade(i, "BTC/USDT", T0 + i * 4 * HOUR_MS) for i in (1, 2, 3)]


# ---------------------------------------------------------------------------- R9 via audit
def test_empty_journal_has_no_adaptations():
    assert audit([], StrategyConfig(), T0, EQUITY) == []
    assert audit([], GUARD_ON, T0, EQUITY) == []


def test_audit_reports_bench_with_evidence():
    trades = [*three_sl_btc(), trade(4, "ETH/USDT", T0 + HOUR_MS, EXIT_TP, pnl=30.0)]
    now = T0 + 13 * HOUR_MS
    found = audit(trades, StrategyConfig(), now, EQUITY)
    assert len(found) == 1
    a = found[0]
    assert (a.rule, a.scope) == ("R9_circuit_breaker", "BTC/USDT")
    until = ms_to_iso(T0 + 12 * HOUR_MS + 24 * HOUR_MS)
    assert a.action == f"no entries until {until}"
    assert a.evidence_trade_ids == (1, 2, 3)
    assert_one_sentence(a.explanation)
    assert until in a.explanation and "#1, #2, #3" in a.explanation


def test_bench_expires_and_trades_after_now_are_ignored():
    trades = three_sl_btc()
    bench_end = T0 + 36 * HOUR_MS
    assert audit(trades, StrategyConfig(), bench_end, EQUITY) == []
    assert len(audit(trades, StrategyConfig(), bench_end - 1, EQUITY)) == 1
    # auditing the past: the third stop-loss has not happened yet at now = its exit - 1
    assert audit(trades, StrategyConfig(), T0 + 12 * HOUR_MS - 1, EQUITY) == []


def test_audit_reports_weekly_halt_scope_all():
    trades = [
        trade(1, "BTC/USDT", T0, EXIT_END, pnl=-180.0),
        trade(2, "ETH/USDT", T0 + DAY_MS, EXIT_END, pnl=-90.0),
        trade(3, "BNB/USDT", T0 + 2 * DAY_MS, EXIT_SL, pnl=-60.0),
        trade(4, "BTC/USDT", T0 - 8 * DAY_MS, EXIT_END, pnl=-5_000.0),  # outside the window
        trade(5, "ETH/USDT", None),  # still open: never evidence
    ]
    now = T0 + 2 * DAY_MS
    found = audit(trades, StrategyConfig(), now, EQUITY)
    assert len(found) == 1
    a = found[0]
    assert (a.rule, a.scope, a.action) == ("R9_circuit_breaker", "ALL", "halt all entries")
    assert a.evidence_trade_ids == (1, 2, 3)
    assert_one_sentence(a.explanation)
    # -330 < -300; dropping #1 (ages out at T0 + 7d) leaves -150 >= -300
    assert "-330.00" in a.explanation and "-300.00" in a.explanation
    assert f"lifts at {ms_to_iso(T0 + 7 * DAY_MS)}" in a.explanation
    assert audit(trades, StrategyConfig(), T0 + 7 * DAY_MS, EQUITY) == []
    assert audit(trades, StrategyConfig(), now, 20_000.0) == []  # limit scales with equity


# ---------------------------------------------------------------------------- expectancy guard
def test_guard_is_off_by_default():
    losers = spaced_history("ETH/USDT", [-0.5] * 25)
    now = T0 + 400 * DAY_MS
    assert StrategyConfig().expectancy_guard is False
    assert audit(losers, StrategyConfig(), now, EQUITY) == []
    assert risk_multiplier(losers, "ETH/USDT", StrategyConfig()) == 1.0


def test_guard_triggers_on_negative_last_window_expectancy():
    losers = spaced_history("ETH/USDT", [1.0] * 5 + [-0.5] * 20)
    winners = spaced_history("BTC/USDT", [0.5] * 20, start_id=100)
    trades = losers + winners
    now = T0 + 400 * DAY_MS
    found = audit(trades, GUARD_ON, now, EQUITY)
    assert len(found) == 1
    a = found[0]
    assert (a.rule, a.scope, a.action) == ("L_expectancy_guard", "ETH/USDT", "risk x0.5")
    assert a.evidence_trade_ids == tuple(range(6, 26))  # exactly the last 20 closed trades
    assert_one_sentence(a.explanation)
    assert "-0.500R" in a.explanation and "last 20" in a.explanation
    assert risk_multiplier(trades, "ETH/USDT", GUARD_ON) == GUARD_ON.guard_risk_mult == 0.5
    assert risk_multiplier(trades, "BTC/USDT", GUARD_ON) == 1.0


def test_guard_uses_only_the_last_window():
    # 5 heavy early losers, then 20 small winners: last-20 mean > 0 -> not triggered
    history = spaced_history("BNB/USDT", [-3.0] * 5 + [0.1] * 20)
    assert risk_multiplier(history, "BNB/USDT", GUARD_ON) == 1.0
    assert audit(history, GUARD_ON, T0 + 400 * DAY_MS, EQUITY) == []


def test_guard_needs_a_full_window_and_a_strictly_negative_mean():
    assert risk_multiplier(spaced_history("ETH/USDT", [-1.0] * 19), "ETH/USDT", GUARD_ON) == 1.0
    flat = spaced_history("ETH/USDT", [1.0, -1.0] * 10)
    assert risk_multiplier(flat, "ETH/USDT", GUARD_ON) == 1.0  # mean exactly 0
    open_one = [*spaced_history("ETH/USDT", [-1.0] * 19), trade(50, "ETH/USDT", None)]
    assert risk_multiplier(open_one, "ETH/USDT", GUARD_ON) == 1.0  # open trades do not count


def test_guard_in_audit_respects_now():
    losers = spaced_history("ETH/USDT", [-0.5] * 20)
    last_exit = losers[-1].exit_ts
    assert len(audit(losers, GUARD_ON, last_exit, EQUITY)) == 1
    assert audit(losers, GUARD_ON, last_exit - 1, EQUITY) == []  # only 19 closed by then


# ---------------------------------------------------------------------------- all together
def everything_active() -> tuple[list[Trade], int]:
    history = spaced_history("ETH/USDT", [-0.4] * 20, start_id=10)
    now = history[-1].exit_ts + 10 * DAY_MS
    recent = [
        trade(101, "BTC/USDT", now - 3 * HOUR_MS, EXIT_SL, pnl=-100.0),
        trade(102, "BTC/USDT", now - 2 * HOUR_MS, EXIT_SL, pnl=-100.0),
        trade(103, "BTC/USDT", now - 1 * HOUR_MS, EXIT_SL, pnl=-150.0),
    ]
    return history + recent, now


def test_every_adaptation_is_one_sentence_with_real_evidence():
    trades, now = everything_active()
    found = audit(trades, GUARD_ON, now, EQUITY)
    assert [(a.rule, a.scope) for a in found] == [
        ("R9_circuit_breaker", "BTC/USDT"),
        ("R9_circuit_breaker", "ALL"),
        ("L_expectancy_guard", "ETH/USDT"),
    ]
    ids = {t.trade_id for t in trades}
    for a in found:
        assert isinstance(a, Adaptation) and a.rule in RULE_IDS
        assert_one_sentence(a.explanation)
        assert a.evidence_trade_ids and set(a.evidence_trade_ids) <= ids
    assert found[0].evidence_trade_ids == found[1].evidence_trade_ids == (101, 102, 103)
    assert [a.rule for a in audit(trades, StrategyConfig(), now, EQUITY)] == [
        "R9_circuit_breaker",
        "R9_circuit_breaker",
    ]


def test_render_markdown():
    trades, now = everything_active()
    table = render_markdown(audit(trades, GUARD_ON, now, EQUITY))
    lines = table.splitlines()
    assert lines[0] == "| rule | scope | action | explanation | evidence |"
    assert len(lines) == 2 + 3
    assert all(line.startswith("| ") and line.endswith(" |") for line in lines[2:])
    assert "#101, #102, #103" in lines[2]
    assert render_markdown([]) == "No active adaptations."


# ---------------------------------------------------------------------------- CLI
def test_cli_prints_table(tmp_path: Path, capsys):
    trades, now = everything_active()
    path = tmp_path / "trades.csv"
    write_journal(trades, path)
    rc = main(["--journal", str(path), "--equity", "10000", "--now", ms_to_iso(now)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "| rule | scope | action | explanation | evidence |" in out
    assert "| R9_circuit_breaker | BTC/USDT | no entries until" in out
    assert "| R9_circuit_breaker | ALL | halt all entries |" in out
    assert "L_expectancy_guard" not in out  # guard is off unless asked for
    assert ms_to_iso(now) in out

    argv = ["--journal", str(path), "--equity", "10000", "--now", ms_to_iso(now)]
    assert main([*argv, "--expectancy-guard"]) == 0
    out = capsys.readouterr().out
    assert "| L_expectancy_guard | ETH/USDT | risk x0.5 |" in out


def test_cli_expectancy_guard_prints_the_adoption_note(tmp_path: Path, capsys):
    path = tmp_path / "trades.csv"
    write_journal([], path)
    argv = ["--journal", str(path), "--equity", "10000", "--now", ms_to_iso(T0)]
    assert main(argv) == 0
    assert GUARD_ADOPTION_NOTE not in capsys.readouterr().out
    assert main([*argv, "--expectancy-guard"]) == 0
    out = capsys.readouterr().out
    assert (
        "L_expectancy_guard is an optional layer; it may only be enabled live through the "
        "adoption path with its own walk-forward evidence (run_research pre-registers "
        "base+guard)"
    ) in out
    assert out.splitlines()[1] == GUARD_ADOPTION_NOTE  # right under the journal header line


def test_cli_no_active_adaptations(tmp_path: Path, capsys):
    trades, now = everything_active()
    path = tmp_path / "trades.csv"
    write_journal(trades, path)
    later = ms_to_iso(now + 30 * DAY_MS)
    assert main(["--journal", str(path), "--equity", "10000", "--now", later]) == 0
    out = capsys.readouterr().out
    assert "No active adaptations." in out and "| rule |" not in out


def test_cli_errors(tmp_path: Path, capsys):
    assert main(["--journal", str(tmp_path / "missing.csv"), "--equity", "1"]) == 1
    assert "error:" in capsys.readouterr().err
    path = tmp_path / "trades.csv"
    write_journal([], path)
    with pytest.raises(SystemExit):
        main(["--journal", str(path), "--equity", "0"])
    with pytest.raises(SystemExit):
        main(["--journal", str(path), "--equity", "1", "--now", "yesterday"])


# ---------------------------------------------------------------------------- v2 A2 exit timing
TF = StrategyConfig().timeframe_ms  # backtest journals: exit_ts is the exit candle OPEN


def test_audit_backtest_journal_benches_24h_from_the_candle_close():
    trades = three_sl_btc()  # third stop-loss recorded at candle open T0 + 12h
    last_open = T0 + 12 * HOUR_MS
    close = last_open + TF
    cfg = StrategyConfig()
    # at the exit candle's open (and until it closes) the third stop-loss is not certain yet
    assert audit(trades, cfg, last_open, EQUITY, exit_time_uncertainty_ms=TF) == []
    assert audit(trades, cfg, close - 1, EQUITY, TF) == []
    (a,) = audit(trades, cfg, close, EQUITY, TF)
    until = ms_to_iso(close + 24 * HOUR_MS)
    assert (a.rule, a.scope, a.action) == (
        "R9_circuit_breaker",
        "BTC/USDT",
        f"no entries until {until}",
    )
    assert a.evidence_trade_ids == (1, 2, 3)
    assert_one_sentence(a.explanation)
    assert f"from {ms_to_iso(close)}" in a.explanation
    assert f"recorded at {ms_to_iso(last_open)} can have filled" in a.explanation
    assert len(audit(trades, cfg, close + 24 * HOUR_MS - 1, EQUITY, TF)) == 1
    assert audit(trades, cfg, close + 24 * HOUR_MS, EQUITY, TF) == []
    # the live default (real fill times) is unchanged: bench from the recorded exit_ts
    (live,) = audit(trades, cfg, last_open, EQUITY)
    assert live.action == f"no entries until {ms_to_iso(last_open + 24 * HOUR_MS)}"
    assert "can have filled" not in live.explanation


def test_audit_backtest_journal_halt_counts_losses_once_certain():
    trades = [
        trade(1, "BTC/USDT", T0, EXIT_END, pnl=-180.0),
        trade(2, "ETH/USDT", T0 + DAY_MS, EXIT_END, pnl=-90.0),
        trade(3, "BNB/USDT", T0 + 2 * DAY_MS, EXIT_SL, pnl=-60.0),
    ]
    at_open = T0 + 2 * DAY_MS
    assert len(audit(trades, StrategyConfig(), at_open, EQUITY)) == 1  # live convention
    assert audit(trades, StrategyConfig(), at_open, EQUITY, TF) == []  # #3 not certain yet
    (a,) = audit(trades, StrategyConfig(), at_open + TF, EQUITY, TF)
    assert (a.scope, a.evidence_trade_ids) == ("ALL", (1, 2, 3))
    assert_one_sentence(a.explanation)
    assert "exit_ts + 4h" in a.explanation
    assert f"lifts at {ms_to_iso(T0 + TF + 7 * DAY_MS)}" in a.explanation


def test_guard_backtest_journal_uses_only_certain_exits():
    losers = spaced_history("ETH/USDT", [-0.5] * 20)
    last_exit = losers[-1].exit_ts
    assert len(audit(losers, GUARD_ON, last_exit, EQUITY)) == 1
    assert audit(losers, GUARD_ON, last_exit, EQUITY, TF) == []  # only 19 certain by then
    (a,) = audit(losers, GUARD_ON, last_exit + TF, EQUITY, TF)
    assert a.rule == "L_expectancy_guard" and a.evidence_trade_ids == tuple(range(1, 21))
    # risk_multiplier: the same t_e <= decision_ts rule when a decision time is given
    assert risk_multiplier(losers, "ETH/USDT", GUARD_ON, TF, decision_ts=last_exit) == 1.0
    assert risk_multiplier(losers, "ETH/USDT", GUARD_ON, TF, decision_ts=last_exit + TF) == 0.5
    assert risk_multiplier(losers, "ETH/USDT", GUARD_ON, 0, decision_ts=last_exit) == 0.5
    assert risk_multiplier(losers, "ETH/USDT", GUARD_ON, 0, decision_ts=last_exit - 1) == 1.0
    # without a decision time the caller pre-filters, exactly as before (default unchanged)
    assert risk_multiplier(losers, "ETH/USDT", GUARD_ON) == 0.5
    assert risk_multiplier(losers, "ETH/USDT", GUARD_ON, TF) == 0.5


@pytest.mark.parametrize("bad", [-1, True, 2.5])
def test_invalid_uncertainty_is_rejected(bad):
    with pytest.raises(ValueError, match="exit_time_uncertainty_ms"):
        audit([], StrategyConfig(), T0, EQUITY, bad)
    with pytest.raises(ValueError, match="exit_time_uncertainty_ms"):
        risk_multiplier([], "BTC/USDT", GUARD_ON, bad)


def test_cli_backtest_journal_flag(tmp_path: Path, capsys):
    path = tmp_path / "trades.csv"
    write_journal(three_sl_btc(), path)
    last_open = T0 + 12 * HOUR_MS
    argv = ["--journal", str(path), "--equity", "10000", "--now", ms_to_iso(last_open)]
    assert main(argv) == 0  # live journal: the recorded exit time is the fill time
    out = capsys.readouterr().out
    assert "| R9_circuit_breaker | BTC/USDT | no entries until" in out
    assert "live journal" in out
    assert main([*argv, "--backtest-journal"]) == 0  # exit candle still open: not certain
    out = capsys.readouterr().out
    assert "No active adaptations." in out and "backtest journal" in out and "+ 4h" in out
    argv[-1] = ms_to_iso(last_open + TF)
    assert main([*argv, "--backtest-journal"]) == 0
    out = capsys.readouterr().out
    until = ms_to_iso(last_open + TF + 24 * HOUR_MS)
    assert f"| R9_circuit_breaker | BTC/USDT | no entries until {until} |" in out


def test_cli_help_explains_the_backtest_convention(capsys):
    with pytest.raises(SystemExit) as info:
        main(["--help"])
    assert info.value.code == 0
    text = " ".join(capsys.readouterr().out.split())
    assert "--backtest-journal" in text
    assert "exit_ts is the OPEN of the exit candle" in text
    assert "candle CLOSE" in text and "(4h)" in text and "live journal" in text
