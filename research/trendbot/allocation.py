"""Regime-based allocation: a 5-state HMM decides how much capital may be in the market.

The market regime comes from a Gaussian HMM (regime_hmm.py) with five states, Crash, Bear,
Neutral, Bull and Euphoria, fitted on the reference series (BTC/USDT: crypto's market
leader, so one regime for every pair). Only FORWARD-FILTERED probabilities are used:
P(state at t | data up to t). There is no ``.predict()`` over the whole series and no
smoothing, so nothing from the future leaks in.

Persistence: a new regime is CONFIRMED only once it has been the most likely state for 3
consecutive 4H bars; until then the previous confirmed regime stays in force.

Exposure caps (share of equity that open positions may use, at cost)::

    euphoria 95%   bull 95%   neutral 50%   bear 25%   crash 0%

The cap only ever SHRINKS or SKIPS a new entry (the size still comes from the stop distance
and the 1% risk limit); it never adds to a position or opens one. The model is refitted by
the weekly retrain (``models/regime_alloc.json``) and on start if it is missing, so it adapts
to new price history; each new candle updates the filtered probabilities.
"""

from __future__ import annotations

import json
import logging
import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Any

from .models import Candle
from .regime_hmm import HmmRegimeLayer


log = logging.getLogger("trendbot.allocation")

DEFAULT_CAPS = {"euphoria": 95.0, "bull": 95.0, "neutral": 50.0, "bear": 25.0, "crash": 0.0}
MODEL_FILE = "regime_alloc.json"


@dataclass
class AllocationSettings:
    enabled: bool = True
    ref_pair: str = "BTC/USDT"
    n_states: int = 5
    persistence: int = 3
    caps: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_CAPS))
    min_candles: int = 1200

    def __post_init__(self) -> None:
        self.caps = {**DEFAULT_CAPS, **self.caps}
        if any(not 0 <= v <= 100 for v in self.caps.values()) or self.persistence < 1:
            raise ValueError("allocation caps must be 0-100% and persistence >= 1")


def confirm(labels: Sequence[str], k: int) -> list[str]:
    """Persistence filter: the regime switches only after k equal bars in a row."""
    out: list[str] = []
    current, run_label, run = None, None, 0
    for lab in labels:
        run = run + 1 if lab == run_label else 1
        run_label = lab
        if current is None or (lab != current and run >= k):
            current = lab
        out.append(current)
    return out


class RegimeAllocator:
    def __init__(self, settings: AllocationSettings) -> None:
        self.s = settings
        self.layer: HmmRegimeLayer | None = None
        self._filt: Any = None  # (hmm, running forward filter, raw labels, confirmed labels)

    # -------------------------------------------------------------- model
    def fit(self, candles: Mapping[str, Sequence[Candle]], tf_ms: int, until_ts: int) -> bool:
        from .layers import MarketView

        layer = HmmRegimeLayer(
            ref_pair=self.s.ref_pair, n_states=self.s.n_states, min_candles=self.s.min_candles
        )
        layer.fit_candles(MarketView(candles, tf_ms), until_ts)
        if not layer.models:
            return False
        self.layer = layer
        return True

    def save(self, models_dir: Path) -> None:
        if self.layer is None:
            return
        models_dir.mkdir(parents=True, exist_ok=True)
        (models_dir / MODEL_FILE).write_text(json.dumps(self.layer.state()), encoding="utf-8")

    def load(self, models_dir: Path) -> bool:
        p = models_dir / MODEL_FILE
        if not p.exists():
            return False
        layer = HmmRegimeLayer(ref_pair=self.s.ref_pair, n_states=self.s.n_states)
        layer.load_state(json.loads(p.read_text(encoding="utf-8")))
        self.layer = layer if layer.models else None
        return self.layer is not None

    # -------------------------------------------------------------- the regime now
    def regime(self, candles: Mapping[str, Sequence[Candle]], tf_ms: int) -> dict[str, Any] | None:
        """Filtered probabilities over the reference history, persistence-confirmed.

        Incremental: only candles after the last one seen are pushed through the forward
        recursion (a changed history, or a new model, restarts it)."""
        if self.layer is None:
            return None
        ref = (
            self.s.ref_pair
            if self.s.ref_pair in self.layer.models
            else sorted(self.layer.models)[0]
        )
        hist = list(candles.get(ref) or [])
        if len(hist) < 50:
            return None
        hmm, labels = self.layer.models[ref], self.layer.labels[ref]
        f = self._filt
        if (
            f is None
            or f[0] is not hmm
            or not f[1].ts
            or f[1].ts[-1] not in {c.ts for c in hist[-2:]}
            or (len(f[1].ts) > len(hist))
        ):
            from .regime_hmm import _Filtered

            f = self._filt = (hmm, _Filtered(int(self.layer._p("vol_window"))), [], [])
        _, filt, raw, conf = f
        last = filt.ts[-1] if filt.ts else None
        for c in hist:
            if last is not None and c.ts <= last:
                continue
            filt.push(hmm, c)
            p = filt.probs[-1]
            if p is None:
                continue
            raw.append((c.ts + tf_ms, labels[max(range(len(p)), key=p.__getitem__)]))
            lab = raw[-1][1]
            run = 1
            for _, x in reversed(raw[:-1]):
                if x != lab or run >= self.s.persistence:
                    break
                run += 1
            current = conf[-1][1] if conf else lab
            conf.append((c.ts + tf_ms, lab if (not conf or run >= self.s.persistence) else current))
        del raw[:-400], conf[:-400]
        if not raw or filt.probs[-1] is None:
            return None
        probs = filt.probs[-1]
        streak = 1
        for _, lab in reversed(raw[:-1]):
            if lab != raw[-1][1]:
                break
            streak += 1
        return {
            "ref": ref,
            "at": raw[-1][0],
            "raw": raw[-1][1],
            "confirmed": conf[-1][1],
            "streak": streak,
            "probs": {lab: round(p, 4) for lab, p in zip(labels, probs, strict=True)},
            "cap_pct": self.s.caps.get(conf[-1][1], 100.0),
            "history": [
                {"ts": t, "raw": r, "confirmed": c}
                for (t, r), (_, c) in zip(raw[-60:], conf[-60:], strict=True)
            ],
        }


def exposure_room(cap_pct: float, equity: float, open_notional: float) -> float:
    """Quote currency a new entry may still use under the regime cap."""
    return max(0.0, cap_pct / 100.0 * equity - open_notional)


def correlation_table(
    candles: Mapping[str, Sequence[Candle]], n: int = 540
) -> dict[str, dict[str, float | None]]:
    """Pearson correlation of 4H log returns over the last n candles (about 90 days)."""
    rets: dict[str, dict[int, float]] = {}
    for p, cs in candles.items():
        cs = list(cs)[-(n + 1) :]
        rets[p] = {b.ts: math.log(b.close / a.close) for a, b in pairwise(cs) if a.close > 0}
    out: dict[str, dict[str, float | None]] = {}
    for a in sorted(rets):
        out[a] = {}
        for b in sorted(rets):
            common = sorted(set(rets[a]) & set(rets[b]))
            if len(common) < 30:
                out[a][b] = None
                continue
            x, y = [rets[a][t] for t in common], [rets[b][t] for t in common]
            try:
                out[a][b] = round(statistics.correlation(x, y), 3)
            except statistics.StatisticsError:
                out[a][b] = None
    return out
