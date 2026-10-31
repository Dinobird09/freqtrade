"""Bot health: the order log, uptime sessions, a scorecard against industry benchmarks, alerts.

Files in the bot's ``state_dir``::

    orders.jsonl   every order the bot placed (one JSON object per line, append-only):
                   {ts, pair, side, type, qty_requested, qty_filled, price, notional,
                   fee_quote, liquidity, status, order_id}
                   type: market | limit | post-only | swap; liquidity: maker | taker | unknown;
                   status: filled | partial | cancelled | rejected | unknown
    uptime.json    run sessions [[start_ms, last_ms], ...], updated with the heartbeat
    alerts.jsonl   every alert the bot (or its dashboard) raised, newest last

The gateways (``live_exchange.CcxtGateway`` / ``PaperBroker``, ``wallets.WalletBroker``)
record each order in their ``order_log`` list; the bot calls :func:`drain_order_log` every
step to append them to ``orders.jsonl``.

:func:`scorecard` reads ``journal.csv`` (closed trades), ``orders.jsonl``, ``uptime.json`` and
``state.json`` and grades the bot against these benchmarks (status good / warn / bad, or
"not enough data yet", which never passes):

    PnL               5-200% a year; about 1% a month (low-volatility bots) up to 10-15% a
                      month (high-volatility bots); above 200% a year is unrealistic or
                      excessive risk
    Uptime            3 months minimum, 12+ months excellent
    Turnover          10x the equity a day minimum, 30-40x excellent (a market-making / grid
                      benchmark: a 4H trend bot is expected to be far below it)
    Matched orders    60%+ (pro bots), 2%+ (base bots)
    Makers / takers   100% maker preferred
    Commission fee    < 0.1% per trade

Alerts (:class:`Alerter`): every event is appended to ``alerts.jsonl`` and, when
``alerts.webhook_url`` is set in bot.json, POSTed as JSON ``{event, bot, mode, exchange,
message, ts}`` from a background thread (5 s timeout, a small queue: the trading loop never
waits for it and never fails because of it). Only company-approved automation receives the
webhook: a Zapier "Catch Hook" (https://hooks.zapier.com/...) or an n8n Webhook node. Slack,
Telegram or email notifications are steps in that Zapier / n8n flow, never called from here.
"""

from __future__ import annotations

import calendar
import json
import logging
import math
import queue
import statistics
import threading
import time
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .journal import atomic_write_text, read_journal
from .models import DAY_MS, Trade


log = logging.getLogger("trendbot.health")

ORDERS_FILE = "orders.jsonl"
UPTIME_FILE = "uptime.json"
ALERTS_FILE = "alerts.jsonl"
UPTIME_WRITE_MS = 30_000  # uptime.json is rewritten at most this often while running
MAX_SESSIONS = 5000
YEAR_DAYS = 365.25
MONTH_DAYS = YEAR_DAYS / 12

GOOD, WARN, BAD, NODATA = "good", "warn", "bad", "nodata"
NODATA_TEXT = "not enough data yet"
DASH = "-"
CATEGORIES = ("low-volatility", "balanced", "high-volatility")
LOW_VOL_DAILY_PCT = 1.0  # stdev of daily returns below this: low-volatility
HIGH_VOL_DAILY_PCT = 3.0  # above this: high-volatility (in between: balanced)
# the monthly return a bot of each category should make (percent): (good from, good up to)
MONTHLY_TARGET = {
    "low-volatility": (0.8, 3.0),
    "balanced": (1.0, 10.0),
    "high-volatility": (10.0, 15.0),
}
MONTHLY_IDEAL = {
    "low-volatility": "about 1% a month",
    "balanced": "1-10% a month",
    "high-volatility": "10-15% a month",
}


# ---------------------------------------------------------------------- JSONL files
def append_jsonl(
    path: str | Path,
    rows: Sequence[Mapping[str, Any]],
    *,
    attempts: int = 5,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Append ``rows`` to a JSON-lines file in one write; False if it stayed locked.

    On Windows another program (the dashboard, an antivirus scan, a text editor) may hold
    the file for a moment: the append is retried ``attempts`` times with short sleeps, then
    skipped (the caller keeps the rows and tries again on its next step).
    """
    if not rows:
        return True
    p = Path(path)
    text = "".join(json.dumps(r, sort_keys=True, default=str) + "\n" for r in rows)
    for i in range(attempts):
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8", newline="") as fh:
                fh.write(text)
            return True
        except PermissionError as exc:
            if i == attempts - 1:
                log.warning("%s is locked by another program: not written now (%s)", p.name, exc)
                return False
            sleep(0.05 * (i + 1))
    return False


def read_jsonl(path: str | Path, tail: int | None = None) -> list[dict[str, Any]]:
    """The objects of a JSON-lines file (a torn or invalid line is skipped)."""
    p = Path(path)
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for line in lines[-tail:] if tail else lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def drain_order_log(gateway: Any, state_dir: str | Path) -> int:
    """Append the orders ``gateway`` recorded (its ``order_log`` list) to orders.jsonl.

    Rows leave the list only once they are on disk: a locked file keeps them for the next
    call. Gateways without an order log (test doubles) are ignored. Returns rows written.
    """
    book = getattr(gateway, "order_log", None)
    if not isinstance(book, list) or not book:
        return 0
    rows = list(book)
    if not append_jsonl(Path(state_dir) / ORDERS_FILE, rows):
        return 0
    del book[: len(rows)]
    return len(rows)


# ---------------------------------------------------------------------- uptime
def gap_ms_for(poll_seconds: float) -> int:
    """A silence longer than this (3 loop intervals + a minute) is downtime."""
    return int(3 * float(poll_seconds) * 1000 + 60_000)


def read_uptime(state_dir: str | Path) -> dict[str, Any]:
    p = Path(state_dir) / UPTIME_FILE
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"sessions": []}
    sessions = [
        [int(s[0]), int(s[1])]
        for s in raw.get("sessions") or []
        if isinstance(s, list | tuple) and len(s) == 2
    ]
    return {**raw, "sessions": sessions}


class UptimeTracker:
    """Run sessions in uptime.json, extended with every heartbeat.

    A new session starts when the process starts and when the loop was silent for longer
    than :func:`gap_ms_for` (a frozen PC, a laptop asleep). The file is rewritten at most
    every 30 s (and always when a session starts or the bot stops).
    """

    def __init__(self, state_dir: str | Path, poll_seconds: float) -> None:
        self.dir = Path(state_dir)
        self.poll_seconds = float(poll_seconds)
        self.gap_ms = gap_ms_for(poll_seconds)
        self.sessions: list[list[int]] = read_uptime(self.dir)["sessions"]
        self._open = False  # a session of this process is the last one
        self._written: int | None = None

    def beat(self, now_ms: int, *, final: bool = False) -> None:
        now = int(now_ms)
        last = self.sessions[-1] if self.sessions else None
        if not self._open or last is None or now - last[1] > self.gap_ms or now < last[0]:
            self.sessions.append([now, now])
            del self.sessions[:-MAX_SESSIONS]
            self._open = True
            final = True
        else:
            last[1] = max(last[1], now)
        if final or self._written is None or now - self._written >= UPTIME_WRITE_MS:
            self.flush(now)

    def flush(self, now_ms: int | None = None) -> None:
        body = {
            "sessions": self.sessions,
            "poll_seconds": self.poll_seconds,
            "gap_ms": self.gap_ms,
        }
        try:
            atomic_write_text(self.dir / UPTIME_FILE, json.dumps(body), attempts=3)
        except OSError as exc:  # locked by the dashboard: the next beat writes it
            log.debug("uptime.json not written: %s", exc)
            return
        self._written = int(now_ms) if now_ms is not None else self._written

    def stop(self, now_ms: int) -> None:
        """The process stops: the session ends here, the next start opens a new one."""
        self.beat(now_ms, final=True)
        self._open = False


def _up_intervals(sessions: Sequence[Sequence[int]], gap_ms: int, now: int) -> list[list[int]]:
    """Merged up-time intervals: a restart within ``gap_ms`` is continuous, and a bot whose
    last session is fresh is up until ``now``."""
    out: list[list[int]] = []
    for s, e in sorted((int(a), int(b)) for a, b in sessions):
        if out and s - out[-1][1] <= gap_ms:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    if out and 0 <= now - out[-1][1] <= gap_ms:
        out[-1][1] = now
    return out


def availability(
    sessions: Sequence[Sequence[int]], gap_ms: int, now: int, window_ms: int
) -> float | None:
    """Percent of the window (since the first start, at most ``window_ms``) the bot ran."""
    if not sessions:
        return None
    first = min(int(s[0]) for s in sessions)
    lo = max(now - window_ms, first)
    if now <= lo:
        return None
    up = 0
    for s, e in _up_intervals(sessions, gap_ms, now):
        up += max(0, min(e, now) - max(s, lo))
    return min(100.0, up / (now - lo) * 100.0)


# ---------------------------------------------------------------------- the scorecard
def _metric(
    key: str,
    label: str,
    value: float | None,
    display: str,
    ideal: str,
    status: str,
    comment: str,
) -> dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "value": None if value is None or not math.isfinite(value) else round(value, 4),
        "display": display if status != NODATA or value is not None else DASH,
        "ideal": ideal,
        "status": status,
        "comment": comment,
    }


def _nodata(key: str, label: str, ideal: str, why: str) -> dict[str, Any]:
    return _metric(key, label, None, DASH, ideal, NODATA, f"{NODATA_TEXT}: {why}")


def _month_start(ms: int) -> int:
    d = datetime.fromtimestamp(ms / 1000, UTC)
    return int(datetime(d.year, d.month, 1, tzinfo=UTC).timestamp() * 1000)


def _next_month(ms: int) -> int:
    d = datetime.fromtimestamp(ms / 1000, UTC)
    y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return int(datetime(y, m, 1, tzinfo=UTC).timestamp() * 1000)


def _year_start(year: int) -> int:
    return int(datetime(year, 1, 1, tzinfo=UTC).timestamp() * 1000)


def _realized_before(closed: Sequence[Trade], ts: int) -> float:
    return sum(t.pnl or 0.0 for t in closed if t.exit_ts is not None and t.exit_ts < ts)


def _pnl_between(closed: Sequence[Trade], lo: int, hi: int) -> float:
    return sum(t.pnl or 0.0 for t in closed if t.exit_ts is not None and lo <= t.exit_ts < hi)


def monthly_table(
    closed: Sequence[Trade], start_equity: float, start_ms: int, now: int
) -> list[dict[str, Any]]:
    """Each calendar month from the first start to now: realized P&L and return %."""
    rows = []
    m = _month_start(start_ms)
    while m <= now:
        nxt = _next_month(m)
        base = start_equity + _realized_before(closed, m)
        pnl = _pnl_between(closed, m, nxt)
        d = datetime.fromtimestamp(m / 1000, UTC)
        rows.append(
            {
                "month": f"{d.year:04d}-{d.month:02d}",
                "pnl": round(pnl, 2),
                "return_pct": round(pnl / base * 100.0, 3) if base > 0 else None,
                "current": m <= now < nxt,
                "days": calendar.monthrange(d.year, d.month)[1],
            }
        )
        m = nxt
    return rows


def annual_table(
    closed: Sequence[Trade], start_equity: float, start_ms: int, now: int
) -> list[dict[str, Any]]:
    rows = []
    first = datetime.fromtimestamp(start_ms / 1000, UTC).year
    last = datetime.fromtimestamp(now / 1000, UTC).year
    for y in range(first, last + 1):
        lo, hi = _year_start(y), _year_start(y + 1)
        base = start_equity + _realized_before(closed, lo)
        pnl = _pnl_between(closed, lo, hi)
        rows.append(
            {
                "year": y,
                "pnl": round(pnl, 2),
                "return_pct": round(pnl / base * 100.0, 3) if base > 0 else None,
                "partial": start_ms > lo or now < hi,
            }
        )
    return rows


def daily_equity(
    closed: Sequence[Trade], start_equity: float, start_ms: int, now: int
) -> list[tuple[int, float]]:
    """Realized equity at the end of each UTC day from the first start to today."""
    day0 = start_ms // DAY_MS * DAY_MS
    exits = sorted((t.exit_ts, t.pnl or 0.0) for t in closed if t.exit_ts is not None)
    out, eq, i = [], start_equity, 0
    d = day0
    while d <= now:
        end = d + DAY_MS
        while i < len(exits) and exits[i][0] < end:
            eq += exits[i][1]
            i += 1
        out.append((d, eq))
        d = end
    return out


def volatility_category(daily_vol_pct: float | None) -> str | None:
    if daily_vol_pct is None:
        return None
    if daily_vol_pct < LOW_VOL_DAILY_PCT:
        return "low-volatility"
    if daily_vol_pct <= HIGH_VOL_DAILY_PCT:
        return "balanced"
    return "high-volatility"


def _pct(x: float | None, nd: int = 2) -> str:
    return DASH if x is None else f"{x:+.{nd}f}%"


def _start_ms(sessions: Sequence[Sequence[int]], trades: Sequence[Trade], orders: Sequence[Any]):
    cands = [min(int(s[0]) for s in sessions)] if sessions else []
    if not cands:
        cands = [t.entry_ts for t in trades] + [int(o["ts"]) for o in orders if o.get("ts")]
    return min(cands) if cands else None


def scorecard(  # noqa: C901 - one block per benchmark
    state_dir: str | Path,
    *,
    now_ms: int | None = None,
    equity: float | None = None,
    style: str = "trend",
) -> dict[str, Any]:
    """The bot's health scorecard (pure: reads its state dir, writes nothing).

    ``equity``: the current (mark-to-market) equity; default the starting equity plus the
    realized P&L. ``style``: "trend" (a 4H trend bot: the turnover benchmark is not its
    game) or "market_making" / "grid".
    """
    d = Path(state_dir)
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    try:
        state = json.loads((d / "state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    trades = read_journal(d / "journal.csv") if (d / "journal.csv").exists() else []
    closed = sorted((t for t in trades if t.is_closed), key=lambda t: (t.exit_ts, t.trade_id))
    orders = read_jsonl(d / ORDERS_FILE)
    up = read_uptime(d)
    sessions = up["sessions"]
    gap = int(up.get("gap_ms") or gap_ms_for(1.0))
    start_eq = float(state.get("starting_equity") or 0.0) or 10_000.0
    eq = float(equity) if equity is not None else start_eq + sum(t.pnl or 0.0 for t in closed)
    start = _start_ms(sessions, trades, orders)
    days = (now - start) / DAY_MS if start is not None else 0.0
    metrics: list[dict[str, Any]] = []

    # ---- volatility category (first: the monthly PnL target depends on it)
    curve = daily_equity(closed, start_eq, start, now) if start is not None else []
    rets = [b / a - 1 for (_, a), (_, b) in pairwise(curve) if a > 0]
    vol = statistics.stdev(rets) * 100.0 if len(rets) >= 14 else None
    category = volatility_category(vol)
    vol_ideal = "any: spread bots across categories"
    if category is None:
        metrics.append(
            _nodata("volatility", "Volatility category", vol_ideal, "needs 14 days of returns")
        )
    else:
        metrics.append(
            _metric(
                "volatility",
                "Volatility category",
                vol,
                f"{category} ({vol:.2f}%/day)",
                vol_ideal,
                GOOD,
                f"daily returns swing {vol:.2f}% (stdev): a {category} bot",
            )
        )

    # ---- PnL
    cagr = monthly_avg = None
    if days >= 30 and start_eq > 0 and eq > 0:
        growth = eq / start_eq
        cagr = (growth ** (YEAR_DAYS / days) - 1) * 100.0
        monthly_avg = (growth ** (MONTH_DAYS / days) - 1) * 100.0
    elif days >= 30:
        cagr = monthly_avg = -100.0
    ideal_y = "5-200% a year"
    if cagr is None:
        metrics.append(
            _nodata("pnl_annual", "Annualised return", ideal_y, f"{days:.0f} of 30 days live")
        )
    else:
        if cagr > 200:
            st, why = WARN, "above 200% a year: unrealistic to keep, or excessive risk"
        elif cagr >= 5:
            st, why = GOOD, "inside the 5-200% a year band"
        elif cagr >= 0:
            st, why = WARN, "below 5% a year: cash-like returns for trading risk"
        else:
            st, why = BAD, "losing money since the start"
        metrics.append(
            _metric("pnl_annual", "Annualised return", cagr, _pct(cagr), ideal_y, st, why)
        )
    cat_m = category or "balanced"
    ideal_m = MONTHLY_IDEAL[cat_m] + ("" if category else " (category not known yet)")
    if monthly_avg is None:
        metrics.append(
            _nodata("pnl_monthly", "Average monthly return", ideal_m, f"{days:.0f} of 30 days")
        )
    else:
        lo, hi = MONTHLY_TARGET[cat_m]
        if monthly_avg < 0:
            st, why = BAD, "losing money on average each month"
        elif monthly_avg < lo:
            st, why = WARN, f"below the {MONTHLY_IDEAL[cat_m]} a {cat_m} bot should make"
        elif monthly_avg > hi * 1.5:
            st, why = WARN, f"well above {MONTHLY_IDEAL[cat_m]}: check the risk it takes"
        else:
            st, why = GOOD, f"in line with {MONTHLY_IDEAL[cat_m]} for a {cat_m} bot"
        metrics.append(
            _metric(
                "pnl_monthly", "Average monthly return", monthly_avg, _pct(monthly_avg), ideal_m,
                st, why,
            )
        )  # fmt: skip

    # ---- uptime
    running_ms = sum(e - s for s, e in _up_intervals(sessions, gap, now)) if sessions else 0
    av7 = availability(sessions, gap, now, 7 * DAY_MS) if days >= 1 else None
    av30 = availability(sessions, gap, now, 30 * DAY_MS) if days >= 7 else None
    ideal_u = "3 months minimum, 12+ months excellent"
    if not sessions:
        metrics.append(_nodata("uptime", "Days live", ideal_u, "no uptime.json yet"))
    else:
        if days >= 365:
            st, why = GOOD, "12+ months live: an excellent track record"
        elif days >= 90:
            st, why = GOOD, "past the 3-month minimum track record"
        else:
            st, why = WARN, f"{90 - days:.0f} more days to the 3-month minimum track record"
        metrics.append(_metric("uptime", "Days live", days, f"{days:.1f} days", ideal_u, st, why))
    for key, label, av, need in (
        ("availability_7d", "Availability, 7 days", av7, "needs 1 day live"),
        ("availability_30d", "Availability, 30 days", av30, "needs 7 days live"),
    ):
        if av is None:
            metrics.append(_nodata(key, label, "99%+", need))
            continue
        if av >= 99:
            st, why = GOOD, "running almost all the time"
        elif av >= 95:
            st, why = WARN, "some downtime: check the PC, its sleep settings and the network"
        else:
            st, why = BAD, "often down: open positions are not watched while it is off"
        metrics.append(_metric(key, label, av, f"{av:.1f}%", "99%+", st, why))

    # ---- orders: turnover, matched, makers / takers, fees
    filled = [o for o in orders if o.get("status") in ("filled", "partial")]
    notional = lambda rows: sum(float(o.get("notional") or 0.0) for o in rows)  # noqa: E731
    trend = style == "trend"
    expected = (
        "expected for a 4H trend bot (a few trades a month); the benchmark is for "
        "market-making / grid bots"
    )
    ideal_t = "10x a day minimum, 30-40x excellent"
    if days < 1:
        why = "needs 1 day live"
    elif not orders:
        why = "no orders logged yet (orders.jsonl)"
    elif eq <= 0:
        why = "no equity"
    else:
        why = ""
    if why:
        metrics.append(_nodata("turnover_24h", "Turnover, 24 hours", ideal_t, why))
    else:
        t24 = notional([o for o in filled if now - int(o.get("ts") or 0) <= DAY_MS]) / eq
        metrics.append(_turnover("turnover_24h", "Turnover, 24 hours", t24, 1.0, trend, expected))
    ideal_30 = "300x in 30 days (10x a day) minimum"
    if not orders or days < 7:
        need = "needs 7 days live" if days < 7 else "no orders logged yet (orders.jsonl)"
        metrics.append(_nodata("turnover_30d", "Turnover, 30 days", ideal_30, need))
    else:
        window = [e for ts, e in curve if ts >= now - 30 * DAY_MS] or [eq]
        avg_eq = sum(window) / len(window)
        span = min(30.0, days)
        t30 = notional([o for o in filled if now - int(o.get("ts") or 0) <= 30 * DAY_MS]) / avg_eq
        metrics.append(_turnover("turnover_30d", "Turnover, 30 days", t30, span, trend, expected))
    ideal_mt = "60%+ (pro bots), 2%+ (base bots)"
    if len(orders) < 5:
        metrics.append(
            _nodata("matched", "Matched orders", ideal_mt, f"{len(orders)} of 5 orders placed")
        )
    else:
        matched = len(filled) / len(orders) * 100.0
        if matched >= 60:
            st, why = GOOD, "most orders fill (pro-bot level)"
        elif matched >= 2:
            st, why = WARN, "base-bot level: many orders are cancelled or refused"
        else:
            st, why = BAD, "almost nothing fills: check prices, sizes and the exchange minimums"
        metrics.append(
            _metric(
                "matched", "Matched orders", matched,
                f"{matched:.1f}% ({len(filled)}/{len(orders)})", ideal_mt, st, why,
            )
        )  # fmt: skip
    known = [o for o in filled if o.get("liquidity") in ("maker", "taker")]
    if len(known) < 3:
        metrics.append(
            _nodata("maker", "Maker share", "100% maker", f"{len(known)} of 3 filled orders")
        )
    else:
        total = notional(known)
        share = notional([o for o in known if o["liquidity"] == "maker"]) / total * 100.0
        if share >= 90:
            st, why = GOOD, "orders rest in the book and earn the lower maker fee"
        elif share >= 40:
            st, why = WARN, "part taker: stops / targets sell at market"
        else:
            st, why = (
                BAD,
                (
                    "mostly taker: market orders pay the higher fee"
                    + ("; turn execution.maker_first on for entries" if trend else "")
                ),
            )
        metrics.append(
            _metric("maker", "Maker share", share, f"{share:.1f}%", "100% maker", st, why)
        )
    fee_known = [o for o in filled if o.get("fee_quote") is not None]
    if len(fee_known) < 3 or notional(fee_known) <= 0:
        metrics.append(
            _nodata("fees", "Commission fee", "< 0.1% per trade", f"{len(fee_known)} of 3 fees")
        )
    else:
        fee_pct = sum(float(o["fee_quote"]) for o in fee_known) / notional(fee_known) * 100.0
        if fee_pct < 0.1:
            st, why = GOOD, "cheap execution"
        elif fee_pct <= 0.2:
            st, why = WARN, "above 0.1%: maker orders or a fee discount would help"
        else:
            st, why = BAD, "expensive: fees eat a large part of each trade's edge"
        unknown = len(filled) - len(fee_known)
        if unknown:
            why += f" ({unknown} order(s) paid fees in another coin: not counted)"
        metrics.append(
            _metric(
                "fees", "Commission fee", fee_pct, f"{fee_pct:.3f}%", "< 0.1% per trade", st, why
            )
        )

    counts = {k: sum(1 for m in metrics if m["status"] == k) for k in (GOOD, WARN, BAD, NODATA)}
    months = monthly_table(closed, start_eq, start, now) if start is not None else []
    return {
        "generated_at": now,
        "start_ms": start,
        "days_live": round(days, 3),
        "starting_equity": round(start_eq, 2),
        "equity": round(eq, 2),
        "style": style,
        "category": category,
        "daily_vol_pct": None if vol is None else round(vol, 4),
        "cagr_pct": None if cagr is None else round(cagr, 3),
        "monthly_avg_pct": None if monthly_avg is None else round(monthly_avg, 3),
        "monthly": months,
        "current_month": next((m for m in months if m["current"]), None),
        "annual": annual_table(closed, start_eq, start, now) if start is not None else [],
        "uptime": {
            "sessions": len(sessions),
            "running_days": round(running_ms / DAY_MS, 3),
            "days_live": round(days, 3),
            "availability_7d": None if av7 is None else round(av7, 2),
            "availability_30d": None if av30 is None else round(av30, 2),
        },
        "orders": {
            "placed": len(orders),
            "filled": len(filled),
            "notional": round(notional(filled), 2),
            "fees": round(sum(float(o["fee_quote"]) for o in fee_known), 4),
        },
        "metrics": metrics,
        "counts": counts,
        "summary": _summary(counts, category),
    }


def _turnover(
    key: str, label: str, x: float, days: float, trend: bool, expected: str
) -> dict[str, Any]:
    per_day = x / max(days, 1e-9)
    ideal = "10x a day minimum, 30-40x excellent" if days <= 1 else "300x in 30 days minimum"
    shown = f"{x:.2f}x" + ("" if days <= 1 else f" ({per_day:.2f}x a day)")
    if per_day >= 30:
        st, why = GOOD, "excellent turnover"
    elif per_day >= 10:
        st, why = GOOD, "above the 10x-a-day minimum"
    elif trend:
        st, why = WARN, f"{per_day:.2f}x a day: {expected}"
    else:
        st, why = BAD, "below the 10x-a-day minimum for a market-making / grid bot"
    return _metric(key, label, x, shown, ideal, st, why)


def _summary(counts: Mapping[str, int], category: str | None) -> str:
    parts = [f"{counts[GOOD]} good", f"{counts[WARN]} to watch", f"{counts[BAD]} bad"]
    if counts[NODATA]:
        parts.append(f"{counts[NODATA]} {NODATA_TEXT}")
    return " · ".join(parts) + (f" · {category} bot" if category else "")


def fleet_scorecard(entries: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
    """Several bots: each one's category and grade, and how diversified the fleet is.

    ``entries``: name -> a :func:`scorecard` result, or a state dir to score (``kwargs``
    go to :func:`scorecard`).
    """
    bots = []
    for name, e in entries.items():
        card = e if isinstance(e, Mapping) and "metrics" in e else scorecard(e, **kwargs)
        bots.append(
            {
                "name": name,
                "category": card.get("category"),
                "counts": card.get("counts"),
                "summary": card.get("summary"),
            }
        )
    by_cat = {c: sum(1 for b in bots if b["category"] == c) for c in CATEGORIES}
    unknown = sum(1 for b in bots if b["category"] is None)
    line = ", ".join(f"{by_cat[c]} {c}" for c in CATEGORIES)
    if unknown:
        line += f", {unknown} not categorised yet (under 14 days of returns)"
    used = [c for c in CATEGORIES if by_cat[c]]
    hint = None
    if len(used) == 1:
        hint = (
            f"all categorised bots are {used[0]}: spread bots across categories "
            "(low-volatility / balanced / high-volatility) so one market regime cannot hit "
            "them all at once"
        )
    return {"bots": bots, "categories": {**by_cat, "unknown": unknown}, "line": line, "hint": hint}


# ---------------------------------------------------------------------- alerts
EVENTS = (
    "bot_started",
    "bot_stopped",
    "bot_error",  # a fatal error (LoopGuard) or several failed steps in a row
    "network_down",  # no successful exchange call for network_down_minutes
    "network_restored",
    "kill_switch",
    "capital_freeze",
    "trade_opened",
    "trade_closed",
    "heartbeat_stale",  # raised by the dashboard: a "running" bot stopped beating
)
ALERT_TIMEOUT_S = 5.0
ALERT_QUEUE = 50


def check_webhook_url(url: str) -> str:
    """The webhook must be https. Use only company-approved automation here: a Zapier
    "Catch Hook" (https://hooks.zapier.com/hooks/catch/...) or an n8n Webhook node URL;
    route Slack / Telegram / email from inside that Zapier / n8n flow."""
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError(
            f"alerts.webhook_url must be an https:// URL of a Zapier or n8n webhook (got {url!r})"
        )
    return url


@dataclass
class AlertSettings:
    """bot.json ``"alerts"``: {"webhook_url": "https://hooks.zapier.com/...", "events": [...]}."""

    webhook_url: str = ""  # Zapier / n8n webhooks only (https); empty: alerts.jsonl only
    events: tuple[str, ...] = EVENTS  # which events are POSTed (alerts.jsonl gets them all)
    network_down_minutes: float = 5.0
    error_steps: int = 5  # this many failed steps in a row raise bot_error
    bot_name: str = ""  # the "bot" field; default: the state dir's name

    def __post_init__(self) -> None:
        self.events = tuple(self.events)
        bad = sorted(set(self.events) - set(EVENTS))
        if bad:
            raise ValueError(f"alerts.events: unknown {bad}; choose from {list(EVENTS)}")
        if self.webhook_url:
            check_webhook_url(self.webhook_url)
        if self.network_down_minutes <= 0 or self.error_steps < 1:
            raise ValueError("alerts: network_down_minutes must be > 0 and error_steps >= 1")


def post_json(url: str, payload: Mapping[str, Any], timeout: float = ALERT_TIMEOUT_S) -> None:
    """POST ``payload`` as JSON (https only); raises on a network error or an HTTP error."""
    check_webhook_url(url)
    req = urllib.request.Request(  # noqa: S310 - https enforced just above
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "trendbot-alerts"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        resp.read(1024)


class Alerter:
    """Raise alerts without ever blocking or breaking the caller.

    ``emit`` appends to alerts.jsonl at once and hands the webhook POST to a daemon thread
    through a small queue (a full queue drops the alert from the webhook, never from the
    file). A failing webhook is logged once until it works again. ``poster`` is injectable
    (tests post to a local server).
    """

    def __init__(
        self,
        settings: AlertSettings | Mapping[str, Any] | None,
        state_dir: str | Path,
        *,
        mode: str = "",
        exchange: str = "",
        poster: Callable[[str, Mapping[str, Any], float], None] | None = None,
        openalice: bool = True,
    ) -> None:
        if settings is None or isinstance(settings, Mapping):
            settings = AlertSettings(**dict(settings or {}))
        self.s = settings
        self.dir = Path(state_dir)
        self.mode, self.exchange = mode, exchange
        self.bot = settings.bot_name or self.dir.name
        self.poster = poster or post_json
        self._q: queue.Queue[dict[str, Any]] = queue.Queue(ALERT_QUEUE)
        self._thread: threading.Thread | None = None
        self._failing = False
        self._dropped = False
        self.sent = 0
        self.openalice = openalice  # also post alerts to the OpenAlice Inbox when set up there
        self._oa_checked, self._oa_on = -1e9, False

    def emit(self, event: str, message: str, **extra: Any) -> dict[str, Any] | None:
        try:
            rec = {
                "event": event,
                "bot": self.bot,
                "mode": self.mode,
                "exchange": self.exchange,
                "message": message,
                "ts": int(time.time() * 1000),
            }
            append_jsonl(self.dir / ALERTS_FILE, [{**rec, **extra}], attempts=3)
            if self.openalice and self._openalice_wanted():
                self._start()
                try:
                    self._q.put_nowait({**rec, "_to": "openalice"})
                except queue.Full:
                    pass
            if self.s.webhook_url and event in self.s.events:
                self._start()
                try:
                    self._q.put_nowait(rec)
                except queue.Full:
                    if not self._dropped:
                        log.warning("alert queue full: %s not sent to the webhook", event)
                        self._dropped = True
            return rec
        except Exception as exc:  # an alert must never stop the bot
            log.warning("alert %s not recorded: %s", event, exc)
            return None

    def _openalice_wanted(self) -> bool:
        """Is the OpenAlice Inbox set up for alerts? (re-read at most once a minute)"""
        now = time.monotonic()
        if now - self._oa_checked > 60:
            self._oa_checked = now
            try:
                from .openalice import load

                cfg = load(None)
                self._oa_on = bool(cfg["workspace"]) and bool(cfg["send"].get("alerts"))
            except Exception:  # a broken settings file means "off"
                self._oa_on = False
        return self._oa_on

    def _deliver(self, rec: Mapping[str, Any]) -> None:
        if rec.get("_to") == "openalice":
            from .openalice import alert_markdown, notify

            body = alert_markdown(str(rec["event"]).replace("_", " "), rec["message"], rec["bot"])
            err = notify(None, "alerts", body)
            if err:
                raise RuntimeError(f"OpenAlice: {err}")
            return
        self.poster(self.s.webhook_url, rec, ALERT_TIMEOUT_S)

    def _start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._worker, name="alerts", daemon=True)
            self._thread.start()

    def _worker(self) -> None:
        while True:
            rec = self._q.get()
            try:
                self._deliver(rec)
                self.sent += 1
                if self._failing:
                    log.info("alert webhook works again")
                self._failing = False
            except Exception as exc:  # network, HTTP 4xx/5xx, a bad URL: logged once
                if not self._failing:
                    log.warning("alert webhook failed (%s); alerts stay in alerts.jsonl", exc)
                self._failing = True
            finally:
                self._q.task_done()

    def flush(self, timeout: float = 3.0) -> bool:
        """Wait (at most ``timeout`` s) for queued POSTs; True when all were attempted."""
        deadline = time.monotonic() + timeout
        while self._q.unfinished_tasks:
            if time.monotonic() > deadline or self._thread is None:
                return False
            time.sleep(0.02)
        return True


def recent_alerts(state_dir: str | Path, n: int = 10) -> list[dict[str, Any]]:
    """The last ``n`` alerts, newest first."""
    return read_jsonl(Path(state_dir) / ALERTS_FILE, tail=n)[::-1]
