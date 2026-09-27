"""Candle CSV storage and data-quality checks.

File format (one file per pair and timeframe, e.g. ``BTC_USDT-4h.csv``)::

    ts,open,high,low,close,volume
    1546300800000,3701.23,3810.16,3695.0,3797.14,1234.5

``ts`` is the candle OPEN time in UTC, either integer milliseconds since the epoch or an
ISO-8601 string with an explicit offset (``2024-01-01T00:00:00Z``). ISO strings without an
offset are rejected rather than guessed, because a silently shifted clock is a look-ahead bug.
``save_candles_csv`` always writes integer milliseconds.

Loading is strict: rows must be strictly ascending (no duplicates), prices finite and > 0,
``high >= max(open, close)``, ``low <= min(open, close)`` and ``volume >= 0``. Any violation
raises ``ValueError`` naming the file and the 1-based line number, so bad data is fixed at the
source instead of leaking into a backtest. Missing candles are NOT an error; use
``gap_report`` to list them.
"""

from __future__ import annotations

import csv
import math
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from itertools import groupby, pairwise
from pathlib import Path

from .models import DAY_MS, HOUR_MS, Candle


CSV_COLUMNS = ("ts", "open", "high", "low", "close", "volume")

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_ONE_MS = timedelta(milliseconds=1)
_UNIT_MS = {"m": 60_000, "h": HOUR_MS, "d": DAY_MS, "w": 7 * DAY_MS}


# ---------------------------------------------------------------------- time helpers
def timeframe_to_ms(timeframe: str) -> int:
    """``"4h" -> 14_400_000``. Units: m (minutes), h, d, w."""
    tf = timeframe.strip()
    num, unit = tf[:-1], tf[-1:]
    if unit not in _UNIT_MS or not (num.isascii() and num.isdigit()) or int(num) <= 0:
        raise ValueError(f"unsupported timeframe {timeframe!r} (expected e.g. '1h', '4h', '1d')")
    return int(num) * _UNIT_MS[unit]


def parse_ts(text: str) -> int:
    """Parse integer milliseconds or an offset-aware ISO-8601 string to epoch ms (UTC)."""
    s = text.strip()
    if s.isascii() and s.isdigit():
        return int(s)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        raise ValueError(f"ts {text!r} is neither integer milliseconds nor ISO-8601") from None
    if dt.tzinfo is None:
        raise ValueError(f"ISO-8601 ts {text!r} has no UTC offset (write e.g. '...T00:00:00Z')")
    return (dt - _EPOCH) // _ONE_MS


def ts_to_iso(ts: int) -> str:
    """Epoch ms -> ``"2024-01-01T00:00:00Z"`` (UTC, second precision)."""
    return (_EPOCH + timedelta(milliseconds=ts)).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------- validation
def candle_problem(c: Candle) -> str | None:
    """Return a one-line description of what is wrong with a single candle, or None."""
    values = (c.open, c.high, c.low, c.close, c.volume)
    if not all(math.isfinite(v) for v in values):
        return f"non-finite value in {values}"
    if min(c.open, c.high, c.low, c.close) <= 0:
        return "prices must be > 0"
    if c.high < max(c.open, c.close):
        return f"high {c.high!r} < max(open, close) {max(c.open, c.close)!r}"
    if c.low > min(c.open, c.close):
        return f"low {c.low!r} > min(open, close) {min(c.open, c.close)!r}"
    if c.volume < 0:
        return f"negative volume {c.volume!r}"
    return None


def _order_problem(prev_ts: int | None, ts: int) -> str | None:
    if prev_ts is None or ts > prev_ts:
        return None
    if ts == prev_ts:
        return f"duplicate ts {ts} ({ts_to_iso(ts)})"
    return f"ts {ts} ({ts_to_iso(ts)}) is not after the previous row ({ts_to_iso(prev_ts)})"


def validate_candles(candles: Sequence[Candle]) -> None:
    """Apply the loader's checks to in-memory candles; raise ValueError naming the index."""
    prev_ts: int | None = None
    for i, c in enumerate(candles):
        problem = candle_problem(c) or _order_problem(prev_ts, c.ts)
        if problem:
            raise ValueError(f"candle #{i} (ts={c.ts}): {problem}")
        prev_ts = c.ts


# ---------------------------------------------------------------------- CSV I/O
def _parse_float(name: str, text: str) -> float:
    try:
        return float(text)
    except ValueError:
        raise ValueError(f"{name} {text!r} is not a number") from None


def _row_to_candle(row: Sequence[str], prev_ts: int | None) -> Candle:
    if len(row) != len(CSV_COLUMNS):
        raise ValueError(f"expected {len(CSV_COLUMNS)} fields, got {len(row)}")
    ts = parse_ts(row[0])
    o, h, lo, c, v = (_parse_float(n, t) for n, t in zip(CSV_COLUMNS[1:], row[1:], strict=True))
    candle = Candle(ts=ts, open=o, high=h, low=lo, close=c, volume=v)
    problem = candle_problem(candle) or _order_problem(prev_ts, ts)
    if problem:
        raise ValueError(problem)
    return candle


def load_candles_csv(path: str | Path) -> list[Candle]:
    """Load and validate one candle file; raise ValueError with the 1-based line number."""
    p = Path(path)
    out: list[Candle] = []
    with p.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.reader(fh)
        header = next(reader, None)
        if header is None or [h.strip().lower() for h in header] != list(CSV_COLUMNS):
            raise ValueError(f"{p}: line 1: header must be {','.join(CSV_COLUMNS)} (got {header})")
        prev_ts: int | None = None
        for row in reader:
            if not any(cell.strip() for cell in row):
                continue  # tolerate blank lines
            try:
                candle = _row_to_candle(row, prev_ts)
            except ValueError as exc:
                raise ValueError(f"{p}: line {reader.line_num}: {exc}") from None
            out.append(candle)
            prev_ts = candle.ts
    return out


def save_candles_csv(candles: Iterable[Candle], path: str | Path) -> None:
    """Write candles (ts as integer ms, floats round-trip exactly); creates parent dirs.

    Written to a temporary file first and then renamed, so an interrupted download never
    leaves a truncated file behind. No validation is done here; loading validates.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(CSV_COLUMNS)
        for c in candles:
            prices = (c.open, c.high, c.low, c.close, c.volume)
            writer.writerow([int(c.ts), *(repr(float(x)) for x in prices)])
    tmp.replace(p)


def pair_filename(pair: str, timeframe: str = "4h") -> str:
    """``("BTC/USDT", "4h") -> "BTC_USDT-4h.csv"`` (``/`` and ``:`` become ``_``)."""
    return f"{pair.replace('/', '_').replace(':', '_')}-{timeframe}.csv"


def load_dataset(
    data_dir: str | Path, pairs: Sequence[str], timeframe: str = "4h"
) -> dict[str, list[Candle]]:
    """Load ``<data_dir>/<pair_filename>`` for every pair, in the given order.

    Besides the per-file checks, every pair must be non-empty and its candle spacing must
    be a whole multiple of the timeframe (catches e.g. a 1h file saved under a 4h name).
    Gaps are allowed; see ``gap_report``.
    """
    tf_ms = timeframe_to_ms(timeframe)
    root = Path(data_dir)
    out: dict[str, list[Candle]] = {}
    for pair in pairs:
        path = root / pair_filename(pair, timeframe)
        if not path.is_file():
            raise FileNotFoundError(
                f"{path}: no {timeframe} candle file for {pair}; download it with "
                "`python -m research.trendbot.fetch_data` (needs network + ccxt)"
            )
        candles = load_candles_csv(path)
        if not candles:
            raise ValueError(f"{path}: header only, no candles")
        for a, b in pairwise(candles):
            if (b.ts - a.ts) % tf_ms:
                raise ValueError(
                    f"{path}: candles at {ts_to_iso(a.ts)} and {ts_to_iso(b.ts)} are not a "
                    f"whole number of {timeframe} apart; wrong timeframe or misaligned file"
                )
        out[pair] = candles
    return out


# ---------------------------------------------------------------------- quality helpers
def gap_report(candles: Sequence[Candle], timeframe_ms: int) -> list[tuple[int, int]]:
    """``(from_ts, to_ts)`` for every pair of consecutive candles more than one bar apart."""
    if timeframe_ms <= 0:
        raise ValueError("timeframe_ms must be > 0")
    return [(a.ts, b.ts) for a, b in pairwise(candles) if b.ts - a.ts > timeframe_ms]


def resample_candles(candles: Sequence[Candle], source_ms: int, target_ms: int) -> list[Candle]:
    """Aggregate ``source_ms`` candles into epoch-aligned ``target_ms`` candles.

    Only COMPLETE buckets (every constituent source candle present) are emitted, so a
    missing hour becomes a visible gap instead of a silently wrong 4H candle. Used when an
    exchange does not serve the target timeframe natively (Coinbase has no 4h candles).
    """
    if source_ms <= 0 or target_ms <= 0 or target_ms % source_ms:
        raise ValueError(f"target {target_ms} ms is not a whole multiple of source {source_ms} ms")
    per = target_ms // source_ms
    out: list[Candle] = []
    for start, group in groupby(candles, key=lambda c: c.ts - c.ts % target_ms):
        bucket = list(group)
        if [c.ts for c in bucket] != [start + k * source_ms for k in range(per)]:
            continue
        out.append(
            Candle(
                ts=start,
                open=bucket[0].open,
                high=max(c.high for c in bucket),
                low=min(c.low for c in bucket),
                close=bucket[-1].close,
                volume=sum(c.volume for c in bucket),
            )
        )
    return out
