# Walk-forward research report: synthetic world `decay` seed 1

## 1. No win rate is promised or targeted

This report does NOT promise, target or optimise a win rate, and nothing in this package selects on one. A win rate is meaningless without the reward:risk it was earned at: with the mandatory minimum of 2:1 a strategy breaks even at about 33% winners before costs, while a 90% win rate with a 1:9 payoff still loses money. A backtest win rate near 90% is evidence of curve-fitting or look-ahead, not of skill. The target metric is POSITIVE EXPECTANCY (average R per trade, net of fees and slippage) that survives a 70/30 chronological walk-forward with controlled drawdown, with every trade planned at reward:risk >= 2:1. Win rate appears below only as context.

## 2. Data provenance

- Source: **SYNTHETIC** world `decay`, seed 1, 6 years of 4H candles (13140 per pair) from 2019-01-01T00:00:00Z, pairs BTC/USDT, ETH/USDT, BNB/USDT (`synthetic.generate_world`).
- Ground truth: the planted effect exists only BEFORE the 70% split; the TEST period is exactly the null world.
- What the ground truth predicts: a positive TRAIN expectancy that does not survive TEST: TRAIN-ONLY (or UNTESTED when the TEST noise is positive but indistinguishable from zero); ROBUST is a false positive.
- Qualifying triggers planted per pair (before / after the 70% split): BTC/USDT 341 / 0, ETH/USDT 333 / 0, BNB/USDT 317 / 0.
- **Synthetic results are verification of the methodology, not evidence about real markets.** No real BTC/ETH/BNB data was used in this run.
- News calendar loaded: yes (synthetic, 273 events: high macro 147, high regulatory 24, high unlock 6, medium bnb_burn 24, medium launchpool 72). The events have no price impact; they exercise R5 only.
- Walk-forward split: 2023-03-14T00:00:00Z = 70% of the common time range of all pairs (TRAIN = signal candles before it, TEST = at or after it, up to 2024-12-30T00:00:00Z). TEST starts with fresh equity and fresh circuit breakers; indicators warm up on earlier candles only.
- Costs: fee 0.10% per side, slippage 0.05% on market fills; starting capital 10,000 per window.

## 3. Baseline: TRAIN vs TEST

Default mandate config `rr2_vol1.5_rsi50-70` (variant `base`). TRAIN window start .. 2023-03-14T00:00:00Z; TEST window 2023-03-14T00:00:00Z .. 2024-12-30T00:00:00Z.

| metric | TRAIN | TEST |
|---|---:|---:|
| trades (n) | 115 | 58 |
| avg R (expectancy, the target metric) | +0.279 | -0.201 |
| 90% bootstrap CI of avg R | [+0.049, +0.504] | [-0.471, +0.114] |
| t-stat of avg R | +2.00 | -1.10 |
| total R | +32.03 | -11.66 |
| profit factor | 1.59 | 0.72 |
| max drawdown % | 8.76% | 15.13% |
| max drawdown R | 10.78 | 17.74 |
| win rate (context only, never a target) | 45.2% | 29.3% |
| exits SL / TP / END | 63 / 51 / 1 | 41 / 17 / 0 |
| avg hold (h) | 100.7 | 112.4 |

**Label: TRAIN-ONLY.** Train expectancy +0.279R (n=115) did not hold out of sample: test expectancy is -0.201R (n=58), which suggests curve-fitting.

Drawdown check (TEST max drawdown <= 20%): passed. Test max drawdown 15.13% (17.74R) is within the 20.00% limit.

## 4. Strategy discovery: TRAIN vs TEST

K = 13 variants tried: 12 legal tightenings (reward:risk {2, 2.5, 3} x volume multiple {1.5, 2} x RSI window {50-70, 55-70}) plus 1 explicit test-only variant with the R4 regime filter OFF, which is reported but can never be selected or adopted.

Selection rule: highest TRAIN t-stat among selectable variants with >= 30 TRAIN trades. rr3_vol2_rsi50-70 has the highest TRAIN t-stat (+3.04, avg +0.678R over 79 trades) among 12 eligible variants; chosen on TRAIN only, before any TEST backtest was run.

Order of operations actually run: TRAIN backtests: 13 variants, candles before the split only -> selection recorded: rr3_vol2_rsi50-70 -> TEST backtests: 13 variants, reported side by side only.

| variant | status | TRAIN n | TRAIN avg R | TRAIN 90% CI | TRAIN t | TEST n | TEST avg R | TEST 90% CI | TEST t | TEST max DD % | label |
|---|---|---:|---:|---|---:|---:|---:|---|---:|---:|---|
| `rr2_vol1.5_rsi50-70` | selectable | 115 | +0.279 | [+0.049, +0.504] | +2.00 | 58 | -0.201 | [-0.471, +0.114] | -1.10 | 15.13% | TRAIN-ONLY |
| `rr2_vol1.5_rsi55-70` | selectable | 106 | +0.338 | [+0.093, +0.571] | +2.33 | 59 | -0.162 | [-0.439, +0.147] | -0.88 | 15.26% | TRAIN-ONLY |
| `rr2_vol2_rsi50-70` | selectable | 93 | +0.405 | [+0.155, +0.643] | +2.60 | 54 | -0.255 | [-0.545, +0.042] | -1.36 | 15.80% | TRAIN-ONLY |
| `rr2_vol2_rsi55-70` | selectable | 86 | +0.387 | [+0.113, +0.658] | +2.40 | 55 | -0.210 | [-0.497, +0.118] | -1.12 | 14.22% | TRAIN-ONLY |
| `rr2.5_vol1.5_rsi50-70` | selectable | 107 | +0.390 | [+0.128, +0.665] | +2.34 | 47 | -0.187 | [-0.558, +0.191] | -0.83 | 12.96% | TRAIN-ONLY |
| `rr2.5_vol1.5_rsi55-70` | selectable | 96 | +0.413 | [+0.120, +0.688] | +2.34 | 52 | -0.134 | [-0.479, +0.265] | -0.61 | 12.96% | TRAIN-ONLY |
| `rr2.5_vol2_rsi50-70` | selectable | 87 | +0.563 | [+0.256, +0.851] | +3.01 | 45 | -0.153 | [-0.542, +0.244] | -0.65 | 11.41% | TRAIN-ONLY |
| `rr2.5_vol2_rsi55-70` | selectable | 79 | +0.509 | [+0.196, +0.824] | +2.60 | 47 | -0.113 | [-0.487, +0.269] | -0.48 | 10.56% | TRAIN-ONLY |
| `rr3_vol1.5_rsi50-70` | selectable | 98 | +0.421 | [+0.116, +0.729] | +2.16 | 50 | -0.206 | [-0.603, +0.198] | -0.86 | 14.70% | TRAIN-ONLY |
| `rr3_vol1.5_rsi55-70` | selectable | 90 | +0.377 | [+0.047, +0.706] | +1.86 | 56 | -0.153 | [-0.514, +0.214] | -0.66 | 14.33% | TRAIN-ONLY |
| `rr3_vol2_rsi50-70` | **SELECTED** | 79 | +0.678 | [+0.325, +1.034] | +3.04 | 47 | -0.158 | [-0.574, +0.273] | -0.62 | 13.18% | TRAIN-ONLY |
| `rr3_vol2_rsi55-70` | selectable | 72 | +0.571 | [+0.204, +0.934] | +2.47 | 49 | -0.108 | [-0.519, +0.309] | -0.43 | 11.92% | TRAIN-ONLY |
| `rr2_vol1.5_rsi50-70_regimeOFF` | TEST-ONLY (regime OFF, never selectable) | 154 | +0.165 | [-0.031, +0.373] | +1.39 | 66 | -0.206 | [-0.477, +0.070] | -1.21 | 17.27% | TRAIN-ONLY |

**Re-selecting a variant on these TEST numbers would turn TEST into TRAIN: the selection was fixed on TRAIN before any TEST backtest ran, and it is not revisited.**

The TRAIN column of the selected variant `rr3_vol2_rsi50-70` is biased upward by the selection itself (it is the best of 12); only its TEST column is an out-of-sample test. The labels of the other variants are context only and do not enter the verdict.

**Label: TRAIN-ONLY.** Train expectancy +0.678R (n=79) did not hold out of sample: test expectancy is -0.158R (n=47), which suggests curve-fitting.

Drawdown check (TEST max drawdown <= 20%): passed. Test max drawdown 13.18% (14.88R) is within the 20.00% limit.

## 5. ML filter layer: TRAIN (in-sample) vs TEST

Variant `base+ml`: the baseline rules plus the `L_ml_filter` layer, a logistic regression on 6 features (rsi, vol_ratio, ema_gap_pct, dist_regime_pct, hour_sin, hour_cos). It can only REMOVE trades that passed every mandatory rule. It is fitted ONLY on TRAIN candidates and applied unchanged to TEST, so its TRAIN numbers are IN-SAMPLE.

Logistic filter (l2=1) fitted on 257 TRAIN candidates (base win rate 41.6%) keeps signals whose predicted win probability exceeds the break-even 0.357.

- TRAIN candidates enumerated (purged: outcome resolved before the split): 257; used for the fit: 257 (0 skipped for missing features); TRAIN candidate win share 41.6% (context only).
- Threshold p* = break-even win probability from the TRAIN avg win +1.93R and avg loss -1.07R: p* = 0.357 (no threshold search).
- L2 penalty 1 (fixed, not tuned); intercept -0.370.

| feature | standardised coefficient (log-odds per TRAIN s.d.) |
|---|---:|
| `rsi` | +0.081 |
| `vol_ratio` | +0.260 |
| `ema_gap_pct` | -0.543 |
| `dist_regime_pct` | -0.203 |
| `hour_sin` | +0.128 |
| `hour_cos` | +0.188 |

| metric | base TRAIN | base+ml TRAIN (in-sample) | base TEST | base+ml TEST |
|---|---:|---:|---:|---:|
| trades (n) | 115 | 94 | 58 | 54 |
| avg R (expectancy, the target metric) | +0.279 | +0.547 | -0.201 | -0.086 |
| 90% bootstrap CI of avg R | [+0.049, +0.504] | [+0.293, +0.806] | [-0.471, +0.114] | [-0.414, +0.248] |
| t-stat of avg R | +2.00 | +3.53 | -1.10 | -0.44 |
| total R | +32.03 | +51.42 | -11.66 | -4.63 |
| profit factor | 1.59 | 2.08 | 0.72 | 0.77 |
| max drawdown % | 8.76% | 4.29% | 15.13% | 13.03% |
| max drawdown R | 10.78 | 4.70 | 17.74 | 12.30 |
| win rate (context only, never a target) | 45.2% | 54.3% | 29.3% | 33.3% |
| exits SL / TP / END | 63 / 51 / 1 | 43 / 50 / 1 | 41 / 17 / 0 | 36 / 18 / 0 |
| avg hold (h) | 100.7 | 93.1 | 112.4 | 102.9 |

On TEST the filter removed 23 signals that had passed every mandatory rule; TEST expectancy -0.201R (base) -> -0.086R (with the filter).

**Label: TRAIN-ONLY.** Train expectancy +0.547R (n=94) did not hold out of sample: test expectancy is -0.086R (n=54), which suggests curve-fitting.

Drawdown check (TEST max drawdown <= 20%): passed. Test max drawdown 13.03% (12.30R) is within the 20.00% limit.

LightGBM, other gradient boosting and neural networks are NOT justified at this sample size (257 TRAIN candidates, 6 features): a flexible model would memorise noise, so only a fixed-penalty logistic regression with a break-even threshold is allowed.

## 6. Verdict

Candidates judged (pre-registered, one TEST look each): baseline `base`, discovery-selected `rr3_vol2_rsi50-70`, ML layer `base+ml`. Other grid variants are excluded because choosing among them by TEST numbers would be selection on TEST. The verdict requires the ROBUST label (positive TRAIN and TEST expectancy with the TEST 90% CI above zero) and reports the drawdown check.

No robust result found.

## 7. Why signals were rejected (decision counts per rule)

Signal candles denied per rule (the FIRST failing rule is counted, so the R1-R4 rows include every candle that was simply not a signal). Exits are never gated.

| rule | base TRAIN | base TEST | selected rr3_vol2_rsi50-70 TRAIN | selected rr3_vol2_rsi50-70 TEST | base+ml TRAIN | base+ml TEST | meaning |
|---|---:|---:|---:|---:|---:|---:|---|
| `R1_trend` | 18588 | 7612 | 18588 | 7612 | 18588 | 7612 | Trend gate: close > EMA9 and close > EMA21 and EMA9 > EMA21 (4H) |
| `R2_momentum` | 3304 | 772 | 3304 | 772 | 3304 | 772 | Momentum: RSI(14) within [50, 70] on the entry candle |
| `R3_volume` | 5246 | 3125 | 5404 | 3243 | 5246 | 3125 | Volume: entry-candle volume >= 1.5x the previous-20-candle average |
| `R4_regime` | 186 | 97 | 120 | 56 | 186 | 97 | Regime: close > EMA200 (unless explicitly testing it off) |
| `R5_news_blackout` | 9 | 8 | 6 | 6 | 9 | 8 | No entries within +/-2h of high-impact news; BNB +/-24h burns/launchpool |
| `R6_correlation_cap` | 145 | 152 | 92 | 89 | 109 | 132 | BTC/ETH/BNB share one risk budget; BNB never stacks |
| `R7_position_risk` | 0 | 0 | 0 | 0 | 0 | 0 | Risk <= 1% (BTC/ETH) / 0.5% (BNB); size derived from stop distance |
| `R8_structure_stop` | 1 | 2 | 1 | 0 | 2 | 2 | Stop behind a confirmed swing low with a buffer (BNB 0.5-0.8%) |
| `R9_circuit_breaker` | 0 | 0 | 0 | 1 | 0 | 1 | 3 consecutive SLs bench a pair 24h; 7d realized loss limit halts all |
| `L_ml_filter` | 0 | 0 | 0 | 0 | 56 | 23 | Layer: logistic-regression filter (only removes trades, never adds) |
| `L_expectancy_guard` | 0 | 0 | 0 | 0 | 0 | 0 | Layer: halve a pair's risk while its last-N-trade expectancy < 0 |
| `X_capital` | 0 | 0 | 0 | 0 | 0 | 0 | Execution: not enough free capital / size below minimum |
| candles evaluated | 27594 | 11826 | 27594 | 11826 | 27594 | 11826 | |
| trades taken | 115 | 58 | 79 | 47 | 94 | 54 | |

## 8. Invariant audit of every backtest

`invariants.check_invariants` independently re-derives every mandatory rule (R1-R9, 2:1 minimum, sizing from the stop, exits on the first touch, no data after the window) from each trade list and the candles.

| backtest | TRAIN violations | TEST violations |
|---|---:|---:|
| `base` | 0 | 0 |
| `rr2_vol1.5_rsi50-70` | 0 | 0 |
| `rr2_vol1.5_rsi55-70` | 0 | 0 |
| `rr2_vol2_rsi50-70` | 0 | 0 |
| `rr2_vol2_rsi55-70` | 0 | 0 |
| `rr2.5_vol1.5_rsi50-70` | 0 | 0 |
| `rr2.5_vol1.5_rsi55-70` | 0 | 0 |
| `rr2.5_vol2_rsi50-70` | 0 | 0 |
| `rr2.5_vol2_rsi55-70` | 0 | 0 |
| `rr3_vol1.5_rsi50-70` | 0 | 0 |
| `rr3_vol1.5_rsi55-70` | 0 | 0 |
| `rr3_vol2_rsi50-70` | 0 | 0 |
| `rr3_vol2_rsi55-70` | 0 | 0 |
| `rr2_vol1.5_rsi50-70_regimeOFF` | 0 | 0 |
| `base+ml` | 0 | 0 |

Result: CLEAN. 0 violations in 30 backtests.

## 9. Journal rules at the end of TEST

`journal_rules.audit` over each TEST journal at 2024-12-30T00:00:00Z (the end of TEST) with the TEST final equity: every adaptation the live bot would be applying right now because of past trades, with the journal rows that caused it.

**baseline `base`** (TEST final equity 8,933.56):

No active adaptations.

**discovery-selected `rr3_vol2_rsi50-70`** (TEST final equity 9,203.35):

No active adaptations.

**ML layer `base+ml`** (TEST final equity 9,209.19):

No active adaptations.


## 10. Journals and human review packs

Trade journals (`journal.write_journal`, one CSV per variant and window). TEST trade ids are offset past the TRAIN ids, so ids are unique across a variant's two journals and its review pack.

| variant | TRAIN journal | TEST journal |
|---|---|---|
| `base` | [journals/base_train.csv](journals/base_train.csv) | [journals/base_test.csv](journals/base_test.csv) |
| `rr2_vol1.5_rsi50-70` | [journals/rr2_vol1.5_rsi50-70_train.csv](journals/rr2_vol1.5_rsi50-70_train.csv) | [journals/rr2_vol1.5_rsi50-70_test.csv](journals/rr2_vol1.5_rsi50-70_test.csv) |
| `rr2_vol1.5_rsi55-70` | [journals/rr2_vol1.5_rsi55-70_train.csv](journals/rr2_vol1.5_rsi55-70_train.csv) | [journals/rr2_vol1.5_rsi55-70_test.csv](journals/rr2_vol1.5_rsi55-70_test.csv) |
| `rr2_vol2_rsi50-70` | [journals/rr2_vol2_rsi50-70_train.csv](journals/rr2_vol2_rsi50-70_train.csv) | [journals/rr2_vol2_rsi50-70_test.csv](journals/rr2_vol2_rsi50-70_test.csv) |
| `rr2_vol2_rsi55-70` | [journals/rr2_vol2_rsi55-70_train.csv](journals/rr2_vol2_rsi55-70_train.csv) | [journals/rr2_vol2_rsi55-70_test.csv](journals/rr2_vol2_rsi55-70_test.csv) |
| `rr2.5_vol1.5_rsi50-70` | [journals/rr2.5_vol1.5_rsi50-70_train.csv](journals/rr2.5_vol1.5_rsi50-70_train.csv) | [journals/rr2.5_vol1.5_rsi50-70_test.csv](journals/rr2.5_vol1.5_rsi50-70_test.csv) |
| `rr2.5_vol1.5_rsi55-70` | [journals/rr2.5_vol1.5_rsi55-70_train.csv](journals/rr2.5_vol1.5_rsi55-70_train.csv) | [journals/rr2.5_vol1.5_rsi55-70_test.csv](journals/rr2.5_vol1.5_rsi55-70_test.csv) |
| `rr2.5_vol2_rsi50-70` | [journals/rr2.5_vol2_rsi50-70_train.csv](journals/rr2.5_vol2_rsi50-70_train.csv) | [journals/rr2.5_vol2_rsi50-70_test.csv](journals/rr2.5_vol2_rsi50-70_test.csv) |
| `rr2.5_vol2_rsi55-70` | [journals/rr2.5_vol2_rsi55-70_train.csv](journals/rr2.5_vol2_rsi55-70_train.csv) | [journals/rr2.5_vol2_rsi55-70_test.csv](journals/rr2.5_vol2_rsi55-70_test.csv) |
| `rr3_vol1.5_rsi50-70` | [journals/rr3_vol1.5_rsi50-70_train.csv](journals/rr3_vol1.5_rsi50-70_train.csv) | [journals/rr3_vol1.5_rsi50-70_test.csv](journals/rr3_vol1.5_rsi50-70_test.csv) |
| `rr3_vol1.5_rsi55-70` | [journals/rr3_vol1.5_rsi55-70_train.csv](journals/rr3_vol1.5_rsi55-70_train.csv) | [journals/rr3_vol1.5_rsi55-70_test.csv](journals/rr3_vol1.5_rsi55-70_test.csv) |
| `rr3_vol2_rsi50-70` | [journals/rr3_vol2_rsi50-70_train.csv](journals/rr3_vol2_rsi50-70_train.csv) | [journals/rr3_vol2_rsi50-70_test.csv](journals/rr3_vol2_rsi50-70_test.csv) |
| `rr3_vol2_rsi55-70` | [journals/rr3_vol2_rsi55-70_train.csv](journals/rr3_vol2_rsi55-70_train.csv) | [journals/rr3_vol2_rsi55-70_test.csv](journals/rr3_vol2_rsi55-70_test.csv) |
| `rr2_vol1.5_rsi50-70_regimeOFF` | [journals/rr2_vol1.5_rsi50-70_regimeOFF_train.csv](journals/rr2_vol1.5_rsi50-70_regimeOFF_train.csv) | [journals/rr2_vol1.5_rsi50-70_regimeOFF_test.csv](journals/rr2_vol1.5_rsi50-70_regimeOFF_test.csv) |
| `base+ml` | [journals/base_plus_ml_train.csv](journals/base_plus_ml_train.csv) | [journals/base_plus_ml_test.csv](journals/base_plus_ml_test.csv) |

Human review packs (`review_sheet.write_review_pack`, TRAIN and TEST):

- `base`: [review_base/trades_review.md](review_base/trades_review.md) and [review_base/trades_review.csv](review_base/trades_review.csv)
- `rr3_vol2_rsi50-70`: [review_selected_rr3_vol2_rsi50-70/trades_review.md](review_selected_rr3_vol2_rsi50-70/trades_review.md) and [review_selected_rr3_vol2_rsi50-70/trades_review.csv](review_selected_rr3_vol2_rsi50-70/trades_review.csv)

## 11. Adoption path and current stage

The only path to real money (enforced by `adoption.py`; no step can be skipped and the promoted config must match the tested config's sha256 fingerprint):

backtest -> walk-forward (ROBUST + drawdown check) -> human review of EVERY trade -> >= 2 weeks on Binance testnet with zero rule violations -> live.

Stages: BACKTEST -> WALK_FORWARD -> HUMAN_REVIEW -> TESTNET -> LIVE. Stage reached by this run: **WALK_FORWARD** (recorded). Next: **HUMAN_REVIEW**.

**This run used SYNTHETIC data: its records only demonstrate the mechanism. A synthetic result can never justify adopting a variant; the path must be run on real candles from the backtest stage.**

- `base`: record [adoption_base.json](adoption_base.json); promotion to HUMAN_REVIEW is BLOCKED: [ADOPT_walk_forward] walk_forward.label is 'TRAIN-ONLY' (the edge did not hold out of sample, a sign of curve-fitting), and only ROBUST may proceed. [ADOPT_walk_forward] walk_forward.test_avg_r must be a positive out-of-sample expectancy in R (got -0.20102114436967852).
- `rr3_vol2_rsi50-70`: record [adoption_rr3_vol2_rsi50-70.json](adoption_rr3_vol2_rsi50-70.json); promotion to HUMAN_REVIEW is BLOCKED: [ADOPT_walk_forward] walk_forward.label is 'TRAIN-ONLY' (the edge did not hold out of sample, a sign of curve-fitting), and only ROBUST may proceed. [ADOPT_walk_forward] walk_forward.test_avg_r must be a positive out-of-sample expectancy in R (got -0.15755095990247586).

The ML layer `base+ml` has no adoption record: its fitted coefficients are not part of `StrategyConfig`, so the config fingerprint cannot pin them. The regime-OFF variant is test-only and can never be adopted.

Check a record with `python -m research.trendbot.adoption check --record research/results/synthetic_decay_s1/adoption_base.json --stage HUMAN_REVIEW`.

## 12. Risk disclaimer

This is a research and testing tool, not financial advice and not a recommendation to trade. Backtests and synthetic worlds are simplified models; past or simulated results do not predict future results. Crypto trading can result in the total loss of the capital used.
