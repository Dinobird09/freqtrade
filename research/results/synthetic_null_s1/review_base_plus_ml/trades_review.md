# Trade review: ML layer `base+ml`

**Human review pack.** The reviewer must inspect EVERY row of the trade table below (all 25 trades, also in `trades_review.csv`) before signing off: summary statistics alone are not a review. Automated flags only point at suspicious rows; an unflagged trade is not an approved trade. Record a verdict for every trade in the `reviewer_ok` (exactly Y or N; any other value is rejected when the sheet is read back) and `reviewer_note` columns of `trades_review.csv`.

- Trades: 25 (TRAIN 8, TEST 17)
- Closed trades: 25; open or missing exit: 0
- Flagged trades: 0 of 25
- Pairs: BNB/USDT 8, BTC/USDT 8, ETH/USDT 9
- Variants: base+ml 25
- Walk-forward split: 2023-03-14T00:00:00Z (TRAIN = signal candle before it, TEST = at or after it)
- Config: `rr2_vol1.5_rsi50-70`, reward:risk 2.00 (net of costs), risk caps BNB 0.50%, BTC 1.00%, ETH 1.00%
- Costs: fee 0.1000% per side, slippage 0.0500% on market fills (entry and stop), exchange `binance`. Risk is the ALL-IN loss at the stop, so a clean stop-out is -1R and a TP is +reward:risk R; `RR price` is the chart-distance ratio, `RR net` what a TP earns after both fees.
- Row key: (window, trade_id), unique in this pack; `trades_review.csv` is matched against the TRAIN and TEST journals by this key
- Candle context: unavailable (no candles supplied, see the trade context section)

## Summary per window

avg R is the expectancy per closed trade (the target metric), net of fees and slippage. Win rate is deliberately not shown: it is never a target.

| metric | TRAIN | TEST |
|---|---:|---:|
| trades | 8 | 17 |
| closed with R | 8 | 17 |
| avg R (expectancy) | -1.000 | -0.294 |
| total R | -8.000 | -5.000 |
| exits SL / TP / END | 8 / 0 / 0 | 13 / 4 / 0 |
| flagged | 0 | 0 |

## All trades (25)

| id | window | pair | variant | signal UTC | entry UTC | exit UTC | entry | stop | target | stop method | RR price | RR net | risk % | cap % | exit | reason | R | hold h | ml p | flags |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | TRAIN | BTC/USDT | base+ml | 2021-12-26T04:00:00Z | 2021-12-26T08:00:00Z | 2022-01-02T20:00:00Z | 1438.687019 | 1359.312002 | 1607.437128 | pivot | 2.1260 | 2.0000 | 1.0000 | 1.0000 | 1358.632346 | SL | -1.0000 | 180.00 | 0.3770 |  |
| 2 | TRAIN | ETH/USDT | base+ml | 2022-01-26T08:00:00Z | 2022-01-26T12:00:00Z | 2022-01-27T08:00:00Z | 16.698564 | 16.239212 | 17.733799 | pivot | 2.2537 | 2.0000 | 1.0000 | 1.0000 | 16.231092 | SL | -1.0000 | 20.00 | 0.3497 |  |
| 3 | TRAIN | ETH/USDT | base+ml | 2022-01-28T16:00:00Z | 2022-01-28T20:00:00Z | 2022-01-29T04:00:00Z | 16.609703 | 16.091984 | 17.760991 | pivot | 2.2238 | 2.0000 | 1.0000 | 1.0000 | 16.083938 | SL | -1.0000 | 8.00 | 0.3405 |  |
| 4 | TRAIN | ETH/USDT | base+ml | 2022-02-26T16:00:00Z | 2022-02-26T20:00:00Z | 2022-02-28T04:00:00Z | 16.061687 | 15.322735 | 17.651380 | pivot | 2.1513 | 2.0000 | 1.0000 | 1.0000 | 15.315074 | SL | -1.0000 | 32.00 | 0.3522 |  |
| 5 | TRAIN | BNB/USDT | base+ml | 2022-03-01T04:00:00Z | 2022-03-01T08:00:00Z | 2022-03-02T20:00:00Z | 4.563087 | 4.437082 | 4.846938 | pivot | 2.2527 | 2.0000 | 0.5000 | 0.5000 | 4.434864 | SL | -1.0000 | 36.00 | 0.3718 |  |
| 6 | TRAIN | BTC/USDT | base+ml | 2022-07-02T00:00:00Z | 2022-07-02T04:00:00Z | 2022-07-05T04:00:00Z | 1860.119219 | 1812.711234 | 1967.919787 | pivot | 2.2739 | 2.0000 | 1.0000 | 1.0000 | 1811.804878 | SL | -1.0000 | 72.00 | 0.3480 |  |
| 7 | TRAIN | BNB/USDT | base+ml | 2022-09-01T20:00:00Z | 2022-09-02T00:00:00Z | 2022-09-03T00:00:00Z | 4.125823 | 3.923190 | 4.559792 | pivot | 2.1416 | 2.0000 | 0.5000 | 0.5000 | 3.921229 | SL | -1.0000 | 24.00 | 0.3408 |  |
| 8 | TRAIN | BTC/USDT | base+ml | 2022-10-25T00:00:00Z | 2022-10-25T04:00:00Z | 2022-10-25T08:00:00Z | 1253.367134 | 1222.087414 | 1324.676393 | pivot | 2.2797 | 2.0000 | 1.0000 | 1.0000 | 1221.476370 | SL | -1.0000 | 4.00 | 0.3371 |  |
| 9 | TEST | BTC/USDT | base+ml | 2023-06-09T12:00:00Z | 2023-06-09T16:00:00Z | 2023-06-16T08:00:00Z | 1781.993817 | 1667.714350 | 2022.923133 | pivot | 2.1082 | 2.0000 | 1.0000 | 1.0000 | 1666.880492 | SL | -1.0000 | 160.00 | 0.3413 |  |
| 10 | TEST | BTC/USDT | base+ml | 2023-06-28T04:00:00Z | 2023-06-28T08:00:00Z | 2023-06-29T08:00:00Z | 1722.386840 | 1656.607010 | 1865.947775 | pivot | 2.1824 | 2.0000 | 1.0000 | 1.0000 | 1865.947775 | TP | 2.0000 | 24.00 | 0.3435 |  |
| 11 | TEST | ETH/USDT | base+ml | 2023-07-02T00:00:00Z | 2023-07-02T04:00:00Z | 2023-07-03T00:00:00Z | 8.393287 | 8.206278 | 8.825921 | pivot | 2.3134 | 2.0000 | 1.0000 | 1.0000 | 8.202175 | SL | -1.0000 | 20.00 | 0.3469 |  |
| 12 | TEST | BNB/USDT | base+ml | 2023-08-05T00:00:00Z | 2023-08-05T04:00:00Z | 2023-08-07T00:00:00Z | 2.774758 | 2.616695 | 3.110166 | pivot | 2.1220 | 2.0000 | 0.5000 | 0.5000 | 2.615387 | SL | -1.0000 | 44.00 | 0.3676 |  |
| 13 | TEST | ETH/USDT | base+ml | 2023-08-25T08:00:00Z | 2023-08-25T12:00:00Z | 2023-08-26T00:00:00Z | 8.219609 | 8.057326 | 8.601599 | pivot | 2.3539 | 2.0000 | 1.0000 | 1.0000 | 8.053298 | SL | -1.0000 | 12.00 | 0.3792 |  |
| 14 | TEST | BTC/USDT | base+ml | 2023-09-24T12:00:00Z | 2023-09-24T16:00:00Z | 2023-09-25T08:00:00Z | 1655.521130 | 1626.053366 | 1726.025781 | pivot | 2.3926 | 2.0000 | 1.0000 | 1.0000 | 1625.240340 | SL | -1.0000 | 16.00 | 0.3437 |  |
| 15 | TEST | BNB/USDT | base+ml | 2023-10-12T12:00:00Z | 2023-10-12T16:00:00Z | 2023-10-16T00:00:00Z | 2.152474 | 2.042970 | 2.386453 | pivot | 2.1367 | 2.0000 | 0.5000 | 0.5000 | 2.386453 | TP | 2.0000 | 80.00 | 0.3672 |  |
| 16 | TEST | ETH/USDT | base+ml | 2023-11-05T08:00:00Z | 2023-11-05T12:00:00Z | 2023-11-08T08:00:00Z | 7.580450 | 7.373817 | 8.046619 | pivot | 2.2560 | 2.0000 | 1.0000 | 1.0000 | 7.370130 | SL | -1.0000 | 68.00 | 0.3558 |  |
| 17 | TEST | BNB/USDT | base+ml | 2024-01-01T04:00:00Z | 2024-01-01T08:00:00Z | 2024-01-01T12:00:00Z | 2.097234 | 2.044333 | 2.217678 | pivot | 2.2767 | 2.0000 | 0.5000 | 0.5000 | 2.043311 | SL | -1.0000 | 4.00 | 0.3441 |  |
| 18 | TEST | ETH/USDT | base+ml | 2024-03-04T08:00:00Z | 2024-03-04T12:00:00Z | 2024-03-05T04:00:00Z | 7.928654 | 7.768580 | 8.304189 | pivot | 2.3460 | 2.0000 | 1.0000 | 1.0000 | 7.764696 | SL | -1.0000 | 16.00 | 0.4045 |  |
| 19 | TEST | BNB/USDT | base+ml | 2024-09-02T04:00:00Z | 2024-09-02T08:00:00Z | 2024-09-04T04:00:00Z | 1.865821 | 1.806872 | 1.996733 | pivot | 2.2207 | 2.0000 | 0.5000 | 0.5000 | 1.805969 | SL | -1.0000 | 44.00 | 0.3585 |  |
| 20 | TEST | BTC/USDT | base+ml | 2024-09-30T04:00:00Z | 2024-09-30T08:00:00Z | 2024-10-01T20:00:00Z | 1727.545964 | 1681.237114 | 1832.220553 | pivot | 2.2604 | 2.0000 | 1.0000 | 1.0000 | 1680.396496 | SL | -1.0000 | 36.00 | 0.3690 |  |
| 21 | TEST | BNB/USDT | base+ml | 2024-10-06T08:00:00Z | 2024-10-06T12:00:00Z | 2024-10-08T20:00:00Z | 1.970261 | 1.911764 | 2.101000 | pivot | 2.2350 | 2.0000 | 0.5000 | 0.5000 | 1.910808 | SL | -1.0000 | 56.00 | 0.3989 |  |
| 22 | TEST | ETH/USDT | base+ml | 2024-10-16T00:00:00Z | 2024-10-16T04:00:00Z | 2024-10-18T16:00:00Z | 9.051462 | 8.814804 | 9.587955 | pivot | 2.2670 | 2.0000 | 1.0000 | 1.0000 | 8.810397 | SL | -1.0000 | 60.00 | 0.3365 |  |
| 23 | TEST | ETH/USDT | base+ml | 2024-10-24T04:00:00Z | 2024-10-24T08:00:00Z | 2024-10-29T00:00:00Z | 8.980310 | 8.578733 | 9.845980 | pivot | 2.1557 | 2.0000 | 1.0000 | 1.0000 | 9.845980 | TP | 2.0000 | 112.00 | 0.3461 |  |
| 24 | TEST | BNB/USDT | base+ml | 2024-11-03T12:00:00Z | 2024-11-03T16:00:00Z | 2024-11-04T00:00:00Z | 1.911290 | 1.887028 | 1.973180 | pivot | 2.5509 | 2.0000 | 0.5000 | 0.5000 | 1.886084 | SL | -1.0000 | 8.00 | 0.3812 |  |
| 25 | TEST | BTC/USDT | base+ml | 2024-11-23T04:00:00Z | 2024-11-23T08:00:00Z | 2024-11-29T04:00:00Z | 1732.659125 | 1650.858823 | 1908.316948 | pivot | 2.1474 | 2.0000 | 1.0000 | 1.0000 | 1908.316948 | TP | 2.0000 | 140.00 | 0.3406 |  |

## Flagged trades (0)

None. Unflagged trades still require the full row-by-row review.

## Trade context (candles)

Candle context unavailable: no candles were supplied (`write_review_pack(..., data=...)`, or the CLI's `--data-dir` / `--synthetic`), so the MAE, MFE, stop-structure and wick columns of `trades_review.csv` are blank and no `context/` files were written. Judge each trade on a chart of its pair from 30 candles before the entry through the exit, and check the stop against the swing low it claims to sit behind.

## Rule denials

Signal candles denied by each rule (the first failing rule is counted), for context on how often the mandatory rules said no.

| rule | denied | meaning |
|---|---:|---|
| R1_trend | 13048 | Trend gate: close > EMA9 and close > EMA21 and EMA9 > EMA21 (4H) |
| R3_volume | 5217 | Volume: entry-candle volume >= 1.5x the previous-20-candle average |
| R2_momentum | 1138 | Momentum: RSI(14) within [50, 70] on the entry candle |
| L_ml_filter | 288 | Layer: logistic-regression veto; it can only veto an entry that passed every mandatory rule and never approves one a rule denies (a veto can free R6 budget or change R9 state, which may admit other rule-compliant trades) |
| R4_regime | 160 | Regime: close > EMA200 (unless explicitly testing it off) |
| R6_correlation_cap | 22 | BTC/ETH/BNB share one risk budget; BNB never stacks |
| R5_news_blackout | 10 | No entries within +/-2h of high-impact news; BNB +/-24h burns/launchpool |
| R8_structure_stop | 3 | Stop behind a confirmed swing low with a buffer (BNB 0.5-0.8%) |

## Reviewer sign-off

Complete by hand. APPROVE only if every row was inspected and every flag is explained in `reviewer_note` of `trades_review.csv`.

- Reviewer name: ______________________________
- Date (UTC): ______________________________
- Trades reviewed: ______ of 25
- Flagged trades explained: ______ of 0
- Decision: [ ] APPROVE    [ ] REJECT
- Notes: ____________________________________________________________
- Follow-ups required before adoption: ______________________________
