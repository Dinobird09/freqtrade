"""Crafted candle scenarios for the gatekeeper / backtester / invariants tests (W4).

A :class:`Scenario` is a deterministic zig-zag uptrend (closes x 1.008, 1.006, 0.993, 0.994,
1.007, 1.005 repeating) with flat volume. On it, after the 200-candle EMA warm-up, every
candle with ``i % 6 == 4`` passes R1 (trend), R2 (RSI ~64) and R4 (regime) and has a
confirmed pivot-low stop ~2.5 % below its close; it becomes a full R1-R4 signal only when
:meth:`Scenario.spike` gives it a volume spike (R3). Outcomes are then forced with wicks
and gaps on LATER candles, which never change the indicators (closes only) or the stop of
an earlier signal (``find_stop`` reads candles up to the signal only).

Levels follow CONTRACT.md v2 A1 and are computed here from the formulas, independently of
``sizing.py``: :meth:`Scenario.unit_loss` is the all-in loss per unit at the stop and
:meth:`Scenario.target` the take-profit whose net win is ``reward_risk`` times it.

:func:`drive_live_session` plays the EXCHANGE for a ``gatekeeper.LiveSession`` candle by
candle (CONTRACT v4 D5 parity), handing it only :class:`Prefix` views that raise
``IndexError`` beyond the candle that just closed.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from research.trendbot.backtester import exit_on_candle, fill_candle_index
from research.trendbot.config import StrategyConfig
from research.trendbot.gatekeeper import DecisionRecord, LiveSession
from research.trendbot.indicators import compute_features
from research.trendbot.journal import read_journal, write_journal
from research.trendbot.models import EXIT_END, HOUR_MS, Candle, EntryFilter, NewsEvent, Trade
from research.trendbot.structure import find_stop


TF = 4 * HOUR_MS
T0 = 1_704_067_200_000  # 2024-01-01T00:00:00Z
PATTERN = (1.008, 1.006, 0.993, 0.994, 1.007, 1.005)
BASE_VOLUME = 100.0
SPIKE_VOLUME = 400.0
FIRST_SIGNAL = 208  # first i >= 200 with i % 6 == 4


def slot(k: int) -> int:
    """The k-th usable signal index (k = 0, 1, 2, ...), 6 candles apart."""
    return FIRST_SIGNAL + 6 * k


def ts(i: int) -> int:
    return T0 + i * TF


class Scenario:
    """``amp`` scales every move and wick (0.2 -> stops ~0.5 % below the close)."""

    def __init__(
        self, pair: str = "BTC/USDT", n: int = 330, start: float = 100.0, amp: float = 1.0
    ) -> None:
        self.pair = pair
        self.candles: list[Candle] = []
        o = start
        for t in range(n):
            c = o * (1 + (PATTERN[t % 6] - 1) * amp)
            hi = max(o, c) * (1 + 0.001 * amp)
            lo = min(o, c) * (1 - (0.003 if c < o else 0.001) * amp)
            self.candles.append(Candle(ts(t), o, hi, lo, c, BASE_VOLUME))
            o = c

    # ------------------------------------------------------------------ signals
    def spike(self, *idxs: int) -> Scenario:
        for i in idxs:
            self.candles[i] = replace(self.candles[i], volume=SPIKE_VOLUME)
        return self

    def stop(self, i: int, cfg: StrategyConfig) -> float:
        plan, why = find_stop(self.candles, i, self.pair, cfg)
        assert plan is not None, why
        return plan.stop

    def entry(self, i: int, cfg: StrategyConfig) -> float:
        return self.candles[i + 1].open * (1 + cfg.slippage_pct / 100)

    def unit_loss(self, i: int, cfg: StrategyConfig) -> float:
        """A1 ``L_u = (E - S_x) + f*E + f*S_x`` with ``S_x = stop * (1 - slippage)``."""
        return unit_loss(self.entry(i, cfg), self.stop(i, cfg), cfg)

    def target(self, i: int, cfg: StrategyConfig) -> float:
        """A1 ``T = (E*(1+f) + reward_risk*L_u) / (1-f)``."""
        e, f = self.entry(i, cfg), cfg.fee_rate
        return (e * (1 + f) + cfg.reward_risk * self.unit_loss(i, cfg)) / (1 - f)

    # ------------------------------------------------------------------ outcomes
    def low(self, j: int, price: float) -> Scenario:
        c = self.candles[j]
        assert price <= min(c.open, c.close)
        self.candles[j] = replace(c, low=price)
        return self

    def high(self, j: int, price: float) -> Scenario:
        c = self.candles[j]
        assert price >= max(c.open, c.close)
        self.candles[j] = replace(c, high=price)
        return self

    def gap_open(self, j: int, price: float) -> Scenario:
        c = self.candles[j]
        self.candles[j] = replace(c, open=price, low=min(c.low, price), high=max(c.high, price))
        return self

    def stop_out(self, i: int, cfg: StrategyConfig, j: int | None = None) -> Scenario:
        """Wick below the stop of signal ``i`` at candle ``j`` (default: the one after the fill)."""
        return self.low(i + 2 if j is None else j, self.stop(i, cfg) * 0.999)

    def take_profit(self, i: int, cfg: StrategyConfig, j: int | None = None) -> Scenario:
        return self.high(i + 2 if j is None else j, self.target(i, cfg) * 1.01)


def unit_loss(entry: float, stop: float, cfg: StrategyConfig) -> float:
    """A1 all-in loss per unit at the stop (price loss to ``stop*(1-s)`` plus both fees)."""
    stop_x = stop * (1 - cfg.slippage_pct / 100)
    return (entry - stop_x) + cfg.fee_rate * entry + cfg.fee_rate * stop_x


def bench_scenario(cfg: StrategyConfig) -> Scenario:
    """BNB: three stop-losses, the third one on its FILL candle ``slot(2) + 1``, then signals at
    ``slot(3)`` and ``slot(4)``. ``slot(3)`` is decided exactly 24h after the third SL's exit
    candle OPEN (so a bench counted from the recorded exit_ts would just have lifted) but only
    20h after that candle's CLOSE: under A2 it must be benched. ``slot(4)`` (44h) trades."""
    bnb = Scenario("BNB/USDT", start=10.0)
    for k in range(3):
        bnb.spike(slot(k)).stop_out(slot(k), cfg, j=slot(k) + (1 if k == 2 else 2))
    return bnb.spike(slot(3), slot(4))


def news(i: int, offset_h: float, scope: str = "ALL", kind: str = "macro", impact: str = "high"):
    """An event ``offset_h`` hours after the DECISION time of signal candle ``i``."""
    return NewsEvent(ts(i + 1) + round(offset_h * HOUR_MS), scope, impact, kind, "test")


def data_of(*scenarios: Scenario) -> dict[str, list[Candle]]:
    return {s.pair: s.candles for s in scenarios}


# ---------------------------------------------------------------------------- live driver
class Prefix(Sequence):
    """Read-only view of ``seq[:n]``. Any index at or beyond ``n`` raises ``IndexError`` (and
    slices are clipped at ``n``), so whoever holds it cannot read a later candle."""

    __slots__ = ("_n", "_seq")

    def __init__(self, seq: Sequence, n: int) -> None:
        if not 0 <= n <= len(seq):
            raise ValueError(f"prefix length {n} outside [0, {len(seq)}]")
        self._seq, self._n = seq, n

    def __len__(self) -> int:
        return self._n

    def __getitem__(self, idx):  # type: ignore[override]
        if isinstance(idx, slice):
            return [self._seq[k] for k in range(*idx.indices(self._n))]
        k = idx + self._n if idx < 0 else idx
        if not 0 <= k < self._n:
            raise IndexError(f"index {idx} is beyond the {self._n} closed candles")
        return self._seq[k]


@dataclass
class LiveRun:
    trades: list[Trade]  # closed trades in close order (final session)
    decision_log: list[DecisionRecord]  # every session's log, concatenated
    breaker_log: list[tuple[int, str]]
    equity_curve: list[tuple[int, float]]
    final_equity: float
    session: LiveSession  # the last session
    # (timestamp after which the session was rebuilt from its journal, trades open then)
    restarts: list[tuple[int, list[Trade]]] = field(default_factory=list)

    def denials(self) -> list[tuple[int, str, str, str]]:
        """Denied signals as ``BacktestResult.decision_log`` rows (ts, pair, rule, reason)."""
        return [(d.signal_ts, d.pair, d.rule, d.reason) for d in self.decision_log if not d.allowed]


class _Exchange:
    """The simulated exchange + bot loop around one LiveSession (see drive_live_session)."""

    def __init__(
        self,
        data: Mapping[str, Sequence[Candle]],
        cfg: StrategyConfig,
        events: list[NewsEvent],
        offset: int,
        exit_fill_ms: int,
        entry_filter: EntryFilter | None,
    ) -> None:
        self.cfg, self.events, self.offset = cfg, events, offset
        self.exit_fill_ms, self.entry_filter = exit_fill_ms, entry_filter
        self.slip = cfg.slippage_pct / 100.0
        self.series = {p: list(data[p]) for p in sorted(data)}
        self.rows = {p: compute_features(c, cfg) for p, c in self.series.items()}
        self.session = self.new_session()
        self.decisions: list[DecisionRecord] = []
        self.breaker_log: list[tuple[int, str]] = []

    def new_session(self, journal: Sequence[Trade] = ()) -> LiveSession:
        return LiveSession(
            self.cfg,
            self.events,
            journal,
            exit_time_uncertainty_ms=self.offset,
            entry_filter=self.entry_filter,
        )

    def exits(self, current: Mapping[str, int]) -> None:
        session = self.session
        for p in sorted(session.open_positions):
            if p not in current:
                continue
            k, t = current[p], session.open_positions[p]
            c = self.series[p][k]
            if c.ts < t.entry_ts:
                continue
            hit = exit_on_candle(c, t.stop, t.target, self.slip, self.cfg.stop_fill_wick_k)
            exit_ts = c.ts + self.exit_fill_ms
            if hit is not None:
                session.on_exit(t.trade_id, exit_ts, hit[0], hit[1])
            elif k == len(self.series[p]) - 1:
                session.on_exit(t.trade_id, exit_ts, c.close * (1 - self.slip), EXIT_END)

    def entries(self, current: Mapping[str, int]) -> None:
        session = self.session
        for p, k in current.items():
            candles = self.series[p]
            dec = session.on_candle_close(p, Prefix(candles, k + 1), Prefix(self.rows[p], k + 1))
            if not dec.allowed:
                continue
            j, why = fill_candle_index(candles, k, dec.decision_ts)
            if j is None:
                session.on_fill_skipped(p, dec, why)
                continue
            market = candles[j].open
            session.on_fill(p, dec, market, market * (1 + self.slip))

    def restart(self, journal_path: Path) -> list[Trade]:
        """Write the journal, then REBUILD the session from the file alone."""
        self.decisions.extend(self.session.decision_log)
        self.breaker_log.extend(self.session.breaker_log)
        still_open = list(self.session.open_positions.values())
        write_journal(self.session.journal_trades(), journal_path)
        self.session = self.new_session(read_journal(journal_path))
        return still_open


def drive_live_session(
    data: Mapping[str, Sequence[Candle]],
    cfg: StrategyConfig,
    events: Iterable[NewsEvent] = (),
    *,
    exit_time_uncertainty_ms: int,
    exit_fill_ms: int = 0,
    restarts: Sequence[tuple[int, bool]] = (),
    journal_path: Path | None = None,
    entry_filter: EntryFilter | None = None,
) -> LiveRun:
    """Drive a LiveSession through ``data`` exactly like ``run_backtest``'s loop.

    At each timestamp T (union of all candle times, ascending): (1) exits of candle T for the
    open positions, pairs sorted, with the backtest exit model (``exit_on_candle``; a
    position whose pair has no later candle is force-closed at the close, EXIT_END) reported
    through ``on_exit`` with ``exit_ts = T + exit_fill_ms``; (2) for each pair with a candle
    at T, sorted, ``on_candle_close`` on the prefix up to T, then ``on_fill`` at the next
    candle's open * (1 + slippage) if that candle opens at the decision time, else
    ``on_fill_skipped`` with the backtester's reason. ``exit_fill_ms = 0`` with
    ``exit_time_uncertainty_ms = cfg.timeframe_ms`` is the backtest convention;
    ``exit_fill_ms = tf // 2`` with offset 0 journals "real" mid-candle fill times.

    ``restarts``: ``(after_ts, needs_open)`` points. At the first timestamp >= ``after_ts``
    (after its exits and entries; if ``needs_open``, the first one with a position OPEN) the
    session's journal is written to ``journal_path`` and a brand-new session is rebuilt from
    ``read_journal`` of that file alone. Features come from
    ``compute_features`` on the full series, handed over as a Prefix: the indicators are
    causal and prefix-exact (the parity test re-checks that on every signal candle).
    """
    ex = _Exchange(data, cfg, list(events), exit_time_uncertainty_ms, exit_fill_ms, entry_filter)
    run = LiveRun([], ex.decisions, ex.breaker_log, [], 0.0, ex.session)
    pending = sorted(restarts)
    if pending and journal_path is None:
        raise ValueError("a restart needs a journal_path")
    ptr = dict.fromkeys(ex.series, 0)
    for ts in sorted({c.ts for cs in ex.series.values() for c in cs}):
        current: dict[str, int] = {}
        for p, candles in ex.series.items():
            k = ptr[p]
            if k < len(candles) and candles[k].ts == ts:
                current[p], ptr[p] = k, k + 1
        ex.exits(current)
        ex.entries(current)
        due = [r for r in pending if r[0] <= ts and (ex.session.open_positions or not r[1])]
        if due:
            pending = [r for r in pending if r not in due]
            run.restarts.append((ts, ex.restart(journal_path)))  # type: ignore[arg-type]
    ex.decisions.extend(ex.session.decision_log)
    ex.breaker_log.extend(ex.session.breaker_log)
    run.trades = list(ex.session.closed)
    run.equity_curve = list(ex.session.equity_curve)
    run.final_equity = ex.session.equity
    run.session = ex.session
    return run
