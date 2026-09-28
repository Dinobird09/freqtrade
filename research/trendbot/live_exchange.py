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
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .data import resample_candles, timeframe_to_ms
from .fetch_data import _DEFAULT_LIMIT, _PAGE_LIMITS, fetch_ohlcv, plan_timeframe
from .models import Candle, base_of


log = logging.getLogger("trendbot.exchange")

ENV_KEY = "TRENDBOT_API_KEY"
ENV_SECRET = "TRENDBOT_API_SECRET"  # noqa: S105 - env var name, not a secret
ENV_PASSWORD = "TRENDBOT_API_PASSWORD"  # noqa: S105 - env var name, not a secret
TIMEFRAME = "4h"


def load_env_file(path: str | os.PathLike[str] | None = None) -> list[str]:
    """Load ``KEY=VALUE`` lines from a .env file into ``os.environ`` (existing vars win).

    Default file: ``$TRENDBOT_ENV_FILE`` or ``./.env``. Returns the names loaded (never the
    values). Lines starting with ``#`` and blank lines are ignored; quotes are stripped.
    """
    from pathlib import Path

    p = Path(path or os.environ.get("TRENDBOT_ENV_FILE") or ".env")
    if not p.is_file():
        return []
    loaded = []
    for raw in p.read_text(encoding="utf-8").splitlines():
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
            if i == attempts - 1:
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
    fee_quote: float | None = 0.0
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


class CcxtGateway:
    """Real exchange access (live or testnet) through ccxt."""

    def __init__(self, exchange: Any, *, sleep: Callable[[float], None] = time.sleep) -> None:
        self.ex = exchange
        self.sleep = sleep
        self.tf_ms = timeframe_to_ms(TIMEFRAME)
        self.request_tf = plan_timeframe(exchange, TIMEFRAME)
        self.request_ms = timeframe_to_ms(self.request_tf)
        try:
            import ccxt

            self._transient: tuple[type[BaseException], ...] = (ccxt.NetworkError,)
        except ImportError:  # a fake exchange in tests
            self._transient = (ConnectionError, TimeoutError)
        self._call(self.ex.load_markets, "load_markets")

    @classmethod
    def connect(cls, exchange_id: str, mode: str, **kwargs: Any) -> CcxtGateway:
        try:
            import ccxt
        except ImportError as exc:
            raise SystemExit("ccxt is not installed: pip install ccxt") from exc
        load_env_file()
        key, secret = os.environ.get(ENV_KEY), os.environ.get(ENV_SECRET)
        if not key or not secret:
            raise SystemExit(f"set {ENV_KEY} and {ENV_SECRET} in the environment for {mode}")
        params: dict[str, Any] = {"apiKey": key, "secret": secret, "enableRateLimit": True}
        if os.environ.get(ENV_PASSWORD):
            params["password"] = os.environ[ENV_PASSWORD]
        exchange = getattr(ccxt, exchange_id)(params)
        if mode == "testnet":
            exchange.set_sandbox_mode(True)
        return cls(exchange, **kwargs)

    # ------------------------------------------------------------------ helpers
    def _call(self, fn: Callable[[], Any], what: str) -> Any:
        return with_retries(fn, what, self._transient, sleep=self.sleep)

    def now_ms(self) -> int:
        clock = getattr(self.ex, "milliseconds", None)
        return int(clock()) if callable(clock) else int(time.time() * 1000)

    # ------------------------------------------------------------------ market data
    def closed_candles(self, pair: str, n: int) -> list[Candle]:
        per = self.tf_ms // self.request_ms
        page = _PAGE_LIMITS.get(getattr(self.ex, "id", ""), _DEFAULT_LIMIT)
        since = self.now_ms() - (n + 2) * self.tf_ms
        candles = self._call(
            lambda: fetch_ohlcv(self.ex, pair, self.request_tf, since, limit=page),
            f"fetch_ohlcv {pair}",
        )
        if per != 1:
            candles = resample_candles(candles, self.request_ms, self.tf_ms)
        return candles[-n:]

    def _ticker(self, pair: str) -> dict[str, Any]:
        return self._call(lambda: self.ex.fetch_ticker(pair), f"fetch_ticker {pair}")

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

    # ------------------------------------------------------------------ orders
    def round_qty(self, pair: str, qty: float) -> float:
        if qty <= 0:
            return 0.0
        text = self.ex.amount_to_precision(pair, qty)
        rounded = float(text)
        return rounded if rounded <= qty * (1 + 1e-12) else 0.0

    def min_order(self, pair: str) -> tuple[float, float]:
        limits = (self.ex.market(pair) or {}).get("limits") or {}
        amount = float(((limits.get("amount") or {}).get("min")) or 0.0)
        cost = float(((limits.get("cost") or {}).get("min")) or 0.0)
        return amount, cost

    def _market_order(self, pair: str, side: str, qty: float) -> Fill:
        try:
            order = self.ex.create_order(pair, "market", side, qty)
        except self._transient as exc:
            # Never blindly re-send an order: the first one may have been accepted.
            raise OrderError(f"{side} {pair} {qty}: network error, outcome unknown: {exc}")
        deadline = time.monotonic() + 30
        while order.get("status") not in ("closed", "canceled", "expired", "rejected"):
            if time.monotonic() > deadline:
                break
            self.sleep(1.0)
            oid = order.get("id")
            order = self._call(lambda oid=oid: self.ex.fetch_order(oid, pair), f"fetch_order {oid}")
        return fill_from_order(order, pair, side, self.now_ms())

    def buy(self, pair: str, qty: float) -> Fill:
        return self._market_order(pair, "buy", qty)

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
        public = CcxtGateway(getattr(ccxt, exchange_id)({"enableRateLimit": True}))
        return cls(public, **kwargs)

    def state(self) -> dict[str, float]:
        return dict(self.balances)

    def now_ms(self) -> int:
        return self.market.now_ms()

    def closed_candles(self, pair: str, n: int) -> list[Candle]:
        return self.market.closed_candles(pair, n)

    def bid(self, pair: str) -> float:
        return self.market.bid(pair)

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
