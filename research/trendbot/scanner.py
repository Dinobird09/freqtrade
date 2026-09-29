"""Market scanner: today's movers that pass the momentum, volume, catalyst and float filters.

Filters (all configurable in the bot settings' ``"scanner"`` block):

========================  ================================================================
price expansion           up at least ``min_day_change_pct`` (10%) since today's 00:00 UTC
                          open (the exchange's daily candle)
relative volume (RVOL)    today's volume >= ``min_rvol`` (5x) the average of the previous
                          ``rvol_days`` (50) daily volumes
catalyst                  a headline, Reddit post or token-unlock mention of the coin in the
                          last ``catalyst_hours`` (24), from the collected news (sentiment.py)
float                     circulating supply below ``max_float`` (10 million tokens), from
                          CoinGecko's free API (``COINGECKO_API_KEY`` optional)
========================  ================================================================

One request lists every ticker; the daily candles are read only for the coins up at least
half the threshold over 24h (at most ``max_candidates``). The result (``scanner.json``) is a
WATCHLIST: the bot does not trade these coins. To trade one, add its pair to a bot, where the
nine rules and the 1% risk limit apply as always. Runs with the hourly ``collect`` job.
"""

from __future__ import annotations

import json
import logging
import os
import re
import statistics
import time
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .journal import ms_to_iso


log = logging.getLogger("trendbot.scanner")

OUT_FILE = "scanner.json"
CG = "https://api.coingecko.com/api/v3"
STABLES = {"USDT", "USDC", "FDUSD", "BUSD", "TUSD", "DAI", "USDP", "EUR", "USDE", "PYUSD"}


@dataclass
class ScannerSettings:
    enabled: bool = True
    quote: str = "USDT"
    min_day_change_pct: float = 10.0
    min_rvol: float = 5.0
    rvol_days: int = 50
    max_float: float = 10_000_000.0
    catalyst_hours: float = 24.0
    require_catalyst: bool = True
    require_float: bool = True
    max_candidates: int = 25


def _get_json(url: str, fetch: Callable[[str], Any] | None) -> Any:
    if fetch is not None:
        return fetch(url)
    headers = {"Accept": "application/json", "User-Agent": "trendbot-scanner"}
    key = os.environ.get("COINGECKO_API_KEY")
    if key and url.startswith(CG):
        headers["x-cg-demo-api-key"] = key
    req = urllib.request.Request(url, headers=headers)  # noqa: S310 - fixed https URLs
    with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310
        return json.loads(r.read())


def circulating_supply(
    symbols: list[str], cache_path: Path, fetch: Callable[[str], Any] | None = None
) -> dict[str, dict[str, Any]]:
    """CoinGecko id (best market-cap rank for the symbol), circulating supply, market cap."""
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    ids = cache.setdefault("ids", {})
    for sym in symbols:
        if sym in ids:
            continue
        try:
            coins = _get_json(f"{CG}/search?query={sym}", fetch).get("coins") or []
        except Exception as exc:
            log.warning("CoinGecko search %s failed: %s", sym, exc)
            continue
        exact = [c for c in coins if str(c.get("symbol", "")).upper() == sym]
        exact.sort(key=lambda c: c.get("market_cap_rank") or 10**9)
        ids[sym] = exact[0]["id"] if exact else None
        time.sleep(0 if fetch else 1.5)  # the free API allows ~30 calls a minute
    want = [ids[s] for s in symbols if ids.get(s)]
    out: dict[str, dict[str, Any]] = {}
    if want:
        try:
            rows = _get_json(f"{CG}/coins/markets?vs_currency=usd&ids={','.join(want)}", fetch)
        except Exception as exc:
            log.warning("CoinGecko markets failed: %s", exc)
            rows = []
        by_id = {r["id"]: r for r in rows or []}
        for s in symbols:
            r = by_id.get(ids.get(s) or "")
            if r:
                out[s] = {
                    "id": r["id"],
                    "circulating_supply": r.get("circulating_supply"),
                    "market_cap": r.get("market_cap"),
                }
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(cache), encoding="utf-8")
    return out


def catalysts(state_dir: Path, base: str, since_ms: int) -> list[dict[str, Any]]:
    """Recent headlines / posts naming the coin (whole word, or $TICKER)."""
    try:
        from .sentiment import load_news
    except ImportError:
        return []
    rx = re.compile(rf"(?<![\w$])\$?{re.escape(base)}(?!\w)", re.IGNORECASE if len(base) > 4 else 0)
    out = []
    for item in load_news(state_dir):
        if item.fetched_ts >= since_ms and (base in item.coins or rx.search(item.title)):
            kind = (
                "unlock"
                if re.search(r"\bunlock", item.title, re.IGNORECASE)
                else ("social" if item.source.startswith("reddit") else "news")
            )
            out.append({"kind": kind, "title": item.title, "source": item.source, "url": item.url})
    return out[-5:]


def scan(  # noqa: C901 - one pass over the filters
    exchange: Any,
    state_dir: str | Path,
    settings: ScannerSettings | None = None,
    *,
    fetch: Callable[[str], Any] | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    s = settings or ScannerSettings()
    d = Path(state_dir)
    now = int(time.time() * 1000) if now_ms is None else now_ms
    tickers = exchange.fetch_tickers()
    movers = []
    for sym, t in tickers.items():
        if "/" not in sym or ":" in sym:
            continue  # spot pairs only
        base, quote = sym.split("/")
        if quote != s.quote or base in STABLES:
            continue
        pct = t.get("percentage")
        if pct is not None and pct >= s.min_day_change_pct / 2:
            movers.append((float(pct), sym, t))
    movers.sort(reverse=True)
    rows = []
    for pct24, sym, t in movers[: s.max_candidates]:
        base = sym.split("/")[0]
        try:
            daily = exchange.fetch_ohlcv(sym, "1d", limit=s.rvol_days + 1)
        except Exception as exc:
            rows.append({"symbol": sym, "error": f"{type(exc).__name__}: {exc}"})
            continue
        if len(daily) < 2:
            continue
        today, prev = daily[-1], daily[:-1][-s.rvol_days :]
        last = float(t.get("last") or today[4])
        day_pct = (last / float(today[1]) - 1) * 100 if today[1] else 0.0
        avg = statistics.fmean(float(r[5]) for r in prev) if prev else 0.0
        rvol = float(today[5]) / avg if avg else None
        rows.append(
            {
                "symbol": sym,
                "base": base,
                "last": last,
                "day_change_pct": round(day_pct, 2),
                "change_24h_pct": round(pct24, 2),
                "volume_today": float(today[5]),
                "avg_volume": round(avg, 2),
                "rvol": round(rvol, 2) if rvol is not None else None,
                "days_of_history": len(prev),
            }
        )
    ok_rows = [r for r in rows if "error" not in r]
    supply = circulating_supply([r["base"] for r in ok_rows], d / "scanner_cache.json", fetch)
    since = now - int(s.catalyst_hours * 3_600_000)
    for r in ok_rows:
        cat = catalysts(d, r["base"], since)
        sup = supply.get(r["base"]) or {}
        r.update(
            catalysts=cat,
            circulating_supply=sup.get("circulating_supply"),
            market_cap=sup.get("market_cap"),
            coingecko_id=sup.get("id"),
        )
        why = []
        if r["day_change_pct"] < s.min_day_change_pct:
            why.append(f"up {r['day_change_pct']:.1f}% today (needs {s.min_day_change_pct:g}%)")
        if r["rvol"] is None or r["rvol"] < s.min_rvol:
            why.append(
                f"RVOL {r['rvol'] or 0:.1f}x (needs {s.min_rvol:g}x the {s.rvol_days}-day average)"
            )
        if s.require_catalyst and not cat:
            why.append(f"no news, social or unlock catalyst in {s.catalyst_hours:g}h")
        cs = r["circulating_supply"]
        if s.require_float and (cs is None or cs >= s.max_float):
            why.append("float unknown" if cs is None else f"float {cs:,.0f} >= {s.max_float:,.0f}")
        r["passes"] = not why
        r["why"] = "; ".join(why) or "passes every filter"
    rows.sort(key=lambda r: (not r.get("passes", False), -(r.get("day_change_pct") or 0)))
    out = {
        "scanned_ms": now,
        "scanned_utc": ms_to_iso(now),
        "exchange": getattr(exchange, "id", "?"),
        "settings": asdict(s),
        "tickers": len(tickers),
        "rows": rows,
        "passing": [r["symbol"] for r in rows if r.get("passes")],
    }
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (OUT_FILE + ".tmp")
    tmp.write_text(json.dumps(out, indent=1), encoding="utf-8")
    tmp.replace(d / OUT_FILE)
    return out


def collect(
    state_dir: str | Path,
    settings: Mapping[str, Any] | None = None,
    fetch: Any = None,
    exchange: Any = None,
) -> dict[str, Any]:
    """The hourly job's entry point (retrain.py collect)."""
    s = ScannerSettings(**(settings or {}))
    if not s.enabled:
        return {"skipped": "scanner disabled"}
    if exchange is None:
        return {"skipped": "no public exchange connection (install ccxt)"}
    res = scan(exchange, state_dir, s, fetch=fetch)
    return {"candidates": len(res["rows"]), "passing": res["passing"]}


def load(state_dir: str | Path) -> dict[str, Any]:
    p = Path(state_dir) / OUT_FILE
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        return {}
