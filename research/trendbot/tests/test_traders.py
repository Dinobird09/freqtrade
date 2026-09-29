"""Smart money: other traders' histories, ranking, copy signals, wallets, memory."""

import json

import pytest

from research.trendbot import traders as tr
from research.trendbot.config import StrategyConfig
from research.trendbot.synthetic import make_world

from .test_live_bot import world  # noqa: F401


H = 3_600_000
T0 = 1_700_000_000_000


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("TRENDBOT_TRADERS_DIR", str(tmp_path))


def _export(tmp_path, n_win=40, n_loss=10, start=T0, pair="BTCUSDT", hold_h=30, name="alice.csv"):
    """A Binance 'Trade History' export: Date(UTC),Pair,Side,Price,Executed,Amount,Fee."""
    rows = ["Date(UTC),Pair,Side,Price,Executed,Amount,Fee"]
    ts = start
    for i in range(n_win + n_loss):
        win = i % 5 != 4 if n_loss else True
        buy, sell = 100.0, (104.0 if win else 97.0)
        for side, px, t in (("BUY", buy, ts), ("SELL", sell, ts + hold_h * H)):
            d = tr.datetime.fromtimestamp(t / 1000, tr.UTC).strftime("%Y-%m-%d %H:%M:%S")
            rows.append(f"{d},{pair},{side},{px},0.5{pair[:3]},{px * 0.5}USDT,0.01USDT")
        ts += (hold_h + 10) * H
    p = tmp_path / name
    p.write_text("\n".join(rows) + "\n")
    return p


def test_binance_export_becomes_round_trips_and_stats(tmp_path):
    fills, _ = tr.parse_history(_export(tmp_path).read_text())
    assert len(fills) == 100 and fills[0].pair == "BTC/USDT" and fills[0].qty == 0.5
    trips = tr.round_trips(fills)
    assert len(trips) == 50 and all(not t.is_open for t in trips)
    assert trips[0].ret_pct == pytest.approx((0.5 * 104 - 0.01 - 0.5 * 100 - 0.01) / 50.01 * 100)
    st = tr.trader_stats(trips, tr.DEFAULT_RULES)
    assert st["trades"] == 50 and st["win_rate"] == 0.8 and st["style"] == "swing"
    assert 0.66 < st["win_rate_lb"] < 0.8 and st["profit_factor"] > 3 and st["qualified"]


def test_small_samples_and_scalpers_do_not_qualify(tmp_path):
    lucky = tr.trader_stats(
        tr.round_trips(tr.parse_history(_export(tmp_path, 3, 0).read_text())[0]), tr.DEFAULT_RULES
    )
    assert lucky["win_rate"] == 1.0 and lucky["win_rate_lb"] < 0.45 and not lucky["qualified"]
    assert "3 of 30 trades" in lucky["why"]
    fast = tr.trader_stats(
        tr.round_trips(
            tr.parse_history(_export(tmp_path, 40, 10, hold_h=0.25, name="s.csv").read_text())[0]
        ),
        tr.DEFAULT_RULES,
    )
    assert fast["style"] == "scalper" and "can't be followed" in fast["why"]
    assert tr.wilson_lower(0, 0) == 0 and tr.wilson_lower(50, 100) == pytest.approx(0.404, abs=0.01)


def test_partial_fills_and_open_positions():
    fills = [
        tr.Fill(T0, "ETH/USDT", "buy", 100.0, 1.0),
        tr.Fill(T0 + H, "ETH/USDT", "buy", 110.0, 1.0),
        tr.Fill(T0 + 2 * H, "ETH/USDT", "sell", 120.0, 1.5),
        tr.Fill(T0 + 3 * H, "ETH/USDT", "sell", 120.0, 0.5),
        tr.Fill(T0 + 4 * H, "ETH/USDT", "sell", 120.0, 1.0),  # nothing held: ignored
        tr.Fill(T0 + 5 * H, "SOL/USDT", "buy", 10.0, 3.0),
    ]
    trips = tr.round_trips(fills)
    eth, sol = trips
    assert eth.entry_price == 105.0 and eth.pnl == pytest.approx(30.0) and not eth.is_open
    assert sol.is_open and sol.entry_price == 10.0
    assert tr.norm_pair("btc-usdt") == "BTC/USDT" and tr.norm_pair("ETHBTC") == "ETH/BTC"
    assert (
        tr.parse_time("2024-01-02 03:04:05") == 1704164645000
        and tr.parse_time(1704164645) == 1704164645000
    )


def test_follow_and_copy_signals(tmp_path):
    p = _export(tmp_path)
    with p.open("a") as fh:  # alice is holding SOL right now
        fh.write("2026-09-29 10:00:00,SOLUSDT,BUY,150,2SOL,300USDT,0.1USDT\n")
    now = tr.parse_time("2026-09-29 12:00:00")
    assert "added" in tr.add_trader("alice", "file", str(p))
    st = tr.refresh(now_ms=now)
    assert st["traders"][0]["qualified"] and st["copy_signals"] == []  # not followed yet
    tr.set_follow("alice", True)
    st = tr.refresh(now_ms=now)
    sig = st["copy_signals"][0]
    assert sig["pair"] == "SOL/USDT" and sig["trader"] == "alice"
    assert tr.copy_signals_for(["SOL/USDT"], set()) == [sig]
    assert tr.copy_signals_for(["SOL/USDT"], {sig["id"]}) == []
    assert tr.refresh(now_ms=now + 13 * H)["copy_signals"] == []  # older than the copy window
    with pytest.raises(tr.TraderError):
        tr.add_trader("Bad Name", "file", str(p))
    with pytest.raises(tr.TraderError):
        tr.add_trader("x", "url", "http://insecure")
    tr.remove_trader("alice")
    assert tr.load_registry()["follow"] == []


def test_solana_wallet_swaps_become_fills(tmp_path):
    owner, mint = (
        "Wa11et1111111111111111111111111111111111111",
        "M1nt111111111111111111111111111111111111111",
    )

    def tx(ts, token_delta, sol_delta):
        return {
            "blockTime": ts // 1000,
            "meta": {
                "err": None,
                "preBalances": [10_000_000_000],
                "postBalances": [int(10e9 + sol_delta * 1e9)],
                "preTokenBalances": [
                    {"owner": owner, "mint": mint, "uiTokenAmount": {"uiAmount": 1000.0}}
                ],
                "postTokenBalances": [
                    {
                        "owner": owner,
                        "mint": mint,
                        "uiTokenAmount": {"uiAmount": 1000.0 + token_delta},
                    }
                ],
            },
            "transaction": {"message": {"accountKeys": [{"pubkey": owner}]}},
        }

    txs = {"s1": tx(T0, 500, -1.0), "s2": tx(T0 + H, -500, 1.5), "s3": {"meta": {"err": "x"}}}
    calls = []

    def fetch(url, body):
        req = json.loads(body)
        calls.append(req["method"])
        if req["method"] == "getSignaturesForAddress":
            return [{"signature": s} for s in txs]
        return txs[req["params"][0]]

    fills = tr.solana_fills(owner, tmp_path / "w.json", fetch=fetch)
    assert [f.side for f in fills] == ["buy", "sell"] and fills[0].pair == f"{mint}/SOL"
    trip = tr.round_trips(fills)[0]
    assert trip.ret_pct == pytest.approx(50.0)
    n = calls.count("getTransaction")
    tr.solana_fills(owner, tmp_path / "w.json", fetch=fetch)
    assert calls.count("getTransaction") == n  # cached signatures are not fetched again


def test_trader_trades_join_chantisimo_memory_and_mistakes(tmp_path):
    cfg = StrategyConfig()
    data, _ = make_world("null", seed=4, years=1)
    btc = data["BTC/USDT"]
    start = btc[400].ts
    p = _export(tmp_path, 30, 20, start=start, hold_h=30)
    tr.add_trader("bob", "file", str(p))
    tr.refresh()
    mem = tr.trader_memory(data, cfg)
    assert len(mem) == 50 and {m.source for m in mem} == {"trader:bob"}
    m = mem[0]
    assert m.trade.features and m.trade.signal_ts + cfg.timeframe_ms <= m.trade.entry_ts
    losers = [x.trade.r_multiple for x in mem if x.trade.r_multiple < 0]
    assert losers and max(abs(r) for r in losers) == pytest.approx(1.0, rel=0.05)  # their loss = 1R
    from research.trendbot.chantisimo import BrainSettings, Chantisimo

    brain = Chantisimo(tmp_path / "bot", BrainSettings(use_backtest=False), "paper")
    brain.remember([], candles=data, cfg=cfg)
    assert len(brain.recall_pool()) == 50
    state = brain.write(["BTC/USDT"])
    assert state["memory"]["traders"] == ["bob"] and state["memory"]["trades"] == 50


def test_bot_arms_a_pair_when_a_followed_trader_buys(tmp_path, world):  # noqa: F811
    from research.trendbot.live_bot import armed_pairs

    from .test_live_bot import FakeExchange, _bot, _settings, _start_ts

    data, _ = world
    ex = FakeExchange(data, _start_ts(data))
    bot = _bot(_settings(tmp_path, layers={"auto_jobs": False}), ex)
    bot.start()
    status = {
        "traders": [],
        "copy_signals": [
            {
                "trader": "alice",
                "pair": "ETH/USDT",
                "entry_ts": ex.now,
                "entry_price": 1.0,
                "id": "a:1",
            },
            {
                "trader": "alice",
                "pair": "DOGE/USDT",
                "entry_ts": ex.now,
                "entry_price": 1.0,
                "id": "a:2",
            },
        ],
    }
    tr.status_path().write_text(json.dumps(status))
    bot.copy_trades()
    assert list(armed_pairs(bot.dir, ex.now)) == ["ETH/USDT"]  # DOGE is not one of its pairs
    assert bot.state["copied"] == ["a:1"]
    from research.trendbot.live_bot import disarm_pair

    disarm_pair(bot.dir, "ETH/USDT")
    tr.status_path().write_text(json.dumps(status) + " ")  # rewritten: the same signal again
    bot.copy_trades()
    assert armed_pairs(bot.dir, ex.now) == {}  # each signal is acted on once


def test_terminal_lists_traders_and_follows(tmp_path):
    from research.trendbot.assistant import Terminal
    from research.trendbot.dashboard import FleetRuntime

    tr.add_trader("alice", "file", str(_export(tmp_path)))
    tr.refresh()
    t = Terminal(FleetRuntime(None, {"bot": (tmp_path / "s", None, None)}))
    r = t.handle("show me the leaderboard")
    assert r["table"]["rows"][0][1] == "alice" and "1 qualified" in r["text"]
    assert "following alice" in t.handle("follow alice")["text"]
    assert tr.load_registry()["follow"] == ["alice"]
    assert "no longer following" in t.handle("unfollow alice")["text"]
