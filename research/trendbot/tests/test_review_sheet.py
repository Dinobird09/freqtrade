import csv
import random
import re
from dataclasses import replace

import pytest

from research.trendbot.config import StrategyConfig
from research.trendbot.journal import write_journal
from research.trendbot.models import EXIT_END, EXIT_SL, EXIT_TP, FeatureRow, Trade
from research.trendbot.review_sheet import (
    BASE_FEATURES,
    CSV_NAME,
    FLAG_BAD_LEVELS,
    FLAG_CODES,
    FLAG_EXIT_BEFORE_ENTRY,
    FLAG_EXIT_MODEL,
    FLAG_LOOKAHEAD,
    FLAG_LOSS_OUTLIER,
    FLAG_MISSING_EXIT,
    FLAG_NON_FINITE,
    FLAG_PAIR,
    FLAG_R_MISMATCH,
    FLAG_RISK_CAP,
    FLAG_RR_ABOVE,
    FLAG_RR_BELOW,
    FLAG_SIZE,
    FLAG_WIN_TOO_LARGE,
    FLAG_WINDOW_END,
    FLAG_ZERO_HOLD,
    MD_NAME,
    auto_flags,
    expected_sl_r,
    flag_code,
    main,
    review_row,
    write_review_pack,
)


CFG = StrategyConfig()
TF = CFG.timeframe_ms
T0 = 1_704_067_200_000  # 2024-01-01T00:00:00Z
FEATURES = {
    "rsi": 61.5,
    "vol_ratio": 2.2,
    "ema_gap_pct": 0.4,
    "dist_regime_pct": 3.1,
    "hour_utc": 12.0,
}

EXPECTED_HEADER = (
    "trade_id",
    "window",
    "pair",
    "variant",
    "signal_time_utc",
    "entry_time_utc",
    "exit_time_utc",
    "entry",
    "stop",
    "target",
    "stop_method",
    "planned_rr",
    "risk_pct",
    "pair_cap_pct",
    "qty",
    "notional",
    "risk_amount",
    "exit_price",
    "exit_reason",
    "fees",
    "pnl",
    "r_multiple",
    "hold_h",
    "f_dist_regime_pct",
    "f_ema_gap_pct",
    "f_hour_utc",
    "f_rsi",
    "f_vol_ratio",
    "ml_prob",
    "notes",
    "auto_flags",
    "reviewer_ok",
    "reviewer_note",
)


def _trade(
    trade_id=1,
    pair="BTC/USDT",
    signal_ts=T0,
    entry=100.0,
    stop=98.0,
    rr=2.0,
    risk_pct=1.0,
    exit_reason=EXIT_TP,
    exit_price=None,
    hold_candles=2,
    cfg=CFG,
    **overrides,
):
    """A self-consistent closed trade built exactly like the backtester's exit model."""
    target = entry + rr * (entry - stop)
    qty = 10_000.0 * risk_pct / 100.0 / (entry - stop)
    entry_ts = signal_ts + cfg.timeframe_ms
    if exit_price is None:
        exit_price = target if exit_reason == EXIT_TP else stop * (1 - cfg.slippage_pct / 100)
    fees = cfg.fee_rate * qty * entry + cfg.fee_rate * qty * exit_price
    pnl = qty * (exit_price - entry) - fees
    risk_amount = qty * (entry - stop)
    trade = Trade(
        trade_id=trade_id,
        pair=pair,
        variant="base",
        signal_ts=signal_ts,
        entry_ts=entry_ts,
        entry_price=entry,
        stop=stop,
        target=target,
        qty=qty,
        risk_amount=risk_amount,
        risk_pct=risk_pct,
        stop_method="pivot",
        exit_ts=entry_ts + hold_candles * cfg.timeframe_ms,
        exit_price=exit_price,
        exit_reason=exit_reason,
        fees=fees,
        pnl=pnl,
        r_multiple=pnl / risk_amount,
        features=dict(FEATURES),
    )
    return replace(trade, **overrides)


def _with_r(trade, r):
    """Same trade with a consistent (pnl, r_multiple) pair."""
    return replace(trade, r_multiple=r, pnl=r * trade.risk_amount)


def _codes(trade, cfg=CFG):
    return [flag_code(f) for f in auto_flags(trade, cfg)]


def _read_csv(path):
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        rows = [dict(zip(header, r, strict=True)) for r in reader]
    return header, rows


# ---------------------------------------------------------------------------- auto_flags
@pytest.mark.parametrize(
    "trade",
    [
        _trade(),
        _trade(exit_reason=EXIT_SL, stop=97.0),
        _trade(pair="ETH/USDT", entry=2_345.67, stop=2_300.12, exit_reason=EXIT_TP),
        _trade(pair="BNB/USDT", entry=300.0, stop=294.0, risk_pct=0.5, exit_reason=EXIT_SL),
        _trade(pair="BNB/USDT", entry=300.0, stop=294.0, risk_pct=0.5, rr=2.0),
        _trade(risk_pct=0.25, hold_candles=30),
    ],
)
def test_good_trades_have_no_flags(trade):
    assert auto_flags(trade, CFG) == []


def test_random_good_trades_have_no_flags():
    rng = random.Random(20240101)
    for i in range(500):
        entry = rng.uniform(10.0, 90_000.0)
        stop = entry * (1 - rng.uniform(0.015, 0.10))  # >= 1.5%: an SL costs < 1.25R
        pair = rng.choice(["BTC/USDT", "ETH/USDT", "BNB/USDT"])
        risk = rng.uniform(0.25, 0.5 if pair == "BNB/USDT" else 1.0)
        reason = rng.choice([EXIT_SL, EXIT_TP])
        rr = rng.choice([2.0, 2.5, 3.0])
        cfg = CFG.with_changes(reward_risk=rr)
        t = _trade(i, pair, T0 + i * TF, entry, stop, rr, risk, reason, None, rng.randint(1, 40))
        assert auto_flags(t, cfg) == [], (t, auto_flags(t, cfg))


@pytest.mark.parametrize(
    ("code", "trade"),
    [
        (FLAG_RR_BELOW, _trade(rr=1.5)),
        (FLAG_RR_ABOVE, _trade(rr=3.0)),
        (FLAG_RISK_CAP, _trade(risk_pct=1.2)),
        (FLAG_RISK_CAP, _trade(pair="BNB/USDT", entry=300.0, stop=294.0, risk_pct=0.75)),
        (FLAG_PAIR, _trade(pair="DOGE/USDT")),
        (FLAG_BAD_LEVELS, replace(_trade(), stop=101.0)),
        (FLAG_BAD_LEVELS, replace(_trade(), stop=100.0)),
        (FLAG_BAD_LEVELS, replace(_trade(), target=99.0)),
        (FLAG_SIZE, replace(_trade(), qty=_trade().qty * 2)),
        (FLAG_SIZE, replace(_trade(), risk_pct=0.0)),
        (FLAG_LOSS_OUTLIER, _trade(exit_reason=EXIT_SL, exit_price=96.0)),
        (FLAG_WIN_TOO_LARGE, _with_r(_trade(), 2.6)),
        (FLAG_ZERO_HOLD, _trade(hold_candles=0)),
        (FLAG_EXIT_BEFORE_ENTRY, _trade(hold_candles=-1)),
        (FLAG_LOOKAHEAD, replace(_trade(), entry_ts=T0)),
        (FLAG_LOOKAHEAD, replace(_trade(), entry_ts=T0 + TF - 1)),
        (
            FLAG_MISSING_EXIT,
            replace(
                _trade(), exit_ts=None, exit_price=None, exit_reason=None, pnl=None, r_multiple=None
            ),
        ),
        (FLAG_MISSING_EXIT, replace(_trade(), r_multiple=None)),
        (FLAG_WINDOW_END, _trade(exit_reason=EXIT_END, exit_price=101.0)),
        (FLAG_R_MISMATCH, replace(_trade(), r_multiple=_trade().r_multiple + 0.5)),
        (FLAG_EXIT_MODEL, _trade(exit_reason=EXIT_SL, exit_price=101.0)),  # SL above stop, R>0
        (FLAG_EXIT_MODEL, _trade(exit_reason=EXIT_SL, exit_price=99.0)),  # SL above stop, R<0
        (FLAG_EXIT_MODEL, _trade(exit_reason=EXIT_TP, exit_price=103.5)),  # TP not at target
        (FLAG_EXIT_MODEL, _with_r(_trade(), -0.1)),  # TP with a loss
        (FLAG_EXIT_MODEL, replace(_trade(), exit_reason="TRAIL")),  # unknown reason
        (FLAG_NON_FINITE, replace(_trade(), pnl=float("nan"))),
    ],
)
def test_flags_fire_on_crafted_bad_trades(code, trade):
    flags = auto_flags(trade, CFG)
    assert code in [flag_code(f) for f in flags], flags
    for f in flags:
        assert flag_code(f) in FLAG_CODES
        assert "\n" not in f and ";" not in f and "|" not in f  # safe to join / tabulate


def test_window_end_flag_text():
    flags = auto_flags(_trade(exit_reason=EXIT_END, exit_price=100.5), CFG)
    assert flags == [f for f in flags if flag_code(f) == FLAG_WINDOW_END]
    assert "window-end forced exit" in flags[0]


def test_missing_exit_is_the_only_outcome_flag_for_an_open_trade():
    open_trade = replace(
        _trade(), exit_ts=None, exit_price=None, exit_reason=None, pnl=None, r_multiple=None
    )
    assert _codes(open_trade) == [FLAG_MISSING_EXIT]
    assert "no exit recorded" in auto_flags(open_trade, CFG)[0]
    partial = replace(_trade(), exit_price=None, pnl=None)
    assert _codes(partial) == [FLAG_MISSING_EXIT]
    assert "exit_price, pnl" in auto_flags(partial, CFG)[0]


def test_negative_hold_is_not_also_reported_as_zero_hold():
    codes = _codes(_trade(hold_candles=-1))
    assert FLAG_EXIT_BEFORE_ENTRY in codes
    assert FLAG_ZERO_HOLD not in codes


def test_r_thresholds_are_strict():
    sl = _trade(exit_reason=EXIT_SL, stop=97.0)
    assert FLAG_LOSS_OUTLIER not in _codes(_with_r(sl, -1.25))
    assert FLAG_LOSS_OUTLIER in _codes(_with_r(sl, -1.26))
    tp = _trade()
    assert FLAG_WIN_TOO_LARGE not in _codes(_with_r(tp, 2.25))
    assert FLAG_WIN_TOO_LARGE in _codes(_with_r(tp, 2.26))


def test_win_bound_follows_the_configured_reward_risk():
    cfg3 = CFG.with_changes(reward_risk=3.0)
    tp3 = _trade(rr=3.0, cfg=cfg3)
    assert tp3.r_multiple > 2.25
    assert auto_flags(tp3, cfg3) == []
    # Reviewed with the wrong (base) config, the mismatch is visible instead of silent.
    assert {FLAG_RR_ABOVE, FLAG_WIN_TOO_LARGE} <= set(_codes(tp3, CFG))


def test_rr_floor_is_the_configured_reward_risk_not_just_the_mandate():
    cfg25 = CFG.with_changes(reward_risk=2.5)
    assert FLAG_RR_BELOW in _codes(_trade(rr=2.2, cfg=cfg25), cfg25)
    assert auto_flags(_trade(rr=2.5, cfg=cfg25), cfg25) == []


def test_cap_and_rr_tolerate_float_noise():
    assert auto_flags(replace(_trade(), risk_pct=1.0 + 1e-12), CFG) == []
    for entry, stop in ((43_123.37, 42_700.11), (0.3, 0.29), (2_345.6789, 2_301.2345)):
        assert auto_flags(_trade(entry=entry, stop=stop), CFG) == []


def test_loss_outlier_detail_separates_cost_drag_from_gaps():
    # Wide stop: a normal stop-out costs ~-1.07R, so -2R is a gap/slippage outlier.
    wide = _trade(exit_reason=EXIT_SL, stop=96.0, exit_price=92.0)
    assert expected_sl_r(wide, CFG) == pytest.approx(-1.060988, abs=1e-6)
    [flag] = [f for f in auto_flags(wide, CFG) if flag_code(f) == FLAG_LOSS_OUTLIER]
    assert "normal stop-out would be -1.061R: gap-through" in flag
    # Tight stop (0.5%): fees + slippage alone make a normal stop-out worse than -1.25R.
    tight = _trade(exit_reason=EXIT_SL, stop=99.5)
    assert expected_sl_r(tight, CFG) == pytest.approx(tight.r_multiple, rel=1e-12)
    assert expected_sl_r(tight, CFG) < -1.25
    [flag] = [f for f in auto_flags(tight, CFG) if flag_code(f) == FLAG_LOSS_OUTLIER]
    assert "large versus the stop distance" in flag
    assert "gap-through" not in flag
    # Tight stop AND a gap well beyond the cost-model stop-out: reported as a gap.
    gapped = _trade(exit_reason=EXIT_SL, stop=99.5, exit_price=99.0)
    [flag] = [f for f in auto_flags(gapped, CFG) if flag_code(f) == FLAG_LOSS_OUTLIER]
    assert "gap-through" in flag


def test_auto_flags_do_not_mutate_the_trade():
    t = _trade(exit_reason=EXIT_SL, exit_price=96.0)
    before = replace(t)
    auto_flags(t, CFG)
    assert t == before


def test_base_features_match_feature_row():
    row = FeatureRow(T0, T0 + TF, 1.0, 1.0, 1.0, 1.0, 55.0, 1.0, 2.0, 0.1, 0.2, 4)
    assert tuple(sorted(row.ml_features())) == BASE_FEATURES


# ---------------------------------------------------------------------------- the pack
def _sample_trades():
    return [
        _trade(1, signal_ts=T0),
        _trade(2, signal_ts=T0 + 10 * TF, exit_reason=EXIT_SL, stop=97.0),
        _trade(3, "BNB/USDT", T0 + 20 * TF, 300.0, 294.0, 2.0, 0.5, EXIT_TP),
        _trade(4, signal_ts=T0 + 30 * TF, exit_reason=EXIT_END, exit_price=100.8),
        _trade(5, "ETH/USDT", T0 + 40 * TF, 2_000.0, 1_950.0, 2.0, 1.0, EXIT_SL, 1_900.0),
    ]


def test_pack_files_and_exact_header(tmp_path):
    trades = _sample_trades()
    paths = write_review_pack(trades, tmp_path / "pack", "Base variant", CFG)
    assert set(paths) == {"csv", "md"}
    assert paths["csv"] == tmp_path / "pack" / CSV_NAME
    assert paths["md"] == tmp_path / "pack" / MD_NAME
    assert paths["csv"].is_file() and paths["md"].is_file()
    header, rows = _read_csv(paths["csv"])
    assert tuple(header) == EXPECTED_HEADER
    assert len(rows) == len(trades)
    for row in rows:
        assert row["reviewer_ok"] == ""
        assert row["reviewer_note"] == ""
        assert row["window"] == "ALL"


def test_rows_are_in_entry_order_and_output_is_deterministic(tmp_path):
    trades = _sample_trades()
    shuffled = list(trades)
    random.Random(3).shuffle(shuffled)
    a = write_review_pack(trades, tmp_path / "a", "T", CFG, split_ts=T0 + 25 * TF)
    b = write_review_pack(shuffled, tmp_path / "b", "T", CFG, split_ts=T0 + 25 * TF)
    assert a["csv"].read_bytes() == b["csv"].read_bytes()
    assert a["md"].read_bytes() == b["md"].read_bytes()
    _, rows = _read_csv(a["csv"])
    assert [r["trade_id"] for r in rows] == ["1", "2", "3", "4", "5"]


def test_rows_follow_entry_time_not_trade_id(tmp_path):
    # e.g. ids assigned in exit order: the review sheet must still list trades by entry.
    late = _trade(1, signal_ts=T0 + 5 * TF)
    early = _trade(2, signal_ts=T0, hold_candles=20)
    same_a = _trade(4, signal_ts=T0 + 9 * TF)
    same_b = _trade(3, signal_ts=T0 + 9 * TF, exit_reason=EXIT_SL, stop=97.0)
    paths = write_review_pack([late, same_a, early, same_b], tmp_path, "order", CFG)
    _, rows = _read_csv(paths["csv"])
    assert [r["trade_id"] for r in rows] == ["2", "1", "3", "4"]
    md = paths["md"].read_text(encoding="utf-8")
    ids = re.findall(r"^\| (\d+) \| ALL \| ", md, re.MULTILINE)
    assert ids == ["2", "1", "3", "4"]


def test_row_values_use_fixed_formatting():
    t = _trade(entry=100.0, stop=98.0)
    row = review_row(replace(t, ml_prob=0.61234, notes="capped"), CFG, None)
    assert row["entry"] == "100.000000"
    assert row["stop"] == "98.000000"
    assert row["target"] == "104.000000"
    assert row["planned_rr"] == "2.0000"
    assert row["risk_pct"] == "1.0000"
    assert row["pair_cap_pct"] == "1.0000"
    assert row["qty"] == "50.00000000"
    assert row["notional"] == "5000.0000"
    assert row["risk_amount"] == "100.0000"
    assert row["exit_reason"] == "TP"
    assert row["fees"] == "10.2000"
    assert row["pnl"] == "189.8000"
    assert row["r_multiple"] == "1.8980"
    assert row["hold_h"] == "8.00"
    assert row["signal_time_utc"] == "2024-01-01T00:00:00Z"
    assert row["entry_time_utc"] == "2024-01-01T04:00:00Z"
    assert row["exit_time_utc"] == "2024-01-01T12:00:00Z"
    assert row["f_rsi"] == "61.500000"
    assert row["ml_prob"] == "0.6123"
    assert row["notes"] == "capped"
    assert row["auto_flags"] == ""
    zero = review_row(_with_r(_trade(exit_reason=EXIT_END, exit_price=100.0), -0.0), CFG, None)
    assert zero["r_multiple"] == "0.0000"  # never "-0.0000"
    bnb = review_row(_trade(pair="BNB/USDT", entry=300.0, stop=294.0, risk_pct=0.5), CFG, None)
    assert bnb["pair_cap_pct"] == "0.5000"
    unknown = review_row(_trade(pair="DOGE/USDT"), CFG, None)
    assert unknown["pair_cap_pct"] == ""


def test_open_trade_row_has_empty_exit_cells(tmp_path):
    open_trade = replace(
        _trade(9), exit_ts=None, exit_price=None, exit_reason=None, pnl=None, r_multiple=None
    )
    paths = write_review_pack([open_trade], tmp_path, "open", CFG)
    _, [row] = _read_csv(paths["csv"])
    for col in ("exit_time_utc", "exit_price", "exit_reason", "pnl", "r_multiple", "hold_h"):
        assert row[col] == ""
    assert row["auto_flags"].startswith(FLAG_MISSING_EXIT)
    md = paths["md"].read_text(encoding="utf-8")
    assert "Closed trades: 0; open or missing exit: 1" in md


def test_train_test_split_labelling(tmp_path):
    split = T0 + 20 * TF
    trades = [
        _trade(1, signal_ts=split - TF),
        _trade(2, signal_ts=split),  # exactly at the split: TEST
        _trade(3, signal_ts=split + TF, exit_reason=EXIT_SL, stop=97.0),
    ]
    # Entry order follows the fill candle, but the window follows the SIGNAL candle.
    paths = write_review_pack(trades, tmp_path, "wf", CFG, split_ts=split)
    _, rows = _read_csv(paths["csv"])
    assert [(r["trade_id"], r["window"]) for r in rows] == [
        ("1", "TRAIN"),
        ("2", "TEST"),
        ("3", "TEST"),
    ]
    md = paths["md"].read_text(encoding="utf-8")
    assert "| metric | TRAIN | TEST |" in md
    assert "- Trades: 3 (TRAIN 1, TEST 2)" in md
    tp_r = trades[0].r_multiple
    sl_r = trades[2].r_multiple
    avg_line = f"| avg R (expectancy) | {tp_r:+.3f} | {(tp_r + sl_r) / 2:+.3f} |"
    assert avg_line in md
    assert f"| total R | {tp_r:+.3f} | {tp_r + sl_r:+.3f} |" in md
    assert "| exits SL / TP / END | 0 / 1 / 0 | 1 / 1 / 0 |" in md


def test_without_split_everything_is_all(tmp_path):
    paths = write_review_pack(_sample_trades(), tmp_path, "all", CFG)
    md = paths["md"].read_text(encoding="utf-8")
    assert "| metric | ALL |" in md
    assert "TRAIN" not in md and "TEST |" not in md


def test_md_has_every_trade_flags_and_signoff(tmp_path):
    trades = _sample_trades()
    counts = {"R3_volume": 40, "R1_trend": 120, "R5_news_blackout": 3, "R6_correlation_cap": 3}
    paths = write_review_pack(trades, tmp_path, "Review", CFG, T0 + 25 * TF, counts)
    md = paths["md"].read_text(encoding="utf-8")
    assert md.startswith("# Review\n")
    assert "must inspect EVERY row" in md
    for t in trades:
        assert re.search(rf"^\| {t.trade_id} \| (TRAIN|TEST) \| ", md, re.MULTILINE), t.trade_id
    # Trade 4 (END) and 5 (gapped SL) are flagged; 1-3 are clean.
    flagged_section = md.split("## Flagged trades (2)", 1)[1].split("##", 1)[0]
    assert "**#4**" in flagged_section and "window-end forced exit" in flagged_section
    assert "**#5**" in flagged_section and FLAG_LOSS_OUTLIER in flagged_section
    for clean in (1, 2, 3):
        assert f"**#{clean}**" not in md
    assert "- Flagged trades: 2 of 5" in md
    # Decision counts: descending count, ties by rule id, with the rule meaning.
    table = [line for line in md.splitlines() if line.startswith("| R")]
    assert [line.split(" | ")[0] for line in table] == [
        "| R1_trend",
        "| R3_volume",
        "| R5_news_blackout",
        "| R6_correlation_cap",
    ]
    assert "| R1_trend | 120 | Trend gate" in md
    # Sign-off block.
    signoff = md.split("## Reviewer sign-off", 1)[1]
    for text in (
        "Reviewer name:",
        "Date (UTC):",
        "Trades reviewed: ______ of 5",
        "Flagged trades explained: ______ of 2",
        "[ ] APPROVE",
        "[ ] REJECT",
        "Notes:",
    ):
        assert text in signoff
    assert md.index("## All trades (5)") < md.index("## Flagged trades") < md.index("sign-off")


def test_md_decision_section_only_when_given(tmp_path):
    md = write_review_pack(_sample_trades(), tmp_path / "a", "x", CFG)["md"].read_text()
    assert "## Rule denials" not in md
    md = write_review_pack(_sample_trades(), tmp_path / "b", "x", CFG, None, {})["md"].read_text()
    assert "## Rule denials" in md and "No denials recorded." in md


def test_md_escapes_pipes_and_warns(tmp_path):
    trades = [replace(_trade(1), variant="a|b"), replace(_trade(1, signal_ts=T0 + TF))]
    cfg = CFG.with_changes(regime_filter=False)
    md = write_review_pack(trades, tmp_path, "A | B", cfg)["md"].read_text(encoding="utf-8")
    assert md.startswith("# A \\| B\n")
    assert "| a\\|b |" in md
    assert "TEST-ONLY config" in md
    assert "duplicate trade ids 1" in md
    table_rows = [line for line in md.splitlines() if re.match(r"^\| 1 \| ALL \| ", line)]
    assert len(table_rows) == 2


def test_empty_pack(tmp_path):
    paths = write_review_pack([], tmp_path, "empty", CFG, split_ts=T0)
    header, rows = _read_csv(paths["csv"])
    assert tuple(header) == EXPECTED_HEADER
    assert rows == []
    md = paths["md"].read_text(encoding="utf-8")
    assert "No trades." in md
    assert "| avg R (expectancy) | n/a | n/a |" in md
    assert "Trades reviewed: ______ of 0" in md


def test_extra_feature_keys_get_sorted_columns(tmp_path):
    t = _trade(features={"rsi": 60.0, "zeta": 1.5, "alpha": -2.0})
    paths = write_review_pack([t], tmp_path, "features", CFG)
    header, [row] = _read_csv(paths["csv"])
    feats = [c for c in header if c.startswith("f_")]
    assert feats == sorted(["f_alpha", "f_zeta", *("f_" + k for k in BASE_FEATURES)])
    assert row["f_alpha"] == "-2.000000"
    assert row["f_vol_ratio"] == ""  # missing feature -> empty cell


def test_flags_in_csv_match_auto_flags(tmp_path):
    trades = _sample_trades()
    paths = write_review_pack(trades, tmp_path, "flags", CFG)
    _, rows = _read_csv(paths["csv"])
    by_id = {t.trade_id: t for t in trades}
    for row in rows:
        want = auto_flags(by_id[int(row["trade_id"])], CFG)
        assert row["auto_flags"] == "; ".join(want)


# ---------------------------------------------------------------------------- CLI
def test_cli_builds_pack_from_journal(tmp_path, capsys):
    journal = tmp_path / "trades.csv"
    write_journal(_sample_trades(), journal)
    out = tmp_path / "review"
    rc = main(
        [
            "--journal",
            str(journal),
            "--out-dir",
            str(out),
            "--split",
            "2024-01-04T12:00:00Z",  # T0 + 21 candles
        ]
    )
    assert rc == 0
    assert "5 trades (2 flagged)" in capsys.readouterr().out
    _, rows = _read_csv(out / CSV_NAME)
    assert [r["window"] for r in rows] == ["TRAIN", "TRAIN", "TRAIN", "TEST", "TEST"]
    md = (out / MD_NAME).read_text(encoding="utf-8")
    assert md.startswith("# Trade review: trades.csv\n")


def test_cli_rejects_a_loosened_reward_risk(tmp_path):
    journal = tmp_path / "trades.csv"
    write_journal(_sample_trades(), journal)
    with pytest.raises(SystemExit) as exc:
        main(["--journal", str(journal), "--out-dir", str(tmp_path), "--reward-risk", "1.5"])
    assert exc.value.code == 2


def test_cli_reports_a_missing_journal_as_usage_error(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main(["--journal", str(tmp_path / "missing.csv"), "--out-dir", str(tmp_path / "o")])
    assert exc.value.code == 2
    assert not (tmp_path / "o").exists()
