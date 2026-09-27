# Calibration: synthetic world `null`, seeds 1-20

This report does NOT promise, target or optimise a win rate, and nothing in this package selects on one. A win rate is meaningless without the reward:risk it was earned at: with the mandatory minimum of 2:1 a strategy breaks even at about 33% winners before costs, while a 90% win rate with a 1:9 payoff still loses money. A backtest win rate near 90% is evidence of curve-fitting or look-ahead, not of skill. The target metric is POSITIVE EXPECTANCY (average R per trade, net of fees and slippage) that survives a 70/30 chronological walk-forward with controlled drawdown, with every trade planned at reward:risk >= 2:1. Win rate appears below only as context and is labelled 'context only' wherever it is shown.

Each seed is a full run of the pipeline (6 years of 4H candles, 70/30 walk-forward): baseline, discovery (K = 13 variants, selected on TRAIN) and the ML layer. **Synthetic results are verification of the methodology, not evidence about real markets.** Each label below combines that candidate's TRAIN and TEST windows (`metrics.label`); the verdict takes up to three looks at TEST per seed.

- Ground truth: martingale prices (zero drift): no entry/exit rule has an edge before costs, so every strategy has negative expectancy after fees and slippage.
- What the ground truth predicts: nothing should be ROBUST; the expected labels are NO-EDGE, TRAIN-ONLY or UNTESTED, and a ROBUST label here is a false positive.
- **Costs used in every backtest:** fee 0.100% of notional per side (the default: Binance spot taker, no discounts) (`--fee-rate 0.001`), charged on the entry AND on the exit; slippage 0.05% (`--slippage-pct 0.05`) against the trade on market fills (entries and stop exits; take-profit limit exits get none); exchange `binance` (`--exchange-id`; `EXCHANGE:binance` news events block every pair). Starting capital 10,000 per window.
- Cost-aware sizing and targets (CONTRACT.md v2 A1): the planned risk is the ALL-IN loss at the stop (the stop fill after slippage plus both fees), so a clean stop is exactly -1R and the target is placed so that a take-profit nets exactly +2R after fees; the target's price distance is therefore more than 2x the stop distance. Only a gap through the stop loses more than 1R.
- Coinbase Advanced Trade taker fees at low volume tiers are several times Binance's, so a Coinbase run must pass the account's real tier with `--fee-rate` (a fraction of notional per side: 0.006 = 0.6%) and `--exchange-id coinbase`; the default costs would understate them.

## Label frequencies

| walk-forward label (TRAIN + TEST) | baseline | discovery-selected | ML layer |
|---|---:|---:|---:|
| ROBUST | 0/20 | 0/20 | 0/20 |
| TRAIN-ONLY | 0/20 | 4/20 | 2/20 |
| UNTESTED | 3/20 | 8/20 | 15/20 |
| NO-EDGE | 17/20 | 8/20 | 3/20 |

## Findings

- Verdict ROBUST (any of the 3 pre-registered candidates) = FALSE-POSITIVE rate (no edge exists): 0/20 = 0% (90% Wilson CI 0%-12%).
- Baseline labelled ROBUST = FALSE-POSITIVE rate (no edge exists): 0/20 = 0% (90% Wilson CI 0%-12%).
- Discovery-selected labelled ROBUST = FALSE-POSITIVE rate (no edge exists): 0/20 = 0% (90% Wilson CI 0%-12%).
- ML layer labelled ROBUST = FALSE-POSITIVE rate (no edge exists): 0/20 = 0% (90% Wilson CI 0%-12%).
- Baseline mean avg R: TRAIN -0.107 vs TEST -0.021.
- ML layer fitted in 20/20 seeds; its TEST avg R beat the baseline's in 5/20 of those (mean TEST avg R: base -0.021, ML -0.204; mean TEST n: base 43.9, ML 23.7).
- ML coefficients (standardised, fitted on TRAIN only): the largest-magnitude coefficient was an hour term (hour_sin/hour_cos) in 5/20 fitted seeds; mean hour-term magnitude sqrt(hour_sin^2 + hour_cos^2) = 0.149 log-odds per TRAIN s.d.
- Invariant violations over all backtests of all seeds: 0.

## Per seed (TRAIN and TEST side by side)

| seed | base TRAIN n | base TRAIN avg R | base TEST n | base TEST avg R | base TEST 90% CI low | base label | selected | selected TRAIN avg R | selected TEST avg R | selected label | ML TRAIN avg R (in-sample) | ML TEST avg R | ML TEST n | ML label | verdict ROBUST | violations |
|---:|---:|---:|---:|---:|---:|---|---|---:|---:|---|---:|---:|---:|---|---|---:|
| 1 | 98 | -0.138 | 58 | -0.121 | -0.379 | NO-EDGE | `rr2.5_vol2_rsi50-70` | -0.086 | -0.143 | NO-EDGE | +0.267 | -0.294 | 17 | UNTESTED | no | 0 |
| 2 | 91 | -0.426 | 39 | -0.231 | -0.538 | NO-EDGE | `rr2.5_vol2_rsi55-70` | -0.333 | +0.167 | NO-EDGE | -0.077 | -0.700 | 10 | UNTESTED | no | 0 |
| 3 | 89 | -0.124 | 36 | +0.542 | +0.128 | NO-EDGE | `rr3_vol2_rsi50-70` | +0.133 | +0.532 | UNTESTED | +0.071 | -1.000 | 2 | UNTESTED | no | 0 |
| 4 | 97 | -0.041 | 51 | -0.176 | -0.471 | NO-EDGE | `rr2_vol2_rsi50-70` | +0.084 | -0.455 | TRAIN-ONLY | +0.078 | -0.364 | 33 | TRAIN-ONLY | no | 0 |
| 5 | 105 | +0.029 | 46 | +0.081 | -0.272 | UNTESTED | `rr3_vol1.5_rsi55-70` | +0.268 | +0.271 | UNTESTED | -0.080 | -0.175 | 40 | NO-EDGE | no | 0 |
| 6 | 113 | -0.018 | 38 | -0.605 | -0.842 | NO-EDGE | `rr2.5_vol2_rsi50-70` | +0.006 | -0.580 | UNTESTED | +0.013 | -0.690 | 29 | UNTESTED | no | 0 |
| 7 | 87 | -0.112 | 40 | -0.308 | -0.625 | NO-EDGE | `rr2_vol1.5_rsi50-70` | -0.112 | -0.308 | NO-EDGE | -0.062 | -0.727 | 11 | UNTESTED | no | 0 |
| 8 | 110 | +0.145 | 35 | +0.029 | -0.314 | UNTESTED | `rr2.5_vol2_rsi50-70` | +0.318 | -0.087 | UNTESTED | +0.247 | +0.031 | 32 | UNTESTED | no | 0 |
| 9 | 113 | -0.071 | 51 | -0.059 | -0.353 | NO-EDGE | `rr3_vol2_rsi55-70` | +0.101 | +0.176 | UNTESTED | -0.096 | -0.036 | 28 | UNTESTED | no | 0 |
| 10 | 99 | -0.030 | 37 | +0.077 | -0.305 | NO-EDGE | `rr3_vol1.5_rsi55-70` | +0.105 | +0.000 | TRAIN-ONLY | +0.071 | +0.321 | 12 | UNTESTED | no | 0 |
| 11 | 91 | -0.077 | 56 | +0.010 | -0.304 | NO-EDGE | `rr2_vol1.5_rsi55-70` | -0.047 | -0.119 | NO-EDGE | +0.000 | -0.111 | 27 | UNTESTED | no | 0 |
| 12 | 128 | -0.039 | 54 | +0.203 | -0.131 | NO-EDGE | `rr2_vol2_rsi50-70` | +0.086 | +0.290 | UNTESTED | +0.061 | +0.179 | 28 | UNTESTED | no | 0 |
| 13 | 98 | -0.235 | 51 | +0.176 | -0.121 | NO-EDGE | `rr2.5_vol1.5_rsi50-70` | -0.135 | +0.138 | NO-EDGE | -0.214 | +0.174 | 23 | UNTESTED | no | 0 |
| 14 | 91 | -0.011 | 54 | -0.111 | -0.444 | NO-EDGE | `rr3_vol2_rsi50-70` | +0.051 | +0.023 | UNTESTED | -0.016 | -0.083 | 36 | NO-EDGE | no | 0 |
| 15 | 102 | +0.029 | 38 | +0.210 | -0.185 | UNTESTED | `rr2_vol1.5_rsi55-70` | +0.091 | -0.001 | TRAIN-ONLY | +0.040 | -0.001 | 31 | TRAIN-ONLY | no | 0 |
| 16 | 98 | -0.204 | 37 | +0.071 | -0.318 | NO-EDGE | `rr2_vol2_rsi55-70` | -0.047 | -0.163 | NO-EDGE | +0.059 | -0.060 | 23 | UNTESTED | no | 0 |
| 17 | 113 | -0.253 | 35 | -0.314 | -0.657 | NO-EDGE | `rr2.5_vol2_rsi55-70` | -0.065 | -0.067 | NO-EDGE | -0.051 | -0.318 | 22 | UNTESTED | no | 0 |
| 18 | 103 | -0.113 | 33 | +0.182 | -0.273 | NO-EDGE | `rr2.5_vol2_rsi55-70` | +0.054 | +0.375 | UNTESTED | -0.176 | +0.046 | 24 | UNTESTED | no | 0 |
| 19 | 103 | -0.164 | 46 | -0.193 | -0.519 | NO-EDGE | `rr3_vol2_rsi55-70` | +0.107 | -0.333 | TRAIN-ONLY | -0.026 | -0.122 | 32 | NO-EDGE | no | 0 |
| 20 | 94 | -0.298 | 43 | +0.116 | -0.233 | NO-EDGE | `rr2.5_vol2_rsi55-70` | -0.137 | +0.129 | NO-EDGE | -0.062 | -0.143 | 14 | UNTESTED | no | 0 |

## Risk disclaimer

This is a research and testing tool, not financial advice and not a recommendation to trade. Backtests and synthetic worlds are simplified models; past or simulated results do not predict future results. Crypto trading can result in the total loss of the capital used.
