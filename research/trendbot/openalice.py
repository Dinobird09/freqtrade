"""The bridge to OpenAlice, the AI trading workspace app, running on the same computer.

OpenAlice (https://github.com/Dinobird09/OpenAlice) is a separate program with its own
licence (AGPL-3.0), so none of its code is copied here. The two talk over MCP, the open tool
protocol, on 127.0.0.1 only:

* **Trendbot -> OpenAlice.** Trade cards, "NO TRADE" verdicts and alerts go to an OpenAlice
  workspace's Inbox through its ``inbox_push`` tool. OpenAlice serves MCP at
  ``http://127.0.0.1:3001/mcp`` once you turn MCP on in its settings; each workspace has its
  own address, ``http://127.0.0.1:3001/mcp/<workspace id>``, and the Inbox lives there.
* **OpenAlice -> Trendbot.** The dashboard serves Chantisimo's own MCP server at
  ``http://127.0.0.1:8050/mcp`` (see ``mcp_server.py``). ``openalice_mcp_json()`` prints the
  ``.mcp.json`` block to paste into an OpenAlice workspace folder so its agent can read the
  bot's status, positions, trade memory and trade cards.

Settings live in ``openalice.json`` next to ``connections.json`` (one per install). Nothing
here needs an API key: OpenAlice's MCP server only answers this computer.
"""

from __future__ import annotations

import argparse
import json
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .connections import ConnectionSetupError, McpClient, tool_text


DEFAULT_MCP = "http://127.0.0.1:3001/mcp"
WEB_UI = "http://127.0.0.1:47331"
SETTINGS_FILE = "openalice.json"
EVENTS = ("trades", "no_trade", "alerts")  # what may be sent to the Inbox
DEFAULTS: dict[str, Any] = {
    "url": DEFAULT_MCP,
    "workspace": "",
    "send": {"trades": True, "no_trade": False, "alerts": True},
}
_lock = threading.Lock()

ClientFactory = Callable[[str], Any]


def _client(url: str) -> McpClient:
    return McpClient(url, timeout=8.0)


def settings_path(state_dir: str | Path | None) -> Path:
    """One file per install, next to connections.json (``state_dir`` overrides, for tests)."""
    if state_dir is None:
        from .connections import connections_path

        return connections_path().with_name(SETTINGS_FILE)
    return Path(state_dir) / SETTINGS_FILE


def load(state_dir: str | Path | None) -> dict[str, Any]:
    p = settings_path(state_dir)
    try:
        raw = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        raw = {}
    out = {**DEFAULTS, **{k: v for k, v in raw.items() if k in DEFAULTS or k == "last"}}
    out["send"] = {**DEFAULTS["send"], **(raw.get("send") or {})}
    return out


def _save(state_dir: str | Path | None, data: Mapping[str, Any]) -> None:
    from .journal import atomic_write_text

    p = settings_path(state_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(p, json.dumps(data, indent=1))


def _check_url(url: str) -> str:
    u = str(url or "").strip().rstrip("/") or DEFAULT_MCP
    if not u.startswith(("http://127.0.0.1", "http://localhost")):
        raise ConnectionSetupError(
            "OpenAlice runs on this computer: its address starts with http://127.0.0.1"
        )
    return u


def _check_workspace(ws: str) -> str:
    w = str(ws or "").strip()
    if w and not all(c.isalnum() or c in "-_" for c in w):
        raise ConnectionSetupError("workspace id: letters, digits, - and _ only")
    return w[:80]


def configure(
    state_dir: str | Path | None,
    url: str | None = None,
    workspace: str | None = None,
    send: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Save the OpenAlice address, the workspace whose Inbox gets messages, and what to send."""
    with _lock:
        cfg = load(state_dir)
        if url is not None:
            cfg["url"] = _check_url(url)
        if workspace is not None:
            cfg["workspace"] = _check_workspace(workspace)
        for k, v in (send or {}).items():
            if k in EVENTS:
                cfg["send"][k] = bool(v)
        _save(state_dir, cfg)
        return cfg


def inbox_url(cfg: Mapping[str, Any]) -> str | None:
    ws = cfg.get("workspace")
    return f"{_check_url(cfg.get('url') or DEFAULT_MCP)}/{ws}" if ws else None


def _remember(state_dir: str | Path | None, **last: Any) -> None:
    with _lock:
        cfg = load(state_dir)
        cfg["last"] = {**(cfg.get("last") or {}), **last}
        _save(state_dir, cfg)


def status(state_dir: str | Path | None, client: ClientFactory = _client) -> dict[str, Any]:
    """Is OpenAlice running with MCP on? Which tools does it offer? (Sends nothing.)"""
    cfg = load(state_dir)
    out: dict[str, Any] = {
        "url": cfg["url"],
        "workspace": cfg["workspace"],
        "inbox_url": inbox_url(cfg),
        "send": cfg["send"],
        "web_ui": WEB_UI,
        "last": cfg.get("last") or {},
        "chantisimo_mcp": "http://127.0.0.1:8050/mcp",
    }
    try:
        c = client(cfg["url"])
        c.initialize()
        tools = c.list_tools()
    except ConnectionSetupError as exc:
        out.update(
            reachable=False,
            error=str(exc),
            hint=(
                "Start OpenAlice, open Settings and turn on the MCP server (port 3001), then "
                "press Check again."
            ),
        )
        return out
    out.update(
        reachable=True,
        server=getattr(c, "server", {}),
        tools=sorted(str(t.get("name")) for t in tools if t.get("name")),
    )
    if cfg["workspace"]:
        try:
            w = client(inbox_url(cfg) or "")
            w.initialize()
            names = {t.get("name") for t in w.list_tools()}
            out["inbox_ready"] = "inbox_push" in names
            if not out["inbox_ready"]:
                out["inbox_hint"] = "that workspace offers no inbox_push tool: check the id"
        except ConnectionSetupError as exc:
            out.update(inbox_ready=False, inbox_hint=str(exc))
    else:
        out["inbox_ready"] = False
        out["inbox_hint"] = "paste a workspace id to send trade cards to its Inbox"
    return out


def push_inbox(
    state_dir: str | Path | None, body: str, client: ClientFactory = _client
) -> dict[str, Any]:
    """Post one Markdown message to the chosen workspace's Inbox (raises when it can't)."""
    cfg = load(state_dir)
    url = inbox_url(cfg)
    if not url:
        raise ConnectionSetupError("no OpenAlice workspace chosen yet (paste its id)")
    text = str(body or "").strip()
    if not text:
        raise ConnectionSetupError("nothing to send")
    c = client(url)
    c.initialize()
    res = c.call_tool("inbox_push", {"body": text[:20_000]})
    if res.get("isError"):
        raise ConnectionSetupError(f"OpenAlice refused the message: {tool_text(res, 300)}")
    reply: dict[str, Any] = {}
    try:
        reply = json.loads(tool_text(res)) if tool_text(res).startswith("{") else {}
    except ValueError:
        pass
    _remember(state_dir, sent=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), error=None)
    return {"ok": True, "entry": reply.get("entryId"), "url": url}


def notify(
    state_dir: str | Path | None, event: str, body: str, client: ClientFactory = _client
) -> str | None:
    """Send ``body`` when that kind of message is switched on; never raises (the bot goes on).

    Returns None when sent or not wanted, else the reason it could not be sent.
    """
    cfg = load(state_dir)
    if event not in EVENTS or not cfg["send"].get(event) or not cfg["workspace"]:
        return None
    try:
        push_inbox(state_dir, body, client)
    except ConnectionSetupError as exc:
        _remember(state_dir, error=str(exc), failed=time.strftime("%Y-%m-%dT%H:%M:%SZ"))
        return str(exc)
    return None


def card_markdown(card: Mapping[str, Any] | str, bot: str | None = None) -> str:
    """A trade card (dict from ``confluence.analyze`` or its formatted text) as Inbox Markdown."""
    head = f"**Chantisimo{f' · {bot}' if bot else ''}**"
    if isinstance(card, str):
        return f"{head}\n\n```\n{card.strip()}\n```"
    if card.get("text"):
        return f"{head}\n\n```\n{str(card['text']).strip()}\n```"
    lines = [head, ""]
    for k, v in card.items():
        if isinstance(v, (list, tuple)):
            v = ", ".join(map(str, v))
        lines.append(f"- **{str(k).replace('_', ' ').title()}:** {v}")
    return "\n".join(lines)


def alert_markdown(title: str, detail: str = "", bot: str | None = None) -> str:
    return f"**⚠ {title}**{f' · {bot}' if bot else ''}\n\n{detail}".strip()


def openalice_mcp_json(dashboard: str = "http://127.0.0.1:8050") -> str:
    """The ``.mcp.json`` block for an OpenAlice workspace folder (the token stays a variable).

    Claude Code inside OpenAlice expands ``${TRENDBOT_MCP_SERVER_TOKEN}`` from the environment,
    so the real token is never written into the file.
    """
    return json.dumps(
        {
            "mcpServers": {
                "chantisimo": {
                    "type": "http",
                    "url": f"{dashboard.rstrip('/')}/mcp",
                    "headers": {"Authorization": "Bearer ${TRENDBOT_MCP_SERVER_TOKEN}"},
                }
            }
        },
        indent=2,
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="trendbot openalice", description="the OpenAlice link")
    p.add_argument("--state-dir", help=argparse.SUPPRESS)  # tests: another settings folder
    p.add_argument("--workspace", help="save the OpenAlice workspace id for the Inbox")
    p.add_argument("--url", help=f"OpenAlice's MCP address (default {DEFAULT_MCP})")
    p.add_argument("--test", action="store_true", help="send a test message to the Inbox")
    p.add_argument("--mcp-json", action="store_true", help="print the .mcp.json for OpenAlice")
    a = p.parse_args(argv)
    if a.mcp_json:
        from .connections import env_path
        from .mcp_server import TOKEN_ENV, ensure_token

        ensure_token()
        print(openalice_mcp_json())
        print(f"\nThe token is {TOKEN_ENV} in {env_path().resolve()}")
        return 0
    try:
        if a.workspace is not None or a.url is not None:
            configure(a.state_dir, url=a.url, workspace=a.workspace)
        st = status(a.state_dir)
        if not st["reachable"]:
            print(f"OpenAlice: not reachable at {st['url']} ({st['error']}). {st['hint']}")
            return 1
        print(f"OpenAlice: reachable at {st['url']}, {len(st['tools'])} tools")
        print(f"Inbox: {'ready' if st['inbox_ready'] else st.get('inbox_hint')}")
        if a.test:
            out = push_inbox(a.state_dir, "**Chantisimo** is connected. Trade cards land here.")
            print(f"test message sent ({out['url']})")
    except ConnectionSetupError as exc:
        print(f"OpenAlice: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
