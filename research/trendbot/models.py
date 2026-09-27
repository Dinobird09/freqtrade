"""Shared data types. Every module in the package exchanges these, nothing else.

Conventions (binding for every module):
- Timestamps are ``int`` milliseconds since the Unix epoch, UTC.
- ``Candle.ts`` is the candle OPEN time. A candle's information is only known at
  ``ts + timeframe_ms`` (its close); nothing may act on it earlier.
- Pairs are ccxt-style symbols, e.g. ``"BTC/USDT"``; ``base_of("BNB/USDT") == "BNB"``.
- Percentages are expressed in percent units (``1.0`` means 1 %), never fractions,
  unless a name ends in ``_frac``.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS

EXIT_SL = "SL"  # stop-loss hit (including gap-through fills at the open)
EXIT_TP = "TP"  # take-profit hit
EXIT_END = "END"  # force-closed at the end of an evaluation window (no data beyond it)


def base_of(pair: str) -> str:
    """Return the base asset of a ccxt symbol: ``"BTC/USDT" -> "BTC"``."""
    return pair.split("/", 1)[0].upper()


@dataclass(frozen=True, slots=True)
class Candle:
    ts: int  # open time, ms UTC
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class FeatureRow:
    """Indicator values as known at the CLOSE of candle ``ts``. ``None`` = not enough history."""

    ts: int  # candle open time, ms UTC
    close_ts: int  # ts + timeframe_ms: the earliest moment these values are known
    close: float
    ema_fast: float | None  # EMA(9) of closes
    ema_slow: float | None  # EMA(21) of closes
    ema_regime: float | None  # EMA(200) of closes
    rsi: float | None  # Wilder RSI(14)
    vol_avg: float | None  # mean volume of the PREVIOUS 20 candles (current candle excluded)
    vol_ratio: float | None  # volume / vol_avg
    ema_gap_pct: float | None  # (ema_fast - ema_slow) / close * 100
    dist_regime_pct: float | None  # (close - ema_regime) / ema_regime * 100
    hour_utc: int  # UTC hour of close_ts (0..23)

    def ml_features(self) -> dict[str, float] | None:
        """The engineered features used by the ML layer, or None if any is missing."""
        vals = (self.rsi, self.vol_ratio, self.ema_gap_pct, self.dist_regime_pct)
        if any(v is None for v in vals):
            return None
        return {
            "rsi": float(self.rsi),  # type: ignore[arg-type]
            "vol_ratio": float(self.vol_ratio),  # type: ignore[arg-type]
            "ema_gap_pct": float(self.ema_gap_pct),  # type: ignore[arg-type]
            "dist_regime_pct": float(self.dist_regime_pct),  # type: ignore[arg-type]
            "hour_utc": float(self.hour_utc),
        }


@dataclass(frozen=True, slots=True)
class Decision:
    """Allow/deny verdict of one rule, with a one-sentence human-readable reason."""

    allowed: bool
    rule: str  # stable rule id, e.g. "R5_news_blackout" (see config.RULE_IDS)
    reason: str


@dataclass(frozen=True, slots=True)
class GateResult:
    name: str  # "trend" | "momentum" | "volume" | "regime"
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class SignalCheck:
    pair: str
    ts: int  # open time of the signal candle
    gates: tuple[GateResult, ...]

    @property
    def passed(self) -> bool:
        return bool(self.gates) and all(g.passed for g in self.gates)

    def failed(self) -> list[GateResult]:
        return [g for g in self.gates if not g.passed]


@dataclass(frozen=True, slots=True)
class StopPlan:
    stop: float  # stop price actually used
    structure_level: float  # the swing low the stop sits behind
    buffer_pct: float  # buffer applied below the structure level, percent
    method: str  # "pivot" (confirmed swing low) | "lookback_low" (fallback)


@dataclass(frozen=True, slots=True)
class SizingResult:
    qty: float  # base-asset quantity
    # ALL-IN quote currency lost if the stop fills at stop*(1-slippage), incl. both fees:
    # qty * ((entry - stop_x) + fee_rate*entry + fee_rate*stop_x), stop_x = stop*(1-slip).
    # (CONTRACT.md v2 cost-aware risk.)
    risk_amount: float
    risk_pct: float  # risk_amount / equity * 100
    stop_distance: float  # entry - stop, quote currency per unit
    notional: float  # qty * entry
    capped_by: str | None  # None, or "notional" if qty was reduced to avoid leverage


@dataclass(frozen=True, slots=True)
class NewsEvent:
    ts: int  # event time, ms UTC
    scope: str  # "ALL", a base asset ("BNB", "BTC", ...), or "EXCHANGE:<ccxt id>"
    impact: str  # "high" | "medium" | "low"
    kind: str  # "macro" | "regulatory" | "legal" | "unlock" | "bnb_burn" | "launchpool" | "other"
    note: str = ""
    # When the event became knowable (ms UTC). None -> kind default (CONTRACT v3 C2):
    # scheduled kinds (macro, unlock, bnb_burn, launchpool) are known in advance; unscheduled
    # kinds (regulatory, legal, other) are only known from their own timestamp onward.
    known_from_ts: int | None = None


@dataclass(slots=True)
class Trade:
    trade_id: int
    pair: str
    variant: str  # strategy variant id, e.g. "base", "rr2.5_vol2.0", "base+ml"
    signal_ts: int  # open time of the signal candle (decision taken at its close)
    entry_ts: int  # open time of the fill candle (= signal_ts + timeframe_ms)
    entry_price: float  # fill price incl. slippage
    stop: float
    target: float
    qty: float
    risk_amount: float  # planned ALL-IN loss at the stop (see SizingResult.risk_amount)
    risk_pct: float  # planned risk as percent of equity at entry
    stop_method: str = ""
    exit_ts: int | None = None  # open time of the candle in which the exit filled
    exit_price: float | None = None
    exit_reason: str | None = None  # EXIT_SL | EXIT_TP | EXIT_END
    fees: float = 0.0  # total fees paid, quote currency
    pnl: float | None = None  # net of fees and slippage
    r_multiple: float | None = None  # pnl / risk_amount
    features: dict[str, float] = field(default_factory=dict)  # FeatureRow.ml_features()
    ml_prob: float | None = None  # win probability from the ML layer, if one was applied
    notes: str = ""

    @property
    def is_closed(self) -> bool:
        return self.exit_ts is not None


@dataclass(frozen=True, slots=True)
class CandidateOutcome:
    """A rule-passing signal and the outcome it WOULD have had if taken in isolation.

    Used only to build ML training labels. Portfolio constraints (correlation cap,
    circuit breakers, capital) are deliberately ignored here; the label is the pure
    SL/TP path outcome using the exact same exit model as the backtester.
    """

    pair: str
    signal_ts: int
    entry_ts: int
    exit_ts: int
    exit_reason: str  # EXIT_SL | EXIT_TP (never END: unresolved candidates are dropped)
    r_multiple: float
    features: dict[str, float]


# An optional layer consulted after all mandatory rules pass: (pair, row, check) ->
# (allow, probability_or_None, one-sentence reason). A layer can only VETO an entry that
# passed every mandatory rule; it never approves an entry a rule denies. Because a veto can
# free R6 budget or change R9 state, the resulting trade list may contain other
# rule-compliant trades the base variant did not take (reports count both directions).
EntryFilter = Callable[[str, FeatureRow, SignalCheck], tuple[bool, float | None, str]]
