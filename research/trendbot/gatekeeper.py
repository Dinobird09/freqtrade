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
    7. L_expectancy_guard   journal_rules.risk_multiplier(...) over the trades CLOSED BEFORE
                            decision_ts (``exit_ts < decision_ts``: a trade's ``exit_ts`` is
                            the OPEN of its exit candle, so ``exit_ts == decision_ts`` would be
                            an exit that happens after the decision)
    R7  risk_pct = min(pair cap, R6 allowed) * guard multiplier   (can only ever shrink)

Timing (no look-ahead): the signal candle is ``candles[i]``; everything it tells us is known
at its CLOSE, ``decision_ts = candles[i].ts + cfg.timeframe_ms``, which is also the OPEN of
the fill candle. ``evaluate`` reads ``candles[0..i]`` and ``rows[0..i]`` only (the tests
prove that truncating the lists after ``i`` never changes the decision).

Execution step :meth:`Gatekeeper.plan_fill` (after the order is filled or, in the
backtest, at ``candles[i+1].open * (1 + slippage)``): refuses a fill that gapped through
the stop (R8 "gapped through stop"), re-checks the stop-distance bounds against the ACTUAL
fill (R8), sets ``target = fill + reward_risk * (fill - stop)`` and sizes the position with
``sizing.size_for_pair`` on current realized equity (R7, size derived from the stop
distance). The notional is additionally capped by the FREE cash (equity minus the cost of
the other open positions): the quantity only ever shrinks, so the risk only ever decreases.
If less than ``MIN_NOTIONAL`` of quote currency can be bought the entry is skipped
(``X_capital``).

How the live bot uses it (one call per pair, pairs in sorted order, on every CLOSED 4H
candle)::

    gk = Gatekeeper(cfg, events=load_events("events.csv"),
                    breakers=CircuitBreakers.from_journal(read_journal(path), cfg))
    # when the exchange reports an exit (SL/TP): record it in the journal, then
    gk.on_trade_closed(trade, equity=equity_after_close)       # exits are never gated
    # at each 4H close (decision_ts == the close time of candles[-1]):
    candles = closed candles of `pair` up to and including the one that just closed
    rows = compute_features(candles, cfg)
    dec = gk.evaluate(pair, candles, rows, len(candles) - 1, open_risk, equity,
                      journal_closed_trades, entry_filter)
    if dec.allowed:
        market buy; then plan = gk.plan_fill(pair, dec, market_price, fill_price, equity,
                                             free_cash)
        if plan.ok: place stop at plan.stop and a limit take-profit at plan.target, record
                    open_risk[pair] = dec.risk_pct (the R6 budget reserved by this trade)
        else: flatten immediately (the fill violated R8 / capital limits)
    journal every denial (dec.rule, dec.reason) for the audit trail.

``open_risk`` maps each open pair to the risk it RESERVED from the shared R6 budget
(``EntryDecision.risk_pct``), not to the possibly smaller realized risk after a notional
cap, so a capped position still blocks the budget it was granted.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from .circuit_breakers import CircuitBreakers
from .config import StrategyConfig
from .correlation import CorrelationGuard
from .journal_rules import risk_multiplier
from .models import (
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
from .sizing import max_risk_pct, size_for_pair
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
    """Holds the stateful rule objects (news calendar, breakers, correlation guard)."""

    def __init__(
        self,
        cfg: StrategyConfig,
        events: Iterable[NewsEvent] = (),
        breakers: CircuitBreakers | None = None,
    ) -> None:
        self.cfg = cfg
        self.news = NewsCalendar(events, cfg)
        self.breakers = breakers if breakers is not None else CircuitBreakers(cfg)
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
                return self._deny(ML, why, check, decision_ts, plan, ml_prob)
        mult = self.guard_multiplier(pair, decision_ts, closed_trades)
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
        """L_expectancy_guard multiplier from trades closed strictly before ``decision_ts``."""
        if not self.cfg.expectancy_guard:
            return 1.0
        known = [t for t in closed_trades if t.exit_ts is not None and t.exit_ts < decision_ts]
        return risk_multiplier(known, pair, self.cfg)

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
        """Turn an allowed decision and an actual fill into stop, target and size.

        ``market_price`` is the price the order met (backtest: the fill candle's open) and
        ``fill_price`` the executed price incl. slippage. Refuses (R8) a fill at or below the
        stop and a stop distance outside the configured bounds measured from the fill.
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
        sizing = _cap_to_free_cash(sizing, fill_price, equity, free_cash, cfg.fee_rate)
        if sizing is None or sizing.notional < MIN_NOTIONAL:
            return FillPlan(
                False,
                CAPITAL,
                f"{pair}: free cash {max(free_cash, 0.0):.2f} buys less than the "
                f"{MIN_NOTIONAL:g} minimum order",
            )
        target = fill_price + cfg.reward_risk * (fill_price - stop)
        reason = (
            f"{pair} filled at {_px(fill_price)}: stop {_px(stop)} ({dist:.2f}%), target "
            f"{_px(target)} ({cfg.reward_risk:g}R), qty {sizing.qty:.8g}, risk "
            f"{sizing.risk_pct:.4g}%"
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
    sizing: SizingResult, entry: float, equity: float, free_cash: float, fee_rate: float
) -> SizingResult | None:
    """Shrink ``sizing`` so notional plus entry fee fits in ``free_cash`` (None if nothing fits)."""
    if sizing.notional * (1 + fee_rate) <= free_cash:
        return sizing
    if free_cash <= 0:
        return None
    qty = free_cash / (entry * (1 + fee_rate))
    while qty > 0 and qty * entry * (1 + fee_rate) > free_cash:
        qty = math.nextafter(qty, 0.0)
    if qty <= 0:
        return None
    risk_amount = qty * sizing.stop_distance
    return SizingResult(
        qty=qty,
        risk_amount=risk_amount,
        risk_pct=risk_amount / equity * 100.0,
        stop_distance=sizing.stop_distance,
        notional=qty * entry,
        capped_by="free_cash",
    )
