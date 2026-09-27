# Walk-forward research report: synthetic world `null` seed 1

## 1. No win rate is promised or targeted

This report does NOT promise, target or optimise a win rate, and nothing in this package selects on one. A win rate is meaningless without the reward:risk it was earned at: with the mandatory minimum of 2:1 a strategy breaks even at about 33% winners before costs, while a 90% win rate with a 1:9 payoff still loses money. A backtest win rate near 90% is evidence of curve-fitting or look-ahead, not of skill. The target metric is POSITIVE EXPECTANCY (average R per trade, net of fees and slippage) that survives a 70/30 chronological walk-forward with controlled drawdown, with every trade planned at reward:risk >= 2:1. Win rate appears below only as context and is labelled 'context only' wherever it is shown.

## 2. Data provenance

- Source: **SYNTHETIC** world `null`, seed 1, 6 years of 4H candles (13140 per pair) from 2019-01-01T00:00:00Z, pairs BTC/USDT, ETH/USDT, BNB/USDT (`synthetic.generate_world`); adoption provenance `synthetic:null:1`.
- Ground truth: martingale prices (zero drift): no entry/exit rule has an edge before costs, so every strategy has negative expectancy after fees and slippage.
- What the ground truth predicts: nothing should be ROBUST; the expected labels are NO-EDGE, TRAIN-ONLY or UNTESTED, and a ROBUST label here is a false positive.
- Qualifying triggers planted per pair (before / after the 70% split): BTC/USDT 0 / 0, ETH/USDT 0 / 0, BNB/USDT 0 / 0.
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
- Measured on the baseline's TRAIN and TEST trades: fees alone cost 0.06R per trade on average (median stop distance 3.78% of the entry price), so the expectancy below is only as good as the fee rate above.

## 3. Baseline: TRAIN vs TEST

Default mandate config `rr2_vol1.5_rsi50-70` (variant `base`). TRAIN window start .. 2023-03-14T00:00:00Z; TEST window 2023-03-14T00:00:00Z .. 2024-12-30T00:00:00Z.

| metric | TRAIN | TEST |
|---|---:|---:|
| trades (n) | 98 | 58 |
| avg R (expectancy, the target metric) | -0.138 | -0.121 |
| iid bootstrap 90% CI of avg R | [-0.357, +0.102] | [-0.379, +0.190] |
| calendar-month block bootstrap 90% CI of avg R | [-0.323, +0.042] (44 months) | [-0.381, +0.154] (20 months) |
| one-sided lower bound of avg R at 1 - alpha/m, iid / block | -0.444 / -0.394 (98.75%) | -0.483 / -0.492 (98.75%) |
| adjusted lower bound used by the label (the smaller) | -0.444 | -0.492 |
| t-stat of avg R | -1.00 | -0.67 |
| total R | -13.48 | -7.00 |
| profit factor | 0.73 | 0.80 |
| max drawdown % realised (closed trades) | 16.86% | 12.84% |
| max drawdown % mark-to-market (4H closes) | 18.54% | 13.76% |
| max drawdown R (closed trades) | 19.00 | 14.00 |
| win rate (context only, never a target) | 28.6% | 29.3% |
| exits SL / TP / END | 69 / 28 / 1 | 41 / 17 / 0 |
| avg hold (h) | 177.8 | 112.6 |

**Label: NO-EDGE.** Train expectancy is -0.138R over 98 trades, so there is no edge to validate.

Drawdown check (CONTRACT v3 C5; dd_ok = TEST mark-to-market max drawdown <= min(20%, 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length)): **passed**. Test mark-to-market max drawdown 13.76% (realised closed-trade 12.84% (14.00R)) is within the limit 20.00% = min(20%, 95th percentile 21.53% of max drawdown over TRAIN trade sequences bootstrapped at the TEST length of 58). For comparison, TRAIN max drawdown: realised 16.86%, mark-to-market 18.54%.

## 4. Strategy discovery: TRAIN vs TEST

K = 13 variants tried: 12 legal tightenings (reward:risk {2, 2.5, 3} x volume multiple {1.5, 2} x RSI window {50-70, 55-70}) plus 1 explicit test-only variant with the R4 regime filter OFF, which is reported but can never be selected or adopted.

Selection rule: highest TRAIN t-stat among selectable variants with >= 30 TRAIN trades. rr2.5_vol2_rsi50-70 has the highest TRAIN t-stat (-0.50, avg -0.086R over 81 trades) among 12 eligible variants; chosen on TRAIN only, before any TEST backtest was run.

Order of operations actually run: TRAIN backtests: 13 variants, candles before the split only -> selection recorded: rr2.5_vol2_rsi50-70 -> TEST backtests: 13 variants, reported side by side only.

Only the selected variant is a pre-registered candidate (one of the m TEST looks of section 6). Every other row, including the regime-OFF test variant, is shown as `context: <label>, not judged`: picking one of them because of its TEST numbers would be selection on TEST.

| variant | status | TRAIN n | TRAIN avg R | TRAIN 90% CI | TRAIN t | TEST n | TEST avg R | TEST 90% CI | TEST adjusted LB | TEST max DD % realised / MTM | label |
|---|---|---:|---:|---|---:|---:|---:|---|---:|---|---|
| `rr2_vol1.5_rsi50-70` | selectable | 98 | -0.138 | [-0.357, +0.102] | -1.00 | 58 | -0.121 | [-0.379, +0.190] | -0.492 | 12.84% / 13.76% | context: NO-EDGE, not judged |
| `rr2_vol1.5_rsi55-70` | selectable | 93 | -0.123 | [-0.349, +0.108] | -0.87 | 59 | -0.085 | [-0.339, +0.220] | -0.492 | 12.86% / 13.79% | context: NO-EDGE, not judged |
| `rr2_vol2_rsi50-70` | selectable | 89 | -0.185 | [-0.415, +0.051] | -1.31 | 53 | -0.151 | [-0.434, +0.132] | -0.547 | 11.62% / 11.65% | context: NO-EDGE, not judged |
| `rr2_vol2_rsi55-70` | selectable | 81 | -0.290 | [-0.519, -0.068] | -2.04 | 54 | -0.111 | [-0.389, +0.222] | -0.500 | 10.66% / 10.77% | context: NO-EDGE, not judged |
| `rr2.5_vol1.5_rsi50-70` | selectable | 93 | -0.091 | [-0.349, +0.172] | -0.57 | 48 | -0.198 | [-0.563, +0.167] | -0.635 | 14.59% / 14.94% | context: NO-EDGE, not judged |
| `rr2.5_vol1.5_rsi55-70` | selectable | 88 | -0.159 | [-0.403, +0.114] | -0.99 | 53 | -0.142 | [-0.472, +0.189] | -0.604 | 14.23% / 14.58% | context: NO-EDGE, not judged |
| `rr2.5_vol2_rsi50-70` | **SELECTED** | 81 | -0.086 | [-0.352, +0.210] | -0.50 | 49 | -0.143 | [-0.500, +0.214] | -0.571 | 12.38% / 12.91% | NO-EDGE |
| `rr2.5_vol2_rsi55-70` | selectable | 75 | -0.200 | [-0.473, +0.080] | -1.17 | 51 | -0.108 | [-0.451, +0.235] | -0.520 | 11.53% / 12.07% | context: NO-EDGE, not judged |
| `rr3_vol1.5_rsi50-70` | selectable | 81 | -0.203 | [-0.493, +0.093] | -1.14 | 47 | -0.064 | [-0.489, +0.362] | -0.574 | 11.20% / 11.64% | context: NO-EDGE, not judged |
| `rr3_vol1.5_rsi55-70` | selectable | 80 | -0.293 | [-0.550, +0.000] | -1.72 | 53 | -0.019 | [-0.396, +0.358] | -0.547 | 10.41% / 10.85% | context: NO-EDGE, not judged |
| `rr3_vol2_rsi50-70` | selectable | 76 | -0.204 | [-0.474, +0.105] | -1.11 | 45 | -0.022 | [-0.467, +0.422] | -0.556 | 9.40% / 9.85% | context: NO-EDGE, not judged |
| `rr3_vol2_rsi55-70` | selectable | 72 | -0.326 | [-0.604, -0.041] | -1.85 | 47 | +0.021 | [-0.404, +0.447] | -0.510 | 8.08% / 8.53% | context: NO-EDGE, not judged |
| `rr2_vol1.5_rsi50-70_regimeOFF` | TEST-ONLY (regime OFF, never selectable) | 133 | -0.274 | [-0.447, -0.090] | -2.46 | 66 | -0.136 | [-0.409, +0.136] | -0.500 | 14.63% / 14.90% | context: NO-EDGE, not judged |

**Re-selecting a variant on these TEST numbers would turn TEST into TRAIN: the selection was fixed on TRAIN before any TEST backtest ran, and it is not revisited.**

The TRAIN column of the selected variant `rr2.5_vol2_rsi50-70` is biased upward by the selection itself (it is the best of 12); only its TEST column is an out-of-sample test.

**Label: NO-EDGE.** Train expectancy is -0.086R over 81 trades, so there is no edge to validate.

Drawdown check (CONTRACT v3 C5; dd_ok = TEST mark-to-market max drawdown <= min(20%, 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length)): **passed**. Test mark-to-market max drawdown 12.91% (realised closed-trade 12.38% (14.00R)) is within the limit 20.00% = min(20%, 95th percentile 21.46% of max drawdown over TRAIN trade sequences bootstrapped at the TEST length of 49). For comparison, TRAIN max drawdown: realised 16.53%, mark-to-market 16.57%.

## 5. Entry layers (ML filter, expectancy guard): TRAIN vs TEST

Two pre-registered layers on the baseline rules. `base+ml` adds `L_ml_filter`, a logistic regression on 6 features (rsi, vol_ratio, ema_gap_pct, dist_regime_pct, hour_sin, hour_cos), fitted ONLY on purged TRAIN candidates and applied unchanged to TEST, so its TRAIN numbers are IN-SAMPLE. `base+guard` switches on `L_expectancy_guard` (`expectancy_guard=True`: a pair's risk is multiplied by 0.5 while its last 20 closed trades average below 0R); it is a fixed rule over the journal, nothing is fitted, and it needs the same walk-forward evidence as any other variant.

An entry layer can only VETO an entry that passed every mandatory rule; it never approves an entry a rule denies. A veto can free R6 budget or change R9 state, admitting other rule-compliant trades, so a layer's journal is not a subset of the base journal: both directions are counted below, matched by (pair, signal time).

| layer | window (TRAIN / TEST) | signals vetoed | entries at reduced risk (guard x < 1) | base trades absent from the layer journal | layer trades absent from the base journal | trades in both | base trades | layer trades |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `base+ml` | TRAIN | 260 | 0 | 73 | 20 | 25 | 98 | 45 |
| `base+ml` | TEST | 175 | 0 | 47 | 6 | 11 | 58 | 17 |
| `base+guard` | TRAIN | 0 | 37 | 0 | 10 | 98 | 98 | 108 |
| `base+guard` | TEST | 0 | 4 | 0 | 0 | 58 | 58 | 58 |

- `base+ml` vs `base`: TRAIN avg R -0.138 -> +0.267 (n 98 -> 45, in-sample) | TEST avg R -0.121 -> -0.294 (n 58 -> 17).
- `base+guard` vs `base`: TRAIN avg R -0.138 -> -0.183 (n 98 -> 108) | TEST avg R -0.121 -> -0.121 (n 58 -> 58).

Logistic filter (l2=1) fitted on 407 TRAIN candidates (TRAIN base win rate 24.6%, context only) vetoes every rule-passing signal whose predicted win probability is at or below the break-even 0.333.

- TRAIN candidates enumerated (purged: outcome resolved before the split): 407; used for the fit: 407 (0 skipped for missing features); TRAIN candidate win share 24.6% (context only).
- Threshold p* = break-even win probability from the TRAIN avg win +2.00R and avg loss -1.00R: p* = 0.333 (no threshold search).
- L2 penalty 1 (fixed, not tuned); intercept -1.173.
- Model fingerprint (sha256 of the canonical JSON of features, TRAIN means/stds, intercept, coefficients, threshold and l2): `bbc52bd7ee0166eaf2899d12f2aad10e5118cfc5595e36550ac6f5358d44f32d`. It is pinned in the ML adoption record (section 11); `MLFilter.from_json` re-verifies it when the live bot loads the model file.

| feature | standardised coefficient, fitted on TRAIN only and applied unchanged to TEST (log-odds per TRAIN s.d.) |
|---|---:|
| `rsi` | -0.123 |
| `vol_ratio` | -0.116 |
| `ema_gap_pct` | +0.090 |
| `dist_regime_pct` | -0.416 |
| `hour_sin` | +0.018 |
| `hour_cos` | -0.090 |

| metric | base TRAIN | base+ml TRAIN (in-sample) | base+guard TRAIN | base TEST | base+ml TEST | base+guard TEST |
|---|---:|---:|---:|---:|---:|---:|
| trades (n) | 98 | 45 | 108 | 58 | 17 | 58 |
| avg R (expectancy, the target metric) | -0.138 | +0.267 | -0.183 | -0.121 | -0.294 | -0.121 |
| iid bootstrap 90% CI of avg R | [-0.357, +0.102] | [-0.067, +0.600] | [-0.384, +0.026] | [-0.379, +0.190] | [-0.824, +0.235] | [-0.379, +0.190] |
| calendar-month block bootstrap 90% CI of avg R | [-0.323, +0.042] (44 months) | [-0.038, +0.600] (28 months) | [-0.352, -0.018] (45 months) | [-0.381, +0.154] (20 months) | [-0.701, +0.091] (11 months) | [-0.381, +0.154] (20 months) |
| one-sided lower bound of avg R at 1 - alpha/m, iid / block | -0.444 / -0.394 (98.75%) | -0.200 / -0.143 (98.75%) | -0.460 / -0.416 (98.75%) | -0.483 / -0.492 (98.75%) | -0.824 / -0.800 (98.75%) | -0.483 / -0.492 (98.75%) |
| adjusted lower bound used by the label (the smaller) | -0.444 | -0.200 | -0.460 | -0.492 | -0.824 | -0.492 |
| t-stat of avg R | -1.00 | +1.19 | -1.43 | -0.67 | -0.92 | -0.67 |
| total R | -13.48 | +12.00 | -19.73 | -7.00 | -5.00 | -7.00 |
| profit factor | 0.73 | 1.11 | 0.69 | 0.80 | 0.65 | 0.80 |
| max drawdown % realised (closed trades) | 16.86% | 6.84% | 16.15% | 12.84% | 7.73% | 12.81% |
| max drawdown % mark-to-market (4H closes) | 18.54% | 6.84% | 17.67% | 13.76% | 8.83% | 13.76% |
| max drawdown R (closed trades) | 19.00 | 7.00 | 21.73 | 14.00 | 9.00 | 14.00 |
| win rate (context only, never a target) | 28.6% | 42.2% | 26.9% | 29.3% | 23.5% | 29.3% |
| exits SL / TP / END | 69 / 28 / 1 | 26 / 19 / 0 | 77 / 29 / 2 | 41 / 17 / 0 | 13 / 4 / 0 | 41 / 17 / 0 |
| avg hold (h) | 177.8 | 107.0 | 170.7 | 112.6 | 52.9 | 112.6 |

`base+ml`:

**Label: UNTESTED.** Too few trades to judge: test n=17 (need 30).

Drawdown check (CONTRACT v3 C5; dd_ok = TEST mark-to-market max drawdown <= min(20%, 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length)): **FAILED**. Test mark-to-market max drawdown 8.83% (realised closed-trade 7.73% (9.00R)) exceeds the limit 8.21% = min(20%, 95th percentile 8.21% of max drawdown over TRAIN trade sequences bootstrapped at the TEST length of 17). For comparison, TRAIN max drawdown: realised 6.84%, mark-to-market 6.84%.

`base+guard`:

**Label: NO-EDGE.** Train expectancy is -0.183R over 108 trades, so there is no edge to validate.

Drawdown check (CONTRACT v3 C5; dd_ok = TEST mark-to-market max drawdown <= min(20%, 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length)): **passed**. Test mark-to-market max drawdown 13.76% (realised closed-trade 12.81% (14.00R)) is within the limit 19.17% = min(20%, 95th percentile 19.17% of max drawdown over TRAIN trade sequences bootstrapped at the TEST length of 58). For comparison, TRAIN max drawdown: realised 16.15%, mark-to-market 17.67%.

LightGBM, other gradient boosting and neural networks are NOT justified at this sample size (407 TRAIN candidates, 6 features): a flexible model would memorise noise, so only a fixed-penalty logistic regression with a break-even threshold is allowed.

## 6. Verdict

Candidates judged (pre-registered, one TEST look each, m = 4): baseline `base`, discovery-selected `rr2.5_vol2_rsi50-70`, ML layer `base+ml`, expectancy guard `base+guard`. Other grid variants are excluded because choosing among them by TEST numbers would be selection on TEST. ROBUST requires at least 30 trades in each window, TRAIN and TEST avg R > 0, and a one-sided 98.75% lower bound of the TEST mean above zero for BOTH an iid and a calendar-month block bootstrap (the more conservative is used): alpha 0.05 is split over the m = 4 pre-registered candidates (base, discovery-selected, base+ml, base+guard), so when none has an edge the chance that ANY is called ROBUST is at most about 5%; a positive TEST mean that fails only the bound is UNTESTED (positive but not distinguishable from zero after multiplicity correction); the rule was fixed in advance and is never tuned on TEST outcomes. The TEST gate is reached when TRAIN avg R > 0 and both windows hold >= 30 trades; the drawdown check is reported beside the label.

| candidate | TRAIN n | TRAIN avg R | TEST n | TEST avg R | TEST iid / block LB (1 - alpha/m) | reached the TEST gate | label | TEST MTM max DD vs limit | dd_ok |
|---|---:|---:|---:|---:|---|---|---|---|---|
| baseline `base` | 98 | -0.138 | 58 | -0.121 | -0.483 / -0.492 | no | NO-EDGE | 13.76% vs 20.00% | yes |
| discovery-selected `rr2.5_vol2_rsi50-70` | 81 | -0.086 | 49 | -0.143 | -0.571 / -0.538 | no | NO-EDGE | 12.91% vs 20.00% | yes |
| ML layer `base+ml` | 45 | +0.267 | 17 | -0.294 | -0.824 / -0.800 | no | UNTESTED | 8.83% vs 8.21% | no |
| expectancy guard `base+guard` | 108 | -0.183 | 58 | -0.121 | -0.483 / -0.492 | no | NO-EDGE | 13.76% vs 19.17% | yes |

No robust result found. Pre-registered candidates, TRAIN | TEST: baseline `base` NO-EDGE (TRAIN -0.138R n=98 | TEST -0.121R n=58); discovery-selected `rr2.5_vol2_rsi50-70` NO-EDGE (TRAIN -0.086R n=81 | TEST -0.143R n=49); ML layer `base+ml` UNTESTED (TRAIN +0.267R n=45 | TEST -0.294R n=17); expectancy guard `base+guard` NO-EDGE (TRAIN -0.183R n=108 | TEST -0.121R n=58).

## 7. Why signals were rejected (decision counts per rule)

Signal candles denied per rule (the FIRST failing rule is counted, so the R1-R4 rows include every candle that was simply not a signal). Exits are never gated.

| rule | base TRAIN | base TEST | rr2.5_vol2_rsi50-70 TRAIN | rr2.5_vol2_rsi50-70 TEST | base+ml TRAIN | base+ml TEST | base+guard TRAIN | base+guard TEST | meaning |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `R1_trend` | 18810 | 7617 | 18810 | 7617 | 18810 | 7617 | 18810 | 7617 | Trend gate: close > EMA9 and close > EMA21 and EMA9 > EMA21 (4H) |
| `R2_momentum` | 1187 | 758 | 1187 | 758 | 1187 | 758 | 1187 | 758 | Momentum: RSI(14) within [50, 70] on the entry candle |
| `R3_volume` | 6920 | 3134 | 7172 | 3252 | 6920 | 3134 | 6920 | 3134 | Volume: entry-candle volume >= 1.5x the previous-20-candle average |
| `R4_regime` | 254 | 97 | 156 | 56 | 254 | 97 | 254 | 97 | Regime: close > EMA200 (unless explicitly testing it off) |
| `R5_news_blackout` | 13 | 8 | 10 | 6 | 13 | 8 | 13 | 8 | No entries within +/-2h of high-impact news; BNB +/-24h burns/launchpool |
| `R6_correlation_cap` | 312 | 152 | 178 | 87 | 104 | 18 | 302 | 152 | BTC/ETH/BNB share one risk budget; BNB never stacks |
| `R7_position_risk` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Risk <= 1% (BTC/ETH) / 0.5% (BNB); size derived from stop distance |
| `R8_structure_stop` | 0 | 2 | 0 | 0 | 1 | 2 | 0 | 2 | Stop behind a confirmed swing low with a buffer (BNB 0.5-0.8%) |
| `R9_circuit_breaker` | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 3 consecutive SLs bench a pair 24h; 7d realized loss limit halts all |
| `L_ml_filter` | 0 | 0 | 0 | 0 | 260 | 175 | 0 | 0 | Layer: logistic-regression veto; it can only veto an entry that passed every mandatory rule and never approves one a rule denies (a veto can free R6 budget or change R9 state, which may admit other rule-compliant trades) |
| `L_expectancy_guard` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Layer: halve a pair's risk while its last-N-trade expectancy < 0 |
| `X_capital` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Execution: not enough free capital / size below minimum |
| candles evaluated | 27594 | 11826 | 27594 | 11826 | 27594 | 11826 | 27594 | 11826 | |
| trades taken | 98 | 58 | 81 | 49 | 45 | 17 | 108 | 58 | |

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
| TRAIN at 2023-03-14T00:00:00Z (equity 8,599.00) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 9,283.92) | - | - | none | No active adaptations. | - |

**discovery-selected `rr2.5_vol2_rsi50-70`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 8,896.75) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 9,351.77) | - | - | none | No active adaptations. | - |

**ML layer `base+ml`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 10,270.77) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 9,645.37) | - | - | none | No active adaptations. | - |

**expectancy guard `base+guard`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 8,520.35) | L_expectancy_guard | BTC/USDT | risk x0.5 | BTC/USDT's last 20 closed trades average -0.213R (below 0), so new BTC/USDT entries risk 0.5x the normal amount until that average is back at or above 0. | #58, #63, #64, #72, #78, #81, #83, #86, #87, #89, #90, #91, #92, #93, #95, #96, #98, #102, #104, #108 |
| TRAIN at 2023-03-14T00:00:00Z (equity 8,520.35) | L_expectancy_guard | ETH/USDT | risk x0.5 | ETH/USDT's last 20 closed trades average -0.374R (below 0), so new ETH/USDT entries risk 0.5x the normal amount until that average is back at or above 0. | #49, #50, #55, #59, #61, #62, #67, #74, #75, #76, #77, #79, #84, #85, #88, #94, #97, #101, #103, #107 |
| TEST at 2024-12-30T00:00:00Z (equity 9,332.90) | L_expectancy_guard | ETH/USDT | risk x0.5 | ETH/USDT's last 20 closed trades average -0.250R (below 0), so new ETH/USDT entries risk 0.5x the normal amount until that average is back at or above 0. | #122, #124, #125, #128, #131, #132, #135, #137, #139, #141, #144, #146, #147, #148, #152, #153, #158, #159, #164, #166 |

Reproduce the baseline TEST row with `python -m research.trendbot.journal_rules --journal research/results/synthetic_null_s1/journals/base_test.csv --equity 9283.92 --now 2024-12-30T00:00:00Z --backtest-journal`.

## 10. Journals and human review packs

Trade journals (`journal.write_journal`, one CSV per window) for the 4 pre-registered candidates that ran, the variants whose TRAIN and TEST columns this report shows in full. The other discovery variants appear only as one context row each in section 4, so no journal is kept for them (the same command regenerates them). TEST trade ids are offset past the TRAIN ids, so ids are unique across a variant's two journals and its review pack.

| variant | TRAIN journal | TEST journal |
|---|---|---|
| `base` | [journals/base_train.csv](journals/base_train.csv) | [journals/base_test.csv](journals/base_test.csv) |
| `rr2.5_vol2_rsi50-70` | [journals/rr2.5_vol2_rsi50-70_train.csv](journals/rr2.5_vol2_rsi50-70_train.csv) | [journals/rr2.5_vol2_rsi50-70_test.csv](journals/rr2.5_vol2_rsi50-70_test.csv) |
| `base+ml` | [journals/base_plus_ml_train.csv](journals/base_plus_ml_train.csv) | [journals/base_plus_ml_test.csv](journals/base_plus_ml_test.csv) |
| `base+guard` | [journals/base_plus_guard_train.csv](journals/base_plus_guard_train.csv) | [journals/base_plus_guard_test.csv](journals/base_plus_guard_test.csv) |

Human review packs (`review_sheet.write_review_pack`, TRAIN and TEST):

- `base`: [review_base/trades_review.md](review_base/trades_review.md) and [review_base/trades_review.csv](review_base/trades_review.csv)
- `rr2.5_vol2_rsi50-70`: [review_selected_rr2.5_vol2_rsi50-70/trades_review.md](review_selected_rr2.5_vol2_rsi50-70/trades_review.md) and [review_selected_rr2.5_vol2_rsi50-70/trades_review.csv](review_selected_rr2.5_vol2_rsi50-70/trades_review.csv)
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
| `base` | NO-EDGE | 98 | -0.138 | 58 | -0.121 | yes | `synthetic:null:1` | `9be9d6da6ad86d85` | `none (no ML layer)` | PASS | BLOCKED by ADOPT_provenance, ADOPT_walk_forward |
| `rr2.5_vol2_rsi50-70` | NO-EDGE | 81 | -0.086 | 49 | -0.143 | yes | `synthetic:null:1` | `04c83eeb8d4d852e` | `none (no ML layer)` | PASS | BLOCKED by ADOPT_provenance, ADOPT_walk_forward |
| `base+ml` | UNTESTED | 45 | +0.267 | 17 | -0.294 | no | `synthetic:null:1` | `9be9d6da6ad86d85` | `bbc52bd7ee0166ea` | PASS | BLOCKED by ADOPT_provenance, ADOPT_walk_forward |
| `base+guard` | NO-EDGE | 108 | -0.183 | 58 | -0.121 | yes | `synthetic:null:1` | `2c5274264328eeee` | `none (no ML layer)` | PASS | BLOCKED by ADOPT_provenance, ADOPT_walk_forward |

- `base`: record [adoption_base.json](adoption_base.json).
  - `--stage WALK_FORWARD`: PASS
    Check: `python -m research.trendbot.adoption check --record research/results/synthetic_null_s1/adoption_base.json --stage WALK_FORWARD`
  - `--stage HUMAN_REVIEW`: BLOCKED: [ADOPT_provenance] provenance is 'synthetic:null:1': a synthetic world only verifies the harness and is never evidence about real markets, so it may not go past WALK_FORWARD. [ADOPT_provenance] data_files is missing or empty, so the market data behind the backtest is not bound to this record by sha256. [ADOPT_walk_forward] walk_forward.label is 'NO-EDGE' (the train window showed no positive expectancy to validate), and only ROBUST may proceed. [ADOPT_walk_forward] walk_forward.test_avg_r must be a positive out-of-sample expectancy in R (got -0.12068965517241485).
    Check: `python -m research.trendbot.adoption check --record research/results/synthetic_null_s1/adoption_base.json --stage HUMAN_REVIEW`
- `rr2.5_vol2_rsi50-70`: record [adoption_rr2.5_vol2_rsi50-70.json](adoption_rr2.5_vol2_rsi50-70.json), [config_rr2.5_vol2_rsi50-70.json](config_rr2.5_vol2_rsi50-70.json).
  - `--stage WALK_FORWARD`: PASS
    Check: `python -m research.trendbot.adoption check --record research/results/synthetic_null_s1/adoption_rr2.5_vol2_rsi50-70.json --stage WALK_FORWARD --config research/results/synthetic_null_s1/config_rr2.5_vol2_rsi50-70.json`
  - `--stage HUMAN_REVIEW`: BLOCKED: [ADOPT_provenance] provenance is 'synthetic:null:1': a synthetic world only verifies the harness and is never evidence about real markets, so it may not go past WALK_FORWARD. [ADOPT_provenance] data_files is missing or empty, so the market data behind the backtest is not bound to this record by sha256. [ADOPT_walk_forward] walk_forward.label is 'NO-EDGE' (the train window showed no positive expectancy to validate), and only ROBUST may proceed. [ADOPT_walk_forward] walk_forward.test_avg_r must be a positive out-of-sample expectancy in R (got -0.1428571428571439).
    Check: `python -m research.trendbot.adoption check --record research/results/synthetic_null_s1/adoption_rr2.5_vol2_rsi50-70.json --stage HUMAN_REVIEW --config research/results/synthetic_null_s1/config_rr2.5_vol2_rsi50-70.json`
- `base+ml`: record [adoption_base_plus_ml.json](adoption_base_plus_ml.json), [model_base_plus_ml.json](model_base_plus_ml.json).
  - `--stage WALK_FORWARD`: PASS
    Check: `python -m research.trendbot.adoption check --record research/results/synthetic_null_s1/adoption_base_plus_ml.json --stage WALK_FORWARD --model-fingerprint bbc52bd7ee0166eaf2899d12f2aad10e5118cfc5595e36550ac6f5358d44f32d`
  - `--stage HUMAN_REVIEW`: BLOCKED: [ADOPT_provenance] provenance is 'synthetic:null:1': a synthetic world only verifies the harness and is never evidence about real markets, so it may not go past WALK_FORWARD. [ADOPT_provenance] data_files is missing or empty, so the market data behind the backtest is not bound to this record by sha256. [ADOPT_walk_forward] walk_forward.label is 'UNTESTED' (too few trades, or a test expectancy not distinguishable from zero), and only ROBUST may proceed. [ADOPT_walk_forward] walk_forward.dd_ok is false: the test-window drawdown exceeded the limit. [ADOPT_walk_forward] walk_forward.test_avg_r must be a positive out-of-sample expectancy in R (got -0.29411764705882426).
    Check: `python -m research.trendbot.adoption check --record research/results/synthetic_null_s1/adoption_base_plus_ml.json --stage HUMAN_REVIEW --model-fingerprint bbc52bd7ee0166eaf2899d12f2aad10e5118cfc5595e36550ac6f5358d44f32d`
- `base+guard`: record [adoption_base_plus_guard.json](adoption_base_plus_guard.json), [config_base_plus_guard.json](config_base_plus_guard.json).
  - `--stage WALK_FORWARD`: PASS
    Check: `python -m research.trendbot.adoption check --record research/results/synthetic_null_s1/adoption_base_plus_guard.json --stage WALK_FORWARD --config research/results/synthetic_null_s1/config_base_plus_guard.json`
  - `--stage HUMAN_REVIEW`: BLOCKED: [ADOPT_provenance] provenance is 'synthetic:null:1': a synthetic world only verifies the harness and is never evidence about real markets, so it may not go past WALK_FORWARD. [ADOPT_provenance] data_files is missing or empty, so the market data behind the backtest is not bound to this record by sha256. [ADOPT_walk_forward] walk_forward.label is 'NO-EDGE' (the train window showed no positive expectancy to validate), and only ROBUST may proceed. [ADOPT_walk_forward] walk_forward.test_avg_r must be a positive out-of-sample expectancy in R (got -0.12068965517241485).
    Check: `python -m research.trendbot.adoption check --record research/results/synthetic_null_s1/adoption_base_plus_guard.json --stage HUMAN_REVIEW --config research/results/synthetic_null_s1/config_base_plus_guard.json`

The ML layer `base+ml` is adopted as a (config, model) pair: its record pins the config fingerprint AND the fitted model's fingerprint `bbc52bd7ee0166eaf2899d12f2aad10e5118cfc5595e36550ac6f5358d44f32d` (`MLFilter.fingerprint()`). The model itself is [model_base_plus_ml.json](model_base_plus_ml.json) (`MLFilter.to_json()`), which the live bot loads with `MLFilter.from_json(text, expected_fingerprint=<the record's model_fingerprint>)`: loading raises if the file does not hash to that fingerprint. **Refitting the model (new TRAIN data, a later split, a different l2) changes the fingerprint, so a refitted model is a NEW candidate and restarts the adoption path at BACKTEST.** `adoption check` blocks (`ADOPT_fingerprint`) a model whose fingerprint differs from the recorded one, and any `+ml` record without one.

A `config_<variant>.json` file (the `StrategyConfig` overrides versus the defaults, e.g. costs, discovery parameters or `expectancy_guard`) is written whenever the tested config is not the default one; the check needs it as `--config`. The regime-OFF variant is test-only and can never be adopted.

## 12. Risk disclaimer

This is a research and testing tool, not financial advice and not a recommendation to trade. Backtests and synthetic worlds are simplified models; past or simulated results do not predict future results. Crypto trading can result in the total loss of the capital used.
