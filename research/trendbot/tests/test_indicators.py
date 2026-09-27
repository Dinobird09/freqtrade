import random
from datetime import UTC, datetime

import pytest

from research.trendbot.config import StrategyConfig
from research.trendbot.indicators import compute_features, ema, prev_mean, rsi_wilder
from research.trendbot.models import HOUR_MS, Candle


CFG = StrategyConfig()
T0 = 1_704_067_200_000  # 2024-01-01 00:00 UTC
TF = 4 * HOUR_MS

# StockCharts "RSI" ChartSchool worksheet (Wilder, 14 periods): closes and published RSI.
WILDER_CLOSES = [
    44.3389, 44.0902, 44.1497, 43.6124, 44.2778, 44.8264, 45.0955, 45.4245, 45.8433,
    46.0826, 45.8931, 46.0328, 45.6140, 46.2820, 46.2820, 46.0028, 46.0328, 46.4116,
    46.2222, 45.6439, 46.2122, 46.2521, 45.7137, 46.4515, 45.7835, 45.3548, 44.0288,
    44.1783, 44.2181, 44.5672, 43.4205, 42.6628, 43.1314,
]  # fmt: skip
WILDER_RSI_PUBLISHED = [
    70.53, 66.32, 66.55, 69.41, 66.36, 57.97, 62.93, 63.26, 56.06, 62.38,
    54.71, 50.42, 39.99, 41.46, 41.87, 45.46, 37.30, 33.08, 37.77,
]  # fmt: skip


def _candles(n: int, seed: int = 11) -> list[Candle]:
    rng = random.Random(seed)
    out, price = [], 100.0
    for t in range(n):
        opn = price
        price = max(1.0, price * (1.0 + rng.gauss(0.0005, 0.02)))
        high = max(opn, price) * (1.0 + rng.random() * 0.01)
        low = min(opn, price) * (1.0 - rng.random() * 0.01)
        vol = 1000.0 * (0.5 + rng.random())
        out.append(Candle(T0 + t * TF, opn, high, low, price, vol))
    return out


# ------------------------------------------------------------------------------ ema
def test_ema_hand_computed_period_4():
    # seed = (2+4+6+8)/4 = 5 at index 3; alpha = 2/5 = 0.4
    # idx4: 0.4*10 + 0.6*5 = 7 ; idx5: 0.4*12 + 0.6*7 = 9
    assert ema([2, 4, 6, 8, 10, 12], 4) == pytest.approx([None, None, None, 5.0, 7.0, 9.0])


def test_ema_hand_computed_period_3():
    # seed = 11 at index 2; alpha = 0.5
    # idx3: .5*11 + .5*11 = 11 ; idx4: .5*10 + .5*11 = 10.5 ; idx5: .5*13 + .5*10.5 = 11.75
    out = ema([10, 11, 12, 11, 10, 13], 3)
    assert out[:2] == [None, None]
    assert out[2:] == pytest.approx([11.0, 11.0, 10.5, 11.75])


def test_ema_short_series_and_edges():
    assert ema([1.0, 2.0], 3) == [None, None]
    assert ema([], 3) == []
    assert ema([1.0, 2.0, 6.0], 3) == [None, None, 3.0]  # exactly period -> only the seed
    assert ema([3.0, 1.0, 4.0], 1) == [3.0, 1.0, 4.0]  # alpha = 1
    assert ema([7.0] * 30, 9)[8:] == pytest.approx([7.0] * 22)
    with pytest.raises(ValueError):
        ema([1.0, 2.0], 0)


# ------------------------------------------------------------------------------ rsi
def test_rsi_matches_published_wilder_worksheet():
    out = rsi_wilder(WILDER_CLOSES, 14)
    assert out[:14] == [None] * 14
    assert out[14:] == pytest.approx(WILDER_RSI_PUBLISHED, abs=0.006)


def test_rsi_hand_computed_period_3():
    # changes: +1, -1, +2, +1, -1
    # idx3: avg_gain = 3/3 = 1, avg_loss = 1/3 -> RS 3 -> RSI 75
    # idx4: gain (1*2+1)/3 = 1, loss (1/3*2+0)/3 = 2/9 -> RS 4.5 -> 100*4.5/5.5 = 900/11
    # idx5: gain (1*2+0)/3 = 2/3, loss (2/9*2+1)/3 = 13/27 -> RS 18/13 -> 1800/31
    out = rsi_wilder([10, 11, 10, 12, 13, 12], 3)
    assert out[:3] == [None, None, None]
    assert out[3:] == pytest.approx([75.0, 900 / 11, 1800 / 31])


def test_rsi_special_cases():
    assert rsi_wilder([1, 2, 3, 4, 5, 6], 3)[3:] == [100.0, 100.0, 100.0]  # no losses
    assert rsi_wilder([5, 5, 5, 5, 5], 3)[3:] == [50.0, 50.0]  # no movement at all
    assert rsi_wilder([6, 5, 4, 3, 2], 3)[3:] == [0.0, 0.0]  # no gains
    # Gains decay but stay > 0 while losses stay 0 -> still 100.
    assert rsi_wilder([1, 2, 3, 4, 4, 4, 4], 3)[3:] == [100.0] * 4
    # Equal average gain and loss -> exactly 50.
    assert rsi_wilder([10, 11, 10], 2) == [None, None, 50.0]


def test_rsi_insufficient_history_and_bad_period():
    assert rsi_wilder([1.0] * 14, 14) == [None] * 14  # needs period + 1 closes
    assert rsi_wilder([1.0] * 15, 14)[14] == 50.0
    assert rsi_wilder([], 14) == []
    with pytest.raises(ValueError):
        rsi_wilder([1.0, 2.0], 0)


# ------------------------------------------------------------------------------ prev_mean
def test_prev_mean_excludes_current_value():
    assert prev_mean([1, 2, 3, 4, 5], 2) == [None, None, 1.5, 2.5, 3.5]
    vals = [100.0] * 20 + [1_000_000.0, 100.0]
    out = prev_mean(vals, 20)
    assert out[:20] == [None] * 20
    assert out[20] == 100.0  # the spike at index 20 is NOT in its own average
    assert out[21] == pytest.approx((19 * 100.0 + 1_000_000.0) / 20)  # ...but is in the next
    with pytest.raises(ValueError):
        prev_mean([1.0], 0)


# ------------------------------------------------------------------------------ causality
def test_indicators_never_change_when_future_values_are_removed():
    closes = [c.close for c in _candles(260)]
    full_ema, full_rsi, full_mean = ema(closes, 21), rsi_wilder(closes, 14), prev_mean(closes, 20)
    for m in (1, 13, 14, 15, 20, 21, 22, 100, 259):
        assert ema(closes[:m], 21) == full_ema[:m]
        assert rsi_wilder(closes[:m], 14) == full_rsi[:m]
        assert prev_mean(closes[:m], 20) == full_mean[:m]


# ------------------------------------------------------------------------------ features
def test_compute_features_values_and_warmup():
    candles = _candles(260)
    rows = compute_features(candles, CFG)
    assert len(rows) == len(candles)
    closes = [c.close for c in candles]
    for i, (c, r) in enumerate(zip(candles, rows, strict=True)):
        assert r.ts == c.ts
        assert r.close_ts == c.ts + CFG.timeframe_ms
        assert r.close == c.close
        assert (r.ema_fast is None) == (i < 8)
        assert (r.ema_slow is None) == (i < 20)
        assert (r.ema_regime is None) == (i < 199)
        assert (r.rsi is None) == (i < 14)
        assert (r.vol_avg is None) == (i < 20)
        assert (r.vol_ratio is None) == (i < 20)
        assert (r.ema_gap_pct is None) == (i < 20)
        assert (r.dist_regime_pct is None) == (i < 199)
    # Independent spot checks.
    assert rows[8].ema_fast == pytest.approx(sum(closes[:9]) / 9)
    assert rows[20].ema_slow == pytest.approx(sum(closes[:21]) / 21)
    assert rows[199].ema_regime == pytest.approx(sum(closes[:200]) / 200)
    r = rows[230]
    avg = sum(c.volume for c in candles[210:230]) / 20
    assert r.vol_avg == pytest.approx(avg)
    assert r.vol_ratio == pytest.approx(candles[230].volume / avg)
    assert r.ema_gap_pct == pytest.approx((r.ema_fast - r.ema_slow) / r.close * 100)
    assert r.dist_regime_pct == pytest.approx((r.close - r.ema_regime) / r.ema_regime * 100)
    assert r.ml_features() is not None


def test_compute_features_is_causal():
    candles = _candles(230)
    full = compute_features(candles, CFG)
    for m in (1, 21, 200, 229):
        assert compute_features(candles[:m], CFG) == full[:m]


def test_hour_utc_is_hour_of_close_ts():
    candles = _candles(12)
    rows = compute_features(candles, CFG)
    for r in rows:
        expected = datetime.fromtimestamp(r.close_ts / 1000, tz=UTC).hour
        assert r.hour_utc == expected
    # 2024-01-01 00:00 candle closes at 04:00; the 20:00 candle closes at 00:00 next day.
    assert [r.hour_utc for r in rows[:6]] == [4, 8, 12, 16, 20, 0]


def test_vol_ratio_none_when_previous_average_is_zero():
    candles = [
        Candle(T0 + t * TF, 10.0, 11.0, 9.0, 10.0, 0.0 if t < 20 else 50.0) for t in range(22)
    ]
    rows = compute_features(candles, CFG)
    assert rows[20].vol_avg == 0.0
    assert rows[20].vol_ratio is None
    assert rows[21].vol_avg == pytest.approx(50.0 / 20)
    assert rows[21].vol_ratio == pytest.approx(20.0)


def test_compute_features_rejects_unsorted_candles():
    candles = _candles(5)
    with pytest.raises(ValueError):
        compute_features([candles[1], candles[0], *candles[2:]], CFG)
    with pytest.raises(ValueError):
        compute_features([candles[0], candles[0]], CFG)
    assert compute_features([], CFG) == []
