import csv
import hashlib
import io
import random
import re
from dataclasses import replace
from pathlib import Path

import pytest

from research.trendbot.backtester import run_backtest
from research.trendbot.config import StrategyConfig
from research.trendbot.data import pair_filename, save_candles_csv
from research.trendbot.journal import write_journal
from research.trendbot.models import EXIT_END, EXIT_SL, EXIT_TP, Candle, FeatureRow, Trade
from research.trendbot.review_sheet import (
    BASE_FEATURES,
    CONTEXT_COLUMNS,
    CONTEXT_CSV_COLUMNS,
    CONTEXT_UNAVAILABLE,
    CSV_NAME,
    DEEP_WICK_STOP_DISTANCES,
    FLAG_BAD_LEVELS,
    FLAG_CODES,
    FLAG_CONTEXT_MISMATCH,
    FLAG_CONTEXT_MISSING,
    FLAG_DEEP_WICK,
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
    FLAG_STOP_MISMATCH,
    FLAG_WIN_TOO_LARGE,
    FLAG_WINDOW_END,
    FLAG_ZERO_HOLD,
    MD_NAME,
    REVIEW_KEY,
    REVIEW_OK_VALUES,
    WINDOW_TEST,
    WINDOW_TRAIN,
    ReviewRow,
    auto_flags,
    context_flags,
    duplicate_keys,
    entry_order,
    expected_sl_r,
    flag_code,
    load_review,
    main,
    planned_net_rr,
    planned_rr,
    review_key,
    review_row,
    trade_context,
    wick_fill_extra_r,
    window_of,
    write_review_pack,
)
from research.trendbot.structure import find_stop, latest_confirmed_pivot
from research.trendbot.synthetic import make_world


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
    "mae_r",
    "mfe_r",
    "stop_ref_time_utc",
    "stop_ref_low",
    "stop_ref_bar",
    "exit_low",
    "wick_depth_r",
    "context_csv",
    "context_sha256",
    "ml_prob",
    "notes",
    "auto_flags",
    "reviewer_ok",
    "reviewer_note",
)
# The header of a pack written before the trade-context columns existed (still readable).
LEGACY_HEADER = tuple(c for c in EXPECTED_HEADER if c not in CONTEXT_COLUMNS)


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
    assert "(exactly Y or N; any other value is rejected when the sheet is read back)" in md
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
    # The same trade id in TRAIN and TEST is two distinct keys: allowed, with a note.
    trades = [replace(_trade(1), variant="a|b"), replace(_trade(1, signal_ts=T0 + TF))]
    cfg = CFG.with_changes(regime_filter=False)
    md = write_review_pack(trades, tmp_path, "A | B", cfg, split_ts=T0 + TF)["md"].read_text(
        encoding="utf-8"
    )
    assert md.startswith("# A \\| B\n")
    assert "| a\\|b |" in md
    assert "TEST-ONLY config" in md
    assert "- Row key: (window, trade_id), unique in this pack" in md
    assert "trade ids 1 appear in more than one window" in md
    table_rows = [line for line in md.splitlines() if re.match(r"^\| 1 \| (TRAIN|TEST) \| ", line)]
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


# ---------------------------------------------------------------------------- row keys (C3)
def _split_trades():
    """TRAIN ids 1-3 and TEST ids 4-5, like run_research (TEST ids offset past TRAIN)."""
    split = T0 + 25 * TF
    return split, _sample_trades()


def test_review_key_and_duplicates():
    split, trades = _split_trades()
    assert REVIEW_KEY == ("window", "trade_id")
    assert [review_key(t, split) for t in trades] == [
        ("TRAIN", 1),
        ("TRAIN", 2),
        ("TRAIN", 3),
        ("TEST", 4),
        ("TEST", 5),
    ]
    assert duplicate_keys(trades, split) == []
    twin = replace(trades[1], signal_ts=trades[1].signal_ts + TF)
    assert duplicate_keys([*trades, twin], split) == [("TRAIN", 2)]
    # The same id in different windows is two keys.
    other_window = replace(trades[0], signal_ts=split + 50 * TF)
    assert duplicate_keys([*trades, other_window], split) == []
    assert duplicate_keys([*trades, other_window], None) == [("ALL", 1)]


def test_pack_with_duplicate_keys_raises_and_writes_nothing(tmp_path):
    split, trades = _split_trades()
    dup = replace(trades[3], signal_ts=trades[3].signal_ts + TF)  # a second TEST id 4
    with pytest.raises(
        ValueError, match=r"duplicate review keys \(window, trade_id\): \(TEST, 4\)"
    ):
        write_review_pack([*trades, dup], tmp_path / "pack", "dup", CFG, split_ts=split)
    assert not (tmp_path / "pack").exists()
    with pytest.raises(ValueError, match=r"\(ALL, 1\)"):
        write_review_pack([trades[0], replace(trades[0])], tmp_path / "all", "dup", CFG)


def test_cli_rejects_a_journal_with_duplicate_ids(tmp_path, capsys):
    journal = tmp_path / "trades.csv"
    trades = _sample_trades()
    write_journal([*trades, replace(trades[1], signal_ts=T0 + 60 * TF)], journal)
    with pytest.raises(SystemExit) as exc:
        main(["--journal", str(journal), "--out-dir", str(tmp_path / "o")])
    assert exc.value.code == 2
    assert "duplicate review keys" in capsys.readouterr().err
    assert not (tmp_path / "o").exists()


# ---------------------------------------------------------------------------- load_review
def _fill(csv_path, verdicts, notes=None):
    """Write reviewer verdicts (by trade_id) into a review CSV, like a person would."""
    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    header = rows[0]
    ok_col, note_col, id_col = (
        header.index(c) for c in ("reviewer_ok", "reviewer_note", "trade_id")
    )
    for row in rows[1:]:
        tid = int(row[id_col])
        row[ok_col] = verdicts.get(tid, row[ok_col])
        row[note_col] = (notes or {}).get(tid, row[note_col])
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh, lineterminator="\n").writerows(rows)
    return csv_path


def _rewrite(csv_path, transform):
    lines = csv_path.read_text(encoding="utf-8").splitlines(keepends=True)
    csv_path.write_text("".join(transform(lines)), encoding="utf-8")
    return csv_path


def test_load_review_round_trips_a_fresh_pack(tmp_path):
    split, trades = _split_trades()
    paths = write_review_pack(trades, tmp_path, "rt", CFG, split_ts=split)
    rows = load_review(paths["csv"])
    assert all(isinstance(r, ReviewRow) for r in rows)
    assert [r.key for r in rows] == [review_key(t, split) for t in entry_order(trades)]
    by_id = {t.trade_id: t for t in trades}
    for r in rows:
        t = by_id[r.trade_id]
        assert (r.pair, r.signal_ts) == (t.pair, t.signal_ts)
        assert (r.reviewer_ok, r.reviewer_note) == ("", "")
    # A plain tuple in the documented field order as well.
    window, trade_id, pair, signal_ts, ok, note = rows[0]
    assert (window, trade_id, pair, signal_ts, ok, note) == ("TRAIN", 1, "BTC/USDT", T0, "", "")


def test_load_review_key_set_matches_the_journals(tmp_path):
    # run_research: TRAIN journal ids 1..3, TEST journal ids offset past them (4, 5).
    split, trades = _split_trades()
    train = [t for t in trades if t.signal_ts < split]
    test = [t for t in trades if t.signal_ts >= split]
    paths = write_review_pack([*train, *test], tmp_path, "keys", CFG, split_ts=split)
    keys = {r.key for r in load_review(paths["csv"])}
    want = {("TRAIN", t.trade_id) for t in train} | {("TEST", t.trade_id) for t in test}
    assert keys == want and len(keys) == len(trades)


def test_load_review_reads_verdicts_and_notes(tmp_path):
    split, trades = _split_trades()
    csv_path = write_review_pack(trades, tmp_path, "filled", CFG, split_ts=split)["csv"]
    _fill(
        csv_path,
        {1: "Y", 2: " N ", 3: "Y", 4: "", 5: "Y"},
        {2: "  stop sits above the swing low, reject ", 5: "gap-through explained"},
    )
    rows = {r.trade_id: r for r in load_review(csv_path)}
    assert {i: r.reviewer_ok for i, r in rows.items()} == {1: "Y", 2: "N", 3: "Y", 4: "", 5: "Y"}
    assert rows[2].reviewer_note == "stop sits above the swing low, reject"
    assert rows[5].reviewer_note == "gap-through explained"
    assert set(REVIEW_OK_VALUES) == {"Y", "N", ""}


@pytest.mark.parametrize("bad", ["y", "n", "yes", "OK", "X", "1", "Y?", "YN"])
def test_load_review_rejects_other_verdicts_with_the_line(tmp_path, bad):
    split, trades = _split_trades()
    csv_path = write_review_pack(trades, tmp_path, "bad", CFG, split_ts=split)["csv"]
    _fill(csv_path, {1: "Y", 3: bad})
    with pytest.raises(ValueError, match=r"line 4: reviewer_ok") as info:
        load_review(csv_path)
    assert "must be Y (approved), N (rejected) or blank" in str(info.value)
    assert str(csv_path) in str(info.value)


def _header_edit(old, new):
    def transform(lines):
        return [lines[0].replace(old, new, 1), *lines[1:]]

    return transform


@pytest.mark.parametrize(
    ("transform", "fragment"),
    [
        (_header_edit(",reviewer_ok,", ",verdict,"), "last 5 columns"),
        (_header_edit(",reviewer_note", ""), "last 5 columns"),
        (_header_edit("trade_id,window", "window,trade_id"), "first 24 columns"),
        (_header_edit("signal_time_utc", "signal_ts"), "first 24 columns"),
        (_header_edit("f_dist_regime_pct,", ""), "f_dist_regime_pct are missing"),
        (_header_edit("f_rsi,f_vol_ratio", "f_vol_ratio,f_rsi"), "unique and sorted"),
        (_header_edit("f_rsi,", "rsi,"), "must all be f_<feature>"),
        (lambda lines: ["trade_id,window,reviewer_ok\n", *lines[1:]], "fewer than"),
    ],
)
def test_load_review_validates_the_header(tmp_path, transform, fragment):
    split, trades = _split_trades()
    csv_path = write_review_pack(trades, tmp_path, "hdr", CFG, split_ts=split)["csv"]
    _rewrite(csv_path, transform)
    with pytest.raises(ValueError, match=r"line 1: not a trades_review.csv header") as info:
        load_review(csv_path)
    assert fragment in str(info.value)


def _cell_edit(line_no, column, value):
    def transform(lines):
        header = next(csv.reader([lines[0]]))
        cells = next(csv.reader([lines[line_no - 1]]))
        cells[header.index(column)] = value
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\n").writerow(cells)
        return [*lines[: line_no - 1], buf.getvalue(), *lines[line_no:]]

    return transform


@pytest.mark.parametrize(
    ("transform", "line", "fragment"),
    [
        (_cell_edit(3, "window", "train"), 3, "window 'train' is not one of"),
        (_cell_edit(2, "window", "VALIDATION"), 2, "window"),
        (_cell_edit(4, "trade_id", "3.0"), 4, "trade_id '3.0'"),
        (_cell_edit(4, "trade_id", "-3"), 4, "trade_id"),
        (_cell_edit(5, "pair", " "), 5, "pair is empty"),
        (_cell_edit(2, "signal_time_utc", "2024-01-01T00:00:00"), 2, "no UTC offset"),
        (_cell_edit(2, "signal_time_utc", "yesterday"), 2, "signal_time_utc"),
        (_cell_edit(3, "trade_id", "1"), 3, "duplicate key (window, trade_id) = (TRAIN, 1)"),
        (_cell_edit(6, "window", "ALL"), 6, "mixed with TRAIN/TEST"),
        (lambda lines: [*lines[:2], lines[2].rstrip("\n") + ",extra\n", *lines[3:]], 3, "cells"),
        (lambda lines: [*lines[:3], "4,TEST,BTC/USDT\n", *lines[3:]], 4, "cells"),
    ],
)
def test_load_review_rejects_malformed_rows_with_the_line(tmp_path, transform, line, fragment):
    split, trades = _split_trades()
    csv_path = write_review_pack(trades, tmp_path, "rows", CFG, split_ts=split)["csv"]
    _rewrite(csv_path, transform)
    with pytest.raises(ValueError, match=f"line {line}:") as info:
        load_review(csv_path)
    assert fragment in str(info.value)


def test_load_review_duplicate_key_names_both_lines(tmp_path):
    split, trades = _split_trades()
    csv_path = write_review_pack(trades, tmp_path, "dup", CFG, split_ts=split)["csv"]
    _rewrite(csv_path, lambda lines: [*lines, lines[4]])  # repeat the TEST id 4 row
    with pytest.raises(
        ValueError, match=r"line 7: duplicate key .*\(TEST, 4\), first seen on line 5"
    ):
        load_review(csv_path)


def test_load_review_tolerates_bom_blank_lines_and_crlf(tmp_path):
    split, trades = _split_trades()
    csv_path = write_review_pack(trades, tmp_path, "bom", CFG, split_ts=split)["csv"]
    _fill(csv_path, {1: "Y", 2: "N"})
    text = csv_path.read_text(encoding="utf-8").replace("\n", "\r\n")
    lines = text.splitlines(keepends=True)
    csv_path.write_bytes(("﻿" + lines[0] + "\r\n" + "".join(lines[1:]) + ",,\r\n").encode())
    rows = load_review(csv_path)
    assert [r.key for r in rows] == [review_key(t, split) for t in entry_order(trades)]
    assert [r.reviewer_ok for r in rows[:2]] == ["Y", "N"]


def test_load_review_same_id_in_both_windows_and_all_packs(tmp_path):
    split = T0 + 5 * TF
    trades = [_trade(1, signal_ts=T0), _trade(1, signal_ts=split)]
    rows = load_review(write_review_pack(trades, tmp_path / "a", "x", CFG, split_ts=split)["csv"])
    assert [r.key for r in rows] == [("TRAIN", 1), ("TEST", 1)]
    rows = load_review(write_review_pack(_sample_trades(), tmp_path / "b", "x", CFG)["csv"])
    assert {r.window for r in rows} == {"ALL"} and len(rows) == 5


def test_load_review_empty_and_header_only(tmp_path):
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="line 1: empty file"):
        load_review(empty)
    paths = write_review_pack([], tmp_path / "none", "none", CFG, split_ts=T0)
    assert load_review(paths["csv"]) == []


def test_load_review_accepts_extra_sorted_feature_columns(tmp_path):
    t = _trade(features={**FEATURES, "zeta": 1.0, "alpha": 2.0})
    rows = load_review(write_review_pack([t], tmp_path, "feat", CFG)["csv"])
    assert [r.key for r in rows] == [("ALL", 1)]


# ---------------------------------------------------------------------------- trade context
# Crafted candles (4H from T0; flat at 100 with lows 99.5, so no pivot anywhere except where
# placed) and a trade built by hand on them, so every context number is checked by hand.
SIGNAL = 45  # signal candle index; the fill candle is 46, the exit candle 50
EXIT = 50
PIVOT = 40
SLIP = CFG.slippage_pct / 100


def _c(i, o, h, lo, c, v=100.0):
    return Candle(T0 + i * TF, o, h, lo, c, v)


def _pivot_candles(exit_low=95.0, exit_open=99.2, pivot=PIVOT, n=60):
    candles = [_c(i, 100.0, 100.5, 99.5, 100.0) for i in range(n)]
    candles[pivot] = _c(pivot, 100.0, 100.5, 97.0, 100.0)  # the only pivot low
    candles[SIGNAL] = _c(SIGNAL, 100.0, 101.2, 99.6, 101.0, 400.0)
    candles[46] = _c(46, 101.2, 102.0, 100.0, 101.5)  # fill candle
    candles[47] = _c(47, 101.5, 103.5, 99.8, 100.2)  # highest high of the trade
    candles[48] = _c(48, 100.2, 102.5, 100.0, 101.0)
    candles[49] = _c(49, 101.0, 101.5, 99.0, 99.2)
    close = max(exit_low, min(exit_open, 96.0))
    candles[EXIT] = _c(EXIT, exit_open, max(exit_open, 99.4), exit_low, close)
    return candles


def _stop_of(candles, i=SIGNAL, pair="BTC/USDT", cfg=CFG):
    plan, why = find_stop(candles, i, pair, cfg)
    assert plan is not None, why
    return plan.stop


def _entry_of(candles, i=SIGNAL):
    return candles[i + 1].open * (1 + SLIP)


def _pivot_trade(candles, reason=EXIT_SL, **kw):
    """The trade the backtester would record on ``candles`` (fill at the open * (1 + slip))."""
    stop = _stop_of(candles)
    if reason == EXIT_SL and "exit_price" not in kw and candles[EXIT].open <= stop:
        kw["exit_price"] = candles[EXIT].open * (1 - SLIP)  # gap-through fills at the open
    return _trade(
        signal_ts=T0 + SIGNAL * TF,
        entry=_entry_of(candles),
        stop=stop,
        exit_reason=reason,
        hold_candles=EXIT - SIGNAL - 1,
        **kw,
    )


def _low_at_depth(candles, stop_distances):
    """The exit-candle low that sits ``stop_distances`` stop distances below the stop."""
    stop, entry = _stop_of(candles), _entry_of(candles)
    return stop - stop_distances * (entry - stop)


def _read_context(path):
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_pivot_stop_is_the_crafted_one():
    candles = _pivot_candles()
    assert latest_confirmed_pivot(candles, SIGNAL, CFG) == PIVOT
    assert _stop_of(candles) == pytest.approx(97.0 * (1 - 0.0025), abs=1e-12)


def test_mae_mfe_and_wick_by_hand():
    candles = _pivot_candles(exit_low=95.0)
    t = _pivot_trade(candles)
    ctx = trade_context(t, candles, CFG)
    # Hand arithmetic: E = 101.2 * 1.0005 = 101.2506, S = 97 * 0.9975 = 96.7575,
    # R per unit = risk_amount / qty = L_u = (E - S*0.9995) + 0.001*E + 0.001*S*0.9995
    #   = (101.2506 - 96.709121) + 0.101251 + 0.096709 = 4.739439.
    # MAE = (101.2506 - 95.0) / 4.739439 = 1.3188; MFE = (103.5 - 101.2506) / 4.739439 = 0.4746;
    # wick depth = (96.7575 - 95.0) / 4.739439 = 0.3708 R = 1.7575 / 4.4931 = 0.39 stop distances.
    e, s = 101.2 * 1.0005, 97.0 * 0.9975
    unit = (e - s * 0.9995) + 0.001 * e + 0.001 * s * 0.9995
    assert unit == pytest.approx(t.risk_amount / t.qty, rel=1e-12)
    assert ctx.available and ctx.mismatches == ()
    assert (ctx.signal_index, ctx.fill_index, ctx.exit_index) == (SIGNAL, 46, EXIT)
    # Lowest low over the fill .. exit candles (46..50) is the exit candle's 95.0; the highest
    # high is candle 47's 103.5.
    assert ctx.mae_r == pytest.approx((e - 95.0) / unit, rel=1e-12)
    assert ctx.mfe_r == pytest.approx((103.5 - e) / unit, rel=1e-12)
    assert ctx.exit_low == 95.0
    assert ctx.wick_depth_r == pytest.approx((s - 95.0) / unit, rel=1e-12)
    assert ctx.wick_depth_sd == pytest.approx((s - 95.0) / (e - s), rel=1e-12)
    assert ctx.ref_index == PIVOT and ctx.ref_method == "pivot"
    assert ctx.rederived_stop == pytest.approx(t.stop, rel=1e-15)
    row = review_row(t, CFG, None, context=ctx)
    assert row["mae_r"] == f"{(e - 95.0) / unit:.4f}" == "1.3188"
    assert row["mfe_r"] == f"{(103.5 - e) / unit:.4f}" == "0.4746"
    assert row["stop_ref_time_utc"] == "2024-01-07T16:00:00Z"  # T0 + 40 * 4h
    assert row["stop_ref_low"] == "97.000000"
    assert row["stop_ref_bar"] == "-5"
    assert row["exit_low"] == "95.000000"
    assert row["wick_depth_r"] == f"{(s - 95.0) / unit:.4f}" == "0.3708"
    # 0.39 stop distances below the stop: a normal, not a deep, wick.
    assert auto_flags(t, CFG, ctx) == []


def test_tp_trade_has_no_wick_cells_and_mfe_reaches_the_target():
    candles = _pivot_candles()
    t0 = _pivot_trade(candles, EXIT_TP)
    candles[EXIT] = _c(EXIT, 101.0, t0.target + 0.5, 100.5, 101.0)
    t = _pivot_trade(candles, EXIT_TP)
    ctx = trade_context(t, candles, CFG)
    unit = t.risk_amount / t.qty
    assert ctx.mismatches == ()
    assert ctx.mfe_r == pytest.approx((t.target + 0.5 - t.entry_price) / unit, rel=1e-12)
    assert ctx.mae_r == pytest.approx((t.entry_price - 99.0) / unit, rel=1e-12)  # candle 49
    assert (ctx.exit_low, ctx.wick_depth_r, ctx.wick_depth_sd) == (None, None, None)
    row = review_row(t, CFG, None, context=ctx)
    assert row["exit_low"] == row["wick_depth_r"] == ""
    assert auto_flags(t, CFG, ctx) == []


def test_lookback_low_trade_names_the_lowest_low_used():
    # Strictly rising lows: no pivot low anywhere, so R8 falls back to the lowest low of the
    # last fallback_lookback (10) candles, candles 36..45, i.e. candle 36 (bar -9).
    candles = []
    for i in range(60):
        lo = 90.0 + 0.1 * i
        candles.append(_c(i, lo + 0.5, lo + 1.2, lo, lo + 1.0))
    assert latest_confirmed_pivot(candles, SIGNAL, CFG) is None
    plan, _ = find_stop(candles, SIGNAL, "BTC/USDT", CFG)
    assert plan.method == "lookback_low" and plan.structure_level == pytest.approx(93.6)
    t = _trade(
        signal_ts=T0 + SIGNAL * TF,
        entry=_entry_of(candles),
        stop=plan.stop,
        exit_reason=EXIT_TP,
        hold_candles=EXIT - SIGNAL - 1,
        stop_method="lookback_low",
    )
    candles[EXIT] = replace(candles[EXIT], high=t.target + 1.0)
    ctx = trade_context(t, candles, CFG)
    assert ctx.ref_index == 36 and ctx.ref_method == "lookback_low"
    row = review_row(t, CFG, None, context=ctx)
    assert row["stop_ref_bar"] == "-9"
    assert row["stop_ref_low"] == "93.600000"
    assert row["stop_ref_time_utc"] == "2024-01-07T00:00:00Z"  # T0 + 36 * 4h
    assert auto_flags(t, CFG, ctx) == []
    # The context starts 30 candles before the fill (the structure candle is inside it).
    assert (ctx.first_index, len(ctx.rows)) == (16, EXIT - 16 + 1)


def test_context_csv_rows_marks_and_hash(tmp_path):
    candles = _pivot_candles()
    t = _pivot_trade(candles, trade_id=7)
    split = T0 + 10 * TF  # the trade is TEST
    paths = write_review_pack([t], tmp_path, "ctx", CFG, split_ts=split, data={t.pair: candles})
    _, [row] = _read_csv(paths["csv"])
    assert row["context_csv"] == "context/TEST_7.csv"
    ctx_path = tmp_path / "context" / "TEST_7.csv"
    assert row["context_sha256"] == hashlib.sha256(ctx_path.read_bytes()).hexdigest()
    rows = _read_context(ctx_path)
    assert tuple(rows[0]) == CONTEXT_CSV_COLUMNS
    # 30 candles before the fill candle (16..45, the signal candle is the last) through the
    # exit candle 50: 35 rows, bars -29 .. +5 relative to the signal candle.
    assert [int(r["index"]) for r in rows] == list(range(16, EXIT + 1))
    assert [int(r["bar"]) for r in rows] == list(range(16 - SIGNAL, EXIT - SIGNAL + 1))
    assert rows[0]["time_utc"] == "2024-01-03T16:00:00Z" and rows[0]["ts"] == str(T0 + 16 * TF)
    marks = {int(r["index"]): r["marks"] for r in rows if r["marks"]}
    assert marks == {PIVOT: "stop_ref_pivot", SIGNAL: "signal", 46: "entry", EXIT: "exit_SL"}
    for r in rows:
        c = candles[int(r["index"])]
        assert (r["open"], r["high"], r["low"], r["close"]) == tuple(
            f"{x:.8f}" for x in (c.open, c.high, c.low, c.close)
        )
        assert r["volume"] == f"{c.volume:.6f}"
    # MAE / MFE recomputed by hand from the context file itself match the sheet.
    unit = t.risk_amount / t.qty
    span = [r for r in rows if int(r["index"]) >= 46]
    mae = (t.entry_price - min(float(r["low"]) for r in span)) / unit
    mfe = (max(float(r["high"]) for r in span) - t.entry_price) / unit
    assert (row["mae_r"], row["mfe_r"]) == (f"{mae:.4f}", f"{mfe:.4f}")
    md = paths["md"].read_text(encoding="utf-8")
    assert "## Trade context (candles)" in md
    assert "[context/TEST_7.csv](context/TEST_7.csv)" in md
    assert f"pivot bar -5 (2024-01-07T16:00:00Z, low 97.000000) | {row['wick_depth_r']} |" in md
    assert "- Candle context: candles supplied by the caller; found for 1 of 1 trades" in md
    assert load_review(paths["csv"])[0].key == ("TEST", 7)


def test_context_reaches_back_to_an_old_pivot_and_its_left_side():
    # A pivot 30 candles before the signal (the oldest swing_lookback allows) is 31 before the
    # fill candle: the context extends to it and its k = 2 left neighbours.
    candles = _pivot_candles(pivot=SIGNAL - CFG.swing_lookback)
    t = _pivot_trade(candles)
    ctx = trade_context(t, candles, CFG)
    assert ctx.ref_index == 15
    assert ctx.first_index == 15 - CFG.swing_pivot_k
    assert len(ctx.rows) == EXIT - ctx.first_index + 1
    assert review_row(t, CFG, None, context=ctx)["stop_ref_bar"] == "-30"


@pytest.mark.parametrize(
    ("stop_distances", "gap", "deep"),
    [
        (0.39, False, False),
        (0.49, False, False),
        (0.51, False, True),
        (1.50, False, True),
        (1.50, True, False),  # gap-through: filled at the open, not by the touch model
    ],
)
def test_deep_wick_flag_fires_only_on_deep_non_gap_stop_outs(stop_distances, gap, deep):
    base = _pivot_candles()
    low = _low_at_depth(base, stop_distances)
    stop = _stop_of(base)
    candles = _pivot_candles(exit_low=low, exit_open=stop - 0.1 if gap else 99.2)
    t = _pivot_trade(candles)
    ctx = trade_context(t, candles, CFG)
    assert ctx.mismatches == ()
    assert ctx.wick_depth_sd == pytest.approx(stop_distances, rel=1e-9)
    codes = [flag_code(f) for f in auto_flags(t, CFG, ctx)]
    assert (FLAG_DEEP_WICK in codes) is deep
    assert FLAG_DEEP_WICK not in _codes(t)  # never without the candles
    if deep:
        assert codes == [FLAG_DEEP_WICK]
        [flag] = context_flags(t, ctx, CFG)
        assert f"is {stop_distances:.3f} stop distances ({ctx.wick_depth_r:.3f}R) below" in flag
        # The extra loss of the D4 wick fill, by hand from the pnl of the two fill prices.
        f = CFG.fee_rate

        def pnl(x):
            return t.qty * (x - t.entry_price) - f * t.qty * (t.entry_price + x)

        for k in (0.5, 1.0):
            touch, wick = stop * (1 - SLIP), (stop - k * (stop - low)) * (1 - SLIP)
            extra = (pnl(touch) - pnl(wick)) / t.risk_amount
            assert wick_fill_extra_r(t, low, k, CFG) == pytest.approx(extra, rel=1e-9)
            assert f"k={k:g} {extra:.3f}R" in flag
        assert ";" not in flag and "|" not in flag and "\n" not in flag


def test_deep_wick_threshold_is_in_stop_distances():
    assert DEEP_WICK_STOP_DISTANCES == 0.5
    assert FLAG_DEEP_WICK in FLAG_CODES and FLAG_DEEP_WICK not in _codes(_trade())


def test_context_mismatches_are_flagged():
    candles = _pivot_candles()
    t = _pivot_trade(candles)
    # The SL exit candle never reaches the stop in these candles.
    shallow = list(candles)
    shallow[EXIT] = _c(EXIT, 99.2, 99.4, 98.0, 98.5)
    [flag] = context_flags(t, trade_context(t, shallow, CFG), CFG)
    assert flag.startswith(f"{FLAG_CONTEXT_MISMATCH}: the SL exit candle")
    assert "never reached the stop" in flag
    # An earlier candle already hit the stop: the recorded exit is not the model's first.
    early = list(candles)
    early[48] = _c(48, 100.2, 102.5, 96.0, 101.0)
    [flag] = context_flags(t, trade_context(t, early, CFG), CFG)
    assert "the candle 2024-01-09T00:00:00Z already reached the stop before" in flag
    # A TP whose exit candle also touched the stop (the model fills the stop first).
    tp = _pivot_trade(candles, EXIT_TP)
    both = list(candles)
    both[EXIT] = _c(EXIT, 99.2, tp.target + 1, 95.0, 99.0)
    [flag] = context_flags(tp, trade_context(tp, both, CFG), CFG)
    assert "also reached the stop" in flag and "fills first" in flag
    # A fill candle that is not the candle after the signal.
    late = replace(t, entry_ts=t.entry_ts + TF)
    codes = [flag_code(f) for f in context_flags(late, trade_context(late, candles, CFG), CFG)]
    assert FLAG_CONTEXT_MISMATCH in codes


def test_stop_that_structure_does_not_reproduce_is_flagged():
    candles = _pivot_candles()
    t = _pivot_trade(candles)
    moved = replace(t, stop=t.stop * 0.99)
    [flag] = [
        f
        for f in auto_flags(moved, CFG, trade_context(moved, candles, CFG))
        if flag_code(f) == FLAG_STOP_MISMATCH
    ]
    assert "re-derived by structure.find_stop at the signal candle" in flag
    assert ";" not in flag
    relabelled = replace(t, stop_method="lookback_low")
    [flag] = context_flags(relabelled, trade_context(relabelled, candles, CFG), CFG)
    assert flag == (
        f"{FLAG_STOP_MISMATCH}: recorded stop method 'lookback_low' != 'pivot' re-derived at "
        "the signal candle"
    )
    # A different buffer (wrong config for these trades) is visible, not silent.
    wide = CFG.with_changes(
        pair_risk={**CFG.pair_risk, "BTC": replace(CFG.pair_risk["BTC"], stop_buffer_pct=0.5)}
    )
    assert FLAG_STOP_MISMATCH in [
        flag_code(f) for f in context_flags(t, trade_context(t, candles, wide), wide)
    ]


def test_missing_candles_are_flagged_and_leave_the_cells_blank(tmp_path):
    candles = _pivot_candles()
    t = _pivot_trade(candles)
    for data in ({}, {"ETH/USDT": candles}, {t.pair: candles[:48]}, {t.pair: candles[46:]}):
        ctx = trade_context(t, data.get(t.pair), CFG)
        assert not ctx.available
        assert [flag_code(f) for f in context_flags(t, ctx, CFG)] == [FLAG_CONTEXT_MISSING]
    [flag] = context_flags(t, trade_context(t, candles[:48], CFG), CFG)
    assert flag == (
        f"{FLAG_CONTEXT_MISSING}: no BTC/USDT candle in the supplied data for the exit candle "
        "2024-01-09T08:00:00Z"
    )
    paths = write_review_pack([t], tmp_path, "gap", CFG, data={"ETH/USDT": candles})
    _, [row] = _read_csv(paths["csv"])
    assert all(row[c] == "" for c in CONTEXT_COLUMNS)
    assert row["auto_flags"].startswith(FLAG_CONTEXT_MISSING)
    assert not (tmp_path / "context").exists()
    md = paths["md"].read_text(encoding="utf-8")
    assert "found for 0 of 1 trades; context_missing 1" in md
    assert "| 1 | ALL | BTC/USDT | SL | -1.0000 | | | | | missing |" in md


def test_without_data_the_context_is_blank_and_old_behaviour_unchanged(tmp_path):
    trades = _sample_trades()
    a = write_review_pack(trades, tmp_path / "a", "T", CFG, T0 + 25 * TF, {"R1_trend": 3})
    b = write_review_pack(trades, tmp_path / "b", "T", CFG, T0 + 25 * TF, {"R1_trend": 3}, None)
    assert a["csv"].read_bytes() == b["csv"].read_bytes()
    assert a["md"].read_bytes() == b["md"].read_bytes()
    header, rows = _read_csv(a["csv"])
    assert tuple(header) == EXPECTED_HEADER
    by_id = {t.trade_id: t for t in trades}
    for row in rows:
        assert all(row[c] == "" for c in CONTEXT_COLUMNS)
        trade = by_id[int(row["trade_id"])]
        assert row["auto_flags"] == "; ".join(auto_flags(trade, CFG))  # no context flags
        # Every pre-existing column holds exactly what it held before the context columns.
        old = review_row(trade, CFG, T0 + 25 * TF)
        assert {c: row[c] for c in LEGACY_HEADER} == {c: old[c] for c in LEGACY_HEADER}
    assert not (tmp_path / "a" / "context").exists()
    md = a["md"].read_text(encoding="utf-8")
    assert CONTEXT_UNAVAILABLE in md
    assert "- Candle context: unavailable" in md
    assert "- Flagged trades: 2 of 5" in md


def test_rewriting_a_pack_removes_stale_context_files(tmp_path):
    candles = _pivot_candles()
    t = _pivot_trade(candles)
    write_review_pack([t], tmp_path, "x", CFG, data={t.pair: candles})
    keep = tmp_path / "context" / "README.txt"
    keep.write_text("not ours", encoding="utf-8")
    assert (tmp_path / "context" / "ALL_1.csv").is_file()
    write_review_pack([replace(t, trade_id=2)], tmp_path, "x", CFG, data={t.pair: candles})
    assert sorted(p.name for p in (tmp_path / "context").iterdir()) == ["ALL_2.csv", "README.txt"]
    keep.unlink()
    write_review_pack([t], tmp_path, "x", CFG)
    assert not (tmp_path / "context").exists()


def test_test_only_banner_names_the_reason(tmp_path):
    # v4 D1: a stop-fill stress config is test-only too, and the banner says why.
    stressed = CFG.with_changes(stop_fill_wick_k=0.5)
    md = write_review_pack([], tmp_path / "k", "k", stressed)["md"].read_text(encoding="utf-8")
    assert "- **TEST-ONLY config: stop fills are stressed with stop_fill_wick_k=0.5" in md
    assert "regime filter is OFF" not in md
    both = stressed.with_changes(regime_filter=False)
    md = write_review_pack([], tmp_path / "b", "b", both)["md"].read_text(encoding="utf-8")
    assert "TEST-ONLY config: the R4 regime filter is OFF and stop fills are stressed" in md


# ---------------------------------------------------------------------------- with a backtest
def _walk_forward_trades(world, gaps=False):
    data, events = make_world(world, 1, years=3.0, gaps=gaps)
    times = [c.ts for c in data["BTC/USDT"]]
    split = times[int(len(times) * 0.7)]
    train = run_backtest(data, CFG, events, end_ts=split).trades
    test = run_backtest(data, CFG, events, start_ts=split).trades
    offset = max((t.trade_id for t in train), default=0)
    test = [replace(t, trade_id=t.trade_id + offset) for t in test]
    return data, split, train + test


@pytest.mark.parametrize(("world", "gaps"), [("planted", False), ("null", True)])
def test_backtest_trades_are_reproduced_by_their_candles(tmp_path, world, gaps):
    data, split, trades = _walk_forward_trades(world, gaps)
    assert len(trades) >= 20
    paths = write_review_pack(trades, tmp_path, "bt", CFG, split_ts=split, data=data)
    _, rows = _read_csv(paths["csv"])
    by_key = {(window_of(t, split), t.trade_id): t for t in trades}
    for row in rows:
        codes = {flag_code(f) for f in row["auto_flags"].split("; ") if f}
        # The candles reproduce every trade: no missing context, no contradiction and the
        # R8 stop re-derived at the signal candle is the recorded one.
        assert not codes & {FLAG_CONTEXT_MISSING, FLAG_CONTEXT_MISMATCH, FLAG_STOP_MISMATCH}
        t = by_key[(row["window"], int(row["trade_id"]))]
        assert row["context_csv"] == f"context/{row['window']}_{t.trade_id}.csv"
        assert row["stop_ref_bar"] and int(row["stop_ref_bar"]) <= 0
        assert float(row["mae_r"]) >= 0 and float(row["mfe_r"]) >= 0
        candles = data[t.pair]
        exit_c = next(c for c in candles if c.ts == t.exit_ts)
        if t.exit_reason == EXIT_SL:
            depth = (t.stop - exit_c.low) / (t.entry_price - t.stop)
            deep = depth > DEEP_WICK_STOP_DISTANCES and exit_c.open > t.stop
            assert (FLAG_DEEP_WICK in codes) is deep
            assert float(row["wick_depth_r"]) >= 0
        else:
            assert FLAG_DEEP_WICK not in codes and row["wick_depth_r"] == ""
        if t.exit_reason == EXIT_TP:
            assert float(row["mfe_r"]) >= (t.target - t.entry_price) / (t.risk_amount / t.qty)
    assert {r["window"] for r in rows} == {WINDOW_TRAIN, WINDOW_TEST}
    assert len(load_review(paths["csv"])) == len(trades)


# ---------------------------------------------------------------------------- load_review (v4)
def _drop_context_columns(csv_path):
    with csv_path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    keep = [i for i, c in enumerate(rows[0]) if c not in CONTEXT_COLUMNS]
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh, lineterminator="\n").writerows([[r[i] for i in keep] for r in rows])
    return csv_path


def test_load_review_accepts_a_pack_written_before_the_context_columns(tmp_path):
    split, trades = _split_trades()
    csv_path = write_review_pack(trades, tmp_path, "old", CFG, split_ts=split)["csv"]
    fresh = load_review(csv_path)
    _drop_context_columns(csv_path)
    header, _ = _read_csv(csv_path)
    assert tuple(header) == LEGACY_HEADER
    assert load_review(csv_path) == fresh


def test_load_review_reads_a_committed_legacy_or_current_pack():
    results = Path(__file__).resolve().parents[2] / "results"
    committed = results / "synthetic_planted_s1" / "review_base" / CSV_NAME
    if not committed.is_file():
        pytest.skip("no committed review pack")
    assert len(load_review(committed)) > 0


@pytest.mark.parametrize(
    "transform",
    [
        _header_edit(",wick_depth_r", ""),
        _header_edit("mae_r,mfe_r", "mfe_r,mae_r"),
        _header_edit(",context_sha256", ",context_hash"),
    ],
)
def test_load_review_rejects_partial_or_reordered_context_columns(tmp_path, transform):
    split, trades = _split_trades()
    csv_path = write_review_pack(trades, tmp_path, "ctx", CFG, split_ts=split)["csv"]
    _rewrite(csv_path, transform)
    with pytest.raises(ValueError, match=r"line 1: not a trades_review\.csv header") as info:
        load_review(csv_path)
    assert "trade-context columns must be exactly" in str(info.value)


def _context_pack(tmp_path):
    candles = _pivot_candles()
    t = _pivot_trade(candles)
    return write_review_pack([t], tmp_path, "c", CFG, data={t.pair: candles})["csv"]


def test_load_review_checks_the_context_files(tmp_path):
    csv_path = _context_pack(tmp_path)
    assert [r.key for r in load_review(csv_path)] == [("ALL", 1)]
    ctx_file = tmp_path / "context" / "ALL_1.csv"
    original = ctx_file.read_bytes()
    ctx_file.write_bytes(original.replace(b"95.00000000", b"96.00000000"))
    with pytest.raises(
        ValueError, match=r"line 2: context file .* does not match its context_sha256"
    ):
        load_review(csv_path)
    ctx_file.unlink()
    with pytest.raises(ValueError, match=r"line 2: context file .* is missing"):
        load_review(csv_path)
    ctx_file.write_bytes(original)
    assert len(load_review(csv_path)) == 1


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("context_csv", "../elsewhere/ALL_1.csv"),
        ("context_csv", "context/ALL_2.csv"),
        ("context_csv", ""),
        ("context_sha256", ""),
    ],
)
def test_load_review_rejects_a_foreign_context_reference(tmp_path, column, value):
    csv_path = _context_pack(tmp_path)
    _rewrite(csv_path, _cell_edit(2, column, value))
    with pytest.raises(ValueError, match="line 2: context_csv") as info:
        load_review(csv_path)
    assert "expected 'context/ALL_1.csv'" in str(info.value)


# ---------------------------------------------------------------------------- CLI (v4)
def test_cli_data_dir_adds_the_context(tmp_path, capsys):
    candles = _pivot_candles(exit_low=_low_at_depth(_pivot_candles(), 0.8))
    t = _pivot_trade(candles)
    data_dir = tmp_path / "data"
    save_candles_csv(candles, data_dir / pair_filename(t.pair, "4h"))
    journal = tmp_path / "trades.csv"
    write_journal([t], journal)
    out = tmp_path / "review"
    assert (
        main(["--journal", str(journal), "--out-dir", str(out), "--data-dir", str(data_dir)]) == 0
    )
    printed = capsys.readouterr().out
    assert "1 trades (1 flagged, candle context from 4h candle files in" in printed
    _, [row] = _read_csv(out / CSV_NAME)
    assert row["context_csv"] == "context/ALL_1.csv" and row["stop_ref_bar"] == "-5"
    assert flag_code(row["auto_flags"]) == FLAG_DEEP_WICK
    # Identical to the API with the same candles (the journal round-trip is exact).
    api = write_review_pack(
        [t], tmp_path / "api", f"Trade review: {journal.name}", CFG, data={t.pair: candles}
    )
    assert (out / CSV_NAME).read_bytes() == api["csv"].read_bytes()
    assert (out / "context" / "ALL_1.csv").read_bytes() == (
        tmp_path / "api" / "context" / "ALL_1.csv"
    ).read_bytes()
    md = (out / MD_NAME).read_text(encoding="utf-8")
    assert f"- Candle context: 4h candle files in {data_dir} (data.load_dataset)" in md


def test_cli_synthetic_world_adds_the_context(tmp_path, capsys):
    data, events = make_world("planted", 1, years=2.0)
    trades = run_backtest(data, CFG, events).trades
    journal = tmp_path / "trades.csv"
    write_journal(trades, journal)
    out = tmp_path / "review"
    args = ["--journal", str(journal), "--out-dir", str(out)]
    assert main([*args, "--synthetic", "planted", "--seed", "1", "--years", "2"]) == 0
    assert "candle context from synthetic world planted seed 1, 2 years" in capsys.readouterr().out
    api = write_review_pack(
        trades, tmp_path / "api", f"Trade review: {journal.name}", CFG, data=data
    )
    assert (out / CSV_NAME).read_bytes() == api["csv"].read_bytes()
    md = (out / MD_NAME).read_text(encoding="utf-8")
    assert "verification only, never evidence about real markets" in md
    assert f"found for {len(trades)} of {len(trades)} trades; context_missing 0, " in md
    assert "context_mismatch 0, stop_mismatch 0" in md


@pytest.mark.parametrize(
    "extra",
    [
        ["--seed", "3"],
        ["--years", "2"],
        ["--synthetic", "planted", "--data-dir", "x"],
        ["--synthetic", "nowhere"],
        ["--data-dir", "does-not-exist"],
    ],
)
def test_cli_rejects_bad_context_sources(tmp_path, extra):
    journal = tmp_path / "trades.csv"
    write_journal(_sample_trades(), journal)
    with pytest.raises(SystemExit) as exc:
        main(["--journal", str(journal), "--out-dir", str(tmp_path / "o"), *extra])
    assert exc.value.code == 2
    assert not (tmp_path / "o").exists()
