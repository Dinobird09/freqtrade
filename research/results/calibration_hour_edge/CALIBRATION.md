# Calibration: synthetic world `hour_edge`, seeds 1-10

This report does NOT promise, target or optimise a win rate, and nothing in this package selects on one. A win rate is meaningless without the reward:risk it was earned at: with the mandatory minimum of 2:1 a strategy breaks even at about 33% winners before costs, while a 90% win rate with a 1:9 payoff still loses money. A backtest win rate near 90% is evidence of curve-fitting or look-ahead, not of skill. The target metric is POSITIVE EXPECTANCY (average R per trade, net of fees and slippage) that survives a 70/30 chronological walk-forward with controlled drawdown, with every trade planned at reward:risk >= 2:1. Win rate appears below only as context and is labelled 'context only' wherever it is shown.

Each seed is a full run of the pipeline (6 years of 4H candles, 70/30 walk-forward): baseline, discovery (K = 13 variants, selected on TRAIN) and the ML layer. **Synthetic results are verification of the methodology, not evidence about real markets.** Each label below combines that candidate's TRAIN and TEST windows (`metrics.label`); the verdict takes up to three looks at TEST per seed.

- Ground truth: the planted effect exists only for spike candles closing 12:00-20:00 UTC; spikes closing at other hours trigger nothing.
- What the ground truth predicts: the hour-blind base is diluted (weak or no edge); an hour-aware filter fitted on TRAIN has something real to learn and should raise TEST expectancy versus the base.
- **Costs used in every backtest:** fee 0.100% of notional per side (the default: Binance spot taker, no discounts) (`--fee-rate 0.001`), charged on the entry AND on the exit; slippage 0.05% (`--slippage-pct 0.05`) against the trade on market fills (entries and stop exits; take-profit limit exits get none); exchange `binance` (`--exchange-id`; `EXCHANGE:binance` news events block every pair). Starting capital 10,000 per window.
- Cost-aware sizing and targets (CONTRACT.md v2 A1): the planned risk is the ALL-IN loss at the stop (the stop fill after slippage plus both fees), so a clean stop is exactly -1R and the target is placed so that a take-profit nets exactly +2R after fees; the target's price distance is therefore more than 2x the stop distance. Only a gap through the stop loses more than 1R.
- Coinbase Advanced Trade taker fees at low volume tiers are several times Binance's, so a Coinbase run must pass the account's real tier with `--fee-rate` (a fraction of notional per side: 0.006 = 0.6%) and `--exchange-id coinbase`; the default costs would understate them.

## Label frequencies

| walk-forward label (TRAIN + TEST) | baseline | discovery-selected | ML layer |
|---|---:|---:|---:|
| ROBUST | 1/10 | 3/10 | 3/10 |
| TRAIN-ONLY | 1/10 | 1/10 | 0/10 |
| UNTESTED | 6/10 | 6/10 | 7/10 |
| NO-EDGE | 2/10 | 0/10 | 0/10 |

## Findings

- Verdict ROBUST (any of the 3 pre-registered candidates) (a real but hour-specific edge exists): 4/10 = 40% (90% Wilson CI 19%-65%).
- Baseline labelled ROBUST (a real but hour-specific edge exists): 1/10 = 10% (90% Wilson CI 2%-35%).
- Discovery-selected labelled ROBUST (a real but hour-specific edge exists): 3/10 = 30% (90% Wilson CI 13%-56%).
- ML layer labelled ROBUST (a real but hour-specific edge exists): 3/10 = 30% (90% Wilson CI 13%-56%).
- Baseline mean avg R: TRAIN +0.178 vs TEST +0.185.
- ML layer fitted in 10/10 seeds; its TEST avg R beat the baseline's in 9/10 of those (mean TEST avg R: base +0.185, ML +0.342; mean TEST n: base 50.8, ML 42.7).
- ML coefficients (standardised, fitted on TRAIN only): the largest-magnitude coefficient was an hour term (hour_sin/hour_cos) in 6/10 fitted seeds; mean hour-term magnitude sqrt(hour_sin^2 + hour_cos^2) = 0.464 log-odds per TRAIN s.d.
- Invariant violations over all backtests of all seeds: 0.

## Per seed (TRAIN and TEST side by side)

| seed | base TRAIN n | base TRAIN avg R | base TEST n | base TEST avg R | base TEST 90% CI low | base label | selected | selected TRAIN avg R | selected TEST avg R | selected label | ML TRAIN avg R (in-sample) | ML TEST avg R | ML TEST n | ML label | verdict ROBUST | violations |
|---:|---:|---:|---:|---:|---:|---|---|---:|---:|---|---:|---:|---:|---|---|---:|
| 1 | 100 | -0.044 | 60 | +0.050 | -0.250 | NO-EDGE | `rr3_vol2_rsi50-70` | +0.203 | +0.436 | UNTESTED | +0.299 | +0.295 | 44 | UNTESTED | no | 0 |
| 2 | 113 | +0.274 | 61 | -0.016 | -0.311 | TRAIN-ONLY | `rr2.5_vol2_rsi50-70` | +0.545 | +0.034 | UNTESTED | +0.567 | +0.091 | 44 | UNTESTED | no | 0 |
| 3 | 117 | +0.244 | 46 | +0.097 | -0.229 | UNTESTED | `rr2_vol2_rsi55-70` | +0.338 | -0.115 | TRAIN-ONLY | +0.469 | +0.167 | 43 | UNTESTED | no | 0 |
| 4 | 114 | -0.026 | 52 | +0.269 | -0.077 | NO-EDGE | `rr2.5_vol2_rsi50-70` | +0.312 | +0.658 | ROBUST | +0.214 | +0.784 | 37 | ROBUST | yes | 0 |
| 5 | 123 | +0.244 | 45 | +0.400 | +0.067 | ROBUST | `rr2.5_vol2_rsi55-70` | +0.793 | +0.288 | UNTESTED | +0.278 | +0.465 | 43 | ROBUST | yes | 0 |
| 6 | 102 | +0.088 | 46 | +0.239 | -0.087 | UNTESTED | `rr2_vol2_rsi55-70` | +0.326 | +0.500 | ROBUST | +0.312 | +0.538 | 39 | ROBUST | yes | 0 |
| 7 | 109 | +0.257 | 54 | +0.077 | -0.235 | UNTESTED | `rr3_vol1.5_rsi55-70` | +0.445 | +0.337 | UNTESTED | +0.401 | +0.317 | 41 | UNTESTED | no | 0 |
| 8 | 110 | +0.234 | 55 | +0.174 | -0.127 | UNTESTED | `rr2.5_vol2_rsi50-70` | +0.531 | +0.424 | ROBUST | +0.321 | +0.196 | 54 | UNTESTED | yes | 0 |
| 9 | 132 | +0.182 | 40 | +0.275 | -0.100 | UNTESTED | `rr3_vol2_rsi55-70` | +0.730 | +0.793 | UNTESTED | +0.261 | +0.297 | 37 | UNTESTED | no | 0 |
| 10 | 104 | +0.327 | 49 | +0.286 | -0.082 | UNTESTED | `rr2.5_vol2_rsi55-70` | +0.639 | +0.328 | UNTESTED | +0.633 | +0.267 | 45 | UNTESTED | no | 0 |

## Risk disclaimer

This is a research and testing tool, not financial advice and not a recommendation to trade. Backtests and synthetic worlds are simplified models; past or simulated results do not predict future results. Crypto trading can result in the total loss of the capital used.
