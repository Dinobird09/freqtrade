# Results summary: synthetic verification of the research methodology

**NO real BTC/ETH/BNB data was backtested.** The build sandbox has no network access and no
`ccxt`, so every number below comes from `synthetic.py` worlds with a KNOWN ground truth.
They test the methodology: does the pipeline find an edge where one was planted, and does it
refuse to find one where there is none? They are **not evidence about real markets**, and no
variant here can be adopted on the strength of them: every adoption record written below is
`synthetic:<world>:<seed>` and `adoption check --stage HUMAN_REVIEW` blocks it with
`ADOPT_provenance`. The exact commands for running the same pipeline on real data are at the
end.

No win rate is promised or targeted anywhere in this package. A win rate without its
reward:risk is meaningless: at the mandatory 2:1 a strategy breaks even near 33% winners, and
a backtest win rate near 90% points to curve-fitting or look-ahead. The target is positive
expectancy (avg R per trade, net of fees and slippage) that survives the 70/30 chronological
walk-forward with controlled drawdown. Win rate appears in the reports only as context.

Everything in this folder was regenerated from the current code (CONTRACT.md v3). The
previous cycle's folders were deleted first. Every folder holds a `run.log` written by
`run_research` itself: the exact command (argv) and the console summary.

## The rules every label below uses (fixed in advance, never tuned on TEST)

- **Four pre-registered candidates get one TEST look each (m = 4):** `base` (the mandate
  config), the discovery-selected variant (highest TRAIN t-stat of 12 legal tightenings,
  chosen before any TEST backtest), `base+ml` (logistic veto layer fitted on purged TRAIN
  candidates) and `base+guard` (`expectancy_guard=True`). The 11 other grid variants and the
  regime-OFF test variant are shown as `context: <label>, not judged`.
- **ROBUST** needs all of: >= 30 trades in each window; TRAIN avg R > 0; TEST avg R > 0; and
  a one-sided lower bound of the TEST mean at confidence 1 - 0.05/4 = **98.75%** above zero
  for BOTH an iid bootstrap and a calendar-month block bootstrap of the TEST R (4000 seeded
  resamples each; the more conservative bound is used). Bonferroni over the four looks keeps
  the chance that ANY candidate is falsely called ROBUST at about 5% or less when none has an
  edge. The block bootstrap resamples whole calendar months, so trades that cluster in one
  regime count as one piece of evidence, not many.
- A positive TEST mean that fails only the bound is **UNTESTED** ("positive but not
  distinguishable from zero after multiplicity correction"). TRAIN avg R <= 0 is NO-EDGE,
  TEST avg R <= 0 is TRAIN-ONLY.
- **Drawdown (C5):** every result reports the realised (closed-trade) and the mark-to-market
  (open positions at each 4H close) max drawdown. `dd_ok` = TEST MTM max drawdown <= min(20%,
  the 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST
  length). The adoption path needs ROBUST AND `dd_ok`.
- **Layers (C1):** a layer can only VETO an entry that passed every mandatory rule; it never
  approves one a rule denies. A veto (or the guard's halved risk) can free R6 budget or change
  R9 state, which admits other rule-compliant trades the base never took. Every report counts,
  per window: signals vetoed, base trades absent from the layer's journal, and layer trades
  absent from the base journal (matched by pair and signal time).

## Cost assumptions used by every run below

- Fee **0.10% of notional per side** (Binance spot taker, `--fee-rate 0.001`), charged on
  entry AND exit; slippage **0.05%** on market fills (`--slippage-pct 0.05`), none on
  take-profit limits; exchange `binance`.
- Cost-aware sizing (CONTRACT.md v2 A1): a clean stop is exactly -1R and a take-profit nets
  exactly +2R after fees. At these costs fees alone averaged 0.06R per baseline trade in every
  seed-1 run (median stop distance 3.5-3.8% of the entry; REPORT.md section 2).
- **Coinbase Advanced Trade taker fees at low tiers are several times Binance's.** A Coinbase
  run must pass the real tier (`--fee-rate 0.006` = 0.6% per side, `--exchange-id coinbase`),
  or every number is optimistic. Fees scale with the rate, so at 0.6% per side fees alone
  would cost about 0.36R per trade instead of 0.06R (an estimate, not a committed run), which
  is most of the edges measured below.

## What was run (all executed; the outputs are in this folder)

| folder | command (from the repo root) | wall time (4 CPUs) |
|---|---|---:|
| `synthetic_null_s1/` | `python -m research.trendbot.run_research --synthetic null --seed 1 --now 2026-09-27T12:00:00Z --out-dir research/results/synthetic_null_s1` | 36 s |
| `synthetic_planted_s1/` | the same with `--synthetic planted` (`--out-dir research/results/synthetic_planted_s1`) | 37 s |
| `synthetic_decay_s1/` | the same with `--synthetic decay` | 36 s |
| `synthetic_hour_edge_s1/` | the same with `--synthetic hour_edge` | 37 s |
| `synthetic_zero_edge_s1/` | the same with `--synthetic zero_edge` | 35 s |
| `calibration_zero_edge/` | `python -m research.trendbot.run_research --synthetic zero_edge --calibrate-seeds 50 --workers 4 --out-dir research/results/calibration_zero_edge` | 352 s |
| `calibration_decay/` | the same with `--synthetic decay --calibrate-seeds 50` | 353 s |
| `calibration_null/` | the same with `--synthetic null --calibrate-seeds 20` | 134 s |
| `calibration_planted/` | the same with `--synthetic planted --calibrate-seeds 20` | 136 s |
| `calibration_hour_edge/` | the same with `--synthetic hour_edge --calibrate-seeds 20` | 129 s |
| `power_planted/` | `python -m research.trendbot.run_research --synthetic planted --calibrate-seeds 20 --power-strengths 0.15 0.3 0.45 0.6 0.75 0.9 1.05 --workers 4 --out-dir research/results/power_planted` | 923 s |

(The five seed-1 runs ran in parallel.) Each run uses 6 years of 4H candles (13,140 per pair
for BTC/USDT, ETH/USDT and BNB/USDT). The split falls at 70% of the common range
(2023-03-14T00:00Z): 9,198 TRAIN and 3,942 TEST candles per pair.

**Determinism.** After all runs, the five seed-1 runs and `calibration_null` were re-run with
the same commands into the same folders and compared with a copy of the first run:
`diff -r` found no difference in any of the 5 x 25 + 3 files (reports, journals, review
packs, model file, adoption records with their sha256 fields, CSVs, run.log). A second check:
the power curve's 0.15-sigma cell reproduces `calibration_zero_edge` seeds 1-20 exactly
(zero_edge IS the planted mechanism at 0.15 sigma), base TRAIN and TEST avg R identical in
20/20 seeds.

## Seed 1: ground truth vs what the pipeline labelled

TRAIN | TEST avg R (n) per pre-registered candidate. Every adoption record passes
`adoption check --stage WALK_FORWARD` and is BLOCKED at `HUMAN_REVIEW` by `ADOPT_provenance`
(plus `ADOPT_walk_forward` wherever the label is not ROBUST); REPORT.md section 11 prints the
commands.

| world (truth) | baseline | discovery-selected | base+ml | base+guard | verdict | right? |
|---|---|---|---|---|---|---|
| null (no edge) | NO-EDGE: -0.138 (98) \| -0.121 (58) | `rr2.5_vol2_rsi50-70` NO-EDGE: -0.086 (81) \| -0.143 (49) | UNTESTED: +0.267 (45) \| -0.294 (17) | NO-EDGE: -0.183 (108) \| -0.121 (58) | No robust result | yes |
| zero_edge (net ~0) | NO-EDGE: -0.004 (94) \| -0.050 (60) | `rr2_vol2_rsi55-70` TRAIN-ONLY: +0.048 (75) \| -0.020 (49) | UNTESTED: +0.333 (9) \| +0.500 (6) | NO-EDGE: -0.044 (102) \| -0.050 (60) | No robust result | yes |
| decay (edge in TRAIN only) | TRAIN-ONLY: +0.307 (114) \| -0.143 (56) | `rr3_vol2_rsi50-70` TRAIN-ONLY: +0.742 (70) \| -0.043 (46) | TRAIN-ONLY: +0.590 (88) \| -0.073 (55) | TRAIN-ONLY: +0.307 (114) \| -0.143 (56) | No robust result | yes |
| planted (edge everywhere) | UNTESTED: +0.296 (115) \| +0.412 (51) | `rr3_vol2_rsi50-70` **ROBUST**: +0.836 (73) \| +0.829 (35) | UNTESTED: +0.489 (94) \| +0.235 (34) | UNTESTED: +0.296 (115) \| +0.412 (51) | ROBUST (selected) | yes, 1 of 4 |
| hour_edge (edge at 12-20 UTC) | NO-EDGE: -0.044 (100) \| +0.050 (60) | `rr3_vol2_rsi50-70` UNTESTED: +0.203 (72) \| +0.436 (39) | UNTESTED: +0.299 (69) \| +0.295 (44) | NO-EDGE: -0.057 (111) \| +0.082 (61) | No robust result | missed |

In planted seed 1 the baseline's TEST +0.412R (n=51) has 98.75% lower bounds of -0.059R (iid)
and -0.125R (block): a real edge, labelled UNTESTED because 51 trades cannot separate it from
zero under the multiplicity rule (see the power curve below).

## Calibration: ground truth vs labels over seeds (90% Wilson intervals)

"Verdict" = any of the 4 candidates ROBUST (the family-wise rate). The TEST gate is reached
when TRAIN avg R > 0 and both windows hold >= 30 trades; the bootstrap bound decides from
there.

| world | seeds | truth | verdict ROBUST | baseline ROBUST | base reached the TEST gate | base mean avg R TRAIN \| TEST | base dd_ok |
|---|---:|---|---|---|---|---|---|
| null | 20 | no edge | **0/20 = 0% (0%-12%)** = FP | 0/20 (0%-12%) | 3/20 (6%-32%) | -0.107 \| -0.021 | 19/20 |
| zero_edge | 50 | net ~0 (H0 boundary) | **1/50 = 2% (0%-8%)** = FP | 0/50 (0%-5%) | 31/50 (50%-72%) | +0.035 \| +0.027 | 46/50 |
| decay | 50 | no edge in TEST | **0/50 = 0% (0%-5%)** = FP | 0/50 (0%-5%) | 49/50 (92%-100%) | +0.458 \| -0.095 | 9/50 |
| planted | 20 | edge (0.7 sigma) | 10/20 = 50% (33%-67%) = detection | 8/20 = 40% (24%-58%) | 20/20 (88%-100%) | +0.442 \| +0.445 | 20/20 |
| hour_edge | 20 | edge at 12-20 UTC only | 5/20 = 25% (13%-43%) | 0/20 (0%-12%) | 18/20 (74%-97%) | +0.198 \| +0.201 | 18/20 |

Per candidate (ROBUST all seeds; ROBUST given the TEST gate was reached):

| world | discovery-selected | base+ml | base+guard |
|---|---|---|---|
| null | 0/20; 0/8 | 0/20; 0/3 | 0/20; 0/3 |
| zero_edge | 0/50; 0/39 | 1/50 (0%-8%); 1/32 (1%-13%) | 0/50; 0/31 |
| decay | 0/50; 0/46 | 0/50; 0/49 | 0/50; 0/49 |
| planted | 9/20 (28%-63%); 9/18 | 7/20 (20%-53%); 7/20 | 8/20 (24%-58%); 8/20 |
| hour_edge | 3/20 (6%-32%); 3/16 | 5/20 (13%-43%); 5/18 | 0/20; 0/19 |

What this shows:

- **False positives are controlled at the H0 boundary and under decay.** Zero_edge (a real
  pre-cost edge that costs eat) produced 1 ROBUST in 50 seeds (2%, 90% CI 0-8%), and decay
  (a TRAIN edge that vanishes at the split) produced 0 in 50 (0-5%), although decay reached
  the TEST gate in 49/50 seeds and its baseline TEST avg R was as high as +0.50R in one seed.
  The cycle-1 rule (a 90% two-sided iid CI per look, three looks) had a 1/10 decay false
  positive at exactly that seed (decay seed 3: TRAIN +0.360R, TEST +0.501R, n=39); under the
  m = 4 rule its 98.75% bounds are -0.038R (iid) and -0.013R (block), so it is UNTESTED. The one zero_edge ROBUST is `base+ml` in seed 38 (TEST +0.552R, n=58, iid bound
  +0.138R, block +0.125R). By the world's definition it is a false positive; a filtered subset
  of a world with a real pre-cost edge may also carry a small genuine net edge (the ML layer
  averaged +0.060R on zero_edge TEST vs +0.027R for the base).
- **Decay is caught:** 30/50 TRAIN-ONLY and 20/50 UNTESTED for the baseline (50/50 = 100%
  not passed, 90% CI 95-100%). The drawdown rule catches it too: `dd_ok` failed in 41/50
  decay seeds, because the TEST (null) drawdowns (MTM mean 10.9%) exceed what the TRAIN
  sequences with an edge predict (limit mean 8.3%).
- **Power is the price of that control.** The planted edge (+0.445R per trade in TEST) was
  detected by the baseline in only 8/20 seeds; all 12 misses are UNTESTED with TEST avg R
  +0.14 to +0.51R (never TRAIN-ONLY or NO-EDGE). The block bootstrap was the stricter bound
  in 13/20 planted seeds, and decided the label alone (iid > 0, block <= 0) in 1 of them.
- **Hour_edge:** the hour-blind baseline was never ROBUST (0/20); the ML layer, which can
  learn the hour, was ROBUST in 5/20 and was the reason for all 5 verdicts.

## Power curve and the minimum detectable effect (`power_planted/POWER.md`)

The planted mechanism at seven strengths, 20 seeds each. "True mean TEST expectancy" is the
mean over seeds of the baseline's TEST avg R (what one run expects to see), with its 90%
interval.

| strength (sigma/candle) | true mean TEST expectancy, base | mean TRAIN avg R | mean TEST n | base ROBUST = detection | any of 4 ROBUST |
|---:|---|---:|---:|---|---|
| 0.15 | +0.066 [-0.009, +0.141] | +0.037 | 45.8 | 0/20 = 0% (0%-12%) | 0/20 (0%-12%) |
| 0.30 | +0.145 [+0.070, +0.221] | +0.163 | 45.9 | 1/20 = 5% (1%-20%) | 1/20 (1%-20%) |
| 0.45 | +0.252 [+0.193, +0.311] | +0.272 | 50.3 | 2/20 = 10% (3%-26%) | 4/20 (9%-38%) |
| 0.60 | +0.351 [+0.278, +0.425] | +0.388 | 50.1 | 4/20 = 20% (9%-38%) | 7/20 (20%-53%) |
| 0.75 | +0.422 [+0.357, +0.486] | +0.471 | 48.6 | 6/20 = 30% (16%-48%) | 11/20 (37%-72%) |
| 0.90 | +0.582 [+0.508, +0.656] | +0.590 | 45.9 | 14/20 = 70% (52%-84%) | 17/20 (68%-94%) |
| 1.05 | +0.628 [+0.554, +0.701] | +0.672 | 40.0 | 15/20 = 75% (57%-87%) | 16/20 (62%-91%) |

- **Minimum detectable effect of the baseline at this sample size (~46-50 TEST trades):**
  50% power at about **+0.50R** per trade (strength ~0.83 sigma, interpolated between 0.75
  and 0.9); **80% power is not reached** at any tested strength (75% at +0.63R). Stronger
  drift pushes RSI above 70, so R2 admits fewer signals and TEST n falls (40 at 1.05 sigma);
  detection cannot rise much further this way (an uncommitted 4-seed pilot at 2.0 sigma had
  a mean TEST n of 14.5, below the 30 ROBUST needs).
- **An edge below the MDE will be labelled UNTESTED on real data however real it is.** With
  6 years of 4H data, a 30% TEST window and the multiplicity-corrected block-bootstrapped
  bound, an edge of +0.1 to +0.4R per trade (plausible for a real market, and far larger than
  most) is detected in 5-30% of runs at best. UNTESTED means "not shown", never "shown to be
  absent". More history (or a longer TEST window) is the only honest way to raise power; the
  label rule is fixed and will not be loosened to find more.

## ML layer and expectancy guard: TRAIN | TEST side by side, vetoes and added trades

Means over seeds; TRAIN of `base+ml` is in-sample (the filter was fitted on it). Counts are
per seed, TRAIN | TEST: signals vetoed; base trades absent from the layer journal; layer
trades absent from the base journal (the trades a veto or a halved risk ADMITTED).

| world | base avg R TRAIN \| TEST | base+ml avg R TRAIN \| TEST | ML vetoed | ML base-only | ML layer-only | base+guard avg R TRAIN \| TEST | guard entries at half risk | guard layer-only |
|---|---|---|---|---|---|---|---|---|
| null | -0.107 \| -0.021 | -0.014 \| -0.207 | 195.8 \| 90.5 | 72.2 \| 31.4 | 26.4 \| 11.0 | -0.097 \| -0.020 | 29.8 \| 0.5 | 10.2 \| 0.1 |
| zero_edge | +0.035 \| +0.027 | +0.139 \| +0.060 | 107.0 \| 52.2 | 52.9 \| 25.6 | 27.4 \| 12.9 | +0.042 \| +0.025 | 22.4 \| 0.6 | 7.2 \| 0.1 |
| decay | +0.458 \| -0.095 | +0.506 \| -0.081 | 11.8 \| 3.9 | 11.3 \| 4.3 | 5.8 \| 3.7 | +0.459 \| -0.097 | 3.8 \| 1.0 | 0.6 \| 0.5 |
| planted | +0.442 \| +0.445 | +0.483 \| +0.435 | 11.1 \| 5.2 | 10.3 \| 4.8 | 5.2 \| 2.2 | +0.441 \| +0.445 | 2.1 \| 0.1 | 0.2 \| 0.0 |
| hour_edge | +0.198 \| +0.201 | +0.420 \| +0.399 | 53.9 \| 23.2 | 43.1 \| 18.5 | 21.2 \| 8.8 | +0.207 \| +0.203 | 13.1 \| 0.8 | 2.5 \| 0.1 |

- **The ML layer helps only where there is something to learn.** In hour_edge it doubled TEST
  expectancy (+0.201 -> +0.399R, better than the base in 18/20 seeds, hour terms the largest
  coefficient in 12/20 fits) and produced all 5 ROBUST verdicts. In null it looked better in
  TRAIN (in-sample, -0.107 -> -0.014R) and was worse in TEST (-0.207R): the in-sample gain
  is fitting noise. In planted, where every spike already carries the edge, it removed trades
  without improving TEST (+0.445 -> +0.435R).
- **Vetoes do admit other trades** (C1): in null the ML layer's journal held 26.4 TRAIN and
  11.0 TEST trades per seed that the base never took, because each veto left R6 budget free
  for another pair. A layer's journal is not a subset of the base's.
- **The guard rarely binds after its warm-up:** it halves a pair's risk while that pair's
  last 20 closed trades average below 0R, so it acts mostly in TRAIN (29.8 half-risk entries
  per null seed, 2.1 in planted) and almost never in the 1.8-year TEST window (a fresh
  journal needs 20 trades per pair first). The halved risk frees R6 budget, admitting extra
  trades (10.2 per null TRAIN). Its TEST avg R equals the base's within 0.002R in every world
  and it was never ROBUST where the base was not: there is no evidence it adds expectancy.

## Where the methodology is weak (honest list)

1. **Low power, by design of the rule.** See the power curve: a +0.4R edge is detected in
   about a third of runs. On real data expect UNTESTED far more often than ROBUST.
2. **Calibration samples are small and paired.** 0/20 in null only bounds the false-positive
   rate below 12%; zero_edge and decay (50 seeds) bound it near 5-8%. All worlds share the
   same noise for a given seed (only the planted drift differs), so decay seed k's TEST
   window is null seed k's: null and decay are not independent draws.
3. **The drawdown rule compares a MTM drawdown with a closed-trade bootstrap.** The TEST MTM
   drawdown includes intra-trade dips that a bootstrap of closed-trade returns does not, so
   `dd_ok` is conservative. It still passed in 20/20 planted seeds (MTM mean 5.4% vs limit
   8.6%) and failed in 41/50 decay seeds, so in these worlds it separates a regime change
   from a stable edge; it is only as good as the TRAIN window is representative.
4. **The synthetic effect is a positive-control fixture** (hand-calibrated in `synthetic.py`),
   not an estimate of any real edge. Real edges, if any, are likely weaker, and Coinbase
   low-tier fees would remove edges of this size.
5. **One split.** Every label rests on a single chronological 70/30 split; a ROBUST label is
   followed by a human review of every trade and at least 2 weeks of testnet, never by
   money.

No invariant violation was found in any backtest of any run (sections 8 of every REPORT.md,
and 0 in every calibration and power row).

## How to run this on REAL data (needs a machine with network access)

```bash
# from the repo root, on a networked machine
pip install ccxt
python -m research.trendbot.fetch_data --exchange binance \
    --pairs BTC/USDT ETH/USDT BNB/USDT --timeframe 4h --since 2019-01-01 --out research/data

# news calendar (R5), research/data/events.csv, header:
#   time_utc,scope,impact,kind,note,known_from_utc
# from a real economic calendar (high-impact macro, scope ALL) and Binance announcements
# (BNB burns / launchpools, scope BNB). known_from_utc = when the event became public:
# leave it empty for scheduled kinds (macro, unlock, bnb_burn, launchpool: known in advance)
# and for unscheduled headlines (regulatory, legal, other: blocked only from their own time);
# fill it when a scheduled event was announced late, e.g. a launchpool announced 12h ahead.
# research/trendbot/events_example.csv shows the format only; it is NOT a real calendar.

# Binance spot: pass YOUR fee tier (0.001 = 0.10% taker) and slippage; --now is optional
python -m research.trendbot.run_research --data-dir research/data \
    --events research/data/events.csv \
    --fee-rate 0.001 --slippage-pct 0.05 --exchange-id binance \
    --out-dir research/results/real_binance

# Coinbase Advanced Trade: low-tier taker fees are several times Binance's (e.g. 0.006 =
# 0.6% per side); BNB/USDT is not listed there.
python -m research.trendbot.fetch_data --exchange coinbase \
    --pairs BTC/USDT ETH/USDT --timeframe 4h --since 2019-01-01 --out research/data/coinbase
python -m research.trendbot.run_research --data-dir research/data/coinbase \
    --pairs BTC/USDT ETH/USDT --events research/data/events.csv \
    --fee-rate 0.006 --slippage-pct 0.05 --exchange-id coinbase \
    --out-dir research/results/real_coinbase

# the EXISTING bot's journal: write a map.json for its real column names first, e.g.
#   {"columns": {"<its pair column>": "pair", "<its entry time>": "entry_ts",
#                "<its exit time>": "exit_ts", "<its entry price>": "entry_price", ...},
#    "exit_reason_values": {"<its stop label>": "SL", "<its target label>": "TP"},
#    "time_format": "iso"}
# (see the research/trendbot/journal.py docstring), then compare a few converted rows with
# the bot's own figures before trusting them
python -m research.trendbot.journal convert --in bot_trades.csv --map map.json \
    --out research/data/bot_journal.csv --fee-rate 0.001 --slippage-pct 0.05
python -m research.trendbot.invariants --journal research/data/bot_journal.csv \
    --data-dir research/data --events research/data/events.csv --fee-rate 0.001
python -m research.trendbot.journal_rules --journal research/data/bot_journal.csv \
    --equity <the bot's current equity>

# independent re-audit of a written journal against the candles with the SAME config the run
# used (config_<variant>.json holds every override, costs included; the TEST journal needs
# --start-ts = the split in ms, REPORT.md section 2)
python -m research.trendbot.invariants \
    --journal research/results/real_binance/journals/base_test.csv \
    --data-dir research/data --events research/data/events.csv --start-ts <split_ms> \
    --config research/results/real_binance/config_base.json   # only if that file exists

# adoption gate: REPORT.md section 11 prints the exact command per record (with --config for
# a non-default config and --model-fingerprint for base+ml); a real record binds the data
# file and events hashes, so WALK_FORWARD -> HUMAN_REVIEW needs ROBUST + dd_ok and nothing
# synthetic
python -m research.trendbot.adoption check \
    --record research/results/real_binance/adoption_base.json --stage HUMAN_REVIEW
python -m research.trendbot.adoption check \
    --record research/results/real_binance/adoption_base_plus_ml.json --stage HUMAN_REVIEW \
    --model-fingerprint <sha256 printed in REPORT.md section 5>
python -m research.trendbot.adoption check \
    --record research/results/real_binance/adoption_base_plus_guard.json --stage HUMAN_REVIEW \
    --config research/results/real_binance/config_base_plus_guard.json
# after a named reviewer fills in reviewer_ok (Y/N) for EVERY row of the review pack:
python -m research.trendbot.adoption hash research/results/real_binance/review_base/trades_review.csv
```

The adoption path is the same for real data and cannot be shortcut: backtest -> walk-forward
(ROBUST + drawdown check) -> a named human reviews EVERY trade in the review pack -> at least
2 weeks on Binance testnet with zero rule violations -> live. The ML variant is adopted as a
(config, model) pair: the live bot loads `model_base_plus_ml.json` with
`MLFilter.from_json(text, expected_fingerprint=<the record's model_fingerprint>)`, which
raises on any mismatch. **Refitting the model changes its fingerprint and restarts the
adoption path at BACKTEST.**

## Risk disclaimer

This is a research and testing tool, not financial advice. Backtests and synthetic worlds are
simplified models, and past or simulated results do not predict future results. Crypto
trading can result in the total loss of the capital used.
