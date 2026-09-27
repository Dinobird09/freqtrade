"""Optional entry-filter layer ``L_ml_filter``: small L2-regularised logistic regression.

Why only this model: the training sample is a few hundred rule-passing signals. At that
size anything more flexible (boosting, neural nets) memorises noise, so the only model
allowed here is a 6-feature logistic regression with a fixed ridge penalty, fitted by
Newton/IRLS in pure Python. There is NO hyper-parameter search and NO threshold search:

- features are standardised with TRAIN mean/std only (std 0 -> 1);
- the label is ``r_multiple > 0`` (fees and slippage included);
- a trade is kept iff the predicted win probability exceeds the break-even probability
  implied by the TRAIN average win and loss sizes,
  ``p* = -avg_loss_r / (avg_win_r - avg_loss_r)``, i.e. where expectancy crosses zero.

The layer can only REMOVE trades that already passed every mandatory rule.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Mapping, Sequence

from .models import CandidateOutcome, EntryFilter, FeatureRow, SignalCheck


FEATURES = ("rsi", "vol_ratio", "ema_gap_pct", "dist_regime_pct", "hour_sin", "hour_cos")
_RAW_FEATURES = ("rsi", "vol_ratio", "ema_gap_pct", "dist_regime_pct")
LOGIT_CLIP = 30.0  # |logit| cap: keeps exp() finite and probabilities strictly in (0, 1)
_ARMIJO = 1e-4
_MAX_HALVINGS = 40


class InsufficientData(ValueError):
    """Too few TRAIN candidates (overall or per class) to fit the ML filter honestly."""


# ---------------------------------------------------------------------------- features
def feature_vector(features: Mapping[str, float] | None) -> list[float] | None:
    """Model inputs in ``FEATURES`` order from a ``FeatureRow.ml_features()`` dict.

    Returns None if any input is missing or non-finite. The hour is encoded on the unit
    circle (``sin``/``cos`` of ``2*pi*hour/24``) so 23:00 and 00:00 are neighbours.
    """
    if not features:
        return None
    try:
        raw = [float(features[k]) for k in _RAW_FEATURES]
        angle = 2.0 * math.pi * float(features["hour_utc"]) / 24.0
    except (KeyError, TypeError, ValueError):
        return None
    vec = [*raw, math.sin(angle), math.cos(angle)]
    if not all(math.isfinite(v) for v in vec):
        return None
    return vec


# ---------------------------------------------------------------------------- linear algebra
def solve_linear(a: Sequence[Sequence[float]], b: Sequence[float]) -> list[float]:
    """Solve ``a @ x = b`` by Gaussian elimination with partial pivoting.

    Raises ValueError if ``a`` is not square or is numerically singular.
    """
    n = len(b)
    if len(a) != n or any(len(row) != n for row in a):
        raise ValueError("solve_linear needs a square matrix matching len(b)")
    m = [[float(v) for v in row] + [float(bi)] for row, bi in zip(a, b, strict=True)]
    scale = max((abs(v) for row in a for v in row), default=0.0)
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) <= 1e-14 * scale:
            raise ValueError("matrix is singular to working precision")
        m[col], m[piv] = m[piv], m[col]
        pivot_row = m[col]
        for r in range(col + 1, n):
            factor = m[r][col] / pivot_row[col]
            if factor != 0.0:
                row = m[r]
                for c in range(col, n + 1):
                    row[c] -= factor * pivot_row[c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        acc = m[r][n] - math.fsum(m[r][c] * x[c] for c in range(r + 1, n))
        x[r] = acc / m[r][r]
    return x


# ---------------------------------------------------------------------------- logistic model
def _sigmoid(z: float) -> float:
    if math.isnan(z):
        return math.nan
    z = max(-LOGIT_CLIP, min(LOGIT_CLIP, z))
    return 1.0 / (1.0 + math.exp(-z))


def _softplus(z: float) -> float:
    """Numerically stable ``log(1 + exp(z))``."""
    if z > 0:
        return z + math.log1p(math.exp(-z))
    return math.log1p(math.exp(z))


def _dot(u: Sequence[float], v: Sequence[float]) -> float:
    # Builtin sum: overflow gives +/-inf or nan (handled by callers) instead of raising.
    return sum(a * b for a, b in zip(u, v, strict=True))


def _objective(design: list[list[float]], y: list[float], beta: list[float], l2: float) -> float:
    """Penalised negative log-likelihood; the intercept ``beta[0]`` is not penalised."""
    terms = []
    for row, yi in zip(design, y, strict=True):
        z = _dot(row, beta)
        terms.append(_softplus(z) - yi * z)
    try:
        return math.fsum(terms) + 0.5 * l2 * math.fsum(w * w for w in beta[1:])
    except (OverflowError, ValueError):  # a wild trial step: reject it in the line search
        return math.inf


def _grad_hess(
    design: list[list[float]], y: list[float], beta: list[float], l2: float
) -> tuple[list[float], list[list[float]]]:
    k = len(beta)
    grad = [0.0] * k
    hess = [[0.0] * k for _ in range(k)]
    for row, yi in zip(design, y, strict=True):
        p = _sigmoid(_dot(row, beta))
        resid = p - yi
        weight = p * (1.0 - p)
        for i in range(k):
            grad[i] += resid * row[i]
            wi = weight * row[i]
            hrow = hess[i]
            for j in range(i, k):
                hrow[j] += wi * row[j]
    for i in range(k):
        for j in range(i):
            hess[i][j] = hess[j][i]
    for j in range(1, k):
        grad[j] += l2 * beta[j]
        hess[j][j] += l2
    return grad, hess


def _line_search(
    design: list[list[float]],
    y: list[float],
    beta: list[float],
    step: list[float],
    grad: list[float],
    obj: float,
    l2: float,
) -> tuple[list[float], float, bool]:
    """Backtracking (Armijo) along the Newton direction; returns (beta, objective, moved)."""
    decrease = _dot(grad, step)  # > 0: step = H^-1 g with H positive definite
    t = 1.0
    for _ in range(_MAX_HALVINGS):
        cand = [b - t * s for b, s in zip(beta, step, strict=True)]
        cand_obj = _objective(design, y, cand, l2)
        if cand_obj <= obj - _ARMIJO * t * decrease:
            return cand, cand_obj, True
        t *= 0.5
    return beta, obj, False


class LogisticModel:
    """L2-regularised logistic regression fitted by damped Newton / IRLS (pure Python).

    Minimises ``sum_i [log(1 + e^{z_i}) - y_i z_i] + l2/2 * ||w||^2`` with
    ``z_i = b + w . x_i``; the intercept ``b`` is not penalised. Each Newton system is
    solved by Gaussian elimination with partial pivoting; iteration stops when the largest
    Newton step component is below ``tol`` or after ``max_iter`` iterations.
    """

    def __init__(self) -> None:
        self.intercept: float | None = None
        self.weights: tuple[float, ...] = ()
        self.l2: float = 0.0
        self.n_iter: int = 0
        self.converged: bool = False

    @staticmethod
    def _validate(
        x: Sequence[Sequence[float]], y: Sequence[float], l2: float
    ) -> tuple[list[list[float]], list[float]]:
        if not (math.isfinite(l2) and l2 >= 0):
            raise ValueError(f"l2 must be a finite number >= 0 (got {l2})")
        if not x or len(x) != len(y):
            raise ValueError(f"need a non-empty X with one label per row ({len(x)} vs {len(y)})")
        d = len(x[0])
        if any(len(row) != d for row in x):
            raise ValueError("all rows of X must have the same length")
        labels = [float(v) for v in y]
        if any(v not in (0.0, 1.0) for v in labels):
            raise ValueError("labels must be 0/1 (or bool)")
        design = [[1.0, *(float(v) for v in row)] for row in x]
        if not all(math.isfinite(v) for row in design for v in row):
            raise ValueError("X contains a non-finite value")
        return design, labels

    def fit(
        self,
        x: Sequence[Sequence[float]],
        y: Sequence[float],
        l2: float = 1.0,
        tol: float = 1e-8,
        max_iter: int = 50,
    ) -> LogisticModel:
        """Fit on rows ``x`` and 0/1 labels ``y``; returns ``self``."""
        design, labels = self._validate(x, y, l2)
        beta = [0.0] * len(design[0])
        obj = _objective(design, labels, beta, l2)
        self.converged = False
        self.n_iter = 0
        for it in range(1, max_iter + 1):
            self.n_iter = it
            grad, hess = _grad_hess(design, labels, beta, l2)
            step = solve_linear(hess, grad)
            if max(abs(s) for s in step) < tol:
                beta = [b - s for b, s in zip(beta, step, strict=True)]
                self.converged = True
                break
            beta, obj, moved = _line_search(design, labels, beta, step, grad, obj, l2)
            if not moved:  # at the floating-point floor of the objective
                break
        self.intercept = beta[0]
        self.weights = tuple(beta[1:])
        self.l2 = l2
        return self

    def _require_fitted(self) -> float:
        if self.intercept is None:
            raise RuntimeError("LogisticModel is not fitted")
        return self.intercept

    def logit(self, row: Sequence[float]) -> float:
        """Linear score ``b + w . x`` of one row (unclipped)."""
        return self._require_fitted() + _dot(self.weights, row)

    def predict_proba(self, x: Sequence[Sequence[float]]) -> list[float]:
        """P(y = 1) per row; logits are clipped to +/-LOGIT_CLIP (a NaN input gives NaN)."""
        return [_sigmoid(self.logit(row)) for row in x]

    def coefficients(self) -> tuple[float, tuple[float, ...]]:
        """``(intercept, weights)`` in the column order of the training X."""
        return self._require_fitted(), self.weights


# ---------------------------------------------------------------------------- filter
def breakeven_probability(avg_win_r: float, avg_loss_r: float) -> float:
    """Win probability at which expectancy is zero: ``-avg_loss / (avg_win - avg_loss)``."""
    if not (avg_win_r > 0 and avg_loss_r <= 0):
        raise ValueError(f"need avg_win_r > 0 and avg_loss_r <= 0 (got {avg_win_r}, {avg_loss_r})")
    return -avg_loss_r / (avg_win_r - avg_loss_r)


def _training_rows(
    candidates: Sequence[CandidateOutcome],
) -> tuple[list[list[float]], list[float], int]:
    rows: list[list[float]] = []
    rs: list[float] = []
    skipped = 0
    for cand in candidates:
        vec = feature_vector(cand.features)
        if vec is None or not math.isfinite(cand.r_multiple):
            skipped += 1
            continue
        rows.append(vec)
        rs.append(float(cand.r_multiple))
    return rows, rs, skipped


def _check_sample(n: int, wins: int, skipped: int, min_samples: int, min_per_class: int) -> None:
    if n < min_samples:
        raise InsufficientData(
            f"ML filter needs at least {min_samples} TRAIN candidates with complete features; "
            f"got {n} ({skipped} skipped for missing features)."
        )
    losses = n - wins
    if min(wins, losses) < min_per_class:
        raise InsufficientData(
            f"ML filter needs at least {min_per_class} wins and {min_per_class} losses among "
            f"TRAIN candidates; got {wins} wins and {losses} losses."
        )


def _standardiser(rows: list[list[float]]) -> tuple[tuple[float, ...], tuple[float, ...]]:
    cols = list(zip(*rows, strict=True))
    means = tuple(statistics.fmean(c) for c in cols)
    stds = tuple(statistics.pstdev(c, mu) for c, mu in zip(cols, means, strict=True))
    return means, tuple(s if s > 1e-12 else 1.0 for s in stds)


class MLFilter:
    """A fitted logistic filter plus the TRAIN statistics it was built from."""

    def __init__(
        self,
        model: LogisticModel,
        means: Sequence[float],
        stds: Sequence[float],
        threshold: float,
        train_n: int,
        train_wins: int,
        avg_win_r: float,
        avg_loss_r: float,
        train_skipped: int = 0,
    ) -> None:
        self.model = model
        self.means = tuple(means)
        self.stds = tuple(stds)
        self.threshold = threshold  # break-even win probability p*
        self.train_n = train_n
        self.train_wins = train_wins
        self.train_base_rate = train_wins / train_n if train_n else 0.0
        self.avg_win_r = avg_win_r
        self.avg_loss_r = avg_loss_r
        self.train_skipped = train_skipped
        self.l2 = model.l2

    @classmethod
    def fit(
        cls,
        candidates: Sequence[CandidateOutcome],
        l2: float = 1.0,
        min_samples: int = 100,
        min_per_class: int = 20,
    ) -> MLFilter:
        """Fit on TRAIN candidates only. Raises InsufficientData if the sample is too small."""
        rows, rs, skipped = _training_rows(candidates)
        labels = [1.0 if r > 0 else 0.0 for r in rs]
        wins = int(sum(labels))
        _check_sample(len(rows), wins, skipped, min_samples, min_per_class)
        means, stds = _standardiser(rows)
        z = [[(v - m) / s for v, m, s in zip(row, means, stds, strict=True)] for row in rows]
        model = LogisticModel().fit(z, labels, l2=l2)
        avg_win = statistics.fmean(r for r in rs if r > 0)
        avg_loss = statistics.fmean(r for r in rs if r <= 0)
        return cls(
            model=model,
            means=means,
            stds=stds,
            threshold=breakeven_probability(avg_win, avg_loss),
            train_n=len(rows),
            train_wins=wins,
            avg_win_r=avg_win,
            avg_loss_r=avg_loss,
            train_skipped=skipped,
        )

    def probability(self, features: Mapping[str, float] | None) -> float | None:
        """Predicted win probability for an ``ml_features()`` dict, or None if unusable."""
        vec = feature_vector(features)
        if vec is None:
            return None
        z = [(v - m) / s for v, m, s in zip(vec, self.means, self.stds, strict=True)]
        score = self.model.logit(z)
        if math.isnan(score):
            return None
        return _sigmoid(score)

    def decide(self, row: FeatureRow) -> tuple[bool, float | None, str]:
        """``(keep, probability, one-sentence reason)`` for one signal candle; never raises."""
        p = self.probability(row.ml_features())
        if p is None:
            return (
                False,
                None,
                (
                    "ML filter cannot score this signal because an input feature is missing "
                    "(insufficient indicator history), so the trade is removed."
                ),
            )
        basis = (
            f"TRAIN avg win {self.avg_win_r:+.2f}R / avg loss {self.avg_loss_r:+.2f}R "
            f"over {self.train_n} candidates"
        )
        if p > self.threshold:
            return (
                True,
                p,
                (
                    f"ML win probability {p:.3f} is above the break-even {self.threshold:.3f} "
                    f"({basis}), so the trade is kept."
                ),
            )
        return (
            False,
            p,
            (
                f"ML win probability {p:.3f} is at or below the break-even {self.threshold:.3f} "
                f"({basis}), so the trade is removed."
            ),
        )

    def entry_filter(self) -> EntryFilter:
        """An ``EntryFilter`` callable bound to this model (``.ml`` gives it back for reports)."""
        return MLEntryFilter(self)

    def explain(self) -> list[tuple[str, float]]:
        """Standardised coefficients (log-odds per TRAIN standard deviation), FEATURES order."""
        _, weights = self.model.coefficients()
        return list(zip(FEATURES, weights, strict=True))

    def describe(self) -> str:
        """One-sentence description of the fitted filter for reports."""
        return (
            f"Logistic filter (l2={self.l2:g}) fitted on {self.train_n} TRAIN candidates "
            f"(base win rate {self.train_base_rate:.1%}) keeps signals whose predicted win "
            f"probability exceeds the break-even {self.threshold:.3f}."
        )


class MLEntryFilter:
    """``EntryFilter`` adapter: ``(pair, row, check) -> (allow, prob, reason)``."""

    def __init__(self, ml: MLFilter) -> None:
        self.ml = ml

    def __call__(
        self, pair: str, row: FeatureRow, check: SignalCheck
    ) -> tuple[bool, float | None, str]:
        return self.ml.decide(row)


def make_factory(l2: float = 1.0) -> Callable[[Sequence[CandidateOutcome]], EntryFilter]:
    """``factory(train_candidates) -> EntryFilter``, fitted on exactly the candidates given.

    The caller (walk-forward) must pass TRAIN candidates only; the returned filter is then
    applied unchanged to TEST. InsufficientData propagates so the caller can report it.
    """

    def factory(train_candidates: Sequence[CandidateOutcome]) -> EntryFilter:
        return MLFilter.fit(train_candidates, l2=l2).entry_filter()

    return factory
