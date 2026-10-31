"""Grid trading and dollar-cost averaging: engines that are not entry / stop strategies, so
they have their own backtest simulators and their own live bots.

``grid``  a virtual grid of price levels between ``lower`` and ``upper`` (``levels`` evenly
          spaced levels, or a geometric ``step_pct``). The ``budget`` (quote) is split into
          one unit per level below the top. When the price crosses a level DOWNWARD the bot
          market-buys one unit there; when it crosses the NEXT level up, that unit is sold.
          Each unit remembers its buy level, so every round trip earns one grid step (less
          fees). Optional ``stop_below`` / ``take_above`` sell everything and stop the grid.
          Best for sideways / ranging markets: in a trend the grid either sells out early
          (up) or keeps buying a falling price (down), and the backtest says how long the
          price spent outside the range.

``dca``   buys ``amount_quote`` every ``every`` ("1d", "7d", "30d", ...). Optional
          ``buy_the_dip`` (percent): one extra buy of ``dip_amount_quote`` (default
          ``amount_quote``) per period when the price is that far below the average cost.
          Optional ``take_profit_pct``: sell everything once the price is that far above the
          average cost, then keep accumulating. The backtest compares the result with
          investing the same total as one lump sum on the first day.

Live bots (``"engine": "grid"`` / ``"engine": "dca"`` in the settings, parameters in the
existing ``lab`` settings dict)::

    {"engine": "grid", "mode": "paper", "pairs": ["BTC/USDT"],
     "lab": {"grid": {"lower": 55000, "upper": 70000, "levels": 16, "budget": 2000}}}
    {"engine": "dca", "mode": "paper", "pairs": ["BTC/USDT"],
     "lab": {"dca": {"amount_quote": 50, "every": "7d", "buy_the_dip": 10}}}

One pair per bot. Fills go through the gateway's buy / sell, so paper, testnet, live and
wallet modes all work. State lives in ``<state_dir>/<engine>_state.json``; every lot is a
row in the bot's journal (one Trade per grid round trip, one per DCA buy), so the
dashboard and the Memory bank see them; the heartbeat, stop / close requests and the
"entries paused" switch are the main bot's.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .capital import read_lock
from .data import timeframe_to_ms
from .journal import atomic_write_text, read_journal
from .live_exchange import PaperBroker
from .models import EXIT_END, EXIT_SL, EXIT_TP, Candle, Trade, base_of


log = logging.getLogger("trendbot.engines")

GRID_NOTE = (
    "Best for sideways / ranging markets: the grid buys dips and sells rips inside its range; "
    "in a trend it sells out early (up) or keeps buying a falling price (down)."
)
DCA_NOTE = (
    "Dollar-cost averaging spreads the entry over time: it never picks the bottom, but it "
    "never puts everything in at the top either."
)


# ---------------------------------------------------------------------- grid
def grid_levels(params: Mapping[str, Any]) -> list[float]:
    """The grid's price levels, ascending: ``levels`` evenly spaced from ``lower`` to
    ``upper`` (both included), or every ``step_pct`` up from ``lower`` while <= ``upper``."""
    lo, hi = float(params["lower"]), float(params["upper"])
    if not 0 < lo < hi:
        raise ValueError("a grid needs 0 < lower < upper")
    step = params.get("step_pct")
    if step:
        f = 1 + float(step) / 100
        if f <= 1:
            raise ValueError("step_pct must be > 0")
        out = [lo]
        while out[-1] * f <= hi * (1 + 1e-9):
            out.append(out[-1] * f)
    else:
        n = int(params.get("levels", 10))
        if n < 2:
            raise ValueError("a grid needs at least 2 levels")
        out = [lo + (hi - lo) * k / (n - 1) for k in range(n)]
    if len(out) < 2:
        raise ValueError("step_pct is wider than the range: the grid has a single level")
    return out


def _path(c: Candle) -> list[float]:
    """The assumed intra-candle path: a green candle dips first, a red one rallies first."""
    return (
        [c.open, c.low, c.high, c.close] if c.close >= c.open else [c.open, c.high, c.low, c.close]
    )


def grid_backtest(  # noqa: C901 - one pass along every candle path: buys, sells, stops
    candles: Sequence[Candle],
    params: Mapping[str, Any],
    fee_rate: float = 0.001,
    slip_pct: float = 0.05,
) -> dict[str, Any]:
    """Simulate the virtual grid along each candle's path (see the module doc).

    A level crossed while the price moves fills AT the level (plus slippage); a gap between
    candles fills at the new open. Returns trades (round trips), realized / unrealized
    profit, fills, max drawdown, time out of range and the sideways-market note.
    """
    lv = grid_levels(params)
    budget = float(params.get("budget", 1000.0))
    unit = budget / (len(lv) - 1)
    stop_below, take_above = params.get("stop_below"), params.get("take_above")
    slip = slip_pct / 100
    cash = budget
    held: dict[int, dict[str, Any]] = {}  # level index -> the unit bought there
    trades: list[dict[str, Any]] = []
    fills, stopped = 0, None
    peak, max_dd = budget, 0.0
    below = above = 0
    if not candles:
        return {"engine": "grid", "trades": [], "note": GRID_NOTE, "warnings": ["no candles"]}

    def buy(k: int, px: float, ts: int) -> None:
        nonlocal cash, fills
        fill = px * (1 + slip)
        qty = unit / (fill * (1 + fee_rate))
        if cash + 1e-9 < unit:
            return
        cash -= qty * fill * (1 + fee_rate)
        held[k] = {"qty": qty, "price": fill, "ts": ts, "fee": qty * fill * fee_rate}
        fills += 1

    def sell(k: int, px: float, ts: int, why: str) -> None:
        nonlocal cash, fills
        u = held.pop(k)
        fill = px * (1 - slip)
        proceeds = u["qty"] * fill * (1 - fee_rate)
        cash += proceeds
        fills += 1
        trades.append(
            {
                "level": k,
                "buy_ts": u["ts"],
                "buy_price": u["price"],
                "sell_ts": ts,
                "sell_price": fill,
                "qty": u["qty"],
                "pnl": proceeds - u["qty"] * u["price"] - u["fee"],
                "exit": why,
            }
        )

    prev = candles[0].open
    for c in candles:
        legs = [(prev, c.open, True)] + [
            (a, b, False) for a, b in zip(_path(c)[:-1], _path(c)[1:], strict=True)
        ]
        for a, b, gap in legs:
            if stopped:
                break
            if stop_below is not None and min(a, b) <= float(stop_below):
                px = b if gap else min(a, float(stop_below))
                for k in sorted(held):
                    sell(k, px, c.ts, "stop_below")
                stopped = f"price fell to the stop {float(stop_below):g}: grid stopped"
                break
            if take_above is not None and max(a, b) >= float(take_above):
                px = b if gap else max(a, float(take_above))
                for k in sorted(held):
                    sell(k, px, c.ts, "take_above")
                stopped = f"price reached the take-profit {float(take_above):g}: grid stopped"
                break
            if b < a:  # down: buy every level crossed, top first
                for k in range(len(lv) - 2, -1, -1):
                    if a > lv[k] >= b and k not in held:
                        buy(k, b if gap else lv[k], c.ts)
            elif b > a:  # up: sell each unit whose next level was crossed, bottom first
                for k in range(1, len(lv)):
                    if a < lv[k] <= b and k - 1 in held:
                        sell(k - 1, b if gap else lv[k], c.ts, "grid")
        prev = c.close
        below += c.close < lv[0]
        above += c.close > lv[-1]
        eq = cash + sum(u["qty"] for u in held.values()) * c.close
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak * 100 if peak > 0 else 0.0)
    last = candles[-1].close
    realized = sum(t["pnl"] for t in trades)
    unrealized = sum(u["qty"] * last - u["qty"] * u["price"] - u["fee"] for u in held.values())
    final = cash + sum(u["qty"] for u in held.values()) * last
    n = len(candles)
    out_pct = (below + above) / n * 100
    warnings = []
    if out_pct > 0:
        warnings.append(
            f"price left the grid range {out_pct:.1f}% of the time "
            f"({below / n * 100:.1f}% below {lv[0]:g}, {above / n * 100:.1f}% above "
            f"{lv[-1]:g}): a grid only earns inside its range"
        )
    if stopped:
        warnings.append(stopped)
    return {
        "engine": "grid",
        "levels": lv,
        "unit_quote": unit,
        "budget": budget,
        "trades": trades,
        "round_trips": len(trades),
        "grid_fills": fills,
        "realized_profit": round(realized, 8),
        "unrealized_profit": round(unrealized, 8),
        "open_units": len(held),
        "final_equity": round(final, 8),
        "return_pct": round((final / budget - 1) * 100, 4),
        "buy_and_hold_pct": round((last / candles[0].open - 1) * 100, 4),
        "max_drawdown_pct": round(max_dd, 4),
        "time_out_of_range_pct": round(out_pct, 3),
        "time_below_pct": round(below / n * 100, 3),
        "time_above_pct": round(above / n * 100, 3),
        "stopped": stopped,
        "note": GRID_NOTE,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------- dca
def _dca_params(params: Mapping[str, Any]) -> tuple[float, int, float | None, float, float | None]:
    amount = float(params["amount_quote"])
    if amount <= 0:
        raise ValueError("amount_quote must be > 0")
    every = timeframe_to_ms(str(params.get("every", "7d")))
    dip = params.get("buy_the_dip")
    dip_amount = float(params.get("dip_amount_quote") or amount)
    tp = params.get("take_profit_pct")
    return amount, every, float(dip) if dip else None, dip_amount, float(tp) if tp else None


def average_cost(lots: Sequence[Mapping[str, float]]) -> float | None:
    """Quote spent (fees included) per unit held."""
    qty = sum(lot["qty"] for lot in lots)
    return sum(lot["cost"] for lot in lots) / qty if qty > 0 else None


def dca_backtest(
    candles: Sequence[Candle],
    params: Mapping[str, Any],
    fee_rate: float = 0.001,
    slip_pct: float = 0.05,
) -> dict[str, Any]:
    """Scheduled buys at the open of the first candle of each period, optional dip buys
    (at the dip price, once per period) and take-profit sells; compared with a lump sum."""
    amount, every, dip, dip_amount, tp = _dca_params(params)
    slip = slip_pct / 100
    if not candles:
        return {"engine": "dca", "buys": [], "note": DCA_NOTE}
    lots: list[dict[str, float]] = []
    buys: list[dict[str, Any]] = []
    sells: list[dict[str, Any]] = []
    invested = proceeds = realized = 0.0
    due = candles[0].ts
    dip_done = True
    peak_ratio, max_dd = 1.0, 0.0

    def buy(px: float, quote: float, ts: int, kind: str) -> None:
        nonlocal invested
        fill = px * (1 + slip)
        qty = quote / (fill * (1 + fee_rate))
        lots.append({"qty": qty, "cost": quote, "price": fill})
        buys.append({"ts": ts, "price": fill, "qty": qty, "quote": quote, "kind": kind})
        invested += quote

    for c in candles:
        if c.ts >= due:
            buy(c.open, amount, c.ts, "scheduled")
            while due <= c.ts:
                due += every
            dip_done = False
        avg = average_cost(lots)
        if dip and avg and not dip_done and c.low <= avg * (1 - dip / 100):
            buy(min(c.open, avg * (1 - dip / 100)), dip_amount, c.ts, "dip")
            dip_done = True
            avg = average_cost(lots)
        if tp and avg and c.high >= avg * (1 + tp / 100):
            qty = sum(lot["qty"] for lot in lots)
            fill = max(c.open, avg * (1 + tp / 100)) * (1 - slip)
            got = qty * fill * (1 - fee_rate)
            cost = sum(lot["cost"] for lot in lots)
            proceeds += got
            realized += got - cost
            sells.append({"ts": c.ts, "price": fill, "qty": qty, "quote": got, "pnl": got - cost})
            lots.clear()
        value = sum(lot["qty"] for lot in lots) * c.close
        if invested > 0:
            ratio = (value + proceeds) / invested
            peak_ratio = max(peak_ratio, ratio)
            max_dd = max(max_dd, (peak_ratio - ratio) / peak_ratio * 100)
    last = candles[-1].close
    qty = sum(lot["qty"] for lot in lots)
    value = qty * last
    profit = value + proceeds - invested
    first = candles[0].open * (1 + slip)
    ls_qty = invested / (first * (1 + fee_rate)) if invested else 0.0
    ls_return = (ls_qty * last / invested - 1) * 100 if invested else 0.0
    ret = profit / invested * 100 if invested else 0.0
    all_qty = sum(b["qty"] for b in buys)
    return {
        "engine": "dca",
        "buys": buys,
        "sells": sells,
        "n_buys": len(buys),
        "n_dip_buys": sum(1 for b in buys if b["kind"] == "dip"),
        "invested": round(invested, 8),
        "qty_held": qty,
        "average_cost": average_cost(lots),
        "average_cost_all_buys": invested / all_qty if all_qty else None,
        "value": round(value, 8),
        "realized_profit": round(realized, 8),
        "profit": round(profit, 8),
        "return_pct": round(ret, 4),
        "lump_sum_return_pct": round(ls_return, 4),
        "vs_lump_sum_pct": round(ret - ls_return, 4),
        "max_drawdown_pct": round(max_dd, 4),
        "note": DCA_NOTE,
    }


def backtest(
    name: str,
    candles: Sequence[Candle],
    params: Mapping[str, Any],
    fee_rate: float = 0.001,
    slip_pct: float = 0.05,
) -> dict[str, Any]:
    """``"grid"`` or ``"dca"``: the engine's backtest."""
    if name == "grid":
        return grid_backtest(candles, params, fee_rate, slip_pct)
    if name == "dca":
        return dca_backtest(candles, params, fee_rate, slip_pct)
    raise ValueError(f"unknown engine {name!r}: choose grid or dca")


# ---------------------------------------------------------------------- live bots
class _EngineBot:
    """What the grid and DCA bots share: files, the heartbeat, requests and the run loop."""

    engine = "base"

    def __init__(self, settings: Any, gateway: Any, *, sleep: Any = time.sleep) -> None:
        self.s = settings
        self.gw = gateway
        self.sleep = sleep
        if len(settings.pairs) != 1:
            raise SystemExit(f"a {self.engine} bot trades exactly one pair")
        self.pair = settings.pairs[0]
        self.base, self.quote = base_of(self.pair), self.pair.split("/")[1].split(":")[0].upper()
        self.params: dict[str, Any] = dict((settings.lab or {}).get(self.engine) or {})
        self.cfg = settings.strategy_config()
        self.dir = Path(settings.state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state: dict[str, Any] = {}
        self.trades: list[Trade] = []
        self.stop_requested = False
        self.settings_path: Path | None = None
        if hasattr(gateway, "on_wait"):  # a wallet ticket waits for you: stay "running"
            gateway.on_wait = lambda: self._heartbeat("running")

    # -------------------------------------------------------------- files
    @property
    def journal_path(self) -> Path:
        return self.dir / "journal.csv"

    @property
    def state_path(self) -> Path:
        return self.dir / f"{self.engine}_state.json"

    def _save(self) -> None:
        from .live_bot import save_journal

        if isinstance(self.gw, PaperBroker):
            self.state["paper_balances"] = self.gw.state()
        atomic_write_text(self.state_path, json.dumps(self.state, indent=1))
        save_journal(self.trades, self.journal_path)

    def _heartbeat(self, status: str, error: str | None = None) -> None:
        from .live_bot import write_heartbeat

        write_heartbeat(self.dir, self.s, status, self.gw.now_ms(), error)

    def _fatal(self, message: str) -> None:
        for last in (self._save, lambda: self._heartbeat("error", message)):
            try:
                last()
            except Exception as exc:
                log.warning("could not write the final state: %s", exc)

    # -------------------------------------------------------------- start
    def _initial_state(self, equity: float) -> dict[str, Any]:
        raise NotImplementedError

    def start(self) -> None:
        lock = read_lock(self.dir)
        if lock is not None:
            raise SystemExit(
                f"trading is halted ({lock.get('reason')}); delete trading_halted.lock by hand"
            )
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
            self.state["params"] = self.params or self.state.get("params") or {}
            self.params = dict(self.state["params"])
        else:
            eq = float(self.s.starting_equity or 10_000.0)
            if self.s.mode != "paper":
                eq = float(self.gw.free(self.quote))
            self.state = {
                "engine": self.engine,
                "pair": self.pair,
                "mode": self.s.mode,
                "exchange": self.s.exchange,
                "starting_equity": eq,
                "params": self.params,
                "next_id": 1,
                **self._initial_state(eq),
            }
        self._check_params()
        if isinstance(self.gw, PaperBroker):
            self.gw.balances = dict(
                self.state.get("paper_balances") or {self.quote: self.state["starting_equity"]}
            )
        self.trades = read_journal(self.journal_path) if self.journal_path.exists() else []
        self._save()
        log.info(
            "%s bot on %s %s (%s) started, equity %.2f",
            self.engine,
            self.pair,
            self.s.exchange,
            self.s.mode,
            self.equity(),
        )

    def _check_params(self) -> None:
        raise NotImplementedError

    # -------------------------------------------------------------- accounting
    def equity(self) -> float:
        return float(self.state["starting_equity"]) + sum(
            t.pnl or 0.0 for t in self.trades if t.is_closed
        )

    def _trade(self, tid: int) -> Trade:
        return next(t for t in self.trades if t.trade_id == tid)

    def _fee(self, fill: Any) -> float:
        return (
            fill.fee_quote
            if fill.fee_quote is not None
            else self.cfg.fee_rate * fill.qty * fill.price
        )

    def _open_trade(self, fill: Any, stop: float, target: float, risk: float, note: str) -> Trade:
        tid = int(self.state["next_id"])
        self.state["next_id"] = tid + 1
        eq = self.equity()
        t = Trade(
            tid,
            self.pair,
            self.engine,
            fill.ts,
            fill.ts,
            fill.price,
            stop,
            target,
            fill.qty,
            risk,
            risk / eq * 100 if eq > 0 else 0.0,
            self.engine,
            fees=self._fee(fill),
            notes=note,
        )
        self.trades.append(t)
        return t

    def _close_trade(
        self, t: Trade, qty: float, price: float, fee: float, ts: int, reason: str, why: str
    ) -> None:
        t.exit_ts, t.exit_price, t.exit_reason = ts, price, reason
        t.fees += fee
        t.pnl = qty * price - t.qty * t.entry_price - t.fees
        t.r_multiple = t.pnl / t.risk_amount if t.risk_amount else None
        t.notes = f"{t.notes} | exit: {why}"

    def _sellable(self, qty: float, price: float) -> float:
        """``qty`` rounded to the exchange's step and capped at what is held; 0 if below the
        exchange minimum."""
        q = self.gw.round_qty(self.pair, min(qty, self.gw.free(self.base)))
        mn_amt, mn_cost = self.gw.min_order(self.pair)
        return q if q > 0 and q >= mn_amt and q * price >= mn_cost else 0.0

    # -------------------------------------------------------------- loop
    def run(self, max_steps: int | None = None) -> None:
        from .live_bot import LoopGuard

        guard = LoopGuard(self.s.exchange, self.s.poll_seconds, self._fatal)
        steps = 0
        while not self.stop_requested and (max_steps is None or steps < max_steps):
            try:
                self.step()
                delay = guard.ok()
            except Exception as exc:  # outages and locked files must not kill the bot
                delay = guard.failed(exc)
            steps += 1
            if not self.stop_requested and (max_steps is None or steps < max_steps):
                self.sleep(delay)
        self._save()
        self._heartbeat("stopped")

    def _requests(self) -> bool:
        """Handle stop / close requests; True when new buys are paused."""
        from .live_bot import pop_requests, read_control

        for _, req in pop_requests(self.dir):
            if req.get("action") == "stop":
                self.stop_requested = True
            elif req.get("action") == "close":
                self.flatten("closed by the operator")
        return bool(read_control(self.dir).get("entries_paused"))

    def step(self) -> None:
        raise NotImplementedError

    def flatten(self, why: str = "flatten", reason: str = EXIT_END) -> list[Trade]:
        raise NotImplementedError


class GridBot(_EngineBot):
    """The virtual grid of ``grid_backtest``, live: each step compares the price with the
    previous step's and buys / sells the units of every level crossed in between."""

    engine = "grid"

    def _initial_state(self, equity: float) -> dict[str, Any]:
        return {"units": {}, "last_price": None, "fills": 0, "stopped": None}

    def _check_params(self) -> None:
        self.levels = grid_levels(self.params)
        budget = float(self.params.get("budget", 1000.0))
        self.unit_quote = budget / (len(self.levels) - 1)
        self.state["levels"] = self.levels

    def step(self) -> None:
        paused = self._requests()
        bid, ask = self.gw.bid(self.pair), self.gw.ask(self.pair)
        price = (bid + ask) / 2
        st = self.state
        if st.get("stopped") or self.stop_requested:
            self._heartbeat("running")
            return
        prev = st.get("last_price")
        p = self.params
        if p.get("stop_below") is not None and bid <= float(p["stop_below"]):
            self.flatten(f"price {bid:.8g} fell to the stop {float(p['stop_below']):g}", EXIT_SL)
            st["stopped"] = "stop_below"
        elif p.get("take_above") is not None and bid >= float(p["take_above"]):
            self.flatten(f"price {bid:.8g} reached {float(p['take_above']):g}", EXIT_TP)
            st["stopped"] = "take_above"
        elif prev is not None and price < prev and not paused:
            for k in range(len(self.levels) - 2, -1, -1):
                if prev > self.levels[k] >= price and str(k) not in st["units"]:
                    self._buy_unit(k, ask)
        elif prev is not None and price > prev:  # each unit keeps its own sell price
            for key in sorted(st["units"], key=int):
                u = st["units"][key]
                if prev < u["sell_price"] <= price:
                    self._sell_unit(int(key), bid, EXIT_TP, f"grid level {u['sell_price']:.8g}")
        st["last_price"] = price
        self._save()
        self._heartbeat("running")

    def _buy_unit(self, k: int, ask: float) -> None:
        fee = self.cfg.fee_rate
        est = ask * (1 + self.cfg.slippage_pct / 100)
        qty = self.gw.round_qty(self.pair, self.unit_quote / (est * (1 + fee)))
        mn_amt, mn_cost = self.gw.min_order(self.pair)
        if qty <= 0 or qty < mn_amt or qty * est < mn_cost:
            log.info("SKIP grid %s level %d: a unit is below the exchange minimum", self.pair, k)
            return
        if qty * est * (1 + fee) > self.gw.free(self.quote):
            log.info("SKIP grid %s level %d: not enough %s", self.pair, k, self.quote)
            return
        fill = self.gw.buy(self.pair, qty)
        if fill.qty <= 0:
            return
        sell = self.levels[k + 1]
        stop = float(self.params.get("stop_below") or 0.0)
        risk = fill.qty * (fill.price - stop) if stop else fill.qty * fill.price
        t = self._open_trade(
            fill,
            stop,
            sell,
            max(risk, 1e-12),
            f"grid buy at level {k} ({self.levels[k]:.8g}); sells at {sell:.8g}",
        )
        self.state["units"][str(k)] = {"id": t.trade_id, "qty": fill.qty, "sell_price": sell}
        self.state["fills"] = int(self.state.get("fills", 0)) + 1
        log.info("GRID BUY %s level %d: %.8g at %.8g", self.pair, k, fill.qty, fill.price)
        self._save()

    def _sell_unit(self, k: int, bid: float, reason: str, why: str) -> None:
        u = self.state["units"].get(str(k))
        if u is None:
            return
        qty = self._sellable(u["qty"], bid)
        t = self._trade(int(u["id"]))
        if qty <= 0:
            log.error(
                "grid %s level %d: %.8g left is below the exchange minimum", self.pair, k, u["qty"]
            )
            return
        fill = self.gw.sell(self.pair, qty)
        self._close_trade(t, fill.qty, fill.price, self._fee(fill), fill.ts, reason, why)
        del self.state["units"][str(k)]
        self.state["fills"] = int(self.state.get("fills", 0)) + 1
        self.state["realized"] = float(self.state.get("realized", 0.0)) + (t.pnl or 0.0)
        log.info("GRID SELL %s level %d at %.8g: pnl %+.4f", self.pair, k, fill.price, t.pnl or 0)
        self._save()

    def flatten(self, why: str = "flatten", reason: str = EXIT_END) -> list[Trade]:
        bid = self.gw.bid(self.pair)
        closed = []
        for key in sorted(self.state.get("units", {}), key=int):
            u = self.state["units"][key]
            self._sell_unit(int(key), bid, reason, why)
            if key not in self.state["units"]:
                closed.append(self._trade(int(u["id"])))
        return closed


class DcaBot(_EngineBot):
    """``dca_backtest`` live: a scheduled buy every period (the first one at start), one
    optional dip buy per period, an optional take-profit that sells every lot."""

    engine = "dca"

    def _initial_state(self, equity: float) -> dict[str, Any]:
        return {"next_buy_ms": None, "lots": [], "dip_period": None, "invested": 0.0}

    def _check_params(self) -> None:
        self.amount, self.every, self.dip, self.dip_amount, self.tp = _dca_params(self.params)

    def _lots(self) -> list[Trade]:
        return [self._trade(int(i)) for i in self.state["lots"]]

    def average_cost(self) -> float | None:
        return average_cost(
            [{"qty": t.qty, "cost": t.qty * t.entry_price + t.fees} for t in self._lots()]
        )

    def step(self) -> None:
        paused = self._requests()
        if self.stop_requested:
            self._heartbeat("running")
            return
        st = self.state
        now = self.gw.now_ms()
        bid, ask = self.gw.bid(self.pair), self.gw.ask(self.pair)
        if st.get("next_buy_ms") is None:
            st["next_buy_ms"] = now
        if now >= st["next_buy_ms"]:
            if paused:
                log.info("DCA %s: buy skipped, entries are paused", self.pair)
            else:
                self._buy(self.amount, ask, "scheduled")
            while st["next_buy_ms"] <= now:
                st["next_buy_ms"] += self.every
            st["dip_period"] = None
        avg = self.average_cost()
        if (
            self.dip
            and avg
            and not paused
            and st.get("dip_period") != st["next_buy_ms"]
            and bid <= avg * (1 - self.dip / 100)
        ):
            self._buy(self.dip_amount, ask, "dip")
            st["dip_period"] = st["next_buy_ms"]
            avg = self.average_cost()
        if self.tp and avg and bid >= avg * (1 + self.tp / 100):
            self.flatten(f"take profit: {self.tp:g}% above the average cost {avg:.8g}", EXIT_TP)
        self._save()
        self._heartbeat("running")

    def _buy(self, quote: float, ask: float, kind: str) -> None:
        fee = self.cfg.fee_rate
        est = ask * (1 + self.cfg.slippage_pct / 100)
        cash = self.gw.free(self.quote)
        quote = min(quote, cash / (1 + fee) * 0.999)
        qty = self.gw.round_qty(self.pair, quote / (est * (1 + fee)))
        mn_amt, mn_cost = self.gw.min_order(self.pair)
        if qty <= 0 or qty < mn_amt or qty * est < mn_cost:
            log.info(
                "SKIP DCA %s %s buy: below the exchange minimum or no cash left", self.pair, kind
            )
            return
        fill = self.gw.buy(self.pair, qty)
        if fill.qty <= 0:
            return
        cost = fill.qty * fill.price + self._fee(fill)
        target = fill.price * (1 + self.tp / 100) if self.tp else 0.0
        t = self._open_trade(fill, 0.0, target, cost, f"DCA {kind} buy of {cost:.2f} {self.quote}")
        self.state["lots"].append(t.trade_id)
        self.state["invested"] = float(self.state.get("invested", 0.0)) + cost
        log.info("DCA %s %s: %.8g at %.8g", kind.upper(), self.pair, fill.qty, fill.price)
        self._save()

    def flatten(self, why: str = "flatten", reason: str = EXIT_END) -> list[Trade]:
        lots = self._lots()
        if not lots:
            return []
        bid = self.gw.bid(self.pair)
        held = sum(t.qty for t in lots)
        qty = self._sellable(held, bid)
        if qty <= 0:
            log.error("DCA %s: %.8g held is below the exchange minimum; not sold", self.pair, held)
            return []
        fill = self.gw.sell(self.pair, qty)
        fee = self._fee(fill)
        for t in lots:  # the sale is shared by quantity
            share = t.qty / held
            self._close_trade(t, fill.qty * share, fill.price, fee * share, fill.ts, reason, why)
        self.state["lots"] = []
        self.state["realized"] = float(self.state.get("realized", 0.0)) + sum(
            t.pnl or 0 for t in lots
        )
        log.info("DCA SELL %s: %.8g at %.8g (%s)", self.pair, fill.qty, fill.price, why)
        self._save()
        return lots


ENGINES: dict[str, type[_EngineBot]] = {"grid": GridBot, "dca": DcaBot}


def make_bot(settings: Any, gateway: Any, *, sleep: Any = time.sleep) -> _EngineBot:
    """The live bot for ``settings.engine`` ("grid" | "dca")."""
    if settings.engine not in ENGINES:
        raise SystemExit(f"unknown engine {settings.engine!r}: choose one of {sorted(ENGINES)}")
    return ENGINES[settings.engine](settings, gateway, sleep=sleep)
