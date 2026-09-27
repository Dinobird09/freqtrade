from datetime import UTC, datetime
from pathlib import Path

import pytest

from research.trendbot import news as news_mod
from research.trendbot.config import StrategyConfig
from research.trendbot.models import HOUR_MS, NewsEvent
from research.trendbot.news import (
    RULE,
    NewsCalendar,
    format_time_utc,
    load_events,
    parse_time_utc,
)


CFG = StrategyConfig()
EXAMPLE = Path(__file__).resolve().parents[1] / "events_example.csv"
PAIRS = ("BTC/USDT", "ETH/USDT", "BNB/USDT")
HEADER = "time_utc,scope,impact,kind,note\n"
T = int(datetime(2024, 3, 12, 12, 30, tzinfo=UTC).timestamp() * 1000)
H = HOUR_MS


def _ms(y, mo, d, h=0, mi=0):
    return int(datetime(y, mo, d, h, mi, tzinfo=UTC).timestamp() * 1000)


def _ev(ts=T, scope="ALL", impact="high", kind="macro", note="test event"):
    return NewsEvent(ts=ts, scope=scope, impact=impact, kind=kind, note=note)


def _cal(*events, cfg=CFG):
    return NewsCalendar(events, cfg)


def _write(tmp_path, body, name="events.csv"):
    path = tmp_path / name
    path.write_text(HEADER + body, encoding="utf-8")
    return path


# ------------------------------------------------------------------------------ parsing
def test_time_formats_agree():
    assert parse_time_utc("2024-03-12T12:30:00Z") == T
    assert parse_time_utc("2024-03-12T12:30:00+00:00") == T
    assert parse_time_utc(str(T)) == T
    assert parse_time_utc(" 2024-03-12T14:30:00+02:00 ") == T  # offsets converted to UTC
    assert parse_time_utc("2024-03-12T12:30:00.250Z") == T + 250
    assert format_time_utc(T) == "2024-03-12T12:30:00Z"
    assert format_time_utc(T + 250) == "2024-03-12T12:30:00.250Z"


def test_load_example_calendar():
    events = load_events(EXAMPLE)
    assert len(events) == 7
    assert [e.ts for e in events] == sorted(e.ts for e in events)
    assert all("EXAMPLE ONLY" in e.note and "not a real calendar" in e.note for e in events)
    by_kind = {e.kind: e for e in events}
    assert set(by_kind) == {"macro", "regulatory", "unlock", "launchpool", "bnb_burn"}
    assert by_kind["unlock"].ts == _ms(2024, 3, 1)  # the integer-ms row
    assert by_kind["unlock"].scope == "BTC"
    assert by_kind["regulatory"].scope == "EXCHANGE:binance"
    assert by_kind["regulatory"].ts == _ms(2024, 2, 20, 8)  # the +00:00 row
    assert by_kind["bnb_burn"].impact == "low"
    assert sum(e.kind == "macro" and e.impact == "high" for e in events) == 3


def test_loader_normalizes_case_and_skips_blank_lines(tmp_path):
    path = _write(tmp_path, f"{T},bnb,LOW,BNB_Burn,  burn  \n\n{T},exchange:Binance,High,Macro,x\n")
    events = load_events(path)
    assert [(e.scope, e.impact, e.kind, e.note) for e in events] == [
        ("BNB", "low", "bnb_burn", "burn"),
        ("EXCHANGE:binance", "high", "macro", "x"),
    ]


def test_header_only_file_is_an_empty_unloaded_calendar(tmp_path):
    path = _write(tmp_path, "")
    events = load_events(path)
    assert events == []
    cal = NewsCalendar(events, CFG)
    assert cal.loaded() is False
    dec = cal.check("BTC/USDT", T)
    assert dec.allowed and dec.rule == RULE
    assert "EMPTY" in dec.reason


@pytest.mark.parametrize(
    ("body", "line", "fragment"),
    [
        (f"{T},ALL,high,macro,ok\nnot-a-time,ALL,high,macro,bad\n", 3, "time_utc"),
        (f"{T},ALL,high,macro,ok\n2024-03-12T12:30:00,ALL,high,macro,naive\n", 3, "offset"),
        ("12.5,ALL,high,macro,float ms\n", 2, "time_utc"),
        (f"{T},ALL,critical,macro,bad impact\n", 2, "impact"),
        (f"{T},ALL,,macro,missing impact\n", 2, "impact"),
        (f"{T},,high,macro,empty scope\n", 2, "scope"),
        (f"{T},   ,high,macro,blank scope\n", 2, "scope"),
        (f"{T},EXCHANGE:,high,macro,no exchange id\n", 2, "EXCHANGE"),
        (f"{T},ALL,high,bnb-burn,typo kind\n", 2, "kind"),
        (f"{T},ALL,high,macro\n", 2, "columns"),
        (f"{T},ALL,high,macro,a,b\n", 2, "columns"),
        (f"{T},ALL,high,macro,ok\n\n{T},ALL,high,macro,ok\n,ALL,high,macro,no time\n", 5, "time"),
    ],
)
def test_malformed_rows_raise_with_line_number(tmp_path, body, line, fragment):
    path = _write(tmp_path, body)
    with pytest.raises(ValueError, match=f"line {line}:") as info:
        load_events(path)
    assert fragment in str(info.value)
    assert str(path) in str(info.value)


def test_bad_or_missing_header(tmp_path):
    bad = tmp_path / "bad.csv"
    bad.write_text("ts,scope,impact,kind,note\n1,ALL,high,macro,x\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"line 1:.*header"):
        load_events(bad)
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="line 1:"):
        load_events(empty)


# ------------------------------------------------------------------------------ blackout
@pytest.mark.parametrize("pair", PAIRS)
def test_high_impact_all_boundaries_are_inclusive(pair):
    cal = _cal(_ev())
    for ts in (T - 2 * H, T - 1, T, T + 1, T + 2 * H):
        dec = cal.check(pair, ts)
        assert not dec.allowed, (pair, ts)
        assert dec.rule == RULE
    for ts in (T - 2 * H - 1, T + 2 * H + 1, T - 30 * H, T + 30 * H):
        dec = cal.check(pair, ts)
        assert dec.allowed, (pair, ts)
        assert dec.rule == RULE


@pytest.mark.parametrize("impact", ["medium", "low"])
def test_non_high_impact_does_not_block(impact):
    cal = _cal(_ev(impact=impact))
    for pair in PAIRS:
        assert cal.check(pair, T).allowed


def test_widened_blackout_is_respected():
    cal = _cal(_ev(), cfg=CFG.with_changes(news_blackout_hours=3.0))
    assert not cal.check("ETH/USDT", T + 3 * H).allowed
    assert cal.check("ETH/USDT", T + 3 * H + 1).allowed


def test_exchange_scope_matches_configured_exchange_only():
    cal = _cal(_ev(scope="EXCHANGE:binance", kind="regulatory"))
    for pair in PAIRS:
        assert not cal.check(pair, T + 2 * H).allowed
        assert cal.check(pair, T + 2 * H + 1).allowed
    other = _cal(_ev(scope="EXCHANGE:coinbase", kind="regulatory"))
    assert all(other.check(p, T).allowed for p in PAIRS)
    on_coinbase = _cal(
        _ev(scope="EXCHANGE:coinbase", kind="regulatory"),
        cfg=CFG.with_changes(exchange_id="coinbase"),
    )
    assert all(not on_coinbase.check(p, T).allowed for p in PAIRS)


def test_btc_scoped_high_impact_unlock_does_not_block_eth_or_bnb():
    cal = _cal(_ev(scope="BTC", kind="unlock", note="BTC unlock"))
    assert not cal.check("BTC/USDT", T + H).allowed
    assert cal.check("ETH/USDT", T).allowed
    assert cal.check("BNB/USDT", T).allowed


@pytest.mark.parametrize("impact", ["low", "medium", "high"])
@pytest.mark.parametrize("scope", ["BNB", "ALL", "EXCHANGE:binance"])
@pytest.mark.parametrize("kind", ["bnb_burn", "launchpool"])
def test_bnb_events_block_bnb_for_24h_any_impact(impact, scope, kind):
    cal = _cal(_ev(scope=scope, impact=impact, kind=kind, note="bnb thing"))
    for ts in (T - 24 * H, T - 23 * H, T, T + 24 * H):
        dec = cal.check("BNB/USDT", ts)
        assert not dec.allowed, ts
        assert dec.rule == RULE
        assert "+/-24h BNB-event" in dec.reason
    assert cal.check("BNB/USDT", T - 24 * H - 1).allowed
    assert cal.check("BNB/USDT", T + 24 * H + 1).allowed


def test_bnb_burn_does_not_block_btc_or_eth():
    for impact in ("low", "medium", "high"):
        cal = _cal(_ev(scope="BNB", impact=impact, kind="bnb_burn"))
        for pair in ("BTC/USDT", "ETH/USDT"):
            assert cal.check(pair, T).allowed, (impact, pair)
            assert cal.check(pair, T + H).allowed, (impact, pair)


def test_high_impact_exchange_launchpool_blocks_others_only_for_2h():
    cal = _cal(_ev(scope="EXCHANGE:binance", kind="launchpool"))
    assert not cal.check("BTC/USDT", T + 2 * H).allowed
    assert cal.check("BTC/USDT", T + 3 * H).allowed
    assert not cal.check("BNB/USDT", T + 20 * H).allowed


def test_bnb_scope_rule_ignores_other_exchanges_and_other_kinds():
    other_exchange = _cal(_ev(scope="EXCHANGE:coinbase", impact="low", kind="bnb_burn"))
    assert other_exchange.check("BNB/USDT", T).allowed
    assert _cal(_ev(scope="BNB", impact="medium", kind="unlock")).check("BNB/USDT", T).allowed
    assert _cal(_ev(scope="ETH", impact="low", kind="launchpool")).check("BNB/USDT", T).allowed


def test_reason_names_kind_time_and_note():
    cal = _cal(_ev(kind="macro", note="US CPI print"))
    dec = cal.check("ETH/USDT", T - H)
    assert not dec.allowed
    assert "macro" in dec.reason
    assert "2024-03-12T12:30:00Z" in dec.reason
    assert "US CPI print" in dec.reason
    assert "ETH/USDT" in dec.reason
    assert dec.reason.endswith(".") and dec.reason.count("\n") == 0


def test_nearest_blocking_event_is_reported():
    far = _ev(ts=T - 90 * 60_000, note="far")
    near = _ev(ts=T + 30 * 60_000, note="near")
    dec = _cal(far, near).check("BTC/USDT", T)
    assert not dec.allowed
    assert "near" in dec.reason and "far" not in dec.reason


def test_unsorted_input_and_normalization():
    events = [_ev(ts=T + 50 * H, note="late"), _ev(ts=T, scope="all", impact="HIGH", note="x")]
    cal = NewsCalendar(events, CFG)
    assert [e.ts for e in cal.events] == [T, T + 50 * H]
    assert cal.events[0].scope == "ALL" and cal.events[0].impact == "high"
    assert not cal.check("BTC/USDT", T).allowed
    assert not cal.check("BTC/USDT", T + 50 * H).allowed
    assert cal.loaded() and len(cal) == 2


def test_calendar_rejects_invalid_events():
    with pytest.raises(ValueError, match="impact"):
        NewsCalendar([_ev(impact="severe")], CFG)
    with pytest.raises(ValueError, match="scope"):
        NewsCalendar([_ev(scope="")], CFG)


def test_bisect_only_scans_the_window_on_a_large_calendar(monkeypatch):
    # 100k events spaced 5h apart: +/-2h windows contain at most one event, and a BNB
    # +/-24h window at most ~10, no matter how large the calendar is.
    n = 100_000
    start = _ms(2010, 1, 1)
    events = [_ev(ts=start + i * 5 * H, impact="medium", kind="other") for i in range(n)]
    events.append(_ev(ts=T + 60_000, note="the one"))
    cal = NewsCalendar(events, CFG)
    calls = []
    real_left, real_right = news_mod.bisect_left, news_mod.bisect_right
    monkeypatch.setattr(news_mod, "bisect_left", lambda a, x: calls.append("L") or real_left(a, x))
    monkeypatch.setattr(
        news_mod, "bisect_right", lambda a, x: calls.append("R") or real_right(a, x)
    )
    lo, hi = cal._window(T, CFG.news_blackout_hours * H)
    assert hi - lo <= 2
    lo, hi = cal._window(T, CFG.bnb_event_blackout_hours * H)
    assert hi - lo <= 11
    dec = cal.check("BTC/USDT", T)
    assert not dec.allowed and "the one" in dec.reason
    assert calls[-2:] == ["L", "R"]


def test_example_calendar_semantics():
    cal = NewsCalendar(load_events(EXAMPLE), CFG)
    assert cal.loaded()
    fomc = _ms(2024, 1, 31, 19)
    assert all(not cal.check(p, fomc - 2 * H).allowed for p in PAIRS)
    assert all(cal.check(p, fomc - 2 * H - 1).allowed for p in PAIRS)
    reg = _ms(2024, 2, 20, 8)
    assert all(not cal.check(p, reg + H).allowed for p in PAIRS)
    unlock = _ms(2024, 3, 1)
    assert not cal.check("BTC/USDT", unlock).allowed
    assert cal.check("ETH/USDT", unlock).allowed
    assert cal.check("BNB/USDT", unlock).allowed
    burn = _ms(2024, 4, 15)
    dec = cal.check("BNB/USDT", burn - 20 * H)
    assert not dec.allowed
    assert "bnb_burn" in dec.reason and "2024-04-15T00:00:00Z" in dec.reason
    assert "quarterly BNB burn" in dec.reason
    assert cal.check("BTC/USDT", burn).allowed and cal.check("ETH/USDT", burn).allowed
    pool = _ms(2024, 3, 20)
    assert not cal.check("BNB/USDT", pool + 24 * H).allowed
    assert cal.check("BNB/USDT", pool + 24 * H + 1).allowed
    assert cal.check("ETH/USDT", pool).allowed  # medium impact: no block for ETH
    quiet = _ms(2024, 2, 5)
    dec = cal.check("BNB/USDT", quiet)
    assert dec.allowed and dec.rule == RULE and "No high-impact news" in dec.reason
