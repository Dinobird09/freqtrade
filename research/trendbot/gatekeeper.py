"""The SINGLE entry-decision code path, shared by the backtester and the live bot.

Every "may we open a long on ``pair`` now, with which stop and at what risk?" question in
this package is answered by :meth:`Gatekeeper.evaluate`, and every fill is turned into a
position size by :meth:`Gatekeeper.plan_fill`. The backtester (``backtester.py``) calls
exactly these two methods; a live bot must call exactly the same two methods, so a
backtest and the live bot cannot silently diverge on any mandatory rule.

Decision pipeline of :meth:`Gatekeeper.evaluate` (first failure wins; ``rule`` is always a
``config.RULE_IDS`` key)::

    1. R1-R4  signals.check_entry(rows[i]); the first failed gate maps to its rule id via
              signals.GATE_RULE_IDS (trend -> R1_trend, ..., regime -> R4_regime)
    2. R5     NewsCalendar.check(pair, decision_ts)
    3. R9     CircuitBreakers.can_enter(pair, decision_ts, equity)
    4. R6     CorrelationGuard.check(pair, requested = the pair's R7 cap, open_risk)
    5. R8     structure.find_stop(candles, i, pair, cfg)
    6. L_ml_filter          entry_filter(pair, rows[i], check), only if a filter is given
    7. L_expectancy_guard   journal_rules.risk_multiplier(..., exit_time_uncertainty_ms,
                            decision_ts): only trades whose exit is CERTAIN at the decision,
                            ``exit_ts + exit_time_uncertainty_ms <= decision_ts``
    R7  risk_pct = min(pair cap, R6 allowed) * guard multiplier   (can only ever shrink)

Exit timing (CONTRACT.md v2 A2): R9 and the guard measure every exit from ``t_e = exit_ts +
exit_time_uncertainty_ms``, the latest moment the exit can have filled. The gatekeeper owns ONE
such offset and hands the same value to its ``CircuitBreakers`` and to
``journal_rules.risk_multiplier``. The default is ``cfg.timeframe_ms``: backtest trades record
``exit_ts`` as the OPEN of the exit candle while the fill happens somewhere inside it, so a
bench then lasts at least ``bench_hours`` of real time after the fill. A live bot that journals
real fill times passes ``exit_time_uncertainty_ms=0`` (or breakers built with 0).

Timing (no look-ahead): the signal candle is ``candles[i]``; everything it tells us is known
at its CLOSE, ``decision_ts = candles[i].ts + cfg.timeframe_ms``, which is also the OPEN of
the fill candle. ``evaluate`` reads ``candles[0..i]`` and ``rows[0..i]`` only (the tests
prove that truncating the lists after ``i`` never changes the decision).

Execution step :meth:`Gatekeeper.plan_fill` (after the order is filled or, in the
backtest, at ``candles[i+1].open * (1 + slippage)``): refuses a fill that gapped through
the stop (R8 "gapped through stop"), re-checks the stop-distance bounds against the ACTUAL
fill (R8), then applies the cost-aware arithmetic of CONTRACT.md v2 A1 with fill ``E``, stop
``S``, ``f = fee_rate``, ``s = slippage_pct / 100``::

    L_u    = (E - S*(1-s)) + f*E + f*S*(1-s)        all-in loss per unit at the stop
    qty    = equity * risk_pct / 100 / L_u          (sizing.size_for_pair; R7)
    risk   = qty * L_u                              planned ALL-IN loss (risk_amount)
    target = (E*(1+f) + reward_risk*L_u) / (1-f)    (sizing.cost_aware_target)

so a stop filled at ``S*(1-s)`` loses exactly 1R after both fees and a take-profit wins exactly
``reward_risk`` R after both fees: the 2:1 minimum holds NET of costs, and the price ratio
``(target - E) / (E - S)`` is above ``reward_risk``. The notional is additionally capped by
the FREE cash (equity minus the cost of the other open positions): the quantity only ever
shrinks and ``risk_amount`` is recomputed as ``qty * L_u``, so the risk only ever decreases
(the target is per unit and does not change). If less than ``MIN_NOTIONAL`` of quote
currency can be bought the entry is skipped (``X_capital``).

How the live bot uses it (one call per pair, pairs in sorted order, on every CLOSED 4H
candle; :class:`LiveSession` below implements exactly this loop and its state, so a bot
should drive that instead of re-implementing it)::

    gk = Gatekeeper(cfg, events=load_events("events.csv"),
                    breakers=CircuitBreakers.from_journal(read_journal(path), cfg, 0))
    # (live journals hold real fill times: offset 0; the guard then uses 0 as well)
    # when the exchange reports an exit (SL/TP): record it in the journal, then
    gk.on_trade_closed(trade, equity=equity_after_close)       # exits are never gated
    # at each 4H close (decision_ts == the close time of candles[-1]):
    candles = closed candles of `pair` up to and including the one that just closed
    rows = compute_features(candles, cfg)
    dec = gk.evaluate(pair, candles, rows, len(candles) - 1, open_risk, equity,
                      journal_closed_trades, entry_filter)
    if dec.allowed:
        size the order with the EXPECTED fill (last price * (1 + slippage)), market buy;
        then plan = gk.plan_fill(pair, dec, market_price, fill_price, equity, free_cash)
        (the target and size are recomputed from the ACTUAL fill)
        if plan.ok: place stop at plan.stop and a limit take-profit at plan.target, record
                    open_risk[pair] = dec.risk_pct (the R6 budget reserved by this trade)
        else: flatten immediately (the fill violated R8 / capital limits)
    journal every denial (dec.rule, dec.reason) for the audit trail.

``open_risk`` maps each open pair to the risk it RESERVED from the shared R6 budget
(``EntryDecision.risk_pct``), not to the possibly smaller realized risk after a notional
cap, so a capped position still blocks the budget it was granted.

:class:`LiveSession` (CONTRACT v4 D5) packages exactly that loop, with the state a live bot
must own (open positions, reserved R6 risk, realized equity, R9 breakers rebuilt from the
journal on restart). It is the adapter a live / testnet bot, or a replay of its journal,
drives; a whole-run parity test shows that driving it candle by candle reproduces
``backtester.run_backtest`` trade for trade, including a restart from the written journal.
The bookkeeping helpers :func:`open_trade`, :func:`close_trade` and :func:`settle` are shared
by the backtester and the session, so both record a trade with the same code.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace

from .circuit_breakers import CircuitBreakers, validate_uncertainty_ms
from .config import StrategyConfig
from .correlation import CorrelationGuard
from .indicators import compute_features
from .journal_rules import risk_multiplier
from .models import (
    EXIT_END,
    EXIT_SL,
    EXIT_TP,
    Candle,
    EntryFilter,
    FeatureRow,
    NewsEvent,
    SignalCheck,
    SizingResult,
    StopPlan,
    Trade,
)
from .news import NewsCalendar
from .signals import GATE_RULE_IDS, check_entry
from .sizing import cost_aware_target, loss_per_unit, max_risk_pct, size_for_pair
from .structure import find_stop


R5 = "R5_news_blackout"
R6 = "R6_correlation_cap"
R7 = "R7_position_risk"
R8 = "R8_structure_stop"
R9 = "R9_circuit_breaker"
ML = "L_ml_filter"
GUARD = "L_expectancy_guard"
CAPITAL = "X_capital"

GAPPED_THROUGH_STOP = "gapped through stop"
# Smallest order worth placing, quote currency (Binance spot's minimum order value is
# 5-10 USDT). Only reachable when free cash is nearly exhausted by other open positions.
MIN_NOTIONAL = 10.0


@dataclass(frozen=True, slots=True)
class EntryDecision:
    """Verdict on one signal candle. ``rule`` names the first failed rule, or R7 if allowed."""

    allowed: bool
    rule: str  # config.RULE_IDS key
    reason: str  # one human-readable sentence
    check: SignalCheck  # full R1-R4 audit trail (all gates are always evaluated)
    stop_plan: StopPlan | None = None  # set once R8 passed
    risk_pct: float = 0.0  # R7 risk to reserve / size with (0 when denied)
    ml_prob: float | None = None  # probability reported by the entry filter, if any
    decision_ts: int = 0  # close of the signal candle = open of the fill candle
    pair_cap: float = 0.0  # R7 per-pair cap
    correlation_allowed: float = 0.0  # what R6 allowed out of the shared budget
    guard_mult: float = 1.0  # L_expectancy_guard multiplier applied


@dataclass(frozen=True, slots=True)
class FillPlan:
    """Result of :meth:`Gatekeeper.plan_fill`: the position to hold, or why not."""

    ok: bool
    rule: str
    reason: str
    entry: float = 0.0  # actual fill price (incl. slippage)
    stop: float = 0.0
    target: float = 0.0
    sizing: SizingResult | None = None


def _px(x: float) -> str:
    return f"{x:.8g}"


class Gatekeeper:
    """Holds the stateful rule objects (news calendar, breakers, correlation guard).

    ``exit_time_uncertainty_ms`` (A2) is shared by the R9 breakers and the expectancy guard.
    ``None`` (default) means: take it from ``breakers`` if given, else ``cfg.timeframe_ms``
    (the backtest convention). Passing both with different offsets is an error, so the two
    R9-style rules can never measure exits from different times.
    """

    def __init__(
        self,
        cfg: StrategyConfig,
        events: Iterable[NewsEvent] = (),
        breakers: CircuitBreakers | None = None,
        exit_time_uncertainty_ms: int | None = None,
    ) -> None:
        self.cfg = cfg
        self.news = NewsCalendar(events, cfg)
        if exit_time_uncertainty_ms is None:
            exit_time_uncertainty_ms = (
                breakers.exit_time_uncertainty_ms if breakers is not None else cfg.timeframe_ms
            )
        self.exit_time_uncertainty_ms = validate_uncertainty_ms(exit_time_uncertainty_ms)
        self.risk_scale = 1.0  # live capital guard (capital.py): <= 1, only ever shrinks risk
        if breakers is None:
            breakers = CircuitBreakers(cfg, exit_time_uncertainty_ms=self.exit_time_uncertainty_ms)
        elif breakers.exit_time_uncertainty_ms != self.exit_time_uncertainty_ms:
            raise ValueError(
                f"breakers use exit_time_uncertainty_ms={breakers.exit_time_uncertainty_ms} but "
                f"the gatekeeper was given {self.exit_time_uncertainty_ms}: R9 and the "
                "expectancy guard must measure exits from the same time"
            )
        self.breakers = breakers
        self.correlation = CorrelationGuard(cfg)

    # ------------------------------------------------------------------ state
    def on_trade_closed(self, trade: Trade, equity: float | None = None) -> list[str]:
        """Feed a closed trade to the R9 breakers; returns one sentence per breaker tripped.

        Exits are never gated: this only updates state for FUTURE entry decisions.
        """
        return self.breakers.on_trade_closed(trade, equity=equity)

    def news_loaded(self) -> bool:
        return self.news.loaded()

    # ------------------------------------------------------------------ decisions
    def evaluate(
        self,
        pair: str,
        candles: Sequence[Candle],
        rows: Sequence[FeatureRow],
        i: int,
        open_risk: Mapping[str, float],
        equity: float,
        closed_trades: Sequence[Trade] = (),
        entry_filter: EntryFilter | None = None,
    ) -> EntryDecision:
        """Full entry decision for signal candle ``candles[i]`` (see the module docstring)."""
        check, decision_ts, denial = self._signal_gates(pair, candles, rows, i)
        if denial is not None:
            return denial
        denial = self._portfolio_gates(pair, check, decision_ts, open_risk, equity)
        if isinstance(denial, EntryDecision):
            return denial
        cap, allowed_risk = denial
        plan, stop_reason = find_stop(candles, i, pair, self.cfg)
        if plan is None:
            return self._deny(R8, stop_reason, check, decision_ts)
        ml_prob: float | None = None
        if entry_filter is not None:
            ok, ml_prob, why = entry_filter(pair, rows[i], check)
            if not ok:
                rule = getattr(entry_filter, "rule_id", ML)  # e.g. L_learned_rule
                return self._deny(rule, why, check, decision_ts, plan, ml_prob)
        mult = self.guard_multiplier(pair, decision_ts, closed_trades)
        mult *= min(1.0, max(0.0, self.risk_scale))
        risk = min(cap, allowed_risk) * mult
        reason = (
            f"{pair} passes R1-R6, R8 and R9: risk {risk:g}% = min(cap {cap:g}%, R6 "
            f"allows {allowed_risk:g}%) x guard {mult:g}; {stop_reason}"
        )
        return EntryDecision(
            True, R7, reason, check, plan, risk, ml_prob, decision_ts, cap, allowed_risk, mult
        )

    def evaluate_signal(
        self, pair: str, candles: Sequence[Candle], rows: Sequence[FeatureRow], i: int
    ) -> EntryDecision:
        """Signal-level rules only (R1-R5 and R8), ignoring portfolio state (R6, R9, layers).

        Used to enumerate ML training candidates: the same gates and the same stop as
        :meth:`evaluate`, with the risk set to the pair cap.
        """
        check, decision_ts, denial = self._signal_gates(pair, candles, rows, i)
        if denial is not None:
            return denial
        plan, stop_reason = find_stop(candles, i, pair, self.cfg)
        if plan is None:
            return self._deny(R8, stop_reason, check, decision_ts)
        cap = max_risk_pct(pair, self.cfg)
        return EntryDecision(
            True, R7, stop_reason, check, plan, cap, None, decision_ts, cap, cap, 1.0
        )

    def guard_multiplier(
        self, pair: str, decision_ts: int, closed_trades: Sequence[Trade]
    ) -> float:
        """L_expectancy_guard multiplier from the trades whose exit is certain at the decision
        (``exit_ts + exit_time_uncertainty_ms <= decision_ts``, the same offset as R9)."""
        return risk_multiplier(
            closed_trades,
            pair,
            self.cfg,
            exit_time_uncertainty_ms=self.exit_time_uncertainty_ms,
            decision_ts=decision_ts,
        )

    # ------------------------------------------------------------------ execution
    def plan_fill(
        self,
        pair: str,
        decision: EntryDecision,
        market_price: float,
        fill_price: float,
        equity: float,
        free_cash: float,
    ) -> FillPlan:
        """Turn an allowed decision and an actual fill into stop, target and size (A1).

        ``market_price`` is the price the order met (backtest: the fill candle's open) and
        ``fill_price`` the executed price incl. slippage. Refuses (R8) a fill at or below the
        stop and a stop distance outside the configured bounds measured from the fill. The
        size makes a stop fill at ``stop * (1 - slippage)`` lose exactly ``risk_amount`` after
        both fees; the target makes a take-profit win exactly ``reward_risk * risk_amount``.
        """
        if not decision.allowed or decision.stop_plan is None:
            raise ValueError("plan_fill needs an allowed EntryDecision with a stop plan")
        cfg = self.cfg
        stop = decision.stop_plan.stop
        if market_price <= stop or fill_price <= stop:
            return FillPlan(
                False,
                R8,
                f"{GAPPED_THROUGH_STOP}: {pair} opened at {_px(market_price)} (fill "
                f"{_px(fill_price)}) at or below the planned stop {_px(stop)}",
            )
        dist = (fill_price - stop) / fill_price * 100.0
        if not cfg.min_stop_distance_pct <= dist <= cfg.max_stop_distance_pct:
            return FillPlan(
                False,
                R8,
                f"stop {_px(stop)} is {dist:.3f}% below the actual fill {_px(fill_price)}, "
                f"outside [{cfg.min_stop_distance_pct:g}%, {cfg.max_stop_distance_pct:g}%]",
            )
        sizing = size_for_pair(pair, equity, fill_price, stop, decision.risk_pct, cfg)
        sizing = _cap_to_free_cash(sizing, fill_price, stop, equity, free_cash, cfg)
        if sizing is None or sizing.notional < MIN_NOTIONAL:
            return FillPlan(
                False,
                CAPITAL,
                f"{pair}: free cash {max(free_cash, 0.0):.2f} buys less than the "
                f"{MIN_NOTIONAL:g} minimum order",
            )
        target = cost_aware_target(fill_price, stop, cfg)
        price_rr = (target - fill_price) / (fill_price - stop)
        reason = (
            f"{pair} filled at {_px(fill_price)}: stop {_px(stop)} ({dist:.2f}%), target "
            f"{_px(target)} ({cfg.reward_risk:g}R net of fees and slippage, {price_rr:.3f}:1 in "
            f"price), qty {sizing.qty:.8g}, all-in risk {sizing.risk_pct:.4g}%"
            + (f" (capped by {sizing.capped_by})" if sizing.capped_by else "")
        )
        return FillPlan(True, R7, reason, fill_price, stop, target, sizing)

    # ------------------------------------------------------------------ internals
    def _signal_gates(
        self, pair: str, candles: Sequence[Candle], rows: Sequence[FeatureRow], i: int
    ) -> tuple[SignalCheck, int, EntryDecision | None]:
        if not 0 <= i < len(candles) or len(rows) <= i:
            raise ValueError(f"signal index {i} out of range ({len(candles)} candles)")
        row = rows[i]
        if row.ts != candles[i].ts:
            raise ValueError(f"rows[{i}].ts {row.ts} != candles[{i}].ts {candles[i].ts}")
        decision_ts = candles[i].ts + self.cfg.timeframe_ms
        check = check_entry(pair, row, self.cfg)
        if not check.passed:
            gate = check.failed()[0]
            reason = f"{gate.name} gate failed: {gate.detail}"
            return (
                check,
                decision_ts,
                self._deny(GATE_RULE_IDS[gate.name], reason, check, decision_ts),
            )
        news = self.news.check(pair, decision_ts)
        if not news.allowed:
            return check, decision_ts, self._deny(R5, news.reason, check, decision_ts)
        return check, decision_ts, None

    def _portfolio_gates(
        self,
        pair: str,
        check: SignalCheck,
        decision_ts: int,
        open_risk: Mapping[str, float],
        equity: float,
    ) -> EntryDecision | tuple[float, float]:
        breaker = self.breakers.can_enter(pair, decision_ts, equity)
        if not breaker.allowed:
            return self._deny(R9, breaker.reason, check, decision_ts)
        cap = max_risk_pct(pair, self.cfg)
        corr, allowed_risk = self.correlation.check(pair, cap, open_risk)
        if not corr.allowed:
            return self._deny(R6, corr.reason, check, decision_ts)
        return cap, allowed_risk

    @staticmethod
    def _deny(
        rule: str,
        reason: str,
        check: SignalCheck,
        decision_ts: int,
        plan: StopPlan | None = None,
        ml_prob: float | None = None,
    ) -> EntryDecision:
        return EntryDecision(False, rule, reason, check, plan, 0.0, ml_prob, decision_ts)


def _cap_to_free_cash(
    sizing: SizingResult,
    entry: float,
    stop: float,
    equity: float,
    free_cash: float,
    cfg: StrategyConfig,
) -> SizingResult | None:
    """Shrink ``sizing`` so notional plus entry fee fits in ``free_cash`` (None if nothing fits).

    The all-in risk is recomputed as ``qty * L_u`` (A1), so it can only go down.
    """
    fee_rate = cfg.fee_rate
    if sizing.notional * (1 + fee_rate) <= free_cash:
        return sizing
    if free_cash <= 0:
        return None
    qty = min(sizing.qty, free_cash / (entry * (1 + fee_rate)))
    while qty > 0 and qty * entry * (1 + fee_rate) > free_cash:
        qty = math.nextafter(qty, 0.0)
    if qty <= 0:
        return None
    risk_amount = qty * loss_per_unit(entry, stop, cfg)
    return SizingResult(
        qty=qty,
        risk_amount=risk_amount,
        risk_pct=risk_amount / equity * 100.0,
        stop_distance=sizing.stop_distance,
        notional=qty * entry,
        capped_by="free_cash",
    )


# ---------------------------------------------------------------------------- bookkeeping
def settle(qty: float, entry: float, exit_price: float, fee_rate: float) -> tuple[float, float]:
    """``(fees, net pnl)`` of a long: fee_rate on both notional legs."""
    fees = fee_rate * qty * entry + fee_rate * qty * exit_price
    return fees, qty * (exit_price - entry) - fees


def entry_note(decision: EntryDecision, sizing: SizingResult) -> str:
    """Journal ``notes`` of an opened trade: the R6 reservation, how it was derived, the stop.

    Starts with ``"reserved <risk_pct:g>%"``; :func:`reserved_risk_of` reads it back when a
    session restarts from the journal.
    """
    plan = decision.stop_plan
    if plan is None:
        raise ValueError("an opened position needs a stop plan")
    note = (
        f"reserved {decision.risk_pct:g}% (cap {decision.pair_cap:g}%, R6 "
        f"{decision.correlation_allowed:g}%, guard x{decision.guard_mult:g}); stop "
        f"{plan.method} {plan.structure_level:.8g} -{plan.buffer_pct:g}%"
    )
    if sizing.capped_by:
        note += f"; size capped by {sizing.capped_by}"
    return note


def open_trade(
    trade_id: int,
    pair: str,
    variant: str,
    signal_ts: int,
    entry_ts: int,
    decision: EntryDecision,
    fill: FillPlan,
    features: Mapping[str, float],
) -> Trade:
    """The journal row of a position opened from an allowed decision and its fill plan."""
    plan, sizing = decision.stop_plan, fill.sizing
    if not fill.ok or plan is None or sizing is None:
        raise ValueError("an opened position needs a stop plan and a size")
    return Trade(
        trade_id=trade_id,
        pair=pair,
        variant=variant,
        signal_ts=signal_ts,
        entry_ts=entry_ts,
        entry_price=fill.entry,
        stop=plan.stop,
        target=fill.target,
        qty=sizing.qty,
        risk_amount=sizing.risk_amount,
        risk_pct=sizing.risk_pct,
        stop_method=plan.method,
        features=dict(features),
        ml_prob=decision.ml_prob,
        notes=entry_note(decision, sizing),
    )


def close_trade(
    trade: Trade,
    exit_ts: int,
    exit_price: float,
    reason: str,
    fee_rate: float,
    fees: float | None = None,
) -> Trade:
    """Record the exit on ``trade`` (in place): fees (``settle`` unless the ACTUAL ``fees`` are
    given), net pnl and ``r_multiple = pnl / risk_amount``. Returns the trade."""
    trade.exit_ts, trade.exit_price, trade.exit_reason = exit_ts, exit_price, reason
    if fees is None:
        trade.fees, trade.pnl = settle(trade.qty, trade.entry_price, exit_price, fee_rate)
    else:
        trade.fees = float(fees)
        trade.pnl = trade.qty * (exit_price - trade.entry_price) - trade.fees
    trade.r_multiple = trade.pnl / trade.risk_amount
    return trade


_RESERVED_NOTE = re.compile(r"^reserved ([0-9][0-9.eE+-]*)%")


def reserved_risk_of(trade: Trade, cfg: StrategyConfig) -> float:
    """R6 budget an OPEN journal trade holds: the ``"reserved X%"`` of its notes (written by
    :func:`entry_note`), else the pair's R7 cap (conservative: it blocks at least as much of
    the shared budget as the trade can have reserved)."""
    match = _RESERVED_NOTE.match(trade.notes or "")
    if match:
        try:
            value = float(match.group(1))
        except ValueError:
            value = math.nan
        if math.isfinite(value) and value > 0:
            return value
    return max_risk_pct(trade.pair, cfg)


# ---------------------------------------------------------------------------- live adapter
@dataclass(frozen=True, slots=True)
class DecisionRecord:
    """One evaluated signal candle: the row of the D6 decisions log."""

    pair: str
    signal_ts: int  # open time of the signal candle
    allowed: bool  # False also when the fill was refused or skipped afterwards
    rule: str  # config.RULE_IDS key: first failed rule, or R7_position_risk if allowed
    reason: str


@dataclass(slots=True)
class _Position:
    trade: Trade
    reserved_risk: float  # R6 budget reserved (EntryDecision.risk_pct)
    cost: float  # notional + entry fee, blocked from free cash while open


@dataclass(slots=True)
class _Pending:
    decision: EntryDecision
    signal_ts: int
    features: dict[str, float]
    log_index: int


def _is_closed_row(t: Trade) -> bool:
    fields = (t.exit_ts, t.exit_price, t.exit_reason, t.pnl)
    if all(v is None for v in fields):
        return False
    if any(v is None for v in fields) or t.exit_reason not in (EXIT_SL, EXIT_TP, EXIT_END):
        raise ValueError(
            f"journal trade #{t.trade_id} {t.pair}: exit_ts, exit_price, exit_reason (SL/TP/END) "
            "and pnl must be all set (closed) or all empty (open)"
        )
    return True


def _split_journal(trades: Iterable[Trade]) -> tuple[list[Trade], list[Trade]]:
    """COPIES of the journal's closed trades in exit order (``(exit_ts, pair, trade_id)``, the
    backtest's exit-processing order) and of its open trades in ``trade_id`` order."""
    rows = [replace(t, features=dict(t.features)) for t in trades]
    ids = [t.trade_id for t in rows]
    if len(set(ids)) != len(ids):
        raise ValueError("journal has duplicate trade ids")
    closed = sorted(
        (t for t in rows if _is_closed_row(t)), key=lambda t: (t.exit_ts, t.pair, t.trade_id)
    )
    still_open = sorted((t for t in rows if t.exit_ts is None), key=lambda t: t.trade_id)
    pairs = [t.pair for t in still_open]
    if len(set(pairs)) != len(pairs):
        raise ValueError(f"journal has more than one open trade on a pair: {sorted(pairs)}")
    return closed, still_open


class LiveSession:
    """Live / testnet / replay adapter around :class:`Gatekeeper` (CONTRACT v4 D5).

    It owns the state a bot must keep between candles: the open positions, the R6 budget
    each one reserved (``open_risk``), the realized equity, the closed trades (for the
    expectancy guard) and the R9 breakers. Every entry decision is ``Gatekeeper.evaluate``
    and every fill is sized by ``Gatekeeper.plan_fill``; exits are never gated.

    Construction (also a restart)::

        LiveSession(cfg, events=(), journal_trades=(), starting_equity=None, *,
                    exit_time_uncertainty_ms=0, entry_filter=None, variant="base",
                    reserved_risk=None)

    - ``events``: the news calendar (R5).
    - ``journal_trades``: every trade journaled so far (``journal.read_journal``), closed AND
      open. Closed rows (exit_ts, exit_price, exit_reason, pnl all set) rebuild the breakers
      with ``CircuitBreakers.from_journal(closed, cfg, exit_time_uncertainty_ms)``, the
      realized equity ``starting_equity + sum(pnl)`` (summed in ``(exit_ts, pair)`` order) and
      the guard's trade history. Open rows (all four empty) become open positions: their free
      cash cost is ``qty * entry_price * (1 + fee_rate)`` and their R6 reservation is
      ``reserved_risk[trade_id]`` if given, else the ``"reserved X%"`` of their notes, else
      the pair's cap (:func:`reserved_risk_of`). ``ValueError`` on duplicate ids, half-closed
      rows, or two open trades on one pair. The session works on copies.
    - ``starting_equity``: account equity before the first journaled trade (default
      ``cfg.starting_capital``).
    - ``exit_time_uncertainty_ms``: 0 (default) for journals of REAL fill times (live,
      testnet); ``cfg.timeframe_ms`` to replay a backtest-convention journal whose ``exit_ts``
      is the exit candle OPEN (A2). The same offset drives R9 and the guard.
    - ``entry_filter``: an optional veto layer (``models.EntryFilter``); ``variant`` is
      written into every new trade.

    Protocol, at every CLOSED 4H candle (decision time ``d`` = that candle's close)::

        1. exits first: for every stop / take-profit / forced exit that filled up to ``d``,
           in time order (pairs in sorted order on ties):
               trade = session.on_exit(trade_id, exit_ts, exit_price, reason, fees=None)
        2. then, pairs in SORTED order:
               dec = session.on_candle_close(pair, candles, rows=None)
               if dec.allowed:   market buy; then EITHER
                   trade = session.on_fill(pair, dec, market_price, fill_price,
                                           qty=None, trade_id=None, fill_ts=None)
                   (None = the fill was refused: flatten it; see ``last_fill_plan``)
               OR  session.on_fill_skipped(pair, dec, reason)   (no order was filled)

    A decision must be resolved (``on_fill`` / ``on_fill_skipped``) before the next
    ``on_candle_close``, so a position opened for one pair is visible to the pairs after it
    (R6), exactly like the backtest; otherwise ``ValueError``.

    - ``on_candle_close(pair, candles, rows=None) -> EntryDecision``: ``candles`` = the pair's
      closed candles up to and including the one that just closed (``candles[-1]`` is the
      signal candle; nothing later may be passed). ``rows`` = ``compute_features(candles,
      cfg)``, computed here if None; ``compute_features`` is causal and prefix-exact, so a
      caller may pass the first ``len(candles)`` rows of a longer history. ``ValueError`` if
      the candle is not later than the pair's previous one. Logs a :class:`DecisionRecord`.
    - ``on_fill(pair, decision, market_price, fill_price, *, qty=None, trade_id=None,
      fill_ts=None) -> Trade | None``: ``decision`` must be the allowed decision just returned
      for ``pair``. ``market_price`` = the price the order met, ``fill_price`` = the ACTUAL
      executed price incl. slippage (backtest: fill-candle open and open * (1 + slippage)).
      ``plan_fill`` re-checks R8 on the actual fill and sizes it on the current realized
      equity and free cash (A1). Refused -> None, the decision's log row becomes a denial
      with the plan's rule. Else the open :class:`Trade` (``entry_ts = fill_ts`` or the
      decision time; ``fill_ts`` must lie in ``[d, d + timeframe)``). Replay injection:
      ``qty`` = the actual filled quantity (``0 < qty <= planned qty``: an exchange may round
      it down, never up, else ``ValueError``; risk is recomputed as ``qty * L_u``),
      ``trade_id`` = the journal's id (default: the next free id).
    - ``on_fill_skipped(pair, decision, reason, rule="X_capital") -> None``: an allowed
      decision that did not lead to a position; its log row becomes a denial.
    - ``on_exit(trade_id, exit_ts, exit_price, reason, *, fees=None) -> Trade``: the ACTUAL
      exit of an open trade (``reason`` in SL / TP / END, ``exit_ts >= entry_ts``). Fees
      default to ``fee_rate`` on both legs; pass the exchange's actual ``fees`` in a replay.
      Updates pnl, R, equity, the equity curve and the breakers (trip sentences go to
      ``breaker_log``). Never refused for a rule: exits are never paused.

    State (read-only): ``equity`` (realized), ``open_positions`` {pair: Trade},
    ``open_risk`` {pair: reserved %}, ``free_cash()``, ``closed`` (closed trades in close
    order), ``journal_trades()`` (closed, then open by id: what to ``write_journal``),
    ``reservations()`` {trade_id: reserved %}, ``decision_log`` (list of
    :class:`DecisionRecord`), ``breaker_log`` [(exit_ts, sentence)], ``equity_curve``
    [(exit_ts, equity)], ``breakers``, ``gatekeeper``, ``last_fill_plan``.
    """

    def __init__(
        self,
        cfg: StrategyConfig,
        events: Iterable[NewsEvent] = (),
        journal_trades: Iterable[Trade] = (),
        starting_equity: float | None = None,
        *,
        exit_time_uncertainty_ms: int = 0,
        entry_filter: EntryFilter | None = None,
        variant: str = "base",
        reserved_risk: Mapping[int, float] | None = None,
    ) -> None:
        start = cfg.starting_capital if starting_equity is None else float(starting_equity)
        if not (math.isfinite(start) and start > 0):
            raise ValueError(f"starting_equity must be > 0 (got {starting_equity!r})")
        offset = validate_uncertainty_ms(exit_time_uncertainty_ms)
        self.cfg = cfg
        self.starting_equity = start
        self.entry_filter = entry_filter
        self.variant = variant
        closed, still_open = _split_journal(journal_trades)
        self.closed: list[Trade] = closed
        self.equity = start
        self.equity_curve: list[tuple[int, float]] = []
        for t in closed:
            self.equity += t.pnl  # type: ignore[operator]
            self.equity_curve.append((t.exit_ts, self.equity))  # type: ignore[arg-type]
        breakers = CircuitBreakers.from_journal(closed, cfg, offset)
        self.gatekeeper = Gatekeeper(cfg, events, breakers=breakers)
        given = dict(reserved_risk or {})
        self._open: dict[str, _Position] = {}
        for t in still_open:
            reserved = given.get(t.trade_id, reserved_risk_of(t, cfg))
            self._open[t.pair] = _Position(t, reserved, t.qty * t.entry_price * (1 + cfg.fee_rate))
        self._ids = {t.trade_id for t in (*closed, *still_open)}
        self._next_id = max(self._ids, default=0) + 1
        self._pending: dict[str, _Pending] = {}
        self._last_close: dict[str, int] = {}
        self.decision_log: list[DecisionRecord] = []
        self.breaker_log: list[tuple[int, str]] = []
        self.last_fill_plan: FillPlan | None = None

    # ------------------------------------------------------------------ state
    @property
    def breakers(self) -> CircuitBreakers:
        return self.gatekeeper.breakers

    @property
    def exit_time_uncertainty_ms(self) -> int:
        return self.gatekeeper.exit_time_uncertainty_ms

    @property
    def open_positions(self) -> dict[str, Trade]:
        return {pair: pos.trade for pair, pos in self._open.items()}

    @property
    def open_risk(self) -> dict[str, float]:
        """pair -> R6 budget reserved by its open position (what ``evaluate`` is given)."""
        return {pair: pos.reserved_risk for pair, pos in self._open.items()}

    def reservations(self) -> dict[int, float]:
        """trade_id -> reserved R6 risk of every open position (persist it for a restart)."""
        return {pos.trade.trade_id: pos.reserved_risk for pos in self._open.values()}

    def free_cash(self) -> float:
        """Realized equity minus the cost (notional + entry fee) of the open positions."""
        return self.equity - sum(pos.cost for pos in self._open.values())

    def journal_trades(self) -> list[Trade]:
        """Closed trades in close order, then open trades by id (for ``write_journal``)."""
        return [*self.closed, *sorted(self.open_positions.values(), key=lambda t: t.trade_id)]

    # ------------------------------------------------------------------ protocol
    def on_candle_close(
        self,
        pair: str,
        candles: Sequence[Candle],
        rows: Sequence[FeatureRow] | None = None,
    ) -> EntryDecision:
        """Entry decision on the candle that just closed (``candles[-1]``); see the class doc."""
        if self._pending:
            waiting = ", ".join(sorted(self._pending))
            raise ValueError(
                f"resolve the pending decision for {waiting} (on_fill or on_fill_skipped) "
                "before the next on_candle_close: a position opened for one pair must be "
                "visible to the pairs evaluated after it (R6)"
            )
        n = len(candles)
        if n == 0:
            raise ValueError(f"{pair}: on_candle_close needs at least the candle that closed")
        signal_ts = candles[n - 1].ts
        previous = self._last_close.get(pair)
        if previous is not None and signal_ts <= previous:
            raise ValueError(f"{pair}: candle {signal_ts} is not after the last one {previous}")
        if rows is None:
            rows = compute_features(candles, self.cfg)
        elif len(rows) != n:
            raise ValueError(f"{pair}: {len(rows)} feature rows for {n} candles")
        dec = self.gatekeeper.evaluate(
            pair, candles, rows, n - 1, self.open_risk, self.equity, self.closed, self.entry_filter
        )
        self._last_close[pair] = signal_ts
        self.decision_log.append(DecisionRecord(pair, signal_ts, dec.allowed, dec.rule, dec.reason))
        if dec.allowed:
            features = rows[n - 1].ml_features() or {}
            self._pending[pair] = _Pending(dec, signal_ts, features, len(self.decision_log) - 1)
        return dec

    def on_fill(
        self,
        pair: str,
        decision: EntryDecision,
        market_price: float,
        fill_price: float,
        *,
        qty: float | None = None,
        trade_id: int | None = None,
        fill_ts: int | None = None,
    ) -> Trade | None:
        """Open the position for an ACTUAL fill of ``decision`` (None if refused)."""
        pending = self._take(pair, decision, "on_fill", remove=False)
        entry_ts = self._fill_time(decision, fill_ts)
        plan = self.gatekeeper.plan_fill(
            pair, decision, market_price, fill_price, self.equity, self.free_cash()
        )
        self.last_fill_plan = plan
        if not plan.ok or plan.sizing is None:
            del self._pending[pair]
            self._refuse(pending, plan.rule, plan.reason)
            return None
        if qty is not None:
            plan = self._actual_qty(pair, plan, qty)
        tid = self._claim_id(trade_id)
        del self._pending[pair]
        trade = open_trade(
            tid, pair, self.variant, pending.signal_ts, entry_ts, decision, plan, pending.features
        )
        cost = plan.sizing.notional * (1.0 + self.cfg.fee_rate)  # type: ignore[union-attr]
        self._open[pair] = _Position(trade, decision.risk_pct, cost)
        return trade

    def on_fill_skipped(
        self, pair: str, decision: EntryDecision, reason: str, rule: str = CAPITAL
    ) -> None:
        """An allowed decision that did not become a position (no order filled)."""
        pending = self._take(pair, decision, "on_fill_skipped", remove=True)
        self._refuse(pending, rule, reason)

    def on_exit(
        self,
        trade_id: int,
        exit_ts: int,
        exit_price: float,
        reason: str,
        *,
        fees: float | None = None,
    ) -> Trade:
        """Close an open trade at its ACTUAL exit; never refused for a rule (see class doc)."""
        pair = next((p for p, pos in self._open.items() if pos.trade.trade_id == trade_id), None)
        if pair is None:
            raise ValueError(f"trade #{trade_id} is not an open position of this session")
        t = self._open[pair].trade
        if reason not in (EXIT_SL, EXIT_TP, EXIT_END):
            raise ValueError(f"exit reason must be SL, TP or END (got {reason!r})")
        if not (math.isfinite(exit_price) and exit_price > 0):
            raise ValueError(f"exit price must be > 0 (got {exit_price!r})")
        if exit_ts < t.entry_ts:
            raise ValueError(f"trade #{trade_id} cannot exit at {exit_ts} before its entry")
        if fees is not None and not (math.isfinite(fees) and fees >= 0):
            raise ValueError(f"fees must be >= 0 (got {fees!r})")
        del self._open[pair]
        close_trade(t, exit_ts, exit_price, reason, self.cfg.fee_rate, fees)
        self.equity += t.pnl  # type: ignore[operator]
        self.closed.append(t)
        self.equity_curve.append((exit_ts, self.equity))
        for msg in self.gatekeeper.on_trade_closed(t, equity=self.equity):
            self.breaker_log.append((exit_ts, msg))
        return t

    # ------------------------------------------------------------------ internals
    def _take(self, pair: str, decision: EntryDecision, what: str, remove: bool) -> _Pending:
        pending = self._pending.get(pair)
        if pending is None or pending.decision is not decision:
            raise ValueError(
                f"{pair}: {what} needs the allowed EntryDecision just returned by "
                f"on_candle_close for {pair}"
            )
        if remove:
            del self._pending[pair]
        return pending

    def _refuse(self, pending: _Pending, rule: str, reason: str) -> None:
        row = self.decision_log[pending.log_index]
        self.decision_log[pending.log_index] = DecisionRecord(
            row.pair, row.signal_ts, False, rule, reason
        )

    def _fill_time(self, decision: EntryDecision, fill_ts: int | None) -> int:
        d = decision.decision_ts
        if fill_ts is None:
            return d
        if not d <= fill_ts < d + self.cfg.timeframe_ms:
            raise ValueError(
                f"fill at {fill_ts} lies outside the fill candle [{d}, "
                f"{d + self.cfg.timeframe_ms}) of the decision: stale or premature"
            )
        return int(fill_ts)

    def _actual_qty(self, pair: str, plan: FillPlan, qty: float) -> FillPlan:
        sizing = plan.sizing
        if sizing is None:
            raise ValueError("a fill plan without a size cannot be adjusted")
        if not (math.isfinite(qty) and 0 < qty <= sizing.qty * (1 + 1e-9)):
            raise ValueError(
                f"{pair}: actual fill qty {qty!r} must be in (0, planned {sizing.qty!r}]: a fill "
                "may be smaller than the R7 size, never larger"
            )
        if qty == sizing.qty:
            return plan
        risk = qty * loss_per_unit(plan.entry, plan.stop, self.cfg)
        actual = SizingResult(
            qty=qty,
            risk_amount=risk,
            risk_pct=risk / self.equity * 100.0,
            stop_distance=sizing.stop_distance,
            notional=qty * plan.entry,
            capped_by=sizing.capped_by or "actual_fill_qty",
        )
        return replace(plan, sizing=actual)

    def _claim_id(self, trade_id: int | None) -> int:
        tid = self._next_id if trade_id is None else trade_id
        if isinstance(tid, bool) or not isinstance(tid, int) or tid <= 0 or tid in self._ids:
            raise ValueError(f"trade_id {trade_id!r} must be a positive int not used before")
        self._ids.add(tid)
        self._next_id = max(self._next_id, tid + 1)
        return tid
