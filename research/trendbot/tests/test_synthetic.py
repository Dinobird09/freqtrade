"""Synthetic worlds: determinism, candle validity and STATISTICAL checks of the ground truth.

The edge tests use only what a strategy could observe (volume / previous-20 mean >= 1.5 and
an up close), with forward windows thinned to be non-overlapping so the standard errors are
honest. Statistics are on 12-candle forward log returns from the signal close (= entry at
the next open) to the close 12 candles later.
"""

from __future__ import annotations

import math
from functools import cache
from itertools import pairwise

import pytest

from research.trendbot import synthetic as syn
from research.trendbot.data import candle_problem
from research.trendbot.models import HOUR_MS
from research.trendbot.synthetic import (
    EFFECT_HORIZON,
    EFFECT_MU_SIGMAS,
    PAIR_SPECS,
    WORLDS,
    generate_world,
    make_world,
)


SEED = 11
YEARS = 3.0
K = EFFECT_HORIZON
PAIRS = ("BTC/USDT", "ETH/USDT", "BNB/USDT")


@cache
def world(name: str, seed: int = SEED, years: float = YEARS) -> syn.SyntheticWorld:
    return generate_world(name, seed, years=years)


def sigma(pair: str) -> float:
    return PAIR_SPECS[pair.split("/")[0]].sigma


def effect(pair: str) -> float:
    """Planted expected forward log return over the K-candle window."""
    return K * EFFECT_MU_SIGMAS * sigma(pair)


def vol_spikes(candles, mult: float = 1.5, n: int = 20) -> list[int]:
    """Indices whose volume >= mult * mean of the previous n volumes (observable, causal)."""
    out = []
    window = sum(c.volume for c in candles[:n])
    for i in range(n, len(candles)):
        if candles[i].volume >= mult * window / n:
            out.append(i)
        window += candles[i].volume - candles[i - n].volume
    return out


def up_spikes(candles) -> list[int]:
    return [i for i in vol_spikes(candles) if candles[i].close > candles[i].open]


def thin(idxs, gap: int = K) -> list[int]:
    """Greedy, causal thinning so the K-candle forward windows never overlap."""
    out, last = [], -(10**9)
    for i in idxs:
        if i - last >= gap:
            out.append(i)
            last = i
    return out


def fwd_stats(candles, idxs) -> tuple[float, float, int]:
    """(mean, standard error, n) of the K-candle forward log return after each index."""
    kept = [i for i in thin(idxs) if i + K < len(candles)]
    vals = [math.log(candles[i + K].close / candles[i].close) for i in kept]
    n = len(vals)
    assert n >= 30, f"too few samples ({n}) for a meaningful test"
    mean = sum(vals) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / (n - 1))
    return mean, sd / math.sqrt(n), n


def assert_present(pair: str, candles, idxs) -> None:
    mean, se, n = fwd_stats(candles, idxs)
    assert mean / se > 3.0, f"{pair}: effect not detected, t={mean / se:.2f} (n={n})"


def assert_absent(pair: str, candles, idxs) -> None:
    """Consistent with zero AND inconsistent with the planted effect size (power check)."""
    mean, se, n = fwd_stats(candles, idxs)
    assert abs(mean / se) < 3.0, f"{pair}: spurious effect, t={mean / se:.2f} (n={n})"
    assert (mean - effect(pair)) / se < -3.0, f"{pair}: cannot rule out the effect (n={n})"


def log_returns(candles) -> list[float]:
    return [math.log(c.close / c.open) for c in candles]


# ---------------------------------------------------------------------- shape / validity
def test_default_length_start_and_spacing():
    data, events = make_world("null", 1)  # default 6 years
    assert set(data) == set(PAIRS)
    for pair, candles in data.items():
        assert len(candles) == 13_140
        assert candles[0].ts == syn.START_TS == 1_546_300_800_000  # 2019-01-01T00:00Z
        assert candles[0].open == PAIR_SPECS[pair.split("/")[0]].start_price
        assert all(b.ts - a.ts == 4 * HOUR_MS for a, b in pairwise(candles))
    assert events


@pytest.mark.parametrize("name", WORLDS)
def test_ohlc_valid_and_open_is_previous_close(name):
    for candles in world(name).candles.values():
        assert all(candle_problem(c) is None for c in candles)
        assert all(c.volume > 0 for c in candles)
        assert all(b.open == a.close for a, b in pairwise(candles))


def test_same_seed_identical_different_seed_differs():
    a = make_world("planted", 5, years=0.5)
    b = make_world("planted", 5, years=0.5)
    c = make_world("planted", 6, years=0.5)
    assert a == b
    assert a[0]["BTC/USDT"] != c[0]["BTC/USDT"]
    assert a[1] != c[1]


def test_worlds_share_noise_and_pair_paths_are_order_independent():
    null, planted = world("null"), world("planted")
    for pair in PAIRS:
        assert [c.volume for c in null.candles[pair]] == [c.volume for c in planted.candles[pair]]
        assert null.candles[pair] != planted.candles[pair]  # only the drift differs
    assert null.events == planted.events
    solo, _ = make_world("null", SEED, years=YEARS, pairs=("ETH/USDT",))
    assert solo["ETH/USDT"] == null.candles["ETH/USDT"]


def test_invalid_arguments():
    with pytest.raises(ValueError, match="unknown world"):
        make_world("bogus", 1)
    with pytest.raises(ValueError, match="split_frac"):
        make_world("decay", 1, split_frac=1.0)
    with pytest.raises(ValueError, match="no candles"):
        make_world("null", 1, years=0.0)
    with pytest.raises(ValueError, match="unique"):
        make_world("null", 1, pairs=("BTC/USDT", "BTC/USDT"))


# ---------------------------------------------------------------------- return statistics
def test_per_candle_vol_fat_tails_and_correlation():
    data = world("null").candles
    rets = {p: log_returns(c) for p, c in data.items()}
    for pair, r in rets.items():
        n = len(r)
        mean = sum(r) / n
        var = sum((x - mean) ** 2 for x in r) / n
        assert 0.9 < math.sqrt(var) / sigma(pair) < 1.1, pair
        kurt = sum((x - mean) ** 4 for x in r) / n / var**2
        assert kurt > 4.0, f"{pair}: tails not fat (kurtosis {kurt:.2f})"

    def corr(a, b):
        ma, mb = sum(a) / len(a), sum(b) / len(b)
        cov = sum((x - ma) * (y - mb) for x, y in zip(a, b, strict=True))
        return cov / math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))

    for i, p in enumerate(PAIRS):
        for q in PAIRS[i + 1 :]:
            rho = corr(rets[p], rets[q])
            assert 0.4 <= rho <= 0.9, f"corr({p}, {q}) = {rho:.3f}"


def test_volume_spikes_and_bnb_wicks():
    w = world("null")
    for pair in PAIRS:
        rate = sum(w.truth[pair].spike) / len(w.truth[pair].spike)
        assert 0.06 <= rate <= 0.08, f"{pair}: spike rate {rate:.3f}"
        obs = len(vol_spikes(w.candles[pair])) / len(w.candles[pair])
        assert 0.05 <= obs <= 0.10, f"{pair}: observable spike rate {obs:.3f}"

    def wick_asymmetry(candles, frac=0.02):
        """Rate(lower wick exceeds upper by > frac) minus the mirror rate; ~0 if symmetric."""
        down = up = 0
        for c in candles:
            lower = (min(c.open, c.close) - c.low) / min(c.open, c.close)
            upper = (c.high - max(c.open, c.close)) / max(c.open, c.close)
            down += lower - upper > frac
            up += upper - lower > frac
        return (down - up) / len(candles)

    # ~2 % of BNB candles get a 1-3 % news wick; about half of those clear the 2 % bar.
    assert wick_asymmetry(w.candles["BNB/USDT"]) > 0.005
    for pair in ("BTC/USDT", "ETH/USDT"):
        assert abs(wick_asymmetry(w.candles[pair])) < 0.004, pair


# ---------------------------------------------------------------------- ground truth
def test_null_has_no_edge():
    w = world("null")
    for pair in PAIRS:
        assert not any(w.truth[pair].drift) and not w.truth[pair].triggers
        assert_absent(pair, w.candles[pair], up_spikes(w.candles[pair]))


def test_planted_edge_is_detectable():
    w = world("planted")
    for pair in PAIRS:
        truth = w.truth[pair]
        assert truth.mu == pytest.approx(EFFECT_MU_SIGMAS * sigma(pair))
        assert any(truth.drift) and all(d in (0.0, truth.mu) for d in truth.drift)
        assert all(truth.spike[i] for i in truth.triggers)
        candles = w.candles[pair]
        assert_present(pair, candles, up_spikes(candles))
        mean, _, _ = fwd_stats(candles, up_spikes(candles))
        assert 0.5 * effect(pair) < mean < 1.5 * effect(pair)
        # Down-closing spikes do not trigger: whatever follows them is not planted by them.
        down = [i for i in vol_spikes(candles) if candles[i].close <= candles[i].open]
        assert not set(down) & set(truth.triggers)


def test_decay_edge_only_before_split():
    w = world("decay")
    split = w.split_idx
    assert split == int(0.7 * len(w.candles["BTC/USDT"]))
    for pair in PAIRS:
        truth = w.truth[pair]
        assert not any(truth.drift[split:]), "no planted drift after the split"
        assert truth.triggers and max(truth.triggers) < split
        candles = w.candles[pair]
        spikes = up_spikes(candles)
        assert_present(pair, candles, [i for i in spikes if i + K < split])
        assert_absent(pair, candles, [i for i in spikes if i >= split])


def test_decay_respects_split_frac():
    w = generate_world("decay", SEED, years=1.0, split_frac=0.5)
    for truth in w.truth.values():
        assert not any(truth.drift[w.split_idx :])
        assert any(truth.drift[: w.split_idx])
    assert w.split_ts == syn.START_TS + w.split_idx * syn.TIMEFRAME_MS


def test_hour_edge_only_for_12_to_20_utc_closes():
    w = world("hour_edge")
    lo, hi = syn.HOUR_EDGE_CLOSE_HOURS
    edge_hours = {h for h in range(0, 24, 4) if lo <= h <= hi}
    assert edge_hours == {12, 16, 20}  # 4H candles opening 08:00, 12:00, 16:00 UTC
    for pair in PAIRS:
        truth = w.truth[pair]
        assert truth.triggers
        assert {syn.close_hour(i) for i in truth.triggers} <= edge_hours
        candles = w.candles[pair]
        for i in truth.triggers:  # close hour of the Candle itself, not just the index rule
            assert (candles[i].ts + 4 * HOUR_MS) // HOUR_MS % 24 in edge_hours
        spikes = vol_spikes(candles)
        edge_spikes = {i for i in spikes if syn.close_hour(i) in edge_hours}
        ups = [i for i in spikes if candles[i].close > candles[i].open]
        assert_present(pair, candles, [i for i in ups if i in edge_spikes])
        # Non-edge spikes, excluding any whose forward window could overlap the drift of
        # an edge-hour spike (volume/hour only: both independent of returns, so no bias).
        isolated = [
            i
            for i in ups
            if i not in edge_spikes and not any(j in edge_spikes for j in range(i - K + 1, i + K))
        ]
        assert_absent(pair, candles, isolated)


# ---------------------------------------------------------------------- news events
def test_synthetic_events():
    w = world("null")
    ev = w.events
    months = YEARS * 12
    end_ts = syn.START_TS + len(w.candles["BTC/USDT"]) * syn.TIMEFRAME_MS
    assert ev == sorted(ev, key=lambda e: (e.ts, e.scope, e.kind))
    assert all(syn.START_TS <= e.ts < end_ts and e.note == "synthetic" for e in ev)

    def select(kind):
        return [e for e in ev if e.kind == kind]

    macro = select("macro")
    assert 1.5 * months <= len(macro) <= 2.5 * months
    assert all(e.scope == "ALL" and e.impact == "high" for e in macro)
    # The 3-year span ends 2021-12-31 (leap year 2020), so the last month is partial.
    reg = select("regulatory")
    assert months // 3 - 1 <= len(reg) <= months // 3
    assert all(e.scope == "EXCHANGE:binance" and e.impact == "high" for e in reg)
    burns = select("bnb_burn")
    assert len(burns) == months // 3
    assert all(e.scope == "BNB" and e.impact == "medium" for e in burns)
    gaps = [b.ts - a.ts for a, b in pairwise(burns)]
    assert all(60 * 24 * HOUR_MS < g < 120 * 24 * HOUR_MS for g in gaps)  # quarterly
    pools = select("launchpool")
    assert months - 1 <= len(pools) <= months
    assert all(e.scope == "BNB" and e.impact == "medium" for e in pools)
    unlocks = select("unlock")
    assert YEARS - 1 <= len(unlocks) <= YEARS
    assert all(e.scope == "ETH" and e.impact == "high" for e in unlocks)
    assert {e.kind for e in ev} == {"macro", "regulatory", "bnb_burn", "launchpool", "unlock"}
