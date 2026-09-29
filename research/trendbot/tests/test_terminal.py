"""The dashboard terminal: plain-word commands, earnings, confirmations, arming a pair."""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from research.trendbot.adoption_evidence import write_decisions_log
from research.trendbot.assistant import Terminal, detect_period
from research.trendbot.dashboard import FleetRuntime, make_handler
from research.trendbot.gatekeeper import DecisionRecord
from research.trendbot.journal import read_journal, write_journal
from research.trendbot.live_bot import armed_pairs, read_control, write_control
from research.trendbot.models import HOUR_MS, Trade

from .test_live_bot import FakeExchange, _bot, _settings, _start_ts, world  # noqa: F401


NOW = 1_800_000_000_000 // (4 * HOUR_MS) * (4 * HOUR_MS) + HOUR_MS  # 1h after a 4H close
DAY = 86_400_000


def _t(tid, pair, pnl, exit_ts, closed=True):
    r = pnl / 5.0
    return Trade(
        tid, pair, "base", exit_ts - 12 * HOUR_MS, exit_ts - 8 * HOUR_MS, 100.0, 95.0, 110.0,
        1.0, 5.0, 1.0, "pivot", exit_ts if closed else None, 100 + pnl if closed else None,
        ("TP" if pnl > 0 else "SL") if closed else None, 0.2, pnl if closed else None,
        r if closed else None, {"vol_ratio": 2.0, "rsi": 60.0, "ema_gap_pct": 0.5,
                                "dist_regime_pct": 3.0, "hour_utc": 4.0},
    )  # fmt: skip


class FakeCtrl:
    def __init__(self, d):
        self.dir = d
        self.running = False
        self.log = []

    def bot_status(self):
        return {"running": self.running, "can_start": True, "pid": 1, "heartbeat_age_s": 1}

    def act(self, req):
        self.log.append(req)
        a = req["action"]
        if a == "start":
            self.running = True
        elif a == "stop":
            self.running = False
        elif a in ("pause", "resume"):
            write_control(self.dir, entries_paused=a == "pause")
        return True, f"{a} done"


@pytest.fixture
def term(tmp_path):
    d = tmp_path / "state"
    trades = [
        _t(1, "BTC/USDT", 10.0, NOW - 40 * DAY),  # old
        _t(2, "ETH/USDT", -5.0, NOW - 3 * DAY),  # this week
        _t(3, "BTC/USDT", 10.0, NOW - 30 * 60_000),  # today
        _t(4, "BNB/USDT", -5.0, NOW - 20 * 60_000),  # today
        _t(5, "ETH/USDT", 0.0, NOW, closed=False),  # open
    ]
    write_journal(trades, d / "journal.csv")
    (d / "state.json").write_text(json.dumps({"mode": "paper", "exchange": "binance",
                                              "starting_equity": 10_000}))  # fmt: skip
    write_decisions_log(
        [DecisionRecord("BTC/USDT", NOW - 5 * HOUR_MS, False, "R2_momentum", "RSI 45 below 50")],
        d / "decisions.csv",
    )
    ctrl = FakeCtrl(d)
    rt = FleetRuntime(None, {"bot": (d, None, ctrl)})
    return Terminal(rt, now_ms=lambda: NOW), ctrl, d


def test_help_and_pull_out_commands(term):
    t, _, _ = term
    for words in ("help", "pull out commands", "show me the commands", "what can you do?"):
        assert "start day / end day" in t.handle(words)["text"], words
    assert "didn't catch" in t.handle("sing me a song")["text"]


def test_earnings_by_period(term):
    t, _, _ = term
    today = t.handle("how much did I earn today?")
    assert "+5.00 USDT from 2 closed trades, 1 won" in today["text"]
    assert {r[0] for r in today["table"]["rows"]} == {"BTC/USDT", "BNB/USDT"}
    assert "+0.00 USDT from 3" in t.handle("profit this week")["text"]
    assert "+10.00 USDT from 4" in t.handle("total earnings")["text"]
    assert detect_period("pnl for the month") == "month" and detect_period("earnings") == "all"


def test_status_positions_trades_and_why(term):
    t, _, _ = term
    assert (
        "stopped" in t.handle("status")["text"] and "1 open position" in t.handle("status")["text"]
    )
    assert t.handle("positions")["table"]["rows"][0][0] == "ETH/USDT"
    assert t.handle("last trades")["table"]["rows"][0][0] == "#4"
    why = t.handle("why no trade on bitcoin?")
    assert (
        why["table"]["rows"][0][3] == "R2_momentum"
        and "Next 4H close: 12:00 UTC (in 3h 00m)" in why["text"]
    )


def test_start_day_and_end_day(term):
    t, ctrl, d = term
    r = t.handle("start the day")
    assert ctrl.running and "Good morning" in r["text"]
    assert read_control(d)["day_started_ms"] == NOW and not read_control(d)["entries_paused"]
    write_control(d, day_started_ms=NOW - HOUR_MS)  # the day began an hour ago
    r = t.handle("end day")
    assert read_control(d)["entries_paused"] and "Day ended" in r["text"]
    assert "2 closed trades" in r["text"] and read_control(d)["day_started_ms"] is None
    r = t.handle("end the day and close all positions")
    assert "confirm" in r and not any(x["action"] == "close" for x in ctrl.log)
    t.handle("yes")
    assert any(x["action"] == "close" and x["pair"] == "ALL" for x in ctrl.log)


def test_risky_actions_wait_for_yes(term):
    t, ctrl, _ = term
    r = t.handle("close eth")
    assert r["confirm"] and "Market-sell the ETH/USDT position" in r["text"]
    assert t.handle("no")["text"].startswith("Cancelled")
    assert not any(x["action"] == "close" for x in ctrl.log)
    t.handle("sell ETH")
    t.handle("yes")
    assert ctrl.log[-1] == {"action": "close", "pair": "ETH/USDT", "bot": "bot"}
    assert "no open BTC/USDT position" in t.handle("close btc")["text"]
    assert "nothing waiting" in t.handle("yes")["text"]
    ctrl.running = True
    assert t.handle("stop the bot")["confirm"] and ctrl.running
    t.handle("yes")
    assert not ctrl.running


def test_execute_trade_arms_only_when_entries_are_paused(term):
    t, _, d = term
    r = t.handle("execute trade BTC")
    assert "Entries are open" in r["text"] and "R2_momentum" in r["text"]
    write_control(d, entries_paused=True)
    r = t.handle("buy bitcoin")
    assert r["confirm"] and "Arm BTC/USDT" in r["text"]
    assert armed_pairs(d, NOW) == {}
    t.handle("yes")
    assert list(armed_pairs(d, NOW)) == ["BTC/USDT"]
    assert armed_pairs(d, NOW + 25 * HOUR_MS) == {}  # expires after 24h
    assert "already holds ETH/USDT" in t.handle("execute trade eth")["text"]
    assert "does not trade DOGE/USDT" in t.handle("execute trade doge")["text"]
    assert "Which pair?" in t.handle("execute a trade")["text"]
    t.handle("end day")
    assert armed_pairs(d, NOW) == {}  # ending the day disarms


def test_terminal_http_endpoint_needs_the_token(term):
    t, _, _ = term
    s = ThreadingHTTPServer(
        ("127.0.0.1", 0), make_handler(None, None, 15, token="tok", runtime=t.rt)
    )
    threading.Thread(target=s.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{s.server_address[1]}/api/terminal"

    def post(text, tok="tok"):
        req = urllib.request.Request(url, method="POST", data=json.dumps({"text": text}).encode(),
                                     headers={"X-Trendbot-Token": tok})  # fmt: skip
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    try:
        assert post("status", tok="bad")[0] == 403
        code, body = post("help")
        assert code == 200 and "execute trade BTC" in body["text"]
    finally:
        s.shutdown()


def test_bot_takes_an_armed_pair_while_entries_are_paused(tmp_path, world):  # noqa: F811
    data, _ = world
    s = _settings(tmp_path, layers={"auto_jobs": False})
    ex = FakeExchange(data, _start_ts(data))
    bot = _bot(s, ex)
    bot.start()
    write_control(bot.dir, entries_paused=True, armed_pairs={"ETH/USDT": 10**15})
    end = data["BTC/USDT"][-2].ts
    while ex.now < end:
        bot.step()
        ex.advance(bot.s.poll_seconds)
    trades = read_journal(bot.journal_path)
    assert len(trades) == 1 and trades[0].pair == "ETH/USDT"  # one armed trade, then disarmed
    assert "ETH/USDT" not in (read_control(bot.dir).get("armed_pairs") or {})
