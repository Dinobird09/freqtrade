# Calibration: synthetic world `decay`, seeds 1-50

This report does NOT promise, target or optimise a win rate, and nothing in this package selects on one. A win rate is meaningless without the reward:risk it was earned at: with the mandatory minimum of 2:1 a strategy breaks even at about 33% winners before costs, while a 90% win rate with a 1:9 payoff still loses money. A backtest win rate near 90% is evidence of curve-fitting or look-ahead, not of skill. The target metric is POSITIVE EXPECTANCY (average R per trade, net of fees and slippage) that survives a 70/30 chronological walk-forward with controlled drawdown, with every trade planned at reward:risk >= 2:1. Win rate appears below only as context and is labelled 'context only' wherever it is shown.

Each seed is a full run of the pipeline (6 years of 4H candles, 70/30 walk-forward): baseline, discovery (K = 13 variants, one selected on TRAIN), the ML layer and the expectancy guard, i.e. m = 4 pre-registered TEST looks per seed. **Synthetic results are verification of the methodology, not evidence about real markets.** Each label below combines that candidate's TRAIN and TEST windows (`metrics.label`). ROBUST requires at least 30 trades in each window, TRAIN and TEST avg R > 0, and a one-sided 98.75% lower bound of the TEST mean above zero for BOTH an iid and a calendar-month block bootstrap (the more conservative is used): alpha 0.05 is split over the m = 4 pre-registered candidates (base, discovery-selected, base+ml, base+guard), so when none has an edge the chance that ANY is called ROBUST is at most about 5%; a positive TEST mean that fails only the bound is UNTESTED (positive but not distinguishable from zero after multiplicity correction); the rule was fixed in advance and is never tuned on TEST outcomes.

- Ground truth: the planted effect exists only BEFORE the 70% split; the TEST period is exactly the null world.
- What the ground truth predicts: a positive TRAIN expectancy that does not survive TEST: TRAIN-ONLY (or UNTESTED when the TEST noise is positive but indistinguishable from zero); ROBUST is a false positive.
- **Costs used in every backtest:** fee 0.100% of notional per side (the default: Binance spot taker, no discounts) (`--fee-rate 0.001`), charged on the entry AND on the exit; slippage 0.05% (`--slippage-pct 0.05`) against the trade on market fills (entries and stop exits; take-profit limit exits get none); exchange `binance` (`--exchange-id`; `EXCHANGE:binance` news events block every pair). Starting capital 10,000 per window.
- Cost-aware sizing and targets (CONTRACT.md v2 A1): the planned risk is the ALL-IN loss at the stop (the stop fill after slippage plus both fees), so a clean stop is exactly -1R and the target is placed so that a take-profit nets exactly +2R after fees; the target's price distance is therefore more than 2x the stop distance. Only a gap through the stop loses more than 1R.
- Coinbase Advanced Trade taker fees at low volume tiers are several times Binance's, so a Coinbase run must pass the account's real tier with `--fee-rate` (a fraction of notional per side: 0.006 = 0.6%) and `--exchange-id coinbase`; the default costs would understate them.

## Label frequencies

| walk-forward label (TRAIN + TEST) | baseline | discovery-selected | ML layer | expectancy guard |
|---|---:|---:|---:|---:|
| ROBUST | 0/50 | 0/50 | 0/50 | 0/50 |
| TRAIN-ONLY | 30/50 | 31/50 | 30/50 | 30/50 |
| UNTESTED | 20/50 | 19/50 | 20/50 | 20/50 |
| NO-EDGE | 0/50 | 0/50 | 0/50 | 0/50 |

## Ground truth vs labels (rates with 90% Wilson intervals)

- Verdict ROBUST (any of the 4 pre-registered candidates, the family-wise rate) = FALSE-POSITIVE rate (no edge exists in TEST): 0/50 = 0% (90% Wilson CI 0%-5%).
- baseline labelled ROBUST = FALSE-POSITIVE rate (no edge exists in TEST): 0/50 = 0% (90% Wilson CI 0%-5%).
- discovery-selected labelled ROBUST = FALSE-POSITIVE rate (no edge exists in TEST): 0/50 = 0% (90% Wilson CI 0%-5%).
- ML layer labelled ROBUST = FALSE-POSITIVE rate (no edge exists in TEST): 0/50 = 0% (90% Wilson CI 0%-5%).
- expectancy guard labelled ROBUST = FALSE-POSITIVE rate (no edge exists in TEST): 0/50 = 0% (90% Wilson CI 0%-5%).
- Baseline labelled TRAIN-ONLY: 30/50 = 60% (90% Wilson CI 48%-71%); UNTESTED: 20/50 = 40% (90% Wilson CI 29%-52%).
- Baseline labelled TRAIN-ONLY or UNTESTED (the decay is caught or at least not passed): 50/50 = 100% (90% Wilson CI 95%-100%).
- ML layer (`base+ml`) ran in 50/50 seeds. Mean avg R, base -> layer: TRAIN +0.458 -> +0.506 (in-sample) | TEST -0.095 -> -0.081; its TEST avg R beat the base in 20/50 = 40% (90% Wilson CI 29%-52%) of those seeds.
  Per seed, mean counts TRAIN | TEST: signals vetoed 11.8 | 3.9; entries at reduced risk 0.0 | 0.0; base trades absent from the layer journal 11.3 | 4.3; layer trades absent from the base journal 5.8 | 3.7.
- Expectancy guard (`base+guard`) ran in 50/50 seeds. Mean avg R, base -> layer: TRAIN +0.458 -> +0.459 | TEST -0.095 -> -0.097; its TEST avg R beat the base in 4/50 = 8% (90% Wilson CI 4%-17%) of those seeds.
  Per seed, mean counts TRAIN | TEST: signals vetoed 0.0 | 0.0; entries at reduced risk 3.8 | 1.0; base trades absent from the layer journal 0.2 | 0.0; layer trades absent from the base journal 0.6 | 0.5.
- ML coefficients (standardised, fitted on TRAIN only): the largest-magnitude coefficient was an hour term (hour_sin/hour_cos) in 3/50 fitted seeds; mean hour-term magnitude sqrt(hour_sin^2 + hour_cos^2) = 0.159 log-odds per TRAIN s.d.
- Invariant violations over all backtests of all seeds: 0.

Drawdown (CONTRACT.md v3 C5): every result reports the realised (closed-trade) and the mark-to-market (open positions valued at each 4H close) max drawdown; dd_ok = TEST mark-to-market max drawdown <= min(20%, the 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length, in percent of equity at the risk each trade took).

| candidate | seeds that reached the TEST gate (TRAIN avg R > 0, TRAIN n >= 30, TEST n >= 30) | ROBUST, all seeds | ROBUST given the TEST gate was reached | mean TRAIN avg R | mean TEST avg R | mean TRAIN n | mean TEST n | mean TEST max DD % realised / MTM / limit | dd_ok (TEST MTM within limit) |
|---|---|---|---|---:|---:|---:|---:|---|---|
| baseline | 49/50 = 98% (90% Wilson CI 92%-100%) | 0/50 = 0% (90% Wilson CI 0%-5%) | 0/49 = 0% (90% Wilson CI 0%-5%) | +0.458 | -0.095 | 113.9 | 45.9 | 10.17 / 10.92 / 8.30 | 9/50 = 18% (90% Wilson CI 11%-29%) |
| discovery-selected | 46/50 = 92% (90% Wilson CI 83%-96%) | 0/50 = 0% (90% Wilson CI 0%-5%) | 0/46 = 0% (90% Wilson CI 0%-6%) | +0.717 | -0.081 | 89.7 | 39.6 | 9.79 / 10.47 / 6.62 | 7/50 = 14% (90% Wilson CI 8%-24%) |
| ML layer | 49/50 = 98% (90% Wilson CI 92%-100%) | 0/50 = 0% (90% Wilson CI 0%-5%) | 0/49 = 0% (90% Wilson CI 0%-5%) | +0.506 | -0.081 | 108.4 | 45.3 | 9.75 / 10.50 / 7.93 | 10/50 = 20% (90% Wilson CI 12%-31%) |
| expectancy guard | 49/50 = 98% (90% Wilson CI 92%-100%) | 0/50 = 0% (90% Wilson CI 0%-5%) | 0/49 = 0% (90% Wilson CI 0%-5%) | +0.459 | -0.097 | 114.4 | 46.3 | 10.14 / 10.90 / 8.28 | 9/50 = 18% (90% Wilson CI 11%-29%) |

## Per seed (TRAIN and TEST side by side)

| seed | base TRAIN n | base TRAIN avg R | base TEST n | base TEST avg R | base TEST adj LB | base label | selected | selected TRAIN n | selected TRAIN avg R | selected TEST n | selected TEST avg R | selected label | ML TRAIN n | ML TRAIN avg R (in-sample) | ML TEST n | ML TEST avg R | ML label | guard TRAIN n | guard TRAIN avg R | guard TEST n | guard TEST avg R | guard label | verdict ROBUST | violations |
|---:|---:|---:|---:|---:|---:|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---|---|---:|
| 1 | 114 | +0.307 | 56 | -0.143 | -0.518 | TRAIN-ONLY | `rr3_vol2_rsi50-70` | 70 | +0.742 | 46 | -0.043 | TRAIN-ONLY | 88 | +0.590 | 55 | -0.073 | TRAIN-ONLY | 114 | +0.307 | 56 | -0.143 | TRAIN-ONLY | no | 0 |
| 2 | 116 | +0.371 | 40 | -0.325 | -0.700 | TRAIN-ONLY | `rr2_vol2_rsi55-70` | 90 | +0.567 | 33 | +0.000 | TRAIN-ONLY | 105 | +0.400 | 38 | -0.132 | TRAIN-ONLY | 116 | +0.371 | 40 | -0.325 | TRAIN-ONLY | no | 0 |
| 3 | 111 | +0.360 | 39 | +0.501 | -0.038 | UNTESTED | `rr3_vol2_rsi55-70` | 67 | +0.925 | 29 | +0.426 | UNTESTED | 107 | +0.495 | 42 | +0.322 | UNTESTED | 111 | +0.387 | 39 | +0.501 | UNTESTED | no | 0 |
| 4 | 106 | +0.472 | 51 | -0.176 | -0.588 | TRAIN-ONLY | `rr2_vol1.5_rsi50-70` | 106 | +0.472 | 51 | -0.176 | TRAIN-ONLY | 106 | +0.472 | 51 | -0.176 | TRAIN-ONLY | 106 | +0.472 | 51 | -0.176 | TRAIN-ONLY | no | 0 |
| 5 | 124 | +0.403 | 46 | +0.081 | -0.403 | UNTESTED | `rr2_vol2_rsi50-70` | 97 | +0.825 | 50 | -0.093 | TRAIN-ONLY | 111 | +0.540 | 44 | +0.062 | UNTESTED | 124 | +0.403 | 46 | +0.081 | UNTESTED | no | 0 |
| 6 | 118 | +0.424 | 39 | -0.615 | -1.000 | TRAIN-ONLY | `rr2_vol2_rsi50-70` | 90 | +0.600 | 27 | -0.667 | UNTESTED | 115 | +0.461 | 36 | -0.583 | TRAIN-ONLY | 118 | +0.424 | 39 | -0.615 | TRAIN-ONLY | no | 0 |
| 7 | 115 | +0.556 | 40 | -0.308 | -0.757 | TRAIN-ONLY | `rr2_vol2_rsi55-70` | 90 | +0.821 | 34 | -0.270 | TRAIN-ONLY | 114 | +0.569 | 40 | -0.308 | TRAIN-ONLY | 115 | +0.556 | 40 | -0.308 | TRAIN-ONLY | no | 0 |
| 8 | 110 | +0.473 | 35 | +0.029 | -0.486 | UNTESTED | `rr2_vol1.5_rsi50-70` | 110 | +0.473 | 35 | +0.029 | UNTESTED | 105 | +0.571 | 35 | +0.029 | UNTESTED | 110 | +0.473 | 35 | +0.029 | UNTESTED | no | 0 |
| 9 | 121 | +0.465 | 52 | -0.077 | -0.547 | TRAIN-ONLY | `rr3_vol1.5_rsi55-70` | 89 | +0.845 | 37 | +0.189 | UNTESTED | 118 | +0.451 | 51 | -0.059 | TRAIN-ONLY | 121 | +0.465 | 52 | -0.077 | TRAIN-ONLY | no | 0 |
| 10 | 101 | +0.426 | 39 | +0.022 | -0.462 | UNTESTED | `rr2_vol2_rsi50-70` | 82 | +0.573 | 34 | -0.004 | TRAIN-ONLY | 96 | +0.469 | 39 | +0.022 | UNTESTED | 101 | +0.426 | 39 | +0.022 | UNTESTED | no | 0 |
| 11 | 113 | +0.540 | 56 | +0.010 | -0.411 | UNTESTED | `rr2.5_vol2_rsi55-70` | 72 | +1.139 | 45 | -0.144 | TRAIN-ONLY | 110 | +0.582 | 56 | +0.010 | UNTESTED | 113 | +0.540 | 58 | +0.027 | UNTESTED | no | 0 |
| 12 | 145 | +0.390 | 47 | +0.191 | -0.408 | UNTESTED | `rr2.5_vol2_rsi50-70` | 102 | +0.716 | 41 | +0.328 | UNTESTED | 143 | +0.410 | 45 | +0.243 | UNTESTED | 145 | +0.390 | 47 | +0.191 | UNTESTED | no | 0 |
| 13 | 100 | +0.530 | 48 | +0.188 | -0.250 | UNTESTED | `rr2_vol2_rsi55-70` | 77 | +0.753 | 38 | +0.026 | UNTESTED | 98 | +0.561 | 47 | +0.213 | UNTESTED | 100 | +0.530 | 48 | +0.188 | UNTESTED | no | 0 |
| 14 | 107 | +0.458 | 54 | -0.111 | -0.529 | TRAIN-ONLY | `rr2_vol2_rsi55-70` | 83 | +0.626 | 46 | -0.022 | TRAIN-ONLY | 104 | +0.500 | 50 | -0.040 | TRAIN-ONLY | 107 | +0.458 | 54 | -0.111 | TRAIN-ONLY | no | 0 |
| 15 | 128 | +0.430 | 36 | +0.277 | -0.250 | UNTESTED | `rr2_vol2_rsi55-70` | 98 | +0.653 | 28 | +0.322 | UNTESTED | 124 | +0.476 | 36 | +0.277 | UNTESTED | 128 | +0.430 | 36 | +0.277 | UNTESTED | no | 0 |
| 16 | 97 | +0.392 | 37 | +0.071 | -0.468 | UNTESTED | `rr2.5_vol1.5_rsi55-70` | 78 | +0.615 | 38 | -0.063 | TRAIN-ONLY | 97 | +0.423 | 38 | +0.121 | UNTESTED | 97 | +0.392 | 37 | +0.071 | UNTESTED | no | 0 |
| 17 | 111 | +0.335 | 35 | -0.314 | -0.800 | TRAIN-ONLY | `rr2_vol2_rsi50-70` | 87 | +0.565 | 37 | -0.189 | TRAIN-ONLY | 111 | +0.335 | 35 | -0.314 | TRAIN-ONLY | 111 | +0.335 | 35 | -0.314 | TRAIN-ONLY | no | 0 |
| 18 | 118 | +0.441 | 33 | +0.091 | -0.455 | UNTESTED | `rr2_vol2_rsi50-70` | 87 | +0.713 | 38 | +0.263 | UNTESTED | 112 | +0.518 | 39 | +0.231 | UNTESTED | 118 | +0.441 | 33 | +0.091 | UNTESTED | no | 0 |
| 19 | 112 | +0.473 | 45 | -0.175 | -0.734 | TRAIN-ONLY | `rr3_vol1.5_rsi55-70` | 90 | +0.894 | 37 | -0.213 | TRAIN-ONLY | 104 | +0.615 | 40 | -0.147 | TRAIN-ONLY | 112 | +0.473 | 45 | -0.175 | TRAIN-ONLY | no | 0 |
| 20 | 111 | +0.243 | 43 | +0.116 | -0.372 | UNTESTED | `rr2_vol2_rsi55-70` | 82 | +0.354 | 35 | +0.200 | UNTESTED | 104 | +0.211 | 43 | +0.116 | UNTESTED | 114 | +0.237 | 43 | +0.116 | UNTESTED | no | 0 |
| 21 | 100 | +0.610 | 45 | -0.032 | -0.467 | TRAIN-ONLY | `rr2_vol2_rsi55-70` | 79 | +0.811 | 42 | -0.130 | TRAIN-ONLY | 100 | +0.610 | 45 | -0.032 | TRAIN-ONLY | 100 | +0.610 | 45 | -0.032 | TRAIN-ONLY | no | 0 |
| 22 | 116 | +0.267 | 48 | -0.011 | -0.448 | TRAIN-ONLY | `rr2_vol2_rsi55-70` | 93 | +0.419 | 38 | +0.092 | UNTESTED | 112 | +0.286 | 48 | -0.011 | TRAIN-ONLY | 123 | +0.268 | 48 | -0.011 | TRAIN-ONLY | no | 0 |
| 23 | 125 | +0.418 | 49 | +0.317 | -0.143 | UNTESTED | `rr2.5_vol2_rsi50-70` | 88 | +0.869 | 52 | -0.096 | TRAIN-ONLY | 106 | +0.531 | 51 | +0.265 | UNTESTED | 125 | +0.418 | 49 | +0.317 | UNTESTED | no | 0 |
| 24 | 105 | +0.504 | 42 | -0.164 | -0.659 | TRAIN-ONLY | `rr2_vol2_rsi50-70` | 96 | +0.570 | 39 | -0.176 | TRAIN-ONLY | 104 | +0.547 | 41 | -0.143 | TRAIN-ONLY | 105 | +0.504 | 42 | -0.164 | TRAIN-ONLY | no | 0 |
| 25 | 119 | +0.597 | 41 | +0.306 | -0.206 | UNTESTED | `rr2_vol2_rsi50-70` | 100 | +0.781 | 38 | +0.331 | UNTESTED | 113 | +0.655 | 43 | +0.385 | UNTESTED | 119 | +0.597 | 41 | +0.306 | UNTESTED | no | 0 |
| 26 | 100 | +0.200 | 45 | -0.122 | -0.533 | TRAIN-ONLY | `rr2.5_vol2_rsi50-70` | 73 | +0.534 | 34 | -0.073 | TRAIN-ONLY | 76 | +0.303 | 43 | -0.012 | TRAIN-ONLY | 100 | +0.200 | 49 | -0.194 | TRAIN-ONLY | no | 0 |
| 27 | 117 | +0.564 | 40 | -0.217 | -0.635 | TRAIN-ONLY | `rr2_vol2_rsi50-70` | 98 | +0.684 | 39 | -0.120 | TRAIN-ONLY | 114 | +0.605 | 40 | -0.217 | TRAIN-ONLY | 117 | +0.564 | 40 | -0.217 | TRAIN-ONLY | no | 0 |
| 28 | 111 | +0.444 | 52 | -0.365 | -0.739 | TRAIN-ONLY | `rr2_vol2_rsi50-70` | 84 | +0.621 | 44 | -0.318 | TRAIN-ONLY | 95 | +0.718 | 50 | -0.460 | TRAIN-ONLY | 111 | +0.444 | 52 | -0.365 | TRAIN-ONLY | no | 0 |
| 29 | 125 | +0.474 | 59 | -0.122 | -0.491 | TRAIN-ONLY | `rr2_vol2_rsi55-70` | 94 | +0.769 | 41 | -0.268 | TRAIN-ONLY | 119 | +0.523 | 57 | -0.091 | TRAIN-ONLY | 125 | +0.474 | 60 | -0.137 | TRAIN-ONLY | no | 0 |
| 30 | 126 | +0.429 | 60 | -0.200 | -0.553 | TRAIN-ONLY | `rr2_vol2_rsi50-70` | 99 | +0.667 | 53 | -0.377 | TRAIN-ONLY | 126 | +0.452 | 60 | -0.200 | TRAIN-ONLY | 126 | +0.429 | 63 | -0.191 | TRAIN-ONLY | no | 0 |
| 31 | 122 | +0.576 | 38 | +0.026 | -0.455 | UNTESTED | `rr2.5_vol2_rsi50-70` | 96 | +0.823 | 33 | +0.061 | UNTESTED | 116 | +0.606 | 37 | +0.054 | UNTESTED | 122 | +0.576 | 38 | +0.026 | UNTESTED | no | 0 |
| 32 | 107 | +0.402 | 25 | -0.370 | -0.851 | UNTESTED | `rr2.5_vol2_rsi50-70` | 81 | +0.858 | 26 | -0.702 | UNTESTED | 107 | +0.458 | 25 | -0.370 | UNTESTED | 107 | +0.402 | 25 | -0.370 | UNTESTED | no | 0 |
| 33 | 108 | +0.402 | 63 | -0.126 | -0.507 | TRAIN-ONLY | `rr2_vol2_rsi55-70` | 86 | +0.588 | 47 | +0.044 | UNTESTED | 108 | +0.402 | 63 | -0.126 | TRAIN-ONLY | 113 | +0.446 | 69 | -0.101 | TRAIN-ONLY | no | 0 |
| 34 | 109 | +0.461 | 44 | -0.571 | -0.864 | TRAIN-ONLY | `rr2_vol2_rsi50-70` | 89 | +0.587 | 32 | -0.250 | TRAIN-ONLY | 109 | +0.461 | 44 | -0.571 | TRAIN-ONLY | 109 | +0.461 | 44 | -0.571 | TRAIN-ONLY | no | 0 |
| 35 | 104 | +0.702 | 47 | +0.060 | -0.387 | UNTESTED | `rr2_vol2_rsi55-70` | 77 | +1.026 | 48 | +0.017 | UNTESTED | 104 | +0.702 | 47 | +0.060 | UNTESTED | 104 | +0.702 | 47 | +0.060 | UNTESTED | no | 0 |
| 36 | 111 | +0.513 | 33 | -0.545 | -0.914 | TRAIN-ONLY | `rr2_vol2_rsi55-70` | 83 | +0.735 | 30 | -0.500 | TRAIN-ONLY | 98 | +0.622 | 33 | -0.545 | TRAIN-ONLY | 111 | +0.513 | 33 | -0.545 | TRAIN-ONLY | no | 0 |
| 37 | 119 | +0.462 | 47 | -0.170 | -0.553 | TRAIN-ONLY | `rr2.5_vol2_rsi50-70` | 84 | +0.917 | 41 | -0.146 | TRAIN-ONLY | 108 | +0.556 | 49 | -0.204 | TRAIN-ONLY | 120 | +0.450 | 47 | -0.170 | TRAIN-ONLY | no | 0 |
| 38 | 118 | +0.703 | 62 | -0.177 | -0.565 | TRAIN-ONLY | `rr2_vol2_rsi50-70` | 92 | +0.924 | 58 | -0.017 | TRAIN-ONLY | 116 | +0.707 | 62 | -0.177 | TRAIN-ONLY | 118 | +0.703 | 63 | -0.191 | TRAIN-ONLY | no | 0 |
| 39 | 108 | +0.533 | 31 | -0.419 | -0.889 | TRAIN-ONLY | `rr2_vol2_rsi50-70` | 87 | +0.799 | 31 | +0.065 | UNTESTED | 108 | +0.533 | 31 | -0.419 | TRAIN-ONLY | 108 | +0.533 | 31 | -0.419 | TRAIN-ONLY | no | 0 |
| 40 | 117 | +0.308 | 50 | -0.136 | -0.574 | TRAIN-ONLY | `rr2.5_vol2_rsi50-70` | 83 | +0.538 | 41 | -0.061 | TRAIN-ONLY | 102 | +0.235 | 51 | -0.095 | TRAIN-ONLY | 120 | +0.300 | 50 | -0.136 | TRAIN-ONLY | no | 0 |
| 41 | 118 | +0.500 | 46 | +0.043 | -0.500 | UNTESTED | `rr2_vol2_rsi50-70` | 96 | +0.656 | 47 | -0.043 | TRAIN-ONLY | 116 | +0.526 | 46 | +0.043 | UNTESTED | 118 | +0.500 | 46 | +0.043 | UNTESTED | no | 0 |
| 42 | 132 | +0.549 | 59 | -0.136 | -0.524 | TRAIN-ONLY | `rr2_vol2_rsi50-70` | 108 | +0.728 | 51 | -0.294 | TRAIN-ONLY | 130 | +0.573 | 59 | -0.136 | TRAIN-ONLY | 132 | +0.549 | 59 | -0.136 | TRAIN-ONLY | no | 0 |
| 43 | 138 | +0.573 | 48 | +0.018 | -0.429 | UNTESTED | `rr2.5_vol1.5_rsi50-70` | 128 | +0.840 | 41 | +0.046 | UNTESTED | 138 | +0.573 | 48 | +0.018 | UNTESTED | 138 | +0.573 | 48 | +0.018 | UNTESTED | no | 0 |
| 44 | 115 | +0.617 | 48 | -0.174 | -0.597 | TRAIN-ONLY | `rr2.5_vol2_rsi50-70` | 96 | +0.985 | 31 | -0.069 | TRAIN-ONLY | 111 | +0.622 | 47 | -0.220 | TRAIN-ONLY | 115 | +0.617 | 48 | -0.174 | TRAIN-ONLY | no | 0 |
| 45 | 102 | +0.412 | 51 | -0.059 | -0.529 | TRAIN-ONLY | `rr2_vol2_rsi50-70` | 90 | +0.567 | 45 | -0.067 | TRAIN-ONLY | 96 | +0.500 | 50 | -0.040 | TRAIN-ONLY | 103 | +0.398 | 54 | -0.111 | TRAIN-ONLY | no | 0 |
| 46 | 118 | +0.551 | 49 | +0.178 | -0.339 | UNTESTED | `rr2.5_vol1.5_rsi55-70` | 106 | +0.717 | 43 | +0.156 | UNTESTED | 115 | +0.565 | 49 | +0.178 | UNTESTED | 118 | +0.551 | 49 | +0.178 | UNTESTED | no | 0 |
| 47 | 127 | +0.498 | 65 | +0.015 | -0.373 | UNTESTED | `rr2_vol2_rsi55-70` | 98 | +0.819 | 39 | +0.231 | UNTESTED | 120 | +0.510 | 64 | +0.031 | UNTESTED | 130 | +0.510 | 65 | +0.015 | UNTESTED | no | 0 |
| 48 | 110 | +0.267 | 42 | -0.286 | -0.727 | TRAIN-ONLY | `rr2_vol2_rsi50-70` | 93 | +0.516 | 33 | -0.364 | TRAIN-ONLY | 104 | +0.253 | 41 | -0.268 | TRAIN-ONLY | 110 | +0.267 | 42 | -0.286 | TRAIN-ONLY | no | 0 |
| 49 | 99 | +0.303 | 41 | -0.465 | -0.831 | TRAIN-ONLY | `rr3_vol2_rsi50-70` | 69 | +1.029 | 32 | -0.750 | TRAIN-ONLY | 87 | +0.483 | 34 | -0.471 | TRAIN-ONLY | 99 | +0.303 | 41 | -0.465 | TRAIN-ONLY | no | 0 |
| 50 | 93 | +0.581 | 53 | -0.186 | -0.582 | TRAIN-ONLY | `rr2_vol1.5_rsi50-70` | 93 | +0.581 | 53 | -0.186 | TRAIN-ONLY | 89 | +0.551 | 45 | -0.108 | TRAIN-ONLY | 93 | +0.581 | 56 | -0.176 | TRAIN-ONLY | no | 0 |

## Adoption path

The only path to real money (enforced by `adoption.py`; no step can be skipped): BACKTEST -> WALK_FORWARD (ROBUST + drawdown check) -> HUMAN_REVIEW of EVERY trade -> >= 14 days on Binance testnet with zero rule violations -> LIVE. A synthetic result never gets past WALK_FORWARD: `adoption check` blocks every record whose provenance is `synthetic:<world>:<seed>` with `ADOPT_provenance`. A calibration writes no adoption records; it measures how often the labels are right when the truth is known.

## Risk disclaimer

This is a research and testing tool, not financial advice and not a recommendation to trade. Backtests and synthetic worlds are simplified models; past or simulated results do not predict future results. Crypto trading can result in the total loss of the capital used.
