"""Core Learning Architecture: ledger, learnings and context injection for the live bot.

Three pieces, all plain files a human can read and audit:

``ledger.json``
    Every trade in detail: signal time, entry, stop, target, exit, the EXPECTED outcome at
    entry (+reward_risk R at the target, -1R at the stop, and the expectancy of similar past
    trades), the actual outcome, and WHY it was triggered (every gate's detail, the stop's
    structure, the sizing sentence).

``learnings.md`` / ``learnings.json``
    After every close the bot writes a short plain-English lesson about that trade
    (``learnings.md``). It also re-derives LEARNED RULES from all closed trades
    (``learnings.json``), e.g. "Avoid entries with volume below 1.72x the 20-candle average:
    7 of 8 such trades lost (avg -0.61R)."

Context injection
    Before every new entry the bot reads both files: ``LearningFilter`` is an entry-veto
    layer (``models.EntryFilter``) that blocks a signal matching an ACTIVE learned rule.

Guardrails, because a few hundred trades are easy to over-fit:

- a rule needs at least ``min_trades`` matching closed trades, an average below
  ``max_avg_r`` and a one-sided 95% upper bound on its mean R below zero;
- a rule is only ACTIVE (enforced) when it was also validated on the cached candle
  history: vetoing its signals must raise average R in BOTH the older 70% and the newer
  30% of that history, without looking at which half it was learned from;
- at most ``max_active`` rules are active, and together they may not veto more than
  ``max_blocked_share`` of all past entries (the bot must not learn to never trade);
- the operator can switch any rule off (or on) from the dashboard; learning never
  loosens a mandatory rule, it only vetoes entries that already passed R1-R9.
"""

from __future__ import annotations

import json
import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .config import StrategyConfig
from .journal import ms_to_iso
from .models import EXIT_SL, EXIT_TP, Candle, FeatureRow, SignalCheck, Trade


RULE_ID = "L_learned_rule"

FEATURES = {
    "vol_ratio": ("volume", "x the 20-candle average", 2),
    "rsi": ("RSI", "", 1),
    "ema_gap_pct": ("EMA9-EMA21 gap", "% of price", 2),
    "dist_regime_pct": ("distance above EMA200", "%", 2),
}
SESSIONS = {"Asia (00-08 UTC)": (0, 8), "Europe (08-16 UTC)": (8, 16), "US (16-24 UTC)": (16, 24)}


@dataclass
class LearningSettings:
    mode: str = "enforce"  # "enforce" | "advisory" (rules are shown but never block)
    min_trades: int = 8
    max_avg_r: float = -0.25
    max_active: int = 3
    max_blocked_share: float = 0.5
    validate_on_history: bool = True


@dataclass
class LearnedRule:
    id: str
    feature: str  # a FEATURES key, "session" or "pair"
    op: str  # "below" | "above" | "in" | "is"
    value: Any  # threshold, (lo, hi) hours, or pair
    text: str
    n: int
    wins: int
    avg_r: float
    upper95: float
    trade_ids: list[int]
    status: str = "candidate"  # candidate | active | disabled (by operator) | rejected
    why: str = ""
    validation: dict[str, Any] = field(default_factory=dict)

    def matches(self, pair: str, feats: Mapping[str, float]) -> bool:
        if self.feature == "pair":
            return pair == self.value
        if self.feature == "session":
            h = feats.get("hour_utc")
            return h is not None and self.value[0] <= h < self.value[1]
        v = feats.get(self.feature)
        if v is None:
            return False
        return v < self.value if self.op == "below" else v > self.value


# ---------------------------------------------------------------------- statistics
def _upper95(rs: Sequence[float]) -> float:
    if len(rs) < 2:
        return math.inf
    return statistics.fmean(rs) + 1.645 * statistics.stdev(rs) / math.sqrt(len(rs))


def _tercile_cuts(values: Sequence[float]) -> tuple[float, float] | None:
    if len(values) < 6:
        return None
    s = sorted(values)
    return s[len(s) // 3], s[(2 * len(s)) // 3]


def _conditions(closed: Sequence[Trade]) -> list[tuple[str, str, Any, str]]:
    """Candidate conditions (feature, op, value, human text) from the closed trades."""
    out: list[tuple[str, str, Any, str]] = []
    for key, (name, unit, nd) in FEATURES.items():
        vals = [t.features[key] for t in closed if key in t.features]
        cuts = _tercile_cuts(vals)
        if cuts is None:
            continue
        lo, hi = round(cuts[0], nd), round(cuts[1], nd)
        out.append((key, "below", lo, f"{name} below {lo:g}{unit}"))
        out.append((key, "above", hi, f"{name} above {hi:g}{unit}"))
    for label, rng in SESSIONS.items():
        out.append(("session", "in", rng, f"signals closing in the {label} session"))
    for pair in sorted({t.pair for t in closed}):
        out.append(("pair", "is", pair, f"{pair} entries"))
    return out


def derive_rules(closed: Sequence[Trade], s: LearningSettings) -> list[LearnedRule]:
    """Every condition whose matching trades clearly lost, strongest first."""
    rules: list[LearnedRule] = []
    closed = [t for t in closed if t.r_multiple is not None]
    for feature, op, value, text in _conditions(closed):
        probe = LearnedRule("", feature, op, value, "", 0, 0, 0.0, 0.0, [])
        hits = [t for t in closed if probe.matches(t.pair, t.features)]
        rs = [t.r_multiple for t in hits]
        if len(hits) < s.min_trades:
            continue
        avg, up = statistics.fmean(rs), _upper95(rs)
        if avg > s.max_avg_r or up >= 0:
            continue
        wins = sum(1 for r in rs if r > 0)
        sentence = (
            f"Avoid {text}: {len(hits) - wins} of {len(hits)} such trades lost "
            f"(avg {avg:+.2f}R, 95% upper bound {up:+.2f}R)."
        )
        rules.append(
            LearnedRule(
                id="",
                feature=feature,
                op=op,
                value=value,
                text=sentence,
                n=len(hits),
                wins=wins,
                avg_r=round(avg, 4),
                upper95=round(up, 4),
                trade_ids=[t.trade_id for t in hits],
            )
        )
    rules.sort(key=lambda r: (r.upper95, r.avg_r))
    for i, r in enumerate(rules, 1):
        r.id = f"L{i}:{r.feature}:{r.op}:{r.value}"
    return rules


# ---------------------------------------------------------------------- validation
def _halves(data: Mapping[str, Sequence[Candle]]) -> list[tuple[str, int | None, int | None]]:
    from .walkforward import split_ts

    split = split_ts(data)
    return [("older", None, split), ("newer", split, None)]


def baseline(data: Mapping[str, Sequence[Candle]], cfg: StrategyConfig) -> dict[str, Any] | None:
    """Base-strategy backtest of both halves of the cached history (shared by all rules)."""
    from .backtester import run_backtest

    if not data or min(len(c) for c in data.values()) < 400:
        return None
    out = {}
    for name, lo, hi in _halves(data):
        out[name] = [t.r_multiple for t in run_backtest(data, cfg, start_ts=lo, end_ts=hi).trades]
    return out


def validate_rule(
    rule: LearnedRule,
    data: Mapping[str, Sequence[Candle]],
    cfg: StrategyConfig,
    base: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Backtest the cached history with and without the rule's veto, in two halves (70/30)."""
    from .backtester import run_backtest

    base = base if base is not None else baseline(data, cfg)
    if base is None:
        return {"ok": False, "reason": "not enough cached candle history to validate"}
    veto = LearningFilter([rule], enforce=True)
    out: dict[str, Any] = {}
    ok = True
    for name, lo, hi in _halves(data):
        rs = [
            t.r_multiple
            for t in run_backtest(data, cfg, start_ts=lo, end_ts=hi, entry_filter=veto).trades
        ]
        b = statistics.fmean(base[name]) if base[name] else 0.0
        w = statistics.fmean(rs) if rs else 0.0
        out[name] = {
            "base_n": len(base[name]),
            "base_avg_r": round(b, 4),
            "rule_n": len(rs),
            "rule_avg_r": round(w, 4),
        }
        ok = ok and bool(base[name]) and bool(rs) and w > b
    out["ok"] = ok
    out["reason"] = (
        "the veto raised average R in both the older and the newer part of the history"
        if ok
        else "the veto did not raise average R in both parts of the history"
    )
    return out


def select_active(
    rules: list[LearnedRule],
    closed: Sequence[Trade],
    s: LearningSettings,
    overrides: Mapping[str, str],
    data: Mapping[str, Sequence[Candle]] | None,
    cfg: StrategyConfig,
) -> list[LearnedRule]:
    """Decide each rule's status: active / candidate / rejected / disabled.

    Limits are checked before the (costly) backtest validation, so at most a handful of
    backtests run per rebuild; the base backtest is shared by every rule.
    """
    blocked: set[int] = set()
    active = 0
    base: dict[str, Any] | None = None
    for r in rules:
        forced = overrides.get(r.id)
        if forced == "disabled":
            r.status, r.why = "disabled", "switched off by the operator"
            continue
        share = len(blocked | set(r.trade_ids)) / max(1, len(closed))
        if forced != "active" and (active >= s.max_active or share > s.max_blocked_share):
            r.status, r.why = (
                "candidate",
                "limit reached: too many rules or too many vetoed entries",
            )
            continue
        if s.validate_on_history and data and forced != "active":
            if base is None:
                base = baseline(data, cfg) or {}
            r.validation = validate_rule(r, data, cfg, base or None)
            if not r.validation["ok"]:
                r.status, r.why = "rejected", r.validation["reason"]
                continue
        r.status = "active"
        r.why = "switched on by the operator" if forced == "active" else "enough evidence"
        blocked |= set(r.trade_ids)
        active += 1
    return rules


# ---------------------------------------------------------------------- context injection
class LearningFilter:
    """Entry veto from the ACTIVE learned rules (``models.EntryFilter`` signature)."""

    rule_id = RULE_ID

    def __init__(self, rules: Sequence[LearnedRule], enforce: bool = True) -> None:
        self.rules = list(rules)  # the caller passes the rules to apply (normally the active ones)
        self.enforce = enforce

    def __call__(
        self, pair: str, row: FeatureRow, check: SignalCheck
    ) -> tuple[bool, float | None, str]:
        feats = row.ml_features() or {}
        for r in self.rules:
            if r.matches(pair, feats):
                if self.enforce:
                    return False, None, f"learned rule {r.id} vetoed it: {r.text}"
                return True, None, f"advisory: learned rule {r.id} would veto it: {r.text}"
        return True, None, "no learned rule matches this signal"


# ---------------------------------------------------------------------- ledger & lessons
def expected_outcome(
    pair: str, feats: Mapping[str, float], closed: Sequence[Trade], cfg: StrategyConfig
) -> dict[str, Any]:
    """What the bot expects at entry: the fixed R outcomes and the record of similar trades."""
    similar = [t for t in closed if t.pair == pair and t.r_multiple is not None]
    exp: dict[str, Any] = {
        "at_target_r": cfg.reward_risk,
        "at_stop_r": -1.0,
        "breakeven_win_rate": round(1 / (1 + cfg.reward_risk), 4),
    }
    if len(similar) >= 5:
        rs = [t.r_multiple for t in similar]
        exp["similar_trades"] = len(similar)
        exp["similar_avg_r"] = round(statistics.fmean(rs), 4)
        exp["similar_win_rate"] = round(sum(r > 0 for r in rs) / len(rs), 4)
    return exp


def entry_context(
    check: SignalCheck,
    reason: str,
    feats: Mapping[str, float],
    expected: Mapping[str, Any],
    learning_note: str,
) -> dict[str, Any]:
    return {
        "why": [f"{g.name}: {g.detail}" for g in check.gates] + [reason],
        "features": dict(feats),
        "expected": dict(expected),
        "learning_check": learning_note,
    }


def _describe(feats: Mapping[str, float], closed: Sequence[Trade]) -> list[tuple[float, str]]:
    """(how unusual, phrase) per feature, relative to the winners' distribution."""
    winners = [t for t in closed if (t.r_multiple or 0) > 0]
    out = []
    for key, (name, unit, nd) in FEATURES.items():
        v = feats.get(key)
        ref = [t.features[key] for t in winners if key in t.features]
        if v is None or len(ref) < 3:
            continue
        med = statistics.median(ref)
        spread = statistics.pstdev(ref) or 1.0
        z = (v - med) / spread
        side = "below" if z < 0 else "above"
        out.append((abs(z), f"{name} {v:.{nd}f}{unit} ({side} the winners' median {med:.{nd}f})"))
    return sorted(out, reverse=True)


def lesson_for(t: Trade, closed_before: Sequence[Trade]) -> str:
    """A short plain-English lesson about one closed trade."""
    hours = (t.exit_ts - t.entry_ts) / 3.6e6 if t.exit_ts else 0
    head = f"Trade #{t.trade_id} {t.pair} "
    if t.exit_reason == EXIT_TP:
        head += f"reached its target ({t.r_multiple:+.2f}R) after {hours:.0f}h."
    elif t.exit_reason == EXIT_SL:
        head += f"was stopped out ({t.r_multiple:+.2f}R) after {hours:.0f}h."
    else:
        head += f"was closed manually ({t.r_multiple:+.2f}R) after {hours:.0f}h."
    notes = _describe(t.features, closed_before)
    if not notes:
        return head + " Not enough winning trades yet to compare its entry against."
    if (t.r_multiple or 0) > 0:
        return head + f" It worked with {notes[-1][1]}; nothing unusual at entry."
    return (
        head + f" Most unusual at entry: {notes[0][1]}. Lesson: treat that condition as a "
        "warning sign; it becomes a blocking rule only once enough trades confirm it."
    )


class LearningBook:
    """Owns ledger.json, learnings.md and learnings.json in the bot's state dir."""

    def __init__(self, state_dir: Path, cfg: StrategyConfig, settings: LearningSettings) -> None:
        self.dir = state_dir
        self.cfg = cfg
        self.s = settings
        self.overrides: dict[str, str] = {}
        self.rules: list[LearnedRule] = []
        self.lessons: dict[str, str] = {}
        path = self.dir / "learnings.json"
        if path.exists():
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.overrides = dict(raw.get("overrides", {}))
            self.lessons = dict(raw.get("lessons", {}))
            self.rules = [_rule_from(d) for d in raw.get("rules", [])]

    @property
    def ledger_path(self) -> Path:
        return self.dir / "ledger.json"

    def filter(self) -> LearningFilter:
        return LearningFilter(
            [r for r in self.rules if r.status == "active"], enforce=self.s.mode == "enforce"
        )

    def relearn(
        self,
        trades: Sequence[Trade],
        data: Mapping[str, Sequence[Candle]] | None,
        extra: Sequence[Trade] = (),
    ):
        """Lessons for own trades; rules from own + ``extra`` (e.g. paper teachers') trades."""
        closed = sorted((t for t in trades if t.is_closed), key=lambda t: (t.exit_ts, t.trade_id))
        for i, t in enumerate(closed):
            self.lessons.setdefault(str(t.trade_id), lesson_for(t, closed[:i]))
        pool = sorted(
            closed + [t for t in extra if t.is_closed], key=lambda t: (t.exit_ts, t.trade_id)
        )
        rules = derive_rules(pool, self.s)
        self.rules = select_active(rules, pool, self.s, self.overrides, data, self.cfg)
        return self.rules

    def set_override(self, rule_id: str, status: str | None) -> None:
        if status is None:
            self.overrides.pop(rule_id, None)
        elif status in ("active", "disabled"):
            self.overrides[rule_id] = status
        else:
            raise ValueError("override must be 'active', 'disabled' or None")

    def write(self, trades: Sequence[Trade], contexts: Mapping[str, Any]) -> None:
        closed = sorted((t for t in trades if t.is_closed), key=lambda t: (t.exit_ts, t.trade_id))
        ledger = [
            ledger_row(t, contexts.get(str(t.trade_id), {}), self.lessons)
            for t in sorted(trades, key=lambda t: t.trade_id)
        ]
        _atomic(self.ledger_path, json.dumps(ledger, indent=1))
        _atomic(
            self.dir / "learnings.json",
            json.dumps(
                {
                    "mode": self.s.mode,
                    "settings": asdict(self.s),
                    "rules": [asdict(r) for r in self.rules],
                    "overrides": self.overrides,
                    "lessons": self.lessons,
                },
                indent=1,
                default=list,
            ),
        )
        _atomic(
            self.dir / "learnings.md", render_learnings(self.rules, closed, self.lessons, self.s)
        )


def ledger_row(t: Trade, ctx: Mapping[str, Any], lessons: Mapping[str, str]) -> dict[str, Any]:
    return {
        "trade_id": t.trade_id,
        "pair": t.pair,
        "status": "closed" if t.is_closed else "open",
        "signal_time": ms_to_iso(t.signal_ts),
        "entry_time": ms_to_iso(t.entry_ts),
        "entry_price": t.entry_price,
        "stop": t.stop,
        "target": t.target,
        "qty": t.qty,
        "risk_pct": t.risk_pct,
        "risk_amount": t.risk_amount,
        "why_triggered": ctx.get("why", []),
        "expected_outcome": ctx.get("expected", {}),
        "learning_check": ctx.get("learning_check", ""),
        "features": ctx.get("features", t.features),
        "exit_time": ms_to_iso(t.exit_ts) if t.exit_ts else None,
        "exit_price": t.exit_price,
        "exit_reason": t.exit_reason,
        "pnl": t.pnl,
        "r_multiple": t.r_multiple,
        "lesson": lessons.get(str(t.trade_id)),
    }


def render_learnings(
    rules: Sequence[LearnedRule],
    closed: Sequence[Trade],
    lessons: Mapping[str, str],
    s: LearningSettings,
) -> str:
    lines = [
        "# trendbot learnings",
        "",
        f"Mode: **{s.mode}**. {len(closed)} closed trades. Rules need >= {s.min_trades} "
        f"matching trades, avg <= {s.max_avg_r}R, a 95% upper bound below 0R and (when "
        "cached history exists) a backtest check on both halves of the history.",
        "",
        "## Learned rules",
        "",
    ]
    if not rules:
        lines.append("None yet: no condition has enough losing trades behind it.")
    for r in rules:
        lines.append(f"- **{r.status.upper()}** `{r.id}`: {r.text} ({r.why})")
    lines += ["", "## Lessons, newest first", ""]
    for t in reversed(closed):
        text = lessons.get(str(t.trade_id))
        if text:
            lines.append(f"- {ms_to_iso(t.exit_ts)}: {text}")
    return "\n".join(lines) + "\n"


def _rule_from(d: Mapping[str, Any]) -> LearnedRule:
    d = dict(d)
    if d.get("feature") == "session":
        d["value"] = tuple(d["value"])
    return LearnedRule(**d)


def _atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
