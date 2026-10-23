"""The live bot survives outages, lost order replies, partial fills, dust and Windows quirks.

Offline: a fake ``ccxt`` module holding the real v4 exception hierarchy is installed in
``sys.modules`` where a test needs ccxt's error classes.
"""

import json
import logging
import subprocess
import sys
import types
from pathlib import Path

import pytest

from research.trendbot.data import save_candles_csv
from research.trendbot.journal import atomic_write_text, read_journal, write_journal
from research.trendbot.live_bot import (
    MAX_BACKOFF_S,
    BotSettings,
    LoopGuard,
    TrendBot,
    _fill_gaps,
    main,
)
from research.trendbot.live_exchange import (
    CcxtGateway,
    Fill,
    OrderError,
    PaperBroker,
    exchange_config,
    fatal_reason,
    fill_from_order,
    load_env_file,
    with_retries,
)
from research.trendbot.models import EXIT_SL, Candle

from . import test_live_bot as tlb
from .test_live_bot import TF, WARMUP, FakeExchange, StubGateway, _settings, _start_ts


world = tlb.world  # the module-scoped synthetic world fixture


REPO = Path(__file__).resolve().parents[3]


def _hierarchy() -> types.ModuleType:
    """ccxt v4's exception classes (what the bot classifies), nothing else."""
    m = types.ModuleType("ccxt")

    def cls(name, base):
        c = type(name, (base,), {})
        setattr(m, name, c)
        return c

    base = cls("BaseError", Exception)
    ex = cls("ExchangeError", base)
    auth = cls("AuthenticationError", ex)
    cls("PermissionDenied", auth)
    cls("AccountSuspended", auth)
    cls("BadSymbol", cls("BadRequest", ex))
    cls("InvalidOrder", ex)
    cls("NotSupported", ex)
    failed = cls("OperationFailed", base)
    net = cls("NetworkError", failed)
    for name in ("RequestTimeout", "ExchangeNotAvailable", "DDoSProtection", "InvalidNonce"):
        cls(name, net)
    cls("RateLimitExceeded", net)
    cls("BadResponse", failed)
    return m


@pytest.fixture
def ccxt(monkeypatch):
    m = _hierarchy()
    monkeypatch.setitem(sys.modules, "ccxt", m)
    return m


def _paper_bot(tmp_path, gw, **kw):
    s = _settings(tmp_path, mode="paper", pairs=("BTC/USDT",), **kw)
    bot = TrendBot(s, gw, sleep=lambda x: None)
    bot.start()
    return bot


def _allowed(bot, data):
    """(decision, next candle's open, index) of the first allowed BTC signal."""
    btc = data["BTC/USDT"]
    for i in range(WARMUP, len(btc) - 1):
        dec = bot.session.on_candle_close("BTC/USDT", btc[: i + 1])
        if dec.allowed:
            return dec, btc[i + 1].open, i
    raise AssertionError("no allowed signal")


def _open_trade(bot, data):
    dec, open_px, _ = _allowed(bot, data)
    bot.gw._ask = bot.gw.fill_price = open_px
    trade = bot.enter("BTC/USDT", dec)
    assert trade is not None
    return trade


class HeldGateway(StubGateway):
    """StubGateway that tracks the BTC it holds and can fill sells only partly."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.held = 0.0
        self.fraction = None
        self.minimum = (0.0, 0.0)

    def free(self, asset):
        return self.held if asset == "BTC" else 1e9

    total = free

    def min_order(self, pair):
        return self.minimum

    def buy(self, pair, qty):
        self.held += qty
        return Fill(qty, self.fill_price, None, 0, "b")

    def sell(self, pair, qty):
        done = self.round_qty(pair, qty * self.fraction) if self.fraction else qty
        self.held -= done
        self.sells.append(done)
        return Fill(done, self.fill_price, 0.1, 0, f"s{len(self.sells)}")


# ---------------------------------------------------------------------- 1. the run loop
def test_loop_guard_backs_off_transient_errors_and_logs_a_bug_once(ccxt, caplog):
    guard = LoopGuard("binance", 1.0, on_fatal=lambda m: None)
    delays = [guard.failed(ccxt.RequestTimeout("timeout")) for _ in range(8)]
    assert delays == [2.0, 4.0, 8.0, 16.0, 32.0, MAX_BACKOFF_S, MAX_BACKOFF_S, MAX_BACKOFF_S]
    assert guard.ok() == 1.0 and guard.failures == 0
    assert guard.failed(PermissionError(13, "[WinError 5] Access is denied")) == 2.0
    assert guard.failed(ccxt.BadResponse("502")) == 4.0
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="trendbot.bot"):
        for _ in range(3):
            guard.failed(KeyError("surprise"))
    tracebacks = [r for r in caplog.records if r.exc_info]
    assert len(tracebacks) == 1  # once per distinct error, then one line each
    assert guard.failed(OrderError("rejected")) == 1.0  # order problems keep the poll pace


def test_fatal_errors_stop_with_one_plain_line(ccxt):
    msg451 = (
        "binance GET https://api.binance.com/api/v3/exchangeInfo 451 Service unavailable "
        "from a restricted location"
    )
    assert fatal_reason(ccxt.ExchangeNotAvailable(msg451), "binance") == (
        "Binance refuses this location (HTTP 451): use another exchange in bot.json"
    )
    assert "rejected the API keys" in fatal_reason(ccxt.AuthenticationError("bad"), "binance")
    assert "rejected the API keys" in fatal_reason(ccxt.PermissionDenied("ip"), "binance")
    assert "suspended" in fatal_reason(ccxt.AccountSuspended("x"), "binance")
    assert "pairs in bot.json" in fatal_reason(ccxt.BadSymbol("XYZ/USDT"), "binance")
    assert fatal_reason(ccxt.ExchangeNotAvailable("503 at price 451.2"), "binance") is None
    assert fatal_reason(ccxt.RequestTimeout("timeout"), "binance") is None
    seen = []

    def blocked():
        seen.append(1)
        raise ccxt.ExchangeNotAvailable(msg451)

    with pytest.raises(ccxt.ExchangeNotAvailable):
        with_retries(blocked, "x", (ccxt.NetworkError,), sleep=lambda s: None)
    assert len(seen) == 1  # waiting cannot fix it: no retries


def test_run_survives_outages_and_locked_files(tmp_path, ccxt):
    bot = _paper_bot(tmp_path, StubGateway(100.0, 100.0), poll_seconds=1.0)
    errors = [
        ccxt.NetworkError("[Errno -3] Temporary failure in name resolution"),
        ccxt.DDoSProtection("418 I'm a teapot"),
        PermissionError(13, "[WinError 5] Access is denied: heartbeat.json"),
        ValueError("a bug"),
        None,
        None,
    ]
    steps = []

    def step():
        steps.append(1)
        exc = errors[len(steps) - 1]
        if exc is not None:
            raise exc

    bot.step = step
    slept = []
    bot.sleep = slept.append
    bot.run(max_steps=6)
    assert len(steps) == 6
    assert slept == [2.0, 4.0, 8.0, 16.0, 1.0]  # backed off, then the normal pace again
    assert json.loads((bot.dir / "heartbeat.json").read_text())["status"] == "stopped"


def test_run_exits_on_a_fatal_error_with_an_error_heartbeat(tmp_path, ccxt):
    bot = _paper_bot(tmp_path, StubGateway(100.0, 100.0))

    def step():
        raise ccxt.ExchangeNotAvailable("binance GET /api/v3/ping 451 restricted location")

    bot.step = step
    with pytest.raises(SystemExit) as info:
        bot.run(max_steps=3)
    assert str(info.value).startswith("Binance refuses this location (HTTP 451)")
    beat = json.loads((bot.dir / "heartbeat.json").read_text())
    assert beat["status"] == "error" and "HTTP 451" in beat["error"]


def test_a_451_from_the_live_price_feed_is_fatal(tmp_path, ccxt):
    gw = StubGateway(100.0, 100.0)

    def tickers(pairs):
        raise ccxt.ExchangeNotAvailable("binance GET ticker/24hr 451 restricted location")

    gw.tickers = tickers
    gw.now_ms = lambda: 10**12
    bot = _paper_bot(tmp_path, gw)
    with pytest.raises(SystemExit, match="HTTP 451"):
        bot.run(max_steps=2)


def test_lab_bot_run_survives_errors(tmp_path, ccxt):
    from research.trendbot.lab_bot import LabBot

    s = BotSettings(
        engine="lab",
        mode="paper",
        pairs=("BTC/USDT",),
        state_dir=str(tmp_path / "lab"),
        history_candles=1200,
        lab={"strategy": "nnfx"},
    )
    bot = LabBot(s, StubGateway(100.0, 100.0), sleep=lambda x: None)
    calls = []

    def step():
        calls.append(1)
        if len(calls) < 3:
            raise ccxt.RequestTimeout("timeout")

    bot.step = step
    bot.run(max_steps=4)
    assert len(calls) == 4
    assert json.loads((bot.dir / "heartbeat.json").read_text())["status"] == "stopped"


def test_main_turns_a_fatal_start_error_into_one_line(tmp_path, ccxt):
    class Rejected(StubGateway):
        def total(self, asset):
            raise ccxt.AuthenticationError('binance {"code":-2015,"msg":"Invalid API-key"}')

    path = tmp_path / "bot.json"
    state = tmp_path / "st"
    path.write_text(
        json.dumps({"mode": "testnet", "state_dir": str(state), "history_candles": 1200})
    )
    with pytest.raises(SystemExit) as info:
        main(["run", "--settings", str(path)], gateway=Rejected(1, 1))
    assert "rejected the API keys" in str(info.value) and "\n" not in str(info.value)
    assert json.loads((state / "heartbeat.json").read_text())["status"] == "error"


# ---------------------------------------------------------------------- 2. pending decision
def test_an_error_inside_enter_does_not_block_later_entries(tmp_path, world, ccxt):
    data, _ = world
    gw = StubGateway(1, 1)
    bot = _paper_bot(tmp_path, gw)
    dec, _, i = _allowed(bot, data)

    def down(pair):
        raise ccxt.ExchangeNotAvailable("binance GET ticker 503")

    gw.ask = down
    with pytest.raises(ccxt.ExchangeNotAvailable):
        bot.enter("BTC/USDT", dec)
    row = bot.session.decision_log[-1]
    assert not row.allowed and "buy failed" in row.reason
    bot.session.on_candle_close("BTC/USDT", data["BTC/USDT"][: i + 2])  # no "pending" error


# ---------------------------------------------------------------------- 3. atomic writes
def test_atomic_write_retries_a_locked_target(tmp_path, monkeypatch):
    target = tmp_path / "heartbeat.json"
    real = Path.replace
    fails = {"n": 2}

    def locked(self, dst):
        if Path(dst).name == "heartbeat.json" and fails["n"] > 0:
            fails["n"] -= 1
            raise PermissionError(13, "[WinError 5] Access is denied")
        return real(self, dst)

    monkeypatch.setattr(Path, "replace", locked)
    atomic_write_text(target, '{"a": 1}\n', sleep=lambda s: None)
    assert target.read_bytes() == b'{"a": 1}\n'  # no \r\n translation
    fails["n"] = 99
    with pytest.raises(PermissionError):
        atomic_write_text(target, "{}", attempts=3, sleep=lambda s: None)
    assert target.read_text() == '{"a": 1}\n'  # the old file is intact
    assert sorted(p.name for p in tmp_path.iterdir()) == ["heartbeat.json"]  # no temp left


def test_journal_is_written_via_a_temp_file_and_a_locked_live_feed_is_skipped(
    tmp_path, monkeypatch
):
    from research.trendbot.models import Trade

    t = Trade(1, "BTC/USDT", "v", 0, 1, 100.0, 90.0, 120.0, 1.0, 10.0, 1.0, "atr")
    opened = []
    real_open = Path.open

    def spy(self, mode="r", *a, **k):
        if "w" in mode:
            opened.append(self.name)
        return real_open(self, mode, *a, **k)

    monkeypatch.setattr(Path, "open", spy)
    write_journal([t], tmp_path / "journal.csv")
    assert "journal.csv" not in opened and read_journal(tmp_path / "journal.csv") == [t]
    monkeypatch.setattr(Path, "open", real_open)

    bot = _paper_bot(tmp_path, StubGateway(100.0, 100.0))
    bot._live_ready = True

    real_replace = Path.replace

    def locked(self, dst):  # the dashboard reads live.json, Excel holds journal.csv
        if Path(dst).name in ("live.json", "journal.csv"):
            raise PermissionError(13, "[WinError 5] Access is denied")
        return real_replace(self, dst)

    monkeypatch.setattr(Path, "replace", locked)
    monkeypatch.setattr("research.trendbot.journal.time.sleep", lambda s: None)
    bot._write_live()  # skipped, not raised
    assert not (bot.dir / "live.json").exists()
    bot._save()  # a journal held open by Excel is written at the next save


# ---------------------------------------------------------------------- 4. lost order replies
class LostReply(FakeExchange):
    """The order executes on the exchange, but its HTTP reply is lost."""

    def __init__(self, *a, ccxt=None, lose="buy", execute=True, **kw):
        super().__init__(*a, **kw)
        self.errors, self.lose, self.execute = ccxt, lose, execute
        self.params = None

    def create_order(self, pair, kind, side, qty, price=None, params=None):
        self.params = params
        if side == self.lose and not self.execute:
            self.lose = None
            raise self.errors.RequestTimeout("binance POST /api/v3/order request timeout")
        order = super().create_order(pair, kind, side, qty)
        order["clientOrderId"] = (params or {}).get("newClientOrderId")
        if side == self.lose:
            self.lose = None
            raise self.errors.RequestTimeout("binance POST /api/v3/order request timeout")
        return order


def test_a_lost_buy_reply_is_recovered_from_the_balance(world, ccxt):
    data, _ = world
    ex = LostReply(data, _start_ts(data), ccxt=ccxt)
    gw = CcxtGateway(ex, sleep=lambda s: None)
    fill = gw.buy("BTC/USDT", 0.01)
    assert fill.qty == pytest.approx(0.01) and fill.order_id.startswith("unknown-")
    assert fill.price == pytest.approx(gw.ask("BTC/USDT"))
    assert set(ex.params) == {"newClientOrderId"} and len(ex.orders) == 1  # never re-sent


def test_a_lost_reply_is_found_by_the_client_order_id(world, ccxt):
    data, _ = world
    ex = LostReply(data, _start_ts(data), ccxt=ccxt, lose="sell")
    ex.has = {"fetchClosedOrders": True}
    ex.fetch_closed_orders = lambda pair: [dict(o) for o in ex.orders]
    gw = CcxtGateway(ex, sleep=lambda s: None)
    gw.buy("BTC/USDT", 0.01)
    fill = gw.sell("BTC/USDT", 0.01)
    assert fill.order_id == ex.orders[-1]["id"] and fill.fee_quote is not None


def test_a_lost_order_that_never_executed_raises(world, ccxt):
    data, _ = world
    ex = LostReply(data, _start_ts(data), ccxt=ccxt, execute=False)
    gw = CcxtGateway(ex, sleep=lambda s: None)
    with pytest.raises(OrderError, match=r"outcome unknown.*did not move"):
        gw.buy("BTC/USDT", 0.01)
    assert ex.orders == []


def test_reconcile_warns_about_coins_the_journal_does_not_know(tmp_path, caplog):
    gw = HeldGateway(100.0, 100.0)
    gw.held = 3.0
    with caplog.at_level(logging.WARNING, logger="trendbot.bot"):
        _paper_bot(tmp_path, gw)
    assert any("does not manage the difference" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------- 5. partial exits
def test_a_partly_filled_exit_sells_the_rest_then_books_one_exit(tmp_path, world):
    data, _ = world
    gw = HeldGateway(1, 1)
    bot = _paper_bot(tmp_path, gw)
    trade = _open_trade(bot, data)
    gw.fraction = 0.4
    assert bot.exit_position(trade, EXIT_SL, trade.stop) is None
    assert bot.session.open_positions  # still open: 60% is held
    part = json.loads(bot.state_path.read_text())["partial_exits"][str(trade.trade_id)]
    assert part["reason"] == EXIT_SL and part["fill"]["qty"] == pytest.approx(gw.sells[0])
    gw.fraction = None
    gw._ask = trade.target * 2  # the price no longer says "exit": the rest is sold anyway
    bot.manage_exits()
    assert bot.session.open_positions == {}
    (closed,) = [t for t in read_journal(bot.journal_path) if t.is_closed]
    assert closed.exit_reason == EXIT_SL
    assert sum(gw.sells) == pytest.approx(trade.qty) and gw.held == pytest.approx(0, abs=1e-9)
    assert bot.state["partial_exits"] == {}


# ---------------------------------------------------------------------- 6. dust
def test_dust_rounds_to_zero_and_closes_the_row_without_selling(tmp_path, world, ccxt):
    data, _ = world
    ex = FakeExchange(data, _start_ts(data))

    def precision(pair, qty):
        if qty < 1e-6:
            raise ccxt.InvalidOrder("amount must be greater than minimum amount precision")
        return f"{qty:.6f}"

    ex.amount_to_precision = precision
    assert CcxtGateway(ex, sleep=lambda s: None).round_qty("BTC/USDT", 4e-7) == 0.0

    gw = HeldGateway(1, 1)
    bot = _paper_bot(tmp_path, gw)
    trade = _open_trade(bot, data)
    gw.held = 4e-6  # sold elsewhere: only dust is left
    gw.minimum = (1e-5, 5.0)
    closed = bot.exit_position(trade, EXIT_SL, trade.stop)
    assert closed is not None and closed.exit_price == trade.stop and gw.sells == []


# ---------------------------------------------------------------------- 7. excess sell-back
def test_excess_below_the_minimum_is_kept_and_the_candle_is_saved_first(tmp_path, world):
    data, _ = world
    gw = HeldGateway(1, 1)
    bot = _paper_bot(tmp_path, gw)
    dec, open_px, _ = _allowed(bot, data)
    bot.state["last_signal_ts"]["BTC/USDT"] = dec.decision_ts - TF
    on_disk = []
    real_buy = gw.buy

    def buy(pair, qty):
        on_disk.append(json.loads(bot.state_path.read_text())["last_signal_ts"])
        gw.minimum = (qty * 0.5, 0.0)  # the excess (about 23%) is below the minimum
        return real_buy(pair, qty)

    gw.buy = buy
    gw._ask = open_px
    gw.fill_price = open_px + (open_px - dec.stop_plan.stop) * 0.3
    trade = bot.enter("BTC/USDT", dec)
    assert on_disk == [{"BTC/USDT": dec.decision_ts - TF}]  # persisted BEFORE the order
    assert trade is not None and gw.sells == []


def test_a_failing_excess_sell_still_books_the_position(tmp_path, world):
    data, _ = world
    gw = HeldGateway(1, 1)
    bot = _paper_bot(tmp_path, gw)
    dec, open_px, _ = _allowed(bot, data)

    def refuse(pair, qty):
        raise OrderError('binance {"code":-1013,"msg":"Filter failure: NOTIONAL"}')

    gw.sell = refuse
    gw._ask = open_px
    gw.fill_price = open_px + (open_px - dec.stop_plan.stop) * 0.3
    trade = bot.enter("BTC/USDT", dec)
    assert trade is not None and bot.session.open_positions


# ---------------------------------------------------------------------- 8. Coinbase market buy
def test_market_buys_send_the_price_when_the_exchange_needs_it(world):
    data, _ = world

    class Coinbase(FakeExchange):
        id = "coinbase"
        options = {"createMarketBuyOrderRequiresPrice": True}

        def create_order(self, pair, kind, side, qty, price=None, params=None):
            self.sent = (side, price, params)
            return super().create_order(pair, kind, side, qty)

    ex = Coinbase(data, _start_ts(data))
    gw = CcxtGateway(ex, sleep=lambda s: None)
    gw.buy("BTC/USDT", 0.01)
    _, price, params = ex.sent
    assert price == pytest.approx(gw.ask("BTC/USDT")) and set(params) == {"client_order_id"}
    gw.sell("BTC/USDT", 0.01)
    assert ex.sent[1] is None


# ---------------------------------------------------------------------- 9. fees
def test_missing_fee_info_is_unknown_and_a_buy_holds_what_the_balance_gained(world):
    f = fill_from_order({"id": "1", "filled": 2.0, "average": 100.0}, "BTC/USDT", "buy", 0)
    assert f.fee_quote is None and f.qty == 2.0
    data, _ = world

    class FeeInBase(FakeExchange):  # Binance without BNB: the fee is taken in the coin
        def create_order(self, pair, kind, side, qty, price=None, params=None):
            order = super().create_order(pair, kind, side, qty)
            if side == "buy":
                self.balances["USDT"] += order["fee"]["cost"]
                self.balances["BTC"] -= qty * self.fee_rate
            return {**order, "fee": None}  # fetch_order-like: no commission fields

    ex = FeeInBase(data, _start_ts(data))
    gw = CcxtGateway(ex, sleep=lambda s: None)
    fill = gw.buy("BTC/USDT", 0.01)
    assert fill.qty == pytest.approx(0.01 * 0.999)
    assert fill.fee_quote == pytest.approx(0.01 * 0.001 * fill.price)


# ---------------------------------------------------------------------- 10. clock
def test_decisions_use_the_exchange_clock(world):
    data, _ = world

    class Behind(FakeExchange):  # the local clock (milliseconds) runs 8 s ahead
        has = {"fetchTime": True}
        calls = 0
        down = False

        def fetch_time(self):
            self.calls += 1
            if self.down:
                raise ConnectionError("timeout")
            return self.now - 8000

    boundary = (_start_ts(data) // TF + 3) * TF
    ex = Behind(data, boundary + 3000)
    gw = CcxtGateway(ex, sleep=lambda s: None)
    assert gw.now_ms() == boundary - 5000
    assert gw.closed_candles("BTC/USDT", 5)[-1].ts == boundary - 2 * TF  # not the forming one
    ex.advance(600)
    gw.now_ms()
    assert ex.calls == 1  # re-measured every 15 minutes, not every call
    ex.down = True
    ex.advance(900)
    assert gw.now_ms() == ex.now - 8000 and ex.calls == 2  # a failure keeps the offset


def test_binance_syncs_its_request_clock(world, ccxt, monkeypatch):
    assert exchange_config("binance")["options"] == {"adjustForTimeDifference": True}
    assert "options" not in exchange_config("coinbase")
    data, _ = world
    seen = []

    class binance(FakeExchange):
        def __init__(self, config):
            seen.append(config)
            super().__init__(data, _start_ts(data))

    ccxt.binance = binance
    PaperBroker.connect("binance", fee_rate=0.001, slippage_pct=0.05)
    assert seen[0]["options"]["adjustForTimeDifference"] is True


# ---------------------------------------------------------------------- 11. candle gaps
def _candles(ts_list):
    return [Candle(t, 1.0 + i, 2.0 + i, 0.5 + i, 1.5 + i, 10.0) for i, t in enumerate(ts_list)]


def test_small_gaps_are_filled_with_flat_candles_large_ones_truncate(tmp_path, caplog):
    cs = _candles([i * TF for i in range(300) if i not in (100, 101, 250)])
    out, added, cut = _fill_gaps(cs, TF)
    assert (len(out), added, cut) == (300, 3, False)
    flat = out[100]
    assert (flat.open, flat.high, flat.low, flat.close, flat.volume) == (out[99].close,) * 4 + (
        0.0,
    )
    big = _candles([i * TF for i in range(300) if not 100 <= i < 110])
    out, added, cut = _fill_gaps(big, TF)
    assert cut and out[0].ts == 110 * TF and len(out) == 190

    gw = StubGateway(1.0, 1.0)
    gw.closed_candles = lambda pair, n: cs
    bot = _paper_bot(tmp_path, gw)
    with caplog.at_level(logging.WARNING, logger="trendbot.bot"):
        assert len(bot.refresh_candles("BTC/USDT")) == 300
        bot._cache = {}
        gw.closed_candles = lambda pair, n: big
        assert len(bot.refresh_candles("BTC/USDT")) == 190
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "filled with flat candles" in text and "is paused until the history" in text


# ---------------------------------------------------------------------- 12. Windows files
def test_settings_files_saved_by_notepad(tmp_path, monkeypatch):
    from research.trendbot.fleet import Fleet

    body = json.dumps({"state_dir": str(tmp_path / "st"), "history_candles": 1200})
    bom = tmp_path / "bot.json"
    bom.write_bytes(b"\xef\xbb\xbf" + body.encode())
    assert BotSettings.load(bom).history_candles == 1200
    utf16 = tmp_path / "utf16" / "bot.json"
    utf16.parent.mkdir()
    utf16.write_text(body, encoding="utf-16")
    with pytest.raises(ValueError, match=r"could not read bot\.json.*save it as UTF-8 in Notepad"):
        BotSettings.load(utf16)
    with pytest.raises(ValueError, match="does not exist"):
        BotSettings.load(tmp_path / "missing.json")
    (tmp_path / "bad.json").write_text("{'pairs': 1}")
    with pytest.raises(ValueError, match="not valid JSON"):
        BotSettings.load(tmp_path / "bad.json")
    with pytest.raises(SystemExit) as info:
        main(["status", "--settings", str(utf16)])
    assert str(info.value).startswith("could not read bot.json")

    fleet = tmp_path / "fleet.json"
    fleet.write_bytes(
        b"\xef\xbb\xbf" + json.dumps({"bots": [{"name": "a", "settings": "bot.json"}]}).encode()
    )
    assert list(Fleet.load(fleet).bots) == ["a"]
    fleet.write_text("{}", encoding="utf-16")
    with pytest.raises(ValueError, match=r"could not read fleet\.json"):
        Fleet.load(fleet)

    env = tmp_path / ".env"
    monkeypatch.delenv("TB_TEST_KEY", raising=False)
    env.write_bytes(b"\xef\xbb\xbfTB_TEST_KEY=abc\n")
    assert load_env_file(env) == ["TB_TEST_KEY"]
    monkeypatch.delenv("TB_TEST_KEY")
    env.write_text("TB_TEST_KEY=abc\n", encoding="utf-16")
    with pytest.raises(ValueError, match="save it as UTF-8 in Notepad"):
        load_env_file(env)


# ---------------------------------------------------------------------- 13. console encoding
def test_child_processes_and_the_console_log_are_utf8_safe(tmp_path):
    from research.trendbot.launch import command

    _, env = command("retrain", "collect")
    assert env["PYTHONUTF8"] == "1" and env["PYTHONIOENCODING"] == "utf-8"
    code = (
        "import logging, sys; from research.trendbot.live_bot import _setup_logging; "
        f"_setup_logging({str(tmp_path)!r}, False); "
        "logging.getLogger('trendbot.bot').info('BTC \\u2192 \\U0001f680 \\u20bf')"
    )
    res = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO,
        env={"PYTHONIOENCODING": "cp1252", "PATH": "", "PYTHONPATH": str(REPO)},
        capture_output=True,
        timeout=120,
    )
    assert res.returncode == 0 and b"Logging error" not in res.stderr, res.stderr
    assert b"BTC \\u2192" in res.stdout


# ---------------------------------------------------------------------- 14. Coinbase testnet
def test_coinbase_testnet_is_refused_in_plain_english(tmp_path, ccxt, monkeypatch):
    import research.trendbot.connections as connections

    class coinbase:
        def __init__(self, config):
            pass

        def set_sandbox_mode(self, on):
            raise ccxt.NotSupported("coinbase does not have a sandbox URL")

    ccxt.coinbase = coinbase
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(connections, "resolve_keys", lambda ex, mode: ("k", "s", None))
    with pytest.raises(SystemExit, match="Coinbase has no testnet in ccxt: use paper mode"):
        CcxtGateway.connect("coinbase", "testnet")


# ---------------------------------------------------------------------- 15. candle file names
def test_futures_pair_candle_files_have_no_colon(tmp_path):
    from research.trendbot.retrain import load_cached_candles

    bot = TrendBot(_settings(tmp_path, mode="paper", pairs=("BTC/USDT",)), StubGateway(1, 1))
    path = bot._candle_path("BTC/USDT:USDT")
    assert ":" not in path.name and path.name == "BTC_USDT_USDT-4h.csv"
    save_candles_csv(_candles([0, TF]), bot.dir / "candles" / path.name)
    assert set(load_cached_candles(bot.dir)) == {"BTC/USDT:USDT"}
