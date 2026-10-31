"""Arbitrage monitor: price gaps between exchanges (spatial) and inside one (triangular).

Uses only the exchanges' public order-book tops (no API keys), the same way the market
terminal does. Every gap is shown **after taker fees** on each leg; a gap that is gone after
fees is not an opportunity.

* **Spatial**: buy a coin where it is cheapest, sell it where it is dearest. This needs money
  already waiting on both exchanges (moving coins takes minutes and costs a withdrawal fee,
  which is listed but not subtracted, because it is a fixed amount, not a percentage).
* **Triangular**: three trades on one exchange that end in the coin you started with, e.g.
  USDT -> BTC -> ETH -> USDT. All three legs must fill at the shown prices at once.

This module only watches and keeps a log (``arbitrage.jsonl``): real arbitrage is a speed race
against professional market makers with co-located servers, so the dashboard reports what it
sees and how long gaps lasted instead of firing orders.
"""

from __future__ import annotations

import argparse
import json
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

from .dex_scan import FetchError, default_fetch, fetch_json


Book = dict[str, dict[str, float]]  # "BASE/QUOTE" -> {"bid", "ask", "bid_qty", "ask_qty"}
Fetch = Callable[..., bytes]

# taker fee per leg as a fraction (the exchanges' entry tiers; change them to your own tier)
FEES = {
    "binance": 0.001,
    "binanceus": 0.006,
    "okx": 0.001,
    "bybit": 0.001,
    "kucoin": 0.001,
    "kraken": 0.004,
    "coinbase": 0.006,
}
EXCHANGES = tuple(FEES)
DEFAULT_EXCHANGES = ("binance", "okx", "bybit", "kucoin", "kraken", "coinbase")
COINBASE_PRODUCTS = (
    "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "DOGE-USD", "ADA-USD", "AVAX-USD", "LINK-USD",
    "LTC-USD", "DOT-USD", "BTC-USDT", "ETH-USDT", "SOL-USDT", "ETH-BTC", "SOL-BTC",
)  # fmt: skip
# longest first, so "FDUSD" is tried before "USD"
QUOTES = ("FDUSD", "USDT", "USDC", "TUSD", "BUSD", "USD", "EUR", "TRY", "BTC", "ETH", "BNB")
KRAKEN_ALIASES = {"XBT": "BTC", "XDG": "DOGE"}
SUSPECT_GAP = 0.15  # a gap this big is almost always two different tokens sharing a ticker
LOG_FILE = "arbitrage.jsonl"


def split_symbol(sym: str) -> str | None:
    """'BTCUSDT' -> 'BTC/USDT' (None when the quote currency is not a known one)."""
    s = sym.upper()
    for q in QUOTES:
        if s.endswith(q) and len(s) > len(q):
            return f"{s[: -len(q)]}/{q}"
    return None


def _row(bid: Any, ask: Any, bq: Any = 0, aq: Any = 0) -> dict[str, float] | None:
    try:
        b, a = float(bid), float(ask)
    except (TypeError, ValueError):
        return None
    if b <= 0 or a <= 0 or b > a:  # a crossed or empty book is bad data
        return None
    return {"bid": b, "ask": a, "bid_qty": float(bq or 0), "ask_qty": float(aq or 0)}


# ------------------------------------------------------------------------- order-book tops
def _binance(fetch: Fetch, host: str) -> Book:
    out: Book = {}
    for t in fetch_json(fetch, f"https://{host}/api/v3/ticker/bookTicker"):
        pair = split_symbol(str(t.get("symbol", "")))
        r = _row(t.get("bidPrice"), t.get("askPrice"), t.get("bidQty"), t.get("askQty"))
        if pair and r:
            out[pair] = r
    return out


def _okx(fetch: Fetch) -> Book:
    data = fetch_json(fetch, "https://www.okx.com/api/v5/market/tickers?instType=SPOT")
    out: Book = {}
    for t in data.get("data") or []:
        r = _row(t.get("bidPx"), t.get("askPx"), t.get("bidSz"), t.get("askSz"))
        if r and "-" in str(t.get("instId")):
            out[str(t["instId"]).replace("-", "/")] = r
    return out


def _bybit(fetch: Fetch) -> Book:
    data = fetch_json(fetch, "https://api.bybit.com/v5/market/tickers?category=spot")
    out: Book = {}
    for t in (data.get("result") or {}).get("list") or []:
        pair = split_symbol(str(t.get("symbol", "")))
        r = _row(t.get("bid1Price"), t.get("ask1Price"), t.get("bid1Size"), t.get("ask1Size"))
        if pair and r:
            out[pair] = r
    return out


def _kucoin(fetch: Fetch) -> Book:
    data = fetch_json(fetch, "https://api.kucoin.com/api/v1/market/allTickers")
    out: Book = {}
    for t in (data.get("data") or {}).get("ticker") or []:
        r = _row(t.get("buy"), t.get("sell"))
        if r and "-" in str(t.get("symbol")):
            out[str(t["symbol"]).replace("-", "/")] = r
    return out


def _kraken_name(wsname: str) -> str:
    base, _, quote = wsname.partition("/")
    return f"{KRAKEN_ALIASES.get(base, base)}/{KRAKEN_ALIASES.get(quote, quote)}"


def _kraken(fetch: Fetch) -> Book:
    pairs = fetch_json(fetch, "https://api.kraken.com/0/public/AssetPairs").get("result") or {}
    names = {k: _kraken_name(v["wsname"]) for k, v in pairs.items() if v.get("wsname")}
    tick = fetch_json(fetch, "https://api.kraken.com/0/public/Ticker").get("result") or {}
    out: Book = {}
    for k, t in tick.items():
        r = _row((t.get("b") or [0])[0], (t.get("a") or [0])[0],
                 (t.get("b") or [0, 0, 0])[2], (t.get("a") or [0, 0, 0])[2])  # fmt: skip
        if k in names and r:
            out[names[k]] = r
    return out


def _coinbase(fetch: Fetch, products: Iterable[str] = COINBASE_PRODUCTS) -> Book:
    out: Book = {}
    err: FetchError | None = None
    for pid in products:  # Coinbase has no all-products ticker, so only the main pairs
        try:
            t = fetch_json(fetch, f"https://api.exchange.coinbase.com/products/{pid}/ticker")
        except FetchError as exc:
            err = exc
            continue
        r = _row(t.get("bid"), t.get("ask"))
        if r:
            out[pid.replace("-", "/")] = r
    if not out and err is not None:
        raise err
    return out


READERS: dict[str, Callable[[Fetch], Book]] = {
    "binance": lambda f: _binance(f, "api.binance.com"),
    "binanceus": lambda f: _binance(f, "api.binance.us"),
    "okx": _okx,
    "bybit": _bybit,
    "kucoin": _kucoin,
    "kraken": _kraken,
    "coinbase": _coinbase,
}


def books(
    exchanges: Iterable[str] = DEFAULT_EXCHANGES, fetch: Fetch = default_fetch
) -> tuple[dict[str, Book], dict[str, str]]:
    """Order-book tops per exchange, and the reason for each one that could not be read."""
    out: dict[str, Book] = {}
    errors: dict[str, str] = {}
    for ex in exchanges:
        if ex not in READERS:
            errors[ex] = "unknown exchange"
            continue
        try:
            out[ex] = READERS[ex](fetch)
        except (FetchError, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            from .connections import plain_error

            msg = plain_error(exc.__cause__ or exc) if isinstance(exc, FetchError) else str(exc)
            if "451" in str(exc):
                msg = "blocked in your country (HTTP 451)"
            errors[ex] = msg[:200] or type(exc).__name__
    return out, errors


# ------------------------------------------------------------------------------ spatial
def spatial(
    all_books: Mapping[str, Book],
    fees: Mapping[str, float] = FEES,
    min_net: float = 0.0,
    top: int = 30,
) -> list[dict[str, Any]]:
    """For each pair on 2+ exchanges: buy at the lowest ask, sell at the highest bid."""
    pairs: dict[str, list[str]] = {}
    for ex, b in all_books.items():
        for p in b:
            pairs.setdefault(p, []).append(ex)
    rows = []
    for pair, exs in pairs.items():
        if len(exs) < 2:
            continue
        buy = min(exs, key=lambda e: all_books[e][pair]["ask"])
        sell = max((e for e in exs if e != buy), key=lambda e: all_books[e][pair]["bid"])
        a, b = all_books[buy][pair], all_books[sell][pair]
        gross = b["bid"] / a["ask"] - 1
        fb, fs = fees.get(buy, 0.002), fees.get(sell, 0.002)
        net = b["bid"] / a["ask"] * (1 - fb) * (1 - fs) - 1
        if net < min_net:
            continue
        qty = min(a["ask_qty"], b["bid_qty"]) if a["ask_qty"] and b["bid_qty"] else None
        rows.append(
            {
                "pair": pair,
                "buy_on": buy,
                "buy_at": a["ask"],
                "sell_on": sell,
                "sell_at": b["bid"],
                "gross_pct": round(gross * 100, 4),
                "fees_pct": round((fb + fs) * 100, 3),
                "net_pct": round(net * 100, 4),
                "size_quote": round(qty * a["ask"], 2) if qty else None,
                "suspect": gross > SUSPECT_GAP,
                "note": (
                    "probably two different tokens with the same ticker"
                    if gross > SUSPECT_GAP
                    else "needs money on both exchanges; withdrawal fees not counted"
                ),
            }
        )
    rows.sort(key=lambda r: (r["suspect"], -r["net_pct"]))
    return rows[:top]


# ---------------------------------------------------------------------------- triangular
def _edges(book: Book, fee: float) -> dict[str, list[tuple[str, float, str, float]]]:
    """currency -> [(next currency, rate after fee, market, most it takes in, in the source)]"""
    g: dict[str, list[tuple[str, float, str, float]]] = {}
    for pair, r in book.items():
        base, _, quote = pair.partition("/")
        # quote -> base: buy base at the ask; base -> quote: sell base at the bid
        g.setdefault(quote, []).append((base, (1 - fee) / r["ask"], pair, r["ask_qty"] * r["ask"]))
        g.setdefault(base, []).append((quote, r["bid"] * (1 - fee), pair, r["bid_qty"]))
    return g


def triangular(
    book: Book,
    fee: float = 0.001,
    start: str = "USDT",
    min_net: float = 0.0,
    top: int = 20,
) -> list[dict[str, Any]]:
    """Every start -> X -> Y -> start loop on one exchange, net of three taker fees."""
    g = _edges(book, fee)
    rows = []
    for x, r1, m1, s1 in g.get(start, []):
        for y, r2, m2, s2 in g.get(x, []):
            if y in (start, x):
                continue
            for z, r3, m3, s3 in g.get(y, []):
                if z != start:
                    continue
                net = r1 * r2 * r3 - 1
                if net < min_net or net > SUSPECT_GAP:
                    continue
                # each leg's top-of-book limit, converted back into the start currency
                caps = (s1, s2 / r1, s3 / (r1 * r2))
                rows.append(
                    {
                        "route": f"{start} → {x} → {y} → {start}",
                        "markets": [m1, m2, m3],
                        "net_pct": round(net * 100, 4),
                        "fees_pct": round(3 * fee * 100, 3),
                        "size_start": round(min(caps), 2) if all(caps) else None,
                    }
                )
    rows.sort(key=lambda r: -r["net_pct"])
    return rows[:top]


# ------------------------------------------------------------------------- scan + log
def scan(
    exchanges: Iterable[str] = DEFAULT_EXCHANGES,
    fetch: Fetch = default_fetch,
    fees: Mapping[str, float] | None = None,
    min_net: float = -0.05,
    starts: Iterable[str] = ("USDT", "USD"),
) -> dict[str, Any]:
    """One pass: spatial gaps across ``exchanges`` and triangular loops inside each one.

    ``min_net`` is in percent; the default shows the closest gaps even when slightly
    negative, so you can see how near the market is to paying.
    """
    f = {**FEES, **(fees or {})}
    all_books, errors = books(exchanges, fetch)
    tri = {}
    for ex, b in all_books.items():
        rows: list[dict[str, Any]] = []
        for s in starts:
            if any(p.endswith("/" + s) for p in b):
                rows += triangular(b, f.get(ex, 0.002), s, min_net / 100)
        tri[ex] = sorted(rows, key=lambda r: -r["net_pct"])[:10]
    return {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "exchanges": {ex: len(b) for ex, b in all_books.items()},
        "errors": errors,
        "fees_pct": {ex: round(f[ex] * 100, 3) for ex in all_books},
        "spatial": spatial(all_books, f, min_net / 100),
        "triangular": tri,
    }


def record(state_dir: str | Path, result: Mapping[str, Any], min_net_pct: float = 0.05) -> int:
    """Append the gaps that pay at least ``min_net_pct`` after fees to the log; returns count."""
    hits = [
        {"ts": result["ts"], "kind": "spatial", **r}
        for r in result.get("spatial") or []
        if r["net_pct"] >= min_net_pct and not r["suspect"]
    ]
    for ex, rows in (result.get("triangular") or {}).items():
        hits += [
            {"ts": result["ts"], "kind": "triangular", "exchange": ex, **r}
            for r in rows
            if r["net_pct"] >= min_net_pct
        ]
    if hits:
        p = Path(state_dir) / LOG_FILE
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:
            fh.writelines(json.dumps(h) + "\n" for h in hits)
    return len(hits)


def history(state_dir: str | Path, limit: int = 2000) -> dict[str, Any]:
    """How often a paying gap appeared, the average and best, per route (from the log)."""
    p = Path(state_dir) / LOG_FILE
    rows = []
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines()[-limit:]:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    by: dict[str, dict[str, Any]] = {}
    for r in rows:
        key = (
            f"{r['pair']} {r['buy_on']}→{r['sell_on']}"
            if r["kind"] == "spatial"
            else f"{r['exchange']}: {r['route']}"
        )
        s = by.setdefault(key, {"route": key, "kind": r["kind"], "seen": 0, "best_pct": -1e9,
                                "sum": 0.0, "first": r["ts"], "last": r["ts"]})  # fmt: skip
        s["seen"] += 1
        s["sum"] += r["net_pct"]
        s["best_pct"] = max(s["best_pct"], r["net_pct"])
        s["last"] = r["ts"]
    out = []
    for s in by.values():
        s["avg_pct"] = round(s.pop("sum") / s["seen"], 4)
        out.append(s)
    out.sort(key=lambda s: -s["seen"])
    return {"entries": len(rows), "routes": out[:50]}


class Monitor:
    """The dashboard's cached view (public endpoints are rate limited: one scan per 15 s)."""

    TTL_S = 15.0

    def __init__(self, state_dir: str | Path, fetch: Fetch = default_fetch) -> None:
        self.state_dir = Path(state_dir)
        self.fetch = fetch
        self._last: tuple[float, dict[str, Any]] | None = None

    def view(self, q: Mapping[str, str]) -> dict[str, Any]:
        exs = [e for e in (q.get("exchanges") or "").split(",") if e in READERS]
        exs = exs or list(DEFAULT_EXCHANGES)
        key = ",".join(exs)
        last = self._last
        if (
            last
            and last[1].get("_key") == key
            and time.monotonic() - last[0] < self.TTL_S
            and q.get("fresh") != "1"
        ):
            return last[1]
        res = scan(exs, self.fetch)
        res["logged"] = record(self.state_dir, res)
        res["history"] = history(self.state_dir)
        res["_key"] = key
        self._last = (time.monotonic(), res)
        return res


def _print(res: Mapping[str, Any]) -> None:
    print(f"arbitrage scan {res['ts']}  books: {res['exchanges']}")
    for ex, err in res["errors"].items():
        print(f"  {ex}: not read ({err})")
    print("\nSPATIAL (buy low on one exchange, sell high on another), net of taker fees")
    for r in res["spatial"][:15]:
        flag = "  ⚠ suspect" if r["suspect"] else ""
        buy = f"buy {r['buy_on']:<9} {r['buy_at']:<14.8g}"
        sell = f"sell {r['sell_on']:<9} {r['sell_at']:<14.8g}"
        print(f"  {r['pair']:<14} {buy} {sell} net {r['net_pct']:+.3f}%{flag}")
    print("\nTRIANGULAR (three trades on one exchange), net of three taker fees")
    for ex, rows in res["triangular"].items():
        for r in rows[:5]:
            print(f"  {ex:<9} {r['route']:<34} net {r['net_pct']:+.3f}%")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="trendbot arbitrage", description="watch arbitrage gaps")
    p.add_argument("--exchanges", default=",".join(DEFAULT_EXCHANGES))
    p.add_argument("--min-net", type=float, default=-0.05, help="percent, after fees")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    res = scan([e.strip() for e in a.exchanges.split(",") if e.strip()], min_net=a.min_net)
    print(json.dumps(res, indent=1) if a.json else "", end="")
    if not a.json:
        _print(res)
    return 0 if res["exchanges"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
