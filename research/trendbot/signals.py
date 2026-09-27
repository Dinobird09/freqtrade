"""Entry gates R1-R4 on one FeatureRow (known at the close of the signal candle).

All four gates are ALWAYS evaluated, in the order trend, momentum, volume, regime, so
every signal candle leaves a complete audit trail. Comparisons are written as
``not (a > b)`` style checks so a NaN input can only ever fail a gate, never pass it.
"""

from __future__ import annotations

from .config import StrategyConfig
from .models import FeatureRow, GateResult, SignalCheck


# Gate name -> stable rule id (config.RULE_IDS) for callers turning gates into Decisions.
GATE_RULE_IDS = {
    "trend": "R1_trend",
    "momentum": "R2_momentum",
    "volume": "R3_volume",
    "regime": "R4_regime",
}
INSUFFICIENT_HISTORY = "insufficient history"
REGIME_DISABLED = "disabled (explicit test variant)"


def _fmt(x: float, nd: int, *compared_to: float) -> str:
    """``x`` with ``nd`` decimals, or more decimals if rounding would make it print
    identical to a different value it is compared against (e.g. RSI 70.004 vs 70)."""
    others = [o for o in compared_to if o != x]
    for places in range(nd, 13):
        s = f"{x:.{places}f}"
        if all(s != f"{o:.{places}f}" for o in others):
            return s
    return repr(x)


def _trend_gate(row: FeatureRow, cfg: StrategyConfig) -> GateResult:
    fast, slow, close = row.ema_fast, row.ema_slow, row.close
    if fast is None or slow is None:
        return GateResult("trend", False, INSUFFICIENT_HISTORY)
    c, f, s = _fmt(close, 2, fast, slow), _fmt(fast, 2, close, slow), _fmt(slow, 2, close, fast)
    ef, es = f"EMA{cfg.ema_fast}", f"EMA{cfg.ema_slow}"
    problems = []
    if not close > fast:
        problems.append(f"close {c} not above {ef} {f}")
    if not close > slow:
        problems.append(f"close {c} not above {es} {s}")
    if not fast > slow:
        problems.append(f"{ef} {f} not above {es} {s}")
    if problems:
        return GateResult("trend", False, "; ".join(problems))
    return GateResult("trend", True, f"close {c} > {ef} {f} > {es} {s}")


def _momentum_gate(row: FeatureRow, cfg: StrategyConfig) -> GateResult:
    rsi = row.rsi
    if rsi is None:
        return GateResult("momentum", False, INSUFFICIENT_HISTORY)
    shown = _fmt(rsi, 2, cfg.rsi_min, cfg.rsi_max)
    window = f"[{cfg.rsi_min:g}, {cfg.rsi_max:g}]"
    if cfg.rsi_min <= rsi <= cfg.rsi_max:
        return GateResult("momentum", True, f"RSI {shown} within {window}")
    return GateResult("momentum", False, f"RSI {shown} outside {window}")


def _volume_gate(row: FeatureRow, cfg: StrategyConfig) -> GateResult:
    ratio = row.vol_ratio
    if ratio is None:
        return GateResult("volume", False, INSUFFICIENT_HISTORY)
    shown = _fmt(ratio, 2, cfg.vol_mult)
    ref = f"the previous-{cfg.vol_lookback}-candle average"
    if ratio >= cfg.vol_mult:
        return GateResult("volume", True, f"volume {shown}x {ref} (>= {cfg.vol_mult:g}x)")
    return GateResult("volume", False, f"volume {shown}x {ref}, below {cfg.vol_mult:g}x")


def _regime_gate(row: FeatureRow, cfg: StrategyConfig) -> GateResult:
    if not cfg.regime_filter:
        return GateResult("regime", True, REGIME_DISABLED)
    ema_regime = row.ema_regime
    if ema_regime is None:
        return GateResult("regime", False, INSUFFICIENT_HISTORY)
    c, e = _fmt(row.close, 2, ema_regime), _fmt(ema_regime, 2, row.close)
    dist = "" if row.dist_regime_pct is None else f" ({row.dist_regime_pct:+.2f}%)"
    name = f"EMA{cfg.ema_regime}"
    if row.close > ema_regime:
        return GateResult("regime", True, f"close {c} above {name} {e}{dist}")
    return GateResult("regime", False, f"close {c} not above {name} {e}{dist}")


def check_entry(pair: str, row: FeatureRow, cfg: StrategyConfig) -> SignalCheck:
    """Evaluate R1 trend, R2 momentum, R3 volume and R4 regime on ``row``.

    - trend    (R1): close > ema_fast and close > ema_slow and ema_fast > ema_slow
    - momentum (R2): cfg.rsi_min <= rsi <= cfg.rsi_max (both ends inclusive)
    - volume   (R3): vol_ratio >= cfg.vol_mult
    - regime   (R4): close > ema_regime; PASSES with detail "disabled (explicit test
      variant)" when cfg.regime_filter is False (EMA200 is then not an input at all).

    A gate whose input is None fails with detail "insufficient history". The other gates
    are still evaluated.
    """
    gates = (
        _trend_gate(row, cfg),
        _momentum_gate(row, cfg),
        _volume_gate(row, cfg),
        _regime_gate(row, cfg),
    )
    return SignalCheck(pair=pair, ts=row.ts, gates=gates)
