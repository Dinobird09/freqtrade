"""Per-stage problem finders of the adoption gate (used by :func:`.adoption.check_promotion`).

Each ``*_problems(ctx)`` function returns one sentence per problem of one stage or rule; the
module docstring of :mod:`.adoption` lists exactly what each one verifies. Evidence files are
resolved against ``ctx.base_dir`` and parsed once per check (``ctx.cache``). The TESTNET
stage lives in :mod:`.adoption_testnet`, which shares the context and helpers defined here.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, fields
from pathlib import Path

from . import metrics
from .adoption_evidence import (
    C5_TOL,
    LabelParams,
    WalkForwardVerdict,
    distinct_looks,
    label_params_problems,
    load_candle_files,
    manifest_problems,
    own_looks,
    parse_label_params,
    read_ledger,
    recompute_test_mtm,
    recompute_train_dd_p95,
    recompute_walk_forward,
)
from .adoption_record import (
    ML_VARIANT_MARKER,
    PROVENANCE_REAL,
    PROVENANCE_UNVERIFIED,
    REQUIRED_WF_LABEL,
    SHA256_HEX_LEN,
    SYNTHETIC_PROVENANCE_PREFIX,
    WALK_FORWARD,
    AdoptionRecord,
    WalkForwardStage,
    config_fingerprint,
    is_ml_variant,
    is_sha256_hex,
    sha256_file,
)
from .config import StrategyConfig
from .journal import iso_to_ms, ms_to_iso, read_journal
from .metrics import LABELS
from .models import Trade
from .review_sheet import (
    REVIEW_OK_NO,
    REVIEW_OK_YES,
    WINDOW_TEST,
    WINDOW_TRAIN,
    ReviewRow,
    load_review,
)


ROBUST_MIN_N = metrics.ROBUST_MIN_N  # C3/C4: ROBUST needs >= 30 trades in EACH window
AVG_R_TOL = 1e-9  # absolute tolerance of typed vs recomputed avg R

REVIEW_REJECTION_POLICY = (
    "the variant must be revised and restart the adoption path at BACKTEST under a new "
    "record (a changed config has a new fingerprint) with TEST data it has never seen, so "
    "this record can never pass HUMAN_REVIEW"
)
_LABEL_WHY = {
    "TRAIN-ONLY": "the edge did not hold out of sample, a sign of curve-fitting",
    "UNTESTED": "too few trades, or a test expectancy not distinguishable from zero",
    "NO-EDGE": "the train window showed no positive expectancy to validate",
}
_MAX_IDS_SHOWN = 5
FP_SHOWN = 16


# ---------------------------------------------------------------------------- context
@dataclass(frozen=True, slots=True)
class CheckContext:
    record: AdoptionRecord
    cfg: StrategyConfig
    now: int
    base_dir: Path
    model_fingerprint: str | None = None  # fingerprint of the ML model being promoted
    cache: dict[str, object] = field(default_factory=dict)  # parsed evidence, per check


def cached(ctx: CheckContext, key: str, build: Callable[[], object]) -> object:
    if key not in ctx.cache:
        ctx.cache[key] = build()
    return ctx.cache[key]


def is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def blank(value: object) -> bool:
    return not (isinstance(value, str) and value.strip())


def is_empty(section: object) -> bool:
    return all(getattr(section, f.name) is None for f in fields(section))  # type: ignore[arg-type]


def quiet_ts(value: str | None) -> int | None:
    """Parsed time or ``None`` (the stage that owns the field reports parse errors)."""
    if blank(value):
        return None
    try:
        return iso_to_ms(value)  # type: ignore[arg-type]
    except (ValueError, OverflowError):
        return None


def parse_time(value: str | None, name: str, now: int) -> tuple[int | None, str | None]:
    """``(ms, problem sentence)``: missing, unparseable, or later than the check time."""
    if blank(value):
        return None, f"{name} is missing, so this stage is not complete."
    ts = quiet_ts(value)
    if ts is None:
        return None, f"{name} {value!r} is not an ISO-8601 UTC time."
    if ts > now:
        return ts, (
            f"{name} {ms_to_iso(ts)} is later than the check time {ms_to_iso(now)}, so this "
            "stage cannot be complete yet."
        )
    return ts, None


def _resolve(ctx: CheckContext, value: str) -> Path:
    p = Path(value.strip())
    return p if p.is_absolute() else ctx.base_dir / p


def _path(ctx: CheckContext, value: str | None, name: str) -> tuple[Path | None, str | None]:
    """``(resolved existing path or None, problem sentence)``; existence only, no hash."""
    if blank(value):
        return None, f"{name} is missing, so there is no evidence file to audit."
    p = _resolve(ctx, value)  # type: ignore[arg-type]
    if not p.is_file():
        return None, f"{name} {value!r} does not exist (looked for {p})."
    return p, None


def hash_bound(
    ctx: CheckContext, path_value: str | None, sha_value: str | None, path_name: str, sha_name: str
) -> tuple[Path | None, list[str]]:
    """``(verified path or None, problems)``: recorded, hash recorded, exists, hash matches."""
    if blank(path_value):
        return None, [f"{path_name} is missing, so there is no evidence file to audit."]
    if blank(sha_value):
        return None, [
            f"{sha_name} is missing, so {path_name} {path_value!r} is not bound to this record "
            "by its sha256."
        ]
    sha = sha_value.strip()  # type: ignore[union-attr]
    if not is_sha256_hex(sha):
        return None, [
            f"{sha_name} {sha[:SHA256_HEX_LEN]!r} is not a sha256 hex digest "
            f"({SHA256_HEX_LEN} hex characters)."
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
            f"{path_name} {path_value!r} has sha256 {actual[:FP_SHOWN]} but {sha_name} is "
            f"{sha[:FP_SHOWN].lower()} (first {FP_SHOWN} hex digits shown), so the file is "
            "not the evidence that was recorded."
        ]
    return p, []


def hash_bound_map(
    ctx: CheckContext, files: Mapping[str, str], name: str
) -> tuple[dict[str, Path], list[str]]:
    """Every ``{path: sha256}`` entry hash-bound: ``(verified paths, problems)``."""
    paths: dict[str, Path] = {}
    problems: list[str] = []
    for key, sha in sorted(files.items()):
        path, found = hash_bound(ctx, key, sha, f"{name} entry", f"{name}[{key!r}]")
        problems += found
        if path is not None:
            paths[key] = path
    return paths, problems


def _pos_int(value: object) -> bool:
    return is_int(value) and value > 0  # type: ignore[operator]


def _positive_count(value: object, name: str) -> str | None:
    if _pos_int(value):
        return None
    return f"{name} must be a positive trade count (got {value!r})."


def _shown(items: Sequence[str]) -> str:
    shown = ", ".join(items[:_MAX_IDS_SHOWN])
    extra = len(items) - _MAX_IDS_SHOWN
    return f"{shown} (+{extra} more)" if extra > 0 else shown


def ids_text(ids: Sequence[int]) -> str:
    return _shown([f"#{i}" for i in ids])


def _keys(keys: Sequence[tuple[str, int]]) -> str:
    return _shown([f"{window} #{i}" for window, i in keys])


def problems_of(*items: str | None) -> list[str]:
    return [p for p in items if p is not None]


def _num_close(typed: object, actual: float) -> bool:
    if isinstance(typed, bool) or not isinstance(typed, (int, float)):
        return False
    return abs(float(typed) - actual) <= AVG_R_TOL


# ---------------------------------------------------------------------------- identity
def record_problems(ctx: CheckContext) -> list[tuple[bool, str]]:
    """``(is_fingerprint_rule, reason)`` pairs: variant named, config and model fingerprints."""
    rec = ctx.record
    out: list[tuple[bool, str]] = []
    if blank(rec.variant):
        out.append((False, "variant is empty, so the record tracks no variant."))
    expected = config_fingerprint(ctx.cfg)
    if blank(rec.config_fingerprint):
        out.append(
            (True, "config_fingerprint is missing, so the tested config cannot be identified.")
        )
    elif rec.config_fingerprint != expected:
        out.append(
            (
                True,
                f"config_fingerprint {rec.config_fingerprint[:FP_SHOWN]} was tested but the "
                f"config being promoted has fingerprint {expected[:FP_SHOWN]} (first {FP_SHOWN} "
                "hex digits shown), so the promoted variant is not the one that was tested.",
            )
        )
    model_problem = model_fingerprint_problem(rec, ctx.model_fingerprint)
    if model_problem is not None:
        out.append((True, model_problem))
    return out


def model_fingerprint_problem(record: AdoptionRecord, supplied: str | None) -> str | None:
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
                f"a model fingerprint {supplied[:FP_SHOWN]!r} was supplied but the record has "
                "no model_fingerprint, so that ML model was never tested under this record."
            )
        return None
    if not is_sha256_hex(recorded):
        return (
            f"model_fingerprint {recorded[:SHA256_HEX_LEN]!r} is not a sha256 hex digest "
            f"({SHA256_HEX_LEN} hex characters); use null for a variant without an ML model."
        )
    if supplied is None:
        return (
            f"the record was tested with ML model {recorded[:FP_SHOWN]} but no current model "
            "fingerprint was supplied, so the model being promoted cannot be verified."
        )
    if supplied != recorded:
        return (
            f"model_fingerprint {recorded[:FP_SHOWN]} was tested but the ML model being "
            f"promoted has fingerprint {supplied[:FP_SHOWN]!r} (first {FP_SHOWN} characters "
            "shown), so the promoted model is not the one that was tested."
        )
    return None


# ---------------------------------------------------------------------------- provenance
def _provenance_value_problem(provenance: str | None) -> str | None:
    if provenance == PROVENANCE_REAL:
        return None
    if blank(provenance):
        return (
            "provenance is missing, so nothing shows the walk-forward ran on real market data "
            f"(record {PROVENANCE_REAL!r} with the data file hashes and the manifest)."
        )
    if provenance.strip().lower().startswith(SYNTHETIC_PROVENANCE_PREFIX):  # type: ignore[union-attr]
        return (
            f"provenance is {provenance!r}: a synthetic world only verifies the harness and is "
            f"never evidence about real markets, so it may not go past {WALK_FORWARD}."
        )
    if provenance == PROVENANCE_UNVERIFIED:
        return (
            f"provenance is {provenance!r}: an unverified CSV, not an exchange download (no "
            "manifest.json lists every file with a matching sha256), so it may not go past "
            f"{WALK_FORWARD}."
        )
    return (
        f"provenance is {provenance!r}, but only {PROVENANCE_REAL!r} (exchange data verified by "
        f"its manifest) may go past {WALK_FORWARD}."
    )


def data_paths(ctx: CheckContext) -> tuple[dict[str, Path], list[str]]:
    """The hash-verified ``data_files`` (record key -> path) and their problems (cached)."""
    files = ctx.record.data_files or {}
    return cached(ctx, "data_paths", lambda: hash_bound_map(ctx, files, "data_files"))  # type: ignore[return-value]


def _manifest_problems(ctx: CheckContext, paths: Mapping[str, Path]) -> list[str]:
    """D2: a ``real`` record's data files must be exactly what their manifest verifies."""
    rec = ctx.record
    unverified = "so the data files are an unverified CSV, not an exchange download"
    if rec.manifest is None:
        return [
            f"provenance is {PROVENANCE_REAL!r} but no manifest is recorded, {unverified} "
            "(record the data directory's manifest.json written by fetch_data, CONTRACT v4 D2)."
        ]
    path = _resolve(ctx, rec.manifest.path or "")  # hash-bound: checked by the caller
    hashes = rec.data_files or {}
    return [
        f"the manifest does not verify the data files ({p}), {unverified}."
        for p in manifest_problems(path, paths, hashes)
    ]


def provenance_problems(ctx: CheckContext, require_real: bool) -> list[str]:
    """C3 data binding + D2 manifest: recorded files hash-bound; later stages need real data."""
    rec = ctx.record
    out: list[str] = []
    if require_real:
        out += problems_of(_provenance_value_problem(rec.provenance))
        if not rec.data_files:
            out.append(
                "data_files is missing or empty, so the market data behind the backtest is not "
                "bound to this record by sha256."
            )
    paths, file_problems = data_paths(ctx)
    out += file_problems
    ref_problems: dict[str, list[str]] = {}
    for name in ("events_file", "manifest"):
        ref = getattr(rec, name)
        if ref is not None:
            found = hash_bound(ctx, ref.path, ref.sha256, f"{name}.path", f"{name}.sha256")[1]
            ref_problems[name] = found
            out += found
    verifiable = rec.provenance == PROVENANCE_REAL and rec.data_files and not file_problems
    # A recorded manifest whose hash does not match is already reported just above.
    if require_real and verifiable and not ref_problems.get("manifest"):
        out += _manifest_problems(ctx, paths)
    return out


# ---------------------------------------------------------------------------- D1 test-only
def testonly_problems(ctx: CheckContext) -> list[str]:
    """D1: regime-off and stop-fill-stress configs never go past WALK_FORWARD."""
    cfg = ctx.cfg
    if not cfg.is_test_only:
        return []
    why = []
    if not cfg.regime_filter:
        why.append("the R4 regime filter is off")
    if cfg.stop_fill_wick_k > 0:
        why.append(f"stop_fill_wick_k is {cfg.stop_fill_wick_k:g} (a stop-fill stress model)")
    return [
        f"the config is test-only ({' and '.join(why)}): such research configs may be "
        f"backtested and walk-forward tested but never go past {WALK_FORWARD} (CONTRACT v4 D1)."
    ]


# ---------------------------------------------------------------------------- D3 holdout
def _own_look_problem(ctx: CheckContext, split: int) -> str:
    rec = ctx.record
    model = "none" if rec.model_fingerprint is None else rec.model_fingerprint[:FP_SHOWN]
    data = ", the recorded data file hashes" if rec.data_files else ""
    return (
        f"no line of the holdout ledger records this record's TEST look (config "
        f"{rec.config_fingerprint[:FP_SHOWN]}, model {model}, split {ms_to_iso(split)}{data}), "
        "so it is a context variant (not a pre-registered adoptable candidate) or its TEST "
        "look was never logged (CONTRACT v4 D3)."
    )


def holdout_problems(ctx: CheckContext) -> list[str]:
    """D3: the record's TEST window may have been looked at by at most m candidates."""
    rec = ctx.record
    out: list[str] = []
    if ctx.cfg.is_test_only:
        out.append(
            "the config is test-only, a context variant that is never adoptable whatever the "
            "holdout ledger holds (CONTRACT v4 D3)."
        )
    path, problem = _path(ctx, rec.ledger_path, "ledger_path")
    if path is None:
        return [*out, f"{problem[:-1]} (the holdout ledger of TEST looks, CONTRACT v4 D3)."]  # type: ignore[index]
    try:
        looks = read_ledger(path)
    except (OSError, ValueError) as exc:
        return [*out, f"the holdout ledger {rec.ledger_path!r} could not be read ({exc})."]
    split = quiet_ts(rec.walk_forward.split_utc)
    if split is None:
        return out  # the WALK_FORWARD stage (always checked from HUMAN_REVIEW on) blocks
    hashes = list(rec.data_files.values()) if rec.data_files else None
    own = own_looks(looks, rec.config_fingerprint, rec.model_fingerprint, split, hashes)
    if not own:
        return [*out, _own_look_problem(ctx, split)]
    windows = sorted({(e.pair, e.test_start_ts, e.test_end_ts) for look in own for e in look.pairs})
    for pair, start, end in windows:
        keys = distinct_looks(looks, pair, start, end)
        if len(keys) <= metrics.M_CANDIDATES:
            continue
        latest = max(
            e.test_end_ts
            for look in looks
            for e in look.pairs
            if e.pair == pair and e.test_start_ts < end and start < e.test_end_ts
        )
        out.append(
            f"{pair}: the holdout ledger holds {len(keys)} distinct (config, model) TEST looks "
            f"overlapping this record's TEST window {ms_to_iso(start)} to {ms_to_iso(end)}, "
            f"more than m = {metrics.M_CANDIDATES} (CONTRACT v4 D3), so that TEST data is spent "
            f"and a revised variant needs a TEST window starting at or after {ms_to_iso(latest)}."
        )
    return out


# ---------------------------------------------------------------------------- backtest
def backtest_problems(ctx: CheckContext) -> list[str]:
    bt = ctx.record.backtest
    _p, path_problems = hash_bound(
        ctx, bt.report_path, bt.report_sha256, "backtest.report_path", "backtest.report_sha256"
    )
    _ts, time_problem = parse_time(bt.completed_utc, "backtest.completed_utc", ctx.now)
    return path_problems + problems_of(time_problem)


# ---------------------------------------------------------------------------- walk-forward
def _label_problem(label: str | None) -> str | None:
    if label == REQUIRED_WF_LABEL:
        return None
    if blank(label):
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
            "walk_forward.label_params is missing: record the closed D1 schema {seed, n_boot, "
            "m, alpha, max_dd_pct, train_dd_p95_pct, mtm_max_dd_pct}."
        )
    else:
        out += [f"{p} (CONTRACT v4 D1)." for p in label_params_problems(wf.label_params)]
    return out


def _robust_sample_problem(wf: WalkForwardStage) -> str | None:
    if wf.label != REQUIRED_WF_LABEL:
        return None
    counts = (wf.train_n, wf.test_n)
    if all(is_int(n) and n >= ROBUST_MIN_N for n in counts):  # type: ignore[operator]
        return None
    return (
        f"walk_forward.label is {REQUIRED_WF_LABEL} with train_n {wf.train_n!r} and test_n "
        f"{wf.test_n!r}, but {REQUIRED_WF_LABEL} needs at least {ROBUST_MIN_N} trades in each "
        "window (CONTRACT v3 C3), whatever min_train and min_test say."
    )


Journals = tuple[list[Trade], list[Trade]]


def _load_wf_journals(ctx: CheckContext) -> tuple[Journals | None, list[str]]:
    wf = ctx.record.walk_forward
    problems: list[str] = []
    loaded: list[list[Trade]] = []
    specs = (
        ("train", wf.train_journal_path, wf.train_journal_sha256),
        ("test", wf.test_journal_path, wf.test_journal_sha256),
    )
    for window, path_value, sha_value in specs:
        name = f"walk_forward.{window}_journal"
        path, found = hash_bound(ctx, path_value, sha_value, f"{name}_path", f"{name}_sha256")
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


def wf_journals(ctx: CheckContext) -> tuple[Journals | None, list[str]]:
    """The hash-verified (TRAIN, TEST) journals, parsed once per check, or None + problems."""
    return cached(ctx, "wf_journals", lambda: _load_wf_journals(ctx))  # type: ignore[return-value]


def _window_problems(window: str, trades: Sequence[Trade], ctx: CheckContext) -> list[str]:
    rec = ctx.record
    split = quiet_ts(rec.walk_forward.split_utc)
    out: list[str] = []
    unclosed = [t.trade_id for t in trades if t.exit_ts is None or t.r_multiple is None]
    if unclosed:
        out.append(
            f"the {window} journal has trades {ids_text(unclosed)} without an exit or R, but a "
            "walk-forward journal holds closed trades only."
        )
    dup = sorted(i for i, n in Counter(t.trade_id for t in trades).items() if n > 1)
    if dup:
        out.append(
            f"the {window} journal repeats trade ids {ids_text(dup)}, so its rows cannot be "
            "matched to the review pack."
        )
    foreign = [t.trade_id for t in trades if t.variant != rec.variant]
    if foreign:
        out.append(
            f"{window} journal trades {ids_text(foreign)} are not of variant {rec.variant!r}, so "
            "the journal is not the evidence of the variant being promoted."
        )
    if split is not None:
        wrong = [t.trade_id for t in trades if (t.signal_ts < split) != (window == WINDOW_TRAIN)]
        if wrong:
            out.append(
                f"{window} journal trades {ids_text(wrong)} have signals on the wrong side of "
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


def _c5_mismatch(name: str, recorded: float, actual: float | None, source: str) -> str | None:
    if actual is None:
        return f"walk_forward.label_params.{name} cannot be recomputed ({source} is empty)."
    if abs(recorded - actual) <= C5_TOL:
        return None
    return (
        f"walk_forward.label_params.{name} is {recorded!r} but {source} gives {actual!r} "
        f"(tolerance {C5_TOL:g}), so the recorded C5 drawdown input does not match its evidence."
    )


def _mtm_problem(ctx: CheckContext, test: Sequence[Trade], p: LabelParams) -> str | None:
    """D1: ``mtm_max_dd_pct`` recomputed from the hash-bound candle files (real provenance)."""
    rec = ctx.record
    paths, file_problems = data_paths(ctx)
    if rec.provenance != PROVENANCE_REAL or not paths or file_problems:
        return None  # not real, or ADOPT_provenance already reports the data files
    try:
        candles = load_candle_files(paths.values())
    except (OSError, ValueError) as exc:
        return f"the TEST mark-to-market drawdown cannot be recomputed from data_files ({exc})."
    missing = sorted({t.pair for t in test} - set(candles))
    if missing:
        return (
            f"the TEST mark-to-market drawdown cannot be recomputed: data_files holds no candle "
            f"file for {', '.join(missing)}."
        )
    source = "metrics.mtm_max_dd_pct of the TEST journal on the hash-bound candle files"
    actual = recompute_test_mtm(test, candles, ctx.cfg)
    return _c5_mismatch("mtm_max_dd_pct", p.mtm_max_dd_pct, actual, source)


def _recompute_problems(ctx: CheckContext, train: list[Trade], test: list[Trade]) -> list[str]:
    wf = ctx.record.walk_forward
    if not (_pos_int(wf.min_train) and _pos_int(wf.min_test)) or wf.label_params is None:
        return []  # _wf_param_problems already blocks; nothing well-defined to recompute
    try:
        params = parse_label_params(wf.label_params)
        verdict = recompute_walk_forward(
            train,
            test,
            ctx.cfg,
            wf.min_train,
            wf.min_test,
            wf.label_params,  # type: ignore[arg-type]
        )
    except (TypeError, ValueError):
        return []  # the schema problems are reported by _wf_param_problems
    out = _verdict_mismatches(wf, verdict)
    source = (
        f"metrics.train_dd_quantile of the TRAIN journal at the TEST length {verdict.test.n} "
        f"(seed {params.seed}, n_boot {params.n_boot})"
    )
    p95 = recompute_train_dd_p95(train, verdict.test.n, params)
    out += problems_of(
        _c5_mismatch("train_dd_p95_pct", params.train_dd_p95_pct, p95, source),
        _mtm_problem(ctx, test, params),
    )
    return out


def walk_forward_problems(ctx: CheckContext) -> list[str]:
    wf = ctx.record.walk_forward
    _ts, split_problem = parse_time(wf.split_utc, "walk_forward.split_utc", ctx.now)
    _p, path_problem = _path(ctx, wf.report_path, "walk_forward.report_path")
    out = problems_of(
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
    journals, journal_problems = wf_journals(ctx)
    out += journal_problems
    if journals is not None:
        train, test = journals
        out += _window_problems(WINDOW_TRAIN, train, ctx)
        out += _window_problems(WINDOW_TEST, test, ctx)
        out += _recompute_problems(ctx, train, test)
    return out


# ---------------------------------------------------------------------------- human review
def _coverage_problems(ctx: CheckContext) -> list[str]:
    hr, wf = ctx.record.human_review, ctx.record.walk_forward
    total, reviewed = hr.trades_total, hr.trades_reviewed
    problem = _positive_count(total, "human_review.trades_total")
    if problem is not None:
        return [problem]
    out: list[str] = []
    if not is_int(reviewed):
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
    blank_rows = [r.key for r in rows if r.reviewer_ok not in (REVIEW_OK_YES, REVIEW_OK_NO)]
    if blank_rows:
        out.append(
            f"{len(blank_rows)} review rows ({_keys(blank_rows)}) have no reviewer_ok verdict, "
            f"and every trade needs an explicit {REVIEW_OK_YES} or {REVIEW_OK_NO}."
        )
    rejected = [r.key for r in rows if r.reviewer_ok == REVIEW_OK_NO]
    if rejected:
        out.append(
            f"the reviewer rejected {len(rejected)} trades ({_keys(rejected)}) with reviewer_ok "
            f"{REVIEW_OK_NO}, and by policy {REVIEW_REJECTION_POLICY}."
        )
    return out


def _review_pack_problems(ctx: CheckContext) -> list[str]:
    hr, wf = ctx.record.human_review, ctx.record.walk_forward
    path, problems = hash_bound(
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
    journals, _unused = wf_journals(ctx)
    if journals is not None:
        out += _review_key_problems(rows, *journals)
    return out + _review_verdict_problems(rows)


def human_review_problems(ctx: CheckContext) -> list[str]:
    hr = ctx.record.human_review
    reviewer_problem = None
    if blank(hr.reviewer):
        reviewer_problem = "human_review.reviewer is missing: a named person must sign off."
    date, date_problem = parse_time(hr.date_utc, "human_review.date_utc", ctx.now)
    order_problem = None
    backtest_done = quiet_ts(ctx.record.backtest.completed_utc)
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
    out = problems_of(reviewer_problem, date_problem, order_problem)
    out += _coverage_problems(ctx)
    out += problems_of(approved_problem)
    return out + _review_pack_problems(ctx)


# ---------------------------------------------------------------------------- live
def live_problems(ctx: CheckContext) -> list[str]:
    live = ctx.record.live
    if live.enabled_utc is None:
        return []
    enabled, problem = parse_time(live.enabled_utc, "live.enabled_utc", ctx.now)
    out = problems_of(problem)
    testnet_end = quiet_ts(ctx.record.testnet.end_utc)
    if enabled is not None and testnet_end is not None and enabled < testnet_end:
        out.append(
            f"live.enabled_utc {ms_to_iso(enabled)} is before the testnet run ended at "
            f"{ms_to_iso(testnet_end)}, so live trading started before testnet finished."
        )
    return out
