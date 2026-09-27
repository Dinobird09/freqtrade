# Walk-forward research report: synthetic world `decay` seed 1

## 1. No win rate is promised or targeted

This report does NOT promise, target or optimise a win rate, and nothing in this package selects on one. A win rate is meaningless without the reward:risk it was earned at: with the mandatory minimum of 2:1 a strategy breaks even at about 33% winners before costs, while a 90% win rate with a 1:9 payoff still loses money. A backtest win rate near 90% is evidence of curve-fitting or look-ahead, not of skill. The target metric is POSITIVE EXPECTANCY (average R per trade, net of fees and slippage) that survives a 70/30 chronological walk-forward with controlled drawdown, with every trade planned at reward:risk >= 2:1. Win rate appears below only as context and is labelled 'context only' wherever it is shown.

## 2. Data provenance

- Source: **SYNTHETIC** world `decay`, seed 1, 6 years of 4H candles (13140 per pair) from 2019-01-01T00:00:00Z, pairs BTC/USDT, ETH/USDT, BNB/USDT (`synthetic.generate_world`).
- Ground truth: the planted effect exists only BEFORE the 70% split; the TEST period is exactly the null world.
- What the ground truth predicts: a positive TRAIN expectancy that does not survive TEST: TRAIN-ONLY (or UNTESTED when the TEST noise is positive but indistinguishable from zero); ROBUST is a false positive.
- Qualifying triggers planted per pair (before / after the 70% split): BTC/USDT 341 / 0, ETH/USDT 333 / 0, BNB/USDT 317 / 0.
- **Synthetic results are verification of the methodology, not evidence about real markets.** No real BTC/ETH/BNB data was used in this run.
- News calendar loaded: yes (synthetic, 273 events: high macro 147, high regulatory 24, high unlock 6, medium bnb_burn 24, medium launchpool 72). The events have no price impact; they exercise R5 only.
- Walk-forward split: 2023-03-14T00:00:00Z = 70% of the common time range of all pairs (TRAIN = signal candles before it, TEST = at or after it, up to 2024-12-30T00:00:00Z). TEST starts with fresh equity and fresh circuit breakers; indicators warm up on earlier candles only.

| pair | candles | TRAIN candles (before the split) | TEST candles (from the split) | first candle (UTC) | last candle (UTC) | gaps |
|---|---:|---:|---:|---|---|---|
| BTC/USDT | 13140 | 9198 | 3942 | 2019-01-01T00:00:00Z | 2024-12-29T20:00:00Z | 0 |
| ETH/USDT | 13140 | 9198 | 3942 | 2019-01-01T00:00:00Z | 2024-12-29T20:00:00Z | 0 |
| BNB/USDT | 13140 | 9198 | 3942 | 2019-01-01T00:00:00Z | 2024-12-29T20:00:00Z | 0 |

- **Costs used in every backtest:** fee 0.100% of notional per side (the default: Binance spot taker, no discounts) (`--fee-rate 0.001`), charged on the entry AND on the exit; slippage 0.05% (`--slippage-pct 0.05`) against the trade on market fills (entries and stop exits; take-profit limit exits get none); exchange `binance` (`--exchange-id`; `EXCHANGE:binance` news events block every pair). Starting capital 10,000 per window.
- Cost-aware sizing and targets (CONTRACT.md v2 A1): the planned risk is the ALL-IN loss at the stop (the stop fill after slippage plus both fees), so a clean stop is exactly -1R and the target is placed so that a take-profit nets exactly +2R after fees; the target's price distance is therefore more than 2x the stop distance. Only a gap through the stop loses more than 1R.
- Coinbase Advanced Trade taker fees at low volume tiers are several times Binance's, so a Coinbase run must pass the account's real tier with `--fee-rate` (a fraction of notional per side: 0.006 = 0.6%) and `--exchange-id coinbase`; the default costs would understate them.
- Measured on the baseline's TRAIN and TEST trades: fees alone cost 0.06R per trade on average (median stop distance 3.71% of the entry price), so the expectancy below is only as good as the fee rate above.

## 3. Baseline: TRAIN vs TEST

Default mandate config `rr2_vol1.5_rsi50-70` (variant `base`). TRAIN window start .. 2023-03-14T00:00:00Z; TEST window 2023-03-14T00:00:00Z .. 2024-12-30T00:00:00Z.

| metric | TRAIN | TEST |
|---|---:|---:|
| trades (n) | 114 | 56 |
| avg R (expectancy, the target metric) | +0.307 | -0.143 |
| 90% bootstrap CI of avg R | [+0.079, +0.527] | [-0.464, +0.179] |
| t-stat of avg R | +2.20 | -0.78 |
| total R | +34.96 | -8.00 |
| profit factor | 1.68 | 0.75 |
| max drawdown % | 8.19% | 12.84% |
| max drawdown R | 10.00 | 14.00 |
| win rate (context only, never a target) | 43.9% | 28.6% |
| exits SL / TP / END | 64 / 49 / 1 | 40 / 16 / 0 |
| avg hold (h) | 105.1 | 118.9 |

**Label: TRAIN-ONLY.** Train expectancy +0.307R (n=114) did not hold out of sample: test expectancy is -0.143R (n=56), which suggests curve-fitting.

Drawdown check (TEST max drawdown <= 20%): passed. Test max drawdown 12.84% (14.00R) is within the 20.00% limit.

## 4. Strategy discovery: TRAIN vs TEST

K = 13 variants tried: 12 legal tightenings (reward:risk {2, 2.5, 3} x volume multiple {1.5, 2} x RSI window {50-70, 55-70}) plus 1 explicit test-only variant with the R4 regime filter OFF, which is reported but can never be selected or adopted.

Selection rule: highest TRAIN t-stat among selectable variants with >= 30 TRAIN trades. rr3_vol2_rsi50-70 has the highest TRAIN t-stat (+3.13, avg +0.742R over 70 trades) among 12 eligible variants; chosen on TRAIN only, before any TEST backtest was run.

Order of operations actually run: TRAIN backtests: 13 variants, candles before the split only -> selection recorded: rr3_vol2_rsi50-70 -> TEST backtests: 13 variants, reported side by side only.

| variant | status | TRAIN n | TRAIN avg R | TRAIN 90% CI | TRAIN t | TEST n | TEST avg R | TEST 90% CI | TEST t | TEST max DD % | label |
|---|---|---:|---:|---|---:|---:|---:|---|---:|---:|---|
| `rr2_vol1.5_rsi50-70` | selectable | 114 | +0.307 | [+0.079, +0.527] | +2.20 | 56 | -0.143 | [-0.464, +0.179] | -0.78 | 12.84% | TRAIN-ONLY |
| `rr2_vol1.5_rsi55-70` | selectable | 103 | +0.330 | [+0.078, +0.573] | +2.24 | 57 | -0.105 | [-0.421, +0.211] | -0.57 | 12.86% | TRAIN-ONLY |
| `rr2_vol2_rsi50-70` | selectable | 92 | +0.456 | [+0.184, +0.706] | +2.92 | 51 | -0.176 | [-0.471, +0.176] | -0.93 | 11.61% | TRAIN-ONLY |
| `rr2_vol2_rsi55-70` | selectable | 84 | +0.416 | [+0.143, +0.690] | +2.55 | 52 | -0.135 | [-0.423, +0.212] | -0.71 | 10.66% | TRAIN-ONLY |
| `rr2.5_vol1.5_rsi50-70` | selectable | 103 | +0.378 | [+0.091, +0.665] | +2.25 | 48 | -0.198 | [-0.563, +0.167] | -0.92 | 14.59% | TRAIN-ONLY |
| `rr2.5_vol1.5_rsi55-70` | selectable | 92 | +0.391 | [+0.103, +0.695] | +2.19 | 53 | -0.142 | [-0.472, +0.255] | -0.68 | 14.23% | TRAIN-ONLY |
| `rr2.5_vol2_rsi50-70` | selectable | 85 | +0.547 | [+0.235, +0.858] | +2.90 | 49 | -0.143 | [-0.500, +0.214] | -0.66 | 12.38% | TRAIN-ONLY |
| `rr2.5_vol2_rsi55-70` | selectable | 77 | +0.480 | [+0.162, +0.779] | +2.44 | 51 | -0.108 | [-0.451, +0.235] | -0.50 | 11.53% | TRAIN-ONLY |
| `rr3_vol1.5_rsi50-70` | selectable | 90 | +0.355 | [+0.022, +0.688] | +1.78 | 48 | -0.083 | [-0.500, +0.333] | -0.34 | 11.20% | TRAIN-ONLY |
| `rr3_vol1.5_rsi55-70` | selectable | 85 | +0.294 | [-0.036, +0.646] | +1.45 | 54 | -0.037 | [-0.407, +0.333] | -0.16 | 10.41% | TRAIN-ONLY |
| `rr3_vol2_rsi50-70` | **SELECTED** | 70 | +0.742 | [+0.370, +1.141] | +3.13 | 46 | -0.043 | [-0.478, +0.391] | -0.17 | 9.40% | TRAIN-ONLY |
| `rr3_vol2_rsi55-70` | selectable | 65 | +0.630 | [+0.258, +1.031] | +2.59 | 48 | +0.000 | [-0.417, +0.417] | +0.00 | 8.08% | TRAIN-ONLY |
| `rr2_vol1.5_rsi50-70_regimeOFF` | TEST-ONLY (regime OFF, never selectable) | 154 | +0.213 | [+0.018, +0.419] | +1.80 | 64 | -0.156 | [-0.438, +0.125] | -0.92 | 14.63% | TRAIN-ONLY |

**Re-selecting a variant on these TEST numbers would turn TEST into TRAIN: the selection was fixed on TRAIN before any TEST backtest ran, and it is not revisited.**

The TRAIN column of the selected variant `rr3_vol2_rsi50-70` is biased upward by the selection itself (it is the best of 12); only its TEST column is an out-of-sample test. The labels of the other variants are context only and do not enter the verdict.

**Label: TRAIN-ONLY.** Train expectancy +0.742R (n=70) did not hold out of sample: test expectancy is -0.043R (n=46), which suggests curve-fitting.

Drawdown check (TEST max drawdown <= 20%): passed. Test max drawdown 9.40% (10.00R) is within the 20.00% limit.

## 5. ML filter layer: TRAIN (in-sample) vs TEST

Variant `base+ml`: the baseline rules plus the `L_ml_filter` layer, a logistic regression on 6 features (rsi, vol_ratio, ema_gap_pct, dist_regime_pct, hour_sin, hour_cos). It can only REMOVE trades that passed every mandatory rule. It is fitted ONLY on TRAIN candidates and applied unchanged to TEST, so its TRAIN numbers are IN-SAMPLE.

Logistic filter (l2=1) fitted on 257 TRAIN candidates (TRAIN base win rate 39.3%, context only) keeps signals whose predicted win probability exceeds the break-even 0.333.

- TRAIN candidates enumerated (purged: outcome resolved before the split): 257; used for the fit: 257 (0 skipped for missing features); TRAIN candidate win share 39.3% (context only).
- Threshold p* = break-even win probability from the TRAIN avg win +2.00R and avg loss -1.00R: p* = 0.333 (no threshold search).
- L2 penalty 1 (fixed, not tuned); intercept -0.488.
- Model fingerprint (sha256 of features, TRAIN means/stds, intercept, coefficients, threshold and l2): `3d4ae99353458efed55a39af109e9ecba66ddaf2646f29663ca5038661dd4aef`. It is pinned in the ML adoption record (section 11).

| feature | standardised coefficient, fitted on TRAIN only and applied unchanged to TEST (log-odds per TRAIN s.d.) |
|---|---:|
| `rsi` | +0.049 |
| `vol_ratio` | +0.303 |
| `ema_gap_pct` | -0.613 |
| `dist_regime_pct` | -0.189 |
| `hour_sin` | +0.131 |
| `hour_cos` | +0.230 |

| metric | base TRAIN | base+ml TRAIN (in-sample) | base TEST | base+ml TEST |
|---|---:|---:|---:|---:|
| trades (n) | 114 | 88 | 56 | 55 |
| avg R (expectancy, the target metric) | +0.307 | +0.590 | -0.143 | -0.073 |
| 90% bootstrap CI of avg R | [+0.079, +0.527] | [+0.330, +0.841] | [-0.464, +0.179] | [-0.345, +0.255] |
| t-stat of avg R | +2.20 | +3.70 | -0.78 | -0.39 |
| total R | +34.96 | +51.96 | -8.00 | -4.00 |
| profit factor | 1.68 | 2.22 | 0.75 | 0.80 |
| max drawdown % | 8.19% | 3.94% | 12.84% | 12.07% |
| max drawdown R | 10.00 | 4.00 | 14.00 | 12.00 |
| win rate (context only, never a target) | 43.9% | 53.4% | 28.6% | 30.9% |
| exits SL / TP / END | 64 / 49 / 1 | 41 / 46 / 1 | 40 / 16 / 0 | 38 / 17 / 0 |
| avg hold (h) | 105.1 | 107.8 | 118.9 | 98.3 |

On TEST the filter removed 26 signals that had passed every mandatory rule; TEST expectancy -0.143R (base) -> -0.073R (with the filter).

**Label: TRAIN-ONLY.** Train expectancy +0.590R (n=88) did not hold out of sample: test expectancy is -0.073R (n=55), which suggests curve-fitting.

Drawdown check (TEST max drawdown <= 20%): passed. Test max drawdown 12.07% (12.00R) is within the 20.00% limit.

LightGBM, other gradient boosting and neural networks are NOT justified at this sample size (257 TRAIN candidates, 6 features): a flexible model would memorise noise, so only a fixed-penalty logistic regression with a break-even threshold is allowed.

## 6. Verdict

Candidates judged (pre-registered, one TEST look each): baseline `base`, discovery-selected `rr3_vol2_rsi50-70`, ML layer `base+ml`. Other grid variants are excluded because choosing among them by TEST numbers would be selection on TEST. The verdict requires the ROBUST label (positive TRAIN and TEST expectancy with the TEST 90% CI above zero) and reports the drawdown check.

No robust result found.

## 7. Why signals were rejected (decision counts per rule)

Signal candles denied per rule (the FIRST failing rule is counted, so the R1-R4 rows include every candle that was simply not a signal). Exits are never gated.

| rule | base TRAIN | base TEST | selected rr3_vol2_rsi50-70 TRAIN | selected rr3_vol2_rsi50-70 TEST | base+ml TRAIN | base+ml TEST | meaning |
|---|---:|---:|---:|---:|---:|---:|---|
| `R1_trend` | 18588 | 7612 | 18588 | 7612 | 18588 | 7612 | Trend gate: close > EMA9 and close > EMA21 and EMA9 > EMA21 (4H) |
| `R2_momentum` | 3304 | 772 | 3304 | 772 | 3304 | 772 | Momentum: RSI(14) within [50, 70] on the entry candle |
| `R3_volume` | 5246 | 3125 | 5404 | 3243 | 5246 | 3125 | Volume: entry-candle volume >= 1.5x the previous-20-candle average |
| `R4_regime` | 186 | 97 | 120 | 56 | 186 | 97 | Regime: close > EMA200 (unless explicitly testing it off) |
| `R5_news_blackout` | 9 | 8 | 6 | 6 | 9 | 8 | No entries within +/-2h of high-impact news; BNB +/-24h burns/launchpool |
| `R6_correlation_cap` | 146 | 154 | 101 | 90 | 110 | 127 | BTC/ETH/BNB share one risk budget; BNB never stacks |
| `R7_position_risk` | 0 | 0 | 0 | 0 | 0 | 0 | Risk <= 1% (BTC/ETH) / 0.5% (BNB); size derived from stop distance |
| `R8_structure_stop` | 1 | 2 | 1 | 0 | 2 | 2 | Stop behind a confirmed swing low with a buffer (BNB 0.5-0.8%) |
| `R9_circuit_breaker` | 0 | 0 | 0 | 1 | 0 | 2 | 3 consecutive SLs bench a pair 24h; 7d realized loss limit halts all |
| `L_ml_filter` | 0 | 0 | 0 | 0 | 61 | 26 | Layer: logistic-regression filter (only removes trades, never adds) |
| `L_expectancy_guard` | 0 | 0 | 0 | 0 | 0 | 0 | Layer: halve a pair's risk while its last-N-trade expectancy < 0 |
| `X_capital` | 0 | 0 | 0 | 0 | 0 | 0 | Execution: not enough free capital / size below minimum |
| `ADOPT_record` | 0 | 0 | 0 | 0 | 0 | 0 | The adoption record names the variant it tracks |
| `ADOPT_fingerprint` | 0 | 0 | 0 | 0 | 0 | 0 | The config (and ML model, if any) promoted is exactly the one tested |
| `ADOPT_backtest` | 0 | 0 | 0 | 0 | 0 | 0 | Stage 1: a completed backtest with a report |
| `ADOPT_walk_forward` | 0 | 0 | 0 | 0 | 0 | 0 | Stage 2: 70/30 walk-forward labelled ROBUST, drawdown within limit |
| `ADOPT_human_review` | 0 | 0 | 0 | 0 | 0 | 0 | Stage 3: a named human reviewed EVERY trade and approved |
| `ADOPT_testnet` | 0 | 0 | 0 | 0 | 0 | 0 | Stage 4: >= 14 days on Binance testnet, zero rule violations, journal |
| `ADOPT_live` | 0 | 0 | 0 | 0 | 0 | 0 | Stage 5: live trading (never for an explicit test-only config) |
| candles evaluated | 27594 | 11826 | 27594 | 11826 | 27594 | 11826 | |
| trades taken | 114 | 56 | 70 | 46 | 88 | 55 | |

## 8. Invariant audit of every backtest

`invariants.check_invariants` independently re-derives every mandatory rule (R1-R9, 2:1 minimum, sizing from the stop, exits on the first touch, no data after the window) from each trade list and the candles.

| backtest | TRAIN violations | TEST violations |
|---|---:|---:|
| `base` | 0 | 0 |
| `rr2_vol1.5_rsi50-70` | 0 | 0 |
| `rr2_vol1.5_rsi55-70` | 0 | 0 |
| `rr2_vol2_rsi50-70` | 0 | 0 |
| `rr2_vol2_rsi55-70` | 0 | 0 |
| `rr2.5_vol1.5_rsi50-70` | 0 | 0 |
| `rr2.5_vol1.5_rsi55-70` | 0 | 0 |
| `rr2.5_vol2_rsi50-70` | 0 | 0 |
| `rr2.5_vol2_rsi55-70` | 0 | 0 |
| `rr3_vol1.5_rsi50-70` | 0 | 0 |
| `rr3_vol1.5_rsi55-70` | 0 | 0 |
| `rr3_vol2_rsi50-70` | 0 | 0 |
| `rr3_vol2_rsi55-70` | 0 | 0 |
| `rr2_vol1.5_rsi50-70_regimeOFF` | 0 | 0 |
| `base+ml` | 0 | 0 |

Result: CLEAN. 0 violations in 30 backtests.

## 9. Journal rules at the end of TRAIN and at the end of TEST

`journal_rules.audit` over each pre-registered candidate's TRAIN journal at the split and its TEST journal at 2024-12-30T00:00:00Z (the end of TEST), each with that window's final equity: every adaptation the live bot would be applying at that moment because of past trades, with the journal rows that caused it. Backtest journals record the OPEN of the exit candle, so each exit counts from exit_ts + 4h, when the exit is certain (`exit_time_uncertainty_ms = cfg.timeframe_ms`, CONTRACT.md v2 A2); a bench therefore lasts at least 24h of real time. A live journal records real fill times and uses 0.

**baseline `base`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 14,049.53) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 9,147.63) | - | - | none | No active adaptations. | - |

**discovery-selected `rr3_vol2_rsi50-70`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 15,961.45) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 9,701.34) | - | - | none | No active adaptations. | - |

**ML layer `base+ml`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 15,312.91) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 9,332.19) | - | - | none | No active adaptations. | - |

Reproduce the baseline TEST row with `python -m research.trendbot.journal_rules --journal research/results/synthetic_decay_s1/journals/base_test.csv --equity 9147.63 --now 2024-12-30T00:00:00Z --backtest-journal`.

## 10. Journals and human review packs

Trade journals (`journal.write_journal`, one CSV per window) for the three pre-registered candidates, the variants whose TRAIN and TEST columns this report shows in full. The other discovery variants appear only as one context row each in section 4, so no journal is kept for them (the same command regenerates them). TEST trade ids are offset past the TRAIN ids, so ids are unique across a variant's two journals and its review pack.

| variant | TRAIN journal | TEST journal |
|---|---|---|
| `base` | [journals/base_train.csv](journals/base_train.csv) | [journals/base_test.csv](journals/base_test.csv) |
| `rr3_vol2_rsi50-70` | [journals/rr3_vol2_rsi50-70_train.csv](journals/rr3_vol2_rsi50-70_train.csv) | [journals/rr3_vol2_rsi50-70_test.csv](journals/rr3_vol2_rsi50-70_test.csv) |
| `base+ml` | [journals/base_plus_ml_train.csv](journals/base_plus_ml_train.csv) | [journals/base_plus_ml_test.csv](journals/base_plus_ml_test.csv) |

Human review packs (`review_sheet.write_review_pack`, TRAIN and TEST):

- `base`: [review_base/trades_review.md](review_base/trades_review.md) and [review_base/trades_review.csv](review_base/trades_review.csv)
- `rr3_vol2_rsi50-70`: [review_selected_rr3_vol2_rsi50-70/trades_review.md](review_selected_rr3_vol2_rsi50-70/trades_review.md) and [review_selected_rr3_vol2_rsi50-70/trades_review.csv](review_selected_rr3_vol2_rsi50-70/trades_review.csv)
- `base+ml`: [review_base_plus_ml/trades_review.md](review_base_plus_ml/trades_review.md) and [review_base_plus_ml/trades_review.csv](review_base_plus_ml/trades_review.csv)

## 11. Adoption path and current stage

The only path to real money (enforced by `adoption.py`; no step can be skipped, the promoted config must match the tested config's sha256 fingerprint, and an ML layer's model must match the tested model's fingerprint):

backtest -> walk-forward (ROBUST + drawdown check) -> human review of EVERY trade -> >= 2 weeks on Binance testnet with zero rule violations -> live.

Stages: BACKTEST -> WALK_FORWARD -> HUMAN_REVIEW -> TESTNET -> LIVE. Stage reached by this run: **WALK_FORWARD** (recorded). Next: **HUMAN_REVIEW**.

**This run used SYNTHETIC data: its records only demonstrate the mechanism. A synthetic result can never justify adopting a variant; the path must be run on real candles from the backtest stage.**

| variant | walk-forward label (TRAIN + TEST) | TRAIN n | TEST n | TEST avg R | config sha256 (first 16) | ML model sha256 (first 16) | promotion to HUMAN_REVIEW |
|---|---|---:|---:|---:|---|---|---|
| `base` | TRAIN-ONLY | 114 | 56 | -0.143 | `9be9d6da6ad86d85` | `none (no ML layer)` | BLOCKED |
| `rr3_vol2_rsi50-70` | TRAIN-ONLY | 70 | 46 | -0.043 | `5cbe1d74d6aa0330` | `none (no ML layer)` | BLOCKED |
| `base+ml` | TRAIN-ONLY | 88 | 55 | -0.073 | `9be9d6da6ad86d85` | `3d4ae99353458efe` | BLOCKED |

- `base`: record [adoption_base.json](adoption_base.json); promotion to HUMAN_REVIEW is BLOCKED: [ADOPT_walk_forward] walk_forward.label is 'TRAIN-ONLY' (the edge did not hold out of sample, a sign of curve-fitting), and only ROBUST may proceed. [ADOPT_walk_forward] walk_forward.test_avg_r must be a positive out-of-sample expectancy in R (got -0.1428571428571436).
  Check: `python -m research.trendbot.adoption check --record research/results/synthetic_decay_s1/adoption_base.json --stage HUMAN_REVIEW`
- `rr3_vol2_rsi50-70`: record [adoption_rr3_vol2_rsi50-70.json](adoption_rr3_vol2_rsi50-70.json), [config_rr3_vol2_rsi50-70.json](config_rr3_vol2_rsi50-70.json); promotion to HUMAN_REVIEW is BLOCKED: [ADOPT_walk_forward] walk_forward.label is 'TRAIN-ONLY' (the edge did not hold out of sample, a sign of curve-fitting), and only ROBUST may proceed. [ADOPT_walk_forward] walk_forward.test_avg_r must be a positive out-of-sample expectancy in R (got -0.04347826086956564).
  Check: `python -m research.trendbot.adoption check --record research/results/synthetic_decay_s1/adoption_rr3_vol2_rsi50-70.json --stage HUMAN_REVIEW --config research/results/synthetic_decay_s1/config_rr3_vol2_rsi50-70.json`
- `base+ml`: record [adoption_base_plus_ml.json](adoption_base_plus_ml.json), [model_base_plus_ml.json](model_base_plus_ml.json); promotion to HUMAN_REVIEW is BLOCKED: [ADOPT_walk_forward] walk_forward.label is 'TRAIN-ONLY' (the edge did not hold out of sample, a sign of curve-fitting), and only ROBUST may proceed. [ADOPT_walk_forward] walk_forward.test_avg_r must be a positive out-of-sample expectancy in R (got -0.0727272727272737).
  Check: `python -m research.trendbot.adoption check --record research/results/synthetic_decay_s1/adoption_base_plus_ml.json --stage HUMAN_REVIEW --model-fingerprint 3d4ae99353458efed55a39af109e9ecba66ddaf2646f29663ca5038661dd4aef`

The ML layer `base+ml` is adopted as a (config, model) pair: its record pins the config fingerprint AND the fitted model's fingerprint `3d4ae99353458efed55a39af109e9ecba66ddaf2646f29663ca5038661dd4aef` (`MLFilter.fingerprint()`, the sha256 of [model_base_plus_ml.json](model_base_plus_ml.json)). **Refitting the model (new TRAIN data, a later split, a different l2) changes the fingerprint, so a refitted model is a NEW candidate and restarts the adoption path at BACKTEST.** `adoption check` blocks (`ADOPT_fingerprint`) a model whose fingerprint differs from the recorded one, and any `+ml` record without one; the live bot must pass the fingerprint of the model it actually runs.

A `config_<variant>.json` file (the `StrategyConfig` overrides versus the defaults, e.g. costs or discovery parameters) is written whenever the tested config is not the default one; the check needs it as `--config`. The regime-OFF variant is test-only and can never be adopted.

## 12. Risk disclaimer

This is a research and testing tool, not financial advice and not a recommendation to trade. Backtests and synthetic worlds are simplified models; past or simulated results do not predict future results. Crypto trading can result in the total loss of the capital used.
