# Calibration: synthetic world `decay`, seeds 1-10

This report does NOT promise, target or optimise a win rate, and nothing in this package selects on one. A win rate is meaningless without the reward:risk it was earned at: with the mandatory minimum of 2:1 a strategy breaks even at about 33% winners before costs, while a 90% win rate with a 1:9 payoff still loses money. A backtest win rate near 90% is evidence of curve-fitting or look-ahead, not of skill. The target metric is POSITIVE EXPECTANCY (average R per trade, net of fees and slippage) that survives a 70/30 chronological walk-forward with controlled drawdown, with every trade planned at reward:risk >= 2:1. Win rate appears below only as context and is labelled 'context only' wherever it is shown.

Each seed is a full run of the pipeline (6 years of 4H candles, 70/30 walk-forward): baseline, discovery (K = 13 variants, selected on TRAIN) and the ML layer. **Synthetic results are verification of the methodology, not evidence about real markets.** Each label below combines that candidate's TRAIN and TEST windows (`metrics.label`); the verdict takes up to three looks at TEST per seed.

- Ground truth: the planted effect exists only BEFORE the 70% split; the TEST period is exactly the null world.
- What the ground truth predicts: a positive TRAIN expectancy that does not survive TEST: TRAIN-ONLY (or UNTESTED when the TEST noise is positive but indistinguishable from zero); ROBUST is a false positive.
- **Costs used in every backtest:** fee 0.100% of notional per side (the default: Binance spot taker, no discounts) (`--fee-rate 0.001`), charged on the entry AND on the exit; slippage 0.05% (`--slippage-pct 0.05`) against the trade on market fills (entries and stop exits; take-profit limit exits get none); exchange `binance` (`--exchange-id`; `EXCHANGE:binance` news events block every pair). Starting capital 10,000 per window.
- Cost-aware sizing and targets (CONTRACT.md v2 A1): the planned risk is the ALL-IN loss at the stop (the stop fill after slippage plus both fees), so a clean stop is exactly -1R and the target is placed so that a take-profit nets exactly +2R after fees; the target's price distance is therefore more than 2x the stop distance. Only a gap through the stop loses more than 1R.
- Coinbase Advanced Trade taker fees at low volume tiers are several times Binance's, so a Coinbase run must pass the account's real tier with `--fee-rate` (a fraction of notional per side: 0.006 = 0.6%) and `--exchange-id coinbase`; the default costs would understate them.

## Label frequencies

| walk-forward label (TRAIN + TEST) | baseline | discovery-selected | ML layer |
|---|---:|---:|---:|
| ROBUST | 1/10 | 0/10 | 0/10 |
| TRAIN-ONLY | 6/10 | 6/10 | 6/10 |
| UNTESTED | 3/10 | 4/10 | 4/10 |
| NO-EDGE | 0/10 | 0/10 | 0/10 |

## Findings

- Verdict ROBUST (any of the 3 pre-registered candidates) = FALSE-POSITIVE rate (no edge exists in TEST): 1/10 = 10% (90% Wilson CI 2%-35%).
- Baseline labelled ROBUST = FALSE-POSITIVE rate (no edge exists in TEST): 1/10 = 10% (90% Wilson CI 2%-35%).
- Discovery-selected labelled ROBUST = FALSE-POSITIVE rate (no edge exists in TEST): 0/10 = 0% (90% Wilson CI 0%-21%).
- ML layer labelled ROBUST = FALSE-POSITIVE rate (no edge exists in TEST): 0/10 = 0% (90% Wilson CI 0%-21%).
- Baseline mean avg R: TRAIN +0.424 vs TEST -0.101.
- Baseline labelled TRAIN-ONLY: 6/10 = 60% (90% Wilson CI 35%-81%); UNTESTED: 3/10 = 30% (90% Wilson CI 13%-56%).
- Baseline labelled TRAIN-ONLY or UNTESTED (the decay is caught or at least not passed): 9/10 = 90% (90% Wilson CI 65%-98%).
- ML layer fitted in 10/10 seeds; its TEST avg R beat the baseline's in 4/10 of those (mean TEST avg R: base -0.101, ML -0.090; mean TEST n: base 43.7, ML 43.1).
- ML coefficients (standardised, fitted on TRAIN only): the largest-magnitude coefficient was an hour term (hour_sin/hour_cos) in 1/10 fitted seeds; mean hour-term magnitude sqrt(hour_sin^2 + hour_cos^2) = 0.178 log-odds per TRAIN s.d.
- Invariant violations over all backtests of all seeds: 0.

## Per seed (TRAIN and TEST side by side)

| seed | base TRAIN n | base TRAIN avg R | base TEST n | base TEST avg R | base TEST 90% CI low | base label | selected | selected TRAIN avg R | selected TEST avg R | selected label | ML TRAIN avg R (in-sample) | ML TEST avg R | ML TEST n | ML label | verdict ROBUST | violations |
|---:|---:|---:|---:|---:|---:|---|---|---:|---:|---|---:|---:|---:|---|---|---:|
| 1 | 114 | +0.307 | 56 | -0.143 | -0.464 | TRAIN-ONLY | `rr3_vol2_rsi50-70` | +0.742 | -0.043 | TRAIN-ONLY | +0.590 | -0.073 | 55 | TRAIN-ONLY | no | 0 |
| 2 | 116 | +0.371 | 40 | -0.325 | -0.625 | TRAIN-ONLY | `rr2_vol2_rsi55-70` | +0.567 | +0.000 | TRAIN-ONLY | +0.387 | -0.132 | 38 | TRAIN-ONLY | no | 0 |
| 3 | 111 | +0.360 | 39 | +0.501 | +0.116 | ROBUST | `rr3_vol2_rsi55-70` | +0.925 | +0.426 | UNTESTED | +0.495 | +0.322 | 42 | UNTESTED | yes | 0 |
| 4 | 105 | +0.457 | 51 | -0.176 | -0.471 | TRAIN-ONLY | `rr2_vol1.5_rsi50-70` | +0.457 | -0.176 | TRAIN-ONLY | +0.457 | -0.176 | 51 | TRAIN-ONLY | no | 0 |
| 5 | 124 | +0.403 | 46 | +0.081 | -0.272 | UNTESTED | `rr2_vol2_rsi50-70` | +0.825 | -0.093 | TRAIN-ONLY | +0.540 | +0.062 | 44 | UNTESTED | no | 0 |
| 6 | 118 | +0.424 | 39 | -0.615 | -0.846 | TRAIN-ONLY | `rr2_vol2_rsi50-70` | +0.600 | -0.667 | UNTESTED | +0.474 | -0.583 | 36 | TRAIN-ONLY | no | 0 |
| 7 | 114 | +0.569 | 40 | -0.308 | -0.625 | TRAIN-ONLY | `rr2_vol2_rsi55-70` | +0.842 | -0.270 | TRAIN-ONLY | +0.583 | -0.308 | 40 | TRAIN-ONLY | no | 0 |
| 8 | 109 | +0.459 | 35 | +0.029 | -0.314 | UNTESTED | `rr2_vol1.5_rsi50-70` | +0.459 | +0.029 | UNTESTED | +0.558 | +0.029 | 35 | UNTESTED | no | 0 |
| 9 | 121 | +0.465 | 52 | -0.077 | -0.365 | TRAIN-ONLY | `rr3_vol1.5_rsi55-70` | +0.845 | +0.189 | UNTESTED | +0.451 | -0.059 | 51 | TRAIN-ONLY | no | 0 |
| 10 | 101 | +0.426 | 39 | +0.022 | -0.319 | UNTESTED | `rr2_vol2_rsi50-70` | +0.573 | -0.004 | TRAIN-ONLY | +0.469 | +0.022 | 39 | UNTESTED | no | 0 |

## Risk disclaimer

This is a research and testing tool, not financial advice and not a recommendation to trade. Backtests and synthetic worlds are simplified models; past or simulated results do not predict future results. Crypto trading can result in the total loss of the capital used.
