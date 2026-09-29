"""Strategy lab: strategies are causal, the engine sizes and exits correctly, the research
loop discards leaks and proposes only what beats the bar, incubation gates real money."""

import json

import pytest

from research.trendbot import lab
from research.trendbot.models import Candle
from research.trendbot.strategies import (
    NNFX,
    STRATEGIES,
    FibFVG,
    Signal,
    SneakyPivot,
    Strategy,
    backtest,
    elliott_valid,
)
from research.trendbot.synthetic import make_world


DAY = 86_400_000
M15 = 900_000


@pytest.fixture(scope="module")
def world4h():
    data, _ = make_world("planted", seed=3, years=3)
    return data


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("TRENDBOT_TRADERS_DIR", str(tmp_path))


def _c(ts, o, h, lo, c, v=100.0):
    return Candle(ts, o, h, lo, c, v)


@pytest.mark.parametrize("name", ["nnfx", "fib_fvg"])
def test_strategies_never_look_ahead(world4h, name):
    ok, why = lab.lookahead_check(STRATEGIES[name](), world4h["BTC/USDT"])
    assert ok, why


def test_the_detector_catches_a_leaky_strategy(world4h):
    class Leaky(Strategy):
        name = "leaky"

        def signals(self, cs, flow=None):  # buys when the NEXT candle closes higher: a leak
            return [
                Signal(
                    i,
                    cs[i].ts,
                    "next_open",
                    None,
                    cs[i].close * 0.98,
                    None,
                    stop_atr=cs[i].close * 0.02,
                )
                for i in range(len(cs) - 1)
                if cs[i + 1].close > cs[i].close * 1.01
            ]

    ok, why = lab.lookahead_check(Leaky(), world4h["BTC/USDT"])
    assert not ok and "looks ahead" in why
    ok, why = lab.believable({"cagr_pct": 11_000, "sharpe": 9})
    assert not ok and "discarded" in why


def test_sneaky_pivot_boundary_entry():
    d0 = 1_800_000_000_000 // DAY * DAY
    cs = []
    for k in range(96):  # yesterday: a 100-110 range
        px = 100 + 10 * (k % 48) / 47
        cs.append(_c(d0 + k * M15, px, px + 0.05, px - 0.05, px))
    t = d0 + DAY
    for k in range(20):  # today: drifting mid-range, small candles
        cs.append(_c(t + k * M15, 104, 104.2, 103.8, 104))
    t += 20 * M15
    cs.append(_c(t, 104, 104.1, 100.0, 100.2))  # the impulse drop taps the Range Low (100)
    cs.append(_c(t + M15, 100.2, 101.0, 100.1, 100.9))  # the sneaky green pivot candle
    sig = SneakyPivot(impulse_atr=1.0).signals(cs)
    assert sig, "expected a signal at the green pivot candle"
    s = sig[-1]
    assert s.entry == "stop" and s.entry_price == 101.0 and s.target == pytest.approx(110.05)
    assert s.stop == pytest.approx(100.0 * (1 - 0.0005)) and "Range Low" in s.reason
    mid = cs[:-2] + [_c(t, 107, 107.1, 104.0, 104.2), _c(t + M15, 104.2, 105, 104.1, 104.9)]
    assert not [
        x for x in SneakyPivot(impulse_atr=1.0).signals(mid) if x.i == len(mid) - 1
    ]  # mid-chart


def test_engine_risks_one_percent_and_trails_after_the_partial():
    cs = [_c(i * DAY, 100, 101, 99, 100) for i in range(5)]
    cs += [
        _c(5 * DAY, 100, 110, 99.5, 109),
        _c(6 * DAY, 109, 125, 108, 124),
        _c(7 * DAY, 124, 124.5, 112, 113),
    ]
    sig = Signal(
        4, cs[4].ts, "next_open", None, 0, None, stop_atr=5.0, rr=2.0, partial=0.5, trail_atr=6.0
    )
    r = backtest(cs, Strategy(), signals=[sig], fee=0.0, slip_pct=0.0)
    t = r.trades[0]
    assert t.entry == 100 and t.stop == 95 and t.target == 110
    assert t.risk == pytest.approx(100.0)  # 1% of 10,000 at risk
    assert [e[3] for e in t.exits] == ["TP", "TRAIL"]  # half at 2R, the rest trailed out
    assert t.exits[1][2] == pytest.approx(118.0)  # 124 best close - 6
    assert t.r == pytest.approx((10 * 10 + 10 * 18) / 100)  # 20 units: half at +10, half at +18
    loser = [_c(i * DAY, 100, 101, 99, 100) for i in range(5)] + [_c(5 * DAY, 100, 100.5, 90, 91)]
    r = backtest(
        loser,
        Strategy(),
        signals=[Signal(4, loser[4].ts, "next_open", None, 0, None, stop_atr=5.0)],
        fee=0.001,
        slip_pct=0.05,
    )
    assert r.trades[0].r == pytest.approx(-1.0, abs=0.02) and r.stats[
        "return_pct"
    ] == pytest.approx(-1.0, abs=0.05)


def test_elliott_hard_rules():
    assert elliott_valid([100, 110, 104, 125, 115, 130])
    assert not elliott_valid([100, 110, 104, 108, 105, 130])  # wave 3 the shortest
    assert not elliott_valid([100, 110, 104, 125, 108, 130])  # wave 4 overlaps wave 1


def test_walk_forward_rolls_and_counts_only_out_of_sample(world4h):
    cs = world4h["BTC/USDT"]
    wf = lab.rolling_walk_forward(cs, NNFX, [{}, {"baseline": 30}])
    assert len(wf["windows"]) == 4  # 3 years: 252-day IS + 182-day OOS, rolling by 182 days
    first = wf["windows"][0]
    assert first["in_sample"][1] < first["out_of_sample"][0]
    assert wf["oos"]["trades"] == sum(w["oos"]["trades"] for w in wf["windows"])


def test_crash_injection_and_stress(world4h):
    cs = world4h["ETH/USDT"][:1000]
    crashed = lab.inject_crash(cs, 50, 0.10)
    day = sorted({c.ts // DAY for c in cs})[50]
    after = [(a, b) for a, b in zip(cs, crashed, strict=True) if a.ts // DAY > day]
    assert all(b.close == pytest.approx(a.close * 0.9) for a, b in after)
    st = lab.stress_test(cs, FibFVG(), runs=4)
    assert st["runs"] == 4 and st["worst_max_dd_pct"] >= 0


def test_research_run_proposal_approval_and_incubation(tmp_path, world4h, monkeypatch):
    from research.trendbot.data import save_candles_csv

    save_candles_csv(world4h["BTC/USDT"], tmp_path / "candles" / "BTC_USDT-4h.csv")
    monkeypatch.setattr(lab, "MAX_BELIEVABLE_SHARPE", 6.0)
    res = lab.run(
        tmp_path, n_mutations=2, stress_runs=2, min_sharpe=-99
    )  # a low bar: something proposes
    assert (tmp_path / "lab" / "program.md").read_text().startswith("# Strategy lab")
    assert res["proposals"], res["leaderboard"]
    pid = res["proposals"][0]["id"]
    assert lab.incubation(tmp_path, pid, "live")[0] is False  # not approved yet
    a = lab.approve(tmp_path, pid, now_ms=1_000)
    assert a["status"] == "incubating"
    ok, why = lab.incubation(tmp_path, pid, "live", now_ms=40 * DAY)
    assert not ok and "not paper-traded" in why
    lab.mark_paper_start(pid, 2 * DAY)
    assert "incubated 10 of 30 days" in lab.incubation(tmp_path, pid, "testnet", now_ms=12 * DAY)[1]
    assert lab.incubation(tmp_path, pid, "live", now_ms=33 * DAY)[0]
    assert lab.incubation(tmp_path, pid, "paper")[0]
    assert json.loads(lab.approved_path().read_text())[pid]["paper_since_ms"] == 2 * DAY


def test_lab_bot_paper_trades_nnfx_and_refuses_live_without_incubation(tmp_path, world4h):
    from research.trendbot.journal import read_journal
    from research.trendbot.lab_bot import LabBot
    from research.trendbot.live_bot import BotSettings
    from research.trendbot.live_exchange import CcxtGateway, PaperBroker

    from .test_live_bot import FakeExchange

    data = world4h
    s = BotSettings(
        engine="lab",
        mode="paper",
        pairs=("BTC/USDT",),
        state_dir=str(tmp_path / "lab-bot"),
        poll_seconds=3600.0,
        history_candles=1200,
        lab={"strategy": "nnfx"},
    )
    start = data["BTC/USDT"][1500].ts + 2000
    ex = FakeExchange(data, start)
    bot = LabBot(
        s,
        PaperBroker(CcxtGateway(ex, sleep=lambda x: None), fee_rate=0.001, slippage_pct=0.05),
        sleep=ex.advance,
    )
    bot.start()
    end = data["BTC/USDT"][2600].ts
    while ex.now < end:
        bot.step()
        ex.advance(3600)
    trades = read_journal(bot.journal_path)
    assert trades and all(t.variant == "nnfx" for t in trades)
    for t in trades:
        if t.is_closed and t.exit_reason == "SL" and not (t.notes or "").count("trailing"):
            assert t.r_multiple == pytest.approx(-1.0, abs=0.35)  # a plain stop-out loses about 1R
    live = BotSettings(**{**s.__dict__, "mode": "live", "state_dir": str(tmp_path / "live")})
    with pytest.raises(SystemExit, match="not approved"):
        LabBot(live, bot.gw).start()
