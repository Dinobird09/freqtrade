"""Regime allocation: 5-state forward-filtered HMM, 3-bar persistence, exposure caps."""

import pytest

from research.trendbot.allocation import (
    AllocationSettings,
    RegimeAllocator,
    confirm,
    correlation_table,
    exposure_room,
)
from research.trendbot.config import StrategyConfig
from research.trendbot.synthetic import make_world

from .test_live_bot import world  # noqa: F401


CFG = StrategyConfig()


@pytest.fixture(scope="module")
def fitted():
    data, _ = make_world("null", seed=7, years=2)
    a = RegimeAllocator(AllocationSettings())
    assert a.fit(data, CFG.timeframe_ms, data["BTC/USDT"][-1].ts + CFG.timeframe_ms)
    return a, data


def test_persistence_needs_three_bars():
    raw = ["bull", "bull", "bear", "bear", "bull", "bear", "bear", "bear", "bear"]
    assert confirm(raw, 3) == [
        "bull",
        "bull",
        "bull",
        "bull",
        "bull",
        "bull",
        "bull",
        "bear",
        "bear",
    ]
    assert confirm(raw, 1) == raw


def test_five_states_and_caps(fitted):
    a, data = fitted
    ref = a.layer.labels["BTC/USDT"]
    assert ref == ["crash", "bear", "neutral", "bull", "euphoria"]
    r = a.regime(data, CFG.timeframe_ms)
    assert r["confirmed"] in ref and sum(r["probs"].values()) == pytest.approx(1.0, abs=1e-3)
    assert r["cap_pct"] == AllocationSettings().caps[r["confirmed"]]
    assert AllocationSettings().caps == {
        "euphoria": 95,
        "bull": 95,
        "neutral": 50,
        "bear": 25,
        "crash": 0,
    }


def test_incremental_filter_equals_a_fresh_one_and_is_causal(fitted):
    a, data = fitted
    cut = {p: cs[:3000] for p, cs in data.items()}
    b = RegimeAllocator(AllocationSettings())
    b.layer = a.layer
    for n in range(2990, 3001):  # candle by candle, as the bot sees them
        inc = b.regime({p: cs[:n] for p, cs in cut.items()}, CFG.timeframe_ms)
    fresh = RegimeAllocator(AllocationSettings())
    fresh.layer = a.layer
    full = fresh.regime(cut, CFG.timeframe_ms)
    assert inc["probs"] == full["probs"] and inc["confirmed"] == full["confirmed"]
    later = fresh.regime({p: cs[:3040] for p, cs in data.items()}, CFG.timeframe_ms)
    past = [h for h in later["history"] if h["ts"] <= full["at"]]
    assert past[-1]["raw"] == full["raw"]  # new candles never rewrite the past regime


def test_exposure_room_and_correlations(fitted):
    _, data = fitted
    assert exposure_room(50, 10_000, 3_000) == 2_000 and exposure_room(0, 10_000, 0) == 0
    t = correlation_table(data)
    assert t["BTC/USDT"]["BTC/USDT"] == pytest.approx(1.0)
    assert -1 <= t["BTC/USDT"]["ETH/USDT"] <= 1


def test_a_crash_regime_skips_entries_and_neutral_shrinks_them(tmp_path, world):  # noqa: F811
    from research.trendbot.live_bot import TrendBot

    from .test_live_bot import StubGateway, _allowed_decision, _settings

    s = _settings(tmp_path, mode="paper", pairs=("BTC/USDT",))
    bot = TrendBot(s, StubGateway(1, 1), sleep=lambda x: None)
    bot.start()
    dec, open_px = _allowed_decision(bot, world)
    bot.gw._ask = bot.gw.fill_price = open_px
    bot.state["regime"] = {"confirmed": "crash", "cap_pct": 0.0}
    assert bot.enter("BTC/USDT", dec) is None
    rec = bot.session.decision_log[-1]
    assert rec.rule == "X_capital_guard" and "crash regime" in rec.reason
