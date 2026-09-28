"""Offline tests for dex_scan: fixtures modelled on DEXScreener / RugCheck / Solana RPC shapes."""

from __future__ import annotations

import csv
import json
import urllib.error
from pathlib import Path

import pytest

from research.trendbot import dex_scan as ds


FIX = Path(__file__).parent / "fixtures" / "dex"
NOW = 1_760_000_000_000
H = ds.HOUR_MS
RPC = "https://rpc.test.invalid"


def load(name: str):
    return json.loads((FIX / name).read_text())


def raw(name: str) -> bytes:
    return (FIX / name).read_bytes()


def generic_largest(n: int = 10, each: int = 10) -> dict:
    """``n`` holders with ``each`` units each of a 1000-unit supply (n*each/1000 share)."""
    vals = [{"address": f"H{i}", "amount": str(each), "decimals": 0} for i in range(n)]
    return {"jsonrpc": "2.0", "id": 1, "result": {"context": {"slot": 1}, "value": vals}}


GENERIC_SUPPLY = {"jsonrpc": "2.0", "id": 1, "result": {"value": {"amount": "1000"}}}


class FakeFetch:
    """Routes URLs (and RPC methods) to fixture bytes; records every call."""

    def __init__(self, fail: tuple[str, ...] = ()) -> None:
        self.calls: list[tuple[str, object]] = []
        self.fail = fail

    def _rpc(self, data: dict) -> bytes:
        method, mint = data["method"], data["params"][0]
        if method == "getMultipleAccounts":
            if "PoolVaultGood" in mint:
                return raw("rpc_multiple_good.json")
            return json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"value": []}}).encode()
        if mint == "GoodMint1111":
            name = {"getTokenLargestAccounts": "rpc_largest_good.json"}.get(method)
            return raw(name or "rpc_supply.json")
        if method == "getTokenLargestAccounts":
            return json.dumps(generic_largest()).encode()
        return json.dumps(GENERIC_SUPPLY).encode()

    def __call__(self, url, headers=None, data=None) -> bytes:
        self.calls.append((url, data))
        for f in self.fail:
            if f in url:
                raise urllib.error.URLError(f"simulated outage {f}")
        if url == RPC:
            return self._rpc(data)
        if url == ds.PROFILES_URL:
            return raw("profiles.json")
        if url == ds.BOOSTS_URL:
            return raw("boosts.json")
        if url.startswith(ds.SEARCH_URL):
            return raw("search_meme.json")
        if url.startswith(f"{ds.TOKENS_URL}/solana/"):
            return raw("tokens_solana.json")
        if "rugcheck" in url:
            if "/RugMint1111/" in url:
                return raw("rugcheck_danger.json")
            if any(m in url for m in ("GoodMint1111", "HotMint1111", "MidMint1111")):
                return raw("rugcheck_clean.json")
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
        raise AssertionError(f"unexpected url {url}")


def make_pair(**kw) -> ds.Pair:
    base = {
        "chainId": "solana",
        "pairAddress": "P",
        "baseToken": {"address": "M", "symbol": "M"},
        "txns": {w: {"buys": 100, "sells": 90} for w in ds.WINDOWS},
        "volume": {"m5": 100, "h1": 1000, "h6": 6000, "h24": 100_000},
        "priceChange": {"m5": 0, "h1": 0, "h6": 0, "h24": 0},
        "liquidity": {"usd": 100_000},
        "pairCreatedAt": NOW - 48 * H,
    }
    base.update(kw)
    p = ds.parse_pair(base)
    assert p is not None
    return p


def names(flags) -> set[str]:
    return {f.name for f in flags}


T = ds.Thresholds()


# ---------------------------------------------------------------------------- parsing
def test_parse_profiles_filters_invalid():
    out = ds.parse_profiles(load("profiles.json"))
    assert [p["token_address"] for p in out] == [
        "GoodMint1111",
        "RugMint1111",
        "0xAbC0000000000000000000000000000000000001",
    ]
    assert out[0]["description"] == "A good token"
    assert out[2]["url"] is not None and out[1]["url"].endswith("rugmint1111")
    assert ds.parse_profiles({"not": "a list"}) == []


def test_parse_boosts_amounts_and_bad_values():
    out = {b["token_address"]: b for b in ds.parse_boosts(load("boosts.json"))}
    assert out["HotMint1111"]["amount"] == 100 and out["HotMint1111"]["total_amount"] == 500
    assert out["MidMint1111"]["amount"] is None and out["MidMint1111"]["total_amount"] is None
    assert ds.parse_boosts(None) == []


def test_parse_pairs_full_and_missing_fields():
    pairs = ds.parse_pairs(load("tokens_solana.json"))
    good = pairs[0]
    assert good.base_symbol == "GOOD" and good.pair_address == "GoodPair1111"
    assert good.price_usd == pytest.approx(0.0031)  # string -> float
    assert good.txns["h24"] == {"buys": 5000, "sells": 4000}
    assert good.volume["h1"] == 60000 and good.price_change["h24"] == 80.0
    assert good.liquidity_usd == 200000 and good.liquidity_quote == 700
    assert good.fdv == 3100000 and good.market_cap == 3000000
    assert good.pair_created_at == 1759740800000 and good.boosts_active == 1
    sparse = pairs[1]  # GoodPairOrca: no txns/volume/fdv
    assert sparse.txns["h24"] == {"buys": None, "sells": None}
    assert sparse.volume == dict.fromkeys(ds.WINDOWS)
    assert sparse.fdv is None and sparse.market_cap is None and sparse.liquidity_base is None


def test_parse_search_shape_and_garbage():
    pairs = ds.parse_pairs(load("search_meme.json"))
    assert len(pairs) == 3
    eth = pairs[1]
    assert eth.chain_id == "ethereum" and eth.liquidity_usd is None and eth.price_usd is None
    assert ds.parse_pairs({"pairs": None}) == []
    assert ds.parse_pairs([1, "x", None]) == []
    p = ds.parse_pair({"priceUsd": "NaN", "liquidity": {"usd": True}, "txns": "bad"})
    assert p.price_usd is None and p.liquidity_usd is None and p.txns["h1"]["buys"] is None


def test_parse_rugcheck():
    clean = ds.parse_rugcheck(load("rugcheck_clean.json"))
    assert clean.score == 501 and clean.score_normalised == 7
    assert [(r.name, r.level) for r in clean.risks] == [
        ("Low amount of LP Providers", "warn"),
        ("Mutable metadata", "info"),
    ]
    danger = ds.parse_rugcheck(load("rugcheck_danger.json"))
    assert len(danger.risks) == 2  # nameless risk skipped
    assert danger.risks[0].score == 30000
    empty = ds.parse_rugcheck("garbage")
    assert empty.score is None and empty.risks == []


# ---------------------------------------------------------------------------- holders
def test_top_holder_share_excludes_pair_address_and_owned_vault():
    largest = load("rpc_largest_good.json")["result"]
    supply = load("rpc_supply.json")["result"]
    owners = {"PoolVaultGood": "GoodPair1111"}
    h = ds.compute_top_holder_share(largest, supply, ["GoodPair1111"], owners)
    # A 3% + B 2% + C..I 7 x 1% + J 0.5% = 12.5% once vault and pair account are removed
    assert h.top10_share == pytest.approx(0.125)
    assert h.excluded == ["PoolVaultGood", "GoodPair1111"]
    assert h.accounts_used == 10 and h.is_upper_bound is True


def test_top_holder_share_without_exclusion_is_higher_upper_bound():
    largest = load("rpc_largest_good.json")["result"]
    supply = load("rpc_supply.json")["result"]
    h = ds.compute_top_holder_share(largest, supply)
    assert h.top10_share == pytest.approx(0.66)  # 50 + 5 + 3 + 2 + 6 x 1
    assert h.excluded == []


def test_top_holder_share_missing_supply_is_unknown():
    largest = load("rpc_largest_good.json")["result"]
    assert ds.compute_top_holder_share(largest, {"value": {}}).top10_share is None
    assert ds.compute_top_holder_share({}, load("rpc_supply.json")["result"]).top10_share is None


def test_fetch_top_holder_share_resolves_owners_via_rpc():
    f = FakeFetch()
    h = ds.fetch_top_holder_share(f, RPC, "GoodMint1111", "GoodPair1111")
    assert h.top10_share == pytest.approx(0.125)
    methods = [d["method"] for _, d in f.calls]
    assert methods == ["getTokenLargestAccounts", "getTokenSupply", "getMultipleAccounts"]
    assert f.calls[0][1]["jsonrpc"] == "2.0" and f.calls[0][1]["params"] == ["GoodMint1111"]


def test_rpc_error_object_raises_fetch_error():
    def fetch(url, headers=None, data=None):
        return b'{"jsonrpc":"2.0","id":1,"error":{"code":-32602,"message":"bad"}}'

    with pytest.raises(ds.FetchError, match="RPC error"):
        ds.rpc_call(fetch, RPC, "getTokenSupply", ["x"])


# ---------------------------------------------------------------------------- flags
def test_liquidity_min_boundary():
    at = ds._liquidity_flags(make_pair(liquidity={"usd": 50_000}), 500, T)
    below = ds._liquidity_flags(make_pair(liquidity={"usd": 49_999.99}), 500, T)
    assert "LOW_LIQUIDITY" not in names(at)
    assert "LOW_LIQUIDITY" in names(below)


def test_cannot_exit_boundary_is_20x_position():
    t = ds.Thresholds(min_liquidity_usd=0)
    ok = ds._liquidity_flags(make_pair(liquidity={"usd": 200_000}), 10_000, t)
    bad = ds._liquidity_flags(make_pair(liquidity={"usd": 199_999}), 10_000, t)
    assert names(ok) == set()
    assert names(bad) == {"CANNOT_EXIT"}
    assert bad[0].severity == ds.HARD and "exiting" in bad[0].message
    custom = ds.Thresholds(min_liquidity_usd=0, exit_liquidity_multiple=50)
    assert "CANNOT_EXIT" in names(ds._liquidity_flags(make_pair(), 10_000, custom))


def test_holder_concentration_boundary():
    assert ds._holder_flags(ds.HolderShare(0.20, 10, []), T) == []
    flags = ds._holder_flags(ds.HolderShare(0.2001, 10, []), T)
    assert names(flags) == {"HOLDER_CONCENTRATION"} and flags[0].severity == ds.HARD
    assert "upper bound" in flags[0].message


def test_new_pair_boundary():
    assert ds._age_flags(make_pair(pairCreatedAt=NOW - 24 * H), NOW, T) == []
    young = ds._age_flags(make_pair(pairCreatedAt=NOW - 24 * H + 1), NOW, T)
    assert names(young) == {"NEW_PAIR"}
    assert (
        ds._age_flags(make_pair(pairCreatedAt=NOW), NOW, ds.Thresholds(min_pair_age_hours=0)) == []
    )


def _txns(buys, sells):
    return {"h24": {"buys": buys, "sells": sells}}


def test_honeypot_boundary():
    # 20 buys with 1 sell (= 5%) fires; 2 sells does not; 19 buys is too few to judge
    assert names(ds._txn_flags(make_pair(txns=_txns(20, 1)), T)) == {"HONEYPOT_HINT"}
    assert names(ds._txn_flags(make_pair(txns=_txns(20, 0)), T)) == {"HONEYPOT_HINT"}
    assert ds._txn_flags(make_pair(txns=_txns(20, 2)), T) == []
    assert ds._txn_flags(make_pair(txns=_txns(19, 0)), T) == []


def test_sell_pressure_boundary():
    assert ds._txn_flags(make_pair(txns=_txns(100, 300)), T) == []
    flags = ds._txn_flags(make_pair(txns=_txns(100, 301)), T)
    assert names(flags) == {"SELL_PRESSURE"} and flags[0].severity == ds.SOFT


def test_volume_liquidity_boundaries():
    liq = {"usd": 100_000}
    vol = {"h1": 1, "h6": 1, "h24": 5_000_000}
    assert ds._volume_flags(make_pair(liquidity=liq, volume=vol), T) == []
    vol["h24"] = 5_000_001
    assert names(ds._volume_flags(make_pair(liquidity=liq, volume=vol), T)) == {
        "VOLUME_LIQUIDITY_HIGH"
    }
    vol["h24"] = 5_000
    assert ds._volume_flags(make_pair(liquidity=liq, volume=vol), T) == []
    vol["h24"] = 4_999
    assert names(ds._volume_flags(make_pair(liquidity=liq, volume=vol), T)) == {
        "VOLUME_LIQUIDITY_LOW"
    }


def test_rugcheck_flags():
    clean = ds._rug_flags(ds.parse_rugcheck(load("rugcheck_clean.json")))
    assert names(clean) == {"RUGCHECK_WARN"} and ds.is_tradable(clean)
    danger = ds._rug_flags(ds.parse_rugcheck(load("rugcheck_danger.json")))
    assert names(danger) == {"RUGCHECK_DANGER"}
    # LP-unlocked is hard even though RugCheck labelled it "warn"
    assert "Mint Authority" in danger[0].message and "LP Unlocked" in danger[0].message
    freeze = ds.RugReport(1, None, [ds.RugRisk("Freeze Authority still enabled", "warn", None, 1)])
    assert names(ds._rug_flags(freeze)) == {"RUGCHECK_DANGER"}
    assert ds._rug_flags(ds.RugReport(0, 0, [])) == []


def test_unknown_data_never_passes():
    empty = ds.parse_pair({})
    flags = ds.evaluate_flags(empty, 500, NOW, T, holders=None, rug=None)
    assert names(flags) == {
        "UNKNOWN_LIQUIDITY",
        "UNKNOWN_PAIR_AGE",
        "UNKNOWN_TXNS",
        "UNKNOWN_VOLUME",
        "UNKNOWN_HOLDERS",
        "UNKNOWN_RUGCHECK",
    }
    assert all(f.severity == ds.UNKNOWN for f in flags)
    assert not ds.is_tradable(flags)
    assert not ds.is_tradable(ds.evaluate_flags(None, 500, NOW, T))
    # a perfect pair still fails if only holder data is missing
    good = ds.evaluate_flags(make_pair(), 500, NOW, T, None, ds.RugReport(0, 0, []))
    assert names(good) == {"UNKNOWN_HOLDERS"} and not ds.is_tradable(good)
    holders = ds.HolderShare(None, 0, [])
    assert names(ds._holder_flags(holders, T)) == {"UNKNOWN_HOLDERS"}


def test_all_clear_is_tradable():
    flags = ds.evaluate_flags(
        make_pair(), 500, NOW, T, ds.HolderShare(0.1, 10, []), ds.RugReport(0, 0, [])
    )
    assert flags == [] and ds.is_tradable(flags)


def test_every_flag_has_one_sentence_message():
    flags = [
        *ds._liquidity_flags(make_pair(liquidity={"usd": 1}), 500, T),
        *ds._age_flags(make_pair(pairCreatedAt=NOW), NOW, T),
        *ds._txn_flags(make_pair(txns=_txns(100, 0)), T),
        *ds._holder_flags(ds.HolderShare(0.9, 10, []), T),
        *ds.evaluate_flags(ds.parse_pair({}), 500, NOW, T),
    ]
    for f in flags:
        assert f.message.endswith(".") and f.severity in (ds.HARD, ds.SOFT, ds.UNKNOWN)


# ---------------------------------------------------------------------------- scoring
def test_volume_acceleration():
    p = make_pair(volume={"h1": 3000, "h6": 9000, "h24": 24000})
    assert ds.volume_acceleration(p) == pytest.approx(2.5)  # 2x h6 avg, 3x h24 avg -> 2.5
    assert ds.volume_acceleration(make_pair(volume={"h1": 10})) is None
    assert ds.volume_acceleration(make_pair(volume={})) is None


def test_momentum_rises_with_volume_price_txns_boost():
    base = make_pair()
    assert ds.momentum_score(make_pair(volume={"h1": 5000, "h6": 6000, "h24": 24000}), None) > (
        ds.momentum_score(base, None)
    )
    assert ds.momentum_score(make_pair(priceChange={"h1": 20}), None) > ds.momentum_score(
        base, None
    )
    assert ds.momentum_score(base, 500) > ds.momentum_score(base, None)
    assert ds.momentum_score(None, 1000) == 0.0


def test_rank_rows_tradable_first_then_score():
    rows = [
        {"address": "a", "symbol": "A", "tradable": False, "score": 99.0},
        {"address": "b", "symbol": "B", "tradable": True, "score": 1.0},
        {"address": "c", "symbol": "C", "tradable": True, "score": 5.0},
        {"address": "d", "symbol": None, "tradable": False, "score": 100.0},
    ]
    ranked = ds.rank_rows(rows)
    assert [r["address"] for r in ranked] == ["c", "b", "d", "a"]
    assert [r["rank"] for r in ranked] == [1, 2, 3, 4]


def test_primary_pair_picks_deepest_liquidity():
    pairs = ds.parse_pairs(load("tokens_solana.json"))[:2]
    assert ds.primary_pair(list(reversed(pairs))).pair_address == "GoodPair1111"
    assert ds.primary_pair([]) is None


# ---------------------------------------------------------------------------- scan / CLI
def run_scan(fetch, **kw):
    kw.setdefault("from_profiles", True)
    kw.setdefault("from_boosts", True)
    kw.setdefault("queries", ["meme"])
    return ds.scan(chain="solana", fetch=fetch, rpc_url=RPC, now_ms=NOW, **kw)


def test_scan_ranking_and_hard_fail_exclusion():
    wl = run_scan(FakeFetch())
    by = {r["address"]: r for r in wl["tokens"]}
    assert "0xAbC0000000000000000000000000000000000001" not in by  # other chain filtered
    assert "0xEthMeme" not in by
    assert wl["tradable"] == ["GoodMint1111", "MidMint1111"]
    assert [r["address"] for r in wl["tokens"][:2]] == ["GoodMint1111", "MidMint1111"]
    # hard-fails stay in the table, with reasons
    assert "NEW_PAIR" in names(ds.Flag(**f) for f in by["HotMint1111"]["flags"])
    assert by["HotMint1111"]["momentum"] > by["GoodMint1111"]["momentum"]  # momentum not enough
    assert by["RugMint1111"]["hard_fail_reasons"]
    assert not by["RugMint1111"]["tradable"]
    good = by["GoodMint1111"]
    assert good["holders"]["top10_share"] == pytest.approx(0.125)
    assert good["pair"]["pair_address"] == "GoodPair1111" and good["n_pairs"] == 2
    assert good["boost_amount"] == 50 and set(good["sources"]) == {"profiles", "boosts"}
    assert good["pair_age_hours"] == pytest.approx(72.0)
    assert good["volume_liquidity"] == pytest.approx(2.0)
    assert by["MidMint1111"]["boost_amount"] is None  # "oops" amount
    assert wl["scan_started_ms"] <= wl["scan_finished_ms"] and wl["evaluated_at_ms"] == NOW


def test_scan_source_failures_reported_not_raised():
    wl = run_scan(FakeFetch())
    by = {r["address"]: r for r in wl["tokens"]}
    # NoRugMint: RugCheck 404 -> unknown, not safe; the error is recorded
    assert "UNKNOWN_RUGCHECK" in {f["name"] for f in by["NoRugMint1111"]["flags"]}
    assert not by["NoRugMint1111"]["tradable"]
    assert any(e["source"] == "rugcheck" and "NoRugMint1111" in e["url"] for e in wl["errors"])
    # SOL showed up only as the base of a sparse search pair: all unknown, never tradable
    sol = by["So11111111111111111111111111111111111111112"]
    assert not sol["tradable"] and "UNKNOWN_LIQUIDITY" in {f["name"] for f in sol["flags"]}


def test_scan_whole_source_outage():
    wl = run_scan(FakeFetch(fail=(ds.BOOSTS_URL, "rugcheck", RPC)))
    assert wl["sources"]["boosts"] == "error" and wl["sources"]["profiles"] == "ok"
    srcs = {e["source"] for e in wl["errors"]}
    assert {"boosts", "rugcheck", "solana_rpc"} <= srcs
    assert wl["tradable"] == []  # no RugCheck / holder data -> nothing passes
    assert "HotMint1111" not in {r["address"] for r in wl["tokens"]}
    wl = run_scan(FakeFetch(fail=("dexscreener",)))
    assert wl["tokens"] == [] and len(wl["errors"]) == 3


def test_scan_non_solana_chain_has_unknown_holders_and_rugcheck():
    def fetch(url, headers=None, data=None):
        assert "rugcheck" not in url and data is None
        return json.dumps(
            [
                {
                    "chainId": "base",
                    "tokenAddress": "0xAAA",
                }
            ]
            if url == ds.PROFILES_URL
            else [
                {
                    "chainId": "base",
                    "pairAddress": "0xP",
                    "baseToken": {"address": "0xaaa", "symbol": "AAA"},
                    "liquidity": {"usd": 1e6},
                }
            ]
        ).encode()

    wl = ds.scan(chain="base", from_profiles=True, fetch=fetch, now_ms=NOW)
    row = wl["tokens"][0]
    assert row["symbol"] == "AAA"  # EVM address match is case-insensitive
    assert {"UNKNOWN_HOLDERS", "UNKNOWN_RUGCHECK"} <= {f["name"] for f in row["flags"]}
    assert not row["tradable"]


def test_max_tokens_caps_enrichment():
    f = FakeFetch()
    wl = run_scan(f, max_tokens=2)
    assert len(wl["tokens"]) == 2
    assert sum("rugcheck" in u for u, _ in f.calls) == 2


def test_cli_end_to_end_writes_watchlist(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv(ds.ENV_SOLANA_RPC, RPC)
    monkeypatch.setattr(ds.time, "time", lambda: NOW / 1000)
    f = FakeFetch()
    argv = ["--chain", "solana", "--query", "meme", "--from-profiles", "--from-boosts"]
    rc = ds.main([*argv, "--position-usd", "500", "--out", str(tmp_path)], fetch=f)
    assert rc == 0
    out = capsys.readouterr().out
    assert "GOOD" in out and "2/6 tradable" in out and "[source error] rugcheck" in out
    wl = ds.load_watchlist(tmp_path)
    assert wl is not None and wl["tradable"] == ["GoodMint1111", "MidMint1111"]
    assert wl["position_usd"] == 500 and wl["thresholds"]["exit_liquidity_multiple"] == 20
    with (tmp_path / "dex" / "watchlist.csv").open() as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 6 and tuple(rows[0]) == ds.CSV_COLUMNS
    assert rows[0]["symbol"] == "GOOD" and rows[0]["tradable"] == "True"
    assert rows[0]["top10_share"] == "0.125" and "RUGCHECK_WARN(soft)" in rows[0]["flags"]
    assert rows[0]["scan_started_ms"] == str(NOW)
    rug = next(r for r in rows if r["symbol"] == "RUG")
    assert "RugCheck danger-level risks" in rug["hard_fail_reasons"]
    assert any(u == RPC for u, _ in f.calls)  # SOLANA_RPC_URL honoured


def test_cli_requires_a_source(tmp_path):
    with pytest.raises(SystemExit):
        ds.main(["--out", str(tmp_path)], fetch=FakeFetch())


def test_load_watchlist_missing_or_corrupt(tmp_path):
    assert ds.load_watchlist(tmp_path) is None
    (tmp_path / "dex").mkdir()
    (tmp_path / "dex" / "watchlist.json").write_text("{not json")
    assert ds.load_watchlist(tmp_path) is None


def test_default_fetch_rejects_non_http_and_import_is_offline():
    with pytest.raises(ds.FetchError):
        ds.default_fetch("file:///etc/passwd")
    assert ds.fetch_json(lambda u: b"[1]", "https://x") == [1]
    with pytest.raises(ds.FetchError):
        ds.fetch_json(lambda u: b"<html>", "https://x")
