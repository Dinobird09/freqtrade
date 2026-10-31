"""TradingAgents, the multi-agent AI research team, connected to Chantisimo.

TradingAgents (https://github.com/Dinobird09/tradingagents, Apache-2.0) runs a team of AI
agents on one coin: market, sentiment and news analysts work in parallel, a bull and a bear
researcher debate their reports, a trader proposes a trade, and the risk team and portfolio
manager settle on a five-step rating: Buy, Overweight, Hold, Underweight or Sell.

It needs heavy libraries (LangGraph, LangChain, yfinance) and an AI provider's API key, so it
lives in its own Python environment and this module starts it as a separate process. The
bot itself stays light. One run takes a few minutes and costs a few AI calls, so runs happen
when you ask (dashboard, terminal ``agents BTC``, ``trendbot.pyz agents run BTC/USDT``) or
once a day if you switch that on.

What Chantisimo does with a verdict (``"brain": {"agents": ...}`` in a bot's settings):

* ``"off"``: nothing; the verdicts are only shown.
* ``"inform"`` (default): each entry's thought and each trade's reflection record what the
  agents said, and the brain keeps score of how often they were right.
* ``"veto"``: as inform, and a long entry is skipped while the latest verdict on that coin
  (at most ``agents_max_age_hours`` old, default 36) is Underweight or Sell.

Only verdicts that existed before a decision are used (point-in-time), like every other input.
Files, next to connections.json: ``tradingagents.json`` (settings), ``tradingagents/
results.jsonl`` (one line per verdict), ``tradingagents/reports/`` and ``logs/``.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


SETTINGS_FILE = "tradingagents.json"
RATINGS = ("Buy", "Overweight", "Hold", "Underweight", "Sell")
BULLISH, BEARISH = ("Buy", "Overweight"), ("Underweight", "Sell")
PROVIDERS = (
    "openai",
    "anthropic",
    "google",
    "xai",
    "deepseek",
    "groq",
    "mistral",
    "openrouter",
    "ollama",
    "openai_compatible",
)
KEY_ENV = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GOOGLE_API_KEY",
    "xai": "XAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "openai_compatible": "OPENAI_COMPATIBLE_API_KEY",
}  # ollama runs models on your own computer: no key
DEFAULTS: dict[str, Any] = {
    "python": "",  # TradingAgents' own python (its .venv\Scripts\python.exe)
    "provider": "openai",
    "deep_model": "",  # empty: TradingAgents' default for the provider
    "quick_model": "",
    "backend_url": "",  # ollama / a local OpenAI-compatible server
    "debate_rounds": 1,
    "analysts": ["market", "social", "news"],  # no company fundamentals for a coin
    "timeout_minutes": 40,
    "auto_daily": False,  # run once a day for the bots' coins (costs AI calls)
    "auto_hour_utc": 1,
}
MARK = "@@TRENDBOT_RESULT@@"

# Runs inside TradingAgents' own python: reads its arguments as JSON on stdin and prints one
# marked JSON line. Kept small on purpose: everything else happens in this module.
RUNNER = r"""
import glob, json, os, sys, time, traceback
MARK = "@@TRENDBOT_RESULT@@"
a = json.loads(sys.stdin.read())
out = {"ok": False}
try:
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    cfg = DEFAULT_CONFIG.copy()
    cfg.update(a["config"])
    ta = TradingAgentsGraph(selected_analysts=tuple(a["analysts"]), debug=False, config=cfg)
    state, signal = ta.propagate(a["ticker"], a["date"], asset_type="crypto")
    out = {"ok": True, "signal": str(signal)}
    for k in ("final_trade_decision", "trader_investment_plan", "investment_plan"):
        v = state.get(k) if hasattr(state, "get") else None
        out[k] = v if isinstance(v, str) else (json.dumps(v, default=str) if v else "")
    try:
        p = ta.save_reports(state, a["ticker"], save_path=a["report_dir"])
        pages = glob.glob(os.path.join(a["report_dir"], "**", "*.html"), recursive=True)
        out["report_html"] = pages[0] if pages else None
        out["report_path"] = str(p)
    except Exception as exc:
        out["report_error"] = str(exc)
except Exception as exc:
    out = {"ok": False, "error": f"{type(exc).__name__}: {exc}",
           "trace": traceback.format_exc()[-3000:]}
print(MARK + json.dumps(out), flush=True)
"""


class AgentsError(ValueError):
    """A plain-English problem with TradingAgents (safe to show)."""


# ------------------------------------------------------------------------- files
def home(base: str | Path | None = None) -> Path:
    """The folder next to connections.json (``base`` overrides, for tests)."""
    if base is not None:
        return Path(base)
    from .connections import connections_path

    return connections_path().parent


def data_dir(base: str | Path | None = None) -> Path:
    return home(base) / "tradingagents"


def load(base: str | Path | None = None) -> dict[str, Any]:
    p = home(base) / SETTINGS_FILE
    try:
        raw = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        raw = {}
    return {**DEFAULTS, **{k: v for k, v in raw.items() if k in DEFAULTS or k == "last_auto"}}


def _save(base: str | Path | None, cfg: Mapping[str, Any]) -> None:
    from .journal import atomic_write_text

    p = home(base) / SETTINGS_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(p, json.dumps(dict(cfg), indent=1))


def configure(base: str | Path | None = None, **changes: Any) -> dict[str, Any]:
    """Save settings (unknown keys are refused; the API key goes to .env, not here)."""
    cfg = load(base)
    for k, v in changes.items():
        if v is None:
            continue
        if k not in DEFAULTS:
            raise AgentsError(f"unknown TradingAgents setting {k!r}")
        if k == "provider" and v not in PROVIDERS:
            raise AgentsError(f"provider must be one of {', '.join(PROVIDERS)}")
        if k == "python" and v and not Path(str(v)).name.lower().startswith("python"):
            raise AgentsError("python: the path of TradingAgents' python (…\\Scripts\\python.exe)")
        if k == "debate_rounds" and not 1 <= int(v) <= 5:
            raise AgentsError("debate rounds: 1 to 5 (each round is more AI calls)")
        if k == "analysts":
            v = [x for x in v if x in ("market", "social", "news", "fundamentals")] or ["market"]
        cfg[k] = v
    _save(base, cfg)
    return cfg


def set_api_key(provider: str, key: str | None) -> str:
    """Store the provider's key in the bot's .env on this computer (never shown again)."""
    from .connections import set_env_vars

    name = KEY_ENV.get(provider)
    if name is None:
        raise AgentsError(f"{provider} needs no API key")
    set_env_vars({name: (key or "").strip() or None})
    return f"{name} {'saved' if key else 'removed'} in .env"


# ------------------------------------------------------------------------- pairs
def ticker_for(pair: str) -> str:
    """'BTC/USDT' -> 'BTC-USD' (the Yahoo Finance symbol TradingAgents prices)."""
    base = pair.upper().replace("-", "/").split("/")[0].strip()
    if not base.isalnum() or len(base) > 12:
        raise AgentsError(f"not a coin: {pair!r}")
    return f"{base}-USD"


def coin_of(pair_or_ticker: str) -> str:
    return pair_or_ticker.upper().replace("-", "/").split("/")[0].strip()


def bias(rating: str | None) -> str:
    if rating in BULLISH:
        return "bullish"
    if rating in BEARISH:
        return "bearish"
    return "neutral" if rating == "Hold" else "unknown"


# ------------------------------------------------------------------------- results
def results(base: str | Path | None = None, limit: int = 200) -> list[dict[str, Any]]:
    p = data_dir(base) / "results.jsonl"
    rows: list[dict[str, Any]] = []
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines()[-limit:]:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows[::-1]  # newest first


def latest(
    coin: str,
    now_ms: int | None = None,
    max_age_hours: float = 36.0,
    base: str | Path | None = None,
) -> dict[str, Any] | None:
    """The newest successful verdict on ``coin`` known at ``now_ms`` and not too old."""
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    c = coin_of(coin)
    for r in results(base):
        if r.get("coin") != c or not r.get("ok") or r.get("rating") not in RATINGS:
            continue
        done = int(r.get("finished_ms") or 0)
        if done <= now and now - done <= max_age_hours * 3_600_000:
            return r
    return None


def _append(base: str | Path | None, rec: Mapping[str, Any]) -> None:
    d = data_dir(base)
    d.mkdir(parents=True, exist_ok=True)
    with (d / "results.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(dict(rec), default=str) + "\n")


def _summary(text: str, limit: int = 600) -> str:
    t = " ".join(str(text or "").split())
    return t if len(t) <= limit else t[: limit - 1] + "…"


def _rating(signal: str, decision: str) -> str | None:
    s = str(signal or "").strip().strip("*").title()
    if s in RATINGS:
        return s
    import re

    m = re.search(
        r"rating\W{0,6}(buy|overweight|hold|underweight|sell)\b", decision or "", re.IGNORECASE
    )
    return m.group(1).title() if m else None


# ------------------------------------------------------------------------- one run
def status(base: str | Path | None = None) -> dict[str, Any]:
    """Settings, whether TradingAgents' python can be found, the key, running jobs."""
    from .live_exchange import load_env_file

    load_env_file()
    cfg = load(base)
    py = cfg["python"]
    key = KEY_ENV.get(cfg["provider"])
    running = []
    for p in sorted((data_dir(base) / "running").glob("*.json")):
        try:
            job = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if _alive(int(job.get("pid") or 0)):
            running.append(job)
        else:
            p.unlink(missing_ok=True)
    return {
        "settings": {k: v for k, v in cfg.items() if k != "last_auto"},
        "python_found": bool(py) and Path(py).exists(),
        "key_env": key,
        "key_set": True if key is None else bool(os.environ.get(key)),
        "running": running,
        "providers": list(PROVIDERS),
        "results": results(base, 30),
    }


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            return str(pid) in out.stdout
        os.kill(pid, 0)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def check_ready(cfg: Mapping[str, Any]) -> None:
    py = str(cfg.get("python") or "")
    if not py:
        raise AgentsError(
            "TradingAgents is not set up: install it in its own folder and paste the path "
            "of its python (e.g. C:\\TradingAgents\\.venv\\Scripts\\python.exe)"
        )
    if not Path(py).exists():
        raise AgentsError(f"TradingAgents' python was not found at {py}")
    key = KEY_ENV.get(str(cfg.get("provider")))
    if key and not os.environ.get(key):
        raise AgentsError(f"no API key for {cfg.get('provider')}: add {key} on the card")


def run(
    pair: str,
    date: str | None = None,
    base: str | Path | None = None,
    runner: Any = None,
) -> dict[str, Any]:
    """One TradingAgents run on ``pair`` (blocks for minutes); the verdict is saved."""
    from .live_exchange import load_env_file

    load_env_file()
    cfg = load(base)
    ticker = ticker_for(pair)
    day = date or time.strftime("%Y-%m-%d", time.gmtime())
    started = int(time.time() * 1000)
    rd = data_dir(base) / "reports" / f"{coin_of(pair)}-{day}-{started}"
    args = {
        "ticker": ticker,
        "date": day,
        "analysts": list(cfg["analysts"]),
        "report_dir": str(rd),
        "config": _ta_config(cfg, base),
    }
    rec: dict[str, Any] = {
        "id": f"{coin_of(pair)}-{started}",
        "coin": coin_of(pair),
        "ticker": ticker,
        "date": day,
        "started_ms": started,
        "provider": cfg["provider"],
        "models": [cfg["quick_model"] or "default", cfg["deep_model"] or "default"],
    }
    try:
        if runner is None:
            check_ready(cfg)
        out = (runner or _subprocess_runner)(cfg, args, base)
    except AgentsError as exc:
        out = {"ok": False, "error": str(exc)}
    rec["finished_ms"] = int(time.time() * 1000)
    rec["ok"] = bool(out.get("ok"))
    if rec["ok"]:
        decision = out.get("final_trade_decision") or ""
        rec["rating"] = _rating(out.get("signal", ""), decision)
        rec["ok"] = rec["rating"] is not None  # REVIEW: no readable rating, not a Hold
        rec["bias"] = bias(rec["rating"])
        rec["summary"] = _summary(decision)
        rec["trader_plan"] = _summary(out.get("trader_investment_plan") or "", 1200)
        rec["decision"] = str(decision)[:20_000]
        rec["report_html"] = out.get("report_html")
        if not rec["ok"]:
            rec["error"] = "the agents gave no readable rating (REVIEW)"
    else:
        rec["error"] = str(out.get("error") or "the run failed")[:2000]
    _append(base, rec)
    return rec


def _ta_config(cfg: Mapping[str, Any], base: str | Path | None) -> dict[str, Any]:
    c: dict[str, Any] = {
        "llm_provider": cfg["provider"],
        "max_debate_rounds": int(cfg["debate_rounds"]),
        "max_risk_discuss_rounds": int(cfg["debate_rounds"]),
        "results_dir": str(data_dir(base) / "ta_logs"),
    }
    if cfg["deep_model"]:
        c["deep_think_llm"] = cfg["deep_model"]
    if cfg["quick_model"]:
        c["quick_think_llm"] = cfg["quick_model"]
    if cfg["backend_url"]:
        c["backend_url"] = cfg["backend_url"]
    return c


def _subprocess_runner(
    cfg: Mapping[str, Any], args: Mapping[str, Any], base: Any
) -> dict[str, Any]:
    logs = data_dir(base) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / f"{args['ticker']}-{args['date']}.log"
    env = dict(os.environ)
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    env.pop("PYTHONPATH", None)  # TradingAgents' own packages only
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    try:
        p = subprocess.run(
            [str(cfg["python"]), "-c", RUNNER],
            input=json.dumps(args),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=float(cfg["timeout_minutes"]) * 60,
            check=False,
            creationflags=flags,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"no answer within {cfg['timeout_minutes']} minutes"}
    except OSError as exc:
        return {"ok": False, "error": f"could not start TradingAgents' python: {exc}"}
    log_path.write_text((p.stdout or "") + "\n--- stderr ---\n" + (p.stderr or ""), "utf-8")
    for line in reversed((p.stdout or "").splitlines()):
        if line.startswith(MARK):
            out = json.loads(line[len(MARK) :])
            if not out.get("ok") and "No module named 'tradingagents'" in str(out.get("error")):
                out["error"] = "that python has no TradingAgents: run  pip install .  in its folder"
            return out
    tail = (p.stderr or p.stdout or "").strip().splitlines()[-3:]
    return {"ok": False, "error": "TradingAgents stopped without an answer: " + " / ".join(tail)}


# ------------------------------------------------------------------------- background
def start(pairs: Sequence[str], base: str | Path | None = None) -> str:
    """Start runs in a separate process (the dashboard and the bot never wait for them)."""
    from .launch import command

    cfg = load(base)
    check_ready(cfg)
    coins = sorted({coin_of(p) for p in pairs})
    if not coins:
        raise AgentsError("which coin? e.g. agents BTC")
    busy = {j.get("coin") for j in status(base)["running"]}
    todo = [c for c in coins if c not in busy]
    if not todo:
        return f"already running for {', '.join(coins)}"
    cmd, env = command("agents_link", "run", *[f"{c}/USD" for c in todo])
    if base is not None:
        cmd += ["--home", str(base)]
    d = data_dir(base)
    (d / "running").mkdir(parents=True, exist_ok=True)
    err = (d / "jobs.stderr.log").open("ab")
    flags = 0x08000000 if os.name == "nt" else 0
    proc = subprocess.Popen(
        cmd, env=env, stdout=subprocess.DEVNULL, stderr=err, creationflags=flags
    )
    for c in todo:
        (d / "running" / f"{c}.json").write_text(
            json.dumps({"coin": c, "pid": proc.pid, "started_ms": int(time.time() * 1000)}), "utf-8"
        )
    return f"TradingAgents started on {', '.join(todo)}: a run takes a few minutes"


def auto_due(base: str | Path | None, now_ms: int, coins: Sequence[str]) -> list[str]:
    """The coins whose once-a-day run is due (only when switched on)."""
    cfg = load(base)
    if not cfg["auto_daily"]:
        return []
    slot = now_ms // 86_400_000 * 86_400_000 + int(cfg["auto_hour_utc"]) * 3_600_000
    slot = slot if slot <= now_ms else slot - 86_400_000
    last = cfg.get("last_auto") if isinstance(cfg.get("last_auto"), dict) else {}
    return sorted({coin_of(c) for c in coins if int(last.get(coin_of(c)) or 0) < slot <= now_ms})


_auto_lock = threading.Lock()


def maybe_auto(pairs: Sequence[str], base: str | Path | None = None) -> str | None:
    """Called by the bot loop: start the daily run once per coin per day; never raises."""
    now = int(time.time() * 1000)
    with _auto_lock:
        try:
            todo = auto_due(base, now, pairs)
            if not todo:
                return None
            cfg = load(base)
            last = cfg.get("last_auto") if isinstance(cfg.get("last_auto"), dict) else {}
            cfg["last_auto"] = {**last, **{c: now for c in todo}}
            _save(base, cfg)
            return start(todo, base)
        except (AgentsError, OSError, ValueError) as exc:
            return f"TradingAgents daily run not started: {exc}"


# ------------------------------------------------------------------------- Chantisimo
def gate(
    pair: str, now_ms: int, mode: str, max_age_hours: float = 36.0, base: Any = None
) -> tuple[bool, str, dict[str, Any] | None]:
    """(ok, why, verdict): in "veto" mode a bearish verdict blocks a long entry."""
    if mode not in ("inform", "veto"):
        return True, "", None
    try:
        v = latest(coin_of(pair), now_ms, max_age_hours, base)
    except (OSError, ValueError):
        return True, "", None
    if v is None:
        return True, "", None
    note = f"TradingAgents rated {coin_of(pair)} {v['rating']} on {v['date']}"
    if mode == "veto" and v["rating"] in BEARISH:
        return False, note + ": no long against the agents", v
    return True, note, v


def scorecard(reflections: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """How often the agents' call matched the trade's result (from the brain's reflections)."""
    rows = [r for r in reflections if (r.get("agents") or {}).get("rating")]
    right = sum(1 for r in rows if r["agents"].get("right"))
    return {
        "trades": len(rows),
        "right": right,
        "hit_rate": round(right / len(rows), 3) if rows else None,
        "text": (
            f"the agents were right on {right} of {len(rows)} trades taken with a verdict"
            if rows
            else "no trade taken with a TradingAgents verdict yet"
        ),
    }


# ------------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="trendbot agents", description="TradingAgents for Chantisimo")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="run the agents on coins now (waits for the verdicts)")
    r.add_argument("pairs", nargs="+", help="BTC/USDT, ETH, ...")
    r.add_argument("--date", help="YYYY-MM-DD (default today)")
    r.add_argument("--home", help=argparse.SUPPRESS)
    s = sub.add_parser("setup", help="save settings")
    s.add_argument("--python", help="TradingAgents' python (its .venv\\Scripts\\python.exe)")
    s.add_argument("--provider", choices=PROVIDERS)
    s.add_argument("--deep-model")
    s.add_argument("--quick-model")
    s.add_argument("--rounds", type=int)
    s.add_argument("--home", help=argparse.SUPPRESS)
    sub.add_parser("status", help="settings and the latest verdicts").add_argument(
        "--home", help=argparse.SUPPRESS
    )
    a = p.parse_args(argv)
    try:
        if a.cmd == "setup":
            cfg = configure(
                a.home,
                python=a.python,
                provider=a.provider,
                deep_model=a.deep_model,
                quick_model=a.quick_model,
                debate_rounds=a.rounds,
            )
            print(json.dumps({k: v for k, v in cfg.items() if k != "last_auto"}, indent=1))
            return 0
        if a.cmd == "status":
            st = status(a.home)
            print(
                f"python: {st['settings']['python'] or 'not set'} "
                f"({'found' if st['python_found'] else 'not found'})"
            )
            print(
                f"provider: {st['settings']['provider']}, "
                f"key {'set' if st['key_set'] else 'MISSING'}"
            )
            for v in st["results"][:10]:
                print(
                    f"  {v['date']} {v['coin']:<6} {v.get('rating') or 'failed':<11} "
                    f"{v.get('summary') or v.get('error') or ''}"[:160]
                )
            return 0
        failed = 0
        for pair in a.pairs:
            rec = run(pair, a.date, a.home)
            (data_dir(a.home) / "running" / f"{coin_of(pair)}.json").unlink(missing_ok=True)
            if rec["ok"]:
                print(f"{rec['coin']}: {rec['rating']} ({rec['bias']}): {rec['summary'][:300]}")
            else:
                failed += 1
                print(f"{rec['coin']}: failed: {rec.get('error')}", file=sys.stderr)
        return 1 if failed else 0
    except AgentsError as exc:
        print(f"agents: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
