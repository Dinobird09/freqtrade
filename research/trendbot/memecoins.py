"""Meme coin desk: a feed of new coins, the safety checklist before you buy, quick swaps.

Every number comes from free public sources (no API key):

- DexScreener: pairs, liquidity, volume, buys/sells, socials, paid boosts and ads, other
  tokens using the same ticker;
- GeckoTerminal: the newest pools (the feed), unique buyers, 30 days of daily volume;
- RugCheck (Solana): liquidity locked or burned, top holders, insiders, the creator;
- GoPlus: token security on Solana and the EVM chains (mintable, honeypot, taxes, LP holders);
- honeypot.is (Ethereum, BNB Chain, Base): a simulated buy and sell;
- the chain itself (Solana RPC): mint and freeze authority, Token-2022 traps, metadata
  mutability (Metaplex), the largest holders; and a Jupiter sell quote;
- Telegram's public page (members) and Reddit search (mentions in the last day).

One rule throughout: **unknown never passes**. A check whose data could not be read is
"unknown", and the verdict says so instead of calling the coin safe.

The six checks before a buy::

    contract   the address matches the project's own channel (you confirm it; tokens that
               copy the ticker are listed)
    mint       mint authority revoked
    freeze     freeze authority revoked
    liquidity  liquidity burned or locked
    holders    a few wallets do not hold most of the supply
    sell       you can sell (a sell route exists; no honeypot or transfer trap)

Trades are wallet tickets (wallets.py): you approve every swap in Phantom or MetaMask.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import re
import struct
import time
import urllib.parse
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from .dex_scan import (
    FetchError,
    Pair,
    _dict,
    _int,
    _list,
    _num,
    _str,
    default_fetch,
    fetch_json,
    parse_pair,
)


Fetch = Callable[..., bytes]
Check = dict[str, Any]

GECKO = "https://api.geckoterminal.com/api/v2"
DEXS = "https://api.dexscreener.com"
RUGCHECK = "https://api.rugcheck.xyz/v1/tokens/{mint}/report"
GOPLUS_SOL = "https://api.gopluslabs.io/api/v1/solana/token_security?contract_addresses={addr}"
GOPLUS_EVM = "https://api.gopluslabs.io/api/v1/token_security/{cid}?contract_addresses={addr}"
HONEYPOT = "https://api.honeypot.is/v2/IsHoneypot?address={addr}&chainID={cid}"
JUPITER_QUOTE = "https://lite-api.jup.ag/swap/v1/quote"
SOLANA_RPC = "https://api.mainnet-beta.solana.com"

# fmt: off
CHAINS: dict[str, dict[str, Any]] = {
    "solana": {"gecko": "solana", "dexs": "solana", "chain_id": None, "wallet": "phantom",
               "explorer": "https://solscan.io/token/", "stable": "USDC"},
    "base": {"gecko": "base", "dexs": "base", "chain_id": 8453, "wallet": "metamask",
             "explorer": "https://basescan.org/token/", "stable": "USDC"},
    "ethereum": {"gecko": "eth", "dexs": "ethereum", "chain_id": 1, "wallet": "metamask",
                 "explorer": "https://etherscan.io/token/", "stable": "USDC"},
    "bsc": {"gecko": "bsc", "dexs": "bsc", "chain_id": 56, "wallet": "metamask",
            "explorer": "https://bscscan.com/token/", "stable": "USDT"},
}
# fmt: on
HONEYPOT_CHAINS = (1, 56, 8453)
SOL_MINT = "So11111111111111111111111111111111111111112"
METADATA_PROGRAM = "metaqbxxUerdq28cj1RbAWkYQm3ybzjb6a8bt518x1s"
BURN_ADDRESSES = {
    "1nc1nerator11111111111111111111111111111111",
    "11111111111111111111111111111111",
    "0x000000000000000000000000000000000000dead",
    "0x0000000000000000000000000000000000000000",
}
TOKEN_2022_TRAPS = {
    "nonTransferable": "it cannot be transferred",
    "permanentDelegate": "a permanent delegate can move anyone's tokens",
    "transferHook": "a program runs on every transfer and can block sells",
    "pausableConfig": "transfers can be paused",
}

PASS, WARN, FAIL, UNKNOWN = "pass", "warn", "fail", "unknown"
DUMP_WINDOW_MIN = 30  # most developer dumps happen in the first 5-30 minutes


# ---------------------------------------------------------------------- base58 + ed25519
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        i = _B58.find(ch)
        if i < 0:
            raise ValueError(f"not base58: {s!r}")
        n = n * 58 + i
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\0" * (len(s) - len(s.lstrip("1"))) + body


def b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _B58[r] + out
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + out


_P = 2**255 - 19
_D = -121665 * pow(121666, _P - 2, _P) % _P


def on_curve(key: bytes) -> bool:
    """Is a 32-byte key a point on ed25519 (Solana's ``is_on_curve``)? Wallets are; program
    accounts (pools, vaults, bonding curves, lockers) are not."""
    if len(key) != 32:
        return False
    y = (int.from_bytes(key, "little") & ((1 << 255) - 1)) % _P
    x2 = (y * y - 1) * pow((_D * y * y + 1) % _P, _P - 2, _P) % _P
    if x2 == 0:
        return True
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * pow(2, (_P - 1) // 4, _P) % _P
    return (x * x - x2) % _P == 0


def find_program_address(seeds: list[bytes], program_id: bytes) -> tuple[bytes, int]:
    for bump in range(255, -1, -1):
        h = hashlib.sha256(
            b"".join(seeds) + bytes([bump]) + program_id + b"ProgramDerivedAddress"
        ).digest()
        if not on_curve(h):
            return h, bump
    raise ValueError("no program address")


def metadata_address(mint: str) -> str:
    prog = b58decode(METADATA_PROGRAM)
    return b58encode(find_program_address([b"metadata", prog, b58decode(mint)], prog)[0])


def parse_metadata(data: bytes) -> dict[str, Any]:
    """Metaplex token metadata: update authority, name, symbol, uri and ``is_mutable``."""
    i = 1 + 32 + 32

    def string() -> str:
        nonlocal i
        (n,) = struct.unpack_from("<I", data, i)
        s = data[i + 4 : i + 4 + n].decode("utf-8", "replace").rstrip("\0")
        i += 4 + n
        return s

    name, symbol, uri = string(), string(), string()
    i += 2  # seller fee basis points
    if data[i] == 1:  # Some(creators)
        (count,) = struct.unpack_from("<I", data, i + 1)
        i += 1 + 4 + 34 * count
    else:
        i += 1
    i += 1  # primary sale happened
    return {
        "update_authority": b58encode(data[1:33]),
        "name": name,
        "symbol": symbol,
        "uri": uri,
        "mutable": bool(data[i]),
    }


# ---------------------------------------------------------------------- sources
class Sources:
    """Every outside read of one request; a failure is recorded in ``status``, never raised."""

    def __init__(self, fetch: Fetch = default_fetch, rpc_url: str | None = None) -> None:
        self.fetch = fetch
        self.rpc_url = rpc_url or os.environ.get("SOLANA_RPC_URL") or SOLANA_RPC
        self.status: dict[str, str] = {}

    def get(self, name: str, url: str, data: Any = None) -> Any:
        try:
            out = fetch_json(self.fetch, url, data)
        except FetchError as exc:
            self.status[name] = f"unavailable: {str(exc)[:120]}"
            return None
        self.status.setdefault(name, "ok")
        return out

    def text(self, name: str, url: str) -> str | None:
        try:
            raw = self.fetch(url)
        except Exception as exc:  # network, HTTP: reported like any other source
            self.status[name] = f"unavailable: {type(exc).__name__}"
            return None
        self.status.setdefault(name, "ok")
        return raw.decode("utf-8", "replace")

    def rpc(self, method: str, params: list[Any]) -> Any:
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        out = self.get("solana rpc", self.rpc_url, body)
        if not isinstance(out, dict):
            return None
        if "error" in out:
            self.status["solana rpc"] = f"error: {str(out.get('error'))[:120]}"
            return None
        return out.get("result")


def _check(
    cid: str, title: str, status: str, detail: str, source: str = "", value: Any = None
) -> Check:
    return {
        "id": cid,
        "title": title,
        "status": status,
        "detail": detail,
        "source": source,
        "value": value,
    }


def _age(minutes: float) -> str:
    if minutes < 90:
        return f"{minutes:.0f} min"
    if minutes < 48 * 60:
        return f"{minutes / 60:.1f} hours"
    return f"{minutes / 1440:.0f} days"


def _iso_ms(s: str | None) -> int | None:
    if not s:
        return None
    try:
        return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)
    except ValueError:
        return None


def _parsed_info(account: Any) -> dict[str, Any]:
    """``account.data.parsed.info`` of a jsonParsed RPC account."""
    return _dict(_dict(_dict(_dict(account).get("data")).get("parsed")).get("info"))


def valid_address(chain: str, address: str) -> bool:
    if chain == "solana":
        try:
            return len(b58decode(address)) == 32
        except ValueError:
            return False
    return bool(re.fullmatch(r"0x[0-9a-fA-F]{40}", address))


# ---------------------------------------------------------------------- the feed
def _pool_row(
    item: Mapping[str, Any], tokens: Mapping[Any, Any], chain: str, now: int
) -> Check | None:
    a, rel = _dict(item.get("attributes")), _dict(item.get("relationships"))
    base_id = _str(_dict(_dict(rel.get("base_token")).get("data")).get("id")) or ""
    if "_" not in base_id:
        return None
    tok = _dict(_dict(tokens.get(base_id)).get("attributes"))
    created = _iso_ms(_str(a.get("pool_created_at")))
    tx, vol = _dict(a.get("transactions")), _dict(a.get("volume_usd"))
    chg, name = _dict(a.get("price_change_percentage")), _str(a.get("name")) or ""
    icon, pool = _str(tok.get("image_url")), _str(a.get("address"))
    return {
        "chain": chain,
        "address": base_id.split("_", 1)[1],
        "pair": pool,
        "dex": _str(_dict(_dict(rel.get("dex")).get("data")).get("id")),
        "symbol": _str(tok.get("symbol")) or name.split(" / ")[0],
        "name": _str(tok.get("name")) or name,
        "icon": icon if icon and icon.startswith("https://") else None,
        "price_usd": _num(a.get("base_token_price_usd")),
        "liquidity_usd": _num(a.get("reserve_in_usd")),
        "market_cap": _num(a.get("market_cap_usd")) or _num(a.get("fdv_usd")),
        "created_ms": created,
        "age_min": None if created is None else max(0.0, (now - created) / 60_000),
        "volume": {w: _num(vol.get(w)) for w in ("m5", "h1", "h6", "h24")},
        "change": {w: _num(chg.get(w)) for w in ("m5", "h1", "h6", "h24")},
        "txns": {
            w: {k: _int(_dict(tx.get(w)).get(k)) for k in ("buys", "sells", "buyers", "sellers")}
            for w in ("m5", "h1", "h24")
        },
        "url": f"https://dexscreener.com/{CHAINS[chain]['dexs']}/{pool}",
    }


def quick_flags(row: Mapping[str, Any]) -> dict[str, Any]:
    """Feed-level warnings from market data alone (the full check reads the contract)."""
    flags, verdict = [], PASS
    liq, mcap, age = row.get("liquidity_usd") or 0, row.get("market_cap") or 0, row.get("age_min")
    h1 = (row.get("txns") or {}).get("h1") or {}
    if age is not None and age < DUMP_WINDOW_MIN:
        flags.append(f"{age:.0f} min old: most dev dumps happen in the first 5-30 minutes")
        verdict = WARN
    if liq < 5_000:
        flags.append(f"only ${liq:,.0f} liquidity")
        verdict = WARN
    if mcap and liq / mcap < 0.03:
        flags.append(f"liquidity is {liq / mcap:.1%} of market cap")
        verdict = WARN
    buys, sells = h1.get("buys") or 0, h1.get("sells") or 0
    if buys >= 20 and sells == 0:
        flags.append(f"{buys} buys and no sells in the last hour: possible honeypot")
        verdict = FAIL
    return {"verdict": verdict, "flags": flags}


def feed(
    chain: str = "solana",
    min_liquidity: float = 1_000.0,
    *,
    src: Sources | None = None,
    now_ms: int | None = None,
    pages: int = 2,
) -> dict[str, Any]:
    """The newest pools on ``chain`` with at least ``min_liquidity`` USD (GeckoTerminal)."""
    if chain not in CHAINS:
        raise ValueError(f"chain must be one of {sorted(CHAINS)}")
    src = src or Sources()
    now = int(time.time() * 1000) if now_ms is None else now_ms
    rows: dict[str, Check] = {}
    net = CHAINS[chain]["gecko"]
    for page in range(1, pages + 1):
        url = f"{GECKO}/networks/{net}/new_pools?include=base_token&page={page}"
        data = src.get("geckoterminal", url)
        if not isinstance(data, dict):
            break
        tokens = {_str(_dict(t).get("id")): t for t in _list(data.get("included"))}
        for item in _list(data.get("data")):
            row = _pool_row(_dict(item), tokens, chain, now)
            if row and (row["liquidity_usd"] or 0) >= min_liquidity and row["address"] not in rows:
                row["quick"] = quick_flags(row)
                rows[row["address"]] = row
    items = sorted(rows.values(), key=lambda r: -(r.get("created_ms") or 0))
    return {
        "chain": chain,
        "min_liquidity": min_liquidity,
        "updated_ms": now,
        "items": items,
        "sources": src.status,
    }


# ---------------------------------------------------------------------- reads for one token
class Token:
    """What the check knows about one token, read once from every source."""

    def __init__(self, src: Sources, chain: str, address: str, now: int) -> None:
        self.src, self.chain, self.address, self.now = src, chain, address, now
        self.spec = CHAINS[chain]
        raw = src.get("dexscreener", f"{DEXS}/tokens/v1/{self.spec['dexs']}/{address}")
        self.raw_pairs = raw if isinstance(raw, list) else []
        pairs = [p for p in (parse_pair(x) for x in self.raw_pairs) if p]
        self.pairs = [p for p in pairs if (p.base_address or "").lower() == address.lower()]
        self.pair: Pair | None = max(self.pairs, key=lambda p: p.liquidity_usd or 0.0, default=None)
        self.symbol = (self.pair.base_symbol if self.pair else None) or ""
        self.socials = self._socials()
        self.rug: dict[str, Any] = {}
        if chain == "solana":
            self.rug = _dict(src.get("rugcheck", RUGCHECK.format(mint=address)))
        self.gp = self._goplus()
        self.hp: dict[str, Any] | None = None
        if self.spec["chain_id"] in HONEYPOT_CHAINS:
            url = HONEYPOT.format(addr=address, cid=self.spec["chain_id"])
            self.hp = _dict(src.get("honeypot.is", url))
        self.mint = self._mint() if chain == "solana" else None

    def _socials(self) -> list[dict[str, str]]:
        info: dict[str, Any] = {}
        for x in self.raw_pairs:
            if self.pair and _str(_dict(x).get("pairAddress")) == self.pair.pair_address:
                info = _dict(_dict(x).get("info"))
        out = [
            {"type": _str(s.get("type")) or "link", "url": _str(s.get("url")) or ""}
            for s in map(_dict, _list(info.get("socials")))
        ]
        out += [
            {"type": "website", "url": _str(w.get("url")) or ""}
            for w in map(_dict, _list(info.get("websites")))
        ]
        return [s for s in out if s["url"].startswith("https://")]

    def _goplus(self) -> dict[str, Any]:
        if self.chain == "solana":
            url = GOPLUS_SOL.format(addr=self.address)
        else:
            url = GOPLUS_EVM.format(cid=self.spec["chain_id"], addr=self.address)
        res = _dict(_dict(self.src.get("goplus", url)).get("result"))
        hit = res.get(self.address) or res.get(self.address.lower())
        if hit is None and len(res) == 1:
            hit = next(iter(res.values()))
        return _dict(hit)

    def _mint(self) -> dict[str, Any] | None:
        res = self.src.rpc("getAccountInfo", [self.address, {"encoding": "jsonParsed"}])
        v = _dict(_dict(res).get("value"))
        if not v:
            return None
        info = _parsed_info(v)
        return {
            "program": _str(v.get("owner")),
            "mint_authority": info.get("mintAuthority"),
            "freeze_authority": info.get("freezeAuthority"),
            "decimals": _int(info.get("decimals")),
            "supply": _num(info.get("supply")),
            "extensions": [_dict(e) for e in _list(info.get("extensions"))],
        }

    @property
    def price(self) -> float | None:
        return self.pair.price_usd if self.pair else None

    @property
    def market_cap(self) -> float | None:
        return (self.pair.market_cap or self.pair.fdv) if self.pair else None


# ---------------------------------------------------------------------- safety & contract
def _status(ok: bool | None, good: str, bad: str, unknown: str) -> tuple[str, str]:
    if ok is None:
        return UNKNOWN, unknown
    return (PASS, good) if ok else (FAIL, bad)


def mint_check(t: Token) -> Check:
    title = "Mint authority revoked"
    if t.chain == "solana":
        auth = t.mint["mint_authority"] if t.mint else None
        st, d = _status(
            None if t.mint is None else auth is None,
            "revoked: nobody can create more tokens",
            f"still set ({auth}): the developer can mint unlimited new tokens",
            "the mint account could not be read",
        )
        return _check("mint", title, st, d, "chain")
    m = _str(t.gp.get("is_mintable"))
    st, d = _status(
        None if m is None else m == "0",
        "the contract cannot mint more tokens",
        "the owner can mint more tokens",
        "GoPlus has no data for this token",
    )
    return _check("mint", title, st, d, "goplus")


def freeze_check(t: Token) -> Check:
    title = "Freeze authority revoked"
    if t.chain == "solana":
        auth = t.mint["freeze_authority"] if t.mint else None
        st, d = _status(
            None if t.mint is None else auth is None,
            "revoked: nobody can freeze your tokens",
            f"still set ({auth}): your account can be frozen, so you may be unable to sell",
            "the mint account could not be read",
        )
        return _check("freeze", title, st, d, "chain")
    keys = ("transfer_pausable", "is_blacklisted", "is_whitelisted")
    bad = [k.replace("is_", "").replace("_", " ") for k in keys if _str(t.gp.get(k)) == "1"]
    known = any(_str(t.gp.get(k)) is not None for k in keys)
    st, d = _status(
        None if not known else not bad,
        "the owner cannot pause transfers or blacklist wallets",
        "the owner has a " + ", ".join(bad) + " switch over holders",
        "GoPlus has no data for this token",
    )
    return _check("freeze", title, st, d, "goplus")


def _metadata(t: Token) -> dict[str, Any] | None:
    for e in (t.mint or {}).get("extensions") or []:  # Token-2022 keeps metadata in the mint
        if e.get("extension") == "tokenMetadata":
            upd = _dict(e.get("state")).get("updateAuthority")
            return {"mutable": bool(upd), "update_authority": upd, "source": "chain"}
    if t.mint is not None:
        res = t.src.rpc("getAccountInfo", [metadata_address(t.address), {"encoding": "base64"}])
        data = _list(_dict(_dict(res).get("value")).get("data"))
        if data:
            try:
                return {**parse_metadata(base64.b64decode(data[0])), "source": "chain"}
            except (ValueError, struct.error, IndexError):
                pass
    meta = _dict(t.rug.get("tokenMeta"))
    if "mutable" in meta:
        return {"mutable": bool(meta["mutable"]), "source": "rugcheck"}
    return None


def metadata_check(t: Token) -> Check | None:
    if t.chain != "solana":
        return None
    title = "Name, symbol and image are fixed"
    meta = _metadata(t)
    if meta is None:
        return _check("metadata", title, UNKNOWN, "the token metadata could not be read")
    if meta["mutable"]:
        who = meta.get("update_authority") or "the update authority"
        d = (
            f"mutable: {who} can still change the name, symbol and image "
            "(a dead coin can be re-dressed as a new meme)"
        )
        return _check("metadata", title, WARN, d, meta["source"])
    d = "immutable: the name, symbol and image can never change"
    return _check("metadata", title, PASS, d, meta["source"])


def liquidity_lock_check(t: Token) -> Check:
    title = "Liquidity burned or locked"
    pct, src = None, "goplus"
    if t.chain == "solana":
        markets = map(_dict, _list(t.rug.get("markets")))
        locked = [
            x
            for x in (_num(_dict(m.get("lp")).get("lpLockedPct")) for m in markets)
            if x is not None
        ]
        pct, src = (max(locked) if locked else None), "rugcheck"
    else:
        lph = [_dict(h) for h in _list(t.gp.get("lp_holders"))]
        if lph:
            pct = sum(
                (_num(h.get("percent")) or 0) * 100
                for h in lph
                if _str(h.get("is_locked")) == "1"
                or (_str(h.get("address")) or "").lower() in BURN_ADDRESSES
            )
    if pct is None:
        d = "no lock or burn data: whoever holds the LP tokens may be able to pull the liquidity"
        return _check("liquidity", title, UNKNOWN, d, src)
    st = PASS if pct >= 90 else WARN if pct >= 50 else FAIL
    d = f"{pct:.0f}% of the liquidity is burned or locked"
    if st != PASS:
        d += ": the rest can be withdrawn (a rug pull)"
    return _check("liquidity", title, st, d, src, pct)


def solana_holders(t: Token) -> dict[str, Any] | None:
    """The largest holders that are people: pools, vaults, lockers and burns not counted."""
    supply = (t.mint or {}).get("supply")
    largest = t.src.rpc("getTokenLargestAccounts", [t.address, {"commitment": "confirmed"}])
    accs = [_dict(a) for a in _list(_dict(largest).get("value"))]
    if not accs or not supply:
        return None
    addrs = [a for a in (_str(x.get("address")) for x in accs) if a]
    res = t.src.rpc("getMultipleAccounts", [addrs, {"encoding": "jsonParsed"}])
    owners = {}
    for addr, acc in zip(addrs, _list(_dict(res).get("value")), strict=False):
        owner = _str(_parsed_info(acc).get("owner"))
        if owner:
            owners[addr] = owner
    people = []
    for a in accs:
        owner = owners.get(_str(a.get("address")) or "")
        if owner in BURN_ADDRESSES:
            continue
        try:  # a program account (pool, vault, curve, locker) is not on the curve
            person = owner is None or on_curve(b58decode(owner))
        except ValueError:
            person = True
        if person:
            pct = (_num(a.get("amount")) or 0) / supply * 100
            people.append({"owner": owner or a.get("address"), "pct": pct})
    people.sort(key=lambda r: -r["pct"])
    return {"top": people[:10], "top5_pct": sum(r["pct"] for r in people[:5])}


def _evm_holders(t: Token) -> list[dict[str, Any]]:
    people = [
        h
        for h in map(_dict, _list(t.gp.get("holders")))
        if _str(h.get("is_contract")) != "1"
        and _str(h.get("is_locked")) != "1"
        and (_str(h.get("address")) or "").lower() not in BURN_ADDRESSES
    ]
    return [{"owner": h.get("address"), "pct": (_num(h.get("percent")) or 0) * 100} for h in people]


def holders_check(t: Token) -> tuple[Check, list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    top5, src = None, "goplus"
    onchain = solana_holders(t) if t.chain == "solana" and t.mint else None
    if onchain:
        top5, rows, src = onchain["top5_pct"], onchain["top"], "chain"
    elif t.chain == "solana" and _list(t.rug.get("topHolders")):
        pcts = sorted(
            (_num(_dict(h).get("pct")) or 0 for h in _list(t.rug["topHolders"])), reverse=True
        )
        top5, src = sum(pcts[:5]), "rugcheck"
    elif t.gp.get("holders"):
        rows = _evm_holders(t)[:10]
        top5 = sum(r["pct"] for r in rows[:5])
    title = "No small group holds most of the supply"
    if top5 is None:
        return _check("holders", title, UNKNOWN, "the largest holders could not be read"), rows
    st = FAIL if top5 >= 40 else WARN if top5 >= 25 else PASS
    d = f"the 5 largest wallets hold {top5:.1f}% of the supply"
    d += (
        ": a small group can dump into every buyer"
        if st == FAIL
        else " (pools, lockers and burns not counted)"
    )
    insiders = [h for h in map(_dict, _list(t.rug.get("topHolders"))) if h.get("insider")]
    if insiders and st == PASS:
        st, d = WARN, d + f"; RugCheck marks {len(insiders)} top holders as insiders"
    return _check("holders", title, st, d, src, top5), rows


def _token2022_traps(t: Token) -> list[str]:
    traps = []
    for e in (t.mint or {}).get("extensions") or []:
        name = e.get("extension")
        if name == "transferFeeConfig":
            fee = _dict(_dict(e.get("state")).get("newerTransferFee"))
            bps = _num(fee.get("transferFeeBasisPoints")) or 0
            if bps >= 500:
                traps.append(f"a {bps / 100:.0f}% transfer fee")
        elif name in TOKEN_2022_TRAPS:
            traps.append(TOKEN_2022_TRAPS[name])
    return traps


def jupiter_sell(t: Token, usd: float) -> dict[str, Any] | None:
    """Ask Jupiter for a real sell route of ``usd`` worth of the token into SOL."""
    dec = (t.mint or {}).get("decimals")
    if dec is None or not t.price:
        return None
    raw = int(usd / t.price * 10**dec)
    if raw <= 0:
        return None
    q = urllib.parse.urlencode(
        {"inputMint": t.address, "outputMint": SOL_MINT, "amount": raw, "slippageBps": 300}
    )
    out = t.src.get("jupiter", f"{JUPITER_QUOTE}?{q}")
    if not isinstance(out, dict):
        return None
    if not out.get("outAmount"):
        return {"route": False, "error": _str(out.get("error")) or "no route"}
    impact = (_num(out.get("priceImpactPct")) or 0.0) * 100
    return {"route": True, "impact_pct": impact, "out_sol": int(out["outAmount"]) / 1e9}


def _solana_sell(t: Token, usd: float, title: str) -> Check:
    traps = _token2022_traps(t)
    if traps:
        return _check("sell", title, FAIL, "Token-2022 trap: " + "; ".join(traps), "chain")
    jq = jupiter_sell(t, usd)
    if jq is None:
        return _check(
            "sell", title, UNKNOWN, "Jupiter could not be asked for a sell quote", "jupiter"
        )
    if not jq["route"]:
        d = f"no sell route on Jupiter ({jq['error']}): you may be unable to sell"
        return _check("sell", title, FAIL, d, "jupiter")
    imp = jq["impact_pct"]
    st = FAIL if imp > 25 else WARN if imp > 5 else PASS
    d = (
        f"selling ${usd:,.0f} works on Jupiter: {imp:.1f}% price impact, "
        f"about {jq['out_sol']:.4f} SOL back"
    )
    keys = ("non_transferable", "transfer_hook", "balance_mutable_authority")
    flags = [k for k in keys if _dict(t.gp.get(k)).get("status") == "1"]
    if flags and st == PASS:
        st, d = WARN, d + "; GoPlus flags " + ", ".join(flags)
    return _check("sell", title, st, d, "jupiter", imp)


def sell_check(t: Token, position_usd: float) -> Check:
    title = "You can sell (no honeypot)"
    if t.chain == "solana":
        return _solana_sell(t, position_usd, title)
    res = _dict((t.hp or {}).get("honeypotResult"))
    sim = _dict((t.hp or {}).get("simulationResult"))
    if (
        res.get("isHoneypot")
        or _str(t.gp.get("is_honeypot")) == "1"
        or _str(t.gp.get("cannot_sell_all")) == "1"
    ):
        why = _str(res.get("honeypotReason")) or "a simulated sell fails"
        return _check("sell", title, FAIL, "HONEYPOT: " + why, "honeypot.is")
    if t.hp and t.hp.get("simulationSuccess"):
        tax = _num(sim.get("sellTax")) or 0
        st = FAIL if tax > 25 else WARN if tax > 8 else PASS
        d = f"a simulated buy and sell went through; sell tax {tax:.1f}%"
        return _check("sell", title, st, d, "honeypot.is", tax)
    if _str(t.gp.get("is_honeypot")) == "0":
        tax = (_num(t.gp.get("sell_tax")) or 0) * 100
        d = f"GoPlus sees no honeypot; sell tax {tax:.1f}%"
        return _check("sell", title, WARN if tax > 8 else PASS, d, "goplus", tax)
    return _check("sell", title, UNKNOWN, "could not test a sell", "honeypot.is")


def dev_check(t: Token) -> tuple[Check | None, dict[str, Any]]:
    creator = _str(t.rug.get("creator")) or _str(t.gp.get("creator_address"))
    others = len(_list(t.rug.get("creatorTokens")))
    dev: dict[str, Any] = {"address": creator, "holds_pct": None, "other_tokens": others}
    if not creator:
        return None, dev
    supply = (t.mint or {}).get("supply")
    if t.chain == "solana" and supply:
        params = [creator, {"mint": t.address}, {"encoding": "jsonParsed"}]
        res = t.src.rpc("getTokenAccountsByOwner", params)
        if res is not None:
            raw = sum(
                _num(_dict(_parsed_info(_dict(a).get("account")).get("tokenAmount")).get("amount"))
                or 0
                for a in _list(_dict(res).get("value"))
            )
            dev["holds_pct"] = raw / supply * 100
    elif t.gp.get("creator_percent") not in (None, ""):
        dev["holds_pct"] = (_num(t.gp.get("creator_percent")) or 0) * 100
    held = dev["holds_pct"]
    st = WARN if (held or 0) > 5 or others >= 3 else PASS if held is not None else UNKNOWN
    if held is not None:
        d = f"the creator holds {held:.1f}% of the supply"
    else:
        d = "the creator's holding is unknown"
    if others:
        d += f", and launched {others} other tokens" + (
            " (a serial launcher)" if others >= 3 else ""
        )
    src = "rugcheck" if t.rug else "goplus"
    return _check("dev", "The creator is not sitting on a big bag", st, d, src, held), dev


def contract_check(t: Token, state_dir: str | Path | None) -> tuple[Check, list[dict[str, Any]]]:
    """Other tokens using the same ticker, and whether you confirmed this address."""
    copies: dict[str, dict[str, Any]] = {}
    if t.symbol:
        url = f"{DEXS}/latest/dex/search?q={urllib.parse.quote(t.symbol)}"
        for p in (parse_pair(x) for x in _list(_dict(t.src.get("dexscreener", url)).get("pairs"))):
            if not p or (p.base_symbol or "").lower() != t.symbol.lower():
                continue
            if (p.base_address or "").lower() != t.address.lower():
                row = {"address": p.base_address, "chain": p.chain_id, "url": p.url}
                copies.setdefault(p.base_address or "", {**row, "liquidity_usd": p.liquidity_usd})
    rows = sorted(copies.values(), key=lambda c: -(c["liquidity_usd"] or 0))
    title = "The address matches the project's channel"
    if is_confirmed(state_dir, t.chain, t.address):
        d = "you confirmed this address against the project's own channel"
        return _check("contract", title, PASS, d, "you"), rows
    d = f"{len(rows)} other tokens use ${t.symbol}: a ticker proves nothing. " if rows else ""
    d += (
        "Compare this address with the one posted on the project's own X or Telegram, then tick it."
    )
    return _check("contract", title, WARN if rows else UNKNOWN, d, "you", len(rows)), rows


# ---------------------------------------------------------------------- liquidity & volume
def daily_volume(t: Token) -> list[dict[str, float]]:
    if not t.pair or not t.pair.pair_address:
        return []
    url = (
        f"{GECKO}/networks/{t.spec['gecko']}/pools/{t.pair.pair_address}"
        "/ohlcv/day?aggregate=1&limit=30"
    )
    attrs = _dict(_dict(_dict(t.src.get("geckoterminal", url)).get("data")).get("attributes"))
    rows = [r for r in _list(attrs.get("ohlcv_list")) if isinstance(r, list) and len(r) >= 6]
    out = [{"t": float(r[0]) * 1000, "c": float(r[4]), "v": float(r[5])} for r in rows]
    return sorted(out, key=lambda r: r["t"])


def volume_checks(
    days: list[Mapping[str, float]],
    v24: float | None,
    liq: float | None,
    buys: int | None,
    buyers: int | None,
) -> list[Check]:
    """Real or wash volume: steady over days, many different buyers, believable turnover."""
    out = []
    vols = [d["v"] for d in days if d["v"] > 0]
    title = "Volume is steady, not one spike"
    if len(vols) >= 3:
        total = sum(vols)
        top, mean = max(vols) / total, total / len(vols)
        cv = math.sqrt(sum((v - mean) ** 2 for v in vols) / len(vols)) / mean
        last7 = sum(vols[-7:]) / len(vols[-7:])
        st = FAIL if top > 0.6 else WARN if top > 0.35 or cv > 1.5 else PASS
        d = (
            f"the biggest day is {top:.0%} of {len(vols)} days' volume; "
            f"7-day average ${last7:,.0f}, {len(vols)}-day average ${mean:,.0f}"
        )
        value = {"top_day": top, "days": len(vols)}
        out.append(_check("volume_consistency", title, st, d, "geckoterminal", value))
    else:
        d = "less than 3 days of trading: there is no history to judge the volume by"
        out.append(_check("volume_consistency", title, UNKNOWN, d, "geckoterminal"))
    title = "Many different buyers, not a few bots"
    if buys and buyers:
        per = buys / buyers
        st = FAIL if per > 8 else WARN if per > 4 else PASS
        d = f"{buys:,} buys by {buyers:,} wallets in 24h: {per:.1f} buys per wallet"
        out.append(_check("buyers", title, st, d, "geckoterminal", per))
    else:
        d = "unique buyer counts are not available"
        out.append(_check("buyers", title, UNKNOWN, d, "geckoterminal"))
    if v24 is not None and liq:
        turn = v24 / liq
        st = FAIL if turn > 60 else WARN if turn > 25 else PASS
        d = f"24h volume is {turn:.1f}x the pool's liquidity"
        if st != PASS:
            d += ": bots wash-trading the same money look like this"
        title = "24h volume is believable for the liquidity"
        out.append(_check("turnover", title, st, d, "dexscreener", turn))
    return out


def slippage(
    liq_usd: float | None, sizes: tuple[float, ...] = (50, 100, 250, 500, 1000)
) -> list[dict[str, float]]:
    """Price impact of selling ``usd`` into a constant-product pool holding ``liq_usd``."""
    if not liq_usd:
        return []
    q = liq_usd / 2
    return [{"usd": s, "pct": s / (q + s) * 100} for s in sizes]


def liquidity_checks(
    t: Token, position_usd: float
) -> tuple[list[Check], list[dict[str, float]], list[dict[str, float]]]:
    p = t.pair
    attr: dict[str, Any] = {}
    if p and p.pair_address:
        url = f"{GECKO}/networks/{t.spec['gecko']}/pools/{p.pair_address}"
        attr = _dict(_dict(_dict(t.src.get("geckoterminal", url)).get("data")).get("attributes"))
    tx24 = _dict(_dict(attr.get("transactions")).get("h24"))
    days = daily_volume(t)
    out = volume_checks(
        days,
        p.volume.get("h24") if p else None,
        p.liquidity_usd if p else None,
        _int(tx24.get("buys")),
        _int(tx24.get("buyers")),
    )
    title = "Liquidity is deep enough for the market cap"
    mcap = t.market_cap
    if p and p.liquidity_usd and mcap:
        ratio = p.liquidity_usd / mcap
        st = FAIL if ratio < 0.02 else WARN if ratio < 0.05 else PASS
        d = f"${p.liquidity_usd:,.0f} liquidity against a ${mcap:,.0f} market cap ({ratio:.1%})"
        if st != PASS:
            d += ": most of the market cap could never be sold"
        out.append(_check("depth", title, st, d, "dexscreener", ratio))
    else:
        out.append(
            _check("depth", title, UNKNOWN, "no liquidity or market cap data", "dexscreener")
        )
    slip = slippage(p.liquidity_usd if p else None)
    at = next((s for s in slip if s["usd"] >= position_usd), slip[-1] if slip else None)
    if at and p:
        st = FAIL if at["pct"] > 15 else WARN if at["pct"] > 5 else PASS
        side = (
            f"{p.liquidity_quote:,.2f} {p.quote_symbol}" if p.liquidity_quote else "the quote side"
        )
        d = f"selling ${at['usd']:,.0f} moves the price about {at['pct']:.1f}%"
        d += f" (the pool holds {side})"
        title = "You can exit without crashing the price"
        out.append(_check("slippage", title, st, d, "dexscreener", at["pct"]))
    return out, slip, days


# ---------------------------------------------------------------------- attention & narrative
def trend_check(p: Pair | None) -> Check | None:
    """Viral durability from the volume curve: growing, steady, peaking or fading."""
    if not p:
        return None
    v1, v6 = p.volume.get("h1"), p.volume.get("h6")
    if not v1 or not v6:
        return None
    accel = v1 / (v6 / 6)
    ch1, ch24 = p.price_change.get("h1") or 0, p.price_change.get("h24") or 0
    pace = f"{accel:.1f}x the 6-hour pace"
    if ch24 > 200 and (accel < 0.7 or ch1 < -15):
        how = f"down {-ch1:.0f}%" if ch1 < 0 else "slowing"
        st, d = FAIL, f"peaking: up {ch24:.0f}% in 24h, but the last hour is {how} ({pace})"
    elif accel >= 1.3 and ch1 >= 0:
        st, d = PASS, f"growing: the last hour traded {pace}"
    elif accel < 0.5:
        st, d = WARN, f"fading: the last hour traded only {pace}"
    else:
        st, d = PASS, f"steady: the last hour traded {pace}"
    return _check("trend", "Attention is growing, not peaking", st, d, "dexscreener", accel)


def telegram_members(src: Sources, url: str) -> int | None:
    m = re.match(r"^https?://(t\.me|telegram\.me)/([A-Za-z0-9_]{4,64})/?$", url.strip())
    if not m:
        return None
    html = src.text("telegram", f"https://t.me/{m.group(2)}")
    hit = re.search(r'tgme_page_extra">\s*([\d\s,.]+)\s*(members|subscribers)', html or "")
    return int(re.sub(r"\D", "", hit.group(1))) if hit else None


def reddit_mentions(src: Sources, symbol: str, now: int) -> int | None:
    if len(symbol) < 2:
        return None
    q = urllib.parse.urlencode({"q": f'"${symbol}"', "sort": "new", "t": "day", "limit": 100})
    data = src.get("reddit", f"https://www.reddit.com/search.json?{q}")
    if data is None:
        return None
    posts = _list(_dict(_dict(data).get("data")).get("children"))
    since = now - 86_400_000
    return sum(
        1
        for p in posts
        if (_num(_dict(_dict(p).get("data")).get("created_utc")) or 0) * 1000 > since
    )


def _paid_check(t: Token) -> Check:
    orders = _list(t.src.get("dexscreener", f"{DEXS}/orders/v1/{t.spec['dexs']}/{t.address}"))
    paid = [
        x
        for x in (
            _str(_dict(o).get("type")) for o in orders if _str(_dict(o).get("status")) == "approved"
        )
        if x
    ]
    boosts = (t.pair.boosts_active or 0) if t.pair else 0
    title = "Attention is organic, not bought"
    if paid or boosts:
        what = paid + ([f"{boosts} active boosts"] if boosts else [])
        d = "paid promotion on DexScreener: " + ", ".join(what)
        return _check("paid", title, WARN, d, "dexscreener")
    return _check("paid", title, PASS, "no paid DexScreener ads or boosts", "dexscreener")


def attention_checks(
    t: Token, state_dir: str | Path | None
) -> tuple[list[Check], list[dict[str, Any]]]:
    out: list[Check] = []
    p = t.pair
    if p and p.pair_created_at:
        age = (t.now - p.pair_created_at) / 60_000
        st = FAIL if age < DUMP_WINDOW_MIN else WARN if age < 24 * 60 else PASS
        d = f"{_age(age)} old"
        if st == FAIL:
            d += ": most dev dumps happen in the first 5-30 minutes"
        out.append(_check("age", "Past the first-hour dump window", st, d, "dexscreener", age))
    trend = trend_check(p)
    if trend:
        out.append(trend)
    kinds = sorted({s["type"] for s in t.socials})
    tg = next((s["url"] for s in t.socials if s["type"] == "telegram"), None)
    members = telegram_members(t.src, tg) if tg else None
    st = PASS if {"twitter", "telegram"} <= set(kinds) else WARN if kinds else FAIL
    d = "links: " + ", ".join(kinds) if kinds else "no X, Telegram or website listed on DexScreener"
    if members is not None:
        d += f"; Telegram has {members:,} members"
    title = "Has a real community (X, Telegram, website)"
    out.append(_check("socials", title, st, d, "dexscreener", members))
    out.append(_paid_check(t))
    n = reddit_mentions(t.src, t.symbol, t.now)
    if n is None:
        st, d = UNKNOWN, "Reddit could not be searched"
    else:
        st, d = (PASS if n >= 5 else WARN), f"{n} Reddit posts mention ${t.symbol} in the last 24h"
    d += "; influencers' posts on X need X's paid API, so read the token's X account yourself"
    out.append(_check("mentions", "People are talking about it (catalysts)", st, d, "reddit", n))
    hist = record_history(state_dir, t)
    growth = growth_check(hist)
    if growth:
        out.append(growth)
    return out, hist


# ---------------------------------------------------------------------- the report
def verdict(six: list[Check], others: list[Check]) -> tuple[str, str]:
    fails = [c["title"] for c in six if c["status"] == FAIL]
    fails += [c["title"] for c in others if c["status"] == FAIL and c["id"] == "rugged"]
    unknown = [c["title"] for c in six if c["status"] == UNKNOWN and c["id"] != "contract"]
    warns = [c["title"] for c in six + others if c["status"] in (WARN, FAIL)]
    if fails:
        return "avoid", "failed: " + "; ".join(fails)
    if unknown:
        return "unknown", "not verified: " + "; ".join(unknown) + " (unknown never passes)"
    if warns:
        return "risky", f"{len(warns)} warnings: " + "; ".join(dict.fromkeys(warns[:4]))
    return "passes", "every check passed. It is still a meme coin: only use money you can lose"


def check(
    chain: str,
    address: str,
    *,
    src: Sources | None = None,
    now_ms: int | None = None,
    position_usd: float = 100.0,
    state_dir: str | Path | None = None,
) -> dict[str, Any]:
    """The full report for one token: the six-point checklist and all three groups."""
    if chain not in CHAINS:
        raise ValueError(f"chain must be one of {sorted(CHAINS)}")
    address = address.strip()
    if not valid_address(chain, address):
        raise ValueError(f"that is not a {chain} token address")
    now = int(time.time() * 1000) if now_ms is None else now_ms
    t = Token(src or Sources(), chain, address, now)
    holders, holder_rows = holders_check(t)
    dev, dev_info = dev_check(t)
    safety = [
        c
        for c in (
            mint_check(t),
            freeze_check(t),
            metadata_check(t),
            liquidity_lock_check(t),
            holders,
            sell_check(t, position_usd),
            dev,
        )
        if c
    ]
    if t.rug.get("rugged"):
        d = "RugCheck marks this token as RUGGED"
        safety.insert(0, _check("rugged", "Not already rugged", FAIL, d, "rugcheck"))
    contract, copies = contract_check(t, state_dir)
    liq, slip, days = liquidity_checks(t, position_usd)
    att, hist = attention_checks(t, state_dir)
    by_id = {c["id"]: c for c in safety}
    six = [contract] + [by_id[k] for k in ("mint", "freeze", "liquidity", "holders", "sell")]
    v, why = verdict(six, safety + liq + att)
    return _report(
        t, v, why, six, safety, liq, att, holder_rows, copies, dev_info, slip, days, hist
    )


def _report(t: Token, v: str, why: str, six: list[Check], safety: list[Check], liq: list[Check],
            att: list[Check], holder_rows: list[dict[str, Any]], copies: list[dict[str, Any]],
            dev: dict[str, Any], slip: list[dict[str, float]], days: list[dict[str, float]],
            hist: list[dict[str, Any]]) -> dict[str, Any]:  # fmt: skip
    p = t.pair
    chart = None
    if p and p.pair_address:
        chart = f"https://dexscreener.com/{t.spec['dexs']}/{p.pair_address}?embed=1&theme=dark&info=0&trades=0"
    return {
        "chain": t.chain,
        "address": t.address,
        "symbol": t.symbol,
        "name": p.base_name if p else None,
        "pair": p.pair_address if p else None,
        "dex": p.dex_id if p else None,
        "url": p.url if p else None,
        "explorer": t.spec["explorer"] + t.address,
        "chart": chart,
        "checked_ms": t.now,
        "verdict": v,
        "verdict_why": why,
        "six": six,
        "groups": [
            {"id": "safety", "title": "Safety & contract", "checks": safety},
            {"id": "liquidity", "title": "Liquidity & volume quality", "checks": liq},
            {"id": "attention", "title": "Attention & narrative", "checks": att},
        ],
        "market": {
            "price_usd": t.price,
            "liquidity_usd": p.liquidity_usd if p else None,
            "market_cap": t.market_cap,
            "volume": p.volume if p else {},
            "change": p.price_change if p else {},
            "txns": p.txns if p else {},
            "daily": days,
            "pairs": len(t.pairs),
            "decimals": (t.mint or {}).get("decimals"),
        },
        "holders": holder_rows,
        "copies": copies[:10],
        "socials": t.socials,
        "dev": dev,
        "slippage": slip,
        "history": hist[-48:],
        "sources": t.src.status,
        "confirmed": six[0]["status"] == PASS,
        "wallet": t.spec["wallet"],
    }


# ---------------------------------------------------------------------- history, confirmations
def memes_dir(state_dir: str | Path) -> Path:
    return Path(state_dir) / "memes"


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def is_confirmed(state_dir: str | Path | None, chain: str, address: str) -> bool:
    if state_dir is None:
        return False
    return f"{chain}:{address}" in _read(memes_dir(state_dir) / "confirmed.json", {})


def confirm_contract(state_dir: str | Path, chain: str, address: str, yes: bool = True) -> None:
    """You compared the address with the one on the project's own channel."""
    from .journal import atomic_write_text

    if not valid_address(chain, address):
        raise ValueError("that is not a token address")
    p = memes_dir(state_dir) / "confirmed.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    data = _read(p, {})
    if yes:
        data[f"{chain}:{address}"] = int(time.time() * 1000)
    else:
        data.pop(f"{chain}:{address}", None)
    atomic_write_text(p, json.dumps(data))


def record_history(state_dir: str | Path | None, t: Token) -> list[dict[str, Any]]:
    """A snapshot of holders, liquidity and price at each check (at most every 5 minutes)."""
    holders = _int(t.rug.get("totalHolders")) or _int(t.gp.get("holder_count"))
    liq = t.pair.liquidity_usd if t.pair else None
    snap = {"ts": t.now, "holders": holders, "liquidity": liq, "price": t.price}
    if state_dir is None:
        return [snap]
    p = memes_dir(state_dir) / "history" / f"{t.chain}_{t.address}.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    except (OSError, ValueError):
        rows = []
    if not rows or t.now - int(rows[-1]["ts"]) >= 5 * 60_000:
        rows.append(snap)
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(snap) + "\n")
    return rows


def growth_check(hist: list[dict[str, Any]]) -> Check | None:
    pts = [h for h in hist if h.get("holders")]
    if len(pts) < 2 or pts[-1]["ts"] - pts[0]["ts"] < 10 * 60_000:
        return None
    a, b = pts[0], pts[-1]
    hours = (b["ts"] - a["ts"]) / 3_600_000
    rate = (b["holders"] - a["holders"]) / hours
    d = f"{a['holders']:,} -> {b['holders']:,} holders in {hours:.1f} h ({rate:+,.0f} an hour)"
    st = PASS if rate > 0 else WARN
    return _check("holder_growth", "Holders keep growing", st, d, "your checks", rate)


# ---------------------------------------------------------------------- trading (wallet tickets)
def wallet_settings(chain: str, slippage_bps: int = 300) -> Any:
    from .wallets import WalletSettings

    return WalletSettings(
        provider=CHAINS[chain]["wallet"],
        chain=chain,
        slippage_bps=slippage_bps,
        approve_seconds=180,
        confirm_seconds=180,
        max_trade_usd=1e12,
    )


def token_decimals(chain: str, address: str, src: Sources | None = None) -> int:
    if chain == "solana":
        res = (src or Sources()).rpc("getAccountInfo", [address, {"encoding": "jsonParsed"}])
        dec = _int(_parsed_info(_dict(res).get("value")).get("decimals"))
        if dec is None:
            raise ValueError("could not read the token's decimals")
        return dec
    from .wallets import DECIMALS, Chain, erc20_call

    call = [{"to": address, "data": erc20_call(DECIMALS)}, "latest"]
    return int(Chain(wallet_settings(chain)).rpc("eth_call", call), 16)


def new_trade(
    state_dir: str | Path,
    chain: str,
    address: str,
    side: str,
    *,
    usd: float | None = None,
    pct: float | None = None,
    symbol: str = "",
    price_usd: float | None = None,
    liquidity_usd: float | None = None,
    slippage_bps: int = 300,
    turbo: bool = True,
    decimals: int | None = None,
    src: Sources | None = None,
) -> dict[str, Any]:
    """Buy (spend ``usd`` of the stable coin) or sell (``pct`` of what you hold): a wallet
    ticket, already routed, for the page to send to the wallet."""
    from . import wallets as w

    if chain not in CHAINS or not valid_address(chain, address):
        raise w.WalletError("unknown chain or token address")
    if side not in ("buy", "sell"):
        raise w.WalletError("side must be buy or sell")
    d = memes_dir(state_dir)
    ws = wallet_settings(chain, slippage_bps)
    conn = w.read_connection(d)
    if conn is None or conn.get("provider") != ws.provider:
        raise w.WalletError(f"connect {w.NAMES[ws.provider]} first (top right of the meme desk)")
    dec = decimals if decimals is not None else token_decimals(chain, address, src)
    sym = (symbol or "TOKEN").upper()[:16]
    tok = {"symbol": sym, "address": address, "decimals": dec, "native": False}
    stable, native = ws.token(CHAINS[chain]["stable"]), ws.token(ws.spec["native"])
    ch, addr = w.Chain(ws), str(conn["address"])
    if ch.balance(addr, native) < ws.reserve / 2:
        need = f"{ws.reserve:g} {native['symbol']}"
        raise w.WalletError(f"keep at least {need} in the wallet for network fees")
    held, cash = ch.balance(addr, tok), ch.balance(addr, stable)
    if side == "buy":
        amount = float(usd or 0)
        if amount <= 0 or cash + 1e-9 < amount:
            raise w.WalletError(f"the wallet has {cash:,.2f} {stable['symbol']}: enter up to that")
        tin, tout, est = stable, tok, amount / price_usd if price_usd else 0.0
    else:
        share = min(max(float(pct or 100), 0.0), 100.0) / 100
        amount = math.floor(held * share * 10**dec) / 10**dec
        if amount <= 0:
            raise w.WalletError(f"the wallet holds no {sym}")
        tin, tout, est = tok, stable, amount * price_usd if price_usd else 0.0
    now = int(time.time() * 1000)
    tid = f"{now}-{os.urandom(3).hex()}"
    ticket = {
        "id": tid,
        "status": "pending",
        "side": side,
        "pair": f"{sym}/{stable['symbol']}",
        "provider": ws.provider,
        "chain": chain,
        "chain_name": ws.spec["name"],
        "chain_id": ws.spec.get("chain_id"),
        "address": addr,
        "token_in": tin,
        "token_out": tout,
        "amount_in": amount,
        "amount_in_raw": str(w.to_raw(amount, tin["decimals"])),
        "est_out": est,
        "slippage_bps": slippage_bps,
        "priority": "turbo" if turbo else None,
        "meme": {"address": address, "price_usd": price_usd, "liquidity_usd": liquidity_usd},
        "pre": {
            tin["symbol"]: cash if side == "buy" else held,
            tout["symbol"]: held if side == "buy" else cash,
        },
        "created_ms": now,
        "expires_ms": now + 180_000,
    }
    w._write(w.wallet_dir(d) / f"{tid}.json", ticket)
    return {"id": tid, "tx": w.prepare(d, tid, ws), "ticket": w.read_ticket(d, tid)}


def settle(state_dir: str | Path, now_ms: int | None = None) -> list[str]:
    """Confirm the meme swaps you sent, on chain, and book them into positions.json."""
    from . import wallets as w

    d = memes_dir(state_dir)
    now = int(time.time() * 1000) if now_ms is None else now_ms
    done = []
    for t in w.open_tickets(d, now):
        if t.get("status") != "sent":
            continue
        ch = w.Chain(wallet_settings(t["chain"], int(t["slippage_bps"])))
        tin, tout = t["token_in"], t["token_out"]
        try:
            st = ch.tx_status(str(t["tx"]))
            got = ch.balance(t["address"], tout) - float(t["pre"][tout["symbol"]])
            spent = float(t["pre"][tin["symbol"]]) - ch.balance(t["address"], tin)
        except w.WalletError:
            continue  # the RPC is down: try again on the next poll
        late = now > int(t.get("updated_ms") or now) + 180_000
        if st == "failed" or (late and got <= 0):
            err = "the transaction did not go through"
            w.update_ticket(d, t["id"], ("sent",), status="failed", error=err)
            continue
        if got <= 0:
            continue  # not confirmed yet, or the balance has not caught up
        paid = spent if spent > 0 else float(t["amount_in"])
        fill = {"qty": got, "usd": paid} if t["side"] == "buy" else {"qty": paid, "usd": got}
        w.update_ticket(d, t["id"], ("sent",), status="filled", fill=fill)
        _book(d, t, fill)
        done.append(t["id"])
    return done


def _book(d: Path, t: Mapping[str, Any], fill: Mapping[str, float]) -> None:
    from .journal import atomic_write_text

    p = d / "positions.json"
    pos = _read(p, {})
    meme = t["meme"]
    row = pos.setdefault(
        f"{t['chain']}:{meme['address']}",
        {
            "chain": t["chain"],
            "address": meme["address"],
            "symbol": t["pair"].split("/")[0],
            "qty": 0.0,
            "cost_usd": 0.0,
            "realized_usd": 0.0,
            "opened_ms": t["created_ms"],
            "entry_liquidity": meme.get("liquidity_usd"),
            "trades": [],
        },
    )
    if t["side"] == "buy":
        row["qty"] += fill["qty"]
        row["cost_usd"] += fill["usd"]
    else:
        share = min(1.0, fill["qty"] / row["qty"]) if row["qty"] else 1.0
        cost = row["cost_usd"] * share
        row["realized_usd"] += fill["usd"] - cost
        row["qty"] = max(0.0, row["qty"] - fill["qty"])
        row["cost_usd"] -= cost
    row["trades"].append(
        {"ts": int(time.time() * 1000), "side": t["side"], **fill, "tx": t.get("tx")}
    )
    atomic_write_text(p, json.dumps(pos, indent=1))


def set_exits(
    state_dir: str | Path, key: str, stop_pct: float | None, target_pct: float | None
) -> None:
    """Alert levels for a position: a loss (stop) and a gain (target), in percent."""
    from .journal import atomic_write_text

    p = memes_dir(state_dir) / "positions.json"
    pos = _read(p, {})
    if key not in pos:
        raise ValueError("no position in that token")
    pos[key]["stop_pct"] = None if stop_pct is None else abs(float(stop_pct))
    pos[key]["target_pct"] = None if target_pct is None else abs(float(target_pct))
    atomic_write_text(p, json.dumps(pos, indent=1))


def live_prices(keys: list[str], src: Sources | None = None) -> dict[str, dict[str, Any]]:
    """Price and liquidity of the tokens behind ``chain:address`` keys (30 per call)."""
    src = src or Sources()
    by_chain: dict[str, list[str]] = {}
    for k in keys:
        c, _, a = k.partition(":")
        if c in CHAINS and a:
            by_chain.setdefault(c, []).append(a)
    out = {}
    for c, addrs in by_chain.items():
        url = f"{DEXS}/tokens/v1/{CHAINS[c]['dexs']}/{','.join(addrs[:30])}"
        pairs = [p for p in (parse_pair(x) for x in _list(src.get("dexscreener", url))) if p]
        for a in addrs:
            mine = [p for p in pairs if (p.base_address or "").lower() == a.lower()]
            best = max(mine, key=lambda p: p.liquidity_usd or 0.0, default=None)
            if best:
                out[f"{c}:{a}"] = {"price_usd": best.price_usd, "liquidity_usd": best.liquidity_usd}
    return out


def _alerts(r: Mapping[str, Any], liq: float | None, pnl_pct: float | None) -> list[str]:
    alerts = []
    entry = r.get("entry_liquidity")
    if liq is not None and entry and liq < entry * 0.6:
        alerts.append(
            f"liquidity fell {1 - liq / entry:.0%} since you bought: it may be being pulled"
        )
    if pnl_pct is not None and r.get("stop_pct") is not None and pnl_pct <= -r["stop_pct"]:
        alerts.append(f"stop hit: {pnl_pct:.0f}%")
    if pnl_pct is not None and r.get("target_pct") and pnl_pct >= r["target_pct"]:
        alerts.append(f"target hit: +{pnl_pct:.0f}%")
    return alerts


def positions(
    state_dir: str | Path, prices: Mapping[str, Mapping[str, Any]] | None = None
) -> dict[str, Any]:
    """Your meme positions at live prices, with alerts: stop, target, liquidity being pulled."""
    from . import wallets as w

    d = memes_dir(state_dir)
    rows = []
    for key, r in _read(d / "positions.json", {}).items():
        if r["qty"] <= 0 and not r["realized_usd"]:
            continue
        q = (prices or {}).get(key) or {}
        px, liq = q.get("price_usd"), q.get("liquidity_usd")
        value = r["qty"] * px if px is not None else None
        pnl_pct = None
        if value is not None and r["cost_usd"]:
            pnl_pct = (value - r["cost_usd"]) / r["cost_usd"] * 100
        rows.append(
            {
                **r,
                "key": key,
                "price_usd": px,
                "liquidity_usd": liq,
                "value_usd": value,
                "pnl_usd": None if value is None else value - r["cost_usd"] + r["realized_usd"],
                "pnl_pct": pnl_pct,
                "alerts": _alerts(r, liq, pnl_pct),
            }
        )
    return {
        "wallet": w.read_connection(d),
        "positions": rows,
        "tickets": w.open_tickets(d),
        "recent": w.recent_tickets(d, 10),
    }


# ---------------------------------------------------------------------- the dashboard's desk
class Desk:
    """The meme desk behind the dashboard's /memes page: cached reads and the buttons."""

    FEED_TTL_S, CHECK_TTL_S, PRICE_TTL_S = 20.0, 60.0, 5.0

    def __init__(self, state_dir: str | Path, src_factory: Callable[[], Sources] = Sources) -> None:
        self.state_dir = Path(state_dir)
        self.src = src_factory
        self._cache: dict[str, tuple[float, Any]] = {}

    def _cached(self, key: str, ttl: float, make: Callable[[], Any], fresh: bool = False) -> Any:
        hit = self._cache.get(key)
        if hit and not fresh and time.monotonic() - hit[0] < ttl:
            return hit[1]
        value = make()
        self._cache[key] = (time.monotonic(), value)
        return value

    def feed(self, q: Mapping[str, str]) -> dict[str, Any]:
        chain = q.get("chain") or "solana"
        floor = float(q.get("min_liq") or 1000)
        return self._cached(
            f"feed:{chain}:{floor}", self.FEED_TTL_S, lambda: feed(chain, floor, src=self.src())
        )

    def check(self, q: Mapping[str, str]) -> dict[str, Any]:
        chain, address = q.get("chain") or "solana", (q.get("address") or "").strip()
        usd = float(q.get("usd") or 100)
        return self._cached(
            f"check:{chain}:{address}:{usd}",
            self.CHECK_TTL_S,
            lambda: check(
                chain, address, src=self.src(), position_usd=usd, state_dir=self.state_dir
            ),
            fresh=q.get("fresh") == "1",
        )

    def positions(self) -> dict[str, Any]:
        settle(self.state_dir)
        keys = list(_read(memes_dir(self.state_dir) / "positions.json", {}))
        px = self._cached(
            "prices:" + ",".join(sorted(keys)),
            self.PRICE_TTL_S,
            lambda: live_prices(keys, self.src()) if keys else {},
        )
        return positions(self.state_dir, px)

    def act(self, req: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
        from . import wallets as w

        a, d = str(req.get("action")), memes_dir(self.state_dir)
        try:
            if a == "meme_connect":
                conn = w.connect(
                    d, str(req.get("provider")), str(req.get("address")), req.get("chain_id")
                )
                short = f"{conn['address'][:6]}…{conn['address'][-4:]}"
                return 200, {
                    "ok": True,
                    "message": f"{w.NAMES[conn['provider']]} connected: {short}",
                }
            if a == "meme_disconnect":
                w.disconnect(d)
                return 200, {"ok": True, "message": "wallet disconnected from the meme desk"}
            if a == "meme_confirm":
                yes = bool(req.get("yes", True))
                confirm_contract(
                    self.state_dir, str(req.get("chain")), str(req.get("address")), yes
                )
                self._cache = {k: v for k, v in self._cache.items() if not k.startswith("check:")}
                msg = "marked as checked against the project's channel" if yes else "unmarked"
                return 200, {"ok": True, "message": msg}
            if a in ("meme_buy", "meme_sell"):
                out = new_trade(
                    self.state_dir,
                    str(req.get("chain")),
                    str(req.get("address")),
                    "buy" if a == "meme_buy" else "sell",
                    usd=_num(req.get("usd")),
                    pct=_num(req.get("pct")),
                    symbol=str(req.get("symbol") or ""),
                    price_usd=_num(req.get("price_usd")),
                    liquidity_usd=_num(req.get("liquidity_usd")),
                    slippage_bps=int(_num(req.get("slippage_bps")) or 300),
                    turbo=bool(req.get("turbo", True)),
                    src=self.src(),
                )
                return 200, {"ok": True, "message": "approve it in your wallet", **out}
            if a == "meme_sent":
                w.mark_sent(d, str(req.get("id")), str(req.get("tx", "")))
                return 200, {"ok": True, "message": "sent: confirming it on chain"}
            if a == "meme_reject":
                w.reject(d, str(req.get("id")), str(req.get("reason", "")))
                return 200, {"ok": True, "message": "trade cancelled"}
            if a == "meme_exits":
                stop, target = _num(req.get("stop_pct")), _num(req.get("target_pct"))
                set_exits(self.state_dir, str(req.get("key")), stop, target)
                return 200, {"ok": True, "message": "alerts saved"}
        except (w.WalletError, ValueError) as exc:
            return 400, {"ok": False, "message": str(exc)}
        return 400, {"ok": False, "message": f"unknown action {a!r}"}
