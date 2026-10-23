"""Dashboard controls: real-money confirmation data, requests only for a running bot, errors
as JSON, crashed bots, one Start at a time, and the terminal's replies about them."""

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from research.trendbot import dashboard as dash
from research.trendbot import launch
from research.trendbot.assistant import HELP, Terminal
from research.trendbot.dashboard import Controller, FleetRuntime, make_handler


def _beat(d, pid=None, age_s=0.0, status="running"):
    beat = {"pid": pid or os.getpid(), "wall_ts": int((time.time() - age_s) * 1000)}
    (d / "heartbeat.json").write_text(json.dumps({**beat, "status": status}))


def _bot_json(tmp_path, **extra):
    d = tmp_path / "state"
    d.mkdir(exist_ok=True)
    p = tmp_path / "bot.json"
    p.write_text(json.dumps({"state_dir": str(d), **extra}))
    return d, p


def _requests(d):
    return sorted(x.name.split("-", 1)[1] for x in (d / "requests").glob("*.json"))


@pytest.mark.parametrize(
    "extra",
    [
        {"mode": "live"},
        {"mode": "wallet", "pairs": ["SOL/USDT"], "wallet": {"provider": "phantom"}},
    ],
)
def test_a_fresh_single_bot_dashboard_reports_the_configured_mode(tmp_path, extra):
    d, p = _bot_json(tmp_path, **extra)
    rt = FleetRuntime(None, {"bot": (d, None, Controller(d, p))})
    meta = rt.snapshot("bot")["meta"]
    assert meta["mode"] == extra["mode"] and meta["configured_mode"] == extra["mode"]
    # an older run's state.json says paper, but Start runs bot.json's real-money mode
    (d / "state.json").write_text(json.dumps({"mode": "paper", "exchange": "binance"}))
    dash._CACHE.clear()
    meta = rt.snapshot("bot")["meta"]
    assert meta["mode"] == "paper" and meta["configured_mode"] == extra["mode"]
    t = Terminal(rt, start_wait_s=0)
    r = t.handle("start the day")
    assert r.get("confirm") and "real funds" in r["text"]
    assert t.handle("no")["text"].startswith("Cancelled")
    assert t.handle("start bot").get("confirm")


def test_requests_are_queued_only_for_a_running_bot(tmp_path):
    d, _ = _bot_json(tmp_path)
    (d / "learnings.json").write_text(
        json.dumps({"rules": [{"id": "L1", "status": "active", "why": "enough evidence"}]})
    )
    ctrl = Controller(d, None)
    ok, msg = ctrl.act({"action": "stop"})
    assert not ok and msg == "the bot is not running"
    ok, msg = ctrl.act({"action": "close", "pair": "ALL"})
    assert not ok and "only a running bot sends the sell order" in msg
    ok, msg = ctrl.act({"action": "rule", "rule_id": "L1", "status": "disabled"})
    assert ok and "next start" in msg
    raw = json.loads((d / "learnings.json").read_text())
    assert raw["overrides"] == {"L1": "disabled"} and raw["rules"][0]["status"] == "disabled"
    assert ctrl.act({"action": "rule", "rule_id": "L1", "status": None})[0]
    assert json.loads((d / "learnings.json").read_text())["overrides"] == {}
    assert _requests(d) == []  # nothing waits to fire on the next start
    _beat(d)
    for status in ("disabled", "active", "disabled"):
        assert ctrl.act({"action": "rule", "rule_id": "L1", "status": status})[0]
    assert ctrl.act({"action": "stop"})[0] and ctrl.act({"action": "close", "pair": "ALL"})[0]
    assert _requests(d) == ["close.json", "rule.json", "stop.json"]  # one rule request


def test_start_deletes_stale_stop_and_close_requests(tmp_path, monkeypatch):
    from research.trendbot.live_bot import post_request

    d, p = _bot_json(tmp_path)
    for action in ("stop", "close", "rule"):
        post_request(d, action=action)
    monkeypatch.setattr(launch, "command", lambda *a: ([sys.executable, "-c", "pass"], None))
    ctrl = Controller(d, p)
    assert ctrl._start()[0]
    ctrl.proc.wait(timeout=30)
    assert _requests(d) == ["rule.json"]


def test_a_crashed_bot_is_not_running_even_with_a_fresh_heartbeat(tmp_path):
    d, _ = _bot_json(tmp_path)
    ctrl = Controller(d, None)
    ctrl.proc = subprocess.Popen([sys.executable, "-c", "pass"])
    ctrl.proc.wait(timeout=30)
    _beat(d)  # written by the bot just before it crashed (pid alive: the venv launcher case)
    assert ctrl.bot_status()["running"] is False
    _beat(d, age_s=-2)  # a newer heartbeat: another bot started since
    assert ctrl.bot_status()["running"] is True


def test_pid_alive_on_windows_reads_the_exit_code():
    import ctypes

    class Kernel32:
        def __init__(self, handle, code):
            self.handle, self.code, self.closed = handle, code, False

        def OpenProcess(self, access, inherit, pid):
            return self.handle

        def GetExitCodeProcess(self, handle, ref):
            ctypes.cast(ref, ctypes.POINTER(ctypes.c_ulong))[0] = self.code
            return 1

        def CloseHandle(self, handle):
            self.closed = True

        def GetLastError(self):
            return 87  # ERROR_INVALID_PARAMETER: no such process

    k = Kernel32(42, dash.STILL_ACTIVE)
    assert dash._pid_alive_windows(1234, k) is True and k.closed
    assert dash._pid_alive_windows(1234, Kernel32(42, 1)) is False  # exited with code 1
    assert dash._pid_alive_windows(1234, Kernel32(0, 0)) is False  # no such process


def test_the_bot_survives_its_dashboard_window_on_windows(monkeypatch):
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    assert dash._detached_kwargs(nt=True) == {"creationflags": 0x08000200}
    assert dash._detached_kwargs(nt=False) == {"start_new_session": True}


def test_two_simultaneous_starts_launch_one_bot(tmp_path, monkeypatch):
    d, p = _bot_json(tmp_path)
    sleeper = [sys.executable, "-c", "import time; time.sleep(20)"]
    monkeypatch.setattr(launch, "command", lambda *a: (sleeper, None))
    real = subprocess.Popen

    def slow_popen(*a, **kw):  # widens the race window between the check and the launch
        time.sleep(0.3)
        return real(*a, **kw)

    monkeypatch.setattr(dash.subprocess, "Popen", slow_popen)
    ctrl = Controller(d, p)
    rt = FleetRuntime(None, {"bot": (d, None, ctrl)})
    out = []
    threads = [
        threading.Thread(target=lambda: out.append(rt.act({"action": "start"}))) for _ in range(2)
    ]
    try:
        for th in threads:
            th.start()
        for th in threads:
            th.join(30)
        assert sorted(code for code, _ in out) == [200, 400]
    finally:
        if ctrl.proc is not None:
            ctrl.proc.kill()
            ctrl.proc.wait(timeout=30)


@pytest.fixture
def server(tmp_path):
    d, p = _bot_json(tmp_path)
    ctrl = Controller(d, p)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(d, None, 15, ctrl, "tok"))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def post(body, path="/api/control"):
        req = urllib.request.Request(
            base + path, data=json.dumps(body).encode(), method="POST",
            headers={"X-Trendbot-Token": "tok"},
        )  # fmt: skip
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    yield post, ctrl, d
    srv.shutdown()
    srv.server_close()


def test_unexpected_errors_come_back_as_json(server, monkeypatch):
    post, _, _ = server
    monkeypatch.setitem(sys.modules, "ccxt", None)  # ccxt missing
    code, body = post({"action": "scan"})
    assert code == 400 and body == {
        "ok": False,
        "message": "ccxt is not installed: pip install ccxt",
    }

    class Unreachable:
        def __init__(self, params):
            pass

        def fetch_tickers(self):
            raise OSError("<urlopen error Tunnel connection failed: 403 Forbidden>")

    monkeypatch.setitem(sys.modules, "ccxt", SimpleNamespace(binance=Unreachable))
    code, body = post({"action": "scan"})
    assert code == 400 and "blocked the connection (403 Forbidden)" in body["message"]
    monkeypatch.setattr(Controller, "act", lambda *a: 1 / 0)
    code, body = post({"action": "pause"})
    assert code == 500 and body["ok"] is False and "division by zero" in body["message"]
    assert post({"action": "approve", "approval_id": "x"})[0] == 400  # single bot: no fleet


def test_terminal_reports_a_bot_that_dies_at_start(tmp_path):
    d, _ = _bot_json(tmp_path)

    class DyingCtrl:
        dir = d

        def bot_status(self):
            return {"running": False, "last_error": "ccxt is not installed: pip install ccxt"}

        def act(self, req):
            return True, "bot started (pid 7)"

    t = Terminal(FleetRuntime(None, {"bot": (d, None, DyingCtrl())}), start_wait_s=0.1)
    r = t.handle("start the day")["text"]
    assert "stopped right after starting: ccxt is not installed" in r and "Good morning" not in r


class StubCtrl:
    def __init__(self, d, running=False):
        self.dir, self.running, self.log = d, running, []

    def bot_status(self):
        return {"running": self.running, "can_start": True, "last_error": None}

    def act(self, req):
        self.log.append(req["action"])
        if req["action"] == "start":
            self.running = True
        return True, {"start": "bot started (pid 7)"}.get(req["action"], "done")


def test_terminal_start_stop_and_trading_need_a_running_bot(tmp_path):
    from research.trendbot.journal import write_journal

    from .test_terminal import NOW, _t

    d, _ = _bot_json(tmp_path)
    trades = [_t(1, "BTC/USDT", 5.0, NOW - 3_600_000), _t(2, "ETH/USDT", 0.0, NOW, closed=False)]
    write_journal(trades, d / "journal.csv")
    ctrl = StubCtrl(d)
    t = Terminal(FleetRuntime(None, {"bot": (d, None, ctrl)}), start_wait_s=0)
    assert "not running" in t.handle("execute trade BTC")["text"]
    r = t.handle("close all")
    assert "confirm" not in r and "only a running bot sends the sell order" in r["text"]
    assert "not running" in t.handle("stop")["text"] and "stop" not in ctrl.log
    r = t.handle("start the day")["text"]
    assert "bot: bot" not in r and r.startswith("Good morning. Bot started (pid 7)")
    assert t.handle("stop")["confirm"]  # plain "stop" is stop bot
    t.handle("yes")
    assert ctrl.log[-1] == "stop"


def test_terminal_unfollow_and_help(monkeypatch, tmp_path):
    import re

    from research.trendbot import traders

    monkeypatch.setattr(
        traders, "set_follow", lambda n, f: f"{'following' if f else 'unfollowed'} {n}"
    )
    monkeypatch.setattr(traders, "refresh", lambda: {})
    d, _ = _bot_json(tmp_path)
    t = Terminal(FleetRuntime(None, {"bot": (d, None, StubCtrl(d))}))
    assert "no longer arm" in t.handle("unfollow alice")["text"]
    assert "now arm the pair" in t.handle("follow alice")["text"]
    assert not re.search(r"\S {2,}\S", HELP)  # no column alignment (it breaks on a phone)
    assert "start day / end day" in HELP and "execute trade BTC" in HELP
