"""Run a strategy-lab strategy (strategies.py) as a bot: ``"engine": "lab"`` in the settings.

::

    {"engine": "lab", "mode": "paper", "pairs": ["BTC/USDT"],
     "lab": {"strategy": "nnfx", "params": {}}, ...}

One pair per lab bot. Every second it reads the bid / ask, manages the open position (stop,
take-profit, the partial exit and the trailing stop), fills a pending buy-stop when the ask
reaches it, and at each close of the strategy's timeframe (4H, or 15m for the Sneaky Pivot)
asks the strategy for a new signal. Sizing: 1% of equity at risk (the pair's cap, lowered by
the capital guard on a bad day). The capital guard, the kill switch lock, pause / stop /
close from the dashboard and the journal format are the same as the main bot's, so the
dashboard, the terminal and the fleet work unchanged.

Real money needs incubation: a testnet or live lab bot refuses to start until the strategy
was approved on the dashboard and has paper-traded for at least 30 days (lab.incubation).
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from .capital import CapitalGuard, CapitalSettings, read_lock, write_lock
from .data import save_candles_csv, timeframe_to_ms
from .journal import ms_to_iso, read_journal, write_journal
from .live_exchange import PaperBroker
from .models import EXIT_END, EXIT_SL, EXIT_TP, Trade, base_of
from .sizing import max_risk_pct
from .strategies import STRATEGIES


log = logging.getLogger("trendbot.lab_bot")


class LabBot:
    def __init__(self, settings: Any, gateway: Any, *, sleep: Any = time.sleep) -> None:
        self.s = settings
        self.gw = gateway
        self.sleep = sleep
        lab = dict(settings.lab or {})
        name = lab.get("strategy", "nnfx")
        if name not in STRATEGIES:
            raise SystemExit(f"unknown lab strategy {name!r}: choose one of {sorted(STRATEGIES)}")
        if len(settings.pairs) != 1:
            raise SystemExit("a lab bot trades exactly one pair")
        self.pair = settings.pairs[0]
        self.strategy = STRATEGIES[name](**(lab.get("params") or {}))
        self.key = f"{name}:{self.pair}"
        self.tf = self.strategy.timeframe
        self.tf_ms = timeframe_to_ms(self.tf)
        self.cfg = settings.strategy_config()
        self.dir = Path(settings.state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.state: dict[str, Any] = {}
        self.trades: list[Trade] = []
        self.stop_requested = False
        self.capital: CapitalGuard | None = None
        self.settings_path: Path | None = None

    # -------------------------------------------------------------- files
    @property
    def journal_path(self) -> Path:
        return self.dir / "journal.csv"

    def _save(self) -> None:
        tmp = self.dir / "state.json.tmp"
        tmp.write_text(json.dumps(self.state, indent=1), encoding="utf-8")
        tmp.replace(self.dir / "state.json")
        write_journal(self.trades, self.journal_path)

    def _heartbeat(self, status: str) -> None:
        beat = {
            "pid": os.getpid(),
            "ts": self.gw.now_ms(),
            "wall_ts": int(time.time() * 1000),
            "status": status,
            "mode": self.s.mode,
            "exchange": self.s.exchange,
        }
        tmp = self.dir / "heartbeat.json.tmp"
        tmp.write_text(json.dumps(beat), encoding="utf-8")
        tmp.replace(self.dir / "heartbeat.json")

    # -------------------------------------------------------------- start
    def start(self) -> None:
        from .lab import incubation, mark_paper_start

        lock = read_lock(self.dir)
        if lock is not None:
            raise SystemExit(
                f"trading is halted ({lock.get('reason')}); delete trading_halted.lock by hand"
            )
        ok, why = incubation(self.dir, self.key, self.s.mode)
        if not ok:
            raise SystemExit(why)
        p = self.dir / "state.json"
        if p.exists():
            self.state = json.loads(p.read_text(encoding="utf-8"))
        else:
            eq = float(self.s.starting_equity or 10_000.0)
            if self.s.mode != "paper":
                eq = float(self.gw.free(self.pair.split("/")[1]))
            self.state = {
                "engine": "lab",
                "strategy": self.key,
                "mode": self.s.mode,
                "exchange": self.s.exchange,
                "starting_equity": eq,
                "open": None,
                "pending": [],
                "seen": [],
                "last_bar": None,
                "next_id": 1,
            }
        if isinstance(self.gw, PaperBroker):
            self.gw.balances = dict(
                self.state.get("paper_balances")
                or {self.pair.split("/")[1]: self.state["starting_equity"]}
            )
        self.trades = read_journal(self.journal_path) if self.journal_path.exists() else []
        self.capital = CapitalGuard(
            CapitalSettings(**self.s.capital), self.state.setdefault("capital", {})
        )
        if self.s.mode == "paper":
            mark_paper_start(self.key, self.gw.now_ms())
        self._save()
        log.info(
            "lab bot %s on %s (%s) started, equity %.2f",
            self.key,
            self.s.exchange,
            self.s.mode,
            self.equity(),
        )

    # -------------------------------------------------------------- accounting
    def equity(self) -> float:
        return float(self.state["starting_equity"]) + sum(
            t.pnl or 0.0 for t in self.trades if t.is_closed
        )

    def mtm(self, bid: float) -> float:
        o = self.state.get("open")
        if not o:
            return self.equity()
        left = o["qty"] - sum(q for q, _ in o["exits"])
        realized = sum(q * (px - o["entry"]) for q, px in o["exits"])
        return self.equity() + realized + left * (bid - o["entry"]) - self.cfg.fee_rate * left * bid

    # -------------------------------------------------------------- loop
    def run(self, max_steps: int | None = None) -> None:
        steps = 0
        while not self.stop_requested and (max_steps is None or steps < max_steps):
            self.step()
            steps += 1
            if not self.stop_requested and (max_steps is None or steps < max_steps):
                self.sleep(self.s.poll_seconds)
        self._save()
        self._heartbeat("stopped")

    def step(self) -> None:
        from .live_bot import pop_requests, read_control

        for _, req in pop_requests(self.dir):
            if req.get("action") == "stop":
                self.stop_requested = True
            elif req.get("action") == "close" and self.state.get("open"):
                self._exit_all(self.gw.bid(self.pair), EXIT_END, "closed by the operator")
        bid, ask = self.gw.bid(self.pair), self.gw.ask(self.pair)
        now = self.gw.now_ms()
        v = self.capital.evaluate(now, self.mtm(bid), [t for t in self.trades if t.is_closed])
        if v.kill:
            self._exit_all(bid, EXIT_END, v.kill)
            write_lock(
                self.dir,
                v.kill,
                now,
                self.mtm(bid),
                float(self.state["capital"].get("peak_equity") or 0),
            )
            self.stop_requested = True
        elif v.flatten and self.state.get("open"):
            self._exit_all(bid, EXIT_END, v.flatten)
        self.manage(bid)
        blocked = v.block or (
            read_control(self.dir).get("entries_paused") and "entries paused by the operator"
        )
        self.fill_pending(ask, now, None if blocked else v.risk_scale)
        self.on_bar(now, None if blocked else v.risk_scale)
        self._heartbeat("running")

    # -------------------------------------------------------------- signals
    def on_bar(self, now: int, risk_scale: float | None) -> None:
        last_closed = now // self.tf_ms * self.tf_ms - self.tf_ms
        if self.state.get("last_bar") is not None and self.state["last_bar"] >= last_closed:
            return
        cs = self.gw.closed_candles(self.pair, 1500) if self.tf == "4h" else self._candles_tf(1500)
        if not cs or cs[-1].ts != last_closed:
            return
        self.state["last_bar"] = last_closed
        save_candles_csv(cs, self.dir / "candles" / f"{self.pair.replace('/', '_')}-{self.tf}.csv")
        for s in self.strategy.signals(cs):
            if s.i != len(cs) - 1 or s.ts in self.state["seen"]:
                continue
            self.state["seen"] = (self.state["seen"] + [s.ts])[-200:]
            log.info("SIGNAL %s %s: %s", self.key, ms_to_iso(s.ts), s.reason)
            if risk_scale is None or self.state.get("open"):
                continue
            order = {
                "ts": s.ts,
                "entry": s.entry,
                "price": s.entry_price,
                "stop": s.stop,
                "stop_atr": s.stop_atr,
                "target": s.target,
                "rr": s.rr,
                "partial": s.partial,
                "trail_atr": s.trail_atr,
                "reason": s.reason,
                "expires": last_closed + self.tf_ms * (s.expiry_bars + 1),
            }
            if s.entry == "next_open":
                self._buy(order, self.gw.ask(self.pair), risk_scale)
            else:
                self.state["pending"] = [order]
        self._save()

    def _candles_tf(self, n: int) -> list[Any]:
        from .fetch_data import fetch_ohlcv

        ex = getattr(self.gw, "ex", None) or getattr(getattr(self.gw, "market", None), "ex", None)
        since = self.gw.now_ms() - (n + 2) * self.tf_ms
        return fetch_ohlcv(ex, self.pair, self.tf, since)[-n:]

    def fill_pending(self, ask: float, now: int, risk_scale: float | None) -> None:
        keep = []
        for o in self.state.get("pending") or []:
            if now >= o["expires"]:
                continue
            if risk_scale is not None and not self.state.get("open") and ask >= o["price"]:
                self._buy(o, ask, risk_scale)
                continue
            keep.append(o)
        self.state["pending"] = keep

    # -------------------------------------------------------------- orders
    def _buy(self, o: dict[str, Any], ask: float, risk_scale: float) -> None:
        fee, slip = self.cfg.fee_rate, self.cfg.slippage_pct / 100
        est = ask * (1 + slip)
        stop = est - o["stop_atr"] if o.get("stop_atr") else o["stop"]
        target = o["target"] if o.get("target") is not None else est + o["rr"] * (est - stop)
        if est <= stop or target - est < 2.0 * (est - stop) * 0.999:
            log.info("SKIP %s: the price moved and no 2R room is left", self.key)
            return
        eq = self.equity()
        risk_pct = min(1.0, max_risk_pct(self.pair, self.cfg)) * risk_scale
        per_unit = (est - stop) + fee * (est + stop)
        cash = self.gw.free(self.pair.split("/")[1])
        qty = self.gw.round_qty(
            self.pair, min(eq * risk_pct / 100 / per_unit, cash / (est * (1 + fee)))
        )
        mn_amt, mn_cost = self.gw.min_order(self.pair)
        if qty <= 0 or qty < mn_amt or qty * est < mn_cost:
            log.info("SKIP %s: size below the exchange minimum", self.key)
            return
        fill = self.gw.buy(self.pair, qty)
        if fill.qty <= 0:
            return
        if o.get("stop_atr"):  # ATR stops and the 2R target follow the actual fill
            stop = fill.price - o["stop_atr"]
            target = (
                o["target"]
                if o.get("target") is not None
                else fill.price + o["rr"] * (fill.price - stop)
            )
        tid = int(self.state["next_id"])
        self.state["next_id"] = tid + 1
        risk_amt = fill.qty * ((fill.price - stop) + fee * (fill.price + stop))
        self.state["open"] = {
            "id": tid,
            "signal_ts": o["ts"],
            "entry_ts": fill.ts,
            "entry": fill.price,
            "qty": fill.qty,
            "stop": stop,
            "target": target,
            "partial": o["partial"],
            "trail_atr": o["trail_atr"],
            "best": fill.price,
            "trailing": False,
            "exits": [],
            "risk": risk_amt,
            "risk_pct": risk_amt / eq * 100,
            "fees": fill.fee_quote if fill.fee_quote is not None else fee * fill.qty * fill.price,
            "reason": o["reason"],
        }
        self.state["pending"] = []
        log.info(
            "ENTRY %s #%d qty %.8g at %.8g, stop %.8g, target %.8g",
            self.key,
            tid,
            fill.qty,
            fill.price,
            stop,
            target,
        )
        self._save()

    def manage(self, bid: float) -> None:
        o = self.state.get("open")
        if not o:
            return
        if bid <= o["stop"]:
            self._exit_all(bid, EXIT_SL, "stop" if not o["trailing"] else "trailing stop")
            return
        left = o["qty"] - sum(q for q, _ in o["exits"])
        if not o["exits"] and o["target"] is not None and bid >= o["target"]:
            if o["partial"] and o["trail_atr"]:
                part = self.gw.round_qty(self.pair, left * o["partial"])
                fill = self.gw.sell(self.pair, part)
                o["exits"].append([fill.qty, fill.price])
                o["fees"] += (
                    fill.fee_quote
                    if fill.fee_quote is not None
                    else self.cfg.fee_rate * fill.qty * fill.price
                )
                o["stop"], o["trailing"] = max(o["stop"], o["entry"]), True
                log.info(
                    "PARTIAL %s #%d: %.8g sold at %.8g, the rest trails",
                    self.key,
                    o["id"],
                    fill.qty,
                    fill.price,
                )
                self._save()
            else:
                self._exit_all(bid, EXIT_TP, "target")
                return
        if o["trailing"] and o["trail_atr"]:
            o["best"] = max(o["best"], bid)
            o["stop"] = max(o["stop"], o["best"] - o["trail_atr"])

    def _exit_all(self, bid: float, reason: str, why: str) -> None:
        o = self.state.get("open")
        if not o:
            return
        left = self.gw.round_qty(
            self.pair,
            min(o["qty"] - sum(q for q, _ in o["exits"]), self.gw.free(base_of(self.pair))),
        )
        if left > 0:
            fill = self.gw.sell(self.pair, left)
            o["exits"].append([fill.qty, fill.price])
            o["fees"] += (
                fill.fee_quote
                if fill.fee_quote is not None
                else self.cfg.fee_rate * fill.qty * fill.price
            )
        sold = sum(q for q, _ in o["exits"]) or o["qty"]
        exit_px = sum(q * px for q, px in o["exits"]) / sold if o["exits"] else bid
        pnl = sum(q * (px - o["entry"]) for q, px in o["exits"]) - o["fees"]
        if reason == EXIT_SL and pnl > 0:
            reason = EXIT_TP if o["exits"] and len(o["exits"]) > 1 else reason
        t = Trade(
            o["id"],
            self.pair,
            self.strategy.name,
            o["signal_ts"],
            o["entry_ts"],
            o["entry"],
            o["stop"],
            o["target"] or 0.0,
            o["qty"],
            o["risk"],
            o["risk_pct"],
            self.strategy.name,
            self.gw.now_ms(),
            exit_px,
            reason,
            o["fees"],
            pnl,
            pnl / o["risk"] if o["risk"] else None,
            {},
            None,
            f"{o['reason']} | exit: {why}",
        )
        self.trades.append(t)
        self.state["open"] = None
        if isinstance(self.gw, PaperBroker):
            self.state["paper_balances"] = self.gw.state()
        log.info(
            "EXIT %s #%d %s (%s) at %.8g: %+.2fR, pnl %+.2f",
            self.key,
            t.trade_id,
            reason,
            why,
            exit_px,
            t.r_multiple or 0,
            pnl,
        )
        self._save()
