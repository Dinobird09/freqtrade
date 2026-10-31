"""Grid and DCA engines: the backtest simulators by hand, and the live bots against the fake
exchange (testnet through CcxtGateway, paper through PaperBroker)."""

import json
import math

import pytest

from research.trendbot import engines
from research.trendbot.engines import (
    DcaBot,
    GridBot,
    dca_backtest,
    grid_backtest,
    grid_levels,
    make_bot,
)
from research.trendbot.journal import read_journal
from research.trendbot.live_bot import BotSettings, post_request
from research.trendbot.live_exchange import CcxtGateway, PaperBroker
from research.trendbot.models import EXIT_END, EXIT_TP, Candle
from research.trendbot.synthetic import make_world

from .test_live_bot import FakeExchange


DAY = 86_400_000
H4 = 14_400_000


def _c(k, o, h, lo, c, step=DAY):
    return Candle(k * step, o, h, lo, c, 1.0)


@pytest.fixture(scope="module")
def world():
    data, _ = make_world("planted", seed=3, years=2.0)
    return data


# ---------------------------------------------------------------------- grid backtest
def test_grid_levels():
    assert grid_levels({"lower": 90, "upper": 110, "levels": 3}) == [90, 100, 110]
    geo = grid_levels({"lower": 100, "upper": 121, "step_pct": 10})
    assert geo == pytest.approx([100, 110, 121])
    with pytest.raises(ValueError):
        grid_levels({"lower": 110, "upper": 100})


def test_one_grid_round_trip_by_hand():
    cs = [_c(0, 105, 105, 95, 95), _c(1, 95, 112, 95, 112)]  # down through 100, up through 110
    r = grid_backtest(cs, {"lower": 90, "upper": 110, "levels": 3, "budget": 200}, 0.0, 0.0)
    assert r["unit_quote"] == 100 and r["round_trips"] == 1 and r["grid_fills"] == 2
    t = r["trades"][0]
    assert (t["buy_price"], t["sell_price"], t["qty"]) == (100, 110, pytest.approx(1.0))
    assert r["realized_profit"] == pytest.approx(10.0) and r["open_units"] == 0
    assert r["final_equity"] == pytest.approx(210.0)
    assert r["time_out_of_range_pct"] == pytest.approx(50.0)  # closed above 110 once
    with_fees = grid_backtest(cs, {"lower": 90, "upper": 110, "levels": 3, "budget": 200})
    assert 0 < with_fees["realized_profit"] < 10


def test_grid_earns_in_a_range_and_warns_in_a_trend():
    osc = []
    for k in range(400):  # oscillates 95-105 around 100, never leaving the 90-110 grid
        o, c = 100 + 5 * math.sin(k / 6), 100 + 5 * math.sin((k + 1) / 6)
        osc.append(_c(k, o, max(o, c) + 0.3, min(o, c) - 0.3, c, H4))
    p = {"lower": 90, "upper": 110, "levels": 11, "budget": 1000}
    r = grid_backtest(osc, p)
    assert r["realized_profit"] > 0 and r["round_trips"] > 20
    assert r["time_out_of_range_pct"] == 0 and r["warnings"] == []
    assert "sideways" in r["note"]
    assert r["realized_profit"] + r["unrealized_profit"] == pytest.approx(
        r["final_equity"] - 1000, abs=1e-6
    )
    rising = [
        _c(k, 95 + k * 0.2, 95.3 + k * 0.2, 94.9 + k * 0.2, 95.2 + k * 0.2, H4) for k in range(300)
    ]
    t = grid_backtest(rising, p)
    assert t["time_out_of_range_pct"] > 50 and t["time_above_pct"] > 50
    assert "left the grid range" in t["warnings"][0]
    stop = grid_backtest(
        [_c(0, 105, 105, 95, 96), _c(1, 96, 97, 80, 82)], {**p, "stop_below": 85}, 0.0, 0.0
    )
    assert stop["stopped"] and stop["open_units"] == 0
    assert all(x["exit"] == "stop_below" for x in stop["trades"])
    assert engines.backtest("grid", osc, p)["round_trips"] == r["round_trips"]
    with pytest.raises(ValueError, match="unknown engine"):
        engines.backtest("martingale", osc, p)


# ---------------------------------------------------------------------- dca backtest
def test_dca_average_cost_and_lump_sum():
    cs = [_c(0, 100, 100, 100, 100), _c(1, 50, 50, 50, 50), _c(2, 80, 80, 80, 80)]
    r = dca_backtest(cs, {"amount_quote": 100, "every": "1d"}, 0.0, 0.0)
    assert r["n_buys"] == 3 and r["invested"] == 300
    qty = 1 + 2 + 1.25
    assert r["qty_held"] == pytest.approx(qty)
    assert r["average_cost"] == pytest.approx(300 / qty)  # quote spent / units held
    assert r["value"] == pytest.approx(qty * 80)
    assert r["return_pct"] == pytest.approx((qty * 80 / 300 - 1) * 100, abs=1e-3)
    assert r["lump_sum_return_pct"] == pytest.approx(-20.0)  # 300 at 100, worth 80 each
    assert r["vs_lump_sum_pct"] == pytest.approx(r["return_pct"] + 20)
    weekly = dca_backtest(cs, {"amount_quote": 100, "every": "7d"}, 0.0, 0.0)
    assert weekly["n_buys"] == 1
    assert engines.backtest("dca", cs, {"amount_quote": 100, "every": "1d"}) is not None


def test_dca_buy_the_dip_and_take_profit():
    cs = [_c(0, 100, 100, 100, 100), _c(1, 95, 95, 85, 88), _c(2, 88, 130, 88, 125)]
    p = {"amount_quote": 100, "every": "7d", "buy_the_dip": 10, "dip_amount_quote": 50}
    r = dca_backtest(cs, p, 0.0, 0.0)
    assert [b["kind"] for b in r["buys"]] == ["scheduled", "dip"]
    assert r["buys"][1]["price"] == pytest.approx(90)  # the dip level, 10% under the average
    avg = 150 / (1 + 50 / 90)
    assert r["average_cost"] == pytest.approx(avg)
    tp = dca_backtest(cs, {**p, "take_profit_pct": 20}, 0.0, 0.0)
    assert tp["sells"][0]["price"] == pytest.approx(avg * 1.2) and tp["qty_held"] == 0
    assert tp["realized_profit"] == pytest.approx(150 * 0.2)


# ---------------------------------------------------------------------- live bots
def _settings(tmp_path, engine, mode, params, name):
    return BotSettings(
        engine=engine,
        mode=mode,
        pairs=("BTC/USDT",),
        state_dir=str(tmp_path / name),
        poll_seconds=3600.0,
        history_candles=1200,
        lab={engine: params},
    )


def _gateways(world, start):
    ex1 = FakeExchange(world, start)
    ex2 = FakeExchange(world, start)
    paper = PaperBroker(CcxtGateway(ex2, sleep=lambda x: None), fee_rate=0.001, slippage_pct=0.05)
    return [("testnet", ex1, CcxtGateway(ex1, sleep=lambda x: None)), ("paper", ex2, paper)]


def test_grid_bot_trades_round_trips_and_journals_them(tmp_path, world):
    cs = world["BTC/USDT"]
    window = cs[1500:1600]
    lo, hi = min(c.close for c in window), max(c.close for c in window)
    p = {"lower": lo, "upper": hi, "levels": 12, "budget": 3000}
    for mode, ex, gw in _gateways(world, cs[1500].ts + 2000):
        s = _settings(tmp_path, "grid", mode, p, f"grid-{mode}")
        bot = make_bot(s, gw, sleep=ex.advance)
        assert isinstance(bot, GridBot)
        bot.start()
        bot.run(max_steps=400)
        trades = read_journal(bot.journal_path)
        closed = [t for t in trades if t.is_closed]
        assert closed, mode
        assert all(t.variant == "grid" and t.exit_reason == EXIT_TP for t in closed)
        assert all(t.pnl > 0 for t in closed)  # each round trip earns one grid step net
        held = sum(t.qty for t in trades if not t.is_closed)
        assert gw.free("BTC") == pytest.approx(held, abs=1e-6)
        state = json.loads((tmp_path / f"grid-{mode}" / "grid_state.json").read_text())
        assert state["engine"] == "grid" and len(state["units"]) == len(trades) - len(closed)
        assert state["fills"] == len(trades) + len(closed)
        beat = json.loads((tmp_path / f"grid-{mode}" / "heartbeat.json").read_text())
        assert beat["status"] == "stopped" and beat["mode"] == mode
        post_request(bot.dir, action="close")
        bot.step()
        after = read_journal(bot.journal_path)
        assert all(t.is_closed for t in after) and gw.free("BTC") < 1e-5


def test_dca_bot_buys_on_schedule_and_flattens(tmp_path, world):
    cs = world["BTC/USDT"]
    p = {"amount_quote": 100, "every": "1d", "buy_the_dip": 3}
    for mode, ex, gw in _gateways(world, cs[1500].ts + 2000):
        s = _settings(tmp_path, "dca", mode, p, f"dca-{mode}")
        bot = DcaBot(s, gw, sleep=ex.advance)
        bot.start()
        bot.run(max_steps=400)  # 400 hours: 17 daily buys plus any dip buys
        lots = read_journal(bot.journal_path)
        state = json.loads((tmp_path / f"dca-{mode}" / "dca_state.json").read_text())
        scheduled = [t for t in lots if "scheduled" in t.notes]
        assert len(scheduled) == 17 and len(lots) == len(state["lots"])
        assert all(not t.is_closed and t.variant == "dca" for t in lots)
        assert gw.free("BTC") == pytest.approx(sum(t.qty for t in lots), abs=1e-6)
        spent = sum(t.qty * t.entry_price + t.fees for t in lots)
        assert bot.average_cost() == pytest.approx(spent / sum(t.qty for t in lots))
        assert state["invested"] == pytest.approx(spent)
        again = DcaBot(s, gw, sleep=ex.advance)  # a restart keeps the schedule and the lots
        again.start()
        assert again.state["next_buy_ms"] == state["next_buy_ms"]
        assert again.state["lots"] == state["lots"]
        closed = again.flatten("test")
        assert len(closed) == len(lots) and all(t.exit_reason == EXIT_END for t in closed)
        assert all(t.is_closed for t in read_journal(bot.journal_path))
        assert gw.free("BTC") < 1e-5


def test_dca_take_profit_closes_every_lot(tmp_path, world):
    cs = world["BTC/USDT"]
    ex = FakeExchange(world, cs[1500].ts + 2000)
    gw = CcxtGateway(ex, sleep=lambda x: None)
    s = _settings(tmp_path, "dca", "testnet", {"amount_quote": 200, "every": "1d"}, "dca-tp")
    bot = DcaBot(s, gw, sleep=ex.advance)
    bot.start()
    bot.run(max_steps=48)
    avg = bot.average_cost()
    bot.tp = (gw.bid("BTC/USDT") / avg - 1) * 100 - 0.01  # just under the current price
    bot.step()
    lots = read_journal(bot.journal_path)
    assert lots and all(t.exit_reason == EXIT_TP for t in lots)
    with pytest.raises(SystemExit, match="unknown engine"):
        make_bot(BotSettings(**{**s.__dict__, "engine": "martingale"}), gw)
