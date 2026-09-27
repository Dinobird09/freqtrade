import csv
from dataclasses import fields, replace
from pathlib import Path

import pytest

from research.trendbot.journal import (
    FEATURE_PREFIX,
    JOURNAL_COLUMNS,
    TIME_HELPER_COLUMNS,
    iso_to_ms,
    journal_header,
    ms_to_iso,
    read_journal,
    write_journal,
)
from research.trendbot.models import EXIT_SL, EXIT_TP, HOUR_MS, Trade


T0 = 1_704_067_200_000  # 2024-01-01T00:00:00Z


def make_trade(trade_id: int = 1, **changes) -> Trade:
    base = Trade(
        trade_id=trade_id,
        pair="BTC/USDT",
        variant="base",
        signal_ts=T0,
        entry_ts=T0 + 4 * HOUR_MS,
        entry_price=42_000.123456789,
        stop=41_000.5,
        target=44_000.0,
        qty=0.1 + 0.2,  # 0.30000000000000004: needs repr to survive
        risk_amount=100.0,
        risk_pct=1.0,
        stop_method="pivot",
        exit_ts=T0 + 12 * HOUR_MS,
        exit_price=44_000.0,
        exit_reason=EXIT_TP,
        fees=8.600000000000001,
        pnl=191.4,
        r_multiple=1.914,
        features={"rsi": 61.25, "vol_ratio": 1.7, "hour_utc": 8.0},
        ml_prob=0.6180339887498949,
        notes="",
    )
    return replace(base, **changes)


def open_trade(trade_id: int = 3) -> Trade:
    return make_trade(
        trade_id,
        pair="ETH/USDT",
        exit_ts=None,
        exit_price=None,
        exit_reason=None,
        pnl=None,
        r_multiple=None,
        ml_prob=None,
        features={"rsi": 55.0, "ema_gap_pct": -0.0},
    )


def sample_trades() -> list[Trade]:
    return [
        make_trade(1),
        make_trade(
            2,
            pair="BNB/USDT",
            exit_reason=EXIT_SL,
            pnl=-51.123456789012345,
            r_multiple=-1.0000000000000002,
            ml_prob=None,
            stop_method="lookback_low",
            notes='gap, "quoted"\nsecond line caf\u00e9 \u20ac',
            features={"dist_regime_pct": 1e-17, "rsi": 70.0},
        ),
        open_trade(3),
        make_trade(4, features={}, ml_prob=0.0, fees=0.0, risk_pct=0.25),
    ]


def test_columns_cover_every_trade_field():
    names = [f.name for f in fields(Trade) if f.name != "features"]
    assert [c for c in JOURNAL_COLUMNS if c in names] == names  # all fields, in field order
    for ms_col, iso_col in TIME_HELPER_COLUMNS.items():
        assert JOURNAL_COLUMNS.index(iso_col) == JOURNAL_COLUMNS.index(ms_col) + 1
    assert not any(c.startswith(FEATURE_PREFIX) for c in JOURNAL_COLUMNS)
    assert len(set(JOURNAL_COLUMNS)) == len(JOURNAL_COLUMNS)


def test_round_trip_is_exact(tmp_path: Path):
    trades = sample_trades()
    path = tmp_path / "trades.csv"
    write_journal(trades, path)
    back = read_journal(path)
    assert back == trades
    # dict equality ignores key order; also check the float bits and None-ness explicitly
    assert back[0].qty == 0.30000000000000004
    assert back[1].ml_prob is None and back[0].ml_prob == 0.6180339887498949
    assert back[3].ml_prob == 0.0
    assert back[2].exit_ts is None and back[2].exit_reason is None and back[2].pnl is None
    assert back[2].is_closed is False
    assert back[2].features == {"rsi": 55.0, "ema_gap_pct": -0.0}
    assert str(back[2].features["ema_gap_pct"]) == "-0.0"
    assert back[3].features == {}
    assert back[1].notes == trades[1].notes
    assert type(back[0].exit_ts) is int and type(back[0].trade_id) is int


def test_header_is_stable_union_of_sorted_features(tmp_path: Path):
    trades = sample_trades()
    path = tmp_path / "trades.csv"
    write_journal(trades, path)
    with path.open(newline="") as fh:
        header = next(csv.reader(fh))
    expected_features = ["dist_regime_pct", "ema_gap_pct", "hour_utc", "rsi", "vol_ratio"]
    assert tuple(header) == JOURNAL_COLUMNS + tuple("f_" + k for k in expected_features)
    assert journal_header(reversed(trades)) == tuple(header)


def test_none_is_empty_and_iso_helpers_are_written(tmp_path: Path):
    path = tmp_path / "trades.csv"
    write_journal(sample_trades(), path)
    with path.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    closed, open_row = rows[0], rows[2]
    assert closed["entry_ts"] == str(T0 + 4 * HOUR_MS)
    assert closed["signal_time_utc"] == "2024-01-01T00:00:00Z"
    assert closed["entry_time_utc"] == "2024-01-01T04:00:00Z"
    assert closed["exit_time_utc"] == "2024-01-01T12:00:00Z"
    for col in ("exit_ts", "exit_time_utc", "exit_price", "exit_reason", "pnl", "ml_prob"):
        assert open_row[col] == ""
    assert open_row["f_vol_ratio"] == ""  # feature absent for this trade
    assert rows[1]["ml_prob"] == ""


def test_read_uses_ms_columns_and_ignores_helper_columns(tmp_path: Path):
    trades = sample_trades()
    path = tmp_path / "trades.csv"
    write_journal(trades, path)
    with path.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
        header = list(rows[0].keys())
    for row in rows:
        for iso_col in TIME_HELPER_COLUMNS.values():
            row[iso_col] = "garbage, not a date"
        row["reviewer_comment"] = "extra human column"
    edited = tmp_path / "edited.csv"
    with edited.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=[*header, "reviewer_comment"])
        writer.writeheader()
        writer.writerows(rows)
    assert read_journal(edited) == trades

    # helper columns are optional on input
    stripped = tmp_path / "stripped.csv"
    keep = [c for c in header if c not in TIME_HELPER_COLUMNS.values()]
    with stripped.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=keep, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    assert read_journal(stripped) == trades


def test_empty_journal_round_trips(tmp_path: Path):
    path = tmp_path / "sub" / "empty.csv"
    write_journal([], path)
    assert path.read_text().strip() == ",".join(JOURNAL_COLUMNS)
    assert read_journal(path) == []


def test_write_is_deterministic(tmp_path: Path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    write_journal(sample_trades(), a)
    write_journal(sample_trades(), b)
    assert a.read_bytes() == b.read_bytes()


def test_missing_required_column_raises(tmp_path: Path):
    path = tmp_path / "bad.csv"
    path.write_text("trade_id,pair\n1,BTC/USDT\n")
    with pytest.raises(ValueError, match="missing required columns"):
        read_journal(path)


def test_malformed_cell_reports_line_number(tmp_path: Path):
    path = tmp_path / "trades.csv"
    write_journal([make_trade(1), make_trade(2)], path)
    lines = path.read_text().splitlines()
    lines[2] = lines[2].replace(",191.4,", ",not-a-number,", 1)
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(ValueError, match=r"line 3: column 'pnl'"):
        read_journal(path)


def test_required_numeric_cell_may_not_be_empty(tmp_path: Path):
    path = tmp_path / "trades.csv"
    write_journal([make_trade(1)], path)
    with path.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    rows[0]["entry_price"] = ""
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    with pytest.raises(ValueError, match="entry_price"):
        read_journal(path)


def test_int_fields_refuse_floats(tmp_path: Path):
    with pytest.raises(TypeError):
        write_journal([make_trade(1, exit_ts=1.5)], tmp_path / "x.csv")


def test_time_helpers():
    assert ms_to_iso(T0) == "2024-01-01T00:00:00Z"
    assert ms_to_iso(T0 + 1) == "2024-01-01T00:00:00.001Z"
    assert iso_to_ms("2024-01-01T00:00:00Z") == T0
    assert iso_to_ms("2024-01-01T01:00:00+01:00") == T0
    assert iso_to_ms("2024-01-01T00:00:00") == T0  # naive means UTC
    assert iso_to_ms(str(T0)) == T0
    for ts in (0, T0, T0 + 123, T0 + 4 * HOUR_MS + 999):
        assert iso_to_ms(ms_to_iso(ts)) == ts
