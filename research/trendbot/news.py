"""R5 news blackout: a CSV news calendar and an O(log n) blackout check, without look-ahead.

An event applies to an entry on ``pair`` with a window ``w`` when:

- ``impact == "high"`` and ``scope`` is ``"ALL"``, the pair's base asset, or
  ``"EXCHANGE:<cfg.exchange_id>"``: ``w = news_blackout_hours`` (+/-2h by mandate);
- the pair's base is ``BNB``, ``kind`` is in ``cfg.bnb_event_kinds`` (any impact), and
  ``scope`` is ``"ALL"``, ``"BNB"`` or ``"EXCHANGE:binance"``: ``w = bnb_event_blackout_hours``
  (+/-24h by mandate).

If both apply, the wider window is used. An applicable event blocks an entry at ``ts`` iff
``ts`` lies in its block interval (CONTRACT.md v3 C2, both ends inclusive)::

    [max(event.ts - w, known_from), event.ts + w]          (see block_interval)

``known_from`` is when the event became knowable. It is ``event.known_from_ts`` when set,
otherwise the kind default:

- :data:`SCHEDULED_KINDS` (macro, unlock, bnb_burn, launchpool) are on a calendar in advance:
  ``known_from = -inf``, so the full ``+/-w`` window applies;
- :data:`UNSCHEDULED_KINDS` (regulatory, legal, other) are headlines nobody could see coming:
  ``known_from = event.ts``, so they block only ``[event.ts, event.ts + w]``. Blocking the
  hours BEFORE a surprise headline would be look-ahead: a backtest would dodge news the
  live bot could not have known about.

An explicit ``known_from_ts`` narrows the pre-event window (a print announced only 30 min
ahead blocks from then on) or, for an unscheduled kind that was in fact announced, widens it
back up to ``event.ts - w``; it never widens anything beyond ``+/-w``. A ``known_from_ts``
later than ``event.ts + w`` gives an empty interval (known only after its window had ended).
Boundaries are inclusive: a scheduled event exactly 2h away blocks, 2h + 1 ms does not.
The lookup bisects the sorted event times for ``[ts - W, ts + W]`` (``W`` = widest window
that can apply to the pair) and only filters the events inside that slice.

CSV format (header required), with or without the optional last column::

    time_utc,scope,impact,kind,note[,known_from_utc]

``time_utc`` and ``known_from_utc`` are either ISO-8601 with an explicit offset
(``2024-03-12T12:30:00Z`` or ``...+00:00``; other offsets are converted to UTC) or integer
epoch milliseconds. Timestamps without an offset are rejected so that a local-time typo can
never silently shift a blackout window. An empty ``known_from_utc`` cell means the kind
default above.
"""

from __future__ import annotations

import csv
import math
import re
from bisect import bisect_left, bisect_right
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import StrategyConfig
from .models import HOUR_MS, Decision, NewsEvent, base_of


RULE = "R5_news_blackout"
KNOWN_FROM_COLUMN = "known_from_utc"
CSV_HEADER = ("time_utc", "scope", "impact", "kind", "note")
CSV_HEADER_WITH_KNOWN_FROM = (*CSV_HEADER, KNOWN_FROM_COLUMN)
ACCEPTED_HEADERS = (CSV_HEADER, CSV_HEADER_WITH_KNOWN_FROM)
IMPACTS = ("high", "medium", "low")
KNOWN_KINDS = ("macro", "regulatory", "legal", "unlock", "bnb_burn", "launchpool", "other")
# CONTRACT.md v3 C2: scheduled kinds are knowable in advance (default known_from = -inf);
# unscheduled kinds only from their own timestamp (default known_from = event.ts).
SCHEDULED_KINDS: tuple[str, ...] = ("macro", "unlock", "bnb_burn", "launchpool")
UNSCHEDULED_KINDS: tuple[str, ...] = ("regulatory", "legal", "other")
BNB_EVENT_SCOPES = frozenset({"ALL", "BNB", "EXCHANGE:binance"})

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_INT_MS_RE = re.compile(r"[0-9]+")
_ASSET_RE = re.compile(r"[A-Z0-9]+")
_EXCHANGE_ID_RE = re.compile(r"[a-z0-9_.-]+")


# ---------------------------------------------------------------------------- semantics
def is_scheduled(kind: str) -> bool:
    """True for :data:`SCHEDULED_KINDS`, False for :data:`UNSCHEDULED_KINDS` (case-insensitive).

    Raises ``ValueError`` for any other kind, so no event has an undefined default.
    """
    k = kind.strip().lower()
    if k in SCHEDULED_KINDS:
        return True
    if k in UNSCHEDULED_KINDS:
        return False
    raise ValueError(f"kind {kind.strip()!r} is not one of {', '.join(KNOWN_KINDS)}")


def known_from(event: NewsEvent) -> float:
    """When ``event`` became knowable, epoch ms: ``event.known_from_ts`` if set, else ``-inf``
    for a scheduled kind and ``event.ts`` for an unscheduled one (CONTRACT.md v3 C2)."""
    scheduled = is_scheduled(event.kind)
    if event.known_from_ts is not None:
        return int(event.known_from_ts)
    return -math.inf if scheduled else int(event.ts)


def block_interval(event: NewsEvent, window_ms: float) -> tuple[int, int]:
    """Inclusive block interval ``(start, end)`` of ``event`` for a +/-``window_ms`` blackout.

    ``start = max(event.ts - w, known_from(event))`` and ``end = event.ts + w``: an entry
    at ``ts`` is blocked iff ``start <= ts <= end``. The interval is empty (``start > end``)
    if the event only became known after its window ended. ``w = floor(window_ms)``, which
    is exactly equivalent to ``<= window_ms`` for integer-ms timestamps. Pure: this is the
    single definition of R5 timing, shared by :class:`NewsCalendar` and the invariants.
    """
    if not (math.isfinite(window_ms) and window_ms >= 0):
        raise ValueError(f"window_ms must be finite and >= 0, got {window_ms!r}")
    w = math.floor(window_ms)
    ts = int(event.ts)
    start = max(ts - w, known_from(event))
    return int(start), ts + w


# ---------------------------------------------------------------------------- parsing
def parse_time_utc(text: str, column: str = "time_utc") -> int:
    """Parse integer epoch ms or offset-aware ISO-8601 into epoch ms (UTC)."""
    s = text.strip()
    if _INT_MS_RE.fullmatch(s):
        return int(s)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        raise ValueError(f"{column} {s!r} is neither integer ms nor ISO-8601") from None
    if dt.utcoffset() is None:
        raise ValueError(f"{column} {s!r} has no UTC offset; append 'Z' or '+00:00'")
    delta = dt - _EPOCH
    return (delta.days * 86_400 + delta.seconds) * 1000 + delta.microseconds // 1000


def format_time_utc(ts: int) -> str:
    """Epoch ms -> ``YYYY-MM-DDTHH:MM:SSZ`` (milliseconds shown only when non-zero)."""
    dt = _EPOCH + timedelta(milliseconds=ts)
    if ts % 1000:
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts % 1000:03d}Z"
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_scope(raw: str) -> str:
    """Canonical scope: ``all`` -> ``ALL``, ``bnb`` -> ``BNB``, ``exchange:Binance`` ->
    ``EXCHANGE:binance``. Raises ``ValueError`` for an empty or malformed scope.
    """
    s = raw.strip()
    if not s:
        raise ValueError("scope is empty (use ALL, a base asset or EXCHANGE:<ccxt id>)")
    head, sep, tail = s.partition(":")
    if sep:
        exchange = tail.strip().lower()
        if head.strip().upper() != "EXCHANGE" or not _EXCHANGE_ID_RE.fullmatch(exchange):
            raise ValueError(f"scope {s!r} is not of the form EXCHANGE:<ccxt id>")
        return f"EXCHANGE:{exchange}"
    asset = s.upper()
    if not _ASSET_RE.fullmatch(asset):
        raise ValueError(f"scope {s!r} is not ALL, a base asset or EXCHANGE:<ccxt id>")
    return asset


def _normalize_impact(raw: str) -> str:
    impact = raw.strip().lower()
    if impact not in IMPACTS:
        raise ValueError(f"impact {raw.strip()!r} is not one of {', '.join(IMPACTS)}")
    return impact


def _normalize_kind(raw: str) -> str:
    kind = raw.strip().lower()
    if kind not in KNOWN_KINDS:
        raise ValueError(f"kind {raw.strip()!r} is not one of {', '.join(KNOWN_KINDS)}")
    return kind


def _normalize_known_from(value: object) -> int | None:
    if value is None:
        return None
    integral_float = isinstance(value, float) and math.isfinite(value) and value.is_integer()
    if isinstance(value, bool) or not (isinstance(value, int) or integral_float):
        raise ValueError(f"known_from_ts must be integer epoch ms or None, got {value!r}")
    return int(value)  # type: ignore[arg-type]


def _parse_known_from(text: str) -> int | None:
    """``known_from_utc`` cell: empty -> ``None`` (kind default), else a timestamp."""
    if not text.strip():
        return None
    return parse_time_utc(text, KNOWN_FROM_COLUMN)


def _parse_row(fields: list[str], where: str, header: tuple[str, ...]) -> NewsEvent:
    if len(fields) != len(header):
        raise ValueError(
            f"{where}: expected {len(header)} columns ({','.join(header)}), got {len(fields)}"
        )
    time_s, scope_s, impact_s, kind_s, note_s = fields[:5]
    try:
        ts = parse_time_utc(time_s)
        scope = normalize_scope(scope_s)
        impact = _normalize_impact(impact_s)
        kind = _normalize_kind(kind_s)
        known = _parse_known_from(fields[5]) if len(header) > 5 else None
    except ValueError as exc:
        raise ValueError(f"{where}: {exc}") from None
    return NewsEvent(
        ts=ts, scope=scope, impact=impact, kind=kind, note=note_s.strip(), known_from_ts=known
    )


def load_events(path: str | Path) -> list[NewsEvent]:
    """Load a news calendar CSV; events are returned sorted by time (stable for ties).

    The header is ``time_utc,scope,impact,kind,note`` with or without a trailing
    ``known_from_utc`` column (empty cell = kind default, see module doc). Raises
    ``ValueError`` naming the file and line number of the first malformed row. Blank lines
    are ignored. A header-only file yields an empty list.
    """
    p = Path(path)
    events: list[NewsEvent] = []
    accepted = " or ".join(",".join(h) for h in ACCEPTED_HEADERS)
    with p.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        raw_header = next(reader, None)
        if raw_header is None:
            raise ValueError(f"{p}: line 1: empty file, expected header {accepted}")
        header = tuple(h.strip().lower() for h in raw_header)
        if header not in ACCEPTED_HEADERS:
            raise ValueError(
                f"{p}: line {reader.line_num}: header must be {accepted} "
                f"(got {','.join(raw_header)})"
            )
        for fields in reader:
            if not any(f.strip() for f in fields):
                continue
            events.append(_parse_row(fields, f"{p}: line {reader.line_num}", header))
    events.sort(key=lambda e: e.ts)
    return events


def _normalize_event(ev: NewsEvent) -> NewsEvent:
    """Canonical casing for events built in code (synthetic worlds); rejects bad ones."""
    try:
        scope = normalize_scope(ev.scope)
        impact = _normalize_impact(ev.impact)
        kind = _normalize_kind(ev.kind)
        known = _normalize_known_from(ev.known_from_ts)
    except ValueError as exc:
        raise ValueError(f"invalid news event {ev!r}: {exc}") from None
    return NewsEvent(
        ts=int(ev.ts),
        scope=scope,
        impact=impact,
        kind=kind,
        note=ev.note.strip(),
        known_from_ts=known,
    )


def _timing_text(ev: NewsEvent) -> str:
    """How the event's pre-window was derived, for the denial reason."""
    if ev.known_from_ts is not None:
        return f"known from {format_time_utc(ev.known_from_ts)}"
    if is_scheduled(ev.kind):
        return "scheduled, so known in advance"
    return "unscheduled, so it blocks only from the moment it happened"


# ---------------------------------------------------------------------------- calendar
class NewsCalendar:
    """Sorted, immutable news calendar answering "may ``pair`` enter at ``ts``?" (rule R5)."""

    def __init__(self, events: Iterable[NewsEvent], cfg: StrategyConfig) -> None:
        normalized = sorted((_normalize_event(e) for e in events), key=lambda e: e.ts)
        self._events: tuple[NewsEvent, ...] = tuple(normalized)
        self._times: list[int] = [e.ts for e in normalized]
        self._news_hours = cfg.news_blackout_hours
        self._bnb_hours = cfg.bnb_event_blackout_hours
        self._news_ms = cfg.news_blackout_hours * HOUR_MS
        self._bnb_ms = cfg.bnb_event_blackout_hours * HOUR_MS
        self._bnb_kinds = frozenset(k.strip().lower() for k in cfg.bnb_event_kinds)
        self._bnb_kinds_txt = "/".join(sorted(self._bnb_kinds)) or "BNB"
        self._exchange_scope = f"EXCHANGE:{cfg.exchange_id.strip().lower()}"

    def loaded(self) -> bool:
        """False if the calendar holds zero events (reports must flag such runs)."""
        return bool(self._events)

    def __len__(self) -> int:
        return len(self._events)

    @property
    def events(self) -> tuple[NewsEvent, ...]:
        return self._events

    def _max_window_ms(self, base: str) -> float:
        return max(self._news_ms, self._bnb_ms) if base == "BNB" else self._news_ms

    def _window(self, ts: int, half_width_ms: float) -> tuple[int, int]:
        """Index slice ``[lo, hi)`` of events with ``ts - w <= event.ts <= ts + w``.

        Every block interval lies inside ``[event.ts - w, event.ts + w]``, so this slice
        holds every event that can block ``ts``.
        """
        return (
            bisect_left(self._times, ts - half_width_ms),
            bisect_right(self._times, ts + half_width_ms),
        )

    def _applicable(self, ev: NewsEvent, base: str) -> tuple[float, float, str] | None:
        """(window_ms, window_hours, label) of the widest blackout ``ev`` imposes on ``base``.

        The wider window's block interval contains the narrower one's (same ``known_from``),
        so checking only the widest applicable window is exact.
        """
        best: tuple[float, float, str] | None = None
        if ev.impact == "high" and ev.scope in ("ALL", base, self._exchange_scope):
            best = (self._news_ms, self._news_hours, "news")
        bnb_rule = base == "BNB" and ev.kind in self._bnb_kinds and ev.scope in BNB_EVENT_SCOPES
        if bnb_rule and (best is None or self._bnb_ms > best[0]):
            best = (self._bnb_ms, self._bnb_hours, "BNB-event")
        return best

    def check(self, pair: str, ts: int) -> Decision:
        """R5 verdict for a long entry on ``pair`` at epoch-ms ``ts``; O(log n + k)."""
        base = base_of(pair)
        lo, hi = self._window(ts, self._max_window_ms(base))
        hit: tuple[int, NewsEvent, float, str, tuple[int, int]] | None = None
        for idx in range(lo, hi):
            ev = self._events[idx]
            rule = self._applicable(ev, base)
            if rule is None:
                continue
            start, end = block_interval(ev, rule[0])
            if not start <= ts <= end:
                continue
            dist = abs(ev.ts - ts)
            if hit is None or dist < hit[0]:
                hit = (dist, ev, rule[1], rule[2], (start, end))
        if hit is None:
            return Decision(True, RULE, self._clear_reason(pair, ts, base))
        _, ev, hours, label, (start, end) = hit
        sched = "scheduled" if is_scheduled(ev.kind) else "unscheduled"
        note = " ".join(ev.note.split()) or "none"
        return Decision(
            False,
            RULE,
            f"{pair} entry at {format_time_utc(ts)} is inside the +/-{hours:g}h {label} "
            f"blackout of the {ev.impact}-impact {sched} {ev.kind} event at "
            f"{format_time_utc(ev.ts)} (blocked {format_time_utc(start)} to "
            f"{format_time_utc(end)}: {_timing_text(ev)}; scope {ev.scope}; note: {note}).",
        )

    def _clear_reason(self, pair: str, ts: int, base: str) -> str:
        when = format_time_utc(ts)
        if not self._events:
            return (
                f"News calendar is EMPTY (no events loaded), so R5 could not block {pair} at "
                f"{when} and results are not news-filtered."
            )
        extra = ""
        if base == "BNB":
            extra = f" and no {self._bnb_kinds_txt} event within +/-{self._bnb_hours:g}h"
        return (
            f"No news blackout covers the {pair} entry at {when}: no high-impact news within "
            f"+/-{self._news_hours:g}h{extra}, each event counted only from when it was known "
            "(unscheduled news from the moment it happened)."
        )
