"""Memory bank: every trade with its story, your notes, the totals and the CSV export."""

import csv
import io
import json
import threading
import urllib.error
import urllib.request

from research.trendbot import memory_bank as mb
from research.trendbot.config import StrategyConfig
from research.trendbot.journal import write_journal
from research.trendbot.models import Trade


H = 3_600_000
T0 = 1_760_000_000_000


def trade(i, pnl, pair="BTC/USDT", closed=True):
    t = Trade(
        i, pair, "base", T0 + i * 10 * H, T0 + i * 10 * H + 4 * H, 100.0, 95.0, 110.0, 1.0, 5.0, 1.0
    )
    if closed:
        t.exit_ts, t.exit_price = t.entry_ts + 8 * H, 100.0 + pnl
        t.exit_reason, t.pnl, t.r_multiple, t.fees = (
            ("TP" if pnl > 0 else "SL"),
            pnl,
            pnl / 5.0,
            0.2,
        )
    return t


def make_bot(d, mode="paper"):
    d.mkdir(parents=True)
    write_journal(
        [trade(1, 10.0), trade(2, -5.0, "ETH/USDT"), trade(3, 0.0, closed=False)], d / "journal.csv"
    )
    (d / "state.json").write_text(json.dumps({"mode": mode, "exchange": "binance"}))
    ledger = [
        {
            "trade_id": 1,
            "why_triggered": ["R1 trend: price above EMA200", "R3 volume 2.1x"],
            "expected_outcome": {"n": 100, "win_rate": 0.55},
            "lesson": "a clean trend entry",
        },
        {"trade_id": 2, "why_triggered": ["R1 trend"], "lesson": "entered into resistance"},
    ]
    (d / "ledger.json").write_text(json.dumps(ledger))
    refl = {
        "kind": "reflection",
        "trade_id": 2,
        "verdict": "lost",
        "mistakes": ["RSI was overheated"],
        "predicted": {"n": 100, "wins": 48, "avg_r": -0.1},
        "lesson": "watch for: RSI was overheated",
    }
    (d / "chantisimo_thoughts.jsonl").write_text(json.dumps(refl) + "\n")
    tickets = d / "wallet" / "tickets"
    tickets.mkdir(parents=True)
    tx = {
        "status": "filled",
        "pair": "BTC/USDT",
        "side": "buy",
        "tx": "sig123",
        "chain": "solana",
        "provider": "phantom",
        "updated_ms": T0 + 10 * H + 4 * H + 60_000,
    }
    (tickets / "a.json").write_text(json.dumps(tx))
    (tickets / "test.json").write_text(json.dumps({**tx, "dry_run": True, "tx": "nope"}))


def test_the_bank_collects_every_trade_with_its_story(tmp_path):
    make_bot(tmp_path / "a")
    make_bot(tmp_path / "b", mode="wallet")
    memes = tmp_path / "a" / "memes"
    memes.mkdir()
    (memes / "positions.json").write_text(
        json.dumps(
            {
                "solana:Mint1": {
                    "chain": "solana",
                    "address": "Mint1",
                    "symbol": "PEPE",
                    "qty": 0.0,
                    "cost_usd": 0.0,
                    "realized_usd": 12.5,
                    "opened_ms": T0,
                    "trades": [
                        {"ts": T0, "side": "buy", "qty": 5e4, "usd": 50, "tx": "s1"},
                        {"ts": T0 + H, "side": "sell", "qty": 5e4, "usd": 62.5, "tx": "s2"},
                    ],
                }
            }
        )
    )
    out = mb.bank(
        {"alpha": (tmp_path / "a", None), "beta": (tmp_path / "b", {"mode": "wallet"})},
        tmp_path / "a",
    )
    recs = {r["id"]: r for r in out["records"]}
    assert len(recs) == 7  # 3 + 3 bot trades + 1 meme position
    win = recs["alpha:1"]
    assert (
        win["outcome"] == "win"
        and win["why"][0].startswith("R1 trend")
        and win["lesson"] == "a clean trend entry"
    )
    assert win["txs"] == [
        {"tx": "sig123", "chain": "solana", "provider": "phantom"}
    ]  # the dry run is not a trade
    loss = recs["alpha:2"]
    assert loss["brain"]["mistakes"] == ["RSI was overheated"] and loss["held_hours"] == 8.0
    assert recs["alpha:3"]["outcome"] == "open" and recs["beta:1"]["mode"] == "wallet"
    meme = recs["memes:solana:Mint1"]
    assert meme["outcome"] == "win" and meme["pnl"] == 12.5 and meme["pair"] == "PEPE/USD"
    s = out["summary"]
    assert s["trades"] == 7 and s["closed"] == 5 and s["wins"] == 3 and s["pnl"] == 22.5
    assert s["mistakes"] == [("RSI was overheated", 2)] and s["curve"][-1]["pnl"] == 22.5
    assert s["by"]["pair:ETH/USDT"]["win_rate"] == 0.0


def test_your_notes_tags_and_stars_are_kept(tmp_path):
    make_bot(tmp_path / "a")
    mb.annotate(tmp_path, "alpha:2", note="chased it", tags=["FOMO", " news ", ""], star=True)
    mb.annotate(tmp_path, "alpha:2", star=False)  # other fields stay
    r = {x["id"]: x for x in mb.bank({"alpha": (tmp_path / "a", None)}, tmp_path)["records"]}[
        "alpha:2"
    ]
    assert r["note"] == "chased it" and r["tags"] == ["fomo", "news"] and r["star"] is False
    rows = list(csv.DictReader(io.StringIO(mb.to_csv([r]))))
    assert rows[0]["mistakes"] == "RSI was overheated" and rows[0]["tags"] == "fomo, news"


def test_the_memory_tab_is_served_and_saves_notes(tmp_path):
    from http.server import ThreadingHTTPServer

    from research.trendbot.dashboard import Controller, FleetRuntime, make_handler

    make_bot(tmp_path / "a")
    rt = FleetRuntime(
        None, {"alpha": (tmp_path / "a", StrategyConfig(), Controller(tmp_path / "a", None))}
    )
    srv = ThreadingHTTPServer(
        ("127.0.0.1", 0), make_handler(None, None, 15, token="tok", runtime=rt)
    )
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/memory") as r:
            page = r.read().decode()
            assert "Memory bank" in page and '"tok"' in page
            assert "connect-src 'self'" in r.headers["Content-Security-Policy"]
        with urllib.request.urlopen(base + "/api/memory") as r:
            assert len(json.loads(r.read())["records"]) == 3
        req = urllib.request.Request(
            base + "/api/control",
            headers={"X-Trendbot-Token": "tok"},
            data=json.dumps(
                {"action": "memory_note", "id": "alpha:1", "note": "textbook"}
            ).encode(),
        )
        with urllib.request.urlopen(req) as r:
            assert json.loads(r.read())["ok"]
        with urllib.request.urlopen(base + "/api/memory/export.csv") as r:
            assert "attachment" in r.headers["Content-Disposition"]
            text = r.read().decode("utf-8-sig")
        assert "textbook" in text and text.startswith("id,bot,mode")
    finally:
        srv.shutdown()
