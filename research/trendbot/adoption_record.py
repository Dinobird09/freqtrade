"""The adoption record: stages, rule ids, config fingerprints and the record JSON schema.

This module holds everything about WHAT an adoption record is; :mod:`.adoption_evidence`
recomputes the evidence it points to and :mod:`.adoption` decides promotions. The names are
re-exported by :mod:`.adoption`, so ``from research.trendbot.adoption import ...`` keeps
working for every name defined here.

Record file (JSON, written by :func:`save_record`; every value ``null`` until filled in; keys
added by CONTRACT.md v2 A3 / v3 C3 / v4 D2, D3, D6 are optional when loading, so older records
still load, but the stages require them; times are ISO-8601 UTC strings such as
``"2024-03-12T12:30:00Z"``; sha256 values are 64 hex characters; unknown keys, duplicate keys
and wrong JSON types are refused with ``ValueError``)::

    {"variant": "base", "config_fingerprint": "<sha256 hex>",
     "model_fingerprint": "<sha256 hex>" or null,
     "provenance": "real" | "unverified-csv" | "synthetic:<world>:<seed>",
     "data_files": {"<path>": "<sha256>", ...}, "events_file": {"path", "sha256"} or null,
     "manifest": {"path", "sha256"} or null,            # D2: the data dir's manifest.json
     "ledger_path": "<path>" or null,                   # D3: the holdout ledger (JSON lines)
     "backtest": {"report_path", "completed_utc", "report_sha256"},
     "walk_forward": {"label", "dd_ok", "split_utc", "train_n", "test_n", "test_avg_r",
                      "report_path", "train_journal_path", "train_journal_sha256",
                      "test_journal_path", "test_journal_sha256", "min_train", "min_test",
                      "train_avg_r",
                      "label_params": {"seed", "n_boot", "m", "alpha", "max_dd_pct",
                                       "train_dd_p95_pct", "mtm_max_dd_pct"}},
     "human_review": {"reviewer", "date_utc", "trades_reviewed", "trades_total", "approved",
                      "notes", "review_path", "review_sha256"},
     "testnet": {"exchange", "start_utc", "end_utc", "trades", "rule_violations",
                 "journal_path", "notes", "events_path", "events_sha256",
                 "journal_sha256", "decisions_path", "decisions_sha256",
                 "candles": {"<path>": "<sha256>", ...}, "starting_equity"},
     "live": {"enabled_utc"}}

Relative paths are resolved against the record's directory by the check (``base_dir``).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import string
import typing
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from types import MappingProxyType

from .config import RULE_IDS, ConfigError, PairRisk, StrategyConfig


BACKTEST = "BACKTEST"
WALK_FORWARD = "WALK_FORWARD"
HUMAN_REVIEW = "HUMAN_REVIEW"
TESTNET = "TESTNET"
LIVE = "LIVE"
STAGES: tuple[str, ...] = (BACKTEST, WALK_FORWARD, HUMAN_REVIEW, TESTNET, LIVE)

REQUIRED_WF_LABEL = "ROBUST"
TESTNET_EXCHANGE = "binance-testnet"
TESTNET_MIN_DAYS = 14
PROVENANCE_REAL = "real"  # data.PROVENANCE_REAL: every data file matches the manifest
PROVENANCE_UNVERIFIED = "unverified-csv"  # data.PROVENANCE_UNVERIFIED
SYNTHETIC_PROVENANCE_PREFIX = "synthetic:"  # "synthetic:<world>:<seed>"
ML_VARIANT_MARKER = "+ml"  # a variant id containing this (any case) runs an ML entry filter

ADOPT_RECORD = "ADOPT_record"
ADOPT_FINGERPRINT = "ADOPT_fingerprint"
ADOPT_BACKTEST = "ADOPT_backtest"
ADOPT_WALK_FORWARD = "ADOPT_walk_forward"
ADOPT_HUMAN_REVIEW = "ADOPT_human_review"
ADOPT_TESTNET = "ADOPT_testnet"
ADOPT_LIVE = "ADOPT_live"
ADOPT_PROVENANCE = "ADOPT_provenance"
ADOPT_HOLDOUT = "ADOPT_holdout"
ADOPT_TEST_ONLY = "ADOPT_test_only"

# Rule ids of the adoption gate. The single source of truth is config.RULE_IDS (which holds
# every Decision.rule id of the package); this is the ADOPT_* slice of it, in the same order,
# so the two cannot drift.
ADOPTION_RULE_IDS: Mapping[str, str] = MappingProxyType(
    {
        rule: RULE_IDS[rule]
        for rule in (
            ADOPT_RECORD,
            ADOPT_FINGERPRINT,
            ADOPT_BACKTEST,
            ADOPT_WALK_FORWARD,
            ADOPT_HUMAN_REVIEW,
            ADOPT_TESTNET,
            ADOPT_LIVE,
            ADOPT_PROVENANCE,
            ADOPT_HOLDOUT,
            ADOPT_TEST_ONLY,
        )
    }
)
STAGE_RULES: Mapping[str, str] = MappingProxyType(
    {
        BACKTEST: ADOPT_BACKTEST,
        WALK_FORWARD: ADOPT_WALK_FORWARD,
        HUMAN_REVIEW: ADOPT_HUMAN_REVIEW,
        TESTNET: ADOPT_TESTNET,
        LIVE: ADOPT_LIVE,
    }
)
STAGE_SECTIONS: Mapping[str, str] = MappingProxyType(
    {
        BACKTEST: "backtest",
        WALK_FORWARD: "walk_forward",
        HUMAN_REVIEW: "human_review",
        TESTNET: "testnet",
        LIVE: "live",
    }
)

SHA256_HEX_LEN = 64
_HASH_CHUNK = 1 << 20


def stage_index(stage: str) -> int:
    """Position of ``stage`` in :data:`STAGES`; ``ValueError`` for an unknown stage."""
    if stage not in STAGES:
        raise ValueError(f"unknown adoption stage {stage!r}; expected one of {STAGES}")
    return STAGES.index(stage)


# ---------------------------------------------------------------------------- fingerprint
def _canonical(value: object) -> object:
    """JSON-ready canonical form: numbers as floats (2 and 2.0 are the same config)."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: _canonical(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, Mapping):
        return {str(k): _canonical(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [_canonical(v) for v in value]
    raise TypeError(f"cannot fingerprint a {type(value).__name__} config value")


def canonical_config_json(cfg: StrategyConfig) -> str:
    """The exact text :func:`config_fingerprint` hashes: every field, sorted keys, compact."""
    data = {f.name: _canonical(getattr(cfg, f.name)) for f in fields(cfg)}
    return json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False)


def config_fingerprint(cfg: StrategyConfig) -> str:
    """sha256 hex digest of :func:`canonical_config_json` (``pair_risk`` included)."""
    return hashlib.sha256(canonical_config_json(cfg).encode("utf-8")).hexdigest()


def sha256_file(path: str | Path) -> str:
    """sha256 hex digest of a file's bytes (the value every ``*_sha256`` field records)."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(_HASH_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_sha256_hex(value: object) -> bool:
    """True for a 64-character hexadecimal string (the form of every fingerprint here)."""
    return (
        isinstance(value, str)
        and len(value) == SHA256_HEX_LEN
        and all(c in string.hexdigits for c in value)
    )


def is_ml_variant(variant: str) -> bool:
    """True if the variant id marks an ML layer (contains ``"+ml"``, case-insensitive)."""
    return ML_VARIANT_MARKER in variant.lower()


# ---------------------------------------------------------------------------- config overrides
def _coerce_override(name: str, value: object, default: object) -> object:
    """Type-check one JSON override against the default's type (bools are never numbers)."""
    ok = False
    if isinstance(default, bool):
        ok = isinstance(value, bool)
    elif isinstance(default, int):
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif isinstance(default, float):
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        value = float(value) if ok else value  # type: ignore[arg-type]
    elif isinstance(default, str):
        ok = isinstance(value, str)
    elif isinstance(default, tuple):
        ok = isinstance(value, (list, tuple)) and all(isinstance(v, str) for v in value)
        value = tuple(value) if ok else value  # type: ignore[arg-type]
    if not ok:
        raise ConfigError(
            f"config override {name}: expected {type(default).__name__}, got {value!r}"
        )
    return value


def _pair_risk_override(value: object) -> Mapping[str, PairRisk]:
    if not isinstance(value, Mapping):
        raise ConfigError("config override pair_risk must map base -> {max_risk_pct, ...}")
    out: dict[str, PairRisk] = {}
    for base, spec in value.items():
        if not isinstance(spec, Mapping) or set(spec) != {"max_risk_pct", "stop_buffer_pct"}:
            raise ConfigError(
                f"config override pair_risk.{base} needs exactly max_risk_pct and stop_buffer_pct"
            )
        nums = {k: _coerce_override(f"pair_risk.{base}.{k}", v, 0.0) for k, v in spec.items()}
        out[str(base).upper()] = PairRisk(**nums)  # type: ignore[arg-type]
    return MappingProxyType(out)


def config_from_overrides(overrides: Mapping[str, object]) -> StrategyConfig:
    """Default ``StrategyConfig`` with JSON-style ``overrides`` applied and validated.

    ``pair_risk`` REPLACES the whole mapping (``{"BTC": {"max_risk_pct": 1.0,
    "stop_buffer_pct": 0.25}, ...}``); lists become tuples. Raises ``ConfigError`` on unknown
    fields, wrong types, or any loosening of a mandatory rule.
    """
    defaults = StrategyConfig()
    names = {f.name for f in fields(StrategyConfig)}
    unknown = sorted(set(overrides) - names)
    if unknown:
        raise ConfigError(f"unknown StrategyConfig fields {unknown}")
    changes: dict[str, object] = {}
    for name, value in overrides.items():
        if name == "pair_risk":
            changes[name] = _pair_risk_override(value)
        else:
            changes[name] = _coerce_override(name, value, getattr(defaults, name))
    return defaults.with_changes(**changes)


def load_config(path: str | Path | None) -> StrategyConfig:
    """``StrategyConfig()`` for ``None``, else the overrides JSON object in ``path``."""
    if path is None:
        return StrategyConfig()
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: config overrides must be a JSON object")
    return config_from_overrides(data)


# ---------------------------------------------------------------------------- record
@dataclass(frozen=True, slots=True)
class FileRef:
    """A hash-bound evidence file: ``path`` (relative to the record's directory) + sha256."""

    path: str | None = None
    sha256: str | None = None


@dataclass(frozen=True, slots=True)
class BacktestStage:
    report_path: str | None = None
    completed_utc: str | None = None
    report_sha256: str | None = None  # C3: binds the report file to the record


@dataclass(frozen=True, slots=True)
class WalkForwardStage:
    label: str | None = None  # metrics.LABELS; only "ROBUST" may proceed
    dd_ok: bool | None = None  # metrics.dd_check on the TEST window
    split_utc: str | None = None  # the 70/30 chronological split time
    train_n: int | None = None
    test_n: int | None = None
    test_avg_r: float | None = None  # out-of-sample expectancy (R per trade)
    report_path: str | None = None
    # C3 evidence: the journals the typed numbers above are recomputed from.
    train_journal_path: str | None = None
    train_journal_sha256: str | None = None
    test_journal_path: str | None = None
    test_journal_sha256: str | None = None
    min_train: int | None = None  # metrics.label min_train used for the label
    min_test: int | None = None  # metrics.label min_test used for the label
    train_avg_r: float | None = None  # in-sample expectancy (R per trade)
    # D1 closed schema {seed, n_boot, m, alpha, max_dd_pct, train_dd_p95_pct, mtm_max_dd_pct}
    # (adoption_evidence.LABEL_PARAM_KEYS); see adoption_evidence.recompute_walk_forward.
    label_params: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class HumanReviewStage:
    reviewer: str | None = None
    date_utc: str | None = None
    trades_reviewed: int | None = None
    trades_total: int | None = None
    approved: bool | None = None
    notes: str | None = None
    review_path: str | None = None  # the filled-in trades_review.csv of the review pack
    review_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class TestnetStage:
    __test__ = False  # not a pytest test class despite the name

    exchange: str | None = None
    start_utc: str | None = None
    end_utc: str | None = None
    trades: int | None = None
    rule_violations: int | None = None
    journal_path: str | None = None  # journal.write_journal format, REAL fill times
    notes: str | None = None
    events_path: str | None = None  # news calendar of the testnet run (enables the R5 audit)
    events_sha256: str | None = None
    # D6 evidence (each hash-bound): the journal, the decisions log (every evaluated signal:
    # pair, signal_ts, allowed, rule, reason) and the 4H candle files the bot evaluated.
    journal_sha256: str | None = None
    decisions_path: str | None = None
    decisions_sha256: str | None = None
    candles: dict[str, str] | None = None  # candle file path -> sha256
    starting_equity: float | None = None  # account equity before the first testnet trade


@dataclass(frozen=True, slots=True)
class LiveStage:
    enabled_utc: str | None = None


@dataclass(frozen=True, slots=True)
class AdoptionRecord:
    variant: str
    config_fingerprint: str
    # sha256 of the ML model that was tested (MLFilter.fingerprint()), None if no ML layer.
    model_fingerprint: str | None = None
    provenance: str | None = None  # "real", "unverified-csv" or "synthetic:<world>:<seed>"
    data_files: dict[str, str] | None = None  # candle file path -> sha256 (C3)
    events_file: FileRef | None = None  # news calendar the backtest used, or None
    manifest: FileRef | None = None  # D2: manifest.json of the data directory
    ledger_path: str | None = None  # D3: holdout ledger (append-only, so not hash-bound)
    backtest: BacktestStage = field(default_factory=BacktestStage)
    walk_forward: WalkForwardStage = field(default_factory=WalkForwardStage)
    human_review: HumanReviewStage = field(default_factory=HumanReviewStage)
    testnet: TestnetStage = field(default_factory=TestnetStage)
    live: LiveStage = field(default_factory=LiveStage)


SECTIONS: Mapping[str, type] = MappingProxyType(
    {
        "backtest": BacktestStage,
        "walk_forward": WalkForwardStage,
        "human_review": HumanReviewStage,
        "testnet": TestnetStage,
        "live": LiveStage,
    }
)
_FILE_REFS = ("events_file", "manifest")
_KIND_NAMES = {str: "a string", int: "an integer", float: "a number", bool: "true/false"}


def empty_record(
    variant: str, cfg: StrategyConfig | None = None, model_fingerprint: str | None = None
) -> AdoptionRecord:
    """A record with no stage completed, fingerprinted with ``cfg`` (default config).

    ``model_fingerprint`` is the tested ML model's ``MLFilter.fingerprint()`` (required for a
    ``"+ml"`` variant before any promotion; ``None`` for a variant without an ML layer).
    """
    fp = config_fingerprint(cfg if cfg is not None else StrategyConfig())
    return AdoptionRecord(variant, fp, model_fingerprint)


def _kind(hint: object) -> object:
    args = [a for a in typing.get_args(hint) if a is not type(None)]
    return args[0] if args else hint


def _json_scalar(value: object) -> bool:
    if value is None or isinstance(value, (bool, str)):
        return True
    return isinstance(value, (int, float)) and math.isfinite(value)


def _json_object(value: object, value_kind: object, where: str) -> dict[str, object]:
    """A flat JSON object: string values (``value_kind`` str) or JSON scalars (object)."""
    if not isinstance(value, dict):
        raise ValueError(f"{where} must be a JSON object or null (got {value!r})")
    for key, item in value.items():
        ok = isinstance(item, str) if value_kind is str else _json_scalar(item)
        if not ok:
            need = "a string" if value_kind is str else "a string, finite number, bool or null"
            raise ValueError(f"{where}.{key} must be {need} (got {item!r})")
    return dict(value)


def _typed(value: object, kind: object, where: str) -> object:
    if value is None:
        return None
    if typing.get_origin(kind) is dict:
        return _json_object(value, typing.get_args(kind)[1], where)
    if kind is bool:
        ok = isinstance(value, bool)
    elif kind is int:
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif kind is float:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        ok = ok and math.isfinite(value)  # type: ignore[arg-type]
        value = float(value) if ok else value  # type: ignore[arg-type]
    else:
        ok = isinstance(value, kind)  # type: ignore[arg-type]
    if not ok:
        raise ValueError(f"{where} must be {_KIND_NAMES[kind]} or null (got {value!r})")  # type: ignore[index]
    return value


def _section(cls: type, raw: object, where: str) -> object:
    if raw is None:
        return cls()
    if not isinstance(raw, dict):
        raise ValueError(f"{where} must be a JSON object or null")
    names = [f.name for f in fields(cls)]
    unknown = sorted(set(raw) - set(names))
    if unknown:
        raise ValueError(f"{where}: unknown keys {unknown} (expected {names})")
    hints = typing.get_type_hints(cls)
    return cls(**{n: _typed(raw.get(n), _kind(hints[n]), f"{where}.{n}") for n in names})


def _no_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate key {key!r}")
        out[key] = value
    return out


def record_from_dict(data: object, where: str = "record") -> AdoptionRecord:
    """Strictly validated record: unknown keys and wrong JSON types raise ``ValueError``."""
    if not isinstance(data, dict):
        raise ValueError(f"{where} must be a JSON object")
    head_keys = ("variant", "config_fingerprint", "model_fingerprint", "provenance")
    known = (*head_keys, "data_files", *_FILE_REFS, "ledger_path", *SECTIONS)
    unknown = sorted(set(data) - set(known))
    if unknown:
        raise ValueError(f"{where}: unknown keys {unknown} (expected {list(known)})")
    head: dict[str, object] = {}
    for key in ("variant", "config_fingerprint"):
        if not isinstance(data.get(key), str):
            raise ValueError(f"{where}.{key} must be a string (got {data.get(key)!r})")
        head[key] = data[key]
    # Optional keys (v2 A3, v3 C3, v4 D2/D3): records written before them lack the key.
    for key in ("model_fingerprint", "provenance", "ledger_path"):
        head[key] = _typed(data.get(key), str, f"{where}.{key}")
    head["data_files"] = _typed(data.get("data_files"), dict[str, str], f"{where}.data_files")
    for key in _FILE_REFS:
        raw = data.get(key)
        head[key] = None if raw is None else _section(FileRef, raw, f"{where}.{key}")
    stages = {k: _section(cls, data.get(k), f"{where}.{k}") for k, cls in SECTIONS.items()}
    return AdoptionRecord(**head, **stages)  # type: ignore[arg-type]


def load_record(path: str | Path) -> AdoptionRecord:
    """Load a record written by :func:`save_record` (or by hand); raises ``ValueError``."""
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"), object_pairs_hook=_no_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{p}: not valid JSON ({exc})")
    except ValueError as exc:
        raise ValueError(f"{p}: {exc}")
    return record_from_dict(data, where=str(p))


def save_record(record: AdoptionRecord, path: str | Path) -> None:
    """Write ``record`` as indented JSON (stage order kept); creates parent directories."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(record), indent=2, allow_nan=False) + "\n", encoding="utf-8")
