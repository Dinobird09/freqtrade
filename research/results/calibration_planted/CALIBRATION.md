# Calibration: synthetic world `planted`, seeds 1-10

This report does NOT promise, target or optimise a win rate, and nothing in this package selects on one. A win rate is meaningless without the reward:risk it was earned at: with the mandatory minimum of 2:1 a strategy breaks even at about 33% winners before costs, while a 90% win rate with a 1:9 payoff still loses money. A backtest win rate near 90% is evidence of curve-fitting or look-ahead, not of skill. The target metric is POSITIVE EXPECTANCY (average R per trade, net of fees and slippage) that survives a 70/30 chronological walk-forward with controlled drawdown, with every trade planned at reward:risk >= 2:1. Win rate appears below only as context.

Each seed is a full run of the pipeline (6 years of 4H candles, 70/30 walk-forward): baseline, discovery (K = 13 variants, selected on TRAIN) and the ML layer. **Synthetic results are verification of the methodology, not evidence about real markets.**

- Ground truth: after every up-closing volume-spike candle the next 12 candles get extra positive drift (0.7 sigma per candle) over the WHOLE history; the drift is offset elsewhere, so buy-and-hold gains nothing from it.
- What the ground truth predicts: the base rules (which require a volume spike) should show positive expectancy on TRAIN and TEST; ROBUST is the correct label when the TEST sample is large enough.

## Label frequencies

| label | baseline | discovery-selected | ML layer |
|---|---:|---:|---:|
| ROBUST | 8/10 | 8/10 | 7/10 |
| TRAIN-ONLY | 0/10 | 0/10 | 0/10 |
| UNTESTED | 2/10 | 2/10 | 3/10 |
| NO-EDGE | 0/10 | 0/10 | 0/10 |

## Findings

- Verdict ROBUST (any pre-registered candidate) = DETECTION rate (a real edge exists in TRAIN and TEST): 9/10 = 90% (90% Wilson CI 65%-98%).
- Baseline labelled ROBUST = DETECTION rate (a real edge exists in TRAIN and TEST): 8/10 = 80% (90% Wilson CI 54%-93%).
- Discovery-selected labelled ROBUST = DETECTION rate (a real edge exists in TRAIN and TEST): 8/10 = 80% (90% Wilson CI 54%-93%).
- ML layer labelled ROBUST = DETECTION rate (a real edge exists in TRAIN and TEST): 7/10 = 70% (90% Wilson CI 44%-87%).
- ML layer fitted in 10/10 seeds; its TEST avg R beat the baseline's in 5/10 of those (mean TEST avg R: base +0.444, ML +0.434).
- Invariant violations over all backtests of all seeds: 0.

## Per seed (TRAIN | TEST avg R side by side for the baseline)

| seed | base TRAIN avg R | base TEST avg R | base TEST n | base label | selected | selected TEST avg R | selected label | ML TEST avg R | ML TEST n | ML label | verdict ROBUST | violations |
|---:|---:|---:|---:|---|---|---:|---|---:|---:|---|---|---:|
| 1 | +0.254 | +0.405 | 53 | ROBUST | `rr2.5_vol2_rsi55-70` | +0.784 | ROBUST | +0.186 | 36 | UNTESTED | yes | 0 |
| 2 | +0.245 | +0.454 | 51 | ROBUST | `rr3_vol1.5_rsi55-70` | +0.567 | UNTESTED | +0.588 | 47 | ROBUST | yes | 0 |
| 3 | +0.534 | +0.388 | 62 | ROBUST | `rr3_vol2_rsi50-70` | +0.558 | ROBUST | +0.333 | 58 | ROBUST | yes | 0 |
| 4 | +0.495 | +0.754 | 41 | ROBUST | `rr2_vol2_rsi50-70` | +0.862 | ROBUST | +0.754 | 41 | ROBUST | yes | 0 |
| 5 | +0.379 | +0.499 | 47 | ROBUST | `rr2_vol2_rsi50-70` | +0.556 | ROBUST | +0.512 | 43 | ROBUST | yes | 0 |
| 6 | +0.378 | +0.367 | 43 | UNTESTED | `rr2_vol2_rsi50-70` | +0.479 | ROBUST | +0.330 | 42 | UNTESTED | yes | 0 |
| 7 | +0.595 | +0.355 | 61 | ROBUST | `rr2_vol2_rsi55-70` | +0.640 | ROBUST | +0.381 | 58 | ROBUST | yes | 0 |
| 8 | +0.412 | +0.510 | 52 | ROBUST | `rr2_vol1.5_rsi55-70` | +0.552 | ROBUST | +0.492 | 45 | ROBUST | yes | 0 |
| 9 | +0.486 | +0.566 | 53 | ROBUST | `rr2.5_vol1.5_rsi50-70` | +0.598 | ROBUST | +0.598 | 52 | ROBUST | yes | 0 |
| 10 | +0.402 | +0.142 | 42 | UNTESTED | `rr2_vol2_rsi50-70` | +0.436 | UNTESTED | +0.162 | 39 | UNTESTED | no | 0 |

## Risk disclaimer

This is a research and testing tool, not financial advice and not a recommendation to trade. Backtests and synthetic worlds are simplified models; past or simulated results do not predict future results. Crypto trading can result in the total loss of the capital used.
