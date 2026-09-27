"""R9 circuit breakers: they gate NEW ENTRIES only.

Exits are never paused: this class deliberately has no exit-related API, so no breaker can
ever block, delay or veto a stop-loss, take-profit or forced close. A trade that closes while
its pair is benched or while all entries are halted is still processed and recorded.

Two breakers (``Decision.rule == "R9_circuit_breaker"``):
- Consecutive stop-losses, per pair: the streak grows by one on every ``EXIT_SL`` and resets
  to 0 on any other exit reason. When it reaches ``cfg.consecutive_sl_limit`` the pair is
  benched for ``ts`` in ``[exit_ts, exit_ts + bench_hours)`` and the streak resets to 0.
  Other pairs are unaffected.
- Trailing realized loss, all pairs: entries are denied everywhere while the pnl sum of trades
  with ``exit_ts`` in ``(ts - loss_window_days, ts]`` is below
  ``-weekly_loss_limit_pct / 100 * equity``. It lifts by itself once the losses age out.

A decision at ``ts`` depends only on trades with ``exit_ts <= ts`` (no look-ahead), whatever
order queries arrive in, so ``from_journal`` reproduces the live state exactly. Closed trades
are kept as parallel lists sorted by ``exit_ts``; each ``can_enter`` costs two bisections plus a
sum over the (small) trailing window.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass

from .config import StrategyConfig
from .journal import ms_to_iso
from .models import DAY_MS, EXIT_SL, HOUR_MS, Decision, Trade


RULE_ID = "R9_circuit_breaker"


def _ids(trade_ids: Iterable[int]) -> str:
    return ", ".join(f"#{i}" for i in trade_ids)


@dataclass(frozen=True, slots=True)
class Bench:
    """A pair benched by the consecutive stop-loss breaker for ``start_ts <= ts < until_ts``."""

    pair: str
    start_ts: int  # exit_ts of the stop-loss that completed the streak (inclusive)
    until_ts: int  # start_ts + bench_hours (exclusive)
    trade_ids: tuple[int, ...]  # the consecutive stop-loss trades that caused the bench

    def covers(self, ts: int) -> bool:
        return self.start_ts <= ts < self.until_ts

    def sentence(self, limit: int, bench_hours: float) -> str:
        return (
            f"{self.pair} takes no new entries until {ms_to_iso(self.until_ts)} because trades "
            f"{_ids(self.trade_ids)} were {len(self.trade_ids)} consecutive stop-losses "
            f"(limit {limit}, bench {bench_hours:g}h from {ms_to_iso(self.start_ts)})."
        )


class CircuitBreakers:
    """Rule R9 state machine; feed every closed trade, ask before every entry.

    Exits are never paused: there is no method that can block or delay an exit.
    """

    __slots__ = (
        "_bench_ms",
        "_benches",
        "_cfg",
        "_closed_ids",
        "_closed_pnl",
        "_closed_ts",
        "_streaks",
        "_window_ms",
    )

    def __init__(self, cfg: StrategyConfig) -> None:
        self._cfg = cfg
        self._bench_ms = round(cfg.bench_hours * HOUR_MS)
        self._window_ms = round(cfg.loss_window_days * DAY_MS)
        self._streaks: dict[str, list[int]] = {}  # pair -> trade ids of the current SL streak
        self._benches: dict[str, list[Bench]] = {}  # pair -> benches in start order
        self._closed_ts: list[int] = []  # sorted exit_ts of every closed trade
        self._closed_pnl: list[float] = []
        self._closed_ids: list[int] = []

    # ------------------------------------------------------------------ updates
    def on_trade_closed(self, trade: Trade, equity: float | None = None) -> list[str]:
        """Record a closed trade; return one sentence per breaker that just tripped.

        ``equity`` (optional, e.g. equity after this close) is only needed to report the
        trailing-loss halt tripping; enforcement happens in ``can_enter`` either way.
        """
        if trade.exit_ts is None or trade.pnl is None:
            raise ValueError(f"trade #{trade.trade_id} is not closed (exit_ts and pnl required)")
        ts = trade.exit_ts
        halted_before = equity is not None and self._window_sum(ts) < self.halt_threshold(equity)
        idx = bisect_right(self._closed_ts, ts)
        self._closed_ts.insert(idx, ts)
        self._closed_pnl.insert(idx, float(trade.pnl))
        self._closed_ids.insert(idx, trade.trade_id)
        messages: list[str] = []
        bench = self._update_streak(trade, ts)
        if bench is not None:
            messages.append(
                f"{bench.pair} benched until {ms_to_iso(bench.until_ts)} after "
                f"{len(bench.trade_ids)} consecutive stop-losses (trades {_ids(bench.trade_ids)}, "
                f"limit {self._cfg.consecutive_sl_limit})."
            )
        if equity is not None and not halted_before:
            pnl = self._window_sum(ts)
            if pnl < self.halt_threshold(equity):
                messages.append(self._halt_sentence(ts, equity, pnl))
        return messages

    def _update_streak(self, trade: Trade, ts: int) -> Bench | None:
        streak = self._streaks.setdefault(trade.pair, [])
        if trade.exit_reason != EXIT_SL:
            streak.clear()
            return None
        streak.append(trade.trade_id)
        if len(streak) < self._cfg.consecutive_sl_limit:
            return None
        bench = Bench(trade.pair, ts, ts + self._bench_ms, tuple(streak))
        self._benches.setdefault(trade.pair, []).append(bench)
        streak.clear()
        return bench

    @classmethod
    def from_journal(cls, trades: Iterable[Trade], cfg: StrategyConfig) -> CircuitBreakers:
        """Rebuild state by replaying the closed trades in ``(exit_ts, trade_id)`` order."""
        breakers = cls(cfg)
        closed = [t for t in trades if t.exit_ts is not None]
        closed.sort(key=lambda t: (t.exit_ts, t.trade_id))
        for t in closed:
            breakers.on_trade_closed(t)
        return breakers

    # ------------------------------------------------------------------ queries
    def can_enter(self, pair: str, ts: int, equity: float) -> Decision:
        """Entry gate: deny a benched pair, or every pair while the trailing-loss halt is on."""
        bench = self.active_bench(pair, ts)
        if bench is not None:
            reason = bench.sentence(self._cfg.consecutive_sl_limit, self._cfg.bench_hours)
            return Decision(False, RULE_ID, reason)
        pnl = self._window_sum(ts)
        threshold = self.halt_threshold(equity)
        if pnl < threshold:
            return Decision(False, RULE_ID, self._halt_sentence(ts, equity, pnl))
        return Decision(
            True,
            RULE_ID,
            f"{pair} is not benched and the {self._cfg.loss_window_days:g}-day realized PnL "
            f"{pnl:.2f} is not below the halt limit {threshold:.2f}.",
        )

    def active_bench(self, pair: str, ts: int) -> Bench | None:
        """The bench covering ``pair`` at ``ts``, if any."""
        for bench in reversed(self._benches.get(pair, ())):
            if bench.start_ts <= ts:
                # Later benches start later and end later, so only this one can cover ts.
                return bench if bench.covers(ts) else None
        return None

    def benches(self, ts: int) -> list[Bench]:
        """Every bench active at ``ts``, sorted by pair."""
        found = (self.active_bench(pair, ts) for pair in sorted(self._benches))
        return [b for b in found if b is not None]

    def streak(self, pair: str) -> int:
        """Current consecutive stop-loss count of ``pair`` (after the last trade fed)."""
        return len(self._streaks.get(pair, ()))

    def halt_threshold(self, equity: float) -> float:
        """Trailing-window pnl below which all entries halt: ``-limit_pct / 100 * equity``."""
        return -self._cfg.weekly_loss_limit_pct / 100 * equity

    def window_pnl(self, ts: int) -> tuple[float, tuple[int, ...]]:
        """(pnl sum, trade ids in exit order) of trades with exit_ts in (ts - window, ts]."""
        lo, hi = self._window_bounds(ts)
        return math.fsum(self._closed_pnl[lo:hi]), tuple(self._closed_ids[lo:hi])

    def halt_lifts_at(self, ts: int, equity: float) -> int | None:
        """When the halt active at ``ts`` lifts if nothing else closes (None if not halted)."""
        lo, hi = self._window_bounds(ts)
        threshold = self.halt_threshold(equity)
        if math.fsum(self._closed_pnl[lo:hi]) >= threshold:
            return None
        for i in range(lo, hi):
            if i + 1 < hi and self._closed_ts[i + 1] == self._closed_ts[i]:
                continue  # trades sharing an exit_ts age out together
            if math.fsum(self._closed_pnl[i + 1 : hi]) >= threshold:
                return self._closed_ts[i] + self._window_ms
        return None

    # ------------------------------------------------------------------ internals
    def _window_bounds(self, ts: int) -> tuple[int, int]:
        lo = bisect_right(self._closed_ts, ts - self._window_ms)
        hi = bisect_right(self._closed_ts, ts, lo=lo)
        return lo, hi

    def _window_sum(self, ts: int) -> float:
        lo, hi = self._window_bounds(ts)
        return math.fsum(self._closed_pnl[lo:hi])

    def _halt_sentence(self, ts: int, equity: float, pnl: float) -> str:
        lo, hi = self._window_bounds(ts)
        return (
            f"All entries are halted because trades {_ids(self._closed_ids[lo:hi])} closed in the "
            f"{self._cfg.loss_window_days:g} days to {ms_to_iso(ts)} realized {pnl:.2f}, below "
            f"the -{self._cfg.weekly_loss_limit_pct:g}% limit of {self.halt_threshold(equity):.2f} "
            f"on equity {equity:.2f}."
        )
