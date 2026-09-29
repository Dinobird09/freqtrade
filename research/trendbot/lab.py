"""Strategy lab: overnight auto-research, walk-forward, stress tests, look-ahead detection and
the incubation gate before real money.

The loop (items 91-97), in the spirit of an "auto-research" loop (prepare -> train -> a
readable ``program.md`` report):

``prepare``    loads the cached candles of every pair (4H, or 15m for 15-minute strategies)
``train``      for every strategy: the current parameters and ``mutations`` random variants
               from its grid are scored by a ROLLING WALK-FORWARD, allocation-based (1% risk
               per trade on a compounding equity): 252 days in-sample pick the best variant,
               the next 6 months out-of-sample are traded with it; the window then rolls on
               by 6 months. Only the out-of-sample months count.
``check``      every variant must pass the LOOK-AHEAD detector: re-running it on history cut
               at several points must reproduce exactly the signals it gave before each cut
               (a strategy that peeks at the future changes its past), and its out-of-sample
               result must be believable (more than +1,000%/year or a Sharpe above 6 is
               treated as a leak and discarded). Survivors are STRESS-TESTED: 20 runs with a
               random single-day crash of -5% to -15% injected into the prices.
``report``     ``lab/leaderboard.json`` and ``lab/program.md`` (plain English).

Promotion never happens by itself. A variant becomes a PROPOSAL only if its out-of-sample
Sharpe beats ``min_sharpe`` (1.5) and the currently approved version of that strategy. You
approve a proposal on the dashboard; it then runs in PAPER mode (``engine: "lab"`` bots) and
must INCUBATE for at least ``incubation_days`` (30) of paper trading on live data before a
testnet or live bot may run it.
"""

from __future__ import annotations

import json
import logging
import random
import time
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from .journal import ms_to_iso
from .models import Candle
from .strategies import DAY_MS, STRATEGIES, Strategy, backtest, performance


log = logging.getLogger("trendbot.lab")

IS_DAYS, OOS_DAYS = 252, 182
MAX_BELIEVABLE_CAGR, MAX_BELIEVABLE_SHARPE = 1000.0, 6.0


def lab_dir(state_dir: str | Path) -> Path:
    return Path(state_dir) / "lab"


# ---------------------------------------------------------------------- walk-forward
def rolling_walk_forward(
    cs: Sequence[Candle],
    make: Any,
    variants: Sequence[Mapping[str, Any]],
    *,
    is_days: int = IS_DAYS,
    oos_days: int = OOS_DAYS,
    min_is_trades: int = 5,
) -> dict[str, Any]:
    """Pick the best variant on each in-sample window, trade it on the next out-of-sample one."""
    if not cs:
        return {"windows": [], "oos": {"trades": 0}}
    start, end = cs[0].ts, cs[-1].ts
    idx_of = lambda ts: next((k for k, c in enumerate(cs) if c.ts >= ts), len(cs))  # noqa: E731
    sig_cache = {i: make(**v).signals(cs) for i, v in enumerate(variants)}
    windows, oos_trades, curve = [], [], []
    equity = 10_000.0
    t = start
    while t + (is_days + oos_days) * DAY_MS <= end + DAY_MS:
        is_a, is_b = idx_of(t), idx_of(t + is_days * DAY_MS)
        oos_b = idx_of(t + (is_days + oos_days) * DAY_MS)
        best, best_s = 0, -1e9
        for vi, v in enumerate(variants):
            sigs = [s for s in sig_cache[vi] if is_a <= s.i < is_b]
            r = backtest(cs[:is_b], make(**v), signals=sigs, start_i=is_a)
            score = (
                r.stats.get("sharpe", -1e9) if r.stats.get("trades", 0) >= min_is_trades else -1e9
            )
            if score > best_s:
                best, best_s = vi, score
        sigs = [s for s in sig_cache[best] if is_b <= s.i < oos_b]
        r = backtest(cs[:oos_b], make(**variants[best]), signals=sigs, start_i=is_b, equity=equity)
        windows.append(
            {
                "in_sample": [ms_to_iso(cs[is_a].ts)[:10], ms_to_iso(cs[is_b - 1].ts)[:10]],
                "out_of_sample": [ms_to_iso(cs[is_b].ts)[:10], ms_to_iso(cs[oos_b - 1].ts)[:10]],
                "chosen": dict(variants[best]),
                "in_sample_sharpe": round(best_s, 3) if best_s > -1e8 else None,
                "oos": r.stats,
            }
        )
        oos_trades += r.trades
        curve += r.equity
        equity = r.equity[-1][1] if r.equity else equity
        t += oos_days * DAY_MS
    return {"windows": windows, "oos": performance(oos_trades, curve, 10_000.0)}


# ---------------------------------------------------------------------- look-ahead
def lookahead_check(
    strategy: Strategy, cs: Sequence[Candle], cuts: int = 6, samples: int = 25
) -> tuple[bool, str]:
    """No signal may depend on the future: (1) each sampled signal at bar i reappears when
    the history ends at bar i; (2) signals before a cut do not change when later bars go."""
    sigs = strategy.signals(cs)
    full = [s.key() for s in sigs]
    step = max(1, len(sigs) // samples)
    for s in sigs[::step][:samples]:
        again = [x.key() for x in strategy.signals(cs[: s.i + 1]) if x.i == s.i]
        if s.key() not in again:
            return False, (
                f"its signal at bar {s.i} disappears when the history ends at that bar: "
                "it looks ahead"
            )
    n = len(cs)
    for k in range(1, cuts + 1):
        cut = n * k // (cuts + 1)
        if cut < 50:
            continue
        before = [s.key() for s in strategy.signals(cs[:cut])]
        expect = [s for s in full if s[0] < cut]
        if before != expect:
            return (
                False,
                f"signals before bar {cut} change when later bars are removed: it looks ahead",
            )
    return True, "no look-ahead: truncated history reproduces every earlier signal"


def believable(stats: Mapping[str, Any]) -> tuple[bool, str]:
    cagr, sh = stats.get("cagr_pct") or 0, stats.get("sharpe") or 0
    if cagr > MAX_BELIEVABLE_CAGR or sh > MAX_BELIEVABLE_SHARPE:
        return (
            False,
            f"unrealistic ({cagr:.0f}%/year, Sharpe {sh:.1f}): treated as look-ahead and discarded",
        )
    return True, "believable"


# ---------------------------------------------------------------------- stress test
def inject_crash(cs: Sequence[Candle], day_index: int, drop: float) -> list[Candle]:
    """A single-day crash: from that day on every price is ``drop`` lower (a gap and a wick)."""
    days = sorted({c.ts // DAY_MS for c in cs})
    if not days:
        return list(cs)
    d = days[min(day_index, len(days) - 1)]
    f = 1 - drop
    out = []
    for c in cs:
        day = c.ts // DAY_MS
        if day < d:
            out.append(c)
        elif day == d:  # the crash day: opens normally, trades through the drop, closes on it
            lo = min(c.low, c.open * f * (1 - drop / 3))
            out.append(
                replace(
                    c, high=max(c.open, c.high * f), low=lo, close=c.close * f, volume=c.volume * 3
                )
            )
        else:
            out.append(
                replace(c, open=c.open * f, high=c.high * f, low=c.low * f, close=c.close * f)
            )
    return out


def stress_test(
    cs: Sequence[Candle], strategy: Strategy, runs: int = 20, seed: int = 7
) -> dict[str, Any]:
    rng = random.Random(seed)
    base = backtest(cs, strategy).stats
    n_days = len({c.ts // DAY_MS for c in cs})
    res = []
    for _ in range(runs):
        drop = rng.uniform(0.05, 0.15)
        r = backtest(inject_crash(cs, rng.randrange(max(1, n_days)), drop), strategy).stats
        res.append(
            {
                "drop_pct": round(drop * 100, 1),
                "return_pct": r.get("return_pct"),
                "max_dd_pct": r.get("max_dd_pct"),
            }
        )
    worst = max((x["max_dd_pct"] or 0 for x in res), default=0)
    return {
        "baseline": {"return_pct": base.get("return_pct"), "max_dd_pct": base.get("max_dd_pct")},
        "runs": len(res),
        "worst_max_dd_pct": worst,
        "median_return_pct": sorted(x["return_pct"] or 0 for x in res)[len(res) // 2]
        if res
        else None,
        "samples": res[:5],
    }


# ---------------------------------------------------------------------- auto-research
def mutations(cls: type[Strategy], n: int, rng: random.Random) -> list[dict[str, Any]]:
    grid = cls.grid
    out: list[dict[str, Any]] = [{}]
    seen = {json.dumps({}, sort_keys=True)}
    for _ in range(n * 5):
        if len(out) > n:
            break
        v = {k: rng.choice(vals) for k, vals in grid.items()}
        key = json.dumps(v, sort_keys=True)
        if key not in seen:
            seen.add(key)
            out.append(v)
    return out


def prepare(state_dir: Path) -> dict[str, dict[str, list[Candle]]]:
    """Candles by timeframe and pair: the cached 4H history plus any 15m cache."""
    from .data import load_candles_csv

    out: dict[str, dict[str, list[Candle]]] = {"4h": {}, "15m": {}}
    for tf in out:
        d = state_dir / "candles"
        for p in sorted(d.glob(f"*-{tf}.csv")) if d.exists() else []:
            out[tf][p.name[: -len(f"-{tf}.csv")].replace("_", "/", 1)] = load_candles_csv(p)
    return out


def train(
    data: Mapping[str, Mapping[str, Sequence[Candle]]],
    *,
    n_mutations: int = 12,
    seed: int = 11,
    min_sharpe: float = 1.5,
    approved: Mapping[str, Any] | None = None,
    stress_runs: int = 20,
) -> dict[str, Any]:
    rng = random.Random(seed)
    board, proposals = [], []
    for name, cls in STRATEGIES.items():
        for pair, cs in sorted((data.get(cls.timeframe) or {}).items()):
            if len(cs) < 400:
                continue
            variants = mutations(cls, n_mutations, rng)
            row: dict[str, Any] = {
                "strategy": name,
                "pair": pair,
                "timeframe": cls.timeframe,
                "variants": len(variants),
            }
            clean = []
            for v in variants:
                ok, why = lookahead_check(cls(**v), cs, cuts=3)
                if ok:
                    clean.append(v)
                else:
                    row.setdefault("discarded", []).append({"params": v, "why": why})
            if not clean:
                row["status"] = "discarded"
                board.append(row)
                continue
            wf = rolling_walk_forward(cs, cls, clean)
            oos = wf["oos"]
            row.update(
                oos=oos,
                windows=len(wf["windows"]),
                last_params=wf["windows"][-1]["chosen"] if wf["windows"] else {},
            )
            ok, why = believable(oos)
            if not ok:
                row["status"] = "discarded"
                row["why"] = why
                board.append(row)
                continue
            row["stress"] = stress_test(cs, cls(**row["last_params"]), runs=stress_runs)
            current = (approved or {}).get(f"{name}:{pair}", {})
            beat = oos.get("sharpe", 0) > max(min_sharpe, float(current.get("sharpe") or 0))
            row["status"] = "proposal" if beat and oos.get("trades", 0) >= 10 else "not good enough"
            row["why"] = (
                f"out-of-sample Sharpe {oos.get('sharpe')} over {oos.get('trades')} trades "
                + ("beats" if beat else "does not beat")
                + f" {max(min_sharpe, float(current.get('sharpe') or 0)):g}"
            )
            if row["status"] == "proposal":
                proposals.append(
                    {
                        "id": f"{name}:{pair}",
                        "strategy": name,
                        "pair": pair,
                        "timeframe": cls.timeframe,
                        "params": row["last_params"],
                        "sharpe": oos.get("sharpe"),
                        "oos": oos,
                    }
                )
            board.append(row)
    board.sort(key=lambda r: -((r.get("oos") or {}).get("sharpe") or -99))
    return {"leaderboard": board, "proposals": proposals}


def render_program(res: Mapping[str, Any], when: int) -> str:
    lines = [
        "# Strategy lab: last night's research",
        "",
        f"Run {ms_to_iso(when)}. Rolling walk-forward: {IS_DAYS} days in-sample, {OOS_DAYS} days "
        "out-of-sample, only out-of-sample results count. Look-ahead detector and crash "
        "stress test on every survivor. Nothing is switched on without your approval.",
        "",
        "| strategy | pair | out-of-sample Sharpe | return | max DD | trades | status |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in res["leaderboard"]:
        o = r.get("oos") or {}
        lines.append(
            f"| {r['strategy']} | {r['pair']} | {o.get('sharpe', '-')} | "
            f"{o.get('return_pct', '-')}% | {o.get('max_dd_pct', '-')}% | "
            f"{o.get('trades', '-')} | {r['status']} |"
        )
    lines += ["", "## Proposals", ""]
    lines += [
        f"- **{p['id']}** {p['params']}: out-of-sample Sharpe {p['sharpe']}"
        for p in res["proposals"]
    ] or ["None tonight: no variant beat the bar on unseen data."]
    return "\n".join(lines) + "\n"


def run(state_dir: str | Path, **kw: Any) -> dict[str, Any]:
    """The nightly job: prepare -> train -> report. Writes lab/leaderboard.json + program.md."""
    d = lab_dir(state_dir)
    d.mkdir(parents=True, exist_ok=True)
    approved = load_approved()
    t0 = time.time()
    res = train(prepare(Path(state_dir)), approved=approved, **kw)
    now = int(time.time() * 1000)
    res.update(run_ms=now, seconds=round(time.time() - t0, 1))
    (d / "leaderboard.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    (d / "program.md").write_text(render_program(res, now), encoding="utf-8")
    return res


# ---------------------------------------------------------------------- approval + incubation
def approved_path() -> Path:
    """One approval list per computer (next to connections.json), shared by every bot."""
    from .traders import traders_dir

    return traders_dir() / "lab_approved.json"


def load_approved(state_dir: str | Path | None = None) -> dict[str, Any]:
    p = approved_path()
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_approved(appr: Mapping[str, Any]) -> None:
    p = approved_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(appr, indent=1), encoding="utf-8")


def mark_paper_start(key: str, now_ms: int) -> None:
    """A paper lab bot started running ``key``: its incubation clock starts (once)."""
    appr = load_approved()
    if key in appr and not appr[key].get("paper_since_ms"):
        appr[key]["paper_since_ms"] = now_ms
        save_approved(appr)


def approve(state_dir: str | Path, proposal_id: str, now_ms: int | None = None) -> dict[str, Any]:
    """The operator approved a proposal: it starts its paper incubation now."""
    board = json.loads((lab_dir(state_dir) / "leaderboard.json").read_text(encoding="utf-8"))
    prop = next((p for p in board.get("proposals", []) if p["id"] == proposal_id), None)
    if prop is None:
        raise ValueError(f"no proposal {proposal_id!r} in the last research run")
    appr = load_approved()
    appr[proposal_id] = {
        **prop,
        "approved_ms": now_ms or int(time.time() * 1000),
        "status": "incubating",
    }
    save_approved(appr)
    return appr[proposal_id]


def incubation(
    state_dir: str | Path, key: str, mode: str, days: float = 30, now_ms: int | None = None
) -> tuple[bool, str]:
    """May a lab strategy run in ``mode``? Paper always; real money only after incubation."""
    if mode == "paper":
        return True, "paper trading is how a strategy incubates"
    appr = load_approved().get(key)
    if appr is None:
        return False, f"{key} is not approved: approve a proposal on the dashboard first"
    now = int(time.time() * 1000) if now_ms is None else now_ms
    if not appr.get("paper_since_ms"):
        return False, f"{key} has not paper-traded yet: run it on a paper bot first"
    started = int(appr["paper_since_ms"])
    age = (now - started) / DAY_MS
    if age < days:
        return False, (
            f"{key} has incubated {age:.0f} of {days:g} days in paper trading; testnet and "
            "live wait until the incubation is complete"
        )
    return True, f"{key} incubated {age:.0f} days"
