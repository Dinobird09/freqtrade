"""Scheduled data collection and weekly retraining of the signal layers.

Two jobs, both safe to run while the bot trades (they only write files the bot reloads):

``collect``  (hourly by default)
    Pulls the external data sources: Fear & Greed, news RSS / Reddit / CryptoPanic
    sentiment (and high-impact headlines -> R5 blackout events), recent exchange trades for
    order flow, and optionally a DEX memecoin watchlist. Status: ``collect_status.json``.

``retrain``  (weekly by default, Sunday 01:00 UTC)
    Rebuilds every enabled layer from the cached candles plus every closed trade in the
    journal (paper, testnet and live trades all count), validates each on the newer 30% of
    history it never saw, refits the passing ones on all data and writes ``layers.json`` +
    ``models/``. The bot picks the new models up at its next step. Status:
    ``retrain_status.json``.

Usage::

    python3 -m research.trendbot.retrain collect --settings bot.json
    python3 -m research.trendbot.retrain retrain --settings bot.json
    python3 -m research.trendbot.retrain all     --settings bot.json
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import sys
import time
import traceback
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .data import load_candles_csv
from .journal import ms_to_iso, read_journal
from .layers import LayerBook, ValidationSettings
from .news import load_events


log = logging.getLogger("trendbot.retrain")

SOURCE_MODULES = ("sentiment", "orderflow")
DEFAULT_SCHEDULE = {
    "retrain_weekday": 6,
    "retrain_hour_utc": 1,
    "collect_every_minutes": 60,
    "traders_every_minutes": 5,  # smart money: re-read followed traders, list copy signals
    "verify_every_minutes": 15,  # verification agents (verify.py)
    "lab_hour_utc": 2,  # the strategy lab's nightly research (lab.py); -1 = off
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def load_cached_candles(state_dir: Path) -> dict[str, list[Any]]:
    out = {}
    d = state_dir / "candles"
    for path in sorted(d.glob("*-4h.csv")) if d.exists() else []:
        pair = path.name[: -len("-4h.csv")].replace("_", "/", 1)
        out[pair] = load_candles_csv(path)
    return out


def load_all_sources(state_dir: Path) -> dict[str, Any]:
    sources: dict[str, Any] = {}
    for name in SOURCE_MODULES:
        try:
            mod = importlib.import_module(f"{__package__}.{name}")
            sources.update(mod.load_sources(state_dir) or {})
        except Exception as exc:
            log.warning("source %s could not be loaded: %s", name, exc)
    return sources


def _write_status(state_dir: Path, name: str, status: Mapping[str, Any]) -> None:
    tmp = state_dir / f"{name}.json.tmp"
    tmp.write_text(json.dumps(status, indent=1, default=str), encoding="utf-8")
    tmp.replace(state_dir / f"{name}.json")


def _public_exchange(exchange_id: str) -> Any | None:
    try:
        import ccxt
    except ImportError:
        return None
    return getattr(ccxt, exchange_id)({"enableRateLimit": True})


def run_collect(settings: Any, *, fetch: Any = None, exchange: Any = None) -> dict[str, Any]:
    """Run every data collector; one failing source never stops the others."""
    state_dir = Path(settings.state_dir)
    started = _now_ms()
    report: dict[str, Any] = {"started_utc": ms_to_iso(started), "sources": {}}
    jobs = [
        ("sentiment", {**settings.sentiment, "events": settings.events}),
        ("orderflow", {"pairs": list(settings.pairs), "orderflow": dict(settings.orderflow)}),
    ]
    if settings.dex.get("enabled"):
        jobs.append(("dex_scan", dict(settings.dex)))
    scan_cfg = dict(getattr(settings, "scanner", None) or {})
    if scan_cfg.get("enabled", True):
        jobs.append(("scanner", scan_cfg))
    from .connections import load_connections

    if any(e.get("sources") for e in load_connections()["mcp"].values()):
        jobs.append(("connections", {"pairs": list(settings.pairs), "exchange": settings.exchange}))
    for name, section in jobs:
        try:
            mod = importlib.import_module(f"{__package__}.{name}")
            kwargs: dict[str, Any] = {"fetch": fetch} if fetch is not None else {}
            if name in ("orderflow", "scanner"):
                kwargs["exchange"] = exchange or _public_exchange(settings.exchange)
            report["sources"][name] = {
                "ok": True,
                "result": mod.collect(state_dir, section, **kwargs),
            }
        except Exception as exc:
            log.error("collector %s failed: %s", name, exc)
            report["sources"][name] = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "trace": traceback.format_exc(limit=3),
            }
    report["finished_utc"] = ms_to_iso(_now_ms())
    _write_status(state_dir, "collect_status", report)
    return report


def run_retrain(settings: Any, cfg: Any, *, now_ms: int | None = None) -> dict[str, Any]:
    """Validate and refit every enabled layer on the latest data; write layers.json."""
    state_dir = Path(settings.state_dir)
    now = _now_ms() if now_ms is None else int(now_ms)
    data = load_cached_candles(state_dir)
    journal = state_dir / "journal.csv"
    trades = [t for t in (read_journal(journal) if journal.exists() else []) if t.is_closed]
    teachers = (getattr(settings, "brain", None) or {}).get("learn_from") or []
    if teachers:  # Chantisimo: the paper bots' trades teach this bot's layers too
        from .chantisimo import Chantisimo, load_teachers

        brain = Chantisimo(state_dir)
        brain.teachers = load_teachers(teachers, state_dir)
        trades += brain.teacher_trades(trades)
    events = (
        load_events(settings.events) if settings.events and Path(settings.events).exists() else []
    )
    sources = load_all_sources(state_dir)
    lay = settings.layers
    book = LayerBook(state_dir, cfg, enabled=lay.get("enabled"), params=lay.get("params"))
    vs = ValidationSettings(**lay.get("validation", {}))
    t0 = time.time()
    results = book.retrain(data, now_ms=now, events=events, trades=trades, sources=sources, vs=vs)
    alloc = None
    try:  # the regime allocator's 5-state HMM is refitted every week (allocation.py)
        from .allocation import AllocationSettings, RegimeAllocator

        ra = RegimeAllocator(AllocationSettings(**(getattr(settings, "allocation", None) or {})))
        if ra.s.enabled and ra.fit(data, cfg.timeframe_ms, now):
            ra.save(state_dir / "models")
            alloc = "fitted"
    except Exception as exc:
        log.warning("regime allocator not fitted: %s", exc)
        alloc = f"error: {exc}"
    backtest_n = 0
    if data:  # Chantisimo's recall draws on every simulated signal in history too
        from .chantisimo import write_backtest_memory
        from .layers import build_context

        ctx = build_context(data, cfg, now, events, trades, sources, state_dir)
        backtest_n = write_backtest_memory(state_dir, ctx.candidates)
    status = {
        "finished_utc": ms_to_iso(_now_ms()),
        "trained_until_utc": ms_to_iso(now),
        "seconds": round(time.time() - t0, 1),
        "candles": {p: len(c) for p, c in data.items()},
        "journal_trades": len(trades),
        "teacher_dirs": list(teachers),
        "backtest_memory": backtest_n,
        "regime_allocator": alloc,
        "layers": {
            n: {"status": r.get("status"), "reason": r.get("reason")} for n, r in results.items()
        },
    }
    _write_status(state_dir, "retrain_status", status)
    return status


def due(schedule: Mapping[str, Any], last: Mapping[str, Any], now_ms: int) -> list[str]:
    """Which jobs are due now: 'collect' every N minutes, 'retrain' once per scheduled week."""
    sch = {**DEFAULT_SCHEDULE, **schedule}
    jobs = []
    minutes = float(sch["collect_every_minutes"])
    if minutes > 0 and now_ms - int(last.get("collect", 0)) >= minutes * 60_000:
        jobs.append("collect")
    lab_h = int(sch["lab_hour_utc"])
    if lab_h >= 0:
        today = now_ms // 86_400_000 * 86_400_000 + lab_h * 3_600_000
        slot = today if today <= now_ms else today - 86_400_000
        if int(last.get("lab", 0)) < slot <= now_ms:
            jobs.append("lab")
    vmin = float(sch["verify_every_minutes"])
    if vmin > 0 and now_ms - int(last.get("verify", 0)) >= vmin * 60_000:
        jobs.append("verify")
    tmin = float(sch["traders_every_minutes"])
    if tmin > 0 and now_ms - int(last.get("traders", 0)) >= tmin * 60_000:
        from .traders import registry_path

        if registry_path().exists():
            jobs.append("traders")
    now = datetime.fromtimestamp(now_ms / 1000, UTC)
    week_start = now.replace(minute=0, second=0, microsecond=0)
    slot = (
        week_start.timestamp() * 1000
        - (
            ((now.weekday() - int(sch["retrain_weekday"])) % 7) * 24
            + (now.hour - int(sch["retrain_hour_utc"]))
        )
        * 3_600_000
    )
    if slot > now_ms:  # this week's slot is still ahead: the previous one applies
        slot -= 7 * 24 * 3_600_000
    if int(last.get("retrain", 0)) < slot <= now_ms:
        jobs.append("retrain")
    return jobs


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python3 -m research.trendbot.retrain",
        description="collect data sources and retrain signal layers",
    )
    p.add_argument("job", choices=("collect", "retrain", "traders", "verify", "lab", "all"))
    p.add_argument("--settings", required=True)
    return p


def main(argv: list[str] | None = None) -> int:
    from .live_bot import BotSettings

    args = _parser().parse_args(argv)
    settings = BotSettings.load(args.settings)
    state_dir = Path(settings.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        force=True,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(state_dir / "retrain.log", encoding="utf-8"),
        ],
    )
    lock = state_dir / f".{args.job}.lock"
    if lock.exists() and time.time() - lock.stat().st_mtime < 6 * 3600:
        log.warning("%s already running (lock %s); exiting", args.job, lock)
        return 1
    lock.write_text(str(_now_ms()))
    try:
        if args.job in ("collect", "all"):
            run_collect(settings)
        if args.job in ("retrain", "all"):
            run_retrain(settings, settings.strategy_config())
        if args.job in ("lab", "all"):
            from .lab import run as run_lab

            run_lab(settings.state_dir)
        if args.job in ("verify", "all"):
            from .verify import run_for_settings

            run_for_settings(settings)
        if args.job in ("traders", "all"):
            from .traders import refresh

            refresh()
    finally:
        lock.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
