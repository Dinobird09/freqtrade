"""Regime layer: a pure-Python Gaussian hidden Markov model over a reference series.

Model
    A K-state Gaussian HMM with DIAGONAL covariance (default K=5), fitted by Baum-Welch EM
    with per-step scaling (emission log-likelihoods are shifted by their per-step maximum
    before exponentiation, so neither the forward nor the backward pass can underflow).
    Initialisation is deterministic: k-means (Lloyd) on z-scored observations, seeded with
    points taken at the K quantiles of the "calm-and-rising vs volatile-and-falling" score
    ``z(return) - z(volatility)``. Variances are floored at ``var_floor`` times the overall
    variance of each dimension; transition rows are floored so every transition stays
    possible. EM stops after ``max_iter`` iterations or when the log-likelihood gain per
    observation drops below ``tol``.

Observations (one per 4H candle of the reference series, all causal)
    ``[log return, realized volatility]`` where the log return is ``log(close_t /
    close_{t-1})`` and the realized volatility is the sample std of the last ``vol_window``
    (default 12) log returns ending at ``t``. The first observation is at the candle where
    the window is first full. The reference series is ``ref_pair`` (default "BTC/USDT") if
    the market view has it, else the traded pair itself.

Labels
    After fitting, states are ordered by the mean of their log-return emission and named
    from the most negative up: K=2 bear, bull; K=3 bear, neutral, bull; K=4 crash, bear,
    neutral, bull; K=5 crash, bear, neutral, bull, euphoria; other K: s0..s{K-1}. The
    BEARISH states are crash and bear.

Decisions use only FILTERED probabilities P(state_t | obs_1..obs_t) from the forward
recursion. The smoothed (forward-backward) posteriors are used only inside EM fitting on
the training window, never for a veto.

Layer ``hmm_regime`` vetoes an entry when P(crash) + P(bear) at ``row.close_ts`` is >=
``threshold`` (default 0.6). Running forward probabilities are cached per reference series
and extended incrementally; the cache is validated against the view's candles (so a new
``MarketView`` with appended candles extends it, and changed history rebuilds it).
"""

from __future__ import annotations

import bisect
import math
from collections import deque
from collections.abc import Mapping, Sequence
from typing import Any

from .layers import Layer, MarketView, TrainContext, register
from .models import Candle, FeatureRow


LOG_2PI = math.log(2.0 * math.pi)
LABELS = {
    2: ("bear", "bull"),
    3: ("bear", "neutral", "bull"),
    4: ("crash", "bear", "neutral", "bull"),
    5: ("crash", "bear", "neutral", "bull", "euphoria"),
}
BEARISH = ("crash", "bear")
TRANS_FLOOR = 1e-8


def state_labels(k: int) -> tuple[str, ...]:
    """Names of the K states ordered from the lowest to the highest mean return."""
    return LABELS.get(k, tuple(f"s{i}" for i in range(k)))


# ---------------------------------------------------------------------- Gaussian HMM
class GaussianHMM:
    """Diagonal-covariance Gaussian HMM (pure Python, deterministic)."""

    def __init__(
        self,
        n_states: int = 4,
        max_iter: int = 50,
        tol: float = 1e-4,
        var_floor: float = 1e-3,
        sticky: float = 0.9,
        kmeans_iter: int = 10,
    ) -> None:
        if n_states < 1:
            raise ValueError("n_states must be >= 1")
        self.k = n_states
        self.max_iter = max_iter
        self.tol = tol
        self.var_floor = var_floor
        self.sticky = sticky
        self.kmeans_iter = kmeans_iter
        self.means: list[list[float]] = []
        self.variances: list[list[float]] = []
        self.trans: list[list[float]] = []
        self.init: list[float] = []
        self.floors: list[float] = []
        self.loglik = float("-inf")
        self.n_iter = 0
        self._prep()

    # ------------------------------------------------------------ emissions
    def _prep(self) -> None:
        self._inv = [[1.0 / v for v in var] for var in self.variances]
        self._const = [-0.5 * sum(LOG_2PI + math.log(v) for v in var) for var in self.variances]

    def log_emission(self, x: Sequence[float]) -> list[float]:
        out = []
        for mu, inv, c in zip(self.means, self._inv, self._const, strict=True):
            s = 0.0
            for xi, m, iv in zip(x, mu, inv, strict=True):
                d = xi - m
                s += d * d * iv
            out.append(c - 0.5 * s)
        return out

    def _scaled_emission(self, x: Sequence[float]) -> tuple[list[float], float]:
        lb = self.log_emission(x)
        m = max(lb)
        return [math.exp(v - m) for v in lb], m

    # ------------------------------------------------------------ filtering
    def filter_step(
        self, prev: Sequence[float] | None, x: Sequence[float]
    ) -> tuple[list[float], float]:
        """One forward step: (filtered probs at t, log p(x_t | x_<t)). ``prev`` None = t0."""
        b, m = self._scaled_emission(x)
        k = self.k
        if prev is None:
            pred = self.init
        else:
            a = self.trans
            pred = [0.0] * k
            for i, pi in enumerate(prev):
                if pi:
                    row = a[i]
                    for j in range(k):
                        pred[j] += pi * row[j]
        alpha = [p * bj for p, bj in zip(pred, b, strict=True)]
        c = sum(alpha)
        if not c > 0.0 or not math.isfinite(c):  # degenerate: fall back to the prediction
            s = sum(pred)
            return [p / s for p in pred], float("-inf")
        return [v / c for v in alpha], math.log(c) + m

    def filter(self, obs: Sequence[Sequence[float]]) -> tuple[list[list[float]], float]:
        """Forward-only filtered probabilities for every t (uses obs[:t+1] only)."""
        out: list[list[float]] = []
        ll = 0.0
        prev: list[float] | None = None
        for x in obs:
            prev, step = self.filter_step(prev, x)
            out.append(prev)
            ll += step
        return out, ll

    # ------------------------------------------------------------ fitting
    def fit(self, obs: Sequence[Sequence[float]]) -> GaussianHMM:
        if len(obs) < 2 * self.k:
            raise ValueError(f"need at least {2 * self.k} observations, got {len(obs)}")
        self._initialise(obs)
        prev_ll = float("-inf")
        t_n = len(obs)
        for it in range(1, self.max_iter + 1):
            ll, gamma_sum, gamma0, xi_sum, sx, sxx = self._e_step(obs)
            self._m_step(t_n, gamma_sum, gamma0, xi_sum, sx, sxx)
            self.n_iter = it
            self.loglik = ll
            if math.isfinite(prev_ll) and (ll - prev_ll) / t_n < self.tol:
                break
            prev_ll = ll
        self.loglik = self.filter(obs)[1]
        return self

    def _initialise(self, obs: Sequence[Sequence[float]]) -> None:
        d = len(obs[0])
        n = len(obs)
        mu = [sum(x[j] for x in obs) / n for j in range(d)]
        var = [max(sum((x[j] - mu[j]) ** 2 for x in obs) / n, 1e-300) for j in range(d)]
        sd = [math.sqrt(v) for v in var]
        self.floors = [self.var_floor * v for v in var]
        z = [[(x[j] - mu[j]) / sd[j] for j in range(d)] for x in obs]
        centroids = _kmeans(z, self.k, self.kmeans_iter)
        assign = [_nearest(p, centroids) for p in z]
        means, variances = [], []
        for s in range(self.k):
            members = [obs[i] for i, a in enumerate(assign) if a == s]
            if len(members) < 2:
                means.append([mu[j] + centroids[s][j] * sd[j] for j in range(d)])
                variances.append(list(var))
                continue
            m_s = [sum(x[j] for x in members) / len(members) for j in range(d)]
            v_s = [sum((x[j] - m_s[j]) ** 2 for x in members) / len(members) for j in range(d)]
            means.append(m_s)
            variances.append([max(v, f) for v, f in zip(v_s, self.floors, strict=True)])
        self.means, self.variances = means, variances
        k = self.k
        off = (1.0 - self.sticky) / (k - 1) if k > 1 else 0.0
        self.trans = [[self.sticky if i == j else off for j in range(k)] for i in range(k)]
        if k == 1:
            self.trans = [[1.0]]
        self.init = [1.0 / k] * k
        self._prep()

    def _e_step(self, obs: Sequence[Sequence[float]]) -> tuple[Any, ...]:
        k = self.k
        a = self.trans
        t_n = len(obs)
        bs: list[list[float]] = []
        alphas: list[list[float]] = []
        cs: list[float] = []
        ll = 0.0
        prev: list[float] | None = None
        for x in obs:  # scaled forward pass
            b, m = self._scaled_emission(x)
            pred = self.init if prev is None else _mat_vec_left(prev, a, k)
            al = [p * bj for p, bj in zip(pred, b, strict=True)]
            c = sum(al) or 1e-300
            prev = [v / c for v in al]
            bs.append(b)
            alphas.append(prev)
            cs.append(c)
            ll += math.log(c) + m
        d = len(obs[0])
        xi_sum = [[0.0] * k for _ in range(k)]
        gamma_sum = [0.0] * k
        sx = [[0.0] * d for _ in range(k)]
        sxx = [[0.0] * d for _ in range(k)]
        beta = [1.0] * k
        gamma0: list[float] = []
        for t in range(t_n - 1, -1, -1):  # scaled backward pass + accumulation
            al = alphas[t]
            g = [al[i] * beta[i] for i in range(k)]
            gs = sum(g) or 1e-300
            x = obs[t]
            for i in range(k):
                gi = g[i] / gs
                gamma_sum[i] += gi
                sxi, sxxi = sx[i], sxx[i]
                for j in range(d):
                    v = x[j]
                    sxi[j] += gi * v
                    sxxi[j] += gi * v * v
            if t == 0:
                gamma0 = [v / gs for v in g]
                break
            # beta_{t-1} and xi_{t-1}
            bb = [bs[t][j] * beta[j] / cs[t] for j in range(k)]
            ap = alphas[t - 1]
            new_beta = [0.0] * k
            for i in range(k):
                row = a[i]
                api = ap[i]
                xrow = xi_sum[i]
                s = 0.0
                for j in range(k):
                    v = row[j] * bb[j]
                    s += v
                    xrow[j] += api * v
                new_beta[i] = s
            beta = new_beta
        return ll, gamma_sum, gamma0, xi_sum, sx, sxx

    def _m_step(
        self,
        t_n: int,
        gamma_sum: list[float],
        gamma0: list[float],
        xi_sum: list[list[float]],
        sx: list[list[float]],
        sxx: list[list[float]],
    ) -> None:
        k = self.k
        d = len(self.floors)
        for i in range(k):
            w = gamma_sum[i]
            if w < 1e-6 * t_n:  # a dead state keeps its previous emission
                continue
            m = [sx[i][j] / w for j in range(d)]
            v = [max(sxx[i][j] / w - m[j] * m[j], self.floors[j]) for j in range(d)]
            self.means[i], self.variances[i] = m, v
        trans = []
        for i in range(k):
            row = [max(x, TRANS_FLOOR) for x in xi_sum[i]]
            s = sum(row)
            trans.append([x / s for x in row])
        self.trans = trans
        g0 = [max(x, 1e-6) for x in gamma0]
        s0 = sum(g0)
        self.init = [x / s0 for x in g0]
        self._prep()

    def reorder(self, order: Sequence[int]) -> None:
        """Permute the states: new state i is old state ``order[i]``."""
        self.means = [self.means[o] for o in order]
        self.variances = [self.variances[o] for o in order]
        self.init = [self.init[o] for o in order]
        self.trans = [[self.trans[o][p] for p in order] for o in order]
        self._prep()

    # ------------------------------------------------------------ persistence
    def to_dict(self) -> dict[str, Any]:
        return {
            "n_states": self.k,
            "means": self.means,
            "variances": self.variances,
            "trans": self.trans,
            "init": self.init,
            "floors": self.floors,
            "loglik": self.loglik,
            "n_iter": self.n_iter,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> GaussianHMM:
        h = cls(n_states=int(d["n_states"]))
        h.means = [list(map(float, r)) for r in d["means"]]
        h.variances = [list(map(float, r)) for r in d["variances"]]
        h.trans = [list(map(float, r)) for r in d["trans"]]
        h.init = list(map(float, d["init"]))
        h.floors = list(map(float, d.get("floors", [])))
        h.loglik = float(d.get("loglik", float("-inf")))
        h.n_iter = int(d.get("n_iter", 0))
        h._prep()
        return h


def _mat_vec_left(v: Sequence[float], a: Sequence[Sequence[float]], k: int) -> list[float]:
    out = [0.0] * k
    for i, vi in enumerate(v):
        row = a[i]
        for j in range(k):
            out[j] += vi * row[j]
    return out


def _nearest(p: Sequence[float], cents: Sequence[Sequence[float]]) -> int:
    best, bi = float("inf"), 0
    for i, c in enumerate(cents):
        dist = sum((a - b) ** 2 for a, b in zip(p, c, strict=True))
        if dist < best:
            best, bi = dist, i
    return bi


def _kmeans(z: Sequence[Sequence[float]], k: int, iters: int) -> list[list[float]]:
    """Deterministic Lloyd k-means seeded at quantiles of z[0] - z[1] (or z[0] in 1-D)."""
    n = len(z)
    score = [p[0] - p[1] if len(p) > 1 else p[0] for p in z]
    order = sorted(range(n), key=lambda i: (score[i], i))
    cents = [list(z[order[min(n - 1, int((s + 0.5) * n / k))]]) for s in range(k)]
    d = len(z[0])
    for _ in range(iters):
        sums = [[0.0] * d for _ in range(k)]
        counts = [0] * k
        for p in z:
            c = _nearest(p, cents)
            counts[c] += 1
            for j in range(d):
                sums[c][j] += p[j]
        new = [[s / counts[c] for s in sums[c]] if counts[c] else cents[c] for c in range(k)]
        if new == cents:
            break
        cents = new
    return cents


# ---------------------------------------------------------------------- observations
class ObservationStream:
    """Causal [log return, rolling realized vol] per candle; batch and incremental alike."""

    def __init__(self, window: int = 12) -> None:
        self.window = max(2, int(window))
        self.last_close: float | None = None
        self.rets: deque[float] = deque(maxlen=self.window)

    def push(self, close: float) -> list[float] | None:
        if not close > 0.0 or not math.isfinite(close):
            return None  # unusable candle: skip it without touching the state
        prev, self.last_close = self.last_close, close
        if prev is None:
            return None
        r = math.log(close / prev)
        self.rets.append(r)
        if len(self.rets) < self.window:
            return None
        m = sum(self.rets) / self.window
        v = sum((x - m) ** 2 for x in self.rets) / (self.window - 1)
        return [r, math.sqrt(v)]


def build_observations(
    candles: Sequence[Candle], window: int = 12, timeframe_ms: int | None = None
) -> tuple[list[int], list[list[float]]]:
    """(close_ts, observation) for every candle with a full volatility window."""
    stream = ObservationStream(window)
    ts_out: list[int] = []
    obs: list[list[float]] = []
    for c in candles:
        o = stream.push(c.close)
        if o is not None:
            ts_out.append(c.ts + (timeframe_ms or 0))
            obs.append(o)
    return ts_out, obs


# ---------------------------------------------------------------------- the layer
class _Filtered:
    """Running forward probabilities for one reference series (per candle)."""

    def __init__(self, window: int) -> None:
        self.obs = ObservationStream(window)
        self.alpha: list[float] | None = None
        self.ts: list[int] = []
        self.closes: list[float] = []
        self.probs: list[list[float] | None] = []

    def push(self, hmm: GaussianHMM, c: Candle) -> None:
        o = self.obs.push(c.close)
        if o is not None:
            self.alpha, _ = hmm.filter_step(self.alpha, o)
        self.ts.append(c.ts)
        self.closes.append(c.close)
        self.probs.append(self.alpha)


@register
class HmmRegimeLayer(Layer):
    name = "hmm_regime"
    kind = "regime"
    description = (
        "5-state Gaussian HMM on the reference pair's 4H log return and 12-candle realized "
        "volatility; vetoes entries when the forward-filtered P(crash) + P(bear) >= threshold"
    )
    DEFAULTS: dict[str, Any] = {
        "ref_pair": "BTC/USDT",
        "n_states": 5,  # crash, bear, neutral, bull, euphoria
        "threshold": 0.6,
        "min_candles": 1500,
        "vol_window": 12,
        "max_iter": 50,
        "tol": 1e-4,
        "var_floor": 1e-3,
        "max_stale_candles": 6,
    }

    def __init__(self, **params: Any) -> None:
        super().__init__(**{**self.DEFAULTS, **params})
        self.models: dict[str, GaussianHMM] = {}
        self.labels: dict[str, list[str]] = {}
        self.fit_until: int | None = None
        self._cache: dict[str, _Filtered] = {}

    def _p(self, key: str) -> Any:
        return self.params.get(key, self.DEFAULTS[key])

    # ------------------------------------------------------------ training
    def _fit_refs(self, view: MarketView) -> list[str]:
        ref = self._p("ref_pair")
        return [ref] if ref in view.pairs else view.pairs

    def ready(self, ctx: TrainContext) -> tuple[bool, str]:
        need = int(self._p("min_candles"))
        refs = self._fit_refs(ctx.view)
        counts = {r: len(ctx.view.history(r, ctx.until_ts)) for r in refs}
        ok = [r for r, n in counts.items() if n >= need]
        have = ", ".join(f"{r}={n}" for r, n in counts.items()) or "no pairs"
        return bool(ok), f"reference candles {have} (needs {need})"

    def fit(self, ctx: TrainContext) -> None:
        self.fit_candles(ctx.view, ctx.until_ts)

    def fit_candles(self, view: MarketView, until_ts: int) -> None:
        """Fit one HMM per reference series on candles closed by ``until_ts``."""
        need = int(self._p("min_candles"))
        self.models, self.labels, self._cache = {}, {}, {}
        for ref in self._fit_refs(view):
            hist = view.history(ref, until_ts)
            if len(hist) < need:
                continue
            _, obs = build_observations(hist, int(self._p("vol_window")))
            hmm = GaussianHMM(
                n_states=int(self._p("n_states")),
                max_iter=int(self._p("max_iter")),
                tol=float(self._p("tol")),
                var_floor=float(self._p("var_floor")),
            ).fit(obs)
            hmm.reorder(sorted(range(hmm.k), key=lambda s: (hmm.means[s][0], s)))
            self.models[ref] = hmm
            self.labels[ref] = list(state_labels(hmm.k))
        self.fit_until = until_ts

    # ------------------------------------------------------------ filtering
    def ref_for(self, pair: str, view: MarketView) -> str | None:
        ref = self._p("ref_pair")
        if ref in self.models and ref in view.pairs:
            return ref
        return pair if pair in self.models else None

    def filtered_at(
        self, ref: str, view: MarketView, close_ts: int
    ) -> tuple[list[float] | None, int | None]:
        """Filtered probs of the last ``ref`` candle closed by ``close_ts`` and its close ts."""
        tail = view.history(ref, close_ts, n=1)
        if not tail or ref not in self.models:
            return None, None
        last = tail[-1]
        f = self._cache.get(ref)
        if f is not None and f.ts:
            idx = bisect.bisect_left(f.ts, last.ts)
            if idx < len(f.ts):  # already filtered: validate, then reuse
                if f.ts[idx] == last.ts and f.closes[idx] == last.close:
                    return f.probs[idx], last.ts + view.tf
                f = None
            elif not self._extend(f, ref, view, close_ts, last.ts):
                f = None
        if f is None:
            f = _Filtered(int(self._p("vol_window")))
            for c in view.history(ref, close_ts):
                f.push(self.models[ref], c)
            self._cache[ref] = f
        return f.probs[-1], last.ts + view.tf

    def _extend(self, f: _Filtered, ref: str, view: MarketView, close_ts: int, ts: int) -> bool:
        gap = (ts - f.ts[-1]) // view.tf
        new = view.history(ref, close_ts, n=gap + 1)
        pos = next((i for i, c in enumerate(new) if c.ts == f.ts[-1]), None)
        if pos is None or new[pos].close != f.closes[-1]:
            return False
        for c in new[pos + 1 :]:
            f.push(self.models[ref], c)
        return True

    def regime_probs(self, pair: str, view: MarketView, close_ts: int) -> dict[str, float] | None:
        ref = self.ref_for(pair, view)
        if ref is None:
            return None
        probs, _ = self.filtered_at(ref, view, close_ts)
        if probs is None:
            return None
        return dict(zip(self.labels[ref], probs, strict=True))

    def veto(self, pair: str, row: FeatureRow, view: MarketView) -> tuple[bool, str]:
        ref = self.ref_for(pair, view)
        if ref is None:
            return False, f"no regime model for {pair}"
        probs, at = self.filtered_at(ref, view, row.close_ts)
        if probs is None or at is None:
            return False, f"no filtered regime yet on {ref}"
        stale = (row.close_ts - at) // view.tf
        if stale > int(self._p("max_stale_candles")):
            return False, f"{ref} regime is {stale} candles stale"
        named = dict(zip(self.labels[ref], probs, strict=True))
        bear = sum(p for lab, p in named.items() if lab in BEARISH)
        thr = float(self._p("threshold"))
        shown = " ".join(f"P({lab})={p:.2f}" for lab, p in named.items())
        if bear >= thr:
            return True, f"{ref} regime {shown}; bearish {bear:.2f} >= {thr:.2f}"
        return False, f"{ref} regime {shown}; bearish {bear:.2f} < {thr:.2f}"

    # ------------------------------------------------------------ persistence
    def state(self) -> dict[str, Any]:
        models = {}
        for ref, h in self.models.items():
            d = h.to_dict()
            d["labels"] = self.labels[ref]
            models[ref] = d
        return {"params": self.params, "fit_until": self.fit_until, "models": models}

    def load_state(self, d: Mapping[str, Any]) -> None:
        super().load_state(d)
        self.models, self.labels, self._cache = {}, {}, {}
        for ref, md in d.get("models", {}).items():
            self.models[ref] = GaussianHMM.from_dict(md)
            self.labels[ref] = list(md.get("labels") or state_labels(int(md["n_states"])))
        self.fit_until = d.get("fit_until")


def regime_series(
    view: MarketView,
    until_ts: int,
    layer: HmmRegimeLayer | None = None,
    pair: str | None = None,
    **params: Any,
) -> list[tuple[int, str, dict[str, float]]]:
    """(close_ts, most likely label, filtered probs by label) per candle for the dashboard.

    Uses ``layer`` if it is fitted, else fits a fresh ``hmm_regime`` on candles closed by
    ``until_ts``. Probabilities are forward-filtered (causal). ``pair`` picks the series
    when the reference pair is absent. Empty if there is not enough history.
    """
    if layer is None or not layer.models:
        layer = HmmRegimeLayer(**params)
        layer.fit_candles(view, until_ts)
    ref = layer.ref_for(pair or layer._p("ref_pair"), view)
    if ref is None and layer.models:
        ref = sorted(layer.models)[0]
    if ref is None:
        return []
    hmm, labels = layer.models[ref], layer.labels[ref]
    ts, obs = build_observations(view.history(ref, until_ts), int(layer._p("vol_window")), view.tf)
    probs, _ = hmm.filter(obs)
    out = []
    for t, p in zip(ts, probs, strict=True):
        best = max(range(len(p)), key=p.__getitem__)
        out.append((t, labels[best], dict(zip(labels, p, strict=True))))
    return out
