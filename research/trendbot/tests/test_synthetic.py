"""Synthetic worlds: determinism, candle validity and STATISTICAL checks of the ground truth.

The edge tests use only what a strategy could observe (volume / previous-20 mean >= 1.5 and
an up close), with forward windows thinned to be non-overlapping so the standard errors are
honest. Statistics are on 12-candle forward log returns from the signal close (= entry at
the next open) to the close 12 candles later, compared with what the generator's recorded
ground truth (``PairTruth.drift``) implies for exactly those windows.

The planted drift is conditional only (zero mean over the active region), so outside the
effect windows every candle drifts down by ``truth.offset``; "absent" therefore means "no
more than the background the ground truth implies", not "exactly zero".
"""

from __future__ import annotations

import hashlib
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
    GAP_PROB,
    GAP_SIGMAS,
    PAIR_SPECS,
    WORLDS,
    ZERO_EDGE_MU_SIGMAS,
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


def kept(candles, idxs) -> list[int]:
    return [i for i in thin(idxs) if i + K < len(candles)]


def fwd_stats(candles, idxs) -> tuple[float, float, int]:
    """(mean, standard error, n) of the K-candle forward log return after each index."""
    vals = [math.log(candles[i + K].close / candles[i].close) for i in kept(candles, idxs)]
    n = len(vals)
    assert n >= 30, f"too few samples ({n}) for a meaningful test"
    mean = sum(vals) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / (n - 1))
    return mean, sd / math.sqrt(n), n


def expected_fwd(pair: str, candles, truth: syn.PairTruth, idxs) -> float:
    """Ground-truth expected K-candle forward log return over exactly the tested windows:
    the recorded planted drift plus the martingale convexity term -K * sigma^2 / 2."""
    ks = kept(candles, idxs)
    planted = sum(math.fsum(truth.drift[i + 1 : i + K + 1]) for i in ks) / len(ks)
    return planted - K * sigma(pair) ** 2 / 2


def assert_present(pair: str, candles, truth, idxs) -> None:
    """Detectably positive AND consistent with the ground truth of those windows."""
    mean, se, n = fwd_stats(candles, idxs)
    assert mean / se > 3.0, f"{pair}: effect not detected, t={mean / se:.2f} (n={n})"
    exp = expected_fwd(pair, candles, truth, idxs)
    assert exp > 0.5 * K * (truth.mu - truth.offset), f"{pair}: windows not planted ({exp})"
    assert abs(mean - exp) / se < 3.5, f"{pair}: {mean:.4f} vs truth {exp:.4f} (se {se:.4f})"


def assert_absent(pair: str, candles, truth, idxs) -> None:
    """No conditional effect beyond the ground-truth background, with power to rule out a
    planted-size effect (K * mu on top of that background)."""
    mean, se, n = fwd_stats(candles, idxs)
    background = expected_fwd(pair, candles, truth, idxs)
    planted = K * EFFECT_MU_SIGMAS * sigma(pair)
    t = (mean - background) / se
    assert abs(t) < 3.0, f"{pair}: spurious effect, t={t:.2f} vs background (n={n})"
    assert (mean - background - planted) / se < -3.0, f"{pair}: cannot rule out the effect"


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
        truth = w.truth[pair]
        assert not any(truth.drift) and not truth.triggers and truth.offset == 0.0
        assert_absent(pair, w.candles[pair], truth, up_spikes(w.candles[pair]))


def test_planted_edge_is_detectable():
    w = world("planted")
    for pair in PAIRS:
        truth = w.truth[pair]
        assert truth.mu == pytest.approx(EFFECT_MU_SIGMAS * sigma(pair))
        assert 0.2 * truth.mu < truth.offset < 0.5 * truth.mu  # ~1/3 of candles in a window
        assert all(d in (truth.mu - truth.offset, -truth.offset) for d in truth.drift)
        assert all(truth.spike[i] for i in truth.triggers)
        assert all(truth.in_window(i + 1) for i in truth.triggers if i + 1 < len(truth.drift))
        candles = w.candles[pair]
        assert_present(pair, candles, truth, up_spikes(candles))
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
        assert truth.active_end == split
        assert truth.triggers and max(truth.triggers) < split
        # The offset is computed over the pre-split region only.
        assert abs(math.fsum(truth.drift[:split])) <= 2 * K * truth.mu
        candles = w.candles[pair]
        spikes = up_spikes(candles)
        assert_present(pair, candles, truth, [i for i in spikes if i + K < split])
        assert_absent(pair, candles, truth, [i for i in spikes if i >= split])
        assert expected_fwd(pair, candles, truth, [i for i in spikes if i >= split]) == (
            pytest.approx(-K * sigma(pair) ** 2 / 2)
        )


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
        assert_present(pair, candles, truth, [i for i in ups if i in edge_spikes])
        # Non-edge spikes, excluding any whose forward window could overlap the drift of
        # an edge-hour spike (volume/hour only: both independent of returns, so no bias).
        isolated = [
            i
            for i in ups
            if i not in edge_spikes and not any(j in edge_spikes for j in range(i - K + 1, i + K))
        ]
        assert_absent(pair, candles, truth, isolated)
        # ... and whose forward windows (almost all) carry only the background offset. The
        # few exceptions follow a generator spike that is not observable as a >= 1.5x candle.
        ks = kept(candles, isolated)
        clean = [i for i in ks if not any(truth.in_window(t) for t in range(i + 1, i + K + 1))]
        assert len(clean) >= 0.8 * len(ks)


@pytest.mark.parametrize("name", ["planted", "decay", "hour_edge"])
def test_planted_drift_is_conditional_only(name):
    """Zero-mean planted drift: the world's terminal prices equal the null world's up to
    the (tiny) residual of the offset search, so buy-and-hold gains nothing from it."""
    null, w = world("null"), world(name)
    for pair in PAIRS:
        truth = w.truth[pair]
        total = math.fsum(truth.drift)
        assert abs(total) <= 2 * K * truth.mu, f"{pair}: residual drift {total}"
        bh_log_ratio = math.log(w.candles[pair][-1].close / null.candles[pair][-1].close)
        assert bh_log_ratio == pytest.approx(total, abs=1e-9)


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_buy_and_hold_stays_bounded(seed):
    """Six-year buy-and-hold multiples stay in [0.05x, 20x] (null; the other worlds match it
    up to the residual checked above), so no world is a crash or a moonshot."""
    data, _ = make_world("null", seed)
    for pair, candles in data.items():
        multiple = candles[-1].close / candles[0].open
        assert 0.05 <= multiple <= 20.0, f"seed {seed} {pair}: buy-and-hold {multiple:.3f}x"


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


# ---------------------------------------------------------------------- v3 C6 options
def world_digest(data, events) -> tuple[str, str]:
    """sha256 of every candle (exact float repr) and of every event, in a canonical order."""
    candles = hashlib.sha256()
    for pair in sorted(data):
        for c in data[pair]:
            row = f"{pair},{c.ts},{c.open!r},{c.high!r},{c.low!r},{c.close!r},{c.volume!r}\n"
            candles.update(row.encode())
    ev = hashlib.sha256()
    for e in events:
        ev.update(f"{e.ts},{e.scope},{e.impact},{e.kind},{e.note},{e.known_from_ts}\n".encode())
    return candles.hexdigest(), ev.hexdigest()


# Digests of the committed default worlds (seed 1, 6 years, default pairs), taken from the
# generator BEFORE effect_strength / gaps / zero_edge existed. Any change to a default
# world's bytes (and therefore to every committed result) fails here.
PINNED_EVENTS = "d2e7ee8555d725576d056179e63f6ab5946763191198c896863e3b9518994b1f"
PINNED_CANDLES = {
    "null": "8d2bf31364aa64954b2323355e4e03d2bbfa5459714f3ba77d945df0a4cf21c8",
    "planted": "26063bfc7d91cfebbf41620bbb792af3c353fd9589c1eaee53c9edcc008eacef",
    "decay": "b5ce0d4609a6dcfb20c3a5d8d520783f44ffbe5052572706e0e8a7cc0e7b7161",
    "hour_edge": "0f1c95b2f5aeca5b95fa41736a44fc85af1d0c6680666bcb69a7a7a8990e147d",
}


@pytest.mark.parametrize("name", sorted(PINNED_CANDLES))
def test_default_worlds_are_byte_identical_to_the_committed_ones(name):
    data, events = make_world(name, 1)
    assert world_digest(data, events) == (PINNED_CANDLES[name], PINNED_EVENTS)
    # Passing the defaults explicitly changes nothing either.
    explicit = make_world(name, 1, effect_strength=None, gaps=False)
    assert world_digest(*explicit) == (PINNED_CANDLES[name], PINNED_EVENTS)


def test_effect_strength_overrides_the_planted_drift():
    base = world("planted")
    same = generate_world("planted", SEED, years=YEARS, effect_strength=EFFECT_MU_SIGMAS)
    assert same.candles == base.candles and same.effect_strength == EFFECT_MU_SIGMAS
    zero = generate_world("planted", SEED, years=YEARS, effect_strength=0.0)
    assert zero.candles == world("null").candles  # strength 0 is exactly the null world
    strong = generate_world("planted", SEED, years=YEARS, effect_strength=1.4)
    for pair in PAIRS:
        truth = strong.truth[pair]
        assert truth.mu == pytest.approx(1.4 * sigma(pair))
        assert abs(math.fsum(truth.drift)) <= 2 * K * truth.mu  # still conditional only
        assert_present(pair, strong.candles[pair], truth, up_spikes(strong.candles[pair]))
    # Stronger planted effect -> larger ground-truth in-window drift, same noise.
    for pair in PAIRS:
        vols = [c.volume for c in strong.candles[pair]]
        assert vols == [c.volume for c in base.candles[pair]]


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("planted", -0.1, "effect_strength must be in"),
        ("planted", float("nan"), "effect_strength must be in"),
        ("planted", 50.0, "effect_strength must be in"),
        ("planted", "0.5", "must be a number"),
        ("planted", True, "must be a number"),
        ("null", 0.3, "null world plants no effect"),
    ],
)
def test_invalid_effect_strength(name, value, message):
    with pytest.raises(ValueError, match=message):
        make_world(name, 1, years=0.2, effect_strength=value)


def test_null_accepts_zero_strength():
    assert make_world("null", 1, years=0.2, effect_strength=0.0) == make_world("null", 1, years=0.2)


def test_zero_edge_world_is_the_planted_mechanism_at_the_calibrated_strength():
    assert "zero_edge" in WORLDS
    w = world("zero_edge")
    assert w.effect_strength == ZERO_EDGE_MU_SIGMAS
    assert 0.0 < ZERO_EDGE_MU_SIGMAS < EFFECT_MU_SIGMAS
    same = generate_world("planted", SEED, years=YEARS, effect_strength=ZERO_EDGE_MU_SIGMAS)
    assert w.candles == same.candles and w.events == world("null").events
    for pair in PAIRS:
        truth = w.truth[pair]
        assert truth.mu == pytest.approx(ZERO_EDGE_MU_SIGMAS * sigma(pair))
        assert truth.triggers and truth.active_end == len(truth.drift)  # whole history
        assert abs(math.fsum(truth.drift)) <= 2 * K * truth.mu
        assert [c.volume for c in w.candles[pair]] == [
            c.volume for c in world("null").candles[pair]
        ]


def test_gaps_are_opt_in_rare_sized_and_otherwise_share_the_noise():
    plain, gapped = world("planted"), generate_world("planted", SEED, years=YEARS, gaps=True)
    assert not plain.gaps and gapped.gaps
    lo, hi = GAP_SIGMAS
    total = 0
    for pair in PAIRS:
        assert plain.truth[pair].gaps == ()
        idx = gapped.truth[pair].gaps
        candles = gapped.candles[pair]
        n = len(candles)
        total += n
        assert idx and 0 not in idx
        rate = len(idx) / n
        assert 0.5 * GAP_PROB <= rate <= 1.6 * GAP_PROB, f"{pair}: gap rate {rate:.4f}"
        gap_set = set(idx)
        for t in range(1, n):
            jump = math.log(candles[t].open / candles[t - 1].close)
            if t in gap_set:
                size = abs(jump) / sigma(pair)
                # |g| in [1, 3] sigma, shifted by the tiny martingale term -log(cosh(g)).
                tol = math.log(math.cosh(hi * sigma(pair))) / sigma(pair) + 1e-9
                assert lo - tol <= size <= hi + tol, f"{pair} candle {t}: {size:.3f} sigma"
            else:
                assert candles[t].open == candles[t - 1].close
        ups = sum(candles[t].open > candles[t - 1].close for t in idx)
        assert 0.25 * len(idx) <= ups <= 0.75 * len(idx)  # both directions
        assert all(candle_problem(c) is None for c in candles)
        assert [c.volume for c in candles] == [c.volume for c in plain.candles[pair]]
    assert gapped.events == plain.events
    assert generate_world("planted", SEED, years=YEARS, gaps=True).candles == gapped.candles


def test_synthetic_events_leave_known_from_to_the_kind_default():
    """C2: make_events never sets known_from_ts, so the news module applies the kind
    default: scheduled kinds (macro, unlock, bnb_burn, launchpool) block +/-w, the
    unscheduled 'regulatory' events only from their own timestamp on."""
    assert all(e.known_from_ts is None for e in world("null").events)
