# Walk-forward research report: synthetic world `hour_edge` seed 1

## 1. No win rate is promised or targeted

This report does NOT promise, target or optimise a win rate, and nothing in this package selects on one. A win rate is meaningless without the reward:risk it was earned at: with the mandatory minimum of 2:1 a strategy breaks even at about 33% winners before costs, while a 90% win rate with a 1:9 payoff still loses money. A backtest win rate near 90% is evidence of curve-fitting or look-ahead, not of skill. The target metric is POSITIVE EXPECTANCY (average R per trade, net of fees and slippage) that survives a 70/30 chronological walk-forward with controlled drawdown, with every trade planned at reward:risk >= 2:1. Win rate appears below only as context and is labelled 'context only' wherever it is shown.

## 2. Data provenance

- Source: **SYNTHETIC** world `hour_edge`, seed 1, 6 years of 4H candles (13140 per pair) from 2019-01-01T00:00:00Z, pairs BTC/USDT, ETH/USDT, BNB/USDT (`synthetic.generate_world`).
- Ground truth: the planted effect exists only for spike candles closing 12:00-20:00 UTC; spikes closing at other hours trigger nothing.
- What the ground truth predicts: the hour-blind base is diluted (weak or no edge); an hour-aware filter fitted on TRAIN has something real to learn and should raise TEST expectancy versus the base.
- Qualifying triggers planted per pair (before / after the 70% split): BTC/USDT 165 / 77, ETH/USDT 153 / 65, BNB/USDT 168 / 63.
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
- Measured on the baseline's TRAIN and TEST trades: fees alone cost 0.06R per trade on average (median stop distance 3.48% of the entry price), so the expectancy below is only as good as the fee rate above.

## 3. Baseline: TRAIN vs TEST

Default mandate config `rr2_vol1.5_rsi50-70` (variant `base`). TRAIN window start .. 2023-03-14T00:00:00Z; TEST window 2023-03-14T00:00:00Z .. 2024-12-30T00:00:00Z.

| metric | TRAIN | TEST |
|---|---:|---:|
| trades (n) | 100 | 60 |
| avg R (expectancy, the target metric) | -0.044 | +0.050 |
| 90% bootstrap CI of avg R | [-0.280, +0.193] | [-0.250, +0.350] |
| t-stat of avg R | -0.31 | +0.27 |
| total R | -4.36 | +3.00 |
| profit factor | 0.87 | 1.06 |
| max drawdown % | 14.97% | 9.60% |
| max drawdown R | 16.00 | 11.00 |
| win rate (context only, never a target) | 32.0% | 35.0% |
| exits SL / TP / END | 68 / 31 / 1 | 39 / 21 / 0 |
| avg hold (h) | 137.8 | 92.0 |

**Label: NO-EDGE.** Train expectancy is -0.044R over 100 trades, so there is no edge to validate.

Drawdown check (TEST max drawdown <= 20%): passed. Test max drawdown 9.60% (11.00R) is within the 20.00% limit.

## 4. Strategy discovery: TRAIN vs TEST

K = 13 variants tried: 12 legal tightenings (reward:risk {2, 2.5, 3} x volume multiple {1.5, 2} x RSI window {50-70, 55-70}) plus 1 explicit test-only variant with the R4 regime filter OFF, which is reported but can never be selected or adopted.

Selection rule: highest TRAIN t-stat among selectable variants with >= 30 TRAIN trades. rr3_vol2_rsi50-70 has the highest TRAIN t-stat (+0.94, avg +0.203R over 72 trades) among 12 eligible variants; chosen on TRAIN only, before any TEST backtest was run.

Order of operations actually run: TRAIN backtests: 13 variants, candles before the split only -> selection recorded: rr3_vol2_rsi50-70 -> TEST backtests: 13 variants, reported side by side only.

| variant | status | TRAIN n | TRAIN avg R | TRAIN 90% CI | TRAIN t | TEST n | TEST avg R | TEST 90% CI | TEST t | TEST max DD % | label |
|---|---|---:|---:|---|---:|---:|---:|---|---:|---:|---|
| `rr2_vol1.5_rsi50-70` | selectable | 100 | -0.044 | [-0.280, +0.193] | -0.31 | 60 | +0.050 | [-0.250, +0.350] | +0.27 | 9.60% | NO-EDGE |
| `rr2_vol1.5_rsi55-70` | selectable | 92 | +0.040 | [-0.193, +0.297] | +0.27 | 58 | +0.086 | [-0.224, +0.397] | +0.45 | 8.25% | UNTESTED |
| `rr2_vol2_rsi50-70` | selectable | 82 | +0.130 | [-0.126, +0.414] | +0.81 | 44 | +0.159 | [-0.182, +0.500] | +0.71 | 4.90% | UNTESTED |
| `rr2_vol2_rsi55-70` | selectable | 77 | +0.125 | [-0.143, +0.393] | +0.75 | 42 | +0.143 | [-0.214, +0.500] | +0.63 | 4.90% | UNTESTED |
| `rr2.5_vol1.5_rsi50-70` | selectable | 97 | +0.001 | [-0.251, +0.263] | +0.01 | 57 | +0.167 | [-0.202, +0.535] | +0.76 | 7.78% | UNTESTED |
| `rr2.5_vol1.5_rsi55-70` | selectable | 89 | +0.052 | [-0.223, +0.328] | +0.31 | 56 | +0.187 | [-0.188, +0.562] | +0.84 | 7.32% | UNTESTED |
| `rr2.5_vol2_rsi50-70` | selectable | 78 | +0.156 | [-0.147, +0.459] | +0.83 | 43 | +0.302 | [-0.105, +0.709] | +1.16 | 4.90% | UNTESTED |
| `rr2.5_vol2_rsi55-70` | selectable | 73 | +0.139 | [-0.149, +0.463] | +0.72 | 42 | +0.333 | [-0.083, +0.833] | +1.26 | 4.90% | UNTESTED |
| `rr3_vol1.5_rsi50-70` | selectable | 87 | -0.050 | [-0.326, +0.242] | -0.28 | 51 | +0.176 | [-0.216, +0.569] | +0.68 | 5.37% | NO-EDGE |
| `rr3_vol1.5_rsi55-70` | selectable | 80 | -0.017 | [-0.317, +0.300] | -0.09 | 50 | +0.200 | [-0.200, +0.680] | +0.76 | 5.37% | NO-EDGE |
| `rr3_vol2_rsi50-70` | **SELECTED** | 72 | +0.203 | [-0.149, +0.556] | +0.94 | 39 | +0.436 | [-0.077, +0.949] | +1.40 | 4.90% | UNTESTED |
| `rr3_vol2_rsi55-70` | selectable | 68 | +0.156 | [-0.196, +0.509] | +0.71 | 38 | +0.474 | [-0.053, +1.000] | +1.49 | 4.90% | UNTESTED |
| `rr2_vol1.5_rsi50-70_regimeOFF` | TEST-ONLY (regime OFF, never selectable) | 153 | +0.213 | [+0.017, +0.424] | +1.79 | 79 | +0.101 | [-0.165, +0.367] | +0.62 | 8.75% | UNTESTED |

**Re-selecting a variant on these TEST numbers would turn TEST into TRAIN: the selection was fixed on TRAIN before any TEST backtest ran, and it is not revisited.**

The TRAIN column of the selected variant `rr3_vol2_rsi50-70` is biased upward by the selection itself (it is the best of 12); only its TEST column is an out-of-sample test. The labels of the other variants are context only and do not enter the verdict.

**Label: UNTESTED.** Test expectancy is positive at +0.436R (n=39) but its 90% bootstrap CI [-0.077, +0.949]R includes zero, so it is not distinguishable from no edge.

Drawdown check (TEST max drawdown <= 20%): passed. Test max drawdown 4.90% (6.00R) is within the 20.00% limit.

## 5. ML filter layer: TRAIN (in-sample) vs TEST

Variant `base+ml`: the baseline rules plus the `L_ml_filter` layer, a logistic regression on 6 features (rsi, vol_ratio, ema_gap_pct, dist_regime_pct, hour_sin, hour_cos). It can only REMOVE trades that passed every mandatory rule. It is fitted ONLY on TRAIN candidates and applied unchanged to TEST, so its TRAIN numbers are IN-SAMPLE.

Logistic filter (l2=1) fitted on 249 TRAIN candidates (TRAIN base win rate 32.5%, context only) keeps signals whose predicted win probability exceeds the break-even 0.333.

- TRAIN candidates enumerated (purged: outcome resolved before the split): 249; used for the fit: 249 (0 skipped for missing features); TRAIN candidate win share 32.5% (context only).
- Threshold p* = break-even win probability from the TRAIN avg win +2.00R and avg loss -1.00R: p* = 0.333 (no threshold search).
- L2 penalty 1 (fixed, not tuned); intercept -0.768.
- Model fingerprint (sha256 of features, TRAIN means/stds, intercept, coefficients, threshold and l2): `b38d6af2d40d7fa65a3bd7386a4c4504718409b0770eaa6daac3b85a8ddf8b71`. It is pinned in the ML adoption record (section 11).

| feature | standardised coefficient, fitted on TRAIN only and applied unchanged to TEST (log-odds per TRAIN s.d.) |
|---|---:|
| `rsi` | +0.182 |
| `vol_ratio` | +0.196 |
| `ema_gap_pct` | -0.128 |
| `dist_regime_pct` | +0.151 |
| `hour_sin` | -0.413 |
| `hour_cos` | -0.079 |

| metric | base TRAIN | base+ml TRAIN (in-sample) | base TEST | base+ml TEST |
|---|---:|---:|---:|---:|
| trades (n) | 100 | 69 | 60 | 44 |
| avg R (expectancy, the target metric) | -0.044 | +0.299 | +0.050 | +0.295 |
| 90% bootstrap CI of avg R | [-0.280, +0.193] | [-0.005, +0.603] | [-0.250, +0.350] | [-0.045, +0.636] |
| t-stat of avg R | -0.31 | +1.66 | +0.27 | +1.30 |
| total R | -4.36 | +20.64 | +3.00 | +13.00 |
| profit factor | 0.87 | 1.52 | 1.06 | 1.49 |
| max drawdown % | 14.97% | 8.83% | 9.60% | 5.38% |
| max drawdown R | 16.00 | 9.00 | 11.00 | 6.00 |
| win rate (context only, never a target) | 32.0% | 43.5% | 35.0% | 43.2% |
| exits SL / TP / END | 68 / 31 / 1 | 39 / 29 / 1 | 39 / 21 / 0 | 25 / 19 / 0 |
| avg hold (h) | 137.8 | 103.7 | 92.0 | 87.9 |

On TEST the filter removed 30 signals that had passed every mandatory rule; TEST expectancy +0.050R (base) -> +0.295R (with the filter).

**Label: UNTESTED.** Test expectancy is positive at +0.295R (n=44) but its 90% bootstrap CI [-0.045, +0.636]R includes zero, so it is not distinguishable from no edge.

Drawdown check (TEST max drawdown <= 20%): passed. Test max drawdown 5.38% (6.00R) is within the 20.00% limit.

LightGBM, other gradient boosting and neural networks are NOT justified at this sample size (249 TRAIN candidates, 6 features): a flexible model would memorise noise, so only a fixed-penalty logistic regression with a break-even threshold is allowed.

## 6. Verdict

Candidates judged (pre-registered, one TEST look each): baseline `base`, discovery-selected `rr3_vol2_rsi50-70`, ML layer `base+ml`. Other grid variants are excluded because choosing among them by TEST numbers would be selection on TEST. The verdict requires the ROBUST label (positive TRAIN and TEST expectancy with the TEST 90% CI above zero) and reports the drawdown check.

No robust result found.

## 7. Why signals were rejected (decision counts per rule)

Signal candles denied per rule (the FIRST failing rule is counted, so the R1-R4 rows include every candle that was simply not a signal). Exits are never gated.

| rule | base TRAIN | base TEST | selected rr3_vol2_rsi50-70 TRAIN | selected rr3_vol2_rsi50-70 TEST | base+ml TRAIN | base+ml TEST | meaning |
|---|---:|---:|---:|---:|---:|---:|---|
| `R1_trend` | 19100 | 7899 | 19100 | 7899 | 19100 | 7899 | Trend gate: close > EMA9 and close > EMA21 and EMA9 > EMA21 (4H) |
| `R2_momentum` | 2680 | 1500 | 2680 | 1500 | 2680 | 1500 | Momentum: RSI(14) within [50, 70] on the entry candle |
| `R3_volume` | 5352 | 2212 | 5517 | 2293 | 5352 | 2212 | Volume: entry-candle volume >= 1.5x the previous-20-candle average |
| `R4_regime` | 191 | 69 | 126 | 42 | 191 | 69 | Regime: close > EMA200 (unless explicitly testing it off) |
| `R5_news_blackout` | 17 | 7 | 11 | 6 | 17 | 7 | No entries within +/-2h of high-impact news; BNB +/-24h burns/launchpool |
| `R6_correlation_cap` | 150 | 77 | 87 | 46 | 84 | 63 | BTC/ETH/BNB share one risk budget; BNB never stacks |
| `R7_position_risk` | 0 | 0 | 0 | 0 | 0 | 0 | Risk <= 1% (BTC/ETH) / 0.5% (BNB); size derived from stop distance |
| `R8_structure_stop` | 2 | 1 | 1 | 1 | 3 | 1 | Stop behind a confirmed swing low with a buffer (BNB 0.5-0.8%) |
| `R9_circuit_breaker` | 2 | 1 | 0 | 0 | 0 | 1 | 3 consecutive SLs bench a pair 24h; 7d realized loss limit halts all |
| `L_ml_filter` | 0 | 0 | 0 | 0 | 98 | 30 | Layer: logistic-regression filter (only removes trades, never adds) |
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
| trades taken | 100 | 60 | 72 | 39 | 69 | 44 | |

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
| TRAIN at 2023-03-14T00:00:00Z (equity 9,321.09) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 10,209.68) | - | - | none | No active adaptations. | - |

**discovery-selected `rr3_vol2_rsi50-70`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 10,816.17) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 11,590.48) | - | - | none | No active adaptations. | - |

**ML layer `base+ml`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 11,917.16) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 11,070.51) | - | - | none | No active adaptations. | - |

Reproduce the baseline TEST row with `python -m research.trendbot.journal_rules --journal research/results/synthetic_hour_edge_s1/journals/base_test.csv --equity 10209.68 --now 2024-12-30T00:00:00Z --backtest-journal`.

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
| `base` | NO-EDGE | 100 | 60 | +0.050 | `9be9d6da6ad86d85` | `none (no ML layer)` | BLOCKED |
| `rr3_vol2_rsi50-70` | UNTESTED | 72 | 39 | +0.436 | `5cbe1d74d6aa0330` | `none (no ML layer)` | BLOCKED |
| `base+ml` | UNTESTED | 69 | 44 | +0.295 | `9be9d6da6ad86d85` | `b38d6af2d40d7fa6` | BLOCKED |

- `base`: record [adoption_base.json](adoption_base.json); promotion to HUMAN_REVIEW is BLOCKED: [ADOPT_walk_forward] walk_forward.label is 'NO-EDGE' (the train window showed no positive expectancy to validate), and only ROBUST may proceed.
  Check: `python -m research.trendbot.adoption check --record research/results/synthetic_hour_edge_s1/adoption_base.json --stage HUMAN_REVIEW`
- `rr3_vol2_rsi50-70`: record [adoption_rr3_vol2_rsi50-70.json](adoption_rr3_vol2_rsi50-70.json), [config_rr3_vol2_rsi50-70.json](config_rr3_vol2_rsi50-70.json); promotion to HUMAN_REVIEW is BLOCKED: [ADOPT_walk_forward] walk_forward.label is 'UNTESTED' (too few trades, or a test expectancy not distinguishable from zero), and only ROBUST may proceed.
  Check: `python -m research.trendbot.adoption check --record research/results/synthetic_hour_edge_s1/adoption_rr3_vol2_rsi50-70.json --stage HUMAN_REVIEW --config research/results/synthetic_hour_edge_s1/config_rr3_vol2_rsi50-70.json`
- `base+ml`: record [adoption_base_plus_ml.json](adoption_base_plus_ml.json), [model_base_plus_ml.json](model_base_plus_ml.json); promotion to HUMAN_REVIEW is BLOCKED: [ADOPT_walk_forward] walk_forward.label is 'UNTESTED' (too few trades, or a test expectancy not distinguishable from zero), and only ROBUST may proceed.
  Check: `python -m research.trendbot.adoption check --record research/results/synthetic_hour_edge_s1/adoption_base_plus_ml.json --stage HUMAN_REVIEW --model-fingerprint b38d6af2d40d7fa65a3bd7386a4c4504718409b0770eaa6daac3b85a8ddf8b71`

The ML layer `base+ml` is adopted as a (config, model) pair: its record pins the config fingerprint AND the fitted model's fingerprint `b38d6af2d40d7fa65a3bd7386a4c4504718409b0770eaa6daac3b85a8ddf8b71` (`MLFilter.fingerprint()`, the sha256 of [model_base_plus_ml.json](model_base_plus_ml.json)). **Refitting the model (new TRAIN data, a later split, a different l2) changes the fingerprint, so a refitted model is a NEW candidate and restarts the adoption path at BACKTEST.** `adoption check` blocks (`ADOPT_fingerprint`) a model whose fingerprint differs from the recorded one, and any `+ml` record without one; the live bot must pass the fingerprint of the model it actually runs.

A `config_<variant>.json` file (the `StrategyConfig` overrides versus the defaults, e.g. costs or discovery parameters) is written whenever the tested config is not the default one; the check needs it as `--config`. The regime-OFF variant is test-only and can never be adopted.

## 12. Risk disclaimer

This is a research and testing tool, not financial advice and not a recommendation to trade. Backtests and synthetic worlds are simplified models; past or simulated results do not predict future results. Crypto trading can result in the total loss of the capital used.
