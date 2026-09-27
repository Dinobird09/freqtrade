# Walk-forward research report: synthetic world `hour_edge` seed 1, STOP-FILL STRESS k = 0.5 (test-only)

## 1. No win rate is promised or targeted

This report does NOT promise, target or optimise a win rate, and nothing in this package selects on one. A win rate is meaningless without the reward:risk it was earned at: with the mandatory minimum of 2:1 a strategy breaks even at about 33% winners before costs, while a 90% win rate with a 1:9 payoff still loses money. A backtest win rate near 90% is evidence of curve-fitting or look-ahead, not of skill. The target metric is POSITIVE EXPECTANCY (average R per trade, net of fees and slippage) that survives a 70/30 chronological walk-forward with controlled drawdown, with every trade planned at reward:risk >= 2:1. Win rate appears below only as context and is labelled 'context only' wherever it is shown.

## 2. Data provenance

- Source: **SYNTHETIC** world `hour_edge`, seed 1, 6 years of 4H candles (13140 per pair) from 2019-01-01T00:00:00Z, pairs BTC/USDT, ETH/USDT, BNB/USDT (`synthetic.generate_world`); adoption provenance `synthetic:hour_edge:1`.
- Ground truth: the planted effect exists only for spike candles closing 12:00-20:00 UTC; spikes closing at other hours trigger nothing.
- What the ground truth predicts: the hour-blind base is diluted (weak or no edge); an hour-aware filter fitted on TRAIN has something real to learn and should raise TEST expectancy versus the base.
- Qualifying triggers planted per pair (before / after the 70% split): BTC/USDT 165 / 77, ETH/USDT 153 / 65, BNB/USDT 168 / 63.
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
- Measured on the baseline's TRAIN and TEST trades: fees alone cost 0.06R per trade on average (median stop distance 3.48% of the entry price), so the expectancy below is only as good as the fee rate above.

## 3. Baseline: TRAIN vs TEST

Default mandate config `rr2_vol1.5_rsi50-70_wick0.5` (variant `base`). TRAIN window start .. 2023-03-14T00:00:00Z; TEST window 2023-03-14T00:00:00Z .. 2024-12-30T00:00:00Z.

| metric | TRAIN | TEST |
|---|---:|---:|
| trades (n) | 100 | 60 |
| avg R (expectancy, the target metric) | -0.103 | -0.023 |
| iid bootstrap 90% CI of avg R | [-0.334, +0.136] | [-0.338, +0.293] |
| calendar-month block bootstrap 90% CI of avg R | [-0.338, +0.143] (41 months) | [-0.312, +0.271] (20 months) |
| one-sided lower bound of avg R at 1 - alpha/m, iid / block | -0.408 / -0.423 (98.75%) | -0.441 / -0.422 (98.75%) |
| adjusted lower bound used by the label (the smaller) | -0.423 | -0.441 |
| t-stat of avg R | -0.71 | -0.12 |
| total R | -10.33 | -1.36 |
| profit factor | 0.81 | 0.95 |
| max drawdown % realised (closed trades) | 17.51% | 11.04% |
| max drawdown % mark-to-market (4H closes) | 17.86% | 11.19% |
| max drawdown R (closed trades) | 18.82 | 12.86 |
| win rate (context only, never a target) | 32.0% | 35.0% |
| exits SL / TP / END | 68 / 31 / 1 | 39 / 21 / 0 |
| avg hold (h) | 137.8 | 92.0 |

**Label: NO-EDGE.** Train expectancy is -0.103R over 100 trades, so there is no edge to validate.

Drawdown check (CONTRACT v3 C5 / v4 D7; dd_ok = TEST mark-to-market max drawdown <= min(15%, 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length); the 15% cap is about 15 consecutive full-size losses, a streak of probability (2/3)^15 = 0.23% at the 2:1 break-even win probability p* = 1/3): **passed**. Test mark-to-market max drawdown 11.19% (realised closed-trade 11.04% (12.86R)) is within the limit 15.00% = min(15%, 95th percentile 21.99% of max drawdown over TRAIN trade sequences bootstrapped at the TEST length of 60). For comparison, TRAIN max drawdown: realised 17.51%, mark-to-market 17.86%.

## 4. Strategy discovery: TRAIN vs TEST

K = 13 variants tried: 12 legal tightenings (reward:risk {2, 2.5, 3} x volume multiple {1.5, 2} x RSI window {50-70, 55-70}) plus 1 explicit test-only variant with the R4 regime filter OFF, which is reported but can never be selected or adopted.

Selection rule: highest TRAIN t-stat among selectable variants with >= 30 TRAIN trades. rr3_vol2_rsi50-70_wick0.5 has the highest TRAIN t-stat (+0.67, avg +0.148R over 72 trades) among 12 eligible variants; chosen on TRAIN only, before any TEST backtest was run.

Order of operations actually run: TRAIN backtests: 13 variants, candles closed by the split only -> selection recorded: rr3_vol2_rsi50-70_wick0.5 -> TEST backtests: 13 variants, reported side by side only.

Only the selected variant is a pre-registered candidate (one of the m TEST looks of section 6). Every other row, including the regime-OFF test variant, is shown as `context: <label>, not judged`: picking one of them because of its TEST numbers would be selection on TEST. Context variants are not adoptable: they get no adoption record and no holdout-ledger line.

| variant | status | TRAIN n | TRAIN avg R | TRAIN 90% CI | TRAIN t | TEST n | TEST avg R | TEST 90% CI | TEST adjusted LB | TEST max DD % realised / MTM | label |
|---|---|---:|---:|---|---:|---:|---:|---|---:|---|---|
| `rr2_vol1.5_rsi50-70_wick0.5` | selectable | 100 | -0.103 | [-0.334, +0.136] | -0.71 | 60 | -0.023 | [-0.338, +0.293] | -0.441 | 11.04% / 11.19% | context: NO-EDGE, not judged |
| `rr2_vol1.5_rsi55-70_wick0.5` | selectable | 92 | -0.019 | [-0.270, +0.243] | -0.12 | 58 | +0.013 | [-0.308, +0.336] | -0.419 | 9.75% / 9.86% | context: NO-EDGE, not judged |
| `rr2_vol2_rsi50-70_wick0.5` | selectable | 82 | +0.077 | [-0.186, +0.354] | +0.47 | 44 | +0.088 | [-0.275, +0.452] | -0.403 | 5.53% / 6.04% | context: UNTESTED, not judged |
| `rr2_vol2_rsi55-70_wick0.5` | selectable | 77 | +0.072 | [-0.199, +0.348] | +0.42 | 42 | +0.072 | [-0.300, +0.454] | -0.428 | 5.53% / 6.04% | context: UNTESTED, not judged |
| `rr2.5_vol1.5_rsi50-70_wick0.5` | selectable | 97 | -0.062 | [-0.321, +0.208] | -0.38 | 57 | +0.086 | [-0.287, +0.474] | -0.421 | 9.25% / 9.39% | context: NO-EDGE, not judged |
| `rr2.5_vol1.5_rsi55-70_wick0.5` | selectable | 89 | -0.008 | [-0.287, +0.278] | -0.05 | 56 | +0.107 | [-0.273, +0.493] | -0.371 | 8.76% / 8.76% | context: NO-EDGE, not judged |
| `rr2.5_vol2_rsi50-70_wick0.5` | selectable | 78 | +0.104 | [-0.207, +0.417] | +0.54 | 43 | +0.228 | [-0.202, +0.667] | -0.355 | 5.53% / 6.04% | context: UNTESTED, not judged |
| `rr2.5_vol2_rsi55-70_wick0.5` | selectable | 73 | +0.087 | [-0.225, +0.412] | +0.44 | 42 | +0.259 | [-0.173, +0.709] | -0.336 | 5.53% / 6.04% | context: UNTESTED, not judged |
| `rr3_vol1.5_rsi50-70_wick0.5` | selectable | 87 | -0.116 | [-0.403, +0.188] | -0.62 | 51 | +0.098 | [-0.310, +0.515] | -0.465 | 6.04% / 6.18% | context: NO-EDGE, not judged |
| `rr3_vol1.5_rsi55-70_wick0.5` | selectable | 80 | -0.079 | [-0.391, +0.250] | -0.40 | 50 | +0.122 | [-0.295, +0.555] | -0.439 | 5.89% / 5.89% | context: NO-EDGE, not judged |
| `rr3_vol2_rsi50-70_wick0.5` | **SELECTED** | 72 | +0.148 | [-0.196, +0.513] | +0.67 | 39 | +0.361 | [-0.159, +0.890] | -0.357 | 5.53% / 6.04% | UNTESTED |
| `rr3_vol2_rsi55-70_wick0.5` | selectable | 68 | +0.100 | [-0.259, +0.465] | +0.45 | 38 | +0.399 | [-0.132, +0.941] | -0.336 | 5.53% / 6.04% | context: UNTESTED, not judged |
| `rr2_vol1.5_rsi50-70_regimeOFF_wick0.5` | TEST-ONLY (regime OFF, never selectable) | 153 | +0.172 | [-0.028, +0.374] | +1.40 | 79 | +0.034 | [-0.246, +0.311] | -0.353 | 10.41% / 11.15% | context: UNTESTED, not judged |

**Re-selecting a variant on these TEST numbers would turn TEST into TRAIN: the selection was fixed on TRAIN before any TEST backtest ran, and it is not revisited.**

The TRAIN column of the selected variant `rr3_vol2_rsi50-70_wick0.5` is biased upward by the selection itself (it is the best of 12); only its TEST column is an out-of-sample test.

**Label: UNTESTED.** Test expectancy +0.361R (n=39) is positive but not distinguishable from zero after multiplicity correction: the one-sided 98.75% lower bound of the test mean (alpha 0.05 split over m=4 pre-registered candidates) is -0.357R by the iid bootstrap and -0.331R by the calendar-month block bootstrap (18 months, 4000 resamples each), and the more conservative -0.357R is not above zero.

Drawdown check (CONTRACT v3 C5 / v4 D7; dd_ok = TEST mark-to-market max drawdown <= min(15%, 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length); the 15% cap is about 15 consecutive full-size losses, a streak of probability (2/3)^15 = 0.23% at the 2:1 break-even win probability p* = 1/3): **passed**. Test mark-to-market max drawdown 6.04% (realised closed-trade 5.53% (6.75R)) is within the limit 15.00% = min(15%, 95th percentile 15.36% of max drawdown over TRAIN trade sequences bootstrapped at the TEST length of 39). For comparison, TRAIN max drawdown: realised 9.28%, mark-to-market 10.21%.

## 5. Entry layers (ML filter, expectancy guard): TRAIN vs TEST

Two pre-registered layers on the baseline rules. `base+ml` adds `L_ml_filter`, a logistic regression on 6 features (rsi, vol_ratio, ema_gap_pct, dist_regime_pct, hour_sin, hour_cos), fitted ONLY on purged TRAIN candidates and applied unchanged to TEST, so its full-TRAIN numbers are IN-SAMPLE. `base+guard` switches on `L_expectancy_guard` (`expectancy_guard=True`: a pair's risk is multiplied by 0.5 while its last 20 closed trades average below 0R); it is a fixed rule over the journal, nothing is fitted, and it needs the same walk-forward evidence as any other variant.

An entry layer can only VETO an entry that passed every mandatory rule; it never approves an entry a rule denies. A veto can free R6 budget or change R9 state, admitting other rule-compliant trades, so a layer's journal is not a subset of the base journal: both directions are counted below, matched by (pair, signal time).

A fitted layer's TRAIN gate is out of sample (CONTRACT v4 D8): its purged TRAIN candidates are split 70/30 in time order, the layer is fitted on the first 70% (purged at the inner boundary) and that inner filter is backtested on the rest of TRAIN. That out-of-sample TRAIN window is what the label judges (NO-EDGE gate, trade counts, the TRAIN drawdown bootstrap) and what the adoption record binds as the TRAIN journal; the in-sample full-TRAIN figure is context only. The model applied to TEST is still fitted on ALL purged TRAIN candidates.

| layer | window (TRAIN / TEST) | signals vetoed | entries at reduced risk (guard x < 1) | base trades absent from the layer journal | layer trades absent from the base journal | trades in both | base trades | layer trades |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `base+ml` | TRAIN | 109 | 0 | 67 | 29 | 33 | 100 | 62 |
| `base+ml` | TEST | 38 | 0 | 33 | 10 | 27 | 60 | 37 |
| `base+guard` | TRAIN | 0 | 35 | 1 | 12 | 99 | 100 | 111 |
| `base+guard` | TEST | 0 | 3 | 0 | 1 | 60 | 60 | 61 |

- `base+ml` vs `base`: TRAIN avg R -0.103 -> +0.347 (n 100 -> 62, in-sample; out-of-sample gate +0.316 (n 22)) | TEST avg R -0.023 -> +0.377 (n 60 -> 37).
- `base+guard` vs `base`: TRAIN avg R -0.103 -> -0.120 (n 100 -> 111) | TEST avg R -0.023 -> +0.011 (n 60 -> 61).

Logistic filter (l2=1) fitted on 249 TRAIN candidates (TRAIN base win rate 32.5%, context only) vetoes every rule-passing signal whose predicted win probability is at or below the break-even 0.354.

- TRAIN candidates enumerated (purged: outcome resolved by the split): 249; used for the final fit: 249 (0 skipped for missing features); TRAIN candidate win share 32.5% (context only).
- **Out-of-sample TRAIN gate (CONTRACT v4 D8):** the 249 purged TRAIN candidates were split 70/30 in time order at 2021-11-03T12:00:00Z; the inner model was fitted on 172 candidates (2 purged: signalled before the inner boundary but resolved after it); inner model fingerprint `cc9af6f6c388a21e`, and backtested from 2021-11-03T12:00:00Z to the split: n=22, avg +0.316R. That window is the TRAIN the label judges and the TRAIN journal the adoption record binds; the in-sample full-TRAIN figure (n=62, avg +0.347R) is context only.
- Threshold p* = break-even win probability from the TRAIN avg win +2.00R and avg loss -1.10R: p* = 0.354 (no threshold search).
- L2 penalty 1 (fixed, not tuned); intercept -0.768.
- Model fingerprint (sha256 of the canonical JSON of features, TRAIN means/stds, intercept, coefficients, threshold and l2): `b04b58e81190dc2884b9b90c7af1b7faa85365ff687da06d5e79fdfe9f391e9a`. It is pinned in the ML adoption record (section 11); `MLFilter.from_json` re-verifies it when the live bot loads the model file.

| feature | standardised coefficient, fitted on TRAIN only and applied unchanged to TEST (log-odds per TRAIN s.d.) |
|---|---:|
| `rsi` | +0.182 |
| `vol_ratio` | +0.196 |
| `ema_gap_pct` | -0.128 |
| `dist_regime_pct` | +0.151 |
| `hour_sin` | -0.413 |
| `hour_cos` | -0.079 |

| metric | base TRAIN | base+ml TRAIN (in-sample) | base+ml TRAIN (out-of-sample inner split) | base+guard TRAIN | base TEST | base+ml TEST | base+guard TEST |
|---|---:|---:|---:|---:|---:|---:|---:|
| trades (n) | 100 | 62 | 22 | 111 | 60 | 37 | 61 |
| avg R (expectancy, the target metric) | -0.103 | +0.347 | +0.316 | -0.120 | -0.023 | +0.377 | +0.011 |
| iid bootstrap 90% CI of avg R | [-0.334, +0.136] | [+0.031, +0.670] | [-0.229, +0.869] | [-0.340, +0.111] | [-0.338, +0.293] | [-0.049, +0.802] | [-0.298, +0.326] |
| calendar-month block bootstrap 90% CI of avg R | [-0.338, +0.143] (41 months) | [-0.029, +0.718] (35 months) | [-0.141, +0.764] (13 months) | [-0.326, +0.107] (42 months) | [-0.312, +0.271] (20 months) | [+0.086, +0.639] (18 months) | [-0.273, +0.304] (20 months) |
| one-sided lower bound of avg R at 1 - alpha/m, iid / block | -0.408 / -0.423 (98.75%) | -0.092 / -0.158 (98.75%) | -0.385 / -0.293 (98.75%) | -0.415 / -0.402 (98.75%) | -0.441 / -0.422 (98.75%) | -0.219 / -0.015 (98.75%) | -0.397 / -0.391 (98.75%) |
| adjusted lower bound used by the label (the smaller) | -0.423 | -0.158 | -0.385 | -0.415 | -0.441 | -0.219 | -0.397 |
| t-stat of avg R | -0.71 | +1.76 | +0.96 | -0.88 | -0.12 | +1.43 | +0.05 |
| total R | -10.33 | +21.49 | +6.95 | -13.37 | -1.36 | +13.93 | +0.64 |
| profit factor | 0.81 | 1.55 | 1.40 | 0.77 | 0.95 | 1.63 | 0.95 |
| max drawdown % realised (closed trades) | 17.51% | 8.72% | 5.26% | 18.43% | 11.04% | 5.26% | 11.04% |
| max drawdown % mark-to-market (4H closes) | 17.86% | 9.81% | 5.41% | 18.92% | 11.19% | 5.81% | 11.19% |
| max drawdown R (closed trades) | 18.82 | 10.10 | 5.35 | 24.84 | 12.86 | 5.87 | 12.86 |
| win rate (context only, never a target) | 32.0% | 46.8% | 45.5% | 31.5% | 35.0% | 48.6% | 36.1% |
| exits SL / TP / END | 68 / 31 / 1 | 33 / 28 / 1 | 12 / 9 / 1 | 76 / 34 / 1 | 39 / 21 / 0 | 19 / 18 / 0 | 39 / 22 / 0 |
| avg hold (h) | 137.8 | 108.1 | 77.3 | 129.7 | 92.0 | 105.8 | 90.7 |

`base+ml`:

**Label: UNTESTED.** TRAIN here is the out-of-sample inner split (CONTRACT v4 D8: the layer refitted on 172 of the first 70% of the 249 purged TRAIN candidates, purged at the inner boundary 2021-11-03T12:00:00Z, and backtested from there to the split; the in-sample full-TRAIN figure is context only). Too few trades to judge: train n=22 (need 30).

Drawdown check (CONTRACT v3 C5 / v4 D7; dd_ok = TEST mark-to-market max drawdown <= min(15%, 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length); the 15% cap is about 15 consecutive full-size losses, a streak of probability (2/3)^15 = 0.23% at the 2:1 break-even win probability p* = 1/3): **passed**. Test mark-to-market max drawdown 5.81% (realised closed-trade 5.26% (5.87R)) is within the limit 10.55% = min(15%, 95th percentile 10.55% of max drawdown over TRAIN trade sequences bootstrapped at the TEST length of 37). For comparison, out-of-sample gate max drawdown: realised 5.26%, mark-to-market 5.41% (in-sample TRAIN max drawdown: realised 8.72%, mark-to-market 9.81%).

`base+guard`:

**Label: NO-EDGE.** Train expectancy is -0.120R over 111 trades, so there is no edge to validate.

Drawdown check (CONTRACT v3 C5 / v4 D7; dd_ok = TEST mark-to-market max drawdown <= min(15%, 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length); the 15% cap is about 15 consecutive full-size losses, a streak of probability (2/3)^15 = 0.23% at the 2:1 break-even win probability p* = 1/3): **passed**. Test mark-to-market max drawdown 11.19% (realised closed-trade 11.04% (12.86R)) is within the limit 15.00% = min(15%, 95th percentile 19.49% of max drawdown over TRAIN trade sequences bootstrapped at the TEST length of 61). For comparison, TRAIN max drawdown: realised 18.43%, mark-to-market 18.92%.

LightGBM, other gradient boosting and neural networks are NOT justified at this sample size (249 TRAIN candidates, 6 features): a flexible model would memorise noise, so only a fixed-penalty logistic regression with a break-even threshold is allowed.

## 6. Verdict

Candidates judged (pre-registered, one TEST look each, m = 4): baseline `base`, discovery-selected `rr3_vol2_rsi50-70_wick0.5`, ML layer `base+ml`, expectancy guard `base+guard`. Other grid variants are excluded because choosing among them by TEST numbers would be selection on TEST. ROBUST requires at least 30 trades in each window, TRAIN and TEST avg R > 0, and a one-sided 98.75% lower bound of the TEST mean above zero for BOTH an iid and a calendar-month block bootstrap (the more conservative is used): alpha 0.05 is split over the m = 4 pre-registered candidates (base, discovery-selected, base+ml, base+guard), so when none has an edge the chance that ANY is called ROBUST is at most about 5%; a positive TEST mean that fails only the bound is UNTESTED (positive but not distinguishable from zero after multiplicity correction); the rule was fixed in advance and is never tuned on TEST outcomes. The TEST gate is reached when the judged TRAIN avg R > 0 and both windows hold >= 30 trades; the drawdown check is reported beside the label. The judged TRAIN of `base+ml` is its out-of-sample gate (CONTRACT v4 D8, section 5).

| candidate | TRAIN n (judged) | TRAIN avg R (judged) | TEST n | TEST avg R | TEST iid / block LB (1 - alpha/m) | reached the TEST gate | label | TEST MTM max DD vs limit | dd_ok |
|---|---:|---:|---:|---:|---|---|---|---|---|
| baseline `base` | 100 | -0.103 | 60 | -0.023 | -0.441 / -0.422 | no | NO-EDGE | 11.19% vs 15.00% | yes |
| discovery-selected `rr3_vol2_rsi50-70_wick0.5` | 72 | +0.148 | 39 | +0.361 | -0.357 / -0.331 | yes | UNTESTED | 6.04% vs 15.00% | yes |
| ML layer `base+ml` | 22 | +0.316 | 37 | +0.377 | -0.219 / -0.015 | no | UNTESTED | 5.81% vs 10.55% | yes |
| expectancy guard `base+guard` | 111 | -0.120 | 61 | +0.011 | -0.397 / -0.391 | no | NO-EDGE | 11.19% vs 15.00% | yes |

No robust result found. Pre-registered candidates, TRAIN | TEST: baseline `base` NO-EDGE (TRAIN -0.103R n=100 | TEST -0.023R n=60); discovery-selected `rr3_vol2_rsi50-70_wick0.5` UNTESTED (TRAIN +0.148R n=72 | TEST +0.361R n=39); ML layer `base+ml` UNTESTED (TRAIN +0.316R n=22 | TEST +0.377R n=37); expectancy guard `base+guard` NO-EDGE (TRAIN -0.120R n=111 | TEST +0.011R n=61).

Holdout ledger (CONTRACT v4 D3): `test_looks.jsonl` (append-only JSON lines). This run appended 4 line(s), one per adoptable pre-registered candidate that got a TEST backtest (context discovery variants and test-only configs are never recorded). A look is a DISTINCT (config fingerprint, model fingerprint), so re-running identical candidates is not a new look. `adoption check` blocks (`ADOPT_holdout`) from HUMAN_REVIEW on when a pair's count exceeds m = 4. After a reviewer N or any other revision, a variant needs a TEST window starting at or after the latest test_end_ts of every earlier look; it is never re-run on the same TEST window.

| pair | TRAIN window (UTC) | TEST window (UTC) | cumulative distinct TEST looks on this TEST window | limit m | status |
|---|---|---|---:|---:|---|
| BNB/USDT | start .. 2023-03-14T00:00:00Z | 2023-03-14T00:00:00Z .. 2024-12-30T00:00:00Z | 4 | 4 | within the limit |
| BTC/USDT | start .. 2023-03-14T00:00:00Z | 2023-03-14T00:00:00Z .. 2024-12-30T00:00:00Z | 4 | 4 | within the limit |
| ETH/USDT | start .. 2023-03-14T00:00:00Z | 2023-03-14T00:00:00Z .. 2024-12-30T00:00:00Z | 4 | 4 | within the limit |

**Stop-fill stress (CONTRACT v4 D4), k = 0.5 versus the touch fill model k = 0**, both on this data and split (the k = 0 run is the reference; TRAIN is the judged TRAIN window). Every stressed config is test-only, so this table measures how optimistic the touch fill is, nothing here is adoptable:

| candidate | variant k=0 / k=0.5 | same config apart from k | judged TRAIN avg R k=0 | judged TRAIN avg R k=0.5 | TRAIN R shift | TEST avg R k=0 | TEST avg R k=0.5 | TEST R shift | label k=0 | label k=0.5 | label changed |
|---|---|---|---:|---:|---:|---:|---:|---:|---|---|---|
| baseline | `base` / `base` | yes | -0.044 (n 100) | -0.103 (n 100) | -0.060 | +0.050 (n 60) | -0.023 (n 60) | -0.073 | NO-EDGE | NO-EDGE | no |
| discovery-selected | `rr3_vol2_rsi50-70` / `rr3_vol2_rsi50-70_wick0.5` | yes | +0.203 (n 72) | +0.148 (n 72) | -0.055 | +0.436 (n 39) | +0.361 (n 39) | -0.075 | UNTESTED | UNTESTED | no |
| ML layer | `base+ml` / `base+ml` | yes | +0.371 (n 26) | +0.316 (n 22) | -0.055 | +0.295 (n 44) | +0.377 (n 37) | +0.081 | UNTESTED | UNTESTED | no |
| expectancy guard | `base+guard` / `base+guard` | yes | -0.057 (n 111) | -0.120 (n 111) | -0.063 | +0.082 (n 61) | +0.011 (n 61) | -0.071 | NO-EDGE | NO-EDGE | no |

## 7. Why signals were rejected (decision counts per rule)

Signal candles denied per rule (the FIRST failing rule is counted, so the R1-R4 rows include every candle that was simply not a signal). Exits are never gated. TRAIN is the full TRAIN backtest (in-sample for the ML layer).

| rule | base TRAIN | base TEST | rr3_vol2_rsi50-70_wick0.5 TRAIN | rr3_vol2_rsi50-70_wick0.5 TEST | base+ml TRAIN | base+ml TEST | base+guard TRAIN | base+guard TEST | meaning |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `R1_trend` | 19100 | 7899 | 19100 | 7899 | 19100 | 7899 | 19100 | 7899 | Trend gate: close > EMA9 and close > EMA21 and EMA9 > EMA21 (4H) |
| `R2_momentum` | 2680 | 1500 | 2680 | 1500 | 2680 | 1500 | 2680 | 1500 | Momentum: RSI(14) within [50, 70] on the entry candle |
| `R3_volume` | 5352 | 2212 | 5517 | 2293 | 5352 | 2212 | 5352 | 2212 | Volume: entry-candle volume >= 1.5x the previous-20-candle average |
| `R4_regime` | 191 | 69 | 126 | 42 | 191 | 69 | 191 | 69 | Regime: close > EMA200 (unless explicitly testing it off) |
| `R5_news_blackout` | 17 | 7 | 11 | 6 | 17 | 7 | 17 | 7 | No entries within +/-2h of high-impact news; BNB +/-24h burns/launchpool |
| `R6_correlation_cap` | 150 | 77 | 87 | 46 | 80 | 61 | 140 | 76 | BTC/ETH/BNB share one risk budget; BNB never stacks |
| `R7_position_risk` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Risk <= 1% (BTC/ETH) / 0.5% (BNB); size derived from stop distance |
| `R8_structure_stop` | 2 | 1 | 1 | 1 | 3 | 2 | 1 | 1 | Stop behind a confirmed swing low with a buffer (BNB 0.5-0.8%) |
| `R9_circuit_breaker` | 2 | 1 | 0 | 0 | 0 | 1 | 2 | 1 | 3 consecutive SLs bench a pair 24h; 7d realized loss limit halts all |
| `L_ml_filter` | 0 | 0 | 0 | 0 | 109 | 38 | 0 | 0 | Layer: logistic-regression veto; it can only veto an entry that passed every mandatory rule and never approves one a rule denies (a veto can free R6 budget or change R9 state, which may admit other rule-compliant trades) |
| `L_expectancy_guard` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Layer: halve a pair's risk while its last-N-trade expectancy < 0 |
| `X_capital` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Execution: not enough free capital / size below minimum |
| candles evaluated | 27594 | 11826 | 27594 | 11826 | 27594 | 11826 | 27594 | 11826 | |
| trades taken | 100 | 60 | 72 | 39 | 62 | 37 | 111 | 61 | |

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
| TRAIN at 2023-03-14T00:00:00Z (equity 8,889.28) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 9,816.17) | - | - | none | No active adaptations. | - |

**discovery-selected `rr3_vol2_rsi50-70_wick0.5`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 10,474.98) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 11,302.57) | - | - | none | No active adaptations. | - |

**ML layer `base+ml`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 10,447.08) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 11,170.52) | - | - | none | No active adaptations. | - |

**expectancy guard `base+guard`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 8,805.55) | L_expectancy_guard | BTC/USDT | risk x0.5 | BTC/USDT's last 20 closed trades average -0.347R (below 0), so new BTC/USDT entries risk 0.5x the normal amount until that average is back at or above 0. | #53, #57, #58, #59, #65, #67, #68, #72, #79, #84, #85, #87, #88, #89, #93, #96, #98, #100, #102, #104 |
| TEST at 2024-12-30T00:00:00Z (equity 9,826.57) | L_expectancy_guard | ETH/USDT | risk x0.5 | ETH/USDT's last 20 closed trades average -0.027R (below 0), so new ETH/USDT entries risk 0.5x the normal amount until that average is back at or above 0. | #133, #135, #136, #138, #140, #142, #144, #146, #147, #148, #150, #152, #157, #161, #163, #165, #168, #169, #170, #171 |

Reproduce the baseline TEST row with `python -m research.trendbot.journal_rules --journal research/results/stress_k0.5/synthetic_hour_edge_s1/journals/base_test.csv --equity 9816.17 --now 2024-12-30T00:00:00Z --backtest-journal`.

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
