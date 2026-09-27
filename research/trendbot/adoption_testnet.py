"""The TESTNET stage of the adoption gate (CONTRACT.md v4 D6), used by :mod:`.adoption`.

:func:`testnet_problems` returns one sentence per problem of a record's ``testnet`` section;
the :mod:`.adoption` module docstring lists exactly what it verifies. The evidence (journal,
decisions log, candle files, optional news calendar) is hash-bound, parsed once per check,
audited with ``invariants.live_journal_violations`` and replayed with
``adoption_evidence.replay_testnet`` (``gatekeeper.LiveSession``).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .adoption_evidence import (
    TestnetReplay,
    expected_testnet_trades,
    load_candle_files,
    read_decisions_log,
    replay_differences,
    replay_testnet,
    window_days,
)
from .adoption_record import TESTNET_EXCHANGE, TESTNET_MIN_DAYS, AdoptionRecord
from .adoption_stages import (
    CheckContext,
    blank,
    cached,
    hash_bound,
    hash_bound_map,
    ids_text,
    is_int,
    parse_time,
    problems_of,
    quiet_ts,
)
from .config import StrategyConfig
from .gatekeeper import DecisionRecord
from .invariants import live_journal_violations
from .journal import ms_to_iso, read_journal
from .models import DAY_MS, Candle, NewsEvent, Trade
from .news import load_events
from .review_sheet import (
    FLAG_BAD_LEVELS,
    FLAG_LOOKAHEAD,
    FLAG_NON_FINITE,
    FLAG_PAIR,
    FLAG_RISK_CAP,
    FLAG_RR_BELOW,
    FLAG_SIZE,
    auto_flags,
    flag_code,
)


# review_sheet.auto_flags codes that mean a MANDATORY rule was broken on a trade (as opposed
# to outcome oddities such as gap-through losses or window-end exits):
#   bad_levels (R8 stop below entry / long target above entry), rr_below_min (2:1 minimum),
#   size_mismatch and risk_above_cap (R7), pair_not_configured (R7 caps), lookahead (entry
#   before the signal candle closed), non_finite (corrupt numbers).
RULE_VIOLATION_FLAGS = frozenset(
    {
        FLAG_NON_FINITE,
        FLAG_BAD_LEVELS,
        FLAG_RR_BELOW,
        FLAG_SIZE,
        FLAG_PAIR,
        FLAG_RISK_CAP,
        FLAG_LOOKAHEAD,
    }
)

R5_NOT_VERIFIABLE = (
    "R5 (news blackout) was not checked because testnet.events_path is not recorded, and R5 "
    "is not journal-verifiable without the news calendar the bot traded under"
)
NOT_MAINNET = (
    "Binance spot testnet prices and liquidity are not mainnet, so the TESTNET stage verifies "
    "execution and rule compliance, not edge"
)


# ---------------------------------------------------------------------------- testnet
@dataclass(frozen=True)
class TestnetEvidence:
    """The parsed, hash-verified D6 testnet evidence."""

    __test__ = False  # not a pytest test class despite the name

    journal: list[Trade]
    decisions: list[DecisionRecord]
    candles: dict[str, list[Candle]]
    events: list[NewsEvent] | None  # None: no calendar recorded (R5 not verified)
    starting_equity: float


def _read_hash_bound(
    ctx: CheckContext, stem: str, reader: Callable[[Path], object], what: str
) -> tuple[object | None, list[str]]:
    tn = ctx.record.testnet
    path_value, sha_value = getattr(tn, f"{stem}_path"), getattr(tn, f"{stem}_sha256")
    name = f"testnet.{stem}"
    path, problems = hash_bound(ctx, path_value, sha_value, f"{name}_path", f"{name}_sha256")
    if path is None:
        return None, problems
    try:
        return reader(path), []
    except (OSError, ValueError) as exc:
        return None, [f"{name}_path could not be read as {what} ({exc})."]


def _testnet_candles(ctx: CheckContext) -> tuple[dict[str, list[Candle]] | None, list[str]]:
    files = ctx.record.testnet.candles
    if not files:
        return None, [
            "testnet.candles is missing or empty, so the testnet decisions cannot be replayed "
            "(record every 4H candle file the testnet bot evaluated, with its sha256)."
        ]
    paths, problems = hash_bound_map(ctx, files, "testnet.candles")
    if problems:
        return None, problems
    try:
        return load_candle_files(paths.values()), []
    except (OSError, ValueError) as exc:
        return None, [f"testnet.candles could not be loaded as 4H candle files ({exc})."]


def _testnet_events(ctx: CheckContext) -> tuple[list[NewsEvent] | None, list[str]]:
    """The hash-bound testnet news calendar, ``(None, [])`` if none is recorded."""
    tn = ctx.record.testnet
    if blank(tn.events_path) and blank(tn.events_sha256):
        return None, []
    events, problems = _read_hash_bound(ctx, "events", load_events, "a news calendar")
    return events, problems  # type: ignore[return-value]


def _starting_equity_problem(value: object) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        if math.isfinite(value):
            return None
    return (
        f"testnet.starting_equity must be the positive account equity before the first testnet "
        f"trade (got {value!r}), so the R7 and 7-day-halt replay has no starting point."
    )


def _load_testnet(ctx: CheckContext) -> tuple[TestnetEvidence | None, list[str]]:
    tn = ctx.record.testnet
    journal, problems = _read_hash_bound(ctx, "journal", read_journal, "a trade journal")
    decisions, found = _read_hash_bound(ctx, "decisions", read_decisions_log, "a decisions log")
    problems += found
    candles, found = _testnet_candles(ctx)
    problems += found
    events, found = _testnet_events(ctx)
    problems += found
    problems += problems_of(_starting_equity_problem(tn.starting_equity))
    if problems:
        return None, problems
    evidence = TestnetEvidence(
        journal,
        decisions,
        candles,
        events,
        float(tn.starting_equity),  # type: ignore[arg-type]
    )
    return evidence, []


def testnet_evidence(ctx: CheckContext) -> tuple[TestnetEvidence | None, list[str]]:
    """The verified testnet evidence (parsed once per check), or None + problems."""
    return cached(ctx, "testnet", lambda: _load_testnet(ctx))  # type: ignore[return-value]


def testnet_window(record: AdoptionRecord) -> tuple[int, int] | None:
    """The typed testnet window in ms, or None if either end is missing or invalid."""
    tn = record.testnet
    start, end = quiet_ts(tn.start_utc), quiet_ts(tn.end_utc)
    return None if start is None or end is None else (start, end)


def _testnet_window_problems(ctx: CheckContext) -> list[str]:
    tn = ctx.record.testnet
    start, start_problem = parse_time(tn.start_utc, "testnet.start_utc", ctx.now)
    end, end_problem = parse_time(tn.end_utc, "testnet.end_utc", ctx.now)
    out = problems_of(start_problem, end_problem)
    if start is not None and end is not None and end - start < TESTNET_MIN_DAYS * DAY_MS:
        days = math.floor((end - start) / DAY_MS * 100) / 100  # truncate: never shows "14.00"
        out.append(
            f"testnet ran {days:.2f} days ({ms_to_iso(start)} to "
            f"{ms_to_iso(end)}), short of the required {TESTNET_MIN_DAYS} days."
        )
    signed_off = quiet_ts(ctx.record.human_review.date_utc)
    if start is not None and signed_off is not None and start < signed_off:
        out.append(
            f"testnet.start_utc {ms_to_iso(start)} is before the human review sign-off at "
            f"{ms_to_iso(signed_off)}, so the testnet run was not of an approved variant."
        )
    return out


def _violations_problem(value: object) -> str | None:
    if value is None:
        return "testnet.rule_violations is not recorded, and promotion needs an explicit 0."
    if is_int(value) and value == 0:
        return None
    return (
        f"testnet.rule_violations is {value!r}, and any rule violation on testnet blocks "
        "promotion until it is fixed and the testnet run repeated."
    )


def expectation_clause(record: AdoptionRecord) -> str:
    """D6: the trade count to expect at the baseline rate, and what the stage cannot show
    (a lower-case clause without a final full stop)."""
    window = testnet_window(record)
    days = TESTNET_MIN_DAYS if window is None else window_days(*window)
    span = "a 14-day testnet window" if window is None else f"the {days:.2f}-day testnet window"
    return (
        f"at the measured baseline rate of about 1 trade per 14 days, {span} is expected to "
        f"hold about {expected_testnet_trades(days):.1f} trades, and {NOT_MAINNET}"
    )


def _trades_problem(ctx: CheckContext) -> str | None:
    tn = ctx.record.testnet
    if is_int(tn.trades) and tn.trades >= 1:  # type: ignore[operator]
        return None
    return (
        f"testnet.trades must be at least 1 (got {tn.trades!r}): a run without trades never "
        f"exercised order, stop and exit handling ({expectation_clause(ctx.record)})."
    )


def _violation_codes(trade: Trade, cfg: StrategyConfig) -> set[str]:
    return {flag_code(f) for f in auto_flags(trade, cfg)} & RULE_VIOLATION_FLAGS


def _journal_problems(ctx: CheckContext, ev: TestnetEvidence) -> list[str]:
    """The testnet journal against the typed testnet fields and the per-trade flags."""
    rec, tn, trades = ctx.record, ctx.record.testnet, ev.journal
    out: list[str] = []
    if is_int(tn.trades) and len(trades) != tn.trades:
        out.append(
            f"the testnet journal holds {len(trades)} trades but testnet.trades is {tn.trades}, "
            "so the record does not match its evidence."
        )
    window = testnet_window(ctx.record)
    if window is not None:
        outside = [t.trade_id for t in trades if not window[0] <= t.entry_ts <= window[1]]
        if outside:
            out.append(
                f"testnet journal trades {ids_text(outside)} entered outside the testnet window "
                f"{ms_to_iso(window[0])} to {ms_to_iso(window[1])}."
            )
    foreign = [t.trade_id for t in trades if t.variant != rec.variant]
    if foreign:
        out.append(
            f"testnet journal trades {ids_text(foreign)} are not of variant {rec.variant!r}, so "
            "the testnet run was not of the variant being promoted."
        )
    flagged = [(t.trade_id, _violation_codes(t, ctx.cfg)) for t in trades]
    flagged = [(i, codes) for i, codes in flagged if codes]
    if flagged:
        codes = sorted(set().union(*(c for _i, c in flagged)))
        out.append(
            f"testnet journal trades {ids_text([i for i, _c in flagged])} break mandatory rules "
            f"({', '.join(codes)}), so testnet.rule_violations cannot be 0."
        )
    return out


def _live_audit_problems(ctx: CheckContext, ev: TestnetEvidence) -> list[str]:
    """``invariants.live_journal_violations`` of the testnet journal must be clean."""
    try:
        violations = live_journal_violations(
            ev.journal, ev.candles, ctx.cfg, ev.events, ev.starting_equity
        )
    except ValueError as exc:
        return [f"the testnet journal could not be audited in live-journal mode ({exc})."]
    if not violations:
        return []
    r5 = "R5 against testnet.events_path" if ev.events is not None else R5_NOT_VERIFIABLE
    return [
        "the testnet journal fails invariants.live_journal_violations (exit offset 0; per "
        "trade R1-R4 and R8 re-derived from the testnet candles, fill timing, planned geometry, "
        f"R7 on the realized equity from testnet.starting_equity, exits never paused; R6 and R9 "
        f"across trades; {r5}): {'; '.join(violations)}, so testnet.rule_violations cannot be 0."
    ]


def testnet_replay(ctx: CheckContext) -> TestnetReplay | None:
    """The D6 LiveSession replay of the verified testnet evidence (None if not possible)."""
    ev, problems = testnet_evidence(ctx)
    window = testnet_window(ctx.record)
    if ev is None or problems or window is None:
        return None

    def run() -> TestnetReplay:
        return replay_testnet(
            ctx.cfg,
            ev.candles,
            ev.journal,
            ev.decisions,
            ev.events,
            ev.starting_equity,
            window[0],
            window[1],
            inject_ml_vetoes=ctx.record.model_fingerprint is not None,
            variant=ctx.record.variant,
        )

    return cached(ctx, "replay", run)  # type: ignore[return-value]


def _replay_problems(ctx: CheckContext, ev: TestnetEvidence) -> list[str]:
    try:
        replay = testnet_replay(ctx)
    except ValueError as exc:
        return [f"the testnet candles could not be replayed with gatekeeper.LiveSession ({exc})."]
    if replay is None:
        return []  # the window problems already block
    out = [
        f"the LiveSession replay could not inject the testnet journal ({p}), so the journal "
        "is not a run of the gatekeeper."
        for p in replay.problems
    ]
    diffs = replay_differences(replay, ev.journal, ev.decisions, ev.events is not None)
    if diffs:
        out.append(
            "the gatekeeper.LiveSession replay of the testnet candles, with the journal's actual "
            "fills and exits injected, does not reproduce the run (the replayed allowed "
            "(pair, signal_ts) set must equal the journal's entries and the decisions log's "
            f"allowed rows): {'; '.join(diffs)}."
        )
    return out


def testnet_problems(ctx: CheckContext) -> list[str]:
    tn = ctx.record.testnet
    exchange_problem = None
    if tn.exchange != TESTNET_EXCHANGE:
        exchange_problem = (
            f"testnet.exchange is {tn.exchange!r}, but this stage must run on "
            f"{TESTNET_EXCHANGE!r} (never a real-money account)."
        )
    out = problems_of(exchange_problem) + _testnet_window_problems(ctx)
    out += problems_of(_trades_problem(ctx), _violations_problem(tn.rule_violations))
    ev, problems = testnet_evidence(ctx)
    out += problems
    if ev is not None:
        out += _journal_problems(ctx, ev)
        out += _live_audit_problems(ctx, ev)
        out += _replay_problems(ctx, ev)
    return out
