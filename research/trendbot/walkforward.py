"""Chronological 70/30 walk-forward validation: fit and select on TRAIN, judge on TEST.

The split (:func:`split_ts`) is 70 % of the COMMON time range of all pairs (the span every
pair has data for), rounded DOWN to a whole number of candles from the common start, so it
is always a candle open time. Everything that is fitted or chosen sees TRAIN only.

TRAIN is cut on candle CLOSE (:func:`train_view`): a candle belongs to TRAIN only if
``c.ts + tf <= split``, i.e. it has CLOSED by the split. On one epoch-aligned grid that is
the same as ``c.ts < split``; on mixed grids (a pair whose candles open off the split's grid)
it drops the one candle that opens before the split but closes after it, so no TRAIN
decision, exit, forced close (EXIT_END, on the last candle that closed by the split) or
mark-to-market value ever reads a price printed after the split. (``data.load_dataset``
refuses non-epoch-aligned files; this is defence in depth for in-memory data.)

- TRAIN backtest: ``run_backtest(train_view(...), ..., start_ts=None, end_ts=split)``.
- Fitted layers (``entry_filter_factory``, e.g. ``ml_filter.make_factory()``) are fitted ONLY
  on ``enumerate_candidates(train_view(...), cfg, events, end_ts=split)``: candidates whose
  outcome is not resolved by the split are PURGED (dropped), so no TRAIN label depends on a
  TEST price. The fitted filter is then applied UNCHANGED to both the TRAIN and the TEST
  backtest. The full-TRAIN numbers of a fitted layer are therefore IN-SAMPLE (the filter was
  fit on those very signals); they are shown as context only (``train_in_sample``).
- Out-of-sample TRAIN gate of a fitted layer (CONTRACT v4 D8, :class:`TrainGate`): the TRAIN
  candidates, in ``(signal_ts, pair)`` order, are split 70/30 by count. The inner boundary
  ``b`` is the signal time of the first held-out candidate; the layer is fitted (same
  factory, no tuning) on the candidates signalled before ``b`` whose outcome was resolved by
  ``b`` (``exit_ts + tf <= b``: purged at the inner boundary), and that inner filter is
  backtested on ``[b, split)`` with fresh equity and breakers. That out-of-sample TRAIN
  backtest is the TRAIN window the LABEL judges (NO-EDGE gate, trade counts, the C5 TRAIN
  drawdown bootstrap) and the TRAIN journal an adoption record binds. If the inner fit
  raises ``InsufficientData`` the gate has no trades, so the label is UNTESTED. The model
  used on TEST is still the one fitted on ALL purged TRAIN candidates.
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
CONTRACT.md v3 C4). The drawdown check (C5/D7, :func:`metrics.dd_check`) uses the TEST
MARK-TO-MARKET max drawdown (:func:`metrics.mtm_max_dd_pct`, open positions valued at every
4H close) against ``min(15 %, the 95th percentile of the max drawdown of TRAIN trade
sequences bootstrapped at the TEST length)`` (:func:`metrics.train_dd_quantile`); both the
realised and the MTM drawdown of each window are kept on the result. The recorded
:meth:`WalkForwardResult.label_params` follow the closed D1 schema :data:`LABEL_PARAM_KEYS`.

Layers (C1): :func:`layer_diff` compares a layer variant's journal with the base's per window
by ``(pair, signal_ts)``: signals the layer vetoed, base trades absent from the layer's
journal and layer trades absent from the base's journal. A layer can only VETO an entry that
passed every mandatory rule; a veto (or the expectancy guard's smaller risk) can free R6
budget or change R9 state, admitting other rule-compliant trades the base never took.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise

from .backtester import BacktestResult, enumerate_candidates, run_backtest
from .config import StrategyConfig
from .journal import ms_to_iso
from .metrics import (
    ALPHA,
    DD_CAP_PCT,
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
INNER_FRAC = 0.7  # D8: a fitted layer's inner TRAIN split (fit on the first 70% of candidates)
DEFAULT_MAX_DD_PCT = DD_CAP_PCT  # C5/D7: 15 %
MIN_TRAIN = 30
MIN_TEST = 30
ML_LAYER = "L_ml_filter"
GUARD_LAYER = "L_expectancy_guard"
IN_SAMPLE = "in-sample"
OOS_GATE = "out-of-sample inner split"
SUMMARY_SEED = 7  # metrics.summarize seed (recorded in the adoption label_params)
# The closed D1 schema of an adoption record's walk_forward.label_params, in this order.
LABEL_PARAM_KEYS = (
    "seed",
    "n_boot",
    "m",
    "alpha",
    "max_dd_pct",
    "train_dd_p95_pct",
    "mtm_max_dd_pct",
)
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


def train_view(
    data: Mapping[str, Sequence[Candle]], split: int, timeframe_ms: int
) -> dict[str, list[Candle]]:
    """Every pair's candles that have CLOSED by ``split``: ``c.ts + timeframe_ms <= split``.

    The TRAIN window's whole input. A candle opening before the split but closing after it
    (possible only on a grid that is not the split's) is excluded, so the TRAIN backtest's
    forced close and mark-to-market value use the last candle that closed by the split.
    """
    if timeframe_ms <= 0:
        raise ValueError("timeframe_ms must be > 0")
    out: dict[str, list[Candle]] = {}
    for pair, candles in data.items():
        times = [c.ts for c in candles]
        out[pair] = list(candles[: bisect_right(times, split - timeframe_ms)])
    return out


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
class TrainGate:
    """CONTRACT v4 D8: the out-of-sample TRAIN evidence of a fitted layer.

    The purged TRAIN candidates, in ``(signal_ts, pair)`` order, are split ``INNER_FRAC`` /
    rest by count. The layer is fitted on the candidates signalled before the inner boundary
    ``inner_split_ts`` whose outcome was resolved by it (purged), and that inner filter is
    backtested on ``[inner_split_ts, split)``. ``backtest`` / ``summary`` are the TRAIN window
    the label judges; the full-TRAIN backtest of the final model is in-sample context.
    """

    inner_split_ts: int | None  # the inner boundary (None: too few candidates to split)
    n_candidates: int  # purged TRAIN candidates that were split
    n_fit: int  # candidates the inner fit saw (signalled before the boundary, resolved by it)
    n_purged: int  # signalled before the boundary but resolved after it: not in the inner fit
    backtest: BacktestResult  # the inner filter over [inner_split_ts, split); empty if unfit
    summary: Summary
    entry_filter: EntryFilter | None = None  # the inner filter (first 70% of TRAIN only)
    fit_error: str | None = None  # why the gate has no backtest (InsufficientData, no split)
    mtm_dd_pct: float = 0.0  # mark-to-market max drawdown of the gate window, %
    frac: float = INNER_FRAC  # share of the TRAIN candidates in the inner fit side

    @property
    def ran(self) -> bool:
        """False when the inner split or the inner fit failed (the gate then has no trades)."""
        return self.fit_error is None


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
    gate: TrainGate | None = None  # D8 out-of-sample TRAIN gate of a fitted layer

    @property
    def train_in_sample(self) -> bool:
        """True when a layer was FIT on TRAIN, so its full-TRAIN numbers are in-sample."""
        return self.layer is not None

    @property
    def label_train(self) -> BacktestResult:
        """The TRAIN backtest the label judges: the D8 gate for a fitted layer, else TRAIN."""
        return self.gate.backtest if self.gate is not None else self.train

    @property
    def label_train_summary(self) -> Summary:
        return self.gate.summary if self.gate is not None else self.train_summary


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
    gate: TrainGate | None = None  # D8 out-of-sample TRAIN gate of a fitted layer

    @property
    def train_in_sample(self) -> bool:
        """True when a fitted layer was applied: TRAIN numbers are in-sample, not validation."""
        return self.layer is not None

    @property
    def train_tag(self) -> str:
        """Column tag for reports: ``"TRAIN (in-sample)"`` for fitted layers, else ``"TRAIN"``."""
        return f"TRAIN ({IN_SAMPLE})" if self.train_in_sample else "TRAIN"

    @property
    def gate_tag(self) -> str:
        """Column tag of the D8 gate window: ``"TRAIN (out-of-sample inner split)"``."""
        return f"TRAIN ({OOS_GATE})"

    @property
    def label_train(self) -> BacktestResult:
        """The TRAIN backtest the label judges and the adoption record binds: the D8
        out-of-sample gate for a fitted layer, the TRAIN backtest otherwise."""
        return self.gate.backtest if self.gate is not None else self.train

    @property
    def label_train_summary(self) -> Summary:
        """Summary of :attr:`label_train` (the TRAIN numbers the label used)."""
        return self.gate.summary if self.gate is not None else self.train_summary

    @property
    def label_train_mtm_dd_pct(self) -> float:
        return self.gate.mtm_dd_pct if self.gate is not None else self.train_mtm_dd_pct

    @property
    def ran(self) -> bool:
        """False when the layer could not be fitted and no backtest was run."""
        return self.fit_error is None

    @property
    def dd_limit_pct(self) -> float:
        """The C5/D7 limit the TEST MTM drawdown was held to: min(15 %, max_dd_pct, p95)."""
        return dd_limit(self.max_dd_pct, self.train_dd_p95_pct)

    @property
    def reached_test_gate(self) -> bool:
        """TRAIN avg R > 0 and >= 30 trades in both windows: the TEST lower bound decides.

        TRAIN is :attr:`label_train_summary` (the out-of-sample gate for a fitted layer).
        """
        a, b = self.label_train_summary, self.test_summary
        return self.ran and a.avg_r > 0 and min(a.n, b.n) >= ROBUST_MIN_N

    def label_params(self) -> dict[str, object]:
        """The closed D1 schema (:data:`LABEL_PARAM_KEYS`) that makes
        ``adoption.recompute_walk_forward`` (metrics.summarize, label and dd_check over the
        written journals) reproduce this label and ``dd_ok``.

        ``train_dd_p95_pct`` is recomputable from the TRAIN journal (:attr:`label_train`);
        ``mtm_max_dd_pct`` needs the candles. Both are recorded as measured.
        """
        values = {
            "seed": self.stats.seed,
            "n_boot": self.stats.n_boot,
            "m": self.stats.m,
            "alpha": self.stats.alpha,
            "max_dd_pct": self.max_dd_pct,
            "train_dd_p95_pct": self.train_dd_p95_pct,
            "mtm_max_dd_pct": self.test_mtm_dd_pct,
        }
        return {k: values[k] for k in LABEL_PARAM_KEYS}


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


# ---------------------------------------------------------------------------- D8 gate
def inner_split(
    candidates: Sequence[CandidateOutcome], timeframe_ms: int, frac: float = INNER_FRAC
) -> tuple[int, list[CandidateOutcome], int] | None:
    """``(boundary, inner-fit candidates, n purged)`` of the D8 chronological inner split.

    Candidates in ``(signal_ts, pair)`` order; ``boundary`` is the signal time of candidate
    number ``int(frac * n)`` (the first held-out one). The inner fit gets the candidates
    signalled before it whose exit candle CLOSED by it (``exit_ts + tf <= boundary``); the
    others signalled before it are purged. None if either side would be empty.
    """
    if not 0.0 < frac < 1.0:
        raise ValueError(f"inner split fraction must be in (0, 1), got {frac}")
    ordered = sorted(candidates, key=lambda c: (c.signal_ts, c.pair))
    k = int(frac * len(ordered))
    if k < 1 or k >= len(ordered):
        return None
    boundary = ordered[k].signal_ts
    head = [c for c in ordered if c.signal_ts < boundary]
    fit = [c for c in head if c.exit_ts + timeframe_ms <= boundary]
    return boundary, fit, len(head) - len(fit)


def run_gate(
    view: Mapping[str, Sequence[Candle]],
    cfg: StrategyConfig,
    events: Sequence[NewsEvent],
    split: int,
    candidates: Sequence[CandidateOutcome],
    factory: EntryFilterFactory,
    variant: str,
    stats: Stats = DEFAULT_STATS,
    inner_frac: float = INNER_FRAC,
) -> TrainGate:
    """The D8 gate: fit ``factory`` on the inner-fit candidates, backtest ``[boundary, split)``.

    ``view`` must already be the TRAIN view (:func:`train_view`), so nothing after the split
    is read. The factory is the same one the final model uses (no tuning).
    """
    n = len(candidates)
    empty = stats.summarize([], cfg.starting_capital)
    cut = inner_split(candidates, cfg.timeframe_ms, inner_frac)
    if cut is None:
        why = f"only {n} TRAIN candidates, too few for a {inner_frac:.0%} inner split"
        placeholder = _placeholder(cfg, bool(events), (None, split), variant)
        return TrainGate(None, n, 0, 0, placeholder, empty, None, why, frac=inner_frac)
    boundary, fit, purged = cut
    window = (boundary, split)
    try:
        inner = factory(fit)
    except InsufficientData as exc:
        why = (
            f"the inner fit on the first {inner_frac:.0%} of TRAIN candidates ({len(fit)} after "
            f"purging {purged} unresolved at {ms_to_iso(boundary)}) failed: {exc}"
        )
        placeholder = _placeholder(cfg, bool(events), window, variant)
        return TrainGate(
            boundary, n, len(fit), purged, placeholder, empty, None, why, frac=inner_frac
        )
    bt = run_backtest(
        view, cfg, events, start_ts=boundary, end_ts=split, entry_filter=inner, variant=variant
    )
    return TrainGate(
        boundary,
        n,
        len(fit),
        purged,
        bt,
        stats.summarize(bt.trades, cfg.starting_capital),
        inner,
        None,
        _mtm(bt, view, cfg),
        inner_frac,
    )


def _gate_reason(gate: TrainGate, why: str) -> str:
    """The label reason of a gated layer: says which TRAIN window was judged."""
    if not gate.ran:
        return (
            f"The out-of-sample TRAIN gate (CONTRACT v4 D8) could not be evaluated ("
            f"{gate.fit_error}), so there are no out-of-sample TRAIN trades: {why}"
        )
    return (
        f"TRAIN here is the {OOS_GATE} (CONTRACT v4 D8: the layer refitted on {gate.n_fit} of "
        f"the first {gate.frac:.0%} of the {gate.n_candidates} purged TRAIN candidates, purged "
        f"at the inner boundary {ms_to_iso(gate.inner_split_ts)}, and backtested from there to "
        f"the split; the in-sample full-TRAIN figure is context only). {why}"
    )


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
    inner_frac: float = INNER_FRAC,
) -> TrainPhase:
    """TRAIN phase on :func:`train_view` (candles closed by ``split``): fit the layer on
    purged TRAIN candidates, run its D8 out-of-sample gate, then backtest ``[.., split)``.

    Reads no candle that closes after ``split``.
    """
    view = train_view(data, split, cfg.timeframe_ms)
    if entry_filter_factory is None:
        train = run_backtest(view, cfg, events, start_ts=None, end_ts=split, variant=variant)
        return TrainPhase(
            variant=variant,
            cfg=cfg,
            split_ts=split,
            train=train,
            train_summary=stats.summarize(train.trades, cfg.starting_capital),
            train_mtm_dd_pct=_mtm(train, view, cfg),
            stats=stats,
        )
    candidates = enumerate_candidates(view, cfg, events, start_ts=None, end_ts=split)
    common = {"variant": variant, "cfg": cfg, "split_ts": split, "layer": layer}
    try:
        entry_filter = entry_filter_factory(candidates)
    except InsufficientData as exc:
        return TrainPhase(
            **common,  # type: ignore[arg-type]
            train=_placeholder(cfg, bool(events), (None, split), variant),
            train_summary=stats.summarize([], cfg.starting_capital),
            n_train_candidates=len(candidates),
            fit_error=str(exc),
            stats=stats,
        )
    gate = run_gate(
        view, cfg, events, split, candidates, entry_filter_factory, variant, stats, inner_frac
    )
    train = run_backtest(
        view, cfg, events, start_ts=None, end_ts=split, entry_filter=entry_filter, variant=variant
    )
    return TrainPhase(
        **common,  # type: ignore[arg-type]
        train=train,
        train_summary=stats.summarize(train.trades, cfg.starting_capital),
        entry_filter=entry_filter,
        n_train_candidates=len(candidates),
        train_mtm_dd_pct=_mtm(train, view, cfg),
        stats=stats,
        gate=gate,
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

    The label uses ``phase.stats`` (m, alpha, n_boot, seed) for both windows and judges
    ``phase.label_train_summary`` (the D8 gate for a fitted layer); the drawdown rule compares
    the TEST mark-to-market max drawdown with the C5/D7 limit, whose TRAIN bootstrap uses the
    same judged TRAIN trades.
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
    judged = phase.label_train_summary
    verdict, why = label(judged, test_summary, min_train, min_test, stats.m, stats.alpha)
    if phase.gate is not None:
        why = _gate_reason(phase.gate, why)
    test_mtm = _mtm(test, data, cfg)
    p95 = train_dd_quantile(phase.label_train.trades, test_summary.n, stats.n_boot, stats.seed)
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
        gate=phase.gate,
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
    inner_frac: float = INNER_FRAC,
) -> WalkForwardResult:
    """One variant through TRAIN then TEST (see the module docstring).

    ``split`` overrides :func:`split_ts` (used to share one split across many variants).
    ``entry_filter_factory(train_candidates) -> EntryFilter`` is FIT ON TRAIN ONLY and then
    applied unchanged to TEST; ``InsufficientData`` yields an ``UNTESTED`` result. A fitted
    layer's label judges its D8 out-of-sample gate (``inner_frac`` inner split of TRAIN).
    ``stats`` fixes m (pre-registered candidates), alpha, n_boot and the bootstrap seed.
    """
    evs = list(events)
    s = split if split is not None else split_ts(data, train_frac, cfg.timeframe_ms)
    phase = run_train(data, cfg, evs, s, entry_filter_factory, variant, layer, stats, inner_frac)
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
