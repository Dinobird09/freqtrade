# Results summary: synthetic verification of the research methodology

**NO real BTC/ETH/BNB data was backtested.** The build sandbox has no network access (and
no `ccxt`), so every number below comes from `synthetic.py` worlds with KNOWN ground truth.
They verify the methodology (does the pipeline find an edge where one was planted, and refuse
to find one where there is none?). They are **not evidence about real markets**, and no
variant here may be adopted on the strength of them. The exact commands to run the same
pipeline on real data are at the end.

No win rate is promised or targeted anywhere in this package. Win rate without reward:risk
is meaningless (at the mandatory 2:1 a strategy breaks even near 33% winners), and a backtest
win rate near 90% would be a red flag for curve-fitting or look-ahead. The target is
positive expectancy (avg R per trade, net of fees and slippage) that survives the 70/30
chronological walk-forward with controlled drawdown.

## What was run (all actually executed, outputs in this folder)

| folder | command |
|---|---|
| `synthetic_null_s1/` | `python -m research.trendbot.run_research --synthetic null --seed 1 --out-dir research/results/synthetic_null_s1` |
| `synthetic_planted_s1/` | same with `--synthetic planted` |
| `synthetic_decay_s1/` | same with `--synthetic decay` |
| `synthetic_hour_edge_s1/` | same with `--synthetic hour_edge` |
| `calibration_null/` | `python -m research.trendbot.run_research --synthetic null --calibrate-seeds 20 --workers 4 --out-dir research/results/calibration_null` |
| `calibration_planted/` | same with `--synthetic planted --calibrate-seeds 10` |
| `calibration_decay/` | same with `--synthetic decay --calibrate-seeds 10` |
| `calibration_hour_edge/` | same with `--synthetic hour_edge --calibrate-seeds 10` |

Each run is 6 years of 4H candles (13,140 per pair; BTC/USDT, ETH/USDT, BNB/USDT), split at
70% of the common range (2023-03-14T00:00Z). "Base" is the mandate config; "selected" is the
one discovery variant chosen by TRAIN t-stat out of 12 legal tightenings (K = 13 variants
tried including the test-only regime-OFF one, which is never selectable); "ML" is the
baseline plus the logistic `L_ml_filter` fitted on purged TRAIN candidates only. The verdict
considers only these three pre-registered candidates. Every run's `run.log` holds its
console output, and each `REPORT.md` holds the full 12-section report.

`invariants.check_invariants` was run on every backtest of every run: **0 violations**
across 30 backtests x 54 pipeline runs (4 seed-1 runs + 50 calibration seeds).

## Seed 1: ground truth vs what the pipeline labelled

| world | ground truth predicts | base (TRAIN / TEST avg R, TEST n) | selected (TEST avg R, n) | ML (TEST avg R, n) | verdict | matches truth? |
|---|---|---|---|---|---|---|
| null | nothing ROBUST | NO-EDGE (-0.109 / -0.204, 58) | `rr2_vol2_rsi50-70` NO-EDGE (-0.258, 54) | UNTESTED (-0.246, 14) | No robust result found. | yes |
| planted | base ROBUST (edge in TRAIN and TEST) | **ROBUST** (+0.254 / +0.405, 53; TEST 90% CI [+0.066, +0.746]) | `rr2.5_vol2_rsi55-70` **ROBUST** (+0.784, 34) | UNTESTED (+0.186, 36) | ROBUST: base, selected | yes |
| decay | TRAIN-ONLY or UNTESTED, never ROBUST | TRAIN-ONLY (+0.279 / -0.201, 58) | `rr3_vol2_rsi50-70` TRAIN-ONLY (-0.158, 47) | TRAIN-ONLY (-0.086, 54) | No robust result found. | yes |
| hour_edge | base diluted; hour-aware ML should improve TEST | NO-EDGE (-0.017 / +0.052, 61) | `rr3_vol2_rsi50-70` UNTESTED (+0.307, 43) | **ROBUST** (+0.575, 42; TEST 90% CI [+0.213, +0.938]) | ROBUST: ML layer | yes |

In the null seed-1 run the ML layer's TRAIN numbers were +0.430R (in-sample, n=40) against
-0.246R on TEST (n=14). That gap is exactly why the report labels fitted-layer TRAIN numbers
"in-sample" and never lets them stand in for validation.

## Calibration over seeds

Rates carry a 90% Wilson interval (the samples are small).

| world (seeds) | truth | verdict ROBUST (any of 3) | base ROBUST | selected ROBUST | ML ROBUST | other |
|---|---|---|---|---|---|---|
| null (20) | no edge | **0/20 = 0% (90% CI 0-12%)** false positives | 0/20 | 0/20 | 0/20 | base labels: NO-EDGE 18, UNTESTED 2 |
| planted (10) | real edge in TRAIN and TEST | **9/10 = 90% (65-98%)** detection | 8/10 (54-93%) | 8/10 | 7/10 | selected TEST avg R > base in 10/10 (mean +0.603 vs +0.444) |
| decay (10) | edge only in TRAIN | **0/10 = 0% (0-21%)** false positives | 0/10 | 0/10 | 0/10 | base TRAIN-ONLY 7/10, UNTESTED 3/10 (**10/10 = 100%**, 79-100%); mean base TRAIN +0.399R vs TEST -0.152R |
| hour_edge (10) | edge only for 12-20 UTC spike closes | 6/10 = 60% (35-81%) | 0/10 | 2/10 | **5/10** (27-73%) | **ML TEST avg R > base in 8/10**; mean TEST avg R +0.170 (base) -> +0.368 (ML) |

Did ML help in hour_edge on TEST? Yes, in 8 of 10 seeds. Across the 10 seeds the fitted
standardised `hour_sin` coefficient was negative in **10/10** hour_edge seeds (the planted
12-20 UTC direction; mean about -0.40) against 4/10 in null seeds, where the hour signs are
noise (|hour coef| <= 0.18). The largest-magnitude coefficient was an hour feature in 6/10
hour_edge seeds and in 0/10 null seeds. The filter learned the planted hour effect; it did
not invent one. (Measured with `MLFilter.fit` on the same purged TRAIN candidates the
pipeline uses.)

Mean TEST expectancy per candidate (avg R per trade, TEST window only):

| world | base | selected | ML | mean TEST n base / ML |
|---|---:|---:|---:|---:|
| null | -0.037 | -0.052 | -0.166 | 45.8 / 23.8 |
| planted | +0.444 | +0.603 | +0.434 | 50.5 / 46.1 |
| decay | -0.152 | -0.166 | -0.124 | 44.8 / 44.2 |
| hour_edge | +0.170 | +0.258 | +0.368 | 52.1 / 42.8 |

## Where the methodology fails or is weak (honest list)

1. **Power is limited by the TEST sample (about 40-60 trades in 1.8 years).** In planted, a
   +0.4R edge that really exists was missed by the baseline in 2/10 seeds (labelled UNTESTED
   because the TEST 90% CI touched zero: seed 6 +0.367R n=43, seed 10 +0.142R n=42), and by
   all three candidates in 1/10 seeds (seed 10). UNTESTED means "not demonstrated", not
   "no edge".
2. **hour_edge false negatives:** a real (hour-specific) edge exists in every seed, but the
   verdict found it in only 6/10. The hour-blind baseline was never ROBUST (0/10: NO-EDGE 2,
   TRAIN-ONLY 2, UNTESTED 6). The ML layer made TEST expectancy worse than the base in 2/10
   seeds (seed 5 +0.304 vs +0.370, seed 8 +0.271 vs +0.299).
3. **The ML layer costs power when there is nothing extra to learn.** In planted it did not
   help (TEST avg R above the base in 5/10, means +0.434 vs +0.444) and was ROBUST less
   often than the base (7/10 vs 8/10) because it removes trades. In null its TEST
   expectancy was worse than the base on average (-0.166 vs -0.037), while its in-sample TRAIN
   numbers looked good. It was never ROBUST there, and was TRAIN-ONLY 3/20 and UNTESTED 15/20.
4. **Calibration samples are small.** 0/20 null false positives only bounds the ROBUST
   false-positive rate below about 12% (90% Wilson upper bound); 0/10 in decay only bounds
   it below about 21%. Bounding it below 5% (90% Wilson, zero false positives) would need
   at least 52 null seeds.
5. **The verdict takes three looks at TEST** (base, selected, ML), which inflates the
   per-run false-positive rate by up to about 3x versus a single look. Observed: still 0/30
   across null + decay.
6. **Discovery works here because the world rewards it:** in planted the TRAIN-selected
   variant beat the base on TEST in 10/10 seeds (vol_mult 2.0 variants isolate the planted
   volume spikes better). In null the selected variant was no better than the base (TEST
   -0.052 vs -0.037), which is the expected no-free-lunch result. Its TRAIN numbers are
   selection-biased (best of 12) and the report says so.
7. **The synthetic effect is a deliberately strong positive control** (0.7 sigma per candle
   over 12 candles, hand-calibrated in `synthetic.py`). Real edges, if any exist, are likely
   weaker, so on real data expect UNTESTED far more often than ROBUST.
8. The 3 decay seeds labelled UNTESTED (not TRAIN-ONLY) had positive TEST noise (+0.259,
   +0.100, +0.090R) in a TEST period that is exactly the null world. That is correct under
   the rules (not ROBUST), but it shows the noise level of a ~45-trade TEST window: about
   +/-0.3R.

No mislabelling of ground truth beyond these power limits was found: there were no ROBUST
false positives in null or decay, no invariant violations, and no TRAIN-selection leak
(`test_strategy_discovery.py` proves that altering TEST candles cannot change the selection
or the ML fit).

## How to run this on REAL data (needs a machine with network access)

```bash
# from the repo root, on a networked machine
pip install ccxt
python -m research.trendbot.fetch_data --exchange binance \
    --pairs BTC/USDT ETH/USDT BNB/USDT --timeframe 4h --since 2019-01-01 --out research/data

# news calendar (R5): build research/data/events.csv with header
#   time_utc,scope,impact,kind,note
# from a real economic calendar (high-impact macro events, scope ALL) and Binance
# announcements (BNB burns / launchpools, scope BNB). research/trendbot/events_example.csv
# shows the format only; it is NOT a real calendar. Without --events the report states
# that R5 could not be exercised historically.

python -m research.trendbot.run_research --data-dir research/data \
    --events research/data/events.csv --out-dir research/results/real_binance

# independent re-audit of a journal (TEST journal: --start-ts = split in ms, see REPORT.md)
python -m research.trendbot.invariants --journal research/results/real_binance/journals/base_test.csv \
    --data-dir research/data --events research/data/events.csv --start-ts <split_ms>

# adoption gate (only a ROBUST walk-forward with a passing drawdown check can proceed)
python -m research.trendbot.adoption check \
    --record research/results/real_binance/adoption_base.json --stage HUMAN_REVIEW
```

The adoption path is the same for real data and cannot be shortcut: backtest -> walk-forward
(ROBUST + drawdown check) -> a named human reviews EVERY trade in the review pack -> at least 2
weeks on Binance testnet with zero rule violations -> live.

## Risk disclaimer

This is a research and testing tool, not financial advice. Backtests and synthetic worlds are
simplified models, and past or simulated results do not predict future results. Crypto
trading can result in the total loss of the capital used.
