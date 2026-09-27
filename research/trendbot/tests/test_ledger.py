"""ledger: the D3 holdout ledger (append-only JSON lines, distinct looks per TEST window)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from research.trendbot import ledger as lg
from research.trendbot.models import HOUR_MS, Candle


TF = 4 * HOUR_MS
T0 = 1_704_067_200_000  # 2024-01-01T00:00:00Z
SHA = "a" * 64


def _window(pair: str = "BTC/USDT", start: int = T0, end: int = T0 + 100 * TF) -> lg.PairWindow:
    return lg.PairWindow(pair, SHA, start, end)


def _look(
    cfg: str = "c1", model: str | None = None, windows: tuple[lg.PairWindow, ...] | None = None
) -> lg.Look:
    return lg.Look(
        run_utc="2026-01-01T00:00:00Z",
        argv=("--data-dir", "d", "--out-dir", "o"),
        variant="base+ml" if model else "base",
        config_fingerprint=cfg,
        model_fingerprint=model,
        split_utc="2024-01-01T00:00:00Z",
        pairs=windows or (_window(), _window("ETH/USDT")),
    )


def test_line_format_has_exactly_the_d3_fields(tmp_path: Path) -> None:
    path = tmp_path / "looks.jsonl"
    assert lg.append_looks(path, [_look(), _look("c2", "m1")]) == 2
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    raw = json.loads(lines[1])
    assert (
        tuple(raw)
        == lg.LOOK_KEYS
        == (
            "run_utc",
            "argv",
            "variant",
            "config_fingerprint",
            "model_fingerprint",
            "split_utc",
            "pairs",
        )
    )
    assert raw["model_fingerprint"] == "m1" and json.loads(lines[0])["model_fingerprint"] is None
    assert tuple(raw["pairs"][0]) == lg.PAIR_KEYS
    assert raw["pairs"][0] == {
        "pair": "BTC/USDT",
        "file_sha256": SHA,
        "test_start_ts": T0,
        "test_end_ts": T0 + 100 * TF,
    }
    assert lg.read_ledger(path) == [_look(), _look("c2", "m1")]  # exact round trip


def test_append_only_and_missing_file_is_empty(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "looks.jsonl"
    assert lg.read_ledger(path) == []
    lg.append_looks(path, [_look()])
    lg.append_looks(path, [_look()])
    assert len(lg.read_ledger(path)) == 2  # both lines kept; the count is by identity


def test_distinct_looks_count_identities_on_overlapping_windows() -> None:
    later = _window(start=T0 + 100 * TF, end=T0 + 200 * TF)  # starts where the first ends
    looks = [
        _look("c1"),
        _look("c1"),  # an identical re-run: not a new look
        _look("c1", "m1"),  # same config, a fitted model: a different candidate
        _look("c2"),
        _look("c3", windows=(later,)),  # a fresh, non-overlapping TEST window
        _look("c4", windows=(_window("ETH/USDT"),)),  # another pair only
    ]
    got = lg.distinct_looks(looks, "BTC/USDT", T0, T0 + 100 * TF)
    assert got == {("c1", None), ("c1", "m1"), ("c2", None)}
    assert lg.distinct_looks(looks, "BTC/USDT", T0 + 100 * TF, T0 + 300 * TF) == {("c3", None)}
    assert lg.look_counts(looks, [_window(), _window("ETH/USDT")]) == {
        "BTC/USDT": 3,
        "ETH/USDT": 4,
    }
    assert lg.overlaps(0, 10, 9, 20) and not lg.overlaps(0, 10, 10, 20)


def test_record_looks_reports_the_cumulative_counts(tmp_path: Path) -> None:
    path = tmp_path / "looks.jsonl"
    windows = (_window(), _window("ETH/USDT"))
    first = lg.record_looks(path, [_look(f"c{i}") for i in range(4)], windows, 4)
    assert first.appended == 4 and first.counts == {"BTC/USDT": 4, "ETH/USDT": 4}
    assert first.over_limit == []
    again = lg.record_looks(path, [_look(f"c{i}") for i in range(4)], windows, 4)
    assert again.appended == 4 and again.counts == first.counts  # re-run: no new look
    revised = lg.record_looks(path, [_look("c-revised")], windows, 4)
    assert revised.counts == {"BTC/USDT": 5, "ETH/USDT": 5}
    assert revised.over_limit == ["BTC/USDT", "ETH/USDT"]
    assert len(lg.read_ledger(path)) == 9


def test_malformed_lines_name_the_line(tmp_path: Path) -> None:
    path = tmp_path / "looks.jsonl"
    lg.append_looks(path, [_look()])
    with path.open("a", encoding="utf-8") as fh:
        fh.write("\n")  # blank lines are skipped
        fh.write('{"variant": "base"}\n')
    with pytest.raises(ValueError, match=r"looks\.jsonl:3: missing run_utc"):
        lg.read_ledger(path)
    bad = json.loads(_look().to_json())
    bad["pairs"][0]["test_end_ts"] = T0 - 1
    with pytest.raises(ValueError, match="before test_start_ts"):
        lg.parse_look(json.dumps(bad))
    with pytest.raises(ValueError, match="not valid JSON"):
        lg.parse_look("{")
    bad = json.loads(_look().to_json())
    bad["pairs"] = []
    with pytest.raises(ValueError, match="non-empty"):
        lg.parse_look(json.dumps(bad))


def test_default_paths_follow_d3(tmp_path: Path) -> None:
    out, data = tmp_path / "out", tmp_path / "data"
    assert lg.default_ledger_path(out, data) == data / ".test_looks.jsonl"
    assert lg.default_ledger_path(out, None, synthetic=True) == out / "test_looks.jsonl"
    assert lg.default_ledger_path(out, data, synthetic=True) == out / "test_looks.jsonl"


def test_series_hash_and_windows() -> None:
    candles = [Candle(T0 + i * TF, 1.0, 1.5, 0.5, 1.25, 10.0) for i in range(10)]
    h = lg.series_sha256(candles)
    assert h == lg.series_sha256(list(candles)) and len(h) == 64
    moved = [*candles[:-1], Candle(candles[-1].ts, 1.0, 1.5, 0.5, 1.2500001, 10.0)]
    assert lg.series_sha256(moved) != h
    data = {"ETH/USDT": candles, "BTC/USDT": candles[:8]}
    windows = lg.holdout_windows(data, T0 + 5 * TF, TF, {"BTC/USDT": "b", "ETH/USDT": "e"})
    assert windows == (
        lg.PairWindow("BTC/USDT", "b", T0 + 5 * TF, T0 + 8 * TF),
        lg.PairWindow("ETH/USDT", "e", T0 + 5 * TF, T0 + 10 * TF),
    )
    with pytest.raises(ValueError, match="no candles"):
        lg.holdout_windows({"BTC/USDT": []}, T0, TF, {"BTC/USDT": "b"})
