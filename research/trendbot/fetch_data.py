"""Download real OHLCV candles with ccxt and store them as trendbot candle CSV files.

NOT RUN AGAINST A LIVE EXCHANGE: the sandbox this package was built in has no network and
no ccxt, so this module has only been exercised against a fake exchange
(``tests/test_fetch_data.py``). Treat the first real download as untested code: read the
printed gap report and spot-check a few candles against the exchange's own chart.

Usage (from the repository root, with network access and ``pip install ccxt``)::

    python -m research.trendbot.fetch_data --exchange binance \\
        --pairs BTC/USDT ETH/USDT BNB/USDT --timeframe 4h --since 2019-01-01 --out research/data

Exchange notes:
- ``binance`` is Binance spot. BNB/USDT is a Binance pair; other venues may not list it.
- ``coinbase`` is Coinbase Advanced Trade (ccxt id "coinbase"). It returns at most 300
  candles per request and serves no 4h candles; when the exchange lacks the requested
  timeframe this script fetches the largest supported timeframe that divides it (2h on
  Coinbase per ccxt's timeframe table; not verified live) and aggregates COMPLETE buckets
  only (``data.resample_candles``).

Files are written as ``<out>/<BASE>_<QUOTE>-<timeframe>.csv`` (see ``data.pair_filename``),
the format ``data.load_dataset`` reads.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from .data import (
    gap_report,
    pair_filename,
    parse_iso_utc,
    resample_candles,
    save_candles_csv,
    timeframe_to_ms,
    ts_to_iso,
    validate_candles,
)
from .models import Candle


DEFAULT_PAIRS = ("BTC/USDT", "ETH/USDT", "BNB/USDT")
_PAGE_LIMITS = {"coinbase": 300}  # per-request candle caps that differ from the default
_DEFAULT_LIMIT = 1000


def _row_to_candle(row: Sequence[Any]) -> Candle:
    """ccxt row ``[ts, open, high, low, close, volume]`` -> Candle."""
    if len(row) < 6 or any(x is None for x in row[:6]):
        raise ValueError(f"malformed OHLCV row from exchange: {row!r}")
    return Candle(
        ts=int(row[0]),
        open=float(row[1]),
        high=float(row[2]),
        low=float(row[3]),
        close=float(row[4]),
        volume=float(row[5]),
    )


def _now_ms(exchange: Any) -> int:
    clock = getattr(exchange, "milliseconds", None)
    return int(clock()) if callable(clock) else int(time.time() * 1000)


def fetch_ohlcv(
    exchange: Any,
    symbol: str,
    timeframe: str,
    since_ms: int,
    until_ms: int | None = None,
    limit: int = 1000,
) -> list[Candle]:
    """All CLOSED candles with ``since_ms <= ts < until_ms`` (until None = up to now).

    Pages through ``exchange.fetch_ohlcv(symbol, timeframe, since=..., limit=...)``,
    advancing ``since`` to the last returned ts + one timeframe. Stops on an empty page,
    on a page that makes no progress, or once ``since`` reaches ``until_ms``. Rows are
    de-duplicated by ts (last copy wins) and returned ascending. Candles still open at
    ``exchange.milliseconds()`` (``ts + timeframe > now``) are dropped.
    """
    if limit <= 0:
        raise ValueError("limit must be > 0")
    tf_ms = timeframe_to_ms(timeframe)
    by_ts: dict[int, Candle] = {}
    since = since_ms
    while until_ms is None or since < until_ms:
        page = exchange.fetch_ohlcv(symbol, timeframe, since=since, limit=limit)
        if not page:
            break
        for row in page:
            candle = _row_to_candle(row)
            if candle.ts >= since_ms and (until_ms is None or candle.ts < until_ms):
                by_ts[candle.ts] = candle
        next_since = max(int(row[0]) for row in page) + tf_ms
        if next_since <= since:
            break  # the exchange ignored `since` or returned stale data: no progress
        since = next_since
    now = _now_ms(exchange)
    return [by_ts[ts] for ts in sorted(by_ts) if ts + tf_ms <= now]


# ---------------------------------------------------------------------- CLI
def parse_utc_date(text: str) -> int:
    """``"2019-01-01"`` or an ISO-8601 datetime -> epoch ms. Offset-less values are UTC."""
    return parse_iso_utc(text, assume_utc=True)


def plan_timeframe(exchange: Any, timeframe: str) -> str:
    """The timeframe to request: ``timeframe`` itself, or the largest supported divisor."""
    supported = getattr(exchange, "timeframes", None) or {}
    if not supported or timeframe in supported:
        return timeframe
    target = timeframe_to_ms(timeframe)
    divisors = []
    for tf in supported:
        try:
            ms = timeframe_to_ms(tf)
        except ValueError:
            continue
        if ms < target and target % ms == 0:
            divisors.append((ms, tf))
    if not divisors:
        name = getattr(exchange, "id", "exchange")
        raise ValueError(f"{name} serves neither {timeframe} nor a timeframe that divides it")
    return max(divisors)[1]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m research.trendbot.fetch_data",
        description="Download OHLCV candles via ccxt into trendbot CSV files "
        "(needs network access and `pip install ccxt`).",
    )
    p.add_argument(
        "--exchange",
        default="binance",
        help="ccxt exchange id, e.g. 'binance' (spot) or 'coinbase' (Coinbase Advanced Trade "
        "is ccxt id 'coinbase'). BNB/USDT is a Binance pair and is skipped where not listed.",
    )
    p.add_argument("--pairs", nargs="+", default=list(DEFAULT_PAIRS), help="ccxt symbols")
    p.add_argument("--timeframe", default="4h", help="candle timeframe (default 4h)")
    p.add_argument("--since", default="2019-01-01", help="first candle, YYYY-MM-DD (UTC)")
    p.add_argument("--until", default=None, help="stop before this date, YYYY-MM-DD (UTC)")
    p.add_argument("--out", default="research/data", help="output directory")
    p.add_argument(
        "--limit",
        type=int,
        default=None,
        help="candles per request (default 1000; 300 for coinbase)",
    )
    return p


def _download_pair(exchange: Any, pair: str, args: argparse.Namespace) -> int:
    """Fetch, validate and save one pair. Returns 0 on success, 1 on a per-pair failure."""
    name = getattr(exchange, "id", args.exchange)
    markets = getattr(exchange, "markets", None) or {}
    if markets and pair not in markets:
        is_bnb = pair.upper().startswith("BNB/") and name != "binance"
        hint = " (BNB is a Binance pair)" if is_bnb else ""
        print(f"{pair}: not listed on {name}{hint}; skipped", file=sys.stderr)
        return 1
    tf_ms = timeframe_to_ms(args.timeframe)
    try:
        fetch_tf = plan_timeframe(exchange, args.timeframe)
        limit = args.limit or _PAGE_LIMITS.get(name, _DEFAULT_LIMIT)
        candles = fetch_ohlcv(exchange, pair, fetch_tf, args.since_ms, args.until_ms, limit)
        if fetch_tf != args.timeframe:
            print(f"{pair}: {name} has no {args.timeframe} candles; aggregating {fetch_tf}")
            candles = resample_candles(candles, timeframe_to_ms(fetch_tf), tf_ms)
        validate_candles(candles)
    except Exception as exc:  # network / exchange / data errors: report, continue
        print(f"{pair}: download failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if not candles:
        print(f"{pair}: no closed candles in the requested window", file=sys.stderr)
        return 1
    path = Path(args.out) / pair_filename(pair, args.timeframe)
    save_candles_csv(candles, path)
    gaps = gap_report(candles, tf_ms)
    first, last = ts_to_iso(candles[0].ts), ts_to_iso(candles[-1].ts)
    print(f"{pair}: {len(candles)} candles {first} .. {last} -> {path}; {len(gaps)} gap(s)")
    for a, b in gaps[:10]:
        print(f"  gap: {ts_to_iso(a)} -> {ts_to_iso(b)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        timeframe_to_ms(args.timeframe)
        args.since_ms = parse_utc_date(args.since)
        args.until_ms = parse_utc_date(args.until) if args.until else None
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        import ccxt  # lazy: optional dependency, absent in the build sandbox
    except ImportError:
        print(
            "error: ccxt is not installed. Install it with `pip install ccxt` on a machine "
            "with network access, then re-run this command.",
            file=sys.stderr,
        )
        return 2
    exchange_cls = getattr(ccxt, args.exchange, None)
    if exchange_cls is None:
        print(f"error: unknown ccxt exchange id {args.exchange!r}", file=sys.stderr)
        return 2
    exchange = exchange_cls({"enableRateLimit": True})
    try:
        exchange.load_markets()
    except Exception as exc:
        print(f"error: cannot load {args.exchange} markets: {exc}", file=sys.stderr)
        return 1
    return max((_download_pair(exchange, pair, args) for pair in args.pairs), default=0)


if __name__ == "__main__":
    raise SystemExit(main())
