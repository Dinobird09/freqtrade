"""TradingAgents connected to Chantisimo: runs, verdicts, the brain's gate and its score."""

import json
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from research.trendbot import agents_link as al
from research.trendbot.chantisimo import BrainSettings, Chantisimo, _agents_verdict, agents_gate
from research.trendbot.models import SignalCheck

from .test_chantisimo import _row, _t


H = 3_600_000


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("TRENDBOT_CONNECTIONS_FILE", str(tmp_path / "connections.json"))
    monkeypatch.setenv("TRENDBOT_ENV_FILE", str(tmp_path / ".env"))
    monkeypatch.setattr("research.trendbot.live_exchange.load_env_file", lambda *a, **k: None)
    return tmp_path


def fake_runner(signal="Overweight", decision="**Rating**: Overweight\nBuy the dip, size small."):
    seen = []

    def runner(cfg, args, base):
        seen.append(args)
        if signal is None:
            return {"ok": False, "error": "RateLimitError: slow down"}
        return {
            "ok": True,
            "signal": signal,
            "final_trade_decision": decision,
            "trader_investment_plan": "enter on a pullback",
            "report_html": None,
        }

    runner.seen = seen
    return runner


def record(home, coin, rating, finished_ms, ok=True):
    al._append(
        None,
        {
            "id": f"{coin}-{finished_ms}",
            "coin": coin,
            "ticker": f"{coin}-USD",
            "date": time.strftime("%Y-%m-%d", time.gmtime(finished_ms / 1000)),
            "ok": ok,
            "rating": rating,
            "bias": al.bias(rating),
            "finished_ms": finished_ms,
            "summary": f"{rating} on {coin}",
        },
    )


def test_names_and_ratings():
    assert al.ticker_for("BTC/USDT") == "BTC-USD" and al.ticker_for("sol") == "SOL-USD"
    assert al.coin_of("ETH-USD") == "ETH" and al.coin_of("eth/btc") == "ETH"
    with pytest.raises(al.AgentsError):
        al.ticker_for("BTC; rm -rf")
    assert [al.bias(r) for r in ("Buy", "Overweight", "Hold", "Underweight", "Sell", None)] == [
        "bullish",
        "bullish",
        "neutral",
        "bearish",
        "bearish",
        "unknown",
    ]
    assert al._rating("SELL", "") == "Sell"
    assert al._rating("REVIEW", "- **Final Rating**: underweight") == "Underweight"
    assert al._rating("REVIEW", "no idea") is None


def test_settings_validation(home):
    cfg = al.configure(None, provider="anthropic", debate_rounds=2, analysts=["market", "nope"])
    assert cfg["provider"] == "anthropic" and cfg["analysts"] == ["market"]
    assert (home / "tradingagents.json").exists()
    for bad in (
        {"provider": "skynet"},
        {"debate_rounds": 9},
        {"python": "C:/x/notepad.exe"},
        {"bogus": 1},
    ):
        with pytest.raises(al.AgentsError):
            al.configure(None, **bad)


def test_api_key_goes_to_env_file(home):
    assert al.set_api_key("openai", "sk-test-123") == "OPENAI_API_KEY saved in .env"
    assert "OPENAI_API_KEY=sk-test-123" in (home / ".env").read_text()
    with pytest.raises(al.AgentsError, match="needs no API key"):
        al.set_api_key("ollama", "x")


def test_not_ready_is_explained(home, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    rec = al.run("BTC/USDT")
    assert not rec["ok"] and "not set up" in rec["error"]
    al.configure(None, python=str(home / "python.exe"))
    assert "not found" in al.run("BTC/USDT")["error"]
    (home / "python.exe").write_text("")
    assert "no API key for openai" in al.run("BTC/USDT")["error"]
    st = al.status()
    assert st["python_found"] and st["key_env"] == "OPENAI_API_KEY" and not st["key_set"]
    assert [r["ok"] for r in st["results"]] == [False, False, False]


def test_run_saves_the_verdict(home):
    r = fake_runner()
    rec = al.run("btc/usdt", "2026-10-30", runner=r)
    assert rec["ok"] and rec["rating"] == "Overweight" and rec["bias"] == "bullish"
    assert rec["summary"].startswith("**Rating**: Overweight") and rec["coin"] == "BTC"
    a = r.seen[0]
    assert a["ticker"] == "BTC-USD" and a["date"] == "2026-10-30"
    assert a["analysts"] == ["market", "social", "news"]
    assert a["config"]["llm_provider"] == "openai" and a["config"]["max_debate_rounds"] == 1
    assert al.results()[0]["id"] == rec["id"]
    review = al.run("ETH", runner=fake_runner(signal="REVIEW", decision="unclear"))
    assert not review["ok"] and "REVIEW" in review["error"]
    failed = al.run("SOL", runner=fake_runner(signal=None))
    assert not failed["ok"] and "RateLimitError" in failed["error"]


def test_latest_is_point_in_time(home):
    now = 1_800_000_000_000
    record(home, "BTC", "Sell", now - 50 * H)  # too old
    record(home, "BTC", "Buy", now - 2 * H)
    record(home, "BTC", "Underweight", now + H)  # after the decision: unknown then
    record(home, "BTC", None, now - H, ok=False)
    v = al.latest("BTC/USDT", now)
    assert v["rating"] == "Buy"
    assert al.latest("BTC", now - 10 * H) is None  # the Sell is 40 h old by then
    assert al.latest("BTC", now - 10 * H, max_age_hours=48)["rating"] == "Sell"
    assert al.latest("ETH", now) is None


def test_gate_modes(home):
    now = 1_800_000_000_000
    record(home, "ETH", "Sell", now - H)
    record(home, "BTC", "Hold", now - H)
    assert al.gate("ETH/USDT", now, "off") == (True, "", None)
    ok, why, v = al.gate("ETH/USDT", now, "inform")
    assert ok and "rated ETH Sell" in why and v["rating"] == "Sell"
    ok, why, _ = al.gate("ETH/USDT", now, "veto")
    assert not ok and "no long against the agents" in why
    assert al.gate("BTC/USDT", now, "veto")[0]
    assert al.gate("SOL/USDT", now, "veto") == (True, "", None)
    with pytest.raises(ValueError):
        BrainSettings(agents="always")


def test_chantisimo_veto_thought_and_reflection(home, tmp_path):
    row = _row(2.0)
    record(home, "BTC", "Underweight", row.close_ts - H)
    brain = Chantisimo(tmp_path / "b", BrainSettings(agents="veto"), "paper")
    brain.remember([])
    f = brain.filter(None)
    ok, _, why = f("BTC/USDT", row, SignalCheck("BTC/USDT", row.ts, ()))
    assert not ok and "TradingAgents rated BTC Underweight" in why and f.rule_id == "TA_agents"
    assert brain.pending["BTC/USDT"]["agents"]["rating"] == "Underweight"
    thought = [
        json.loads(x)
        for x in (tmp_path / "b" / "chantisimo_thoughts.jsonl").read_text().splitlines()
    ][-1]
    assert thought["verdict"] == "SKIP" and "Underweight" in thought["agents"]

    inform = Chantisimo(tmp_path / "c", BrainSettings(agents="inform"), "paper")
    inform.remember([])
    ok, _, _ = inform.filter(None)("BTC/USDT", row, SignalCheck("BTC/USDT", row.ts, ()))
    assert ok  # inform never blocks
    pred = {k: v for k, v in inform.pending["BTC/USDT"].items() if k != "similar"}
    loss = inform.reflect(_t(1, 2.0, -1.0), pred)
    assert loss["agents"]["right"] is True and "Underweight" in loss["lesson"]
    win = inform.reflect(_t(2, 2.0, 1.5), pred)
    assert win["agents"]["right"] is False and win["lesson"].endswith(": wrong")
    score = inform.agents_score()
    assert score["trades"] == 2 and score["right"] == 1 and score["hit_rate"] == 0.5
    assert inform.write(["BTC/USDT"])["agents"]["mode"] == "inform"


def test_agents_verdict_without_a_call():
    assert _agents_verdict(None, 1.0) is None
    v = _agents_verdict({"rating": "Hold", "bias": "neutral", "date": "d"}, 1.0)
    assert v["right"] is None and v["text"].endswith(": no call")


def test_off_skips_everything(home):
    record(home, "BTC", "Sell", int(time.time() * 1000) - H)
    assert agents_gate("BTC/USDT", int(time.time() * 1000), BrainSettings(agents="off")) == (
        True,
        "",
        None,
    )


def test_lab_bot_respects_the_veto(home):
    from research.trendbot.lab_bot import LabBot

    now = int(time.time() * 1000)
    record(home, "SOL", "Sell", now - H)
    fake = SimpleNamespace(s=SimpleNamespace(brain={"agents": "veto"}), pair="SOL/USDT")
    assert "no long against the agents" in LabBot.agents_blocked(fake, now)
    fake.s.brain = {}
    assert LabBot.agents_blocked(fake, now) is None  # default "inform": records only


def test_daily_run_once_per_coin(home, monkeypatch):
    started = []
    monkeypatch.setattr(al, "start", lambda coins, base=None: started.append(list(coins)) or "ok")
    assert al.maybe_auto(["BTC/USDT"]) is None  # off by default
    al.configure(None, auto_daily=True, auto_hour_utc=0)
    assert al.maybe_auto(["BTC/USDT", "ETH/USDT"]) == "ok"
    assert al.maybe_auto(["BTC/USDT"]) is None  # done today
    assert al.maybe_auto(["BTC/USDT", "SOL/USDT"]) == "ok"
    assert started == [["BTC", "ETH"], ["SOL"]]


def test_start_needs_setup_and_skips_running_coins(home, monkeypatch):
    with pytest.raises(al.AgentsError, match="not set up"):
        al.start(["BTC"])
    py = home / "python"
    py.write_text("")
    al.configure(None, provider="ollama", python=str(py))
    popen = []

    class P:
        pid = 4242

    monkeypatch.setattr(al.subprocess, "Popen", lambda cmd, **k: popen.append(cmd) or P())
    msg = al.start(["BTC/USDT", "eth"])
    assert "started on BTC, ETH" in msg and popen[0][-2:] == ["BTC/USD", "ETH/USD"]
    monkeypatch.setattr(al, "_alive", lambda pid: pid == 4242)
    assert al.start(["BTC"]) == "already running for BTC"
    assert [j["coin"] for j in al.status()["running"]] == ["BTC", "ETH"]


def test_real_subprocess_with_a_stand_in_tradingagents(home, tmp_path):
    """The runner inside a separate python (a venv holding a stand-in TradingAgents)."""
    venv = tmp_path / "ta-venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True)
    site = next(venv.glob("lib/python*/site-packages"))
    pkg = site / "tradingagents"
    (pkg / "graph").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "graph" / "__init__.py").write_text("")
    (pkg / "default_config.py").write_text("DEFAULT_CONFIG = {'llm_provider': 'openai'}\n")
    (pkg / "graph" / "trading_graph.py").write_text(
        "import os\n"
        "class TradingAgentsGraph:\n"
        "    def __init__(self, selected_analysts, debug, config):\n"
        "        assert 'fundamentals' not in selected_analysts\n"
        "        self.config = config\n"
        "    def propagate(self, ticker, date, asset_type='stock'):\n"
        "        assert asset_type == 'crypto' and ticker == 'BTC-USD'\n"
        "        print('noisy library output')\n"
        "        return {'final_trade_decision': 'Rating: Sell. Momentum is fading.',\n"
        "                'trader_investment_plan': 'trim'}, 'Sell'\n"
        "    def save_reports(self, state, ticker, save_path):\n"
        "        os.makedirs(save_path, exist_ok=True)\n"
        "        p = os.path.join(save_path, 'complete_report.html')\n"
        "        open(p, 'w').write('<h1>report</h1><script>alert(1)</script>')\n"
        "        return save_path\n"
    )
    py = venv / "bin" / "python"
    al.configure(None, python=str(py), provider="ollama")
    rec = al.run("BTC/USDT", "2026-10-31")
    assert rec["ok"], rec
    assert rec["rating"] == "Sell" and rec["bias"] == "bearish"
    assert rec["report_html"].endswith("complete_report.html")
    log = next((home / "tradingagents" / "logs").glob("BTC-USD-*.log")).read_text()
    assert "noisy library output" in log

    (pkg / "graph" / "trading_graph.py").write_text("raise ImportError('langgraph missing')\n")
    bad = al.run("BTC/USDT")
    assert not bad["ok"] and "langgraph missing" in bad["error"]


def test_cli_status_and_run(home, capsys, monkeypatch):
    monkeypatch.setattr(
        al,
        "run",
        lambda pair, date=None, base=None: {
            "ok": True,
            "coin": "BTC",
            "rating": "Buy",
            "bias": "bullish",
            "summary": "strong",
        },
    )
    assert al.main(["run", "BTC/USDT"]) == 0
    assert "BTC: Buy (bullish): strong" in capsys.readouterr().out
    assert al.main(["status"]) == 0
    assert "python: not set" in capsys.readouterr().out
