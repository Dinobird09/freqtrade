# Calibration: synthetic world `decay`, seeds 1-10

This report does NOT promise, target or optimise a win rate, and nothing in this package selects on one. A win rate is meaningless without the reward:risk it was earned at: with the mandatory minimum of 2:1 a strategy breaks even at about 33% winners before costs, while a 90% win rate with a 1:9 payoff still loses money. A backtest win rate near 90% is evidence of curve-fitting or look-ahead, not of skill. The target metric is POSITIVE EXPECTANCY (average R per trade, net of fees and slippage) that survives a 70/30 chronological walk-forward with controlled drawdown, with every trade planned at reward:risk >= 2:1. Win rate appears below only as context.

Each seed is a full run of the pipeline (6 years of 4H candles, 70/30 walk-forward): baseline, discovery (K = 13 variants, selected on TRAIN) and the ML layer. **Synthetic results are verification of the methodology, not evidence about real markets.**

- Ground truth: the planted effect exists only BEFORE the 70% split; the TEST period is exactly the null world.
- What the ground truth predicts: a positive TRAIN expectancy that does not survive TEST: TRAIN-ONLY (or UNTESTED when the TEST noise is positive but indistinguishable from zero); ROBUST is a false positive.

## Label frequencies

| label | baseline | discovery-selected | ML layer |
|---|---:|---:|---:|
| ROBUST | 0/10 | 0/10 | 0/10 |
| TRAIN-ONLY | 7/10 | 6/10 | 7/10 |
| UNTESTED | 3/10 | 4/10 | 3/10 |
| NO-EDGE | 0/10 | 0/10 | 0/10 |

## Findings

- Verdict ROBUST (any pre-registered candidate) = FALSE-POSITIVE rate (no edge exists in TEST): 0/10 = 0% (90% Wilson CI 0%-21%).
- Baseline labelled ROBUST = FALSE-POSITIVE rate (no edge exists in TEST): 0/10 = 0% (90% Wilson CI 0%-21%).
- Discovery-selected labelled ROBUST = FALSE-POSITIVE rate (no edge exists in TEST): 0/10 = 0% (90% Wilson CI 0%-21%).
- ML layer labelled ROBUST = FALSE-POSITIVE rate (no edge exists in TEST): 0/10 = 0% (90% Wilson CI 0%-21%).
- Baseline labelled TRAIN-ONLY or UNTESTED: 10/10 = 100% (90% Wilson CI 79%-100%).
- ML layer fitted in 10/10 seeds; its TEST avg R beat the baseline's in 5/10 of those (mean TEST avg R: base -0.152, ML -0.124).
- Invariant violations over all backtests of all seeds: 0.

## Per seed (TRAIN | TEST avg R side by side for the baseline)

| seed | base TRAIN avg R | base TEST avg R | base TEST n | base label | selected | selected TEST avg R | selected label | ML TEST avg R | ML TEST n | ML label | verdict ROBUST | violations |
|---:|---:|---:|---:|---|---|---:|---|---:|---:|---|---|---:|
| 1 | +0.279 | -0.201 | 58 | TRAIN-ONLY | `rr3_vol2_rsi50-70` | -0.158 | TRAIN-ONLY | -0.086 | 54 | TRAIN-ONLY | no | 0 |
| 2 | +0.268 | -0.287 | 41 | TRAIN-ONLY | `rr2.5_vol1.5_rsi55-70` | -0.152 | TRAIN-ONLY | -0.091 | 39 | TRAIN-ONLY | no | 0 |
| 3 | +0.446 | +0.259 | 46 | UNTESTED | `rr3_vol2_rsi55-70` | +0.360 | UNTESTED | +0.297 | 45 | UNTESTED | no | 0 |
| 4 | +0.468 | -0.274 | 52 | TRAIN-ONLY | `rr2_vol2_rsi50-70` | -0.530 | TRAIN-ONLY | -0.274 | 52 | TRAIN-ONLY | no | 0 |
| 5 | +0.344 | +0.100 | 51 | UNTESTED | `rr2_vol2_rsi50-70` | -0.038 | TRAIN-ONLY | +0.114 | 48 | UNTESTED | no | 0 |
| 6 | +0.338 | -0.691 | 39 | TRAIN-ONLY | `rr2_vol2_rsi50-70` | -0.732 | UNTESTED | -0.657 | 36 | TRAIN-ONLY | no | 0 |
| 7 | +0.552 | -0.333 | 41 | TRAIN-ONLY | `rr2_vol2_rsi55-70` | -0.343 | TRAIN-ONLY | -0.333 | 41 | TRAIN-ONLY | no | 0 |
| 8 | +0.499 | +0.090 | 31 | UNTESTED | `rr2_vol2_rsi50-70` | -0.139 | UNTESTED | +0.077 | 39 | UNTESTED | no | 0 |
| 9 | +0.424 | -0.068 | 48 | TRAIN-ONLY | `rr3_vol1.5_rsi55-70` | +0.124 | UNTESTED | -0.176 | 47 | TRAIN-ONLY | no | 0 |
| 10 | +0.373 | -0.115 | 41 | TRAIN-ONLY | `rr2_vol2_rsi50-70` | -0.052 | TRAIN-ONLY | -0.115 | 41 | TRAIN-ONLY | no | 0 |

## Risk disclaimer

This is a research and testing tool, not financial advice and not a recommendation to trade. Backtests and synthetic worlds are simplified models; past or simulated results do not predict future results. Crypto trading can result in the total loss of the capital used.
