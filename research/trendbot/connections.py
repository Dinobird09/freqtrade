"""Connections set from the dashboard: exchange API keys and MCP servers (e.g. TradingView).

Exchange keys
    Saved to the local ``.env`` (``$TRENDBOT_ENV_FILE`` or ``./.env``, permissions 0600), never
    to the repository and never sent back to the page. One key set per exchange and account::

        TRENDBOT_BINANCE_API_KEY / _API_SECRET / _API_PASSWORD            live
        TRENDBOT_BINANCE_TESTNET_API_KEY / _API_SECRET / _API_PASSWORD    testnet

    The generic ``TRENDBOT_API_KEY`` / ``TRENDBOT_API_SECRET`` still work as a fallback.

MCP servers
    ``connections.json`` (next to ``.env``) lists MCP servers by name and URL. A server's
    access token, if it needs one, goes to ``.env`` as ``TRENDBOT_MCP_<NAME>_TOKEN``. The
    dashboard can test a server, list its tools, try a tool, and mark tool calls as a DATA
    SOURCE: the hourly ``collect`` job then calls them for every pair and stores the replies
    in ``<state_dir>/mcp/<server>.jsonl``, shown on the dashboard. MCP replies are free-form,
    so they are recorded and shown but never veto or open trades on their own.

The MCP client speaks the streamable-HTTP transport (JSON-RPC over POST, JSON or SSE replies)
with the standard library only.
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .models import base_of


EXCHANGES = ("binance", "coinbase")
ACCOUNTS = ("testnet", "live")
PRESETS = {
    "tradingview": {
        "url": "https://mcp.tradingview.com/mcp",
        "note": "TradingView MCP. If it asks you to sign in, paste the access token.",
    },
}
MCP_PROTOCOL = "2025-06-18"
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


class ConnectionSetupError(ValueError):
    """A connection could not be saved or used; the message is safe to show."""


# ---------------------------------------------------------------------- .env handling
def env_path() -> Path:
    return Path(os.environ.get("TRENDBOT_ENV_FILE") or ".env")


def connections_path() -> Path:
    return Path(
        os.environ.get("TRENDBOT_CONNECTIONS_FILE") or env_path().parent / "connections.json"
    )


def key_names(exchange: str, account: str) -> tuple[str, str, str]:
    """(key, secret, password) env names for one exchange account."""
    if exchange not in EXCHANGES or account not in ACCOUNTS:
        raise ConnectionSetupError(f"unknown exchange/account {exchange}/{account}")
    stem = f"TRENDBOT_{exchange.upper()}" + ("_TESTNET" if account == "testnet" else "")
    return f"{stem}_API_KEY", f"{stem}_API_SECRET", f"{stem}_API_PASSWORD"


def resolve_keys(exchange: str, account: str) -> tuple[str | None, str | None, str | None]:
    """Keys for this account: the exchange-specific names first, then the generic ones."""
    from .live_exchange import ENV_KEY, ENV_PASSWORD, ENV_SECRET

    names = key_names(exchange, account) if exchange in EXCHANGES else (None, None, None)
    env = os.environ
    key = (names[0] and env.get(names[0])) or env.get(ENV_KEY)
    secret = (names[1] and env.get(names[1])) or env.get(ENV_SECRET)
    password = (names[2] and env.get(names[2])) or env.get(ENV_PASSWORD)
    if secret and "-----BEGIN" in secret:  # a PEM private key stored on one line
        secret = secret.replace("\\n", "\n")
    return key or None, secret or None, password or None


def _clean(value: str) -> str:
    v = str(value).strip()
    if any(c in v for c in "\r\n\0\"'") or len(v) > 4096:
        raise ConnectionSetupError("a value may not contain quotes or line breaks")
    return v


def set_env_vars(updates: Mapping[str, str | None], path: Path | None = None) -> Path:
    """Set (or with None, remove) KEY=VALUE lines in .env; also updates this process."""
    p = path or env_path()
    for k in updates:
        if not _ENV_NAME.match(k):
            raise ConnectionSetupError(f"bad variable name {k!r}")
    clean = {
        k: (None if v is None or str(v).strip() == "" else _clean(v)) for k, v in updates.items()
    }
    lines = p.read_text(encoding="utf-8").splitlines() if p.exists() else []
    out, seen = [], set()
    for line in lines:
        name = line.split("=", 1)[0].strip().removeprefix("export ").strip() if "=" in line else ""
        if name in clean:
            seen.add(name)
            if clean[name] is not None:
                out.append(f"{name}={clean[name]}")
            continue
        out.append(line)
    out += [f"{k}={v}" for k, v in clean.items() if k not in seen and v is not None]
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out) + "\n")
    tmp.replace(p)
    try:
        p.chmod(0o600)
    except OSError:  # e.g. some Windows file systems
        pass
    for k, v in clean.items():  # bots started from this dashboard inherit the new values
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    return p


def exchange_status() -> list[dict[str, Any]]:
    """Which exchange accounts have keys (names only, never values)."""
    from .live_exchange import ENV_KEY, ENV_SECRET, load_env_file

    load_env_file()
    rows = []
    for ex in EXCHANGES:
        for acc in ACCOUNTS:
            k, s, pw = key_names(ex, acc)
            own = bool(os.environ.get(k)) and bool(os.environ.get(s))
            generic = bool(os.environ.get(ENV_KEY)) and bool(os.environ.get(ENV_SECRET))
            rows.append(
                {
                    "exchange": ex,
                    "account": acc,
                    "keys": "set" if own else "generic" if generic else "missing",
                    "password": bool(os.environ.get(pw)),
                    "env_names": [k, s, pw],
                }
            )
    return rows


def set_exchange_keys(
    exchange: str, account: str, key: str, secret: str, password: str = ""
) -> str:
    k, s, pw = key_names(exchange, account)
    if not str(key).strip() or not str(secret).strip():
        raise ConnectionSetupError("both the API key and the API secret are needed")
    # multi-line private keys (Coinbase) are stored on one line with literal \n
    secret = str(secret).strip().replace("\r\n", "\n").replace("\n", "\\n")
    path = set_env_vars({k: key, s: secret, pw: password or None})
    return f"{exchange} {account} keys saved to {path} (restart running bots to use them)"


def clear_exchange_keys(exchange: str, account: str) -> str:
    path = set_env_vars(dict.fromkeys(key_names(exchange, account)))
    return f"{exchange} {account} keys removed from {path}"


def test_exchange(exchange: str, account: str, ccxt_module: Any = None) -> str:
    """Log in with the saved keys and read the balance (nothing is traded)."""
    from .live_exchange import load_env_file

    load_env_file()
    key, secret, password = resolve_keys(exchange, account)
    if not key or not secret:
        raise ConnectionSetupError(f"no keys saved for {exchange} {account}")
    if ccxt_module is None:
        try:
            import ccxt as ccxt_module
        except ImportError as exc:
            raise ConnectionSetupError("ccxt is not installed: pip install ccxt") from exc
    params: dict[str, Any] = {"apiKey": key, "secret": secret, "enableRateLimit": True}
    if password:
        params["password"] = password
    ex = getattr(ccxt_module, exchange)(params)
    if account == "testnet":
        ex.set_sandbox_mode(True)
    try:
        bal = ex.fetch_balance()
    except Exception as exc:  # ccxt raises many types; show the message, never the keys
        raise ConnectionSetupError(
            f"{exchange} {account} login failed: {type(exc).__name__}: {exc}"
        ) from exc
    free = {c: v for c, v in (bal.get("free") or {}).items() if v}
    shown = ", ".join(f"{c} {v:g}" for c, v in sorted(free.items())[:6]) or "no free balance"
    return f"{exchange} {account}: connected, {len(free)} assets ({shown})"


# ---------------------------------------------------------------------- MCP client
class McpClient:
    """Minimal MCP client over streamable HTTP (initialize, tools/list, tools/call)."""

    def __init__(self, url: str, token: str | None = None, timeout: float = 20.0) -> None:
        if not (
            url.startswith("https://") or url.startswith(("http://127.0.0.1", "http://localhost"))
        ):
            raise ConnectionSetupError(
                "MCP URL must be https:// (or http://127.0.0.1 for a local server)"
            )
        self.url = url
        self.token = token
        self.timeout = timeout
        self.session: str | None = None
        self.server: dict[str, Any] = {}
        self._id = 0

    def _post(self, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": MCP_PROTOCOL,
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        req = urllib.request.Request(  # noqa: S310 - URL scheme checked in __init__
            self.url, data=json.dumps(payload).encode(), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310
                self.session = r.headers.get("Mcp-Session-Id") or self.session
                ctype = r.headers.get("Content-Type", "")
                body = r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise ConnectionSetupError(
                    f"the server refused access (HTTP {exc.code}): it needs a sign-in; paste "
                    "an access token for it"
                ) from exc
            raise ConnectionSetupError(f"HTTP {exc.code} from the MCP server") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ConnectionSetupError(f"could not reach the MCP server: {exc}") from exc
        if "id" not in payload:
            return None  # a notification: no reply expected
        msg = _pick_reply(body, ctype, payload["id"])
        if msg is None:
            raise ConnectionSetupError("the MCP server sent no reply")
        if "error" in msg:
            err = msg["error"]
            raise ConnectionSetupError(f"MCP error {err.get('code')}: {err.get('message')}")
        return msg.get("result") or {}

    def _call(self, method: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        self._id += 1
        res = self._post(
            {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}}
        )
        return res or {}

    def initialize(self) -> dict[str, Any]:
        res = self._call(
            "initialize",
            {
                "protocolVersion": MCP_PROTOCOL,
                "capabilities": {},
                "clientInfo": {"name": "trendbot", "version": "1"},
            },
        )
        self.server = dict(res.get("serverInfo") or {})
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return res

    def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor = None
        for _ in range(20):
            res = self._call("tools/list", {"cursor": cursor} if cursor else {})
            tools += list(res.get("tools") or [])
            cursor = res.get("nextCursor")
            if not cursor:
                break
        return tools

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
        return self._call("tools/call", {"name": name, "arguments": dict(arguments)})


def _pick_reply(body: str, ctype: str, want_id: Any) -> dict[str, Any] | None:
    msgs: list[Any] = []
    if "text/event-stream" in ctype:
        data: list[str] = []
        for line in body.splitlines() + [""]:
            if line.startswith("data:"):
                data.append(line[5:].strip())
            elif not line.strip() and data:
                try:
                    msgs.append(json.loads("\n".join(data)))
                except ValueError:
                    pass
                data = []
    elif body.strip():
        parsed = json.loads(body)
        msgs = parsed if isinstance(parsed, list) else [parsed]
    for m in msgs:
        if isinstance(m, dict) and m.get("id") == want_id:
            return m
    return None


def tool_text(result: Mapping[str, Any], limit: int = 20_000) -> str:
    """The text content of a tools/call result (structured content as JSON)."""
    if result.get("structuredContent") is not None:
        return json.dumps(result["structuredContent"])[:limit]
    parts = [c.get("text", "") for c in result.get("content") or [] if c.get("type") == "text"]
    return "\n".join(parts)[:limit]


# ---------------------------------------------------------------------- MCP registry
def load_connections(path: Path | None = None) -> dict[str, Any]:
    p = path or connections_path()
    raw = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    raw.setdefault("mcp", {})
    return raw


def save_connections(data: Mapping[str, Any], path: Path | None = None) -> None:
    p = path or connections_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    tmp.replace(p)


def token_env(name: str) -> str:
    return f"TRENDBOT_MCP_{name.upper().replace('-', '_')}_TOKEN"


def _check_name(name: str) -> str:
    n = str(name).strip().lower()
    if not _NAME.match(n):
        raise ConnectionSetupError("name: lowercase letters, digits, - and _ (max 32)")
    return n


def save_mcp(name: str, url: str, token: str | None = None, path: Path | None = None) -> str:
    n = _check_name(name)
    McpClient(url)  # validates the URL
    data = load_connections(path)
    entry = data["mcp"].setdefault(n, {"sources": []})
    entry["url"] = url.strip()
    entry["token_env"] = token_env(n)
    if token is not None and str(token).strip():
        set_env_vars({token_env(n): token})
    save_connections(data, path)
    return f"MCP server {n} saved"


def remove_mcp(name: str, path: Path | None = None) -> str:
    n = _check_name(name)
    data = load_connections(path)
    if data["mcp"].pop(n, None) is None:
        raise ConnectionSetupError(f"no MCP server named {n}")
    save_connections(data, path)
    set_env_vars({token_env(n): None})
    return f"MCP server {n} removed"


def client_for(name: str, path: Path | None = None) -> McpClient:
    from .live_exchange import load_env_file

    load_env_file()
    entry = load_connections(path)["mcp"].get(_check_name(name))
    if entry is None:
        raise ConnectionSetupError(f"no MCP server named {name}")
    c = McpClient(entry["url"], os.environ.get(entry.get("token_env") or token_env(name)))
    c.initialize()
    return c


def test_mcp(name: str, path: Path | None = None) -> dict[str, Any]:
    c = client_for(name, path)
    tools = c.list_tools()
    data = load_connections(path)
    entry = data["mcp"][_check_name(name)]
    entry["last_test"] = {
        "ok": True,
        "when": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "server": c.server,
        "tools": [
            {"name": t.get("name"), "description": (t.get("description") or "")[:300]}
            for t in tools
        ],
    }
    save_connections(data, path)
    return {"server": c.server, "tools": entry["last_test"]["tools"]}


def set_mcp_source(
    name: str,
    tool: str,
    args: Mapping[str, Any],
    per_pair: bool,
    enabled: bool,
    path: Path | None = None,
) -> str:
    """Add / update / remove (enabled=False) a tool call collected by the hourly job."""
    n = _check_name(name)
    data = load_connections(path)
    entry = data["mcp"].get(n)
    if entry is None:
        raise ConnectionSetupError(f"no MCP server named {n}")
    srcs = [s for s in entry.get("sources", []) if s.get("tool") != tool]
    if enabled:
        srcs.append({"tool": str(tool), "args": dict(args), "per_pair": bool(per_pair)})
    entry["sources"] = srcs
    save_connections(data, path)
    return f"{n}.{tool}: " + ("collected hourly" if enabled else "no longer collected")


def fill_args(args: Any, pair: str | None, exchange: str) -> Any:
    """Replace {pair} {base} {quote} {symbol} {exchange} placeholders in string values."""
    if isinstance(args, dict):
        return {k: fill_args(v, pair, exchange) for k, v in args.items()}
    if isinstance(args, list):
        return [fill_args(v, pair, exchange) for v in args]
    if not isinstance(args, str) or pair is None:
        return args
    base, quote = base_of(pair), pair.split("/", 1)[-1].split(":", 1)[0]
    return (
        args.replace("{pair}", pair)
        .replace("{base}", base)
        .replace("{quote}", quote)
        .replace("{symbol}", f"{exchange.upper()}:{base}{quote}")
        .replace("{exchange}", exchange.upper())
    )


def call_mcp(
    name: str,
    tool: str,
    args: Mapping[str, Any],
    pair: str | None = None,
    exchange: str = "binance",
    path: Path | None = None,
) -> str:
    c = client_for(name, path)
    return tool_text(c.call_tool(tool, fill_args(dict(args), pair, exchange)))


def collect(state_dir: Path, settings: Mapping[str, Any], fetch: Any = None) -> dict[str, Any]:
    """The hourly job: call every MCP data source, append replies to mcp/<server>.jsonl."""
    pairs: Sequence[str] = settings.get("pairs") or []
    exchange = str(settings.get("exchange") or "binance")
    data = load_connections(settings.get("connections_file") and Path(settings["connections_file"]))
    out: dict[str, Any] = {}
    for name, entry in sorted(data["mcp"].items()):
        srcs = entry.get("sources") or []
        if not srcs:
            continue
        try:
            c = client_for(
                name, settings.get("connections_file") and Path(settings["connections_file"])
            )
        except ConnectionSetupError as exc:
            out[name] = {"ok": False, "error": str(exc)}
            continue
        rows = []
        for s in srcs:
            for pair in pairs if s.get("per_pair") else [None]:
                try:
                    text = tool_text(
                        c.call_tool(s["tool"], fill_args(s.get("args") or {}, pair, exchange))
                    )
                    rows.append(
                        {
                            "ts": int(time.time() * 1000),
                            "tool": s["tool"],
                            "pair": pair,
                            "text": text,
                        }
                    )
                except ConnectionSetupError as exc:
                    rows.append(
                        {
                            "ts": int(time.time() * 1000),
                            "tool": s["tool"],
                            "pair": pair,
                            "error": str(exc),
                        }
                    )
        d = Path(state_dir) / "mcp"
        d.mkdir(parents=True, exist_ok=True)
        with (d / f"{name}.jsonl").open("a", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        out[name] = {"ok": True, "calls": len(rows), "errors": sum("error" in r for r in rows)}
    return out


def latest_mcp_rows(
    state_dir: Path, per_key: int = 1, max_bytes: int = 400_000
) -> list[dict[str, Any]]:
    """The newest reply per (server, tool, pair) for the dashboard."""
    d = Path(state_dir) / "mcp"
    rows: dict[tuple[str, str, Any], dict[str, Any]] = {}
    for f in sorted(d.glob("*.jsonl")) if d.exists() else []:
        with f.open("rb") as fh:
            fh.seek(max(0, f.stat().st_size - max_bytes))
            chunk = fh.read().decode("utf-8", "replace").splitlines()
        for line in chunk:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            rows[(f.stem, r.get("tool"), r.get("pair"))] = {"server": f.stem, **r}
    return sorted(
        rows.values(), key=lambda r: (r["server"], r.get("tool") or "", r.get("pair") or "")
    )


def status() -> dict[str, Any]:
    """Everything the Connections card shows (no secret values)."""
    from .live_exchange import load_env_file

    load_env_file()
    data = load_connections()
    mcp = []
    for name, e in sorted(data["mcp"].items()):
        lt = e.get("last_test") or {}
        mcp.append(
            {
                "name": name,
                "url": e.get("url"),
                "token": bool(os.environ.get(e.get("token_env") or token_env(name))),
                "token_env": e.get("token_env") or token_env(name),
                "sources": e.get("sources", []),
                "tested": lt.get("when"),
                "server": lt.get("server"),
                "tools": lt.get("tools", []),
            }
        )
    return {
        "env_file": str(env_path().resolve()),
        "exchanges": exchange_status(),
        "mcp": mcp,
        "presets": PRESETS,
    }
