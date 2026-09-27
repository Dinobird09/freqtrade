# Walk-forward research report: synthetic world `planted` seed 1, STOP-FILL STRESS k = 0.5 (test-only)

## 1. No win rate is promised or targeted

This report does NOT promise, target or optimise a win rate, and nothing in this package selects on one. A win rate is meaningless without the reward:risk it was earned at: with the mandatory minimum of 2:1 a strategy breaks even at about 33% winners before costs, while a 90% win rate with a 1:9 payoff still loses money. A backtest win rate near 90% is evidence of curve-fitting or look-ahead, not of skill. The target metric is POSITIVE EXPECTANCY (average R per trade, net of fees and slippage) that survives a 70/30 chronological walk-forward with controlled drawdown, with every trade planned at reward:risk >= 2:1. Win rate appears below only as context and is labelled 'context only' wherever it is shown.

## 2. Data provenance

- Source: **SYNTHETIC** world `planted`, seed 1, 6 years of 4H candles (13140 per pair) from 2019-01-01T00:00:00Z, pairs BTC/USDT, ETH/USDT, BNB/USDT (`synthetic.generate_world`); adoption provenance `synthetic:planted:1`.
- Ground truth: after every up-closing volume-spike candle the next 12 candles get extra positive drift (0.7 sigma per candle) over the WHOLE history; the drift is offset elsewhere, so buy-and-hold gains nothing from it.
- What the ground truth predicts: the base rules (which require a volume spike) should show positive expectancy on TRAIN and TEST; ROBUST is the correct label when the TEST sample is large enough.
- Qualifying triggers planted per pair (before / after the 70% split): BTC/USDT 342 / 147, ETH/USDT 333 / 134, BNB/USDT 319 / 123.
- **Synthetic results are verification of the methodology, not evidence about real markets.** No real BTC/ETH/BNB data was used in this run.
- News calendar loaded: yes (synthetic, 273 events: high macro 147, high regulatory 24, high unlock 6, medium bnb_burn 24, medium launchpool 72). The events have no price impact; they exercise R5 only.
- Walk-forward split: 2023-03-14T00:00:00Z = 70% of the common time range of all pairs (TRAIN = candles that CLOSED by it, TEST = signal candles at or after it, up to 2024-12-30T00:00:00Z). TEST starts with fresh equity and fresh circuit breakers; indicators warm up on earlier candles only.
- Statistics (CONTRACT.md v3 C4/C5, v4 D1/D7): 4000 bootstrap resamples (seed 7) for the iid and the calendar-month block bootstrap; m = 4 pre-registered candidates, alpha 0.05. ROBUST requires at least 30 trades in each window, TRAIN and TEST avg R > 0, and a one-sided 98.75% lower bound of the TEST mean above zero for BOTH an iid and a calendar-month block bootstrap (the more conservative is used): alpha 0.05 is split over the m = 4 pre-registered candidates (base, discovery-selected, base+ml, base+guard), so when none has an edge the chance that ANY is called ROBUST is at most about 5%; a positive TEST mean that fails only the bound is UNTESTED (positive but not distinguishable from zero after multiplicity correction); the rule was fixed in advance and is never tuned on TEST outcomes.
- Drawdown (CONTRACT.md v3 C5, cap revised by v4 D7): every result reports the realised (closed-trade) and the mark-to-market (open positions valued at each 4H close) max drawdown; dd_ok = TEST mark-to-market max drawdown <= min(15%, the 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length, in percent of equity at the risk each trade took). Why the cap: the 15% cap is about 15 consecutive full-size losses at the mandated 1% cluster risk budget; at the 2:1 break-even win probability p* = 1/3 such a streak has probability (2/3)^15 = 0.23%, so a TEST drawdown beyond it is inconsistent with even a break-even strategy at the mandated risk (the TRAIN-bootstrap p95 usually binds first; the 3% weekly-loss halt limits how fast a drawdown accrues, not how deep it goes).

| pair | candles | TRAIN candles (closed by the split) | TEST candles (from the split) | first candle (UTC) | last candle (UTC) | gaps |
|---|---:|---:|---:|---|---|---|
| BTC/USDT | 13140 | 9198 | 3942 | 2019-01-01T00:00:00Z | 2024-12-29T20:00:00Z | 0 |
| ETH/USDT | 13140 | 9198 | 3942 | 2019-01-01T00:00:00Z | 2024-12-29T20:00:00Z | 0 |
| BNB/USDT | 13140 | 9198 | 3942 | 2019-01-01T00:00:00Z | 2024-12-29T20:00:00Z | 0 |

- **Costs used in every backtest:** fee 0.100% of notional per side (the default: Binance spot taker, no discounts) (`--fee-rate 0.001`), charged on the entry AND on the exit; slippage 0.05% (`--slippage-pct 0.05`) against the trade on market fills (entries and stop exits; take-profit limit exits get none); exchange `binance` (`--exchange-id`; `EXCHANGE:binance` news events block every pair). Starting capital 10,000 per window.
- Cost-aware sizing and targets (CONTRACT.md v2 A1): the planned risk is the ALL-IN loss at the stop (the stop fill after slippage plus both fees), so a clean stop is exactly -1R and the target is placed so that a take-profit nets exactly +2R after fees; the target's price distance is therefore more than 2x the stop distance. Only a gap through the stop loses more than 1R.
- Coinbase Advanced Trade taker fees at low volume tiers are several times Binance's, so a Coinbase run must pass the account's real tier with `--fee-rate` (a fraction of notional per side: 0.006 = 0.6%) and `--exchange-id coinbase`; the default costs would understate them.
- **Stop-fill STRESS model (CONTRACT v4 D4), k = 0.5:** every non-gap stop fills at stop - 0.5 x (stop - exit-candle low), then slippage, instead of at the stop, so a stopped trade can lose MORE than 1R even without a gap (sizing still plans the loss at the stop). Stress configs are test-only (`is_test_only`): nothing in this run is adoptable, gets an adoption record or counts as a holdout look.
- Measured on the baseline's TRAIN and TEST trades: fees alone cost 0.06R per trade on average (median stop distance 3.76% of the entry price), so the expectancy below is only as good as the fee rate above.

## 3. Baseline: TRAIN vs TEST

Default mandate config `rr2_vol1.5_rsi50-70_wick0.5` (variant `base`). TRAIN window start .. 2023-03-14T00:00:00Z; TEST window 2023-03-14T00:00:00Z .. 2024-12-30T00:00:00Z.

| metric | TRAIN | TEST |
|---|---:|---:|
| trades (n) | 115 | 51 |
| avg R (expectancy, the target metric) | +0.251 | +0.346 |
| iid bootstrap 90% CI of avg R | [+0.020, +0.475] | [-0.015, +0.711] |
| calendar-month block bootstrap 90% CI of avg R | [+0.004, +0.484] (49 months) | [-0.060, +0.746] (20 months) |
| one-sided lower bound of avg R at 1 - alpha/m, iid / block | -0.069 / -0.090 (98.75%) | -0.144 / -0.204 (98.75%) |
| adjusted lower bound used by the label (the smaller) | -0.090 | -0.204 |
| t-stat of avg R | +1.76 | +1.56 |
| total R | +28.88 | +17.64 |
| profit factor | 1.50 | 1.58 |
| max drawdown % realised (closed trades) | 8.00% | 5.34% |
| max drawdown % mark-to-market (4H closes) | 8.05% | 5.47% |
| max drawdown R (closed trades) | 9.93 | 6.67 |
| win rate (context only, never a target) | 43.5% | 47.1% |
| exits SL / TP / END | 65 / 49 / 1 | 27 / 24 / 0 |
| avg hold (h) | 112.1 | 99.3 |

**Label: UNTESTED.** Test expectancy +0.346R (n=51) is positive but not distinguishable from zero after multiplicity correction: the one-sided 98.75% lower bound of the test mean (alpha 0.05 split over m=4 pre-registered candidates) is -0.144R by the iid bootstrap and -0.204R by the calendar-month block bootstrap (20 months, 4000 resamples each), and the more conservative -0.204R is not above zero.

Drawdown check (CONTRACT v3 C5 / v4 D7; dd_ok = TEST mark-to-market max drawdown <= min(15%, 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length); the 15% cap is about 15 consecutive full-size losses, a streak of probability (2/3)^15 = 0.23% at the 2:1 break-even win probability p* = 1/3): **passed**. Test mark-to-market max drawdown 5.47% (realised closed-trade 5.34% (6.67R)) is within the limit 10.78% = min(15%, 95th percentile 10.78% of max drawdown over TRAIN trade sequences bootstrapped at the TEST length of 51). For comparison, TRAIN max drawdown: realised 8.00%, mark-to-market 8.05%.

## 4. Strategy discovery: TRAIN vs TEST

K = 13 variants tried: 12 legal tightenings (reward:risk {2, 2.5, 3} x volume multiple {1.5, 2} x RSI window {50-70, 55-70}) plus 1 explicit test-only variant with the R4 regime filter OFF, which is reported but can never be selected or adopted.

Selection rule: highest TRAIN t-stat among selectable variants with >= 30 TRAIN trades. rr3_vol2_rsi50-70_wick0.5 has the highest TRAIN t-stat (+3.36, avg +0.798R over 73 trades) among 12 eligible variants; chosen on TRAIN only, before any TEST backtest was run.

Order of operations actually run: TRAIN backtests: 13 variants, candles closed by the split only -> selection recorded: rr3_vol2_rsi50-70_wick0.5 -> TEST backtests: 13 variants, reported side by side only.

Only the selected variant is a pre-registered candidate (one of the m TEST looks of section 6). Every other row, including the regime-OFF test variant, is shown as `context: <label>, not judged`: picking one of them because of its TEST numbers would be selection on TEST. Context variants are not adoptable: they get no adoption record and no holdout-ledger line.

| variant | status | TRAIN n | TRAIN avg R | TRAIN 90% CI | TRAIN t | TEST n | TEST avg R | TEST 90% CI | TEST adjusted LB | TEST max DD % realised / MTM | label |
|---|---|---:|---:|---|---:|---:|---:|---|---:|---|---|
| `rr2_vol1.5_rsi50-70_wick0.5` | selectable | 115 | +0.251 | [+0.020, +0.475] | +1.76 | 51 | +0.346 | [-0.015, +0.711] | -0.204 | 5.34% / 5.47% | context: UNTESTED, not judged |
| `rr2_vol1.5_rsi55-70_wick0.5` | selectable | 106 | +0.277 | [+0.040, +0.523] | +1.86 | 52 | +0.260 | [-0.098, +0.620] | -0.286 | 5.34% / 5.44% | context: UNTESTED, not judged |
| `rr2_vol2_rsi50-70_wick0.5` | selectable | 94 | +0.445 | [+0.195, +0.696] | +2.79 | 39 | +0.638 | [+0.230, +1.041] | +0.076 | 4.38% / 5.80% | context: ROBUST, not judged |
| `rr2_vol2_rsi55-70_wick0.5` | selectable | 86 | +0.480 | [+0.210, +0.740] | +2.88 | 37 | +0.650 | [+0.230, +1.064] | +0.045 | 4.38% / 5.80% | context: ROBUST, not judged |
| `rr2.5_vol1.5_rsi50-70_wick0.5` | selectable | 104 | +0.319 | [+0.041, +0.596] | +1.86 | 45 | +0.409 | [-0.057, +0.872] | -0.180 | 4.87% / 5.91% | context: UNTESTED, not judged |
| `rr2.5_vol1.5_rsi55-70_wick0.5` | selectable | 95 | +0.338 | [+0.044, +0.624] | +1.88 | 45 | +0.330 | [-0.094, +0.792] | -0.261 | 4.87% / 5.91% | context: UNTESTED, not judged |
| `rr2.5_vol2_rsi50-70_wick0.5` | selectable | 87 | +0.505 | [+0.177, +0.810] | +2.64 | 34 | +0.803 | [+0.284, +1.328] | +0.112 | 4.38% / 5.80% | context: ROBUST, not judged |
| `rr2.5_vol2_rsi55-70_wick0.5` | selectable | 79 | +0.530 | [+0.204, +0.866] | +2.64 | 32 | +0.809 | [+0.260, +1.365] | +0.113 | 4.38% / 5.80% | context: ROBUST, not judged |
| `rr3_vol1.5_rsi50-70_wick0.5` | selectable | 88 | +0.333 | [+0.013, +0.672] | +1.61 | 43 | +0.324 | [-0.159, +0.810] | -0.339 | 4.87% / 6.34% | context: UNTESTED, not judged |
| `rr3_vol1.5_rsi55-70_wick0.5` | selectable | 83 | +0.319 | [-0.025, +0.665] | +1.50 | 42 | +0.260 | [-0.231, +0.757] | -0.371 | 4.87% / 6.34% | context: UNTESTED, not judged |
| `rr3_vol2_rsi50-70_wick0.5` | **SELECTED** | 73 | +0.798 | [+0.415, +1.175] | +3.36 | 35 | +0.773 | [+0.186, +1.358] | -0.019 | 4.38% / 5.80% | UNTESTED |
| `rr3_vol2_rsi55-70_wick0.5` | selectable | 68 | +0.815 | [+0.405, +1.209] | +3.31 | 33 | +0.763 | [+0.156, +1.358] | +0.009 | 4.38% / 5.80% | context: ROBUST, not judged |
| `rr2_vol1.5_rsi50-70_regimeOFF_wick0.5` | TEST-ONLY (regime OFF, never selectable) | 157 | +0.189 | [-0.006, +0.386] | +1.57 | 67 | +0.349 | [+0.046, +0.665] | -0.070 | 6.97% / 7.81% | context: UNTESTED, not judged |

**Re-selecting a variant on these TEST numbers would turn TEST into TRAIN: the selection was fixed on TRAIN before any TEST backtest ran, and it is not revisited.**

The TRAIN column of the selected variant `rr3_vol2_rsi50-70_wick0.5` is biased upward by the selection itself (it is the best of 12); only its TEST column is an out-of-sample test.

**Label: UNTESTED.** Test expectancy +0.773R (n=35) is positive but not distinguishable from zero after multiplicity correction: the one-sided 98.75% lower bound of the test mean (alpha 0.05 split over m=4 pre-registered candidates) is -0.019R by the iid bootstrap and +0.072R by the calendar-month block bootstrap (18 months, 4000 resamples each), and the more conservative -0.019R is not above zero.

Drawdown check (CONTRACT v3 C5 / v4 D7; dd_ok = TEST mark-to-market max drawdown <= min(15%, 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length); the 15% cap is about 15 consecutive full-size losses, a streak of probability (2/3)^15 = 0.23% at the 2:1 break-even win probability p* = 1/3): **passed**. Test mark-to-market max drawdown 5.80% (realised closed-trade 4.38% (4.80R)) is within the limit 7.90% = min(15%, 95th percentile 7.90% of max drawdown over TRAIN trade sequences bootstrapped at the TEST length of 35). For comparison, TRAIN max drawdown: realised 6.55%, mark-to-market 7.34%.

## 5. Entry layers (ML filter, expectancy guard): TRAIN vs TEST

Two pre-registered layers on the baseline rules. `base+ml` adds `L_ml_filter`, a logistic regression on 6 features (rsi, vol_ratio, ema_gap_pct, dist_regime_pct, hour_sin, hour_cos), fitted ONLY on purged TRAIN candidates and applied unchanged to TEST, so its full-TRAIN numbers are IN-SAMPLE. `base+guard` switches on `L_expectancy_guard` (`expectancy_guard=True`: a pair's risk is multiplied by 0.5 while its last 20 closed trades average below 0R); it is a fixed rule over the journal, nothing is fitted, and it needs the same walk-forward evidence as any other variant.

An entry layer can only VETO an entry that passed every mandatory rule; it never approves an entry a rule denies. A veto can free R6 budget or change R9 state, admitting other rule-compliant trades, so a layer's journal is not a subset of the base journal: both directions are counted below, matched by (pair, signal time).

A fitted layer's TRAIN gate is out of sample (CONTRACT v4 D8): its purged TRAIN candidates are split 70/30 in time order, the layer is fitted on the first 70% (purged at the inner boundary) and that inner filter is backtested on the rest of TRAIN. That out-of-sample TRAIN window is what the label judges (NO-EDGE gate, trade counts, the TRAIN drawdown bootstrap) and what the adoption record binds as the TRAIN journal; the in-sample full-TRAIN figure is context only. The model applied to TEST is still fitted on ALL purged TRAIN candidates.

| layer | window (TRAIN / TEST) | signals vetoed | entries at reduced risk (guard x < 1) | base trades absent from the layer journal | layer trades absent from the base journal | trades in both | base trades | layer trades |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `base+ml` | TRAIN | 68 | 0 | 50 | 22 | 65 | 115 | 87 |
| `base+ml` | TEST | 29 | 0 | 20 | 2 | 31 | 51 | 33 |
| `base+guard` | TRAIN | 0 | 8 | 0 | 0 | 115 | 115 | 115 |
| `base+guard` | TEST | 0 | 0 | 0 | 0 | 51 | 51 | 51 |

- `base+ml` vs `base`: TRAIN avg R +0.251 -> +0.530 (n 115 -> 87, in-sample; out-of-sample gate +0.329 (n 17)) | TEST avg R +0.346 -> +0.192 (n 51 -> 33).
- `base+guard` vs `base`: TRAIN avg R +0.251 -> +0.251 (n 115 -> 115) | TEST avg R +0.346 -> +0.346 (n 51 -> 51).

Logistic filter (l2=1) fitted on 268 TRAIN candidates (TRAIN base win rate 39.6%, context only) vetoes every rule-passing signal whose predicted win probability is at or below the break-even 0.352.

- TRAIN candidates enumerated (purged: outcome resolved by the split): 268; used for the final fit: 268 (0 skipped for missing features); TRAIN candidate win share 39.6% (context only).
- **Out-of-sample TRAIN gate (CONTRACT v4 D8):** the 268 purged TRAIN candidates were split 70/30 in time order at 2022-02-13T00:00:00Z; the inner model was fitted on 185 candidates (2 purged: signalled before the inner boundary but resolved after it); inner model fingerprint `42c02403071ed7be`, and backtested from 2022-02-13T00:00:00Z to the split: n=17, avg +0.329R. That window is the TRAIN the label judges and the TRAIN journal the adoption record binds; the in-sample full-TRAIN figure (n=87, avg +0.530R) is context only.
- Threshold p* = break-even win probability from the TRAIN avg win +2.00R and avg loss -1.08R: p* = 0.352 (no threshold search).
- L2 penalty 1 (fixed, not tuned); intercept -0.472.
- Model fingerprint (sha256 of the canonical JSON of features, TRAIN means/stds, intercept, coefficients, threshold and l2): `6a53f5767fda53f731cefe7c2b6e3fc56efd94e287a281b714f272ea0900df7a`. It is pinned in the ML adoption record (section 11); `MLFilter.from_json` re-verifies it when the live bot loads the model file.

| feature | standardised coefficient, fitted on TRAIN only and applied unchanged to TEST (log-odds per TRAIN s.d.) |
|---|---:|
| `rsi` | +0.069 |
| `vol_ratio` | +0.309 |
| `ema_gap_pct` | -0.569 |
| `dist_regime_pct` | -0.172 |
| `hour_sin` | +0.198 |
| `hour_cos` | +0.240 |

| metric | base TRAIN | base+ml TRAIN (in-sample) | base+ml TRAIN (out-of-sample inner split) | base+guard TRAIN | base TEST | base+ml TEST | base+guard TEST |
|---|---:|---:|---:|---:|---:|---:|---:|
| trades (n) | 115 | 87 | 17 | 115 | 51 | 33 | 51 |
| avg R (expectancy, the target metric) | +0.251 | +0.530 | +0.329 | +0.251 | +0.346 | +0.192 | +0.346 |
| iid bootstrap 90% CI of avg R | [+0.020, +0.475] | [+0.259, +0.793] | [-0.216, +0.924] | [+0.020, +0.475] | [-0.015, +0.711] | [-0.266, +0.657] | [-0.015, +0.711] |
| calendar-month block bootstrap 90% CI of avg R | [+0.004, +0.484] (49 months) | [+0.222, +0.830] (43 months) | [-0.334, +1.206] (11 months) | [+0.004, +0.484] (49 months) | [-0.060, +0.746] (20 months) | [-0.231, +0.590] (17 months) | [-0.060, +0.746] (20 months) |
| one-sided lower bound of avg R at 1 - alpha/m, iid / block | -0.069 / -0.090 (98.75%) | +0.162 / +0.116 (98.75%) | -0.401 / -0.536 (98.75%) | -0.069 / -0.090 (98.75%) | -0.144 / -0.204 (98.75%) | -0.400 / -0.402 (98.75%) | -0.144 / -0.204 (98.75%) |
| adjusted lower bound used by the label (the smaller) | -0.090 | +0.116 | -0.536 | -0.090 | -0.204 | -0.402 | -0.204 |
| t-stat of avg R | +1.76 | +3.20 | +0.89 | +1.76 | +1.56 | +0.70 | +1.56 |
| total R | +28.88 | +46.15 | +5.60 | +28.88 | +17.64 | +6.32 | +17.64 |
| profit factor | 1.50 | 1.94 | 1.46 | 1.51 | 1.58 | 1.32 | 1.58 |
| max drawdown % realised (closed trades) | 8.00% | 4.23% | 4.08% | 7.24% | 5.34% | 5.47% | 5.34% |
| max drawdown % mark-to-market (4H closes) | 8.05% | 4.97% | 5.89% | 7.42% | 5.47% | 5.47% | 5.47% |
| max drawdown R (closed trades) | 9.93 | 4.30 | 4.15 | 9.93 | 6.67 | 5.63 | 6.67 |
| win rate (context only, never a target) | 43.5% | 52.9% | 47.1% | 43.5% | 47.1% | 42.4% | 47.1% |
| exits SL / TP / END | 65 / 49 / 1 | 41 / 45 / 1 | 9 / 7 / 1 | 65 / 49 / 1 | 27 / 24 / 0 | 19 / 14 / 0 | 27 / 24 / 0 |
| avg hold (h) | 112.1 | 114.8 | 155.5 | 112.1 | 99.3 | 117.5 | 99.3 |

`base+ml`:

**Label: UNTESTED.** TRAIN here is the out-of-sample inner split (CONTRACT v4 D8: the layer refitted on 185 of the first 70% of the 268 purged TRAIN candidates, purged at the inner boundary 2022-02-13T00:00:00Z, and backtested from there to the split; the in-sample full-TRAIN figure is context only). Too few trades to judge: train n=17 (need 30).

Drawdown check (CONTRACT v3 C5 / v4 D7; dd_ok = TEST mark-to-market max drawdown <= min(15%, 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length); the 15% cap is about 15 consecutive full-size losses, a streak of probability (2/3)^15 = 0.23% at the 2:1 break-even win probability p* = 1/3): **passed**. Test mark-to-market max drawdown 5.47% (realised closed-trade 5.47% (5.63R)) is within the limit 10.61% = min(15%, 95th percentile 10.61% of max drawdown over TRAIN trade sequences bootstrapped at the TEST length of 33). For comparison, out-of-sample gate max drawdown: realised 4.08%, mark-to-market 5.89% (in-sample TRAIN max drawdown: realised 4.23%, mark-to-market 4.97%).

`base+guard`:

**Label: UNTESTED.** Test expectancy +0.346R (n=51) is positive but not distinguishable from zero after multiplicity correction: the one-sided 98.75% lower bound of the test mean (alpha 0.05 split over m=4 pre-registered candidates) is -0.144R by the iid bootstrap and -0.204R by the calendar-month block bootstrap (20 months, 4000 resamples each), and the more conservative -0.204R is not above zero.

Drawdown check (CONTRACT v3 C5 / v4 D7; dd_ok = TEST mark-to-market max drawdown <= min(15%, 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length); the 15% cap is about 15 consecutive full-size losses, a streak of probability (2/3)^15 = 0.23% at the 2:1 break-even win probability p* = 1/3): **passed**. Test mark-to-market max drawdown 5.47% (realised closed-trade 5.34% (6.67R)) is within the limit 10.70% = min(15%, 95th percentile 10.70% of max drawdown over TRAIN trade sequences bootstrapped at the TEST length of 51). For comparison, TRAIN max drawdown: realised 7.24%, mark-to-market 7.42%.

LightGBM, other gradient boosting and neural networks are NOT justified at this sample size (268 TRAIN candidates, 6 features): a flexible model would memorise noise, so only a fixed-penalty logistic regression with a break-even threshold is allowed.

## 6. Verdict

Candidates judged (pre-registered, one TEST look each, m = 4): baseline `base`, discovery-selected `rr3_vol2_rsi50-70_wick0.5`, ML layer `base+ml`, expectancy guard `base+guard`. Other grid variants are excluded because choosing among them by TEST numbers would be selection on TEST. ROBUST requires at least 30 trades in each window, TRAIN and TEST avg R > 0, and a one-sided 98.75% lower bound of the TEST mean above zero for BOTH an iid and a calendar-month block bootstrap (the more conservative is used): alpha 0.05 is split over the m = 4 pre-registered candidates (base, discovery-selected, base+ml, base+guard), so when none has an edge the chance that ANY is called ROBUST is at most about 5%; a positive TEST mean that fails only the bound is UNTESTED (positive but not distinguishable from zero after multiplicity correction); the rule was fixed in advance and is never tuned on TEST outcomes. The TEST gate is reached when the judged TRAIN avg R > 0 and both windows hold >= 30 trades; the drawdown check is reported beside the label. The judged TRAIN of `base+ml` is its out-of-sample gate (CONTRACT v4 D8, section 5).

| candidate | TRAIN n (judged) | TRAIN avg R (judged) | TEST n | TEST avg R | TEST iid / block LB (1 - alpha/m) | reached the TEST gate | label | TEST MTM max DD vs limit | dd_ok |
|---|---:|---:|---:|---:|---|---|---|---|---|
| baseline `base` | 115 | +0.251 | 51 | +0.346 | -0.144 / -0.204 | yes | UNTESTED | 5.47% vs 10.78% | yes |
| discovery-selected `rr3_vol2_rsi50-70_wick0.5` | 73 | +0.798 | 35 | +0.773 | -0.019 / +0.072 | yes | UNTESTED | 5.80% vs 7.90% | yes |
| ML layer `base+ml` | 17 | +0.329 | 33 | +0.192 | -0.400 / -0.402 | no | UNTESTED | 5.47% vs 10.61% | yes |
| expectancy guard `base+guard` | 115 | +0.251 | 51 | +0.346 | -0.144 / -0.204 | yes | UNTESTED | 5.47% vs 10.70% | yes |

No robust result found. Pre-registered candidates, TRAIN | TEST: baseline `base` UNTESTED (TRAIN +0.251R n=115 | TEST +0.346R n=51); discovery-selected `rr3_vol2_rsi50-70_wick0.5` UNTESTED (TRAIN +0.798R n=73 | TEST +0.773R n=35); ML layer `base+ml` UNTESTED (TRAIN +0.329R n=17 | TEST +0.192R n=33); expectancy guard `base+guard` UNTESTED (TRAIN +0.251R n=115 | TEST +0.346R n=51).

Holdout ledger (CONTRACT v4 D3): `test_looks.jsonl` (append-only JSON lines). This run appended 4 line(s), one per adoptable pre-registered candidate that got a TEST backtest (context discovery variants and test-only configs are never recorded). A look is a DISTINCT (config fingerprint, model fingerprint), so re-running identical candidates is not a new look. `adoption check` blocks (`ADOPT_holdout`) from HUMAN_REVIEW on when a pair's count exceeds m = 4. After a reviewer N or any other revision, a variant needs a TEST window starting at or after the latest test_end_ts of every earlier look; it is never re-run on the same TEST window.

| pair | TRAIN window (UTC) | TEST window (UTC) | cumulative distinct TEST looks on this TEST window | limit m | status |
|---|---|---|---:|---:|---|
| BNB/USDT | start .. 2023-03-14T00:00:00Z | 2023-03-14T00:00:00Z .. 2024-12-30T00:00:00Z | 4 | 4 | within the limit |
| BTC/USDT | start .. 2023-03-14T00:00:00Z | 2023-03-14T00:00:00Z .. 2024-12-30T00:00:00Z | 4 | 4 | within the limit |
| ETH/USDT | start .. 2023-03-14T00:00:00Z | 2023-03-14T00:00:00Z .. 2024-12-30T00:00:00Z | 4 | 4 | within the limit |

**Stop-fill stress (CONTRACT v4 D4), k = 0.5 versus the touch fill model k = 0**, both on this data and split (the k = 0 run is the reference; TRAIN is the judged TRAIN window). Every stressed config is test-only, so this table measures how optimistic the touch fill is, nothing here is adoptable:

| candidate | variant k=0 / k=0.5 | same config apart from k | judged TRAIN avg R k=0 | judged TRAIN avg R k=0.5 | TRAIN R shift | TEST avg R k=0 | TEST avg R k=0.5 | TEST R shift | label k=0 | label k=0.5 | label changed |
|---|---|---|---:|---:|---:|---:|---:|---:|---|---|---|
| baseline | `base` / `base` | yes | +0.296 (n 115) | +0.251 (n 115) | -0.045 | +0.412 (n 51) | +0.346 (n 51) | -0.066 | UNTESTED | UNTESTED | no |
| discovery-selected | `rr3_vol2_rsi50-70` / `rr3_vol2_rsi50-70_wick0.5` | yes | +0.836 (n 73) | +0.798 (n 73) | -0.038 | +0.829 (n 35) | +0.773 (n 35) | -0.056 | ROBUST | UNTESTED | yes |
| ML layer | `base+ml` / `base+ml` | yes | +0.278 (n 18) | +0.329 (n 17) | +0.052 | +0.235 (n 34) | +0.192 (n 33) | -0.044 | UNTESTED | UNTESTED | no |
| expectancy guard | `base+guard` / `base+guard` | yes | +0.296 (n 115) | +0.251 (n 115) | -0.045 | +0.412 (n 51) | +0.346 (n 51) | -0.066 | UNTESTED | UNTESTED | no |

## 7. Why signals were rejected (decision counts per rule)

Signal candles denied per rule (the FIRST failing rule is counted, so the R1-R4 rows include every candle that was simply not a signal). Exits are never gated. TRAIN is the full TRAIN backtest (in-sample for the ML layer).

| rule | base TRAIN | base TEST | rr3_vol2_rsi50-70_wick0.5 TRAIN | rr3_vol2_rsi50-70_wick0.5 TEST | base+ml TRAIN | base+ml TEST | base+guard TRAIN | base+guard TEST | meaning |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `R1_trend` | 18476 | 7756 | 18476 | 7756 | 18476 | 7756 | 18476 | 7756 | Trend gate: close > EMA9 and close > EMA21 and EMA9 > EMA21 (4H) |
| `R2_momentum` | 3390 | 1684 | 3390 | 1684 | 3390 | 1684 | 3390 | 1684 | Momentum: RSI(14) within [50, 70] on the entry candle |
| `R3_volume` | 5271 | 2193 | 5428 | 2254 | 5271 | 2193 | 5271 | 2193 | Volume: entry-candle volume >= 1.5x the previous-20-candle average |
| `R4_regime` | 176 | 74 | 112 | 52 | 176 | 74 | 176 | 74 | Regime: close > EMA200 (unless explicitly testing it off) |
| `R5_news_blackout` | 9 | 8 | 6 | 5 | 9 | 8 | 9 | 8 | No entries within +/-2h of high-impact news; BNB +/-24h burns/launchpool |
| `R6_correlation_cap` | 156 | 59 | 108 | 39 | 115 | 48 | 156 | 59 | BTC/ETH/BNB share one risk budget; BNB never stacks |
| `R7_position_risk` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Risk <= 1% (BTC/ETH) / 0.5% (BNB); size derived from stop distance |
| `R8_structure_stop` | 1 | 1 | 1 | 1 | 2 | 1 | 1 | 1 | Stop behind a confirmed swing low with a buffer (BNB 0.5-0.8%) |
| `R9_circuit_breaker` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 3 consecutive SLs bench a pair 24h; 7d realized loss limit halts all |
| `L_ml_filter` | 0 | 0 | 0 | 0 | 68 | 29 | 0 | 0 | Layer: logistic-regression veto; it can only veto an entry that passed every mandatory rule and never approves one a rule denies (a veto can free R6 budget or change R9 state, which may admit other rule-compliant trades) |
| `L_expectancy_guard` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Layer: halve a pair's risk while its last-N-trade expectancy < 0 |
| `X_capital` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Execution: not enough free capital / size below minimum |
| candles evaluated | 27594 | 11826 | 27594 | 11826 | 27594 | 11826 | 27594 | 11826 | |
| trades taken | 115 | 51 | 73 | 35 | 87 | 33 | 115 | 51 | |

## 8. Invariant audit of every backtest

`invariants.check_invariants` independently re-derives every mandatory rule (R1-R9, 2:1 minimum, sizing from the stop, exits on the first touch, no data after the window) from each trade list and the candles. A fitted layer's TRAIN column also covers its D8 out-of-sample gate backtest.

| backtest | TRAIN violations | TEST violations |
|---|---:|---:|
| `base` | 0 | 0 |
| `rr2_vol1.5_rsi50-70_wick0.5` | 0 | 0 |
| `rr2_vol1.5_rsi55-70_wick0.5` | 0 | 0 |
| `rr2_vol2_rsi50-70_wick0.5` | 0 | 0 |
| `rr2_vol2_rsi55-70_wick0.5` | 0 | 0 |
| `rr2.5_vol1.5_rsi50-70_wick0.5` | 0 | 0 |
| `rr2.5_vol1.5_rsi55-70_wick0.5` | 0 | 0 |
| `rr2.5_vol2_rsi50-70_wick0.5` | 0 | 0 |
| `rr2.5_vol2_rsi55-70_wick0.5` | 0 | 0 |
| `rr3_vol1.5_rsi50-70_wick0.5` | 0 | 0 |
| `rr3_vol1.5_rsi55-70_wick0.5` | 0 | 0 |
| `rr3_vol2_rsi50-70_wick0.5` | 0 | 0 |
| `rr3_vol2_rsi55-70_wick0.5` | 0 | 0 |
| `rr2_vol1.5_rsi50-70_regimeOFF_wick0.5` | 0 | 0 |
| `base+ml` | 0 | 0 |
| `base+guard` | 0 | 0 |

Result: CLEAN. 0 violations in 33 backtests.

## 9. Journal rules at the end of TRAIN and at the end of TEST

`journal_rules.audit` over each pre-registered candidate's TRAIN journal at the split and its TEST journal at 2024-12-30T00:00:00Z (the end of TEST), each with that window's final equity: every adaptation the live bot would be applying at that moment because of past trades, with the journal rows that caused it. Backtest journals record the OPEN of the exit candle, so each exit counts from exit_ts + 4h, when the exit is certain (`exit_time_uncertainty_ms = cfg.timeframe_ms`, CONTRACT.md v2 A2); a bench therefore lasts at least 24h of real time. A live journal records real fill times and uses 0.

**baseline `base`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 13,209.55) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 11,696.21) | - | - | none | No active adaptations. | - |

**discovery-selected `rr3_vol2_rsi50-70_wick0.5`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 16,541.11) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 12,223.35) | - | - | none | No active adaptations. | - |

**ML layer `base+ml`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 10,451.88) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 10,603.04) | - | - | none | No active adaptations. | - |

**expectancy guard `base+guard`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 13,190.00) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 11,696.21) | - | - | none | No active adaptations. | - |

Reproduce the baseline TEST row with `python -m research.trendbot.journal_rules --journal research/results/stress_k0.5/synthetic_planted_s1/journals/base_test.csv --equity 11696.21 --now 2024-12-30T00:00:00Z --backtest-journal`.

## 10. Journals and human review packs

Trade journals (`journal.write_journal`, one CSV per window) for the 4 pre-registered candidates that ran, the variants whose TRAIN and TEST columns this report shows in full. The other discovery variants appear only as one context row each in section 4, so no journal is kept for them (the same command regenerates them). TEST trade ids are offset past the TRAIN ids, so ids are unique across a variant's two journals and its review pack. The TRAIN journal is the judged TRAIN window (for a fitted layer its D8 out-of-sample gate; its in-sample full-TRAIN journal is kept as context and bound by no record).

| variant | TRAIN journal (judged) | TEST journal | in-sample full-TRAIN journal (context) |
|---|---|---|---|
| `base` | [journals/base_train.csv](journals/base_train.csv) | [journals/base_test.csv](journals/base_test.csv) | - |
| `rr3_vol2_rsi50-70_wick0.5` | [journals/rr3_vol2_rsi50-70_wick0.5_train.csv](journals/rr3_vol2_rsi50-70_wick0.5_train.csv) | [journals/rr3_vol2_rsi50-70_wick0.5_test.csv](journals/rr3_vol2_rsi50-70_wick0.5_test.csv) | - |
| `base+ml` | [journals/base_plus_ml_train.csv](journals/base_plus_ml_train.csv) | [journals/base_plus_ml_test.csv](journals/base_plus_ml_test.csv) | [journals/base_plus_ml_train_insample.csv](journals/base_plus_ml_train_insample.csv) |
| `base+guard` | [journals/base_plus_guard_train.csv](journals/base_plus_guard_train.csv) | [journals/base_plus_guard_test.csv](journals/base_plus_guard_test.csv) | - |

Human review packs (`review_sheet.write_review_pack`, TRAIN and TEST):

- no pack for `base`, `rr3_vol2_rsi50-70_wick0.5`, `base+ml`, `base+guard`: a test-only config (CONTRACT v4 D1, `ADOPT_test_only` blocks it from HUMAN_REVIEW on), so a review pack could never be used; its journals above hold every trade.

## 11. Adoption path and current stage

The only path to real money (enforced by `adoption.py`; no step can be skipped, the promoted config must match the tested config's sha256 fingerprint, and an ML layer's model must match the tested model's fingerprint):

backtest -> walk-forward (ROBUST + drawdown check) -> human review of EVERY trade -> >= 2 weeks on Binance testnet with zero rule violations -> live.

Stages: BACKTEST -> WALK_FORWARD -> HUMAN_REVIEW -> TESTNET -> LIVE. Stage reached by this run: **WALK_FORWARD** (recorded). Next: **HUMAN_REVIEW**.

**No adoption record was written: every config of this run is test-only (stop-fill stress, CONTRACT v4 D4).** Test-only and context variants are not adoptable: they get no promotable record, and `adoption check` blocks any record for one (`ADOPT_test_only` from HUMAN_REVIEW on, `ADOPT_holdout` 'context variant').

## 12. Risk disclaimer

This is a research and testing tool, not financial advice and not a recommendation to trade. Backtests and synthetic worlds are simplified models; past or simulated results do not predict future results. Crypto trading can result in the total loss of the capital used.
