"""Trade-list statistics, walk-forward labels and the drawdown check.

The target metric of this package is EXPECTANCY: the average R-multiple per closed trade
(``avg_r``, net of fees and slippage) that survives a chronological out-of-sample test.
Win rate is reported for context only and is NEVER a target: with a 2R take-profit a
strategy breaks even near 34 % winners, while a 90 % win rate can still lose money.

Conventions:
- Only CLOSED trades (``exit_ts`` and ``r_multiple`` set) are counted, ordered by
  ``(exit_ts, trade_id)`` so path statistics (drawdowns) follow realised time.
- A trade is a win iff ``r_multiple > 0``.
- The 90 % confidence interval of ``avg_r`` is a percentile bootstrap: ``n_boot``
  resamples of size n drawn with ``random.Random(seed).choices``; the 5 % and 95 %
  percentiles of the resampled means use linear interpolation between order statistics.
"""

from __future__ import annotations

import math
import random
import statistics
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from .models import HOUR_MS, Trade


LABELS = ("ROBUST", "TRAIN-ONLY", "UNTESTED", "NO-EDGE")
CI_LOW_Q = 0.05
CI_HIGH_Q = 0.95


@dataclass(frozen=True)
class Summary:
    """Statistics of a list of closed trades. ``avg_r`` (expectancy) is the target metric.

    ``win_rate`` is descriptive only; nothing in this package selects on it.
    """

    n: int  # closed trades counted
    wins: int  # trades with r_multiple > 0
    win_rate: float  # wins / n (context only, not a target)
    avg_r: float  # expectancy: mean R per trade
    median_r: float
    total_r: float
    profit_factor: float | None  # gross winning pnl / |gross losing pnl|; None if no losses
    max_dd_r: float  # largest peak-to-trough drop of cumulative R (>= 0)
    max_dd_pct: float  # largest peak-to-trough drop of the equity curve, percent (>= 0)
    t_stat: float  # avg_r / (sample stdev / sqrt(n)); 0 if n < 2 or stdev == 0
    ci90_low: float  # bootstrap 5th percentile of the mean R (seeded)
    ci90_high: float  # bootstrap 95th percentile of the mean R (seeded)
    exit_counts: dict[str, int]  # exit_reason -> count
    avg_hold_h: float  # mean (exit_ts - entry_ts) in hours


def _closed(trades: Sequence[Trade]) -> list[Trade]:
    closed = [t for t in trades if t.exit_ts is not None and t.r_multiple is not None]
    closed.sort(key=lambda t: (t.exit_ts, t.trade_id))
    return closed


def _pnl(trade: Trade) -> float:
    """Net pnl of a closed trade; falls back to ``r_multiple * risk_amount`` if unset."""
    if trade.pnl is not None:
        return float(trade.pnl)
    return float(trade.r_multiple) * trade.risk_amount  # type: ignore[arg-type]


def percentile(sorted_values: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile (``q`` in [0, 1]) of an ascending sequence."""
    if not sorted_values:
        raise ValueError("percentile of an empty sequence")
    if not 0.0 <= q <= 1.0:
        raise ValueError(f"q must be in [0, 1] (got {q})")
    pos = q * (len(sorted_values) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac


def bootstrap_mean_ci(
    values: Sequence[float],
    seed: int = 7,
    n_boot: int = 2000,
    low_q: float = CI_LOW_Q,
    high_q: float = CI_HIGH_Q,
) -> tuple[float, float]:
    """Percentile-bootstrap CI of the mean, deterministic for a given ``seed``."""
    if n_boot < 1:
        raise ValueError(f"n_boot must be >= 1 (got {n_boot})")
    if not values:
        return 0.0, 0.0
    rng = random.Random(seed)
    k = len(values)
    means = sorted(statistics.fmean(rng.choices(values, k=k)) for _ in range(n_boot))
    return percentile(means, low_q), percentile(means, high_q)


def _max_dd_r(rs: Sequence[float]) -> float:
    """Largest drop of cumulative R below its running peak (the curve starts at 0R)."""
    cum = peak = worst = 0.0
    for r in rs:
        cum += r
        peak = max(peak, cum)
        worst = max(worst, peak - cum)
    return worst


def _max_dd_pct(pnls: Sequence[float], starting_equity: float) -> float:
    """Largest percent drop of ``starting_equity + cumulative pnl`` below its running peak."""
    equity = peak = starting_equity
    worst = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        worst = max(worst, (peak - equity) / peak * 100.0)
    return worst


def _profit_factor(pnls: Sequence[float]) -> float | None:
    gross_win = math.fsum(p for p in pnls if p > 0)
    gross_loss = -math.fsum(p for p in pnls if p < 0)
    if gross_loss <= 0:
        return None
    return gross_win / gross_loss


def _t_stat(rs: Sequence[float]) -> float:
    if len(rs) < 2:
        return 0.0
    sd = statistics.stdev(rs)
    if sd <= 0:
        return 0.0
    return statistics.fmean(rs) / (sd / math.sqrt(len(rs)))


def _empty_summary() -> Summary:
    return Summary(
        n=0,
        wins=0,
        win_rate=0.0,
        avg_r=0.0,
        median_r=0.0,
        total_r=0.0,
        profit_factor=None,
        max_dd_r=0.0,
        max_dd_pct=0.0,
        t_stat=0.0,
        ci90_low=0.0,
        ci90_high=0.0,
        exit_counts={},
        avg_hold_h=0.0,
    )


def summarize(
    trades: Sequence[Trade], starting_equity: float, seed: int = 7, n_boot: int = 2000
) -> Summary:
    """Summarise the CLOSED trades in ``trades`` (open ones are ignored).

    The headline number is ``avg_r`` (expectancy per trade) with its seeded bootstrap
    90 % CI; ``win_rate`` is reported for context and is not a target. An empty trade
    list yields zeros (and ``profit_factor=None``) rather than an exception.
    """
    if not starting_equity > 0:
        raise ValueError(f"starting_equity must be > 0 (got {starting_equity})")
    if n_boot < 1:
        raise ValueError(f"n_boot must be >= 1 (got {n_boot})")
    closed = _closed(trades)
    if not closed:
        return _empty_summary()
    rs = [float(t.r_multiple) for t in closed]  # type: ignore[arg-type]
    pnls = [_pnl(t) for t in closed]
    n = len(rs)
    wins = sum(1 for r in rs if r > 0)
    ci_low, ci_high = bootstrap_mean_ci(rs, seed=seed, n_boot=n_boot)
    exit_counts = Counter(str(t.exit_reason) for t in closed)
    holds_h = [(t.exit_ts - t.entry_ts) / HOUR_MS for t in closed]  # type: ignore[operator]
    return Summary(
        n=n,
        wins=wins,
        win_rate=wins / n,
        avg_r=statistics.fmean(rs),
        median_r=float(statistics.median(rs)),
        total_r=math.fsum(rs),
        profit_factor=_profit_factor(pnls),
        max_dd_r=_max_dd_r(rs),
        max_dd_pct=_max_dd_pct(pnls, starting_equity),
        t_stat=_t_stat(rs),
        ci90_low=ci_low,
        ci90_high=ci_high,
        exit_counts=dict(sorted(exit_counts.items())),
        avg_hold_h=statistics.fmean(holds_h),
    )


def label(
    train: Summary, test: Summary, min_train: int = 30, min_test: int = 30
) -> tuple[str, str]:
    """Walk-forward verdict ``(label, one-sentence reason)``; rules applied in this order.

    1. too few train or test trades           -> UNTESTED
    2. train expectancy <= 0                  -> NO-EDGE (nothing to validate)
    3. test expectancy <= 0                   -> TRAIN-ONLY (likely curve-fit)
    4. test 90 % CI lower bound <= 0          -> UNTESTED (not distinguishable from zero)
    5. otherwise                              -> ROBUST
    """
    if train.n < min_train or test.n < min_test:
        short = [
            f"{name} n={s.n} (need {need})"
            for name, s, need in (("train", train, min_train), ("test", test, min_test))
            if s.n < need
        ]
        return "UNTESTED", f"Too few trades to judge: {' and '.join(short)}."
    if train.avg_r <= 0:
        return "NO-EDGE", (
            f"Train expectancy is {train.avg_r:+.3f}R over {train.n} trades, so there is "
            "no edge to validate."
        )
    if test.avg_r <= 0:
        return "TRAIN-ONLY", (
            f"Train expectancy {train.avg_r:+.3f}R (n={train.n}) did not hold out of sample: "
            f"test expectancy is {test.avg_r:+.3f}R (n={test.n}), which suggests curve-fitting."
        )
    if test.ci90_low <= 0:
        return "UNTESTED", (
            f"Test expectancy is positive at {test.avg_r:+.3f}R (n={test.n}) but its 90% "
            f"bootstrap CI [{test.ci90_low:+.3f}, {test.ci90_high:+.3f}]R includes zero, so "
            "it is not distinguishable from no edge."
        )
    return "ROBUST", (
        f"Expectancy is positive on train ({train.avg_r:+.3f}R, n={train.n}) and on test "
        f"({test.avg_r:+.3f}R, n={test.n}) with the test 90% bootstrap CI "
        f"[{test.ci90_low:+.3f}, {test.ci90_high:+.3f}]R above zero."
    )


def dd_check(test: Summary, max_dd_pct: float) -> tuple[bool, str]:
    """``(ok, one-sentence reason)``: ok iff the test max drawdown is <= ``max_dd_pct``."""
    if test.n == 0:
        return True, (
            f"No test trades were taken, so no drawdown was observed against the "
            f"{max_dd_pct:.2f}% limit (the label reports the missing sample)."
        )
    if test.max_dd_pct <= max_dd_pct:
        return True, (
            f"Test max drawdown {test.max_dd_pct:.2f}% ({test.max_dd_r:.2f}R) is within "
            f"the {max_dd_pct:.2f}% limit."
        )
    return False, (
        f"Test max drawdown {test.max_dd_pct:.2f}% ({test.max_dd_r:.2f}R) exceeds "
        f"the {max_dd_pct:.2f}% limit."
    )
