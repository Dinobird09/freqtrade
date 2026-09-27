# Calibration: synthetic world `zero_edge`, seeds 1-50

This report does NOT promise, target or optimise a win rate, and nothing in this package selects on one. A win rate is meaningless without the reward:risk it was earned at: with the mandatory minimum of 2:1 a strategy breaks even at about 33% winners before costs, while a 90% win rate with a 1:9 payoff still loses money. A backtest win rate near 90% is evidence of curve-fitting or look-ahead, not of skill. The target metric is POSITIVE EXPECTANCY (average R per trade, net of fees and slippage) that survives a 70/30 chronological walk-forward with controlled drawdown, with every trade planned at reward:risk >= 2:1. Win rate appears below only as context and is labelled 'context only' wherever it is shown.

Each seed is a full run of the pipeline (6 years of 4H candles, 70/30 walk-forward): baseline, discovery (K = 13 variants, one selected on TRAIN), the ML layer and the expectancy guard, i.e. m = 4 pre-registered TEST looks per seed. **Synthetic results are verification of the methodology, not evidence about real markets.** Each label below combines that candidate's TRAIN and TEST windows (`metrics.label`). ROBUST requires at least 30 trades in each window, TRAIN and TEST avg R > 0, and a one-sided 98.75% lower bound of the TEST mean above zero for BOTH an iid and a calendar-month block bootstrap (the more conservative is used): alpha 0.05 is split over the m = 4 pre-registered candidates (base, discovery-selected, base+ml, base+guard), so when none has an edge the chance that ANY is called ROBUST is at most about 5%; a positive TEST mean that fails only the bound is UNTESTED (positive but not distinguishable from zero after multiplicity correction); the rule was fixed in advance and is never tuned on TEST outcomes. The ML layer's TRAIN columns are its out-of-sample TRAIN gate (CONTRACT v4 D8: a purged 70/30 inner split of TRAIN), the window its label judges; its in-sample full-TRAIN figure is context only. The synthetic worlds share their noise for a given seed (only the planted drift differs), so calibrations of different worlds are independent only on disjoint seed ranges (`--seed-offset`).

- Ground truth: the planted mechanism at 0.15 sigma per candle: a real, positive PRE-cost edge that fees and slippage eat, so the base strategy's expected NET R is ~0 in both windows (the H0 boundary).
- What the ground truth predicts: nothing should be ROBUST: an edge of ~0R after costs is not worth trading, so every ROBUST label here is a false positive at the boundary of the null hypothesis.
- **Costs used in every backtest:** fee 0.100% of notional per side (the default: Binance spot taker, no discounts) (`--fee-rate 0.001`), charged on the entry AND on the exit; slippage 0.05% (`--slippage-pct 0.05`) against the trade on market fills (entries and stop exits; take-profit limit exits get none); exchange `binance` (`--exchange-id`; `EXCHANGE:binance` news events block every pair). Starting capital 10,000 per window.
- Cost-aware sizing and targets (CONTRACT.md v2 A1): the planned risk is the ALL-IN loss at the stop (the stop fill after slippage plus both fees), so a clean stop is exactly -1R and the target is placed so that a take-profit nets exactly +2R after fees; the target's price distance is therefore more than 2x the stop distance. Only a gap through the stop loses more than 1R under the touch fill model.
- Coinbase Advanced Trade taker fees at low volume tiers are several times Binance's, so a Coinbase run must pass the account's real tier with `--fee-rate` (a fraction of notional per side: 0.006 = 0.6%) and `--exchange-id coinbase`; the default costs would understate them.

## Label frequencies

| walk-forward label (TRAIN + TEST) | baseline | discovery-selected | ML layer | expectancy guard |
|---|---:|---:|---:|---:|
| ROBUST | 0/50 | 0/50 | 0/50 | 0/50 |
| TRAIN-ONLY | 16/50 | 13/50 | 1/50 | 15/50 |
| UNTESTED | 15/50 | 34/50 | 47/50 | 16/50 |
| NO-EDGE | 19/50 | 3/50 | 2/50 | 19/50 |

## Ground truth vs labels (rates with 90% Wilson intervals)

- Verdict ROBUST (any of the 4 pre-registered candidates, the family-wise rate) = FALSE-POSITIVE rate at the H0 boundary (net edge ~0): 0/50 = 0% (90% Wilson CI 0%-5%).
- baseline labelled ROBUST = FALSE-POSITIVE rate at the H0 boundary (net edge ~0): 0/50 = 0% (90% Wilson CI 0%-5%).
- discovery-selected labelled ROBUST = FALSE-POSITIVE rate at the H0 boundary (net edge ~0): 0/50 = 0% (90% Wilson CI 0%-5%).
- ML layer labelled ROBUST = FALSE-POSITIVE rate at the H0 boundary (net edge ~0): 0/50 = 0% (90% Wilson CI 0%-5%).
- expectancy guard labelled ROBUST = FALSE-POSITIVE rate at the H0 boundary (net edge ~0): 0/50 = 0% (90% Wilson CI 0%-5%).
- ML layer (`base+ml`) ran in 50/50 seeds. Mean avg R, base -> layer: TRAIN +0.035 -> +0.059 (the D8 out-of-sample gate; in-sample full TRAIN, context only: +0.139) | TEST +0.027 -> +0.060; its TEST avg R beat the base in 27/50 = 54% (90% Wilson CI 43%-65%) of those seeds.
  Per seed, mean counts TRAIN | TEST: signals vetoed 107.0 | 52.2; entries at reduced risk 0.0 | 0.0; base trades absent from the layer journal 52.9 | 25.6; layer trades absent from the base journal 27.4 | 12.9.
- Expectancy guard (`base+guard`) ran in 50/50 seeds. Mean avg R, base -> layer: TRAIN +0.035 -> +0.042 | TEST +0.027 -> +0.025; its TEST avg R beat the base in 0/50 = 0% (90% Wilson CI 0%-5%) of those seeds.
  Per seed, mean counts TRAIN | TEST: signals vetoed 0.0 | 0.0; entries at reduced risk 22.4 | 0.6; base trades absent from the layer journal 2.2 | 0.0; layer trades absent from the base journal 7.2 | 0.1.
- ML coefficients (standardised, fitted on TRAIN only): the largest-magnitude coefficient was an hour term (hour_sin/hour_cos) in 9/50 fitted seeds; mean hour-term magnitude sqrt(hour_sin^2 + hour_cos^2) = 0.133 log-odds per TRAIN s.d.
- Invariant violations over all backtests of all seeds: 0.

Drawdown (CONTRACT.md v3 C5, cap revised by v4 D7): every result reports the realised (closed-trade) and the mark-to-market (open positions valued at each 4H close) max drawdown; dd_ok = TEST mark-to-market max drawdown <= min(15%, the 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length, in percent of equity at the risk each trade took). Why the cap: the 15% cap is about 15 consecutive full-size losses at the mandated 1% cluster risk budget; at the 2:1 break-even win probability p* = 1/3 such a streak has probability (2/3)^15 = 0.23%, so a TEST drawdown beyond it is inconsistent with even a break-even strategy at the mandated risk (the TRAIN-bootstrap p95 usually binds first; the 3% weekly-loss halt limits how fast a drawdown accrues, not how deep it goes).

| candidate | seeds that reached the TEST gate (TRAIN avg R > 0, TRAIN n >= 30, TEST n >= 30) | ROBUST, all seeds | ROBUST given the TEST gate was reached | mean TRAIN avg R | mean TEST avg R | mean TRAIN n | mean TEST n | mean TEST max DD % realised / MTM / limit | dd_ok (TEST MTM within limit) |
|---|---|---|---|---:|---:|---:|---:|---|---|
| baseline | 31/50 = 62% (90% Wilson CI 50%-72%) | 0/50 = 0% (90% Wilson CI 0%-5%) | 0/31 = 0% (90% Wilson CI 0%-8%) | +0.035 | +0.027 | 103.2 | 46.7 | 8.37 / 9.01 / 13.67 | 46/50 = 92% (90% Wilson CI 83%-96%) |
| discovery-selected | 39/50 = 78% (90% Wilson CI 67%-86%) | 0/50 = 0% (90% Wilson CI 0%-5%) | 0/39 = 0% (90% Wilson CI 0%-6%) | +0.175 | +0.043 | 80.7 | 37.1 | 7.97 / 8.72 / 12.07 | 38/50 = 76% (90% Wilson CI 65%-84%) |
| ML layer | 6/50 = 12% (90% Wilson CI 6%-22%) | 0/50 = 0% (90% Wilson CI 0%-5%) | 0/6 = 0% (90% Wilson CI 0%-31%) | +0.059 | +0.060 | 22.5 | 34.0 | 6.70 / 7.32 / 11.49 | 43/50 = 86% (90% Wilson CI 76%-92%) |
| expectancy guard | 31/50 = 62% (90% Wilson CI 50%-72%) | 0/50 = 0% (90% Wilson CI 0%-5%) | 0/31 = 0% (90% Wilson CI 0%-8%) | +0.042 | +0.025 | 108.2 | 46.8 | 8.33 / 8.97 / 13.08 | 44/50 = 88% (90% Wilson CI 78%-94%) |

## Per seed (TRAIN and TEST side by side)

| seed | base TRAIN n | base TRAIN avg R | base TEST n | base TEST avg R | base TEST adj LB | base label | selected | selected TRAIN n | selected TRAIN avg R | selected TEST n | selected TEST avg R | selected label | ML TRAIN n | ML TRAIN avg R (out-of-sample gate) | ML TEST n | ML TEST avg R | ML label | guard TRAIN n | guard TRAIN avg R | guard TEST n | guard TEST avg R | guard label | verdict ROBUST | violations |
|---:|---:|---:|---:|---:|---:|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---|---|---:|
| 1 | 94 | -0.004 | 60 | -0.050 | -0.450 | NO-EDGE | `rr2_vol2_rsi55-70` | 75 | +0.048 | 49 | -0.020 | TRAIN-ONLY | 0 | +0.000 | 6 | +0.500 | UNTESTED | 102 | -0.044 | 60 | -0.050 | NO-EDGE | no | 0 |
| 2 | 95 | +0.022 | 45 | -0.133 | -0.533 | TRAIN-ONLY | `rr2.5_vol1.5_rsi55-70` | 79 | +0.033 | 34 | -0.176 | TRAIN-ONLY | 18 | +0.000 | 24 | +0.000 | UNTESTED | 98 | +0.022 | 45 | -0.133 | TRAIN-ONLY | no | 0 |
| 3 | 99 | -0.151 | 43 | +0.293 | -0.227 | NO-EDGE | `rr3_vol2_rsi50-70` | 69 | +0.043 | 30 | +0.387 | UNTESTED | 27 | -0.111 | 31 | +0.117 | UNTESTED | 103 | -0.126 | 43 | +0.293 | NO-EDGE | no | 0 |
| 4 | 94 | +0.085 | 37 | +0.054 | -0.432 | UNTESTED | `rr2.5_vol1.5_rsi50-70` | 90 | +0.244 | 34 | +0.029 | UNTESTED | 16 | +0.688 | 36 | -0.250 | UNTESTED | 95 | +0.105 | 37 | +0.054 | UNTESTED | no | 0 |
| 5 | 120 | +0.050 | 53 | +0.110 | -0.337 | UNTESTED | `rr2.5_vol1.5_rsi55-70` | 96 | +0.312 | 42 | +0.167 | UNTESTED | 37 | +0.216 | 49 | +0.079 | UNTESTED | 135 | +0.000 | 53 | +0.110 | NO-EDGE | no | 0 |
| 6 | 107 | +0.080 | 43 | -0.512 | -0.923 | TRAIN-ONLY | `rr2_vol2_rsi55-70` | 87 | +0.172 | 29 | -0.690 | UNTESTED | 22 | -0.045 | 34 | -0.471 | UNTESTED | 119 | +0.097 | 43 | -0.512 | TRAIN-ONLY | no | 0 |
| 7 | 87 | +0.069 | 38 | -0.132 | -0.605 | TRAIN-ONLY | `rr3_vol2_rsi50-70` | 70 | +0.350 | 31 | -0.226 | TRAIN-ONLY | 16 | -0.625 | 33 | -0.273 | UNTESTED | 87 | +0.069 | 38 | -0.132 | TRAIN-ONLY | no | 0 |
| 8 | 115 | -0.009 | 43 | +0.046 | -0.442 | NO-EDGE | `rr2.5_vol2_rsi55-70` | 89 | +0.219 | 31 | +0.129 | UNTESTED | 19 | +0.421 | 32 | +0.031 | UNTESTED | 124 | -0.008 | 43 | +0.046 | NO-EDGE | no | 0 |
| 9 | 113 | -0.071 | 48 | +0.250 | -0.188 | NO-EDGE | `rr2.5_vol2_rsi55-70` | 75 | +0.260 | 34 | +0.132 | UNTESTED | 24 | +0.000 | 40 | +0.350 | UNTESTED | 118 | -0.110 | 48 | +0.250 | NO-EDGE | no | 0 |
| 10 | 105 | +0.000 | 43 | +0.186 | -0.342 | NO-EDGE | `rr3_vol2_rsi50-70` | 62 | +0.151 | 34 | +0.323 | UNTESTED | 13 | +0.385 | 21 | +0.286 | UNTESTED | 113 | +0.035 | 43 | +0.186 | UNTESTED | no | 0 |
| 11 | 103 | +0.078 | 54 | -0.115 | -0.504 | TRAIN-ONLY | `rr3_vol2_rsi55-70` | 66 | +0.333 | 31 | +0.161 | UNTESTED | 14 | +0.500 | 31 | +0.258 | UNTESTED | 103 | +0.078 | 54 | -0.115 | TRAIN-ONLY | no | 0 |
| 12 | 105 | +0.229 | 56 | +0.057 | -0.361 | UNTESTED | `rr2.5_vol1.5_rsi55-70` | 98 | +0.500 | 33 | +0.445 | UNTESTED | 30 | +0.400 | 45 | +0.333 | UNTESTED | 104 | +0.269 | 56 | +0.057 | UNTESTED | no | 0 |
| 13 | 107 | -0.047 | 45 | +0.272 | -0.251 | NO-EDGE | `rr2.5_vol2_rsi55-70` | 87 | +0.086 | 33 | +0.167 | UNTESTED | 17 | +0.412 | 37 | -0.021 | UNTESTED | 111 | -0.027 | 45 | +0.272 | NO-EDGE | no | 0 |
| 14 | 104 | -0.034 | 48 | -0.062 | -0.500 | NO-EDGE | `rr3_vol2_rsi55-70` | 69 | +0.007 | 28 | -0.143 | UNTESTED | 25 | +0.020 | 30 | -0.100 | UNTESTED | 107 | +0.023 | 48 | -0.062 | TRAIN-ONLY | no | 0 |
| 15 | 98 | +0.225 | 40 | +0.200 | -0.325 | UNTESTED | `rr2_vol1.5_rsi55-70` | 84 | +0.357 | 41 | +0.171 | UNTESTED | 30 | +0.100 | 33 | +0.182 | UNTESTED | 98 | +0.225 | 40 | +0.200 | UNTESTED | no | 0 |
| 16 | 94 | +0.181 | 35 | +0.126 | -0.400 | UNTESTED | `rr2.5_vol2_rsi55-70` | 57 | +0.412 | 29 | -0.021 | UNTESTED | 28 | +0.286 | 38 | +0.037 | UNTESTED | 97 | +0.175 | 35 | +0.126 | UNTESTED | no | 0 |
| 17 | 96 | +0.106 | 42 | +0.000 | -0.500 | TRAIN-ONLY | `rr2_vol1.5_rsi55-70` | 89 | +0.125 | 41 | +0.024 | UNTESTED | 26 | -0.148 | 32 | +0.031 | UNTESTED | 96 | +0.106 | 42 | +0.000 | TRAIN-ONLY | no | 0 |
| 18 | 114 | +0.037 | 56 | +0.415 | -0.024 | UNTESTED | `rr2_vol2_rsi55-70` | 91 | +0.233 | 46 | +0.331 | UNTESTED | 17 | -0.401 | 28 | +0.223 | UNTESTED | 116 | +0.045 | 56 | +0.415 | UNTESTED | no | 0 |
| 19 | 103 | -0.019 | 46 | +0.192 | -0.417 | NO-EDGE | `rr2_vol2_rsi50-70` | 90 | +0.056 | 44 | +0.295 | UNTESTED | 21 | +0.429 | 46 | -0.022 | UNTESTED | 105 | +0.019 | 46 | +0.192 | UNTESTED | no | 0 |
| 20 | 109 | -0.092 | 40 | +0.125 | -0.400 | NO-EDGE | `rr2.5_vol2_rsi55-70` | 74 | +0.088 | 27 | +0.685 | UNTESTED | 25 | -0.280 | 29 | +0.345 | UNTESTED | 109 | -0.092 | 40 | +0.125 | NO-EDGE | no | 0 |
| 21 | 96 | +0.165 | 41 | -0.160 | -0.599 | TRAIN-ONLY | `rr2.5_vol2_rsi55-70` | 70 | +0.337 | 26 | +0.032 | UNTESTED | 17 | +0.107 | 25 | -0.134 | UNTESTED | 98 | +0.202 | 41 | -0.160 | TRAIN-ONLY | no | 0 |
| 22 | 121 | +0.066 | 49 | +0.241 | -0.216 | UNTESTED | `rr2_vol2_rsi55-70` | 102 | +0.088 | 40 | +0.370 | UNTESTED | 39 | -0.077 | 51 | +0.329 | NO-EDGE | 126 | +0.024 | 49 | +0.241 | UNTESTED | no | 0 |
| 23 | 111 | -0.175 | 51 | +0.323 | -0.125 | NO-EDGE | `rr3_vol2_rsi55-70` | 73 | -0.102 | 31 | +0.467 | NO-EDGE | 13 | -0.190 | 25 | +0.019 | UNTESTED | 119 | -0.080 | 51 | +0.323 | NO-EDGE | no | 0 |
| 24 | 115 | -0.053 | 54 | +0.075 | -0.426 | NO-EDGE | `rr2.5_vol2_rsi50-70` | 91 | +0.049 | 45 | -0.121 | TRAIN-ONLY | 20 | +0.200 | 40 | +0.077 | UNTESTED | 115 | -0.001 | 54 | +0.075 | NO-EDGE | no | 0 |
| 25 | 102 | -0.176 | 48 | +0.625 | +0.043 | NO-EDGE | `rr3_vol2_rsi55-70` | 66 | +0.212 | 34 | +0.627 | UNTESTED | 19 | -0.210 | 25 | +0.320 | UNTESTED | 118 | -0.085 | 48 | +0.625 | NO-EDGE | no | 0 |
| 26 | 86 | -0.533 | 37 | +0.135 | -0.351 | NO-EDGE | `rr2_vol2_rsi50-70` | 72 | -0.417 | 44 | +0.091 | NO-EDGE | 1 | -1.000 | 10 | -0.400 | UNTESTED | 95 | -0.514 | 37 | +0.135 | NO-EDGE | no | 0 |
| 27 | 105 | +0.171 | 36 | -0.038 | -0.538 | TRAIN-ONLY | `rr2_vol2_rsi50-70` | 98 | +0.194 | 30 | +0.154 | UNTESTED | 31 | +0.258 | 36 | +0.045 | UNTESTED | 110 | +0.200 | 36 | -0.038 | TRAIN-ONLY | no | 0 |
| 28 | 105 | +0.132 | 61 | -0.066 | -0.459 | TRAIN-ONLY | `rr2.5_vol1.5_rsi55-70` | 83 | +0.203 | 47 | -0.330 | TRAIN-ONLY | 29 | +0.307 | 45 | -0.200 | UNTESTED | 113 | +0.079 | 61 | -0.066 | TRAIN-ONLY | no | 0 |
| 29 | 92 | +0.060 | 46 | +0.157 | -0.332 | UNTESTED | `rr3_vol1.5_rsi55-70` | 69 | +0.175 | 27 | -0.030 | UNTESTED | 18 | +0.151 | 36 | +0.311 | UNTESTED | 97 | +0.098 | 46 | +0.157 | UNTESTED | no | 0 |
| 30 | 111 | -0.113 | 56 | -0.196 | -0.604 | NO-EDGE | `rr3_vol2_rsi50-70` | 72 | +0.145 | 46 | -0.391 | TRAIN-ONLY | 26 | +0.011 | 35 | -0.057 | UNTESTED | 117 | -0.133 | 56 | -0.196 | NO-EDGE | no | 0 |
| 31 | 100 | -0.070 | 50 | +0.200 | -0.318 | NO-EDGE | `rr2.5_vol2_rsi50-70` | 70 | +0.050 | 34 | +0.029 | UNTESTED | 24 | -0.250 | 25 | -0.040 | UNTESTED | 97 | +0.051 | 51 | +0.176 | UNTESTED | no | 0 |
| 32 | 103 | -0.068 | 38 | -0.605 | -1.000 | NO-EDGE | `rr3_vol2_rsi55-70` | 59 | +0.279 | 26 | -0.692 | UNTESTED | 13 | +0.615 | 29 | -0.586 | UNTESTED | 105 | -0.029 | 38 | -0.605 | NO-EDGE | no | 0 |
| 33 | 117 | +0.144 | 46 | +0.067 | -0.389 | UNTESTED | `rr3_vol1.5_rsi50-70` | 96 | +0.228 | 36 | +0.141 | UNTESTED | 25 | +0.394 | 47 | +0.172 | UNTESTED | 120 | +0.190 | 46 | +0.067 | UNTESTED | no | 0 |
| 34 | 94 | +0.034 | 44 | -0.305 | -0.757 | TRAIN-ONLY | `rr3_vol1.5_rsi55-70` | 77 | +0.149 | 38 | -0.564 | TRAIN-ONLY | 22 | -0.263 | 23 | -0.453 | UNTESTED | 112 | -0.025 | 44 | -0.305 | NO-EDGE | no | 0 |
| 35 | 95 | +0.168 | 47 | +0.042 | -0.405 | UNTESTED | `rr3_vol1.5_rsi50-70` | 76 | +0.263 | 37 | +0.164 | UNTESTED | 27 | -0.111 | 39 | +0.308 | UNTESTED | 102 | +0.235 | 47 | +0.042 | UNTESTED | no | 0 |
| 36 | 89 | +0.180 | 39 | -0.376 | -0.829 | TRAIN-ONLY | `rr2_vol1.5_rsi50-70` | 89 | +0.180 | 39 | -0.376 | TRAIN-ONLY | 24 | +0.250 | 29 | -0.379 | UNTESTED | 89 | +0.180 | 39 | -0.376 | TRAIN-ONLY | no | 0 |
| 37 | 110 | +0.145 | 50 | -0.031 | -0.502 | TRAIN-ONLY | `rr2_vol2_rsi50-70` | 92 | +0.304 | 50 | -0.091 | TRAIN-ONLY | 25 | -0.280 | 35 | +0.060 | UNTESTED | 112 | +0.152 | 50 | -0.031 | TRAIN-ONLY | no | 0 |
| 38 | 112 | +0.104 | 57 | +0.000 | -0.421 | TRAIN-ONLY | `rr2_vol2_rsi50-70` | 90 | +0.174 | 49 | +0.041 | UNTESTED | 17 | +0.059 | 58 | +0.552 | UNTESTED | 121 | +0.097 | 57 | +0.000 | TRAIN-ONLY | no | 0 |
| 39 | 107 | +0.262 | 42 | +0.000 | -0.529 | TRAIN-ONLY | `rr2_vol1.5_rsi55-70` | 103 | +0.282 | 39 | +0.000 | TRAIN-ONLY | 18 | +0.333 | 37 | -0.027 | UNTESTED | 110 | +0.255 | 42 | +0.000 | TRAIN-ONLY | no | 0 |
| 40 | 106 | -0.163 | 48 | +0.125 | -0.312 | NO-EDGE | `rr2.5_vol1.5_rsi50-70` | 82 | +0.003 | 43 | +0.211 | UNTESTED | 15 | -0.200 | 11 | +1.032 | UNTESTED | 123 | -0.157 | 48 | +0.125 | NO-EDGE | no | 0 |
| 41 | 91 | +0.194 | 57 | +0.053 | -0.429 | UNTESTED | `rr2_vol2_rsi55-70` | 77 | +0.342 | 45 | +0.067 | UNTESTED | 27 | -0.145 | 49 | +0.163 | UNTESTED | 94 | +0.188 | 59 | +0.017 | UNTESTED | no | 0 |
| 42 | 112 | +0.106 | 49 | +0.041 | -0.388 | UNTESTED | `rr3_vol1.5_rsi55-70` | 62 | +0.521 | 39 | -0.222 | TRAIN-ONLY | 17 | +0.683 | 48 | -0.062 | UNTESTED | 119 | +0.016 | 49 | +0.041 | UNTESTED | no | 0 |
| 43 | 115 | +0.109 | 59 | +0.086 | -0.333 | UNTESTED | `rr2_vol2_rsi55-70` | 94 | +0.196 | 50 | +0.094 | UNTESTED | 39 | +0.271 | 45 | +0.224 | UNTESTED | 122 | +0.144 | 59 | +0.086 | UNTESTED | no | 0 |
| 44 | 107 | +0.262 | 50 | -0.274 | -0.711 | TRAIN-ONLY | `rr2.5_vol1.5_rsi50-70` | 89 | +0.360 | 45 | -0.293 | TRAIN-ONLY | 36 | +0.083 | 44 | -0.174 | TRAIN-ONLY | 107 | +0.262 | 50 | -0.274 | TRAIN-ONLY | no | 0 |
| 45 | 107 | -0.110 | 36 | +0.000 | -0.500 | NO-EDGE | `rr3_vol2_rsi55-70` | 73 | -0.045 | 29 | +0.379 | UNTESTED | 32 | -0.274 | 27 | -0.111 | UNTESTED | 115 | -0.120 | 36 | +0.000 | NO-EDGE | no | 0 |
| 46 | 102 | +0.235 | 42 | -0.071 | -0.500 | TRAIN-ONLY | `rr2_vol2_rsi55-70` | 85 | +0.306 | 34 | +0.500 | UNTESTED | 28 | +0.286 | 35 | +0.286 | UNTESTED | 102 | +0.235 | 42 | -0.071 | TRAIN-ONLY | no | 0 |
| 47 | 111 | +0.108 | 57 | +0.263 | -0.158 | UNTESTED | `rr2.5_vol1.5_rsi50-70` | 94 | +0.303 | 47 | +0.192 | UNTESTED | 29 | -0.049 | 50 | +0.200 | UNTESTED | 122 | +0.033 | 57 | +0.263 | UNTESTED | no | 0 |
| 48 | 93 | -0.242 | 42 | +0.000 | -0.500 | NO-EDGE | `rr3_vol2_rsi50-70` | 59 | -0.172 | 33 | -0.151 | NO-EDGE | 18 | +0.085 | 26 | +0.154 | UNTESTED | 100 | -0.175 | 42 | +0.000 | NO-EDGE | no | 0 |
| 49 | 77 | +0.030 | 40 | -0.379 | -0.775 | TRAIN-ONLY | `rr2_vol1.5_rsi55-70` | 73 | +0.045 | 35 | -0.376 | TRAIN-ONLY | 20 | -0.335 | 25 | -0.520 | UNTESTED | 81 | -0.021 | 40 | -0.379 | NO-EDGE | no | 0 |
| 50 | 106 | +0.019 | 48 | +0.086 | -0.356 | UNTESTED | `rr2_vol2_rsi50-70` | 100 | +0.080 | 38 | +0.056 | UNTESTED | 33 | +0.000 | 37 | +0.246 | NO-EDGE | 108 | -0.056 | 49 | +0.064 | NO-EDGE | no | 0 |

## Adoption path

The only path to real money (enforced by `adoption.py`; no step can be skipped): BACKTEST -> WALK_FORWARD (ROBUST + drawdown check) -> HUMAN_REVIEW of EVERY trade -> >= 14 days on Binance testnet with zero rule violations -> LIVE. A synthetic result never gets past WALK_FORWARD: `adoption check` blocks every record whose provenance is `synthetic:<world>:<seed>` with `ADOPT_provenance`. A calibration writes no adoption records; it measures how often the labels are right when the truth is known.

## Risk disclaimer

This is a research and testing tool, not financial advice and not a recommendation to trade. Backtests and synthetic worlds are simplified models; past or simulated results do not predict future results. Crypto trading can result in the total loss of the capital used.
