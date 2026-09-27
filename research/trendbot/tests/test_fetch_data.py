"""fetch_data against a FAKE exchange (no ccxt, no network), incl. the D2 manifest."""

from __future__ import annotations

import json
import re
import sys
import types

import pytest

from research.trendbot import fetch_data
from research.trendbot.data import (
    MANIFEST_FORMAT,
    MANIFEST_NAME,
    file_sha256,
    load_candles_csv,
    load_dataset,
    save_candles_csv,
    verify_manifest,
)
from research.trendbot.fetch_data import fetch_ohlcv, parse_utc_date, plan_timeframe
from research.trendbot.models import HOUR_MS


TF = 4 * HOUR_MS
T0 = 1_546_300_800_000  # 2019-01-01T00:00:00Z


def rows(n: int, start: int = T0, step: int = TF) -> list[list[float]]:
    out = []
    for i in range(n):
        o = 100.0 + i
        out.append([start + i * step, o, o + 2.0, o - 1.0, o + 1.0, 10.0 + i])
    return out


class FakeExchange:
    """ccxt-compatible surface: fetch_ohlcv(symbol, timeframe, since, limit), milliseconds()."""

    id = "fake"

    def __init__(
        self,
        data: list[list[float]],
        now: int,
        overlap: int = 0,
        ignore_since: bool = False,
        page_cap: int | None = None,
        timeframes: dict[str, str] | None = None,
        markets: dict[str, dict] | None = None,
    ):
        self.data = data
        self.now = now
        self.overlap = overlap  # re-send this many rows before `since` (dedup test)
        self.ignore_since = ignore_since
        self.page_cap = page_cap  # exchange-side cap below the requested limit
        self.timeframes = timeframes or {"4h": "4h"}
        self.markets = markets if markets is not None else {}
        self.calls: list[tuple[str, str, int, int]] = []

    def milliseconds(self) -> int:
        return self.now

    def load_markets(self) -> dict:
        return self.markets

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
        self.calls.append((symbol, timeframe, since, limit))
        visible = [r for r in self.data if r[0] <= self.now]  # the future does not exist yet
        if self.ignore_since:
            return [list(r) for r in visible[:limit]]
        first = next((k for k, r in enumerate(visible) if r[0] >= since), len(visible))
        first = max(0, first - self.overlap) if first < len(visible) else first
        n = min(limit, self.page_cap or limit)
        return [list(r) for r in visible[first : first + n]]


def test_paginates_until_empty_page():
    ex = FakeExchange(rows(2500), now=T0 + 3000 * TF)
    candles = fetch_ohlcv(ex, "BTC/USDT", "4h", T0, limit=1000)
    assert [c.ts for c in candles] == [T0 + i * TF for i in range(2500)]
    assert [call[2] for call in ex.calls] == [T0, T0 + 1000 * TF, T0 + 2000 * TF, T0 + 2500 * TF]
    assert all(call[3] == 1000 for call in ex.calls)
    assert candles[5].open == 105.0 and candles[5].volume == 15.0


def test_short_pages_do_not_stop_pagination():
    ex = FakeExchange(rows(700), now=T0 + 1000 * TF, page_cap=300)  # e.g. Coinbase's 300 cap
    candles = fetch_ohlcv(ex, "BTC/USDT", "4h", T0, limit=1000)
    assert len(candles) == 700
    assert len(ex.calls) == 4  # 300 + 300 + 100 + empty


def test_overlapping_pages_are_deduplicated():
    ex = FakeExchange(rows(250), now=T0 + 1000 * TF, overlap=3)
    candles = fetch_ohlcv(ex, "BTC/USDT", "4h", T0, limit=100)
    ts = [c.ts for c in candles]
    assert ts == sorted(set(ts)) == [T0 + i * TF for i in range(250)]


def test_open_last_candle_is_dropped():
    now = T0 + 99 * TF + TF // 2  # half-way through candle #99
    ex = FakeExchange(rows(100), now=now)
    candles = fetch_ohlcv(ex, "BTC/USDT", "4h", T0)
    assert len(candles) == 99
    assert candles[-1].ts == T0 + 98 * TF
    # A candle whose close time equals `now` exactly is closed and kept.
    ex = FakeExchange(rows(100), now=T0 + 100 * TF)
    assert len(fetch_ohlcv(ex, "BTC/USDT", "4h", T0)) == 100


def test_until_is_exclusive_and_stops_fetching():
    ex = FakeExchange(rows(5000), now=T0 + 6000 * TF)
    until = T0 + 1500 * TF
    candles = fetch_ohlcv(ex, "BTC/USDT", "4h", T0, until_ms=until, limit=1000)
    assert candles[0].ts == T0 and candles[-1].ts == until - TF
    assert len(candles) == 1500
    assert len(ex.calls) == 2  # never asks for data at or beyond `until`


def test_since_filters_and_mid_start():
    ex = FakeExchange(rows(50), now=T0 + 100 * TF, overlap=5)
    since = T0 + 10 * TF
    candles = fetch_ohlcv(ex, "BTC/USDT", "4h", since, limit=20)
    assert candles[0].ts == since and len(candles) == 40


def test_no_progress_terminates():
    ex = FakeExchange(rows(30), now=T0 + 100 * TF, ignore_since=True)
    candles = fetch_ohlcv(ex, "BTC/USDT", "4h", T0, limit=1000)
    assert len(candles) == 30
    assert len(ex.calls) == 2  # second page returns the same rows -> no progress -> stop


def test_empty_exchange_and_bad_rows():
    assert fetch_ohlcv(FakeExchange([], now=T0), "BTC/USDT", "4h", T0) == []
    bad = FakeExchange([[T0, 1.0, None, 1.0, 1.0, 1.0]], now=T0 + TF)
    with pytest.raises(ValueError, match="malformed"):
        fetch_ohlcv(bad, "BTC/USDT", "4h", T0)
    with pytest.raises(ValueError, match="limit"):
        fetch_ohlcv(bad, "BTC/USDT", "4h", T0, limit=0)


def test_plan_timeframe():
    assert plan_timeframe(FakeExchange([], 0), "4h") == "4h"
    coinbase_like = FakeExchange([], 0, timeframes={k: k for k in ("1m", "1h", "2h", "6h", "1d")})
    assert plan_timeframe(coinbase_like, "4h") == "2h"
    with pytest.raises(ValueError, match="neither"):
        plan_timeframe(FakeExchange([], 0, timeframes={"6h": "6h"}), "4h")


def test_parse_utc_date():
    assert parse_utc_date("2019-01-01") == T0
    assert parse_utc_date("2019-01-01T04:00:00Z") == T0 + TF
    assert parse_utc_date("2019-01-01T06:00:00+02:00") == T0 + TF
    for bad in ("1546300800000", "20190101", "Jan 1 2019"):
        with pytest.raises(ValueError, match="not an ISO-8601"):
            parse_utc_date(bad)


# ---------------------------------------------------------------------- CLI
def test_main_without_ccxt_exits_2(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "ccxt", None)  # makes `import ccxt` raise ImportError
    assert fetch_data.main(["--out", "unused"]) == 2
    assert "ccxt is not installed" in capsys.readouterr().err


def test_main_rejects_bad_dates_before_importing_ccxt(capsys):
    assert fetch_data.main(["--since", "not-a-date"]) == 2
    assert "error" in capsys.readouterr().err


FAKE_CCXT_VERSION = "4.9.9-fake"


def _fake_ccxt(monkeypatch, factory) -> None:
    module = types.ModuleType("ccxt")
    module.__version__ = FAKE_CCXT_VERSION
    module.binance = factory
    module.coinbase = factory
    module.kraken = factory
    monkeypatch.setitem(sys.modules, "ccxt", module)


def test_main_end_to_end_with_fake_ccxt(monkeypatch, tmp_path, capsys):
    n = 300
    markets = {"BTC/USDT": {}, "ETH/USDT": {}}  # BNB/USDT deliberately not listed
    data = rows(n)
    del data[100]  # one missing candle -> reported as a gap, not silently filled
    created = []

    def factory(config):
        assert config == {"enableRateLimit": True}
        ex = FakeExchange(data, now=T0 + n * TF, markets=markets)
        ex.id = "kraken"
        created.append(ex)
        return ex

    _fake_ccxt(monkeypatch, factory)
    argv = ["--exchange", "kraken", "--since", "2019-01-01", "--out", str(tmp_path)]
    rc = fetch_data.main([*argv, "--pairs", "BTC/USDT", "ETH/USDT", "BNB/USDT"])
    out = capsys.readouterr()
    assert rc == 1  # BNB/USDT missing on this venue
    assert "BNB/USDT: not listed on kraken (BNB is a Binance pair)" in out.err
    assert "1 gap(s)" in out.out
    loaded = load_dataset(tmp_path, ["BTC/USDT", "ETH/USDT"])
    assert len(loaded["BTC/USDT"]) == n - 1
    assert loaded["BTC/USDT"] == load_candles_csv(tmp_path / "ETH_USDT-4h.csv")
    assert created[0].calls[0][3] == 1000


def test_main_coinbase_aggregates_2h_into_4h(monkeypatch, tmp_path, capsys):
    two_h = 2 * HOUR_MS
    data = rows(40, step=two_h)  # 20 complete 4h buckets

    def factory(config):
        ex = FakeExchange(
            data,
            now=T0 + 40 * two_h,
            timeframes={k: k for k in ("1h", "2h", "6h", "1d")},
            markets={"BTC/USDT": {}},
        )
        ex.id = "coinbase"
        factory.ex = ex
        return ex

    _fake_ccxt(monkeypatch, factory)
    argv = ["--exchange", "coinbase", "--pairs", "BTC/USDT", "--out", str(tmp_path)]
    assert fetch_data.main(argv) == 0
    assert "aggregating 2h" in capsys.readouterr().out
    assert {c[1] for c in factory.ex.calls} == {"2h"}
    assert factory.ex.calls[0][3] == 300  # Coinbase page cap
    candles = load_candles_csv(tmp_path / "BTC_USDT-4h.csv")
    assert len(candles) == 20
    first = candles[0]
    assert (first.ts, first.open, first.close) == (T0, data[0][1], data[1][4])
    assert first.high == max(data[0][2], data[1][2]) and first.volume == data[0][5] + data[1][5]


# ---------------------------------------------------------------------------- D2 manifest
def _kraken(monkeypatch, n: int = 60, data=None):
    markets = {"BTC/USDT": {}, "ETH/USDT": {}}
    data = data if data is not None else rows(n)

    def factory(config):
        ex = FakeExchange(data, now=T0 + n * TF, markets=markets)
        ex.id = "kraken"
        return ex

    _fake_ccxt(monkeypatch, factory)


def test_main_writes_a_manifest_that_verifies_as_real(monkeypatch, tmp_path, capsys):
    _kraken(monkeypatch)
    argv = ["--exchange", "kraken", "--since", "2019-01-01", "--out", str(tmp_path)]
    assert fetch_data.main([*argv, "--pairs", "BTC/USDT", "ETH/USDT"]) == 0
    assert "manifest: 2 file(s) recorded" in capsys.readouterr().out
    manifest = json.loads((tmp_path / MANIFEST_NAME).read_text())
    assert manifest["format"] == MANIFEST_FORMAT
    assert (manifest["exchange_id"], manifest["ccxt_version"], manifest["timeframe"]) == (
        "kraken",
        FAKE_CCXT_VERSION,
        "4h",
    )
    assert (manifest["since_ms"], manifest["since_utc"]) == (T0, "2019-01-01T00:00:00Z")
    assert manifest["until_ms"] is None and manifest["symbols"] == ["BTC/USDT", "ETH/USDT"]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", manifest["fetched_at_utc"])
    by_name = {e["name"]: e for e in manifest["files"]}
    assert sorted(by_name) == ["BTC_USDT-4h.csv", "ETH_USDT-4h.csv"]
    for name, entry in by_name.items():
        candles = load_candles_csv(tmp_path / name, "4h")
        assert entry["sha256"] == file_sha256(tmp_path / name)
        assert (entry["rows"], entry["first_ts"], entry["last_ts"]) == (
            len(candles),
            candles[0].ts,
            candles[-1].ts,
        )
        assert (entry["exchange_id"], entry["fetch_timeframe"], entry["symbol"]) == (
            "kraken",
            "4h",
            name.replace("-4h.csv", "").replace("_", "/"),
        )
    provenance, details = verify_manifest(tmp_path, ["BTC/USDT", "ETH/USDT"])
    assert provenance == "real", details["problems"]
    assert details["ccxt_version"] == FAKE_CCXT_VERSION
    # A hand-written CSV next to them is not in the manifest ...
    save_candles_csv(load_candles_csv(tmp_path / "BTC_USDT-4h.csv"), tmp_path / "BNB_USDT-4h.csv")
    provenance, details = verify_manifest(tmp_path, ["BTC/USDT", "ETH/USDT", "BNB/USDT"])
    assert provenance == "unverified-csv"
    assert details["problems"] == ["BNB_USDT-4h.csv: not listed in manifest.json"]
    # ... and a downloaded file edited afterwards no longer matches its hash.
    eth = tmp_path / "ETH_USDT-4h.csv"
    eth.write_text(eth.read_text().replace(",10.0\n", ",10.5\n", 1))
    provenance, details = verify_manifest(tmp_path, ["BTC/USDT", "ETH/USDT"])
    assert provenance == "unverified-csv"
    assert len(details["problems"]) == 1 and "ETH_USDT-4h.csv: sha256" in details["problems"][0]
    assert load_dataset(tmp_path, ["ETH/USDT"])  # still a valid candle file: only unverified


def test_refetching_one_pair_keeps_the_other_entries(monkeypatch, tmp_path):
    _kraken(monkeypatch)
    base = ["--exchange", "kraken", "--out", str(tmp_path)]
    assert fetch_data.main([*base, "--pairs", "BTC/USDT", "ETH/USDT"]) == 0
    first = json.loads((tmp_path / MANIFEST_NAME).read_text())
    assert fetch_data.main([*base, "--pairs", "ETH/USDT", "--until", "2019-01-05"]) == 0
    second = json.loads((tmp_path / MANIFEST_NAME).read_text())
    assert second["symbols"] == ["ETH/USDT"] and second["until_utc"] == "2019-01-05T00:00:00Z"
    by_name = {e["name"]: e for e in second["files"]}
    assert by_name["BTC_USDT-4h.csv"] == next(
        e for e in first["files"] if e["name"] == "BTC_USDT-4h.csv"
    )
    assert by_name["ETH_USDT-4h.csv"]["rows"] == 24 and by_name["ETH_USDT-4h.csv"]["until_ms"]
    assert verify_manifest(tmp_path, ["BTC/USDT", "ETH/USDT"])[0] == "real"


def test_misaligned_exchange_candles_are_never_saved(monkeypatch, tmp_path, capsys):
    _kraken(monkeypatch, data=rows(60, start=T0 + HOUR_MS))  # a venue with shifted 4h bars
    argv = ["--exchange", "kraken", "--pairs", "BTC/USDT", "--out", str(tmp_path)]
    assert fetch_data.main(argv) == 1
    assert "not epoch-aligned" in capsys.readouterr().err
    assert not (tmp_path / "BTC_USDT-4h.csv").exists()
    assert not (tmp_path / MANIFEST_NAME).exists()
