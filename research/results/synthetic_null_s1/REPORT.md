# Walk-forward research report: synthetic world `null` seed 1

## 1. No win rate is promised or targeted

This report does NOT promise, target or optimise a win rate, and nothing in this package selects on one. A win rate is meaningless without the reward:risk it was earned at: with the mandatory minimum of 2:1 a strategy breaks even at about 33% winners before costs, while a 90% win rate with a 1:9 payoff still loses money. A backtest win rate near 90% is evidence of curve-fitting or look-ahead, not of skill. The target metric is POSITIVE EXPECTANCY (average R per trade, net of fees and slippage) that survives a 70/30 chronological walk-forward with controlled drawdown, with every trade planned at reward:risk >= 2:1. Win rate appears below only as context.

## 2. Data provenance

- Source: **SYNTHETIC** world `null`, seed 1, 6 years of 4H candles (13140 per pair) from 2019-01-01T00:00:00Z, pairs BTC/USDT, ETH/USDT, BNB/USDT (`synthetic.generate_world`).
- Ground truth: martingale prices (zero drift): no entry/exit rule has an edge before costs, so every strategy has negative expectancy after fees and slippage.
- What the ground truth predicts: nothing should be ROBUST; the expected labels are NO-EDGE, TRAIN-ONLY or UNTESTED, and a ROBUST label here is a false positive.
- Qualifying triggers planted per pair (before / after the 70% split): BTC/USDT 0 / 0, ETH/USDT 0 / 0, BNB/USDT 0 / 0.
- **Synthetic results are verification of the methodology, not evidence about real markets.** No real BTC/ETH/BNB data was used in this run.
- News calendar loaded: yes (synthetic, 273 events: high macro 147, high regulatory 24, high unlock 6, medium bnb_burn 24, medium launchpool 72). The events have no price impact; they exercise R5 only.
- Walk-forward split: 2023-03-14T00:00:00Z = 70% of the common time range of all pairs (TRAIN = signal candles before it, TEST = at or after it, up to 2024-12-30T00:00:00Z). TEST starts with fresh equity and fresh circuit breakers; indicators warm up on earlier candles only.
- Costs: fee 0.10% per side, slippage 0.05% on market fills; starting capital 10,000 per window.

## 3. Baseline: TRAIN vs TEST

Default mandate config `rr2_vol1.5_rsi50-70` (variant `base`). TRAIN window start .. 2023-03-14T00:00:00Z; TEST window 2023-03-14T00:00:00Z .. 2024-12-30T00:00:00Z.

| metric | TRAIN | TEST |
|---|---:|---:|
| trades (n) | 101 | 58 |
| avg R (expectancy, the target metric) | -0.109 | -0.204 |
| 90% bootstrap CI of avg R | [-0.333, +0.128] | [-0.472, +0.110] |
| t-stat of avg R | -0.78 | -1.12 |
| total R | -10.96 | -11.83 |
| profit factor | 0.77 | 0.71 |
| max drawdown % | 17.28% | 15.13% |
| max drawdown R | 16.71 | 17.74 |
| win rate (context only, never a target) | 31.7% | 29.3% |
| exits SL / TP / END | 68 / 32 / 1 | 41 / 17 / 0 |
| avg hold (h) | 164.0 | 111.7 |

**Label: NO-EDGE.** Train expectancy is -0.109R over 101 trades, so there is no edge to validate.

Drawdown check (TEST max drawdown <= 20%): passed. Test max drawdown 15.13% (17.74R) is within the 20.00% limit.

## 4. Strategy discovery: TRAIN vs TEST

K = 13 variants tried: 12 legal tightenings (reward:risk {2, 2.5, 3} x volume multiple {1.5, 2} x RSI window {50-70, 55-70}) plus 1 explicit test-only variant with the R4 regime filter OFF, which is reported but can never be selected or adopted.

Selection rule: highest TRAIN t-stat among selectable variants with >= 30 TRAIN trades. rr2_vol2_rsi50-70 has the highest TRAIN t-stat (-0.48, avg -0.070R over 96 trades) among 12 eligible variants; chosen on TRAIN only, before any TEST backtest was run.

Order of operations actually run: TRAIN backtests: 13 variants, candles before the split only -> selection recorded: rr2_vol2_rsi50-70 -> TEST backtests: 13 variants, reported side by side only.

| variant | status | TRAIN n | TRAIN avg R | TRAIN 90% CI | TRAIN t | TEST n | TEST avg R | TEST 90% CI | TEST t | TEST max DD % | label |
|---|---|---:|---:|---|---:|---:|---:|---|---:|---:|---|
| `rr2_vol1.5_rsi50-70` | selectable | 101 | -0.109 | [-0.333, +0.128] | -0.78 | 58 | -0.204 | [-0.472, +0.110] | -1.12 | 15.13% | NO-EDGE |
| `rr2_vol1.5_rsi55-70` | selectable | 96 | -0.120 | [-0.367, +0.106] | -0.85 | 59 | -0.165 | [-0.449, +0.144] | -0.90 | 15.26% | NO-EDGE |
| `rr2_vol2_rsi50-70` | **SELECTED** | 96 | -0.070 | [-0.317, +0.174] | -0.48 | 54 | -0.258 | [-0.549, +0.042] | -1.38 | 15.80% | NO-EDGE |
| `rr2_vol2_rsi55-70` | selectable | 86 | -0.191 | [-0.430, +0.026] | -1.29 | 55 | -0.213 | [-0.500, +0.113] | -1.14 | 14.22% | NO-EDGE |
| `rr2.5_vol1.5_rsi50-70` | selectable | 98 | -0.134 | [-0.385, +0.117] | -0.85 | 49 | -0.155 | [-0.512, +0.210] | -0.69 | 12.96% | NO-EDGE |
| `rr2.5_vol1.5_rsi55-70` | selectable | 93 | -0.156 | [-0.418, +0.108] | -0.98 | 54 | -0.107 | [-0.435, +0.281] | -0.50 | 12.96% | NO-EDGE |
| `rr2.5_vol2_rsi50-70` | selectable | 89 | -0.126 | [-0.391, +0.150] | -0.76 | 47 | -0.121 | [-0.498, +0.258] | -0.52 | 11.41% | NO-EDGE |
| `rr2.5_vol2_rsi55-70` | selectable | 81 | -0.242 | [-0.496, +0.023] | -1.46 | 49 | -0.083 | [-0.445, +0.281] | -0.36 | 10.56% | NO-EDGE |
| `rr3_vol1.5_rsi50-70` | selectable | 85 | -0.166 | [-0.453, +0.156] | -0.91 | 49 | -0.192 | [-0.546, +0.218] | -0.80 | 14.70% | NO-EDGE |
| `rr3_vol1.5_rsi55-70` | selectable | 85 | -0.211 | [-0.493, +0.073] | -1.19 | 55 | -0.140 | [-0.506, +0.289] | -0.60 | 14.33% | NO-EDGE |
| `rr3_vol2_rsi50-70` | selectable | 78 | -0.192 | [-0.460, +0.119] | -1.02 | 46 | -0.143 | [-0.572, +0.295] | -0.56 | 13.18% | NO-EDGE |
| `rr3_vol2_rsi55-70` | selectable | 74 | -0.304 | [-0.585, +0.016] | -1.65 | 48 | -0.093 | [-0.510, +0.331] | -0.37 | 11.92% | NO-EDGE |
| `rr2_vol1.5_rsi50-70_regimeOFF` | TEST-ONLY (regime OFF, never selectable) | 149 | -0.222 | [-0.406, -0.026] | -2.00 | 66 | -0.208 | [-0.479, +0.067] | -1.23 | 17.27% | NO-EDGE |

**Re-selecting a variant on these TEST numbers would turn TEST into TRAIN: the selection was fixed on TRAIN before any TEST backtest ran, and it is not revisited.**

The TRAIN column of the selected variant `rr2_vol2_rsi50-70` is biased upward by the selection itself (it is the best of 12); only its TEST column is an out-of-sample test. The labels of the other variants are context only and do not enter the verdict.

**Label: NO-EDGE.** Train expectancy is -0.070R over 96 trades, so there is no edge to validate.

Drawdown check (TEST max drawdown <= 20%): passed. Test max drawdown 15.80% (19.43R) is within the 20.00% limit.

## 5. ML filter layer: TRAIN (in-sample) vs TEST

Variant `base+ml`: the baseline rules plus the `L_ml_filter` layer, a logistic regression on 6 features (rsi, vol_ratio, ema_gap_pct, dist_regime_pct, hour_sin, hour_cos). It can only REMOVE trades that passed every mandatory rule. It is fitted ONLY on TRAIN candidates and applied unchanged to TEST, so its TRAIN numbers are IN-SAMPLE.

Logistic filter (l2=1) fitted on 407 TRAIN candidates (base win rate 28.0%) keeps signals whose predicted win probability exceeds the break-even 0.359.

- TRAIN candidates enumerated (purged: outcome resolved before the split): 407; used for the fit: 407 (0 skipped for missing features); TRAIN candidate win share 28.0% (context only).
- Threshold p* = break-even win probability from the TRAIN avg win +1.92R and avg loss -1.08R: p* = 0.359 (no threshold search).
- L2 penalty 1 (fixed, not tuned); intercept -0.973.

| feature | standardised coefficient (log-odds per TRAIN s.d.) |
|---|---:|
| `rsi` | -0.107 |
| `vol_ratio` | -0.106 |
| `ema_gap_pct` | +0.171 |
| `dist_regime_pct` | -0.345 |
| `hour_sin` | +0.031 |
| `hour_cos` | -0.064 |

| metric | base TRAIN | base+ml TRAIN (in-sample) | base TEST | base+ml TEST |
|---|---:|---:|---:|---:|
| trades (n) | 101 | 40 | 58 | 14 |
| avg R (expectancy, the target metric) | -0.109 | +0.430 | -0.204 | -0.246 |
| 90% bootstrap CI of avg R | [-0.333, +0.128] | [+0.054, +0.810] | [-0.472, +0.110] | [-0.887, +0.414] |
| t-stat of avg R | -0.78 | +1.79 | -1.12 | -0.64 |
| total R | -10.96 | +17.21 | -11.83 | -3.44 |
| profit factor | 0.77 | 1.46 | 0.71 | 0.94 |
| max drawdown % | 17.28% | 3.72% | 15.13% | 7.31% |
| max drawdown R | 16.71 | 3.96 | 17.74 | 10.02 |
| win rate (context only, never a target) | 31.7% | 50.0% | 29.3% | 28.6% |
| exits SL / TP / END | 68 / 32 / 1 | 20 / 20 / 0 | 41 / 17 / 0 | 10 / 4 / 0 |
| avg hold (h) | 164.0 | 120.5 | 111.7 | 44.9 |

On TEST the filter removed 185 signals that had passed every mandatory rule; TEST expectancy -0.204R (base) -> -0.246R (with the filter).

**Label: UNTESTED.** Too few trades to judge: test n=14 (need 30).

Drawdown check (TEST max drawdown <= 20%): passed. Test max drawdown 7.31% (10.02R) is within the 20.00% limit.

LightGBM, other gradient boosting and neural networks are NOT justified at this sample size (407 TRAIN candidates, 6 features): a flexible model would memorise noise, so only a fixed-penalty logistic regression with a break-even threshold is allowed.

## 6. Verdict

Candidates judged (pre-registered, one TEST look each): baseline `base`, discovery-selected `rr2_vol2_rsi50-70`, ML layer `base+ml`. Other grid variants are excluded because choosing among them by TEST numbers would be selection on TEST. The verdict requires the ROBUST label (positive TRAIN and TEST expectancy with the TEST 90% CI above zero) and reports the drawdown check.

No robust result found.

## 7. Why signals were rejected (decision counts per rule)

Signal candles denied per rule (the FIRST failing rule is counted, so the R1-R4 rows include every candle that was simply not a signal). Exits are never gated.

| rule | base TRAIN | base TEST | selected rr2_vol2_rsi50-70 TRAIN | selected rr2_vol2_rsi50-70 TEST | base+ml TRAIN | base+ml TEST | meaning |
|---|---:|---:|---:|---:|---:|---:|---|
| `R1_trend` | 18810 | 7617 | 18810 | 7617 | 18810 | 7617 | Trend gate: close > EMA9 and close > EMA21 and EMA9 > EMA21 (4H) |
| `R2_momentum` | 1187 | 758 | 1187 | 758 | 1187 | 758 | Momentum: RSI(14) within [50, 70] on the entry candle |
| `R3_volume` | 6920 | 3134 | 7172 | 3252 | 6920 | 3134 | Volume: entry-candle volume >= 1.5x the previous-20-candle average |
| `R4_regime` | 254 | 97 | 156 | 56 | 254 | 97 | Regime: close > EMA200 (unless explicitly testing it off) |
| `R5_news_blackout` | 13 | 8 | 10 | 6 | 13 | 8 | No entries within +/-2h of high-impact news; BNB +/-24h burns/launchpool |
| `R6_correlation_cap` | 308 | 152 | 163 | 82 | 96 | 11 | BTC/ETH/BNB share one risk budget; BNB never stacks |
| `R7_position_risk` | 0 | 0 | 0 | 0 | 0 | 0 | Risk <= 1% (BTC/ETH) / 0.5% (BNB); size derived from stop distance |
| `R8_structure_stop` | 0 | 2 | 0 | 0 | 1 | 2 | Stop behind a confirmed swing low with a buffer (BNB 0.5-0.8%) |
| `R9_circuit_breaker` | 1 | 0 | 0 | 1 | 0 | 0 | 3 consecutive SLs bench a pair 24h; 7d realized loss limit halts all |
| `L_ml_filter` | 0 | 0 | 0 | 0 | 273 | 185 | Layer: logistic-regression filter (only removes trades, never adds) |
| `L_expectancy_guard` | 0 | 0 | 0 | 0 | 0 | 0 | Layer: halve a pair's risk while its last-N-trade expectancy < 0 |
| `X_capital` | 0 | 0 | 0 | 0 | 0 | 0 | Execution: not enough free capital / size below minimum |
| candles evaluated | 27594 | 11826 | 27594 | 11826 | 27594 | 11826 | |
| trades taken | 101 | 58 | 96 | 54 | 40 | 14 | |

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

**baseline `base`** (TEST final equity 8,902.57):

No active adaptations.

**discovery-selected `rr2_vol2_rsi50-70`** (TEST final equity 8,793.83):

No active adaptations.

**ML layer `base+ml`** (TEST final equity 9,951.72):

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
- `rr2_vol2_rsi50-70`: [review_selected_rr2_vol2_rsi50-70/trades_review.md](review_selected_rr2_vol2_rsi50-70/trades_review.md) and [review_selected_rr2_vol2_rsi50-70/trades_review.csv](review_selected_rr2_vol2_rsi50-70/trades_review.csv)

## 11. Adoption path and current stage

The only path to real money (enforced by `adoption.py`; no step can be skipped and the promoted config must match the tested config's sha256 fingerprint):

backtest -> walk-forward (ROBUST + drawdown check) -> human review of EVERY trade -> >= 2 weeks on Binance testnet with zero rule violations -> live.

Stages: BACKTEST -> WALK_FORWARD -> HUMAN_REVIEW -> TESTNET -> LIVE. Stage reached by this run: **WALK_FORWARD** (recorded). Next: **HUMAN_REVIEW**.

**This run used SYNTHETIC data: its records only demonstrate the mechanism. A synthetic result can never justify adopting a variant; the path must be run on real candles from the backtest stage.**

- `base`: record [adoption_base.json](adoption_base.json); promotion to HUMAN_REVIEW is BLOCKED: [ADOPT_walk_forward] walk_forward.label is 'NO-EDGE' (the train window showed no positive expectancy to validate), and only ROBUST may proceed. [ADOPT_walk_forward] walk_forward.test_avg_r must be a positive out-of-sample expectancy in R (got -0.20388112652089616).
- `rr2_vol2_rsi50-70`: record [adoption_rr2_vol2_rsi50-70.json](adoption_rr2_vol2_rsi50-70.json); promotion to HUMAN_REVIEW is BLOCKED: [ADOPT_walk_forward] walk_forward.label is 'NO-EDGE' (the train window showed no positive expectancy to validate), and only ROBUST may proceed. [ADOPT_walk_forward] walk_forward.test_avg_r must be a positive out-of-sample expectancy in R (got -0.2579803176894093).

The ML layer `base+ml` has no adoption record: its fitted coefficients are not part of `StrategyConfig`, so the config fingerprint cannot pin them. The regime-OFF variant is test-only and can never be adopted.

Check a record with `python -m research.trendbot.adoption check --record research/results/synthetic_null_s1/adoption_base.json --stage HUMAN_REVIEW`.

## 12. Risk disclaimer

This is a research and testing tool, not financial advice and not a recommendation to trade. Backtests and synthetic worlds are simplified models; past or simulated results do not predict future results. Crypto trading can result in the total loss of the capital used.
