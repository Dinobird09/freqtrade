"""Chantisimo, the brain: memory, recall, mistakes, graduation from paper, the live bot wiring."""

import dataclasses
import functools
import json

from research.trendbot.chantisimo import (
    QUICK_STOP,
    RULE_ID,
    BrainSettings,
    Chantisimo,
    Memory,
    RecallLayer,
    diagnose,
    load_teachers,
    mistake_book,
    pooled,
    read_thoughts,
    recall,
)
from research.trendbot.config import StrategyConfig
from research.trendbot.journal import read_journal, write_journal
from research.trendbot.layers import MarketView
from research.trendbot.learning import LearningBook, LearningSettings
from research.trendbot.models import HOUR_MS, SignalCheck, Trade

from .test_live_bot import FakeExchange, _bot, _settings, _start_ts, world  # noqa: F401


CFG = StrategyConfig()


def _t(tid, vol, r, pair="BTC/USDT", hold_h=24, hour=4.0):
    ts = 1_000_000_000_000 + tid * 40 * HOUR_MS  # 2001: before every synthetic world
    return Trade(
        tid, pair, "base", ts - 4 * HOUR_MS, ts, 100.0, 95.0, 110.0, 1.0, 5.0, 1.0, "pivot",
        ts + hold_h * HOUR_MS, 100 + 5 * r, "TP" if r > 0 else "SL", 0.0, 5 * r, r,
        {"vol_ratio": vol, "rsi": 60.0, "ema_gap_pct": 0.5, "dist_regime_pct": 3.0,
         "hour_utc": hour},
    )  # fmt: skip


def _paper_dir(tmp_path, trades, name="paper-bot"):
    d = tmp_path / name
    write_journal(trades, d / "journal.csv")
    (d / "state.json").write_text(json.dumps({"mode": "paper", "exchange": "binance"}))
    return d


def _mixed():
    # weak volume (1.5-1.6x) loses, strong volume (2.5x+) wins
    losers = [_t(i, 1.5 + i * 0.01, -1.0) for i in range(1, 11)]
    winners = [_t(i, 2.5 + i * 0.05, 2.0) for i in range(11, 21)]
    return losers + winners


def test_recall_finds_the_most_similar_trades():
    mem = [Memory("self", "paper", t) for t in _mixed()]
    weak = recall("BTC/USDT", {**_t(0, 1.52, 0).features}, mem, k=5)
    assert weak["n"] == 5 and weak["wins"] == 0 and weak["avg_r"] == -1.0
    strong = recall("BTC/USDT", {**_t(0, 2.9, 0).features}, mem, k=5)
    assert strong["wins"] == 5 and "Chantisimo recalls 5 similar trades" in strong["text"]
    assert recall("BTC/USDT", {}, mem)["n"] == 0


def test_diagnose_names_the_mistakes_behind_a_loss():
    winners = [t for t in _mixed() if t.r_multiple > 0]
    tags = diagnose(_t(99, 1.5, -1.0, hold_h=4), winners)
    assert "volume weaker than the winners'" in tags and QUICK_STOP in tags
    assert diagnose(_t(98, 3.0, 2.0), winners) == []  # a winner has no mistakes
    book = mistake_book([Memory("self", "paper", t) for t in _mixed()])
    top = book[0]
    assert top["mistake"] == "volume weaker than the winners'" and top["losses"] == 10


def test_teacher_memory_is_pooled_with_own_trades_first(tmp_path):
    d = _paper_dir(tmp_path, _mixed())
    teachers = load_teachers([d])
    assert len(teachers) == 20 and all(m.mode == "paper" for m in teachers)
    assert all(m.trade.trade_id > 1_000_000 for m in teachers)  # no clash with own ids
    own = [_t(1, 1.5, 2.0)]  # same signal as teacher trade 1: own record wins
    mem = pooled(own, teachers, "live")
    assert len(mem) == 20 and sum(m.source == "self" for m in mem) == 1


def test_rules_learned_from_paper_losses_apply_to_the_live_bot(tmp_path):
    many = [_t(i, 1.5 + i * 0.004, -1.0) for i in range(1, 26)]
    many += [_t(i, 2.5 + i * 0.02, 2.0) for i in range(26, 41)]
    teacher = _paper_dir(tmp_path, many)
    brain = Chantisimo(tmp_path / "live", BrainSettings(learn_from=[str(teacher)]), "live")
    brain.remember([])
    book = LearningBook(tmp_path / "live", CFG, LearningSettings(validate_on_history=False))
    (tmp_path / "live").mkdir()
    rules = book.relearn([], None, extra=brain.teacher_trades([]))
    active = [r for r in rules if r.status == "active"]
    assert any(r.feature == "vol_ratio" and r.op == "below" for r in active), rules
    state = brain.write(["BTC/USDT"], book.rules)
    assert state["memory"]["trades"] == 40 and state["memory"]["by_source"] == {
        "paper:paper-bot": 40
    }
    assert state["mistakes"][0]["status"].startswith("blocked by rule")
    assert "Chantisimo" in (tmp_path / "live" / "chantisimo.md").read_text()


def test_graduation_blocks_real_money_until_the_pair_is_proven_in_paper(tmp_path):
    good = [_t(i, 2.6, 2.0 if i % 2 else -1.0) for i in range(1, 25)]  # avg +0.5R, BTC only
    teacher = _paper_dir(tmp_path, good)
    s = BrainSettings(learn_from=[str(teacher)], min_paper_trades=20)
    live = Chantisimo(tmp_path / "live", s, "live")
    live.remember([])
    assert live.graduation("BTC/USDT")[0]
    ok, why = live.graduation("ETH/USDT")
    assert not ok and "0 of 20 paper trades" in why
    losing = _paper_dir(tmp_path, [_t(i, 1.5, -1.0) for i in range(1, 25)], "paper-2")
    bad = Chantisimo(tmp_path / "l2", BrainSettings(learn_from=[str(losing)]), "live")
    bad.remember([])
    assert "averaged -1.00R" in bad.graduation("BTC/USDT")[1]
    paper = Chantisimo(tmp_path / "p", BrainSettings(learn_from=[str(losing)]), "paper")
    paper.remember([])
    assert paper.graduation("BTC/USDT")[0]  # paper bots are never held back


@functools.cache
def _base_row():
    from research.trendbot.indicators import compute_features
    from research.trendbot.synthetic import make_world

    data, _ = make_world("null", seed=2, years=1)
    return next(r for r in compute_features(data["BTC/USDT"], CFG)[300:] if r.rsi is not None)


def _row(vol):
    return dataclasses.replace(
        _base_row(), vol_ratio=vol, rsi=60.0, ema_gap_pct=0.5, dist_regime_pct=3.0, hour_utc=4
    )


def test_brain_filter_logs_every_verdict_and_names_the_rule(tmp_path):
    teacher = _paper_dir(tmp_path, [_t(i, 2.6, 2.0) for i in range(1, 25)])
    brain = Chantisimo(tmp_path / "b", BrainSettings(learn_from=[str(teacher)]), "live")
    brain.remember([])

    class Inner:
        rule_id = "L_learned_rule"

        def __call__(self, pair, row, check):
            return False, None, "learned rule vetoed it"

    f = brain.filter(Inner())
    row = _row(2.6)
    ok, _, why = f("ETH/USDT", row, SignalCheck("ETH/USDT", row.ts, ()))
    assert not ok and f.rule_id == RULE_ID and "paper trades" in why
    ok, _, why = f("BTC/USDT", row, SignalCheck("BTC/USDT", row.ts, ()))
    assert not ok and f.rule_id == "L_learned_rule"
    thoughts = read_thoughts(tmp_path / "b", kind="thought")
    assert [t["verdict"] for t in thoughts] == ["SKIP", "SKIP"]
    assert thoughts[0]["recall"]["n"] == 24 and "recalls" in thoughts[0]["recall_text"]


def test_reflection_compares_the_recall_with_the_outcome(tmp_path):
    brain = Chantisimo(tmp_path, BrainSettings(), "paper")
    brain.memory = [Memory("self", "paper", t) for t in _mixed()]
    rec = brain.reflect(_t(50, 1.5, -1.0, hold_h=4), {"n": 12, "wins": 9, "avg_r": 1.2})
    assert rec["verdict"] == "lost" and QUICK_STOP in rec["mistakes"]
    assert "against what it recalled" in rec["lesson"]
    assert read_thoughts(tmp_path, kind="reflection")[0]["trade_id"] == 50


def test_recall_layer_vetoes_setups_whose_neighbours_lost():
    layer = RecallLayer(k=5)

    class Ctx:
        def examples(self):
            return [(t.pair, t.signal_ts, t.features, t.r_multiple) for t in _mixed()]

    layer.fit(Ctx())
    view = MarketView({}, CFG.timeframe_ms)
    assert layer.veto("BTC/USDT", _row(1.52), view)[0]
    assert not layer.veto("BTC/USDT", _row(2.9), view)[0]
    clone = RecallLayer()
    clone.load_state(json.loads(json.dumps(layer.state())))
    assert clone.veto("BTC/USDT", _row(1.52), view) == layer.veto("BTC/USDT", _row(1.52), view)


def test_live_bot_with_a_paper_teacher_only_trades_graduated_pairs(tmp_path, world):  # noqa: F811
    data, _ = world
    teacher = _paper_dir(tmp_path, [_t(i, 2.6, 2.0 if i % 3 else -1.0) for i in range(1, 8)])
    s = _settings(
        tmp_path,
        brain={"learn_from": [str(teacher)], "min_paper_trades": 5},
        layers={"auto_jobs": False},
    )
    ex = FakeExchange(data, _start_ts(data))
    bot = _bot(s, ex)
    bot.start()
    end = data["BTC/USDT"][-2].ts
    while ex.now < end:
        bot.step()
        ex.advance(bot.s.poll_seconds)
    trades = read_journal(bot.journal_path)
    assert trades and {t.pair for t in trades} == {"BTC/USDT"}
    thoughts = read_thoughts(bot.dir, 5000, "thought")
    skipped = [t for t in thoughts if t["verdict"] == "SKIP" and "paper trades" in t["reason"]]
    assert skipped and {t["pair"] for t in skipped} <= {"ETH/USDT", "BNB/USDT"}
    assert read_thoughts(bot.dir, 5000, "reflection")
    state = json.loads((bot.dir / "chantisimo.json").read_text())
    assert state["memory"]["teachers"] == ["paper-bot"]
    ledger = json.loads((bot.dir / "ledger.json").read_text())
    assert any("Chantisimo recalls" in w for w in ledger[0]["why_triggered"])


def test_memory_is_causal(tmp_path):
    trades = [_t(i, 2.6, 2.0) for i in range(1, 25)]
    teacher = _paper_dir(tmp_path, trades)
    brain = Chantisimo(
        tmp_path / "b", BrainSettings(learn_from=[str(teacher)], min_paper_trades=20), "live"
    )
    brain.remember([])
    early = trades[5].exit_ts + 4 * HOUR_MS  # only 6 teacher trades had closed then
    ok, why = brain.graduation("BTC/USDT", early)
    assert not ok and "6 of 20" in why
    assert brain.graduation("BTC/USDT", trades[-1].exit_ts + 4 * HOUR_MS)[0]


def test_recall_reaches_100_neighbours_with_backtest_memory(tmp_path):
    from research.trendbot.chantisimo import load_backtest_memory, write_backtest_memory
    from research.trendbot.models import CandidateOutcome

    cands = [
        CandidateOutcome(
            "BTC/USDT", t.signal_ts, t.entry_ts, t.exit_ts, t.exit_reason, t.r_multiple, t.features
        )
        for t in [_t(i, 1.5 + (i % 50) * 0.03, 2.0 if i % 3 == 0 else -1.0) for i in range(1, 301)]
    ]
    assert write_backtest_memory(tmp_path, cands) == 300
    assert len(load_backtest_memory(tmp_path)) == 300
    brain = Chantisimo(tmp_path, BrainSettings(), "paper")
    brain.remember([_t(900, 2.6, 2.0)])
    assert len(brain.recall_pool()) == 301 and brain.s.recall_k == 100
    mem = recall("BTC/USDT", _t(0, 2.0, 0).features, brain.recall_pool(), brain.s.recall_k)
    assert mem["n"] == 100 and set(mem["by_source"]) <= {"own", "backtest"}
    assert "recalls 100 similar trades" in mem["text"]
    off = Chantisimo(tmp_path, BrainSettings(use_backtest=False), "paper")
    off.remember([])
    assert off.recall_pool() == []
