# Walk-forward research report: synthetic world `hour_edge` seed 1

## 1. No win rate is promised or targeted

This report does NOT promise, target or optimise a win rate, and nothing in this package selects on one. A win rate is meaningless without the reward:risk it was earned at: with the mandatory minimum of 2:1 a strategy breaks even at about 33% winners before costs, while a 90% win rate with a 1:9 payoff still loses money. A backtest win rate near 90% is evidence of curve-fitting or look-ahead, not of skill. The target metric is POSITIVE EXPECTANCY (average R per trade, net of fees and slippage) that survives a 70/30 chronological walk-forward with controlled drawdown, with every trade planned at reward:risk >= 2:1. Win rate appears below only as context.

## 2. Data provenance

- Source: **SYNTHETIC** world `hour_edge`, seed 1, 6 years of 4H candles (13140 per pair) from 2019-01-01T00:00:00Z, pairs BTC/USDT, ETH/USDT, BNB/USDT (`synthetic.generate_world`).
- Ground truth: the planted effect exists only for spike candles closing 12:00-20:00 UTC; spikes closing at other hours trigger nothing.
- What the ground truth predicts: the hour-blind base is diluted (weak or no edge); an hour-aware filter fitted on TRAIN has something real to learn and should raise TEST expectancy versus the base.
- Qualifying triggers planted per pair (before / after the 70% split): BTC/USDT 165 / 77, ETH/USDT 153 / 65, BNB/USDT 168 / 63.
- **Synthetic results are verification of the methodology, not evidence about real markets.** No real BTC/ETH/BNB data was used in this run.
- News calendar loaded: yes (synthetic, 273 events: high macro 147, high regulatory 24, high unlock 6, medium bnb_burn 24, medium launchpool 72). The events have no price impact; they exercise R5 only.
- Walk-forward split: 2023-03-14T00:00:00Z = 70% of the common time range of all pairs (TRAIN = signal candles before it, TEST = at or after it, up to 2024-12-30T00:00:00Z). TEST starts with fresh equity and fresh circuit breakers; indicators warm up on earlier candles only.
- Costs: fee 0.10% per side, slippage 0.05% on market fills; starting capital 10,000 per window.

## 3. Baseline: TRAIN vs TEST

Default mandate config `rr2_vol1.5_rsi50-70` (variant `base`). TRAIN window start .. 2023-03-14T00:00:00Z; TEST window 2023-03-14T00:00:00Z .. 2024-12-30T00:00:00Z.

| metric | TRAIN | TEST |
|---|---:|---:|
| trades (n) | 104 | 61 |
| avg R (expectancy, the target metric) | -0.017 | +0.052 |
| 90% bootstrap CI of avg R | [-0.250, +0.218] | [-0.249, +0.353] |
| t-stat of avg R | -0.12 | +0.28 |
| total R | -1.81 | +3.20 |
| profit factor | 0.91 | 1.05 |
| max drawdown % | 14.96% | 9.82% |
| max drawdown R | 13.29 | 11.50 |
| win rate (context only, never a target) | 35.6% | 37.7% |
| exits SL / TP / END | 67 / 36 / 1 | 38 / 23 / 0 |
| avg hold (h) | 124.7 | 84.7 |

**Label: NO-EDGE.** Train expectancy is -0.017R over 104 trades, so there is no edge to validate.

Drawdown check (TEST max drawdown <= 20%): passed. Test max drawdown 9.82% (11.50R) is within the 20.00% limit.

## 4. Strategy discovery: TRAIN vs TEST

K = 13 variants tried: 12 legal tightenings (reward:risk {2, 2.5, 3} x volume multiple {1.5, 2} x RSI window {50-70, 55-70}) plus 1 explicit test-only variant with the R4 regime filter OFF, which is reported but can never be selected or adopted.

Selection rule: highest TRAIN t-stat among selectable variants with >= 30 TRAIN trades. rr3_vol2_rsi50-70 has the highest TRAIN t-stat (+0.76, avg +0.161R over 76 trades) among 12 eligible variants; chosen on TRAIN only, before any TEST backtest was run.

Order of operations actually run: TRAIN backtests: 13 variants, candles before the split only -> selection recorded: rr3_vol2_rsi50-70 -> TEST backtests: 13 variants, reported side by side only.

| variant | status | TRAIN n | TRAIN avg R | TRAIN 90% CI | TRAIN t | TEST n | TEST avg R | TEST 90% CI | TEST t | TEST max DD % | label |
|---|---|---:|---:|---|---:|---:|---:|---|---:|---:|---|
| `rr2_vol1.5_rsi50-70` | selectable | 104 | -0.017 | [-0.250, +0.218] | -0.12 | 61 | +0.052 | [-0.249, +0.353] | +0.28 | 9.82% | NO-EDGE |
| `rr2_vol1.5_rsi55-70` | selectable | 95 | +0.026 | [-0.219, +0.255] | +0.17 | 59 | +0.090 | [-0.217, +0.405] | +0.47 | 8.38% | UNTESTED |
| `rr2_vol2_rsi50-70` | selectable | 83 | +0.071 | [-0.190, +0.327] | +0.44 | 44 | +0.139 | [-0.213, +0.487] | +0.62 | 5.35% | UNTESTED |
| `rr2_vol2_rsi55-70` | selectable | 78 | +0.032 | [-0.238, +0.299] | +0.19 | 42 | +0.127 | [-0.240, +0.499] | +0.55 | 5.35% | UNTESTED |
| `rr2.5_vol1.5_rsi50-70` | selectable | 98 | -0.019 | [-0.290, +0.243] | -0.12 | 56 | +0.106 | [-0.266, +0.432] | +0.47 | 9.05% | NO-EDGE |
| `rr2.5_vol1.5_rsi55-70` | selectable | 90 | +0.081 | [-0.199, +0.355] | +0.46 | 55 | +0.128 | [-0.248, +0.505] | +0.56 | 8.57% | UNTESTED |
| `rr2.5_vol2_rsi50-70` | selectable | 79 | +0.103 | [-0.201, +0.413] | +0.55 | 42 | +0.245 | [-0.178, +0.671] | +0.92 | 5.35% | UNTESTED |
| `rr2.5_vol2_rsi55-70` | selectable | 74 | +0.091 | [-0.233, +0.420] | +0.47 | 41 | +0.277 | [-0.157, +0.713] | +1.02 | 5.35% | UNTESTED |
| `rr3_vol1.5_rsi50-70` | selectable | 96 | +0.029 | [-0.251, +0.328] | +0.16 | 54 | +0.177 | [-0.261, +0.562] | +0.69 | 6.10% | UNTESTED |
| `rr3_vol1.5_rsi55-70` | selectable | 88 | +0.091 | [-0.219, +0.417] | +0.47 | 53 | +0.201 | [-0.196, +0.591] | +0.77 | 5.99% | UNTESTED |
| `rr3_vol2_rsi50-70` | **SELECTED** | 76 | +0.161 | [-0.190, +0.494] | +0.76 | 43 | +0.307 | [-0.162, +0.788] | +1.04 | 5.35% | UNTESTED |
| `rr3_vol2_rsi55-70` | selectable | 71 | +0.139 | [-0.232, +0.492] | +0.64 | 42 | +0.339 | [-0.134, +0.829] | +1.13 | 5.35% | UNTESTED |
| `rr2_vol1.5_rsi50-70_regimeOFF` | TEST-ONLY (regime OFF, never selectable) | 153 | +0.243 | [+0.044, +0.445] | +2.01 | 80 | +0.048 | [-0.221, +0.316] | +0.29 | 9.25% | UNTESTED |

**Re-selecting a variant on these TEST numbers would turn TEST into TRAIN: the selection was fixed on TRAIN before any TEST backtest ran, and it is not revisited.**

The TRAIN column of the selected variant `rr3_vol2_rsi50-70` is biased upward by the selection itself (it is the best of 12); only its TEST column is an out-of-sample test. The labels of the other variants are context only and do not enter the verdict.

**Label: UNTESTED.** Test expectancy is positive at +0.307R (n=43) but its 90% bootstrap CI [-0.162, +0.788]R includes zero, so it is not distinguishable from no edge.

Drawdown check (TEST max drawdown <= 20%): passed. Test max drawdown 5.35% (6.72R) is within the 20.00% limit.

## 5. ML filter layer: TRAIN (in-sample) vs TEST

Variant `base+ml`: the baseline rules plus the `L_ml_filter` layer, a logistic regression on 6 features (rsi, vol_ratio, ema_gap_pct, dist_regime_pct, hour_sin, hour_cos). It can only REMOVE trades that passed every mandatory rule. It is fitted ONLY on TRAIN candidates and applied unchanged to TEST, so its TRAIN numbers are IN-SAMPLE.

Logistic filter (l2=1) fitted on 249 TRAIN candidates (base win rate 35.3%) keeps signals whose predicted win probability exceeds the break-even 0.361.

- TRAIN candidates enumerated (purged: outcome resolved before the split): 249; used for the fit: 249 (0 skipped for missing features); TRAIN candidate win share 35.3% (context only).
- Threshold p* = break-even win probability from the TRAIN avg win +1.92R and avg loss -1.09R: p* = 0.361 (no threshold search).
- L2 penalty 1 (fixed, not tuned); intercept -0.633.

| feature | standardised coefficient (log-odds per TRAIN s.d.) |
|---|---:|
| `rsi` | +0.203 |
| `vol_ratio` | +0.161 |
| `ema_gap_pct` | -0.199 |
| `dist_regime_pct` | +0.094 |
| `hour_sin` | -0.392 |
| `hour_cos` | -0.055 |

| metric | base TRAIN | base+ml TRAIN (in-sample) | base TEST | base+ml TEST |
|---|---:|---:|---:|---:|
| trades (n) | 104 | 71 | 61 | 42 |
| avg R (expectancy, the target metric) | -0.017 | +0.438 | +0.052 | +0.575 |
| 90% bootstrap CI of avg R | [-0.250, +0.218] | [+0.142, +0.722] | [-0.249, +0.353] | [+0.213, +0.938] |
| t-stat of avg R | -0.12 | +2.46 | +0.28 | +2.46 |
| total R | -1.81 | +31.08 | +3.20 | +24.17 |
| profit factor | 0.91 | 1.77 | 1.05 | 2.16 |
| max drawdown % | 14.96% | 7.34% | 9.82% | 3.15% |
| max drawdown R | 13.29 | 7.58 | 11.50 | 4.25 |
| win rate (context only, never a target) | 35.6% | 50.7% | 37.7% | 54.8% |
| exits SL / TP / END | 67 / 36 / 1 | 35 / 35 / 1 | 38 / 23 / 0 | 19 / 23 / 0 |
| avg hold (h) | 124.7 | 87.8 | 84.7 | 81.3 |

On TEST the filter removed 34 signals that had passed every mandatory rule; TEST expectancy +0.052R (base) -> +0.575R (with the filter).

**Label: ROBUST.** Expectancy is positive on train (+0.438R, n=71) and on test (+0.575R, n=42) with the test 90% bootstrap CI [+0.213, +0.938]R above zero.

Drawdown check (TEST max drawdown <= 20%): passed. Test max drawdown 3.15% (4.25R) is within the 20.00% limit.

LightGBM, other gradient boosting and neural networks are NOT justified at this sample size (249 TRAIN candidates, 6 features): a flexible model would memorise noise, so only a fixed-penalty logistic regression with a break-even threshold is allowed.

## 6. Verdict

Candidates judged (pre-registered, one TEST look each): baseline `base`, discovery-selected `rr3_vol2_rsi50-70`, ML layer `base+ml`. Other grid variants are excluded because choosing among them by TEST numbers would be selection on TEST. The verdict requires the ROBUST label (positive TRAIN and TEST expectancy with the TEST 90% CI above zero) and reports the drawdown check.

ROBUST: ML layer `base+ml` (TEST avg +0.575R, 90% CI [+0.213, +0.938]R, n=42; drawdown check passed).

## 7. Why signals were rejected (decision counts per rule)

Signal candles denied per rule (the FIRST failing rule is counted, so the R1-R4 rows include every candle that was simply not a signal). Exits are never gated.

| rule | base TRAIN | base TEST | selected rr3_vol2_rsi50-70 TRAIN | selected rr3_vol2_rsi50-70 TEST | base+ml TRAIN | base+ml TEST | meaning |
|---|---:|---:|---:|---:|---:|---:|---|
| `R1_trend` | 19100 | 7899 | 19100 | 7899 | 19100 | 7899 | Trend gate: close > EMA9 and close > EMA21 and EMA9 > EMA21 (4H) |
| `R2_momentum` | 2680 | 1500 | 2680 | 1500 | 2680 | 1500 | Momentum: RSI(14) within [50, 70] on the entry candle |
| `R3_volume` | 5352 | 2212 | 5517 | 2293 | 5352 | 2212 | Volume: entry-candle volume >= 1.5x the previous-20-candle average |
| `R4_regime` | 191 | 69 | 126 | 42 | 191 | 69 | Regime: close > EMA200 (unless explicitly testing it off) |
| `R5_news_blackout` | 17 | 7 | 11 | 6 | 17 | 7 | No entries within +/-2h of high-impact news; BNB +/-24h burns/launchpool |
| `R6_correlation_cap` | 146 | 76 | 83 | 42 | 82 | 62 | BTC/ETH/BNB share one risk budget; BNB never stacks |
| `R7_position_risk` | 0 | 0 | 0 | 0 | 0 | 0 | Risk <= 1% (BTC/ETH) / 0.5% (BNB); size derived from stop distance |
| `R8_structure_stop` | 2 | 1 | 1 | 1 | 3 | 1 | Stop behind a confirmed swing low with a buffer (BNB 0.5-0.8%) |
| `R9_circuit_breaker` | 2 | 1 | 0 | 0 | 0 | 0 | 3 consecutive SLs bench a pair 24h; 7d realized loss limit halts all |
| `L_ml_filter` | 0 | 0 | 0 | 0 | 98 | 34 | Layer: logistic-regression filter (only removes trades, never adds) |
| `L_expectancy_guard` | 0 | 0 | 0 | 0 | 0 | 0 | Layer: halve a pair's risk while its last-N-trade expectancy < 0 |
| `X_capital` | 0 | 0 | 0 | 0 | 0 | 0 | Execution: not enough free capital / size below minimum |
| candles evaluated | 27594 | 11826 | 27594 | 11826 | 27594 | 11826 | |
| trades taken | 104 | 61 | 76 | 43 | 71 | 42 | |

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

**baseline `base`** (TEST final equity 10,159.93):

No active adaptations.

**discovery-selected `rr3_vol2_rsi50-70`** (TEST final equity 11,204.29):

No active adaptations.

**ML layer `base+ml`** (TEST final equity 12,118.70):

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

- `base`: record [adoption_base.json](adoption_base.json); promotion to HUMAN_REVIEW is BLOCKED: [ADOPT_walk_forward] walk_forward.label is 'NO-EDGE' (the train window showed no positive expectancy to validate), and only ROBUST may proceed.
- `rr3_vol2_rsi50-70`: record [adoption_rr3_vol2_rsi50-70.json](adoption_rr3_vol2_rsi50-70.json); promotion to HUMAN_REVIEW is BLOCKED: [ADOPT_walk_forward] walk_forward.label is 'UNTESTED' (too few trades, or a test expectancy not distinguishable from zero), and only ROBUST may proceed.

The ML layer `base+ml` has no adoption record: its fitted coefficients are not part of `StrategyConfig`, so the config fingerprint cannot pin them. The regime-OFF variant is test-only and can never be adopted.

Check a record with `python -m research.trendbot.adoption check --record research/results/synthetic_hour_edge_s1/adoption_base.json --stage HUMAN_REVIEW`.

## 12. Risk disclaimer

This is a research and testing tool, not financial advice and not a recommendation to trade. Backtests and synthetic worlds are simplified models; past or simulated results do not predict future results. Crypto trading can result in the total loss of the capital used.
