"""Order flow: 4H footprint summaries from taker trades, their sources, and a veto layer.

NOT RUN AGAINST A LIVE EXCHANGE OR data.binance.vision: the sandbox this module was built in
has no network and no ccxt. Everything below was exercised only against fakes and zip files
built in ``tests/test_orderflow.py``. Treat the first real download as untested code.

Footprint (one per epoch-aligned 4H candle, ``ts % 4h == 0``; see :class:`FootprintSummary`):

- taker buy volume (ask side, lifted offers), taker sell volume (bid side, hit bids),
  ``delta = buy - sell``, ``delta_pct = delta / volume * 100`` and the pair's cumulative
  delta (running sum over the stored candles, oldest first);
- price levels: bin size = ``bin_ticks * tick_size`` if both are given, else ``bin_pct`` %
  of the candle open (default 0.05 %). A trade at price p is in level ``floor(p / bin)``;
- point of control (the level with the most volume; ties -> the lowest level);
- diagonal imbalances: a BUY imbalance at level L when ``ask[L] >= ratio * bid[L-1]``, a SELL
  imbalance at L when ``bid[L] >= ratio * ask[L+1]`` (ratio 4.0 = 400 %). By default the
  opposite side must be non-zero (``count_zero_opposite=False``): an empty level is no
  evidence of aggression in thin books;
- absorption (from exact trade prices, range = high - low of the candle's trades):
  ``bull_absorption`` = taker SELLING absorbed at the lows: taker sells at prices in the
  lower third >= ``absorption_share`` (30 %) of the candle volume AND >= half of all taker
  sells, while the candle closes in its upper half (close > low + range / 2).
  ``bear_absorption`` is the mirror: taker BUYING in the upper third, close in the lower half
  (passive sellers absorbed the buyers - a warning sign for a long breakout).

Sources: live/recent trades via a ccxt-compatible ``exchange.fetch_trades`` (:func:`fetch_trades`),
history from Binance public ``aggTrades`` zip files (:func:`download_aggtrades`,
:func:`iter_aggtrades_zip`), both aggregated into summaries stored in
``<state_dir>/orderflow/<BASE>_<QUOTE>-4h.csv`` (:func:`merge_store`). :func:`collect` runs
both; :func:`load_sources` returns ``{"orderflow": {pair: {candle_ts: FootprintSummary}}}``
for ``layers.TrainContext.sources``.

Layer ``orderflow``: see :class:`OrderFlowLayer`.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import math
import statistics
import time
import zipfile
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, fields, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .data import pair_filename
from .journal import ms_to_iso
from .layers import Layer, MarketView, TrainContext, register
from .models import HOUR_MS, FeatureRow


log = logging.getLogger("trendbot.orderflow")

TIMEFRAME_MS = 4 * HOUR_MS
DEFAULT_PAIRS = ("BTC/USDT", "ETH/USDT", "BNB/USDT")
BINANCE_DATA_URL = "https://data.binance.vision/data/spot"
STORE_DIR = "orderflow"
SOURCE_LIVE = "live"
SOURCE_BINANCE = "binance_aggtrades"

Fetch = Callable[[str], bytes]


# ---------------------------------------------------------------------- trade model
@dataclass(frozen=True, slots=True)
class FlowTrade:
    """One executed trade. ``taker_side`` is the aggressor: 'buy' lifted an ask, 'sell' hit a bid.

    (Named FlowTrade to avoid confusion with ``models.Trade``, the bot's own positions.)
    """

    ts: int  # ms UTC
    price: float
    qty: float  # base-asset quantity
    taker_side: str  # "buy" | "sell"
    trade_id: str | None = None

    def __post_init__(self) -> None:
        if self.taker_side not in ("buy", "sell"):
            raise ValueError(f"taker_side must be 'buy' or 'sell' (got {self.taker_side!r})")


def candle_ts(ts: int, timeframe_ms: int = TIMEFRAME_MS) -> int:
    """Epoch-aligned open time of the candle containing ``ts``."""
    return ts - ts % timeframe_ms


# ---------------------------------------------------------------------- footprint
@dataclass(frozen=True, slots=True)
class FootprintParams:
    bin_pct: float = 0.05  # level size in percent of the candle open (used unless ticks given)
    bin_ticks: int | None = None
    tick_size: float | None = None
    imbalance_ratio: float = 4.0  # 4.0 = 400 %
    count_zero_opposite: bool = False
    absorption_share: float = 0.30  # lower-third taker sells as a share of candle volume
    keep_levels: bool = False  # attach full per-level data to the summaries
    timeframe_ms: int = TIMEFRAME_MS

    @classmethod
    def from_settings(cls, s: Mapping[str, Any] | None) -> FootprintParams:
        s = s or {}
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in s.items() if k in names})

    def bin_size(self, open_price: float) -> float:
        if self.bin_ticks and self.tick_size:
            return self.bin_ticks * self.tick_size
        size = open_price * self.bin_pct / 100.0
        if size <= 0:
            raise ValueError("bin size must be > 0")
        return size


@dataclass(frozen=True, slots=True)
class Level:
    price: float  # lower edge of the level
    bid: float  # taker SELL volume traded in this level
    ask: float  # taker BUY volume traded in this level


@dataclass(frozen=True, slots=True)
class FootprintSummary:
    """Compact per-candle footprint. ``to_dict``/``from_dict`` and the store CSV round-trip it."""

    pair: str
    ts: int  # candle open, ms UTC; known at ts + 4h
    open: float
    high: float
    low: float
    close: float
    volume: float
    buy_volume: float
    sell_volume: float
    delta: float
    delta_pct: float  # percent units
    cum_delta: float
    trades: int
    bin_size: float
    levels_n: int
    poc_price: float
    poc_volume: float
    buy_imbalances: int
    sell_imbalances: int
    bull_absorption: bool
    bear_absorption: bool
    source: str = ""
    levels: tuple[Level, ...] | None = None  # optional full per-level data (not in the CSV)

    def to_dict(self, include_levels: bool = False) -> dict[str, Any]:
        d = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "levels"}
        if include_levels and self.levels is not None:
            d["levels"] = [asdict(lv) for lv in self.levels]
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> FootprintSummary:
        kw: dict[str, Any] = {}
        for name in CSV_FIELDS:
            v = d[name]
            if name in _INT_FIELDS:
                kw[name] = int(float(v))
            elif name in _BOOL_FIELDS:
                kw[name] = v if isinstance(v, bool) else str(v).strip().lower() in ("1", "true")
            elif name in _STR_FIELDS:
                kw[name] = str(v)
            else:
                kw[name] = float(v)
        lv = d.get("levels")
        if lv:
            kw["levels"] = tuple(
                Level(float(x["price"]), float(x["bid"]), float(x["ask"])) for x in lv
            )
        return cls(**kw)

    def same_flow(self, other: FootprintSummary) -> bool:
        """Equal apart from the pair-level running ``cum_delta`` and the optional levels."""
        a, b = self.to_dict(), other.to_dict()
        a.pop("cum_delta")
        b.pop("cum_delta")
        return a == b


CSV_FIELDS = tuple(f.name for f in fields(FootprintSummary) if f.name != "levels")
_INT_FIELDS = {"ts", "trades", "levels_n", "buy_imbalances", "sell_imbalances"}
_BOOL_FIELDS = {"bull_absorption", "bear_absorption"}
_STR_FIELDS = {"pair", "source"}


class _CandleAcc:
    """Accumulates the trades of one candle: exact price -> [bid (taker sell), ask (taker buy)]."""

    __slots__ = ("close", "first_ts", "last_ts", "n", "open", "prices", "ts")

    def __init__(self, ts: int, t: FlowTrade) -> None:
        self.ts = ts
        self.first_ts = self.last_ts = t.ts
        self.open = self.close = t.price
        self.n = 0
        self.prices: dict[float, list[float]] = {}

    def add(self, t: FlowTrade) -> None:
        if t.ts < self.first_ts:
            self.first_ts, self.open = t.ts, t.price
        if t.ts >= self.last_ts:
            self.last_ts, self.close = t.ts, t.price
        slot = self.prices.setdefault(t.price, [0.0, 0.0])
        slot[1 if t.taker_side == "buy" else 0] += t.qty
        self.n += 1


def _levels(acc: _CandleAcc, size: float) -> tuple[int, dict[int, list[float]]]:
    by_idx: dict[int, list[float]] = {}
    for price, (bid, ask) in acc.prices.items():
        slot = by_idx.setdefault(math.floor(price / size + 1e-9), [0.0, 0.0])
        slot[0] += bid
        slot[1] += ask
    return min(by_idx), by_idx


def _imbalances(
    lo: int, hi: int, lv: Mapping[int, list[float]], p: FootprintParams
) -> tuple[int, int]:
    def hit(aggr: float, opposite: float) -> bool:
        if aggr <= 0 or (opposite <= 0 and not p.count_zero_opposite):
            return False
        return aggr >= p.imbalance_ratio * opposite

    zero = [0.0, 0.0]
    buys = sum(hit(lv.get(i, zero)[1], lv.get(i - 1, zero)[0]) for i in range(lo + 1, hi + 1))
    sells = sum(hit(lv.get(i, zero)[0], lv.get(i + 1, zero)[1]) for i in range(lo, hi))
    return buys, sells


def _absorption(
    acc: _CandleAcc, low: float, high: float, volume: float, share: float
) -> tuple[bool, bool]:
    rng = high - low
    if rng <= 0 or volume <= 0:
        return False, False
    lower, upper, mid = low + rng / 3, high - rng / 3, low + rng / 2
    sell_total = sum(b for b, _ in acc.prices.values())
    buy_total = sum(a for _, a in acc.prices.values())
    sell_low = sum(b for p, (b, _) in acc.prices.items() if p <= lower)
    buy_high = sum(a for p, (_, a) in acc.prices.items() if p >= upper)
    bull = sell_low >= share * volume and sell_low >= 0.5 * sell_total and acc.close > mid
    bear = buy_high >= share * volume and buy_high >= 0.5 * buy_total and acc.close < mid
    return bull, bear


def _finalize(
    acc: _CandleAcc, pair: str, p: FootprintParams, cum: float, source: str
) -> FootprintSummary:
    size = p.bin_size(acc.open)
    lo, lv = _levels(acc, size)
    hi = max(lv)
    poc_idx = min(lv, key=lambda i: (-(lv[i][0] + lv[i][1]), i))
    buy = sum(a for _, a in acc.prices.values())
    sell = sum(b for b, _ in acc.prices.values())
    volume = buy + sell
    delta = buy - sell
    low, high = min(acc.prices), max(acc.prices)
    bull, bear = _absorption(acc, low, high, volume, p.absorption_share)
    n_buy, n_sell = _imbalances(lo, hi, lv, p)
    levels = None
    if p.keep_levels:
        levels = tuple(Level(i * size, lv[i][0], lv[i][1]) for i in sorted(lv))
    return FootprintSummary(
        pair=pair,
        ts=acc.ts,
        open=acc.open,
        high=high,
        low=low,
        close=acc.close,
        volume=volume,
        buy_volume=buy,
        sell_volume=sell,
        delta=delta,
        delta_pct=delta / volume * 100.0 if volume > 0 else 0.0,
        cum_delta=cum + delta,
        trades=acc.n,
        bin_size=size,
        levels_n=hi - lo + 1,
        poc_price=poc_idx * size,
        poc_volume=lv[poc_idx][0] + lv[poc_idx][1],
        buy_imbalances=n_buy,
        sell_imbalances=n_sell,
        bull_absorption=bull,
        bear_absorption=bear,
        source=source,
        levels=levels,
    )


def aggregate_trades(
    trades: Iterable[FlowTrade],
    pair: str,
    params: FootprintParams | None = None,
    cum_start: float = 0.0,
    source: str = "",
) -> Iterator[FootprintSummary]:
    """Stream trades (roughly time-ordered) into per-candle summaries, oldest first.

    Constant memory: at most two candles are open at a time (a trade up to one candle late
    is still accepted; later stragglers are dropped with a warning). Zero-quantity trades
    are ignored.
    """
    p = params or FootprintParams()
    tf = p.timeframe_ms
    open_accs: dict[int, _CandleAcc] = {}
    done_before = -(2**62)  # candles < this ts are already emitted
    cum = cum_start
    dropped = 0
    for t in trades:
        if t.qty <= 0:
            continue
        c = candle_ts(t.ts, tf)
        if c < done_before:
            dropped += 1
            continue
        acc = open_accs.get(c)
        if acc is None:
            acc = open_accs[c] = _CandleAcc(c, t)
            for old in sorted(k for k in open_accs if k < c - tf):
                s = _finalize(open_accs.pop(old), pair, p, cum, source)
                cum = s.cum_delta
                done_before = old + tf
                yield s
        acc.add(t)
    for old in sorted(open_accs):
        s = _finalize(open_accs.pop(old), pair, p, cum, source)
        cum = s.cum_delta
        yield s
    if dropped:
        log.warning(
            "%s: dropped %d out-of-order trades older than an emitted candle", pair, dropped
        )


def footprints(
    trades: Iterable[FlowTrade], pair: str, params: FootprintParams | None = None, source: str = ""
) -> list[FootprintSummary]:
    """Sorted list version of :func:`aggregate_trades` (sorts the trades first)."""
    ordered = sorted(trades, key=lambda t: t.ts)
    return list(aggregate_trades(ordered, pair, params, source=source))


# ---------------------------------------------------------------------- live trades (ccxt)
def _now_ms(exchange: Any) -> int:
    clock = getattr(exchange, "milliseconds", None)
    return int(clock()) if callable(clock) else int(time.time() * 1000)


def _row_to_trade(row: Mapping[str, Any]) -> FlowTrade | None:
    """ccxt unified trade -> FlowTrade (``side`` is the taker side in ccxt), None if unusable."""
    side = row.get("side")
    if side not in ("buy", "sell") or row.get("timestamp") is None:
        return None
    tid = row.get("id")
    return FlowTrade(
        ts=int(row["timestamp"]),
        price=float(row["price"]),
        qty=float(row["amount"]),
        taker_side=side,
        trade_id=None if tid is None else str(tid),
    )


def _dedupe_key(t: FlowTrade) -> tuple[Any, ...]:
    return ("id", t.trade_id) if t.trade_id is not None else (t.ts, t.price, t.qty, t.taker_side)


def fetch_trades(
    exchange: Any,
    pair: str,
    since_ms: int,
    until_ms: int | None = None,
    limit: int = 1000,
    empty_step_ms: int = HOUR_MS,
    max_pages: int = 200_000,
) -> list[FlowTrade]:
    """All trades with ``since_ms <= ts < until_ms`` (None = now), ascending, de-duplicated.

    Pages ``exchange.fetch_trades(symbol, since=..., limit=...)``. The next ``since`` is the
    last returned timestamp (the same-millisecond overlap is removed by id, or by
    (ts, price, amount, side) when the exchange sends no id), +1 ms if that makes no
    progress. An empty page (or one holding only rows older than ``since``) advances
    ``since`` by ``empty_step_ms``: ccxt's Binance implementation answers ``since`` with a
    bounded time window (assumed ~1h), so a quiet hour must not end the download.
    """
    if limit <= 0:
        raise ValueError("limit must be > 0")
    until = _now_ms(exchange) if until_ms is None else until_ms
    seen: dict[tuple[Any, ...], FlowTrade] = {}
    since = since_ms
    for _ in range(max_pages):
        if since >= until:
            break
        page = exchange.fetch_trades(pair, since=since, limit=limit)
        if not page:
            since += empty_step_ms
            continue
        stamps = [int(r["timestamp"]) for r in page if r.get("timestamp") is not None]
        for row in page:
            t = _row_to_trade(row)
            if t is not None and since_ms <= t.ts < until:
                seen[_dedupe_key(t)] = t
        nxt = max(stamps, default=since - 1)
        if nxt > since:
            since = nxt
        elif nxt == since:
            since += 1  # everything left at this millisecond was returned (or a full page)
        else:
            since += empty_step_ms  # only stale rows before `since`: nothing new here
    else:
        log.warning("%s: fetch_trades stopped after %d pages", pair, max_pages)
    return sorted(seen.values(), key=lambda t: (t.ts, t.trade_id or ""))


# ---------------------------------------------------------------------- Binance aggTrades
def binance_symbol(pair: str) -> str:
    """``"BTC/USDT" -> "BTCUSDT"``."""
    return pair.split(":", 1)[0].replace("/", "").upper()


def aggtrades_url(pair: str, period: str) -> str:
    """Monthly (``period="2024-03"``) or daily (``"2024-03-15"``) aggTrades zip URL."""
    sym = binance_symbol(pair)
    kind = "monthly" if len(period) == 7 else "daily"
    return f"{BINANCE_DATA_URL}/{kind}/aggTrades/{sym}/{sym}-aggTrades-{period}.zip"


def _normalise_ts(raw: str) -> int:
    ts = int(float(raw))
    if ts >= 10**17:  # nanoseconds
        return ts // 1_000_000
    if ts >= 10**14:  # microseconds (newer Binance files)
        return ts // 1000
    return ts


def iter_aggtrades_csv(lines: Iterable[str]) -> Iterator[FlowTrade]:
    """Parse aggTrades CSV rows (with or without a header).

    Columns: agg_trade_id, price, quantity, first_trade_id, last_trade_id, transact_time,
    is_buyer_maker, is_best_match. ``is_buyer_maker`` true means the BUYER was the resting
    maker, so the taker SOLD.
    """
    for row in csv.reader(lines):
        if len(row) < 7 or not row[0].strip().lstrip("-").isdigit():
            continue  # header or blank line
        maker_buy = row[6].strip().lower() in ("true", "1", "t")
        yield FlowTrade(
            ts=_normalise_ts(row[5]),
            price=float(row[1]),
            qty=float(row[2]),
            taker_side="sell" if maker_buy else "buy",
            trade_id=row[0].strip(),
        )


def iter_aggtrades_zip(path: str | Path) -> Iterator[FlowTrade]:
    """Stream every CSV member of an aggTrades zip (constant memory)."""
    with zipfile.ZipFile(path) as zf:
        for name in sorted(n for n in zf.namelist() if n.lower().endswith(".csv")):
            with zf.open(name) as raw:
                yield from iter_aggtrades_csv(io.TextIOWrapper(raw, encoding="utf-8", newline=""))


def zip_footprints(
    path: str | Path, pair: str, params: FootprintParams | None = None
) -> Iterator[FootprintSummary]:
    """aggTrades zip -> per-candle summaries, streamed."""
    return aggregate_trades(iter_aggtrades_zip(path), pair, params, source=SOURCE_BINANCE)


def default_fetch(url: str, timeout: float = 120.0) -> bytes:
    import urllib.request

    if not url.startswith("https://"):
        raise ValueError(f"refusing non-https url {url!r}")
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - https only
        return resp.read()


def download_aggtrades(
    pair: str,
    period: str,
    cache_dir: str | Path,
    fetch: Fetch | None = None,
    verify: bool = True,
) -> Path:
    """Download one aggTrades zip into ``cache_dir`` (skipped if already cached).

    With ``verify`` the ``<url>.CHECKSUM`` file (assumed ``"<sha256>  <name>"``) is fetched
    too; a mismatch raises, an unavailable checksum only logs a warning.
    """
    fetch = fetch or default_fetch
    url = aggtrades_url(pair, period)
    dest = Path(cache_dir) / url.rsplit("/", 1)[1]
    if dest.exists():
        return dest
    body = fetch(url)
    if verify:
        try:
            expected = fetch(url + ".CHECKSUM").decode("utf-8").split()[0].lower()
        except Exception as exc:  # checksum is a best-effort extra
            log.warning("no checksum for %s: %s", url, exc)
        else:
            got = hashlib.sha256(body).hexdigest()
            if got != expected:
                raise ValueError(f"{dest.name}: sha256 {got} != published {expected}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".zip.tmp")
    tmp.write_bytes(body)
    tmp.replace(dest)
    return dest


def history_periods(now_ms: int, months: Any = 0, days: Any = 0) -> list[str]:
    """Explicit period lists pass through; ints mean the last N COMPLETE months / days."""
    now = datetime.fromtimestamp(now_ms / 1000, tz=UTC)
    out: list[str] = []
    if isinstance(months, (list, tuple)):
        out.extend(str(m) for m in months)
    else:
        y, m = now.year, now.month
        for _ in range(int(months or 0)):
            y, m = (y, m - 1) if m > 1 else (y - 1, 12)
            out.append(f"{y:04d}-{m:02d}")
    if isinstance(days, (list, tuple)):
        out.extend(str(d) for d in days)
    else:
        for k in range(int(days or 0), 0, -1):
            out.append((now - timedelta(days=k)).strftime("%Y-%m-%d"))
    return sorted(set(out))


# ---------------------------------------------------------------------- store
def store_path(state_dir: str | Path, pair: str) -> Path:
    return Path(state_dir) / STORE_DIR / pair_filename(pair, "4h")


def load_store(state_dir: str | Path, pair: str) -> dict[int, FootprintSummary]:
    path = store_path(state_dir, pair)
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as fh:
        rows = [FootprintSummary.from_dict(r) for r in csv.DictReader(fh)]
    return {s.ts: s for s in rows}


def _write_store(path: Path, rows: Sequence[FootprintSummary]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        w.writeheader()
        for s in rows:
            w.writerow(s.to_dict())
    tmp.replace(path)


def merge_store(
    state_dir: str | Path, pair: str, summaries: Iterable[FootprintSummary]
) -> dict[str, int]:
    """Merge summaries into the pair's CSV (a new copy of a candle replaces the old one).

    ``cum_delta`` is recomputed over the whole stored series. Merging the same summaries
    twice changes nothing (the file is not rewritten).
    """
    have = load_store(state_dir, pair)
    added = replaced = 0
    for s in summaries:
        s = replace(s, pair=pair, levels=None)
        old = have.get(s.ts)
        if old is None:
            added += 1
        elif old.same_flow(s):
            continue
        else:
            replaced += 1
        have[s.ts] = s
    rows: list[FootprintSummary] = []
    cum = 0.0
    changed = bool(added or replaced)
    for ts in sorted(have):
        cum += have[ts].delta
        s = have[ts]
        if s.cum_delta != cum:
            s, changed = replace(s, cum_delta=cum), True
        rows.append(s)
    if changed:
        _write_store(store_path(state_dir, pair), rows)
    return {"added": added, "replaced": replaced, "stored": len(rows)}


def load_sources(state_dir: str | Path) -> dict[str, dict[str, dict[int, FootprintSummary]]]:
    """``{"orderflow": {pair: {candle_ts: FootprintSummary}}}`` from every stored pair."""
    out: dict[str, dict[int, FootprintSummary]] = {}
    folder = Path(state_dir) / STORE_DIR
    for path in sorted(folder.glob("*-4h.csv")) if folder.is_dir() else []:
        with path.open(encoding="utf-8", newline="") as fh:
            rows = [FootprintSummary.from_dict(r) for r in csv.DictReader(fh)]
        for s in rows:
            out.setdefault(s.pair, {})[s.ts] = s
    return {"orderflow": out}


# ---------------------------------------------------------------------- collect
def _history_done_path(state_dir: Path) -> Path:
    return state_dir / STORE_DIR / "history_done.json"


def _backfill_pair(
    state_dir: Path,
    pair: str,
    periods: Sequence[str],
    of: Mapping[str, Any],
    fetch: Fetch,
    params: FootprintParams,
) -> dict[str, Any]:
    done_path = _history_done_path(state_dir)
    done = set(json.loads(done_path.read_text(encoding="utf-8"))) if done_path.exists() else set()
    cache = Path(of.get("cache_dir") or state_dir / STORE_DIR / "cache")
    res: dict[str, Any] = {"files": 0, "added": 0, "replaced": 0, "errors": []}
    for period in periods:
        key = f"{pair}|{period}"
        if key in done:
            continue
        try:
            path = download_aggtrades(pair, period, cache, fetch, bool(of.get("verify", True)))
            m = merge_store(state_dir, pair, zip_footprints(path, pair, params))
        except Exception as exc:
            res["errors"].append(f"{period}: {type(exc).__name__}: {exc}")
            continue
        res["files"] += 1
        res["added"] += m["added"]
        res["replaced"] += m["replaced"]
        done.add(key)
        done_path.parent.mkdir(parents=True, exist_ok=True)
        done_path.write_text(json.dumps(sorted(done)), encoding="utf-8")
        if not of.get("keep_zips", False):
            path.unlink(missing_ok=True)
    return res


def _live_pair(
    state_dir: Path,
    pair: str,
    exchange: Any,
    of: Mapping[str, Any],
    params: FootprintParams,
    now: int,
) -> dict[str, Any]:
    tf = params.timeframe_ms
    have = load_store(state_dir, pair)
    first_open = candle_ts(now, tf)  # the still-open candle is never stored
    oldest = first_open - int(of.get("max_live_candles", 42)) * tf
    since = max(have) + tf if have else first_open - int(of.get("bootstrap_candles", 6)) * tf
    since = max(since, oldest)
    if since >= first_open:
        return {"added": 0, "replaced": 0, "since_utc": ms_to_iso(since)}
    trades = fetch_trades(
        exchange, pair, since, first_open, limit=int(of.get("trades_limit", 1000))
    )
    fps = [s for s in footprints(trades, pair, params, SOURCE_LIVE) if s.ts + tf <= now]
    m = merge_store(state_dir, pair, fps)
    return {
        "added": m["added"],
        "replaced": m["replaced"],
        "trades": len(trades),
        "since_utc": ms_to_iso(since),
    }


def collect(
    state_dir: str | Path,
    settings: Mapping[str, Any] | None = None,
    exchange: Any = None,
    fetch: Fetch | None = None,
    *,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Update the footprint store for every pair; returns a per-pair summary.

    ``settings``: the bot settings dict (``pairs``) with an optional ``orderflow`` block:
    ``history_months`` (int = last N complete months, or a list of "YYYY-MM"),
    ``history_days`` (int or list of "YYYY-MM-DD"; daily files for the current month),
    ``cache_dir``, ``keep_zips``, ``verify``, ``bootstrap_candles`` (live window on an empty
    store, default 6), ``max_live_candles`` (default 42 = 7 days; older gaps need the
    Binance history), ``trades_limit``, and any :class:`FootprintParams` field.
    History is only downloaded when ``fetch`` is given (use :func:`default_fetch` for the
    real site); live trades only when ``exchange`` is given. Each downloaded period is
    recorded in ``orderflow/history_done.json`` and not processed again.
    """
    sd = Path(state_dir)
    settings = settings or {}
    of = dict(settings.get("orderflow") or {})
    pairs = list(of.get("pairs") or settings.get("pairs") or DEFAULT_PAIRS)
    params = FootprintParams.from_settings(of)
    now = now_ms if now_ms is not None else _now_ms(exchange)
    periods = history_periods(now, of.get("history_months", 0), of.get("history_days", 0))
    out: dict[str, Any] = {"state_dir": str(sd), "now_utc": ms_to_iso(now), "pairs": {}}
    for pair in pairs:
        res: dict[str, Any] = {}
        if fetch is not None and periods:
            res["history"] = _backfill_pair(sd, pair, periods, of, fetch, params)
        if exchange is not None:
            try:
                res["live"] = _live_pair(sd, pair, exchange, of, params, now)
            except Exception as exc:  # one pair's failure never stops the others
                log.exception("%s: live order-flow collection failed", pair)
                res["live"] = {"error": f"{type(exc).__name__}: {exc}"}
        have = load_store(sd, pair)
        res["stored"] = len(have)
        res["last_utc"] = ms_to_iso(max(have)) if have else None
        out["pairs"][pair] = res
    return out


# ---------------------------------------------------------------------- layer
def _veto_none(fp: FootprintSummary) -> bool:
    return False


def _veto_delta_neg(fp: FootprintSummary) -> bool:
    return fp.delta_pct < 0


def _veto_delta_neg10(fp: FootprintSummary) -> bool:
    return fp.delta_pct < -10.0


def _veto_absorbed(fp: FootprintSummary) -> bool:
    return fp.bear_absorption or fp.buy_imbalances == 0


# Pre-registered candidate rules, in tie-break order (the first, "no veto", wins ties).
RULES: dict[str, tuple[Callable[[FootprintSummary], bool], str]] = {
    "none": (_veto_none, "no veto"),
    "delta_lt_0": (_veto_delta_neg, "veto if the signal candle's delta % < 0"),
    "delta_lt_-10": (_veto_delta_neg10, "veto if the signal candle's delta % < -10 %"),
    "absorbed_or_no_buy_imbalance": (
        _veto_absorbed,
        "veto if the breakout candle shows bear absorption (taker buying absorbed at the "
        "highs, close in the lower half) or no diagonal buy imbalance",
    ),
}


@register
class OrderFlowLayer(Layer):
    """Veto from the signal candle's footprint, with one pre-registered rule chosen on TRAIN.

    The footprint of candle ``row.ts`` is complete at ``row.close_ts`` (the decision time),
    so using it is causal. A signal without a footprint is never vetoed.
    """

    name = "orderflow"
    kind = "orderflow"
    description = (
        "Order-flow footprint veto: the signal candle's delta % / absorption / diagonal "
        "imbalances, one of 4 pre-registered rules picked on the training window"
    )

    def __init__(self, **params: Any) -> None:
        super().__init__(**params)
        self.rule = "none"
        self.fit_report: dict[str, Any] = {}
        self._flow: dict[str, dict[int, FootprintSummary]] = {}
        self._state_dir: str | None = params.get("state_dir")
        self._mtimes: dict[str, float] = {}

    # -- data
    def _flow_from(self, ctx: TrainContext) -> dict[str, dict[int, FootprintSummary]]:
        flow = ctx.sources.get("orderflow")
        if flow is None and ctx.state_dir is not None:
            flow = load_sources(ctx.state_dir)["orderflow"]
        return flow or {}

    def _joined(self, ctx: TrainContext) -> tuple[list[tuple[FootprintSummary, float]], int]:
        flow = self._flow_from(ctx)
        ex = ctx.examples()
        joined = [
            (flow[p][ts], r)
            for p, ts, _f, r in ex
            if ts + TIMEFRAME_MS <= ctx.until_ts and ts in flow.get(p, {})
        ]
        return joined, len(ex)

    def _lookup(self, pair: str, ts: int) -> FootprintSummary | None:
        fp = self._flow.get(pair, {}).get(ts)
        if fp is None and self._state_dir:
            path = store_path(self._state_dir, pair)
            mtime = path.stat().st_mtime if path.exists() else 0.0
            if mtime and mtime != self._mtimes.get(pair):
                self._mtimes[pair] = mtime
                self._flow[pair] = load_store(self._state_dir, pair)
                fp = self._flow[pair].get(ts)
        return fp

    # -- Layer interface
    def ready(self, ctx: TrainContext) -> tuple[bool, str]:
        joined, n = self._joined(ctx)
        need_cov = float(self.params.get("min_coverage", 0.8))
        need_n = int(self.params.get("min_examples", 30))
        cov = len(joined) / n if n else 0.0
        msg = (
            f"footprints cover {len(joined)} of {n} training signals ({cov:.0%}; needs "
            f">= {need_cov:.0%} and >= {need_n})"
        )
        if cov >= need_cov and len(joined) >= need_n:
            return True, msg
        return False, (
            msg + "; backfill history from Binance aggTrades (orderflow.history_months) or "
            "collect live trades with orderflow.collect() first"
        )

    def fit(self, ctx: TrainContext) -> None:
        joined, n = self._joined(ctx)
        scores: dict[str, dict[str, Any]] = {}
        best, best_avg = "none", None
        for rule, (blocks, _desc) in RULES.items():
            kept = [r for fp, r in joined if not blocks(fp)]
            avg = statistics.fmean(kept) if kept else None
            scores[rule] = {"kept": len(kept), "avg_r": None if avg is None else round(avg, 4)}
            if avg is not None and (best_avg is None or avg > best_avg + 1e-12):
                best, best_avg = rule, avg
        self.rule = best
        self.fit_report = {"train_signals": n, "with_footprint": len(joined), "rules": scores}
        self._flow = self._flow_from(ctx)
        if ctx.state_dir is not None:
            self._state_dir = str(ctx.state_dir)

    def veto(self, pair: str, row: FeatureRow, view: MarketView) -> tuple[bool, str]:
        blocks, desc = RULES[self.rule]
        if self.rule == "none":
            return False, "fitted rule is 'no veto'"
        fp = self._lookup(pair, row.ts)
        if fp is None:
            return False, "no footprint for the signal candle (not vetoed)"
        detail = (
            f"delta {fp.delta_pct:+.1f}%, buy imbalances {fp.buy_imbalances}, "
            f"bear absorption {fp.bear_absorption}"
        )
        return (True, f"{desc}: {detail}") if blocks(fp) else (False, f"passed ({detail})")

    def state(self) -> dict[str, Any]:
        return {
            "params": self.params,
            "rule": self.rule,
            "fit_report": self.fit_report,
            "state_dir": self._state_dir,
        }

    def load_state(self, d: Mapping[str, Any]) -> None:
        super().load_state(d)
        rule = d.get("rule", "none")
        if rule not in RULES:
            raise KeyError(f"unknown order-flow rule {rule!r}")
        self.rule = rule
        self.fit_report = dict(d.get("fit_report", {}))
        self._state_dir = d.get("state_dir") or self.params.get("state_dir")
        self._flow, self._mtimes = {}, {}
