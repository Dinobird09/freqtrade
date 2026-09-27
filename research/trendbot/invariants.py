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
else is below -1R or above +``reward_risk`` R; no data at or after ``end_ts``. Under the D4
stop-fill stress (``cfg.stop_fill_wick_k = k > 0``, test-only configs) a non-gap stop fills
at ``(stop - k * (stop - low)) * (1 - s)``: such a stop-loss must then be at most -1R, and a
loss below -1R without a gap is accepted ONLY for these stressed stop-losses.

Checked across trades (``cross_trade_violations``, which needs only the journal rows and
the config, so it also audits a live / testnet journal of real fill times): no pyramiding;
BNB never overlaps another cluster position; open cluster risk never exceeds the shared
budget; while a cluster position that took its full R7 cap leaves less than
``min_trade_risk_pct`` of the budget (BTC or ETH under the default config), no other
cluster position overlaps it; no entry inside a news blackout (R5, CONTRACT v3 C2: an
applicable event blocks ``[max(ts - w, known_from), ts + w]``, with ``known_from`` the
event's ``known_from_ts`` or its kind default, -inf for scheduled kinds and ``ts`` for
unscheduled ones; recomputed from the raw events, only the kind lists come from
``news.py``); no entry while benched (R9, recomputed from the SL streaks); every bench
lasts at least ``bench_hours`` from the moment the stop-loss that completed the streak
was certain, i.e. the pair's next entry decision is at least that late (R9, A2); no entry
while the 7-day realized loss halt is active (R9, recomputed from the closed pnl). The
backtest audit adds: equity curve and final equity consistent with the trades.

Live / testnet journals (``live_journal_violations``, CLI ``--live-journal``; CONTRACT v4
D5): the journal holds REAL fill times, so every exit counts from ``exit_ts`` itself (exit
offset 0), open positions are legal, and the backtest fill-price identities (entry = next
open plus slippage, exit = modelled price, exact -1R / +RR outcomes, fees = fee_rate on both
legs) are NOT asserted. Checked instead, per trade: R1-R4 and R8 re-derived at its
``signal_ts`` from the supplied candles (the signal candle must be in the data); the fill
lies in ``[signal close, signal close + timeframe)`` (never before the decision, never on a
stale one); the planned geometry (stop < entry < target, net and price reward:risk >=
``reward_risk``, ``risk_amount == qty * L_u``, stop distance within bounds, the R7 cap) and
the R7 cap measured on the realized equity at the fill; exits never paused (no candle lying
wholly between the fill and the recorded exit, or after the fill of a still-open trade,
reached the stop (low <= stop) or traded through the target (high > target)); the pnl / R
arithmetic of the recorded fees. Across trades: R6 and R9 at offset 0, and R5 only when
events are supplied (without them R5 is not journal-verifiable and the CLI says so).

CLI (exit code 1 if any violation is found)::

    python -m research.trendbot.invariants --journal trades.csv --data-dir research/data \
        [--events events.csv] [--start-ts MS] [--end-ts MS] \
        [--config overrides.json] [--fee-rate F] [--slippage-pct S]
    python -m research.trendbot.invariants --live-journal testnet.csv --data-dir DIR \
        [--events events.csv] [--starting-equity X] [--config ...] [--fee-rate F] ...
    python -m research.trendbot.invariants --synthetic planted --seed 1 [--years 6] [--gaps]

Audit a journal against the candles it was traded on (the journal must hold every trade of
the run, since the breakers and the correlation cap are recomputed from it), or run and
audit a backtest of a synthetic world. Both use the backtest timing convention above (a
journal of the backtester, whose ``exit_ts`` is the exit candle open). The config is the
default ``StrategyConfig``, then the JSON overrides of ``--config`` (validated by
``adoption.config_from_overrides``, so a loosened rule is refused), then ``--fee-rate`` /
``--slippage-pct``: a journal written with non-default costs or by a discovery variant
(e.g. ``{"reward_risk": 2.5}``) must be audited with the config that produced it, or its
exact -1R / +RR outcomes and fill prices are (correctly) reported as inconsistent.
"""

from __future__ import annotations

import argparse
import json
import math
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import accumulate
from pathlib import Path

from .backtester import BacktestResult, run_backtest
from .config import (
    MANDATE_BNB_BUFFER_RANGE,
    MANDATE_MAX_RISK_PCT,
    MANDATE_MIN_RR,
    ConfigError,
    StrategyConfig,
)
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
from .news import UNSCHEDULED_KINDS, load_events
from .synthetic import WORLDS, make_world


TOL = 1e-9
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _iso(ts: int) -> str:
    return (_EPOCH + timedelta(milliseconds=ts)).strftime("%Y-%m-%d %H:%M")


def _close(a: float, b: float, rel: float = TOL) -> bool:
    return abs(a - b) <= rel * max(abs(a), abs(b), 1e-12)


def _who(t: Trade) -> str:
    return f"trade #{t.trade_id} {t.pair} (signal {_iso(t.signal_ts)})"


def _offset(cfg: StrategyConfig, exit_time_uncertainty_ms: int | None) -> int:
    """The A2 exit-time offset: ``None`` -> ``cfg.timeframe_ms`` (backtest journals)."""
    if exit_time_uncertainty_ms is None:
        return cfg.timeframe_ms
    if isinstance(exit_time_uncertainty_ms, bool) or not isinstance(exit_time_uncertainty_ms, int):
        raise ValueError(
            f"exit_time_uncertainty_ms must be an int, got {exit_time_uncertainty_ms!r}"
        )
    if exit_time_uncertainty_ms < 0:
        raise ValueError(f"exit_time_uncertainty_ms must be >= 0, got {exit_time_uncertainty_ms}")
    return exit_time_uncertainty_ms


def effective_exit(
    t: Trade, cfg: StrategyConfig, exit_time_uncertainty_ms: int | None = None
) -> int:
    """A2: when the exit is certain, ``exit_ts + offset``. The default offset is the timeframe
    (a backtest ``exit_ts`` is the exit candle OPEN); a live journal of real fills uses 0."""
    return t.exit_ts + _offset(cfg, exit_time_uncertainty_ms)  # type: ignore[operator]


def all_in_unit_loss(t: Trade, cfg: StrategyConfig) -> float:
    """A1: ``L_u = (E - S_x) + f*E + f*S_x`` with ``S_x = stop * (1 - slippage)``."""
    stop_x = t.stop * (1.0 - cfg.slippage_pct / 100.0)
    return (t.entry_price - stop_x) + cfg.fee_rate * t.entry_price + cfg.fee_rate * stop_x


def decision_time(t: Trade, cfg: StrategyConfig) -> int:
    """When the entry was decided: the signal candle close ``signal_ts + timeframe_ms``."""
    return t.signal_ts + cfg.timeframe_ms


class _Ledger:
    """Realized pnl ordered by the time each exit became certain (``t_e = exit_ts + offset``)."""

    def __init__(
        self,
        trades: Iterable[Trade],
        cfg: StrategyConfig,
        offset: int,
        starting_equity: float | None = None,
    ) -> None:
        self.cfg = cfg
        self.offset = offset
        self.start = cfg.starting_capital if starting_equity is None else float(starting_equity)
        closed = sorted(
            (t for t in trades if t.exit_ts is not None and t.pnl is not None),
            key=lambda t: (t.exit_ts, t.trade_id),
        )
        self.certain_ts = [t.exit_ts + offset for t in closed]  # type: ignore[operator]
        self.cum_pnl = [0.0, *accumulate(float(t.pnl) for t in closed)]  # type: ignore[arg-type]

    def equity_before(self, ts: int) -> float:
        """Realized equity from the trades known at ``ts`` (``t_e <= ts``)."""
        return self.start + self.cum_pnl[bisect_right(self.certain_ts, ts)]

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


@dataclass(slots=True)
class _PairData:
    candles: list[Candle]
    index: dict[int, int]  # candle ts -> index
    rows: list[FeatureRow]


class _Audit:
    """Everything the per-trade checks need, prepared once (backtest timing: offset = tf)."""

    def __init__(
        self,
        result: BacktestResult,
        data: Mapping[str, Sequence[Candle]],
        cfg: StrategyConfig,
    ) -> None:
        self.cfg = cfg
        self.result = result
        self.start_ts, self.end_ts = result.window
        self.tf = cfg.timeframe_ms
        self.slip = cfg.slippage_pct / 100.0
        self.trades = list(result.trades)
        self.pairs: dict[str, _PairData] = {}
        for pair in {t.pair for t in self.trades}:
            candles = [c for c in data.get(pair, ()) if self.end_ts is None or c.ts < self.end_ts]
            index = {c.ts: k for k, c in enumerate(candles)}
            self.pairs[pair] = _PairData(candles, index, compute_features(candles, cfg))
        self.ledger = _Ledger(self.trades, cfg, self.tf)

    def decision_ts(self, t: Trade) -> int:
        return t.signal_ts + self.tf

    def equity_before(self, ts: int) -> float:
        return self.ledger.equity_before(ts)


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


def _check_gates(t: Trade, pd: _PairData, cfg: StrategyConfig) -> list[str]:
    """R1-R4 on the signal candle, re-derived from the candles (compute_features)."""
    i = pd.index.get(t.signal_ts)
    if i is None:
        return []  # reported by the timing check
    row = pd.rows[i]
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


_STOP_METHODS = ("pivot", "lookback_low")


def _check_structure(t: Trade, pd: _PairData, cfg: StrategyConfig) -> list[str]:
    """R8: the stop sits ``buffer`` below a low that was a confirmed pivot (or the fallback
    lookback low) at the close of the signal candle. A trade labelled "pivot" or
    "lookback_low" must match that construction; a foreign label (an imported bot journal,
    ``stop_method="external"``) may match either."""
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
        if t.stop_method not in _STOP_METHODS and (pivot or lookback):
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


def _expected_exit_price(c: Candle, reason: str, t: Trade, slip: float, wick_k: float) -> float:
    if reason == EXIT_TP:
        return t.target
    if reason == EXIT_END:
        return c.close * (1.0 - slip)
    if c.open <= t.stop:
        return c.open * (1.0 - slip)  # gap through the stop
    # D4 stop-fill stress: a share wick_k of the way from the stop to the candle low.
    return (t.stop - wick_k * (t.stop - c.low) if wick_k else t.stop) * (1.0 - slip)


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
    price = _expected_exit_price(c, reason, t, a.slip, a.cfg.stop_fill_wick_k)
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
    """A1 in R: clean SL exactly -1, TP exactly +reward_risk; only gap-through SLs below -1R,
    or (D4, ``stop_fill_wick_k > 0`` only) stressed non-gap SLs."""
    r, x = t.r_multiple, a.pairs[t.pair].index.get(t.exit_ts or -1)
    if r is None or x is None:
        return []  # missing exits / candles are reported by _check_closed / _check_exit
    rr, reason = a.cfg.reward_risk, t.exit_reason
    gap = reason == EXIT_SL and a.pairs[t.pair].candles[x].open <= t.stop
    stressed = reason == EXIT_SL and not gap and a.cfg.stop_fill_wick_k > 0
    out: list[str] = []
    if reason == EXIT_TP and abs(r - rr) > TOL:
        out.append(f"{_who(t)} take-profit made {r:.12f}R, not exactly +{rr:g}R net of costs (A1)")
    if reason == EXIT_SL and not gap and not stressed and abs(r + 1.0) > TOL:
        out.append(f"{_who(t)} clean stop-loss made {r:.12f}R, not exactly -1R all-in (A1)")
    if stressed and r > -1.0 + TOL:
        out.append(f"{_who(t)} stressed stop-loss made {r:.12f}R, better than -1R (D4)")
    if gap and r > -1.0 + TOL:
        out.append(f"{_who(t)} gap-through stop-loss made {r:.12f}R, better than -1R (A1)")
    if r < -1.0 - TOL and not gap and not stressed:
        out.append(f"{_who(t)} lost {r:.6f}R, below -1R without a gap through the stop (A1)")
    if r > rr + TOL and reason != EXIT_TP:
        out.append(f"{_who(t)} made {r:.6f}R, above the +{rr:g}R take-profit (A1)")
    return out


# ---------------------------------------------------------------------------- portfolio
def _fills_the_budget(first: Trade, cfg: StrategyConfig) -> bool:
    """True if ``first`` open at its full R7 cap leaves less than ``min_trade_risk_pct`` of
    the shared budget, so no other cluster position may open while it is open.

    Without the expectancy guard the first cluster position of a flat book reserves
    ``min(cap, budget)``; by induction nothing else is open alongside it. With the guard
    its reservation can be smaller (a multiplier), so this stricter check is not applied
    and only the budget sum below is. Exclusive bases (BNB) are handled separately.
    """
    if cfg.expectancy_guard:
        return False
    base = base_of(first.pair)
    if base not in cfg.pair_risk:
        return False
    reserved = min(cfg.pair_risk[base].max_risk_pct, cfg.cluster_risk_budget_pct)
    return cfg.cluster_risk_budget_pct - reserved < cfg.min_trade_risk_pct - 1e-12


def _pair_conflict(first: Trade, later: Trade, cfg: StrategyConfig) -> str | None:
    ba, bb = base_of(first.pair), base_of(later.pair)
    both = f"trades #{first.trade_id} {first.pair} and #{later.trade_id} {later.pair}"
    if ba == bb:
        return f"{both} overlap in the same asset (no pyramiding, R6)"
    cluster = set(cfg.correlated_cluster)
    if ba not in cluster or bb not in cluster:
        return None
    if ba in cfg.exclusive_bases or bb in cfg.exclusive_bases:
        return f"{both} overlap although BNB never stacks with another cluster position (R6)"
    if _fills_the_budget(first, cfg):
        return f"{both} overlap although one full-size position uses the whole budget (R6)"
    return None


def _check_portfolio(trades: Sequence[Trade], cfg: StrategyConfig, offset: int) -> list[str]:
    """R6. A position holds its budget from its decision until its exit is certain: an
    earlier position ``o`` is still open at the decision ``d`` of ``t`` iff
    ``o.exit_ts + offset > d`` (backtest offset = tf: ``o`` exited in a candle at or after
    ``t``'s signal candle; exits of a candle are processed before its entry decisions). A
    trade without an exit is open for good."""
    cluster = set(cfg.correlated_cluster)
    ordered = sorted(trades, key=lambda t: (decision_time(t, cfg), t.trade_id))
    out: list[str] = []
    active: list[Trade] = []
    for t in ordered:
        d = decision_time(t, cfg)
        active = [o for o in active if o.exit_ts is None or o.exit_ts + offset > d]
        for o in active:
            msg = _pair_conflict(o, t, cfg)
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


def _known_from(ev: NewsEvent) -> float:
    """C2: when ``ev`` became knowable. Explicit ``known_from_ts`` wins; else ``ev.ts`` for an
    unscheduled kind and -inf otherwise (scheduled, and any unrecognised kind: the audit
    applies the full window rather than risk under-reporting)."""
    if ev.known_from_ts is not None:
        return float(ev.known_from_ts)
    return float(ev.ts) if ev.kind.strip().lower() in UNSCHEDULED_KINDS else -math.inf


def _blackout(ev: NewsEvent, base: str, ts: int, cfg: StrategyConfig) -> tuple[float, str] | None:
    """(half-width hours, interval text) of the blackout ``ev`` imposes on ``base`` if ``ts``
    lies inside its C2 block interval ``[max(ev.ts - w, known_from), ev.ts + w]``."""
    scope = ev.scope.strip()
    _head, sep, tail = scope.partition(":")
    scope = f"EXCHANGE:{tail.strip().lower()}" if sep else scope.upper()
    hours: float | None = None
    high_scopes = {"ALL", base, f"EXCHANGE:{cfg.exchange_id.strip().lower()}"}
    if ev.impact.strip().lower() == "high" and scope in high_scopes:
        hours = cfg.news_blackout_hours
    bnb_kinds = {k.strip().lower() for k in cfg.bnb_event_kinds}
    bnb_scopes = {"ALL", "BNB", "EXCHANGE:binance"}
    if base == "BNB" and ev.kind.strip().lower() in bnb_kinds and scope in bnb_scopes:
        hours = max(hours or 0.0, cfg.bnb_event_blackout_hours)
    if hours is None:
        return None
    w = hours * HOUR_MS
    known = _known_from(ev)
    start, end = max(ev.ts - w, known), ev.ts + w
    if not start <= ts <= end:
        return None
    if known > ev.ts - w:  # unscheduled, or announced late: blocks only once knowable
        return hours, f"known from {_iso(round(known))}: {_iso(round(start))} .. {_iso(end)}"
    return hours, f"+/-{hours:g}h"


def _check_news(
    trades: Sequence[Trade], events: Iterable[NewsEvent], cfg: StrategyConfig
) -> list[str]:
    """R5 with the C2 semantics, recomputed from the raw events (no NewsCalendar)."""
    ordered = sorted(events, key=lambda e: e.ts)
    times = [e.ts for e in ordered]
    widest = max(cfg.news_blackout_hours, cfg.bnb_event_blackout_hours) * HOUR_MS
    out: list[str] = []
    for t in trades:
        ts, base = decision_time(t, cfg), base_of(t.pair)
        lo, hi = bisect_left(times, ts - widest), bisect_right(times, ts + widest)
        for ev in ordered[lo:hi]:
            hit = _blackout(ev, base, ts, cfg)
            if hit is not None:
                out.append(
                    f"{_who(t)} entered at {_iso(ts)} inside the blackout ({hit[1]}) of the "
                    f"{ev.impact} {ev.kind} event at {_iso(ev.ts)} ({ev.scope}) (R5)"
                )
                break
    return out


def benches(
    trades: Sequence[Trade], cfg: StrategyConfig, exit_time_uncertainty_ms: int | None = None
) -> dict[str, list[tuple[int, int]]]:
    """Recomputed R9 benches: pair -> [(start, until)] from consecutive-SL streaks.

    ``start`` is the effective exit time ``t_e = exit_ts + offset`` (A2; default offset the
    timeframe, i.e. the exit candle close of a backtest) of the stop-loss that completed
    the streak; the bench covers ``start <= ts < until = start + bench_hours``.
    """
    offset = _offset(cfg, exit_time_uncertainty_ms)
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
            start = t.exit_ts + offset  # type: ignore[operator]
            out.setdefault(t.pair, []).append((start, start + bench_ms))
            streak[t.pair] = 0
    return out


def _check_benches(
    trades: Sequence[Trade],
    all_benches: Mapping[str, list[tuple[int, int]]],
    cfg: StrategyConfig,
) -> list[str]:
    out: list[str] = []
    for t in trades:
        ts = decision_time(t, cfg)
        for start, until in all_benches.get(t.pair, ()):
            if start <= ts < until:
                out.append(
                    f"{_who(t)} entered at {_iso(ts)} while {t.pair} was benched from "
                    f"{_iso(start)} until {_iso(until)} after consecutive stop-losses (R9)"
                )
    return out


def _check_bench_duration(
    trades: Sequence[Trade],
    all_benches: Mapping[str, list[tuple[int, int]]],
    cfg: StrategyConfig,
    offset: int,
) -> list[str]:
    """A2: after a streak-completing stop-loss, the pair's NEXT entry decision comes at least
    ``bench_hours`` after the exit became certain (``start = exit_ts + offset``; in a
    backtest the exit candle close), so >= 24h of real time wherever the stop filled.

    "Next" = the first decision strictly after ``exit_ts = start - offset``: a stop-loss hit
    on its own fill candle was DECIDED at that candle's open, and any other same-pair
    decision at or before it would overlap the open trade (reported as pyramiding).
    """
    bench_ms = round(cfg.bench_hours * HOUR_MS)
    out: list[str] = []
    for pair, spans in all_benches.items():
        decisions = sorted(decision_time(t, cfg) for t in trades if t.pair == pair)
        for start, _until in spans:
            k = bisect_right(decisions, start - offset)
            if k < len(decisions) and decisions[k] - start < bench_ms:
                out.append(
                    f"{pair} was benched too briefly: its next entry was decided at "
                    f"{_iso(decisions[k])}, {(decisions[k] - start) / HOUR_MS:g}h after the exit "
                    f"of the stop-loss that completed the streak became certain at "
                    f"{_iso(start)}, less than the {cfg.bench_hours:g}h bench (R9)"
                )
    return out


def _check_halt(trades: Sequence[Trade], ledger: _Ledger) -> list[str]:
    cfg = ledger.cfg
    out: list[str] = []
    for t in trades:
        ts = decision_time(t, cfg)
        halted, pnl, threshold = ledger.halted(ts)
        if halted:
            out.append(
                f"{_who(t)} entered at {_iso(ts)} while the trailing "
                f"{cfg.loss_window_days:g}-day realized pnl {pnl:.2f} was below the halt "
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
def cross_trade_violations(
    trades: Iterable[Trade],
    cfg: StrategyConfig,
    events: Iterable[NewsEvent] | None = None,
    exit_time_uncertainty_ms: int = 0,
    starting_equity: float | None = None,
) -> list[str]:
    """Rules that span trades, recomputed from the journal rows alone (no candles needed).

    - R6: no pyramiding; BNB never overlaps another cluster position; open cluster risk
      never exceeds ``cluster_risk_budget_pct``; and, when one full-size cluster position
      exhausts the budget (the default config), no two cluster positions overlap.
    - R9: no entry decision inside a 3-SL bench (and the pair's next decision after such a
      stop-loss is at least ``bench_hours`` later), no entry during a 7-day loss halt.
    - R5, only if ``events`` is given: no entry decision inside a C2 block interval.

    Every exit counts from ``exit_ts + exit_time_uncertainty_ms`` (A2): 0 for a live or
    testnet journal of real fill times (the default), ``cfg.timeframe_ms`` for a backtest
    journal whose ``exit_ts`` is the exit candle open. Entry decisions are at
    ``signal_ts + timeframe_ms``. The 7-day halt compares the window's realized pnl with
    ``weekly_loss_limit_pct`` of the realized equity, starting from ``starting_equity``
    (default ``cfg.starting_capital``). Returns one sentence per violation.
    """
    offset = _offset(cfg, exit_time_uncertainty_ms)
    rows = list(trades)
    out = _check_portfolio(rows, cfg, offset)
    if events is not None:
        out.extend(_check_news(rows, events, cfg))
    all_benches = benches(rows, cfg, offset)
    out.extend(_check_benches(rows, all_benches, cfg))
    out.extend(_check_bench_duration(rows, all_benches, cfg, offset))
    out.extend(_check_halt(rows, _Ledger(rows, cfg, offset, starting_equity)))
    return out


def blocked_open_trades(result: BacktestResult, cfg: StrategyConfig | None = None) -> list[Trade]:
    """Trades that were OPEN at a decision time while their pair was benched or all entries
    were halted (recomputed). Their exits must still be on time: see ``check_invariants``.
    """
    cfg = cfg or result.cfg
    ledger = _Ledger(result.trades, cfg, cfg.timeframe_ms)
    all_benches = benches(result.trades, cfg)
    out: list[Trade] = []
    for t in result.trades:
        if t.exit_ts is None:
            continue
        for ts in range(t.entry_ts + cfg.timeframe_ms, t.exit_ts + 1, cfg.timeframe_ms):
            bench = any(s <= ts < u for s, u in all_benches.get(t.pair, ()))
            if bench or ledger.halted(ts)[0]:
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
    audit = _Audit(result, data, cfg or result.cfg)
    out: list[str] = []
    for t in audit.trades:
        if t.pair not in audit.pairs or not audit.pairs[t.pair].candles:
            out.append(f"{_who(t)} has no candle data")
            continue
        closed = _check_closed(t)
        out.extend(closed)
        out.extend(_check_geometry(t, audit.cfg))
        out.extend(_check_timing(t, audit))
        out.extend(_check_gates(t, audit.pairs[t.pair], audit.cfg))
        out.extend(_check_structure(t, audit.pairs[t.pair], audit.cfg))
        if not closed:
            out.extend(_check_exit(t, audit))
            out.extend(_check_accounting(t, audit))
            out.extend(_check_r_outcome(t, audit))
    # Backtest timing: exit_ts is the exit candle OPEN, the exit is certain at its close.
    out.extend(cross_trade_violations(audit.trades, audit.cfg, list(events), audit.tf))
    out.extend(_check_equity(audit))
    return out


# ---------------------------------------------------------------------------- live journals
def _pair_data(candles: Iterable[Candle], cfg: StrategyConfig) -> _PairData:
    rows = list(candles)
    return _PairData(rows, {c.ts: k for k, c in enumerate(rows)}, compute_features(rows, cfg))


def _check_live_record(t: Trade) -> list[str]:
    """Exit fields all set (closed) or all empty (open); pnl / R arithmetic of the RECORDED
    fees (live fees may differ from ``fee_rate``, so they are not recomputed)."""
    values = (t.exit_ts, t.exit_price, t.exit_reason, t.pnl, t.r_multiple)
    if all(v is None for v in values):
        return []
    if any(v is None for v in values):
        return [f"{_who(t)} is half-closed (exit_ts, exit_price, exit_reason, pnl, r_multiple)"]
    if t.exit_reason not in (EXIT_SL, EXIT_TP, EXIT_END):
        return [f"{_who(t)} has unknown exit reason {t.exit_reason!r}"]
    out: list[str] = []
    exit_ts, exit_price, pnl, r = (
        int(t.exit_ts),
        float(t.exit_price),
        float(t.pnl),
        float(t.r_multiple),
    )  # all set: checked just above
    if exit_ts < t.entry_ts:
        out.append(f"{_who(t)} exits at {_iso(exit_ts)} before its entry")
    expected = t.qty * (exit_price - t.entry_price) - t.fees
    if t.fees < 0 or abs(pnl - expected) > TOL * max(t.risk_amount, 1e-12):
        out.append(f"{_who(t)} pnl {pnl} != qty * (exit - entry) - fees {expected}")
    if abs(r - pnl / t.risk_amount) > TOL:
        out.append(f"{_who(t)} r_multiple {r} != pnl / planned risk")
    return out


def _check_live_timing(t: Trade, pd: _PairData, cfg: StrategyConfig) -> list[str]:
    """The signal candle is in the data and the fill lies in its fill candle."""
    if t.signal_ts not in pd.index:
        return [f"{_who(t)} has no signal candle in the supplied data (R1-R4/R8 unverifiable)"]
    d, tf = decision_time(t, cfg), cfg.timeframe_ms
    if t.entry_ts < d:
        return [f"{_who(t)} filled at {_iso(t.entry_ts)}, before its signal candle closed"]
    if t.entry_ts >= d + tf:
        return [
            f"{_who(t)} filled at {_iso(t.entry_ts)}, more than one candle after the decision "
            f"at {_iso(d)} (stale signal)"
        ]
    return []


def _check_live_exit(t: Trade, pd: _PairData, cfg: StrategyConfig) -> list[str]:
    """Exits never paused: no candle wholly inside (fill, exit) -- or after the fill of a
    still-open trade -- reached the stop (low <= stop) or traded through the target."""
    tf = cfg.timeframe_ms
    start = bisect_left([c.ts for c in pd.candles], t.entry_ts)
    for c in pd.candles[start:]:
        if t.exit_ts is not None and c.ts + tf > t.exit_ts:
            break
        level = "stop" if c.low <= t.stop else "target" if c.high > t.target else None
        if level is None:
            continue
        state = "is still open" if t.exit_ts is None else f"exits only at {_iso(t.exit_ts)}"
        return [
            f"{_who(t)} exit delayed: its {level} was reached in the candle of "
            f"{_iso(c.ts)} but it {state} (exits are never paused)"
        ]
    return []


def _check_live_risk(t: Trade, ledger: _Ledger, cfg: StrategyConfig) -> list[str]:
    """R7 on the realized equity at the decision (known exits only, offset 0)."""
    equity = ledger.equity_before(decision_time(t, cfg))
    cap = min(cfg.risk_for(t.pair).max_risk_pct, MANDATE_MAX_RISK_PCT.get(base_of(t.pair), 0.0))
    if equity <= 0:
        return [f"{_who(t)} entered with a realized equity of {equity:.2f}"]
    pct = t.risk_amount / equity * 100.0
    if pct > cap * (1.0 + 1e-7):
        return [
            f"{_who(t)} risks {pct:.6f}% of the realized equity {equity:.2f} at its decision, "
            f"above the {cap:g}% cap (R7)"
        ]
    return []


def live_journal_violations(
    trades: Iterable[Trade],
    data: Mapping[str, Sequence[Candle]],
    cfg: StrategyConfig,
    events: Iterable[NewsEvent] | None = None,
    starting_equity: float | None = None,
) -> list[str]:
    """Audit a LIVE / testnet journal (real fill times) against the candles it traded on.

    D5 live-journal mode (see the module docstring): exit offset 0; per trade R1-R4 and R8
    re-derived at ``signal_ts`` from ``data`` (a trade whose signal candle is missing is a
    violation), fill inside the signal's fill candle, planned geometry and R7 (also against
    the realized equity from ``starting_equity``, default ``cfg.starting_capital``), exits
    never paused, pnl / R arithmetic; across trades ``cross_trade_violations`` at offset 0
    (R6, R9, and R5 only if ``events`` is given). Open trades are allowed. The backtest
    fill-price identities are not asserted. Returns one sentence per violation.
    """
    rows = list(trades)
    pairs = {pair: _pair_data(data.get(pair, ()), cfg) for pair in sorted({t.pair for t in rows})}
    ledger = _Ledger(rows, cfg, 0, starting_equity)
    out: list[str] = []
    ids = [t.trade_id for t in rows]
    if len(set(ids)) != len(ids):
        out.append("duplicate trade ids in the journal")
    for t in sorted(rows, key=lambda t: (t.signal_ts, t.trade_id)):
        pd = pairs[t.pair]
        if not pd.candles:
            out.append(f"{_who(t)} has no candle data")
            continue
        out.extend(_check_live_record(t))
        out.extend(_check_geometry(t, cfg))
        out.extend(_check_live_timing(t, pd, cfg))
        out.extend(_check_gates(t, pd, cfg))
        out.extend(_check_structure(t, pd, cfg))
        out.extend(_check_live_exit(t, pd, cfg))
        out.extend(_check_live_risk(t, ledger, cfg))
    out.extend(cross_trade_violations(rows, cfg, events, 0, starting_equity))
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
    src.add_argument("--journal", help="BACKTEST journal CSV (journal.write_journal format)")
    src.add_argument(
        "--live-journal",
        help="LIVE / testnet journal CSV of real fill times: exit offset 0, per-trade R1-R5/R8 "
        "re-derived from --data-dir candles and --events, no backtest fill-price identities",
    )
    src.add_argument("--synthetic", choices=WORLDS, help="run and audit a synthetic world")
    p.add_argument(
        "--data-dir", help="candle CSV directory (required with --journal / --live-journal)"
    )
    p.add_argument(
        "--starting-equity",
        type=float,
        default=None,
        help="--live-journal: account equity before the first trade (default: the config's)",
    )
    p.add_argument("--events", help="news calendar CSV (time_utc,scope,impact,kind,note)")
    p.add_argument("--start-ts", type=int, default=None, help="window start used by the run")
    p.add_argument("--end-ts", type=int, default=None, help="window end used by the run")
    p.add_argument(
        "--config",
        help="JSON object of StrategyConfig overrides the run used (e.g. a discovery variant: "
        '{"reward_risk": 2.5}); validated, a loosened mandatory rule is refused',
    )
    p.add_argument("--fee-rate", type=float, default=None, help="per-side fee the run used")
    p.add_argument("--slippage-pct", type=float, default=None, help="slippage %% the run used")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--years", type=float, default=6.0)
    p.add_argument("--gaps", action="store_true", help="synthetic world with open gaps")
    p.add_argument("--timeframe", default="4h")
    return p


def load_cli_config(
    config_path: str | None, fee_rate: float | None = None, slippage_pct: float | None = None
) -> StrategyConfig:
    """The config a journal was produced with: defaults, then the JSON overrides in
    ``config_path``, then the explicit cost flags. Raises ``ConfigError`` / ``ValueError``."""
    overrides: dict[str, object] = {}
    if config_path:
        loaded = json.loads(Path(config_path).read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ConfigError(f"{config_path}: config overrides must be a JSON object")
        overrides.update(loaded)
    if fee_rate is not None:
        overrides["fee_rate"] = fee_rate
    if slippage_pct is not None:
        overrides["slippage_pct"] = slippage_pct
    if not overrides:
        return StrategyConfig()
    # Imported here, not at module level: adoption.py imports this module (TESTNET check).
    from .adoption import config_from_overrides

    return config_from_overrides(overrides)


def _audit_live(args: argparse.Namespace, cfg: StrategyConfig) -> tuple[str, int, list[str]]:
    trades = read_journal(args.live_journal)
    data = load_dataset(args.data_dir, sorted({t.pair for t in trades}), args.timeframe)
    events = load_events(args.events) if args.events else None
    violations = live_journal_violations(trades, data, cfg, events, args.starting_equity)
    r5 = (
        f"R5 checked against {len(events)} event(s)"
        if events is not None
        else "R5 NOT checked: no --events given (R5 is not journal-verifiable without them)"
    )
    return f"live journal {args.live_journal} (exit offset 0; {r5})", len(trades), violations


def _audit_backtest(args: argparse.Namespace, cfg: StrategyConfig) -> tuple[str, int, list[str]]:
    events: list[NewsEvent] = load_events(args.events) if args.events else []
    window = (args.start_ts, args.end_ts)
    if args.synthetic:
        data, events = make_world(args.synthetic, args.seed, years=args.years, gaps=args.gaps)
        result = run_backtest(data, cfg, events, start_ts=window[0], end_ts=window[1])
        label = f"synthetic {args.synthetic} seed {args.seed}" + (" (gaps)" if args.gaps else "")
    else:
        trades = read_journal(args.journal)
        data = load_dataset(args.data_dir, sorted({t.pair for t in trades}), args.timeframe)
        result = result_from_journal(trades, cfg, window, bool(events))
        label = f"journal {args.journal}"
    return label, len(result.trades), check_invariants(result, data, events, cfg)


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        cfg = load_cli_config(args.config, args.fee_rate, args.slippage_pct)
    except (ConfigError, ValueError, OSError) as exc:
        parser.error(f"invalid --config / cost flags: {exc}")
    if not args.synthetic and not args.data_dir:
        parser.error("--data-dir is required with --journal / --live-journal")
    if args.live_journal:
        label, n, violations = _audit_live(args, cfg)
    else:
        label, n, violations = _audit_backtest(args, cfg)
    print(
        f"config: {cfg.variant_id()}, fee_rate {cfg.fee_rate:g}/side, slippage "
        f"{cfg.slippage_pct:g}%, exchange {cfg.exchange_id}"
    )
    print(f"{label}: {n} trades audited, {len(violations)} violation(s).")
    for v in violations:
        print(f"- {v}")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
