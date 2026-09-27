"""Independent post-hoc audit: did a backtest obey every mandatory rule?

``check_invariants(result, data, events, cfg)`` re-derives each rule from the trade list, the
candles and the news calendar WITHOUT reusing the backtester's, gatekeeper's, breakers' or
news calendar's internals (only ``compute_features`` is shared, for the indicator values),
and returns one human-readable sentence per violation. An empty list means clean. It is
used by the tests, by the research report and for human review of any journal.

Timing conventions used throughout (the same the backtester documents): a trade's
decision time is ``signal_ts + timeframe_ms`` (close of the signal candle = open of the fill
candle); ``exit_ts`` is the OPEN of the candle in which the exit filled, and the fill happens
somewhere inside that candle. Following CONTRACT.md v2 A2, an exit is therefore counted from
its EFFECTIVE time ``t_e = exit_ts + timeframe_ms`` (the exit candle close, the latest moment
it can have filled): at a decision time ``d`` the bot knows exactly the trades with
``t_e <= d``, a bench runs ``[t_e, t_e + bench_hours)`` and the 7-day window holds the trades
with ``t_e`` in ``(d - loss_window, d]``. A trade occupies the portfolio over
``[signal_ts, exit_ts)`` in candle time (from its decision until its exit candle, whose exits
are processed before that candle's entry decisions).

Cost-aware R (CONTRACT.md v2 A1), with fill ``E``, stop ``S``, ``f = fee_rate`` and
``s = slippage_pct / 100``: ``L_u = (E - S*(1-s)) + f*E + f*S*(1-s)`` is the all-in loss per
unit at the stop, recomputed here from the trade row and the config alone.

Checked, per trade: closed; ``stop < entry < target``; NET reward:risk
``((target - entry) - f*(entry + target)) / L_u >= reward_risk`` and PRICE reward:risk
``(target - entry) / (entry - stop) >= reward_risk``, with ``cfg.reward_risk >= 2``;
``risk_pct <= pair cap`` and ``risk_amount == qty * L_u`` (relative 1e-9) and ``risk_pct``
equal to ``risk_amount`` over the realized equity at the decision (size derived from the stop);
stop distance within the configured bounds from the fill; signal window, fill timing and
fill price (next open plus slippage); R1-R4 on the signal candle; R8: the stop sits
``buffer`` below a real low that was a CONFIRMED pivot at decision time (or the fallback
lookback low), strictly below that low, BNB buffer within 0.5-0.8 %; exit on the FIRST
candle that touches stop or target, at the documented price (exits never paused or
delayed); fee/pnl/R arithmetic; outcome in R: a clean stop-loss (exit candle opens above
the stop, fill at ``stop * (1 - s)``) is exactly -1R, a take-profit exactly
+``reward_risk`` R (both within 1e-9), a gap-through stop-loss is at most -1R, and NOTHING
else is below -1R or above +``reward_risk`` R; no data at or after ``end_ts``.

Checked across trades: no pyramiding; BNB never overlaps another cluster position; open
cluster risk never exceeds the shared budget; under a config where one full-size
cluster position exhausts the budget (the default) no two cluster positions overlap; no
entry inside a news blackout (R5); no entry while benched (R9, recomputed from the SL
streaks); every bench lasts at least ``bench_hours`` of real time from the close of the
exit candle of the stop-loss that completed the streak, i.e. the pair's next entry decision
is at least that late (R9, A2); no entry while the 7-day realized loss halt is active (R9,
recomputed from the closed pnl); equity curve and final equity consistent with the trades.

CLI (exit code 1 if any violation is found)::

    python -m research.trendbot.invariants --journal trades.csv --data-dir research/data \
        [--events events.csv] [--start-ts MS] [--end-ts MS]
    python -m research.trendbot.invariants --synthetic planted --seed 1 [--years 6]

Audit a journal against the candles it was traded on (the journal must hold every trade of
the run, since the breakers and the correlation cap are recomputed from it), or run and
audit a backtest of a synthetic world. Both use the default ``StrategyConfig`` and the
backtest timing convention above (a journal of the backtester, whose ``exit_ts`` is the exit
candle open).
"""

from __future__ import annotations

import argparse
import math
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import accumulate

from .backtester import BacktestResult, run_backtest
from .config import MANDATE_BNB_BUFFER_RANGE, MANDATE_MAX_RISK_PCT, MANDATE_MIN_RR, StrategyConfig
from .data import load_dataset
from .indicators import compute_features
from .journal import read_journal
from .models import (
    DAY_MS,
    EXIT_END,
    EXIT_SL,
    EXIT_TP,
    HOUR_MS,
    Candle,
    FeatureRow,
    NewsEvent,
    Trade,
    base_of,
)
from .news import load_events
from .synthetic import WORLDS, make_world


TOL = 1e-9
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _iso(ts: int) -> str:
    return (_EPOCH + timedelta(milliseconds=ts)).strftime("%Y-%m-%d %H:%M")


def _close(a: float, b: float, rel: float = TOL) -> bool:
    return abs(a - b) <= rel * max(abs(a), abs(b), 1e-12)


def _who(t: Trade) -> str:
    return f"trade #{t.trade_id} {t.pair} (signal {_iso(t.signal_ts)})"


def effective_exit(t: Trade, cfg: StrategyConfig) -> int:
    """A2: the exit candle CLOSE ``exit_ts + timeframe_ms``, when a backtest exit is certain."""
    return t.exit_ts + cfg.timeframe_ms  # type: ignore[operator]


def all_in_unit_loss(t: Trade, cfg: StrategyConfig) -> float:
    """A1: ``L_u = (E - S_x) + f*E + f*S_x`` with ``S_x = stop * (1 - slippage)``."""
    stop_x = t.stop * (1.0 - cfg.slippage_pct / 100.0)
    return (t.entry_price - stop_x) + cfg.fee_rate * t.entry_price + cfg.fee_rate * stop_x


@dataclass(slots=True)
class _PairData:
    candles: list[Candle]
    index: dict[int, int]  # candle ts -> index
    rows: list[FeatureRow]


class _Audit:
    """Everything the checks need, prepared once."""

    def __init__(
        self,
        result: BacktestResult,
        data: Mapping[str, Sequence[Candle]],
        events: Iterable[NewsEvent],
        cfg: StrategyConfig,
    ) -> None:
        self.cfg = cfg
        self.result = result
        self.start_ts, self.end_ts = result.window
        self.tf = cfg.timeframe_ms
        self.slip = cfg.slippage_pct / 100.0
        self.trades = list(result.trades)
        self.events = sorted(events, key=lambda e: e.ts)
        self.event_ts = [e.ts for e in self.events]
        self.pairs: dict[str, _PairData] = {}
        for pair in {t.pair for t in self.trades}:
            candles = [c for c in data.get(pair, ()) if self.end_ts is None or c.ts < self.end_ts]
            index = {c.ts: k for k, c in enumerate(candles)}
            self.pairs[pair] = _PairData(candles, index, compute_features(candles, cfg))
        closed = sorted(
            (t for t in self.trades if t.exit_ts is not None and t.pnl is not None),
            key=lambda t: (t.exit_ts, t.trade_id),
        )
        # Effective exit times t_e = exit_ts + tf (A2), ascending.
        self.certain_ts = [effective_exit(t, cfg) for t in closed]
        self.cum_pnl = [0.0, *accumulate(float(t.pnl) for t in closed)]  # type: ignore[arg-type]

    def decision_ts(self, t: Trade) -> int:
        return t.signal_ts + self.tf

    def equity_before(self, ts: int) -> float:
        """Realized equity from the trades known at ``ts`` (``t_e <= ts``)."""
        return self.cfg.starting_capital + self.cum_pnl[bisect_right(self.certain_ts, ts)]

    def window_pnl_before(self, ts: int) -> float:
        """Pnl of trades with ``ts - loss_window < t_e <= ts`` (known at ``ts``)."""
        window_ms = round(self.cfg.loss_window_days * DAY_MS)
        lo = bisect_right(self.certain_ts, ts - window_ms)
        hi = bisect_right(self.certain_ts, ts)
        return self.cum_pnl[hi] - self.cum_pnl[lo] if hi > lo else 0.0

    def halted(self, ts: int) -> tuple[bool, float, float]:
        """(halted, window pnl, threshold) for an entry decision at ``ts``."""
        pnl = self.window_pnl_before(ts)
        threshold = -self.cfg.weekly_loss_limit_pct / 100.0 * self.equity_before(ts)
        return pnl < threshold - TOL * abs(threshold), pnl, threshold


# ---------------------------------------------------------------------------- per trade
def _check_closed(t: Trade) -> list[str]:
    missing = [
        name
        for name in ("exit_ts", "exit_price", "exit_reason", "pnl", "r_multiple")
        if getattr(t, name) is None
    ]
    if missing:
        return [f"{_who(t)} is not closed (missing {', '.join(missing)}): exits must never pause"]
    if t.exit_reason not in (EXIT_SL, EXIT_TP, EXIT_END):
        return [f"{_who(t)} has unknown exit reason {t.exit_reason!r}"]
    return []


def _check_geometry(t: Trade, cfg: StrategyConfig) -> list[str]:
    out: list[str] = []
    entry, stop, target = t.entry_price, t.stop, t.target
    if not stop < entry < target:
        return [f"{_who(t)} violates stop < entry < target ({stop}, {entry}, {target})"]
    rr, fee = cfg.reward_risk, cfg.fee_rate
    unit_loss = all_in_unit_loss(t, cfg)
    net_rr = ((target - entry) - fee * (entry + target)) / unit_loss
    price_rr = (target - entry) / (entry - stop)
    if rr < MANDATE_MIN_RR:
        out.append(f"{_who(t)} was traded with reward_risk {rr:g}, below the mandated 2:1")
    if net_rr < rr * (1.0 - TOL):
        out.append(
            f"{_who(t)} plans a net reward:risk of {net_rr:.6f} after fees and slippage, "
            f"below {rr:g}:1 (min 2:1, A1)"
        )
    if price_rr < rr * (1.0 - TOL):
        out.append(f"{_who(t)} plans a price reward:risk of {price_rr:.6f}, below {rr:g}:1 (min 2)")
    base = base_of(t.pair)
    cap = min(cfg.risk_for(t.pair).max_risk_pct, MANDATE_MAX_RISK_PCT.get(base, 0.0))
    if t.risk_pct > cap + TOL:
        out.append(f"{_who(t)} risks {t.risk_pct:.6f}% above the {cap:g}% cap (R7)")
    if not (t.qty > 0 and _close(t.risk_amount, t.qty * unit_loss)):
        out.append(
            f"{_who(t)} risk_amount {t.risk_amount} != qty * all-in loss per unit "
            f"{t.qty * unit_loss} (stop fill, slippage and both fees; R7, A1)"
        )
    dist = (entry - stop) / entry * 100.0
    lo, hi = cfg.min_stop_distance_pct, cfg.max_stop_distance_pct
    if not lo - TOL <= dist <= hi + TOL:
        out.append(f"{_who(t)} stop distance {dist:.4f}% from the fill is outside [{lo}, {hi}]%")
    return out


def _check_timing(t: Trade, a: _Audit) -> list[str]:
    out: list[str] = []
    if a.start_ts is not None and t.signal_ts < a.start_ts:
        out.append(f"{_who(t)} signals before the window start {_iso(a.start_ts)}")
    if a.end_ts is not None and t.signal_ts >= a.end_ts:
        out.append(f"{_who(t)} signals at/after the window end {_iso(a.end_ts)}")
    if t.entry_ts != a.decision_ts(t):
        out.append(f"{_who(t)} fills at {_iso(t.entry_ts)}, not at the signal close")
    if t.exit_ts is not None:
        if t.exit_ts < t.entry_ts:
            out.append(f"{_who(t)} exits at {_iso(t.exit_ts)} before its entry")
        if a.end_ts is not None and t.exit_ts >= a.end_ts:
            out.append(f"{_who(t)} exits at {_iso(t.exit_ts)}, using data at/after end_ts")
    pd = a.pairs[t.pair]
    fill = pd.index.get(t.entry_ts)
    if fill is None or t.signal_ts not in pd.index:
        return [*out, f"{_who(t)} has no signal/fill candle in the data"]
    expected = pd.candles[fill].open * (1.0 + a.slip)
    if not _close(t.entry_price, expected):
        out.append(f"{_who(t)} entry {t.entry_price} != next open plus slippage {expected}")
    return out


def _check_gates(t: Trade, a: _Audit) -> list[str]:
    pd = a.pairs[t.pair]
    i = pd.index.get(t.signal_ts)
    if i is None:
        return []  # reported by _check_timing
    row, cfg = pd.rows[i], a.cfg
    fast, slow, reg, rsi, vol = row.ema_fast, row.ema_slow, row.ema_regime, row.rsi, row.vol_ratio
    out: list[str] = []
    if fast is None or slow is None or not (row.close > fast and row.close > slow and fast > slow):
        out.append(f"{_who(t)} entered without the R1 trend (close > EMA9 > EMA21)")
    if rsi is None or not cfg.rsi_min <= rsi <= cfg.rsi_max:
        out.append(f"{_who(t)} entered with RSI {rsi} outside [{cfg.rsi_min}, {cfg.rsi_max}] (R2)")
    if vol is None or not vol >= cfg.vol_mult:
        out.append(f"{_who(t)} entered with volume ratio {vol} below {cfg.vol_mult} (R3)")
    if cfg.regime_filter and (reg is None or not row.close > reg):
        out.append(f"{_who(t)} entered below EMA200 with the regime filter on (R4)")
    return out


def _is_confirmed_pivot(candles: Sequence[Candle], j: int, i: int, k: int) -> bool:
    if j < k or j + k > i:
        return False  # not confirmed by the close of the signal candle
    low = candles[j].low
    return all(low < candles[j - m].low and low < candles[j + m].low for m in range(1, k + 1))


def _check_structure(t: Trade, a: _Audit) -> list[str]:
    pd, cfg = a.pairs[t.pair], a.cfg
    i = pd.index.get(t.signal_ts)
    if i is None:
        return []
    buffer = cfg.risk_for(t.pair).stop_buffer_pct
    out: list[str] = []
    blo, bhi = MANDATE_BNB_BUFFER_RANGE
    if base_of(t.pair) == "BNB" and not blo <= buffer <= bhi:
        out.append(f"{_who(t)} uses a BNB stop buffer {buffer}% outside [{blo}, {bhi}]% (R8)")
    candles, k = pd.candles, cfg.swing_pivot_k
    oldest = max(0, i - max(cfg.swing_lookback, cfg.fallback_lookback))
    fb_lo = max(0, i - cfg.fallback_lookback + 1)
    fb_min = min(c.low for c in candles[fb_lo : i + 1])
    for j in range(i, oldest - 1, -1):
        low = candles[j].low
        if not (t.stop < low and _close(t.stop, low * (1.0 - buffer / 100.0), 1e-12)):
            continue
        pivot = (
            j >= i - cfg.swing_lookback
            and low < candles[i].close
            and _is_confirmed_pivot(candles, j, i, k)
        )
        lookback = j >= fb_lo and low == fb_min
        if (t.stop_method == "pivot" and pivot) or (t.stop_method == "lookback_low" and lookback):
            return out
    out.append(
        f"{_who(t)} stop {t.stop:.8g} ({t.stop_method}) is not {buffer:g}% below a swing low "
        "that was confirmed at decision time (R8)"
    )
    return out


def _touch(c: Candle, stop: float, target: float) -> str | None:
    if c.open <= stop or c.low <= stop:
        return EXIT_SL
    if c.high >= target:
        return EXIT_TP
    return None


def _expected_exit_price(c: Candle, reason: str, t: Trade, slip: float) -> float:
    if reason == EXIT_TP:
        return t.target
    if reason == EXIT_END:
        return c.close * (1.0 - slip)
    return (c.open if c.open <= t.stop else t.stop) * (1.0 - slip)


def _check_exit(t: Trade, a: _Audit) -> list[str]:
    pd = a.pairs[t.pair]
    e, x = pd.index.get(t.entry_ts), pd.index.get(t.exit_ts or -1)
    if e is None or x is None or x < e:
        return [] if t.exit_ts is None else [f"{_who(t)} exit candle is not in the data"]
    for j in range(e, x):
        hit = _touch(pd.candles[j], t.stop, t.target)
        if hit is not None:
            return [
                f"{_who(t)} exit delayed: {hit} level touched at {_iso(pd.candles[j].ts)} but the "
                f"exit is recorded at {_iso(pd.candles[x].ts)} (exits are never paused)"
            ]
    c = pd.candles[x]
    hit = _touch(c, t.stop, t.target)
    reason = hit if hit is not None else (EXIT_END if x == len(pd.candles) - 1 else None)
    if reason != t.exit_reason:
        return [f"{_who(t)} recorded exit {t.exit_reason} but candle {_iso(c.ts)} implies {reason}"]
    price = _expected_exit_price(c, reason, t, a.slip)
    if t.exit_price is None or not _close(t.exit_price, price):
        return [f"{_who(t)} exit price {t.exit_price} != {price} implied by the exit model"]
    return []


def _check_accounting(t: Trade, a: _Audit) -> list[str]:
    if t.exit_price is None or t.pnl is None or t.r_multiple is None:
        return []
    fee = a.cfg.fee_rate
    fees = fee * t.qty * t.entry_price + fee * t.qty * t.exit_price
    pnl = t.qty * (t.exit_price - t.entry_price) - fees
    out: list[str] = []
    scale = max(t.risk_amount, 1e-12)
    if abs(t.fees - fees) > TOL * scale or abs(t.pnl - pnl) > TOL * scale:
        out.append(f"{_who(t)} fees/pnl {t.fees}/{t.pnl} != {fees}/{pnl} (fee on both legs)")
    if abs(t.r_multiple - pnl / t.risk_amount) > TOL:
        out.append(f"{_who(t)} r_multiple {t.r_multiple} != pnl / planned risk")
    equity = a.equity_before(a.decision_ts(t))
    if not _close(t.risk_pct, t.risk_amount / equity * 100.0, 1e-7):
        out.append(f"{_who(t)} risk_pct {t.risk_pct} is not risk_amount / realized equity {equity}")
    return out


def _check_r_outcome(t: Trade, a: _Audit) -> list[str]:
    """A1 in R: clean SL exactly -1, TP exactly +reward_risk; only gap-through SLs below -1R."""
    r, x = t.r_multiple, a.pairs[t.pair].index.get(t.exit_ts or -1)
    if r is None or x is None:
        return []  # missing exits / candles are reported by _check_closed / _check_exit
    rr, reason = a.cfg.reward_risk, t.exit_reason
    gap = reason == EXIT_SL and a.pairs[t.pair].candles[x].open <= t.stop
    out: list[str] = []
    if reason == EXIT_TP and abs(r - rr) > TOL:
        out.append(f"{_who(t)} take-profit made {r:.12f}R, not exactly +{rr:g}R net of costs (A1)")
    if reason == EXIT_SL and not gap and abs(r + 1.0) > TOL:
        out.append(f"{_who(t)} clean stop-loss made {r:.12f}R, not exactly -1R all-in (A1)")
    if gap and r > -1.0 + TOL:
        out.append(f"{_who(t)} gap-through stop-loss made {r:.12f}R, better than -1R (A1)")
    if r < -1.0 - TOL and not gap:
        out.append(f"{_who(t)} lost {r:.6f}R, below -1R without a gap through the stop (A1)")
    if r > rr + TOL and reason != EXIT_TP:
        out.append(f"{_who(t)} made {r:.6f}R, above the +{rr:g}R take-profit (A1)")
    return out


# ---------------------------------------------------------------------------- portfolio
def _full_size_blocks_cluster(cfg: StrategyConfig) -> bool:
    """True if one full-size cluster position leaves less than the minimum trade budget."""
    if cfg.expectancy_guard:
        return False
    caps = [cfg.pair_risk[b].max_risk_pct for b in cfg.correlated_cluster if b in cfg.pair_risk]
    return bool(caps) and all(
        cfg.cluster_risk_budget_pct - cap < cfg.min_trade_risk_pct - 1e-12 for cap in caps
    )


def _pair_conflict(a: Trade, b: Trade, cfg: StrategyConfig, strict: bool) -> str | None:
    ba, bb = base_of(a.pair), base_of(b.pair)
    both = f"trades #{a.trade_id} {a.pair} and #{b.trade_id} {b.pair}"
    if ba == bb:
        return f"{both} overlap in the same asset (no pyramiding, R6)"
    cluster = set(cfg.correlated_cluster)
    if ba not in cluster or bb not in cluster:
        return None
    if ba in cfg.exclusive_bases or bb in cfg.exclusive_bases:
        return f"{both} overlap although BNB never stacks with another cluster position (R6)"
    if strict:
        return f"{both} overlap although one full-size position uses the whole budget (R6)"
    return None


def _check_portfolio(a: _Audit) -> list[str]:
    cfg = a.cfg
    strict = _full_size_blocks_cluster(cfg)
    cluster = set(cfg.correlated_cluster)
    trades = sorted(
        (t for t in a.trades if t.exit_ts is not None), key=lambda t: (t.signal_ts, t.trade_id)
    )
    out: list[str] = []
    active: list[Trade] = []
    for t in trades:
        active = [o for o in active if o.exit_ts > t.signal_ts]  # type: ignore[operator]
        for o in active:
            msg = _pair_conflict(o, t, cfg, strict)
            if msg is not None:
                out.append(msg)
        active.append(t)
        used = math.fsum(o.risk_pct for o in active if base_of(o.pair) in cluster)
        if used > cfg.cluster_risk_budget_pct + TOL:
            out.append(
                f"open cluster risk {used:.6f}% exceeds the shared "
                f"{cfg.cluster_risk_budget_pct:g}% budget when {_who(t)} opens (R6)"
            )
    return out


def _blackout(ev: NewsEvent, base: str, ts: int, cfg: StrategyConfig) -> float | None:
    """Blackout half-width (hours) ``ev`` imposes on ``base`` at ``ts``, if inside it."""
    scope = ev.scope.strip()
    _head, sep, tail = scope.partition(":")
    scope = f"EXCHANGE:{tail.strip().lower()}" if sep else scope.upper()
    dist_h = abs(ev.ts - ts) / HOUR_MS
    high_scopes = {"ALL", base, f"EXCHANGE:{cfg.exchange_id.lower()}"}
    if ev.impact.strip().lower() == "high" and scope in high_scopes:
        if dist_h <= cfg.news_blackout_hours:
            return cfg.news_blackout_hours
    bnb_kinds = {k.lower() for k in cfg.bnb_event_kinds}
    if base == "BNB" and ev.kind.strip().lower() in bnb_kinds:
        if scope in {"ALL", "BNB", "EXCHANGE:binance"} and dist_h <= cfg.bnb_event_blackout_hours:
            return cfg.bnb_event_blackout_hours
    return None


def _check_news(a: _Audit) -> list[str]:
    cfg = a.cfg
    widest = max(cfg.news_blackout_hours, cfg.bnb_event_blackout_hours) * HOUR_MS
    out: list[str] = []
    for t in a.trades:
        ts, base = a.decision_ts(t), base_of(t.pair)
        lo, hi = bisect_left(a.event_ts, ts - widest), bisect_right(a.event_ts, ts + widest)
        for ev in a.events[lo:hi]:
            hours = _blackout(ev, base, ts, cfg)
            if hours is not None:
                out.append(
                    f"{_who(t)} entered at {_iso(ts)} inside the +/-{hours:g}h blackout of the "
                    f"{ev.impact} {ev.kind} event at {_iso(ev.ts)} ({ev.scope}) (R5)"
                )
                break
    return out


def benches(trades: Sequence[Trade], cfg: StrategyConfig) -> dict[str, list[tuple[int, int]]]:
    """Recomputed R9 benches: pair -> [(start, until)] from consecutive-SL streaks.

    ``start`` is the effective exit time ``t_e = exit_ts + timeframe_ms`` (the exit candle
    close, A2) of the stop-loss that completed the streak; the bench covers
    ``start <= ts < until = start + bench_hours``.
    """
    bench_ms = round(cfg.bench_hours * HOUR_MS)
    out: dict[str, list[tuple[int, int]]] = {}
    streak: dict[str, int] = {}
    closed = sorted(
        (t for t in trades if t.exit_ts is not None), key=lambda t: (t.exit_ts, t.trade_id)
    )
    for t in closed:
        if t.exit_reason != EXIT_SL:
            streak[t.pair] = 0
            continue
        streak[t.pair] = streak.get(t.pair, 0) + 1
        if streak[t.pair] >= cfg.consecutive_sl_limit:
            start = effective_exit(t, cfg)
            out.setdefault(t.pair, []).append((start, start + bench_ms))
            streak[t.pair] = 0
    return out


def _check_benches(a: _Audit, all_benches: Mapping[str, list[tuple[int, int]]]) -> list[str]:
    out: list[str] = []
    for t in a.trades:
        ts = a.decision_ts(t)
        for start, until in all_benches.get(t.pair, ()):
            if start <= ts < until:
                out.append(
                    f"{_who(t)} entered at {_iso(ts)} while {t.pair} was benched from "
                    f"{_iso(start)} until {_iso(until)} after consecutive stop-losses (R9)"
                )
    return out


def _check_bench_duration(a: _Audit, all_benches: Mapping[str, list[tuple[int, int]]]) -> list[str]:
    """A2: after a streak-completing stop-loss, the pair's NEXT entry decision comes at least
    ``bench_hours`` after the exit candle close, so >= 24h of real time wherever inside that
    candle the stop filled.

    "Next" = the first decision strictly after the exit candle OPEN ``start - timeframe``: a
    stop-loss hit on its own fill candle was DECIDED at that open, and any other same-pair
    decision at or before it would overlap the open trade (reported as pyramiding).
    """
    bench_ms = round(a.cfg.bench_hours * HOUR_MS)
    out: list[str] = []
    for pair, spans in all_benches.items():
        decisions = sorted(a.decision_ts(t) for t in a.trades if t.pair == pair)
        for start, _until in spans:
            k = bisect_right(decisions, start - a.tf)
            if k < len(decisions) and decisions[k] - start < bench_ms:
                out.append(
                    f"{pair} was benched too briefly: its next entry was decided at "
                    f"{_iso(decisions[k])}, {(decisions[k] - start) / HOUR_MS:g}h after the exit "
                    f"candle close {_iso(start)} of the stop-loss that completed the streak, "
                    f"less than the {a.cfg.bench_hours:g}h bench (R9)"
                )
    return out


def _check_halt(a: _Audit) -> list[str]:
    out: list[str] = []
    for t in a.trades:
        ts = a.decision_ts(t)
        halted, pnl, threshold = a.halted(ts)
        if halted:
            out.append(
                f"{_who(t)} entered at {_iso(ts)} while the trailing "
                f"{a.cfg.loss_window_days:g}-day realized pnl {pnl:.2f} was below the halt "
                f"limit {threshold:.2f} (R9)"
            )
    return out


def _check_equity(a: _Audit) -> list[str]:
    res, out = a.result, []
    ids = [t.trade_id for t in a.trades]
    if len(set(ids)) != len(ids):
        out.append("duplicate trade ids in the result")
    exits = [t.exit_ts for t in a.trades if t.exit_ts is not None]
    if exits != sorted(exits):
        out.append("result.trades is not in exit order")
    total = math.fsum(float(t.pnl) for t in a.trades if t.pnl is not None)
    if not _close(res.final_equity, a.cfg.starting_capital + total, 1e-9):
        out.append(f"final equity {res.final_equity} != start + sum(pnl) {total}")
    if len(res.equity_curve) != len(a.trades):
        out.append("equity curve does not have one point per closed trade")
    elif res.equity_curve and not _close(res.equity_curve[-1][1], res.final_equity):
        out.append("equity curve does not end at the final equity")
    return out


# ---------------------------------------------------------------------------- public API
def blocked_open_trades(result: BacktestResult, cfg: StrategyConfig | None = None) -> list[Trade]:
    """Trades that were OPEN at a decision time while their pair was benched or all entries
    were halted (recomputed). Their exits must still be on time: see ``check_invariants``.
    """
    cfg = cfg or result.cfg
    audit = _Audit(result, {}, (), cfg)
    all_benches = benches(result.trades, cfg)
    out: list[Trade] = []
    for t in result.trades:
        if t.exit_ts is None:
            continue
        for ts in range(t.entry_ts + cfg.timeframe_ms, t.exit_ts + 1, cfg.timeframe_ms):
            bench = any(s <= ts < u for s, u in all_benches.get(t.pair, ()))
            if bench or audit.halted(ts)[0]:
                out.append(t)
                break
    return out


def check_invariants(
    result: BacktestResult,
    data: Mapping[str, Sequence[Candle]],
    events: Iterable[NewsEvent] = (),
    cfg: StrategyConfig | None = None,
) -> list[str]:
    """One sentence per rule violation found in ``result`` (empty list = clean)."""
    audit = _Audit(result, data, events, cfg or result.cfg)
    out: list[str] = []
    for t in audit.trades:
        if t.pair not in audit.pairs or not audit.pairs[t.pair].candles:
            out.append(f"{_who(t)} has no candle data")
            continue
        closed = _check_closed(t)
        out.extend(closed)
        out.extend(_check_geometry(t, audit.cfg))
        out.extend(_check_timing(t, audit))
        out.extend(_check_gates(t, audit))
        out.extend(_check_structure(t, audit))
        if not closed:
            out.extend(_check_exit(t, audit))
            out.extend(_check_accounting(t, audit))
            out.extend(_check_r_outcome(t, audit))
    out.extend(_check_portfolio(audit))
    out.extend(_check_news(audit))
    all_benches = benches(audit.trades, audit.cfg)
    out.extend(_check_benches(audit, all_benches))
    out.extend(_check_bench_duration(audit, all_benches))
    out.extend(_check_halt(audit))
    out.extend(_check_equity(audit))
    return out


# ---------------------------------------------------------------------------- CLI
def result_from_journal(
    trades: Iterable[Trade],
    cfg: StrategyConfig,
    window: tuple[int | None, int | None] = (None, None),
    news_loaded: bool = False,
) -> BacktestResult:
    """Wrap journal rows as a ``BacktestResult`` (exit order, equity curve from the pnl)."""
    closed = sorted(trades, key=lambda t: (t.exit_ts is None, t.exit_ts or 0, t.trade_id))
    equity, curve = cfg.starting_capital, []
    for t in closed:
        if t.exit_ts is not None and t.pnl is not None:
            equity += t.pnl
            curve.append((t.exit_ts, equity))
    return BacktestResult(closed, Counter(), [], curve, equity, cfg, news_loaded, window)


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m research.trendbot.invariants",
        description="Independently audit a trade list against every mandatory rule.",
    )
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--journal", help="trade journal CSV (journal.write_journal format)")
    src.add_argument("--synthetic", choices=WORLDS, help="run and audit a synthetic world")
    p.add_argument("--data-dir", help="candle CSV directory (required with --journal)")
    p.add_argument("--events", help="news calendar CSV (time_utc,scope,impact,kind,note)")
    p.add_argument("--start-ts", type=int, default=None, help="window start used by the run")
    p.add_argument("--end-ts", type=int, default=None, help="window end used by the run")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--years", type=float, default=6.0)
    p.add_argument("--timeframe", default="4h")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    cfg = StrategyConfig()
    events: list[NewsEvent] = load_events(args.events) if args.events else []
    window = (args.start_ts, args.end_ts)
    if args.synthetic:
        data, events = make_world(args.synthetic, args.seed, years=args.years)
        result = run_backtest(data, cfg, events, start_ts=window[0], end_ts=window[1])
        label = f"synthetic {args.synthetic} seed {args.seed}"
    else:
        if not args.data_dir:
            parser.error("--data-dir is required with --journal")
        trades = read_journal(args.journal)
        data = load_dataset(args.data_dir, sorted({t.pair for t in trades}), args.timeframe)
        result = result_from_journal(trades, cfg, window, bool(events))
        label = f"journal {args.journal}"
    violations = check_invariants(result, data, events, cfg)
    print(f"{label}: {len(result.trades)} trades audited, {len(violations)} violation(s).")
    for v in violations:
        print(f"- {v}")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
