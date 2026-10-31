"""Bot health wiring: the gateways' order log, uptime from the heartbeat, alerts to a webhook
(never blocking or breaking the bot), the dashboard's stale-heartbeat alert and "health"."""

import json
import math
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from research.trendbot import scorecard as sc
from research.trendbot.live_bot import BotSettings, TrendBot, drain_order_log
from research.trendbot.live_exchange import CcxtGateway, OrderError, PaperBroker
from research.trendbot.models import EXIT_TP, Candle

from .test_live_bot import TF, StubGateway, _allowed_decision, _settings, world  # noqa: F401
from .test_live_resilience import ccxt  # noqa: F401 - the fake ccxt exception classes


ZAPIER = "https://hooks.zapier.com/hooks/catch/123/abc/"


def _flat(pair_px=100.0, n=20):
    return {"BTC/USDT": [Candle(i * TF, pair_px, pair_px, pair_px, pair_px, 1.0) for i in range(n)]}


# ---------------------------------------------------------------------- the order log
class MakerExchange:
    """A ccxt-shaped exchange at a flat price with post-only limit orders that fill
    ``fraction`` of their size, then rest until cancelled."""

    id = "binance"
    timeframes = {"4h": 1}
    has = {"createPostOnlyOrder": True}

    def __init__(self, fraction=0.4, reject_limit=None):
        self.now = 5 * TF
        self.fraction = fraction
        self.reject_limit = reject_limit
        self.balances = {"USDT": 10_000.0}
        self.book = {}
        self.cancelled = []

    def milliseconds(self):
        return self.now

    def load_markets(self):
        return {}

    def market(self, pair):
        return {"limits": {"amount": {"min": 1e-6}, "cost": {"min": 5.0}}}

    def amount_to_precision(self, pair, qty):
        return f"{math.floor(qty * 1e6) / 1e6:.6f}"

    def price_to_precision(self, pair, price):
        return f"{price:.2f}"

    def fetch_ticker(self, pair):
        return {"bid": 100.0, "ask": 100.0, "last": 100.0}

    def fetch_balance(self):
        return {k: {"free": v, "total": v} for k, v in self.balances.items()}

    def _book(self, pair, qty, px, fee_rate):
        base, quote = pair.split("/")
        self.balances[quote] -= qty * px * (1 + fee_rate)
        self.balances[base] = self.balances.get(base, 0.0) + qty
        return {"cost": qty * px * fee_rate, "currency": quote}

    def create_order(self, pair, kind, side, qty, price=None, params=None):
        oid = f"o{len(self.book) + 1}"
        if kind == "market":
            o = {"id": oid, "status": "closed", "filled": qty, "average": 100.0}
            o["fee"] = self._book(pair, qty, 100.0, 0.001)
        else:
            assert kind == "limit" and params["postOnly"]
            if self.reject_limit is not None:
                raise self.reject_limit("Order would immediately match and take")
            filled = qty * self.fraction
            o = {"id": oid, "status": "closed" if filled >= qty else "open", "filled": filled}
            if filled:
                o.update(average=price, fee=self._book(pair, filled, price, 0.0002))
        o["timestamp"] = self.now
        self.book[oid] = o
        return dict(o)

    def fetch_order(self, oid, pair, params=None):
        return dict(self.book[oid])

    def cancel_order(self, oid, pair):
        self.cancelled.append(oid)
        self.book[oid]["status"] = "canceled"


def _gw(ex):
    return CcxtGateway(ex, sleep=lambda s: None)


def test_ccxt_market_orders_are_logged_as_taker(tmp_path):
    from .test_live_bot import FakeExchange

    ex = FakeExchange(_flat(), 5 * TF)
    gw = _gw(ex)
    gw.buy("BTC/USDT", 2.0)
    gw.sell("BTC/USDT", 2.0)
    buy, sell = gw.order_log
    assert buy["type"] == "market" and buy["side"] == "buy" and buy["liquidity"] == "taker"
    assert buy["status"] == "filled" and buy["qty_filled"] == pytest.approx(2.0)
    assert buy["notional"] == pytest.approx(200.0) and buy["fee_quote"] == pytest.approx(0.2)
    assert buy["order_id"] == "1" and sell["order_id"] == "2" and sell["side"] == "sell"
    assert set(buy) == {
        "ts", "pair", "side", "type", "qty_requested", "qty_filled", "price", "notional",
        "fee_quote", "liquidity", "status", "order_id",
    }  # fmt: skip
    assert drain_order_log(gw, tmp_path) == 2 and gw.order_log == []
    assert [r["side"] for r in sc.read_jsonl(tmp_path / "orders.jsonl")] == ["buy", "sell"]
    with pytest.raises(AssertionError):  # the fake refuses: insufficient funds
        gw.buy("BTC/USDT", 1e6)
    assert gw.order_log[-1]["status"] == "rejected" and gw.order_log[-1]["qty_filled"] == 0


def test_maker_first_logs_the_post_only_order_and_the_taker_top_up():
    ex = MakerExchange(fraction=0.4)
    gw = _gw(ex)
    fill = gw.buy_maker_first("BTC/USDT", 10.0, wait_s=2)
    assert fill.qty == pytest.approx(10.0) and ex.cancelled == ["o1"]
    maker, taker = gw.order_log
    assert maker["type"] == "post-only" and maker["status"] == "partial"
    assert maker["liquidity"] == "maker" and maker["qty_filled"] == pytest.approx(4.0)
    assert maker["notional"] == pytest.approx(400.0) and maker["fee_quote"] == pytest.approx(0.08)
    assert taker["type"] == "market" and taker["liquidity"] == "taker"
    assert taker["qty_requested"] == pytest.approx(6.0) and taker["status"] == "filled"

    ex = MakerExchange(fraction=0.0)  # nothing filled while it waited: cancelled, then market
    gw = _gw(ex)
    gw.buy_maker_first("BTC/USDT", 10.0, wait_s=1)
    unfilled, rest = gw.order_log
    assert unfilled["status"] == "cancelled" and unfilled["notional"] == 0.0
    assert unfilled["price"] is None and rest["qty_filled"] == pytest.approx(10.0)

    ex = MakerExchange(fraction=1.0)  # filled at once: one maker order, no top-up
    gw = _gw(ex)
    gw.buy_maker_first("BTC/USDT", 10.0)
    (only,) = gw.order_log
    assert only["status"] == "filled" and only["liquidity"] == "maker" and ex.cancelled == []


def test_a_refused_post_only_order_is_logged_rejected(ccxt):  # noqa: F811
    ex = MakerExchange(reject_limit=ccxt.InvalidOrder)
    gw = _gw(ex)
    gw.buy_maker_first("BTC/USDT", 1.0)
    refused, market = gw.order_log
    assert (refused["type"], refused["status"]) == ("post-only", "rejected")
    assert (market["type"], market["status"], market["liquidity"]) == ("market", "filled", "taker")


def test_gateway_tracks_its_last_good_and_failed_exchange_call(ccxt):  # noqa: F811
    ex = MakerExchange()
    gw = _gw(ex)
    assert gw.last_ok_ms == ex.now and gw.last_error_ms is None  # load_markets worked

    def down(pair):
        raise ccxt.NetworkError("[Errno -3] Temporary failure in name resolution")

    ex.fetch_ticker = down
    ex.now += 60_000
    with pytest.raises(ccxt.NetworkError):
        gw.bid("BTC/USDT")
    assert gw.last_error_ms == ex.now and gw.last_ok_ms < gw.last_error_ms


class Market:
    def __init__(self, px):
        self.px = px

    def now_ms(self):
        return 1_000

    def bid(self, pair):
        return self.px

    ask = bid

    def round_qty(self, pair, qty):
        return math.floor(qty * 1e6) / 1e6

    def min_order(self, pair):
        return 0.0, 0.0


def test_paper_fills_are_logged_as_taker():
    pb = PaperBroker(Market(100.0), fee_rate=0.001, slippage_pct=0.0, balances={"USDT": 1000})
    pb.buy("BTC/USDT", 2.0)
    with pytest.raises(OrderError):
        pb.buy("BTC/USDT", 100.0)
    ok, refused = pb.order_log
    assert ok["status"] == "filled" and ok["liquidity"] == "taker" and ok["type"] == "market"
    assert ok["notional"] == pytest.approx(200.0) and ok["fee_quote"] == pytest.approx(0.2)
    assert ok["order_id"] == "paper-1" and ok["ts"] == 1_000
    assert refused["status"] == "rejected" and refused["qty_requested"] == 100.0


def test_wallet_swaps_are_logged(tmp_path):
    from . import test_wallets as tw

    ws, chain, broker = tw._wallet(tmp_path, approve=0.3)
    chain.fund("USDC", 500)
    chain.fund("SOL", 1)
    th, _ = tw._user(tmp_path, ws, chain)
    fill = broker.buy("SOL/USDT", 0.5)
    th.join(5)
    th, _ = tw._user(tmp_path, ws, chain, act="reject")
    with pytest.raises(tw.w.WalletError):
        broker.buy("SOL/USDT", 0.1)
    th, _ = tw._user(tmp_path, ws, chain, act="wait")
    with pytest.raises(tw.w.WalletError, match="not approved within"):
        broker.buy("SOL/USDT", 0.1)
    th.join(5)
    swap, rejected, expired = broker.order_log
    assert swap["type"] == "swap" and swap["status"] == "filled" and swap["liquidity"] == "taker"
    assert swap["qty_filled"] == pytest.approx(fill.qty) and swap["fee_quote"] is None
    assert swap["notional"] == pytest.approx(75.0) and swap["order_id"] == fill.order_id
    assert rejected["status"] == "rejected" and expired["status"] == "cancelled"
    assert drain_order_log(broker, tmp_path) == 3


# ---------------------------------------------------------------------- the bot
def test_the_bot_logs_orders_and_alerts_trades(tmp_path, world):  # noqa: F811
    s = _settings(tmp_path, mode="paper", pairs=("BTC/USDT",))
    pb = PaperBroker(Market(1.0), fee_rate=0.001, slippage_pct=0.0)
    bot = TrendBot(s, pb, sleep=lambda x: None)
    bot.start()
    dec, open_px = _allowed_decision(bot, world)
    pb.market.px = open_px
    trade = bot.enter("BTC/USDT", dec)
    assert trade is not None and pb.order_log == []  # drained by the save after booking
    (buy,) = sc.read_jsonl(bot.dir / "orders.jsonl")
    assert buy["side"] == "buy" and buy["qty_filled"] == pytest.approx(trade.qty)
    pb.market.px = trade.target * 1.01
    closed = bot.exit_position(trade, EXIT_TP, pb.market.px)
    rows = sc.read_jsonl(bot.dir / "orders.jsonl")
    assert [r["side"] for r in rows] == ["buy", "sell"]
    events = [a["event"] for a in sc.read_jsonl(bot.dir / "alerts.jsonl")]
    assert events == ["trade_opened", "trade_closed"]
    opened, done = sc.read_jsonl(bot.dir / "alerts.jsonl")
    assert opened["trade_id"] == done["trade_id"] == trade.trade_id
    assert f"#{trade.trade_id}" in done["message"]
    assert f"{closed.r_multiple:+.2f}R" in done["message"]
    card = sc.scorecard(bot.dir)
    assert card["orders"]["placed"] == 2 and card["orders"]["filled"] == 2


class ClockGateway(StubGateway):
    """A gateway whose clock moves with the bot's sleeps."""

    def __init__(self):
        super().__init__(100.0, 100.0)
        self.t = 10**12

    def now_ms(self):
        return self.t


def _clock_bot(tmp_path, **alerts):
    gw = ClockGateway()
    s = _settings(tmp_path, mode="paper", pairs=("BTC/USDT",), poll_seconds=1.0, alerts=alerts)
    bot = TrendBot(s, gw, sleep=lambda x: setattr(gw, "t", gw.t + int(x * 1000)))
    bot.start()
    return bot, gw


def test_run_raises_start_stop_network_and_error_alerts_and_tracks_uptime(tmp_path):
    bot, gw = _clock_bot(tmp_path, network_down_minutes=1)
    outcomes = [None] + [ConnectionError("no route to host")] * 8 + [None, None]
    steps = []

    def step():
        steps.append(1)
        exc = outcomes[len(steps) - 1]
        if exc is not None:
            raise exc
        bot._heartbeat("running")

    bot.step = step
    bot.run(max_steps=11)
    alerts = sc.read_jsonl(bot.dir / "alerts.jsonl")
    events = [a["event"] for a in alerts]
    assert events == [
        "bot_started",
        "bot_error",  # 5 failed steps in a row
        "network_down",  # 1 + 2 + 4 + 8 + 16 + 32 s > 1 min without a good step
        "network_restored",
        "bot_stopped",
    ]
    assert all(a["bot"] == "state" and a["mode"] == "paper" for a in alerts)
    assert "5 steps in a row failed" in alerts[1]["message"]
    sessions = sc.read_uptime(bot.dir)["sessions"]
    assert len(sessions) == 2  # minutes of silence: a gap > 3 x 1 s + 60 s
    assert sessions[-1][1] == gw.t


class Hook(BaseHTTPRequestHandler):
    got: list = []

    def do_POST(self):
        size = int(self.headers.get("Content-Length") or 0)
        Hook.got.append((self.path, json.loads(self.rfile.read(size))))
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        return


@pytest.fixture
def hook():
    Hook.got = []
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Hook)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def _local(hook_url):
    """The injectable poster: tests deliver to a local fake webhook over plain http."""

    def post(url, payload, timeout):
        assert url == ZAPIER  # the configured (https) webhook
        req = urllib.request.Request(
            hook_url + "/hook",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()

    return post


def test_alerts_are_posted_to_the_webhook_and_always_written(tmp_path, hook):
    a = sc.Alerter(
        {"webhook_url": ZAPIER, "events": ["trade_closed", "bot_error"]},
        tmp_path / "btc-bot",
        mode="live",
        exchange="binance",
        poster=_local(hook),
    )
    a.emit("trade_opened", "bought BTC")  # not a chosen event: alerts.jsonl only
    rec = a.emit("trade_closed", "sold BTC: +1.50R", pair="BTC/USDT")
    assert a.flush(5)
    ((path, body),) = Hook.got
    assert path == "/hook" and body == rec
    assert set(body) == {"event", "bot", "mode", "exchange", "message", "ts"}
    assert (body["event"], body["bot"], body["mode"], body["exchange"]) == (
        "trade_closed",
        "btc-bot",
        "live",
        "binance",
    )
    rows = sc.read_jsonl(tmp_path / "btc-bot" / "alerts.jsonl")
    assert [r["event"] for r in rows] == ["trade_opened", "trade_closed"]
    assert rows[1]["pair"] == "BTC/USDT"  # extra details stay in the file
    assert [r["event"] for r in sc.recent_alerts(tmp_path / "btc-bot")] == [
        "trade_closed",
        "trade_opened",
    ]


def test_webhook_settings_are_validated():
    with pytest.raises(ValueError, match="https"):
        BotSettings(alerts={"webhook_url": "http://hooks.zapier.com/hooks/catch/1/2"})
    with pytest.raises(ValueError, match="unknown"):
        BotSettings(alerts={"events": ["trade_opened", "moon"]})
    with pytest.raises(TypeError):
        BotSettings(alerts={"webhook": ZAPIER})
    with pytest.raises(ValueError, match="https"):
        sc.post_json("http://127.0.0.1:1/x", {})  # the real poster never sends plain http
    BotSettings(alerts={"webhook_url": "https://example.app.n8n.cloud/webhook/abc"})


def test_a_failing_or_hanging_webhook_never_breaks_the_bot(tmp_path):
    bot, _ = _clock_bot(tmp_path, webhook_url=ZAPIER)
    calls = []

    def broken(url, payload, timeout):
        calls.append(payload["event"])
        raise OSError("connection refused")

    bot.alerts.poster = broken
    bot.run(max_steps=3)  # the real step
    assert calls == ["bot_started", "bot_stopped"]
    assert json.loads((bot.dir / "heartbeat.json").read_text())["status"] == "stopped"
    assert [a["event"] for a in sc.read_jsonl(bot.dir / "alerts.jsonl")] == [
        "bot_started",
        "bot_stopped",
    ]

    gate = threading.Event()
    hanging = sc.Alerter({"webhook_url": ZAPIER}, tmp_path / "h", poster=lambda *a: gate.wait())
    t0 = time.monotonic()
    for i in range(sc.ALERT_QUEUE + 10):  # more than the queue holds: dropped, not blocked
        hanging.emit("trade_opened", f"#{i}")
    assert time.monotonic() - t0 < 2.0
    assert hanging.flush(0.05) is False
    assert len(sc.read_jsonl(tmp_path / "h" / "alerts.jsonl")) == sc.ALERT_QUEUE + 10
    gate.set()


# ---------------------------------------------------------------------- the dashboard
def test_the_dashboard_alerts_a_stale_heartbeat_once(tmp_path):
    from research.trendbot.dashboard import Controller

    d = tmp_path / "bot"
    d.mkdir()
    wall = int(time.time() * 1000) - 5 * 60_000
    beat = {"pid": 999_999_999, "ts": wall, "wall_ts": wall, "status": "running"}
    (d / "heartbeat.json").write_text(json.dumps(beat))
    ctrl = Controller(d, None)
    assert ctrl.bot_status()["running"] is False
    ctrl.bot_status()
    Controller(d, None).bot_status()  # a dashboard restart does not repeat it
    (alert,) = sc.read_jsonl(d / "alerts.jsonl")
    assert alert["event"] == "heartbeat_stale" and alert["heartbeat_wall_ts"] == wall
    assert "no heartbeat for 5 min" in alert["message"]
    (d / "heartbeat.json").write_text(json.dumps({**beat, "status": "stopped"}))
    ctrl.bot_status()  # a clean stop is not stale
    assert len(sc.read_jsonl(d / "alerts.jsonl")) == 1


def test_the_snapshot_and_the_fleet_summary_carry_health(tmp_path):
    from research.trendbot.config import StrategyConfig
    from research.trendbot.dashboard import FleetRuntime, build_snapshot, live_snapshot

    d = tmp_path / "bot"
    d.mkdir()
    (d / "state.json").write_text(json.dumps({"starting_equity": 1000.0, "mode": "paper"}))
    now = int(time.time() * 1000)
    (d / "uptime.json").write_text(json.dumps({"sessions": [[now - 20 * 86_400_000, now]]}))
    sc.append_jsonl(d / "orders.jsonl", [{"ts": now, "status": "filled", "notional": 50.0}])
    sc.Alerter(None, d).emit("bot_started", "hello")
    snap = build_snapshot(d)
    h = snap["health"]
    assert {m["key"] for m in h["metrics"]} >= {"pnl_annual", "uptime", "maker", "fees"}
    assert h["uptime"]["sessions"] == 1 and h["alerts"][0]["event"] == "bot_started"
    assert h["category"] == "low-volatility"  # flat: no trades in 20 days
    assert live_snapshot(d)["health"]["days_live"] == pytest.approx(20.0, abs=0.01)
    cfg = StrategyConfig()
    rt = FleetRuntime(None, {"a": (d, cfg, None), "b": (d, cfg, None)})
    summary = rt.summary()
    assert [r["category"] for r in summary["bots"]] == ["low-volatility"] * 2
    assert "spread bots across categories" in summary["diversification"]["hint"]
