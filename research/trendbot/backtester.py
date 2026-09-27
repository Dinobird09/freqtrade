"""Event-driven portfolio backtest of the long-only trend strategy (no look-ahead).

Every entry decision goes through :class:`gatekeeper.Gatekeeper` (the same code path the
live bot uses), so the backtest cannot apply a rule differently from the bot.

Time model (candle ``T`` = the candle that OPENS at ``T``)::

    for each timestamp T, ascending (union of all pairs' candle times in the window):
        1. EXITS of candle T for every open position, pairs in sorted order. Each close is
           fed to the R9 breakers (``on_trade_closed(trade, equity=equity_after)``).
           A position whose pair has no later candle before ``end_ts`` is force-closed
           (EXIT_END) at the close of that last candle. Exits are NEVER gated.
        2. ENTRY decisions on signal candle T for every pair, sorted: decided at T + tf
           (the signal close) from data up to T only, filled at the next candle's open
           * (1 + slippage). A position opened here is visible to the pairs after it.

Execution model (shared with :func:`enumerate_candidates` through :func:`simulate_exit`):
    - The fill candle must open exactly at the decision time (a data gap there skips the
      entry: ``X_capital``, "no fill candle"). A fill candle that opens at or below the stop
      is skipped (``R8_structure_stop``, "gapped through stop"); the stop-distance bounds
      are re-checked against the actual fill.
    - ``target = entry + reward_risk * (entry - stop)``.
    - Per candle ``j >= fill candle``: open <= stop -> SL at open * (1 - slip) (gap-through);
      low <= stop -> SL at stop * (1 - slip) (also when the same candle reaches the target:
      the stop is conservatively assumed to fill first); high >= target -> TP at target
      (resting limit order, no slippage).
    - Forced close (EXIT_END) is a market sell: close * (1 - slip).
    - Fees: ``fee_rate`` of the notional on BOTH legs. ``pnl`` is net of fees and slippage;
      ``r_multiple = pnl / risk_amount`` with ``risk_amount = qty * (entry - stop)`` the
      PLANNED risk.
    - Size: ``sizing.size_for_pair`` on current REALIZED equity (starting capital plus
      closed pnl); the notional never exceeds the free cash (equity minus the cost of the
      other open positions), shrinking the quantity (so risk only ever decreases).

Windows: entries only for signal candles with ``start_ts <= ts < end_ts``; indicators use all
earlier data as warm-up. NO candle with ``ts >= end_ts`` is read at all (the input is cut
before anything else happens), so ``run_backtest(data, end_ts=T)`` is identical to
``run_backtest(data cut before T)``.

Features are computed once per pair per run (``compute_features`` is causal).
"""

from __future__ import annotations

from bisect import bisect_left
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from .config import StrategyConfig
from .gatekeeper import CAPITAL, EntryDecision, FillPlan, Gatekeeper
from .indicators import compute_features
from .models import (
    EXIT_END,
    EXIT_SL,
    EXIT_TP,
    CandidateOutcome,
    Candle,
    EntryFilter,
    FeatureRow,
    NewsEvent,
    Trade,
)


@dataclass
class BacktestResult:
    trades: list[Trade]  # closed trades, in exit order
    decisions: Counter[str]  # rule id -> number of denied signal candles
    decision_log: list[tuple[int, str, str, str]]  # (signal ts, pair, rule, reason) per denial
    equity_curve: list[tuple[int, float]]  # (exit_ts, equity) after each close
    final_equity: float
    cfg: StrategyConfig
    news_calendar_loaded: bool
    window: tuple[int | None, int | None]
    variant: str = "base"
    breaker_log: list[tuple[int, str]] = field(default_factory=list)  # (exit_ts, trip sentence)
    signals_evaluated: int = 0  # signal candles passed to the gatekeeper


@dataclass(slots=True)
class _Series:
    candles: list[Candle]
    rows: list[FeatureRow]
    first: int  # index of the first candle with ts >= start_ts


@dataclass(slots=True)
class _Position:
    trade: Trade
    fill_idx: int
    reserved_risk: float  # R6 budget reserved (EntryDecision.risk_pct)
    cost: float  # notional + entry fee, blocked from free cash while open


# ---------------------------------------------------------------------------- exit model
def exit_on_candle(c: Candle, stop: float, target: float, slip: float) -> tuple[float, str] | None:
    """Exit price and reason if candle ``c`` closes a long with this stop/target, else None."""
    if c.open <= stop:
        return c.open * (1.0 - slip), EXIT_SL  # gapped through the stop
    if c.low <= stop:
        return stop * (1.0 - slip), EXIT_SL  # stop first, even if the target was also hit
    if c.high >= target:
        return target, EXIT_TP  # resting limit: no slippage
    return None


def simulate_exit(
    candles: Sequence[Candle],
    fill_idx: int,
    entry: float,
    stop: float,
    target: float,
    cfg: StrategyConfig,
    last_idx: int | None = None,
) -> tuple[int, float, str] | None:
    """First SL/TP exit of a long filled at ``candles[fill_idx].open`` (price ``entry``).

    Scans ``candles[fill_idx .. last_idx]`` (default: to the end). Returns ``(exit_idx,
    exit_price, reason)`` or None if neither stop nor target is reached by ``last_idx``.
    """
    if not stop < entry < target:
        raise ValueError(f"need stop < entry < target (got {stop}, {entry}, {target})")
    slip = cfg.slippage_pct / 100.0
    last = len(candles) - 1 if last_idx is None else min(last_idx, len(candles) - 1)
    for j in range(fill_idx, last + 1):
        hit = exit_on_candle(candles[j], stop, target, slip)
        if hit is not None:
            return j, hit[0], hit[1]
    return None


def settle(qty: float, entry: float, exit_price: float, fee_rate: float) -> tuple[float, float]:
    """``(fees, net pnl)`` of a long: fee_rate on both notional legs."""
    fees = fee_rate * qty * entry + fee_rate * qty * exit_price
    return fees, qty * (exit_price - entry) - fees


# ---------------------------------------------------------------------------- preparation
def _prepare(
    data: Mapping[str, Sequence[Candle]],
    cfg: StrategyConfig,
    start_ts: int | None,
    end_ts: int | None,
) -> dict[str, _Series]:
    out: dict[str, _Series] = {}
    for pair in sorted(data):
        candles = list(data[pair])
        times = [c.ts for c in candles]
        if end_ts is not None:
            candles = candles[: bisect_left(times, end_ts)]
            times = times[: len(candles)]
        first = 0 if start_ts is None else bisect_left(times, start_ts)
        out[pair] = _Series(candles, compute_features(candles, cfg), first)
    return out


def _fill_candle(s: _Series, i: int, decision_ts: int) -> tuple[int | None, str]:
    """Index of the fill candle for signal ``i`` or None with the reason (X_capital)."""
    j = i + 1
    if j >= len(s.candles):
        return None, "no fill candle: the data (or the evaluation window) ends at the signal close"
    if s.candles[j].ts != decision_ts:
        return None, (
            f"no fill candle at the decision time {decision_ts}: data gap until "
            f"{s.candles[j].ts}, so no order could be placed when the signal closed"
        )
    return j, ""


# ---------------------------------------------------------------------------- simulation
class _Simulation:
    def __init__(
        self,
        cfg: StrategyConfig,
        events: Iterable[NewsEvent],
        entry_filter: EntryFilter | None,
        variant: str,
    ) -> None:
        self.cfg = cfg
        self.gk = Gatekeeper(cfg, events)
        self.entry_filter = entry_filter
        self.variant = variant
        self.slip = cfg.slippage_pct / 100.0
        self.equity = cfg.starting_capital
        self.open: dict[str, _Position] = {}
        self.trades: list[Trade] = []
        self.decisions: Counter[str] = Counter()
        self.log: list[tuple[int, str, str, str]] = []
        self.curve: list[tuple[int, float]] = []
        self.breaker_log: list[tuple[int, str]] = []
        self.next_id = 1
        self.evaluated = 0

    # -------------------------------------------------------------- exits
    def process_exit(self, pair: str, s: _Series, j: int) -> None:
        pos = self.open[pair]
        if j < pos.fill_idx:
            return
        t = pos.trade
        c = s.candles[j]
        hit = exit_on_candle(c, t.stop, t.target, self.slip)
        if hit is not None:
            self._close(pair, c.ts, hit[0], hit[1])
        elif j == len(s.candles) - 1:
            self._close(pair, c.ts, c.close * (1.0 - self.slip), EXIT_END)

    def _close(self, pair: str, exit_ts: int, price: float, reason: str) -> None:
        pos = self.open.pop(pair)
        t = pos.trade
        t.exit_ts, t.exit_price, t.exit_reason = exit_ts, price, reason
        t.fees, t.pnl = settle(t.qty, t.entry_price, price, self.cfg.fee_rate)
        t.r_multiple = t.pnl / t.risk_amount
        self.equity += t.pnl
        self.trades.append(t)
        self.curve.append((exit_ts, self.equity))
        for msg in self.gk.on_trade_closed(t, equity=self.equity):
            self.breaker_log.append((exit_ts, msg))

    # -------------------------------------------------------------- entries
    def _deny(self, ts: int, pair: str, rule: str, reason: str) -> None:
        self.decisions[rule] += 1
        self.log.append((ts, pair, rule, reason))

    def process_entry(self, pair: str, s: _Series, i: int) -> None:
        self.evaluated += 1
        open_risk = {p: pos.reserved_risk for p, pos in self.open.items()}
        dec = self.gk.evaluate(
            pair, s.candles, s.rows, i, open_risk, self.equity, self.trades, self.entry_filter
        )
        ts = s.candles[i].ts
        if not dec.allowed:
            self._deny(ts, pair, dec.rule, dec.reason)
            return
        j, why = _fill_candle(s, i, dec.decision_ts)
        if j is None:
            self._deny(ts, pair, CAPITAL, why)
            return
        market = s.candles[j].open
        free_cash = self.equity - sum(pos.cost for pos in self.open.values())
        plan = self.gk.plan_fill(
            pair, dec, market, market * (1.0 + self.slip), self.equity, free_cash
        )
        if not plan.ok or plan.sizing is None:
            self._deny(ts, pair, plan.rule, plan.reason)
            return
        self._open(pair, s, i, j, dec, plan)

    def _open(
        self, pair: str, s: _Series, i: int, j: int, dec: EntryDecision, fill: FillPlan
    ) -> None:
        plan, sizing = dec.stop_plan, fill.sizing
        if plan is None or sizing is None:
            raise ValueError("an opened position needs a stop plan and a size")
        note = (
            f"reserved {dec.risk_pct:g}% (cap {dec.pair_cap:g}%, R6 {dec.correlation_allowed:g}%,"
            f" guard x{dec.guard_mult:g}); stop {plan.method} {plan.structure_level:.8g} "
            f"-{plan.buffer_pct:g}%"
        )
        if sizing.capped_by:
            note += f"; size capped by {sizing.capped_by}"
        trade = Trade(
            trade_id=self.next_id,
            pair=pair,
            variant=self.variant,
            signal_ts=s.candles[i].ts,
            entry_ts=s.candles[j].ts,
            entry_price=fill.entry,
            stop=plan.stop,
            target=fill.target,
            qty=sizing.qty,
            risk_amount=sizing.risk_amount,
            risk_pct=sizing.risk_pct,
            stop_method=plan.method,
            features=s.rows[i].ml_features() or {},
            ml_prob=dec.ml_prob,
            notes=note,
        )
        self.next_id += 1
        cost = sizing.notional * (1.0 + self.cfg.fee_rate)
        self.open[pair] = _Position(trade, j, dec.risk_pct, cost)


def _timeline(series: Mapping[str, _Series]) -> list[int]:
    stamps: set[int] = set()
    for s in series.values():
        stamps.update(c.ts for c in s.candles[s.first :])
    return sorted(stamps)


def run_backtest(
    data: Mapping[str, Sequence[Candle]],
    cfg: StrategyConfig,
    events: Iterable[NewsEvent] = (),
    start_ts: int | None = None,
    end_ts: int | None = None,
    entry_filter: EntryFilter | None = None,
    variant: str = "base",
) -> BacktestResult:
    """Simulate the portfolio over ``[start_ts, end_ts)``; see the module docstring."""
    series = _prepare(data, cfg, start_ts, end_ts)
    sim = _Simulation(cfg, events, entry_filter, variant)
    pairs = list(series)
    ptr = {p: series[p].first for p in pairs}
    for ts in _timeline(series):
        current: dict[str, int] = {}
        for p in pairs:
            k = ptr[p]
            candles = series[p].candles
            if k < len(candles) and candles[k].ts == ts:
                current[p] = k
                ptr[p] = k + 1
        for p in sorted(sim.open):
            if p in current:
                sim.process_exit(p, series[p], current[p])
        for p, k in current.items():
            sim.process_entry(p, series[p], k)
    return BacktestResult(
        trades=sim.trades,
        decisions=sim.decisions,
        decision_log=sim.log,
        equity_curve=sim.curve,
        final_equity=sim.equity,
        cfg=cfg,
        news_calendar_loaded=sim.gk.news_loaded(),
        window=(start_ts, end_ts),
        variant=variant,
        breaker_log=sim.breaker_log,
        signals_evaluated=sim.evaluated,
    )


# ---------------------------------------------------------------------------- ML candidates
def _candidate(
    pair: str, s: _Series, i: int, gk: Gatekeeper, cfg: StrategyConfig
) -> CandidateOutcome | None:
    dec = gk.evaluate_signal(pair, s.candles, s.rows, i)
    if not dec.allowed:
        return None
    j, _ = _fill_candle(s, i, dec.decision_ts)
    if j is None:
        return None
    market = s.candles[j].open
    slip = cfg.slippage_pct / 100.0
    plan = gk.plan_fill(
        pair, dec, market, market * (1.0 + slip), cfg.starting_capital, float("inf")
    )
    if not plan.ok or plan.sizing is None:
        return None
    hit = simulate_exit(s.candles, j, plan.entry, plan.stop, plan.target, cfg)
    if hit is None:
        return None  # unresolved before the end of the window: purged
    exit_idx, price, reason = hit
    qty = plan.sizing.qty
    _, pnl = settle(qty, plan.entry, price, cfg.fee_rate)
    return CandidateOutcome(
        pair=pair,
        signal_ts=s.candles[i].ts,
        entry_ts=s.candles[j].ts,
        exit_ts=s.candles[exit_idx].ts,
        exit_reason=reason,
        r_multiple=pnl / plan.sizing.risk_amount,
        features=s.rows[i].ml_features() or {},
    )


def enumerate_candidates(
    data: Mapping[str, Sequence[Candle]],
    cfg: StrategyConfig,
    events: Iterable[NewsEvent] = (),
    start_ts: int | None = None,
    end_ts: int | None = None,
) -> list[CandidateOutcome]:
    """Every signal in ``[start_ts, end_ts)`` passing R1-R5 and R8, simulated in isolation.

    Portfolio rules (R6, R9, capital) are ignored; the fill, stop re-check, exit model, fees
    and slippage are exactly those of :func:`run_backtest` (so a candidate the backtest also
    took has the identical ``r_multiple``). Candidates whose exit is not resolved strictly
    before ``end_ts`` are DROPPED (purging), since no data at or after ``end_ts`` is read.
    Sorted by ``(signal_ts, pair)``.
    """
    series = _prepare(data, cfg, start_ts, end_ts)
    gk = Gatekeeper(cfg, events)
    out: list[CandidateOutcome] = []
    for pair, s in series.items():
        for i in range(s.first, len(s.candles)):
            cand = _candidate(pair, s, i, gk, cfg)
            if cand is not None:
                out.append(cand)
    out.sort(key=lambda c: (c.signal_ts, c.pair))
    return out
