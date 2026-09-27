# Calibration: synthetic world `null`, seeds 1001-1050

This report does NOT promise, target or optimise a win rate, and nothing in this package selects on one. A win rate is meaningless without the reward:risk it was earned at: with the mandatory minimum of 2:1 a strategy breaks even at about 33% winners before costs, while a 90% win rate with a 1:9 payoff still loses money. A backtest win rate near 90% is evidence of curve-fitting or look-ahead, not of skill. The target metric is POSITIVE EXPECTANCY (average R per trade, net of fees and slippage) that survives a 70/30 chronological walk-forward with controlled drawdown, with every trade planned at reward:risk >= 2:1. Win rate appears below only as context and is labelled 'context only' wherever it is shown.

Each seed is a full run of the pipeline (6 years of 4H candles, 70/30 walk-forward): baseline, discovery (K = 13 variants, one selected on TRAIN), the ML layer and the expectancy guard, i.e. m = 4 pre-registered TEST looks per seed. **Synthetic results are verification of the methodology, not evidence about real markets.** Each label below combines that candidate's TRAIN and TEST windows (`metrics.label`). ROBUST requires at least 30 trades in each window, TRAIN and TEST avg R > 0, and a one-sided 98.75% lower bound of the TEST mean above zero for BOTH an iid and a calendar-month block bootstrap (the more conservative is used): alpha 0.05 is split over the m = 4 pre-registered candidates (base, discovery-selected, base+ml, base+guard), so when none has an edge the chance that ANY is called ROBUST is at most about 5%; a positive TEST mean that fails only the bound is UNTESTED (positive but not distinguishable from zero after multiplicity correction); the rule was fixed in advance and is never tuned on TEST outcomes. The ML layer's TRAIN columns are its out-of-sample TRAIN gate (CONTRACT v4 D8: a purged 70/30 inner split of TRAIN), the window its label judges; its in-sample full-TRAIN figure is context only. The synthetic worlds share their noise for a given seed (only the planted drift differs), so calibrations of different worlds are independent only on disjoint seed ranges (`--seed-offset`).

- Ground truth: martingale prices (zero drift): no entry/exit rule has an edge before costs, so every strategy has negative expectancy after fees and slippage.
- What the ground truth predicts: nothing should be ROBUST; the expected labels are NO-EDGE, TRAIN-ONLY or UNTESTED, and a ROBUST label here is a false positive.
- **Costs used in every backtest:** fee 0.100% of notional per side (the default: Binance spot taker, no discounts) (`--fee-rate 0.001`), charged on the entry AND on the exit; slippage 0.05% (`--slippage-pct 0.05`) against the trade on market fills (entries and stop exits; take-profit limit exits get none); exchange `binance` (`--exchange-id`; `EXCHANGE:binance` news events block every pair). Starting capital 10,000 per window.
- Cost-aware sizing and targets (CONTRACT.md v2 A1): the planned risk is the ALL-IN loss at the stop (the stop fill after slippage plus both fees), so a clean stop is exactly -1R and the target is placed so that a take-profit nets exactly +2R after fees; the target's price distance is therefore more than 2x the stop distance. Only a gap through the stop loses more than 1R under the touch fill model.
- Coinbase Advanced Trade taker fees at low volume tiers are several times Binance's, so a Coinbase run must pass the account's real tier with `--fee-rate` (a fraction of notional per side: 0.006 = 0.6%) and `--exchange-id coinbase`; the default costs would understate them.

## Label frequencies

| walk-forward label (TRAIN + TEST) | baseline | discovery-selected | ML layer | expectancy guard |
|---|---:|---:|---:|---:|
| ROBUST | 0/50 | 0/50 | 0/50 | 0/50 |
| TRAIN-ONLY | 3/50 | 14/50 | 0/50 | 2/50 |
| UNTESTED | 2/50 | 13/50 | 48/50 | 1/50 |
| NO-EDGE | 45/50 | 23/50 | 2/50 | 47/50 |

## Ground truth vs labels (rates with 90% Wilson intervals)

- Verdict ROBUST (any of the 4 pre-registered candidates, the family-wise rate) = FALSE-POSITIVE rate (no edge exists): 0/50 = 0% (90% Wilson CI 0%-5%).
- baseline labelled ROBUST = FALSE-POSITIVE rate (no edge exists): 0/50 = 0% (90% Wilson CI 0%-5%).
- discovery-selected labelled ROBUST = FALSE-POSITIVE rate (no edge exists): 0/50 = 0% (90% Wilson CI 0%-5%).
- ML layer labelled ROBUST = FALSE-POSITIVE rate (no edge exists): 0/50 = 0% (90% Wilson CI 0%-5%).
- expectancy guard labelled ROBUST = FALSE-POSITIVE rate (no edge exists): 0/50 = 0% (90% Wilson CI 0%-5%).
- ML layer (`base+ml`) ran in 50/50 seeds. Mean avg R, base -> layer: TRAIN -0.146 -> -0.161 (the D8 out-of-sample gate; in-sample full TRAIN, context only: +0.093) | TEST -0.046 -> -0.077; its TEST avg R beat the base in 17/50 = 34% (90% Wilson CI 24%-46%) of those seeds.
  Per seed, mean counts TRAIN | TEST: signals vetoed 216.4 | 98.9; entries at reduced risk 0.0 | 0.0; base trades absent from the layer journal 76.4 | 34.2; layer trades absent from the base journal 23.9 | 12.3.
- Expectancy guard (`base+guard`) ran in 50/50 seeds. Mean avg R, base -> layer: TRAIN -0.146 -> -0.149 | TEST -0.046 -> -0.046; its TEST avg R beat the base in 2/50 = 4% (90% Wilson CI 1%-11%) of those seeds.
  Per seed, mean counts TRAIN | TEST: signals vetoed 0.0 | 0.0; entries at reduced risk 32.0 | 0.4; base trades absent from the layer journal 3.7 | 0.0; layer trades absent from the base journal 12.8 | 0.2.
- ML coefficients (standardised, fitted on TRAIN only): the largest-magnitude coefficient was an hour term (hour_sin/hour_cos) in 8/50 fitted seeds; mean hour-term magnitude sqrt(hour_sin^2 + hour_cos^2) = 0.140 log-odds per TRAIN s.d.
- Invariant violations over all backtests of all seeds: 0.

Drawdown (CONTRACT.md v3 C5, cap revised by v4 D7): every result reports the realised (closed-trade) and the mark-to-market (open positions valued at each 4H close) max drawdown; dd_ok = TEST mark-to-market max drawdown <= min(15%, the 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length, in percent of equity at the risk each trade took). Why the cap: the 15% cap is about 15 consecutive full-size losses at the mandated 1% cluster risk budget; at the 2:1 break-even win probability p* = 1/3 such a streak has probability (2/3)^15 = 0.23%, so a TEST drawdown beyond it is inconsistent with even a break-even strategy at the mandated risk (the TRAIN-bootstrap p95 usually binds first; the 3% weekly-loss halt limits how fast a drawdown accrues, not how deep it goes).

| candidate | seeds that reached the TEST gate (TRAIN avg R > 0, TRAIN n >= 30, TEST n >= 30) | ROBUST, all seeds | ROBUST given the TEST gate was reached | mean TRAIN avg R | mean TEST avg R | mean TRAIN n | mean TEST n | mean TEST max DD % realised / MTM / limit | dd_ok (TEST MTM within limit) |
|---|---|---|---|---:|---:|---:|---:|---|---|
| baseline | 5/50 = 10% (90% Wilson CI 5%-19%) | 0/50 = 0% (90% Wilson CI 0%-5%) | 0/5 = 0% (90% Wilson CI 0%-35%) | -0.146 | -0.046 | 101.6 | 45.6 | 9.29 / 9.95 / 14.95 | 42/50 = 84% (90% Wilson CI 74%-91%) |
| discovery-selected | 19/50 = 38% (90% Wilson CI 28%-50%) | 0/50 = 0% (90% Wilson CI 0%-5%) | 0/19 = 0% (90% Wilson CI 0%-12%) | -0.026 | -0.048 | 79.9 | 35.6 | 8.54 / 9.46 / 14.15 | 46/50 = 92% (90% Wilson CI 83%-96%) |
| ML layer | 0/50 = 0% (90% Wilson CI 0%-5%) | 0/50 = 0% (90% Wilson CI 0%-5%) | 0/0 (n/a) | -0.161 | -0.077 | 15.6 | 23.7 | 6.35 / 6.99 / 11.95 | 40/50 = 80% (90% Wilson CI 69%-88%) |
| expectancy guard | 3/50 = 6% (90% Wilson CI 2%-14%) | 0/50 = 0% (90% Wilson CI 0%-5%) | 0/3 = 0% (90% Wilson CI 0%-47%) | -0.149 | -0.046 | 110.7 | 45.8 | 9.30 / 9.94 / 14.71 | 42/50 = 84% (90% Wilson CI 74%-91%) |

## Per seed (TRAIN and TEST side by side)

| seed | base TRAIN n | base TRAIN avg R | base TEST n | base TEST avg R | base TEST adj LB | base label | selected | selected TRAIN n | selected TRAIN avg R | selected TEST n | selected TEST avg R | selected label | ML TRAIN n | ML TRAIN avg R (out-of-sample gate) | ML TEST n | ML TEST avg R | ML label | guard TRAIN n | guard TRAIN avg R | guard TEST n | guard TEST avg R | guard label | verdict ROBUST | violations |
|---:|---:|---:|---:|---:|---:|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---|---|---:|
| 1001 | 97 | -0.134 | 65 | +0.015 | -0.410 | NO-EDGE | `rr3_vol1.5_rsi55-70` | 68 | -0.059 | 46 | +0.043 | NO-EDGE | 23 | -0.478 | 40 | +0.200 | UNTESTED | 106 | -0.179 | 66 | +0.045 | NO-EDGE | no | 0 |
| 1002 | 94 | -0.202 | 55 | -0.236 | -0.618 | NO-EDGE | `rr2_vol2_rsi50-70` | 89 | -0.157 | 44 | -0.250 | NO-EDGE | 10 | -0.700 | 16 | -0.250 | UNTESTED | 97 | -0.227 | 55 | -0.236 | NO-EDGE | no | 0 |
| 1003 | 92 | -0.250 | 46 | +0.239 | -0.217 | NO-EDGE | `rr2_vol2_rsi55-70` | 72 | -0.083 | 39 | -0.049 | NO-EDGE | 8 | -0.250 | 10 | -0.400 | UNTESTED | 99 | -0.242 | 46 | +0.239 | NO-EDGE | no | 0 |
| 1004 | 102 | -0.075 | 44 | +0.227 | -0.294 | NO-EDGE | `rr2_vol1.5_rsi50-70` | 102 | -0.075 | 44 | +0.227 | NO-EDGE | 1 | -1.000 | 5 | +1.400 | UNTESTED | 120 | -0.139 | 44 | +0.227 | NO-EDGE | no | 0 |
| 1005 | 104 | -0.048 | 55 | -0.108 | -0.490 | NO-EDGE | `rr3_vol2_rsi55-70` | 82 | +0.139 | 33 | -0.363 | TRAIN-ONLY | 21 | -0.143 | 41 | -0.024 | UNTESTED | 115 | -0.061 | 55 | -0.108 | NO-EDGE | no | 0 |
| 1006 | 99 | -0.273 | 47 | -0.106 | -0.563 | NO-EDGE | `rr3_vol2_rsi55-70` | 74 | -0.103 | 32 | -0.403 | NO-EDGE | 10 | -0.400 | 16 | +0.125 | UNTESTED | 111 | -0.228 | 47 | -0.106 | NO-EDGE | no | 0 |
| 1007 | 94 | -0.099 | 47 | +0.363 | -0.143 | NO-EDGE | `rr3_vol1.5_rsi55-70` | 69 | +0.054 | 30 | +0.369 | UNTESTED | 25 | -0.292 | 34 | +0.355 | UNTESTED | 122 | -0.134 | 47 | +0.363 | NO-EDGE | no | 0 |
| 1008 | 90 | -0.258 | 33 | -0.535 | -0.922 | NO-EDGE | `rr3_vol2_rsi55-70` | 56 | -0.058 | 19 | -0.350 | UNTESTED | 3 | +0.000 | 7 | -0.571 | UNTESTED | 96 | -0.242 | 33 | -0.535 | NO-EDGE | no | 0 |
| 1009 | 90 | -0.097 | 42 | -0.214 | -0.676 | NO-EDGE | `rr2.5_vol1.5_rsi50-70` | 75 | +0.077 | 37 | -0.243 | TRAIN-ONLY | 14 | +0.093 | 16 | -0.438 | UNTESTED | 99 | -0.148 | 42 | -0.214 | NO-EDGE | no | 0 |
| 1010 | 105 | -0.229 | 39 | +0.047 | -0.519 | NO-EDGE | `rr2.5_vol2_rsi50-70` | 99 | -0.081 | 38 | -0.006 | NO-EDGE | 32 | -0.250 | 36 | -0.199 | NO-EDGE | 117 | -0.256 | 39 | +0.047 | NO-EDGE | no | 0 |
| 1011 | 90 | -0.233 | 42 | +0.071 | -0.429 | NO-EDGE | `rr2.5_vol2_rsi55-70` | 69 | +0.015 | 28 | +0.250 | UNTESTED | 14 | -0.143 | 18 | +0.167 | UNTESTED | 103 | -0.214 | 42 | +0.071 | NO-EDGE | no | 0 |
| 1012 | 104 | -0.077 | 39 | -0.138 | -0.600 | NO-EDGE | `rr3_vol1.5_rsi50-70` | 72 | +0.111 | 31 | +0.052 | UNTESTED | 6 | -0.500 | 17 | -0.118 | UNTESTED | 105 | -0.057 | 39 | -0.138 | NO-EDGE | no | 0 |
| 1013 | 122 | -0.298 | 48 | -0.250 | -0.625 | NO-EDGE | `rr2.5_vol1.5_rsi55-70` | 95 | -0.135 | 36 | -0.222 | NO-EDGE | 20 | -0.250 | 27 | -0.333 | UNTESTED | 135 | -0.254 | 48 | -0.250 | NO-EDGE | no | 0 |
| 1014 | 117 | -0.205 | 33 | -0.141 | -0.636 | NO-EDGE | `rr3_vol2_rsi55-70` | 60 | -0.067 | 27 | -0.062 | UNTESTED | 25 | -0.280 | 21 | -0.222 | UNTESTED | 123 | -0.220 | 33 | -0.141 | NO-EDGE | no | 0 |
| 1015 | 89 | -0.326 | 43 | -0.023 | -0.512 | NO-EDGE | `rr2.5_vol2_rsi55-70` | 68 | -0.228 | 18 | +0.167 | UNTESTED | 13 | -0.769 | 17 | +0.059 | UNTESTED | 92 | -0.348 | 43 | -0.023 | NO-EDGE | no | 0 |
| 1016 | 97 | -0.258 | 42 | -0.071 | -0.510 | NO-EDGE | `rr2_vol2_rsi55-70` | 78 | -0.154 | 38 | -0.210 | NO-EDGE | 22 | +0.364 | 30 | -0.200 | UNTESTED | 113 | -0.257 | 42 | -0.071 | NO-EDGE | no | 0 |
| 1017 | 111 | -0.135 | 40 | +0.185 | -0.366 | NO-EDGE | `rr2_vol1.5_rsi55-70` | 98 | -0.112 | 40 | +0.185 | NO-EDGE | 18 | -0.667 | 10 | +0.200 | UNTESTED | 131 | -0.153 | 40 | +0.185 | NO-EDGE | no | 0 |
| 1018 | 108 | -0.109 | 42 | -0.373 | -0.838 | NO-EDGE | `rr3_vol1.5_rsi50-70` | 85 | +0.148 | 35 | -0.498 | TRAIN-ONLY | 26 | -0.183 | 32 | -0.459 | UNTESTED | 115 | -0.111 | 42 | -0.373 | NO-EDGE | no | 0 |
| 1019 | 100 | -0.184 | 48 | +0.042 | -0.455 | NO-EDGE | `rr2.5_vol1.5_rsi55-70` | 80 | -0.144 | 34 | +0.179 | NO-EDGE | 0 | +0.000 | 2 | +2.000 | UNTESTED | 112 | -0.137 | 48 | +0.042 | NO-EDGE | no | 0 |
| 1020 | 118 | -0.237 | 39 | -0.342 | -0.769 | NO-EDGE | `rr2.5_vol1.5_rsi55-70` | 99 | -0.222 | 38 | -0.127 | NO-EDGE | 9 | -0.333 | 9 | -0.333 | UNTESTED | 121 | -0.256 | 39 | -0.342 | NO-EDGE | no | 0 |
| 1021 | 94 | -0.282 | 56 | -0.196 | -0.571 | NO-EDGE | `rr2.5_vol2_rsi55-70` | 67 | -0.094 | 37 | -0.338 | NO-EDGE | 22 | -0.182 | 43 | -0.442 | UNTESTED | 100 | -0.253 | 56 | -0.196 | NO-EDGE | no | 0 |
| 1022 | 109 | -0.182 | 41 | +0.171 | -0.342 | NO-EDGE | `rr3_vol2_rsi55-70` | 67 | +0.068 | 25 | -0.040 | UNTESTED | 24 | -0.286 | 11 | -0.455 | UNTESTED | 121 | -0.189 | 41 | +0.171 | NO-EDGE | no | 0 |
| 1023 | 110 | -0.209 | 38 | -0.103 | -0.577 | NO-EDGE | `rr3_vol2_rsi55-70` | 68 | -0.118 | 30 | -0.168 | NO-EDGE | 11 | +0.091 | 24 | -0.250 | UNTESTED | 125 | -0.160 | 38 | -0.103 | NO-EDGE | no | 0 |
| 1024 | 99 | +0.061 | 49 | +0.062 | -0.389 | UNTESTED | `rr2_vol1.5_rsi50-70` | 99 | +0.061 | 49 | +0.062 | UNTESTED | 18 | +0.667 | 26 | -0.036 | UNTESTED | 101 | +0.069 | 49 | +0.062 | UNTESTED | no | 0 |
| 1025 | 84 | -0.267 | 55 | +0.200 | -0.238 | NO-EDGE | `rr3_vol1.5_rsi55-70` | 75 | -0.233 | 34 | +0.202 | NO-EDGE | 22 | -0.112 | 19 | -0.210 | UNTESTED | 93 | -0.259 | 55 | +0.200 | NO-EDGE | no | 0 |
| 1026 | 109 | +0.073 | 40 | -0.145 | -0.612 | TRAIN-ONLY | `rr2_vol1.5_rsi55-70` | 102 | +0.118 | 40 | -0.145 | TRAIN-ONLY | 26 | -0.308 | 33 | -0.055 | UNTESTED | 111 | +0.081 | 40 | -0.145 | TRAIN-ONLY | no | 0 |
| 1027 | 112 | -0.223 | 38 | -0.132 | -0.605 | NO-EDGE | `rr3_vol2_rsi50-70` | 88 | -0.171 | 30 | -0.306 | NO-EDGE | 0 | +0.000 | 0 | +0.000 | UNTESTED | 116 | -0.224 | 38 | -0.132 | NO-EDGE | no | 0 |
| 1028 | 103 | -0.135 | 51 | -0.118 | -0.529 | NO-EDGE | `rr2.5_vol1.5_rsi55-70` | 74 | +0.210 | 38 | -0.057 | TRAIN-ONLY | 16 | -0.120 | 19 | -0.135 | UNTESTED | 113 | -0.159 | 51 | -0.118 | NO-EDGE | no | 0 |
| 1029 | 96 | -0.344 | 47 | +0.085 | -0.362 | NO-EDGE | `rr3_vol1.5_rsi55-70` | 77 | -0.273 | 37 | +0.310 | NO-EDGE | 9 | +0.000 | 14 | +0.071 | UNTESTED | 107 | -0.355 | 47 | +0.085 | NO-EDGE | no | 0 |
| 1030 | 103 | -0.019 | 48 | -0.344 | -0.765 | NO-EDGE | `rr2.5_vol1.5_rsi50-70` | 101 | +0.025 | 49 | -0.327 | TRAIN-ONLY | 16 | +0.252 | 36 | -0.500 | UNTESTED | 112 | +0.025 | 48 | -0.344 | TRAIN-ONLY | no | 0 |
| 1031 | 106 | -0.097 | 43 | -0.207 | -0.626 | NO-EDGE | `rr2.5_vol1.5_rsi55-70` | 90 | +0.002 | 33 | -0.331 | TRAIN-ONLY | 14 | +0.071 | 32 | -0.344 | UNTESTED | 115 | -0.158 | 43 | -0.207 | NO-EDGE | no | 0 |
| 1032 | 103 | -0.417 | 46 | -0.462 | -0.872 | NO-EDGE | `rr2_vol2_rsi50-70` | 83 | -0.246 | 41 | -0.342 | NO-EDGE | 0 | +0.000 | 0 | +0.000 | UNTESTED | 115 | -0.426 | 46 | -0.462 | NO-EDGE | no | 0 |
| 1033 | 109 | -0.064 | 43 | +0.186 | -0.302 | NO-EDGE | `rr2.5_vol2_rsi55-70` | 79 | +0.019 | 27 | -0.023 | UNTESTED | 28 | -0.357 | 39 | +0.154 | UNTESTED | 114 | -0.053 | 43 | +0.186 | NO-EDGE | no | 0 |
| 1034 | 108 | +0.041 | 46 | +0.076 | -0.417 | UNTESTED | `rr2.5_vol1.5_rsi55-70` | 92 | +0.157 | 37 | -0.014 | TRAIN-ONLY | 24 | +0.184 | 32 | +0.196 | UNTESTED | 117 | -0.014 | 46 | +0.076 | NO-EDGE | no | 0 |
| 1035 | 100 | -0.172 | 46 | -0.022 | -0.571 | NO-EDGE | `rr3_vol1.5_rsi55-70` | 76 | +0.138 | 33 | -0.030 | TRAIN-ONLY | 20 | -0.036 | 38 | -0.053 | UNTESTED | 121 | -0.158 | 46 | -0.022 | NO-EDGE | no | 0 |
| 1036 | 105 | -0.103 | 55 | -0.222 | -0.611 | NO-EDGE | `rr3_vol2_rsi50-70` | 78 | -0.061 | 44 | -0.273 | NO-EDGE | 11 | -0.182 | 35 | -0.229 | UNTESTED | 111 | -0.085 | 60 | -0.236 | NO-EDGE | no | 0 |
| 1037 | 100 | -0.100 | 45 | -0.133 | -0.667 | NO-EDGE | `rr2.5_vol2_rsi55-70` | 63 | +0.111 | 34 | -0.201 | TRAIN-ONLY | 11 | +0.206 | 32 | -0.438 | UNTESTED | 112 | -0.062 | 48 | -0.125 | NO-EDGE | no | 0 |
| 1038 | 110 | +0.036 | 52 | -0.051 | -0.455 | TRAIN-ONLY | `rr2.5_vol1.5_rsi55-70` | 88 | +0.074 | 44 | -0.015 | TRAIN-ONLY | 27 | -0.222 | 33 | -0.182 | UNTESTED | 117 | -0.020 | 52 | -0.051 | NO-EDGE | no | 0 |
| 1039 | 85 | -0.047 | 42 | -0.143 | -0.615 | NO-EDGE | `rr2_vol1.5_rsi55-70` | 80 | +0.013 | 37 | -0.108 | TRAIN-ONLY | 9 | -0.333 | 19 | -0.368 | UNTESTED | 91 | -0.077 | 42 | -0.143 | NO-EDGE | no | 0 |
| 1040 | 104 | -0.164 | 39 | -0.048 | -0.676 | NO-EDGE | `rr2_vol2_rsi55-70` | 86 | -0.093 | 27 | +0.153 | UNTESTED | 15 | -0.200 | 32 | -0.375 | UNTESTED | 110 | -0.236 | 39 | -0.048 | NO-EDGE | no | 0 |
| 1041 | 104 | -0.106 | 47 | +0.213 | -0.250 | NO-EDGE | `rr2.5_vol2_rsi50-70` | 86 | -0.023 | 34 | +0.282 | NO-EDGE | 0 | +0.000 | 1 | -1.000 | UNTESTED | 117 | -0.205 | 47 | +0.213 | NO-EDGE | no | 0 |
| 1042 | 95 | -0.147 | 47 | +0.149 | -0.298 | NO-EDGE | `rr2.5_vol2_rsi50-70` | 79 | -0.070 | 35 | +0.400 | NO-EDGE | 0 | +0.000 | 19 | +0.263 | UNTESTED | 109 | -0.092 | 47 | +0.149 | NO-EDGE | no | 0 |
| 1043 | 89 | -0.148 | 39 | +0.308 | -0.231 | NO-EDGE | `rr3_vol2_rsi55-70` | 67 | +0.120 | 29 | +0.517 | UNTESTED | 18 | +0.211 | 43 | +0.186 | UNTESTED | 100 | -0.122 | 39 | +0.308 | NO-EDGE | no | 0 |
| 1044 | 95 | -0.048 | 49 | +0.000 | -0.429 | NO-EDGE | `rr3_vol1.5_rsi55-70` | 61 | +0.155 | 35 | +0.285 | UNTESTED | 11 | -0.182 | 25 | +0.320 | UNTESTED | 95 | -0.048 | 49 | +0.000 | NO-EDGE | no | 0 |
| 1045 | 132 | -0.079 | 48 | +0.250 | -0.351 | NO-EDGE | `rr3_vol1.5_rsi50-70` | 95 | -0.057 | 38 | -0.053 | NO-EDGE | 30 | -0.476 | 43 | +0.046 | NO-EDGE | 148 | -0.072 | 48 | +0.250 | NO-EDGE | no | 0 |
| 1046 | 88 | -0.025 | 49 | -0.204 | -0.654 | NO-EDGE | `rr3_vol2_rsi55-70` | 58 | -0.035 | 36 | -0.176 | NO-EDGE | 14 | +0.500 | 35 | -0.314 | UNTESTED | 94 | -0.024 | 49 | -0.204 | NO-EDGE | no | 0 |
| 1047 | 93 | -0.083 | 51 | -0.023 | -0.474 | NO-EDGE | `rr3_vol1.5_rsi55-70` | 75 | +0.038 | 34 | +0.101 | UNTESTED | 26 | -0.237 | 19 | -0.024 | UNTESTED | 98 | -0.056 | 51 | -0.023 | NO-EDGE | no | 0 |
| 1048 | 77 | +0.013 | 52 | -0.127 | -0.543 | TRAIN-ONLY | `rr2_vol1.5_rsi50-70` | 77 | +0.013 | 52 | -0.127 | TRAIN-ONLY | 14 | -0.143 | 29 | -0.069 | UNTESTED | 79 | -0.013 | 52 | -0.127 | NO-EDGE | no | 0 |
| 1049 | 114 | -0.210 | 39 | +0.124 | -0.400 | NO-EDGE | `rr2_vol2_rsi55-70` | 83 | -0.060 | 38 | -0.170 | NO-EDGE | 16 | -0.625 | 21 | -0.341 | UNTESTED | 118 | -0.186 | 39 | +0.124 | NO-EDGE | no | 0 |
| 1050 | 119 | -0.099 | 50 | -0.100 | -0.520 | NO-EDGE | `rr2.5_vol2_rsi50-70` | 93 | +0.054 | 38 | -0.137 | TRAIN-ONLY | 27 | -0.040 | 28 | -0.204 | UNTESTED | 121 | -0.064 | 51 | -0.118 | NO-EDGE | no | 0 |

## Adoption path

The only path to real money (enforced by `adoption.py`; no step can be skipped): BACKTEST -> WALK_FORWARD (ROBUST + drawdown check) -> HUMAN_REVIEW of EVERY trade -> >= 14 days on Binance testnet with zero rule violations -> LIVE. A synthetic result never gets past WALK_FORWARD: `adoption check` blocks every record whose provenance is `synthetic:<world>:<seed>` with `ADOPT_provenance`. A calibration writes no adoption records; it measures how often the labels are right when the truth is known.

## Risk disclaimer

This is a research and testing tool, not financial advice and not a recommendation to trade. Backtests and synthetic worlds are simplified models; past or simulated results do not predict future results. Crypto trading can result in the total loss of the capital used.
