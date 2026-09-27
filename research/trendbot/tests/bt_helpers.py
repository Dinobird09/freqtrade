"""Crafted candle scenarios for the gatekeeper / backtester / invariants tests (W4).

A :class:`Scenario` is a deterministic zig-zag uptrend (closes x 1.008, 1.006, 0.993, 0.994,
1.007, 1.005 repeating) with flat volume. On it, after the 200-candle EMA warm-up, every
candle with ``i % 6 == 4`` passes R1 (trend), R2 (RSI ~64) and R4 (regime) and has a
confirmed pivot-low stop ~2.5 % below its close; it becomes a full R1-R4 signal only when
:meth:`Scenario.spike` gives it a volume spike (R3). Outcomes are then forced with wicks
and gaps on LATER candles, which never change the indicators (closes only) or the stop of
an earlier signal (``find_stop`` reads candles up to the signal only).
"""

from __future__ import annotations

from dataclasses import replace

from research.trendbot.config import StrategyConfig
from research.trendbot.models import HOUR_MS, Candle, NewsEvent
from research.trendbot.structure import find_stop


TF = 4 * HOUR_MS
T0 = 1_704_067_200_000  # 2024-01-01T00:00:00Z
PATTERN = (1.008, 1.006, 0.993, 0.994, 1.007, 1.005)
BASE_VOLUME = 100.0
SPIKE_VOLUME = 400.0
FIRST_SIGNAL = 208  # first i >= 200 with i % 6 == 4


def slot(k: int) -> int:
    """The k-th usable signal index (k = 0, 1, 2, ...), 6 candles apart."""
    return FIRST_SIGNAL + 6 * k


def ts(i: int) -> int:
    return T0 + i * TF


class Scenario:
    """``amp`` scales every move and wick (0.2 -> stops ~0.5 % below the close)."""

    def __init__(
        self, pair: str = "BTC/USDT", n: int = 330, start: float = 100.0, amp: float = 1.0
    ) -> None:
        self.pair = pair
        self.candles: list[Candle] = []
        o = start
        for t in range(n):
            c = o * (1 + (PATTERN[t % 6] - 1) * amp)
            hi = max(o, c) * (1 + 0.001 * amp)
            lo = min(o, c) * (1 - (0.003 if c < o else 0.001) * amp)
            self.candles.append(Candle(ts(t), o, hi, lo, c, BASE_VOLUME))
            o = c

    # ------------------------------------------------------------------ signals
    def spike(self, *idxs: int) -> Scenario:
        for i in idxs:
            self.candles[i] = replace(self.candles[i], volume=SPIKE_VOLUME)
        return self

    def stop(self, i: int, cfg: StrategyConfig) -> float:
        plan, why = find_stop(self.candles, i, self.pair, cfg)
        assert plan is not None, why
        return plan.stop

    def entry(self, i: int, cfg: StrategyConfig) -> float:
        return self.candles[i + 1].open * (1 + cfg.slippage_pct / 100)

    def target(self, i: int, cfg: StrategyConfig) -> float:
        e = self.entry(i, cfg)
        return e + cfg.reward_risk * (e - self.stop(i, cfg))

    # ------------------------------------------------------------------ outcomes
    def low(self, j: int, price: float) -> Scenario:
        c = self.candles[j]
        assert price <= min(c.open, c.close)
        self.candles[j] = replace(c, low=price)
        return self

    def high(self, j: int, price: float) -> Scenario:
        c = self.candles[j]
        assert price >= max(c.open, c.close)
        self.candles[j] = replace(c, high=price)
        return self

    def gap_open(self, j: int, price: float) -> Scenario:
        c = self.candles[j]
        self.candles[j] = replace(c, open=price, low=min(c.low, price), high=max(c.high, price))
        return self

    def stop_out(self, i: int, cfg: StrategyConfig, j: int | None = None) -> Scenario:
        """Wick below the stop of signal ``i`` at candle ``j`` (default: the one after the fill)."""
        return self.low(i + 2 if j is None else j, self.stop(i, cfg) * 0.999)

    def take_profit(self, i: int, cfg: StrategyConfig, j: int | None = None) -> Scenario:
        return self.high(i + 2 if j is None else j, self.target(i, cfg) * 1.01)


def news(i: int, offset_h: float, scope: str = "ALL", kind: str = "macro", impact: str = "high"):
    """An event ``offset_h`` hours after the DECISION time of signal candle ``i``."""
    return NewsEvent(ts(i + 1) + round(offset_h * HOUR_MS), scope, impact, kind, "test")


def data_of(*scenarios: Scenario) -> dict[str, list[Candle]]:
    return {s.pair: s.candles for s in scenarios}
