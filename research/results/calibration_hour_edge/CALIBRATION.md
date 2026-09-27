# Calibration: synthetic world `hour_edge`, seeds 1-10

This report does NOT promise, target or optimise a win rate, and nothing in this package selects on one. A win rate is meaningless without the reward:risk it was earned at: with the mandatory minimum of 2:1 a strategy breaks even at about 33% winners before costs, while a 90% win rate with a 1:9 payoff still loses money. A backtest win rate near 90% is evidence of curve-fitting or look-ahead, not of skill. The target metric is POSITIVE EXPECTANCY (average R per trade, net of fees and slippage) that survives a 70/30 chronological walk-forward with controlled drawdown, with every trade planned at reward:risk >= 2:1. Win rate appears below only as context.

Each seed is a full run of the pipeline (6 years of 4H candles, 70/30 walk-forward): baseline, discovery (K = 13 variants, selected on TRAIN) and the ML layer. **Synthetic results are verification of the methodology, not evidence about real markets.**

- Ground truth: the planted effect exists only for spike candles closing 12:00-20:00 UTC; spikes closing at other hours trigger nothing.
- What the ground truth predicts: the hour-blind base is diluted (weak or no edge); an hour-aware filter fitted on TRAIN has something real to learn and should raise TEST expectancy versus the base.

## Label frequencies

| label | baseline | discovery-selected | ML layer |
|---|---:|---:|---:|
| ROBUST | 0/10 | 2/10 | 5/10 |
| TRAIN-ONLY | 2/10 | 2/10 | 0/10 |
| UNTESTED | 6/10 | 6/10 | 5/10 |
| NO-EDGE | 2/10 | 0/10 | 0/10 |

## Findings

- Verdict ROBUST (any pre-registered candidate) (a real but hour-specific edge exists): 6/10 = 60% (90% Wilson CI 35%-81%).
- Baseline labelled ROBUST (a real but hour-specific edge exists): 0/10 = 0% (90% Wilson CI 0%-21%).
- Discovery-selected labelled ROBUST (a real but hour-specific edge exists): 2/10 = 20% (90% Wilson CI 7%-46%).
- ML layer labelled ROBUST (a real but hour-specific edge exists): 5/10 = 50% (90% Wilson CI 27%-73%).
- ML layer fitted in 10/10 seeds; its TEST avg R beat the baseline's in 8/10 of those (mean TEST avg R: base +0.170, ML +0.368).
- Invariant violations over all backtests of all seeds: 0.

## Per seed (TRAIN | TEST avg R side by side for the baseline)

| seed | base TRAIN avg R | base TEST avg R | base TEST n | base label | selected | selected TEST avg R | selected label | ML TEST avg R | ML TEST n | ML label | verdict ROBUST | violations |
|---:|---:|---:|---:|---|---|---:|---|---:|---:|---|---|---:|
| 1 | -0.017 | +0.052 | 61 | NO-EDGE | `rr3_vol2_rsi50-70` | +0.307 | UNTESTED | +0.576 | 42 | ROBUST | yes | 0 |
| 2 | +0.256 | -0.010 | 64 | TRAIN-ONLY | `rr2.5_vol2_rsi50-70` | -0.076 | TRAIN-ONLY | +0.034 | 43 | UNTESTED | no | 0 |
| 3 | +0.166 | -0.017 | 45 | TRAIN-ONLY | `rr2.5_vol2_rsi55-70` | -0.087 | TRAIN-ONLY | +0.150 | 41 | UNTESTED | no | 0 |
| 4 | -0.041 | +0.221 | 53 | NO-EDGE | `rr2_vol2_rsi50-70` | +0.497 | ROBUST | +0.784 | 37 | ROBUST | yes | 0 |
| 5 | +0.233 | +0.370 | 48 | UNTESTED | `rr3_vol2_rsi55-70` | +0.231 | UNTESTED | +0.304 | 46 | UNTESTED | no | 0 |
| 6 | +0.114 | +0.146 | 44 | UNTESTED | `rr2_vol2_rsi55-70` | +0.382 | UNTESTED | +0.511 | 34 | ROBUST | yes | 0 |
| 7 | +0.297 | +0.134 | 58 | UNTESTED | `rr2_vol1.5_rsi55-70` | +0.171 | UNTESTED | +0.391 | 45 | ROBUST | yes | 0 |
| 8 | +0.245 | +0.299 | 58 | UNTESTED | `rr2.5_vol2_rsi50-70` | +0.450 | ROBUST | +0.271 | 57 | UNTESTED | yes | 0 |
| 9 | +0.095 | +0.203 | 40 | UNTESTED | `rr3_vol2_rsi55-70` | +0.506 | UNTESTED | +0.265 | 36 | UNTESTED | no | 0 |
| 10 | +0.272 | +0.302 | 50 | UNTESTED | `rr2.5_vol2_rsi55-70` | +0.206 | UNTESTED | +0.395 | 47 | ROBUST | yes | 0 |

## Risk disclaimer

This is a research and testing tool, not financial advice and not a recommendation to trade. Backtests and synthetic worlds are simplified models; past or simulated results do not predict future results. Crypto trading can result in the total loss of the capital used.
