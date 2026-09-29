"""The dashboard terminal: talk to the bot in plain words ("start day", "earnings this week",
"execute trade BTC", "why no trade on ETH?", "pull out commands").

An offline command interpreter: no API key, nothing leaves the computer. It reads the journal,
the decisions and the brain, and starts, stops, pauses, arms or closes through the same
dashboard actions as the buttons.

Safety:

- anything that changes trading (stop, close positions, execute trade, starting a LIVE bot)
  is only PREPARED; it runs when you type ``yes``;
- "execute trade" never bypasses the nine rules: it ARMS the pair, so its next signal that
  passes every rule is taken even while the day is ended (entries paused). With entries open
  the bot already takes every such signal;
- the fleet approval (more than 5 bots) still pops up.
"""

from __future__ import annotations

import logging
import re
import secrets
import statistics
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .journal import ms_to_iso, read_journal


log = logging.getLogger("trendbot.assistant")

NAME = "Chantisimo"
CONFIRM_TTL_S = 120
TF_MS = 4 * 3_600_000
ARM_HOURS = 24
COIN_NAMES = {
    "bitcoin": "BTC", "btc": "BTC", "xbt": "BTC",
    "ethereum": "ETH", "ether": "ETH", "eth": "ETH",
    "bnb": "BNB", "binance coin": "BNB",
    "solana": "SOL", "sol": "SOL",
    "ripple": "XRP", "xrp": "XRP",
    "cardano": "ADA", "ada": "ADA",
    "dogecoin": "DOGE", "doge": "DOGE",
    "avalanche": "AVAX", "avax": "AVAX",
    "chainlink": "LINK", "link": "LINK",
    "polkadot": "DOT", "dot": "DOT",
    "litecoin": "LTC", "ltc": "LTC",
    "tron": "TRX", "trx": "TRX",
}  # fmt: skip

HELP = """Things you can type (plain words are fine):
  status                       how the bot is doing right now
  earnings [today|yesterday|week|month|all]   profit and loss (add "all bots" in a fleet)
  positions                    open trades with their unrealized P&L
  trades                       the last closed trades
  start day / end day          start trading (bot on, entries open) / stop new entries and
                               get the day's summary ("end day and close all" also sells)
  execute trade BTC            take BTC's next signal that passes all nine rules
  close BTC / close all        market-sell a position (asks you to confirm)
  why BTC                      the latest decision on BTC and its reason
  brain                        what Chantisimo has learned: mistakes, graduation
  pause / resume               new entries off / on (exits always keep running)
  start bot / stop bot         the bot process
  bots / use <bot name>        the fleet, and which bot the terminal talks to
  retrain / collect            the weekly retrain / hourly data job, now
  help / pull out commands     this list"""


# ---------------------------------------------------------------------- replies
@dataclass
class Reply:
    text: str
    table: dict[str, Any] | None = None
    confirm: dict[str, Any] | None = None
    approval: dict[str, Any] | None = None
    bot: str | None = None
    data: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": True, "text": self.text}
        for k in ("table", "confirm", "approval", "bot"):
            v = getattr(self, k)
            if v is not None:
                out[k] = v
        return out


DEC_COLS = ["pair", "time", "result", "rule", "reason"]


def _hm(ms: int) -> str:
    return ms_to_iso(ms)[:16].replace("T", " ")


def _table(cols: list[str], rows: list[list[Any]]) -> dict[str, Any]:
    return {"cols": cols, "rows": rows}


def _money(x: float | None, quote: str) -> str:
    if x is None:
        return "-"
    return f"{x:+,.2f} {quote}"


# ---------------------------------------------------------------------- time
def _day_start(now_ms: int) -> int:
    d = datetime.fromtimestamp(now_ms / 1000, UTC).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return int(d.timestamp() * 1000)


PERIODS = {
    "today": "since 00:00 UTC today",
    "yesterday": "yesterday (UTC)",
    "week": "the last 7 days",
    "month": "the last 30 days",
    "year": "the last 365 days",
    "all": "all time",
}


def period_range(period: str, now_ms: int) -> tuple[int, int]:
    day = _day_start(now_ms)
    if period == "today":
        return day, now_ms
    if period == "yesterday":
        return day - 86_400_000, day
    days = {"week": 7, "month": 30, "year": 365}.get(period)
    if days:
        return now_ms - days * 86_400_000, now_ms
    return 0, now_ms


def detect_period(text: str) -> str:
    t = text.lower()
    for key, words in (
        ("yesterday", ("yesterday",)),
        ("today", ("today", "day", "daily", "so far")),
        ("week", ("week", "7 days", "seven days")),
        ("month", ("month", "30 days")),
        ("year", ("year", "365")),
        ("all", ("all time", "total", "overall", "ever", "since start", "all")),
    ):
        if any(re.search(rf"\b{re.escape(w)}\b", t) for w in words):
            return key
    return "all"


# ---------------------------------------------------------------------- the terminal
class Terminal:
    """One operator's terminal over a dashboard ``FleetRuntime``."""

    def __init__(
        self,
        runtime: Any,
        *,
        now_ms: Callable[[], int] = lambda: int(time.time() * 1000),
    ) -> None:
        self.rt = runtime
        self.now_ms = now_ms
        self.pending: dict[str, Any] | None = None
        self._snaps: dict[str, dict[str, Any]] = {}

    # -------------------------------------------------------------- plumbing
    def _bot(self, bot: str | None) -> str:
        names = self.rt.names
        return bot if bot in names else names[0]

    def _dir(self, bot: str) -> Path:
        return Path(self.rt.entries[bot][0])

    def _snap(self, bot: str) -> dict[str, Any]:
        if bot not in self._snaps:  # one snapshot per command (it reads every file)
            self._snaps[bot] = self.rt.snapshot(bot)
        return self._snaps[bot]

    def _quote(self, bot: str) -> str:
        return self._snap(bot)["meta"].get("quote") or "USDT"

    def _pairs(self, bot: str) -> list[str]:
        snap = self._snap(bot)
        pairs = set(snap.get("pairs") or {})
        fleet = getattr(self.rt, "fleet", None)
        if fleet is not None and bot in fleet.bots:
            pairs |= set(fleet.bots[bot].settings.pairs)
        settings_path = getattr(self.rt.entries[bot][2], "settings_path", None)
        if settings_path is not None:
            from .live_bot import BotSettings

            pairs |= set(BotSettings.load(settings_path).pairs)
        if not pairs:  # never ran and no settings file: what it traded or decided on
            j = self._dir(bot) / "journal.csv"
            pairs |= {t.pair for t in read_journal(j)} if j.exists() else set()
            pairs |= set(self._latest_decisions(bot))
        return sorted(pairs)

    def find_pair(self, text: str, bot: str) -> str | None:
        t = text.lower()
        pairs = self._pairs(bot)
        for p in pairs:
            if p.lower() in t or p.replace("/", "").lower() in t:
                return p
        for word, base in COIN_NAMES.items():
            if re.search(rf"\b{re.escape(word)}\b", t):
                for p in pairs:
                    if p.split("/")[0] == base:
                        return p
                quote = pairs[0].split("/")[1] if pairs else "USDT"
                return f"{base}/{quote}"
        return None

    def find_bot(self, text: str) -> str | None:
        t = text.lower()
        for n in sorted(self.rt.names, key=len, reverse=True):
            if n.lower() in t:
                return n
        return None

    def _act(self, req: dict[str, Any]) -> tuple[bool, str, dict[str, Any] | None]:
        code, body = self.rt.act(req)
        self._snaps.clear()  # the action changed the state
        if code == 409 and body.get("needs_approval"):
            return False, body.get("message", "approval needed"), body
        return bool(body.get("ok")), str(body.get("message", "")), None

    # -------------------------------------------------------------- entry point
    def handle(self, text: str, bot: str | None = None) -> dict[str, Any]:
        text = (text or "").strip()[:2000]
        self._snaps = {}
        bot = self._bot(self.find_bot(text) if self.rt.is_fleet and self.find_bot(text) else bot)
        try:
            reply = self._handle(text, bot)
        except Exception as exc:  # the terminal must never take the dashboard down
            log.exception("terminal command failed")
            reply = Reply(f"Something went wrong: {type(exc).__name__}: {exc}")
        return reply.to_json()

    def _handle(self, text: str, bot: str) -> Reply:
        t = text.lower().strip(" .!?")
        if not t:
            return Reply("Type a command, or help.")
        if t in ("yes", "y", "confirm", "do it", "ok", "okay", "go", "go ahead", "yes please"):
            return self.confirm()
        if t in ("no", "n", "cancel", "stop that", "never mind", "nevermind", "abort"):
            had = self.pending is not None
            self.pending = None
            return Reply("Cancelled; nothing was changed." if had else "Nothing to cancel.")
        intent = self.parse(t, bot)
        if intent is not None:
            return self.run_tool(intent[0], intent[1], bot)
        return Reply(
            "I didn't catch that. Try: status, earnings today, positions, start day, end day, "
            "execute trade BTC, close BTC, why BTC. Type help (or pull out commands) for all."
        )

    # -------------------------------------------------------------- built-in parser
    def parse(self, t: str, bot: str) -> tuple[str, dict[str, Any]] | None:  # noqa: C901
        has = lambda *ws: any(re.search(rf"\b{w}\b", t) for w in ws)  # noqa: E731
        pair = self.find_pair(t, bot)
        all_bots = self.rt.is_fleet and has("all bots", "every bot", "fleet", "all of them")
        if t in ("help", "?", "commands", "menu", "h") or has(
            "what can you do",
            "commands?",
            "command list",
            "list (the |of )?commands",
            "(show|give|pull out|pull up|get)( me)?( the| all| my)? commands?",
            "options",
        ):
            return "help", {}
        m = re.match(r"^(?:use|switch to|talk to|select) (?:bot )?(.+)$", t)
        if m and self.find_bot(m.group(1)):
            return "use_bot", {"name": self.find_bot(m.group(1))}
        if has(
            "start (the |my |trading )?day", "good morning", "open (the )?day", "begin (the )?day"
        ):
            return "start_day", {}
        if has(
            "end (the |my |trading )?day",
            "close (the )?day",
            "good night",
            "finish (the )?day",
            "stop for (the )?day",
        ):
            return "end_day", {
                "close_positions": has(
                    "close all",
                    "close everything",
                    "flatten",
                    "sell all",
                    "close positions",
                    "close them",
                )
            }
        if has("start all", "start every"):
            return "start_all", {}
        if has("stop all", "stop every"):
            return "stop_all", {}
        if has("(start|run|launch|turn on) (the )?bot"):
            return "start_bot", {}
        if has("(stop|shut ?down|kill|turn off) (the )?bot"):
            return "stop_bot", {}
        if has(
            "why",
            "reason",
            "signal",
            "signals",
            "decision",
            "decisions",
            "blocked",
            "skipped",
            "skip",
            "no trade",
        ):
            return "why", {"pair": pair}
        if has(
            "execute", "place", "take", "enter", "buy", "long", "open (a )?(trade|position)"
        ) and not has("close", "sell"):
            return "execute_trade", {"pair": pair}
        if has("close", "sell", "exit", "flatten", "liquidate"):
            target = "ALL" if has("all", "everything", "every position") else pair
            return "close_position", {"pair": target}
        if has(
            "pause",
            "hold (new )?entries",
            "no new (trades|entries)",
            "stop trading",
            "stop entries",
        ):
            return "pause", {}
        if has("resume", "unpause", "allow (new )?entries", "start trading", "open entries"):
            return "resume", {}
        if has(
            "earn",
            "earning",
            "earnings",
            "profit",
            "profits",
            "p&l",
            "pnl",
            "p/l",
            "made",
            "income",
            "gain",
            "gains",
            "loss",
            "losses",
            "return",
            "returns",
            "how much",
        ):
            return "earnings", {"period": detect_period(t), "all_bots": bool(all_bots)}
        if has("position", "positions", "open trades", "holding", "holdings", "exposure"):
            return "positions", {}
        if has("trades", "history", "closed", "last trade", "recent"):
            return "trades", {"n": 10}
        if has(
            "brain",
            "chantisimo",
            "mistake",
            "mistakes",
            "learn",
            "learned",
            "lesson",
            "lessons",
            "graduat\\w*",
        ):
            return "brain", {}
        if has("retrain", "train"):
            return "retrain", {}
        if has("collect"):
            return "collect", {}
        if has("bots", "fleet"):
            return "bots", {}
        if has(
            "status",
            "summary",
            "overview",
            "report",
            "how are (we|you|things)",
            "how is it going",
            "doing",
        ):
            return "status", {"all_bots": bool(all_bots)}
        return None

    # -------------------------------------------------------------- tools
    def run_tool(self, name: str, args: Mapping[str, Any], bot: str) -> Reply:
        fn = getattr(self, f"tool_{name}", None)
        if fn is None:
            return Reply(f"Unknown command {name!r}. Type help.")
        return fn(dict(args), bot)

    def tool_help(self, args: dict[str, Any], bot: str) -> Reply:
        return Reply(HELP)

    def tool_use_bot(self, args: dict[str, Any], bot: str) -> Reply:
        name = self._bot(args.get("name"))
        return Reply(f"Now talking to bot {name}.", bot=name)

    def tool_bots(self, args: dict[str, Any], bot: str) -> Reply:
        s = self.rt.summary()
        rows = [
            [
                r["name"],
                "running" if r.get("running") else "stopped",
                r.get("mode"),
                f"{r.get('equity') or 0:,.2f}",
                f"{r.get('return_pct') or 0:+.2f}%",
                r.get("open_positions", 0),
            ]
            for r in s["bots"]
        ]
        return Reply(
            f"{s['running']} of {len(rows)} bots running.",
            _table(["bot", "status", "mode", "equity", "return", "open"], rows),
        )

    def _status_one(self, bot: str) -> tuple[str, dict[str, Any]]:
        snap = self._snap(bot)
        st, meta, q = snap["stats"], snap["meta"], self._quote(bot)
        b = snap.get("bot") or {}
        running = "running" if b.get("running") else "stopped"
        paused = (snap.get("control") or {}).get("entries_paused")
        entries = "entries paused (day ended)" if paused else "entries open"
        opn = snap.get("open_positions") or []
        unreal = sum(p.get("unrealized") or 0 for p in opn)
        today = self._earn(bot, "today")
        breakers = [x["explanation"] for x in snap.get("breakers") or []]
        text = (
            f"{bot}: {running}, {meta.get('mode')} on {meta.get('exchange')}, {entries}. "
            f"Equity {st.get('equity', 0):,.2f} {q} "
            f"({st.get('return_pct') or 0:+.2f}% since start). "
            f"Today: {_money(today['pnl'], q)} over {today['trades']} closed trades. "
            f"{len(opn)} open position(s), unrealized {_money(unreal, q)}."
        )
        if breakers:
            text += " Circuit breaker: " + breakers[0]
        return text, {"bot": bot, "running": bool(b.get("running")), "paused": bool(paused),
                      "equity": st.get("equity"), "return_pct": st.get("return_pct"),
                      "today_pnl": today["pnl"], "open_positions": len(opn), "unrealized": unreal,
                      "quote": q, "mode": meta.get("mode")}  # fmt: skip

    def tool_status(self, args: dict[str, Any], bot: str) -> Reply:
        bots = self.rt.names if args.get("all_bots") else [bot]
        parts = [self._status_one(b) for b in bots]
        return Reply("\n".join(p[0] for p in parts), data={"bots": [p[1] for p in parts]})

    def _earn(self, bot: str, period: str) -> dict[str, Any]:
        lo, hi = period_range(period, self.now_ms())
        j = self._dir(bot) / "journal.csv"
        closed = [t for t in (read_journal(j) if j.exists() else []) if t.is_closed]
        sel = [t for t in closed if lo <= (t.exit_ts or 0) < hi + 1]
        by_pair: dict[str, list[Any]] = {}
        for t in sel:
            by_pair.setdefault(t.pair, []).append(t)
        rs = [t.r_multiple or 0.0 for t in sel]
        return {
            "period": period,
            "trades": len(sel),
            "wins": sum(1 for t in sel if (t.pnl or 0) > 0),
            "pnl": round(sum(t.pnl or 0.0 for t in sel), 2),
            "fees": round(sum(t.fees or 0.0 for t in sel), 2),
            "avg_r": round(statistics.fmean(rs), 3) if rs else None,
            "total_r": round(sum(rs), 3),
            "by_pair": {
                p: {"trades": len(ts), "pnl": round(sum(t.pnl or 0.0 for t in ts), 2)}
                for p, ts in sorted(by_pair.items())
            },
        }

    def tool_earnings(self, args: dict[str, Any], bot: str) -> Reply:
        period = args.get("period") if args.get("period") in PERIODS else "all"
        bots = self.rt.names if args.get("all_bots") else [bot]
        rows, data, total = [], [], 0.0
        for b in bots:
            e = self._earn(b, period)
            snap = self._snap(b)
            unreal = sum(p.get("unrealized") or 0 for p in snap.get("open_positions") or [])
            q = self._quote(b)
            e.update(
                bot=b, unrealized=round(unreal, 2), quote=q, equity=snap["stats"].get("equity")
            )
            data.append(e)
            total += e["pnl"]
            if len(bots) == 1:
                rows = [[p, v["trades"], _money(v["pnl"], q)] for p, v in e["by_pair"].items()]
            else:
                rows.append([b, e["trades"], _money(e["pnl"], q), _money(unreal, q)])
        if len(bots) == 1:
            e = data[0]
            q = e["quote"]
            win = f", {e['wins']} won" if e["trades"] else ""
            text = (
                f"Realized {PERIODS[period]}: {_money(e['pnl'], q)} from {e['trades']} closed "
                f"trades{win}"
                + (f", avg {e['avg_r']:+.2f}R" if e["avg_r"] is not None else "")
                + f" (fees {e['fees']:,.2f} {q}). Open positions: {_money(e['unrealized'], q)} "
                f"unrealized. Equity {e['equity'] or 0:,.2f} {q}."
            )
            table = _table(["pair", "trades", "P&L"], rows) if rows else None
        else:
            text = (
                f"Realized {PERIODS[period]} across {len(bots)} bots: {total:+,.2f} "
                "(in each bot's quote currency)."
            )
            table = _table(["bot", "trades", "realized", "unrealized"], rows)
        return Reply(text, table, data={"earnings": data})

    def tool_positions(self, args: dict[str, Any], bot: str) -> Reply:
        snap, q = self._snap(bot), self._quote(bot)
        opn = snap.get("open_positions") or []
        if not opn:
            return Reply(f"{bot} has no open positions.")
        rows = [
            [
                p["pair"],
                f"{p.get('entry_price', 0):.6g}",
                f"{p.get('last') or 0:.6g}",
                f"{p.get('stop', 0):.6g}",
                f"{p.get('target', 0):.6g}",
                _money(p.get("unrealized"), q),
                f"{p.get('r_now'):+.2f}R" if p.get("r_now") is not None else "-",
            ]
            for p in opn
        ]
        return Reply(
            f"{len(opn)} open position(s).",
            _table(["pair", "entry", "last", "stop", "target", "unrealized", "R now"], rows),
            data={"positions": opn},
        )

    def tool_trades(self, args: dict[str, Any], bot: str) -> Reply:
        n = max(1, min(int(args.get("n") or 10), 50))
        j = self._dir(bot) / "journal.csv"
        closed = [t for t in (read_journal(j) if j.exists() else []) if t.is_closed][-n:]
        if not closed:
            return Reply("No closed trades yet.")
        q = self._quote(bot)
        rows = [
            [f"#{t.trade_id}", t.pair, _hm(t.exit_ts or 0), t.exit_reason,
             f"{t.r_multiple or 0:+.2f}R", _money(t.pnl, q)]
            for t in reversed(closed)
        ]  # fmt: skip
        return Reply(
            f"Last {len(closed)} closed trades.",
            _table(["trade", "pair", "closed (UTC)", "exit", "R", "P&L"], rows),
            data={"trades": [
                {"id": t.trade_id, "pair": t.pair, "r": t.r_multiple, "pnl": t.pnl,
                 "exit": t.exit_reason, "exit_utc": ms_to_iso(t.exit_ts or 0)}
                for t in closed
            ]},
        )  # fmt: skip

    def _latest_decisions(self, bot: str) -> dict[str, Any]:
        from .adoption_evidence import read_decisions_log

        p = self._dir(bot) / "decisions.csv"
        out: dict[str, Any] = {}
        for r in read_decisions_log(p) if p.exists() else []:
            out[r.pair] = r
        return out

    def _next_close(self) -> str:
        now = self.now_ms()
        nxt = now // TF_MS * TF_MS + TF_MS
        mins = (nxt - now) // 60_000
        return f"{ms_to_iso(nxt)[11:16]} UTC (in {mins // 60}h {mins % 60:02d}m)"

    def tool_why(self, args: dict[str, Any], bot: str) -> Reply:
        latest = self._latest_decisions(bot)
        pair = args.get("pair")
        if pair and pair not in latest:
            return Reply(
                f"No decision on {pair} yet. The next 4H candle closes at {self._next_close()}."
            )
        pairs = [pair] if pair else sorted(latest)
        if not pairs:
            return Reply("No decisions yet: the bot decides at each 4H candle close.")
        rows = [
            [p, ms_to_iso(latest[p].signal_ts + TF_MS)[:16].replace("T", " "),
             "allowed" if latest[p].allowed else "denied", latest[p].rule, latest[p].reason]
            for p in pairs
        ]  # fmt: skip
        head = f"Latest decision per pair. Next 4H close: {self._next_close()}."
        table = _table(["pair", "decided (UTC)", "result", "rule", "reason"], rows)
        data = {"decisions": [dict(zip(DEC_COLS, r, strict=True)) for r in rows]}
        return Reply(head, table, data=data)

    def tool_brain(self, args: dict[str, Any], bot: str) -> Reply:
        b = self._snap(bot).get("brain") or {}
        m = b.get("memory") or {}
        if not m:
            return Reply(f"{NAME} has no memory yet: it starts remembering as the bot trades.")
        src = ", ".join(f"{v} {k}" for k, v in (m.get("by_source") or {}).items())
        text = f"{NAME} remembers {m.get('trades')} closed trades ({src})."
        grad = [g for g in b.get("graduation") or [] if g.get("why") != "no graduation needed"]
        if grad:
            text += (
                " Graduated from paper: "
                + (", ".join(g["pair"] for g in grad if g["graduated"]) or "none yet")
                + "."
            )
        rows = [
            [x["mistake"], x["losses"], f"{x['lost_r']:+.2f}R", x.get("status", "")]
            for x in (b.get("mistakes") or [])[:8]
        ]
        table = _table(["mistake", "losses", "R lost", "what it does"], rows) if rows else None
        data = {"memory": m, "mistakes": (b.get("mistakes") or [])[:8], "graduation": grad}
        return Reply(text, table, data=data)

    # -------------------------------------------------------------- actions (confirmed when risky)
    def _prepare(self, name: str, args: dict[str, Any], bot: str, prompt: str) -> Reply:
        self.pending = {"id": secrets.token_urlsafe(8), "tool": name, "args": args, "bot": bot,
                        "prompt": prompt, "expires": time.time() + CONFIRM_TTL_S}  # fmt: skip
        end = "" if prompt.rstrip().endswith((".", "?", "!")) else "."
        return Reply(
            prompt.rstrip() + end + " Type yes to confirm or no to cancel.",
            confirm={"id": self.pending["id"], "prompt": prompt},
            data={"status": "PENDING_CONFIRMATION", "what": prompt},
        )

    def confirm(self) -> Reply:
        p, self.pending = self.pending, None
        if p is None:
            return Reply("There is nothing waiting for confirmation.")
        if p["expires"] < time.time():
            return Reply("That request expired (2 minutes); ask again.")
        return getattr(self, f"do_{p['tool']}")(p["args"], p["bot"])

    def _mode(self, bot: str) -> str:
        return str(self._snap(bot)["meta"].get("mode"))

    def tool_start_day(self, args: dict[str, Any], bot: str) -> Reply:
        if self._mode(bot) == "live" and not (self._snap(bot).get("bot") or {}).get("running"):
            return self._prepare(
                "start_day", args, bot, f"Start the day on {bot} in LIVE mode with real funds?"
            )
        return self.do_start_day(args, bot)

    def do_start_day(self, args: dict[str, Any], bot: str) -> Reply:
        from .live_bot import write_control

        msgs, approval = [], None
        if not (self._snap(bot).get("bot") or {}).get("running"):
            ok, msg, approval = self._act({"action": "start", "bot": bot})
            msgs.append(msg if ok or approval else f"could not start the bot: {msg}")
        ok, msg, _ = self._act({"action": "resume", "bot": bot})
        msgs.append(msg)
        write_control(self._dir(bot), day_started_ms=self.now_ms())
        text = (
            f"Good morning. {bot}: "
            + "; ".join(m for m in msgs if m)
            + f". Next 4H close {self._next_close()}."
        )
        return Reply(text, approval=approval)

    def tool_end_day(self, args: dict[str, Any], bot: str) -> Reply:
        if args.get("close_positions") and self._snap(bot).get("open_positions"):
            n = len(self._snap(bot)["open_positions"])
            return self._prepare(
                "end_day",
                args,
                bot,
                f"End the day on {bot} and market-sell all {n} open position(s)?",
            )
        return self.do_end_day(args, bot)

    def do_end_day(self, args: dict[str, Any], bot: str) -> Reply:
        from .live_bot import read_control, write_control

        self._act({"action": "pause", "bot": bot})
        extra = ""
        if args.get("close_positions"):
            msg = self._act({"action": "close", "pair": "ALL", "bot": bot})[1]
            extra = f" {msg}."
        started = read_control(self._dir(bot)).get("day_started_ms")
        write_control(self._dir(bot), day_started_ms=None, armed_pairs={})
        q = self._quote(bot)
        e = self._earn(bot, "today")
        if started:
            lo = int(started)
            j = self._dir(bot) / "journal.csv"
            sel = [
                t
                for t in (read_journal(j) if j.exists() else [])
                if t.is_closed and (t.exit_ts or 0) >= lo
            ]
            e = {"trades": len(sel), "pnl": round(sum(t.pnl or 0 for t in sel), 2),
                 "wins": sum(1 for t in sel if (t.pnl or 0) > 0)}  # fmt: skip
        opn = self._snap(bot).get("open_positions") or []
        unreal = sum(p.get("unrealized") or 0 for p in opn)
        text = (
            f"Day ended on {bot}: new entries paused (stops and targets keep running).{extra} "
            f"Today: {e['trades']} closed trades, {e['wins']} won, realized {_money(e['pnl'], q)}. "
            f"{len(opn)} position(s) still open, unrealized {_money(unreal, q)}."
        )
        return Reply(text, data={"day": e})

    def tool_start_bot(self, args: dict[str, Any], bot: str) -> Reply:
        if self._mode(bot) == "live":
            return self._prepare(
                "start_bot", args, bot, f"Start {bot} in LIVE mode with real funds?"
            )
        return self.do_start_bot(args, bot)

    def do_start_bot(self, args: dict[str, Any], bot: str) -> Reply:
        _, msg, approval = self._act({"action": "start", "bot": bot})
        return Reply(msg, approval=approval)

    def tool_stop_bot(self, args: dict[str, Any], bot: str) -> Reply:
        return self._prepare(
            "stop_bot",
            args,
            bot,
            f"Stop the {bot} process? Open positions stay open (their stops are not watched "
            "while it is stopped).",
        )

    def do_stop_bot(self, args: dict[str, Any], bot: str) -> Reply:
        return Reply(self._act({"action": "stop", "bot": bot})[1])

    def tool_start_all(self, args: dict[str, Any], bot: str) -> Reply:
        _, msg, approval = self._act({"action": "start_all"})
        return Reply(msg, approval=approval)

    def tool_stop_all(self, args: dict[str, Any], bot: str) -> Reply:
        return self._prepare("stop_all", args, bot, "Stop every bot? Open positions stay open.")

    def do_stop_all(self, args: dict[str, Any], bot: str) -> Reply:
        return Reply(self._act({"action": "stop_all"})[1])

    def tool_pause(self, args: dict[str, Any], bot: str) -> Reply:
        return Reply(self._act({"action": "pause", "bot": bot})[1])

    def tool_resume(self, args: dict[str, Any], bot: str) -> Reply:
        return Reply(self._act({"action": "resume", "bot": bot})[1])

    def tool_retrain(self, args: dict[str, Any], bot: str) -> Reply:
        return Reply(self._act({"action": "retrain", "bot": bot})[1])

    def tool_collect(self, args: dict[str, Any], bot: str) -> Reply:
        return Reply(self._act({"action": "collect", "bot": bot})[1])

    def tool_close_position(self, args: dict[str, Any], bot: str) -> Reply:
        pair = args.get("pair")
        opn = {p["pair"]: p for p in self._snap(bot).get("open_positions") or []}
        if not opn:
            return Reply(f"{bot} has no open positions to close.")
        if not pair:
            return Reply("Which one? Open: " + ", ".join(sorted(opn)) + " (or say close all).")
        if pair != "ALL" and pair not in opn:
            return Reply(f"There is no open {pair} position. Open: {', '.join(sorted(opn))}.")
        q = self._quote(bot)
        if pair == "ALL":
            unreal = sum(p.get("unrealized") or 0 for p in opn.values())
            what = f"all {len(opn)} positions (unrealized {_money(unreal, q)})"
        else:
            what = f"the {pair} position (unrealized {_money(opn[pair].get('unrealized'), q)})"
        return self._prepare("close_position", {"pair": pair}, bot, f"Market-sell {what} on {bot}?")

    def do_close_position(self, args: dict[str, Any], bot: str) -> Reply:
        return Reply(self._act({"action": "close", "pair": args["pair"], "bot": bot})[1])

    def tool_execute_trade(self, args: dict[str, Any], bot: str) -> Reply:
        pair = args.get("pair")
        pairs = self._pairs(bot)
        if not pair:
            return Reply(
                "Which pair? This bot trades "
                + ", ".join(pairs)
                + ". For example: execute trade BTC."
            )
        if pair not in pairs:
            return Reply(
                f"{bot} does not trade {pair} (it trades {', '.join(pairs)}). "
                "Add it to the bot's pairs first."
            )
        snap = self._snap(bot)
        if pair in {p["pair"] for p in snap.get("open_positions") or []}:
            return Reply(f"{bot} already holds {pair}: one position per pair.")
        latest = self._latest_decisions(bot).get(pair)
        last = (
            f" Its latest check: {'allowed' if latest.allowed else 'denied'} "
            f"({latest.rule}): {latest.reason}"
            if latest
            else ""
        )
        if not (snap.get("control") or {}).get("entries_paused"):
            return Reply(
                f"Entries are open, so {bot} buys {pair} at the next 4H close "
                f"({self._next_close()}) if "
                f"the signal passes all nine rules and {NAME}'s checks; nothing to arm.{last}"
            )
        return self._prepare(
            "execute_trade",
            {"pair": pair},
            bot,
            f"Arm {pair} on {bot}: take its next signal that passes all nine rules (next close "
            f"{self._next_close()}), even though the day is ended? "
            f"It stays armed for {ARM_HOURS}h.{last}",
        )

    def do_execute_trade(self, args: dict[str, Any], bot: str) -> Reply:
        from .live_bot import arm_pair

        arm_pair(self._dir(bot), args["pair"], self.now_ms() + ARM_HOURS * 3_600_000)
        return Reply(
            f"{args['pair']} is armed on {bot}: the bot buys it at the first 4H close where the "
            f"signal passes every rule (checked next at {self._next_close()}); the size comes from "
            "the stop distance and the risk limit as always."
        )
