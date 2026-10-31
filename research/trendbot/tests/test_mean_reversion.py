"""Mean reversion: buys a statistical stretch in a range, exits at the mean, stays out of
trends, and never looks ahead."""

import math
import random

import pytest

from research.trendbot import lab
from research.trendbot.indicators import bollinger
from research.trendbot.models import HOUR_MS, Candle
from research.trendbot.strategies import STRATEGIES, MeanReversion, atr, backtest
from research.trendbot.synthetic import make_world


H4 = 4 * HOUR_MS
DROP = 300


def ranging(n=400, seed=2, drift=0.0, drop=True):
    """A mean-reverting (AR(1)) market around 100; at bar DROP one 6% drop, then a rebound."""
    rng = random.Random(seed)
    out, x, base = [], 0.0, 100.0
    for t in range(n):
        x = 0.85 * x + rng.gauss(0, 0.006)
        base *= math.exp(drift)
        c = base * math.exp(x)
        if drop and t == DROP:
            c = out[-1].close * 0.94
        elif drop and DROP < t < DROP + 6:
            c = out[-1].close * 1.012
        o = out[-1].close if out else c
        out.append(Candle(t * H4, o, max(o, c) * 1.002, min(o, c) * 0.998, c, 1000.0))
    return out


def test_buys_the_stretch_and_exits_at_the_mean():
    assert STRATEGIES["mean_reversion"] is MeanReversion
    cs = ranging()
    sig = MeanReversion().signals(cs)
    assert [s.i for s in sig] == [DROP]
    s = sig[0]
    closes = [c.close for c in cs]
    mid, _, lower = bollinger(closes, 20, 2.0)
    a = atr(cs, 14)[DROP]
    assert closes[DROP] < lower[DROP]
    assert s.entry == "next_open" and s.target == pytest.approx(mid[DROP])
    assert s.stop == pytest.approx(closes[DROP] - a) and s.stop_atr == pytest.approx(a)
    assert "lower band" in s.reason and "target the mean" in s.reason
    t = backtest(cs, MeanReversion(), fee=0.0, slip_pct=0.0).trades[0]
    assert t.exits[-1][3] == "TP" and t.exit == pytest.approx(mid[DROP]) and t.r > 2
    wide = MeanReversion(stop_atr=3.0).signals(cs)  # the mean is closer than 2R: skipped
    assert not wide


def test_stays_out_of_trends_and_calm_markets():
    assert not MeanReversion().signals(ranging(drift=-0.004))  # a downtrend: ADX far above 20
    assert not MeanReversion().signals(ranging(drop=False))  # nothing stretched enough
    slope = MeanReversion(trend_filter="ema_slope").signals(ranging())
    assert [s.i for s in slope] == [DROP]  # the flat-EMA200 filter agrees in a range
    with pytest.raises(ValueError, match="trend_filter"):
        MeanReversion(trend_filter="moon").signals(ranging())


def test_z_score_entry_without_rsi():
    cs = ranging()
    assert not MeanReversion(rsi_max=5.0).signals(cs)  # RSI 24: not oversold enough
    z_only = [s.i for s in MeanReversion(rsi_max=5.0, z_entry=-2.5).signals(cs)]
    assert DROP in z_only  # z-score alone qualifies (and catches smaller stretches too)


def test_mean_reversion_never_looks_ahead():
    data, _ = make_world("planted", seed=3, years=3)
    for p in ({}, {"trend_filter": "both", "z_entry": -2.5}):
        ok, why = lab.lookahead_check(MeanReversion(**p), data["BTC/USDT"])
        assert ok, why
    ok, why = lab.lookahead_check(MeanReversion(), ranging())
    assert ok, why
