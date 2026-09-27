# Walk-forward research report: synthetic world `zero_edge` seed 1

## 1. No win rate is promised or targeted

This report does NOT promise, target or optimise a win rate, and nothing in this package selects on one. A win rate is meaningless without the reward:risk it was earned at: with the mandatory minimum of 2:1 a strategy breaks even at about 33% winners before costs, while a 90% win rate with a 1:9 payoff still loses money. A backtest win rate near 90% is evidence of curve-fitting or look-ahead, not of skill. The target metric is POSITIVE EXPECTANCY (average R per trade, net of fees and slippage) that survives a 70/30 chronological walk-forward with controlled drawdown, with every trade planned at reward:risk >= 2:1. Win rate appears below only as context and is labelled 'context only' wherever it is shown.

## 2. Data provenance

- Source: **SYNTHETIC** world `zero_edge`, seed 1, 6 years of 4H candles (13140 per pair) from 2019-01-01T00:00:00Z, pairs BTC/USDT, ETH/USDT, BNB/USDT (`synthetic.generate_world`); adoption provenance `synthetic:zero_edge:1`.
- Ground truth: the planted mechanism at 0.15 sigma per candle: a real, positive PRE-cost edge that fees and slippage eat, so the base strategy's expected NET R is ~0 in both windows (the H0 boundary).
- What the ground truth predicts: nothing should be ROBUST: an edge of ~0R after costs is not worth trading, so every ROBUST label here is a false positive at the boundary of the null hypothesis.
- Qualifying triggers planted per pair (before / after the 70% split): BTC/USDT 344 / 143, ETH/USDT 336 / 129, BNB/USDT 317 / 114.
- **Synthetic results are verification of the methodology, not evidence about real markets.** No real BTC/ETH/BNB data was used in this run.
- News calendar loaded: yes (synthetic, 273 events: high macro 147, high regulatory 24, high unlock 6, medium bnb_burn 24, medium launchpool 72). The events have no price impact; they exercise R5 only.
- Walk-forward split: 2023-03-14T00:00:00Z = 70% of the common time range of all pairs (TRAIN = signal candles before it, TEST = at or after it, up to 2024-12-30T00:00:00Z). TEST starts with fresh equity and fresh circuit breakers; indicators warm up on earlier candles only.
- Statistics (CONTRACT.md v3 C4/C5): 4000 bootstrap resamples (seed 7) for the iid and the calendar-month block bootstrap; m = 4 pre-registered candidates, alpha 0.05. ROBUST requires at least 30 trades in each window, TRAIN and TEST avg R > 0, and a one-sided 98.75% lower bound of the TEST mean above zero for BOTH an iid and a calendar-month block bootstrap (the more conservative is used): alpha 0.05 is split over the m = 4 pre-registered candidates (base, discovery-selected, base+ml, base+guard), so when none has an edge the chance that ANY is called ROBUST is at most about 5%; a positive TEST mean that fails only the bound is UNTESTED (positive but not distinguishable from zero after multiplicity correction); the rule was fixed in advance and is never tuned on TEST outcomes.

| pair | candles | TRAIN candles (before the split) | TEST candles (from the split) | first candle (UTC) | last candle (UTC) | gaps |
|---|---:|---:|---:|---|---|---|
| BTC/USDT | 13140 | 9198 | 3942 | 2019-01-01T00:00:00Z | 2024-12-29T20:00:00Z | 0 |
| ETH/USDT | 13140 | 9198 | 3942 | 2019-01-01T00:00:00Z | 2024-12-29T20:00:00Z | 0 |
| BNB/USDT | 13140 | 9198 | 3942 | 2019-01-01T00:00:00Z | 2024-12-29T20:00:00Z | 0 |

- **Costs used in every backtest:** fee 0.100% of notional per side (the default: Binance spot taker, no discounts) (`--fee-rate 0.001`), charged on the entry AND on the exit; slippage 0.05% (`--slippage-pct 0.05`) against the trade on market fills (entries and stop exits; take-profit limit exits get none); exchange `binance` (`--exchange-id`; `EXCHANGE:binance` news events block every pair). Starting capital 10,000 per window.
- Cost-aware sizing and targets (CONTRACT.md v2 A1): the planned risk is the ALL-IN loss at the stop (the stop fill after slippage plus both fees), so a clean stop is exactly -1R and the target is placed so that a take-profit nets exactly +2R after fees; the target's price distance is therefore more than 2x the stop distance. Only a gap through the stop loses more than 1R.
- Coinbase Advanced Trade taker fees at low volume tiers are several times Binance's, so a Coinbase run must pass the account's real tier with `--fee-rate` (a fraction of notional per side: 0.006 = 0.6%) and `--exchange-id coinbase`; the default costs would understate them.
- Measured on the baseline's TRAIN and TEST trades: fees alone cost 0.06R per trade on average (median stop distance 3.79% of the entry price), so the expectancy below is only as good as the fee rate above.

## 3. Baseline: TRAIN vs TEST

Default mandate config `rr2_vol1.5_rsi50-70` (variant `base`). TRAIN window start .. 2023-03-14T00:00:00Z; TEST window 2023-03-14T00:00:00Z .. 2024-12-30T00:00:00Z.

| metric | TRAIN | TEST |
|---|---:|---:|
| trades (n) | 94 | 60 |
| avg R (expectancy, the target metric) | -0.004 | -0.050 |
| iid bootstrap 90% CI of avg R | [-0.234, +0.245] | [-0.350, +0.250] |
| calendar-month block bootstrap 90% CI of avg R | [-0.162, +0.157] (44 months) | [-0.290, +0.212] (20 months) |
| one-sided lower bound of avg R at 1 - alpha/m, iid / block | -0.311 / -0.227 (98.75%) | -0.450 / -0.364 (98.75%) |
| adjusted lower bound used by the label (the smaller) | -0.311 | -0.450 |
| t-stat of avg R | -0.03 | -0.28 |
| total R | -0.40 | -3.00 |
| profit factor | 1.01 | 0.86 |
| max drawdown % realised (closed trades) | 5.50% | 10.27% |
| max drawdown % mark-to-market (4H closes) | 6.44% | 10.79% |
| max drawdown R (closed trades) | 7.00 | 11.00 |
| win rate (context only, never a target) | 33.0% | 31.7% |
| exits SL / TP / END | 62 / 31 / 1 | 41 / 19 / 0 |
| avg hold (h) | 171.0 | 112.0 |

**Label: NO-EDGE.** Train expectancy is -0.004R over 94 trades, so there is no edge to validate.

Drawdown check (CONTRACT v3 C5; dd_ok = TEST mark-to-market max drawdown <= min(20%, 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length)): **passed**. Test mark-to-market max drawdown 10.79% (realised closed-trade 10.27% (11.00R)) is within the limit 16.63% = min(20%, 95th percentile 16.63% of max drawdown over TRAIN trade sequences bootstrapped at the TEST length of 60). For comparison, TRAIN max drawdown: realised 5.50%, mark-to-market 6.44%.

## 4. Strategy discovery: TRAIN vs TEST

K = 13 variants tried: 12 legal tightenings (reward:risk {2, 2.5, 3} x volume multiple {1.5, 2} x RSI window {50-70, 55-70}) plus 1 explicit test-only variant with the R4 regime filter OFF, which is reported but can never be selected or adopted.

Selection rule: highest TRAIN t-stat among selectable variants with >= 30 TRAIN trades. rr2_vol2_rsi55-70 has the highest TRAIN t-stat (+0.29, avg +0.048R over 75 trades) among 12 eligible variants; chosen on TRAIN only, before any TEST backtest was run.

Order of operations actually run: TRAIN backtests: 13 variants, candles before the split only -> selection recorded: rr2_vol2_rsi55-70 -> TEST backtests: 13 variants, reported side by side only.

Only the selected variant is a pre-registered candidate (one of the m TEST looks of section 6). Every other row, including the regime-OFF test variant, is shown as `context: <label>, not judged`: picking one of them because of its TEST numbers would be selection on TEST.

| variant | status | TRAIN n | TRAIN avg R | TRAIN 90% CI | TRAIN t | TEST n | TEST avg R | TEST 90% CI | TEST adjusted LB | TEST max DD % realised / MTM | label |
|---|---|---:|---:|---|---:|---:|---:|---|---:|---|---|
| `rr2_vol1.5_rsi50-70` | selectable | 94 | -0.004 | [-0.234, +0.245] | -0.03 | 60 | -0.050 | [-0.350, +0.250] | -0.450 | 10.27% / 10.79% | context: NO-EDGE, not judged |
| `rr2_vol1.5_rsi55-70` | selectable | 87 | +0.041 | [-0.207, +0.296] | +0.27 | 56 | -0.089 | [-0.357, +0.232] | -0.464 | 11.14% / 11.66% | context: TRAIN-ONLY, not judged |
| `rr2_vol2_rsi50-70` | selectable | 80 | +0.020 | [-0.243, +0.282] | +0.13 | 51 | +0.000 | [-0.294, +0.353] | -0.412 | 7.80% / 8.07% | context: TRAIN-ONLY, not judged |
| `rr2_vol2_rsi55-70` | **SELECTED** | 75 | +0.048 | [-0.224, +0.320] | +0.29 | 49 | -0.020 | [-0.327, +0.289] | -0.449 | 6.81% / 7.05% | TRAIN-ONLY |
| `rr2.5_vol1.5_rsi50-70` | selectable | 89 | -0.135 | [-0.371, +0.140] | -0.84 | 49 | -0.143 | [-0.500, +0.214] | -0.571 | 12.05% / 12.05% | context: NO-EDGE, not judged |
| `rr2.5_vol1.5_rsi55-70` | selectable | 82 | -0.104 | [-0.360, +0.195] | -0.61 | 47 | -0.106 | [-0.479, +0.266] | -0.553 | 10.72% / 10.72% | context: NO-EDGE, not judged |
| `rr2.5_vol2_rsi50-70` | selectable | 76 | -0.171 | [-0.447, +0.105] | -1.00 | 43 | -0.105 | [-0.512, +0.302] | -0.593 | 10.81% / 10.93% | context: NO-EDGE, not judged |
| `rr2.5_vol2_rsi55-70` | selectable | 72 | -0.174 | [-0.465, +0.118] | -0.98 | 43 | -0.105 | [-0.512, +0.302] | -0.593 | 10.74% / 10.93% | context: NO-EDGE, not judged |
| `rr3_vol1.5_rsi50-70` | selectable | 67 | -0.123 | [-0.421, +0.217] | -0.61 | 48 | -0.167 | [-0.500, +0.250] | -0.667 | 14.22% / 14.55% | context: NO-EDGE, not judged |
| `rr3_vol1.5_rsi55-70` | selectable | 62 | -0.052 | [-0.414, +0.315] | -0.24 | 45 | -0.111 | [-0.467, +0.333] | -0.644 | 12.04% / 12.38% | context: NO-EDGE, not judged |
| `rr3_vol2_rsi50-70` | selectable | 64 | -0.144 | [-0.457, +0.187] | -0.70 | 43 | -0.070 | [-0.442, +0.395] | -0.628 | 12.36% / 12.70% | context: NO-EDGE, not judged |
| `rr3_vol2_rsi55-70` | selectable | 60 | -0.087 | [-0.420, +0.267] | -0.40 | 43 | -0.070 | [-0.442, +0.395] | -0.628 | 11.48% / 11.82% | context: NO-EDGE, not judged |
| `rr2_vol1.5_rsi50-70_regimeOFF` | TEST-ONLY (regime OFF, never selectable) | 141 | -0.058 | [-0.249, +0.134] | -0.49 | 71 | -0.028 | [-0.282, +0.268] | -0.408 | 9.89% / 10.41% | context: NO-EDGE, not judged |

**Re-selecting a variant on these TEST numbers would turn TEST into TRAIN: the selection was fixed on TRAIN before any TEST backtest ran, and it is not revisited.**

The TRAIN column of the selected variant `rr2_vol2_rsi55-70` is biased upward by the selection itself (it is the best of 12); only its TEST column is an out-of-sample test.

**Label: TRAIN-ONLY.** Train expectancy +0.048R (n=75) did not hold out of sample: test expectancy is -0.020R (n=49), which suggests curve-fitting.

Drawdown check (CONTRACT v3 C5; dd_ok = TEST mark-to-market max drawdown <= min(20%, 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length)): **passed**. Test mark-to-market max drawdown 7.05% (realised closed-trade 6.81% (7.00R)) is within the limit 15.76% = min(20%, 95th percentile 15.76% of max drawdown over TRAIN trade sequences bootstrapped at the TEST length of 49). For comparison, TRAIN max drawdown: realised 5.92%, mark-to-market 6.48%.

## 5. Entry layers (ML filter, expectancy guard): TRAIN vs TEST

Two pre-registered layers on the baseline rules. `base+ml` adds `L_ml_filter`, a logistic regression on 6 features (rsi, vol_ratio, ema_gap_pct, dist_regime_pct, hour_sin, hour_cos), fitted ONLY on purged TRAIN candidates and applied unchanged to TEST, so its TRAIN numbers are IN-SAMPLE. `base+guard` switches on `L_expectancy_guard` (`expectancy_guard=True`: a pair's risk is multiplied by 0.5 while its last 20 closed trades average below 0R); it is a fixed rule over the journal, nothing is fitted, and it needs the same walk-forward evidence as any other variant.

An entry layer can only VETO an entry that passed every mandatory rule; it never approves an entry a rule denies. A veto can free R6 budget or change R9 state, admitting other rule-compliant trades, so a layer's journal is not a subset of the base journal: both directions are counted below, matched by (pair, signal time).

| layer | window (TRAIN / TEST) | signals vetoed | entries at reduced risk (guard x < 1) | base trades absent from the layer journal | layer trades absent from the base journal | trades in both | base trades | layer trades |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `base+ml` | TRAIN | 342 | 0 | 90 | 5 | 4 | 94 | 9 |
| `base+ml` | TEST | 191 | 0 | 58 | 4 | 2 | 60 | 6 |
| `base+guard` | TRAIN | 0 | 21 | 1 | 9 | 93 | 94 | 102 |
| `base+guard` | TEST | 0 | 3 | 0 | 0 | 60 | 60 | 60 |

- `base+ml` vs `base`: TRAIN avg R -0.004 -> +0.333 (n 94 -> 9, in-sample) | TEST avg R -0.050 -> +0.500 (n 60 -> 6).
- `base+guard` vs `base`: TRAIN avg R -0.004 -> -0.044 (n 94 -> 102) | TEST avg R -0.050 -> -0.050 (n 60 -> 60).

Logistic filter (l2=1) fitted on 371 TRAIN candidates (TRAIN base win rate 24.0%, context only) vetoes every rule-passing signal whose predicted win probability is at or below the break-even 0.333.

- TRAIN candidates enumerated (purged: outcome resolved before the split): 371; used for the fit: 371 (0 skipped for missing features); TRAIN candidate win share 24.0% (context only).
- Threshold p* = break-even win probability from the TRAIN avg win +2.00R and avg loss -1.00R: p* = 0.333 (no threshold search).
- L2 penalty 1 (fixed, not tuned); intercept -1.172.
- Model fingerprint (sha256 of the canonical JSON of features, TRAIN means/stds, intercept, coefficients, threshold and l2): `80c90c198bdf1de2a14c0f73a2f55df74c1ecf18088fa0f3c80aefbc97c70fe5`. It is pinned in the ML adoption record (section 11); `MLFilter.from_json` re-verifies it when the live bot loads the model file.

| feature | standardised coefficient, fitted on TRAIN only and applied unchanged to TEST (log-odds per TRAIN s.d.) |
|---|---:|
| `rsi` | -0.079 |
| `vol_ratio` | +0.122 |
| `ema_gap_pct` | +0.024 |
| `dist_regime_pct` | -0.211 |
| `hour_sin` | -0.034 |
| `hour_cos` | +0.001 |

| metric | base TRAIN | base+ml TRAIN (in-sample) | base+guard TRAIN | base TEST | base+ml TEST | base+guard TEST |
|---|---:|---:|---:|---:|---:|---:|
| trades (n) | 94 | 9 | 102 | 60 | 6 | 60 |
| avg R (expectancy, the target metric) | -0.004 | +0.333 | -0.044 | -0.050 | +0.500 | -0.050 |
| iid bootstrap 90% CI of avg R | [-0.234, +0.245] | [-0.333, +1.000] | [-0.268, +0.185] | [-0.350, +0.250] | [-0.500, +1.500] | [-0.350, +0.250] |
| calendar-month block bootstrap 90% CI of avg R | [-0.162, +0.157] (44 months) | [-0.625, +1.182] (8 months) | [-0.194, +0.110] (44 months) | [-0.290, +0.212] (20 months) | [-0.500, +1.500] (6 months) | [-0.290, +0.212] (20 months) |
| one-sided lower bound of avg R at 1 - alpha/m, iid / block | -0.311 / -0.227 (98.75%) | -0.667 / -1.000 (98.75%) | -0.345 / -0.251 (98.75%) | -0.450 / -0.364 (98.75%) | -1.000 / -1.000 (98.75%) | -0.450 / -0.364 (98.75%) |
| adjusted lower bound used by the label (the smaller) | -0.311 | -1.000 | -0.345 | -0.450 | -1.000 | -0.450 |
| t-stat of avg R | -0.03 | +0.63 | -0.32 | -0.28 | +0.75 | -0.28 |
| total R | -0.40 | +3.00 | -4.53 | -3.00 | +3.00 | -3.00 |
| profit factor | 1.01 | 1.49 | 0.91 | 0.86 | 2.37 | 0.86 |
| max drawdown % realised (closed trades) | 5.50% | 2.98% | 7.87% | 10.27% | 1.50% | 10.22% |
| max drawdown % mark-to-market (4H closes) | 6.44% | 3.19% | 8.69% | 10.79% | 1.62% | 10.79% |
| max drawdown R (closed trades) | 7.00 | 3.00 | 10.00 | 11.00 | 2.00 | 11.00 |
| win rate (context only, never a target) | 33.0% | 44.4% | 31.4% | 31.7% | 50.0% | 31.7% |
| exits SL / TP / END | 62 / 31 / 1 | 5 / 4 / 0 | 68 / 32 / 2 | 41 / 19 / 0 | 3 / 3 / 0 | 41 / 19 / 0 |
| avg hold (h) | 171.0 | 97.8 | 163.2 | 112.0 | 63.3 | 112.0 |

`base+ml`:

**Label: UNTESTED.** Too few trades to judge: train n=9 (need 30) and test n=6 (need 30).

Drawdown check (CONTRACT v3 C5; dd_ok = TEST mark-to-market max drawdown <= min(20%, 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length)): **passed**. Test mark-to-market max drawdown 1.62% (realised closed-trade 1.50% (2.00R)) is within the limit 3.94% = min(20%, 95th percentile 3.94% of max drawdown over TRAIN trade sequences bootstrapped at the TEST length of 6). For comparison, TRAIN max drawdown: realised 2.98%, mark-to-market 3.19%.

`base+guard`:

**Label: NO-EDGE.** Train expectancy is -0.044R over 102 trades, so there is no edge to validate.

Drawdown check (CONTRACT v3 C5; dd_ok = TEST mark-to-market max drawdown <= min(20%, 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length)): **passed**. Test mark-to-market max drawdown 10.79% (realised closed-trade 10.22% (11.00R)) is within the limit 16.84% = min(20%, 95th percentile 16.84% of max drawdown over TRAIN trade sequences bootstrapped at the TEST length of 60). For comparison, TRAIN max drawdown: realised 7.87%, mark-to-market 8.69%.

LightGBM, other gradient boosting and neural networks are NOT justified at this sample size (371 TRAIN candidates, 6 features): a flexible model would memorise noise, so only a fixed-penalty logistic regression with a break-even threshold is allowed.

## 6. Verdict

Candidates judged (pre-registered, one TEST look each, m = 4): baseline `base`, discovery-selected `rr2_vol2_rsi55-70`, ML layer `base+ml`, expectancy guard `base+guard`. Other grid variants are excluded because choosing among them by TEST numbers would be selection on TEST. ROBUST requires at least 30 trades in each window, TRAIN and TEST avg R > 0, and a one-sided 98.75% lower bound of the TEST mean above zero for BOTH an iid and a calendar-month block bootstrap (the more conservative is used): alpha 0.05 is split over the m = 4 pre-registered candidates (base, discovery-selected, base+ml, base+guard), so when none has an edge the chance that ANY is called ROBUST is at most about 5%; a positive TEST mean that fails only the bound is UNTESTED (positive but not distinguishable from zero after multiplicity correction); the rule was fixed in advance and is never tuned on TEST outcomes. The TEST gate is reached when TRAIN avg R > 0 and both windows hold >= 30 trades; the drawdown check is reported beside the label.

| candidate | TRAIN n | TRAIN avg R | TEST n | TEST avg R | TEST iid / block LB (1 - alpha/m) | reached the TEST gate | label | TEST MTM max DD vs limit | dd_ok |
|---|---:|---:|---:|---:|---|---|---|---|---|
| baseline `base` | 94 | -0.004 | 60 | -0.050 | -0.450 / -0.364 | no | NO-EDGE | 10.79% vs 16.63% | yes |
| discovery-selected `rr2_vol2_rsi55-70` | 75 | +0.048 | 49 | -0.020 | -0.449 / -0.385 | yes | TRAIN-ONLY | 7.05% vs 15.76% | yes |
| ML layer `base+ml` | 9 | +0.333 | 6 | +0.500 | -1.000 / -1.000 | no | UNTESTED | 1.62% vs 3.94% | yes |
| expectancy guard `base+guard` | 102 | -0.044 | 60 | -0.050 | -0.450 / -0.364 | no | NO-EDGE | 10.79% vs 16.84% | yes |

No robust result found. Pre-registered candidates, TRAIN | TEST: baseline `base` NO-EDGE (TRAIN -0.004R n=94 | TEST -0.050R n=60); discovery-selected `rr2_vol2_rsi55-70` TRAIN-ONLY (TRAIN +0.048R n=75 | TEST -0.020R n=49); ML layer `base+ml` UNTESTED (TRAIN +0.333R n=9 | TEST +0.500R n=6); expectancy guard `base+guard` NO-EDGE (TRAIN -0.044R n=102 | TEST -0.050R n=60).

## 7. Why signals were rejected (decision counts per rule)

Signal candles denied per rule (the FIRST failing rule is counted, so the R1-R4 rows include every candle that was simply not a signal). Exits are never gated.

| rule | base TRAIN | base TEST | rr2_vol2_rsi55-70 TRAIN | rr2_vol2_rsi55-70 TEST | base+ml TRAIN | base+ml TEST | base+guard TRAIN | base+guard TEST | meaning |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `R1_trend` | 18699 | 7667 | 18699 | 7667 | 18699 | 7667 | 18699 | 7667 | Trend gate: close > EMA9 and close > EMA21 and EMA9 > EMA21 (4H) |
| `R2_momentum` | 1369 | 789 | 2635 | 1311 | 1369 | 789 | 1369 | 789 | Momentum: RSI(14) within [50, 70] on the entry candle |
| `R3_volume` | 6884 | 3070 | 5918 | 2687 | 6884 | 3070 | 6884 | 3070 | Volume: entry-candle volume >= 1.5x the previous-20-candle average |
| `R4_regime` | 252 | 87 | 120 | 35 | 252 | 87 | 252 | 87 | Regime: close > EMA200 (unless explicitly testing it off) |
| `R5_news_blackout` | 14 | 7 | 10 | 5 | 14 | 7 | 14 | 7 | No entries within +/-2h of high-impact news; BNB +/-24h burns/launchpool |
| `R6_correlation_cap` | 282 | 145 | 137 | 72 | 24 | 8 | 274 | 145 | BTC/ETH/BNB share one risk budget; BNB never stacks |
| `R7_position_risk` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Risk <= 1% (BTC/ETH) / 0.5% (BNB); size derived from stop distance |
| `R8_structure_stop` | 0 | 1 | 0 | 0 | 1 | 1 | 0 | 1 | Stop behind a confirmed swing low with a buffer (BNB 0.5-0.8%) |
| `R9_circuit_breaker` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 3 consecutive SLs bench a pair 24h; 7d realized loss limit halts all |
| `L_ml_filter` | 0 | 0 | 0 | 0 | 342 | 191 | 0 | 0 | Layer: logistic-regression veto; it can only veto an entry that passed every mandatory rule and never approves one a rule denies (a veto can free R6 budget or change R9 state, which may admit other rule-compliant trades) |
| `L_expectancy_guard` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Layer: halve a pair's risk while its last-N-trade expectancy < 0 |
| `X_capital` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Execution: not enough free capital / size below minimum |
| candles evaluated | 27594 | 11826 | 27594 | 11826 | 27594 | 11826 | 27594 | 11826 | |
| trades taken | 94 | 60 | 75 | 49 | 9 | 6 | 102 | 60 | |

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
| `base+guard` | 0 | 0 |

Result: CLEAN. 0 violations in 32 backtests.

## 9. Journal rules at the end of TRAIN and at the end of TEST

`journal_rules.audit` over each pre-registered candidate's TRAIN journal at the split and its TEST journal at 2024-12-30T00:00:00Z (the end of TEST), each with that window's final equity: every adaptation the live bot would be applying at that moment because of past trades, with the journal rows that caused it. Backtest journals record the OPEN of the exit candle, so each exit counts from exit_ts + 4h, when the exit is certain (`exit_time_uncertainty_ms = cfg.timeframe_ms`, CONTRACT.md v2 A2); a bench therefore lasts at least 24h of real time. A live journal records real fill times and uses 0.

**baseline `base`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 10,045.22) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 9,518.34) | - | - | none | No active adaptations. | - |

**discovery-selected `rr2_vol2_rsi55-70`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 10,149.71) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 10,011.88) | - | - | none | No active adaptations. | - |

**ML layer `base+ml`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 10,195.18) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 10,348.90) | - | - | none | No active adaptations. | - |

**expectancy guard `base+guard`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 9,588.11) | L_expectancy_guard | BNB/USDT | risk x0.5 | BNB/USDT's last 20 closed trades average -0.250R (below 0), so new BNB/USDT entries risk 0.5x the normal amount until that average is back at or above 0. | #38, #41, #43, #46, #48, #49, #50, #51, #54, #56, #62, #63, #69, #70, #72, #84, #87, #91, #96, #99 |
| TRAIN at 2023-03-14T00:00:00Z (equity 9,588.11) | L_expectancy_guard | BTC/USDT | risk x0.5 | BTC/USDT's last 20 closed trades average -0.070R (below 0), so new BTC/USDT entries risk 0.5x the normal amount until that average is back at or above 0. | #60, #61, #66, #71, #74, #77, #79, #80, #81, #82, #85, #86, #88, #89, #93, #94, #95, #98, #100, #102 |
| TRAIN at 2023-03-14T00:00:00Z (equity 9,588.11) | L_expectancy_guard | ETH/USDT | risk x0.5 | ETH/USDT's last 20 closed trades average -0.206R (below 0), so new ETH/USDT entries risk 0.5x the normal amount until that average is back at or above 0. | #32, #34, #40, #45, #53, #55, #57, #64, #65, #67, #68, #73, #75, #76, #78, #83, #90, #92, #97, #101 |
| TEST at 2024-12-30T00:00:00Z (equity 9,520.47) | L_expectancy_guard | ETH/USDT | risk x0.5 | ETH/USDT's last 20 closed trades average -0.100R (below 0), so new ETH/USDT entries risk 0.5x the normal amount until that average is back at or above 0. | #116, #117, #118, #127, #129, #132, #135, #140, #141, #142, #144, #145, #147, #148, #149, #151, #152, #155, #156, #162 |

Reproduce the baseline TEST row with `python -m research.trendbot.journal_rules --journal research/results/synthetic_zero_edge_s1/journals/base_test.csv --equity 9518.34 --now 2024-12-30T00:00:00Z --backtest-journal`.

## 10. Journals and human review packs

Trade journals (`journal.write_journal`, one CSV per window) for the 4 pre-registered candidates that ran, the variants whose TRAIN and TEST columns this report shows in full. The other discovery variants appear only as one context row each in section 4, so no journal is kept for them (the same command regenerates them). TEST trade ids are offset past the TRAIN ids, so ids are unique across a variant's two journals and its review pack.

| variant | TRAIN journal | TEST journal |
|---|---|---|
| `base` | [journals/base_train.csv](journals/base_train.csv) | [journals/base_test.csv](journals/base_test.csv) |
| `rr2_vol2_rsi55-70` | [journals/rr2_vol2_rsi55-70_train.csv](journals/rr2_vol2_rsi55-70_train.csv) | [journals/rr2_vol2_rsi55-70_test.csv](journals/rr2_vol2_rsi55-70_test.csv) |
| `base+ml` | [journals/base_plus_ml_train.csv](journals/base_plus_ml_train.csv) | [journals/base_plus_ml_test.csv](journals/base_plus_ml_test.csv) |
| `base+guard` | [journals/base_plus_guard_train.csv](journals/base_plus_guard_train.csv) | [journals/base_plus_guard_test.csv](journals/base_plus_guard_test.csv) |

Human review packs (`review_sheet.write_review_pack`, TRAIN and TEST):

- `base`: [review_base/trades_review.md](review_base/trades_review.md) and [review_base/trades_review.csv](review_base/trades_review.csv)
- `rr2_vol2_rsi55-70`: [review_selected_rr2_vol2_rsi55-70/trades_review.md](review_selected_rr2_vol2_rsi55-70/trades_review.md) and [review_selected_rr2_vol2_rsi55-70/trades_review.csv](review_selected_rr2_vol2_rsi55-70/trades_review.csv)
- `base+ml`: [review_base_plus_ml/trades_review.md](review_base_plus_ml/trades_review.md) and [review_base_plus_ml/trades_review.csv](review_base_plus_ml/trades_review.csv)
- `base+guard`: [review_base_plus_guard/trades_review.md](review_base_plus_guard/trades_review.md) and [review_base_plus_guard/trades_review.csv](review_base_plus_guard/trades_review.csv)

## 11. Adoption path and current stage

The only path to real money (enforced by `adoption.py`; no step can be skipped, the promoted config must match the tested config's sha256 fingerprint, and an ML layer's model must match the tested model's fingerprint):

backtest -> walk-forward (ROBUST + drawdown check) -> human review of EVERY trade -> >= 2 weeks on Binance testnet with zero rule violations -> live.

Stages: BACKTEST -> WALK_FORWARD -> HUMAN_REVIEW -> TESTNET -> LIVE. Stage reached by this run: **WALK_FORWARD** (recorded). Next: **HUMAN_REVIEW**.

**This run used SYNTHETIC data: its records only demonstrate the mechanism. A synthetic result can never justify adopting a variant: `adoption check --stage HUMAN_REVIEW` is BLOCKED by `ADOPT_provenance` for every record below, whatever its label.**

Every record is bound to its evidence (CONTRACT.md v3 C3): `provenance`, `data_files` (sha256 per candle file) and `events_file`; `backtest.report_sha256` (this REPORT.md, the record being rewritten after the report so the hash matches); both walk-forward journals with their sha256, `min_train` / `min_test`, `train_avg_r` and `label_params` (m, alpha, n_boot, seed, and the C5 drawdown inputs `max_dd_pct`, `train_dd_p95_pct` and `mtm_max_dd_pct`, which need the candles and are recorded as measured), from which `adoption check` recomputes the label, n, avg R and dd_ok; and `human_review.review_path` / `review_sha256` (the blank review pack; after filling in `reviewer_ok`, re-hash it with `python -m research.trendbot.adoption hash`). The check status below was computed on the records as written.

| variant | walk-forward label (TRAIN + TEST) | TRAIN n | TRAIN avg R | TEST n | TEST avg R | dd_ok | provenance | config sha256 (first 16) | ML model sha256 (first 16) | `check --stage WALK_FORWARD` | `check --stage HUMAN_REVIEW` |
|---|---|---:|---:|---:|---:|---|---|---|---|---|---|
| `base` | NO-EDGE | 94 | -0.004 | 60 | -0.050 | yes | `synthetic:zero_edge:1` | `9be9d6da6ad86d85` | `none (no ML layer)` | PASS | BLOCKED by ADOPT_provenance, ADOPT_walk_forward |
| `rr2_vol2_rsi55-70` | TRAIN-ONLY | 75 | +0.048 | 49 | -0.020 | yes | `synthetic:zero_edge:1` | `e6bf999c94f1d730` | `none (no ML layer)` | PASS | BLOCKED by ADOPT_provenance, ADOPT_walk_forward |
| `base+ml` | UNTESTED | 9 | +0.333 | 6 | +0.500 | yes | `synthetic:zero_edge:1` | `9be9d6da6ad86d85` | `80c90c198bdf1de2` | PASS | BLOCKED by ADOPT_provenance, ADOPT_walk_forward |
| `base+guard` | NO-EDGE | 102 | -0.044 | 60 | -0.050 | yes | `synthetic:zero_edge:1` | `2c5274264328eeee` | `none (no ML layer)` | PASS | BLOCKED by ADOPT_provenance, ADOPT_walk_forward |

- `base`: record [adoption_base.json](adoption_base.json).
  - `--stage WALK_FORWARD`: PASS
    Check: `python -m research.trendbot.adoption check --record research/results/synthetic_zero_edge_s1/adoption_base.json --stage WALK_FORWARD`
  - `--stage HUMAN_REVIEW`: BLOCKED: [ADOPT_provenance] provenance is 'synthetic:zero_edge:1': a synthetic world only verifies the harness and is never evidence about real markets, so it may not go past WALK_FORWARD. [ADOPT_provenance] data_files is missing or empty, so the market data behind the backtest is not bound to this record by sha256. [ADOPT_walk_forward] walk_forward.label is 'NO-EDGE' (the train window showed no positive expectancy to validate), and only ROBUST may proceed. [ADOPT_walk_forward] walk_forward.test_avg_r must be a positive out-of-sample expectancy in R (got -0.050000000000000835).
    Check: `python -m research.trendbot.adoption check --record research/results/synthetic_zero_edge_s1/adoption_base.json --stage HUMAN_REVIEW`
- `rr2_vol2_rsi55-70`: record [adoption_rr2_vol2_rsi55-70.json](adoption_rr2_vol2_rsi55-70.json), [config_rr2_vol2_rsi55-70.json](config_rr2_vol2_rsi55-70.json).
  - `--stage WALK_FORWARD`: PASS
    Check: `python -m research.trendbot.adoption check --record research/results/synthetic_zero_edge_s1/adoption_rr2_vol2_rsi55-70.json --stage WALK_FORWARD --config research/results/synthetic_zero_edge_s1/config_rr2_vol2_rsi55-70.json`
  - `--stage HUMAN_REVIEW`: BLOCKED: [ADOPT_provenance] provenance is 'synthetic:zero_edge:1': a synthetic world only verifies the harness and is never evidence about real markets, so it may not go past WALK_FORWARD. [ADOPT_provenance] data_files is missing or empty, so the market data behind the backtest is not bound to this record by sha256. [ADOPT_walk_forward] walk_forward.label is 'TRAIN-ONLY' (the edge did not hold out of sample, a sign of curve-fitting), and only ROBUST may proceed. [ADOPT_walk_forward] walk_forward.test_avg_r must be a positive out-of-sample expectancy in R (got -0.020408163265307006).
    Check: `python -m research.trendbot.adoption check --record research/results/synthetic_zero_edge_s1/adoption_rr2_vol2_rsi55-70.json --stage HUMAN_REVIEW --config research/results/synthetic_zero_edge_s1/config_rr2_vol2_rsi55-70.json`
- `base+ml`: record [adoption_base_plus_ml.json](adoption_base_plus_ml.json), [model_base_plus_ml.json](model_base_plus_ml.json).
  - `--stage WALK_FORWARD`: PASS
    Check: `python -m research.trendbot.adoption check --record research/results/synthetic_zero_edge_s1/adoption_base_plus_ml.json --stage WALK_FORWARD --model-fingerprint 80c90c198bdf1de2a14c0f73a2f55df74c1ecf18088fa0f3c80aefbc97c70fe5`
  - `--stage HUMAN_REVIEW`: BLOCKED: [ADOPT_provenance] provenance is 'synthetic:zero_edge:1': a synthetic world only verifies the harness and is never evidence about real markets, so it may not go past WALK_FORWARD. [ADOPT_provenance] data_files is missing or empty, so the market data behind the backtest is not bound to this record by sha256. [ADOPT_walk_forward] walk_forward.label is 'UNTESTED' (too few trades, or a test expectancy not distinguishable from zero), and only ROBUST may proceed.
    Check: `python -m research.trendbot.adoption check --record research/results/synthetic_zero_edge_s1/adoption_base_plus_ml.json --stage HUMAN_REVIEW --model-fingerprint 80c90c198bdf1de2a14c0f73a2f55df74c1ecf18088fa0f3c80aefbc97c70fe5`
- `base+guard`: record [adoption_base_plus_guard.json](adoption_base_plus_guard.json), [config_base_plus_guard.json](config_base_plus_guard.json).
  - `--stage WALK_FORWARD`: PASS
    Check: `python -m research.trendbot.adoption check --record research/results/synthetic_zero_edge_s1/adoption_base_plus_guard.json --stage WALK_FORWARD --config research/results/synthetic_zero_edge_s1/config_base_plus_guard.json`
  - `--stage HUMAN_REVIEW`: BLOCKED: [ADOPT_provenance] provenance is 'synthetic:zero_edge:1': a synthetic world only verifies the harness and is never evidence about real markets, so it may not go past WALK_FORWARD. [ADOPT_provenance] data_files is missing or empty, so the market data behind the backtest is not bound to this record by sha256. [ADOPT_walk_forward] walk_forward.label is 'NO-EDGE' (the train window showed no positive expectancy to validate), and only ROBUST may proceed. [ADOPT_walk_forward] walk_forward.test_avg_r must be a positive out-of-sample expectancy in R (got -0.05000000000000083).
    Check: `python -m research.trendbot.adoption check --record research/results/synthetic_zero_edge_s1/adoption_base_plus_guard.json --stage HUMAN_REVIEW --config research/results/synthetic_zero_edge_s1/config_base_plus_guard.json`

The ML layer `base+ml` is adopted as a (config, model) pair: its record pins the config fingerprint AND the fitted model's fingerprint `80c90c198bdf1de2a14c0f73a2f55df74c1ecf18088fa0f3c80aefbc97c70fe5` (`MLFilter.fingerprint()`). The model itself is [model_base_plus_ml.json](model_base_plus_ml.json) (`MLFilter.to_json()`), which the live bot loads with `MLFilter.from_json(text, expected_fingerprint=<the record's model_fingerprint>)`: loading raises if the file does not hash to that fingerprint. **Refitting the model (new TRAIN data, a later split, a different l2) changes the fingerprint, so a refitted model is a NEW candidate and restarts the adoption path at BACKTEST.** `adoption check` blocks (`ADOPT_fingerprint`) a model whose fingerprint differs from the recorded one, and any `+ml` record without one.

A `config_<variant>.json` file (the `StrategyConfig` overrides versus the defaults, e.g. costs, discovery parameters or `expectancy_guard`) is written whenever the tested config is not the default one; the check needs it as `--config`. The regime-OFF variant is test-only and can never be adopted.

## 12. Risk disclaimer

This is a research and testing tool, not financial advice and not a recommendation to trade. Backtests and synthetic worlds are simplified models; past or simulated results do not predict future results. Crypto trading can result in the total loss of the capital used.
