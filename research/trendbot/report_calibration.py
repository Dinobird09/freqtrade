"""Markdown rendering of the synthetic-world calibrations: CALIBRATION.md and POWER.md.

Split out of ``report`` (which renders REPORT.md). A calibration runs the full pipeline over
many seeds of one synthetic world with KNOWN ground truth and reports how often each label
was right, with 90 % Wilson intervals, TRAIN and TEST side by side; a power curve does the
same at several planted effect strengths and reads off the minimum detectable effect. Rows
are the dicts ``run_research.cal_row`` writes to ``calibration_runs.csv`` / ``power_runs.csv``
(their values may be the typed ones or the strings read back from the CSV).

Seeds: a calibration runs seeds ``seed_offset + 1 .. seed_offset + N``
(``run_research --seed-offset``). The synthetic worlds share their noise for a given seed
(only the planted drift differs), so calibrations of DIFFERENT worlds should use disjoint
seed ranges when their rates are compared or combined. The seed column of every row is the
actual seed.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from itertools import pairwise

from .config import StrategyConfig
from .metrics import LABELS
from .report_tables import (
    ADOPTION_PATH_LINES,
    DD_RULE,
    DEFAULT_CFG,
    DISCLAIMER,
    WIN_RATE_STATEMENT,
    WORLD_TRUTH,
    cost_lines,
    multiplicity_rule,
    sr,
)


# ---------------------------------------------------------------------------- calibration
CANDIDATES: tuple[tuple[str, str], ...] = (
    ("base", "baseline"),
    ("selected", "discovery-selected"),
    ("ml", "ML layer"),
    ("guard", "expectancy guard"),
)
HOUR_TERMS = ("hour_sin", "hour_cos")
WILSON_Z90 = 1.6448536269514722  # two-sided 90 %


def wilson(k: int, n: int, z: float = WILSON_Z90) -> tuple[float, float]:
    """Wilson score interval of a binomial proportion (default 90%)."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def rate(k: int, n: int) -> str:
    """``k/n = p% (90% Wilson CI lo%-hi%)``."""
    if not n:
        return "0/0 (n/a)"
    lo, hi = wilson(k, n)
    return f"{k}/{n} = {k / n:.0%} (90% Wilson CI {lo:.0%}-{hi:.0%})"


def _nums(values: Sequence[object]) -> list[float]:
    return [float(v) for v in values if v != "" and v is not None]  # type: ignore[arg-type]


def mean_text(values: Sequence[object], digits: int = 3) -> str:
    nums = _nums(values)
    return sr(math.fsum(nums) / len(nums), digits) if nums else "n/a"


def mean_ci(values: Sequence[object]) -> tuple[float, float, float] | None:
    """Mean over seeds and its normal-theory 90% interval (SE across seeds)."""
    nums = _nums(values)
    if not nums:
        return None
    mu = statistics.fmean(nums)
    se = statistics.stdev(nums) / math.sqrt(len(nums)) if len(nums) > 1 else 0.0
    return mu, mu - WILSON_Z90 * se, mu + WILSON_Z90 * se


def _mean_n(values: Sequence[object]) -> str:
    nums = _nums(values)
    return f"{statistics.fmean(nums):.1f}" if nums else "n/a"


def count(rows: Sequence[Mapping[str, object]], key: str, *values: object) -> int:
    return sum(1 for r in rows if r[key] in values)


def _truthy(value: object) -> bool:
    return value is True or value == "True" or value == "true"


_ROBUST_MEANING = {
    "null": " = FALSE-POSITIVE rate (no edge exists)",
    "zero_edge": " = FALSE-POSITIVE rate at the H0 boundary (net edge ~0)",
    "decay": " = FALSE-POSITIVE rate (no edge exists in TEST)",
    "planted": " = DETECTION rate (a real edge exists in TRAIN and TEST)",
    "hour_edge": " (a real but hour-specific edge exists)",
}


def _dd_means(rows: Sequence[Mapping[str, object]], key: str) -> str:
    """``mean realised / mean MTM / mean limit`` TEST max drawdown (%) over ``rows``."""
    parts = []
    for field in ("test_realised_dd_pct", "test_mtm_dd_pct", "dd_limit_pct"):
        nums = _nums([r[f"{key}_{field}"] for r in rows])
        parts.append(f"{statistics.fmean(nums):.2f}" if nums else "n/a")
    return " / ".join(parts)


def candidate_rate_table(rows: Sequence[Mapping[str, object]]) -> list[str]:
    """Per candidate: TEST-gate reach, ROBUST rates and TRAIN | TEST means (Wilson 90%)."""
    n = len(rows)
    lines = [
        "| candidate | seeds that reached the TEST gate (TRAIN avg R > 0, TRAIN n >= 30, TEST "
        "n >= 30) | ROBUST, all seeds | ROBUST given the TEST gate was reached | mean TRAIN "
        "avg R | mean TEST avg R | mean TRAIN n | mean TEST n | mean TEST max DD % realised / "
        "MTM / limit | dd_ok (TEST MTM within limit) |",
        "|---|---|---|---|---:|---:|---:|---:|---|---|",
    ]
    for key, name in CANDIDATES:
        reached = [r for r in rows if _truthy(r[f"{key}_reached_gate"])]
        robust = count(rows, f"{key}_label", "ROBUST")
        robust_reached = count(reached, f"{key}_label", "ROBUST")
        ran = [r for r in rows if r[f"{key}_test_n"] != ""]
        dd_ok = sum(1 for r in ran if _truthy(r[f"{key}_dd_ok"]))
        lines.append(
            f"| {name} | {rate(len(reached), n)} | {rate(robust, n)} | "
            f"{rate(robust_reached, len(reached))} | "
            f"{mean_text([r[f'{key}_train_avg_r'] for r in rows])} | "
            f"{mean_text([r[f'{key}_test_avg_r'] for r in rows])} | "
            f"{_mean_n([r[f'{key}_train_n'] for r in rows])} | "
            f"{_mean_n([r[f'{key}_test_n'] for r in rows])} | {_dd_means(ran, key)} | "
            f"{rate(dd_ok, len(ran))} |"
        )
    return lines


def ml_hour_line(rows: Sequence[Mapping[str, object]]) -> str | None:
    fitted = [r for r in rows if r["ml_top_feature"] != ""]
    if not fitted:
        return None
    top_hour = count(fitted, "ml_top_feature", *HOUR_TERMS)
    mags = [
        math.hypot(float(r["ml_hour_sin"]), float(r["ml_hour_cos"]))  # type: ignore[arg-type]
        for r in fitted
    ]
    return (
        f"- ML coefficients (standardised, fitted on TRAIN only): the largest-magnitude "
        f"coefficient was an hour term (hour_sin/hour_cos) in {top_hour}/{len(fitted)} fitted "
        f"seeds; mean hour-term magnitude sqrt(hour_sin^2 + hour_cos^2) = "
        f"{statistics.fmean(mags):.3f} log-odds per TRAIN s.d."
    )


def _layer_effect(rows: Sequence[Mapping[str, object]], key: str, name: str) -> list[str]:
    ran = [r for r in rows if r[f"{key}_test_n"] != ""]
    n = len(rows)
    if not ran:
        return [f"- {name}: not run in any of the {n} seeds."]
    helped = sum(
        1
        for r in ran
        if float(r[f"{key}_test_avg_r"]) > float(r["base_test_avg_r"])  # type: ignore[arg-type]
    )

    def m(field: str) -> str:
        return _mean_n([r[field] for r in ran])

    insample = ""
    if key == "ml":
        insample = (
            " (the D8 out-of-sample gate; in-sample full TRAIN, context only: "
            f"{mean_text([r.get('ml_train_insample_avg_r', '') for r in ran])})"
        )
    return [
        f"- {name} ran in {len(ran)}/{n} seeds. Mean avg R, base -> layer: TRAIN "
        f"{mean_text([r['base_train_avg_r'] for r in ran])} -> "
        f"{mean_text([r[f'{key}_train_avg_r'] for r in ran])}{insample} | TEST "
        f"{mean_text([r['base_test_avg_r'] for r in ran])} -> "
        f"{mean_text([r[f'{key}_test_avg_r'] for r in ran])}; its TEST avg R beat the base in "
        f"{rate(helped, len(ran))} of those seeds.",
        f"  Per seed, mean counts TRAIN | TEST: signals vetoed {m(f'{key}_train_vetoed')} | "
        f"{m(f'{key}_test_vetoed')}; entries at reduced risk {m(f'{key}_train_reduced')} | "
        f"{m(f'{key}_test_reduced')}; base trades absent from the layer journal "
        f"{m(f'{key}_train_base_only')} | {m(f'{key}_test_base_only')}; layer trades absent "
        f"from the base journal {m(f'{key}_train_layer_only')} | {m(f'{key}_test_layer_only')}.",
    ]


def calibration_console_lines(rows: Sequence[Mapping[str, object]]) -> list[str]:
    """One console line per candidate: mean TRAIN | TEST avg R and n, gate reach, ROBUST."""
    n = len(rows)
    out = []
    for key, name in CANDIDATES:
        reached = sum(1 for r in rows if _truthy(r[f"{key}_reached_gate"]))
        out.append(
            f"{name}: mean TRAIN avg R {mean_text([r[f'{key}_train_avg_r'] for r in rows])} "
            f"(n {_mean_n([r[f'{key}_train_n'] for r in rows])}) | TEST avg R "
            f"{mean_text([r[f'{key}_test_avg_r'] for r in rows])} (n "
            f"{_mean_n([r[f'{key}_test_n'] for r in rows])}); reached the TEST gate "
            f"{rate(reached, n)}; ROBUST {rate(count(rows, f'{key}_label', 'ROBUST'), n)}"
        )
    return out


def world_findings(world: str, rows: Sequence[Mapping[str, object]]) -> list[str]:
    n = len(rows)
    robust = sum(1 for r in rows if _truthy(r["verdict_robust"]))
    meaning = _ROBUST_MEANING.get(world, "")
    lines = [
        f"- Verdict ROBUST (any of the 4 pre-registered candidates, the family-wise rate)"
        f"{meaning}: {rate(robust, n)}.",
    ]
    for key, name in CANDIDATES:
        lines.append(
            f"- {name} labelled ROBUST{meaning}: {rate(count(rows, f'{key}_label', 'ROBUST'), n)}."
        )
    if world == "decay":
        t_only = count(rows, "base_label", "TRAIN-ONLY")
        untested = count(rows, "base_label", "UNTESTED")
        lines += [
            f"- Baseline labelled TRAIN-ONLY: {rate(t_only, n)}; UNTESTED: {rate(untested, n)}.",
            f"- Baseline labelled TRAIN-ONLY or UNTESTED (the decay is caught or at least not "
            f"passed): {rate(t_only + untested, n)}.",
        ]
    not_fitted = sum(1 for r in rows if r["ml_fit_error"])
    if not_fitted:
        lines.append(
            f"- ML layer NOT fitted (InsufficientData, labelled UNTESTED, no fall-back) in "
            f"{not_fitted}/{n} seeds."
        )
    lines += _layer_effect(rows, "ml", "ML layer (`base+ml`)")
    lines += _layer_effect(rows, "guard", "Expectancy guard (`base+guard`)")
    hour = ml_hour_line(rows)
    if hour is not None:
        lines.append(hour)
    viol = sum(int(r["violations"]) for r in rows)  # type: ignore[call-overload]
    lines.append(f"- Invariant violations over all backtests of all seeds: {viol}.")
    return lines


def _num(value: object, digits: int = 3) -> str:
    return sr(float(value), digits) if value not in ("", None) else "n/a"  # type: ignore[arg-type]


def _n(value: object) -> str:
    return "n/a" if value in ("", None) else str(value)


PER_SEED_HEADER = (
    "| seed | base TRAIN n | base TRAIN avg R | base TEST n | base TEST avg R | base TEST adj LB "
    "| base label | selected | selected TRAIN n | selected TRAIN avg R | selected TEST n | "
    "selected TEST avg R | selected label | ML TRAIN n | ML TRAIN avg R (out-of-sample gate) | "
    "ML TEST n | ML TEST avg R | ML label | guard TRAIN n | guard TRAIN avg R | guard TEST n | "
    "guard TEST avg R | guard label | verdict ROBUST | violations |"
)


def per_seed_lines(rows: Sequence[Mapping[str, object]]) -> list[str]:
    lines = [
        "## Per seed (TRAIN and TEST side by side)",
        "",
        PER_SEED_HEADER,
        "|---:|---:|---:|---:|---:|---:|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---|"
        "---:|---:|---:|---:|---|---|---:|",
    ]
    for r in rows:
        cells = [
            str(r["seed"]),
            _n(r["base_train_n"]),
            _num(r["base_train_avg_r"]),
            _n(r["base_test_n"]),
            _num(r["base_test_avg_r"]),
            _num(r["base_test_adj_lb"]),
            str(r["base_label"]),
            f"`{r['selected'] or '-'}`",
            _n(r["selected_train_n"]),
            _num(r["selected_train_avg_r"]),
            _n(r["selected_test_n"]),
            _num(r["selected_test_avg_r"]),
            str(r["selected_label"]),
            _n(r["ml_train_n"]),
            _num(r["ml_train_avg_r"]),
            _n(r["ml_test_n"]),
            _num(r["ml_test_avg_r"]),
            str(r["ml_label"]),
            _n(r["guard_train_n"]),
            _num(r["guard_train_avg_r"]),
            _n(r["guard_test_n"]),
            _num(r["guard_test_avg_r"]),
            str(r["guard_label"]),
            "yes" if _truthy(r["verdict_robust"]) else "no",
            str(r["violations"]),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def label_frequency_table(rows: Sequence[Mapping[str, object]]) -> list[str]:
    n = len(rows)
    lines = [
        "| walk-forward label (TRAIN + TEST) | "
        + " | ".join(name for _, name in CANDIDATES)
        + " |",
        "|---|" + "---:|" * len(CANDIDATES),
    ]
    for lab in (*LABELS, "NONE"):
        counts = [count(rows, f"{key}_label", lab) for key, _ in CANDIDATES]
        if lab == "NONE" and not any(counts):
            continue
        lines.append(f"| {lab} | " + " | ".join(f"{c}/{n}" for c in counts) + " |")
    return lines


def seed_range(rows: Sequence[Mapping[str, object]]) -> str:
    """``"seeds 1-50"`` (or ``"seeds 1001-1050"`` with a seed offset) of the rows run."""
    seeds = sorted(int(r["seed"]) for r in rows)  # type: ignore[call-overload]
    if not seeds:
        return "no seeds"
    if seeds == list(range(seeds[0], seeds[-1] + 1)):
        return f"seeds {seeds[0]}-{seeds[-1]}" if len(seeds) > 1 else f"seed {seeds[0]}"
    return "seeds " + ", ".join(str(x) for x in seeds)


SEED_NOTE = (
    "The synthetic worlds share their noise for a given seed (only the planted drift differs), "
    "so calibrations of different worlds are independent only on disjoint seed ranges "
    "(`--seed-offset`)."
)


def _calibration_intro(
    world: str, years: float, rows: Sequence[Mapping[str, object]], strength: object
) -> list[str]:
    truth, prediction = WORLD_TRUTH[world]
    strength_text = "" if strength in ("", None) else f", effect strength {strength} sigma"
    if strength_text:
        truth += f" (This calibration overrides the strength: {strength} sigma per candle.)"
    return [
        f"# Calibration: synthetic world `{world}`{strength_text}, {seed_range(rows)}",
        "",
        WIN_RATE_STATEMENT,
        "",
        f"Each seed is a full run of the pipeline ({years:g} years of 4H candles, 70/30 "
        "walk-forward): baseline, discovery (K = 13 variants, one selected on TRAIN), the ML "
        "layer and the expectancy guard, i.e. m = 4 pre-registered TEST looks per seed. "
        "**Synthetic results are verification of the methodology, not evidence about real "
        "markets.** Each label below combines that candidate's TRAIN and TEST windows "
        f"(`metrics.label`). {multiplicity_rule(4, 0.05)} The ML layer's TRAIN columns are "
        "its out-of-sample TRAIN gate (CONTRACT v4 D8: a purged 70/30 inner split of TRAIN), "
        "the window its label judges; its in-sample full-TRAIN figure is context only. "
        + SEED_NOTE,
        "",
        f"- Ground truth: {truth}",
        f"- What the ground truth predicts: {prediction}",
    ]


def render_calibration(
    world: str,
    years: float,
    rows: Sequence[Mapping[str, object]],
    cfg: StrategyConfig | None = None,
) -> str:
    strength = rows[0].get("effect_strength", "") if rows else ""
    lines = [
        *_calibration_intro(world, years, rows, strength),
        *cost_lines(cfg or DEFAULT_CFG),
        "",
        "## Label frequencies",
        "",
        *label_frequency_table(rows),
        "",
        "## Ground truth vs labels (rates with 90% Wilson intervals)",
        "",
        *world_findings(world, rows),
        "",
        DD_RULE,
        "",
        *candidate_rate_table(rows),
        "",
        *per_seed_lines(rows),
        "",
        *ADOPTION_PATH_LINES,
        "",
        "## Risk disclaimer",
        "",
        DISCLAIMER,
    ]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------- power curve
OBS = "mean observed TEST avg R over seeds"


def power_points(
    by_strength: Mapping[float, Sequence[Mapping[str, object]]], key: str = "base"
) -> list[tuple[float, float, float, int, int]]:
    """``(strength, mean TEST avg R over seeds, detection rate, k ROBUST, n seeds)``."""
    out = []
    for strength in sorted(by_strength):
        rows = by_strength[strength]
        stats = mean_ci([r[f"{key}_test_avg_r"] for r in rows])
        k = count(rows, f"{key}_label", "ROBUST")
        out.append((strength, stats[0] if stats else math.nan, k / len(rows), k, len(rows)))
    return out


def mde(points: Sequence[tuple[float, float, float, int, int]], power: float) -> str:
    """Minimum detectable effect at ``power``, read along INCREASING strength: the mean
    observed TEST avg R over seeds (and the strength) where the detection rate first reaches
    ``power``, linearly interpolated between the two tested strengths around the crossing.

    The mean observed TEST avg R over seeds estimates the expected TEST avg R of one run at
    that strength; it is an average of estimates, not the true parameter."""
    pts = sorted(points)
    if not pts:
        return "not computable (no strengths)"
    first = pts[0]
    if first[2] >= power:
        return (
            f"at or below the smallest tested effect (strength {first[0]:g} sigma, {OBS} "
            f"{sr(first[1])}R, detection {first[2]:.0%})"
        )
    for a, b in pairwise(pts):
        if a[2] < power <= b[2]:
            f = (power - a[2]) / (b[2] - a[2])
            e, x = a[1] + f * (b[1] - a[1]), a[0] + f * (b[0] - a[0])
            return (
                f"~{sr(e)}R {OBS} (strength ~{x:.2f} sigma, "
                f"interpolated between {a[0]:g} and {b[0]:g} sigma)"
            )
    best = max(pts, key=lambda p: (p[2], -p[0]))
    return (
        f"not reached at any tested strength (highest detection {best[2]:.0%} at "
        f"{best[0]:g} sigma, {OBS} {sr(best[1])}R)"
    )


def power_table(by_strength: Mapping[float, Sequence[Mapping[str, object]]]) -> list[str]:
    lines = [
        "| effect strength (sigma per candle) | seeds | mean observed TEST avg R over seeds, "
        "base [90% CI] | pooled TEST R per trade, base | mean TRAIN avg R, base | mean "
        "TEST n, base | mean TEST max DD % realised / MTM / limit, base | base dd_ok | base "
        "ROBUST = detection rate | base reached the TEST gate | ROBUST given the gate | any of the "
        "4 candidates ROBUST |",
        "|---:|---:|---|---:|---:|---:|---|---|---|---|---|---|",
    ]
    for strength in sorted(by_strength):
        rows = by_strength[strength]
        n = len(rows)
        stats = mean_ci([r["base_test_avg_r"] for r in rows])
        tr = f"{sr(stats[0])} [{sr(stats[1])}, {sr(stats[2])}]" if stats else "n/a"
        tot_n = sum(int(r["base_test_n"]) for r in rows if r["base_test_n"] != "")  # type: ignore[call-overload]
        tot_r = math.fsum(float(r["base_test_total_r"]) for r in rows)  # type: ignore[arg-type]
        pooled = sr(tot_r / tot_n) if tot_n else "n/a"
        reached = [r for r in rows if _truthy(r["base_reached_gate"])]
        k = count(rows, "base_label", "ROBUST")
        k_reached = count(reached, "base_label", "ROBUST")
        any_robust = sum(1 for r in rows if _truthy(r["verdict_robust"]))
        lines.append(
            f"| {strength:g} | {n} | {tr} | {pooled} | "
            f"{mean_text([r['base_train_avg_r'] for r in rows])} | "
            f"{_mean_n([r['base_test_n'] for r in rows])} | {_dd_means(rows, 'base')} | "
            f"{rate(sum(1 for r in rows if _truthy(r['base_dd_ok'])), n)} | {rate(k, n)} | "
            f"{rate(len(reached), n)} | {rate(k_reached, len(reached))} | {rate(any_robust, n)} |"
        )
    return lines


def _power_seeds(by_strength: Mapping[float, Sequence[Mapping[str, object]]]) -> str:
    ranges = sorted({seed_range(rows) for rows in by_strength.values()})
    return "every strength ran " + " / ".join(ranges)


def render_power(
    world: str,
    years: float,
    by_strength: Mapping[float, Sequence[Mapping[str, object]]],
    cfg: StrategyConfig | None = None,
) -> str:
    points = power_points(by_strength)
    seeds = sorted({len(v) for v in by_strength.values()})
    mde50, mde80 = mde(points, 0.5), mde(points, 0.8)
    lines = [
        f"# Power curve: synthetic world `{world}` at {len(by_strength)} effect strengths",
        "",
        WIN_RATE_STATEMENT,
        "",
        f"Each cell is {', '.join(str(s) for s in seeds)} seeds of the full pipeline ({years:g} "
        "years of 4H candles, 70/30 walk-forward, the 4 pre-registered candidates) with the "
        "planted drift set by `effect_strength` (sigma units per candle); "
        f"{_power_seeds(by_strength)}. "
        f"{multiplicity_rule(4, 0.05)} **Synthetic results are verification of the methodology, "
        "not evidence about real markets.**",
        "",
        "The detection rate is the share of seeds whose BASELINE is labelled ROBUST, plotted "
        f"against the {OBS} of the baseline at that strength (the mean over seeds of each "
        "run's observed TEST avg R: an estimate of what one run expects to see, not the true "
        "parameter). Rates carry 90% Wilson intervals.",
        "",
        *cost_lines(cfg or DEFAULT_CFG),
        "",
        DD_RULE,
        "",
        *power_table(by_strength),
        "",
        "## Minimum detectable effect (baseline, this sample size)",
        "",
        f"- 50% power: {mde50}.",
        f"- 80% power: {mde80}.",
        "- Read along increasing strength with linear interpolation between tested strengths; "
        "each detection rate is itself an estimate (see its Wilson interval), so the MDE is "
        "approximate. A stronger drift also pushes RSI above 70 more often, so R2 admits fewer "
        "signals and the TEST trade count (mean TEST n column) can fall below the 30 that "
        "ROBUST needs: detection can drop again at high strengths, and the MDE holds only for "
        "TEST samples of about the size shown.",
        "- **An edge below the MDE will be labelled UNTESTED on real data however real it is:** "
        "with ~6 years of 4H data and a 30% TEST window the baseline takes only about the TEST "
        "trade counts shown above, and the multiplicity-corrected, block-bootstrapped lower "
        "bound cannot separate a smaller edge from zero. UNTESTED means 'not shown', never "
        "'shown to be absent'.",
        "",
        *ADOPTION_PATH_LINES,
        "",
        "## Risk disclaimer",
        "",
        DISCLAIMER,
    ]
    return "\n".join(lines) + "\n"
