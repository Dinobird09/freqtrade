"""Market scanner filters, page scraping and the VADER scorer."""

import pytest

from research.trendbot import scanner as sc
from research.trendbot import sentiment as se


DAY = 86_400_000
NOW = 1_800_000_000_000 // DAY * DAY + 10 * 3_600_000


class Ex:
    id = "fake"

    def __init__(self):
        self.calls = []

    def fetch_tickers(self):
        return {
            "PEPE/USDT": {"percentage": 30.0, "last": 1.30},  # passes everything
            "BIG/USDT": {"percentage": 25.0, "last": 2.50},  # float too large
            "QUIET/USDT": {"percentage": 12.0, "last": 1.12},  # RVOL too low
            "BTC/USDT": {"percentage": 1.0, "last": 100.0},  # not a mover
            "USDC/USDT": {"percentage": 40.0, "last": 1.0},  # stablecoin
            "ETH/BTC": {"percentage": 50.0, "last": 1.0},  # wrong quote
            "XYZ/USDT:USDT": {"percentage": 90.0, "last": 1.0},  # a perpetual, not spot
        }

    def fetch_ohlcv(self, sym, tf, limit=None):
        self.calls.append(sym)
        vol_today = {"PEPE/USDT": 900.0, "BIG/USDT": 900.0, "QUIET/USDT": 150.0}[sym]
        past = [[NOW - (51 - i) * DAY, 1.0, 1.1, 0.9, 1.0, 100.0] for i in range(50)]
        return past + [[NOW // DAY * DAY, 1.0, 1.4, 0.95, 1.3, vol_today]]


def cg(url):
    if "/search" in url:
        sym = url.split("query=")[1]
        return {"coins": [{"id": sym.lower(), "symbol": sym, "market_cap_rank": 50}]}
    ids = url.split("ids=")[1].split(",")
    supply = {"pepe": 4_000_000, "big": 900_000_000, "quiet": 1_000_000}
    return [{"id": i, "circulating_supply": supply[i], "market_cap": 1e7} for i in ids]


def _news(tmp_path, titles):
    items = [
        se.NewsItem("rss:test", str(i), "https://x", t, None, NOW - 3_600_000)
        for i, t in enumerate(titles)
    ]
    se.save_news(tmp_path, items)


def test_scanner_applies_every_filter(tmp_path):
    _news(tmp_path, ["PEPE token unlock next week", "$BIG breaks out", "QUIET listing"])
    ex = Ex()
    res = sc.scan(ex, tmp_path, fetch=cg, now_ms=NOW)
    assert sorted(ex.calls) == ["BIG/USDT", "PEPE/USDT", "QUIET/USDT"]  # only the movers
    by = {r["symbol"]: r for r in res["rows"]}
    assert res["passing"] == ["PEPE/USDT"] and by["PEPE/USDT"]["rvol"] == 9.0
    assert by["PEPE/USDT"]["day_change_pct"] == pytest.approx(30.0)
    assert by["PEPE/USDT"]["catalysts"][0]["kind"] == "unlock"
    assert "float 900,000,000" in by["BIG/USDT"]["why"]
    assert "RVOL 1.5x" in by["QUIET/USDT"]["why"]
    assert sc.load(tmp_path)["passing"] == ["PEPE/USDT"]


def test_no_catalyst_no_pass(tmp_path):
    res = sc.scan(Ex(), tmp_path, fetch=cg, now_ms=NOW)
    assert res["passing"] == [] and "no news, social or unlock catalyst" in res["rows"][0]["why"]
    loose = sc.ScannerSettings(require_catalyst=False)
    assert sc.scan(Ex(), tmp_path, loose, fetch=cg, now_ms=NOW)["passing"] == ["PEPE/USDT"]


def test_page_scraping_without_beautifulsoup():
    html = b"""<html><nav><h3>Menu</h3></nav>
      <h2 class="t"><a href="/a/1">Bitcoin ETF inflows hit a record high</a></h2>
      <h2><a href="https://x.com/2">Solana outage halts block production</a></h2></html>"""
    items = se.parse_page(html, "page:test", 1, base_url="https://news.example")
    assert [i.title for i in items] == [
        "Bitcoin ETF inflows hit a record high",
        "Solana outage halts block production",
    ]
    assert items[0].url == "https://news.example/a/1" and items[1].url == "https://x.com/2"


def test_vader_scorer_and_the_auto_choice():
    class FakeVader:
        def polarity_scores(self, text):
            return {"compound": 0.8 if "record" in text else -0.5}

    v = se.VaderScorer(FakeVader())
    assert v.available()[0] and v.score_many(["record high", "hack"]) == [0.8, -0.5]
    assert se.make_scorer("lexicon").name == "lexicon"
    with pytest.raises(ValueError):
        se.make_scorer("nope")
