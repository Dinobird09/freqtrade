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


# ---------------------------------------------------------------------- candle indicators
# Used by the confluence system and the mean-reversion strategy. Same rule as above: every
# value at index ``i`` uses candles ``0..i`` only.
def rolling_std(values: Sequence[float], n: int) -> list[float | None]:
    """Population standard deviation of the last ``n`` values (current included)."""
    _check_window(n, "n")
    out: list[float | None] = [None] * len(values)
    for i in range(n - 1, len(values)):
        win = values[i - n + 1 : i + 1]
        m = math.fsum(win) / n
        out[i] = math.sqrt(math.fsum((x - m) ** 2 for x in win) / n)
    return out


def bollinger(
    closes: Sequence[float], n: int = 20, k: float = 2.0
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Bollinger bands ``(middle, upper, lower)``: the ``n``-bar SMA +- ``k`` population sd."""
    sd = rolling_std(closes, n)
    mid: list[float | None] = [None] * len(closes)
    up: list[float | None] = [None] * len(closes)
    lo: list[float | None] = [None] * len(closes)
    for i in range(n - 1, len(closes)):
        m = math.fsum(closes[i - n + 1 : i + 1]) / n
        mid[i], up[i], lo[i] = m, m + k * sd[i], m - k * sd[i]  # type: ignore[operator]
    return mid, up, lo


def zscore(closes: Sequence[float], n: int = 20) -> list[float | None]:
    """``(close - SMA(n)) / sd(n)``; None while the window is short or flat."""
    mid, _, _ = bollinger(closes, n, 1.0)
    sd = rolling_std(closes, n)
    return [
        (c - m) / s if m is not None and s else None
        for c, m, s in zip(closes, mid, sd, strict=True)
    ]


def obv(candles: Sequence[Candle]) -> list[float]:
    """On-Balance Volume: + volume on an up close, - volume on a down close, 0 at the start."""
    out = [0.0] * len(candles)
    for i in range(1, len(candles)):
        c, p = candles[i].close, candles[i - 1].close
        out[i] = out[i - 1] + (candles[i].volume if c > p else -candles[i].volume if c < p else 0.0)
    return out


def vwap_session(candles: Sequence[Candle], session_ms: int = 24 * HOUR_MS) -> list[float]:
    """Session VWAP of the typical price ``(h + l + c) / 3``, reset at each UTC session start."""
    out: list[float] = []
    pv = vol = 0.0
    session = None
    for c in candles:
        s = c.ts // session_ms
        if s != session:
            session, pv, vol = s, 0.0, 0.0
        tp = (c.high + c.low + c.close) / 3
        pv += tp * c.volume
        vol += c.volume
        out.append(pv / vol if vol > 0 else tp)
    return out


def vwap_rolling(candles: Sequence[Candle], n: int) -> list[float | None]:
    """VWAP of the typical price over the last ``n`` candles (current included)."""
    _check_window(n, "n")
    out: list[float | None] = [None] * len(candles)
    pv = vol = 0.0
    for i, c in enumerate(candles):
        pv += (c.high + c.low + c.close) / 3 * c.volume
        vol += c.volume
        if i >= n:
            o = candles[i - n]
            pv -= (o.high + o.low + o.close) / 3 * o.volume
            vol -= o.volume
        if i >= n - 1:
            out[i] = pv / vol if vol > 1e-12 else (c.high + c.low + c.close) / 3
    return out


def _midpoint(candles: Sequence[Candle], i: int, n: int) -> float | None:
    if i < n - 1:
        return None
    win = candles[i - n + 1 : i + 1]
    return (max(c.high for c in win) + min(c.low for c in win)) / 2


def ichimoku(
    candles: Sequence[Candle],
    tenkan: int = 9,
    kijun: int = 26,
    senkou: int = 52,
    shift: int = 26,
) -> dict[str, list[float | None]]:
    """Ichimoku lines as known at each candle's close.

    ``tenkan`` / ``kijun``: midpoints of the last 9 / 26 highs and lows. ``span_a`` /
    ``span_b`` are the cloud AS PLOTTED at bar ``i``: (tenkan + kijun) / 2 and the 52-bar
    midpoint computed ``shift`` bars earlier (the forward displacement), so the cloud at
    ``i`` never needs a future candle. The Chikou span (a backward shift) is left out: it
    can only be compared with the past, which the other lines already do.
    """
    n = len(candles)
    tk = [_midpoint(candles, i, tenkan) for i in range(n)]
    kj = [_midpoint(candles, i, kijun) for i in range(n)]
    sb_raw = [_midpoint(candles, i, senkou) for i in range(n)]
    sa_raw = [
        (a + b) / 2 if a is not None and b is not None else None
        for a, b in zip(tk, kj, strict=True)
    ]
    span_a: list[float | None] = [None] * n
    span_b: list[float | None] = [None] * n
    for i in range(shift, n):
        span_a[i], span_b[i] = sa_raw[i - shift], sb_raw[i - shift]
    return {"tenkan": tk, "kijun": kj, "span_a": span_a, "span_b": span_b}  # type: ignore[dict-item]


def parabolic_sar(
    candles: Sequence[Candle], step: float = 0.02, max_step: float = 0.2
) -> tuple[list[float | None], list[int]]:
    """Wilder's Parabolic SAR and its trend (+1 below price / bullish, -1 above / bearish).

    Starts bullish at bar 0 with SAR = low[0] and the extreme point high[0]. Each bar: SAR +=
    af * (EP - SAR), clamped to stay beyond the previous two bars' lows (highs when
    bearish); a bar that trades through the SAR flips the trend, the SAR jumps to the old EP
    and af resets to ``step``; a new extreme raises af by ``step`` up to ``max_step``.
    """
    n = len(candles)
    sar_out: list[float | None] = [None] * n
    trend_out = [0] * n
    if not n:
        return sar_out, trend_out
    trend, sar, ep, af = 1, candles[0].low, candles[0].high, step
    sar_out[0], trend_out[0] = sar, trend
    for i in range(1, n):
        c, p1 = candles[i], candles[i - 1]
        p2 = candles[i - 2] if i >= 2 else p1
        sar = sar + af * (ep - sar)
        if trend == 1:
            sar = min(sar, p1.low, p2.low)
            if c.low < sar:
                trend, sar, ep, af = -1, ep, c.low, step
            elif c.high > ep:
                ep, af = c.high, min(af + step, max_step)
        else:
            sar = max(sar, p1.high, p2.high)
            if c.high > sar:
                trend, sar, ep, af = 1, ep, c.high, step
            elif c.low < ep:
                ep, af = c.low, min(af + step, max_step)
        sar_out[i], trend_out[i] = sar, trend
    return sar_out, trend_out


def adx(candles: Sequence[Candle], n: int = 14) -> list[float | None]:
    """Wilder's Average Directional Index (0-100). First value at index ``2n - 1``."""
    _check_window(n, "n")
    size = len(candles)
    out: list[float | None] = [None] * size
    if size <= 2 * n - 1:
        return out
    tr_s = pdm_s = mdm_s = 0.0
    dxs: list[float] = []
    adx_v = 0.0
    for i in range(1, size):
        c, p = candles[i], candles[i - 1]
        tr = max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close))
        up, down = c.high - p.high, p.low - c.low
        pdm = up if up > down and up > 0 else 0.0
        mdm = down if down > up and down > 0 else 0.0
        if i <= n:
            tr_s, pdm_s, mdm_s = tr_s + tr, pdm_s + pdm, mdm_s + mdm
            if i < n:
                continue
        else:
            tr_s = tr_s - tr_s / n + tr
            pdm_s = pdm_s - pdm_s / n + pdm
            mdm_s = mdm_s - mdm_s / n + mdm
        pdi = 100 * pdm_s / tr_s if tr_s else 0.0
        mdi = 100 * mdm_s / tr_s if tr_s else 0.0
        dx = 100 * abs(pdi - mdi) / (pdi + mdi) if pdi + mdi else 0.0
        if len(dxs) < n:
            dxs.append(dx)
            if len(dxs) == n:
                adx_v = math.fsum(dxs) / n
                out[i] = adx_v
        else:
            adx_v = (adx_v * (n - 1) + dx) / n
            out[i] = adx_v
    return out
