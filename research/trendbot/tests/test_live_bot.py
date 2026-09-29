"""End-to-end tests of the live bot against a fake ccxt exchange replaying a synthetic world."""

import json
import math

import pytest

from research.trendbot.config import StrategyConfig
from research.trendbot.gatekeeper import EntryDecision
from research.trendbot.invariants import live_journal_violations
from research.trendbot.journal import read_journal
from research.trendbot.live_bot import BotSettings, TrendBot, main
from research.trendbot.live_exchange import (
    CcxtGateway,
    Fill,
    OrderError,
    PaperBroker,
    fill_from_order,
    with_retries,
)
from research.trendbot.models import EXIT_END, EXIT_SL, EXIT_TP, HOUR_MS
from research.trendbot.synthetic import make_world


TF = 4 * HOUR_MS
PAIRS = ("BTC/USDT", "ETH/USDT", "BNB/USDT")
WARMUP = 1250


class FakeExchange:
    """ccxt-shaped exchange over fixed candles; intra-candle price path O -> L -> H -> C."""

    id = "binance"
    timeframes = {"1h": 1, "4h": 1, "1d": 1}

    def __init__(self, data, start_ts, fee_rate=0.001, balances=None):
        self.data = data
        self.now = start_ts
        self.fee_rate = fee_rate
        self.balances = dict(balances or {"USDT": 10_000.0})
        self.orders = []
        self._index = {p: {c.ts: c for c in cs} for p, cs in data.items()}

    # clock ------------------------------------------------------------------
    def milliseconds(self):
        return self.now

    def advance(self, seconds):
        self.now += int(seconds * 1000)

    # markets ----------------------------------------------------------------
    def load_markets(self):
        return {p: {} for p in self.data}

    def market(self, pair):
        return {"limits": {"amount": {"min": 1e-6}, "cost": {"min": 5.0}}}

    def amount_to_precision(self, pair, qty):
        return f"{math.floor(qty * 1e6) / 1e6:.6f}"

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=1000):
        rows = [
            [c.ts, c.open, c.high, c.low, c.close, c.volume]
            for c in self.data[symbol]
            if c.ts >= (since or 0) and c.ts <= self.now
        ]
        return rows[:limit]

    def price(self, pair):
        start = self.now // TF * TF
        c = self._index[pair][start]
        quarter = (self.now - start) * 4 // TF
        return (c.open, c.low, c.high, c.close)[quarter]

    def fetch_ticker(self, pair):
        p = self.price(pair)
        return {"bid": p, "ask": p, "last": p}

    # account ----------------------------------------------------------------
    def fetch_balance(self):
        return {k: {"free": v, "total": v} for k, v in self.balances.items()}

    def create_order(self, pair, kind, side, qty):
        assert kind == "market"
        base, quote = pair.split("/")
        px = self.price(pair)
        notional = qty * px
        fee = notional * self.fee_rate
        if side == "buy":
            assert notional + fee <= self.balances.get(quote, 0) + 1e-6, "insufficient funds"
            self.balances[quote] -= notional + fee
            self.balances[base] = self.balances.get(base, 0.0) + qty
        else:
            assert qty <= self.balances.get(base, 0) + 1e-9, "selling more than held"
            self.balances[base] -= qty
            self.balances[quote] += notional - fee
        order = {
            "id": str(len(self.orders) + 1),
            "status": "closed",
            "filled": qty,
            "average": px,
            "timestamp": self.now,
            "fee": {"cost": fee, "currency": quote},
            "side": side,
            "symbol": pair,
        }
        self.orders.append(order)
        return order


@pytest.fixture(scope="module")
def world():
    data, events = make_world("planted", seed=3, years=2.0)
    return data, events


def _settings(tmp_path, **kw):
    base = {
        "exchange": "binance",
        "mode": "testnet",
        "pairs": PAIRS,
        "state_dir": str(tmp_path / "state"),
        "poll_seconds": 3600.0,
        "close_delay_seconds": 1.0,
        "history_candles": 1200,
    }
    base.update(kw)
    return BotSettings(**base)


def _bot(settings, ex):
    gw = CcxtGateway(ex, sleep=lambda s: None)
    return TrendBot(settings, gw, sleep=ex.advance)


def _start_ts(data):
    return data["BTC/USDT"][WARMUP].ts + 2000


def _run(bot, ex, until_ts):
    bot.start()
    while ex.now < until_ts:
        bot.step()
        ex.advance(bot.s.poll_seconds)
    bot._save()


def test_bot_trades_a_synthetic_world_and_the_journal_audits_clean(tmp_path, world):
    data, _ = world
    ex = FakeExchange(data, _start_ts(data))
    bot = _bot(_settings(tmp_path), ex)
    end = data["BTC/USDT"][-2].ts
    _run(bot, ex, end)
    trades = read_journal(bot.journal_path)
    closed = [t for t in trades if t.is_closed]
    assert len(closed) >= 10
    assert {t.exit_reason for t in closed} >= {EXIT_SL, EXIT_TP}
    for t in closed:
        if t.exit_reason == EXIT_SL:
            assert t.exit_price <= t.stop
        if t.exit_reason == EXIT_TP:
            assert t.exit_price >= t.target
    cfg = bot.cfg
    assert live_journal_violations(trades, data, cfg, events=None, starting_equity=10_000.0) == []
    # every buy/sell reached the exchange; balances match the journal when flat
    bot.flatten()
    pnl = sum(t.pnl for t in read_journal(bot.journal_path))
    assert ex.balances["USDT"] == pytest.approx(10_000.0 + pnl, abs=1e-6)
    for base in ("BTC", "ETH", "BNB"):
        assert ex.balances.get(base, 0.0) < 1e-5


def test_restart_mid_run_reproduces_the_uninterrupted_journal(tmp_path, world):
    data, _ = world
    end = data["BTC/USDT"][WARMUP + 1500].ts
    mid = data["BTC/USDT"][WARMUP + 700].ts + HOUR_MS * 2 + 2000

    ex1 = FakeExchange(data, _start_ts(data))
    _run(_bot(_settings(tmp_path / "a"), ex1), ex1, end)

    ex2 = FakeExchange(data, _start_ts(data))
    first = _bot(_settings(tmp_path / "b"), ex2)
    _run(first, ex2, mid)
    second = _bot(_settings(tmp_path / "b"), ex2)  # new process, same state dir
    _run(second, ex2, end)

    a = read_journal(tmp_path / "a" / "state" / "journal.csv")
    b = read_journal(tmp_path / "b" / "state" / "journal.csv")
    assert len(a) >= 5
    assert a == b
    assert ex1.balances == pytest.approx(ex2.balances)


def test_decisions_log_and_candles_are_persisted(tmp_path, world):
    data, _ = world
    ex = FakeExchange(data, _start_ts(data))
    bot = _bot(_settings(tmp_path), ex)
    _run(bot, ex, data["BTC/USDT"][WARMUP + 60].ts)
    lines = bot.decisions_path.read_text().splitlines()
    assert len(lines) - 1 == 3 * 60  # one row per pair per closed candle
    for pair in PAIRS:
        assert (bot.dir / "candles" / f"{pair.replace('/', '_')}-4h.csv").exists()
    state = json.loads(bot.state_path.read_text())
    assert state["mode"] == "testnet" and state["starting_equity"] == 10_000.0


def test_state_dir_of_another_mode_is_refused(tmp_path, world):
    data, _ = world
    ex = FakeExchange(data, _start_ts(data))
    _bot(_settings(tmp_path), ex).start()
    with pytest.raises(SystemExit):
        _bot(_settings(tmp_path, mode="live"), ex).start()


def test_paper_broker_simulates_fills_and_persists_balances(tmp_path, world):
    data, _ = world
    ex = FakeExchange(data, _start_ts(data), balances={})
    public = CcxtGateway(ex, sleep=lambda s: None)
    cfg = StrategyConfig()
    paper = PaperBroker(public, fee_rate=cfg.fee_rate, slippage_pct=cfg.slippage_pct)
    s = _settings(tmp_path, mode="paper", starting_equity=5_000.0)
    bot = TrendBot(s, paper, cfg=cfg, sleep=ex.advance)
    _run(bot, ex, data["BTC/USDT"][WARMUP + 900].ts)
    assert ex.orders == []  # paper never touches the exchange
    trades = read_journal(bot.journal_path)
    assert trades
    state = json.loads(bot.state_path.read_text())
    assert state["paper_balances"]["USDT"] > 0
    bot.flatten()
    pnl = sum(t.pnl for t in read_journal(bot.journal_path))
    assert paper.free("USDT") == pytest.approx(5_000.0 + pnl, abs=1e-6)


class StubGateway:
    def __init__(self, ask, fill_price, fill_qty=None):
        self._ask, self.fill_price, self.fill_qty = ask, fill_price, fill_qty
        self.sells = []

    def now_ms(self):
        return 0

    def ask(self, pair):
        return self._ask

    def bid(self, pair):
        return self._ask

    def free(self, asset):
        return 1e9

    total = free

    def round_qty(self, pair, qty):
        return math.floor(qty * 1e6) / 1e6

    def min_order(self, pair):
        return 0.0, 0.0

    def buy(self, pair, qty):
        return Fill(self.fill_qty or qty, self.fill_price, None, 0, "b")

    def sell(self, pair, qty):
        self.sells.append(qty)
        return Fill(qty, self.fill_price, None, 0, "s")


def _allowed_decision(bot, world):
    data, _ = world
    sess = bot.session
    btc = data["BTC/USDT"]
    for i in range(WARMUP, len(btc) - 1):
        dec = sess.on_candle_close("BTC/USDT", btc[: i + 1])
        if dec.allowed:
            return dec, btc[i + 1].open
    raise AssertionError("no allowed signal")


def test_worse_than_expected_fill_sells_the_excess(tmp_path, world):
    s = _settings(tmp_path, mode="paper", pairs=("BTC/USDT",))
    bot = TrendBot(s, StubGateway(1, 1), sleep=lambda x: None)
    bot.start()
    dec, open_px = _allowed_decision(bot, world)
    assert isinstance(dec, EntryDecision)
    bot.gw._ask = open_px
    stop = dec.stop_plan.stop
    bot.gw.fill_price = open_px + (open_px - stop) * 0.3  # much worse than the 0.3% buffer
    trade = bot.enter("BTC/USDT", dec)
    assert trade is not None
    assert bot.gw.sells, "excess quantity must be sold back"
    assert trade.risk_pct <= 1.0 + 1e-9


def test_fill_through_the_stop_is_flattened(tmp_path, world):
    s = _settings(tmp_path, mode="paper", pairs=("BTC/USDT",))
    bot = TrendBot(s, StubGateway(1, 1), sleep=lambda x: None)
    bot.start()
    dec, open_px = _allowed_decision(bot, world)
    bot.gw._ask = open_px
    bot.gw.fill_price = dec.stop_plan.stop * 0.99
    assert bot.enter("BTC/USDT", dec) is None
    assert bot.gw.sells and bot.state["unmanaged"]
    assert bot.session.open_positions == {}


def test_fill_parsing_handles_quote_base_and_third_asset_fees():
    base = {"id": "1", "filled": 2.0, "average": 100.0, "timestamp": 5}
    f = fill_from_order({**base, "fee": {"cost": 0.2, "currency": "USDT"}}, "BTC/USDT", "buy", 0)
    assert (f.qty, f.price, f.fee_quote) == (2.0, 100.0, 0.2)
    f = fill_from_order({**base, "fee": {"cost": 0.002, "currency": "BTC"}}, "BTC/USDT", "buy", 0)
    assert f.qty == pytest.approx(1.998) and f.fee_quote == pytest.approx(0.2)
    f = fill_from_order({**base, "fee": {"cost": 0.01, "currency": "BNB"}}, "BTC/USDT", "buy", 0)
    assert f.qty == 2.0 and f.fee_quote is None
    f = fill_from_order({"id": "2", "filled": 0}, "BTC/USDT", "buy", 7)
    assert f.qty == 0.0
    f = fill_from_order({"id": "3", "filled": 4.0, "cost": 400.0}, "BTC/USDT", "sell", 1)
    assert f.price == 100.0


def test_retries_back_off_then_raise():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionError("down")
        return "ok"

    slept = []
    assert with_retries(flaky, "x", (ConnectionError,), sleep=slept.append) == "ok"
    assert slept == [1.0, 2.0]
    with pytest.raises(ConnectionError):
        with_retries(
            lambda: (_ for _ in ()).throw(ConnectionError()),
            "x",
            (ConnectionError,),
            attempts=2,
            sleep=lambda s: None,
        )


def test_order_network_error_is_never_resent(world):
    data, _ = world
    ex = FakeExchange(data, _start_ts(data))

    def boom(*a, **k):
        raise ConnectionError("timeout")

    ex.create_order = boom
    gw = CcxtGateway(ex, sleep=lambda s: None)
    with pytest.raises(OrderError, match="outcome unknown"):
        gw.buy("BTC/USDT", 0.01)


def test_settings_validation_and_cli(tmp_path, world, capsys):
    with pytest.raises(ValueError):
        BotSettings(mode="yolo")
    with pytest.raises(ValueError):
        BotSettings(pairs=("BTC/USDT", "ETH/BTC"))
    path = tmp_path / "bot.json"
    path.write_text(
        json.dumps(
            {
                "pairs": list(PAIRS),
                "state_dir": str(tmp_path / "st"),
                "mode": "testnet",
                "history_candles": 1200,
            }
        )
    )
    data, _ = world
    ex = FakeExchange(data, _start_ts(data))
    gw = CcxtGateway(ex, sleep=lambda s: None)
    assert main(["status", "--settings", str(path)], gateway=gw) == 0
    assert "realized equity 10000.00 USDT" in capsys.readouterr().out
    path.write_text(json.dumps({"bogus": 1}))
    with pytest.raises(ValueError, match="unknown settings keys"):
        BotSettings.load(path)


def test_flatten_closes_everything_as_end(tmp_path, world):
    data, _ = world
    ex = FakeExchange(data, _start_ts(data))
    bot = _bot(_settings(tmp_path), ex)
    bot.start()
    while not bot.session.open_positions:
        bot.step()
        ex.advance(3600)
    closed = bot.flatten()
    assert closed and all(t.exit_reason == EXIT_END for t in closed)
    assert bot.session.open_positions == {}


def test_operator_pause_close_stop_and_learning_files(tmp_path, world):
    from research.trendbot.adoption_evidence import read_decisions_log
    from research.trendbot.live_bot import post_request, write_control

    data, _ = world
    ex = FakeExchange(data, _start_ts(data))
    bot = _bot(_settings(tmp_path), ex)
    bot.start()
    write_control(bot.dir, entries_paused=True)
    for _ in range(6 * 90):  # 90 days of hourly polls with entries paused
        bot.step()
        ex.advance(3600)
    decisions = read_decisions_log(bot.decisions_path)
    assert any(d.rule == "X_operator" for d in decisions)
    assert bot.session.open_positions == {}
    write_control(bot.dir, entries_paused=False)
    while not bot.session.open_positions:
        bot.step()
        ex.advance(3600)
    (pair,) = bot.session.open_positions
    post_request(bot.dir, action="close", pair=pair)
    bot.step()
    assert bot.session.open_positions == {}
    ledger = json.loads((bot.dir / "ledger.json").read_text())
    closed = [r for r in ledger if r["status"] == "closed"]
    assert closed and closed[-1]["exit_reason"] == EXIT_END
    assert closed[-1]["why_triggered"] and closed[-1]["expected_outcome"]["at_target_r"] == 2.0
    assert closed[-1]["lesson"].startswith(f"Trade #{closed[-1]['trade_id']}")
    assert (bot.dir / "learnings.md").exists() and (bot.dir / "learnings.json").exists()
    post_request(bot.dir, action="stop")
    bot.run(max_steps=5)
    assert bot.stop_requested
    beat = json.loads((bot.dir / "heartbeat.json").read_text())
    assert beat["status"] == "stopped" and beat["mode"] == "testnet"


def test_bot_writes_live_prices_every_step_and_exits_on_them(tmp_path, world):
    data, _ = world
    ex = FakeExchange(data, _start_ts(data))
    bot = _bot(_settings(tmp_path), ex)
    bot.start()
    bot.step()
    live = json.loads((bot.dir / "live.json").read_text())
    assert set(live["prices"]) == set(PAIRS)
    assert live["prices"]["BTC/USDT"]["bid"] == pytest.approx(bot.gw.bid("BTC/USDT"))
    assert bot._bid("BTC/USDT") == live["prices"]["BTC/USDT"]["bid"]
    ex.advance(3600)  # an hour later the cached price is stale: a fresh request is made
    assert bot._bid("BTC/USDT") == pytest.approx(bot.gw.bid("BTC/USDT"))
