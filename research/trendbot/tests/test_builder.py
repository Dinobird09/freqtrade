"""Strategy builder: the block catalog, public candles, backtests and saving a paper bot."""

import json
from itertools import pairwise

import pytest

from research.trendbot import builder as bd
from research.trendbot.live_bot import BotSettings


H = 3_600_000
NOW = 1_760_000_000_000 // H * H


def kline_rows(n, start, step=H, base=100.0):
    return [
        [
            start + i * step,
            str(base + i % 7),
            str(base + i % 7 + 1),
            str(base + i % 7 - 1),
            str(base + (i + 1) % 7),
            "10",
        ]
        for i in range(n)
    ]


def fetch_from(responses, calls=None):
    def fetch(url, headers=None, data=None):
        if calls is not None:
            calls.append(url)
        for key, body in responses.items():
            if key in url:
                if isinstance(body, Exception):
                    raise body
                return json.dumps(body(url) if callable(body) else body).encode()
        raise bd.FetchError(f"HTTP Error 404 for {url}")

    return fetch


def test_catalog_lists_every_block():
    cat = bd.catalog()
    ids = [s["id"] for s in cat["strategies"]]
    for want in ("confluence", "mean_reversion", "grid", "dca"):
        assert want in ids
    assert len(cat["signals"]) >= 10 and all(s["description"] for s in cat["signals"])
    assert set(cat["default_required"]) <= {s["id"] for s in cat["signals"]}
    assert {k["id"] for k in cat["knobs"]} >= {"min_signals", "risk_pct", "stop_atr"}
    json.dumps(cat)  # the page gets it as JSON


def test_binance_candles_page_and_drop_the_forming_one():
    start = NOW - 1500 * H

    def page(url):
        t = int(url.split("startTime=")[1].split("&")[0])
        n = min(1000, (NOW - t) // H + 1)  # Binance also returns the still-forming candle
        return kline_rows(n, t)

    calls = []
    cs, src = bd.public_candles(
        "BTC/USDT",
        "1h",
        1500 // 24 + 1,
        fetch=fetch_from({"api.binance.com": page}, calls),
        now_ms=NOW,
    )
    assert src == "binance" and len(calls) == 2
    assert cs[-1].ts == NOW - H  # closed candles only
    assert all(b.ts - a.ts == H for a, b in pairwise(cs))
    assert cs[0].ts >= start - 25 * H


def test_falls_back_to_coinbase_when_binance_is_blocked():
    def cb(url):
        return [[(NOW - (i + 1) * H) // 1000, 99, 101, 100, 100.5, 3] for i in range(300)]

    fetch = fetch_from(
        {
            "api.binance.com": bd.FetchError("HTTP Error 451"),
            "api.binance.us": bd.FetchError("HTTP Error 403"),
            "api.exchange.coinbase.com/products/BTC-USD/candles": cb,
        }
    )
    cs, src = bd.public_candles("BTC/USDT", "1h", 10, fetch=fetch, now_ms=NOW)
    assert src == "coinbase" and len(cs) == 300
    assert cs[0].ts < cs[-1].ts and cs[-1].open == 100 and cs[-1].high == 101


def test_no_source_explains_and_suggests_demo():
    fetch = fetch_from({"binance": bd.FetchError("HTTP Error 451"), "coinbase": bd.FetchError("x")})
    with pytest.raises(bd.BuilderError, match="demo"):
        bd.public_candles("BTC/USDT", "1h", 30, fetch=fetch, now_ms=NOW)
    with pytest.raises(bd.BuilderError, match="timeframe"):
        bd.public_candles("BTC/USDT", "2h", 30)


def test_clean_spec_rules():
    s = bd.clean_spec(
        {
            "strategy": "confluence",
            "required": ["ema200", "bogus"],
            "counted": [],
            "params": {"min_signals": 1},
        }
    )
    assert s["params"]["required"] == ["ema200"] and s["params"]["use"] == ["ema200"]
    assert s["params"]["min_signals"] == 1
    with pytest.raises(bd.BuilderError, match="only 2 are on the board"):
        bd.clean_spec(
            {
                "strategy": "confluence",
                "required": ["ema200"],
                "counted": ["vwap"],
                "params": {"min_signals": 5},
            }
        )
    with pytest.raises(bd.BuilderError, match="unknown strategy"):
        bd.clean_spec({"strategy": "martingale"})
    with pytest.raises(bd.BuilderError, match="pair"):
        bd.clean_spec({"strategy": "dca", "pair": "BTC USDT; rm"})
    assert bd.clean_spec({"strategy": "dca"})["timeframe"] == "1d"
    assert bd.clean_spec({"strategy": "mean_reversion", "pair": "eth-usdt"})["pair"] == "ETH/USDT"


def test_backtest_a_lab_strategy_on_demo_data():
    res = bd.backtest(
        {"strategy": "mean_reversion", "exchange": "demo", "timeframe": "4h", "days": 400}
    )
    assert res["source"].startswith("demo") and res["candles"] > 500
    assert set(res["stats"]) >= {"trades", "return_pct", "cagr_pct", "max_dd_pct"}
    assert len(res["equity"]) <= 301
    json.dumps(res)


def test_backtest_confluence_with_builder_signals():
    from research.trendbot.confluence import SIGNALS

    ids = list(SIGNALS)
    res = bd.backtest(
        {
            "strategy": "confluence",
            "exchange": "demo",
            "timeframe": "4h",
            "days": 900,
            "required": ids[:1],
            "counted": ids[1:6],
            "params": {"min_signals": 3},
        }
    )
    assert res["spec"]["params"]["use"] == ids[:6]
    assert "trades" in res["stats"]
    for t in res["trades"]:
        assert t["stop"] < t["entry"]


def test_backtest_grid_and_dca_engines():
    g = bd.backtest(
        {
            "strategy": "grid",
            "exchange": "demo",
            "timeframe": "4h",
            "days": 200,
            "params": {"levels": 8, "budget": 1000},
        }
    )
    assert isinstance(g["result"], dict) and g["spec"]["strategy"] == "grid"
    d = bd.backtest(
        {
            "strategy": "dca",
            "exchange": "demo",
            "timeframe": "1d",
            "days": 400,
            "params": {"amount_quote": 50, "every": "7d"},
        }
    )
    assert isinstance(d["result"], dict)
    json.dumps([g, d])


def test_save_as_paper_bot(tmp_path):
    out = bd.save_bot(
        {"strategy": "mean_reversion", "pair": "SOL/USDT", "exchange": "coinbase"},
        "sol-mr",
        tmp_path,
    )
    raw = json.loads((tmp_path / "sol-mr.json").read_text())
    assert raw["mode"] == "paper" and raw["engine"] == "lab"
    assert raw["lab"]["strategy"] == "mean_reversion" and raw["pairs"] == ["SOL/USDT"]
    assert out["settings"] == raw
    BotSettings(**raw)
    with pytest.raises(bd.BuilderError, match="already exists"):
        bd.save_bot({"strategy": "mean_reversion"}, "sol-mr", tmp_path)
    with pytest.raises(bd.BuilderError, match="bot name"):
        bd.save_bot({"strategy": "mean_reversion"}, "../evil", tmp_path)


def test_save_engine_bots(tmp_path):
    for eng in ("grid", "dca"):
        bd.save_bot(
            {"strategy": eng, "params": {"budget": 500} if eng == "grid" else {}},
            f"b-{eng}",
            tmp_path,
        )
        raw = json.loads((tmp_path / f"b-{eng}.json").read_text())
        assert raw["engine"] == eng and eng in raw["lab"] and raw["mode"] == "paper"


def test_coinbase_4h_is_built_from_1h():
    def cb(url):
        return [[(NOW - (i + 1) * H) // 1000, 99, 101 + i % 3, 98, 100, 1] for i in range(300)]

    fetch = fetch_from({"coinbase.com/products/BTC-USD/candles": cb})
    cs, src = bd.public_candles("BTC/USDT", "4h", 40, "coinbase", fetch, now_ms=NOW)
    assert src == "coinbase" and len(cs) >= 50
    assert all(b.ts - a.ts == 4 * H for a, b in pairwise(cs))
    assert all(c.volume == 4 for c in cs)
