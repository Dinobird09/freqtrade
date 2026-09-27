"""CSV trade journal: one row per ``Trade``, readable by humans and exactly re-loadable.

Layout (binding for every writer and reader of the journal):
- ``JOURNAL_COLUMNS`` holds one column per ``Trade`` field except ``features``, in field order.
  Each timestamp column (``signal_ts``, ``entry_ts``, ``exit_ts``; int ms UTC) is followed by
  an ISO-8601 UTC helper column (``signal_time_utc``, ...) for human review. The helper
  columns are write-only: ``read_journal`` always uses the ms columns and ignores them.
- ``Trade.features`` is flattened into ``f_<name>`` columns appended after
  ``JOURNAL_COLUMNS``: the union of feature keys over all written trades, sorted, so the
  header is stable. A trade lacking a feature leaves that cell empty.
- ``None`` is written as the empty string and read back as ``None``. Floats are written with
  ``repr`` (shortest exact round-trip), so ``read_journal(write_journal(x))`` reproduces the
  ``Trade`` objects exactly. ``exit_reason`` must therefore never be the empty string.
"""

from __future__ import annotations

import csv
import operator
from collections.abc import Callable, Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .models import Trade


FEATURE_PREFIX = "f_"

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_MS = timedelta(milliseconds=1)

# (Trade field, parser, optional). Order = Trade field order; features are handled apart.
_FIELDS: tuple[tuple[str, Callable[[str], object], bool], ...] = (
    ("trade_id", int, False),
    ("pair", str, False),
    ("variant", str, False),
    ("signal_ts", int, False),
    ("entry_ts", int, False),
    ("entry_price", float, False),
    ("stop", float, False),
    ("target", float, False),
    ("qty", float, False),
    ("risk_amount", float, False),
    ("risk_pct", float, False),
    ("stop_method", str, False),
    ("exit_ts", int, True),
    ("exit_price", float, True),
    ("exit_reason", str, True),
    ("fees", float, False),
    ("pnl", float, True),
    ("r_multiple", float, True),
    ("ml_prob", float, True),
    ("notes", str, False),
)

# ms timestamp column -> ISO-8601 UTC helper column written right after it.
TIME_HELPER_COLUMNS = {
    "signal_ts": "signal_time_utc",
    "entry_ts": "entry_time_utc",
    "exit_ts": "exit_time_utc",
}


def _build_columns() -> tuple[str, ...]:
    cols: list[str] = []
    for name, _parser, _optional in _FIELDS:
        cols.append(name)
        if name in TIME_HELPER_COLUMNS:
            cols.append(TIME_HELPER_COLUMNS[name])
    return tuple(cols)


JOURNAL_COLUMNS: tuple[str, ...] = _build_columns()


# ---------------------------------------------------------------------------- time helpers
def ms_to_iso(ts: int) -> str:
    """Epoch ms -> ``"2024-03-12T12:30:00Z"`` (milliseconds shown only when non-zero)."""
    dt = _EPOCH + timedelta(milliseconds=ts)
    base = dt.strftime("%Y-%m-%dT%H:%M:%S")
    ms = ts % 1000
    return f"{base}.{ms:03d}Z" if ms else f"{base}Z"


def iso_to_ms(text: str) -> int:
    """ISO-8601 (``Z``/offset aware; naive means UTC) or an integer ms string -> epoch ms."""
    s = text.strip()
    if s.lstrip("-").isdigit():
        return int(s)
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return (dt - _EPOCH) // _MS


# ---------------------------------------------------------------------------- writing
def journal_header(trades: Iterable[Trade]) -> tuple[str, ...]:
    """Full header for ``trades``: ``JOURNAL_COLUMNS`` + sorted ``f_<name>`` feature columns."""
    keys: set[str] = set()
    for t in trades:
        keys.update(t.features)
    return JOURNAL_COLUMNS + tuple(FEATURE_PREFIX + k for k in sorted(keys))


def _format(value: object, parser: Callable[[str], object]) -> str:
    if value is None:
        return ""
    if parser is float:
        return repr(float(value))  # type: ignore[arg-type]
    if parser is int:
        return str(operator.index(value))  # type: ignore[arg-type]  # refuses floats
    return str(value)


def _row(trade: Trade, feature_keys: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for name, parser, _optional in _FIELDS:
        value = getattr(trade, name)
        out.append(_format(value, parser))
        if name in TIME_HELPER_COLUMNS:
            out.append("" if value is None else ms_to_iso(value))
    for key in feature_keys:
        value = trade.features.get(key)
        out.append(_format(value, float))
    return out


def write_journal(trades: Iterable[Trade], path: str | Path) -> None:
    """Write one CSV row per trade (order preserved); creates parent directories."""
    rows = list(trades)
    header = journal_header(rows)
    feature_keys = tuple(c[len(FEATURE_PREFIX) :] for c in header[len(JOURNAL_COLUMNS) :])
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(header)
        for t in rows:
            writer.writerow(_row(t, feature_keys))


# ---------------------------------------------------------------------------- reading
def _parse_cell(raw: str, name: str, parser: Callable[[str], object], optional: bool) -> object:
    if raw == "":
        if optional:
            return None
        if parser is str:
            return ""
        raise ValueError(f"column {name!r} may not be empty")
    try:
        return parser(raw)
    except ValueError:
        raise ValueError(f"column {name!r}: cannot parse {raw!r} as {parser.__name__}")


def _parse_row(row: dict[str, str], feature_cols: list[str]) -> Trade:
    kwargs = {
        name: _parse_cell(row[name], name, parser, optional) for name, parser, optional in _FIELDS
    }
    features = {
        col[len(FEATURE_PREFIX) :]: _parse_cell(row[col], col, float, False)
        for col in feature_cols
        if row[col] != ""
    }
    return Trade(**kwargs, features=features)  # type: ignore[arg-type]


def read_journal(path: str | Path) -> list[Trade]:
    """Load a journal written by ``write_journal`` (exact round-trip).

    Uses the ms timestamp columns only; ISO helper columns and unknown extra columns are
    ignored. Raises ``ValueError`` (with the CSV line number) on missing columns or bad cells.
    """
    p = Path(path)
    with p.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        missing = [name for name, _p, _o in _FIELDS if name not in header]
        if missing:
            raise ValueError(f"{p}: journal is missing required columns {missing}")
        feature_cols = [c for c in header if c.startswith(FEATURE_PREFIX)]
        trades: list[Trade] = []
        for row in reader:
            if None in row or any(v is None for v in row.values()):
                raise ValueError(f"{p}: line {reader.line_num}: wrong number of cells")
            try:
                trades.append(_parse_row(row, feature_cols))
            except ValueError as exc:
                raise ValueError(f"{p}: line {reader.line_num}: {exc}")
    return trades
