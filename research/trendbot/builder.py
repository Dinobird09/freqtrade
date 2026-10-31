"""The drag-and-drop strategy builder behind the dashboard's /builder page.

You pick a strategy block (Confluence, Mean reversion, Grid, DCA or one of the lab
strategies), drop signal blocks into "must pass" and "also counts", set the numbers, and press
Backtest. The candles come free from the exchanges' public APIs (no key) or, offline, from a
synthetic demo market. "Save as bot" writes a normal bot settings file in **paper** mode next
to your other bots; switching it to real money is a separate, deliberate step.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .dex_scan import FetchError, default_fetch, fetch_json
from .models import Candle


Fetch = Callable[..., bytes]
TF_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
}
MAX_CANDLES = 6000
ENGINES = ("grid", "dca")
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")
_PAIR = re.compile(r"^[A-Z0-9]{2,12}/[A-Z0-9]{2,8}$")

GRID_DEFAULTS = {"lower": None, "upper": None, "levels": 10, "budget": 1000.0}
DCA_DEFAULTS = {"amount_quote": 100.0, "every": "7d", "buy_the_dip": None, "take_profit_pct": None}
CONFLUENCE_KNOBS = (  # (param, label, min, max, step)
    ("min_signals", "Signals that must line up", 1, 11, 1),
    ("risk_pct", "Risk per trade %", 0.25, 2.0, 0.25),
    ("max_trades_per_day", "Max trades a day", 1, 10, 1),
    ("stop_after_losses", "Stop for the day after losses", 1, 5, 1),
    ("stop_atr", "Stop distance (x ATR)", 0.5, 4.0, 0.25),
    ("tp1_rr", "TP1 (R)", 1.0, 5.0, 0.5),
    ("tp2_rr", "TP2 (R)", 1.5, 8.0, 0.5),
)  # fmt: skip


KNOB_KEYS = ("id", "label", "min", "max", "step")


class BuilderError(ValueError):
    """A plain-English problem with the builder request (safe to show)."""


# ------------------------------------------------------------------------- catalog
def catalog() -> dict[str, Any]:
    """Every block the page can show: strategies, engines, signals and their knobs."""
    from .strategies import STRATEGIES

    try:
        from .confluence import SIGNALS

        signals = dict(SIGNALS)
    except ImportError:  # pragma: no cover - the confluence module ships with the bot
        signals = {}
    try:
        from .confluence import DEFAULT_REQUIRED

        required = list(DEFAULT_REQUIRED)
    except ImportError:  # pragma: no cover
        required = []
    strategies = [
        {
            "id": name,
            "kind": "strategy",
            "description": cls.description,
            "timeframe": cls.timeframe,
            "defaults": _jsonable(cls.defaults),
        }
        for name, cls in STRATEGIES.items()
    ]
    strategies += [
        {
            "id": "grid",
            "kind": "engine",
            "description": "Grid: buy each step down, sell each step up. Best in sideways ranges.",
            "timeframe": "1h",
            "defaults": GRID_DEFAULTS,
        },
        {
            "id": "dca",
            "kind": "engine",
            "description": "DCA: buy a fixed amount on a schedule, extra on dips. Long-term.",
            "timeframe": "1d",
            "defaults": DCA_DEFAULTS,
        },
    ]
    return {
        "strategies": strategies,
        "signals": [{"id": k, "description": v} for k, v in signals.items()],
        "default_required": required,
        "knobs": [dict(zip(KNOB_KEYS, k, strict=True)) for k in CONFLUENCE_KNOBS],
        "timeframes": [t for t in TF_MS if t not in ("1m", "5m")],
        "exchanges": ["binance", "binanceus", "coinbase", "demo"],
    }


def _jsonable(d: Mapping[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(dict(d), default=str))


# ------------------------------------------------------------------------- candles
def _binance(pair: str, tf: str, start: int, end: int, fetch: Fetch, host: str) -> list[Candle]:
    sym = pair.replace("/", "")
    out: list[Candle] = []
    t = start
    while t < end and len(out) < MAX_CANDLES:
        q = urllib.parse.urlencode(
            {"symbol": sym, "interval": tf, "startTime": t, "endTime": end, "limit": 1000}
        )
        rows = fetch_json(fetch, f"https://{host}/api/v3/klines?{q}")
        if not rows:
            break
        out += [Candle(int(r[0]), *(float(x) for x in r[1:6])) for r in rows]
        t = int(rows[-1][0]) + TF_MS[tf]
        if len(rows) < 1000:
            break
    return out


def _coinbase(pair: str, tf: str, start: int, end: int, fetch: Fetch) -> list[Candle]:
    gran = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": None, "1d": 86400}[tf]
    if gran is None:  # Coinbase has no 4h candles: build them from 1h
        from .data import resample_candles

        return resample_candles(_coinbase(pair, "1h", start, end, fetch), TF_MS["1h"], TF_MS["4h"])
    pid = pair.replace("/", "-").replace("-USDT", "-USD")
    rows: dict[int, Candle] = {}
    t = start
    while t < end and len(rows) < MAX_CANDLES:
        t2 = min(end, t + gran * 1000 * 300)
        q = urllib.parse.urlencode(
            {
                "granularity": gran,
                "start": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t / 1000)),
                "end": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t2 / 1000)),
            }
        )
        data = fetch_json(fetch, f"https://api.exchange.coinbase.com/products/{pid}/candles?{q}")
        for r in data or []:  # [time, low, high, open, close, volume], newest first
            ts = int(r[0]) * 1000
            rows[ts] = Candle(ts, float(r[3]), float(r[2]), float(r[1]), float(r[4]), float(r[5]))
        t = t2
    return [rows[k] for k in sorted(rows)]


def demo_candles(tf: str, days: int, seed: int = 7) -> list[Candle]:
    """An offline synthetic market (4H candles, resampled up when asked for 1D)."""
    from .synthetic import make_world

    world, _ = make_world("planted", seed, years=max(days, 30) / 365)
    cs = world[next(iter(world))]
    if tf == "1d":
        from .data import resample_candles

        cs = resample_candles(cs, TF_MS["4h"], TF_MS["1d"])
    return cs


def public_candles(
    pair: str,
    tf: str,
    days: int,
    exchange: str = "binance",
    fetch: Fetch = default_fetch,
    now_ms: int | None = None,
) -> tuple[list[Candle], str]:
    """Closed candles for a backtest, and where they came from (falls back between sources)."""
    if tf not in TF_MS:
        raise BuilderError(f"timeframe must be one of {', '.join(TF_MS)}")
    days = max(1, min(int(days), 1500))
    if exchange == "demo":
        return demo_candles(tf, days), "demo (synthetic market, offline)"
    end = (now_ms or int(time.time() * 1000)) // TF_MS[tf] * TF_MS[tf]
    start = max(end - days * 86_400_000, end - MAX_CANDLES * TF_MS[tf])
    order = [exchange] + [e for e in ("binance", "binanceus", "coinbase") if e != exchange]
    errors = []
    for ex in order:
        try:
            if ex == "coinbase":
                cs = _coinbase(pair, tf, start, end, fetch)
            else:
                host = "api.binance.us" if ex == "binanceus" else "api.binance.com"
                cs = _binance(pair, tf, start, end, fetch, host)
        except (FetchError, OSError, ValueError, KeyError, TypeError, IndexError) as exc:
            errors.append(f"{ex}: {str(exc)[:120]}")
            continue
        cs = [c for c in cs if c.ts + TF_MS[tf] <= end]  # closed candles only
        if len(cs) >= 50:
            return cs, ex
        errors.append(f"{ex}: only {len(cs)} candles")
    raise BuilderError("no candles: " + "; ".join(errors) + " (try exchange 'demo' offline)")


# ------------------------------------------------------------------------- the spec
def clean_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Validate what the page sent: strategy, params, signals, pair, timeframe, days."""
    from .strategies import STRATEGIES

    sid = str(spec.get("strategy") or "confluence")
    if sid not in STRATEGIES and sid not in ENGINES:
        raise BuilderError(f"unknown strategy block: {sid}")
    pair = str(spec.get("pair") or "BTC/USDT").upper().replace("-", "/").strip()
    if not _PAIR.match(pair):
        raise BuilderError("pair looks like BTC/USDT")
    params = dict(spec.get("params") or {})
    if sid == "confluence":
        from .confluence import SIGNALS

        req = [s for s in (spec.get("required") or []) if s in SIGNALS]
        counted = [s for s in (spec.get("counted") or []) if s in SIGNALS and s not in req]
        custom = bool(req or counted)  # the user's own board; else the system's default rule
        params["required"] = req if custom else None
        pool = len(req) + len(counted) if custom else len(SIGNALS)
        params["min_signals"] = int(params.get("min_signals") or max(5, len(req)))
        if params["min_signals"] > pool:
            raise BuilderError(
                f"{params['min_signals']} signals must line up but only {pool} are on the board"
            )
        params["min_signals"] = max(params["min_signals"], len(req))
        params["use"] = (req + counted) if custom else None
    tf = str(spec.get("timeframe") or ("1d" if sid == "dca" else "1h"))
    if sid in STRATEGIES and sid != "confluence":
        tf = str(spec.get("timeframe") or STRATEGIES[sid].timeframe)
    return {
        "strategy": sid,
        "params": params,
        "pair": pair,
        "timeframe": tf,
        "days": int(spec.get("days") or 365),
        "exchange": str(spec.get("exchange") or "binance"),
        "equity": float(spec.get("equity") or 10_000),
        "fee_pct": float(spec.get("fee_pct") if spec.get("fee_pct") is not None else 0.1),
    }


def _strategy_params(s: Mapping[str, Any]) -> dict[str, Any]:
    p = dict(s["params"])
    if s["strategy"] == "confluence":
        p["timeframe"] = s["timeframe"]
    return p


def backtest(
    spec: Mapping[str, Any],
    candles: Sequence[Candle] | None = None,
    fetch: Fetch = default_fetch,
) -> dict[str, Any]:
    """Run the built strategy on real (or demo) candles; returns stats, curve and trades."""
    s = clean_spec(spec)
    source = "given"
    if candles is None:
        candles, source = public_candles(s["pair"], s["timeframe"], s["days"], s["exchange"], fetch)
    fee = s["fee_pct"] / 100
    out: dict[str, Any] = {"spec": s, "source": source, "candles": len(candles)}
    if candles:
        out["from"], out["to"] = candles[0].ts, candles[-1].ts
    if s["strategy"] in ENGINES:
        from . import engines

        params = dict(s["params"])
        if s["strategy"] == "grid":
            closes = [c.close for c in candles]
            params.setdefault("lower", None)
            params.setdefault("upper", None)
            if not params.get("lower") or not params.get("upper"):  # default: the past range
                params["lower"], params["upper"] = min(closes) * 0.98, max(closes) * 1.02
        res = engines.backtest(s["strategy"], list(candles), params, fee_rate=fee, slip_pct=0.05)
        out["result"] = _jsonable(res) if isinstance(res, Mapping) else res
        return out
    from .strategies import STRATEGIES
    from .strategies import backtest as run

    strat = STRATEGIES[s["strategy"]](**_strategy_params(s))
    res = run(list(candles), strat, fee=fee, equity=s["equity"])
    step = max(1, len(res.equity) // 300)
    out["stats"] = res.stats
    out["equity"] = [[ts, round(e, 2)] for ts, e in res.equity[::step]]
    out["trades"] = [
        {
            "entry_ts": candles[t.entry_i].ts,
            "exit_ts": candles[t.exit_i].ts if t.exit_i is not None else None,
            "entry": t.entry,
            "stop": t.stop,
            "exit": t.exit,
            "pnl": round(t.pnl, 2),
            "r": None if t.r is None else round(t.r, 2),
            "reason": t.reason,
        }
        for t in res.trades[-200:]
    ]
    return out


# ------------------------------------------------------------------------- save as bot
def settings_for(spec: Mapping[str, Any], name: str) -> dict[str, Any]:
    """A paper-mode bot settings dict that runs the built strategy."""
    s = clean_spec(spec)
    raw: dict[str, Any] = {
        "exchange": s["exchange"] if s["exchange"] != "demo" else "binance",
        "mode": "paper",
        "pairs": [s["pair"]],
        "state_dir": f"trendbot_state/{name}",
        "starting_equity": s["equity"],
    }
    if s["strategy"] in ENGINES:
        raw["engine"] = s["strategy"]
        raw["lab"] = {s["strategy"]: s["params"]}
    else:
        raw["engine"] = "lab"
        raw["lab"] = {"strategy": s["strategy"], "params": _strategy_params(s)}
    return raw


def save_bot(spec: Mapping[str, Any], name: str, folder: str | Path) -> dict[str, Any]:
    """Write ``<folder>/<name>.json`` (never overwrites an existing bot)."""
    from .live_bot import BotSettings

    n = str(name or "").strip().lower()
    if not _NAME.match(n):
        raise BuilderError("bot name: lowercase letters, digits, - and _ (max 41)")
    raw = settings_for(spec, n)
    try:
        BotSettings(**raw)
    except (TypeError, ValueError) as exc:
        raise BuilderError(f"those settings would not start: {exc}") from exc
    d = Path(folder)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{n}.json"
    if p.exists():
        raise BuilderError(f"a bot called {n} already exists ({p.name}); pick another name")
    from .journal import atomic_write_text

    atomic_write_text(p, json.dumps(raw, indent=2))
    return {"path": str(p), "settings": raw}


# ------------------------------------------------------------------------- live card
def latest_fear_greed(state_dir: str | Path | None, now_ms: int) -> int | None:
    """Today's Crypto Fear & Greed value if the bot has collected it (at most 2 days old)."""
    if state_dir is None:
        return None
    from .sentiment import load_fear_greed

    try:
        rows = load_fear_greed(state_dir)
    except (OSError, ValueError):
        return None
    if not rows:
        return None
    day_ts, value, _ = max(rows)
    return int(value) if now_ms - day_ts <= 2 * 86_400_000 else None


def trade_card(
    pair: str,
    exchange: str = "binance",
    *,
    state_dir: str | Path | None = None,
    equity: float | None = None,
    fetch: Fetch = default_fetch,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """The confluence verdict on ``pair`` right now: 1H candles (+ 5m execution check).

    Returns ``{"result": analyze(...), "text": format_card(...), "source", "pair"}``.
    """
    from . import confluence

    p = pair.upper().replace("-", "/").strip()
    if not _PAIR.match(p):
        raise BuilderError("pair looks like BTC/USDT")
    now = now_ms or int(time.time() * 1000)
    ex = exchange if exchange in ("binance", "binanceus", "coinbase") else "binance"
    days = confluence.history_needed("1h") // 24 + 2
    cs, src = public_candles(p, "1h", days, ex, fetch, now)
    try:
        lower, _ = public_candles(p, "5m", 1, src, fetch, now)
    except BuilderError:
        lower = None
    res = confluence.analyze(
        cs,
        fear_greed=latest_fear_greed(state_dir, now),
        lower=lower,
        equity=equity,
        now_ms=now,
    )
    return {"pair": p, "source": src, "result": res, "text": confluence.format_card(res)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="trendbot analyze", description="the confluence trade card for one pair, now"
    )
    p.add_argument("pair", nargs="?", default="BTC/USDT", help="e.g. BTC/USDT or SOL")
    p.add_argument("--exchange", default="binance", choices=["binance", "binanceus", "coinbase"])
    p.add_argument("--equity", type=float, help="your account size, to show the position size")
    p.add_argument("--state-dir", help="a bot folder with fear_greed.csv (optional)")
    a = p.parse_args(argv)
    pair = a.pair.upper() if "/" in a.pair or "-" in a.pair else f"{a.pair.upper()}/USDT"
    try:
        card = trade_card(pair, a.exchange, state_dir=a.state_dir, equity=a.equity)
    except BuilderError as exc:
        print(f"analyze: {exc}")
        return 1
    print(card["text"])
    print(f"\n({card['pair']}, 1H candles from {card['source']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
