import csv
import dataclasses
import json
import math
import statistics
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType

import pytest

import research.trendbot.adoption as adoption
from research.trendbot import metrics
from research.trendbot.adoption import (
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
    DD_CAP_PCT,
    HUMAN_REVIEW,
    LABEL_PARAM_KEYS,
    LIVE,
    RULE_VIOLATION_FLAGS,
    STAGE_RULES,
    STAGE_SECTIONS,
    STAGES,
    TESTNET,
    TESTNET_EXCHANGE,
    WALK_FORWARD,
    AdoptionBlocked,
    AdoptionRecord,
    BacktestStage,
    FileRef,
    HumanReviewStage,
    LiveStage,
    TestnetStage,
    WalkForwardStage,
    canonical_config_json,
    check_promotion,
    config_fingerprint,
    config_from_overrides,
    distinct_looks,
    empty_record,
    load_record,
    main,
    read_decisions_log,
    read_ledger,
    recompute_walk_forward,
    replay_testnet,
    require_stage,
    save_record,
    sha256_file,
    unverified_notes,
    write_decisions_log,
)
from research.trendbot.config import RULE_IDS, ConfigError, PairRisk, StrategyConfig
from research.trendbot.data import pair_filename, save_candles_csv
from research.trendbot.fetch_data import manifest_entry, write_manifest
from research.trendbot.gatekeeper import DecisionRecord
from research.trendbot.journal import import_external, iso_to_ms, write_journal
from research.trendbot.models import (
    DAY_MS,
    EXIT_SL,
    EXIT_TP,
    HOUR_MS,
    Candle,
    Decision,
    NewsEvent,
    Trade,
)
from research.trendbot.review_sheet import (
    REVIEW_OK_BLANK,
    REVIEW_OK_NO,
    REVIEW_OK_YES,
    auto_flags,
    flag_code,
    write_review_pack,
)
from research.trendbot.tests.bt_helpers import (
    BASE_VOLUME,
    TF,
    Scenario,
    bench_scenario,
    drive_live_session,
    slot,
)


CFG = StrategyConfig()
NOW_ISO = "2024-07-01T00:00:00Z"
NOW = iso_to_ms(NOW_ISO)
TESTNET_START_ISO = "2024-04-01T00:00:00Z"
TESTNET_END_ISO = "2024-04-15T00:00:00Z"
TESTNET_START = iso_to_ms(TESTNET_START_ISO)
SPLIT_ISO = "2023-01-01T00:00:00Z"
SPLIT = iso_to_ms(SPLIT_ISO)
TRAIN_START = iso_to_ms("2021-01-01T00:00:00Z")
N_TRAIN, N_TEST = 32, 30
N_ALL = N_TRAIN + N_TEST
BTC, ETH = "BTC/USDT", "ETH/USDT"

# Evidence layout, relative to the record's directory (the CLI's base_dir).
DATA_FILE = "data/BTC_USDT-4h.csv"
MANIFEST = "data/manifest.json"
EVENTS = "data/events.csv"
LEDGER = "ledger/test_looks.jsonl"
BT_REPORT = "bt/REPORT.md"
WF_REPORT = "wf/REPORT.md"
TRAIN_JOURNAL = "wf/journals/train.csv"
TEST_JOURNAL = "wf/journals/test.csv"
REVIEW_DIR = "review"
REVIEW_CSV = "review/trades_review.csv"
JOURNAL = "testnet/trades.csv"
DECISIONS = "testnet/decisions.csv"
TESTNET_CANDLES = "testnet/candles"
TESTNET_BTC = f"{TESTNET_CANDLES}/BTC_USDT-4h.csv"
TESTNET_EVENTS = "testnet/events.csv"

# The testnet window: scenario candle FIRST_EVALUATED closes at TESTNET_START, the last one
# at TESTNET_START + 14 days (bt_helpers.Scenario signals every 6 candles from slot(0)).
FIRST_EVALUATED = 202
N_TESTNET_CANDLES = FIRST_EVALUATED + 14 * 6 + 1
MANIFEST_RUN = {
    "exchange_id": "binance",
    "ccxt_version": "4.3.0",
    "timeframe": "4h",
    "since_ms": SPLIT - DAY_MS,
    "until_ms": None,
    "fetched_at_utc": "2024-02-01T00:00:00Z",
}


# ---------------------------------------------------------------------------- fixtures
def a1_levels(entry: float, stop: float, cfg: StrategyConfig = CFG) -> tuple[float, float]:
    """(all-in loss per unit L_u, target T) exactly as CONTRACT.md v2 A1 defines them."""
    f, s = cfg.fee_rate, cfg.slippage_pct / 100
    stop_x = stop * (1 - s)
    loss_per_unit = (entry - stop_x) + f * entry + f * stop_x
    return loss_per_unit, (entry * (1 + f) + cfg.reward_risk * loss_per_unit) / (1 - f)


def closed_trade(
    trade_id: int,
    signal_ts: int,
    win: bool,
    *,
    variant: str = "base",
    cfg: StrategyConfig = CFG,
    pair: str = BTC,
    hold_h: int = 12,
    **changes: object,
) -> Trade:
    """A clean long with A1 cost-aware size and target: a TP is exactly +reward_risk R, an
    SL (filled at stop * (1 - slippage)) exactly -1R; risk ~0.1% of equity."""
    entry, stop, qty = 100.0, 95.0, 2.0
    loss_per_unit, target = a1_levels(entry, stop, cfg)
    risk_amount = qty * loss_per_unit
    exit_price = target if win else stop * (1 - cfg.slippage_pct / 100)
    fees = cfg.fee_rate * qty * (entry + exit_price)
    pnl = qty * (exit_price - entry) - fees
    t = Trade(
        trade_id=trade_id,
        pair=pair,
        variant=variant,
        signal_ts=signal_ts,
        entry_ts=signal_ts + 4 * HOUR_MS,
        entry_price=entry,
        stop=stop,
        target=target,
        qty=qty,
        risk_amount=risk_amount,
        risk_pct=risk_amount / cfg.starting_capital * 100,
        stop_method="pivot",
        exit_ts=signal_ts + hold_h * HOUR_MS,
        exit_price=exit_price,
        exit_reason=EXIT_TP if win else EXIT_SL,
        fees=fees,
        pnl=pnl,
        r_multiple=pnl / risk_amount,
    )
    return dataclasses.replace(t, **changes)


def wf_trades(variant: str = "base", cfg: StrategyConfig = CFG) -> tuple[list[Trade], list[Trade]]:
    """A strong edge in both windows (TRAIN 3 TP : 1 SL, TEST 4 TP : 1 SL), clearly ROBUST."""
    train = [
        closed_trade(i + 1, TRAIN_START + i * 3 * DAY_MS, i % 4 != 3, variant=variant, cfg=cfg)
        for i in range(N_TRAIN)
    ]
    test = [
        closed_trade(
            N_TRAIN + i + 1, SPLIT + DAY_MS + i * 3 * DAY_MS, i % 5 != 4, variant=variant, cfg=cfg
        )
        for i in range(N_TEST)
    ]
    return train, test


def weak_test_trades(variant: str = "base") -> list[Trade]:
    """30 TEST trades, 11 TP / 19 SL: mean +0.1R, positive but not distinguishable from 0."""
    return [
        closed_trade(
            N_TRAIN + i + 1, SPLIT + DAY_MS + i * 3 * DAY_MS, (i * 11) % 30 < 11, variant=variant
        )
        for i in range(N_TEST)
    ]


def mean_r(trades: list[Trade]) -> float:
    return statistics.fmean(t.r_multiple for t in trades if t.r_multiple is not None)


def flat_candles(first_ts: int, last_ts: int, price: float = 100.0) -> list[Candle]:
    """4H candles closing at ``price`` (the walk-forward trades' entry) over [first, last]."""
    return [
        Candle(ts, price, price * 1.005, price * 0.995, price, 1.0)
        for ts in range(first_ts, last_ts + 1, TF)
    ]


def wf_candles(test: list[Trade]) -> list[Candle]:
    last = max((t.exit_ts for t in test), default=SPLIT + 10 * DAY_MS)
    return flat_candles(SPLIT - DAY_MS, last + DAY_MS)


def label_params_for(
    train: list[Trade], test: list[Trade], candles: list[Candle], cfg: StrategyConfig = CFG
) -> dict[str, object]:
    """The closed D1 schema, the C5 inputs measured exactly as the walk-forward does."""
    n_test = sum(1 for t in test if t.exit_ts is not None)
    p95 = metrics.train_dd_quantile(train, n_test, metrics.N_BOOT, 7)
    data = {pair: candles for pair in {BTC, *(t.pair for t in test)}}
    mtm = metrics.mtm_max_dd_pct(test, data, cfg.starting_capital, cfg.fee_rate)
    return {
        "seed": 7,
        "n_boot": metrics.N_BOOT,
        "m": metrics.M_CANDIDATES,
        "alpha": metrics.ALPHA,
        "max_dd_pct": DD_CAP_PCT,
        "train_dd_p95_pct": 0.0 if p95 is None else p95,
        "mtm_max_dd_pct": mtm,
    }


def fill_review(path: Path, verdicts: dict[tuple[str, int], str] | None = None) -> None:
    """Enter the reviewer's verdicts into a trades_review.csv (default: every row Y)."""
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    header = rows[0]
    ok, window, trade_id = (header.index(c) for c in ("reviewer_ok", "window", "trade_id"))
    for row in rows[1:]:
        row[ok] = (verdicts or {}).get((row[window], int(row[trade_id])), REVIEW_OK_YES)
    with path.open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh, lineterminator="\n").writerows(rows)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def ledger_line(
    config_fp: str,
    model_fp: str | None,
    pairs: list[tuple[str, str | None, int, int]],
    *,
    split: str = SPLIT_ISO,
    variant: str = "base",
    run_utc: str = "2024-03-01T00:00:00Z",
) -> str:
    """One D3 ledger line (run_research writes these; argv / run_utc are ignored)."""
    entries = [
        {"pair": p, "file_sha256": sha, "test_start_ts": start, "test_end_ts": end}
        for p, sha, start, end in pairs
    ]
    return json.dumps(
        {
            "run_utc": run_utc,
            "argv": ["--data-dir", "data"],
            "variant": variant,
            "config_fingerprint": config_fp,
            "model_fingerprint": model_fp,
            "split_utc": split,
            "pairs": entries,
        }
    )


# ---------------------------------------------------------------------------- testnet world
@dataclass(frozen=True)
class TestnetWorld:
    """Candles, journal and decisions log of a (simulated) testnet run of LiveSession."""

    __test__ = False

    candles: dict[str, list[Candle]]
    journal: list[Trade]
    decisions: list[DecisionRecord]


def scenario_candles(
    pair: str,
    outcomes: dict[int, str],
    cfg: StrategyConfig = CFG,
    start: float = 100.0,
    extra_spikes: tuple[int, ...] = (),
) -> list[Candle]:
    """A bt_helpers.Scenario whose signal slots ``outcomes`` ({k: "tp" | "sl"}) are spiked and
    forced, shifted so candle FIRST_EVALUATED closes at TESTNET_START (epoch-aligned)."""
    s = Scenario(pair, n=N_TESTNET_CANDLES, start=start)
    for k, outcome in outcomes.items():
        s.spike(slot(k))
        (s.take_profit if outcome == "tp" else s.stop_out)(slot(k), cfg)
    s.spike(*(slot(k) for k in extra_spikes))
    return shifted(s.candles)


def drive_testnet(
    candles: dict[str, list[Candle]],
    cfg: StrategyConfig = CFG,
    variant: str = "base",
    events=(),
    entry_filter=None,
) -> TestnetWorld:
    """Run LiveSession like a testnet bot (real mid-candle exit fill times, exit offset 0)."""
    run = drive_live_session(
        candles,
        cfg,
        events,
        exit_time_uncertainty_ms=0,
        exit_fill_ms=TF // 2,
        entry_filter=entry_filter,
    )
    journal = [replace(t, variant=variant) for t in run.session.journal_trades()]
    decisions = [d for d in run.decision_log if d.signal_ts + TF >= TESTNET_START]
    return TestnetWorld(candles, journal, decisions)


_WORLDS: dict[tuple[str, str], TestnetWorld] = {}


def default_world(cfg: StrategyConfig = CFG, variant: str = "base") -> TestnetWorld:
    """BTC only: a take-profit, a stop-loss and a take-profit inside the 14-day window."""
    key = (config_fingerprint(cfg), variant)
    if key not in _WORLDS:
        candles = {BTC: scenario_candles(BTC, {0: "tp", 3: "sl", 6: "tp"}, cfg)}
        _WORLDS[key] = drive_testnet(candles, cfg, variant)
    return _WORLDS[key]


def write_testnet(
    d: Path, rel, world: TestnetWorld, candles: dict[str, list[Candle]] | None = None
) -> dict[str, object]:
    """Write the D6 evidence files; return the TestnetStage hash fields."""
    write_journal(world.journal, d / JOURNAL)
    write_decisions_log(world.decisions, d / DECISIONS)
    files: dict[str, str] = {}
    for pair, cs in sorted((world.candles if candles is None else candles).items()):
        path = f"{TESTNET_CANDLES}/{pair_filename(pair)}"
        (d / path).parent.mkdir(parents=True, exist_ok=True)
        save_candles_csv(cs, d / path)
        files[rel(path)] = sha256_file(d / path)
    return {
        "journal_path": rel(JOURNAL),
        "journal_sha256": sha256_file(d / JOURNAL),
        "decisions_path": rel(DECISIONS),
        "decisions_sha256": sha256_file(d / DECISIONS),
        "candles": files,
        "trades": len(world.journal),
    }


def build_evidence(
    root: Path,
    cfg: StrategyConfig = CFG,
    variant: str = "base",
    *,
    prefix: str = "",
    train: list[Trade] | None = None,
    test: list[Trade] | None = None,
    world: TestnetWorld | None = None,
    verdicts: dict[tuple[str, int], str] | None = None,
    model_fp: str | None = None,
    pairs: tuple[str, ...] = (BTC,),
) -> AdoptionRecord:
    """Write every evidence file under ``root / prefix`` and return the complete record
    (real provenance with a verifying manifest, every file hash-bound, the record's own TEST
    look in the holdout ledger, typed walk-forward numbers and C5 inputs from the journals
    and candles, an all-Y review pack unless ``verdicts`` say otherwise, and a 14-day testnet
    window driven through LiveSession: journal, decisions log and candles)."""
    d = root / prefix if prefix else root

    def rel(path: str) -> str:
        return f"{prefix}/{path}" if prefix else path

    def sha(path: str) -> str:
        return sha256_file(d / path)

    default_train, default_test = wf_trades(variant, cfg)
    train = default_train if train is None else train
    test = default_test if test is None else test
    world = default_world(cfg, variant) if world is None else world
    candles = wf_candles(test)  # BTC, the pair of the walk-forward trades, closes at 100
    (d / DATA_FILE).parent.mkdir(parents=True, exist_ok=True)
    entries, data_files = [], {}
    for k, pair in enumerate(pairs):  # other pairs only add their (distinct) candle files
        path = f"data/{pair_filename(pair)}"
        pair_candles = (
            candles if pair == BTC else flat_candles(candles[0].ts, candles[-1].ts, 100.0 * (k + 2))
        )
        save_candles_csv(pair_candles, d / path)
        entries.append(manifest_entry(d / path, pair, pair_candles, "4h", MANIFEST_RUN))
        data_files[rel(path)] = sha(path)
    write_manifest(d / "data", MANIFEST_RUN, list(pairs), entries)
    write_text(d / EVENTS, "time_utc,scope,impact,kind,note\n")
    write_text(d / BT_REPORT, "# backtest report\n")
    write_text(d / WF_REPORT, "# walk-forward report\n")
    write_journal(train, d / TRAIN_JOURNAL)
    write_journal(test, d / TEST_JOURNAL)
    pack = write_review_pack(train + test, d / REVIEW_DIR, "fixture", cfg, split_ts=SPLIT)
    fill_review(pack["csv"], verdicts)
    windows = [(pr, sha(f"data/{pair_filename(pr)}"), SPLIT, candles[-1].ts + TF) for pr in pairs]
    write_text(d / LEDGER, ledger_line(config_fingerprint(cfg), model_fp, windows) + "\n")
    testnet = write_testnet(d, rel, world)
    n = len(train) + len(test)
    return AdoptionRecord(
        variant=variant,
        config_fingerprint=config_fingerprint(cfg),
        model_fingerprint=model_fp,
        provenance="real",
        data_files=data_files,
        events_file=FileRef(rel(EVENTS), sha(EVENTS)),
        manifest=FileRef(rel(MANIFEST), sha(MANIFEST)),
        ledger_path=rel(LEDGER),
        backtest=BacktestStage(
            report_path=rel(BT_REPORT),
            completed_utc="2024-03-01T00:00:00Z",
            report_sha256=sha(BT_REPORT),
        ),
        walk_forward=WalkForwardStage(
            label="ROBUST",
            dd_ok=True,
            split_utc=SPLIT_ISO,
            train_n=len(train),
            test_n=len(test),
            test_avg_r=mean_r(test) if test else None,
            report_path=rel(WF_REPORT),
            train_journal_path=rel(TRAIN_JOURNAL),
            train_journal_sha256=sha(TRAIN_JOURNAL),
            test_journal_path=rel(TEST_JOURNAL),
            test_journal_sha256=sha(TEST_JOURNAL),
            min_train=30,
            min_test=30,
            train_avg_r=mean_r(train) if train else None,
            label_params=label_params_for(train, test, candles, cfg),
        ),
        human_review=HumanReviewStage(
            reviewer="Jane Doe",
            date_utc="2024-03-10T00:00:00Z",
            trades_reviewed=n,
            trades_total=n,
            approved=True,
            notes="checked every row against the charts",
            review_path=rel(REVIEW_CSV),
            review_sha256=sha(REVIEW_CSV),
        ),
        testnet=TestnetStage(
            exchange=TESTNET_EXCHANGE,
            start_utc=TESTNET_START_ISO,
            end_utc=TESTNET_END_ISO,
            rule_violations=0,
            notes="",
            starting_equity=cfg.starting_capital,
            **testnet,  # type: ignore[arg-type]
        ),
    )


@pytest.fixture(scope="module")
def base(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One evidence directory shared by the tests that do not modify files."""
    return tmp_path_factory.mktemp("evidence")


_BUILT: dict[tuple[str, str, str, str | None], AdoptionRecord] = {}


def valid_record(
    base: Path, cfg: StrategyConfig = CFG, variant: str = "base", model_fp: str | None = None
) -> AdoptionRecord:
    """Every stage complete with hash-bound evidence; testnet lasts EXACTLY 14 days."""
    fp = config_fingerprint(cfg)
    key = (str(base), variant, fp, model_fp)
    if key not in _BUILT:
        tag = "none" if model_fp is None else str(abs(hash(model_fp)))
        prefix = f"{variant.replace('+', '_plus_')}_{fp[:12]}_{tag}"
        _BUILT[key] = build_evidence(base, cfg, variant, prefix=prefix, model_fp=model_fp)
    return _BUILT[key]


def check(
    base: Path,
    record: AdoptionRecord,
    stage: str,
    cfg: StrategyConfig = CFG,
    model_fingerprint: str | None = None,
) -> list[Decision]:
    return check_promotion(record, stage, cfg, NOW, base, model_fingerprint)


def mutate(record: AdoptionRecord, section: str, **changes: object) -> AdoptionRecord:
    return dataclasses.replace(
        record, **{section: dataclasses.replace(getattr(record, section), **changes)}
    )


def with_params(record: AdoptionRecord, drop: tuple[str, ...] = (), **changes) -> AdoptionRecord:
    params = {k: v for k, v in record.walk_forward.label_params.items() if k not in drop}
    return mutate(record, "walk_forward", label_params={**params, **changes})


def assert_one_sentence(text: str) -> None:
    assert text.endswith("."), text
    assert ". " not in text and "\n" not in text, text


def blocking_rules(decisions: list[Decision]) -> set[str]:
    """Check the Decision invariants and return the set of rule ids."""
    for d in decisions:
        assert isinstance(d, Decision)
        assert d.allowed is False
        assert d.rule in ADOPTION_RULE_IDS, d.rule
        assert_one_sentence(d.reason)
    return {d.rule for d in decisions}


def reasons(decisions: list[Decision]) -> str:
    return " | ".join(d.reason for d in decisions)


def rehash(record: AdoptionRecord, root: Path, section: str, path_field: str) -> AdoptionRecord:
    """Record the CURRENT sha256 of a (deliberately edited) evidence file."""
    sha_field = path_field.replace("_path", "_sha256")
    path = getattr(getattr(record, section), path_field)
    return mutate(record, section, **{sha_field: sha256_file(root / path)})


def put_testnet(
    record: AdoptionRecord,
    root: Path,
    *,
    journal: list[Trade] | None = None,
    decisions: list[DecisionRecord] | None = None,
) -> AdoptionRecord:
    """Rewrite testnet evidence under ``root`` (prefix-free records) and re-record its hashes."""
    changes: dict[str, object] = {}
    if journal is not None:
        write_journal(journal, root / JOURNAL)
        changes.update(journal_sha256=sha256_file(root / JOURNAL), trades=len(journal))
    if decisions is not None:
        write_decisions_log(decisions, root / DECISIONS)
        changes["decisions_sha256"] = sha256_file(root / DECISIONS)
    return mutate(record, "testnet", **changes)


def flip(decisions: list[DecisionRecord], signal_ts: int, allowed: bool, rule: str):
    return [
        replace(d, allowed=allowed, rule=rule, reason="edited") if d.signal_ts == signal_ts else d
        for d in decisions
    ]


def signal_ts_of(k: int) -> int:
    """Signal candle time of scenario slot k in the shifted testnet world."""
    return default_world().candles[BTC][slot(k)].ts


LATER_THAN_WF = (HUMAN_REVIEW, TESTNET, LIVE)
LATER_THAN_REVIEW = (TESTNET, LIVE)


def test_fixture_trades_are_clean_under_the_review_flags():
    for win in (True, False):
        t = closed_trade(1, TESTNET_START, win)
        assert auto_flags(t, CFG) == []
        assert math.isclose(t.r_multiple, CFG.reward_risk if win else -1.0, rel_tol=1e-12)


def test_fixture_walk_forward_is_robust_under_the_current_metrics():
    train, test = wf_trades()
    params = label_params_for(train, test, wf_candles(test))
    verdict = recompute_walk_forward(train, test, CFG, 30, 30, params)
    assert verdict.label == "ROBUST" and verdict.dd_ok is True
    weak = weak_test_trades()
    weak_params = label_params_for(train, weak, wf_candles(weak))
    verdict = recompute_walk_forward(train, weak, CFG, 30, 30, weak_params)
    assert verdict.label == "UNTESTED" and verdict.test.avg_r > 0


def test_fixture_testnet_window_is_a_real_liveSession_run():
    world = default_world()
    assert [t.exit_reason for t in world.journal] == [EXIT_TP, EXIT_SL, EXIT_TP]
    assert [d.signal_ts for d in world.decisions if d.allowed] == [
        signal_ts_of(k) for k in (0, 3, 6)
    ]
    assert all(TESTNET_START <= t.entry_ts <= iso_to_ms(TESTNET_END_ISO) for t in world.journal)
    assert world.candles[BTC][-1].ts + TF == iso_to_ms(TESTNET_END_ISO)


# ---------------------------------------------------------------------------- fingerprint
def test_fingerprint_is_sha256_hex_and_deterministic():
    fp = config_fingerprint(CFG)
    assert len(fp) == 64 and int(fp, 16) >= 0
    assert fp == config_fingerprint(StrategyConfig())


def test_canonical_json_covers_every_field_with_sorted_keys():
    text = canonical_config_json(CFG)
    data = json.loads(text)
    assert set(data) == {f.name for f in dataclasses.fields(StrategyConfig)}
    assert list(data) == sorted(data)
    assert data["pair_risk"]["BNB"] == {"max_risk_pct": 0.5, "stop_buffer_pct": 0.65}


def test_fingerprint_ignores_representation_only_differences():
    assert config_fingerprint(StrategyConfig(reward_risk=2)) == config_fingerprint(CFG)
    plain = dict(CFG.pair_risk)
    assert config_fingerprint(StrategyConfig(pair_risk=plain)) == config_fingerprint(CFG)
    proxied = MappingProxyType(dict(reversed(list(plain.items()))))
    assert config_fingerprint(StrategyConfig(pair_risk=proxied)) == config_fingerprint(CFG)


@pytest.mark.parametrize(
    "changes",
    [
        {"reward_risk": 2.5},
        {"vol_mult": 2.0},
        {"rsi_min": 55.0},
        {"regime_filter": False},
        {"expectancy_guard": True},
        {"weekly_loss_limit_pct": 2.0},
        {"fee_rate": 0.00075},
        {"starting_capital": 5_000.0},
        {"news_blackout_hours": 3.0},
        {"stop_fill_wick_k": 0.5},
    ],
)
def test_fingerprint_changes_with_any_field(changes):
    assert config_fingerprint(CFG.with_changes(**changes)) != config_fingerprint(CFG)


def test_fingerprint_includes_pair_risk():
    risk = dict(CFG.pair_risk)
    risk["BNB"] = PairRisk(max_risk_pct=0.5, stop_buffer_pct=0.7)
    assert config_fingerprint(StrategyConfig(pair_risk=risk)) != config_fingerprint(CFG)
    risk["BNB"] = PairRisk(max_risk_pct=0.4, stop_buffer_pct=0.65)
    assert config_fingerprint(StrategyConfig(pair_risk=risk)) != config_fingerprint(CFG)


def test_sha256_file_hashes_the_bytes(tmp_path: Path):
    path = tmp_path / "f.txt"
    path.write_bytes(b"hello\n")
    assert sha256_file(path) == "5891b5b522d5df086d0ff0b110fbd9d21bb4fc7163af34d08286a2e846f6be03"


# ---------------------------------------------------------------------------- config overrides
def test_config_from_overrides_applies_legal_tightenings():
    cfg = config_from_overrides(
        {
            "reward_risk": 3,
            "vol_mult": 2.0,
            "bnb_event_kinds": ["bnb_burn", "launchpool", "other"],
            "pair_risk": {
                "BTC": {"max_risk_pct": 0.5, "stop_buffer_pct": 0.25},
                "ETH": {"max_risk_pct": 1.0, "stop_buffer_pct": 0.25},
                "BNB": {"max_risk_pct": 0.5, "stop_buffer_pct": 0.8},
            },
        }
    )
    assert cfg.reward_risk == 3.0 and isinstance(cfg.reward_risk, float)
    assert cfg.bnb_event_kinds == ("bnb_burn", "launchpool", "other")
    assert cfg.pair_risk["BNB"] == PairRisk(0.5, 0.8)
    assert config_from_overrides({}) == CFG


@pytest.mark.parametrize(
    "overrides",
    [
        {"reward_risk": 1.5},  # loosens the 2:1 minimum
        {"rsi_max": 75.0},  # loosens R2
        {"pair_risk": {"BNB": {"max_risk_pct": 1.0, "stop_buffer_pct": 0.65}}},  # R7 BNB cap
        {"not_a_field": 1},
        {"regime_filter": "false"},  # a string is not a bool
        {"vol_mult": True},  # a bool is not a number
        {"consecutive_sl_limit": 2.5},  # int field
        {"pair_risk": {"BTC": {"max_risk_pct": 1.0}}},  # incomplete PairRisk
        {"pair_risk": [1, 2]},
        {"correlated_cluster": "BTC,ETH,BNB"},
        {"bnb_event_kinds": []},  # drops the R5 BNB burn/launchpool blackout
        {"bnb_event_kinds": ["bnb_burn"]},  # drops launchpool
    ],
)
def test_config_from_overrides_rejects_bad_or_loosening_values(overrides):
    with pytest.raises(ConfigError):
        config_from_overrides(overrides)


def test_config_from_overrides_rejects_empty_bnb_event_kinds():
    with pytest.raises(ConfigError, match="bnb_event_kinds must include"):
        config_from_overrides({"bnb_event_kinds": []})


# ---------------------------------------------------------------------------- record JSON
def test_record_round_trip(tmp_path: Path, base: Path):
    for record in (
        valid_record(base),
        empty_record("base"),
        mutate(valid_record(base), "live", enabled_utc=NOW_ISO),
        mutate(valid_record(base), "walk_forward", label_params={"max_dd_pct": 15, "x": "y"}),
        dataclasses.replace(valid_record(base), events_file=None, data_files={}, manifest=None),
        dataclasses.replace(valid_record(base), ledger_path=None),
    ):
        path = tmp_path / "sub" / "rec.json"
        save_record(record, path)
        assert load_record(path) == record


def test_saved_record_has_every_c3_and_v4_field(tmp_path: Path, base: Path):
    path = tmp_path / "rec.json"
    save_record(valid_record(base), path)
    data = json.loads(path.read_text())
    assert data["provenance"] == "real"
    assert list(data["data_files"].values()) == [sha256_file(base / next(iter(data["data_files"])))]
    assert set(data["events_file"]) == {"path", "sha256"}
    assert set(data["manifest"]) == {"path", "sha256"}  # D2
    assert data["ledger_path"].endswith(LEDGER)  # D3
    assert "report_sha256" in data["backtest"]
    assert {
        "train_journal_path",
        "train_journal_sha256",
        "test_journal_path",
        "test_journal_sha256",
        "min_train",
        "min_test",
        "train_avg_r",
        "label_params",
    } <= set(data["walk_forward"])
    assert set(data["walk_forward"]["label_params"]) == set(LABEL_PARAM_KEYS)  # D1
    assert {"review_path", "review_sha256"} <= set(data["human_review"])
    assert {
        "events_path",
        "events_sha256",
        "journal_sha256",
        "decisions_path",
        "decisions_sha256",
        "candles",
        "starting_equity",
    } <= set(data["testnet"])  # D6


def test_empty_record_uses_default_fingerprint_and_empty_stages():
    record = empty_record("rr2.5_vol2")
    assert record.variant == "rr2.5_vol2"
    assert record.config_fingerprint == config_fingerprint(StrategyConfig())
    assert record.backtest == BacktestStage() and record.live == LiveStage()
    assert record.provenance is None and record.data_files is None and record.events_file is None
    assert record.manifest is None and record.ledger_path is None
    cfg = CFG.with_changes(reward_risk=2.5)
    assert empty_record("x", cfg).config_fingerprint == config_fingerprint(cfg)


def test_load_record_missing_sections_are_empty_and_ints_become_floats(tmp_path: Path):
    path = tmp_path / "rec.json"
    path.write_text(
        json.dumps(
            {"variant": "base", "config_fingerprint": "ab", "walk_forward": {"test_avg_r": 1}}
        )
    )
    record = load_record(path)
    assert record.testnet == TestnetStage()
    assert record.walk_forward.test_avg_r == 1.0
    assert isinstance(record.walk_forward.test_avg_r, float)


def test_record_without_c3_fields_loads_but_is_blocked_after_walk_forward(
    tmp_path: Path, base: Path
):
    """Backward compatibility: a pre-C3 record loads unchanged, promotion then needs C3."""
    path = tmp_path / "rec.json"
    save_record(valid_record(base), path)
    data = json.loads(path.read_text())
    for key in ("provenance", "data_files", "events_file", "model_fingerprint", "manifest"):
        del data[key]
    del data["ledger_path"]
    del data["backtest"]["report_sha256"]
    for key in ("train_journal_sha256", "test_journal_sha256", "label_params", "min_train"):
        del data["walk_forward"][key]
    path.write_text(json.dumps(data))
    old = load_record(path)
    assert old.provenance is None and old.walk_forward.label_params is None
    rules = blocking_rules(check(base, old, HUMAN_REVIEW))
    assert rules == {ADOPT_PROVENANCE, ADOPT_BACKTEST, ADOPT_WALK_FORWARD, ADOPT_HOLDOUT}


@pytest.mark.parametrize(
    "text, match",
    [
        ('{"variant": "b", "config_fingerprint": "x", "extra": 1}', "unknown keys"),
        (
            '{"variant": "b", "config_fingerprint": "x", "human_review": {"trades_reviwed": 9}}',
            "unknown keys",
        ),
        (
            '{"variant": "b", "config_fingerprint": "x", "human_review": {"approved": "yes"}}',
            "approved must be true/false",
        ),
        (
            '{"variant": "b", "config_fingerprint": "x", "human_review": {"trades_total": 99.0}}',
            "must be an integer",
        ),
        (
            '{"variant": "b", "config_fingerprint": "x", "testnet": {"rule_violations": false}}',
            "must be an integer",
        ),
        (
            '{"variant": "b", "config_fingerprint": "x", "walk_forward": {"test_avg_r": NaN}}',
            "must be a number",
        ),
        ('{"variant": "b", "config_fingerprint": "x", "testnet": [1]}', "JSON object or null"),
        ('{"variant": "b", "variant": "c", "config_fingerprint": "x"}', "duplicate key"),
        ('{"config_fingerprint": "x"}', "variant must be a string"),
        ("[1, 2]", "must be a JSON object"),
        ("{not json", "not valid JSON"),
        ('{"variant": "b", "config_fingerprint": "x", "provenance": 1}', "provenance must be"),
        ('{"variant": "b", "config_fingerprint": "x", "data_files": ["a"]}', "data_files must be"),
        ('{"variant": "b", "config_fingerprint": "x", "data_files": {"a": 1}}', "data_files.a"),
        (
            '{"variant": "b", "config_fingerprint": "x", "events_file": {"file": "e.csv"}}',
            "unknown keys",
        ),
        (
            '{"variant": "b", "config_fingerprint": "x", "manifest": {"sha": "e"}}',
            "unknown keys",
        ),
        ('{"variant": "b", "config_fingerprint": "x", "ledger_path": 3}', "ledger_path must be"),
        (
            '{"variant": "b", "config_fingerprint": "x", "testnet": {"candles": ["a.csv"]}}',
            "candles must be a JSON object",
        ),
        (
            '{"variant": "b", "config_fingerprint": "x", "testnet": {"starting_equity": "10k"}}',
            "starting_equity must be a number",
        ),
        (
            '{"variant": "b", "config_fingerprint": "x", "walk_forward": {"label_params": 3}}',
            "label_params must be a JSON object",
        ),
        (
            '{"variant": "b", "config_fingerprint": "x", '
            '"walk_forward": {"label_params": {"m": [4]}}}',
            "label_params.m must be",
        ),
        (
            '{"variant": "b", "config_fingerprint": "x", '
            '"walk_forward": {"label_params": {"m": NaN}}}',
            "label_params.m must be",
        ),
        (
            '{"variant": "b", "config_fingerprint": "x", "backtest": {"report_sha256": 5}}',
            "report_sha256 must be a string",
        ),
    ],
)
def test_load_record_rejects_malformed_files(tmp_path: Path, text: str, match: str):
    path = tmp_path / "rec.json"
    path.write_text(text)
    with pytest.raises(ValueError, match=match):
        load_record(path)


# ---------------------------------------------------------------------------- promotion: pass
@pytest.mark.parametrize("stage", STAGES)
def test_fully_valid_real_record_with_an_all_y_pack_passes_every_stage(base: Path, stage: str):
    assert check(base, valid_record(base), stage) == []


def test_testnet_of_exactly_14_days_passes_to_live(base: Path):
    record = valid_record(base)
    start, end = iso_to_ms(record.testnet.start_utc), iso_to_ms(record.testnet.end_utc)
    assert end - start == 14 * DAY_MS
    assert check(base, record, LIVE) == []
    require_stage(record, LIVE, CFG, NOW, base)  # does not raise


def test_empty_record_may_only_start_the_backtest(base: Path):
    record = empty_record("base")
    assert check(base, record, BACKTEST) == []
    decisions = check(base, record, WALK_FORWARD)
    assert blocking_rules(decisions) == {ADOPT_BACKTEST} and len(decisions) == 1
    decisions = check(base, record, LIVE)
    assert [d.rule for d in decisions] == [
        ADOPT_PROVENANCE,  # provenance missing
        ADOPT_PROVENANCE,  # no data file hashes
        ADOPT_HOLDOUT,  # no holdout ledger
        ADOPT_BACKTEST,
        ADOPT_WALK_FORWARD,
        ADOPT_HUMAN_REVIEW,
        ADOPT_TESTNET,
    ]


def test_base_dir_none_resolves_evidence_against_the_working_directory(
    base: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    record = valid_record(base)
    monkeypatch.chdir(base)
    assert check_promotion(record, LIVE, CFG, NOW) == []
    monkeypatch.chdir(tmp_path)  # evidence is ALWAYS checked: elsewhere nothing is found
    decisions = check_promotion(record, LIVE, CFG, NOW)
    assert "does not exist" in reasons(decisions)
    assert blocking_rules(decisions) >= {ADOPT_PROVENANCE, ADOPT_BACKTEST, ADOPT_WALK_FORWARD}


# ---------------------------------------------------------------------------- skipping stages
@pytest.mark.parametrize("skipped", STAGES[:-1])
def test_skipping_any_stage_blocks_every_later_stage(base: Path, skipped: str):
    section = STAGE_SECTIONS[skipped]
    good = valid_record(base)
    record = dataclasses.replace(good, **{section: type(getattr(good, section))()})
    for target in STAGES[STAGES.index(skipped) + 1 :]:
        decisions = check(base, record, target)
        assert blocking_rules(decisions) == {STAGE_RULES[skipped]}, target
        assert len(decisions) == 1, decisions
        assert "no recorded result" in decisions[0].reason
    earlier = STAGES[STAGES.index(skipped)]
    assert check(base, record, earlier) == []


def test_skipping_human_review_to_testnet_blocks(base: Path):
    record = dataclasses.replace(valid_record(base), human_review=HumanReviewStage())
    decisions = check(base, record, TESTNET)
    assert blocking_rules(decisions) == {ADOPT_HUMAN_REVIEW}
    assert "HUMAN_REVIEW" in decisions[0].reason and "TESTNET" in decisions[0].reason


# ---------------------------------------------------------------------------- blocking conditions
BLOCKING_CASES = [
    # (id, section, changes, rule)
    ("backtest_no_report", "backtest", {"report_path": None}, ADOPT_BACKTEST),
    ("backtest_blank_report", "backtest", {"report_path": "  "}, ADOPT_BACKTEST),
    ("backtest_no_report_hash", "backtest", {"report_sha256": None}, ADOPT_BACKTEST),
    ("backtest_bad_report_hash", "backtest", {"report_sha256": "abc"}, ADOPT_BACKTEST),
    ("backtest_wrong_report_hash", "backtest", {"report_sha256": "0" * 64}, ADOPT_BACKTEST),
    ("backtest_no_time", "backtest", {"completed_utc": None}, ADOPT_BACKTEST),
    ("backtest_bad_time", "backtest", {"completed_utc": "last tuesday"}, ADOPT_BACKTEST),
    ("wf_train_only", "walk_forward", {"label": "TRAIN-ONLY"}, ADOPT_WALK_FORWARD),
    ("wf_untested", "walk_forward", {"label": "UNTESTED"}, ADOPT_WALK_FORWARD),
    ("wf_no_edge", "walk_forward", {"label": "NO-EDGE"}, ADOPT_WALK_FORWARD),
    ("wf_label_case", "walk_forward", {"label": "robust"}, ADOPT_WALK_FORWARD),
    ("wf_no_label", "walk_forward", {"label": None}, ADOPT_WALK_FORWARD),
    ("wf_dd_failed", "walk_forward", {"dd_ok": False}, ADOPT_WALK_FORWARD),
    ("wf_dd_missing", "walk_forward", {"dd_ok": None}, ADOPT_WALK_FORWARD),
    ("wf_zero_avg_r", "walk_forward", {"test_avg_r": 0.0}, ADOPT_WALK_FORWARD),
    ("wf_negative_avg_r", "walk_forward", {"test_avg_r": -0.1}, ADOPT_WALK_FORWARD),
    ("wf_no_train", "walk_forward", {"train_n": 0}, ADOPT_WALK_FORWARD),
    ("wf_no_test", "walk_forward", {"test_n": None}, ADOPT_WALK_FORWARD),
    ("wf_no_report", "walk_forward", {"report_path": ""}, ADOPT_WALK_FORWARD),
    ("wf_no_train_journal", "walk_forward", {"train_journal_path": None}, ADOPT_WALK_FORWARD),
    ("wf_no_test_hash", "walk_forward", {"test_journal_sha256": None}, ADOPT_WALK_FORWARD),
    ("wf_no_min_train", "walk_forward", {"min_train": None}, ADOPT_WALK_FORWARD),
    ("wf_zero_min_test", "walk_forward", {"min_test": 0}, ADOPT_WALK_FORWARD),
    ("wf_no_train_avg_r", "walk_forward", {"train_avg_r": None}, ADOPT_WALK_FORWARD),
    ("wf_no_label_params", "walk_forward", {"label_params": None}, ADOPT_WALK_FORWARD),
    ("review_short", "human_review", {"trades_reviewed": N_ALL - 1}, ADOPT_HUMAN_REVIEW),
    ("review_over", "human_review", {"trades_reviewed": N_ALL + 1}, ADOPT_HUMAN_REVIEW),
    ("review_count_missing", "human_review", {"trades_reviewed": None}, ADOPT_HUMAN_REVIEW),
    (
        "review_zero_trades",
        "human_review",
        {"trades_reviewed": 0, "trades_total": 0},
        ADOPT_HUMAN_REVIEW,
    ),
    (
        "review_not_all_wf_trades",
        "human_review",
        {"trades_reviewed": N_ALL - 2, "trades_total": N_ALL - 2},
        ADOPT_HUMAN_REVIEW,
    ),
    ("review_not_approved", "human_review", {"approved": False}, ADOPT_HUMAN_REVIEW),
    ("review_approval_missing", "human_review", {"approved": None}, ADOPT_HUMAN_REVIEW),
    ("review_no_reviewer", "human_review", {"reviewer": None}, ADOPT_HUMAN_REVIEW),
    ("review_blank_reviewer", "human_review", {"reviewer": "   "}, ADOPT_HUMAN_REVIEW),
    (
        "review_before_backtest",
        "human_review",
        {"date_utc": "2024-02-01T00:00:00Z"},
        ADOPT_HUMAN_REVIEW,
    ),
    ("review_no_pack", "human_review", {"review_path": None}, ADOPT_HUMAN_REVIEW),
    ("review_no_pack_hash", "human_review", {"review_sha256": None}, ADOPT_HUMAN_REVIEW),
    ("review_wrong_pack_hash", "human_review", {"review_sha256": "f" * 64}, ADOPT_HUMAN_REVIEW),
    ("testnet_13_days", "testnet", {"end_utc": "2024-04-14T00:00:00Z"}, ADOPT_TESTNET),
    ("testnet_1ms_short", "testnet", {"end_utc": "2024-04-14T23:59:59.999Z"}, ADOPT_TESTNET),
    ("testnet_real_exchange", "testnet", {"exchange": "binance"}, ADOPT_TESTNET),
    ("testnet_other_testnet", "testnet", {"exchange": "coinbase-sandbox"}, ADOPT_TESTNET),
    ("testnet_no_exchange", "testnet", {"exchange": None}, ADOPT_TESTNET),
    (
        "testnet_not_finished",
        "testnet",
        {"end_utc": "2024-07-02T00:00:00Z"},  # after NOW
        ADOPT_TESTNET,
    ),
    ("testnet_one_violation", "testnet", {"rule_violations": 1}, ADOPT_TESTNET),
    ("testnet_violations_unrecorded", "testnet", {"rule_violations": None}, ADOPT_TESTNET),
    ("testnet_no_journal", "testnet", {"journal_path": None}, ADOPT_TESTNET),
    ("testnet_blank_journal", "testnet", {"journal_path": ""}, ADOPT_TESTNET),
    ("testnet_no_journal_hash", "testnet", {"journal_sha256": None}, ADOPT_TESTNET),
    ("testnet_wrong_journal_hash", "testnet", {"journal_sha256": "0" * 64}, ADOPT_TESTNET),
    ("testnet_no_decisions", "testnet", {"decisions_path": None}, ADOPT_TESTNET),
    ("testnet_no_decisions_hash", "testnet", {"decisions_sha256": None}, ADOPT_TESTNET),
    ("testnet_no_candles", "testnet", {"candles": None}, ADOPT_TESTNET),
    ("testnet_empty_candles", "testnet", {"candles": {}}, ADOPT_TESTNET),
    ("testnet_no_equity", "testnet", {"starting_equity": None}, ADOPT_TESTNET),
    ("testnet_zero_equity", "testnet", {"starting_equity": 0.0}, ADOPT_TESTNET),
    ("testnet_no_trades", "testnet", {"trades": 0}, ADOPT_TESTNET),
    (
        "testnet_before_review",
        "testnet",
        {"start_utc": "2024-03-05T00:00:00Z", "end_utc": "2024-03-25T00:00:00Z"},
        ADOPT_TESTNET,
    ),
    ("testnet_bad_start", "testnet", {"start_utc": "2024-04-31T00:00:00Z"}, ADOPT_TESTNET),
    ("testnet_no_end", "testnet", {"end_utc": None}, ADOPT_TESTNET),
    ("testnet_events_without_hash", "testnet", {"events_path": "e.csv"}, ADOPT_TESTNET),
]


@pytest.mark.parametrize(
    "section, changes, rule", [c[1:] for c in BLOCKING_CASES], ids=[c[0] for c in BLOCKING_CASES]
)
def test_each_blocking_condition_blocks_individually(base: Path, section, changes, rule):
    record = mutate(valid_record(base), section, **changes)
    stage = next(s for s, sec in STAGE_SECTIONS.items() if sec == section)
    for target in STAGES[STAGES.index(stage) + 1 :]:
        decisions = check(base, record, target)
        assert blocking_rules(decisions) == {rule}, (target, decisions)
    assert check(base, record, stage) == []  # the stage itself may still start


def test_reasons_name_the_problem(base: Path):
    def reason(section, **changes):
        return reasons(check(base, mutate(valid_record(base), section, **changes), LIVE))

    assert f"{N_ALL - 1} of {N_ALL}" in reason("human_review", trades_reviewed=N_ALL - 1)
    assert "TRAIN-ONLY" in reason("walk_forward", label="TRAIN-ONLY")
    assert "13.00 days" in reason("testnet", end_utc="2024-04-14T00:00:00Z")
    assert "'binance'" in reason("testnet", exchange="binance")
    assert f"train {N_TRAIN} + test {N_TEST}" in reason(
        "human_review", trades_reviewed=N_ALL - 2, trades_total=N_ALL - 2
    )
    assert "not the evidence that was recorded" in reason("backtest", report_sha256="0" * 64)
    no_trades = reason("testnet", trades=0)
    assert "about 1 trade per 14 days" in no_trades and "expected to hold about 1.0" in no_trades
    assert "not mainnet" in no_trades and "not edge" in no_trades
    assert "positive account equity" in reason("testnet", starting_equity=-5.0)


def test_future_times_block_the_stage_that_owns_them(base: Path):
    late_review = mutate(valid_record(base), "human_review", date_utc="2024-08-01T00:00:00Z")
    decisions = check(base, late_review, TESTNET)
    assert blocking_rules(decisions) == {ADOPT_HUMAN_REVIEW}
    # at LIVE the testnet additionally started before that (future) sign-off
    decisions = check(base, late_review, LIVE)
    assert blocking_rules(decisions) == {ADOPT_HUMAN_REVIEW, ADOPT_TESTNET}
    late_backtest = mutate(valid_record(base), "backtest", completed_utc="2024-08-01T00:00:00Z")
    decisions = check(base, late_backtest, WALK_FORWARD)
    assert blocking_rules(decisions) == {ADOPT_BACKTEST}


def test_several_problems_are_all_reported(base: Path):
    record = mutate(valid_record(base), "human_review", trades_reviewed=N_ALL - 1, approved=False)
    record = mutate(record, "testnet", end_utc="2024-04-10T00:00:00Z", rule_violations=2)
    decisions = check(base, record, LIVE)
    assert blocking_rules(decisions) == {ADOPT_HUMAN_REVIEW, ADOPT_TESTNET}
    assert len(decisions) == 4


# ---------------------------------------------------------------------------- fingerprint / live
@pytest.mark.parametrize("stage", STAGES)
def test_fingerprint_mismatch_blocks_every_stage(base: Path, stage: str):
    tested = CFG.with_changes(reward_risk=2.5)
    record = valid_record(base, tested)
    assert check(base, record, stage, tested) == []
    decisions = check(base, record, stage, CFG)  # promoting a different config
    assert blocking_rules(decisions) == {ADOPT_FINGERPRINT}
    assert "not the one that was tested" in decisions[0].reason


def test_missing_fingerprint_or_variant_blocks(base: Path):
    record = dataclasses.replace(valid_record(base), config_fingerprint="")
    assert blocking_rules(check(base, record, BACKTEST)) == {ADOPT_FINGERPRINT}
    record = dataclasses.replace(valid_record(base), variant=" ")
    assert ADOPT_RECORD in blocking_rules(check(base, record, LIVE))


@pytest.mark.parametrize(
    "changes, needle",
    [
        ({"regime_filter": False}, "the R4 regime filter is off"),
        ({"stop_fill_wick_k": 0.5}, "stop_fill_wick_k is 0.5"),
        ({"regime_filter": False, "stop_fill_wick_k": 1.0}, "is off and stop_fill_wick_k is 1"),
    ],
)
def test_test_only_config_never_goes_past_walk_forward(base: Path, changes, needle):
    """D1: regime-off and stop-fill-stress configs are blocked from HUMAN_REVIEW on
    (ADOPT_test_only), and D3 treats them as context variants (ADOPT_holdout), even with
    every piece of evidence in place and their own TEST look in the ledger."""
    test_only = CFG.with_changes(**changes)
    assert test_only.is_test_only
    record = valid_record(base, test_only)
    assert check(base, record, BACKTEST, test_only) == []
    assert check(base, record, WALK_FORWARD, test_only) == []
    for stage in LATER_THAN_WF:
        decisions = check(base, record, stage, test_only)
        assert blocking_rules(decisions) == {ADOPT_TEST_ONLY, ADOPT_HOLDOUT}, stage
        by_rule = {d.rule: d.reason for d in decisions}
        assert needle in by_rule[ADOPT_TEST_ONLY]
        assert "never go past WALK_FORWARD" in by_rule[ADOPT_TEST_ONLY]
        assert "context variant" in by_rule[ADOPT_HOLDOUT]


def test_live_enabled_before_testnet_ended_blocks(base: Path):
    early = mutate(valid_record(base), "live", enabled_utc="2024-04-10T00:00:00Z")
    assert blocking_rules(check(base, early, LIVE)) == {ADOPT_LIVE}
    garbage = mutate(valid_record(base), "live", enabled_utc="soon")
    assert blocking_rules(check(base, garbage, LIVE)) == {ADOPT_LIVE}
    later = mutate(valid_record(base), "live", enabled_utc="2024-04-16T00:00:00Z")
    assert check(base, later, LIVE) == []


def test_unknown_stage_raises(base: Path):
    with pytest.raises(ValueError, match="unknown adoption stage"):
        check(base, valid_record(base), "PAPER")
    with pytest.raises(ValueError):
        check(base, valid_record(base), "live")
    with pytest.raises(ValueError, match="unknown adoption stage"):
        unverified_notes(valid_record(base), "PAPER")


def test_require_stage_raises_with_every_decision(base: Path):
    record = mutate(valid_record(base), "testnet", end_utc="2024-04-14T00:00:00Z")
    with pytest.raises(AdoptionBlocked) as info:
        require_stage(record, LIVE, CFG, NOW, base)
    assert info.value.stage == LIVE
    assert [d.rule for d in info.value.decisions] == [ADOPT_TESTNET]
    assert "ADOPT_testnet" in str(info.value)


def test_rule_ids_cover_every_stage():
    assert set(STAGE_RULES) == set(STAGES)
    assert set(STAGE_RULES.values()) | {
        ADOPT_RECORD,
        ADOPT_FINGERPRINT,
        ADOPT_PROVENANCE,
        ADOPT_HOLDOUT,
        ADOPT_TEST_ONLY,
    } == set(ADOPTION_RULE_IDS)


def test_adoption_rule_ids_agree_with_config_rule_ids():
    adopt_ids = {k: v for k, v in RULE_IDS.items() if k.startswith("ADOPT_")}
    assert dict(ADOPTION_RULE_IDS) == adopt_ids
    assert list(ADOPTION_RULE_IDS) == list(adopt_ids)  # same order as the config listing
    assert {ADOPT_PROVENANCE, ADOPT_HOLDOUT, ADOPT_TEST_ONLY} <= set(RULE_IDS)


def test_module_split_keeps_every_public_name_importable_from_adoption():
    from research.trendbot import adoption_evidence, adoption_record, adoption_stages

    assert adoption.check_promotion.__module__ == "research.trendbot.adoption"
    assert adoption.AdoptionRecord is adoption_record.AdoptionRecord
    assert adoption.recompute_walk_forward is adoption_evidence.recompute_walk_forward
    assert adoption.ROBUST_MIN_N == adoption_stages.ROBUST_MIN_N == 30
    assert all(hasattr(adoption, name) for name in adoption.__all__)
    assert not hasattr(adoption, "route_label_params")  # D1: no signature introspection


# ---------------------------------------------------------------------------- C3: cycle-1 forgery
def forged_cycle1_record(root: Path) -> Path:
    """The record that passed LIVE in gate cycle 1: typed ROBUST on 1 + 1 trades, a 6-byte
    'hello' report, a 2-of-2 'review', and a clean testnet journal. No C3 evidence at all."""
    (root / "REPORT.md").write_bytes(b"hello\n")
    write_journal(default_world().journal, root / "trades.csv")
    data = {
        "variant": "base",
        "config_fingerprint": config_fingerprint(CFG),
        "model_fingerprint": None,
        "backtest": {"report_path": "REPORT.md", "completed_utc": "2024-03-01T00:00:00Z"},
        "walk_forward": {
            "label": "ROBUST",
            "dd_ok": True,
            "split_utc": "2023-01-01T00:00:00Z",
            "train_n": 1,
            "test_n": 1,
            "test_avg_r": 0.01,
            "report_path": "REPORT.md",
        },
        "human_review": {
            "reviewer": "me",
            "date_utc": "2024-03-10T00:00:00Z",
            "trades_reviewed": 2,
            "trades_total": 2,
            "approved": True,
            "notes": None,
        },
        "testnet": {
            "exchange": "binance-testnet",
            "start_utc": "2024-04-01T00:00:00Z",
            "end_utc": "2024-04-15T00:00:00Z",
            "trades": 3,
            "rule_violations": 0,
            "journal_path": "trades.csv",
            "notes": None,
        },
        "live": {"enabled_utc": None},
    }
    path = root / "adoption_base.json"
    path.write_text(json.dumps(data, indent=2))
    return path


def test_cycle1_forged_record_is_blocked(tmp_path: Path, capsys):
    path = forged_cycle1_record(tmp_path)
    assert (tmp_path / "REPORT.md").stat().st_size == 6
    record = load_record(path)
    for stage in LATER_THAN_WF:
        decisions = check(tmp_path, record, stage)
        rules = blocking_rules(decisions)
        assert {ADOPT_PROVENANCE, ADOPT_BACKTEST, ADOPT_WALK_FORWARD, ADOPT_HOLDOUT} <= rules
        text = reasons(decisions)
        assert "report_sha256 is missing" in text
        assert "provenance is missing" in text
        assert "at least 30 trades in each window" in text
        assert "train_journal_path is missing" in text
        assert "ledger_path is missing" in text
        if stage != HUMAN_REVIEW:
            assert ADOPT_HUMAN_REVIEW in rules and "review_path is missing" in text
        if stage == LIVE:
            assert "testnet.journal_sha256 is missing" in text
            assert "testnet.candles is missing" in text
    assert main(["check", "--record", str(path), "--stage", LIVE, "--now", NOW_ISO]) == 1
    assert capsys.readouterr().out.startswith("BLOCKED")


def test_forgery_with_hashes_and_one_trade_windows_is_still_blocked(tmp_path: Path):
    """Even with every file hash-bound and min_train = min_test = 1, a typed ROBUST on 1 + 1
    trades is refused: metrics.label itself now calls such a sample UNTESTED (ROBUST needs 30
    trades in each window whatever min_* say), so the typed label contradicts its evidence,
    and the adoption gate independently demands 30 + 30 for ROBUST."""
    train = [closed_trade(1, TRAIN_START, True)]
    test = [closed_trade(2, SPLIT + DAY_MS, True)]
    record = build_evidence(tmp_path, train=train, test=test)
    record = mutate(record, "walk_forward", min_train=1, min_test=1)
    params = record.walk_forward.label_params
    assert recompute_walk_forward(train, test, CFG, 1, 1, params).label == "UNTESTED"
    for stage in LATER_THAN_WF:
        decisions = check(tmp_path, record, stage)
        assert blocking_rules(decisions) == {ADOPT_WALK_FORWARD}, stage
        text = reasons(decisions)
        assert "needs at least 30 trades in each window" in text
        assert "walk_forward.label is 'ROBUST' but metrics.label" in text and "'UNTESTED'" in text


# ---------------------------------------------------------------------------- C3/D2: provenance
@pytest.mark.parametrize(
    "provenance, needle",
    [
        ("synthetic:planted:1", "never evidence about real markets"),
        ("SYNTHETIC:null:7", "never evidence about real markets"),
        (None, "provenance is missing"),
        ("  ", "provenance is missing"),
        ("real-ish", "only 'real'"),
        ("unverified-csv", "an unverified CSV, not an exchange download"),
    ],
)
def test_synthetic_or_missing_provenance_blocks_after_walk_forward(
    base: Path, provenance: str | None, needle: str
):
    record = dataclasses.replace(valid_record(base), provenance=provenance)
    assert check(base, record, WALK_FORWARD) == []  # synthetic worlds may be walk-forwarded
    for stage in LATER_THAN_WF:
        decisions = check(base, record, stage)
        assert blocking_rules(decisions) == {ADOPT_PROVENANCE}, stage
        assert len(decisions) == 1 and needle in decisions[0].reason


@pytest.mark.parametrize("data_files", [None, {}])
def test_real_provenance_needs_data_file_hashes(base: Path, data_files):
    record = dataclasses.replace(valid_record(base), data_files=data_files)
    assert check(base, record, WALK_FORWARD) == []
    for stage in LATER_THAN_WF:
        decisions = check(base, record, stage)
        assert blocking_rules(decisions) == {ADOPT_PROVENANCE}
        assert "data_files is missing or empty" in decisions[0].reason


def test_real_provenance_without_a_manifest_is_an_unverified_csv(base: Path):
    """D2: 'real' past WALK_FORWARD needs the manifest that verifies every data file."""
    record = dataclasses.replace(valid_record(base), manifest=None)
    assert check(base, record, WALK_FORWARD) == []
    for stage in LATER_THAN_WF:
        decisions = check(base, record, stage)
        assert blocking_rules(decisions) == {ADOPT_PROVENANCE}, stage
        assert len(decisions) == 1
        assert "no manifest is recorded" in decisions[0].reason
        assert "an unverified CSV, not an exchange download" in decisions[0].reason


def rewrite_manifest(root: Path, record: AdoptionRecord, edit) -> AdoptionRecord:
    """Edit manifest.json in place, then re-record its hash (a deliberate laundering act)."""
    path = root / MANIFEST
    data = json.loads(path.read_text())
    edit(data)
    path.write_text(json.dumps(data))
    return dataclasses.replace(record, manifest=FileRef(MANIFEST, sha256_file(path)))


def test_manifest_must_verify_every_data_file(tmp_path: Path):
    record = build_evidence(tmp_path)
    assert check(tmp_path, record, HUMAN_REVIEW) == []

    def wrong_sha(data):
        data["files"][0]["sha256"] = "0" * 64

    def unlisted(data):
        data["files"] = []

    for edit, needle in (
        (wrong_sha, "does not match the manifest"),
        (unlisted, "not listed with a symbol in the manifest"),
    ):
        edited = rewrite_manifest(tmp_path, record, edit)
        decisions = check(tmp_path, edited, HUMAN_REVIEW)
        assert blocking_rules(decisions) == {ADOPT_PROVENANCE}, needle
        assert needle in reasons(decisions)
        assert "an unverified CSV, not an exchange download" in reasons(decisions)
        assert check(tmp_path, edited, WALK_FORWARD) == []


def test_manifest_hash_mismatch_and_foreign_directory_block(tmp_path: Path):
    record = build_evidence(tmp_path)
    (tmp_path / MANIFEST).write_text((tmp_path / MANIFEST).read_text() + " ")
    decisions = check(tmp_path, record, WALK_FORWARD)  # a recorded manifest is always bound
    assert blocking_rules(decisions) == {ADOPT_PROVENANCE} and len(decisions) == 1
    assert "manifest.path" in decisions[0].reason and "not the evidence" in decisions[0].reason
    record = build_evidence(tmp_path)
    elsewhere = tmp_path / "elsewhere" / "BTC_USDT-4h.csv"
    elsewhere.parent.mkdir()
    elsewhere.write_bytes((tmp_path / DATA_FILE).read_bytes())  # same bytes, other directory
    moved = dataclasses.replace(record, data_files={str(elsewhere): sha256_file(elsewhere)})
    decisions = check(tmp_path, moved, HUMAN_REVIEW)
    assert ADOPT_PROVENANCE in blocking_rules(decisions)
    assert "is not in the manifest's directory" in reasons(decisions)
    renamed = tmp_path / "data" / "other.json"
    renamed.write_bytes((tmp_path / MANIFEST).read_bytes())
    other = dataclasses.replace(record, manifest=FileRef("data/other.json", sha256_file(renamed)))
    assert "not the data directory's manifest.json" in reasons(check(tmp_path, other, LIVE))


def test_data_and_events_file_hashes_are_checked_from_walk_forward_on(tmp_path: Path):
    record = build_evidence(tmp_path)
    data = tmp_path / DATA_FILE
    data.write_text(data.read_text().replace("100.0", "100.5", 1))
    for stage in (WALK_FORWARD, *LATER_THAN_WF):
        decisions = check(tmp_path, record, stage)
        assert blocking_rules(decisions) == {ADOPT_PROVENANCE}
        assert len(decisions) == 1  # the manifest and the C5 recompute defer to this reason
        assert "not the evidence that was recorded" in decisions[0].reason
    assert check(tmp_path, record, BACKTEST) == []
    record = build_evidence(tmp_path)
    (tmp_path / EVENTS).unlink()
    decisions = check(tmp_path, record, WALK_FORWARD)
    assert blocking_rules(decisions) == {ADOPT_PROVENANCE}
    assert "events_file.path" in decisions[0].reason and "does not exist" in decisions[0].reason
    bad = dataclasses.replace(record, events_file=FileRef(EVENTS, None))
    assert "events_file.sha256 is missing" in reasons(check(tmp_path, bad, WALK_FORWARD))


# ---------------------------------------------------------------------------- C3: report hash
def test_report_hash_mismatch_blocks(tmp_path: Path):
    record = build_evidence(tmp_path)
    (tmp_path / BT_REPORT).write_text("# backtest report (edited after recording)\n")
    for stage in (WALK_FORWARD, *LATER_THAN_WF):
        decisions = check(tmp_path, record, stage)
        assert blocking_rules(decisions) == {ADOPT_BACKTEST}, stage
        assert "backtest.report_path" in decisions[0].reason
        assert "not the evidence that was recorded" in decisions[0].reason


# ---------------------------------------------------------------------------- C3: journals
@pytest.mark.parametrize("journal", [TRAIN_JOURNAL, TEST_JOURNAL])
def test_journal_hash_mismatch_blocks(tmp_path: Path, journal: str):
    record = build_evidence(tmp_path)
    train, test = wf_trades()
    doctored = [dataclasses.replace(t, r_multiple=2.0, exit_reason=EXIT_TP) for t in train]
    write_journal(doctored if journal == TRAIN_JOURNAL else test[:-1], tmp_path / journal)
    for stage in LATER_THAN_WF:
        decisions = check(tmp_path, record, stage)
        assert blocking_rules(decisions) == {ADOPT_WALK_FORWARD}, stage
        assert len(decisions) == 1
        assert "not the evidence that was recorded" in decisions[0].reason
        assert journal.split("/")[-1].split(".")[0] + "_journal_path" in decisions[0].reason


def test_typed_label_differs_from_recomputed_blocks(tmp_path: Path):
    record = build_evidence(tmp_path, test=weak_test_trades())  # typed ROBUST, evidence UNTESTED
    assert record.walk_forward.label == "ROBUST" and record.walk_forward.test_avg_r > 0
    for stage in LATER_THAN_WF:
        decisions = check(tmp_path, record, stage)
        assert blocking_rules(decisions) == {ADOPT_WALK_FORWARD}, stage
        assert len(decisions) == 1
        assert "walk_forward.label is 'ROBUST' but metrics.label" in decisions[0].reason
        assert "does not match its evidence" in decisions[0].reason
    honest = mutate(record, "walk_forward", label="UNTESTED")
    assert "only ROBUST may proceed" in reasons(check(tmp_path, honest, HUMAN_REVIEW))


@pytest.mark.parametrize(
    "changes, needle",
    [
        ({"test_avg_r": 1.2}, "walk_forward.test_avg_r is 1.2 but the journal gives"),
        ({"train_avg_r": 5.0}, "walk_forward.train_avg_r is 5.0 but the journal gives"),
        ({"test_n": N_TEST + 1}, f"test_n is {N_TEST + 1} but the journal holds {N_TEST}"),
        ({"train_n": 40}, f"train_n is 40 but the journal holds {N_TRAIN}"),
        ({"min_test": 31}, "walk_forward.label is 'ROBUST' but metrics.label"),
    ],
)
def test_typed_numbers_must_match_the_recomputed_ones(base: Path, changes, needle):
    record = mutate(valid_record(base), "walk_forward", **changes)
    decisions = check(base, record, HUMAN_REVIEW)
    assert blocking_rules(decisions) == {ADOPT_WALK_FORWARD}
    assert needle in reasons(decisions)
    assert ADOPT_WALK_FORWARD in blocking_rules(check(base, record, LIVE))


def test_typed_dd_ok_must_match_the_recomputed_drawdown_verdict(base: Path):
    record = with_params(valid_record(base), max_dd_pct=0.001)  # a limit the TEST DD exceeds
    decisions = check(base, record, HUMAN_REVIEW)
    assert blocking_rules(decisions) == {ADOPT_WALK_FORWARD} and len(decisions) == 1
    assert "walk_forward.dd_ok is True but metrics.dd_check" in decisions[0].reason


def test_avg_r_within_tolerance_passes(base: Path):
    record = valid_record(base)
    nudged = mutate(record, "walk_forward", test_avg_r=record.walk_forward.test_avg_r + 1e-10)
    assert check(base, nudged, LIVE) == []


# ---------------------------------------------------------------------------- D1 pinned label rule
def _mtm(record: AdoptionRecord) -> float:
    return record.walk_forward.label_params["mtm_max_dd_pct"]


def _p95(record: AdoptionRecord) -> float:
    return record.walk_forward.label_params["train_dd_p95_pct"]


D1_CASES = [
    ("m_1", {"m": 1}, (), "pinned to metrics.M_CANDIDATES = 4"),
    ("alpha_half", {"alpha": 0.5}, (), "pinned to metrics.ALPHA = 0.05"),
    ("n_boot_100", {"n_boot": 100}, (), "at least metrics.N_BOOT = 4000"),
    ("seed_8", {"seed": 8}, (), "pinned to walkforward.SUMMARY_SEED = 7"),
    ("extra_key", {"min_train": 1}, (), "outside the closed schema"),
    ("extra_unknown", {"tuned": True}, (), "outside the closed schema"),
    ("no_mtm", {}, ("mtm_max_dd_pct",), "lacks ['mtm_max_dd_pct']"),
    ("no_p95", {}, ("train_dd_p95_pct",), "required from HUMAN_REVIEW on"),
    ("null_mtm", {"mtm_max_dd_pct": None}, (), "mtm_max_dd_pct must be a number"),
    ("bool_m", {"m": True}, (), "label_params.m must be a positive integer"),
    ("float_m", {"m": 4.0}, (), "label_params.m must be a positive integer"),
    ("zero_max_dd", {"max_dd_pct": 0}, (), "max_dd_pct must be a positive number"),
]


@pytest.mark.parametrize(
    "changes, drop, needle", [c[1:] for c in D1_CASES], ids=[c[0] for c in D1_CASES]
)
def test_label_params_are_a_pinned_closed_schema(base: Path, changes, drop, needle):
    record = with_params(valid_record(base), drop, **changes)
    assert check(base, record, WALK_FORWARD) == []  # the WALK_FORWARD stage itself may start
    for stage in LATER_THAN_WF:
        decisions = check(base, record, stage)
        assert blocking_rules(decisions) == {ADOPT_WALK_FORWARD}, (stage, decisions)
        assert needle in reasons(decisions)
        assert "CONTRACT v4 D1" in reasons(decisions)


def test_lowered_mtm_or_raised_p95_blocks(base: Path):
    """D1: the C5 inputs are RECOMPUTED (TRAIN journal / hash-bound candles), not trusted."""
    good = valid_record(base)
    for name, value in (
        ("mtm_max_dd_pct", _mtm(good) - 0.01),
        ("mtm_max_dd_pct", _mtm(good) + 2e-9),
        ("train_dd_p95_pct", _p95(good) + 1.0),
        ("train_dd_p95_pct", _p95(good) - 2e-9),
    ):
        record = with_params(good, **{name: value})
        for stage in LATER_THAN_WF:
            decisions = check(base, record, stage)
            assert blocking_rules(decisions) == {ADOPT_WALK_FORWARD}, (name, stage)
            assert len(decisions) == 1
            reason = decisions[0].reason
            assert f"label_params.{name} is {value!r}" in reason
            assert "does not match its evidence" in reason and "tolerance 1e-09" in reason
    within = with_params(good, mtm_max_dd_pct=_mtm(good) + 5e-10)
    assert check(base, within, LIVE) == []


def test_mtm_is_recomputed_from_the_candles_only_for_real_provenance(tmp_path: Path):
    record = build_evidence(tmp_path)
    good = _mtm(record)
    assert good > 0
    synthetic = dataclasses.replace(record, provenance="synthetic:planted:1")
    lowered = with_params(synthetic, mtm_max_dd_pct=good / 2)
    rules = blocking_rules(check(tmp_path, lowered, HUMAN_REVIEW))
    assert rules == {ADOPT_PROVENANCE}  # not recomputed: provenance already blocks
    assert any("mark-to-market" in n for n in unverified_notes(lowered, HUMAN_REVIEW))
    assert not any("mark-to-market" in n for n in unverified_notes(record, HUMAN_REVIEW))
    # a TEST pair without a candle file cannot be recomputed
    eth = [dataclasses.replace(t, pair=ETH) for t in wf_trades()[1]]
    wrong_pair = build_evidence(tmp_path / "eth", test=eth)
    decisions = check(tmp_path / "eth", wrong_pair, HUMAN_REVIEW)
    assert "holds no candle file for ETH/USDT" in reasons(decisions)


def test_label_params_are_passed_by_name_not_by_signature():
    """recompute_walk_forward calls metrics with explicit keyword arguments of the closed
    schema (CONTRACT v4 D1): the result equals the direct calls."""
    train, test = wf_trades()
    params = label_params_for(train, test, wf_candles(test))
    v = recompute_walk_forward(train, test, CFG, 30, 30, params)
    kw = {k: params[k] for k in ("seed", "n_boot", "m", "alpha")}
    s_train = metrics.summarize(train, CFG.starting_capital, **kw)
    s_test = metrics.summarize(test, CFG.starting_capital, **kw)
    assert v.train == s_train and v.test == s_test
    assert (v.label, v.label_reason) == metrics.label(
        s_train, s_test, min_train=30, min_test=30, m=params["m"], alpha=params["alpha"]
    )
    assert (v.dd_ok, v.dd_reason) == metrics.dd_check(
        s_test,
        max_dd_pct=params["max_dd_pct"],
        train_dd_p95_pct=params["train_dd_p95_pct"],
        mtm_max_dd_pct=params["mtm_max_dd_pct"],
    )
    for bad in ({}, {**params, "x": 1}, {k: v for k, v in params.items() if k != "seed"}):
        with pytest.raises(ValueError, match="label_params"):
            recompute_walk_forward(train, test, CFG, 30, 30, bad)


@pytest.mark.parametrize(
    "train_change, needle",
    [
        ({"signal_ts": SPLIT + DAY_MS}, "wrong side of walk_forward.split_utc"),
        ({"variant": "rr3"}, "are not of variant 'base'"),
        ({"exit_ts": None, "r_multiple": None}, "without an exit or R"),
        ({"trade_id": 2}, "repeats trade ids #2"),
    ],
)
def test_walk_forward_journal_content_is_checked(tmp_path: Path, train_change, needle):
    record = build_evidence(tmp_path)
    train, _test = wf_trades()
    train[0] = dataclasses.replace(train[0], **train_change)
    write_journal(train, tmp_path / TRAIN_JOURNAL)  # re-recorded, so the hash matches
    record = rehash(record, tmp_path, "walk_forward", "train_journal_path")
    decisions = check(tmp_path, record, HUMAN_REVIEW)
    assert ADOPT_WALK_FORWARD in blocking_rules(decisions)
    assert needle in reasons(decisions)


def test_unreadable_walk_forward_journal_blocks(tmp_path: Path):
    record = build_evidence(tmp_path)
    (tmp_path / TEST_JOURNAL).write_text("not,a,journal\n1,2,3\n")
    record = rehash(record, tmp_path, "walk_forward", "test_journal_path")
    decisions = check(tmp_path, record, HUMAN_REVIEW)
    assert blocking_rules(decisions) == {ADOPT_WALK_FORWARD}
    assert "could not be read as a trade journal" in reasons(decisions)


# ---------------------------------------------------------------------------- D3 holdout ledger
OTHER_FPS = [f"{d}" * 64 for d in "123456789"]


def own_windows(root: Path, record: AdoptionRecord) -> list[tuple[str, str, int, int]]:
    look = read_ledger(root / record.ledger_path)[0]
    return [(e.pair, e.file_sha256, e.test_start_ts, e.test_end_ts) for e in look.pairs]


def own_window(root: Path, record: AdoptionRecord) -> tuple[str, str, int, int]:
    return own_windows(root, record)[0]


def append_ledger(path: Path, lines: list[str]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.writelines(f"{line}\n" for line in lines)


def run_lines(fps: list[str], pairs: list[tuple[str, str, int, int]], run_utc: str) -> list[str]:
    return [ledger_line(fp, None, pairs, run_utc=run_utc) for fp in fps]


def test_holdout_ledger_counts_distinct_looks_per_pair(tmp_path: Path):
    """D3: a second run on the same data (a --pairs subset) with 4 new candidates spends the
    shared BTC TEST window, so a record of EITHER run is blocked."""
    shared = tmp_path / "shared_looks.jsonl"
    rec1 = build_evidence(tmp_path / "run1", pairs=(BTC, ETH, "BNB/USDT"))
    cfg2 = CFG.with_changes(fee_rate=0.002)
    rec2 = build_evidence(tmp_path / "run2", cfg2, pairs=(BTC, ETH))  # --pairs BTC/USDT ETH/USDT
    three = own_windows(tmp_path / "run1", rec1)
    two = own_windows(tmp_path / "run2", rec2)
    assert two == three[:2]  # the same data files, the same TEST windows
    _pair, _sha, start, end = three[0]
    # run 1: rec1's config + 3 other pre-registered candidates, on BTC, ETH and BNB
    append_ledger(shared, [ledger_line(config_fingerprint(CFG), None, three)])
    append_ledger(shared, run_lines(OTHER_FPS[:3], three, "2024-03-01T00:00:00Z"))
    rec1 = dataclasses.replace(rec1, ledger_path=str(shared))
    rec2 = dataclasses.replace(rec2, ledger_path=str(shared))
    assert check(tmp_path / "run1", rec1, LIVE) == []  # 4 looks: at most m
    assert len(distinct_looks(read_ledger(shared), BTC, start, end)) == 4
    # run 2 (--pairs BTC ETH): rec2's config + 3 more new candidates on the SAME TEST window
    append_ledger(shared, [ledger_line(config_fingerprint(cfg2), None, two)])
    append_ledger(shared, run_lines(OTHER_FPS[3:6], two, "2024-03-02T00:00:00Z"))
    for root, record, cfg in ((tmp_path / "run1", rec1, CFG), (tmp_path / "run2", rec2, cfg2)):
        decisions = check(root, record, HUMAN_REVIEW, cfg)
        assert blocking_rules(decisions) == {ADOPT_HOLDOUT}
        assert [d.reason.split(":")[0] for d in decisions] == [BTC, ETH]  # BNB: still 4 looks
        for d in decisions:
            assert "the holdout ledger holds 8 distinct (config, model) TEST looks" in d.reason
            assert "more than m = 4" in d.reason and "starting at or after" in d.reason
        assert check(root, record, WALK_FORWARD, cfg) == []


def test_identical_rerun_is_not_an_extra_look(tmp_path: Path):
    record = build_evidence(tmp_path)
    window = own_window(tmp_path, record)
    ledger = tmp_path / LEDGER
    append_ledger(ledger, run_lines(OTHER_FPS[:3], [window], "2024-03-01T00:00:00Z"))
    for day in ("02", "03", "04"):  # the same 4 candidates re-run three more times
        rerun = run_lines(OTHER_FPS[:3], [window], f"2024-03-{day}T00:00:00Z")
        own = ledger_line(config_fingerprint(CFG), None, [window], run_utc=f"2024-03-{day}")
        append_ledger(ledger, [own, *rerun])
    assert len(read_ledger(ledger)) == 16
    assert check(tmp_path, record, LIVE) == []


def test_later_non_overlapping_test_window_is_clean(tmp_path: Path):
    record = build_evidence(tmp_path)
    pair, _sha, start, end = own_window(tmp_path, record)
    ledger = tmp_path / LEDGER
    earlier = [(pair, "a" * 64, start - 200 * DAY_MS, start)]  # ends exactly where TEST starts
    later = [(pair, "b" * 64, end, end + 90 * DAY_MS)]  # starts at the latest test_end_ts
    append_ledger(ledger, run_lines(OTHER_FPS[:5], earlier, "2023-01-01T00:00:00Z"))
    append_ledger(ledger, run_lines(OTHER_FPS[5:9], later, "2024-06-01T00:00:00Z"))
    assert check(tmp_path, record, LIVE) == []
    # one millisecond of overlap makes those looks count against this TEST window
    overlap = [(pair, "b" * 64, end - 1, end + 90 * DAY_MS)]
    append_ledger(ledger, run_lines(OTHER_FPS[:4], overlap, "2024-06-02T00:00:00Z"))
    assert "holds 5 distinct" in reasons(check(tmp_path, record, HUMAN_REVIEW))


@pytest.mark.parametrize(
    "line, needle",
    [
        (lambda fp, w: ledger_line("f" * 64, None, [w]), "context variant"),
        (lambda fp, w: ledger_line(fp, "ab" * 32, [w]), "context variant"),
        (lambda fp, w: ledger_line(fp, None, [w], split="2023-02-01T00:00:00Z"), "context variant"),
        (lambda fp, w: ledger_line(fp, None, [(w[0], "c" * 64, w[2], w[3])]), "context variant"),
        (lambda fp, w: "{not json", "could not be read"),
        (lambda fp, w: json.dumps({"variant": "base"}), "could not be read"),
        (lambda fp, w: ledger_line(fp, None, [(w[0], w[1], w[3], w[2])]), "could not be read"),
    ],
)
def test_record_without_its_own_ledger_look_is_a_context_variant(tmp_path: Path, line, needle):
    record = build_evidence(tmp_path)
    window = own_window(tmp_path, record)
    write_text(tmp_path / LEDGER, line(config_fingerprint(CFG), window) + "\n")
    decisions = check(tmp_path, record, HUMAN_REVIEW)
    assert blocking_rules(decisions) == {ADOPT_HOLDOUT}
    assert len(decisions) == 1 and needle in decisions[0].reason
    assert check(tmp_path, record, WALK_FORWARD) == []


def test_gate_parses_the_ledger_run_research_writes(tmp_path: Path):
    """The gate parses the ledger independently of its writer (research.trendbot.ledger), so
    this pins the shared line format and the half-open overlap rule."""
    from research.trendbot import ledger as writer

    path = tmp_path / "looks.jsonl"
    window = writer.PairWindow(BTC, "a" * 64, SPLIT, SPLIT + 90 * DAY_MS)
    looks = [
        writer.Look("2024-03-01T00:00:00Z", ("--x",), "base", fp, None, SPLIT_ISO, (window,))
        for fp in OTHER_FPS[:3]
    ]
    looks.append(replace(looks[0], model_fingerprint="ab" * 32))  # a refitted model is new
    looks.append(replace(looks[0], run_utc="2024-03-02T00:00:00Z"))  # an identical re-run
    writer.append_looks(path, looks)
    parsed = read_ledger(path)
    assert [(p.key, p.split_ts) for p in parsed] == [(w.identity, SPLIT) for w in looks]
    for start, end in ((SPLIT, SPLIT + DAY_MS), (SPLIT + 90 * DAY_MS, SPLIT + 99 * DAY_MS)):
        ours = distinct_looks(parsed, BTC, start, end)
        assert ours == writer.distinct_looks(writer.read_ledger(path), BTC, start, end)
    assert len(distinct_looks(parsed, BTC, SPLIT, SPLIT + DAY_MS)) == 4


def test_label_param_keys_agree_with_the_walk_forward_writer():
    from research.trendbot import walkforward

    assert LABEL_PARAM_KEYS == walkforward.LABEL_PARAM_KEYS


def test_missing_ledger_path_blocks(base: Path):
    record = dataclasses.replace(valid_record(base), ledger_path=None)
    decisions = check(base, record, HUMAN_REVIEW)
    assert blocking_rules(decisions) == {ADOPT_HOLDOUT}
    assert "ledger_path is missing" in decisions[0].reason and "D3" in decisions[0].reason


# ---------------------------------------------------------------------------- C3: review pack
def test_missing_review_pack_blocks(tmp_path: Path):
    record = build_evidence(tmp_path)
    (tmp_path / REVIEW_CSV).unlink()
    for stage in LATER_THAN_REVIEW:
        decisions = check(tmp_path, record, stage)
        assert blocking_rules(decisions) == {ADOPT_HUMAN_REVIEW}, stage
        assert "human_review.review_path" in decisions[0].reason
        assert "does not exist" in decisions[0].reason
    assert check(tmp_path, record, HUMAN_REVIEW) == []


def test_review_pack_hash_mismatch_blocks(tmp_path: Path):
    record = build_evidence(tmp_path)
    fill_review(tmp_path / REVIEW_CSV, {("TEST", N_TRAIN + 1): REVIEW_OK_NO})  # after sign-off
    for stage in LATER_THAN_REVIEW:
        decisions = check(tmp_path, record, stage)
        assert blocking_rules(decisions) == {ADOPT_HUMAN_REVIEW}
        assert len(decisions) == 1 and "not the evidence that was recorded" in decisions[0].reason


def test_blank_reviewer_ok_row_blocks(tmp_path: Path):
    record = build_evidence(tmp_path, verdicts={("TRAIN", 5): REVIEW_OK_BLANK})
    for stage in LATER_THAN_REVIEW:
        decisions = check(tmp_path, record, stage)
        assert blocking_rules(decisions) == {ADOPT_HUMAN_REVIEW}, stage
        assert len(decisions) == 1
        assert "1 review rows (TRAIN #5) have no reviewer_ok verdict" in decisions[0].reason


def test_one_rejected_row_blocks_and_states_the_policy(tmp_path: Path):
    record = build_evidence(tmp_path, verdicts={("TEST", N_TRAIN + 3): REVIEW_OK_NO})
    assert record.human_review.approved is True  # an "approved" flag cannot outvote an N
    for stage in LATER_THAN_REVIEW:
        decisions = check(tmp_path, record, stage)
        assert blocking_rules(decisions) == {ADOPT_HUMAN_REVIEW}, stage
        assert len(decisions) == 1
        reason = decisions[0].reason
        assert f"rejected 1 trades (TEST #{N_TRAIN + 3})" in reason
        assert "restart the adoption path at BACKTEST" in reason
        assert "TEST data it has never seen" in reason


def test_unparseable_review_pack_blocks(tmp_path: Path):
    record = build_evidence(tmp_path)
    fill_review(tmp_path / REVIEW_CSV, {("TRAIN", 1): "yes"})
    record = rehash(record, tmp_path, "human_review", "review_path")
    decisions = check(tmp_path, record, TESTNET)
    assert blocking_rules(decisions) == {ADOPT_HUMAN_REVIEW}
    assert "could not be read as a review pack" in decisions[0].reason


def test_review_pack_must_describe_the_journal_trades(tmp_path: Path):
    train, test = wf_trades()
    record = build_evidence(tmp_path, train=train, test=test)
    # a pack of other trades: one TEST trade dropped, one TRAIN trade on another pair
    other = [dataclasses.replace(train[0], pair=ETH), *train[1:], *test[:-1]]
    write_review_pack(other, tmp_path / REVIEW_DIR, "other", CFG, split_ts=SPLIT)
    fill_review(tmp_path / REVIEW_CSV)
    record = rehash(record, tmp_path, "human_review", "review_path")
    decisions = check(tmp_path, record, TESTNET)
    assert blocking_rules(decisions) == {ADOPT_HUMAN_REVIEW}
    text = reasons(decisions)
    assert f"has {N_ALL - 1} rows but the walk-forward produced {N_ALL} trades" in text
    assert f"no row for walk-forward trades TEST #{N_ALL}" in text
    assert "review rows TRAIN #1 name a different pair or signal time" in text


def test_review_pack_with_extra_rows_blocks(tmp_path: Path):
    train, test = wf_trades()
    record = build_evidence(tmp_path, train=train, test=test)
    extra = closed_trade(999, SPLIT + 400 * DAY_MS, True)
    write_review_pack([*train, *test, extra], tmp_path / REVIEW_DIR, "extra", CFG, split_ts=SPLIT)
    fill_review(tmp_path / REVIEW_CSV)
    record = rehash(record, tmp_path, "human_review", "review_path")
    text = reasons(check(tmp_path, record, TESTNET))
    assert "has rows TEST #999 that are not trades of the walk-forward journals" in text


# ---------------------------------------------------------------------------- evidence paths
def test_absolute_evidence_paths_are_used_as_is(tmp_path: Path):
    record = build_evidence(tmp_path)
    elsewhere = tmp_path / "elsewhere"

    def absolute(value: str) -> str:
        return str(tmp_path / value)

    rec = dataclasses.replace(
        record,
        data_files={absolute(k): v for k, v in record.data_files.items()},
        events_file=FileRef(absolute(record.events_file.path), record.events_file.sha256),
        manifest=FileRef(absolute(record.manifest.path), record.manifest.sha256),
        ledger_path=absolute(record.ledger_path),
    )
    rec = mutate(rec, "backtest", report_path=absolute(BT_REPORT))
    rec = mutate(
        rec,
        "walk_forward",
        report_path=absolute(WF_REPORT),
        train_journal_path=absolute(TRAIN_JOURNAL),
        test_journal_path=absolute(TEST_JOURNAL),
    )
    rec = mutate(rec, "human_review", review_path=absolute(REVIEW_CSV))
    rec = mutate(
        rec,
        "testnet",
        decisions_path=absolute(DECISIONS),
        candles={absolute(k): v for k, v in rec.testnet.candles.items()},
    )
    partly = rec
    rec = mutate(rec, "testnet", journal_path=absolute(JOURNAL))
    assert check_promotion(rec, LIVE, CFG, NOW, elsewhere) == []
    decisions = check_promotion(partly, LIVE, CFG, NOW, elsewhere)  # one path still relative
    assert blocking_rules(decisions) == {ADOPT_TESTNET}


@pytest.mark.parametrize(
    "missing, rule",
    [
        (BT_REPORT, ADOPT_BACKTEST),
        (WF_REPORT, ADOPT_WALK_FORWARD),
        (TRAIN_JOURNAL, ADOPT_WALK_FORWARD),
        (TEST_JOURNAL, ADOPT_WALK_FORWARD),
        (REVIEW_CSV, ADOPT_HUMAN_REVIEW),
        (JOURNAL, ADOPT_TESTNET),
        (DECISIONS, ADOPT_TESTNET),
        (TESTNET_BTC, ADOPT_TESTNET),
        (DATA_FILE, ADOPT_PROVENANCE),
        (EVENTS, ADOPT_PROVENANCE),
        (MANIFEST, ADOPT_PROVENANCE),
        (LEDGER, ADOPT_HOLDOUT),
    ],
)
def test_missing_evidence_file_blocks(tmp_path: Path, missing: str, rule: str):
    record = build_evidence(tmp_path)
    (tmp_path / missing).unlink()
    decisions = check(tmp_path, record, LIVE)
    assert blocking_rules(decisions) == {rule}
    assert len(decisions) == 1 and "does not exist" in decisions[0].reason


# ---------------------------------------------------------------------------- D6 testnet evidence
def test_consistent_testnet_window_passes_and_replays_exactly(tmp_path: Path):
    """D6: LiveSession replayed over the recorded candles, with the journal's fills and exits
    injected, allows exactly the journal's entries and the decisions log's allowed rows."""
    record = build_evidence(tmp_path)
    assert check(tmp_path, record, LIVE) == []
    world = default_world()
    replay = replay_testnet(
        CFG,
        world.candles,
        world.journal,
        world.decisions,
        None,
        CFG.starting_capital,
        TESTNET_START,
        iso_to_ms(TESTNET_END_ISO),
    )
    keys = {(t.pair, t.signal_ts) for t in world.journal}
    assert replay.allowed == keys == {(d.pair, d.signal_ts) for d in world.decisions if d.allowed}
    assert replay.problems == () and replay.injected_refusals == ()
    assert [(t.trade_id, t.entry_price, t.qty, t.exit_ts, t.pnl) for t in replay.trades] == [
        (t.trade_id, t.entry_price, t.qty, t.exit_ts, t.pnl) for t in world.journal
    ]
    assert len(replay.evaluated) == 14 * 6 + 1  # every candle closing in [start, end]
    assert read_decisions_log(tmp_path / DECISIONS) == world.decisions


def shifted(candles: list[Candle]) -> list[Candle]:
    """Scenario candles moved so candle FIRST_EVALUATED closes at TESTNET_START."""
    shift = TESTNET_START - TF - candles[FIRST_EVALUATED].ts
    return [replace(c, ts=c.ts + shift) for c in candles[:N_TESTNET_CANDLES]]


def test_testnet_r6_r9_violations_block_live(tmp_path: Path):
    """Entries the replay denies block: an ETH buy while a full-size BTC position was open
    (R6), and a BNB buy inside the 24h bench after three stop-losses (R9). Every forged
    trade is fine on its own; the replay denies it and so does the live audit."""
    btc = scenario_candles(BTC, {0: "tp"})
    eth = scenario_candles(ETH, {0: "tp"}, start=50.0)
    honest = drive_testnet({BTC: btc, ETH: eth})
    assert [t.pair for t in honest.journal] == [BTC]  # R6 denied ETH
    forged_eth = replace(drive_testnet({ETH: eth}).journal[0], trade_id=2)
    assert auto_flags(forged_eth, CFG) == []
    decisions = flip(honest.decisions, forged_eth.signal_ts, True, "R7_position_risk")
    world = TestnetWorld({BTC: btc, ETH: eth}, [*honest.journal, forged_eth], decisions)
    record = build_evidence(tmp_path / "r6", world=world)
    assert check(tmp_path / "r6", record, TESTNET) == []  # testnet itself may run
    found = check(tmp_path / "r6", record, LIVE)
    assert blocking_rules(found) == {ADOPT_TESTNET}
    text = reasons(found)
    when = "2024-04-01T20:00:00Z"
    assert f"the journal enters ETH/USDT {when} (replay: R6_correlation_cap)" in text
    assert f"the decisions log allows ETH/USDT {when} (replay: R6_correlation_cap)" in text
    assert "fails invariants.live_journal_violations" in text and "(R6)" in text
    # R9: the honest bot is benched at slot(3) (22h after the third stop-loss filled)
    bench = bench_scenario(CFG)
    bnb = shifted(bench.candles)
    honest = drive_testnet({"BNB/USDT": bnb})
    benched = bnb[slot(3)].ts
    assert [d.rule for d in honest.decisions if d.signal_ts == benched] == ["R9_circuit_breaker"]
    assert [t.exit_reason for t in honest.journal][:3] == [EXIT_SL] * 3
    # the forged slot(3) trade: sized on the equity the bot really had after the three losses
    quiet = [
        replace(c, volume=BASE_VOLUME) if k in (slot(0), slot(1), slot(2), slot(4)) else c
        for k, c in enumerate(bnb)
    ]
    equity = CFG.starting_capital + sum(t.pnl for t in honest.journal[:3])
    alone = drive_testnet({"BNB/USDT": quiet}, CFG.with_changes(starting_capital=equity))
    forged = replace(alone.journal[0], trade_id=99)
    assert forged.signal_ts == benched
    assert not {flag_code(f) for f in auto_flags(forged, CFG)} & RULE_VIOLATION_FLAGS
    decisions = flip(honest.decisions, benched, True, "R7_position_risk")
    world = TestnetWorld({"BNB/USDT": bnb}, [*honest.journal, forged], decisions)
    record = build_evidence(tmp_path / "r9", world=world)
    found = check(tmp_path / "r9", record, LIVE)
    assert blocking_rules(found) == {ADOPT_TESTNET}
    text = reasons(found)
    assert "(replay: R9_circuit_breaker) but the replay does not allow them" in text
    assert "while BNB/USDT was benched" in text and "(R9)" in text


def test_decisions_log_mismatch_blocks(tmp_path: Path):
    record = build_evidence(tmp_path)
    world = default_world()
    t3, t1 = signal_ts_of(3), signal_ts_of(1)
    cases = [
        (flip(world.decisions, t3, False, "R3_volume"), "but the decisions log does not"),
        (flip(world.decisions, t1, True, "R7_position_risk"), "(replay: R3_volume)"),
        ([d for d in world.decisions if d.signal_ts != t3], "(no row in the decisions log)"),
    ]
    for decisions, needle in cases:
        edited = put_testnet(record, tmp_path, decisions=decisions)
        found = check(tmp_path, edited, LIVE)
        assert blocking_rules(found) == {ADOPT_TESTNET}, needle
        assert len(found) == 1 and needle in found[0].reason
        assert "does not reproduce the run" in found[0].reason


def test_missing_or_wrong_testnet_candles_block(tmp_path: Path):
    record = build_evidence(tmp_path)
    for changes, needle in (
        ({"candles": None}, "testnet.candles is missing or empty"),
        ({"candles": {TESTNET_BTC: "0" * 64}}, "not the evidence that was recorded"),
        ({"candles": {"testnet/candles/nope-4h.csv": "0" * 64}}, "does not exist"),
    ):
        decisions = check(tmp_path, mutate(record, "testnet", **changes), LIVE)
        assert blocking_rules(decisions) == {ADOPT_TESTNET} and len(decisions) == 1, needle
        assert needle in decisions[0].reason
    # candles of the wrong pair: nothing to replay the BTC journal on
    eth = scenario_candles(ETH, {}, start=50.0)
    path = tmp_path / TESTNET_CANDLES / "ETH_USDT-4h.csv"
    save_candles_csv(eth, path)
    only_eth = mutate(record, "testnet", candles={str(path): sha256_file(path)})
    text = reasons(check(tmp_path, only_eth, LIVE))
    assert "not evaluated, no candle in the window" in text and "has no candle data" in text
    # a file name that is not data.pair_filename(pair, "4h")
    odd = tmp_path / TESTNET_CANDLES / "btc.csv"
    odd.write_bytes((tmp_path / TESTNET_BTC).read_bytes())
    named = mutate(record, "testnet", candles={str(odd): sha256_file(odd)})
    assert "must be named like data.pair_filename" in reasons(check(tmp_path, named, LIVE))


@pytest.mark.parametrize(
    "edit, needle",
    [
        (lambda js: js[:2], "holds 2 trades but testnet.trades is 3"),
        (lambda js: [js[0], js[1], replace(js[2], variant="rr3")], "not of variant"),
        (lambda js: [js[0], js[1], replace(js[2], risk_pct=2.0)], "risk_above_cap"),
        (
            lambda js: [js[0], js[1], replace(js[2], target=js[2].entry_price * 1.01)],
            "rr_below_min",
        ),
        (
            lambda js: [js[0], js[1], replace(js[2], entry_ts=js[2].signal_ts)],
            "lookahead",
        ),
        (lambda js: [js[0], js[1], replace(js[2], qty=js[2].qty * 2)], "not reproducible"),
    ],
)
def test_testnet_journal_is_cross_checked(tmp_path: Path, edit, needle):
    record = build_evidence(tmp_path)
    edited = put_testnet(record, tmp_path, journal=edit(default_world().journal))
    edited = mutate(edited, "testnet", trades=3)
    decisions = check(tmp_path, edited, LIVE)
    assert blocking_rules(decisions) == {ADOPT_TESTNET}
    assert needle in reasons(decisions), decisions


def test_unreadable_testnet_journal_or_decisions_log_blocks(tmp_path: Path):
    record = build_evidence(tmp_path)
    (tmp_path / JOURNAL).write_text("not,a,journal\n1,2,3\n")
    decisions = check(tmp_path, rehash(record, tmp_path, "testnet", "journal_path"), LIVE)
    assert blocking_rules(decisions) == {ADOPT_TESTNET}
    assert "could not be read as a trade journal" in decisions[0].reason
    record = build_evidence(tmp_path)
    for text, needle in (
        ("pair,signal_ts\nBTC/USDT,1\n", "columns ['allowed', 'rule', 'reason'] are missing"),
        ("pair,signal_ts,allowed,rule,reason\nBTC/USDT,0,yes,R7_position_risk,x\n", "true or"),
        ("pair,signal_ts,allowed,rule,reason\nBTC/USDT,0,true,R99,x\n", "not a config.RULE_IDS"),
        (
            "pair,signal_ts,allowed,rule,reason\nBTC/USDT,0,true,R3_volume,x\n"
            "BTC/USDT,0,false,R3_volume,x\n",
            "repeats line 2",
        ),
    ):
        write_text(tmp_path / DECISIONS, text)
        edited = rehash(record, tmp_path, "testnet", "decisions_path")
        decisions = check(tmp_path, edited, LIVE)
        assert blocking_rules(decisions) == {ADOPT_TESTNET} and len(decisions) == 1
        assert "could not be read as a decisions log" in decisions[0].reason
        assert needle in decisions[0].reason


def test_fill_refusal_is_injected_from_the_decisions_log(tmp_path: Path):
    """An allowed signal the bot could not fill (logged X_capital, no journal entry) replays
    as that refusal; the same signal logged as a mandatory-rule denial is a mismatch."""
    world = default_world()
    last = signal_ts_of(6)  # the last trade: nothing after it depends on its outcome
    journal = [t for t in world.journal if t.signal_ts != last]
    refused = flip(world.decisions, last, False, "X_capital")
    record = build_evidence(tmp_path, world=TestnetWorld(world.candles, journal, refused))
    assert record.testnet.trades == 2
    assert check(tmp_path, record, LIVE) == []
    notes = " ".join(unverified_notes(record, LIVE))
    assert "fill refusal" in notes and "not journaled" in notes
    denied = put_testnet(
        record, tmp_path, decisions=flip(world.decisions, last, False, "R3_volume")
    )
    decisions = check(tmp_path, denied, LIVE)
    assert blocking_rules(decisions) == {ADOPT_TESTNET}
    assert "(log: R3_volume)" in reasons(decisions)


def test_ml_vetoes_are_taken_from_the_decisions_log_only_for_an_ml_record(tmp_path: Path):
    veto_at = scenario_candles(BTC, {})[slot(9)].ts
    candles = {BTC: scenario_candles(BTC, {0: "tp", 3: "sl", 6: "tp"}, extra_spikes=(9,))}

    def veto(pair, row, check_):
        return (row.ts != veto_at, 0.2, "vetoed by the model")

    world = drive_testnet(candles, variant="base+ml", entry_filter=veto)
    assert [d.rule for d in world.decisions if d.signal_ts == veto_at] == ["L_ml_filter"]
    model = "ab" * 32
    record = build_evidence(tmp_path / "ml", variant="base+ml", world=world, model_fp=model)
    assert check(tmp_path / "ml", record, LIVE, model_fingerprint=model) == []
    assert any("L_ml_filter vetoes are taken from" in n for n in unverified_notes(record, LIVE))
    plain_world = TestnetWorld(
        candles, [replace(t, variant="base") for t in world.journal], world.decisions
    )
    plain = build_evidence(tmp_path / "plain", world=plain_world)
    decisions = check(tmp_path / "plain", plain, LIVE)
    assert blocking_rules(decisions) == {ADOPT_TESTNET}
    assert "(log: L_ml_filter)" in reasons(decisions)


def test_converted_bot_journal_feeds_the_testnet_audit(tmp_path: Path):
    """journal.import_external output is what the TESTNET stage audits (made-up bot format;
    a real mapping must be written for the bot's real columns)."""
    world = default_world()
    rows = ["id,sym,opened,px,sl,tp,size,closed,out,why,equity,method"]
    for t in world.journal:
        equity = t.risk_amount / t.risk_pct * 100
        rows.append(
            f"{t.trade_id},{t.pair.replace('/', '')},{t.entry_ts},{t.entry_price!r},{t.stop!r},"
            f"{t.target!r},{t.qty!r},{t.exit_ts},{t.exit_price!r},{t.exit_reason.lower()},"
            f"{equity!r},{t.stop_method}"
        )
    bot = tmp_path / "bot.csv"
    write_text(bot, "\n".join(rows) + "\n")
    columns = ["trade_id", "pair", "entry_ts", "entry_price", "stop", "target", "qty"]
    columns += ["exit_ts", "exit_price", "exit_reason", "equity", "stop_method"]
    mapping = dict(zip(rows[0].split(","), columns, strict=True))
    trades = import_external(
        bot,
        mapping,
        time_format="ms",
        exit_reason_map={"tp": "TP", "sl": "SL"},
        defaults={"variant": "base"},
    )
    assert [(t.pair, t.signal_ts) for t in trades] == [(t.pair, t.signal_ts) for t in world.journal]
    record = build_evidence(tmp_path, world=TestnetWorld(world.candles, trades, world.decisions))
    assert check(tmp_path, record, LIVE) == []
    shifted = [trades[0], replace(trades[1], signal_ts=trades[1].signal_ts - TF)]
    decisions = check(tmp_path, put_testnet(record, tmp_path, journal=shifted), LIVE)
    assert blocking_rules(decisions) == {ADOPT_TESTNET}
    assert "the journal enters" in reasons(decisions)


def write_testnet_events(root: Path, rows: list[str]) -> str:
    path = root / TESTNET_EVENTS
    write_text(path, "time_utc,scope,impact,kind,note\n" + "".join(f"{r}\n" for r in rows))
    return sha256_file(path)


def test_testnet_r5_is_checked_when_the_calendar_is_recorded(tmp_path: Path):
    record = build_evidence(tmp_path)
    # the first testnet trade is decided at 2024-04-02 00:00; an event at 01:00 blocks it
    sha = write_testnet_events(tmp_path, ["2024-04-02T01:00:00Z,ALL,high,macro,CPI"])
    with_events = mutate(record, "testnet", events_path=TESTNET_EVENTS, events_sha256=sha)
    decisions = check(tmp_path, with_events, LIVE)
    assert blocking_rules(decisions) == {ADOPT_TESTNET}
    text = reasons(decisions)
    assert "(R5)" in text and "R5 against testnet.events_path" in text
    assert "the journal enters BTC/USDT 2024-04-01T20:00:00Z (replay: R5_news_blackout)" in text
    assert not any("R5" in n for n in unverified_notes(with_events, LIVE))
    sha = write_testnet_events(tmp_path, ["2024-05-20T12:00:00Z,ALL,high,macro,later"])
    clean = mutate(record, "testnet", events_path=TESTNET_EVENTS, events_sha256=sha)
    assert check(tmp_path, clean, LIVE) == []
    (tmp_path / TESTNET_EVENTS).write_text("time_utc,scope,impact,kind,note\n")
    decisions = check(tmp_path, clean, LIVE)  # calendar edited after recording
    assert blocking_rules(decisions) == {ADOPT_TESTNET}
    assert "not the evidence that was recorded" in decisions[0].reason


def test_r5_denial_without_a_recorded_calendar_is_flagged(tmp_path: Path):
    """A bot that honoured a news blackout needs its calendar recorded: without it the replay
    allows the signal and the difference says no news calendar was recorded."""
    world = default_world()
    first = signal_ts_of(0)
    cpi = NewsEvent(first + TF + HOUR_MS, "ALL", "high", "macro", "CPI")  # 1h after the decision
    blocked = drive_testnet(world.candles, events=[cpi])
    assert first not in {t.signal_ts for t in blocked.journal}
    record = build_evidence(tmp_path, world=blocked)
    decisions = check(tmp_path, record, LIVE)
    assert blocking_rules(decisions) == {ADOPT_TESTNET}
    assert "log: R5_news_blackout, no news calendar recorded" in reasons(decisions)


def test_unreadable_testnet_calendar_blocks(tmp_path: Path):
    record = build_evidence(tmp_path)
    write_text(tmp_path / TESTNET_EVENTS, "when,what\nx,y\n")
    sha = sha256_file(tmp_path / TESTNET_EVENTS)
    record = mutate(record, "testnet", events_path=TESTNET_EVENTS, events_sha256=sha)
    decisions = check(tmp_path, record, LIVE)
    assert blocking_rules(decisions) == {ADOPT_TESTNET}
    assert "could not be read as a news calendar" in decisions[0].reason


def test_unverified_notes_say_what_is_not_checked(base: Path):
    record = valid_record(base)
    assert unverified_notes(record, BACKTEST) == unverified_notes(record, WALK_FORWARD) == []
    review = " ".join(unverified_notes(record, HUMAN_REVIEW))
    assert "do not prove the journals were produced" in review
    assert "cannot authenticate an exchange download" in review and "deliberate act" in review
    assert "append-only and not hash-bound" in review
    live = unverified_notes(record, LIVE)
    joined = " ".join(live)
    assert "R5 (news blackout) was not checked" in joined and "not journal-verifiable" in joined
    assert "about 1 trade per 14 days" in joined and "about 1.0 trades" in joined
    assert "not mainnet" in joined and "execution and rule compliance, not edge" in joined
    assert "typed exchange field" in joined
    for note in live:
        assert_one_sentence(note)


# ---------------------------------------------------------------------------- CLI
def run_check(record_path: Path, stage: str = LIVE, *extra: str) -> int:
    return main(["check", "--record", str(record_path), "--stage", stage, "--now", NOW_ISO, *extra])


def test_cli_init_writes_empty_record_with_default_fingerprint(tmp_path: Path, capsys):
    out = tmp_path / "rec.json"
    assert main(["init", "--variant", "base", "--out", str(out)]) == 0
    assert load_record(out) == empty_record("base")
    assert load_record(out).config_fingerprint == config_fingerprint(StrategyConfig())
    assert "next stage: BACKTEST" in capsys.readouterr().out


def test_cli_init_refuses_to_overwrite(tmp_path: Path, base: Path, capsys):
    out = tmp_path / "rec.json"
    save_record(valid_record(base), out)
    assert main(["init", "--variant", "base", "--out", str(out)]) == 1
    assert "refusing to overwrite" in capsys.readouterr().err
    assert load_record(out) == valid_record(base)
    assert main(["init", "--variant", "base", "--out", str(out), "--force"]) == 0
    assert load_record(out) == empty_record("base")
    assert main(["init", "--variant", " ", "--out", str(tmp_path / "x.json")]) == 1


def test_cli_check_pass_exit_0_and_prints_what_is_not_verified(tmp_path: Path, capsys):
    path = tmp_path / "rec.json"
    save_record(build_evidence(tmp_path), path)
    assert run_check(path) == 0
    out = capsys.readouterr().out
    assert out.startswith("PASS") and "LIVE" in out
    assert "Not verified by this check" in out
    assert "R5 (news blackout) was not checked" in out and "not journal-verifiable" in out
    assert "about 1 trade per 14 days" in out and "not mainnet" in out
    assert run_check(path, "testnet") == 0  # stage names are case-insensitive on the CLI
    assert capsys.readouterr().out.startswith("PASS")


def test_cli_check_prints_each_blocking_reason_exit_1(tmp_path: Path, capsys):
    path = tmp_path / "rec.json"
    record = mutate(build_evidence(tmp_path), "human_review", trades_reviewed=N_ALL - 1)
    save_record(mutate(record, "testnet", end_utc="2024-04-14T00:00:00Z"), path)
    assert run_check(path) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("BLOCKED")
    blocking = [ln for ln in lines if ln.startswith("- [")]
    assert len(blocking) == 2
    assert blocking[0].startswith(f"- [{ADOPT_HUMAN_REVIEW}]")
    assert f"{N_ALL - 1} of {N_ALL}" in blocking[0]
    assert blocking[1].startswith(f"- [{ADOPT_TESTNET}]") and "13.00 days" in blocking[1]


def test_cli_check_checks_evidence_files_next_to_the_record(tmp_path: Path, base: Path, capsys):
    path = tmp_path / "rec.json"
    save_record(valid_record(base), path)  # no evidence files next to this copy
    assert run_check(path) == 1
    out = capsys.readouterr().out
    # data file, events file, manifest, ledger, bt report, wf report, 2 journals, review
    # pack, testnet journal, decisions log, testnet candle file
    assert out.count("does not exist") == 12


def test_cli_init_then_check(tmp_path: Path, capsys):
    path = tmp_path / "rec.json"
    assert main(["init", "--variant", "base", "--out", str(path)]) == 0
    assert run_check(path, BACKTEST) == 0
    assert run_check(path, WALK_FORWARD) == 1
    out = capsys.readouterr().out
    assert f"[{ADOPT_BACKTEST}]" in out


def test_cli_config_overrides_and_fingerprint(tmp_path: Path, capsys):
    cfg_path = tmp_path / "cfg.json"
    cfg_path.write_text(json.dumps({"reward_risk": 2.5}))
    path = tmp_path / "rec.json"
    assert main(["init", "--variant", "rr2.5", "--out", str(path), "--config", str(cfg_path)]) == 0
    tested = CFG.with_changes(reward_risk=2.5)
    assert load_record(path).config_fingerprint == config_fingerprint(tested)
    capsys.readouterr()
    assert run_check(path, BACKTEST, "--config", str(cfg_path)) == 0
    assert run_check(path, BACKTEST) == 1  # default config is not the tested one
    assert f"[{ADOPT_FINGERPRINT}]" in capsys.readouterr().out
    assert main(["fingerprint", "--config", str(cfg_path)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == [canonical_config_json(tested), config_fingerprint(tested)]


def test_cli_hash_prints_sha256_of_each_file(tmp_path: Path, capsys):
    a, b = tmp_path / "a.txt", tmp_path / "b.txt"
    a.write_bytes(b"hello\n")
    b.write_bytes(b"")
    assert main(["hash", str(a), str(b)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines == [f"{sha256_file(a)}  {a}", f"{sha256_file(b)}  {b}"]
    assert main(["hash", str(tmp_path / "missing.txt")]) == 1
    assert "error:" in capsys.readouterr().err


def test_cli_errors(tmp_path: Path, base: Path, capsys):
    assert run_check(tmp_path / "missing.json") == 1
    assert "error:" in capsys.readouterr().err
    loose = tmp_path / "loose.json"
    loose.write_text(json.dumps({"reward_risk": 1.5}))
    path = tmp_path / "rec.json"
    save_record(valid_record(base), path)
    assert run_check(path, LIVE, "--config", str(loose)) == 1
    assert "reward_risk must be >= 2.0" in capsys.readouterr().err
    with pytest.raises(SystemExit) as info:
        main(["check", "--record", str(path), "--stage", "LIVE", "--now", "tomorrow"])
    assert info.value.code == 2
    with pytest.raises(SystemExit) as info:
        main(["check", "--record", str(path), "--stage", "PAPER"])
    assert info.value.code == 2


# ---------------------------------------------------------------------------- v2 A3 ML model
MODEL_FP = "ab" * 32  # a sha256 hex digest (as MLFilter.fingerprint() returns)
OTHER_FP = "cd" * 32


def ml_record(base: Path, model_fp: str | None = MODEL_FP, variant: str = "base+ml"):
    """A complete record whose ledger look carries ``model_fp`` (as run_research writes it)."""
    return valid_record(base, variant=variant, model_fp=model_fp)


def test_model_fingerprint_round_trips_and_is_optional_in_old_records(tmp_path: Path, base: Path):
    path = tmp_path / "rec.json"
    for record in (ml_record(base), empty_record("base+ml", CFG, MODEL_FP), valid_record(base)):
        save_record(record, path)
        assert load_record(path) == record
    save_record(valid_record(base), path)
    assert json.loads(path.read_text())["model_fingerprint"] is None
    # a record written before v2 A3 has no model_fingerprint key at all: still loads
    data = json.loads(path.read_text())
    del data["model_fingerprint"]
    path.write_text(json.dumps(data))
    old = load_record(path)
    assert old.model_fingerprint is None and old == valid_record(base)
    assert check(base, old, LIVE) == []
    assert empty_record("base+ml", model_fingerprint=MODEL_FP).model_fingerprint == MODEL_FP


@pytest.mark.parametrize("bad", ["5", "true", "[]", '{"a": 1}'])
def test_load_record_rejects_non_string_model_fingerprint(tmp_path: Path, bad: str):
    path = tmp_path / "rec.json"
    path.write_text(f'{{"variant": "b", "config_fingerprint": "x", "model_fingerprint": {bad}}}')
    with pytest.raises(ValueError, match="model_fingerprint must be a string or null"):
        load_record(path)


@pytest.mark.parametrize("stage", STAGES)
def test_matching_model_fingerprint_passes_every_stage(base: Path, stage: str):
    assert check(base, ml_record(base), stage, model_fingerprint=MODEL_FP) == []
    require_stage(ml_record(base), stage, CFG, NOW, base, MODEL_FP)  # no raise


def model_block(base: Path, record: AdoptionRecord, stage: str, supplied: str | None) -> str:
    decisions = check(base, record, stage, model_fingerprint=supplied)
    assert blocking_rules(decisions) == {ADOPT_FINGERPRINT}, decisions
    assert len(decisions) == 1
    return decisions[0].reason


@pytest.mark.parametrize("stage", STAGES)
def test_model_fingerprint_mismatch_blocks_every_stage(base: Path, stage: str):
    reason = model_block(base, ml_record(base), stage, OTHER_FP)
    assert "not the one that was tested" in reason
    assert MODEL_FP[:16] in reason and OTHER_FP[:16] in reason
    upper = model_block(base, ml_record(base), stage, MODEL_FP.upper())
    assert "not the one that was tested" in upper


@pytest.mark.parametrize("stage", STAGES)
def test_recorded_model_but_none_supplied_blocks_every_stage(base: Path, stage: str):
    assert "cannot be verified" in model_block(base, ml_record(base), stage, None)
    # a recorded model pins the variant even when its id has no "+ml" marker
    plain = ml_record(base, variant="base")
    assert "cannot be verified" in model_block(base, plain, stage, None)


@pytest.mark.parametrize("variant", ["base+ml", "rr2.5_vol2+ml", "base+ML"])
@pytest.mark.parametrize("stage", STAGES)
def test_ml_variant_without_recorded_model_fingerprint_blocks(base: Path, stage: str, variant):
    record = ml_record(base, model_fp=None, variant=variant)
    for supplied in (None, MODEL_FP):  # supplying one now cannot repair the record
        reason = model_block(base, record, stage, supplied)
        assert "has no model_fingerprint" in reason and "+ml" in reason


def test_model_fingerprint_supplied_for_a_record_without_one_blocks(base: Path):
    reason = model_block(base, valid_record(base), LIVE, MODEL_FP)
    assert "never tested under this record" in reason
    assert check(base, valid_record(base), LIVE, model_fingerprint=None) == []


@pytest.mark.parametrize("recorded", ["", "   ", "abc", "zz" * 32, MODEL_FP + "0"])
def test_malformed_recorded_model_fingerprint_blocks_even_if_supplied_equal(
    base: Path, recorded: str
):
    reason = model_block(base, ml_record(base, recorded), LIVE, recorded)
    assert "is not a sha256 hex digest" in reason


def test_model_and_config_mismatch_are_both_reported(base: Path):
    record = valid_record(base, CFG.with_changes(reward_risk=2.5), "rr2.5+ml", MODEL_FP)
    decisions = check(base, record, LIVE, model_fingerprint=OTHER_FP)
    assert blocking_rules(decisions) == {ADOPT_FINGERPRINT} and len(decisions) == 2
    with pytest.raises(AdoptionBlocked) as info:
        require_stage(record, LIVE, CFG, NOW, base, OTHER_FP)
    assert len(info.value.decisions) == 2


def test_cli_model_fingerprint_check(tmp_path: Path, capsys):
    path = tmp_path / "rec.json"
    save_record(build_evidence(tmp_path, variant="base+ml", model_fp=MODEL_FP), path)
    assert run_check(path, LIVE, "--model-fingerprint", MODEL_FP) == 0
    out = capsys.readouterr().out
    assert out.startswith("PASS") and f"model {MODEL_FP[:16]}" in out
    assert run_check(path, LIVE, "--model-fingerprint", f"  {MODEL_FP}\n") == 0  # stripped
    capsys.readouterr()
    assert run_check(path, LIVE) == 1
    out = capsys.readouterr().out
    assert out.startswith("BLOCKED") and f"[{ADOPT_FINGERPRINT}]" in out
    assert "cannot be verified" in out
    assert run_check(path, LIVE, "--model-fingerprint", OTHER_FP) == 1
    assert "not the one that was tested" in capsys.readouterr().out


def test_cli_init_records_the_model_fingerprint(tmp_path: Path, capsys):
    path = tmp_path / "rec.json"
    argv = ["init", "--variant", "base+ml", "--out", str(path)]
    assert main(argv) == 1  # a +ml variant needs its model fingerprint
    assert "--model-fingerprint is required" in capsys.readouterr().err
    assert not path.exists()
    assert main([*argv, "--model-fingerprint", "not-a-hash"]) == 1
    assert "not a sha256 hex digest" in capsys.readouterr().err
    assert main([*argv, "--model-fingerprint", MODEL_FP]) == 0
    assert MODEL_FP in capsys.readouterr().out
    assert load_record(path) == empty_record("base+ml", CFG, MODEL_FP)
    assert run_check(path, BACKTEST, "--model-fingerprint", MODEL_FP) == 0
    assert run_check(path, BACKTEST) == 1
    assert main(["init", "--variant", "base", "--out", str(tmp_path / "b.json")]) == 0
    assert load_record(tmp_path / "b.json").model_fingerprint is None
