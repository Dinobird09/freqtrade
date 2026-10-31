"""Chantisimo's own MCP server: lets OpenAlice (or any MCP app on this computer) ask the bot.

The dashboard answers MCP at ``POST /mcp`` (streamable HTTP, plain JSON replies). Every call
needs ``Authorization: Bearer <token>``; the token is made once and kept in the bot's ``.env``
as ``TRENDBOT_MCP_SERVER_TOKEN`` (``trendbot.pyz openalice`` prints where to find it). The
dashboard only listens on 127.0.0.1, so nothing outside this computer can reach it.

The tools read: status, open positions, the memory bank, meme checks, trade cards and the
health scorecard. One tool writes: ``propose_trade`` files a suggestion that waits for *you*
on the dashboard. No tool places an order, moves money or touches a wallet.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any


PROTOCOL = "2025-06-18"
TOKEN_ENV = "TRENDBOT_MCP_SERVER_TOKEN"  # noqa: S105 - the variable name, not a value
SERVER_INFO = {"name": "chantisimo", "title": "Chantisimo (trendbot)", "version": "1"}
INSTRUCTIONS = (
    "Chantisimo is a crypto trading bot on this computer. Use bot_status first, then "
    "open_positions, search_memory or analyze. propose_trade only files a suggestion for the "
    "human to approve on the dashboard; nothing here places orders."
)


class Tool:
    def __init__(
        self,
        name: str,
        description: str,
        func: Callable[[Mapping[str, Any]], Any],
        properties: Mapping[str, Any] | None = None,
        required: tuple[str, ...] = (),
        read_only: bool = True,
    ) -> None:
        self.name, self.description, self.func = name, description, func
        self.schema = {"type": "object", "properties": dict(properties or {})}
        if required:
            self.schema["required"] = list(required)
        self.read_only = read_only

    def listing(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.schema,
            "annotations": {"readOnlyHint": self.read_only, "openWorldHint": False},
        }


# ------------------------------------------------------------------------------ token
def ensure_token() -> str:
    """The server's bearer token: read from .env, or made and saved there the first time."""
    from .connections import set_env_vars
    from .live_exchange import load_env_file

    load_env_file()
    tok = os.environ.get(TOKEN_ENV, "").strip()
    if len(tok) < 20:
        tok = secrets.token_urlsafe(32)
        set_env_vars({TOKEN_ENV: tok})
        os.environ[TOKEN_ENV] = tok
    return tok


# ------------------------------------------------------------------------------ tools
_STR = {"type": "string"}
_NUM = {"type": "number"}
MEMORY_FIELDS = (
    "id", "bot", "mode", "strategy", "pair", "outcome", "entry_utc", "exit_utc", "entry",
    "exit", "stop", "target", "pnl", "r", "exit_text", "lesson", "mistakes", "note", "tags",
)  # fmt: skip


def _search_memory(runtime: Any, a: Mapping[str, Any]) -> dict[str, Any]:
    bank = runtime.memory_bank()
    q = str(a.get("query") or "").lower().strip()
    pair = str(a.get("pair") or "").upper().replace("-", "/").strip()
    outcome = str(a.get("outcome") or "").lower().strip()
    rows = []
    for r in bank.get("records") or []:
        if pair and pair not in str(r.get("pair") or "").upper():
            continue
        if outcome and outcome != str(r.get("outcome") or "").lower():
            continue
        if q and q not in json.dumps(r, default=str).lower():
            continue
        rows.append({k: r.get(k) for k in MEMORY_FIELDS if r.get(k) not in (None, "", [])})
    limit = max(1, min(int(a.get("limit") or 20), 100))
    return {"matches": len(rows), "records": rows[:limit], "summary": bank.get("summary")}


def _positions(runtime: Any, a: Mapping[str, Any]) -> dict[str, Any]:
    names = [a["bot"]] if a.get("bot") else runtime.names
    out = {}
    for n in names:
        snap = runtime.snapshot(n)
        out[n] = {
            "mode": (snap.get("meta") or {}).get("mode"),
            "capital": snap.get("capital"),
            "open_positions": snap.get("open_positions") or [],
        }
    return out


def _meme_check(runtime: Any, a: Mapping[str, Any]) -> dict[str, Any]:
    rep = runtime.memes.check({"chain": a.get("chain") or "solana", "address": a["address"]})
    keep = ("verdict", "why", "token", "six", "price", "market_cap", "liquidity_usd")
    return {k: rep.get(k) for k in keep if k in rep} or rep


def _proposals_path(runtime: Any) -> Path:
    return Path(runtime.entries[runtime.names[0]][0]) / "proposals.jsonl"


def _propose(runtime: Any, a: Mapping[str, Any]) -> dict[str, Any]:
    side = str(a.get("side") or "long").lower()
    if side not in ("long", "short"):
        raise ValueError("side must be long or short")
    rec = {
        "id": secrets.token_hex(4),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pair": str(a["pair"]).upper().replace("-", "/")[:24],
        "side": side,
        "entry": float(a["entry"]),
        "stop": float(a["stop"]),
        "take_profit": [float(x) for x in (a.get("take_profit") or [])][:3],
        "reason": str(a.get("reason") or "")[:2000],
        "source": str(a.get("source") or "mcp")[:40],
        "status": "waiting for you",
    }
    if (rec["stop"] < rec["entry"]) != (side == "long"):
        raise ValueError("the stop must sit below the entry for a long, above for a short")
    p = _proposals_path(runtime)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")
    return {"filed": rec, "note": "waiting for the human on the dashboard; no order was placed"}


def read_proposals(state_dir: str | Path, limit: int = 20) -> list[dict[str, Any]]:
    p = Path(state_dir) / "proposals.jsonl"
    if not p.exists():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows[::-1]


def _analyze(runtime: Any, a: Mapping[str, Any]) -> dict[str, Any]:
    from .builder import trade_card

    bot = runtime.names[0]
    snap = runtime.snapshot(bot)
    pair = str(a["pair"]).upper().replace("-", "/")
    if "/" not in pair:
        pair += "/" + ((snap.get("meta") or {}).get("quote") or "USDT")
    card = trade_card(
        pair,
        str(a.get("exchange") or (snap.get("meta") or {}).get("exchange") or "binance"),
        state_dir=runtime.entries[bot][0],
        equity=(snap.get("capital") or {}).get("equity"),
    )
    res = dict(card["result"])
    res.pop("signals", None) if not a.get("details") else None
    return {"pair": card["pair"], "source": card["source"], "card": card["text"], "result": res}


def default_tools(runtime: Any, extra: list[Tool] | None = None) -> list[Tool]:
    """The tools over one dashboard runtime (``FleetRuntime``)."""
    tools = [
        Tool(
            "bot_status",
            "Every bot: running or not, mode (paper/live/wallet), equity, open trades, health.",
            lambda a: runtime.summary(),
        ),
        Tool(
            "open_positions",
            "Open positions with entry, stop, target, live price and unrealised PnL.",
            lambda a: _positions(runtime, a),
            {"bot": _STR},
        ),
        Tool(
            "search_memory",
            "Search the memory bank of past trades (text, pair, outcome win/loss/open).",
            lambda a: _search_memory(runtime, a),
            {"query": _STR, "pair": _STR, "outcome": _STR, "limit": {"type": "integer"}},
        ),
        Tool(
            "meme_check",
            "Run the meme desk's six-point safety check on one token (chain + address).",
            lambda a: _meme_check(runtime, a),
            {"chain": {"type": "string", "enum": ["solana", "ethereum", "base", "bsc"]},
             "address": _STR},
            ("address",),
        ),
        Tool(
            "analyze",
            "Confluence trade card for a coin now (1D bias, 4H structure, 1H signals): entry, "
            "stop, TP1/TP2, R:R, factors, confidence; or NO TRADE with the reasons.",
            lambda a: _analyze(runtime, a),
            {"pair": {"type": "string", "description": "BTC/USDT or just BTC"},
             "exchange": {"type": "string", "enum": ["binance", "binanceus", "coinbase"]},
             "details": {"type": "boolean", "description": "include every signal's detail"}},
            ("pair",),
        ),
        Tool(
            "list_proposals",
            "Trade suggestions filed with propose_trade and their status.",
            lambda a: read_proposals(runtime.entries[runtime.names[0]][0]),
        ),
        Tool(
            "propose_trade",
            "File a trade suggestion for the human to approve on the dashboard (no order).",
            lambda a: _propose(runtime, a),
            {"pair": _STR, "side": {"type": "string", "enum": ["long", "short"]},
             "entry": _NUM, "stop": _NUM, "take_profit": {"type": "array", "items": _NUM},
             "reason": _STR, "source": _STR},
            ("pair", "entry", "stop"),
            read_only=False,
        ),
    ]  # fmt: skip
    return tools + list(extra or [])


# ------------------------------------------------------------------------------ protocol
class McpServer:
    """JSON-RPC over HTTP: ``handle(body)`` -> (HTTP status, reply dict or None)."""

    def __init__(self, tools: list[Tool]) -> None:
        self.tools = {t.name: t for t in tools}

    def _result(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        if method == "initialize":
            return {
                "protocolVersion": PROTOCOL,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
                "instructions": INSTRUCTIONS,
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": [t.listing() for t in self.tools.values()]}
        if method == "tools/call":
            return self._call(str(params.get("name")), params.get("arguments") or {})
        raise LookupError(method)

    def _call(self, name: str, args: Mapping[str, Any]) -> dict[str, Any]:
        tool = self.tools.get(name)
        if tool is None:
            raise KeyError(name)
        missing = [k for k in tool.schema.get("required", []) if args.get(k) in (None, "")]
        try:
            if missing:
                raise ValueError(f"missing {', '.join(missing)}")
            value = tool.func(args)
        except Exception as exc:  # a tool error is a result the agent can read, not a crash
            return {"content": [{"type": "text", "text": f"error: {exc}"}], "isError": True}
        text = json.dumps(value, default=str, indent=1)
        out: dict[str, Any] = {"content": [{"type": "text", "text": text[:200_000]}]}
        if isinstance(value, dict):
            out["structuredContent"] = json.loads(json.dumps(value, default=str))
        return out

    def handle(self, body: bytes) -> tuple[int, Any]:
        try:
            msg = json.loads(body or b"null")
        except ValueError:
            return 400, _error(None, -32700, "parse error")
        if isinstance(msg, list):  # a batch
            replies = [r for m in msg if (r := self._one(m)) is not None]
            return (200, replies) if replies else (202, None)
        reply = self._one(msg)
        return (200, reply) if reply is not None else (202, None)

    def _one(self, msg: Any) -> dict[str, Any] | None:
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or "method" not in msg:
            return _error(msg.get("id") if isinstance(msg, dict) else None, -32600, "bad request")
        if "id" not in msg:
            return None  # a notification (notifications/initialized, ...)
        try:
            result = self._result(str(msg["method"]), msg.get("params") or {})
        except LookupError as exc:
            kind = "tool" if isinstance(exc, KeyError) else "method"
            return _error(msg["id"], -32601 if kind == "method" else -32602,
                          f"unknown {kind}: {exc.args[0]}")  # fmt: skip
        return {"jsonrpc": "2.0", "id": msg["id"], "result": result}


def _error(mid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}
