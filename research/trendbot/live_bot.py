"""trendbot live trading bot: runs the strategy against Binance / Coinbase through ccxt.

Every entry decision goes through ``gatekeeper.LiveSession`` (the same code path as the
backtester), so rules R1-R9 apply exactly as backtested. Stops and take-profits are
software-managed: the bot polls the bid every ``poll_seconds`` and sends a market sell when
the stop or the target is crossed. Exits are never paused by any rule.

Modes:
    paper    live public market data, simulated fills and balances (no API keys)
    testnet  exchange sandbox (Binance spot testnet) with testnet API keys
    live     real orders with real funds

Usage::

    export TRENDBOT_API_KEY=... TRENDBOT_API_SECRET=...     # testnet / live only
    python3 -m research.trendbot.live_bot run --settings bot.json
    python3 -m research.trendbot.live_bot status --settings bot.json
    python3 -m research.trendbot.live_bot flatten --settings bot.json

State lives in ``state_dir`` and survives restarts:
    journal.csv     every trade (open and closed), the CSV trade journal
    decisions.csv   every evaluated signal candle (allowed / denied, rule, reason)
    state.json      starting equity, R6 reservations, entry fees, last candle per pair,
                    paper balances
    candles/        the 4H candles the bot evaluated (for audits / testnet evidence)
    bot.log         log
Kill the process at any time (SIGINT / SIGTERM stop after the current step); on restart the
open positions are rebuilt from the journal and managed again.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import signal
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from typing import Any

from .adoption_evidence import read_decisions_log, write_decisions_log
from .adoption_record import config_from_overrides
from .allocation import AllocationSettings, RegimeAllocator, exposure_room
from .capital import CapitalGuard, CapitalSettings, fee_warning, read_lock, write_lock
from .chantisimo import BrainSettings, Chantisimo
from .config import StrategyConfig
from .data import load_candles_csv, save_candles_csv, timeframe_to_ms
from .gatekeeper import DecisionRecord, EntryDecision, LiveSession
from .journal import ms_to_iso, read_journal, write_journal
from .journal_rules import audit, render_markdown
from .layers import LayerBook, MarketView
from .learning import LearningBook, LearningSettings, entry_context, expected_outcome
from .live_exchange import CcxtGateway, Fill, OrderError, PaperBroker, quote_of
from .models import EXIT_END, EXIT_SL, EXIT_TP, Candle, Trade, base_of
from .news import NewsCalendar, load_events
from .retrain import due


log = logging.getLogger("trendbot.bot")

MODES = ("paper", "testnet", "live")
STATE_VERSION = 1


@dataclass
class BotSettings:
    exchange: str = "binance"
    mode: str = "paper"
    pairs: tuple[str, ...] = ("BTC/USDT", "ETH/USDT", "BNB/USDT")
    state_dir: str = "trendbot_state"
    events: str | None = None  # news calendar CSV (reloaded every candle close)
    starting_equity: float | None = None  # paper: default 10000; testnet/live: quote balance
    poll_seconds: float = 1.0  # loop interval: stop / target checks and live prices
    close_delay_seconds: float = 5.0  # wait after a 4H close before fetching the candle
    history_candles: int = 1500  # deep history fetched once (EMA200 converges; then cached)
    entry_buffer_pct: float = 0.3  # pre-size for a fill this much worse than the ask
    strategy: dict[str, Any] = field(default_factory=dict)  # StrategyConfig overrides
    adoption_record: str | None = None  # if set, live mode requires this record to pass LIVE
    learning: dict[str, Any] = field(default_factory=dict)  # learning.LearningSettings fields
    # Signal layers (layers.py): {"enabled": [...], "params": {name: {...}}, "validation": {...},
    # "schedule": {"retrain_weekday": 6, "retrain_hour_utc": 1, "collect_every_minutes": 60},
    # "auto_jobs": true}
    layers: dict[str, Any] = field(default_factory=dict)
    sentiment: dict[str, Any] = field(default_factory=dict)  # sentiment.collect settings
    orderflow: dict[str, Any] = field(default_factory=dict)  # orderflow.collect settings
    dex: dict[str, Any] = field(default_factory=dict)  # {"enabled": false, ...} dex_scan settings
    engine: str = "trendbot"  # "trendbot" (the nine rules) or "lab" (a strategy-lab strategy)
    lab: dict[str, Any] = field(default_factory=dict)  # {"strategy": "nnfx", "params": {...}}
    brain: dict[str, Any] = field(default_factory=dict)  # chantisimo.BrainSettings fields
    # live feed for the dashboard: {"enabled": true, "price_seconds": 1, "candle_seconds": 5}
    live: dict[str, Any] = field(default_factory=dict)
    copy: dict[str, Any] = field(default_factory=dict)  # {"enabled": true}: follow traders.json
    capital: dict[str, Any] = field(default_factory=dict)  # capital.CapitalSettings fields
    # {"maker_first": true, "maker_wait_seconds": 15}: post-only limit entries (testnet/live)
    execution: dict[str, Any] = field(default_factory=dict)
    allocation: dict[str, Any] = field(default_factory=dict)  # allocation.AllocationSettings
    scanner: dict[str, Any] = field(default_factory=dict)  # scanner.ScannerSettings (watchlist)

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES} (got {self.mode!r})")
        self.pairs = tuple(self.pairs)
        if not self.pairs:
            raise ValueError("at least one pair is required")
        quotes = {quote_of(p) for p in self.pairs}
        if len(quotes) != 1:
            raise ValueError(f"all pairs must share one quote currency (got {sorted(quotes)})")
        if self.poll_seconds <= 0 or self.history_candles < 1200:
            raise ValueError(
                "poll_seconds must be > 0 and history_candles >= 1200 (EMA200 needs ~1000 "
                "candles after its seed to match the backtest)"
            )

    @classmethod
    def load(cls, path: str | Path, **overrides: Any) -> BotSettings:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        unknown = set(raw) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"unknown settings keys: {sorted(unknown)}")
        raw.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**raw)

    @property
    def quote(self) -> str:
        return quote_of(self.pairs[0])

    def strategy_config(self) -> StrategyConfig:
        overrides = {"exchange_id": self.exchange, **self.strategy}
        return config_from_overrides(overrides)


class TrendBot:
    """The trading loop. ``gateway`` is a CcxtGateway, a PaperBroker or a test double."""

    def __init__(
        self,
        settings: BotSettings,
        gateway: Any,
        *,
        cfg: StrategyConfig | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.s = settings
        self.gw = gateway
        self.cfg = cfg or settings.strategy_config()
        self.sleep = sleep
        self.tf_ms = timeframe_to_ms("4h")
        self.dir = Path(settings.state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.stop_requested = False
        self.session: LiveSession | None = None
        self.state: dict[str, Any] = {}
        self._decisions_saved = 0
        self._cache: dict[str, list[Candle]] = {}
        self.book = LearningBook(self.dir, self.cfg, LearningSettings(**settings.learning))
        lay = settings.layers
        self.layer_book = LayerBook(
            self.dir, self.cfg, enabled=lay.get("enabled"), params=lay.get("params")
        )
        self.brain = Chantisimo(self.dir, BrainSettings(**settings.brain), settings.mode)
        self._teacher_sig: tuple[Any, ...] = ()
        self._prices: dict[str, dict[str, Any]] = {}
        self._prices_wall = 0  # exchange ms of the last price fetch
        self._forming: dict[str, Any] = {}
        self._forming_wall = 0.0  # wall-clock seconds of the last forming-candle fetch
        self._copy_mtime: float | None = None
        self.capital_settings = CapitalSettings(**settings.capital)
        self.capital: CapitalGuard | None = None
        self._capital_block: str | None = None
        self._capital_sig: tuple[Any, ...] = ()
        self._live_ready = False
        self.allocator = RegimeAllocator(AllocationSettings(**settings.allocation))
        self._alloc_mtime: float | None = None
        self.settings_path: Path | None = None  # set by main(); needed to spawn jobs
        self._jobs: dict[str, Any] = {}

    # ------------------------------------------------------------------ paths
    @property
    def journal_path(self) -> Path:
        return self.dir / "journal.csv"

    @property
    def state_path(self) -> Path:
        return self.dir / "state.json"

    @property
    def decisions_path(self) -> Path:
        return self.dir / "decisions.csv"

    # ------------------------------------------------------------------ start / persist
    def start(self) -> None:
        """Restore state (or initialise it) and rebuild the session from the journal."""
        lock = read_lock(self.dir)
        if lock is not None:
            raise SystemExit(
                f"trading is halted ({lock.get('reason')}, {lock.get('halted_utc')}). Review what "
                f"happened, then delete {self.dir / 'trading_halted.lock'} by hand to restart."
            )
        if self.state_path.exists():
            self.state = json.loads(self.state_path.read_text(encoding="utf-8"))
            if (
                self.state.get("mode") != self.s.mode
                or self.state.get("exchange") != self.s.exchange
            ):
                raise SystemExit(
                    f"{self.state_path} belongs to {self.state.get('exchange')}/"
                    f"{self.state.get('mode')}; use another state_dir for {self.s.exchange}/"
                    f"{self.s.mode}"
                )
        else:
            self.state = self._initial_state()
        if isinstance(self.gw, PaperBroker):
            self.gw.balances = {k: float(v) for k, v in self.state["paper_balances"].items()}
        trades = read_journal(self.journal_path) if self.journal_path.exists() else []
        reserved = {int(k): float(v) for k, v in self.state.get("reservations", {}).items()}
        self.session = LiveSession(
            self.cfg,
            self._events(),
            trades,
            float(self.state["starting_equity"]),
            reserved_risk=reserved,
        )
        if self.decisions_path.exists():
            read_decisions_log(self.decisions_path)  # validate the existing log
        for pair in self.s.pairs:
            path = self._candle_path(pair)
            self._cache[pair] = load_candles_csv(path) if path.exists() else []
        self._reconcile()
        self.state.setdefault("trade_context", {})
        self.capital = CapitalGuard(self.capital_settings, self.state.setdefault("capital", {}))
        warn = fee_warning(self.cfg.fee_rate, self.capital_settings)
        self.state["capital"]["fee_warning"] = warn
        if warn:
            log.warning("FEES: %s", warn)
        self._relearn()
        self._save()
        log.info(
            "started %s/%s: equity %.2f %s, open %s",
            self.s.exchange,
            self.s.mode,
            self.session.equity,
            self.s.quote,
            sorted(self.session.open_positions),
        )

    def _initial_state(self) -> dict[str, Any]:
        if self.s.mode == "paper":
            equity = float(self.s.starting_equity or 10_000.0)
            balances = {self.s.quote: equity}
        else:
            equity = float(self.s.starting_equity or self.gw.total(self.s.quote))
            balances = {}
        if equity <= 0:
            raise SystemExit(f"no {self.s.quote} balance to trade with")
        return {
            "version": STATE_VERSION,
            "exchange": self.s.exchange,
            "mode": self.s.mode,
            "starting_equity": equity,
            "reservations": {},
            "entry_fees": {},
            "last_signal_ts": {},
            "paper_balances": balances,
            "unmanaged": [],
        }

    def _events(self) -> list[Any]:
        """The news calendar (R5); a missing file is an empty calendar (logged once)."""
        if not self.s.events:
            return []
        if not Path(self.s.events).exists():
            if not getattr(self, "_warned_events", False):
                log.warning("events file %s not found yet: no news blackouts", self.s.events)
                self._warned_events = True
            return []
        return load_events(self.s.events)

    def _save(self) -> None:
        sess = self._sess()
        write_journal(sess.journal_trades(), self.journal_path)
        self.state["reservations"] = {str(k): v for k, v in sess.reservations().items()}
        if isinstance(self.gw, PaperBroker):
            self.state["paper_balances"] = self.gw.state()
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(self.state_path)
        self.book.write(sess.journal_trades(), self.state.get("trade_context", {}))
        new = sess.decision_log[self._decisions_saved :]
        if new:
            append_decisions(new, self.decisions_path)
            self._decisions_saved = len(sess.decision_log)

    def _sess(self) -> LiveSession:
        if self.session is None:
            raise RuntimeError("call start() first")
        return self.session

    def _reconcile(self) -> None:
        """Warn when an open journal position is not backed by the exchange balance."""
        for pair, t in self._sess().open_positions.items():
            held = self.gw.free(base_of(pair))
            if held < t.qty * 0.98:
                log.warning(
                    "%s: journal holds %.8g but the account has %.8g free; the exit "
                    "will sell what is there",
                    pair,
                    t.qty,
                    held,
                )

    # ------------------------------------------------------------------ main loop
    def run(self, max_steps: int | None = None) -> None:
        steps = 0
        while not self.stop_requested and (max_steps is None or steps < max_steps):
            try:
                self.step()
            except OrderError as exc:
                log.error("order problem: %s", exc)
            steps += 1
            if not self.stop_requested and (max_steps is None or steps < max_steps):
                self.sleep(self.s.poll_seconds)
        self._save()
        self._heartbeat("stopped")
        log.info("stopped")

    def step(self) -> None:
        """One iteration: operator requests, exits, then entries on newly closed candles."""
        if self.layer_book.reload():
            log.info("signal layers reloaded: active %s", sorted(self.layer_book.layers))
            self._compose_filter()
        self.schedule_jobs()
        self.handle_requests()
        self.update_live()
        self.apply_capital()
        self._write_live()
        self.copy_trades()
        self.manage_exits()
        if not self.stop_requested:
            self.process_closed_candles()
        self._heartbeat("running")

    # ------------------------------------------------------------------ operator controls
    @property
    def entries_paused(self) -> bool:
        return bool(read_control(self.dir).get("entries_paused", False))

    def handle_requests(self) -> None:
        """Apply dashboard requests (stop, close, rule on/off); each file is consumed once."""
        for path, req in pop_requests(self.dir):
            action = req.get("action")
            log.info("operator request %s: %s", path.name, req)
            if action == "stop":
                self.stop_requested = True
            elif action == "close":
                self._close_request(str(req.get("pair", "ALL")))
            elif action == "layer":
                self.layer_book.set_override(str(req["name"]), req.get("status"))
                self._compose_filter()
            elif action == "rule":
                self.book.set_override(str(req["rule_id"]), req.get("status"))
                self._relearn()
                self._save()
            else:
                log.warning("unknown operator request %r ignored", action)

    def _close_request(self, pair: str) -> None:
        for p, t in sorted(self._sess().open_positions.items()):
            if pair in ("ALL", p):
                self.exit_position(t, EXIT_END, self.gw.bid(p))

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

    # ------------------------------------------------------------------ learning
    def _relearn(self) -> None:
        """Chantisimo re-reads its memory (own + teacher trades) and the rules are re-learned."""
        sess = self._sess()
        own = sess.journal_trades()
        self._teacher_sig = self._teacher_signature()
        self.brain.remember(own, candles=self._cache, cfg=self.cfg)
        extra = self.brain.teacher_trades(own)
        self.book.relearn(own, self._cache, extra=extra)
        self._compose_filter()
        self.book.write(own, self.state.get("trade_context", {}))
        self.brain.write(self.s.pairs, self.book.rules)

    def _teacher_signature(self) -> tuple[Any, ...]:
        sig = []
        for d in self.brain.s.learn_from:
            p = Path(d) / "journal.csv"
            sig.append((str(p), p.stat().st_mtime if p.exists() else None))
        return tuple(sig)

    def _compose_filter(self) -> None:
        """Entry veto: Chantisimo (graduation) -> learned rules -> validated signal layers."""
        view = MarketView(self._cache, self.cfg.timeframe_ms)
        inner = self.layer_book.filter(view, extra=[self.book.filter()])
        self._sess().entry_filter = self.brain.filter(inner) if self.brain.s.enabled else inner

    # ------------------------------------------------------------------ scheduled jobs
    def schedule_jobs(self) -> None:
        """Spawn the collect / retrain jobs when due (never blocks the trading loop)."""
        if not self.s.layers.get("auto_jobs", True) or self.settings_path is None:
            return
        last = self.state.setdefault("jobs_last", {})
        for job in due(self.s.layers.get("schedule", {}), last, int(time.time() * 1000)):
            proc = self._jobs.get(job)
            if proc is not None and proc.poll() is None:
                continue
            self._jobs[job] = spawn_job(job, self.settings_path, self.dir)
            last[job] = int(time.time() * 1000)
            log.info("scheduled job %s started (pid %d)", job, self._jobs[job].pid)

    # ------------------------------------------------------------------ exits
    # ------------------------------------------------------------------ copy trading
    def copy_trades(self) -> None:
        """Arm a pair when a followed, qualified trader opened a position on it (traders.py)."""
        if not self.s.copy.get("enabled", True):
            return
        from .traders import copy_signals_for, status_path

        p = status_path()
        mtime = p.stat().st_mtime if p.exists() else None
        if mtime is None or mtime == self._copy_mtime:
            return
        self._copy_mtime = mtime
        done = self.state.setdefault("copied", [])
        held = set(self._sess().open_positions)
        for sig in copy_signals_for(list(self.s.pairs), set(done)):
            done.append(sig["id"])
            if sig["pair"] in held:
                continue
            arm_pair(self.dir, sig["pair"], self.gw.now_ms() + 24 * 3_600_000)
            log.info(
                "COPY %s bought %s: armed for its next rule-passing signal",
                sig["trader"],
                sig["pair"],
            )
            self.brain.think(
                {
                    "kind": "copy",
                    "pair": sig["pair"],
                    "trader": sig["trader"],
                    "signal_utc": ms_to_iso(sig["entry_ts"]),
                    "reason": f"followed trader {sig['trader']} bought {sig['pair']}: armed",
                }
            )
        del done[:-500]
        self._save()

    # ------------------------------------------------------------------ live feed
    def _live_cfg(self) -> dict[str, Any]:
        return {"enabled": True, "price_seconds": 1.0, "candle_seconds": 5.0, **self.s.live}

    def update_live(self) -> None:
        """Live prices (one request) and the forming 4H candle, written to live.json."""
        cfg = self._live_cfg()
        tickers = getattr(self.gw, "tickers", None)
        if not cfg["enabled"] or tickers is None:
            return
        now = self.gw.now_ms()  # the exchange clock (also right in replays)
        if now - self._prices_wall >= 900 * float(cfg["price_seconds"]):
            try:
                got = tickers(list(self.s.pairs))
            except Exception as exc:  # the feed is for display; trading falls back to bid()
                log.warning("live prices unavailable: %s", exc)
                return
            self._prices = {
                p: {
                    "bid": _num_or_none(t.get("bid") or t.get("last")),
                    "ask": _num_or_none(t.get("ask") or t.get("last")),
                    "last": _num_or_none(t.get("last") or t.get("close")),
                    "ts": now,
                }
                for p, t in got.items()
            }
            self._prices_wall = now
        forming = getattr(self.gw, "forming_candle", None)
        wall = time.time()  # the forming candle is paced by real time (cheap in replays)
        if forming is not None and wall - self._forming_wall >= float(cfg["candle_seconds"]):
            self._forming_wall = wall
            for p in self.s.pairs:
                try:
                    c = forming(p)
                except Exception as exc:
                    log.debug("forming candle %s unavailable: %s", p, exc)
                    continue
                if c is not None:
                    self._forming[p] = {
                        "ts": c.ts,
                        "open": c.open,
                        "high": c.high,
                        "low": c.low,
                        "close": c.close,
                        "volume": c.volume,
                    }
        for p, f in self._forming.items():  # keep the forming candle in step with the price
            last = (self._prices.get(p) or {}).get("last")
            if last and f["ts"] == now // self.tf_ms * self.tf_ms:
                f.update(close=last, high=max(f["high"], last), low=min(f["low"], last))
        self._live_ready = True

    def _write_live(self) -> None:
        if not self._live_ready:
            return
        cap = self.state.get("capital") or {}
        beat = {
            "wall_ts": int(time.time() * 1000),
            "ts": self.gw.now_ms(),
            "prices": self._prices,
            "forming": self._forming,
            "capital": {k: cap.get(k) for k in ("equity_mtm", "daily_dd_pct", "peak_dd_pct")},
        }
        tmp = self.dir / "live.json.tmp"
        tmp.write_text(json.dumps(beat), encoding="utf-8")
        tmp.replace(self.dir / "live.json")

    # ------------------------------------------------------------------ regime allocation
    def update_regime(self) -> None:
        """Forward-filtered 5-state HMM regime with persistence (allocation.py)."""
        a = self.allocator
        if not a.s.enabled:
            return
        models = self.dir / "models"
        p = models / "regime_alloc.json"
        mtime = p.stat().st_mtime if p.exists() else None
        if mtime != self._alloc_mtime:
            self._alloc_mtime = mtime
            a.load(models)
        if a.layer is None and len(self._cache.get(a.s.ref_pair) or []) >= a.s.min_candles:
            if a.fit(self._cache, self.tf_ms, self.gw.now_ms()):  # first run: fit now
                a.save(models)
                self._alloc_mtime = p.stat().st_mtime
        try:
            r = a.regime(self._cache, self.tf_ms)
        except Exception as exc:  # the allocation overlay must never stop trading
            log.warning("regime unavailable: %s", exc)
            r = None
        if r is not None:
            prev = (self.state.get("regime") or {}).get("confirmed")
            if prev and prev != r["confirmed"]:
                log.info("REGIME %s -> %s: exposure cap %g%%", prev, r["confirmed"], r["cap_pct"])
            self.state["regime"] = r

    # ------------------------------------------------------------------ capital protection
    def mark_to_market(self) -> float:
        sess = self._sess()
        eq = sess.equity
        for p, t in sess.open_positions.items():
            bid = (self._prices.get(p) or {}).get("bid")
            if not bid:
                continue  # no live price this step: counted at cost
            eq += t.qty * (bid - t.entry_price) - self.cfg.fee_rate * t.qty * (bid + t.entry_price)
        return eq

    def apply_capital(self) -> None:
        """Daily / peak drawdown limits and the losing-streak stop (capital.py)."""
        if self.capital is None:
            return
        sess = self._sess()
        now = self.gw.now_ms()
        eq = self.mark_to_market()
        v = self.capital.evaluate(now, eq, list(sess.closed))
        sess.gatekeeper.risk_scale = v.risk_scale
        self._capital_block = v.block
        if v.kill:
            log.critical("KILL SWITCH: %s; selling everything and stopping", v.kill)
            self.flatten()
            write_control(self.dir, entries_paused=True)
            peak = float(self.state["capital"].get("peak_equity") or eq)
            path = write_lock(self.dir, v.kill, now, self.mark_to_market(), peak)
            log.critical("wrote %s: delete it by hand to allow a restart", path)
            self.stop_requested = True
        elif v.flatten:
            log.warning("CAPITAL: %s; selling every open position, entries frozen", v.flatten)
            self.flatten()
        st = self.state["capital"]
        sig = (
            st.get("day"),
            st.get("frozen_until"),
            st.get("day_reduced"),
            st.get("day_stopped"),
            round(float(st.get("peak_equity") or 0), 0),
        )
        if sig != self._capital_sig:  # persist the limits' state when it changes
            self._capital_sig = sig
            self._save()

    def _bid(self, pair: str) -> float:
        """The live bid fetched this second, else a fresh ticker request."""
        q = self._prices.get(pair) or {}
        age = self.gw.now_ms() - int(q.get("ts") or 0)
        if q.get("bid") and 0 <= age <= 2000 * float(self._live_cfg()["price_seconds"]):
            return float(q["bid"])
        return self.gw.bid(pair)

    def manage_exits(self) -> None:
        for pair, t in sorted(self._sess().open_positions.items()):
            bid = self._bid(pair)
            if bid <= t.stop:
                self.exit_position(t, EXIT_SL, bid)
            elif bid >= t.target:
                self.exit_position(t, EXIT_TP, bid)

    def exit_position(self, t: Trade, reason: str, bid: float) -> Trade:
        sess = self._sess()
        qty = self.gw.round_qty(t.pair, min(t.qty, self.gw.free(base_of(t.pair))))
        if qty <= 0:
            log.error(
                "%s #%d: nothing left to sell; closing the journal row at the bid",
                t.pair,
                t.trade_id,
            )
            fill = Fill(0.0, bid, None, self.gw.now_ms(), "none")
        else:
            fill = self.gw.sell(t.pair, qty)
            if fill.qty <= 0:
                raise OrderError(f"{t.pair} #{t.trade_id}: sell of {qty} did not fill")
        entry_fee = self.state["entry_fees"].pop(str(t.trade_id), None)
        fees = None if entry_fee is None or fill.fee_quote is None else entry_fee + fill.fee_quote
        closed = sess.on_exit(t.trade_id, max(fill.ts, t.entry_ts), fill.price, reason, fees=fees)
        log.info(
            "EXIT %s #%d %s at %.8g: %+.2fR, pnl %+.2f, equity %.2f",
            t.pair,
            t.trade_id,
            reason,
            fill.price,
            closed.r_multiple,
            closed.pnl,
            sess.equity,
        )
        for _, msg in sess.breaker_log[-3:]:
            log.info("breaker: %s", msg)
        self.layer_book.observe(t.pair, t.features, closed.r_multiple or 0.0, closed.exit_ts)
        ctx = self.state.get("trade_context", {}).get(str(t.trade_id), {})
        reflection = self.brain.reflect(closed, ctx.get("brain_recall"))
        log.info("CHANTISIMO #%d: %s", t.trade_id, reflection["lesson"])
        self._relearn()
        log.info("LESSON %s", self.book.lessons.get(str(t.trade_id), ""))
        self._save()
        return closed

    def flatten(self) -> list[Trade]:
        """Sell every open position at market (journaled as END exits)."""
        return [
            self.exit_position(t, EXIT_END, self.gw.bid(p))
            for p, t in sorted(self._sess().open_positions.items())
        ]

    # ------------------------------------------------------------------ entries
    def process_closed_candles(self) -> None:
        now = self.gw.now_ms()
        boundary = now // self.tf_ms * self.tf_ms  # close time of the latest closed candle
        if now < boundary + self.s.close_delay_seconds * 1000:
            return
        signal_ts = boundary - self.tf_ms
        last = self.state["last_signal_ts"]
        todo = [p for p in sorted(self.s.pairs) if int(last.get(p, -1)) < signal_ts]
        if not todo:
            return
        sess = self._sess()
        if self._teacher_signature() != self._teacher_sig:  # teachers closed trades: learn
            self._relearn()
        sess.gatekeeper.news = NewsCalendar(self._events(), self.cfg)  # reload the calendar
        for pair in todo:
            self.refresh_candles(pair)
        self._compose_filter()  # layers see the freshly topped-up history
        self.update_regime()
        for pair in todo:
            candles = self._cache.get(pair, [])
            if not candles or candles[-1].ts != signal_ts:
                log.info("%s: candle %d not available yet", pair, signal_ts)
                continue
            dec = sess.on_candle_close(pair, candles)
            last[pair] = signal_ts
            log.info("SIGNAL %s %s: %s", pair, dec.rule, dec.reason)
            armed = armed_pairs(self.dir, self.gw.now_ms())
            if dec.allowed and self._capital_block:
                reason = f"{pair}: {self._capital_block}"
                sess.on_fill_skipped(pair, dec, reason, rule="X_capital_guard")
            elif dec.allowed and self.entries_paused and pair not in armed:
                sess.on_fill_skipped(
                    pair, dec, f"{pair}: new entries paused by the operator", rule="X_operator"
                )
            elif dec.allowed:
                if pair in armed:  # "execute trade": the operator asked for this pair's next one
                    log.info("%s: armed by the operator; taking this rule-passing signal", pair)
                    disarm_pair(self.dir, pair)
                self.enter(pair, dec)
            self._save()

    def _candle_path(self, pair: str) -> Path:
        return self.dir / "candles" / f"{pair.replace('/', '_')}-4h.csv"

    def refresh_candles(self, pair: str) -> list[Candle]:
        """The pair's full closed-candle history: cached on disk, topped up incrementally.

        The whole history is evaluated so EMA200 matches a backtest over the same data.
        A gap in the cache (e.g. after a long outage) triggers a fresh deep download.
        """
        cache = self._cache.get(pair, [])
        if cache:
            missing = (self.gw.now_ms() - cache[-1].ts) // self.tf_ms
            fresh = self.gw.closed_candles(pair, int(max(missing + 3, 5)))
            merged = {c.ts: c for c in cache}
            added = [c for c in fresh if c.ts not in merged]
            merged.update({c.ts: c for c in fresh})
            cache = [merged[t] for t in sorted(merged)]
            if _contiguous(cache, self.tf_ms):
                self._cache[pair] = cache
                append_candles(added, self._candle_path(pair))
                return cache
            log.info("%s: candle cache has a gap; re-downloading history", pair)
        cache = self.gw.closed_candles(pair, self.s.history_candles)
        if not _contiguous(cache, self.tf_ms):
            log.warning("%s: exchange history has gaps; using the latest contiguous run", pair)
            cache = _latest_contiguous(cache, self.tf_ms)
        self._cache[pair] = cache
        save_candles_csv(cache, self._candle_path(pair))
        return cache

    def enter(self, pair: str, dec: EntryDecision) -> Trade | None:
        sess = self._sess()
        ask = self.gw.ask(pair)
        slip = self.cfg.slippage_pct / 100.0
        pessimistic = ask * (1 + slip) * (1 + self.s.entry_buffer_pct / 100.0)
        cash = min(sess.free_cash(), self.gw.free(self.s.quote))
        regime = self.state.get("regime") if self.allocator.s.enabled else None
        if regime:  # the regime's exposure cap can only shrink or skip the entry
            used = sum(t.qty * t.entry_price for t in sess.open_positions.values())
            room = exposure_room(float(regime["cap_pct"]), sess.equity, used)
            if room < max(self.gw.min_order(pair)[1], 5.0):
                reason = (
                    f"{pair}: {regime['confirmed']} regime caps exposure at "
                    f"{regime['cap_pct']:g}% of equity and {used:,.2f} is already in use"
                )
                sess.on_fill_skipped(pair, dec, reason, rule="X_capital_guard")
                log.info("SKIP %s", reason)
                return None
            cash = min(cash, room)
        plan = sess.gatekeeper.plan_fill(pair, dec, ask, pessimistic, sess.equity, cash)
        if not plan.ok or plan.sizing is None:
            sess.on_fill_skipped(pair, dec, plan.reason, rule=plan.rule)
            log.info("SKIP %s: %s", pair, plan.reason)
            return None
        qty = self.gw.round_qty(pair, plan.sizing.qty)
        min_amount, min_cost = self.gw.min_order(pair)
        if qty <= 0 or qty < min_amount or qty * ask < min_cost:
            reason = f"{pair}: size {qty} is below the exchange minimum ({min_amount}, {min_cost})"
            sess.on_fill_skipped(pair, dec, reason)
            log.info("SKIP %s", reason)
            return None
        try:
            ex = {"maker_first": True, "maker_wait_seconds": 15, **self.s.execution}
            maker = getattr(self.gw, "buy_maker_first", None)
            if ex["maker_first"] and self.s.mode != "paper" and maker is not None:
                fill = maker(pair, qty, wait_s=float(ex["maker_wait_seconds"]))
            else:
                fill = self.gw.buy(pair, qty)
        except OrderError as exc:
            sess.on_fill_skipped(pair, dec, f"{pair}: buy failed: {exc}")
            raise
        if fill.qty <= 0:
            sess.on_fill_skipped(pair, dec, f"{pair}: buy of {qty} did not fill")
            return None
        return self._book_fill(pair, dec, ask, fill)

    def _book_fill(self, pair: str, dec: EntryDecision, ask: float, fill: Fill) -> Trade | None:
        sess = self._sess()
        cash = sess.free_cash()
        actual = sess.gatekeeper.plan_fill(pair, dec, ask, fill.price, sess.equity, cash)
        held = fill.qty
        if actual.ok and actual.sizing is not None and held > actual.sizing.qty:
            keep = self.gw.round_qty(pair, actual.sizing.qty)
            excess = self.gw.round_qty(pair, held - keep)
            if excess > 0:
                self.gw.sell(pair, excess)
                held -= excess
            held = min(held, actual.sizing.qty)
        d = dec.decision_ts
        fill_ts = fill.ts if d <= fill.ts < d + self.tf_ms else None
        trade = sess.on_fill(pair, dec, ask, fill.price, qty=held, fill_ts=fill_ts)
        if trade is None:
            log.warning(
                "%s: fill at %.8g refused (%s); flattening",
                pair,
                fill.price,
                sess.last_fill_plan.reason if sess.last_fill_plan else "?",
            )
            out = self.gw.sell(pair, self.gw.round_qty(pair, held))
            self.state["unmanaged"].append(
                {"pair": pair, "buy": fill.price, "sell": out.price, "qty": held, "ts": fill.ts}
            )
            return None
        if fill.fee_quote is not None:
            self.state["entry_fees"][str(trade.trade_id)] = fill.fee_quote
        expected = expected_outcome(pair, trade.features, sess.closed, self.cfg)
        note = (
            "; ".join(
                f"advisory rule {r.id} matched"
                for r in self.book.rules
                if r.status != "active" and r.matches(pair, trade.features)
            )
            or "no learned rule matched (all active rules were checked before entry)"
        )
        ctx = entry_context(dec.check, dec.reason, trade.features, expected, note)
        recalled = self.brain.pending.get(pair)
        if recalled is not None:
            ctx["brain_recall"] = {k: v for k, v in recalled.items() if k != "similar"}
            ctx["why"].append(recalled["text"])
        self.state["trade_context"][str(trade.trade_id)] = ctx
        log.info(
            "ENTRY %s #%d qty %.8g at %.8g, stop %.8g, target %.8g, risk %.2f%%",
            pair,
            trade.trade_id,
            trade.qty,
            trade.entry_price,
            trade.stop,
            trade.target,
            trade.risk_pct,
        )
        self._save()
        return trade

    # ------------------------------------------------------------------ status
    def status(self) -> str:
        sess = self._sess()
        lines = [
            f"{self.s.exchange}/{self.s.mode}: realized equity {sess.equity:.2f} {self.s.quote} "
            f"(start {sess.starting_equity:.2f}), closed trades {len(sess.closed)}",
        ]
        for pair, t in sorted(sess.open_positions.items()):
            lines.append(
                f"  OPEN {pair} #{t.trade_id} qty {t.qty:.8g} entry {t.entry_price:.8g}"
                f" stop {t.stop:.8g} target {t.target:.8g} risk {t.risk_pct:.2f}%"
            )
        adaptations = audit(sess.journal_trades(), self.cfg, self.gw.now_ms(), sess.equity)
        lines.append(render_markdown(adaptations))
        return "\n".join(lines)


# ---------------------------------------------------------------------- background jobs
def spawn_job(job: str, settings_path: Path, state_dir: Path) -> Any:
    """Start ``retrain.py <job>`` as a separate process (output in retrain.log)."""
    import subprocess

    from .launch import command

    cmd, env = command("retrain", job, "--settings", str(settings_path))
    err = (Path(state_dir) / "jobs.stderr.log").open("ab")
    return subprocess.Popen(cmd, cwd=Path.cwd(), env=env, stdout=subprocess.DEVNULL, stderr=err)


# ---------------------------------------------------------------------- control files
def read_control(state_dir: Path) -> dict[str, Any]:
    """Persistent operator flags written by the dashboard (control.json)."""
    path = Path(state_dir) / "control.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        return {}


def write_control(state_dir: Path, **flags: Any) -> dict[str, Any]:
    current = read_control(state_dir)
    current.update(flags)
    tmp = Path(state_dir) / "control.json.tmp"
    tmp.write_text(json.dumps(current, indent=1), encoding="utf-8")
    tmp.replace(Path(state_dir) / "control.json")
    return current


def _num_or_none(x: Any) -> float | None:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def arm_pair(state_dir: Path, pair: str, until_ms: int) -> dict[str, Any]:
    """Take ``pair``'s next signal that passes every rule, even while entries are paused."""
    armed = dict(read_control(state_dir).get("armed_pairs") or {})
    armed[pair] = int(until_ms)
    return write_control(state_dir, armed_pairs=armed)


def disarm_pair(state_dir: Path, pair: str) -> dict[str, Any]:
    armed = dict(read_control(state_dir).get("armed_pairs") or {})
    armed.pop(pair, None)
    return write_control(state_dir, armed_pairs=armed)


def armed_pairs(state_dir: Path, now_ms: int) -> dict[str, int]:
    armed = read_control(state_dir).get("armed_pairs") or {}
    return {p: int(u) for p, u in armed.items() if int(u) > now_ms}


def post_request(state_dir: Path, **request: Any) -> Path:
    """Queue a one-shot request (stop / close / rule) for the running bot."""
    d = Path(state_dir) / "requests"
    d.mkdir(parents=True, exist_ok=True)
    name = f"{time.time_ns()}-{request.get('action', 'x')}.json"
    tmp = d / (name + ".tmp")
    tmp.write_text(json.dumps(request), encoding="utf-8")
    final = d / name
    tmp.replace(final)
    return final


def pop_requests(state_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    d = Path(state_dir) / "requests"
    if not d.exists():
        return []
    out = []
    for path in sorted(d.glob("*.json")):
        try:
            req = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            req = {"action": "invalid"}
        path.unlink(missing_ok=True)
        out.append((path, req))
    return out


# ---------------------------------------------------------------------- helpers
def _contiguous(candles: Sequence[Candle], tf_ms: int) -> bool:
    return all(b.ts - a.ts == tf_ms for a, b in pairwise(candles))


def _latest_contiguous(candles: Sequence[Candle], tf_ms: int) -> list[Candle]:
    start = len(candles) - 1
    while start > 0 and candles[start].ts - candles[start - 1].ts == tf_ms:
        start -= 1
    return list(candles[start:])


def append_candles(candles: Sequence[Candle], path: Path) -> None:
    """Append candles in ``data.save_candles_csv`` format (creates the file if needed)."""
    if not candles:
        return
    if not path.exists():
        save_candles_csv(candles, path)
        return
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        for c in candles:
            prices = (c.open, c.high, c.low, c.close, c.volume)
            writer.writerow([int(c.ts), *(repr(float(x)) for x in prices)])


def append_decisions(records: Sequence[DecisionRecord], path: Path) -> None:
    """Append rows in ``adoption_evidence.write_decisions_log`` format."""
    if not path.exists():
        write_decisions_log(records, path)
        return
    with path.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        for r in records:
            allowed = "true" if r.allowed else "false"
            writer.writerow(
                [r.pair, r.signal_ts, ms_to_iso(r.signal_ts), allowed, r.rule, r.reason]
            )


# ---------------------------------------------------------------------- CLI
def build_gateway(settings: BotSettings, cfg: StrategyConfig) -> Any:
    if settings.mode == "paper":
        return PaperBroker.connect(
            settings.exchange, fee_rate=cfg.fee_rate, slippage_pct=cfg.slippage_pct
        )
    return CcxtGateway.connect(settings.exchange, settings.mode)


def _check_adoption(settings: BotSettings, cfg: StrategyConfig, now_ms: int) -> None:
    if settings.mode != "live" or not settings.adoption_record:
        return
    from .adoption import require_stage
    from .adoption_record import load_record

    path = Path(settings.adoption_record)
    require_stage(load_record(path), "LIVE", cfg, now_ms, base_dir=path.parent)


def _setup_logging(state_dir: str, verbose: bool) -> None:
    Path(state_dir).mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(Path(state_dir) / "bot.log", encoding="utf-8"),
    ]
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        handlers=handlers,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python3 -m research.trendbot.live_bot", description="trendbot live trading bot"
    )
    p.add_argument("command", choices=("run", "status", "flatten"))
    p.add_argument("--settings", required=True, help="bot settings JSON")
    p.add_argument("--mode", choices=MODES, help="override the settings mode")
    p.add_argument("--steps", type=int, help="stop after N loop iterations (default: forever)")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None, gateway: Any = None) -> int:
    args = _parser().parse_args(argv)
    settings = BotSettings.load(args.settings, mode=args.mode)
    _setup_logging(settings.state_dir, args.verbose)
    cfg = settings.strategy_config()
    gw = gateway if gateway is not None else build_gateway(settings, cfg)
    if settings.engine == "lab":
        from .lab_bot import LabBot

        lab = LabBot(settings, gw)
        lab.settings_path = Path(args.settings).resolve()
        lab.start()
        if args.command == "flatten":
            lab._exit_all(gw.bid(lab.pair), EXIT_END, "flatten")
            return 0
        if args.command == "run":
            signal.signal(signal.SIGINT, lambda *_: setattr(lab, "stop_requested", True))
            signal.signal(signal.SIGTERM, lambda *_: setattr(lab, "stop_requested", True))
            lab.run(max_steps=args.steps)
        return 0
    bot = TrendBot(settings, gw, cfg=cfg)
    bot.settings_path = Path(args.settings).resolve()
    bot.start()
    if args.command == "status":
        print(bot.status())
        return 0
    if args.command == "flatten":
        for t in bot.flatten():
            print(f"closed {t.pair} #{t.trade_id} at {t.exit_price:.8g} ({t.r_multiple:+.2f}R)")
        return 0
    _check_adoption(settings, cfg, gw.now_ms())

    def _stop(signum: int, _frame: Any) -> None:
        log.info("signal %d: stopping after this step", signum)
        bot.stop_requested = True

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    log.info(
        "running %s on %s (%s), pid %d",
        ",".join(settings.pairs),
        settings.exchange,
        settings.mode,
        os.getpid(),
    )
    bot.run(max_steps=args.steps)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
