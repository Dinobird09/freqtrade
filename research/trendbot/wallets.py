"""Wallet trading: MetaMask (Ethereum, Base, Arbitrum, BNB Chain, Polygon) and Phantom (Solana).

How it works
    The bot never holds a private key. MetaMask and Phantom only sign what you approve, so
    every trade is a **ticket** you approve in the wallet:

    1. you connect the wallet in the dashboard (Wallet card); its public address is saved to
       ``<state_dir>/wallet.json``;
    2. when the nine rules pass, or a stop / target is hit, the bot writes a ticket to
       ``<state_dir>/wallet/tickets/<id>.json`` and waits (``approve_seconds``, default 120);
    3. the dashboard pops it up. **Approve** asks the swap aggregator for a route (Jupiter on
       Solana, KyberSwap on the EVM chains), the wallet shows you the transaction and you
       confirm it there; **Reject** declines it;
    4. the bot confirms the transaction on chain and books the fill from the wallet's balance
       change (the quote's amounts if the RPC lags).

    Prices, candles and signals still come from the exchange's free public data (``exchange``
    in bot.json, no key). Pairs map to tokens on the chain: ``BTC/USDT`` on Base is cbBTC
    against USDC, ``SOL/USDT`` on Solana is SOL against USDC (see :data:`CHAINS`; add your
    own under ``wallet.tokens``). Token decimals are checked on chain before the first
    trade, so a wrong address is refused instead of traded.

    Stops are only as good as your presence: an exit also needs your approval. If you don't
    approve in time the bot asks again every loop until the position is closed.

Settings (bot.json)::

    "mode": "wallet",
    "wallet": {"provider": "phantom", "chain": "solana", "max_trade_usd": 100}

The swap APIs and RPC endpoints are public and need no key; override them with
``wallet.rpc_url`` / ``wallet.aggregator_url`` (e.g. a Helius or Alchemy URL).
"""

from __future__ import annotations

import json
import logging
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .live_exchange import Fill, OrderError, quote_of
from .models import Candle, base_of


log = logging.getLogger("trendbot.wallet")

PROVIDERS = {"metamask": "evm", "phantom": "solana"}
NAMES = {"metamask": "MetaMask", "phantom": "Phantom"}
NATIVE_EVM = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"  # the aggregators' "native coin" address
STABLES = ("USDT", "USDC", "USD", "BUSD", "FDUSD", "DAI")
JUPITER = "https://lite-api.jup.ag"
KYBER = "https://aggregator-api.kyberswap.com"


def _t(address: str, decimals: int, native: bool = False) -> dict[str, Any]:
    return {"address": address, "decimals": decimals, "native": native}


# The bot's asset names -> the token traded on each chain. Quote assets (USDT, USDC, ...) map
# to the chain's ``stable``.
CHAINS: dict[str, dict[str, Any]] = {
    "solana": {
        "kind": "solana",
        "name": "Solana",
        "rpc": "https://api.mainnet-beta.solana.com",
        "native": "SOL",
        "stable": "USDC",
        "gas_reserve": 0.02,
        "explorer": "https://solscan.io/tx/",
        "tokens": {
            "SOL": _t("So11111111111111111111111111111111111111112", 9, native=True),
            "USDC": _t("EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", 6),
            "USDT": _t("Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", 6),
            "JUP": _t("JUPyiwrYJFskUPiHa7hkeR8VUtAeFoSYbKedZNsDvCN", 6),
            "BONK": _t("DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", 5),
        },
    },
    "ethereum": {
        "kind": "evm",
        "name": "Ethereum",
        "chain_id": 1,
        "kyber": "ethereum",
        "rpc": "https://ethereum-rpc.publicnode.com",
        "native": "ETH",
        "stable": "USDC",
        "gas_reserve": 0.005,
        "explorer": "https://etherscan.io/tx/",
        "tokens": {
            "ETH": _t(NATIVE_EVM, 18, native=True),
            "USDC": _t("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", 6),
            "USDT": _t("0xdAC17F958D2ee523a2206206994597C13D831ec7", 6),
            "BTC": _t("0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599", 8),  # WBTC
            "LINK": _t("0x514910771AF9Ca656af840dff83E8264EcF986CA", 18),
        },
    },
    "base": {
        "kind": "evm",
        "name": "Base",
        "chain_id": 8453,
        "kyber": "base",
        "rpc": "https://mainnet.base.org",
        "native": "ETH",
        "stable": "USDC",
        "gas_reserve": 0.0005,
        "explorer": "https://basescan.org/tx/",
        "tokens": {
            "ETH": _t(NATIVE_EVM, 18, native=True),
            "USDC": _t("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", 6),
            "BTC": _t("0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf", 8),  # cbBTC
        },
    },
    "arbitrum": {
        "kind": "evm",
        "name": "Arbitrum",
        "chain_id": 42161,
        "kyber": "arbitrum",
        "rpc": "https://arb1.arbitrum.io/rpc",
        "native": "ETH",
        "stable": "USDC",
        "gas_reserve": 0.0005,
        "explorer": "https://arbiscan.io/tx/",
        "tokens": {
            "ETH": _t(NATIVE_EVM, 18, native=True),
            "USDC": _t("0xaf88d065e77c8cC2239327C5EDb3A432268e5831", 6),
            "BTC": _t("0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f", 8),  # WBTC
            "ARB": _t("0x912CE59144191C1204E64559FE8253a0e49E6548", 18),
        },
    },
    "bsc": {
        "kind": "evm",
        "name": "BNB Chain",
        "chain_id": 56,
        "kyber": "bsc",
        "rpc": "https://bsc-dataseed.bnbchain.org",
        "native": "BNB",
        "stable": "USDT",
        "gas_reserve": 0.003,
        "explorer": "https://bscscan.com/tx/",
        "tokens": {
            "BNB": _t(NATIVE_EVM, 18, native=True),
            "USDT": _t("0x55d398326f99059fF775485246999027B3197955", 18),
            "USDC": _t("0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d", 18),
            "BTC": _t("0x7130d2A12B9BCbFAe4f2634d864A1Ee1Ce3Ead9c", 18),  # BTCB
            "ETH": _t("0x2170Ed0880ac9A755fd29B2688956BD959F933F8", 18),
        },
    },
    "polygon": {
        "kind": "evm",
        "name": "Polygon",
        "chain_id": 137,
        "kyber": "polygon",
        "rpc": "https://polygon-rpc.com",
        "native": "POL",
        "stable": "USDC",
        "gas_reserve": 1.0,
        "explorer": "https://polygonscan.com/tx/",
        "tokens": {
            "POL": _t(NATIVE_EVM, 18, native=True),
            "USDC": _t("0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359", 6),
            "ETH": _t("0x7ceB23fD6bC0adD59E62ac25578270cFf1b9f619", 18),  # WETH
            "BTC": _t("0x1BFD67037B42Cf73acF2047067bd4F2C47D9BfD6", 8),  # WBTC
        },
    },
}
CHAIN_BY_ID = {c["chain_id"]: name for name, c in CHAINS.items() if "chain_id" in c}


class WalletError(OrderError):
    """A wallet trade could not be made or confirmed; the message is safe to show."""


@dataclass
class WalletSettings:
    provider: str = "phantom"  # "phantom" (Solana) or "metamask" (EVM chains)
    chain: str = "solana"
    max_trade_usd: float = 100.0  # no single swap spends more than this
    slippage_bps: int = 50  # 0.5%
    approve_seconds: float = 120.0  # how long a ticket waits for you
    confirm_seconds: float = 180.0  # how long a sent transaction may take to confirm
    rpc_url: str | None = None
    aggregator_url: str | None = None
    stable: str | None = None  # the token quote assets map to (default: the chain's)
    gas_reserve: float | None = None  # native coin never sold, kept for network fees
    tokens: dict[str, Any] = field(default_factory=dict)  # {"WIF": {"address", "decimals"}}

    def __post_init__(self) -> None:
        if self.provider not in PROVIDERS:
            raise ValueError(f"wallet.provider must be one of {sorted(PROVIDERS)}")
        if self.chain not in CHAINS:
            raise ValueError(f"wallet.chain must be one of {sorted(CHAINS)}")
        if PROVIDERS[self.provider] != CHAINS[self.chain]["kind"]:
            kinds = [n for n, c in CHAINS.items() if c["kind"] == PROVIDERS[self.provider]]
            raise ValueError(f"{self.provider} trades on {', '.join(kinds)}, not {self.chain}")
        if self.max_trade_usd <= 0 or not 1 <= self.slippage_bps <= 500:
            raise ValueError("wallet.max_trade_usd must be > 0 and slippage_bps 1-500")
        for sym, tok in self.tokens.items():
            if not isinstance(tok, Mapping) or not tok.get("address") or "decimals" not in tok:
                raise ValueError(f"wallet.tokens.{sym} needs an address and decimals")

    @property
    def spec(self) -> dict[str, Any]:
        return CHAINS[self.chain]

    @property
    def rpc(self) -> str:
        return (
            self.rpc_url
            or os.environ.get(f"TRENDBOT_{self.chain.upper()}_RPC_URL")
            or (os.environ.get("SOLANA_RPC_URL") if self.chain == "solana" else None)
            or self.spec["rpc"]
        )

    @property
    def reserve(self) -> float:
        return float(self.spec["gas_reserve"] if self.gas_reserve is None else self.gas_reserve)

    def token(self, asset: str) -> dict[str, Any]:
        """The token the bot's ``asset`` trades as on this chain, with its symbol."""
        a = asset.upper()
        tokens = {**self.spec["tokens"], **{k.upper(): dict(v) for k, v in self.tokens.items()}}
        if a in STABLES and a not in self.tokens:
            a = (self.stable or self.spec["stable"]).upper()
        if a not in tokens:
            raise ValueError(
                f"{asset} has no token on {self.spec['name']}; add it under wallet.tokens "
                f"(known: {', '.join(sorted(tokens))})"
            )
        tok = tokens[a]
        return {
            "symbol": a,
            **tok,
            "decimals": int(tok["decimals"]),
            "native": bool(tok.get("native")),
        }

    def check_pairs(self, pairs: tuple[str, ...]) -> None:
        for p in pairs:
            self.token(base_of(p))
            self.token(quote_of(p))


# ---------------------------------------------------------------------- HTTP / JSON-RPC
Http = Callable[[str, Any, Mapping[str, str] | None], Any]


def http_json(url: str, payload: Any = None, headers: Mapping[str, str] | None = None) -> Any:
    """GET (``payload`` None) or POST JSON; returns the decoded reply."""
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    if not url.startswith("https://"):
        raise WalletError(f"only https URLs are used, not {url!r}")
    req = urllib.request.Request(  # noqa: S310 - https only (checked above)
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "trendbot/1.0",
            **(headers or {}),
        },
        method="GET" if payload is None else "POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 - https endpoints
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if 400 <= exc.code < 500:  # the API's own error reply ("no route", ...): not retried
            try:
                return json.loads(exc.read().decode("utf-8"))
            except ValueError:
                return {"error": f"HTTP {exc.code}"}
        raise


def _retry(fn: Callable[[], Any], what: str, sleep: Callable[[float], None]) -> Any:
    for i in range(3):
        try:
            return fn()
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, ValueError) as exc:
            if i == 2:
                detail = exc.reason if isinstance(exc, urllib.error.URLError) else exc
                raise WalletError(f"{what} failed: {detail}") from exc
            sleep(1.5 * (i + 1))
    raise AssertionError("unreachable")


class Rpc:
    def __init__(
        self, url: str, http: Http | None = None, sleep: Callable[[float], None] = time.sleep
    ):
        self.url, self.http, self.sleep = url, http, sleep

    def __call__(self, method: str, params: list[Any]) -> Any:
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        http = self.http or http_json  # looked up per call, so a test can swap the network
        reply = _retry(lambda: http(self.url, body, None), f"RPC {method}", self.sleep)
        if not isinstance(reply, Mapping):
            raise WalletError(f"RPC {method}: unexpected reply")
        if reply.get("error"):
            err = reply["error"]
            raise WalletError(
                f"RPC {method}: {err.get('message', err) if isinstance(err, Mapping) else err}"
            )
        return reply.get("result")


def _pad(hex_or_int: str | int) -> str:
    v = hex_or_int if isinstance(hex_or_int, int) else int(hex_or_int, 16)
    return f"{v:064x}"


def erc20_call(selector: str, *args: str | int) -> str:
    """ABI-encode a call with address / uint256 arguments: ``0x<selector><32-byte args>``."""
    return "0x" + selector + "".join(_pad(a) for a in args)


BALANCE_OF, DECIMALS, ALLOWANCE, APPROVE = "70a08231", "313ce567", "dd62ed3e", "095ea7b3"


def to_raw(amount: float, decimals: int) -> int:
    """Human amount -> integer base units, rounded DOWN (never spend more than asked)."""
    return math.floor(amount * 10**decimals + 1e-9)


# ---------------------------------------------------------------------- the chain
class Chain:
    """Balances, decimals checks and confirmations for one wallet address on one chain."""

    def __init__(
        self,
        ws: WalletSettings,
        http: Http | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.ws = ws
        self.rpc = Rpc(ws.rpc, http, sleep)
        self.kind = ws.spec["kind"]
        self._checked: set[str] = set()

    def check_token(self, tok: Mapping[str, Any]) -> None:
        """Refuse a token whose on-chain decimals differ from the table (a wrong address)."""
        if tok["native"] or tok["address"] in self._checked:
            return
        if self.kind == "solana":
            res = self.rpc("getTokenSupply", [tok["address"]]) or {}
            dec = (res.get("value") or {}).get("decimals")
        else:
            out = self.rpc(
                "eth_call", [{"to": tok["address"], "data": erc20_call(DECIMALS)}, "latest"]
            )
            dec = int(out, 16) if isinstance(out, str) and len(out) > 2 else None
        if dec != tok["decimals"]:
            raise WalletError(
                f"{tok['symbol']} at {tok['address']} reports {dec} decimals on chain, "
                f"expected {tok['decimals']}: check wallet.tokens"
            )
        self._checked.add(tok["address"])

    def balance(self, address: str, tok: Mapping[str, Any]) -> float:
        if self.kind == "solana":
            if tok["native"]:
                res = self.rpc("getBalance", [address, {"commitment": "confirmed"}]) or {}
                return int(res.get("value") or 0) / 1e9
            res = (
                self.rpc(
                    "getTokenAccountsByOwner",
                    [
                        address,
                        {"mint": tok["address"]},
                        {"encoding": "jsonParsed", "commitment": "confirmed"},
                    ],
                )
                or {}
            )
            raw = 0
            for acc in res.get("value") or []:
                info = (((acc.get("account") or {}).get("data") or {}).get("parsed") or {}).get(
                    "info"
                ) or {}
                raw += int((info.get("tokenAmount") or {}).get("amount") or 0)
            return raw / 10 ** tok["decimals"]
        if tok["native"]:
            return int(self.rpc("eth_getBalance", [address, "latest"]), 16) / 1e18
        out = self.rpc(
            "eth_call", [{"to": tok["address"], "data": erc20_call(BALANCE_OF, address)}, "latest"]
        )
        return (int(out, 16) if isinstance(out, str) and len(out) > 2 else 0) / 10 ** tok[
            "decimals"
        ]

    def allowance(self, owner: str, spender: str, tok: Mapping[str, Any]) -> int:
        out = self.rpc(
            "eth_call",
            [{"to": tok["address"], "data": erc20_call(ALLOWANCE, owner, spender)}, "latest"],
        )
        return int(out, 16) if isinstance(out, str) and len(out) > 2 else 0

    def tx_status(self, tx: str) -> str:
        """``"ok"``, ``"failed"`` or ``"pending"``."""
        if self.kind == "solana":
            res = self.rpc("getSignatureStatuses", [[tx], {"searchTransactionHistory": True}]) or {}
            st = (res.get("value") or [None])[0]
            if not st:
                return "pending"
            if st.get("err"):
                return "failed"
            return "ok" if st.get("confirmationStatus") in ("confirmed", "finalized") else "pending"
        rec = self.rpc("eth_getTransactionReceipt", [tx])
        if not rec:
            return "pending"
        return "ok" if str(rec.get("status")) in ("0x1", "1") else "failed"


def all_balances(chain: Chain, ws: WalletSettings, address: str) -> dict[str, float]:
    out = {}
    for sym in {*ws.spec["tokens"], *(k.upper() for k in ws.tokens)}:
        tok = ws.token(sym)
        out[tok["symbol"]] = chain.balance(address, tok)
    return out


def write_status(
    state_dir: Path, ws: WalletSettings, address: str, bal: Mapping[str, float], ts: int
) -> None:
    try:
        _write(
            Path(state_dir) / "wallet" / "status.json",
            {
                "provider": ws.provider,
                "chain": ws.chain,
                "address": address,
                "balances": dict(bal),
                "ts": ts,
            },
        )
    except (WalletError, OSError) as exc:
        log.warning("wallet status not written: %s", exc)


def refresh_status(
    state_dir: Path, ws: WalletSettings, http: Http | None = None
) -> dict[str, float]:
    """Read the connected wallet's balances now (the dashboard does this on connect)."""
    conn = read_connection(state_dir)
    if conn is None or conn.get("provider") != ws.provider:
        raise WalletError(f"connect {NAMES[ws.provider]} first")
    bal = all_balances(Chain(ws, http, lambda s: None), ws, str(conn["address"]))
    write_status(state_dir, ws, str(conn["address"]), bal, int(time.time() * 1000))
    return bal


# ---------------------------------------------------------------------- tickets
def wallet_dir(state_dir: Path) -> Path:
    return Path(state_dir) / "wallet" / "tickets"


def read_connection(state_dir: Path) -> dict[str, Any] | None:
    p = Path(state_dir) / "wallet.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("address") else None


def _write(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:6]}.tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    for i in range(20):  # Windows refuses to replace a file another process is reading
        try:
            tmp.replace(path)
            return
        except PermissionError:
            time.sleep(0.05 * (i + 1))
    tmp.unlink(missing_ok=True)
    raise WalletError(f"could not write {path.name}")


def read_ticket(state_dir: Path, tid: str) -> dict[str, Any] | None:
    if not tid or not all(c.isalnum() or c == "-" for c in tid):
        return None
    try:
        return json.loads((wallet_dir(state_dir) / f"{tid}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def update_ticket(
    state_dir: Path, tid: str, expect: tuple[str, ...], **changes: Any
) -> dict[str, Any]:
    """Move a ticket on only from one of the ``expect`` states (so a late click can't revive it)."""
    t = read_ticket(state_dir, tid)
    if t is None:
        raise WalletError("no such trade ticket")
    if t["status"] not in expect:
        raise WalletError(f"this ticket is already {t['status']}")
    t.update(changes, updated_ms=int(time.time() * 1000))
    _write(wallet_dir(state_dir) / f"{tid}.json", t)
    return t


def open_tickets(state_dir: Path, now_ms: int | None = None) -> list[dict[str, Any]]:
    """Tickets still waiting for you or for the chain, newest first."""
    d = wallet_dir(state_dir)
    if not d.is_dir():
        return []
    now = int(time.time() * 1000) if now_ms is None else now_ms
    out = []
    for p in d.glob("*.json"):
        try:
            t = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if t.get("status") in ("pending", "approved", "sent"):
            t["seconds_left"] = max(0, round((int(t.get("expires_ms", 0)) - now) / 1000))
            out.append(t)
    return sorted(out, key=lambda t: -int(t.get("created_ms", 0)))


def recent_tickets(state_dir: Path, n: int = 8) -> list[dict[str, Any]]:
    d = wallet_dir(state_dir)
    if not d.is_dir():
        return []
    rows = []
    for p in sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:n]:
        try:
            rows.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return rows


# ---------------------------------------------------------------------- the broker
class WalletBroker:
    """Same API as PaperBroker / CcxtGateway; orders become tickets you approve in the wallet."""

    buy_maker_first = None  # swaps are market orders

    def __init__(
        self,
        market: Any,
        ws: WalletSettings,
        state_dir: Path,
        *,
        http: Http | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.market = market  # public prices and candles (a CcxtGateway without keys)
        self.ws = ws
        self.dir = Path(state_dir)
        self.chain = Chain(ws, http, sleep)
        self.sleep = sleep
        self.clock = clock
        self.on_wait: Callable[[], None] | None = None  # the bot keeps its heartbeat fresh
        self._bal: dict[str, float] = {}
        self._bal_ts = 0.0

    @classmethod
    def connect(cls, exchange_id: str, ws: WalletSettings, state_dir: Path) -> WalletBroker:
        from .live_exchange import CcxtGateway

        conn = read_connection(state_dir)
        if conn is None or conn.get("provider") != ws.provider:
            raise SystemExit(
                f"connect {NAMES[ws.provider]} first: open the dashboard, Wallet card, "
                f"Connect {NAMES[ws.provider]}"
            )
        try:
            import ccxt
        except ImportError as exc:
            raise SystemExit("ccxt is not installed: pip install ccxt") from exc
        public = CcxtGateway(getattr(ccxt, exchange_id)({"enableRateLimit": True}))
        return cls(public, ws, state_dir)

    # ------------------------------------------------------------------ market data
    def now_ms(self) -> int:
        return self.market.now_ms()

    def closed_candles(self, pair: str, n: int) -> list[Candle]:
        return self.market.closed_candles(pair, n)

    def tickers(self, pairs: list[str]) -> dict[str, dict[str, Any]]:
        return self.market.tickers(pairs)

    def forming_candle(self, pair: str) -> Candle | None:
        return self.market.forming_candle(pair)

    def bid(self, pair: str) -> float:
        return self.market.bid(pair)

    def ask(self, pair: str) -> float:
        return self.market.ask(pair)

    # ------------------------------------------------------------------ the wallet
    @property
    def address(self) -> str:
        conn = read_connection(self.dir)
        if conn is None:
            raise WalletError(
                f"no wallet connected: open the dashboard and connect {NAMES[self.ws.provider]}"
            )
        if conn.get("provider") != self.ws.provider:
            raise WalletError(
                f"the dashboard connected {conn.get('provider')}, "
                f"but bot.json says {self.ws.provider}"
            )
        return str(conn["address"])

    def balances(self, fresh: bool = False) -> dict[str, float]:
        """Wallet balances of every token the bot can trade (cached 5 s)."""
        if not fresh and self._bal and self.clock() - self._bal_ts < 5:
            return self._bal
        self._bal = all_balances(self.chain, self.ws, self.address)
        self._bal_ts = self.clock()
        write_status(self.dir, self.ws, self.address, self._bal, int(self._bal_ts * 1000))
        return self._bal

    def free(self, asset: str) -> float:
        tok = self.ws.token(asset)
        have = self.balances().get(tok["symbol"], 0.0)
        return max(0.0, have - self.ws.reserve) if tok["native"] else have

    def total(self, asset: str) -> float:
        return self.balances().get(self.ws.token(asset)["symbol"], 0.0)

    def round_qty(self, pair: str, qty: float) -> float:
        if qty <= 0:
            return 0.0
        dec = min(self.ws.token(base_of(pair))["decimals"], 8)
        return math.floor(qty * 10**dec) / 10**dec

    def min_order(self, pair: str) -> tuple[float, float]:
        return 0.0, 5.0  # swaps below ~$5 cost more in fees than they move

    # ------------------------------------------------------------------ orders
    def buy(self, pair: str, qty: float) -> Fill:
        ask = self.ask(pair)
        usd = min(qty * ask, self.ws.max_trade_usd)
        if usd < qty * ask:
            log.info("%s: capped at max_trade_usd %.2f", pair, self.ws.max_trade_usd)
        return self._swap(pair, "buy", usd, est_out=usd / ask)

    def sell(self, pair: str, qty: float) -> Fill:
        return self._swap(pair, "sell", qty, est_out=qty * self.bid(pair))

    def _swap(self, pair: str, side: str, amount_in: float, est_out: float) -> Fill:
        base, quote = self.ws.token(base_of(pair)), self.ws.token(quote_of(pair))
        tin, tout = (quote, base) if side == "buy" else (base, quote)
        for tok in (tin, tout):
            self.chain.check_token(tok)
        native = self.ws.token(self.ws.spec["native"])
        bal = self.balances(fresh=True)
        if bal.get(native["symbol"], 0.0) < self.ws.reserve / 2:
            raise WalletError(
                f"keep at least {self.ws.reserve:g} {native['symbol']} in the wallet "
                "for network fees"
            )
        if bal.get(tin["symbol"], 0.0) + 1e-12 < amount_in:
            raise WalletError(
                f"the wallet has {bal.get(tin['symbol'], 0.0):.6g} {tin['symbol']}, "
                f"needs {amount_in:.6g}"
            )
        now = int(self.clock() * 1000)
        tid = f"{now}-{uuid.uuid4().hex[:6]}"
        ticket = {
            "id": tid,
            "status": "pending",
            "side": side,
            "pair": pair,
            "provider": self.ws.provider,
            "chain": self.ws.chain,
            "chain_name": self.ws.spec["name"],
            "chain_id": self.ws.spec.get("chain_id"),
            "address": self.address,
            "token_in": tin,
            "token_out": tout,
            "amount_in": amount_in,
            "amount_in_raw": str(to_raw(amount_in, tin["decimals"])),
            "est_out": est_out,
            "slippage_bps": self.ws.slippage_bps,
            "pre": {
                tin["symbol"]: bal.get(tin["symbol"], 0.0),
                tout["symbol"]: bal.get(tout["symbol"], 0.0),
            },
            "created_ms": now,
            "expires_ms": now + int(self.ws.approve_seconds * 1000),
        }
        _write(wallet_dir(self.dir) / f"{tid}.json", ticket)
        log.info(
            "WALLET %s %s: approve swapping %.6g %s for ~%.6g %s in %s (ticket %s)",
            side.upper(),
            pair,
            amount_in,
            tin["symbol"],
            est_out,
            tout["symbol"],
            NAMES[self.ws.provider],
            tid,
        )
        return self._await(tid)

    def _await(self, tid: str) -> Fill:
        give_up = self.clock() + self.ws.approve_seconds + self.ws.confirm_seconds + 60
        while True:
            if self.on_wait is not None:
                self.on_wait()
            t = read_ticket(self.dir, tid) or {}
            now = int(self.clock() * 1000)
            st = t.get("status")
            if not t and self.clock() > give_up:
                raise WalletError(f"ticket {tid} disappeared")
            if st == "rejected":
                raise WalletError(
                    f"ticket {tid} rejected: {t.get('error') or 'declined in the wallet'}"
                )
            if st == "pending" and now > int(t["expires_ms"]):
                try:
                    update_ticket(
                        self.dir, tid, ("pending",), status="expired", error="not approved in time"
                    )
                except WalletError:
                    continue  # approved at the last moment: look again
                raise WalletError(
                    f"ticket {tid} was not approved within {self.ws.approve_seconds:g}s"
                )
            if (
                st == "approved"
                and now > int(t.get("updated_ms", now)) + self.ws.confirm_seconds * 1000
            ):
                return self._settle(t, None)  # the page went away after approving: did it swap?
            if st == "sent":
                status = self.chain.tx_status(str(t["tx"]))
                if status == "failed":
                    update_ticket(
                        self.dir,
                        tid,
                        ("sent",),
                        status="failed",
                        error="the transaction failed on chain",
                    )
                    raise WalletError(f"ticket {tid}: the transaction failed on chain ({t['tx']})")
                if status == "ok":
                    return self._settle(t, t["tx"])
                if now > int(t.get("updated_ms", now)) + self.ws.confirm_seconds * 1000:
                    return self._settle(t, None)
            self.sleep(1.0)

    def _settle(self, t: dict[str, Any], tx: str | None) -> Fill:
        """Book the fill from the wallet's balance change (the quote if the RPC lags)."""
        tin, tout = t["token_in"], t["token_out"]
        got = spent = 0.0
        for _ in range(6 if tx else 1):
            bal = self.balances(fresh=True)
            got = bal.get(tout["symbol"], 0.0) - float(t["pre"][tout["symbol"]])
            spent = float(t["pre"][tin["symbol"]]) - bal.get(tin["symbol"], 0.0)
            if got > 0:
                break
            self.sleep(2.0)
        if got <= 0 and tx and t.get("quote"):
            got = int(t["quote"]["out_raw"]) / 10 ** tout["decimals"]
            spent = float(t["amount_in"])
            log.warning("ticket %s: balances not updated yet; booked from the quote", t["id"])
        if got <= 0:
            update_ticket(
                self.dir,
                t["id"],
                ("approved", "sent"),
                status="failed",
                error="no swap seen in the wallet",
            )
            raise WalletError(
                f"ticket {t['id']}: no swap seen in the wallet; check {NAMES[self.ws.provider]}"
            )
        spent = min(spent, float(t["amount_in"])) if spent > 0 else float(t["amount_in"])
        if tin["native"]:
            spent = float(t["amount_in"])  # the native balance also paid the network fee
        price = spent / got if t["side"] == "buy" else got / spent
        qty = got if t["side"] == "buy" else spent
        update_ticket(
            self.dir,
            t["id"],
            ("approved", "sent"),
            status="filled",
            fill={"qty": qty, "price": price, "in": spent, "out": got},
        )
        log.info("WALLET filled %s: %s %.8g at %.8g", t["id"], t["side"], qty, price)
        return Fill(qty, price, None, int(self.clock() * 1000), str(tx or t["id"]))


# ---------------------------------------------------------------------- the dashboard side
def connect(state_dir: Path, provider: str, address: str, chain_id: Any = None) -> dict[str, Any]:
    """Save the wallet the page connected (public address only)."""
    if provider not in PROVIDERS:
        raise WalletError("unknown wallet")
    address = str(address or "").strip()
    if PROVIDERS[provider] == "evm":
        ok = (
            address.startswith("0x")
            and len(address) == 42
            and all(c in "0123456789abcdefABCDEF" for c in address[2:])
        )
    else:
        ok = 32 <= len(address) <= 44 and all(c in _B58 for c in address)
    if not ok:
        raise WalletError(f"that is not a {NAMES[provider]} address")
    chain = None
    if chain_id is not None:
        try:
            chain = CHAIN_BY_ID.get(int(str(chain_id), 0))
        except ValueError:
            chain = None
    conn = {
        "provider": provider,
        "address": address,
        "chain": chain or ("solana" if provider == "phantom" else None),
        "connected_ms": int(time.time() * 1000),
    }
    _write(Path(state_dir) / "wallet.json", conn)
    return conn


def disconnect(state_dir: Path) -> None:
    (Path(state_dir) / "wallet.json").unlink(missing_ok=True)


def _why(reply: Any, key: str) -> Any:
    return reply.get(key) or reply if isinstance(reply, Mapping) else reply


_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def prepare(
    state_dir: Path,
    tid: str,
    ws: WalletSettings,
    *,
    http: Http | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Approve a pending ticket: fetch a route and return the transaction(s) for the wallet."""
    http = http or http_json
    t = read_ticket(state_dir, tid)
    if t is None:
        raise WalletError("no such trade ticket")
    if t["status"] != "pending":
        raise WalletError(f"this ticket is already {t['status']}")
    if int(time.time() * 1000) > int(t["expires_ms"]):
        raise WalletError(
            "this ticket has expired; the bot will ask again if the trade still stands"
        )
    tin, tout, raw = t["token_in"], t["token_out"], int(t["amount_in_raw"])
    if ws.spec["kind"] == "solana":
        base = (ws.aggregator_url or JUPITER).rstrip("/")
        q = urllib.parse.urlencode(
            {
                "inputMint": tin["address"],
                "outputMint": tout["address"],
                "amount": raw,
                "slippageBps": t["slippage_bps"],
            }
        )
        quote = _retry(
            lambda: http(f"{base}/swap/v1/quote?{q}", None, None), "Jupiter quote", sleep
        )
        if (
            not isinstance(quote, Mapping)
            or quote.get("outputMint") != tout["address"]
            or not quote.get("outAmount")
        ):
            raise WalletError(f"Jupiter found no route: {_why(quote, 'error')}")
        body = {
            "quoteResponse": quote,
            "userPublicKey": t["address"],
            "wrapAndUnwrapSol": True,
            "dynamicComputeUnitLimit": True,
        }
        swap = _retry(lambda: http(f"{base}/swap/v1/swap", body, None), "Jupiter swap", sleep)
        if not isinstance(swap, Mapping) or not swap.get("swapTransaction"):
            raise WalletError("Jupiter did not return a transaction")
        out = {
            "kind": "solana",
            "tx": swap["swapTransaction"],
            "out_raw": str(quote["outAmount"]),
            "min_out_raw": str(quote.get("otherAmountThreshold") or ""),
        }
    else:
        base = (ws.aggregator_url or KYBER).rstrip("/") + f"/{ws.spec['kyber']}/api/v1"
        hdr = {"x-client-id": "trendbot"}
        q = urllib.parse.urlencode(
            {"tokenIn": tin["address"], "tokenOut": tout["address"], "amountIn": raw}
        )
        routes = _retry(lambda: http(f"{base}/routes?{q}", None, hdr), "KyberSwap route", sleep)
        data = (routes or {}).get("data") if isinstance(routes, Mapping) else None
        if not data or not data.get("routeSummary"):
            raise WalletError(f"KyberSwap found no route: {_why(routes, 'message')}")
        body = {
            "routeSummary": data["routeSummary"],
            "sender": t["address"],
            "recipient": t["address"],
            "slippageTolerance": t["slippage_bps"],
            "source": "trendbot",
        }
        built = _retry(lambda: http(f"{base}/route/build", body, hdr), "KyberSwap build", sleep)
        b = (built or {}).get("data") if isinstance(built, Mapping) else None
        if not b or not b.get("data") or not b.get("routerAddress"):
            raise WalletError(f"KyberSwap could not build the swap: {_why(built, 'message')}")
        router = b["routerAddress"]
        if data.get("routerAddress") and data["routerAddress"].lower() != router.lower():
            raise WalletError("KyberSwap returned two different routers; refusing")
        value = int(b.get("transactionValue") or (raw if tin["native"] else 0))
        approve = None
        if not tin["native"] and Chain(ws, http, sleep).allowance(t["address"], router, tin) < raw:
            approve = {
                "from": t["address"],
                "to": tin["address"],
                "data": erc20_call(APPROVE, router, raw),
                "value": "0x0",
            }
        out = {
            "kind": "evm",
            "chain_id": hex(int(ws.spec["chain_id"])),
            "approve": approve,
            "swap": {"from": t["address"], "to": router, "data": b["data"], "value": hex(value)},
            "out_raw": str(b.get("amountOut") or data["routeSummary"].get("amountOut") or ""),
        }
    update_ticket(
        state_dir,
        tid,
        ("pending",),
        status="approved",
        quote={"out_raw": out["out_raw"]},
        expires_ms=int(time.time() * 1000) + int(ws.confirm_seconds * 1000),
    )
    return {**out, "id": tid, "explorer": ws.spec["explorer"]}


def mark_sent(state_dir: Path, tid: str, tx: str) -> dict[str, Any]:
    tx = str(tx or "").strip()
    if not tx or len(tx) > 120 or not all(c.isalnum() for c in tx.removeprefix("0x")):
        raise WalletError("that is not a transaction id")
    return update_ticket(state_dir, tid, ("approved",), status="sent", tx=tx)


def reject(state_dir: Path, tid: str, reason: str = "") -> dict[str, Any]:
    return update_ticket(
        state_dir,
        tid,
        ("pending", "approved"),
        status="rejected",
        error=(reason or "declined")[:200],
    )


def wallet_view(state_dir: Path, settings: Mapping[str, Any] | None) -> dict[str, Any]:
    """What the dashboard's Wallet card shows."""
    status = None
    try:
        status = json.loads(
            (Path(state_dir) / "wallet" / "status.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        status = None
    ws = None
    if settings is not None:
        try:
            ws = WalletSettings(**(settings.get("wallet") or {}))
        except (TypeError, ValueError):
            ws = None
    return {
        "mode": (settings or {}).get("mode"),
        "provider": ws.provider if ws else None,
        "chain": ws.chain if ws else None,
        "chain_name": ws.spec["name"] if ws else None,
        "chain_id": ws.spec.get("chain_id") if ws else None,
        "max_trade_usd": ws.max_trade_usd if ws else None,
        "connected": read_connection(state_dir),
        "status": status,
        "tickets": open_tickets(state_dir),
        "recent": [
            {
                k: t.get(k)
                for k in (
                    "id",
                    "status",
                    "side",
                    "pair",
                    "amount_in",
                    "token_in",
                    "token_out",
                    "tx",
                    "error",
                    "fill",
                    "created_ms",
                )
            }
            for t in recent_tickets(state_dir)
        ],
        "explorer": ws.spec["explorer"] if ws else None,
    }
