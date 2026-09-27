# Calibration: synthetic world `zero_edge`, seeds 2001-2050

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
| ROBUST | 2/50 | 0/50 | 0/50 | 2/50 |
| TRAIN-ONLY | 13/50 | 12/50 | 3/50 | 12/50 |
| UNTESTED | 18/50 | 33/50 | 42/50 | 18/50 |
| NO-EDGE | 17/50 | 5/50 | 5/50 | 18/50 |

## Ground truth vs labels (rates with 90% Wilson intervals)

- Verdict ROBUST (any of the 4 pre-registered candidates, the family-wise rate) = FALSE-POSITIVE rate at the H0 boundary (net edge ~0): 2/50 = 4% (90% Wilson CI 1%-11%).
- baseline labelled ROBUST = FALSE-POSITIVE rate at the H0 boundary (net edge ~0): 2/50 = 4% (90% Wilson CI 1%-11%).
- discovery-selected labelled ROBUST = FALSE-POSITIVE rate at the H0 boundary (net edge ~0): 0/50 = 0% (90% Wilson CI 0%-5%).
- ML layer labelled ROBUST = FALSE-POSITIVE rate at the H0 boundary (net edge ~0): 0/50 = 0% (90% Wilson CI 0%-5%).
- expectancy guard labelled ROBUST = FALSE-POSITIVE rate at the H0 boundary (net edge ~0): 2/50 = 4% (90% Wilson CI 1%-11%).
- ML layer (`base+ml`) ran in 50/50 seeds. Mean avg R, base -> layer: TRAIN +0.024 -> +0.026 (the D8 out-of-sample gate; in-sample full TRAIN, context only: +0.135) | TEST +0.047 -> +0.002; its TEST avg R beat the base in 19/50 = 38% (90% Wilson CI 28%-50%) of those seeds.
  Per seed, mean counts TRAIN | TEST: signals vetoed 119.1 | 52.2; entries at reduced risk 0.0 | 0.0; base trades absent from the layer journal 57.2 | 25.9; layer trades absent from the base journal 27.0 | 13.2.
- Expectancy guard (`base+guard`) ran in 50/50 seeds. Mean avg R, base -> layer: TRAIN +0.024 -> +0.027 | TEST +0.047 -> +0.047; its TEST avg R beat the base in 1/50 = 2% (90% Wilson CI 0%-8%) of those seeds.
  Per seed, mean counts TRAIN | TEST: signals vetoed 0.0 | 0.0; entries at reduced risk 21.3 | 0.5; base trades absent from the layer journal 2.4 | 0.0; layer trades absent from the base journal 7.5 | 0.1.
- ML coefficients (standardised, fitted on TRAIN only): the largest-magnitude coefficient was an hour term (hour_sin/hour_cos) in 1/50 fitted seeds; mean hour-term magnitude sqrt(hour_sin^2 + hour_cos^2) = 0.117 log-odds per TRAIN s.d.
- Invariant violations over all backtests of all seeds: 0.

Drawdown (CONTRACT.md v3 C5, cap revised by v4 D7): every result reports the realised (closed-trade) and the mark-to-market (open positions valued at each 4H close) max drawdown; dd_ok = TEST mark-to-market max drawdown <= min(15%, the 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length, in percent of equity at the risk each trade took). Why the cap: the 15% cap is about 15 consecutive full-size losses at the mandated 1% cluster risk budget; at the 2:1 break-even win probability p* = 1/3 such a streak has probability (2/3)^15 = 0.23%, so a TEST drawdown beyond it is inconsistent with even a break-even strategy at the mandated risk (the TRAIN-bootstrap p95 usually binds first; the 3% weekly-loss halt limits how fast a drawdown accrues, not how deep it goes).

| candidate | seeds that reached the TEST gate (TRAIN avg R > 0, TRAIN n >= 30, TEST n >= 30) | ROBUST, all seeds | ROBUST given the TEST gate was reached | mean TRAIN avg R | mean TEST avg R | mean TRAIN n | mean TEST n | mean TEST max DD % realised / MTM / limit | dd_ok (TEST MTM within limit) |
|---|---|---|---|---:|---:|---:|---:|---|---|
| baseline | 33/50 = 66% (90% Wilson CI 54%-76%) | 2/50 = 4% (90% Wilson CI 1%-11%) | 2/33 = 6% (90% Wilson CI 2%-17%) | +0.024 | +0.047 | 105.4 | 46.5 | 8.17 / 8.89 / 14.16 | 46/50 = 92% (90% Wilson CI 83%-96%) |
| discovery-selected | 38/50 = 76% (90% Wilson CI 65%-84%) | 0/50 = 0% (90% Wilson CI 0%-5%) | 0/38 = 0% (90% Wilson CI 0%-7%) | +0.172 | +0.115 | 85.8 | 38.5 | 7.33 / 8.16 / 12.29 | 46/50 = 92% (90% Wilson CI 83%-96%) |
| ML layer | 7/50 = 14% (90% Wilson CI 8%-24%) | 0/50 = 0% (90% Wilson CI 0%-5%) | 0/7 = 0% (90% Wilson CI 0%-28%) | +0.026 | +0.002 | 23.8 | 33.8 | 7.33 / 7.98 / 11.92 | 42/50 = 84% (90% Wilson CI 74%-91%) |
| expectancy guard | 32/50 = 64% (90% Wilson CI 52%-74%) | 2/50 = 4% (90% Wilson CI 1%-11%) | 2/32 = 6% (90% Wilson CI 2%-17%) | +0.027 | +0.047 | 110.6 | 46.6 | 8.14 / 8.85 / 13.52 | 44/50 = 88% (90% Wilson CI 78%-94%) |

## Per seed (TRAIN and TEST side by side)

| seed | base TRAIN n | base TRAIN avg R | base TEST n | base TEST avg R | base TEST adj LB | base label | selected | selected TRAIN n | selected TRAIN avg R | selected TEST n | selected TEST avg R | selected label | ML TRAIN n | ML TRAIN avg R (out-of-sample gate) | ML TEST n | ML TEST avg R | ML label | guard TRAIN n | guard TRAIN avg R | guard TEST n | guard TEST avg R | guard label | verdict ROBUST | violations |
|---:|---:|---:|---:|---:|---:|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---|---|---:|
| 2001 | 113 | +0.088 | 42 | +0.000 | -0.625 | TRAIN-ONLY | `rr3_vol2_rsi55-70` | 59 | +0.559 | 26 | +0.692 | UNTESTED | 28 | -0.036 | 33 | +0.000 | UNTESTED | 113 | +0.088 | 42 | +0.000 | TRAIN-ONLY | no | 0 |
| 2002 | 87 | +0.103 | 59 | +0.193 | -0.214 | UNTESTED | `rr2_vol2_rsi55-70` | 80 | +0.312 | 50 | +0.284 | UNTESTED | 32 | -0.344 | 51 | -0.032 | NO-EDGE | 87 | +0.103 | 59 | +0.193 | UNTESTED | no | 0 |
| 2003 | 108 | +0.094 | 59 | -0.095 | -0.452 | TRAIN-ONLY | `rr2_vol2_rsi55-70` | 88 | +0.172 | 51 | +0.000 | TRAIN-ONLY | 19 | +0.164 | 44 | +0.023 | UNTESTED | 109 | +0.111 | 60 | -0.111 | TRAIN-ONLY | no | 0 |
| 2004 | 131 | -0.084 | 36 | -0.417 | -0.833 | NO-EDGE | `rr3_vol2_rsi50-70` | 86 | +0.036 | 27 | -0.407 | UNTESTED | 32 | -0.250 | 40 | -0.550 | NO-EDGE | 146 | -0.096 | 36 | -0.417 | NO-EDGE | no | 0 |
| 2005 | 91 | +0.029 | 50 | -0.100 | -0.520 | TRAIN-ONLY | `rr2_vol1.5_rsi55-70` | 85 | +0.067 | 48 | -0.125 | TRAIN-ONLY | 20 | -0.516 | 39 | -0.231 | UNTESTED | 91 | +0.029 | 50 | -0.100 | TRAIN-ONLY | no | 0 |
| 2006 | 111 | -0.027 | 47 | -0.223 | -0.643 | NO-EDGE | `rr2.5_vol2_rsi55-70` | 81 | +0.080 | 38 | -0.102 | TRAIN-ONLY | 20 | -0.100 | 26 | -0.057 | UNTESTED | 116 | -0.043 | 47 | -0.223 | NO-EDGE | no | 0 |
| 2007 | 103 | +0.005 | 44 | -0.045 | -0.531 | TRAIN-ONLY | `rr3_vol2_rsi55-70` | 67 | +0.157 | 29 | +0.103 | UNTESTED | 22 | -0.182 | 33 | -0.182 | UNTESTED | 111 | -0.041 | 44 | -0.045 | NO-EDGE | no | 0 |
| 2008 | 92 | +0.011 | 54 | +0.225 | -0.279 | UNTESTED | `rr3_vol2_rsi50-70` | 74 | +0.243 | 35 | +0.600 | UNTESTED | 25 | +0.080 | 51 | +0.062 | UNTESTED | 95 | +0.042 | 54 | +0.225 | UNTESTED | no | 0 |
| 2009 | 98 | +0.057 | 46 | +0.043 | -0.429 | UNTESTED | `rr3_vol1.5_rsi55-70` | 73 | +0.212 | 39 | +0.318 | UNTESTED | 28 | -0.017 | 41 | +0.024 | UNTESTED | 104 | +0.083 | 49 | +0.041 | UNTESTED | no | 0 |
| 2010 | 116 | +0.016 | 44 | +0.477 | +0.000 | UNTESTED | `rr2.5_vol1.5_rsi55-70` | 91 | +0.083 | 36 | +0.416 | UNTESTED | 15 | +0.039 | 36 | +0.138 | UNTESTED | 118 | +0.025 | 44 | +0.477 | UNTESTED | no | 0 |
| 2011 | 109 | +0.105 | 44 | +0.098 | -0.459 | UNTESTED | `rr2_vol2_rsi55-70` | 81 | +0.296 | 40 | +0.207 | UNTESTED | 26 | -0.290 | 39 | +0.315 | UNTESTED | 109 | +0.105 | 44 | +0.098 | UNTESTED | no | 0 |
| 2012 | 97 | +0.051 | 55 | -0.002 | -0.478 | TRAIN-ONLY | `rr2_vol2_rsi50-70` | 90 | +0.167 | 49 | +0.181 | UNTESTED | 19 | -0.368 | 37 | +0.054 | UNTESTED | 102 | +0.059 | 55 | -0.002 | TRAIN-ONLY | no | 0 |
| 2013 | 97 | +0.047 | 41 | -0.446 | -0.812 | TRAIN-ONLY | `rr2.5_vol2_rsi50-70` | 76 | +0.197 | 35 | -0.351 | TRAIN-ONLY | 18 | +0.311 | 42 | -0.451 | UNTESTED | 106 | -0.042 | 41 | -0.446 | NO-EDGE | no | 0 |
| 2014 | 119 | +0.084 | 49 | +0.440 | -0.075 | UNTESTED | `rr2_vol1.5_rsi55-70` | 112 | +0.098 | 47 | +0.404 | UNTESTED | 21 | -0.143 | 30 | +0.200 | UNTESTED | 120 | +0.100 | 49 | +0.440 | UNTESTED | no | 0 |
| 2015 | 90 | +0.067 | 40 | -0.025 | -0.571 | TRAIN-ONLY | `rr2_vol1.5_rsi55-70` | 89 | +0.079 | 38 | +0.026 | UNTESTED | 7 | +0.286 | 26 | -0.077 | UNTESTED | 90 | +0.067 | 40 | -0.025 | TRAIN-ONLY | no | 0 |
| 2016 | 96 | +0.023 | 56 | +0.525 | +0.087 | ROBUST | `rr3_vol1.5_rsi55-70` | 77 | +0.270 | 38 | +0.510 | UNTESTED | 22 | +0.054 | 33 | +0.678 | UNTESTED | 96 | +0.023 | 56 | +0.525 | ROBUST | yes | 0 |
| 2017 | 127 | +0.193 | 63 | +0.333 | -0.127 | UNTESTED | `rr2_vol1.5_rsi55-70` | 126 | +0.250 | 61 | +0.377 | UNTESTED | 48 | +0.093 | 63 | +0.333 | UNTESTED | 130 | +0.175 | 63 | +0.333 | UNTESTED | no | 0 |
| 2018 | 120 | +0.050 | 64 | -0.062 | -0.438 | TRAIN-ONLY | `rr2.5_vol2_rsi55-70` | 89 | +0.219 | 45 | +0.244 | UNTESTED | 18 | +0.333 | 41 | -0.049 | UNTESTED | 133 | +0.083 | 64 | -0.062 | TRAIN-ONLY | no | 0 |
| 2019 | 112 | -0.009 | 42 | -0.071 | -0.757 | NO-EDGE | `rr2.5_vol1.5_rsi55-70` | 86 | +0.336 | 33 | -0.151 | TRAIN-ONLY | 23 | +0.354 | 29 | +0.241 | UNTESTED | 113 | +0.062 | 42 | -0.071 | TRAIN-ONLY | no | 0 |
| 2020 | 117 | -0.040 | 50 | -0.018 | -0.493 | NO-EDGE | `rr3_vol1.5_rsi50-70` | 96 | +0.000 | 35 | -0.511 | NO-EDGE | 22 | -0.318 | 12 | -0.906 | UNTESTED | 117 | -0.040 | 50 | -0.018 | NO-EDGE | no | 0 |
| 2021 | 98 | -0.020 | 44 | -0.097 | -0.523 | NO-EDGE | `rr2_vol2_rsi55-70` | 81 | +0.111 | 37 | +0.155 | UNTESTED | 20 | -0.100 | 23 | -0.316 | UNTESTED | 102 | +0.000 | 44 | -0.097 | NO-EDGE | no | 0 |
| 2022 | 109 | +0.046 | 34 | -0.179 | -0.740 | TRAIN-ONLY | `rr2_vol1.5_rsi55-70` | 103 | +0.049 | 34 | -0.091 | TRAIN-ONLY | 26 | -0.308 | 28 | -0.250 | UNTESTED | 114 | +0.026 | 34 | -0.179 | TRAIN-ONLY | no | 0 |
| 2023 | 100 | -0.080 | 40 | -0.250 | -0.735 | NO-EDGE | `rr2.5_vol1.5_rsi55-70` | 88 | +0.057 | 36 | -0.319 | TRAIN-ONLY | 11 | +0.531 | 27 | -0.111 | UNTESTED | 102 | -0.039 | 40 | -0.250 | NO-EDGE | no | 0 |
| 2024 | 94 | -0.085 | 33 | -0.273 | -0.778 | NO-EDGE | `rr2_vol2_rsi55-70` | 87 | +0.081 | 27 | -0.222 | UNTESTED | 21 | +0.097 | 21 | -0.143 | UNTESTED | 98 | -0.061 | 33 | -0.273 | NO-EDGE | no | 0 |
| 2025 | 111 | -0.258 | 43 | +0.046 | -0.526 | NO-EDGE | `rr2.5_vol1.5_rsi55-70` | 85 | -0.160 | 34 | +0.029 | NO-EDGE | 13 | +0.490 | 11 | +0.091 | UNTESTED | 122 | -0.226 | 43 | +0.046 | NO-EDGE | no | 0 |
| 2026 | 82 | -0.085 | 41 | -0.195 | -0.657 | NO-EDGE | `rr3_vol2_rsi50-70` | 74 | +0.058 | 31 | +0.161 | UNTESTED | 17 | -0.118 | 11 | -0.727 | UNTESTED | 86 | -0.093 | 41 | -0.195 | NO-EDGE | no | 0 |
| 2027 | 116 | +0.169 | 40 | +0.155 | -0.325 | UNTESTED | `rr2_vol2_rsi55-70` | 93 | +0.265 | 31 | -0.107 | TRAIN-ONLY | 30 | +0.122 | 25 | -0.040 | UNTESTED | 127 | +0.092 | 40 | +0.155 | UNTESTED | no | 0 |
| 2028 | 93 | +0.006 | 42 | +0.357 | -0.189 | UNTESTED | `rr3_vol2_rsi55-70` | 65 | +0.362 | 35 | +0.143 | UNTESTED | 28 | -0.123 | 38 | +0.421 | UNTESTED | 96 | +0.068 | 42 | +0.357 | UNTESTED | no | 0 |
| 2029 | 116 | +0.049 | 46 | -0.331 | -0.722 | TRAIN-ONLY | `rr3_vol1.5_rsi55-70` | 76 | +0.181 | 32 | -0.285 | TRAIN-ONLY | 30 | +0.155 | 37 | -0.351 | TRAIN-ONLY | 130 | +0.074 | 46 | -0.331 | TRAIN-ONLY | no | 0 |
| 2030 | 85 | +0.043 | 39 | +0.247 | -0.399 | UNTESTED | `rr3_vol2_rsi55-70` | 68 | +0.201 | 28 | +0.257 | UNTESTED | 21 | +0.365 | 33 | -0.071 | UNTESTED | 87 | +0.031 | 39 | +0.247 | UNTESTED | no | 0 |
| 2031 | 92 | -0.185 | 58 | -0.012 | -0.533 | NO-EDGE | `rr2_vol2_rsi50-70` | 80 | -0.025 | 52 | +0.154 | NO-EDGE | 18 | -0.333 | 37 | -0.262 | UNTESTED | 99 | -0.151 | 58 | -0.012 | NO-EDGE | no | 0 |
| 2032 | 102 | -0.160 | 50 | +0.272 | -0.222 | NO-EDGE | `rr2_vol2_rsi55-70` | 83 | -0.004 | 39 | +0.231 | NO-EDGE | 17 | -0.019 | 19 | +0.611 | UNTESTED | 114 | -0.161 | 50 | +0.272 | NO-EDGE | no | 0 |
| 2033 | 109 | -0.193 | 36 | +0.108 | -0.445 | NO-EDGE | `rr2_vol2_rsi55-70` | 85 | +0.000 | 30 | +0.200 | UNTESTED | 22 | +0.091 | 23 | +0.043 | UNTESTED | 113 | -0.221 | 36 | +0.108 | NO-EDGE | no | 0 |
| 2034 | 90 | -0.248 | 40 | -0.175 | -0.700 | NO-EDGE | `rr3_vol2_rsi50-70` | 59 | -0.091 | 25 | -0.126 | UNTESTED | 11 | -0.033 | 29 | -0.276 | UNTESTED | 96 | -0.171 | 40 | -0.175 | NO-EDGE | no | 0 |
| 2035 | 121 | +0.181 | 47 | -0.043 | -0.523 | TRAIN-ONLY | `rr2_vol2_rsi50-70` | 105 | +0.353 | 43 | +0.116 | UNTESTED | 35 | +0.342 | 46 | +0.043 | UNTESTED | 126 | +0.158 | 47 | -0.043 | TRAIN-ONLY | no | 0 |
| 2036 | 106 | +0.160 | 55 | -0.369 | -0.800 | TRAIN-ONLY | `rr2_vol1.5_rsi55-70` | 98 | +0.194 | 51 | -0.362 | TRAIN-ONLY | 33 | +0.364 | 53 | -0.402 | TRAIN-ONLY | 108 | +0.167 | 56 | -0.327 | TRAIN-ONLY | no | 0 |
| 2037 | 121 | +0.041 | 45 | +0.118 | -0.349 | UNTESTED | `rr3_vol1.5_rsi50-70` | 82 | +0.480 | 33 | +0.282 | UNTESTED | 16 | +0.190 | 29 | +0.241 | UNTESTED | 132 | +0.023 | 45 | +0.118 | UNTESTED | no | 0 |
| 2038 | 108 | -0.056 | 56 | +0.179 | -0.250 | NO-EDGE | `rr3_vol2_rsi50-70` | 78 | -0.008 | 46 | +0.565 | NO-EDGE | 34 | -0.294 | 34 | +0.412 | NO-EDGE | 123 | -0.098 | 56 | +0.179 | NO-EDGE | no | 0 |
| 2039 | 114 | -0.096 | 36 | +0.024 | -0.476 | NO-EDGE | `rr2.5_vol2_rsi50-70` | 81 | +0.051 | 30 | +0.283 | UNTESTED | 30 | -0.163 | 40 | -0.303 | NO-EDGE | 125 | -0.103 | 36 | +0.024 | NO-EDGE | no | 0 |
| 2040 | 99 | +0.151 | 46 | +0.207 | -0.286 | UNTESTED | `rr2_vol1.5_rsi50-70` | 99 | +0.151 | 46 | +0.207 | UNTESTED | 0 | +0.000 | 11 | +0.091 | UNTESTED | 107 | +0.178 | 46 | +0.207 | UNTESTED | no | 0 |
| 2041 | 99 | +0.071 | 47 | +0.620 | +0.126 | ROBUST | `rr3_vol2_rsi50-70` | 66 | +0.333 | 32 | +0.440 | UNTESTED | 26 | +0.038 | 30 | +0.470 | UNTESTED | 100 | +0.060 | 47 | +0.620 | ROBUST | yes | 0 |
| 2042 | 104 | +0.067 | 51 | +0.235 | -0.250 | UNTESTED | `rr2_vol1.5_rsi50-70` | 104 | +0.067 | 51 | +0.235 | UNTESTED | 18 | +0.167 | 25 | +0.440 | UNTESTED | 120 | +0.025 | 51 | +0.235 | UNTESTED | no | 0 |
| 2043 | 131 | +0.168 | 48 | +0.087 | -0.401 | UNTESTED | `rr2.5_vol1.5_rsi55-70` | 114 | +0.284 | 40 | +0.254 | UNTESTED | 37 | +0.258 | 50 | +0.343 | UNTESTED | 139 | +0.166 | 48 | +0.087 | UNTESTED | no | 0 |
| 2044 | 117 | +0.026 | 44 | +0.364 | -0.114 | UNTESTED | `rr2.5_vol1.5_rsi55-70` | 85 | +0.112 | 36 | +0.361 | UNTESTED | 22 | -0.045 | 31 | +0.355 | UNTESTED | 118 | +0.017 | 44 | +0.364 | UNTESTED | no | 0 |
| 2045 | 104 | +0.367 | 40 | +0.221 | -0.283 | UNTESTED | `rr2_vol1.5_rsi50-70` | 104 | +0.367 | 40 | +0.221 | UNTESTED | 27 | +0.600 | 33 | +0.364 | UNTESTED | 105 | +0.354 | 40 | +0.221 | UNTESTED | no | 0 |
| 2046 | 99 | +0.182 | 48 | +0.109 | -0.344 | UNTESTED | `rr2_vol1.5_rsi50-70` | 99 | +0.182 | 48 | +0.109 | UNTESTED | 27 | +0.000 | 42 | +0.053 | UNTESTED | 105 | +0.229 | 48 | +0.109 | UNTESTED | no | 0 |
| 2047 | 102 | +0.066 | 50 | -0.040 | -0.460 | TRAIN-ONLY | `rr2_vol1.5_rsi55-70` | 93 | +0.096 | 45 | +0.000 | TRAIN-ONLY | 35 | +0.134 | 35 | -0.057 | TRAIN-ONLY | 106 | +0.009 | 50 | -0.040 | TRAIN-ONLY | no | 0 |
| 2048 | 104 | -0.048 | 48 | -0.040 | -0.493 | NO-EDGE | `rr2_vol2_rsi55-70` | 78 | +0.269 | 43 | -0.207 | TRAIN-ONLY | 39 | -0.308 | 41 | -0.122 | NO-EDGE | 109 | -0.037 | 48 | -0.040 | NO-EDGE | no | 0 |
| 2049 | 97 | -0.103 | 44 | -0.182 | -0.659 | NO-EDGE | `rr2_vol2_rsi55-70` | 90 | +0.200 | 41 | +0.024 | UNTESTED | 29 | -0.172 | 33 | -0.091 | UNTESTED | 98 | -0.020 | 44 | -0.182 | NO-EDGE | no | 0 |
| 2050 | 115 | +0.166 | 48 | +0.336 | -0.125 | UNTESTED | `rr3_vol2_rsi50-70` | 80 | +0.526 | 28 | +0.312 | UNTESTED | 31 | +0.228 | 47 | +0.109 | UNTESTED | 116 | +0.182 | 48 | +0.336 | UNTESTED | no | 0 |

## Adoption path

The only path to real money (enforced by `adoption.py`; no step can be skipped): BACKTEST -> WALK_FORWARD (ROBUST + drawdown check) -> HUMAN_REVIEW of EVERY trade -> >= 14 days on Binance testnet with zero rule violations -> LIVE. A synthetic result never gets past WALK_FORWARD: `adoption check` blocks every record whose provenance is `synthetic:<world>:<seed>` with `ADOPT_provenance`. A calibration writes no adoption records; it measures how often the labels are right when the truth is known.

## Risk disclaimer

This is a research and testing tool, not financial advice and not a recommendation to trade. Backtests and synthetic worlds are simplified models; past or simulated results do not predict future results. Crypto trading can result in the total loss of the capital used.
