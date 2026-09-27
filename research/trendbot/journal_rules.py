"""Journal rules: "learn from previous trades" as transparent, auditable rules.

Nothing here is fitted or hidden. Every adaptation the bot applies because of past trades is
one of the rules below, is listed by ``audit`` with a one-sentence explanation, and names the
journal rows (``trade_id``) that caused it so a human can check it against the CSV:

- ``R9_circuit_breaker`` bench (scope = the pair): 3 consecutive stop-losses bench the pair.
- ``R9_circuit_breaker`` halt (scope = ``ALL``): trailing 7-day realized loss limit hit.
- ``L_expectancy_guard`` (scope = the pair), only when ``cfg.expectancy_guard`` is True: the
  pair's last ``cfg.guard_window`` closed trades average R < 0 -> risk x ``guard_risk_mult``.
  The window must be full (fewer closed trades never trigger it). Off by default.

CLI (markdown table on stdout)::

    python -m research.trendbot.journal_rules --journal trades.csv --equity 10000 \\
        [--now 2024-03-12T12:00:00Z] [--expectancy-guard]
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .circuit_breakers import CircuitBreakers
from .config import StrategyConfig
from .journal import iso_to_ms, ms_to_iso, read_journal
from .models import Trade


R9 = "R9_circuit_breaker"
GUARD = "L_expectancy_guard"


@dataclass(frozen=True)
class Adaptation:
    rule: str  # a config.RULE_IDS key
    scope: str  # a pair, or "ALL"
    action: str  # what changes, e.g. "no entries until 2024-03-13T08:00:00Z", "risk x0.5"
    explanation: str  # exactly one sentence
    evidence_trade_ids: tuple[int, ...]  # journal rows (trade_id) that caused it


# ---------------------------------------------------------------------------- expectancy guard
def _guard_sample(
    trades: Sequence[Trade], pair: str, cfg: StrategyConfig, now_ts: int | None = None
) -> list[Trade] | None:
    """The pair's last ``guard_window`` closed trades (by exit time), or None if fewer."""
    closed = [
        t
        for t in trades
        if t.pair == pair
        and t.exit_ts is not None
        and t.r_multiple is not None
        and (now_ts is None or t.exit_ts <= now_ts)
    ]
    if len(closed) < cfg.guard_window:
        return None
    closed.sort(key=lambda t: (t.exit_ts, t.trade_id))
    return closed[len(closed) - cfg.guard_window :]


def _mean_r(sample: Sequence[Trade]) -> float:
    return math.fsum(t.r_multiple for t in sample) / len(sample)  # type: ignore[misc]


def risk_multiplier(trades: Sequence[Trade], pair: str, cfg: StrategyConfig) -> float:
    """1.0, or ``cfg.guard_risk_mult`` when the expectancy guard is on and triggered.

    ``trades`` must only contain trades already closed at decision time (no look-ahead).
    """
    if not cfg.expectancy_guard:
        return 1.0
    sample = _guard_sample(trades, pair, cfg)
    if sample is not None and _mean_r(sample) < 0:
        return cfg.guard_risk_mult
    return 1.0


def _guard_adaptations(
    trades: Sequence[Trade], cfg: StrategyConfig, now_ts: int
) -> list[Adaptation]:
    out: list[Adaptation] = []
    for pair in sorted({t.pair for t in trades}):
        sample = _guard_sample(trades, pair, cfg, now_ts)
        if sample is None:
            continue
        mean_r = _mean_r(sample)
        if mean_r >= 0:
            continue
        explanation = (
            f"{pair}'s last {len(sample)} closed trades average {mean_r:.3f}R (below 0), so new "
            f"{pair} entries risk {cfg.guard_risk_mult:g}x the normal amount until that "
            "average is back at or above 0."
        )
        ids = tuple(t.trade_id for t in sample)
        out.append(Adaptation(GUARD, pair, f"risk x{cfg.guard_risk_mult:g}", explanation, ids))
    return out


# ---------------------------------------------------------------------------- audit
def _halt_adaptation(
    breakers: CircuitBreakers, cfg: StrategyConfig, now_ts: int, equity: float
) -> Adaptation | None:
    pnl, ids = breakers.window_pnl(now_ts)
    threshold = breakers.halt_threshold(equity)
    if pnl >= threshold:
        return None
    lifts = breakers.halt_lifts_at(now_ts, equity)
    until = "lasts until the losses age out" if lifts is None else f"lifts at {ms_to_iso(lifts)}"
    explanation = (
        f"No pair may open a trade because trades {', '.join(f'#{i}' for i in ids)} closed in "
        f"the {cfg.loss_window_days:g} days to {ms_to_iso(now_ts)} realized {pnl:.2f}, below "
        f"the -{cfg.weekly_loss_limit_pct:g}% limit of {threshold:.2f} on equity {equity:.2f}, "
        f"and the halt {until} if no other trade closes."
    )
    return Adaptation(R9, "ALL", "halt all entries", explanation, ids)


def audit(
    trades: Sequence[Trade], cfg: StrategyConfig, now_ts: int, equity: float
) -> list[Adaptation]:
    """Every adaptation active at ``now_ts``, using only trades closed at or before it.

    Order: R9 benches (by pair), R9 halt, then expectancy guards (by pair).
    """
    known = [t for t in trades if t.exit_ts is not None and t.exit_ts <= now_ts]
    breakers = CircuitBreakers.from_journal(known, cfg)
    out = [
        Adaptation(
            R9,
            bench.pair,
            f"no entries until {ms_to_iso(bench.until_ts)}",
            bench.sentence(cfg.consecutive_sl_limit, cfg.bench_hours),
            bench.trade_ids,
        )
        for bench in breakers.benches(now_ts)
    ]
    halt = _halt_adaptation(breakers, cfg, now_ts, equity)
    if halt is not None:
        out.append(halt)
    if cfg.expectancy_guard:
        out.extend(_guard_adaptations(known, cfg, now_ts))
    return out


# ---------------------------------------------------------------------------- CLI
def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(adaptations: Sequence[Adaptation]) -> str:
    """Markdown table ``rule | scope | action | explanation | evidence``."""
    if not adaptations:
        return "No active adaptations."
    lines = ["| rule | scope | action | explanation | evidence |", "|---|---|---|---|---|"]
    for a in adaptations:
        evidence = ", ".join(f"#{i}" for i in a.evidence_trade_ids)
        cells = (a.rule, a.scope, a.action, a.explanation, evidence)
        lines.append("| " + " | ".join(_cell(c) for c in cells) + " |")
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m research.trendbot.journal_rules",
        description="List every adaptation the bot applies because of past trades.",
    )
    parser.add_argument("--journal", required=True, type=Path, help="trade journal CSV")
    parser.add_argument("--equity", required=True, type=float, help="current equity (quote)")
    parser.add_argument(
        "--now",
        default=None,
        help="ISO-8601 UTC time or epoch ms to audit at (default: the current UTC time)",
    )
    parser.add_argument(
        "--expectancy-guard",
        action="store_true",
        help="also evaluate the optional L_expectancy_guard layer (off by default)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.equity > 0:
        parser.error("--equity must be > 0")
    try:
        now_ts = time.time_ns() // 1_000_000 if args.now is None else iso_to_ms(args.now)
    except ValueError:
        parser.error(f"--now: not an ISO-8601 time or epoch ms: {args.now!r}")
    try:
        trades = read_journal(args.journal)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    cfg = StrategyConfig(expectancy_guard=args.expectancy_guard)
    adaptations = audit(trades, cfg, now_ts, args.equity)
    closed = sum(1 for t in trades if t.exit_ts is not None)
    print(
        f"Journal {args.journal}: {len(trades)} trades ({closed} closed), audited at "
        f"{ms_to_iso(now_ts)} with equity {args.equity:.2f}."
    )
    print()
    print(render_markdown(adaptations))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
