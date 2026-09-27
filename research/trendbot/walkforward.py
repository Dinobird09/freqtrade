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

Labels come from :func:`metrics.label` (expectancy with a bootstrap CI, never win rate) and
the drawdown check from :func:`metrics.dd_check` on the TEST window.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise

from .backtester import BacktestResult, enumerate_candidates, run_backtest
from .config import StrategyConfig
from .metrics import Summary, dd_check, label, summarize
from .ml_filter import InsufficientData
from .models import CandidateOutcome, Candle, EntryFilter, NewsEvent


EntryFilterFactory = Callable[[Sequence[CandidateOutcome]], EntryFilter]

TRAIN_FRAC = 0.7
DEFAULT_MAX_DD_PCT = 20.0
MIN_TRAIN = 30
MIN_TEST = 30
ML_LAYER = "L_ml_filter"
IN_SAMPLE = "in-sample"


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


# ---------------------------------------------------------------------------- phases
def run_train(
    data: Mapping[str, Sequence[Candle]],
    cfg: StrategyConfig,
    events: Sequence[NewsEvent],
    split: int,
    entry_filter_factory: EntryFilterFactory | None = None,
    variant: str = "base",
    layer: str = ML_LAYER,
) -> TrainPhase:
    """TRAIN phase: fit the layer on purged TRAIN candidates, then backtest ``[.., split)``.

    Reads no candle at or after ``split``.
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
                summarize([], cfg.starting_capital),
                layer_name,
                None,
                n_cands,
                str(exc),
            )
    train = run_backtest(
        data, cfg, events, start_ts=None, end_ts=split, entry_filter=entry_filter, variant=variant
    )
    return TrainPhase(
        variant,
        cfg,
        split,
        train,
        summarize(train.trades, cfg.starting_capital),
        layer_name,
        entry_filter,
        n_cands,
    )


def _unfitted_result(
    phase: TrainPhase, events: Sequence[NewsEvent], max_dd_pct: float
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
        test_summary=summarize([], phase.cfg.starting_capital),
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
    )


def run_test(
    data: Mapping[str, Sequence[Candle]],
    phase: TrainPhase,
    events: Sequence[NewsEvent],
    max_dd_pct: float = DEFAULT_MAX_DD_PCT,
    min_train: int = MIN_TRAIN,
    min_test: int = MIN_TEST,
) -> WalkForwardResult:
    """TEST phase: backtest ``[split, ..)`` with the TRAIN-fitted filter, label the pair."""
    if phase.fit_error is not None:
        return _unfitted_result(phase, events, max_dd_pct)
    cfg = phase.cfg
    test = run_backtest(
        data,
        cfg,
        events,
        start_ts=phase.split_ts,
        end_ts=None,
        entry_filter=phase.entry_filter,
        variant=phase.variant,
    )
    test_summary = summarize(test.trades, cfg.starting_capital)
    verdict, why = label(phase.train_summary, test_summary, min_train, min_test)
    dd_ok, dd_why = dd_check(test_summary, max_dd_pct)
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
) -> WalkForwardResult:
    """One variant through TRAIN then TEST (see the module docstring).

    ``split`` overrides :func:`split_ts` (used to share one split across many variants).
    ``entry_filter_factory(train_candidates) -> EntryFilter`` is FIT ON TRAIN ONLY and then
    applied unchanged to TEST; ``InsufficientData`` yields an ``UNTESTED`` result.
    """
    evs = list(events)
    s = split if split is not None else split_ts(data, train_frac, cfg.timeframe_ms)
    phase = run_train(data, cfg, evs, s, entry_filter_factory, variant, layer)
    return run_test(data, phase, evs, max_dd_pct, min_train, min_test)
