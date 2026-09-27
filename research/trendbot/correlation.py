"""R6 correlation cap: BTC, ETH and BNB share ONE risk budget and BNB never stacks.

The three majors move together, so two full-size longs on them are one oversized bet.
:class:`CorrelationGuard` answers, for a new long on ``pair`` that wants
``requested_risk_pct``, how much risk (if any) it may take given the open positions:

1. No pyramiding: a pair that is already open is denied. An open position in the same
   base asset under another quote (``BTC/USDC`` vs ``BTC/USDT``) counts as the same pair.
   This applies to every pair, inside or outside the cluster (tighten-only reading).
2. Pairs whose base is outside ``cfg.correlated_cluster`` are allowed at the requested risk
   and do not consume the cluster budget.
3. Exclusive bases (BNB): a BNB long is denied if any other cluster position is open, and
   any cluster long is denied while a BNB position is open.
4. Shared budget: ``remaining = cluster_risk_budget_pct - sum(open cluster risk)``;
   ``allowed = min(requested, remaining)``; the trade is skipped (not shrunk further) if
   ``allowed < cfg.min_trade_risk_pct``.

Because ``config.validate`` forbids a cluster budget above the largest single-pair cap, the
sum of open cluster risk can never exceed ONE full-size position.
"""

from __future__ import annotations

import math
from collections.abc import Mapping

from .config import StrategyConfig
from .models import Decision, base_of


RULE = "R6_correlation_cap"
_EPS = 1e-12  # float slack for budget arithmetic such as 1.0 - 0.75


def _validate(requested_risk_pct: float, open_risk: Mapping[str, float]) -> None:
    if not (math.isfinite(requested_risk_pct) and requested_risk_pct > 0):
        raise ValueError(f"requested_risk_pct must be finite and > 0 (got {requested_risk_pct})")
    for open_pair, risk in open_risk.items():
        if not (math.isfinite(risk) and risk >= 0):
            raise ValueError(f"open risk for {open_pair} must be finite and >= 0 (got {risk})")


def _fmt_open(positions: Mapping[str, float]) -> str:
    return ", ".join(f"{p} {r:g}%" for p, r in positions.items()) or "none"


class CorrelationGuard:
    """Stateless R6 gate; the caller passes the currently open positions on every call."""

    def __init__(self, cfg: StrategyConfig) -> None:
        self._cluster = frozenset(b.upper() for b in cfg.correlated_cluster)
        self._exclusive = frozenset(b.upper() for b in cfg.exclusive_bases)
        self._budget = cfg.cluster_risk_budget_pct
        self._min_trade = cfg.min_trade_risk_pct

    def check(
        self, pair: str, requested_risk_pct: float, open_risk: Mapping[str, float]
    ) -> tuple[Decision, float]:
        """Return ``(decision, allowed_risk_pct)``; ``allowed_risk_pct`` is 0.0 when denied.

        ``open_risk`` maps each open pair to its planned risk in percent of equity.
        """
        _validate(requested_risk_pct, open_risk)
        base = base_of(pair)
        ordered = dict(sorted(open_risk.items()))
        same = [p for p in ordered if base_of(p) == base]
        if same:
            return self._deny(
                f"{pair} is denied because {same[0]} is already open in {base} and R6 "
                "forbids pyramiding into an open position."
            )
        if base not in self._cluster:
            return (
                Decision(
                    True,
                    RULE,
                    f"{pair} is outside the correlated cluster, so it is allowed at the "
                    f"requested {requested_risk_pct:g}% without using the cluster budget.",
                ),
                requested_risk_pct,
            )
        cluster_open = {p: r for p, r in ordered.items() if base_of(p) in self._cluster}
        denial = self._exclusivity(pair, base, cluster_open)
        if denial is not None:
            return self._deny(denial)
        return self._budget_check(pair, requested_risk_pct, cluster_open)

    def _exclusivity(self, pair: str, base: str, cluster_open: Mapping[str, float]) -> str | None:
        if base in self._exclusive and cluster_open:
            return (
                f"{pair} is denied because {base} is exclusive and never stacks with another "
                f"cluster position, but {_fmt_open(cluster_open)} is open."
            )
        blockers = [p for p in cluster_open if base_of(p) in self._exclusive]
        if blockers:
            return (
                f"{pair} is denied because exclusive cluster position {blockers[0]} is open "
                "and no other cluster long may be added alongside it."
            )
        return None

    def _budget_check(
        self, pair: str, requested: float, cluster_open: Mapping[str, float]
    ) -> tuple[Decision, float]:
        used = math.fsum(cluster_open.values())
        remaining = self._budget - used
        allowed = min(requested, remaining)
        budget_txt = (
            f"cluster budget {self._budget:g}% minus open {used:g}% "
            f"({_fmt_open(cluster_open)}) leaves {max(remaining, 0.0):g}%"
        )
        if allowed < self._min_trade - _EPS:
            return self._deny(
                f"{pair} is skipped because the {budget_txt}, below the "
                f"{self._min_trade:g}% minimum trade risk."
            )
        if allowed < requested:
            reason = f"{pair} is allowed at a reduced {allowed:g}% (requested {requested:g}%): "
        else:
            reason = f"{pair} is allowed at the requested {requested:g}%: "
        return Decision(True, RULE, reason + budget_txt + "."), allowed

    @staticmethod
    def _deny(reason: str) -> tuple[Decision, float]:
        return Decision(False, RULE, reason), 0.0
