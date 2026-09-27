"""Tests for metrics.py: every statistic is recomputed independently here."""

from __future__ import annotations

import math
import random
from dataclasses import replace

import pytest

from research.trendbot import metrics
from research.trendbot.metrics import (
    BREAKEVEN_WIN_P,
    DD_CAP_PCT,
    DD_CAP_RATIONALE,
    DD_CAP_STREAK_P,
    LABELS,
    N_BOOT,
    NOT_DISTINGUISHABLE,
    ROBUST_MIN_N,
    Summary,
    block_bootstrap_means,
    bootstrap_mean_ci,
    dd_check,
    dd_limit,
    label,
    lb_confidence,
    month_blocks,
    mtm_equity_curve,
    mtm_max_dd_pct,
    percentile,
    summarize,
    train_dd_quantile,
)
from research.trendbot.models import DAY_MS, EXIT_END, EXIT_SL, EXIT_TP, HOUR_MS, Candle, Trade


RISK = 100.0
EQ0 = 10_000.0


def _trade(
    i: int, r: float, hold_h: float = 8.0, exit_reason: str | None = None, start: int = 0
) -> Trade:
    entry_ts = (start or 1_700_000_000_000) + i * 24 * HOUR_MS
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


# ------------------------------------------------------------------ C4 bootstraps
JAN_2024 = 1_704_067_200_000  # 2024-01-01T00:00:00Z


def test_default_resamples_and_confidence() -> None:
    s = summarize(_trades([1.0, -1.0, 2.0]), EQ0)
    assert s.n_boot == N_BOOT >= 4000
    assert s.lb_confidence == pytest.approx(1 - 0.05 / 4) == lb_confidence()
    assert summarize(_trades([1.0]), EQ0, m=1, alpha=0.1).lb_confidence == pytest.approx(0.9)
    for bad in ({"m": 0}, {"m": 1.5}, {"alpha": 0.0}, {"alpha": 1.0}):
        with pytest.raises(ValueError):
            summarize(_trades([1.0]), EQ0, **bad)  # type: ignore[arg-type]


def test_iid_bounds_follow_the_documented_algorithm() -> None:
    rng = random.Random(4)
    rs = [rng.choice([2.0, -1.0, -1.0, 0.3]) for _ in range(45)]
    s = summarize(_trades(rs), EQ0, seed=5, n_boot=800)
    boot = random.Random(5)
    means = sorted(sum(boot.choices(rs, k=len(rs))) / len(rs) for _ in range(800))
    assert s.ci90_low == pytest.approx(percentile(means, 0.05))
    assert s.ci90_high == pytest.approx(percentile(means, 0.95))
    assert s.iid_lb == pytest.approx(percentile(means, 0.05 / 4))
    assert s.iid_lb <= s.ci90_low  # 98.75% one-sided is wider than the 90% two-sided


def test_month_blocks_group_by_the_utc_month_of_the_exit() -> None:
    trades = [
        _trade(0, 1.0, start=JAN_2024),  # exits 2024-01-01
        _trade(30, -1.0, start=JAN_2024),  # exits 2024-01-31
        _trade(31, 2.0, start=JAN_2024, hold_h=8.0),  # entry 2024-02-01
        _trade(95, 0.5, start=JAN_2024),  # April (skips March: no empty blocks)
    ]
    random.Random(1).shuffle(trades)
    assert month_blocks(trades) == [(0.0, 2), (2.0, 1), (0.5, 1)]
    blocks = month_blocks(trades)
    rng = random.Random(9 + 1)  # the block bootstrap uses Random(seed + 1)
    expected = []
    for _ in range(300):
        drawn = rng.choices(blocks, k=len(blocks))
        expected.append(sum(b[0] for b in drawn) / sum(b[1] for b in drawn))
    assert block_bootstrap_means(blocks, seed=9, n_boot=300) == sorted(expected)
    s = summarize(trades, EQ0, seed=9, n_boot=300)
    assert s.n_blocks == 3
    assert s.block_lb == pytest.approx(percentile(sorted(expected), 0.0125))
    assert s.adj_lb == min(s.iid_lb, s.block_lb)
    assert block_bootstrap_means([], n_boot=10) == []


def _clustered_test_trades() -> list[Trade]:
    """60 TEST trades in 3 calendar months: two months of +1R, one month of -0.8R.

    Mean +0.4R and a small iid standard error (the iid bootstrap sees 60 independent
    trades), but only 3 independent blocks: a block resample draws the bad month three times
    with probability 1/27 = 3.7% > 1.25%, so the block lower bound is -0.8R.
    """
    out = []
    month_starts = (JAN_2024, JAN_2024 + 31 * DAY_MS, JAN_2024 + 60 * DAY_MS)  # Jan, Feb, Mar
    for m, (start, r) in enumerate(zip(month_starts, (1.0, 1.0, -0.8), strict=True)):
        for k in range(20):
            t = _trade(m * 20 + k, r, start=start)
            t.entry_ts = start + k * DAY_MS
            t.exit_ts = t.entry_ts + 8 * HOUR_MS
            out.append(t)
    return out


def test_iid_passes_but_calendar_month_block_bootstrap_fails() -> None:
    test = summarize(_clustered_test_trades(), EQ0)
    train = summarize(_trades([2.0, -1.0, 0.5] * 12), EQ0)
    assert test.n == 60 and test.n_blocks == 3 and test.avg_r == pytest.approx(0.4)
    assert test.iid_lb > 0.1  # the iid bound alone would say ROBUST ...
    assert test.block_lb == pytest.approx(-0.8)  # ... the block bound does not
    assert test.adj_lb == test.block_lb
    got, reason = label(train, test)
    assert got == "UNTESTED"
    assert NOT_DISTINGUISHABLE in reason and "calendar-month block bootstrap" in reason
    assert reason.endswith(".") and ". " not in reason
    # the same trades spread over 60 months pass both bounds
    spread = _clustered_test_trades()
    for i, t in enumerate(spread):
        t.exit_ts = JAN_2024 + i * 31 * DAY_MS
        t.entry_ts = t.exit_ts - 8 * HOUR_MS
    wide = summarize(spread, EQ0)
    assert wide.n_blocks == 60 and wide.block_lb > 0 and label(train, wide)[0] == "ROBUST"


# ------------------------------------------------------------------ labels
def _s(
    n: int,
    avg_r: float,
    lb: float = 0.1,
    block_lb: float | None = None,
    dd: float = 5.0,
    conf: float = 1 - 0.05 / 4,
) -> Summary:
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
        ci90_low=lb,
        ci90_high=0.5,
        exit_counts={},
        avg_hold_h=12.0,
        n_blocks=12,
        lb_confidence=conf,
        iid_lb=lb,
        block_lb=lb if block_lb is None else block_lb,
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
        (_s(50, 0.3), _s(50, 0.2, lb=-0.05), "UNTESTED", NOT_DISTINGUISHABLE),
        (_s(50, 0.3), _s(50, 0.2, lb=0.0), "UNTESTED", "+0.000R is not above zero"),
        (_s(50, 0.3), _s(50, 0.2, lb=0.05, block_lb=-0.01), "UNTESTED", "-0.010R"),
        (_s(50, 0.3), _s(50, 0.2, lb=-0.01, block_lb=0.05), "UNTESTED", "-0.010R"),
        (_s(50, 0.3), _s(50, 0.2, lb=0.01), "ROBUST", "98.75%"),
    ],
)
def test_label_branches(train: Summary, test: Summary, expected: str, fragment: str) -> None:
    got, reason = label(train, test)
    assert got == expected
    assert got in LABELS
    assert fragment in reason, reason
    assert reason.endswith(".") and reason.count(". ") == 0  # one sentence


def test_robust_needs_30_trades_in_each_window_whatever_min_says() -> None:
    train, test = _s(20, 0.3), _s(20, 0.2, lb=0.05)
    assert label(train, test)[0] == "UNTESTED"
    got, reason = label(train, test, min_train=20, min_test=20)
    assert got == "UNTESTED" and f"at least {ROBUST_MIN_N} trades in each window" in reason
    got, _ = label(_s(30, 0.3), _s(30, 0.2, lb=0.05), min_train=5, min_test=5)
    assert got == "ROBUST"
    # lower minima still let NO-EDGE / TRAIN-ONLY speak for small samples
    assert label(_s(10, -0.1), _s(10, 0.2), min_train=5, min_test=5)[0] == "NO-EDGE"
    assert label(_s(10, 0.1), _s(10, -0.2), min_train=5, min_test=5)[0] == "TRAIN-ONLY"
    # stricter minima are honoured
    assert label(_s(40, 0.3), _s(40, 0.2, lb=0.05), min_test=50)[0] == "UNTESTED"


def test_label_multiplicity_is_fixed_by_m_and_alpha() -> None:
    train = _s(50, 0.3)
    one_look = _s(50, 0.2, lb=0.05, conf=0.95)
    assert label(train, one_look, m=1, alpha=0.05)[0] == "ROBUST"
    with pytest.raises(ValueError, match="same m and alpha"):
        label(train, one_look)  # a 95% bound may not be judged under m = 4
    # more candidates -> a higher confidence -> a lower bound -> fewer ROBUST labels
    rng = random.Random(2)
    rs = [rng.gauss(0.25, 1.2) for _ in range(60)]
    lbs = [summarize(_trades(rs), EQ0, m=m).iid_lb for m in (1, 4, 16)]
    assert lbs[0] > lbs[1] > lbs[2]


# ------------------------------------------------------------------ C5 drawdown
def test_train_dd_quantile_follows_the_documented_algorithm() -> None:
    train = _trades([2.0, -1.0, -1.0, 0.5, -1.0])
    for t, risk in zip(train, (1.0, 0.5, 1.0, 1.0, 0.25), strict=True):
        t.risk_pct = risk
    rets = [2.0 * 0.01, -1.0 * 0.005, -0.01, 0.5 * 0.01, -0.0025]
    rng = random.Random(3)
    dds = []
    for _ in range(200):
        eq = peak = 1.0
        worst = 0.0
        for x in rng.choices(rets, k=7):
            eq *= 1 + x
            peak = max(peak, eq)
            worst = max(worst, (peak - eq) / peak)
        dds.append(worst * 100)
    got = train_dd_quantile(train, 7, n_boot=200, seed=3)
    assert got == pytest.approx(percentile(sorted(dds), 0.95))
    assert train_dd_quantile([], 7) is None and train_dd_quantile(train, 0) is None


def _candles(start: int, closes: list[float]) -> list[Candle]:
    tf = 4 * HOUR_MS
    return [Candle(start + i * tf, c, c + 1, c - 1, c, 1.0) for i, c in enumerate(closes)]


def test_mark_to_market_curve_values_open_positions_at_each_close() -> None:
    tf = 4 * HOUR_MS
    t0 = JAN_2024
    data = {
        "BTC/USDT": _candles(t0, [100, 100, 96, 94, 97, 102, 102]),
        "ETH/USDT": _candles(t0, [50, 50, 50, 50, 50, 50, 50]),
    }
    fee = 0.001
    trade = _trade(1, 2.0)
    trade.entry_ts, trade.exit_ts = t0 + tf, t0 + 5 * tf  # entered at candle 1, exits in 5
    trade.entry_price, trade.qty, trade.pnl = 100.0, 10.0, 17.0
    curve = mtm_equity_curve([trade], data, 10_000.0, fee)
    assert [ts for ts, _ in curve] == [t0 + k * tf for k in range(1, 6)]
    entry_fee = fee * 10 * 100
    expected = [10_000 + 10 * (c - 100) - entry_fee for c in (100, 96, 94, 97)] + [10_017.0]
    assert [eq for _, eq in curve] == pytest.approx(expected)
    dd = mtm_max_dd_pct([trade], data, 10_000.0, fee)
    assert dd == pytest.approx((10_000 - (10_000 - 60 - entry_fee)) / 10_000 * 100)
    # the realised (closed-trade) drawdown of the same winning trade is zero
    assert summarize([trade], 10_000.0, n_boot=10).max_dd_pct == 0.0
    assert mtm_equity_curve([], data, 10_000.0, fee) == []


def test_mark_to_market_realises_exits_and_carries_the_last_close() -> None:
    tf = 4 * HOUR_MS
    t0 = JAN_2024
    btc = _candles(t0, [100, 99, 98, 97, 96])
    del btc[3]  # a data gap: candle 3 missing, candle 2's close is carried
    data = {"BTC/USDT": btc, "ETH/USDT": _candles(t0, [10, 10, 10, 10, 10])}
    a = _trade(1, -1.0)
    a.entry_ts, a.exit_ts, a.entry_price, a.qty, a.pnl = t0, t0 + 4 * tf, 100.0, 1.0, -4.5
    curve = dict(mtm_equity_curve([a], data, 1_000.0, 0.0))
    assert curve[t0 + 3 * tf] == pytest.approx(1_000 - 2.0)  # carried close 98
    assert curve[t0 + 4 * tf] == pytest.approx(1_000 - 4.5)  # realised at the exit candle


def test_dd_check() -> None:
    """C5 as revised by D7: the TEST MTM drawdown against min(15%, the TRAIN bootstrap p95)."""
    s = _s(40, 0.2, dd=4.0)
    assert DD_CAP_PCT == 15.0
    assert dd_limit() == 15.0 and dd_limit(25.0) == 15.0 and dd_limit(20.0, 7.5) == 7.5
    assert dd_limit(10.0) == 10.0  # max_dd_pct may tighten the cap, never loosen it
    ok, reason = dd_check(s, 15.0, train_dd_p95_pct=9.0, mtm_max_dd_pct=8.5)
    assert ok and "mark-to-market max drawdown 8.50%" in reason and "9.00%" in reason
    assert "realised closed-trade 4.00%" in reason and "TEST length of 40" in reason
    assert "min(15%, 95th percentile" in reason
    bad, reason = dd_check(s, 15.0, train_dd_p95_pct=8.0, mtm_max_dd_pct=8.5)
    assert not bad and "exceeds" in reason
    assert dd_check(s, 15.0, 30.0, 15.0)[0] and not dd_check(s, 15.0, 30.0, 15.01)[0]
    assert not dd_check(s, 20.0, 30.0, 15.01)[0]  # a 20% request is still capped at 15%
    assert dd_check(s, 15.0, 8.5, 8.5)[0]  # the boundary is allowed
    # no MTM value (journals only): the realised drawdown is used and the reason says so
    ok, reason = dd_check(_s(40, 0.2, dd=12.5), 15.0)
    assert ok and "12.50%" in reason and "no mark-to-market value" in reason
    assert not dd_check(_s(40, 0.2, dd=15.5), 15.0)[0]
    empty_ok, reason = dd_check(summarize([], EQ0), 15.0)
    assert empty_ok and "No test trades" in reason
    for _ok, text in (dd_check(s, 15.0, 9.0, 8.5), dd_check(s, 15.0, 8.0, 8.5)):
        assert text.endswith(".") and ". " not in text


def test_dd_cap_rationale_is_stated() -> None:
    """D7: 15% ~ 15 full-size 1% losses; (2/3)^15 = 0.23% at the 2:1 break-even p* = 1/3."""
    assert BREAKEVEN_WIN_P == pytest.approx(1 / 3)
    assert DD_CAP_STREAK_P == pytest.approx((2 / 3) ** 15) and f"{DD_CAP_STREAK_P:.2%}" == "0.23%"
    for needle in ("15%", "15 consecutive full-size losses", "1% cluster risk", "p* = 1/3"):
        assert needle in DD_CAP_RATIONALE, needle
    assert "(2/3)^15 = 0.23%" in DD_CAP_RATIONALE and "weekly-loss halt" in DD_CAP_RATIONALE
    doc = metrics.__doc__ or ""
    assert "DD_CAP_PCT = 15 %" in doc and "(2/3)^15 = 0.23 %" in doc


def test_summary_is_frozen() -> None:
    s = _s(1, 0.1)
    with pytest.raises(AttributeError):
        s.n = 2  # type: ignore[misc]
    assert replace(s, n=2).n == 2
