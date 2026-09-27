"""Adoption path enforcement: no variant reaches real money by skipping a step.

The ONLY way from research to live trading is, strictly in this order::

    BACKTEST -> WALK_FORWARD -> HUMAN_REVIEW -> TESTNET (>= 14 days, Binance testnet) -> LIVE

A good backtest number never shortcuts the path. :func:`check_promotion` returns every
blocking :class:`~.models.Decision` for promoting a record to a target stage (empty list =
allowed): every EARLIER stage must be complete and consistent with its evidence files, and
the config being promoted must be exactly the config that was tested (sha256
:func:`config_fingerprint`). A live bot must call :func:`require_stage` with ``"LIVE"`` at
startup and refuse to trade if it raises.

The code is split along its seams, and every public name is re-exported here:
:mod:`.adoption_record` (stages, rule ids, fingerprints, the record JSON schema and its IO),
:mod:`.adoption_evidence` (pure recomputation: pinned label schema, C5 drawdown inputs,
manifest, holdout ledger, decisions log, testnet replay), :mod:`.adoption_stages` (the
per-stage checks below) and :mod:`.adoption_testnet` (the TESTNET stage).

Evidence binding (CONTRACT.md v3 C3). Relative paths in the record are resolved against
``base_dir`` (the CLI passes the record file's directory; ``None`` means the current working
directory, so the files are ALWAYS checked); absolute paths are used as they are. A
"hash-bound" file must be recorded with its ``*_sha256`` field (or, for ``{path: sha256}``
maps, its value), exist, and have exactly that sha256 (:func:`sha256_file`;
``python -m research.trendbot.adoption hash FILE`` prints it).

What IS verified, by target stage (each item is a separate blocking reason, rule id in
brackets; a stage with nothing recorded yields one "no recorded result" reason instead):

- Always: ``variant`` named [ADOPT_record]; ``config_fingerprint`` equals
  ``config_fingerprint(cfg)`` [ADOPT_fingerprint]; ML model (CONTRACT.md v2 A3,
  [ADOPT_fingerprint]): a recorded ``model_fingerprint`` (``MLFilter.fingerprint()``) must be
  a sha256 hex digest equal to the one the caller supplies, a variant whose id contains
  ``"+ml"`` must have one, and none may be supplied for a record without one.
- Target WALK_FORWARD or later (the BACKTEST stage is checked): [ADOPT_backtest]
  ``report_path`` hash-bound by ``report_sha256``; ``completed_utc`` a valid time not after
  the check time. [ADOPT_provenance] every ``data_files`` entry (``{path: sha256}``) and, if
  recorded, the ``events_file`` and the ``manifest`` (``{path, sha256}``) are hash-bound.
- Target HUMAN_REVIEW or later:

  - [ADOPT_provenance] ``provenance`` exactly ``"real"`` (``"synthetic:<world>:<seed>"``,
    ``"unverified-csv"``, anything else and a missing value are blocked) and at least one
    data file hash recorded. For a ``"real"`` record whose data files all hash-verify
    (CONTRACT.md v4 D2): a ``manifest`` must be recorded; it must be the ``manifest.json`` of
    the directory holding EVERY data file; ``data.verify_manifest(that directory, the
    manifest's symbol of each data file, "4h")`` must return ``"real"`` (each file listed
    with a matching sha256, symbol, timeframe, rows and first/last ts); and each
    ``data_files`` hash must equal the sha256 that verification measured. Otherwise the data
    is "an unverified CSV, not an exchange download".
  - [ADOPT_test_only] ``cfg.is_test_only`` (R4 regime filter off, or ``stop_fill_wick_k >
    0``) never goes past WALK_FORWARD (D1).
  - [ADOPT_holdout] (D3) a test-only config is a context variant and is blocked whatever
    the ledger holds; ``ledger_path`` must exist and parse (``adoption_evidence.read_ledger``:
    one JSON object per line). The record's own TEST look must be in it (same
    ``config_fingerprint``, ``model_fingerprint`` and ``split_utc`` and, when data files are
    recorded, every pair's ``file_sha256`` one of their hashes), else it is a context variant
    (or was never logged). For every pair window ``[test_start_ts, test_end_ts)`` of those
    lines, the DISTINCT ``(config_fingerprint, model_fingerprint)`` over all ledger lines
    whose TEST window on that pair overlaps it (half-open, so a window starting at or after
    an earlier ``test_end_ts`` is fresh) may number at most ``metrics.M_CANDIDATES``;
    identical re-runs add nothing. (If ``split_utc`` is invalid, the WALK_FORWARD stage
    blocks and the own-look match is skipped.)
  - [ADOPT_walk_forward] (the WALK_FORWARD stage) ``label`` exactly ``"ROBUST"``; ``dd_ok``
    exactly true; ``split_utc`` valid; ``train_n``/``test_n`` positive and, for ROBUST, both
    >= 30 whatever ``min_train``/``min_test`` say; ``test_avg_r`` > 0 (expectancy, never win
    rate); ``train_avg_r`` recorded; ``min_train``/``min_test`` positive integers;
    ``label_params`` exactly the closed D1 key set ``{seed, n_boot, m, alpha, max_dd_pct,
    train_dd_p95_pct, mtm_max_dd_pct}`` with valid types and ranges (integer seed, positive
    integer n_boot and m, alpha in (0, 1), positive max_dd_pct, C5 inputs >= 0), and pinned:
    ``m == metrics.M_CANDIDATES``, ``alpha == metrics.ALPHA``, ``n_boot >= metrics.N_BOOT``
    and ``seed == walkforward.SUMMARY_SEED``; ``report_path`` exists; both journals hash-bound
    (``train_journal_path``/``_sha256``, ``test_journal_path``/``_sha256``) and re-parsed with
    ``journal.read_journal``: closed trades only, unique trade ids, every trade of the
    record's variant, TRAIN signals before ``split_utc`` and TEST signals at or after it. Then
    :func:`recompute_walk_forward` (explicit keyword arguments, no signature introspection:
    ``metrics.summarize`` at ``cfg.starting_capital`` with the recorded seed, n_boot, m,
    alpha; ``metrics.label`` with the recorded ``min_train``/``min_test``, m, alpha;
    ``metrics.dd_check`` with the recorded ``max_dd_pct`` (capped by
    ``metrics.DD_CAP_PCT``) and C5 inputs): the typed ``label``, ``train_n``, ``test_n``,
    ``train_avg_r``, ``test_avg_r`` (absolute 1e-9) and ``dd_ok`` must equal it. The C5 inputs
    are recomputed (a difference above 1e-9 blocks): ``train_dd_p95_pct`` as
    ``metrics.train_dd_quantile(TRAIN journal, TEST n, n_boot, seed)`` with the record's
    (pinned) n_boot and seed, and, for ``"real"`` provenance whose data files hash-verify,
    ``mtm_max_dd_pct`` as
    ``metrics.mtm_max_dd_pct(TEST journal, candles of every data file, cfg.starting_capital,
    cfg.fee_rate)`` (files loaded with ``data.load_candles_csv(..., "4h")`` and named like
    ``data.pair_filename(pair, "4h")``; a TEST pair without a file blocks).
- Target TESTNET or later (the HUMAN_REVIEW stage is checked): [ADOPT_human_review] a named
  ``reviewer``; ``date_utc`` valid and not before the backtest completed;
  ``trades_reviewed == trades_total == train_n + test_n``; ``approved`` exactly true;
  ``review_path`` (the pack's ``trades_review.csv``) hash-bound by ``review_sha256`` and
  parsed with ``review_sheet.load_review``; its row count equals ``train_n + test_n``; its
  ``(window, trade_id)`` key set equals the TRAIN journal's ``TRAIN`` keys plus the TEST
  journal's ``TEST`` keys, and each row's pair and signal time equal its journal trade's;
  every ``reviewer_ok`` is ``Y`` or ``N`` (a blank blocks). Any ``N`` blocks, and the
  documented policy is: the variant is revised and restarts the adoption path at BACKTEST
  under a new record (a changed config has a new fingerprint) on TEST data it has never
  seen (D3), so a record with a rejected trade never passes HUMAN_REVIEW.
- Target LIVE (the TESTNET stage is checked, CONTRACT.md v4 D6): [ADOPT_testnet]
  ``exchange == "binance-testnet"``; ``end_utc - start_utc >= 14 days``; ``end_utc`` not
  after the check time; ``start_utc`` not before the review sign-off; ``trades >= 1``;
  ``rule_violations`` exactly 0; ``starting_equity`` a positive number; hash-bound and
  parsed: ``journal_path``/``journal_sha256`` (``journal.read_journal``, REAL fill times),
  ``decisions_path``/``decisions_sha256`` (``adoption_evidence.read_decisions_log``: pair,
  signal_ts, allowed, rule, reason of every evaluated signal), every ``candles`` entry
  (``{path: sha256}``, non-empty; 4H files named like ``data.pair_filename``) and, if
  recorded, ``events_path``/``events_sha256`` (``news.load_events``). Then: the journal's
  trade count equals ``trades``; every entry lies inside the window; every trade is of the
  record's variant; no trade carries a mandatory-rule flag of ``review_sheet.auto_flags``
  (:data:`RULE_VIOLATION_FLAGS`); ``invariants.live_journal_violations(journal, candles,
  cfg, events, starting_equity)`` is empty (exit offset 0; per trade R1-R4 and R8 re-derived
  from the candles, fill timing, planned geometry, R7 on the realized equity, exits never
  paused; R6 and R9 across trades; R5 only with ``events_path``); and the
  :func:`~.adoption_evidence.replay_testnet` replay of ``gatekeeper.LiveSession`` (starting
  at ``starting_equity``, exit offset 0) over the candles whose close lies in
  ``[start_utc, end_utc]``, with the journal's actual fills and exits injected, accepts
  every injected fill and exit and yields an allowed ``(pair, signal_ts)`` set equal to the
  journal's entries AND the decisions log's allowed rows (every difference listed).
  [ADOPT_live] ``live.enabled_utc``, if recorded, is a valid time not before the testnet run
  ended.

What is NOT verified (:func:`unverified_notes` lists the items relevant to a target; the
CLI prints them under PASS and BLOCKED):

- That the journals were produced by running the backtester on the hash-bound data files:
  the hashes bind the files to the record, they do not prove how the files were made
  (re-run ``run_research`` / ``python -m research.trendbot.invariants`` on the data to check).
- That the data came from an exchange (D2): an offline check cannot authenticate an
  exchange download; the manifest only makes a laundered CSV a deliberate act (the
  manifest has to be rewritten too) instead of an accident.
- The holdout ledger is append-only and therefore not hash-bound (D3): a deleted or edited
  line, or a run that wrote its looks to another ledger, cannot be detected.
- For non-``"real"`` provenance the mark-to-market drawdown is not recomputed (such records
  are blocked by ADOPT_provenance anyway).
- ``walk_forward.report_path`` carries no hash: the report is only required to exist.
- That the reviewer actually inspected each trade: the pack only proves every row got an
  explicit ``Y``.
- Testnet: that the account really was a testnet (only the typed ``exchange`` is checked)
  and that the candles are the exchange's (they are hash-bound, not authenticated); the
  denied rows of the decisions log beyond the allowed-set comparison; an allowed decision
  the log records as refused at the fill (``X_capital`` / ``R8_structure_stop``, no journal
  entry) is replayed as that refusal, because a refused order's fill price is not
  journaled; fill and exit prices are the journal's own (injected into the replay and
  audited for arithmetic, not checked against the candles' range); for an ML variant the
  log's ``L_ml_filter`` vetoes are injected (the model is not re-run; its identity is bound
  by ``model_fingerprint``); without ``testnet.events_path``, R5. Binance spot testnet
  prices and liquidity are not mainnet, so the stage verifies execution and rule
  compliance, not edge; at the measured baseline rate of about 1 trade per 14 days a 14-day
  window is expected to hold about 1 trade.
- Hand-typed times beyond their format, order and not lying in the future.

A live bot must also round quantities DOWN and take-profit prices UP to the exchange tick
so R7 and the 2:1 minimum hold after rounding (not checkable from a record).

The record JSON schema is documented in :mod:`.adoption_record`.

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
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from types import MappingProxyType

from .adoption_evidence import (
    BASELINE_TRADE_EVERY_DAYS,
    C5_KEYS,
    C5_TOL,
    DECISIONS_COLUMNS,
    LABEL_PARAM_KEYS,
    LabelParams,
    LedgerLook,
    LedgerPair,
    TestnetReplay,
    WalkForwardVerdict,
    distinct_looks,
    expected_testnet_trades,
    label_params_pin_problems,
    label_params_problems,
    label_params_schema_problems,
    load_candle_files,
    manifest_problems,
    own_looks,
    pair_of_candle_file,
    parse_label_params,
    read_decisions_log,
    read_ledger,
    recompute_test_mtm,
    recompute_train_dd_p95,
    recompute_walk_forward,
    replay_differences,
    replay_testnet,
    write_decisions_log,
)
from .adoption_record import (
    ADOPT_BACKTEST,
    ADOPT_FINGERPRINT,
    ADOPT_HOLDOUT,
    ADOPT_HUMAN_REVIEW,
    ADOPT_LIVE,
    ADOPT_PROVENANCE,
    ADOPT_RECORD,
    ADOPT_TEST_ONLY,
    ADOPT_TESTNET,
    ADOPT_WALK_FORWARD,
    ADOPTION_RULE_IDS,
    BACKTEST,
    HUMAN_REVIEW,
    LIVE,
    ML_VARIANT_MARKER,
    PROVENANCE_REAL,
    PROVENANCE_UNVERIFIED,
    REQUIRED_WF_LABEL,
    SHA256_HEX_LEN,
    STAGE_RULES,
    STAGE_SECTIONS,
    STAGES,
    SYNTHETIC_PROVENANCE_PREFIX,
    TESTNET,
    TESTNET_EXCHANGE,
    TESTNET_MIN_DAYS,
    WALK_FORWARD,
    AdoptionRecord,
    BacktestStage,
    FileRef,
    HumanReviewStage,
    LiveStage,
    TestnetStage,
    WalkForwardStage,
    canonical_config_json,
    config_fingerprint,
    config_from_overrides,
    empty_record,
    is_ml_variant,
    is_sha256_hex,
    load_config,
    load_record,
    record_from_dict,
    save_record,
    sha256_file,
    stage_index,
)
from .adoption_stages import (
    FP_SHOWN,
    REVIEW_REJECTION_POLICY,
    ROBUST_MIN_N,
    CheckContext,
    backtest_problems,
    blank,
    holdout_problems,
    human_review_problems,
    is_empty,
    live_problems,
    provenance_problems,
    record_problems,
    testonly_problems,
    walk_forward_problems,
)
from .adoption_testnet import (
    NOT_MAINNET,
    R5_NOT_VERIFIABLE,
    RULE_VIOLATION_FLAGS,
    expectation_clause,
    testnet_problems,
)
from .config import StrategyConfig
from .journal import iso_to_ms, ms_to_iso
from .metrics import DD_CAP_PCT
from .models import Decision


__all__ = [
    "ADOPTION_RULE_IDS",
    "ADOPT_BACKTEST",
    "ADOPT_FINGERPRINT",
    "ADOPT_HOLDOUT",
    "ADOPT_HUMAN_REVIEW",
    "ADOPT_LIVE",
    "ADOPT_PROVENANCE",
    "ADOPT_RECORD",
    "ADOPT_TESTNET",
    "ADOPT_TEST_ONLY",
    "ADOPT_WALK_FORWARD",
    "BACKTEST",
    "BASELINE_TRADE_EVERY_DAYS",
    "C5_KEYS",
    "C5_TOL",
    "DD_CAP_PCT",
    "DECISIONS_COLUMNS",
    "HUMAN_REVIEW",
    "LABEL_PARAM_KEYS",
    "LIVE",
    "ML_VARIANT_MARKER",
    "NOT_MAINNET",
    "PROVENANCE_REAL",
    "PROVENANCE_UNVERIFIED",
    "R5_NOT_VERIFIABLE",
    "REQUIRED_WF_LABEL",
    "REVIEW_REJECTION_POLICY",
    "ROBUST_MIN_N",
    "RULE_VIOLATION_FLAGS",
    "STAGES",
    "STAGE_RULES",
    "STAGE_SECTIONS",
    "SYNTHETIC_PROVENANCE_PREFIX",
    "TESTNET",
    "TESTNET_EXCHANGE",
    "TESTNET_MIN_DAYS",
    "WALK_FORWARD",
    "AdoptionBlocked",
    "AdoptionRecord",
    "BacktestStage",
    "FileRef",
    "HumanReviewStage",
    "LabelParams",
    "LedgerLook",
    "LedgerPair",
    "LiveStage",
    "TestnetReplay",
    "TestnetStage",
    "WalkForwardStage",
    "WalkForwardVerdict",
    "canonical_config_json",
    "check_promotion",
    "config_fingerprint",
    "config_from_overrides",
    "distinct_looks",
    "empty_record",
    "expected_testnet_trades",
    "is_ml_variant",
    "is_sha256_hex",
    "label_params_pin_problems",
    "label_params_problems",
    "label_params_schema_problems",
    "load_candle_files",
    "load_config",
    "load_record",
    "main",
    "manifest_problems",
    "own_looks",
    "pair_of_candle_file",
    "parse_label_params",
    "read_decisions_log",
    "read_ledger",
    "recompute_test_mtm",
    "recompute_train_dd_p95",
    "recompute_walk_forward",
    "record_from_dict",
    "replay_differences",
    "replay_testnet",
    "require_stage",
    "save_record",
    "sha256_file",
    "unverified_notes",
    "write_decisions_log",
]


STAGE_CHECKS: Mapping[str, Callable[[CheckContext], list[str]]] = MappingProxyType(
    {
        BACKTEST: backtest_problems,
        WALK_FORWARD: walk_forward_problems,
        HUMAN_REVIEW: human_review_problems,
        TESTNET: testnet_problems,
    }
)


# ---------------------------------------------------------------------------- public checks
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
    test-only, holdout, stages, live. Raises ``ValueError`` for an unknown ``target_stage``.
    """
    target = stage_index(target_stage)
    root = Path.cwd() if base_dir is None else Path(base_dir)
    ctx = CheckContext(record, cfg, now_utc_ms, root, model_fingerprint)
    out = [
        Decision(False, ADOPT_FINGERPRINT if fp_rule else ADOPT_RECORD, reason)
        for fp_rule, reason in record_problems(ctx)
    ]
    if target >= STAGES.index(WALK_FORWARD):
        require_real = target >= STAGES.index(HUMAN_REVIEW)
        problems = provenance_problems(ctx, require_real)
        out.extend(Decision(False, ADOPT_PROVENANCE, r) for r in problems)
    if target >= STAGES.index(HUMAN_REVIEW):
        out.extend(Decision(False, ADOPT_TEST_ONLY, r) for r in testonly_problems(ctx))
        out.extend(Decision(False, ADOPT_HOLDOUT, r) for r in holdout_problems(ctx))
    for stage in STAGES[:target]:
        rule = STAGE_RULES[stage]
        if is_empty(getattr(record, STAGE_SECTIONS[stage])):
            reason = (
                f"stage {stage} has no recorded result, and {target_stage} may only follow a "
                f"completed {stage} (no step can be skipped)."
            )
            out.append(Decision(False, rule, reason))
            continue
        out.extend(Decision(False, rule, r) for r in STAGE_CHECKS[stage](ctx))
    if target_stage == LIVE:
        out.extend(Decision(False, ADOPT_LIVE, r) for r in live_problems(ctx))
    return out


def unverified_notes(record: AdoptionRecord, target_stage: str) -> list[str]:
    """One sentence per thing :func:`check_promotion` does NOT verify for ``target_stage``.

    Read these before trusting a PASS (the CLI prints them). Raises ``ValueError`` for an
    unknown stage.
    """
    target = stage_index(target_stage)
    out: list[str] = []
    if target >= STAGES.index(HUMAN_REVIEW):
        out += [
            "The sha256 hashes bind the data files, journals and report to this record, but "
            "they do not prove the journals were produced from those data files (re-run "
            "run_research or the invariants CLI on the data to check that).",
            "An offline check cannot authenticate an exchange download: the manifest only "
            "shows the data files are byte-identical to what it lists, so it makes a laundered "
            "CSV a deliberate act (the manifest has to be rewritten too) instead of an "
            "accident.",
            "The holdout ledger is append-only and not hash-bound, so a deleted or edited "
            "ledger line, or TEST looks written to another ledger, cannot be detected.",
        ]
        if record.provenance != PROVENANCE_REAL:
            out.append(
                "The mark-to-market drawdown (label_params.mtm_max_dd_pct) is recomputed from "
                "the candle files only for 'real' provenance, so it is not recomputed here."
            )
    if target >= STAGES.index(TESTNET):
        out.append(
            "The review pack proves every trade got an explicit Y, not that the reviewer "
            "inspected each trade."
        )
    if target_stage == LIVE:
        out += _testnet_notes(record)
    return out


def _testnet_notes(record: AdoptionRecord) -> list[str]:
    tn = record.testnet
    out: list[str] = []
    if blank(tn.events_path):
        out.append(f"{R5_NOT_VERIFIABLE[0].upper()}{R5_NOT_VERIFIABLE[1:]}.")
    clause = expectation_clause(record)
    out.append(f"{clause[0].upper()}{clause[1:]}.")
    out.append(
        "That the account was a testnet rests on the typed exchange field, and the testnet "
        "candles are hash-bound but not authenticated as the exchange's."
    )
    out.append(
        "Fill and exit prices are the journal's own real executions: they are injected into "
        "the replay and audited for arithmetic, not checked against the candles' price range."
    )
    out.append(
        "The replay compares allowed signals only: a decisions-log fill refusal (X_capital or "
        "R8_structure_stop) of an allowed signal without a journal entry is replayed as logged, "
        "because a refused order's fill price is not journaled, and denied rows' rule ids are "
        "not compared."
    )
    if record.model_fingerprint is not None:
        out.append(
            "The ML model is not re-run on testnet: its L_ml_filter vetoes are taken from the "
            "decisions log, and its identity is bound by model_fingerprint."
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
    fp = config_fingerprint(cfg)[:FP_SHOWN]
    what = f"config {fp}" if model_fp is None else f"config {fp}, model {model_fp[:FP_SHOWN]}"
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
    if blank(args.variant):
        print("error: --variant may not be empty", file=sys.stderr)
        return 1
    variant, model_fp = args.variant.strip(), _model_arg(args.model_fingerprint)
    if model_fp is not None and not is_sha256_hex(model_fp):
        print(
            f"error: --model-fingerprint {model_fp!r} is not a sha256 hex digest "
            f"({SHA256_HEX_LEN} hex characters)",
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
