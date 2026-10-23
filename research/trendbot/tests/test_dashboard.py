"""Dashboard: snapshot numbers, HTTP endpoints and the static export."""

import json
import threading
import urllib.error
import urllib.request

import pytest

from research.trendbot.adoption_evidence import write_decisions_log
from research.trendbot.config import StrategyConfig
from research.trendbot.dashboard import build_snapshot, main, make_handler, render_html
from research.trendbot.data import save_candles_csv
from research.trendbot.gatekeeper import DecisionRecord
from research.trendbot.journal import write_journal
from research.trendbot.models import DAY_MS, HOUR_MS, Candle, Trade


T0 = 1_700_000_000_000 // (4 * HOUR_MS) * (4 * HOUR_MS)


def _trade(
    tid,
    pair,
    entry_ts,
    entry,
    stop,
    target,
    qty,
    exit_ts=None,
    exit_px=None,
    reason=None,
    pnl=None,
    r=None,
):
    risk = qty * (entry - stop)
    return Trade(
        tid,
        pair,
        "base",
        entry_ts - 4 * HOUR_MS,
        entry_ts,
        entry,
        stop,
        target,
        qty,
        risk,
        1.0,
        "pivot",
        exit_ts,
        exit_px,
        reason,
        0.0,
        pnl,
        r,
        {},
    )


@pytest.fixture
def state_dir(tmp_path):
    d = tmp_path / "state"
    trades = [
        _trade(1, "BTC/USDT", T0, 100.0, 95.0, 110.0, 20.0, T0 + DAY_MS, 110.0, "TP", 200.0, 2.0),
        _trade(
            2,
            "BTC/USDT",
            T0 + 2 * DAY_MS,
            100.0,
            95.0,
            110.0,
            20.0,
            T0 + 3 * DAY_MS,
            95.0,
            "SL",
            -100.0,
            -1.0,
        ),
        _trade(3, "ETH/USDT", T0 + 4 * DAY_MS, 50.0, 48.0, 54.0, 50.0),
    ]
    write_journal(trades, d / "journal.csv")
    (d / "state.json").write_text(
        json.dumps(
            {
                "exchange": "binance",
                "mode": "live",
                "starting_equity": 10_000.0,
                "reservations": {"3": 1.0},
                "unmanaged": [],
            }
        )
    )
    write_decisions_log(
        [
            DecisionRecord("BTC/USDT", T0, True, "R7_position_risk", "ok"),
            DecisionRecord("ETH/USDT", T0, False, "R3_volume", "volume <b>low</b>"),
            DecisionRecord("BNB/USDT", T0, False, "R3_volume", "volume low"),
        ],
        d / "decisions.csv",
    )
    candles = [
        Candle(T0 + i * 4 * HOUR_MS, 50 + i * 0.1, 51 + i * 0.1, 49 + i * 0.1, 50.5 + i * 0.1, 10.0)
        for i in range(260)
    ]
    save_candles_csv(candles, d / "candles" / "ETH_USDT-4h.csv")
    (d / "bot.log").write_text("line 1\nline 2\n")
    return d


def test_snapshot_numbers(state_dir):
    now = T0 + 5 * DAY_MS
    s = build_snapshot(state_dir, StrategyConfig(), now_ms=now)
    st = s["stats"]
    assert st["equity"] == 10_100.0 and st["closed_trades"] == 2
    assert st["avg_r"] == 0.5 and st["total_r"] == 1.0 and st["profit_factor"] == 2.0
    assert st["max_dd_pct"] == pytest.approx(100 / 10_200 * 100, abs=0.01)
    assert st["exits"] == {"TP": 1, "SL": 1}
    assert [p["equity"] for p in s["equity_curve"]] == [10_000.0, 10_200.0, 10_100.0]
    assert s["risk"]["open_risk_pct"] == 1.0
    assert s["risk"]["weekly"]["pnl"] == 100.0 and not s["risk"]["weekly"]["halted"]
    (pos,) = s["open_positions"]
    last = 50.5 + 259 * 0.1
    assert pos["pair"] == "ETH/USDT" and pos["last"] == pytest.approx(last)
    expected = 50 * (last - 50) - 0.001 * 50 * (50 + last)
    assert pos["unrealized"] == pytest.approx(expected, abs=0.01)
    assert pos["progress"] == pytest.approx((last - 48) / 6, abs=1e-4)
    eth = s["pairs"]["ETH/USDT"]
    assert len(eth["candles"]) == 180 and eth["candles"][-1]["ema200"] is not None
    assert s["decision_counts"] == {"R3_volume": 2, "R7_position_risk": 1}
    assert s["decisions"][0]["pair"] == "BNB/USDT"  # newest first
    assert s["meta"]["mode"] == "live" and s["meta"]["quote"] == "USDT"
    assert s["log"] == ["line 1", "line 2"]
    assert s["breakers"] == []


def test_snapshot_of_an_empty_state_dir(tmp_path):
    s = build_snapshot(tmp_path, StrategyConfig(), now_ms=T0)
    assert s["stats"]["closed_trades"] == 0 and s["open_positions"] == [] and s["pairs"] == {}
    assert s["meta"]["has_state"] is False


def test_export_embeds_escaped_snapshot(state_dir, tmp_path):
    snap = build_snapshot(state_dir, StrategyConfig(), now_ms=T0)
    snap["log"].append("</script><script>alert(1)</script>")
    html = render_html(snap, 15)
    assert "/*__SNAPSHOT__*/" not in html and "__REFRESH_S__" not in html
    assert "</script><script>alert(1)" not in html
    assert "textContent" in html and "innerHTML" not in html
    out = tmp_path / "d.html"
    assert (
        main(["--state-dir", str(state_dir), "--export", str(out), "--now", "2023-11-20T00:00:00Z"])
        == 0
    )
    assert '"mode": "live"' in out.read_text()


def test_http_endpoints_without_controls_are_read_only(state_dir):
    from http.server import ThreadingHTTPServer

    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(state_dir, StrategyConfig(), 15))
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/") as r:
            page = r.read().decode()
            assert "trendbot" in page and "const EMBEDDED = null" in page
            assert "default-src 'none'" in r.headers["Content-Security-Policy"]
        with urllib.request.urlopen(base + "/api/snapshot") as r:
            snap = json.loads(r.read())
            assert snap["stats"]["closed_trades"] == 2
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/journal.csv")
        assert e.value.code == 404
        req = urllib.request.Request(base + "/api/snapshot", data=b"x", method="POST")
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req)
        assert e.value.code == 404
        ctl = urllib.request.Request(base + "/api/control", data=b"{}", method="POST")
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(ctl)
        assert e.value.code == 403  # no controller: controls are off
    finally:
        srv.shutdown()
        srv.server_close()


def test_cli_requires_a_state_dir(tmp_path):
    with pytest.raises(SystemExit):
        main(["--state-dir", str(tmp_path / "missing"), "--export", str(tmp_path / "x.html")])


def test_controls_need_the_token_and_write_request_files(state_dir):
    from http.server import ThreadingHTTPServer

    from research.trendbot.dashboard import Controller
    from research.trendbot.live_bot import pop_requests, read_control

    ctl = Controller(state_dir, None)
    srv = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(state_dir, StrategyConfig(), 15, ctl, "tok", [f"127.0.0.1:{0}"]),
    )
    port = srv.server_address[1]
    srv.RequestHandlerClass = make_handler(
        state_dir, StrategyConfig(), 15, ctl, "tok", [f"127.0.0.1:{port}"]
    )
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"

    def post(body, token="tok", host=None):
        req = urllib.request.Request(
            base + "/api/control",
            data=json.dumps(body).encode(),
            method="POST",
            headers={"X-Trendbot-Token": token},
        )
        if host:
            req.add_header("Host", host)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, None

    try:
        assert post({"action": "pause"}, token="wrong")[0] == 403
        assert post({"action": "pause"}, host="evil.example:80")[0] == 404
        code, body = post({"action": "pause"})
        assert code == 200 and body["ok"] and read_control(state_dir)["entries_paused"] is True
        assert post({"action": "resume"})[0] == 200
        assert read_control(state_dir)["entries_paused"] is False
        _beat(state_dir)  # a running bot: stop / close / rule are requests it reads
        assert post({"action": "close", "pair": "ETH/USDT"})[0] == 200
        assert post({"action": "stop"})[0] == 200
        assert post({"action": "rule", "rule_id": "L1", "status": "disabled"})[0] == 200
        assert post({"action": "rule", "rule_id": "L1", "status": "bogus"})[0] == 400
        assert post({"action": "start"})[0] == 400  # no --settings: start is unavailable
        reqs = [r for _, r in pop_requests(state_dir)]
        assert [r["action"] for r in reqs] == ["close", "stop", "rule"]
        assert reqs[0]["pair"] == "ETH/USDT"
        (state_dir / "heartbeat.json").unlink()
        with urllib.request.urlopen(base + "/") as r:
            assert 'const CONTROL_TOKEN = "tok"' in r.read().decode()
        with urllib.request.urlopen(base + "/api/snapshot") as r:
            snap = json.loads(r.read())
        assert snap["bot"]["running"] is False and snap["bot"]["can_start"] is False
    finally:
        srv.shutdown()
        srv.server_close()


def _beat(d, pid=None, age_s=0.0, status="running"):
    import os
    import time

    beat = {"pid": pid or os.getpid(), "wall_ts": int((time.time() - age_s) * 1000)}
    (d / "heartbeat.json").write_text(json.dumps({**beat, "status": status}))


def test_export_never_contains_a_control_token(state_dir):
    html = render_html(build_snapshot(state_dir, StrategyConfig(), now_ms=T0), 15)
    assert "const CONTROL_TOKEN = null" in html


def test_live_snapshot_caches_files_and_overlays_live_prices(state_dir):
    import time as _time

    from research.trendbot import dashboard as dash

    cfg = StrategyConfig()
    now = int(_time.time() * 1000) // 60_000 * 60_000 + 1_000  # every call below in one minute
    a = dash.live_snapshot(state_dir, cfg, now_ms=now)
    assert a["live"]["fresh"] is False and a["open_positions"][0].get("live") is None
    (state_dir / "live.json").write_text(
        json.dumps(
            {
                "wall_ts": now,
                "prices": {"ETH/USDT": {"bid": 52.0, "ask": 52.1, "last": 52.05}},
                "forming": {"ETH/USDT": {"ts": T0 + 260 * 4 * HOUR_MS, "close": 52.05}},
            }
        )
    )
    b = dash.live_snapshot(state_dir, cfg, now_ms=now + 500)
    pos = b["open_positions"][0]
    assert b["live"]["fresh"] and pos["live"] and pos["last"] == 52.0
    fee = cfg.fee_rate * 50 * (50 + 52)
    assert pos["unrealized"] == pytest.approx(50 * 2 - fee, abs=0.01)
    assert b["pairs"] is a["pairs"]  # the heavy part came from the cache
    stale = dash.live_snapshot(state_dir, cfg, now_ms=now + 60_000)
    assert stale["live"]["fresh"] is False  # an old live.json is never shown as live
    write_journal([], state_dir / "journal.csv")  # a file changed: rebuilt
    c = dash.live_snapshot(state_dir, cfg, now_ms=now + 700)
    assert c["pairs"] is not a["pairs"] and c["open_positions"] == []


def test_the_dashboard_shows_why_the_bot_stopped(tmp_path, monkeypatch):
    import sys

    from research.trendbot import launch
    from research.trendbot.dashboard import Controller

    d = tmp_path / "state"
    d.mkdir()
    (d / "bot.stderr.log").write_bytes(b"an old run's error\n")
    settings = tmp_path / "bot.json"
    settings.write_text(json.dumps({"state_dir": str(d)}))
    crash = "import sys; sys.exit('ccxt is not installed: pip install ccxt')"
    monkeypatch.setattr(launch, "command", lambda *a: ([sys.executable, "-c", crash], None))
    ctrl = Controller(d, settings)
    ok, _ = ctrl._start()
    assert ok
    ctrl.proc.wait(timeout=30)
    st = ctrl.bot_status()
    assert st["running"] is False and st["last_error"] == "ccxt is not installed: pip install ccxt"
    monkeypatch.setattr(launch, "command", lambda *a: ([sys.executable, "-c", "pass"], None))
    ctrl._start()
    ctrl.proc.wait(timeout=30)
    assert ctrl.bot_status()["last_error"] is None  # a clean exit is not an error


def test_market_pages_are_served_without_the_token(state_dir):
    from http.server import ThreadingHTTPServer

    from research.trendbot.dashboard import Controller

    ctl = Controller(state_dir, None)
    srv = ThreadingHTTPServer(
        ("127.0.0.1", 0), make_handler(state_dir, StrategyConfig(), 15, ctl, "secret-tok")
    )
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        for path in ("/market", "/market/"):
            with urllib.request.urlopen(base + path) as r:
                body, headers = r.read().decode(), r.headers
            assert r.status == 200 and "Market terminal" in body and "secret-tok" not in body
            assert (
                "https://api.binance.com"
                in headers["Content-Security-Policy"].split("connect-src", 1)[1].split(";")[0]
            )
            assert headers["X-Frame-Options"] == "DENY"
            assert headers["X-Content-Type-Options"] == "nosniff"
        with urllib.request.urlopen(base + "/market/tv") as r:
            assert r.status == 200 and r.headers.get("X-Frame-Options") is None
            assert "secret-tok" not in r.read().decode()
            assert "frame-ancestors http://127.0.0.1:*" in r.headers["Content-Security-Policy"]
        with urllib.request.urlopen(base + "/") as r:  # the dashboard keeps its strict policy
            assert r.headers["X-Frame-Options"] == "DENY"
            assert "connect-src 'self';" in r.headers["Content-Security-Policy"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_meme_desk_page_and_its_api(state_dir):
    from http.server import ThreadingHTTPServer

    from research.trendbot import memecoins
    from research.trendbot.dashboard import Controller, FleetRuntime

    from .test_memecoins import MINT, Net, safe_routes

    ctl = Controller(state_dir, None)
    rt = FleetRuntime(None, {"bot": (state_dir, StrategyConfig(), ctl)})
    rt._memes = memecoins.Desk(
        state_dir, lambda: memecoins.Sources(Net(safe_routes()), "https://rpc")
    )
    srv = ThreadingHTTPServer(
        ("127.0.0.1", 0), make_handler(None, None, 15, token="secret-tok", runtime=rt)
    )
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def post(body):
        req = urllib.request.Request(
            base + "/api/control",
            data=json.dumps(body).encode(),
            headers={"X-Trendbot-Token": "secret-tok", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    try:
        with urllib.request.urlopen(base + "/memes") as r:
            body, csp = r.read().decode(), r.headers["Content-Security-Policy"]
        assert "Meme desk" in body and '"secret-tok"' in body  # the page drives the buttons
        assert "frame-src https://dexscreener.com" in csp and "connect-src 'self'" in csp
        with urllib.request.urlopen(f"{base}/api/memes/check?chain=solana&address={MINT}") as r:
            rep = json.loads(r.read())
        assert [c["status"] for c in rep["six"]][1:] == ["pass"] * 5
        with pytest.raises(urllib.error.HTTPError) as bad:
            urllib.request.urlopen(f"{base}/api/memes/check?chain=solana&address=nope")
        assert "not a solana token address" in json.loads(bad.value.read())["error"]
        assert post({"action": "meme_confirm", "chain": "solana", "address": MINT})[0] == 200
        assert memecoins.is_confirmed(state_dir, "solana", MINT)
        code, out = post({"action": "meme_buy", "chain": "solana", "address": MINT, "usd": 20})
        assert code == 400 and "connect Phantom first" in out["message"]
        with urllib.request.urlopen(base + "/api/memes/positions") as r:
            assert json.loads(r.read())["positions"] == []
    finally:
        srv.shutdown()
