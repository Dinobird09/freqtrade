"""Post-mortems: why a strategy worked, why its losses happened, why the best is best."""

from research.trendbot import postmortem as pm
from research.trendbot.models import Candle
from research.trendbot.strategies import LabTrade


DAY = 86_400_000


def _candles(n=400, px=100.0, drift=0.1):
    return [
        Candle(
            i * DAY,
            px + i * drift,
            px + i * drift + 2,
            px + i * drift - 2,
            px + i * drift + 0.5,
            100.0,
        )
        for i in range(n)
    ]


def _t(i, bars, r, entry=150.0, stop=145.0, exits=None, target=160.0):
    t = LabTrade(i, i + 1, entry, stop, target, 20.0, 100.0)
    t.exit_i, t.r, t.pnl = i + 1 + bars, r, r * 100.0
    t.exits = exits or [(t.exit_i, 20.0, entry + r * 5, "TP" if r > 0 else "SL")]
    return t


def test_trade_stories():
    cs = _candles()
    assert "in the entry bar" in pm.trade_story(_t(250, 0, -1.0), cs)
    assert "reached the target" in pm.trade_story(_t(250, 5, 2.0), cs)
    trailed = _t(250, 9, 3.1, exits=[(255, 10, 160, "TP"), (260, 10, 171, "TRAIL")])
    assert "trailed the trend" in pm.trade_story(trailed, cs)
    rising = _candles(drift=1.0)  # the trade was well in profit before it was stopped
    assert "in profit first" in pm.trade_story(
        _t(250, 8, -1.0, entry=rising[251].open, stop=rising[251].open - 2), rising
    )


def test_a_winning_strategy_is_explained():
    cs = _candles()
    trades = [_t(210 + k * 8, 5, 2.0) for k in range(12)] + [
        _t(214 + k * 8, 4, -1.0) for k in range(8)
    ]
    m = pm.analyse(trades, cs)
    assert m["win_rate"] == 0.6 and m["breakeven_win_rate"] == round(1 / 3, 4)
    ex = pm.explain(m)
    assert "60% of 20 trades won" in ex["worked"][0] and "clears that bar" in ex["worked"][0]


def test_a_losing_strategy_is_explained_with_fixes():
    cs = _candles()
    trades = [_t(210 + k * 6, 0 if k % 2 else 1, -1.0) for k in range(16)] + [
        _t(300 + k * 10, 5, 2.0) for k in range(4)
    ]
    ex = pm.explain(pm.analyse(trades, cs))
    assert "falls short" in ex["failed"][0]
    assert any("stopped within 2 bars" in x for x in ex["failed"])
    assert any("widen the stop" in x for x in ex["help"])


def test_why_best_compares_the_leaders():
    rows = [
        {
            "strategy": "a",
            "pair": "X",
            "oos": {"sharpe": 1.4, "trades": 20, "max_dd_pct": 3.0, "return_pct": 12},
            "analysis": {
                "win_rate": 0.57,
                "breakeven_win_rate": 0.35,
                "quick_stop_share": 0.1,
                "gave_back_share": 0.2,
            },
        },
        {
            "strategy": "b",
            "pair": "X",
            "oos": {"sharpe": 0.3, "trades": 20, "max_dd_pct": 9.0, "return_pct": 2},
            "analysis": {"win_rate": 0.3, "quick_stop_share": 0.5, "gave_back_share": 0.2},
        },
    ]
    lines = pm.why_best(rows)
    assert lines[0].startswith("a on X is the best") and "57%" in lines[1]
    assert "wins less often" in lines[2] and "stopped out right after entry" in lines[2]


def test_the_lab_explains_every_strategy_and_the_terminal_answers(tmp_path, monkeypatch):
    from research.trendbot import lab
    from research.trendbot.assistant import Terminal
    from research.trendbot.dashboard import FleetRuntime
    from research.trendbot.data import save_candles_csv
    from research.trendbot.synthetic import make_world

    monkeypatch.setenv("TRENDBOT_TRADERS_DIR", str(tmp_path))
    data, _ = make_world("planted", seed=3, years=3)
    save_candles_csv(data["ETH/USDT"], tmp_path / "candles" / "ETH_USDT-4h.csv")
    res = lab.run(tmp_path, n_mutations=2, stress_runs=2)
    assert res["why_best"] and all("explain" in r for r in res["leaderboard"] if r.get("oos"))
    md = (tmp_path / "lab" / "program.md").read_text()
    assert "## Why the best one is the best" in md and "**Why the losses happened**" in md
    t = Terminal(FleetRuntime(None, {"bot": (tmp_path, None, None)}))
    ans = t.handle("why did nnfx lose trades?")["text"]
    assert ans.startswith("nnfx on ETH/USDT") and "Why the losses happened" in ans
    assert "is the best" in t.handle("which strategy is the best?")["text"]
