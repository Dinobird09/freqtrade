"""Adoption path enforcement: no variant reaches real money by skipping a step.

The ONLY way from research to live trading is, strictly in this order::

    BACKTEST -> WALK_FORWARD -> HUMAN_REVIEW -> TESTNET (>= 14 days, Binance testnet) -> LIVE

A good backtest number never shortcuts the path. :func:`check_promotion` returns every
blocking :class:`~.models.Decision` for promoting a record to a target stage (empty list =
allowed): every EARLIER stage must be complete and consistent with its evidence files, and
the config being promoted must be exactly the config that was tested (sha256
:func:`config_fingerprint`). A live bot must call :func:`require_stage` with ``"LIVE"`` at
startup and refuse to trade if it raises.

Evidence binding (CONTRACT.md v3 C3). Relative paths in the record are resolved against
``base_dir`` (the CLI passes the record file's directory; ``None`` means the current working
directory, so the files are ALWAYS checked); absolute paths are used as they are. A
"hash-bound" file must be recorded with its ``*_sha256`` field, exist, and have exactly that
sha256 (:func:`sha256_file`; ``python -m research.trendbot.adoption hash FILE`` prints it).

What IS verified, by target stage (each item is a separate blocking reason, rule id in
brackets; a stage with nothing recorded yields one "no recorded result" reason instead):

- Always: ``variant`` named [ADOPT_record]; ``config_fingerprint`` equals
  ``config_fingerprint(cfg)`` [ADOPT_fingerprint]; ML model (CONTRACT.md v2 A3,
  [ADOPT_fingerprint]): a recorded ``model_fingerprint`` (``MLFilter.fingerprint()``) must be
  a sha256 hex digest equal to the one the caller supplies, a variant whose id contains
  ``"+ml"`` must have one, and none may be supplied for a record without one.
- Target WALK_FORWARD or later (the BACKTEST stage is checked):
  [ADOPT_backtest] ``report_path`` hash-bound by ``report_sha256``; ``completed_utc`` a
  valid time not after the check time. [ADOPT_provenance] every ``data_files`` entry
  (``{path: sha256}``) and the ``events_file`` (``{path, sha256}``, if recorded) is
  hash-bound.
- Target HUMAN_REVIEW or later (the WALK_FORWARD stage is checked): [ADOPT_provenance]
  ``provenance`` exactly ``"real"`` (``"synthetic:<world>:<seed>"`` and a missing value are
  blocked) and at least one data file hash recorded. [ADOPT_walk_forward] ``label`` exactly
  ``"ROBUST"``; ``dd_ok`` exactly true; ``split_utc`` valid; ``train_n``/``test_n``
  positive and, for ROBUST, both >= 30 whatever ``min_train``/``min_test`` say;
  ``test_avg_r`` > 0 (expectancy, never win rate); ``train_avg_r`` recorded;
  ``min_train``/``min_test`` positive integers; ``label_params`` a JSON object;
  ``report_path`` exists; both journals hash-bound
  (``train_journal_path``/``_sha256``, ``test_journal_path``/``_sha256``) and re-parsed with
  ``journal.read_journal``: closed trades only, unique trade ids, every trade of the
  record's variant, TRAIN signals before ``split_utc`` and TEST signals at or after it.
  Then ``metrics.summarize`` (starting equity ``cfg.starting_capital``), ``metrics.label``
  (recorded ``min_train``/``min_test``) and ``metrics.dd_check`` are recomputed from the
  journals (:func:`recompute_walk_forward`; each ``label_params`` entry is passed to every
  one of those three functions that declares a parameter of that name, an entry none
  declares blocks, and ``max_dd_pct`` defaults to and may not exceed 20). The typed
  ``label``, ``train_n``, ``test_n``, ``train_avg_r``, ``test_avg_r`` (absolute 1e-9) and
  ``dd_ok`` must equal the recomputed values.
- Target TESTNET or later (the HUMAN_REVIEW stage is checked): [ADOPT_human_review] a named
  ``reviewer``; ``date_utc`` valid and not before the backtest completed;
  ``trades_reviewed == trades_total == train_n + test_n``; ``approved`` exactly true;
  ``review_path`` (the pack's ``trades_review.csv``) hash-bound by ``review_sha256`` and
  parsed with ``review_sheet.load_review``; its row count equals ``train_n + test_n``; its
  ``(window, trade_id)`` key set equals the TRAIN journal's ``TRAIN`` keys plus the TEST
  journal's ``TEST`` keys, and each row's pair and signal time equal its journal trade's;
  every ``reviewer_ok`` is ``Y`` or ``N`` (a blank blocks). Any ``N`` blocks, and the
  documented policy is: the variant is revised and restarts the adoption path at BACKTEST
  under a new record (a changed config has a new fingerprint), so a record with a rejected
  trade never passes HUMAN_REVIEW.
- Target LIVE (the TESTNET stage is checked): [ADOPT_testnet] ``exchange ==
  "binance-testnet"``; ``end_utc - start_utc >= 14 days``; ``end_utc`` not after the check
  time; ``start_utc`` not before the review sign-off; ``trades >= 1``; ``rule_violations``
  exactly 0; ``journal_path`` exists and is re-parsed: its trade count equals
  ``testnet.trades``, every entry lies inside the window, every trade is of the record's
  variant, no trade carries a per-trade mandatory-rule flag of ``review_sheet.auto_flags``
  (:data:`RULE_VIOLATION_FLAGS`), and ``invariants.cross_trade_violations`` over the journal
  with exit offset 0 (real fill times; 7-day halt on ``cfg.starting_capital``) finds
  nothing: R6 (no pyramiding, BNB never stacks, no concurrent full-size cluster risk, the
  shared budget respected) and R9 (no entry inside a 3-SL bench, every bench >= 24h, no
  entry during a 7-day loss halt). R5 is checked the same way only if ``testnet.events_path``
  is recorded (hash-bound by ``events_sha256``, read with ``news.load_events``); otherwise a
  blocking reason and :func:`unverified_notes` state that R5 is not journal-verifiable.
  [ADOPT_live] the config is not an explicit test-only variant (R4 regime filter off never
  trades live); ``live.enabled_utc``, if recorded, is not before the testnet run ended.

What is NOT verified (:func:`unverified_notes` lists the items relevant to a target; the
CLI prints them under PASS and BLOCKED):

- That the journals were produced by running the backtester on the hash-bound data files:
  the hashes bind the files to the record, they do not prove how the files were made
  (re-run ``run_research`` / ``python -m research.trendbot.invariants`` on the data to check).
- The mark-to-market part of the drawdown rule (CONTRACT.md v3 C5) needs the candles:
  ``dd_ok`` is recomputed only with ``metrics.dd_check`` on the closed-trade TEST summary.
- ``walk_forward.report_path`` and ``testnet.journal_path`` carry no hash: the report is
  only required to exist, and the testnet journal is re-audited on every check instead.
- That the reviewer actually inspected each trade: the pack only proves every row got an
  explicit ``Y``.
- Per-candle rules of testnet trades (R1-R4 gates, R8 structure stops, fill prices) and,
  without ``testnet.events_path``, R5: they need the candles or the news calendar.
- That the testnet account really was a testnet (only the typed ``exchange`` is checked),
  and hand-typed times beyond their format, order and not lying in the future.

A live bot must also round quantities DOWN and take-profit prices UP to the exchange tick
so R7 and the 2:1 minimum hold after rounding (not checkable from a record).

Record file (JSON, written by :func:`save_record`; every value ``null`` until filled in;
keys added by CONTRACT.md v2 A3 / v3 C3 are optional when loading, so older records still
load, but the stages above require them; times are ISO-8601 UTC strings such as
``"2024-03-12T12:30:00Z"``; sha256 values are 64 hex characters)::

    {"variant": "base", "config_fingerprint": "<sha256 hex>",
     "model_fingerprint": "<sha256 hex>" or null,
     "provenance": "real" or "synthetic:<world>:<seed>",
     "data_files": {"<path>": "<sha256>", ...}, "events_file": {"path", "sha256"} or null,
     "backtest": {"report_path", "completed_utc", "report_sha256"},
     "walk_forward": {"label", "dd_ok", "split_utc", "train_n", "test_n", "test_avg_r",
                      "report_path", "train_journal_path", "train_journal_sha256",
                      "test_journal_path", "test_journal_sha256", "min_train", "min_test",
                      "train_avg_r", "label_params": {...}},
     "human_review": {"reviewer", "date_utc", "trades_reviewed", "trades_total", "approved",
                      "notes", "review_path", "review_sha256"},
     "testnet": {"exchange", "start_utc", "end_utc", "trades", "rule_violations",
                 "journal_path", "notes", "events_path", "events_sha256"},
     "live": {"enabled_utc"}}

CLI::

    python -m research.trendbot.adoption init --variant base --out rec.json [--config c.json]
        [--model-fingerprint HEX]
    python -m research.trendbot.adoption check --record rec.json --stage LIVE [--now ISO]
        [--config c.json] [--model-fingerprint HEX]
    python -m research.trendbot.adoption fingerprint [--config c.json]
    python -m research.trendbot.adoption hash FILE [FILE ...]

``--config`` is a JSON object of ``StrategyConfig`` field overrides (validated: a loosening
of a mandatory rule is refused); without it the default config is used. ``check`` prints
``PASS`` or every blocking reason, then what the check could not verify, and exits 0 / 1.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import inspect
import json
import math
import string
import sys
import time
import typing
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from types import MappingProxyType

from . import metrics
from .config import RULE_IDS, ConfigError, PairRisk, StrategyConfig
from .invariants import cross_trade_violations
from .journal import iso_to_ms, ms_to_iso, read_journal
from .metrics import LABELS, Summary
from .models import DAY_MS, Decision, NewsEvent, Trade
from .news import load_events
from .review_sheet import (
    FLAG_BAD_LEVELS,
    FLAG_LOOKAHEAD,
    FLAG_NON_FINITE,
    FLAG_PAIR,
    FLAG_RISK_CAP,
    FLAG_RR_BELOW,
    FLAG_SIZE,
    REVIEW_OK_NO,
    REVIEW_OK_YES,
    WINDOW_TEST,
    WINDOW_TRAIN,
    ReviewRow,
    auto_flags,
    flag_code,
    load_review,
)


BACKTEST = "BACKTEST"
WALK_FORWARD = "WALK_FORWARD"
HUMAN_REVIEW = "HUMAN_REVIEW"
TESTNET = "TESTNET"
LIVE = "LIVE"
STAGES: tuple[str, ...] = (BACKTEST, WALK_FORWARD, HUMAN_REVIEW, TESTNET, LIVE)

REQUIRED_WF_LABEL = "ROBUST"
TESTNET_EXCHANGE = "binance-testnet"
TESTNET_MIN_DAYS = 14
PROVENANCE_REAL = "real"
SYNTHETIC_PROVENANCE_PREFIX = "synthetic:"  # "synthetic:<world>:<seed>"
ROBUST_MIN_N = 30  # C3: ROBUST needs >= 30 trades in EACH window, whatever min_* say
DD_CAP_PCT = 20.0  # C5: the TEST drawdown limit is never above 20 %
AVG_R_TOL = 1e-9  # absolute tolerance of typed vs recomputed avg R

ADOPT_RECORD = "ADOPT_record"
ADOPT_FINGERPRINT = "ADOPT_fingerprint"
ADOPT_BACKTEST = "ADOPT_backtest"
ADOPT_WALK_FORWARD = "ADOPT_walk_forward"
ADOPT_HUMAN_REVIEW = "ADOPT_human_review"
ADOPT_TESTNET = "ADOPT_testnet"
ADOPT_LIVE = "ADOPT_live"
ADOPT_PROVENANCE = "ADOPT_provenance"

# Rule ids of this module. The single source of truth is config.RULE_IDS (which holds every
# Decision.rule id of the package); this is the ADOPT_* slice of it, so the two cannot drift.
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

REVIEW_REJECTION_POLICY = (
    "the variant must be revised and restart the adoption path at BACKTEST under a new "
    "record (a changed config has a new fingerprint), so this record can never pass "
    "HUMAN_REVIEW"
)
R5_NOT_VERIFIABLE = (
    "R5 (news blackout) was not checked because testnet.events_path is not recorded, and R5 "
    "is not journal-verifiable without the news calendar the bot traded under"
)

_LABEL_WHY = {
    "TRAIN-ONLY": "the edge did not hold out of sample, a sign of curve-fitting",
    "UNTESTED": "too few trades, or a test expectancy not distinguishable from zero",
    "NO-EDGE": "the train window showed no positive expectancy to validate",
}
_MAX_IDS_SHOWN = 5
_MAX_VIOLATIONS_SHOWN = 5
_FP_SHOWN = 16
ML_VARIANT_MARKER = "+ml"  # a variant id containing this (any case) runs an ML entry filter
_SHA256_HEX_LEN = 64
_HASH_CHUNK = 1 << 20


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
        and len(value) == _SHA256_HEX_LEN
        and all(c in string.hexdigits for c in value)
    )


def is_ml_variant(variant: str) -> bool:
    """True if the variant id marks an ML layer (contains ``"+ml"``, case-insensitive)."""
    return ML_VARIANT_MARKER in variant.lower()


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
    # Extra keyword arguments of metrics.summarize / label / dd_check (C4 label parameters,
    # C5 max_dd_pct); {} means their defaults. See recompute_walk_forward.
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
    journal_path: str | None = None
    notes: str | None = None
    events_path: str | None = None  # news calendar of the testnet run (enables the R5 audit)
    events_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class LiveStage:
    enabled_utc: str | None = None


@dataclass(frozen=True, slots=True)
class AdoptionRecord:
    variant: str
    config_fingerprint: str
    # sha256 of the ML model that was tested (MLFilter.fingerprint()), None if no ML layer.
    model_fingerprint: str | None = None
    provenance: str | None = None  # "real" or "synthetic:<world>:<seed>" (C3)
    data_files: dict[str, str] | None = None  # candle file path -> sha256 (C3)
    events_file: FileRef | None = None  # news calendar the backtest used, or None
    backtest: BacktestStage = field(default_factory=BacktestStage)
    walk_forward: WalkForwardStage = field(default_factory=WalkForwardStage)
    human_review: HumanReviewStage = field(default_factory=HumanReviewStage)
    testnet: TestnetStage = field(default_factory=TestnetStage)
    live: LiveStage = field(default_factory=LiveStage)


_SECTIONS: Mapping[str, type] = MappingProxyType(
    {
        "backtest": BacktestStage,
        "walk_forward": WalkForwardStage,
        "human_review": HumanReviewStage,
        "testnet": TestnetStage,
        "live": LiveStage,
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
_KIND_NAMES = {str: "a string", int: "an integer", float: "a number", bool: "true/false"}
_HEAD_KEYS = ("variant", "config_fingerprint", "model_fingerprint", "provenance")


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
    known = (*_HEAD_KEYS, "data_files", "events_file", *_SECTIONS)
    unknown = sorted(set(data) - set(known))
    if unknown:
        raise ValueError(f"{where}: unknown keys {unknown} (expected {list(known)})")
    head: dict[str, object] = {}
    for key in ("variant", "config_fingerprint"):
        if not isinstance(data.get(key), str):
            raise ValueError(f"{where}.{key} must be a string (got {data.get(key)!r})")
        head[key] = data[key]
    # Optional keys (v2 A3, v3 C3): records written before them simply lack the key.
    for key in ("model_fingerprint", "provenance"):
        head[key] = _typed(data.get(key), str, f"{where}.{key}")
    head["data_files"] = _typed(data.get("data_files"), dict[str, str], f"{where}.data_files")
    raw_events = data.get("events_file")
    events = None if raw_events is None else _section(FileRef, raw_events, f"{where}.events_file")
    stages = {k: _section(cls, data.get(k), f"{where}.{k}") for k, cls in _SECTIONS.items()}
    return AdoptionRecord(**head, events_file=events, **stages)  # type: ignore[arg-type]


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


# ---------------------------------------------------------------------------- recompute
@dataclass(frozen=True)
class WalkForwardVerdict:
    """The walk-forward numbers recomputed from the TRAIN and TEST journals."""

    label: str
    label_reason: str
    train: Summary
    test: Summary
    dd_ok: bool
    dd_reason: str


_VERDICT_FUNCTIONS = ("summarize", "label", "dd_check")
# Arguments that come from the journals, the typed fields and the config, never label_params.
_FIXED_ARGUMENTS = frozenset(
    {"trades", "starting_equity", "train", "test", "min_train", "min_test"}
)
_KEYWORD_KINDS = (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)


def _keyword_names(fn: Callable[..., object]) -> frozenset[str]:
    params = inspect.signature(fn).parameters.values()
    return frozenset(p.name for p in params if p.kind in _KEYWORD_KINDS)


def route_label_params(label_params: Mapping[str, object]) -> dict[str, dict[str, object]]:
    """Split ``walk_forward.label_params`` into keyword arguments per metrics function.

    Each entry goes to every one of ``metrics.summarize``, ``metrics.label`` and
    ``metrics.dd_check`` whose signature declares a parameter of that name. Raises
    ``ValueError`` for an entry no function declares, for the fixed arguments (journals,
    ``starting_equity``, ``min_train``/``min_test``), and for a ``max_dd_pct`` outside
    ``(0, 20]`` (it defaults to 20, the C5 cap).
    """
    routed: dict[str, dict[str, object]] = {name: {} for name in _VERDICT_FUNCTIONS}
    accepted = {name: _keyword_names(getattr(metrics, name)) for name in _VERDICT_FUNCTIONS}
    for key, value in label_params.items():
        if key in _FIXED_ARGUMENTS:
            raise ValueError(
                f"walk_forward.label_params may not set {key!r}, which comes from the journals, "
                "the typed fields or the config"
            )
        targets = [name for name in _VERDICT_FUNCTIONS if key in accepted[name]]
        if not targets:
            raise ValueError(
                f"walk_forward.label_params key {key!r} is not a parameter of "
                "metrics.summarize, metrics.label or metrics.dd_check"
            )
        for name in targets:
            routed[name][key] = value
    if "max_dd_pct" in accepted["dd_check"]:
        limit = routed["dd_check"].setdefault("max_dd_pct", DD_CAP_PCT)
        number = isinstance(limit, (int, float)) and not isinstance(limit, bool)
        if not (number and 0 < limit <= DD_CAP_PCT):  # type: ignore[operator]
            raise ValueError(
                f"walk_forward.label_params max_dd_pct {limit!r} must be in (0, {DD_CAP_PCT:g}] "
                f"because the TEST drawdown limit is never above {DD_CAP_PCT:g}% (CONTRACT v3 C5)"
            )
    return routed


def recompute_walk_forward(
    train_trades: Sequence[Trade],
    test_trades: Sequence[Trade],
    cfg: StrategyConfig,
    min_train: int,
    min_test: int,
    label_params: Mapping[str, object],
) -> WalkForwardVerdict:
    """Recompute the walk-forward verdict from the journals, exactly as the check does.

    ``metrics.summarize(trades, cfg.starting_capital, ...)`` per window, then
    ``metrics.label(train, test, min_train=, min_test=, ...)`` and
    ``metrics.dd_check(test, ...)``, the extra keyword arguments routed from
    ``label_params`` by :func:`route_label_params`. A record writer can call this to fill
    the typed fields. Raises ``ValueError`` / ``TypeError`` if the parameters do not apply.
    """
    kw = route_label_params(label_params)
    train = metrics.summarize(train_trades, cfg.starting_capital, **kw["summarize"])  # type: ignore[arg-type]
    test = metrics.summarize(test_trades, cfg.starting_capital, **kw["summarize"])  # type: ignore[arg-type]
    verdict, why = metrics.label(
        train,
        test,
        min_train=min_train,
        min_test=min_test,
        **kw["label"],  # type: ignore[arg-type]
    )
    dd_ok, dd_why = metrics.dd_check(test, **kw["dd_check"])  # type: ignore[arg-type]
    return WalkForwardVerdict(verdict, why, train, test, bool(dd_ok), dd_why)


# ---------------------------------------------------------------------------- check helpers
@dataclass(frozen=True, slots=True)
class _Ctx:
    record: AdoptionRecord
    cfg: StrategyConfig
    now: int
    base_dir: Path
    model_fingerprint: str | None = None  # fingerprint of the ML model being promoted
    cache: dict[str, object] = field(default_factory=dict)  # parsed evidence, per check


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _blank(value: object) -> bool:
    return not (isinstance(value, str) and value.strip())


def _is_empty(section: object) -> bool:
    return all(getattr(section, f.name) is None for f in fields(section))  # type: ignore[arg-type]


def _quiet_ts(value: str | None) -> int | None:
    """Parsed time or ``None`` (the stage that owns the field reports parse errors)."""
    if _blank(value):
        return None
    try:
        return iso_to_ms(value)  # type: ignore[arg-type]
    except (ValueError, OverflowError):
        return None


def _time(value: str | None, name: str, now: int) -> tuple[int | None, str | None]:
    """``(ms, problem sentence)``: missing, unparseable, or later than the check time."""
    if _blank(value):
        return None, f"{name} is missing, so this stage is not complete."
    ts = _quiet_ts(value)
    if ts is None:
        return None, f"{name} {value!r} is not an ISO-8601 UTC time."
    if ts > now:
        return ts, (
            f"{name} {ms_to_iso(ts)} is later than the check time {ms_to_iso(now)}, so this "
            "stage cannot be complete yet."
        )
    return ts, None


def _resolve(ctx: _Ctx, value: str) -> Path:
    p = Path(value.strip())
    return p if p.is_absolute() else ctx.base_dir / p


def _path(ctx: _Ctx, value: str | None, name: str) -> tuple[Path | None, str | None]:
    """``(resolved existing path or None, problem sentence)``; existence only, no hash."""
    if _blank(value):
        return None, f"{name} is missing, so there is no evidence file to audit."
    p = _resolve(ctx, value)  # type: ignore[arg-type]
    if not p.is_file():
        return None, f"{name} {value!r} does not exist (looked for {p})."
    return p, None


def _hash_bound(
    ctx: _Ctx, path_value: str | None, sha_value: str | None, path_name: str, sha_name: str
) -> tuple[Path | None, list[str]]:
    """``(verified path or None, problems)``: recorded, hash recorded, exists, hash matches."""
    if _blank(path_value):
        return None, [f"{path_name} is missing, so there is no evidence file to audit."]
    if _blank(sha_value):
        return None, [
            f"{sha_name} is missing, so {path_name} {path_value!r} is not bound to this record "
            "by its sha256."
        ]
    sha = sha_value.strip()  # type: ignore[union-attr]
    if not is_sha256_hex(sha):
        return None, [
            f"{sha_name} {sha[:_SHA256_HEX_LEN]!r} is not a sha256 hex digest "
            f"({_SHA256_HEX_LEN} hex characters)."
        ]
    p = _resolve(ctx, path_value)  # type: ignore[arg-type]
    if not p.is_file():
        return None, [f"{path_name} {path_value!r} does not exist (looked for {p})."]
    try:
        actual = sha256_file(p)
    except OSError as exc:
        return None, [f"{path_name} {path_value!r} could not be read ({exc})."]
    if actual != sha.lower():
        return None, [
            f"{path_name} {path_value!r} has sha256 {actual[:_FP_SHOWN]} but {sha_name} is "
            f"{sha[:_FP_SHOWN].lower()} (first {_FP_SHOWN} hex digits shown), so the file is "
            "not the evidence that was recorded."
        ]
    return p, []


def _pos_int(value: object) -> bool:
    return _is_int(value) and value > 0  # type: ignore[operator]


def _positive_count(value: object, name: str) -> str | None:
    if _pos_int(value):
        return None
    return f"{name} must be a positive trade count (got {value!r})."


def _shown(items: Sequence[str]) -> str:
    shown = ", ".join(items[:_MAX_IDS_SHOWN])
    extra = len(items) - _MAX_IDS_SHOWN
    return f"{shown} (+{extra} more)" if extra > 0 else shown


def _ids(ids: Sequence[int]) -> str:
    return _shown([f"#{i}" for i in ids])


def _keys(keys: Sequence[tuple[str, int]]) -> str:
    return _shown([f"{window} #{i}" for window, i in keys])


def _problems(*items: str | None) -> list[str]:
    return [p for p in items if p is not None]


def _num_close(typed: object, actual: float) -> bool:
    if isinstance(typed, bool) or not isinstance(typed, (int, float)):
        return False
    return abs(float(typed) - actual) <= AVG_R_TOL


# ---------------------------------------------------------------------------- identity
def _record_problems(ctx: _Ctx) -> list[Decision]:
    rec = ctx.record
    out: list[Decision] = []
    if _blank(rec.variant):
        out.append(
            Decision(False, ADOPT_RECORD, "variant is empty, so the record tracks no variant.")
        )
    expected = config_fingerprint(ctx.cfg)
    if _blank(rec.config_fingerprint):
        reason = "config_fingerprint is missing, so the tested config cannot be identified."
        out.append(Decision(False, ADOPT_FINGERPRINT, reason))
    elif rec.config_fingerprint != expected:
        reason = (
            f"config_fingerprint {rec.config_fingerprint[:_FP_SHOWN]} was tested but the config "
            f"being promoted has fingerprint {expected[:_FP_SHOWN]} (first {_FP_SHOWN} hex "
            "digits shown), so the promoted variant is not the one that was tested."
        )
        out.append(Decision(False, ADOPT_FINGERPRINT, reason))
    model_problem = _model_fingerprint_problem(rec, ctx.model_fingerprint)
    if model_problem is not None:
        out.append(Decision(False, ADOPT_FINGERPRINT, model_problem))
    return out


def _model_fingerprint_problem(record: AdoptionRecord, supplied: str | None) -> str | None:
    """CONTRACT.md v2 A3: the ML model promoted must be exactly the ML model tested."""
    recorded = record.model_fingerprint
    if recorded is None:
        if is_ml_variant(record.variant):
            return (
                f"variant {record.variant!r} runs an ML filter ({ML_VARIANT_MARKER!r} in its id) "
                "but the record has no model_fingerprint, so the tested ML model cannot be "
                "identified."
            )
        if supplied is not None:
            return (
                f"a model fingerprint {supplied[:_FP_SHOWN]!r} was supplied but the record has "
                "no model_fingerprint, so that ML model was never tested under this record."
            )
        return None
    if not is_sha256_hex(recorded):
        return (
            f"model_fingerprint {recorded[:_SHA256_HEX_LEN]!r} is not a sha256 hex digest "
            f"({_SHA256_HEX_LEN} hex characters); use null for a variant without an ML model."
        )
    if supplied is None:
        return (
            f"the record was tested with ML model {recorded[:_FP_SHOWN]} but no current model "
            "fingerprint was supplied, so the model being promoted cannot be verified."
        )
    if supplied != recorded:
        return (
            f"model_fingerprint {recorded[:_FP_SHOWN]} was tested but the ML model being "
            f"promoted has fingerprint {supplied[:_FP_SHOWN]!r} (first {_FP_SHOWN} characters "
            "shown), so the promoted model is not the one that was tested."
        )
    return None


# ---------------------------------------------------------------------------- provenance
def _provenance_value_problem(provenance: str | None) -> str | None:
    if provenance == PROVENANCE_REAL:
        return None
    if _blank(provenance):
        return (
            "provenance is missing, so nothing shows the walk-forward ran on real market data "
            f"(record {PROVENANCE_REAL!r} with the data file hashes)."
        )
    if provenance.strip().lower().startswith(SYNTHETIC_PROVENANCE_PREFIX):  # type: ignore[union-attr]
        return (
            f"provenance is {provenance!r}: a synthetic world only verifies the harness and is "
            f"never evidence about real markets, so it may not go past {WALK_FORWARD}."
        )
    return (
        f"provenance is {provenance!r}, but only {PROVENANCE_REAL!r} (hash-bound exchange data) "
        f"may go past {WALK_FORWARD}."
    )


def _provenance_problems(ctx: _Ctx, require_real: bool) -> list[str]:
    """C3 data binding: recorded data/event files hash-bound; later stages need real data."""
    rec = ctx.record
    out: list[str] = []
    if require_real:
        out += _problems(_provenance_value_problem(rec.provenance))
        if not rec.data_files:
            out.append(
                "data_files is missing or empty, so the market data behind the backtest is not "
                "bound to this record by sha256."
            )
    for path, sha in sorted((rec.data_files or {}).items()):
        out += _hash_bound(ctx, path, sha, "data_files entry", f"data_files[{path!r}]")[1]
    if rec.events_file is not None:
        ef = rec.events_file
        out += _hash_bound(ctx, ef.path, ef.sha256, "events_file.path", "events_file.sha256")[1]
    return out


# ---------------------------------------------------------------------------- backtest
def _backtest_problems(ctx: _Ctx) -> list[str]:
    bt = ctx.record.backtest
    _p, path_problems = _hash_bound(
        ctx, bt.report_path, bt.report_sha256, "backtest.report_path", "backtest.report_sha256"
    )
    _ts, time_problem = _time(bt.completed_utc, "backtest.completed_utc", ctx.now)
    return path_problems + _problems(time_problem)


# ---------------------------------------------------------------------------- walk-forward
def _label_problem(label: str | None) -> str | None:
    if label == REQUIRED_WF_LABEL:
        return None
    if _blank(label):
        return "walk_forward.label is missing, so the out-of-sample result is unknown."
    why = _LABEL_WHY.get(str(label), f"not one of the walk-forward labels {', '.join(LABELS)}")
    return f"walk_forward.label is {label!r} ({why}), and only {REQUIRED_WF_LABEL} may proceed."


def _dd_problem(dd_ok: bool | None) -> str | None:
    if dd_ok is True:
        return None
    if dd_ok is False:
        return "walk_forward.dd_ok is false: the test-window drawdown exceeded the limit."
    return "walk_forward.dd_ok is missing, so the test drawdown was never checked."


def _avg_r_problem(avg_r: object) -> str | None:
    if not isinstance(avg_r, bool) and isinstance(avg_r, (int, float)) and avg_r > 0:
        return None
    return (
        f"walk_forward.test_avg_r must be a positive out-of-sample expectancy in R (got {avg_r!r})."
    )


def _wf_param_problems(wf: WalkForwardStage) -> list[str]:
    out = [
        f"walk_forward.{name} must be the positive minimum trade count passed to metrics.label "
        f"(got {getattr(wf, name)!r})."
        for name in ("min_train", "min_test")
        if not _pos_int(getattr(wf, name))
    ]
    if wf.train_avg_r is None:
        out.append(
            "walk_forward.train_avg_r is missing, so the TRAIN expectancy cannot be checked "
            "against its journal."
        )
    if wf.label_params is None:
        out.append(
            "walk_forward.label_params is missing: record the exact extra keyword arguments "
            "given to metrics.summarize, metrics.label and metrics.dd_check (an empty object "
            "for their defaults)."
        )
    return out


def _robust_sample_problem(wf: WalkForwardStage) -> str | None:
    if wf.label != REQUIRED_WF_LABEL:
        return None
    counts = (wf.train_n, wf.test_n)
    if all(_is_int(n) and n >= ROBUST_MIN_N for n in counts):  # type: ignore[operator]
        return None
    return (
        f"walk_forward.label is {REQUIRED_WF_LABEL} with train_n {wf.train_n!r} and test_n "
        f"{wf.test_n!r}, but {REQUIRED_WF_LABEL} needs at least {ROBUST_MIN_N} trades in each "
        "window (CONTRACT v3 C3), whatever min_train and min_test say."
    )


def _load_wf_journals(ctx: _Ctx) -> tuple[tuple[list[Trade], list[Trade]] | None, list[str]]:
    wf = ctx.record.walk_forward
    problems: list[str] = []
    loaded: list[list[Trade]] = []
    specs = (
        ("train", wf.train_journal_path, wf.train_journal_sha256),
        ("test", wf.test_journal_path, wf.test_journal_sha256),
    )
    for window, path_value, sha_value in specs:
        name = f"walk_forward.{window}_journal"
        path, found = _hash_bound(ctx, path_value, sha_value, f"{name}_path", f"{name}_sha256")
        problems += found
        if path is None:
            continue
        try:
            loaded.append(read_journal(path))
        except (OSError, ValueError) as exc:
            problems.append(f"{name}_path could not be read as a trade journal ({exc}).")
    if problems:
        return None, problems
    return (loaded[0], loaded[1]), []


def _wf_journals(ctx: _Ctx) -> tuple[tuple[list[Trade], list[Trade]] | None, list[str]]:
    """The hash-verified (TRAIN, TEST) journals, parsed once per check, or None + problems."""
    if "wf_journals" not in ctx.cache:
        ctx.cache["wf_journals"] = _load_wf_journals(ctx)
    return ctx.cache["wf_journals"]  # type: ignore[return-value]


def _window_problems(window: str, trades: Sequence[Trade], ctx: _Ctx) -> list[str]:
    rec = ctx.record
    split = _quiet_ts(rec.walk_forward.split_utc)
    out: list[str] = []
    unclosed = [t.trade_id for t in trades if t.exit_ts is None or t.r_multiple is None]
    if unclosed:
        out.append(
            f"the {window} journal has trades {_ids(unclosed)} without an exit or R, but a "
            "walk-forward journal holds closed trades only."
        )
    dup = sorted(i for i, n in Counter(t.trade_id for t in trades).items() if n > 1)
    if dup:
        out.append(
            f"the {window} journal repeats trade ids {_ids(dup)}, so its rows cannot be matched "
            "to the review pack."
        )
    foreign = [t.trade_id for t in trades if t.variant != rec.variant]
    if foreign:
        out.append(
            f"{window} journal trades {_ids(foreign)} are not of variant {rec.variant!r}, so "
            "the journal is not the evidence of the variant being promoted."
        )
    if split is not None:
        wrong = [t.trade_id for t in trades if (t.signal_ts < split) != (window == WINDOW_TRAIN)]
        if wrong:
            out.append(
                f"{window} journal trades {_ids(wrong)} have signals on the wrong side of "
                f"walk_forward.split_utc {ms_to_iso(split)} (TRAIN signals are before it, TEST "
                "signals at or after it)."
            )
    return out


def _verdict_mismatches(wf: WalkForwardStage, v: WalkForwardVerdict) -> list[str]:
    source = "recomputed from the hash-bound journals with the recorded parameters"
    out: list[str] = []
    if wf.label != v.label:
        out.append(
            f"walk_forward.label is {wf.label!r} but metrics.label {source} gives "
            f"{v.label!r}, so the typed label does not match its evidence."
        )
    for name, typed, actual in (
        ("train_n", wf.train_n, v.train.n),
        ("test_n", wf.test_n, v.test.n),
    ):
        if typed != actual:
            out.append(
                f"walk_forward.{name} is {typed!r} but the journal holds {actual} closed trades, "
                "so the typed count does not match its evidence."
            )
    for name, typed, avg in (
        ("train_avg_r", wf.train_avg_r, v.train.avg_r),
        ("test_avg_r", wf.test_avg_r, v.test.avg_r),
    ):
        if not _num_close(typed, avg):
            out.append(
                f"walk_forward.{name} is {typed!r} but the journal gives {avg!r}R (tolerance "
                f"{AVG_R_TOL:g}), so the typed expectancy does not match its evidence."
            )
    if wf.dd_ok is not v.dd_ok:
        out.append(
            f"walk_forward.dd_ok is {wf.dd_ok!r} but metrics.dd_check {source} gives "
            f"{v.dd_ok!r}, so the typed drawdown verdict does not match its evidence."
        )
    return out


def _recompute_problems(ctx: _Ctx, train: list[Trade], test: list[Trade]) -> list[str]:
    wf = ctx.record.walk_forward
    if not (_pos_int(wf.min_train) and _pos_int(wf.min_test)) or wf.label_params is None:
        return []  # _wf_param_problems already blocks; nothing well-defined to recompute
    try:
        verdict = recompute_walk_forward(
            train,
            test,
            ctx.cfg,
            wf.min_train,
            wf.min_test,
            wf.label_params,  # type: ignore[arg-type]
        )
    except (TypeError, ValueError) as exc:
        return [f"the walk-forward verdict could not be recomputed from the journals ({exc})."]
    return _verdict_mismatches(wf, verdict)


def _walk_forward_problems(ctx: _Ctx) -> list[str]:
    wf = ctx.record.walk_forward
    _ts, split_problem = _time(wf.split_utc, "walk_forward.split_utc", ctx.now)
    _p, path_problem = _path(ctx, wf.report_path, "walk_forward.report_path")
    out = _problems(
        _label_problem(wf.label),
        _dd_problem(wf.dd_ok),
        split_problem,
        _positive_count(wf.train_n, "walk_forward.train_n"),
        _positive_count(wf.test_n, "walk_forward.test_n"),
        _robust_sample_problem(wf),
        _avg_r_problem(wf.test_avg_r),
        path_problem,
    )
    out += _wf_param_problems(wf)
    journals, journal_problems = _wf_journals(ctx)
    out += journal_problems
    if journals is not None:
        train, test = journals
        out += _window_problems(WINDOW_TRAIN, train, ctx)
        out += _window_problems(WINDOW_TEST, test, ctx)
        out += _recompute_problems(ctx, train, test)
    return out


# ---------------------------------------------------------------------------- human review
def _coverage_problems(ctx: _Ctx) -> list[str]:
    hr, wf = ctx.record.human_review, ctx.record.walk_forward
    total, reviewed = hr.trades_total, hr.trades_reviewed
    problem = _positive_count(total, "human_review.trades_total")
    if problem is not None:
        return [problem]
    out: list[str] = []
    if not _is_int(reviewed):
        out.append(f"human_review.trades_reviewed must be a trade count (got {reviewed!r}).")
    elif reviewed != total:
        out.append(
            f"human_review.trades_reviewed is {reviewed} of {total} trades, and every trade "
            "must be reviewed individually."
        )
    if _pos_int(wf.train_n) and _pos_int(wf.test_n):
        wf_total = wf.train_n + wf.test_n  # type: ignore[operator]
        if total != wf_total:
            out.append(
                f"human_review.trades_total is {total} but the walk-forward produced "
                f"{wf_total} trades (train {wf.train_n} + test {wf.test_n}), so the review did "
                "not cover every trade."
            )
    return out


def _review_key_problems(
    rows: Sequence[ReviewRow], train: Sequence[Trade], test: Sequence[Trade]
) -> list[str]:
    expected = {(WINDOW_TRAIN, t.trade_id): t for t in train}
    expected.update({(WINDOW_TEST, t.trade_id): t for t in test})
    got = {r.key: r for r in rows}
    out: list[str] = []
    missing = sorted(set(expected) - set(got))
    if missing:
        out.append(
            f"the review pack has no row for walk-forward trades {_keys(missing)}, so they "
            "were never reviewed."
        )
    extra = sorted(set(got) - set(expected))
    if extra:
        out.append(
            f"the review pack has rows {_keys(extra)} that are not trades of the walk-forward "
            "journals, so it is not the review pack of this walk-forward."
        )
    differ = sorted(
        k
        for k in set(expected) & set(got)
        if (got[k].pair, got[k].signal_ts) != (expected[k].pair, expected[k].signal_ts)
    )
    if differ:
        out.append(
            f"review rows {_keys(differ)} name a different pair or signal time than the journal "
            "trade with the same (window, trade_id), so the pack does not describe these trades."
        )
    return out


def _review_verdict_problems(rows: Sequence[ReviewRow]) -> list[str]:
    out: list[str] = []
    blank = [r.key for r in rows if r.reviewer_ok not in (REVIEW_OK_YES, REVIEW_OK_NO)]
    if blank:
        out.append(
            f"{len(blank)} review rows ({_keys(blank)}) have no reviewer_ok verdict, and every "
            f"trade needs an explicit {REVIEW_OK_YES} or {REVIEW_OK_NO}."
        )
    rejected = [r.key for r in rows if r.reviewer_ok == REVIEW_OK_NO]
    if rejected:
        out.append(
            f"the reviewer rejected {len(rejected)} trades ({_keys(rejected)}) with reviewer_ok "
            f"{REVIEW_OK_NO}, and by policy {REVIEW_REJECTION_POLICY}."
        )
    return out


def _review_pack_problems(ctx: _Ctx) -> list[str]:
    hr, wf = ctx.record.human_review, ctx.record.walk_forward
    path, problems = _hash_bound(
        ctx,
        hr.review_path,
        hr.review_sha256,
        "human_review.review_path",
        "human_review.review_sha256",
    )
    if path is None:
        return problems
    try:
        rows = load_review(path)
    except (OSError, ValueError) as exc:
        return [f"human_review.review_path could not be read as a review pack ({exc})."]
    out: list[str] = []
    if _pos_int(wf.train_n) and _pos_int(wf.test_n) and len(rows) != wf.train_n + wf.test_n:  # type: ignore[operator]
        out.append(
            f"the review pack has {len(rows)} rows but the walk-forward produced "
            f"{wf.train_n + wf.test_n} trades (train {wf.train_n} + test {wf.test_n}), so it "  # type: ignore[operator]
            "does not cover every trade exactly once."
        )
    # Without verified journals the WALK_FORWARD stage (always checked before this one)
    # already blocks, so the key comparison is skipped rather than reported twice.
    journals, _unused = _wf_journals(ctx)
    if journals is not None:
        out += _review_key_problems(rows, *journals)
    return out + _review_verdict_problems(rows)


def _human_review_problems(ctx: _Ctx) -> list[str]:
    hr = ctx.record.human_review
    reviewer_problem = None
    if _blank(hr.reviewer):
        reviewer_problem = "human_review.reviewer is missing: a named person must sign off."
    date, date_problem = _time(hr.date_utc, "human_review.date_utc", ctx.now)
    order_problem = None
    backtest_done = _quiet_ts(ctx.record.backtest.completed_utc)
    if date is not None and backtest_done is not None and date < backtest_done:
        order_problem = (
            f"human_review.date_utc {ms_to_iso(date)} is before the backtest completed at "
            f"{ms_to_iso(backtest_done)}, so the review cannot have covered its trades."
        )
    approved_problem = None
    if hr.approved is False:
        approved_problem = "human_review.approved is false: the reviewer did not approve."
    elif hr.approved is not True:
        approved_problem = "human_review.approved is missing, so there is no sign-off."
    out = _problems(reviewer_problem, date_problem, order_problem)
    out += _coverage_problems(ctx)
    out += _problems(approved_problem)
    return out + _review_pack_problems(ctx)


# ---------------------------------------------------------------------------- testnet
def _testnet_window_problems(ctx: _Ctx) -> list[str]:
    tn = ctx.record.testnet
    start, start_problem = _time(tn.start_utc, "testnet.start_utc", ctx.now)
    end, end_problem = _time(tn.end_utc, "testnet.end_utc", ctx.now)
    out = _problems(start_problem, end_problem)
    if start is not None and end is not None and end - start < TESTNET_MIN_DAYS * DAY_MS:
        days = math.floor((end - start) / DAY_MS * 100) / 100  # truncate: never shows "14.00"
        out.append(
            f"testnet ran {days:.2f} days ({ms_to_iso(start)} to "
            f"{ms_to_iso(end)}), short of the required {TESTNET_MIN_DAYS} days."
        )
    signed_off = _quiet_ts(ctx.record.human_review.date_utc)
    if start is not None and signed_off is not None and start < signed_off:
        out.append(
            f"testnet.start_utc {ms_to_iso(start)} is before the human review sign-off at "
            f"{ms_to_iso(signed_off)}, so the testnet run was not of an approved variant."
        )
    return out


def _violations_problem(value: object) -> str | None:
    if value is None:
        return "testnet.rule_violations is not recorded, and promotion needs an explicit 0."
    if _is_int(value) and value == 0:
        return None
    return (
        f"testnet.rule_violations is {value!r}, and any rule violation on testnet blocks "
        "promotion until it is fixed and the testnet run repeated."
    )


def _violation_codes(trade: Trade, cfg: StrategyConfig) -> set[str]:
    return {flag_code(f) for f in auto_flags(trade, cfg)} & RULE_VIOLATION_FLAGS


def _testnet_events(ctx: _Ctx) -> tuple[list[NewsEvent] | None, list[str]]:
    """The hash-bound testnet news calendar, ``(None, [])`` if none is recorded."""
    tn = ctx.record.testnet
    if _blank(tn.events_path) and _blank(tn.events_sha256):
        return None, []
    path, problems = _hash_bound(
        ctx, tn.events_path, tn.events_sha256, "testnet.events_path", "testnet.events_sha256"
    )
    if path is None:
        return None, problems
    try:
        return load_events(path), []
    except (OSError, ValueError) as exc:
        return None, [f"testnet.events_path could not be read as a news calendar ({exc})."]


def _cross_trade_problems(ctx: _Ctx, trades: Sequence[Trade]) -> list[str]:
    """R6 / R9 (and R5 with a calendar) recomputed over the testnet journal, offset 0."""
    events, problems = _testnet_events(ctx)
    if problems:
        return problems
    try:
        violations = cross_trade_violations(trades, ctx.cfg, events, exit_time_uncertainty_ms=0)
    except ValueError as exc:
        return [f"the testnet journal could not be audited for cross-trade rules ({exc})."]
    if not violations:
        return []
    scope = "R5 checked against testnet.events_path" if events is not None else R5_NOT_VERIFIABLE
    shown = "; ".join(violations[:_MAX_VIOLATIONS_SHOWN])
    extra = len(violations) - _MAX_VIOLATIONS_SHOWN
    more = f" (+{extra} more)" if extra > 0 else ""
    return [
        f"the testnet journal breaks mandatory cross-trade rules recomputed from its rows with "
        f"exit offset 0 for real fill times (R6 and R9 checked; {scope}): {shown}{more}, so "
        "testnet.rule_violations cannot be 0."
    ]


def _journal_problems(ctx: _Ctx, path: Path) -> list[str]:
    """Cross-check the testnet journal (the evidence) against the testnet record."""
    rec, tn = ctx.record, ctx.record.testnet
    try:
        trades = read_journal(path)
    except (OSError, ValueError) as exc:
        return [f"testnet.journal_path could not be read as a trade journal ({exc})."]
    out: list[str] = []
    if _is_int(tn.trades) and len(trades) != tn.trades:
        out.append(
            f"the testnet journal holds {len(trades)} trades but testnet.trades is {tn.trades}, "
            "so the record does not match its evidence."
        )
    start, end = _quiet_ts(tn.start_utc), _quiet_ts(tn.end_utc)
    if start is not None and end is not None:
        outside = [t.trade_id for t in trades if not start <= t.entry_ts <= end]
        if outside:
            out.append(
                f"testnet journal trades {_ids(outside)} entered outside the testnet window "
                f"{ms_to_iso(start)} to {ms_to_iso(end)}."
            )
    foreign = [t.trade_id for t in trades if t.variant != rec.variant]
    if foreign:
        out.append(
            f"testnet journal trades {_ids(foreign)} are not of variant {rec.variant!r}, so "
            "the testnet run was not of the variant being promoted."
        )
    flagged = [(t.trade_id, _violation_codes(t, ctx.cfg)) for t in trades]
    flagged = [(i, codes) for i, codes in flagged if codes]
    if flagged:
        codes = sorted(set().union(*(c for _i, c in flagged)))
        out.append(
            f"testnet journal trades {_ids([i for i, _c in flagged])} break mandatory rules "
            f"({', '.join(codes)}), so testnet.rule_violations cannot be 0."
        )
    return out + _cross_trade_problems(ctx, trades)


def _testnet_problems(ctx: _Ctx) -> list[str]:
    tn = ctx.record.testnet
    exchange_problem = None
    if tn.exchange != TESTNET_EXCHANGE:
        exchange_problem = (
            f"testnet.exchange is {tn.exchange!r}, but this stage must run on "
            f"{TESTNET_EXCHANGE!r} (never a real-money account)."
        )
    trades_problem = None
    if not (_is_int(tn.trades) and tn.trades >= 1):  # type: ignore[operator]
        trades_problem = (
            f"testnet.trades must be at least 1 (got {tn.trades!r}): a run without trades "
            "never exercised order, stop and exit handling."
        )
    journal, journal_problem = _path(ctx, tn.journal_path, "testnet.journal_path")
    out = _problems(exchange_problem) + _testnet_window_problems(ctx)
    out += _problems(trades_problem, _violations_problem(tn.rule_violations), journal_problem)
    if journal is not None:
        out += _journal_problems(ctx, journal)
    return out


def _live_problems(ctx: _Ctx) -> list[str]:
    out: list[str] = []
    if ctx.cfg.is_test_only:
        out.append(
            "the config switches the R4 regime filter off, an explicit test-only variant that "
            "may never trade live."
        )
    live = ctx.record.live
    if live.enabled_utc is not None:
        enabled, problem = _time(live.enabled_utc, "live.enabled_utc", ctx.now)
        out += _problems(problem)
        testnet_end = _quiet_ts(ctx.record.testnet.end_utc)
        if enabled is not None and testnet_end is not None and enabled < testnet_end:
            out.append(
                f"live.enabled_utc {ms_to_iso(enabled)} is before the testnet run ended at "
                f"{ms_to_iso(testnet_end)}, so live trading started before testnet finished."
            )
    return out


_STAGE_CHECKS: Mapping[str, Callable[[_Ctx], list[str]]] = MappingProxyType(
    {
        BACKTEST: _backtest_problems,
        WALK_FORWARD: _walk_forward_problems,
        HUMAN_REVIEW: _human_review_problems,
        TESTNET: _testnet_problems,
    }
)


# ---------------------------------------------------------------------------- public checks
def _stage_index(stage: str) -> int:
    if stage not in STAGES:
        raise ValueError(f"unknown adoption stage {stage!r}; expected one of {STAGES}")
    return STAGES.index(stage)


def check_promotion(
    record: AdoptionRecord,
    target_stage: str,
    cfg: StrategyConfig,
    now_utc_ms: int,
    base_dir: str | Path | None = None,
    model_fingerprint: str | None = None,
) -> list[Decision]:
    """Every reason ``record`` may NOT be promoted to ``target_stage`` (empty = allowed).

    All stages before ``target_stage`` must be complete and consistent with their evidence
    files (the module docstring lists every check, and what is NOT verified). A stage with
    nothing recorded yields one "no recorded result" decision. The record's
    ``config_fingerprint`` must equal ``config_fingerprint(cfg)`` for every target, and
    ``model_fingerprint`` (the ``MLFilter.fingerprint()`` of the ML model being promoted, or
    ``None`` if there is none) must equal the record's ``model_fingerprint``. Relative
    evidence paths are resolved against ``base_dir`` (``None``: the current working
    directory). Each decision has ``allowed=False``, a rule id from
    :data:`ADOPTION_RULE_IDS` and a one-sentence reason, in the order identity, provenance,
    stages, live. Raises ``ValueError`` for an unknown ``target_stage``.
    """
    target = _stage_index(target_stage)
    root = Path.cwd() if base_dir is None else Path(base_dir)
    ctx = _Ctx(record, cfg, now_utc_ms, root, model_fingerprint)
    out = _record_problems(ctx)
    if target >= STAGES.index(WALK_FORWARD):
        require_real = target >= STAGES.index(HUMAN_REVIEW)
        problems = _provenance_problems(ctx, require_real)
        out.extend(Decision(False, ADOPT_PROVENANCE, r) for r in problems)
    for stage in STAGES[:target]:
        rule = STAGE_RULES[stage]
        if _is_empty(getattr(record, STAGE_SECTIONS[stage])):
            reason = (
                f"stage {stage} has no recorded result, and {target_stage} may only follow a "
                f"completed {stage} (no step can be skipped)."
            )
            out.append(Decision(False, rule, reason))
            continue
        out.extend(Decision(False, rule, r) for r in _STAGE_CHECKS[stage](ctx))
    if target_stage == LIVE:
        out.extend(Decision(False, ADOPT_LIVE, r) for r in _live_problems(ctx))
    return out


def unverified_notes(record: AdoptionRecord, target_stage: str) -> list[str]:
    """One sentence per thing :func:`check_promotion` does NOT verify for ``target_stage``.

    Read these before trusting a PASS (the CLI prints them). Raises ``ValueError`` for an
    unknown stage.
    """
    target = _stage_index(target_stage)
    out: list[str] = []
    if target >= STAGES.index(HUMAN_REVIEW):
        out.append(
            "The sha256 hashes bind the data files, journals and report to this record, but "
            "they do not prove the journals were produced from those data files (re-run "
            "run_research or the invariants CLI on the data to check that)."
        )
        out.append(
            "dd_ok is recomputed with metrics.dd_check on the closed-trade TEST summary only; "
            "the mark-to-market drawdown of CONTRACT v3 C5 needs the candles and is not "
            "recomputed here."
        )
    if target >= STAGES.index(TESTNET):
        out.append(
            "The review pack proves every trade got an explicit Y, not that the reviewer "
            "inspected each trade."
        )
    if target_stage == LIVE:
        if _blank(record.testnet.events_path):
            out.append(f"{R5_NOT_VERIFIABLE[0].upper()}{R5_NOT_VERIFIABLE[1:]}.")
        out.append(
            "Per-candle rules of the testnet trades (R1-R4 gates, R8 structure stops, fill "
            "prices) need the candles and are not checked here (use python -m "
            "research.trendbot.invariants), and that the account was a testnet rests on the "
            "typed exchange field."
        )
    return out


class AdoptionBlocked(RuntimeError):
    """Raised by :func:`require_stage`; ``decisions`` holds every blocking reason."""

    def __init__(self, stage: str, decisions: Sequence[Decision]) -> None:
        self.stage = stage
        self.decisions = tuple(decisions)
        reasons = " ".join(f"[{d.rule}] {d.reason}" for d in self.decisions)
        super().__init__(f"promotion to {stage} is blocked: {reasons}")


def require_stage(
    record: AdoptionRecord,
    stage: str,
    cfg: StrategyConfig,
    now_utc_ms: int,
    base_dir: str | Path | None = None,
    model_fingerprint: str | None = None,
) -> None:
    """Raise :class:`AdoptionBlocked` unless :func:`check_promotion` allows ``stage``.

    The live bot calls this with ``"LIVE"`` (the record's directory as ``base_dir`` and, if it
    runs an ML filter, that model's ``MLFilter.fingerprint()``) before its first order and
    refuses to start if it raises.
    """
    decisions = check_promotion(record, stage, cfg, now_utc_ms, base_dir, model_fingerprint)
    if decisions:
        raise AdoptionBlocked(stage, decisions)


# ---------------------------------------------------------------------------- CLI
def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m research.trendbot.adoption",
        description="Enforce BACKTEST -> WALK_FORWARD -> HUMAN_REVIEW -> TESTNET -> LIVE.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    config_help = "JSON object of StrategyConfig overrides (default: the default config)"
    check = sub.add_parser("check", help="may the record be promoted to --stage?")
    check.add_argument("--record", required=True, type=Path, help="adoption record JSON")
    check.add_argument("--stage", required=True, type=str.upper, choices=STAGES)
    check.add_argument("--now", default=None, help="ISO-8601 UTC or epoch ms (default: now)")
    check.add_argument("--config", type=Path, default=None, help=config_help)
    check.add_argument(
        "--model-fingerprint",
        default=None,
        help=(
            "sha256 (MLFilter.fingerprint()) of the ML model that will run; required when the "
            "record has a model_fingerprint, and it must match it exactly"
        ),
    )
    init = sub.add_parser("init", help="write an empty record for a variant")
    init.add_argument("--variant", required=True, help="strategy variant id, e.g. base")
    init.add_argument("--out", required=True, type=Path, help="record JSON to create")
    init.add_argument("--config", type=Path, default=None, help=config_help)
    init.add_argument(
        "--model-fingerprint",
        default=None,
        help="sha256 (MLFilter.fingerprint()) of the tested ML model; required for a +ml variant",
    )
    init.add_argument("--force", action="store_true", help="overwrite an existing record")
    fingerprint = sub.add_parser("fingerprint", help="print a config's canonical JSON + sha256")
    fingerprint.add_argument("--config", type=Path, default=None, help=config_help)
    hashes = sub.add_parser("hash", help="print the sha256 of evidence files (*_sha256 fields)")
    hashes.add_argument("files", nargs="+", type=Path, help="files to hash")
    return parser


def _model_arg(value: str | None) -> str | None:
    """``--model-fingerprint`` with surrounding whitespace removed (blank = not given)."""
    if value is None or not value.strip():
        return None
    return value.strip()


def _cmd_check(args: argparse.Namespace, now: int) -> int:
    cfg = load_config(args.config)
    record = load_record(args.record)
    model_fp = _model_arg(args.model_fingerprint)
    decisions = check_promotion(
        record, args.stage, cfg, now, args.record.resolve().parent, model_fp
    )
    fp = config_fingerprint(cfg)[:_FP_SHOWN]
    what = f"config {fp}" if model_fp is None else f"config {fp}, model {model_fp[:_FP_SHOWN]}"
    if not decisions:
        print(
            f"PASS: variant {record.variant!r} ({what}) may be promoted to "
            f"{args.stage} at {ms_to_iso(now)}."
        )
    else:
        print(
            f"BLOCKED: variant {record.variant!r} ({what}) may not be promoted to "
            f"{args.stage} at {ms_to_iso(now)}; {len(decisions)} blocking reason(s):"
        )
        for d in decisions:
            print(f"- [{d.rule}] {d.reason}")
    notes = unverified_notes(record, args.stage)
    if notes:
        print("Not verified by this check (see the adoption.py docstring):")
        for note in notes:
            print(f"  * {note}")
    return 1 if decisions else 0


def _cmd_init(args: argparse.Namespace) -> int:
    if args.out.exists() and not args.force:
        print(f"error: {args.out} exists; refusing to overwrite (use --force)", file=sys.stderr)
        return 1
    if _blank(args.variant):
        print("error: --variant may not be empty", file=sys.stderr)
        return 1
    variant, model_fp = args.variant.strip(), _model_arg(args.model_fingerprint)
    if model_fp is not None and not is_sha256_hex(model_fp):
        print(
            f"error: --model-fingerprint {model_fp!r} is not a sha256 hex digest "
            f"({_SHA256_HEX_LEN} hex characters)",
            file=sys.stderr,
        )
        return 1
    if model_fp is None and is_ml_variant(variant):
        print(
            f"error: variant {variant!r} runs an ML filter ({ML_VARIANT_MARKER!r} in its id), so "
            "--model-fingerprint is required",
            file=sys.stderr,
        )
        return 1
    record = empty_record(variant, load_config(args.config), model_fp)
    save_record(record, args.out)
    model = "" if model_fp is None else f", model {model_fp}"
    print(
        f"Wrote an empty adoption record for variant {record.variant!r} (config "
        f"{record.config_fingerprint}{model}) to {args.out}; next stage: {BACKTEST}."
    )
    return 0


def _cmd_fingerprint(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    print(canonical_config_json(cfg))
    print(config_fingerprint(cfg))
    return 0


def _cmd_hash(args: argparse.Namespace) -> int:
    for path in args.files:
        print(f"{sha256_file(path)}  {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    now = time.time_ns() // 1_000_000
    if getattr(args, "now", None) is not None:
        try:
            now = iso_to_ms(args.now)
        except (ValueError, OverflowError):
            parser.error(f"--now: not an ISO-8601 time or epoch ms: {args.now!r}")
    commands = {
        "check": lambda: _cmd_check(args, now),
        "init": lambda: _cmd_init(args),
        "fingerprint": lambda: _cmd_fingerprint(args),
        "hash": lambda: _cmd_hash(args),
    }
    try:
        return commands[args.command]()
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
