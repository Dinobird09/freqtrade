"""The dashboard's newer buttons and pages: strategy builder, OpenAlice, arbitrage, MCP.

Kept out of dashboard.py so that file stays readable. Every function takes the dashboard's
``FleetRuntime`` and a request dict, and answers ``(HTTP status, JSON body)``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


Answer = tuple[int, dict[str, Any]]


# ------------------------------------------------------------------ strategy builder
def bots_folder(runtime: Any) -> tuple[Path, Path | None]:
    """Where a new bot file goes (next to the others) and the fleet file to add it to."""
    fleet = getattr(runtime, "fleet", None)
    fleet_path = getattr(fleet, "path", None) if fleet is not None else None
    if fleet is not None and fleet.bots:
        first = next(iter(fleet.bots.values())).settings_path
        if first and str(first) not in ("", "."):
            return Path(first).parent, fleet_path
    for _, _, ctrl in runtime.entries.values():
        sp = getattr(ctrl, "settings_path", None)
        if sp:
            return Path(sp).parent, fleet_path
    return Path("bots"), fleet_path


def _add_to_fleet(fleet_path: Path, name: str, settings: Path) -> str:
    from .journal import atomic_write_text
    from .live_bot import read_settings_json

    raw = read_settings_json(fleet_path)
    bots = list(raw.get("bots") or [])
    if len(bots) >= int(raw.get("max_bots", 10)):
        return f"the fleet is full ({len(bots)} bots): add it to {fleet_path.name} yourself"
    try:
        rel = settings.resolve().relative_to(fleet_path.resolve().parent)
    except ValueError:
        rel = settings.resolve()
    bots.append({"name": name, "settings": rel.as_posix()})
    raw["bots"] = bots
    atomic_write_text(fleet_path, json.dumps(raw, indent=2))
    return f"added to {fleet_path.name}: restart the dashboard to see it"


def builder_action(runtime: Any, req: Mapping[str, Any]) -> Answer:
    from . import builder

    a = str(req.get("action"))
    spec = req.get("spec") if isinstance(req.get("spec"), Mapping) else {}
    try:
        if a == "builder_backtest":
            return 200, {"ok": True, "message": "backtest done", "result": builder.backtest(spec)}
        if a == "builder_save":
            folder, fleet_path = bots_folder(runtime)
            out = builder.save_bot(spec, str(req.get("name", "")), folder)
            note = None
            if fleet_path is not None:
                note = _add_to_fleet(Path(fleet_path), out["settings"]["state_dir"].split("/")[-1],
                                     Path(out["path"]))  # fmt: skip
            return 200, {"ok": True, "message": f"saved {out['path']}", "path": out["path"],
                         "fleet": note}  # fmt: skip
    except (builder.BuilderError, ValueError, KeyError) as exc:
        return 400, {"ok": False, "message": str(exc)}
    return 400, {"ok": False, "message": f"unknown action {a!r}"}


# ------------------------------------------------------------------ OpenAlice
def openalice_view(runtime: Any) -> dict[str, Any]:
    from . import openalice
    from .mcp_server import read_proposals

    return {"status": openalice.status(None), "proposals": read_proposals(runtime.home)}


def openalice_action(runtime: Any, req: Mapping[str, Any]) -> Answer:
    from . import openalice
    from .connections import ConnectionSetupError

    a = str(req.get("action"))
    try:
        if a == "openalice_save":
            send = req.get("send") if isinstance(req.get("send"), Mapping) else None
            openalice.configure(None, url=req.get("url"), workspace=req.get("workspace"), send=send)
            return 200, {"ok": True, "message": "OpenAlice settings saved"}
        if a == "openalice_test":
            out = openalice.push_inbox(
                None, "**Chantisimo** is connected: trade cards and alerts will land here."
            )
            return 200, {"ok": True, "message": f"test message sent to {out['url']}"}
        if a == "openalice_connect":
            from .connections import env_path
            from .mcp_server import TOKEN_ENV, ensure_token

            tok = ensure_token()
            return 200, {
                "ok": True,
                "message": "save the block as .mcp.json in the OpenAlice workspace folder",
                "mcp_json": openalice.openalice_mcp_json(),
                "token_env": TOKEN_ENV,
                "token": tok,
                "env_file": str(env_path().resolve()),
            }
        if a == "proposal_dismiss":
            return 200, {"ok": True, "message": dismiss_proposal(runtime.home, str(req.get("id")))}
    except ConnectionSetupError as exc:
        return 400, {"ok": False, "message": str(exc)}
    return 400, {"ok": False, "message": f"unknown action {a!r}"}


def dismiss_proposal(home: Path, pid: str) -> str:
    from .journal import atomic_write_text

    p = Path(home) / "proposals.jsonl"
    rows = []
    found = False
    for line in p.read_text(encoding="utf-8").splitlines() if p.exists() else []:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("id") == pid and r.get("status") == "waiting for you":
            r["status"], found = "dismissed", True
        rows.append(r)
    if not found:
        return f"no waiting suggestion {pid}"
    atomic_write_text(p, "".join(json.dumps(r) + "\n" for r in rows))
    return f"suggestion {pid} dismissed"


# ------------------------------------------------------------------ MCP server
def mcp_tools(runtime: Any) -> list[Any]:
    """Chantisimo's MCP tools, plus the health scorecard."""
    from .mcp_server import Tool, default_tools

    def scorecard(a: Mapping[str, Any]) -> dict[str, Any]:
        names = [a["bot"]] if a.get("bot") in runtime.names else runtime.names
        out: dict[str, Any] = {n: runtime.snapshot(n).get("health") for n in names}
        out["_fleet"] = runtime.summary().get("diversification")
        return out

    extra = [
        Tool(
            "scorecard",
            "Bot health against the benchmarks: yearly / monthly P&L, uptime, turnover, matched "
            "orders, maker share, fees, volatility category; and the fleet's diversification.",
            scorecard,
            {"bot": {"type": "string"}},
        )
    ]
    return default_tools(runtime, extra)


_TOKEN: list[str] = []


def mcp_authorized(header: str) -> bool:
    import hmac

    from .mcp_server import ensure_token

    if not _TOKEN:
        _TOKEN.append(ensure_token())
    want = _TOKEN[0]
    got = header[7:].strip() if header.lower().startswith("bearer ") else ""
    return bool(got) and hmac.compare_digest(got, want)
