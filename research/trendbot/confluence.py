"""The "elite multi-indicator confluence" trend-following system: a trade only when the daily
trend, the 4H structure and at least five named signals on the entry timeframe agree.

Input: 1H (or 15m, or 4H) candles; the 4H and 1D candles are resampled from them
(complete buckets only), so one candle list drives every timeframe. Optional: the Crypto
Fear & Greed value and lower-timeframe (5m / 1m) candles for the execution check.

1. MARKET BIAS (1D): price above the 1D 200 EMA = bullish, below = bearish. Sideways = NO
   TRADE: price within ``bias_band_pct`` (1%) of the EMA, the EMA flat (moved less than
   ``flat_pct`` in ``flat_days``), or price crossed it ``whipsaw_crosses`` (3) or more times
   in the last ``whipsaw_days`` (20) days. Never trade against the 1D trend.
2. STRUCTURE (4H): the last two confirmed swing highs and lows make HH + HL (bullish) or
   LH + LL (bearish); it must agree with the bias.
3. SENTIMENT (optional Fear & Greed 0-100): <= 25 bullish opportunity, 26-45 accumulation,
   65-84 caution (counts against longs: one more signal is needed), >= 85 no new longs
   (consider shorts).
4. SIGNALS (entry timeframe, each a named boolean known at the bar's close, see SIGNALS).
5. ENTRY: the bias and structure filters, ALL of DEFAULT_REQUIRED (price above the 200 EMA
   and the cloud, RSI 55-70, a fresh MACD cross with an expanding histogram, OBV rising,
   price above VWAP on strong volume, ATR rising) and at least ``min_signals`` (5) aligned
   signals in total, on a pullback / re-test of VWAP, the 20 / 50 EMA or a Fibonacci zone.
   ``required`` + ``min_signals`` replace the fixed list (the drag-and-drop builder).
   With lower-timeframe candles: a VWAP reclaim and a bullish confirmation candle there.
6. RISK: stop 1.5 x ATR, TP1 2R (half), TP2 3R; risk 1% (at most 2%) per trade, at most 3
   trades a day, no more trades that day after 2 losses in a row (backtest + lab bot).

Shorts are reported as setups (the mirror image of every rule); the bot trades spot, so the
lab strategy and a live bot only ever take the longs.

``analyze`` returns a TRADE CARD or a NO TRADE verdict; ``format_card`` prints it in the
mandatory format. ``scan`` is the same decision run causally over a whole history (the lab
strategy ``confluence`` in strategies.py).
"""

from __future__ import annotations

import math
import statistics
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Mapping, Sequence
from itertools import pairwise
from typing import Any

from .data import resample_candles, timeframe_to_ms
from .indicators import (
    ema,
    ichimoku,
    obv,
    parabolic_sar,
    prev_mean,
    rsi_wilder,
    vwap_rolling,
    vwap_session,
)
from .models import DAY_MS, HOUR_MS, Candle
from .strategies import MIN_RR, Confluence, Signal, atr, macd, pivots


__all__ = [
    "DEFAULTS",
    "DEFAULT_REQUIRED",
    "NO_TRADE_TEXT",
    "SIGNALS",
    "Confluence",
    "analyze",
    "format_card",
    "scan",
]

H4_MS = 4 * HOUR_MS
NO_TRADE_TEXT = "NO TRADE – Insufficient confluence or misaligned market conditions."  # noqa: RUF001

SIGNALS: dict[str, str] = {
    "ema200": "Price is above the 200 EMA (below it for a short): the long-term trend is "
    "on your side.",
    "ichimoku_cloud": "Price is above the Ichimoku cloud (below it for a short).",
    "rsi_zone": "RSI(14) is in the 55-70 continuation zone (30-45 for a short); above 70 or "
    "below 30 is avoided and 45-55 is a reset zone that does not count.",
    "macd_cross": "MACD crossed above its signal line in the last few bars and the histogram "
    "is expanding (the mirror for a short).",
    "obv_trend": "On-Balance Volume is rising with price (falling for a short), with no "
    "divergence against it.",
    "volume_rising": "Volume on this bar is clearly above its 20-bar average: real "
    "participation behind the move.",
    "vwap": "Price is above VWAP (below it for a short); the best entries come on a pullback "
    "to it.",
    "fib_zone": "The pullback held inside the 0.382-0.618 Fibonacci retracement of the last "
    "impulse (best when an EMA or VWAP sits in the zone too).",
    "psar_flip": "Parabolic SAR flipped bullish recently and sits below price (bearish and "
    "above it for a short).",
    "tenkan_kijun": "Ichimoku Tenkan-sen crossed above the Kijun-sen recently and stays "
    "above it (below it for a short).",
    "atr_rising": "ATR is rising (a live trend) and not unusually low (no dead market).",
}

DEFAULT_REQUIRED: tuple[str, ...] = (
    "ema200",
    "ichimoku_cloud",
    "rsi_zone",
    "macd_cross",
    "obv_trend",
    "vwap",
    "volume_rising",
    "atr_rising",
)

DEFAULTS: dict[str, Any] = {
    "required": None,
    "use": None,  # None: every signal counts toward min_signals
    "min_signals": 5,
    "risk_pct": 1.0,
    "max_trades_per_day": 3,
    "stop_after_losses": 2,
    "timeframe": "1h",
    "stop_atr": 1.5,
    "tp1_rr": 2.0,
    "tp2_rr": 3.0,
    "tp1_share": 0.5,
    # 1D bias
    "bias_band_pct": 1.0,
    "flat_days": 10,
    "flat_pct": 0.25,
    "whipsaw_days": 20,
    "whipsaw_crosses": 3,
    # 4H structure
    "struct_pivot": 3,
    # entry-timeframe signals
    "rsi_lo": 55.0,
    "rsi_hi": 70.0,
    "cross_bars": 5,
    "obv_n": 20,
    "vol_n": 20,
    "vol_mult": 1.2,
    "vwap": "auto",  # "session" (UTC day) | "rolling" | "auto" (session below 4H)
    "vwap_n": 24,
    "fib_pivot": 3,
    "fib_min_atr": 2.0,
    "psar_bars": 10,
    "tk_bars": 20,
    "atr_n": 14,
    "atr_slope": 3,
    "min_atr_ratio": 0.5,
    # the pullback / re-test the entry must come from
    "require_retest": True,
    "retest_bars": 6,
    "retest_atr": 0.3,
    # lower-timeframe execution check
    "lower_bars": 12,
}

_MAX_RISK_PCT = 2.0
_HISTORY_DAYS = 240  # 1D EMA200 + its slope + the whipsaw window, with a margin


def check_params(p: Mapping[str, Any]) -> None:
    """ValueError with a plain-English reason for a rule set that cannot work."""
    req, use = p.get("required"), p.get("use")
    unknown = sorted((set(req or ()) | set(use or ())) - set(SIGNALS))
    if unknown:
        raise ValueError(f"unknown confluence signals {unknown}: choose from {sorted(SIGNALS)}")
    if not 1 <= int(p.get("min_signals", 5)) <= len(SIGNALS):
        raise ValueError(f"min_signals must be between 1 and {len(SIGNALS)}")
    counted = counted_signals(req, use)
    if int(p.get("min_signals", 5)) > len(counted):
        raise ValueError(
            f"min_signals {p.get('min_signals')} is more than the {len(counted)} signals that "
            f"count ({', '.join(sorted(counted))})"
        )
    risk = float(p.get("risk_pct", 1.0))
    if not 0 < risk <= _MAX_RISK_PCT:
        raise ValueError(f"risk_pct must be above 0 and at most {_MAX_RISK_PCT:g}% per trade")
    if float(p.get("stop_atr", 1.5)) <= 0:
        raise ValueError("stop_atr must be > 0")
    tp1, tp2 = float(p.get("tp1_rr", 2.0)), float(p.get("tp2_rr", 3.0))
    if tp1 < MIN_RR or tp2 < tp1:
        raise ValueError(f"targets need tp1_rr >= {MIN_RR:g} and tp2_rr >= tp1_rr")
    tf = timeframe_to_ms(str(p.get("timeframe", "1h")))
    if tf > H4_MS or H4_MS % tf:
        raise ValueError("the entry timeframe must divide 4h (e.g. 15m, 1h, 4h)")


def counted_signals(required: Sequence[str] | None, use: Sequence[str] | None) -> set[str]:
    """The signals that count toward ``min_signals``: all of them, or ``use`` plus the
    required ones (``required`` None = DEFAULT_REQUIRED)."""
    if use is None:
        return set(SIGNALS)
    return set(use) | set(DEFAULT_REQUIRED if required is None else required)


def history_needed(timeframe: str) -> int:
    """Entry-timeframe candles needed for a converged 1D EMA200 (at least 1500)."""
    return max(1500, math.ceil(_HISTORY_DAYS * DAY_MS / timeframe_to_ms(timeframe)))


def tf_label(ms: int) -> str:
    for unit, size in (("D", DAY_MS), ("H", HOUR_MS), ("M", 60_000)):
        if ms % size == 0:
            return f"{ms // size}{unit}"
    return f"{ms}ms"


def infer_timeframe_ms(candles: Sequence[Candle]) -> int:
    """The most common spacing between consecutive candles."""
    gaps = Counter(b.ts - a.ts for a, b in pairwise(candles) if b.ts > a.ts)
    if not gaps:
        raise ValueError("need at least two candles to tell the timeframe")
    return gaps.most_common(1)[0][0]


# ---------------------------------------------------------------------- indicator context
class _Ctx:
    """Every series the decision needs, computed once over the whole (causal) history."""

    def __init__(self, cs: Sequence[Candle], p: Mapping[str, Any], tf_ms: int) -> None:
        self.cs, self.p, self.tf = cs, p, tf_ms
        closes = [c.close for c in cs]
        self.closes = closes
        self.ema200, self.ema20, self.ema50 = ema(closes, 200), ema(closes, 20), ema(closes, 50)
        self.ichi = ichimoku(cs)
        self.rsi = rsi_wilder(closes, 14)
        self.line, self.sig = macd(closes)
        self.hist = [
            a - b if a is not None and b is not None else None
            for a, b in zip(self.line, self.sig, strict=True)
        ]
        self.obv = obv(cs)
        self.obv_ema = ema(self.obv, int(p["obv_n"]))
        self.vol_avg = prev_mean([c.volume for c in cs], int(p["vol_n"]))
        mode = p["vwap"] if p["vwap"] != "auto" else ("session" if tf_ms < H4_MS else "rolling")
        self.vwap_mode = mode
        self.vwap: list[float | None] = (
            list(vwap_session(cs)) if mode == "session" else vwap_rolling(cs, int(p["vwap_n"]))
        )
        _, self.psar_dir = parabolic_sar(cs)
        self.atr = atr(cs, int(p["atr_n"]))
        self.natr = [a / c if a is not None and c else None for a, c in zip(self.atr, closes)]  # noqa: B905
        r = int(p["fib_pivot"])
        self.ph, self.pl = pivots(cs, r, r)
        self.ph_i, self.pl_i = [j for j, _ in self.ph], [j for j, _ in self.pl]
        # 1D: bias
        self.day = resample_candles(cs, tf_ms, DAY_MS)
        self.day_ts = [d.ts for d in self.day]
        self.day_ema = ema([d.close for d in self.day], 200)
        # 4H: structure
        self.h4 = list(cs) if tf_ms == H4_MS else resample_candles(cs, tf_ms, H4_MS)
        self.h4_ts = [h.ts for h in self.h4]
        sp = int(p["struct_pivot"])
        self.h4_ph, self.h4_pl = pivots(self.h4, sp, sp)
        self.h4_ph_i, self.h4_pl_i = [j for j, _ in self.h4_ph], [j for j, _ in self.h4_pl]

    # -------------------------------------------------------------- higher timeframes
    def _htf_index(self, ts_list: Sequence[int], size: int, i: int) -> int:
        """The last higher-timeframe candle that is complete at the close of bar ``i``."""
        return bisect_right(ts_list, self.cs[i].ts + self.tf - size) - 1

    def bias(self, i: int) -> tuple[str, str, float]:
        """("bullish" | "bearish" | "sideways" | "unknown", explanation, % from the EMA)."""
        p = self.p
        k = self._htf_index(self.day_ts, DAY_MS, i)
        if k < 0 or self.day_ema[k] is None:
            return "unknown", "not enough daily history for the 1D 200 EMA", 0.0
        e = self.day_ema[k]
        dist = (self.closes[i] / e - 1) * 100  # type: ignore[operator]
        w = int(p["whipsaw_days"])
        sides = [
            1 if d.close > de else -1
            for d, de in zip(self.day[max(0, k - w) : k + 1], self.day_ema[max(0, k - w) : k + 1])  # noqa: B905
            if de is not None
        ]
        crosses = sum(1 for a, b in pairwise(sides) if a != b)
        f = int(p["flat_days"])
        prev = self.day_ema[k - f] if k - f >= 0 else None
        slope = (e / prev - 1) * 100 if prev else 0.0  # type: ignore[operator]
        where = f"price {abs(dist):.1f}% {'above' if dist > 0 else 'below'} the 1D 200 EMA"
        trend = f"EMA {'rising' if slope > 0 else 'falling'} {abs(slope):.2f}% in {f} days"
        if crosses >= int(p["whipsaw_crosses"]):
            return (
                "sideways",
                f"whipsaw: price crossed the 1D 200 EMA {crosses} times in {w} days",
                dist,
            )
        if abs(dist) < float(p["bias_band_pct"]):
            return "sideways", f"sideways: {where}, inside the {p['bias_band_pct']:g}% band", dist
        if abs(slope) < float(p["flat_pct"]):
            return "sideways", f"sideways: the 1D 200 EMA is flat ({trend})", dist
        return ("bullish" if dist > 0 else "bearish"), f"{where}, {trend}", dist

    def structure(self, i: int) -> tuple[str, str]:
        """4H swing structure from the last two CONFIRMED swing highs and lows."""
        h = self._htf_index(self.h4_ts, H4_MS, i)
        sp = int(self.p["struct_pivot"])
        nh = bisect_right(self.h4_ph_i, h - sp)
        nl = bisect_right(self.h4_pl_i, h - sp)
        if h < 0 or nh < 2 or nl < 2:
            return "unknown", "not enough 4H swing points yet"
        (_, h1), (_, h2) = self.h4_ph[nh - 2], self.h4_ph[nh - 1]
        (_, l1), (_, l2) = self.h4_pl[nl - 2], self.h4_pl[nl - 1]
        hs = "higher high" if h2 > h1 else "lower high"
        ls = "higher low" if l2 > l1 else "lower low"
        text = f"4H {hs} ({_px(h2)}) and {ls} ({_px(l2)})"
        if h2 > h1 and l2 > l1:
            return "bullish", text
        if h2 < h1 and l2 < l1:
            return "bearish", text
        return "mixed", text

    # -------------------------------------------------------------- signals
    def signals(self, i: int, s: int) -> dict[str, tuple[bool, str]]:
        """Every signal at bar ``i`` for side ``s`` (+1 long, -1 short): (ok, detail)."""
        return {name: getattr(self, f"_s_{name}")(i, s) for name in SIGNALS}

    def _s_ema200(self, i: int, s: int) -> tuple[bool, str]:
        e = self.ema200[i]
        if e is None:
            return False, "not enough history for the 200 EMA"
        d = (self.closes[i] / e - 1) * 100
        return s * d > 0, f"price {abs(d):.2f}% {'above' if d > 0 else 'below'} the 200 EMA"

    def _s_ichimoku_cloud(self, i: int, s: int) -> tuple[bool, str]:
        a, b = self.ichi["span_a"][i], self.ichi["span_b"][i]
        if a is None or b is None:
            return False, "not enough history for the Ichimoku cloud"
        c, top, bot = self.closes[i], max(a, b), min(a, b)
        where = "above" if c > top else "below" if c < bot else "inside"
        return (c > top if s > 0 else c < bot), f"price {where} the cloud ({_px(bot)}-{_px(top)})"

    def _s_rsi_zone(self, i: int, s: int) -> tuple[bool, str]:
        r = self.rsi[i]
        if r is None:
            return False, "not enough history for RSI"
        lo, hi = float(self.p["rsi_lo"]), float(self.p["rsi_hi"])
        if s < 0:
            lo, hi = 100 - hi, 100 - lo
        ok = lo <= r <= hi
        if r > 70:
            why = "overbought (above 70): avoid chasing"
        elif r < 30:
            why = "oversold (below 30): avoid chasing"
        elif 45 < r < 55:
            why = "reset zone (45-55): not counted"
        else:
            why = "in the continuation zone" if ok else "outside the zone"
        return ok, f"RSI {r:.1f}: {why} ({lo:g}-{hi:g})"

    def _s_macd_cross(self, i: int, s: int) -> tuple[bool, str]:
        line, sig, hist = self.line, self.sig, self.hist
        if i < 1 or None in (line[i], sig[i], hist[i], hist[i - 1]):
            return False, "not enough history for MACD"
        n = int(self.p["cross_bars"])
        ago = None
        for k in range(i, max(1, i - n + 1) - 1, -1):
            if None in (line[k - 1], sig[k - 1]):
                break
            if s * (line[k - 1] - sig[k - 1]) <= 0 < s * (line[k] - sig[k]):  # type: ignore[operator]
                ago = i - k
                break
        expanding = s * (hist[i] - hist[i - 1]) > 0 and s * hist[i] > 0  # type: ignore[operator]
        kind = "bullish" if s > 0 else "bearish"
        if ago is None:
            return False, f"no {kind} MACD crossover in the last {n} bars"
        grow = "histogram expanding" if expanding else "histogram not expanding"
        return expanding, f"{kind} MACD crossover {ago} bars ago, {grow}"

    def _s_obv_trend(self, i: int, s: int) -> tuple[bool, str]:
        n = int(self.p["obv_n"])
        oe = self.obv_ema[i]
        if oe is None or i < n:
            return False, "not enough history for the OBV trend"
        o, c = self.obv, self.closes
        rising = s * (o[i] - oe) > 0 and s * (o[i] - o[i - n]) > 0
        if s > 0:
            diverges = c[i] >= max(c[i - n : i]) and o[i] < max(o[i - n : i])
        else:
            diverges = c[i] <= min(c[i - n : i]) and o[i] > min(o[i - n : i])
        word = "rising" if s > 0 else "falling"
        if diverges:
            return False, f"OBV diverges: price made a new {n}-bar extreme without it"
        return rising, f"OBV {word} over {n} bars" if rising else f"OBV is not {word}"

    def _s_volume_rising(self, i: int, s: int) -> tuple[bool, str]:
        avg = self.vol_avg[i]
        if not avg:
            return False, "not enough history for the volume average"
        ratio = self.cs[i].volume / avg
        need = float(self.p["vol_mult"])
        return (
            ratio > need,
            f"volume {ratio:.2f}x its {self.p['vol_n']}-bar average (need {need:g}x)",
        )

    def _s_vwap(self, i: int, s: int) -> tuple[bool, str]:
        v, a = self.vwap[i], self.atr[i]
        if v is None:
            return False, "not enough history for VWAP"
        d = self.closes[i] - v
        gap = f" ({abs(d) / a:.1f} ATR)" if a else ""
        where = "above" if d > 0 else "below"
        return s * d > 0, f"price {where} the {self.vwap_mode} VWAP {_px(v)}{gap}"

    def _s_fib_zone(self, i: int, s: int) -> tuple[bool, str]:
        r = int(self.p["fib_pivot"])
        a = self.atr[i]
        if a is None:
            return False, "not enough history for the Fibonacci zone"
        # the impulse: last confirmed swing high and the swing low before it (long)
        ends, starts = (self.ph, self.pl) if s > 0 else (self.pl, self.ph)
        ends_i, starts_i = (self.ph_i, self.pl_i) if s > 0 else (self.pl_i, self.ph_i)
        ne = bisect_right(ends_i, i - r)
        if ne == 0:
            return False, "no confirmed impulse yet"
        je, pe = ends[ne - 1]
        ns = bisect_left(starts_i, je)
        if ns == 0 or i - je > 120:
            return False, "no recent impulse to measure"
        _, p0 = starts[ns - 1]
        size = s * (pe - p0)
        if size < float(self.p["fib_min_atr"]) * a:
            return False, f"the last impulse is too small ({size / a:.1f} ATR)"
        bars = self.cs[je + 1 : i + 1]
        ext = min(c.low for c in bars) if s > 0 else max(c.high for c in bars)
        ret = s * (pe - ext) / size
        z382, z618 = pe - s * 0.382 * size, pe - s * 0.618 * size
        tol = 0.1 * a / size
        ok = 0.382 - tol <= ret <= 0.618 + tol and s * (self.closes[i] - z618) > 0
        conf = [
            name
            for name, lv in (("50 EMA", self.ema50[i]), ("VWAP", self.vwap[i]))
            if lv is not None and min(z382, z618) <= lv <= max(z382, z618)
        ]
        detail = (
            f"pullback to the {ret:.3f} retracement of {_px(p0)} -> {_px(pe)} "
            f"(zone {_px(min(z382, z618))}-{_px(max(z382, z618))})"
        )
        if conf:
            detail += ", confluent with the " + " and ".join(conf)
        return ok, detail

    def _s_psar_flip(self, i: int, s: int) -> tuple[bool, str]:
        d = self.psar_dir
        n = int(self.p["psar_bars"])
        word = "bullish" if s > 0 else "bearish"
        if d[i] != s:
            return False, f"Parabolic SAR is not {word}"
        for k in range(i, max(1, i - n + 1) - 1, -1):
            if d[k - 1] != s:
                return True, f"Parabolic SAR flipped {word} {i - k} bars ago"
        return False, f"Parabolic SAR {word}, but the flip is older than {n} bars"

    def _s_tenkan_kijun(self, i: int, s: int) -> tuple[bool, str]:
        tk, kj = self.ichi["tenkan"], self.ichi["kijun"]
        if tk[i] is None or kj[i] is None:
            return False, "not enough history for Tenkan / Kijun"
        n = int(self.p["tk_bars"])
        word = "above" if s > 0 else "below"
        if s * (tk[i] - kj[i]) <= 0:  # type: ignore[operator]
            return False, f"Tenkan is not {word} the Kijun"
        for k in range(i, max(1, i - n + 1) - 1, -1):
            if tk[k - 1] is None or kj[k - 1] is None:
                break
            if s * (tk[k - 1] - kj[k - 1]) <= 0:  # type: ignore[operator]
                return True, f"Tenkan crossed {word} the Kijun {i - k} bars ago"
        return False, f"Tenkan {word} the Kijun, but the cross is older than {n} bars"

    def _s_atr_rising(self, i: int, s: int) -> tuple[bool, str]:
        k = int(self.p["atr_slope"])
        a, nat = self.atr, self.natr
        if i < k or a[i] is None or a[i - k] is None:
            return False, "not enough history for ATR"
        hist = [x for x in nat[max(0, i - 100) : i] if x is not None]
        if len(hist) < 20:
            return False, "not enough history for the ATR baseline"
        med = statistics.median(hist)
        rel = nat[i] / med if med else 0.0  # type: ignore[operator]
        rising = a[i] > a[i - k]  # type: ignore[operator]
        low = rel < float(self.p["min_atr_ratio"])
        if low:
            return False, f"ATR very low ({rel:.2f}x its usual level): a dead market"
        return rising, f"ATR {'rising' if rising else 'falling'}, {rel:.2f}x its usual level"

    # -------------------------------------------------------------- the entry
    def retest(self, i: int, s: int, fib_ok: bool) -> tuple[bool, str]:
        """The entry must come off a pullback to VWAP, the 20 / 50 EMA or a Fibonacci zone."""
        if fib_ok:
            return True, "re-test of the Fibonacci zone"
        n, tol = int(self.p["retest_bars"]), float(self.p["retest_atr"])
        for name, series in (("VWAP", self.vwap), ("20 EMA", self.ema20), ("50 EMA", self.ema50)):
            now = series[i]
            if now is None or s * (self.closes[i] - now) <= 0:
                continue
            for k in range(i, max(0, i - n + 1) - 1, -1):
                lv, a = series[k], self.atr[k]
                if lv is None or a is None:
                    break
                c = self.cs[k]
                if c.low <= lv + tol * a and c.high >= lv - tol * a:
                    back = "above" if s > 0 else "below"
                    return True, f"pullback to the {name} {i - k} bars ago, price back {back} it"
        return False, f"no pullback to VWAP, the 20 / 50 EMA or a Fibonacci zone in {n} bars"

    def decide(
        self,
        i: int,
        s: int,
        *,
        required: Sequence[str] | None,
        min_signals: int,
        use: Sequence[str] | None = None,
        fear_greed: float | None = None,
        lower: Sequence[Candle] | None = None,
    ) -> dict[str, Any]:
        """The full rule set at bar ``i`` for side ``s``: {"ok", "reasons", "factors", ...}."""
        p = self.p
        word = "bullish" if s > 0 else "bearish"
        bias, bias_text, dist = self.bias(i)
        struct, struct_text = self.structure(i)
        sig = self.signals(i, s)
        reasons: list[str] = []
        factors: list[str] = []
        if bias != word:
            reasons.append(f"1D bias is {bias} ({bias_text}): no {_side(s)} against it")
        else:
            factors.append(f"1D bias {word}: {bias_text}")
        if struct != word:
            reasons.append(f"4H structure is {struct} ({struct_text}), not {word}")
        else:
            factors.append(f"4H structure {word}: {struct_text}")
        need = int(min_signals)
        fg_adj = 0
        cautions: list[str] = []
        if fear_greed is not None:
            fg_adj, fg_text, block, extra = fear_greed_view(fear_greed, s)
            need += extra
            if block:
                reasons.append(fg_text)
            elif fg_adj > 0:
                factors.append(fg_text)
            elif extra:
                cautions.append(f"{fg_text}: one more signal is needed")
        req = list(required) if required is not None else list(DEFAULT_REQUIRED)
        missing = [k for k in req if not sig[k][0]]
        for k in missing:
            reasons.append(f"required signal {k} failed: {sig[k][1]}")
        counted = counted_signals(required, use)
        aligned = [k for k in SIGNALS if sig[k][0] and k in counted]
        if len(aligned) < need:
            why = f" ({cautions[0]})" if cautions else ""
            reasons.append(
                f"only {len(aligned)} of {len(counted)} signals aligned (need {need}){why}"
            )
        factors += [f"{_SHORT[k]}: {sig[k][1]}" for k in aligned]
        if p.get("require_retest", True):
            ok, why = self.retest(i, s, sig["fib_zone"][0])
            (factors if ok else reasons).append(why if ok else f"no entry trigger: {why}")
        lower_ok = None
        if lower is not None:
            lower_ok, why = lower_confirmation(lower, s, int(p["lower_bars"]))
            (factors if lower_ok else reasons).append(why)
        a = self.atr[i]
        if a is None or a <= 0:
            reasons.append("no ATR for the stop")
            a = 0.0
        entry = self.closes[i]
        risk = float(p["stop_atr"]) * a
        conf = confidence(
            len(aligned),
            dist,
            self.cs[i].volume / self.vol_avg[i] if self.vol_avg[i] else 1.0,
            self.rsi[i],
            "confluent" in sig["fib_zone"][1] and sig["fib_zone"][0],
            bool(lower_ok),
            fg_adj,
            s,
        )
        return {
            "ok": not reasons,
            "reasons": reasons,
            "factors": factors,
            "signals": {k: {"ok": v[0], "detail": v[1]} for k, v in sig.items()},
            "aligned": aligned,
            "cautions": cautions,
            "bias": bias,
            "market_bias": f"{bias.capitalize()}: {bias_text}; {struct_text}",
            "entry": entry,
            "stop": entry - s * risk,
            "tp1": entry + s * float(p["tp1_rr"]) * risk,
            "tp2": entry + s * float(p["tp2_rr"]) * risk,
            "atr": a,
            "confidence": conf,
        }


_SHORT = {
    "ema200": "200 EMA",
    "ichimoku_cloud": "Ichimoku cloud",
    "rsi_zone": "RSI zone",
    "macd_cross": "MACD crossover",
    "obv_trend": "OBV trend",
    "volume_rising": "Volume",
    "vwap": "VWAP",
    "fib_zone": "Fibonacci zone",
    "psar_flip": "Parabolic SAR",
    "tenkan_kijun": "Tenkan / Kijun",
    "atr_rising": "ATR",
}


def _side(s: int) -> str:
    return "long" if s > 0 else "short"


def _px(x: float) -> str:
    return f"{x:,.2f}" if abs(x) >= 100 else f"{x:.6g}"


def fear_greed_view(value: float, s: int) -> tuple[int, str, bool, int]:
    """(confidence adjustment, explanation, blocks the trade, extra signals needed)."""
    v = float(value)
    if s > 0:
        if v >= 85:
            return (
                -20,
                f"Fear & Greed {v:g} (extreme greed): no new longs, consider shorts",
                True,
                0,
            )
        if v >= 65:
            return -10, f"Fear & Greed {v:g} (greed): caution, it counts against longs", False, 1
        if v <= 25:
            return 5, f"Fear & Greed {v:g} (extreme fear): a bullish opportunity", False, 0
        if v <= 45:
            return 3, f"Fear & Greed {v:g} (fear): accumulation zone", False, 0
        return 0, f"Fear & Greed {v:g}: neutral", False, 0
    if v >= 85:
        return 5, f"Fear & Greed {v:g} (extreme greed): shorts favoured", False, 0
    if v >= 65:
        return 3, f"Fear & Greed {v:g} (greed): supports a short", False, 0
    if v <= 25:
        return -10, f"Fear & Greed {v:g} (extreme fear): counts against shorts", False, 1
    return 0, f"Fear & Greed {v:g}: neutral for a short", False, 0


def confidence(
    aligned: int,
    dist_pct: float,
    vol_ratio: float,
    rsi: float | None,
    fib_confluent: bool,
    lower_ok: bool,
    fg_adj: int,
    s: int,
) -> int:
    """0-100: 60 for the share of the eleven signals aligned, up to 30 for their strength
    (distance from the 1D EMA, volume, RSI in the middle of its zone, Fibonacci confluence,
    lower-timeframe confirmation), plus the Fear & Greed adjustment."""
    score = 60 * aligned / len(SIGNALS)
    score += min(10.0, abs(dist_pct) * 1.5)
    score += max(0.0, min(5.0, (vol_ratio - 1) * 5))
    if rsi is not None:
        mid = 62.5 if s > 0 else 37.5
        score += max(0.0, 5 - abs(rsi - mid))
    score += 5 if fib_confluent else 0
    score += 5 if lower_ok else 0
    return int(max(0, min(100, round(score + fg_adj))))


def lower_confirmation(lower: Sequence[Candle], s: int, n: int = 12) -> tuple[bool, str]:
    """Execution check on 5m / 1m candles: a VWAP reclaim (a close on the wrong side of the
    session VWAP in the last ``n`` candles, the last one back on the right side) and a
    confirmation candle (strong close in the trade's direction, or an engulfing candle)."""
    if len(lower) < 3:
        return False, "lower timeframe: not enough candles for the execution check"
    vw = vwap_session(lower)
    last, prev = lower[-1], lower[-2]
    window = range(max(0, len(lower) - n), len(lower) - 1)
    was_wrong = any(s * (lower[k].close - vw[k]) < 0 for k in window)
    back = s * (last.close - vw[-1]) > 0
    rng = last.high - last.low
    strong = rng > 0 and (
        (last.close - last.low) / rng >= 2 / 3 if s > 0 else (last.high - last.close) / rng >= 2 / 3
    )
    engulf = (
        last.close > prev.open and last.open <= prev.close and prev.close < prev.open
        if s > 0
        else last.close < prev.open and last.open >= prev.close and prev.close > prev.open
    )
    green = s * (last.close - last.open) > 0
    ok = was_wrong and back and green and (strong or engulf)
    if ok:
        return True, "lower timeframe: VWAP reclaimed with a confirmation candle"
    miss = [
        w
        for w, bad in (
            ("no VWAP reclaim", not (was_wrong and back)),
            ("no confirmation candle", not (green and (strong or engulf))),
        )
        if bad
    ]
    return False, "lower timeframe: " + " and ".join(miss)


# ---------------------------------------------------------------------- the lab strategy
def scan(cs: Sequence[Candle], params: Mapping[str, Any]) -> list[Signal]:
    """Long signals (spot) at every bar that passes the rule set, each decided at the close
    of that bar from ``cs[: i + 1]`` only; a signal only on the FIRST bar of a passing run."""
    p = {**DEFAULTS, **params}
    tf = timeframe_to_ms(str(p["timeframe"]))
    if len(cs) < 210:
        return []
    ctx = _Ctx(cs, p, tf)
    req = p["required"]
    out: list[Signal] = []
    prev = False
    for i in range(200, len(cs)):
        if ctx.bias(i)[0] != "bullish" or ctx.structure(i)[0] != "bullish":
            prev = False
            continue
        d = ctx.decide(i, 1, required=req, min_signals=int(p["min_signals"]), use=p["use"])
        fresh, prev = d["ok"] and not prev, d["ok"]
        if not fresh:
            continue
        risk = d["entry"] - d["stop"]
        out.append(
            Signal(
                i,
                cs[i].ts,
                "next_open",
                None,
                d["stop"],
                None,
                stop_atr=risk,
                rr=float(p["tp1_rr"]),
                partial=float(p["tp1_share"]),
                rr2=float(p["tp2_rr"]),
                reason=(
                    f"confluence {len(d['aligned'])}/{len(SIGNALS)} ({', '.join(d['aligned'])}), "
                    f"confidence {d['confidence']}"
                ),
            )
        )
    return out


# ---------------------------------------------------------------------- the trade card
def _fear_greed_at(fear_greed: Any, t: int) -> float | None:
    """A number, or a series of (known_from_ts, value): the latest value known at ``t``."""
    if fear_greed is None:
        return None
    if isinstance(fear_greed, int | float):
        return float(fear_greed)
    pts = sorted((int(a), float(b)) for a, b in fear_greed)
    k = bisect_right([a for a, _ in pts], t) - 1
    return pts[k][1] if k >= 0 else None


def analyze(
    candles: Sequence[Candle],
    *,
    fear_greed: Any = None,
    lower: Sequence[Candle] | None = None,
    required: Sequence[str] | None = None,
    min_signals: int = 5,
    risk_pct: float = 1.0,
    equity: float | None = None,
    now_ms: int | None = None,
    use: Sequence[str] | None = None,
) -> dict[str, Any]:
    """The decision at the last CLOSED candle: a trade card or NO TRADE (see the module doc).

    ``fear_greed``: a value 0-100 or a series of (known_from_ts, value). ``lower``: 5m / 1m
    candles for the execution check. ``required`` / ``min_signals``: the builder's rule set
    (None = the fixed entry list); ``use``: only these signals (plus the required ones) count
    toward ``min_signals``, the others are still shown. ``equity``: adds the position
    ``size`` for ``risk_pct``. ``now_ms``: ignore candles not closed by then.
    """
    p = {
        **DEFAULTS,
        "required": required,
        "use": use,
        "min_signals": min_signals,
        "risk_pct": risk_pct,
    }
    cs = sorted(candles, key=lambda c: c.ts)
    tf = infer_timeframe_ms(cs)
    p["timeframe"] = tf_label(tf).lower()
    check_params(p)
    if now_ms is not None:
        cs = [c for c in cs if c.ts + tf <= now_ms]
    t = cs[-1].ts + tf if cs else 0
    if lower is not None:
        ltf = infer_timeframe_ms(lower) if len(lower) > 1 else tf
        lower = [c for c in sorted(lower, key=lambda c: c.ts) if c.ts + ltf <= t]
    timeframes = ["1D", "4H"] + ([tf_label(tf)] if tf < H4_MS else [])
    if lower is not None:
        timeframes.append(tf_label(infer_timeframe_ms(lower)) if len(lower) > 1 else "lower")
    if len(cs) < 210:
        return _no_trade(["not enough history: at least 200 candles are needed"], {}, "Unknown")
    ctx = _Ctx(cs, p, tf)
    i = len(cs) - 1
    fg = _fear_greed_at(fear_greed, t)
    bias, bias_text, _ = ctx.bias(i)
    side = 1 if bias == "bullish" else -1 if bias == "bearish" else 0
    d = ctx.decide(
        i,
        side or 1,
        required=required,
        min_signals=min_signals,
        use=use,
        fear_greed=fg,
        lower=lower,
    )
    if not side:
        reasons = [f"1D market bias is {bias}: {bias_text}"] + [
            r for r in d["reasons"] if not r.startswith("1D bias")
        ]
        return _no_trade(reasons, d["signals"], d["market_bias"])
    if not d["ok"]:
        return _no_trade(d["reasons"], d["signals"], d["market_bias"])
    entry, stop = d["entry"], d["stop"]
    card: dict[str, Any] = {
        "decision": "trade",
        "trade_type": "Long" if side > 0 else "Short",
        "entry": entry,
        "stop": stop,
        "tp1": d["tp1"],
        "tp2": d["tp2"],
        "rr": round(abs(d["tp2"] - entry) / abs(entry - stop), 2),
        "timeframes": timeframes,
        "confluence": d["factors"],
        "market_bias": d["market_bias"],
        "confidence": d["confidence"],
        "risk_pct": float(risk_pct),
        "signals": d["signals"],
        "aligned": len(d["aligned"]),
        "cautions": d["cautions"],
        "fear_greed": fg,
    }
    if equity is not None:
        amount = float(equity) * float(risk_pct) / 100
        card["risk_amount"] = amount
        card["size"] = amount / abs(entry - stop)
    if side < 0:
        card["note"] = "spot bots only take longs: this short is a setup to watch, not an order"
    return card


def _no_trade(reasons: list[str], signals: dict[str, Any], bias: str) -> dict[str, Any]:
    return {
        "decision": "no_trade",
        "text": NO_TRADE_TEXT,
        "reasons": reasons,
        "signals": signals,
        "market_bias": bias,
    }


def format_card(result: Mapping[str, Any]) -> str:
    """The mandatory output format (a trade card, or the NO TRADE line and its reasons)."""
    if result.get("decision") != "trade":
        lines = [NO_TRADE_TEXT, "Reasons:"]
        lines += [f"- {r}" for r in result.get("reasons") or ["no setup"]]
        lines.append(f"Market Bias: {result.get('market_bias', 'Unknown')}")
        return "\n".join(lines) + "\n"
    lines = [
        f"Trade Type: {result['trade_type']}",
        f"Entry Price: {_px(result['entry'])}",
        f"Stop Loss: {_px(result['stop'])}",
        f"Take Profit (TP1, TP2): {_px(result['tp1'])}, {_px(result['tp2'])}",
        f"Risk:Reward Ratio: 1:{result['rr']:g}",
        f"Timeframes Used: {', '.join(result['timeframes'])}",
        "Confluence Factors:",
    ]
    lines += [f"- {f}" for f in result["confluence"]]
    lines += [
        f"Market Bias: {result['market_bias']}",
        f"Confidence Score (0–100%): {result['confidence']}%",  # noqa: RUF001
    ]
    if result.get("size") is not None:
        lines.append(f"Size: {result['size']:.8g} at {result['risk_pct']:g}% risk")
    if result.get("note"):
        lines.append(f"Note: {result['note']}")
    return "\n".join(lines) + "\n"
