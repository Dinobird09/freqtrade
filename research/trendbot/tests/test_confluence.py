"""The confluence system: trade cards in a clean uptrend, NO TRADE in a range or against the
daily trend, the builder's overrides, sentiment and lower-timeframe checks, the day rules
of the backtest engine and the look-ahead detector."""

import math
import random

import pytest

from research.trendbot import confluence as cf
from research.trendbot import lab
from research.trendbot.models import HOUR_MS, Candle
from research.trendbot.strategies import (
    STRATEGIES,
    Confluence,
    Signal,
    Strategy,
    atr,
    backtest,
)
from research.trendbot.synthetic import make_world


T0 = 1_704_067_200_000  # 2024-01-01 00:00 UTC
M5 = 300_000
LABELS = (
    "Trade Type:",
    "Entry Price:",
    "Stop Loss:",
    "Take Profit (TP1, TP2):",
    "Risk:Reward Ratio:",
    "Timeframes Used:",
    "Confluence Factors:",
    "Market Bias:",
    "Confidence Score (0–100%):",  # noqa: RUF001
)


def trend(days=260, up=0.0012, down=0.0024, cyc=48, pull=12, seed=5, sigma=0.002):
    """1H candles: legs of ``cyc - pull`` hours drifting ``up`` per hour on strong volume,
    then ``pull`` hours of pullback drifting ``-down`` on light volume (negative values
    give the mirror-image downtrend)."""
    rng = random.Random(seed)
    out, px = [], 100.0
    for t in range(days * 24):
        leg = t % cyc < cyc - pull
        r = (up if leg else -down) + rng.gauss(0, sigma)
        o, c = px, px * math.exp(r)
        hi = max(o, c) * (1 + abs(rng.gauss(0, sigma / 2)))
        lo = min(o, c) * (1 - abs(rng.gauss(0, sigma / 2)))
        vol = 1000 * (1.6 if leg else 0.6) * (0.8 + 0.4 * rng.random())
        out.append(Candle(T0 + t * HOUR_MS, o, hi, lo, c, vol))
        px = c
    return out


def sideways(days=260, seed=9):
    rng = random.Random(seed)
    out = []
    for t in range(days * 24):
        mid = 100 * (1 + 0.03 * math.sin(t / 40)) * (1 + rng.gauss(0, 0.002))
        o = out[-1].close if out else mid
        out.append(
            Candle(T0 + t * HOUR_MS, o, max(o, mid) * 1.001, min(o, mid) * 0.999, mid, 1000.0)
        )
    return out


@pytest.fixture(scope="module")
def up():
    return trend()


@pytest.fixture(scope="module")
def up_signals(up):
    sig = Confluence().signals(up)
    assert sig, "the clean uptrend must produce confluence entries"
    return sig


def test_registered_with_the_documented_api():
    assert STRATEGIES["confluence"] is Confluence and cf.Confluence is Confluence
    assert set(cf.DEFAULT_REQUIRED) <= set(cf.SIGNALS) and len(cf.SIGNALS) == 11
    assert all(isinstance(v, str) and v for v in cf.SIGNALS.values())
    p = Confluence().p
    for k in ("required", "min_signals", "risk_pct", "max_trades_per_day", "stop_after_losses"):
        assert k in p
    assert p["timeframe"] == "1h" and Confluence(timeframe="4h").timeframe == "4h"
    assert Confluence().history_candles >= 240 * 24  # a converged 1D EMA200 from 1H candles
    with pytest.raises(ValueError, match="risk_pct"):
        Confluence(risk_pct=3)
    with pytest.raises(ValueError, match="unknown confluence signals"):
        Confluence(required=["ema200", "astrology"])


def test_trade_card_on_an_uptrend_pullback(up, up_signals):
    i = up_signals[0].i
    res = cf.analyze(up[: i + 1], equity=10_000)
    assert res["decision"] == "trade" and res["trade_type"] == "Long", res.get("reasons")
    a = atr(up[: i + 1], 14)[-1]
    entry, risk = res["entry"], res["entry"] - res["stop"]
    assert entry == up[i].close and risk == pytest.approx(1.5 * a)
    assert res["tp1"] == pytest.approx(entry + 2 * risk)
    assert res["tp2"] == pytest.approx(entry + 3 * risk)
    assert res["rr"] >= 2 and res["rr"] == pytest.approx(3.0)
    assert len(res["confluence"]) >= 5 and res["timeframes"] == ["1D", "4H", "1H"]
    assert res["market_bias"].startswith("Bullish") and 0 <= res["confidence"] <= 100
    assert set(res["signals"]) == set(cf.SIGNALS)
    assert all(res["signals"][k]["ok"] for k in cf.DEFAULT_REQUIRED)
    assert res["risk_pct"] == 1.0 and res["size"] == pytest.approx(100 / risk)
    card = cf.format_card(res)
    for label in LABELS:
        assert label in card, label
    assert "Trade Type: Long" in card and "Risk:Reward Ratio: 1:3" in card
    assert card.count("\n- ") >= 5  # the confluence factors, one per line
    # the lab signal is the same decision: next open, 1.5 ATR stop, half at 2R, rest at 3R
    s = up_signals[0]
    assert s.stop_atr == pytest.approx(risk) and s.rr == 2.0 and s.rr2 == 3.0
    assert s.partial == 0.5


def test_no_trade_in_a_sideways_market():
    res = cf.analyze(sideways())
    assert res["decision"] == "no_trade" and res["text"] == cf.NO_TRADE_TEXT
    assert res["reasons"][0].startswith("1D market bias is sideways")
    assert res["market_bias"].startswith("Sideways")
    card = cf.format_card(res)
    assert card.startswith(cf.NO_TRADE_TEXT) and "Reasons:" in card
    assert not Confluence().signals(sideways())


def test_never_long_against_a_bearish_daily_trend():
    down = trend(up=-0.0012, down=-0.0024)
    assert not Confluence().signals(down)  # the lab strategy is long-only: nothing here
    px, rally = down[-1].close, []
    for k in range(30):  # a sharp, high-volume 1H rally at the end
        o, px = px, px * 1.004
        rally.append(Candle(down[-1].ts + (k + 1) * HOUR_MS, o, px * 1.001, o * 0.999, px, 4000))
    res = cf.analyze(down + rally)
    assert res["market_bias"].startswith("Bearish")
    assert not (res["decision"] == "trade" and res["trade_type"] == "Long")
    # the mirror-image setups are reported as shorts (watch only: the bot trades spot)
    ctx = cf._Ctx(down, dict(cf.DEFAULTS), HOUR_MS)
    j = next(
        k
        for k in range(len(down) - 800, len(down))
        if ctx.decide(k, -1, required=None, min_signals=5)["ok"]
    )
    short = cf.analyze(down[: j + 1])
    assert short["decision"] == "trade" and short["trade_type"] == "Short"
    assert short["stop"] > short["entry"] > short["tp1"] > short["tp2"]
    assert "spot" in short["note"] and "Trade Type: Short" in cf.format_card(short)


def test_required_and_min_signals_override(up, up_signals):
    i = up_signals[0].i
    strict = cf.analyze(up[: i + 1], required=list(cf.SIGNALS), min_signals=11)
    if strict["decision"] == "no_trade":  # every one of the eleven must pass
        assert any("failed" in r or "aligned" in r for r in strict["reasons"])
    loose = {"required": ["ema200", "vwap"], "min_signals": 5}
    more = Confluence(**loose).signals(up)
    extra = sorted({s.i for s in more} - {s.i for s in up_signals})
    assert extra, "dropping required signals must allow more entries"
    j = extra[0]
    assert cf.analyze(up[: j + 1])["decision"] == "no_trade"
    res = cf.analyze(up[: j + 1], **loose)
    assert res["decision"] == "trade" and res["aligned"] >= 5
    # ``use``: only these (plus the required ones) count toward min_signals
    n_ok = sum(1 for v in res["signals"].values() if v["ok"])
    narrow = cf.analyze(up[: j + 1], required=["ema200"], use=["vwap"], min_signals=2)
    assert narrow["decision"] == "trade" and narrow["aligned"] == 2 < n_ok
    assert len(narrow["signals"]) == 11  # still computed and shown
    with pytest.raises(ValueError, match="more than"):
        cf.analyze(up[: j + 1], required=["ema200"], use=["vwap"], min_signals=3)
    with pytest.raises(ValueError, match="unknown"):
        cf.analyze(up[: j + 1], use=["moon_phase"])


def test_fear_and_greed(up, up_signals):
    i = up_signals[0].i
    cs = up[: i + 1]
    t = cs[-1].ts + HOUR_MS
    greedy = cf.analyze(cs, fear_greed=90)
    assert greedy["decision"] == "no_trade"
    assert any("extreme greed" in r for r in greedy["reasons"])
    fear = cf.analyze(cs, fear_greed=20)
    assert fear["decision"] == "trade" and any("extreme fear" in f for f in fear["confluence"])
    assert fear["confidence"] > cf.analyze(cs)["confidence"]
    # a series: only values already known at the decision time count
    assert cf.analyze(cs, fear_greed=[(t - HOUR_MS, 90)])["decision"] == "no_trade"
    assert cf.analyze(cs, fear_greed=[(t + HOUR_MS, 90)])["decision"] == "trade"


def _lower(t_end, prices, last):
    """5m candles ending at ``t_end``: closes ``prices`` then the candle ``last`` (o, h, l, c)."""
    n = len(prices) + 1
    out = [
        Candle(t_end - (n - k) * M5, p, p * 1.0005, p * 0.9995, p, 10.0)
        for k, p in enumerate(prices)
    ]
    return out + [Candle(t_end - M5, *last, 30.0)]


def test_lower_timeframe_execution_check(up, up_signals):
    i = up_signals[0].i
    cs = up[: i + 1]
    t, px = cs[-1].ts + HOUR_MS, cs[-1].close
    dip = [px, px * 0.998, px * 0.996, px * 0.995, px * 0.997]  # under VWAP, then back
    good = _lower(t, dip, (px * 0.997, px * 1.004, px * 0.9965, px * 1.0035))
    res = cf.analyze(cs, lower=good)
    assert res["decision"] == "trade" and res["timeframes"] == ["1D", "4H", "1H", "5M"]
    assert any("VWAP reclaimed" in f for f in res["confluence"])
    above = [px * 1.01, px * 1.011, px * 1.012]  # never below VWAP: nothing reclaimed
    bad = _lower(t, above, (px * 1.0, px * 1.013, px * 0.999, px * 1.0125))
    res = cf.analyze(cs, lower=bad)
    assert res["decision"] == "no_trade" and any("no VWAP reclaim" in r for r in res["reasons"])


def test_day_rules_in_the_backtest():
    def run(day_bar, **p):
        cs = [Candle(k * HOUR_MS, 100, *day_bar(k), 100, 100.0) for k in range(48)]
        sigs = [
            Signal(k, cs[k].ts, "next_open", None, 99, None, stop_atr=1.0, rr=2.0)
            for k in range(47)
        ]
        return backtest(cs, Strategy(**p), signals=sigs, fee=0.0, slip_pct=0.0)

    losing = lambda k: (100.5, 98.0)  # noqa: E731 - every entry is stopped in its bar
    winning = lambda k: (103.0, 99.5)  # noqa: E731 - every entry reaches 2R in its bar
    assert len(run(losing).trades) == 47  # no rules: one trade per bar
    r = run(losing, stop_after_losses=2)
    days = [t.entry_i * HOUR_MS // 86_400_000 for t in r.trades]
    assert days == [0, 0, 1, 1] and all(t.r == pytest.approx(-1) for t in r.trades)
    assert r.stats["skipped_by_day_rules"] > 0
    r = run(winning, max_trades_per_day=3, stop_after_losses=2)
    assert len(r.trades) == 6 and all(t.r == pytest.approx(2) for t in r.trades)
    cs = [Candle(k * HOUR_MS, 100, 111, 99.5, 100, 1.0) for k in range(3)]
    wide = [Signal(0, 0, "next_open", None, 95, None, stop_atr=5.0, rr=2.0)]
    for pct, risk in ((None, 100), (2.0, 200)):  # the strategy's risk_pct sizes the trade
        p = {} if pct is None else {"risk_pct": pct}
        r = backtest(cs, Strategy(**p), signals=wide, fee=0.0, slip_pct=0.0)
        assert r.trades[0].risk == pytest.approx(risk)


def test_second_target_after_the_partial():
    cs = [Candle(k * HOUR_MS, 100, 101, 99, 100, 1) for k in range(5)]
    cs += [Candle(5 * HOUR_MS, 100, 111, 99.5, 110, 1), Candle(6 * HOUR_MS, 110, 116, 109, 115, 1)]
    s = Signal(4, cs[4].ts, "next_open", None, 0, None, stop_atr=5.0, rr=2.0, partial=0.5, rr2=3.0)
    t = backtest(cs, Strategy(), signals=[s], fee=0.0, slip_pct=0.0).trades[0]
    assert [(e[3], e[2]) for e in t.exits] == [("TP", 110.0), ("TP2", 115.0)]
    assert t.r == pytest.approx((10 * 10 + 10 * 15) / 100)  # 20 units: half +10, half +15
    back = cs[:6] + [Candle(6 * HOUR_MS, 110, 110.5, 99, 100, 1)]
    t = backtest(back, Strategy(), signals=[s], fee=0.0, slip_pct=0.0).trades[0]
    assert [e[3] for e in t.exits] == ["TP", "BE"] and t.r == pytest.approx(1.0)


def test_confluence_backtest_respects_the_day_limit(up):
    r = backtest(up, Confluence(required=["ema200"], min_signals=6))
    assert r.trades
    per_day: dict[int, int] = {}
    for t in r.trades:
        d = up[t.entry_i].ts // 86_400_000
        per_day[d] = per_day.get(d, 0) + 1
    assert max(per_day.values()) <= 3


def test_confluence_never_looks_ahead(up):
    ok, why = lab.lookahead_check(Confluence(), up)
    assert ok, why
    data, _ = make_world("planted", seed=3, years=3)
    ok, why = lab.lookahead_check(
        Confluence(timeframe="4h", required=["ema200"], min_signals=6), data["BTC/USDT"]
    )
    assert ok, why


def test_lab_bot_runs_confluence_with_its_day_rules(tmp_path, monkeypatch):
    from research.trendbot.journal import read_journal
    from research.trendbot.lab_bot import LabBot
    from research.trendbot.live_bot import BotSettings
    from research.trendbot.live_exchange import CcxtGateway, PaperBroker
    from research.trendbot.models import Trade

    from .test_live_bot import FakeExchange

    monkeypatch.setenv("TRENDBOT_TRADERS_DIR", str(tmp_path))
    data, _ = make_world("planted", seed=3, years=3)
    params = {"timeframe": "4h", "required": ["ema200"], "min_signals": 6}
    s = BotSettings(
        engine="lab",
        mode="paper",
        pairs=("BTC/USDT",),
        state_dir=str(tmp_path / "conf-bot"),
        poll_seconds=3600.0,
        history_candles=1200,
        lab={"strategy": "confluence", "params": params},
    )
    cs = data["BTC/USDT"]
    ex = FakeExchange(data, cs[1500].ts + 2000)
    gw = PaperBroker(CcxtGateway(ex, sleep=lambda x: None), fee_rate=0.001, slippage_pct=0.05)
    bot = LabBot(s, gw, sleep=ex.advance)
    bot.start()
    while ex.now < cs[1950].ts:
        bot.step()
        ex.advance(3600)
    trades = read_journal(bot.journal_path)
    assert trades and all(t.variant == "confluence" for t in trades)
    per_day: dict[int, int] = {}
    for t in trades:
        per_day[t.entry_ts // 86_400_000] = per_day.get(t.entry_ts // 86_400_000, 0) + 1
    assert max(per_day.values()) <= 3
    for t in trades:
        if t.is_closed and t.exit_reason == "TP" and "second target" in t.notes:
            assert t.r_multiple > 2  # half at 2R, the rest at 3R
    # the day rules, live: two losses closed today stop new entries until tomorrow
    now = ex.now // 86_400_000 * 86_400_000 + 12 * 3_600_000  # midday UTC
    loss = lambda k: Trade(  # noqa: E731
        900 + k, "BTC/USDT", "confluence", now - 9_000_000, now - 9_000_000, 100, 95, 110, 1,
        5, 1, exit_ts=now - 1000 * (3 - k), exit_price=95, exit_reason="SL", pnl=-5,
    )  # fmt: skip
    bot.state["open"] = None
    bot.trades = [loss(0), loss(1)]
    assert "2 losses in a row" in bot.day_blocked(now)
    bot.trades = [loss(0)]
    assert bot.day_blocked(now) is None
    bot.trades = [loss(0), loss(1), loss(2)]
    bot.trades[1].pnl = 5.0  # win, loss: no streak, but three entries today
    assert "daily maximum of 3" in bot.day_blocked(now)
    assert bot.day_blocked(now + 86_400_000) is None  # a new day


def test_lab_bot_sells_the_rest_at_the_second_target(tmp_path, monkeypatch):
    from research.trendbot.lab_bot import LabBot
    from research.trendbot.live_bot import BotSettings
    from research.trendbot.live_exchange import Fill

    monkeypatch.setenv("TRENDBOT_TRADERS_DIR", str(tmp_path))

    class Gw:
        px, held, cash = 100.0, 0.0, 10_000.0

        def now_ms(self):
            return T0

        def bid(self, pair):
            return self.px

        ask = bid

        def free(self, asset):
            return self.held if asset == "BTC" else self.cash

        def round_qty(self, pair, q):
            return math.floor(q * 1e6) / 1e6

        def min_order(self, pair):
            return 1e-6, 5.0

        def buy(self, pair, q):
            self.held += q
            self.cash -= q * self.px
            return Fill(q, self.px, 0.0, T0, "b")

        def sell(self, pair, q):
            self.held -= q
            self.cash += q * self.px
            return Fill(q, self.px, 0.0, T0, "s")

    s = BotSettings(
        engine="lab",
        mode="paper",
        pairs=("BTC/USDT",),
        state_dir=str(tmp_path / "tp2"),
        poll_seconds=1.0,
        history_candles=1200,
        lab={"strategy": "confluence", "params": {"timeframe": "4h"}},
    )
    gw = Gw()
    bot = LabBot(s, gw)
    bot.start()
    order = {"ts": T0, "stop_atr": 5.0, "target": None, "rr": 2.0, "partial": 0.5}
    bot._buy({**order, "trail_atr": None, "rr2": 3.0, "reason": "test"}, 100.0, 1.0)
    o = bot.state["open"]
    assert o["target"] == pytest.approx(o["entry"] + 2 * (o["entry"] - o["stop"]))
    assert o["target2"] == pytest.approx(o["entry"] + 3 * (o["entry"] - o["stop"]))
    gw.px = o["target"] + 0.01
    bot.manage(gw.px)
    assert bot.state["open"]["exits"] and bot.state["open"]["stop"] >= o["entry"]
    gw.px = o["target2"] + 0.01
    bot.manage(gw.px)
    assert bot.state["open"] is None
    t = bot.trades[-1]
    assert t.exit_reason == "TP" and "second target" in t.notes
    per_unit = 5.0 + bot.cfg.fee_rate * (o["entry"] + o["stop"])  # the all-in planned risk
    assert t.r_multiple == pytest.approx(12.5 / per_unit, abs=0.01)  # half +10, half +15
