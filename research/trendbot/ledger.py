"""Holdout ledger (CONTRACT v4 D3): every adoptable TEST look is appended, never rewritten.

A TEST window can only answer ONE pre-registered question honestly. Each time a candidate is
backtested on it the researcher sees its TEST numbers, and a candidate revised and re-run
on the same window is judged on data that already shaped it. The label rule spends
``alpha = 0.05`` over ``m = metrics.M_CANDIDATES = 4`` candidates per TEST window, so this
ledger counts how many DISTINCT candidates have looked at a window.

File format: JSON Lines (one object per line, UTF-8, append-only). ``run_research`` appends
one line per pre-registered ADOPTABLE candidate that got a TEST backtest (baseline,
TRAIN-selected variant, fitted ML layer, expectancy guard; never a context discovery
variant and never a test-only config)::

    {"run_utc": "2026-01-01T00:00:00Z", "argv": ["--data-dir", "research/data", ...],
     "variant": "base", "config_fingerprint": "<sha256>", "model_fingerprint": null,
     "split_utc": "2023-03-14T00:00:00Z",
     "pairs": [{"pair": "BTC/USDT", "file_sha256": "<sha256>",
                "test_start_ts": 1678752000000, "test_end_ts": 1735689600000}, ...]}

- ``test_start_ts`` / ``test_end_ts`` are the pair's TEST window ``[split, close of its last
  candle)`` in ms UTC (half-open);
- ``file_sha256`` is the sha256 of the pair's candle file (real data) or of the canonical
  CSV text of the in-memory series (:func:`series_sha256`, synthetic data).

Counting (:func:`distinct_looks`): for one pair and one TEST window, the DISTINCT
``(config_fingerprint, model_fingerprint)`` among the lines for that pair whose TEST window
overlaps it. Re-running the identical candidates is therefore NOT a new look, while a
revised config (new fingerprint) or a refitted model is. ``adoption.check_promotion`` blocks
(``ADOPT_holdout``) a record from HUMAN_REVIEW on when the count for any of its pairs
exceeds ``m``. After a reviewer N, or any other revision, a variant needs TEST data it has
never seen: a TEST window starting at or after the latest ``test_end_ts`` of every earlier
look; it is never re-run on the same TEST window.

Default location (D3): ``<data-dir>/.test_looks.jsonl`` for real data (it travels with the
data, so every out-dir run on it shares one ledger) and ``<out-dir>/test_looks.jsonl`` for
synthetic data; ``run_research --ledger PATH`` overrides it. The ledger is not hash-bound
(it is append-only); deleting it is a deliberate act that the ledger cannot prevent.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .models import Candle


LEDGER_NAME_REAL = ".test_looks.jsonl"
LEDGER_NAME_SYNTHETIC = "test_looks.jsonl"
LOOK_KEYS = (
    "run_utc",
    "argv",
    "variant",
    "config_fingerprint",
    "model_fingerprint",
    "split_utc",
    "pairs",
)
PAIR_KEYS = ("pair", "file_sha256", "test_start_ts", "test_end_ts")


@dataclass(frozen=True)
class PairWindow:
    """One pair's TEST window ``[test_start_ts, test_end_ts)`` and the data it was cut from."""

    pair: str
    file_sha256: str
    test_start_ts: int
    test_end_ts: int

    def to_dict(self) -> dict[str, object]:
        return {
            "pair": self.pair,
            "file_sha256": self.file_sha256,
            "test_start_ts": self.test_start_ts,
            "test_end_ts": self.test_end_ts,
        }


@dataclass(frozen=True)
class Look:
    """One ledger line: one adoptable candidate that got a TEST backtest."""

    run_utc: str
    argv: tuple[str, ...]
    variant: str
    config_fingerprint: str
    model_fingerprint: str | None
    split_utc: str
    pairs: tuple[PairWindow, ...]

    @property
    def identity(self) -> tuple[str, str | None]:
        """What makes two looks the same look: ``(config_fingerprint, model_fingerprint)``."""
        return self.config_fingerprint, self.model_fingerprint

    def to_json(self) -> str:
        """The ledger line (keys in :data:`LOOK_KEYS` order, compact, no trailing newline)."""
        data = {
            "run_utc": self.run_utc,
            "argv": list(self.argv),
            "variant": self.variant,
            "config_fingerprint": self.config_fingerprint,
            "model_fingerprint": self.model_fingerprint,
            "split_utc": self.split_utc,
            "pairs": [p.to_dict() for p in self.pairs],
        }
        return json.dumps(data, separators=(",", ":"), allow_nan=False)


# ---------------------------------------------------------------------------- hashing
def series_sha256(candles: Iterable[Candle]) -> str:
    """sha256 of a candle series' canonical CSV text (``ts,open,high,low,close,volume`` header,
    ``repr`` floats, ``\\n`` line ends): the identity of in-memory (synthetic) data."""
    digest = hashlib.sha256(b"ts,open,high,low,close,volume\n")
    for c in candles:
        row = f"{c.ts},{c.open!r},{c.high!r},{c.low!r},{c.close!r},{c.volume!r}\n"
        digest.update(row.encode("ascii"))
    return digest.hexdigest()


def holdout_windows(
    data: Mapping[str, Sequence[Candle]],
    split: int,
    timeframe_ms: int,
    file_sha256: Mapping[str, str],
) -> tuple[PairWindow, ...]:
    """Every pair's TEST window: ``[split, last candle ts + timeframe)``, pairs sorted."""
    out = []
    for pair in sorted(data):
        candles = data[pair]
        if not candles:
            raise ValueError(f"{pair}: no candles, so there is no TEST window")
        end = candles[-1].ts + timeframe_ms
        out.append(PairWindow(pair, file_sha256[pair], split, max(split, end)))
    return tuple(out)


# ---------------------------------------------------------------------------- file I/O
def default_ledger_path(
    out_dir: str | Path, data_dir: str | Path | None = None, synthetic: bool = False
) -> Path:
    """D3 default: ``<data-dir>/.test_looks.jsonl`` (real), ``<out-dir>/test_looks.jsonl``
    (synthetic)."""
    if synthetic or data_dir is None:
        return Path(out_dir) / LEDGER_NAME_SYNTHETIC
    return Path(data_dir) / LEDGER_NAME_REAL


def append_looks(path: str | Path, looks: Iterable[Look]) -> int:
    """Append one line per look (creates the file and its directory); returns the count."""
    lines = [look.to_json() + "\n" for look in looks]
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8", newline="\n") as fh:
        fh.writelines(lines)
    return len(lines)


def _require(obj: Mapping[str, object], keys: Sequence[str], where: str) -> None:
    missing = [k for k in keys if k not in obj]
    if missing:
        raise ValueError(f"{where}: missing {', '.join(missing)}")


def _int(value: object, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{where} must be an integer (got {value!r})")
    return value


def _str(value: object, where: str, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{where} must be a non-empty string (got {value!r})")
    return value


def _pair_window(raw: object, where: str) -> PairWindow:
    if not isinstance(raw, dict):
        raise ValueError(f"{where} must be a JSON object")
    _require(raw, PAIR_KEYS, where)
    start = _int(raw["test_start_ts"], f"{where}.test_start_ts")
    end = _int(raw["test_end_ts"], f"{where}.test_end_ts")
    if end < start:
        raise ValueError(f"{where}: test_end_ts {end} is before test_start_ts {start}")
    return PairWindow(
        str(_str(raw["pair"], f"{where}.pair")),
        str(_str(raw["file_sha256"], f"{where}.file_sha256")),
        start,
        end,
    )


def parse_look(line: str, where: str = "ledger line") -> Look:
    """One ledger line -> :class:`Look`; ``ValueError`` naming ``where`` if malformed."""
    try:
        raw = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{where}: not valid JSON ({exc})") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{where}: must be a JSON object")
    _require(raw, LOOK_KEYS, where)
    argv = raw["argv"]
    if not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise ValueError(f"{where}.argv must be a list of strings")
    pairs = raw["pairs"]
    if not isinstance(pairs, list) or not pairs:
        raise ValueError(f"{where}.pairs must be a non-empty list")
    return Look(
        run_utc=str(_str(raw["run_utc"], f"{where}.run_utc")),
        argv=tuple(argv),
        variant=str(_str(raw["variant"], f"{where}.variant")),
        config_fingerprint=str(_str(raw["config_fingerprint"], f"{where}.config_fingerprint")),
        model_fingerprint=_str(raw["model_fingerprint"], f"{where}.model_fingerprint", True),
        split_utc=str(_str(raw["split_utc"], f"{where}.split_utc")),
        pairs=tuple(_pair_window(p, f"{where}.pairs[{i}]") for i, p in enumerate(pairs)),
    )


def read_ledger(path: str | Path) -> list[Look]:
    """Every look in the ledger (empty if the file does not exist); blank lines are skipped.

    Raises ``ValueError`` with the 1-based line number on a malformed line: a damaged ledger
    must be repaired, never silently under-counted.
    """
    p = Path(path)
    if not p.is_file():
        return []
    looks = []
    for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        if line.strip():
            looks.append(parse_look(line, f"{p}:{n}"))
    return looks


# ---------------------------------------------------------------------------- counting
def overlaps(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    """True if the half-open windows ``[a_start, a_end)`` and ``[b_start, b_end)`` overlap."""
    return a_start < b_end and b_start < a_end


def distinct_looks(
    looks: Iterable[Look], pair: str, test_start_ts: int, test_end_ts: int
) -> set[tuple[str, str | None]]:
    """The distinct ``(config_fingerprint, model_fingerprint)`` that looked at ``pair`` on a
    TEST window overlapping ``[test_start_ts, test_end_ts)``."""
    out: set[tuple[str, str | None]] = set()
    for look in looks:
        windows = (w for w in look.pairs if w.pair == pair)
        if any(
            overlaps(w.test_start_ts, w.test_end_ts, test_start_ts, test_end_ts) for w in windows
        ):
            out.add(look.identity)
    return out


def look_counts(looks: Sequence[Look], windows: Iterable[PairWindow]) -> dict[str, int]:
    """Per pair of ``windows``: how many distinct candidates have looked at that TEST window."""
    return {
        w.pair: len(distinct_looks(looks, w.pair, w.test_start_ts, w.test_end_ts)) for w in windows
    }


@dataclass(frozen=True)
class HoldoutStatus:
    """What one run did to the ledger and where the TEST windows stand afterwards."""

    path: Path
    appended: int  # lines this run appended
    counts: dict[str, int]  # pair -> cumulative distinct looks on this run's TEST window
    limit: int  # metrics.M_CANDIDATES
    windows: tuple[PairWindow, ...]

    @property
    def over_limit(self) -> list[str]:
        """Pairs whose TEST window has been looked at by more than ``limit`` candidates."""
        return [p for p, n in self.counts.items() if n > self.limit]


def record_looks(
    path: str | Path, looks: Sequence[Look], windows: tuple[PairWindow, ...], limit: int
) -> HoldoutStatus:
    """Append ``looks``, then re-read the whole ledger and count per pair of ``windows``."""
    appended = append_looks(path, looks)
    counts = look_counts(read_ledger(path), windows)
    return HoldoutStatus(Path(path), appended, counts, limit, windows)
