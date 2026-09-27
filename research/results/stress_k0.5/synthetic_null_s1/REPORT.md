# Walk-forward research report: synthetic world `null` seed 1, STOP-FILL STRESS k = 0.5 (test-only)

## 1. No win rate is promised or targeted

This report does NOT promise, target or optimise a win rate, and nothing in this package selects on one. A win rate is meaningless without the reward:risk it was earned at: with the mandatory minimum of 2:1 a strategy breaks even at about 33% winners before costs, while a 90% win rate with a 1:9 payoff still loses money. A backtest win rate near 90% is evidence of curve-fitting or look-ahead, not of skill. The target metric is POSITIVE EXPECTANCY (average R per trade, net of fees and slippage) that survives a 70/30 chronological walk-forward with controlled drawdown, with every trade planned at reward:risk >= 2:1. Win rate appears below only as context and is labelled 'context only' wherever it is shown.

## 2. Data provenance

- Source: **SYNTHETIC** world `null`, seed 1, 6 years of 4H candles (13140 per pair) from 2019-01-01T00:00:00Z, pairs BTC/USDT, ETH/USDT, BNB/USDT (`synthetic.generate_world`); adoption provenance `synthetic:null:1`.
- Ground truth: martingale prices (zero drift): no entry/exit rule has an edge before costs, so every strategy has negative expectancy after fees and slippage.
- What the ground truth predicts: nothing should be ROBUST; the expected labels are NO-EDGE, TRAIN-ONLY or UNTESTED, and a ROBUST label here is a false positive.
- Qualifying triggers planted per pair (before / after the 70% split): BTC/USDT 0 / 0, ETH/USDT 0 / 0, BNB/USDT 0 / 0.
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
- Measured on the baseline's TRAIN and TEST trades: fees alone cost 0.06R per trade on average (median stop distance 3.78% of the entry price), so the expectancy below is only as good as the fee rate above.

## 3. Baseline: TRAIN vs TEST

Default mandate config `rr2_vol1.5_rsi50-70_wick0.5` (variant `base`). TRAIN window start .. 2023-03-14T00:00:00Z; TEST window 2023-03-14T00:00:00Z .. 2024-12-30T00:00:00Z.

| metric | TRAIN | TEST |
|---|---:|---:|
| trades (n) | 98 | 58 |
| avg R (expectancy, the target metric) | -0.189 | -0.174 |
| iid bootstrap 90% CI of avg R | [-0.416, +0.055] | [-0.451, +0.145] |
| calendar-month block bootstrap 90% CI of avg R | [-0.377, -0.003] (44 months) | [-0.441, +0.104] (20 months) |
| one-sided lower bound of avg R at 1 - alpha/m, iid / block | -0.503 / -0.451 (98.75%) | -0.559 / -0.550 (98.75%) |
| adjusted lower bound used by the label (the smaller) | -0.503 | -0.559 |
| t-stat of avg R | -1.34 | -0.94 |
| total R | -18.54 | -10.09 |
| profit factor | 0.68 | 0.75 |
| max drawdown % realised (closed trades) | 20.09% | 14.58% |
| max drawdown % mark-to-market (4H closes) | 21.71% | 15.36% |
| max drawdown R (closed trades) | 22.71 | 16.70 |
| win rate (context only, never a target) | 28.6% | 29.3% |
| exits SL / TP / END | 69 / 28 / 1 | 41 / 17 / 0 |
| avg hold (h) | 177.8 | 112.6 |

**Label: NO-EDGE.** Train expectancy is -0.189R over 98 trades, so there is no edge to validate.

Drawdown check (CONTRACT v3 C5 / v4 D7; dd_ok = TEST mark-to-market max drawdown <= min(15%, 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length); the 15% cap is about 15 consecutive full-size losses, a streak of probability (2/3)^15 = 0.23% at the 2:1 break-even win probability p* = 1/3): **FAILED**. Test mark-to-market max drawdown 15.36% (realised closed-trade 14.58% (16.70R)) exceeds the limit 15.00% = min(15%, 95th percentile 23.62% of max drawdown over TRAIN trade sequences bootstrapped at the TEST length of 58). For comparison, TRAIN max drawdown: realised 20.09%, mark-to-market 21.71%.

## 4. Strategy discovery: TRAIN vs TEST

K = 13 variants tried: 12 legal tightenings (reward:risk {2, 2.5, 3} x volume multiple {1.5, 2} x RSI window {50-70, 55-70}) plus 1 explicit test-only variant with the R4 regime filter OFF, which is reported but can never be selected or adopted.

Selection rule: highest TRAIN t-stat among selectable variants with >= 30 TRAIN trades. rr2.5_vol2_rsi50-70_wick0.5 has the highest TRAIN t-stat (-0.77, avg -0.134R over 81 trades) among 12 eligible variants; chosen on TRAIN only, before any TEST backtest was run.

Order of operations actually run: TRAIN backtests: 13 variants, candles closed by the split only -> selection recorded: rr2.5_vol2_rsi50-70_wick0.5 -> TEST backtests: 13 variants, reported side by side only.

Only the selected variant is a pre-registered candidate (one of the m TEST looks of section 6). Every other row, including the regime-OFF test variant, is shown as `context: <label>, not judged`: picking one of them because of its TEST numbers would be selection on TEST. Context variants are not adoptable: they get no adoption record and no holdout-ledger line.

| variant | status | TRAIN n | TRAIN avg R | TRAIN 90% CI | TRAIN t | TEST n | TEST avg R | TEST 90% CI | TEST adjusted LB | TEST max DD % realised / MTM | label |
|---|---|---:|---:|---|---:|---:|---:|---|---:|---|---|
| `rr2_vol1.5_rsi50-70_wick0.5` | selectable | 98 | -0.189 | [-0.416, +0.055] | -1.34 | 58 | -0.174 | [-0.451, +0.145] | -0.559 | 14.58% / 15.36% | context: NO-EDGE, not judged |
| `rr2_vol1.5_rsi55-70_wick0.5` | selectable | 93 | -0.172 | [-0.407, +0.070] | -1.19 | 59 | -0.140 | [-0.421, +0.175] | -0.546 | 14.82% / 15.60% | context: NO-EDGE, not judged |
| `rr2_vol2_rsi50-70_wick0.5` | selectable | 89 | -0.240 | [-0.473, +0.001] | -1.65 | 53 | -0.208 | [-0.507, +0.098] | -0.615 | 13.79% / 13.80% | context: NO-EDGE, not judged |
| `rr2_vol2_rsi55-70_wick0.5` | selectable | 81 | -0.343 | [-0.576, -0.111] | -2.36 | 54 | -0.172 | [-0.473, +0.160] | -0.575 | 12.66% / 12.72% | context: NO-EDGE, not judged |
| `rr2.5_vol1.5_rsi50-70_wick0.5` | selectable | 93 | -0.146 | [-0.407, +0.126] | -0.90 | 48 | -0.255 | [-0.617, +0.122] | -0.710 | 16.44% / 16.78% | context: NO-EDGE, not judged |
| `rr2.5_vol1.5_rsi55-70_wick0.5` | selectable | 88 | -0.212 | [-0.465, +0.068] | -1.30 | 53 | -0.201 | [-0.541, +0.145] | -0.666 | 16.46% / 16.80% | context: NO-EDGE, not judged |
| `rr2.5_vol2_rsi50-70_wick0.5` | **SELECTED** | 81 | -0.134 | [-0.405, +0.163] | -0.77 | 49 | -0.202 | [-0.562, +0.171] | -0.647 | 14.05% / 14.58% | NO-EDGE |
| `rr2.5_vol2_rsi55-70_wick0.5` | selectable | 75 | -0.248 | [-0.528, +0.037] | -1.43 | 51 | -0.170 | [-0.524, +0.190] | -0.619 | 13.53% / 14.06% | context: NO-EDGE, not judged |
| `rr3_vol1.5_rsi50-70_wick0.5` | selectable | 81 | -0.267 | [-0.558, +0.040] | -1.47 | 47 | -0.124 | [-0.547, +0.307] | -0.650 | 13.20% / 13.59% | context: NO-EDGE, not judged |
| `rr3_vol1.5_rsi55-70_wick0.5` | selectable | 80 | -0.354 | [-0.622, -0.065] | -2.03 | 53 | -0.085 | [-0.472, +0.316] | -0.618 | 12.94% / 13.33% | context: NO-EDGE, not judged |
| `rr3_vol2_rsi50-70_wick0.5` | selectable | 76 | -0.252 | [-0.538, +0.059] | -1.35 | 45 | -0.085 | [-0.526, +0.366] | -0.633 | 11.39% / 11.58% | context: NO-EDGE, not judged |
| `rr3_vol2_rsi55-70_wick0.5` | selectable | 72 | -0.383 | [-0.671, -0.092] | -2.13 | 47 | -0.045 | [-0.467, +0.394] | -0.580 | 10.42% / 10.61% | context: NO-EDGE, not judged |
| `rr2_vol1.5_rsi50-70_regimeOFF_wick0.5` | TEST-ONLY (regime OFF, never selectable) | 133 | -0.340 | [-0.519, -0.150] | -2.96 | 66 | -0.191 | [-0.468, +0.091] | -0.570 | 16.77% / 17.03% | context: NO-EDGE, not judged |

**Re-selecting a variant on these TEST numbers would turn TEST into TRAIN: the selection was fixed on TRAIN before any TEST backtest ran, and it is not revisited.**

The TRAIN column of the selected variant `rr2.5_vol2_rsi50-70_wick0.5` is biased upward by the selection itself (it is the best of 12); only its TEST column is an out-of-sample test.

**Label: NO-EDGE.** Train expectancy is -0.134R over 81 trades, so there is no edge to validate.

Drawdown check (CONTRACT v3 C5 / v4 D7; dd_ok = TEST mark-to-market max drawdown <= min(15%, 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length); the 15% cap is about 15 consecutive full-size losses, a streak of probability (2/3)^15 = 0.23% at the 2:1 break-even win probability p* = 1/3): **passed**. Test mark-to-market max drawdown 14.58% (realised closed-trade 14.05% (16.74R)) is within the limit 15.00% = min(15%, 95th percentile 23.24% of max drawdown over TRAIN trade sequences bootstrapped at the TEST length of 49). For comparison, TRAIN max drawdown: realised 18.63%, mark-to-market 18.66%.

## 5. Entry layers (ML filter, expectancy guard): TRAIN vs TEST

Two pre-registered layers on the baseline rules. `base+ml` adds `L_ml_filter`, a logistic regression on 6 features (rsi, vol_ratio, ema_gap_pct, dist_regime_pct, hour_sin, hour_cos), fitted ONLY on purged TRAIN candidates and applied unchanged to TEST, so its full-TRAIN numbers are IN-SAMPLE. `base+guard` switches on `L_expectancy_guard` (`expectancy_guard=True`: a pair's risk is multiplied by 0.5 while its last 20 closed trades average below 0R); it is a fixed rule over the journal, nothing is fitted, and it needs the same walk-forward evidence as any other variant.

An entry layer can only VETO an entry that passed every mandatory rule; it never approves an entry a rule denies. A veto can free R6 budget or change R9 state, admitting other rule-compliant trades, so a layer's journal is not a subset of the base journal: both directions are counted below, matched by (pair, signal time).

A fitted layer's TRAIN gate is out of sample (CONTRACT v4 D8): its purged TRAIN candidates are split 70/30 in time order, the layer is fitted on the first 70% (purged at the inner boundary) and that inner filter is backtested on the rest of TRAIN. That out-of-sample TRAIN window is what the label judges (NO-EDGE gate, trade counts, the TRAIN drawdown bootstrap) and what the adoption record binds as the TRAIN journal; the in-sample full-TRAIN figure is context only. The model applied to TEST is still fitted on ALL purged TRAIN candidates.

| layer | window (TRAIN / TEST) | signals vetoed | entries at reduced risk (guard x < 1) | base trades absent from the layer journal | layer trades absent from the base journal | trades in both | base trades | layer trades |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `base+ml` | TRAIN | 302 | 0 | 83 | 12 | 15 | 98 | 27 |
| `base+ml` | TEST | 189 | 0 | 53 | 4 | 5 | 58 | 9 |
| `base+guard` | TRAIN | 0 | 38 | 0 | 10 | 98 | 98 | 108 |
| `base+guard` | TEST | 0 | 4 | 0 | 0 | 58 | 58 | 58 |

- `base+ml` vs `base`: TRAIN avg R -0.189 -> +0.279 (n 98 -> 27, in-sample; out-of-sample gate -1.044 (n 2)) | TEST avg R -0.174 -> -0.808 (n 58 -> 9).
- `base+guard` vs `base`: TRAIN avg R -0.189 -> -0.235 (n 98 -> 108) | TEST avg R -0.174 -> -0.174 (n 58 -> 58).

Logistic filter (l2=1) fitted on 407 TRAIN candidates (TRAIN base win rate 24.6%, context only) vetoes every rule-passing signal whose predicted win probability is at or below the break-even 0.353.

- TRAIN candidates enumerated (purged: outcome resolved by the split): 407; used for the final fit: 407 (0 skipped for missing features); TRAIN candidate win share 24.6% (context only).
- **Out-of-sample TRAIN gate (CONTRACT v4 D8):** the 407 purged TRAIN candidates were split 70/30 in time order at 2021-12-19T20:00:00Z; the inner model was fitted on 281 candidates (3 purged: signalled before the inner boundary but resolved after it); inner model fingerprint `4bfd1be02796024a`, and backtested from 2021-12-19T20:00:00Z to the split: n=2, avg -1.044R. That window is the TRAIN the label judges and the TRAIN journal the adoption record binds; the in-sample full-TRAIN figure (n=27, avg +0.279R) is context only.
- Threshold p* = break-even win probability from the TRAIN avg win +2.00R and avg loss -1.09R: p* = 0.353 (no threshold search).
- L2 penalty 1 (fixed, not tuned); intercept -1.173.
- Model fingerprint (sha256 of the canonical JSON of features, TRAIN means/stds, intercept, coefficients, threshold and l2): `3b57f3649d4cb212cbc3b6a3bb46a6ee69ff559d81b33122260beca853b5d6f7`. It is pinned in the ML adoption record (section 11); `MLFilter.from_json` re-verifies it when the live bot loads the model file.

| feature | standardised coefficient, fitted on TRAIN only and applied unchanged to TEST (log-odds per TRAIN s.d.) |
|---|---:|
| `rsi` | -0.123 |
| `vol_ratio` | -0.116 |
| `ema_gap_pct` | +0.090 |
| `dist_regime_pct` | -0.416 |
| `hour_sin` | +0.018 |
| `hour_cos` | -0.090 |

| metric | base TRAIN | base+ml TRAIN (in-sample) | base+ml TRAIN (out-of-sample inner split) | base+guard TRAIN | base TEST | base+ml TEST | base+guard TEST |
|---|---:|---:|---:|---:|---:|---:|---:|
| trades (n) | 98 | 27 | 2 | 108 | 58 | 9 | 58 |
| avg R (expectancy, the target metric) | -0.189 | +0.279 | -1.044 | -0.235 | -0.174 | -0.808 | -0.174 |
| iid bootstrap 90% CI of avg R | [-0.416, +0.055] | [-0.189, +0.748] | [-1.050, -1.039] | [-0.444, -0.025] | [-0.451, +0.145] | [-1.196, -0.124] | [-0.451, +0.145] |
| calendar-month block bootstrap 90% CI of avg R | [-0.377, -0.003] (44 months) | [-0.176, +0.775] (21 months) | [-1.050, -1.039] (2 months) | [-0.406, -0.066] (45 months) | [-0.441, +0.104] (20 months) | [-1.201, -0.094] (7 months) | [-0.441, +0.104] (20 months) |
| one-sided lower bound of avg R at 1 - alpha/m, iid / block | -0.503 / -0.451 (98.75%) | -0.396 / -0.311 (98.75%) | -1.050 / -1.050 (98.75%) | -0.518 / -0.470 (98.75%) | -0.559 / -0.550 (98.75%) | -1.223 / -1.238 (98.75%) | -0.559 / -0.550 (98.75%) |
| adjusted lower bound used by the label (the smaller) | -0.503 | -0.396 | -1.050 | -0.518 | -0.559 | -1.238 | -0.559 |
| t-stat of avg R | -1.34 | +0.92 | -192.07 | -1.79 | -0.94 | -2.29 | -0.94 |
| total R | -18.54 | +7.54 | -2.09 | -25.35 | -10.09 | -7.28 | -10.09 |
| profit factor | 0.68 | 1.03 | 0.00 | 0.63 | 0.75 | 0.15 | 0.75 |
| max drawdown % realised (closed trades) | 20.09% | 8.30% | 1.56% | 19.57% | 14.58% | 5.80% | 14.43% |
| max drawdown % mark-to-market (4H closes) | 21.71% | 8.37% | 1.75% | 21.03% | 15.36% | 5.80% | 15.36% |
| max drawdown R (closed trades) | 22.71 | 9.18 | 2.09 | 27.29 | 16.70 | 7.28 | 16.70 |
| win rate (context only, never a target) | 28.6% | 44.4% | 0.0% | 26.9% | 29.3% | 11.1% | 29.3% |
| exits SL / TP / END | 69 / 28 / 1 | 15 / 12 / 0 | 2 / 0 / 0 | 77 / 29 / 2 | 41 / 17 / 0 | 8 / 1 / 0 | 41 / 17 / 0 |
| avg hold (h) | 177.8 | 133.8 | 108.0 | 170.7 | 112.6 | 40.4 | 112.6 |

`base+ml`:

**Label: UNTESTED.** TRAIN here is the out-of-sample inner split (CONTRACT v4 D8: the layer refitted on 281 of the first 70% of the 407 purged TRAIN candidates, purged at the inner boundary 2021-12-19T20:00:00Z, and backtested from there to the split; the in-sample full-TRAIN figure is context only). Too few trades to judge: train n=2 (need 30) and test n=9 (need 30).

Drawdown check (CONTRACT v3 C5 / v4 D7; dd_ok = TEST mark-to-market max drawdown <= min(15%, 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length); the 15% cap is about 15 consecutive full-size losses, a streak of probability (2/3)^15 = 0.23% at the 2:1 break-even win probability p* = 1/3): **passed**. Test mark-to-market max drawdown 5.80% (realised closed-trade 5.80% (7.28R)) is within the limit 8.08% = min(15%, 95th percentile 8.08% of max drawdown over TRAIN trade sequences bootstrapped at the TEST length of 9). For comparison, out-of-sample gate max drawdown: realised 1.56%, mark-to-market 1.75% (in-sample TRAIN max drawdown: realised 8.30%, mark-to-market 8.37%).

`base+guard`:

**Label: NO-EDGE.** Train expectancy is -0.235R over 108 trades, so there is no edge to validate.

Drawdown check (CONTRACT v3 C5 / v4 D7; dd_ok = TEST mark-to-market max drawdown <= min(15%, 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length); the 15% cap is about 15 consecutive full-size losses, a streak of probability (2/3)^15 = 0.23% at the 2:1 break-even win probability p* = 1/3): **FAILED**. Test mark-to-market max drawdown 15.36% (realised closed-trade 14.43% (16.70R)) exceeds the limit 15.00% = min(15%, 95th percentile 21.16% of max drawdown over TRAIN trade sequences bootstrapped at the TEST length of 58). For comparison, TRAIN max drawdown: realised 19.57%, mark-to-market 21.03%.

LightGBM, other gradient boosting and neural networks are NOT justified at this sample size (407 TRAIN candidates, 6 features): a flexible model would memorise noise, so only a fixed-penalty logistic regression with a break-even threshold is allowed.

## 6. Verdict

Candidates judged (pre-registered, one TEST look each, m = 4): baseline `base`, discovery-selected `rr2.5_vol2_rsi50-70_wick0.5`, ML layer `base+ml`, expectancy guard `base+guard`. Other grid variants are excluded because choosing among them by TEST numbers would be selection on TEST. ROBUST requires at least 30 trades in each window, TRAIN and TEST avg R > 0, and a one-sided 98.75% lower bound of the TEST mean above zero for BOTH an iid and a calendar-month block bootstrap (the more conservative is used): alpha 0.05 is split over the m = 4 pre-registered candidates (base, discovery-selected, base+ml, base+guard), so when none has an edge the chance that ANY is called ROBUST is at most about 5%; a positive TEST mean that fails only the bound is UNTESTED (positive but not distinguishable from zero after multiplicity correction); the rule was fixed in advance and is never tuned on TEST outcomes. The TEST gate is reached when the judged TRAIN avg R > 0 and both windows hold >= 30 trades; the drawdown check is reported beside the label. The judged TRAIN of `base+ml` is its out-of-sample gate (CONTRACT v4 D8, section 5).

| candidate | TRAIN n (judged) | TRAIN avg R (judged) | TEST n | TEST avg R | TEST iid / block LB (1 - alpha/m) | reached the TEST gate | label | TEST MTM max DD vs limit | dd_ok |
|---|---:|---:|---:|---:|---|---|---|---|---|
| baseline `base` | 98 | -0.189 | 58 | -0.174 | -0.559 / -0.550 | no | NO-EDGE | 15.36% vs 15.00% | no |
| discovery-selected `rr2.5_vol2_rsi50-70_wick0.5` | 81 | -0.134 | 49 | -0.202 | -0.647 / -0.600 | no | NO-EDGE | 14.58% vs 15.00% | yes |
| ML layer `base+ml` | 2 | -1.044 | 9 | -0.808 | -1.223 / -1.238 | no | UNTESTED | 5.80% vs 8.08% | yes |
| expectancy guard `base+guard` | 108 | -0.235 | 58 | -0.174 | -0.559 / -0.550 | no | NO-EDGE | 15.36% vs 15.00% | no |

No robust result found. Pre-registered candidates, TRAIN | TEST: baseline `base` NO-EDGE (TRAIN -0.189R n=98 | TEST -0.174R n=58); discovery-selected `rr2.5_vol2_rsi50-70_wick0.5` NO-EDGE (TRAIN -0.134R n=81 | TEST -0.202R n=49); ML layer `base+ml` UNTESTED (TRAIN -1.044R n=2 | TEST -0.808R n=9); expectancy guard `base+guard` NO-EDGE (TRAIN -0.235R n=108 | TEST -0.174R n=58).

Holdout ledger (CONTRACT v4 D3): `test_looks.jsonl` (append-only JSON lines). This run appended 4 line(s), one per adoptable pre-registered candidate that got a TEST backtest (context discovery variants and test-only configs are never recorded). A look is a DISTINCT (config fingerprint, model fingerprint), so re-running identical candidates is not a new look. `adoption check` blocks (`ADOPT_holdout`) from HUMAN_REVIEW on when a pair's count exceeds m = 4. After a reviewer N or any other revision, a variant needs a TEST window starting at or after the latest test_end_ts of every earlier look; it is never re-run on the same TEST window.

| pair | TRAIN window (UTC) | TEST window (UTC) | cumulative distinct TEST looks on this TEST window | limit m | status |
|---|---|---|---:|---:|---|
| BNB/USDT | start .. 2023-03-14T00:00:00Z | 2023-03-14T00:00:00Z .. 2024-12-30T00:00:00Z | 4 | 4 | within the limit |
| BTC/USDT | start .. 2023-03-14T00:00:00Z | 2023-03-14T00:00:00Z .. 2024-12-30T00:00:00Z | 4 | 4 | within the limit |
| ETH/USDT | start .. 2023-03-14T00:00:00Z | 2023-03-14T00:00:00Z .. 2024-12-30T00:00:00Z | 4 | 4 | within the limit |

**Stop-fill stress (CONTRACT v4 D4), k = 0.5 versus the touch fill model k = 0**, both on this data and split (the k = 0 run is the reference; TRAIN is the judged TRAIN window). Every stressed config is test-only, so this table measures how optimistic the touch fill is, nothing here is adoptable:

| candidate | variant k=0 / k=0.5 | same config apart from k | judged TRAIN avg R k=0 | judged TRAIN avg R k=0.5 | TRAIN R shift | TEST avg R k=0 | TEST avg R k=0.5 | TEST R shift | label k=0 | label k=0.5 | label changed |
|---|---|---|---:|---:|---:|---:|---:|---:|---|---|---|
| baseline | `base` / `base` | yes | -0.138 (n 98) | -0.189 (n 98) | -0.052 | -0.121 (n 58) | -0.174 (n 58) | -0.053 | NO-EDGE | NO-EDGE | no |
| discovery-selected | `rr2.5_vol2_rsi50-70` / `rr2.5_vol2_rsi50-70_wick0.5` | yes | -0.086 (n 81) | -0.134 (n 81) | -0.048 | -0.143 (n 49) | -0.202 (n 49) | -0.059 | NO-EDGE | NO-EDGE | no |
| ML layer | `base+ml` / `base+ml` | yes | -1.000 (n 8) | -1.044 (n 2) | -0.044 | -0.294 (n 17) | -0.808 (n 9) | -0.514 | UNTESTED | UNTESTED | no |
| expectancy guard | `base+guard` / `base+guard` | yes | -0.183 (n 108) | -0.235 (n 108) | -0.052 | -0.121 (n 58) | -0.174 (n 58) | -0.053 | NO-EDGE | NO-EDGE | no |

## 7. Why signals were rejected (decision counts per rule)

Signal candles denied per rule (the FIRST failing rule is counted, so the R1-R4 rows include every candle that was simply not a signal). Exits are never gated. TRAIN is the full TRAIN backtest (in-sample for the ML layer).

| rule | base TRAIN | base TEST | rr2.5_vol2_rsi50-70_wick0.5 TRAIN | rr2.5_vol2_rsi50-70_wick0.5 TEST | base+ml TRAIN | base+ml TEST | base+guard TRAIN | base+guard TEST | meaning |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `R1_trend` | 18810 | 7617 | 18810 | 7617 | 18810 | 7617 | 18810 | 7617 | Trend gate: close > EMA9 and close > EMA21 and EMA9 > EMA21 (4H) |
| `R2_momentum` | 1187 | 758 | 1187 | 758 | 1187 | 758 | 1187 | 758 | Momentum: RSI(14) within [50, 70] on the entry candle |
| `R3_volume` | 6920 | 3134 | 7172 | 3252 | 6920 | 3134 | 6920 | 3134 | Volume: entry-candle volume >= 1.5x the previous-20-candle average |
| `R4_regime` | 254 | 97 | 156 | 56 | 254 | 97 | 254 | 97 | Regime: close > EMA200 (unless explicitly testing it off) |
| `R5_news_blackout` | 13 | 8 | 10 | 6 | 13 | 8 | 13 | 8 | No entries within +/-2h of high-impact news; BNB +/-24h burns/launchpool |
| `R6_correlation_cap` | 312 | 152 | 178 | 87 | 80 | 12 | 302 | 152 | BTC/ETH/BNB share one risk budget; BNB never stacks |
| `R7_position_risk` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Risk <= 1% (BTC/ETH) / 0.5% (BNB); size derived from stop distance |
| `R8_structure_stop` | 0 | 2 | 0 | 0 | 1 | 2 | 0 | 2 | Stop behind a confirmed swing low with a buffer (BNB 0.5-0.8%) |
| `R9_circuit_breaker` | 0 | 0 | 0 | 1 | 0 | 0 | 0 | 0 | 3 consecutive SLs bench a pair 24h; 7d realized loss limit halts all |
| `L_ml_filter` | 0 | 0 | 0 | 0 | 302 | 189 | 0 | 0 | Layer: logistic-regression veto; it can only veto an entry that passed every mandatory rule and never approves one a rule denies (a veto can free R6 budget or change R9 state, which may admit other rule-compliant trades) |
| `L_expectancy_guard` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Layer: halve a pair's risk while its last-N-trade expectancy < 0 |
| `X_capital` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Execution: not enough free capital / size below minimum |
| candles evaluated | 27594 | 11826 | 27594 | 11826 | 27594 | 11826 | 27594 | 11826 | |
| trades taken | 98 | 58 | 81 | 49 | 27 | 9 | 108 | 58 | |

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
| TRAIN at 2023-03-14T00:00:00Z (equity 8,242.75) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 9,047.57) | - | - | none | No active adaptations. | - |

**discovery-selected `rr2.5_vol2_rsi50-70_wick0.5`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 8,576.63) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 9,125.19) | - | - | none | No active adaptations. | - |

**ML layer `base+ml`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 9,843.63) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 9,420.03) | - | - | none | No active adaptations. | - |

**expectancy guard `base+guard`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 8,149.28) | L_expectancy_guard | BTC/USDT | risk x0.5 | BTC/USDT's last 20 closed trades average -0.261R (below 0), so new BTC/USDT entries risk 0.5x the normal amount until that average is back at or above 0. | #58, #63, #64, #72, #78, #81, #83, #86, #87, #89, #90, #91, #92, #93, #95, #96, #98, #102, #104, #108 |
| TRAIN at 2023-03-14T00:00:00Z (equity 8,149.28) | L_expectancy_guard | ETH/USDT | risk x0.5 | ETH/USDT's last 20 closed trades average -0.422R (below 0), so new ETH/USDT entries risk 0.5x the normal amount until that average is back at or above 0. | #49, #50, #55, #59, #61, #62, #67, #74, #75, #76, #77, #79, #84, #85, #88, #94, #97, #101, #103, #107 |
| TEST at 2024-12-30T00:00:00Z (equity 9,102.63) | L_expectancy_guard | ETH/USDT | risk x0.5 | ETH/USDT's last 20 closed trades average -0.301R (below 0), so new ETH/USDT entries risk 0.5x the normal amount until that average is back at or above 0. | #122, #124, #125, #128, #131, #132, #135, #137, #139, #141, #144, #146, #147, #148, #152, #153, #158, #159, #164, #166 |

Reproduce the baseline TEST row with `python -m research.trendbot.journal_rules --journal research/results/stress_k0.5/synthetic_null_s1/journals/base_test.csv --equity 9047.57 --now 2024-12-30T00:00:00Z --backtest-journal`.

## 10. Journals and human review packs

Trade journals (`journal.write_journal`, one CSV per window) for the 4 pre-registered candidates that ran, the variants whose TRAIN and TEST columns this report shows in full. The other discovery variants appear only as one context row each in section 4, so no journal is kept for them (the same command regenerates them). TEST trade ids are offset past the TRAIN ids, so ids are unique across a variant's two journals and its review pack. The TRAIN journal is the judged TRAIN window (for a fitted layer its D8 out-of-sample gate; its in-sample full-TRAIN journal is kept as context and bound by no record).

| variant | TRAIN journal (judged) | TEST journal | in-sample full-TRAIN journal (context) |
|---|---|---|---|
| `base` | [journals/base_train.csv](journals/base_train.csv) | [journals/base_test.csv](journals/base_test.csv) | - |
| `rr2.5_vol2_rsi50-70_wick0.5` | [journals/rr2.5_vol2_rsi50-70_wick0.5_train.csv](journals/rr2.5_vol2_rsi50-70_wick0.5_train.csv) | [journals/rr2.5_vol2_rsi50-70_wick0.5_test.csv](journals/rr2.5_vol2_rsi50-70_wick0.5_test.csv) | - |
| `base+ml` | [journals/base_plus_ml_train.csv](journals/base_plus_ml_train.csv) | [journals/base_plus_ml_test.csv](journals/base_plus_ml_test.csv) | [journals/base_plus_ml_train_insample.csv](journals/base_plus_ml_train_insample.csv) |
| `base+guard` | [journals/base_plus_guard_train.csv](journals/base_plus_guard_train.csv) | [journals/base_plus_guard_test.csv](journals/base_plus_guard_test.csv) | - |

Human review packs (`review_sheet.write_review_pack`, TRAIN and TEST):

- no pack for `base`, `rr2.5_vol2_rsi50-70_wick0.5`, `base+ml`, `base+guard`: a test-only config (CONTRACT v4 D1, `ADOPT_test_only` blocks it from HUMAN_REVIEW on), so a review pack could never be used; its journals above hold every trade.

## 11. Adoption path and current stage

The only path to real money (enforced by `adoption.py`; no step can be skipped, the promoted config must match the tested config's sha256 fingerprint, and an ML layer's model must match the tested model's fingerprint):

backtest -> walk-forward (ROBUST + drawdown check) -> human review of EVERY trade -> >= 2 weeks on Binance testnet with zero rule violations -> live.

Stages: BACKTEST -> WALK_FORWARD -> HUMAN_REVIEW -> TESTNET -> LIVE. Stage reached by this run: **WALK_FORWARD** (recorded). Next: **HUMAN_REVIEW**.

**No adoption record was written: every config of this run is test-only (stop-fill stress, CONTRACT v4 D4).** Test-only and context variants are not adoptable: they get no promotable record, and `adoption check` blocks any record for one (`ADOPT_test_only` from HUMAN_REVIEW on, `ADOPT_holdout` 'context variant').

## 12. Risk disclaimer

This is a research and testing tool, not financial advice and not a recommendation to trade. Backtests and synthetic worlds are simplified models; past or simulated results do not predict future results. Crypto trading can result in the total loss of the capital used.
