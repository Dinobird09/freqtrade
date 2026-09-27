from dataclasses import replace

import pytest

from research.trendbot.config import RULE_IDS, ConfigError, StrategyConfig
from research.trendbot.models import FeatureRow
from research.trendbot.signals import (
    GATE_RULE_IDS,
    INSUFFICIENT_HISTORY,
    REGIME_DISABLED,
    check_entry,
)


CFG = StrategyConfig()
GATE_ORDER = ("trend", "momentum", "volume", "regime")


def _row(**overrides) -> FeatureRow:
    """A row that passes all four gates; override fields to break one."""
    base = FeatureRow(
        ts=1_704_067_200_000,
        close_ts=1_704_067_200_000 + 4 * 3_600_000,
        close=105.0,
        ema_fast=103.0,
        ema_slow=100.0,
        ema_regime=90.0,
        rsi=60.0,
        vol_avg=100.0,
        vol_ratio=2.0,
        ema_gap_pct=(103.0 - 100.0) / 105.0 * 100,
        dist_regime_pct=(105.0 - 90.0) / 90.0 * 100,
        hour_utc=4,
    )
    return replace(base, **overrides)


def _gates(check):
    return {g.name: g for g in check.gates}


def _only_failure(check) -> str:
    """Assert exactly one gate failed (all four still evaluated) and return its name."""
    assert tuple(g.name for g in check.gates) == GATE_ORDER
    failed = check.failed()
    assert len(failed) == 1, failed
    assert not check.passed
    return failed[0].name


def test_all_gates_pass_in_order():
    check = check_entry("BTC/USDT", _row(), CFG)
    assert check.passed
    assert check.pair == "BTC/USDT"
    assert check.ts == 1_704_067_200_000
    assert tuple(g.name for g in check.gates) == GATE_ORDER
    g = _gates(check)
    assert g["trend"].detail == "close 105.00 > EMA9 103.00 > EMA21 100.00"
    assert g["momentum"].detail == "RSI 60.00 within [50, 70]"
    assert g["volume"].detail == "volume 2.00x the previous-20-candle average (>= 1.5x)"
    assert g["regime"].detail == "close 105.00 above EMA200 90.00 (+16.67%)"


def test_gate_rule_ids_are_registered():
    assert tuple(GATE_RULE_IDS) == GATE_ORDER
    assert all(rule in RULE_IDS for rule in GATE_RULE_IDS.values())


# ------------------------------------------------------------------------------ R1 trend
@pytest.mark.parametrize(
    "close, fast, slow",
    [
        (103.0, 103.0, 100.0),  # close == EMA9 fails the strict >
        (102.0, 103.0, 100.0),  # close below EMA9
        (105.0, 100.0, 100.0),  # EMA9 == EMA21
        (105.0, 99.0, 100.0),  # EMA9 below EMA21 although close is above both
        (100.0, 99.0, 100.0),  # close == EMA21
    ],
)
def test_trend_gate_fails(close, fast, slow):
    check = check_entry("BTC/USDT", _row(close=close, ema_fast=fast, ema_slow=slow), CFG)
    assert _only_failure(check) == "trend"
    assert "not above" in _gates(check)["trend"].detail


def test_trend_detail_lists_every_broken_condition():
    detail = _gates(check_entry("ETH/USDT", _row(close=95.0), CFG))["trend"].detail
    assert detail == "close 95.00 not above EMA9 103.00; close 95.00 not above EMA21 100.00"


def test_trend_detail_widens_numbers_that_would_look_equal():
    detail = _gates(check_entry("BTC/USDT", _row(close=103.001), CFG))["trend"].detail
    assert detail == "close 103.001 > EMA9 103.000 > EMA21 100.00"
    detail = _gates(check_entry("BTC/USDT", _row(close=103.0), CFG))["trend"].detail
    assert detail == "close 103.00 not above EMA9 103.00"


# ------------------------------------------------------------------------------ R2 momentum
@pytest.mark.parametrize("rsi", [50.0, 55.5, 70.0])
def test_momentum_bounds_inclusive(rsi):
    assert check_entry("BTC/USDT", _row(rsi=rsi), CFG).passed


@pytest.mark.parametrize(
    "rsi, detail",
    [
        (70.01, "RSI 70.01 outside [50, 70]"),
        (72.4, "RSI 72.40 outside [50, 70]"),
        (49.99, "RSI 49.99 outside [50, 70]"),
        (70.0001, "RSI 70.0001 outside [50, 70]"),  # never printed as "70.00"
        (0.0, "RSI 0.00 outside [50, 70]"),
        (100.0, "RSI 100.00 outside [50, 70]"),
    ],
)
def test_momentum_fails_outside_window(rsi, detail):
    check = check_entry("BTC/USDT", _row(rsi=rsi), CFG)
    assert _only_failure(check) == "momentum"
    assert _gates(check)["momentum"].detail == detail


def test_momentum_uses_tightened_window():
    cfg = CFG.with_changes(rsi_min=55.0)
    check = check_entry("BTC/USDT", _row(rsi=54.9), cfg)
    assert _only_failure(check) == "momentum"
    assert _gates(check)["momentum"].detail == "RSI 54.90 outside [55, 70]"
    assert check_entry("BTC/USDT", _row(rsi=55.0), cfg).passed


# ------------------------------------------------------------------------------ R3 volume
def test_volume_exactly_at_multiple_passes():
    check = check_entry("BTC/USDT", _row(vol_ratio=1.5), CFG)
    assert check.passed
    assert _gates(check)["volume"].detail == (
        "volume 1.50x the previous-20-candle average (>= 1.5x)"
    )


@pytest.mark.parametrize(
    "ratio, shown", [(1.4999, "1.4999"), (1.49, "1.49"), (0.0, "0.00"), (1.2, "1.20")]
)
def test_volume_below_multiple_fails(ratio, shown):
    check = check_entry("BTC/USDT", _row(vol_ratio=ratio), CFG)
    assert _only_failure(check) == "volume"
    assert _gates(check)["volume"].detail == (
        f"volume {shown}x the previous-20-candle average, below 1.5x"
    )


def test_volume_uses_tightened_multiple():
    cfg = CFG.with_changes(vol_mult=2.0)
    assert _only_failure(check_entry("BTC/USDT", _row(vol_ratio=1.9), cfg)) == "volume"
    assert check_entry("BTC/USDT", _row(vol_ratio=2.0), cfg).passed


# ------------------------------------------------------------------------------ R4 regime
@pytest.mark.parametrize("ema_regime", [105.0, 110.0])
def test_regime_close_not_above_ema200_fails(ema_regime):
    check = check_entry("BNB/USDT", _row(ema_regime=ema_regime, dist_regime_pct=None), CFG)
    assert _only_failure(check) == "regime"
    assert _gates(check)["regime"].detail.startswith("close 105.00 not above EMA200")


def test_regime_detail_shows_distance():
    check = check_entry("BTC/USDT", _row(ema_regime=110.0, dist_regime_pct=-4.545454), CFG)
    assert _gates(check)["regime"].detail == "close 105.00 not above EMA200 110.00 (-4.55%)"


@pytest.mark.parametrize("ema_regime", [200.0, 105.0, None])
def test_regime_disabled_is_an_explicit_pass(ema_regime):
    cfg = CFG.with_changes(regime_filter=False)
    assert cfg.is_test_only
    check = check_entry("BTC/USDT", _row(ema_regime=ema_regime, dist_regime_pct=None), cfg)
    assert check.passed
    regime = _gates(check)["regime"]
    assert regime.passed
    assert regime.detail == REGIME_DISABLED == "disabled (explicit test variant)"


# ------------------------------------------------------------------------------ config guard
@pytest.mark.parametrize(
    "changes",
    [
        {"ema_fast": 8},  # R1: EMA periods are fixed at 9/21/200
        {"ema_slow": 20},
        {"ema_regime": 100},  # R4
        {"rsi_period": 10},  # R2: RSI(14)
        {"rsi_min": 45.0},  # R2: the window may only shrink inside [50, 70]
        {"rsi_max": 75.0},
        {"rsi_min": 70.0},  # empty window
        {"vol_mult": 1.4},  # R3: the multiple may only rise above 1.5
        {"vol_lookback": 10},  # R3: previous-20-candle average
    ],
)
def test_config_refuses_loosened_entry_gates(changes):
    with pytest.raises(ConfigError):
        CFG.with_changes(**changes)


# ------------------------------------------------------------------------------ None inputs
@pytest.mark.parametrize(
    "field, gate",
    [
        ("ema_fast", "trend"),
        ("ema_slow", "trend"),
        ("rsi", "momentum"),
        ("vol_ratio", "volume"),
        ("ema_regime", "regime"),
    ],
)
def test_none_input_fails_only_its_gate(field, gate):
    check = check_entry("BTC/USDT", _row(**{field: None}), CFG)
    assert _only_failure(check) == gate
    assert _gates(check)[gate].detail == INSUFFICIENT_HISTORY == "insufficient history"


def test_all_gates_evaluated_even_when_all_fail():
    row = _row(ema_fast=None, rsi=80.0, vol_ratio=1.0, ema_regime=120.0, dist_regime_pct=None)
    check = check_entry("BTC/USDT", row, CFG)
    assert tuple(g.name for g in check.failed()) == GATE_ORDER
    details = [g.detail for g in check.gates]
    assert details[0] == "insufficient history"
    assert details[1] == "RSI 80.00 outside [50, 70]"
    assert details[2] == "volume 1.00x the previous-20-candle average, below 1.5x"
    assert details[3] == "close 105.00 not above EMA200 120.00"


def test_nan_inputs_never_pass():
    nan = float("nan")
    row = _row(ema_fast=nan, rsi=nan, vol_ratio=nan, ema_regime=nan)
    check = check_entry("BTC/USDT", row, CFG)
    assert tuple(g.name for g in check.failed()) == GATE_ORDER
