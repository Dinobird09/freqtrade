# Trade review: ML layer `base+ml`

**Human review pack.** The reviewer must inspect EVERY row of the trade table below (all 52 trades, also in `trades_review.csv`) before signing off: summary statistics alone are not a review. Automated flags only point at suspicious rows; an unflagged trade is not an approved trade. Record a verdict for every trade in the `reviewer_ok` (exactly Y or N; any other value is rejected when the sheet is read back) and `reviewer_note` columns of `trades_review.csv`.

- Trades: 52 (TRAIN 18, TEST 34)
- Closed trades: 52; open or missing exit: 0
- Flagged trades: 1 of 52
- Pairs: BNB/USDT 10, BTC/USDT 24, ETH/USDT 18
- Variants: base+ml 52
- Walk-forward split: 2023-03-14T00:00:00Z (TRAIN = signal candle before it, TEST = at or after it)
- Config: `rr2_vol1.5_rsi50-70`, reward:risk 2.00 (net of costs), risk caps BNB 0.50%, BTC 1.00%, ETH 1.00%
- Costs: fee 0.1000% per side, slippage 0.0500% on market fills (entry and stop), exchange `binance`. Risk is the ALL-IN loss at the stop, so a clean stop-out is -1R and a TP is +reward:risk R; `RR price` is the chart-distance ratio, `RR net` what a TP earns after both fees.
- Row key: (window, trade_id), unique in this pack; `trades_review.csv` is matched against the TRAIN and TEST journals by this key
- Candle context: unavailable (no candles supplied, see the trade context section)

## Summary per window

avg R is the expectancy per closed trade (the target metric), net of fees and slippage. Win rate is deliberately not shown: it is never a target.

| metric | TRAIN | TEST |
|---|---:|---:|
| trades | 18 | 34 |
| closed with R | 18 | 34 |
| avg R (expectancy) | +0.278 | +0.235 |
| total R | +4.999 | +8.000 |
| exits SL / TP / END | 10 / 7 / 1 | 20 / 14 / 0 |
| flagged | 1 | 0 |

## All trades (52)

| id | window | pair | variant | signal UTC | entry UTC | exit UTC | entry | stop | target | stop method | RR price | RR net | risk % | cap % | exit | reason | R | hold h | ml p | flags |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | TRAIN | BTC/USDT | base+ml | 2022-02-13T00:00:00Z | 2022-02-13T04:00:00Z | 2022-03-20T20:00:00Z | 1077.900108 | 1001.994376 | 1237.187440 | pivot | 2.0985 | 2.0000 | 1.0000 | 1.0000 | 1237.187440 | TP | 2.0000 | 856.00 | 0.6897 |  |
| 2 | TRAIN | BTC/USDT | base+ml | 2022-04-08T04:00:00Z | 2022-04-08T08:00:00Z | 2022-04-15T04:00:00Z | 1462.198221 | 1417.554941 | 1561.684307 | pivot | 2.2285 | 2.0000 | 1.0000 | 1.0000 | 1416.846163 | SL | -1.0000 | 164.00 | 0.6040 |  |
| 3 | TRAIN | BNB/USDT | base+ml | 2022-04-27T20:00:00Z | 2022-04-28T00:00:00Z | 2022-04-29T04:00:00Z | 7.573691 | 7.378888 | 8.016162 | pivot | 2.2714 | 2.0000 | 0.5000 | 0.5000 | 8.016162 | TP | 2.0000 | 28.00 | 0.4626 |  |
| 4 | TRAIN | ETH/USDT | base+ml | 2022-05-15T00:00:00Z | 2022-05-15T04:00:00Z | 2022-05-15T16:00:00Z | 36.969562 | 35.735671 | 39.695119 | pivot | 2.2089 | 2.0000 | 1.0000 | 1.0000 | 35.717804 | SL | -1.0000 | 12.00 | 0.8401 |  |
| 5 | TRAIN | BTC/USDT | base+ml | 2022-05-19T04:00:00Z | 2022-05-19T08:00:00Z | 2022-05-19T12:00:00Z | 1522.524398 | 1497.816970 | 1582.581363 | pivot | 2.4307 | 2.0000 | 1.0000 | 1.0000 | 1497.068061 | SL | -1.0000 | 4.00 | 0.4370 |  |
| 6 | TRAIN | BTC/USDT | base+ml | 2022-05-25T04:00:00Z | 2022-05-25T08:00:00Z | 2022-05-30T08:00:00Z | 1573.467546 | 1516.592340 | 1698.184807 | pivot | 2.1928 | 2.0000 | 1.0000 | 1.0000 | 1515.834043 | SL | -1.0000 | 120.00 | 0.6654 |  |
| 7 | TRAIN | BTC/USDT | base+ml | 2022-06-20T12:00:00Z | 2022-06-20T16:00:00Z | 2022-06-22T08:00:00Z | 1559.341840 | 1513.591455 | 1661.721618 | pivot | 2.2378 | 2.0000 | 1.0000 | 1.0000 | 1661.721618 | TP | 2.0000 | 40.00 | 0.5496 |  |
| 8 | TRAIN | BTC/USDT | base+ml | 2022-06-29T08:00:00Z | 2022-06-29T12:00:00Z | 2022-07-02T08:00:00Z | 1773.491826 | 1708.053079 | 1916.728976 | pivot | 2.1889 | 2.0000 | 1.0000 | 1.0000 | 1916.728976 | TP | 2.0000 | 68.00 | 0.4584 |  |
| 9 | TRAIN | BTC/USDT | base+ml | 2022-10-04T04:00:00Z | 2022-10-04T08:00:00Z | 2022-10-08T04:00:00Z | 1822.385045 | 1761.085802 | 1957.689875 | pivot | 2.2073 | 2.0000 | 1.0000 | 1.0000 | 1760.205259 | SL | -1.0000 | 92.00 | 0.4779 |  |
| 10 | TRAIN | BTC/USDT | base+ml | 2022-10-26T16:00:00Z | 2022-10-26T20:00:00Z | 2022-11-17T08:00:00Z | 1727.640301 | 1613.577691 | 1967.755316 | pivot | 2.1051 | 2.0000 | 1.0000 | 1.0000 | 1967.755316 | TP | 2.0000 | 516.00 | 0.3998 |  |
| 11 | TRAIN | ETH/USDT | base+ml | 2022-11-17T16:00:00Z | 2022-11-17T20:00:00Z | 2022-11-18T20:00:00Z | 13.829625 | 13.379549 | 14.826219 | pivot | 2.2143 | 2.0000 | 1.0000 | 1.0000 | 13.372859 | SL | -1.0000 | 24.00 | 0.3364 |  |
| 12 | TRAIN | ETH/USDT | base+ml | 2022-11-20T16:00:00Z | 2022-11-20T20:00:00Z | 2022-11-26T12:00:00Z | 13.721885 | 13.173787 | 14.913667 | pivot | 2.1744 | 2.0000 | 1.0000 | 1.0000 | 14.913667 | TP | 2.0000 | 136.00 | 0.4509 |  |
| 13 | TRAIN | BTC/USDT | base+ml | 2022-11-30T16:00:00Z | 2022-11-30T20:00:00Z | 2022-12-07T12:00:00Z | 1813.172862 | 1718.445558 | 2015.235842 | pivot | 2.1331 | 2.0000 | 1.0000 | 1.0000 | 2015.235842 | TP | 2.0000 | 160.00 | 0.4434 |  |
| 14 | TRAIN | ETH/USDT | base+ml | 2022-12-29T00:00:00Z | 2022-12-29T04:00:00Z | 2023-01-02T12:00:00Z | 15.842544 | 15.338017 | 16.962085 | pivot | 2.2190 | 2.0000 | 1.0000 | 1.0000 | 15.330348 | SL | -1.0000 | 104.00 | 0.3817 |  |
| 15 | TRAIN | ETH/USDT | base+ml | 2023-01-06T16:00:00Z | 2023-01-06T20:00:00Z | 2023-01-07T12:00:00Z | 15.759665 | 15.471143 | 16.446833 | pivot | 2.3817 | 2.0000 | 1.0000 | 1.0000 | 15.463407 | SL | -1.0000 | 16.00 | 0.5271 |  |
| 16 | TRAIN | BTC/USDT | base+ml | 2023-01-25T04:00:00Z | 2023-01-25T08:00:00Z | 2023-01-30T04:00:00Z | 1928.880571 | 1849.955274 | 2100.165987 | pivot | 2.1702 | 2.0000 | 1.0000 | 1.0000 | 1849.030296 | SL | -1.0000 | 116.00 | 0.4613 |  |
| 17 | TRAIN | BTC/USDT | base+ml | 2023-02-06T00:00:00Z | 2023-02-06T04:00:00Z | 2023-02-07T16:00:00Z | 1900.064225 | 1856.101550 | 2001.257473 | pivot | 2.3018 | 2.0000 | 1.0000 | 1.0000 | 1855.173499 | SL | -1.0000 | 36.00 | 0.7426 |  |
| 18 | TRAIN | ETH/USDT | base+ml | 2023-03-06T08:00:00Z | 2023-03-06T12:00:00Z | 2023-03-13T20:00:00Z | 13.901084 | 13.023282 | 15.753201 | pivot | 2.1099 | 2.0000 | 1.0000 | 1.0000 | 14.840090 | END | 0.9989 | 176.00 | 0.6187 | window_end |
| 19 | TEST | ETH/USDT | base+ml | 2023-03-21T04:00:00Z | 2023-03-21T08:00:00Z | 2023-03-26T08:00:00Z | 15.765938 | 15.313624 | 16.780570 | pivot | 2.2432 | 2.0000 | 1.0000 | 1.0000 | 15.305968 | SL | -1.0000 | 120.00 | 0.5881 |  |
| 20 | TEST | BNB/USDT | base+ml | 2023-03-27T16:00:00Z | 2023-03-27T20:00:00Z | 2023-03-28T20:00:00Z | 3.995411 | 3.900223 | 4.213684 | pivot | 2.2931 | 2.0000 | 0.5000 | 0.5000 | 4.213684 | TP | 2.0000 | 24.00 | 0.4866 |  |
| 21 | TEST | BTC/USDT | base+ml | 2023-04-20T00:00:00Z | 2023-04-20T04:00:00Z | 2023-04-23T00:00:00Z | 2306.436519 | 2227.712650 | 2479.964442 | pivot | 2.2043 | 2.0000 | 1.0000 | 1.0000 | 2226.598794 | SL | -1.0000 | 68.00 | 0.6710 |  |
| 22 | TEST | BNB/USDT | base+ml | 2023-05-24T04:00:00Z | 2023-05-24T08:00:00Z | 2023-05-24T16:00:00Z | 4.406391 | 4.320697 | 4.608566 | pivot | 2.3592 | 2.0000 | 0.5000 | 0.5000 | 4.318536 | SL | -1.0000 | 8.00 | 0.3623 |  |
| 23 | TEST | BTC/USDT | base+ml | 2023-06-27T16:00:00Z | 2023-06-27T20:00:00Z | 2023-06-28T12:00:00Z | 1755.133521 | 1700.487943 | 1876.666508 | pivot | 2.2240 | 2.0000 | 1.0000 | 1.0000 | 1876.666508 | TP | 2.0000 | 16.00 | 0.4363 |  |
| 24 | TEST | BNB/USDT | base+ml | 2023-07-04T20:00:00Z | 2023-07-05T00:00:00Z | 2023-07-07T16:00:00Z | 3.270700 | 3.203698 | 3.427550 | pivot | 2.3410 | 2.0000 | 0.5000 | 0.5000 | 3.202096 | SL | -1.0000 | 64.00 | 0.3988 |  |
| 25 | TEST | BNB/USDT | base+ml | 2023-07-11T04:00:00Z | 2023-07-11T08:00:00Z | 2023-07-17T16:00:00Z | 3.297682 | 3.176579 | 3.562871 | pivot | 2.1898 | 2.0000 | 0.5000 | 0.5000 | 3.562871 | TP | 2.0000 | 152.00 | 0.4778 |  |
| 26 | TEST | BTC/USDT | base+ml | 2023-07-18T00:00:00Z | 2023-07-18T04:00:00Z | 2023-07-21T12:00:00Z | 2157.798646 | 2083.176552 | 2322.085761 | pivot | 2.2016 | 2.0000 | 1.0000 | 1.0000 | 2322.085761 | TP | 2.0000 | 80.00 | 0.6140 |  |
| 27 | TEST | ETH/USDT | base+ml | 2023-07-21T20:00:00Z | 2023-07-22T00:00:00Z | 2023-07-23T12:00:00Z | 9.428939 | 9.117751 | 10.117063 | pivot | 2.2113 | 2.0000 | 1.0000 | 1.0000 | 9.113192 | SL | -1.0000 | 36.00 | 0.6096 |  |
| 28 | TEST | BTC/USDT | base+ml | 2023-07-28T20:00:00Z | 2023-07-29T00:00:00Z | 2023-08-01T04:00:00Z | 2359.472461 | 2260.403650 | 2574.041492 | pivot | 2.1659 | 2.0000 | 1.0000 | 1.0000 | 2259.273448 | SL | -1.0000 | 76.00 | 0.4505 |  |
| 29 | TEST | ETH/USDT | base+ml | 2023-08-02T20:00:00Z | 2023-08-03T00:00:00Z | 2023-08-06T08:00:00Z | 8.970406 | 8.682199 | 9.609377 | pivot | 2.2171 | 2.0000 | 1.0000 | 1.0000 | 8.677858 | SL | -1.0000 | 80.00 | 0.7083 |  |
| 30 | TEST | ETH/USDT | base+ml | 2023-08-29T00:00:00Z | 2023-08-29T04:00:00Z | 2023-08-30T20:00:00Z | 8.540407 | 8.295170 | 9.090471 | pivot | 2.2430 | 2.0000 | 1.0000 | 1.0000 | 8.291023 | SL | -1.0000 | 40.00 | 0.6920 |  |
| 31 | TEST | BTC/USDT | base+ml | 2023-09-16T04:00:00Z | 2023-09-16T08:00:00Z | 2023-10-06T08:00:00Z | 2075.444402 | 1961.699268 | 2317.361501 | pivot | 2.1268 | 2.0000 | 1.0000 | 1.0000 | 1960.718419 | SL | -1.0000 | 480.00 | 0.4756 |  |
| 32 | TEST | BNB/USDT | base+ml | 2023-10-13T00:00:00Z | 2023-10-13T04:00:00Z | 2023-10-14T20:00:00Z | 2.744530 | 2.596851 | 3.058970 | pivot | 2.1292 | 2.0000 | 0.5000 | 0.5000 | 3.058970 | TP | 2.0000 | 40.00 | 0.7039 |  |
| 33 | TEST | BTC/USDT | base+ml | 2023-10-14T20:00:00Z | 2023-10-15T00:00:00Z | 2023-10-21T12:00:00Z | 2140.275625 | 2057.068836 | 2321.600779 | pivot | 2.1792 | 2.0000 | 1.0000 | 1.0000 | 2321.600779 | TP | 2.0000 | 156.00 | 0.5564 |  |
| 34 | TEST | BNB/USDT | base+ml | 2023-10-30T16:00:00Z | 2023-10-30T20:00:00Z | 2023-11-24T20:00:00Z | 3.415321 | 3.157512 | 3.954608 | pivot | 2.0918 | 2.0000 | 0.5000 | 0.5000 | 3.155933 | SL | -1.0000 | 600.00 | 0.5852 |  |
| 35 | TEST | ETH/USDT | base+ml | 2024-04-16T00:00:00Z | 2024-04-16T04:00:00Z | 2024-04-21T12:00:00Z | 7.507000 | 7.007299 | 8.558497 | pivot | 2.1043 | 2.0000 | 1.0000 | 1.0000 | 8.558497 | TP | 2.0000 | 128.00 | 0.6410 |  |
| 36 | TEST | ETH/USDT | base+ml | 2024-05-01T16:00:00Z | 2024-05-01T20:00:00Z | 2024-05-14T00:00:00Z | 8.963951 | 8.231002 | 10.491916 | pivot | 2.0847 | 2.0000 | 1.0000 | 1.0000 | 10.491916 | TP | 2.0000 | 292.00 | 0.4226 |  |
| 37 | TEST | BNB/USDT | base+ml | 2024-05-21T12:00:00Z | 2024-05-21T16:00:00Z | 2024-05-24T08:00:00Z | 2.963421 | 2.861395 | 3.188135 | pivot | 2.2025 | 2.0000 | 0.5000 | 0.5000 | 2.859964 | SL | -1.0000 | 64.00 | 0.3960 |  |
| 38 | TEST | ETH/USDT | base+ml | 2024-05-25T20:00:00Z | 2024-05-26T00:00:00Z | 2024-06-06T16:00:00Z | 10.102669 | 9.308358 | 11.761274 | pivot | 2.0881 | 2.0000 | 1.0000 | 1.0000 | 9.303704 | SL | -1.0000 | 280.00 | 0.5858 |  |
| 39 | TEST | BNB/USDT | base+ml | 2024-07-07T00:00:00Z | 2024-07-07T04:00:00Z | 2024-07-08T04:00:00Z | 2.787035 | 2.693408 | 2.993721 | pivot | 2.2076 | 2.0000 | 0.5000 | 0.5000 | 2.692062 | SL | -1.0000 | 24.00 | 0.4915 |  |
| 40 | TEST | BTC/USDT | base+ml | 2024-07-12T20:00:00Z | 2024-07-13T00:00:00Z | 2024-07-13T20:00:00Z | 1005.113903 | 980.786246 | 1060.786725 | pivot | 2.2885 | 2.0000 | 1.0000 | 1.0000 | 980.295852 | SL | -1.0000 | 20.00 | 0.7420 |  |
| 41 | TEST | ETH/USDT | base+ml | 2024-07-18T20:00:00Z | 2024-07-19T00:00:00Z | 2024-07-22T12:00:00Z | 9.199124 | 8.498987 | 10.663145 | pivot | 2.0911 | 2.0000 | 1.0000 | 1.0000 | 8.494738 | SL | -1.0000 | 84.00 | 0.4181 |  |
| 42 | TEST | ETH/USDT | base+ml | 2024-08-12T00:00:00Z | 2024-08-12T04:00:00Z | 2024-08-24T16:00:00Z | 8.464737 | 7.947796 | 9.557404 | pivot | 2.1137 | 2.0000 | 1.0000 | 1.0000 | 9.557404 | TP | 2.0000 | 300.00 | 0.5939 |  |
| 43 | TEST | ETH/USDT | base+ml | 2024-08-25T00:00:00Z | 2024-08-25T04:00:00Z | 2024-08-27T00:00:00Z | 9.691018 | 9.121213 | 10.897954 | pivot | 2.1182 | 2.0000 | 1.0000 | 1.0000 | 10.897954 | TP | 2.0000 | 44.00 | 0.4447 |  |
| 44 | TEST | BTC/USDT | base+ml | 2024-08-27T20:00:00Z | 2024-08-28T00:00:00Z | 2024-08-30T08:00:00Z | 952.776251 | 930.624860 | 1003.732036 | pivot | 2.3003 | 2.0000 | 1.0000 | 1.0000 | 930.159548 | SL | -1.0000 | 56.00 | 0.3887 |  |
| 45 | TEST | ETH/USDT | base+ml | 2024-09-02T20:00:00Z | 2024-09-03T00:00:00Z | 2024-09-04T04:00:00Z | 9.747979 | 9.534947 | 10.242123 | pivot | 2.3196 | 2.0000 | 1.0000 | 1.0000 | 10.242123 | TP | 2.0000 | 28.00 | 0.6666 |  |
| 46 | TEST | BNB/USDT | base+ml | 2024-09-13T20:00:00Z | 2024-09-14T00:00:00Z | 2024-09-15T20:00:00Z | 2.567476 | 2.469759 | 2.780801 | pivot | 2.1831 | 2.0000 | 0.5000 | 0.5000 | 2.468524 | SL | -1.0000 | 44.00 | 0.3417 |  |
| 47 | TEST | BTC/USDT | base+ml | 2024-09-28T00:00:00Z | 2024-09-28T04:00:00Z | 2024-10-01T20:00:00Z | 1061.585243 | 1026.747579 | 1138.663207 | pivot | 2.2125 | 2.0000 | 1.0000 | 1.0000 | 1026.234205 | SL | -1.0000 | 88.00 | 0.5567 |  |
| 48 | TEST | BTC/USDT | base+ml | 2024-10-07T04:00:00Z | 2024-10-07T08:00:00Z | 2024-10-08T20:00:00Z | 1109.116946 | 1076.524087 | 1182.040553 | pivot | 2.2374 | 2.0000 | 1.0000 | 1.0000 | 1075.985824 | SL | -1.0000 | 36.00 | 0.4239 |  |
| 49 | TEST | BTC/USDT | base+ml | 2024-10-18T00:00:00Z | 2024-10-18T04:00:00Z | 2024-10-25T08:00:00Z | 1047.499802 | 987.792435 | 1174.193618 | pivot | 2.1219 | 2.0000 | 1.0000 | 1.0000 | 1174.193618 | TP | 2.0000 | 172.00 | 0.7103 |  |
| 50 | TEST | BTC/USDT | base+ml | 2024-11-23T04:00:00Z | 2024-11-23T08:00:00Z | 2024-11-28T16:00:00Z | 1635.566872 | 1534.309555 | 1849.439040 | pivot | 2.1122 | 2.0000 | 1.0000 | 1.0000 | 1849.439040 | TP | 2.0000 | 128.00 | 0.4297 |  |
| 51 | TEST | ETH/USDT | base+ml | 2024-12-14T20:00:00Z | 2024-12-15T00:00:00Z | 2024-12-18T08:00:00Z | 13.123570 | 12.409498 | 14.642943 | pivot | 2.1278 | 2.0000 | 1.0000 | 1.0000 | 12.403294 | SL | -1.0000 | 80.00 | 0.8003 |  |
| 52 | TEST | BTC/USDT | base+ml | 2024-12-19T12:00:00Z | 2024-12-19T16:00:00Z | 2024-12-20T04:00:00Z | 2132.784507 | 2094.401779 | 2224.453881 | pivot | 2.3883 | 2.0000 | 1.0000 | 1.0000 | 2224.453881 | TP | 2.0000 | 12.00 | 0.4563 |  |

## Flagged trades (1)

- **#18** ETH/USDT (TRAIN, entry 2023-03-06T12:00:00Z): window_end: window-end forced exit (window-boundary artifact, not an SL/TP outcome)

## Trade context (candles)

Candle context unavailable: no candles were supplied (`write_review_pack(..., data=...)`, or the CLI's `--data-dir` / `--synthetic`), so the MAE, MFE, stop-structure and wick columns of `trades_review.csv` are blank and no `context/` files were written. Judge each trade on a chart of its pair from 30 candles before the entry through the exit, and check the stop against the swing low it claims to sit behind.

## Rule denials

Signal candles denied by each rule (the first failing rule is counted), for context on how often the mandatory rules said no.

| rule | denied | meaning |
|---|---:|---|
| R1_trend | 12393 | Trend gate: close > EMA9 and close > EMA21 and EMA9 > EMA21 (4H) |
| R3_volume | 3691 | Volume: entry-candle volume >= 1.5x the previous-20-candle average |
| R2_momentum | 2522 | Momentum: RSI(14) within [50, 70] on the entry candle |
| R4_regime | 109 | Regime: close > EMA200 (unless explicitly testing it off) |
| R6_correlation_cap | 91 | BTC/ETH/BNB share one risk budget; BNB never stacks |
| L_ml_filter | 49 | Layer: logistic-regression veto; it can only veto an entry that passed every mandatory rule and never approves one a rule denies (a veto can free R6 budget or change R9 state, which may admit other rule-compliant trades) |
| R5_news_blackout | 9 | No entries within +/-2h of high-impact news; BNB +/-24h burns/launchpool |
| R8_structure_stop | 2 | Stop behind a confirmed swing low with a buffer (BNB 0.5-0.8%) |

## Reviewer sign-off

Complete by hand. APPROVE only if every row was inspected and every flag is explained in `reviewer_note` of `trades_review.csv`.

- Reviewer name: ______________________________
- Date (UTC): ______________________________
- Trades reviewed: ______ of 52
- Flagged trades explained: ______ of 1
- Decision: [ ] APPROVE    [ ] REJECT
- Notes: ____________________________________________________________
- Follow-ups required before adoption: ______________________________
