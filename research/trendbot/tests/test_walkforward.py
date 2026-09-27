"""walkforward: split on the common range, TRAIN-only fitting (purged), untouched TEST."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from research.trendbot import walkforward as wf
from research.trendbot.backtester import enumerate_candidates, run_backtest
from research.trendbot.config import StrategyConfig
from research.trendbot.metrics import dd_check, label
from research.trendbot.ml_filter import InsufficientData, MLFilter, make_factory
from research.trendbot.models import HOUR_MS, CandidateOutcome, Candle
from research.trendbot.synthetic import generate_world, make_world


TF = 4 * HOUR_MS
CFG = StrategyConfig()


def _flat(start: int, n: int) -> list[Candle]:
    return [Candle(start + i * TF, 10.0, 10.5, 9.5, 10.0, 1.0) for i in range(n)]


def swap_test_period(
    data: dict[str, list[Candle]], other: dict[str, list[Candle]], split: int
) -> dict[str, list[Candle]]:
    """Replace every candle at/after ``split`` by ``other``'s, rescaled to join the TRAIN price."""
    out = {}
    for pair, candles in data.items():
        pre = [c for c in candles if c.ts < split]
        post = [c for c in other[pair] if c.ts >= split]
        k = pre[-1].close / post[0].open
        out[pair] = pre + [
            Candle(c.ts, c.open * k, c.high * k, c.low * k, c.close * k, c.volume * 1.7)
            for c in post
        ]
    return out


@pytest.fixture(scope="module")
def world() -> tuple[dict[str, list[Candle]], list, int]:
    data, events = make_world("hour_edge", 3, years=2.5)
    return data, events, wf.split_ts(data)


@pytest.fixture(scope="module")
def perturbed(world) -> dict[str, list[Candle]]:
    data, _events, split = world
    other, _ = make_world("planted", 11, years=2.5)
    return swap_test_period(data, other, split)


def _small_ml_factory(cands: Sequence[CandidateOutcome]):
    return MLFilter.fit(cands, min_samples=30, min_per_class=5).entry_filter()


# ---------------------------------------------------------------------------- split
def test_split_uses_common_range_and_candle_boundary() -> None:
    t0 = 1_700_000_000_000 - 1_700_000_000_000 % TF
    data = {"BTC/USDT": _flat(t0, 115), "ETH/USDT": _flat(t0 + 10 * TF, 100)}
    # common range: [t0 + 10 TF, t0 + 110 TF) = 100 candles -> 70 TRAIN candles
    s = wf.split_ts(data)
    assert s == t0 + 10 * TF + 70 * TF
    assert (s - t0) % TF == 0
    assert wf.common_range(data, TF) == (t0 + 10 * TF, t0 + 110 * TF)
    assert wf.split_ts(data, 0.5, TF) == t0 + 10 * TF + 50 * TF
    # same float expression as synthetic.split_idx = int(split_frac * n): int(0.7 * 90) == 62
    short = {"BTC/USDT": _flat(t0, 90)}
    assert wf.split_ts(short) == t0 + int(0.7 * 90) * TF


def test_split_matches_the_synthetic_ground_truth_split() -> None:
    w = generate_world("decay", 1, years=1.0)
    assert wf.split_ts(w.candles, 0.7) == w.split_ts


@pytest.mark.parametrize("frac", [0.0, 1.0, -0.1, 1.5])
def test_split_rejects_bad_fraction(frac: float) -> None:
    with pytest.raises(ValueError, match="train_frac"):
        wf.split_ts({"BTC/USDT": _flat(0, 50)}, frac)


def test_split_rejects_degenerate_data() -> None:
    with pytest.raises(ValueError):
        wf.split_ts({})
    with pytest.raises(ValueError):
        wf.split_ts({"BTC/USDT": _flat(0, 1)})
    with pytest.raises(ValueError, match="common time range"):
        wf.split_ts({"BTC/USDT": _flat(0, 10), "ETH/USDT": _flat(20 * TF, 10)}, 0.7, TF)
    with pytest.raises(ValueError, match="too short"):
        wf.split_ts({"BTC/USDT": _flat(0, 2)}, 0.3, TF)


# ---------------------------------------------------------------------------- windows
def test_train_and_test_windows(world) -> None:
    data, events, split = world
    res = wf.walk_forward(data, CFG, events, min_train=5, min_test=5)
    assert res.split_ts == split
    assert res.train.window == (None, split) and res.test.window == (split, None)
    assert res.train.trades and res.test.trades
    assert all(t.signal_ts < split and t.exit_ts < split for t in res.train.trades)
    assert all(t.signal_ts >= split for t in res.test.trades)
    assert not res.train_in_sample and res.train_tag == "TRAIN" and res.ran
    # label and drawdown check are exactly metrics.label / dd_check of the two summaries
    assert (res.label, res.label_reason) == label(res.train_summary, res.test_summary, 5, 5)
    assert (res.dd_ok, res.dd_reason) == dd_check(res.test_summary, res.max_dd_pct)


def test_test_backtest_starts_fresh(world) -> None:
    """TEST is an independent run from the split: fresh equity and circuit breakers."""
    data, events, split = world
    res = wf.walk_forward(data, CFG, events)
    direct = run_backtest(data, CFG, events, start_ts=split, end_ts=None)
    assert [(t.signal_ts, t.pnl) for t in res.test.trades] == [
        (t.signal_ts, t.pnl) for t in direct.trades
    ]
    assert res.test.final_equity == direct.final_equity


def test_train_is_invariant_to_test_period_candles(world, perturbed) -> None:
    data, events, split = world
    a = wf.walk_forward(data, CFG, events)
    b = wf.walk_forward(perturbed, CFG, events)
    assert b.split_ts == split
    assert a.train_summary == b.train_summary
    assert [(t.signal_ts, t.pnl) for t in a.train.trades] == [
        (t.signal_ts, t.pnl) for t in b.train.trades
    ]
    assert a.test_summary != b.test_summary  # the perturbation did change TEST


# ---------------------------------------------------------------------------- fitted layers
def test_layer_is_fit_on_purged_train_candidates_only(world) -> None:
    data, events, natural = world
    # put the split INSIDE the life of a real candidate so that purging has work to do
    everything = enumerate_candidates(data, CFG, events)
    long_lived = [c for c in everything if c.exit_ts - c.entry_ts >= 3 * TF]
    victim = min(long_lived, key=lambda c: abs(c.signal_ts - natural))
    split = victim.entry_ts + TF
    seen: list[list[CandidateOutcome]] = []
    calls: list[int] = []

    def factory(cands):
        seen.append(list(cands))

        def keep_all(pair, row, check):
            calls.append(row.ts)
            return True, None, "keep"

        return keep_all

    res = wf.walk_forward(data, CFG, events, entry_filter_factory=factory, split=split)
    assert len(seen) == 1, "the factory is fitted exactly once"
    fitted = seen[0]
    assert fitted and res.n_train_candidates == len(fitted)
    assert all(c.signal_ts < split and c.exit_ts < split for c in fitted)
    # purge: candidates signalled in TRAIN but resolved at/after the split are NOT in the fit
    straddling = [c for c in everything if c.signal_ts < split <= c.exit_ts]
    assert victim in straddling
    fitted_keys = {(c.pair, c.signal_ts) for c in fitted}
    assert not fitted_keys & {(c.pair, c.signal_ts) for c in straddling}
    # the SAME fitted filter is applied to TRAIN and TEST
    assert any(ts < split for ts in calls) and any(ts >= split for ts in calls)
    assert res.entry_filter is not None and res.layer == wf.ML_LAYER
    assert res.train_in_sample and res.train_tag == "TRAIN (in-sample)"


def test_ml_fit_is_invariant_to_test_period_candles(world, perturbed) -> None:
    data, events, _split = world
    a = wf.walk_forward(data, CFG, events, entry_filter_factory=_small_ml_factory)
    b = wf.walk_forward(perturbed, CFG, events, entry_filter_factory=_small_ml_factory)
    ma, mb = a.entry_filter.ml, b.entry_filter.ml
    assert ma.model.coefficients() == mb.model.coefficients()
    assert (ma.threshold, ma.means, ma.stds) == (mb.threshold, mb.means, mb.stds)
    assert a.n_train_candidates == b.n_train_candidates
    assert a.train_summary == b.train_summary
    assert a.test_summary != b.test_summary


def test_insufficient_data_is_untested_without_fallback(world) -> None:
    data, events, split = world

    def refuse(cands):
        raise InsufficientData(f"only {len(cands)} candidates, need a million")

    res = wf.walk_forward(data, CFG, events, entry_filter_factory=refuse, variant="x+ml")
    assert res.label == "UNTESTED"
    assert "need a million" in res.label_reason and "could not be fitted" in res.label_reason
    assert res.fit_error and "need a million" in res.fit_error
    assert not res.ran and res.entry_filter is None and res.dd_ok is False
    # no silent fall-back to the unfiltered rules: nothing was simulated at all
    assert res.train.trades == [] and res.test.trades == []
    assert res.train_summary.n == 0 and res.test_summary.n == 0
    assert res.train.window == (None, split) and res.test.window == (split, None)
    assert res.variant == "x+ml" and res.train_in_sample


def test_real_ml_factory_on_a_short_history_is_untested() -> None:
    data, events = make_world("planted", 2, years=1.0)
    res = wf.walk_forward(data, CFG, events, entry_filter_factory=make_factory())
    assert res.label == "UNTESTED" and not res.ran
    assert "ML filter needs at least" in res.label_reason


def test_other_exceptions_are_not_swallowed(world) -> None:
    data, events, _ = world

    def broken(cands):
        raise RuntimeError("bug")

    with pytest.raises(RuntimeError, match="bug"):
        wf.walk_forward(data, CFG, events, entry_filter_factory=broken)


def test_explicit_split_override(world) -> None:
    data, events, split = world
    later = split + 30 * TF
    res = wf.walk_forward(data, CFG, events, split=later)
    assert res.split_ts == later and res.train.window == (None, later)
    phase = wf.run_train(data, CFG.with_changes(reward_risk=2.5), events, later, variant="rr")
    assert phase.variant == "rr" and phase.train.window == (None, later)
    assert not phase.train_in_sample
