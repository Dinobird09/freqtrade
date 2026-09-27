"""walkforward: split on the common range, TRAIN cut on candle close, TRAIN-only fitting
(purged), the D8 out-of-sample TRAIN gate of fitted layers, untouched TEST."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from research.trendbot import walkforward as wf
from research.trendbot.adoption import recompute_walk_forward
from research.trendbot.backtester import enumerate_candidates, run_backtest
from research.trendbot.config import StrategyConfig
from research.trendbot.metrics import (
    DD_CAP_PCT,
    dd_check,
    label,
    mtm_max_dd_pct,
    summarize,
    train_dd_quantile,
)
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
    # C5: TEST mark-to-market drawdown vs min(20%, TRAIN bootstrap p95 at the TEST length)
    cap = CFG.starting_capital
    assert res.test_mtm_dd_pct == mtm_max_dd_pct(res.test.trades, data, cap, CFG.fee_rate)
    assert res.train_mtm_dd_pct == mtm_max_dd_pct(res.train.trades, data, cap, CFG.fee_rate)
    assert res.train_dd_p95_pct == train_dd_quantile(res.train.trades, res.test_summary.n)
    assert res.max_dd_pct == DD_CAP_PCT == 15.0  # D7
    assert res.dd_limit_pct == min(15.0, res.train_dd_p95_pct)
    assert (res.dd_ok, res.dd_reason) == dd_check(
        res.test_summary, res.max_dd_pct, res.train_dd_p95_pct, res.test_mtm_dd_pct
    )
    assert res.dd_ok == (res.test_mtm_dd_pct <= res.dd_limit_pct)
    assert res.test_mtm_dd_pct > 0 and res.train_mtm_dd_pct > 0


def test_label_params_reproduce_the_verdict_from_the_journals(world) -> None:
    """adoption.recompute_walk_forward(journals, label_params) == the walk-forward's own."""
    data, events, _split = world
    res = wf.walk_forward(data, CFG, events, min_train=5, min_test=5)
    params = res.label_params()
    # the closed D1 schema, exactly these keys in this order
    assert (
        tuple(params)
        == wf.LABEL_PARAM_KEYS
        == (
            "seed",
            "n_boot",
            "m",
            "alpha",
            "max_dd_pct",
            "train_dd_p95_pct",
            "mtm_max_dd_pct",
        )
    )
    assert (params["m"], params["alpha"], params["n_boot"]) == (4, 0.05, 4000)
    assert params["seed"] == wf.SUMMARY_SEED and params["max_dd_pct"] == 15.0
    assert params["train_dd_p95_pct"] == res.train_dd_p95_pct
    assert params["mtm_max_dd_pct"] == res.test_mtm_dd_pct
    v = recompute_walk_forward(res.train.trades, res.test.trades, CFG, 5, 5, params)
    assert (v.label, v.label_reason) == (res.label, res.label_reason)
    assert (v.dd_ok, v.dd_reason) == (res.dd_ok, res.dd_reason)
    assert v.train == res.train_summary and v.test == res.test_summary


def test_stats_fix_m_alpha_and_resamples(world) -> None:
    data, events, _ = world
    one = wf.walk_forward(data, CFG, events, stats=wf.Stats(m=1, n_boot=500))
    assert one.test_summary.lb_confidence == pytest.approx(0.95)
    assert one.test_summary.n_boot == 500 and one.label_params()["m"] == 1
    four = wf.walk_forward(data, CFG, events)
    assert four.test_summary.iid_lb < one.test_summary.iid_lb  # wider under m = 4


def test_reached_test_gate(world) -> None:
    data, events, _ = world
    res = wf.walk_forward(data, CFG, events)
    a, b = res.train_summary, res.test_summary
    assert res.reached_test_gate == (a.avg_r > 0 and a.n >= 30 and b.n >= 30)


def test_layer_diff_counts_both_directions() -> None:
    def t(pair: str, ts: int, notes: str = "") -> object:
        return type("T", (), {"pair": pair, "signal_ts": ts, "notes": notes})()

    base = [t("BTC/USDT", 1), t("ETH/USDT", 2), t("BNB/USDT", 3)]
    layer = [t("BTC/USDT", 1, "reserved 0.5% (guard x0.5)"), t("ETH/USDT", 9, "guard x1)")]
    d = wf.layer_diff(base, layer, "TEST", vetoed=4)  # type: ignore[arg-type]
    assert (d.window, d.vetoed, d.base_only, d.layer_only, d.shared) == ("TEST", 4, 2, 1, 1)
    assert (d.reduced_risk, d.base_n, d.layer_n) == (1, 3, 2)
    assert wf.guard_multiplier_of(layer[0]) == 0.5 and wf.guard_multiplier_of(base[0]) is None


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
    assert a.train_mtm_dd_pct == b.train_mtm_dd_pct  # MTM of TRAIN reads no TEST candle
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
    # fitted twice: first on ALL purged TRAIN candidates (the model used on TEST), then the
    # D8 inner fit on the first 70% of them, purged at the inner boundary
    assert len(seen) == 2
    fitted, inner = seen
    assert fitted and res.n_train_candidates == len(fitted)
    assert all(c.signal_ts < split and c.exit_ts < split for c in fitted)
    gate = res.gate
    assert gate is not None and gate.ran and gate.n_candidates == len(fitted)
    b = gate.inner_split_ts
    assert b is not None and len(inner) == gate.n_fit
    assert all(c.signal_ts < b and c.exit_ts + TF <= b for c in inner)
    assert {(c.pair, c.signal_ts) for c in inner} < {(c.pair, c.signal_ts) for c in fitted}
    # purge: candidates signalled in TRAIN but resolved at/after the split are NOT in the fit
    straddling = [c for c in everything if c.signal_ts < split <= c.exit_ts]
    assert victim in straddling
    fitted_keys = {(c.pair, c.signal_ts) for c in fitted}
    assert not fitted_keys & {(c.pair, c.signal_ts) for c in straddling}
    # the SAME fitted filter is applied to TRAIN and TEST
    assert any(ts < split for ts in calls) and any(ts >= split for ts in calls)
    assert res.entry_filter is not None and res.layer == wf.ML_LAYER
    assert res.train_in_sample and res.train_tag == "TRAIN (in-sample)"
    assert res.gate_tag == "TRAIN (out-of-sample inner split)"


def test_ml_fit_is_invariant_to_test_period_candles(world, perturbed) -> None:
    data, events, _split = world
    a = wf.walk_forward(data, CFG, events, entry_filter_factory=_small_ml_factory)
    b = wf.walk_forward(perturbed, CFG, events, entry_filter_factory=_small_ml_factory)
    ma, mb = a.entry_filter.ml, b.entry_filter.ml
    assert ma.model.coefficients() == mb.model.coefficients()
    assert (ma.threshold, ma.means, ma.stds) == (mb.threshold, mb.means, mb.stds)
    # the adoption identity of the model (CONTRACT v2 A3) depends on TRAIN only
    assert ma.fingerprint() == mb.fingerprint()
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


# ---------------------------------------------------------------------------- close-time cut
def _shift(candles: Sequence[Candle], ms: int) -> list[Candle]:
    return [Candle(c.ts + ms, c.open, c.high, c.low, c.close, c.volume) for c in candles]


@pytest.fixture(scope="module")
def mixed_grid() -> tuple[dict[str, list[Candle]], list, int]:
    """planted seed 1 with ETH moved +2h IN MEMORY (data.load_dataset refuses such a file).

    The split then sits on ETH's grid, so BTC's and BNB's candle at ``split - 2h`` opens
    before the split but closes 2h after it; in this world a BNB trade is still open there.
    """
    data, events = make_world("planted", 1, years=2.5)
    mixed = {p: _shift(cs, 2 * HOUR_MS) if p == "ETH/USDT" else list(cs) for p, cs in data.items()}
    return mixed, events, wf.split_ts(mixed, 0.7, TF)


def _post_split_changed(data: dict[str, list[Candle]], split: int) -> dict[str, list[Candle]]:
    """Every candle that CLOSES after the split crashes to half its open (close and low)."""
    return {
        p: [
            c
            if c.ts + TF <= split
            else Candle(c.ts, c.open, c.high, c.open * 0.5, c.open * 0.5, 9.0)
            for c in cs
        ]
        for p, cs in data.items()
    }


def test_train_view_keeps_only_candles_closed_by_the_split(mixed_grid) -> None:
    data, _events, split = mixed_grid
    assert split % TF == 2 * HOUR_MS  # on ETH's (shifted) grid
    view = wf.train_view(data, split, TF)
    for pair, candles in data.items():
        kept = view[pair]
        assert kept == [c for c in candles if c.ts + TF <= split]
        assert kept[-1].ts + TF <= split < kept[-1].ts + 2 * TF
    # BTC / BNB each have exactly one candle that opens before the split and closes after it
    straddling = {p: [c for c in cs if c.ts < split < c.ts + TF] for p, cs in data.items()}
    assert {p: len(v) for p, v in straddling.items()} == {
        "BNB/USDT": 1,
        "BTC/USDT": 1,
        "ETH/USDT": 0,
    }
    assert all(c not in view[p] for p, cs in straddling.items() for c in cs)
    with pytest.raises(ValueError):
        wf.train_view(data, split, 0)


def test_train_results_are_invariant_to_post_split_closes_on_a_mixed_grid(mixed_grid) -> None:
    """TRAIN (trades, summary, MTM drawdown, the D8 gate) never reads a price printed after
    the split, even the close of a candle that OPENED before it (cut on candle close)."""
    data, events, split = mixed_grid
    changed = _post_split_changed(data, split)
    a = wf.walk_forward(data, CFG, events, split=split, entry_filter_factory=_small_ml_factory)
    b = wf.walk_forward(changed, CFG, events, split=split, entry_filter_factory=_small_ml_factory)
    key = [(t.pair, t.signal_ts, t.exit_ts, t.exit_reason, t.pnl) for t in a.train.trades]
    assert key == [(t.pair, t.signal_ts, t.exit_ts, t.exit_reason, t.pnl) for t in b.train.trades]
    assert a.train_summary == b.train_summary and a.train_mtm_dd_pct == b.train_mtm_dd_pct
    assert a.gate is not None and b.gate is not None and a.gate.ran
    assert a.gate.summary == b.gate.summary and a.gate.mtm_dd_pct == b.gate.mtm_dd_pct
    assert a.entry_filter.ml.fingerprint() == b.entry_filter.ml.fingerprint()
    base_a = wf.walk_forward(data, CFG, events, split=split)
    base_b = wf.walk_forward(changed, CFG, events, split=split)
    assert base_a.train_summary == base_b.train_summary
    # every TRAIN trade (incl. a forced close) ends on a candle that closed by the split
    ends = [t for t in base_a.train.trades if t.exit_reason == "END"]
    assert ends and all(t.exit_ts + TF <= split for t in base_a.train.trades)
    assert base_a.test_summary != base_b.test_summary  # the change did reach TEST
    # the old cut (open time < split) would have read the straddling BNB close
    old_a = run_backtest(data, CFG, events, end_ts=split)
    old_b = run_backtest(changed, CFG, events, end_ts=split)
    assert [t.pnl for t in old_a.trades] != [t.pnl for t in old_b.trades]


# ---------------------------------------------------------------------------- D8 gate
@pytest.fixture(scope="module")
def ml_result(world) -> wf.WalkForwardResult:
    data, events, _split = world
    return wf.walk_forward(data, CFG, events, entry_filter_factory=_small_ml_factory)


def test_inner_split_is_chronological_by_count_and_purged() -> None:
    def cand(pair: str, signal: int, exit_: int) -> CandidateOutcome:
        return CandidateOutcome(pair, signal * TF, (signal + 1) * TF, exit_ * TF, "SL", -1.0, {})

    cands = [cand("BTC/USDT", i, i + 2) for i in range(10)]
    boundary, fit, purged = wf.inner_split(list(reversed(cands)), TF, 0.7)
    assert boundary == 7 * TF  # candidate #7 of 10 opens the held-out 30%
    # signalled before the boundary: #0..#6; exit candle closes by it: exit + 1 <= 7 -> #0..#4
    assert [c.signal_ts // TF for c in fit] == [0, 1, 2, 3, 4] and purged == 2
    assert wf.inner_split(cands[:1], TF) is None
    with pytest.raises(ValueError):
        wf.inner_split(cands, TF, 1.0)


def test_ml_label_judges_the_out_of_sample_gate(world, ml_result) -> None:
    """D8: label, TEST-gate reach and the C5 TRAIN bootstrap use the inner out-of-sample
    TRAIN window; the in-sample full-TRAIN numbers are context only."""
    data, events, split = world
    res = ml_result
    g = res.gate
    assert g is not None and g.ran and g.inner_split_ts is not None
    assert g.backtest.window == (g.inner_split_ts, split)
    assert all(
        g.inner_split_ts <= t.signal_ts and t.exit_ts + TF <= split for t in g.backtest.trades
    )
    assert res.label_train is g.backtest and res.label_train_summary is g.summary
    assert g.summary == summarize(g.backtest.trades, CFG.starting_capital)
    assert g.summary != res.train_summary  # the in-sample figure is a different window
    verdict, why = label(g.summary, res.test_summary, 30, 30)
    assert res.label == verdict and why in res.label_reason
    assert "out-of-sample inner split" in res.label_reason
    assert res.train_dd_p95_pct == train_dd_quantile(g.backtest.trades, res.test_summary.n)
    a = g.summary
    assert res.reached_test_gate == (a.avg_r > 0 and a.n >= 30 and res.test_summary.n >= 30)
    # the gate's filter is fitted on the first 70% only; TEST uses the model of ALL TRAIN
    assert g.entry_filter.ml.fingerprint() != res.entry_filter.ml.fingerprint()
    view = wf.train_view(data, split, TF)
    full = MLFilter.fit(
        enumerate_candidates(view, CFG, events, end_ts=split), min_samples=30, min_per_class=5
    )
    assert full.fingerprint() == res.entry_filter.ml.fingerprint()


def test_ml_label_is_recomputable_from_the_gate_journal(ml_result) -> None:
    """The record binds the gate trades as the TRAIN journal, so adoption's recomputation
    over (gate journal, TEST journal) reproduces the label and dd_ok."""
    res = ml_result
    v = recompute_walk_forward(
        res.label_train.trades, res.test.trades, CFG, 30, 30, res.label_params()
    )
    assert (v.label, v.dd_ok) == (res.label, res.dd_ok)
    assert v.train == res.label_train_summary and v.test == res.test_summary


def test_gate_inner_fit_failure_is_untested_not_a_fallback(world) -> None:
    """If the inner 70% cannot be fitted, the gate has no trades: UNTESTED, never the
    in-sample numbers."""
    data, events, _split = world
    calls: list[int] = []

    def fits_only_the_full_sample(cands):
        calls.append(len(cands))
        if len(calls) > 1:
            raise InsufficientData(f"inner sample of {len(cands)} is too small")
        return MLFilter.fit(cands, min_samples=30, min_per_class=5).entry_filter()

    res = wf.walk_forward(data, CFG, events, entry_filter_factory=fits_only_the_full_sample)
    assert len(calls) == 2 and calls[1] < calls[0]
    g = res.gate
    assert g is not None and not g.ran and "inner sample" in (g.fit_error or "")
    assert g.backtest.trades == [] and g.summary.n == 0
    assert res.ran and res.train.trades  # the in-sample context backtest still exists
    assert res.label == "UNTESTED" and "could not be evaluated" in res.label_reason
    assert not res.reached_test_gate and res.label_train_summary.n == 0
    # recomputable from an EMPTY TRAIN journal: metrics.label says UNTESTED; the C5 TRAIN
    # bootstrap has nothing to resample, so train_dd_p95_pct is recorded as None
    empty = summarize([], CFG.starting_capital)
    assert label(empty, res.test_summary, 30, 30)[0] == "UNTESTED" == res.label
    assert res.train_dd_p95_pct is None and res.label_params()["train_dd_p95_pct"] is None


def test_unfitted_variants_have_no_gate(world) -> None:
    data, events, _ = world
    res = wf.walk_forward(data, CFG, events)
    assert res.gate is None and res.label_train is res.train
    assert res.label_train_summary is res.train_summary
    assert res.label_train_mtm_dd_pct == res.train_mtm_dd_pct
