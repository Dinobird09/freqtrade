# Trade review: ML layer `base+ml`

**Human review pack.** The reviewer must inspect EVERY row of the trade table below (all 15 trades, also in `trades_review.csv`) before signing off: summary statistics alone are not a review. Automated flags only point at suspicious rows; an unflagged trade is not an approved trade. Record a verdict for every trade in the `reviewer_ok` (exactly Y or N; any other value is rejected when the sheet is read back) and `reviewer_note` columns of `trades_review.csv`.

- Trades: 15 (TRAIN 9, TEST 6)
- Closed trades: 15; open or missing exit: 0
- Flagged trades: 0 of 15
- Pairs: BNB/USDT 5, BTC/USDT 5, ETH/USDT 5
- Variants: base+ml 15
- Walk-forward split: 2023-03-14T00:00:00Z (TRAIN = signal candle before it, TEST = at or after it)
- Config: `rr2_vol1.5_rsi50-70`, reward:risk 2.00 (net of costs), risk caps BNB 0.50%, BTC 1.00%, ETH 1.00%
- Costs: fee 0.1000% per side, slippage 0.0500% on market fills (entry and stop), exchange `binance`. Risk is the ALL-IN loss at the stop, so a clean stop-out is -1R and a TP is +reward:risk R; `RR price` is the chart-distance ratio, `RR net` what a TP earns after both fees.
- Row key: (window, trade_id), unique in this pack; `trades_review.csv` is matched against the TRAIN and TEST journals by this key

## Summary per window

avg R is the expectancy per closed trade (the target metric), net of fees and slippage. Win rate is deliberately not shown: it is never a target.

| metric | TRAIN | TEST |
|---|---:|---:|
| trades | 9 | 6 |
| closed with R | 9 | 6 |
| avg R (expectancy) | +0.333 | +0.500 |
| total R | +3.000 | +3.000 |
| exits SL / TP / END | 5 / 4 / 0 | 3 / 3 / 0 |
| flagged | 0 | 0 |

## All trades (15)

| id | window | pair | variant | signal UTC | entry UTC | exit UTC | entry | stop | target | stop method | RR price | RR net | risk % | cap % | exit | reason | R | hold h | ml p | flags |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | TRAIN | BNB/USDT | base+ml | 2020-01-15T12:00:00Z | 2020-01-15T16:00:00Z | 2020-01-18T12:00:00Z | 4.241744 | 4.048516 | 4.657724 | pivot | 2.1528 | 2.0000 | 0.5000 | 0.5000 | 4.657724 | TP | 2.0000 | 68.00 | 0.3484 |  |
| 2 | TRAIN | BNB/USDT | base+ml | 2021-01-18T00:00:00Z | 2021-01-18T04:00:00Z | 2021-01-18T16:00:00Z | 3.546540 | 3.398744 | 3.866831 | pivot | 2.1671 | 2.0000 | 0.5000 | 0.5000 | 3.397045 | SL | -1.0000 | 12.00 | 0.3539 |  |
| 3 | TRAIN | BTC/USDT | base+ml | 2021-02-09T20:00:00Z | 2021-02-10T00:00:00Z | 2021-02-10T16:00:00Z | 1642.008104 | 1603.799564 | 1729.890895 | pivot | 2.3001 | 2.0000 | 1.0000 | 1.0000 | 1602.997664 | SL | -1.0000 | 16.00 | 0.3541 |  |
| 4 | TRAIN | BNB/USDT | base+ml | 2021-04-01T12:00:00Z | 2021-04-01T16:00:00Z | 2021-04-02T12:00:00Z | 3.850886 | 3.731351 | 4.116817 | pivot | 2.2247 | 2.0000 | 0.5000 | 0.5000 | 3.729485 | SL | -1.0000 | 20.00 | 0.3542 |  |
| 5 | TRAIN | BNB/USDT | base+ml | 2021-05-04T12:00:00Z | 2021-05-04T16:00:00Z | 2021-05-07T20:00:00Z | 3.943778 | 3.827561 | 4.203725 | pivot | 2.2367 | 2.0000 | 0.5000 | 0.5000 | 4.203725 | TP | 2.0000 | 76.00 | 0.3397 |  |
| 6 | TRAIN | ETH/USDT | base+ml | 2021-08-08T12:00:00Z | 2021-08-08T16:00:00Z | 2021-08-29T20:00:00Z | 24.223547 | 23.180252 | 26.478804 | pivot | 2.1617 | 2.0000 | 1.0000 | 1.0000 | 23.168662 | SL | -1.0000 | 508.00 | 0.3424 |  |
| 7 | TRAIN | BTC/USDT | base+ml | 2022-02-05T04:00:00Z | 2022-02-05T08:00:00Z | 2022-02-09T16:00:00Z | 1415.890592 | 1383.497255 | 1490.564612 | pivot | 2.3052 | 2.0000 | 1.0000 | 1.0000 | 1382.805506 | SL | -1.0000 | 104.00 | 0.3393 |  |
| 8 | TRAIN | BTC/USDT | base+ml | 2022-11-30T16:00:00Z | 2022-11-30T20:00:00Z | 2022-12-02T20:00:00Z | 1433.796818 | 1403.707222 | 1503.991110 | pivot | 2.3328 | 2.0000 | 1.0000 | 1.0000 | 1503.991110 | TP | 2.0000 | 48.00 | 0.3373 |  |
| 9 | TRAIN | ETH/USDT | base+ml | 2022-12-14T08:00:00Z | 2022-12-14T12:00:00Z | 2022-12-15T16:00:00Z | 10.913213 | 10.717597 | 11.380709 | pivot | 2.3899 | 2.0000 | 1.0000 | 1.0000 | 11.380709 | TP | 2.0000 | 28.00 | 0.3520 |  |
| 10 | TEST | ETH/USDT | base+ml | 2023-08-02T20:00:00Z | 2023-08-03T00:00:00Z | 2023-08-03T08:00:00Z | 8.610499 | 8.428248 | 9.035144 | pivot | 2.3300 | 2.0000 | 1.0000 | 1.0000 | 8.424034 | SL | -1.0000 | 8.00 | 0.3707 |  |
| 11 | TEST | BTC/USDT | base+ml | 2023-10-18T16:00:00Z | 2023-10-18T20:00:00Z | 2023-10-23T04:00:00Z | 1734.466187 | 1681.308273 | 1852.880538 | pivot | 2.2276 | 2.0000 | 1.0000 | 1.0000 | 1852.880538 | TP | 2.0000 | 104.00 | 0.3427 |  |
| 12 | TEST | ETH/USDT | base+ml | 2024-03-02T16:00:00Z | 2024-03-02T20:00:00Z | 2024-03-08T12:00:00Z | 7.311050 | 7.177396 | 7.629446 | pivot | 2.3822 | 2.0000 | 1.0000 | 1.0000 | 7.629446 | TP | 2.0000 | 136.00 | 0.3406 |  |
| 13 | TEST | ETH/USDT | base+ml | 2024-04-13T20:00:00Z | 2024-04-14T00:00:00Z | 2024-04-15T04:00:00Z | 8.113150 | 7.854677 | 8.686678 | pivot | 2.2189 | 2.0000 | 1.0000 | 1.0000 | 7.850750 | SL | -1.0000 | 28.00 | 0.3694 |  |
| 14 | TEST | BNB/USDT | base+ml | 2024-10-07T12:00:00Z | 2024-10-07T16:00:00Z | 2024-10-07T20:00:00Z | 2.034124 | 1.994814 | 2.126956 | pivot | 2.3615 | 2.0000 | 0.5000 | 0.5000 | 1.993817 | SL | -1.0000 | 4.00 | 0.3403 |  |
| 15 | TEST | BTC/USDT | base+ml | 2024-11-24T08:00:00Z | 2024-11-24T12:00:00Z | 2024-11-28T16:00:00Z | 1654.002162 | 1582.180360 | 1809.161893 | pivot | 2.1603 | 2.0000 | 1.0000 | 1.0000 | 1809.161893 | TP | 2.0000 | 100.00 | 0.3353 |  |

## Flagged trades (0)

None. Unflagged trades still require the full row-by-row review.

## Rule denials

Signal candles denied by each rule (the first failing rule is counted), for context on how often the mandatory rules said no.

| rule | denied | meaning |
|---|---:|---|
| R1_trend | 26366 | Trend gate: close > EMA9 and close > EMA21 and EMA9 > EMA21 (4H) |
| R3_volume | 9954 | Volume: entry-candle volume >= 1.5x the previous-20-candle average |
| R2_momentum | 2158 | Momentum: RSI(14) within [50, 70] on the entry candle |
| L_ml_filter | 533 | Layer: logistic-regression veto; it can only veto an entry that passed every mandatory rule and never approves one a rule denies (a veto can free R6 budget or change R9 state, which may admit other rule-compliant trades) |
| R4_regime | 339 | Regime: close > EMA200 (unless explicitly testing it off) |
| R6_correlation_cap | 32 | BTC/ETH/BNB share one risk budget; BNB never stacks |
| R5_news_blackout | 21 | No entries within +/-2h of high-impact news; BNB +/-24h burns/launchpool |
| R8_structure_stop | 2 | Stop behind a confirmed swing low with a buffer (BNB 0.5-0.8%) |

## Reviewer sign-off

Complete by hand. APPROVE only if every row was inspected and every flag is explained in `reviewer_note` of `trades_review.csv`.

- Reviewer name: ______________________________
- Date (UTC): ______________________________
- Trades reviewed: ______ of 15
- Flagged trades explained: ______ of 0
- Decision: [ ] APPROVE    [ ] REJECT
- Notes: ____________________________________________________________
- Follow-ups required before adoption: ______________________________
