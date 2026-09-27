"""R7 position sizing: the position size is DERIVED from the stop distance, never the reverse.

``qty = equity * risk_pct / 100 / (entry - stop)``, so a trade stopped out exactly at its
stop loses ``risk_pct`` percent of equity (before fees and slippage). A wider stop means a
smaller position; the stop is never moved to make a desired size fit.

No leverage: if ``qty * entry * (1 + fee_rate)`` would exceed equity, the quantity is
shrunk until the entry (notional plus entry fee) fits and ``capped_by="notional"`` is set.
The realised risk of a capped trade is therefore LOWER than requested, never higher.

Per-pair caps (BTC/ETH 1 %, BNB 0.5 %) are enforced by the callers via
:func:`max_risk_pct` (or :func:`size_for_pair`), because the contract signature of
:func:`size_position` carries no pair. :func:`size_position` itself still refuses any
``risk_pct`` above the largest mandated cap of any pair (1 %), so no caller can size a
trade above the mandate by mistake.
"""

from __future__ import annotations

import math

from .config import MANDATE_MAX_RISK_PCT, StrategyConfig
from .models import SizingResult


# The largest per-trade risk the mandate allows for ANY pair (percent of equity).
ABSOLUTE_MAX_RISK_PCT = max(MANDATE_MAX_RISK_PCT.values())
_REL_TOL = 1e-12


def max_risk_pct(pair: str, cfg: StrategyConfig) -> float:
    """Configured per-trade risk cap for ``pair`` in percent (1.0 BTC/ETH, 0.5 BNB by default).

    Raises ``ConfigError`` (a ``ValueError``) for a pair that is not configured as tradable.
    """
    return cfg.risk_for(pair).max_risk_pct


def _validate_inputs(equity: float, entry: float, stop: float, risk_pct: float) -> None:
    for name, value in (("equity", equity), ("entry", entry), ("stop", stop), ("risk", risk_pct)):
        if not math.isfinite(value):
            raise ValueError(f"{name} must be a finite number (got {value!r})")
    if equity <= 0:
        raise ValueError(f"equity must be > 0 (got {equity})")
    if entry <= 0:
        raise ValueError(f"entry price must be > 0 (got {entry})")
    if stop <= 0:
        raise ValueError(f"stop price must be > 0 (got {stop})")
    if stop >= entry:
        raise ValueError(f"a long stop must be below the entry (entry {entry}, stop {stop})")
    if risk_pct <= 0:
        raise ValueError(f"risk_pct must be > 0 (got {risk_pct})")
    if risk_pct > ABSOLUTE_MAX_RISK_PCT * (1 + _REL_TOL):
        raise ValueError(
            f"risk_pct {risk_pct}% exceeds the largest mandated per-trade cap "
            f"({ABSOLUTE_MAX_RISK_PCT}%)"
        )


def _fit_notional(qty: float, entry: float, fee_rate: float, equity: float) -> float:
    """Largest qty <= ``qty`` whose notional plus entry fee does not exceed ``equity``."""
    qty = min(qty, equity / (entry * (1 + fee_rate)))
    # Step down the last ulps so that rounding can never let the cost exceed equity; the
    # test is written exactly like the capping condition in size_position.
    while qty > 0 and qty * entry * (1 + fee_rate) > equity:
        qty = math.nextafter(qty, 0.0)
    return qty


def size_position(
    equity: float, entry: float, stop: float, risk_pct: float, cfg: StrategyConfig
) -> SizingResult:
    """R7: size a long so that a fill exactly at ``stop`` loses ``risk_pct`` % of ``equity``.

    Raises ``ValueError`` if ``stop >= entry``, ``equity <= 0``, ``risk_pct <= 0``, any input
    is not finite, or ``risk_pct`` exceeds the largest mandated cap. The per-pair cap is the
    caller's job (see :func:`max_risk_pct` / :func:`size_for_pair`).
    """
    _validate_inputs(equity, entry, stop, risk_pct)
    stop_distance = entry - stop
    qty = equity * risk_pct / 100.0 / stop_distance
    capped_by: str | None = None
    if qty * entry * (1 + cfg.fee_rate) > equity:
        qty = _fit_notional(qty, entry, cfg.fee_rate, equity)
        capped_by = "notional"
    risk_amount = qty * stop_distance
    return SizingResult(
        qty=qty,
        risk_amount=risk_amount,
        risk_pct=risk_amount / equity * 100.0,
        stop_distance=stop_distance,
        notional=qty * entry,
        capped_by=capped_by,
    )


def size_for_pair(
    pair: str, equity: float, entry: float, stop: float, risk_pct: float, cfg: StrategyConfig
) -> SizingResult:
    """:func:`size_position` that first refuses (``ValueError``) a risk above the pair's cap."""
    cap = max_risk_pct(pair, cfg)
    if risk_pct > cap * (1 + _REL_TOL):
        raise ValueError(f"{pair}: risk_pct {risk_pct}% exceeds the pair's cap of {cap}%")
    return size_position(equity, entry, stop, risk_pct, cfg)
