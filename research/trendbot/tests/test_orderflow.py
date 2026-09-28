"""orderflow: footprint arithmetic, Binance aggTrades zips, ccxt paging, store, collect, layer.

Offline only: fake exchanges, zip files built in the test, synthetic worlds.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from research.trendbot import orderflow as of
from research.trendbot.config import StrategyConfig
from research.trendbot.layers import LAYER_TYPES, MarketView, TrainContext, validate_layer
from research.trendbot.models import HOUR_MS, CandidateOutcome, FeatureRow
from research.trendbot.orderflow import (
    FlowTrade,
    FootprintParams,
    FootprintSummary,
    OrderFlowLayer,
)
from research.trendbot.synthetic import make_world


TF = 4 * HOUR_MS
T0 = 1_704_067_200_000  # 2024-01-01T00:00:00Z (4h-aligned)
TICK = FootprintParams(bin_ticks=1, tick_size=1.0, keep_levels=True)


def tr(ts_off: int, price: float, qty: float, side: str, c: int = 0) -> FlowTrade:
    return FlowTrade(T0 + c * TF + ts_off, price, qty, side)


def candle_a() -> list[FlowTrade]:
    """Hand-checked: buy 12, sell 4, delta +8 (50 %), POC 101 (9), one buy imbalance at 101."""
    return [
        tr(0, 100, 1, "buy"),
        tr(1, 100, 2, "sell"),
        tr(2, 101, 8, "buy"),  # ask[101] = 8 >= 4 * bid[100] = 8  -> 400 % diagonal
        tr(3, 101, 1, "sell"),
        tr(4, 102, 3, "buy"),  # ask[102] = 3 < 4 * bid[101] = 4  -> no imbalance
        tr(5, 102, 1, "sell"),
    ]


def candle_bull(c: int, close: float) -> list[FlowTrade]:
    """Heavy taker selling at the low (100); range 100..110, lower third <= 103.33."""
    return [
        tr(0, 110, 1, "buy", c),
        tr(1, 100, 10, "sell", c),
        tr(2, 104, 2, "buy", c),
        tr(3, close, 1, "buy", c),
    ]


# ---------------------------------------------------------------------- aggregation
def test_footprint_arithmetic_by_hand():
    (s,) = of.footprints(candle_a(), "BTC/USDT", TICK)
    assert s.ts == T0 and (s.open, s.high, s.low, s.close) == (100, 102, 100, 102)
    assert (s.buy_volume, s.sell_volume, s.volume) == (12, 4, 16)
    assert s.delta == 8 and s.delta_pct == pytest.approx(50.0)
    assert s.trades == 6 and s.bin_size == 1.0 and s.levels_n == 3
    assert (s.poc_price, s.poc_volume) == (101.0, 9.0)
    assert (s.buy_imbalances, s.sell_imbalances) == (1, 0)
    assert not s.bull_absorption and not s.bear_absorption
    assert [(lv.price, lv.bid, lv.ask) for lv in s.levels] == [
        (100.0, 2.0, 1.0),
        (101.0, 1.0, 8.0),
        (102.0, 1.0, 3.0),
    ]


def test_imbalance_threshold_is_inclusive_and_sell_mirror():
    trades = [tr(0, 100, 4, "sell"), tr(1, 101, 1, "buy"), tr(2, 101, 1, "sell")]
    (s,) = of.footprints(trades, "X/USDT", TICK)
    # bid[100] = 4 >= 4 * ask[101] = 4 -> sell imbalance; ask[101] = 1 < 4 * bid[100]
    assert (s.buy_imbalances, s.sell_imbalances) == (0, 1)
    below = [tr(0, 100, 3.99, "sell"), tr(1, 101, 1, "buy")]
    (s2,) = of.footprints(below, "X/USDT", TICK)
    assert s2.sell_imbalances == 0


def test_zero_opposite_counts_only_when_enabled():
    trades = [tr(0, 100, 1, "buy"), tr(1, 101, 5, "buy")]  # bid[100] = 0
    (s,) = of.footprints(trades, "X/USDT", TICK)
    assert s.buy_imbalances == 0
    p = FootprintParams(bin_ticks=1, tick_size=1.0, count_zero_opposite=True)
    (s2,) = of.footprints(trades, "X/USDT", p)
    assert s2.buy_imbalances == 1


def test_relative_bins():
    trades = [tr(0, 20_000, 1, "buy"), tr(1, 20_009, 1, "sell"), tr(2, 20_011, 3, "buy")]
    (s,) = of.footprints(trades, "BTC/USDT", FootprintParams(bin_pct=0.05))
    assert s.bin_size == pytest.approx(10.0)  # 0.05 % of the 20,000 open
    assert s.levels_n == 2 and s.poc_price == pytest.approx(20_010.0)


def test_absorption_true_false_and_mirror():
    (bull,) = of.footprints(candle_bull(0, 109), "X/USDT", TICK)
    assert bull.bull_absorption and not bull.bear_absorption  # closes 109 > mid 105
    (no,) = of.footprints(candle_bull(0, 101), "X/USDT", TICK)
    assert not no.bull_absorption  # same selling, but closes in the lower half
    bear_trades = [
        tr(0, 100, 1, "sell"),
        tr(1, 110, 10, "buy"),
        tr(2, 106, 2, "sell"),
        tr(3, 101, 1, "sell"),
    ]
    (bear,) = of.footprints(bear_trades, "X/USDT", TICK)
    assert bear.bear_absorption and not bear.bull_absorption
    # selling in the lower third that is not heavy (< 30 % of volume) is no absorption
    light = [tr(0, 110, 20, "buy"), tr(1, 100, 2, "sell"), tr(2, 109, 1, "buy")]
    (lt,) = of.footprints(light, "X/USDT", TICK)
    assert not lt.bull_absorption


def test_epoch_alignment_and_cumulative_delta():
    trades = candle_a() + candle_bull(1, 109) + [tr(TF - 1, 100, 1, "sell", 1)]
    a, b = of.footprints(trades, "X/USDT", TICK)
    assert (a.ts, b.ts) == (T0, T0 + TF)
    assert b.delta == pytest.approx(4 - 11)
    assert b.cum_delta == pytest.approx(8 + 4 - 11)


def test_summary_json_roundtrip():
    (s,) = of.footprints(candle_a(), "BTC/USDT", TICK)
    d = json.loads(json.dumps(s.to_dict(include_levels=True)))
    assert FootprintSummary.from_dict(d) == s
    assert "levels" not in s.to_dict()


def test_trade_side_validated():
    with pytest.raises(ValueError):
        FlowTrade(T0, 1.0, 1.0, "long")


# ---------------------------------------------------------------------- Binance zips
def make_zip(rows: list[list[str]], header: bool) -> bytes:
    lines = []
    if header:
        lines.append(
            "agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,"
            "is_buyer_maker,is_best_match"
        )
    lines += [",".join(r) for r in rows]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("BTCUSDT-aggTrades-2024-01.csv", "\n".join(lines) + "\n")
    return buf.getvalue()


def agg_rows(us: bool = False) -> list[list[str]]:
    mult = 1000 if us else 1
    out = []
    for i, t in enumerate(candle_a()):
        maker = "True" if t.taker_side == "sell" else "False"  # buyer maker -> taker SOLD
        out.append(
            [str(i + 1), f"{t.price}", f"{t.qty}", "1", "1", str(t.ts * mult), maker, "True"]
        )
    return out


@pytest.mark.parametrize(("header", "us"), [(True, False), (False, False), (False, True)])
def test_zip_parse_header_and_microseconds(tmp_path, header, us):
    path = tmp_path / "a.zip"
    path.write_bytes(make_zip(agg_rows(us), header))
    trades = list(of.iter_aggtrades_zip(path))
    assert [(t.ts, t.price, t.qty, t.taker_side) for t in trades] == [
        (t.ts, t.price, t.qty, t.taker_side) for t in candle_a()
    ]
    (s,) = of.zip_footprints(path, "BTC/USDT", TICK)
    assert s.delta == 8 and s.source == of.SOURCE_BINANCE


def test_is_buyer_maker_semantics():
    rows = ["1,100.0,2.0,1,1,1704067200000,true,true", "2,100.0,3.0,2,2,1704067200001,false,true"]
    a, b = of.iter_aggtrades_csv(rows)
    assert a.taker_side == "sell" and b.taker_side == "buy"


def test_urls_and_download_cache_and_checksum(tmp_path):
    assert of.aggtrades_url("BTC/USDT", "2024-01") == (
        "https://data.binance.vision/data/spot/monthly/aggTrades/BTCUSDT/"
        "BTCUSDT-aggTrades-2024-01.zip"
    )
    assert "/daily/aggTrades/ETHUSDT/ETHUSDT-aggTrades-2024-02-03.zip" in of.aggtrades_url(
        "ETH/USDT", "2024-02-03"
    )
    body = make_zip(agg_rows(), True)
    calls: list[str] = []

    def fetch(url: str) -> bytes:
        calls.append(url)
        if url.endswith(".CHECKSUM"):
            return f"{hashlib.sha256(body).hexdigest()}  x.zip\n".encode()
        return body

    p = of.download_aggtrades("BTC/USDT", "2024-01", tmp_path, fetch)
    assert p.read_bytes() == body and len(calls) == 2
    of.download_aggtrades("BTC/USDT", "2024-01", tmp_path, fetch)
    assert len(calls) == 2  # cached
    with pytest.raises(ValueError, match="sha256"):
        of.download_aggtrades(
            "ETH/USDT", "2024-01", tmp_path, lambda u: b"x" if "CHECK" in u else body
        )


# ---------------------------------------------------------------------- ccxt paging
class FakeTradeExchange:
    """ccxt surface: fetch_trades(symbol, since, limit) with a Binance-like 1h window."""

    def __init__(self, rows, now, window=HOUR_MS, overlap=0, with_ids=True):
        self.rows = sorted(rows, key=lambda r: r["timestamp"])
        self.now, self.window, self.overlap = now, window, overlap
        self.with_ids = with_ids
        self.calls: list[tuple[str, int, int]] = []

    def milliseconds(self):
        return self.now

    def fetch_trades(self, symbol, since=None, limit=None):
        self.calls.append((symbol, since, limit))
        vis = [r for r in self.rows if r["timestamp"] < self.now]
        first = next((k for k, r in enumerate(vis) if r["timestamp"] >= since), len(vis))
        first = max(0, first - self.overlap)
        page = [
            r for r in vis[first:] if self.window is None or r["timestamp"] < since + self.window
        ]
        page = page[:limit]
        return [
            dict(r) if self.with_ids else {k: v for k, v in r.items() if k != "id"} for r in page
        ]


def ccxt_rows(trades: list[FlowTrade]) -> list[dict]:
    return [
        {"id": str(i), "timestamp": t.ts, "price": t.price, "amount": t.qty, "side": t.taker_side}
        for i, t in enumerate(trades)
    ]


def spread_trades(n: int, step: int, start: int = T0) -> list[FlowTrade]:
    return [
        FlowTrade(start + i * step, 100.0 + i % 3, 1.0, "buy" if i % 2 else "sell")
        for i in range(n)
    ]


@pytest.mark.parametrize("with_ids", [True, False])
def test_fetch_trades_pagination_and_dedupe(with_ids):
    trades = spread_trades(500, 60_000)  # one per minute, ~8.3h, gaps none
    trades += [FlowTrade(T0 + 60_000, 101.0, 0.5, "sell")]  # same-ms neighbour
    ex = FakeTradeExchange(ccxt_rows(trades), now=T0 + 3 * TF, overlap=3, with_ids=with_ids)
    got = of.fetch_trades(ex, "BTC/USDT", T0, limit=25)
    assert len(got) == len(trades)
    assert [t.ts for t in got] == sorted(t.ts for t in got)
    assert len(ex.calls) > 20  # paged


def test_fetch_trades_steps_over_quiet_hours_and_respects_until():
    trades = spread_trades(5, 1000) + spread_trades(5, 1000, start=T0 + 5 * HOUR_MS)
    ex = FakeTradeExchange(ccxt_rows(trades), now=T0 + 2 * TF)
    got = of.fetch_trades(ex, "BTC/USDT", T0, T0 + 6 * HOUR_MS)
    assert len(got) == 10
    got2 = of.fetch_trades(ex, "BTC/USDT", T0, T0 + 5 * HOUR_MS)
    assert len(got2) == 5


def test_fetch_trades_same_ms_full_page_makes_progress():
    trades = [FlowTrade(T0, 100.0, 1.0, "buy") for _ in range(10)] + spread_trades(3, 5, T0 + 1)
    ex = FakeTradeExchange(ccxt_rows(trades), now=T0 + TF, window=None)
    got = of.fetch_trades(ex, "BTC/USDT", T0, T0 + 100, limit=5)
    assert len(got) >= 8  # no infinite loop; trades beyond the page of one ms may be lost


# ---------------------------------------------------------------------- store
def test_store_merge_idempotent_and_cum_delta(tmp_path):
    a, b = of.footprints(candle_a() + candle_bull(1, 109), "BTC/USDT", TICK)
    r1 = of.merge_store(tmp_path, "BTC/USDT", [b])
    assert r1 == {"added": 1, "replaced": 0, "stored": 1}
    path = of.store_path(tmp_path, "BTC/USDT")
    assert path.name == "BTC_USDT-4h.csv" and path.parent.name == "orderflow"
    r2 = of.merge_store(tmp_path, "BTC/USDT", [a, b])
    assert r2["added"] == 1 and r2["replaced"] == 0
    text = path.read_text()
    mtime = path.stat().st_mtime_ns
    r3 = of.merge_store(tmp_path, "BTC/USDT", [a, b])
    assert r3 == {"added": 0, "replaced": 0, "stored": 2}
    assert path.read_text() == text and path.stat().st_mtime_ns == mtime
    stored = of.load_store(tmp_path, "BTC/USDT")
    assert stored[T0].cum_delta == pytest.approx(8.0)
    assert stored[T0 + TF].cum_delta == pytest.approx(8.0 - 6.0)
    assert stored[T0].levels is None and stored[T0] == a.__class__(
        **{**a.to_dict(), "levels": None}
    )
    src = of.load_sources(tmp_path)
    assert set(src) == {"orderflow"} and set(src["orderflow"]["BTC/USDT"]) == {T0, T0 + TF}


# ---------------------------------------------------------------------- collect
def test_collect_with_fakes(tmp_path):
    now = T0 + 40 * TF + 5_000  # Jan 7th 16:00 + 5s, inside the open candle
    body = make_zip(agg_rows(), False)  # the January file holds candle T0
    fetched: list[str] = []

    def fetch(url: str) -> bytes:
        fetched.append(url)
        if url.endswith(".CHECKSUM"):
            raise OSError("404")
        return body

    live = spread_trades(3 * 240, 60_000, start=now - 3 * TF)  # up to and incl. open candle
    ex = FakeTradeExchange(ccxt_rows(live), now=now)
    settings = {
        "pairs": ["BTC/USDT"],
        "orderflow": {
            "history_months": ["2024-01"],
            "bin_ticks": 1,
            "tick_size": 1.0,
            "max_live_candles": 2,
        },
    }
    res = of.collect(tmp_path, settings, exchange=ex, fetch=fetch, now_ms=now)
    pr = res["pairs"]["BTC/USDT"]
    assert pr["history"]["files"] == 1 and pr["history"]["added"] == 1
    # live is capped at the last 2 closed candles (older gaps need history); open one excluded
    assert pr["live"]["added"] == 2
    stored = of.load_store(tmp_path, "BTC/USDT")
    open_candle = of.candle_ts(now)
    assert set(stored) == {T0, open_candle - 2 * TF, open_candle - TF}
    assert stored[T0].source == of.SOURCE_BINANCE and stored[open_candle - TF].source == "live"
    assert not list((tmp_path / "orderflow" / "cache").glob("*.zip"))  # zips removed

    n_fetch = len(fetched)
    ex.calls.clear()
    res2 = of.collect(tmp_path, settings, exchange=ex, fetch=fetch, now_ms=now)
    assert len(fetched) == n_fetch  # month already processed
    assert res2["pairs"]["BTC/USDT"]["live"]["added"] == 0 and ex.calls == []
    assert of.load_store(tmp_path, "BTC/USDT") == stored


def test_collect_live_failure_is_reported(tmp_path):
    class Broken:
        def milliseconds(self):
            return T0 + 10 * TF

        def fetch_trades(self, *a, **k):
            raise RuntimeError("down")

    res = of.collect(tmp_path, {"pairs": ["BTC/USDT", "ETH/USDT"]}, exchange=Broken())
    assert "down" in res["pairs"]["ETH/USDT"]["live"]["error"]
    assert res["pairs"]["BTC/USDT"]["stored"] == 0


def test_history_periods():
    now = 1_709_856_000_000  # 2024-03-08
    assert of.history_periods(now, 2, 0) == ["2024-01", "2024-02"]
    assert of.history_periods(now, 0, 2) == ["2024-03-06", "2024-03-07"]


# ---------------------------------------------------------------------- layer
def fp(pair: str, ts: int, delta_pct: float, buy_imb: int = 1, bear: bool = False):
    vol = 100.0
    delta = delta_pct
    return FootprintSummary(
        pair,
        ts,
        1.0,
        1.0,
        1.0,
        1.0,
        vol,
        (vol + delta) / 2,
        (vol - delta) / 2,
        delta,
        delta_pct,
        0.0,
        10,
        0.01,
        1,
        1.0,
        vol,
        buy_imb,
        0,
        False,
        bear,
        "test",
    )


def cand(pair: str, i: int, r: float) -> CandidateOutcome:
    ts = T0 + i * TF
    return CandidateOutcome(pair, ts, ts + TF, ts + 3 * TF, "TP" if r > 0 else "SL", r, {})


def make_ctx(cands, flow, until):
    return TrainContext(StrategyConfig(), MarketView({}, TF), until, cands, [], {"orderflow": flow})


def train_world(n=40):
    """Negative-delta signals lose (-1R), positive ones win (+1R)."""
    cands, flow = [], {"BTC/USDT": {}}
    for i in range(n):
        d = -20.0 if i % 2 else 5.0
        c = cand("BTC/USDT", i * 5, -1.0 if d < 0 else 1.0)
        cands.append(c)
        flow["BTC/USDT"][c.signal_ts] = fp("BTC/USDT", c.signal_ts, d)
    return cands, flow, T0 + n * 5 * TF + 10 * TF


def test_registered():
    assert LAYER_TYPES["orderflow"] is OrderFlowLayer and OrderFlowLayer.kind == "orderflow"


def test_fit_picks_best_preregistered_rule_on_train_only():
    cands, flow, until = train_world()
    layer = OrderFlowLayer()
    ctx = make_ctx(cands, flow, until)
    assert layer.ready(ctx)[0]
    layer.fit(ctx)
    assert layer.rule == "delta_lt_0"
    # later signals (exit after until) with the opposite relation must not change the fit
    late = []
    for i in range(200):
        c = cand("BTC/USDT", 1000 + i, 1.0)
        late.append(c)
        flow["BTC/USDT"][c.signal_ts] = fp("BTC/USDT", c.signal_ts, -50.0, buy_imb=0, bear=True)
    layer2 = OrderFlowLayer()
    layer2.fit(make_ctx(cands + late, flow, until))
    assert layer2.rule == "delta_lt_0"
    assert layer2.fit_report["with_footprint"] == len(cands)


def test_fit_ties_prefer_no_veto():
    cands, flow, until = train_world()
    cands = [
        CandidateOutcome(c.pair, c.signal_ts, c.entry_ts, c.exit_ts, "TP", 1.0, {}) for c in cands
    ]
    layer = OrderFlowLayer()
    layer.fit(make_ctx(cands, flow, until))
    assert layer.rule == "none"


def row_at(ts: int) -> FeatureRow:
    return FeatureRow(ts, ts + TF, 1.0, None, None, None, None, None, None, None, None, 0)


def test_veto_at_decision_time_and_state_roundtrip(tmp_path):
    cands, flow, until = train_world()
    layer = OrderFlowLayer()
    layer.fit(make_ctx(cands, flow, until))
    view = MarketView({}, TF)
    bad, good = T0 + 5 * TF, T0
    assert layer.veto("BTC/USDT", row_at(bad), view)[0]
    assert not layer.veto("BTC/USDT", row_at(good), view)[0]
    assert layer.veto("BTC/USDT", row_at(T0 + 3 * TF), view) == (
        False,
        "no footprint for the signal candle (not vetoed)",
    )
    # a live layer restored from JSON reads the store in its state dir lazily
    of.merge_store(tmp_path, "BTC/USDT", [flow["BTC/USDT"][bad], flow["BTC/USDT"][good]])
    st = json.loads(json.dumps({**layer.state(), "state_dir": str(tmp_path)}))
    live = OrderFlowLayer()
    live.load_state(st)
    assert live.rule == "delta_lt_0"
    assert live.veto("BTC/USDT", row_at(bad), view)[0]
    assert not live.veto("BTC/USDT", row_at(good), view)[0]


def test_ready_reports_coverage():
    cands, flow, until = train_world()
    half = {"BTC/USDT": dict(list(flow["BTC/USDT"].items())[:20])}
    ok, why = OrderFlowLayer().ready(make_ctx(cands, half, until))
    assert not ok and "20 of 40" in why and "50%" in why and "Binance aggTrades" in why


def synthetic_flow(data):
    """A footprint for every candle: delta % follows the candle body (a toy relation)."""
    flow = {}
    for pair, candles in data.items():
        flow[pair] = {
            c.ts: fp(pair, c.ts, max(-100.0, min(100.0, (c.close - c.open) / c.open * 5000)))
            for c in candles
        }
    return flow


def test_validate_layer_collecting_then_valid():
    cfg = StrategyConfig()
    data, events = make_world("planted", seed=3, years=1.5)
    empty = validate_layer(OrderFlowLayer, data, cfg, events=events, sources={})
    assert empty["status"] == "collecting" and "footprints cover 0 of" in empty["reason"]
    assert validate_layer(OrderFlowLayer, {}, cfg)["status"] == "collecting"
    res = validate_layer(
        OrderFlowLayer, data, cfg, events=events, sources={"orderflow": synthetic_flow(data)}
    )
    assert res["status"] in ("active", "rejected"), res
    assert "test_layer_n" in res
