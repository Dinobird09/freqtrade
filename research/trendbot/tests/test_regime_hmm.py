"""hmm_regime: pure-Python Gaussian HMM, forward-only filtering and the regime veto layer."""

from __future__ import annotations

import json
import math
import random
import time

import pytest

from research.trendbot.config import StrategyConfig
from research.trendbot.layers import LAYER_TYPES, MarketView, STATUSES, validate_layer
from research.trendbot.models import HOUR_MS, Candle, FeatureRow
from research.trendbot.regime_hmm import (
    GaussianHMM,
    HmmRegimeLayer,
    ObservationStream,
    build_observations,
    regime_series,
    state_labels,
)
from research.trendbot.synthetic import make_world


TF = 4 * HOUR_MS
T0 = 1_546_300_800_000  # 2019-01-01 UTC


def markov_switching(n: int, seed: int, params: list[tuple[float, float]], stay: float = 0.98):
    """Returns and true states of a sticky Markov-switching Gaussian series."""
    rng = random.Random(seed)
    s = 0
    xs, ss = [], []
    for _ in range(n):
        if rng.random() > stay:
            nxt = rng.randrange(len(params) - 1)  # uniformly one of the OTHER states
            s = nxt if nxt < s else nxt + 1
        mu, sd = params[s]
        xs.append(rng.gauss(mu, sd))
        ss.append(s)
    return xs, ss


def candles_from_returns(rets: list[float], start: float = 100.0) -> list[Candle]:
    out, p = [], start
    for i, r in enumerate(rets):
        q = p * math.exp(r)
        out.append(Candle(T0 + i * TF, p, max(p, q), min(p, q), q, 1.0))
        p = q
    return out


def row_at(c: Candle) -> FeatureRow:
    h = (c.ts + TF) // HOUR_MS % 24
    return FeatureRow(c.ts, c.ts + TF, c.close, *([None] * 8), hour_utc=h)


TWO = [(-0.01, 0.03), (0.004, 0.01)]  # (mean, std): volatile falling, calm rising


def test_labels():
    assert state_labels(4) == ("crash", "bear", "neutral", "bull")
    assert state_labels(5)[-1] == "euphoria"
    assert state_labels(2) == ("bear", "bull")


def test_parameter_recovery_two_regimes_1d():
    xs, ss = markov_switching(6000, seed=3, params=TWO)
    hmm = GaussianHMM(n_states=2, max_iter=100).fit([[x] for x in xs])
    hmm.reorder(sorted(range(2), key=lambda s: hmm.means[s][0]))
    for s, (mu, sd) in enumerate(TWO):
        assert hmm.means[s][0] == pytest.approx(mu, abs=0.0015)
        assert math.sqrt(hmm.variances[s][0]) == pytest.approx(sd, rel=0.08)
    assert hmm.trans[0][0] == pytest.approx(0.98, abs=0.015)
    probs, ll = hmm.filter([[x] for x in xs])
    hit = sum((p[1] > p[0]) == (s == 1) for p, s in zip(probs, ss, strict=True)) / len(ss)
    assert hit > 0.9 and math.isfinite(ll)


def test_layer_recovers_regimes_and_labels_from_candles():
    xs, ss = markov_switching(6000, seed=5, params=TWO)
    view = MarketView({"BTC/USDT": candles_from_returns(xs)}, TF)
    layer = HmmRegimeLayer(n_states=2, min_candles=1000, max_iter=100)
    layer.fit_candles(view, T0 + 6000 * TF)
    hmm = layer.models["BTC/USDT"]
    assert layer.labels["BTC/USDT"] == ["bear", "bull"]
    assert hmm.means[0][0] == pytest.approx(TWO[0][0], abs=0.003)
    assert hmm.means[1][0] == pytest.approx(TWO[1][0], abs=0.002)
    assert math.sqrt(hmm.variances[0][0]) == pytest.approx(TWO[0][1], rel=0.15)
    assert math.sqrt(hmm.variances[1][0]) == pytest.approx(TWO[1][1], rel=0.15)
    assert hmm.means[0][1] > 2 * hmm.means[1][1]  # bear state has the higher realized vol
    series = regime_series(view, T0 + 6000 * TF, layer=layer)
    truth = {T0 + (i + 1) * TF: s for i, s in enumerate(ss)}
    hit = sum((lab == "bull") == (truth[t] == 1) for t, lab, _ in series) / len(series)
    assert hit > 0.85


def test_observations_are_causal_and_match_the_stream():
    xs, _ = markov_switching(300, seed=1, params=TWO)
    cs = candles_from_returns(xs)
    ts, obs = build_observations(cs, window=12, timeframe_ms=TF)
    assert ts[0] == cs[12].ts + TF  # 12 returns need 13 closes
    assert obs[0][0] == pytest.approx(xs[12])
    st = ObservationStream(12)
    inc = [o for o in (st.push(c.close) for c in cs) if o is not None]
    assert inc == obs
    _, obs2 = build_observations(cs[:150] + candles_from_returns([0.5] * 150), 12, TF)
    assert obs2[: 150 - 12] == obs[: 150 - 12]


def test_filter_is_forward_only():
    xs, _ = markov_switching(3000, seed=7, params=TWO)
    obs = [[x] for x in xs]
    hmm = GaussianHMM(n_states=3).fit(obs)
    t = 1700
    changed = obs[: t + 1] + [[rng_x * 50] for rng_x in xs[t + 1 :]]
    a, _ = hmm.filter(obs)
    b, _ = hmm.filter(changed)
    assert a[: t + 1] == b[: t + 1]
    assert a[t + 1] != b[t + 1]


def test_layer_veto_is_causal_to_future_candles():
    xs, _ = markov_switching(4000, seed=9, params=TWO)
    cs = candles_from_returns(xs)
    layer = HmmRegimeLayer(n_states=2, min_candles=1000)
    layer.fit_candles(MarketView({"BTC/USDT": cs}, TF), cs[2000].ts)
    t = 3000
    future = candles_from_returns([-0.2 if i % 2 else 0.2 for i in range(999)], cs[t].close)
    future = [Candle(cs[t].ts + (i + 1) * TF, *c[1:]) for i, c in enumerate(_tuples(future))]
    v1 = MarketView({"BTC/USDT": cs}, TF)
    v2 = MarketView({"BTC/USDT": cs[: t + 1] + future}, TF)
    fresh = HmmRegimeLayer()
    fresh.load_state(json.loads(json.dumps(layer.state())))
    assert layer.veto("ETH/USDT", row_at(cs[t]), v2) == fresh.veto("X", row_at(cs[t]), v1)
    assert layer.regime_probs("BTC/USDT", v1, cs[t].ts + TF) == layer.regime_probs(
        "BTC/USDT", v2, cs[t].ts + TF
    )


def _tuples(cs):
    return [(c.ts, c.open, c.high, c.low, c.close, c.volume) for c in cs]


def test_incremental_cache_equals_batch_filter_and_handles_new_views():
    xs, _ = markov_switching(3000, seed=11, params=TWO)
    cs = candles_from_returns(xs)
    layer = HmmRegimeLayer(n_states=2, min_candles=1000)
    layer.fit_candles(MarketView({"BTC/USDT": cs}, TF), cs[1500].ts)
    hmm = layer.models["BTC/USDT"]
    ts, obs = build_observations(cs, 12, TF)
    batch = dict(zip(ts, hmm.filter(obs)[0], strict=True))
    view = MarketView({"BTC/USDT": cs}, TF)
    for i in list(range(100, 3000, 37)) + [500, 50, 2999]:  # forward, then out of order
        close_ts = cs[i].ts + TF
        probs, at = layer.filtered_at("BTC/USDT", view, close_ts)
        assert at == close_ts and probs == batch[close_ts]
    # a live-style new view per step with appended candles extends the cache
    for i in range(2000, 2010):
        v = MarketView({"BTC/USDT": cs[: i + 1]}, TF)
        assert layer.filtered_at("BTC/USDT", v, cs[i].ts + TF)[0] == batch[cs[i].ts + TF]
    # rewritten history is detected and rebuilt
    edited = cs[:1000] + [Candle(c.ts, c.open, c.high, c.low, c.close * 1.5, 1.0) for c in cs[1000:]]
    v = MarketView({"BTC/USDT": edited}, TF)
    got = layer.filtered_at("BTC/USDT", v, edited[2500].ts + TF)[0]
    ts2, obs2 = build_observations(edited[:2501], 12, TF)
    assert got == hmm.filter(obs2)[0][-1]


def test_scaling_is_stable_over_20k_observations():
    rng = random.Random(2)
    xs, _ = markov_switching(20_000, seed=13, params=[(-0.02, 0.05), (0.0, 0.01), (0.01, 0.004)])
    obs = [[x, abs(x) + rng.random() * 1e-3] for x in xs]
    obs[5000] = [5.0, 5.0]  # a ~100-sigma outlier must not underflow the recursion
    hmm = GaussianHMM(n_states=4, max_iter=8).fit(obs)
    probs, ll = hmm.filter(obs)
    assert math.isfinite(ll) and math.isfinite(hmm.loglik)
    for p in probs:
        assert all(math.isfinite(v) and v >= 0 for v in p)
        assert sum(p) == pytest.approx(1.0, abs=1e-9)
    for var in hmm.variances:
        assert all(v >= f for v, f in zip(var, hmm.floors, strict=True))


def test_fit_is_deterministic_and_state_round_trip_is_exact():
    xs, _ = markov_switching(3000, seed=17, params=TWO)
    cs = candles_from_returns(xs)
    view = MarketView({"BTC/USDT": cs}, TF)
    a, b = HmmRegimeLayer(min_candles=1000), HmmRegimeLayer(min_candles=1000)
    a.fit_candles(view, cs[-1].ts + TF)
    b.fit_candles(view, cs[-1].ts + TF)
    assert a.state() == b.state()
    st = json.loads(json.dumps(a.state()))
    assert set(st["models"]["BTC/USDT"]) >= {"means", "variances", "trans", "init", "labels"}
    c = HmmRegimeLayer()
    c.load_state(st)
    assert c.params["min_candles"] == 1000
    assert regime_series(view, cs[-1].ts + TF, layer=c) == regime_series(
        view, cs[-1].ts + TF, layer=a
    )
    for i in range(200, 3000, 101):
        assert c.veto("BTC/USDT", row_at(cs[i]), view) == a.veto("BTC/USDT", row_at(cs[i]), view)


def test_veto_threshold_reason_and_fallbacks():
    xs, ss = markov_switching(3000, seed=19, params=TWO)
    cs = candles_from_returns(xs)
    view = MarketView({"BTC/USDT": cs}, TF)
    layer = HmmRegimeLayer(n_states=2, min_candles=1000, threshold=0.6)
    layer.fit_candles(view, cs[-1].ts + TF)
    results = [layer.veto("ETH/USDT", row_at(cs[i]), view) for i in range(20, 3000)]
    blocked = [b for b, _ in results]
    assert any(blocked) and not all(blocked)
    assert all("P(bear)=" in why and "P(bull)=" in why for _, why in results)
    agree = sum(b == (s == 0) for b, s in zip(blocked, ss[20:], strict=True)) / len(blocked)
    assert agree > 0.85
    assert layer.veto("ETH/USDT", row_at(cs[3]), view)[0] is False  # vol window not full
    late = row_at(Candle(cs[-1].ts + 20 * TF, 1, 1, 1, 1, 1))
    ok, why = layer.veto("ETH/USDT", late, view)
    assert not ok and "stale" in why
    # no reference pair in the view: per-pair models on the traded pair itself
    alt = MarketView({"SOL/USDT": cs}, TF)
    own = HmmRegimeLayer(n_states=2, min_candles=1000)
    own.fit_candles(alt, cs[-1].ts + TF)
    assert list(own.models) == ["SOL/USDT"]
    assert own.veto("SOL/USDT", row_at(cs[500]), alt)[1].startswith("SOL/USDT regime")
    assert own.veto("ADA/USDT", row_at(cs[500]), alt) == (False, "no regime model for ADA/USDT")


def test_registered():
    assert LAYER_TYPES["hmm_regime"] is HmmRegimeLayer
    assert HmmRegimeLayer.kind == "regime"


def test_validate_on_null_world_and_performance():
    data, events = make_world("null", seed=1)  # 6 years x 3 pairs of 4H candles
    t0 = time.time()
    res = validate_layer(lambda: HmmRegimeLayer(), data, StrategyConfig(), events=events)
    elapsed = time.time() - t0
    print("null world validate_layer:", res, f"{elapsed:.1f}s")
    assert res["status"] in STATUSES and res["status"] not in ("error", "unavailable")
    assert res["status"] in ("active", "rejected")
    assert 0.0 <= res["vetoed_share"] <= 1.0
    assert elapsed < 30.0
    series = regime_series(MarketView(data, TF), data["BTC/USDT"][-1].ts + TF)
    assert len(series) == len(data["BTC/USDT"]) - 12
    assert {lab for _, lab, _ in series} <= set(state_labels(4))
