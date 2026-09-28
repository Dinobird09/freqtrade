"""Strategy configuration with the bot's mandatory rules enforced at construction time.

Variants (parameter searches, ML layers) may only TIGHTEN the mandatory rules below.
Any attempt to loosen one raises ``ConfigError`` so a search can never silently drop a
rule. The single documented exception is ``regime_filter=False``, which the rules allow
"when explicitly testing it off"; such configs are flagged by ``is_test_only``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from types import MappingProxyType

from .models import HOUR_MS, base_of


class ConfigError(ValueError):
    """Raised when a configuration would violate a mandatory rule."""


# Stable ids used in Decision.rule and in reports. One per mandatory rule plus layers.
RULE_IDS = {
    "R1_trend": "Trend gate: close > EMA9 and close > EMA21 and EMA9 > EMA21 (4H)",
    "R2_momentum": "Momentum: RSI(14) within [50, 70] on the entry candle",
    "R3_volume": "Volume: entry-candle volume >= 1.5x the previous-20-candle average",
    "R4_regime": "Regime: close > EMA200 (unless explicitly testing it off)",
    "R5_news_blackout": "No entries within +/-2h of high-impact news; BNB +/-24h burns/launchpool",
    "R6_correlation_cap": "BTC/ETH/BNB share one risk budget; BNB never stacks",
    "R7_position_risk": "Risk <= 1% (BTC/ETH) / 0.5% (BNB); size derived from stop distance",
    "R8_structure_stop": "Stop behind a confirmed swing low with a buffer (BNB 0.5-0.8%)",
    "R9_circuit_breaker": "3 consecutive SLs bench a pair 24h; 7d realized loss limit halts all",
    "L_ml_filter": (
        "Layer: logistic-regression veto; it can only veto an entry that passed every "
        "mandatory rule and never approves one a rule denies (a veto can free R6 budget or "
        "change R9 state, which may admit other rule-compliant trades)"
    ),
    "L_expectancy_guard": "Layer: halve a pair's risk while its last-N-trade expectancy < 0",
    "X_capital": "Execution: not enough free capital / size below minimum",
    "X_operator": "Execution: new entries paused by the operator (exits keep running)",
    "L_learned_rule": "Layer: veto from a learned rule (learning.py), evidence- and backtest-gated",
    # Adoption-path gates (adoption.py); mirrored there as ADOPTION_RULE_IDS.
    "ADOPT_record": "The adoption record names the variant it tracks",
    "ADOPT_fingerprint": "The config (and ML model, if any) promoted is exactly the one tested",
    "ADOPT_backtest": "Stage 1: a completed backtest with a report",
    "ADOPT_walk_forward": "Stage 2: 70/30 walk-forward labelled ROBUST, drawdown within limit",
    "ADOPT_human_review": "Stage 3: a named human reviewed EVERY trade and approved",
    "ADOPT_testnet": "Stage 4: >= 14 days on Binance testnet, zero rule violations, journal",
    "ADOPT_live": "Stage 5: live trading (never for an explicit test-only config)",
    "ADOPT_provenance": "Stages after walk-forward need real, hash-bound data (never synthetic)",
    "ADOPT_holdout": "At most m adoptable TEST looks per pair on an overlapping TEST window",
    "ADOPT_test_only": "Test-only / stress configs never go past walk-forward",
}

# Hard ceilings from the mandate. Variants may go lower, never higher.
MANDATE_MAX_RISK_PCT = {"BTC": 1.0, "ETH": 1.0, "BNB": 0.5}
MANDATE_BNB_BUFFER_RANGE = (0.5, 0.8)
MANDATE_MIN_RR = 2.0
MANDATE_RSI_RANGE = (50.0, 70.0)
MANDATE_MIN_VOL_MULT = 1.5
MANDATE_MAX_NEWS_BLACKOUT_FLOOR_H = 2.0  # blackout may be widened, never narrowed below 2h
MANDATE_BNB_EVENT_FLOOR_H = 24.0
MANDATE_MAX_CONSEC_SL = 3
MANDATE_MIN_BENCH_H = 24.0

# Harness sanity bounds (not from the mandate): keep research runs realistic.
MIN_FEE_RATE = 0.0005  # 0.05 % per side; zero-cost backtests are optimistic by construction
MIN_SLIPPAGE_PCT = 0.01
MAX_WEEKLY_LOSS_LIMIT_PCT = 10.0
STOP_DISTANCE_BOUNDS_PCT = (0.05, 25.0)


@dataclass(frozen=True, slots=True)
class PairRisk:
    max_risk_pct: float  # percent of trading capital risked per trade (at the stop)
    stop_buffer_pct: float  # percent placed below the swing low


def _default_pair_risk() -> Mapping[str, PairRisk]:
    return MappingProxyType(
        {
            "BTC": PairRisk(max_risk_pct=1.0, stop_buffer_pct=0.25),
            "ETH": PairRisk(max_risk_pct=1.0, stop_buffer_pct=0.25),
            "BNB": PairRisk(max_risk_pct=0.5, stop_buffer_pct=0.65),
        }
    )


@dataclass(frozen=True, slots=True)
class StrategyConfig:
    # --- data ---
    timeframe_ms: int = 4 * HOUR_MS

    # --- R1-R4 entry signal ---
    ema_fast: int = 9
    ema_slow: int = 21
    ema_regime: int = 200
    rsi_period: int = 14
    rsi_min: float = 50.0
    rsi_max: float = 70.0
    vol_lookback: int = 20
    vol_mult: float = 1.5
    regime_filter: bool = True

    # --- R7 / R8 risk & exits ---
    reward_risk: float = 2.0  # take-profit distance in multiples of the stop distance
    pair_risk: Mapping[str, PairRisk] = field(default_factory=_default_pair_risk)
    swing_pivot_k: int = 2  # a pivot low needs k higher lows on each side (confirmed k bars later)
    swing_lookback: int = 30  # candles searched backwards for the most recent confirmed pivot
    fallback_lookback: int = 10  # if no pivot: lowest low of the last N candles
    min_stop_distance_pct: float = 0.3  # reject stops tighter than this (noise)
    max_stop_distance_pct: float = 10.0  # reject stops wider than this (no structure)

    # --- R6 correlation cap ---
    correlated_cluster: tuple[str, ...] = ("BTC", "ETH", "BNB")
    cluster_risk_budget_pct: float = 1.0  # total open risk across the cluster
    exclusive_bases: tuple[str, ...] = ("BNB",)  # never open alongside any other cluster member
    min_trade_risk_pct: float = 0.25  # below this remaining budget, skip instead of shrinking

    # --- R5 news blackout ---
    news_blackout_hours: float = 2.0  # +/- window around high-impact events
    bnb_event_blackout_hours: float = 24.0  # +/- window around BNB burn / launchpool events
    bnb_event_kinds: tuple[str, ...] = ("bnb_burn", "launchpool")
    exchange_id: str = "binance"  # "EXCHANGE:<id>" scoped events apply to all pairs

    # --- R9 circuit breakers ---
    consecutive_sl_limit: int = 3
    bench_hours: float = 24.0
    loss_window_days: float = 7.0
    weekly_loss_limit_pct: float = 3.0  # halt all entries if trailing-7d realized loss exceeds this

    # --- execution model ---
    fee_rate: float = 0.001  # per side, fraction of notional (Binance spot taker)
    slippage_pct: float = 0.05  # adverse slippage on market fills (entries, stop fills)
    starting_capital: float = 10_000.0
    allow_leverage: bool = False
    # Stop-fill stress (research only, CONTRACT v4 D4): 0 = touch model (stop*(1-slip));
    # k > 0 fills a non-gap stop at stop - k*(stop - exit_candle.low), minus slippage.
    stop_fill_wick_k: float = 0.0

    # --- optional layers (off by default; must pass the adoption path to be enabled) ---
    expectancy_guard: bool = False
    guard_window: int = 20
    guard_risk_mult: float = 0.5

    def __post_init__(self) -> None:
        # Freeze caller-supplied containers so validation cannot be bypassed by mutating the
        # original dict/list after construction.
        frozen_pairs = {
            str(k): PairRisk(float(v.max_risk_pct), float(v.stop_buffer_pct))
            for k, v in dict(self.pair_risk).items()
        }
        object.__setattr__(self, "pair_risk", MappingProxyType(frozen_pairs))
        for name in ("correlated_cluster", "exclusive_bases", "bnb_event_kinds"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        self.validate()

    # ------------------------------------------------------------------ validation
    def validate(self) -> None:  # noqa: C901 - flat list of independent mandate checks
        nonfinite = self._nonfinite_fields()
        if nonfinite:
            # Checked first: NaN compares False with everything, so it would slip past
            # every range check below (e.g. reward_risk=NaN would drop the 2:1 rule).
            raise ConfigError(f"non-finite values are not allowed: {', '.join(nonfinite)}")
        errs: list[str] = []
        if (self.ema_fast, self.ema_slow, self.ema_regime) != (9, 21, 200):
            errs.append("EMA periods are fixed by the mandate at 9/21/200")
        if self.rsi_period != 14:
            errs.append("RSI period is fixed by the mandate at 14")
        lo, hi = MANDATE_RSI_RANGE
        if not (lo <= self.rsi_min < self.rsi_max <= hi):
            errs.append(f"RSI window must lie in [{lo}, {hi}] (got {self.rsi_min}-{self.rsi_max})")
        if self.vol_lookback != 20:
            errs.append("volume average lookback is fixed by the mandate at 20 candles")
        if self.vol_mult < MANDATE_MIN_VOL_MULT:
            errs.append(f"vol_mult must be >= {MANDATE_MIN_VOL_MULT} (got {self.vol_mult})")
        if self.reward_risk < MANDATE_MIN_RR:
            errs.append(f"reward_risk must be >= {MANDATE_MIN_RR}:1 (got {self.reward_risk})")
        for base, pr in self.pair_risk.items():
            cap = MANDATE_MAX_RISK_PCT.get(base)
            if cap is None:
                errs.append(f"{base}: no mandated risk cap; only {sorted(MANDATE_MAX_RISK_PCT)}")
                continue
            if not (0 < pr.max_risk_pct <= cap):
                errs.append(f"{base}: max_risk_pct must be in (0, {cap}] (got {pr.max_risk_pct})")
            if pr.stop_buffer_pct <= 0:
                errs.append(f"{base}: stop buffer must be > 0 (stops sit BEHIND structure)")
        bnb = self.pair_risk.get("BNB")
        if bnb is not None:
            blo, bhi = MANDATE_BNB_BUFFER_RANGE
            if not (blo <= bnb.stop_buffer_pct <= bhi):
                errs.append(f"BNB buffer must be in [{blo}, {bhi}]% (got {bnb.stop_buffer_pct})")
        cluster_caps = [
            self.pair_risk[b].max_risk_pct for b in self.correlated_cluster if b in self.pair_risk
        ]
        max_cluster_cap = max(cluster_caps, default=0.0)
        if self.cluster_risk_budget_pct > max_cluster_cap + 1e-12:
            errs.append(
                "cluster_risk_budget_pct may not exceed the largest single-pair cap "
                f"({max_cluster_cap}%): the cluster shares ONE risk budget"
            )
        if not set(self.correlated_cluster) >= {"BTC", "ETH", "BNB"}:
            errs.append("BTC, ETH and BNB must all be in the correlated cluster")
        if "BNB" not in self.exclusive_bases:
            errs.append("BNB must be exclusive (never stacks with another cluster position)")
        if self.news_blackout_hours < MANDATE_MAX_NEWS_BLACKOUT_FLOOR_H:
            errs.append("news blackout may not be narrower than +/-2h")
        if self.bnb_event_blackout_hours < MANDATE_BNB_EVENT_FLOOR_H:
            errs.append("BNB burn/launchpool blackout may not be narrower than 24h")
        if self.consecutive_sl_limit > MANDATE_MAX_CONSEC_SL or self.consecutive_sl_limit < 1:
            errs.append("consecutive_sl_limit must be in [1, 3]")
        if self.bench_hours < MANDATE_MIN_BENCH_H:
            errs.append("bench_hours may not be shorter than 24h")
        if not (0 < self.weekly_loss_limit_pct <= MAX_WEEKLY_LOSS_LIMIT_PCT):
            errs.append(f"7-day loss limit must be in (0, {MAX_WEEKLY_LOSS_LIMIT_PCT}]%")
        if self.loss_window_days < 7:
            errs.append("the realized-loss window must be >= 7 days")
        if self.allow_leverage:
            errs.append("leverage is not supported by this research harness")
        errs.extend(self._harness_errors())
        if errs:
            raise ConfigError("; ".join(errs))

    def _nonfinite_fields(self) -> list[str]:
        bad: list[str] = []
        for f in fields(self):
            v = getattr(self, f.name)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and not math.isfinite(v):
                bad.append(f.name)
        for base, pr in self.pair_risk.items():
            for name in ("max_risk_pct", "stop_buffer_pct"):
                if not math.isfinite(getattr(pr, name)):
                    bad.append(f"pair_risk[{base}].{name}")
        return bad

    def _harness_errors(self) -> list[str]:  # noqa: C901 - flat list of independent checks
        errs: list[str] = []
        if self.timeframe_ms != 4 * HOUR_MS:
            errs.append("timeframe is fixed by the mandate at 4H (R1 is a 4H trend gate)")
        if not {"bnb_burn", "launchpool"} <= set(self.bnb_event_kinds):
            errs.append("bnb_event_kinds must include 'bnb_burn' and 'launchpool' (R5)")
        if self.swing_pivot_k < 1:
            errs.append("swing_pivot_k must be >= 1 (a pivot needs higher lows on each side)")
        if self.swing_lookback < 2 * self.swing_pivot_k + 1:
            errs.append("swing_lookback must be >= 2*swing_pivot_k+1 to find a confirmed pivot")
        if self.fallback_lookback < 2:
            errs.append("fallback_lookback must be >= 2 (a stop behind more than one candle)")
        if not (0 < self.guard_risk_mult <= 1):
            errs.append("guard_risk_mult must be in (0, 1]: a layer may only reduce risk")
        if self.guard_window < 1:
            errs.append("guard_window must be >= 1")
        if self.min_trade_risk_pct <= 0:
            errs.append("min_trade_risk_pct must be > 0")
        if self.starting_capital <= 0:
            errs.append("starting_capital must be > 0")
        if not (0.0 <= self.stop_fill_wick_k <= 1.0):
            errs.append("stop_fill_wick_k must be in [0, 1] (0 = touch fill model)")
        if self.fee_rate < MIN_FEE_RATE:
            errs.append(f"fee_rate must be >= {MIN_FEE_RATE} per side (realistic costs)")
        if self.slippage_pct < MIN_SLIPPAGE_PCT:
            errs.append(f"slippage_pct must be >= {MIN_SLIPPAGE_PCT}% (realistic costs)")
        lo, hi = STOP_DISTANCE_BOUNDS_PCT
        if not (lo <= self.min_stop_distance_pct < self.max_stop_distance_pct <= hi):
            errs.append(f"stop distance bounds must satisfy {lo} <= min < max <= {hi} (percent)")
        return errs

    # ------------------------------------------------------------------ helpers
    @property
    def is_test_only(self) -> bool:
        """True for research-only configs: regime filter off, or a stop-fill stress model.

        Such configs may be backtested and walk-forward tested but never adopted.
        """
        return (not self.regime_filter) or self.stop_fill_wick_k > 0

    def risk_for(self, pair: str) -> PairRisk:
        base = base_of(pair)
        if base not in self.pair_risk:
            raise ConfigError(f"{pair}: not a configured tradable pair")
        return self.pair_risk[base]

    def with_changes(self, **changes: object) -> StrategyConfig:
        """Return a validated copy with ``changes`` applied (raises ConfigError if illegal)."""
        return replace(self, **changes)  # type: ignore[arg-type]

    def variant_id(self) -> str:
        """Compact id of the parameters a discovery search may vary."""
        parts = [
            f"rr{self.reward_risk:g}",
            f"vol{self.vol_mult:g}",
            f"rsi{self.rsi_min:g}-{self.rsi_max:g}",
        ]
        if not self.regime_filter:
            parts.append("regimeOFF")
        if self.expectancy_guard:
            parts.append("guard")
        if self.stop_fill_wick_k > 0:
            parts.append(f"wick{self.stop_fill_wick_k:g}")
        return "_".join(parts)
