"""Tests for ml_models.py: stdlib boosting, the gbm / rl layers, and the torch-less lstm path."""

from __future__ import annotations

import json
import random
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from research.trendbot import layers, ml_models
from research.trendbot.config import StrategyConfig
from research.trendbot.indicators import compute_features
from research.trendbot.layers import MarketView, TrainContext, build_context, validate_layer
from research.trendbot.ml_models import (
    GBMLayer,
    GradientBoostingClassifier,
    LSTMLayer,
    QStat,
    RLLayer,
    candle_extras,
)
from research.trendbot.models import EXIT_SL, EXIT_TP, HOUR_MS, CandidateOutcome, FeatureRow
from research.trendbot.synthetic import make_world
from research.trendbot.walkforward import split_ts


CFG = StrategyConfig()
REPO = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def hour_edge():
    return make_world("hour_edge", seed=1)


@pytest.fixture(scope="module")
def fitted_gbm(hour_edge):
    data, events = hour_edge
    ctx = build_context(data, CFG, split_ts(data, 0.7), events)
    layer = GBMLayer()
    layer.fit(ctx)
    return layer, ctx


def _feature_row(rsi: float, vol: float, dist: float, hour: int, ts: int = 0) -> FeatureRow:
    close_ts = ts + CFG.timeframe_ms
    return FeatureRow(
        ts=ts,
        close_ts=close_ts,
        close=100.0,
        ema_fast=101.0,
        ema_slow=100.0,
        ema_regime=90.0,
        rsi=rsi,
        vol_avg=1.0,
        vol_ratio=vol,
        ema_gap_pct=1.0,
        dist_regime_pct=dist,
        hour_utc=hour,
    )


# ---------------------------------------------------------------------- stdlib boosting
def _xor(n: int, seed: int) -> tuple[list[list[float]], list[float]]:
    rng = random.Random(seed)
    x, y = [], []
    for _ in range(n):
        a, b, noise = rng.uniform(-1, 1), rng.uniform(-1, 1), rng.uniform(-1, 1)
        label = (a > 0) != (b > 0)
        if rng.random() < 0.1:  # 10% label noise
            label = not label
        x.append([a, b, noise])
        y.append(1.0 if label else 0.0)
    return x, y


def test_gbm_fits_xor_better_than_chance_and_is_deterministic():
    x, y = _xor(600, seed=1)
    xt, yt = _xor(600, seed=2)
    m1 = GradientBoostingClassifier(n_estimators=100, learning_rate=0.1, l2=1.0).fit(x, y)
    m2 = GradientBoostingClassifier(n_estimators=100, learning_rate=0.1, l2=1.0).fit(x, y)
    assert m1.to_dict() == m2.to_dict()
    acc = sum((p > 0.5) == (t > 0.5) for p, t in zip(m1.predict_proba(xt), yt, strict=True)) / len(
        yt
    )
    assert acc > 0.8  # XOR: a linear model is at chance (~0.5); the Bayes rate is 0.9


def test_gbm_classifier_json_round_trip():
    x, y = _xor(300, seed=3)
    m = GradientBoostingClassifier(n_estimators=30, subsample=0.7, seed=5).fit(x, y)
    back = GradientBoostingClassifier.from_dict(json.loads(json.dumps(m.to_dict())))
    assert back.predict_proba(x) == m.predict_proba(x)


def test_candle_extras_need_history_and_only_use_given_candles(hour_edge):
    data, _ = hour_edge
    cs = data["BTC/USDT"][:40]
    assert candle_extras(cs[:24]) is None
    vec = candle_extras(cs[:30])
    assert vec is not None and len(vec) == 4
    assert vec[0] == pytest.approx((cs[29].close / cs[23].close - 1) * 100)
    assert candle_extras(cs[5:30]) == vec  # only the last 25 candles matter


# ---------------------------------------------------------------------- gbm layer
def test_gbm_layer_is_stdlib_json_and_round_trips(fitted_gbm, hour_edge):
    layer, _ = fitted_gbm
    assert layer.backend == "stdlib"
    assert layer.breakeven is not None and 0 < layer.breakeven["p_star"] < 1
    state = json.loads(json.dumps(layer.state()))
    other = GBMLayer()
    other.load_state(state)
    data, _ = hour_edge
    view = MarketView(data, CFG.timeframe_ms)
    rows = [r for r in compute_features(data["ETH/USDT"], CFG)[-400:] if r.ml_features()]
    assert rows
    for row in rows[::7]:
        assert other.probability("ETH/USDT", row, view) == layer.probability("ETH/USDT", row, view)
        assert other.veto("ETH/USDT", row, view) == layer.veto("ETH/USDT", row, view)


def test_gbm_fit_is_deterministic(fitted_gbm):
    layer, ctx = fitted_gbm
    again = GBMLayer()
    again.fit(ctx)
    assert again.state() == layer.state()


def test_gbm_does_not_veto_unscorable_rows(fitted_gbm):
    layer, ctx = fitted_gbm
    row = replace(_feature_row(60, 2, 5, 12), rsi=None)
    assert layer.veto("BTC/USDT", row, ctx.view)[0] is False
    early = _feature_row(60, 2, 5, 12, ts=ctx.view.history("BTC/USDT", 10**15)[0].ts)
    assert layer.veto("BTC/USDT", early, ctx.view)[0] is False  # < 25 candles of history


def test_gbm_finds_the_hour_edge_on_seed_1_but_misses_significance(hour_edge):
    """Honest seed-1 result: a real gain, but p = 0.070 vs random vetoes (> 0.05)."""
    data, events = hour_edge
    res = validate_layer(GBMLayer, data, CFG, events=events)
    assert res["gain_r"] >= 0.2
    assert res["test_layer_n"] >= 20 and res["vetoed_share"] <= 0.6
    assert 0.05 < res["p_value"] < 0.1
    assert res["status"] == "rejected"
    assert res["fit_seconds"] < 30


def test_gbm_validates_active_on_hour_edge_seed_6():
    data, events = make_world("hour_edge", seed=6)
    res = validate_layer(GBMLayer, data, CFG, events=events)
    assert res["status"] == "active", res
    assert res["gain_r"] >= 0.3 and res["p_value"] <= 0.01


def test_gbm_not_active_on_null_world():
    data, events = make_world("null", seed=1)
    res = validate_layer(GBMLayer, data, CFG, events=events)
    assert res["status"] != "active", res


def test_veto_at_t_ignores_candles_after_t(fitted_gbm, hour_edge):
    """Perturb every candle closing after t0: decisions at or before t0 must not move."""
    layer, ctx = fitted_gbm
    data, _ = hour_edge
    pair = "BNB/USDT"
    cs = data[pair]
    cut = int(len(cs) * 0.85)
    t0 = cs[cut].ts + CFG.timeframe_ms
    rng = random.Random(7)
    altered = list(cs[: cut + 1])
    for c in cs[cut + 1 :]:
        k = rng.uniform(0.5, 1.5)
        altered.append(
            replace(
                c,
                open=c.open * k,
                high=c.high * k * 1.05,
                low=c.low * k * 0.95,
                close=c.close * k,
                volume=c.volume * rng.uniform(0.1, 10),
            )
        )
    view_a = MarketView(data, CFG.timeframe_ms)
    view_b = MarketView({**data, pair: altered}, CFG.timeframe_ms)
    rows_a = compute_features(cs, CFG)[cut - 60 : cut + 1]
    rows_b = compute_features(altered, CFG)[cut - 60 : cut + 1]
    assert rows_a == rows_b and rows_a[-1].close_ts == t0
    rl = RLLayer()
    rl.fit(ctx)
    other = GBMLayer()
    other.load_state(layer.state())  # a separate instance: no shared feature cache
    for ra, rb in zip(rows_a, rows_b, strict=True):
        if ra.ml_features() is None:
            continue
        assert layer.probability(pair, ra, view_a) == other.probability(pair, rb, view_b)
        assert layer.veto(pair, ra, view_a) == other.veto(pair, rb, view_b)
        assert rl.veto(pair, ra, view_a) == rl.veto(pair, rb, view_b)
    # sanity: the perturbation does change what a LATER decision would see
    late_a = compute_features(cs, CFG)[cut + 40]
    late_b = compute_features(altered, CFG)[cut + 40]
    assert late_a != late_b


def test_use_xgboost_false_forces_stdlib(monkeypatch):
    monkeypatch.setattr(ml_models, "_module_available", lambda name: True)
    assert GBMLayer(use_xgboost=False)._use_xgboost() is False
    assert GBMLayer()._use_xgboost() is True


# ---------------------------------------------------------------------- rl layer
def _cand(i: int, rsi: float, vol: float, dist: float, hour: int, r: float) -> CandidateOutcome:
    ts = i * 4 * HOUR_MS
    feats = {
        "rsi": rsi,
        "vol_ratio": vol,
        "ema_gap_pct": 1.0,
        "dist_regime_pct": dist,
        "hour_utc": float(hour),
    }
    reason = EXIT_TP if r > 0 else EXIT_SL
    return CandidateOutcome("BTC/USDT", ts, ts + 4 * HOUR_MS, ts + 8 * HOUR_MS, reason, r, feats)


LOSE = (52.0, 1.2, 1.0, 2)  # low bins, asia session
WIN = (75.0, 4.0, 9.0, 14)  # high bins, us session
MID = (62.0, 2.0, 4.0, 8)


def _rl_ctx() -> TrainContext:
    cands, i = [], 0
    for k in range(20):
        for (rsi, vol, dist, hour), r in (
            (LOSE, -1.0 if k % 2 else -0.9),
            (WIN, 2.0 if k % 2 else 1.9),
            (MID, 2.0 if k % 3 == 0 else -1.0),
        ):
            cands.append(_cand(i, rsi, vol, dist, hour, r))
            i += 1
    return TrainContext(CFG, MarketView({}, CFG.timeframe_ms), 10**15, cands, [])


def test_rl_learns_negative_and_positive_states():
    rl = RLLayer()
    rl.fit(_rl_ctx())
    lose = rl.q_take(_cand(0, *LOSE, 0).features)
    win = rl.q_take(_cand(0, *WIN, 0).features)
    assert lose is not None and win is not None
    assert lose.n == 20 and lose.mean == pytest.approx(-0.95)
    assert win.mean == pytest.approx(1.95)
    view = MarketView({}, CFG.timeframe_ms)
    assert rl.veto("BTC/USDT", _feature_row(*LOSE), view)[0] is True
    assert rl.veto("BTC/USDT", _feature_row(*WIN), view)[0] is False
    assert rl.veto("BTC/USDT", _feature_row(*MID), view)[0] is False  # mean 0, not confident


def test_rl_rare_and_unseen_states_never_veto_and_observe_updates_online():
    rl = RLLayer()
    rl.fit(_rl_ctx())
    view = MarketView({}, CFG.timeframe_ms)
    rare = (52.0, 4.0, 1.0, 20)  # low rsi, high vol, low dist, late session: never seen
    assert rl.q_take(_cand(0, *rare, 0).features) is None
    assert rl.veto("BTC/USDT", _feature_row(*rare), view)[0] is False
    feats = _cand(0, *rare, 0).features
    for k in range(7):
        rl.observe("ETH/USDT", feats, -1.0 - 0.01 * k, 10**12 + k)
    assert rl.q_take(feats).n == 7
    assert rl.veto("BTC/USDT", _feature_row(*rare), view)[0] is False  # 7 < n_min = 8
    rl.observe("ETH/USDT", feats, -1.0, 10**12)  # duplicate (pair, ts): ignored
    assert rl.q_take(feats).n == 7
    rl.observe("ETH/USDT", feats, -1.0, 10**12 + 99)
    assert rl.q_take(feats).n == 8
    assert rl.veto("BTC/USDT", _feature_row(*rare), view)[0] is True
    back = RLLayer()
    back.load_state(json.loads(json.dumps(rl.state())))
    assert back.state() == rl.state()
    assert back.veto("BTC/USDT", _feature_row(*rare), view) == rl.veto(
        "BTC/USDT", _feature_row(*rare), view
    )


def test_qstat_forgetting_weights_recent_outcomes():
    plain, forget = QStat(), QStat()
    for r in [-1.0] * 10 + [2.0] * 10:
        plain.update(r)
        forget.update(r, decay=0.8)
    assert plain.mean == pytest.approx(0.5)
    assert forget.mean > 1.5
    assert plain.stderr() > 0 and QStat().stderr() == float("inf")


def test_rl_rejects_bad_decay():
    with pytest.raises(ValueError):
        RLLayer(decay=0.0)


# ---------------------------------------------------------------------- lstm / registry
def test_lstm_unavailable_without_torch(monkeypatch, hour_edge):
    monkeypatch.setattr(ml_models, "_module_available", lambda name: False)
    assert LSTMLayer().available() == (False, "pip install torch")
    data, events = hour_edge
    res = validate_layer(LSTMLayer, data, CFG, events=events)
    assert res["status"] == "unavailable"
    assert res["reason"] == "pip install torch"


def test_module_import_does_not_import_torch_or_xgboost():
    code = (
        "import sys; import research.trendbot.ml_models; "
        "print('torch' in sys.modules, 'xgboost' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, check=True
    )
    assert out.stdout.split() == ["False", "False"]


def test_layers_are_registered():
    for name in ("gbm", "lstm", "rl"):
        assert name in layers.LAYER_TYPES
        assert layers.LAYER_TYPES[name].kind == "ml"
