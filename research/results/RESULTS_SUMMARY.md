# Results summary: synthetic verification of the research methodology

**NO real BTC/ETH/BNB data was backtested.** The build sandbox has no network access and no
`ccxt`, so every number below comes from `synthetic.py` worlds with a KNOWN ground truth.
They test the methodology: does the pipeline find an edge where one was planted, and does it
refuse to find one where there is none? They are **not evidence about real markets**, and
no variant here may be adopted on the strength of them. The exact commands for running the
same pipeline on real data are at the end.

No win rate is promised or targeted anywhere in this package. A win rate without its
reward:risk is meaningless: at the mandatory 2:1 a strategy breaks even near 33% winners.
A backtest win rate near 90% would point to curve-fitting or look-ahead. The target is
positive expectancy (avg R per trade, net of fees and slippage) that survives the 70/30
chronological walk-forward with controlled drawdown. Win rate appears in the reports only
as context, labelled "context only".

Everything here was regenerated from the current code (CONTRACT.md v2). The previous run's
files were deleted first.

## Cost assumptions used by every run below

- Fee **0.10% of notional per side**, the Binance spot taker default, charged on the entry
  AND on the exit (`--fee-rate 0.001`).
- Slippage **0.05%** against the trade on market fills (`--slippage-pct 0.05`): entries and
  stop exits. Take-profit limit exits get no slippage.
- Exchange id `binance` (`--exchange-id`).
- Cost-aware sizing (CONTRACT.md v2 A1). The planned risk is the ALL-IN loss at the stop:
  the stop fill after slippage, plus both fees. So a clean stop is exactly -1R, and the target
  is placed so that a take-profit nets exactly +2R (the mandate's 2:1) after fees. Only a gap
  through the stop can lose more than 1R. At these costs, fees alone averaged 0.06R per
  baseline trade (median stop distance 3.5-3.8% of the entry price; see each REPORT.md,
  section 2).
- **Coinbase Advanced Trade taker fees at low volume tiers are several times Binance's.** A
  Coinbase run must pass the account's real tier with `--fee-rate` (a fraction per side:
  0.006 = 0.6%) and `--exchange-id coinbase`, or every number is optimistic. How much this
  matters, measured (not committed; the command is shown): the same planted seed-1 world at
  a 0.6% fee (`--synthetic planted --seed 1 --fee-rate 0.006 --exchange-id coinbase`) cut the
  baseline from ROBUST (TRAIN +0.296R, TEST +0.412R) to UNTESTED (TRAIN +0.127R, TEST
  +0.043R, n=46). Fees alone then cost 0.25R per trade, and the verdict became "No robust
  result found." A real edge that is smaller than this deliberately strong synthetic one would
  not survive Coinbase low-tier fees.

## What was run (all executed; the outputs are in this folder)

| folder | command (from the repo root) | wall time (4 CPUs) |
|---|---|---:|
| `synthetic_null_s1/` | `python -m research.trendbot.run_research --synthetic null --seed 1 --now 2026-09-27T12:00:00Z --out-dir research/results/synthetic_null_s1` | 29 s |
| `synthetic_planted_s1/` | the same with `--synthetic planted` | 28 s |
| `synthetic_decay_s1/` | the same with `--synthetic decay` | 29 s |
| `synthetic_hour_edge_s1/` | the same with `--synthetic hour_edge` | 29 s |
| `calibration_null/` | `python -m research.trendbot.run_research --synthetic null --calibrate-seeds 20 --workers 4 --out-dir research/results/calibration_null` | 123 s |
| `calibration_planted/` | the same with `--synthetic planted --calibrate-seeds 10` | 72 s |
| `calibration_decay/` | the same with `--synthetic decay --calibrate-seeds 10` | 73 s |
| `calibration_hour_edge/` | the same with `--synthetic hour_edge --calibrate-seeds 10` | 75 s |

Each run uses 6 years of 4H candles: 13,140 per pair for BTC/USDT, ETH/USDT and BNB/USDT.
The split falls at 70% of the common range (2023-03-14T00:00Z), giving 9,198 TRAIN and
3,942 TEST candles per pair. There are three pre-registered candidates:

- "base": the mandate config.
- "selected": the one discovery variant picked by TRAIN t-stat from 12 legal tightenings.
  K = 13 variants were tried, including the test-only regime-OFF variant, which can never be
  selected.
- "ML": the baseline plus the logistic `L_ml_filter`, fitted on purged TRAIN candidates only.

The verdict considers only these three. What each seed-1 folder holds:

- `REPORT.md`, the 12-section report. Every table header names its TRAIN and TEST columns.
- `run.log`, the console output.
- TRAIN and TEST journals and a review pack, but only for the three candidates. The other
  12 discovery variants are one context row each in the report, so they get no journal.
- One adoption record per candidate. The ML record carries the fitted model's sha256
  (`model_base_plus_ml.json` is the exact JSON that is hashed).
- `config_<variant>.json` for the non-default config of the selected variant, which
  `adoption check --config` needs.

Each calibration folder holds `CALIBRATION.md` plus `calibration_runs.csv`. The CSV has one
row per seed, including the ML model fingerprint and its hour coefficients.

`invariants.check_invariants` was run on every backtest of every run and found **0
violations** across 30 backtests x 54 pipeline runs. Each seed-1 run repeats seed 1 of its
calibration and reproduced it exactly, down to the ML model fingerprint (for example
hour_edge `b38d6af2...` in both). All 12 `adoption check` commands printed in the four
reports ran and gave the expected results. Two PASSed HUMAN_REVIEW (planted `base` and
`rr3_vol2_rsi50-70`). The rest were BLOCKED only by `ADOPT_walk_forward`, never by a
fingerprint mismatch.

## Seed 1: ground truth vs what the pipeline labelled

| world | ground truth predicts | base: TRAIN avg R (n) / TEST avg R (n), label | selected: TRAIN / TEST avg R (TEST n), label | ML: TRAIN avg R (in-sample) / TEST avg R (TEST n), label | verdict | matches truth? |
|---|---|---|---|---|---|---|
| null | nothing ROBUST | -0.138 (98) / -0.121 (58), NO-EDGE | `rr2.5_vol2_rsi50-70` -0.086 / -0.143 (49), NO-EDGE | +0.267 / -0.294 (17), UNTESTED | No robust result found. | yes |
| planted | base ROBUST | +0.296 (115) / **+0.412 (51), ROBUST**, TEST 90% CI [+0.059, +0.765] | `rr3_vol2_rsi50-70` +0.836 / +0.829 (35), **ROBUST** | +0.489 / +0.235 (34), UNTESTED | ROBUST: base, selected | yes |
| decay | TRAIN-ONLY or UNTESTED, never ROBUST | +0.307 (114) / -0.143 (56), TRAIN-ONLY | `rr3_vol2_rsi50-70` +0.742 / -0.043 (46), TRAIN-ONLY | +0.590 / -0.073 (55), TRAIN-ONLY | No robust result found. | yes |
| hour_edge | base diluted; hour-aware ML should raise TEST expectancy | -0.044 (100) / +0.050 (60), NO-EDGE | `rr3_vol2_rsi50-70` +0.203 / +0.436 (39), UNTESTED | +0.299 / **+0.295** (44), UNTESTED, TEST 90% CI [-0.045, +0.636] | No robust result found. | partly: ML raised TEST expectancy (+0.050R to +0.295R), as predicted, but a real edge exists and was not demonstrated (a false negative) |

In the null run the ML layer's in-sample TRAIN avg R was +0.267R, against -0.294R on TEST
(n=17). This is why the report labels a fitted layer's TRAIN column "in-sample" and never
lets it stand in for validation.

## Calibration over seeds

Every rate carries its 90% Wilson interval, because the samples are small. Labels combine
each candidate's TRAIN and TEST windows (`metrics.label`).

| world (seeds) | truth | verdict ROBUST (any of 3 candidates; labels use TRAIN + TEST) | base ROBUST | selected ROBUST | ML ROBUST | other labels (TRAIN + TEST) |
|---|---|---|---|---|---|---|
| null (20) | no edge | **false-positive rate 0/20 = 0% (90% CI 0-12%)** | 0/20 | 0/20 | 0/20 | base NO-EDGE 17, UNTESTED 3; selected NO-EDGE 8, UNTESTED 8, TRAIN-ONLY 4; ML UNTESTED 15, NO-EDGE 3, TRAIN-ONLY 2 |
| planted (10) | real edge in TRAIN and TEST | **detection rate 8/10 = 80% (54-93%)** | 8/10 = 80% (54-93%) | 6/10 = 60% (35-81%) | 7/10 = 70% (44-87%) | seeds 6 and 10 were missed by all three (UNTESTED) |
| decay (10) | edge only in TRAIN | **false-positive rate 1/10 = 10% (2-35%)** (seed 3) | 1/10 | 0/10 | 0/10 | base **TRAIN-ONLY 6/10 = 60% (35-81%)**, **UNTESTED 3/10 = 30% (13-56%)**, TRAIN-ONLY or UNTESTED 9/10 = 90% (65-98%); selected TRAIN-ONLY 6, UNTESTED 4; ML TRAIN-ONLY 6, UNTESTED 4 |
| hour_edge (10) | edge only for spike candles closing 12:00-20:00 UTC | detection rate 4/10 = 40% (19-65%) | 1/10 = 10% (2-35%) | 3/10 = 30% (13-56%) | 3/10 = 30% (13-56%) | **ML TEST avg R above the base's in 9/10** |

Mean avg R per trade over the seeds:

| world | base TRAIN | base TEST | selected TRAIN (selection-biased) | selected TEST | ML TRAIN (in-sample) | ML TEST | mean TEST n, base / ML |
|---|---:|---:|---:|---:|---:|---:|---:|
| null | -0.107 | -0.021 | +0.022 | -0.008 | +0.002 | -0.204 | 43.9 / 23.7 |
| planted | +0.450 | +0.460 | +0.724 | +0.603 | +0.501 | +0.442 | 48.3 / 44.6 |
| decay | +0.424 | -0.101 | +0.683 | -0.061 | +0.500 | -0.090 | 43.7 / 43.1 |
| hour_edge | +0.178 | +0.185 | +0.486 | +0.368 | +0.376 | +0.342 | 50.8 / 42.7 |

### Did the ML layer help in hour_edge?

On TEST, yes in 9 of 10 seeds. The mean TEST avg R rose from +0.185R (base) to +0.342R
(ML). The exception was seed 10 (+0.267R vs +0.286R). The ML layer was ROBUST in 3/10 seeds,
against 1/10 for the base.

The fitted coefficients show it learned the planted hour effect rather than inventing one.
These are standardised coefficients, fitted on TRAIN only, from the `ml_hour_sin`,
`ml_hour_cos` and `ml_top_feature` columns of the calibration CSVs:

- `hour_sin` was negative in **10/10** hour_edge seeds, against 11/20 null seeds (a coin
  flip). Candle closes at 12:00, 16:00 and 20:00 UTC have sin(2 pi h / 24) <= 0, so a
  negative sign points at the planted hours. `hour_cos` was negative in 9/10.
- The largest-magnitude coefficient was an hour term in 6/10 hour_edge seeds, against 5/20
  null seeds.
- The mean hour-term magnitude sqrt(hour_sin^2 + hour_cos^2) was 0.464 log-odds per TRAIN s.d.
  (range 0.144-0.745) in hour_edge, against 0.149 (0.031-0.303) in null and 0.192 in planted.

Where there was nothing extra to learn, the layer cost power. In planted its TEST avg R beat
the base in only 3/10 seeds (+0.442R vs +0.460R on average). In null it made TEST worse
(-0.204R vs -0.021R; worse in 15/20 seeds, better in 5) while its in-sample TRAIN looked better than the
base's (+0.002R vs -0.107R). It removed about half the TEST trades, which left it UNTESTED
in 15/20 null seeds.

## Where the methodology fails or is weak (honest list)

1. **One ROBUST false positive, in decay seed 3.** The baseline had TRAIN +0.360R (n=111)
   and TEST +0.501R (n=39), with a TEST 90% CI low of +0.116. It was labelled ROBUST with a
   passing drawdown check, although the TEST period is exactly the null world. Its adoption
   record would pass the automated HUMAN_REVIEW gate. Null seed 3 has the same post-split
   noise, and its baseline TEST was +0.542R (CI low +0.128). It was stopped only because its
   TRAIN avg R was -0.124R (NO-EDGE). The lesson: once a genuine TRAIN edge exists that then
   disappears (a regime change), the TEST CI is the only guard. A 90% two-sided bootstrap CI
   leaves about a 5% one-sided chance per look. The measured decay false-positive rate is
   1/10 = 10% (90% CI 2-35%). This is why a ROBUST walk-forward is followed by a human
   review of every trade and at least 2 weeks of testnet, and why one split is never enough.
2. **The verdict takes three looks at TEST** (base, selected, ML). Against a single
   pre-registered look, this inflates the per-run false-positive rate by up to 3x (the
   Bonferroni bound). The looks share one TEST window and overlapping trades, so they are
   positively correlated and the real inflation is smaller, but it is not zero. Observed
   verdict false positives: 0/20 in null and 1/10 in decay. The decay one came from the base
   look alone. A stricter verdict would require a 1 - 0.10/3 CI per candidate. It is not
   applied here, and the report states the three looks.
3. **Power is limited by the TEST sample.** The TEST window is 1.8 years with 33-61 baseline
   trades per seed. The per-trade standard deviation of R is about 1.5 (outcomes are mostly
   -1 or +2). So with n near 45, the 90% CI lower bound clears zero only when the observed
   avg R is above about +0.37R.
   - planted: the baseline missed a real +0.46R average edge in 2/10 seeds. Seed 6 had
     +0.354R (n=41, CI low -0.012) and seed 10 had +0.171R (n=41).
   - hour_edge: the verdict found the real, but diluted, edge in only 4/10 seeds.

   UNTESTED means "not demonstrated", not "no edge".
4. **The calibration samples are small and paired.** 0 false positives in 20 null seeds
   only bounds the false-positive rate below 12% (90% Wilson upper bound). Bounding it below
   5% needs 0 false positives in at least 52 seeds. The four worlds also share the same noise
   for a given seed (`synthetic.py`; only the planted drift differs), so decay seed k's TEST
   window is null seed k's. Decay seeds 4, 5, 7 and 8 reproduce null's baseline TEST numbers
   exactly. Null and decay together are therefore about 20 independent TEST draws, not 30.
   Pairing is good for comparing worlds, and bad for counting false positives as if they
   were independent.
5. **Discovery can only choose among the variants' TRAIN numbers, and those are biased.** In
   null the selected variant's TRAIN avg R (+0.022R) was higher than the base's (-0.107R),
   which is the best-of-12 selection bias. Its TEST was no better (-0.008R vs -0.021R; it
   beat the base in 9/20 seeds), the expected no-free-lunch result. In planted it beat the
   base on TEST in 9/10 seeds (+0.603R vs +0.460R), yet it was ROBUST less often (6/10 vs
   8/10). The tighter variants trade less, so their TEST CI is wider (for example decay seed
   3's selected variant had only 29 TEST trades).
6. **The synthetic effect is a deliberately strong positive control** (hand-calibrated in
   `synthetic.py`). Real edges, if any exist, are likely weaker. And as the fee example above
   shows, realistic Coinbase fees can remove an edge of this size entirely. On real data,
   expect UNTESTED far more often than ROBUST.
7. **The noise level of a roughly 45-trade TEST window is about +/-0.5R.** Null seed 3's
   baseline TEST was +0.542R with no edge at all. Never read a single TEST expectancy
   without its CI and its TRAIN column.

Apart from these power limits and the one decay false positive, no mislabelling of ground
truth was found: 0 ROBUST in 20 null seeds, and no invariant violations. There was also no
TRAIN-selection leak: `test_strategy_discovery.py` and `test_walkforward.py` show that
swapping in different TEST-period candles cannot change the selection, the ML fit or the ML
model fingerprint.

## How to run this on REAL data (needs a machine with network access)

```bash
# from the repo root, on a networked machine
pip install ccxt
python -m research.trendbot.fetch_data --exchange binance \
    --pairs BTC/USDT ETH/USDT BNB/USDT --timeframe 4h --since 2019-01-01 --out research/data

# news calendar (R5): build research/data/events.csv with the header
#   time_utc,scope,impact,kind,note
# from a real economic calendar (high-impact macro events, scope ALL) and Binance
# announcements (BNB burns / launchpools, scope BNB). research/trendbot/events_example.csv
# shows the format only; it is NOT a real calendar. Without --events the report states
# that R5 could not be exercised historically.

# Binance spot: pass YOUR fee tier (0.001 = 0.10% taker, no BNB discount) and slippage
python -m research.trendbot.run_research --data-dir research/data \
    --events research/data/events.csv \
    --fee-rate 0.001 --slippage-pct 0.05 --exchange-id binance \
    --out-dir research/results/real_binance

# Coinbase Advanced Trade: low-tier taker fees are several times Binance's; pass the real
# tier (e.g. 0.006 for 0.6% per side). BNB/USDT is not listed there.
python -m research.trendbot.fetch_data --exchange coinbase \
    --pairs BTC/USDT ETH/USDT --timeframe 4h --since 2019-01-01 --out research/data/coinbase
python -m research.trendbot.run_research --data-dir research/data/coinbase \
    --pairs BTC/USDT ETH/USDT --events research/data/events.csv \
    --fee-rate 0.006 --slippage-pct 0.05 --exchange-id coinbase \
    --out-dir research/results/real_coinbase

# adaptations the bot would apply at the end of TEST (R9 benches / halt): backtest journals
# need --backtest-journal (exits count from the exit candle's close). REPORT.md section 9
# prints this command with the right --equity and --now.
python -m research.trendbot.journal_rules \
    --journal research/results/real_binance/journals/base_test.csv \
    --equity <TEST final equity from REPORT.md> --now <end of TEST from REPORT.md> \
    --backtest-journal

# adoption gate (only ROBUST + a passing drawdown check can proceed). REPORT.md section 11
# prints the exact command per record; a non-default config needs --config, and the ML
# record needs the model fingerprint:
python -m research.trendbot.adoption check \
    --record research/results/real_binance/adoption_base.json --stage HUMAN_REVIEW \
    --config research/results/real_binance/config_base.json  # only if that file exists
python -m research.trendbot.adoption check \
    --record research/results/real_binance/adoption_base_plus_ml.json --stage HUMAN_REVIEW \
    --model-fingerprint <sha256 printed in REPORT.md section 5>

# independent re-audit of the BASELINE journal with default costs (TEST journal: --start-ts
# = the split in ms, see REPORT.md section 2). The invariants CLI assumes the default
# StrategyConfig, so for a run with non-default --fee-rate/--slippage-pct, or for a
# discovery variant, rely on the in-process audit in REPORT.md section 8.
python -m research.trendbot.invariants \
    --journal research/results/real_binance/journals/base_test.csv \
    --data-dir research/data --events research/data/events.csv --start-ts <split_ms>
```

The adoption path is the same for real data and cannot be shortcut: backtest ->
walk-forward (ROBUST + drawdown check) -> a named human reviews EVERY trade in the review
pack -> at least 2 weeks on Binance testnet with zero rule violations -> live. The ML
variant is adopted as a (config, model) pair. **Refitting the model changes its fingerprint
and restarts the adoption path at BACKTEST.**

## Risk disclaimer

This is a research and testing tool, not financial advice. Backtests and synthetic worlds are
simplified models, and past or simulated results do not predict future results. Crypto
trading can result in the total loss of the capital used.
