"""OpenAlice bridge (Inbox over MCP) and Chantisimo's own MCP server."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from research.trendbot import mcp_server as ms
from research.trendbot import openalice as oa
from research.trendbot.connections import ConnectionSetupError, McpClient


# ------------------------------------------------------------------ a fake OpenAlice MCP
class FakeAlice:
    """Answers like OpenAlice: global /mcp has market tools, /mcp/<ws> has inbox_push."""

    def __init__(self):
        self.pushed = []
        alice = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                msg = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if "id" not in msg:
                    self.send_response(202)
                    self.end_headers()
                    return
                ws = self.path.rstrip("/").split("/mcp")[-1].strip("/")
                m = msg["method"]
                if m == "initialize":
                    res = {"protocolVersion": "2025-06-18", "serverInfo": {"name": "open-alice"}}
                elif m == "tools/list":
                    names = ["inbox_push", "inbox_read"] if ws else ["getQuote", "searchNews"]
                    if ws == "empty":
                        names = []
                    res = {"tools": [{"name": n} for n in names]}
                elif m == "tools/call":
                    body = msg["params"]["arguments"]["body"]
                    alice.pushed.append((ws, body))
                    if "REFUSE" in body:
                        res = {"content": [{"type": "text", "text": "nope"}], "isError": True}
                    else:
                        txt = json.dumps({"ok": True, "entryId": "e1", "ts": 1})
                        res = {"content": [{"type": "text", "text": txt}]}
                out = json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": res}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/mcp"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def alice():
    a = FakeAlice()
    yield a
    a.close()


def test_settings_defaults_and_validation(tmp_path):
    cfg = oa.load(tmp_path)
    assert cfg["url"] == "http://127.0.0.1:3001/mcp" and cfg["workspace"] == ""
    assert cfg["send"] == {"trades": True, "no_trade": False, "alerts": True}
    with pytest.raises(ConnectionSetupError, match="this computer"):
        oa.configure(tmp_path, url="https://example.com/mcp")
    with pytest.raises(ConnectionSetupError, match="workspace id"):
        oa.configure(tmp_path, workspace="../etc")
    cfg = oa.configure(tmp_path, workspace="ws_1", send={"no_trade": 1, "bogus": True})
    assert cfg["send"]["no_trade"] is True and "bogus" not in cfg["send"]
    assert oa.inbox_url(cfg) == "http://127.0.0.1:3001/mcp/ws_1"


def test_status_when_openalice_is_off(tmp_path):
    oa.configure(tmp_path, url="http://127.0.0.1:9/mcp")
    st = oa.status(tmp_path)
    assert st["reachable"] is False and "turn on the MCP server" in st["hint"]
    assert st["web_ui"] == "http://127.0.0.1:47331"


def test_status_and_inbox_push(tmp_path, alice):
    oa.configure(tmp_path, url=alice.url)
    st = oa.status(tmp_path)
    assert st["reachable"] and st["tools"] == ["getQuote", "searchNews"]
    assert st["inbox_ready"] is False and "workspace id" in st["inbox_hint"]
    with pytest.raises(ConnectionSetupError, match="no OpenAlice workspace"):
        oa.push_inbox(tmp_path, "hi")

    oa.configure(tmp_path, workspace="ws1")
    assert oa.status(tmp_path)["inbox_ready"] is True
    out = oa.push_inbox(tmp_path, "**BTC** long")
    assert out == {"ok": True, "entry": "e1", "url": alice.url + "/ws1"}
    assert alice.pushed == [("ws1", "**BTC** long")]
    assert oa.load(tmp_path)["last"]["sent"]

    with pytest.raises(ConnectionSetupError, match="refused the message"):
        oa.push_inbox(tmp_path, "REFUSE")


def test_wrong_workspace_is_explained(tmp_path, alice):
    oa.configure(tmp_path, url=alice.url, workspace="empty")
    st = oa.status(tmp_path)
    assert st["inbox_ready"] is False and "check the id" in st["inbox_hint"]


def test_notify_respects_switches_and_never_raises(tmp_path, alice):
    oa.configure(tmp_path, url=alice.url, workspace="ws1")
    assert oa.notify(tmp_path, "no_trade", "NO TRADE") is None  # switched off: not sent
    assert alice.pushed == []
    assert oa.notify(tmp_path, "trades", "card") is None
    assert alice.pushed == [("ws1", "card")]
    oa.configure(tmp_path, url="http://127.0.0.1:9/mcp")
    err = oa.notify(tmp_path, "alerts", "bot stopped")
    assert err and "could not reach" in err
    assert oa.load(tmp_path)["last"]["error"] == err


def test_markdown_helpers():
    assert oa.card_markdown("Trade Type: Long", bot="b1").startswith("**Chantisimo · b1**")
    md = oa.card_markdown({"trade_type": "Long", "confluence": ["EMA", "RSI"]})
    assert "- **Trade Type:** Long" in md and "EMA, RSI" in md
    assert oa.card_markdown({"text": "X"}).endswith("```\nX\n```")
    assert oa.alert_markdown("Bot stopped", "timeout", "b2") == "**⚠ Bot stopped** · b2\n\ntimeout"
    cfg = json.loads(oa.openalice_mcp_json())
    srv = cfg["mcpServers"]["chantisimo"]
    assert srv["url"] == "http://127.0.0.1:8050/mcp"
    assert srv["headers"]["Authorization"] == "Bearer ${TRENDBOT_MCP_SERVER_TOKEN}"


# ------------------------------------------------------------------ Chantisimo MCP server
class FakeRuntime:
    def __init__(self, d):
        self.entries = {"main": (d, None, None)}
        self.names = ["main"]
        self.memes = SimpleNamespace(
            check=lambda q: {"verdict": "AVOID", "why": "mint on", "extra": 1, "q": q}
        )

    def summary(self):
        return {"bots": [{"name": "main", "running": True}], "running": 1}

    def snapshot(self, name):
        return {
            "meta": {"mode": "paper"},
            "capital": {"equity": 1000},
            "open_positions": [{"pair": "BTC/USDT", "entry": 100}],
        }

    def memory_bank(self):
        recs = [
            {"id": "a", "pair": "BTC/USDT", "outcome": "win", "lesson": "patience", "pnl": 5},
            {"id": "b", "pair": "ETH/USDT", "outcome": "loss", "lesson": "chased", "pnl": -2},
        ]
        return {"records": recs, "summary": {"trades": 2}}


@pytest.fixture
def server(tmp_path):
    return ms.McpServer(ms.default_tools(FakeRuntime(tmp_path)))


def rpc(server, method, params=None, mid=1):
    code, reply = server.handle(
        json.dumps({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}}).encode()
    )
    assert code == 200
    return reply


def call(server, name, args=None):
    return rpc(server, "tools/call", {"name": name, "arguments": args or {}})["result"]


def test_initialize_and_list(server):
    r = rpc(server, "initialize", {"protocolVersion": "2025-06-18"})["result"]
    assert r["serverInfo"]["name"] == "chantisimo" and "tools" in r["capabilities"]
    names = [t["name"] for t in rpc(server, "tools/list")["result"]["tools"]]
    assert names[:4] == ["bot_status", "open_positions", "search_memory", "meme_check"]
    writes = [
        t["name"]
        for t in rpc(server, "tools/list")["result"]["tools"]
        if not t["annotations"]["readOnlyHint"]
    ]
    assert writes == ["propose_trade"]


def test_notifications_and_errors(server):
    assert server.handle(b'{"jsonrpc":"2.0","method":"notifications/initialized"}') == (202, None)
    assert server.handle(b"{not json")[1]["error"]["code"] == -32700
    assert server.handle(b'{"id":1}')[1]["error"]["code"] == -32600
    assert rpc(server, "nope")["error"]["code"] == -32601
    assert rpc(server, "tools/call", {"name": "x"})["error"]["message"] == "unknown tool: x"
    code, batch = server.handle(
        json.dumps(
            [
                {"jsonrpc": "2.0", "id": 1, "method": "ping"},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
            ]
        ).encode()
    )
    assert code == 200 and batch == [{"jsonrpc": "2.0", "id": 1, "result": {}}]


def test_read_tools(server):
    assert call(server, "bot_status")["structuredContent"]["running"] == 1
    pos = call(server, "open_positions")["structuredContent"]
    assert pos["main"]["open_positions"][0]["pair"] == "BTC/USDT"
    found = call(server, "search_memory", {"query": "chased"})["structuredContent"]
    assert found["matches"] == 1 and found["records"][0]["id"] == "b"
    found = call(server, "search_memory", {"pair": "btc-usdt", "outcome": "win"})
    assert found["structuredContent"]["records"][0]["lesson"] == "patience"
    meme = call(server, "meme_check", {"address": "Mint1"})["structuredContent"]
    assert meme == {"verdict": "AVOID", "why": "mint on"}
    bad = call(server, "meme_check", {})
    assert bad["isError"] and "missing address" in bad["content"][0]["text"]


def test_propose_trade_files_a_suggestion_only(server, tmp_path):
    res = call(
        server,
        "propose_trade",
        {
            "pair": "sol-usdt",
            "side": "long",
            "entry": 150,
            "stop": 140,
            "take_profit": [170, 180],
            "reason": "5 confluences",
            "source": "openalice",
        },
    )
    filed = res["structuredContent"]["filed"]
    assert filed["pair"] == "SOL/USDT" and filed["status"] == "waiting for you"
    assert "no order was placed" in res["structuredContent"]["note"]
    bad = call(server, "propose_trade", {"pair": "X", "side": "long", "entry": 1, "stop": 2})
    assert bad["isError"] and "below the entry" in bad["content"][0]["text"]
    rows = ms.read_proposals(tmp_path)
    assert [r["pair"] for r in rows] == ["SOL/USDT"]
    assert call(server, "list_proposals")["content"][0]["text"].count("SOL/USDT") == 1


def test_token_is_made_once(monkeypatch, tmp_path):
    saved = {}
    monkeypatch.delenv(ms.TOKEN_ENV, raising=False)
    monkeypatch.setattr("research.trendbot.live_exchange.load_env_file", lambda *a, **k: None)
    monkeypatch.setattr("research.trendbot.connections.set_env_vars", lambda u: saved.update(u))
    tok = ms.ensure_token()
    assert len(tok) >= 40 and saved == {ms.TOKEN_ENV: tok}
    saved.clear()
    assert ms.ensure_token() == tok and saved == {}


def test_mcp_client_talks_to_the_server_end_to_end(server):
    """Chantisimo's own MCP client against the server over real loopback HTTP."""

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            if self.headers.get("Authorization") != "Bearer good-token-123456789012":
                self.send_response(401)
                self.end_headers()
                return
            code, reply = server.handle(self.rfile.read(int(self.headers["Content-Length"])))
            out = b"" if reply is None else json.dumps(reply).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{httpd.server_address[1]}/mcp"
    try:
        with pytest.raises(ConnectionSetupError, match="401"):
            McpClient(url, "wrong").initialize()
        c = McpClient(url, "good-token-123456789012")
        assert c.initialize()["serverInfo"]["name"] == "chantisimo"
        assert "search_memory" in [t["name"] for t in c.list_tools()]
        r = c.call_tool("bot_status", {})
        assert r["structuredContent"]["running"] == 1
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_analyze_tool_returns_the_card(server, monkeypatch):
    def fake(pair, exchange, **kw):
        return {
            "pair": pair,
            "source": exchange,
            "text": "NO TRADE: insufficient confluence",
            "result": {"decision": "no_trade", "signals": {"ema200": {"ok": False}}},
        }

    monkeypatch.setattr("research.trendbot.builder.trade_card", fake)
    out = call(server, "analyze", {"pair": "sol"})["structuredContent"]
    assert out["pair"] == "SOL/USDT" and out["card"].startswith("NO TRADE")
    assert "signals" not in out["result"]
    out = call(server, "analyze", {"pair": "eth/btc", "details": True, "exchange": "coinbase"})
    s = out["structuredContent"]
    assert s["pair"] == "ETH/BTC" and s["source"] == "coinbase" and "signals" in s["result"]


def test_bot_alerts_reach_the_openalice_inbox(tmp_path, alice, monkeypatch):
    from research.trendbot.scorecard import Alerter

    monkeypatch.setenv("TRENDBOT_CONNECTIONS_FILE", str(tmp_path / "connections.json"))
    a = Alerter({}, tmp_path / "bot1")
    a.emit("bot_started", "not sent: OpenAlice not set up yet")
    assert a.flush(3.0) and alice.pushed == []

    oa.configure(None, url=alice.url, workspace="ws9")  # one settings file per install
    assert (tmp_path / "openalice.json").exists()
    a = Alerter({}, tmp_path / "bot1")
    a.emit("network_down", "no exchange reply for 5 minutes")
    assert a.flush(3.0)
    assert alice.pushed == [("ws9", "**⚠ network down** · bot1\n\nno exchange reply for 5 minutes")]

    oa.configure(None, send={"alerts": False})
    a = Alerter({}, tmp_path / "bot1")
    a.emit("bot_error", "x")
    assert a.flush(3.0) and len(alice.pushed) == 1
    assert Alerter({}, tmp_path / "b", openalice=False).openalice is False
