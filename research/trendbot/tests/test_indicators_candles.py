"""Candle indicators of the confluence / mean-reversion strategies against hand-computed
values, and their causality (truncating the input never changes an earlier value)."""

import math
import random

import pytest

from research.trendbot.indicators import (
    adx,
    bollinger,
    ichimoku,
    obv,
    parabolic_sar,
    rolling_std,
    vwap_rolling,
    vwap_session,
    zscore,
)
from research.trendbot.models import HOUR_MS, Candle
from research.trendbot.strategies import atr, macd, pivots


DAY = 24 * HOUR_MS


def _hl(rows):
    """(high, low[, close]) rows -> candles one hour apart (close = midpoint by default)."""
    out = []
    for k, r in enumerate(rows):
        h, lo = r[0], r[1]
        c = r[2] if len(r) > 2 else (h + lo) / 2
        out.append(Candle(k * HOUR_MS, c, h, lo, c, 1.0))
    return out


def test_obv_adds_up_volume_and_subtracts_down_volume():
    closes, vols = [10, 11, 11, 9, 12], [5, 3, 4, 2, 6]
    cs = [Candle(k, c, c, c, c, v) for k, (c, v) in enumerate(zip(closes, vols, strict=True))]
    assert obv(cs) == [0, 3, 3, 1, 7]


def test_vwap_session_resets_each_utc_day_and_rolling_window():
    cs = [
        Candle(0, 10, 10, 10, 10, 1.0),
        Candle(HOUR_MS, 20, 20, 20, 20, 3.0),
        Candle(DAY, 30, 30, 30, 30, 2.0),  # a new UTC day
    ]
    assert vwap_session(cs) == [10, pytest.approx(17.5), 30]
    assert vwap_rolling(cs, 2) == [None, pytest.approx(17.5), pytest.approx(24.0)]
    typical = Candle(0, 0, 12, 6, 9, 1.0)  # (12 + 6 + 9) / 3
    assert vwap_session([typical]) == [9.0]


def test_ichimoku_lines_and_the_forward_shifted_cloud():
    cs = _hl([(10, 8), (12, 9), (11, 7), (14, 10), (13, 11), (15, 12)])
    ic = ichimoku(cs, tenkan=2, kijun=3, senkou=4, shift=2)
    assert ic["tenkan"] == [None, 10, 9.5, 10.5, 12, 13]
    assert ic["kijun"] == [None, None, 9.5, 10.5, 10.5, 12.5]
    # span A computed at i-2 = (tenkan + kijun) / 2; span B = the 4-bar midpoint at i-2
    assert ic["span_a"] == [None, None, None, None, 9.5, 10.5]
    assert ic["span_b"] == [None, None, None, None, None, 10.5]


def test_parabolic_sar_by_hand():
    cs = _hl([(10, 9), (11, 10), (12, 11), (11.5, 8), (10, 7.5)])
    sar, trend = parabolic_sar(cs, 0.02, 0.2)
    # bar 1: 9 + .02*(10-9) = 9.02 -> clamped to the prior lows (9); bar 2: 9 + .04*(11-9)
    # = 9.08 -> 9; bar 3: 9 + .06*(12-9) = 9.18, low 8 breaks it: flip to the EP 12;
    # bar 4: 12 + .02*(8-12) = 11.92 -> clamped to the prior highs (12)
    assert sar == [9, 9, 9, pytest.approx(12), pytest.approx(12)]
    assert trend == [1, 1, 1, -1, -1]


def test_atr_macd_and_swing_points():
    cs = [
        Candle(0, 9, 10, 8, 9, 1),
        Candle(1, 9, 11, 9, 10, 1),
        Candle(2, 10, 12, 10, 11, 1),
        Candle(3, 11, 11, 8, 9, 1),
    ]
    # TR 2, 2, 2, 3 (|8 - 11|); Wilder n=2: seed (2+2)/2, then (2+2)/2, (2+3)/2
    assert atr(cs, 2) == [None, 2.0, 2.0, 2.5]
    line, sig = macd([float(x) for x in range(1, 60)])
    assert line[24] is None and line[25] == pytest.approx(7.0)  # EMA12 - EMA26 of a ramp
    assert sig[33] == pytest.approx(7.0) and sig[32] is None
    zig = _hl([(5, 4), (6, 5), (9, 8), (6, 5), (5, 3), (6, 5), (7, 6)])
    highs, lows = pivots(zig, 1, 1)
    assert highs == [(2, 9)] and lows == [(4, 3)]


def test_bollinger_zscore_and_adx():
    closes = [1.0, 2.0, 3.0, 4.0, 5.0]
    mid, up, lo = bollinger(closes, 5, 2.0)
    assert mid[-1] == 3.0 and up[-1] == pytest.approx(3 + 2 * math.sqrt(2))
    assert lo[-1] == pytest.approx(3 - 2 * math.sqrt(2)) and mid[3] is None
    assert zscore(closes, 5)[-1] == pytest.approx(2 / math.sqrt(2))
    assert rolling_std([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0], 8)[-1] == pytest.approx(2.0)
    rising = [Candle(k, 10 + k, 11 + k, 9 + k, 10.5 + k, 1) for k in range(30)]
    a = adx(rising, 5)
    assert a[8] is None and a[9] == pytest.approx(100.0) and a[-1] == pytest.approx(100.0)
    chop = [Candle(k, 10, 11 + (k % 2), 9 - (k % 2), 10, 1) for k in range(60)]
    assert adx(chop, 5)[-1] < 20  # outside bars both ways: no direction


def test_every_candle_indicator_is_causal():
    rng = random.Random(4)
    px, cs = 100.0, []
    for k in range(300):
        o = px
        px *= math.exp(rng.gauss(0, 0.01))
        cs.append(
            Candle(
                k * HOUR_MS,
                o,
                max(o, px) * (1 + rng.random() / 200),
                min(o, px) * (1 - rng.random() / 200),
                px,
                100 + rng.random() * 50,
            )
        )
    full = {
        "obv": obv(cs),
        "vwap_s": vwap_session(cs),
        "vwap_r": vwap_rolling(cs, 24),
        "ichi": list(zip(*ichimoku(cs).values(), strict=True)),
        "psar": list(zip(*parabolic_sar(cs), strict=True)),
        "adx": adx(cs),
        "bb": list(zip(*bollinger([c.close for c in cs]), strict=True)),
    }
    for cut in (60, 151, 299):
        part = cs[:cut]
        short = {
            "obv": obv(part),
            "vwap_s": vwap_session(part),
            "vwap_r": vwap_rolling(part, 24),
            "ichi": list(zip(*ichimoku(part).values(), strict=True)),
            "psar": list(zip(*parabolic_sar(part), strict=True)),
            "adx": adx(part),
            "bb": list(zip(*bollinger([c.close for c in part]), strict=True)),
        }
        for k, v in short.items():
            assert v == full[k][:cut], k
