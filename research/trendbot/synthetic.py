"""Synthetic 4H candle worlds with KNOWN ground truth, for verifying the research pipeline.

These worlds are verification fixtures for the METHODOLOGY: does the backtest / walk-forward
/ ML stack find an edge where one was deliberately planted, and refuse to find one where
there is none? They are never evidence about real markets. Every parameter below was
picked by hand to look roughly crypto-like; none was estimated from market data.

Generative model (pair p, candle t; t = 0 opens 2019-01-01T00:00Z, one candle per 4 hours)::

    f_t   = k_t * z_t          common market factor, shared by all pairs
    e_pt  = k_pt * z_pt        idiosyncratic shock
            z ~ N(0, 1);  k = K_LO w.p. 0.9, K_HI = 2.5 * K_LO w.p. 0.1, with E[k^2] = 1
            (Gaussian scale mixture: unit variance, excess kurtosis 3.2 per component and
            ~1.7-2.4 for the combined candle return: "fat-ish" tails)
    beta_p = sigma_p * sqrt(rho_p),  s_p = sigma_p * sqrt(1 - rho_p)
    v_pt  = beta_p^2 k_t^2 + s_p^2 k_pt^2                     conditional variance
    r_pt  = -v_pt / 2 + d_pt + beta_p f_t + s_p e_pt           log(close / open)

    sigma (per-candle vol): BTC 1.2 %, ETH 1.5 %, BNB 1.4 %
    rho (factor share of variance): BTC 0.80, ETH 0.70, BNB 0.55
        -> return correlations BTC-ETH ~0.75, BTC-BNB ~0.66, ETH-BNB ~0.62
    start prices: BTC 4000, ETH 150, BNB 6 (unknown pairs: 100, sigma 1.5 %, rho 0.6)

Martingale choice: the -v/2 convexity term makes E[exp(r) | k] = 1 whenever d = 0, so the
PRICE (not the log-price) is an exact martingale. By optional stopping, any entry/exit rule
then has zero expected P&L before costs and negative expectancy after fees and slippage.
(The median log-price drifts down by sigma^2/2 per candle as a consequence.)

Candles: open_t = close_{t-1} (no gaps), close_t = open_t * exp(r_pt),
high_t = max(open, close) * exp(0.5 * sqrt(v_pt) * |z|), low_t = min(open, close) *
exp(-0.5 * sqrt(v_pt) * |z'|). BNB additionally gets a "news wick" on 2 % of candles: low
extended down by a further U(1 %, 3 %).

Volume: log V_pt = log(base_p) + slow_pt + 0.25 z, where slow is an AR(1) with phi = 0.998
(half-life ~350 candles, ~2 months) and stationary sd 0.35. With probability 7 % a candle
is a SPIKE and its volume is multiplied by U(2, 4). Spike flags are drawn independently of
every return.

Planted drift d_pt (the only thing that differs between worlds, all noise is shared):
a TRIGGER is a spike candle s that closes up (close > open) and qualifies for the world;
the next EFFECT_HORIZON = 12 candles s+1..s+12 get d = +0.35 * sigma_p each (expected
+4.2 sigma over the window). Windows do not stack: a new trigger extends the window.

    null      : no trigger ever qualifies (d = 0 everywhere; volume says nothing).
    planted   : every up-closing spike qualifies, over the whole history.
    decay     : only triggers s < split_idx qualify, and no candle t >= split_idx is ever
                drifted (split_idx = int(split_frac * n)), so the last (1 - split_frac) of
                the history is exactly the null world.
    hour_edge : only triggers whose CLOSE hour (UTC) h satisfies 12 <= h <= 20 qualify,
                i.e. the 4H candles opening 08:00, 12:00 and 16:00 (closing 12:00, 16:00,
                20:00). Spikes closing at 00:00, 04:00 or 08:00 trigger nothing, although
                a window opened by an earlier edge-hour trigger keeps running through them.

A long entered at the next open after a qualifying trigger therefore has a genuine edge.

Caveat on magnitude: with these parameters roughly 7 % x 1/2 x 12 of all candles sit in a
drift window (~36 % in "planted", ~20 % in "hour_edge"), so the planted worlds also trend
up strongly UNCONDITIONALLY (planted BTC gains ~+21 log units over 6 years). Any long-biased
rule profits there; "planted" vs "null" shows the pipeline can find an edge, while only the
hour_edge / decay contrasts test whether it finds the RIGHT (conditional, persistent) edge.

Synthetic news (identical in every world, NO price impact; they exercise R5 only), all with
note "synthetic": 1-3 (mean 2) high-impact "macro" events scoped "ALL" per calendar month;
one high-impact "regulatory" event scoped "EXCHANGE:binance" per quarter; one medium "bnb_burn"
(scope "BNB") per quarter, 9-20 days into the quarter; one medium "launchpool" (scope "BNB")
per month; one high-impact "unlock" scoped "ETH" per calendar year.

Determinism: every random draw comes from ``random.Random`` instances seeded with strings
derived from (seed, stream name), so the same seed gives identical output across runs and
Python processes, a pair's path does not depend on which other pairs are generated, and
all four worlds share the same noise for a given seed (only the planted drift differs).
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from .models import DAY_MS, HOUR_MS, Candle, NewsEvent, base_of


WORLDS = ("null", "planted", "decay", "hour_edge")
DEFAULT_PAIRS = ("BTC/USDT", "ETH/USDT", "BNB/USDT")

TIMEFRAME_MS = 4 * HOUR_MS
CANDLES_PER_YEAR = 365 * 24 * HOUR_MS // TIMEFRAME_MS  # 2190
START_TS = 1_546_300_800_000  # 2019-01-01T00:00:00Z

# Ground truth of the planted effect.
EFFECT_HORIZON = 12  # candles of extra drift after a qualifying trigger
EFFECT_MU_SIGMAS = 0.35  # extra drift per candle, in units of the pair's sigma
HOUR_EDGE_CLOSE_HOURS = (12, 20)  # inclusive window on the trigger candle's UTC close hour

# Volume model.
SPIKE_PROB = 0.07
SPIKE_MULT = (2.0, 4.0)
VOL_NOISE_SD = 0.25
VOL_SLOW_SD = 0.35
VOL_SLOW_PHI = 0.998

# Candle shape.
WICK_SCALE = 0.5
NEWS_WICK_RANGE = (0.01, 0.03)

# Two-state scale mixture with E[k^2] = 1.
_MIX_HI_PROB = 0.1
_MIX_RATIO = 2.5
_K_LO = 1.0 / math.sqrt(1.0 - _MIX_HI_PROB + _MIX_HI_PROB * _MIX_RATIO**2)
_K_HI = _MIX_RATIO * _K_LO

_MINUTE_MS = 60_000
_NOTE = "synthetic"


@dataclass(frozen=True, slots=True)
class PairSpec:
    start_price: float
    sigma: float  # per-candle standard deviation of log returns
    factor_share: float  # fraction of return variance explained by the common factor
    base_volume: float  # median baseline volume per candle (base-asset units)
    news_wick_prob: float = 0.0  # probability of an extra 1-3 % downside wick


PAIR_SPECS: dict[str, PairSpec] = {
    "BTC": PairSpec(start_price=4000.0, sigma=0.012, factor_share=0.80, base_volume=3_000.0),
    "ETH": PairSpec(start_price=150.0, sigma=0.015, factor_share=0.70, base_volume=60_000.0),
    "BNB": PairSpec(
        start_price=6.0, sigma=0.014, factor_share=0.55, base_volume=500_000.0, news_wick_prob=0.02
    ),
}
DEFAULT_SPEC = PairSpec(start_price=100.0, sigma=0.015, factor_share=0.60, base_volume=100_000.0)


@dataclass(frozen=True, slots=True)
class PairTruth:
    """What the generator actually did for one pair (for verification, never for trading)."""

    spike: tuple[bool, ...]  # candle t is a volume-spike candle
    drift: tuple[float, ...]  # planted extra log drift applied to candle t (0.0 or mu)
    triggers: tuple[int, ...]  # indices of spike candles that switched the drift on
    mu: float  # EFFECT_MU_SIGMAS * sigma for this pair


@dataclass(frozen=True, slots=True)
class SyntheticWorld:
    world: str
    seed: int
    split_idx: int  # decay: first candle index without any planted drift
    candles: dict[str, list[Candle]]
    events: list[NewsEvent]
    truth: dict[str, PairTruth]

    @property
    def split_ts(self) -> int:
        return START_TS + self.split_idx * TIMEFRAME_MS


# ---------------------------------------------------------------------- ground-truth rule
def close_hour(idx: int) -> int:
    """UTC hour at which synthetic candle ``idx`` closes."""
    return (START_TS + (idx + 1) * TIMEFRAME_MS) // HOUR_MS % 24


def trigger_qualifies(world: str, idx: int, split_idx: int) -> bool:
    """True if an up-closing spike at candle ``idx`` switches the planted drift on."""
    if world == "planted":
        return True
    if world == "decay":
        return idx < split_idx
    if world == "hour_edge":
        lo, hi = HOUR_EDGE_CLOSE_HOURS
        return lo <= close_hour(idx) <= hi
    return False


# ---------------------------------------------------------------------- random streams
def _rng(seed: int, stream: str) -> random.Random:
    # str seeds are hashed with SHA-512 by random.Random: stable across processes.
    return random.Random(f"trendbot.synthetic:{seed}:{stream}")


def _scale(rng: random.Random) -> float:
    return _K_HI if rng.random() < _MIX_HI_PROB else _K_LO


def _factor_path(seed: int, n: int) -> tuple[list[float], list[float]]:
    """Common factor shocks k*z and their conditional variances k^2."""
    rng = _rng(seed, "factor")
    shocks: list[float] = []
    variances: list[float] = []
    for _ in range(n):
        k = _scale(rng)
        shocks.append(k * rng.gauss(0.0, 1.0))
        variances.append(k * k)
    return shocks, variances


@dataclass(slots=True)
class _PairNoise:
    shock: list[float]
    var: list[float]
    spike: list[bool]
    volume: list[float]
    wick_up: list[float]
    wick_dn: list[float]
    news_wick: list[float]


def _pair_noise(seed: int, pair: str, spec: PairSpec, n: int) -> _PairNoise:
    """Every world-independent random draw for one pair, drawn in a fixed order."""
    rng = _rng(seed, f"pair:{pair}")
    gauss, rand, uniform = rng.gauss, rng.random, rng.uniform
    noise = _PairNoise([], [], [], [], [], [], [])
    log_base = math.log(spec.base_volume)
    slow = gauss(0.0, VOL_SLOW_SD)
    innov_sd = VOL_SLOW_SD * math.sqrt(1.0 - VOL_SLOW_PHI**2)
    for _ in range(n):
        k = _scale(rng)
        noise.shock.append(k * gauss(0.0, 1.0))
        noise.var.append(k * k)
        slow = VOL_SLOW_PHI * slow + innov_sd * gauss(0.0, 1.0)
        volume = math.exp(log_base + slow + VOL_NOISE_SD * gauss(0.0, 1.0))
        is_spike = rand() < SPIKE_PROB
        mult = uniform(*SPIKE_MULT) if is_spike else 1.0
        noise.spike.append(is_spike)
        noise.volume.append(volume * mult)
        noise.wick_up.append(abs(gauss(0.0, 1.0)))
        noise.wick_dn.append(abs(gauss(0.0, 1.0)))
        wick = rand() < spec.news_wick_prob
        noise.news_wick.append(uniform(*NEWS_WICK_RANGE) if wick else 0.0)
    return noise


# ---------------------------------------------------------------------- price paths
def _simulate_pair(
    spec: PairSpec,
    noise: _PairNoise,
    factor: tuple[list[float], list[float]],
    qualifies: Callable[[int], bool],
    drift_end: int,
) -> tuple[list[Candle], PairTruth]:
    f_shock, f_var = factor
    beta = spec.sigma * math.sqrt(spec.factor_share)
    idio = spec.sigma * math.sqrt(1.0 - spec.factor_share)
    beta2, idio2 = beta * beta, idio * idio
    mu = EFFECT_MU_SIGMAS * spec.sigma
    exp, sqrt = math.exp, math.sqrt
    candles: list[Candle] = []
    drift: list[float] = []
    triggers: list[int] = []
    active_until = -1
    price = spec.start_price
    for t in range(len(noise.shock)):
        v = beta2 * f_var[t] + idio2 * noise.var[t]
        d = mu if t <= active_until and t < drift_end else 0.0
        o = price
        c = o * exp(-0.5 * v + d + beta * f_shock[t] + idio * noise.shock[t])
        sd = sqrt(v)
        hi = max(o, c) * exp(WICK_SCALE * sd * noise.wick_up[t])
        lo = min(o, c) * exp(-WICK_SCALE * sd * noise.wick_dn[t]) * (1.0 - noise.news_wick[t])
        candles.append(Candle(START_TS + t * TIMEFRAME_MS, o, hi, lo, c, noise.volume[t]))
        drift.append(d)
        if noise.spike[t] and c > o and qualifies(t):
            active_until = t + EFFECT_HORIZON
            triggers.append(t)
        price = c
    truth = PairTruth(tuple(noise.spike), tuple(drift), tuple(triggers), mu)
    return candles, truth


# ---------------------------------------------------------------------- news events
def _add_months(dt: datetime, k: int) -> datetime:
    y, m = divmod(dt.month - 1 + k, 12)
    return dt.replace(year=dt.year + y, month=m + 1, day=1)


def _ms(dt: datetime) -> int:
    return int(dt.timestamp()) * 1000


def _uniform_ts(rng: random.Random, lo: int, hi: int) -> int:
    ts = lo + int(rng.random() * (hi - lo))
    return ts - ts % _MINUTE_MS


def make_events(seed: int, start_ts: int, end_ts: int) -> list[NewsEvent]:
    """Synthetic news calendar over ``[start_ts, end_ts)``, sorted by time."""
    rng = _rng(seed, "events")
    start = datetime.fromtimestamp(start_ts // 1000, tz=UTC)
    first = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    events: list[NewsEvent] = []
    k = 0
    while _ms(_add_months(first, k)) < end_ts:
        m0 = _add_months(first, k)
        lo, hi = _ms(m0), _ms(_add_months(first, k + 1))
        for _ in range(rng.choice((1, 2, 2, 3))):
            events.append(NewsEvent(_uniform_ts(rng, lo, hi), "ALL", "high", "macro", _NOTE))
        events.append(NewsEvent(_uniform_ts(rng, lo, hi), "BNB", "medium", "launchpool", _NOTE))
        if m0.month in (1, 4, 7, 10):
            q_hi = _ms(_add_months(first, k + 3))
            ts = _uniform_ts(rng, lo, q_hi)
            events.append(NewsEvent(ts, "EXCHANGE:binance", "high", "regulatory", _NOTE))
            ts = _uniform_ts(rng, lo + 9 * DAY_MS, lo + 20 * DAY_MS)
            events.append(NewsEvent(ts, "BNB", "medium", "bnb_burn", _NOTE))
        if m0.month == 1:
            ts = _uniform_ts(rng, lo, _ms(_add_months(first, k + 12)))
            events.append(NewsEvent(ts, "ETH", "high", "unlock", _NOTE))
        k += 1
    kept = [e for e in events if start_ts <= e.ts < end_ts]
    return sorted(kept, key=lambda e: (e.ts, e.scope, e.kind))


# ---------------------------------------------------------------------- public API
def _check_args(world: str, years: float, pairs: Sequence[str], split_frac: float) -> int:
    if world not in WORLDS:
        raise ValueError(f"unknown world {world!r}; choose one of {WORLDS}")
    if not 0.0 < split_frac < 1.0:
        raise ValueError(f"split_frac must be in (0, 1), got {split_frac}")
    if not pairs or len(set(pairs)) != len(pairs):
        raise ValueError(f"pairs must be non-empty and unique, got {pairs!r}")
    n = round(years * CANDLES_PER_YEAR)
    if n < 1:
        raise ValueError(f"years={years} gives no candles")
    return n


def generate_world(
    world: str,
    seed: int,
    years: float = 6.0,
    pairs: Sequence[str] = DEFAULT_PAIRS,
    split_frac: float = 0.7,
) -> SyntheticWorld:
    """Like ``make_world`` but also returns the ground truth (spikes, drift, triggers)."""
    n = _check_args(world, years, pairs, split_frac)
    split_idx = int(split_frac * n)
    factor = _factor_path(seed, n)
    drift_end = split_idx if world == "decay" else n

    def qualifies(idx: int) -> bool:
        return trigger_qualifies(world, idx, split_idx)

    candles: dict[str, list[Candle]] = {}
    truth: dict[str, PairTruth] = {}
    for pair in pairs:
        spec = PAIR_SPECS.get(base_of(pair), DEFAULT_SPEC)
        noise = _pair_noise(seed, pair, spec, n)
        candles[pair], truth[pair] = _simulate_pair(spec, noise, factor, qualifies, drift_end)
    events = make_events(seed, START_TS, START_TS + n * TIMEFRAME_MS)
    return SyntheticWorld(world, seed, split_idx, candles, events, truth)


def make_world(
    world: str,
    seed: int,
    years: float = 6.0,
    pairs: Sequence[str] = DEFAULT_PAIRS,
    split_frac: float = 0.7,
) -> tuple[dict[str, list[Candle]], list[NewsEvent]]:
    """Generate one synthetic world: ``(candles per pair, sorted news events)``.

    ``years`` of 4H candles (2190 per year; 6 years = 13,140 per pair) from 2019-01-01 UTC.
    See the module docstring for the exact generative model of each world.
    """
    w = generate_world(world, seed, years, pairs, split_frac)
    return w.candles, w.events
