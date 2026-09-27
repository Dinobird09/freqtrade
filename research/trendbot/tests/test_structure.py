import math
import random
from types import MappingProxyType

import pytest

from research.trendbot.config import ConfigError, PairRisk, StrategyConfig
from research.trendbot.models import HOUR_MS, Candle
from research.trendbot.structure import find_stop, latest_confirmed_pivot


CFG = StrategyConfig()
T0 = 1_704_067_200_000  # 2024-01-01 00:00 UTC
TF = 4 * HOUR_MS


def _build(lows: list[float], close: float = 100.0) -> list[Candle]:
    """Valid candles with the given lows; open = close = max(close, low), high = that + 1."""
    out = []
    for t, low in enumerate(lows):
        c = max(close, low)
        out.append(Candle(T0 + t * TF, c, c + 1.0, low, c, 100.0))
    return out


def _flat_with(n: int, **at: float) -> list[float]:
    lows = [99.0] * n
    for key, value in at.items():
        lows[int(key.lstrip("i"))] = value
    return lows


# Confirmed pivot 98 at index 5; the lowest recent low 95 sits at index 12, and only the
# candles AFTER 12 (97, 98, 99 at 13, 14, 15) turn it into a pivot.
LOOKAHEAD_LOWS = _flat_with(16, i5=98.0, i12=95.0, i13=97.0, i14=98.0, i15=99.0)


# ------------------------------------------------------------------------------ pivot
def test_confirmed_pivot_with_btc_buffer():
    candles = _build(_flat_with(15, i8=97.0))
    plan, reason = find_stop(candles, 14, "BTC/USDT", CFG)
    assert plan is not None
    assert plan.method == "pivot"
    assert plan.structure_level == 97.0
    assert plan.buffer_pct == 0.25
    assert plan.stop == pytest.approx(97.0 * (1 - 0.25 / 100))  # 96.7575
    assert latest_confirmed_pivot(candles, 14, CFG) == 8
    assert "confirmed pivot low 97 of 2024-01-02 08:00 UTC" in reason
    assert "3.24% below close 100" in reason


def test_most_recent_pivot_wins():
    candles = _build(_flat_with(15, i5=96.0, i10=97.5))
    plan, _ = find_stop(candles, 14, "ETH/USDT", CFG)
    assert (plan.method, plan.structure_level) == ("pivot", 97.5)


def test_pivot_at_or_above_close_is_skipped():
    lows = _flat_with(20, i4=95.0)
    lows[10:15] = [103.0, 102.0, 101.0, 102.0, 103.0]  # pivot 101 at index 12 > close 100
    lows[15:20] = [99.5] * 5
    candles = _build(lows)
    assert candles[12].low > candles[19].close
    plan, _ = find_stop(candles, 19, "BTC/USDT", CFG)
    assert (plan.method, plan.structure_level) == ("pivot", 95.0)
    assert latest_confirmed_pivot(candles, 19, CFG) == 4


def test_equal_lows_are_not_a_pivot():
    candles = _build(_flat_with(15, i8=97.0, i9=97.0))  # double bottom: strict < fails
    plan, _ = find_stop(candles, 14, "BTC/USDT", CFG)
    assert (plan.method, plan.structure_level) == ("lookback_low", 97.0)


def test_pivot_needs_a_full_left_side():
    # index 0 has no left neighbours; negative indexing must not make it a pivot.
    candles = _build([97.0, 99.0, 99.0, 99.0, 99.0, 99.0])
    assert latest_confirmed_pivot(candles, 5, CFG) is None
    plan, _ = find_stop(candles, 5, "BTC/USDT", CFG)
    assert (plan.method, plan.structure_level) == ("lookback_low", 97.0)


# ------------------------------------------------------------------------------ look-ahead
def test_unconfirmed_pivot_is_not_used():
    candles = _build(LOOKAHEAD_LOWS)
    i = 13  # low at i-1 = 95 is the lowest recent low, confirmed only by candle 14
    plan, reason = find_stop(candles, i, "BTC/USDT", CFG)
    assert (plan.method, plan.structure_level) == ("pivot", 98.0)
    assert latest_confirmed_pivot(candles, i, CFG) == 5
    # Identical answer when the future candles do not exist at all.
    assert find_stop(candles[: i + 1], i, "BTC/USDT", CFG) == (plan, reason)
    # One candle later the same pivot is confirmed (j + k == i) and becomes the structure.
    plan2, _ = find_stop(candles, i + 1, "BTC/USDT", CFG)
    assert (plan2.method, plan2.structure_level) == ("pivot", 95.0)
    assert latest_confirmed_pivot(candles, i + 1, CFG) == 12


def test_unconfirmed_low_only_reachable_through_the_fallback():
    lows = list(LOOKAHEAD_LOWS)
    lows[5] = 99.0  # no earlier confirmed pivot
    candles = _build(lows)
    plan, _ = find_stop(candles, 13, "BTC/USDT", CFG)
    # 95 at i-1 is already KNOWN at the close of i, so the lookback low may use it, but it
    # is reported as a lookback low, not as a (not yet confirmed) pivot.
    assert (plan.method, plan.structure_level) == ("lookback_low", 95.0)
    assert find_stop(candles[:14], 13, "BTC/USDT", CFG)[0] == plan
    plan2, _ = find_stop(candles, 14, "BTC/USDT", CFG)
    assert (plan2.method, plan2.structure_level) == ("pivot", 95.0)


@pytest.mark.parametrize("k", [1, 2, 3])
def test_pivot_becomes_usable_exactly_k_candles_later(k):
    cfg = CFG.with_changes(swing_pivot_k=k)
    candles = _build(LOOKAHEAD_LOWS)
    before, _ = find_stop(candles, 12 + k - 1, "BTC/USDT", cfg)
    at, _ = find_stop(candles, 12 + k, "BTC/USDT", cfg)
    assert (before.method, before.structure_level) == ("pivot", 98.0)
    assert (at.method, at.structure_level) == ("pivot", 95.0)


def test_find_stop_never_reads_candles_after_i():
    rng = random.Random(3)
    candles, price = [], 100.0
    for t in range(300):
        opn = price
        price *= 1.0 + rng.gauss(0.0, 0.02)
        low = min(opn, price) * (1.0 - rng.random() * 0.015)
        high = max(opn, price) * (1.0 + rng.random() * 0.015)
        candles.append(Candle(T0 + t * TF, opn, high, low, price, 1.0))
    methods = set()
    for pair in ("BTC/USDT", "BNB/USDT"):
        for i in range(len(candles)):
            full = find_stop(candles, i, pair, CFG)
            assert find_stop(candles[: i + 1], i, pair, CFG) == full
            j = latest_confirmed_pivot(candles, i, CFG)
            if j is not None:
                assert j + CFG.swing_pivot_k <= i
                assert i - CFG.swing_lookback <= j
                assert candles[j].low < candles[i].close
            if full[0] is not None:
                methods.add(full[0].method)
                assert full[0].stop < candles[i].close
    assert methods == {"pivot", "lookback_low"}  # both code paths exercised


# ------------------------------------------------------------------------------ windows
def test_swing_lookback_boundary():
    cfg = CFG.with_changes(swing_lookback=6, fallback_lookback=3)
    inside = _build(_flat_with(14, i7=97.0))  # j = i - 6
    plan, _ = find_stop(inside, 13, "BTC/USDT", cfg)
    assert (plan.method, plan.structure_level) == ("pivot", 97.0)
    outside = _build(_flat_with(14, i6=97.0))  # j = i - 7
    plan, reason = find_stop(outside, 13, "BTC/USDT", cfg)
    assert (plan.method, plan.structure_level) == ("lookback_low", 99.0)
    assert "no confirmed pivot in the last 6 candles" in reason


def test_fallback_window_is_last_n_candles_including_i():
    lows = [99.0] * 10 + [95.0, 95.0]  # 95s at i-11 and i-10: just outside, not pivots
    lows += [99.0, 98.9, 98.8, 98.7, 98.6, 98.5, 98.4, 98.3, 98.2, 98.1]  # falling, no pivot
    candles = _build(lows)
    i = len(candles) - 1
    plan, _ = find_stop(candles, i, "BTC/USDT", CFG)
    assert (plan.method, plan.structure_level) == ("lookback_low", 98.1)  # candle i itself
    wider, _ = find_stop(candles, i, "BTC/USDT", CFG.with_changes(fallback_lookback=12))
    assert (wider.method, wider.structure_level) == ("lookback_low", 95.0)


# ------------------------------------------------------------------------------ buffers
def test_bnb_stop_sits_further_below_the_same_structure():
    candles = _build(_flat_with(15, i8=97.0))
    btc, _ = find_stop(candles, 14, "BTC/USDT", CFG)
    eth, _ = find_stop(candles, 14, "ETH/USDT", CFG)
    bnb, _ = find_stop(candles, 14, "BNB/USDT", CFG)
    assert btc == eth
    assert bnb.structure_level == btc.structure_level == 97.0
    assert (btc.buffer_pct, bnb.buffer_pct) == (0.25, 0.65)
    assert bnb.stop == pytest.approx(97.0 * (1 - 0.65 / 100))  # 96.3695
    assert bnb.stop < btc.stop


def test_buffer_comes_from_config():
    risk = dict(CFG.pair_risk)
    risk["BNB"] = PairRisk(max_risk_pct=0.5, stop_buffer_pct=0.8)
    cfg = CFG.with_changes(pair_risk=MappingProxyType(risk))
    plan, _ = find_stop(_build(_flat_with(15, i8=97.0)), 14, "BNB/USDT", cfg)
    assert plan.buffer_pct == 0.8
    assert plan.stop == pytest.approx(97.0 * 0.992)


# ------------------------------------------------------------------------------ rejection
def test_too_tight_stop_rejected_and_bnb_buffer_can_widen_it():
    lows = [100.01] * 15
    lows[8] = 100.0
    candles = _build(lows, close=100.02)
    plan, reason = find_stop(candles, 14, "BTC/USDT", CFG)  # stop 99.75, 0.27% below
    assert plan is None
    assert reason.startswith("stop 99.75 is only 0.27% below close 100.02, tighter than the")
    assert "0.3% minimum" in reason
    bnb, _ = find_stop(candles, 14, "BNB/USDT", CFG)  # stop 99.35, 0.67% below
    assert bnb is not None
    assert bnb.structure_level == 100.0


def test_too_wide_stop_rejected():
    candles = _build(_flat_with(15, i8=85.0))
    plan, reason = find_stop(candles, 14, "BTC/USDT", CFG)  # 84.7875 -> 15.21% below
    assert plan is None
    assert "15.21% below close 100, wider than the 10% maximum" in reason
    assert "confirmed pivot low 85" in reason
    loose, _ = find_stop(candles, 14, "BTC/USDT", CFG.with_changes(max_stop_distance_pct=20.0))
    assert loose is not None


def test_distance_bounds_are_inclusive():
    candles = _build(_flat_with(15, i8=97.0))
    plan, _ = find_stop(candles, 14, "BTC/USDT", CFG)
    close = candles[14].close
    d = (close - plan.stop) / close * 100.0

    def run(**changes):
        return find_stop(candles, 14, "BTC/USDT", CFG.with_changes(**changes))[0]

    assert run(min_stop_distance_pct=d) == plan
    assert run(max_stop_distance_pct=d) == plan
    assert run(min_stop_distance_pct=math.nextafter(d, math.inf)) is None
    assert run(max_stop_distance_pct=math.nextafter(d, -math.inf)) is None


def test_stop_not_below_close_rejected():
    # Malformed candles (low above close) must never yield a stop at/above the entry price.
    candles = [Candle(T0 + t * TF, 102.0, 103.0, 101.0, 100.0, 1.0) for t in range(15)]
    plan, reason = find_stop(candles, 14, "BTC/USDT", CFG)
    assert plan is None
    assert reason.startswith("stop 100.7475 is not below close 100")


def test_bad_arguments():
    candles = _build(_flat_with(15, i8=97.0))
    for i in (-1, 15):
        with pytest.raises(ValueError):
            find_stop(candles, i, "BTC/USDT", CFG)
    with pytest.raises(ConfigError):
        find_stop(candles, 14, "SOL/USDT", CFG)
    with pytest.raises(ValueError):
        find_stop(candles, 14, "BTC/USDT", CFG.with_changes(swing_pivot_k=0))
