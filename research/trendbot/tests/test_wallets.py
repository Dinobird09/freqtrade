"""Wallet trading: tokens map per chain, every order is a ticket you approve in the wallet,
fills come from the chain, and nothing trades without you."""

import base64
import json
import threading
import time
import urllib.parse

import pytest

from research.trendbot import wallets as w
from research.trendbot.journal import read_journal
from research.trendbot.live_bot import BotSettings, TrendBot
from research.trendbot.live_exchange import CcxtGateway

from .test_live_bot import PAIRS, WARMUP, FakeExchange


SOL_ADDR = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
EVM_ADDR = "0x" + "ab" * 20
ROUTER = "0x" + "cd" * 20
FAST = lambda s: time.sleep(0.002)  # noqa: E731 - the bot's wait loop, sped up


class FakeChain:
    """A chain + swap aggregator in memory: balances, decimals, allowances, transactions."""

    def __init__(self, ws, address, prices):
        self.ws, self.addr, self.prices = ws, address, prices
        self.tokens = {t["address"]: t for t in (ws.token(s) for s in ws.spec["tokens"])}
        self.raw = {a: 0 for a in self.tokens}
        self.allow = {}
        self.txs = {}
        self.wrong_decimals = set()
        self.n = 0

    def fund(self, sym, amount):
        t = self.ws.token(sym)
        self.raw[t["address"]] = w.to_raw(amount, t["decimals"])

    def human(self, sym):
        t = self.ws.token(sym)
        return self.raw[t["address"]] / 10 ** t["decimals"]

    def _out(self, tin, tout, raw_in):
        a, b = self.tokens[tin], self.tokens[tout]
        usd = raw_in / 10 ** a["decimals"] * self.prices(a["symbol"])
        return int(usd / self.prices(b["symbol"]) * 10 ** b["decimals"])

    def execute(self, tin, tout, raw_in, fail=False):
        """What the wallet does after you confirm: the swap lands on chain."""
        self.n += 1
        sig = f"{'0x' if self.ws.spec['kind'] == 'evm' else 'sig'}{self.n:062d}"
        if fail:
            self.txs[sig] = "failed"
            return sig
        out = self._out(tin, tout, raw_in)
        assert self.raw[tin] >= raw_in, "the wallet would refuse: insufficient balance"
        self.raw[tin] -= raw_in
        self.raw[tout] += out
        native = next(a for a, t in self.tokens.items() if t["native"])
        self.raw[native] -= 5_000 if self.ws.spec["kind"] == "solana" else 10**13  # gas
        self.txs[sig] = "ok"
        return sig

    def __call__(self, url, payload, headers):
        if isinstance(payload, dict) and payload.get("jsonrpc"):
            return {
                "jsonrpc": "2.0",
                "id": 1,
                "result": self.rpc(payload["method"], payload["params"]),
            }
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        if "/swap/v1/quote" in url:
            out = self._out(q["inputMint"], q["outputMint"], int(q["amount"]))
            return {
                "inputMint": q["inputMint"],
                "outputMint": q["outputMint"],
                "inAmount": q["amount"],
                "outAmount": str(out),
                "otherAmountThreshold": str(out * 995 // 1000),
            }
        if url.endswith("/swap/v1/swap"):
            assert payload["userPublicKey"] == self.addr
            blob = json.dumps(payload["quoteResponse"]).encode()
            return {"swapTransaction": base64.b64encode(blob).decode()}
        if "/api/v1/routes" in url:
            assert headers and headers.get("x-client-id")
            out = self._out(q["tokenIn"], q["tokenOut"], int(q["amountIn"]))
            rs = {
                "tokenIn": q["tokenIn"],
                "tokenOut": q["tokenOut"],
                "amountIn": q["amountIn"],
                "amountOut": str(out),
            }
            return {"code": 0, "data": {"routeSummary": rs, "routerAddress": ROUTER}}
        if url.endswith("/route/build"):
            rs = payload["routeSummary"]
            assert payload["sender"] == payload["recipient"] == self.addr
            data = "0x" + json.dumps(rs).encode().hex()
            value = rs["amountIn"] if self.tokens[rs["tokenIn"]]["native"] else "0"
            return {
                "code": 0,
                "data": {
                    "amountIn": rs["amountIn"],
                    "amountOut": rs["amountOut"],
                    "data": data,
                    "routerAddress": ROUTER,
                    "transactionValue": value,
                },
            }
        raise AssertionError(url)

    def rpc(self, method, params):
        native = next(a for a, t in self.tokens.items() if t["native"])
        if method == "getBalance":
            return {"value": self.raw[native]}
        if method == "getTokenAccountsByOwner":
            raw = self.raw.get(params[1]["mint"], 0)
            info = {"tokenAmount": {"amount": str(raw)}}
            return {"value": [{"account": {"data": {"parsed": {"info": info}}}}]}
        if method == "getTokenSupply":
            t = self.tokens[params[0]]
            return {
                "value": {
                    "decimals": t["decimals"] + (1 if params[0] in self.wrong_decimals else 0)
                }
            }
        if method == "getSignatureStatuses":
            st = self.txs.get(params[0][0])
            return {
                "value": [
                    None
                    if st is None
                    else {
                        "err": {"x": 1} if st == "failed" else None,
                        "confirmationStatus": "confirmed",
                    }
                ]
            }
        if method == "eth_getBalance":
            return hex(self.raw[native])
        if method == "eth_call":
            to, data = params[0]["to"], params[0]["data"]
            sel = data[2:10]
            if sel == w.DECIMALS:
                return hex(self.tokens[to]["decimals"] + (1 if to in self.wrong_decimals else 0))
            if sel == w.BALANCE_OF:
                return hex(self.raw[to])
            if sel == w.ALLOWANCE:
                return hex(self.allow.get(to, 0))
        if method == "eth_getTransactionReceipt":
            st = self.txs.get(params[0])
            return None if st is None else {"status": "0x1" if st == "ok" else "0x0"}
        raise AssertionError(method)


class Market:
    def __init__(self, px):
        self.px = px

    def now_ms(self):
        return int(time.time() * 1000)

    def bid(self, pair):
        return self.px[pair.split("/")[0]]

    ask = bid


def _wallet(tmp_path, provider="phantom", chain="solana", **kw):
    ws = w.WalletSettings(
        provider=provider,
        chain=chain,
        approve_seconds=kw.pop("approve", 5),
        confirm_seconds=kw.pop("confirm", 5),
        **kw,
    )
    addr = SOL_ADDR if provider == "phantom" else EVM_ADDR
    w.connect(tmp_path, provider, addr, None if provider == "phantom" else hex(ws.spec["chain_id"]))
    usd = {
        "SOL": 150.0,
        "USDC": 1.0,
        "USDT": 1.0,
        "JUP": 0.5,
        "BONK": 0.00002,
        "ETH": 3000.0,
        "BTC": 60_000.0,
        "LINK": 15.0,
        "ARB": 0.8,
        "BNB": 600.0,
        "POL": 0.4,
    }
    chain_ = FakeChain(ws, addr, lambda s: usd[s])
    broker = w.WalletBroker(Market(usd), ws, tmp_path, http=chain_, sleep=FAST)
    return ws, chain_, broker


def _user(tmp_path, ws, chain, *, act="approve", fail=False, gone=False):
    """You, at the dashboard: wait for the ticket, then approve / reject / walk away."""
    done = {}

    def run():
        for _ in range(4000):
            tickets = w.open_tickets(tmp_path)
            if tickets:
                break
            time.sleep(0.002)
        else:
            return
        t = tickets[0]
        done["ticket"] = t
        if act == "reject":
            w.reject(tmp_path, t["id"], "no")
            return
        if act == "wait":
            return
        tx = w.prepare(tmp_path, t["id"], ws, http=chain, sleep=lambda s: None)
        done["tx"] = tx
        if tx["kind"] == "solana":
            q = json.loads(base64.b64decode(tx["tx"]))
            sig = chain.execute(q["inputMint"], q["outputMint"], int(q["inAmount"]), fail=fail)
        else:
            if tx["approve"]:
                chain.allow[tx["approve"]["to"]] = int(tx["approve"]["data"][-64:], 16)
            rs = json.loads(bytes.fromhex(tx["swap"]["data"][2:]))
            if not chain.tokens[rs["tokenIn"]]["native"]:
                assert chain.allow.get(rs["tokenIn"], 0) >= int(rs["amountIn"])
            sig = chain.execute(rs["tokenIn"], rs["tokenOut"], int(rs["amountIn"]), fail=fail)
        if not gone:
            w.mark_sent(tmp_path, t["id"], sig)

    th = threading.Thread(target=run, daemon=True)
    th.start()
    return th, done


def test_tokens_map_per_chain_and_wrong_setups_are_refused():
    ws = w.WalletSettings(provider="phantom", chain="solana")
    assert ws.token("SOL")["native"] and ws.token("USDT")["symbol"] == "USDC"
    assert w.WalletSettings(provider="metamask", chain="base").token("USDT")["symbol"] == "USDC"
    assert w.WalletSettings(provider="metamask", chain="base").token("BTC")["decimals"] == 8
    with pytest.raises(ValueError, match="metamask trades on"):
        w.WalletSettings(provider="metamask", chain="solana")
    with pytest.raises(ValueError, match="BNB has no token on Solana"):
        BotSettings(mode="wallet", pairs=("BNB/USDT",), wallet={"provider": "phantom"})
    custom = w.WalletSettings(
        tokens={"WIF": {"address": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm", "decimals": 6}}
    )
    assert custom.token("wif")["decimals"] == 6
    BotSettings(mode="wallet", pairs=("SOL/USDT", "JUP/USDT"), wallet={"provider": "phantom"})


def test_connect_validates_the_address(tmp_path):
    with pytest.raises(w.WalletError, match="not a MetaMask address"):
        w.connect(tmp_path, "metamask", "0x123")
    with pytest.raises(w.WalletError, match="not a Phantom address"):
        w.connect(tmp_path, "phantom", "0OIl" * 10)
    c = w.connect(tmp_path, "metamask", EVM_ADDR, "0x2105")
    assert c["chain"] == "base" and w.read_connection(tmp_path)["address"] == EVM_ADDR
    w.disconnect(tmp_path)
    assert w.read_connection(tmp_path) is None


def test_phantom_buy_is_booked_from_the_wallet_balance_change(tmp_path):
    ws, chain, broker = _wallet(tmp_path)
    chain.fund("USDC", 500)
    chain.fund("SOL", 0.1)
    assert broker.free("SOL") == pytest.approx(0.08)  # 0.02 SOL kept for fees
    th, done = _user(tmp_path, ws, chain)
    fill = broker.buy("SOL/USDT", 0.5)  # 75 USDC at 150
    th.join(5)
    assert fill.qty == pytest.approx(0.5 - 5_000 / 1e9)  # the network fee came out of SOL
    assert fill.price == pytest.approx(75 / fill.qty)
    assert chain.human("USDC") == pytest.approx(425)
    t = w.read_ticket(tmp_path, done["ticket"]["id"])
    assert t["status"] == "filled" and t["tx"].startswith("sig")
    assert w.open_tickets(tmp_path) == [] and w.recent_tickets(tmp_path)[0]["status"] == "filled"


def test_metamask_sell_and_buy_with_token_approval(tmp_path):
    ws, chain, broker = _wallet(tmp_path, "metamask", "base")
    chain.fund("USDC", 1000)
    chain.fund("ETH", 0.01)
    th, done = _user(tmp_path, ws, chain)
    fill = broker.buy("BTC/USDT", 0.001)  # 60 USDC of cbBTC
    th.join(5)
    assert done["tx"]["approve"] is not None  # the router could not spend USDC yet
    a = done["tx"]["approve"]["data"]
    assert a.startswith("0x" + w.APPROVE) and ROUTER[2:] in a and int(a[-64:], 16) == 60 * 10**6
    assert fill.qty == pytest.approx(0.001) and fill.price == pytest.approx(60_000)
    th, done = _user(tmp_path, ws, chain)
    out = broker.sell("BTC/USDT", fill.qty)
    th.join(5)
    assert done["tx"]["approve"] is not None and done["tx"]["swap"]["value"] == "0x0"
    assert out.qty == pytest.approx(0.001) and out.price == pytest.approx(60_000)
    assert chain.human("USDC") == pytest.approx(1000)


def test_nothing_trades_without_you(tmp_path):
    ws, chain, broker = _wallet(tmp_path, approve=0.3)
    chain.fund("USDC", 500)
    chain.fund("SOL", 1)
    th, _ = _user(tmp_path, ws, chain, act="reject")
    with pytest.raises(w.WalletError, match="rejected"):
        broker.buy("SOL/USDT", 0.1)
    th, done = _user(tmp_path, ws, chain, act="wait")
    with pytest.raises(w.WalletError, match="not approved within"):
        broker.buy("SOL/USDT", 0.1)
    th.join(5)
    with pytest.raises(w.WalletError, match="already expired"):  # a late click does nothing
        w.prepare(tmp_path, done["ticket"]["id"], ws, http=chain)
    assert chain.human("USDC") == 500


def test_failed_transactions_and_walking_away_after_approving(tmp_path):
    ws, chain, broker = _wallet(tmp_path, confirm=0.5)
    chain.fund("USDC", 500)
    chain.fund("SOL", 1)
    th, _ = _user(tmp_path, ws, chain, fail=True)
    with pytest.raises(w.WalletError, match="failed on chain"):
        broker.buy("SOL/USDT", 0.1)
    th.join(5)
    th, _ = _user(tmp_path, ws, chain, gone=True)  # swapped, but the page never reported it
    fill = broker.buy("SOL/USDT", 0.1)
    th.join(5)
    assert fill.qty == pytest.approx(0.1, rel=1e-3)


def test_wrong_token_addresses_and_missing_gas_are_refused(tmp_path):
    ws, chain, broker = _wallet(tmp_path, max_trade_usd=1e6)
    chain.fund("USDC", 500)
    with pytest.raises(w.WalletError, match="for network fees"):
        broker.buy("SOL/USDT", 0.1)
    chain.fund("SOL", 1)
    chain.wrong_decimals.add(ws.token("JUP")["address"])
    with pytest.raises(w.WalletError, match="reports 7 decimals"):
        broker.buy("JUP/USDT", 10)
    with pytest.raises(w.WalletError, match="needs 1500"):
        broker.buy("SOL/USDT", 10)
    assert w.open_tickets(tmp_path) == []


def test_max_trade_usd_caps_a_swap(tmp_path):
    ws, chain, broker = _wallet(tmp_path, max_trade_usd=30)
    chain.fund("USDC", 500)
    chain.fund("SOL", 1)
    th, done = _user(tmp_path, ws, chain)
    fill = broker.buy("SOL/USDT", 1.0)  # 150 USDC asked, 30 allowed
    th.join(5)
    assert done["ticket"]["amount_in"] == pytest.approx(30) and fill.qty == pytest.approx(
        0.2, rel=1e-3
    )


def test_the_dashboard_connects_and_approves_through_its_api(tmp_path):
    from research.trendbot.dashboard import Controller, FleetRuntime

    settings = tmp_path / "bot.json"
    state = tmp_path / "state"
    raw = {
        "mode": "wallet",
        "pairs": ["SOL/USDT"],
        "state_dir": str(state),
        "wallet": {"provider": "phantom", "chain": "solana"},
    }
    settings.write_text(json.dumps(raw))
    state.mkdir()
    rt = FleetRuntime(None, {"bot": (state, None, Controller(state, settings))})
    code, body = rt.act({"action": "wallet_connect", "provider": "metamask", "address": EVM_ADDR})
    assert code == 200 and "not MetaMask" in body["message"].replace("Metamask", "MetaMask")
    code, body = rt.act({"action": "wallet_connect", "provider": "phantom", "address": "nope"})
    assert code == 400 and "not a Phantom address" in body["message"]
    code, body = rt.act({"action": "wallet_prepare", "id": "123-abc"})
    assert code == 400 and "no such trade ticket" in body["message"]
    snap = rt.snapshot("bot")
    assert snap["wallet"]["mode"] == "wallet" and snap["wallet"]["chain_name"] == "Solana"
    assert snap["wallet"]["connected"]["provider"] == "metamask"
    raw["mode"] = "paper"
    time.sleep(0.01)
    settings.write_text(json.dumps(raw))
    import os

    os.utime(settings, (time.time() + 5, time.time() + 5))
    code, body = rt.act({"action": "wallet_prepare", "id": "x"})
    assert code == 400 and 'not in "wallet" mode' in body["message"]


def test_the_bot_trades_through_wallet_tickets(tmp_path, monkeypatch):
    """The full loop on a synthetic world: rules -> ticket -> you approve -> journal."""
    from research.trendbot.synthetic import make_world

    data, _ = make_world("planted", seed=3, years=2.0)
    ex = FakeExchange(data, data["BTC/USDT"][WARMUP].ts + 2000)
    s = BotSettings(
        exchange="binance",
        mode="wallet",
        pairs=PAIRS,
        state_dir=str(tmp_path / "s"),
        poll_seconds=3600.0,
        close_delay_seconds=1.0,
        history_candles=1200,
        wallet={
            "provider": "metamask",
            "chain": "bsc",
            "max_trade_usd": 1e9,
            "approve_seconds": 5,
            "confirm_seconds": 5,
        },
    )
    ws = w.WalletSettings(**s.wallet)
    w.connect(tmp_path / "s", "metamask", EVM_ADDR, "0x38")
    market = CcxtGateway(ex, sleep=lambda x: None)
    usd = lambda sym: 1.0 if sym in ("USDT", "USDC") else market.bid(f"{sym}/USDT")  # noqa: E731
    chain = FakeChain(ws, EVM_ADDR, usd)
    chain.fund("USDT", 10_000)
    chain.fund("BNB", 0.05)  # gas
    broker = w.WalletBroker(market, ws, tmp_path / "s", http=chain, sleep=FAST)
    bot = TrendBot(s, broker, sleep=ex.advance)
    approvals = {"n": 0}
    stop = threading.Event()

    def you():
        while not stop.is_set():
            for t in w.open_tickets(tmp_path / "s"):
                if t["status"] != "pending":
                    continue
                tx = w.prepare(tmp_path / "s", t["id"], ws, http=chain, sleep=lambda x: None)
                if tx["approve"]:
                    chain.allow[tx["approve"]["to"]] = int(tx["approve"]["data"][-64:], 16)
                rs = json.loads(bytes.fromhex(tx["swap"]["data"][2:]))
                sig = chain.execute(rs["tokenIn"], rs["tokenOut"], int(rs["amountIn"]))
                w.mark_sent(tmp_path / "s", t["id"], sig)
                approvals["n"] += 1
            time.sleep(0.002)

    th = threading.Thread(target=you, daemon=True)
    th.start()
    try:
        bot.start()
        end = data["BTC/USDT"][WARMUP + 1500].ts
        while ex.now < end:
            bot.step()
            ex.advance(s.poll_seconds)
    finally:
        stop.set()
        th.join(5)
    trades = read_journal(bot.journal_path)
    closed = [t for t in trades if t.is_closed]
    assert closed and approvals["n"] >= 2 * len(closed)
    beat = json.loads((tmp_path / "s" / "heartbeat.json").read_text())
    assert beat["mode"] == "wallet"
    held = {sym: chain.human(sym) for sym in ("BTC", "ETH")}
    for sym, qty in held.items():  # what the wallet holds is exactly the open positions
        assert qty == pytest.approx(
            sum(t.qty for t in trades if not t.is_closed and t.pair.startswith(sym)), abs=1e-6
        )
