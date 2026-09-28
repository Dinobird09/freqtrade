"""Connections from the dashboard: exchange keys in .env, MCP servers (TradingView etc.)."""

import json
import os
import stat
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from research.trendbot import connections as c
from research.trendbot.dashboard import FleetRuntime, make_handler


SECRET = "s3cr3t-value-never-shown"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("TRENDBOT_ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.setenv("TRENDBOT_CONNECTIONS_FILE", str(tmp_path / "connections.json"))
    before = {k for k in os.environ if k.startswith("TRENDBOT_")}
    yield
    for k in [k for k in os.environ if k.startswith("TRENDBOT_") and k not in before]:
        del os.environ[k]


def test_env_file_is_updated_in_place_and_private(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# my notes\nOTHER=1\nTRENDBOT_BINANCE_API_KEY=old\n")
    c.set_exchange_keys("binance", "live", "k1", SECRET)
    text = env.read_text()
    assert "# my notes" in text and "OTHER=1" in text and "old" not in text
    assert f"TRENDBOT_BINANCE_API_SECRET={SECRET}" in text
    if os.name == "posix":
        assert stat.S_IMODE(env.stat().st_mode) == 0o600
    assert os.environ["TRENDBOT_BINANCE_API_KEY"] == "k1"  # bots started later inherit it
    c.clear_exchange_keys("binance", "live")
    assert "TRENDBOT_BINANCE_API" not in env.read_text() and "OTHER=1" in env.read_text()
    with pytest.raises(c.ConnectionSetupError):
        c.set_exchange_keys("binance", "live", "k\nINJECTED=1", SECRET)
    with pytest.raises(c.ConnectionSetupError):
        c.set_exchange_keys("kraken", "live", "k", SECRET)


def test_status_never_contains_a_secret():
    c.set_exchange_keys("binance", "testnet", "key-abc", SECRET)
    st = c.status()
    blob = json.dumps(st)
    assert SECRET not in blob and "key-abc" not in blob
    row = next(
        r for r in st["exchanges"] if r["exchange"] == "binance" and r["account"] == "testnet"
    )
    assert row["keys"] == "set"
    other = next(
        r for r in st["exchanges"] if r["exchange"] == "coinbase" and r["account"] == "live"
    )
    assert other["keys"] == "missing"


def test_exchange_specific_keys_win_over_the_generic_ones(monkeypatch):
    monkeypatch.setenv("TRENDBOT_API_KEY", "generic")
    monkeypatch.setenv("TRENDBOT_API_SECRET", "generic-s")
    assert c.resolve_keys("coinbase", "live")[:2] == ("generic", "generic-s")
    c.set_exchange_keys("coinbase", "live", "cb", "cb-s", "pass")
    assert c.resolve_keys("coinbase", "live") == ("cb", "cb-s", "pass")


def test_test_exchange_logs_in_and_reads_the_balance():
    class FakeEx:
        def __init__(self, params):
            self.params = params
            self.sandbox = False

        def set_sandbox_mode(self, on):
            self.sandbox = on

        def fetch_balance(self):
            if self.params["apiKey"] != "good":
                raise PermissionError("invalid api key")
            assert self.sandbox
            return {"free": {"USDT": 1000.0, "BTC": 0.0}}

    class FakeCcxt:
        binance = FakeEx

    c.set_exchange_keys("binance", "testnet", "good", SECRET)
    assert "connected, 1 assets (USDT 1000)" in c.test_exchange("binance", "testnet", FakeCcxt)
    c.set_exchange_keys("binance", "testnet", "bad", SECRET)
    with pytest.raises(c.ConnectionSetupError, match="login failed") as e:
        c.test_exchange("binance", "testnet", FakeCcxt)
    assert SECRET not in str(e.value)


# ---------------------------------------------------------------------- fake MCP server
TOOLS = [
    {"name": "technical_rating", "description": "TradingView technical rating for a symbol"},
    {"name": "news", "description": "headlines"},
]


def _mcp_server(sse=False, token=None):
    calls = []

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            if token and self.headers.get("Authorization") != f"Bearer {token}":
                self.send_response(401)
                self.end_headers()
                return
            msg = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((msg, self.headers.get("Mcp-Session-Id")))
            if "id" not in msg:
                self.send_response(202)
                self.end_headers()
                return
            m = msg["method"]
            if m == "initialize":
                result = {"serverInfo": {"name": "fake-tv", "version": "1"}, "capabilities": {}}
            elif m == "tools/list":
                result = {"tools": TOOLS}
            elif m == "tools/call":
                a = msg["params"]["arguments"]
                result = {"content": [{"type": "text", "text": f"rating {a.get('symbol')}: BUY"}]}
            else:
                result = None
            body = {"jsonrpc": "2.0", "id": msg["id"], "result": result}
            data = json.dumps(body).encode()
            self.send_response(200)
            if sse:
                data = b"event: message\ndata: " + data + b"\n\n"
                self.send_header("Content-Type", "text/event-stream")
            else:
                self.send_header("Content-Type", "application/json")
            if m == "initialize":
                self.send_header("Mcp-Session-Id", "sess-1")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}/mcp", calls


@pytest.mark.parametrize("sse", [False, True])
def test_mcp_client_lists_and_calls_tools(sse):
    srv, url, calls = _mcp_server(sse=sse)
    try:
        cl = c.McpClient(url)
        cl.initialize()
        assert cl.server["name"] == "fake-tv"
        assert [t["name"] for t in cl.list_tools()] == ["technical_rating", "news"]
        res = cl.call_tool("technical_rating", {"symbol": "BINANCE:BTCUSDT"})
        assert c.tool_text(res) == "rating BINANCE:BTCUSDT: BUY"
        assert calls[1][0]["method"] == "notifications/initialized"
        assert calls[-1][1] == "sess-1"  # the session id is sent back
    finally:
        srv.shutdown()


def test_mcp_urls_must_be_https_or_local():
    with pytest.raises(c.ConnectionSetupError):
        c.McpClient("http://example.com/mcp")
    c.McpClient("https://mcp.tradingview.com/mcp")


def test_token_is_stored_in_env_and_a_missing_one_is_explained(tmp_path):
    srv, url, _ = _mcp_server(token="tv-token")
    try:
        c.save_mcp("tradingview", url)
        with pytest.raises(c.ConnectionSetupError, match="needs a sign-in"):
            c.test_mcp("tradingview")
        c.save_mcp("tradingview", url, token="tv-token")
        assert "TRENDBOT_MCP_TRADINGVIEW_TOKEN=tv-token" in (tmp_path / ".env").read_text()
        assert "tv-token" not in (tmp_path / "connections.json").read_text()
        res = c.test_mcp("tradingview")
        assert len(res["tools"]) == 2
        st = c.status()["mcp"][0]
        assert st["token"] is True and st["tested"] and "tv-token" not in json.dumps(st)
        c.remove_mcp("tradingview")
        assert c.status()["mcp"] == [] and "tv-token" not in (tmp_path / ".env").read_text()
    finally:
        srv.shutdown()


def test_mcp_sources_are_collected_per_pair(tmp_path):
    srv, url, _ = _mcp_server()
    try:
        c.save_mcp("tv", url)
        c.set_mcp_source("tv", "technical_rating", {"symbol": "{symbol}"}, True, True)
        out = c.collect(
            tmp_path / "state", {"pairs": ["BTC/USDT", "ETH/USDT"], "exchange": "binance"}
        )
        assert out["tv"] == {"ok": True, "calls": 2, "errors": 0}
        rows = c.latest_mcp_rows(tmp_path / "state")
        assert [r["text"] for r in rows] == [
            "rating BINANCE:BTCUSDT: BUY",
            "rating BINANCE:ETHUSDT: BUY",
        ]
        c.set_mcp_source("tv", "technical_rating", {}, True, False)
        assert c.load_connections()["mcp"]["tv"]["sources"] == []
    finally:
        srv.shutdown()


def test_dashboard_connection_api_needs_the_token_and_hides_secrets(tmp_path):
    rt = FleetRuntime(None, {"bot": (tmp_path, None, object())})
    srv = ThreadingHTTPServer(
        ("127.0.0.1", 0), make_handler(None, None, 15, token="tok", runtime=rt)
    )
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def post(body, tok="tok"):
        req = urllib.request.Request(
            base + "/api/control",
            method="POST",
            data=json.dumps(body).encode(),
            headers={"X-Trendbot-Token": tok},
        )
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    try:
        body = {
            "action": "keys_save",
            "exchange": "binance",
            "account": "live",
            "key": "kk",
            "secret": SECRET,
        }
        assert post(body, tok="wrong")[0] == 403
        code, res = post(body)
        assert code == 200 and res["ok"] and SECRET not in json.dumps(res)
        with urllib.request.urlopen(base + "/api/connections") as r:
            blob = r.read().decode()
        assert SECRET not in blob and '"keys": "set"' in blob
        code, res = post({"action": "mcp_save", "name": "Bad Name!", "url": "https://x"})
        assert code == 400
    finally:
        srv.shutdown()


def test_multiline_private_keys_round_trip(tmp_path):
    pem = "-----BEGIN EC PRIVATE KEY-----\nAAAA\nBBBB\n-----END EC PRIVATE KEY-----"
    c.set_exchange_keys("coinbase", "live", "organizations/x/apiKeys/y", pem)
    line = next(
        x for x in (tmp_path / ".env").read_text().splitlines() if "COINBASE_API_SECRET" in x
    )
    assert "\\n" in line  # stored on one line
    assert c.resolve_keys("coinbase", "live")[1] == pem
