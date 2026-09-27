"""data.py: candle CSV round-trip, strict validation with line numbers, epoch alignment,
gaps, resampling, and manifest-backed provenance (CONTRACT v4 D2)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from research.trendbot.data import (
    CSV_COLUMNS,
    MANIFEST_FORMAT,
    MANIFEST_NAME,
    alignment_offset,
    file_sha256,
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
    verify_manifest,
)
from research.trendbot.models import DAY_MS, HOUR_MS, Candle


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
    with pytest.raises(ValueError, match=r"lines 2-3: .* 1h apart, not a whole number of 4h"):
        load_dataset(tmp_path, ["BTC/USDT"])
    save_candles_csv([], tmp_path / "ETH_USDT-4h.csv")
    with pytest.raises(ValueError, match="no candles"):
        load_dataset(tmp_path, ["ETH/USDT"])


def test_load_dataset_rejects_a_daily_file_saved_as_4h(tmp_path):
    """Every 1d step IS a whole number of 4h bars, so only the MINIMUM spacing reveals it."""
    daily = make(10, step=24 * HOUR_MS)
    save_candles_csv(daily, tmp_path / "BTC_USDT-4h.csv")
    with pytest.raises(ValueError, match="smallest spacing") as err:
        load_dataset(tmp_path, ["BTC/USDT"])
    msg = str(err.value)
    assert "BTC_USDT-4h.csv" in msg and "is 1d" in msg and "not 4h" in msg
    # The offending rows are named by line number and timestamp (first smallest step).
    assert f"lines 2-3: {T0} (2024-01-01T00:00:00Z) -> {T0 + 24 * HOUR_MS}" in msg
    with pytest.raises(ValueError, match="smallest spacing"):
        load_candles_csv(tmp_path / "BTC_USDT-4h.csv", "4h")
    with pytest.raises(ValueError, match="smallest spacing"):
        load_candles_csv(tmp_path / "BTC_USDT-4h.csv", TF)
    # Without a timeframe the file is just a valid candle file; as a 1d file it loads.
    assert load_candles_csv(tmp_path / "BTC_USDT-4h.csv") == daily
    save_candles_csv(daily, tmp_path / "BTC_USDT-1d.csv")
    assert load_dataset(tmp_path, ["BTC/USDT"], "1d")["BTC/USDT"] == daily


@pytest.mark.parametrize(
    ("keep", "ok"),
    [
        (lambda i: True, True),  # complete 4h file
        (lambda i: i not in (3, 4, 7), True),  # gaps are fine: the smallest step is still 4h
        (lambda i: i % 2 == 0, False),  # every other candle missing: 8h file, not 4h
    ],
    ids=["complete", "with-gaps", "8h"],
)
def test_minimum_spacing_must_equal_the_timeframe(tmp_path, keep, ok):
    candles = [c for i, c in enumerate(make(12)) if keep(i)]
    save_candles_csv(candles, tmp_path / "BTC_USDT-4h.csv")
    if ok:
        assert load_dataset(tmp_path, ["BTC/USDT"])["BTC/USDT"] == candles
        assert load_candles_csv(tmp_path / "BTC_USDT-4h.csv", "4h") == candles
    else:
        with pytest.raises(ValueError, match="smallest spacing between consecutive candles is 8h"):
            load_dataset(tmp_path, ["BTC/USDT"])


def test_the_smallest_step_is_reported_with_its_lines(tmp_path):
    p = write(
        tmp_path,
        HEADER,
        f"{T0},1,2,0.5,1.5,10",
        f"{T0 + 3 * TF},1,2,0.5,1.5,10",
        "",  # blank lines keep the physical line numbers
        f"{T0 + 5 * TF},1,2,0.5,1.5,10",
        f"{T0 + 7 * TF},1,2,0.5,1.5,10",
    )
    with pytest.raises(ValueError, match=r"is 8h \(first at lines 3-5: "):
        load_candles_csv(p, "4h")
    one = write(tmp_path, HEADER, f"{T0},1,2,0.5,1.5,10")
    with pytest.raises(ValueError, match="at least 2 are needed to verify the 4h spacing"):
        load_candles_csv(one, "4h")
    with pytest.raises(ValueError):
        load_candles_csv(one, 0)


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


# ---------------------------------------------------------------------------- D2 alignment
def test_a_shifted_4h_file_is_refused_as_not_epoch_aligned(tmp_path):
    """+1h shifted 4H candles (a local-time export): correctly spaced, but every candle
    opens at 01:00, 05:00, ... so every decision time would be an hour off."""
    shifted = make(10, start=T0 + HOUR_MS)
    path = tmp_path / "BTC_USDT-4h.csv"
    save_candles_csv(shifted, path)
    msg = rf"line 2: ts {T0 + HOUR_MS} \(2024-01-01T01:00:00Z\) is not epoch-aligned.*1h past"
    with pytest.raises(ValueError, match=msg):
        load_dataset(tmp_path, ["BTC/USDT"])
    with pytest.raises(ValueError, match=r"BTC_USDT-4h\.csv: line 2: .*not epoch-aligned"):
        load_candles_csv(path, "4h")
    with pytest.raises(ValueError, match="not epoch-aligned"):
        load_candles_csv(path, TF)
    assert load_candles_csv(path) == shifted  # timeframe unknown: nothing to align against
    # Any uniform shift keeps the 4h spacing, so only the alignment check can refuse it.
    save_candles_csv(make(10, start=T0 + 60_000), path)
    with pytest.raises(ValueError, match=r"line 2: .*not epoch-aligned.*1m past"):
        load_dataset(tmp_path, ["BTC/USDT"])


def test_alignment_rule():
    assert alignment_offset(T0, TF) == 0 and alignment_offset(T0 + HOUR_MS, TF) == HOUR_MS
    assert alignment_offset(T0 + TF - 1, TF) == TF - 1
    # Weekly candles open on Monday 00:00 UTC (the exchange convention; 1970-01-01 was a
    # Thursday). 2024-01-01 is a Monday.
    week = 7 * DAY_MS
    assert alignment_offset(T0, week) == 0 and alignment_offset(0, week) == 3 * DAY_MS
    validate_candles(make(5), TF)
    with pytest.raises(ValueError, match=r"candle #0 .*not epoch-aligned"):
        validate_candles(make(5, start=T0 + HOUR_MS), TF)
    validate_candles(make(5, start=T0 + HOUR_MS))  # no timeframe: alignment not checked


def test_weekly_files_align_to_monday(tmp_path):
    save_candles_csv(make(6, step=7 * DAY_MS), tmp_path / "BTC_USDT-1w.csv")
    assert len(load_dataset(tmp_path, ["BTC/USDT"], "1w")["BTC/USDT"]) == 6
    save_candles_csv(make(6, start=T0 + 3 * DAY_MS, step=7 * DAY_MS), tmp_path / "BTC_USDT-1w.csv")
    with pytest.raises(ValueError, match="not epoch-aligned"):
        load_dataset(tmp_path, ["BTC/USDT"], "1w")  # Thursday-open weeks


# ---------------------------------------------------------------------------- D2 manifest
PAIRS = ["BTC/USDT", "ETH/USDT"]


def _manifest_dir(root: Path) -> Path:
    """Two candle files plus the manifest fetch_data would have written for them."""
    files = []
    for pair, n in zip(PAIRS, (30, 25), strict=True):
        candles = make(n)
        path = root / pair_filename(pair)
        save_candles_csv(candles, path)
        files.append(
            {
                "name": path.name,
                "symbol": pair,
                "exchange_id": "binance",
                "timeframe": "4h",
                "rows": n,
                "first_ts": candles[0].ts,
                "last_ts": candles[-1].ts,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    manifest = {
        "format": MANIFEST_FORMAT,
        "exchange_id": "binance",
        "ccxt_version": "4.0.0",
        "timeframe": "4h",
        "fetched_at_utc": "2026-09-01T00:00:00Z",
        "files": files,
    }
    (root / MANIFEST_NAME).write_text(json.dumps(manifest))
    return root


def test_verify_manifest_accepts_only_hash_matching_listed_files(tmp_path):
    root = _manifest_dir(tmp_path)
    provenance, details = verify_manifest(root, PAIRS)
    assert provenance == "real" and details["problems"] == []
    assert details["manifest_sha256"] == file_sha256(root / MANIFEST_NAME)
    assert details["manifest_path"] == str(root / MANIFEST_NAME)
    assert details["exchange_id"] == "binance" and details["exchange_ids"] == ["binance"]
    assert all(f["verified"] and f["listed"] for f in details["files"].values())
    assert "cannot authenticate" in details["note"]
    assert verify_manifest(root, ["BTC/USDT"])[0] == "real"  # a subset is fine


@pytest.mark.parametrize(
    ("tamper", "problem"),
    [
        (lambda r: (r / MANIFEST_NAME).unlink(), "no manifest.json"),
        (lambda r: (r / MANIFEST_NAME).write_text("{not json"), "not valid JSON"),
        (lambda r: _edit_manifest(r, format="other/1"), "format is"),
        (lambda r: _edit_manifest(r, ccxt_version=""), "ccxt_version"),
        (lambda r: _edit_entry(r, 1, None), "not listed"),
        (lambda r: _edit_entry(r, 1, sha256="0" * 64), "does not match"),
        (lambda r: _edit_entry(r, 1, symbol="BNB/USDT"), "expected ETH/USDT 4h"),
        (lambda r: _edit_entry(r, 1, rows=24), "manifest says"),
        (lambda r: _edit_entry(r, 1, first_ts=T0 - TF), "manifest says"),
        (lambda r: _append_row(r / "ETH_USDT-4h.csv"), "does not match"),
        (lambda r: (r / "ETH_USDT-4h.csv").unlink(), "file not found"),
    ],
    ids=[
        "no-manifest",
        "bad-json",
        "format",
        "no-ccxt-version",
        "unlisted-file",
        "wrong-hash",
        "wrong-symbol",
        "wrong-rows",
        "wrong-first-ts",
        "tampered-csv",
        "missing-file",
    ],
)
def test_verify_manifest_otherwise_says_unverified(tmp_path, tamper, problem):
    root = _manifest_dir(tmp_path)
    tamper(root)
    provenance, details = verify_manifest(root, PAIRS)
    assert provenance == "unverified-csv"
    assert any(problem in p for p in details["problems"]), details["problems"]


def test_a_hand_written_csv_is_unverified(tmp_path):
    save_candles_csv(make(20), tmp_path / "BTC_USDT-4h.csv")
    provenance, details = verify_manifest(tmp_path, ["BTC/USDT"])
    assert provenance == "unverified-csv" and details["manifest_sha256"] is None
    assert details["files"]["BTC_USDT-4h.csv"]["sha256"] == file_sha256(
        tmp_path / "BTC_USDT-4h.csv"
    )
    assert verify_manifest(_manifest_dir(tmp_path / "m"), [])[0] == "unverified-csv"


def _edit_manifest(root: Path, **changes) -> None:
    path = root / MANIFEST_NAME
    manifest = json.loads(path.read_text())
    manifest.update(changes)
    path.write_text(json.dumps(manifest))


def _edit_entry(root: Path, k: int, entry=..., **changes) -> None:
    path = root / MANIFEST_NAME
    manifest = json.loads(path.read_text())
    if entry is None:
        del manifest["files"][k]
    else:
        manifest["files"][k].update(changes)
    path.write_text(json.dumps(manifest))


def _append_row(path: Path) -> None:
    candles = load_candles_csv(path)
    last = candles[-1]
    save_candles_csv([*candles, Candle(last.ts + TF, 1.0, 2.0, 0.5, 1.5, 1.0)], path)
