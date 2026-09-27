"""Evidence recomputation for the adoption gate: pure functions over parsed evidence files.

Nothing here decides a promotion (that is :mod:`.adoption`); each function recomputes one
piece of evidence from the files a record points to, or parses such a file strictly:

- :func:`recompute_walk_forward` and the pinned label schema (CONTRACT.md v4 D1):
  ``label_params`` is the closed key set :data:`LABEL_PARAM_KEYS`; :func:`label_params_problems`
  also pins ``m == metrics.M_CANDIDATES``, ``alpha == metrics.ALPHA``,
  ``n_boot >= metrics.N_BOOT`` and ``seed == walkforward.SUMMARY_SEED``. The metrics functions
  are called with explicit keyword arguments (no signature introspection).
- The C5 inputs (D1): :func:`recompute_train_dd_p95` (TRAIN journal) and
  :func:`recompute_test_mtm` (TEST journal + candle files).
- :func:`manifest_problems` (D2): the data files must be the files ``data.verify_manifest``
  accepts as ``"real"``. An offline check cannot authenticate an exchange download: the
  manifest only makes a laundered CSV a deliberate act (someone has to rewrite the manifest)
  instead of an accident.
- :func:`read_ledger`, :func:`own_looks`, :func:`distinct_looks` (D3 holdout ledger).
- :func:`write_decisions_log` / :func:`read_decisions_log` (D6 decisions log format).
- :func:`replay_testnet` / :func:`replay_differences` (D6): ``gatekeeper.LiveSession``
  replayed over the testnet candles with the journal's ACTUAL fills and exits injected.
"""

from __future__ import annotations

import csv
import json
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import metrics
from .config import RULE_IDS, StrategyConfig
from .data import MANIFEST_NAME, load_candles_csv, pair_filename, verify_manifest
from .gatekeeper import CAPITAL, ML, R7, R8, DecisionRecord, EntryDecision, LiveSession
from .indicators import compute_features
from .journal import iso_to_ms, ms_to_iso
from .models import DAY_MS, Candle, EntryFilter, FeatureRow, NewsEvent, SignalCheck, Trade
from .walkforward import SUMMARY_SEED


CANDLE_TIMEFRAME = "4h"  # config fixes the timeframe at 4H; files are data.pair_filename(p, "4h")
C5_TOL = 1e-9  # D1: a recomputed C5 input differing by more than this blocks
PROVENANCE_REAL = "real"
Key = tuple[str, int]  # (pair, signal_ts)


# ---------------------------------------------------------------------------- D1 label params
LABEL_PARAM_KEYS: tuple[str, ...] = (
    "seed",
    "n_boot",
    "m",
    "alpha",
    "max_dd_pct",
    "train_dd_p95_pct",
    "mtm_max_dd_pct",
)
C5_KEYS: tuple[str, ...] = ("train_dd_p95_pct", "mtm_max_dd_pct")
_INT_KEYS = ("seed", "n_boot", "m")


@dataclass(frozen=True, slots=True)
class LabelParams:
    """A parsed ``walk_forward.label_params`` object (every key of the closed schema)."""

    seed: int
    n_boot: int
    m: int
    alpha: float
    max_dd_pct: float
    train_dd_p95_pct: float
    mtm_max_dd_pct: float


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return (
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)  # type: ignore[arg-type]
    )


def _value_problem(key: str, value: object) -> str | None:
    """Why one label_params value has the wrong type or range (None if it is fine)."""
    if key in _INT_KEYS:
        ok = _is_int(value) and (key == "seed" or value >= 1)  # type: ignore[operator]
        need = "an integer" if key == "seed" else "a positive integer"
    elif key == "alpha":
        ok = _is_number(value) and 0.0 < value < 1.0  # type: ignore[operator]
        need = "a number in (0, 1)"
    elif key == "max_dd_pct":
        ok = _is_number(value) and value > 0  # type: ignore[operator]
        need = "a positive number (percent)"
    else:  # the C5 inputs
        ok = _is_number(value) and value >= 0  # type: ignore[operator]
        need = "a number >= 0 (percent), required from HUMAN_REVIEW on"
    return None if ok else f"walk_forward.label_params.{key} must be {need} (got {value!r})"


def label_params_schema_problems(raw: Mapping[str, object]) -> list[str]:
    """The closed key set and the value types of ``label_params`` (no pinned values)."""
    out: list[str] = []
    extra = sorted(str(k) for k in set(raw) - set(LABEL_PARAM_KEYS))
    if extra:
        out.append(
            f"walk_forward.label_params has keys {extra} outside the closed schema "
            f"{list(LABEL_PARAM_KEYS)} (CONTRACT v4 D1)"
        )
    missing = [k for k in LABEL_PARAM_KEYS if k not in raw]
    if missing:
        c5 = [k for k in missing if k in C5_KEYS]
        why = f" (the C5 inputs {c5} are required from HUMAN_REVIEW on)" if c5 else ""
        out.append(f"walk_forward.label_params lacks {missing}{why}")
    for key in LABEL_PARAM_KEYS:
        if key in raw:
            problem = _value_problem(key, raw[key])
            if problem is not None:
                out.append(problem)
    return out


def parse_label_params(raw: Mapping[str, object]) -> LabelParams:
    """The typed closed-schema parameters; ``ValueError`` listing every schema problem."""
    problems = label_params_schema_problems(raw)
    if problems:
        raise ValueError("; ".join(problems))
    return LabelParams(
        seed=int(raw["seed"]),  # type: ignore[call-overload]
        n_boot=int(raw["n_boot"]),  # type: ignore[call-overload]
        m=int(raw["m"]),  # type: ignore[call-overload]
        alpha=float(raw["alpha"]),  # type: ignore[arg-type]
        max_dd_pct=float(raw["max_dd_pct"]),  # type: ignore[arg-type]
        train_dd_p95_pct=float(raw["train_dd_p95_pct"]),  # type: ignore[arg-type]
        mtm_max_dd_pct=float(raw["mtm_max_dd_pct"]),  # type: ignore[arg-type]
    )


def label_params_pin_problems(p: LabelParams) -> list[str]:
    """D1 pins: the label rule is fixed in advance, so a record cannot choose its own."""
    out: list[str] = []
    if p.m != metrics.M_CANDIDATES:
        out.append(
            f"walk_forward.label_params.m is {p.m} but the multiplicity correction is pinned "
            f"to metrics.M_CANDIDATES = {metrics.M_CANDIDATES} pre-registered candidates"
        )
    if p.alpha != metrics.ALPHA:
        out.append(
            f"walk_forward.label_params.alpha is {p.alpha!r} but the family-wise error rate is "
            f"pinned to metrics.ALPHA = {metrics.ALPHA!r}"
        )
    if p.n_boot < metrics.N_BOOT:
        out.append(
            f"walk_forward.label_params.n_boot is {p.n_boot} but the bootstraps need at least "
            f"metrics.N_BOOT = {metrics.N_BOOT} resamples"
        )
    if p.seed != SUMMARY_SEED:
        out.append(
            f"walk_forward.label_params.seed is {p.seed} but the bootstrap seed is pinned to "
            f"walkforward.SUMMARY_SEED = {SUMMARY_SEED}"
        )
    return out


def label_params_problems(raw: Mapping[str, object]) -> list[str]:
    """Schema problems, or (for a well-formed object) the D1 pin problems."""
    problems = label_params_schema_problems(raw)
    return problems if problems else label_params_pin_problems(parse_label_params(raw))


# ---------------------------------------------------------------------------- walk-forward
@dataclass(frozen=True)
class WalkForwardVerdict:
    """The walk-forward numbers recomputed from the TRAIN and TEST journals."""

    label: str
    label_reason: str
    train: metrics.Summary
    test: metrics.Summary
    dd_ok: bool
    dd_reason: str


def recompute_walk_forward(
    train_trades: Sequence[Trade],
    test_trades: Sequence[Trade],
    cfg: StrategyConfig,
    min_train: int,
    min_test: int,
    label_params: Mapping[str, object],
) -> WalkForwardVerdict:
    """Recompute the walk-forward verdict from the journals, exactly as the check does.

    With ``p = parse_label_params(label_params)`` (closed schema; the D1 pins are checked
    separately by :func:`label_params_pin_problems`)::

        train = metrics.summarize(train_trades, cfg.starting_capital, seed=p.seed,
                                  n_boot=p.n_boot, m=p.m, alpha=p.alpha)   # same for test
        label = metrics.label(train, test, min_train=min_train, min_test=min_test,
                              m=p.m, alpha=p.alpha)
        dd    = metrics.dd_check(test, max_dd_pct=p.max_dd_pct,
                                 train_dd_p95_pct=p.train_dd_p95_pct,
                                 mtm_max_dd_pct=p.mtm_max_dd_pct)

    ``dd_check`` uses the RECORDED C5 inputs (the check recomputes them separately with
    :func:`recompute_train_dd_p95` / :func:`recompute_test_mtm`); ``metrics.dd_limit`` caps
    ``max_dd_pct`` at ``metrics.DD_CAP_PCT``. A record writer can call this to fill the typed
    fields. Raises ``ValueError`` if the parameters do not fit the schema.
    """
    p = parse_label_params(label_params)
    equity = cfg.starting_capital
    train = metrics.summarize(
        train_trades, equity, seed=p.seed, n_boot=p.n_boot, m=p.m, alpha=p.alpha
    )
    test = metrics.summarize(
        test_trades, equity, seed=p.seed, n_boot=p.n_boot, m=p.m, alpha=p.alpha
    )
    verdict, why = metrics.label(
        train, test, min_train=min_train, min_test=min_test, m=p.m, alpha=p.alpha
    )
    dd_ok, dd_why = metrics.dd_check(
        test,
        max_dd_pct=p.max_dd_pct,
        train_dd_p95_pct=p.train_dd_p95_pct,
        mtm_max_dd_pct=p.mtm_max_dd_pct,
    )
    return WalkForwardVerdict(verdict, why, train, test, bool(dd_ok), dd_why)


def recompute_train_dd_p95(
    train_trades: Sequence[Trade], n_test: int, p: LabelParams
) -> float | None:
    """C5 ``train_dd_p95_pct``: ``metrics.train_dd_quantile(train, n_test, p.n_boot, p.seed)``."""
    return metrics.train_dd_quantile(train_trades, n_test, n_boot=p.n_boot, seed=p.seed)


def recompute_test_mtm(
    test_trades: Sequence[Trade], candles: Mapping[str, Sequence[Candle]], cfg: StrategyConfig
) -> float:
    """C5 ``mtm_max_dd_pct``: ``metrics.mtm_max_dd_pct`` of the TEST trades on the candles."""
    return metrics.mtm_max_dd_pct(test_trades, candles, cfg.starting_capital, cfg.fee_rate)


# ---------------------------------------------------------------------------- candle files
def pair_of_candle_file(path: str | Path) -> str:
    """``".../BTC_USDT-4h.csv" -> "BTC/USDT"``: the inverse of ``data.pair_filename`` for a
    ``BASE_QUOTE`` spot pair; ``ValueError`` for any other name."""
    name = Path(path).name
    suffix = f"-{CANDLE_TIMEFRAME}.csv"
    parts = name[: -len(suffix)].split("_") if name.endswith(suffix) else []
    if len(parts) != 2 or not all(parts):
        raise ValueError(
            f"{name}: a candle file must be named like data.pair_filename(pair, "
            f"{CANDLE_TIMEFRAME!r}), e.g. BTC_USDT-4h.csv"
        )
    pair = f"{parts[0]}/{parts[1]}"
    if pair_filename(pair, CANDLE_TIMEFRAME) != name:
        raise ValueError(f"{name}: not the file name of pair {pair}")
    return pair


def load_candle_files(paths: Iterable[Path]) -> dict[str, list[Candle]]:
    """``{pair: candles}`` from 4H candle files (``data.load_candles_csv`` with the timeframe,
    so unaligned or mis-spaced files are refused); ``ValueError`` for a bad name, a pair
    given twice or an invalid file."""
    out: dict[str, list[Candle]] = {}
    for path in paths:
        pair = pair_of_candle_file(path)
        if pair in out:
            raise ValueError(f"two candle files are named for {pair}")
        out[pair] = load_candles_csv(path, CANDLE_TIMEFRAME)
    return out


# ---------------------------------------------------------------------------- D2 manifest
def _manifest_symbols(
    manifest_path: Path, data_paths: Mapping[str, Path]
) -> tuple[list[str], list[str]]:
    """``(symbols, problems)``: the manifest's symbol of every data file."""
    try:
        parsed = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return [], [f"{manifest_path.name} is not a readable JSON manifest ({exc})"]
    files = parsed.get("files") if isinstance(parsed, dict) else None
    listed = {str(e.get("name")): e for e in files or () if isinstance(e, dict)}
    root = manifest_path.resolve().parent
    symbols: list[str] = []
    problems: list[str] = []
    for key, path in sorted(data_paths.items()):
        if path.resolve().parent != root:
            problems.append(f"data file {key!r} is not in the manifest's directory {root}")
            continue
        symbol = listed.get(path.name, {}).get("symbol")
        if not isinstance(symbol, str):
            problems.append(f"data file {key!r} is not listed with a symbol in the manifest")
            continue
        symbols.append(symbol)
    return symbols, problems


def manifest_problems(
    manifest_path: Path, data_paths: Mapping[str, Path], data_hashes: Mapping[str, str]
) -> list[str]:
    """Why the data files are not what ``data.verify_manifest`` calls ``"real"`` (D2).

    ``manifest_path`` must be the ``manifest.json`` of the directory holding EVERY data file;
    each file's manifest ``symbol`` is passed to ``verify_manifest(directory, symbols, "4h")``,
    which must return ``"real"`` (every file listed with a matching sha256, symbol,
    timeframe, rows and first/last ts), and the record's ``data_files`` hash of each file
    must equal the sha256 that verification measured. Empty list = verified.
    """
    if manifest_path.name != MANIFEST_NAME:
        return [f"the manifest is {manifest_path.name!r}, not the data directory's {MANIFEST_NAME}"]
    symbols, problems = _manifest_symbols(manifest_path, data_paths)
    if problems:
        return problems
    provenance, details = verify_manifest(manifest_path.parent, symbols, CANDLE_TIMEFRAME)
    if provenance != PROVENANCE_REAL:
        found = "; ".join(str(p) for p in details.get("problems", ()))  # type: ignore[union-attr]
        return [f"data.verify_manifest returns {provenance!r} ({found})"]
    measured = details.get("files", {})
    for key, path in sorted(data_paths.items()):
        info = measured.get(path.name) if isinstance(measured, dict) else None  # type: ignore[union-attr]
        if not isinstance(info, dict) or info.get("sha256") != data_hashes[key].strip().lower():
            problems.append(
                f"data file {key!r} is not the file data.verify_manifest verified with the "
                "recorded sha256"
            )
    return problems


# ---------------------------------------------------------------------------- D3 holdout ledger
@dataclass(frozen=True, slots=True)
class LedgerPair:
    pair: str
    file_sha256: str | None
    test_start_ts: int  # TEST window [test_start_ts, test_end_ts) of this pair, ms UTC
    test_end_ts: int


@dataclass(frozen=True, slots=True)
class LedgerLook:
    """One ledger line: a pre-registered candidate that got a TEST backtest."""

    line: int  # 1-based line number in the ledger file
    variant: str
    config_fingerprint: str
    model_fingerprint: str | None
    split_ts: int
    pairs: tuple[LedgerPair, ...]

    @property
    def key(self) -> tuple[str, str | None]:
        """The identity of a TEST look: ``(config_fingerprint, model_fingerprint)``."""
        return self.config_fingerprint, self.model_fingerprint


def _ledger_pair(raw: object) -> LedgerPair:
    if not isinstance(raw, dict):
        raise ValueError("every pairs entry must be a JSON object")
    pair, sha = raw.get("pair"), raw.get("file_sha256")
    start, end = raw.get("test_start_ts"), raw.get("test_end_ts")
    if not isinstance(pair, str) or not pair:
        raise ValueError(f"pairs entry {raw!r} lacks a pair")
    if sha is not None and not isinstance(sha, str):
        raise ValueError(f"{pair}: file_sha256 must be a string or null")
    if not (_is_int(start) and _is_int(end) and start <= end):  # type: ignore[operator]
        raise ValueError(f"{pair}: test_start_ts <= test_end_ts must be integer ms")
    return LedgerPair(pair, sha, start, end)  # type: ignore[arg-type]


def _ledger_look(number: int, raw: object) -> LedgerLook:
    if not isinstance(raw, dict):
        raise ValueError("a ledger line must be a JSON object")
    variant, cfp = raw.get("variant"), raw.get("config_fingerprint")
    if not isinstance(variant, str) or not isinstance(cfp, str):
        raise ValueError("variant and config_fingerprint must be strings")
    if "model_fingerprint" not in raw:
        raise ValueError("model_fingerprint (a string or null) is missing")
    mfp = raw["model_fingerprint"]
    if mfp is not None and not isinstance(mfp, str):
        raise ValueError("model_fingerprint must be a string or null")
    split = raw.get("split_utc")
    if not isinstance(split, str):
        raise ValueError("split_utc must be an ISO-8601 string")
    pairs = raw.get("pairs")
    if not isinstance(pairs, list) or not pairs:
        raise ValueError("pairs must be a non-empty list")
    return LedgerLook(number, variant, cfp, mfp, iso_to_ms(split), tuple(map(_ledger_pair, pairs)))


def read_ledger(path: str | Path) -> list[LedgerLook]:
    """Every look of a D3 holdout ledger (one JSON object per line, blank lines ignored).

    Required per line: ``variant``, ``config_fingerprint``, ``model_fingerprint`` (string or
    null), ``split_utc`` and a non-empty ``pairs`` list of ``{pair, file_sha256,
    test_start_ts, test_end_ts}``; other keys (``run_utc``, ``argv``) are ignored.
    ``ValueError`` names the first bad line.
    """
    p = Path(path)
    out: list[LedgerLook] = []
    for number, text in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        if not text.strip():
            continue
        try:
            out.append(_ledger_look(number, json.loads(text)))
        except (ValueError, OverflowError) as exc:
            raise ValueError(f"{p.name} line {number}: {exc}") from None
    return out


def own_looks(
    looks: Sequence[LedgerLook],
    config_fp: str,
    model_fp: str | None,
    split_ts: int,
    data_hashes: Iterable[str] | None,
) -> list[LedgerLook]:
    """The ledger lines that record THIS record's TEST look: same config and model
    fingerprint and split, and (when the record hash-binds data files) every pair's
    ``file_sha256`` one of the record's data file hashes."""
    hashes = None if data_hashes is None else {h.strip().lower() for h in data_hashes}
    out: list[LedgerLook] = []
    for look in looks:
        if look.key != (config_fp, model_fp) or look.split_ts != split_ts:
            continue
        if hashes is not None and any(
            (e.file_sha256 or "").strip().lower() not in hashes for e in look.pairs
        ):
            continue
        out.append(look)
    return out


def distinct_looks(
    looks: Sequence[LedgerLook], pair: str, start: int, end: int
) -> set[tuple[str, str | None]]:
    """Distinct ``(config, model)`` fingerprints of the looks whose TEST window on ``pair``
    overlaps ``[start, end)`` (half-open: a window starting at or after ``end`` is new)."""
    return {
        look.key
        for look in looks
        for e in look.pairs
        if e.pair == pair and e.test_start_ts < end and start < e.test_end_ts
    }


# ---------------------------------------------------------------------------- D6 decisions log
DECISIONS_COLUMNS: tuple[str, ...] = (
    "pair",
    "signal_ts",
    "signal_utc",
    "allowed",
    "rule",
    "reason",
)
_REQUIRED_DECISION_COLUMNS = ("pair", "signal_ts", "allowed", "rule", "reason")
_BOOL_TEXT = {"true": True, "false": False}


def write_decisions_log(records: Iterable[DecisionRecord], path: str | Path) -> None:
    """Write a D6 decisions log: one row per evaluated signal candle (``LiveSession
    .decision_log``), ``signal_ts`` in ms plus ``signal_utc``, ``allowed`` as true/false."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(DECISIONS_COLUMNS)
        for r in records:
            allowed = "true" if r.allowed else "false"
            writer.writerow(
                [r.pair, r.signal_ts, ms_to_iso(r.signal_ts), allowed, r.rule, r.reason]
            )


def _decision_row(cells: Mapping[str, str]) -> DecisionRecord:
    pair = cells["pair"].strip()
    if not pair:
        raise ValueError("empty pair")
    signal_ts = iso_to_ms(cells["signal_ts"])
    utc = (cells.get("signal_utc") or "").strip()
    if utc and iso_to_ms(utc) != signal_ts:
        raise ValueError(f"signal_utc {utc} is not signal_ts {signal_ts}")
    allowed = _BOOL_TEXT.get(cells["allowed"].strip().lower())
    if allowed is None:
        raise ValueError(f"allowed must be true or false (got {cells['allowed']!r})")
    rule = cells["rule"].strip()
    if rule not in RULE_IDS:
        raise ValueError(f"rule {rule!r} is not a config.RULE_IDS id")
    return DecisionRecord(pair, signal_ts, allowed, rule, cells["reason"])


def read_decisions_log(path: str | Path) -> list[DecisionRecord]:
    """Parse a decisions log written by :func:`write_decisions_log` (or by a bot adapter).

    Columns ``pair, signal_ts, allowed, rule, reason`` are required (``signal_utc`` is
    optional and, when filled, must be the same time); ``signal_ts`` is ms or ISO-8601,
    ``allowed`` true/false, ``rule`` a ``config.RULE_IDS`` id, and a ``(pair, signal_ts)``
    may appear once. ``ValueError`` names the first bad line.
    """
    p = Path(path)
    out: list[DecisionRecord] = []
    seen: dict[Key, int] = {}
    with p.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in _REQUIRED_DECISION_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{p.name}: columns {missing} are missing")
        for cells in reader:
            where = f"{p.name} line {reader.line_num}"
            if None in cells or any(v is None for v in cells.values()):
                raise ValueError(f"{where}: wrong number of cells")
            try:
                row = _decision_row(cells)
            except (ValueError, OverflowError) as exc:
                raise ValueError(f"{where}: {exc}") from None
            key = (row.pair, row.signal_ts)
            if key in seen:
                when = ms_to_iso(row.signal_ts)
                raise ValueError(f"{where}: {row.pair} {when} repeats line {seen[key]}")
            seen[key] = reader.line_num
            out.append(row)
    return out


# ---------------------------------------------------------------------------- D6 testnet replay
BASELINE_TRADE_EVERY_DAYS = 14.0  # D6: the measured baseline rate, about 1 trade per 14 days
FILL_REFUSAL_RULES = frozenset({CAPITAL, R8})  # rules LiveSession logs for refused/skipped fills


def expected_testnet_trades(days: float) -> float:
    """Trades a ``days``-long window is expected to hold at the baseline rate (1 per 14 days)."""
    return days / BASELINE_TRADE_EVERY_DAYS


@dataclass(frozen=True)
class TestnetReplay:
    """What :func:`replay_testnet` reproduced (keys are ``(pair, signal_ts)``)."""

    __test__ = False  # not a pytest test class despite the name

    evaluated: dict[Key, str]  # every replayed signal candle -> the replay's rule
    allowed: frozenset[Key]  # replayed allowed set (see replay_testnet)
    injected_refusals: tuple[Key, ...]  # allowed decisions replayed as the log's fill refusal
    injected_vetoes: tuple[Key, ...]  # ML vetoes taken from the decisions log
    problems: tuple[str, ...]  # journal fills / exits the session could not accept
    trades: tuple[Trade, ...]  # the replay's own journal (closed, then open)


def _veto_filter(vetoes: set[Key]) -> EntryFilter:
    def entry_filter(pair: str, row: FeatureRow, check: SignalCheck) -> tuple[bool, None, str]:
        if (pair, row.ts) in vetoes:
            return False, None, "ML veto injected from the testnet decisions log"
        return True, None, "no ML veto in the testnet decisions log"

    return entry_filter


class _Replay:
    """State of one replay (see :func:`replay_testnet`)."""

    def __init__(
        self,
        session: LiveSession,
        journal: Sequence[Trade],
        decisions: Sequence[DecisionRecord],
    ) -> None:
        self.session = session
        self.log = {(r.pair, r.signal_ts): r for r in decisions}
        self.entries: dict[Key, Trade] = {}
        self.problems: list[str] = []
        for t in journal:
            key = (t.pair, t.signal_ts)
            if key in self.entries:
                self.problems.append(
                    f"journal trades #{self.entries[key].trade_id} and #{t.trade_id} share the "
                    f"signal {t.pair} {ms_to_iso(t.signal_ts)}"
                )
            else:
                self.entries[key] = t
        closed = (t for t in journal if t.exit_ts is not None)
        self.exits = sorted(closed, key=lambda t: (t.exit_ts, t.pair, t.trade_id))
        self.next_exit = 0
        self.evaluated: dict[Key, str] = {}
        self.allowed: set[Key] = set()
        self.refusals: list[Key] = []

    def exits_until(self, d: int) -> None:
        """Inject every journal exit with ``exit_ts <= d`` of a trade the replay holds."""
        while self.next_exit < len(self.exits) and self.exits[self.next_exit].exit_ts <= d:  # type: ignore[operator]
            t = self.exits[self.next_exit]
            self.next_exit += 1
            held = {p.trade_id for p in self.session.open_positions.values()}
            if t.trade_id not in held:
                continue  # never opened by the replay: reported as a set difference
            try:
                self.session.on_exit(
                    t.trade_id,
                    t.exit_ts,
                    t.exit_price,
                    t.exit_reason,
                    fees=t.fees,  # type: ignore[arg-type]
                )
            except ValueError as exc:
                self.problems.append(f"journal trade #{t.trade_id}: its exit was refused ({exc})")

    def resolve(self, pair: str, key: Key, dec: EntryDecision) -> None:
        """Turn an allowed replay decision into the journal's fill, a logged refusal, or none."""
        trade = self.entries.get(key)
        if trade is not None:
            self.inject_fill(pair, key, dec, trade)
            return
        row = self.log.get(key)
        if row is not None and not row.allowed and row.rule in FILL_REFUSAL_RULES:
            self.session.on_fill_skipped(pair, dec, row.reason, rule=row.rule)
            self.evaluated[key] = f"{row.rule} (fill refusal taken from the decisions log)"
            self.refusals.append(key)
            return
        self.session.on_fill_skipped(pair, dec, "no fill in the testnet journal")
        self.evaluated[key] = R7
        self.allowed.add(key)

    def inject_fill(self, pair: str, key: Key, dec: EntryDecision, trade: Trade) -> None:
        try:
            opened = self.session.on_fill(
                pair,
                dec,
                trade.entry_price,
                trade.entry_price,
                qty=trade.qty,
                trade_id=trade.trade_id,
                fill_ts=trade.entry_ts,
            )
        except ValueError as exc:
            self.session.on_fill_skipped(pair, dec, str(exc))
            self.evaluated[key] = "fill not reproducible"
            self.problems.append(
                f"journal trade #{trade.trade_id}: its fill was not reproducible ({exc})"
            )
            return
        if opened is None:
            plan = self.session.last_fill_plan
            self.evaluated[key] = plan.rule if plan is not None else CAPITAL
            return
        self.evaluated[key] = R7
        self.allowed.add(key)


def replay_testnet(
    cfg: StrategyConfig,
    candles: Mapping[str, Sequence[Candle]],
    journal: Sequence[Trade],
    decisions: Sequence[DecisionRecord],
    events: Iterable[NewsEvent] | None,
    starting_equity: float,
    start_ms: int,
    end_ms: int,
    *,
    inject_ml_vetoes: bool = False,
    variant: str = "base",
) -> TestnetReplay:
    """Replay ``gatekeeper.LiveSession`` over the testnet candles (CONTRACT.md v4 D6).

    A fresh session (``starting_equity``, exit offset 0: the journal holds real fill times,
    ``events`` as its news calendar or none) is driven over every candle whose close
    ``d = ts + 4h`` lies in ``[start_ms, end_ms]``, in time order, earlier candles serving as
    indicator history. At each ``d``: first every journal exit with ``exit_ts <= d`` of a
    position the replay holds is injected (``on_exit`` with the journal's price, reason and
    fees); then each pair (sorted) with a candle at ``ts`` is evaluated
    (``on_candle_close``). An allowed decision is resolved, in this order:

    - the journal has an entry for ``(pair, ts)``: its ACTUAL fill is injected (``on_fill``
      with the executed price as market and fill price, its qty, trade id and fill time);
      ``plan_fill`` may still refuse it (R8 on the actual fill, capital), and a qty above the
      R7 size or a fill outside the fill candle is not reproducible (both: not allowed);
    - else the decisions log denies it with a fill-refusal rule (``X_capital`` or
      ``R8_structure_stop``): that refusal is injected, because a refused order's fill price
      is not journaled (so such a refusal is NOT verified);
    - else no position is opened (the bot took no trade) but the key stays ALLOWED, so it
      shows up as a difference against the journal.

    With ``inject_ml_vetoes`` (an ML variant) the decisions log's ``L_ml_filter`` denials are
    the session's entry filter: the model is not re-run, its identity is bound by
    ``model_fingerprint``. Returns the replayed allowed set and what could not be injected.
    """
    log_vetoes = {(r.pair, r.signal_ts) for r in decisions if not r.allowed and r.rule == ML}
    vetoes = log_vetoes if inject_ml_vetoes else set()
    session = LiveSession(
        cfg,
        events or (),
        (),
        starting_equity,
        exit_time_uncertainty_ms=0,
        entry_filter=_veto_filter(vetoes) if inject_ml_vetoes else None,
        variant=variant,
    )
    state = _Replay(session, journal, decisions)
    tf = cfg.timeframe_ms
    series = {p: list(cs) for p, cs in sorted(candles.items())}
    rows = {p: compute_features(cs, cfg) for p, cs in series.items()}
    index = {p: {c.ts: k for k, c in enumerate(cs)} for p, cs in series.items()}
    stamps = sorted({c.ts for cs in series.values() for c in cs if start_ms <= c.ts + tf <= end_ms})
    for ts in stamps:
        state.exits_until(ts + tf)
        for pair, cs in series.items():
            k = index[pair].get(ts)
            if k is None:
                continue
            dec = session.on_candle_close(pair, cs[: k + 1], rows[pair][: k + 1])
            if dec.allowed:
                state.resolve(pair, (pair, ts), dec)
            else:
                state.evaluated[(pair, ts)] = dec.rule
    used = tuple(sorted(k for k in vetoes if state.evaluated.get(k) == ML))
    return TestnetReplay(
        state.evaluated,
        frozenset(state.allowed),
        tuple(state.refusals),
        used,
        tuple(state.problems),
        tuple(session.journal_trades()),
    )


def _fmt(key: Key, note: str | None = None) -> str:
    text = f"{key[0]} {ms_to_iso(key[1])}"
    return f"{text} ({note})" if note else text


def replay_differences(
    replay: TestnetReplay,
    journal: Sequence[Trade],
    decisions: Sequence[DecisionRecord],
    has_events: bool,
) -> list[str]:
    """Every difference between the replayed allowed set R, the journal's entries J and the
    decisions log's allowed rows D (one clause per direction, every key listed)."""
    entries = {(t.pair, t.signal_ts) for t in journal}
    log = {(r.pair, r.signal_ts): r for r in decisions}
    logged = {k for k, r in log.items() if r.allowed}
    allowed = replay.allowed

    def replay_rule(k: Key) -> str:
        return f"replay: {replay.evaluated.get(k, 'not evaluated, no candle in the window')}"

    def log_rule(k: Key) -> str:
        row = log.get(k)
        if row is None:
            return "no row in the decisions log"
        hint = "" if has_events or row.rule != "R5_news_blackout" else ", no news calendar recorded"
        return f"log: {row.rule}{hint}"

    out: list[str] = []
    groups = (
        (allowed - entries, "the replay allows", "but the journal has no entry for them", None),
        (
            entries - allowed,
            "the journal enters",
            "but the replay does not allow them",
            replay_rule,
        ),
        (logged - allowed, "the decisions log allows", "but the replay does not", replay_rule),
        (allowed - logged, "the replay allows", "but the decisions log does not", log_rule),
    )
    for keys, head, tail, note in groups:
        if keys:
            listed = ", ".join(_fmt(k, note(k) if note else None) for k in sorted(keys))
            out.append(f"{head} {listed} {tail}")
    return out


def window_days(start_ms: int, end_ms: int) -> float:
    """Window length in days (``end - start``)."""
    return (end_ms - start_ms) / DAY_MS
