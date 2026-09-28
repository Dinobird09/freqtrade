"""On-chain / DEX memecoin scanner: public market data -> risk flags -> ranked watchlist.

ANALYSIS ONLY. This module places no trades, signs nothing, and holds no wallet keys or
private keys of any kind. It reads public, key-less HTTP APIs, computes metrics and named risk
flags, and writes a ranked watchlist (``<state_dir>/dex/watchlist.json`` and ``.csv``). Being
on the "tradable" list only means no hard-fail or unknown flag fired; it is not a
recommendation. Research tool only, not financial advice.

Sources (all public, no API key):

- DEXScreener (``https://api.dexscreener.com``): latest token profiles
  (``/token-profiles/latest/v1``), latest boosts (``/token-boosts/latest/v1``), pair search
  (``/latest/dex/search?q=``) and pairs by token address
  (``/tokens/v1/{chainId}/{addr,addr,...}``, at most 30 addresses per call). Pair fields used:
  ``chainId, dexId, url, pairAddress, baseToken{address,name,symbol}, quoteToken, priceUsd,
  txns{m5,h1,h6,h24:{buys,sells}}, volume{...}, priceChange{...}, liquidity{usd,base,quote},
  fdv, marketCap, pairCreatedAt`` (epoch ms). Every field is parsed defensively; a missing or
  malformed field becomes ``None``.
- RugCheck (Solana only): ``https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary``
  (``score``, ``risks[{name, level, description, score}]``). If RugCheck is unavailable the
  token gets an ``UNKNOWN_RUGCHECK`` flag; it is never treated as safe.
- Solana JSON-RPC (default ``https://api.mainnet-beta.solana.com``; override with the env var
  ``SOLANA_RPC_URL``, e.g. a Helius URL): ``getTokenLargestAccounts`` + ``getTokenSupply`` give
  the top-10 holder share of supply. CAVEAT: the largest *token accounts* usually include the
  liquidity pool's vault and sometimes burn / locker addresses, so the raw figure overstates
  insider concentration. Accounts whose address, or whose owner (resolved with
  ``getMultipleAccounts``/``jsonParsed`` when possible), equals the pair address are excluded,
  but other pool vaults cannot be identified reliably, so the figure is reported as an UPPER
  BOUND (``top10_is_upper_bound = True``).

Risk flags (each has a severity and a one-sentence explanation; thresholds live in
:class:`Thresholds`):

- ``hard``: excludes the token from the tradable list (still shown with its reasons);
- ``unknown``: required data missing; treated like a hard fail ("unknown never passes");
- ``soft``: a score penalty only.

Score = momentum/attention (volume acceleration h1 vs h6/h24 hourly averages, price change,
h1 transaction count, boost amount) minus per-flag penalties. Rows are ranked tradable first,
then by score.

CLI::

    python3 -m research.trendbot.dex_scan --chain solana --query bonk --from-boosts \\
        --position-usd 500 --out trendbot_state

``load_watchlist(state_dir)`` returns the last written watchlist for the dashboard.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


DEXSCREENER_BASE = "https://api.dexscreener.com"
PROFILES_URL = f"{DEXSCREENER_BASE}/token-profiles/latest/v1"
BOOSTS_URL = f"{DEXSCREENER_BASE}/token-boosts/latest/v1"
SEARCH_URL = f"{DEXSCREENER_BASE}/latest/dex/search?q="
TOKENS_URL = f"{DEXSCREENER_BASE}/tokens/v1"
RUGCHECK_URL = "https://api.rugcheck.xyz/v1/tokens/{mint}/report/summary"
DEFAULT_SOLANA_RPC = "https://api.mainnet-beta.solana.com"
ENV_SOLANA_RPC = "SOLANA_RPC_URL"

USER_AGENT = "trendbot-dex-scan/1.0 (research; read-only)"
TIMEOUT_S = 15.0
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
TOKENS_PER_CALL = 30  # DEXScreener /tokens/v1 accepts up to 30 addresses
TOP_N_HOLDERS = 10
HOUR_MS = 3_600_000

HARD = "hard"
SOFT = "soft"
UNKNOWN = "unknown"
# RugCheck risk names that are hard fails whatever level RugCheck assigns them.
HARD_RUG_KEYWORDS = ("mint authority", "freeze authority", "lp unlocked")

Fetch = Callable[..., bytes]


# ---------------------------------------------------------------------------- fetch
class FetchError(RuntimeError):
    """A source could not be fetched or decoded."""


def default_fetch(url: str, headers: dict[str, str] | None = None, data: Any = None) -> bytes:
    """GET ``url`` (or POST ``data`` as JSON when given) with a UA, 15 s timeout and size cap."""
    if not url.startswith(("https://", "http://")):
        raise FetchError(f"refusing non-http(s) URL: {url!r}")
    hdrs = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    hdrs.update(headers or {})
    body = None
    if data is not None:
        body = data if isinstance(data, bytes) else json.dumps(data).encode()
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST" if body else "GET")  # noqa: S310
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310 - scheme checked
        raw = resp.read(MAX_RESPONSE_BYTES + 1)
    if len(raw) > MAX_RESPONSE_BYTES:
        raise FetchError(f"response from {url} exceeds {MAX_RESPONSE_BYTES} bytes")
    return raw


def fetch_json(fetch: Fetch, url: str, data: Any = None) -> Any:
    """Fetch and decode JSON; every failure is re-raised as :class:`FetchError`."""
    try:
        raw = fetch(url, None, data) if data is not None else fetch(url)
        return json.loads(raw.decode("utf-8"))
    except FetchError:
        raise
    except Exception as exc:  # network, HTTP, decode: all reported uniformly
        raise FetchError(f"{type(exc).__name__}: {exc}") from exc


# ---------------------------------------------------------------------------- coercion
def _num(v: Any) -> float | None:
    """Float from a number or numeric string; ``None`` for anything else (incl. NaN/inf/bool)."""
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _int(v: Any) -> int | None:
    f = _num(v)
    return int(f) if f is not None else None


def _dict(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _str(v: Any) -> str | None:
    return v if isinstance(v, str) and v else None


def _list(v: Any) -> list[Any]:
    return v if isinstance(v, list) else []


# ---------------------------------------------------------------------------- models
@dataclass
class Pair:
    chain_id: str | None
    dex_id: str | None
    url: str | None
    pair_address: str | None
    base_address: str | None
    base_name: str | None
    base_symbol: str | None
    quote_address: str | None
    quote_symbol: str | None
    price_usd: float | None
    txns: dict[str, dict[str, int | None]]
    volume: dict[str, float | None]
    price_change: dict[str, float | None]
    liquidity_usd: float | None
    liquidity_base: float | None
    liquidity_quote: float | None
    fdv: float | None
    market_cap: float | None
    pair_created_at: int | None
    boosts_active: int | None = None


@dataclass
class RugRisk:
    name: str
    level: str | None
    description: str | None
    score: float | None


@dataclass
class RugReport:
    score: float | None
    score_normalised: float | None
    risks: list[RugRisk]


@dataclass
class HolderShare:
    top10_share: float | None  # fraction of supply, 0..1
    accounts_used: int
    excluded: list[str]
    is_upper_bound: bool = True


@dataclass
class Flag:
    name: str
    severity: str  # hard | soft | unknown
    message: str


@dataclass
class Thresholds:
    min_liquidity_usd: float = 50_000.0
    exit_liquidity_multiple: float = 20.0  # liquidity must be >= this x position size
    max_top10_share: float = 0.20
    min_pair_age_hours: float = 24.0
    max_sell_buy_ratio: float = 3.0
    honeypot_min_buys: int = 20
    honeypot_max_sell_ratio: float = 0.05
    max_volume_liquidity: float = 50.0
    min_volume_liquidity: float = 0.05
    penalty_hard: float = 100.0
    penalty_unknown: float = 50.0
    penalty_soft: float = 15.0


WINDOWS = ("m5", "h1", "h6", "h24")


# ---------------------------------------------------------------------------- parsing
def parse_pair(raw: Any) -> Pair | None:
    """One DEXScreener pair object; ``None`` if it is not an object."""
    if not isinstance(raw, dict):
        return None
    base, quote = _dict(raw.get("baseToken")), _dict(raw.get("quoteToken"))
    txns_raw, liq = _dict(raw.get("txns")), _dict(raw.get("liquidity"))
    vol, pc = _dict(raw.get("volume")), _dict(raw.get("priceChange"))
    txns = {
        w: {
            "buys": _int(_dict(txns_raw.get(w)).get("buys")),
            "sells": _int(_dict(txns_raw.get(w)).get("sells")),
        }
        for w in WINDOWS
    }
    return Pair(
        chain_id=_str(raw.get("chainId")),
        dex_id=_str(raw.get("dexId")),
        url=_str(raw.get("url")),
        pair_address=_str(raw.get("pairAddress")),
        base_address=_str(base.get("address")),
        base_name=_str(base.get("name")),
        base_symbol=_str(base.get("symbol")),
        quote_address=_str(quote.get("address")),
        quote_symbol=_str(quote.get("symbol")),
        price_usd=_num(raw.get("priceUsd")),
        txns=txns,
        volume={w: _num(vol.get(w)) for w in WINDOWS},
        price_change={w: _num(pc.get(w)) for w in WINDOWS},
        liquidity_usd=_num(liq.get("usd")),
        liquidity_base=_num(liq.get("base")),
        liquidity_quote=_num(liq.get("quote")),
        fdv=_num(raw.get("fdv")),
        market_cap=_num(raw.get("marketCap")),
        pair_created_at=_int(raw.get("pairCreatedAt")),
        boosts_active=_int(_dict(raw.get("boosts")).get("active")),
    )


def parse_pairs(data: Any) -> list[Pair]:
    """Pairs from ``/tokens/v1`` (a list) or ``/latest/dex/search`` (``{"pairs": [...]}``)."""
    items = data if isinstance(data, list) else _list(_dict(data).get("pairs"))
    return [p for p in (parse_pair(x) for x in items) if p is not None]


def parse_profiles(data: Any) -> list[dict[str, Any]]:
    """``/token-profiles/latest/v1``: list of ``{chainId, tokenAddress, url, description}``."""
    out = []
    for item in data if isinstance(data, list) else []:
        item = _dict(item)
        chain, addr = _str(item.get("chainId")), _str(item.get("tokenAddress"))
        if chain and addr:
            out.append(
                {
                    "chain_id": chain,
                    "token_address": addr,
                    "url": _str(item.get("url")),
                    "description": _str(item.get("description")),
                }
            )
    return out


def parse_boosts(data: Any) -> list[dict[str, Any]]:
    """``/token-boosts/latest/v1``: profile fields plus ``amount`` and ``totalAmount``."""
    raw = data if isinstance(data, list) else []
    out = parse_profiles(raw)
    by_key = {(_str(_dict(x).get("chainId")), _str(_dict(x).get("tokenAddress"))): x for x in raw}
    for item in out:
        src = _dict(by_key.get((item["chain_id"], item["token_address"])))
        item["amount"] = _num(src.get("amount"))
        item["total_amount"] = _num(src.get("totalAmount"))
    return out


def parse_rugcheck(data: Any) -> RugReport:
    """RugCheck ``/report/summary``; missing fields -> ``None`` / empty risk list."""
    d = _dict(data)
    risks = []
    for r in _list(d.get("risks")):
        r = _dict(r)
        name = _str(r.get("name"))
        if name:
            level = _str(r.get("level"))
            risks.append(
                RugRisk(
                    name=name,
                    level=level.lower() if level else None,
                    description=_str(r.get("description")),
                    score=_num(r.get("score")),
                )
            )
    return RugReport(
        score=_num(d.get("score")),
        score_normalised=_num(d.get("score_normalised")),
        risks=risks,
    )


# ---------------------------------------------------------------------------- solana rpc
def rpc_call(fetch: Fetch, rpc_url: str, method: str, params: list[Any]) -> Any:
    """One JSON-RPC 2.0 call; an RPC ``error`` object is raised as :class:`FetchError`."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    resp = _dict(fetch_json(fetch, rpc_url, data=payload))
    if "error" in resp:
        raise FetchError(f"{method}: RPC error {resp['error']}")
    if "result" not in resp:
        raise FetchError(f"{method}: no result in RPC response")
    return resp["result"]


def _account_owners(fetch: Fetch, rpc_url: str, addresses: list[str]) -> dict[str, str]:
    """Best-effort owner of each token account (empty dict if the RPC cannot resolve them)."""
    try:
        res = rpc_call(
            fetch, rpc_url, "getMultipleAccounts", [addresses, {"encoding": "jsonParsed"}]
        )
    except FetchError:
        return {}
    owners = {}
    for addr, acc in zip(addresses, _list(_dict(res).get("value")), strict=False):
        info = _dict(_dict(_dict(_dict(acc).get("data")).get("parsed")).get("info"))
        owner = _str(info.get("owner"))
        if owner:
            owners[addr] = owner
    return owners


def compute_top_holder_share(
    largest: Any,
    supply: Any,
    exclude: Iterable[str] = (),
    owners: dict[str, str] | None = None,
    top_n: int = TOP_N_HOLDERS,
) -> HolderShare:
    """Top-``top_n`` share of supply from ``getTokenLargestAccounts``/``getTokenSupply`` results.

    Accounts whose address or owner is in ``exclude`` (the pair address) are dropped before the
    top ``top_n`` are taken. Raw integer ``amount`` strings are used so decimals cancel out.
    """
    excl, owners = set(exclude), owners or {}
    total = _num(_dict(_dict(supply).get("value")).get("amount"))
    accounts = []
    for acc in _list(_dict(largest).get("value")):
        acc = _dict(acc)
        addr, amt = _str(acc.get("address")), _num(acc.get("amount"))
        if addr and amt is not None:
            accounts.append((addr, amt))
    excluded = [a for a, _ in accounts if a in excl or owners.get(a) in excl]
    kept = sorted((amt for a, amt in accounts if a not in excluded), reverse=True)[:top_n]
    if not total or total <= 0 or not accounts:
        return HolderShare(None, len(kept), excluded)
    return HolderShare(sum(kept) / total, len(kept), excluded)


def fetch_top_holder_share(
    fetch: Fetch, rpc_url: str, mint: str, pair_address: str | None
) -> HolderShare:
    largest = rpc_call(fetch, rpc_url, "getTokenLargestAccounts", [mint])
    supply = rpc_call(fetch, rpc_url, "getTokenSupply", [mint])
    exclude = [pair_address] if pair_address else []
    addrs = [
        a for a in (_str(_dict(x).get("address")) for x in _list(_dict(largest).get("value"))) if a
    ]
    owners = _account_owners(fetch, rpc_url, addrs) if addrs and exclude else {}
    return compute_top_holder_share(largest, supply, exclude, owners)


# ---------------------------------------------------------------------------- risk checks
def _liquidity_flags(pair: Pair, position_usd: float, t: Thresholds) -> list[Flag]:
    liq = pair.liquidity_usd
    if liq is None:
        return [Flag("UNKNOWN_LIQUIDITY", UNKNOWN, "Liquidity in USD is not reported.")]
    out = []
    if liq < t.min_liquidity_usd:
        out.append(
            Flag(
                "LOW_LIQUIDITY",
                HARD,
                f"Liquidity ${liq:,.0f} is below the ${t.min_liquidity_usd:,.0f} minimum.",
            )
        )
    need = t.exit_liquidity_multiple * position_usd
    if liq < need:
        out.append(
            Flag(
                "CANNOT_EXIT",
                HARD,
                f"Liquidity ${liq:,.0f} is under {t.exit_liquidity_multiple:g}x the "
                f"${position_usd:,.0f} position (${need:,.0f}); exiting would move the price.",
            )
        )
    return out


def _age_flags(pair: Pair, now_ms: int, t: Thresholds) -> list[Flag]:
    if pair.pair_created_at is None:
        return [Flag("UNKNOWN_PAIR_AGE", UNKNOWN, "Pair creation time is not reported.")]
    age_h = (now_ms - pair.pair_created_at) / HOUR_MS
    if age_h < t.min_pair_age_hours:
        return [
            Flag(
                "NEW_PAIR",
                HARD,
                f"Pair is {age_h:.1f}h old, younger than {t.min_pair_age_hours:g}h.",
            )
        ]
    return []


def _txn_flags(pair: Pair, t: Thresholds) -> list[Flag]:
    buys, sells = pair.txns["h24"]["buys"], pair.txns["h24"]["sells"]
    if buys is None or sells is None:
        return [Flag("UNKNOWN_TXNS", UNKNOWN, "24h buy/sell counts are not reported.")]
    if buys >= t.honeypot_min_buys and sells <= buys * t.honeypot_max_sell_ratio:
        return [
            Flag(
                "HONEYPOT_HINT",
                HARD,
                f"{buys} buys but only {sells} sells in 24h; holders may be unable to sell.",
            )
        ]
    if sells > 0 and sells > buys * t.max_sell_buy_ratio:
        return [
            Flag(
                "SELL_PRESSURE",
                SOFT,
                f"24h sells/buys ratio {sells / max(buys, 1):.1f} exceeds "
                f"{t.max_sell_buy_ratio:g}.",
            )
        ]
    return []


def _volume_flags(pair: Pair, t: Thresholds) -> list[Flag]:
    vol, liq = pair.volume["h24"], pair.liquidity_usd
    if vol is None:
        return [Flag("UNKNOWN_VOLUME", UNKNOWN, "24h volume is not reported.")]
    if not liq:
        return []  # liquidity problems are flagged by _liquidity_flags
    ratio = vol / liq
    if ratio > t.max_volume_liquidity:
        return [
            Flag(
                "VOLUME_LIQUIDITY_HIGH",
                SOFT,
                f"24h volume is {ratio:.1f}x liquidity (> {t.max_volume_liquidity:g}x), "
                "a wash-trading hint.",
            )
        ]
    if ratio < t.min_volume_liquidity:
        return [
            Flag(
                "VOLUME_LIQUIDITY_LOW",
                SOFT,
                f"24h volume is {ratio:.3f}x liquidity (< {t.min_volume_liquidity:g}x); "
                "the pool is barely traded.",
            )
        ]
    return []


def _holder_flags(holders: HolderShare | None, t: Thresholds) -> list[Flag]:
    if holders is None or holders.top10_share is None:
        return [Flag("UNKNOWN_HOLDERS", UNKNOWN, "Top-holder concentration is not available.")]
    if holders.top10_share > t.max_top10_share:
        return [
            Flag(
                "HOLDER_CONCENTRATION",
                HARD,
                f"Top-10 accounts hold {holders.top10_share:.1%} of supply "
                f"(> {t.max_top10_share:.0%}; upper bound, may include pool vaults).",
            )
        ]
    return []


def _rug_flags(rug: RugReport | None) -> list[Flag]:
    if rug is None:
        return [Flag("UNKNOWN_RUGCHECK", UNKNOWN, "RugCheck report is not available.")]
    hard = [
        r
        for r in rug.risks
        if r.level == "danger" or any(k in r.name.lower() for k in HARD_RUG_KEYWORDS)
    ]
    warn = [r for r in rug.risks if r.level == "warn" and r not in hard]
    out = []
    if hard:
        names = "; ".join(r.name for r in hard)
        out.append(Flag("RUGCHECK_DANGER", HARD, f"RugCheck danger-level risks: {names}."))
    if warn:
        names = "; ".join(r.name for r in warn)
        out.append(Flag("RUGCHECK_WARN", SOFT, f"RugCheck warnings: {names}."))
    return out


def evaluate_flags(
    pair: Pair | None,
    position_usd: float,
    now_ms: int,
    t: Thresholds,
    holders: HolderShare | None = None,
    rug: RugReport | None = None,
) -> list[Flag]:
    """All risk flags for one token. Missing inputs produce ``unknown`` flags, never passes."""
    if pair is None:
        return [Flag("UNKNOWN_PAIR", UNKNOWN, "No DEX pair data was found for this token.")]
    return [
        *_liquidity_flags(pair, position_usd, t),
        *_age_flags(pair, now_ms, t),
        *_txn_flags(pair, t),
        *_volume_flags(pair, t),
        *_holder_flags(holders, t),
        *_rug_flags(rug),
    ]


def is_tradable(flags: Iterable[Flag]) -> bool:
    """True only when no hard or unknown flag fired."""
    return all(f.severity == SOFT for f in flags)


# ---------------------------------------------------------------------------- scoring
def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def volume_acceleration(pair: Pair) -> float | None:
    """h1 volume over the mean hourly volume of h6 and h24 (1.0 = steady); None if unknown."""
    h1 = pair.volume["h1"]
    if h1 is None:
        return None
    ratios = []
    for w, hours in (("h6", 6.0), ("h24", 24.0)):
        v = pair.volume[w]
        if v is not None and v > 0:
            ratios.append(h1 / (v / hours))
    return sum(ratios) / len(ratios) if ratios else None


def momentum_score(pair: Pair | None, boost_amount: float | None) -> float:
    """Momentum/attention: volume acceleration, price change, h1 txns and boost amount."""
    if pair is None:
        return 0.0
    score = 0.0
    accel = volume_acceleration(pair)
    if accel is not None:
        score += 20.0 * _clip(math.log2(max(accel, 1e-9)), -2.0, 3.0)
    if pair.price_change["h1"] is not None:
        score += 0.3 * _clip(pair.price_change["h1"], -50.0, 100.0)
    if pair.price_change["h24"] is not None:
        score += 0.05 * _clip(pair.price_change["h24"], -100.0, 300.0)
    h1 = pair.txns["h1"]
    n = (h1["buys"] or 0) + (h1["sells"] or 0)
    score += 10.0 * math.log10(1 + n)
    if boost_amount:
        score += 5.0 * math.log10(1 + max(boost_amount, 0.0))
    return round(score, 3)


def risk_penalty(flags: Iterable[Flag], t: Thresholds) -> float:
    per = {HARD: t.penalty_hard, UNKNOWN: t.penalty_unknown, SOFT: t.penalty_soft}
    return sum(per.get(f.severity, 0.0) for f in flags)


# ---------------------------------------------------------------------------- scan
@dataclass
class Candidate:
    address: str
    sources: set[str] = field(default_factory=set)
    boost_amount: float | None = None
    pairs: list[Pair] = field(default_factory=list)


def _key(chain: str, addr: str) -> str:
    # EVM addresses are case-insensitive hex; Solana base58 is case-sensitive.
    return addr if chain == "solana" else addr.lower()


class _Scan:
    def __init__(self, chain: str, fetch: Fetch, errors: list[dict[str, str]]) -> None:
        self.chain, self.fetch, self.errors = chain, fetch, errors
        self.cands: dict[str, Candidate] = {}
        self.sources: dict[str, str] = {}

    def _get(self, source: str, url: str) -> Any:
        try:
            data = fetch_json(self.fetch, url)
        except FetchError as exc:
            self.errors.append({"source": source, "url": url, "error": str(exc)})
            self.sources[source] = "error"
            return None
        self.sources.setdefault(source, "ok")
        return data

    def _cand(self, addr: str, source: str) -> Candidate:
        c = self.cands.setdefault(_key(self.chain, addr), Candidate(addr))
        c.sources.add(source)
        return c

    def add_listing(self, source: str, url: str, parser: Callable[[Any], list]) -> None:
        data = self._get(source, url)
        for item in parser(data) if data is not None else []:
            if item["chain_id"] != self.chain:
                continue
            c = self._cand(item["token_address"], source)
            amt = item.get("total_amount") or item.get("amount")
            if amt is not None:
                c.boost_amount = max(c.boost_amount or 0.0, amt)

    def add_search(self, query: str) -> None:
        url = SEARCH_URL + urllib.parse.quote(query)
        data = self._get("search", url)
        for p in parse_pairs(data) if data is not None else []:
            if p.chain_id == self.chain and p.base_address:
                self._cand(p.base_address, f"search:{query}").pairs.append(p)

    def fill_pairs(self, limit: int) -> None:
        todo = [c for c in list(self.cands.values())[:limit] if not c.pairs]
        for i in range(0, len(todo), TOKENS_PER_CALL):
            chunk = todo[i : i + TOKENS_PER_CALL]
            addrs = ",".join(urllib.parse.quote(c.address, safe="") for c in chunk)
            data = self._get("pairs", f"{TOKENS_URL}/{self.chain}/{addrs}")
            for p in parse_pairs(data) if data is not None else []:
                c = self.cands.get(_key(self.chain, p.base_address or ""))
                if c is not None and p.chain_id == self.chain:
                    c.pairs.append(p)


def primary_pair(pairs: list[Pair]) -> Pair | None:
    """The deepest-liquidity pair (pairs with unknown liquidity sort last)."""
    if not pairs:
        return None
    return max(pairs, key=lambda p: (p.liquidity_usd is not None, p.liquidity_usd or 0.0))


def _enrich_solana(
    fetch: Fetch, rpc_url: str, c: Candidate, pair: Pair | None, errors: list
) -> tuple[HolderShare | None, RugReport | None]:
    rug = holders = None
    url = RUGCHECK_URL.format(mint=urllib.parse.quote(c.address, safe=""))
    try:
        rug = parse_rugcheck(fetch_json(fetch, url))
    except FetchError as exc:
        errors.append({"source": "rugcheck", "url": url, "error": str(exc)})
    try:
        holders = fetch_top_holder_share(
            fetch, rpc_url, c.address, pair.pair_address if pair else None
        )
    except FetchError as exc:
        errors.append({"source": "solana_rpc", "url": rpc_url, "error": f"{c.address}: {exc}"})
    return holders, rug


def _row(
    c: Candidate,
    pair: Pair | None,
    flags: list[Flag],
    holders: HolderShare | None,
    rug: RugReport | None,
    now_ms: int,
    t: Thresholds,
) -> dict[str, Any]:
    momentum = momentum_score(pair, c.boost_amount)
    penalty = risk_penalty(flags, t)
    p = pair
    liq = p.liquidity_usd if p else None
    vol24 = p.volume["h24"] if p else None
    return {
        "address": c.address,
        "symbol": p.base_symbol if p else None,
        "name": p.base_name if p else None,
        "sources": sorted(c.sources),
        "tradable": is_tradable(flags),
        "score": round(momentum - penalty, 3),
        "momentum": momentum,
        "penalty": penalty,
        "boost_amount": c.boost_amount,
        "n_pairs": len(c.pairs),
        "pair": asdict(p) if p else None,
        "pair_age_hours": (
            round((now_ms - p.pair_created_at) / HOUR_MS, 2)
            if p and p.pair_created_at is not None
            else None
        ),
        "volume_liquidity": round(vol24 / liq, 4) if liq and vol24 is not None else None,
        "volume_acceleration": volume_acceleration(p) if p else None,
        "holders": asdict(holders) if holders else None,
        "rugcheck": asdict(rug) if rug else None,
        "flags": [asdict(f) for f in flags],
        "hard_fail_reasons": [f.message for f in flags if f.severity != SOFT],
    }


def rank_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Tradable first, then score descending, then symbol/address; sets ``rank`` (1-based)."""
    rows = sorted(
        rows, key=lambda r: (not r["tradable"], -r["score"], r["symbol"] or "", r["address"])
    )
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def scan(
    chain: str = "solana",
    queries: Iterable[str] = (),
    from_profiles: bool = False,
    from_boosts: bool = False,
    position_usd: float = 500.0,
    thresholds: Thresholds | None = None,
    fetch: Fetch | None = None,
    rpc_url: str | None = None,
    now_ms: int | None = None,
    max_tokens: int = 30,
) -> dict[str, Any]:
    """Run one scan and return the watchlist dict. Source failures go to ``errors``."""
    t = thresholds or Thresholds()
    fetch = fetch or default_fetch
    rpc_url = rpc_url or os.environ.get(ENV_SOLANA_RPC) or DEFAULT_SOLANA_RPC
    started = int(time.time() * 1000)
    errors: list[dict[str, str]] = []
    s = _Scan(chain, fetch, errors)
    if from_profiles:
        s.add_listing("profiles", PROFILES_URL, parse_profiles)
    if from_boosts:
        s.add_listing("boosts", BOOSTS_URL, parse_boosts)
    for q in queries:
        s.add_search(q)
    s.fill_pairs(max_tokens)
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    rows = []
    for c in list(s.cands.values())[:max_tokens]:
        pair = primary_pair(c.pairs)
        holders = rug = None
        if chain == "solana":
            holders, rug = _enrich_solana(fetch, rpc_url, c, pair, errors)
        flags = evaluate_flags(pair, position_usd, now, t, holders, rug)
        rows.append(_row(c, pair, flags, holders, rug, now, t))
    rows = rank_rows(rows)
    finished = int(time.time() * 1000)
    return {
        "kind": "dex_watchlist",
        "disclaimer": "Analysis only: no trades, no wallet keys. Not financial advice.",
        "chain": chain,
        "position_usd": position_usd,
        "scan_started_ms": started,
        "scan_finished_ms": finished,
        "evaluated_at_ms": now,
        "generated_at": datetime.fromtimestamp(finished / 1000, UTC).isoformat(),
        "thresholds": asdict(t),
        "sources": s.sources,
        "errors": errors,
        "tradable": [r["address"] for r in rows if r["tradable"]],
        "tokens": rows,
    }


# ---------------------------------------------------------------------------- output
CSV_COLUMNS = (
    "rank", "address", "symbol", "name", "tradable", "score", "momentum", "penalty",
    "price_usd", "liquidity_usd", "fdv", "market_cap",
    "volume_m5", "volume_h1", "volume_h6", "volume_h24",
    "price_change_m5", "price_change_h1", "price_change_h6", "price_change_h24",
    "buys_h1", "sells_h1", "buys_h24", "sells_h24",
    "pair_age_hours", "volume_liquidity", "volume_acceleration",
    "top10_share", "top10_is_upper_bound", "rugcheck_score", "boost_amount", "n_pairs",
    "sources", "flags", "hard_fail_reasons", "dex_id", "pair_address", "pair_url",
    "scan_started_ms", "scan_finished_ms",
)  # fmt: skip


def _csv_row(r: dict[str, Any], wl: dict[str, Any]) -> dict[str, Any]:
    p = _dict(r.get("pair"))
    vol, pc, tx = _dict(p.get("volume")), _dict(p.get("price_change")), _dict(p.get("txns"))
    h, rug = _dict(r.get("holders")), _dict(r.get("rugcheck"))
    out = {k: r.get(k) for k in CSV_COLUMNS if k in r}
    out.update({f"volume_{w}": vol.get(w) for w in WINDOWS})
    out.update({f"price_change_{w}": pc.get(w) for w in WINDOWS})
    for w in ("h1", "h24"):
        out[f"buys_{w}"] = _dict(tx.get(w)).get("buys")
        out[f"sells_{w}"] = _dict(tx.get(w)).get("sells")
    out.update(
        price_usd=p.get("price_usd"),
        liquidity_usd=p.get("liquidity_usd"),
        fdv=p.get("fdv"),
        market_cap=p.get("market_cap"),
        top10_share=h.get("top10_share"),
        top10_is_upper_bound=h.get("is_upper_bound"),
        rugcheck_score=rug.get("score"),
        sources=";".join(r["sources"]),
        flags=";".join(f"{f['name']}({f['severity']})" for f in r["flags"]),
        hard_fail_reasons=" | ".join(r["hard_fail_reasons"]),
        dex_id=p.get("dex_id"),
        pair_address=p.get("pair_address"),
        pair_url=p.get("url"),
        scan_started_ms=wl["scan_started_ms"],
        scan_finished_ms=wl["scan_finished_ms"],
    )
    return out


def write_watchlist(watchlist: dict[str, Any], state_dir: str | Path) -> tuple[Path, Path]:
    """Write ``<state_dir>/dex/watchlist.json`` and ``.csv`` atomically; return both paths."""
    d = Path(state_dir) / "dex"
    d.mkdir(parents=True, exist_ok=True)
    jpath, cpath = d / "watchlist.json", d / "watchlist.csv"
    tmp = jpath.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(watchlist, indent=2, sort_keys=False), encoding="utf-8")
    tmp.replace(jpath)
    tmp = cpath.with_suffix(".csv.tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_COLUMNS)
        w.writeheader()
        for r in watchlist["tokens"]:
            w.writerow(_csv_row(r, watchlist))
    tmp.replace(cpath)
    return jpath, cpath


def load_watchlist(state_dir: str | Path) -> dict[str, Any] | None:
    """Last written watchlist, or ``None`` if absent or unreadable (for the dashboard)."""
    path = Path(state_dir) / "dex" / "watchlist.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _fmt(v: Any, spec: str) -> str:
    return "-" if v is None else format(v, spec)


def format_table(watchlist: dict[str, Any]) -> str:
    head = (
        f"{'#':>3} {'SYMBOL':<10} {'OK':<3} {'SCORE':>8} {'LIQ$':>12} {'VOL24$':>12} "
        f"{'1h%':>7} {'AGEh':>7} {'TOP10':>6}  FLAGS"
    )
    lines = [head, "-" * len(head)]
    for r in watchlist["tokens"]:
        p, h = _dict(r.get("pair")), _dict(r.get("holders"))
        share = h.get("top10_share")
        lines.append(
            f"{r['rank']:>3} {(r['symbol'] or r['address'][:8])[:10]:<10} "
            f"{'Y' if r['tradable'] else 'N':<3} {r['score']:>8.1f} "
            f"{_fmt(p.get('liquidity_usd'), ',.0f'):>12} "
            f"{_fmt(_dict(p.get('volume')).get('h24'), ',.0f'):>12} "
            f"{_fmt(_dict(p.get('price_change')).get('h1'), '.1f'):>7} "
            f"{_fmt(r['pair_age_hours'], '.1f'):>7} "
            f"{_fmt(share * 100 if share is not None else None, '.0f'):>6}  "
            + ",".join(f["name"] for f in r["flags"])
        )
    for e in watchlist["errors"]:
        lines.append(f"[source error] {e['source']}: {e['error']}")
    lines.append(
        f"{len(watchlist['tradable'])}/{len(watchlist['tokens'])} tradable "
        "(analysis only; no trades placed)"
    )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python3 -m research.trendbot.dex_scan",
        description="DEX memecoin scanner: ranked watchlist with risk flags (analysis only).",
    )
    p.add_argument("--chain", default="solana", help="DEXScreener chainId (default solana)")
    p.add_argument("--query", action="append", default=[], help="DEXScreener search text")
    p.add_argument("--from-profiles", action="store_true", help="latest token profiles")
    p.add_argument("--from-boosts", action="store_true", help="latest boosted tokens")
    p.add_argument("--position-usd", type=float, default=500.0, help="intended position size")
    p.add_argument("--max-tokens", type=int, default=30, help="cap on tokens evaluated")
    p.add_argument("--out", default="trendbot_state", help="state dir (writes <out>/dex/)")
    return p


def main(argv: list[str] | None = None, fetch: Fetch | None = None) -> int:
    args = _parser().parse_args(argv)
    if not (args.query or args.from_profiles or args.from_boosts):
        _parser().error("give at least one of --query, --from-profiles, --from-boosts")
    wl = scan(
        chain=args.chain,
        queries=args.query,
        from_profiles=args.from_profiles,
        from_boosts=args.from_boosts,
        position_usd=args.position_usd,
        fetch=fetch,
        max_tokens=args.max_tokens,
    )
    jpath, cpath = write_watchlist(wl, args.out)
    print(format_table(wl))
    print(f"wrote {jpath} and {cpath}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
