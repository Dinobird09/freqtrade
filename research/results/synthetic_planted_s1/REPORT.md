# Walk-forward research report: synthetic world `planted` seed 1

## 1. No win rate is promised or targeted

This report does NOT promise, target or optimise a win rate, and nothing in this package selects on one. A win rate is meaningless without the reward:risk it was earned at: with the mandatory minimum of 2:1 a strategy breaks even at about 33% winners before costs, while a 90% win rate with a 1:9 payoff still loses money. A backtest win rate near 90% is evidence of curve-fitting or look-ahead, not of skill. The target metric is POSITIVE EXPECTANCY (average R per trade, net of fees and slippage) that survives a 70/30 chronological walk-forward with controlled drawdown, with every trade planned at reward:risk >= 2:1. Win rate appears below only as context.

## 2. Data provenance

- Source: **SYNTHETIC** world `planted`, seed 1, 6 years of 4H candles (13140 per pair) from 2019-01-01T00:00:00Z, pairs BTC/USDT, ETH/USDT, BNB/USDT (`synthetic.generate_world`).
- Ground truth: after every up-closing volume-spike candle the next 12 candles get extra positive drift (0.7 sigma per candle) over the WHOLE history; the drift is offset elsewhere, so buy-and-hold gains nothing from it.
- What the ground truth predicts: the base rules (which require a volume spike) should show positive expectancy on TRAIN and TEST; ROBUST is the correct label when the TEST sample is large enough.
- Qualifying triggers planted per pair (before / after the 70% split): BTC/USDT 342 / 147, ETH/USDT 333 / 134, BNB/USDT 319 / 123.
- **Synthetic results are verification of the methodology, not evidence about real markets.** No real BTC/ETH/BNB data was used in this run.
- News calendar loaded: yes (synthetic, 273 events: high macro 147, high regulatory 24, high unlock 6, medium bnb_burn 24, medium launchpool 72). The events have no price impact; they exercise R5 only.
- Walk-forward split: 2023-03-14T00:00:00Z = 70% of the common time range of all pairs (TRAIN = signal candles before it, TEST = at or after it, up to 2024-12-30T00:00:00Z). TEST starts with fresh equity and fresh circuit breakers; indicators warm up on earlier candles only.
- Costs: fee 0.10% per side, slippage 0.05% on market fills; starting capital 10,000 per window.

## 3. Baseline: TRAIN vs TEST

Default mandate config `rr2_vol1.5_rsi50-70` (variant `base`). TRAIN window start .. 2023-03-14T00:00:00Z; TEST window 2023-03-14T00:00:00Z .. 2024-12-30T00:00:00Z.

| metric | TRAIN | TEST |
|---|---:|---:|
| trades (n) | 115 | 53 |
| avg R (expectancy, the target metric) | +0.254 | +0.405 |
| 90% bootstrap CI of avg R | [+0.028, +0.475] | [+0.066, +0.746] |
| t-stat of avg R | +1.83 | +1.95 |
| total R | +29.21 | +21.46 |
| profit factor | 1.52 | 1.76 |
| max drawdown % | 8.01% | 5.18% |
| max drawdown R | 9.93 | 6.36 |
| win rate (context only, never a target) | 44.3% | 49.1% |
| exits SL / TP / END | 64 / 50 / 1 | 27 / 26 / 0 |
| avg hold (h) | 108.5 | 80.1 |

**Label: ROBUST.** Expectancy is positive on train (+0.254R, n=115) and on test (+0.405R, n=53) with the test 90% bootstrap CI [+0.066, +0.746]R above zero.

Drawdown check (TEST max drawdown <= 20%): passed. Test max drawdown 5.18% (6.36R) is within the 20.00% limit.

## 4. Strategy discovery: TRAIN vs TEST

K = 13 variants tried: 12 legal tightenings (reward:risk {2, 2.5, 3} x volume multiple {1.5, 2} x RSI window {50-70, 55-70}) plus 1 explicit test-only variant with the R4 regime filter OFF, which is reported but can never be selected or adopted.

Selection rule: highest TRAIN t-stat among selectable variants with >= 30 TRAIN trades. rr2.5_vol2_rsi55-70 has the highest TRAIN t-stat (+3.32, avg +0.644R over 81 trades) among 12 eligible variants; chosen on TRAIN only, before any TEST backtest was run.

Order of operations actually run: TRAIN backtests: 13 variants, candles before the split only -> selection recorded: rr2.5_vol2_rsi55-70 -> TEST backtests: 13 variants, reported side by side only.

| variant | status | TRAIN n | TRAIN avg R | TRAIN 90% CI | TRAIN t | TEST n | TEST avg R | TEST 90% CI | TEST t | TEST max DD % | label |
|---|---|---:|---:|---|---:|---:|---:|---|---:|---:|---|
| `rr2_vol1.5_rsi50-70` | selectable | 115 | +0.254 | [+0.028, +0.475] | +1.83 | 53 | +0.405 | [+0.066, +0.746] | +1.95 | 5.18% | ROBUST |
| `rr2_vol1.5_rsi55-70` | selectable | 108 | +0.313 | [+0.073, +0.540] | +2.18 | 53 | +0.349 | [+0.011, +0.688] | +1.68 | 5.16% | ROBUST |
| `rr2_vol2_rsi50-70` | selectable | 95 | +0.438 | [+0.170, +0.690] | +2.85 | 41 | +0.687 | [+0.312, +1.059] | +2.93 | 3.79% | ROBUST |
| `rr2_vol2_rsi55-70` | selectable | 88 | +0.491 | [+0.230, +0.754] | +3.08 | 39 | +0.702 | [+0.317, +1.090] | +2.93 | 3.79% | ROBUST |
| `rr2.5_vol1.5_rsi50-70` | selectable | 108 | +0.378 | [+0.103, +0.644] | +2.28 | 46 | +0.377 | [-0.071, +0.769] | +1.47 | 5.18% | UNTESTED |
| `rr2.5_vol1.5_rsi55-70` | selectable | 99 | +0.406 | [+0.125, +0.692] | +2.34 | 46 | +0.302 | [-0.086, +0.689] | +1.19 | 5.16% | UNTESTED |
| `rr2.5_vol2_rsi50-70` | selectable | 89 | +0.607 | [+0.297, +0.903] | +3.28 | 36 | +0.777 | [+0.289, +1.262] | +2.63 | 4.13% | ROBUST |
| `rr2.5_vol2_rsi55-70` | **SELECTED** | 81 | +0.644 | [+0.317, +0.958] | +3.32 | 34 | +0.784 | [+0.269, +1.298] | +2.58 | 4.13% | ROBUST |
| `rr3_vol1.5_rsi50-70` | selectable | 95 | +0.429 | [+0.109, +0.760] | +2.16 | 45 | +0.620 | [+0.090, +1.072] | +2.08 | 4.66% | ROBUST |
| `rr3_vol1.5_rsi55-70` | selectable | 87 | +0.429 | [+0.084, +0.778] | +2.07 | 45 | +0.532 | [+0.086, +1.060] | +1.80 | 4.66% | ROBUST |
| `rr3_vol2_rsi50-70` | selectable | 82 | +0.712 | [+0.370, +1.071] | +3.25 | 36 | +0.929 | [+0.371, +1.484] | +2.75 | 4.13% | ROBUST |
| `rr3_vol2_rsi55-70` | selectable | 75 | +0.719 | [+0.344, +1.115] | +3.13 | 34 | +0.931 | [+0.342, +1.518] | +2.67 | 4.13% | ROBUST |
| `rr2_vol1.5_rsi50-70_regimeOFF` | TEST-ONLY (regime OFF, never selectable) | 157 | +0.179 | [-0.016, +0.381] | +1.52 | 73 | +0.386 | [+0.097, +0.670] | +2.20 | 5.99% | ROBUST |

**Re-selecting a variant on these TEST numbers would turn TEST into TRAIN: the selection was fixed on TRAIN before any TEST backtest ran, and it is not revisited.**

The TRAIN column of the selected variant `rr2.5_vol2_rsi55-70` is biased upward by the selection itself (it is the best of 12); only its TEST column is an out-of-sample test. The labels of the other variants are context only and do not enter the verdict.

**Label: ROBUST.** Expectancy is positive on train (+0.644R, n=81) and on test (+0.784R, n=34) with the test 90% bootstrap CI [+0.269, +1.298]R above zero.

Drawdown check (TEST max drawdown <= 20%): passed. Test max drawdown 4.13% (4.38R) is within the 20.00% limit.

## 5. ML filter layer: TRAIN (in-sample) vs TEST

Variant `base+ml`: the baseline rules plus the `L_ml_filter` layer, a logistic regression on 6 features (rsi, vol_ratio, ema_gap_pct, dist_regime_pct, hour_sin, hour_cos). It can only REMOVE trades that passed every mandatory rule. It is fitted ONLY on TRAIN candidates and applied unchanged to TEST, so its TRAIN numbers are IN-SAMPLE.

Logistic filter (l2=1) fitted on 268 TRAIN candidates (base win rate 41.8%) keeps signals whose predicted win probability exceeds the break-even 0.357.

- TRAIN candidates enumerated (purged: outcome resolved before the split): 268; used for the fit: 268 (0 skipped for missing features); TRAIN candidate win share 41.8% (context only).
- Threshold p* = break-even win probability from the TRAIN avg win +1.93R and avg loss -1.07R: p* = 0.357 (no threshold search).
- L2 penalty 1 (fixed, not tuned); intercept -0.360.

| feature | standardised coefficient (log-odds per TRAIN s.d.) |
|---|---:|
| `rsi` | +0.098 |
| `vol_ratio` | +0.267 |
| `ema_gap_pct` | -0.500 |
| `dist_regime_pct` | -0.175 |
| `hour_sin` | +0.192 |
| `hour_cos` | +0.193 |

| metric | base TRAIN | base+ml TRAIN (in-sample) | base TEST | base+ml TEST |
|---|---:|---:|---:|---:|
| trades (n) | 115 | 101 | 53 | 36 |
| avg R (expectancy, the target metric) | +0.254 | +0.437 | +0.405 | +0.186 |
| 90% bootstrap CI of avg R | [+0.028, +0.475] | [+0.186, +0.675] | [+0.066, +0.746] | [-0.233, +0.604] |
| t-stat of avg R | +1.83 | +2.91 | +1.95 | +0.74 |
| total R | +29.21 | +44.13 | +21.46 | +6.69 |
| profit factor | 1.52 | 1.79 | 1.76 | 1.42 |
| max drawdown % | 8.01% | 4.81% | 5.18% | 4.17% |
| max drawdown R | 9.93 | 5.49 | 6.36 | 5.31 |
| win rate (context only, never a target) | 44.3% | 50.5% | 49.1% | 41.7% |
| exits SL / TP / END | 64 / 50 / 1 | 50 / 50 / 1 | 27 / 26 / 0 | 21 / 15 / 0 |
| avg hold (h) | 108.5 | 91.9 | 80.1 | 96.7 |

On TEST the filter removed 27 signals that had passed every mandatory rule; TEST expectancy +0.405R (base) -> +0.186R (with the filter).

**Label: UNTESTED.** Test expectancy is positive at +0.186R (n=36) but its 90% bootstrap CI [-0.233, +0.604]R includes zero, so it is not distinguishable from no edge.

Drawdown check (TEST max drawdown <= 20%): passed. Test max drawdown 4.17% (5.31R) is within the 20.00% limit.

LightGBM, other gradient boosting and neural networks are NOT justified at this sample size (268 TRAIN candidates, 6 features): a flexible model would memorise noise, so only a fixed-penalty logistic regression with a break-even threshold is allowed.

## 6. Verdict

Candidates judged (pre-registered, one TEST look each): baseline `base`, discovery-selected `rr2.5_vol2_rsi55-70`, ML layer `base+ml`. Other grid variants are excluded because choosing among them by TEST numbers would be selection on TEST. The verdict requires the ROBUST label (positive TRAIN and TEST expectancy with the TEST 90% CI above zero) and reports the drawdown check.

ROBUST: baseline `base` (TEST avg +0.405R, 90% CI [+0.066, +0.746]R, n=53; drawdown check passed); discovery-selected `rr2.5_vol2_rsi55-70` (TEST avg +0.784R, 90% CI [+0.269, +1.298]R, n=34; drawdown check passed).

## 7. Why signals were rejected (decision counts per rule)

Signal candles denied per rule (the FIRST failing rule is counted, so the R1-R4 rows include every candle that was simply not a signal). Exits are never gated.

| rule | base TRAIN | base TEST | selected rr2.5_vol2_rsi55-70 TRAIN | selected rr2.5_vol2_rsi55-70 TEST | base+ml TRAIN | base+ml TEST | meaning |
|---|---:|---:|---:|---:|---:|---:|---|
| `R1_trend` | 18476 | 7756 | 18476 | 7756 | 18476 | 7756 | Trend gate: close > EMA9 and close > EMA21 and EMA9 > EMA21 (4H) |
| `R2_momentum` | 3390 | 1684 | 4002 | 1881 | 3390 | 1684 | Momentum: RSI(14) within [50, 70] on the entry candle |
| `R3_volume` | 5271 | 2193 | 4846 | 2070 | 5271 | 2193 | Volume: entry-candle volume >= 1.5x the previous-20-candle average |
| `R4_regime` | 176 | 74 | 94 | 46 | 176 | 74 | Regime: close > EMA200 (unless explicitly testing it off) |
| `R5_news_blackout` | 9 | 8 | 6 | 5 | 9 | 8 | No entries within +/-2h of high-impact news; BNB +/-24h burns/launchpool |
| `R6_correlation_cap` | 156 | 57 | 88 | 33 | 116 | 47 | BTC/ETH/BNB share one risk budget; BNB never stacks |
| `R7_position_risk` | 0 | 0 | 0 | 0 | 0 | 0 | Risk <= 1% (BTC/ETH) / 0.5% (BNB); size derived from stop distance |
| `R8_structure_stop` | 1 | 1 | 1 | 1 | 2 | 1 | Stop behind a confirmed swing low with a buffer (BNB 0.5-0.8%) |
| `R9_circuit_breaker` | 0 | 0 | 0 | 0 | 0 | 0 | 3 consecutive SLs bench a pair 24h; 7d realized loss limit halts all |
| `L_ml_filter` | 0 | 0 | 0 | 0 | 53 | 27 | Layer: logistic-regression filter (only removes trades, never adds) |
| `L_expectancy_guard` | 0 | 0 | 0 | 0 | 0 | 0 | Layer: halve a pair's risk while its last-N-trade expectancy < 0 |
| `X_capital` | 0 | 0 | 0 | 0 | 0 | 0 | Execution: not enough free capital / size below minimum |
| candles evaluated | 27594 | 11826 | 27594 | 11826 | 27594 | 11826 | |
| trades taken | 115 | 53 | 81 | 34 | 101 | 36 | |

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

**baseline `base`** (TEST final equity 12,029.39):

No active adaptations.

**discovery-selected `rr2.5_vol2_rsi55-70`** (TEST final equity 12,483.89):

No active adaptations.

**ML layer `base+ml`** (TEST final equity 10,760.52):

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
- `rr2.5_vol2_rsi55-70`: [review_selected_rr2.5_vol2_rsi55-70/trades_review.md](review_selected_rr2.5_vol2_rsi55-70/trades_review.md) and [review_selected_rr2.5_vol2_rsi55-70/trades_review.csv](review_selected_rr2.5_vol2_rsi55-70/trades_review.csv)

## 11. Adoption path and current stage

The only path to real money (enforced by `adoption.py`; no step can be skipped and the promoted config must match the tested config's sha256 fingerprint):

backtest -> walk-forward (ROBUST + drawdown check) -> human review of EVERY trade -> >= 2 weeks on Binance testnet with zero rule violations -> live.

Stages: BACKTEST -> WALK_FORWARD -> HUMAN_REVIEW -> TESTNET -> LIVE. Stage reached by this run: **WALK_FORWARD** (recorded). Next: **HUMAN_REVIEW**.

**This run used SYNTHETIC data: its records only demonstrate the mechanism. A synthetic result can never justify adopting a variant; the path must be run on real candles from the backtest stage.**

- `base`: record [adoption_base.json](adoption_base.json); promotion to HUMAN_REVIEW is allowed: a named person must now review every trade in its review pack, then fill `human_review` in the record before any testnet run.
- `rr2.5_vol2_rsi55-70`: record [adoption_rr2.5_vol2_rsi55-70.json](adoption_rr2.5_vol2_rsi55-70.json); promotion to HUMAN_REVIEW is allowed: a named person must now review every trade in its review pack, then fill `human_review` in the record before any testnet run.

The ML layer `base+ml` has no adoption record: its fitted coefficients are not part of `StrategyConfig`, so the config fingerprint cannot pin them. The regime-OFF variant is test-only and can never be adopted.

Check a record with `python -m research.trendbot.adoption check --record research/results/synthetic_planted_s1/adoption_base.json --stage HUMAN_REVIEW`.

## 12. Risk disclaimer

This is a research and testing tool, not financial advice and not a recommendation to trade. Backtests and synthetic worlds are simplified models; past or simulated results do not predict future results. Crypto trading can result in the total loss of the capital used.
