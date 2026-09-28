"""Sentiment sources and sentiment layers: Fear & Greed, crypto headlines, auto R5 events.

Data sources (all free; every one takes an injectable ``fetch(url, headers) -> bytes`` so
tests run offline; nothing touches the network at import):

- Fear & Greed index (alternative.me): full daily history. The value dated day ``D`` is
  treated as KNOWN FROM the next UTC midnight ``D + 1 day`` (conservative, no look-ahead).
- News RSS / Atom feeds (CoinDesk, Cointelegraph, Decrypt, The Block, Bitcoin Magazine by
  default), parsed with ``xml.etree`` after a size cap; documents with a DOCTYPE or ENTITY
  declaration are rejected outright (no entity-expansion attacks).
- Reddit public JSON listings (r/CryptoCurrency, r/Bitcoin, r/ethereum by default).
- CryptoPanic (optional): only with a token in ``CRYPTOPANIC_TOKEN`` (or settings),
  otherwise skipped cleanly.

Every headline becomes a :class:`NewsItem` whose ``known_from_ts`` is the time WE fetched it
(``fetched_ts``), never its claimed publication time: a backtest on the stored items can
only use what the bot had actually seen. Items are tagged with coins by keyword / ticker,
scored in [-1, 1] (:class:`FinBertScorer` if ``transformers`` is installed, else the stdlib
:class:`LexiconScorer`) and stored under ``state_dir/sentiment/``:

- ``news.csv``          deduplicated items with their scores (first fetch wins);
- ``fear_greed.csv``    the daily index with its known-from time;
- ``sentiment_4h.csv``  per coin (and ``ALL``) per 4H bucket of KNOWN-FROM time: mean score
  and item count. An item fetched after a bucket ended is never counted in that bucket.

High-impact headlines (hacks, exploits, SEC suits, lawsuits, delistings, halted withdrawals,
bans, insolvency) become unscheduled high-impact R5 events (:func:`auto_events`) appended to
the bot's events CSV (:func:`append_events`). That only ever ADDS blackouts.

Layers (kind ``"sentiment"``, veto only, see ``layers.py``):

- ``fear_greed``: picks, on the TRAIN window only, one rule from the pre-registered set
  ``none | F&G <= 25 | F&G >= 75 | F&G <= 40`` (best train avg R of the kept trades; ties
  and too-few-kept -> ``none``) and applies it with the value known at ``row.close_ts``.
- ``news_sentiment``: veto when the coin's trailing-24h mean headline score (buckets that
  closed before ``row.close_ts``) is below a threshold picked the same way from
  ``none | < -0.2 | < -0.4``. It needs >= 30 days of COLLECTED headlines inside the train
  window: there is no honest way to backtest it on history that was never collected.
"""

from __future__ import annotations

import bisect
import csv
import html
import importlib.util
import json
import logging
import math
import os
import re
import statistics
import time
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from .layers import Layer, MarketView, TrainContext, register
from .models import DAY_MS, HOUR_MS, FeatureRow, NewsEvent, base_of
from .news import (
    CSV_HEADER,
    CSV_HEADER_WITH_KNOWN_FROM,
    format_time_utc,
    load_events,
    parse_time_utc,
)


log = logging.getLogger("trendbot.sentiment")

Fetch = Callable[[str, Mapping[str, str]], bytes]

USER_AGENT = "trendbot-research/1.0 (+https://github.com/freqtrade/freqtrade; sentiment)"
TIMEOUT_S = 15.0
MAX_BYTES = 5 * 1024 * 1024
BUCKET_MS = 4 * HOUR_MS
SUBDIR = "sentiment"
NEWS_CSV = "news.csv"
FG_CSV = "fear_greed.csv"
AGG_CSV = "sentiment_4h.csv"
ALL = "ALL"

FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=0&format=json"
DEFAULT_FEEDS: dict[str, str] = {
    "coindesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "cointelegraph": "https://cointelegraph.com/rss",
    "decrypt": "https://decrypt.co/feed",
    "theblock": "https://www.theblock.co/rss.xml",
    "bitcoinmagazine": "https://bitcoinmagazine.com/.rss/full/",
}
DEFAULT_SUBREDDITS: tuple[str, ...] = ("CryptoCurrency", "Bitcoin", "ethereum")
REDDIT_URL = "https://www.reddit.com/r/{sub}/top.json?t=day&limit=50"
CRYPTOPANIC_URL = "https://cryptopanic.com/api/v1/posts/?auth_token={token}&public=true"
CRYPTOPANIC_ENV = "CRYPTOPANIC_TOKEN"

# coin -> keywords. An ALL-CAPS keyword (and the coin ticker itself) is matched
# case-sensitively as a whole word ("SOL", "$SOL"); any other keyword case-insensitively.
DEFAULT_COINS: dict[str, tuple[str, ...]] = {
    "BTC": ("bitcoin", "XBT"),
    "ETH": ("ethereum", "ether"),
    "BNB": ("binance coin", "bnb chain", "bnb"),
    "SOL": ("solana",),
    "XRP": ("ripple", "xrp"),
    "ADA": ("cardano",),
    "DOGE": ("dogecoin", "doge"),
    "AVAX": ("avalanche",),
    "DOT": ("polkadot",),
    "LINK": ("chainlink",),
    "TRX": ("tron",),
    "LTC": ("litecoin",),
    "TON": ("toncoin",),
    "POL": ("polygon", "MATIC"),
}


# ---------------------------------------------------------------------- HTTP
def http_fetch(
    url: str,
    headers: Mapping[str, str] | None = None,
    *,
    timeout: float = TIMEOUT_S,
    max_bytes: int = MAX_BYTES,
) -> bytes:
    """GET ``url`` with a User-Agent and a timeout; refuse bodies larger than ``max_bytes``."""
    if not url.startswith(("https://", "http://")):
        raise ValueError(f"refusing non-HTTP URL {url[:60]!r}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})  # noqa: S310
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - scheme checked
        body = resp.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise ValueError(f"response larger than {max_bytes} bytes")
    return body


def _get(fetch: Fetch, url: str, headers: Mapping[str, str] | None = None) -> bytes:
    body = fetch(url, dict(headers or {}))
    if not isinstance(body, bytes | bytearray):
        raise TypeError("fetch must return bytes")
    if len(body) > MAX_BYTES:
        raise ValueError(f"response larger than {MAX_BYTES} bytes")
    return bytes(body)


# ---------------------------------------------------------------------- items
@dataclass(frozen=True, slots=True)
class NewsItem:
    source: str  # "rss:coindesk", "reddit:Bitcoin", "cryptopanic"
    item_id: str
    url: str
    title: str
    published_ts: int | None  # as claimed by the source (informational only)
    fetched_ts: int  # when we first saw it = when it became known to the bot
    coins: tuple[str, ...] = ()
    score: float | None = None
    scorer: str = ""

    @property
    def known_from_ts(self) -> int:
        return self.fetched_ts

    @property
    def key(self) -> str:
        return f"{self.source}|{self.item_id or self.url}"


def _clean(text: object) -> str:
    return " ".join(html.unescape(str(text or "")).split())


def _parse_date(text: str | None) -> int | None:
    """RFC 2822 (RSS) or ISO-8601 (Atom) -> epoch ms; naive times are taken as UTC."""
    s = (text or "").strip()
    if not s:
        return None
    dt: datetime | None = None
    try:
        dt = parsedate_to_datetime(s)
    except (TypeError, ValueError, IndexError):
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


class CoinTagger:
    """Tag a headline with coin tickers by name / ticker keyword (whole-word matching)."""

    def __init__(self, coins: Mapping[str, Sequence[str]] | None = None) -> None:
        self.patterns: list[tuple[str, re.Pattern[str]]] = []
        for coin, words in (coins or DEFAULT_COINS).items():
            cs = [re.escape(coin.upper())] + [re.escape(w) for w in words if w.isupper()]
            ci = [re.escape(w) for w in words if not w.isupper()]
            parts = [rf"(?-i:\$?(?:{'|'.join(cs)}))"]
            if ci:
                parts.append(rf"(?:{'|'.join(ci)})")
            rx = re.compile(rf"(?<![\w$])(?:{'|'.join(parts)})(?!\w)", re.IGNORECASE)
            self.patterns.append((coin.upper(), rx))

    def tag(self, text: str) -> tuple[str, ...]:
        return tuple(sorted(c for c, rx in self.patterns if rx.search(text)))


# ---------------------------------------------------------------------- parsers
def parse_fear_greed(body: bytes) -> list[tuple[int, int, str]]:
    """alternative.me JSON -> sorted ``[(date_ts_ms, value 0..100, classification)]``.

    Rows with a missing / non-numeric / out-of-range value are skipped.
    """
    doc = json.loads(body.decode("utf-8"))
    rows = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        raise ValueError("fear & greed response has no 'data' list")
    out: dict[int, tuple[int, int, str]] = {}
    for r in rows:
        try:
            day = int(r["timestamp"]) * 1000 // DAY_MS * DAY_MS
            value = int(float(r["value"]))
        except (KeyError, TypeError, ValueError):
            continue
        if 0 <= value <= 100:
            out[day] = (day, value, str(r.get("value_classification", "")))
    return [out[k] for k in sorted(out)]


def fg_known_from(date_ts: int) -> int:
    """The F&G value dated ``date_ts`` is known from the next UTC midnight after it."""
    return (date_ts // DAY_MS + 1) * DAY_MS


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _child_text(el: ET.Element, *names: str) -> str:
    for child in el:
        if _local(child.tag) in names and (child.text or "").strip():
            return child.text.strip()  # type: ignore[union-attr]
    return ""


def _entry_link(el: ET.Element) -> str:
    fallback = ""
    for child in el:
        if _local(child.tag) != "link":
            continue
        href = child.get("href")
        if href is None:  # RSS: <link>url</link>
            if (child.text or "").strip():
                return child.text.strip()  # type: ignore[union-attr]
            continue
        if child.get("rel", "alternate") == "alternate":
            return href.strip()
        fallback = fallback or href.strip()
    return fallback


_DTD_RE = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)", re.IGNORECASE)


def parse_feed(body: bytes, source: str, fetched_ts: int) -> list[NewsItem]:
    """RSS 2.0 / RSS 1.0 / Atom bytes -> items. DOCTYPE/ENTITY or oversize input: ValueError."""
    if len(body) > MAX_BYTES:
        raise ValueError(f"feed larger than {MAX_BYTES} bytes")
    if _DTD_RE.search(body):
        raise ValueError("feed contains a DOCTYPE/ENTITY declaration; refused")
    try:
        root = ET.fromstring(body)  # noqa: S314 - size-capped and DTD-free (checked above)
    except ET.ParseError as exc:
        raise ValueError(f"malformed feed XML: {exc}") from None
    items = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        title = _clean(_child_text(el, "title"))
        url = _entry_link(el)
        if not title:
            continue
        item_id = _child_text(el, "guid", "id") or url or title
        published = _parse_date(_child_text(el, "pubdate", "published", "updated", "date"))
        items.append(NewsItem(source, item_id, url, title, published, fetched_ts))
    return items


def parse_reddit(body: bytes, subreddit: str, fetched_ts: int) -> list[NewsItem]:
    """Reddit listing JSON (``/top.json``) -> items (stickied posts skipped)."""
    doc = json.loads(body.decode("utf-8"))
    children = (doc.get("data") or {}).get("children") if isinstance(doc, dict) else None
    if not isinstance(children, list):
        raise ValueError("reddit response has no data.children list")
    out = []
    for ch in children:
        d = ch.get("data") if isinstance(ch, dict) else None
        if not isinstance(d, dict) or d.get("stickied") or not d.get("title"):
            continue
        permalink = str(d.get("permalink") or "")
        url = f"https://www.reddit.com{permalink}" if permalink.startswith("/") else permalink
        try:
            published: int | None = int(float(d["created_utc"]) * 1000)
        except (KeyError, TypeError, ValueError):
            published = None
        item_id = str(d.get("name") or d.get("id") or url)
        out.append(
            NewsItem(f"reddit:{subreddit}", item_id, url, _clean(d["title"]), published, fetched_ts)
        )
    return out


def parse_cryptopanic(body: bytes, fetched_ts: int) -> list[NewsItem]:
    """CryptoPanic posts JSON -> items; its ``currencies[].code`` tags are kept as coins."""
    doc = json.loads(body.decode("utf-8"))
    results = doc.get("results") if isinstance(doc, dict) else None
    if not isinstance(results, list):
        raise ValueError("cryptopanic response has no 'results' list")
    out = []
    for r in results:
        if not isinstance(r, dict) or not r.get("title"):
            continue
        item_id = str(r.get("id") or r.get("slug") or r.get("url") or r["title"])
        url = str(r.get("url") or f"https://cryptopanic.com/news/{item_id}/")
        tagged = r.get("currencies") or r.get("instruments") or []
        coins = tuple(
            sorted(
                {str(c["code"]).upper() for c in tagged if isinstance(c, dict) and c.get("code")}
            )
        )
        published = _parse_date(r.get("published_at") or r.get("created_at"))
        out.append(
            NewsItem("cryptopanic", item_id, url, _clean(r["title"]), published, fetched_ts, coins)
        )
    return out


# ---------------------------------------------------------------------- scoring
def _lex(groups: Mapping[float, str]) -> dict[str, float]:
    return {w.replace("_", " "): v for v, words in groups.items() for w in words.split()}


LEXICON: dict[str, float] = _lex(
    {
        3.0: "skyrocket skyrockets skyrocketed soar soars soared soaring all_time_high "
        "record_high moon moons mooning",
        2.0: "surge surges surged surging rally rallies rallied rallying breakout jump jumps "
        "jumped bullish approve approves approved approval boom booming rebound rebounds "
        "rebounded recover recovers recovered recovery gain gains gained upgrade upgraded "
        "adoption inflow inflows outperform outperforms profit profits profitable win wins "
        "optimism optimistic breakthrough milestone record",
        1.0: "rise rises rising rose climb climbs climbed up higher high green buy buying "
        "accumulate accumulation partnership partner launch launches launched support "
        "growth grow grows strong strength positive boost boosts boosted bull bulls "
        "integrate integrates listing lists listed expands expansion",
        -1.0: "fall falls fell falling drop drops dropped decline declines declined down lower "
        "low dip dips weak weakness red sell selling concern concerns worry worries risk "
        "risks uncertainty volatile warns warning delay delays delayed bear bears outflow "
        "outflows negative pressure probe investigation fine fined slow",
        -2.0: "plunge plunges plunged tumble tumbles tumbled slump slumps slumped sink sinks "
        "sank bearish sell_off selloff dump dumps dumped loss losses liquidation "
        "liquidations liquidated reject rejects rejected rejection lawsuit sue sues sued "
        "charges charged ban bans banned delist delists delisted halt halts halted "
        "suspend suspends suspended fear panic outage vulnerability bug crisis",
        -3.0: "crash crashes crashed collapse collapses collapsed hack hacks hacked exploit "
        "exploited exploits drained stolen theft scam fraud ponzi rug_pull bankrupt "
        "bankruptcy insolvent insolvency capitulation",
    }
)
NEGATORS = frozenset(
    "not no never without nor none neither cannot fails fail failed despite barely".split()
)
INTENSIFIERS: dict[str, float] = {
    "very": 1.3, "extremely": 1.5, "massive": 1.5, "massively": 1.5, "huge": 1.4,
    "sharply": 1.4, "sharp": 1.3, "major": 1.3, "big": 1.2, "biggest": 1.4, "strongly": 1.3,
    "record": 1.2, "slightly": 0.5, "modest": 0.6, "modestly": 0.6, "somewhat": 0.7,
    "minor": 0.6, "partially": 0.7, "small": 0.7, "mild": 0.6, "mildly": 0.6,
}  # fmt: skip
_TOKEN_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


class LexiconScorer:
    """Stdlib headline scorer: crypto/finance lexicon with negation and intensifiers.

    Each lexicon hit (1-3 word phrases, longest first) has a valence in [-3, 3]; it is
    multiplied by an intensifier directly before it and flipped (x -0.74) if a negator is
    among the 3 tokens before it. The sum ``s`` is squashed to ``s / sqrt(s^2 + 15)`` in
    (-1, 1); a headline with no hit scores 0.
    """

    name = "lexicon"

    def available(self) -> tuple[bool, str]:
        return True, "stdlib"

    @staticmethod
    def _hit(tokens: list[str], i: int) -> tuple[float, int]:
        for n in (3, 2, 1):
            phrase = " ".join(tokens[i : i + n])
            if len(tokens) - i >= n and phrase in LEXICON:
                return LEXICON[phrase], n
        return 0.0, 1

    def score(self, text: str) -> float:
        tokens = _TOKEN_RE.findall(text.lower().replace("\u2019", "'"))
        total, i = 0.0, 0
        while i < len(tokens):
            val, n = self._hit(tokens, i)
            if val:
                if i > 0:
                    val *= INTENSIFIERS.get(tokens[i - 1], 1.0)
                window = tokens[max(0, i - 3) : i]
                if any(t in NEGATORS or t.endswith("n't") for t in window):
                    val *= -0.74
                total += val
            i += n
        return max(-1.0, min(1.0, total / math.sqrt(total * total + 15.0)))

    def score_many(self, texts: Sequence[str]) -> list[float]:
        return [self.score(t) for t in texts]


class FinBertScorer:
    """HuggingFace ``ProsusAI/finbert``: score = P(positive) - P(negative), in [-1, 1].

    ``transformers`` (and torch) are imported lazily on first use; ``available()`` is False
    without them. A ready-made ``pipeline`` callable can be injected (tests).
    """

    name = "finbert"
    MODEL = "ProsusAI/finbert"

    def __init__(self, pipeline: Callable[..., Any] | None = None, batch_size: int = 16) -> None:
        self._pipe = pipeline
        self.batch_size = batch_size

    def available(self) -> tuple[bool, str]:
        if self._pipe is not None:
            return True, "injected pipeline"
        try:
            found = importlib.util.find_spec("transformers") is not None
        except (ImportError, ValueError):
            found = False
        return (found, "transformers installed" if found else "transformers is not installed")

    def _pipeline(self) -> Callable[..., Any]:
        if self._pipe is None:
            from transformers import pipeline  # lazy: heavy optional dependency

            self._pipe = pipeline("text-classification", model=self.MODEL, top_k=None)
        return self._pipe

    @staticmethod
    def _one(res: Any) -> float:
        rows = res if isinstance(res, list) else [res]
        probs = {str(r.get("label", "")).lower(): float(r.get("score", 0.0)) for r in rows}
        return max(-1.0, min(1.0, probs.get("positive", 0.0) - probs.get("negative", 0.0)))

    def score_many(self, texts: Sequence[str]) -> list[float]:
        if not texts:
            return []
        out = self._pipeline()(list(texts), batch_size=self.batch_size, truncation=True)
        return [self._one(r) for r in out]

    def score(self, text: str) -> float:
        return self.score_many([text])[0]


def make_scorer(kind: str = "auto") -> LexiconScorer | FinBertScorer:
    """``"lexicon"``, ``"finbert"`` (RuntimeError if not installed) or ``"auto"``."""
    k = (kind or "auto").strip().lower()
    if k == "lexicon":
        return LexiconScorer()
    if k not in ("finbert", "auto"):
        raise ValueError(f"unknown scorer {kind!r}; use auto, finbert or lexicon")
    fb = FinBertScorer()
    ok, why = fb.available()
    if ok:
        return fb
    if k == "finbert":
        raise RuntimeError(f"FinBERT scorer requested but {why}")
    return LexiconScorer()


def score_items(
    items: Sequence[NewsItem], scorer: LexiconScorer | FinBertScorer
) -> tuple[list[NewsItem], str]:
    """Score every item; if the scorer fails, fall back to the lexicon (never raises)."""
    try:
        scores = scorer.score_many([i.title for i in items])
        if len(scores) != len(items):
            raise ValueError(f"{len(scores)} scores for {len(items)} items")
        name = scorer.name
    except Exception as exc:
        log.error("scorer %s failed (%s); falling back to the lexicon", scorer.name, exc)
        scores, name = LexiconScorer().score_many([i.title for i in items]), "lexicon"
    return [
        replace(i, score=round(s, 4), scorer=name) for i, s in zip(items, scores, strict=True)
    ], name


# ---------------------------------------------------------------------- store
NEWS_HEADER = (
    "key", "source", "item_id", "url", "title", "published_utc", "fetched_utc", "coins",
    "score", "scorer",
)  # fmt: skip
FG_HEADER = ("date_utc", "value", "classification", "known_from_utc")
AGG_HEADER = ("coin", "bucket_utc", "mean_score", "n")


def sentiment_dir(state_dir: str | Path) -> Path:
    return Path(state_dir) / SUBDIR


def _write_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    tmp.replace(path)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _item_from_row(r: Mapping[str, str]) -> NewsItem:
    score = r.get("score", "")
    return NewsItem(
        source=r["source"],
        item_id=r["item_id"],
        url=r.get("url", ""),
        title=r.get("title", ""),
        published_ts=parse_time_utc(r["published_utc"]) if r.get("published_utc") else None,
        fetched_ts=parse_time_utc(r["fetched_utc"]),
        coins=tuple(c for c in (r.get("coins") or "").split(";") if c),
        score=float(score) if score not in ("", None) else None,
        scorer=r.get("scorer", ""),
    )


def load_news(state_dir: str | Path) -> list[NewsItem]:
    """Stored items sorted by known-from time; malformed rows are skipped with a warning."""
    out = []
    for r in _read_csv(sentiment_dir(state_dir) / NEWS_CSV):
        try:
            out.append(_item_from_row(r))
        except (KeyError, ValueError) as exc:
            log.warning("skipping malformed news.csv row: %s", exc)
    return sorted(out, key=lambda i: (i.fetched_ts, i.key))


def merge_items(
    existing: Sequence[NewsItem], new: Iterable[NewsItem]
) -> tuple[list[NewsItem], list[NewsItem]]:
    """Dedupe by key and by URL; the FIRST sighting (earliest fetch) wins. -> (all, added)."""
    by_key = {i.key: i for i in existing}
    urls = {i.url for i in existing if i.url}
    added = []
    for item in sorted(new, key=lambda i: i.fetched_ts):
        if item.key in by_key or (item.url and item.url in urls):
            continue
        by_key[item.key] = item
        if item.url:
            urls.add(item.url)
        added.append(item)
    return sorted(by_key.values(), key=lambda i: (i.fetched_ts, i.key)), added


def save_news(state_dir: str | Path, items: Sequence[NewsItem]) -> Path:
    path = sentiment_dir(state_dir) / NEWS_CSV
    rows = [
        (
            i.key,
            i.source,
            i.item_id,
            i.url,
            i.title,
            format_time_utc(i.published_ts) if i.published_ts is not None else "",
            format_time_utc(i.fetched_ts),
            ";".join(i.coins),
            "" if i.score is None else f"{i.score:.4f}",
            i.scorer,
        )
        for i in items
    ]
    _write_csv(path, NEWS_HEADER, rows)
    return path


def aggregate_4h(items: Iterable[NewsItem]) -> dict[str, list[tuple[int, float, int]]]:
    """Per coin (+ ``ALL``) per 4H bucket of KNOWN-FROM time: ``(bucket_ts, mean, n)``.

    An item belongs only to the bucket containing its ``fetched_ts``; the bucket's value is
    usable from ``bucket_ts + 4h`` on. Unscored items are ignored.
    """
    acc: dict[str, dict[int, list[float]]] = {}
    for it in items:
        if it.score is None:
            continue
        b = it.known_from_ts // BUCKET_MS * BUCKET_MS
        for coin in (*it.coins, ALL):
            acc.setdefault(coin, {}).setdefault(b, []).append(it.score)
    return {
        coin: [(b, round(statistics.fmean(v), 4), len(v)) for b, v in sorted(by_b.items())]
        for coin, by_b in sorted(acc.items())
    }


def save_aggregate(
    state_dir: str | Path, agg: Mapping[str, Sequence[tuple[int, float, int]]]
) -> Path:
    path = sentiment_dir(state_dir) / AGG_CSV
    rows = [(coin, format_time_utc(b), f"{m:.4f}", n) for coin, s in agg.items() for b, m, n in s]
    _write_csv(path, AGG_HEADER, rows)
    return path


def load_fear_greed(state_dir: str | Path) -> list[tuple[int, int, str]]:
    out = []
    for r in _read_csv(sentiment_dir(state_dir) / FG_CSV):
        try:
            out.append(
                (parse_time_utc(r["date_utc"]), int(r["value"]), r.get("classification", ""))
            )
        except (KeyError, ValueError) as exc:
            log.warning("skipping malformed fear_greed.csv row: %s", exc)
    return sorted(out)


def save_fear_greed(state_dir: str | Path, rows: Iterable[tuple[int, int, str]]) -> Path:
    """Merge with the stored history; a date already stored keeps its FIRST recorded value."""
    merged = {d: (d, v, c) for d, v, c in rows}
    merged.update({d: (d, v, c) for d, v, c in load_fear_greed(state_dir)})
    path = sentiment_dir(state_dir) / FG_CSV
    out = [
        (format_time_utc(d), v, c, format_time_utc(fg_known_from(d)))
        for d, v, c in (merged[k] for k in sorted(merged))
    ]
    _write_csv(path, FG_HEADER, out)
    return path


def load_sources(state_dir: str | Path) -> dict[str, Any]:
    """``{"fear_greed": [(known_from_ts, value)], "news_sentiment": {coin: [(bucket_ts,
    mean, n)]}}`` from ``state_dir/sentiment/`` (empty when nothing was collected)."""
    fg = [(fg_known_from(d), v) for d, v, _ in load_fear_greed(state_dir)]
    news: dict[str, list[tuple[int, float, int]]] = {}
    for r in _read_csv(sentiment_dir(state_dir) / AGG_CSV):
        try:
            row = (parse_time_utc(r["bucket_utc"]), float(r["mean_score"]), int(r["n"]))
        except (KeyError, ValueError) as exc:
            log.warning("skipping malformed sentiment_4h.csv row: %s", exc)
            continue
        news.setdefault(r["coin"], []).append(row)
    return {"fear_greed": fg, "news_sentiment": {c: sorted(v) for c, v in news.items()}}


# ---------------------------------------------------------------------- auto R5 events
_IMPACT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (kind, re.compile(rx, re.IGNORECASE))
    for kind, rx in (
        ("other", r"\b(?:hack(?:s|ed|er|ers)?|exploit(?:s|ed)?|drained)\b"),
        ("regulatory", r"(?-i:\bSEC\b).{0,60}?\b(?:sues|sued|suing|charges|charged)\b"),
        ("legal", r"\blawsuits?\b"),
        ("other", r"\bdelist(?:s|ed|ing|ings)?\b"),
        (
            "other",
            r"\bwithdrawals?\b.{0,40}?\b(?:halt(?:s|ed)?|suspend(?:s|ed)?|paus(?:es|ed)|frozen)\b"
            r"|\b(?:halt(?:s|ed)?|suspend(?:s|ed)?|paus(?:es|ed)|freez(?:es)|froze)\b"
            r".{0,40}?\bwithdrawals?\b",
        ),
        ("regulatory", r"\bban(?:s|ned)?\b"),
        ("legal", r"\b(?:insolven(?:t|cy)|bankrupt(?:cy)?)\b"),
    )
)
_BINANCE_RE = re.compile(r"\bbinance\b", re.IGNORECASE)
AUTO_NOTE_PREFIX = "auto:"


def classify_headline(title: str) -> str | None:
    """The event kind of the first high-impact keyword rule matching ``title``, or None."""
    for kind, rx in _IMPACT_RULES:
        if rx.search(title):
            return kind
    return None


def _note(item: NewsItem) -> str:
    text = " ".join(f"{AUTO_NOTE_PREFIX}{item.source}: {item.title}".split())
    return text[:200]


def auto_events(items: Iterable[NewsItem]) -> list[NewsEvent]:
    """High-impact headlines -> unscheduled high-impact R5 events (never scheduled kinds).

    Scope is ``EXCHANGE:binance`` when Binance is named, else each tagged coin; untagged
    headlines are skipped (a generic headline should not black out every pair).
    ``ts = known_from_ts = fetched_ts``: the blackout starts when the bot saw the headline.
    """
    out: dict[tuple[str, str, str], NewsEvent] = {}
    for it in sorted(items, key=lambda i: i.fetched_ts):
        kind = classify_headline(it.title)
        if kind is None:
            continue
        scopes = ["EXCHANGE:binance"] if _BINANCE_RE.search(it.title) else list(it.coins)
        note = _note(it)
        for scope in scopes:
            ev = NewsEvent(it.fetched_ts, scope, "high", kind, note, it.fetched_ts)
            out.setdefault((scope, kind, note), ev)
    return sorted(out.values(), key=lambda e: (e.ts, e.scope))


def _read_header(path: Path) -> tuple[str, ...] | None:
    with path.open(newline="", encoding="utf-8-sig") as fh:
        row = next(csv.reader(fh), None)
    return tuple(h.strip().lower() for h in row) if row else None


def append_events(events_csv: str | Path, events: Iterable[NewsEvent]) -> list[NewsEvent]:
    """Append ``events`` that are not already in ``events_csv`` (news.py format); never
    rewrites or removes a row. Returns the events actually appended.

    Duplicates are matched on ``(scope, kind, note)`` or ``(ts, scope, kind)``. A file with
    the 5-column header gets 5-column rows: for unscheduled kinds with ``ts ==
    known_from_ts`` (what :func:`auto_events` makes) the empty known-from default is
    identical. A missing file is created with the 6-column header. A malformed existing
    file raises ``ValueError`` (from ``news.load_events``) and nothing is written.
    """
    path = Path(events_csv)
    exists = path.exists() and path.stat().st_size > 0
    existing = load_events(path) if exists else []
    seen = {(e.scope, e.kind, e.note) for e in existing} | {
        (e.ts, e.scope, e.kind) for e in existing
    }
    header = _read_header(path) if exists else CSV_HEADER_WITH_KNOWN_FROM
    six = header == CSV_HEADER_WITH_KNOWN_FROM
    new = []
    for ev in events:
        keys = ((ev.scope, ev.kind, ev.note), (ev.ts, ev.scope, ev.kind))
        if any(k in seen for k in keys):
            continue
        if not six and ev.known_from_ts not in (None, ev.ts):
            log.warning("events CSV has no known_from_utc column; skipping %s", ev.note)
            continue
        seen.update(keys)
        new.append(ev)
    if not new:
        return []
    needs_nl = exists and not path.read_bytes().endswith(b"\n")
    with path.open("a", newline="", encoding="utf-8") as fh:
        if needs_nl:
            fh.write("\n")
        w = csv.writer(fh, lineterminator="\n")
        if not exists:
            w.writerow(CSV_HEADER_WITH_KNOWN_FROM if six else CSV_HEADER)
        for ev in new:
            row = [format_time_utc(ev.ts), ev.scope, ev.impact, ev.kind, ev.note]
            if six:
                row.append("" if ev.known_from_ts is None else format_time_utc(ev.known_from_ts))
            w.writerow(row)
    return new


# ---------------------------------------------------------------------- collect
def _redact(text: str, secret: str | None) -> str:
    return text.replace(secret, "***") if secret else text


SOURCE_NAMES = ("fear_greed", "rss", "reddit", "cryptopanic")


def _enabled(settings: Mapping[str, Any]) -> set[str]:
    return set(settings.get("enabled_sources", SOURCE_NAMES))


def _feeds(settings: Mapping[str, Any]) -> dict[str, str]:
    feeds = settings.get("feeds", DEFAULT_FEEDS)
    if isinstance(feeds, Mapping):
        return {str(k): str(v) for k, v in feeds.items()}
    return {re.sub(r"\W+", "_", str(u).split("//")[-1]).strip("_")[:40]: str(u) for u in feeds}


def _jobs(
    settings: Mapping[str, Any], fetch: Fetch, now: int
) -> list[tuple[str, Callable[[], list[NewsItem]], str | None]]:
    """(source name, fetch+parse thunk, secret to redact) for every enabled headline source."""
    enabled = _enabled(settings)
    jobs: list[tuple[str, Callable[[], list[NewsItem]], str | None]] = []
    if "rss" in enabled:
        for name, url in _feeds(settings).items():
            jobs.append(
                (
                    f"rss:{name}",
                    lambda n=name, u=url: parse_feed(_get(fetch, u), f"rss:{n}", now),
                    None,
                )
            )
    if "reddit" in enabled:
        for sub in settings.get("subreddits", DEFAULT_SUBREDDITS):
            url = REDDIT_URL.format(sub=sub)
            jobs.append(
                (f"reddit:{sub}", lambda s=sub, u=url: parse_reddit(_get(fetch, u), s, now), None)
            )
    token = settings.get("cryptopanic_token") or os.environ.get(CRYPTOPANIC_ENV)
    if "cryptopanic" in enabled and token:
        url = str(settings.get("cryptopanic_url", CRYPTOPANIC_URL)).format(token=token)
        jobs.append(("cryptopanic", lambda: parse_cryptopanic(_get(fetch, url), now), token))
    return jobs


def _run_fear_greed(
    state_dir: Path, settings: Mapping[str, Any], fetch: Fetch, summary: dict[str, Any]
) -> None:
    if "fear_greed" not in _enabled(settings):
        return
    try:
        rows = parse_fear_greed(_get(fetch, str(settings.get("fear_greed_url", FEAR_GREED_URL))))
        save_fear_greed(state_dir, rows)
        summary["sources"]["fear_greed"] = {"ok": True, "items": len(rows)}
        summary["fear_greed_points"] = len(rows)
    except Exception as exc:
        _fail(summary, "fear_greed", exc)


def _fail(
    summary: dict[str, Any], name: str, exc: BaseException, secret: str | None = None
) -> None:
    msg = _redact(f"{type(exc).__name__}: {exc}", secret)
    log.error("sentiment source %s failed: %s", name, msg)
    summary["sources"][name] = {"ok": False, "error": msg}
    summary["errors"].append(f"{name}: {msg}")


def _fetch_headlines(
    settings: Mapping[str, Any], fetch: Fetch, now: int, summary: dict[str, Any]
) -> list[NewsItem]:
    tagger = CoinTagger(settings.get("coins"))
    items: list[NewsItem] = []
    for name, job, secret in _jobs(settings, fetch, now):
        try:
            got = job()
        except Exception as exc:
            _fail(summary, name, exc, secret)
            continue
        summary["sources"][name] = {"ok": True, "items": len(got)}
        items += [
            replace(i, coins=tuple(sorted(set(i.coins) | set(tagger.tag(i.title))))) for i in got
        ]
    if "cryptopanic" not in summary["sources"]:
        summary["sources"]["cryptopanic"] = {
            "ok": True,
            "skipped": True,
            "reason": f"no token ({CRYPTOPANIC_ENV} not set)",
        }
    return items


def _append_auto_events(
    settings: Mapping[str, Any], added: Sequence[NewsItem], summary: dict[str, Any]
) -> None:
    events_path = settings.get("events")
    evs = auto_events(added)
    summary["auto_events_found"] = len(evs)
    if not events_path or not evs:
        return
    try:
        summary["auto_events_added"] = len(append_events(events_path, evs))
    except Exception as exc:
        _fail(summary, "events_csv", exc)


def collect(
    state_dir: str | Path,
    settings: Mapping[str, Any] | None = None,
    fetch: Fetch | None = None,
    *,
    now_ms: int | None = None,
) -> dict[str, Any]:
    """Fetch every source, score, store and append auto events; never raises for a source.

    ``settings`` keys (all optional): ``feeds`` ({name: url} or [url]), ``subreddits``,
    ``coins`` ({ticker: [keywords]}), ``scorer`` (auto|finbert|lexicon), ``cryptopanic_token``
    (else env ``CRYPTOPANIC_TOKEN``), ``cryptopanic_url``, ``fear_greed_url``,
    ``enabled_sources`` (subset of fear_greed, rss, reddit, cryptopanic), ``events`` (path
    of the bot's events CSV to append auto R5 events to).
    """
    settings = dict(settings or {})
    fetch = fetch or http_fetch
    sd = Path(state_dir)
    now = int(time.time() * 1000) if now_ms is None else int(now_ms)
    summary: dict[str, Any] = {
        "collected_utc": format_time_utc(now),
        "sources": {},
        "errors": [],
        "fear_greed_points": 0,
        "auto_events_found": 0,
        "auto_events_added": 0,
    }
    _run_fear_greed(sd, settings, fetch, summary)
    items = _fetch_headlines(settings, fetch, now, summary)
    existing = load_news(sd)
    _, fresh = merge_items(existing, items)
    try:
        scorer: LexiconScorer | FinBertScorer = make_scorer(settings.get("scorer", "auto"))
    except (RuntimeError, ValueError) as exc:
        _fail(summary, "scorer", exc)
        scorer = LexiconScorer()
    scored, summary["scorer"] = score_items(fresh, scorer)
    all_items, added = merge_items(existing, scored)
    save_news(sd, all_items)
    save_aggregate(sd, aggregate_4h(all_items))
    _append_auto_events(settings, added, summary)
    summary.update(
        items_fetched=len(items),
        items_new=len(added),
        items_stored=len(all_items),
        ok=not summary["errors"],
        dir=str(sentiment_dir(sd)),
    )
    return summary


# ---------------------------------------------------------------------- layers
def _choose_rule(
    samples: Sequence[tuple[float | None, float]],
    rules: Sequence[str],
    vetoes: Callable[[str, float | None], bool],
    min_kept: int,
) -> tuple[str, dict[str, dict[str, float]]]:
    """Pre-registered choice: best train avg R of the KEPT trades; ties / thin -> rules[0]."""
    table: dict[str, dict[str, float]] = {}
    best, best_avg = rules[0], -math.inf
    for rule in rules:
        kept = [r for v, r in samples if not vetoes(rule, v)]
        avg = statistics.fmean(kept) if kept else -math.inf
        table[rule] = {"kept": len(kept), "avg_r": round(avg, 4) if kept else float("nan")}
        if rule != rules[0] and len(kept) < min_kept:
            continue
        if avg > best_avg + 1e-12:
            best, best_avg = rule, avg
    return best, table


def _file_sig(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


class _SentimentLayer(Layer):
    """Shared plumbing: source series from ctx.sources / state_dir, rule persistence."""

    kind = "sentiment"
    source_key = ""
    source_file = ""
    rules: tuple[str, ...] = ("none",)

    def __init__(self, **params: Any) -> None:
        super().__init__(**params)
        self.rule = "none"
        self.fit_table: dict[str, Any] = {}
        self._data: Any = None
        self._mtime: tuple[int, int] | None = None

    def _from_sources(self, sources: Mapping[str, Any], state_dir: Path | None) -> Any:
        if self.source_key in sources:
            return sources[self.source_key]
        if state_dir is not None:
            return load_sources(state_dir).get(self.source_key)
        return None

    def _series(self) -> Any:
        """Live: reload from ``state_dir`` whenever the stored file changed."""
        sd = self.params.get("state_dir")
        if sd:
            p = sentiment_dir(sd) / self.source_file
            mtime = _file_sig(p)
            if mtime != self._mtime:
                self._mtime = mtime
                self._set_data(load_sources(sd).get(self.source_key))
        return self._data

    def _set_data(self, data: Any) -> None:
        self._data = data

    def _bind(self, ctx: TrainContext) -> None:
        self._set_data(self._from_sources(ctx.sources, ctx.state_dir))
        if ctx.state_dir is not None:
            self.params["state_dir"] = str(ctx.state_dir)
            p = sentiment_dir(ctx.state_dir) / self.source_file
            self._mtime = _file_sig(p)

    def state(self) -> dict[str, Any]:
        return {"params": self.params, "rule": self.rule, "fit": self.fit_table}

    def load_state(self, d: Mapping[str, Any]) -> None:
        super().load_state(d)
        rule = d.get("rule", "none")
        self.rule = rule if rule in self.rules else "none"
        self.fit_table = dict(d.get("fit", {}))
        self._mtime = (-1, -1)  # force a reload from state_dir on first use

    def _decision_ts(self, ctx: TrainContext, signal_ts: int) -> int:
        return signal_ts + ctx.cfg.timeframe_ms


class _FearGreedSeries:
    def __init__(self, rows: Iterable[Sequence[Any]] | None) -> None:
        pts = sorted((int(r[0]), float(r[1])) for r in (rows or ()))
        self.ts = [t for t, _ in pts]
        self.vals = [v for _, v in pts]

    def at(self, t: int, max_age_ms: float) -> float | None:
        """The latest value with ``known_from <= t`` (None if none or older than max_age)."""
        i = bisect.bisect_right(self.ts, t) - 1
        if i < 0 or t - self.ts[i] > max_age_ms:
            return None
        return self.vals[i]


FG_RULES: dict[str, tuple[str, float] | None] = {
    "none": None,
    "veto_le_25": ("le", 25.0),
    "veto_ge_75": ("ge", 75.0),
    "veto_le_40": ("le", 40.0),
}


def _fg_vetoes(rule: str, value: float | None) -> bool:
    spec = FG_RULES.get(rule)
    if spec is None or value is None:
        return False
    op, thr = spec
    return value <= thr if op == "le" else value >= thr


@register
class FearGreedLayer(_SentimentLayer):
    """Veto entries in an extreme-sentiment regime chosen on the train window only."""

    name = "fear_greed"
    description = (
        "Fear & Greed index veto: one pre-registered rule (none, <=25, >=75, <=40) picked on "
        "the train window, applied with the value known at the decision time"
    )
    source_key = "fear_greed"
    source_file = FG_CSV
    rules = tuple(FG_RULES)

    def _set_data(self, data: Any) -> None:
        self._data = _FearGreedSeries(data)

    def _max_age(self) -> float:
        return float(self.params.get("max_age_days", 3)) * DAY_MS

    def _samples(self, ctx: TrainContext) -> list[tuple[float | None, float]]:
        series = _FearGreedSeries(self._from_sources(ctx.sources, ctx.state_dir))
        return [
            (series.at(self._decision_ts(ctx, ts), self._max_age()), r)
            for _, ts, _, r in ctx.examples()
        ]

    def ready(self, ctx: TrainContext) -> tuple[bool, str]:
        samples = self._samples(ctx)
        if not samples:
            return False, "no train-window signals to evaluate Fear & Greed on"
        cov = sum(v is not None for v, _ in samples) / len(samples)
        need = float(self.params.get("min_coverage", 0.8))
        return (
            cov >= need,
            f"Fear & Greed history covers {cov:.0%} of {len(samples)} train signals "
            f"(needs {need:.0%})",
        )

    def fit(self, ctx: TrainContext) -> None:
        self._bind(ctx)
        min_kept = int(self.params.get("min_kept", 10))
        self.rule, table = _choose_rule(self._samples(ctx), self.rules, _fg_vetoes, min_kept)
        self.fit_table = {"train_rules": table, "chosen": self.rule}

    def veto(self, pair: str, row: FeatureRow, view: MarketView) -> tuple[bool, str]:
        series = self._series()
        value = series.at(row.close_ts, self._max_age()) if series is not None else None
        if value is None:
            return False, "no Fear & Greed value known at the decision time"
        blocked = _fg_vetoes(self.rule, value)
        verb = "vetoes" if blocked else "allows"
        return (
            blocked,
            f"Fear & Greed {value:g} known at the decision time; rule {self.rule} {verb}",
        )


NS_RULES: dict[str, float | None] = {"none": None, "lt_-0.2": -0.2, "lt_-0.4": -0.4}


def _ns_vetoes(rule: str, value: float | None) -> bool:
    thr = NS_RULES.get(rule)
    return thr is not None and value is not None and value < thr


class _NewsSeries:
    def __init__(self, data: Mapping[str, Iterable[Sequence[Any]]] | None) -> None:
        self.by_coin: dict[str, tuple[list[int], list[float], list[int]]] = {}
        for coin, rows in (data or {}).items():
            pts = sorted((int(r[0]), float(r[1]), int(r[2])) for r in rows)
            self.by_coin[coin] = ([p[0] for p in pts], [p[1] for p in pts], [p[2] for p in pts])

    def trailing(self, coin: str, t: int, window_ms: int, min_items: int) -> float | None:
        """n-weighted mean of the buckets that CLOSED in ``(t - window, t]``."""
        s = self.by_coin.get(coin)
        if not s:
            return None
        bts, means, ns = s
        lo = bisect.bisect_left(bts, t - window_ms)
        hi = bisect.bisect_right(bts, t - BUCKET_MS)
        n = sum(ns[lo:hi])
        if n < max(1, min_items):
            return None
        return sum(m * k for m, k in zip(means[lo:hi], ns[lo:hi], strict=True)) / n

    def days(self, lo_ts: int, hi_ts: int) -> int:
        """Distinct UTC days with collected headlines whose bucket closed inside [lo, hi]."""
        return len(
            {
                b // DAY_MS
                for bts, _, _ in self.by_coin.values()
                for b in bts
                if lo_ts <= b and b + BUCKET_MS <= hi_ts
            }
        )


def _train_start(ctx: TrainContext) -> int:
    starts = [h[0].ts for p in ctx.view.pairs if (h := ctx.view.history(p, ctx.until_ts))]
    return min(starts) if starts else ctx.until_ts


@register
class NewsSentimentLayer(_SentimentLayer):
    """Veto when a coin's trailing-24h headline sentiment is below a train-chosen threshold."""

    name = "news_sentiment"
    description = (
        "Headline sentiment veto: trailing-24h mean score per coin below a pre-registered "
        "threshold (none, -0.2, -0.4) picked on the train window; needs collected history"
    )
    source_key = "news_sentiment"
    source_file = AGG_CSV
    rules = tuple(NS_RULES)

    def _set_data(self, data: Any) -> None:
        self._data = _NewsSeries(data)

    def _mean(self, series: _NewsSeries, pair: str, t: int) -> float | None:
        window = int(float(self.params.get("window_hours", 24)) * HOUR_MS)
        return series.trailing(base_of(pair), t, window, int(self.params.get("min_items", 3)))

    def ready(self, ctx: TrainContext) -> tuple[bool, str]:
        series = _NewsSeries(self._from_sources(ctx.sources, ctx.state_dir))
        days = series.days(_train_start(ctx), ctx.until_ts)
        need = int(self.params.get("min_days", 30))
        if days >= need:
            return True, f"{days} days of collected headlines inside the train window"
        return False, (
            f"only {days} days of collected headlines inside the train window (needs {need}); "
            "headline sentiment cannot be backtested on history that was never collected, so "
            "this layer keeps collecting until enough live history exists"
        )

    def fit(self, ctx: TrainContext) -> None:
        self._bind(ctx)
        series = _NewsSeries(self._from_sources(ctx.sources, ctx.state_dir))
        samples = [
            (self._mean(series, pair, self._decision_ts(ctx, ts)), r)
            for pair, ts, _, r in ctx.examples()
        ]
        min_kept = int(self.params.get("min_kept", 10))
        self.rule, table = _choose_rule(samples, self.rules, _ns_vetoes, min_kept)
        self.fit_table = {"train_rules": table, "chosen": self.rule}

    def veto(self, pair: str, row: FeatureRow, view: MarketView) -> tuple[bool, str]:
        series = self._series()
        mean = self._mean(series, pair, row.close_ts) if series is not None else None
        if mean is None:
            return False, f"not enough {base_of(pair)} headlines in the last 24h"
        blocked = _ns_vetoes(self.rule, mean)
        verb = "vetoes" if blocked else "allows"
        return blocked, (
            f"{base_of(pair)} trailing-24h headline sentiment {mean:+.2f}; rule {self.rule} {verb}"
        )


__all__ = [
    "DEFAULT_COINS",
    "DEFAULT_FEEDS",
    "CoinTagger",
    "FearGreedLayer",
    "FinBertScorer",
    "LexiconScorer",
    "NewsItem",
    "NewsSentimentLayer",
    "aggregate_4h",
    "append_events",
    "auto_events",
    "collect",
    "http_fetch",
    "load_news",
    "load_sources",
    "make_scorer",
    "merge_items",
    "parse_cryptopanic",
    "parse_fear_greed",
    "parse_feed",
    "parse_reddit",
]
