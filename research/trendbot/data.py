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

The timeframe is verified whenever it is known (``load_dataset``, or ``load_candles_csv``
with ``timeframe=``):

- every candle must be EPOCH-ALIGNED, ``ts % timeframe_ms == 0`` (CONTRACT v4 D2): exchange 4H
  candles open at 00:00, 04:00, ... UTC, so a file whose clock is shifted (e.g. by a local
  time zone or a DST hour) is refused with the first offending line and ts instead of
  silently moving every decision time. Week-multiple timeframes are the one exception: they
  are aligned to Monday 00:00 UTC (``ts % tf == 4 days``), the convention of exchange weekly
  candles (1970-01-01 was a Thursday);
- every spacing between consecutive candles must be a whole multiple of the timeframe and
  the SMALLEST spacing must equal it, so a 1h or a 1d file saved under a ``-4h.csv`` name is
  refused with the offending line numbers and timestamps.

Provenance (CONTRACT v4 D2): ``fetch_data`` writes ``manifest.json`` next to the candle files
(exchange id, ccxt version, symbols, timeframe, since/until, fetched_at_utc and, per file,
name, symbol, rows, first/last ts and sha256). :func:`verify_manifest` returns ``"real"`` only
when every requested file is listed there with a matching sha256 (and matching symbol,
timeframe, rows and first/last ts), else ``"unverified-csv"``. An offline check cannot
authenticate an exchange download: the manifest proves only that the files are byte-identical
to what was recorded, so it makes a laundered or hand-edited CSV a deliberate act (someone has
to rewrite the manifest) instead of an accident.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from itertools import groupby, pairwise
from pathlib import Path

from .models import DAY_MS, HOUR_MS, Candle


CSV_COLUMNS = ("ts", "open", "high", "low", "close", "volume")

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_ONE_MS = timedelta(milliseconds=1)
_ISO_PREFIX = re.compile(r"\d{4}-\d{2}-\d{2}([T ]|$)")  # extended format only: YYYY-MM-DD
_UNIT_MS = {"m": 60_000, "h": HOUR_MS, "d": DAY_MS, "w": 7 * DAY_MS}
_WEEK_MS = 7 * DAY_MS
_MONDAY_ANCHOR_MS = 4 * DAY_MS  # 1970-01-05T00:00Z, the first Monday after the epoch

MANIFEST_NAME = "manifest.json"
MANIFEST_FORMAT = "trendbot-candle-manifest/1"
PROVENANCE_REAL = "real"
PROVENANCE_UNVERIFIED = "unverified-csv"
MANIFEST_NOTE = (
    "An offline check cannot authenticate an exchange download: manifest.json only shows that "
    "the candle files are byte-identical to what fetch_data recorded (or to what someone "
    "deliberately wrote into it). It makes a laundered CSV a deliberate act, not an accident."
)


# ---------------------------------------------------------------------- time helpers
def timeframe_to_ms(timeframe: str) -> int:
    """``"4h" -> 14_400_000``. Units: m (minutes), h, d, w."""
    tf = timeframe.strip()
    num, unit = tf[:-1], tf[-1:]
    if unit not in _UNIT_MS or not (num.isascii() and num.isdigit()) or int(num) <= 0:
        raise ValueError(f"unsupported timeframe {timeframe!r} (expected e.g. '1h', '4h', '1d')")
    return int(num) * _UNIT_MS[unit]


def parse_iso_utc(text: str, assume_utc: bool = False) -> int:
    """Extended-format ISO-8601 (``YYYY-MM-DD[THH:MM[:SS[.fff]]][Z|+HH:MM]``) -> epoch ms.

    Offset-less values raise unless ``assume_utc``. Basic-format strings are rejected on
    purpose: ``datetime.fromisoformat`` would read ``"1704081600000.0"`` as the year 1704.
    """
    s = text.strip()
    try:
        if not _ISO_PREFIX.match(s):
            raise ValueError
        dt = datetime.fromisoformat(s)
    except ValueError:
        raise ValueError(f"{text!r} is not an ISO-8601 date/time (YYYY-MM-DD...)") from None
    if dt.tzinfo is None:
        if not assume_utc:
            raise ValueError(f"ISO-8601 ts {text!r} has no UTC offset (write e.g. '...T00:00:00Z')")
        dt = dt.replace(tzinfo=UTC)
    return (dt - _EPOCH) // _ONE_MS


def parse_ts(text: str) -> int:
    """Parse integer milliseconds or an offset-aware ISO-8601 string to epoch ms (UTC)."""
    s = text.strip()
    if s.isascii() and s.isdigit():
        return int(s)
    if not _ISO_PREFIX.match(s):
        raise ValueError(f"ts {text!r} is neither integer milliseconds nor ISO-8601")
    return parse_iso_utc(s)


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


def alignment_offset(ts: int, timeframe_ms: int) -> int:
    """How far ``ts`` lies past the latest candle boundary of ``timeframe_ms`` (0 = aligned).

    Boundaries are multiples of the timeframe since the epoch; for week multiples they are
    shifted to Monday 00:00 UTC (the exchange convention for weekly candles).
    """
    anchor = _MONDAY_ANCHOR_MS if timeframe_ms % _WEEK_MS == 0 else 0
    return (ts - anchor) % timeframe_ms


def _alignment_problem(ts: int, tf_ms: int) -> str | None:
    off = alignment_offset(ts, tf_ms)
    if not off:
        return None
    tf = _fmt_span(tf_ms)
    return (
        f"ts {ts} ({ts_to_iso(ts)}) is not epoch-aligned: {tf} candles open at multiples of "
        f"{tf} since 1970-01-01T00:00Z, this one is {_fmt_span(off)} past a boundary "
        "(shifted clock or local-time export?)"
    )


def validate_candles(candles: Sequence[Candle], timeframe_ms: int | None = None) -> None:
    """Apply the loader's checks to in-memory candles; raise ValueError naming the index.

    With ``timeframe_ms`` every candle must also be epoch-aligned (see the module docstring).
    """
    prev_ts: int | None = None
    for i, c in enumerate(candles):
        problem = candle_problem(c) or _order_problem(prev_ts, c.ts)
        if problem is None and timeframe_ms is not None:
            problem = _alignment_problem(c.ts, timeframe_ms)
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


def _read_rows(p: Path) -> list[tuple[int, Candle]]:
    """``(1-based line number, candle)`` for every data row of a validated candle file."""
    out: list[tuple[int, Candle]] = []
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
            out.append((reader.line_num, candle))
            prev_ts = candle.ts
    return out


def _fmt_span(ms: int) -> str:
    """``14_400_000 -> "4h"``, ``86_400_000 -> "1d"`` (largest whole unit; else ms)."""
    for unit in ("w", "d", "h", "m"):
        if ms % _UNIT_MS[unit] == 0:
            return f"{ms // _UNIT_MS[unit]}{unit}"
    return f"{ms} ms"


def _check_alignment(p: Path, rows: Sequence[tuple[int, Candle]], tf_ms: int) -> None:
    """Refuse a file with any candle that does not open on a ``tf_ms`` boundary (D2)."""
    for line, c in rows:
        problem = _alignment_problem(c.ts, tf_ms)
        if problem:
            raise ValueError(f"{p}: line {line}: {problem}")


def _check_timeframe(p: Path, rows: Sequence[tuple[int, Candle]], tf_ms: int) -> None:
    """The spacing checks (a wrong timeframe is named as such), then the epoch alignment of
    every candle (a correctly spaced but shifted file, e.g. +1h, fails here)."""
    _check_spacing(p, rows, tf_ms)
    _check_alignment(p, rows, tf_ms)


def _check_spacing(p: Path, rows: Sequence[tuple[int, Candle]], tf_ms: int) -> None:
    """Refuse a file whose candles are not ``tf_ms`` candles (wrong or mixed timeframe).

    Every spacing between consecutive candles must be a whole multiple of ``tf_ms`` (gaps
    are allowed) AND the SMALLEST spacing must equal ``tf_ms``: a 1d file saved as
    ``*-4h.csv`` passes the first test (1d is six 4h bars) but not the second.
    """
    tf = _fmt_span(tf_ms)
    if len(rows) < 2:
        raise ValueError(
            f"{p}: {len(rows)} candle(s): at least 2 are needed to verify the {tf} spacing"
        )
    for (la, a), (lb, b) in pairwise(rows):
        step = b.ts - a.ts
        if step % tf_ms:
            raise ValueError(
                f"{p}: lines {la}-{lb}: candles at {a.ts} ({ts_to_iso(a.ts)}) and {b.ts} "
                f"({ts_to_iso(b.ts)}) are {_fmt_span(step)} apart, not a whole number of {tf}: "
                "wrong timeframe or misaligned file"
            )
    # The FIRST pair of consecutive rows with the smallest spacing.
    k = min(range(len(rows) - 1), key=lambda i: rows[i + 1][1].ts - rows[i][1].ts)
    (la, a), (lb, b) = rows[k], rows[k + 1]
    step = b.ts - a.ts
    if step != tf_ms:
        raise ValueError(
            f"{p}: the smallest spacing between consecutive candles is {_fmt_span(step)} "
            f"(first at lines {la}-{lb}: {a.ts} ({ts_to_iso(a.ts)}) -> {b.ts} "
            f"({ts_to_iso(b.ts)})), not {tf}: the file does not hold {tf} candles "
            "(wrong timeframe in the file name?)"
        )


def load_candles_csv(path: str | Path, timeframe: str | int | None = None) -> list[Candle]:
    """Load and validate one candle file; raise ValueError with the 1-based line number.

    With ``timeframe`` (``"4h"`` or milliseconds) every candle must be epoch-aligned
    (``ts % timeframe_ms == 0``; the first misaligned line and ts are named) and the candle
    spacing is verified: every step a whole multiple of it and the smallest step EQUAL to it
    (see ``_check_spacing``), so a shifted file or one holding another timeframe is refused.
    """
    p = Path(path)
    rows = _read_rows(p)
    if timeframe is not None:
        tf_ms = timeframe if isinstance(timeframe, int) else timeframe_to_ms(timeframe)
        if isinstance(tf_ms, bool) or tf_ms <= 0:
            raise ValueError(f"timeframe must be > 0 ms, got {timeframe!r}")
        _check_timeframe(p, rows, tf_ms)
    return [c for _, c in rows]


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

    Besides the per-file checks, every pair must be non-empty and hold epoch-aligned
    ``timeframe`` candles: every ``ts % timeframe_ms == 0`` (a +1h-shifted 4H file is refused
    at its first line), each spacing a whole multiple of the timeframe (gaps are allowed; see
    ``gap_report``) and the smallest spacing exactly one timeframe. That catches a 1h file
    saved under a 4h name as well as a 1d file saved under a 4h name. Provenance is a
    separate question: see :func:`verify_manifest`.
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
        rows = _read_rows(path)
        if not rows:
            raise ValueError(f"{path}: header only, no candles")
        _check_timeframe(path, rows, tf_ms)
        out[pair] = [c for _, c in rows]
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


# ---------------------------------------------------------------------- provenance (D2)
def file_sha256(path: str | Path) -> str:
    """Hex sha256 of a file's raw bytes."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


_MANIFEST_TOP_FIELDS = ("exchange_id", "ccxt_version", "timeframe", "fetched_at_utc")
_MANIFEST_FILE_FIELDS = ("name", "symbol", "timeframe", "rows", "first_ts", "last_ts", "sha256")


def _read_manifest(path: Path) -> tuple[dict[str, object] | None, list[str]]:
    """Parsed manifest (None if unusable) and its structural problems."""
    if not path.is_file():
        return None, [f"{path}: no {MANIFEST_NAME} (written by fetch_data next to the candles)"]
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        return None, [f"{path}: not valid JSON ({exc})"]
    if not isinstance(manifest, dict):
        return None, [f"{path}: the manifest must be a JSON object"]
    problems: list[str] = []
    if manifest.get("format") != MANIFEST_FORMAT:
        problems.append(f"{path}: format is {manifest.get('format')!r}, not {MANIFEST_FORMAT!r}")
    for key in _MANIFEST_TOP_FIELDS:
        if not isinstance(manifest.get(key), str) or not manifest[key]:
            problems.append(f"{path}: missing or empty {key!r}")
    files = manifest.get("files")
    if not isinstance(files, list) or not all(isinstance(e, dict) for e in files):
        return None, [*problems, f"{path}: 'files' must be a list of objects"]
    names = [e.get("name") for e in files]
    dups = sorted({str(n) for n in names if names.count(n) > 1})
    if dups:
        problems.append(f"{path}: files listed more than once: {', '.join(dups)}")
    return manifest, problems


def _file_problems(
    name: str, pair: str, timeframe: str, fpath: Path, actual_sha: str | None, entry: object
) -> list[str]:
    """Why ``fpath`` is not the file the manifest entry describes (empty list = verified)."""
    if actual_sha is None:
        return [f"{name}: file not found in the data directory"]
    if not isinstance(entry, dict):
        return [f"{name}: not listed in {MANIFEST_NAME}"]
    missing = [k for k in _MANIFEST_FILE_FIELDS if k not in entry]
    if missing:
        return [f"{name}: manifest entry lacks {', '.join(missing)}"]
    out: list[str] = []
    if entry["sha256"] != actual_sha:
        out.append(
            f"{name}: sha256 {actual_sha} does not match the manifest's {entry['sha256']} "
            "(file changed after download, or not the downloaded file)"
        )
    if entry["symbol"] != pair or entry["timeframe"] != timeframe:
        out.append(
            f"{name}: manifest lists {entry['symbol']} {entry['timeframe']}, "
            f"expected {pair} {timeframe}"
        )
    if out:
        return out
    try:
        rows = _read_rows(fpath)
    except ValueError as exc:
        return [f"{name}: {exc}"]
    facts = (len(rows), rows[0][1].ts if rows else None, rows[-1][1].ts if rows else None)
    listed = (entry["rows"], entry["first_ts"], entry["last_ts"])
    if facts != listed:
        out.append(f"{name}: file has (rows, first_ts, last_ts) {facts}, manifest says {listed}")
    return out


def verify_manifest(
    data_dir: str | Path, pairs: Sequence[str], timeframe: str = "4h"
) -> tuple[str, dict[str, object]]:
    """``("real", details)`` only if EVERY pair's candle file is listed in
    ``<data_dir>/manifest.json`` with a matching sha256 (and matching symbol, timeframe,
    rows and first/last ts); otherwise ``("unverified-csv", details)``. Never raises for a
    missing or malformed manifest: that is simply unverified.

    ``details``: ``manifest_path``, ``manifest_sha256`` (None if absent), ``timeframe``,
    ``exchange_id`` / ``ccxt_version`` / ``fetched_at_utc`` (from the manifest, or None),
    ``exchange_ids`` (sorted per-file venues), ``files`` ({file name: {pair, path, sha256,
    listed, verified}}), ``problems`` (one sentence each; empty iff "real") and ``note`` (the
    D2 caveat: an offline check cannot authenticate an exchange download).
    """
    root = Path(data_dir)
    mpath = root / MANIFEST_NAME
    manifest, problems = _read_manifest(mpath)
    listed: dict[str, object] = {}
    if manifest is not None:
        listed = {str(e.get("name")): e for e in manifest["files"]}  # type: ignore[union-attr]
    files: dict[str, dict[str, object]] = {}
    venues: set[str] = set()
    if not pairs:
        problems.append("no pairs to verify")
    for pair in pairs:
        name = pair_filename(pair, timeframe)
        fpath = root / name
        actual = file_sha256(fpath) if fpath.is_file() else None
        entry = listed.get(name)
        file_problems = (
            _file_problems(name, pair, timeframe, fpath, actual, entry)
            if manifest is not None
            else []
        )
        problems.extend(file_problems)
        if isinstance(entry, dict) and isinstance(entry.get("exchange_id"), str):
            venues.add(entry["exchange_id"])
        files[name] = {
            "pair": pair,
            "path": str(fpath),
            "sha256": actual,
            "listed": isinstance(entry, dict),
            "verified": manifest is not None and not file_problems,
        }
    head = manifest or {}
    details: dict[str, object] = {
        "manifest_path": str(mpath),
        "manifest_sha256": file_sha256(mpath) if mpath.is_file() else None,
        "timeframe": timeframe,
        "exchange_id": head.get("exchange_id"),
        "ccxt_version": head.get("ccxt_version"),
        "fetched_at_utc": head.get("fetched_at_utc"),
        "exchange_ids": sorted(venues),
        "files": files,
        "problems": problems,
        "note": MANIFEST_NOTE,
    }
    return (PROVENANCE_UNVERIFIED if problems else PROVENANCE_REAL), details
