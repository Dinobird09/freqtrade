"""Tests for ml_filter.py against closed-form and independently computed references."""

from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
from collections.abc import Callable

import pytest

from research.trendbot.adoption import is_sha256_hex
from research.trendbot.ml_filter import (
    FEATURES,
    MODEL_FORMAT,
    InsufficientData,
    LogisticModel,
    MLEntryFilter,
    MLFilter,
    breakeven_probability,
    feature_vector,
    make_factory,
    solve_linear,
)
from research.trendbot.models import (
    EXIT_SL,
    EXIT_TP,
    HOUR_MS,
    CandidateOutcome,
    FeatureRow,
    SignalCheck,
)


WIN_R = 1.95  # a 2R target net of fees
LOSS_R = -1.05  # a 1R stop net of fees
HOURS = (0, 4, 8, 12, 16, 20)  # 4H candle close hours


def _logit(p: float) -> float:
    return math.log(p / (1 - p))


def _sig(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


def _logistic_data(
    seed: int, n: int, beta: tuple[float, ...]
) -> tuple[list[list[float]], list[int]]:
    rng = random.Random(seed)
    x, y = [], []
    for _ in range(n):
        row = [rng.gauss(0, 1), rng.uniform(-2, 2), rng.gauss(0.5, 2)][: len(beta) - 1]
        z = beta[0] + sum(b * v for b, v in zip(beta[1:], row, strict=True))
        x.append(row)
        y.append(1 if rng.random() < _sig(z) else 0)
    return x, y


def _penalised_gradient(
    x: list[list[float]], y: list[int], b0: float, w: tuple[float, ...], l2: float
) -> list[float]:
    """Gradient of sum log-loss + l2/2 ||w||^2, written independently of the module."""
    g = [0.0] * (len(w) + 1)
    for row, yi in zip(x, y, strict=True):
        p = _sig(b0 + sum(wj * xj for wj, xj in zip(w, row, strict=True)))
        g[0] += p - yi
        for j, xj in enumerate(row):
            g[j + 1] += (p - yi) * xj
    for j, wj in enumerate(w):
        g[j + 1] += l2 * wj
    return g


# ------------------------------------------------------------------ linear algebra
def test_solve_linear_needs_pivoting() -> None:
    a = [[0.0, 2.0, 1.0], [1.0, 1.0, 1.0], [2.0, 1.0, 3.0]]  # a[0][0] == 0
    b = [-1.0, 2.0, 9.0]  # = a @ [1, -2, 3]
    assert solve_linear(a, b) == pytest.approx([1.0, -2.0, 3.0])
    with pytest.raises(ValueError):
        solve_linear([[1.0, 2.0], [2.0, 4.0]], [1.0, 2.0])
    with pytest.raises(ValueError):
        solve_linear([[1.0, 2.0]], [1.0])


# ------------------------------------------------------------------ logistic model
@pytest.mark.parametrize("l2", [1e-8, 1.0, 25.0])
def test_gradient_is_zero_at_solution(l2: float) -> None:
    x, y = _logistic_data(1, 500, (-0.3, 0.8, -0.5, 0.2))
    m = LogisticModel().fit(x, y, l2=l2)
    b0, w = m.coefficients()
    assert m.converged and m.n_iter <= 50
    g = _penalised_gradient(x, y, b0, w, l2)
    assert max(abs(v) for v in g) < 1e-6
    # Unpenalised intercept => mean fitted probability equals the base rate exactly.
    assert statistics.fmean(m.predict_proba(x)) == pytest.approx(sum(y) / len(y), abs=1e-9)


def test_saturated_binary_feature_closed_form() -> None:
    # x = 0: 100 wins of 400; x = 1: 420 wins of 600. MLE: b = logit(.25), b + w = logit(.7).
    x = [[0.0]] * 400 + [[1.0]] * 600
    y = [1] * 100 + [0] * 300 + [1] * 420 + [0] * 180
    b0, (w,) = LogisticModel().fit(x, y, l2=1e-10).coefficients()
    assert b0 == pytest.approx(_logit(0.25), abs=1e-6)
    assert b0 + w == pytest.approx(_logit(0.7), abs=1e-6)


def test_zero_column_gives_intercept_only_closed_form() -> None:
    x = [[0.0]] * 50
    y = [1] * 15 + [0] * 35
    b0, (w,) = LogisticModel().fit(x, y, l2=1.0).coefficients()
    assert w == 0.0
    assert b0 == pytest.approx(_logit(0.3), abs=1e-9)


def test_recovers_known_coefficients() -> None:
    beta = (-0.5, 1.2, -0.8)
    x, y = _logistic_data(42, 20_000, beta)
    b0, w = LogisticModel().fit(x, y, l2=1e-6).coefficients()
    assert b0 == pytest.approx(beta[0], abs=0.08)
    assert w[0] == pytest.approx(beta[1], abs=0.08)
    assert w[1] == pytest.approx(beta[2], abs=0.08)


def test_no_signal_shrinks_to_base_rate() -> None:
    rng = random.Random(9)
    x = [[rng.gauss(0, 1) for _ in range(3)] for _ in range(3000)]
    y = [1 if rng.random() < 0.3 else 0 for _ in x]
    base = sum(y) / len(y)
    light = LogisticModel().fit(x, y, l2=1.0)
    heavy = LogisticModel().fit(x, y, l2=1e4)
    _, w_light = light.coefficients()
    _, w_heavy = heavy.coefficients()
    assert max(abs(v) for v in w_light) < 0.15
    assert math.hypot(*w_heavy) < 0.2 * math.hypot(*w_light)
    probs = heavy.predict_proba(x)
    assert max(abs(p - base) for p in probs) < 0.01
    assert all(abs(p - base) < 0.06 for p in light.predict_proba(x))


def test_numerically_safe_on_separable_and_extreme_inputs() -> None:
    x = [[float(i)] for i in range(-20, 20)]
    y = [1 if row[0] > 0 else 0 for row in x]  # perfectly separable
    m = LogisticModel().fit(x, y, l2=1e-6)
    probs = m.predict_proba([*x, [1e300], [-1e300]])
    assert all(0.0 < p < 1.0 and math.isfinite(p) for p in probs)
    assert all((p > 0.5) == bool(t) for p, t in zip(probs, y, strict=False))
    assert probs[-2] > 0.999 and probs[-1] < 0.001


def test_model_input_validation() -> None:
    with pytest.raises(RuntimeError):
        LogisticModel().predict_proba([[1.0]])
    with pytest.raises(ValueError):
        LogisticModel().fit([[1.0]], [2])
    with pytest.raises(ValueError):
        LogisticModel().fit([[1.0], [2.0]], [1])
    with pytest.raises(ValueError):
        LogisticModel().fit([[1.0], [2.0]], [1, 0], l2=-1.0)
    with pytest.raises(ValueError):
        LogisticModel().fit([[1.0], [math.nan]], [1, 0])


# ------------------------------------------------------------------ filter
def _features(rng: random.Random, hour: int) -> dict[str, float]:
    return {
        "rsi": rng.uniform(50, 70),
        "vol_ratio": 1.5 + rng.expovariate(1.5),
        "ema_gap_pct": rng.uniform(0.05, 2.0),
        "dist_regime_pct": rng.gauss(5.0, 3.0),
        "hour_utc": float(hour),
    }


def _candidates(seed: int, n: int, win_prob: Callable[[int], float]) -> list[CandidateOutcome]:
    rng = random.Random(seed)
    out = []
    for i in range(n):
        hour = rng.choice(HOURS)
        feats = _features(rng, hour)
        win = rng.random() < win_prob(hour)
        ts = 1_600_000_000_000 + i * 4 * HOUR_MS
        out.append(
            CandidateOutcome(
                pair="ETH/USDT",
                signal_ts=ts,
                entry_ts=ts + 4 * HOUR_MS,
                exit_ts=ts + 16 * HOUR_MS,
                exit_reason=EXIT_TP if win else EXIT_SL,
                r_multiple=WIN_R if win else LOSS_R,
                features=feats,
            )
        )
    return out


def _row(features: dict[str, float] | None, hour: int = 12) -> FeatureRow:
    f = features or {}
    return FeatureRow(
        ts=0,
        close_ts=4 * HOUR_MS,
        close=100.0,
        ema_fast=99.0,
        ema_slow=98.0,
        ema_regime=90.0,
        rsi=f.get("rsi"),
        vol_avg=10.0,
        vol_ratio=f.get("vol_ratio"),
        ema_gap_pct=f.get("ema_gap_pct"),
        dist_regime_pct=f.get("dist_regime_pct"),
        hour_utc=int(f.get("hour_utc", hour)),
    )


CHECK = SignalCheck(pair="ETH/USDT", ts=0, gates=())


def _hour_edge(hour: int) -> float:
    return 0.65 if 12 <= hour <= 19 else 0.12


def test_filter_learns_hour_edge_and_improves_test_expectancy() -> None:
    train = _candidates(1, 1200, _hour_edge)
    ml = MLFilter.fit(train)
    rs = [c.r_multiple for c in train]
    avg_win = statistics.fmean(r for r in rs if r > 0)
    avg_loss = statistics.fmean(r for r in rs if r <= 0)
    assert ml.threshold == pytest.approx(-avg_loss / (avg_win - avg_loss))
    assert ml.threshold == pytest.approx(1.05 / 3.0)
    assert ml.train_n == 1200
    assert ml.train_base_rate == pytest.approx(sum(r > 0 for r in rs) / 1200)
    # Standardisation uses TRAIN statistics.
    assert ml.means[0] == pytest.approx(statistics.fmean(c.features["rsi"] for c in train))

    flt = ml.entry_filter()
    typical = {k: statistics.fmean(c.features[k] for c in train) for k in FEATURES[:4]}
    for hour in HOURS:
        allow, p, reason = flt("ETH/USDT", _row({**typical, "hour_utc": float(hour)}), CHECK)
        assert p is not None and f"{p:.3f}" in reason
        assert allow == (12 <= hour <= 19), (hour, p, ml.threshold)

    # The hour terms dominate the standardised coefficients.
    coefs = dict(ml.explain())
    assert list(coefs) == list(FEATURES)
    hour_mag = math.hypot(coefs["hour_sin"], coefs["hour_cos"])
    assert hour_mag > 3 * max(abs(coefs[k]) for k in FEATURES[:4])

    # Out of sample (fresh seed) the kept trades have better expectancy than all trades.
    test = _candidates(2, 1200, _hour_edge)
    kept = [c.r_multiple for c in test if flt("ETH/USDT", _row(c.features), CHECK)[0]]
    all_r = [c.r_multiple for c in test]
    assert statistics.fmean(all_r) < 0 < 0.5 < statistics.fmean(kept)
    assert 0.2 < len(kept) / len(test) < 0.5


def test_filter_on_noise_is_not_systematically_better() -> None:
    kept_r: list[float] = []
    all_r: list[float] = []
    for seed in range(6):
        ml = MLFilter.fit(_candidates(100 + seed, 800, lambda _h: 0.36))
        probs = []
        for c in _candidates(200 + seed, 800, lambda _h: 0.36):
            allow, p, _ = ml.decide(_row(c.features))
            probs.append(p)
            all_r.append(c.r_multiple)
            if allow:
                kept_r.append(c.r_multiple)
        assert statistics.pstdev(probs) < 0.1  # no signal -> probabilities stay near base
    assert len(kept_r) > 300
    assert abs(statistics.fmean(kept_r) - statistics.fmean(all_r)) < 0.15


def test_insufficient_data() -> None:
    with pytest.raises(InsufficientData, match="at least 100"):
        MLFilter.fit(_candidates(3, 40, _hour_edge))
    rare = _candidates(4, 300, lambda _h: 0.03)  # ~9 wins
    with pytest.raises(InsufficientData, match="wins"):
        MLFilter.fit(rare)
    assert issubclass(InsufficientData, ValueError)
    with pytest.raises(InsufficientData):
        make_factory()(_candidates(5, 10, _hour_edge))


def test_candidates_without_features_are_skipped() -> None:
    good = _candidates(6, 120, _hour_edge)
    bad = [CandidateOutcome("BTC/USDT", 0, 1, 2, EXIT_SL, LOSS_R, {})] * 30
    ml = MLFilter.fit([*good, *bad])
    assert ml.train_n == 120 and ml.train_skipped == 30


def test_entry_filter_never_raises_on_missing_features() -> None:
    ml = MLFilter.fit(_candidates(7, 400, _hour_edge))
    flt = ml.entry_filter()
    allow, p, reason = flt("BTC/USDT", _row(None), CHECK)
    assert (allow, p) == (False, None) and "missing" in reason
    nan_row = _row({"rsi": math.nan, "vol_ratio": 2.0, "ema_gap_pct": 0.5, "dist_regime_pct": 3})
    assert flt("BTC/USDT", nan_row, CHECK)[:2] == (False, None)
    huge = {"rsi": 1e300, "vol_ratio": 1e300, "ema_gap_pct": -1e300, "dist_regime_pct": 1e300}
    allow, p, _ = flt("BTC/USDT", _row(huge), CHECK)
    assert p is None or 0.0 <= p <= 1.0


def test_constant_feature_uses_unit_std_and_factory_exposes_model() -> None:
    cands = _candidates(8, 300, _hour_edge)
    const = [
        CandidateOutcome(
            c.pair,
            c.signal_ts,
            c.entry_ts,
            c.exit_ts,
            c.exit_reason,
            c.r_multiple,
            {**c.features, "rsi": 60.0},
        )
        for c in cands
    ]
    ml = MLFilter.fit(const)
    assert ml.means[0] == 60.0 and ml.stds[0] == 1.0
    assert dict(ml.explain())["rsi"] == pytest.approx(0.0, abs=1e-12)

    flt = make_factory(l2=2.0)(cands)
    assert isinstance(flt, MLEntryFilter)
    assert flt.ml.l2 == 2.0 and flt.ml.train_n == 300
    assert 0 < flt.ml.threshold < 1 and 0 < flt.ml.train_base_rate < 1
    assert "300 TRAIN candidates" in flt.ml.describe()


def test_feature_vector_and_breakeven() -> None:
    vec = feature_vector(
        {"rsi": 55, "vol_ratio": 2, "ema_gap_pct": 0.4, "dist_regime_pct": 3, "hour_utc": 6}
    )
    assert vec == pytest.approx([55, 2, 0.4, 3, 1.0, 0.0], abs=1e-12)  # 06:00 -> angle pi/2
    assert feature_vector(None) is None
    assert feature_vector({"rsi": 55}) is None
    assert breakeven_probability(2.0, -1.0) == pytest.approx(1 / 3)
    with pytest.raises(ValueError):
        breakeven_probability(-0.1, -1.0)


# ------------------------------------------------------------------ fingerprint (CONTRACT v2 A3)
def _independent_fingerprint(ml: MLFilter) -> str:
    """sha256 of the canonical JSON, rebuilt here from the public attributes only."""
    intercept, weights = ml.model.coefficients()
    doc = {
        "coefficients": [repr(float(w)) for w in weights],
        "features": list(FEATURES),
        "intercept": repr(float(intercept)),
        "l2": repr(float(ml.l2)),
        "means": [repr(float(v)) for v in ml.means],
        "stds": [repr(float(v)) for v in ml.stds],
        "threshold": repr(float(ml.threshold)),
    }
    text = json.dumps(doc, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_fingerprint_is_a_deterministic_sha256_of_the_canonical_model() -> None:
    cands = _candidates(11, 400, _hour_edge)
    ml = MLFilter.fit(cands)
    fp = ml.fingerprint()
    assert is_sha256_hex(fp) and fp == fp.lower()
    assert fp == _independent_fingerprint(ml)
    assert fp == hashlib.sha256(ml.canonical_json().encode("utf-8")).hexdigest()
    doc = json.loads(ml.canonical_json())
    assert sorted(doc) == sorted(
        ("features", "means", "stds", "intercept", "coefficients", "threshold", "l2")
    )
    assert doc["features"] == list(FEATURES) and len(doc["coefficients"]) == len(FEATURES)
    # floats are repr strings: they round-trip bit for bit
    assert float(doc["threshold"]) == ml.threshold and doc["l2"] == "1.0"
    assert tuple(float(v) for v in doc["means"]) == ml.means
    # deterministic: the same TRAIN candidates always give the same model and fingerprint
    assert MLFilter.fit(list(cands)).fingerprint() == fp
    assert make_factory()(cands).ml.fingerprint() == fp


def test_fingerprint_changes_whenever_the_model_changes() -> None:
    cands = _candidates(12, 400, _hour_edge)
    ml = MLFilter.fit(cands)
    fp = ml.fingerprint()
    others = {
        "refit on other TRAIN data": MLFilter.fit(_candidates(13, 400, _hour_edge)),
        "one more TRAIN candidate": MLFilter.fit([*cands, _candidates(14, 1, _hour_edge)[0]]),
        "another l2": MLFilter.fit(cands, l2=2.0),
    }
    fps = {name: other.fingerprint() for name, other in others.items()}
    assert fp not in fps.values() and len(set(fps.values())) == len(fps), fps

    def tweaked(**changes: object) -> str:
        args = {
            "model": ml.model,
            "means": ml.means,
            "stds": ml.stds,
            "threshold": ml.threshold,
            "train_n": ml.train_n,
            "train_wins": ml.train_wins,
            "avg_win_r": ml.avg_win_r,
            "avg_loss_r": ml.avg_loss_r,
        }
        args.update(changes)
        return MLFilter(**args).fingerprint()  # type: ignore[arg-type]

    assert tweaked() == fp  # same parameters -> same identity
    # train statistics that do not change a decision are not part of the identity
    assert tweaked(train_n=1, train_wins=0) == fp
    one_ulp = math.nextafter(ml.threshold, 1.0)
    assert tweaked(threshold=one_ulp) != fp
    assert tweaked(means=(math.nextafter(ml.means[0], math.inf), *ml.means[1:])) != fp
    assert tweaked(stds=(*ml.stds[:-1], ml.stds[-1] * 2)) != fp
    model = LogisticModel()
    model.intercept, model.weights, model.l2 = ml.model.intercept, ml.model.weights, ml.l2
    assert tweaked(model=model) == fp
    model.weights = (*ml.model.weights[:-1], math.nextafter(ml.model.weights[-1], math.inf))
    assert tweaked(model=model) != fp
    model.weights, model.intercept = ml.model.weights, ml.model.intercept + 1e-9  # type: ignore[operator]
    assert tweaked(model=model) != fp


# ------------------------------------------------------------------ serialisation (live bot)
def test_to_json_from_json_round_trip_is_exact() -> None:
    cands = _candidates(15, 400, _hour_edge)
    ml = MLFilter.fit(cands)
    text = ml.to_json()
    doc = json.loads(text)
    assert doc["format"] == MODEL_FORMAT and doc["fingerprint"] == ml.fingerprint()
    assert json.dumps(doc["model"], sort_keys=True, separators=(",", ":")) == ml.canonical_json()
    assert text == json.dumps(doc, sort_keys=True, indent=2) + "\n"  # canonical form
    back = MLFilter.from_json(text, expected_fingerprint=ml.fingerprint())
    assert back.fingerprint() == ml.fingerprint() and back.to_json() == text
    assert (back.means, back.stds, back.threshold, back.l2) == (
        ml.means,
        ml.stds,
        ml.threshold,
        ml.l2,
    )
    assert back.model.coefficients() == ml.model.coefficients()
    assert (back.train_n, back.train_wins, back.avg_win_r, back.avg_loss_r) == (
        ml.train_n,
        ml.train_wins,
        ml.avg_win_r,
        ml.avg_loss_r,
    )
    # the reloaded filter takes exactly the same decisions, with the same reasons
    for cand in _candidates(16, 200, _hour_edge):
        row = _row(cand.features)
        assert back.decide(row) == ml.decide(row)


def test_from_json_refuses_a_model_that_is_not_the_fingerprinted_one() -> None:
    ml = MLFilter.fit(_candidates(17, 400, _hour_edge))
    doc = json.loads(ml.to_json())
    tampered = json.loads(ml.to_json())
    tampered["model"]["threshold"] = repr(ml.threshold + 0.01)
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        MLFilter.from_json(json.dumps(tampered))
    with pytest.raises(ValueError, match="fingerprint mismatch"):
        MLFilter.from_json(ml.to_json(), expected_fingerprint="0" * 64)
    for broken in (
        {**doc, "format": "other/1"},
        {k: v for k, v in doc.items() if k != "train"},
        {**doc, "model": {**doc["model"], "features": ["rsi"]}},
        {**doc, "model": {**doc["model"], "coefficients": doc["model"]["coefficients"][:-1]}},
    ):
        with pytest.raises(ValueError):
            MLFilter.from_json(json.dumps(broken))
    with pytest.raises(ValueError):
        MLFilter.from_json("not json")


def test_veto_wording_follows_contract_c1() -> None:
    ml = MLFilter.fit(_candidates(18, 400, _hour_edge))
    reasons = {ml.decide(_row(c.features))[2] for c in _candidates(19, 100, _hour_edge)}
    assert any(r.endswith("so the entry is vetoed.") for r in reasons)
    assert any(r.endswith("so the entry is allowed.") for r in reasons)
    assert not any("removed" in r for r in reasons)
    assert "vetoes every rule-passing signal" in ml.describe()
