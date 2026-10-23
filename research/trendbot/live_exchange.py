"""Exchange access for the live bot: a ccxt gateway and a paper broker with the same API.

Both expose exactly what :mod:`live_bot` needs::

    closed_candles(pair, n)  -> the last ``n`` CLOSED 4H candles (Coinbase: aggregated 2H)
    bid(pair) / ask(pair)    -> current best bid / ask (falls back to last)
    buy(pair, qty) / sell(pair, qty) -> Fill (actual average price, filled qty, fees)
    free(asset) / total(asset)       -> balances
    round_qty(pair, qty)     -> qty rounded DOWN to the exchange's amount precision
    min_order(pair)          -> (min amount, min cost) from the market limits
    now_ms()                 -> exchange clock

``ccxt`` is imported lazily so the rest of the package stays stdlib-only. API keys are
read from the environment (``TRENDBOT_API_KEY``, ``TRENDBOT_API_SECRET``, optional
``TRENDBOT_API_PASSWORD``); they are never written to disk or logs.
"""

from __future__ import annotations

import logging
import math
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from .data import resample_candles, timeframe_to_ms
from .fetch_data import _DEFAULT_LIMIT, _PAGE_LIMITS, fetch_ohlcv, plan_timeframe
from .models import Candle, base_of


log = logging.getLogger("trendbot.exchange")

ENV_KEY = "TRENDBOT_API_KEY"
ENV_SECRET = "TRENDBOT_API_SECRET"  # noqa: S105 - env var name, not a secret
ENV_PASSWORD = "TRENDBOT_API_PASSWORD"  # noqa: S105 - env var name, not a secret
TIMEFRAME = "4h"
CLOCK_REFRESH_MS = 15 * 60_000  # re-measure the exchange clock offset this often


def _ccxt_errors(*names: str) -> tuple[type[BaseException], ...]:
    """The named ccxt exception classes; () when ccxt is not installed (it is optional)."""
    try:
        import ccxt
    except ImportError:
        return ()
    return tuple(c for c in (getattr(ccxt, n, None) for n in names) if isinstance(c, type))


def transient_errors() -> tuple[type[BaseException], ...]:
    """Errors worth waiting out: network trouble, exchange 5xx / 429 / 418, a file locked by
    another program (Windows PermissionError). The run loop backs off and continues."""
    return (
        *_ccxt_errors("NetworkError", "BadResponse"),
        OSError,
        ConnectionError,
        TimeoutError,
    )


_HTTP_451 = re.compile(r"(?<![\d.])451(?![\d.])|restricted location", re.IGNORECASE)


def fatal_reason(exc: BaseException, exchange: str = "the exchange") -> str | None:
    """One plain-English line when ``exc`` will not go away by waiting (else None).

    Rejected API keys, a suspended account, an unknown pair, and an exchange that refuses
    the country the bot runs from (HTTP 451) stop the bot instead of retrying forever.
    """
    name = exchange.capitalize() if exchange.islower() else exchange
    blocked = _ccxt_errors("ExchangeError", "ExchangeNotAvailable")
    if blocked and isinstance(exc, blocked) and _HTTP_451.search(str(exc)):
        return f"{name} refuses this location (HTTP 451): use another exchange in bot.json"
    if isinstance(exc, _ccxt_errors("AccountSuspended")):
        return f"{name} says the account is suspended: {exc}"
    if isinstance(exc, _ccxt_errors("AuthenticationError", "PermissionDenied")):
        return (
            f"{name} rejected the API keys ({exc}): check the keys and their permissions in "
            "the dashboard (Connections) or .env"
        )
    if isinstance(exc, _ccxt_errors("BadSymbol")):
        return f"{name} does not list a pair in bot.json ({exc}): fix pairs in bot.json"
    return None


def load_env_file(path: str | os.PathLike[str] | None = None) -> list[str]:
    """Load ``KEY=VALUE`` lines from a .env file into ``os.environ`` (existing vars win).

    Default file: ``$TRENDBOT_ENV_FILE`` or ``./.env``. Returns the names loaded (never the
    values). Lines starting with ``#`` and blank lines are ignored; quotes are stripped.
    """
    from pathlib import Path

    p = Path(path or os.environ.get("TRENDBOT_ENV_FILE") or ".env")
    if not p.is_file():
        return []
    try:  # utf-8-sig: Notepad may save a byte-order mark first
        text = p.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = "\x00"
    except OSError as exc:
        raise ValueError(f"could not read {p.name}: {exc}") from None
    if "\x00" in text:  # UTF-16 ("Unicode" in Notepad)
        raise ValueError(
            f"could not read {p.name}: it is not UTF-8 text; save it as UTF-8 in Notepad"
        )
    loaded = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded


def combine_fills(a: Fill, b: Fill) -> Fill:
    """One fill from two partial fills of the same order intent."""
    qty = a.qty + b.qty
    if qty <= 0:
        return b
    price = (a.qty * a.price + b.qty * b.price) / qty
    fee = None if a.fee_quote is None or b.fee_quote is None else a.fee_quote + b.fee_quote
    return Fill(qty, price, fee, max(a.ts, b.ts), f"{a.order_id}+{b.order_id}".strip("+"))


class OrderError(RuntimeError):
    """An order could not be placed or its outcome could not be established."""


@dataclass(frozen=True, slots=True)
class Fill:
    qty: float  # base quantity actually held after the order (fees in base deducted)
    price: float  # average execution price
    fee_quote: float | None  # fee paid in quote currency, None if paid in another asset
    ts: int  # ms UTC
    order_id: str


def quote_of(pair: str) -> str:
    return pair.split("/", 1)[1].split(":", 1)[0].upper()


def with_retries(
    fn: Callable[[], Any],
    what: str,
    retry_on: tuple[type[BaseException], ...],
    attempts: int = 4,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """Call ``fn`` retrying transient errors with exponential backoff (1, 2, 4 s ...)."""
    for i in range(attempts):
        try:
            return fn()
        except retry_on as exc:
            if i == attempts - 1 or fatal_reason(exc) is not None:
                raise
            delay = 2.0**i
            log.warning(
                "%s failed (%s); retry %d/%d in %.0fs", what, exc, i + 1, attempts - 1, delay
            )
            sleep(delay)
    raise AssertionError("unreachable")


def fill_from_order(order: dict[str, Any], pair: str, side: str, now_ms: int) -> Fill:
    """Parse a ccxt order structure into a :class:`Fill` (fees in base reduce a buy's qty)."""
    filled = float(order.get("filled") or 0.0)
    if filled <= 0:
        return Fill(0.0, 0.0, 0.0, now_ms, str(order.get("id", "")))
    price = order.get("average")
    if not price:
        cost = order.get("cost")
        price = float(cost) / filled if cost else float(order.get("price") or 0.0)
    price = float(price)
    fees = list(order.get("fees") or ([order["fee"]] if order.get("fee") else []))
    base, quote = base_of(pair), quote_of(pair)
    # no fee entries (Binance fetch_order has none): unknown, the model fee rate is used
    fee_quote: float | None = 0.0 if fees else None
    held = filled
    for fee in fees:
        cost = float(fee.get("cost") or 0.0)
        cur = str(fee.get("currency") or "").upper()
        if cost == 0:
            continue
        if cur == quote:
            fee_quote = (fee_quote or 0.0) + cost
        elif cur == base:
            if side == "buy":
                held -= cost
            fee_quote = (fee_quote or 0.0) + cost * price
        else:
            fee_quote = None  # e.g. paid in BNB: the model fee rate is used instead
            break
    ts = int(order.get("timestamp") or now_ms)
    return Fill(max(held, 0.0), price, fee_quote, ts, str(order.get("id", "")))


def exchange_config(exchange_id: str, **params: Any) -> dict[str, Any]:
    """ccxt constructor config: rate limiting on; Binance also syncs its request clock
    (``adjustForTimeDifference``) so a drifting PC clock does not get orders rejected."""
    config: dict[str, Any] = {"enableRateLimit": True, **params}
    if exchange_id.startswith("binance"):
        config["options"] = {"adjustForTimeDifference": True}
    return config


class CcxtGateway:
    """Real exchange access (live or testnet) through ccxt."""

    def __init__(self, exchange: Any, *, sleep: Callable[[float], None] = time.sleep) -> None:
        self.ex = exchange
        self.sleep = sleep
        self.tf_ms = timeframe_to_ms(TIMEFRAME)
        self.request_tf = plan_timeframe(exchange, TIMEFRAME)
        self.request_ms = timeframe_to_ms(self.request_tf)
        self._transient: tuple[type[BaseException], ...] = _ccxt_errors(
            "NetworkError", "BadResponse"
        ) or (ConnectionError, TimeoutError)  # no ccxt: a fake exchange in tests
        self._invalid = _ccxt_errors("InvalidOrder")
        self._clock_offset = 0  # exchange time minus local time, ms
        self._clock_checked: int | None = None
        self._orders = 0
        self._call(self.ex.load_markets, "load_markets")

    @classmethod
    def connect(cls, exchange_id: str, mode: str, **kwargs: Any) -> CcxtGateway:
        try:
            import ccxt
        except ImportError as exc:
            raise SystemExit("ccxt is not installed: pip install ccxt") from exc
        from .connections import key_names, resolve_keys

        load_env_file()
        key, secret, password = resolve_keys(exchange_id, mode)
        if not key or not secret:
            names = key_names(exchange_id, mode) if exchange_id in ("binance", "coinbase") else ()
            raise SystemExit(
                f"no API keys for {exchange_id} {mode}: add them in the dashboard "
                f"(Connections) or set {' / '.join(names[:2]) or ENV_KEY} in .env"
            )
        params: dict[str, Any] = {"apiKey": key, "secret": secret}
        if password:
            params["password"] = password
        exchange = getattr(ccxt, exchange_id)(exchange_config(exchange_id, **params))
        if mode == "testnet":
            try:
                exchange.set_sandbox_mode(True)
            except _ccxt_errors("NotSupported"):
                raise SystemExit(
                    f"{exchange_id.capitalize()} has no testnet in ccxt: use paper mode, or "
                    "Binance testnet"
                ) from None
        return cls(exchange, **kwargs)

    # ------------------------------------------------------------------ helpers
    def _call(self, fn: Callable[[], Any], what: str) -> Any:
        return with_retries(fn, what, self._transient, sleep=self.sleep)

    def _local_ms(self) -> int:
        clock = getattr(self.ex, "milliseconds", None)
        return int(clock()) if callable(clock) else int(time.time() * 1000)

    def now_ms(self) -> int:
        """The exchange clock: local time corrected by the offset measured with fetch_time
        (every 15 minutes), so a candle counts as closed when it closed on the exchange."""
        local = self._local_ms()
        has = getattr(self.ex, "has", None) or {}
        if has.get("fetchTime") and (
            self._clock_checked is None or local - self._clock_checked >= CLOCK_REFRESH_MS
        ):
            self._clock_checked = local
            try:
                server = int(self.ex.fetch_time())
                after = self._local_ms()
                self._clock_offset = server - (local + after) // 2
                local = after
            except Exception as exc:  # a missed measurement keeps the last offset
                log.debug("exchange clock unavailable: %s", exc)
        return local + self._clock_offset

    # ------------------------------------------------------------------ market data
    def closed_candles(self, pair: str, n: int) -> list[Candle]:
        per = self.tf_ms // self.request_ms
        page = _PAGE_LIMITS.get(getattr(self.ex, "id", ""), _DEFAULT_LIMIT)
        now = self.now_ms()
        since = now - (n + 2) * self.tf_ms
        candles = self._call(
            lambda: fetch_ohlcv(self.ex, pair, self.request_tf, since, limit=page),
            f"fetch_ohlcv {pair}",
        )
        if per != 1:
            candles = resample_candles(candles, self.request_ms, self.tf_ms)
        # by the exchange clock: the local clock may run ahead into the forming candle
        candles = [c for c in candles if c.ts + self.tf_ms <= now]
        return candles[-n:]

    def _ticker(self, pair: str) -> dict[str, Any]:
        return self._call(lambda: self.ex.fetch_ticker(pair), f"fetch_ticker {pair}")

    def tickers(self, pairs: list[str]) -> dict[str, dict[str, Any]]:
        """Latest ticker of every pair: one request when the exchange supports it."""
        if (getattr(self.ex, "has", None) or {}).get("fetchTickers"):
            got = self._call(lambda: self.ex.fetch_tickers(list(pairs)), "fetch_tickers")
            return {p: got[p] for p in pairs if p in got}
        return {p: self._ticker(p) for p in pairs}

    def forming_candle(self, pair: str) -> Candle | None:
        """The 4H candle that is still open, aggregated from the exchange's own candles."""
        now = self.now_ms()
        start = now // self.tf_ms * self.tf_ms
        n = self.tf_ms // self.request_ms + 1
        rows = self._call(
            lambda: self.ex.fetch_ohlcv(pair, self.request_tf, since=start, limit=n),
            f"fetch_ohlcv {pair} (forming)",
        )
        rows = [r for r in rows or [] if int(r[0]) >= start]
        if not rows:
            return None
        return Candle(
            start,
            float(rows[0][1]),
            max(float(r[2]) for r in rows),
            min(float(r[3]) for r in rows),
            float(rows[-1][4]),
            sum(float(r[5] or 0) for r in rows),
        )

    def bid(self, pair: str) -> float:
        t = self._ticker(pair)
        return float(t.get("bid") or t.get("last"))

    def ask(self, pair: str) -> float:
        t = self._ticker(pair)
        return float(t.get("ask") or t.get("last"))

    # ------------------------------------------------------------------ balances
    def _balance(self) -> dict[str, Any]:
        return self._call(self.ex.fetch_balance, "fetch_balance")

    def free(self, asset: str) -> float:
        return float((self._balance().get(asset.upper()) or {}).get("free") or 0.0)

    def total(self, asset: str) -> float:
        return float((self._balance().get(asset.upper()) or {}).get("total") or 0.0)

    def _free_or_none(self, asset: str) -> float | None:
        try:
            return self.free(asset)
        except Exception as exc:  # only used to cross-check an order: never fatal here
            log.debug("balance of %s unavailable: %s", asset, exc)
            return None

    # ------------------------------------------------------------------ orders
    def round_qty(self, pair: str, qty: float) -> float:
        if qty <= 0:
            return 0.0
        try:
            text = self.ex.amount_to_precision(pair, qty)
        except self._invalid:  # ccxt refuses a result of "0": dust below one step
            return 0.0
        rounded = float(text)
        return rounded if rounded <= qty * (1 + 1e-12) else 0.0

    def min_order(self, pair: str) -> tuple[float, float]:
        limits = (self.ex.market(pair) or {}).get("limits") or {}
        amount = float(((limits.get("amount") or {}).get("min")) or 0.0)
        cost = float(((limits.get("cost") or {}).get("min")) or 0.0)
        return amount, cost

    def _client_id(self, side: str) -> str:
        """A unique client order id (Binance: at most 36 of [A-Za-z0-9_-])."""
        self._orders += 1
        return f"tb{side[0]}{self._local_ms()}{os.getpid() % 1000:03d}{self._orders % 1000:03d}"

    def _id_params(self, cid: str) -> dict[str, Any]:
        """The order parameter carrying our client order id (exchanges without one: none)."""
        ex_id = str(getattr(self.ex, "id", ""))
        if ex_id.startswith("binance"):
            return {"newClientOrderId": cid}
        if ex_id.startswith("coinbase"):
            return {"client_order_id": cid}
        return {}

    def _create(
        self, pair: str, kind: str, side: str, qty: float, price: float | None, params: dict
    ) -> dict[str, Any]:
        if price is None and not params:
            return self.ex.create_order(pair, kind, side, qty)
        return self.ex.create_order(pair, kind, side, qty, price, params)

    def _find_order(self, pair: str, cid: str) -> dict[str, Any] | None:
        """Look an order up by our client order id after its reply was lost."""
        ex_id = str(getattr(self.ex, "id", ""))
        lookups: list[Callable[[], Any]] = []
        if ex_id.startswith("binance"):
            lookups.append(lambda: [self.ex.fetch_order(None, pair, {"origClientOrderId": cid})])
        has = getattr(self.ex, "has", None) or {}
        for method, name in (
            ("fetchOpenOrders", "fetch_open_orders"),
            ("fetchClosedOrders", "fetch_closed_orders"),
        ):
            if has.get(method):
                lookups.append(lambda name=name: getattr(self.ex, name)(pair))
        for lookup in lookups:
            try:
                found = self._call(lookup, f"look up order {cid}") or []
            except Exception as exc:  # not found / not supported: try the next way
                log.debug("order lookup %s: %s", cid, exc)
                continue
            for o in found:
                info = o.get("info") or {}
                if cid in (
                    o.get("clientOrderId"),
                    info.get("clientOrderId"),
                    info.get("client_order_id"),
                ):
                    return o
        return None

    def _unknown_outcome(
        self, pair: str, side: str, qty: float, cid: str, before: float | None, exc: Exception
    ) -> dict[str, Any] | Fill:
        """The order's reply was lost: find the order, else read what the balance did.

        Never blindly re-send an order: the first one may have been accepted.
        """
        what = f"{side} {pair} {qty}: network error, outcome unknown: {exc}"
        log.warning("%s; checking whether it went through", what)
        if self._id_params(cid):
            order = self._find_order(pair, cid)
            if order is not None:
                log.warning("%s: found order %s (%s)", pair, order.get("id"), order.get("status"))
                return order
        base = base_of(pair)
        after = self._free_or_none(base) if before is not None else None
        if before is not None and after is not None:
            moved = after - before if side == "buy" else before - after
            if moved > qty * 0.01:  # it went through (fees in base make a buy a hair smaller)
                done = min(moved, qty)
                price = self.ask(pair) if side == "buy" else self.bid(pair)
                log.warning(
                    "%s: the %s balance moved by %.8g: the %s went through at about %.8g",
                    pair,
                    base,
                    moved,
                    side,
                    price,
                )
                return Fill(done, price, None, self.now_ms(), f"unknown-{cid}")
            raise OrderError(f"{what}; no order found and the {base} balance did not move")
        raise OrderError(what)

    def _await(self, order: dict[str, Any], pair: str) -> dict[str, Any]:
        deadline = time.monotonic() + 30
        while order.get("status") not in ("closed", "canceled", "expired", "rejected"):
            if time.monotonic() > deadline:
                break
            self.sleep(1.0)
            oid = order.get("id")
            order = self._call(lambda oid=oid: self.ex.fetch_order(oid, pair), f"fetch_order {oid}")
        return order

    def _cap_bought(self, fill: Fill, pair: str, before: float | None) -> Fill:
        """A buy holds at most what the base balance grew by (Binance takes its fee in the
        base coin and fetch_order does not report it)."""
        after = self._free_or_none(base_of(pair)) if before is not None and fill.qty > 0 else None
        if before is None or after is None:
            return fill
        grew = after - before
        if 0 < grew < fill.qty * (1 - 1e-9):
            fee = fill.fee_quote
            if fee is None:  # the shortfall is the fee, paid in the base coin
                fee = (fill.qty - grew) * fill.price
            return replace(fill, qty=grew, fee_quote=fee)
        return fill

    def _market_buy_price(self, pair: str) -> float | None:
        """Coinbase prices a market buy in quote (amount * price): ccxt needs the price."""
        options = getattr(self.ex, "options", None) or {}
        return self.ask(pair) if options.get("createMarketBuyOrderRequiresPrice") else None

    def _market_order(self, pair: str, side: str, qty: float) -> Fill:
        before = self._free_or_none(base_of(pair))
        cid = self._client_id(side)
        price = self._market_buy_price(pair) if side == "buy" else None
        try:
            order = self._create(pair, "market", side, qty, price, self._id_params(cid))
        except self._transient as exc:
            found = self._unknown_outcome(pair, side, qty, cid, before, exc)
            if isinstance(found, Fill):
                return found
            order = found
        fill = fill_from_order(self._await(order, pair), pair, side, self.now_ms())
        return self._cap_bought(fill, pair, before) if side == "buy" else fill

    def buy(self, pair: str, qty: float) -> Fill:
        return self._market_order(pair, "buy", qty)

    def buy_maker_first(self, pair: str, qty: float, *, wait_s: float = 15.0) -> Fill:
        """Post-only limit buy at the bid (maker fee, no slippage); whatever has not filled
        after ``wait_s`` seconds is cancelled and bought at market."""
        has = getattr(self.ex, "has", None) or {}
        if not (has.get("createPostOnlyOrder") or has.get("createLimitOrder")):
            return self.buy(pair, qty)
        before = self._free_or_none(base_of(pair))
        price = self.bid(pair)
        if hasattr(self.ex, "price_to_precision"):
            price = float(self.ex.price_to_precision(pair, price))
        cid = self._client_id("buy")
        params = {"postOnly": True, **self._id_params(cid)}
        try:
            order = self.ex.create_order(pair, "limit", "buy", qty, price, params)
        except self._invalid:  # it would have taken liquidity (or was refused): plain market order
            return self.buy(pair, qty)
        except self._transient as exc:
            found = self._unknown_outcome(pair, "buy", qty, cid, before, exc)
            if isinstance(found, Fill):
                return found
            order = found
        oid = order.get("id")
        done = ("closed", "canceled", "expired", "rejected")
        for _ in range(max(1, int(wait_s))):
            if order.get("status") in done:
                break
            self.sleep(1.0)
            order = self._call(lambda: self.ex.fetch_order(oid, pair), f"fetch_order {oid}")
        if order.get("status") not in done:
            try:
                self._call(lambda: self.ex.cancel_order(oid, pair), f"cancel_order {oid}")
            except Exception as exc:  # it may have filled meanwhile: the fetch below tells
                log.warning("cancel of %s failed: %s", oid, exc)
            order = self._call(lambda: self.ex.fetch_order(oid, pair), f"fetch_order {oid}")
        maker = self._cap_bought(fill_from_order(order, pair, "buy", self.now_ms()), pair, before)
        rest = self.round_qty(pair, qty - float(order.get("filled") or 0.0))
        min_amount, min_cost = self.min_order(pair)
        if rest <= 0 or rest < min_amount or rest * price < min_cost:
            return maker
        taker = self.buy(pair, rest)
        return combine_fills(maker, taker)

    def sell(self, pair: str, qty: float) -> Fill:
        return self._market_order(pair, "sell", qty)


class PaperBroker:
    """Paper trading on live public market data: same API, simulated market fills.

    Fills at ask*(1+slippage) / bid*(1-slippage), fee ``fee_rate`` of notional in quote.
    Balances are kept in memory and persisted by the bot through :meth:`state` /
    :meth:`restore`.
    """

    def __init__(
        self,
        market: Any,
        *,
        fee_rate: float,
        slippage_pct: float,
        balances: dict[str, float] | None = None,
    ) -> None:
        self.market = market  # a CcxtGateway (public data) or any object with the same reads
        self.fee_rate = fee_rate
        self.slip = slippage_pct / 100.0
        self.balances: dict[str, float] = dict(balances or {})
        self._n = 0

    @classmethod
    def connect(cls, exchange_id: str, **kwargs: Any) -> PaperBroker:
        try:
            import ccxt
        except ImportError as exc:
            raise SystemExit("ccxt is not installed: pip install ccxt") from exc
        public = CcxtGateway(getattr(ccxt, exchange_id)(exchange_config(exchange_id)))
        return cls(public, **kwargs)

    def state(self) -> dict[str, float]:
        return dict(self.balances)

    def now_ms(self) -> int:
        return self.market.now_ms()

    def closed_candles(self, pair: str, n: int) -> list[Candle]:
        return self.market.closed_candles(pair, n)

    def bid(self, pair: str) -> float:
        return self.market.bid(pair)

    def tickers(self, pairs: list[str]) -> dict[str, dict[str, Any]]:
        return self.market.tickers(pairs)

    def forming_candle(self, pair: str) -> Candle | None:
        return self.market.forming_candle(pair)

    def ask(self, pair: str) -> float:
        return self.market.ask(pair)

    def free(self, asset: str) -> float:
        return self.balances.get(asset.upper(), 0.0)

    total = free

    def round_qty(self, pair: str, qty: float) -> float:
        return self.market.round_qty(pair, qty)

    def min_order(self, pair: str) -> tuple[float, float]:
        return self.market.min_order(pair)

    def _fill(self, pair: str, side: str, qty: float) -> Fill:
        base, quote = base_of(pair), quote_of(pair)
        if side == "buy":
            price = self.ask(pair) * (1 + self.slip)
            cost = qty * price
            fee = cost * self.fee_rate
            if cost + fee > self.free(quote) + 1e-9:
                raise OrderError(f"paper: insufficient {quote} for {qty} {base}")
            self.balances[quote] = self.free(quote) - cost - fee
            self.balances[base] = self.free(base) + qty
        else:
            qty = min(qty, self.free(base))
            price = self.bid(pair) * (1 - self.slip)
            proceeds = qty * price
            fee = proceeds * self.fee_rate
            self.balances[base] = self.free(base) - qty
            self.balances[quote] = self.free(quote) + proceeds - fee
        self._n += 1
        if not math.isfinite(price) or price <= 0:
            raise OrderError(f"paper: bad price {price!r} for {pair}")
        return Fill(qty, price, fee, self.now_ms(), f"paper-{self._n}")

    def buy(self, pair: str, qty: float) -> Fill:
        return self._fill(pair, "buy", qty)

    def sell(self, pair: str, qty: float) -> Fill:
        return self._fill(pair, "sell", qty)
