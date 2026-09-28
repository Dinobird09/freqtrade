"""Offline tests for research/trendbot/sentiment.py (fixtures only, never the network)."""

import http.server
import json
import random
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from research.trendbot import sentiment as sm
from research.trendbot.config import StrategyConfig
from research.trendbot.layers import (
    LAYER_TYPES,
    STATUSES,
    MarketView,
    TrainContext,
    validate_layer,
)
from research.trendbot.models import DAY_MS, HOUR_MS, CandidateOutcome, FeatureRow, NewsEvent
from research.trendbot.news import CSV_HEADER, NewsCalendar, load_events
from research.trendbot.synthetic import make_world


FIX = Path(__file__).resolve().parent / "fixtures" / "sentiment"
CFG = StrategyConfig()
TF = CFG.timeframe_ms
NOW = int(datetime(2024, 3, 1, 13, 30, tzinfo=UTC).timestamp() * 1000)
MAR1 = int(datetime(2024, 3, 1, tzinfo=UTC).timestamp() * 1000)


def fx(name: str) -> bytes:
    return (FIX / name).read_bytes()


def item(title, fetched, coins=("BTC",), score=None, source="rss:x", item_id=None, url=""):
    return sm.NewsItem(source, item_id or title, url, title, None, fetched, tuple(coins), score)


def row_at(close_ts: int) -> FeatureRow:
    return FeatureRow(close_ts - TF, close_ts, 1.0, *([None] * 8), hour_utc=0)


# ---------------------------------------------------------------------- HTTP
def test_import_does_not_touch_network_and_default_fetch_rejects_non_http():
    with pytest.raises(ValueError):
        sm.http_fetch("file:///etc/passwd")


def test_http_fetch_sends_user_agent_and_caps_size(monkeypatch):
    for var in ("http_proxy", "HTTP_PROXY", "https_proxy", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(var, raising=False)
    seen = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen["ua"] = self.headers.get("User-Agent")
            body = b"x" * 5000
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    th = threading.Thread(target=srv.handle_request, daemon=True)
    th.start()
    try:
        with pytest.raises(ValueError, match="larger than"):
            sm.http_fetch(f"http://127.0.0.1:{srv.server_port}/", {}, max_bytes=1000)
    finally:
        th.join(5)
        srv.server_close()
    assert seen["ua"] == sm.USER_AGENT


# ---------------------------------------------------------------------- parsers
def test_parse_fear_greed_fixture_skips_bad_rows_and_is_sorted():
    rows = sm.parse_fear_greed(fx("fear_greed.json"))
    assert [(v, c) for _, v, c in rows] == [(55, "Greed"), (20, "Extreme Fear"), (72, "Greed")]
    assert rows[1][0] == MAR1
    assert sm.fg_known_from(MAR1) == MAR1 + DAY_MS  # known from the NEXT UTC midnight
    with pytest.raises(ValueError):
        sm.parse_fear_greed(b'{"nodata": 1}')
    with pytest.raises(ValueError):
        sm.parse_fear_greed(b"<html>rate limited</html>")


def test_parse_rss_fixture():
    items = sm.parse_feed(fx("rss.xml"), "rss:example", NOW)
    assert [i.item_id for i in items] == ["ex-1001", "ex-1002", "https://example.com/sec-sol"]
    first, second, third = items
    assert first.url == "https://example.com/btc-record"
    assert first.published_ts == MAR1 + 10 * HOUR_MS + 15 * 60_000
    assert first.fetched_ts == first.known_from_ts == NOW
    assert second.title == 'Binance halts withdrawals after "hot wallet" exploit'
    assert third.published_ts is None  # unparseable date


def test_parse_atom_fixture():
    items = sm.parse_feed(fx("atom.xml"), "rss:atom", NOW)
    assert [i.url for i in items] == [
        "https://example.org/eth-slides",
        "https://example.org/ada-upgrade",
    ]
    assert items[0].item_id == "urn:example:entry:1"
    assert items[0].published_ts == MAR1 + 9 * HOUR_MS + 30 * 60_000
    assert items[1].published_ts == MAR1 + 7 * HOUR_MS  # 08:00+01:00
    assert items[1].title == "Cardano upgrade launches <b>on time</b>"


def test_xml_with_doctype_or_entity_is_rejected_and_malformed_xml_raises():
    with pytest.raises(ValueError, match="DOCTYPE"):
        sm.parse_feed(fx("doctype.xml"), "rss:evil", NOW)
    with pytest.raises(ValueError, match="DOCTYPE"):
        sm.parse_feed(b'<rss><!ENTITY x "y"><channel/></rss>', "rss:evil", NOW)
    with pytest.raises(ValueError, match="malformed"):
        sm.parse_feed(b"<rss><channel><item><title>x</title></channel>", "rss:bad", NOW)
    with pytest.raises(ValueError, match="larger"):
        sm.parse_feed(b" " * (sm.MAX_BYTES + 1), "rss:big", NOW)


def test_parse_reddit_fixture():
    items = sm.parse_reddit(fx("reddit.json"), "Bitcoin", NOW)
    assert [i.item_id for i in items] == ["t3_abc2", "t3_abc3"]  # stickied / untitled skipped
    assert items[0].source == "reddit:Bitcoin"
    assert items[0].url == "https://www.reddit.com/r/Bitcoin/comments/abc2/btc/"
    assert items[0].published_ts == 1709283600_000
    assert items[1].published_ts is None
    with pytest.raises(ValueError):
        sm.parse_reddit(b"[]", "Bitcoin", NOW)


def test_parse_cryptopanic_fixture():
    items = sm.parse_cryptopanic(fx("cryptopanic.json"), NOW)
    assert [i.item_id for i in items] == ["991", "992"]
    assert items[0].coins == ("DOGE",)
    assert items[1].url == "https://cryptopanic.com/news/992/"
    with pytest.raises(ValueError):
        sm.parse_cryptopanic(b"not json", NOW)


# ---------------------------------------------------------------------- tagging + scoring
def test_coin_tagging_names_tickers_and_word_boundaries():
    tag = sm.CoinTagger().tag
    assert tag("Ether and $SOL rally as Bitcoin ETF approved") == ("BTC", "ETH", "SOL")
    assert tag("Whether ethereal solutions matter") == ()  # no 'ether'/'sol' substrings
    assert tag("sol is lowercase here, but Solana is not") == ("SOL",)
    assert tag("BNB Chain upgrade; XRP and dogecoin") == ("BNB", "DOGE", "XRP")
    custom = sm.CoinTagger({"PEPE": ("pepe coin",)}).tag
    assert custom("PEPE coin surges; Pepe Coin fans cheer") == ("PEPE",)
    assert custom("Bitcoin") == ()


def test_lexicon_signs_negation_and_intensifiers():
    s = sm.LexiconScorer().score
    assert s("Bitcoin surges to record high") > 0.3
    assert s("Exchange hacked, $40M drained") < -0.5
    assert s("Weather report for Tuesday") == 0.0
    assert s("Bitcoin will not crash, analysts say") > 0
    assert s("Ether doesn't rally") < 0 < s("Ether rallies")
    assert s("Bitcoin rallies") > 0 and s("Bitcoin fails to rally") < 0
    assert s("massive surge in BTC") > s("surge in BTC") > s("slightly surge in BTC") > 0
    for text in ("crash " * 50, "surge " * 50):
        assert -1.0 <= s(text) <= 1.0


def test_finbert_unavailable_path(monkeypatch):
    real = sm.importlib.util.find_spec
    monkeypatch.setattr(
        sm.importlib.util,
        "find_spec",
        lambda name, *a: None if name == "transformers" else real(name, *a),
    )
    ok, why = sm.FinBertScorer().available()
    assert not ok and "not installed" in why
    assert isinstance(sm.make_scorer("auto"), sm.LexiconScorer)
    assert isinstance(sm.make_scorer("lexicon"), sm.LexiconScorer)
    with pytest.raises(RuntimeError):
        sm.make_scorer("finbert")
    with pytest.raises(ValueError):
        sm.make_scorer("vader")


def test_finbert_score_is_p_positive_minus_p_negative_with_injected_pipeline():
    def fake(texts, **kw):
        return [
            [
                {"label": "positive", "score": 0.7},
                {"label": "negative", "score": 0.2},
                {"label": "neutral", "score": 0.1},
            ]
            for _ in texts
        ]

    fb = sm.FinBertScorer(pipeline=fake)
    assert fb.available()[0]
    assert fb.score_many(["a", "b"]) == pytest.approx([0.5, 0.5])
    assert fb.score_many([]) == []


def test_score_items_falls_back_to_lexicon_when_scorer_breaks():
    class Broken(sm.LexiconScorer):
        name = "broken"

        def score_many(self, texts):
            raise RuntimeError("model crashed")

    out, name = sm.score_items([item("Bitcoin surges", NOW)], Broken())
    assert name == "lexicon" and out[0].score > 0 and out[0].scorer == "lexicon"


# ---------------------------------------------------------------------- store
def test_merge_dedupes_by_key_and_url_first_sighting_wins(tmp_path):
    a = item("Bitcoin surges", NOW, score=0.5, url="https://x/1")
    again = item("Bitcoin surges", NOW + HOUR_MS, score=0.9, url="https://x/1")
    same_url_other_source = item("BTC up", NOW + 2, source="cryptopanic", url="https://x/1")
    allitems, added = sm.merge_items([], [a])
    allitems, added = sm.merge_items(allitems, [again, same_url_other_source])
    assert added == [] and len(allitems) == 1 and allitems[0].fetched_ts == NOW
    sm.save_news(tmp_path, allitems)
    back = sm.load_news(tmp_path)
    assert back == allitems


def test_4h_aggregation_uses_known_from_only(tmp_path):
    b = MAR1 + 8 * HOUR_MS  # a 4H bucket [08:00, 12:00)
    items = [
        item("a", b, ("BTC",), 0.5),
        item("b", b + 4 * HOUR_MS - 1, ("BTC", "ETH"), -0.1),
        item("c", b + 4 * HOUR_MS, ("BTC",), -1.0),  # fetched at the bucket end: next bucket
        item("d", b + 1, ("BTC",), None),  # unscored: ignored
    ]
    agg = sm.aggregate_4h(items)
    assert agg["BTC"] == [(b, 0.2, 2), (b + 4 * HOUR_MS, -1.0, 1)]
    assert agg["ETH"] == [(b, -0.1, 1)]
    assert agg["ALL"][0] == (b, 0.2, 2)
    sm.save_aggregate(tmp_path, agg)
    src = sm.load_sources(tmp_path)
    assert src["news_sentiment"]["BTC"] == agg["BTC"]
    assert src["fear_greed"] == []
    # the layer's trailing mean only sees buckets that CLOSED by the decision time
    series = sm._NewsSeries(src["news_sentiment"])
    assert series.trailing("BTC", b + 4 * HOUR_MS - 1, DAY_MS, 1) is None
    assert series.trailing("BTC", b + 4 * HOUR_MS, DAY_MS, 1) == pytest.approx(0.2)
    assert series.trailing("BTC", b + 8 * HOUR_MS, DAY_MS, 1) == pytest.approx(-0.2)
    assert series.trailing("BTC", b + 4 * HOUR_MS + DAY_MS, DAY_MS, 1) == pytest.approx(-1.0)


def test_fear_greed_store_keeps_first_value_and_known_from(tmp_path):
    sm.save_fear_greed(tmp_path, [(MAR1, 20, "Extreme Fear")])
    sm.save_fear_greed(tmp_path, [(MAR1, 99, "revised"), (MAR1 + DAY_MS, 50, "Neutral")])
    assert sm.load_sources(tmp_path)["fear_greed"] == [
        (MAR1 + DAY_MS, 20),
        (MAR1 + 2 * DAY_MS, 50),
    ]


# ---------------------------------------------------------------------- auto events
def test_auto_events_keywords_scopes_and_kinds():
    t = NOW
    items = [
        item("Binance halts withdrawals after exploit", t, ("BNB",)),
        item("SEC sues Solana foundation", t, ("SOL",)),
        item("Class-action lawsuit filed against ether staking firm", t, ("ETH",)),
        item("Exchange to delist DOGE pairs", t, ("DOGE",)),
        item("Country moves to ban bitcoin mining", t, ("BTC",)),
        item("Crypto lender insolvency hits XRP holders", t, ("XRP",)),
        item("Bitcoin surges to record high", t, ("BTC",)),  # not high impact
        item("DeFi protocol hacked", t, ()),  # untagged: skipped
        item("sec filing shows BTC charges", t, ("BTC",)),  # lowercase 'sec' is not the SEC
    ]
    evs = sm.auto_events(items)
    got = {(e.scope, e.kind) for e in evs}
    assert got == {
        ("EXCHANGE:binance", "other"),
        ("SOL", "regulatory"),
        ("ETH", "legal"),
        ("DOGE", "other"),
        ("BTC", "regulatory"),
        ("XRP", "legal"),
    }
    assert all(e.impact == "high" and e.ts == e.known_from_ts == t for e in evs)


def test_append_events_round_trip_dedupe_and_never_removes(tmp_path):
    path = tmp_path / "events.csv"
    path.write_text(
        ",".join(CSV_HEADER) + "\n2024-03-12T12:30:00Z,ALL,high,macro,US CPI", encoding="utf-8"
    )  # no trailing newline, 5-column header
    evs = sm.auto_events([item("Binance halts withdrawals, users say", NOW, ("BNB",))])
    assert len(sm.append_events(path, evs)) == 1
    assert sm.append_events(path, evs) == []  # dedupe
    loaded = load_events(path)
    assert len(loaded) == 2 and loaded[1].note == "US CPI"
    auto = loaded[0]
    assert (auto.ts, auto.scope, auto.kind, auto.impact) == (
        NOW,
        "EXCHANGE:binance",
        "other",
        "high",
    )
    # empty known_from + unscheduled kind == blocks from the fetch time only
    cal = NewsCalendar(loaded, CFG)
    assert cal.check("BTC/USDT", NOW - 1).allowed
    assert not cal.check("BTC/USDT", NOW + HOUR_MS).allowed

    fresh = tmp_path / "new.csv"
    sm.append_events(fresh, evs)
    ev = load_events(fresh)[0]
    assert ev.known_from_ts == NOW and ev.note.startswith("auto:rss:x: Binance")

    bad = tmp_path / "bad.csv"
    bad.write_text("nope\n", encoding="utf-8")
    with pytest.raises(ValueError):
        sm.append_events(bad, evs)
    assert bad.read_text(encoding="utf-8") == "nope\n"


# ---------------------------------------------------------------------- collect
def _fake_fetch(fail: set[str] = frozenset()):
    calls = []

    def fetch(url, headers):
        calls.append(url)
        if any(f in url for f in fail):
            raise OSError(f"connection refused for {url}")
        if "alternative.me" in url:
            return fx("fear_greed.json")
        if "reddit.com" in url:
            return fx("reddit.json")
        if "cryptopanic.com" in url:
            return fx("cryptopanic.json")
        if "decrypt" in url:
            return fx("atom.xml")
        return fx("rss.xml")

    return fetch, calls


def test_collect_all_sources_one_failing(tmp_path, monkeypatch):
    monkeypatch.setenv(sm.CRYPTOPANIC_ENV, "sekrit-token")
    events = tmp_path / "events.csv"
    fetch, calls = _fake_fetch({"theblock", "cryptopanic"})
    settings = {
        "events": str(events),
        "scorer": "lexicon",
        "feeds": {"coindesk": sm.DEFAULT_FEEDS["coindesk"], "decrypt": sm.DEFAULT_FEEDS["decrypt"],
                  "theblock": sm.DEFAULT_FEEDS["theblock"]},
    }  # fmt: skip
    out = sm.collect(tmp_path, settings, fetch, now_ms=NOW)
    src = out["sources"]
    assert src["fear_greed"] == {"ok": True, "items": 3}
    assert src["rss:coindesk"]["ok"] and src["rss:decrypt"]["items"] == 2
    assert not src["rss:theblock"]["ok"] and "connection refused" in src["rss:theblock"]["error"]
    assert not src["cryptopanic"]["ok"]
    assert any("auth_token=sekrit-token" in u for u in calls)
    assert "sekrit-token" not in json.dumps(out)  # token redacted from errors
    assert all(src[f"reddit:{s}"]["items"] == 2 for s in sm.DEFAULT_SUBREDDITS)
    assert not out["ok"] and len(out["errors"]) == 2
    # reddit items are the same across subreddits by id but different sources: key differs,
    # yet the URL dedupe folds them into one
    assert out["items_new"] == 3 + 2 + 2
    stored = sm.load_news(tmp_path)
    assert all(i.score is not None and i.scorer == "lexicon" for i in stored)
    assert all(i.fetched_ts == NOW for i in stored)
    btc = next(i for i in stored if i.item_id == "ex-1001")
    assert btc.coins == ("BTC",)
    for f in (sm.NEWS_CSV, sm.FG_CSV, sm.AGG_CSV):
        assert (tmp_path / "sentiment" / f).exists()
    loaded = sm.load_sources(tmp_path)
    assert loaded["fear_greed"][0] == (MAR1 - DAY_MS, 55)
    assert "BTC" in loaded["news_sentiment"] and "ALL" in loaded["news_sentiment"]
    assert out["auto_events_added"] >= 3
    scopes = {e.scope for e in load_events(events)}
    assert {"EXCHANGE:binance", "SOL", "ETH"} <= scopes

    # a second run adds nothing new: no duplicate items, no duplicate events
    n_events = len(load_events(events))
    out2 = sm.collect(tmp_path, settings, _fake_fetch()[0], now_ms=NOW + HOUR_MS)
    assert out2["items_new"] == 1 + 1  # only cryptopanic (now working) is new
    assert out2["ok"] and len(load_events(events)) == n_events
    assert len(sm.load_news(tmp_path)) == len(stored) + 2


def test_collect_skips_cryptopanic_without_token(tmp_path, monkeypatch):
    monkeypatch.delenv(sm.CRYPTOPANIC_ENV, raising=False)
    fetch, calls = _fake_fetch()
    out = sm.collect(tmp_path, {"feeds": {}, "subreddits": []}, fetch, now_ms=NOW)
    assert out["sources"]["cryptopanic"]["skipped"]
    assert out["ok"] and not any("cryptopanic" in u for u in calls)
    assert out["auto_events_added"] == 0  # no events path configured


def test_collect_survives_total_outage(tmp_path):
    def down(url, headers):
        raise TimeoutError("offline")

    out = sm.collect(tmp_path, {"scorer": "lexicon"}, down, now_ms=NOW)
    assert not out["ok"] and out["items_new"] == 0
    assert len(out["errors"]) == 1 + len(sm.DEFAULT_FEEDS) + len(sm.DEFAULT_SUBREDDITS)
    assert sm.load_sources(tmp_path) == {"fear_greed": [], "news_sentiment": {}}


# ---------------------------------------------------------------------- layers
def _ctx(until_ts, candidates, sources, state_dir=None):
    return TrainContext(CFG, MarketView({}, TF), until_ts, candidates, [], sources, state_dir)


def _cand(signal_ts, r, pair="BTC/USDT"):
    return CandidateOutcome(pair, signal_ts, signal_ts + TF, signal_ts + 2 * TF, "TP", r, {})


def _fg_world(days=80):
    """F&G alternates 10 / 60 per day. Train (first half): value 10 -> -1R, 60 -> +1R.
    Test (second half): the opposite. Signals decide 6h after the day's value is known."""
    fg, cands = [], []
    for k in range(days):
        value = 10 if k % 2 else 60
        fg.append((MAR1 + k * DAY_MS, value))
        bad = value == 10
        r = -1.0 if bad == (k < days // 2) else 1.0
        cands.append(_cand(MAR1 + k * DAY_MS + 2 * HOUR_MS, r))
    return fg, cands, MAR1 + (days // 2) * DAY_MS


def test_layers_are_registered():
    assert LAYER_TYPES["fear_greed"] is sm.FearGreedLayer
    assert LAYER_TYPES["news_sentiment"] is sm.NewsSentimentLayer
    assert sm.FearGreedLayer.kind == sm.NewsSentimentLayer.kind == "sentiment"


def test_fear_greed_fit_uses_train_window_only():
    fg, cands, until = _fg_world()
    layer = sm.FearGreedLayer(min_kept=5)
    ctx = _ctx(until, cands, {"fear_greed": fg})
    assert layer.ready(ctx)[0]
    layer.fit(ctx)
    assert layer.rule == "veto_le_25"  # ties with <=40 go to the earlier pre-registered rule
    table = layer.fit_table["train_rules"]
    assert table["none"]["kept"] == 40 and table["veto_le_25"]["avg_r"] == 1.0
    # with the (opposite) later half also visible, nothing beats "no veto": ties -> none
    everything = sm.FearGreedLayer(min_kept=5)
    everything.fit(_ctx(MAR1 + 200 * DAY_MS, cands, {"fear_greed": fg}))
    assert everything.rule == "none"
    # state round trip
    clone = sm.FearGreedLayer()
    clone.load_state(json.loads(json.dumps(layer.state())))
    assert clone.rule == "veto_le_25"


def test_fear_greed_decision_time_value():
    """A day's value is known from the next UTC midnight; signal_ts + 4h is the decision."""
    fg = [(MAR1, 60), (MAR1 + DAY_MS, 10)]
    # decision at MAR1 + 1 day exactly: sees 10 (bad) -> the fit must learn from it
    cands = [_cand(MAR1 + DAY_MS - TF, -1.0) for _ in range(1)] + [
        _cand(MAR1 + DAY_MS - TF - 1 - k, 1.0)
        for k in range(3)  # decide just before: 60
    ]
    layer = sm.FearGreedLayer(min_kept=1)
    layer.fit(_ctx(MAR1 + 10 * DAY_MS, cands, {"fear_greed": fg}))
    assert layer.rule == "veto_le_25"
    assert layer.veto("BTC/USDT", row_at(MAR1 + DAY_MS), None)[0]
    blocked, why = layer.veto("BTC/USDT", row_at(MAR1 + DAY_MS - 1), None)
    assert not blocked and "60" in why
    assert not layer.veto("BTC/USDT", row_at(MAR1 - 1), None)[0]  # nothing known yet
    assert not layer.veto("BTC/USDT", row_at(MAR1 + 10 * DAY_MS), None)[0]  # stale value


def test_fear_greed_ready_requires_coverage():
    fg, cands, until = _fg_world()
    ok, why = sm.FearGreedLayer().ready(_ctx(until, cands, {"fear_greed": fg[30:]}))
    assert not ok and "needs 80%" in why
    assert not sm.FearGreedLayer().ready(_ctx(until, [], {"fear_greed": fg}))[0]


def test_fear_greed_live_reload_from_state_dir(tmp_path):
    fg, cands, until = _fg_world()
    sm.save_fear_greed(tmp_path, [(k - DAY_MS, v, "") for k, v in fg])  # same known_from
    layer = sm.FearGreedLayer(min_kept=5)
    layer.fit(_ctx(until, cands, {}, state_dir=tmp_path))  # no ctx.sources: loads from disk
    assert layer.rule == "veto_le_25" and layer.params["state_dir"] == str(tmp_path)
    live = sm.FearGreedLayer()
    live.load_state(json.loads(json.dumps(layer.state())))
    assert live.veto("BTC/USDT", row_at(MAR1 + DAY_MS + HOUR_MS), None)[0]  # day 1 = 10
    later = MAR1 + 90 * DAY_MS
    assert not live.veto("BTC/USDT", row_at(later), None)[0]
    sm.save_fear_greed(tmp_path, [(later - DAY_MS, 5, "Extreme Fear")])  # new data on disk
    assert live.veto("BTC/USDT", row_at(later), None)[0]


def _synthetic_fg(start, end, seed=7):
    rng = random.Random(seed)
    v, out = 50.0, []
    for d in range(start // DAY_MS, end // DAY_MS + 2):
        v = min(100.0, max(0.0, v + rng.gauss(0, 8)))
        out.append((d * DAY_MS, round(v)))
    return out


def test_fear_greed_validate_layer_on_synthetic_world():
    data, events = make_world("planted", 3, years=1.5)
    start = min(c[0].ts for c in data.values())
    end = max(c[-1].ts for c in data.values())
    sources = {"fear_greed": _synthetic_fg(start, end)}
    res = validate_layer(lambda: sm.FearGreedLayer(), data, CFG, events=events, sources=sources)
    assert res["status"] in STATUSES
    assert res["status"] in ("active", "rejected"), res
    res2 = validate_layer(lambda: sm.FearGreedLayer(), data, CFG, events=events, sources={})
    assert res2["status"] == "collecting"


def test_news_sentiment_collecting_without_history():
    data, events = make_world("planted", 3, years=1.0)
    res = validate_layer(lambda: sm.NewsSentimentLayer(), data, CFG, events=events, sources={})
    assert res["status"] == "collecting"
    assert "never collected" in res["reason"]


def test_news_sentiment_fit_and_veto():
    b0 = MAR1
    agg = {"BTC": [], "ETH": []}
    cands = []
    for k in range(40):  # 40 days, one BTC bucket per day at 00:00
        score = -0.5 if k % 2 else 0.3
        agg["BTC"].append((b0 + k * DAY_MS, score, 5))
        # decision at 08:00 the same day: the 00:00 bucket closed at 04:00 -> known
        cands.append(_cand(b0 + k * DAY_MS + 4 * HOUR_MS, -1.0 if score < 0 else 1.0))
    ctx = _ctx(b0 + 45 * DAY_MS, cands, {"news_sentiment": agg})
    ctx.view = MarketView({"BTC/USDT": []}, TF)
    layer = sm.NewsSentimentLayer(min_kept=5)
    # the view has no candles, so the train start falls back to until_ts: 0 days overlap
    assert not layer.ready(ctx)[0]
    layer.fit(ctx)
    assert layer.rule == "lt_-0.2"
    t = b0 + 4 * DAY_MS + 4 * HOUR_MS  # day 4 bucket (+0.3) just closed
    blocked, why = layer.veto("BTC/USDT", row_at(b0 + 1 * DAY_MS + 4 * HOUR_MS), None)
    assert blocked and "-0.50" in why  # day 1 bucket (-0.5) closed at 04:00
    assert not layer.veto("BTC/USDT", row_at(t), None)[0]
    # one ms earlier the day 1 bucket has not closed yet: nothing known, no veto
    assert not layer.veto("BTC/USDT", row_at(b0 + 1 * DAY_MS + 4 * HOUR_MS - 1), None)[0]
    assert not layer.veto("ETH/USDT", row_at(t), None)[0]  # no ETH headlines


def test_news_sentiment_ready_counts_collected_days_in_train_window():
    candles = make_world("null", 1, years=0.3)[0]
    start = candles["BTC/USDT"][0].ts
    until = start + 60 * DAY_MS
    view = MarketView(candles, TF)
    agg = {"ALL": [(start + k * DAY_MS, 0.0, 1) for k in range(29)]}
    ctx = TrainContext(CFG, view, until, [], [], {"news_sentiment": agg})
    ok, why = sm.NewsSentimentLayer().ready(ctx)
    assert not ok and "only 29 days" in why
    agg["ALL"].append((start + 40 * DAY_MS, 0.0, 1))
    agg["ALL"].append((until + DAY_MS, 0.0, 1))  # after the train window: never counted
    assert sm.NewsSentimentLayer().ready(ctx)[0]


def test_auto_event_blackout_is_only_additive():
    """An auto event never unblocks anything a hand-written calendar blocked."""
    manual = [NewsEvent(NOW + HOUR_MS, "ALL", "high", "macro", "FOMC")]
    auto = sm.auto_events([item("Bitcoin ETF lawsuit filed", NOW, ("BTC",))])
    base, both = NewsCalendar(manual, CFG), NewsCalendar(manual + auto, CFG)
    for k in range(-12, 12):
        ts = NOW + k * HOUR_MS
        if not base.check("BTC/USDT", ts).allowed:
            assert not both.check("BTC/USDT", ts).allowed
