"""R7 position sizing: the position size is DERIVED from the stop distance, never the reverse.

Cost-aware risk (CONTRACT.md v2 amendment A1). With fill price ``E`` (entry slippage already
included), stop ``S``, fee rate ``f = cfg.fee_rate`` per side and ``s = cfg.slippage_pct/100``:

- the stop is assumed to fill at ``S_x = S * (1 - s)`` (:func:`stop_fill_price`);
- the ALL-IN loss per unit at the stop is ``L_u = (E - S_x) + f*E + f*S_x``
  (:func:`loss_per_unit`): price loss plus the entry fee plus the exit fee;
- ``qty = equity * risk_pct / 100 / L_u``, so a trade stopped out at ``S_x`` loses exactly
  ``risk_pct`` percent of equity AFTER fees and slippage (a clean stop is exactly -1R);
- ``risk_amount = qty * L_u`` and ``SizingResult.stop_distance`` stays ``E - S``.

A wider stop means a smaller position; the stop is never moved to make a desired size fit.
:func:`cost_aware_target` gives the take-profit ``T = (E*(1+f) + reward_risk*L_u) / (1-f)``
whose NET win is exactly ``reward_risk`` times the all-in risk (the 2:1 minimum holds net of
costs; the price-distance ratio ``(T-E)/(E-S)`` is then larger than ``reward_risk``).

No leverage: if ``qty * entry * (1 + fee_rate)`` would exceed equity, the quantity is
shrunk until the entry (notional plus entry fee) fits and ``capped_by="notional"`` is set;
``risk_amount`` is then recomputed as ``qty * L_u``, so the realised risk of a capped trade
is LOWER than requested, never higher.

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


def stop_fill_price(stop: float, cfg: StrategyConfig) -> float:
    """Assumed stop exit price ``S_x = stop * (1 - slippage_pct / 100)`` (A1)."""
    return stop * (1.0 - cfg.slippage_pct / 100.0)


def loss_per_unit(entry: float, stop: float, cfg: StrategyConfig) -> float:
    """All-in loss per unit at the stop: ``(entry - S_x) + fee*entry + fee*S_x`` (A1).

    ``entry`` is the fill price including entry slippage; ``S_x`` is :func:`stop_fill_price`.
    """
    stop_x = stop_fill_price(stop, cfg)
    return (entry - stop_x) + cfg.fee_rate * entry + cfg.fee_rate * stop_x


def cost_aware_target(
    entry: float, stop: float, cfg: StrategyConfig, reward_risk: float | None = None
) -> float:
    """Take-profit whose NET win equals ``reward_risk`` (default ``cfg.reward_risk``) times the
    all-in risk: ``T = (entry*(1+fee) + reward_risk*L_u) / (1-fee)`` (A1).

    A limit fill at ``T`` nets ``qty*(T-entry) - fee*qty*(entry+T) = reward_risk*qty*L_u``.
    """
    rr = cfg.reward_risk if reward_risk is None else reward_risk
    fee = cfg.fee_rate
    if not 0.0 <= fee < 1.0:
        raise ValueError(f"fee_rate must be in [0, 1) (got {fee})")
    return (entry * (1.0 + fee) + rr * loss_per_unit(entry, stop, cfg)) / (1.0 - fee)


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
    """R7 + A1: size a long so that a stop fill at ``stop * (1 - slippage)``, with the fee
    paid on both the entry and the exit, loses exactly ``risk_pct`` % of ``equity``.

    ``entry`` is the fill price incl. entry slippage. Raises ``ValueError`` if
    ``stop >= entry``, ``equity <= 0``, ``risk_pct <= 0``, any input is not finite, or
    ``risk_pct`` exceeds the largest mandated cap. The per-pair cap is the caller's job
    (see :func:`max_risk_pct` / :func:`size_for_pair`). A notional cap only shrinks
    ``qty``; ``risk_amount = qty * L_u`` is recomputed afterwards, so risk only decreases.
    """
    _validate_inputs(equity, entry, stop, risk_pct)
    unit_loss = loss_per_unit(entry, stop, cfg)
    if not (math.isfinite(unit_loss) and unit_loss > 0):
        raise ValueError(f"all-in loss per unit must be > 0 (got {unit_loss!r})")
    qty = equity * risk_pct / 100.0 / unit_loss
    capped_by: str | None = None
    if qty * entry * (1 + cfg.fee_rate) > equity:
        qty = _fit_notional(qty, entry, cfg.fee_rate, equity)
        capped_by = "notional"
    risk_amount = qty * unit_loss
    return SizingResult(
        qty=qty,
        risk_amount=risk_amount,
        risk_pct=risk_amount / equity * 100.0,
        stop_distance=entry - stop,
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
