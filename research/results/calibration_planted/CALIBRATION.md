# Calibration: synthetic world `planted`, seeds 1-20

This report does NOT promise, target or optimise a win rate, and nothing in this package selects on one. A win rate is meaningless without the reward:risk it was earned at: with the mandatory minimum of 2:1 a strategy breaks even at about 33% winners before costs, while a 90% win rate with a 1:9 payoff still loses money. A backtest win rate near 90% is evidence of curve-fitting or look-ahead, not of skill. The target metric is POSITIVE EXPECTANCY (average R per trade, net of fees and slippage) that survives a 70/30 chronological walk-forward with controlled drawdown, with every trade planned at reward:risk >= 2:1. Win rate appears below only as context and is labelled 'context only' wherever it is shown.

Each seed is a full run of the pipeline (6 years of 4H candles, 70/30 walk-forward): baseline, discovery (K = 13 variants, one selected on TRAIN), the ML layer and the expectancy guard, i.e. m = 4 pre-registered TEST looks per seed. **Synthetic results are verification of the methodology, not evidence about real markets.** Each label below combines that candidate's TRAIN and TEST windows (`metrics.label`). ROBUST requires at least 30 trades in each window, TRAIN and TEST avg R > 0, and a one-sided 98.75% lower bound of the TEST mean above zero for BOTH an iid and a calendar-month block bootstrap (the more conservative is used): alpha 0.05 is split over the m = 4 pre-registered candidates (base, discovery-selected, base+ml, base+guard), so when none has an edge the chance that ANY is called ROBUST is at most about 5%; a positive TEST mean that fails only the bound is UNTESTED (positive but not distinguishable from zero after multiplicity correction); the rule was fixed in advance and is never tuned on TEST outcomes. The ML layer's TRAIN columns are its out-of-sample TRAIN gate (CONTRACT v4 D8: a purged 70/30 inner split of TRAIN), the window its label judges; its in-sample full-TRAIN figure is context only. The synthetic worlds share their noise for a given seed (only the planted drift differs), so calibrations of different worlds are independent only on disjoint seed ranges (`--seed-offset`).

- Ground truth: after every up-closing volume-spike candle the next 12 candles get extra positive drift (0.7 sigma per candle) over the WHOLE history; the drift is offset elsewhere, so buy-and-hold gains nothing from it.
- What the ground truth predicts: the base rules (which require a volume spike) should show positive expectancy on TRAIN and TEST; ROBUST is the correct label when the TEST sample is large enough.
- **Costs used in every backtest:** fee 0.100% of notional per side (the default: Binance spot taker, no discounts) (`--fee-rate 0.001`), charged on the entry AND on the exit; slippage 0.05% (`--slippage-pct 0.05`) against the trade on market fills (entries and stop exits; take-profit limit exits get none); exchange `binance` (`--exchange-id`; `EXCHANGE:binance` news events block every pair). Starting capital 10,000 per window.
- Cost-aware sizing and targets (CONTRACT.md v2 A1): the planned risk is the ALL-IN loss at the stop (the stop fill after slippage plus both fees), so a clean stop is exactly -1R and the target is placed so that a take-profit nets exactly +2R after fees; the target's price distance is therefore more than 2x the stop distance. Only a gap through the stop loses more than 1R under the touch fill model.
- Coinbase Advanced Trade taker fees at low volume tiers are several times Binance's, so a Coinbase run must pass the account's real tier with `--fee-rate` (a fraction of notional per side: 0.006 = 0.6%) and `--exchange-id coinbase`; the default costs would understate them.

## Label frequencies

| walk-forward label (TRAIN + TEST) | baseline | discovery-selected | ML layer | expectancy guard |
|---|---:|---:|---:|---:|
| ROBUST | 8/20 | 9/20 | 7/20 | 8/20 |
| TRAIN-ONLY | 0/20 | 0/20 | 0/20 | 0/20 |
| UNTESTED | 12/20 | 11/20 | 13/20 | 12/20 |
| NO-EDGE | 0/20 | 0/20 | 0/20 | 0/20 |

## Ground truth vs labels (rates with 90% Wilson intervals)

- Verdict ROBUST (any of the 4 pre-registered candidates, the family-wise rate) = DETECTION rate (a real edge exists in TRAIN and TEST): 10/20 = 50% (90% Wilson CI 33%-67%).
- baseline labelled ROBUST = DETECTION rate (a real edge exists in TRAIN and TEST): 8/20 = 40% (90% Wilson CI 24%-58%).
- discovery-selected labelled ROBUST = DETECTION rate (a real edge exists in TRAIN and TEST): 9/20 = 45% (90% Wilson CI 28%-63%).
- ML layer labelled ROBUST = DETECTION rate (a real edge exists in TRAIN and TEST): 7/20 = 35% (90% Wilson CI 20%-53%).
- expectancy guard labelled ROBUST = DETECTION rate (a real edge exists in TRAIN and TEST): 8/20 = 40% (90% Wilson CI 24%-58%).
- ML layer (`base+ml`) ran in 20/20 seeds. Mean avg R, base -> layer: TRAIN +0.442 -> +0.448 (the D8 out-of-sample gate; in-sample full TRAIN, context only: +0.483) | TEST +0.445 -> +0.435; its TEST avg R beat the base in 7/20 = 35% (90% Wilson CI 20%-53%) of those seeds.
  Per seed, mean counts TRAIN | TEST: signals vetoed 11.1 | 5.2; entries at reduced risk 0.0 | 0.0; base trades absent from the layer journal 10.3 | 4.8; layer trades absent from the base journal 5.2 | 2.2.
- Expectancy guard (`base+guard`) ran in 20/20 seeds. Mean avg R, base -> layer: TRAIN +0.442 -> +0.441 | TEST +0.445 -> +0.445; its TEST avg R beat the base in 0/20 = 0% (90% Wilson CI 0%-12%) of those seeds.
  Per seed, mean counts TRAIN | TEST: signals vetoed 0.0 | 0.0; entries at reduced risk 2.1 | 0.1; base trades absent from the layer journal 0.0 | 0.0; layer trades absent from the base journal 0.2 | 0.0.
- ML coefficients (standardised, fitted on TRAIN only): the largest-magnitude coefficient was an hour term (hour_sin/hour_cos) in 1/20 fitted seeds; mean hour-term magnitude sqrt(hour_sin^2 + hour_cos^2) = 0.176 log-odds per TRAIN s.d.
- Invariant violations over all backtests of all seeds: 0.

Drawdown (CONTRACT.md v3 C5, cap revised by v4 D7): every result reports the realised (closed-trade) and the mark-to-market (open positions valued at each 4H close) max drawdown; dd_ok = TEST mark-to-market max drawdown <= min(15%, the 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length, in percent of equity at the risk each trade took). Why the cap: the 15% cap is about 15 consecutive full-size losses at the mandated 1% cluster risk budget; at the 2:1 break-even win probability p* = 1/3 such a streak has probability (2/3)^15 = 0.23%, so a TEST drawdown beyond it is inconsistent with even a break-even strategy at the mandated risk (the TRAIN-bootstrap p95 usually binds first; the 3% weekly-loss halt limits how fast a drawdown accrues, not how deep it goes).

| candidate | seeds that reached the TEST gate (TRAIN avg R > 0, TRAIN n >= 30, TEST n >= 30) | ROBUST, all seeds | ROBUST given the TEST gate was reached | mean TRAIN avg R | mean TEST avg R | mean TRAIN n | mean TEST n | mean TEST max DD % realised / MTM / limit | dd_ok (TEST MTM within limit) |
|---|---|---|---|---:|---:|---:|---:|---|---|
| baseline | 20/20 = 100% (90% Wilson CI 88%-100%) | 8/20 = 40% (90% Wilson CI 24%-58%) | 8/20 = 40% (90% Wilson CI 24%-58%) | +0.442 | +0.445 | 114.8 | 49.6 | 4.68 / 5.37 / 8.60 | 20/20 = 100% (90% Wilson CI 88%-100%) |
| discovery-selected | 18/20 = 90% (90% Wilson CI 74%-97%) | 9/20 = 45% (90% Wilson CI 28%-63%) | 9/18 = 50% (90% Wilson CI 32%-68%) | +0.682 | +0.553 | 89.2 | 38.5 | 4.13 / 4.91 / 6.57 | 17/20 = 85% (90% Wilson CI 68%-94%) |
| ML layer | 19/20 = 95% (90% Wilson CI 80%-99%) | 7/20 = 35% (90% Wilson CI 20%-53%) | 7/19 = 37% (90% Wilson CI 21%-56%) | +0.448 | +0.435 | 34.4 | 47.0 | 4.51 / 5.31 / 8.77 | 17/20 = 85% (90% Wilson CI 68%-94%) |
| expectancy guard | 20/20 = 100% (90% Wilson CI 88%-100%) | 8/20 = 40% (90% Wilson CI 24%-58%) | 8/20 = 40% (90% Wilson CI 24%-58%) | +0.441 | +0.445 | 115.0 | 49.6 | 4.68 / 5.37 / 8.64 | 20/20 = 100% (90% Wilson CI 88%-100%) |

## Per seed (TRAIN and TEST side by side)

| seed | base TRAIN n | base TRAIN avg R | base TEST n | base TEST avg R | base TEST adj LB | base label | selected | selected TRAIN n | selected TRAIN avg R | selected TEST n | selected TEST avg R | selected label | ML TRAIN n | ML TRAIN avg R (out-of-sample gate) | ML TEST n | ML TEST avg R | ML label | guard TRAIN n | guard TRAIN avg R | guard TEST n | guard TEST avg R | guard label | verdict ROBUST | violations |
|---:|---:|---:|---:|---:|---:|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---|---|---:|
| 1 | 115 | +0.296 | 51 | +0.412 | -0.125 | UNTESTED | `rr3_vol2_rsi50-70` | 73 | +0.836 | 35 | +0.829 | ROBUST | 18 | +0.278 | 34 | +0.235 | UNTESTED | 115 | +0.296 | 51 | +0.412 | UNTESTED | yes | 0 |
| 2 | 113 | +0.327 | 49 | +0.347 | -0.200 | UNTESTED | `rr2.5_vol1.5_rsi55-70` | 93 | +0.731 | 40 | +0.400 | UNTESTED | 33 | +0.273 | 47 | +0.468 | UNTESTED | 114 | +0.316 | 49 | +0.347 | UNTESTED | no | 0 |
| 3 | 114 | +0.430 | 59 | +0.545 | +0.075 | ROBUST | `rr3_vol2_rsi50-70` | 70 | +0.957 | 38 | +0.715 | ROBUST | 36 | +0.278 | 56 | +0.467 | UNTESTED | 114 | +0.430 | 59 | +0.545 | ROBUST | yes | 0 |
| 4 | 106 | +0.472 | 36 | +0.667 | +0.059 | ROBUST | `rr2_vol2_rsi55-70` | 81 | +0.593 | 27 | +0.778 | UNTESTED | 37 | +0.378 | 36 | +0.667 | ROBUST | 106 | +0.472 | 36 | +0.667 | ROBUST | yes | 0 |
| 5 | 128 | +0.406 | 45 | +0.510 | -0.053 | UNTESTED | `rr2_vol2_rsi50-70` | 102 | +0.824 | 36 | +0.638 | UNTESTED | 33 | +0.455 | 41 | +0.511 | UNTESTED | 128 | +0.406 | 45 | +0.510 | UNTESTED | no | 0 |
| 6 | 126 | +0.452 | 41 | +0.354 | -0.228 | UNTESTED | `rr2_vol2_rsi50-70` | 97 | +0.639 | 35 | +0.329 | UNTESTED | 30 | +0.500 | 40 | +0.313 | UNTESTED | 126 | +0.452 | 41 | +0.354 | UNTESTED | no | 0 |
| 7 | 115 | +0.608 | 60 | +0.358 | -0.092 | UNTESTED | `rr2_vol2_rsi55-70` | 89 | +0.875 | 43 | +0.616 | ROBUST | 42 | +0.474 | 60 | +0.358 | UNTESTED | 115 | +0.608 | 60 | +0.358 | UNTESTED | yes | 0 |
| 8 | 107 | +0.458 | 50 | +0.588 | +0.136 | ROBUST | `rr2_vol1.5_rsi55-70` | 103 | +0.515 | 47 | +0.625 | ROBUST | 31 | +0.452 | 43 | +0.567 | ROBUST | 107 | +0.458 | 50 | +0.588 | ROBUST | yes | 0 |
| 9 | 122 | +0.551 | 51 | +0.647 | +0.176 | ROBUST | `rr2_vol1.5_rsi55-70` | 114 | +0.633 | 49 | +0.653 | ROBUST | 32 | +0.507 | 50 | +0.680 | ROBUST | 122 | +0.551 | 51 | +0.647 | ROBUST | yes | 0 |
| 10 | 113 | +0.513 | 41 | +0.171 | -0.471 | UNTESTED | `rr2_vol2_rsi50-70` | 89 | +0.652 | 27 | +0.444 | UNTESTED | 35 | +0.714 | 39 | +0.154 | UNTESTED | 113 | +0.513 | 41 | +0.171 | UNTESTED | no | 0 |
| 11 | 107 | +0.458 | 59 | +0.299 | -0.196 | UNTESTED | `rr2_vol2_rsi55-70` | 78 | +0.885 | 40 | +0.573 | UNTESTED | 30 | +0.400 | 56 | +0.369 | UNTESTED | 107 | +0.458 | 59 | +0.299 | UNTESTED | no | 0 |
| 12 | 149 | +0.454 | 60 | +0.250 | -0.150 | UNTESTED | `rr2.5_vol2_rsi50-70` | 108 | +0.750 | 39 | +0.511 | UNTESTED | 49 | +0.420 | 57 | +0.263 | UNTESTED | 149 | +0.454 | 60 | +0.250 | UNTESTED | no | 0 |
| 13 | 96 | +0.594 | 63 | +0.442 | +0.026 | ROBUST | `rr2_vol2_rsi55-70` | 75 | +0.760 | 48 | +0.581 | ROBUST | 31 | +1.032 | 63 | +0.442 | ROBUST | 96 | +0.594 | 63 | +0.442 | ROBUST | yes | 0 |
| 14 | 105 | +0.371 | 54 | +0.778 | +0.333 | ROBUST | `rr2_vol2_rsi50-70` | 87 | +0.552 | 43 | +0.744 | ROBUST | 30 | +0.700 | 52 | +0.731 | ROBUST | 105 | +0.371 | 54 | +0.778 | ROBUST | yes | 0 |
| 15 | 118 | +0.475 | 43 | +0.884 | +0.258 | ROBUST | `rr2_vol2_rsi50-70` | 95 | +0.674 | 35 | +0.800 | ROBUST | 41 | +0.244 | 41 | +0.902 | ROBUST | 118 | +0.475 | 43 | +0.884 | ROBUST | yes | 0 |
| 16 | 111 | +0.351 | 49 | +0.469 | -0.077 | UNTESTED | `rr2.5_vol1.5_rsi55-70` | 85 | +0.482 | 41 | +0.537 | UNTESTED | 37 | +0.297 | 46 | +0.435 | UNTESTED | 111 | +0.351 | 49 | +0.469 | UNTESTED | no | 0 |
| 17 | 115 | +0.461 | 42 | +0.214 | -0.438 | UNTESTED | `rr2_vol2_rsi50-70` | 95 | +0.623 | 36 | +0.250 | UNTESTED | 44 | +0.705 | 42 | +0.214 | UNTESTED | 115 | +0.461 | 42 | +0.214 | UNTESTED | no | 0 |
| 18 | 115 | +0.374 | 59 | +0.576 | +0.119 | ROBUST | `rr2_vol2_rsi50-70` | 83 | +0.614 | 43 | +0.535 | ROBUST | 30 | +0.567 | 56 | +0.554 | ROBUST | 115 | +0.374 | 59 | +0.576 | ROBUST | yes | 0 |
| 19 | 110 | +0.527 | 45 | +0.141 | -0.342 | UNTESTED | `rr2_vol2_rsi50-70` | 83 | +0.708 | 38 | +0.105 | UNTESTED | 35 | +0.200 | 41 | +0.179 | UNTESTED | 110 | +0.527 | 45 | +0.141 | UNTESTED | no | 0 |
| 20 | 112 | +0.259 | 36 | +0.250 | -0.364 | UNTESTED | `rr2_vol2_rsi55-70` | 83 | +0.337 | 30 | +0.400 | UNTESTED | 33 | +0.091 | 40 | +0.200 | UNTESTED | 115 | +0.252 | 36 | +0.250 | UNTESTED | no | 0 |

## Adoption path

The only path to real money (enforced by `adoption.py`; no step can be skipped): BACKTEST -> WALK_FORWARD (ROBUST + drawdown check) -> HUMAN_REVIEW of EVERY trade -> >= 14 days on Binance testnet with zero rule violations -> LIVE. A synthetic result never gets past WALK_FORWARD: `adoption check` blocks every record whose provenance is `synthetic:<world>:<seed>` with `ADOPT_provenance`. A calibration writes no adoption records; it measures how often the labels are right when the truth is known.

## Risk disclaimer

This is a research and testing tool, not financial advice and not a recommendation to trade. Backtests and synthetic worlds are simplified models; past or simulated results do not predict future results. Crypto trading can result in the total loss of the capital used.
