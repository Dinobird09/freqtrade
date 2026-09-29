"""Web dashboard for the live bot: reads its ``state_dir`` and serves a read-only page.

Usage::

    python3 -m research.trendbot.dashboard --state-dir trendbot_state/binance-live
    python3 -m research.trendbot.dashboard --settings bot.json --port 8050
    python3 -m research.trendbot.dashboard --settings bot.json --export dashboard.html

The server binds to 127.0.0.1 by default and has no write endpoints: it only reads
``journal.csv``, ``state.json``, ``decisions.csv``, ``candles/`` and ``bot.log``, so it never
needs exchange keys and can run beside the bot (or on a copy of its state dir). The page
refreshes itself every ``--refresh`` seconds. ``--export`` writes one self-contained HTML
file with the current snapshot embedded.
"""

from __future__ import annotations

import argparse
import hmac
import json
import math
import os
import secrets
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .adoption_evidence import read_decisions_log
from .adoption_record import config_from_overrides
from .config import StrategyConfig
from .data import load_candles_csv
from .indicators import compute_features
from .journal import iso_to_ms, read_journal
from .journal_rules import audit
from .models import DAY_MS, Candle, Trade


HTML_PATH = Path(__file__).with_name("dashboard.html")
SNAPSHOT_TOKEN = "/*__SNAPSHOT__*/null"  # noqa: S105 - template marker


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _num(x: float | None, nd: int = 6) -> float | None:
    if x is None or not math.isfinite(x):
        return None
    return round(float(x), nd)


def _max_drawdown_pct(values: Sequence[float]) -> float:
    peak, worst = -math.inf, 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, (peak - v) / peak * 100.0)
    return worst


def _stats(closed: Sequence[Trade], start: float, equity: float) -> dict[str, Any]:
    rs = [t.r_multiple for t in closed if t.r_multiple is not None]
    wins = [t for t in closed if (t.pnl or 0) > 0]
    gross_win = sum(t.pnl for t in wins)  # type: ignore[misc]
    gross_loss = -sum(t.pnl for t in closed if (t.pnl or 0) < 0)  # type: ignore[misc]
    curve = [start]
    for t in closed:
        curve.append(curve[-1] + (t.pnl or 0.0))
    return {
        "starting_equity": _num(start, 2),
        "equity": _num(equity, 2),
        "return_pct": _num((equity / start - 1) * 100.0, 3) if start else None,
        "closed_trades": len(closed),
        "wins": len(wins),
        "win_rate_pct": _num(len(wins) / len(closed) * 100.0, 1) if closed else None,
        "avg_r": _num(sum(rs) / len(rs), 3) if rs else None,
        "total_r": _num(sum(rs), 3) if rs else 0.0,
        "profit_factor": _num(gross_win / gross_loss, 2) if gross_loss > 0 else None,
        "max_dd_pct": _num(_max_drawdown_pct(curve), 2),
        "exits": dict(Counter(t.exit_reason for t in closed)),
    }


def _trade_row(t: Trade) -> dict[str, Any]:
    return {
        "id": t.trade_id,
        "pair": t.pair,
        "entry_ts": t.entry_ts,
        "entry": _num(t.entry_price, 8),
        "stop": _num(t.stop, 8),
        "target": _num(t.target, 8),
        "qty": _num(t.qty, 8),
        "risk_pct": _num(t.risk_pct, 3),
        "risk_amount": _num(t.risk_amount, 2),
        "exit_ts": t.exit_ts,
        "exit": _num(t.exit_price, 8),
        "reason": t.exit_reason,
        "pnl": _num(t.pnl, 2),
        "r": _num(t.r_multiple, 3),
    }


def _open_row(t: Trade, last: Candle | None, cfg: StrategyConfig, reserved: float | None):
    row = _trade_row(t)
    row["reserved_pct"] = _num(reserved if reserved is not None else t.risk_pct, 3)
    if last is not None:
        px = last.close
        fees = cfg.fee_rate * t.qty * (t.entry_price + px)
        unreal = t.qty * (px - t.entry_price) - fees
        row.update(
            last=_num(px, 8),
            last_ts=last.ts,
            unrealized=_num(unreal, 2),
            r_now=_num(unreal / t.risk_amount, 3) if t.risk_amount else None,
            progress=_num((px - t.stop) / (t.target - t.stop), 4),
        )
    return row


def _pair_series(
    candles: Sequence[Candle], trades: Sequence[Trade], cfg: StrategyConfig, tail: int
) -> dict[str, Any]:
    rows = compute_features(candles, cfg) if candles else []
    lo = max(0, len(candles) - tail)
    window = candles[lo:]
    start_ts = window[0].ts if window else 0
    series = [
        {
            "ts": c.ts,
            "close": _num(c.close, 8),
            "ema21": _num(r.ema_slow, 8) if r.ema_slow is not None else None,
            "ema200": _num(r.ema_regime, 8) if r.ema_regime is not None else None,
        }
        for c, r in zip(window, rows[lo:], strict=True)
    ]
    marks = []
    for t in trades:
        if t.entry_ts >= start_ts:
            marks.append(
                {
                    "ts": t.entry_ts,
                    "price": _num(t.entry_price, 8),
                    "kind": "entry",
                    "id": t.trade_id,
                }
            )
        if t.exit_ts is not None and t.exit_ts >= start_ts:
            marks.append(
                {
                    "ts": t.exit_ts,
                    "price": _num(t.exit_price, 8),
                    "kind": t.exit_reason,
                    "id": t.trade_id,
                }
            )
    return {"candles": series, "marks": marks}


def _weekly(closed: Sequence[Trade], cfg: StrategyConfig, now: int, equity: float):
    lo = now - int(cfg.loss_window_days * DAY_MS)
    pnl = sum(t.pnl or 0.0 for t in closed if t.exit_ts is not None and lo < t.exit_ts <= now)
    limit = cfg.weekly_loss_limit_pct / 100.0 * equity
    return {"pnl": _num(pnl, 2), "limit": _num(limit, 2), "halted": pnl < -limit}


def _log_tail(path: Path, n: int) -> list[str]:
    if not path.exists():
        return []
    with path.open("rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(max(0, size - 64_000))
        lines = fh.read().decode("utf-8", "replace").splitlines()
    return lines[-n:]


def build_snapshot(
    state_dir: str | Path,
    cfg: StrategyConfig | None = None,
    *,
    now_ms: int | None = None,
    candles_tail: int = 180,
    decisions_tail: int = 150,
    trades_tail: int = 200,
    log_tail: int = 80,
) -> dict[str, Any]:
    """Everything the page shows, as JSON-ready data (pure: reads files, writes nothing)."""
    d = Path(state_dir)
    state = _read_json(d / "state.json")
    if cfg is None:
        cfg = config_from_overrides({"exchange_id": state.get("exchange", "binance")})
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    trades = read_journal(d / "journal.csv") if (d / "journal.csv").exists() else []
    closed = sorted((t for t in trades if t.is_closed), key=lambda t: (t.exit_ts, t.trade_id))
    still_open = [t for t in trades if not t.is_closed]
    start = float(state.get("starting_equity") or cfg.starting_capital)
    equity = start + sum(t.pnl or 0.0 for t in closed)
    reserved = {int(k): float(v) for k, v in (state.get("reservations") or {}).items()}

    pairs: dict[str, Any] = {}
    last: dict[str, Candle] = {}
    all_candles: dict[str, list[Candle]] = {}
    for path in sorted((d / "candles").glob("*-4h.csv")) if (d / "candles").exists() else []:
        pair = path.name[: -len("-4h.csv")].replace("_", "/", 1)
        candles = load_candles_csv(path)
        all_candles[pair] = candles
        if candles:
            last[pair] = candles[-1]
        pairs[pair] = _pair_series(
            candles, [t for t in trades if t.pair == pair], cfg, candles_tail
        )

    decisions = read_decisions_log(d / "decisions.csv") if (d / "decisions.csv").exists() else []
    adaptations = audit(trades, cfg, now, equity)
    curve = [{"ts": closed[0].entry_ts if closed else now, "equity": _num(start, 2)}]
    running = start
    for t in closed:
        running += t.pnl or 0.0
        curve.append({"ts": t.exit_ts, "equity": _num(running, 2), "id": t.trade_id})
    return {
        "meta": {
            "exchange": state.get("exchange", cfg.exchange_id),
            "mode": state.get("mode", "unknown"),
            "state_dir": d.name,
            "generated_at": now,
            "quote": _quote(trades, pairs),
            "has_state": bool(state),
        },
        "stats": _stats(closed, start, equity),
        "risk": {
            "open_risk_pct": _num(sum(reserved.get(t.trade_id, t.risk_pct) for t in still_open), 3),
            "cluster_budget_pct": cfg.cluster_risk_budget_pct,
            "weekly": _weekly(closed, cfg, now, equity),
        },
        "breakers": [
            {"rule": a.rule, "scope": a.scope, "action": a.action, "explanation": a.explanation}
            for a in adaptations
        ],
        "equity_curve": curve,
        "open_positions": [
            _open_row(t, last.get(t.pair), cfg, reserved.get(t.trade_id)) for t in still_open
        ],
        "trades": [_trade_row(t) for t in closed[-trades_tail:]],
        "pairs": pairs,
        "decision_counts": dict(Counter(r.rule for r in decisions).most_common()),
        "decisions": [
            {
                "pair": r.pair,
                "ts": r.signal_ts,
                "allowed": r.allowed,
                "rule": r.rule,
                "reason": r.reason,
            }
            for r in decisions[-decisions_tail:]
        ][::-1],
        "unmanaged": state.get("unmanaged", []),
        "log": _log_tail(d / "bot.log", log_tail),
        "control": _read_json(d / "control.json"),
        "learning": _learning_view(d),
        "ledger": _ledger_view(d, trades_tail),
        "layers": _layers_view(d),
        "market": _market_view(d),
        "brain": _brain_view(d),
        "verify": _read_json(d / "verify_status.json"),
        "regime": state.get("regime"),
        "scanner": _read_json(d / "scanner.json"),
        "lab": _lab_view(d),
        "correlations": _correlations(all_candles),
        "capital": {
            **(state.get("capital") or {}),
            "halted": _read_json(d / "trading_halted.lock")
            or (
                {"reason": "trading_halted.lock exists"}
                if (d / "trading_halted.lock").exists()
                else None
            ),
        },
    }


def _lab_view(d: Path) -> dict[str, Any]:
    from .lab import load_approved

    board = _read_json(d / "lab" / "leaderboard.json")
    rows = [
        {k: r.get(k) for k in ("strategy", "pair", "timeframe", "status", "why", "windows")}
        | {"oos": r.get("oos") or {}, "stress": (r.get("stress") or {}).get("worst_max_dd_pct")}
        for r in board.get("leaderboard", [])
    ]
    try:
        approved = load_approved()
    except (OSError, ValueError):
        approved = {}
    return {
        "run_ms": board.get("run_ms"),
        "seconds": board.get("seconds"),
        "rows": rows,
        "proposals": board.get("proposals", []),
        "approved": approved,
    }


def _correlations(candles: Mapping[str, Sequence[Candle]]) -> dict[str, Any]:
    from .allocation import correlation_table

    try:
        return correlation_table(candles)
    except Exception:  # display only
        return {}


def _quote(trades: Sequence[Trade], pairs: dict[str, Any]) -> str:
    names = [t.pair for t in trades] or list(pairs)
    return names[0].split("/", 1)[1] if names and "/" in names[0] else "USDT"


# ---------------------------------------------------------------------- 1-second refresh
_VOLATILE = {"live.json", "heartbeat.json"}  # rewritten every second: overlaid, never cached
_CACHE: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {}
LIVE_STALE_MS = 15_000


def _signature(d: Path, now_ms: int) -> tuple[Any, ...]:
    """Changes whenever a file the snapshot reads changes (and once a minute for the clock)."""
    sig: list[Any] = [now_ms // 60_000]
    for sub in (d, d / "candles", d / "mcp", d / "models"):
        if not sub.is_dir():
            continue
        for e in os.scandir(sub):
            if e.is_file() and e.name not in _VOLATILE and not e.name.endswith(".tmp"):
                st = e.stat()
                sig.append((sub.name, e.name, st.st_mtime_ns, st.st_size))
    return tuple(sorted(sig, key=str))


def live_snapshot(
    state_dir: str | Path, cfg: StrategyConfig | None = None, now_ms: int | None = None
) -> dict[str, Any]:
    """``build_snapshot`` re-built only when a file changed, plus the live prices of this
    second: cheap enough to refresh the page every second."""
    d = Path(state_dir)
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    key = f"{d.resolve()}|{id(cfg)}"
    sig = _signature(d, now)
    hit = _CACHE.get(key)
    if hit is None or hit[0] != sig:
        hit = (sig, build_snapshot(d, cfg, now_ms=now))
        _CACHE[key] = hit
    return overlay_live(hit[1], d, cfg, now)


def overlay_live(
    base: dict[str, Any], d: Path, cfg: StrategyConfig | None, now: int
) -> dict[str, Any]:
    snap = dict(base)  # shallow: the cached parts are never mutated
    snap["meta"] = {**base["meta"], "generated_at": now}
    live = _read_json(d / "live.json")
    age = now - int(live.get("wall_ts") or 0)
    fresh = bool(live) and 0 <= age <= LIVE_STALE_MS
    snap["live"] = {
        "fresh": fresh,
        "age_ms": age if live else None,
        "prices": live.get("prices", {}) if fresh else {},
        "forming": live.get("forming", {}) if fresh else {},
    }
    if fresh and live.get("capital"):
        snap["capital"] = {
            **(base.get("capital") or {}),
            **{k: v for k, v in live["capital"].items() if v is not None},
        }
    fee = cfg.fee_rate if cfg is not None else 0.001
    rows = []
    for p in base.get("open_positions") or []:
        bid = ((snap["live"]["prices"].get(p["pair"]) or {}).get("bid")) if fresh else None
        if bid and p.get("qty") and p.get("entry"):
            unreal = p["qty"] * (bid - p["entry"]) - fee * p["qty"] * (p["entry"] + bid)
            p = {
                **p,
                "last": _num(bid, 8),
                "unrealized": _num(unreal, 2),
                "r_now": _num(unreal / p["risk_amount"], 3) if p.get("risk_amount") else None,
                "progress": _num((bid - p["stop"]) / (p["target"] - p["stop"]), 4),
                "live": True,
            }
        rows.append(p)
    snap["open_positions"] = rows
    from .traders import read_status

    snap["traders"] = read_status()  # global (all bots), small: read every second
    return snap


def _learning_view(d: Path) -> dict[str, Any]:
    raw = _read_json(d / "learnings.json")
    return {
        "mode": raw.get("mode"),
        "rules": [
            {k: r.get(k) for k in ("id", "text", "status", "why", "n", "wins", "avg_r")}
            | {
                "validation": (r.get("validation") or {}).get("reason"),
                "override": (raw.get("overrides") or {}).get(r.get("id")),
            }
            for r in raw.get("rules", [])
        ],
        "overrides": raw.get("overrides", {}),
    }


def _brain_view(d: Path) -> dict[str, Any]:
    from .chantisimo import read_thoughts

    raw = _read_json(d / "chantisimo.json")
    return {
        "name": raw.get("name", "Chantisimo"),
        "memory": raw.get("memory"),
        "graduation": raw.get("graduation", []),
        "mistakes": raw.get("mistakes", []),
        "active_rules": raw.get("active_rules", []),
        "teachers": (raw.get("settings") or {}).get("learn_from", []),
        "thoughts": read_thoughts(d, 40, "thought"),
        "reflections": read_thoughts(d, 25, "reflection"),
    }


def _layers_view(d: Path) -> dict[str, Any]:
    reg = _read_json(d / "layers.json")
    overrides = reg.get("overrides", {})
    rows = []
    for name, info in sorted((reg.get("layers") or {}).items()):
        status = "disabled" if overrides.get(name) == "disabled" else info.get("status")
        rows.append(
            {
                "name": name,
                "kind": info.get("kind"),
                "status": status,
                "reason": info.get("reason"),
                "description": info.get("description"),
                "gain_r": info.get("gain_r"),
                "trained_utc": info.get("trained_utc"),
                "override": overrides.get(name),
            }
        )
    return {
        "rows": rows,
        "retrained_utc": reg.get("retrained_utc"),
        "retrain_status": _read_json(d / "retrain_status.json"),
        "collect_status": _read_json(d / "collect_status.json"),
    }


def _market_view(d: Path) -> dict[str, Any]:
    """Latest external data for the tables: Fear & Greed, news sentiment, order flow, DEX."""
    out: dict[str, Any] = {"fear_greed": None, "news": [], "orderflow": [], "dex": []}
    try:
        from .sentiment import load_sources as sentiment_sources

        src = sentiment_sources(d) or {}
        fg = src.get("fear_greed") or []
        if fg:
            ts, value = fg[-1][0], fg[-1][1]
            out["fear_greed"] = {"known_from": ts, "value": value}
        for coin, rows in sorted((src.get("news_sentiment") or {}).items()):
            if rows:
                ts, mean, n = rows[-1][0], rows[-1][1], rows[-1][2]
                out["news"].append({"coin": coin, "ts": ts, "score": mean, "items": n})
    except Exception as exc:  # optional module / missing data: show nothing, never fail
        out["news_error"] = str(exc)
    try:
        from .orderflow import load_sources as flow_sources

        flows = (flow_sources(d) or {}).get("orderflow") or {}
        for pair, by_ts in sorted(flows.items()):
            if by_ts:
                ts = max(by_ts)
                fp = by_ts[ts]
                row = fp if isinstance(fp, dict) else getattr(fp, "__dict__", {})
                if not row and hasattr(fp, "__slots__"):
                    row = {k: getattr(fp, k) for k in fp.__slots__}
                out["orderflow"].append(
                    {
                        "pair": pair,
                        "ts": ts,
                        **{k: v for k, v in row.items() if isinstance(v, (int, float, str, bool))},
                    }
                )
    except Exception as exc:
        out["orderflow_error"] = str(exc)
    try:
        from .dex_scan import load_watchlist

        wl = load_watchlist(d) or {}
        out["dex"] = [_dex_row(r) for r in list(wl.get("tokens") or [])[:50]]
        out["dex_scanned"] = wl.get("generated_at")
    except Exception as exc:
        out["dex_error"] = str(exc)
    from .connections import latest_mcp_rows

    out["mcp"] = [{**r, "text": (r.get("text") or "")[:600]} for r in latest_mcp_rows(d)[:40]]
    return out


def _dex_row(r: Mapping[str, Any]) -> dict[str, Any]:
    pair = r.get("pair") or {}
    holders = r.get("holders") or {}
    return {
        "symbol": r.get("symbol") or r.get("name") or (r.get("address") or "?")[:8],
        "chain": pair.get("chain_id"),
        "liquidity_usd": pair.get("liquidity_usd"),
        "volume_h24": (pair.get("volume") or {}).get("h24"),
        "top10_share": holders.get("top10_share"),
        "score": r.get("score"),
        "tradable": bool(r.get("tradable")),
        "flags": [
            f.get("code") or f.get("name")
            for f in r.get("flags") or []
            if f.get("severity") != "soft"
        ],
        "url": pair.get("url"),
    }


def _ledger_view(d: Path, tail: int) -> list[dict[str, Any]]:
    path = d / "ledger.json"
    if not path.exists():
        return []
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return []
    return list(reversed(rows[-tail:]))


def render_html(snapshot: dict[str, Any] | None, refresh_s: int, token: str | None = None) -> str:
    """The page; with a snapshot embedded it is self-contained (``--export``)."""
    html = HTML_PATH.read_text(encoding="utf-8")
    embedded = "null" if snapshot is None else json.dumps(snapshot).replace("</", "<\\/")
    html = html.replace(SNAPSHOT_TOKEN, embedded).replace("__REFRESH_S__", str(int(refresh_s)))
    return html.replace('"__CONTROL_TOKEN__"', json.dumps(token))


def _pid_alive(pid: Any) -> bool:
    """True if ``pid`` is a live process (POSIX); on Windows trust the fresh heartbeat."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    if os.name == "nt":
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Controller:
    """Operator actions behind the dashboard buttons (all go through files the bot reads)."""

    def __init__(self, state_dir: Path, settings_path: Path | None) -> None:
        self.dir = state_dir
        self.settings_path = settings_path
        self.proc: subprocess.Popen[bytes] | None = None
        self.jobs: dict[str, subprocess.Popen[bytes]] = {}
        self.poll_s = 10.0
        if settings_path is not None:
            from .live_bot import BotSettings

            self.poll_s = BotSettings.load(settings_path).poll_seconds

    def bot_status(self) -> dict[str, Any]:
        beat = _read_json(self.dir / "heartbeat.json")
        age_s = (time.time() * 1000 - beat.get("wall_ts", 0)) / 1000 if beat else None
        alive = self.proc is not None and self.proc.poll() is None
        fresh = age_s is not None and age_s <= max(90.0, 3 * self.poll_s + 30)
        running = alive or (
            fresh and beat.get("status") == "running" and _pid_alive(beat.get("pid"))
        )
        return {
            "running": bool(running),
            "pid": beat.get("pid"),
            "heartbeat_age_s": None if age_s is None else round(age_s, 1),
            "last_status": beat.get("status"),
            "can_start": self.settings_path is not None,
            "jobs_running": sorted(
                [n for n, p in self.jobs.items() if p.poll() is None]
                + [n for n in ("collect", "retrain") if (self.dir / f".{n}.lock").exists()]
            ),
            "pending_requests": len(list((self.dir / "requests").glob("*.json")))
            if (self.dir / "requests").exists()
            else 0,
        }

    def act(self, req: dict[str, Any]) -> tuple[bool, str]:  # noqa: C901 - one branch per button
        from .live_bot import post_request, write_control

        action = req.get("action")
        if action == "start":
            return self._start()
        if action == "stop":
            post_request(self.dir, action="stop")
            return True, "stop requested: the bot finishes its current step and exits"
        if action in ("pause", "resume"):
            write_control(self.dir, entries_paused=action == "pause")
            return True, (
                "new entries paused (open trades are still managed)"
                if action == "pause"
                else "new entries resumed"
            )
        if action == "close":
            pair = str(req.get("pair") or "ALL")
            post_request(self.dir, action="close", pair=pair)
            return True, f"close requested for {pair}: market sell at the next bot step"
        if action == "verify":
            from .verify import run_all, run_for_settings

            if self.settings_path is not None:
                from .live_bot import BotSettings

                st = run_for_settings(BotSettings.load(self.settings_path))
            else:
                state = _read_json(self.dir / "state.json")
                st = run_all(
                    self.dir,
                    config_from_overrides({"exchange_id": state.get("exchange", "binance")}),
                )
            bad = [a["agent"] for a in st["agents"] if a["status"] == "fail"]
            return True, f"verification {st['overall']}" + (
                f": {', '.join(bad)} failed" if bad else ""
            )
        if action == "lab_run":
            if self.settings_path is None:
                return False, "the lab needs the dashboard to be launched with --settings"
            from .live_bot import spawn_job

            proc = spawn_job("lab", self.settings_path, self.dir)
            self.jobs["lab"] = proc
            return (
                True,
                f"strategy research started (pid {proc.pid}); results in the Strategy lab card",
            )
        if action == "lab_approve":
            from .lab import approve

            a = approve(self.dir, str(req.get("id")))
            return True, (
                f"{a['id']} approved: run it on a paper bot (engine lab); it can trade real "
                "money after 30 days of paper incubation"
            )
        if action == "scan":
            import ccxt

            from .live_bot import BotSettings
            from .scanner import ScannerSettings, scan

            settings = BotSettings.load(self.settings_path) if self.settings_path else None
            ex_id = (
                settings.exchange
                if settings
                else _read_json(self.dir / "state.json").get("exchange", "binance")
            )
            ex = getattr(ccxt, ex_id)({"enableRateLimit": True})
            res = scan(
                ex, self.dir, ScannerSettings(**((settings.scanner if settings else {}) or {}))
            )
            return (
                True,
                f"scanned {res['tickers']} tickers: {len(res['passing'])} pass every filter",
            )
        if action in ("retrain", "collect"):
            if self.settings_path is None:
                return False, f"{action} needs the dashboard to be launched with --settings"
            from .live_bot import spawn_job

            proc = spawn_job(action, self.settings_path, self.dir)
            self.jobs[action] = proc
            return True, f"{action} started (pid {proc.pid}); progress in retrain.log"
        if action == "layer":
            from .layers import set_layer_override

            status = req.get("status")
            set_layer_override(self.dir, str(req.get("name")), status)
            return True, f"layer {req.get('name')} -> {status or 'automatic'}"
        if action == "rule":
            status = req.get("status")
            if status not in ("active", "disabled", None):
                return False, "rule status must be active, disabled or null"
            post_request(self.dir, action="rule", rule_id=str(req.get("rule_id")), status=status)
            return True, f"rule {req.get('rule_id')} -> {status or 'automatic'}"
        return False, f"unknown action {action!r}"

    def _start(self) -> tuple[bool, str]:
        if self.settings_path is None:
            return False, "start needs the dashboard to be launched with --settings"
        if self.bot_status()["running"]:
            return False, "the bot is already running"
        if (self.dir / "trading_halted.lock").exists():
            lock = _read_json(self.dir / "trading_halted.lock")
            return False, (
                "trading is halted by the kill switch "
                f"({lock.get('reason', 'see the lock file')}). "
                f"Review it, then delete {self.dir / 'trading_halted.lock'} by hand to restart"
            )
        repo_root = Path(__file__).resolve().parents[2]
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(repo_root), env.get("PYTHONPATH")]))
        cmd = [
            sys.executable,
            "-m",
            "research.trendbot.live_bot",
            "run",
            "--settings",
            str(self.settings_path),
        ]
        kwargs: dict[str, Any] = {}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            kwargs["start_new_session"] = True  # keeps running if the dashboard stops
        err = (self.dir / "bot.stderr.log").open("ab")
        self.proc = subprocess.Popen(
            cmd, cwd=Path.cwd(), env=env, stdout=subprocess.DEVNULL, stderr=err, **kwargs
        )
        return True, f"bot started (pid {self.proc.pid})"


class FleetRuntime:
    """One dashboard over 1-10 bots: snapshots, controls and the >5-bot approval flow."""

    def __init__(
        self, fleet: Any, entries: Mapping[str, tuple[Path, StrategyConfig, Controller | None]]
    ) -> None:
        self.fleet = fleet
        self.entries = dict(entries)

    @property
    def names(self) -> list[str]:
        return list(self.entries)

    @property
    def is_fleet(self) -> bool:
        return self.fleet is not None and self.fleet.path is not None

    @property
    def controls(self) -> bool:
        return any(c is not None for _, _, c in self.entries.values())

    def _ctrl(self, name: str) -> Controller | None:
        return self.entries[name][2]

    def running(self) -> list[str]:
        return [n for n in self.names if (c := self._ctrl(n)) and c.bot_status()["running"]]

    def snapshot(self, name: str | None) -> dict[str, Any]:
        name = name if name in self.entries else self.names[0]
        state_dir, cfg, ctrl = self.entries[name]
        snap = live_snapshot(state_dir, cfg)
        snap["bot"] = ctrl.bot_status() if ctrl else None
        snap["meta"]["bot_name"] = name
        bot = self.fleet.bots.get(name) if self.fleet is not None else None
        if bot is not None and snap["meta"].get("mode") in (None, "unknown"):
            snap["meta"]["mode"] = bot.settings.mode  # never started: show the configured mode
            snap["meta"]["exchange"] = bot.settings.exchange
        return snap

    def summary(self) -> dict[str, Any]:
        from .fleet import summary_row

        rows = []
        for n in self.names:
            try:
                rows.append(summary_row(n, self.snapshot(n)))
            except (OSError, ValueError) as exc:
                rows.append({"name": n, "error": str(exc)})
        f = self.fleet
        return {
            "fleet": self.is_fleet,
            "bots": rows,
            "running": sum(1 for r in rows if r.get("running")),
            "approval_threshold": f.approval_threshold if f else None,
            "max_bots": f.max_bots if f else 1,
        }

    def act(self, req: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
        from .fleet import FleetError

        action = req.get("action")
        if str(action).startswith(("keys_", "mcp_", "trader")):
            return connection_action(req)
        try:
            if action == "approve":
                a = self.fleet.take_approval(
                    str(req.get("approval_id")), str(req.get("confirm", ""))
                )
                return self._start_now(a.bots, approved=True)
            if action == "start_all":
                return self._start_many([n for n in self.names if n not in self.running()])
            if action in ("stop_all", "pause_all", "resume_all"):
                inner = {"stop_all": "stop", "pause_all": "pause", "resume_all": "resume"}[action]
                msgs = [
                    f"{n}: {self._ctrl(n).act({'action': inner})[1]}"
                    for n in self.names
                    if self._ctrl(n)
                ]
                return 200, {"ok": True, "message": "; ".join(msgs) or "no bots"}
        except FleetError as exc:
            return 400, {"ok": False, "message": str(exc)}
        name = str(req.get("bot") or self.names[0])
        if name not in self.entries:
            return 400, {"ok": False, "message": f"unknown bot {name!r}"}
        if action == "start":
            return self._start_many([name])
        ctrl = self._ctrl(name)
        if ctrl is None:
            return 403, {"ok": False, "message": "controls are off"}
        ok, msg = ctrl.act(dict(req))
        return (200 if ok else 400), {"ok": ok, "message": msg}

    def _start_many(self, names: list[str]) -> tuple[int, dict[str, Any]]:
        running = self.running()
        names = [n for n in names if n not in running]
        if not names:
            return 400, {"ok": False, "message": "already running"}
        f = self.fleet
        if f is None:  # single-bot dashboard: no fleet rules
            return self._start_now(names)
        room = f.max_bots - len(running)
        if room <= 0:
            return 400, {
                "ok": False,
                "message": f"{len(running)} bots running: the maximum is {f.max_bots}",
            }
        names = names[:room]
        free = max(0, f.approval_threshold - len(running))
        now, later = names[:free], names[free:]
        code, body = self._start_now(now) if now else (200, {"ok": True, "message": ""})
        if later:
            a = f.request_approval(later, len(running) + len(now))
            return 409, {
                "ok": False,
                "needs_approval": True,
                "approval_id": a.id,
                "bots": f.describe(later),
                "running": len(running) + len(now),
                "threshold": f.approval_threshold,
                "max_bots": f.max_bots,
                "expires_in_s": int(a.expires - a.created),
                "message": (body.get("message", "") + " " if now else "")
                + f"{len(running) + len(now)} bots are running; starting {len(later)} more "
                f"needs your approval (more than {f.approval_threshold})",
            }
        return code, body

    def _start_now(self, names: list[str], approved: bool = False) -> tuple[int, dict[str, Any]]:
        msgs, ok_all = [], True
        for n in names:
            ctrl = self._ctrl(n)
            if ctrl is None:
                ok, msg = False, "controls are off"
            else:
                ok, msg = ctrl.act({"action": "start"})
            ok_all = ok_all and ok
            msgs.append(f"{n}: {msg}")
        prefix = "approved; " if approved else ""
        return (200 if ok_all else 400), {"ok": ok_all, "message": prefix + "; ".join(msgs)}


def connection_action(req: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
    """Connections card: exchange keys (saved to .env, never echoed) and MCP servers."""
    from . import connections as c

    a = str(req.get("action"))
    if a.startswith("trader"):
        return trader_action(req)
    ex, acc = str(req.get("exchange", "")), str(req.get("account", ""))
    try:
        if a == "keys_save":
            msg = c.set_exchange_keys(
                ex,
                acc,
                str(req.get("key", "")),
                str(req.get("secret", "")),
                str(req.get("password") or ""),
            )
            return 200, {"ok": True, "message": msg}
        if a == "keys_clear":
            return 200, {"ok": True, "message": c.clear_exchange_keys(ex, acc)}
        if a == "keys_test":
            return 200, {"ok": True, "message": c.test_exchange(ex, acc)}
        name = str(req.get("name", ""))
        if a == "mcp_save":
            return 200, {
                "ok": True,
                "message": c.save_mcp(name, str(req.get("url", "")), req.get("token")),
            }
        if a == "mcp_remove":
            return 200, {"ok": True, "message": c.remove_mcp(name)}
        if a == "mcp_test":
            res = c.test_mcp(name)
            srv = res["server"].get("name") or name
            return 200, {
                "ok": True,
                "message": f"{srv}: connected, {len(res['tools'])} tools",
                **res,
            }
        args = req.get("args") or {}
        if not isinstance(args, dict):
            return 400, {"ok": False, "message": "args must be a JSON object"}
        if a == "mcp_call":
            text = c.call_mcp(
                name,
                str(req.get("tool")),
                args,
                req.get("pair") or None,
                str(req.get("exchange_id") or "binance"),
            )
            return 200, {
                "ok": True,
                "message": f"{name}.{req.get('tool')} replied",
                "text": text[:8000],
            }
        if a == "mcp_source":
            msg = c.set_mcp_source(
                name,
                str(req.get("tool")),
                args,
                bool(req.get("per_pair")),
                bool(req.get("enabled", True)),
            )
            return 200, {"ok": True, "message": msg}
    except c.ConnectionSetupError as exc:
        return 400, {"ok": False, "message": str(exc)}
    return 400, {"ok": False, "message": f"unknown action {a!r}"}


def trader_action(req: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
    """Smart-money card: add / remove / follow traders, refresh the ranking."""
    from . import traders as tr

    a, name = str(req.get("action")), str(req.get("name", ""))
    try:
        if a == "trader_add":
            msg = tr.add_trader(name, str(req.get("kind", "")), str(req.get("value", "")))
            tr.refresh()
            return 200, {"ok": True, "message": msg + "; ranked"}
        if a == "trader_remove":
            msg = tr.remove_trader(name)
            tr.refresh()
            return 200, {"ok": True, "message": msg}
        if a == "trader_follow":
            msg = tr.set_follow(name, bool(req.get("follow", True)))
            tr.refresh()
            return 200, {"ok": True, "message": msg}
        if a == "traders_refresh":
            st = tr.refresh()
            q = sum(1 for r in st["traders"] if r.get("qualified"))
            return 200, {
                "ok": True,
                "message": f"{len(st['traders'])} traders ranked, {q} qualified",
            }
    except (tr.TraderError, OSError, ValueError) as exc:
        return 400, {"ok": False, "message": str(exc)}
    return 400, {"ok": False, "message": f"unknown action {a!r}"}


def make_handler(  # noqa: C901 - one closure per HTTP verb
    state_dir: Path | None,
    cfg: StrategyConfig | None,
    refresh_s: int,
    controller: Controller | None = None,
    token: str | None = None,
    allowed_hosts: Sequence[str] = (),
    runtime: FleetRuntime | None = None,
) -> type:
    if runtime is None:
        runtime = FleetRuntime(None, {"bot": (Path(state_dir), cfg, controller)})
    controls = runtime.controls
    from .assistant import Terminal

    terminal = Terminal(runtime)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'unsafe-inline'; "
                "style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'",
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status: int, obj: Any) -> None:
            self._send(status, json.dumps(obj).encode(), "application/json")

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").lower()
            return not allowed_hosts or host in allowed_hosts

        def _query(self) -> dict[str, str]:
            from urllib.parse import parse_qs, urlsplit

            return {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}

        def do_GET(self) -> None:
            if not self._host_ok():
                self._send(HTTPStatus.FORBIDDEN, b"bad host", "text/plain")
                return
            path = self.path.split("?", 1)[0]
            try:
                if path in ("/", "/index.html"):
                    page = render_html(None, refresh_s, token if controls else None)
                    self._send(HTTPStatus.OK, page.encode(), "text/html; charset=utf-8")
                elif path == "/api/snapshot":
                    self._json(HTTPStatus.OK, runtime.snapshot(self._query().get("bot")))
                elif path == "/api/fleet":
                    self._json(HTTPStatus.OK, runtime.summary())
                elif path == "/api/connections":
                    from .connections import status as connections_status

                    self._json(HTTPStatus.OK, connections_status())
                else:
                    self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
            except (OSError, ValueError) as exc:
                self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc)})

        def do_POST(self) -> None:
            path = self.path.split("?", 1)[0]
            if not self._host_ok() or path not in ("/api/control", "/api/terminal"):
                self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")
                return
            if not controls:
                self._json(HTTPStatus.FORBIDDEN, {"ok": False, "message": "controls are off"})
                return
            if not token or not hmac.compare_digest(
                self.headers.get("X-Trendbot-Token", ""), token
            ):
                self._json(HTTPStatus.FORBIDDEN, {"ok": False, "message": "bad token"})
                return
            try:
                size = min(int(self.headers.get("Content-Length") or 0), 10_000)
                req = json.loads(self.rfile.read(size) or b"{}")
                req = req if isinstance(req, dict) else {}
                if path == "/api/terminal":
                    code, body = 200, terminal.handle(str(req.get("text", "")), req.get("bot"))
                else:
                    code, body = runtime.act(req)
            except (ValueError, OSError) as exc:
                code, body = 400, {"ok": False, "message": str(exc)}
            self._json(code, body)

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    return Handler


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python3 -m research.trendbot.dashboard",
        description="web dashboard (with start/stop controls) for the trendbot live bot",
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--settings", help="bot settings JSON (uses its state_dir and strategy)")
    src.add_argument("--state-dir", help="the bot's state directory")
    src.add_argument("--fleet", help="fleet JSON listing up to 10 bot settings files")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8050)
    p.add_argument("--refresh", type=int, default=1, help="page refresh interval, seconds")
    p.add_argument("--export", metavar="HTML", help="write a static snapshot page and exit")
    p.add_argument("--read-only", action="store_true", help="hide the control buttons")
    p.add_argument("--now", help="evaluate breakers at this ISO-8601 time (replays/exports)")
    return p


def _serve(args: argparse.Namespace, runtime: FleetRuntime, label: str) -> int:
    token = secrets.token_urlsafe(24)
    hosts = [f"{h}:{args.port}" for h in {args.host, "127.0.0.1", "localhost"}]
    handler = make_handler(
        None, None, args.refresh, token=token, allowed_hosts=hosts, runtime=runtime
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    mode = "with controls" if runtime.controls else "read-only"
    print(f"dashboard ({mode}) for {label} on http://{args.host}:{args.port}/ (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _fleet_main(args: argparse.Namespace) -> int:
    from .fleet import Fleet

    fleet = Fleet.load(args.fleet)
    entries = {}
    for name, b in fleet.bots.items():
        b.state_dir.mkdir(parents=True, exist_ok=True)
        ctrl = None if args.read_only else Controller(b.state_dir, b.settings_path)
        entries[name] = (b.state_dir, b.settings.strategy_config(), ctrl)
    return _serve(args, FleetRuntime(fleet, entries), f"fleet {args.fleet} ({len(entries)} bots)")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.fleet:
        return _fleet_main(args)
    if args.settings:
        from .live_bot import BotSettings

        settings = BotSettings.load(args.settings)
        state_dir, cfg = Path(settings.state_dir), settings.strategy_config()
    else:
        state_dir = Path(args.state_dir)
        state = _read_json(state_dir / "state.json")
        cfg = config_from_overrides({"exchange_id": state.get("exchange", "binance")})
    if not state_dir.is_dir():
        raise SystemExit(f"{state_dir} is not a directory")
    now = iso_to_ms(args.now) if args.now else None
    if args.export:
        out = Path(args.export)
        snap = build_snapshot(state_dir, cfg, now_ms=now)
        out.write_text(render_html(snap, args.refresh), encoding="utf-8")
        print(f"wrote {out}")
        return 0
    controller = (
        None
        if args.read_only
        else Controller(state_dir, Path(args.settings).resolve() if args.settings else None)
    )
    runtime = FleetRuntime(None, {"bot": (state_dir, cfg, controller)})
    return _serve(args, runtime, str(state_dir))


if __name__ == "__main__":
    raise SystemExit(main())
