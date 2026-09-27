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
    planned_net_rr,
    planned_rr,
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
    "planned_rr_price",
    "planned_rr_net",
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
    legacy=False,
    **overrides,
):
    """A self-consistent closed trade built exactly like the backtester's exit model.

    Default: cost-aware sizing and target (CONTRACT.md v2 A1), written out by hand here, so a
    clean SL is exactly -1R and a TP exactly +rr R. ``legacy=True`` sizes and targets by PRICE
    distance only (the pre-A1 model): its risk leaves out fees and slippage.
    """
    f = cfg.fee_rate
    stop_x = stop * (1 - cfg.slippage_pct / 100)
    if legacy:
        unit = entry - stop
        target = entry + rr * (entry - stop)
    else:
        unit = (entry - stop_x) + f * entry + f * stop_x  # all-in loss per unit at the stop
        target = (entry * (1 + f) + rr * unit) / (1 - f)  # a TP nets exactly rr * risk
    qty = 10_000.0 * risk_pct / 100.0 / unit
    risk_amount = qty * unit
    entry_ts = signal_ts + cfg.timeframe_ms
    if exit_price is None:
        exit_price = target if exit_reason == EXIT_TP else stop_x
    fees = f * qty * entry + f * qty * exit_price
    pnl = qty * (exit_price - entry) - fees
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
        _trade(exit_reason=EXIT_SL, stop=99.7),  # 0.3 % stop: cost-aware, still exactly -1R
    ],
)
def test_good_trades_have_no_flags(trade):
    assert auto_flags(trade, CFG) == []


def test_cost_aware_trades_stop_at_exactly_minus_one_r_and_tp_at_plus_rr():
    for stop in (99.7, 98.0, 90.0):
        sl = _trade(exit_reason=EXIT_SL, stop=stop)
        assert sl.r_multiple == pytest.approx(-1.0, abs=1e-12)
        assert expected_sl_r(sl, CFG) == pytest.approx(-1.0, abs=1e-12)
        tp = _trade(exit_reason=EXIT_TP, stop=stop)
        assert tp.r_multiple == pytest.approx(CFG.reward_risk, abs=1e-12)
        assert planned_net_rr(tp, CFG) == pytest.approx(CFG.reward_risk, abs=1e-12)
        assert planned_rr(tp) > CFG.reward_risk  # price RR is above 2 once costs are netted


def test_random_good_trades_have_no_flags():
    rng = random.Random(20240101)
    for i in range(500):
        entry = rng.uniform(10.0, 90_000.0)
        stop = entry * (1 - rng.uniform(0.003, 0.10))  # the whole configured stop range
        pair = rng.choice(["BTC/USDT", "ETH/USDT", "BNB/USDT"])
        risk = rng.uniform(0.25, 0.5 if pair == "BNB/USDT" else 1.0)
        reason = rng.choice([EXIT_SL, EXIT_TP])
        rr = rng.choice([2.0, 2.5, 3.0])
        cfg = CFG.with_changes(
            reward_risk=rr,
            fee_rate=rng.choice([0.0005, 0.001, 0.006]),
            slippage_pct=rng.choice([0.01, 0.05, 0.2]),
        )
        t = _trade(
            i, pair, T0 + i * TF, entry, stop, rr, risk, reason, None, rng.randint(1, 40), cfg
        )
        assert auto_flags(t, cfg) == [], (t, auto_flags(t, cfg))


@pytest.mark.parametrize(
    ("code", "trade"),
    [
        (FLAG_RR_BELOW, _trade(rr=1.5)),
        (FLAG_RR_BELOW, _trade(legacy=True)),  # price RR exactly 2, net of costs only 1.898
        (FLAG_RR_BELOW, replace(_trade(), target=104.0)),  # price-distance 2:1 target
        (FLAG_RR_BELOW, replace(_trade(), risk_amount=250.0)),  # net RR < 2 by the risk
        (FLAG_RR_ABOVE, _trade(rr=3.0)),
        (FLAG_RISK_CAP, _trade(risk_pct=1.2)),
        (FLAG_RISK_CAP, _trade(pair="BNB/USDT", entry=300.0, stop=294.0, risk_pct=0.75)),
        (FLAG_PAIR, _trade(pair="DOGE/USDT")),
        (FLAG_BAD_LEVELS, replace(_trade(), stop=101.0)),
        (FLAG_BAD_LEVELS, replace(_trade(), stop=100.0)),
        (FLAG_BAD_LEVELS, replace(_trade(), target=99.0)),
        (FLAG_SIZE, replace(_trade(), qty=_trade().qty * 2)),
        (FLAG_SIZE, replace(_trade(), risk_pct=0.0)),
        (FLAG_SIZE, _trade(legacy=True)),  # risk_amount = qty*(entry-stop) leaves out costs
        (FLAG_LOSS_OUTLIER, _trade(exit_reason=EXIT_SL, exit_price=96.0)),
        (FLAG_LOSS_OUTLIER, _with_r(_trade(exit_reason=EXIT_SL), -1.1)),
        (FLAG_WIN_TOO_LARGE, _with_r(_trade(), 2.05)),
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
    # A cost-aware clean stop is exactly -1R, so the band is tight: -1.05R / rr + 0.01R.
    sl = _trade(exit_reason=EXIT_SL, stop=97.0)
    assert FLAG_LOSS_OUTLIER not in _codes(sl)
    assert FLAG_LOSS_OUTLIER not in _codes(_with_r(sl, -1.05))
    assert FLAG_LOSS_OUTLIER in _codes(_with_r(sl, -1.06))
    tp = _trade()
    assert FLAG_WIN_TOO_LARGE not in _codes(tp)
    assert FLAG_WIN_TOO_LARGE not in _codes(_with_r(tp, 2.01))
    assert FLAG_WIN_TOO_LARGE in _codes(_with_r(tp, 2.02))


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


def _outlier_flag(trade):
    [flag] = [f for f in auto_flags(trade, CFG) if flag_code(f) == FLAG_LOSS_OUTLIER]
    return flag


def test_loss_outlier_detail_separates_gaps_from_cost_blind_sizing():
    # Cost-aware trade: a normal stop-out is exactly -1R, so a -1.93R loss is a gap.
    wide = _trade(exit_reason=EXIT_SL, stop=96.0, exit_price=92.0)
    assert expected_sl_r(wide, CFG) == pytest.approx(-1.0, abs=1e-12)
    flag = _outlier_flag(wide)
    assert "normal stop-out would be -1.000R: gap-through or slippage beyond" in flag
    # A tight cost-aware stop (0.5 %) is still exactly -1R: no false outlier any more.
    assert _codes(_trade(exit_reason=EXIT_SL, stop=99.5)) == []
    # Price-only (legacy) sizing on a tight stop: EVERY stop-out costs ~-1.5R, which the
    # flag attributes to the sizing (and size_mismatch / rr_below_min fire as well).
    legacy = _trade(exit_reason=EXIT_SL, stop=99.5, legacy=True)
    assert expected_sl_r(legacy, CFG) == pytest.approx(legacy.r_multiple, rel=1e-12)
    assert expected_sl_r(legacy, CFG) < -1.05
    flag = _outlier_flag(legacy)
    assert "risk_amount leaves out fees and slippage" in flag
    assert "gap-through" not in flag
    assert {FLAG_SIZE, FLAG_RR_BELOW} <= set(_codes(legacy))
    # Legacy sizing AND a gap well beyond its cost-model stop-out: reported as a gap.
    gapped = _trade(exit_reason=EXIT_SL, stop=99.5, exit_price=99.0, legacy=True)
    assert "gap-through" in _outlier_flag(gapped)


def test_rr_is_checked_by_price_and_net_of_costs():
    good = _trade()
    assert planned_rr(good) == pytest.approx(2.3493003, abs=1e-6)
    assert planned_net_rr(good, CFG) == pytest.approx(2.0, abs=1e-12)
    # Price-distance 2:1 target: nets only 1.898R after both fees, so it is below 2:1.
    legacy = _trade(legacy=True)
    assert planned_rr(legacy) == pytest.approx(2.0, abs=1e-12)
    assert planned_net_rr(legacy, CFG) == pytest.approx(1.898, abs=1e-9)
    [flag] = [f for f in auto_flags(legacy, CFG) if flag_code(f) == FLAG_RR_BELOW]
    assert "net RR 1.8980 < required 2.00" in flag and "price RR" not in flag
    # A net RR fine by the recorded risk but a price RR below 2 is still below the minimum.
    tiny_risk = replace(_trade(rr=1.5), risk_amount=_trade(rr=1.5).risk_amount / 2)
    assert planned_net_rr(tiny_risk, CFG) == pytest.approx(3.0, abs=1e-9)
    assert planned_rr(tiny_risk) < 2.0
    [flag] = [f for f in auto_flags(tiny_risk, CFG) if flag_code(f) == FLAG_RR_BELOW]
    assert "price RR" in flag and "net RR" not in flag
    assert FLAG_RR_ABOVE not in _codes(tiny_risk)
    assert FLAG_SIZE in _codes(tiny_risk)  # the understated risk is caught as well
    # The net RR depends on the fee: reviewing with the wrong costs is visible.
    coinbase = CFG.with_changes(fee_rate=0.006, slippage_pct=0.2)
    cb = _trade(cfg=coinbase)
    assert auto_flags(cb, coinbase) == []
    assert {FLAG_RR_ABOVE, FLAG_SIZE} <= set(_codes(cb, CFG))


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
    # Hand arithmetic (fee 0.1 %/side, slippage 0.05 %, entry 100, stop 98, 1 % of 10,000):
    #   S_x = 98 * 0.9995 = 97.951;  L_u = 2.049 + 0.1 + 0.097951 = 2.246951
    #   qty = 100 / 2.246951 = 44.50475333;  target = (100.1 + 2 * 2.246951) / 0.999
    #       = 104.593902 / 0.999 = 104.698601;  price RR = 4.698601 / 2 = 2.3493
    #   fees = 0.001 * qty * (100 + target) = 9.1101;  net win = qty * 4.698601 - fees = 200
    t = _trade(entry=100.0, stop=98.0)
    row = review_row(replace(t, ml_prob=0.61234, notes="capped"), CFG, None)
    assert row["entry"] == "100.000000"
    assert row["stop"] == "98.000000"
    assert row["target"] == "104.698601"
    assert row["planned_rr_price"] == "2.3493"
    assert row["planned_rr_net"] == "2.0000"
    assert row["risk_pct"] == "1.0000"
    assert row["pair_cap_pct"] == "1.0000"
    assert row["qty"] == "44.50475333"
    assert row["notional"] == "4450.4753"
    assert row["risk_amount"] == "100.0000"
    assert row["exit_reason"] == "TP"
    assert row["fees"] == "9.1101"
    assert row["pnl"] == "200.0000"
    assert row["r_multiple"] == "2.0000"
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
    no_risk = review_row(replace(_trade(), risk_amount=0.0), CFG, None)
    assert no_risk["planned_rr_net"] == ""  # undefined without a positive planned risk
    assert no_risk["planned_rr_price"] == "2.3493"


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
    assert "- Costs: fee 0.1000% per side, slippage 0.0500% on market fills" in md
    assert "| RR price | RR net |" in md
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


def test_cli_uses_the_costs_the_trades_were_generated_with(tmp_path, capsys):
    coinbase = CFG.with_changes(fee_rate=0.006, slippage_pct=0.2)
    trades = [
        _trade(1, cfg=coinbase),
        _trade(2, signal_ts=T0 + 5 * TF, exit_reason=EXIT_SL, stop=97.0, cfg=coinbase),
    ]
    journal = tmp_path / "trades.csv"
    write_journal(trades, journal)
    # Reviewed with the default (Binance) costs every trade is flagged ...
    assert main(["--journal", str(journal), "--out-dir", str(tmp_path / "a")]) == 0
    assert "2 trades (2 flagged)" in capsys.readouterr().out
    # ... and with the costs they were generated with, none is.
    args = ["--journal", str(journal), "--out-dir", str(tmp_path / "b")]
    assert main([*args, "--fee-rate", "0.006", "--slippage-pct", "0.2"]) == 0
    assert "2 trades (0 flagged)" in capsys.readouterr().out
    md = (tmp_path / "b" / MD_NAME).read_text(encoding="utf-8")
    assert "- Costs: fee 0.6000% per side, slippage 0.2000%" in md


@pytest.mark.parametrize(
    "flag", [["--fee-rate", "0"], ["--slippage-pct", "0"], ["--fee-rate", "0.0001"]]
)
def test_cli_rejects_zero_or_unrealistic_costs(tmp_path, flag):
    journal = tmp_path / "trades.csv"
    write_journal(_sample_trades(), journal)
    with pytest.raises(SystemExit) as exc:
        main(["--journal", str(journal), "--out-dir", str(tmp_path / "o"), *flag])
    assert exc.value.code == 2
    assert not (tmp_path / "o").exists()


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
