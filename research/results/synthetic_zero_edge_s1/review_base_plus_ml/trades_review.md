# Trade review: ML layer `base+ml`

**Human review pack.** The reviewer must inspect EVERY row of the trade table below (all 6 trades, also in `trades_review.csv`) before signing off: summary statistics alone are not a review. Automated flags only point at suspicious rows; an unflagged trade is not an approved trade. Record a verdict for every trade in the `reviewer_ok` (exactly Y or N; any other value is rejected when the sheet is read back) and `reviewer_note` columns of `trades_review.csv`.

- Trades: 6 (TRAIN 0, TEST 6)
- Closed trades: 6; open or missing exit: 0
- Flagged trades: 0 of 6
- Pairs: BNB/USDT 1, BTC/USDT 2, ETH/USDT 3
- Variants: base+ml 6
- Walk-forward split: 2023-03-14T00:00:00Z (TRAIN = signal candle before it, TEST = at or after it)
- Config: `rr2_vol1.5_rsi50-70`, reward:risk 2.00 (net of costs), risk caps BNB 0.50%, BTC 1.00%, ETH 1.00%
- Costs: fee 0.1000% per side, slippage 0.0500% on market fills (entry and stop), exchange `binance`. Risk is the ALL-IN loss at the stop, so a clean stop-out is -1R and a TP is +reward:risk R; `RR price` is the chart-distance ratio, `RR net` what a TP earns after both fees.
- Row key: (window, trade_id), unique in this pack; `trades_review.csv` is matched against the TRAIN and TEST journals by this key
- Candle context: unavailable (no candles supplied, see the trade context section)

## Summary per window

avg R is the expectancy per closed trade (the target metric), net of fees and slippage. Win rate is deliberately not shown: it is never a target.

| metric | TRAIN | TEST |
|---|---:|---:|
| trades | 0 | 6 |
| closed with R | 0 | 6 |
| avg R (expectancy) | n/a | +0.500 |
| total R | n/a | +3.000 |
| exits SL / TP / END | 0 / 0 / 0 | 3 / 3 / 0 |
| flagged | 0 | 0 |

## All trades (6)

| id | window | pair | variant | signal UTC | entry UTC | exit UTC | entry | stop | target | stop method | RR price | RR net | risk % | cap % | exit | reason | R | hold h | ml p | flags |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | TEST | ETH/USDT | base+ml | 2023-08-02T20:00:00Z | 2023-08-03T00:00:00Z | 2023-08-03T08:00:00Z | 8.610499 | 8.428248 | 9.035144 | pivot | 2.3300 | 2.0000 | 1.0000 | 1.0000 | 8.424034 | SL | -1.0000 | 8.00 | 0.3707 |  |
| 2 | TEST | BTC/USDT | base+ml | 2023-10-18T16:00:00Z | 2023-10-18T20:00:00Z | 2023-10-23T04:00:00Z | 1734.466187 | 1681.308273 | 1852.880538 | pivot | 2.2276 | 2.0000 | 1.0000 | 1.0000 | 1852.880538 | TP | 2.0000 | 104.00 | 0.3427 |  |
| 3 | TEST | ETH/USDT | base+ml | 2024-03-02T16:00:00Z | 2024-03-02T20:00:00Z | 2024-03-08T12:00:00Z | 7.311050 | 7.177396 | 7.629446 | pivot | 2.3822 | 2.0000 | 1.0000 | 1.0000 | 7.629446 | TP | 2.0000 | 136.00 | 0.3406 |  |
| 4 | TEST | ETH/USDT | base+ml | 2024-04-13T20:00:00Z | 2024-04-14T00:00:00Z | 2024-04-15T04:00:00Z | 8.113150 | 7.854677 | 8.686678 | pivot | 2.2189 | 2.0000 | 1.0000 | 1.0000 | 7.850750 | SL | -1.0000 | 28.00 | 0.3694 |  |
| 5 | TEST | BNB/USDT | base+ml | 2024-10-07T12:00:00Z | 2024-10-07T16:00:00Z | 2024-10-07T20:00:00Z | 2.034124 | 1.994814 | 2.126956 | pivot | 2.3615 | 2.0000 | 0.5000 | 0.5000 | 1.993817 | SL | -1.0000 | 4.00 | 0.3403 |  |
| 6 | TEST | BTC/USDT | base+ml | 2024-11-24T08:00:00Z | 2024-11-24T12:00:00Z | 2024-11-28T16:00:00Z | 1654.002162 | 1582.180360 | 1809.161893 | pivot | 2.1603 | 2.0000 | 1.0000 | 1.0000 | 1809.161893 | TP | 2.0000 | 100.00 | 0.3353 |  |

## Flagged trades (0)

None. Unflagged trades still require the full row-by-row review.

## Trade context (candles)

Candle context unavailable: no candles were supplied (`write_review_pack(..., data=...)`, or the CLI's `--data-dir` / `--synthetic`), so the MAE, MFE, stop-structure and wick columns of `trades_review.csv` are blank and no `context/` files were written. Judge each trade on a chart of its pair from 30 candles before the entry through the exit, and check the stop against the swing low it claims to sit behind.

## Rule denials

Signal candles denied by each rule (the first failing rule is counted), for context on how often the mandatory rules said no.

| rule | denied | meaning |
|---|---:|---|
| R1_trend | 12523 | Trend gate: close > EMA9 and close > EMA21 and EMA9 > EMA21 (4H) |
| R3_volume | 4923 | Volume: entry-candle volume >= 1.5x the previous-20-candle average |
| R2_momentum | 1142 | Momentum: RSI(14) within [50, 70] on the entry candle |
| L_ml_filter | 308 | Layer: logistic-regression veto; it can only veto an entry that passed every mandatory rule and never approves one a rule denies (a veto can free R6 budget or change R9 state, which may admit other rule-compliant trades) |
| R4_regime | 136 | Regime: close > EMA200 (unless explicitly testing it off) |
| R5_news_blackout | 11 | No entries within +/-2h of high-impact news; BNB +/-24h burns/launchpool |
| R6_correlation_cap | 8 | BTC/ETH/BNB share one risk budget; BNB never stacks |
| R8_structure_stop | 2 | Stop behind a confirmed swing low with a buffer (BNB 0.5-0.8%) |

## Reviewer sign-off

Complete by hand. APPROVE only if every row was inspected and every flag is explained in `reviewer_note` of `trades_review.csv`.

- Reviewer name: ______________________________
- Date (UTC): ______________________________
- Trades reviewed: ______ of 6
- Flagged trades explained: ______ of 0
- Decision: [ ] APPROVE    [ ] REJECT
- Notes: ____________________________________________________________
- Follow-ups required before adoption: ______________________________
