"""strategy_discovery: 12 legal tightenings + 1 test-only variant, TRAIN-only selection."""

from __future__ import annotations

from dataclasses import replace

import pytest

from research.trendbot import strategy_discovery as sd
from research.trendbot import walkforward as wf
from research.trendbot.config import MANDATE_MIN_RR, MANDATE_MIN_VOL_MULT, StrategyConfig
from research.trendbot.metrics import summarize
from research.trendbot.models import Candle
from research.trendbot.synthetic import make_world


BASE = StrategyConfig()
MIN_TRAIN = 10


def swap_test_period(data, other, split):
    """Replace every candle at/after ``split`` by ``other``'s, rescaled to join the TRAIN price."""
    out = {}
    for pair, candles in data.items():
        pre = [c for c in candles if c.ts < split]
        post = [c for c in other[pair] if c.ts >= split]
        k = pre[-1].close / post[0].open
        out[pair] = pre + [
            Candle(c.ts, c.open * k, c.high * k, c.low * k, c.close * k, c.volume * 0.6)
            for c in post
        ]
    return out


@pytest.fixture(scope="module")
def world():
    data, events = make_world("planted", 5, years=2.5)
    return data, events, wf.split_ts(data)


@pytest.fixture(scope="module")
def result(world) -> sd.DiscoveryResult:
    data, events, _ = world
    return sd.discover(data, BASE, events, min_train=MIN_TRAIN, min_test=MIN_TRAIN)


# ---------------------------------------------------------------------------- grid
def test_default_grid_is_twelve_legal_tightenings() -> None:
    grid = sd.default_grid(BASE)
    assert len(grid) == 12
    combos = {(c.reward_risk, c.vol_mult, (c.rsi_min, c.rsi_max)) for c in grid}
    assert combos == {
        (rr, vm, rsi) for rr in (2.0, 2.5, 3.0) for vm in (1.5, 2.0) for rsi in sd.GRID_RSI
    }
    assert len({c.variant_id() for c in grid}) == 12
    for c in grid:
        assert not c.is_test_only
        assert c.reward_risk >= MANDATE_MIN_RR and c.vol_mult >= MANDATE_MIN_VOL_MULT
        assert 50.0 <= c.rsi_min < c.rsi_max <= 70.0
        # everything outside the three searched knobs is the base config
        assert replace(c, reward_risk=2.0, vol_mult=1.5, rsi_min=50.0) == BASE
    assert grid[0] == BASE  # the mandate itself is one of the variants


def test_regime_off_is_the_one_test_only_variant() -> None:
    off = sd.regime_off_variant(BASE)
    assert off.is_test_only and not off.regime_filter
    assert off.variant_id().endswith("regimeOFF")


def test_discover_refuses_a_test_only_base(world) -> None:
    data, events, _ = world
    with pytest.raises(ValueError, match="production base"):
        sd.discover(data, sd.regime_off_variant(BASE), events)


# ---------------------------------------------------------------------------- selection
def _phase(variant: str, t: float, n: int, cfg: StrategyConfig = BASE) -> wf.TrainPhase:
    placeholder = wf._placeholder(cfg, False, (None, 0), variant)
    s = replace(summarize([], cfg.starting_capital), n=n, t_stat=t, avg_r=t / 10)
    return wf.TrainPhase(variant, cfg, 0, placeholder, s)


def test_select_on_train_rules() -> None:
    off = sd.regime_off_variant(BASE)
    phases = [
        _phase("a", 1.0, 50),
        _phase("b", 2.5, 50),
        _phase("c", 9.0, 5),  # best t but too few TRAIN trades
        _phase("d", 2.5, 60),  # ties with b: the earlier variant wins
        _phase("off", 99.0, 500, off),  # test-only: never selectable
    ]
    sel = sd.select_on_train(phases, min_train=30)
    assert sel.variant == "b" and sel.train_t_stat == 2.5 and sel.train_n == 50
    assert sel.eligible == ("a", "b", "d")
    assert dict(sel.ineligible).keys() == {"c", "off"}
    assert "test-only" in dict(sel.ineligible)["off"]
    assert sel.k_tried == 5 and sel.k_eligible_by_design == 4
    assert "before any TEST backtest" in sel.reason


def test_select_on_train_with_nothing_eligible() -> None:
    sel = sd.select_on_train([_phase("a", 3.0, 3)], min_train=30)
    assert sel.variant is None and sel.train_t_stat is None
    assert "No variant was selected" in sel.reason


# ---------------------------------------------------------------------------- discover
def test_discover_reports_every_variant_and_k(result: sd.DiscoveryResult) -> None:
    assert result.k_tried == 13 == len(result.results)
    assert result.selection.k_eligible_by_design == 12
    assert len(result.test_only) == 1 and result.test_only[0].endswith("regimeOFF")
    assert result.results[-1].variant == result.test_only[0]
    assert result.selection.variant is not None
    assert result.selection.variant not in result.test_only
    assert result.selected() is result.result(result.selection.variant)
    assert "turn TEST into TRAIN" in result.note
    for r in result.results:
        assert r.split_ts == result.split_ts
        assert r.test.window == (result.split_ts, None)
        assert r.train.window == (None, result.split_ts)


def test_selection_is_the_max_train_t_stat(result: sd.DiscoveryResult) -> None:
    eligible = [
        r
        for r in result.results
        if r.variant not in result.test_only and r.train_summary.n >= MIN_TRAIN
    ]
    best = max(eligible, key=lambda r: r.train_summary.t_stat)
    assert result.selection.variant == best.variant


def test_selection_is_recorded_before_any_test_backtest(world, monkeypatch) -> None:
    data, events, split = world
    events_log: list[str] = []
    real_bt = wf.run_backtest
    real_select = sd.select_on_train

    def spy_bt(data_, cfg, events_, start_ts=None, end_ts=None, **kw):
        events_log.append("train" if end_ts == split else "test")
        assert (start_ts, end_ts) in ((None, split), (split, None))
        return real_bt(data_, cfg, events_, start_ts=start_ts, end_ts=end_ts, **kw)

    def spy_select(phases, min_train):
        events_log.append("select")
        return real_select(phases, min_train)

    monkeypatch.setattr(wf, "run_backtest", spy_bt)
    monkeypatch.setattr(sd, "select_on_train", spy_select)
    res = sd.discover(data, BASE, events, min_train=MIN_TRAIN)
    assert events_log == ["train"] * 13 + ["select"] + ["test"] * 13
    assert res.stage_log[1].startswith("selection recorded")


def test_selection_is_invariant_to_test_period_candles(world, result) -> None:
    data, events, split = world
    other, _ = make_world("null", 17, years=2.5)
    perturbed = swap_test_period(data, other, split)
    again = sd.discover(perturbed, BASE, events, min_train=MIN_TRAIN, min_test=MIN_TRAIN)
    assert again.split_ts == split
    assert again.selection == result.selection
    assert [r.train_summary for r in again.results] == [r.train_summary for r in result.results]
    changed = [
        r.variant
        for r, s in zip(result.results, again.results, strict=True)
        if r.test_summary != s.test_summary
    ]
    assert changed, "the TEST perturbation must actually change TEST results"


def test_only_the_selected_variant_is_judged(result: sd.DiscoveryResult) -> None:
    """C4: discovery contributes ONE pre-registered TEST look; the rest is context."""
    judged = [r.variant for r in result.results if result.judged(r.variant)]
    assert judged == [result.selection.variant]
    assert not result.judged(result.test_only[0])
    for r in result.results:  # every variant is summarised with the same m = 4 rule
        assert r.test_summary.lb_confidence == pytest.approx(1 - 0.05 / 4)
        assert r.stats == wf.DEFAULT_STATS
