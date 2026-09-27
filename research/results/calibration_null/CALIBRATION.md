# Calibration: synthetic world `null`, seeds 1-20

This report does NOT promise, target or optimise a win rate, and nothing in this package selects on one. A win rate is meaningless without the reward:risk it was earned at: with the mandatory minimum of 2:1 a strategy breaks even at about 33% winners before costs, while a 90% win rate with a 1:9 payoff still loses money. A backtest win rate near 90% is evidence of curve-fitting or look-ahead, not of skill. The target metric is POSITIVE EXPECTANCY (average R per trade, net of fees and slippage) that survives a 70/30 chronological walk-forward with controlled drawdown, with every trade planned at reward:risk >= 2:1. Win rate appears below only as context.

Each seed is a full run of the pipeline (6 years of 4H candles, 70/30 walk-forward): baseline, discovery (K = 13 variants, selected on TRAIN) and the ML layer. **Synthetic results are verification of the methodology, not evidence about real markets.**

- Ground truth: martingale prices (zero drift): no entry/exit rule has an edge before costs, so every strategy has negative expectancy after fees and slippage.
- What the ground truth predicts: nothing should be ROBUST; the expected labels are NO-EDGE, TRAIN-ONLY or UNTESTED, and a ROBUST label here is a false positive.

## Label frequencies

| label | baseline | discovery-selected | ML layer |
|---|---:|---:|---:|
| ROBUST | 0/20 | 0/20 | 0/20 |
| TRAIN-ONLY | 0/20 | 2/20 | 3/20 |
| UNTESTED | 2/20 | 11/20 | 15/20 |
| NO-EDGE | 18/20 | 7/20 | 2/20 |

## Findings

- Verdict ROBUST (any pre-registered candidate) = FALSE-POSITIVE rate (no edge exists): 0/20 = 0% (90% Wilson CI 0%-12%).
- Baseline labelled ROBUST = FALSE-POSITIVE rate (no edge exists): 0/20 = 0% (90% Wilson CI 0%-12%).
- Discovery-selected labelled ROBUST = FALSE-POSITIVE rate (no edge exists): 0/20 = 0% (90% Wilson CI 0%-12%).
- ML layer labelled ROBUST = FALSE-POSITIVE rate (no edge exists): 0/20 = 0% (90% Wilson CI 0%-12%).
- ML layer fitted in 20/20 seeds; its TEST avg R beat the baseline's in 8/20 of those (mean TEST avg R: base -0.037, ML -0.166).
- Invariant violations over all backtests of all seeds: 0.

## Per seed (TRAIN | TEST avg R side by side for the baseline)

| seed | base TRAIN avg R | base TEST avg R | base TEST n | base label | selected | selected TEST avg R | selected label | ML TEST avg R | ML TEST n | ML label | verdict ROBUST | violations |
|---:|---:|---:|---:|---|---|---:|---|---:|---:|---|---|---:|
| 1 | -0.108 | -0.204 | 58 | NO-EDGE | `rr2_vol2_rsi50-70` | -0.258 | NO-EDGE | -0.246 | 14 | UNTESTED | no | 0 |
| 2 | -0.341 | -0.188 | 40 | NO-EDGE | `rr2.5_vol2_rsi55-70` | +0.214 | NO-EDGE | -0.581 | 11 | UNTESTED | no | 0 |
| 3 | -0.135 | +0.320 | 44 | NO-EDGE | `rr2.5_vol2_rsi50-70` | +0.170 | UNTESTED | -1.034 | 1 | UNTESTED | no | 0 |
| 4 | -0.002 | -0.274 | 52 | NO-EDGE | `rr2_vol1.5_rsi55-70` | -0.361 | TRAIN-ONLY | -0.396 | 35 | TRAIN-ONLY | no | 0 |
| 5 | +0.031 | +0.100 | 51 | UNTESTED | `rr3_vol1.5_rsi50-70` | +0.049 | UNTESTED | -0.148 | 42 | TRAIN-ONLY | no | 0 |
| 6 | -0.132 | -0.679 | 38 | NO-EDGE | `rr3_vol2_rsi50-70` | -0.584 | UNTESTED | -0.823 | 25 | UNTESTED | no | 0 |
| 7 | -0.112 | -0.332 | 41 | NO-EDGE | `rr2_vol1.5_rsi50-70` | -0.332 | NO-EDGE | -0.129 | 12 | UNTESTED | no | 0 |
| 8 | +0.184 | +0.091 | 31 | UNTESTED | `rr2_vol2_rsi50-70` | -0.146 | UNTESTED | +0.115 | 35 | UNTESTED | no | 0 |
| 9 | -0.193 | -0.046 | 47 | NO-EDGE | `rr3_vol2_rsi55-70` | +0.229 | UNTESTED | -0.013 | 34 | NO-EDGE | no | 0 |
| 10 | -0.013 | -0.065 | 39 | NO-EDGE | `rr3_vol1.5_rsi55-70` | +0.056 | UNTESTED | +0.284 | 14 | UNTESTED | no | 0 |
| 11 | -0.141 | -0.016 | 56 | NO-EDGE | `rr2.5_vol2_rsi55-70` | -0.245 | NO-EDGE | -0.238 | 18 | UNTESTED | no | 0 |
| 12 | -0.044 | +0.102 | 63 | NO-EDGE | `rr2.5_vol2_rsi50-70` | +0.296 | UNTESTED | +0.278 | 27 | UNTESTED | no | 0 |
| 13 | -0.207 | +0.144 | 52 | NO-EDGE | `rr2.5_vol1.5_rsi50-70` | -0.006 | NO-EDGE | +0.344 | 17 | UNTESTED | no | 0 |
| 14 | -0.007 | -0.050 | 58 | NO-EDGE | `rr2_vol1.5_rsi50-70` | -0.050 | NO-EDGE | -0.352 | 41 | TRAIN-ONLY | no | 0 |
| 15 | -0.008 | +0.256 | 48 | NO-EDGE | `rr2_vol1.5_rsi55-70` | +0.066 | UNTESTED | +0.039 | 36 | UNTESTED | no | 0 |
| 16 | -0.129 | +0.036 | 33 | NO-EDGE | `rr2_vol2_rsi55-70` | -0.122 | UNTESTED | -0.013 | 26 | UNTESTED | no | 0 |
| 17 | -0.270 | -0.153 | 39 | NO-EDGE | `rr2.5_vol2_rsi55-70` | -0.150 | NO-EDGE | -0.200 | 24 | UNTESTED | no | 0 |
| 18 | -0.109 | +0.074 | 31 | NO-EDGE | `rr3_vol2_rsi55-70` | +0.438 | UNTESTED | +0.149 | 18 | UNTESTED | no | 0 |
| 19 | -0.090 | +0.029 | 50 | NO-EDGE | `rr3_vol2_rsi55-70` | -0.416 | TRAIN-ONLY | +0.234 | 34 | NO-EDGE | no | 0 |
| 20 | -0.308 | +0.123 | 45 | NO-EDGE | `rr3_vol2_rsi55-70` | +0.108 | UNTESTED | -0.581 | 12 | UNTESTED | no | 0 |

## Risk disclaimer

This is a research and testing tool, not financial advice and not a recommendation to trade. Backtests and synthetic worlds are simplified models; past or simulated results do not predict future results. Crypto trading can result in the total loss of the capital used.
