"""Weekly retraining, the layer registry, operator overrides and the schedule."""

import json
from datetime import UTC, datetime

from research.trendbot.config import StrategyConfig
from research.trendbot.data import save_candles_csv
from research.trendbot.layers import (
    Layer,
    LayerBook,
    LayerFilter,
    MarketView,
    register,
    set_layer_override,
    validate_layer,
)
from research.trendbot.live_bot import BotSettings
from research.trendbot.models import SignalCheck
from research.trendbot.retrain import due, run_retrain
from research.trendbot.synthetic import make_world


CFG = StrategyConfig()


@register
class EdgeHours(Layer):
    """Test layer: vetoes signals outside 12-20 UTC (the planted edge of 'hour_edge')."""

    name = "test_edge_hours"
    kind = "test"

    def fit(self, ctx):
        self.params["fitted_on"] = len(ctx.examples())

    def veto(self, pair, row, view):
        assert all(
            c.ts + CFG.timeframe_ms <= row.close_ts for c in view.history(pair, row.close_ts)
        )
        return (not 12 <= row.hour_utc <= 20, f"hour {row.hour_utc} outside 12-20 UTC")


@register
class NeverHelps(Layer):
    name = "test_random"
    kind = "test"

    def fit(self, ctx):
        pass

    def veto(self, pair, row, view):
        return (row.ts // CFG.timeframe_ms) % 2 == 0, "coin flip"


def _ms(iso):
    return int(datetime.fromisoformat(iso).replace(tzinfo=UTC).timestamp() * 1000)


def _state(tmp_path, world):
    data, _ = make_world(world, seed=1, years=6)
    for pair, cs in data.items():
        save_candles_csv(cs, tmp_path / "candles" / f"{pair.replace('/', '_')}-4h.csv")
    return data


def test_validation_promotes_a_real_edge_and_rejects_noise():
    data, events = make_world("hour_edge", seed=1, years=6)
    ok = validate_layer(EdgeHours, data, CFG, events=events)
    assert ok["status"] == "active" and ok["gain_r"] > 0.05
    bad = validate_layer(NeverHelps, data, CFG, events=events)
    assert bad["status"] == "rejected"
    tiny = {p: c[:300] for p, c in data.items()}
    assert validate_layer(EdgeHours, tiny, CFG)["status"] == "collecting"


def test_retrain_writes_registry_and_models_and_the_bot_reloads(tmp_path):
    data = _state(tmp_path, "hour_edge")
    s = BotSettings(
        state_dir=str(tmp_path),
        history_candles=1200,
        layers={"enabled": ["test_edge_hours", "test_random", "nope"]},
    )
    status = run_retrain(s, CFG, now_ms=data["BTC/USDT"][-1].ts + CFG.timeframe_ms)
    assert status["layers"]["test_edge_hours"]["status"] == "active"
    assert status["layers"]["test_random"]["status"] == "rejected"
    assert status["layers"]["nope"]["status"] == "unavailable"
    reg = json.loads((tmp_path / "layers.json").read_text())
    assert reg["layers"]["test_edge_hours"]["gain_r"] > 0
    assert (tmp_path / "models" / "test_edge_hours.json").exists()
    assert not (tmp_path / "models" / "test_random.json").exists()
    book = LayerBook(tmp_path, CFG, enabled=["test_edge_hours"])
    assert sorted(book.layers) == ["test_edge_hours"]
    assert book.layers["test_edge_hours"].params["fitted_on"] > 0
    # operator switch-off survives the next retrain and is picked up by reload()
    set_layer_override(tmp_path, "test_edge_hours", "disabled")
    assert book.reload() and book.layers == {}
    run_retrain(s, CFG, now_ms=data["BTC/USDT"][-1].ts + CFG.timeframe_ms)
    assert LayerBook(tmp_path, CFG).status("test_edge_hours") == "disabled"
    set_layer_override(tmp_path, "test_edge_hours", None)
    assert LayerBook(tmp_path, CFG).status("test_edge_hours") == "active"


def test_layer_filter_names_the_layer_and_survives_a_broken_layer():
    class Broken(Layer):
        name = "broken"

        def veto(self, pair, row, view):
            raise RuntimeError("boom")

    data, _ = make_world("null", seed=2, years=1)
    view = MarketView(data, CFG.timeframe_ms)
    from research.trendbot.indicators import compute_features

    rows = compute_features(data["BTC/USDT"], CFG)
    row = next(r for r in rows[300:] if not 12 <= r.hour_utc <= 20)
    f = LayerFilter([Broken(), EdgeHours()], view)
    ok, _, why = f("BTC/USDT", row, SignalCheck("BTC/USDT", row.ts, ()))
    assert not ok and "test_edge_hours" in why and f.rule_id == "L_signal_layer"
    assert LayerFilter([Broken()], view)("BTC/USDT", row, SignalCheck("BTC/USDT", 0, ()))[0]


def test_market_view_is_causal():
    data, _ = make_world("null", seed=3, years=1)
    view = MarketView(data, CFG.timeframe_ms)
    c = data["ETH/USDT"]
    until = c[500].ts + CFG.timeframe_ms
    hist = view.history("ETH/USDT", until)
    assert hist[-1].ts == c[500].ts and len(view.history("ETH/USDT", until, n=10)) == 10
    assert view.history("ETH/USDT", until - 1)[-1].ts == c[499].ts


def test_schedule_collect_hourly_and_retrain_weekly():
    sun = _ms("2026-09-27T01:30:00")  # a Sunday
    assert due({}, {}, sun) == ["collect", "retrain"]
    done = {"collect": sun - 10 * 60_000, "retrain": sun - 20 * 60_000}
    assert due({}, done, sun) == []
    assert due({}, done, sun + 60 * 60_000) == ["collect"]
    next_week = _ms("2026-10-04T01:00:00")
    assert "retrain" not in due({}, done, next_week - 60_000)
    assert "retrain" in due({}, done, next_week)
    assert due({"collect_every_minutes": 0}, {}, sun) == ["retrain"]
