"""Smart money: learn from other traders, rank them, and copy the qualified ones you follow.

Where other traders' trades come from (``traders.json``, next to ``connections.json``)::

    {"traders": {
        "alice":  {"type": "file",   "path": "alice_binance_export.csv"},
        "signals":{"type": "url",    "url": "https://example.com/fills.json"},
        "paper2": {"type": "bot",    "path": "trendbot_state/other-bot"},
        "whale":  {"type": "solana", "address": "<public wallet address>"}},
     "follow": ["alice"],
     "rules": {"min_trades": 30, "min_win_rate": 0.45, "min_profit_factor": 1.3,
               "max_drawdown_pct": 35, "styles": ["intraday", "swing", "position"],
               "copy_window_hours": 12}}

- ``file`` / ``url``: a trade history as CSV or JSON fills (time, pair, side, price, qty, fee:
  a Binance "Trade History" export works as is), or JSON ``round_trips``;
- ``bot``: another trendbot's state folder (its journal);
- ``solana``: a public wallet; its swaps are read from the chain (public RPC, or
  ``SOLANA_RPC_URL``) and turned into round trips per token. Watch-only: the bot holds no
  wallet keys and never trades DEX tokens.

Fills become ROUND TRIPS per pair (flat -> position -> flat). Per trader: trades, win rate
and its Wilson 95% lower bound (3 lucky wins are not a 100% win rate), average return,
profit factor, max drawdown, holding style, and a t-score used for ranking.

QUALIFIED = enough trades, win-rate lower bound, profit factor and drawdown within the rules,
and a style this 4-hour bot can follow (scalpers' entries are gone by the next close).

COPY: when a followed, qualified trader holds a position opened within ``copy_window_hours``
on a pair this bot trades, the bot ARMS the pair: it takes that pair's next signal that
passes all nine rules, sized by its own stop and risk limit. It never mirrors blindly.

LEARNING: every trader's round trips (followed or not) join Chantisimo's memory with this
bot's own entry features at their entry time, so their losses feed recall and the mistake
book ("learn from other people's mistakes").

Refreshed by the ``traders`` job (every 5 minutes while a bot runs).
"""

from __future__ import annotations

import bisect
import csv
import io
import json
import logging
import math
import os
import re
import statistics
import time
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


log = logging.getLogger("trendbot.traders")

QUOTES = ("USDT", "USDC", "FDUSD", "BUSD", "TUSD", "USD", "EUR", "GBP", "AUD", "BTC", "ETH", "BNB")
DEFAULT_RULES = {
    "min_trades": 30,
    "min_win_rate": 0.45,  # on the Wilson 95% lower bound
    "min_profit_factor": 1.3,
    "max_drawdown_pct": 35.0,
    "styles": ["intraday", "swing", "position"],
    "copy_window_hours": 12,
}
SOL_MINT = "So11111111111111111111111111111111111111112"
STABLE_MINTS = {
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC",
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT",
}
PUBLIC_SOLANA_RPC = "https://api.mainnet-beta.solana.com"
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


class TraderError(ValueError):
    """A trader source could not be added or read; safe to show."""


@dataclass
class Fill:
    ts: int
    pair: str
    side: str  # buy | sell
    price: float
    qty: float
    fee: float = 0.0  # in quote


@dataclass
class RoundTrip:
    pair: str
    entry_ts: int
    exit_ts: int | None  # None = still open
    entry_price: float
    exit_price: float | None
    cost: float  # quote spent
    pnl: float | None
    ret_pct: float | None

    @property
    def is_open(self) -> bool:
        return self.exit_ts is None


# ---------------------------------------------------------------------- paths
def traders_dir() -> Path:
    env = os.environ.get("TRENDBOT_TRADERS_DIR")
    if env:
        return Path(env)
    from .connections import connections_path

    return connections_path().parent


def registry_path() -> Path:
    return traders_dir() / "traders.json"


def status_path() -> Path:
    return traders_dir() / "traders_status.json"


def load_registry() -> dict[str, Any]:
    p = registry_path()
    raw = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    raw.setdefault("traders", {})
    raw.setdefault("follow", [])
    raw["rules"] = {**DEFAULT_RULES, **(raw.get("rules") or {})}
    return raw


def save_registry(reg: Mapping[str, Any]) -> None:
    p = registry_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(reg, indent=1), encoding="utf-8")
    tmp.replace(p)


def _check_name(name: str) -> str:
    n = str(name).strip().lower()
    if not _NAME.match(n):
        raise TraderError("trader name: lowercase letters, digits, - and _ (max 32)")
    return n


def add_trader(name: str, kind: str, value: str) -> str:
    n = _check_name(name)
    value = str(value).strip()
    if kind in ("file", "bot"):
        if not Path(value).exists():
            raise TraderError(f"{value} does not exist")
        entry = {"type": kind, "path": value}
    elif kind == "url":
        if not value.startswith("https://"):
            raise TraderError("a trader feed URL must be https://")
        entry = {"type": "url", "url": value}
    elif kind == "solana":
        if not re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]{32,44}", value):
            raise TraderError("that is not a Solana address")
        entry = {"type": "solana", "address": value}
    else:
        raise TraderError("type must be file, url, bot or solana")
    reg = load_registry()
    reg["traders"][n] = entry
    save_registry(reg)
    return f"trader {n} added ({kind}); refresh to rank them"


def remove_trader(name: str) -> str:
    n = _check_name(name)
    reg = load_registry()
    if reg["traders"].pop(n, None) is None:
        raise TraderError(f"no trader named {n}")
    reg["follow"] = [f for f in reg["follow"] if f != n]
    save_registry(reg)
    return f"trader {n} removed"


def set_follow(name: str, follow: bool) -> str:
    n = _check_name(name)
    reg = load_registry()
    if n not in reg["traders"]:
        raise TraderError(f"no trader named {n}")
    reg["follow"] = sorted(set(reg["follow"]) - {n} | ({n} if follow else set()))
    save_registry(reg)
    return f"{'following' if follow else 'no longer following'} {n}"


# ---------------------------------------------------------------------- parsing
def norm_pair(raw: str) -> str | None:
    s = str(raw).strip().upper().replace("-", "/").replace("_", "/").replace(" ", "")
    if "/" in s:
        base, quote = s.split("/", 1)
        return f"{base}/{quote.split(':')[0]}" if base and quote else None
    for q in QUOTES:
        if s.endswith(q) and len(s) > len(q):
            return f"{s[: -len(q)]}/{q}"
    return None


def _num(x: Any) -> float | None:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    m = re.match(r"^\s*([-+]?[0-9][0-9,]*\.?[0-9]*(?:[eE][-+]?\d+)?)", str(x))
    return float(m.group(1).replace(",", "")) if m else None


def parse_time(x: Any) -> int | None:
    if x is None or x == "":
        return None
    v = _num(x) if re.fullmatch(r"\s*\d{9,14}(\.\d+)?\s*", str(x)) else None
    if v is not None:
        return int(v if v > 1e11 else v * 1000)
    s = str(x).strip().replace("Z", "+00:00")
    for fmt in (
        None,
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M:%S",
    ):
        try:
            d = datetime.fromisoformat(s) if fmt is None else datetime.strptime(s, fmt)
        except ValueError:
            continue
        if d.tzinfo is None:
            d = d.replace(tzinfo=UTC)
        return int(d.timestamp() * 1000)
    return None


_COLS = {
    "ts": (
        "date(utc)",
        "date(utc+0)",
        "time",
        "timestamp",
        "datetime",
        "date",
        "created_at",
        "filled time",
    ),
    "pair": ("pair", "symbol", "market", "instrument", "product"),
    "side": ("side", "direction", "type"),
    "price": ("price", "avg price", "average price", "fill price", "avg_price"),
    "qty": ("executed", "qty", "quantity", "size", "filled", "amount (base)", "amount"),
    "fee": ("fee", "commission", "fees"),
}


def _pick(row: Mapping[str, Any], key: str) -> Any:
    low = {str(k).strip().lower(): v for k, v in row.items()}
    for name in _COLS[key]:
        if name in low and low[name] not in (None, ""):
            return low[name]
    return None


def fills_from_rows(rows: Sequence[Mapping[str, Any]]) -> list[Fill]:
    out = []
    for r in rows:
        ts, pair = parse_time(_pick(r, "ts")), norm_pair(_pick(r, "pair") or "")
        side = str(_pick(r, "side") or "").strip().lower()
        side = (
            "buy"
            if side in ("buy", "b", "long", "bid")
            else "sell"
            if side in ("sell", "s", "short", "ask")
            else ""
        )
        price, qty = _num(_pick(r, "price")), _num(_pick(r, "qty"))
        if ts is None or pair is None or not side or not price or not qty:
            continue
        out.append(
            Fill(ts, pair, side, float(price), abs(float(qty)), abs(_num(_pick(r, "fee")) or 0.0))
        )
    return sorted(out, key=lambda f: f.ts)


def parse_history(text: str) -> tuple[list[Fill], list[RoundTrip]]:
    """CSV or JSON fills, or JSON {"round_trips": [...]}."""
    t = text.strip()
    if t.startswith(("[", "{")):
        data = json.loads(t)
        if isinstance(data, dict) and "round_trips" in data:
            return [], [_trip_from(d) for d in data["round_trips"]]
        rows = data if isinstance(data, list) else data.get("fills") or data.get("trades") or []
        return fills_from_rows(rows), []
    return fills_from_rows(list(csv.DictReader(io.StringIO(t)))), []


def _trip_from(d: Mapping[str, Any]) -> RoundTrip:
    entry, exit_ = float(d["entry_price"]), _num(d.get("exit_price"))
    qty = float(d.get("qty") or 1.0)
    pnl = _num(d.get("pnl"))
    if pnl is None and exit_ is not None:
        pnl = (exit_ - entry) * qty
    cost = entry * qty
    return RoundTrip(
        norm_pair(d["pair"]) or str(d["pair"]),
        parse_time(d["entry_ts"]) or 0,
        parse_time(d.get("exit_ts")),
        entry,
        exit_,
        cost,
        pnl,
        (pnl / cost * 100) if pnl is not None and cost else None,
    )


def round_trips(fills: Sequence[Fill]) -> list[RoundTrip]:
    """Flat -> long -> flat per pair; partial fills and partial exits are aggregated."""
    pos: dict[str, dict[str, float]] = {}
    out: list[RoundTrip] = []
    for f in sorted(fills, key=lambda f: f.ts):
        p = pos.get(f.pair)
        if f.side == "buy":
            if p is None:
                p = pos[f.pair] = {
                    "qty": 0.0,
                    "cost": 0.0,
                    "proceeds": 0.0,
                    "sold": 0.0,
                    "ts": f.ts,
                }
            p["qty"] += f.qty
            p["cost"] += f.qty * f.price + f.fee
            continue
        if p is None or p["qty"] <= 0:
            continue  # a sell with no position in this history: nothing to pair it with
        q = min(f.qty, p["qty"])
        p["qty"] -= q
        p["sold"] += q
        p["proceeds"] += q * f.price - f.fee * (q / f.qty)
        if p["qty"] <= 1e-12 * max(1.0, p["sold"]):
            bought = p["sold"]
            pnl = p["proceeds"] - p["cost"]
            out.append(
                RoundTrip(
                    f.pair,
                    int(p["ts"]),
                    f.ts,
                    p["cost"] / bought,
                    p["proceeds"] / bought,
                    p["cost"],
                    pnl,
                    pnl / p["cost"] * 100 if p["cost"] else None,
                )
            )
            del pos[f.pair]
    for pair, p in pos.items():
        if p["qty"] > 0:
            held = p["qty"] + p["sold"]
            out.append(
                RoundTrip(pair, int(p["ts"]), None, p["cost"] / held, None, p["cost"], None, None)
            )
    return sorted(out, key=lambda t: t.entry_ts)


def trips_from_bot(state_dir: Path) -> list[RoundTrip]:
    from .journal import read_journal

    j = Path(state_dir) / "journal.csv"
    out = []
    for t in read_journal(j) if j.exists() else []:
        cost = t.entry_price * t.qty
        out.append(
            RoundTrip(
                t.pair,
                t.entry_ts,
                t.exit_ts,
                t.entry_price,
                t.exit_price,
                cost,
                t.pnl,
                (t.pnl / cost * 100) if t.pnl is not None and cost else None,
            )
        )
    return out


# ---------------------------------------------------------------------- Solana wallets
def _rpc(url: str, method: str, params: list[Any], fetch: Callable[..., Any] | None) -> Any:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    if fetch is not None:
        return fetch(url, body)
    req = urllib.request.Request(  # noqa: S310 - the RPC URL is https
        url, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 - https RPC URL
        return json.loads(r.read())["result"]


def solana_fills(
    address: str, cache: Path, *, fetch: Callable[..., Any] | None = None, limit: int = 100
) -> list[Fill]:
    """Swaps of a public wallet as fills (token vs SOL/USDC/USDT); new signatures only."""
    url = os.environ.get("SOLANA_RPC_URL") or PUBLIC_SOLANA_RPC
    seen: dict[str, Any] = json.loads(cache.read_text(encoding="utf-8")) if cache.exists() else {}
    sigs = _rpc(url, "getSignaturesForAddress", [address, {"limit": limit}], fetch) or []
    for s in sigs:
        sig = s.get("signature")
        if not sig or sig in seen or s.get("err"):
            continue
        tx = _rpc(
            url,
            "getTransaction",
            [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
            fetch,
        )
        seen[sig] = _swap_from_tx(tx, address)
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(seen), encoding="utf-8")
    fills = [Fill(**f) for f in seen.values() if f]
    return sorted(fills, key=lambda f: f.ts)


def _swap_from_tx(tx: Mapping[str, Any] | None, owner: str) -> dict[str, Any] | None:
    if not tx or not tx.get("meta") or tx["meta"].get("err"):
        return None
    meta = tx["meta"]
    deltas: dict[str, float] = {}
    for key, sign in (("preTokenBalances", -1), ("postTokenBalances", 1)):
        for b in meta.get(key) or []:
            if b.get("owner") != owner:
                continue
            amt = float((b.get("uiTokenAmount") or {}).get("uiAmount") or 0.0)
            deltas[b["mint"]] = deltas.get(b["mint"], 0.0) + sign * amt
    keys = [
        k.get("pubkey") if isinstance(k, dict) else k
        for k in tx["transaction"]["message"]["accountKeys"]
    ]
    sol = 0.0
    if owner in keys:
        i = keys.index(owner)
        sol = (meta["postBalances"][i] - meta["preBalances"][i]) / 1e9
    sol += deltas.pop(SOL_MINT, 0.0)  # wrapped SOL counts as SOL
    quotes = {
        "SOL": sol,
        **{STABLE_MINTS[m]: deltas.pop(m) for m in list(deltas) if m in STABLE_MINTS},
    }
    tokens = {m: d for m, d in deltas.items() if abs(d) > 0}
    q = [(c, v) for c, v in quotes.items() if abs(v) > (0.002 if c == "SOL" else 0.01)]
    if len(tokens) != 1 or len(q) != 1:
        return None  # not a simple token <-> SOL/stable swap
    (mint, d), (cur, v) = next(iter(tokens.items())), q[0]
    if (d > 0) == (v > 0):
        return None
    ts = int(tx.get("blockTime") or 0) * 1000
    return {
        "ts": ts,
        "pair": f"{mint}/{cur}",
        "side": "buy" if d > 0 else "sell",
        "price": abs(v) / abs(d),
        "qty": abs(d),
        "fee": 0.0,
    }


# ---------------------------------------------------------------------- statistics
def wilson_lower(wins: int, n: int, z: float = 1.96) -> float:
    if n == 0:
        return 0.0
    p = wins / n
    den = 1 + z * z / n
    centre = p + z * z / (2 * n)
    spread = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - spread) / den)


def style_of(hours: float | None) -> str:
    if hours is None:
        return "unknown"
    return (
        "scalper"
        if hours < 1
        else "intraday"
        if hours < 24
        else "swing"
        if hours < 24 * 14
        else "position"
    )


def trader_stats(trips: Sequence[RoundTrip], rules: Mapping[str, Any]) -> dict[str, Any]:
    closed = [t for t in trips if not t.is_open and t.ret_pct is not None]
    rets = [t.ret_pct for t in closed]
    n = len(rets)
    wins = sum(r > 0 for r in rets)
    gw, gl = sum(r for r in rets if r > 0), -sum(r for r in rets if r < 0)
    eq, peak, dd = 100.0, 100.0, 0.0
    for r in rets:
        eq *= 1 + r / 100
        peak = max(peak, eq)
        dd = max(dd, (peak - eq) / peak * 100)
    hold = [(t.exit_ts - t.entry_ts) / 3.6e6 for t in closed if t.exit_ts]
    mean = statistics.fmean(rets) if rets else None
    sd = statistics.stdev(rets) if n > 1 else None
    s = {
        "trades": n,
        "open": sum(t.is_open for t in trips),
        "wins": wins,
        "win_rate": round(wins / n, 4) if n else None,
        "win_rate_lb": round(wilson_lower(wins, n), 4),
        "avg_return_pct": round(mean, 3) if mean is not None else None,
        "profit_factor": round(gw / gl, 2) if gl > 0 else (99.0 if gw else None),  # 99 = no losses
        "max_drawdown_pct": round(dd, 2),
        "median_hold_h": round(statistics.median(hold), 1) if hold else None,
        "style": style_of(statistics.median(hold) if hold else None),
        "t_score": round(mean / (sd / math.sqrt(n)), 2) if mean is not None and sd else 0.0,
        "pairs": sorted({t.pair for t in trips})[:12],
        "last_trade": max((t.exit_ts or t.entry_ts for t in trips), default=None),
    }
    why = []
    if n < rules["min_trades"]:
        why.append(f"{n} of {rules['min_trades']} trades")
    if s["win_rate_lb"] < rules["min_win_rate"]:
        why.append(
            f"win rate (95% lower bound) {s['win_rate_lb']:.0%} < {rules['min_win_rate']:.0%}"
        )
    pf = s["profit_factor"]
    if pf is None or pf < rules["min_profit_factor"]:
        why.append(f"profit factor {pf if pf is not None else '-'} < {rules['min_profit_factor']}")
    if dd > rules["max_drawdown_pct"]:
        why.append(f"drawdown {dd:.0f}% > {rules['max_drawdown_pct']}%")
    if s["style"] not in rules["styles"]:
        why.append(f"style {s['style']} can't be followed by a 4H bot")
    s["qualified"] = not why
    s["why"] = "; ".join(why) or "meets every rule"
    return s


# ---------------------------------------------------------------------- refresh (job)
def _read_source(
    entry: Mapping[str, Any], cache_dir: Path, name: str, fetch: Any
) -> list[RoundTrip]:
    kind = entry.get("type")
    if kind == "bot":
        return trips_from_bot(Path(entry["path"]))
    if kind == "file":
        fills, trips = parse_history(Path(entry["path"]).read_text(encoding="utf-8-sig"))
        return trips or round_trips(fills)
    if kind == "url":
        if fetch is not None:
            text = fetch(entry["url"], None)
        else:
            with urllib.request.urlopen(entry["url"], timeout=20) as r:  # noqa: S310 - https only
                text = r.read().decode("utf-8-sig")
        fills, trips = parse_history(text)
        return trips or round_trips(fills)
    if kind == "solana":
        return round_trips(
            solana_fills(entry["address"], cache_dir / f"{name}.solana.json", fetch=fetch)
        )
    raise TraderError(f"unknown source type {kind!r}")


def refresh(fetch: Any = None, now_ms: int | None = None) -> dict[str, Any]:
    """Re-read every trader, rank them, list copy signals; writes traders_status.json."""
    reg = load_registry()
    rules = reg["rules"]
    d = traders_dir() / "traders"
    d.mkdir(parents=True, exist_ok=True)
    now = int(time.time() * 1000) if now_ms is None else now_ms
    rows, signals = [], []
    for name, entry in sorted(reg["traders"].items()):
        try:
            trips = _read_source(entry, d, name, fetch)
        except Exception as exc:  # one broken source never stops the others
            rows.append(
                {"name": name, "type": entry.get("type"), "error": f"{type(exc).__name__}: {exc}"}
            )
            continue
        (d / f"{name}.trips.json").write_text(
            json.dumps([asdict(t) for t in trips]), encoding="utf-8"
        )
        st = trader_stats(trips, rules)
        st.update(name=name, type=entry.get("type"), following=name in reg["follow"])
        rows.append(st)
        if st["following"] and st["qualified"] and entry.get("type") != "solana":
            window = rules["copy_window_hours"] * 3_600_000
            for t in trips:
                if t.is_open and now - t.entry_ts <= window:
                    signals.append(
                        {
                            "trader": name,
                            "pair": t.pair,
                            "entry_ts": t.entry_ts,
                            "entry_price": t.entry_price,
                            "id": f"{name}:{t.pair}:{t.entry_ts}",
                        }
                    )
    rows.sort(key=lambda r: (not r.get("qualified", False), -(r.get("t_score") or 0)))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    status = {"refreshed_ms": now, "rules": rules, "traders": rows, "copy_signals": signals}
    tmp = status_path().with_name("traders_status.json.tmp")
    tmp.write_text(json.dumps(status, indent=1, default=str), encoding="utf-8")
    tmp.replace(status_path())
    return status


def read_status() -> dict[str, Any]:
    p = status_path()
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        return {}


# ---------------------------------------------------------------------- the bot side
def copy_signals_for(pairs: Sequence[str], done: set[str]) -> list[dict[str, Any]]:
    """Open copy signals of followed, qualified traders on these pairs not yet acted on."""
    return [
        s
        for s in read_status().get("copy_signals") or []
        if s["pair"] in pairs and s["id"] not in done
    ]


def trader_memory(candles: Mapping[str, Sequence[Any]], cfg: Any) -> list[Any]:
    """Every trader's closed round trips on pairs this bot has candles for, as Chantisimo
    memory: this bot's entry features at their entry time, R estimated from their losses."""
    from .chantisimo import Memory
    from .indicators import compute_features
    from .models import Trade

    d = traders_dir() / "traders"
    if not d.exists():
        return []
    rows_by_pair = {p: compute_features(cs, cfg) for p, cs in candles.items() if cs}
    close_ts = {p: [r.close_ts for r in rows] for p, rows in rows_by_pair.items()}
    out: list[Any] = []
    for i, f in enumerate(sorted(d.glob("*.trips.json"))):
        name = f.name[: -len(".trips.json")]
        trips = [RoundTrip(**t) for t in json.loads(f.read_text(encoding="utf-8"))]
        losses = [-t.ret_pct for t in trips if t.ret_pct is not None and t.ret_pct < 0]
        unit = statistics.median(losses) if len(losses) >= 3 else 1.0  # their typical loss = 1R
        for j, t in enumerate(trips):
            if t.is_open or t.ret_pct is None or t.pair not in rows_by_pair:
                continue
            k = bisect.bisect_right(close_ts[t.pair], t.entry_ts) - 1
            if k < 0:
                continue
            feats = rows_by_pair[t.pair][k].ml_features()
            if not feats:
                continue
            tid = -(10_000_000 * (i + 1) + j)
            trade = Trade(
                tid,
                t.pair,
                "trader",
                rows_by_pair[t.pair][k].ts,
                t.entry_ts,
                t.entry_price,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                "",
                t.exit_ts,
                t.exit_price,
                "TP" if t.ret_pct > 0 else "SL",
                0.0,
                t.pnl,
                t.ret_pct / unit,
                feats,
            )
            out.append(Memory(f"trader:{name}", "trader", trade))
    return out


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(prog="python -m research.trendbot.traders")
    p.add_argument("command", choices=("refresh", "list"))
    args = p.parse_args(argv)
    st = refresh() if args.command == "refresh" else read_status()
    for r in st.get("traders", []):
        verdict = "QUALIFIED" if r.get("qualified") else r.get("why") or r.get("error")
        print(
            f"{r.get('rank', '-'):>3} {r['name']:<16} {r.get('trades', 0):>4} trades  "
            f"win {r.get('win_rate') or 0:.0%}  {verdict}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
