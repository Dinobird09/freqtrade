# Walk-forward research report: synthetic world `null` seed 1

## 1. No win rate is promised or targeted

This report does NOT promise, target or optimise a win rate, and nothing in this package selects on one. A win rate is meaningless without the reward:risk it was earned at: with the mandatory minimum of 2:1 a strategy breaks even at about 33% winners before costs, while a 90% win rate with a 1:9 payoff still loses money. A backtest win rate near 90% is evidence of curve-fitting or look-ahead, not of skill. The target metric is POSITIVE EXPECTANCY (average R per trade, net of fees and slippage) that survives a 70/30 chronological walk-forward with controlled drawdown, with every trade planned at reward:risk >= 2:1. Win rate appears below only as context and is labelled 'context only' wherever it is shown.

## 2. Data provenance

- Source: **SYNTHETIC** world `null`, seed 1, 6 years of 4H candles (13140 per pair) from 2019-01-01T00:00:00Z, pairs BTC/USDT, ETH/USDT, BNB/USDT (`synthetic.generate_world`).
- Ground truth: martingale prices (zero drift): no entry/exit rule has an edge before costs, so every strategy has negative expectancy after fees and slippage.
- What the ground truth predicts: nothing should be ROBUST; the expected labels are NO-EDGE, TRAIN-ONLY or UNTESTED, and a ROBUST label here is a false positive.
- Qualifying triggers planted per pair (before / after the 70% split): BTC/USDT 0 / 0, ETH/USDT 0 / 0, BNB/USDT 0 / 0.
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
- Measured on the baseline's TRAIN and TEST trades: fees alone cost 0.06R per trade on average (median stop distance 3.78% of the entry price), so the expectancy below is only as good as the fee rate above.

## 3. Baseline: TRAIN vs TEST

Default mandate config `rr2_vol1.5_rsi50-70` (variant `base`). TRAIN window start .. 2023-03-14T00:00:00Z; TEST window 2023-03-14T00:00:00Z .. 2024-12-30T00:00:00Z.

| metric | TRAIN | TEST |
|---|---:|---:|
| trades (n) | 98 | 58 |
| avg R (expectancy, the target metric) | -0.138 | -0.121 |
| 90% bootstrap CI of avg R | [-0.357, +0.102] | [-0.379, +0.190] |
| t-stat of avg R | -1.00 | -0.67 |
| total R | -13.48 | -7.00 |
| profit factor | 0.73 | 0.80 |
| max drawdown % | 16.86% | 12.84% |
| max drawdown R | 19.00 | 14.00 |
| win rate (context only, never a target) | 28.6% | 29.3% |
| exits SL / TP / END | 69 / 28 / 1 | 41 / 17 / 0 |
| avg hold (h) | 177.8 | 112.6 |

**Label: NO-EDGE.** Train expectancy is -0.138R over 98 trades, so there is no edge to validate.

Drawdown check (TEST max drawdown <= 20%): passed. Test max drawdown 12.84% (14.00R) is within the 20.00% limit.

## 4. Strategy discovery: TRAIN vs TEST

K = 13 variants tried: 12 legal tightenings (reward:risk {2, 2.5, 3} x volume multiple {1.5, 2} x RSI window {50-70, 55-70}) plus 1 explicit test-only variant with the R4 regime filter OFF, which is reported but can never be selected or adopted.

Selection rule: highest TRAIN t-stat among selectable variants with >= 30 TRAIN trades. rr2.5_vol2_rsi50-70 has the highest TRAIN t-stat (-0.50, avg -0.086R over 81 trades) among 12 eligible variants; chosen on TRAIN only, before any TEST backtest was run.

Order of operations actually run: TRAIN backtests: 13 variants, candles before the split only -> selection recorded: rr2.5_vol2_rsi50-70 -> TEST backtests: 13 variants, reported side by side only.

| variant | status | TRAIN n | TRAIN avg R | TRAIN 90% CI | TRAIN t | TEST n | TEST avg R | TEST 90% CI | TEST t | TEST max DD % | label |
|---|---|---:|---:|---|---:|---:|---:|---|---:|---:|---|
| `rr2_vol1.5_rsi50-70` | selectable | 98 | -0.138 | [-0.357, +0.102] | -1.00 | 58 | -0.121 | [-0.379, +0.190] | -0.67 | 12.84% | NO-EDGE |
| `rr2_vol1.5_rsi55-70` | selectable | 93 | -0.123 | [-0.355, +0.108] | -0.87 | 59 | -0.085 | [-0.342, +0.220] | -0.47 | 12.86% | NO-EDGE |
| `rr2_vol2_rsi50-70` | selectable | 89 | -0.185 | [-0.415, +0.057] | -1.31 | 53 | -0.151 | [-0.434, +0.132] | -0.81 | 11.62% | NO-EDGE |
| `rr2_vol2_rsi55-70` | selectable | 81 | -0.290 | [-0.519, -0.061] | -2.04 | 54 | -0.111 | [-0.444, +0.222] | -0.59 | 10.66% | NO-EDGE |
| `rr2.5_vol1.5_rsi50-70` | selectable | 93 | -0.091 | [-0.349, +0.172] | -0.57 | 48 | -0.198 | [-0.563, +0.167] | -0.92 | 14.59% | NO-EDGE |
| `rr2.5_vol1.5_rsi55-70` | selectable | 88 | -0.159 | [-0.403, +0.120] | -0.99 | 53 | -0.142 | [-0.472, +0.255] | -0.68 | 14.23% | NO-EDGE |
| `rr2.5_vol2_rsi50-70` | **SELECTED** | 81 | -0.086 | [-0.352, +0.210] | -0.50 | 49 | -0.143 | [-0.500, +0.214] | -0.66 | 12.38% | NO-EDGE |
| `rr2.5_vol2_rsi55-70` | selectable | 75 | -0.200 | [-0.480, +0.080] | -1.17 | 51 | -0.108 | [-0.451, +0.235] | -0.50 | 11.53% | NO-EDGE |
| `rr3_vol1.5_rsi50-70` | selectable | 81 | -0.203 | [-0.457, +0.093] | -1.14 | 47 | -0.064 | [-0.489, +0.362] | -0.26 | 11.20% | NO-EDGE |
| `rr3_vol1.5_rsi55-70` | selectable | 80 | -0.293 | [-0.550, -0.023] | -1.72 | 53 | -0.019 | [-0.396, +0.434] | -0.08 | 10.41% | NO-EDGE |
| `rr3_vol2_rsi50-70` | selectable | 76 | -0.204 | [-0.513, +0.105] | -1.11 | 45 | -0.022 | [-0.467, +0.422] | -0.09 | 9.40% | NO-EDGE |
| `rr3_vol2_rsi55-70` | selectable | 72 | -0.326 | [-0.611, -0.041] | -1.85 | 47 | +0.021 | [-0.404, +0.447] | +0.08 | 8.08% | NO-EDGE |
| `rr2_vol1.5_rsi50-70_regimeOFF` | TEST-ONLY (regime OFF, never selectable) | 133 | -0.274 | [-0.443, -0.094] | -2.46 | 66 | -0.136 | [-0.409, +0.136] | -0.81 | 14.63% | NO-EDGE |

**Re-selecting a variant on these TEST numbers would turn TEST into TRAIN: the selection was fixed on TRAIN before any TEST backtest ran, and it is not revisited.**

The TRAIN column of the selected variant `rr2.5_vol2_rsi50-70` is biased upward by the selection itself (it is the best of 12); only its TEST column is an out-of-sample test. The labels of the other variants are context only and do not enter the verdict.

**Label: NO-EDGE.** Train expectancy is -0.086R over 81 trades, so there is no edge to validate.

Drawdown check (TEST max drawdown <= 20%): passed. Test max drawdown 12.38% (14.00R) is within the 20.00% limit.

## 5. ML filter layer: TRAIN (in-sample) vs TEST

Variant `base+ml`: the baseline rules plus the `L_ml_filter` layer, a logistic regression on 6 features (rsi, vol_ratio, ema_gap_pct, dist_regime_pct, hour_sin, hour_cos). It can only REMOVE trades that passed every mandatory rule. It is fitted ONLY on TRAIN candidates and applied unchanged to TEST, so its TRAIN numbers are IN-SAMPLE.

Logistic filter (l2=1) fitted on 407 TRAIN candidates (TRAIN base win rate 24.6%, context only) keeps signals whose predicted win probability exceeds the break-even 0.333.

- TRAIN candidates enumerated (purged: outcome resolved before the split): 407; used for the fit: 407 (0 skipped for missing features); TRAIN candidate win share 24.6% (context only).
- Threshold p* = break-even win probability from the TRAIN avg win +2.00R and avg loss -1.00R: p* = 0.333 (no threshold search).
- L2 penalty 1 (fixed, not tuned); intercept -1.173.
- Model fingerprint (sha256 of features, TRAIN means/stds, intercept, coefficients, threshold and l2): `bbc52bd7ee0166eaf2899d12f2aad10e5118cfc5595e36550ac6f5358d44f32d`. It is pinned in the ML adoption record (section 11).

| feature | standardised coefficient, fitted on TRAIN only and applied unchanged to TEST (log-odds per TRAIN s.d.) |
|---|---:|
| `rsi` | -0.123 |
| `vol_ratio` | -0.116 |
| `ema_gap_pct` | +0.090 |
| `dist_regime_pct` | -0.416 |
| `hour_sin` | +0.018 |
| `hour_cos` | -0.090 |

| metric | base TRAIN | base+ml TRAIN (in-sample) | base TEST | base+ml TEST |
|---|---:|---:|---:|---:|
| trades (n) | 98 | 45 | 58 | 17 |
| avg R (expectancy, the target metric) | -0.138 | +0.267 | -0.121 | -0.294 |
| 90% bootstrap CI of avg R | [-0.357, +0.102] | [-0.067, +0.600] | [-0.379, +0.190] | [-0.824, +0.235] |
| t-stat of avg R | -1.00 | +1.19 | -0.67 | -0.92 |
| total R | -13.48 | +12.00 | -7.00 | -5.00 |
| profit factor | 0.73 | 1.11 | 0.80 | 0.65 |
| max drawdown % | 16.86% | 6.84% | 12.84% | 7.73% |
| max drawdown R | 19.00 | 7.00 | 14.00 | 9.00 |
| win rate (context only, never a target) | 28.6% | 42.2% | 29.3% | 23.5% |
| exits SL / TP / END | 69 / 28 / 1 | 26 / 19 / 0 | 41 / 17 / 0 | 13 / 4 / 0 |
| avg hold (h) | 177.8 | 107.0 | 112.6 | 52.9 |

On TEST the filter removed 175 signals that had passed every mandatory rule; TEST expectancy -0.121R (base) -> -0.294R (with the filter).

**Label: UNTESTED.** Too few trades to judge: test n=17 (need 30).

Drawdown check (TEST max drawdown <= 20%): passed. Test max drawdown 7.73% (9.00R) is within the 20.00% limit.

LightGBM, other gradient boosting and neural networks are NOT justified at this sample size (407 TRAIN candidates, 6 features): a flexible model would memorise noise, so only a fixed-penalty logistic regression with a break-even threshold is allowed.

## 6. Verdict

Candidates judged (pre-registered, one TEST look each): baseline `base`, discovery-selected `rr2.5_vol2_rsi50-70`, ML layer `base+ml`. Other grid variants are excluded because choosing among them by TEST numbers would be selection on TEST. The verdict requires the ROBUST label (positive TRAIN and TEST expectancy with the TEST 90% CI above zero) and reports the drawdown check.

No robust result found.

## 7. Why signals were rejected (decision counts per rule)

Signal candles denied per rule (the FIRST failing rule is counted, so the R1-R4 rows include every candle that was simply not a signal). Exits are never gated.

| rule | base TRAIN | base TEST | selected rr2.5_vol2_rsi50-70 TRAIN | selected rr2.5_vol2_rsi50-70 TEST | base+ml TRAIN | base+ml TEST | meaning |
|---|---:|---:|---:|---:|---:|---:|---|
| `R1_trend` | 18810 | 7617 | 18810 | 7617 | 18810 | 7617 | Trend gate: close > EMA9 and close > EMA21 and EMA9 > EMA21 (4H) |
| `R2_momentum` | 1187 | 758 | 1187 | 758 | 1187 | 758 | Momentum: RSI(14) within [50, 70] on the entry candle |
| `R3_volume` | 6920 | 3134 | 7172 | 3252 | 6920 | 3134 | Volume: entry-candle volume >= 1.5x the previous-20-candle average |
| `R4_regime` | 254 | 97 | 156 | 56 | 254 | 97 | Regime: close > EMA200 (unless explicitly testing it off) |
| `R5_news_blackout` | 13 | 8 | 10 | 6 | 13 | 8 | No entries within +/-2h of high-impact news; BNB +/-24h burns/launchpool |
| `R6_correlation_cap` | 312 | 152 | 178 | 87 | 104 | 18 | BTC/ETH/BNB share one risk budget; BNB never stacks |
| `R7_position_risk` | 0 | 0 | 0 | 0 | 0 | 0 | Risk <= 1% (BTC/ETH) / 0.5% (BNB); size derived from stop distance |
| `R8_structure_stop` | 0 | 2 | 0 | 0 | 1 | 2 | Stop behind a confirmed swing low with a buffer (BNB 0.5-0.8%) |
| `R9_circuit_breaker` | 0 | 0 | 0 | 1 | 0 | 0 | 3 consecutive SLs bench a pair 24h; 7d realized loss limit halts all |
| `L_ml_filter` | 0 | 0 | 0 | 0 | 260 | 175 | Layer: logistic-regression filter (only removes trades, never adds) |
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
| trades taken | 98 | 58 | 81 | 49 | 45 | 17 | |

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
| TRAIN at 2023-03-14T00:00:00Z (equity 8,599.00) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 9,283.92) | - | - | none | No active adaptations. | - |

**discovery-selected `rr2.5_vol2_rsi50-70`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 8,896.75) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 9,351.77) | - | - | none | No active adaptations. | - |

**ML layer `base+ml`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 10,270.77) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 9,645.37) | - | - | none | No active adaptations. | - |

Reproduce the baseline TEST row with `python -m research.trendbot.journal_rules --journal research/results/synthetic_null_s1/journals/base_test.csv --equity 9283.92 --now 2024-12-30T00:00:00Z --backtest-journal`.

## 10. Journals and human review packs

Trade journals (`journal.write_journal`, one CSV per window) for the three pre-registered candidates, the variants whose TRAIN and TEST columns this report shows in full. The other discovery variants appear only as one context row each in section 4, so no journal is kept for them (the same command regenerates them). TEST trade ids are offset past the TRAIN ids, so ids are unique across a variant's two journals and its review pack.

| variant | TRAIN journal | TEST journal |
|---|---|---|
| `base` | [journals/base_train.csv](journals/base_train.csv) | [journals/base_test.csv](journals/base_test.csv) |
| `rr2.5_vol2_rsi50-70` | [journals/rr2.5_vol2_rsi50-70_train.csv](journals/rr2.5_vol2_rsi50-70_train.csv) | [journals/rr2.5_vol2_rsi50-70_test.csv](journals/rr2.5_vol2_rsi50-70_test.csv) |
| `base+ml` | [journals/base_plus_ml_train.csv](journals/base_plus_ml_train.csv) | [journals/base_plus_ml_test.csv](journals/base_plus_ml_test.csv) |

Human review packs (`review_sheet.write_review_pack`, TRAIN and TEST):

- `base`: [review_base/trades_review.md](review_base/trades_review.md) and [review_base/trades_review.csv](review_base/trades_review.csv)
- `rr2.5_vol2_rsi50-70`: [review_selected_rr2.5_vol2_rsi50-70/trades_review.md](review_selected_rr2.5_vol2_rsi50-70/trades_review.md) and [review_selected_rr2.5_vol2_rsi50-70/trades_review.csv](review_selected_rr2.5_vol2_rsi50-70/trades_review.csv)
- `base+ml`: [review_base_plus_ml/trades_review.md](review_base_plus_ml/trades_review.md) and [review_base_plus_ml/trades_review.csv](review_base_plus_ml/trades_review.csv)

## 11. Adoption path and current stage

The only path to real money (enforced by `adoption.py`; no step can be skipped, the promoted config must match the tested config's sha256 fingerprint, and an ML layer's model must match the tested model's fingerprint):

backtest -> walk-forward (ROBUST + drawdown check) -> human review of EVERY trade -> >= 2 weeks on Binance testnet with zero rule violations -> live.

Stages: BACKTEST -> WALK_FORWARD -> HUMAN_REVIEW -> TESTNET -> LIVE. Stage reached by this run: **WALK_FORWARD** (recorded). Next: **HUMAN_REVIEW**.

**This run used SYNTHETIC data: its records only demonstrate the mechanism. A synthetic result can never justify adopting a variant; the path must be run on real candles from the backtest stage.**

| variant | walk-forward label (TRAIN + TEST) | TRAIN n | TEST n | TEST avg R | config sha256 (first 16) | ML model sha256 (first 16) | promotion to HUMAN_REVIEW |
|---|---|---:|---:|---:|---|---|---|
| `base` | NO-EDGE | 98 | 58 | -0.121 | `9be9d6da6ad86d85` | `none (no ML layer)` | BLOCKED |
| `rr2.5_vol2_rsi50-70` | NO-EDGE | 81 | 49 | -0.143 | `04c83eeb8d4d852e` | `none (no ML layer)` | BLOCKED |
| `base+ml` | UNTESTED | 45 | 17 | -0.294 | `9be9d6da6ad86d85` | `bbc52bd7ee0166ea` | BLOCKED |

- `base`: record [adoption_base.json](adoption_base.json); promotion to HUMAN_REVIEW is BLOCKED: [ADOPT_walk_forward] walk_forward.label is 'NO-EDGE' (the train window showed no positive expectancy to validate), and only ROBUST may proceed. [ADOPT_walk_forward] walk_forward.test_avg_r must be a positive out-of-sample expectancy in R (got -0.12068965517241485).
  Check: `python -m research.trendbot.adoption check --record research/results/synthetic_null_s1/adoption_base.json --stage HUMAN_REVIEW`
- `rr2.5_vol2_rsi50-70`: record [adoption_rr2.5_vol2_rsi50-70.json](adoption_rr2.5_vol2_rsi50-70.json), [config_rr2.5_vol2_rsi50-70.json](config_rr2.5_vol2_rsi50-70.json); promotion to HUMAN_REVIEW is BLOCKED: [ADOPT_walk_forward] walk_forward.label is 'NO-EDGE' (the train window showed no positive expectancy to validate), and only ROBUST may proceed. [ADOPT_walk_forward] walk_forward.test_avg_r must be a positive out-of-sample expectancy in R (got -0.1428571428571439).
  Check: `python -m research.trendbot.adoption check --record research/results/synthetic_null_s1/adoption_rr2.5_vol2_rsi50-70.json --stage HUMAN_REVIEW --config research/results/synthetic_null_s1/config_rr2.5_vol2_rsi50-70.json`
- `base+ml`: record [adoption_base_plus_ml.json](adoption_base_plus_ml.json), [model_base_plus_ml.json](model_base_plus_ml.json); promotion to HUMAN_REVIEW is BLOCKED: [ADOPT_walk_forward] walk_forward.label is 'UNTESTED' (too few trades, or a test expectancy not distinguishable from zero), and only ROBUST may proceed. [ADOPT_walk_forward] walk_forward.test_avg_r must be a positive out-of-sample expectancy in R (got -0.29411764705882426).
  Check: `python -m research.trendbot.adoption check --record research/results/synthetic_null_s1/adoption_base_plus_ml.json --stage HUMAN_REVIEW --model-fingerprint bbc52bd7ee0166eaf2899d12f2aad10e5118cfc5595e36550ac6f5358d44f32d`

The ML layer `base+ml` is adopted as a (config, model) pair: its record pins the config fingerprint AND the fitted model's fingerprint `bbc52bd7ee0166eaf2899d12f2aad10e5118cfc5595e36550ac6f5358d44f32d` (`MLFilter.fingerprint()`, the sha256 of [model_base_plus_ml.json](model_base_plus_ml.json)). **Refitting the model (new TRAIN data, a later split, a different l2) changes the fingerprint, so a refitted model is a NEW candidate and restarts the adoption path at BACKTEST.** `adoption check` blocks (`ADOPT_fingerprint`) a model whose fingerprint differs from the recorded one, and any `+ml` record without one; the live bot must pass the fingerprint of the model it actually runs.

A `config_<variant>.json` file (the `StrategyConfig` overrides versus the defaults, e.g. costs or discovery parameters) is written whenever the tested config is not the default one; the check needs it as `--config`. The regime-OFF variant is test-only and can never be adopted.

## 12. Risk disclaimer

This is a research and testing tool, not financial advice and not a recommendation to trade. Backtests and synthetic worlds are simplified models; past or simulated results do not predict future results. Crypto trading can result in the total loss of the capital used.
