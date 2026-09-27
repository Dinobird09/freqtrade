"""Small grid of rule TIGHTENINGS, one variant selected on TRAIN only, TEST reported after.

The search space is deliberately tiny and fixed in advance (no adaptive search):

    reward_risk in {2.0, 2.5, 3.0} x vol_mult in {1.5, 2.0} x RSI window in {50-70, 55-70}

i.e. 12 variants, every one of them a legal tightening (``StrategyConfig`` refuses any
loosening), plus exactly ONE explicit test-only variant with the R4 regime filter OFF. The
regime-OFF variant is backtested and reported side by side, but it is NEVER eligible for
selection or adoption (``StrategyConfig.is_test_only``).

Stop-fill stress runs (CONTRACT v4 D4, ``cfg.stop_fill_wick_k > 0``): the stress is an
execution assumption applied to EVERY variant of the grid, not a variant, so selection runs
among the stressed grid exactly as it does unstressed (only the regime-OFF variant is never
selectable). Every config of a stress run is ``is_test_only``, so nothing it selects can be
adopted; the run exists to measure how the labels move under a harsher fill model.

Order of operations in :func:`discover` (the order is the whole point):

1. TRAIN phase for every variant (``walkforward.run_train``): no candle that closes after
   the split is read.
2. SELECTION (:func:`select_on_train`), from TRAIN summaries only: the eligible variant
   (not the regime-OFF test variant, at least ``min_train`` TRAIN trades) with the highest
   TRAIN t-stat of its R-multiples; ties go to the earlier grid entry. The
   :class:`Selection` is frozen and recorded here, BEFORE any TEST backtest has been run.
3. TEST phase for every variant (``walkforward.run_test``), for side-by-side reporting only.
   Nothing computed in this phase can reach the selection. Re-selecting on these TEST
   numbers would turn TEST into TRAIN, so the report must not (and does not) do that.

``K`` (``k_tried``) records how many variants were tried, so a reader can discount the
selected variant's TRAIN numbers for the selection (the best of K looks better than it is).
Only the selected variant's TEST result is an honest out-of-sample test.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from .config import StrategyConfig
from .models import Candle, NewsEvent
from .walkforward import (
    DEFAULT_MAX_DD_PCT,
    DEFAULT_STATS,
    MIN_TEST,
    MIN_TRAIN,
    TRAIN_FRAC,
    Stats,
    TrainPhase,
    WalkForwardResult,
    run_test,
    run_train,
    split_ts,
)


GRID_REWARD_RISK = (2.0, 2.5, 3.0)
GRID_VOL_MULT = (1.5, 2.0)
GRID_RSI = ((50.0, 70.0), (55.0, 70.0))

TEST_BECOMES_TRAIN = (
    "Re-selecting a variant on these TEST numbers would turn TEST into TRAIN: the selection "
    "was fixed on TRAIN before any TEST backtest ran, and it is not revisited."
)


def default_grid(base: StrategyConfig) -> list[StrategyConfig]:
    """The 12 legal tightenings, reward_risk outermost, RSI window innermost."""
    out: list[StrategyConfig] = []
    for rr in GRID_REWARD_RISK:
        for vm in GRID_VOL_MULT:
            for lo, hi in GRID_RSI:
                out.append(base.with_changes(reward_risk=rr, vol_mult=vm, rsi_min=lo, rsi_max=hi))
    return out


def regime_off_variant(base: StrategyConfig) -> StrategyConfig:
    """The ONE explicit test-only variant: ``base`` with the R4 regime filter switched off."""
    return base.with_changes(regime_filter=False)


@dataclass(frozen=True)
class Selection:
    """The TRAIN-only choice, frozen before any TEST number exists."""

    variant: str | None  # None when no variant is eligible
    reason: str  # one sentence
    train_t_stat: float | None
    train_n: int | None
    eligible: tuple[str, ...]  # variants that could be selected (grid order)
    ineligible: tuple[tuple[str, str], ...]  # (variant, why not)
    k_tried: int  # every variant backtested, including the test-only one
    k_eligible_by_design: int  # variants allowed to compete (all but regime OFF)
    min_train: int


@dataclass
class DiscoveryResult:
    split_ts: int
    selection: Selection
    results: list[WalkForwardResult]  # grid order, the test-only variant last
    test_only: tuple[str, ...]  # the explicit test variant ids (regime OFF, never selectable)
    stage_log: list[str] = field(default_factory=list)  # phases in the order they ran
    note: str = TEST_BECOMES_TRAIN

    @property
    def k_tried(self) -> int:
        return self.selection.k_tried

    def result(self, variant: str) -> WalkForwardResult:
        for r in self.results:
            if r.variant == variant:
                return r
        raise KeyError(variant)

    def judged(self, variant: str) -> bool:
        """True only for the selected variant: the one pre-registered TEST look of discovery.

        Every other grid variant and the regime-OFF test variant are reported as
        ``context: <label>, not judged``.
        """
        return variant == self.selection.variant

    def selected(self) -> WalkForwardResult | None:
        """The walk-forward result of the selected variant, or None if nothing was eligible."""
        if self.selection.variant is None:
            return None
        return self.result(self.selection.variant)


def never_selectable(cfg: StrategyConfig) -> bool:
    """The explicit test variant: the R4 regime filter switched OFF (never selectable)."""
    return not cfg.regime_filter


def _ineligibility(phase: TrainPhase, min_train: int) -> str | None:
    if never_selectable(phase.cfg):
        return "explicit test-only variant (R4 regime filter OFF): never selectable"
    if phase.train_summary.n < min_train:
        return f"only {phase.train_summary.n} TRAIN trades (need {min_train})"
    return None


def select_on_train(phases: Sequence[TrainPhase], min_train: int = MIN_TRAIN) -> Selection:
    """Pick the eligible variant with the highest TRAIN t-stat (ties: earliest in order).

    Uses ONLY ``phase.train_summary`` and ``phase.cfg.regime_filter``: there is no TEST
    information in a :class:`TrainPhase`.
    """
    eligible: list[TrainPhase] = []
    ineligible: list[tuple[str, str]] = []
    for phase in phases:
        why = _ineligibility(phase, min_train)
        if why is None:
            eligible.append(phase)
        else:
            ineligible.append((phase.variant, why))
    k_design = sum(1 for p in phases if not never_selectable(p.cfg))
    common = {
        "eligible": tuple(p.variant for p in eligible),
        "ineligible": tuple(ineligible),
        "k_tried": len(phases),
        "k_eligible_by_design": k_design,
        "min_train": min_train,
    }
    if not eligible:
        reason = (
            f"No variant was selected: none of the {k_design} selectable variants had at "
            f"least {min_train} TRAIN trades."
        )
        return Selection(None, reason, None, None, **common)  # type: ignore[arg-type]
    best = eligible[0]
    for phase in eligible[1:]:
        if phase.train_summary.t_stat > best.train_summary.t_stat:
            best = phase
    s = best.train_summary
    reason = (
        f"{best.variant} has the highest TRAIN t-stat ({s.t_stat:+.2f}, avg {s.avg_r:+.3f}R "
        f"over {s.n} trades) among {len(eligible)} eligible variants; chosen on TRAIN only, "
        "before any TEST backtest was run."
    )
    return Selection(best.variant, reason, s.t_stat, s.n, **common)  # type: ignore[arg-type]


def discover(
    data: Mapping[str, Sequence[Candle]],
    base: StrategyConfig,
    events: Iterable[NewsEvent] = (),
    train_frac: float = TRAIN_FRAC,
    min_train: int = MIN_TRAIN,
    *,
    grid: Sequence[StrategyConfig] | None = None,
    max_dd_pct: float = DEFAULT_MAX_DD_PCT,
    min_test: int = MIN_TEST,
    split: int | None = None,
    stats: Stats = DEFAULT_STATS,
) -> DiscoveryResult:
    """Run the grid (+ the regime-OFF test variant) through TRAIN, select, then TEST.

    ``grid`` defaults to :func:`default_grid`; regime-OFF configs in a custom grid are
    reported but never selectable. A stop-fill stress base (``stop_fill_wick_k > 0``) is
    allowed: the stress applies to the whole grid (module docstring). See the module
    docstring for the order of operations. Only the SELECTED variant is a pre-registered
    candidate (one of the ``stats.m`` TEST looks of the multiplicity rule); every other
    variant's label is context, not judged.
    """
    if never_selectable(base):
        raise ValueError("discover() needs a base config with the R4 regime filter ON")
    evs = list(events)
    s = split if split is not None else split_ts(data, train_frac, base.timeframe_ms)
    variants = list(grid) if grid is not None else default_grid(base)
    variants.append(regime_off_variant(base))
    ids = [v.variant_id() for v in variants]
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate variants in the discovery grid: {ids}")
    log: list[str] = []

    # ---- 1. TRAIN only: nothing at or after the split is read.
    phases = [run_train(data, v, evs, s, variant=v.variant_id(), stats=stats) for v in variants]
    log.append(f"TRAIN backtests: {len(phases)} variants, candles closed by the split only")

    # ---- 2. Selection, recorded before any TEST backtest exists.
    selection = select_on_train(phases, min_train)
    log.append(f"selection recorded: {selection.variant}")

    # ---- 3. TEST for side-by-side reporting only; `selection` is frozen and not revisited.
    results = [run_test(data, p, evs, max_dd_pct, min_train, min_test) for p in phases]
    log.append(f"TEST backtests: {len(results)} variants, reported side by side only")

    return DiscoveryResult(
        split_ts=s,
        selection=selection,
        results=results,
        test_only=tuple(v.variant_id() for v in variants if never_selectable(v)),
        stage_log=log,
    )
