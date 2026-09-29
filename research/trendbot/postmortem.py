"""Post-mortems: WHY a strategy made money, WHY its losing trades lost, and what would help.

For a strategy's trades (out-of-sample in the lab, or a lab bot's live journal) it measures:

- the win rate against the BREAK-EVEN win rate its payoffs need (avg loss / (avg win + avg
  loss)): the single number that decides whether a strategy can make money;
- how trades ended: stop, target, trailing stop, and how long winners vs losers were held;
- the LOSERS: stopped within 2 bars (entered into a reversal / stop too tight for the
  volatility), were +1R in profit first and then fell back to the stop (gave the profit
  back), came within 80% of the target (near misses), or were taken below the 200-bar
  average (against the trend);
- the WINNERS: how much the trailing stop added beyond the target, and where they came from
  (with the trend, in high or low volatility);
- the cost of fees and slippage in R per trade.

It then writes plain sentences: "why it worked", "why the losses happened" and "what would
help", and compares the best strategy with the rest ("why it is the best"). Every trade also
gets a one-line story (``trade_story``).
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from typing import Any

from .models import Candle
from .strategies import atr, sma


def _mfe_r(t: Any, cs: Sequence[Candle]) -> float:
    """How far the trade went in its favour before it ended, in R."""
    risk_px = t.entry - t.stop
    if risk_px <= 0 or t.exit_i is None:
        return 0.0
    hi = max(c.high for c in cs[t.entry_i : t.exit_i + 1])
    return (hi - t.entry) / risk_px


def trade_story(t: Any, cs: Sequence[Candle]) -> str:
    """One sentence on how a single lab trade played out."""
    bars = (t.exit_i or t.entry_i) - t.entry_i
    kinds = [e[3] for e in t.exits]
    mfe = _mfe_r(t, cs)
    r = t.r or 0.0
    if r > 0:
        if "TRAIL" in kinds:
            return (
                f"won {r:+.2f}R: half at the 2R target, the rest trailed the trend for {bars} bars"
            )
        return f"won {r:+.2f}R: reached the target after {bars} bars"
    if bars <= 2:
        when = "in the entry bar" if bars == 0 else f"within {bars} bar{'s' if bars > 1 else ''}"
        return f"lost {r:+.2f}R: stopped {when}, the entry came into a reversal"
    if mfe >= 1.0:
        return f"lost {r:+.2f}R: was +{mfe:.1f}R in profit first, then fell back to the stop"
    if t.target and mfe >= 0.8 * (t.target - t.entry) / max(t.entry - t.stop, 1e-12):
        return f"lost {r:+.2f}R: came within reach of the target ({mfe:.1f}R) and turned"
    return f"lost {r:+.2f}R: never got going (best +{mfe:.1f}R) and hit the stop after {bars} bars"


def analyse(trades: Sequence[Any], cs: Sequence[Candle]) -> dict[str, Any]:
    """Measurements behind the explanation (JSON-ready)."""
    done = [t for t in trades if t.r is not None and t.exit_i is not None]
    if not done:
        return {"trades": 0}
    closes = [c.close for c in cs]
    ma200 = sma(closes, 200)
    a14 = atr(cs, 14)
    wins = [t for t in done if t.r > 0]
    losses = [t for t in done if t.r <= 0]
    aw = statistics.fmean(t.r for t in wins) if wins else 0.0
    al = -statistics.fmean(t.r for t in losses) if losses else 0.0
    be = al / (aw + al) if aw + al > 0 else None
    bars = lambda ts: statistics.fmean((t.exit_i - t.entry_i) for t in ts) if ts else None  # noqa: E731
    quick = [t for t in losses if t.exit_i - t.entry_i <= 2]
    gave_back = [t for t in losses if _mfe_r(t, cs) >= 1.0]
    near = [
        t
        for t in losses
        if t.target and _mfe_r(t, cs) >= 0.8 * (t.target - t.entry) / max(t.entry - t.stop, 1e-12)
    ]
    trend: dict[str, list[float]] = {"with": [], "against": []}
    vol: list[tuple[float, float]] = []
    for t in done:
        k = t.signal_i
        if ma200[k] is not None:
            trend["with" if closes[k] > ma200[k] else "against"].append(t.r)
        if a14[k] is not None and closes[k]:
            vol.append((a14[k] / closes[k], t.r))
    vol.sort()
    half = len(vol) // 2
    lo_v, hi_v = [r for _, r in vol[:half]], [r for _, r in vol[half:]]
    trailed = [t for t in wins if any(e[3] == "TRAIL" for e in t.exits)]
    fee_r = [
        ((t.qty * t.entry * 0.001) + sum(q * px * 0.001 for _, q, px, _ in t.exits)) / t.risk
        for t in done
        if t.risk
    ]
    avg = lambda xs: round(statistics.fmean(xs), 3) if xs else None  # noqa: E731
    return {
        "trades": len(done),
        "win_rate": round(len(wins) / len(done), 4),
        "breakeven_win_rate": round(be, 4) if be is not None else None,
        "avg_win_r": round(aw, 3),
        "avg_loss_r": round(-al, 3),
        "avg_r": avg([t.r for t in done]),
        "bars_held_winners": avg([t.exit_i - t.entry_i for t in wins]) if wins else None,
        "bars_held_losers": bars(losses) and round(bars(losses), 1),
        "losers": len(losses),
        "quick_stop_share": round(len(quick) / len(losses), 3) if losses else 0.0,
        "gave_back_share": round(len(gave_back) / len(losses), 3) if losses else 0.0,
        "near_miss_share": round(len(near) / len(losses), 3) if losses else 0.0,
        "with_trend_avg_r": avg(trend["with"]),
        "with_trend_n": len(trend["with"]),
        "against_trend_avg_r": avg(trend["against"]),
        "against_trend_n": len(trend["against"]),
        "low_vol_avg_r": avg(lo_v),
        "high_vol_avg_r": avg(hi_v),
        "trailed_winners": len(trailed),
        "trailed_avg_r": avg([t.r for t in trailed]),
        "fee_r_per_trade": avg(fee_r),
        "best": [trade_story(t, cs) for t in sorted(wins, key=lambda t: -t.r)[:3]],
        "worst": [trade_story(t, cs) for t in sorted(losses, key=lambda t: t.r)[:3]],
    }


def explain(m: Mapping[str, Any]) -> dict[str, list[str]]:  # noqa: C901 - one sentence per finding
    """Plain-English 'why it worked', 'why the losses happened' and 'what would help'."""
    if not m.get("trades"):
        return {
            "worked": [],
            "failed": ["No trades on unseen data: the setup never appeared."],
            "help": [],
        }
    n, wr, be = m["trades"], m["win_rate"], m["breakeven_win_rate"]
    worked, failed, helps = [], [], []
    edge = (m.get("avg_r") or 0) > 0
    if be is not None:
        line = (
            f"{wr:.0%} of {n} trades won; with winners averaging {m['avg_win_r']:+.2f}R and losers "
            f"{m['avg_loss_r']:+.2f}R it needs {be:.0%} to break even"
        )
        if wr > be:
            worked.append(
                line
                + (
                    " - it clears that bar."
                    if wr - be >= 0.03
                    else " - it clears that bar, but only just."
                )
            )
        else:
            failed.append(line + " - it falls short.")
    if n < 20:
        failed.append(f"Only {n} trades: too few to trust either way; treat the result as noise.")
    if m.get("trailed_winners"):
        tr = m["trailed_avg_r"]
        beyond = ", beyond the 2R target" if tr and tr > 2 else ""
        worked.append(
            f"The trailing stop kept {m['trailed_winners']} winners in the trend; they averaged {tr:+.2f}R{beyond}."
        )
    wt, at = m.get("with_trend_avg_r"), m.get("against_trend_avg_r")
    if wt is not None and at is not None and m["with_trend_n"] >= 3 and m["against_trend_n"] >= 3:
        if wt >= at:
            worked.append(
                f"Trades with the trend (above the 200-bar average) did best: {wt:+.2f}R vs {at:+.2f}R against it."
            )
            if at < 0:
                failed.append(
                    f"Trades against the trend lost {at:+.2f}R on average over {m['against_trend_n']} trades."
                )
                helps.append("Only take it above the 200-bar average (a trend filter).")
        elif at > 0:
            worked.append(
                f"It did best against the trend ({at:+.2f}R vs {wt:+.2f}R with it): a mean-reversion edge."
            )
            if wt < 0:
                failed.append(f"Trades with the trend lost {wt:+.2f}R on average.")
        else:
            failed.append(f"It lost both with the trend ({wt:+.2f}R) and against it ({at:+.2f}R).")
    lv, hv = m.get("low_vol_avg_r"), m.get("high_vol_avg_r")
    if lv is not None and hv is not None and abs(lv - hv) > 0.3:
        better, worse = ("calm", "volatile") if lv > hv else ("volatile", "calm")
        if max(lv, hv) > 0:
            worked.append(
                f"It works best in {better} markets ({max(lv, hv):+.2f}R vs {min(lv, hv):+.2f}R when {worse})."
            )
        else:
            failed.append(f"It loses in every market, most when {worse} ({min(lv, hv):+.2f}R).")
        if min(lv, hv) < 0:
            helps.append(f"Skip it when the market is {worse} (a volatility filter).")
    if m["losers"]:
        if m["quick_stop_share"] >= 0.3:
            failed.append(
                f"{m['quick_stop_share']:.0%} of the losers were stopped within 2 bars: the entries came into reversals, or the stop sits inside normal noise."
            )
            helps.append(
                "Wait for a candle to close in the trade's direction before entering, or widen the stop (more ATR)."
            )
        if m["gave_back_share"] >= 0.25:
            failed.append(
                f"{m['gave_back_share']:.0%} of the losers were +1R in profit first and then fell back to the stop: they gave the profit away."
            )
            helps.append(
                "Move the stop to break-even at +1R, or take part of the position earlier."
            )
        if m["near_miss_share"] >= 0.25:
            failed.append(
                f"{m['near_miss_share']:.0%} of the losers came close to the target before turning."
            )
            helps.append("Take half at 1.5R before the full 2R target.")
    fr = m.get("fee_r_per_trade")
    if fr and fr > 0.1:
        failed.append(f"Fees and slippage cost {fr:.2f}R per trade.")
        helps.append(
            "Use limit (maker) orders or a lower fee tier; wider stops also shrink the fee share."
        )
    if edge and not worked:
        worked.append("It made money on unseen data, though no single factor explains most of it.")
    if not edge and not failed:
        failed.append(
            "It lost on unseen data without one dominant cause: the setup has no edge here."
        )
    return {"worked": worked, "failed": failed, "help": helps}


def why_best(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """Why the top strategy of a leaderboard beat the others."""
    ranked = [r for r in rows if (r.get("oos") or {}).get("trades")]
    ranked.sort(key=lambda r: -((r.get("oos") or {}).get("sharpe") or -99))
    if not ranked:
        return ["No strategy traded on unseen data yet."]
    top = ranked[0]
    o, m = top["oos"], top.get("analysis") or {}
    lines = [
        f"{top['strategy']} on {top['pair']} is the best: out-of-sample Sharpe {o.get('sharpe')}, "
        f"{o.get('return_pct')}% return with a {o.get('max_dd_pct')}% worst drawdown over {o.get('trades')} trades."
    ]
    if m.get("breakeven_win_rate") is not None:
        lines.append(
            f"It wins {m['win_rate']:.0%} of the time against the {m['breakeven_win_rate']:.0%} its payoffs need."
        )
    for other in ranked[1:3]:
        oo, om = other["oos"], other.get("analysis") or {}
        diff = []
        if (om.get("win_rate") or 0) < (m.get("win_rate") or 0):
            diff.append(f"wins less often ({om.get('win_rate', 0):.0%})")
        if (om.get("quick_stop_share") or 0) > (m.get("quick_stop_share") or 0) + 0.1:
            diff.append("is stopped out right after entry more often")
        if (om.get("gave_back_share") or 0) > (m.get("gave_back_share") or 0) + 0.1:
            diff.append("gives back more open profit")
        if (oo.get("max_dd_pct") or 0) > (o.get("max_dd_pct") or 0):
            diff.append(f"draws down deeper ({oo.get('max_dd_pct')}%)")
        lines.append(
            f"{other['strategy']} on {other['pair']} (Sharpe {oo.get('sharpe')}) "
            + (", ".join(diff) or "is close behind")
            + "."
        )
    return lines
