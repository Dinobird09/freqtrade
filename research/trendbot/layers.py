"""Signal layers: ML models, RL agent, regime HMM, sentiment and order flow as entry VETOES.

Every layer plugs into the bot the same way as the learned rules (``learning.py``):

- it can only VETO an entry that already passed R1-R9; it never opens a trade or raises
  risk, and it never loosens a mandatory rule;
- it must be VALIDATED before it is enforced: fit on the older 70% of the cached history
  (plus the paper / testnet / live trades that closed in that window), then applied
  unchanged to the newer 30% it never saw. It is switched on only if vetoing its signals
  raises average R there by at least ``min_gain_r`` on at least ``min_test_trades`` trades
  without vetoing more than ``max_veto_share`` of them;
- once validated it is REFIT on all data up to now and used live;
- retraining (``retrain.py``) repeats all of that on a schedule (weekly by default), so a
  layer that stops helping is switched off again automatically.

Layer interface (subclass :class:`Layer`)::

    name, kind, description           class attributes
    available() -> (bool, why)        optional dependency present? (xgboost, torch, ...)
    ready(ctx)  -> (bool, why)        enough data in ctx to train?  (else "collecting")
    fit(ctx)    -> None               train on ctx only (no data after ctx.until_ts)
    veto(pair, row, view) -> (bool, why)
                                      True blocks the entry. ``row.close_ts`` is the decision
                                      time; use ``view.history(pair, row.close_ts)`` for past
                                      candles (never later ones).
    observe(pair, features, r, ts)    optional online update after an outcome is known
    state() -> dict / load_state(d)   JSON persistence (models/<name>.json)

Layers are registered with :func:`register` and built by name from ``LAYER_TYPES``.
"""

from __future__ import annotations

import bisect
import json
import logging
import statistics
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import StrategyConfig
from .journal import ms_to_iso
from .models import CandidateOutcome, Candle, FeatureRow, NewsEvent, SignalCheck, Trade


log = logging.getLogger("trendbot.layers")

RULE_ID = "L_signal_layer"
STATUSES = ("active", "rejected", "collecting", "unavailable", "disabled", "error")


# ---------------------------------------------------------------------- data views
class MarketView:
    """Causal access to candles: ``history`` never returns a candle not closed by ``until``."""

    def __init__(self, data: Mapping[str, Sequence[Candle]], timeframe_ms: int) -> None:
        self.tf = timeframe_ms
        self._data = {p: list(c) for p, c in data.items()}
        self._ts = {p: [x.ts for x in c] for p, c in self._data.items()}

    @property
    def pairs(self) -> list[str]:
        return sorted(self._data)

    def history(self, pair: str, until_ts: int, n: int | None = None) -> list[Candle]:
        """Candles of ``pair`` whose close (ts + tf) is <= ``until_ts``; the last ``n``."""
        ts = self._ts.get(pair)
        if not ts:
            return []
        hi = bisect.bisect_right(ts, until_ts - self.tf)
        lo = 0 if n is None else max(0, hi - n)
        return self._data[pair][lo:hi]


@dataclass
class TrainContext:
    """Everything a layer may learn from. Nothing in here is later than ``until_ts``."""

    cfg: StrategyConfig
    view: MarketView
    until_ts: int
    candidates: list[CandidateOutcome]  # rule-passing signals with simulated outcomes
    trades: list[Trade]  # actual closed trades (paper / testnet / live) before until_ts
    sources: dict[str, Any] = field(default_factory=dict)  # sentiment, order flow, ...
    state_dir: Path | None = None

    def examples(self) -> list[tuple[str, int, dict[str, float], float]]:
        """(pair, signal_ts, features, r) training rows: candidates, then real trades.

        A real trade replaces the simulated candidate of the same (pair, signal_ts): paper
        and live fills are the better label.
        """
        rows: dict[tuple[str, int], tuple[str, int, dict[str, float], float]] = {}
        for c in self.candidates:
            if c.exit_ts < self.until_ts:
                rows[(c.pair, c.signal_ts)] = (c.pair, c.signal_ts, dict(c.features), c.r_multiple)
        for t in self.trades:
            if t.r_multiple is not None and t.exit_ts is not None and t.exit_ts < self.until_ts:
                rows[(t.pair, t.signal_ts)] = (t.pair, t.signal_ts, dict(t.features), t.r_multiple)
        return sorted(rows.values(), key=lambda r: (r[1], r[0]))


class Layer:
    name = "base"
    kind = "ml"
    description = ""

    def __init__(self, **params: Any) -> None:
        self.params = dict(params)

    def available(self) -> tuple[bool, str]:
        return True, "no extra dependency"

    def ready(self, ctx: TrainContext) -> tuple[bool, str]:
        n = len(ctx.examples())
        need = int(self.params.get("min_examples", 60))
        return (n >= need, f"{n} training examples (needs {need})")

    def fit(self, ctx: TrainContext) -> None:
        raise NotImplementedError

    def veto(self, pair: str, row: FeatureRow, view: MarketView) -> tuple[bool, str]:
        raise NotImplementedError

    def observe(self, pair: str, features: Mapping[str, float], r: float, ts: int) -> None:
        return None

    def state(self) -> dict[str, Any]:
        return {"params": self.params}

    def load_state(self, d: Mapping[str, Any]) -> None:
        self.params.update(d.get("params", {}))


LAYER_TYPES: dict[str, type[Layer]] = {}


def register(cls: type[Layer]) -> type[Layer]:
    LAYER_TYPES[cls.name] = cls
    return cls


def load_builtin_layers() -> dict[str, type[Layer]]:
    """Import the modules that register the built-in layers (each optional-dep safe)."""
    import importlib

    for mod in ("ml_models", "regime_hmm", "sentiment", "orderflow"):
        try:
            importlib.import_module(f"{__package__}.{mod}")
        except ImportError as exc:  # a module itself is missing: report, don't crash
            log.warning("layer module %s not importable: %s", mod, exc)
    return LAYER_TYPES


# ---------------------------------------------------------------------- entry filter
class LayerFilter:
    """``models.EntryFilter`` over one or more layers (and optional extra filters)."""

    def __init__(
        self, layers: Sequence[Layer], view: MarketView, extra: Sequence[Any] = ()
    ) -> None:
        self.layers = list(layers)
        self.view = view
        self.extra = list(extra)
        self.rule_id = RULE_ID

    def __call__(
        self, pair: str, row: FeatureRow, check: SignalCheck
    ) -> tuple[bool, float | None, str]:
        for f in self.extra:  # e.g. the learned-rule filter
            ok, prob, why = f(pair, row, check)
            if not ok:
                self.rule_id = getattr(f, "rule_id", RULE_ID)
                return False, prob, why
        for layer in self.layers:
            try:
                blocked, why = layer.veto(pair, row, self.view)
            except Exception as exc:  # a broken layer must never block or crash trading
                log.error("layer %s failed at veto: %s", layer.name, exc)
                continue
            if blocked:
                self.rule_id = RULE_ID
                return False, None, f"layer {layer.name} vetoed it: {why}"
        self.rule_id = RULE_ID
        return True, None, "no layer vetoed this signal"


# ---------------------------------------------------------------------- validation
@dataclass
class ValidationSettings:
    min_test_trades: int = 20
    min_gain_r: float = 0.05
    max_veto_share: float = 0.6
    train_frac: float = 0.7


def build_context(
    data: Mapping[str, Sequence[Candle]],
    cfg: StrategyConfig,
    until_ts: int,
    events: Iterable[NewsEvent] = (),
    trades: Sequence[Trade] = (),
    sources: Mapping[str, Any] | None = None,
    state_dir: Path | None = None,
) -> TrainContext:
    from .backtester import enumerate_candidates

    cut = {p: [c for c in cs if c.ts + cfg.timeframe_ms <= until_ts] for p, cs in data.items()}
    cands = enumerate_candidates(cut, cfg, list(events), end_ts=until_ts)
    return TrainContext(
        cfg,
        MarketView(cut, cfg.timeframe_ms),
        until_ts,
        cands,
        [t for t in trades if t.exit_ts is not None and t.exit_ts < until_ts],
        dict(sources or {}),
        state_dir,
    )


def _avg(rs: Sequence[float]) -> float:
    return statistics.fmean(rs) if rs else 0.0


def validate_layer(
    factory: Callable[[], Layer],
    data: Mapping[str, Sequence[Candle]],
    cfg: StrategyConfig,
    *,
    events: Iterable[NewsEvent] = (),
    trades: Sequence[Trade] = (),
    sources: Mapping[str, Any] | None = None,
    vs: ValidationSettings | None = None,
) -> dict[str, Any]:
    """Fit on the older part, test the veto on the newer part it never saw."""
    from .backtester import run_backtest
    from .walkforward import split_ts

    vs = vs or ValidationSettings()
    events = list(events)
    layer = factory()
    ok_dep, why_dep = layer.available()
    if not ok_dep:
        return {"status": "unavailable", "reason": why_dep}
    if not data or min(len(c) for c in data.values()) < 600:
        return {"status": "collecting", "reason": "not enough cached candle history (600+)"}
    split = split_ts(data, vs.train_frac)
    ctx = build_context(data, cfg, split, events, trades, sources)
    ready, why = layer.ready(ctx)
    if not ready:
        return {"status": "collecting", "reason": why, "split_utc": ms_to_iso(split)}
    t0 = time.time()
    layer.fit(ctx)
    view = MarketView(data, cfg.timeframe_ms)
    base = [t.r_multiple for t in run_backtest(data, cfg, events, start_ts=split).trades]
    with_layer = [
        t.r_multiple
        for t in run_backtest(
            data, cfg, events, start_ts=split, entry_filter=LayerFilter([layer], view)
        ).trades
    ]
    gain = _avg(with_layer) - _avg(base)
    vetoed = 1 - len(with_layer) / len(base) if base else 0.0
    ok = (
        len(with_layer) >= vs.min_test_trades
        and gain >= vs.min_gain_r
        and vetoed <= vs.max_veto_share
    )
    reason = (
        f"on the unseen newer {1 - vs.train_frac:.0%}: avg R {_avg(base):+.3f} -> "
        f"{_avg(with_layer):+.3f} ({gain:+.3f}R) over {len(with_layer)} of {len(base)} "
        f"trades ({vetoed:.0%} vetoed)"
    )
    if not ok:
        reason += (
            f"; needs >= {vs.min_test_trades} trades, a gain >= {vs.min_gain_r}R and "
            f"<= {vs.max_veto_share:.0%} vetoed"
        )
    return {
        "status": "active" if ok else "rejected",
        "reason": reason,
        "split_utc": ms_to_iso(split),
        "test_base_n": len(base),
        "test_base_avg_r": round(_avg(base), 4),
        "test_layer_n": len(with_layer),
        "test_layer_avg_r": round(_avg(with_layer), 4),
        "gain_r": round(gain, 4),
        "vetoed_share": round(vetoed, 4),
        "fit_seconds": round(time.time() - t0, 2),
    }


# ---------------------------------------------------------------------- registry
class LayerBook:
    """layers.json + models/<name>.json in the bot's state dir; builds the live filter."""

    def __init__(
        self,
        state_dir: Path,
        cfg: StrategyConfig,
        enabled: Sequence[str] | None = None,
        params: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> None:
        self.dir = Path(state_dir)
        self.cfg = cfg
        load_builtin_layers()
        self.enabled = list(enabled) if enabled is not None else sorted(LAYER_TYPES)
        self.params = {k: dict(v) for k, v in (params or {}).items()}
        self.registry: dict[str, Any] = {"layers": {}, "overrides": {}}
        self.layers: dict[str, Layer] = {}
        self._mtime = 0.0
        self.reload()

    @property
    def path(self) -> Path:
        return self.dir / "layers.json"

    def model_path(self, name: str) -> Path:
        return self.dir / "models" / f"{name}.json"

    def make(self, name: str) -> Layer:
        return LAYER_TYPES[name](**self.params.get(name, {}))

    def reload(self, force: bool = False) -> bool:
        """Re-read layers.json and the active models if they changed on disk."""
        if not self.path.exists():
            return False
        mtime = self.path.stat().st_mtime
        if not force and mtime == self._mtime:
            return False
        self._mtime = mtime
        self.registry = json.loads(self.path.read_text(encoding="utf-8"))
        self.registry.setdefault("overrides", {})
        self.layers = {}
        for name in self.registry.get("layers", {}):
            if self.status(name) != "active" or name not in LAYER_TYPES:
                continue
            mp = self.model_path(name)
            if not mp.exists():
                continue
            layer = self.make(name)
            try:
                layer.load_state(json.loads(mp.read_text(encoding="utf-8")))
            except (ValueError, KeyError, OSError) as exc:
                log.error("layer %s model could not be loaded: %s", name, exc)
                continue
            self.layers[name] = layer
        return True

    def status(self, name: str) -> str:
        override = self.registry.get("overrides", {}).get(name)
        if override == "disabled":
            return "disabled"
        return self.registry.get("layers", {}).get(name, {}).get("status", "collecting")

    def set_override(self, name: str, status: str | None) -> None:
        self.registry = set_layer_override(self.dir, name, status)
        self.reload(force=True)

    def filter(self, view: MarketView, extra: Sequence[Any] = ()) -> LayerFilter:
        return LayerFilter([self.layers[n] for n in sorted(self.layers)], view, extra)

    def observe(self, pair: str, features: Mapping[str, float], r: float, ts: int) -> None:
        for layer in self.layers.values():
            try:
                layer.observe(pair, features, r, ts)
            except Exception as exc:
                log.error("layer %s observe failed: %s", layer.name, exc)

    def retrain(
        self,
        data: Mapping[str, Sequence[Candle]],
        *,
        now_ms: int,
        events: Iterable[NewsEvent] = (),
        trades: Sequence[Trade] = (),
        sources: Mapping[str, Any] | None = None,
        vs: ValidationSettings | None = None,
    ) -> dict[str, Any]:
        """Validate every enabled layer, refit the passing ones on all data, persist."""
        events = list(events)
        out: dict[str, Any] = {}
        for name in self.enabled:
            if name not in LAYER_TYPES:
                out[name] = {"status": "unavailable", "reason": "unknown layer"}
                continue
            try:
                res = validate_layer(
                    lambda n=name: self.make(n),
                    data,
                    self.cfg,
                    events=events,
                    trades=trades,
                    sources=sources,
                    vs=vs,
                )
                if res["status"] == "active":
                    layer = self.make(name)
                    layer.fit(
                        build_context(data, self.cfg, now_ms, events, trades, sources, self.dir)
                    )
                    self.model_path(name).parent.mkdir(parents=True, exist_ok=True)
                    _atomic(self.model_path(name), json.dumps(layer.state()))
            except Exception as exc:  # one bad layer never stops the others
                log.exception("layer %s failed during retraining", name)
                res = {"status": "error", "reason": f"{type(exc).__name__}: {exc}"}
            res["trained_utc"] = ms_to_iso(now_ms)
            res["kind"] = LAYER_TYPES[name].kind if name in LAYER_TYPES else "?"
            res["description"] = LAYER_TYPES[name].description if name in LAYER_TYPES else ""
            out[name] = res
            log.info("layer %s: %s (%s)", name, res["status"], res.get("reason"))
        if self.path.exists():  # keep operator overrides made while retraining ran
            self.registry["overrides"] = json.loads(self.path.read_text(encoding="utf-8")).get(
                "overrides", {}
            )
        self.registry["layers"] = out
        self.registry["retrained_utc"] = ms_to_iso(now_ms)
        self._write_registry()
        self.reload(force=True)
        return out

    def _write_registry(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        _atomic(self.path, json.dumps(self.registry, indent=1))


def _atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def set_layer_override(state_dir: Path, name: str, status: str | None) -> dict[str, Any]:
    """Operator switch for one layer, written straight into layers.json (the bot reloads it).

    ``status`` is ``"disabled"`` or ``None`` (automatic: the validation result decides).
    """
    if status not in ("disabled", None):
        raise ValueError("a layer override may only be 'disabled' or None (automatic)")
    path = Path(state_dir) / "layers.json"
    reg = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"layers": {}}
    ov = reg.setdefault("overrides", {})
    if status is None:
        ov.pop(name, None)
    else:
        ov[name] = status
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic(path, json.dumps(reg, indent=1))
    return reg
