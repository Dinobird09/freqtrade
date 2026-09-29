"""Verification agents: independent checkers that re-derive what the charts and the bot show.

Each agent re-checks one thing from scratch and reports pass / warn / fail, per pair where it
applies. They run every 15 minutes while the bot runs (``verify`` job), and on demand from the
dashboard ("Verify now") or the terminal ("verify").

======================  ====================================================================
agent                   what it checks
======================  ====================================================================
data agent              cached 4H candles: no gaps or duplicates, sane OHLC, not stale
exchange agent          re-downloads the latest closed candles and compares them with the
                        cache (the chart must show what the exchange says)
indicator agent         recomputes EMA9 / EMA21 / EMA200, RSI(14) and the volume ratio with
                        its own code and compares them with what the bot decides on
chart agent             the dashboard's price chart (close, EMA21, EMA200) equals the
                        independent values candle by candle
price agent             the live price against a second exchange: a bad tick shows up as a
                        large divergence
ledger agent            the dashboard's equity, trade count and open positions equal the
                        journal; every closed trade's P&L and R add up
audit agent             re-checks every live trade against the nine rules on the candles it
                        traded on (invariants.live_journal_violations)
======================  ====================================================================

Guard: a pair FAILED by the data, exchange, indicator or price agent gets no new entries
(rule ``X_data_check``) until a later verification passes. Exits are never blocked.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Mapping, Sequence
from itertools import pairwise
from pathlib import Path
from typing import Any

from .data import candle_problem, load_candles_csv
from .journal import ms_to_iso, read_journal
from .models import Candle


log = logging.getLogger("trendbot.verify")

STATUS_FILE = "verify_status.json"
GUARD_AGENTS = ("data", "exchange", "indicator", "price")
RULE_ID = "X_data_check"
RANK = {"pass": 0, "warn": 1, "fail": 2, "skip": -1}


def _worst(statuses: Sequence[str]) -> str:
    real = [s for s in statuses if s != "skip"]
    return max(real, key=RANK.__getitem__) if real else "skip"


def _result(agent: str, pairs: Mapping[str, tuple[str, str]], note: str = "") -> dict[str, Any]:
    status = _worst([s for s, _ in pairs.values()]) if pairs else "skip"
    return {
        "agent": agent,
        "status": status,
        "note": note,
        "pairs": {p: {"status": s, "detail": d} for p, (s, d) in sorted(pairs.items())},
    }


# ---------------------------------------------------------------------- independent maths
def ind_ema(xs: Sequence[float], n: int) -> list[float | None]:
    """EMA seeded with the SMA of the first n values (written independently of indicators)."""
    out: list[float | None] = [None] * len(xs)
    if len(xs) < n:
        return out
    k = 2 / (n + 1)
    val = sum(xs[:n]) / n
    out[n - 1] = val
    for i in range(n, len(xs)):
        val = val + k * (xs[i] - val)
        out[i] = val
    return out


def ind_rsi(xs: Sequence[float], n: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(xs)
    if len(xs) <= n:
        return out
    ups = [max(xs[i] - xs[i - 1], 0.0) for i in range(1, len(xs))]
    downs = [max(xs[i - 1] - xs[i], 0.0) for i in range(1, len(xs))]
    au, ad = sum(ups[:n]) / n, sum(downs[:n]) / n
    for i in range(n, len(xs)):
        if i > n:
            au = (au * (n - 1) + ups[i - 1]) / n
            ad = (ad * (n - 1) + downs[i - 1]) / n
        out[i] = 50.0 if au == ad == 0 else 100.0 if ad == 0 else 100 - 100 / (1 + au / ad)
    return out


def _close(a: float | None, b: float | None, rel: float) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= rel * max(1.0, abs(a), abs(b))


# ---------------------------------------------------------------------- agents
def data_agent(
    candles: Mapping[str, Sequence[Candle]],
    tf_ms: int,
    now_ms: int,
    unreadable: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    out: dict[str, tuple[str, str]] = {
        p: ("fail", "cached candle file is corrupt: " + ": ".join(e))
        for p, e in (unreadable or {}).items()
    }
    for pair, cs in candles.items():
        if not cs:
            out[pair] = ("warn", "no candles cached yet")
            continue
        problems = []
        for a, b in pairwise(cs):
            if b.ts - a.ts != tf_ms:
                problems.append(f"gap or duplicate at {ms_to_iso(b.ts)}")
                break
        bad = next(((c, candle_problem(c)) for c in cs if candle_problem(c)), None)
        if bad:
            problems.append(f"bad candle {ms_to_iso(bad[0].ts)}: {bad[1]}")
        age = now_ms - (cs[-1].ts + tf_ms)
        if problems:
            out[pair] = ("fail", "; ".join(problems))
        elif age > 2 * tf_ms:
            out[pair] = (
                "warn",
                f"last candle closed {age / 3.6e6:.1f}h ago (the bot may be stopped)",
            )
        else:
            out[pair] = (
                "pass",
                f"{len(cs)} candles, contiguous, sane, up to {ms_to_iso(cs[-1].ts)}",
            )
    return _result("data", out)


def exchange_agent(
    candles: Mapping[str, Sequence[Candle]],
    fetch: Callable[[str, int], Sequence[Candle]] | None,
    n: int = 60,
) -> dict[str, Any]:
    if fetch is None:
        return _result("exchange", {}, "no exchange connection in this process")
    out = {}
    for pair, cs in candles.items():
        try:
            fresh = {c.ts: c for c in fetch(pair, n)}
        except Exception as exc:
            out[pair] = ("warn", f"could not re-download: {type(exc).__name__}: {exc}")
            continue
        both = [c for c in cs[-n:] if c.ts in fresh]
        diffs = [
            (c.ts, k)
            for c in both
            for k in ("open", "high", "low", "close")
            if not _close(getattr(c, k), getattr(fresh[c.ts], k), 1e-6)
        ]
        if not both:
            out[pair] = ("warn", "no overlapping candles to compare")
        elif diffs:
            ts, k = diffs[0]
            out[pair] = (
                "fail",
                f"{len(diffs)} values differ from the exchange, first {k} at {ms_to_iso(ts)}",
            )
        else:
            out[pair] = ("pass", f"last {len(both)} candles match the exchange")
    return _result("exchange", out)


def _independent(cs: Sequence[Candle], cfg: Any) -> dict[str, list[float | None]]:
    closes = [c.close for c in cs]
    vols = [c.volume for c in cs]
    lb = cfg.vol_lookback
    vavg = [None] * len(cs)
    for i in range(lb, len(cs)):
        vavg[i] = sum(vols[i - lb : i]) / lb
    return {
        "ema_fast": ind_ema(closes, cfg.ema_fast),
        "ema_slow": ind_ema(closes, cfg.ema_slow),
        "ema_regime": ind_ema(closes, cfg.ema_regime),
        "rsi": ind_rsi(closes, cfg.rsi_period),
        "vol_ratio": [(v / a) if a else None for v, a in zip(vols, vavg, strict=True)],
    }


def indicator_agent(
    candles: Mapping[str, Sequence[Candle]], cfg: Any, n: int = 50
) -> dict[str, Any]:
    from .indicators import compute_features

    out = {}
    for pair, cs in candles.items():
        if len(cs) < cfg.ema_regime + n:
            out[pair] = ("skip", "not enough candles for EMA200 yet")
            continue
        rows, mine = compute_features(cs, cfg), _independent(cs, cfg)
        bad = [
            (rows[i].ts, k)
            for i in range(len(cs) - n, len(cs))
            for k in mine
            if not _close(getattr(rows[i], k), mine[k][i], 1e-7)
        ]
        if bad:
            ts, k = bad[0]
            out[pair] = ("fail", f"{len(bad)} values differ, first {k} at {ms_to_iso(ts)}")
        else:
            out[pair] = (
                "pass",
                f"EMA9/21/200, RSI14 and volume ratio agree on the last {n} candles",
            )
    return _result("indicator", out)


def chart_agent(
    state_dir: Path, candles: Mapping[str, Sequence[Candle]], cfg: Any
) -> dict[str, Any]:
    from .dashboard import build_snapshot

    snap = build_snapshot(state_dir, cfg)
    out = {}
    for pair, cs in candles.items():
        chart = (snap.get("pairs") or {}).get(pair, {}).get("candles") or []
        if not chart:
            out[pair] = ("skip", "no chart yet")
            continue
        mine = _independent(cs, cfg)
        idx = {c.ts: i for i, c in enumerate(cs)}
        bad = []
        for pt in chart:
            i = idx.get(pt["ts"])
            if i is None:
                bad.append(f"chart candle {ms_to_iso(pt['ts'])} is not in the cache")
                continue
            for key, ref in (
                ("close", cs[i].close),
                ("ema21", mine["ema_slow"][i]),
                ("ema200", mine["ema_regime"][i]),
            ):
                if not _close(pt.get(key), None if ref is None else round(ref, 8), 1e-6):
                    bad.append(f"{key} at {ms_to_iso(pt['ts'])}: chart {pt.get(key)} vs {ref}")
        if bad:
            out[pair] = ("fail", f"{len(bad)} chart values wrong; {bad[0]}")
        elif chart[-1]["ts"] != cs[-1].ts:
            out[pair] = ("warn", "the chart does not end at the latest cached candle")
        else:
            out[pair] = ("pass", f"{len(chart)} chart candles match the independent values")
    return _result("chart", out)


def price_agent(
    live: Mapping[str, Any], other: Callable[[str], float | None] | None, max_pct: float = 1.5
) -> dict[str, Any]:
    prices = live.get("prices") or {}
    if other is None or not prices:
        return _result("price", {}, "no second exchange / no live prices")
    out = {}
    for pair, q in prices.items():
        mine = q.get("last") or q.get("bid")
        try:
            ref = other(pair)
        except Exception as exc:
            out[pair] = ("skip", f"second exchange unavailable: {exc}")
            continue
        if not mine or not ref:
            out[pair] = ("skip", "no comparable price")
            continue
        pct = abs(mine - ref) / ref * 100
        status = "pass" if pct <= max_pct / 3 else "warn" if pct <= max_pct else "fail"
        out[pair] = (status, f"{mine:.8g} vs {ref:.8g} on the second exchange ({pct:.2f}% apart)")
    return _result("price", out)


def ledger_agent(state_dir: Path, cfg: Any) -> dict[str, Any]:
    from .dashboard import build_snapshot

    j = state_dir / "journal.csv"
    trades = read_journal(j) if j.exists() else []
    snap = build_snapshot(state_dir, cfg)
    closed = [t for t in trades if t.is_closed]
    start = float(snap["stats"]["starting_equity"] or 0)
    problems = []
    eq = start + sum(t.pnl or 0 for t in closed)
    if not _close(snap["stats"]["equity"], round(eq, 2), 1e-6):
        problems.append(f"equity {snap['stats']['equity']} vs journal {eq:.2f}")
    if snap["stats"]["closed_trades"] != len(closed):
        problems.append("closed trade count differs from the journal")
    if len(snap["open_positions"]) != sum(not t.is_closed for t in trades):
        problems.append("open positions differ from the journal")
    for t in closed:
        if t.pnl is not None and t.risk_amount and t.r_multiple is not None:
            if not _close(t.pnl / t.risk_amount, t.r_multiple, 1e-4):
                problems.append(f"trade #{t.trade_id}: pnl / risk != R")
                break
    status = "fail" if problems else "pass"
    detail = "; ".join(problems) or f"equity, {len(closed)} closed and the open positions all match"
    return _result("ledger", {"all": (status, detail)})


def audit_agent(
    state_dir: Path, candles: Mapping[str, Sequence[Candle]], cfg: Any
) -> dict[str, Any]:
    from .invariants import live_journal_violations

    j = state_dir / "journal.csv"
    trades = read_journal(j) if j.exists() else []
    if not trades:
        return _result("audit", {"all": ("skip", "no trades yet")})
    state = _read(state_dir / "state.json")
    if state.get("engine") == "lab":
        return _result(
            "audit", {"all": ("skip", "a strategy-lab bot: the nine-rule audit does not apply")}
        )
    try:
        bad = live_journal_violations(
            trades, candles, cfg, starting_equity=state.get("starting_equity")
        )
    except Exception as exc:
        return _result("audit", {"all": ("warn", f"could not audit: {type(exc).__name__}: {exc}")})
    if bad:
        return _result("audit", {"all": ("fail", f"{len(bad)} rule violations; first: {bad[0]}")})
    return _result("audit", {"all": ("pass", f"all {len(trades)} trades follow the nine rules")})


# ---------------------------------------------------------------------- orchestration
def _read(p: Path) -> dict[str, Any]:
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        return {}


def run_all(
    state_dir: str | Path,
    cfg: Any,
    *,
    fetch: Callable[[str, int], Sequence[Candle]] | None = None,
    other_price: Callable[[str], float | None] | None = None,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Run every agent; write verify_status.json; return it."""
    d = Path(state_dir)
    now = int(time.time() * 1000) if now_ms is None else now_ms
    candles, unreadable = {}, {}
    for p in sorted((d / "candles").glob("*-4h.csv")) if (d / "candles").exists() else []:
        pair = p.name[: -len("-4h.csv")].replace("_", "/", 1)
        try:
            candles[pair] = load_candles_csv(p)
        except (OSError, ValueError) as exc:  # a corrupt cache is exactly what we look for
            unreadable[pair] = str(exc).rsplit(": ", 2)[-2:]
    t0 = time.time()
    agents: list[dict[str, Any]] = []
    steps: list[tuple[str, Callable[[], dict[str, Any]]]] = [
        ("data", lambda: data_agent(candles, cfg.timeframe_ms, now, unreadable)),
        ("exchange", lambda: exchange_agent(candles, fetch)),
        ("indicator", lambda: indicator_agent(candles, cfg)),
        ("chart", lambda: chart_agent(d, candles, cfg)),
        ("price", lambda: price_agent(_read(d / "live.json"), other_price)),
        ("ledger", lambda: ledger_agent(d, cfg)),
        ("audit", lambda: audit_agent(d, candles, cfg)),
    ]
    for name, fn in steps:
        try:
            agents.append(fn())
        except Exception as exc:  # one broken agent never hides the others
            log.exception("verification agent %s failed", name)
            agents.append(
                {"agent": name, "status": "warn", "note": f"agent error: {exc}", "pairs": {}}
            )
    blocked = sorted(
        {
            pair
            for a in agents
            if a["agent"] in GUARD_AGENTS
            for pair, r in a["pairs"].items()
            if r["status"] == "fail"
        }
    )
    status = {
        "checked_ms": now,
        "checked_utc": ms_to_iso(now),
        "seconds": round(time.time() - t0, 2),
        "overall": _worst([a["status"] for a in agents]),
        "blocked_pairs": blocked,
        "agents": agents,
    }
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / (STATUS_FILE + ".tmp")
    tmp.write_text(json.dumps(status, indent=1), encoding="utf-8")
    tmp.replace(d / STATUS_FILE)
    return status


def blocked_pairs(
    state_dir: Path, max_age_ms: int = 6 * 3_600_000, now_ms: int | None = None
) -> dict[str, str]:
    """Pairs the guard blocks now, with the reason (a stale report blocks nothing)."""
    st = _read(Path(state_dir) / STATUS_FILE)
    now = int(time.time() * 1000) if now_ms is None else now_ms
    if not st or now - int(st.get("checked_ms") or 0) > max_age_ms:
        return {}
    out = {}
    for a in st.get("agents") or []:
        if a["agent"] not in GUARD_AGENTS:
            continue
        for pair, r in (a.get("pairs") or {}).items():
            if r["status"] == "fail":
                out.setdefault(pair, f"{a['agent']} agent: {r['detail']}")
    return out


def run_for_settings(settings: Any) -> dict[str, Any]:
    """The ``verify`` job: agents with a public exchange connection and a second exchange."""
    cfg = settings.strategy_config()
    fetch = other = None
    try:
        import ccxt

        from .live_exchange import CcxtGateway

        gw = CcxtGateway(getattr(ccxt, settings.exchange)({"enableRateLimit": True}))
        fetch = gw.closed_candles
        ref_id = "coinbase" if settings.exchange != "coinbase" else "kraken"
        ref = getattr(ccxt, ref_id)({"enableRateLimit": True})
        ref.load_markets()

        def other(pair: str) -> float | None:
            base, quote = pair.split("/")
            for q in (quote, "USD", "USDC", "USDT"):
                sym = f"{base}/{q}"
                if sym in ref.markets:
                    t = ref.fetch_ticker(sym)
                    return float(t.get("last") or 0) or None
            return None

    except Exception as exc:  # no ccxt / no network: the offline agents still run
        log.warning("exchange agents unavailable: %s", exc)
    return run_all(settings.state_dir, cfg, fetch=fetch, other_price=other)
