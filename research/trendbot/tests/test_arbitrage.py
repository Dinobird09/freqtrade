"""Arbitrage monitor: spatial gaps across exchanges and triangular loops, net of fees."""

import json

import pytest

from research.trendbot import arbitrage as arb


def book(**pairs):
    return {
        p.replace("_", "/"): {"bid": b, "ask": a, "bid_qty": 10.0, "ask_qty": 10.0}
        for p, (b, a) in pairs.items()
    }


def test_split_symbol():
    assert arb.split_symbol("BTCUSDT") == "BTC/USDT"
    assert arb.split_symbol("ETHFDUSD") == "ETH/FDUSD"
    assert arb.split_symbol("SOLBTC") == "SOL/BTC"
    assert arb.split_symbol("USDT") is None
    assert arb.split_symbol("FOOXYZ") is None


def test_spatial_gap_is_shown_after_fees():
    books = {
        "binance": book(BTC_USDT=(100.0, 100.1)),
        "okx": book(BTC_USDT=(101.0, 101.2)),
    }
    rows = arb.spatial(books, {"binance": 0.001, "okx": 0.001}, min_net=-1)
    r = rows[0]
    assert (r["buy_on"], r["sell_on"]) == ("binance", "okx")
    assert r["gross_pct"] == pytest.approx((101.0 / 100.1 - 1) * 100, abs=1e-3)
    assert r["net_pct"] == pytest.approx((101.0 / 100.1 * 0.999 * 0.999 - 1) * 100, abs=1e-3)
    assert r["net_pct"] < r["gross_pct"]
    assert r["size_quote"] == pytest.approx(10 * 100.1)
    assert not r["suspect"]


def test_spatial_fees_can_erase_a_gap():
    books = {"a": book(ETH_USDT=(10.0, 10.01)), "b": book(ETH_USDT=(10.02, 10.03))}
    assert arb.spatial(books, {"a": 0.001, "b": 0.001}, min_net=0) == []
    assert arb.spatial(books, {"a": 0.0, "b": 0.0}, min_net=0)[0]["net_pct"] > 0


def test_huge_gap_is_flagged_as_a_different_token():
    books = {"a": book(PEPE_USDT=(1.0, 1.01)), "b": book(PEPE_USDT=(2.0, 2.01))}
    r = arb.spatial(books, {}, min_net=-1)[0]
    assert r["suspect"] and "different tokens" in r["note"]


def test_single_exchange_pairs_are_ignored():
    assert arb.spatial({"a": book(X_USDT=(1, 1.1)), "b": book(Y_USDT=(1, 1.1))}, {}) == []


def test_triangular_finds_a_mispriced_loop():
    # USDT -> BTC -> ETH -> USDT: BTC 100, ETH/BTC 0.05 means ETH should be 5; it bids 5.2
    b = book(BTC_USDT=(99.99, 100.0), ETH_BTC=(0.0499, 0.05), ETH_USDT=(5.2, 5.21))
    rows = arb.triangular(b, fee=0.001, start="USDT", min_net=0)
    assert rows, "the loop pays after fees"
    best = rows[0]
    assert best["route"] == "USDT → BTC → ETH → USDT"
    expected = (1 / 100.0 * 0.999) / 0.05 * 0.999 * 5.2 * 0.999 - 1
    assert best["net_pct"] == pytest.approx(expected * 100, abs=1e-3)
    # top-of-book limits in USDT: leg 1 10 BTC*100; leg 2 10 ETH*0.05 BTC -> 0.5 BTC = ~50 USDT
    assert best["size_start"] == pytest.approx(0.5 / (0.999 / 100.0), rel=1e-3)


def test_triangular_fair_prices_lose_the_fees():
    b = book(BTC_USDT=(99.99, 100.0), ETH_BTC=(0.0499, 0.05), ETH_USDT=(4.99, 5.0))
    assert arb.triangular(b, fee=0.001, start="USDT", min_net=0) == []


def fake_fetch(responses):
    def fetch(url, headers=None, data=None):
        for key, body in responses.items():
            if key in url:
                if isinstance(body, Exception):
                    raise body
                return json.dumps(body).encode()
        raise arb.FetchError(f"HTTP Error 404 for {url}")

    return fetch


def test_readers_parse_each_exchange_format():
    fetch = fake_fetch(
        {
            "api.binance.com": [
                {
                    "symbol": "BTCUSDT",
                    "bidPrice": "100",
                    "askPrice": "100.1",
                    "bidQty": "1",
                    "askQty": "2",
                }
            ],
            "okx.com": {
                "data": [
                    {
                        "instId": "BTC-USDT",
                        "bidPx": "101",
                        "askPx": "101.1",
                        "bidSz": "1",
                        "askSz": "1",
                    }
                ]
            },
            "bybit.com": {
                "result": {
                    "list": [
                        {
                            "symbol": "ETHUSDT",
                            "bid1Price": "5",
                            "ask1Price": "5.01",
                            "bid1Size": "3",
                            "ask1Size": "3",
                        }
                    ]
                }
            },
            "kucoin.com": {
                "data": {"ticker": [{"symbol": "SOL-USDT", "buy": "2", "sell": "2.01"}]}
            },
            "AssetPairs": {"result": {"XXBTZUSD": {"wsname": "XBT/USD"}}},
            "kraken.com/0/public/Ticker": {
                "result": {"XXBTZUSD": {"a": ["100.2", "1", "1.5"], "b": ["100.1", "1", "2.5"]}}
            },
            "coinbase.com/products/BTC-USD/ticker": {"bid": "100.3", "ask": "100.4"},
        }
    )
    books, errors = arb.books(["binance", "okx", "bybit", "kucoin", "kraken", "coinbase"], fetch)
    assert errors == {}
    assert books["binance"]["BTC/USDT"] == {
        "bid": 100.0,
        "ask": 100.1,
        "bid_qty": 1.0,
        "ask_qty": 2.0,
    }
    assert books["okx"]["BTC/USDT"]["bid"] == 101.0
    assert books["bybit"]["ETH/USDT"]["ask"] == 5.01
    assert books["kucoin"]["SOL/USDT"]["bid"] == 2.0
    assert books["kraken"]["BTC/USD"] == {
        "bid": 100.1,
        "ask": 100.2,
        "bid_qty": 2.5,
        "ask_qty": 1.5,
    }
    assert books["coinbase"] == {
        "BTC/USD": {"bid": 100.3, "ask": 100.4, "bid_qty": 0.0, "ask_qty": 0.0}
    }


def test_a_blocked_exchange_is_reported_not_fatal():
    fetch = fake_fetch(
        {
            "api.binance.com": arb.FetchError("HTTP Error 451: Unavailable For Legal Reasons"),
            "okx.com": {"data": [{"instId": "BTC-USDT", "bidPx": "101", "askPx": "101.1"}]},
        }
    )
    books, errors = arb.books(["binance", "okx", "nope"], fetch)
    assert "451" in errors["binance"] and "country" in errors["binance"]
    assert errors["nope"] == "unknown exchange"
    assert list(books) == ["okx"]


def test_crossed_or_empty_books_are_dropped():
    assert arb._row("101", "100") is None
    assert arb._row("0", "1") is None
    assert arb._row(None, "1") is None


def test_scan_record_and_history(tmp_path):
    fetch = fake_fetch(
        {
            "api.binance.com": [
                {
                    "symbol": "BTCUSDT",
                    "bidPrice": "100",
                    "askPrice": "100.01",
                    "bidQty": "5",
                    "askQty": "5",
                }
            ],
            "okx.com": {
                "data": [
                    {
                        "instId": "BTC-USDT",
                        "bidPx": "101",
                        "askPx": "101.01",
                        "bidSz": "5",
                        "askSz": "5",
                    }
                ]
            },
        }
    )
    res = arb.scan(["binance", "okx"], fetch)
    assert res["spatial"][0]["net_pct"] > 0.5
    assert arb.record(tmp_path, res) == 1
    assert arb.record(tmp_path, res) == 1
    h = arb.history(tmp_path)
    assert h["entries"] == 2
    assert h["routes"][0]["route"] == "BTC/USDT binance→okx" and h["routes"][0]["seen"] == 2


def test_monitor_caches_between_scans(tmp_path):
    calls = []

    def fetch(url, headers=None, data=None):
        calls.append(url)
        return json.dumps({"data": [{"instId": "BTC-USDT", "bidPx": "1", "askPx": "1.1"}]}).encode()

    m = arb.Monitor(tmp_path, fetch)
    m.view({"exchanges": "okx"})
    m.view({"exchanges": "okx"})
    assert len(calls) == 1
    m.view({"exchanges": "okx", "fresh": "1"})
    assert len(calls) == 2


def test_cli_prints_a_report(monkeypatch, capsys):
    monkeypatch.setattr(arb, "default_fetch", None)
    res = {
        "ts": "t",
        "exchanges": {"okx": 1},
        "errors": {"binance": "blocked"},
        "spatial": [
            {
                "pair": "BTC/USDT",
                "buy_on": "okx",
                "buy_at": 1.0,
                "sell_on": "kraken",
                "sell_at": 1.1,
                "net_pct": 9.0,
                "suspect": False,
            }
        ],
        "triangular": {"okx": [{"route": "USDT → A → B → USDT", "net_pct": 0.1}]},
    }
    monkeypatch.setattr(arb, "scan", lambda *a, **k: res)
    assert arb.main([]) == 0
    out = capsys.readouterr().out
    assert "binance: not read (blocked)" in out and "+9.000%" in out and "USDT → A" in out
