# Walk-forward research report: synthetic world `planted` seed 1

## 1. No win rate is promised or targeted

This report does NOT promise, target or optimise a win rate, and nothing in this package selects on one. A win rate is meaningless without the reward:risk it was earned at: with the mandatory minimum of 2:1 a strategy breaks even at about 33% winners before costs, while a 90% win rate with a 1:9 payoff still loses money. A backtest win rate near 90% is evidence of curve-fitting or look-ahead, not of skill. The target metric is POSITIVE EXPECTANCY (average R per trade, net of fees and slippage) that survives a 70/30 chronological walk-forward with controlled drawdown, with every trade planned at reward:risk >= 2:1. Win rate appears below only as context and is labelled 'context only' wherever it is shown.

## 2. Data provenance

- Source: **SYNTHETIC** world `planted`, seed 1, 6 years of 4H candles (13140 per pair) from 2019-01-01T00:00:00Z, pairs BTC/USDT, ETH/USDT, BNB/USDT (`synthetic.generate_world`).
- Ground truth: after every up-closing volume-spike candle the next 12 candles get extra positive drift (0.7 sigma per candle) over the WHOLE history; the drift is offset elsewhere, so buy-and-hold gains nothing from it.
- What the ground truth predicts: the base rules (which require a volume spike) should show positive expectancy on TRAIN and TEST; ROBUST is the correct label when the TEST sample is large enough.
- Qualifying triggers planted per pair (before / after the 70% split): BTC/USDT 342 / 147, ETH/USDT 333 / 134, BNB/USDT 319 / 123.
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
- Measured on the baseline's TRAIN and TEST trades: fees alone cost 0.06R per trade on average (median stop distance 3.76% of the entry price), so the expectancy below is only as good as the fee rate above.

## 3. Baseline: TRAIN vs TEST

Default mandate config `rr2_vol1.5_rsi50-70` (variant `base`). TRAIN window start .. 2023-03-14T00:00:00Z; TEST window 2023-03-14T00:00:00Z .. 2024-12-30T00:00:00Z.

| metric | TRAIN | TEST |
|---|---:|---:|
| trades (n) | 115 | 51 |
| avg R (expectancy, the target metric) | +0.296 | +0.412 |
| 90% bootstrap CI of avg R | [+0.070, +0.513] | [+0.059, +0.765] |
| t-stat of avg R | +2.13 | +1.94 |
| total R | +34.00 | +21.00 |
| profit factor | 1.62 | 1.77 |
| max drawdown % | 7.29% | 4.90% |
| max drawdown R | 9.00 | 6.00 |
| win rate (context only, never a target) | 43.5% | 47.1% |
| exits SL / TP / END | 65 / 49 / 1 | 27 / 24 / 0 |
| avg hold (h) | 112.1 | 99.3 |

**Label: ROBUST.** Expectancy is positive on train (+0.296R, n=115) and on test (+0.412R, n=51) with the test 90% bootstrap CI [+0.059, +0.765]R above zero.

Drawdown check (TEST max drawdown <= 20%): passed. Test max drawdown 4.90% (6.00R) is within the 20.00% limit.

## 4. Strategy discovery: TRAIN vs TEST

K = 13 variants tried: 12 legal tightenings (reward:risk {2, 2.5, 3} x volume multiple {1.5, 2} x RSI window {50-70, 55-70}) plus 1 explicit test-only variant with the R4 regime filter OFF, which is reported but can never be selected or adopted.

Selection rule: highest TRAIN t-stat among selectable variants with >= 30 TRAIN trades. rr3_vol2_rsi50-70 has the highest TRAIN t-stat (+3.58, avg +0.836R over 73 trades) among 12 eligible variants; chosen on TRAIN only, before any TEST backtest was run.

Order of operations actually run: TRAIN backtests: 13 variants, candles before the split only -> selection recorded: rr3_vol2_rsi50-70 -> TEST backtests: 13 variants, reported side by side only.

| variant | status | TRAIN n | TRAIN avg R | TRAIN 90% CI | TRAIN t | TEST n | TEST avg R | TEST 90% CI | TEST t | TEST max DD % | label |
|---|---|---:|---:|---|---:|---:|---:|---|---:|---:|---|
| `rr2_vol1.5_rsi50-70` | selectable | 115 | +0.296 | [+0.070, +0.513] | +2.13 | 51 | +0.412 | [+0.059, +0.765] | +1.94 | 4.90% | ROBUST |
| `rr2_vol1.5_rsi55-70` | selectable | 106 | +0.321 | [+0.094, +0.557] | +2.22 | 52 | +0.327 | [-0.019, +0.673] | +1.57 | 4.90% | UNTESTED |
| `rr2_vol2_rsi50-70` | selectable | 94 | +0.489 | [+0.245, +0.745] | +3.16 | 39 | +0.692 | [+0.308, +1.077] | +2.87 | 3.94% | ROBUST |
| `rr2_vol2_rsi55-70` | selectable | 86 | +0.523 | [+0.267, +0.767] | +3.23 | 37 | +0.703 | [+0.297, +1.108] | +2.84 | 3.94% | ROBUST |
| `rr2.5_vol1.5_rsi50-70` | selectable | 104 | +0.365 | [+0.096, +0.635] | +2.18 | 45 | +0.478 | [+0.011, +0.867] | +1.83 | 4.42% | ROBUST |
| `rr2.5_vol1.5_rsi55-70` | selectable | 95 | +0.384 | [+0.095, +0.658] | +2.19 | 45 | +0.400 | [+0.011, +0.867] | +1.55 | 4.42% | ROBUST |
| `rr2.5_vol2_rsi50-70` | selectable | 87 | +0.552 | [+0.230, +0.851] | +2.96 | 34 | +0.853 | [+0.338, +1.368] | +2.80 | 3.94% | ROBUST |
| `rr2.5_vol2_rsi55-70` | selectable | 79 | +0.576 | [+0.241, +0.893] | +2.94 | 32 | +0.859 | [+0.312, +1.406] | +2.74 | 3.94% | ROBUST |
| `rr3_vol1.5_rsi50-70` | selectable | 88 | +0.386 | [+0.068, +0.727] | +1.91 | 43 | +0.395 | [-0.070, +0.865] | +1.34 | 4.42% | UNTESTED |
| `rr3_vol1.5_rsi55-70` | selectable | 83 | +0.373 | [+0.036, +0.735] | +1.79 | 42 | +0.333 | [-0.143, +0.810] | +1.13 | 4.42% | UNTESTED |
| `rr3_vol2_rsi50-70` | **SELECTED** | 73 | +0.836 | [+0.452, +1.192] | +3.58 | 35 | +0.829 | [+0.257, +1.400] | +2.42 | 3.94% | ROBUST |
| `rr3_vol2_rsi55-70` | selectable | 68 | +0.853 | [+0.441, +1.235] | +3.53 | 33 | +0.818 | [+0.212, +1.424] | +2.32 | 3.94% | ROBUST |
| `rr2_vol1.5_rsi50-70_regimeOFF` | TEST-ONLY (regime OFF, never selectable) | 157 | +0.228 | [+0.037, +0.420] | +1.94 | 67 | +0.404 | [+0.107, +0.718] | +2.21 | 5.92% | ROBUST |

**Re-selecting a variant on these TEST numbers would turn TEST into TRAIN: the selection was fixed on TRAIN before any TEST backtest ran, and it is not revisited.**

The TRAIN column of the selected variant `rr3_vol2_rsi50-70` is biased upward by the selection itself (it is the best of 12); only its TEST column is an out-of-sample test. The labels of the other variants are context only and do not enter the verdict.

**Label: ROBUST.** Expectancy is positive on train (+0.836R, n=73) and on test (+0.829R, n=35) with the test 90% bootstrap CI [+0.257, +1.400]R above zero.

Drawdown check (TEST max drawdown <= 20%): passed. Test max drawdown 3.94% (4.00R) is within the 20.00% limit.

## 5. ML filter layer: TRAIN (in-sample) vs TEST

Variant `base+ml`: the baseline rules plus the `L_ml_filter` layer, a logistic regression on 6 features (rsi, vol_ratio, ema_gap_pct, dist_regime_pct, hour_sin, hour_cos). It can only REMOVE trades that passed every mandatory rule. It is fitted ONLY on TRAIN candidates and applied unchanged to TEST, so its TRAIN numbers are IN-SAMPLE.

Logistic filter (l2=1) fitted on 268 TRAIN candidates (TRAIN base win rate 39.6%, context only) keeps signals whose predicted win probability exceeds the break-even 0.333.

- TRAIN candidates enumerated (purged: outcome resolved before the split): 268; used for the fit: 268 (0 skipped for missing features); TRAIN candidate win share 39.6% (context only).
- Threshold p* = break-even win probability from the TRAIN avg win +2.00R and avg loss -1.00R: p* = 0.333 (no threshold search).
- L2 penalty 1 (fixed, not tuned); intercept -0.472.
- Model fingerprint (sha256 of features, TRAIN means/stds, intercept, coefficients, threshold and l2): `0986e8bdf0e5206e179c52b257b8872e4d740c0438bee6e8f4f175aa8d17f736`. It is pinned in the ML adoption record (section 11).

| feature | standardised coefficient, fitted on TRAIN only and applied unchanged to TEST (log-odds per TRAIN s.d.) |
|---|---:|
| `rsi` | +0.069 |
| `vol_ratio` | +0.309 |
| `ema_gap_pct` | -0.569 |
| `dist_regime_pct` | -0.172 |
| `hour_sin` | +0.198 |
| `hour_cos` | +0.240 |

| metric | base TRAIN | base+ml TRAIN (in-sample) | base TEST | base+ml TEST |
|---|---:|---:|---:|---:|
| trades (n) | 115 | 94 | 51 | 34 |
| avg R (expectancy, the target metric) | +0.296 | +0.489 | +0.412 | +0.235 |
| 90% bootstrap CI of avg R | [+0.070, +0.513] | [+0.234, +0.755] | [+0.059, +0.765] | [-0.206, +0.676] |
| t-stat of avg R | +2.13 | +3.16 | +1.94 | +0.92 |
| total R | +34.00 | +46.00 | +21.00 | +8.00 |
| profit factor | 1.62 | 1.88 | 1.77 | 1.46 |
| max drawdown % | 7.29% | 4.90% | 4.90% | 4.90% |
| max drawdown R | 9.00 | 5.00 | 6.00 | 5.00 |
| win rate (context only, never a target) | 43.5% | 50.0% | 47.1% | 41.2% |
| exits SL / TP / END | 65 / 49 / 1 | 47 / 46 / 1 | 27 / 24 / 0 | 20 / 14 / 0 |
| avg hold (h) | 112.1 | 100.3 | 99.3 | 115.3 |

On TEST the filter removed 28 signals that had passed every mandatory rule; TEST expectancy +0.412R (base) -> +0.235R (with the filter).

**Label: UNTESTED.** Test expectancy is positive at +0.235R (n=34) but its 90% bootstrap CI [-0.206, +0.676]R includes zero, so it is not distinguishable from no edge.

Drawdown check (TEST max drawdown <= 20%): passed. Test max drawdown 4.90% (5.00R) is within the 20.00% limit.

LightGBM, other gradient boosting and neural networks are NOT justified at this sample size (268 TRAIN candidates, 6 features): a flexible model would memorise noise, so only a fixed-penalty logistic regression with a break-even threshold is allowed.

## 6. Verdict

Candidates judged (pre-registered, one TEST look each): baseline `base`, discovery-selected `rr3_vol2_rsi50-70`, ML layer `base+ml`. Other grid variants are excluded because choosing among them by TEST numbers would be selection on TEST. The verdict requires the ROBUST label (positive TRAIN and TEST expectancy with the TEST 90% CI above zero) and reports the drawdown check.

ROBUST: baseline `base` (TEST avg +0.412R, 90% CI [+0.059, +0.765]R, n=51; drawdown check passed); discovery-selected `rr3_vol2_rsi50-70` (TEST avg +0.829R, 90% CI [+0.257, +1.400]R, n=35; drawdown check passed).

## 7. Why signals were rejected (decision counts per rule)

Signal candles denied per rule (the FIRST failing rule is counted, so the R1-R4 rows include every candle that was simply not a signal). Exits are never gated.

| rule | base TRAIN | base TEST | selected rr3_vol2_rsi50-70 TRAIN | selected rr3_vol2_rsi50-70 TEST | base+ml TRAIN | base+ml TEST | meaning |
|---|---:|---:|---:|---:|---:|---:|---|
| `R1_trend` | 18476 | 7756 | 18476 | 7756 | 18476 | 7756 | Trend gate: close > EMA9 and close > EMA21 and EMA9 > EMA21 (4H) |
| `R2_momentum` | 3390 | 1684 | 3390 | 1684 | 3390 | 1684 | Momentum: RSI(14) within [50, 70] on the entry candle |
| `R3_volume` | 5271 | 2193 | 5428 | 2254 | 5271 | 2193 | Volume: entry-candle volume >= 1.5x the previous-20-candle average |
| `R4_regime` | 176 | 74 | 112 | 52 | 176 | 74 | Regime: close > EMA200 (unless explicitly testing it off) |
| `R5_news_blackout` | 9 | 8 | 6 | 5 | 9 | 8 | No entries within +/-2h of high-impact news; BNB +/-24h burns/launchpool |
| `R6_correlation_cap` | 156 | 59 | 108 | 39 | 116 | 48 | BTC/ETH/BNB share one risk budget; BNB never stacks |
| `R7_position_risk` | 0 | 0 | 0 | 0 | 0 | 0 | Risk <= 1% (BTC/ETH) / 0.5% (BNB); size derived from stop distance |
| `R8_structure_stop` | 1 | 1 | 1 | 1 | 2 | 1 | Stop behind a confirmed swing low with a buffer (BNB 0.5-0.8%) |
| `R9_circuit_breaker` | 0 | 0 | 0 | 0 | 0 | 0 | 3 consecutive SLs bench a pair 24h; 7d realized loss limit halts all |
| `L_ml_filter` | 0 | 0 | 0 | 0 | 60 | 28 | Layer: logistic-regression filter (only removes trades, never adds) |
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
| trades taken | 115 | 51 | 73 | 35 | 94 | 34 | |

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
| TRAIN at 2023-03-14T00:00:00Z (equity 13,775.00) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 12,030.61) | - | - | none | No active adaptations. | - |

**discovery-selected `rr3_vol2_rsi50-70`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 16,943.85) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 12,450.21) | - | - | none | No active adaptations. | - |

**ML layer `base+ml`:**

| window (TRAIN journal at the split, TEST journal at the end) | rule | scope | action | explanation | evidence (journal trade ids) |
|---|---|---|---|---|---|
| TRAIN at 2023-03-14T00:00:00Z (equity 14,422.46) | - | - | none | No active adaptations. | - |
| TEST at 2024-12-30T00:00:00Z (equity 10,799.34) | - | - | none | No active adaptations. | - |

Reproduce the baseline TEST row with `python -m research.trendbot.journal_rules --journal research/results/synthetic_planted_s1/journals/base_test.csv --equity 12030.61 --now 2024-12-30T00:00:00Z --backtest-journal`.

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
| `base` | ROBUST | 115 | 51 | +0.412 | `9be9d6da6ad86d85` | `none (no ML layer)` | allowed |
| `rr3_vol2_rsi50-70` | ROBUST | 73 | 35 | +0.829 | `5cbe1d74d6aa0330` | `none (no ML layer)` | allowed |
| `base+ml` | UNTESTED | 94 | 34 | +0.235 | `9be9d6da6ad86d85` | `0986e8bdf0e5206e` | BLOCKED |

- `base`: record [adoption_base.json](adoption_base.json); promotion to HUMAN_REVIEW is allowed: a named person must now review every trade in its review pack, then fill `human_review` in the record before any testnet run.
  Check: `python -m research.trendbot.adoption check --record research/results/synthetic_planted_s1/adoption_base.json --stage HUMAN_REVIEW`
- `rr3_vol2_rsi50-70`: record [adoption_rr3_vol2_rsi50-70.json](adoption_rr3_vol2_rsi50-70.json), [config_rr3_vol2_rsi50-70.json](config_rr3_vol2_rsi50-70.json); promotion to HUMAN_REVIEW is allowed: a named person must now review every trade in its review pack, then fill `human_review` in the record before any testnet run.
  Check: `python -m research.trendbot.adoption check --record research/results/synthetic_planted_s1/adoption_rr3_vol2_rsi50-70.json --stage HUMAN_REVIEW --config research/results/synthetic_planted_s1/config_rr3_vol2_rsi50-70.json`
- `base+ml`: record [adoption_base_plus_ml.json](adoption_base_plus_ml.json), [model_base_plus_ml.json](model_base_plus_ml.json); promotion to HUMAN_REVIEW is BLOCKED: [ADOPT_walk_forward] walk_forward.label is 'UNTESTED' (too few trades, or a test expectancy not distinguishable from zero), and only ROBUST may proceed.
  Check: `python -m research.trendbot.adoption check --record research/results/synthetic_planted_s1/adoption_base_plus_ml.json --stage HUMAN_REVIEW --model-fingerprint 0986e8bdf0e5206e179c52b257b8872e4d740c0438bee6e8f4f175aa8d17f736`

The ML layer `base+ml` is adopted as a (config, model) pair: its record pins the config fingerprint AND the fitted model's fingerprint `0986e8bdf0e5206e179c52b257b8872e4d740c0438bee6e8f4f175aa8d17f736` (`MLFilter.fingerprint()`, the sha256 of [model_base_plus_ml.json](model_base_plus_ml.json)). **Refitting the model (new TRAIN data, a later split, a different l2) changes the fingerprint, so a refitted model is a NEW candidate and restarts the adoption path at BACKTEST.** `adoption check` blocks (`ADOPT_fingerprint`) a model whose fingerprint differs from the recorded one, and any `+ml` record without one; the live bot must pass the fingerprint of the model it actually runs.

A `config_<variant>.json` file (the `StrategyConfig` overrides versus the defaults, e.g. costs or discovery parameters) is written whenever the tested config is not the default one; the check needs it as `--config`. The regime-OFF variant is test-only and can never be adopted.

## 12. Risk disclaimer

This is a research and testing tool, not financial advice and not a recommendation to trade. Backtests and synthetic worlds are simplified models; past or simulated results do not predict future results. Crypto trading can result in the total loss of the capital used.
