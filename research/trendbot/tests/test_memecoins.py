"""Meme coin desk: the feed, the six-point checklist, unknown-never-passes, and wallet trades."""

import base64
import json
import os
import struct
import time
import urllib.parse

import pytest

from research.trendbot import memecoins as m
from research.trendbot import wallets as w


NOW = 1_800_000_000_000
MINT = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
POOL = "8sLbNZoA1cfnvMJLPfp98ZLAnFSYCFApfJKMbiXNLwxj"
EVM = "0x" + "12" * 20


def person(seed: int) -> str:
    """An ed25519 public key (a wallet): on the curve."""
    i = seed
    while True:
        key = (i.to_bytes(4, "little") * 8)[:32]
        if m.on_curve(key):
            return m.b58encode(key)
        i += 1000


def program_owned(seed: int) -> str:
    """A program-derived address (a pool vault, a bonding curve): off the curve."""
    return m.b58encode(m.find_program_address([b"pool", bytes([seed])], b"\x07" * 32)[0])


class Net:
    """URL -> reply for every source; anything not mapped is 'down'."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, url, headers=None, data=None):
        self.calls.append(url)
        key = data["method"] if isinstance(data, dict) and "method" in data else url
        for pat, reply in self.routes.items():
            if (pat == key) or (not isinstance(data, dict) and pat in url):
                out = reply(data) if callable(reply) else reply
                if isinstance(out, Exception):
                    raise out
                return json.dumps(out).encode() if not isinstance(out, bytes) else out
        raise ConnectionError(f"down: {url}")


def rpc(result):
    return {"jsonrpc": "2.0", "id": 1, "result": result}


def metadata_bytes(mutable: bool) -> bytes:
    def s(x):
        return struct.pack("<I", len(x)) + x

    return (
        b"\x04"
        + b"\x01" * 32
        + m.b58decode(MINT)
        + s(b"PEPE" + b"\0" * 28)
        + s(b"PEPE\0\0")
        + s(b"https://x/y.json")
        + struct.pack("<H", 0)
        + b"\x01"
        + struct.pack("<I", 1)
        + b"\x02" * 34
        + b"\x00"
        + (b"\x01" if mutable else b"\x00")
    )


def dex_pair(
    liq=250_000.0, mcap=2_000_000.0, created=NOW - 20 * 86_400_000, boosts=0, socials=True
):
    info = {
        "socials": [
            {"type": "twitter", "url": "https://x.com/pepe"},
            {"type": "telegram", "url": "https://t.me/pepecoin"},
        ]
        if socials
        else [],
        "websites": [{"url": "https://pepe.example"}] if socials else [],
    }
    return {
        "chainId": "solana",
        "dexId": "raydium",
        "url": f"https://dexscreener.com/solana/{POOL}",
        "pairAddress": POOL,
        "baseToken": {"address": MINT, "name": "Pepe", "symbol": "PEPE"},
        "quoteToken": {"address": m.SOL_MINT, "symbol": "SOL"},
        "priceUsd": "0.001",
        "txns": {"h1": {"buys": 300, "sells": 280}, "h24": {"buys": 5000, "sells": 4800}},
        "volume": {"h1": 40_000, "h6": 200_000, "h24": 900_000},
        "priceChange": {"h1": 3, "h24": 20},
        "liquidity": {"usd": liq, "base": 1e8, "quote": 600.0},
        "fdv": mcap,
        "marketCap": mcap,
        "pairCreatedAt": created,
        "boosts": {"active": boosts},
        "info": info,
    }


def safe_routes(**over):
    holders = [(program_owned(1), 30.0)] + [(person(i), 4.0) for i in range(2, 12)]
    supply = 1_000_000_000_000
    largest = [
        {"address": f"acct{i}", "amount": str(int(pct / 100 * supply))}
        for i, (_, pct) in enumerate(holders)
    ]
    owners = [{"data": {"parsed": {"info": {"owner": o}}}} for o, _ in holders]
    routes = {
        f"/tokens/v1/solana/{MINT}": [dex_pair()],
        "rugcheck": {
            "markets": [{"lp": {"lpLockedPct": 100}}],
            "creator": person(99),
            "totalHolders": 4200,
            "topHolders": [],
            "tokenMeta": {"mutable": False},
        },
        "gopluslabs": {"code": 1, "result": {MINT: {"holder_count": "4200"}}},
        "getAccountInfo": lambda d: rpc(
            {
                "value": {
                    "owner": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA",
                    "data": {
                        "parsed": {
                            "info": {
                                "mintAuthority": None,
                                "freezeAuthority": None,
                                "decimals": 6,
                                "supply": str(supply),
                            }
                        }
                    },
                }
            }
            if d["params"][1]["encoding"] == "jsonParsed"
            else {"value": {"data": [base64.b64encode(metadata_bytes(False)).decode(), "base64"]}}
        ),
        "getTokenLargestAccounts": rpc({"value": largest}),
        "getMultipleAccounts": rpc({"value": owners}),
        "getTokenAccountsByOwner": rpc(
            {
                "value": [
                    {
                        "account": {
                            "data": {
                                "parsed": {"info": {"tokenAmount": {"amount": str(supply // 100)}}}
                            }
                        }
                    }
                ]
            }
        ),
        "lite-api.jup.ag": {"outAmount": "500000000", "priceImpactPct": "0.004"},
        "/latest/dex/search": {"pairs": [dex_pair()]},
        f"/pools/{POOL}/ohlcv/day": {
            "data": {
                "attributes": {
                    "ohlcv_list": [
                        [NOW // 1000 - i * 86400, 1, 1, 1, 1, 800_000 + 10_000 * (i % 3)]
                        for i in range(20)
                    ]
                }
            }
        },
        f"/pools/{POOL}": {
            "data": {"attributes": {"transactions": {"h24": {"buys": 5000, "buyers": 2100}}}}
        },
        "/orders/v1/": [],
        "t.me/pepecoin": b'<div class="tgme_page_extra">12 345 members, 800 online</div>',
        "reddit.com": {"data": {"children": [{"data": {"created_utc": NOW / 1000 - 600}}] * 9}},
    }
    routes.update(over)
    return routes


def report(routes, **kw):
    return m.check(
        "solana", MINT, src=m.Sources(Net(routes), rpc_url="https://rpc.example"), now_ms=NOW, **kw
    )


def by_id(rep, cid):
    return next(c for g in rep["groups"] for c in g["checks"] if c["id"] == cid)


def test_ed25519_and_program_addresses():
    p = 2**255 - 19
    base_y = 4 * pow(5, p - 2, p) % p  # the ed25519 base point: on the curve
    assert m.on_curve(base_y.to_bytes(32, "little"))
    pda, bump = m.find_program_address([b"metadata"], m.b58decode(m.METADATA_PROGRAM))
    assert not m.on_curve(pda) and 0 <= bump <= 255
    assert m.b58decode(m.b58encode(b"\0\0abc")) == b"\0\0abc"
    assert len(m.b58decode(m.metadata_address(MINT))) == 32
    meta = m.parse_metadata(metadata_bytes(True))
    assert meta["name"] == "PEPE" and meta["uri"] == "https://x/y.json" and meta["mutable"] is True


def test_a_clean_coin_passes_every_hard_check():
    rep = report(safe_routes())
    six = {c["id"]: c["status"] for c in rep["six"]}
    assert six == {
        "contract": "unknown",
        "mint": "pass",
        "freeze": "pass",
        "liquidity": "pass",
        "holders": "pass",
        "sell": "pass",
    }
    assert (
        by_id(rep, "metadata")["status"] == "pass"
        and "immutable" in by_id(rep, "metadata")["detail"]
    )
    h = by_id(rep, "holders")
    assert "20.0%" in h["detail"]  # 5 x 4%: the 30% pool vault (a program account) is not a person
    assert (
        rep["verdict"] in ("passes", "risky") and "unknown never passes" not in rep["verdict_why"]
    )
    assert by_id(rep, "socials")["value"] == 12345 and by_id(rep, "mentions")["value"] == 9
    assert by_id(rep, "volume_consistency")["status"] == "pass"
    assert by_id(rep, "dev")["detail"].startswith("the creator holds 1.0%")
    assert (
        rep["chart"].startswith("https://dexscreener.com/solana/")
        and rep["sources"]["rugcheck"] == "ok"
    )


def test_a_rug_fails_the_checklist():
    supply = 1_000_000_000_000
    whales = [{"address": f"w{i}", "amount": str(supply // 8)} for i in range(5)]
    routes = safe_routes(
        **{
            "getAccountInfo": rpc(
                {
                    "value": {
                        "owner": "x",
                        "data": {
                            "parsed": {
                                "info": {
                                    "mintAuthority": person(5),
                                    "freezeAuthority": person(6),
                                    "decimals": 6,
                                    "supply": str(supply),
                                }
                            }
                        },
                    }
                }
            ),
            "getTokenLargestAccounts": rpc({"value": whales}),
            "getMultipleAccounts": rpc(
                {
                    "value": [
                        {"data": {"parsed": {"info": {"owner": person(i + 10)}}}} for i in range(5)
                    ]
                }
            ),
            "rugcheck": {"markets": [{"lp": {"lpLockedPct": 0}}], "tokenMeta": {"mutable": True}},
            "lite-api.jup.ag": {"error": "COULD_NOT_FIND_ANY_ROUTE"},
            f"/tokens/v1/solana/{MINT}": [
                dex_pair(created=NOW - 8 * 60_000, socials=False, boosts=500)
            ],
        }
    )
    rep = report(routes)
    six = {c["id"]: c["status"] for c in rep["six"]}
    assert (
        six["mint"] == six["freeze"] == six["liquidity"] == six["holders"] == six["sell"] == "fail"
    )
    assert rep["verdict"] == "avoid" and "Mint authority revoked" in rep["verdict_why"]
    assert "62.5%" in by_id(rep, "holders")["detail"]
    assert by_id(rep, "age")["status"] == "fail" and "5-30 minutes" in by_id(rep, "age")["detail"]
    assert by_id(rep, "paid")["status"] == "warn" and by_id(rep, "socials")["status"] == "fail"
    assert (
        by_id(rep, "metadata")["status"] == "warn"
        and "re-dressed" in by_id(rep, "metadata")["detail"]
    )


def test_unknown_never_passes_when_the_sources_are_down():
    rep = report({})
    assert rep["verdict"] == "unknown" and "unknown never passes" in rep["verdict_why"]
    assert all(c["status"] == "unknown" for c in rep["six"])
    assert rep["sources"]["dexscreener"].startswith("unavailable")


def test_ticker_copies_and_your_confirmation(tmp_path):
    other = dict(
        dex_pair(), baseToken={"address": person(77), "symbol": "PEPE"}, pairAddress="other"
    )
    routes = safe_routes(**{"/latest/dex/search": {"pairs": [dex_pair(), other]}})
    rep = report(routes, state_dir=tmp_path)
    assert (
        rep["six"][0]["status"] == "warn" and "1 other tokens use $PEPE" in rep["six"][0]["detail"]
    )
    m.confirm_contract(tmp_path, "solana", MINT)
    rep = report(routes, state_dir=tmp_path)
    assert rep["six"][0]["status"] == "pass" and rep["confirmed"]
    assert (tmp_path / "memes" / "history" / f"solana_{MINT}.jsonl").exists()


def test_evm_honeypot_and_goplus():
    net = Net(
        {
            f"/tokens/v1/base/{EVM}": [
                dict(dex_pair(), chainId="base", baseToken={"address": EVM, "symbol": "SCAM"})
            ],
            "gopluslabs": {
                "code": 1,
                "result": {
                    EVM: {
                        "is_mintable": "1",
                        "transfer_pausable": "0",
                        "is_blacklisted": "1",
                        "lp_holders": [
                            {
                                "address": "0x000000000000000000000000000000000000dEaD",
                                "percent": "0.95",
                            }
                        ],
                        "holders": [{"address": "0xa", "percent": "0.5"}],
                    }
                },
            },
            "honeypot.is": {
                "honeypotResult": {"isHoneypot": True, "honeypotReason": "sell reverts"},
                "simulationSuccess": False,
            },
        }
    )
    rep = m.check("base", EVM, src=m.Sources(net), now_ms=NOW)
    six = {c["id"]: c for c in rep["six"]}
    assert six["sell"]["status"] == "fail" and "sell reverts" in six["sell"]["detail"]
    assert six["mint"]["status"] == "fail" and six["freeze"]["status"] == "fail"
    assert six["liquidity"]["status"] == "pass" and six["holders"]["status"] == "fail"
    assert rep["verdict"] == "avoid"
    with pytest.raises(ValueError, match="not a base token address"):
        m.check("base", "0x123", src=m.Sources(net))


def test_volume_quality_and_slippage():
    spike = [{"v": 10.0}] * 10 + [{"v": 1000.0}]
    assert m.volume_checks(spike, None, None, None, None)[0]["status"] == "fail"
    steady = [{"v": 100.0 + i} for i in range(10)]
    vq = m.volume_checks(steady, 50_000, 1_000, 900, 100)
    assert [c["status"] for c in vq] == [
        "pass",
        "fail",
        "warn",
    ]  # steady; 9 buys/wallet; 50x turnover
    assert m.slippage(10_000)[1] == {"usd": 100, "pct": pytest.approx(100 / 5100 * 100)}


def test_the_feed_lists_new_pools_above_the_liquidity_floor():
    created = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime((NOW - 10 * 60_000) / 1000))

    def pool(addr, liq, buys=10, sells=5):
        return {
            "attributes": {
                "address": f"pool{addr}",
                "name": f"{addr} / SOL",
                "reserve_in_usd": str(liq),
                "base_token_price_usd": "0.0001",
                "pool_created_at": created,
                "market_cap_usd": "90000",
                "transactions": {"h1": {"buys": buys, "sells": sells}},
                "volume_usd": {"h1": "500"},
            },
            "relationships": {
                "base_token": {"data": {"id": f"solana_{addr}"}},
                "dex": {"data": {"id": "pumpswap"}},
            },
        }

    page = {
        "data": [pool("AAA", 50_000), pool("BBB", 400), pool("CCC", 20_000, buys=40, sells=0)],
        "included": [
            {"id": "solana_AAA", "attributes": {"symbol": "AAA", "image_url": "https://img/a.png"}}
        ],
    }
    out = m.feed("solana", 1_000, src=m.Sources(Net({"new_pools": page})), now_ms=NOW, pages=1)
    addrs = [r["address"] for r in out["items"]]
    assert addrs == ["AAA", "CCC"] and out["items"][0]["icon"] == "https://img/a.png"
    assert (
        out["items"][0]["quick"]["verdict"] == "warn"
        and "10 min old" in out["items"][0]["quick"]["flags"][0]
    )
    assert out["items"][1]["quick"]["verdict"] == "fail"  # 40 buys, 0 sells: honeypot pattern


class Wallet:
    """Phantom + the chain + Jupiter for trading tests."""

    def __init__(self, me):
        self.me = me
        self.raw = {"USDC": 500 * 10**6, "SOL": 10**9, MINT: 0}
        self.sigs = {}

    def bal(self, mint):
        key = {"EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC"}.get(mint, mint)
        return self.raw.get(key, 0)

    def __call__(self, url, payload, headers):
        if isinstance(payload, dict) and payload.get("jsonrpc"):
            meth, prm = payload["method"], payload["params"]
            if meth == "getBalance":
                return {"result": {"value": self.raw["SOL"]}}
            if meth == "getTokenAccountsByOwner":
                info = {"tokenAmount": {"amount": str(self.bal(prm[1]["mint"]))}}
                return {"result": {"value": [{"account": {"data": {"parsed": {"info": info}}}}]}}
            if meth == "getSignatureStatuses":
                ok = prm[0][0] in self.sigs
                return {
                    "result": {
                        "value": [{"err": None, "confirmationStatus": "confirmed"} if ok else None]
                    }
                }
            if meth == "getTokenSupply":
                return {"result": {"value": {"decimals": 6}}}
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        if "/swap/v1/quote" in url:
            return {
                "inputMint": q["inputMint"],
                "outputMint": q["outputMint"],
                "inAmount": q["amount"],
                "outAmount": str(
                    int(q["amount"])
                    * (1000 if q["outputMint"] == MINT else 1)
                    // (1 if q["outputMint"] == MINT else 1000)
                ),
            }
        if url.endswith("/swap/v1/swap"):
            self.last_swap = payload
            return {
                "swapTransaction": base64.b64encode(
                    json.dumps(payload["quoteResponse"]).encode()
                ).decode()
            }
        raise AssertionError(url)

    def sign(self, tx_b64):
        q = json.loads(base64.b64decode(tx_b64))
        tin = {"EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC"}.get(
            q["inputMint"], q["inputMint"]
        )
        tout = {"EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC"}.get(
            q["outputMint"], q["outputMint"]
        )
        self.raw[tin] -= int(q["inAmount"])
        self.raw[tout] += int(q["outAmount"])
        sig = "sig" + os.urandom(8).hex()
        self.sigs[sig] = True
        return sig


def test_buy_and_sell_a_meme_through_phantom(tmp_path, monkeypatch):
    me = person(3)
    wal = Wallet(me)
    monkeypatch.setattr(w, "http_json", wal)
    with pytest.raises(w.WalletError, match="connect Phantom first"):
        m.new_trade(tmp_path, "solana", MINT, "buy", usd=50, decimals=6)
    w.connect(m.memes_dir(tmp_path), "phantom", me)
    out = m.new_trade(
        tmp_path,
        "solana",
        MINT,
        "buy",
        usd=50,
        symbol="pepe",
        price_usd=0.001,
        liquidity_usd=200_000,
        decimals=6,
    )
    assert out["tx"]["kind"] == "solana" and out["ticket"]["slippage_bps"] == 300
    assert "prioritizationFeeLamports" in wal.last_swap  # turbo: a priority fee to land fast
    w.mark_sent(m.memes_dir(tmp_path), out["id"], wal.sign(out["tx"]["tx"]))
    assert m.settle(tmp_path) == [out["id"]]
    pos = m.positions(tmp_path, {f"solana:{MINT}": {"price_usd": 0.0015, "liquidity_usd": 50_000}})
    row = pos["positions"][0]
    assert row["qty"] == pytest.approx(50_000) and row["cost_usd"] == pytest.approx(50)
    assert row["pnl_pct"] == pytest.approx(50) and "liquidity fell 75%" in row["alerts"][0]
    m.set_exits(tmp_path, f"solana:{MINT}", 30, 40)
    row = m.positions(tmp_path, {f"solana:{MINT}": {"price_usd": 0.0015}})["positions"][0]
    assert any("target hit" in a for a in row["alerts"])
    out = m.new_trade(
        tmp_path, "solana", MINT, "sell", pct=50, symbol="pepe", price_usd=0.0015, decimals=6
    )
    w.mark_sent(m.memes_dir(tmp_path), out["id"], wal.sign(out["tx"]["tx"]))
    m.settle(tmp_path)
    row = m.positions(tmp_path, {f"solana:{MINT}": {"price_usd": 0.0015}})["positions"][0]
    assert row["qty"] == pytest.approx(25_000) and row["realized_usd"] == pytest.approx(25 - 25)
    with pytest.raises(w.WalletError, match="enter up to that"):
        m.new_trade(tmp_path, "solana", MINT, "buy", usd=10_000, decimals=6)
