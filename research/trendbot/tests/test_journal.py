import csv
import json
import math
from dataclasses import fields, replace
from pathlib import Path

import pytest

from research.trendbot.config import StrategyConfig
from research.trendbot.journal import (
    FEATURE_PREFIX,
    IMPORT_REQUIRED_FIELDS,
    JOURNAL_COLUMNS,
    TIME_HELPER_COLUMNS,
    import_external,
    iso_to_ms,
    journal_header,
    load_import_map,
    main,
    ms_to_iso,
    normalize_pair,
    read_journal,
    write_journal,
)
from research.trendbot.models import EXIT_END, EXIT_SL, EXIT_TP, HOUR_MS, Trade
from research.trendbot.review_sheet import auto_flags


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


# ---------------------------------------------------------------------------- import_external
# A MADE-UP external format standing in for the existing bot's journal. The real mapping
# must be written for the user's real journal columns and exit-reason values.
CFG = StrategyConfig()
F, S = CFG.fee_rate, CFG.slippage_pct / 100
ENTRY, STOP, QTY = 100.0, 95.0, 2.0
STOP_X = STOP * (1 - S)
L_U = (ENTRY - STOP_X) + F * ENTRY + F * STOP_X  # CONTRACT v2 A1 all-in loss per unit
TARGET = (ENTRY * (1 + F) + CFG.reward_risk * L_U) / (1 - F)

EXTERNAL_HEADER = (
    "Ticket,Symbol,Opened (UTC),Entry Px,SL Px,TP Px,Size,Closed (UTC),Exit Px,Close Reason,"
    "Account Equity,Net PnL,Commission,Memo"
)
EXTERNAL_ROWS = (
    # a take-profit at the A1 target, pnl and fees NOT given (derived)
    f"7,BTCUSDT,2024-04-01 08:00:05,{ENTRY!r},{STOP!r},{TARGET!r},{QTY!r},"
    f"2024-04-01 20:00:00,{TARGET!r},take_profit,10000,,,first",
    # a clean stop-loss at stop * (1 - slippage)
    f"8,ETH-USDT,2024-04-02T12:30:00Z,{ENTRY!r},{STOP!r},{TARGET!r},{QTY!r},"
    f"2024-04-02T16:00:00Z,{STOP_X!r},Stop Loss,10000,,,",
    # closed by the bot at the end of the run, pnl and commission given by the bot
    f"9,bnb_usdt,2024-04-03 00:00:00,{ENTRY!r},{STOP!r},{TARGET!r},1.0,"
    "2024-04-03 12:00:00,101.0,manual,5000,0.8,0.2,closed by hand",
)
MAPPING = {
    "Ticket": "trade_id",
    "Symbol": "pair",
    "Opened (UTC)": "entry_ts",
    "Entry Px": "entry_price",
    "SL Px": "stop",
    "TP Px": "target",
    "Size": "qty",
    "Closed (UTC)": "exit_ts",
    "Exit Px": "exit_price",
    "Close Reason": "exit_reason",
    "Account Equity": "equity",
    "Net PnL": "pnl",
    "Commission": "fees",
    "Memo": "notes",
}
REASONS = {"take_profit": "TP", "stop loss": "SL", "manual": "END"}


def external_csv(tmp_path: Path, rows=EXTERNAL_ROWS, header=EXTERNAL_HEADER) -> Path:
    path = tmp_path / "bot.csv"
    path.write_text("\n".join([header, *rows]) + "\n", encoding="utf-8")
    return path


def imported(tmp_path: Path, **kwargs) -> list[Trade]:
    opts = {"exit_reason_map": REASONS, "defaults": {"variant": "base"}, **kwargs}
    return import_external(external_csv(tmp_path), MAPPING, **opts)


def test_import_external_maps_columns_and_derives_a1_risk_and_r(tmp_path: Path):
    tp, sl, end = imported(tmp_path)
    assert [t.trade_id for t in (tp, sl, end)] == [7, 8, 9]
    assert [t.pair for t in (tp, sl, end)] == ["BTC/USDT", "ETH/USDT", "BNB/USDT"]
    assert [t.exit_reason for t in (tp, sl, end)] == [EXIT_TP, EXIT_SL, EXIT_END]
    assert tp.variant == "base" and tp.stop_method == "external" and tp.notes == "first"
    assert tp.entry_ts == iso_to_ms("2024-04-01T08:00:05Z")  # the real fill time
    assert tp.signal_ts == iso_to_ms("2024-04-01T04:00:00Z")  # candle before the fill candle
    assert tp.exit_ts == iso_to_ms("2024-04-01T20:00:00Z")
    assert math.isclose(tp.risk_amount, QTY * L_U, rel_tol=1e-12)
    assert math.isclose(tp.risk_pct, QTY * L_U / 10_000 * 100, rel_tol=1e-12)
    assert math.isclose(tp.fees, F * QTY * (ENTRY + TARGET), rel_tol=1e-12)
    assert math.isclose(tp.r_multiple, CFG.reward_risk, rel_tol=1e-9)  # A1: TP == +RR
    assert math.isclose(sl.r_multiple, -1.0, rel_tol=1e-9)  # A1: clean SL == -1R
    assert end.pnl == 0.8 and end.fees == 0.2  # given by the bot: used as is (net pnl)
    assert math.isclose(end.risk_pct, 1.0 * L_U / 5_000 * 100, rel_tol=1e-12)
    assert end.r_multiple == end.pnl / end.risk_amount
    assert auto_flags(tp, CFG) == [] and auto_flags(sl, CFG) == []  # audit-ready rows


def test_import_external_mapped_risk_and_defaults(tmp_path: Path):
    header = "sym,t_in,px_in,sl,tp,n,t_out,px_out,why,risk,riskpct"
    levels = f"{ENTRY!r},{STOP!r},{TARGET!r},{QTY!r}"
    row = f"BTC/USDT,1711958400000,{levels},1712001600000,95,SL,12.5,0.125"
    mapping = dict(
        zip(
            header.split(","),
            [
                "pair",
                "entry_ts",
                "entry_price",
                "stop",
                "target",
                "qty",
                "exit_ts",
                "exit_price",
                "exit_reason",
                "risk_amount",
                "risk_pct",
            ],
            strict=True,
        )
    )
    (t,) = import_external(external_csv(tmp_path, [row], header), mapping, time_format="ms")
    assert (t.trade_id, t.variant, t.risk_amount, t.risk_pct) == (1, "external", 12.5, 0.125)
    assert t.entry_ts == 1711958400000 and t.exit_ts == 1712001600000
    assert t.r_multiple == t.pnl / 12.5


@pytest.mark.parametrize(
    "time_format, opened, closed",
    [("s", "1711958400", "1712001600.5"), ("ms", "1711958400000", "1712001600500")],
)
def test_import_external_epoch_time_formats(tmp_path: Path, time_format, opened, closed):
    rows = [
        EXTERNAL_ROWS[0]
        .replace("2024-04-01 08:00:05", opened)
        .replace("2024-04-01 20:00:00", closed)
    ]
    (t,) = import_external(
        external_csv(tmp_path, rows), MAPPING, time_format=time_format, exit_reason_map=REASONS
    )
    assert t.entry_ts == 1711958400000 and t.exit_ts == 1712001600500


@pytest.mark.parametrize(
    "raw, pair",
    [
        ("BTCUSDT", "BTC/USDT"),
        ("btc-usdt", "BTC/USDT"),
        ("ETH_USDC", "ETH/USDC"),
        ("BNB/USDT", "BNB/USDT"),
        ("BTC/USDT:USDT", "BTC/USDT"),
        ("ETHBTC", "ETH/BTC"),
        (" bnbfdusd ", "BNB/FDUSD"),
    ],
)
def test_normalize_pair(raw: str, pair: str):
    assert normalize_pair(raw) == pair


@pytest.mark.parametrize("raw", ["BTC", "USDT", "", "A/B/C", "BTCXYZ"])
def test_normalize_pair_rejects_unknown_symbols(raw: str):
    with pytest.raises(ValueError, match="neither BASE/QUOTE"):
        normalize_pair(raw)


@pytest.mark.parametrize(
    "mapping_change, kwargs, match",
    [
        ({"Size": "quantity"}, {}, "not an importable field"),
        ({"Memo": "r_multiple"}, {}, "r_multiple is always pnl / risk_amount"),
        ({"Memo": "pair"}, {}, "both map to 'pair'"),
        ({"Size": None}, {}, r"required fields \['qty'\]"),
        ({"Account Equity": None}, {}, "risk_pct cannot be derived"),
        ({"Nope": "ml_prob"}, {}, r"mapped columns \['Nope'\] are not in the header"),
        ({}, {"time_format": "unix"}, "time_format 'unix'"),
        ({}, {"exit_reason_map": {"take_profit": "WIN"}}, "must map to one of SL, TP, END"),
        ({}, {"defaults": {"colour": "red"}}, r"defaults \['colour'\]"),
        ({}, {"exit_reason_map": {"take_profit": "TP"}}, "line 3: exit reason 'Stop Loss'"),
        ({}, {"time_format": "ms"}, "line 2: entry_ts '2024-04-01 08:00:05' is not a 'ms' time"),
    ],
)
def test_import_external_errors(tmp_path: Path, mapping_change, kwargs, match):
    mapping = {k: v for k, v in {**MAPPING, **mapping_change}.items() if v is not None}
    opts = {"exit_reason_map": REASONS, **kwargs}
    with pytest.raises(ValueError, match=match):
        import_external(external_csv(tmp_path), mapping, **opts)


@pytest.mark.parametrize(
    "old, new, match",
    [
        (f"{STOP!r},{TARGET!r},{QTY!r}", f"{ENTRY + 1!r},{TARGET!r},{QTY!r}", "not positive"),
        ("take_profit", "", "exit_reason is empty"),
        (f",{QTY!r},", ",two,", "qty 'two' is not a number"),
        (f",{QTY!r},", ",-1.0,", "qty -1.0 must be positive"),
        ("2024-04-01 20:00:00", "", "entry_ts and exit_ts may not be empty"),
        ("2024-04-01 08:00:05", "1711958400000", "is not a 'iso' time"),
        ("BTCUSDT", "XYZ", "neither BASE/QUOTE"),
        (",10000,", ",0,", "equity 0.0 must be positive"),
        ("7,BTCUSDT", "8,BTCUSDT", "line 3: trade_id 8 repeats line 2"),
        ("7,BTCUSDT", "7.5,BTCUSDT", "trade_id '7.5' is not an integer"),
    ],
)
def test_import_external_row_errors_name_the_line(tmp_path: Path, old, new, match):
    rows = [EXTERNAL_ROWS[0].replace(old, new, 1), EXTERNAL_ROWS[1]]
    with pytest.raises(ValueError, match=match) as info:
        import_external(external_csv(tmp_path, rows), MAPPING, exit_reason_map=REASONS)
    assert "bot.csv: line " in str(info.value)


def test_import_external_required_fields_are_documented():
    assert IMPORT_REQUIRED_FIELDS == (
        "pair",
        "entry_ts",
        "entry_price",
        "stop",
        "target",
        "qty",
        "exit_ts",
        "exit_price",
        "exit_reason",
    )


def write_map(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "map.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_cli_convert_writes_a_journal_that_round_trips(tmp_path: Path, capsys):
    src = external_csv(tmp_path)
    map_path = write_map(
        tmp_path,
        {"columns": MAPPING, "exit_reason_values": REASONS, "defaults": {"variant": "base"}},
    )
    out = tmp_path / "journal.csv"
    argv = ["convert", "--in", str(src), "--map", str(map_path), "--out", str(out)]
    assert main(argv) == 0
    printed = capsys.readouterr().out
    assert "Converted 3 trades" in printed and "real columns" in printed
    assert read_journal(out) == imported(tmp_path)
    # the costs used for the A1 risk come from the flags (the user's real fee tier)
    assert main([*argv, "--fee-rate", "0.006", "--slippage-pct", "0.1"]) == 0
    capsys.readouterr()
    cfg = StrategyConfig(fee_rate=0.006, slippage_pct=0.1)
    assert read_journal(out) == imported(tmp_path, cfg=cfg)
    assert read_journal(out)[0].risk_amount != imported(tmp_path)[0].risk_amount


def test_cli_convert_errors(tmp_path: Path, capsys):
    src = external_csv(tmp_path)
    out = tmp_path / "journal.csv"

    def run(map_data, *extra) -> int:
        path = write_map(tmp_path, map_data)
        return main(["convert", "--in", str(src), "--map", str(path), "--out", str(out), *extra])

    assert run({"columns": MAPPING}) == 1  # 'take_profit' is not mapped to SL/TP/END
    assert "exit reason 'take_profit'" in capsys.readouterr().err
    assert run({"columns": MAPPING, "colour": 1}) == 1
    assert "unknown keys ['colour']" in capsys.readouterr().err
    assert run({"exit_reason_values": REASONS}) == 1
    assert "columns (external column -> Trade field) is required" in capsys.readouterr().err
    assert not out.exists()
    with pytest.raises(SystemExit) as info:
        run({"columns": MAPPING, "exit_reason_values": REASONS}, "--fee-rate", "0")
    assert info.value.code == 2  # zero-cost conversions are refused by the config


def test_load_import_map_defaults(tmp_path: Path):
    kwargs = load_import_map(write_map(tmp_path, {"columns": {"a": "pair"}}))
    assert kwargs == {
        "mapping": {"a": "pair"},
        "exit_reason_map": {},
        "time_format": "iso",
        "defaults": {},
    }
    with pytest.raises(ValueError, match="must be a JSON object"):
        load_import_map(write_map(tmp_path, [1]))
    with pytest.raises(ValueError, match="defaults must be a JSON object"):
        load_import_map(write_map(tmp_path, {"columns": {"a": "pair"}, "defaults": [1]}))
