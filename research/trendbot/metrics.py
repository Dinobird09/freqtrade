"""Trade-list statistics, walk-forward labels and the drawdown check.

The target metric of this package is EXPECTANCY: the average R-multiple per closed trade
(``avg_r``, net of fees and slippage) that survives a chronological out-of-sample test.
Win rate is reported for context only and is NEVER a target: with a 2R take-profit a
strategy breaks even near 34 % winners, while a 90 % win rate can still lose money.

Conventions:
- Only CLOSED trades (``exit_ts`` and ``r_multiple`` set) are counted, ordered by
  ``(exit_ts, trade_id)`` so path statistics (drawdowns) follow realised time.
- A trade is a win iff ``r_multiple > 0``.

Confidence bounds of ``avg_r`` (CONTRACT.md v3 C4), all seeded and deterministic:

- iid bootstrap: ``n_boot`` (default 4000) resamples of size n drawn with
  ``random.Random(seed).choices``; ``ci90_low`` / ``ci90_high`` are the 5 % / 95 %
  percentiles of the resampled means (linear interpolation between order statistics).
- calendar-month block bootstrap: the trades are grouped by the UTC calendar month of their
  EXIT (the months that hold at least one trade are the blocks); each of ``n_boot``
  resamples draws that many months with replacement (``random.Random(seed + 1)``) and takes
  the mean R per trade of all trades in the drawn months. It keeps the within-month
  dependence (clustered regimes, overlapping positions) that the iid bootstrap ignores.
- multiplicity: ``m`` pre-registered candidates get a TEST look (base, discovery-selected,
  base+ml, base+guard, so ``m = 4``), so each one-sided lower bound is taken at confidence
  ``1 - alpha / m`` (Bonferroni: 98.75 % for alpha 0.05, m 4), i.e. the ``alpha / m``
  percentile of each bootstrap. ``adj_lb`` is the SMALLER of the iid and the block bound.

Label (:func:`label`), rules applied in order, never tuned on TEST outcomes:

1. train n < min_train or test n < min_test               -> UNTESTED (too few trades)
2. TRAIN avg R <= 0                                        -> NO-EDGE (nothing to validate)
3. TEST avg R <= 0                                         -> TRAIN-ONLY (likely curve-fit)
4. train n < 30 or test n < 30 (whatever min_* say)        -> UNTESTED (ROBUST needs 30 + 30)
5. ``adj_lb`` of the TEST mean <= 0                        -> UNTESTED ("positive but not
   distinguishable from zero after multiplicity correction")
6. otherwise                                               -> ROBUST

Drawdown (CONTRACT.md v3 C5 as revised by v4 D7, :func:`dd_check`): the realised
(closed-trade) max drawdown is ``Summary.max_dd_pct``; the mark-to-market one
(:func:`mtm_max_dd_pct`) values open positions at every 4H close. ``dd_ok`` requires the TEST
MTM max drawdown to be at most ``min(DD_CAP_PCT = 15 %, the 95th percentile of the max
drawdown of TRAIN trade sequences bootstrapped at the TEST length)``
(:func:`train_dd_quantile`), in percent of equity at the risk each trade actually took.

Why the cap is 15 % (D7, :data:`DD_CAP_RATIONALE`): at the mandated 1 % cluster risk budget a
15 % drawdown is about 15 consecutive full-size losses. At the 2:1 break-even win probability
p* = 1/3 such a streak has probability (2/3)^15 = 0.23 %, so a TEST drawdown beyond it is
inconsistent with even a break-even strategy traded at the mandated risk. The TRAIN-bootstrap
p95 bound usually binds first. The 3 % weekly-loss halt (R9) limits how FAST a drawdown can
accrue, not how DEEP it can go, so it does not replace this cap.
"""

from __future__ import annotations

import math
import random
import statistics
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from .models import HOUR_MS, Candle, Trade


LABELS = ("ROBUST", "TRAIN-ONLY", "UNTESTED", "NO-EDGE")
CI_LOW_Q = 0.05
CI_HIGH_Q = 0.95
N_BOOT = 4000  # bootstrap resamples (C4: >= 4000)
M_CANDIDATES = 4  # pre-registered TEST looks: base, discovery-selected, base+ml, base+guard
ALPHA = 0.05  # family-wise error rate spread over the m candidates (Bonferroni)
ROBUST_MIN_N = 30  # ROBUST needs at least this many trades in EACH window (C4)
DD_CAP_PCT = 15.0  # the TEST drawdown limit is never above 15 % (C5, revised by v4 D7)
BREAKEVEN_WIN_P = 1.0 / 3.0  # break-even win probability at the mandated 2:1 (p* = 1/(1+2))
DD_CAP_STREAK_P = (1.0 - BREAKEVEN_WIN_P) ** round(DD_CAP_PCT)  # (2/3)^15 = 0.23 %
DD_CAP_RATIONALE = (
    f"the {DD_CAP_PCT:g}% cap is about {DD_CAP_PCT:g} consecutive full-size losses at the "
    f"mandated 1% cluster risk budget; at the 2:1 break-even win probability p* = 1/3 such a "
    f"streak has probability (2/3)^{DD_CAP_PCT:g} = {DD_CAP_STREAK_P:.2%}, so a TEST drawdown "
    "beyond it is inconsistent with even a break-even strategy at the mandated risk (the "
    "TRAIN-bootstrap p95 usually binds first; the 3% weekly-loss halt limits how fast a "
    "drawdown accrues, not how deep it goes)"
)
DD_QUANTILE = 0.95  # C5: 95th percentile of bootstrapped TRAIN max drawdowns
BLOCK_SEED_OFFSET = 1  # the block bootstrap uses random.Random(seed + 1)
NOT_DISTINGUISHABLE = "positive but not distinguishable from zero after multiplicity correction"


def lb_confidence(m: int = M_CANDIDATES, alpha: float = ALPHA) -> float:
    """One-sided confidence ``1 - alpha / m`` of the adjusted lower bounds."""
    if isinstance(m, bool) or not isinstance(m, int) or m < 1:
        raise ValueError(f"m must be a positive integer number of candidates (got {m!r})")
    if not (isinstance(alpha, (int, float)) and 0.0 < alpha < 1.0):
        raise ValueError(f"alpha must be in (0, 1) (got {alpha!r})")
    return 1.0 - alpha / m


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
    max_dd_pct: float  # REALISED (closed-trade) max drawdown of the equity curve, % (>= 0)
    t_stat: float  # avg_r / (sample stdev / sqrt(n)); 0 if n < 2 or stdev == 0
    ci90_low: float  # iid bootstrap 5th percentile of the mean R (seeded)
    ci90_high: float  # iid bootstrap 95th percentile of the mean R (seeded)
    exit_counts: dict[str, int]  # exit_reason -> count
    avg_hold_h: float  # mean (exit_ts - entry_ts) in hours
    # --- C4: calendar-month block bootstrap and multiplicity-adjusted lower bounds ---
    block_ci90_low: float = 0.0  # block bootstrap 5th percentile of the mean R
    block_ci90_high: float = 0.0  # block bootstrap 95th percentile of the mean R
    n_blocks: int = 0  # calendar months (UTC, by exit time) holding at least one trade
    lb_confidence: float = 1.0 - ALPHA / M_CANDIDATES  # one-sided confidence 1 - alpha/m
    iid_lb: float = 0.0  # iid bootstrap (alpha/m) percentile of the mean R
    block_lb: float = 0.0  # block bootstrap (alpha/m) percentile of the mean R
    n_boot: int = N_BOOT  # resamples used by both bootstraps

    @property
    def adj_lb(self) -> float:
        """The multiplicity-adjusted lower bound the label uses: the more conservative one."""
        return min(self.iid_lb, self.block_lb)


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


def _check_n_boot(n_boot: int) -> None:
    if isinstance(n_boot, bool) or not isinstance(n_boot, int) or n_boot < 1:
        raise ValueError(f"n_boot must be an integer >= 1 (got {n_boot!r})")


def iid_bootstrap_means(
    values: Sequence[float], seed: int = 7, n_boot: int = N_BOOT
) -> list[float]:
    """Sorted means of ``n_boot`` iid resamples of ``values`` (``random.Random(seed)``)."""
    _check_n_boot(n_boot)
    if not values:
        return []
    rng = random.Random(seed)
    k = len(values)
    return sorted(sum(rng.choices(values, k=k)) / k for _ in range(n_boot))


def bootstrap_mean_ci(
    values: Sequence[float],
    seed: int = 7,
    n_boot: int = N_BOOT,
    low_q: float = CI_LOW_Q,
    high_q: float = CI_HIGH_Q,
) -> tuple[float, float]:
    """Percentile-bootstrap (iid) CI of the mean, deterministic for a given ``seed``."""
    means = iid_bootstrap_means(values, seed, n_boot)
    if not means:
        return 0.0, 0.0
    return percentile(means, low_q), percentile(means, high_q)


def month_key(ts: int) -> tuple[int, int]:
    """``(year, month)`` of a ms timestamp, UTC."""
    tm = time.gmtime(ts // 1000)
    return tm.tm_year, tm.tm_mon


def month_blocks(trades: Sequence[Trade]) -> list[tuple[float, int]]:
    """``(sum of R, trade count)`` per UTC calendar month of the EXIT, chronological.

    Only months holding at least one closed trade are blocks.
    """
    sums: dict[tuple[int, int], float] = {}
    counts: Counter[tuple[int, int]] = Counter()
    for t in _closed(trades):
        key = month_key(t.exit_ts)  # type: ignore[arg-type]
        sums[key] = sums.get(key, 0.0) + float(t.r_multiple)  # type: ignore[arg-type]
        counts[key] += 1
    return [(sums[k], counts[k]) for k in sorted(sums)]


def block_bootstrap_means(
    blocks: Sequence[tuple[float, int]], seed: int = 7, n_boot: int = N_BOOT
) -> list[float]:
    """Sorted means per trade of ``n_boot`` month-block resamples (``Random(seed + 1)``).

    Each resample draws ``len(blocks)`` blocks with replacement and returns the total R of
    the drawn blocks divided by their total trade count.
    """
    _check_n_boot(n_boot)
    if not blocks:
        return []
    rng = random.Random(seed + BLOCK_SEED_OFFSET)
    k = len(blocks)
    out: list[float] = []
    for _ in range(n_boot):
        drawn = rng.choices(blocks, k=k)
        out.append(sum(s for s, _ in drawn) / sum(c for _, c in drawn))
    out.sort()
    return out


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


def max_dd_pct_of_curve(values: Sequence[float], start: float) -> float:
    """Largest percent drop of an equity series below its running peak (peak starts at
    ``start``)."""
    peak, worst = start, 0.0
    for v in values:
        if v > peak:
            peak = v
        elif peak > 0:
            worst = max(worst, (peak - v) / peak * 100.0)
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


def _empty_summary(conf: float, n_boot: int) -> Summary:
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
        lb_confidence=conf,
        n_boot=n_boot,
    )


def summarize(
    trades: Sequence[Trade],
    starting_equity: float,
    seed: int = 7,
    n_boot: int = N_BOOT,
    m: int = M_CANDIDATES,
    alpha: float = ALPHA,
) -> Summary:
    """Summarise the CLOSED trades in ``trades`` (open ones are ignored).

    The headline number is ``avg_r`` (expectancy per trade) with its seeded bootstrap
    bounds (module docstring); ``win_rate`` is reported for context and is not a target.
    ``m`` / ``alpha`` set the one-sided confidence ``1 - alpha / m`` of ``iid_lb`` and
    ``block_lb``. An empty trade list yields zeros (and ``profit_factor=None``).
    """
    if not starting_equity > 0:
        raise ValueError(f"starting_equity must be > 0 (got {starting_equity})")
    _check_n_boot(n_boot)
    conf = lb_confidence(m, alpha)
    closed = _closed(trades)
    if not closed:
        return _empty_summary(conf, n_boot)
    rs = [float(t.r_multiple) for t in closed]  # type: ignore[arg-type]
    pnls = [_pnl(t) for t in closed]
    n = len(rs)
    wins = sum(1 for r in rs if r > 0)
    iid = iid_bootstrap_means(rs, seed, n_boot)
    blocks = month_blocks(closed)
    block = block_bootstrap_means(blocks, seed, n_boot)
    lb_q = 1.0 - conf
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
        ci90_low=percentile(iid, CI_LOW_Q),
        ci90_high=percentile(iid, CI_HIGH_Q),
        exit_counts=dict(sorted(exit_counts.items())),
        avg_hold_h=statistics.fmean(holds_h),
        block_ci90_low=percentile(block, CI_LOW_Q),
        block_ci90_high=percentile(block, CI_HIGH_Q),
        n_blocks=len(blocks),
        lb_confidence=conf,
        iid_lb=percentile(iid, lb_q),
        block_lb=percentile(block, lb_q),
        n_boot=n_boot,
    )


# ---------------------------------------------------------------------------- label
def _too_few(train: Summary, test: Summary, min_train: int, min_test: int) -> str:
    short = [
        f"{name} n={s.n} (need {need})"
        for name, s, need in (("train", train, min_train), ("test", test, min_test))
        if s.n < need
    ]
    return f"Too few trades to judge: {' and '.join(short)}."


def label(
    train: Summary,
    test: Summary,
    min_train: int = 30,
    min_test: int = 30,
    m: int = M_CANDIDATES,
    alpha: float = ALPHA,
) -> tuple[str, str]:
    """Walk-forward verdict ``(label, one-sentence reason)``; rules in the module docstring.

    ``test`` must have been summarised with the same ``m`` and ``alpha`` (its
    ``lb_confidence`` must be ``1 - alpha / m``), else ``ValueError``: the multiplicity rule
    is fixed in advance and cannot be mixed between windows or candidates.
    """
    conf = lb_confidence(m, alpha)
    if not math.isclose(test.lb_confidence, conf, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError(
            f"the TEST summary's lower bounds are at confidence {test.lb_confidence!r}, not "
            f"1 - alpha/m = {conf!r}: summarize it with the same m and alpha"
        )
    if train.n < min_train or test.n < min_test:
        return "UNTESTED", _too_few(train, test, min_train, min_test)
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
    if train.n < ROBUST_MIN_N or test.n < ROBUST_MIN_N:
        return "UNTESTED", (
            f"Expectancy is positive on train ({train.avg_r:+.3f}R, n={train.n}) and test "
            f"({test.avg_r:+.3f}R, n={test.n}), but ROBUST needs at least {ROBUST_MIN_N} "
            "trades in each window, so it cannot be judged."
        )
    bounds = (
        f"the one-sided {conf:.2%} lower bound of the test mean (alpha {alpha:g} split over "
        f"m={m} pre-registered candidates) is {test.iid_lb:+.3f}R by the iid bootstrap and "
        f"{test.block_lb:+.3f}R by the calendar-month block bootstrap ({test.n_blocks} "
        f"months, {test.n_boot} resamples each)"
    )
    if test.adj_lb <= 0:
        return "UNTESTED", (
            f"Test expectancy {test.avg_r:+.3f}R (n={test.n}) is {NOT_DISTINGUISHABLE}: "
            f"{bounds}, and the more conservative {test.adj_lb:+.3f}R is not above zero."
        )
    return "ROBUST", (
        f"Expectancy is positive on train ({train.avg_r:+.3f}R, n={train.n}) and on test "
        f"({test.avg_r:+.3f}R, n={test.n}), and {bounds}, so even the more conservative "
        f"{test.adj_lb:+.3f}R is above zero."
    )


# ---------------------------------------------------------------------------- drawdown
def train_dd_quantile(
    train_trades: Sequence[Trade],
    n_test: int,
    n_boot: int = N_BOOT,
    seed: int = 7,
    q: float = DD_QUANTILE,
) -> float | None:
    """C5: the ``q`` percentile of max drawdown (%) of TRAIN trade sequences of TEST length.

    Each resample draws ``n_test`` closed TRAIN trades with replacement
    (``random.Random(seed)``) and compounds their returns in percent of equity at the risk
    each trade actually took, ``r_multiple * risk_pct`` (= pnl / equity at entry * 100), so
    the drawdowns are in the same units as the TEST mark-to-market drawdown. None if there
    are no closed TRAIN trades or ``n_test < 1``.
    """
    _check_n_boot(n_boot)
    closed = _closed(train_trades)
    if not closed or n_test < 1:
        return None
    rets = [float(t.r_multiple) * t.risk_pct / 100.0 for t in closed]  # type: ignore[arg-type]
    rng = random.Random(seed)
    dds: list[float] = []
    for _ in range(n_boot):
        equity = peak = 1.0
        worst = 0.0
        for x in rng.choices(rets, k=n_test):
            equity *= 1.0 + x
            if equity > peak:
                peak = equity
            elif (peak - equity) / peak > worst:
                worst = (peak - equity) / peak
        dds.append(worst * 100.0)
    dds.sort()
    return percentile(dds, q)


def _timeline(data: Mapping[str, Sequence[Candle]], lo: int, hi: int) -> list[int]:
    stamps: set[int] = set()
    for candles in data.values():
        stamps.update(c.ts for c in candles if lo <= c.ts <= hi)
    return sorted(stamps)


def mtm_equity_curve(
    trades: Sequence[Trade],
    data: Mapping[str, Sequence[Candle]],
    starting_equity: float,
    fee_rate: float,
) -> list[tuple[int, float]]:
    """Mark-to-market equity ``(candle open ts, equity at that candle's CLOSE)``.

    Covers every candle from the first entry to the last exit of ``trades`` (equity is flat
    at ``starting_equity`` before and constant after). At the close of candle ``t``: every
    trade with ``exit_ts <= t`` is realised (net pnl), every trade with
    ``entry_ts <= t < exit_ts`` (or not yet exited) is valued at that candle's close,
    ``qty * (close - entry_price) - fee_rate * qty * entry_price`` (the entry fee is sunk; the
    exit fee and slippage are charged when the trade is realised). A pair without a candle
    at ``t`` keeps its last close.
    """
    ordered = sorted((t for t in trades if t.qty > 0), key=lambda t: (t.entry_ts, t.trade_id))
    if not ordered:
        return []
    pairs = {t.pair for t in ordered}
    closes = {p: {c.ts: c.close for c in data[p]} for p in pairs}
    last_close: dict[str, float] = {}
    if all(t.exit_ts is not None for t in ordered):
        hi = max(t.exit_ts for t in ordered)  # type: ignore[type-var]
    else:  # a still-open trade is valued up to the last candle of its pair
        hi = max(max(closes[p]) for p in pairs)
    curve: list[tuple[int, float]] = []
    realised = 0.0
    open_trades: list[Trade] = []
    k = 0
    for ts in _timeline(data, ordered[0].entry_ts, hi):  # type: ignore[arg-type]
        for pair, by_ts in closes.items():
            if ts in by_ts:
                last_close[pair] = by_ts[ts]
        while k < len(ordered) and ordered[k].entry_ts <= ts:
            open_trades.append(ordered[k])
            k += 1
        still: list[Trade] = []
        for t in open_trades:
            if t.exit_ts is not None and t.exit_ts <= ts:
                realised += _pnl(t)
            else:
                still.append(t)
        open_trades = still
        unrealised = math.fsum(
            t.qty * (last_close.get(t.pair, t.entry_price) - t.entry_price)
            - fee_rate * t.qty * t.entry_price
            for t in open_trades
        )
        curve.append((ts, starting_equity + realised + unrealised))
    return curve


def mtm_max_dd_pct(
    trades: Sequence[Trade],
    data: Mapping[str, Sequence[Candle]],
    starting_equity: float,
    fee_rate: float,
) -> float:
    """Max drawdown (%) of :func:`mtm_equity_curve`, the peak starting at ``starting_equity``."""
    curve = mtm_equity_curve(trades, data, starting_equity, fee_rate)
    return max_dd_pct_of_curve([eq for _, eq in curve], starting_equity)


def dd_limit(max_dd_pct: float = DD_CAP_PCT, train_dd_p95_pct: float | None = None) -> float:
    """The C5/D7 limit ``min(DD_CAP_PCT = 15 %, max_dd_pct, TRAIN bootstrap p95)`` (None p95 =
    not given). ``max_dd_pct`` can only tighten the cap, never loosen it."""
    limit = min(DD_CAP_PCT, float(max_dd_pct))
    if train_dd_p95_pct is not None:
        limit = min(limit, float(train_dd_p95_pct))
    return limit


def dd_check(
    test: Summary,
    max_dd_pct: float = DD_CAP_PCT,
    train_dd_p95_pct: float | None = None,
    mtm_max_dd_pct: float | None = None,
) -> tuple[bool, str]:
    """``(ok, one-sentence reason)`` of the C5 drawdown rule on the TEST window.

    ok iff the TEST mark-to-market max drawdown ``mtm_max_dd_pct`` is at most
    :func:`dd_limit` ``(max_dd_pct, train_dd_p95_pct)``. Without ``mtm_max_dd_pct`` (no
    candles, e.g. a check from journals alone) the realised ``test.max_dd_pct`` is used and
    the reason says so. No TEST trades passes (the label reports the missing sample).
    """
    limit = dd_limit(max_dd_pct, train_dd_p95_pct)
    if train_dd_p95_pct is None:
        rule = f"the limit {limit:.2f}% (min of {DD_CAP_PCT:g}% and {max_dd_pct:g}%)"
    else:
        rule = (
            f"the limit {limit:.2f}% = min({min(DD_CAP_PCT, max_dd_pct):g}%, 95th percentile "
            f"{train_dd_p95_pct:.2f}% of max drawdown over TRAIN trade sequences bootstrapped "
            f"at the TEST length of {test.n})"
        )
    if test.n == 0:
        return True, f"No test trades were taken, so no drawdown was observed against {rule}."
    realised = f"realised closed-trade {test.max_dd_pct:.2f}% ({test.max_dd_r:.2f}R)"
    if mtm_max_dd_pct is None:
        observed, what = test.max_dd_pct, f"Test {realised} max drawdown (no mark-to-market value)"
    else:
        observed = float(mtm_max_dd_pct)
        what = f"Test mark-to-market max drawdown {observed:.2f}% ({realised})"
    if observed <= limit:
        return True, f"{what} is within {rule}."
    return False, f"{what} exceeds {rule}."
