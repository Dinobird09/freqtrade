"""Chronological 70/30 walk-forward validation: fit and select on TRAIN, judge on TEST.

The split (:func:`split_ts`) is 70 % of the COMMON time range of all pairs (the span every
pair has data for), rounded DOWN to a whole number of candles from the common start, so it
is always a candle open time. Everything that is fitted or chosen sees TRAIN only:

- TRAIN backtest: ``run_backtest(..., start_ts=None, end_ts=split)``. The backtester cuts
  the input before ``split`` and force-closes any open trade at the last TRAIN candle, so no
  candle at or after the split is ever read.
- Fitted layers (``entry_filter_factory``, e.g. ``ml_filter.make_factory()``) are fitted ONLY
  on ``enumerate_candidates(data, cfg, events, end_ts=split)``: candidates whose outcome is
  not resolved strictly before the split are PURGED (dropped), so no TRAIN label depends on
  a TEST price. The fitted filter is then applied UNCHANGED to both the TRAIN and the TEST
  backtest. The TRAIN numbers of a fitted layer are therefore IN-SAMPLE (the filter was fit
  on those very signals) and are flagged as such (``train_in_sample``).
- TEST backtest: ``run_backtest(..., start_ts=split, end_ts=None)``. Indicators may warm up
  on earlier candles (they are causal), but the portfolio, equity and R9 circuit breakers
  start fresh at the split, so no TRAIN trade leaks into TEST.

If the layer cannot be fitted (:class:`ml_filter.InsufficientData`) the result is labelled
``UNTESTED`` with the reason, NO backtest is run for it (there is no silent fall-back to
the unfiltered strategy, whose numbers are reported separately as the baseline) and
``fit_error`` holds the message.

The two phases are exposed separately (:func:`run_train`, then :func:`run_test`) so that
``strategy_discovery`` can complete every TRAIN phase, record its selection, and only then
run any TEST backtest. :func:`walk_forward` simply chains them for one variant.

Labels come from :func:`metrics.label` (expectancy with multiplicity-adjusted iid and
calendar-month block bootstrap lower bounds, never win rate; ``m`` pre-registered candidates,
CONTRACT.md v3 C4). The drawdown check (C5, :func:`metrics.dd_check`) uses the TEST
MARK-TO-MARKET max drawdown (:func:`metrics.mtm_max_dd_pct`, open positions valued at every
4H close) against ``min(20 %, the 95th percentile of the max drawdown of TRAIN trade
sequences bootstrapped at the TEST length)`` (:func:`metrics.train_dd_quantile`); both the
realised and the MTM drawdown of each window are kept on the result.

Layers (C1): :func:`layer_diff` compares a layer variant's journal with the base's per window
by ``(pair, signal_ts)``: signals the layer vetoed, base trades absent from the layer's
journal and layer trades absent from the base's journal. A layer can only VETO an entry that
passed every mandatory rule; a veto (or the expectancy guard's smaller risk) can free R6
budget or change R9 state, admitting other rule-compliant trades the base never took.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise

from .backtester import BacktestResult, enumerate_candidates, run_backtest
from .config import StrategyConfig
from .metrics import (
    ALPHA,
    M_CANDIDATES,
    N_BOOT,
    ROBUST_MIN_N,
    Summary,
    dd_check,
    dd_limit,
    label,
    mtm_max_dd_pct,
    summarize,
    train_dd_quantile,
)
from .ml_filter import InsufficientData
from .models import CandidateOutcome, Candle, EntryFilter, NewsEvent, Trade


EntryFilterFactory = Callable[[Sequence[CandidateOutcome]], EntryFilter]

TRAIN_FRAC = 0.7
DEFAULT_MAX_DD_PCT = 20.0
MIN_TRAIN = 30
MIN_TEST = 30
ML_LAYER = "L_ml_filter"
GUARD_LAYER = "L_expectancy_guard"
IN_SAMPLE = "in-sample"
SUMMARY_SEED = 7  # metrics.summarize seed (recorded in the adoption label_params)
BASE = "base"
ML_VARIANT = "base+ml"
GUARD_VARIANT = "base+guard"
_GUARD_NOTE = re.compile(r"guard x([0-9.eE+-]+)")


# ---------------------------------------------------------------------------- split
def infer_timeframe_ms(data: Mapping[str, Sequence[Candle]]) -> int:
    """Smallest positive spacing between consecutive candles of any pair."""
    steps = [b.ts - a.ts for candles in data.values() for a, b in pairwise(candles)]
    steps = [s for s in steps if s > 0]
    if not steps:
        raise ValueError("cannot infer the timeframe: every pair needs at least two candles")
    return min(steps)


def common_range(data: Mapping[str, Sequence[Candle]], timeframe_ms: int) -> tuple[int, int]:
    """``[start, end)`` covered by EVERY pair: latest first open to earliest last close."""
    if not data or any(not candles for candles in data.values()):
        raise ValueError("walk-forward needs at least one pair and candles for every pair")
    start = max(candles[0].ts for candles in data.values())
    end = min(candles[-1].ts + timeframe_ms for candles in data.values())
    if end <= start:
        raise ValueError("the pairs have no common time range")
    return start, end


def split_ts(
    data: Mapping[str, Sequence[Candle]],
    train_frac: float = TRAIN_FRAC,
    timeframe_ms: int | None = None,
) -> int:
    """Chronological split on the COMMON time range of all pairs, on a candle boundary.

    ``split = start + floor(train_frac * n_bars) * timeframe_ms`` where ``n_bars`` is the
    number of candles in the common range ``[start, end)``. TRAIN is ``ts < split``, TEST is
    ``ts >= split``. ``timeframe_ms`` defaults to the smallest candle spacing in ``data``.
    """
    if not 0.0 < train_frac < 1.0:
        raise ValueError(f"train_frac must be in (0, 1), got {train_frac}")
    tf = timeframe_ms if timeframe_ms is not None else infer_timeframe_ms(data)
    if tf <= 0:
        raise ValueError("timeframe_ms must be > 0")
    start, end = common_range(data, tf)
    n_bars = (end - start) // tf
    k = int(train_frac * n_bars)
    if k < 1 or k >= n_bars:
        raise ValueError(f"common range of {n_bars} candles is too short for a split")
    return start + k * tf


# ---------------------------------------------------------------------------- statistics
@dataclass(frozen=True)
class Stats:
    """How every summary of one walk-forward was computed (recorded for re-computation)."""

    m: int = M_CANDIDATES  # pre-registered candidates sharing alpha (C4)
    alpha: float = ALPHA
    n_boot: int = N_BOOT
    seed: int = SUMMARY_SEED

    def summarize(self, trades: Sequence[Trade], starting_equity: float) -> Summary:
        return summarize(trades, starting_equity, self.seed, self.n_boot, self.m, self.alpha)


DEFAULT_STATS = Stats()


# ---------------------------------------------------------------------------- results
@dataclass
class TrainPhase:
    """Everything decided on TRAIN, before any TEST backtest exists."""

    variant: str
    cfg: StrategyConfig
    split_ts: int
    train: BacktestResult  # empty placeholder (no backtest run) when fit_error is set
    train_summary: Summary
    layer: str | None = None  # name of the fitted layer, if any
    entry_filter: EntryFilter | None = None  # the fitted filter, applied unchanged to TEST
    n_train_candidates: int | None = None  # purged TRAIN candidates the layer was fit on
    fit_error: str | None = None  # InsufficientData message: nothing was backtested
    train_mtm_dd_pct: float = 0.0  # mark-to-market max drawdown of the TRAIN window, %
    stats: Stats = DEFAULT_STATS

    @property
    def train_in_sample(self) -> bool:
        """True when a layer was FIT on TRAIN, so its TRAIN numbers are in-sample."""
        return self.layer is not None


@dataclass
class WalkForwardResult:
    variant: str
    split_ts: int
    train: BacktestResult
    test: BacktestResult
    train_summary: Summary
    test_summary: Summary
    label: str
    label_reason: str
    dd_ok: bool
    dd_reason: str
    cfg: StrategyConfig | None = None
    layer: str | None = None
    entry_filter: EntryFilter | None = None
    n_train_candidates: int | None = None
    fit_error: str | None = None
    max_dd_pct: float = DEFAULT_MAX_DD_PCT
    train_mtm_dd_pct: float = 0.0  # mark-to-market (4H close) max drawdown, TRAIN window, %
    test_mtm_dd_pct: float = 0.0  # mark-to-market (4H close) max drawdown, TEST window, %
    train_dd_p95_pct: float | None = None  # C5 bootstrap p95 of TRAIN max DD at TEST length
    min_train: int = MIN_TRAIN
    min_test: int = MIN_TEST
    stats: Stats = DEFAULT_STATS

    @property
    def train_in_sample(self) -> bool:
        """True when a fitted layer was applied: TRAIN numbers are in-sample, not validation."""
        return self.layer is not None

    @property
    def train_tag(self) -> str:
        """Column tag for reports: ``"TRAIN (in-sample)"`` for fitted layers, else ``"TRAIN"``."""
        return f"TRAIN ({IN_SAMPLE})" if self.train_in_sample else "TRAIN"

    @property
    def ran(self) -> bool:
        """False when the layer could not be fitted and no backtest was run."""
        return self.fit_error is None

    @property
    def dd_limit_pct(self) -> float:
        """The C5 limit the TEST MTM drawdown was held to: min(20 %, max_dd_pct, p95)."""
        return dd_limit(self.max_dd_pct, self.train_dd_p95_pct)

    @property
    def reached_test_gate(self) -> bool:
        """TRAIN avg R > 0 and >= 30 trades in both windows: the TEST lower bound decides."""
        a, b = self.train_summary, self.test_summary
        return self.ran and a.avg_r > 0 and min(a.n, b.n) >= ROBUST_MIN_N

    def label_params(self) -> dict[str, object]:
        """Keyword arguments that make ``adoption.recompute_walk_forward`` (metrics.summarize,
        label and dd_check over the written journals) reproduce this label and ``dd_ok``.

        The two drawdown numbers need the candles, so they are recorded as measured.
        """
        return {
            "seed": self.stats.seed,
            "n_boot": self.stats.n_boot,
            "m": self.stats.m,
            "alpha": self.stats.alpha,
            "max_dd_pct": self.max_dd_pct,
            "train_dd_p95_pct": self.train_dd_p95_pct,
            "mtm_max_dd_pct": self.test_mtm_dd_pct,
        }


def _placeholder(
    cfg: StrategyConfig, events_loaded: bool, window: tuple[int | None, int | None], variant: str
) -> BacktestResult:
    return BacktestResult(
        trades=[],
        decisions=Counter(),
        decision_log=[],
        equity_curve=[],
        final_equity=cfg.starting_capital,
        cfg=cfg,
        news_calendar_loaded=events_loaded,
        window=window,
        variant=variant,
    )


def _mtm(bt: BacktestResult, data: Mapping[str, Sequence[Candle]], cfg: StrategyConfig) -> float:
    return mtm_max_dd_pct(bt.trades, data, cfg.starting_capital, cfg.fee_rate)


# ---------------------------------------------------------------------------- phases
def run_train(
    data: Mapping[str, Sequence[Candle]],
    cfg: StrategyConfig,
    events: Sequence[NewsEvent],
    split: int,
    entry_filter_factory: EntryFilterFactory | None = None,
    variant: str = "base",
    layer: str = ML_LAYER,
    stats: Stats = DEFAULT_STATS,
) -> TrainPhase:
    """TRAIN phase: fit the layer on purged TRAIN candidates, then backtest ``[.., split)``.

    Reads no candle at or after ``split`` (the TRAIN backtest cuts its input there, and the
    TRAIN trades it values mark-to-market all close before the split).
    """
    layer_name = layer if entry_filter_factory is not None else None
    entry_filter: EntryFilter | None = None
    n_cands: int | None = None
    if entry_filter_factory is not None:
        candidates = enumerate_candidates(data, cfg, events, start_ts=None, end_ts=split)
        n_cands = len(candidates)
        try:
            entry_filter = entry_filter_factory(candidates)
        except InsufficientData as exc:
            empty = _placeholder(cfg, bool(events), (None, split), variant)
            return TrainPhase(
                variant,
                cfg,
                split,
                empty,
                stats.summarize([], cfg.starting_capital),
                layer_name,
                None,
                n_cands,
                str(exc),
                stats=stats,
            )
    train = run_backtest(
        data, cfg, events, start_ts=None, end_ts=split, entry_filter=entry_filter, variant=variant
    )
    return TrainPhase(
        variant,
        cfg,
        split,
        train,
        stats.summarize(train.trades, cfg.starting_capital),
        layer_name,
        entry_filter,
        n_cands,
        train_mtm_dd_pct=_mtm(train, data, cfg),
        stats=stats,
    )


def _unfitted_result(
    phase: TrainPhase, events: Sequence[NewsEvent], max_dd_pct: float, min_train: int, min_test: int
) -> WalkForwardResult:
    empty_test = _placeholder(phase.cfg, bool(events), (phase.split_ts, None), phase.variant)
    reason = (
        f"The {phase.layer} layer could not be fitted on TRAIN, so it was not tested and no "
        f"trades were simulated for it: {phase.fit_error}"
    )
    return WalkForwardResult(
        variant=phase.variant,
        split_ts=phase.split_ts,
        train=phase.train,
        test=empty_test,
        train_summary=phase.train_summary,
        test_summary=phase.stats.summarize([], phase.cfg.starting_capital),
        label="UNTESTED",
        label_reason=reason,
        dd_ok=False,
        dd_reason="Not evaluated: the layer could not be fitted, so there is no TEST window.",
        cfg=phase.cfg,
        layer=phase.layer,
        entry_filter=None,
        n_train_candidates=phase.n_train_candidates,
        fit_error=phase.fit_error,
        max_dd_pct=max_dd_pct,
        min_train=min_train,
        min_test=min_test,
        stats=phase.stats,
    )


def run_test(
    data: Mapping[str, Sequence[Candle]],
    phase: TrainPhase,
    events: Sequence[NewsEvent],
    max_dd_pct: float = DEFAULT_MAX_DD_PCT,
    min_train: int = MIN_TRAIN,
    min_test: int = MIN_TEST,
) -> WalkForwardResult:
    """TEST phase: backtest ``[split, ..)`` with the TRAIN-fitted filter, label the pair.

    The label uses ``phase.stats`` (m, alpha, n_boot, seed) for both windows; the drawdown
    rule compares the TEST mark-to-market max drawdown with the C5 limit.
    """
    if phase.fit_error is not None:
        return _unfitted_result(phase, events, max_dd_pct, min_train, min_test)
    cfg, stats = phase.cfg, phase.stats
    test = run_backtest(
        data,
        cfg,
        events,
        start_ts=phase.split_ts,
        end_ts=None,
        entry_filter=phase.entry_filter,
        variant=phase.variant,
    )
    test_summary = stats.summarize(test.trades, cfg.starting_capital)
    verdict, why = label(
        phase.train_summary, test_summary, min_train, min_test, stats.m, stats.alpha
    )
    test_mtm = _mtm(test, data, cfg)
    p95 = train_dd_quantile(phase.train.trades, test_summary.n, stats.n_boot, stats.seed)
    dd_ok, dd_why = dd_check(test_summary, max_dd_pct, p95, test_mtm)
    return WalkForwardResult(
        variant=phase.variant,
        split_ts=phase.split_ts,
        train=phase.train,
        test=test,
        train_summary=phase.train_summary,
        test_summary=test_summary,
        label=verdict,
        label_reason=why,
        dd_ok=dd_ok,
        dd_reason=dd_why,
        cfg=cfg,
        layer=phase.layer,
        entry_filter=phase.entry_filter,
        n_train_candidates=phase.n_train_candidates,
        max_dd_pct=max_dd_pct,
        train_mtm_dd_pct=phase.train_mtm_dd_pct,
        test_mtm_dd_pct=test_mtm,
        train_dd_p95_pct=p95,
        min_train=min_train,
        min_test=min_test,
        stats=stats,
    )


def walk_forward(
    data: Mapping[str, Sequence[Candle]],
    cfg: StrategyConfig,
    events: Iterable[NewsEvent] = (),
    train_frac: float = TRAIN_FRAC,
    entry_filter_factory: EntryFilterFactory | None = None,
    variant: str = "base",
    max_dd_pct: float = DEFAULT_MAX_DD_PCT,
    *,
    split: int | None = None,
    min_train: int = MIN_TRAIN,
    min_test: int = MIN_TEST,
    layer: str = ML_LAYER,
    stats: Stats = DEFAULT_STATS,
) -> WalkForwardResult:
    """One variant through TRAIN then TEST (see the module docstring).

    ``split`` overrides :func:`split_ts` (used to share one split across many variants).
    ``entry_filter_factory(train_candidates) -> EntryFilter`` is FIT ON TRAIN ONLY and then
    applied unchanged to TEST; ``InsufficientData`` yields an ``UNTESTED`` result.
    ``stats`` fixes m (pre-registered candidates), alpha, n_boot and the bootstrap seed.
    """
    evs = list(events)
    s = split if split is not None else split_ts(data, train_frac, cfg.timeframe_ms)
    phase = run_train(data, cfg, evs, s, entry_filter_factory, variant, layer, stats)
    return run_test(data, phase, evs, max_dd_pct, min_train, min_test)


# ---------------------------------------------------------------------------- layers (C1)
TradeKey = tuple[str, int]  # (pair, signal_ts)


def trade_keys(trades: Iterable[Trade]) -> set[TradeKey]:
    """``(pair, signal_ts)`` of every trade: the identity used to compare two journals."""
    return {(t.pair, t.signal_ts) for t in trades}


def guard_multiplier_of(trade: Trade) -> float | None:
    """The expectancy-guard multiplier the backtester noted on a trade (None if absent)."""
    found = _GUARD_NOTE.search(trade.notes or "")
    if found is None:
        return None
    try:
        return float(found.group(1))
    except ValueError:
        return None


@dataclass(frozen=True)
class LayerDiff:
    """One window of a layer variant compared with the base journal (CONTRACT.md v3 C1)."""

    window: str  # "TRAIN" | "TEST"
    vetoed: int  # signals the layer vetoed after every mandatory rule passed
    reduced_risk: int  # layer trades entered at a guard multiplier < 1
    base_only: int  # base trades absent from the layer's journal
    layer_only: int  # layer trades absent from the base journal (admitted by freed R6/R9)
    shared: int  # trades in both journals
    base_n: int
    layer_n: int


def layer_diff(
    base: Sequence[Trade], layer: Sequence[Trade], window: str, vetoed: int = 0
) -> LayerDiff:
    """Compare one window's journals by ``(pair, signal_ts)``; ``vetoed`` from the decisions."""
    a, b = trade_keys(base), trade_keys(layer)
    reduced = sum(1 for t in layer if (guard_multiplier_of(t) or 1.0) < 1.0)
    return LayerDiff(window, vetoed, reduced, len(a - b), len(b - a), len(a & b), len(a), len(b))


def layer_diffs(base: WalkForwardResult, layer: WalkForwardResult, rule: str) -> list[LayerDiff]:
    """TRAIN and TEST :class:`LayerDiff` of ``layer`` versus ``base`` (vetoes counted under
    ``rule``, e.g. ``L_ml_filter``); empty if the layer did not run."""
    if not layer.ran:
        return []
    return [
        layer_diff(base.train.trades, layer.train.trades, "TRAIN", layer.train.decisions[rule]),
        layer_diff(base.test.trades, layer.test.trades, "TEST", layer.test.decisions[rule]),
    ]
