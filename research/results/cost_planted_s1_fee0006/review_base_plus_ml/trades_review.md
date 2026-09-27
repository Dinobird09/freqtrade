# Trade review: ML layer `base+ml`

**Human review pack.** The reviewer must inspect EVERY row of the trade table below (all 39 trades, also in `trades_review.csv`) before signing off: summary statistics alone are not a review. Automated flags only point at suspicious rows; an unflagged trade is not an approved trade. Record a verdict for every trade in the `reviewer_ok` (exactly Y or N; any other value is rejected when the sheet is read back) and `reviewer_note` columns of `trades_review.csv`.

- Trades: 39 (TRAIN 14, TEST 25)
- Closed trades: 39; open or missing exit: 0
- Flagged trades: 1 of 39
- Pairs: BNB/USDT 8, BTC/USDT 18, ETH/USDT 13
- Variants: base+ml 39
- Walk-forward split: 2023-03-14T00:00:00Z (TRAIN = signal candle before it, TEST = at or after it)
- Config: `rr2_vol1.5_rsi50-70`, reward:risk 2.00 (net of costs), risk caps BNB 0.50%, BTC 1.00%, ETH 1.00%
- Costs: fee 0.6000% per side, slippage 0.0500% on market fills (entry and stop), exchange `coinbase`. Risk is the ALL-IN loss at the stop, so a clean stop-out is -1R and a TP is +reward:risk R; `RR price` is the chart-distance ratio, `RR net` what a TP earns after both fees.
- Row key: (window, trade_id), unique in this pack; `trades_review.csv` is matched against the TRAIN and TEST journals by this key
- Candle context: unavailable (no candles supplied, see the trade context section)

## Summary per window

avg R is the expectancy per closed trade (the target metric), net of fees and slippage. Win rate is deliberately not shown: it is never a target.

| metric | TRAIN | TEST |
|---|---:|---:|
| trades | 14 | 25 |
| closed with R | 14 | 25 |
| avg R (expectancy) | +0.409 | -0.040 |
| total R | +5.733 | -1.000 |
| exits SL / TP / END | 7 / 6 / 1 | 17 / 8 / 0 |
| flagged | 1 | 0 |

## All trades (39)

| id | window | pair | variant | signal UTC | entry UTC | exit UTC | entry | stop | target | stop method | RR price | RR net | risk % | cap % | exit | reason | R | hold h | ml p | flags |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | TRAIN | BTC/USDT | base+ml | 2022-02-13T00:00:00Z | 2022-02-13T04:00:00Z | 2022-03-21T04:00:00Z | 1077.900108 | 1001.994376 | 1269.752201 | pivot | 2.5275 | 2.0000 | 1.0000 | 1.0000 | 1269.752201 | TP | 2.0000 | 864.00 | 0.6256 |  |
| 2 | TRAIN | BTC/USDT | base+ml | 2022-04-08T04:00:00Z | 2022-04-08T08:00:00Z | 2022-04-15T04:00:00Z | 1462.198221 | 1417.554941 | 1605.859213 | pivot | 3.2180 | 2.0000 | 1.0000 | 1.0000 | 1416.846163 | SL | -1.0000 | 164.00 | 0.5005 |  |
| 3 | TRAIN | BNB/USDT | base+ml | 2022-04-27T20:00:00Z | 2022-04-28T00:00:00Z | 2022-05-01T04:00:00Z | 7.573691 | 7.378888 | 8.244973 | pivot | 3.4460 | 2.0000 | 0.5000 | 0.5000 | 8.244973 | TP | 2.0000 | 76.00 | 0.3985 |  |
| 4 | TRAIN | ETH/USDT | base+ml | 2022-05-15T00:00:00Z | 2022-05-15T04:00:00Z | 2022-05-15T16:00:00Z | 36.969562 | 35.735671 | 40.812017 | pivot | 3.1141 | 2.0000 | 1.0000 | 1.0000 | 35.717804 | SL | -1.0000 | 12.00 | 0.7760 |  |
| 5 | TRAIN | BTC/USDT | base+ml | 2022-05-25T04:00:00Z | 2022-05-25T08:00:00Z | 2022-05-30T08:00:00Z | 1573.467546 | 1516.592340 | 1745.721303 | pivot | 3.0286 | 2.0000 | 1.0000 | 1.0000 | 1515.834043 | SL | -1.0000 | 120.00 | 0.5356 |  |
| 6 | TRAIN | BTC/USDT | base+ml | 2022-06-20T12:00:00Z | 2022-06-20T16:00:00Z | 2022-06-23T00:00:00Z | 1559.341840 | 1513.591455 | 1708.831358 | pivot | 3.2675 | 2.0000 | 1.0000 | 1.0000 | 1708.831358 | TP | 2.0000 | 56.00 | 0.3825 |  |
| 7 | TRAIN | BTC/USDT | base+ml | 2022-06-30T04:00:00Z | 2022-06-30T08:00:00Z | 2022-07-03T20:00:00Z | 1801.305263 | 1749.552288 | 1971.799185 | pivot | 3.2944 | 2.0000 | 1.0000 | 1.0000 | 1971.799185 | TP | 2.0000 | 84.00 | 0.3476 |  |
| 8 | TRAIN | BTC/USDT | base+ml | 2022-10-04T04:00:00Z | 2022-10-04T08:00:00Z | 2022-10-08T04:00:00Z | 1822.385045 | 1761.085802 | 2012.746492 | pivot | 3.1054 | 2.0000 | 1.0000 | 1.0000 | 1760.205259 | SL | -1.0000 | 92.00 | 0.3936 |  |
| 9 | TRAIN | BTC/USDT | base+ml | 2022-10-27T04:00:00Z | 2022-10-27T08:00:00Z | 2022-11-06T04:00:00Z | 1711.253250 | 1642.901356 | 1911.576917 | pivot | 2.9308 | 2.0000 | 1.0000 | 1.0000 | 1911.576917 | TP | 2.0000 | 236.00 | 0.4182 |  |
| 10 | TRAIN | ETH/USDT | base+ml | 2022-11-20T16:00:00Z | 2022-11-20T20:00:00Z | 2022-12-09T00:00:00Z | 13.721885 | 13.173787 | 15.328223 | pivot | 2.9308 | 2.0000 | 1.0000 | 1.0000 | 15.328223 | TP | 2.0000 | 436.00 | 0.3532 |  |
| 11 | TRAIN | ETH/USDT | base+ml | 2023-01-06T16:00:00Z | 2023-01-06T20:00:00Z | 2023-01-07T12:00:00Z | 15.759665 | 15.471143 | 16.922953 | pivot | 4.0319 | 2.0000 | 1.0000 | 1.0000 | 15.463407 | SL | -1.0000 | 16.00 | 0.3996 |  |
| 12 | TRAIN | BTC/USDT | base+ml | 2023-01-25T04:00:00Z | 2023-01-25T08:00:00Z | 2023-01-30T04:00:00Z | 1928.880571 | 1849.955274 | 2158.439972 | pivot | 2.9086 | 2.0000 | 1.0000 | 1.0000 | 1849.030296 | SL | -1.0000 | 116.00 | 0.3443 |  |
| 13 | TRAIN | BTC/USDT | base+ml | 2023-02-06T00:00:00Z | 2023-02-06T04:00:00Z | 2023-02-07T16:00:00Z | 1900.064225 | 1856.101550 | 2058.660879 | pivot | 3.6075 | 2.0000 | 1.0000 | 1.0000 | 1855.173499 | SL | -1.0000 | 36.00 | 0.6447 |  |
| 14 | TRAIN | ETH/USDT | base+ml | 2023-03-06T08:00:00Z | 2023-03-06T12:00:00Z | 2023-03-13T20:00:00Z | 13.901084 | 13.023282 | 16.173171 | pivot | 2.5884 | 2.0000 | 1.0000 | 1.0000 | 14.840090 | END | 0.7330 | 176.00 | 0.4487 | window_end |
| 15 | TEST | ETH/USDT | base+ml | 2023-03-21T04:00:00Z | 2023-03-21T08:00:00Z | 2023-03-26T08:00:00Z | 15.765938 | 15.313624 | 17.256880 | pivot | 3.2963 | 2.0000 | 1.0000 | 1.0000 | 15.305968 | SL | -1.0000 | 120.00 | 0.5047 |  |
| 16 | TEST | BNB/USDT | base+ml | 2023-03-27T16:00:00Z | 2023-03-27T20:00:00Z | 2023-03-29T08:00:00Z | 3.995411 | 3.900223 | 4.334391 | pivot | 3.5612 | 2.0000 | 0.5000 | 0.5000 | 4.334391 | TP | 2.0000 | 36.00 | 0.3903 |  |
| 17 | TEST | BTC/USDT | base+ml | 2023-04-20T00:00:00Z | 2023-04-20T04:00:00Z | 2023-04-23T00:00:00Z | 2306.436519 | 2227.712650 | 2549.644882 | pivot | 3.0894 | 2.0000 | 1.0000 | 1.0000 | 2226.598794 | SL | -1.0000 | 68.00 | 0.6481 |  |
| 18 | TEST | BNB/USDT | base+ml | 2023-07-04T20:00:00Z | 2023-07-05T00:00:00Z | 2023-07-07T16:00:00Z | 3.270700 | 3.203698 | 3.526362 | pivot | 3.8158 | 2.0000 | 0.5000 | 0.5000 | 3.202096 | SL | -1.0000 | 64.00 | 0.3598 |  |
| 19 | TEST | BNB/USDT | base+ml | 2023-07-11T04:00:00Z | 2023-07-11T08:00:00Z | 2023-07-24T16:00:00Z | 3.297682 | 3.176579 | 3.662498 | pivot | 3.0124 | 2.0000 | 0.5000 | 0.5000 | 3.174991 | SL | -1.0000 | 320.00 | 0.3969 |  |
| 20 | TEST | BTC/USDT | base+ml | 2023-07-28T20:00:00Z | 2023-07-29T00:00:00Z | 2023-08-01T04:00:00Z | 2359.472461 | 2260.403650 | 2645.324217 | pivot | 2.8854 | 2.0000 | 1.0000 | 1.0000 | 2259.273448 | SL | -1.0000 | 76.00 | 0.4049 |  |
| 21 | TEST | ETH/USDT | base+ml | 2023-08-02T20:00:00Z | 2023-08-03T00:00:00Z | 2023-08-06T08:00:00Z | 8.970406 | 8.682199 | 9.880384 | pivot | 3.1574 | 2.0000 | 1.0000 | 1.0000 | 8.677858 | SL | -1.0000 | 80.00 | 0.6392 |  |
| 22 | TEST | ETH/USDT | base+ml | 2023-08-29T00:00:00Z | 2023-08-29T04:00:00Z | 2023-08-30T20:00:00Z | 8.540407 | 8.295170 | 9.348488 | pivot | 3.2951 | 2.0000 | 1.0000 | 1.0000 | 8.291023 | SL | -1.0000 | 40.00 | 0.6491 |  |
| 23 | TEST | BTC/USDT | base+ml | 2023-09-16T04:00:00Z | 2023-09-16T08:00:00Z | 2023-10-06T08:00:00Z | 2075.444402 | 1961.699268 | 2380.063370 | pivot | 2.6781 | 2.0000 | 1.0000 | 1.0000 | 1960.718419 | SL | -1.0000 | 480.00 | 0.3646 |  |
| 24 | TEST | BNB/USDT | base+ml | 2023-10-13T00:00:00Z | 2023-10-13T04:00:00Z | 2023-10-16T00:00:00Z | 2.744530 | 2.596851 | 3.141886 | pivot | 2.6907 | 2.0000 | 0.5000 | 0.5000 | 3.141886 | TP | 2.0000 | 68.00 | 0.6739 |  |
| 25 | TEST | BTC/USDT | base+ml | 2023-10-19T20:00:00Z | 2023-10-20T00:00:00Z | 2023-10-24T16:00:00Z | 2262.604797 | 2185.783530 | 2500.378560 | pivot | 3.0952 | 2.0000 | 1.0000 | 1.0000 | 2500.378560 | TP | 2.0000 | 112.00 | 0.4917 |  |
| 26 | TEST | BNB/USDT | base+ml | 2023-10-30T16:00:00Z | 2023-10-30T20:00:00Z | 2023-11-24T20:00:00Z | 3.415321 | 3.157512 | 4.057789 | pivot | 2.4920 | 2.0000 | 0.5000 | 0.5000 | 3.155933 | SL | -1.0000 | 600.00 | 0.4902 |  |
| 27 | TEST | ETH/USDT | base+ml | 2024-04-16T00:00:00Z | 2024-04-16T04:00:00Z | 2024-04-21T12:00:00Z | 7.507000 | 7.007299 | 8.785293 | pivot | 2.5581 | 2.0000 | 1.0000 | 1.0000 | 8.785293 | TP | 2.0000 | 128.00 | 0.6178 |  |
| 28 | TEST | BNB/USDT | base+ml | 2024-05-22T00:00:00Z | 2024-05-22T04:00:00Z | 2024-05-24T08:00:00Z | 3.027401 | 2.861395 | 3.471920 | pivot | 2.6777 | 2.0000 | 0.5000 | 0.5000 | 2.859964 | SL | -1.0000 | 52.00 | 0.5923 |  |
| 29 | TEST | ETH/USDT | base+ml | 2024-05-25T20:00:00Z | 2024-05-26T00:00:00Z | 2024-06-06T16:00:00Z | 10.102669 | 9.308358 | 12.066489 | pivot | 2.4724 | 2.0000 | 1.0000 | 1.0000 | 9.303704 | SL | -1.0000 | 280.00 | 0.5618 |  |
| 30 | TEST | BNB/USDT | base+ml | 2024-07-07T00:00:00Z | 2024-07-07T04:00:00Z | 2024-07-08T04:00:00Z | 2.787035 | 2.693408 | 3.077921 | pivot | 3.1069 | 2.0000 | 0.5000 | 0.5000 | 2.692062 | SL | -1.0000 | 24.00 | 0.4546 |  |
| 31 | TEST | BTC/USDT | base+ml | 2024-07-12T20:00:00Z | 2024-07-13T00:00:00Z | 2024-07-13T20:00:00Z | 1005.113903 | 980.786246 | 1091.152521 | pivot | 3.5367 | 2.0000 | 1.0000 | 1.0000 | 980.295852 | SL | -1.0000 | 20.00 | 0.6938 |  |
| 32 | TEST | ETH/USDT | base+ml | 2024-07-18T20:00:00Z | 2024-07-19T00:00:00Z | 2024-07-22T12:00:00Z | 9.199124 | 8.498987 | 10.941063 | pivot | 2.4880 | 2.0000 | 1.0000 | 1.0000 | 8.494738 | SL | -1.0000 | 84.00 | 0.3827 |  |
| 33 | TEST | ETH/USDT | base+ml | 2024-08-12T00:00:00Z | 2024-08-12T04:00:00Z | 2024-08-25T16:00:00Z | 8.464737 | 7.947796 | 9.813135 | pivot | 2.6084 | 2.0000 | 1.0000 | 1.0000 | 9.813135 | TP | 2.0000 | 324.00 | 0.5446 |  |
| 34 | TEST | BTC/USDT | base+ml | 2024-08-27T20:00:00Z | 2024-08-28T00:00:00Z | 2024-08-30T08:00:00Z | 952.776251 | 930.624860 | 1032.516644 | pivot | 3.5998 | 2.0000 | 1.0000 | 1.0000 | 930.159548 | SL | -1.0000 | 56.00 | 0.3457 |  |
| 35 | TEST | ETH/USDT | base+ml | 2024-09-02T20:00:00Z | 2024-09-03T00:00:00Z | 2024-09-05T04:00:00Z | 9.747979 | 9.534947 | 10.536622 | pivot | 3.7020 | 2.0000 | 1.0000 | 1.0000 | 10.536622 | TP | 2.0000 | 52.00 | 0.6316 |  |
| 36 | TEST | BTC/USDT | base+ml | 2024-09-28T00:00:00Z | 2024-09-28T04:00:00Z | 2024-10-01T20:00:00Z | 1061.585243 | 1026.747579 | 1170.735075 | pivot | 3.1331 | 2.0000 | 1.0000 | 1.0000 | 1026.234205 | SL | -1.0000 | 88.00 | 0.5115 |  |
| 37 | TEST | BTC/USDT | base+ml | 2024-10-18T00:00:00Z | 2024-10-18T04:00:00Z | 2024-10-26T12:00:00Z | 1047.499802 | 987.792435 | 1205.839947 | pivot | 2.6519 | 2.0000 | 1.0000 | 1.0000 | 1205.839947 | TP | 2.0000 | 200.00 | 0.6505 |  |
| 38 | TEST | BTC/USDT | base+ml | 2024-11-23T04:00:00Z | 2024-11-23T08:00:00Z | 2024-11-29T16:00:00Z | 1635.566872 | 1534.309555 | 1898.851638 | pivot | 2.6002 | 2.0000 | 1.0000 | 1.0000 | 1898.851638 | TP | 2.0000 | 152.00 | 0.3640 |  |
| 39 | TEST | ETH/USDT | base+ml | 2024-12-14T20:00:00Z | 2024-12-15T00:00:00Z | 2024-12-18T08:00:00Z | 13.123570 | 12.409498 | 15.039423 | pivot | 2.6830 | 2.0000 | 1.0000 | 1.0000 | 12.403294 | SL | -1.0000 | 80.00 | 0.7466 |  |

## Flagged trades (1)

- **#14** ETH/USDT (TRAIN, entry 2023-03-06T12:00:00Z): window_end: window-end forced exit (window-boundary artifact, not an SL/TP outcome)

## Trade context (candles)

Candle context unavailable: no candles were supplied (`write_review_pack(..., data=...)`, or the CLI's `--data-dir` / `--synthetic`), so the MAE, MFE, stop-structure and wick columns of `trades_review.csv` are blank and no `context/` files were written. Judge each trade on a chart of its pair from 30 candles before the entry through the exit, and check the stop against the swing low it claims to sit behind.

## Rule denials

Signal candles denied by each rule (the first failing rule is counted), for context on how often the mandatory rules said no.

| rule | denied | meaning |
|---|---:|---|
| R1_trend | 12491 | Trend gate: close > EMA9 and close > EMA21 and EMA9 > EMA21 (4H) |
| R3_volume | 3705 | Volume: entry-candle volume >= 1.5x the previous-20-candle average |
| R2_momentum | 2531 | Momentum: RSI(14) within [50, 70] on the entry candle |
| R4_regime | 110 | Regime: close > EMA200 (unless explicitly testing it off) |
| R6_correlation_cap | 81 | BTC/ETH/BNB share one risk budget; BNB never stacks |
| L_ml_filter | 73 | Layer: logistic-regression veto; it can only veto an entry that passed every mandatory rule and never approves one a rule denies (a veto can free R6 budget or change R9 state, which may admit other rule-compliant trades) |
| R5_news_blackout | 9 | No entries within +/-2h of high-impact news; BNB +/-24h burns/launchpool |
| R8_structure_stop | 2 | Stop behind a confirmed swing low with a buffer (BNB 0.5-0.8%) |

## Reviewer sign-off

Complete by hand. APPROVE only if every row was inspected and every flag is explained in `reviewer_note` of `trades_review.csv`.

- Reviewer name: ______________________________
- Date (UTC): ______________________________
- Trades reviewed: ______ of 39
- Flagged trades explained: ______ of 1
- Decision: [ ] APPROVE    [ ] REJECT
- Notes: ____________________________________________________________
- Follow-ups required before adoption: ______________________________
