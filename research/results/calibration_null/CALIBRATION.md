# Calibration: synthetic world `null`, seeds 1-50

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
| TRAIN-ONLY | 3/50 | 12/50 | 1/50 | 4/50 |
| UNTESTED | 7/50 | 16/50 | 49/50 | 7/50 |
| NO-EDGE | 40/50 | 22/50 | 0/50 | 39/50 |

## Ground truth vs labels (rates with 90% Wilson intervals)

- Verdict ROBUST (any of the 4 pre-registered candidates, the family-wise rate) = FALSE-POSITIVE rate (no edge exists): 0/50 = 0% (90% Wilson CI 0%-5%).
- baseline labelled ROBUST = FALSE-POSITIVE rate (no edge exists): 0/50 = 0% (90% Wilson CI 0%-5%).
- discovery-selected labelled ROBUST = FALSE-POSITIVE rate (no edge exists): 0/50 = 0% (90% Wilson CI 0%-5%).
- ML layer labelled ROBUST = FALSE-POSITIVE rate (no edge exists): 0/50 = 0% (90% Wilson CI 0%-5%).
- expectancy guard labelled ROBUST = FALSE-POSITIVE rate (no edge exists): 0/50 = 0% (90% Wilson CI 0%-5%).
- ML layer (`base+ml`) ran in 50/50 seeds. Mean avg R, base -> layer: TRAIN -0.110 -> -0.068 (the D8 out-of-sample gate; in-sample full TRAIN, context only: +0.055) | TEST -0.086 -> -0.150; its TEST avg R beat the base in 23/50 = 46% (90% Wilson CI 35%-57%) of those seeds.
  Per seed, mean counts TRAIN | TEST: signals vetoed 210.6 | 99.2; entries at reduced risk 0.0 | 0.0; base trades absent from the layer journal 73.8 | 34.2; layer trades absent from the base journal 25.7 | 12.2.
- Expectancy guard (`base+guard`) ran in 50/50 seeds. Mean avg R, base -> layer: TRAIN -0.110 -> -0.103 | TEST -0.086 -> -0.087; its TEST avg R beat the base in 4/50 = 8% (90% Wilson CI 4%-17%) of those seeds.
  Per seed, mean counts TRAIN | TEST: signals vetoed 0.0 | 0.0; entries at reduced risk 29.4 | 0.8; base trades absent from the layer journal 3.3 | 0.0; layer trades absent from the base journal 11.3 | 0.3.
- ML coefficients (standardised, fitted on TRAIN only): the largest-magnitude coefficient was an hour term (hour_sin/hour_cos) in 11/50 fitted seeds; mean hour-term magnitude sqrt(hour_sin^2 + hour_cos^2) = 0.141 log-odds per TRAIN s.d.
- Invariant violations over all backtests of all seeds: 0.

Drawdown (CONTRACT.md v3 C5, cap revised by v4 D7): every result reports the realised (closed-trade) and the mark-to-market (open positions valued at each 4H close) max drawdown; dd_ok = TEST mark-to-market max drawdown <= min(15%, the 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length, in percent of equity at the risk each trade took). Why the cap: the 15% cap is about 15 consecutive full-size losses at the mandated 1% cluster risk budget; at the 2:1 break-even win probability p* = 1/3 such a streak has probability (2/3)^15 = 0.23%, so a TEST drawdown beyond it is inconsistent with even a break-even strategy at the mandated risk (the TRAIN-bootstrap p95 usually binds first; the 3% weekly-loss halt limits how fast a drawdown accrues, not how deep it goes).

| candidate | seeds that reached the TEST gate (TRAIN avg R > 0, TRAIN n >= 30, TEST n >= 30) | ROBUST, all seeds | ROBUST given the TEST gate was reached | mean TRAIN avg R | mean TEST avg R | mean TRAIN n | mean TEST n | mean TEST max DD % realised / MTM / limit | dd_ok (TEST MTM within limit) |
|---|---|---|---|---:|---:|---:|---:|---|---|
| baseline | 9/50 = 18% (90% Wilson CI 11%-29%) | 0/50 = 0% (90% Wilson CI 0%-5%) | 0/9 = 0% (90% Wilson CI 0%-23%) | -0.110 | -0.086 | 100.8 | 46.0 | 10.03 / 10.77 / 14.55 | 43/50 = 86% (90% Wilson CI 76%-92%) |
| discovery-selected | 20/50 = 40% (90% Wilson CI 29%-52%) | 0/50 = 0% (90% Wilson CI 0%-5%) | 0/20 = 0% (90% Wilson CI 0%-12%) | +0.013 | -0.097 | 78.5 | 37.5 | 9.74 / 10.52 / 13.64 | 41/50 = 82% (90% Wilson CI 71%-89%) |
| ML layer | 1/50 = 2% (90% Wilson CI 0%-8%) | 0/50 = 0% (90% Wilson CI 0%-5%) | 0/1 = 0% (90% Wilson CI 0%-73%) | -0.068 | -0.150 | 15.7 | 24.1 | 6.34 / 6.92 / 10.64 | 41/50 = 82% (90% Wilson CI 71%-89%) |
| expectancy guard | 10/50 = 20% (90% Wilson CI 12%-31%) | 0/50 = 0% (90% Wilson CI 0%-5%) | 0/10 = 0% (90% Wilson CI 0%-21%) | -0.103 | -0.087 | 108.7 | 46.3 | 10.03 / 10.78 / 14.15 | 41/50 = 82% (90% Wilson CI 71%-89%) |

## Per seed (TRAIN and TEST side by side)

| seed | base TRAIN n | base TRAIN avg R | base TEST n | base TEST avg R | base TEST adj LB | base label | selected | selected TRAIN n | selected TRAIN avg R | selected TEST n | selected TEST avg R | selected label | ML TRAIN n | ML TRAIN avg R (out-of-sample gate) | ML TEST n | ML TEST avg R | ML label | guard TRAIN n | guard TRAIN avg R | guard TEST n | guard TEST avg R | guard label | verdict ROBUST | violations |
|---:|---:|---:|---:|---:|---:|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---|---|---:|
| 1 | 98 | -0.138 | 58 | -0.121 | -0.491 | NO-EDGE | `rr2.5_vol2_rsi50-70` | 81 | -0.086 | 49 | -0.143 | NO-EDGE | 8 | -1.000 | 17 | -0.294 | UNTESTED | 108 | -0.183 | 58 | -0.121 | NO-EDGE | no | 0 |
| 2 | 91 | -0.426 | 39 | -0.231 | -0.692 | NO-EDGE | `rr2.5_vol2_rsi55-70` | 63 | -0.333 | 30 | +0.167 | NO-EDGE | 13 | -1.000 | 10 | -0.700 | UNTESTED | 99 | -0.382 | 39 | -0.231 | NO-EDGE | no | 0 |
| 3 | 89 | -0.124 | 36 | +0.542 | -0.028 | NO-EDGE | `rr3_vol2_rsi50-70` | 60 | +0.133 | 27 | +0.532 | UNTESTED | 13 | -0.077 | 2 | -1.000 | UNTESTED | 95 | -0.084 | 36 | +0.542 | NO-EDGE | no | 0 |
| 4 | 97 | -0.041 | 51 | -0.176 | -0.588 | NO-EDGE | `rr2_vol2_rsi50-70` | 83 | +0.084 | 33 | -0.455 | TRAIN-ONLY | 13 | +0.154 | 33 | -0.364 | UNTESTED | 105 | +0.000 | 51 | -0.176 | NO-EDGE | no | 0 |
| 5 | 105 | +0.029 | 46 | +0.081 | -0.403 | UNTESTED | `rr3_vol1.5_rsi55-70` | 82 | +0.268 | 36 | +0.271 | UNTESTED | 25 | +0.080 | 40 | -0.175 | UNTESTED | 110 | +0.064 | 46 | +0.081 | UNTESTED | no | 0 |
| 6 | 113 | -0.018 | 38 | -0.605 | -1.000 | NO-EDGE | `rr2.5_vol2_rsi50-70` | 80 | +0.006 | 25 | -0.580 | UNTESTED | 29 | -0.172 | 29 | -0.793 | UNTESTED | 118 | -0.034 | 38 | -0.605 | NO-EDGE | no | 0 |
| 7 | 87 | -0.112 | 40 | -0.308 | -0.757 | NO-EDGE | `rr2_vol1.5_rsi50-70` | 87 | -0.112 | 40 | -0.308 | NO-EDGE | 17 | -0.118 | 11 | -0.727 | UNTESTED | 90 | -0.108 | 40 | -0.308 | NO-EDGE | no | 0 |
| 8 | 110 | +0.145 | 35 | +0.029 | -0.486 | UNTESTED | `rr2.5_vol2_rsi50-70` | 85 | +0.318 | 23 | -0.087 | UNTESTED | 15 | +0.400 | 31 | +0.065 | UNTESTED | 119 | +0.109 | 35 | +0.029 | UNTESTED | no | 0 |
| 9 | 113 | -0.071 | 51 | -0.059 | -0.533 | NO-EDGE | `rr3_vol2_rsi55-70` | 69 | +0.101 | 34 | +0.176 | UNTESTED | 20 | +0.050 | 28 | -0.036 | UNTESTED | 114 | -0.026 | 51 | -0.059 | NO-EDGE | no | 0 |
| 10 | 99 | -0.030 | 37 | +0.077 | -0.433 | NO-EDGE | `rr3_vol1.5_rsi55-70` | 72 | +0.105 | 32 | +0.000 | TRAIN-ONLY | 12 | +0.000 | 12 | +0.321 | UNTESTED | 103 | -0.039 | 37 | +0.077 | NO-EDGE | no | 0 |
| 11 | 91 | -0.077 | 56 | +0.010 | -0.411 | NO-EDGE | `rr2_vol1.5_rsi55-70` | 85 | -0.047 | 54 | -0.119 | NO-EDGE | 4 | -0.250 | 26 | -0.192 | UNTESTED | 105 | -0.029 | 58 | +0.027 | NO-EDGE | no | 0 |
| 12 | 128 | -0.039 | 54 | +0.203 | -0.311 | NO-EDGE | `rr2_vol2_rsi50-70` | 105 | +0.086 | 55 | +0.290 | UNTESTED | 31 | -0.032 | 28 | +0.179 | UNTESTED | 135 | -0.022 | 54 | +0.203 | NO-EDGE | no | 0 |
| 13 | 98 | -0.235 | 51 | +0.176 | -0.294 | NO-EDGE | `rr2.5_vol1.5_rsi50-70` | 89 | -0.135 | 40 | +0.138 | NO-EDGE | 6 | +0.500 | 23 | +0.174 | UNTESTED | 106 | -0.236 | 51 | +0.176 | NO-EDGE | no | 0 |
| 14 | 91 | -0.011 | 54 | -0.111 | -0.529 | NO-EDGE | `rr3_vol2_rsi50-70` | 66 | +0.051 | 43 | +0.023 | UNTESTED | 29 | -0.276 | 33 | +0.000 | UNTESTED | 96 | +0.000 | 54 | -0.111 | NO-EDGE | no | 0 |
| 15 | 102 | +0.029 | 38 | +0.210 | -0.317 | UNTESTED | `rr2_vol1.5_rsi55-70` | 88 | +0.091 | 37 | -0.001 | TRAIN-ONLY | 18 | +0.333 | 30 | +0.033 | UNTESTED | 112 | +0.045 | 38 | +0.210 | UNTESTED | no | 0 |
| 16 | 98 | -0.204 | 37 | +0.071 | -0.468 | NO-EDGE | `rr2_vol2_rsi55-70` | 85 | -0.047 | 33 | -0.163 | NO-EDGE | 15 | +0.600 | 23 | -0.060 | UNTESTED | 104 | -0.192 | 37 | +0.071 | NO-EDGE | no | 0 |
| 17 | 113 | -0.253 | 35 | -0.314 | -0.800 | NO-EDGE | `rr2.5_vol2_rsi55-70` | 76 | -0.065 | 30 | -0.067 | NO-EDGE | 17 | -0.568 | 22 | -0.318 | UNTESTED | 123 | -0.260 | 35 | -0.314 | NO-EDGE | no | 0 |
| 18 | 103 | -0.113 | 33 | +0.182 | -0.364 | NO-EDGE | `rr2.5_vol2_rsi55-70` | 80 | +0.054 | 28 | +0.375 | UNTESTED | 26 | -0.484 | 24 | +0.046 | UNTESTED | 105 | -0.129 | 33 | +0.182 | NO-EDGE | no | 0 |
| 19 | 103 | -0.164 | 46 | -0.193 | -0.763 | NO-EDGE | `rr3_vol2_rsi55-70` | 62 | +0.107 | 30 | -0.333 | TRAIN-ONLY | 19 | +0.018 | 33 | -0.148 | UNTESTED | 112 | -0.151 | 46 | -0.193 | NO-EDGE | no | 0 |
| 20 | 94 | -0.298 | 43 | +0.116 | -0.372 | NO-EDGE | `rr2.5_vol2_rsi55-70` | 73 | -0.137 | 32 | +0.203 | NO-EDGE | 11 | -0.455 | 14 | -0.143 | UNTESTED | 109 | -0.284 | 43 | +0.116 | NO-EDGE | no | 0 |
| 21 | 82 | -0.097 | 45 | -0.032 | -0.467 | NO-EDGE | `rr2_vol1.5_rsi55-70` | 71 | +0.001 | 45 | -0.032 | TRAIN-ONLY | 6 | -0.500 | 3 | +0.000 | UNTESTED | 87 | -0.080 | 45 | -0.032 | NO-EDGE | no | 0 |
| 22 | 109 | -0.064 | 49 | +0.030 | -0.398 | NO-EDGE | `rr3_vol2_rsi55-70` | 71 | +0.127 | 32 | +0.078 | UNTESTED | 22 | -0.182 | 55 | +0.287 | UNTESTED | 121 | -0.033 | 49 | +0.030 | NO-EDGE | no | 0 |
| 23 | 104 | -0.324 | 49 | +0.317 | -0.143 | NO-EDGE | `rr3_vol2_rsi50-70` | 86 | -0.163 | 42 | +0.179 | NO-EDGE | 8 | +0.500 | 4 | +0.500 | UNTESTED | 117 | -0.323 | 49 | +0.317 | NO-EDGE | no | 0 |
| 24 | 106 | -0.102 | 43 | -0.183 | -0.676 | NO-EDGE | `rr2.5_vol1.5_rsi55-70` | 95 | +0.091 | 37 | -0.314 | TRAIN-ONLY | 16 | +0.125 | 34 | -0.118 | UNTESTED | 115 | -0.047 | 43 | -0.183 | NO-EDGE | no | 0 |
| 25 | 110 | -0.209 | 43 | +0.316 | -0.173 | NO-EDGE | `rr3_vol1.5_rsi55-70` | 74 | -0.100 | 31 | +0.502 | NO-EDGE | 6 | +0.000 | 18 | +0.500 | UNTESTED | 121 | -0.157 | 43 | +0.316 | NO-EDGE | no | 0 |
| 26 | 77 | -0.455 | 45 | -0.122 | -0.533 | NO-EDGE | `rr2_vol1.5_rsi50-70` | 77 | -0.455 | 45 | -0.122 | NO-EDGE | 0 | +0.000 | 2 | +0.500 | UNTESTED | 84 | -0.429 | 49 | -0.194 | NO-EDGE | no | 0 |
| 27 | 103 | -0.184 | 40 | -0.068 | -0.517 | NO-EDGE | `rr3_vol1.5_rsi55-70` | 66 | +0.091 | 27 | -0.359 | UNTESTED | 19 | -0.423 | 21 | +0.036 | UNTESTED | 109 | -0.147 | 40 | -0.068 | NO-EDGE | no | 0 |
| 28 | 117 | -0.051 | 51 | -0.353 | -0.732 | NO-EDGE | `rr3_vol2_rsi50-70` | 69 | -0.015 | 40 | -0.200 | NO-EDGE | 25 | -0.160 | 30 | -0.200 | UNTESTED | 119 | -0.067 | 51 | -0.353 | NO-EDGE | no | 0 |
| 29 | 106 | -0.124 | 59 | -0.122 | -0.491 | NO-EDGE | `rr3_vol1.5_rsi50-70` | 83 | -0.002 | 41 | +0.093 | NO-EDGE | 11 | -0.455 | 11 | -0.455 | UNTESTED | 124 | -0.154 | 60 | -0.137 | NO-EDGE | no | 0 |
| 30 | 114 | -0.165 | 58 | -0.172 | -0.534 | NO-EDGE | `rr3_vol2_rsi55-70` | 81 | -0.034 | 37 | -0.243 | NO-EDGE | 25 | +0.080 | 22 | -0.455 | UNTESTED | 123 | -0.177 | 61 | -0.164 | NO-EDGE | no | 0 |
| 31 | 93 | -0.161 | 35 | +0.029 | -0.488 | NO-EDGE | `rr2_vol1.5_rsi55-70` | 88 | -0.114 | 32 | +0.125 | NO-EDGE | 15 | -0.600 | 17 | -0.118 | UNTESTED | 102 | -0.147 | 35 | +0.029 | NO-EDGE | no | 0 |
| 32 | 91 | -0.077 | 26 | -0.279 | -0.769 | UNTESTED | `rr2.5_vol2_rsi50-70` | 77 | +0.000 | 26 | -0.702 | UNTESTED | 5 | -0.400 | 23 | -0.609 | UNTESTED | 96 | -0.031 | 26 | -0.279 | UNTESTED | no | 0 |
| 33 | 113 | -0.090 | 61 | -0.097 | -0.459 | NO-EDGE | `rr2.5_vol1.5_rsi55-70` | 100 | +0.058 | 48 | -0.176 | TRAIN-ONLY | 19 | +0.464 | 43 | +0.071 | UNTESTED | 125 | -0.058 | 63 | -0.078 | NO-EDGE | no | 0 |
| 34 | 100 | -0.096 | 45 | -0.514 | -0.847 | NO-EDGE | `rr2_vol1.5_rsi55-70` | 96 | -0.059 | 41 | -0.540 | NO-EDGE | 12 | -0.250 | 6 | -0.500 | UNTESTED | 111 | -0.105 | 45 | -0.514 | NO-EDGE | no | 0 |
| 35 | 87 | -0.033 | 54 | -0.078 | -0.467 | NO-EDGE | `rr3_vol1.5_rsi55-70` | 63 | +0.082 | 37 | +0.048 | UNTESTED | 19 | -0.043 | 27 | +0.000 | UNTESTED | 89 | -0.054 | 54 | -0.078 | NO-EDGE | no | 0 |
| 36 | 96 | -0.188 | 32 | -0.531 | -0.909 | NO-EDGE | `rr3_vol1.5_rsi55-70` | 63 | +0.079 | 29 | -0.724 | UNTESTED | 11 | +0.636 | 16 | -0.438 | UNTESTED | 101 | -0.198 | 32 | -0.531 | NO-EDGE | no | 0 |
| 37 | 87 | -0.035 | 47 | -0.170 | -0.553 | NO-EDGE | `rr3_vol2_rsi55-70` | 54 | -0.037 | 35 | -0.200 | NO-EDGE | 15 | +0.000 | 29 | -0.069 | UNTESTED | 87 | -0.035 | 47 | -0.170 | NO-EDGE | no | 0 |
| 38 | 104 | -0.072 | 62 | -0.177 | -0.565 | NO-EDGE | `rr2_vol2_rsi55-70` | 78 | +0.045 | 55 | -0.127 | TRAIN-ONLY | 14 | +0.071 | 51 | +0.000 | UNTESTED | 121 | -0.128 | 63 | -0.191 | NO-EDGE | no | 0 |
| 39 | 104 | +0.046 | 30 | -0.400 | -0.914 | TRAIN-ONLY | `rr2_vol2_rsi55-70` | 78 | +0.231 | 28 | -0.143 | UNTESTED | 16 | -0.010 | 27 | -0.222 | UNTESTED | 110 | +0.071 | 30 | -0.400 | TRAIN-ONLY | no | 0 |
| 40 | 95 | -0.230 | 50 | -0.136 | -0.574 | NO-EDGE | `rr2.5_vol1.5_rsi55-70` | 73 | -0.025 | 48 | -0.173 | NO-EDGE | 0 | +0.000 | 12 | +0.121 | UNTESTED | 105 | -0.217 | 50 | -0.136 | NO-EDGE | no | 0 |
| 41 | 78 | +0.010 | 47 | +0.021 | -0.512 | UNTESTED | `rr2_vol2_rsi55-70` | 76 | +0.197 | 47 | -0.106 | TRAIN-ONLY | 22 | -0.420 | 18 | -0.167 | UNTESTED | 90 | +0.042 | 47 | +0.021 | UNTESTED | no | 0 |
| 42 | 111 | +0.000 | 59 | -0.136 | -0.524 | NO-EDGE | `rr3_vol1.5_rsi55-70` | 77 | +0.123 | 33 | -0.273 | TRAIN-ONLY | 10 | +0.200 | 32 | -0.122 | UNTESTED | 113 | +0.035 | 59 | -0.136 | TRAIN-ONLY | no | 0 |
| 43 | 116 | +0.122 | 47 | +0.104 | -0.362 | UNTESTED | `rr2_vol2_rsi55-70` | 91 | +0.298 | 43 | -0.023 | TRAIN-ONLY | 25 | +0.243 | 37 | -0.027 | UNTESTED | 119 | +0.144 | 47 | +0.104 | UNTESTED | no | 0 |
| 44 | 114 | +0.210 | 47 | -0.220 | -0.648 | TRAIN-ONLY | `rr2_vol1.5_rsi50-70` | 114 | +0.210 | 47 | -0.220 | TRAIN-ONLY | 26 | +0.385 | 40 | -0.308 | UNTESTED | 118 | +0.195 | 47 | -0.220 | TRAIN-ONLY | no | 0 |
| 45 | 102 | -0.186 | 50 | -0.040 | -0.514 | NO-EDGE | `rr2.5_vol2_rsi50-70` | 83 | -0.097 | 44 | -0.125 | NO-EDGE | 12 | +0.250 | 26 | +0.154 | UNTESTED | 111 | -0.198 | 50 | -0.040 | NO-EDGE | no | 0 |
| 46 | 112 | +0.018 | 50 | +0.154 | -0.349 | UNTESTED | `rr2_vol2_rsi55-70` | 86 | +0.256 | 40 | +0.068 | UNTESTED | 29 | -0.172 | 39 | -0.136 | UNTESTED | 116 | +0.086 | 50 | +0.154 | UNTESTED | no | 0 |
| 47 | 117 | +0.056 | 67 | -0.060 | -0.445 | TRAIN-ONLY | `rr3_vol1.5_rsi50-70` | 73 | +0.228 | 34 | +0.208 | UNTESTED | 34 | +0.073 | 58 | -0.069 | TRAIN-ONLY | 134 | +0.056 | 67 | -0.060 | TRAIN-ONLY | no | 0 |
| 48 | 91 | -0.321 | 37 | -0.351 | -0.818 | NO-EDGE | `rr2.5_vol1.5_rsi50-70` | 68 | -0.356 | 33 | -0.682 | NO-EDGE | 9 | +0.493 | 8 | -0.625 | UNTESTED | 108 | -0.344 | 37 | -0.351 | NO-EDGE | no | 0 |
| 49 | 84 | -0.279 | 41 | -0.465 | -0.831 | NO-EDGE | `rr2_vol2_rsi55-70` | 62 | -0.217 | 35 | -0.571 | NO-EDGE | 3 | -1.000 | 8 | -0.625 | UNTESTED | 93 | -0.317 | 41 | -0.465 | NO-EDGE | no | 0 |
| 50 | 93 | -0.290 | 54 | -0.146 | -0.534 | NO-EDGE | `rr2.5_vol1.5_rsi50-70` | 81 | -0.265 | 50 | +0.003 | NO-EDGE | 12 | +0.000 | 17 | -0.294 | UNTESTED | 100 | -0.370 | 57 | -0.138 | NO-EDGE | no | 0 |

## Adoption path

The only path to real money (enforced by `adoption.py`; no step can be skipped): BACKTEST -> WALK_FORWARD (ROBUST + drawdown check) -> HUMAN_REVIEW of EVERY trade -> >= 14 days on Binance testnet with zero rule violations -> LIVE. A synthetic result never gets past WALK_FORWARD: `adoption check` blocks every record whose provenance is `synthetic:<world>:<seed>` with `ADOPT_provenance`. A calibration writes no adoption records; it measures how often the labels are right when the truth is known.

## Risk disclaimer

This is a research and testing tool, not financial advice and not a recommendation to trade. Backtests and synthetic worlds are simplified models; past or simulated results do not predict future results. Crypto trading can result in the total loss of the capital used.
