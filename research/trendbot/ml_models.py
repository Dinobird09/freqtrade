"""ML signal layers: gradient-boosted trees ("gbm"), an LSTM ("lstm") and a bandit ("rl").

All three are :class:`layers.Layer` vetoes (they can only block an entry that passed every
mandatory rule) and all three are causal: a decision at ``row.close_ts`` only reads
``view.history(pair, row.close_ts)``; a training example with signal candle ``signal_ts``
reads ``ctx.view.history(pair, signal_ts + timeframe_ms)`` (the signal candle's close).

gbm   P(win) (label ``r > 0``) from a small deterministic pure-Python gradient-boosting
      classifier (logistic loss, Newton leaves, shallow trees), or ``xgboost.XGBClassifier``
      with equivalent settings when xgboost is importable and ``use_xgboost`` is not False.
      Vetoes when ``p < p* + margin``, ``p*`` being the break-even win probability of the
      TRAIN examples' average win / loss R (``ml_filter.breakeven_probability``).
lstm  P(win) from a small PyTorch LSTM over the last ``seq_len`` candles' normalised log
      return, range % and log volume ratio; same break-even veto. Needs torch (imported
      lazily, only inside methods); without it the layer reports itself unavailable.
rl    A contextual bandit with tabular Q-learning: state = tercile bins (fitted on TRAIN) of
      vol_ratio, rsi, dist_regime_pct plus a 6-hour session bucket; actions take / skip,
      reward = realised R of taking (skip = 0). Q(take) is an (optionally exponentially
      forgetting) incremental mean per state; it vetoes only when a state has at least
      ``n_min`` outcomes and ``mean + 1.645 * SE < 0``. ``observe`` keeps learning online.

A layer that cannot score a signal (missing features or history) never vetoes it.

gbm defaults (100 trees, depth 2, learning rate 0.05, >= 10 rows per leaf, leaf ridge l2 = 10,
subsample 0.8) were chosen among 8 settings on synthetic seeds 1-6 and checked on seeds 7-12:
``validate_layer`` activated it on 6 of 12 "hour_edge" worlds and 0 of 12 "null" worlds
(with l2 = 1 it was active on 2 of 6 null worlds: too little shrinkage for ~250 labels).
"""

from __future__ import annotations

import base64
import bisect
import importlib.util
import io
import math
import random
import statistics
from collections.abc import Mapping, Sequence
from typing import Any

from .layers import Layer, MarketView, TrainContext, register
from .ml_filter import breakeven_probability, feature_vector
from .models import Candle, FeatureRow


LOGIT_CLIP = 30.0
EXTRA_LOOKBACK = 25  # candles needed by the causal extras (24-candle return + current)
GBM_FEATURES = (
    "rsi",
    "vol_ratio",
    "ema_gap_pct",
    "dist_regime_pct",
    "hour_sin",
    "hour_cos",
    "ret6_pct",
    "ret24_pct",
    "atr14_pct",
    "vol_z",
)


def _sigmoid(z: float) -> float:
    z = max(-LOGIT_CLIP, min(LOGIT_CLIP, z))
    return 1.0 / (1.0 + math.exp(-z))


def _module_available(name: str) -> bool:
    """True if ``name`` could be imported, WITHOUT importing it."""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


# ---------------------------------------------------------------------- break-even rule
def breakeven_from(rs: Sequence[float]) -> dict[str, float] | None:
    """``{p_star, avg_win_r, avg_loss_r}`` of TRAIN outcomes, or None without wins and losses."""
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    if not wins or not losses:
        return None
    avg_win, avg_loss = statistics.fmean(wins), statistics.fmean(losses)
    return {
        "p_star": breakeven_probability(avg_win, avg_loss),
        "avg_win_r": avg_win,
        "avg_loss_r": avg_loss,
    }


def _prob_decision(name: str, p: float, be: Mapping[str, float] | None, margin: float):
    if be is None:
        return False, f"{name}: no break-even (TRAIN had no wins or no losses), never vetoes"
    thr = be["p_star"] + margin
    basis = (
        f"break-even {be['p_star']:.3f}{f' + margin {margin:.3f}' if margin else ''} from "
        f"TRAIN avg win {be['avg_win_r']:+.2f}R / loss {be['avg_loss_r']:+.2f}R"
    )
    if p < thr:
        return True, f"{name} win probability {p:.3f} is below the {basis}"
    return False, f"{name} win probability {p:.3f} is at or above the {basis}"


# ---------------------------------------------------------------------- causal extras
def candle_extras(cs: Sequence[Candle]) -> list[float] | None:
    """6/24-candle return %, 14-candle ATR % of price, volume z-score vs the previous 20.

    ``cs`` are the candles up to and including the decision candle (oldest first).
    """
    if len(cs) < EXTRA_LOOKBACK:
        return None
    last = cs[-1]
    c6, c24 = cs[-7].close, cs[-25].close
    if last.close <= 0 or c6 <= 0 or c24 <= 0:
        return None
    trs = [
        max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close))
        for p, c in zip(cs[-15:-1], cs[-14:], strict=True)
    ]
    prev_vol = [c.volume for c in cs[-21:-1]]
    mu = statistics.fmean(prev_vol)
    sd = statistics.pstdev(prev_vol, mu)
    vec = [
        (last.close / c6 - 1.0) * 100.0,
        (last.close / c24 - 1.0) * 100.0,
        statistics.fmean(trs) / last.close * 100.0,
        (last.volume - mu) / sd if sd > 0 else 0.0,
    ]
    return vec if all(math.isfinite(v) for v in vec) else None


class _ExtrasCache:
    """Per-(pair, decision ts) extras for ONE view (reset when a different view is used)."""

    def __init__(self) -> None:
        self._view: MarketView | None = None
        self._memo: dict[tuple[str, int], list[float] | None] = {}

    def get(self, view: MarketView, pair: str, until_ts: int) -> list[float] | None:
        if view is not self._view:
            self._view, self._memo = view, {}
        key = (pair, until_ts)
        if key not in self._memo:
            self._memo[key] = candle_extras(view.history(pair, until_ts, EXTRA_LOOKBACK))
        return self._memo[key]


def gbm_vector(
    features: Mapping[str, float] | None, extras: Sequence[float] | None
) -> list[float] | None:
    base = feature_vector(features)
    if base is None or extras is None:
        return None
    return [*base, *extras]


# ---------------------------------------------------------------------- boosting (stdlib)
class GradientBoostingClassifier:
    """Deterministic binary gradient boosting: logistic loss, second-order (Newton) trees.

    Each tree is fitted to the gradient / hessian of the log-loss at the current score;
    a split maximises ``GL^2/(HL+l2) + GR^2/(HR+l2) - G^2/(H+l2)`` over exact thresholds
    (midpoints of distinct sorted values), leaves are ``-G/(H+l2)`` scaled by the learning
    rate. ``subsample < 1`` draws rows per tree without replacement from
    ``random.Random(seed)``. Trees are plain nested dicts, so the model is JSON.
    """

    def __init__(
        self,
        n_estimators: int = 100,
        learning_rate: float = 0.05,
        max_depth: int = 2,
        min_samples_leaf: int = 10,
        l2: float = 1.0,
        subsample: float = 0.8,
        seed: int = 0,
    ) -> None:
        self.n_estimators = int(n_estimators)
        self.learning_rate = float(learning_rate)
        self.max_depth = int(max_depth)
        self.min_samples_leaf = max(1, int(min_samples_leaf))
        self.l2 = float(l2)
        self.subsample = float(subsample)
        self.seed = int(seed)
        self.base_score = 0.0
        self.trees: list[dict[str, Any]] = []

    # -- fitting
    def fit(self, x: Sequence[Sequence[float]], y: Sequence[float]) -> GradientBoostingClassifier:
        if not x or len(x) != len(y):
            raise ValueError("need a non-empty X with one label per row")
        rows = [list(map(float, r)) for r in x]
        labels = [1.0 if v > 0.5 else 0.0 for v in y]
        n = len(rows)
        rate = min(max(statistics.fmean(labels), 1e-6), 1 - 1e-6)
        self.base_score = math.log(rate / (1 - rate))
        self.trees = []
        score = [self.base_score] * n
        rng = random.Random(self.seed)
        k = max(2 * self.min_samples_leaf, round(self.subsample * n))
        for _ in range(self.n_estimators):
            idx = sorted(rng.sample(range(n), k)) if k < n else list(range(n))
            p = [_sigmoid(s) for s in score]
            g = [pi - yi for pi, yi in zip(p, labels, strict=True)]
            h = [max(pi * (1 - pi), 1e-12) for pi in p]
            tree = self._grow(rows, g, h, idx, 0)
            self.trees.append(tree)
            for i in range(n):
                score[i] += _tree_value(tree, rows[i])
        return self

    def _grow(self, rows, g, h, idx: list[int], depth: int) -> dict[str, Any]:
        gs, hs = math.fsum(g[i] for i in idx), math.fsum(h[i] for i in idx)
        leaf = {"v": -gs / (hs + self.l2) * self.learning_rate}
        if depth >= self.max_depth or len(idx) < 2 * self.min_samples_leaf:
            return leaf
        best = self._best_split(rows, g, h, idx, gs, hs)
        if best is None:
            return leaf
        f, thr = best
        left = [i for i in idx if rows[i][f] <= thr]
        right = [i for i in idx if rows[i][f] > thr]
        return {
            "f": f,
            "t": thr,
            "l": self._grow(rows, g, h, left, depth + 1),
            "r": self._grow(rows, g, h, right, depth + 1),
        }

    def _best_split(self, rows, g, h, idx, gs, hs) -> tuple[int, float] | None:
        lam, m = self.l2, self.min_samples_leaf
        parent = gs * gs / (hs + lam)
        best_gain, best = 1e-12, None
        for f in range(len(rows[idx[0]])):
            order = sorted(idx, key=lambda i, f=f: rows[i][f])
            gl = hl = 0.0
            for pos in range(len(order) - m):
                i = order[pos]
                gl += g[i]
                hl += h[i]
                if pos + 1 < m:
                    continue
                a, b = rows[i][f], rows[order[pos + 1]][f]
                if a == b:
                    continue
                gr, hr = gs - gl, hs - hl
                gain = gl * gl / (hl + lam) + gr * gr / (hr + lam) - parent
                if gain > best_gain:
                    best_gain, best = gain, (f, (a + b) / 2.0)
        return best

    # -- prediction
    def decision(self, row: Sequence[float]) -> float:
        return self.base_score + math.fsum(_tree_value(t, row) for t in self.trees)

    def predict_proba(self, x: Sequence[Sequence[float]]) -> list[float]:
        return [_sigmoid(self.decision(r)) for r in x]

    # -- persistence
    def to_dict(self) -> dict[str, Any]:
        return {
            "n_estimators": self.n_estimators,
            "learning_rate": self.learning_rate,
            "max_depth": self.max_depth,
            "min_samples_leaf": self.min_samples_leaf,
            "l2": self.l2,
            "subsample": self.subsample,
            "seed": self.seed,
            "base_score": self.base_score,
            "trees": self.trees,
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> GradientBoostingClassifier:
        m = cls(
            d["n_estimators"],
            d["learning_rate"],
            d["max_depth"],
            d["min_samples_leaf"],
            d["l2"],
            d["subsample"],
            d["seed"],
        )
        m.base_score = float(d["base_score"])
        m.trees = [dict(t) for t in d["trees"]]
        return m


def _tree_value(node: Mapping[str, Any], row: Sequence[float]) -> float:
    while "v" not in node:
        node = node["l"] if row[node["f"]] <= node["t"] else node["r"]
    return node["v"]


# ---------------------------------------------------------------------- xgboost (optional)
class _XGBModel:
    """xgboost.XGBClassifier with the stdlib model's settings; JSON via the booster dump."""

    def __init__(self, booster: Any) -> None:
        self.booster = booster

    @classmethod
    def fit(cls, x, y, params: Mapping[str, Any]) -> _XGBModel:
        import xgboost

        clf = xgboost.XGBClassifier(
            n_estimators=int(params["n_estimators"]),
            learning_rate=float(params["learning_rate"]),
            max_depth=int(params["max_depth"]),
            min_child_weight=0.0,
            reg_lambda=float(params["l2"]),
            subsample=float(params["subsample"]),
            random_state=int(params["seed"]),
            n_jobs=1,
            tree_method="exact",
            objective="binary:logistic",
        )
        clf.fit(x, [int(v) for v in y])
        return cls(clf.get_booster())

    def predict_proba(self, x: Sequence[Sequence[float]]) -> list[float]:
        import xgboost

        return [float(p) for p in self.booster.predict(xgboost.DMatrix([list(r) for r in x]))]

    def to_dict(self) -> dict[str, Any]:
        import json

        return {"xgboost_json": json.loads(bytes(self.booster.save_raw(raw_format="json")))}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> _XGBModel:
        import json

        import xgboost

        booster = xgboost.Booster()
        booster.load_model(bytearray(json.dumps(d["xgboost_json"]).encode("utf-8")))
        return cls(booster)


# ---------------------------------------------------------------------- gbm layer
_GBM_DEFAULTS: dict[str, Any] = {
    "n_estimators": 100,
    "learning_rate": 0.05,
    "max_depth": 2,
    "min_samples_leaf": 10,
    "l2": 10.0,  # strong leaf ridge: a few hundred noisy labels (see module docstring)
    "subsample": 0.8,
    "seed": 0,
    "margin": 0.0,
    "min_examples": 60,
    "min_per_class": 10,
}


def _class_counts(ctx: TrainContext) -> tuple[int, int]:
    rs = [e[3] for e in ctx.examples()]
    wins = sum(1 for r in rs if r > 0)
    return wins, len(rs) - wins


@register
class GBMLayer(Layer):
    name = "gbm"
    kind = "ml"
    description = (
        "Gradient-boosted trees for P(win) on the entry features plus causal 6/24-candle "
        "return, ATR% and volume z-score; vetoes below the TRAIN break-even probability"
    )

    def __init__(self, **params: Any) -> None:
        super().__init__(**{**_GBM_DEFAULTS, **params})
        self.model: GradientBoostingClassifier | _XGBModel | None = None
        self.breakeven: dict[str, float] | None = None
        self.backend = "stdlib"
        self.train_n = 0
        self._cache = _ExtrasCache()

    def _use_xgboost(self) -> bool:
        return self.params.get("use_xgboost") is not False and _module_available("xgboost")

    def ready(self, ctx: TrainContext) -> tuple[bool, str]:
        ok, why = super().ready(ctx)
        if not ok:
            return ok, why
        wins, losses = _class_counts(ctx)
        need = int(self.params["min_per_class"])
        if min(wins, losses) < need:
            return False, f"{wins} wins / {losses} losses (needs {need} of each)"
        return True, why

    def vector(self, pair: str, features, view: MarketView, until_ts: int) -> list[float] | None:
        return gbm_vector(features, self._cache.get(view, pair, until_ts))

    def fit(self, ctx: TrainContext) -> None:
        tf = ctx.cfg.timeframe_ms
        x: list[list[float]] = []
        rs: list[float] = []
        for pair, sig_ts, feats, r in ctx.examples():
            vec = self.vector(pair, feats, ctx.view, sig_ts + tf)
            if vec is not None and math.isfinite(r):
                x.append(vec)
                rs.append(float(r))
        self.train_n = len(x)
        self.breakeven = breakeven_from(rs)
        if self.breakeven is None:
            self.model = None
            return
        y = [1.0 if r > 0 else 0.0 for r in rs]
        if self._use_xgboost():
            self.backend, self.model = "xgboost", _XGBModel.fit(x, y, self.params)
        else:
            p = self.params
            self.backend = "stdlib"
            self.model = GradientBoostingClassifier(
                p["n_estimators"],
                p["learning_rate"],
                p["max_depth"],
                p["min_samples_leaf"],
                p["l2"],
                p["subsample"],
                p["seed"],
            ).fit(x, y)

    def probability(self, pair: str, row: FeatureRow, view: MarketView) -> float | None:
        if self.model is None:
            return None
        vec = self.vector(pair, row.ml_features(), view, row.close_ts)
        return None if vec is None else self.model.predict_proba([vec])[0]

    def veto(self, pair: str, row: FeatureRow, view: MarketView) -> tuple[bool, str]:
        if self.model is None:
            return False, "gbm is not fitted (no usable TRAIN wins and losses), never vetoes"
        p = self.probability(pair, row, view)
        if p is None:
            return False, "gbm cannot score this signal (missing features or history)"
        return _prob_decision("gbm", p, self.breakeven, float(self.params["margin"]))

    def state(self) -> dict[str, Any]:
        return {
            "params": self.params,
            "backend": self.backend,
            "features": list(GBM_FEATURES),
            "train_n": self.train_n,
            "breakeven": self.breakeven,
            "model": None if self.model is None else self.model.to_dict(),
        }

    def load_state(self, d: Mapping[str, Any]) -> None:
        super().load_state(d)
        self.backend = d.get("backend", "stdlib")
        self.train_n = int(d.get("train_n", 0))
        self.breakeven = dict(d["breakeven"]) if d.get("breakeven") else None
        m = d.get("model")
        if m is None:
            self.model = None
        elif self.backend == "xgboost":
            self.model = _XGBModel.from_dict(m)
        else:
            self.model = GradientBoostingClassifier.from_dict(m)
        self._cache = _ExtrasCache()


# ---------------------------------------------------------------------- lstm layer
_LSTM_DEFAULTS: dict[str, Any] = {
    "seq_len": 32,
    "hidden": 16,
    "epochs": 30,
    "patience": 5,
    "lr": 5e-3,
    "weight_decay": 1e-4,
    "batch_size": 32,
    "val_frac": 0.2,
    "seed": 0,
    "margin": 0.0,
    "min_examples": 100,
}
_VOL_LOOKBACK = 20


def sequence_channels(cs: Sequence[Candle], seq_len: int) -> list[list[float]] | None:
    """Per candle of the last ``seq_len``: [log return %, range %, log volume ratio].

    Needs ``seq_len + 20`` candles (the volume ratio uses the previous 20 volumes).
    """
    if len(cs) < seq_len + _VOL_LOOKBACK:
        return None
    out: list[list[float]] = []
    for i in range(len(cs) - seq_len, len(cs)):
        c, prev = cs[i], cs[i - 1]
        vols = [x.volume for x in cs[i - _VOL_LOOKBACK : i]]
        mv = statistics.fmean(vols)
        if c.close <= 0 or prev.close <= 0:
            return None
        vr = math.log(c.volume / mv) if c.volume > 0 and mv > 0 else 0.0
        out.append([math.log(c.close / prev.close) * 100, (c.high - c.low) / c.close * 100, vr])
    return out


def _torch_net(torch: Any, hidden: int) -> Any:
    class Net(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.lstm = torch.nn.LSTM(3, hidden, batch_first=True)
            self.head = torch.nn.Linear(hidden, 1)

        def forward(self, x: Any) -> Any:
            out, _ = self.lstm(x)
            return self.head(out[:, -1, :]).squeeze(-1)

    return Net()


@register
class LSTMLayer(Layer):
    name = "lstm"
    kind = "ml"
    description = (
        "Small PyTorch LSTM over the last candles' log return, range% and volume ratio for "
        "P(win); vetoes below the TRAIN break-even probability (needs torch)"
    )

    def __init__(self, **params: Any) -> None:
        super().__init__(**{**_LSTM_DEFAULTS, **params})
        self.net: Any = None
        self.norm: dict[str, list[float]] | None = None
        self.breakeven: dict[str, float] | None = None
        self.weights_b64: str | None = None
        self.train_n = 0

    def available(self) -> tuple[bool, str]:
        if not _module_available("torch"):
            return False, "pip install torch"
        return True, "torch is installed"

    def _seq(self, view: MarketView, pair: str, until_ts: int) -> list[list[float]] | None:
        n = int(self.params["seq_len"])
        return sequence_channels(view.history(pair, until_ts, n + _VOL_LOOKBACK), n)

    def _normalise(self, seqs: Sequence[list[list[float]]]) -> list[list[list[float]]]:
        assert self.norm is not None
        mu, sd = self.norm["mean"], self.norm["std"]
        return [
            [[(v - m) / s for v, m, s in zip(st, mu, sd, strict=True)] for st in q] for q in seqs
        ]

    def fit(self, ctx: TrainContext) -> None:
        import torch

        tf = ctx.cfg.timeframe_ms
        seqs, rs = [], []
        for pair, sig_ts, _feats, r in ctx.examples():
            q = self._seq(ctx.view, pair, sig_ts + tf)
            if q is not None and math.isfinite(r):
                seqs.append(q)
                rs.append(float(r))
        self.train_n = len(seqs)
        self.breakeven = breakeven_from(rs)
        self.net = None
        if self.breakeven is None or len(seqs) < 10:
            return
        n_val = max(1, int(len(seqs) * float(self.params["val_frac"])))
        inner = seqs[:-n_val]  # chronological: examples are sorted by signal time
        cols = list(zip(*(st for q in inner for st in q), strict=True))
        means = [statistics.fmean(c) for c in cols]
        stds = [statistics.pstdev(c, m) or 1.0 for c, m in zip(cols, means, strict=True)]
        self.norm = {"mean": means, "std": stds}
        x = torch.tensor(self._normalise(seqs), dtype=torch.float32)
        y = torch.tensor([1.0 if r > 0 else 0.0 for r in rs], dtype=torch.float32)
        self.net = self._train(torch, x[:-n_val], y[:-n_val], x[-n_val:], y[-n_val:])
        buf = io.BytesIO()
        torch.save(self.net.state_dict(), buf)
        self.weights_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    def _train(self, torch: Any, xt: Any, yt: Any, xv: Any, yv: Any) -> Any:
        p = self.params
        torch.manual_seed(int(p["seed"]))
        torch.set_num_threads(1)
        net = _torch_net(torch, int(p["hidden"]))
        opt = torch.optim.Adam(
            net.parameters(), lr=float(p["lr"]), weight_decay=float(p["weight_decay"])
        )
        loss_fn = torch.nn.BCEWithLogitsLoss()
        gen = torch.Generator().manual_seed(int(p["seed"]))
        best, best_state, bad = math.inf, None, 0
        bs = int(p["batch_size"])
        for _ in range(int(p["epochs"])):
            net.train()
            perm = torch.randperm(len(xt), generator=gen)
            for i in range(0, len(xt), bs):
                b = perm[i : i + bs]
                opt.zero_grad()
                loss_fn(net(xt[b]), yt[b]).backward()
                opt.step()
            net.eval()
            with torch.no_grad():
                val = float(loss_fn(net(xv), yv))
            if val < best - 1e-6:
                best, bad = val, 0
                best_state = {k: v.detach().clone() for k, v in net.state_dict().items()}
            else:
                bad += 1
                if bad >= int(p["patience"]):
                    break
        if best_state is not None:
            net.load_state_dict(best_state)
        net.eval()
        return net

    def probability(self, pair: str, row: FeatureRow, view: MarketView) -> float | None:
        if self.net is None or self.norm is None:
            return None
        q = self._seq(view, pair, row.close_ts)
        if q is None:
            return None
        import torch

        with torch.no_grad():
            x = torch.tensor(self._normalise([q]), dtype=torch.float32)
            return float(torch.sigmoid(self.net(x))[0])

    def veto(self, pair: str, row: FeatureRow, view: MarketView) -> tuple[bool, str]:
        p = self.probability(pair, row, view)
        if p is None:
            return False, "lstm cannot score this signal (not fitted or not enough history)"
        return _prob_decision("lstm", p, self.breakeven, float(self.params["margin"]))

    def state(self) -> dict[str, Any]:
        return {
            "params": self.params,
            "train_n": self.train_n,
            "breakeven": self.breakeven,
            "norm": self.norm,
            "weights_b64": self.weights_b64,
        }

    def load_state(self, d: Mapping[str, Any]) -> None:
        super().load_state(d)
        self.train_n = int(d.get("train_n", 0))
        self.breakeven = dict(d["breakeven"]) if d.get("breakeven") else None
        self.norm = dict(d["norm"]) if d.get("norm") else None
        self.weights_b64 = d.get("weights_b64")
        self.net = None
        if self.weights_b64 and self.norm is not None:
            import torch

            raw = io.BytesIO(base64.b64decode(self.weights_b64))
            try:
                sd = torch.load(raw, map_location="cpu", weights_only=True)
            except TypeError:  # torch < 1.13 has no weights_only
                raw.seek(0)
                sd = torch.load(raw, map_location="cpu")
            net = _torch_net(torch, int(self.params["hidden"]))
            net.load_state_dict(sd)
            net.eval()
            self.net = net


# ---------------------------------------------------------------------- rl (bandit) layer
_RL_DEFAULTS: dict[str, Any] = {"n_min": 8, "z": 1.645, "decay": 1.0, "min_examples": 60}
RL_BINNED = ("vol_ratio", "rsi", "dist_regime_pct")
SESSIONS = ("asia", "europe", "us", "late")  # close hour 0-5, 6-11, 12-17, 18-23 UTC


class QStat:
    """Weighted incremental mean / variance of R with exponential forgetting ``decay``."""

    __slots__ = ("m2", "mean", "n", "w", "w2")

    def __init__(self, n: int = 0, w: float = 0.0, w2: float = 0.0, mean=0.0, m2=0.0) -> None:
        self.n, self.w, self.w2, self.mean, self.m2 = int(n), w, w2, mean, m2

    def update(self, r: float, decay: float = 1.0) -> None:
        self.w *= decay
        self.w2 *= decay * decay
        self.m2 *= decay
        self.n += 1
        self.w += 1.0
        self.w2 += 1.0
        delta = r - self.mean
        self.mean += delta / self.w
        self.m2 += delta * (r - self.mean)

    def stderr(self) -> float:
        """Standard error of the weighted mean (effective n = w^2 / w2); inf below 2 obs."""
        if self.n < 2 or self.w <= 0:
            return math.inf
        denom = self.w - self.w2 / self.w
        if denom <= 1e-12:
            return math.inf
        var = max(self.m2 / denom, 0.0)
        return math.sqrt(var * self.w2) / self.w

    def to_list(self) -> list[float]:
        return [self.n, self.w, self.w2, self.mean, self.m2]


@register
class RLLayer(Layer):
    name = "rl"
    kind = "ml"
    description = (
        "Contextual-bandit Q-learning over tercile bins of vol_ratio, rsi and regime distance "
        "plus the session: skips states whose take-reward is confidently negative; learns "
        "online from every closed trade"
    )

    def __init__(self, **params: Any) -> None:
        super().__init__(**{**_RL_DEFAULTS, **params})
        decay = float(self.params["decay"])
        if not 0.0 < decay <= 1.0:
            raise ValueError(f"decay must be in (0, 1] (got {decay})")
        self.edges: dict[str, list[float]] | None = None
        self.q: dict[str, QStat] = {}
        self.seen: set[tuple[str, int]] = set()

    def state_key(self, features: Mapping[str, float] | None) -> str | None:
        if not features or self.edges is None:
            return None
        parts = []
        try:
            for name in RL_BINNED:
                v = float(features[name])
                if not math.isfinite(v):
                    return None
                parts.append(f"{name}={bisect.bisect_left(self.edges[name], v)}")
            hour = int(float(features["hour_utc"])) % 24
        except (KeyError, TypeError, ValueError):
            return None
        return "|".join([*parts, SESSIONS[hour // 6]])

    def _update(self, features: Mapping[str, float], r: float) -> bool:
        key = self.state_key(features)
        if key is None or not math.isfinite(r):
            return False
        self.q.setdefault(key, QStat()).update(float(r), float(self.params["decay"]))
        return True

    def fit(self, ctx: TrainContext) -> None:
        ex = ctx.examples()
        edges: dict[str, list[float]] = {}
        for name in RL_BINNED:
            vals = sorted(
                float(f[name])
                for _, _, f, _ in ex
                if f.get(name) is not None and math.isfinite(float(f[name]))
            )
            edges[name] = statistics.quantiles(vals, n=3) if len(vals) >= 3 else []
        self.edges, self.q, self.seen = edges, {}, set()
        for pair, ts, feats, r in ex:  # chronological, so forgetting favours recent outcomes
            if self._update(feats, r):
                self.seen.add((pair, ts))

    def observe(self, pair: str, features: Mapping[str, float], r: float, ts: int) -> None:
        if (pair, int(ts)) in self.seen:
            return
        if self._update(features, r):
            self.seen.add((pair, int(ts)))

    def q_take(self, features: Mapping[str, float] | None) -> QStat | None:
        key = self.state_key(features)
        return None if key is None else self.q.get(key)

    def veto(self, pair: str, row: FeatureRow, view: MarketView) -> tuple[bool, str]:
        key = self.state_key(row.ml_features())
        if key is None:
            return False, "rl cannot place this signal in a state (unfitted or missing features)"
        st = self.q.get(key)
        n_min = int(self.params["n_min"])
        if st is None or st.n < n_min:
            return False, f"rl state {key} has {0 if st is None else st.n} outcomes (< {n_min})"
        upper = st.mean + float(self.params["z"]) * st.stderr()
        desc = f"rl state {key}: Q(take) {st.mean:+.3f}R over {st.n}, upper bound {upper:+.3f}R"
        if upper < 0:
            return True, f"{desc} < 0 = Q(skip), so skipping is confidently better"
        return False, f"{desc} is not confidently below Q(skip) = 0"

    def state(self) -> dict[str, Any]:
        return {
            "params": self.params,
            "edges": self.edges,
            "q": {k: v.to_list() for k, v in sorted(self.q.items())},
            "seen": sorted([p, t] for p, t in self.seen),
        }

    def load_state(self, d: Mapping[str, Any]) -> None:
        super().load_state(d)
        self.edges = {k: list(v) for k, v in d["edges"].items()} if d.get("edges") else None
        self.q = {k: QStat(*v) for k, v in d.get("q", {}).items()}
        self.seen = {(str(p), int(t)) for p, t in d.get("seen", [])}
