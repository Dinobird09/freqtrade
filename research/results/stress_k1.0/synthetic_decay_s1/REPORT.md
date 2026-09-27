# Walk-forward research report: synthetic world `decay` seed 1, STOP-FILL STRESS k = 1 (test-only)

## 1. No win rate is promised or targeted

This report does NOT promise, target or optimise a win rate, and nothing in this package selects on one. A win rate is meaningless without the reward:risk it was earned at: with the mandatory minimum of 2:1 a strategy breaks even at about 33% winners before costs, while a 90% win rate with a 1:9 payoff still loses money. A backtest win rate near 90% is evidence of curve-fitting or look-ahead, not of skill. The target metric is POSITIVE EXPECTANCY (average R per trade, net of fees and slippage) that survives a 70/30 chronological walk-forward with controlled drawdown, with every trade planned at reward:risk >= 2:1. Win rate appears below only as context and is labelled 'context only' wherever it is shown.

## 2. Data provenance

- Source: **SYNTHETIC** world `decay`, seed 1, 6 years of 4H candles (13140 per pair) from 2019-01-01T00:00:00Z, pairs BTC/USDT, ETH/USDT, BNB/USDT (`synthetic.generate_world`); adoption provenance `synthetic:decay:1`.
- Ground truth: the planted effect exists only BEFORE the 70% split; the TEST period is exactly the null world.
- What the ground truth predicts: a positive TRAIN expectancy that does not survive TEST: TRAIN-ONLY (or UNTESTED when the TEST noise is positive but indistinguishable from zero); ROBUST is a false positive.
- Qualifying triggers planted per pair (before / after the 70% split): BTC/USDT 341 / 0, ETH/USDT 333 / 0, BNB/USDT 317 / 0.
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
- **Stop-fill STRESS model (CONTRACT v4 D4), k = 1:** every non-gap stop fills at stop - 1 x (stop - exit-candle low), then slippage, instead of at the stop, so a stopped trade can lose MORE than 1R even without a gap (sizing still plans the loss at the stop). Stress configs are test-only (`is_test_only`): nothing in this run is adoptable, gets an adoption record or counts as a holdout look.
- Measured on the baseline's TRAIN and TEST trades: fees alone cost 0.06R per trade on average (median stop distance 3.71% of the entry price), so the expectancy below is only as good as the fee rate above.

## 3. Baseline: TRAIN vs TEST

Default mandate config `rr2_vol1.5_rsi50-70_wick1` (variant `base`). TRAIN window start .. 2023-03-14T00:00:00Z; TEST window 2023-03-14T00:00:00Z .. 2024-12-30T00:00:00Z.

| metric | TRAIN | TEST |
|---|---:|---:|
| trades (n) | 114 | 56 |
| avg R (expectancy, the target metric) | +0.215 | -0.252 |
| iid bootstrap 90% CI of avg R | [-0.025, +0.455] | [-0.555, +0.075] |
| calendar-month block bootstrap 90% CI of avg R | [-0.045, +0.444] (48 months) | [-0.511, +0.024] (20 months) |
| one-sided lower bound of avg R at 1 - alpha/m, iid / block | -0.108 / -0.156 (98.75%) | -0.666 / -0.617 (98.75%) |
| adjusted lower bound used by the label (the smaller) | -0.156 | -0.666 |
| t-stat of avg R | +1.45 | -1.31 |
| total R | +24.46 | -14.13 |
| profit factor | 1.44 | 0.66 |
| max drawdown % realised (closed trades) | 9.57% | 16.49% |
| max drawdown % mark-to-market (4H closes) | 9.62% | 17.03% |
| max drawdown R (closed trades) | 11.82 | 19.41 |
| win rate (context only, never a target) | 43.9% | 28.6% |
| exits SL / TP / END | 64 / 49 / 1 | 40 / 16 / 0 |
| avg hold (h) | 105.1 | 118.9 |

**Label: TRAIN-ONLY.** Train expectancy +0.215R (n=114) did not hold out of sample: test expectancy is -0.252R (n=56), which suggests curve-fitting.

Drawdown check (CONTRACT v3 C5 / v4 D7; dd_ok = TEST mark-to-market max drawdown <= min(15%, 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length); the 15% cap is about 15 consecutive full-size losses, a streak of probability (2/3)^15 = 0.23% at the 2:1 break-even win probability p* = 1/3): **FAILED**. Test mark-to-market max drawdown 17.03% (realised closed-trade 16.49% (19.41R)) exceeds the limit 12.53% = min(15%, 95th percentile 12.53% of max drawdown over TRAIN trade sequences bootstrapped at the TEST length of 56). For comparison, TRAIN max drawdown: realised 9.57%, mark-to-market 9.62%.

## 4. Strategy discovery: TRAIN vs TEST

K = 13 variants tried: 12 legal tightenings (reward:risk {2, 2.5, 3} x volume multiple {1.5, 2} x RSI window {50-70, 55-70}) plus 1 explicit test-only variant with the R4 regime filter OFF, which is reported but can never be selected or adopted.

Selection rule: highest TRAIN t-stat among selectable variants with >= 30 TRAIN trades. rr3_vol2_rsi50-70_wick1 has the highest TRAIN t-stat (+2.65, avg +0.654R over 70 trades) among 12 eligible variants; chosen on TRAIN only, before any TEST backtest was run.

Order of operations actually run: TRAIN backtests: 13 variants, candles closed by the split only -> selection recorded: rr3_vol2_rsi50-70_wick1 -> TEST backtests: 13 variants, reported side by side only.

Only the selected variant is a pre-registered candidate (one of the m TEST looks of section 6). Every other row, including the regime-OFF test variant, is shown as `context: <label>, not judged`: picking one of them because of its TEST numbers would be selection on TEST. Context variants are not adoptable: they get no adoption record and no holdout-ledger line.

| variant | status | TRAIN n | TRAIN avg R | TRAIN 90% CI | TRAIN t | TEST n | TEST avg R | TEST 90% CI | TEST adjusted LB | TEST max DD % realised / MTM | label |
|---|---|---:|---:|---|---:|---:|---:|---|---:|---|---|
| `rr2_vol1.5_rsi50-70_wick1` | selectable | 114 | +0.215 | [-0.025, +0.455] | +1.45 | 56 | -0.252 | [-0.555, +0.075] | -0.666 | 16.49% / 17.03% | context: TRAIN-ONLY, not judged |
| `rr2_vol1.5_rsi55-70_wick1` | selectable | 103 | +0.243 | [-0.017, +0.502] | +1.56 | 57 | -0.218 | [-0.533, +0.107] | -0.629 | 16.74% / 17.48% | context: TRAIN-ONLY, not judged |
| `rr2_vol2_rsi50-70_wick1` | selectable | 92 | +0.364 | [+0.083, +0.640] | +2.19 | 51 | -0.294 | [-0.604, +0.051] | -0.713 | 15.85% / 15.85% | context: TRAIN-ONLY, not judged |
| `rr2_vol2_rsi55-70_wick1` | selectable | 84 | +0.331 | [+0.044, +0.615] | +1.91 | 52 | -0.259 | [-0.580, +0.081] | -0.683 | 14.91% / 14.91% | context: TRAIN-ONLY, not judged |
| `rr2.5_vol1.5_rsi50-70_wick1` | selectable | 103 | +0.280 | [-0.015, +0.576] | +1.58 | 48 | -0.313 | [-0.672, +0.078] | -0.784 | 18.25% / 18.59% | context: TRAIN-ONLY, not judged |
| `rr2.5_vol1.5_rsi55-70_wick1` | selectable | 92 | +0.298 | [-0.007, +0.612] | +1.59 | 53 | -0.260 | [-0.611, +0.101] | -0.729 | 18.64% / 18.97% | context: TRAIN-ONLY, not judged |
| `rr2.5_vol2_rsi50-70_wick1` | selectable | 85 | +0.449 | [+0.124, +0.781] | +2.26 | 49 | -0.260 | [-0.623, +0.128] | -0.722 | 15.69% / 16.21% | context: TRAIN-ONLY, not judged |
| `rr2.5_vol2_rsi55-70_wick1` | selectable | 77 | +0.389 | [+0.057, +0.726] | +1.88 | 51 | -0.232 | [-0.597, +0.145] | -0.708 | 15.49% / 16.01% | context: TRAIN-ONLY, not judged |
| `rr3_vol1.5_rsi50-70_wick1` | selectable | 90 | +0.240 | [-0.105, +0.581] | +1.15 | 48 | -0.203 | [-0.618, +0.232] | -0.731 | 15.15% / 15.54% | context: TRAIN-ONLY, not judged |
| `rr3_vol1.5_rsi55-70_wick1` | selectable | 85 | +0.180 | [-0.162, +0.536] | +0.85 | 54 | -0.168 | [-0.557, +0.234] | -0.692 | 15.39% / 15.78% | context: TRAIN-ONLY, not judged |
| `rr3_vol2_rsi50-70_wick1` | **SELECTED** | 70 | +0.654 | [+0.257, +1.064] | +2.65 | 46 | -0.167 | [-0.593, +0.288] | -0.723 | 13.34% / 13.41% | TRAIN-ONLY |
| `rr3_vol2_rsi55-70_wick1` | selectable | 65 | +0.546 | [+0.143, +0.961] | +2.16 | 48 | -0.130 | [-0.558, +0.301] | -0.683 | 12.71% / 12.78% | context: TRAIN-ONLY, not judged |
| `rr2_vol1.5_rsi50-70_regimeOFF_wick1` | TEST-ONLY (regime OFF, never selectable) | 154 | +0.135 | [-0.072, +0.346] | +1.09 | 64 | -0.267 | [-0.555, +0.035] | -0.668 | 18.87% / 19.12% | context: TRAIN-ONLY, not judged |

**Re-selecting a variant on these TEST numbers would turn TEST into TRAIN: the selection was fixed on TRAIN before any TEST backtest ran, and it is not revisited.**

The TRAIN column of the selected variant `rr3_vol2_rsi50-70_wick1` is biased upward by the selection itself (it is the best of 12); only its TEST column is an out-of-sample test.

**Label: TRAIN-ONLY.** Train expectancy +0.654R (n=70) did not hold out of sample: test expectancy is -0.167R (n=46), which suggests curve-fitting.

Drawdown check (CONTRACT v3 C5 / v4 D7; dd_ok = TEST mark-to-market max drawdown <= min(15%, 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length); the 15% cap is about 15 consecutive full-size losses, a streak of probability (2/3)^15 = 0.23% at the 2:1 break-even win probability p* = 1/3): **FAILED**. Test mark-to-market max drawdown 13.41% (realised closed-trade 13.34% (15.16R)) exceeds the limit 10.20% = min(15%, 95th percentile 10.20% of max drawdown over TRAIN trade sequences bootstrapped at the TEST length of 46). For comparison, TRAIN max drawdown: realised 9.83%, mark-to-market 9.88%.

## 5. Entry layers (ML filter, expectancy guard): TRAIN vs TEST

Two pre-registered layers on the baseline rules. `base+ml` adds `L_ml_filter`, a logistic regression on 6 features (rsi, vol_ratio, ema_gap_pct, dist_regime_pct, hour_sin, hour_cos), fitted ONLY on purged TRAIN candidates and applied unchanged to TEST, so its full-TRAIN numbers are IN-SAMPLE. `base+guard` switches on `L_expectancy_guard` (`expectancy_guard=True`: a pair's risk is multiplied by 0.5 while its last 20 closed trades average below 0R); it is a fixed rule over the journal, nothing is fitted, and it needs the same walk-forward evidence as any other variant.

An entry layer can only VETO an entry that passed every mandatory rule; it never approves an entry a rule denies. A veto can free R6 budget or change R9 state, admitting other rule-compliant trades, so a layer's journal is not a subset of the base journal: both directions are counted below, matched by (pair, signal time).

A fitted layer's TRAIN gate is out of sample (CONTRACT v4 D8): its purged TRAIN candidates are split 70/30 in time order, the layer is fitted on the first 70% (purged at the inner boundary) and that inner filter is backtested on the rest of TRAIN. That out-of-sample TRAIN window is what the label judges (NO-EDGE gate, trade counts, the TRAIN drawdown bootstrap) and what the adoption record binds as the TRAIN journal; the in-sample full-TRAIN figure is context only. The model applied to TEST is still fitted on ALL purged TRAIN candidates.

| layer | window (TRAIN / TEST) | signals vetoed | entries at reduced risk (guard x < 1) | base trades absent from the layer journal | layer trades absent from the base journal | trades in both | base trades | layer trades |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `base+ml` | TRAIN | 82 | 0 | 53 | 20 | 61 | 114 | 81 |
| `base+ml` | TEST | 28 | 0 | 21 | 19 | 35 | 56 | 54 |
| `base+guard` | TRAIN | 0 | 19 | 0 | 0 | 114 | 114 | 114 |
| `base+guard` | TEST | 0 | 2 | 0 | 0 | 56 | 56 | 56 |

- `base+ml` vs `base`: TRAIN avg R +0.215 -> +0.441 (n 114 -> 81, in-sample; out-of-sample gate +0.221 (n 18)) | TEST avg R -0.252 -> -0.177 (n 56 -> 54).
- `base+guard` vs `base`: TRAIN avg R +0.215 -> +0.215 (n 114 -> 114) | TEST avg R -0.252 -> -0.252 (n 56 -> 56).

Logistic filter (l2=1) fitted on 257 TRAIN candidates (TRAIN base win rate 39.3%, context only) vetoes every rule-passing signal whose predicted win probability is at or below the break-even 0.370.

- TRAIN candidates enumerated (purged: outcome resolved by the split): 257; used for the final fit: 257 (0 skipped for missing features); TRAIN candidate win share 39.3% (context only).
- **Out-of-sample TRAIN gate (CONTRACT v4 D8):** the 257 purged TRAIN candidates were split 70/30 in time order at 2022-02-21T08:00:00Z; the inner model was fitted on 175 candidates (4 purged: signalled before the inner boundary but resolved after it); inner model fingerprint `bc41da806bf07dd0`, and backtested from 2022-02-21T08:00:00Z to the split: n=18, avg +0.221R. That window is the TRAIN the label judges and the TRAIN journal the adoption record binds; the in-sample full-TRAIN figure (n=81, avg +0.441R) is context only.
- Threshold p* = break-even win probability from the TRAIN avg win +2.00R and avg loss -1.18R: p* = 0.370 (no threshold search).
- L2 penalty 1 (fixed, not tuned); intercept -0.488.
- Model fingerprint (sha256 of the canonical JSON of features, TRAIN means/stds, intercept, coefficients, threshold and l2): `d0621af3cfae2156b5517ead6c5a8608d39acfa644ff41e56dd53300bb8e2268`. It is pinned in the ML adoption record (section 11); `MLFilter.from_json` re-verifies it when the live bot loads the model file.

| feature | standardised coefficient, fitted on TRAIN only and applied unchanged to TEST (log-odds per TRAIN s.d.) |
|---|---:|
| `rsi` | +0.049 |
| `vol_ratio` | +0.303 |
| `ema_gap_pct` | -0.613 |
| `dist_regime_pct` | -0.189 |
| `hour_sin` | +0.131 |
| `hour_cos` | +0.230 |

| metric | base TRAIN | base+ml TRAIN (in-sample) | base+ml TRAIN (out-of-sample inner split) | base+guard TRAIN | base TEST | base+ml TEST | base+guard TEST |
|---|---:|---:|---:|---:|---:|---:|---:|
| trades (n) | 114 | 81 | 18 | 114 | 56 | 54 | 56 |
| avg R (expectancy, the target metric) | +0.215 | +0.441 | +0.221 | +0.215 | -0.252 | -0.177 | -0.252 |
| iid bootstrap 90% CI of avg R | [-0.025, +0.455] | [+0.137, +0.731] | [-0.334, +0.801] | [-0.025, +0.455] | [-0.555, +0.075] | [-0.491, +0.175] | [-0.555, +0.075] |
| calendar-month block bootstrap 90% CI of avg R | [-0.045, +0.444] (48 months) | [+0.134, +0.770] (43 months) | [-0.309, +0.883] (10 months) | [-0.045, +0.444] (48 months) | [-0.511, +0.024] (20 months) | [-0.454, +0.116] (20 months) | [-0.511, +0.024] (20 months) |
| one-sided lower bound of avg R at 1 - alpha/m, iid / block | -0.108 / -0.156 (98.75%) | +0.047 / +0.022 (98.75%) | -0.568 / -0.485 (98.75%) | -0.108 / -0.156 (98.75%) | -0.666 / -0.617 (98.75%) | -0.621 / -0.568 (98.75%) | -0.666 / -0.617 (98.75%) |
| adjusted lower bound used by the label (the smaller) | -0.156 | +0.022 | -0.568 | -0.156 | -0.666 | -0.621 | -0.666 |
| t-stat of avg R | +1.45 | +2.45 | +0.61 | +1.45 | -1.31 | -0.87 | -1.31 |
| total R | +24.46 | +35.69 | +3.98 | +24.46 | -14.13 | -9.56 | -14.13 |
| profit factor | 1.44 | 1.67 | 1.26 | 1.41 | 0.66 | 0.70 | 0.68 |
| max drawdown % realised (closed trades) | 9.57% | 4.83% | 4.66% | 8.78% | 16.49% | 16.28% | 16.49% |
| max drawdown % mark-to-market (4H closes) | 9.62% | 5.31% | 5.67% | 8.96% | 17.03% | 16.55% | 17.03% |
| max drawdown R (closed trades) | 11.82 | 5.48 | 3.71 | 11.82 | 19.41 | 17.56 | 19.41 |
| win rate (context only, never a target) | 43.9% | 51.9% | 44.4% | 43.9% | 28.6% | 31.5% | 28.6% |
| exits SL / TP / END | 64 / 49 / 1 | 39 / 41 / 1 | 10 / 7 / 1 | 64 / 49 / 1 | 40 / 16 / 0 | 37 / 17 / 0 | 40 / 16 / 0 |
| avg hold (h) | 105.1 | 96.0 | 175.3 | 105.1 | 118.9 | 99.6 | 118.9 |

`base+ml`:

**Label: UNTESTED.** TRAIN here is the out-of-sample inner split (CONTRACT v4 D8: the layer refitted on 175 of the first 70% of the 257 purged TRAIN candidates, purged at the inner boundary 2022-02-21T08:00:00Z, and backtested from there to the split; the in-sample full-TRAIN figure is context only). Too few trades to judge: train n=18 (need 30).

Drawdown check (CONTRACT v3 C5 / v4 D7; dd_ok = TEST mark-to-market max drawdown <= min(15%, 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length); the 15% cap is about 15 consecutive full-size losses, a streak of probability (2/3)^15 = 0.23% at the 2:1 break-even win probability p* = 1/3): **FAILED**. Test mark-to-market max drawdown 16.55% (realised closed-trade 16.28% (17.56R)) exceeds the limit 14.72% = min(15%, 95th percentile 14.72% of max drawdown over TRAIN trade sequences bootstrapped at the TEST length of 54). For comparison, out-of-sample gate max drawdown: realised 4.66%, mark-to-market 5.67% (in-sample TRAIN max drawdown: realised 4.83%, mark-to-market 5.31%).

`base+guard`:

**Label: TRAIN-ONLY.** Train expectancy +0.215R (n=114) did not hold out of sample: test expectancy is -0.252R (n=56), which suggests curve-fitting.

Drawdown check (CONTRACT v3 C5 / v4 D7; dd_ok = TEST mark-to-market max drawdown <= min(15%, 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length); the 15% cap is about 15 consecutive full-size losses, a streak of probability (2/3)^15 = 0.23% at the 2:1 break-even win probability p* = 1/3): **FAILED**. Test mark-to-market max drawdown 17.03% (realised closed-trade 16.49% (19.41R)) exceeds the limit 12.43% = min(15%, 95th percentile 12.43% of max drawdown over TRAIN trade sequences bootstrapped at the TEST length of 56). For comparison, TRAIN max drawdown: realised 8.78%, mark-to-market 8.96%.

LightGBM, other gradient boosting and neural networks are NOT justified at this sample size (257 TRAIN candidates, 6 features): a flexible model would memorise noise, so only a fixed-penalty logistic regression with a break-even threshold is allowed.

## 6. Verdict

Candidates judged (pre-registered, one TEST look each, m = 4): baseline `base`, discovery-selected `rr3_vol2_rsi50-70_wick1`, ML layer `base+ml`, expectancy guard `base+guard`. Other grid variants are excluded because choosing among them by TEST numbers would be selection on TEST. ROBUST requires at least 30 trades in each window, TRAIN and TEST avg R > 0, and a one-sided 98.75% lower bound of the TEST mean above zero for BOTH an iid and a calendar-month block bootstrap (the more conservative is used): alpha 0.05 is split over the m = 4 pre-registered candidates (base, discovery-selected, base+ml, base+guard), so when none has an edge the chance that ANY is called ROBUST is at most about 5%; a positive TEST mean that fails only the bound is UNTESTED (positive but not distinguishable from zero after multiplicity correction); the rule was fixed in advance and is never tuned on TEST outcomes. The TEST gate is reached when the judged TRAIN avg R > 0 and both windows hold >= 30 trades; the drawdown check is reported beside the label. The judged TRAIN of `base+ml` is its out-of-sample gate (CONTRACT v4 D8, section 5).

| candidate | TRAIN n (judged) | TRAIN avg R (judged) | TEST n | TEST avg R | TEST iid / block LB (1 - alpha/m) | reached the TEST gate | label | TEST MTM max DD vs limit | dd_ok |
|---|---:|---:|---:|---:|---|---|---|---|---|
| baseline `base` | 114 | +0.215 | 56 | -0.252 | -0.666 / -0.617 | yes | TRAIN-ONLY | 17.03% vs 12.53% | no |
| discovery-selected `rr3_vol2_rsi50-70_wick1` | 70 | +0.654 | 46 | -0.167 | -0.723 / -0.692 | yes | TRAIN-ONLY | 13.41% vs 10.20% | no |
| ML layer `base+ml` | 18 | +0.221 | 54 | -0.177 | -0.621 / -0.568 | no | UNTESTED | 16.55% vs 14.72% | no |
| expectancy guard `base+guard` | 114 | +0.215 | 56 | -0.252 | -0.666 / -0.617 | yes | TRAIN-ONLY | 17.03% vs 12.43% | no |

No robust result found. Pre-registered candidates, TRAIN | TEST: baseline `base` TRAIN-ONLY (TRAIN +0.215R n=114 | TEST -0.252R n=56); discovery-selected `rr3_vol2_rsi50-70_wick1` TRAIN-ONLY (TRAIN +0.654R n=70 | TEST -0.167R n=46); ML layer `base+ml` UNTESTED (TRAIN +0.221R n=18 | TEST -0.177R n=54); expectancy guard `base+guard` TRAIN-ONLY (TRAIN +0.215R n=114 | TEST -0.252R n=56).

Holdout ledger (CONTRACT v4 D3): `test_looks.jsonl` (append-only JSON lines). This run appended 4 line(s), one per adoptable pre-registered candidate that got a TEST backtest (context discovery variants and test-only configs are never recorded). A look is a DISTINCT (config fingerprint, model fingerprint), so re-running identical candidates is not a new look. `adoption check` blocks (`ADOPT_holdout`) from HUMAN_REVIEW on when a pair's count exceeds m = 4. After a reviewer N or any other revision, a variant needs a TEST window starting at or after the latest test_end_ts of every earlier look; it is never re-run on the same TEST window.

| pair | TRAIN window (UTC) | TEST window (UTC) | cumulative distinct TEST looks on this TEST window | limit m | status |
|---|---|---|---:|---:|---|
| BNB/USDT | start .. 2023-03-14T00:00:00Z | 2023-03-14T00:00:00Z .. 2024-12-30T00:00:00Z | 4 | 4 | within the limit |
| BTC/USDT | start .. 2023-03-14T00:00:00Z | 2023-03-14T00:00:00Z .. 2024-12-30T00:00:00Z | 4 | 4 | within the limit |
| ETH/USDT | start .. 2023-03-14T00:00:00Z | 2023-03-14T00:00:00Z .. 2024-12-30T00:00:00Z | 4 | 4 | within the limit |

**Stop-fill stress (CONTRACT v4 D4), k = 1 versus the touch fill model k = 0**, both on this data and split (the k = 0 run is the reference; TRAIN is the judged TRAIN window). Every stressed config is test-only, so this table measures how optimistic the touch fill is, nothing here is adoptable:

| candidate | variant k=0 / k=1 | same config apart from k | judged TRAIN avg R k=0 | judged TRAIN avg R k=1 | TRAIN R shift | TEST avg R k=0 | TEST avg R k=1 | TEST R shift | label k=0 | label k=1 | label changed |
|---|---|---|---:|---:|---:|---:|---:|---:|---|---|---|
| baseline | `base` / `base` | yes | +0.307 (n 114) | +0.215 (n 114) | -0.092 | -0.143 (n 56) | -0.252 (n 56) | -0.109 | TRAIN-ONLY | TRAIN-ONLY | no |
| discovery-selected | `rr3_vol2_rsi50-70` / `rr3_vol2_rsi50-70_wick1` | yes | +0.742 (n 70) | +0.654 (n 70) | -0.088 | -0.043 (n 46) | -0.167 (n 46) | -0.123 | TRAIN-ONLY | TRAIN-ONLY | no |
| ML layer | `base+ml` / `base+ml` | yes | +0.129 (n 23) | +0.221 (n 18) | +0.092 | -0.073 (n 55) | -0.177 (n 54) | -0.104 | UNTESTED | UNTESTED | no |
| expectancy guard | `base+guard` / `base+guard` | yes | +0.307 (n 114) | +0.215 (n 114) | -0.092 | -0.143 (n 56) | -0.252 (n 56) | -0.109 | TRAIN-ONLY | TRAIN-ONLY | no |

## 7. Why signals were rejected (decision counts per rule)

Signal candles denied per rule (the FIRST failing rule is counted, so the R1-R4 rows include every candle that was simply not a signal). Exits are never gated. TRAIN is the full TRAIN backtest (in-sample for the ML layer).

| rule | base TRAIN | base TEST | rr3_vol2_rsi50-70_wick1 TRAIN | rr3_vol2_rsi50-70_wick1 TEST | base+ml TRAIN | base+ml TEST | base+guard TRAIN | base+guard TEST | meaning |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `R1_trend` | 18588 | 7612 | 18588 | 7612 | 18588 | 7612 | 18588 | 7612 | Trend gate: close > EMA9 and close > EMA21 and EMA9 > EMA21 (4H) |
| `R2_momentum` | 3304 | 772 | 3304 | 772 | 3304 | 772 | 3304 | 772 | Momentum: RSI(14) within [50, 70] on the entry candle |
| `R3_volume` | 5246 | 3125 | 5404 | 3243 | 5246 | 3125 | 5246 | 3125 | Volume: entry-candle volume >= 1.5x the previous-20-candle average |
| `R4_regime` | 186 | 97 | 120 | 56 | 186 | 97 | 186 | 97 | Regime: close > EMA200 (unless explicitly testing it off) |
| `R5_news_blackout` | 9 | 8 | 6 | 6 | 9 | 8 | 9 | 8 | No entries within +/-2h of high-impact news; BNB +/-24h burns/launchpool |
| `R6_correlation_cap` | 146 | 154 | 101 | 90 | 96 | 126 | 146 | 154 | BTC/ETH/BNB share one risk budget; BNB never stacks |
| `R7_position_risk` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Risk <= 1% (BTC/ETH) / 0.5% (BNB); size derived from stop distance |
| `R8_structure_stop` | 1 | 2 | 1 | 0 | 2 | 2 | 1 | 2 | Stop behind a confirmed swing low with a buffer (BNB 0.5-0.8%) |
| `R9_circuit_breaker` | 0 | 0 | 0 | 1 | 0 | 2 | 0 | 0 | 3 consecutive SLs bench a pair 24h; 7d realized loss limit halts all |
| `L_ml_filter` | 0 | 0 | 0 | 0 | 82 | 28 | 0 | 0 | Layer: logistic-regression veto; it can only veto an entry that passed every mandatory rule and never approves one a rule denies (a veto can free R6 budget or change R9 state, which may admit other rule-compliant trades) |
| `L_expectancy_guard` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Layer: halve a pair's risk while its last-N-trade expectancy < 0 |
| `X_capital` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Execution: not enough free capital / size below minimum |
| candles evaluated | 27594 | 11826 | 27594 | 11826 | 27594 | 11826 | 27594 | 11826 | |
| trades taken | 114 | 56 | 70 | 46 | 81 | 54 | 114 | 56 | |

## 8. Invariant audit of every backtest

`invariants.check_invariants` independently re-derives every mandatory rule (R1-R9, 2:1 minimum, sizing from the stop, exits on the first touch, no data after the window) from each trade list and the candles. A fitted layer's TRAIN column also covers its D8 out-of-sample gate backtest.

| backtest | TRAIN violations | TEST violations |
|---|---:|---:|
| `base` | 0 | 0 |
| `rr2_vol1.5_rsi50-70_wick1` | 0 | 0 |
| `rr2_vol1.5_rsi55-70_wick1` | 0 | 0 |
| `rr2_vol2_rsi50-70_wick1` | 0 | 0 |
| `rr2_vol2_rsi55-70_wick1` | 0 | 0 |
| `rr2.5_vol1.5_rsi50-70_wick1` | 0 | 0 |
| `rr2.5_vol1.5_rsi55-70_wick1` | 0 | 0 |
| `rr2.5_vol2_rsi50-70_wick1` | 0 | 0 |
| `rr2.5_vol2_rsi55-70_wick1` | 0 | 0 |
| `rr3_vol1.5_rsi50-70_wick1` | 0 | 0 |
| `rr3_vol1.5_rsi55-70_wick1` | 0 | 0 |
| `rr3_vol2_rsi50-70_wick1` | 0 | 0 |
| `rr3_vol2_rsi55-70_wick1` | 0 | 0 |
| `rr2_vol1.5_rsi50-70_regimeOFF_wick1` | 0 | 0 |
| `base+ml` | 0 | 0 |
| `base+guard` | 0 | 0 |

Result: CLEAN. 0 violations in 33 backtests.

## 9. Journal rules at the end of TRAIN and at the end of TEST

`journal_rules.audit` over each pre-registered candidate's TRAIN journal at the split and its TEST journal at 2024-12-30T00:00:00Z (the end of TEST), each with that window's final equity: every adaptation the live bot would be applying at that moment because of past trades, with the journal rows that caused it. Backtest journals record the OPEN of the exit candle, so each exit counts from exit_ts + 4h, when the exit is certain (`exit_time_uncertainty_ms = cfg.timeframe_ms`, CONTRACT.md v2 A2); a bench therefore lasts at least 24h of real time. A live journal records real fill times and uses 0.

**baseline `base`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 12,891.39) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 8,689.98) | - | - | none | No active adaptations. | - |

**discovery-selected `rr3_vol2_rsi50-70_wick1`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 15,123.65) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 9,240.83) | - | - | none | No active adaptations. | - |

**ML layer `base+ml`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 10,282.94) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 8,885.43) | - | - | none | No active adaptations. | - |

**expectancy guard `base+guard`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 12,558.12) | L_expectancy_guard | BNB/USDT | risk x0.5 | BNB/USDT's last 20 closed trades average -0.028R (below 0), so new BNB/USDT entries risk 0.5x the normal amount until that average is back at or above 0. | #56, #58, #60, #62, #63, #66, #67, #77, #78, #80, #81, #82, #83, #84, #90, #91, #98, #99, #111, #112 |
| TEST at 2024-12-30T00:00:00Z (equity 8,787.05) | L_expectancy_guard | BTC/USDT | risk x0.5 | BTC/USDT's last 20 closed trades average -0.188R (below 0), so new BTC/USDT entries risk 0.5x the normal amount until that average is back at or above 0. | #115, #118, #119, #120, #121, #124, #130, #144, #147, #149, #153, #154, #155, #158, #160, #161, #164, #165, #166, #169 |
| TEST at 2024-12-30T00:00:00Z (equity 8,787.05) | L_expectancy_guard | ETH/USDT | risk x0.5 | ETH/USDT's last 20 closed trades average -0.353R (below 0), so new ETH/USDT entries risk 0.5x the normal amount until that average is back at or above 0. | #126, #128, #129, #132, #135, #136, #139, #141, #143, #145, #148, #150, #151, #152, #156, #157, #162, #163, #168, #170 |

Reproduce the baseline TEST row with `python -m research.trendbot.journal_rules --journal research/results/stress_k1.0/synthetic_decay_s1/journals/base_test.csv --equity 8689.98 --now 2024-12-30T00:00:00Z --backtest-journal`.

## 10. Journals and human review packs

Trade journals (`journal.write_journal`, one CSV per window) for the 4 pre-registered candidates that ran, the variants whose TRAIN and TEST columns this report shows in full. The other discovery variants appear only as one context row each in section 4, so no journal is kept for them (the same command regenerates them). TEST trade ids are offset past the TRAIN ids, so ids are unique across a variant's two journals and its review pack. The TRAIN journal is the judged TRAIN window (for a fitted layer its D8 out-of-sample gate; its in-sample full-TRAIN journal is kept as context and bound by no record).

| variant | TRAIN journal (judged) | TEST journal | in-sample full-TRAIN journal (context) |
|---|---|---|---|
| `base` | [journals/base_train.csv](journals/base_train.csv) | [journals/base_test.csv](journals/base_test.csv) | - |
| `rr3_vol2_rsi50-70_wick1` | [journals/rr3_vol2_rsi50-70_wick1_train.csv](journals/rr3_vol2_rsi50-70_wick1_train.csv) | [journals/rr3_vol2_rsi50-70_wick1_test.csv](journals/rr3_vol2_rsi50-70_wick1_test.csv) | - |
| `base+ml` | [journals/base_plus_ml_train.csv](journals/base_plus_ml_train.csv) | [journals/base_plus_ml_test.csv](journals/base_plus_ml_test.csv) | [journals/base_plus_ml_train_insample.csv](journals/base_plus_ml_train_insample.csv) |
| `base+guard` | [journals/base_plus_guard_train.csv](journals/base_plus_guard_train.csv) | [journals/base_plus_guard_test.csv](journals/base_plus_guard_test.csv) | - |

Human review packs (`review_sheet.write_review_pack`, TRAIN and TEST):

- no pack for `base`, `rr3_vol2_rsi50-70_wick1`, `base+ml`, `base+guard`: a test-only config (CONTRACT v4 D1, `ADOPT_test_only` blocks it from HUMAN_REVIEW on), so a review pack could never be used; its journals above hold every trade.

## 11. Adoption path and current stage

The only path to real money (enforced by `adoption.py`; no step can be skipped, the promoted config must match the tested config's sha256 fingerprint, and an ML layer's model must match the tested model's fingerprint):

backtest -> walk-forward (ROBUST + drawdown check) -> human review of EVERY trade -> >= 2 weeks on Binance testnet with zero rule violations -> live.

Stages: BACKTEST -> WALK_FORWARD -> HUMAN_REVIEW -> TESTNET -> LIVE. Stage reached by this run: **WALK_FORWARD** (recorded). Next: **HUMAN_REVIEW**.

**No adoption record was written: every config of this run is test-only (stop-fill stress, CONTRACT v4 D4).** Test-only and context variants are not adoptable: they get no promotable record, and `adoption check` blocks any record for one (`ADOPT_test_only` from HUMAN_REVIEW on, `ADOPT_holdout` 'context variant').

## 12. Risk disclaimer

This is a research and testing tool, not financial advice and not a recommendation to trade. Backtests and synthetic worlds are simplified models; past or simulated results do not predict future results. Crypto trading can result in the total loss of the capital used.
