"""Adoption path enforcement: no variant reaches real money by skipping a step.

The ONLY way from research to live trading is, strictly in this order::

    BACKTEST -> WALK_FORWARD -> HUMAN_REVIEW -> TESTNET (>= 14 days, Binance testnet) -> LIVE

A good backtest number never shortcuts the path. Promotion to a stage is allowed only when
every EARLIER stage is complete and consistent, and the config being promoted is exactly the
config that was tested (sha256 :func:`config_fingerprint`). :func:`check_promotion` returns
the blocking :class:`~.models.Decision` list (empty = allowed); a live bot must call
:func:`require_stage` with ``"LIVE"`` at startup and refuse to trade if it raises.

What "complete" means (every item is a separate blocking reason, rule ``ADOPT_<stage>``):

- BACKTEST: ``report_path`` set; ``completed_utc`` a valid time not after the check time.
- WALK_FORWARD: ``label`` exactly ``"ROBUST"`` (TRAIN-ONLY, UNTESTED and NO-EDGE all block);
  ``dd_ok`` exactly ``true``; ``split_utc`` valid; ``train_n``/``test_n`` positive;
  ``test_avg_r`` > 0 (expectancy, never win rate); ``report_path`` set.
- HUMAN_REVIEW: a named ``reviewer``; ``date_utc`` valid, not before the backtest completed;
  ``trades_reviewed == trades_total > 0`` and ``trades_total == train_n + test_n`` (EVERY
  walk-forward trade was looked at); ``approved`` exactly ``true``.
- TESTNET: ``exchange == "binance-testnet"``; ``end_utc - start_utc >= 14 days``;
  ``end_utc`` not after the check time; ``start_utc`` not before the review sign-off (the
  testnet run is of the APPROVED variant); ``trades >= 1`` (otherwise the order, stop and
  exit handling was never exercised); ``rule_violations`` recorded as exactly 0;
  ``journal_path`` set.
- LIVE (checked only when promoting to LIVE): the config is not an explicit test-only
  variant (R4 regime filter off never trades live); ``live.enabled_utc``, if already
  recorded, is not before the testnet run ended.
- Always: ``variant`` named (``ADOPT_record``) and ``config_fingerprint`` equal to
  ``config_fingerprint(cfg)`` (``ADOPT_fingerprint``).

Evidence files. With ``base_dir`` (the CLI always passes the record file's directory) the
referenced files must exist; relative paths are resolved against ``base_dir``. The testnet
journal (``journal.write_journal`` format) is then cross-checked against the record: its
trade count must equal ``testnet.trades``, every entry must lie inside the testnet window,
every trade must carry the record's ``variant``, and no trade may carry a mandatory-rule
flag from ``review_sheet.auto_flags`` (:data:`RULE_VIOLATION_FLAGS`), so a hand-typed
``rule_violations: 0`` cannot hide a violation the journal shows. (A live bot must round
quantities DOWN and take-profit prices UP to the exchange tick so R7 and the 2:1 minimum
hold after rounding.)

Record file (JSON, written by :func:`save_record`, every value ``null`` until filled in;
times are ISO-8601 UTC strings such as ``"2024-03-12T12:30:00Z"``)::

    {"variant": "base", "config_fingerprint": "<sha256 hex>",
     "backtest": {"report_path", "completed_utc"},
     "walk_forward": {"label", "dd_ok", "split_utc", "train_n", "test_n", "test_avg_r",
                      "report_path"},
     "human_review": {"reviewer", "date_utc", "trades_reviewed", "trades_total", "approved",
                      "notes"},
     "testnet": {"exchange", "start_utc", "end_utc", "trades", "rule_violations",
                 "journal_path", "notes"},
     "live": {"enabled_utc"}}

CLI::

    python -m research.trendbot.adoption init --variant base --out rec.json [--config c.json]
    python -m research.trendbot.adoption check --record rec.json --stage LIVE [--now ISO]
    python -m research.trendbot.adoption fingerprint [--config c.json]

``--config`` is a JSON object of ``StrategyConfig`` field overrides (validated: a loosening
of a mandatory rule is refused); without it the default config is used. ``check`` prints
``PASS`` or every blocking reason and exits 0 / 1.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import math
import sys
import time
import typing
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from types import MappingProxyType

from .config import ConfigError, PairRisk, StrategyConfig
from .journal import iso_to_ms, ms_to_iso, read_journal
from .metrics import LABELS
from .models import DAY_MS, Decision, Trade
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


BACKTEST = "BACKTEST"
WALK_FORWARD = "WALK_FORWARD"
HUMAN_REVIEW = "HUMAN_REVIEW"
TESTNET = "TESTNET"
LIVE = "LIVE"
STAGES: tuple[str, ...] = (BACKTEST, WALK_FORWARD, HUMAN_REVIEW, TESTNET, LIVE)

REQUIRED_WF_LABEL = "ROBUST"
TESTNET_EXCHANGE = "binance-testnet"
TESTNET_MIN_DAYS = 14

ADOPT_RECORD = "ADOPT_record"
ADOPT_FINGERPRINT = "ADOPT_fingerprint"
ADOPT_BACKTEST = "ADOPT_backtest"
ADOPT_WALK_FORWARD = "ADOPT_walk_forward"
ADOPT_HUMAN_REVIEW = "ADOPT_human_review"
ADOPT_TESTNET = "ADOPT_testnet"
ADOPT_LIVE = "ADOPT_live"

# Rule ids of this module (the adoption path is not a trading rule, so they are kept here
# next to the checks rather than in config.RULE_IDS).
ADOPTION_RULE_IDS: Mapping[str, str] = MappingProxyType(
    {
        ADOPT_RECORD: "The adoption record names the variant it tracks",
        ADOPT_FINGERPRINT: "The config promoted is exactly the config tested (sha256)",
        ADOPT_BACKTEST: "Stage 1: a completed backtest with a report",
        ADOPT_WALK_FORWARD: "Stage 2: 70/30 walk-forward labelled ROBUST, drawdown within limit",
        ADOPT_HUMAN_REVIEW: "Stage 3: a named human reviewed EVERY trade and approved",
        ADOPT_TESTNET: "Stage 4: >= 14 days on Binance testnet, zero rule violations, journal",
        ADOPT_LIVE: "Stage 5: live trading (never for an explicit test-only config)",
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

_LABEL_WHY = {
    "TRAIN-ONLY": "the edge did not hold out of sample, a sign of curve-fitting",
    "UNTESTED": "too few trades, or a test expectancy not distinguishable from zero",
    "NO-EDGE": "the train window showed no positive expectancy to validate",
}
_MAX_IDS_SHOWN = 5
_FP_SHOWN = 16


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
class BacktestStage:
    report_path: str | None = None
    completed_utc: str | None = None


@dataclass(frozen=True, slots=True)
class WalkForwardStage:
    label: str | None = None  # metrics.LABELS; only "ROBUST" may proceed
    dd_ok: bool | None = None  # metrics.dd_check on the TEST window
    split_utc: str | None = None  # the 70/30 chronological split time
    train_n: int | None = None
    test_n: int | None = None
    test_avg_r: float | None = None  # out-of-sample expectancy (R per trade)
    report_path: str | None = None


@dataclass(frozen=True, slots=True)
class HumanReviewStage:
    reviewer: str | None = None
    date_utc: str | None = None
    trades_reviewed: int | None = None
    trades_total: int | None = None
    approved: bool | None = None
    notes: str | None = None


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


@dataclass(frozen=True, slots=True)
class LiveStage:
    enabled_utc: str | None = None


@dataclass(frozen=True, slots=True)
class AdoptionRecord:
    variant: str
    config_fingerprint: str
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


def empty_record(variant: str, cfg: StrategyConfig | None = None) -> AdoptionRecord:
    """A record with no stage completed, fingerprinted with ``cfg`` (default config)."""
    return AdoptionRecord(variant, config_fingerprint(cfg if cfg is not None else StrategyConfig()))


def _kind(hint: object) -> type:
    args = [a for a in typing.get_args(hint) if a is not type(None)]
    return args[0] if args else hint  # type: ignore[return-value]


def _typed(value: object, kind: type, where: str) -> object:
    if value is None:
        return None
    if kind is bool:
        ok = isinstance(value, bool)
    elif kind is int:
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif kind is float:
        ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        ok = ok and math.isfinite(value)  # type: ignore[arg-type]
        value = float(value) if ok else value  # type: ignore[arg-type]
    else:
        ok = isinstance(value, kind)
    if not ok:
        raise ValueError(f"{where} must be {_KIND_NAMES[kind]} or null (got {value!r})")
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
    known = ("variant", "config_fingerprint", *_SECTIONS)
    unknown = sorted(set(data) - set(known))
    if unknown:
        raise ValueError(f"{where}: unknown keys {unknown} (expected {list(known)})")
    head = {}
    for key in ("variant", "config_fingerprint"):
        if not isinstance(data.get(key), str):
            raise ValueError(f"{where}.{key} must be a string (got {data.get(key)!r})")
        head[key] = data[key]
    stages = {k: _section(cls, data.get(k), f"{where}.{k}") for k, cls in _SECTIONS.items()}
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


# ---------------------------------------------------------------------------- check helpers
@dataclass(frozen=True, slots=True)
class _Ctx:
    record: AdoptionRecord
    cfg: StrategyConfig
    now: int
    base_dir: Path | None


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


def _path(value: str | None, name: str, base_dir: Path | None) -> tuple[Path | None, str | None]:
    """``(resolved existing path or None, problem sentence)``; existence only with base_dir."""
    if _blank(value):
        return None, f"{name} is missing, so there is no evidence file to audit."
    if base_dir is None:
        return None, None
    p = Path(value)  # type: ignore[arg-type]
    p = p if p.is_absolute() else base_dir / p
    if not p.is_file():
        return None, f"{name} {value!r} does not exist (looked for {p})."
    return p, None


def _pos_int(value: object) -> bool:
    return _is_int(value) and value > 0  # type: ignore[operator]


def _positive_count(value: object, name: str) -> str | None:
    if _pos_int(value):
        return None
    return f"{name} must be a positive trade count (got {value!r})."


def _ids(ids: Sequence[int]) -> str:
    shown = ", ".join(f"#{i}" for i in ids[:_MAX_IDS_SHOWN])
    extra = len(ids) - _MAX_IDS_SHOWN
    return f"{shown} (+{extra} more)" if extra > 0 else shown


def _problems(*items: str | None) -> list[str]:
    return [p for p in items if p is not None]


# ---------------------------------------------------------------------------- stage checks
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
    return out


def _backtest_problems(ctx: _Ctx) -> list[str]:
    bt = ctx.record.backtest
    _p, path_problem = _path(bt.report_path, "backtest.report_path", ctx.base_dir)
    _ts, time_problem = _time(bt.completed_utc, "backtest.completed_utc", ctx.now)
    return _problems(path_problem, time_problem)


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


def _walk_forward_problems(ctx: _Ctx) -> list[str]:
    wf = ctx.record.walk_forward
    _ts, split_problem = _time(wf.split_utc, "walk_forward.split_utc", ctx.now)
    avg_r_problem = None
    avg_r = wf.test_avg_r
    if isinstance(avg_r, bool) or not (isinstance(avg_r, (int, float)) and avg_r > 0):
        avg_r_problem = (
            f"walk_forward.test_avg_r must be a positive out-of-sample expectancy in R "
            f"(got {avg_r!r})."
        )
    _p, path_problem = _path(wf.report_path, "walk_forward.report_path", ctx.base_dir)
    return _problems(
        _label_problem(wf.label),
        _dd_problem(wf.dd_ok),
        split_problem,
        _positive_count(wf.train_n, "walk_forward.train_n"),
        _positive_count(wf.test_n, "walk_forward.test_n"),
        avg_r_problem,
        path_problem,
    )


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
    return out


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
    return out


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
    journal, journal_problem = _path(tn.journal_path, "testnet.journal_path", ctx.base_dir)
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


_STAGE_CHECKS = {
    BACKTEST: _backtest_problems,
    WALK_FORWARD: _walk_forward_problems,
    HUMAN_REVIEW: _human_review_problems,
    TESTNET: _testnet_problems,
}


# ---------------------------------------------------------------------------- public checks
def check_promotion(
    record: AdoptionRecord,
    target_stage: str,
    cfg: StrategyConfig,
    now_utc_ms: int,
    base_dir: str | Path | None = None,
) -> list[Decision]:
    """Every reason ``record`` may NOT be promoted to ``target_stage`` (empty = allowed).

    All stages before ``target_stage`` must be complete (see the module docstring); a stage
    with nothing recorded yields one "no recorded result" decision. The record's
    ``config_fingerprint`` must equal ``config_fingerprint(cfg)`` for every target. With
    ``base_dir`` the evidence files must exist and the testnet journal is cross-checked.
    Each decision has ``allowed=False``, a rule id from :data:`ADOPTION_RULE_IDS` and a
    one-sentence reason. Raises ``ValueError`` for an unknown ``target_stage``.
    """
    if target_stage not in STAGES:
        raise ValueError(f"unknown adoption stage {target_stage!r}; expected one of {STAGES}")
    ctx = _Ctx(record, cfg, now_utc_ms, None if base_dir is None else Path(base_dir))
    out = _record_problems(ctx)
    for stage in STAGES[: STAGES.index(target_stage)]:
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
) -> None:
    """Raise :class:`AdoptionBlocked` unless :func:`check_promotion` allows ``stage``.

    The live bot calls this with ``"LIVE"`` (and the record's directory as ``base_dir``)
    before its first order and refuses to start if it raises.
    """
    decisions = check_promotion(record, stage, cfg, now_utc_ms, base_dir)
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
    init = sub.add_parser("init", help="write an empty record for a variant")
    init.add_argument("--variant", required=True, help="strategy variant id, e.g. base")
    init.add_argument("--out", required=True, type=Path, help="record JSON to create")
    init.add_argument("--config", type=Path, default=None, help=config_help)
    init.add_argument("--force", action="store_true", help="overwrite an existing record")
    fingerprint = sub.add_parser("fingerprint", help="print a config's canonical JSON + sha256")
    fingerprint.add_argument("--config", type=Path, default=None, help=config_help)
    return parser


def _cmd_check(args: argparse.Namespace, now: int) -> int:
    cfg = load_config(args.config)
    record = load_record(args.record)
    decisions = check_promotion(record, args.stage, cfg, now, args.record.resolve().parent)
    fp = config_fingerprint(cfg)[:_FP_SHOWN]
    if not decisions:
        print(
            f"PASS: variant {record.variant!r} (config {fp}) may be promoted to "
            f"{args.stage} at {ms_to_iso(now)}."
        )
        return 0
    print(
        f"BLOCKED: variant {record.variant!r} (config {fp}) may not be promoted to "
        f"{args.stage} at {ms_to_iso(now)}; {len(decisions)} blocking reason(s):"
    )
    for d in decisions:
        print(f"- [{d.rule}] {d.reason}")
    return 1


def _cmd_init(args: argparse.Namespace) -> int:
    if args.out.exists() and not args.force:
        print(f"error: {args.out} exists; refusing to overwrite (use --force)", file=sys.stderr)
        return 1
    if _blank(args.variant):
        print("error: --variant may not be empty", file=sys.stderr)
        return 1
    record = empty_record(args.variant.strip(), load_config(args.config))
    save_record(record, args.out)
    print(
        f"Wrote an empty adoption record for variant {record.variant!r} (config "
        f"{record.config_fingerprint}) to {args.out}; next stage: {BACKTEST}."
    )
    return 0


def _cmd_fingerprint(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    print(canonical_config_json(cfg))
    print(config_fingerprint(cfg))
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
    try:
        if args.command == "check":
            return _cmd_check(args, now)
        if args.command == "init":
            return _cmd_init(args)
        return _cmd_fingerprint(args)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
