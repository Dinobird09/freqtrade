# Calibration: synthetic world `null`, seeds 1-20

This report does NOT promise, target or optimise a win rate, and nothing in this package selects on one. A win rate is meaningless without the reward:risk it was earned at: with the mandatory minimum of 2:1 a strategy breaks even at about 33% winners before costs, while a 90% win rate with a 1:9 payoff still loses money. A backtest win rate near 90% is evidence of curve-fitting or look-ahead, not of skill. The target metric is POSITIVE EXPECTANCY (average R per trade, net of fees and slippage) that survives a 70/30 chronological walk-forward with controlled drawdown, with every trade planned at reward:risk >= 2:1. Win rate appears below only as context and is labelled 'context only' wherever it is shown.

Each seed is a full run of the pipeline (6 years of 4H candles, 70/30 walk-forward): baseline, discovery (K = 13 variants, one selected on TRAIN), the ML layer and the expectancy guard, i.e. m = 4 pre-registered TEST looks per seed. **Synthetic results are verification of the methodology, not evidence about real markets.** Each label below combines that candidate's TRAIN and TEST windows (`metrics.label`). ROBUST requires at least 30 trades in each window, TRAIN and TEST avg R > 0, and a one-sided 98.75% lower bound of the TEST mean above zero for BOTH an iid and a calendar-month block bootstrap (the more conservative is used): alpha 0.05 is split over the m = 4 pre-registered candidates (base, discovery-selected, base+ml, base+guard), so when none has an edge the chance that ANY is called ROBUST is at most about 5%; a positive TEST mean that fails only the bound is UNTESTED (positive but not distinguishable from zero after multiplicity correction); the rule was fixed in advance and is never tuned on TEST outcomes.

- Ground truth: martingale prices (zero drift): no entry/exit rule has an edge before costs, so every strategy has negative expectancy after fees and slippage.
- What the ground truth predicts: nothing should be ROBUST; the expected labels are NO-EDGE, TRAIN-ONLY or UNTESTED, and a ROBUST label here is a false positive.
- **Costs used in every backtest:** fee 0.100% of notional per side (the default: Binance spot taker, no discounts) (`--fee-rate 0.001`), charged on the entry AND on the exit; slippage 0.05% (`--slippage-pct 0.05`) against the trade on market fills (entries and stop exits; take-profit limit exits get none); exchange `binance` (`--exchange-id`; `EXCHANGE:binance` news events block every pair). Starting capital 10,000 per window.
- Cost-aware sizing and targets (CONTRACT.md v2 A1): the planned risk is the ALL-IN loss at the stop (the stop fill after slippage plus both fees), so a clean stop is exactly -1R and the target is placed so that a take-profit nets exactly +2R after fees; the target's price distance is therefore more than 2x the stop distance. Only a gap through the stop loses more than 1R.
- Coinbase Advanced Trade taker fees at low volume tiers are several times Binance's, so a Coinbase run must pass the account's real tier with `--fee-rate` (a fraction of notional per side: 0.006 = 0.6%) and `--exchange-id coinbase`; the default costs would understate them.

## Label frequencies

| walk-forward label (TRAIN + TEST) | baseline | discovery-selected | ML layer | expectancy guard |
|---|---:|---:|---:|---:|
| ROBUST | 0/20 | 0/20 | 0/20 | 0/20 |
| TRAIN-ONLY | 0/20 | 4/20 | 1/20 | 0/20 |
| UNTESTED | 3/20 | 8/20 | 16/20 | 3/20 |
| NO-EDGE | 17/20 | 8/20 | 3/20 | 17/20 |

## Ground truth vs labels (rates with 90% Wilson intervals)

- Verdict ROBUST (any of the 4 pre-registered candidates, the family-wise rate) = FALSE-POSITIVE rate (no edge exists): 0/20 = 0% (90% Wilson CI 0%-12%).
- baseline labelled ROBUST = FALSE-POSITIVE rate (no edge exists): 0/20 = 0% (90% Wilson CI 0%-12%).
- discovery-selected labelled ROBUST = FALSE-POSITIVE rate (no edge exists): 0/20 = 0% (90% Wilson CI 0%-12%).
- ML layer labelled ROBUST = FALSE-POSITIVE rate (no edge exists): 0/20 = 0% (90% Wilson CI 0%-12%).
- expectancy guard labelled ROBUST = FALSE-POSITIVE rate (no edge exists): 0/20 = 0% (90% Wilson CI 0%-12%).
- ML layer (`base+ml`) ran in 20/20 seeds. Mean avg R, base -> layer: TRAIN -0.107 -> -0.014 (in-sample) | TEST -0.021 -> -0.207; its TEST avg R beat the base in 5/20 = 25% (90% Wilson CI 13%-43%) of those seeds.
  Per seed, mean counts TRAIN | TEST: signals vetoed 195.8 | 90.5; entries at reduced risk 0.0 | 0.0; base trades absent from the layer journal 72.2 | 31.4; layer trades absent from the base journal 26.4 | 11.0.
- Expectancy guard (`base+guard`) ran in 20/20 seeds. Mean avg R, base -> layer: TRAIN -0.107 -> -0.097 | TEST -0.021 -> -0.020; its TEST avg R beat the base in 1/20 = 5% (90% Wilson CI 1%-20%) of those seeds.
  Per seed, mean counts TRAIN | TEST: signals vetoed 0.0 | 0.0; entries at reduced risk 29.8 | 0.5; base trades absent from the layer journal 3.0 | 0.0; layer trades absent from the base journal 10.2 | 0.1.
- ML coefficients (standardised, fitted on TRAIN only): the largest-magnitude coefficient was an hour term (hour_sin/hour_cos) in 5/20 fitted seeds; mean hour-term magnitude sqrt(hour_sin^2 + hour_cos^2) = 0.149 log-odds per TRAIN s.d.
- Invariant violations over all backtests of all seeds: 0.

Drawdown (CONTRACT.md v3 C5): every result reports the realised (closed-trade) and the mark-to-market (open positions valued at each 4H close) max drawdown; dd_ok = TEST mark-to-market max drawdown <= min(20%, the 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length, in percent of equity at the risk each trade took).

| candidate | seeds that reached the TEST gate (TRAIN avg R > 0, TRAIN n >= 30, TEST n >= 30) | ROBUST, all seeds | ROBUST given the TEST gate was reached | mean TRAIN avg R | mean TEST avg R | mean TRAIN n | mean TEST n | mean TEST max DD % realised / MTM / limit | dd_ok (TEST MTM within limit) |
|---|---|---|---|---:|---:|---:|---:|---|---|
| baseline | 3/20 = 15% (90% Wilson CI 6%-32%) | 0/20 = 0% (90% Wilson CI 0%-12%) | 0/3 = 0% (90% Wilson CI 0%-47%) | -0.107 | -0.021 | 101.2 | 43.9 | 9.01 / 9.84 / 16.51 | 19/20 = 95% (90% Wilson CI 80%-99%) |
| discovery-selected | 8/20 = 40% (90% Wilson CI 24%-58%) | 0/20 = 0% (90% Wilson CI 0%-12%) | 0/8 = 0% (90% Wilson CI 0%-25%) | +0.022 | -0.004 | 78.5 | 35.5 | 8.48 / 9.16 / 14.29 | 19/20 = 95% (90% Wilson CI 80%-99%) |
| ML layer | 3/20 = 15% (90% Wilson CI 6%-32%) | 0/20 = 0% (90% Wilson CI 0%-12%) | 0/3 = 0% (90% Wilson CI 0%-47%) | -0.014 | -0.207 | 55.3 | 23.4 | 6.33 / 7.00 / 10.74 | 16/20 = 80% (90% Wilson CI 62%-91%) |
| expectancy guard | 3/20 = 15% (90% Wilson CI 6%-32%) | 0/20 = 0% (90% Wilson CI 0%-12%) | 0/3 = 0% (90% Wilson CI 0%-47%) | -0.097 | -0.020 | 108.4 | 44.0 | 8.99 / 9.82 / 14.86 | 18/20 = 90% (90% Wilson CI 74%-97%) |

## Per seed (TRAIN and TEST side by side)

| seed | base TRAIN n | base TRAIN avg R | base TEST n | base TEST avg R | base TEST adj LB | base label | selected | selected TRAIN n | selected TRAIN avg R | selected TEST n | selected TEST avg R | selected label | ML TRAIN n | ML TRAIN avg R (in-sample) | ML TEST n | ML TEST avg R | ML label | guard TRAIN n | guard TRAIN avg R | guard TEST n | guard TEST avg R | guard label | verdict ROBUST | violations |
|---:|---:|---:|---:|---:|---:|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---|---|---:|
| 1 | 98 | -0.138 | 58 | -0.121 | -0.491 | NO-EDGE | `rr2.5_vol2_rsi50-70` | 81 | -0.086 | 49 | -0.143 | NO-EDGE | 45 | +0.267 | 17 | -0.294 | UNTESTED | 108 | -0.183 | 58 | -0.121 | NO-EDGE | no | 0 |
| 2 | 91 | -0.426 | 39 | -0.231 | -0.692 | NO-EDGE | `rr2.5_vol2_rsi55-70` | 63 | -0.333 | 30 | +0.167 | NO-EDGE | 13 | -0.077 | 10 | -0.700 | UNTESTED | 99 | -0.382 | 39 | -0.231 | NO-EDGE | no | 0 |
| 3 | 89 | -0.124 | 36 | +0.542 | -0.028 | NO-EDGE | `rr3_vol2_rsi50-70` | 60 | +0.133 | 27 | +0.532 | UNTESTED | 14 | +0.071 | 2 | -1.000 | UNTESTED | 95 | -0.084 | 36 | +0.542 | NO-EDGE | no | 0 |
| 4 | 97 | -0.041 | 51 | -0.176 | -0.588 | NO-EDGE | `rr2_vol2_rsi50-70` | 83 | +0.084 | 33 | -0.455 | TRAIN-ONLY | 64 | +0.078 | 33 | -0.364 | TRAIN-ONLY | 105 | +0.000 | 51 | -0.176 | NO-EDGE | no | 0 |
| 5 | 105 | +0.029 | 46 | +0.081 | -0.403 | UNTESTED | `rr3_vol1.5_rsi55-70` | 82 | +0.268 | 36 | +0.271 | UNTESTED | 88 | -0.080 | 40 | -0.175 | NO-EDGE | 110 | +0.064 | 46 | +0.081 | UNTESTED | no | 0 |
| 6 | 113 | -0.018 | 38 | -0.605 | -1.000 | NO-EDGE | `rr2.5_vol2_rsi50-70` | 80 | +0.006 | 25 | -0.580 | UNTESTED | 77 | +0.013 | 29 | -0.793 | UNTESTED | 118 | -0.034 | 38 | -0.605 | NO-EDGE | no | 0 |
| 7 | 87 | -0.112 | 40 | -0.308 | -0.757 | NO-EDGE | `rr2_vol1.5_rsi50-70` | 87 | -0.112 | 40 | -0.308 | NO-EDGE | 32 | -0.062 | 11 | -0.727 | UNTESTED | 90 | -0.108 | 40 | -0.308 | NO-EDGE | no | 0 |
| 8 | 110 | +0.145 | 35 | +0.029 | -0.486 | UNTESTED | `rr2.5_vol2_rsi50-70` | 85 | +0.318 | 23 | -0.087 | UNTESTED | 89 | +0.247 | 31 | +0.065 | UNTESTED | 119 | +0.109 | 35 | +0.029 | UNTESTED | no | 0 |
| 9 | 113 | -0.071 | 51 | -0.059 | -0.533 | NO-EDGE | `rr3_vol2_rsi55-70` | 69 | +0.101 | 34 | +0.176 | UNTESTED | 73 | -0.096 | 28 | -0.036 | UNTESTED | 114 | -0.026 | 51 | -0.059 | NO-EDGE | no | 0 |
| 10 | 99 | -0.030 | 37 | +0.077 | -0.433 | NO-EDGE | `rr3_vol1.5_rsi55-70` | 72 | +0.105 | 32 | +0.000 | TRAIN-ONLY | 42 | +0.071 | 12 | +0.321 | UNTESTED | 103 | -0.039 | 37 | +0.077 | NO-EDGE | no | 0 |
| 11 | 91 | -0.077 | 56 | +0.010 | -0.411 | NO-EDGE | `rr2_vol1.5_rsi55-70` | 85 | -0.047 | 54 | -0.119 | NO-EDGE | 46 | -0.087 | 26 | -0.192 | UNTESTED | 105 | -0.029 | 58 | +0.027 | NO-EDGE | no | 0 |
| 12 | 128 | -0.039 | 54 | +0.203 | -0.311 | NO-EDGE | `rr2_vol2_rsi50-70` | 105 | +0.086 | 55 | +0.290 | UNTESTED | 82 | +0.061 | 28 | +0.179 | UNTESTED | 135 | -0.022 | 54 | +0.203 | NO-EDGE | no | 0 |
| 13 | 98 | -0.235 | 51 | +0.176 | -0.294 | NO-EDGE | `rr2.5_vol1.5_rsi50-70` | 89 | -0.135 | 40 | +0.138 | NO-EDGE | 42 | -0.214 | 23 | +0.174 | UNTESTED | 106 | -0.236 | 51 | +0.176 | NO-EDGE | no | 0 |
| 14 | 91 | -0.011 | 54 | -0.111 | -0.529 | NO-EDGE | `rr3_vol2_rsi50-70` | 66 | +0.051 | 43 | +0.023 | UNTESTED | 60 | -0.200 | 33 | +0.000 | NO-EDGE | 96 | +0.000 | 54 | -0.111 | NO-EDGE | no | 0 |
| 15 | 102 | +0.029 | 38 | +0.210 | -0.317 | UNTESTED | `rr2_vol1.5_rsi55-70` | 88 | +0.091 | 37 | -0.001 | TRAIN-ONLY | 76 | +0.026 | 30 | +0.033 | UNTESTED | 112 | +0.045 | 38 | +0.210 | UNTESTED | no | 0 |
| 16 | 98 | -0.204 | 37 | +0.071 | -0.468 | NO-EDGE | `rr2_vol2_rsi55-70` | 85 | -0.047 | 33 | -0.163 | NO-EDGE | 51 | +0.059 | 23 | -0.060 | UNTESTED | 104 | -0.192 | 37 | +0.071 | NO-EDGE | no | 0 |
| 17 | 113 | -0.253 | 35 | -0.314 | -0.800 | NO-EDGE | `rr2.5_vol2_rsi55-70` | 76 | -0.065 | 30 | -0.067 | NO-EDGE | 58 | -0.051 | 22 | -0.318 | UNTESTED | 123 | -0.260 | 35 | -0.314 | NO-EDGE | no | 0 |
| 18 | 103 | -0.113 | 33 | +0.182 | -0.364 | NO-EDGE | `rr2.5_vol2_rsi55-70` | 80 | +0.054 | 28 | +0.375 | UNTESTED | 60 | -0.176 | 24 | +0.046 | UNTESTED | 105 | -0.129 | 33 | +0.182 | NO-EDGE | no | 0 |
| 19 | 103 | -0.164 | 46 | -0.193 | -0.763 | NO-EDGE | `rr3_vol2_rsi55-70` | 62 | +0.107 | 30 | -0.333 | TRAIN-ONLY | 62 | -0.059 | 33 | -0.148 | NO-EDGE | 112 | -0.151 | 46 | -0.193 | NO-EDGE | no | 0 |
| 20 | 94 | -0.298 | 43 | +0.116 | -0.372 | NO-EDGE | `rr2.5_vol2_rsi55-70` | 73 | -0.137 | 32 | +0.203 | NO-EDGE | 32 | -0.062 | 14 | -0.143 | UNTESTED | 109 | -0.284 | 43 | +0.116 | NO-EDGE | no | 0 |

## Adoption path

The only path to real money (enforced by `adoption.py`; no step can be skipped): BACKTEST -> WALK_FORWARD (ROBUST + drawdown check) -> HUMAN_REVIEW of EVERY trade -> >= 14 days on Binance testnet with zero rule violations -> LIVE. A synthetic result never gets past WALK_FORWARD: `adoption check` blocks every record whose provenance is `synthetic:<world>:<seed>` with `ADOPT_provenance`. A calibration writes no adoption records; it measures how often the labels are right when the truth is known.

## Risk disclaimer

This is a research and testing tool, not financial advice and not a recommendation to trade. Backtests and synthetic worlds are simplified models; past or simulated results do not predict future results. Crypto trading can result in the total loss of the capital used.
