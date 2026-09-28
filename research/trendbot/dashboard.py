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
import json
import math
import time
from collections import Counter
from collections.abc import Sequence
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
    for path in sorted((d / "candles").glob("*-4h.csv")) if (d / "candles").exists() else []:
        pair = path.name[: -len("-4h.csv")].replace("_", "/", 1)
        candles = load_candles_csv(path)
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
    }


def _quote(trades: Sequence[Trade], pairs: dict[str, Any]) -> str:
    names = [t.pair for t in trades] or list(pairs)
    return names[0].split("/", 1)[1] if names and "/" in names[0] else "USDT"


def render_html(snapshot: dict[str, Any] | None, refresh_s: int) -> str:
    """The page; with a snapshot embedded it is self-contained (``--export``)."""
    html = HTML_PATH.read_text(encoding="utf-8")
    embedded = "null" if snapshot is None else json.dumps(snapshot).replace("</", "<\\/")
    return html.replace(SNAPSHOT_TOKEN, embedded).replace("__REFRESH_S__", str(int(refresh_s)))


def make_handler(state_dir: Path, cfg: StrategyConfig, refresh_s: int) -> type:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes, ctype: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'none'; script-src 'unsafe-inline'; "
                "style-src 'unsafe-inline'; connect-src 'self'",
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                self._send(
                    HTTPStatus.OK, render_html(None, refresh_s).encode(), "text/html; charset=utf-8"
                )
            elif path == "/api/snapshot":
                try:
                    body = json.dumps(build_snapshot(state_dir, cfg)).encode()
                except (OSError, ValueError) as exc:
                    body = json.dumps({"error": str(exc)}).encode()
                    self._send(HTTPStatus.SERVICE_UNAVAILABLE, body, "application/json")
                    return
                self._send(HTTPStatus.OK, body, "application/json")
            else:
                self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

        def log_message(self, fmt: str, *args: Any) -> None:
            return

    return Handler


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python3 -m research.trendbot.dashboard",
        description="read-only web dashboard for the trendbot live bot",
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--settings", help="bot settings JSON (uses its state_dir and strategy)")
    src.add_argument("--state-dir", help="the bot's state directory")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8050)
    p.add_argument("--refresh", type=int, default=15, help="page refresh interval, seconds")
    p.add_argument("--export", metavar="HTML", help="write a static snapshot page and exit")
    p.add_argument("--now", help="evaluate breakers at this ISO-8601 time (replays/exports)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
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
    server = ThreadingHTTPServer((args.host, args.port), make_handler(state_dir, cfg, args.refresh))
    print(f"dashboard for {state_dir} on http://{args.host}:{args.port}/ (Ctrl-C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
