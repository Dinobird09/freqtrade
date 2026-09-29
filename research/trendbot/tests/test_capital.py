"""Capital protection: daily / peak drawdown, the kill switch lock, losing streaks, maker-first."""

import json
from types import SimpleNamespace

import pytest

from research.trendbot.capital import CapitalGuard, CapitalSettings, fee_warning, read_lock
from research.trendbot.live_exchange import CcxtGateway

from .test_live_bot import FakeExchange, _bot, _settings, _start_ts, world  # noqa: F401


DAY = 86_400_000
T = 1_800_000_000_000 // DAY * DAY + 3_600_000  # 01:00 UTC


def _loss(ts, r=-1.0):
    return SimpleNamespace(exit_ts=ts, r_multiple=r)


def test_daily_drawdown_halves_then_flattens_and_freezes():
    g = CapitalGuard(CapitalSettings(), {})
    assert g.evaluate(T, 10_000, []).risk_scale == 1.0
    v = g.evaluate(T + 1000, 9_790, [])  # -2.1%
    assert v.risk_scale == 0.5 and v.block is None and v.flatten is None
    assert g.evaluate(T + 2000, 9_900, []).risk_scale == 0.5  # stays halved for the day
    v = g.evaluate(T + 3000, 9_690, [])  # -3.1%
    assert v.flatten and v.block and "frozen" in v.block
    assert g.evaluate(T + 4000, 9_690, []).flatten is None  # flattens once
    v = g.evaluate(T + DAY, 9_700, [])  # next day, still inside the 24h freeze
    assert v.block and v.risk_scale == 1.0
    v = g.evaluate(T + 3000 + 24 * 3_600_000 + 1, 9_700, [])
    assert v.block is None and v.risk_scale == 1.0  # a new day and the freeze is over


def test_peak_drawdown_kills():
    g = CapitalGuard(CapitalSettings(), {})
    g.evaluate(T, 12_000, [])
    g.evaluate(T + DAY, 11_000, [])
    v = g.evaluate(T + 2 * DAY, 10_790, [])  # 10.1% under the 12,000 peak
    assert v.kill and "10.08%" in v.kill


def test_losing_streak_stops_the_day():
    g = CapitalGuard(CapitalSettings(max_consecutive_losses=2), {})
    closed = [_loss(T - 3_600_000), _loss(T + 100, 2.0), _loss(T + 200), _loss(T + 300)]
    v = g.evaluate(T + 400, 10_000, closed)
    assert v.block and "2 losing trades in a row" in v.block
    assert g.evaluate(T + DAY, 10_000, closed).block is None  # a new day
    assert g.evaluate(T + 400, 10_000, closed[:3]).block is None


def test_settings_can_only_reduce_risk():
    with pytest.raises(ValueError):
        CapitalSettings(reduce_factor=1.5)
    with pytest.raises(ValueError):
        CapitalSettings(daily_reduce_pct=4, daily_flatten_pct=3)
    assert fee_warning(0.001, CapitalSettings()) is None and "above 0.10%" in fee_warning(
        0.0015, CapitalSettings()
    )


def test_gatekeeper_risk_scale_halves_the_risk(world):  # noqa: F811
    from research.trendbot.config import StrategyConfig
    from research.trendbot.gatekeeper import Gatekeeper
    from research.trendbot.indicators import compute_features

    data, _ = world
    cfg = StrategyConfig()
    gk = Gatekeeper(cfg, [])
    cs = data["BTC/USDT"]
    rows = compute_features(cs, cfg)
    i = next(
        i for i in range(1300, len(cs)) if gk.evaluate("BTC/USDT", cs, rows, i, {}, 10_000).allowed
    )
    full = gk.evaluate("BTC/USDT", cs, rows, i, {}, 10_000).risk_pct
    gk.risk_scale = 0.5
    assert gk.evaluate("BTC/USDT", cs, rows, i, {}, 10_000).risk_pct == pytest.approx(full / 2)
    gk.risk_scale = 3.0  # clamped: never more than the rules allow
    assert gk.evaluate("BTC/USDT", cs, rows, i, {}, 10_000).risk_pct == pytest.approx(full)


def test_kill_switch_writes_the_lock_and_blocks_restarts(tmp_path, world):  # noqa: F811
    data, _ = world
    ex = FakeExchange(data, _start_ts(data))
    s = _settings(tmp_path, layers={"auto_jobs": False})
    bot = _bot(s, ex)
    bot.start()
    bot.state["capital"]["peak_equity"] = 20_000.0  # equity 10,000 is 50% under that peak
    bot.step()
    assert bot.stop_requested
    lock = read_lock(bot.dir)
    assert lock and "drawdown from the equity peak" in lock["reason"]
    with pytest.raises(SystemExit, match=r"delete .*trading_halted\.lock"):
        _bot(s, FakeExchange(data, _start_ts(data))).start()
    (bot.dir / "trading_halted.lock").unlink()  # the human reviewed it and deleted the lock
    _bot(s, FakeExchange(data, _start_ts(data))).start()


def test_dashboard_refuses_to_start_a_halted_bot(tmp_path):
    from research.trendbot.dashboard import Controller

    d = tmp_path / "state"
    d.mkdir()
    (d / "trading_halted.lock").write_text(json.dumps({"reason": "peak drawdown 10.2%"}))
    settings = tmp_path / "bot.json"
    settings.write_text(json.dumps({"state_dir": str(d)}))
    ok, msg = Controller(d, settings)._start()
    assert not ok and "kill switch" in msg and "peak drawdown 10.2%" in msg


class MakerExchange:
    """A fake exchange with post-only limit orders that fill partially."""

    has = {"createPostOnlyOrder": True, "createLimitOrder": True}

    def __init__(self, fill_frac):
        self.fill_frac = fill_frac
        self.orders = []

    def load_markets(self):
        return {}

    def market(self, pair):
        return {
            "limits": {"amount": {"min": 0.0001}, "cost": {"min": 5}},
            "precision": {"amount": 1e-6},
        }

    def amount_to_precision(self, pair, qty):
        return f"{qty:.6f}"

    def milliseconds(self):
        return 1_700_000_000_000

    def fetch_ticker(self, pair):
        return {"bid": 100.0, "ask": 100.2, "last": 100.1}

    def create_order(self, pair, kind, side, qty, price=None, params=None):
        o = {
            "id": str(len(self.orders)),
            "type": kind,
            "side": side,
            "amount": qty,
            "price": price,
            "params": params,
            "status": "open" if kind == "limit" else "closed",
            "filled": 0.0 if kind == "limit" else qty,
            "average": price if kind == "limit" else 100.2,
            "fee": {"cost": 0.0, "currency": "USDT"},
        }
        self.orders.append(o)
        return dict(o)

    def fetch_order(self, oid, pair):
        o = self.orders[int(oid)]
        if o["type"] == "limit" and o["status"] == "open":
            o["filled"] = o["amount"] * self.fill_frac
            o["status"] = "closed" if self.fill_frac >= 1 else "open"
        return dict(o)

    def cancel_order(self, oid, pair):
        self.orders[int(oid)]["status"] = "canceled"


def test_maker_first_buys_at_the_bid_and_tops_up_at_market():
    ex = MakerExchange(fill_frac=1.0)
    gw = CcxtGateway(ex, sleep=lambda s: None)
    f = gw.buy_maker_first("BTC/USDT", 1.0, wait_s=3)
    assert f.qty == 1.0 and f.price == 100.0 and ex.orders[0]["params"] == {"postOnly": True}
    assert len(ex.orders) == 1  # filled as maker: no market order
    ex = MakerExchange(fill_frac=0.4)
    gw = CcxtGateway(ex, sleep=lambda s: None)
    f = gw.buy_maker_first("BTC/USDT", 1.0, wait_s=3)
    assert [o["type"] for o in ex.orders] == ["limit", "market"] and ex.orders[0][
        "status"
    ] == "canceled"
    assert f.qty == pytest.approx(1.0) and f.price == pytest.approx(0.4 * 100.0 + 0.6 * 100.2)
