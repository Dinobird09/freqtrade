# Calibration: synthetic world `planted`, seeds 1-10

This report does NOT promise, target or optimise a win rate, and nothing in this package selects on one. A win rate is meaningless without the reward:risk it was earned at: with the mandatory minimum of 2:1 a strategy breaks even at about 33% winners before costs, while a 90% win rate with a 1:9 payoff still loses money. A backtest win rate near 90% is evidence of curve-fitting or look-ahead, not of skill. The target metric is POSITIVE EXPECTANCY (average R per trade, net of fees and slippage) that survives a 70/30 chronological walk-forward with controlled drawdown, with every trade planned at reward:risk >= 2:1. Win rate appears below only as context and is labelled 'context only' wherever it is shown.

Each seed is a full run of the pipeline (6 years of 4H candles, 70/30 walk-forward): baseline, discovery (K = 13 variants, selected on TRAIN) and the ML layer. **Synthetic results are verification of the methodology, not evidence about real markets.** Each label below combines that candidate's TRAIN and TEST windows (`metrics.label`); the verdict takes up to three looks at TEST per seed.

- Ground truth: after every up-closing volume-spike candle the next 12 candles get extra positive drift (0.7 sigma per candle) over the WHOLE history; the drift is offset elsewhere, so buy-and-hold gains nothing from it.
- What the ground truth predicts: the base rules (which require a volume spike) should show positive expectancy on TRAIN and TEST; ROBUST is the correct label when the TEST sample is large enough.
- **Costs used in every backtest:** fee 0.100% of notional per side (the default: Binance spot taker, no discounts) (`--fee-rate 0.001`), charged on the entry AND on the exit; slippage 0.05% (`--slippage-pct 0.05`) against the trade on market fills (entries and stop exits; take-profit limit exits get none); exchange `binance` (`--exchange-id`; `EXCHANGE:binance` news events block every pair). Starting capital 10,000 per window.
- Cost-aware sizing and targets (CONTRACT.md v2 A1): the planned risk is the ALL-IN loss at the stop (the stop fill after slippage plus both fees), so a clean stop is exactly -1R and the target is placed so that a take-profit nets exactly +2R after fees; the target's price distance is therefore more than 2x the stop distance. Only a gap through the stop loses more than 1R.
- Coinbase Advanced Trade taker fees at low volume tiers are several times Binance's, so a Coinbase run must pass the account's real tier with `--fee-rate` (a fraction of notional per side: 0.006 = 0.6%) and `--exchange-id coinbase`; the default costs would understate them.

## Label frequencies

| walk-forward label (TRAIN + TEST) | baseline | discovery-selected | ML layer |
|---|---:|---:|---:|
| ROBUST | 8/10 | 6/10 | 7/10 |
| TRAIN-ONLY | 0/10 | 0/10 | 0/10 |
| UNTESTED | 2/10 | 4/10 | 3/10 |
| NO-EDGE | 0/10 | 0/10 | 0/10 |

## Findings

- Verdict ROBUST (any of the 3 pre-registered candidates) = DETECTION rate (a real edge exists in TRAIN and TEST): 8/10 = 80% (90% Wilson CI 54%-93%).
- Baseline labelled ROBUST = DETECTION rate (a real edge exists in TRAIN and TEST): 8/10 = 80% (90% Wilson CI 54%-93%).
- Discovery-selected labelled ROBUST = DETECTION rate (a real edge exists in TRAIN and TEST): 6/10 = 60% (90% Wilson CI 35%-81%).
- ML layer labelled ROBUST = DETECTION rate (a real edge exists in TRAIN and TEST): 7/10 = 70% (90% Wilson CI 44%-87%).
- Baseline mean avg R: TRAIN +0.450 vs TEST +0.460.
- ML layer fitted in 10/10 seeds; its TEST avg R beat the baseline's in 3/10 of those (mean TEST avg R: base +0.460, ML +0.442; mean TEST n: base 48.3, ML 44.6).
- ML coefficients (standardised, fitted on TRAIN only): the largest-magnitude coefficient was an hour term (hour_sin/hour_cos) in 1/10 fitted seeds; mean hour-term magnitude sqrt(hour_sin^2 + hour_cos^2) = 0.192 log-odds per TRAIN s.d.
- Invariant violations over all backtests of all seeds: 0.

## Per seed (TRAIN and TEST side by side)

| seed | base TRAIN n | base TRAIN avg R | base TEST n | base TEST avg R | base TEST 90% CI low | base label | selected | selected TRAIN avg R | selected TEST avg R | selected label | ML TRAIN avg R (in-sample) | ML TEST avg R | ML TEST n | ML label | verdict ROBUST | violations |
|---:|---:|---:|---:|---:|---:|---|---|---:|---:|---|---:|---:|---:|---|---|---:|
| 1 | 115 | +0.296 | 51 | +0.412 | +0.059 | ROBUST | `rr3_vol2_rsi50-70` | +0.836 | +0.829 | ROBUST | +0.489 | +0.235 | 34 | UNTESTED | yes | 0 |
| 2 | 113 | +0.327 | 49 | +0.347 | +0.041 | ROBUST | `rr2.5_vol1.5_rsi55-70` | +0.731 | +0.400 | UNTESTED | +0.343 | +0.468 | 47 | ROBUST | yes | 0 |
| 3 | 114 | +0.430 | 59 | +0.545 | +0.220 | ROBUST | `rr3_vol2_rsi50-70` | +0.957 | +0.715 | ROBUST | +0.495 | +0.467 | 56 | ROBUST | yes | 0 |
| 4 | 105 | +0.457 | 36 | +0.667 | +0.250 | ROBUST | `rr2_vol2_rsi55-70` | +0.575 | +0.778 | UNTESTED | +0.457 | +0.667 | 36 | ROBUST | yes | 0 |
| 5 | 128 | +0.406 | 45 | +0.510 | +0.133 | ROBUST | `rr2_vol2_rsi50-70` | +0.824 | +0.638 | ROBUST | +0.450 | +0.511 | 41 | ROBUST | yes | 0 |
| 6 | 126 | +0.452 | 41 | +0.354 | -0.012 | UNTESTED | `rr2_vol2_rsi50-70` | +0.639 | +0.329 | UNTESTED | +0.500 | +0.313 | 40 | UNTESTED | no | 0 |
| 7 | 114 | +0.622 | 60 | +0.358 | +0.058 | ROBUST | `rr2_vol2_rsi55-70` | +0.896 | +0.616 | ROBUST | +0.622 | +0.358 | 60 | ROBUST | yes | 0 |
| 8 | 106 | +0.443 | 50 | +0.588 | +0.256 | ROBUST | `rr2_vol1.5_rsi55-70` | +0.500 | +0.625 | ROBUST | +0.579 | +0.567 | 43 | ROBUST | yes | 0 |
| 9 | 122 | +0.551 | 51 | +0.647 | +0.294 | ROBUST | `rr2_vol1.5_rsi55-70` | +0.633 | +0.653 | ROBUST | +0.551 | +0.680 | 50 | ROBUST | yes | 0 |
| 10 | 113 | +0.513 | 41 | +0.171 | -0.195 | UNTESTED | `rr2_vol2_rsi50-70` | +0.652 | +0.444 | UNTESTED | +0.528 | +0.154 | 39 | UNTESTED | no | 0 |

## Risk disclaimer

This is a research and testing tool, not financial advice and not a recommendation to trade. Backtests and synthetic worlds are simplified models; past or simulated results do not predict future results. Crypto trading can result in the total loss of the capital used.
