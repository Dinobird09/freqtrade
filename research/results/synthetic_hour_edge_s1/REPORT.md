# Walk-forward research report: synthetic world `hour_edge` seed 1

## 1. No win rate is promised or targeted

This report does NOT promise, target or optimise a win rate, and nothing in this package selects on one. A win rate is meaningless without the reward:risk it was earned at: with the mandatory minimum of 2:1 a strategy breaks even at about 33% winners before costs, while a 90% win rate with a 1:9 payoff still loses money. A backtest win rate near 90% is evidence of curve-fitting or look-ahead, not of skill. The target metric is POSITIVE EXPECTANCY (average R per trade, net of fees and slippage) that survives a 70/30 chronological walk-forward with controlled drawdown, with every trade planned at reward:risk >= 2:1. Win rate appears below only as context and is labelled 'context only' wherever it is shown.

## 2. Data provenance

- Source: **SYNTHETIC** world `hour_edge`, seed 1, 6 years of 4H candles (13140 per pair) from 2019-01-01T00:00:00Z, pairs BTC/USDT, ETH/USDT, BNB/USDT (`synthetic.generate_world`); adoption provenance `synthetic:hour_edge:1`.
- Ground truth: the planted effect exists only for spike candles closing 12:00-20:00 UTC; spikes closing at other hours trigger nothing.
- What the ground truth predicts: the hour-blind base is diluted (weak or no edge); an hour-aware filter fitted on TRAIN has something real to learn and should raise TEST expectancy versus the base.
- Qualifying triggers planted per pair (before / after the 70% split): BTC/USDT 165 / 77, ETH/USDT 153 / 65, BNB/USDT 168 / 63.
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
- Measured on the baseline's TRAIN and TEST trades: fees alone cost 0.06R per trade on average (median stop distance 3.48% of the entry price), so the expectancy below is only as good as the fee rate above.

## 3. Baseline: TRAIN vs TEST

Default mandate config `rr2_vol1.5_rsi50-70` (variant `base`). TRAIN window start .. 2023-03-14T00:00:00Z; TEST window 2023-03-14T00:00:00Z .. 2024-12-30T00:00:00Z.

| metric | TRAIN | TEST |
|---|---:|---:|
| trades (n) | 100 | 60 |
| avg R (expectancy, the target metric) | -0.044 | +0.050 |
| iid bootstrap 90% CI of avg R | [-0.280, +0.193] | [-0.250, +0.350] |
| calendar-month block bootstrap 90% CI of avg R | [-0.275, +0.196] (41 months) | [-0.226, +0.327] (20 months) |
| one-sided lower bound of avg R at 1 - alpha/m, iid / block | -0.344 / -0.359 (98.75%) | -0.350 / -0.333 (98.75%) |
| adjusted lower bound used by the label (the smaller) | -0.359 | -0.350 |
| t-stat of avg R | -0.31 | +0.27 |
| total R | -4.36 | +3.00 |
| profit factor | 0.87 | 1.06 |
| max drawdown % realised (closed trades) | 14.97% | 9.60% |
| max drawdown % mark-to-market (4H closes) | 15.50% | 9.73% |
| max drawdown R (closed trades) | 16.00 | 11.00 |
| win rate (context only, never a target) | 32.0% | 35.0% |
| exits SL / TP / END | 68 / 31 / 1 | 39 / 21 / 0 |
| avg hold (h) | 137.8 | 92.0 |

**Label: NO-EDGE.** Train expectancy is -0.044R over 100 trades, so there is no edge to validate.

Drawdown check (CONTRACT v3 C5; dd_ok = TEST mark-to-market max drawdown <= min(20%, 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length)): **passed**. Test mark-to-market max drawdown 9.73% (realised closed-trade 9.60% (11.00R)) is within the limit 19.54% = min(20%, 95th percentile 19.54% of max drawdown over TRAIN trade sequences bootstrapped at the TEST length of 60). For comparison, TRAIN max drawdown: realised 14.97%, mark-to-market 15.50%.

## 4. Strategy discovery: TRAIN vs TEST

K = 13 variants tried: 12 legal tightenings (reward:risk {2, 2.5, 3} x volume multiple {1.5, 2} x RSI window {50-70, 55-70}) plus 1 explicit test-only variant with the R4 regime filter OFF, which is reported but can never be selected or adopted.

Selection rule: highest TRAIN t-stat among selectable variants with >= 30 TRAIN trades. rr3_vol2_rsi50-70 has the highest TRAIN t-stat (+0.94, avg +0.203R over 72 trades) among 12 eligible variants; chosen on TRAIN only, before any TEST backtest was run.

Order of operations actually run: TRAIN backtests: 13 variants, candles before the split only -> selection recorded: rr3_vol2_rsi50-70 -> TEST backtests: 13 variants, reported side by side only.

Only the selected variant is a pre-registered candidate (one of the m TEST looks of section 6). Every other row, including the regime-OFF test variant, is shown as `context: <label>, not judged`: picking one of them because of its TEST numbers would be selection on TEST.

| variant | status | TRAIN n | TRAIN avg R | TRAIN 90% CI | TRAIN t | TEST n | TEST avg R | TEST 90% CI | TEST adjusted LB | TEST max DD % realised / MTM | label |
|---|---|---:|---:|---|---:|---:|---:|---|---:|---|---|
| `rr2_vol1.5_rsi50-70` | selectable | 100 | -0.044 | [-0.280, +0.193] | -0.31 | 60 | +0.050 | [-0.250, +0.350] | -0.350 | 9.60% / 9.73% | context: NO-EDGE, not judged |
| `rr2_vol1.5_rsi55-70` | selectable | 92 | +0.040 | [-0.198, +0.297] | +0.27 | 58 | +0.086 | [-0.224, +0.397] | -0.328 | 8.25% / 8.35% | context: UNTESTED, not judged |
| `rr2_vol2_rsi50-70` | selectable | 82 | +0.130 | [-0.126, +0.390] | +0.81 | 44 | +0.159 | [-0.182, +0.500] | -0.318 | 4.90% / 5.42% | context: UNTESTED, not judged |
| `rr2_vol2_rsi55-70` | selectable | 77 | +0.125 | [-0.143, +0.398] | +0.75 | 42 | +0.143 | [-0.214, +0.500] | -0.357 | 4.90% / 5.42% | context: UNTESTED, not judged |
| `rr2.5_vol1.5_rsi50-70` | selectable | 97 | +0.001 | [-0.251, +0.263] | +0.01 | 57 | +0.167 | [-0.202, +0.535] | -0.325 | 7.78% / 7.92% | context: UNTESTED, not judged |
| `rr2.5_vol1.5_rsi55-70` | selectable | 89 | +0.052 | [-0.223, +0.327] | +0.31 | 56 | +0.187 | [-0.188, +0.562] | -0.250 | 7.32% / 7.32% | context: UNTESTED, not judged |
| `rr2.5_vol2_rsi50-70` | selectable | 78 | +0.156 | [-0.147, +0.459] | +0.83 | 43 | +0.302 | [-0.105, +0.709] | -0.267 | 4.90% / 5.42% | context: UNTESTED, not judged |
| `rr2.5_vol2_rsi55-70` | selectable | 73 | +0.139 | [-0.161, +0.463] | +0.72 | 42 | +0.333 | [-0.083, +0.750] | -0.250 | 4.90% / 5.42% | context: UNTESTED, not judged |
| `rr3_vol1.5_rsi50-70` | selectable | 87 | -0.050 | [-0.326, +0.242] | -0.28 | 51 | +0.176 | [-0.216, +0.569] | -0.373 | 5.37% / 5.37% | context: NO-EDGE, not judged |
| `rr3_vol1.5_rsi55-70` | selectable | 80 | -0.017 | [-0.317, +0.300] | -0.09 | 50 | +0.200 | [-0.200, +0.600] | -0.360 | 5.37% / 5.37% | context: NO-EDGE, not judged |
| `rr3_vol2_rsi50-70` | **SELECTED** | 72 | +0.203 | [-0.130, +0.556] | +0.94 | 39 | +0.436 | [-0.077, +0.949] | -0.282 | 4.90% / 5.42% | UNTESTED |
| `rr3_vol2_rsi55-70` | selectable | 68 | +0.156 | [-0.196, +0.509] | +0.71 | 38 | +0.474 | [-0.053, +1.000] | -0.263 | 4.90% / 5.42% | context: UNTESTED, not judged |
| `rr2_vol1.5_rsi50-70_regimeOFF` | TEST-ONLY (regime OFF, never selectable) | 153 | +0.213 | [+0.017, +0.409] | +1.79 | 79 | +0.101 | [-0.165, +0.367] | -0.278 | 8.75% / 9.43% | context: UNTESTED, not judged |

**Re-selecting a variant on these TEST numbers would turn TEST into TRAIN: the selection was fixed on TRAIN before any TEST backtest ran, and it is not revisited.**

The TRAIN column of the selected variant `rr3_vol2_rsi50-70` is biased upward by the selection itself (it is the best of 12); only its TEST column is an out-of-sample test.

**Label: UNTESTED.** Test expectancy +0.436R (n=39) is positive but not distinguishable from zero after multiplicity correction: the one-sided 98.75% lower bound of the test mean (alpha 0.05 split over m=4 pre-registered candidates) is -0.282R by the iid bootstrap and -0.250R by the calendar-month block bootstrap (18 months, 4000 resamples each), and the more conservative -0.282R is not above zero.

Drawdown check (CONTRACT v3 C5; dd_ok = TEST mark-to-market max drawdown <= min(20%, 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length)): **passed**. Test mark-to-market max drawdown 5.42% (realised closed-trade 4.90% (6.00R)) is within the limit 13.93% = min(20%, 95th percentile 13.93% of max drawdown over TRAIN trade sequences bootstrapped at the TEST length of 39). For comparison, TRAIN max drawdown: realised 8.19%, mark-to-market 9.12%.

## 5. Entry layers (ML filter, expectancy guard): TRAIN vs TEST

Two pre-registered layers on the baseline rules. `base+ml` adds `L_ml_filter`, a logistic regression on 6 features (rsi, vol_ratio, ema_gap_pct, dist_regime_pct, hour_sin, hour_cos), fitted ONLY on purged TRAIN candidates and applied unchanged to TEST, so its TRAIN numbers are IN-SAMPLE. `base+guard` switches on `L_expectancy_guard` (`expectancy_guard=True`: a pair's risk is multiplied by 0.5 while its last 20 closed trades average below 0R); it is a fixed rule over the journal, nothing is fitted, and it needs the same walk-forward evidence as any other variant.

An entry layer can only VETO an entry that passed every mandatory rule; it never approves an entry a rule denies. A veto can free R6 budget or change R9 state, admitting other rule-compliant trades, so a layer's journal is not a subset of the base journal: both directions are counted below, matched by (pair, signal time).

| layer | window (TRAIN / TEST) | signals vetoed | entries at reduced risk (guard x < 1) | base trades absent from the layer journal | layer trades absent from the base journal | trades in both | base trades | layer trades |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `base+ml` | TRAIN | 98 | 0 | 61 | 30 | 39 | 100 | 69 |
| `base+ml` | TEST | 30 | 0 | 25 | 9 | 35 | 60 | 44 |
| `base+guard` | TRAIN | 0 | 31 | 1 | 12 | 99 | 100 | 111 |
| `base+guard` | TEST | 0 | 1 | 0 | 1 | 60 | 60 | 61 |

- `base+ml` vs `base`: TRAIN avg R -0.044 -> +0.299 (n 100 -> 69, in-sample) | TEST avg R +0.050 -> +0.295 (n 60 -> 44).
- `base+guard` vs `base`: TRAIN avg R -0.044 -> -0.057 (n 100 -> 111) | TEST avg R +0.050 -> +0.082 (n 60 -> 61).

Logistic filter (l2=1) fitted on 249 TRAIN candidates (TRAIN base win rate 32.5%, context only) vetoes every rule-passing signal whose predicted win probability is at or below the break-even 0.333.

- TRAIN candidates enumerated (purged: outcome resolved before the split): 249; used for the fit: 249 (0 skipped for missing features); TRAIN candidate win share 32.5% (context only).
- Threshold p* = break-even win probability from the TRAIN avg win +2.00R and avg loss -1.00R: p* = 0.333 (no threshold search).
- L2 penalty 1 (fixed, not tuned); intercept -0.768.
- Model fingerprint (sha256 of the canonical JSON of features, TRAIN means/stds, intercept, coefficients, threshold and l2): `b38d6af2d40d7fa65a3bd7386a4c4504718409b0770eaa6daac3b85a8ddf8b71`. It is pinned in the ML adoption record (section 11); `MLFilter.from_json` re-verifies it when the live bot loads the model file.

| feature | standardised coefficient, fitted on TRAIN only and applied unchanged to TEST (log-odds per TRAIN s.d.) |
|---|---:|
| `rsi` | +0.182 |
| `vol_ratio` | +0.196 |
| `ema_gap_pct` | -0.128 |
| `dist_regime_pct` | +0.151 |
| `hour_sin` | -0.413 |
| `hour_cos` | -0.079 |

| metric | base TRAIN | base+ml TRAIN (in-sample) | base+guard TRAIN | base TEST | base+ml TEST | base+guard TEST |
|---|---:|---:|---:|---:|---:|---:|
| trades (n) | 100 | 69 | 111 | 60 | 44 | 61 |
| avg R (expectancy, the target metric) | -0.044 | +0.299 | -0.057 | +0.050 | +0.295 | +0.082 |
| iid bootstrap 90% CI of avg R | [-0.280, +0.193] | [+0.000, +0.598] | [-0.270, +0.162] | [-0.250, +0.350] | [-0.045, +0.705] | [-0.213, +0.377] |
| calendar-month block bootstrap 90% CI of avg R | [-0.275, +0.196] (41 months) | [-0.040, +0.645] (38 months) | [-0.261, +0.165] (42 months) | [-0.226, +0.327] (20 months) | [+0.029, +0.500] (19 months) | [-0.190, +0.358] (20 months) |
| one-sided lower bound of avg R at 1 - alpha/m, iid / block | -0.344 / -0.359 (98.75%) | -0.092 / -0.174 (98.75%) | -0.351 / -0.331 (98.75%) | -0.350 / -0.333 (98.75%) | -0.182 / -0.057 (98.75%) | -0.311 / -0.304 (98.75%) |
| adjusted lower bound used by the label (the smaller) | -0.359 | -0.174 | -0.351 | -0.350 | -0.182 | -0.311 |
| t-stat of avg R | -0.31 | +1.66 | -0.43 | +0.27 | +1.30 | +0.44 |
| total R | -4.36 | +20.64 | -6.36 | +3.00 | +13.00 | +5.00 |
| profit factor | 0.87 | 1.52 | 0.86 | 1.06 | 1.49 | 1.06 |
| max drawdown % realised (closed trades) | 14.97% | 8.83% | 14.75% | 9.60% | 5.38% | 9.60% |
| max drawdown % mark-to-market (4H closes) | 15.50% | 9.87% | 15.17% | 9.73% | 5.89% | 9.73% |
| max drawdown R (closed trades) | 16.00 | 9.00 | 19.00 | 11.00 | 6.00 | 11.00 |
| win rate (context only, never a target) | 32.0% | 43.5% | 31.5% | 35.0% | 43.2% | 36.1% |
| exits SL / TP / END | 68 / 31 / 1 | 39 / 29 / 1 | 76 / 34 / 1 | 39 / 21 / 0 | 25 / 19 / 0 | 39 / 22 / 0 |
| avg hold (h) | 137.8 | 103.7 | 129.7 | 92.0 | 87.9 | 90.7 |

`base+ml`:

**Label: UNTESTED.** Test expectancy +0.295R (n=44) is positive but not distinguishable from zero after multiplicity correction: the one-sided 98.75% lower bound of the test mean (alpha 0.05 split over m=4 pre-registered candidates) is -0.182R by the iid bootstrap and -0.057R by the calendar-month block bootstrap (19 months, 4000 resamples each), and the more conservative -0.182R is not above zero.

Drawdown check (CONTRACT v3 C5; dd_ok = TEST mark-to-market max drawdown <= min(20%, 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length)): **passed**. Test mark-to-market max drawdown 5.89% (realised closed-trade 5.38% (6.00R)) is within the limit 10.06% = min(20%, 95th percentile 10.06% of max drawdown over TRAIN trade sequences bootstrapped at the TEST length of 44). For comparison, TRAIN max drawdown: realised 8.83%, mark-to-market 9.87%.

`base+guard`:

**Label: NO-EDGE.** Train expectancy is -0.057R over 111 trades, so there is no edge to validate.

Drawdown check (CONTRACT v3 C5; dd_ok = TEST mark-to-market max drawdown <= min(20%, 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length)): **passed**. Test mark-to-market max drawdown 9.73% (realised closed-trade 9.60% (11.00R)) is within the limit 17.07% = min(20%, 95th percentile 17.07% of max drawdown over TRAIN trade sequences bootstrapped at the TEST length of 61). For comparison, TRAIN max drawdown: realised 14.75%, mark-to-market 15.17%.

LightGBM, other gradient boosting and neural networks are NOT justified at this sample size (249 TRAIN candidates, 6 features): a flexible model would memorise noise, so only a fixed-penalty logistic regression with a break-even threshold is allowed.

## 6. Verdict

Candidates judged (pre-registered, one TEST look each, m = 4): baseline `base`, discovery-selected `rr3_vol2_rsi50-70`, ML layer `base+ml`, expectancy guard `base+guard`. Other grid variants are excluded because choosing among them by TEST numbers would be selection on TEST. ROBUST requires at least 30 trades in each window, TRAIN and TEST avg R > 0, and a one-sided 98.75% lower bound of the TEST mean above zero for BOTH an iid and a calendar-month block bootstrap (the more conservative is used): alpha 0.05 is split over the m = 4 pre-registered candidates (base, discovery-selected, base+ml, base+guard), so when none has an edge the chance that ANY is called ROBUST is at most about 5%; a positive TEST mean that fails only the bound is UNTESTED (positive but not distinguishable from zero after multiplicity correction); the rule was fixed in advance and is never tuned on TEST outcomes. The TEST gate is reached when TRAIN avg R > 0 and both windows hold >= 30 trades; the drawdown check is reported beside the label.

| candidate | TRAIN n | TRAIN avg R | TEST n | TEST avg R | TEST iid / block LB (1 - alpha/m) | reached the TEST gate | label | TEST MTM max DD vs limit | dd_ok |
|---|---:|---:|---:|---:|---|---|---|---|---|
| baseline `base` | 100 | -0.044 | 60 | +0.050 | -0.350 / -0.333 | no | NO-EDGE | 9.73% vs 19.54% | yes |
| discovery-selected `rr3_vol2_rsi50-70` | 72 | +0.203 | 39 | +0.436 | -0.282 / -0.250 | yes | UNTESTED | 5.42% vs 13.93% | yes |
| ML layer `base+ml` | 69 | +0.299 | 44 | +0.295 | -0.182 / -0.057 | yes | UNTESTED | 5.89% vs 10.06% | yes |
| expectancy guard `base+guard` | 111 | -0.057 | 61 | +0.082 | -0.311 / -0.304 | no | NO-EDGE | 9.73% vs 17.07% | yes |

No robust result found. Pre-registered candidates, TRAIN | TEST: baseline `base` NO-EDGE (TRAIN -0.044R n=100 | TEST +0.050R n=60); discovery-selected `rr3_vol2_rsi50-70` UNTESTED (TRAIN +0.203R n=72 | TEST +0.436R n=39); ML layer `base+ml` UNTESTED (TRAIN +0.299R n=69 | TEST +0.295R n=44); expectancy guard `base+guard` NO-EDGE (TRAIN -0.057R n=111 | TEST +0.082R n=61).

## 7. Why signals were rejected (decision counts per rule)

Signal candles denied per rule (the FIRST failing rule is counted, so the R1-R4 rows include every candle that was simply not a signal). Exits are never gated.

| rule | base TRAIN | base TEST | rr3_vol2_rsi50-70 TRAIN | rr3_vol2_rsi50-70 TEST | base+ml TRAIN | base+ml TEST | base+guard TRAIN | base+guard TEST | meaning |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `R1_trend` | 19100 | 7899 | 19100 | 7899 | 19100 | 7899 | 19100 | 7899 | Trend gate: close > EMA9 and close > EMA21 and EMA9 > EMA21 (4H) |
| `R2_momentum` | 2680 | 1500 | 2680 | 1500 | 2680 | 1500 | 2680 | 1500 | Momentum: RSI(14) within [50, 70] on the entry candle |
| `R3_volume` | 5352 | 2212 | 5517 | 2293 | 5352 | 2212 | 5352 | 2212 | Volume: entry-candle volume >= 1.5x the previous-20-candle average |
| `R4_regime` | 191 | 69 | 126 | 42 | 191 | 69 | 191 | 69 | Regime: close > EMA200 (unless explicitly testing it off) |
| `R5_news_blackout` | 17 | 7 | 11 | 6 | 17 | 7 | 17 | 7 | No entries within +/-2h of high-impact news; BNB +/-24h burns/launchpool |
| `R6_correlation_cap` | 150 | 77 | 87 | 46 | 84 | 63 | 140 | 76 | BTC/ETH/BNB share one risk budget; BNB never stacks |
| `R7_position_risk` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Risk <= 1% (BTC/ETH) / 0.5% (BNB); size derived from stop distance |
| `R8_structure_stop` | 2 | 1 | 1 | 1 | 3 | 1 | 1 | 1 | Stop behind a confirmed swing low with a buffer (BNB 0.5-0.8%) |
| `R9_circuit_breaker` | 2 | 1 | 0 | 0 | 0 | 1 | 2 | 1 | 3 consecutive SLs bench a pair 24h; 7d realized loss limit halts all |
| `L_ml_filter` | 0 | 0 | 0 | 0 | 98 | 30 | 0 | 0 | Layer: logistic-regression veto; it can only veto an entry that passed every mandatory rule and never approves one a rule denies (a veto can free R6 budget or change R9 state, which may admit other rule-compliant trades) |
| `L_expectancy_guard` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Layer: halve a pair's risk while its last-N-trade expectancy < 0 |
| `X_capital` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Execution: not enough free capital / size below minimum |
| candles evaluated | 27594 | 11826 | 27594 | 11826 | 27594 | 11826 | 27594 | 11826 | |
| trades taken | 100 | 60 | 72 | 39 | 69 | 44 | 111 | 61 | |

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
| TRAIN at 2023-03-14T00:00:00Z (equity 9,321.09) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 10,209.68) | - | - | none | No active adaptations. | - |

**discovery-selected `rr3_vol2_rsi50-70`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 10,816.17) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 11,590.48) | - | - | none | No active adaptations. | - |

**ML layer `base+ml`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 11,917.16) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 11,070.51) | - | - | none | No active adaptations. | - |

**expectancy guard `base+guard`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 9,322.44) | L_expectancy_guard | BTC/USDT | risk x0.5 | BTC/USDT's last 20 closed trades average -0.250R (below 0), so new BTC/USDT entries risk 0.5x the normal amount until that average is back at or above 0. | #53, #57, #58, #59, #65, #67, #68, #72, #79, #84, #85, #87, #88, #89, #93, #96, #98, #100, #102, #104 |
| TEST at 2024-12-30T00:00:00Z (equity 10,209.68) | - | - | none | No active adaptations. | - |

Reproduce the baseline TEST row with `python -m research.trendbot.journal_rules --journal research/results/synthetic_hour_edge_s1/journals/base_test.csv --equity 10209.68 --now 2024-12-30T00:00:00Z --backtest-journal`.

## 10. Journals and human review packs

Trade journals (`journal.write_journal`, one CSV per window) for the 4 pre-registered candidates that ran, the variants whose TRAIN and TEST columns this report shows in full. The other discovery variants appear only as one context row each in section 4, so no journal is kept for them (the same command regenerates them). TEST trade ids are offset past the TRAIN ids, so ids are unique across a variant's two journals and its review pack.

| variant | TRAIN journal | TEST journal |
|---|---|---|
| `base` | [journals/base_train.csv](journals/base_train.csv) | [journals/base_test.csv](journals/base_test.csv) |
| `rr3_vol2_rsi50-70` | [journals/rr3_vol2_rsi50-70_train.csv](journals/rr3_vol2_rsi50-70_train.csv) | [journals/rr3_vol2_rsi50-70_test.csv](journals/rr3_vol2_rsi50-70_test.csv) |
| `base+ml` | [journals/base_plus_ml_train.csv](journals/base_plus_ml_train.csv) | [journals/base_plus_ml_test.csv](journals/base_plus_ml_test.csv) |
| `base+guard` | [journals/base_plus_guard_train.csv](journals/base_plus_guard_train.csv) | [journals/base_plus_guard_test.csv](journals/base_plus_guard_test.csv) |

Human review packs (`review_sheet.write_review_pack`, TRAIN and TEST):

- `base`: [review_base/trades_review.md](review_base/trades_review.md) and [review_base/trades_review.csv](review_base/trades_review.csv)
- `rr3_vol2_rsi50-70`: [review_selected_rr3_vol2_rsi50-70/trades_review.md](review_selected_rr3_vol2_rsi50-70/trades_review.md) and [review_selected_rr3_vol2_rsi50-70/trades_review.csv](review_selected_rr3_vol2_rsi50-70/trades_review.csv)
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
| `base` | NO-EDGE | 100 | -0.044 | 60 | +0.050 | yes | `synthetic:hour_edge:1` | `9be9d6da6ad86d85` | `none (no ML layer)` | PASS | BLOCKED by ADOPT_provenance, ADOPT_walk_forward |
| `rr3_vol2_rsi50-70` | UNTESTED | 72 | +0.203 | 39 | +0.436 | yes | `synthetic:hour_edge:1` | `5cbe1d74d6aa0330` | `none (no ML layer)` | PASS | BLOCKED by ADOPT_provenance, ADOPT_walk_forward |
| `base+ml` | UNTESTED | 69 | +0.299 | 44 | +0.295 | yes | `synthetic:hour_edge:1` | `9be9d6da6ad86d85` | `b38d6af2d40d7fa6` | PASS | BLOCKED by ADOPT_provenance, ADOPT_walk_forward |
| `base+guard` | NO-EDGE | 111 | -0.057 | 61 | +0.082 | yes | `synthetic:hour_edge:1` | `2c5274264328eeee` | `none (no ML layer)` | PASS | BLOCKED by ADOPT_provenance, ADOPT_walk_forward |

- `base`: record [adoption_base.json](adoption_base.json).
  - `--stage WALK_FORWARD`: PASS
    Check: `python -m research.trendbot.adoption check --record research/results/synthetic_hour_edge_s1/adoption_base.json --stage WALK_FORWARD`
  - `--stage HUMAN_REVIEW`: BLOCKED: [ADOPT_provenance] provenance is 'synthetic:hour_edge:1': a synthetic world only verifies the harness and is never evidence about real markets, so it may not go past WALK_FORWARD. [ADOPT_provenance] data_files is missing or empty, so the market data behind the backtest is not bound to this record by sha256. [ADOPT_walk_forward] walk_forward.label is 'NO-EDGE' (the train window showed no positive expectancy to validate), and only ROBUST may proceed.
    Check: `python -m research.trendbot.adoption check --record research/results/synthetic_hour_edge_s1/adoption_base.json --stage HUMAN_REVIEW`
- `rr3_vol2_rsi50-70`: record [adoption_rr3_vol2_rsi50-70.json](adoption_rr3_vol2_rsi50-70.json), [config_rr3_vol2_rsi50-70.json](config_rr3_vol2_rsi50-70.json).
  - `--stage WALK_FORWARD`: PASS
    Check: `python -m research.trendbot.adoption check --record research/results/synthetic_hour_edge_s1/adoption_rr3_vol2_rsi50-70.json --stage WALK_FORWARD --config research/results/synthetic_hour_edge_s1/config_rr3_vol2_rsi50-70.json`
  - `--stage HUMAN_REVIEW`: BLOCKED: [ADOPT_provenance] provenance is 'synthetic:hour_edge:1': a synthetic world only verifies the harness and is never evidence about real markets, so it may not go past WALK_FORWARD. [ADOPT_provenance] data_files is missing or empty, so the market data behind the backtest is not bound to this record by sha256. [ADOPT_walk_forward] walk_forward.label is 'UNTESTED' (too few trades, or a test expectancy not distinguishable from zero), and only ROBUST may proceed.
    Check: `python -m research.trendbot.adoption check --record research/results/synthetic_hour_edge_s1/adoption_rr3_vol2_rsi50-70.json --stage HUMAN_REVIEW --config research/results/synthetic_hour_edge_s1/config_rr3_vol2_rsi50-70.json`
- `base+ml`: record [adoption_base_plus_ml.json](adoption_base_plus_ml.json), [model_base_plus_ml.json](model_base_plus_ml.json).
  - `--stage WALK_FORWARD`: PASS
    Check: `python -m research.trendbot.adoption check --record research/results/synthetic_hour_edge_s1/adoption_base_plus_ml.json --stage WALK_FORWARD --model-fingerprint b38d6af2d40d7fa65a3bd7386a4c4504718409b0770eaa6daac3b85a8ddf8b71`
  - `--stage HUMAN_REVIEW`: BLOCKED: [ADOPT_provenance] provenance is 'synthetic:hour_edge:1': a synthetic world only verifies the harness and is never evidence about real markets, so it may not go past WALK_FORWARD. [ADOPT_provenance] data_files is missing or empty, so the market data behind the backtest is not bound to this record by sha256. [ADOPT_walk_forward] walk_forward.label is 'UNTESTED' (too few trades, or a test expectancy not distinguishable from zero), and only ROBUST may proceed.
    Check: `python -m research.trendbot.adoption check --record research/results/synthetic_hour_edge_s1/adoption_base_plus_ml.json --stage HUMAN_REVIEW --model-fingerprint b38d6af2d40d7fa65a3bd7386a4c4504718409b0770eaa6daac3b85a8ddf8b71`
- `base+guard`: record [adoption_base_plus_guard.json](adoption_base_plus_guard.json), [config_base_plus_guard.json](config_base_plus_guard.json).
  - `--stage WALK_FORWARD`: PASS
    Check: `python -m research.trendbot.adoption check --record research/results/synthetic_hour_edge_s1/adoption_base_plus_guard.json --stage WALK_FORWARD --config research/results/synthetic_hour_edge_s1/config_base_plus_guard.json`
  - `--stage HUMAN_REVIEW`: BLOCKED: [ADOPT_provenance] provenance is 'synthetic:hour_edge:1': a synthetic world only verifies the harness and is never evidence about real markets, so it may not go past WALK_FORWARD. [ADOPT_provenance] data_files is missing or empty, so the market data behind the backtest is not bound to this record by sha256. [ADOPT_walk_forward] walk_forward.label is 'NO-EDGE' (the train window showed no positive expectancy to validate), and only ROBUST may proceed.
    Check: `python -m research.trendbot.adoption check --record research/results/synthetic_hour_edge_s1/adoption_base_plus_guard.json --stage HUMAN_REVIEW --config research/results/synthetic_hour_edge_s1/config_base_plus_guard.json`

The ML layer `base+ml` is adopted as a (config, model) pair: its record pins the config fingerprint AND the fitted model's fingerprint `b38d6af2d40d7fa65a3bd7386a4c4504718409b0770eaa6daac3b85a8ddf8b71` (`MLFilter.fingerprint()`). The model itself is [model_base_plus_ml.json](model_base_plus_ml.json) (`MLFilter.to_json()`), which the live bot loads with `MLFilter.from_json(text, expected_fingerprint=<the record's model_fingerprint>)`: loading raises if the file does not hash to that fingerprint. **Refitting the model (new TRAIN data, a later split, a different l2) changes the fingerprint, so a refitted model is a NEW candidate and restarts the adoption path at BACKTEST.** `adoption check` blocks (`ADOPT_fingerprint`) a model whose fingerprint differs from the recorded one, and any `+ml` record without one.

A `config_<variant>.json` file (the `StrategyConfig` overrides versus the defaults, e.g. costs, discovery parameters or `expectancy_guard`) is written whenever the tested config is not the default one; the check needs it as `--config`. The regime-OFF variant is test-only and can never be adopted.

## 12. Risk disclaimer

This is a research and testing tool, not financial advice and not a recommendation to trade. Backtests and synthetic worlds are simplified models; past or simulated results do not predict future results. Crypto trading can result in the total loss of the capital used.
