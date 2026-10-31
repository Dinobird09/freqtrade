"""Dashboard wiring: the builder page and its actions, OpenAlice, arbitrage and POST /mcp."""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from research.trendbot import dashboard_extras as extras
from research.trendbot import mcp_server
from research.trendbot.dashboard import Controller, FleetRuntime, make_handler


TOKEN = "control-token-for-tests"
MCP_TOKEN = "mcp-token-for-tests-0123456789"


@pytest.fixture
def site(tmp_path, monkeypatch):
    monkeypatch.setenv("TRENDBOT_CONNECTIONS_FILE", str(tmp_path / "connections.json"))
    monkeypatch.setenv(mcp_server.TOKEN_ENV, MCP_TOKEN)
    monkeypatch.setattr("research.trendbot.live_exchange.load_env_file", lambda *a, **k: None)
    monkeypatch.setattr(extras, "_TOKEN", [])
    d = tmp_path / "state"
    d.mkdir()
    bots = tmp_path / "bots"
    bots.mkdir()
    settings = bots / "main.json"
    settings.write_text(json.dumps({"state_dir": str(d)}))
    rt = FleetRuntime(None, {"main": (d, None, Controller(d, settings))})
    handler = make_handler(None, None, 1, token=TOKEN, runtime=rt)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    yield base, rt, tmp_path
    srv.shutdown()
    srv.server_close()


def get(url):
    with urllib.request.urlopen(url, timeout=20) as r:
        return r.status, r.read().decode(), dict(r.headers)


def post(url, body, headers=None):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, json.loads(raw) if raw else None


def control(base, body):
    return post(base + "/api/control", body, {"X-Trendbot-Token": TOKEN})


def test_builder_page_has_the_token_and_the_catalog(site):
    base, _, _ = site
    code, page, headers = get(base + "/builder")
    assert code == 200 and json.dumps(TOKEN) in page and "__CONTROL_TOKEN__" not in page
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    cat = json.loads(get(base + "/api/builder/catalog")[1])
    assert {"confluence", "grid", "dca"} <= {s["id"] for s in cat["strategies"]}
    assert "🧱 Strategy builder" in get(base + "/")[1]


def test_builder_backtest_and_save_through_the_dashboard(site):
    base, _, tmp = site
    spec = {"strategy": "mean_reversion", "exchange": "demo", "timeframe": "4h", "days": 300}
    code, j = control(base, {"action": "builder_backtest", "spec": spec})
    assert code == 200 and j["ok"] and "trades" in j["result"]["stats"]
    code, j = control(base, {"action": "builder_save", "spec": spec, "name": "mr-demo"})
    assert code == 200 and j["ok"], j
    saved = tmp / "bots" / "mr-demo.json"
    assert saved.exists() and json.loads(saved.read_text())["mode"] == "paper"
    assert j["fleet"] is None  # a single-bot dashboard has no fleet file
    code, j = control(base, {"action": "builder_save", "spec": spec, "name": "mr-demo"})
    assert code == 400 and "already exists" in j["message"]
    code, j = post(base + "/api/control", {"action": "builder_backtest", "spec": spec})
    assert code == 403  # no control token


def test_saving_into_a_fleet_adds_the_bot(tmp_path):
    from types import SimpleNamespace

    fleet_file = tmp_path / "fleet.json"
    (tmp_path / "bots").mkdir()
    fleet_file.write_text(
        json.dumps({"max_bots": 3, "bots": [{"name": "a", "settings": "bots/a.json"}]})
    )
    rt = SimpleNamespace(
        fleet=SimpleNamespace(
            path=fleet_file, bots={"a": SimpleNamespace(settings_path=tmp_path / "bots" / "a.json")}
        ),
        entries={},
    )
    folder, fp = extras.bots_folder(rt)
    assert folder == tmp_path / "bots" and fp == fleet_file
    code, j = extras.builder_action(
        rt, {"action": "builder_save", "name": "dca-btc", "spec": {"strategy": "dca"}}
    )
    assert code == 200 and "restart the dashboard" in j["fleet"]
    bots = json.loads(fleet_file.read_text())["bots"]
    assert bots[-1] == {"name": "dca-btc", "settings": "bots/dca-btc.json"}


def test_openalice_card_actions(site):
    base, _, tmp = site
    view = json.loads(get(base + "/api/openalice")[1])
    assert view["status"]["reachable"] is False and view["proposals"] == []
    code, j = control(
        base, {"action": "openalice_save", "workspace": "ws1", "send": {"no_trade": True}}
    )
    assert code == 200 and j["ok"]
    assert json.loads((tmp / "openalice.json").read_text())["workspace"] == "ws1"
    code, j = control(base, {"action": "openalice_save", "workspace": "bad id!"})
    assert code == 400 and "workspace id" in j["message"]
    code, j = control(base, {"action": "openalice_test"})
    assert code == 400 and "could not reach" in j["message"]
    code, j = control(base, {"action": "openalice_connect"})
    assert j["token"] == MCP_TOKEN and "chantisimo" in j["mcp_json"]


def test_mcp_endpoint_needs_its_bearer_token(site):
    base, _, _ = site
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    code, j = post(base + "/mcp", init)
    assert code == 401
    code, j = post(base + "/mcp", init, {"Authorization": "Bearer " + TOKEN})
    assert code == 401  # the dashboard's control token is not the MCP token
    auth = {"Authorization": "Bearer " + MCP_TOKEN}
    code, j = post(base + "/mcp", init, auth)
    assert code == 200 and j["result"]["serverInfo"]["name"] == "chantisimo"
    code, j = post(base + "/mcp", {"jsonrpc": "2.0", "method": "notifications/initialized"}, auth)
    assert code == 202 and j is None
    code, j = post(base + "/mcp", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, auth)
    names = {t["name"] for t in j["result"]["tools"]}
    assert {"bot_status", "search_memory", "analyze", "scorecard", "propose_trade"} <= names
    code, j = post(
        base + "/mcp",
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "scorecard", "arguments": {}},
        },
        auth,
    )
    assert code == 200 and "main" in j["result"]["structuredContent"]
    with pytest.raises(urllib.error.HTTPError) as e:
        get(base + "/mcp")
    assert e.value.code == 405


def test_proposals_show_and_can_be_dismissed(site):
    base, _, _ = site
    auth = {"Authorization": "Bearer " + MCP_TOKEN}
    call = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "propose_trade",
            "arguments": {"pair": "BTC/USDT", "entry": 100, "stop": 95, "reason": "test"},
        },
    }
    _, j = post(base + "/mcp", call, auth)
    pid = j["result"]["structuredContent"]["filed"]["id"]
    view = json.loads(get(base + "/api/openalice")[1])
    assert [p["id"] for p in view["proposals"]] == [pid]
    _, j = control(base, {"action": "proposal_dismiss", "id": pid})
    assert j["ok"] and "dismissed" in j["message"]
    assert json.loads(get(base + "/api/openalice")[1])["proposals"][0]["status"] == "dismissed"
    assert "no waiting" in control(base, {"action": "proposal_dismiss", "id": pid})[1]["message"]


def test_arbitrage_endpoint(site, monkeypatch):
    base, rt, _ = site

    def fetch(url, headers=None, data=None):
        if "okx" in url:
            return json.dumps(
                {"data": [{"instId": "BTC-USDT", "bidPx": "101", "askPx": "101.1"}]}
            ).encode()
        return json.dumps([{"symbol": "BTCUSDT", "bidPrice": "100", "askPrice": "100.01"}]).encode()

    rt.arbitrage.fetch = fetch
    out = json.loads(get(base + "/api/arbitrage?exchanges=binance,okx")[1])
    assert out["spatial"][0]["buy_on"] == "binance" and out["logged"] >= 1


def test_tradingagents_card_report_and_mcp(site, monkeypatch):
    from research.trendbot import agents_link as al

    base, _, tmp = site
    monkeypatch.setenv("TRENDBOT_ENV_FILE", str(tmp / ".env"))
    view = json.loads(get(base + "/api/agents")[1])
    assert view["python_found"] is False and view["bot_modes"] == {"main": "inform"}
    code, j = control(
        base,
        {
            "action": "agents_save",
            "provider": "anthropic",
            "debate_rounds": "2",
            "auto_daily": 1,
            "auto_hour_utc": 30,
        },
    )
    assert code == 200, j
    cfg = al.load()
    assert (
        cfg["provider"] == "anthropic" and cfg["debate_rounds"] == 2 and cfg["auto_hour_utc"] == 23
    )
    code, j = control(base, {"action": "agents_key", "key": "sk-ant-secret-value"})
    assert j["ok"] and "ANTHROPIC_API_KEY" in j["message"]
    body = get(base + "/api/agents")[1]
    assert "sk-ant-secret-value" not in body and json.loads(body)["key_set"] is True
    code, j = control(base, {"action": "agents_run", "coins": "BTC"})
    assert code == 400 and "not set up" in j["message"]

    report_dir = al.data_dir() / "reports" / "BTC-x"
    report_dir.mkdir(parents=True)
    page = report_dir / "complete_report.html"
    page.write_text("<h1>Sell</h1><script>alert(1)</script>")
    al._append(
        None,
        {
            "id": "BTC-1",
            "coin": "BTC",
            "date": "2026-10-31",
            "ok": True,
            "rating": "Sell",
            "bias": "bearish",
            "finished_ms": 1,
            "summary": "fading",
            "report_html": str(page),
            "decision": "long text",
        },
    )
    al._append(
        None,
        {
            "id": "EVIL-1",
            "coin": "EVIL",
            "ok": True,
            "rating": "Buy",
            "finished_ms": 1,
            "report_html": str(tmp / "bots" / "main.json"),
        },
    )
    rows = json.loads(get(base + "/api/agents")[1])["results"]
    assert rows[1]["has_report"] and "decision" not in rows[1]
    code, html, headers = get(base + "/agents/report?id=BTC-1")
    assert "<h1>Sell</h1>" in html and headers["Content-Security-Policy"].startswith("sandbox")
    assert "script-src" not in headers["Content-Security-Policy"]
    for rid in ("EVIL-1", "nope"):  # only files inside the TradingAgents folder
        with pytest.raises(urllib.error.HTTPError) as e:
            get(base + "/agents/report?id=" + rid)
        assert e.value.code == 404

    auth = {"Authorization": "Bearer " + MCP_TOKEN}
    _, j = post(
        base + "/mcp",
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "agents_verdicts", "arguments": {"coin": "btc"}},
        },
        auth,
    )
    v = j["result"]["structuredContent"]["verdicts"]
    assert v == [
        {
            "coin": "BTC",
            "date": "2026-10-31",
            "rating": "Sell",
            "bias": "bearish",
            "summary": "fading",
        }
    ]
