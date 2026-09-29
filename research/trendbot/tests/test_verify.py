"""Verification agents: each one catches the fault it exists for, and the guard blocks entries."""

import dataclasses
import json

import pytest

from research.trendbot import verify as v
from research.trendbot.config import StrategyConfig
from research.trendbot.data import save_candles_csv
from research.trendbot.synthetic import make_world


CFG = StrategyConfig()


@pytest.fixture(scope="module")
def data():
    d, _ = make_world("null", seed=5, years=1)
    return {p: cs[:1500] for p, cs in d.items()}


def _state(tmp_path, data, **override):
    for pair, cs in {**data, **override}.items():
        save_candles_csv(cs, tmp_path / "candles" / f"{pair.replace('/', '_')}-4h.csv")
    return tmp_path


def _now(data):
    return data["BTC/USDT"][-1].ts + CFG.timeframe_ms + 60_000


def test_clean_state_passes_every_offline_agent(tmp_path, data):
    st = v.run_all(_state(tmp_path, data), CFG, now_ms=_now(data))
    by = {a["agent"]: a for a in st["agents"]}
    for agent in ("data", "indicator", "chart", "ledger"):
        assert by[agent]["status"] == "pass", by[agent]
    assert by["exchange"]["status"] == "skip" and by["audit"]["status"] == "skip"
    assert st["blocked_pairs"] == [] and st["overall"] == "pass"
    assert json.loads((tmp_path / "verify_status.json").read_text())["overall"] == "pass"


def test_a_broken_candle_fails_the_data_agent_and_blocks_entries(tmp_path, data):
    eth = list(data["ETH/USDT"])
    c = eth[-3]
    eth[-3] = dataclasses.replace(c, high=c.low * 0.9)  # high below low
    d = _state(tmp_path, data, **{"ETH/USDT": eth})
    st = v.run_all(d, CFG, now_ms=_now(data))
    assert st["blocked_pairs"] == ["ETH/USDT"]
    assert "corrupt" in st["agents"][0]["pairs"]["ETH/USDT"]["detail"]
    assert "data agent" in v.blocked_pairs(d, now_ms=_now(data))["ETH/USDT"]
    assert (
        v.blocked_pairs(d, now_ms=_now(data) + 7 * 3_600_000) == {}
    )  # a stale report blocks nothing


def test_a_gap_is_found(tmp_path, data):
    btc = list(data["BTC/USDT"])
    del btc[700]
    st = v.run_all(_state(tmp_path, data, **{"BTC/USDT": btc}), CFG, now_ms=_now(data))
    assert "gap or duplicate" in st["agents"][0]["pairs"]["BTC/USDT"]["detail"]


def test_exchange_agent_compares_with_a_fresh_download(tmp_path, data):
    def fetch(pair, n):
        cs = list(data[pair][-n:])
        if pair == "BNB/USDT":
            cs[-1] = dataclasses.replace(cs[-1], close=cs[-1].close * 1.01)
        return cs

    st = v.run_all(_state(tmp_path, data), CFG, fetch=fetch, now_ms=_now(data))
    ex = next(a for a in st["agents"] if a["agent"] == "exchange")
    assert ex["pairs"]["BTC/USDT"]["status"] == "pass"
    assert ex["pairs"]["BNB/USDT"]["status"] == "fail" and st["blocked_pairs"] == ["BNB/USDT"]


def test_indicator_agent_catches_values_the_bot_computed_wrong(data, monkeypatch):
    from research.trendbot import indicators

    real = indicators.rsi_wilder
    monkeypatch.setattr(
        indicators,
        "rsi_wilder",
        lambda xs, n=14: [None if r is None else r + 0.5 for r in real(xs, n)],
    )
    res = v.indicator_agent({"BTC/USDT": data["BTC/USDT"]}, CFG)
    assert res["status"] == "fail" and "rsi" in res["pairs"]["BTC/USDT"]["detail"]


def test_price_agent_flags_a_bad_tick():
    live = {"prices": {"BTC/USDT": {"last": 100.0}, "ETH/USDT": {"last": 50.0}}}
    res = v.price_agent(live, {"BTC/USDT": 100.2, "ETH/USDT": 45.0}.get)
    assert (
        res["pairs"]["BTC/USDT"]["status"] == "pass"
        and res["pairs"]["ETH/USDT"]["status"] == "fail"
    )


def test_independent_indicators_match_the_library(data):
    from research.trendbot.indicators import ema, rsi_wilder

    closes = [c.close for c in data["BTC/USDT"][:400]]
    for a, b in zip(v.ind_ema(closes, 21), ema(closes, 21), strict=True):
        assert (a is None and b is None) or a == pytest.approx(b, rel=1e-9)
    for a, b in zip(v.ind_rsi(closes), rsi_wilder(closes), strict=True):
        assert (a is None and b is None) or a == pytest.approx(b, rel=1e-9)


def test_brain_blocks_a_pair_the_agents_failed(tmp_path, data):
    from research.trendbot.chantisimo import BrainSettings, Chantisimo
    from research.trendbot.indicators import compute_features
    from research.trendbot.models import SignalCheck

    eth = list(data["ETH/USDT"])
    eth[-2] = dataclasses.replace(eth[-2], volume=-1.0)
    d = _state(tmp_path, data, **{"ETH/USDT": eth})
    v.run_all(d, CFG, now_ms=_now(data))
    brain = Chantisimo(d, BrainSettings(use_backtest=False), "paper")
    f = brain.filter(lambda pair, row, check: (True, None, "fine"))
    row = compute_features(data["ETH/USDT"], CFG)[-1]
    ok, _, why = f("ETH/USDT", row, SignalCheck("ETH/USDT", row.ts, ()))
    assert not ok and f.rule_id == "X_data_check" and "data check failed" in why
    ok, _, _ = f("BTC/USDT", row, SignalCheck("BTC/USDT", row.ts, ()))
    assert ok
