"""The memory bank: every trade every bot has made, in one place, with its whole story.

One record per trade, gathered from what the bots already keep:

- ``journal.csv``: the trade itself (entry, exit, stop, target, size, P&L, R, fees);
- ``ledger.json``: why it triggered, what was expected, the learning check and the lesson;
- ``chantisimo_thoughts.jsonl``: Chantisimo's reflection after the exit (verdict, the mistakes
  it saw, what its recall predicted);
- ``wallet/tickets``: the MetaMask / Phantom transactions behind a wallet-mode trade;
- ``memes/positions.json``: meme desk positions (buys, sells, realized and open P&L).

Your own notes, tags and stars are kept in ``memory_bank.json`` next to the first bot's state.
Nothing here changes a trade: the bank only reads, plus your annotations.
"""

from __future__ import annotations

import csv
import io
import json
import statistics
import time
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .journal import atomic_write_text, ms_to_iso, read_journal
from .models import Trade


HOUR_MS = 3_600_000
DAY_MS = 86_400_000
EXIT_TEXT = {"SL": "stop-loss", "TP": "target", "END": "closed by hand / flatten"}


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _reflections(d: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    p = d / "chantisimo_thoughts.jsonl"
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("kind") == "reflection" and r.get("trade_id") is not None:
            out[str(r["trade_id"])] = r
    return out


def _tickets(d: Path) -> list[dict[str, Any]]:
    rows = []
    for p in (d / "wallet" / "tickets").glob("*.json"):
        t = _read(p, None)
        if isinstance(t, dict) and t.get("status") == "filled" and not t.get("dry_run"):
            rows.append(t)
    return rows


def _match_tx(
    tickets: list[dict[str, Any]], pair: str, side: str, ts: int | None
) -> dict[str, Any] | None:
    """The filled wallet swap behind a trade's entry or exit (same pair and side, nearest)."""
    if ts is None:
        return None
    best = None
    for t in tickets:
        if t.get("pair") != pair or t.get("side") != side:
            continue
        gap = abs(int(t.get("updated_ms") or t.get("created_ms") or 0) - ts)
        if gap <= DAY_MS and (best is None or gap < best[0]):
            best = (gap, t)
    if best is None:
        return None
    t = best[1]
    return {"tx": t.get("tx"), "chain": t.get("chain"), "provider": t.get("provider")}


def _record(
    bot: str,
    meta: Mapping[str, Any],
    t: Trade,
    ledger: Mapping[str, Any],
    refl: Mapping[str, Any] | None,
    tickets: list[dict[str, Any]],
) -> dict[str, Any]:
    held = (t.exit_ts - t.entry_ts) / HOUR_MS if t.exit_ts else None
    rid = f"{bot}:{t.trade_id}"
    outcome = "open" if not t.is_closed else "win" if (t.r_multiple or 0) > 0 else "loss"
    entry_tx = _match_tx(tickets, t.pair, "buy", t.entry_ts)
    exit_tx = _match_tx(tickets, t.pair, "sell", t.exit_ts)
    return {
        "id": rid,
        "bot": bot,
        "source": "bot",
        "mode": meta.get("mode"),
        "exchange": meta.get("exchange"),
        "strategy": t.variant,
        "trade_id": t.trade_id,
        "pair": t.pair,
        "outcome": outcome,
        "signal_ms": t.signal_ts,
        "entry_ms": t.entry_ts,
        "exit_ms": t.exit_ts,
        "entry_utc": ms_to_iso(t.entry_ts),
        "exit_utc": ms_to_iso(t.exit_ts) if t.exit_ts else None,
        "held_hours": None if held is None else round(held, 2),
        "entry": t.entry_price,
        "exit": t.exit_price,
        "stop": t.stop,
        "target": t.target,
        "qty": t.qty,
        "risk_amount": t.risk_amount,
        "risk_pct": t.risk_pct,
        "fees": t.fees,
        "pnl": t.pnl,
        "r": t.r_multiple,
        "exit_reason": t.exit_reason,
        "exit_text": EXIT_TEXT.get(t.exit_reason or "", t.exit_reason),
        "why": list(ledger.get("why_triggered") or []),
        "expected": dict(ledger.get("expected_outcome") or {}),
        "learning_check": ledger.get("learning_check") or "",
        "lesson": ledger.get("lesson"),
        "features": dict(ledger.get("features") or t.features or {}),
        "ml_prob": t.ml_prob,
        "notes": t.notes,
        "brain": None
        if refl is None
        else {
            "verdict": refl.get("verdict"),
            "mistakes": list(refl.get("mistakes") or []),
            "predicted": refl.get("predicted"),
            "lesson": refl.get("lesson"),
        },
        "txs": [x for x in (entry_tx, exit_tx) if x],
    }


def bot_records(
    bot: str, state_dir: Path, meta: Mapping[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Every trade in one bot's state folder, with its story."""
    d = Path(state_dir)
    j = d / "journal.csv"
    if not j.exists():
        return []
    try:
        trades = read_journal(j)
    except (OSError, ValueError):
        return []
    state = _read(d / "state.json", {})
    meta = {"mode": state.get("mode"), "exchange": state.get("exchange"), **(meta or {})}
    ledger = {
        str(r.get("trade_id")): r for r in _read(d / "ledger.json", []) if isinstance(r, dict)
    }
    refl, tickets = _reflections(d), _tickets(d)
    return [
        _record(bot, meta, t, ledger.get(str(t.trade_id), {}), refl.get(str(t.trade_id)), tickets)
        for t in trades
    ]


def meme_records(state_dir: Path) -> list[dict[str, Any]]:
    """The meme desk's positions, one record per coin."""
    out = []
    for key, r in _read(Path(state_dir) / "memes" / "positions.json", {}).items():
        trades = r.get("trades") or []
        sells = [x for x in trades if x.get("side") == "sell"]
        closed = r.get("qty", 0) <= 0 and bool(sells)
        realized = float(r.get("realized_usd") or 0)
        cost = sum(x.get("usd", 0) for x in trades if x.get("side") == "buy")
        out.append({
            "id": f"memes:{key}",
            "bot": "meme desk",
            "source": "meme",
            "mode": "wallet",
            "exchange": r.get("chain"),
            "strategy": "meme",
            "trade_id": key,
            "pair": f"{r.get('symbol')}/USD",
            "outcome": "open" if not closed else "win" if realized > 0 else "loss",
            "signal_ms": r.get("opened_ms"),
            "entry_ms": r.get("opened_ms"),
            "exit_ms": max((x["ts"] for x in sells), default=None) if closed else None,
            "entry_utc": ms_to_iso(r["opened_ms"]) if r.get("opened_ms") else None,
            "exit_utc": ms_to_iso(max(x["ts"] for x in sells)) if closed else None,
            "held_hours": None,
            "entry": None,
            "exit": None,
            "qty": r.get("qty"),
            "cost_usd": cost,
            "pnl": realized if closed or realized else None,
            "r": None,
            "exit_reason": "sold" if closed else None,
            "exit_text": "sold on the meme desk" if closed else None,
            "why": [f"bought on the meme desk ({r.get('chain')}, {r.get('address')})"],
            "expected": {},
            "learning_check": "",
            "lesson": None,
            "features": {},
            "brain": None,
            "txs": [{"tx": x.get("tx"), "chain": r.get("chain")} for x in trades if x.get("tx")],
            "swaps": trades,
        })  # fmt: skip
    return out


# ---------------------------------------------------------------------- your annotations
def notes_path(home: Path) -> Path:
    return Path(home) / "memory_bank.json"


def annotate(home: Path, rid: str, note: str | None = None, tags: Iterable[str] | None = None,
             star: bool | None = None) -> dict[str, Any]:  # fmt: skip
    """Your note, tags and star on a trade (``None`` leaves a field as it is)."""
    if not rid or len(rid) > 200:
        raise ValueError("which trade?")
    p = notes_path(home)
    data = _read(p, {})
    row = dict(data.get(rid) or {})
    if note is not None:
        row["note"] = str(note)[:4000]
    if tags is not None:
        clean = sorted({str(x).strip().lower()[:30] for x in tags if str(x).strip()})
        row["tags"] = clean[:20]
    if star is not None:
        row["star"] = bool(star)
    row["updated_ms"] = int(time.time() * 1000)
    data[rid] = row
    Path(home).mkdir(parents=True, exist_ok=True)
    atomic_write_text(p, json.dumps(data, indent=1))
    return row


# ---------------------------------------------------------------------- the bank
def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [r for r in rows if r["outcome"] in ("win", "loss")]
    pnl = [r["pnl"] for r in closed if r.get("pnl") is not None]
    rs = [r["r"] for r in closed if r.get("r") is not None]
    wins = [r for r in closed if r["outcome"] == "win"]
    run = best_streak = worst_streak = 0  # run > 0: wins in a row; run < 0: losses in a row
    for r in sorted(closed, key=lambda r: r.get("exit_ms") or 0):
        if r["outcome"] == "win":
            run = run + 1 if run > 0 else 1
        else:
            run = run - 1 if run < 0 else -1
        best_streak, worst_streak = max(best_streak, run), min(worst_streak, run)
    by = defaultdict(lambda: {"trades": 0, "wins": 0, "pnl": 0.0})
    for r in closed:
        for key in (
            f"bot:{r['bot']}",
            f"pair:{r['pair']}",
            f"mode:{r['mode']}",
            f"strategy:{r['strategy']}",
        ):
            b = by[key]
            b["trades"] += 1
            b["wins"] += r["outcome"] == "win"
            b["pnl"] += r.get("pnl") or 0.0
    mistakes = Counter(m for r in closed for m in ((r.get("brain") or {}).get("mistakes") or []))
    curve, total = [], 0.0
    for r in sorted(closed, key=lambda r: r.get("exit_ms") or 0):
        total += r.get("pnl") or 0.0
        curve.append({"t": r.get("exit_ms"), "pnl": round(total, 2), "id": r["id"]})
    return {
        "trades": len(rows),
        "closed": len(closed),
        "open": sum(r["outcome"] == "open" for r in rows),
        "wins": len(wins),
        "win_rate": len(wins) / len(closed) if closed else None,
        "pnl": round(sum(pnl), 2) if pnl else 0.0,
        "avg_r": round(statistics.fmean(rs), 3) if rs else None,
        "best": max(closed, key=lambda r: r.get("pnl") or 0)["id"] if closed else None,
        "worst": min(closed, key=lambda r: r.get("pnl") or 0)["id"] if closed else None,
        "longest_win_streak": best_streak,
        "longest_loss_streak": -worst_streak,
        "by": {k: {**v, "win_rate": v["wins"] / v["trades"]} for k, v in by.items()},
        "exit_reasons": dict(Counter(r.get("exit_reason") or "open" for r in rows)),
        "mistakes": mistakes.most_common(10),
        "curve": curve,
    }


def bank(bots: Mapping[str, tuple[Path, Mapping[str, Any] | None]], home: Path) -> dict[str, Any]:
    """Everything: ``bots`` maps a bot name to (state folder, {"mode", "exchange"})."""
    rows: list[dict[str, Any]] = []
    for name, (d, meta) in bots.items():
        rows += bot_records(name, d, meta)
    rows += meme_records(home)
    notes = _read(notes_path(home), {})
    for r in rows:
        n = notes.get(r["id"]) or {}
        r["note"], r["tags"], r["star"] = n.get("note", ""), n.get("tags", []), bool(n.get("star"))
    rows.sort(key=lambda r: -(r.get("exit_ms") or r.get("entry_ms") or 0))
    return {"generated_ms": int(time.time() * 1000), "records": rows, "summary": summary(rows)}


CSV_COLUMNS = (
    "id", "bot", "mode", "exchange", "strategy", "pair", "outcome", "entry_utc", "exit_utc",
    "held_hours", "entry", "exit", "stop", "target", "qty", "risk_pct", "fees", "pnl", "r",
    "exit_text", "lesson", "brain_lesson", "mistakes", "why", "note", "tags", "star",
)  # fmt: skip


def to_csv(rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for r in rows:
        brain = r.get("brain") or {}
        extra = {
            "brain_lesson": brain.get("lesson"),
            "mistakes": "; ".join(brain.get("mistakes") or []),
            "why": " | ".join(r.get("why") or []),
            "tags": ", ".join(r.get("tags") or []),
        }
        w.writerow(["" if (v := {**r, **extra}.get(c)) is None else v for c in CSV_COLUMNS])
    return buf.getvalue()
