import random
from datetime import UTC, datetime
from pathlib import Path

import pytest

from research.trendbot import news as news_mod
from research.trendbot.config import StrategyConfig
from research.trendbot.models import HOUR_MS, NewsEvent
from research.trendbot.news import (
    KNOWN_KINDS,
    RULE,
    SCHEDULED_KINDS,
    UNSCHEDULED_KINDS,
    NewsCalendar,
    block_interval,
    format_time_utc,
    is_scheduled,
    known_from,
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


def _ev(ts=T, scope="ALL", impact="high", kind="macro", note="test event", known_from_ts=None):
    return NewsEvent(
        ts=ts, scope=scope, impact=impact, kind=kind, note=note, known_from_ts=known_from_ts
    )


def _cal(*events, cfg=CFG):
    return NewsCalendar(events, cfg)


def _write(tmp_path, body, name="events.csv", header=HEADER):
    path = tmp_path / name
    path.write_text(header + body, encoding="utf-8")
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
    # known_from_utc: empty = kind default for every row but the pre-announced launchpool.
    assert by_kind["launchpool"].known_from_ts == _ms(2024, 3, 19, 12)
    assert all(e.known_from_ts is None for e in events if e.kind != "launchpool")
    assert not is_scheduled(by_kind["regulatory"].kind)  # the headline is unscheduled


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
    with pytest.raises(ValueError, match="kind"):  # every kind must be (un)scheduled
        NewsCalendar([_ev(kind="rumour")], CFG)
    for bad in (1.5, "123", True, float("nan")):
        with pytest.raises(ValueError, match="known_from_ts"):
            NewsCalendar([_ev(known_from_ts=bad)], CFG)


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
    assert all(not cal.check(p, reg).allowed for p in PAIRS)
    assert all(cal.check(p, reg - 1).allowed for p in PAIRS)  # unscheduled: no pre-window
    assert all(cal.check(p, reg - H).allowed for p in PAIRS)
    assert "unscheduled regulatory" in cal.check("BTC/USDT", reg + H).reason
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
    # Announced 12h ahead (known_from_utc): the 24h pre-window starts only at the announcement.
    assert not cal.check("BNB/USDT", pool - 12 * H).allowed
    assert cal.check("BNB/USDT", pool - 12 * H - 1).allowed
    assert "known from 2024-03-19T12:00:00Z" in cal.check("BNB/USDT", pool).reason
    assert cal.check("ETH/USDT", pool).allowed  # medium impact: no block for ETH
    quiet = _ms(2024, 2, 5)
    dec = cal.check("BNB/USDT", quiet)
    assert dec.allowed and dec.rule == RULE and "No news blackout covers" in dec.reason
    assert "unscheduled news from the moment it happened" in dec.reason


# ------------------------------------------------------------------ C2: no look-ahead on R5
M = 60_000


def test_kind_partition_is_complete_and_disjoint():
    assert set(SCHEDULED_KINDS) | set(UNSCHEDULED_KINDS) == set(KNOWN_KINDS)
    assert not set(SCHEDULED_KINDS) & set(UNSCHEDULED_KINDS)
    assert set(SCHEDULED_KINDS) == {"macro", "unlock", "bnb_burn", "launchpool"}
    assert set(UNSCHEDULED_KINDS) == {"regulatory", "legal", "other"}
    assert set(CFG.bnb_event_kinds) <= set(SCHEDULED_KINDS)
    assert is_scheduled(" Macro ") and not is_scheduled("LEGAL")
    with pytest.raises(ValueError, match="kind"):
        is_scheduled("rumour")


@pytest.mark.parametrize("kind", SCHEDULED_KINDS)
def test_block_interval_scheduled_default_is_the_full_window(kind):
    ev = _ev(kind=kind)
    assert known_from(ev) == float("-inf")
    assert block_interval(ev, 2 * H) == (T - 2 * H, T + 2 * H)
    assert block_interval(ev, 24 * H) == (T - 24 * H, T + 24 * H)


@pytest.mark.parametrize("kind", UNSCHEDULED_KINDS)
def test_block_interval_unscheduled_default_starts_at_the_event(kind):
    ev = _ev(kind=kind)
    assert known_from(ev) == T
    assert block_interval(ev, 2 * H) == (T, T + 2 * H)


def test_block_interval_explicit_known_from():
    # A scheduled print announced only 30 min ahead: the pre-window shrinks to 30 min.
    assert block_interval(_ev(known_from_ts=T - 30 * M), 2 * H) == (T - 30 * M, T + 2 * H)
    # known_from before the window start never widens the window.
    assert block_interval(_ev(known_from_ts=T - 5 * H), 2 * H) == (T - 2 * H, T + 2 * H)
    # An unscheduled kind that was in fact announced gets (at most) the full window back.
    reg = _ev(kind="regulatory", known_from_ts=T - H)
    assert block_interval(reg, 2 * H) == (T - H, T + 2 * H)
    reg = _ev(kind="legal", known_from_ts=T - 9 * H)
    assert block_interval(reg, 2 * H) == (T - 2 * H, T + 2 * H)
    # Known only after its window had ended: an empty interval (start > end).
    start, end = block_interval(_ev(kind="other", known_from_ts=T + 3 * H), 2 * H)
    assert start > end


def test_block_interval_rejects_bad_windows_and_floors_fractional_ms():
    for bad in (-1, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="window_ms"):
            block_interval(_ev(), bad)
    assert block_interval(_ev(), 2 * H + 0.9) == (T - 2 * H, T + 2 * H)
    assert block_interval(_ev(), 0) == (T, T)


@pytest.mark.parametrize("kind", UNSCHEDULED_KINDS)
@pytest.mark.parametrize("scope", ["ALL", "BTC", "EXCHANGE:binance"])
def test_unscheduled_headline_after_the_decision_does_not_block(kind, scope):
    # The headline breaks 1h AFTER the entry decision: the live bot could not have known.
    cal = _cal(_ev(kind=kind, scope=scope, note="surprise"))
    dec = cal.check("BTC/USDT", T - H)
    assert dec.allowed, dec.reason
    assert dec.rule == RULE
    assert cal.check("BTC/USDT", T - 1).allowed
    assert cal.check("BTC/USDT", T - 2 * H).allowed


@pytest.mark.parametrize("kind", UNSCHEDULED_KINDS)
@pytest.mark.parametrize("scope", ["ALL", "BTC", "EXCHANGE:binance"])
def test_unscheduled_headline_before_the_decision_blocks_its_tail(kind, scope):
    # The same headline 1h BEFORE the decision: inside its +2h tail, so R5 blocks.
    cal = _cal(_ev(kind=kind, scope=scope, note="surprise"))
    dec = cal.check("BTC/USDT", T + H)
    assert not dec.allowed
    assert dec.rule == RULE
    assert f"unscheduled {kind} event" in dec.reason
    assert "blocks only from the moment it happened" in dec.reason
    assert "blocked 2024-03-12T12:30:00Z to 2024-03-12T14:30:00Z" in dec.reason
    for ts in (T, T + 1, T + 2 * H):
        assert not cal.check("BTC/USDT", ts).allowed, ts
    assert cal.check("BTC/USDT", T + 2 * H + 1).allowed


@pytest.mark.parametrize("kind", ["macro", "unlock"])
def test_scheduled_high_impact_still_blocks_before_the_print(kind):
    cal = _cal(_ev(kind=kind, scope="ALL", note="CPI"))
    dec = cal.check("ETH/USDT", T - H)  # 1h before the print
    assert not dec.allowed
    assert f"high-impact scheduled {kind} event" in dec.reason
    assert "unscheduled" not in dec.reason
    assert "scheduled, so known in advance" in dec.reason
    assert "blocked 2024-03-12T10:30:00Z to 2024-03-12T14:30:00Z" in dec.reason
    assert not cal.check("ETH/USDT", T - 2 * H).allowed
    assert cal.check("ETH/USDT", T - 2 * H - 1).allowed
    assert not cal.check("ETH/USDT", T + 2 * H).allowed


def test_explicit_known_from_narrows_a_scheduled_pre_window():
    cal = _cal(_ev(kind="macro", note="snap decision", known_from_ts=T - 30 * M))
    assert cal.check("BTC/USDT", T - H).allowed  # before it was announced
    assert cal.check("BTC/USDT", T - 30 * M - 1).allowed
    dec = cal.check("BTC/USDT", T - 30 * M)
    assert not dec.allowed
    assert "known from 2024-03-12T12:00:00Z" in dec.reason
    assert "scheduled macro event" in dec.reason
    assert not cal.check("BTC/USDT", T + 2 * H).allowed  # the tail is unchanged
    assert cal.check("BTC/USDT", T + 2 * H + 1).allowed


def test_late_known_event_never_blocks():
    cal = _cal(_ev(kind="regulatory", known_from_ts=T + 3 * H))
    for ts in (T - 2 * H, T, T + H, T + 2 * H, T + 3 * H):
        assert cal.check("BTC/USDT", ts).allowed, ts


@pytest.mark.parametrize("kind", ["bnb_burn", "launchpool"])
def test_bnb_24h_semantics_unchanged_and_explicit_known_from_narrows_them(kind):
    cal = _cal(_ev(scope="BNB", impact="low", kind=kind))
    for ts in (T - 24 * H, T - H, T + 24 * H):
        dec = cal.check("BNB/USDT", ts)
        assert not dec.allowed
        assert f"scheduled {kind} event" in dec.reason and "unscheduled" not in dec.reason
    assert cal.check("BNB/USDT", T - 24 * H - 1).allowed
    assert cal.check("BNB/USDT", T + 24 * H + 1).allowed
    announced = _cal(_ev(scope="BNB", impact="low", kind=kind, known_from_ts=T - 6 * H))
    assert announced.check("BNB/USDT", T - 6 * H - 1).allowed
    assert not announced.check("BNB/USDT", T - 6 * H).allowed
    assert not announced.check("BNB/USDT", T + 24 * H).allowed


def test_unscheduled_exchange_headline_on_bnb_uses_the_2h_news_window():
    cal = _cal(_ev(scope="EXCHANGE:binance", kind="regulatory"))
    assert cal.check("BNB/USDT", T - H).allowed
    dec = cal.check("BNB/USDT", T + 2 * H)
    assert not dec.allowed and "+/-2h news blackout" in dec.reason
    assert cal.check("BNB/USDT", T + 2 * H + 1).allowed


def test_calendar_keeps_known_from_through_normalization():
    cal = _cal(_ev(scope="all", impact="HIGH", kind="Macro", known_from_ts=float(T - H)))
    [ev] = cal.events
    assert ev.known_from_ts == T - H and isinstance(ev.known_from_ts, int)
    assert cal.check("BTC/USDT", T - H).allowed is False
    assert cal.check("BTC/USDT", T - H - 1).allowed


def test_check_matches_a_brute_force_oracle():
    # Random calendars and decision times: check() must block exactly when some applicable
    # event's block_interval contains ts (the bisect slice may never drop a blocking event).
    rng = random.Random(7)
    kinds = KNOWN_KINDS
    scopes = ("ALL", "BTC", "BNB", "ETH", "EXCHANGE:binance", "EXCHANGE:coinbase")
    for _ in range(40):
        events = []
        for _ in range(rng.randint(1, 12)):
            ts = T + rng.randint(-60, 60) * 30 * M
            kf = rng.choice([None, None, ts + rng.randint(-50, 10) * 30 * M])
            events.append(
                _ev(
                    ts=ts,
                    scope=rng.choice(scopes),
                    impact=rng.choice(("high", "medium", "low")),
                    kind=rng.choice(kinds),
                    known_from_ts=kf,
                )
            )
        cal = _cal(*events)
        for _ in range(30):
            ts = T + rng.randint(-70, 70) * 15 * M + rng.choice((0, 1, -1))
            for pair in PAIRS:
                base = pair.split("/")[0]
                want = False
                for ev in events:
                    windows = []
                    if ev.impact == "high" and ev.scope in ("ALL", base, "EXCHANGE:binance"):
                        windows.append(2 * H)
                    bnb_scope = ev.scope in ("ALL", "BNB", "EXCHANGE:binance")
                    if base == "BNB" and ev.kind in ("bnb_burn", "launchpool") and bnb_scope:
                        windows.append(24 * H)
                    for w in windows:
                        lo, hi = block_interval(ev, w)
                        want = want or lo <= ts <= hi
                assert cal.check(pair, ts).allowed is not want, (pair, ts, events)


# ------------------------------------------------------------------ C2: known_from_utc column
HEADER_KF = "time_utc,scope,impact,kind,note,known_from_utc\n"


def test_loader_reads_the_known_from_column(tmp_path):
    body = (
        f"{T},ALL,high,macro,iso,2024-03-12T12:00:00Z\n"
        f"{T},ALL,high,macro,ms,{T - H}\n"
        f"{T},ALL,high,regulatory,empty = kind default,\n"
        f"{T},ALL,high,macro,blank = kind default,   \n"
        f"{T},ALL,high,other,offset,2024-03-12T13:00:00+01:00\n"
    )
    events = load_events(_write(tmp_path, body, header=HEADER_KF))
    assert [e.known_from_ts for e in events] == [T - 30 * M, T - H, None, None, T - 30 * M]
    cal = NewsCalendar(events[2:3], CFG)  # the regulatory row: unscheduled default
    assert cal.check("BTC/USDT", T - H).allowed and not cal.check("BTC/USDT", T + H).allowed


def test_loader_header_without_known_from_is_still_accepted(tmp_path):
    [ev] = load_events(_write(tmp_path, f"{T},ALL,high,regulatory,old format\n"))
    assert ev.known_from_ts is None
    assert block_interval(ev, 2 * H) == (T, T + 2 * H)


@pytest.mark.parametrize(
    ("body", "line", "fragment"),
    [
        (f"{T},ALL,high,macro,ok,\n{T},ALL,high,macro,bad,soon\n", 3, "known_from_utc"),
        (f"{T},ALL,high,macro,naive,2024-03-12T12:00:00\n", 2, "offset"),
        (f"{T},ALL,high,macro,five columns only\n", 2, "columns"),
        (f"{T},ALL,high,macro,seven,{T},x\n", 2, "columns"),
    ],
)
def test_loader_known_from_errors_name_the_line(tmp_path, body, line, fragment):
    path = _write(tmp_path, body, header=HEADER_KF)
    with pytest.raises(ValueError, match=f"line {line}:") as info:
        load_events(path)
    assert fragment in str(info.value)


def test_loader_rejects_a_misplaced_or_unknown_extra_column(tmp_path):
    for header in (
        "time_utc,known_from_utc,scope,impact,kind,note\n",
        "time_utc,scope,impact,kind,note,known_from\n",
    ):
        path = _write(tmp_path, f"{T},ALL,high,macro,x,\n", header=header)
        with pytest.raises(ValueError, match=r"line 1:.*header"):
            load_events(path)
