"""Strategy lab strategies and their backtest engine (long only, spot).

Every strategy turns candles into SIGNALS, each decided at the close of bar ``i`` from
``candles[: i + 1]`` only (the lab's look-ahead detector re-runs every strategy on truncated
history and discards any whose past signals change). A signal says how to enter (next bar's
open, or a buy-stop above a price), where the stop is, where the target is, and whether to
take part of the position there and trail the rest.

``nnfx``          No-Nonsense-Forex style trend following (items 51-60): price above the 50
                  SMA baseline; MACD and a Range Filter (two unrelated confirmations) turn up
                  together; a volatility / volume gate keeps it out of chop; stop 1.5 x ATR
                  (never a fixed %); half the position is taken at 2R, the stop moves to
                  break-even and the rest trails 1.5 x ATR below the highest close, so
                  winners run while losers are cut at the stop.

``sneaky_pivot``  15-minute price action (items 41-50): Range High / Low = the previous UTC
                  day's high / low; Swing High / Low = the nearest confirmed structural pivot
                  beyond them. Only at the lower boundary: a sharp impulse candle drops into
                  the Range Low or a Swing Low, the very next candle closes green (the
                  "sneaky pivot"), and a buy-stop sits at that green candle's high. Stop at
                  the lowest wick of the pullback; target at the Range High (the upper
                  boundary). No trades in the middle of the range.

``fib_fvg``       Auction-market pullbacks (items 61-70): after an impulse from a confirmed
                  swing low to a confirmed swing high, entries only inside the discount zone
                  (0.705 - 0.886 retracement); a close below 0.886 cancels the setup. Entry
                  on a liquidity grab (a wick below 0.886 or the prior swing low that closes
                  back inside the zone, green) or on a re-test of an unfilled bullish fair
                  value gap. Optional: footprint absorption + a 400% positive delta expansion
                  (needs collected order flow) and Elliott's hard rules as extra filters.

Every strategy keeps the bot's floor of 2:1 reward-to-risk: a setup whose target is closer
than 2R is skipped.
"""

from __future__ import annotations

import math
import statistics
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .indicators import ema
from .models import Candle


MIN_RR = 2.0
DAY_MS = 86_400_000


@dataclass
class Signal:
    i: int  # the bar whose close produced the signal
    ts: int  # its open time
    entry: str  # "next_open" | "stop" (buy when price trades above ``entry_price``)
    entry_price: float | None
    stop: float  # absolute price (for next_open, recomputed from the fill: see ``stop_atr``)
    target: float | None
    stop_atr: float | None = None  # next_open entries: stop = fill - stop_atr
    rr: float = MIN_RR  # next_open entries: target = fill + rr * (fill - stop)
    partial: float = 0.0  # share of the position sold at the target (0 = all of it)
    trail_atr: float | None = None  # after the partial: trail this far below the best close
    expiry_bars: int = 1
    reason: str = ""

    def key(self) -> tuple[Any, ...]:
        r = lambda x: None if x is None else round(x, 10)  # noqa: E731
        return (self.i, self.ts, self.entry, r(self.entry_price), r(self.stop), r(self.target))


# ---------------------------------------------------------------------- indicators
def sma(xs: Sequence[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(xs)
    s = 0.0
    for i, x in enumerate(xs):
        s += x
        if i >= n:
            s -= xs[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def atr(cs: Sequence[Candle], n: int = 14) -> list[float | None]:
    """Wilder's average true range."""
    out: list[float | None] = [None] * len(cs)
    trs = []
    for i, c in enumerate(cs):
        tr = (
            c.high - c.low
            if i == 0
            else max(c.high - c.low, abs(c.high - cs[i - 1].close), abs(c.low - cs[i - 1].close))
        )
        trs.append(tr)
        if i == n - 1:
            out[i] = sum(trs) / n
        elif i >= n:
            out[i] = (out[i - 1] * (n - 1) + tr) / n  # type: ignore[operator]
    return out


def macd(
    closes: Sequence[float], fast: int = 12, slow: int = 26, sig: int = 9
) -> tuple[list[float | None], list[float | None]]:
    ef, es = ema(closes, fast), ema(closes, slow)
    line = [a - b if a is not None and b is not None else None for a, b in zip(ef, es, strict=True)]
    start = next((i for i, v in enumerate(line) if v is not None), len(line))
    tail = ema([v for v in line[start:]], sig) if start < len(line) else []  # type: ignore[misc]
    signal = [None] * start + tail
    return line, signal


def range_filter(
    closes: Sequence[float], per: int = 100, mult: float = 3.0
) -> tuple[list[float | None], list[int]]:
    """The "Range Filter" (smoothed average range): the line and its direction (+1 / -1 / 0)."""
    diffs = [0.0] + [abs(closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    a1 = ema(diffs, per)
    a1f = [x if x is not None else 0.0 for x in a1]
    sm = ema(a1f, per * 2 - 1)
    rf: list[float | None] = [None] * len(closes)
    direction = [0] * len(closes)
    for i, c in enumerate(closes):
        r = sm[i]
        if r is None or a1[i] is None:
            continue
        r *= mult
        prev = rf[i - 1] if i and rf[i - 1] is not None else c
        rf[i] = max(prev, c - r) if c > prev else min(prev, c + r)
        up = rf[i] > prev  # type: ignore[operator]
        down = rf[i] < prev  # type: ignore[operator]
        direction[i] = 1 if up else -1 if down else (direction[i - 1] if i else 0)
    return rf, direction


def pivots(
    cs: Sequence[Candle], left: int = 3, right: int = 3
) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Fractal pivot highs / lows as (index, price); pivot j is only KNOWN at bar j + right."""
    highs, lows = [], []
    for j in range(left, len(cs) - right):
        hi, lo = cs[j].high, cs[j].low
        if all(hi > cs[k].high for k in range(j - left, j)) and all(
            hi >= cs[k].high for k in range(j + 1, j + right + 1)
        ):
            highs.append((j, hi))
        if all(lo < cs[k].low for k in range(j - left, j)) and all(
            lo <= cs[k].low for k in range(j + 1, j + right + 1)
        ):
            lows.append((j, lo))
    return highs, lows


def elliott_valid(points: Sequence[float]) -> bool:
    """Hard rules of an impulse (6 pivot prices: start, end of waves 1-5, rising):
    wave 2 never retraces all of wave 1, wave 3 is never the shortest, wave 4 never overlaps
    wave 1."""
    if len(points) != 6:
        return False
    p0, p1, p2, p3, p4, p5 = points
    w1, w3, w5 = p1 - p0, p3 - p2, p5 - p4
    if min(w1, w3, w5) <= 0 or p2 <= p0:
        return False
    return w3 >= min(w1, w5) and p4 > p1


# ---------------------------------------------------------------------- strategies
class Strategy:
    name = "base"
    timeframe = "4h"
    description = ""
    defaults: dict[str, Any] = {}
    grid: dict[str, list[Any]] = {}  # the lab's walk-forward chooses among these

    def __init__(self, **params: Any) -> None:
        self.p = {**self.defaults, **params}

    def signals(self, cs: Sequence[Candle], flow: Mapping[int, Any] | None = None) -> list[Signal]:
        raise NotImplementedError


class NNFX(Strategy):
    name = "nnfx"
    timeframe = "4h"
    description = (
        "50 SMA baseline, MACD + Range Filter confirmations, ATR stops, 2R partial, ATR trail"
    )
    defaults = {
        "baseline": 50,
        "atr_n": 14,
        "stop_atr": 1.5,
        "trail_atr": 1.5,
        "rf_per": 50,
        "rf_mult": 2.0,
        "vol_n": 20,
        "min_atr_ratio": 0.8,
        "partial": 0.5,
    }
    grid = {"baseline": [30, 50, 100], "stop_atr": [1.5, 2.0], "rf_mult": [1.5, 2.0, 3.0]}

    def signals(self, cs: Sequence[Candle], flow: Mapping[int, Any] | None = None) -> list[Signal]:
        p = self.p
        closes = [c.close for c in cs]
        base = sma(closes, p["baseline"])
        a = atr(cs, p["atr_n"])
        line, sig = macd(closes)
        _, rdir = range_filter(closes, p["rf_per"], p["rf_mult"])
        vavg = sma([c.volume for c in cs], p["vol_n"])
        natr = [x / c if x is not None and c else None for x, c in zip(a, closes, strict=True)]
        out = []
        for i in range(1, len(cs)):
            if None in (base[i], a[i], line[i], sig[i], line[i - 1], sig[i - 1], vavg[i], natr[i]):
                continue
            up = line[i] > sig[i] and rdir[i] > 0  # type: ignore[operator]
            was = line[i - 1] > sig[i - 1] and rdir[i - 1] > 0  # type: ignore[operator]
            if not up or was or closes[i] <= base[i]:  # type: ignore[operator]
                continue
            hist = [x for x in natr[max(0, i - 100) : i] if x is not None]
            if len(hist) < 20 or natr[i] < p["min_atr_ratio"] * statistics.median(hist):  # type: ignore[operator]
                continue  # chop: volatility far below normal
            if cs[i].volume < vavg[i]:  # type: ignore[operator]
                continue  # no volume behind the move
            out.append(
                Signal(
                    i,
                    cs[i].ts,
                    "next_open",
                    None,
                    closes[i] - p["stop_atr"] * a[i],
                    None,  # type: ignore[operator]
                    stop_atr=p["stop_atr"] * a[i],
                    rr=MIN_RR,
                    partial=p["partial"],  # type: ignore[operator]
                    trail_atr=p["trail_atr"] * a[i],  # type: ignore[operator]
                    reason=(
                        "above the SMA baseline; MACD and Range Filter both turned up; "
                        "volatility and volume gate passed"
                    ),
                )
            )
        return out


class SneakyPivot(Strategy):
    name = "sneaky_pivot"
    timeframe = "15m"
    description = (
        "previous-day range boundaries; impulse into the low, green pivot candle, buy-stop above it"
    )
    defaults = {
        "atr_n": 14,
        "impulse_atr": 1.5,
        "tap_atr": 0.5,
        "pivot": 3,
        "expiry": 4,
        "lower_zone": 0.25,
        "stop_buffer": 0.0005,
    }
    grid = {"impulse_atr": [1.0, 1.5, 2.0], "tap_atr": [0.3, 0.5, 1.0]}

    def signals(self, cs: Sequence[Candle], flow: Mapping[int, Any] | None = None) -> list[Signal]:
        p = self.p
        a = atr(cs, p["atr_n"])
        days: dict[int, tuple[float, float]] = {}
        for c in cs:
            d = c.ts // DAY_MS
            hi, lo = days.get(d, (-math.inf, math.inf))
            days[d] = (max(hi, c.high), min(lo, c.low))
        _, plows = pivots(cs, p["pivot"], p["pivot"])
        out = []
        recent: deque[tuple[int, float]] = deque()  # pivot lows known so far, last 3 days
        nxt = 0
        for i in range(1, len(cs)):
            j, c, g = i - 1, cs[i - 1], cs[i]
            while nxt < len(plows) and plows[nxt][0] + p["pivot"] <= j:
                recent.append(plows[nxt])
                nxt += 1
            prev_day = c.ts // DAY_MS - 1
            while recent and cs[recent[0][0]].ts < (prev_day - 2) * DAY_MS:
                recent.popleft()
            if prev_day not in days or a[j] is None or g.ts // DAY_MS != c.ts // DAY_MS:
                continue
            rh, rl = days[prev_day]
            if not rh > rl:
                continue
            known = [lo for _, lo in recent if lo < rl]
            levels = [rl] + (
                [max(known)] if known else []
            )  # Range Low, then the Swing Low below it
            impulse = c.close < c.open and (c.high - c.low) >= p["impulse_atr"] * a[j]  # type: ignore[operator]
            tapped = [lv for lv in levels if lv - p["tap_atr"] * a[j] <= c.low <= lv * (1 + 0.001)]  # type: ignore[operator]
            if not impulse or not tapped or not g.close > g.open:
                continue
            entry = g.high
            if (entry - rl) / (rh - rl) > p["lower_zone"]:
                continue  # the middle of the chart: no trade
            stop = min(c.low, g.low) * (1 - p["stop_buffer"])
            target = rh
            if entry <= stop or (target - entry) < MIN_RR * (entry - stop):
                continue
            out.append(
                Signal(
                    i,
                    g.ts,
                    "stop",
                    entry,
                    stop,
                    target,
                    expiry_bars=p["expiry"],
                    reason=(
                        f"impulse into the {'Range Low' if tapped[0] == rl else 'Swing Low'} "
                        f"{tapped[0]:.6g}, green pivot candle; buy above {entry:.6g}, "
                        f"target the Range High {rh:.6g}"
                    ),
                )
            )
        return out


class FibFVG(Strategy):
    name = "fib_fvg"
    timeframe = "4h"
    description = (
        "impulse, 0.705-0.886 discount zone, liquidity grab or FVG re-test; 0.886 close cancels"
    )
    defaults = {
        "atr_n": 14,
        "pivot": 3,
        "min_impulse_atr": 4.0,
        "zone_hi": 0.705,
        "zone_lo": 0.886,
        "stop_atr": 0.25,
        "require_orderflow": False,
        "elliott": False,
    }
    grid = {"min_impulse_atr": [3.0, 4.0, 6.0], "pivot": [3, 5]}

    def signals(  # noqa: C901 - one pass over the setup's states
        self, cs: Sequence[Candle], flow: Mapping[int, Any] | None = None
    ) -> list[Signal]:
        p, r = self.p, self.p["pivot"]
        a = atr(cs, p["atr_n"])
        phighs, plows = pivots(cs, r, r)
        out: list[Signal] = []
        setup: dict[str, Any] | None = None
        hi_i = lo_i = 0
        for i in range(len(cs)):
            if a[i] is None:
                continue
            while hi_i < len(phighs) and phighs[hi_i][0] + r <= i:  # a swing high became known
                jh, ph = phighs[hi_i]
                hi_i += 1
                lows_before = [(k, lo) for k, lo in plows if k < jh and k + r <= i]
                if not lows_before:
                    continue
                jl, pl = lows_before[-1]
                if ph - pl >= p["min_impulse_atr"] * a[i]:  # type: ignore[operator]
                    fvgs = [
                        (cs[k - 2].high, cs[k].low)
                        for k in range(jl + 2, jh + 1)
                        if cs[k - 2].high < cs[k].low
                    ]
                    setup = {"jl": jl, "pl": pl, "jh": jh, "ph": ph, "fvgs": fvgs}
            while lo_i < len(plows) and plows[lo_i][0] + r <= i:
                lo_i += 1
            if setup is None or i <= setup["jh"]:
                continue
            pl, ph = setup["pl"], setup["ph"]
            top, bottom = ph - p["zone_hi"] * (ph - pl), ph - p["zone_lo"] * (ph - pl)
            c = cs[i]
            if c.close < bottom or c.high > ph:
                setup = None  # invalidated below 0.886, or the high was taken out
                continue
            if c.low > top or not c.close > c.open:
                continue  # not in the discount zone, or no green reaction candle
            grab = c.low < bottom and c.close > bottom
            prior = [lo for k, lo in plows if setup["jl"] < k < i and k + r <= i]
            grab = grab or bool(prior and c.low < min(prior) < c.close)
            fvg = next(
                (
                    (lo_g, hi_g)
                    for lo_g, hi_g in setup["fvgs"]
                    if c.low <= hi_g and c.close > lo_g and hi_g >= bottom
                ),
                None,
            )
            if not grab and fvg is None:
                continue
            if p["require_orderflow"] and not _absorption_then_expansion(flow, cs, i):
                continue
            if p["elliott"] and not _elliott_ok(phighs, plows, setup["jl"], setup["jh"], i, r):
                continue
            stop = min(c.low, bottom) - p["stop_atr"] * a[i]  # type: ignore[operator]
            entry_est = c.close
            if ph - entry_est < MIN_RR * (entry_est - stop):
                continue
            why = (
                "liquidity grab below the 0.886 / prior swing low"
                if grab
                else "re-test of an unfilled bullish FVG"
            )
            out.append(
                Signal(
                    i,
                    c.ts,
                    "next_open",
                    None,
                    stop,
                    ph,
                    rr=MIN_RR,
                    reason=(
                        f"discount zone {bottom:.6g}-{top:.6g} after the impulse "
                        f"{pl:.6g} -> {ph:.6g}: {why}"
                    ),
                )
            )
            setup = None  # one entry per impulse
        return out


def _absorption_then_expansion(
    flow: Mapping[int, Any] | None, cs: Sequence[Candle], i: int
) -> bool:
    """Footprint (items 64-66): candle i-1 absorbed heavy selling at its low (negative delta,
    no lower close), candle i's delta expanded 400%+ positive."""
    if not flow or i < 1:
        return False
    a, b = flow.get(cs[i - 1].ts), flow.get(cs[i].ts)
    if not a or not b:
        return False
    da, db = float(a.get("delta", 0)), float(b.get("delta", 0))
    return bool(a.get("absorption") or da < 0) and db > 0 and db >= 4 * abs(da)


def _elliott_ok(phighs: Any, plows: Any, jl: int, jh: int, i: int, r: int) -> bool:
    pts = sorted(
        [(k, lo) for k, lo in plows if jl <= k <= jh and k + r <= i]
        + [(k, h) for k, h in phighs if jl < k <= jh and k + r <= i]
    )
    if len(pts) < 6:
        return True  # no clear 5-wave count: the rule does not apply
    return elliott_valid([x for _, x in pts[-6:]])


STRATEGIES: dict[str, type[Strategy]] = {s.name: s for s in (NNFX, SneakyPivot, FibFVG)}


# ---------------------------------------------------------------------- backtest engine
@dataclass
class LabTrade:
    signal_i: int
    entry_i: int
    entry: float
    stop: float
    target: float | None
    qty: float
    risk: float
    exit_i: int | None = None
    exit: float | None = None
    pnl: float = 0.0
    r: float | None = None
    reason: str = ""
    exits: list[tuple[int, float, float, str]] = field(default_factory=list)


@dataclass
class BTResult:
    trades: list[LabTrade]
    equity: list[tuple[int, float]]  # (ts, equity) per bar
    stats: dict[str, Any]


def backtest(  # noqa: C901 - the bar loop: entries, stops, targets, trailing
    cs: Sequence[Candle],
    strategy: Strategy,
    *,
    fee: float = 0.001,
    slip_pct: float = 0.05,
    risk_pct: float = 1.0,
    equity: float = 10_000.0,
    signals: Sequence[Signal] | None = None,
    start_i: int = 0,
    flow: Mapping[int, Any] | None = None,
) -> BTResult:
    """One position at a time; stops are checked before targets inside a bar (worst case);
    entries and exits pay ``fee`` and ``slip_pct``; size = ``risk_pct`` of equity at risk."""
    sigs = sorted(signals if signals is not None else strategy.signals(cs, flow), key=lambda s: s.i)
    slip = slip_pct / 100
    by_i: dict[int, list[Signal]] = {}
    for s in sigs:
        if s.i >= start_i:
            by_i.setdefault(s.i, []).append(s)
    pending: list[Signal] = []
    pos: LabTrade | None = None
    best = 0.0
    trail: float | None = None
    stop = 0.0
    eq = equity
    trades: list[LabTrade] = []
    curve: list[tuple[int, float]] = []
    for i in range(start_i, len(cs)):
        c = cs[i]
        # ---- entries (orders placed at the close of an earlier bar)
        if pos is None:
            for s in list(pending):
                if i > s.i + s.expiry_bars:
                    pending.remove(s)
                    continue
                if s.entry == "next_open":
                    fill = c.open * (1 + slip)
                    st = fill - s.stop_atr if s.stop_atr else s.stop
                    tgt = s.target if s.target is not None else fill + s.rr * (fill - st)
                elif c.high >= s.entry_price:  # type: ignore[operator]
                    fill = max(c.open, s.entry_price) * (1 + slip)  # type: ignore[type-var]
                    st, tgt = s.stop, s.target
                else:
                    continue
                pending.clear()
                if fill <= st or (tgt is not None and tgt - fill < MIN_RR * (fill - st) * 0.999):
                    break  # the fill left no room for 2R: skipped
                risk_amt = eq * risk_pct / 100
                per_unit = (fill - st) + fee * (fill + st)
                qty = min(risk_amt / per_unit, eq / (fill * (1 + fee)))
                pos = LabTrade(s.i, i, fill, st, tgt, qty, qty * per_unit, reason=s.reason)
                eq -= qty * fill * fee
                stop, best, trail = st, c.close, None
                pos_sig = s
                break
        # ---- exits
        if pos is not None:
            left = pos.qty - sum(q for _, q, _, _ in pos.exits)
            if c.low <= stop:
                px = min(c.open, stop) * (1 - slip)
                pos.exits.append((i, left, px, "SL" if trail is None else "TRAIL"))
                left = 0.0
            elif pos.target is not None and c.high >= pos.target and not pos.exits:
                px = max(c.open, pos.target) * (1 - slip)
                part = left * pos_sig.partial if pos_sig.partial and pos_sig.trail_atr else left
                pos.exits.append((i, part, px, "TP"))
                left -= part
                if left > 1e-12:
                    stop = max(stop, pos.entry)  # the rest can no longer lose
                    trail = pos_sig.trail_atr
            if left > 1e-12 and trail is not None:
                best = max(best, c.close)
                stop = max(stop, best - trail)
            if left <= 1e-12:
                gross = sum(q * (px - pos.entry) for _, q, px, _ in pos.exits)
                fees = sum(q * px * fee for _, q, px, _ in pos.exits)
                pos.pnl = gross - fees - pos.qty * pos.entry * fee
                eq += gross - fees
                pos.exit_i = i
                pos.exit = sum(q * px for _, q, px, _ in pos.exits) / pos.qty
                pos.r = pos.pnl / pos.risk if pos.risk else None
                trades.append(pos)
                pos = None
        if pos is None:
            pending.extend(by_i.get(i, []))
        mtm = eq
        if pos is not None:
            left = pos.qty - sum(q for _, q, _, _ in pos.exits)
            realized = sum(q * (px - pos.entry) for _, q, px, _ in pos.exits)
            mtm = eq + realized + left * (c.close - pos.entry)
        curve.append((c.ts, mtm))
    return BTResult(trades, curve, performance(trades, curve, equity))


def performance(
    trades: Sequence[LabTrade], curve: Sequence[tuple[int, float]], start: float
) -> dict[str, Any]:
    if not curve:
        return {"trades": 0}
    daily: dict[int, float] = {}
    for ts, e in curve:
        daily[ts // DAY_MS] = e
    days = sorted(daily)
    vals = [start] + [daily[d] for d in days]
    rets = [vals[k] / vals[k - 1] - 1 for k in range(1, len(vals)) if vals[k - 1] > 0]
    sd = statistics.pstdev(rets) if len(rets) > 1 else 0.0
    sharpe = statistics.fmean(rets) / sd * math.sqrt(365) if sd > 0 else 0.0
    peak, dd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        dd = max(dd, (peak - v) / peak * 100 if peak else 0)
    years = max(len(days) / 365, 1e-9)
    total = vals[-1] / start - 1
    rs = [t.r for t in trades if t.r is not None]
    wins = [t for t in trades if t.pnl > 0]
    gl = -sum(t.pnl for t in trades if t.pnl < 0)
    return {
        "trades": len(trades),
        "win_rate": round(len(wins) / len(trades), 4) if trades else None,
        "avg_r": round(statistics.fmean(rs), 4) if rs else None,
        "profit_factor": round(sum(t.pnl for t in wins) / gl, 3) if gl > 0 else None,
        "return_pct": round(total * 100, 3),
        "cagr_pct": round(((1 + total) ** (1 / years) - 1) * 100, 3) if total > -1 else -100.0,
        "sharpe": round(sharpe, 3),
        "max_dd_pct": round(dd, 3),
        "days": len(days),
    }
