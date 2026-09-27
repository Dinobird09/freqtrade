# Walk-forward research report: synthetic world `zero_edge` seed 1, STOP-FILL STRESS k = 1 (test-only)

## 1. No win rate is promised or targeted

This report does NOT promise, target or optimise a win rate, and nothing in this package selects on one. A win rate is meaningless without the reward:risk it was earned at: with the mandatory minimum of 2:1 a strategy breaks even at about 33% winners before costs, while a 90% win rate with a 1:9 payoff still loses money. A backtest win rate near 90% is evidence of curve-fitting or look-ahead, not of skill. The target metric is POSITIVE EXPECTANCY (average R per trade, net of fees and slippage) that survives a 70/30 chronological walk-forward with controlled drawdown, with every trade planned at reward:risk >= 2:1. Win rate appears below only as context and is labelled 'context only' wherever it is shown.

## 2. Data provenance

- Source: **SYNTHETIC** world `zero_edge`, seed 1, 6 years of 4H candles (13140 per pair) from 2019-01-01T00:00:00Z, pairs BTC/USDT, ETH/USDT, BNB/USDT (`synthetic.generate_world`); adoption provenance `synthetic:zero_edge:1`.
- Ground truth: the planted mechanism at 0.15 sigma per candle: a real, positive PRE-cost edge that fees and slippage eat, so the base strategy's expected NET R is ~0 in both windows (the H0 boundary).
- What the ground truth predicts: nothing should be ROBUST: an edge of ~0R after costs is not worth trading, so every ROBUST label here is a false positive at the boundary of the null hypothesis.
- Qualifying triggers planted per pair (before / after the 70% split): BTC/USDT 344 / 143, ETH/USDT 336 / 129, BNB/USDT 317 / 114.
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
- Measured on the baseline's TRAIN and TEST trades: fees alone cost 0.06R per trade on average (median stop distance 3.79% of the entry price), so the expectancy below is only as good as the fee rate above.

## 3. Baseline: TRAIN vs TEST

Default mandate config `rr2_vol1.5_rsi50-70_wick1` (variant `base`). TRAIN window start .. 2023-03-14T00:00:00Z; TEST window 2023-03-14T00:00:00Z .. 2024-12-30T00:00:00Z.

| metric | TRAIN | TEST |
|---|---:|---:|
| trades (n) | 94 | 60 |
| avg R (expectancy, the target metric) | -0.091 | -0.167 |
| iid bootstrap 90% CI of avg R | [-0.330, +0.163] | [-0.468, +0.161] |
| calendar-month block bootstrap 90% CI of avg R | [-0.256, +0.075] (44 months) | [-0.428, +0.116] (20 months) |
| one-sided lower bound of avg R at 1 - alpha/m, iid / block | -0.412 / -0.323 (98.75%) | -0.570 / -0.504 (98.75%) |
| adjusted lower bound used by the label (the smaller) | -0.412 | -0.570 |
| t-stat of avg R | -0.60 | -0.86 |
| total R | -8.60 | -9.99 |
| profit factor | 0.89 | 0.73 |
| max drawdown % realised (closed trades) | 8.67% | 14.95% |
| max drawdown % mark-to-market (4H closes) | 9.16% | 15.04% |
| max drawdown R (closed trades) | 11.16 | 17.34 |
| win rate (context only, never a target) | 33.0% | 31.7% |
| exits SL / TP / END | 62 / 31 / 1 | 41 / 19 / 0 |
| avg hold (h) | 171.0 | 112.0 |

**Label: NO-EDGE.** Train expectancy is -0.091R over 94 trades, so there is no edge to validate.

Drawdown check (CONTRACT v3 C5 / v4 D7; dd_ok = TEST mark-to-market max drawdown <= min(15%, 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length); the 15% cap is about 15 consecutive full-size losses, a streak of probability (2/3)^15 = 0.23% at the 2:1 break-even win probability p* = 1/3): **FAILED**. Test mark-to-market max drawdown 15.04% (realised closed-trade 14.95% (17.34R)) exceeds the limit 15.00% = min(15%, 95th percentile 20.01% of max drawdown over TRAIN trade sequences bootstrapped at the TEST length of 60). For comparison, TRAIN max drawdown: realised 8.67%, mark-to-market 9.16%.

## 4. Strategy discovery: TRAIN vs TEST

K = 13 variants tried: 12 legal tightenings (reward:risk {2, 2.5, 3} x volume multiple {1.5, 2} x RSI window {50-70, 55-70}) plus 1 explicit test-only variant with the R4 regime filter OFF, which is reported but can never be selected or adopted.

Selection rule: highest TRAIN t-stat among selectable variants with >= 30 TRAIN trades. rr2_vol2_rsi55-70_wick1 has the highest TRAIN t-stat (-0.17, avg -0.029R over 75 trades) among 12 eligible variants; chosen on TRAIN only, before any TEST backtest was run.

Order of operations actually run: TRAIN backtests: 13 variants, candles closed by the split only -> selection recorded: rr2_vol2_rsi55-70_wick1 -> TEST backtests: 13 variants, reported side by side only.

Only the selected variant is a pre-registered candidate (one of the m TEST looks of section 6). Every other row, including the regime-OFF test variant, is shown as `context: <label>, not judged`: picking one of them because of its TEST numbers would be selection on TEST. Context variants are not adoptable: they get no adoption record and no holdout-ledger line.

| variant | status | TRAIN n | TRAIN avg R | TRAIN 90% CI | TRAIN t | TEST n | TEST avg R | TEST 90% CI | TEST adjusted LB | TEST max DD % realised / MTM | label |
|---|---|---:|---:|---|---:|---:|---:|---|---:|---|---|
| `rr2_vol1.5_rsi50-70_wick1` | selectable | 94 | -0.091 | [-0.330, +0.163] | -0.60 | 60 | -0.167 | [-0.468, +0.161] | -0.570 | 14.95% / 15.04% | context: NO-EDGE, not judged |
| `rr2_vol1.5_rsi55-70_wick1` | selectable | 87 | -0.038 | [-0.293, +0.227] | -0.23 | 56 | -0.202 | [-0.508, +0.132] | -0.620 | 15.44% / 15.64% | context: NO-EDGE, not judged |
| `rr2_vol2_rsi50-70_wick1` | selectable | 80 | -0.069 | [-0.337, +0.205] | -0.41 | 51 | -0.154 | [-0.508, +0.206] | -0.622 | 11.74% / 11.93% | context: NO-EDGE, not judged |
| `rr2_vol2_rsi55-70_wick1` | **SELECTED** | 75 | -0.029 | [-0.312, +0.251] | -0.17 | 49 | -0.153 | [-0.510, +0.205] | -0.611 | 9.86% / 9.94% | NO-EDGE |
| `rr2.5_vol1.5_rsi50-70_wick1` | selectable | 89 | -0.255 | [-0.525, +0.023] | -1.51 | 49 | -0.252 | [-0.607, +0.128] | -0.702 | 15.74% / 15.74% | context: NO-EDGE, not judged |
| `rr2.5_vol1.5_rsi55-70_wick1` | selectable | 82 | -0.211 | [-0.490, +0.090] | -1.19 | 47 | -0.215 | [-0.594, +0.185] | -0.709 | 14.00% / 14.00% | context: NO-EDGE, not judged |
| `rr2.5_vol2_rsi50-70_wick1` | selectable | 76 | -0.300 | [-0.588, +0.003] | -1.66 | 43 | -0.250 | [-0.647, +0.176] | -0.775 | 13.35% / 13.35% | context: NO-EDGE, not judged |
| `rr2.5_vol2_rsi55-70_wick1` | selectable | 72 | -0.295 | [-0.597, +0.022] | -1.59 | 43 | -0.246 | [-0.649, +0.171] | -0.758 | 12.73% / 12.73% | context: NO-EDGE, not judged |
| `rr3_vol1.5_rsi50-70_wick1` | selectable | 67 | -0.231 | [-0.560, +0.124] | -1.10 | 48 | -0.264 | [-0.654, +0.159] | -0.778 | 17.21% / 17.21% | context: NO-EDGE, not judged |
| `rr3_vol1.5_rsi55-70_wick1` | selectable | 62 | -0.142 | [-0.506, +0.228] | -0.64 | 45 | -0.209 | [-0.608, +0.235] | -0.751 | 14.62% / 14.62% | context: NO-EDGE, not judged |
| `rr3_vol2_rsi50-70_wick1` | selectable | 64 | -0.266 | [-0.608, +0.087] | -1.24 | 43 | -0.211 | [-0.654, +0.247] | -0.799 | 14.45% / 14.45% | context: NO-EDGE, not judged |
| `rr3_vol2_rsi55-70_wick1` | selectable | 60 | -0.195 | [-0.550, +0.177] | -0.87 | 43 | -0.207 | [-0.654, +0.239] | -0.783 | 13.30% / 13.30% | context: NO-EDGE, not judged |
| `rr2_vol1.5_rsi50-70_regimeOFF_wick1` | TEST-ONLY (regime OFF, never selectable) | 138 | -0.161 | [-0.360, +0.043] | -1.29 | 71 | -0.134 | [-0.416, +0.167] | -0.522 | 14.86% / 14.96% | context: NO-EDGE, not judged |

**Re-selecting a variant on these TEST numbers would turn TEST into TRAIN: the selection was fixed on TRAIN before any TEST backtest ran, and it is not revisited.**

The TRAIN column of the selected variant `rr2_vol2_rsi55-70_wick1` is biased upward by the selection itself (it is the best of 12); only its TEST column is an out-of-sample test.

**Label: NO-EDGE.** Train expectancy is -0.029R over 75 trades, so there is no edge to validate.

Drawdown check (CONTRACT v3 C5 / v4 D7; dd_ok = TEST mark-to-market max drawdown <= min(15%, 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length); the 15% cap is about 15 consecutive full-size losses, a streak of probability (2/3)^15 = 0.23% at the 2:1 break-even win probability p* = 1/3): **passed**. Test mark-to-market max drawdown 9.94% (realised closed-trade 9.86% (13.44R)) is within the limit 15.00% = min(15%, 95th percentile 18.81% of max drawdown over TRAIN trade sequences bootstrapped at the TEST length of 49). For comparison, TRAIN max drawdown: realised 7.45%, mark-to-market 7.80%.

## 5. Entry layers (ML filter, expectancy guard): TRAIN vs TEST

Two pre-registered layers on the baseline rules. `base+ml` adds `L_ml_filter`, a logistic regression on 6 features (rsi, vol_ratio, ema_gap_pct, dist_regime_pct, hour_sin, hour_cos), fitted ONLY on purged TRAIN candidates and applied unchanged to TEST, so its full-TRAIN numbers are IN-SAMPLE. `base+guard` switches on `L_expectancy_guard` (`expectancy_guard=True`: a pair's risk is multiplied by 0.5 while its last 20 closed trades average below 0R); it is a fixed rule over the journal, nothing is fitted, and it needs the same walk-forward evidence as any other variant.

An entry layer can only VETO an entry that passed every mandatory rule; it never approves an entry a rule denies. A veto can free R6 budget or change R9 state, admitting other rule-compliant trades, so a layer's journal is not a subset of the base journal: both directions are counted below, matched by (pair, signal time).

A fitted layer's TRAIN gate is out of sample (CONTRACT v4 D8): its purged TRAIN candidates are split 70/30 in time order, the layer is fitted on the first 70% (purged at the inner boundary) and that inner filter is backtested on the rest of TRAIN. That out-of-sample TRAIN window is what the label judges (NO-EDGE gate, trade counts, the TRAIN drawdown bootstrap) and what the adoption record binds as the TRAIN journal; the in-sample full-TRAIN figure is context only. The model applied to TEST is still fitted on ALL purged TRAIN candidates.

| layer | window (TRAIN / TEST) | signals vetoed | entries at reduced risk (guard x < 1) | base trades absent from the layer journal | layer trades absent from the base journal | trades in both | base trades | layer trades |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| `base+ml` | TRAIN | 375 | 0 | 94 | 0 | 0 | 94 | 0 |
| `base+ml` | TEST | 205 | 0 | 60 | 0 | 0 | 60 | 0 |
| `base+guard` | TRAIN | 0 | 38 | 1 | 9 | 93 | 94 | 102 |
| `base+guard` | TEST | 0 | 3 | 0 | 0 | 60 | 60 | 60 |

- `base+ml` vs `base`: TRAIN avg R -0.091 -> +0.000 (n 94 -> 0, in-sample; out-of-sample gate +0.000 (n 0)) | TEST avg R -0.167 -> +0.000 (n 60 -> 0).
- `base+guard` vs `base`: TRAIN avg R -0.091 -> -0.135 (n 94 -> 102) | TEST avg R -0.167 -> -0.167 (n 60 -> 60).

Logistic filter (l2=1) fitted on 371 TRAIN candidates (TRAIN base win rate 24.0%, context only) vetoes every rule-passing signal whose predicted win probability is at or below the break-even 0.371.

- TRAIN candidates enumerated (purged: outcome resolved by the split): 371; used for the final fit: 371 (0 skipped for missing features); TRAIN candidate win share 24.0% (context only).
- **Out-of-sample TRAIN gate (CONTRACT v4 D8):** the 371 purged TRAIN candidates were split 70/30 in time order at 2022-02-05T04:00:00Z; the inner model was fitted on 255 candidates (3 purged: signalled before the inner boundary but resolved after it); inner model fingerprint `fbda9dcdadb3d7be`, and backtested from 2022-02-05T04:00:00Z to the split: n=0, avg +0.000R. That window is the TRAIN the label judges and the TRAIN journal the adoption record binds; the in-sample full-TRAIN figure (n=0, avg +0.000R) is context only.
- Threshold p* = break-even win probability from the TRAIN avg win +2.00R and avg loss -1.18R: p* = 0.371 (no threshold search).
- L2 penalty 1 (fixed, not tuned); intercept -1.172.
- Model fingerprint (sha256 of the canonical JSON of features, TRAIN means/stds, intercept, coefficients, threshold and l2): `c99e6a5d94b7c1581f6b2592f123e787579ea24b4f451380a2c2049ce50ec5e2`. It is pinned in the ML adoption record (section 11); `MLFilter.from_json` re-verifies it when the live bot loads the model file.

| feature | standardised coefficient, fitted on TRAIN only and applied unchanged to TEST (log-odds per TRAIN s.d.) |
|---|---:|
| `rsi` | -0.079 |
| `vol_ratio` | +0.122 |
| `ema_gap_pct` | +0.024 |
| `dist_regime_pct` | -0.211 |
| `hour_sin` | -0.034 |
| `hour_cos` | +0.001 |

| metric | base TRAIN | base+ml TRAIN (in-sample) | base+ml TRAIN (out-of-sample inner split) | base+guard TRAIN | base TEST | base+ml TEST | base+guard TEST |
|---|---:|---:|---:|---:|---:|---:|---:|
| trades (n) | 94 | 0 | 0 | 102 | 60 | 0 | 60 |
| avg R (expectancy, the target metric) | -0.091 | +0.000 | +0.000 | -0.135 | -0.167 | +0.000 | -0.167 |
| iid bootstrap 90% CI of avg R | [-0.330, +0.163] | n/a | n/a | [-0.371, +0.103] | [-0.468, +0.161] | n/a | [-0.468, +0.161] |
| calendar-month block bootstrap 90% CI of avg R | [-0.256, +0.075] (44 months) | n/a | n/a | [-0.289, +0.023] (44 months) | [-0.428, +0.116] (20 months) | n/a | [-0.428, +0.116] (20 months) |
| one-sided lower bound of avg R at 1 - alpha/m, iid / block | -0.412 / -0.323 (98.75%) | n/a | n/a | -0.454 / -0.350 (98.75%) | -0.570 / -0.504 (98.75%) | n/a | -0.570 / -0.504 (98.75%) |
| adjusted lower bound used by the label (the smaller) | -0.412 | +0.000 | +0.000 | -0.454 | -0.570 | +0.000 | -0.570 |
| t-stat of avg R | -0.60 | +0.00 | +0.00 | -0.93 | -0.86 | +0.00 | -0.86 |
| total R | -8.60 | +0.00 | +0.00 | -13.76 | -9.99 | +0.00 | -9.99 |
| profit factor | 0.89 | n/a (no losses) | n/a (no losses) | 0.83 | 0.73 | n/a (no losses) | 0.73 |
| max drawdown % realised (closed trades) | 8.67% | 0.00% | 0.00% | 9.47% | 14.95% | 0.00% | 14.99% |
| max drawdown % mark-to-market (4H closes) | 9.16% | 0.00% | 0.00% | 9.87% | 15.04% | 0.00% | 15.20% |
| max drawdown R (closed trades) | 11.16 | 0.00 | 0.00 | 16.20 | 17.34 | 0.00 | 17.34 |
| win rate (context only, never a target) | 33.0% | n/a | n/a | 31.4% | 31.7% | n/a | 31.7% |
| exits SL / TP / END | 62 / 31 / 1 | 0 / 0 / 0 | 0 / 0 / 0 | 68 / 32 / 2 | 41 / 19 / 0 | 0 / 0 / 0 | 41 / 19 / 0 |
| avg hold (h) | 171.0 | 0.0 | 0.0 | 163.2 | 112.0 | 0.0 | 112.0 |

`base+ml`:

**Label: UNTESTED.** TRAIN here is the out-of-sample inner split (CONTRACT v4 D8: the layer refitted on 255 of the first 70% of the 371 purged TRAIN candidates, purged at the inner boundary 2022-02-05T04:00:00Z, and backtested from there to the split; the in-sample full-TRAIN figure is context only). Too few trades to judge: train n=0 (need 30) and test n=0 (need 30).

Drawdown check (CONTRACT v3 C5 / v4 D7; dd_ok = TEST mark-to-market max drawdown <= min(15%, 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length); the 15% cap is about 15 consecutive full-size losses, a streak of probability (2/3)^15 = 0.23% at the 2:1 break-even win probability p* = 1/3): **passed**. No test trades were taken, so no drawdown was observed against the limit 15.00% (min of 15% and 15%). For comparison, out-of-sample gate max drawdown: realised 0.00%, mark-to-market 0.00% (in-sample TRAIN max drawdown: realised 0.00%, mark-to-market 0.00%).

`base+guard`:

**Label: NO-EDGE.** Train expectancy is -0.135R over 102 trades, so there is no edge to validate.

Drawdown check (CONTRACT v3 C5 / v4 D7; dd_ok = TEST mark-to-market max drawdown <= min(15%, 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length); the 15% cap is about 15 consecutive full-size losses, a streak of probability (2/3)^15 = 0.23% at the 2:1 break-even win probability p* = 1/3): **FAILED**. Test mark-to-market max drawdown 15.20% (realised closed-trade 14.99% (17.34R)) exceeds the limit 15.00% = min(15%, 95th percentile 18.58% of max drawdown over TRAIN trade sequences bootstrapped at the TEST length of 60). For comparison, TRAIN max drawdown: realised 9.47%, mark-to-market 9.87%.

LightGBM, other gradient boosting and neural networks are NOT justified at this sample size (371 TRAIN candidates, 6 features): a flexible model would memorise noise, so only a fixed-penalty logistic regression with a break-even threshold is allowed.

## 6. Verdict

Candidates judged (pre-registered, one TEST look each, m = 4): baseline `base`, discovery-selected `rr2_vol2_rsi55-70_wick1`, ML layer `base+ml`, expectancy guard `base+guard`. Other grid variants are excluded because choosing among them by TEST numbers would be selection on TEST. ROBUST requires at least 30 trades in each window, TRAIN and TEST avg R > 0, and a one-sided 98.75% lower bound of the TEST mean above zero for BOTH an iid and a calendar-month block bootstrap (the more conservative is used): alpha 0.05 is split over the m = 4 pre-registered candidates (base, discovery-selected, base+ml, base+guard), so when none has an edge the chance that ANY is called ROBUST is at most about 5%; a positive TEST mean that fails only the bound is UNTESTED (positive but not distinguishable from zero after multiplicity correction); the rule was fixed in advance and is never tuned on TEST outcomes. The TEST gate is reached when the judged TRAIN avg R > 0 and both windows hold >= 30 trades; the drawdown check is reported beside the label. The judged TRAIN of `base+ml` is its out-of-sample gate (CONTRACT v4 D8, section 5).

| candidate | TRAIN n (judged) | TRAIN avg R (judged) | TEST n | TEST avg R | TEST iid / block LB (1 - alpha/m) | reached the TEST gate | label | TEST MTM max DD vs limit | dd_ok |
|---|---:|---:|---:|---:|---|---|---|---|---|
| baseline `base` | 94 | -0.091 | 60 | -0.167 | -0.570 / -0.504 | no | NO-EDGE | 15.04% vs 15.00% | no |
| discovery-selected `rr2_vol2_rsi55-70_wick1` | 75 | -0.029 | 49 | -0.153 | -0.611 / -0.546 | no | NO-EDGE | 9.94% vs 15.00% | yes |
| ML layer `base+ml` | 0 | +0.000 | 0 | +0.000 | n/a | no | UNTESTED | 0.00% vs 15.00% | yes |
| expectancy guard `base+guard` | 102 | -0.135 | 60 | -0.167 | -0.570 / -0.504 | no | NO-EDGE | 15.20% vs 15.00% | no |

No robust result found. Pre-registered candidates, TRAIN | TEST: baseline `base` NO-EDGE (TRAIN -0.091R n=94 | TEST -0.167R n=60); discovery-selected `rr2_vol2_rsi55-70_wick1` NO-EDGE (TRAIN -0.029R n=75 | TEST -0.153R n=49); ML layer `base+ml` UNTESTED (TRAIN +0.000R n=0 | TEST +0.000R n=0); expectancy guard `base+guard` NO-EDGE (TRAIN -0.135R n=102 | TEST -0.167R n=60).

Holdout ledger (CONTRACT v4 D3): `test_looks.jsonl` (append-only JSON lines). This run appended 4 line(s), one per adoptable pre-registered candidate that got a TEST backtest (context discovery variants and test-only configs are never recorded). A look is a DISTINCT (config fingerprint, model fingerprint), so re-running identical candidates is not a new look. `adoption check` blocks (`ADOPT_holdout`) from HUMAN_REVIEW on when a pair's count exceeds m = 4. After a reviewer N or any other revision, a variant needs a TEST window starting at or after the latest test_end_ts of every earlier look; it is never re-run on the same TEST window.

| pair | TRAIN window (UTC) | TEST window (UTC) | cumulative distinct TEST looks on this TEST window | limit m | status |
|---|---|---|---:|---:|---|
| BNB/USDT | start .. 2023-03-14T00:00:00Z | 2023-03-14T00:00:00Z .. 2024-12-30T00:00:00Z | 4 | 4 | within the limit |
| BTC/USDT | start .. 2023-03-14T00:00:00Z | 2023-03-14T00:00:00Z .. 2024-12-30T00:00:00Z | 4 | 4 | within the limit |
| ETH/USDT | start .. 2023-03-14T00:00:00Z | 2023-03-14T00:00:00Z .. 2024-12-30T00:00:00Z | 4 | 4 | within the limit |

**Stop-fill stress (CONTRACT v4 D4), k = 1 versus the touch fill model k = 0**, both on this data and split (the k = 0 run is the reference; TRAIN is the judged TRAIN window). Every stressed config is test-only, so this table measures how optimistic the touch fill is, nothing here is adoptable:

| candidate | variant k=0 / k=1 | same config apart from k | judged TRAIN avg R k=0 | judged TRAIN avg R k=1 | TRAIN R shift | TEST avg R k=0 | TEST avg R k=1 | TEST R shift | label k=0 | label k=1 | label changed |
|---|---|---|---:|---:|---:|---:|---:|---:|---|---|---|
| baseline | `base` / `base` | yes | -0.004 (n 94) | -0.091 (n 94) | -0.087 | -0.050 (n 60) | -0.167 (n 60) | -0.117 | NO-EDGE | NO-EDGE | no |
| discovery-selected | `rr2_vol2_rsi55-70` / `rr2_vol2_rsi55-70_wick1` | yes | +0.048 (n 75) | -0.029 (n 75) | -0.077 | -0.020 (n 49) | -0.153 (n 49) | -0.133 | TRAIN-ONLY | NO-EDGE | yes |
| ML layer | `base+ml` / `base+ml` | yes | +0.000 (n 0) | +0.000 (n 0) | +0.000 | +0.500 (n 6) | +0.000 (n 0) | -0.500 | UNTESTED | UNTESTED | no |
| expectancy guard | `base+guard` / `base+guard` | yes | -0.044 (n 102) | -0.135 (n 102) | -0.091 | -0.050 (n 60) | -0.167 (n 60) | -0.117 | NO-EDGE | NO-EDGE | no |

## 7. Why signals were rejected (decision counts per rule)

Signal candles denied per rule (the FIRST failing rule is counted, so the R1-R4 rows include every candle that was simply not a signal). Exits are never gated. TRAIN is the full TRAIN backtest (in-sample for the ML layer).

| rule | base TRAIN | base TEST | rr2_vol2_rsi55-70_wick1 TRAIN | rr2_vol2_rsi55-70_wick1 TEST | base+ml TRAIN | base+ml TEST | base+guard TRAIN | base+guard TEST | meaning |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `R1_trend` | 18699 | 7667 | 18699 | 7667 | 18699 | 7667 | 18699 | 7667 | Trend gate: close > EMA9 and close > EMA21 and EMA9 > EMA21 (4H) |
| `R2_momentum` | 1369 | 789 | 2635 | 1311 | 1369 | 789 | 1369 | 789 | Momentum: RSI(14) within [50, 70] on the entry candle |
| `R3_volume` | 6884 | 3070 | 5918 | 2687 | 6884 | 3070 | 6884 | 3070 | Volume: entry-candle volume >= 1.5x the previous-20-candle average |
| `R4_regime` | 252 | 87 | 120 | 35 | 252 | 87 | 252 | 87 | Regime: close > EMA200 (unless explicitly testing it off) |
| `R5_news_blackout` | 14 | 7 | 10 | 5 | 14 | 7 | 14 | 7 | No entries within +/-2h of high-impact news; BNB +/-24h burns/launchpool |
| `R6_correlation_cap` | 282 | 145 | 137 | 72 | 0 | 0 | 274 | 145 | BTC/ETH/BNB share one risk budget; BNB never stacks |
| `R7_position_risk` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Risk <= 1% (BTC/ETH) / 0.5% (BNB); size derived from stop distance |
| `R8_structure_stop` | 0 | 1 | 0 | 0 | 1 | 1 | 0 | 1 | Stop behind a confirmed swing low with a buffer (BNB 0.5-0.8%) |
| `R9_circuit_breaker` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 3 consecutive SLs bench a pair 24h; 7d realized loss limit halts all |
| `L_ml_filter` | 0 | 0 | 0 | 0 | 375 | 205 | 0 | 0 | Layer: logistic-regression veto; it can only veto an entry that passed every mandatory rule and never approves one a rule denies (a veto can free R6 budget or change R9 state, which may admit other rule-compliant trades) |
| `L_expectancy_guard` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Layer: halve a pair's risk while its last-N-trade expectancy < 0 |
| `X_capital` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | Execution: not enough free capital / size below minimum |
| candles evaluated | 27594 | 11826 | 27594 | 11826 | 27594 | 11826 | 27594 | 11826 | |
| trades taken | 94 | 60 | 75 | 49 | 0 | 0 | 102 | 60 | |

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
| TRAIN at 2023-03-14T00:00:00Z (equity 9,414.89) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 8,963.43) | - | - | none | No active adaptations. | - |

**discovery-selected `rr2_vol2_rsi55-70_wick1`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 9,619.63) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 9,464.77) | - | - | none | No active adaptations. | - |

**ML layer `base+ml`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 10,000.00) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 10,000.00) | - | - | none | No active adaptations. | - |

**expectancy guard `base+guard`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 9,194.71) | L_expectancy_guard | BNB/USDT | risk x0.5 | BNB/USDT's last 20 closed trades average -0.344R (below 0), so new BNB/USDT entries risk 0.5x the normal amount until that average is back at or above 0. | #38, #41, #43, #46, #48, #49, #50, #51, #54, #56, #62, #63, #69, #70, #72, #84, #87, #91, #96, #99 |
| TRAIN at 2023-03-14T00:00:00Z (equity 9,194.71) | L_expectancy_guard | BTC/USDT | risk x0.5 | BTC/USDT's last 20 closed trades average -0.160R (below 0), so new BTC/USDT entries risk 0.5x the normal amount until that average is back at or above 0. | #60, #61, #66, #71, #74, #77, #79, #80, #81, #82, #85, #86, #88, #89, #93, #94, #95, #98, #100, #102 |
| TRAIN at 2023-03-14T00:00:00Z (equity 9,194.71) | L_expectancy_guard | ETH/USDT | risk x0.5 | ETH/USDT's last 20 closed trades average -0.280R (below 0), so new ETH/USDT entries risk 0.5x the normal amount until that average is back at or above 0. | #32, #34, #40, #45, #53, #55, #57, #64, #65, #67, #68, #73, #75, #76, #78, #83, #90, #92, #97, #101 |
| TEST at 2024-12-30T00:00:00Z (equity 8,972.31) | L_expectancy_guard | ETH/USDT | risk x0.5 | ETH/USDT's last 20 closed trades average -0.159R (below 0), so new ETH/USDT entries risk 0.5x the normal amount until that average is back at or above 0. | #116, #117, #118, #127, #129, #132, #135, #140, #141, #142, #144, #145, #147, #148, #149, #151, #152, #155, #156, #162 |

Reproduce the baseline TEST row with `python -m research.trendbot.journal_rules --journal research/results/stress_k1.0/synthetic_zero_edge_s1/journals/base_test.csv --equity 8963.43 --now 2024-12-30T00:00:00Z --backtest-journal`.

## 10. Journals and human review packs

Trade journals (`journal.write_journal`, one CSV per window) for the 4 pre-registered candidates that ran, the variants whose TRAIN and TEST columns this report shows in full. The other discovery variants appear only as one context row each in section 4, so no journal is kept for them (the same command regenerates them). TEST trade ids are offset past the TRAIN ids, so ids are unique across a variant's two journals and its review pack. The TRAIN journal is the judged TRAIN window (for a fitted layer its D8 out-of-sample gate; its in-sample full-TRAIN journal is kept as context and bound by no record).

| variant | TRAIN journal (judged) | TEST journal | in-sample full-TRAIN journal (context) |
|---|---|---|---|
| `base` | [journals/base_train.csv](journals/base_train.csv) | [journals/base_test.csv](journals/base_test.csv) | - |
| `rr2_vol2_rsi55-70_wick1` | [journals/rr2_vol2_rsi55-70_wick1_train.csv](journals/rr2_vol2_rsi55-70_wick1_train.csv) | [journals/rr2_vol2_rsi55-70_wick1_test.csv](journals/rr2_vol2_rsi55-70_wick1_test.csv) | - |
| `base+ml` | [journals/base_plus_ml_train.csv](journals/base_plus_ml_train.csv) | [journals/base_plus_ml_test.csv](journals/base_plus_ml_test.csv) | [journals/base_plus_ml_train_insample.csv](journals/base_plus_ml_train_insample.csv) |
| `base+guard` | [journals/base_plus_guard_train.csv](journals/base_plus_guard_train.csv) | [journals/base_plus_guard_test.csv](journals/base_plus_guard_test.csv) | - |

Human review packs (`review_sheet.write_review_pack`, TRAIN and TEST):

- no pack for `base`, `rr2_vol2_rsi55-70_wick1`, `base+ml`, `base+guard`: a test-only config (CONTRACT v4 D1, `ADOPT_test_only` blocks it from HUMAN_REVIEW on), so a review pack could never be used; its journals above hold every trade.

## 11. Adoption path and current stage

The only path to real money (enforced by `adoption.py`; no step can be skipped, the promoted config must match the tested config's sha256 fingerprint, and an ML layer's model must match the tested model's fingerprint):

backtest -> walk-forward (ROBUST + drawdown check) -> human review of EVERY trade -> >= 2 weeks on Binance testnet with zero rule violations -> live.

Stages: BACKTEST -> WALK_FORWARD -> HUMAN_REVIEW -> TESTNET -> LIVE. Stage reached by this run: **WALK_FORWARD** (recorded). Next: **HUMAN_REVIEW**.

**No adoption record was written: every config of this run is test-only (stop-fill stress, CONTRACT v4 D4).** Test-only and context variants are not adoptable: they get no promotable record, and `adoption check` blocks any record for one (`ADOPT_test_only` from HUMAN_REVIEW on, `ADOPT_holdout` 'context variant').

## 12. Risk disclaimer

This is a research and testing tool, not financial advice and not a recommendation to trade. Backtests and synthetic worlds are simplified models; past or simulated results do not predict future results. Crypto trading can result in the total loss of the capital used.
