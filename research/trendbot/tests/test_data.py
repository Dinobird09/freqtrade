"""data.py: candle CSV round-trip, strict validation with line numbers, gaps, resampling."""

from __future__ import annotations

from pathlib import Path

import pytest

from research.trendbot.data import (
    CSV_COLUMNS,
    gap_report,
    load_candles_csv,
    load_dataset,
    pair_filename,
    parse_ts,
    resample_candles,
    save_candles_csv,
    timeframe_to_ms,
    ts_to_iso,
    validate_candles,
)
from research.trendbot.models import HOUR_MS, Candle


TF = 4 * HOUR_MS
T0 = 1_704_067_200_000  # 2024-01-01T00:00:00Z
HEADER = ",".join(CSV_COLUMNS)


def make(n: int, start: int = T0, step: int = TF) -> list[Candle]:
    out, price = [], 100.0
    for i in range(n):
        close = price * (1.01 if i % 2 else 0.995)
        high, low = max(price, close) * 1.002, min(price, close) * 0.997
        out.append(Candle(start + i * step, price, high, low, close, 1000.0 + i / 3))
        price = close
    return out


def write(tmp_path: Path, *lines: str) -> Path:
    p = tmp_path / "c.csv"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def test_round_trip_is_exact(tmp_path):
    candles = make(50)
    path = tmp_path / "sub" / "BTC_USDT-4h.csv"
    save_candles_csv(candles, path)
    assert path.read_text().splitlines()[0] == HEADER
    assert path.read_text().splitlines()[1].split(",")[0] == str(T0)  # ms, not ISO
    assert load_candles_csv(path) == candles  # floats round-trip bit-for-bit
    assert not list(path.parent.glob("*.tmp"))


def test_iso_and_ms_timestamps(tmp_path):
    p = write(
        tmp_path,
        HEADER,
        "2024-01-01T00:00:00Z,1,2,0.5,1.5,10",
        f"{T0 + TF},1.5,2,1,1.2,0",
        "2024-01-01T10:00:00+02:00,1.2,1.3,1.1,1.25,3",  # = 08:00Z
        "",  # blank lines are tolerated
    )
    candles = load_candles_csv(p)
    assert [c.ts for c in candles] == [T0, T0 + TF, T0 + 2 * TF]
    assert candles[1].volume == 0.0
    assert parse_ts(" 2024-01-01T00:00:00.000Z ") == T0
    assert ts_to_iso(T0 + TF) == "2024-01-01T04:00:00Z"


@pytest.mark.parametrize(
    ("row", "line", "message"),
    [
        (f"{T0},1,2,0.5,1.5,10", 3, "duplicate ts"),
        (f"{T0 - TF},1,2,0.5,1.5,10", 3, "not after the previous row"),
        (f"{T0 + TF},1,1.4,0.5,1.5,10", 3, "high"),
        (f"{T0 + TF},1,2,1.2,1.5,10", 3, "low"),
        (f"{T0 + TF},1,2,0.5,1.5,-1", 3, "negative volume"),
        (f"{T0 + TF},1,2,0.5,abc,10", 3, "close 'abc' is not a number"),
        (f"{T0 + TF},1,2,0.5,nan,10", 3, "non-finite"),
        (f"{T0 + TF},0,2,0,1.5,10", 3, "> 0"),
        (f"{T0 + TF},1,2,0.5,1.5", 3, "expected 6 fields"),
        ("2024-01-01T04:00:00,1,2,0.5,1.5,10", 3, "no UTC offset"),
        ("yesterday,1,2,0.5,1.5,10", 3, "neither integer milliseconds nor ISO-8601"),
        (f"{T0 + TF}.0,1,2,0.5,1.5,10", 3, "neither integer"),
        (f"{T0 + TF}.0+00:00,1,2,0.5,1.5,10", 3, "neither integer"),  # not the year 1704
        ("20240101T040000Z,1,2,0.5,1.5,10", 3, "neither integer"),  # basic format rejected
    ],
)
def test_invalid_rows_raise_with_line_number(tmp_path, row, line, message):
    p = write(tmp_path, HEADER, f"{T0},1,2,0.5,1.5,10", row)
    with pytest.raises(ValueError, match=f"line {line}: .*{message}"):
        load_candles_csv(p)


def test_line_number_counts_blank_lines(tmp_path):
    p = write(tmp_path, HEADER, f"{T0},1,2,0.5,1.5,10", "", f"{T0},1,2,0.5,1.5,10")
    with pytest.raises(ValueError, match="line 4: duplicate"):
        load_candles_csv(p)


def test_bad_or_missing_header(tmp_path):
    with pytest.raises(ValueError, match="line 1: header"):
        load_candles_csv(write(tmp_path, "time,open,high,low,close,volume", f"{T0},1,2,0.5,1,1"))
    empty = tmp_path / "empty.csv"
    empty.write_text("")
    with pytest.raises(ValueError, match="line 1: header"):
        load_candles_csv(empty)
    # Header only (with a BOM, as some spreadsheet tools write it) is a valid, empty file.
    bom = tmp_path / "bom.csv"
    bom.write_text("﻿" + HEADER + "\n", encoding="utf-8")
    assert load_candles_csv(bom) == []


def test_load_dataset(tmp_path):
    assert pair_filename("BTC/USDT") == "BTC_USDT-4h.csv"
    assert pair_filename("BTC/USDT:USDT", "1h") == "BTC_USDT_USDT-1h.csv"
    data = {"BTC/USDT": make(30), "BNB/USDT": make(20, start=T0 + TF)}
    for pair, candles in data.items():
        save_candles_csv(candles, tmp_path / pair_filename(pair))
    loaded = load_dataset(tmp_path, ["BTC/USDT", "BNB/USDT"])
    assert list(loaded) == ["BTC/USDT", "BNB/USDT"]
    assert loaded == data
    with pytest.raises(FileNotFoundError, match=r"ETH_USDT-4h\.csv"):
        load_dataset(tmp_path, ["ETH/USDT"])


def test_load_dataset_rejects_wrong_timeframe_and_empty_files(tmp_path):
    save_candles_csv(make(10, step=HOUR_MS), tmp_path / "BTC_USDT-4h.csv")  # 1h data, 4h name
    with pytest.raises(ValueError, match="not a whole number of 4h apart"):
        load_dataset(tmp_path, ["BTC/USDT"])
    save_candles_csv([], tmp_path / "ETH_USDT-4h.csv")
    with pytest.raises(ValueError, match="no candles"):
        load_dataset(tmp_path, ["ETH/USDT"])


def test_gap_report():
    candles = make(10)
    holey = candles[:3] + candles[5:8] + candles[9:]
    assert gap_report(candles, TF) == []
    assert gap_report(holey, TF) == [(candles[2].ts, candles[5].ts), (candles[7].ts, candles[9].ts)]
    assert gap_report([], TF) == []
    with pytest.raises(ValueError):
        gap_report(candles, 0)


def test_validate_candles():
    candles = make(5)
    validate_candles(candles)
    with pytest.raises(ValueError, match=r"candle #3 .*duplicate"):
        validate_candles([*candles[:3], candles[2], candles[4]])
    bad = Candle(T0, 10.0, 9.0, 8.0, 9.5, 1.0)  # high below open
    with pytest.raises(ValueError, match=r"candle #0 .*high"):
        validate_candles([bad])


def test_timeframe_to_ms():
    assert timeframe_to_ms("4h") == TF
    assert timeframe_to_ms("15m") == 15 * 60_000
    assert timeframe_to_ms("1d") == 24 * HOUR_MS
    assert timeframe_to_ms("1w") == 7 * 24 * HOUR_MS
    for bad in ("", "h", "0h", "4x", "-4h", "4.5h"):
        with pytest.raises(ValueError):
            timeframe_to_ms(bad)


def test_resample_keeps_only_complete_buckets():
    hourly = make(12, step=HOUR_MS)  # three complete 4h buckets
    del hourly[5]  # break the second bucket
    out = resample_candles(hourly, HOUR_MS, TF)
    assert [c.ts for c in out] == [T0, T0 + 2 * TF]
    first = out[0]
    src = hourly[:4]
    assert first.open == src[0].open and first.close == src[-1].close
    assert first.high == max(c.high for c in src) and first.low == min(c.low for c in src)
    assert first.volume == pytest.approx(sum(c.volume for c in src))
    with pytest.raises(ValueError):
        resample_candles(hourly, 3 * HOUR_MS, TF)
