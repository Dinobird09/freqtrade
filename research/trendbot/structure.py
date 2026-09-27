"""R8: stop placement behind market structure, using only data known at the signal close.

A pivot low at ``j`` needs ``k`` candles on its right to be recognised, so it becomes
known only at the close of candle ``j + k``. ``find_stop`` for signal candle ``i`` only
ever reads ``candles[0..i]``; truncating the list after ``i`` never changes the result.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from .config import StrategyConfig
from .models import Candle, StopPlan


def _px(x: float) -> str:
    return f"{x:.8g}"


def _pct(x: float, bound: float) -> str:
    """Percent with 2 decimals, or more if rounding would make it print as ``bound``."""
    if x == bound:
        return f"{x:.2f}"
    for places in range(2, 13):
        s = f"{x:.{places}f}"
        if s != f"{bound:.{places}f}":
            return s
    return repr(x)


def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts // 1000, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")


def _is_pivot_low(candles: Sequence[Candle], j: int, k: int) -> bool:
    """``low[j]`` strictly below the lows of the ``k`` candles on EACH side."""
    low = candles[j].low
    return all(low < candles[j - m].low and low < candles[j + m].low for m in range(1, k + 1))


def latest_confirmed_pivot(candles: Sequence[Candle], i: int, cfg: StrategyConfig) -> int | None:
    """Index of the most recent confirmed pivot low usable for signal candle ``i``, or None.

    Conditions on ``j``: pivot low with ``k = cfg.swing_pivot_k`` candles each side,
    ``j + k <= i`` (confirmed by the close of ``i``), ``j >= i - cfg.swing_lookback``,
    ``j >= k`` (full left side exists) and ``low[j] < candles[i].close``. Pivots at or
    above the signal close are skipped and the search continues further back.
    """
    k = cfg.swing_pivot_k
    close = candles[i].close
    oldest = max(k, i - cfg.swing_lookback)
    for j in range(i - k, oldest - 1, -1):
        if candles[j].low < close and _is_pivot_low(candles, j, k):
            return j
    return None


def _lowest_low(candles: Sequence[Candle], i: int, n: int) -> int:
    """Index of the lowest low in ``candles[i-n+1 .. i]`` (clamped at 0; oldest on ties)."""
    return min(range(max(0, i - n + 1), i + 1), key=lambda t: candles[t].low)


def _structure(candles: Sequence[Candle], i: int, cfg: StrategyConfig) -> tuple[float, str, str]:
    """(structure level, method, human-readable description of where it came from)."""
    k = cfg.swing_pivot_k
    j = latest_confirmed_pivot(candles, i, cfg)
    if j is not None:
        where = (
            f"confirmed pivot low {_px(candles[j].low)} of {_iso(candles[j].ts)} "
            f"(k={k} higher lows each side)"
        )
        return candles[j].low, "pivot", where
    j = _lowest_low(candles, i, cfg.fallback_lookback)
    where = (
        f"no confirmed pivot in the last {cfg.swing_lookback} candles, so the lowest low "
        f"{_px(candles[j].low)} of the last {cfg.fallback_lookback} candles ({_iso(candles[j].ts)})"
    )
    return candles[j].low, "lookback_low", where


def find_stop(
    candles: Sequence[Candle], i: int, pair: str, cfg: StrategyConfig
) -> tuple[StopPlan | None, str]:
    """R8 stop for a long whose signal candle is ``candles[i]`` (decided at its close).

    1) Most recent confirmed pivot low (see ``latest_confirmed_pivot``), method "pivot".
    2) Else the min low of ``candles[i-fallback_lookback+1 .. i]``, method "lookback_low".

    ``stop = level * (1 - buffer_pct/100)`` with ``buffer_pct =
    cfg.risk_for(pair).stop_buffer_pct``. With ``dist = (close - stop) / close * 100``,
    returns ``(None, reason)`` if ``stop >= close``, ``dist < cfg.min_stop_distance_pct``
    or ``dist > cfg.max_stop_distance_pct`` (both bounds inclusive-accept); otherwise
    ``(StopPlan, reason)``. Raises ValueError for an out-of-range ``i`` or ``k < 1`` and
    ConfigError for an unconfigured pair.
    """
    if not 0 <= i < len(candles):
        raise ValueError(f"signal index {i} out of range for {len(candles)} candles")
    if cfg.swing_pivot_k < 1 or cfg.fallback_lookback < 1:
        raise ValueError("swing_pivot_k and fallback_lookback must be >= 1")
    buffer_pct = cfg.risk_for(pair).stop_buffer_pct
    close = candles[i].close
    level, method, where = _structure(candles, i, cfg)
    stop = level * (1.0 - buffer_pct / 100.0)
    if not stop < close:
        return None, f"stop {_px(stop)} is not below close {_px(close)}; structure: {where}"
    dist = (close - stop) / close * 100.0
    lo, hi = cfg.min_stop_distance_pct, cfg.max_stop_distance_pct
    if dist < lo:
        return None, (
            f"stop {_px(stop)} is only {_pct(dist, lo)}% below close {_px(close)}, tighter "
            f"than the {lo:g}% minimum; structure: {where}"
        )
    if dist > hi:
        return None, (
            f"stop {_px(stop)} is {_pct(dist, hi)}% below close {_px(close)}, wider than "
            f"the {hi:g}% maximum; structure: {where}"
        )
    plan = StopPlan(stop=stop, structure_level=level, buffer_pct=buffer_pct, method=method)
    reason = (
        f"stop {_px(stop)} = {where} minus a {buffer_pct:g}% buffer, "
        f"{dist:.2f}% below close {_px(close)}"
    )
    return plan, reason
