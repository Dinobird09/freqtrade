"""Bot health scorecard: P&L tables, CAGR, uptime / availability, turnover, matched orders,
maker share, fees, volatility categories, benchmark statuses and "not enough data yet"."""

import json
import statistics
from datetime import UTC, datetime
from itertools import pairwise

import pytest

from research.trendbot import scorecard as sc
from research.trendbot.journal import write_journal
from research.trendbot.models import DAY_MS, HOUR_MS, Trade


def ms(y, m, d, h=0):
    return int(datetime(y, m, d, h, tzinfo=UTC).timestamp() * 1000)


NOW = ms(2026, 3, 15)
START = ms(2026, 1, 1)


def _trade(i, exit_ts, pnl):
    return Trade(
        trade_id=i,
        pair="BTC/USDT",
        variant="base",
        signal_ts=exit_ts - 3 * DAY_MS,
        entry_ts=exit_ts - 2 * DAY_MS,
        entry_price=100.0,
        stop=95.0,
        target=110.0,
        qty=10.0,
        risk_amount=50.0,
        risk_pct=0.5,
        exit_ts=exit_ts,
        exit_price=100.0 + pnl / 10,
        exit_reason="TP" if pnl > 0 else "SL",
        pnl=pnl,
        r_multiple=pnl / 50.0,
    )


def _order(ts, status, notional, fee, liquidity, kind="market"):
    qty = notional / 100.0
    return {
        "ts": ts,
        "pair": "BTC/USDT",
        "side": "buy",
        "type": kind,
        "qty_requested": qty or 1.0,
        "qty_filled": qty,
        "price": 100.0 if qty else None,
        "notional": notional,
        "fee_quote": fee,
        "liquidity": liquidity,
        "status": status,
        "order_id": str(ts),
    }


@pytest.fixture
def bot_dir(tmp_path):
    d = tmp_path / "bot"
    d.mkdir()
    (d / "state.json").write_text(json.dumps({"starting_equity": 10_000.0, "mode": "paper"}))
    trades = [
        _trade(1, ms(2026, 1, 10, 12), 200.0),
        _trade(2, ms(2026, 1, 20, 12), -100.0),
        _trade(3, ms(2026, 2, 5, 12), 300.0),
        _trade(4, ms(2026, 3, 10, 12), 100.0),
    ]
    write_journal(trades, d / "journal.csv")
    orders = [
        _order(ms(2026, 3, 14, 12), "filled", 5000.0, 5.0, "taker"),
        _order(ms(2026, 3, 14, 13), "cancelled", 0.0, 0.0, "maker", "post-only"),
        _order(ms(2026, 3, 10, 12), "filled", 5100.0, 5.1, "taker"),
        _order(ms(2026, 3, 1, 12), "filled", 4000.0, 0.8, "maker", "post-only"),
        _order(ms(2026, 2, 1, 12), "filled", 3000.0, 3.0, "taker"),  # outside 30 days
        _order(ms(2026, 3, 12, 12), "rejected", 0.0, 0.0, "unknown"),
    ]
    sc.append_jsonl(d / sc.ORDERS_FILE, orders)
    # up from Jan 1 to Mar 1, down for one day, up again until now (poll 1 s)
    uptime = {
        "sessions": [[START, ms(2026, 3, 1)], [ms(2026, 3, 2), NOW - 10_000]],
        "poll_seconds": 1.0,
        "gap_ms": sc.gap_ms_for(1.0),
    }
    (d / sc.UPTIME_FILE).write_text(json.dumps(uptime))
    return d


def _m(card, key):
    (m,) = [m for m in card["metrics"] if m["key"] == key]
    return m


def test_monthly_and_annual_pnl_and_cagr(bot_dir):
    card = sc.scorecard(bot_dir, now_ms=NOW)
    assert card["equity"] == 10_500.0 and card["days_live"] == pytest.approx(73.0)
    jan, feb, mar = card["monthly"]
    assert (jan["month"], jan["pnl"], jan["return_pct"]) == ("2026-01", 100.0, 1.0)
    assert feb["pnl"] == 300.0 and feb["return_pct"] == pytest.approx(300 / 10_100 * 100, abs=1e-3)
    assert mar["pnl"] == 100.0 and mar["return_pct"] == pytest.approx(100 / 10_400 * 100, abs=1e-3)
    assert mar["current"] and not feb["current"] and card["current_month"]["month"] == "2026-03"
    (y,) = card["annual"]
    assert y == {"year": 2026, "pnl": 500.0, "return_pct": 5.0, "partial": True}
    cagr = (1.05 ** (365.25 / 73) - 1) * 100
    assert card["cagr_pct"] == pytest.approx(cagr, abs=1e-3)
    assert _m(card, "pnl_annual")["status"] == "good"  # ~27.6% a year: inside 5-200%
    monthly = (1.05 ** ((365.25 / 12) / 73) - 1) * 100
    assert _m(card, "pnl_monthly")["value"] == pytest.approx(monthly, abs=1e-3)
    # a mark-to-market equity replaces the realized one
    assert sc.scorecard(bot_dir, now_ms=NOW, equity=9_000.0)["cagr_pct"] < 0


def test_uptime_and_availability(bot_dir):
    card = sc.scorecard(bot_dir, now_ms=NOW)
    up = card["uptime"]
    assert up["sessions"] == 2 and up["running_days"] == pytest.approx(72.0, abs=1e-3)
    assert up["availability_7d"] == pytest.approx(100.0)
    assert up["availability_30d"] == pytest.approx(29 / 30 * 100, abs=0.01)  # one day down
    assert _m(card, "availability_7d")["status"] == "good"
    assert _m(card, "availability_30d")["status"] == "warn"
    u = _m(card, "uptime")
    assert u["status"] == "warn" and "3-month minimum" in u["comment"]  # 73 days < 3 months
    assert sc.scorecard(bot_dir, now_ms=START + 400 * DAY_MS)["metrics"]  # 12+ months: still ok
    late = sc.scorecard(bot_dir, now_ms=START + 400 * DAY_MS)
    assert "excellent" in _m(late, "uptime")["comment"]
    assert _m(late, "availability_7d")["status"] == "bad"  # stopped for weeks since


def test_turnover_matched_makers_and_fees(bot_dir):
    card = sc.scorecard(bot_dir, now_ms=NOW, equity=10_500.0)
    t24 = _m(card, "turnover_24h")
    assert t24["value"] == pytest.approx(5000 / 10_500, abs=1e-4)
    assert t24["status"] == "warn" and "trend bot" in t24["comment"]  # not "broken"
    avg = (25 * 10_400 + 6 * 10_500) / 31  # daily equity Feb 13 .. Mar 15
    t30 = _m(card, "turnover_30d")
    assert t30["value"] == pytest.approx((5000 + 5100 + 4000) / avg, abs=1e-4)
    assert t30["status"] == "warn"
    matched = _m(card, "matched")
    assert matched["value"] == pytest.approx(4 / 6 * 100, abs=1e-3) and matched["status"] == "good"
    maker = _m(card, "maker")
    assert maker["value"] == pytest.approx(4000 / 17_100 * 100, abs=1e-3)
    assert maker["status"] == "bad" and "maker_first" in maker["comment"]
    fees = _m(card, "fees")
    assert fees["value"] == pytest.approx(13.9 / 17_100 * 100, abs=1e-4)
    assert fees["status"] == "good"  # 0.081% < 0.1% per trade
    assert card["orders"] == {"placed": 6, "filled": 4, "notional": 17_100.0, "fees": 13.9}
    # a market-making bot is held to the benchmark
    mm = sc.scorecard(bot_dir, now_ms=NOW, equity=10_500.0, style="market_making")
    assert _m(mm, "turnover_24h")["status"] == "bad"


def test_volatility_categories():
    assert sc.volatility_category(None) is None
    assert sc.volatility_category(0.4) == "low-volatility"
    assert sc.volatility_category(2.0) == "balanced"
    assert sc.volatility_category(4.5) == "high-volatility"


def test_categories_and_benchmark_statuses(bot_dir, tmp_path):
    card = sc.scorecard(bot_dir, now_ms=NOW)
    assert card["category"] == "low-volatility" and card["daily_vol_pct"] < 1.0
    closed = sc.read_journal(bot_dir / "journal.csv")
    curve = [e for _, e in sc.daily_equity(closed, 10_000.0, START, NOW)]
    rets = [b / a - 1 for a, b in pairwise(curve)]
    assert card["daily_vol_pct"] == pytest.approx(statistics.stdev(rets) * 100, abs=1e-4)
    assert _m(card, "pnl_monthly")["status"] == "good"  # ~2%/month for a low-volatility bot
    assert card["counts"] == {"good": 6, "warn": 4, "bad": 1, "nodata": 0}
    assert card["summary"].startswith("6 good · 4 to watch · 1 bad")

    # +50% in 40 days: high-volatility, and > 200% a year is "unrealistic or excessive risk"
    d = tmp_path / "wild"
    d.mkdir()
    (d / "state.json").write_text(json.dumps({"starting_equity": 10_000.0}))
    write_journal([_trade(1, START + 20 * DAY_MS + HOUR_MS, 5000.0)], d / "journal.csv")
    (d / sc.UPTIME_FILE).write_text(json.dumps({"sessions": [[START, START + 40 * DAY_MS]]}))
    wild = sc.scorecard(d, now_ms=START + 40 * DAY_MS)
    assert wild["category"] == "high-volatility"
    a = _m(wild, "pnl_annual")
    assert a["status"] == "warn" and "excessive risk" in a["comment"]
    assert _m(wild, "pnl_monthly")["status"] == "warn"  # far above 10-15% a month

    # a loss is bad; under 5% a year is a warning
    write_journal([_trade(1, START + 20 * DAY_MS, -500.0)], d / "journal.csv")
    assert _m(sc.scorecard(d, now_ms=START + 40 * DAY_MS), "pnl_annual")["status"] == "bad"
    write_journal([_trade(1, START + 20 * DAY_MS, 20.0)], d / "journal.csv")
    assert _m(sc.scorecard(d, now_ms=START + 300 * DAY_MS), "pnl_annual")["status"] == "warn"


def test_too_little_data_is_never_a_pass(tmp_path):
    empty = sc.scorecard(tmp_path, now_ms=NOW)
    assert empty["monthly"] == [] and empty["category"] is None
    assert {m["status"] for m in empty["metrics"]} == {"nodata"}
    assert all(m["comment"].startswith("not enough data yet") for m in empty["metrics"])

    d = tmp_path / "young"
    d.mkdir()
    (d / "state.json").write_text(json.dumps({"starting_equity": 1000.0}))
    (d / sc.UPTIME_FILE).write_text(json.dumps({"sessions": [[NOW - 3 * DAY_MS, NOW]]}))
    sc.append_jsonl(d / sc.ORDERS_FILE, [_order(NOW - HOUR_MS, "filled", 500.0, 0.5, "taker")])
    young = sc.scorecard(d, now_ms=NOW)
    status = {m["key"]: m["status"] for m in young["metrics"]}
    for key in (
        "volatility",
        "pnl_annual",
        "pnl_monthly",
        "availability_30d",
        "turnover_30d",
        "matched",
        "maker",
        "fees",
    ):
        assert status[key] == "nodata", key
    assert status["availability_7d"] == "good" and status["turnover_24h"] == "warn"
    assert young["counts"]["nodata"] == 8 and "not enough data yet" in young["summary"]


def test_availability_counts_quick_restarts_and_gaps():
    gap = sc.gap_ms_for(10.0)  # 3 x 10 s + 60 s
    assert gap == 90_000
    quick = [[0, 100_000], [100_000 + gap - 1, 200_000]]
    assert sc.availability(quick, gap, 200_000, 7 * DAY_MS) == pytest.approx(100.0)
    slow = [[0, 100_000], [100_000 + gap + 1, 200_000]]
    assert sc.availability(slow, gap, 200_000, 7 * DAY_MS) == pytest.approx(
        (200_000 - gap - 1) / 200_000 * 100
    )
    # stopped since: the time after the last session is downtime
    assert sc.availability([[0, 100_000]], gap, 400_000, 7 * DAY_MS) == pytest.approx(25.0)
    assert sc.availability([], gap, 1, DAY_MS) is None


def test_uptime_tracker_sessions_and_gaps(tmp_path):
    t = sc.UptimeTracker(tmp_path, poll_seconds=10.0)
    t.beat(0)
    read = lambda: sc.read_uptime(tmp_path)["sessions"]  # noqa: E731
    assert read() == [[0, 0]]
    t.beat(10_000)
    assert read() == [[0, 0]]  # rewritten at most every 30 s
    t.beat(40_000)
    assert read() == [[0, 40_000]]
    t.beat(200_000)  # 160 s of silence > 90 s: a new session
    assert read() == [[0, 40_000], [200_000, 200_000]]
    t.stop(210_000)
    assert read() == [[0, 40_000], [200_000, 210_000]]
    restarted = sc.UptimeTracker(tmp_path, poll_seconds=10.0)  # a new process
    restarted.beat(215_000)
    assert read()[-1] == [215_000, 215_000] and len(read()) == 3
    raw = json.loads((tmp_path / sc.UPTIME_FILE).read_text())
    assert raw["gap_ms"] == 90_000 and raw["poll_seconds"] == 10.0
    up = sc.availability(read(), 90_000, 215_000, 7 * DAY_MS)
    assert up == pytest.approx((40_000 + 15_000) / 215_000 * 100)  # the restart is continuous


def test_fleet_scorecard_diversification(bot_dir):
    card = sc.scorecard(bot_dir, now_ms=NOW)
    same = sc.fleet_scorecard({"a": card, "b": card})
    assert same["categories"]["low-volatility"] == 2
    assert same["line"].startswith("2 low-volatility, 0 balanced, 0 high-volatility")
    assert "spread bots across categories" in same["hint"]
    wild = {**card, "category": "high-volatility"}
    mixed = sc.fleet_scorecard({"a": card, "b": wild, "c": sc.scorecard(bot_dir / "none")})
    assert mixed["hint"] is None and mixed["categories"]["unknown"] == 1
    assert "1 not categorised yet" in mixed["line"]
    assert [b["name"] for b in mixed["bots"]] == ["a", "b", "c"]


def test_append_jsonl_skips_a_locked_file(tmp_path, monkeypatch):
    from pathlib import Path

    p = tmp_path / "orders.jsonl"
    real_open = Path.open
    calls = []

    def locked(self, *a, **kw):
        if self == p:
            calls.append(1)
            raise PermissionError(13, "locked")
        return real_open(self, *a, **kw)

    monkeypatch.setattr(Path, "open", locked)
    assert sc.append_jsonl(p, [{"a": 1}], sleep=lambda s: None) is False
    assert len(calls) == 5
    monkeypatch.setattr(Path, "open", real_open)

    class Gw:
        order_log = [{"a": 1}, {"a": 2}]

    gw = Gw()
    monkeypatch.setattr(sc, "append_jsonl", lambda *a, **k: False)
    assert sc.drain_order_log(gw, tmp_path) == 0 and len(gw.order_log) == 2  # kept for later
    monkeypatch.undo()
    assert sc.drain_order_log(gw, tmp_path) == 2 and gw.order_log == []
    assert sc.read_jsonl(p) == [{"a": 1}, {"a": 2}]
    p.write_text(p.read_text() + "{torn\n")
    assert len(sc.read_jsonl(p)) == 2
