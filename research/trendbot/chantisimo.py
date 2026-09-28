"""Chantisimo: the bot's brain. It remembers every trade, recalls similar ones before each
entry, diagnoses the mistakes behind losses, and carries paper-trading lessons into the
bot that trades for real.

How the pieces fit (all plain files in the bot's ``state_dir``)::

    signal passes R1-R9
        -> Chantisimo.filter: graduation check -> learned rules -> validated signal layers
           (including its own recall layer)                        -> TAKE or SKIP
        -> the thought is written to chantisimo_thoughts.jsonl
    trade closes
        -> Chantisimo.reflect: what recall predicted vs what happened, which mistakes
           (entry conditions that differ from the winners'), the lesson
        -> the rules are re-learned from ALL memory (own + paper teachers)

Memory
    Its own closed trades plus the closed trades of its TEACHERS: other bots' state dirs
    listed in ``"brain": {"learn_from": [...]}`` (normally the paper bots). A live bot with
    paper teachers therefore learns its rules from the paper bots' losses before it has
    lost anything itself.

Recall
    Before every entry: the k most similar remembered trades (RSI, volume, EMA gap, distance
    to EMA200, time of day, same pair), with their win count and average R.

Mistakes
    After every loss: which entry conditions were unusual compared with the winners (e.g.
    "volume weaker than winners", "far above EMA200 (late entry)"), plus "stopped out
    quickly". The mistake book counts them. A mistake that keeps costing money becomes a
    blocking rule through the learned-rule checks (learning.py: enough trades, clearly
    losing, confirmed by a backtest on both halves of the history).

Graduation (paper -> real money)
    With teachers configured, a testnet or live bot only takes a pair once its teachers
    have at least ``min_paper_trades`` closed trades on it with an average R of at least
    ``min_paper_avg_r``. Until then it skips the pair and says why.

Recall layer (``chantisimo_recall``)
    A k-nearest-neighbour veto over remembered and simulated trades. Like every signal
    layer it is only switched on after it helps on data it never saw (layers.py).

Chantisimo can only SKIP entries that already passed the nine rules. It never opens a trade
on its own, never loosens a rule and never raises risk.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from .journal import ms_to_iso, read_journal
from .layers import Layer, MarketView, TrainContext, register
from .models import EXIT_SL, FeatureRow, SignalCheck, Trade


NAME = "Chantisimo"
RULE_ID = "L_brain"
CANDLE_MS = 4 * 3_600_000
TEACHER_ID_STEP = 1_000_000  # teacher trade ids are remapped to avoid clashes
FEATURE_KEYS = ("rsi", "vol_ratio", "ema_gap_pct", "dist_regime_pct")
MISTAKE_TEXT = {
    ("vol_ratio", -1): "volume weaker than the winners'",
    ("vol_ratio", 1): "volume spike far above the winners'",
    ("rsi", 1): "RSI more stretched than the winners'",
    ("rsi", -1): "RSI weaker than the winners'",
    ("ema_gap_pct", -1): "EMA9/21 barely crossed (weak trend)",
    ("ema_gap_pct", 1): "EMA9/21 gap stretched (late in the move)",
    ("dist_regime_pct", 1): "far above EMA200 (late entry)",
    ("dist_regime_pct", -1): "barely above EMA200 (weak regime)",
}
QUICK_STOP = "stopped out within 2 candles (entered into a reversal)"


@dataclass
class BrainSettings:
    enabled: bool = True
    learn_from: list[str] = field(default_factory=list)  # teacher state dirs (paper bots)
    recall_k: int = 12
    min_paper_trades: int = 20  # graduation: teacher trades a pair needs (testnet/live only)
    min_paper_avg_r: float = 0.0
    graduation: bool = True
    mistake_z: float = 1.0  # how unusual (in winners' std units) a condition must be
    thoughts_keep: int = 2000  # lines kept in chantisimo_thoughts.jsonl


@dataclass
class Memory:
    source: str  # "self" or the teacher's folder name
    mode: str  # paper | testnet | live
    trade: Trade


# ---------------------------------------------------------------------- memory
def _mode_of(state_dir: Path) -> str:
    try:
        return str(json.loads((state_dir / "state.json").read_text(encoding="utf-8")).get("mode"))
    except (OSError, ValueError):
        return "paper"


def load_teachers(dirs: Sequence[str | Path], own_dir: Path | None = None) -> list[Memory]:
    """Closed trades of every teacher bot, with ids remapped per teacher."""
    out: list[Memory] = []
    own = own_dir.resolve() if own_dir is not None else None
    for i, d in enumerate(dirs):
        p = Path(d)
        if own is not None and p.resolve() == own:
            continue
        journal = p / "journal.csv"
        if not journal.exists():
            continue
        mode = _mode_of(p)
        for t in read_journal(journal):
            if t.is_closed and t.r_multiple is not None:
                tid = (i + 1) * TEACHER_ID_STEP + t.trade_id
                out.append(Memory(p.name, mode, replace(t, trade_id=tid)))
    return out


def pooled(own: Sequence[Trade], teachers: Sequence[Memory], own_mode: str) -> list[Memory]:
    """Own closed trades + teachers', one per (pair, signal): own trades win."""
    mem = [Memory("self", own_mode, t) for t in own if t.is_closed and t.r_multiple is not None]
    seen = {(m.trade.pair, m.trade.signal_ts) for m in mem}
    for m in teachers:
        key = (m.trade.pair, m.trade.signal_ts)
        if key not in seen:
            seen.add(key)
            mem.append(m)
    return sorted(mem, key=lambda m: (m.trade.exit_ts or 0, m.trade.trade_id))


def known_by(memory: Sequence[Memory], now_ts: int | None) -> list[Memory]:
    """Trades whose exit candle had closed by ``now_ts`` (all of them if None)."""
    if now_ts is None:
        return list(memory)
    return [m for m in memory if (m.trade.exit_ts or 0) + CANDLE_MS <= now_ts]


# ---------------------------------------------------------------------- recall
def _scales(rows: Sequence[Mapping[str, float]]) -> dict[str, float]:
    out = {}
    for k in FEATURE_KEYS:
        vals = [r[k] for r in rows if k in r]
        out[k] = (statistics.pstdev(vals) if len(vals) > 1 else 0.0) or 1.0
    return out


def distance(a: Mapping[str, float], b: Mapping[str, float], scales: Mapping[str, float]) -> float:
    d = 0.0
    for k in FEATURE_KEYS:
        if k in a and k in b:
            d += ((a[k] - b[k]) / scales[k]) ** 2
        else:
            d += 4.0
    if "hour_utc" in a and "hour_utc" in b:
        dh = abs(a["hour_utc"] - b["hour_utc"]) % 24
        d += 0.25 * (min(dh, 24 - dh) / 4) ** 2
    return math.sqrt(d)


def recall(
    pair: str, feats: Mapping[str, float], memory: Sequence[Memory], k: int = 12
) -> dict[str, Any]:
    """The k most similar remembered trades and what happened to them."""
    if not memory or not feats:
        return {"n": 0, "text": f"{NAME} has no memory of similar trades yet", "similar": []}
    scales = _scales([m.trade.features for m in memory])
    scored = sorted(
        (
            distance(feats, m.trade.features, scales) + (0.0 if m.trade.pair == pair else 0.5),
            i,
            m,
        )
        for i, m in enumerate(memory)
    )[:k]
    rs = [m.trade.r_multiple or 0.0 for _, _, m in scored]
    wins = sum(r > 0 for r in rs)
    by_source = Counter("own" if m.source == "self" else m.mode for _, _, m in scored)
    avg = statistics.fmean(rs)
    src = ", ".join(f"{n} {s}" for s, n in sorted(by_source.items()))
    return {
        "n": len(rs),
        "wins": wins,
        "avg_r": round(avg, 4),
        "by_source": dict(by_source),
        "text": f"{NAME} recalls {len(rs)} similar trades ({src}): {wins} won, avg {avg:+.2f}R",
        "similar": [
            {
                "source": m.source,
                "mode": m.mode,
                "pair": m.trade.pair,
                "signal_utc": ms_to_iso(m.trade.signal_ts),
                "r": m.trade.r_multiple,
                "distance": round(d, 3),
            }
            for d, _, m in scored[:5]
        ],
    }


# ---------------------------------------------------------------------- mistakes
def diagnose(t: Trade, winners: Sequence[Trade], z_min: float = 1.0) -> list[str]:
    """Mistakes behind one losing trade: entry conditions unlike the winners', quick stops."""
    if (t.r_multiple or 0) > 0:
        return []
    out = []
    ref = winners if len(winners) >= 3 else []
    for key in FEATURE_KEYS:
        vals = [w.features[key] for w in ref if key in w.features]
        v = t.features.get(key)
        if v is None or len(vals) < 3:
            continue
        med, sd = statistics.median(vals), statistics.pstdev(vals) or 1.0
        z = (v - med) / sd
        if abs(z) >= z_min:
            out.append(MISTAKE_TEXT[(key, 1 if z > 0 else -1)])
    if t.exit_reason == EXIT_SL and t.exit_ts is not None:
        candles = (t.exit_ts - t.entry_ts) / (4 * 3_600_000)
        if candles <= 2:
            out.append(QUICK_STOP)
    return out


def mistake_book(memory: Sequence[Memory], z_min: float = 1.0) -> list[dict[str, Any]]:
    """Per mistake: how often it happened, where (own / paper), what those trades averaged."""
    trades = [m.trade for m in memory]
    winners = [t for t in trades if (t.r_multiple or 0) > 0]
    agg: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"losses": 0, "r_sum": 0.0, "sources": Counter(), "trade_ids": []}
    )
    for m in memory:
        for tag in diagnose(m.trade, winners, z_min):
            a = agg[tag]
            a["losses"] += 1
            a["r_sum"] += m.trade.r_multiple or 0.0
            a["sources"]["own" if m.source == "self" else m.mode] += 1
            a["trade_ids"].append(m.trade.trade_id)
    rows = [
        {
            "mistake": tag,
            "losses": a["losses"],
            "lost_r": round(a["r_sum"], 3),
            "sources": dict(a["sources"]),
            "trade_ids": a["trade_ids"][-20:],
        }
        for tag, a in agg.items()
    ]
    return sorted(rows, key=lambda r: (-r["losses"], r["lost_r"]))


# ---------------------------------------------------------------------- the brain
class Chantisimo:
    """Owns chantisimo.json / chantisimo.md / chantisimo_thoughts.jsonl in the state dir."""

    def __init__(
        self, state_dir: Path, settings: BrainSettings | None = None, mode: str = "paper"
    ) -> None:
        self.dir = Path(state_dir)
        self.s = settings or BrainSettings()
        self.mode = mode
        self.memory: list[Memory] = []
        self.teachers: list[Memory] = []
        self.pending: dict[str, dict[str, Any]] = {}  # pair -> recall at the last signal

    @property
    def thoughts_path(self) -> Path:
        return self.dir / "chantisimo_thoughts.jsonl"

    # -------------------------------------------------------------- memory
    def remember(self, own_trades: Sequence[Trade]) -> list[Memory]:
        self.teachers = load_teachers(self.s.learn_from, self.dir)
        self.memory = pooled([t for t in own_trades if t.is_closed], self.teachers, self.mode)
        return self.memory

    def teacher_trades(self, own_trades: Sequence[Trade]) -> list[Trade]:
        """Teachers' closed trades on signals this bot did not trade itself."""
        own_keys = {(t.pair, t.signal_ts) for t in own_trades}
        return [m.trade for m in self.teachers if (m.trade.pair, m.trade.signal_ts) not in own_keys]

    # -------------------------------------------------------------- graduation
    def graduation(self, pair: str, now_ts: int | None = None) -> tuple[bool, str]:
        """Is ``pair`` proven in paper trading (teacher trades known by ``now_ts``)?"""
        if not (self.s.graduation and self.s.learn_from and self.mode in ("testnet", "live")):
            return True, "no graduation needed"
        rs = [
            m.trade.r_multiple or 0.0
            for m in known_by(self.teachers, now_ts)
            if m.trade.pair == pair and m.mode == "paper"
        ]
        need = self.s.min_paper_trades
        if len(rs) < need:
            return False, (
                f"{NAME}: {pair} has {len(rs)} of {need} paper trades; it trades for real only "
                "after it is proven in paper trading"
            )
        avg = statistics.fmean(rs)
        if avg < self.s.min_paper_avg_r:
            return False, (
                f"{NAME}: {pair} averaged {avg:+.2f}R over {len(rs)} paper trades (needs "
                f">= {self.s.min_paper_avg_r:+.2f}R before real money)"
            )
        return True, f"{pair} graduated from paper: {len(rs)} trades, avg {avg:+.2f}R"

    def graduation_table(self, pairs: Sequence[str]) -> list[dict[str, Any]]:
        rows = []
        for p in pairs:
            rs = [
                m.trade.r_multiple or 0.0
                for m in self.teachers
                if m.trade.pair == p and m.mode == "paper"
            ]
            ok, why = self.graduation(p)
            rows.append(
                {
                    "pair": p,
                    "paper_trades": len(rs),
                    "paper_avg_r": round(statistics.fmean(rs), 4) if rs else None,
                    "graduated": ok,
                    "why": why,
                }
            )
        return rows

    # -------------------------------------------------------------- deciding
    def filter(self, inner: Any) -> BrainFilter:
        return BrainFilter(self, inner)

    def think(self, record: Mapping[str, Any]) -> None:
        self._append(dict(record))

    # -------------------------------------------------------------- reflecting
    def reflect(self, t: Trade, predicted: Mapping[str, Any] | None) -> dict[str, Any]:
        winners = [m.trade for m in self.memory if (m.trade.r_multiple or 0) > 0]
        mistakes = diagnose(t, winners, self.s.mistake_z)
        r = t.r_multiple or 0.0
        pred = dict(predicted or {})
        if r > 0:
            verdict = "won"
            lesson = "the setup worked; nothing to correct"
        elif mistakes:
            verdict = "lost"
            lesson = "watch for: " + "; ".join(mistakes)
        else:
            verdict = "lost"
            lesson = "a normal loss: nothing at entry set it apart from the winners"
        if pred.get("n"):
            surprise = (
                "as recalled"
                if (pred.get("avg_r", 0) > 0) == (r > 0)
                else "against what it recalled"
            )
            lesson += f". Recall said avg {pred['avg_r']:+.2f}R over {pred['n']} trades: {surprise}"
        rec = {
            "kind": "reflection",
            "trade_id": t.trade_id,
            "pair": t.pair,
            "exit_utc": ms_to_iso(t.exit_ts) if t.exit_ts else None,
            "exit_reason": t.exit_reason,
            "r": r,
            "verdict": verdict,
            "mistakes": mistakes,
            "predicted": {k: pred.get(k) for k in ("n", "wins", "avg_r")} if pred else None,
            "lesson": lesson,
        }
        self._append(rec)
        return rec

    # -------------------------------------------------------------- files
    def _append(self, rec: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with self.thoughts_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")

    def trim(self) -> None:
        p = self.thoughts_path
        if not p.exists():
            return
        lines = p.read_text(encoding="utf-8").splitlines()
        if len(lines) > self.s.thoughts_keep:
            tmp = p.with_name(p.name + ".tmp")
            tmp.write_text("\n".join(lines[-self.s.thoughts_keep :]) + "\n", encoding="utf-8")
            tmp.replace(p)

    def write(self, pairs: Sequence[str], rules: Sequence[Any] = ()) -> dict[str, Any]:
        mem = self.memory
        by_source = Counter("own" if m.source == "self" else f"{m.mode}:{m.source}" for m in mem)
        rs = [m.trade.r_multiple or 0.0 for m in mem]
        book = mistake_book(mem, self.s.mistake_z)
        active = [r for r in rules if getattr(r, "status", "") == "active"]
        for row in book:
            row["status"] = "watching"
            for r in active:
                if _rule_covers(r, row["mistake"]):
                    row["status"] = f"blocked by rule {r.id}"
        state = {
            "name": NAME,
            "mode": self.mode,
            "settings": asdict(self.s),
            "memory": {
                "trades": len(mem),
                "by_source": dict(by_source),
                "wins": sum(r > 0 for r in rs),
                "avg_r": round(statistics.fmean(rs), 4) if rs else None,
                "teachers": sorted({m.source for m in self.teachers}),
            },
            "graduation": self.graduation_table(pairs),
            "mistakes": book,
            "active_rules": [
                {"id": r.id, "text": r.text, "n": r.n, "avg_r": r.avg_r} for r in active
            ],
        }
        _atomic(self.dir / "chantisimo.json", json.dumps(state, indent=1, default=str))
        _atomic(self.dir / "chantisimo.md", render_md(state))
        self.trim()
        return state


def _rule_covers(rule: Any, mistake: str) -> bool:
    feature, op = getattr(rule, "feature", None), getattr(rule, "op", None)
    for (key, sign), text in MISTAKE_TEXT.items():
        if text == mistake and key == feature:
            return (sign > 0) == (op == "above")
    return False


class BrainFilter:
    """``models.EntryFilter``: graduation, then the inner filter; every verdict is logged."""

    def __init__(self, brain: Chantisimo, inner: Any) -> None:
        self.brain = brain
        self.inner = inner
        self.rule_id = RULE_ID

    def __call__(
        self, pair: str, row: FeatureRow, check: SignalCheck
    ) -> tuple[bool, float | None, str]:
        feats = row.ml_features() or {}
        now = row.close_ts  # only trades that had closed by the decision time
        mem = recall(pair, feats, known_by(self.brain.memory, now), self.brain.s.recall_k)
        self.brain.pending[pair] = mem
        ok, why = self.brain.graduation(pair, now)
        prob: float | None = None
        if ok and self.inner is not None:
            ok, prob, why = self.inner(pair, row, check)
            self.rule_id = getattr(self.inner, "rule_id", RULE_ID)
        else:
            self.rule_id = RULE_ID
        self.brain.think(
            {
                "kind": "thought",
                "pair": pair,
                "signal_utc": ms_to_iso(row.ts),
                "verdict": "TAKE" if ok else "SKIP",
                "reason": why,
                "recall": {k: mem.get(k) for k in ("n", "wins", "avg_r", "by_source")},
                "recall_text": mem["text"],
            }
        )
        return ok, prob, why


def render_md(state: Mapping[str, Any]) -> str:
    m = state["memory"]
    lines = [
        f"# {NAME}: the bot's brain",
        "",
        f"Mode **{state['mode']}**. It remembers **{m['trades']}** closed trades "
        + (
            f"({', '.join(f'{k}: {v}' for k, v in sorted(m['by_source'].items()))})"
            if m["by_source"]
            else ""
        )
        + (f", avg {m['avg_r']:+.2f}R." if m["avg_r"] is not None else "."),
        "",
        "## Mistakes it has learned from",
        "",
    ]
    if not state["mistakes"]:
        lines.append("None yet.")
    for r in state["mistakes"]:
        src = ", ".join(f"{k} {v}" for k, v in sorted(r["sources"].items()))
        lines.append(
            f"- **{r['mistake']}**: {r['losses']} losses ({src}), "
            f"{r['lost_r']:+.2f}R, {r['status']}"
        )
    lines += ["", "## Rules it enforces", ""]
    lines += [
        f"- `{r['id']}` {r['text']} ({r['n']} trades, avg {r['avg_r']:+.2f}R)"
        for r in state["active_rules"]
    ] or ["None active."]
    lines += ["", "## Graduation from paper trading", ""]
    lines += [
        f"- {g['pair']}: {'yes' if g['graduated'] else 'NO'} ({g['why']})"
        for g in state["graduation"]
    ]
    return "\n".join(lines) + "\n"


def read_thoughts(state_dir: Path, n: int = 60, kind: str | None = None) -> list[dict[str, Any]]:
    p = Path(state_dir) / "chantisimo_thoughts.jsonl"
    if not p.exists():
        return []
    out = []
    for line in reversed(p.read_text(encoding="utf-8").splitlines()):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if kind is None or r.get("kind") == kind:
            out.append(r)
            if len(out) >= n:
                break
    return out


def _atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------------- recall layer
@register
class RecallLayer(Layer):
    """k-nearest-neighbour veto: skip a signal whose most similar past trades lost."""

    name = "chantisimo_recall"
    kind = "brain"
    description = (
        f"{NAME}'s memory: vetoes signals whose k most similar past trades averaged below "
        "a threshold"
    )

    def __init__(self, **params: Any) -> None:
        super().__init__(**{"k": 15, "max_avg_r": -0.2, "min_examples": 60, **params})
        self.rows: list[tuple[str, dict[str, float], float]] = []
        self.scales: dict[str, float] = {}

    def fit(self, ctx: TrainContext) -> None:
        ex = ctx.examples()[-3000:]
        self.rows = [
            (p, dict(f), float(r)) for p, _, f, r in ex if all(k in f for k in FEATURE_KEYS)
        ]
        self.scales = _scales([f for _, f, _ in self.rows])

    def veto(self, pair: str, row: FeatureRow, view: MarketView) -> tuple[bool, str]:
        feats = row.ml_features()
        k = int(self.params["k"])
        if not feats or len(self.rows) < k:
            return False, "not enough memory"
        near = sorted(
            (distance(feats, f, self.scales) + (0.0 if p == pair else 0.5), r)
            for p, f, r in self.rows
        )[:k]
        avg = statistics.fmean(r for _, r in near)
        wins = sum(r > 0 for _, r in near)
        blocked = avg < float(self.params["max_avg_r"])
        return blocked, f"{k} most similar past trades: {wins} won, avg {avg:+.2f}R"

    def state(self) -> dict[str, Any]:
        return {"params": self.params, "rows": self.rows, "scales": self.scales}

    def load_state(self, d: Mapping[str, Any]) -> None:
        super().load_state(d)
        self.rows = [(p, dict(f), float(r)) for p, f, r in d.get("rows", [])]
        self.scales = dict(d.get("scales", {}))
