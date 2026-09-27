# Calibration: synthetic world `hour_edge`, seeds 1-20

This report does NOT promise, target or optimise a win rate, and nothing in this package selects on one. A win rate is meaningless without the reward:risk it was earned at: with the mandatory minimum of 2:1 a strategy breaks even at about 33% winners before costs, while a 90% win rate with a 1:9 payoff still loses money. A backtest win rate near 90% is evidence of curve-fitting or look-ahead, not of skill. The target metric is POSITIVE EXPECTANCY (average R per trade, net of fees and slippage) that survives a 70/30 chronological walk-forward with controlled drawdown, with every trade planned at reward:risk >= 2:1. Win rate appears below only as context and is labelled 'context only' wherever it is shown.

Each seed is a full run of the pipeline (6 years of 4H candles, 70/30 walk-forward): baseline, discovery (K = 13 variants, one selected on TRAIN), the ML layer and the expectancy guard, i.e. m = 4 pre-registered TEST looks per seed. **Synthetic results are verification of the methodology, not evidence about real markets.** Each label below combines that candidate's TRAIN and TEST windows (`metrics.label`). ROBUST requires at least 30 trades in each window, TRAIN and TEST avg R > 0, and a one-sided 98.75% lower bound of the TEST mean above zero for BOTH an iid and a calendar-month block bootstrap (the more conservative is used): alpha 0.05 is split over the m = 4 pre-registered candidates (base, discovery-selected, base+ml, base+guard), so when none has an edge the chance that ANY is called ROBUST is at most about 5%; a positive TEST mean that fails only the bound is UNTESTED (positive but not distinguishable from zero after multiplicity correction); the rule was fixed in advance and is never tuned on TEST outcomes. The ML layer's TRAIN columns are its out-of-sample TRAIN gate (CONTRACT v4 D8: a purged 70/30 inner split of TRAIN), the window its label judges; its in-sample full-TRAIN figure is context only. The synthetic worlds share their noise for a given seed (only the planted drift differs), so calibrations of different worlds are independent only on disjoint seed ranges (`--seed-offset`).

- Ground truth: the planted effect exists only for spike candles closing 12:00-20:00 UTC; spikes closing at other hours trigger nothing.
- What the ground truth predicts: the hour-blind base is diluted (weak or no edge); an hour-aware filter fitted on TRAIN has something real to learn and should raise TEST expectancy versus the base.
- **Costs used in every backtest:** fee 0.100% of notional per side (the default: Binance spot taker, no discounts) (`--fee-rate 0.001`), charged on the entry AND on the exit; slippage 0.05% (`--slippage-pct 0.05`) against the trade on market fills (entries and stop exits; take-profit limit exits get none); exchange `binance` (`--exchange-id`; `EXCHANGE:binance` news events block every pair). Starting capital 10,000 per window.
- Cost-aware sizing and targets (CONTRACT.md v2 A1): the planned risk is the ALL-IN loss at the stop (the stop fill after slippage plus both fees), so a clean stop is exactly -1R and the target is placed so that a take-profit nets exactly +2R after fees; the target's price distance is therefore more than 2x the stop distance. Only a gap through the stop loses more than 1R under the touch fill model.
- Coinbase Advanced Trade taker fees at low volume tiers are several times Binance's, so a Coinbase run must pass the account's real tier with `--fee-rate` (a fraction of notional per side: 0.006 = 0.6%) and `--exchange-id coinbase`; the default costs would understate them.

## Label frequencies

| walk-forward label (TRAIN + TEST) | baseline | discovery-selected | ML layer | expectancy guard |
|---|---:|---:|---:|---:|
| ROBUST | 0/20 | 3/20 | 3/20 | 0/20 |
| TRAIN-ONLY | 1/20 | 1/20 | 0/20 | 1/20 |
| UNTESTED | 17/20 | 16/20 | 17/20 | 18/20 |
| NO-EDGE | 2/20 | 0/20 | 0/20 | 1/20 |

## Ground truth vs labels (rates with 90% Wilson intervals)

- Verdict ROBUST (any of the 4 pre-registered candidates, the family-wise rate) (a real but hour-specific edge exists): 5/20 = 25% (90% Wilson CI 13%-43%).
- baseline labelled ROBUST (a real but hour-specific edge exists): 0/20 = 0% (90% Wilson CI 0%-12%).
- discovery-selected labelled ROBUST (a real but hour-specific edge exists): 3/20 = 15% (90% Wilson CI 6%-32%).
- ML layer labelled ROBUST (a real but hour-specific edge exists): 3/20 = 15% (90% Wilson CI 6%-32%).
- expectancy guard labelled ROBUST (a real but hour-specific edge exists): 0/20 = 0% (90% Wilson CI 0%-12%).
- ML layer (`base+ml`) ran in 20/20 seeds. Mean avg R, base -> layer: TRAIN +0.198 -> +0.400 (the D8 out-of-sample gate; in-sample full TRAIN, context only: +0.420) | TEST +0.201 -> +0.399; its TEST avg R beat the base in 18/20 = 90% (90% Wilson CI 74%-97%) of those seeds.
  Per seed, mean counts TRAIN | TEST: signals vetoed 53.9 | 23.2; entries at reduced risk 0.0 | 0.0; base trades absent from the layer journal 43.1 | 18.5; layer trades absent from the base journal 21.2 | 8.8.
- Expectancy guard (`base+guard`) ran in 20/20 seeds. Mean avg R, base -> layer: TRAIN +0.198 -> +0.207 | TEST +0.201 -> +0.203; its TEST avg R beat the base in 1/20 = 5% (90% Wilson CI 1%-20%) of those seeds.
  Per seed, mean counts TRAIN | TEST: signals vetoed 0.0 | 0.0; entries at reduced risk 13.1 | 0.8; base trades absent from the layer journal 0.6 | 0.1; layer trades absent from the base journal 2.5 | 0.1.
- ML coefficients (standardised, fitted on TRAIN only): the largest-magnitude coefficient was an hour term (hour_sin/hour_cos) in 12/20 fitted seeds; mean hour-term magnitude sqrt(hour_sin^2 + hour_cos^2) = 0.478 log-odds per TRAIN s.d.
- Invariant violations over all backtests of all seeds: 0.

Drawdown (CONTRACT.md v3 C5, cap revised by v4 D7): every result reports the realised (closed-trade) and the mark-to-market (open positions valued at each 4H close) max drawdown; dd_ok = TEST mark-to-market max drawdown <= min(15%, the 95th percentile of the max drawdown of TRAIN trade sequences bootstrapped at the TEST length, in percent of equity at the risk each trade took). Why the cap: the 15% cap is about 15 consecutive full-size losses at the mandated 1% cluster risk budget; at the 2:1 break-even win probability p* = 1/3 such a streak has probability (2/3)^15 = 0.23%, so a TEST drawdown beyond it is inconsistent with even a break-even strategy at the mandated risk (the TRAIN-bootstrap p95 usually binds first; the 3% weekly-loss halt limits how fast a drawdown accrues, not how deep it goes).

| candidate | seeds that reached the TEST gate (TRAIN avg R > 0, TRAIN n >= 30, TEST n >= 30) | ROBUST, all seeds | ROBUST given the TEST gate was reached | mean TRAIN avg R | mean TEST avg R | mean TRAIN n | mean TEST n | mean TEST max DD % realised / MTM / limit | dd_ok (TEST MTM within limit) |
|---|---|---|---|---:|---:|---:|---:|---|---|
| baseline | 18/20 = 90% (90% Wilson CI 74%-97%) | 0/20 = 0% (90% Wilson CI 0%-12%) | 0/18 = 0% (90% Wilson CI 0%-13%) | +0.198 | +0.201 | 115.0 | 50.5 | 6.81 / 7.32 / 11.89 | 18/20 = 90% (90% Wilson CI 74%-97%) |
| discovery-selected | 16/20 = 80% (90% Wilson CI 62%-91%) | 3/20 = 15% (90% Wilson CI 6%-32%) | 3/16 = 19% (90% Wilson CI 8%-39%) | +0.480 | +0.382 | 84.8 | 37.9 | 5.26 / 6.11 / 8.94 | 19/20 = 95% (90% Wilson CI 80%-99%) |
| ML layer | 7/20 = 35% (90% Wilson CI 20%-53%) | 3/20 = 15% (90% Wilson CI 6%-32%) | 3/7 = 43% (90% Wilson CI 19%-71%) | +0.400 | +0.399 | 28.6 | 40.8 | 4.28 / 5.15 / 9.07 | 17/20 = 85% (90% Wilson CI 68%-94%) |
| expectancy guard | 19/20 = 95% (90% Wilson CI 80%-99%) | 0/20 = 0% (90% Wilson CI 0%-12%) | 0/19 = 0% (90% Wilson CI 0%-12%) | +0.207 | +0.203 | 117.0 | 50.5 | 6.81 / 7.32 / 11.49 | 18/20 = 90% (90% Wilson CI 74%-97%) |

## Per seed (TRAIN and TEST side by side)

| seed | base TRAIN n | base TRAIN avg R | base TEST n | base TEST avg R | base TEST adj LB | base label | selected | selected TRAIN n | selected TRAIN avg R | selected TEST n | selected TEST avg R | selected label | ML TRAIN n | ML TRAIN avg R (out-of-sample gate) | ML TEST n | ML TEST avg R | ML label | guard TRAIN n | guard TRAIN avg R | guard TEST n | guard TEST avg R | guard label | verdict ROBUST | violations |
|---:|---:|---:|---:|---:|---:|---|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---|---|---:|
| 1 | 100 | -0.044 | 60 | +0.050 | -0.350 | NO-EDGE | `rr3_vol2_rsi50-70` | 72 | +0.203 | 39 | +0.436 | UNTESTED | 26 | +0.371 | 44 | +0.295 | UNTESTED | 111 | -0.057 | 61 | +0.082 | NO-EDGE | no | 0 |
| 2 | 114 | +0.263 | 61 | -0.016 | -0.429 | TRAIN-ONLY | `rr2.5_vol2_rsi50-70` | 78 | +0.526 | 44 | +0.034 | UNTESTED | 30 | +0.500 | 43 | +0.046 | UNTESTED | 115 | +0.252 | 61 | -0.016 | TRAIN-ONLY | no | 0 |
| 3 | 117 | +0.244 | 46 | +0.097 | -0.360 | UNTESTED | `rr2_vol2_rsi55-70` | 102 | +0.338 | 33 | -0.115 | TRAIN-ONLY | 28 | +0.554 | 43 | +0.167 | UNTESTED | 118 | +0.259 | 46 | +0.097 | UNTESTED | no | 0 |
| 4 | 115 | -0.009 | 52 | +0.269 | -0.222 | NO-EDGE | `rr2.5_vol2_rsi50-70` | 81 | +0.340 | 38 | +0.658 | ROBUST | 27 | +0.222 | 36 | +0.833 | UNTESTED | 125 | +0.080 | 52 | +0.269 | UNTESTED | yes | 0 |
| 5 | 123 | +0.244 | 45 | +0.400 | -0.122 | UNTESTED | `rr2.5_vol2_rsi55-70` | 82 | +0.793 | 34 | +0.288 | UNTESTED | 24 | +0.625 | 43 | +0.465 | UNTESTED | 123 | +0.244 | 45 | +0.400 | UNTESTED | no | 0 |
| 6 | 102 | +0.088 | 46 | +0.239 | -0.265 | UNTESTED | `rr2_vol2_rsi55-70` | 86 | +0.326 | 32 | +0.500 | UNTESTED | 33 | +0.182 | 38 | +0.579 | ROBUST | 105 | +0.114 | 46 | +0.239 | UNTESTED | yes | 0 |
| 7 | 109 | +0.257 | 54 | +0.077 | -0.347 | UNTESTED | `rr3_vol1.5_rsi55-70` | 90 | +0.445 | 48 | +0.337 | UNTESTED | 21 | +0.240 | 41 | +0.317 | UNTESTED | 110 | +0.273 | 54 | +0.077 | UNTESTED | no | 0 |
| 8 | 110 | +0.234 | 55 | +0.174 | -0.262 | UNTESTED | `rr2.5_vol2_rsi50-70` | 80 | +0.531 | 38 | +0.424 | UNTESTED | 33 | +0.386 | 54 | +0.196 | UNTESTED | 113 | +0.228 | 55 | +0.174 | UNTESTED | no | 0 |
| 9 | 132 | +0.182 | 40 | +0.275 | -0.432 | UNTESTED | `rr3_vol2_rsi55-70` | 74 | +0.730 | 29 | +0.793 | UNTESTED | 31 | +0.453 | 37 | +0.297 | UNTESTED | 130 | +0.200 | 40 | +0.275 | UNTESTED | no | 0 |
| 10 | 104 | +0.327 | 49 | +0.286 | -0.204 | UNTESTED | `rr2.5_vol2_rsi55-70` | 72 | +0.639 | 29 | +0.328 | UNTESTED | 27 | +1.444 | 45 | +0.267 | UNTESTED | 104 | +0.327 | 49 | +0.286 | UNTESTED | no | 0 |
| 11 | 107 | +0.121 | 60 | +0.050 | -0.389 | UNTESTED | `rr2.5_vol2_rsi55-70` | 80 | +0.487 | 41 | +0.414 | UNTESTED | 26 | +0.385 | 47 | +0.255 | UNTESTED | 114 | +0.158 | 60 | +0.050 | UNTESTED | no | 0 |
| 12 | 133 | +0.466 | 48 | +0.092 | -0.409 | UNTESTED | `rr2_vol2_rsi50-70` | 108 | +0.611 | 42 | +0.462 | UNTESTED | 34 | +0.676 | 49 | +0.192 | UNTESTED | 133 | +0.466 | 48 | +0.092 | UNTESTED | no | 0 |
| 13 | 100 | +0.320 | 60 | +0.406 | +0.000 | UNTESTED | `rr2_vol2_rsi55-70` | 72 | +0.500 | 43 | +0.557 | ROBUST | 24 | +0.250 | 45 | +0.754 | UNTESTED | 100 | +0.320 | 60 | +0.406 | UNTESTED | yes | 0 |
| 14 | 117 | +0.256 | 44 | +0.500 | -0.045 | UNTESTED | `rr2_vol2_rsi55-70` | 85 | +0.553 | 35 | +0.714 | ROBUST | 38 | +0.263 | 38 | +0.737 | ROBUST | 117 | +0.256 | 44 | +0.500 | UNTESTED | yes | 0 |
| 15 | 128 | +0.336 | 37 | +0.135 | -0.351 | UNTESTED | `rr2_vol2_rsi50-70` | 104 | +0.471 | 29 | -0.069 | UNTESTED | 35 | +0.457 | 29 | +0.345 | UNTESTED | 128 | +0.336 | 37 | +0.135 | UNTESTED | no | 0 |
| 16 | 114 | +0.210 | 47 | +0.213 | -0.289 | UNTESTED | `rr2_vol2_rsi50-70` | 99 | +0.333 | 45 | +0.267 | UNTESTED | 32 | +0.125 | 30 | +0.700 | ROBUST | 114 | +0.210 | 47 | +0.213 | UNTESTED | yes | 0 |
| 17 | 121 | +0.141 | 41 | +0.244 | -0.351 | UNTESTED | `rr3_vol2_rsi55-70` | 65 | +0.638 | 24 | +1.000 | UNTESTED | 32 | -0.062 | 27 | +0.667 | UNTESTED | 121 | +0.141 | 41 | +0.244 | UNTESTED | no | 0 |
| 18 | 137 | +0.016 | 63 | +0.085 | -0.411 | UNTESTED | `rr2.5_vol1.5_rsi50-70` | 116 | +0.142 | 63 | +0.037 | UNTESTED | 25 | +0.485 | 45 | +0.467 | UNTESTED | 140 | +0.037 | 63 | +0.085 | UNTESTED | no | 0 |
| 19 | 104 | +0.246 | 48 | +0.312 | -0.235 | UNTESTED | `rr2.5_vol2_rsi55-70` | 77 | +0.463 | 40 | +0.487 | UNTESTED | 26 | -0.053 | 44 | +0.364 | UNTESTED | 104 | +0.246 | 48 | +0.312 | UNTESTED | no | 0 |
| 20 | 114 | +0.053 | 53 | +0.132 | -0.382 | UNTESTED | `rr2.5_vol2_rsi55-70` | 73 | +0.534 | 32 | +0.094 | UNTESTED | 20 | +0.500 | 38 | +0.026 | UNTESTED | 115 | +0.043 | 53 | +0.132 | UNTESTED | no | 0 |

## Adoption path

The only path to real money (enforced by `adoption.py`; no step can be skipped): BACKTEST -> WALK_FORWARD (ROBUST + drawdown check) -> HUMAN_REVIEW of EVERY trade -> >= 14 days on Binance testnet with zero rule violations -> LIVE. A synthetic result never gets past WALK_FORWARD: `adoption check` blocks every record whose provenance is `synthetic:<world>:<seed>` with `ADOPT_provenance`. A calibration writes no adoption records; it measures how often the labels are right when the truth is known.

## Risk disclaimer

This is a research and testing tool, not financial advice and not a recommendation to trade. Backtests and synthetic worlds are simplified models; past or simulated results do not predict future results. Crypto trading can result in the total loss of the capital used.
