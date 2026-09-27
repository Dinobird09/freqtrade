"""R5 news blackout: a CSV news calendar and an O(log n) blackout check.

An entry at time ``ts`` for ``pair`` is blocked if ANY event satisfies:

- ``impact == "high"`` and ``scope`` is ``"ALL"``, the pair's base asset, or
  ``"EXCHANGE:<cfg.exchange_id>"``, and ``|event.ts - ts| <= news_blackout_hours``;
- the pair's base is ``BNB``, ``kind`` is in ``cfg.bnb_event_kinds`` (any impact), ``scope``
  is ``"ALL"``, ``"BNB"`` or ``"EXCHANGE:binance"``, and
  ``|event.ts - ts| <= bnb_event_blackout_hours``.

Both boundaries are inclusive: an event exactly 2h away blocks, 2h + 1 ms does not. The
lookup bisects the sorted event times for ``[ts - w, ts + w]`` (``w`` = widest window that
can apply to the pair) and only filters the events inside that slice.

CSV format (header required): ``time_utc,scope,impact,kind,note``. ``time_utc`` is either
ISO-8601 with an explicit offset (``2024-03-12T12:30:00Z`` or ``...+00:00``; other offsets
are converted to UTC) or integer epoch milliseconds. Timestamps without an offset are
rejected so that a local-time typo can never silently shift a blackout window.
"""

from __future__ import annotations

import csv
import re
from bisect import bisect_left, bisect_right
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import StrategyConfig
from .models import HOUR_MS, Decision, NewsEvent, base_of


RULE = "R5_news_blackout"
CSV_HEADER = ("time_utc", "scope", "impact", "kind", "note")
IMPACTS = ("high", "medium", "low")
KNOWN_KINDS = ("macro", "regulatory", "legal", "unlock", "bnb_burn", "launchpool", "other")
BNB_EVENT_SCOPES = frozenset({"ALL", "BNB", "EXCHANGE:binance"})

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_INT_MS_RE = re.compile(r"[0-9]+")
_ASSET_RE = re.compile(r"[A-Z0-9]+")
_EXCHANGE_ID_RE = re.compile(r"[a-z0-9_.-]+")


# ---------------------------------------------------------------------------- parsing
def parse_time_utc(text: str) -> int:
    """Parse integer epoch ms or offset-aware ISO-8601 into epoch ms (UTC)."""
    s = text.strip()
    if _INT_MS_RE.fullmatch(s):
        return int(s)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        raise ValueError(f"time_utc {s!r} is neither integer ms nor ISO-8601") from None
    if dt.utcoffset() is None:
        raise ValueError(f"time_utc {s!r} has no UTC offset; append 'Z' or '+00:00'")
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


def _parse_row(fields: list[str], where: str) -> NewsEvent:
    if len(fields) != len(CSV_HEADER):
        raise ValueError(
            f"{where}: expected {len(CSV_HEADER)} columns ({','.join(CSV_HEADER)}), "
            f"got {len(fields)}"
        )
    time_s, scope_s, impact_s, kind_s, note_s = fields
    try:
        ts = parse_time_utc(time_s)
        scope = normalize_scope(scope_s)
        impact = _normalize_impact(impact_s)
        kind = _normalize_kind(kind_s)
    except ValueError as exc:
        raise ValueError(f"{where}: {exc}") from None
    return NewsEvent(ts=ts, scope=scope, impact=impact, kind=kind, note=note_s.strip())


def load_events(path: str | Path) -> list[NewsEvent]:
    """Load a news calendar CSV; events are returned sorted by time (stable for ties).

    Raises ``ValueError`` naming the file and line number of the first malformed row.
    Blank lines are ignored. A header-only file yields an empty list.
    """
    p = Path(path)
    events: list[NewsEvent] = []
    with p.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if header is None:
            raise ValueError(f"{p}: line 1: empty file, expected header {','.join(CSV_HEADER)}")
        if tuple(h.strip().lower() for h in header) != CSV_HEADER:
            raise ValueError(
                f"{p}: line {reader.line_num}: header must be {','.join(CSV_HEADER)} "
                f"(got {','.join(header)})"
            )
        for fields in reader:
            if not any(f.strip() for f in fields):
                continue
            events.append(_parse_row(fields, f"{p}: line {reader.line_num}"))
    events.sort(key=lambda e: e.ts)
    return events


def _normalize_event(ev: NewsEvent) -> NewsEvent:
    """Canonical casing for events built in code (synthetic worlds); rejects bad ones."""
    try:
        scope = normalize_scope(ev.scope)
        impact = _normalize_impact(ev.impact)
    except ValueError as exc:
        raise ValueError(f"invalid news event {ev!r}: {exc}") from None
    kind = ev.kind.strip().lower()
    return NewsEvent(ts=int(ev.ts), scope=scope, impact=impact, kind=kind, note=ev.note.strip())


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
        """Index slice ``[lo, hi)`` of events with ``ts - w <= event.ts <= ts + w``."""
        return (
            bisect_left(self._times, ts - half_width_ms),
            bisect_right(self._times, ts + half_width_ms),
        )

    def _applicable(self, ev: NewsEvent, base: str) -> tuple[float, float, str] | None:
        """(window_ms, window_hours, label) of the widest blackout ``ev`` imposes on ``base``."""
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
        width = self._max_window_ms(base)
        lo, hi = self._window(ts, width)
        hit: tuple[int, NewsEvent, float, str] | None = None
        for idx in range(lo, hi):
            ev = self._events[idx]
            rule = self._applicable(ev, base)
            if rule is None:
                continue
            dist = abs(ev.ts - ts)
            if dist <= rule[0] and (hit is None or dist < hit[0]):
                hit = (dist, ev, rule[1], rule[2])
        if hit is None:
            return Decision(True, RULE, self._clear_reason(pair, ts, base))
        _, ev, hours, label = hit
        return Decision(
            False,
            RULE,
            f"{pair} entry at {format_time_utc(ts)} is inside the +/-{hours:g}h {label} "
            f"blackout of the {ev.impact}-impact {ev.kind} event at {format_time_utc(ev.ts)} "
            f"(scope {ev.scope}; note: {ev.note or 'none'}).",
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
            f"No high-impact news within +/-{self._news_hours:g}h{extra} of the {pair} entry "
            f"at {when}."
        )
