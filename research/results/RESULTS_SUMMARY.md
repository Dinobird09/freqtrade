# Results summary: synthetic verification of the research methodology

**NO real BTC/ETH/BNB market data was backtested, and nothing below is evidence about real
markets.** The build sandbox has no network access and no `ccxt`, so no real candle could be
fetched. Every number in this folder comes from `synthetic.py` worlds whose ground truth is
KNOWN. They test the methodology: does the pipeline find an edge where one was planted, and
does it refuse to find one where there is none? No variant here can be adopted on the
strength of them. Every adoption record written below has provenance
`synthetic:<world>:<seed>`, and `adoption check --stage HUMAN_REVIEW` blocks it with
`ADOPT_provenance`. The exact commands for the same pipeline on real data are at the end
(section 12).

No win rate is promised, targeted or selected on anywhere in this package. A win rate
without its reward:risk is meaningless. At the mandatory 2:1 a strategy breaks even near 33%
winners, and a backtest win rate near 90% points to curve-fitting or look-ahead. The target
is positive expectancy (avg R per trade, net of fees and slippage) that survives the 70/30
chronological walk-forward with controlled drawdown. Win rate appears in the reports only as
context. **Every number below is given as TRAIN | TEST**, side by side.

Everything in this folder was regenerated from scratch by the current code (CONTRACT.md v4).
The previous cycle's folders were deleted first. Every run folder holds a `run.log` written
by `run_research` itself: the exact command (argv), the console summary and the exit code.
Every command exited 0, and no backtest of any run violated an invariant.

## 1. The rules every label uses (fixed in advance, never tuned on TEST)

- **Four pre-registered candidates get a TEST look (m = 4):**
  - `base`: the mandate config;
  - the discovery-selected variant: the highest TRAIN t-stat of 12 legal tightenings, chosen
    before any TEST backtest;
  - `base+ml`: a logistic veto layer fitted on purged TRAIN candidates;
  - `base+guard`: `expectancy_guard=True`.

  The 11 other grid variants and the regime-OFF test variant are shown only as
  `context: <label>, not judged`.
- **ROBUST** needs all of:
  - >= 30 trades in each window;
  - TRAIN avg R > 0;
  - TEST avg R > 0;
  - a one-sided lower bound of the TEST mean above zero at confidence
    1 - 0.05/4 = **98.75%**, for BOTH an iid bootstrap and a calendar-month block bootstrap
    (4000 seeded resamples each; the more conservative bound is used).

  Bonferroni over the four looks keeps the chance that ANY candidate is falsely called
  ROBUST at about 5% or less when none has an edge. A positive TEST mean that fails only the
  bound is **UNTESTED**. TRAIN avg R <= 0 is **NO-EDGE**, and TEST avg R <= 0 is
  **TRAIN-ONLY**.
- **Fitted layers are judged out of sample (D8).** For `base+ml`, the judged TRAIN window
  works like this:
  - the model is fitted on the first 70% of the TRAIN candidates;
  - it is backtested on the last 30% of TRAIN, purged at the inner boundary;
  - the TEST model is then refitted on all of TRAIN.

  The in-sample full-TRAIN figure is context only.
- **Drawdown (C5, D7):** every result reports the realised (closed-trade) and the
  mark-to-market (4H close) max drawdown. `dd_ok` means the TEST MTM max drawdown is at most
  min(**15%**, the 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped
  at the TEST length).
  - **Why the cap is 15%:** at the mandated 1% cluster risk budget, 15% is 15 consecutive
    full-size losses. At the 2:1 break-even win probability p* = 1/3, such a streak has
    probability (2/3)^15 = 0.23%. A TEST drawdown beyond it is therefore inconsistent with
    even a break-even strategy at the mandated risk.
  - The TRAIN-bootstrap p95 usually binds first.
  - The 3% weekly-loss halt limits how fast a drawdown accrues, not how deep it goes.

  The adoption path needs ROBUST AND `dd_ok`.
- **Layers veto (C1):** a layer can only VETO an entry that passed every mandatory rule. It
  never approves one that a rule denies. A veto, or the guard's halved risk, can free R6
  budget or change R9 state. That admits other rule-compliant trades the base never took, so
  a layer's journal is not a subset of the base's. Every report counts, per window: signals
  vetoed, base trades absent from the layer's journal, and layer trades absent from the base
  journal.

## 2. What was run (all executed; outputs committed in this folder)

All commands run from the repo root as `python -m research.trendbot.run_research ...`. Wall
times are on 4 CPUs. The 16 single runs ran 4 at a time; the calibrations ran one at a time
with `--workers 4`.

| folder | arguments | wall time |
|---|---|---:|
| `synthetic_<world>_s1/` (null, zero_edge, decay, planted, hour_edge) | `--synthetic <world> --seed 1 --now 2026-09-27T12:00:00Z --out-dir research/results/synthetic_<world>_s1` | 27-29 s each |
| `cost_planted_s1_fee0006/` | `--synthetic planted --seed 1 --fee-rate 0.006 --exchange-id coinbase --now 2026-09-27T12:00:00Z --out-dir research/results/cost_planted_s1_fee0006` | 27 s |
| `stress_k0.5/synthetic_<world>_s1/` (5 worlds) | `--synthetic <world> --seed 1 --stop-fill-wick-k 0.5 --now 2026-09-27T12:00:00Z --out-dir research/results/stress_k0.5/synthetic_<world>_s1` | 51-54 s each |
| `stress_k1.0/synthetic_<world>_s1/` (5 worlds) | the same with `--stop-fill-wick-k 1.0 --out-dir research/results/stress_k1.0/synthetic_<world>_s1` | 50-55 s each |
| `calibration_null/` | `--synthetic null --calibrate-seeds 50 --workers 4 --out-dir research/results/calibration_null` (seeds 1-50) | 383 s |
| `calibration_zero_edge/` | `--synthetic zero_edge --calibrate-seeds 50 --workers 4 ...` (seeds 1-50) | 334 s |
| `calibration_decay/` | `--synthetic decay --calibrate-seeds 50 --workers 4 ...` (seeds 1-50) | 328 s |
| `calibration_planted/` | `--synthetic planted --calibrate-seeds 20 --workers 4 ...` (seeds 1-20) | 130 s |
| `calibration_hour_edge/` | `--synthetic hour_edge --calibrate-seeds 20 --workers 4 ...` (seeds 1-20) | 130 s |
| `calibration_null_seeds1001-1050/` | `--synthetic null --calibrate-seeds 50 --seed-offset 1000 --workers 4 --out-dir research/results/calibration_null_seeds1001-1050` | 332 s |
| `calibration_zero_edge_seeds2001-2050/` | `--synthetic zero_edge --calibrate-seeds 50 --seed-offset 2000 --workers 4 --out-dir research/results/calibration_zero_edge_seeds2001-2050` | 322 s |
| `power_planted/` | `--synthetic planted --calibrate-seeds 20 --power-strengths 0.15 0.3 0.45 0.6 0.75 0.9 1.05 1.2 1.4 --workers 4 --out-dir research/results/power_planted` (seeds 1-20 per strength) | 1146 s |

Data and split used by every run:
- Each run uses 6 years of 4H candles: 13,140 per pair for BTC/USDT, ETH/USDT and BNB/USDT,
  2019-01-01 to 2024-12-29.
- The split falls at 70% of the common range, at 2023-03-14T00:00Z. That gives 9,198 TRAIN
  and 3,942 TEST candles per pair.

What each folder type holds:
- A single run holds:
  - `REPORT.md`;
  - TRAIN and TEST journals per pre-registered candidate;
  - a human review pack and an adoption record per adoptable candidate;
  - the ML model file;
  - its holdout ledger `test_looks.jsonl`.
- Stress runs are test-only (D1). They hold journals and a report but no adoption record and
  no review pack, because `ADOPT_test_only` blocks them from HUMAN_REVIEW on. A pack could
  never be used.
- Calibrations and the power curve hold one CSV row per seed plus the rendered
  `CALIBRATION.md` / `POWER.md`.

## 3. Costs: default, and the measured Coinbase-tier run

- **Default costs (every run except `cost_planted_s1_fee0006/`):**
  - fee **0.10% of notional per side** (Binance spot taker, `--fee-rate 0.001`), charged on
    entry AND exit;
  - slippage **0.05%** on market fills (`--slippage-pct 0.05`), none on take-profit limits;
  - exchange `binance`.

  Cost-aware sizing (A1) makes a clean stop exactly -1R, and a take-profit nets exactly +2R
  after fees. Measured on the baseline trades of every seed-1 run, fees alone cost
  **0.06R per trade**. The median stop distance was 3.5-3.8% of the entry (REPORT.md
  section 2).
- **Coinbase Advanced Trade taker fees at low tiers are several times Binance's.** This
  cost is **measured**, not estimated, in the committed run
  `run_research --synthetic planted --seed 1 --fee-rate 0.006 --exchange-id coinbase`
  (0.6% per side). It is synthetic, and keeps BNB/USDT (which Coinbase does not list), so
  it measures the fee effect only. Fees alone cost **0.25R per baseline trade** there
  (median stop 3.98%), against 0.06R at 0.1%. The planted world's edge is almost gone at
  that tier:

| planted seed 1 | fee 0.1%/side (`synthetic_planted_s1`) TRAIN \| TEST avg R (n) | fee 0.6%/side, coinbase (`cost_planted_s1_fee0006`) TRAIN \| TEST avg R (n) |
|---|---|---|
| baseline `base` | UNTESTED +0.296 (115) \| +0.412 (51) | UNTESTED +0.127 (100) \| +0.043 (46) |
| discovery-selected | `rr3_vol2_rsi50-70` **ROBUST** +0.836 (73) \| +0.829 (35) | `rr2_vol2_rsi55-70` UNTESTED +0.423 (75) \| +0.364 (33) |
| `base+ml` (judged TRAIN = D8 gate) | UNTESTED +0.278 (18) \| +0.235 (34) | UNTESTED +0.409 (14) \| -0.040 (25) |
| `base+guard` | UNTESTED +0.296 (115) \| +0.412 (51) | UNTESTED +0.127 (100) \| +0.043 (46) |
| verdict | ROBUST (selected) | no robust result |

  At 0.6% per side the baseline's TEST expectancy fell from +0.412R to +0.043R, and the only
  ROBUST result disappeared. The gap is larger than the 0.19R of extra fees. The cost-aware
  target moves further away, so fewer trades reach it: the take-profit share fell from
  42.6% | 47.1% to 37.0% | 34.8% (TRAIN | TEST). The trade set also changes (TEST n
  51 -> 46). A Coinbase run must pass the account's real tier, or every number is
  optimistic.

## 4. Seed 1: ground truth vs what the pipeline labelled

Each cell gives the label, then TRAIN | TEST avg R (n). `base+ml`'s TRAIN is its judged D8
out-of-sample gate (its in-sample full-TRAIN figure is in brackets). Every adoption record
passes `adoption check --stage WALK_FORWARD`. At `HUMAN_REVIEW` every record is BLOCKED by
`ADOPT_provenance`, plus `ADOPT_walk_forward` wherever the label is not ROBUST. REPORT.md
section 11 prints the commands.

| world (truth) | baseline | discovery-selected | base+ml | base+guard | verdict | right? |
|---|---|---|---|---|---|---|
| null (no edge) | NO-EDGE -0.138 (98) \| -0.121 (58) | `rr2.5_vol2_rsi50-70` NO-EDGE -0.086 (81) \| -0.143 (49) | UNTESTED -1.000 (8) [in-sample +0.267 (45)] \| -0.294 (17) | NO-EDGE -0.183 (108) \| -0.121 (58) | no robust result | yes |
| zero_edge (net ~0) | NO-EDGE -0.004 (94) \| -0.050 (60) | `rr2_vol2_rsi55-70` TRAIN-ONLY +0.048 (75) \| -0.020 (49) | UNTESTED +0.000 (0) [+0.333 (9)] \| +0.500 (6) | NO-EDGE -0.044 (102) \| -0.050 (60) | no robust result | yes |
| decay (edge in TRAIN only) | TRAIN-ONLY +0.307 (114) \| -0.143 (56) | `rr3_vol2_rsi50-70` TRAIN-ONLY +0.742 (70) \| -0.043 (46) | UNTESTED +0.129 (23) [+0.590 (88)] \| -0.073 (55) | TRAIN-ONLY +0.307 (114) \| -0.143 (56) | no robust result | yes |
| planted (edge everywhere) | UNTESTED +0.296 (115) \| +0.412 (51) | `rr3_vol2_rsi50-70` **ROBUST** +0.836 (73) \| +0.829 (35) | UNTESTED +0.278 (18) [+0.489 (94)] \| +0.235 (34) | UNTESTED +0.296 (115) \| +0.412 (51) | ROBUST (selected) | yes, 1 of 4 |
| hour_edge (edge at 12-20 UTC) | NO-EDGE -0.044 (100) \| +0.050 (60) | `rr3_vol2_rsi50-70` UNTESTED +0.203 (72) \| +0.436 (39) | UNTESTED +0.371 (26) [+0.299 (69)] \| +0.295 (44) | NO-EDGE -0.057 (111) \| +0.082 (61) | no robust result | missed |

- In planted seed 1, the baseline's TEST +0.412R (n=51) has 98.75% lower bounds of -0.059R
  (iid) and -0.125R (block). That is a real edge, labelled UNTESTED because 51 trades cannot
  separate it from zero under the multiplicity rule (see the power curve, section 7).
- The selected `rr3_vol2_rsi50-70` has bounds of +0.029R (iid) and +0.167R (block), and a
  TEST MTM drawdown of 5.36% against a limit of 7.26%.
- In decay seed 1, the drawdown rule fails independently of the label: the TEST MTM
  drawdown was 13.76% against a limit of 9.60% (`dd_ok` no).

## 5. Stop-fill stress (CONTRACT v4 D4): how optimistic is the touch fill?

With `--stop-fill-wick-k k`, a non-gap stop fills at `stop - k*(stop - exit candle low)`,
then slippage. k = 0 is the default touch fill.

How to read the table:
- Each stress run also runs the k = 0 reference pipeline on the same data and split.
- REPORT.md section 6 of every `stress_k*/` folder holds the per-candidate table.
- The k = 0 columns below reproduce the committed seed-1 runs exactly.
- The stressed discovery selected the same grid variant as k = 0 in all 10 runs.
- Cells give TRAIN | TEST avg R (n), with the (TRAIN | TEST) shift against k = 0 in
  brackets. Labels that changed are in bold.

| world | candidate (variant at k=0) | k=0 TRAIN \| TEST avg R (n) | k=0.5 TRAIN \| TEST (shift TRAIN \| TEST) | k=1.0 TRAIN \| TEST (shift TRAIN \| TEST) | label k=0 / 0.5 / 1.0 |
|---|---|---|---|---|---|
| null | baseline `base` | -0.138 (98) \| -0.121 (58) | -0.189 (98) \| -0.174 (58) (-0.052 \| -0.053) | -0.241 (98) \| -0.227 (58) (-0.103 \| -0.107) | NO-EDGE / NO-EDGE / NO-EDGE |
| null | discovery-selected `rr2.5_vol2_rsi50-70` | -0.086 (81) \| -0.143 (49) | -0.134 (81) \| -0.202 (49) (-0.048 \| -0.059) | -0.182 (81) \| -0.260 (49) (-0.096 \| -0.117) | NO-EDGE / NO-EDGE / NO-EDGE |
| null | ML layer `base+ml` | -1.000 (8) \| -0.294 (17) | -1.044 (2) \| -0.808 (9) (-0.044 \| -0.514) | -1.099 (1) \| -1.451 (6) (-0.099 \| -1.157) | UNTESTED / UNTESTED / UNTESTED |
| null | expectancy guard `base+guard` | -0.183 (108) \| -0.121 (58) | -0.235 (108) \| -0.174 (58) (-0.052 \| -0.053) | -0.287 (108) \| -0.227 (58) (-0.104 \| -0.107) | NO-EDGE / NO-EDGE / NO-EDGE |
| zero_edge | baseline `base` | -0.004 (94) \| -0.050 (60) | -0.048 (94) \| -0.108 (60) (-0.044 \| -0.058) | -0.091 (94) \| -0.167 (60) (-0.087 \| -0.117) | NO-EDGE / NO-EDGE / NO-EDGE |
| zero_edge | discovery-selected `rr2_vol2_rsi55-70` | +0.048 (75) \| -0.020 (49) | +0.010 (75) \| -0.087 (49) (-0.038 \| -0.067) | -0.029 (75) \| -0.153 (49) (-0.077 \| -0.133) | **TRAIN-ONLY / TRAIN-ONLY / NO-EDGE** |
| zero_edge | ML layer `base+ml` | +0.000 (0) \| +0.500 (6) | +0.000 (0) \| -1.229 (2) (+0.000 \| -1.729) | +0.000 (0) \| +0.000 (0) (+0.000 \| -0.500) | UNTESTED / UNTESTED / UNTESTED |
| zero_edge | expectancy guard `base+guard` | -0.044 (102) \| -0.050 (60) | -0.090 (102) \| -0.108 (60) (-0.045 \| -0.058) | -0.135 (102) \| -0.167 (60) (-0.091 \| -0.117) | NO-EDGE / NO-EDGE / NO-EDGE |
| decay | baseline `base` | +0.307 (114) \| -0.143 (56) | +0.261 (114) \| -0.198 (56) (-0.046 \| -0.055) | +0.215 (114) \| -0.252 (56) (-0.092 \| -0.109) | TRAIN-ONLY / TRAIN-ONLY / TRAIN-ONLY |
| decay | discovery-selected `rr3_vol2_rsi50-70` | +0.742 (70) \| -0.043 (46) | +0.698 (70) \| -0.105 (46) (-0.044 \| -0.062) | +0.654 (70) \| -0.167 (46) (-0.088 \| -0.123) | TRAIN-ONLY / TRAIN-ONLY / TRAIN-ONLY |
| decay | ML layer `base+ml` | +0.129 (23) \| -0.073 (55) | +0.010 (22) \| -0.131 (55) (-0.118 \| -0.059) | +0.221 (18) \| -0.177 (54) (+0.092 \| -0.104) | UNTESTED / UNTESTED / UNTESTED |
| decay | expectancy guard `base+guard` | +0.307 (114) \| -0.143 (56) | +0.261 (114) \| -0.198 (56) (-0.046 \| -0.055) | +0.215 (114) \| -0.252 (56) (-0.092 \| -0.109) | TRAIN-ONLY / TRAIN-ONLY / TRAIN-ONLY |
| planted | baseline `base` | +0.296 (115) \| +0.412 (51) | +0.251 (115) \| +0.346 (51) (-0.045 \| -0.066) | +0.207 (115) \| +0.280 (51) (-0.089 \| -0.132) | UNTESTED / UNTESTED / UNTESTED |
| planted | discovery-selected `rr3_vol2_rsi50-70` | +0.836 (73) \| +0.829 (35) | +0.798 (73) \| +0.773 (35) (-0.038 \| -0.056) | +0.760 (73) \| +0.717 (35) (-0.076 \| -0.111) | **ROBUST / UNTESTED / UNTESTED** |
| planted | ML layer `base+ml` | +0.278 (18) \| +0.235 (34) | +0.329 (17) \| +0.192 (33) (+0.052 \| -0.044) | +0.306 (17) \| +0.155 (32) (+0.028 \| -0.081) | UNTESTED / UNTESTED / UNTESTED |
| planted | expectancy guard `base+guard` | +0.296 (115) \| +0.412 (51) | +0.251 (115) \| +0.346 (51) (-0.045 \| -0.066) | +0.207 (115) \| +0.280 (51) (-0.089 \| -0.132) | UNTESTED / UNTESTED / UNTESTED |
| hour_edge | baseline `base` | -0.044 (100) \| +0.050 (60) | -0.103 (100) \| -0.023 (60) (-0.060 \| -0.073) | -0.163 (100) \| -0.095 (60) (-0.119 \| -0.145) | NO-EDGE / NO-EDGE / NO-EDGE |
| hour_edge | discovery-selected `rr3_vol2_rsi50-70` | +0.203 (72) \| +0.436 (39) | +0.148 (72) \| +0.361 (39) (-0.055 \| -0.075) | +0.093 (72) \| +0.287 (39) (-0.111 \| -0.149) | UNTESTED / UNTESTED / UNTESTED |
| hour_edge | ML layer `base+ml` | +0.371 (26) \| +0.295 (44) | +0.316 (22) \| +0.377 (37) (-0.055 \| +0.081) | +0.085 (18) \| +0.288 (35) (-0.286 \| -0.008) | UNTESTED / UNTESTED / UNTESTED |
| hour_edge | expectancy guard `base+guard` | -0.057 (111) \| +0.082 (61) | -0.120 (111) \| +0.011 (61) (-0.063 \| -0.071) | -0.164 (112) \| -0.061 (61) (-0.107 \| -0.143) | NO-EDGE / NO-EDGE / NO-EDGE |

- **Size of the shift:**
  - For the baseline, discovery-selected and guard rows, k = 0.5 costs 0.04-0.06R per trade
    in TRAIN and 0.05-0.08R in TEST.
  - k = 1.0 (a stop filled at the candle's low) costs 0.08-0.12R in TRAIN and 0.11-0.15R in
    TEST.
  - The planted baseline's mean TEST stop-loss outcome goes from exactly -1.000R (k = 0) to
    -1.125R (k = 0.5) and -1.249R (k = 1.0). Every one of its 27 TEST stop-losses is worse
    than -1R under stress, which the invariants accept only because k > 0.
  - `python -m research.trendbot.invariants --synthetic planted --seed 1 --config k1.json`,
    with `k1.json` = `{"stop_fill_wick_k": 1.0}`, audits the full k = 1.0 history: 166
    trades, 0 violations.
- **Label changes:**
  - **The only ROBUST result of seed 1 does not survive a harsher fill.** Planted
    `rr3_vol2_rsi50-70` drops to UNTESTED at k = 0.5 (TEST +0.773R, n=35, adjusted lower
    bound -0.019R) and at k = 1.0 (TEST +0.717R, adjusted bound -0.077R).
  - Zero_edge's selected variant goes from TRAIN-ONLY to NO-EDGE at k = 1.0, because its
    TRAIN +0.048R becomes -0.029R.
  - No stressed candidate became ROBUST, and no other label changed.
- The `base+ml` rows move more, and in both directions, because the model is REFITTED on the
  stressed candidates (a different model) and its windows hold only 0-55 trades.
- Every stressed config is test-only: no ledger line, no adoption record. **Reading for real
  data:** an edge whose multiplicity-corrected lower bound is only a few hundredths of an R
  above zero under the touch fill is not robust to execution. Judge real candidates with the
  k = 0.5 stress beside the default.

## 6. Calibration: ground truth vs labels over seeds (90% Wilson intervals)

"Verdict ROBUST" means any of the 4 candidates is ROBUST (the family-wise rate). The TEST
gate is reached when the judged TRAIN avg R > 0 and both windows hold >= 30 trades; the
bootstrap bound decides from there.

**Seeds and independence.** The synthetic worlds share their noise for a given seed; only
the planted drift differs. Decay seed k's TEST window has exactly null seed k's returns and
volumes; only the price level differs, by a constant factor of at most 0.15%. So seeds 1-50
of null, zero_edge and decay are PAIRED draws, not independent ones.
- **The headline false-positive rates use disjoint seed ranges, so their noise is
  independent:** null seeds 1001-1050, zero_edge seeds 2001-2050, decay seeds 1-50.
- The paired seed 1-50 runs of null and zero_edge are kept beside them. They compare worlds
  on identical noise.
- Planted and hour_edge (seeds 1-20) measure detection, and share their noise with decay
  seeds 1-20.

| folder | world | seeds | verdict ROBUST (any of 4) | baseline ROBUST | baseline reached the TEST gate | baseline mean avg R TRAIN (mean n) \| TEST (mean n) | baseline dd_ok | invariant violations |
|---|---|---|---|---|---|---|---|---:|
| `calibration_null_seeds1001-1050` | null | 1001-1050 | 0/50 = 0% (0%-5%) | 0/50 = 0% (0%-5%) | 5/50 = 10% (5%-19%) | -0.146 (n 101.6) \| -0.046 (n 45.6) | 42/50 = 84% (74%-91%) | 0 |
| `calibration_zero_edge_seeds2001-2050` | zero_edge | 2001-2050 | 2/50 = 4% (1%-11%) | 2/50 = 4% (1%-11%) | 33/50 = 66% (54%-76%) | +0.024 (n 105.4) \| +0.047 (n 46.5) | 46/50 = 92% (83%-96%) | 0 |
| `calibration_decay` | decay | 1-50 | 0/50 = 0% (0%-5%) | 0/50 = 0% (0%-5%) | 49/50 = 98% (92%-100%) | +0.458 (n 113.9) \| -0.095 (n 45.9) | 9/50 = 18% (11%-29%) | 0 |
| `calibration_null` | null | 1-50 | 0/50 = 0% (0%-5%) | 0/50 = 0% (0%-5%) | 9/50 = 18% (11%-29%) | -0.110 (n 100.8) \| -0.086 (n 46.0) | 43/50 = 86% (76%-92%) | 0 |
| `calibration_zero_edge` | zero_edge | 1-50 | 0/50 = 0% (0%-5%) | 0/50 = 0% (0%-5%) | 31/50 = 62% (50%-72%) | +0.035 (n 103.2) \| +0.027 (n 46.7) | 46/50 = 92% (83%-96%) | 0 |
| `calibration_planted` | planted | 1-20 | 10/20 = 50% (33%-67%) | 8/20 = 40% (24%-58%) | 20/20 = 100% (88%-100%) | +0.442 (n 114.8) \| +0.445 (n 49.6) | 20/20 = 100% (88%-100%) | 0 |
| `calibration_hour_edge` | hour_edge | 1-20 | 5/20 = 25% (13%-43%) | 0/20 = 0% (0%-12%) | 18/20 = 90% (74%-97%) | +0.198 (n 115.0) \| +0.201 (n 50.5) | 18/20 = 90% (74%-97%) | 0 |

**Headline false-positive rates (independent noise):**
- null: 0/50 = 0% (90% CI 0-5%);
- zero_edge (the H0 boundary: a real pre-cost edge that costs eat): 2/50 = 4% (1-11%);
- decay (an edge that vanishes at the split): 0/50 = 0% (0-5%);
- all three together: 2/150 = 1.3% (0.4-3.9%).

The paired seed 1-50 runs agree: null 0/50, zero_edge 0/50. **Detection:** planted 10/20 =
50% (33-67%), hour_edge 5/20 = 25% (13-43%).

Per candidate: ROBUST over all seeds; how many seeds reached the TEST gate; mean avg R
TRAIN | TEST.

| folder | discovery-selected | base+ml (judged TRAIN = D8 gate) | base+guard |
|---|---|---|---|
| `calibration_null_seeds1001-1050` | 0/50 = 0% (0%-5%); gate 19/50; -0.026 \| -0.048 | 0/50 = 0% (0%-5%); gate 0/50; -0.161 \| -0.077 | 0/50 = 0% (0%-5%); gate 3/50; -0.149 \| -0.046 |
| `calibration_zero_edge_seeds2001-2050` | 0/50 = 0% (0%-5%); gate 38/50; +0.172 \| +0.115 | 0/50 = 0% (0%-5%); gate 7/50; +0.026 \| +0.002 | 2/50 = 4% (1%-11%); gate 32/50; +0.027 \| +0.047 |
| `calibration_decay` | 0/50 = 0% (0%-5%); gate 46/50; +0.717 \| -0.081 | 0/50 = 0% (0%-5%); gate 36/50; +0.525 \| -0.081 | 0/50 = 0% (0%-5%); gate 49/50; +0.459 \| -0.097 |
| `calibration_null` | 0/50 = 0% (0%-5%); gate 20/50; +0.013 \| -0.097 | 0/50 = 0% (0%-5%); gate 1/50; -0.068 \| -0.150 | 0/50 = 0% (0%-5%); gate 10/50; -0.103 \| -0.087 |
| `calibration_zero_edge` | 0/50 = 0% (0%-5%); gate 39/50; +0.175 \| +0.043 | 0/50 = 0% (0%-5%); gate 6/50; +0.059 \| +0.060 | 0/50 = 0% (0%-5%); gate 31/50; +0.042 \| +0.025 |
| `calibration_planted` | 9/20 = 45% (28%-63%); gate 18/20; +0.682 \| +0.553 | 7/20 = 35% (20%-53%); gate 19/20; +0.448 \| +0.435 | 8/20 = 40% (24%-58%); gate 20/20; +0.441 \| +0.445 |
| `calibration_hour_edge` | 3/20 = 15% (6%-32%); gate 16/20; +0.480 \| +0.382 | 3/20 = 15% (6%-32%); gate 7/20; +0.400 \| +0.399 | 0/20 = 0% (0%-12%); gate 19/20; +0.207 \| +0.203 |

What this shows:

- **False positives are controlled at the H0 boundary and under decay.**
  - The two zero_edge false positives are seeds 2016 and 2041, where the baseline (and the
    guard, identical to it in TEST) was ROBUST. Seed 2016: TRAIN +0.023R (n=96) | TEST +0.525R
    (n=56), bounds +0.092R iid / +0.087R block. Seed 2041: TRAIN +0.071R (n=99) | TEST
    +0.620R (n=47), bounds +0.149R / +0.126R.
  - By the world's definition these are false positives: a lucky TEST window on a barely
    positive TRAIN. Their rate (4%, CI 1-11%) is consistent with the design bound of about
    5%, but not far below it.
  - Decay reached the TEST gate in 49/50 seeds, and its baseline TEST avg R was as high as
    +0.50R in one seed, yet it produced 0 ROBUST.
  - The block bootstrap was the stricter bound in 22-32 of 50 seeds in every no-edge world.
- **Decay is caught twice:**
  - the baseline was TRAIN-ONLY in 30/50 and UNTESTED in 20/50, never ROBUST;
  - `dd_ok` failed in 41/50 decay seeds, because the TEST (null) MTM drawdowns (mean 10.9%)
    exceed what TRAIN sequences with an edge predict (mean limit 8.3%).

  In planted, `dd_ok` passed 20/20 (MTM mean 5.4% against a mean limit of 8.6%).
- **Power is the price of that control.** The planted edge (+0.445R per TEST trade on
  average) was detected by the baseline in only 8/20 seeds. All 12 misses are UNTESTED, with
  TEST avg R from +0.14 to +0.51R; none is TRAIN-ONLY or NO-EDGE. The block bootstrap was the
  stricter bound in 13/20 planted seeds, and decided the label alone (iid bound > 0, block
  bound <= 0) in 1 of them.
- **Hour_edge:** the hour-blind baseline was never ROBUST (0/20). The 5 verdicts came from
  the discovery-selected variant (3/20) and the ML layer (3/20), one seed having both. The
  ML layer's largest coefficient was an hour term in 12/20 fits, against 1/20 in planted.
- **The D8 gate makes `base+ml` hard to validate at this sample size.** Its judged TRAIN
  window is only the last ~1.1-1.4 years of TRAIN, so it holds >= 30 trades only when the layer
  vetoes little. That happened in 2/50 null seeds and 9/50 zero_edge seeds (seeds 1-50),
  19/20 planted seeds and 9/20 hour_edge seeds. Where the layer has something to learn (hour_edge) it
  still reached ROBUST in 3/20.

**ML layer and expectancy guard, TRAIN | TEST (means per seed)**:

| folder | base avg R | base+ml avg R (judged TRAIN) | base+guard avg R | base+ml in-sample full-TRAIN avg R (context) | ML vetoed | ML base-only | ML layer-only | guard entries at half risk | guard layer-only | ML TEST avg R above base's |
|---|---|---|---|---:|---|---|---|---|---|---|
| `calibration_null_seeds1001-1050` | -0.146 \| -0.046 | -0.161 \| -0.077 | -0.149 \| -0.046 | +0.093 | 216.4 \| 98.9 | 76.4 \| 34.2 | 23.9 \| 12.3 | 32.0 \| 0.4 | 12.8 \| 0.2 | 17/50; unfitted 0 |
| `calibration_zero_edge_seeds2001-2050` | +0.024 \| +0.047 | +0.026 \| +0.002 | +0.027 \| +0.047 | +0.135 | 119.1 \| 52.2 | 57.2 \| 25.9 | 27.0 \| 13.2 | 21.3 \| 0.5 | 7.5 \| 0.1 | 19/50; unfitted 0 |
| `calibration_decay` | +0.458 \| -0.095 | +0.525 \| -0.081 | +0.459 \| -0.097 | +0.506 | 11.8 \| 3.9 | 11.3 \| 4.3 | 5.8 \| 3.7 | 3.8 \| 1.0 | 0.6 \| 0.5 | 20/50; unfitted 0 |
| `calibration_null` | -0.110 \| -0.086 | -0.068 \| -0.150 | -0.103 \| -0.087 | +0.055 | 210.6 \| 99.2 | 73.8 \| 34.2 | 25.7 \| 12.2 | 29.4 \| 0.8 | 11.3 \| 0.3 | 23/50; unfitted 0 |
| `calibration_zero_edge` | +0.035 \| +0.027 | +0.059 \| +0.060 | +0.042 \| +0.025 | +0.139 | 107.0 \| 52.2 | 52.9 \| 25.6 | 27.4 \| 12.9 | 22.4 \| 0.6 | 7.2 \| 0.1 | 27/50; unfitted 0 |
| `calibration_planted` | +0.442 \| +0.445 | +0.448 \| +0.435 | +0.441 \| +0.445 | +0.483 | 11.1 \| 5.2 | 10.3 \| 4.8 | 5.2 \| 2.2 | 2.1 \| 0.1 | 0.2 \| 0.0 | 7/20; unfitted 0 |
| `calibration_hour_edge` | +0.198 \| +0.201 | +0.400 \| +0.399 | +0.207 \| +0.203 | +0.420 | 53.9 \| 23.2 | 43.1 \| 18.5 | 21.2 \| 8.8 | 13.1 \| 0.8 | 2.5 \| 0.1 | 18/20; unfitted 0 |

- **The ML layer helps only where there is something to learn.**
  - In hour_edge it doubled TEST expectancy (+0.201 -> +0.399R), beating the base in 18/20
    seeds.
  - In null (seeds 1-50), its in-sample full-TRAIN avg R looked better than the base's
    (+0.055R against -0.110R). Its out-of-sample TRAIN gate was -0.068R, and its TEST
    -0.150R was worse than the base's -0.086R. The in-sample gain is fitted noise, which is
    why D8 judges the out-of-sample gate.
  - In planted it removed trades without improving TEST (+0.445 -> +0.435R).
- **Vetoes do admit other trades (C1).** In null, the ML layer's journal held 25.7 TRAIN and
  12.2 TEST trades per seed that the base never took, because each veto left R6 budget free
  for another pair.
- **The guard rarely binds after its warm-up.**
  - It halves a pair's risk while that pair's last 20 closed trades average below 0R. So it
    acts mostly in TRAIN (29.4 half-risk entries per null seed) and almost never in the
    1.8-year TEST window (0.8), where a fresh journal needs 20 trades per pair first.
  - Its mean TEST avg R is within 0.002R of the base's in every calibration.
  - It was never ROBUST where the base was not.

  There is no evidence it adds expectancy.

## 7. Power curve and the minimum detectable effect (`power_planted/POWER.md`)

The planted mechanism at nine strengths, 20 seeds each (seeds 1-20 at every strength, so
the strengths are compared on identical noise). The TEST column is the **mean observed TEST
avg R over seeds** of the baseline, with its 90% interval across seeds. It estimates what one
run expects to see at that strength; it is an average of estimates, not the true parameter.

| strength (sigma per candle) | seeds | base mean avg R over seeds: TRAIN \| TEST [90% CI of the TEST mean] | base mean n TRAIN \| TEST | base reached the TEST gate | **base ROBUST = detection** | any of the 4 ROBUST |
|---:|---:|---|---|---|---|---|
| 0.15 (= zero_edge) | 20 | +0.037 \| +0.066 [-0.009, +0.141] | 103.1 \| 45.8 | 11/20 = 55% (37%-72%) | 0/20 = 0% (0%-12%) | 0/20 = 0% (0%-12%) |
| 0.3 | 20 | +0.163 \| +0.145 [+0.070, +0.221] | 105.5 \| 45.9 | 18/20 = 90% (74%-97%) | 1/20 = 5% (1%-20%) | 1/20 = 5% (1%-20%) |
| 0.45 | 20 | +0.272 \| +0.252 [+0.193, +0.311] | 113.0 \| 50.3 | 20/20 = 100% (88%-100%) | 2/20 = 10% (3%-26%) | 4/20 = 20% (9%-38%) |
| 0.6 | 20 | +0.388 \| +0.351 [+0.278, +0.425] | 117.5 \| 50.1 | 20/20 = 100% (88%-100%) | 4/20 = 20% (9%-38%) | 7/20 = 35% (20%-53%) |
| 0.75 | 20 | +0.471 \| +0.422 [+0.357, +0.486] | 114.0 \| 48.6 | 20/20 = 100% (88%-100%) | 6/20 = 30% (16%-48%) | 11/20 = 55% (37%-72%) |
| 0.9 | 20 | +0.590 \| +0.582 [+0.508, +0.656] | 102.8 \| 45.9 | 20/20 = 100% (88%-100%) | 14/20 = 70% (52%-84%) | 17/20 = 85% (68%-94%) |
| 1.05 | 20 | +0.672 \| +0.628 [+0.554, +0.701] | 93.3 \| 40.0 | 20/20 = 100% (88%-100%) | 15/20 = 75% (57%-87%) | 16/20 = 80% (62%-91%) |
| 1.2 | 20 | +0.748 \| +0.620 [+0.514, +0.727] | 81.2 \| 33.9 | 14/20 = 70% (52%-84%) | 8/20 = 40% (24%-58%) | 8/20 = 40% (24%-58%) |
| 1.4 | 20 | +0.840 \| +0.727 [+0.654, +0.800] | 66.5 \| 27.9 | 8/20 = 40% (24%-58%) | 3/20 = 15% (6%-32%) | 3/20 = 15% (6%-32%) |

(`power_planted/POWER.md` adds the pooled TEST R per trade, the realised / MTM drawdowns
with their limits, `dd_ok`, and ROBUST given the gate. 180 runs, 0 invariant violations.
The 0.15-sigma cell reproduces `calibration_zero_edge` seeds 1-20 exactly, on all 82
per-seed columns.)

**Minimum detectable effect of the baseline at this sample size (6 years of 4H data, ~46-50
TEST trades):**
- **50% power:** about **+0.50R** mean observed TEST avg R over seeds (strength ~0.83
  sigma, interpolated between 0.75 and 0.9 sigma).
- **80% power is never reached** at any tested strength. The highest detection is 75%, at
  1.05 sigma (+0.628R).
  - Beyond that, detection FALLS: 40% at 1.2 sigma and 15% at 1.4 sigma.
  - The reason is that a stronger drift pushes RSI above 70 more often. R2 then admits fewer
    signals, and the mean TEST n drops to 33.9 and 27.9, at or below the 30 ROBUST needs.
  - A stronger edge cannot buy more power here. Only more trades can.
- **An edge below the MDE will be labelled UNTESTED on real data however real it is.**
  - The baseline detected edges of +0.15 to +0.42R per trade (strengths 0.3-0.75 sigma) in
    only 5-30% of runs, and +0.07R (0.15 sigma) never.
  - Real edges, if any exist, are more likely to be in that range or below it.
  - UNTESTED means "not shown", never "shown to be absent".
  - The label rule is fixed and will not be loosened to find more.
- **The only honest way to raise power is more data.** The standard error of the TEST mean
  shrinks roughly with sqrt(n), so the detectable effect falls roughly as 1/sqrt(n).
  - Use the longest available history: Binance BTC/USDT and ETH/USDT 4H candles go back to
    2017-08, BNB/USDT to 2017-11.
  - That is about 9 years instead of 6, so about 1.5x the TEST trades. It lowers the MDE by
    a factor of about 1/sqrt(1.5) = 0.82, to roughly +0.41R at 50% power.
  - This holds only if the edge is stable over those years.
  - Even then, most plausible real edges (a few tenths of an R or less) stay below the MDE.

## 8. Holdout reuse: every re-run on the same data is another TEST look

- **Every look spends the TEST window.** A TEST window can answer one pre-registered
  question honestly. Each time a candidate is backtested on it, its TEST numbers are seen;
  a candidate revised and re-run on the same window is judged on data that already shaped
  it.
- **The ledger enforces at most m distinct adoptable looks.** `run_research` appends one
  line per adoptable pre-registered candidate that got a TEST backtest to the holdout ledger
  (`--ledger`; default `<data-dir>/.test_looks.jsonl` for real data). Each line holds the
  config fingerprint, the model fingerprint, the split, and per pair the file sha256 and
  TEST window. From HUMAN_REVIEW on, `adoption check` counts the DISTINCT
  (config fingerprint, model fingerprint) among lines whose TEST window overlaps the
  record's. It blocks with `ADOPT_holdout` if the count exceeds m = 4.
- **Re-running the identical candidates is not a new look.** Demonstrated in scratch against
  a copy of `synthetic_planted_s1/test_looks.jsonl`:
  - re-running the identical seed-1 command appended 4 lines, and the distinct count stayed
    4 (within the limit);
  - re-running with a revised config (`--fee-rate 0.0012`) on the same TEST window raised
    it to 8 > 4, and every record of that run was BLOCKED at HUMAN_REVIEW by
    `ADOPT_holdout` (plus `ADOPT_provenance`).
- **A revision after a reviewer N needs unseen TEST data.** After a reviewer marks any trade
  N, or after any other revision, the variant restarts at BACKTEST. It needs a TEST window
  starting at or after the latest `test_end_ts` of every earlier look, and it is never
  re-run on the same TEST window.
- **Limit:** the ledger is append-only and not hash-bound. A deleted line, or looks written
  to a different ledger, cannot be detected offline. The ledger makes reuse visible; it
  cannot make it impossible.
- In every committed single run, REPORT.md section 6 and the console print the count per
  pair: 4 in every run, within the limit. The stress runs ledger their k = 0 reference looks,
  because those looks happened. The stressed configs themselves are never ledgered.

## 9. Provenance, testnet and determinism

- **Manifest-backed provenance (D2).**
  - `fetch_data` writes `manifest.json` next to the candles: exchange id, ccxt version,
    symbols, timeframe, since/until, fetch time, and per file its rows, first/last ts and
    sha256.
  - `run_research --data-dir` calls `data.verify_manifest`. The records' provenance is
    `real` only when every candle file is listed with a matching sha256, otherwise
    `unverified-csv`.
  - The manifest path and sha256 are bound into every record. Adoption allows only `real`
    with a hash-matching manifest past WALK_FORWARD.
  - **Its limit:** an offline check cannot authenticate an exchange download. The manifest
    only proves the files are byte-identical to what it lists. That makes a laundered or
    edited CSV a deliberate act (the manifest must be rewritten too) instead of an accident.
    It is not proof that the candles came from the exchange.
- **Testnet is not mainnet.**
  - The TESTNET stage (>= 14 days on Binance spot testnet) replays `gatekeeper.LiveSession`
    over the candles the bot saw. It injects the journal's actual fills and exits, and runs
    `invariants --live-journal`.
  - It verifies execution and rule compliance (the mandatory rules as the live bot applied
    them; R5 only when the events file is recorded), **not edge**. Testnet prices and
    liquidity are not mainnet.
  - The baseline trades about **1.0 time per 14 days** across the three pairs (154-170
    trades per 6 years in the seed-1 worlds). A 14-day testnet run therefore expects about
    one trade, and with a Poisson rate of 1 there is a 37% chance it has none at all.
- **Determinism, checked for this commit.**
  - The package was copied unchanged into a scratch directory (`diff -r` against
    `research/trendbot/`: identical). Three commands were re-run there with the identical
    argv (so the identical relative output paths): the planted seed-1 run, the k = 1.0
    planted stress run, and the `calibration_zero_edge_seeds2001-2050` calibration.
  - `diff -r` against the committed folders found no difference in any file:
    - 27 files for `synthetic_planted_s1`: REPORT.md, journals, review packs, model file,
      adoption records with their sha256 fields, ledger and run.log;
    - 12 files for `stress_k1.0/synthetic_planted_s1`;
    - 3 files for `calibration_zero_edge_seeds2001-2050` (CALIBRATION.md, the 50-row
      calibration_runs.csv, run.log), re-run in 333 s.
  - A second check: the k = 0 reference inside every stress run reproduces the separately
    committed seed-1 run of that world, candidate by candidate (section 5).

## 10. Where the methodology is weak (honest list)

1. **Low power, by design of the rule.** See section 7. The baseline detects a +0.42R edge
   in 30% of 6-year runs (any of the 4 candidates: 55%), and 80% power is never reached. On
   real data expect UNTESTED far more often than ROBUST.
2. **Calibration samples are modest.**
   - Headline counts are 0/50, 2/50 and 0/50, which bound the per-world false-positive rate
     below about 5-11%.
   - Zero_edge's 4% is close to the ~5% design bound: at the H0 boundary the rule is
     calibrated, not conservative.
   - Paired seed ranges (1-50) are not independent across worlds (section 6).
3. **The touch fill is optimistic.** The k = 0.5 stress costs about 0.05-0.08R per TEST
   trade, and the one seed-1 ROBUST result does not survive it (section 5).
4. **The drawdown rule compares an MTM drawdown with a closed-trade bootstrap.** The TEST
   MTM drawdown includes intra-trade dips that a bootstrap of closed-trade returns does not,
   so `dd_ok` is conservative. It separated decay (41/50 failed) from planted (0/20 failed)
   here, but it is only as good as the TRAIN window is representative.
5. **`base+ml` is judged on a small out-of-sample TRAIN window (D8).** It rarely reaches 30
   trades there, so the ML layer is usually UNTESTED whatever it does in TEST.
6. **The synthetic effect is a positive-control fixture**, hand-calibrated in
   `synthetic.py`. It is not an estimate of any real edge. Real edges, if any, are likely
   weaker, and Coinbase low-tier fees remove edges of this size (section 3).
7. **One split.** Every label rests on a single chronological 70/30 split. A ROBUST label is
   followed by a human review of every trade and at least 2 weeks of testnet, never by
   money.

## 11. The adoption path (same for real data; cannot be shortcut)

The path runs in this order:
1. backtest;
2. walk-forward (ROBUST + `dd_ok`, with the pinned label parameters recomputed from the
   hash-bound journals);
3. a named human reviews EVERY trade of the review pack (any N blocks, and the variant
   restarts at BACKTEST on unseen TEST data);
4. at least 14 days on Binance testnet with zero rule violations, replayed and audited;
5. live.

Test-only configs (regime OFF, any stop-fill stress) are blocked from HUMAN_REVIEW on with
`ADOPT_test_only`. The ML variant is adopted as a (config, model) pair. The live bot loads
`model_base_plus_ml.json` with
`MLFilter.from_json(text, expected_fingerprint=<the record's model_fingerprint>)`, which
raises on any mismatch. **Refitting the model changes its fingerprint and restarts the
adoption path at BACKTEST.**

## 12. How to run this on REAL data (needs a machine with network access)

Nothing in this block was run here on real data: the sandbox has no network. The offline
steps were exercised on the planted seed-1 world, exported to candle CSVs and an events.csv
in scratch:
- `invariants --journal ... --start-ts 1678752000000 --end-ts 1735516800000` re-audited the
  committed `synthetic_planted_s1/journals/base_test.csv`: 51 trades, 0 violations.
- The same command with `--config .../config_rr3_vol2_rsi50-70.json` re-audited
  `rr3_vol2_rsi50-70_test.csv`: 35 trades, 0 violations.
- `invariants --live-journal ... --starting-equity 10000` re-audited `base_test.csv`: 51
  trades, 0 violations. With `--events`, R5 was checked against 273 events; without it, the
  CLI printed that R5 was NOT checked.

```bash
# from the repo root, on a networked machine
pip install ccxt

# 1) candles + manifest.json (D2). Use the LONGEST history available (section 7: the MDE
#    falls roughly as 1/sqrt(n)). Binance spot serves BTC/USDT and ETH/USDT from mid-August
#    2017 and BNB/USDT from November 2017; check first_utc per file in manifest.json. Read
#    the printed gap report.
python -m research.trendbot.fetch_data --exchange binance \
    --pairs BTC/USDT ETH/USDT BNB/USDT --timeframe 4h --since 2017-08-01 --out research/data
#    -> research/data/BTC_USDT-4h.csv, ETH_USDT-4h.csv, BNB_USDT-4h.csv, manifest.json

# 2) news calendar (R5) for the WHOLE history: research/data/events.csv, header
#      time_utc,scope,impact,kind,note,known_from_utc
#    high-impact macro releases (scope ALL) from a real economic calendar, and Binance
#    announcements (bnb_burn / launchpool, scope BNB or EXCHANGE:binance).
#    known_from_utc = when the event became public. Leave it EMPTY for the kind default:
#    scheduled kinds (macro, unlock, bnb_burn, launchpool) block the full +/-window, while
#    unscheduled kinds (regulatory, legal, other) block only from their own time. Fill it
#    when a scheduled event was announced late (e.g. a launchpool announced 12h ahead).
#    research/trendbot/events_example.csv shows the format only; it is NOT a real calendar.

# 3) the research run. Pass YOUR fee tier and slippage; --ledger is the holdout ledger
#    (keep it with the data and never delete it).
python -m research.trendbot.run_research --data-dir research/data \
    --events research/data/events.csv \
    --fee-rate 0.001 --slippage-pct 0.05 --exchange-id binance \
    --ledger research/data/.test_looks.jsonl \
    --out-dir research/results/real_binance
#    optional, test-only: the same command with --stop-fill-wick-k 0.5 and another
#    --out-dir, to see how much of any edge survives a harsher stop fill (section 5)

#    Coinbase Advanced Trade: low-tier taker fees are several times Binance's (0.006 =
#    0.6% per side; section 3 measured what that does). BNB/USDT is not listed there.
python -m research.trendbot.fetch_data --exchange coinbase \
    --pairs BTC/USDT ETH/USDT --timeframe 4h --since 2017-08-01 --out research/data/coinbase
python -m research.trendbot.run_research --data-dir research/data/coinbase \
    --pairs BTC/USDT ETH/USDT --events research/data/events.csv \
    --fee-rate 0.006 --slippage-pct 0.05 --exchange-id coinbase \
    --ledger research/data/coinbase/.test_looks.jsonl \
    --out-dir research/results/real_coinbase

# 4) independent re-audit of the run's BACKTEST journals, with the config the run used
#    (config_<variant>.json holds every override, costs included; it exists only for a
#    non-default config). --start-ts / --end-ts = the TEST window in epoch ms: the
#    test_start_ts / test_end_ts of the run's lines in the ledger.
python -m research.trendbot.invariants \
    --journal research/results/real_binance/journals/base_test.csv \
    --data-dir research/data --events research/data/events.csv \
    --start-ts <test_start_ts> --end-ts <test_end_ts> \
    --config research/results/real_binance/config_<variant>.json   # only if it exists

# 5) the EXISTING bot's journal (real fill times). Write a map.json for its real column
#    names first (see the research/trendbot/journal.py docstring), convert, compare a few
#    rows with the bot's own figures, then audit it in LIVE mode: exit offset 0,
#    per-trade R1-R5/R8 re-derived from the candles and events, no backtest fill-price
#    identities. Plain --journal is for backtest journals only.
python -m research.trendbot.journal convert --in bot_trades.csv --map map.json \
    --out research/data/bot_journal.csv --fee-rate 0.001 --slippage-pct 0.05
python -m research.trendbot.invariants --live-journal research/data/bot_journal.csv \
    --data-dir research/data --events research/data/events.csv \
    --starting-equity <account equity before its first trade> --fee-rate 0.001 \
    [--config <overrides.json if the bot does not run the default config>]
python -m research.trendbot.journal_rules --journal research/data/bot_journal.csv \
    --equity <the bot's current equity>

# 6) adoption gate. REPORT.md section 11 prints the exact command per record (with
#    --config for a non-default config and --model-fingerprint for base+ml). A real record
#    binds the data files, the manifest, the events file and the ledger, so WALK_FORWARD ->
#    HUMAN_REVIEW needs ROBUST + dd_ok, provenance 'real' and <= 4 distinct looks.
python -m research.trendbot.adoption check \
    --record research/results/real_binance/adoption_base.json --stage HUMAN_REVIEW
python -m research.trendbot.adoption check \
    --record research/results/real_binance/adoption_base_plus_ml.json --stage HUMAN_REVIEW \
    --model-fingerprint <sha256 printed in REPORT.md section 5>
python -m research.trendbot.adoption check \
    --record research/results/real_binance/adoption_base_plus_guard.json --stage HUMAN_REVIEW \
    --config research/results/real_binance/config_base_plus_guard.json
#    after a named reviewer fills in reviewer_ok (Y/N) for EVERY row of the review pack:
python -m research.trendbot.adoption hash \
    research/results/real_binance/review_base/trades_review.csv
```

## Risk disclaimer

This is a research and testing tool, not financial advice. Backtests and synthetic worlds
are simplified models, and past or simulated results do not predict future results. Crypto
trading can result in the total loss of the capital used.
