"""Tests for metrics.py: every statistic is recomputed independently here."""

from __future__ import annotations

import math
import random
from dataclasses import replace

import pytest

from research.trendbot.metrics import (
    LABELS,
    Summary,
    bootstrap_mean_ci,
    dd_check,
    label,
    percentile,
    summarize,
)
from research.trendbot.models import EXIT_END, EXIT_SL, EXIT_TP, HOUR_MS, Trade


RISK = 100.0
EQ0 = 10_000.0


def _trade(i: int, r: float, hold_h: float = 8.0, exit_reason: str | None = None) -> Trade:
    entry_ts = 1_700_000_000_000 + i * 24 * HOUR_MS
    reason = exit_reason or (EXIT_TP if r > 0 else EXIT_SL)
    return Trade(
        trade_id=i,
        pair="BTC/USDT",
        variant="base",
        signal_ts=entry_ts - 4 * HOUR_MS,
        entry_ts=entry_ts,
        entry_price=100.0,
        stop=99.0,
        target=102.0,
        qty=RISK,
        risk_amount=RISK,
        risk_pct=1.0,
        exit_ts=entry_ts + int(hold_h * HOUR_MS),
        exit_price=100.0 + r,
        exit_reason=reason,
        pnl=r * RISK,
        r_multiple=r,
    )


def _trades(rs: list[float], holds: list[float] | None = None) -> list[Trade]:
    holds = holds or [8.0] * len(rs)
    return [_trade(i, r, h) for i, (r, h) in enumerate(zip(rs, holds, strict=True))]


R_SAMPLE = [2.0, -1.0, -1.0, 2.0, -1.05, 1.9]
HOLDS = [8.0, 4.0, 12.0, 20.0, 4.0, 16.0]


def test_summary_statistics_recomputed_by_hand() -> None:
    trades = _trades(R_SAMPLE, HOLDS)
    random.Random(3).shuffle(trades)  # summarize must re-order by exit_ts itself
    s = summarize(trades, EQ0, seed=11, n_boot=500)

    n = len(R_SAMPLE)
    mean = sum(R_SAMPLE) / n
    var = sum((r - mean) ** 2 for r in R_SAMPLE) / (n - 1)
    assert s.n == 6
    assert s.wins == 3
    assert s.win_rate == pytest.approx(0.5)
    assert s.avg_r == pytest.approx(2.85 / 6)
    assert s.avg_r == pytest.approx(mean)
    assert s.median_r == pytest.approx((-1.0 + 1.9) / 2)  # sorted: -1.05 -1 -1 1.9 2 2
    assert s.total_r == pytest.approx(2.85)
    assert s.profit_factor == pytest.approx((200 + 200 + 190) / (100 + 100 + 105))
    # cumulative R: 2, 1, 0, 2, 0.95, 2.85 -> worst drop is 2 -> 0
    assert s.max_dd_r == pytest.approx(2.0)
    # equity: 10200, 10100, 10000, 10200, 10095, 10285 -> worst drop 10200 -> 10000
    assert s.max_dd_pct == pytest.approx(200 / 10_200 * 100)
    assert s.t_stat == pytest.approx(mean / math.sqrt(var / n))
    assert s.exit_counts == {EXIT_SL: 3, EXIT_TP: 3}
    assert s.avg_hold_h == pytest.approx(sum(HOLDS) / n)
    assert s.ci90_low <= s.avg_r <= s.ci90_high


def test_bootstrap_matches_documented_algorithm() -> None:
    rs = R_SAMPLE
    rng = random.Random(11)
    means = sorted(sum(rng.choices(rs, k=len(rs))) / len(rs) for _ in range(500))

    def interp(q: float) -> float:
        pos = q * (len(means) - 1)
        lo = int(pos)
        return means[lo] + (means[min(lo + 1, len(means) - 1)] - means[lo]) * (pos - lo)

    s = summarize(_trades(rs), EQ0, seed=11, n_boot=500)
    assert s.ci90_low == pytest.approx(interp(0.05))
    assert s.ci90_high == pytest.approx(interp(0.95))


def test_bootstrap_deterministic_and_contains_mean() -> None:
    rng = random.Random(5)
    rs = [rng.gauss(0.2, 1.3) for _ in range(200)]  # continuous: seeds give distinct CIs
    a = summarize(_trades(rs), EQ0, seed=7)
    b = summarize(_trades(rs), EQ0, seed=7)
    c = summarize(_trades(rs), EQ0, seed=8)
    assert (a.ci90_low, a.ci90_high) == (b.ci90_low, b.ci90_high)
    assert (a.ci90_low, a.ci90_high) != (c.ci90_low, c.ci90_high)
    assert a.ci90_low < a.avg_r < a.ci90_high
    # A percentile bootstrap of the mean is close to the normal-theory 90 % interval.
    n = len(rs)
    mean = sum(rs) / n
    se = math.sqrt(sum((r - mean) ** 2 for r in rs) / n / n)
    assert a.ci90_low == pytest.approx(mean - 1.645 * se, abs=0.03)
    assert a.ci90_high == pytest.approx(mean + 1.645 * se, abs=0.03)


def test_bootstrap_helpers() -> None:
    assert bootstrap_mean_ci([], seed=1) == (0.0, 0.0)
    assert bootstrap_mean_ci([0.7], seed=1, n_boot=50) == (0.7, 0.7)
    with pytest.raises(ValueError):
        bootstrap_mean_ci([1.0], n_boot=0)
    assert percentile([0.0, 10.0], 0.25) == pytest.approx(2.5)
    assert percentile([1.0, 2.0, 3.0], 1.0) == 3.0
    with pytest.raises(ValueError):
        percentile([], 0.5)


def test_max_drawdown_known_sequence() -> None:
    # cumulative R: 1, 3, 2, 0, 0.5, -2.5, 1.5 -> peak 3, trough -2.5
    s = summarize(_trades([1.0, 2.0, -1.0, -2.0, 0.5, -3.0, 4.0]), EQ0, n_boot=10)
    assert s.max_dd_r == pytest.approx(5.5)
    # equity peak 10300, trough 9750
    assert s.max_dd_pct == pytest.approx((10_300 - 9_750) / 10_300 * 100)


def test_drawdown_counts_losses_from_the_start() -> None:
    s = summarize(_trades([-1.0, -1.0, 3.0]), EQ0, n_boot=10)
    assert s.max_dd_r == pytest.approx(2.0)
    assert s.max_dd_pct == pytest.approx(200 / EQ0 * 100)


def test_empty_and_open_trades() -> None:
    s = summarize([], EQ0)
    assert s.n == 0 and s.avg_r == 0.0 and s.profit_factor is None
    assert (s.ci90_low, s.ci90_high, s.max_dd_r, s.t_stat) == (0.0, 0.0, 0.0, 0.0)
    assert s.exit_counts == {}

    open_trade = _trade(99, 1.0)
    open_trade.exit_ts = None
    open_trade.r_multiple = None
    open_trade.pnl = None
    s2 = summarize([open_trade, *_trades([1.0, -1.0])], EQ0, n_boot=10)
    assert s2.n == 2


def test_profit_factor_none_without_losses_and_single_trade() -> None:
    s = summarize(_trades([2.0, 1.0]), EQ0, n_boot=10)
    assert s.profit_factor is None
    one = summarize(_trades([-1.0]), EQ0, n_boot=10)
    assert one.profit_factor == 0.0
    assert one.t_stat == 0.0  # n < 2
    flat = summarize(_trades([0.5, 0.5, 0.5]), EQ0, n_boot=10)
    assert flat.t_stat == 0.0  # stdev 0


def test_pnl_fallback_and_exit_counts() -> None:
    t = _trade(0, -1.0, exit_reason=EXIT_END)
    t.pnl = None  # rebuilt as r_multiple * risk_amount
    s = summarize([t, _trade(1, 2.0)], EQ0, n_boot=10)
    assert s.profit_factor == pytest.approx(2.0)
    assert s.exit_counts == {EXIT_END: 1, EXIT_TP: 1}


def test_invalid_arguments() -> None:
    with pytest.raises(ValueError):
        summarize([], 0.0)
    with pytest.raises(ValueError):
        summarize(_trades([1.0]), EQ0, n_boot=0)


# ------------------------------------------------------------------ labels
def _s(n: int, avg_r: float, ci_low: float = 0.1, ci_high: float = 0.5, dd: float = 5.0) -> Summary:
    return Summary(
        n=n,
        wins=n // 2,
        win_rate=0.5 if n else 0.0,
        avg_r=avg_r,
        median_r=avg_r,
        total_r=avg_r * n,
        profit_factor=1.5,
        max_dd_r=3.0,
        max_dd_pct=dd,
        t_stat=2.0,
        ci90_low=ci_low,
        ci90_high=ci_high,
        exit_counts={},
        avg_hold_h=12.0,
    )


@pytest.mark.parametrize(
    ("train", "test", "expected", "fragment"),
    [
        (_s(10, 0.5), _s(50, 0.5), "UNTESTED", "train n=10"),
        (_s(50, 0.5), _s(29, 0.5), "UNTESTED", "test n=29"),
        (_s(10, -0.5), _s(50, -0.5), "UNTESTED", "Too few"),  # sample size checked first
        (_s(50, 0.0), _s(50, 0.5), "NO-EDGE", "+0.000R"),
        (_s(50, -0.2), _s(50, 0.5), "NO-EDGE", "-0.200R"),
        (_s(50, 0.3), _s(50, 0.0), "TRAIN-ONLY", "+0.000R"),
        (_s(50, 0.3), _s(50, -0.1), "TRAIN-ONLY", "-0.100R"),
        (_s(50, 0.3), _s(50, 0.2, ci_low=-0.05), "UNTESTED", "-0.050"),
        (_s(50, 0.3), _s(50, 0.2, ci_low=0.0), "UNTESTED", "includes zero"),
        (_s(50, 0.3), _s(50, 0.2, ci_low=0.01), "ROBUST", "+0.010"),
    ],
)
def test_label_branches(train: Summary, test: Summary, expected: str, fragment: str) -> None:
    got, reason = label(train, test)
    assert got == expected
    assert got in LABELS
    assert fragment in reason
    assert reason.endswith(".") and reason.count(". ") == 0  # one sentence


def test_label_thresholds_are_parameters() -> None:
    train, test = _s(20, 0.3), _s(20, 0.2, ci_low=0.05)
    assert label(train, test)[0] == "UNTESTED"
    assert label(train, test, min_train=20, min_test=20)[0] == "ROBUST"


def test_dd_check() -> None:
    ok, reason = dd_check(_s(40, 0.2, dd=12.5), 20.0)
    assert ok and "12.50%" in reason and "20.00%" in reason
    bad, reason = dd_check(_s(40, 0.2, dd=25.0), 20.0)
    assert not bad and "exceeds" in reason
    assert dd_check(_s(40, 0.2, dd=20.0), 20.0)[0]  # boundary is allowed
    empty_ok, reason = dd_check(summarize([], EQ0), 20.0)
    assert empty_ok and "No test trades" in reason


def test_summary_is_frozen() -> None:
    s = _s(1, 0.1)
    with pytest.raises(AttributeError):
        s.n = 2  # type: ignore[misc]
    assert replace(s, n=2).n == 2
