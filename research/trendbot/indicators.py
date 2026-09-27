"""Indicators feeding the entry gates R1-R4. Pure standard library, causal by construction.

Every value at index ``i`` depends only on ``values[0..i]`` (``prev_mean`` only on
``values[0..i-1]``), so it is known at the close of candle ``i`` and never earlier.
Truncating the input never changes an earlier output (see the tests).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from itertools import pairwise

from .config import StrategyConfig
from .models import HOUR_MS, Candle, FeatureRow


def _check_window(n: int, name: str) -> None:
    if n < 1:
        raise ValueError(f"{name} must be >= 1 (got {n})")


def ema(values: Sequence[float], period: int) -> list[float | None]:
    """Exponential moving average seeded with the SMA of the first ``period`` values.

    ``out[i]`` is None for ``i < period - 1``; ``out[period - 1]`` is the SMA seed and after
    that ``out[i] = alpha * values[i] + (1 - alpha) * out[i - 1]``, ``alpha = 2/(period+1)``.
    """
    _check_window(period, "period")
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    alpha = 2.0 / (period + 1)
    prev = math.fsum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = alpha * values[i] + (1.0 - alpha) * prev
        out[i] = prev
    return out


def _rsi_value(avg_gain: float, avg_loss: float) -> float:
    if avg_loss == 0.0:
        return 100.0 if avg_gain > 0.0 else 50.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def rsi_wilder(closes: Sequence[float], period: int = 14) -> list[float | None]:
    """Wilder's RSI. First value at index ``period`` (it needs ``period`` price changes).

    The first average gain/loss is the simple mean of the first ``period`` changes; later
    ones use Wilder smoothing ``avg = (avg * (period - 1) + x) / period``. RSI is 100 when
    the average loss is 0 and the average gain > 0, and 50 when both are 0.
    """
    _check_window(period, "period")
    out: list[float | None] = [None] * len(closes)
    if len(closes) <= period:
        return out
    first = [closes[t] - closes[t - 1] for t in range(1, period + 1)]
    avg_gain = math.fsum(d for d in first if d > 0.0) / period
    avg_loss = math.fsum(-d for d in first if d < 0.0) / period
    out[period] = _rsi_value(avg_gain, avg_loss)
    for i in range(period + 1, len(closes)):
        change = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
        out[i] = _rsi_value(avg_gain, avg_loss)
    return out


def prev_mean(values: Sequence[float], n: int) -> list[float | None]:
    """``out[i] = mean(values[i-n:i])``: the PREVIOUS ``n`` values, current excluded.

    None for ``i < n``.
    """
    _check_window(n, "n")
    out: list[float | None] = [None] * len(values)
    for i in range(n, len(values)):
        out[i] = math.fsum(values[i - n : i]) / n
    return out


def _safe_div(num: float, den: float | None) -> float | None:
    if den is None or den == 0:
        return None
    return num / den


def _feature_row(
    candle: Candle,
    timeframe_ms: int,
    ema_fast: float | None,
    ema_slow: float | None,
    ema_regime: float | None,
    rsi: float | None,
    vol_avg: float | None,
) -> FeatureRow:
    close_ts = candle.ts + timeframe_ms
    gap = None
    if ema_fast is not None and ema_slow is not None and candle.close != 0:
        gap = (ema_fast - ema_slow) / candle.close * 100.0
    dist = None
    if ema_regime is not None and ema_regime != 0:
        dist = (candle.close - ema_regime) / ema_regime * 100.0
    return FeatureRow(
        ts=candle.ts,
        close_ts=close_ts,
        close=candle.close,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        ema_regime=ema_regime,
        rsi=rsi,
        vol_avg=vol_avg,
        vol_ratio=_safe_div(candle.volume, vol_avg),
        ema_gap_pct=gap,
        dist_regime_pct=dist,
        hour_utc=(close_ts // HOUR_MS) % 24,
    )


def compute_features(candles: Sequence[Candle], cfg: StrategyConfig) -> list[FeatureRow]:
    """One FeatureRow per candle, same order, each as known at that candle's close.

    ``close_ts = ts + cfg.timeframe_ms``; ``hour_utc`` is the UTC hour of ``close_ts``.
    Candles must be strictly ascending by ``ts`` (anything else would leak future data
    into earlier rows), otherwise ValueError.
    """
    for prev, cur in pairwise(candles):
        if cur.ts <= prev.ts:
            raise ValueError(f"candles not strictly ascending at ts={cur.ts}")
    closes = [c.close for c in candles]
    ema_fast = ema(closes, cfg.ema_fast)
    ema_slow = ema(closes, cfg.ema_slow)
    ema_regime = ema(closes, cfg.ema_regime)
    rsi = rsi_wilder(closes, cfg.rsi_period)
    vol_avg = prev_mean([c.volume for c in candles], cfg.vol_lookback)
    return [
        _feature_row(
            c, cfg.timeframe_ms, ema_fast[i], ema_slow[i], ema_regime[i], rsi[i], vol_avg[i]
        )
        for i, c in enumerate(candles)
    ]
