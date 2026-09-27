# trendbot: research, validation and adoption layer for the 4H trend bot

Research and testing tool only. Not financial advice. Crypto trading can result in the total
loss of the capital used. See [Disclaimer](#12-disclaimer).

The binding interface spec is [`CONTRACT.md`](CONTRACT.md): v1, the v2 amendments A1-A4 and
the v3 amendments C1-C6 at its end. This README describes the code and the committed results
as they are after v3 (gate cycle 2).

Contents: 1 win rate; 2 what this is (2.1 costs and 4H, 2.2 gatekeeper and live bot, 2.3 R9
timing, 2.4 R5 without look-ahead, 2.5 layers veto); 3 rule-to-test map; 4 how to run; 5 how
to read results; 6 honest status; 7 model complexity and tools; 8 freqtrade and FreqAI;
9 verification notes; 10 where the outputs live; 11 adoption path; 12 disclaimer.

## 1. No win rate is promised or targeted

Nothing in this package promises, targets or optimises a win rate:

- A win rate tells you nothing without the payoff it was earned at. At the mandatory 2:1
  minimum a strategy breaks even at 1/(1+2) = 33.3% winners. A 90% win rate with a 1:9
  payoff only breaks even before costs, and loses money after them.
- Optimising win rate pushes towards near targets and far stops. That is the opposite of
  the 2:1 rule, and it rewards curve-fitting. A backtest win rate near 90% is a red flag
  for look-ahead or overfitting, not a sign of skill.
- In the code: discovery selects on the TRAIN t-stat of R-multiples
  (`strategy_discovery.select_on_train`). The ML threshold is the break-even probability
  implied by the TRAIN average win and loss in R (`ml_filter`, no threshold search). The
  walk-forward labels use average R and multiplicity-corrected bootstrap lower bounds of it
  (`metrics.label`, section 5). Reports show win rate as context only, and the human review
  pack (`review_sheet`) leaves it out.

**Target metric: positive expectancy.** That is the average R per closed trade, net of fees
and slippage (defaults: 0.10% of notional per side, 0.05% slippage on market fills). It must
survive a 70/30 chronological walk-forward (label `ROBUST` under the fixed rule of section 5)
with a TEST drawdown inside the limit of section 5 (`dd_ok`).

**Reward:risk >= 2:1 NET of costs, no exceptions (CONTRACT.md v2 A1).** `config.StrategyConfig`
raises `ConfigError` for `reward_risk < 2.0`. Both R7 (risk per trade) and the 2:1 minimum
are measured on the ALL-IN loss at the stop, fees and slippage included. With fill price
`E` (entry slippage already in it), stop `S`, fee rate `f` per side and
`s = slippage_pct / 100`:

```text
S_x = S * (1 - s)                           assumed stop fill (slippage against us)
L_u = (E - S_x) + f*E + f*S_x               all-in loss per unit: price loss + both fees
qty = equity * risk_pct / 100 / L_u         R7: the size follows from the stop distance
risk_amount = qty * L_u                     the planned all-in loss, i.e. 1R
T = (E*(1+f) + reward_risk * L_u) / (1-f)   target: a take-profit nets reward_risk * 1R
```

So a stop that fills at `S_x` loses exactly 1R (r = -1), and a take-profit at `T` (a limit
fill with no slippage) wins exactly `reward_risk` R after both fees. Only a stop that gaps
through (the exit candle opens below the stop and fills at `open * (1 - s)`) can lose more
than 1R. Because costs are in `L_u`, the price ratio `(T - E) / (E - S)` is always above
`reward_risk`.

Worked example with the default costs (equity 10,000, BTC at its 1% cap): fill `E = 100`,
stop `S = 96.50` (3.5% below, close to the 3.48-3.79% median stop distance of the committed
seed-1 runs). Then `S_x = 96.45175`, `L_u = 3.74470`, `qty = 26.704` and `T = 107.697`, which
is 2.199:1 in price. A clean stop loses 100.00 (-1R) and a take-profit wins 200.00 (+2R).
Sizing on the price distance alone would have bought 28.571 units, and the same stop would
have lost about 107 (1.07% of equity).

Where it lives: `sizing.loss_per_unit`, `sizing.cost_aware_target` and `sizing.size_for_pair`,
called by `Gatekeeper.plan_fill` on the ACTUAL fill. `invariants` re-derives, from the trade
row and the config alone, that net RR >= `reward_risk`, price RR >= `reward_risk`,
`risk_amount == qty * L_u`, a clean SL is -1R and a TP is +`reward_risk` R (tolerance 1e-9).
The review pack flags a trade whose RR is below the configured value by price OR net of
costs, and on testnet that flag counts as a rule violation. In the 40 committed seed-1
journals (5 worlds x 4 pre-registered candidates x TRAIN/TEST), all 977 take-profits are
+`reward_risk` R (2, 2.5 or 3) within 3e-14 and all 1,695 stop-losses are -1R within 4e-16
(none gapped: the default synthetic candles open at the previous close). The other 20 trades
were force-closed at the end of a window.

## 2. What this is

- **A layer on top of the existing bot's rules. R1-R9 are unchanged.** `config.py` checks
  every mandate when a config is built and raises `ConfigError` on any loosening, so a
  discovery variant can only TIGHTEN a rule. An entry layer (`L_ml_filter`,
  `L_expectancy_guard`) can only VETO an entry that passed every mandatory rule, or (the
  guard) shrink its risk; it never approves an entry a rule denies. A veto can free R6
  budget or change R9 state, which can admit OTHER rule-compliant trades the base never
  took, so a layer's journal is not a subset of the base's (section 2.5). There is one
  documented exception to "no loosening": `regime_filter=False` (R4 off, "when explicitly
  testing it off"). It is flagged `is_test_only`, can never be selected by discovery, and
  `adoption.py` never lets it go LIVE.
- **Python 3.11 standard library only.** The one exception is `ccxt`, imported lazily
  inside `fetch_data.main`. It is only needed to download real candles. scikit-learn is the
  intended tool for the ML layer on a networked machine, but it is not used here (section 7).
- **Deterministic.** All randomness goes through `random.Random(seed)` and the bootstrap
  seeds are fixed. Re-running the committed synthetic commands (same `--now`, same relative
  `--out-dir`) reproduces every committed file byte for byte, `REPORT.md` and `run.log`
  included (checked in this cycle, section 9).
- **Auditable.** Every denied signal candle is logged with its rule id
  (`config.RULE_IDS`) and a one-sentence reason. `invariants.check_invariants` re-derives
  every rule from the trade list and the candles. It does not reuse the gatekeeper's,
  breakers' or news calendar's code; only `compute_features` (and the list of scheduled
  news kinds) is shared.
- **Not included.** Live order execution, exchange account handling and a testnet runner.
  The live bot is responsible for these and must use the gatekeeper below.

| module | role |
|---|---|
| `models.py`, `config.py` | shared types; validated config (mandate floors and ceilings, 4H only, harness cost floors) |
| `indicators.py`, `signals.py`, `structure.py` | EMA / Wilder RSI / previous-20 volume mean; R1-R4 gates; R8 stop |
| `sizing.py`, `correlation.py`, `news.py` | R7 cost-aware sizing and target; R6 shared cluster budget; R5 calendar (scheduled vs unscheduled) |
| `circuit_breakers.py`, `journal.py`, `journal_rules.py` | R9; CSV trade journal and `journal convert` for the existing bot's CSV; "learn from past trades" as explicit rules |
| `gatekeeper.py` | **the single entry-decision path** (backtest and live) |
| `backtester.py`, `invariants.py` | event-driven backtest; independent rule auditor (library and CLI, `--config` aware) |
| `data.py`, `fetch_data.py`, `synthetic.py` | candle CSVs with a 4H spacing check; ccxt download; synthetic worlds with known truth |
| `metrics.py`, `walkforward.py`, `strategy_discovery.py`, `ml_filter.py` | stats, C4 labels and C5 drawdown; 70/30 walk-forward and layer diffs; TRAIN-only selection; logistic veto layer with a model fingerprint and a JSON loader |
| `run_research.py`, `report.py`, `review_sheet.py`, `adoption.py` | one-command pipeline and calibration / power runs; Markdown rendering; human review pack; evidence-bound adoption gate |

### 2.1 Harness cost floors, bounds and the 4H-only timeframe (CONTRACT.md v2 A4)

On top of the mandate, `StrategyConfig` refuses unrealistic research settings, so no
backtest in this package can run cost-free or on another timeframe:

- `timeframe_ms` must be exactly 4h ("timeframe is fixed by the mandate at 4H (R1 is a 4H
  trend gate)"); `run_research --timeframe` accepts only `4h`;
- `fee_rate >= 0.0005` per side (`MIN_FEE_RATE`) and `slippage_pct >= 0.01` (`MIN_SLIPPAGE_PCT`);
- a 7-day realized loss limit in `(0, 10]` percent (`MAX_WEEKLY_LOSS_LIMIT_PCT`);
- `0.05 <= min_stop_distance_pct < max_stop_distance_pct <= 25` (`STOP_DISTANCE_BOUNDS_PCT`);
- no leverage.

The data side enforces the same timeframe. `data.load_dataset` refuses a candle file unless
every spacing between consecutive candles is a whole multiple of 4h (gaps are allowed and
listed by `gap_report`) AND the SMALLEST spacing is exactly 4h, naming the lines and
timestamps. So a 1h file or a 1d file saved under a `-4h.csv` name is refused, not
silently traded as 4H candles.

`run_research` adds CLI ceilings that catch a percent typed where a fraction is expected:
`--fee-rate` at most 0.02 (so `0.6` is refused; `0.006` means 0.6%) and `--slippage-pct` at
most 5. `--exchange-id` must look like a ccxt id. A refused value exits 2 with the reason:

```bash
python -m research.trendbot.run_research --synthetic null --fee-rate 0 --out-dir /tmp/x
# error: fee_rate must be >= 0.0005 per side (realistic costs)                     [exit 2]
python -m research.trendbot.run_research --synthetic null --fee-rate 0.6 --out-dir /tmp/x
# error: --fee-rate 0.6 is not a plausible fraction of notional per side (at most 0.02; 0.006 means 0.6%)
python -m research.trendbot.run_research --synthetic null --timeframe 1h --out-dir /tmp/x
# error: the mandate is a 4H strategy: --timeframe must be 4h                      [exit 2]
```

`tests/test_config.py` (orchestrator-owned) asserts each config refusal above, including the
1h and 1d timeframes, together with every mandate floor and ceiling (the R5 windows, the R9
limits and the R6 cluster membership). `test_run_research.py::test_cli_usage_errors` covers
the CLI ceilings and the `--timeframe` refusal, and `test_data.py` the spacing check
(section 3).

### 2.2 The gatekeeper is the single entry-decision path

The backtester calls `Gatekeeper.evaluate` for every signal candle and `Gatekeeper.plan_fill`
for every fill (`test_backtester.py::test_every_signal_candle_goes_through_the_gatekeeper`).
A live bot must call the same two methods, so a backtest and the bot cannot diverge on a
mandatory rule. Order inside `evaluate` (the first failure wins and names its rule):

```text
R1-R4 signals.check_entry -> R5 NewsCalendar.check -> R9 CircuitBreakers.can_enter
-> R6 CorrelationGuard.check -> R8 structure.find_stop -> L_ml_filter veto (if given)
-> L_expectancy_guard multiplier -> R7 risk = min(pair cap, R6 allowance) x guard
```

`plan_fill(pair, decision, market_price, fill_price, equity, free_cash)` then:

1. refuses a fill at or below the stop (R8 "gapped through stop");
2. re-checks the stop distance against the actual fill (R8);
3. sizes the position with the A1 formula above (`qty = equity * risk% / L_u`) and sets the
   cost-aware target `T`;
4. caps the notional plus entry fee by the free cash (equity minus the cost of the other
   open positions). The quantity only shrinks, and `risk_amount` is recomputed as
   `qty * L_u`, so the risk only goes down;
5. returns `X_capital` if less than `MIN_NOTIONAL` (10 quote units) can be bought.

What the live bot does at startup and on **each closed 4H candle**. The block below was
executed as written in this cycle (section 9), offline against synthetic candles, once for
the base variant and once for the ML variant, with the exchange I/O (`closed_4h_candles`,
the order calls, `record_denial`) replaced by stubs. The other names it uses without
defining (`trade`, `equity_after_close`, `pair`, `open_risk`, `equity`, `free_cash`,
`last_price`, `market_price`, `fill_price`) are the bot's own state.

```python
import time
from pathlib import Path

from research.trendbot.adoption import load_config, load_record, require_stage
from research.trendbot.circuit_breakers import CircuitBreakers
from research.trendbot.gatekeeper import Gatekeeper
from research.trendbot.indicators import compute_features
from research.trendbot.journal import read_journal
from research.trendbot.ml_filter import MLFilter
from research.trendbot.news import load_events

RECORD = "adoption_base.json"  # the adopted variant's record, e.g. adoption_base_plus_ml.json
CONFIG = None  # its config_<variant>.json when run_research wrote one, else None (defaults)
MODEL = None  # "model_base_plus_ml.json" for the ML variant, else None

cfg = load_config(CONFIG)  # the tested overrides, validated: any loosening raises ConfigError
record = load_record(RECORD)
entry_filter = model_fp = None
if MODEL is not None:
    # Raises ValueError unless the file is exactly the model the record pins.
    ml = MLFilter.from_json(Path(MODEL).read_text(encoding="utf-8"),
                            expected_fingerprint=record.model_fingerprint)
    entry_filter, model_fp = ml.entry_filter(), ml.fingerprint()
# At startup: refuse to trade unless the adoption record allows LIVE (raises AdoptionBlocked).
require_stage(record, "LIVE", cfg, time.time_ns() // 1_000_000, base_dir=".",
              model_fingerprint=model_fp)
journal = read_journal("trades.csv")  # every trade the bot has taken (journal.write_journal)
# R9 state after a restart. A LIVE journal holds real fill times, so the exit-time offset
# is 0 (a backtest journal would need cfg.timeframe_ms, see section 2.3). The gatekeeper
# takes the offset from the breakers and uses the same one for the expectancy guard.
gk = Gatekeeper(cfg, events=load_events("events.csv"),
                breakers=CircuitBreakers.from_journal(journal, cfg, 0))

# When the exchange reports an exit (SL/TP): journal it, then feed R9. Exits are never gated.
journal.append(trade)  # and persist it with journal.write_journal
gk.on_trade_closed(trade, equity=equity_after_close)

# At each 4H close, for every pair in sorted order:
candles = closed_4h_candles(pair)  # oldest first, ending with the candle that just closed
rows = compute_features(candles, cfg)
closed = [t for t in journal if t.is_closed]
dec = gk.evaluate(pair, candles, rows, len(candles) - 1, open_risk, equity, closed, entry_filter)
if not dec.allowed:
    record_denial(pair, dec.rule, dec.reason)  # audit trail: rule id + one sentence
else:
    # 1) size the market order from the EXPECTED fill: last price * (1 + slippage)
    expected_fill = last_price * (1 + cfg.slippage_pct / 100)
    order = gk.plan_fill(pair, dec, last_price, expected_fill, equity, free_cash)
    if not order.ok:
        record_denial(pair, order.rule, order.reason)  # no order is sent
    else:
        # 2) market buy order.sizing.qty; the exchange reports market_price and fill_price
        # 3) recompute stop distance, size and target from the ACTUAL fill
        plan = gk.plan_fill(pair, dec, market_price, fill_price, equity, free_cash)
        if plan.ok:
            # hold at most plan.sizing.qty (sell any excess), stop order at plan.stop,
            # limit take-profit at plan.target
            open_risk[pair] = dec.risk_pct  # the share of the R6 budget this trade reserves
        else:
            pass  # flatten now: the actual fill broke R8 or the capital limit (plan.reason)
```

Notes for the bot's author:

- A fill worse than expected raises `L_u`, so `plan.sizing.qty` can be smaller than the
  quantity bought. Selling the excess is what keeps R7 true at the actual fill. The target
  must always come from the actual-fill plan.
- Round quantities DOWN and take-profit prices UP to the exchange tick, so that R7 and the
  2:1 minimum still hold after rounding (see `adoption.py`).
- `open_risk` holds the risk each open position RESERVED from the shared R6 budget
  (`dec.risk_pct`), not the possibly smaller realized risk after a cap.
- Passing breakers built with one offset and a different `exit_time_uncertainty_ms` to
  `Gatekeeper` raises `ValueError`, so R9 and the guard cannot measure exits differently.
- **ML variant: the model file is loaded, never refitted.** `MLFilter.from_json` rebuilds the
  filter from `model_base_plus_ml.json` (every float stored as its exact `repr` string) and
  raises `ValueError` if the model inside does not hash to the fingerprint stored in the
  file (edited or corrupted) or to `expected_fingerprint` (the record's
  `model_fingerprint`). `require_stage(..., model_fingerprint=ml.fingerprint())` then checks
  the same fingerprint against the record (`ADOPT_fingerprint`). The file's own sha256 is
  NOT the fingerprint: the fingerprint is the sha256 of the canonical model JSON inside it
  (`MLFilter.canonical_json`).
- **Expectancy-guard variant (`base+guard`):** `CONFIG = "config_base_plus_guard.json"` (it
  holds `{"expectancy_guard": true}`). The guard then runs inside `evaluate` from the
  journal passed as `closed`; it needs no model file.

### 2.3 R9 timing: an exit counts from when it is certain (CONTRACT.md v2 A2)

A backtest records `Trade.exit_ts` as the OPEN of the exit candle, but the fill happens
somewhere in `[exit_ts, exit_ts + 4h)`. R9 and the expectancy guard therefore use the
effective exit time `t_e = exit_ts + exit_time_uncertainty_ms`:

- the bench runs `[t_e, t_e + bench_hours)`;
- the 7-day loss window counts trades with `t_e` in `(ts - loss_window_days, ts]`.

The backtester and the gatekeeper pass `cfg.timeframe_ms` (4h), so in a backtest a bench
starts at the close of the exit candle and lasts at least 24h of real time after the fill.
A live bot journals real fill times and passes 0. `journal_rules` takes the same parameter,
and its CLI flag `--backtest-journal` sets it to the timeframe. `invariants` recomputes the
benches and the halt with the backtest convention; the adoption TESTNET check recomputes
them on the testnet journal with offset 0 (section 11).

Run on the committed null seed-1 TEST journal, where trades #104-#106 were three
consecutive BTC stop-losses, the last recorded at `exit_ts` 2023-06-16T08:00Z:

```bash
python -m research.trendbot.journal_rules \
    --journal research/results/synthetic_null_s1/journals/base_test.csv \
    --equity 10000 --now 2023-06-16T12:00:00Z --backtest-journal
# | R9_circuit_breaker | BTC/USDT | no entries until 2023-06-17T12:00:00Z | ... bench 24h from
#   2023-06-16T12:00:00Z, the latest time the stop-loss recorded at 2023-06-16T08:00:00Z can
#   have filled). | #104, #105, #106 |
```

Without `--backtest-journal` the same journal is read as a live one, and the bench ends at
2023-06-17T08:00Z. That is only 20h after the exit candle closed, which is why backtest
journals need the flag. At `--now 2023-06-16T08:00:00Z --backtest-journal` there is no
bench yet, because the third loss is not yet certain at that time.

### 2.4 R5 without look-ahead: scheduled vs unscheduled news (CONTRACT.md v3 C2)

An event applies to an entry on a pair when it is high-impact and scoped `ALL`, the pair's
base asset or `EXCHANGE:<cfg.exchange_id>` (window `w` = 2h, `news_blackout_hours`), or when
the pair is BNB and the event is a `bnb_burn` / `launchpool` of any impact scoped `ALL`,
`BNB` or `EXCHANGE:binance` (window `w` = 24h, `bnb_event_blackout_hours`; the wider window
wins if both apply). An applicable event at time `ts` blocks the entry decisions in

```text
[max(ts - w, known_from), ts + w]        both ends inclusive (news.block_interval)
```

where `known_from` is when the event became knowable:

- **scheduled kinds** (`macro`, `unlock`, `bnb_burn`, `launchpool`) are on a calendar in
  advance: by default `known_from = -inf`, so the full `+/-w` window applies (a CPI print
  at 13:30 blocks 11:30-15:30);
- **unscheduled kinds** (`regulatory`, `legal`, `other`) are headlines nobody could see
  coming: by default `known_from = ts`, so they block only `[ts, ts + w]`;
- an explicit `known_from_utc` in `events.csv` overrides the kind default. It narrows a
  scheduled window (a launchpool announced only 12h ahead blocks from the announcement) or
  widens an unscheduled one back to `ts - w` (a court date that was public in advance). It
  never widens anything beyond `+/-w`, and a `known_from` later than `ts + w` never blocks.

**Why.** Blocking the hours BEFORE a surprise headline would use information the live bot
could not have had at that moment: the backtest would dodge losses that live trading
cannot, which is look-ahead and makes every R5-filtered result optimistic. Scheduled events
keep the full mandated `+/-2h` (and BNB `+/-24h`) window because the bot does know them in
advance.

The `events.csv` header gains an optional last column, and the old 5-column header is still
accepted (backward compatible; an empty cell means the kind default):

```text
time_utc,scope,impact,kind,note,known_from_utc
2024-02-13T13:30:00Z,ALL,high,macro,CPI-style inflation print,
2024-02-20T08:00:00+00:00,EXCHANGE:binance,high,regulatory,unscheduled headline,
2024-03-20T00:00:00Z,EXCHANGE:binance,medium,launchpool,announced 12h ahead,2024-03-19T12:00:00Z
```

On [`events_example.csv`](events_example.csv) (format only, every row "EXAMPLE ONLY - not a
real calendar"), checked in this cycle with `NewsCalendar.check`: the unscheduled
regulatory headline at 2024-02-20T08:00Z blocks BTC from 08:00:00 to 10:00:00 inclusive and
allows 07:59:59; the CPI-style print at 13:30 blocks from 11:30; the launchpool at
2024-03-20T00:00Z, known from 2024-03-19T12:00Z, blocks BNB from 2024-03-19T12:00Z to
2024-03-21T00:00Z (instead of from 2024-03-19T00:00Z) and does not block BTC (medium impact).
The synthetic worlds leave `known_from_ts` empty on every event, so the kind defaults apply:
their quarterly `regulatory` events block every pair only for `[ts, ts + 2h]`.
`invariants` recomputes R5 from the raw events with the same interval, and the adoption
TESTNET check applies it only when the testnet news calendar is recorded (section 11).

### 2.5 Layers veto, and what a veto can admit (CONTRACT.md v3 C1)

Two pre-registered layers run on the baseline rules:

- `base+ml` adds `L_ml_filter`: a logistic model fitted on purged TRAIN candidates only
  (section 7) vetoes a rule-passing signal whose predicted win probability is at or below
  the TRAIN break-even probability;
- `base+guard` is the baseline with `expectancy_guard=True` (`L_expectancy_guard`): a pair's
  risk is multiplied by 0.5 while its last 20 closed trades average below 0R. Nothing is
  fitted. It is a separate candidate with its own config (`config_base_plus_guard.json`),
  config fingerprint, journals, review pack and adoption record, and it needs the same
  walk-forward evidence as any other variant.

The C1 wording, which the reports, `models.EntryFilter` and `config.RULE_IDS["L_ml_filter"]`
follow: **an entry layer can only VETO an entry that passed every mandatory rule; it never
approves an entry a rule denies. Because a veto can free R6 budget or change R9 state, the
layer's trade list may contain other rule-compliant trades the base never took.** Every
report therefore counts, per window
(TRAIN and TEST), matched by `(pair, signal_ts)`: signals vetoed, entries at reduced risk,
base trades absent from the layer's journal, and layer trades absent from the base journal
(`walkforward.layer_diff`, REPORT.md section 5). Planted seed 1, TRAIN | TEST: `base+ml`
vetoed 60 | 28 signals, 44 | 20 base trades are missing from its journal, and 23 | 3 of its
trades are not in the base journal. In null seed 1 the guard took 37 | 4 entries at half
risk and 10 | 0 trades the base never took (a halved risk reserves less of the shared R6
budget, so other entries fit).

## 3. Rule-to-code-to-test map

Each test named here exists and passed in this cycle: every name was checked against
`pytest --collect-only` and all of them were run together with `pytest -k` (section 9).
The file prefix is `research/trendbot/tests/`.

| rule | enforced by | proven by |
|---|---|---|
| **R1** trend: close > EMA9, close > EMA21, EMA9 > EMA21, on 4H candles only | `signals.check_entry` (trend gate) on `indicators.compute_features`; `StrategyConfig.validate` fixes EMA 9/21/200 and `timeframe_ms` = 4h; `run_research --timeframe` must be `4h`; `data.load_dataset` spacing check (every step a multiple of 4h, the smallest exactly 4h) | `test_signals.py::test_trend_gate_fails`<br>`test_signals.py::test_trend_detail_lists_every_broken_condition`<br>`test_signals.py::test_config_refuses_loosened_entry_gates`<br>`test_config.py::test_loosening_is_rejected`<br>`test_data.py::test_load_dataset_rejects_wrong_timeframe_and_empty_files`<br>`test_data.py::test_load_dataset_rejects_a_daily_file_saved_as_4h`<br>`test_data.py::test_minimum_spacing_must_equal_the_timeframe`<br>`test_run_research.py::test_cli_usage_errors` |
| **R2** RSI(14) in [50, 70] | `signals.check_entry` (momentum gate, both ends inclusive); `indicators.rsi_wilder`; `StrategyConfig.validate` (period 14, window inside [50, 70]) | `test_signals.py::test_momentum_bounds_inclusive`<br>`test_signals.py::test_momentum_fails_outside_window`<br>`test_indicators.py::test_rsi_matches_published_wilder_worksheet`<br>`test_config.py::test_loosening_is_rejected` |
| **R3** volume >= 1.5x previous-20 average | `signals.check_entry` (volume gate); `indicators.prev_mean` (current candle excluded); `StrategyConfig.validate` (multiple >= 1.5, lookback 20) | `test_signals.py::test_volume_exactly_at_multiple_passes`<br>`test_signals.py::test_volume_below_multiple_fails`<br>`test_indicators.py::test_prev_mean_excludes_current_value`<br>`test_config.py::test_loosening_is_rejected` |
| **R4** close > EMA200 unless explicitly tested off | `signals.check_entry` (regime gate); `StrategyConfig.is_test_only`; `strategy_discovery.select_on_train` never selects it; `adoption.check_promotion` blocks LIVE | `test_signals.py::test_regime_close_not_above_ema200_fails`<br>`test_signals.py::test_regime_disabled_is_an_explicit_pass`<br>`test_config.py::test_regime_off_is_flagged_test_only`<br>`test_strategy_discovery.py::test_select_on_train_rules`<br>`test_adoption.py::test_test_only_config_may_reach_testnet_but_never_live` |
| **R5** no entries +/-2h of high-impact news; BNB +/-24h around burns / launchpools; scheduled vs unscheduled without look-ahead (C2) | `news.NewsCalendar.check` (bisect over sorted events; block interval `[max(ts - w, known_from), ts + w]`, both ends inclusive; `known_from_utc` column), step 2 of `Gatekeeper.evaluate`; `StrategyConfig.validate` (windows may widen, never narrow); `invariants` recomputes it from the raw events | `test_news.py::test_high_impact_all_boundaries_are_inclusive`<br>`test_news.py::test_bnb_events_block_bnb_for_24h_any_impact`<br>`test_news.py::test_bnb_burn_does_not_block_btc_or_eth`<br>`test_news.py::test_exchange_scope_matches_configured_exchange_only`<br>`test_news.py::test_unscheduled_headline_after_the_decision_does_not_block`<br>`test_news.py::test_unscheduled_headline_before_the_decision_blocks_its_tail`<br>`test_news.py::test_scheduled_high_impact_still_blocks_before_the_print`<br>`test_news.py::test_explicit_known_from_narrows_a_scheduled_pre_window`<br>`test_news.py::test_late_known_event_never_blocks`<br>`test_news.py::test_example_calendar_semantics`<br>`test_news.py::test_check_matches_a_brute_force_oracle`<br>`test_news.py::test_loader_reads_the_known_from_column`<br>`test_news.py::test_loader_header_without_known_from_is_still_accepted`<br>`test_synthetic.py::test_synthetic_events_leave_known_from_to_the_kind_default`<br>`test_config.py::test_loosening_is_rejected`<br>`test_backtester.py::test_news_blackout_blocks_entries_end_to_end`<br>`test_invariants.py::test_disabled_news_rule_is_caught`<br>`test_invariants.py::test_unscheduled_news_blocks_only_after_it_happened_end_to_end`<br>`test_invariants.py::test_r5_recomputation_agrees_with_the_news_calendar` |
| **R6** BTC/ETH/BNB share one risk budget, never full size on more than one, BNB never stacks | `correlation.CorrelationGuard.check` (no pyramiding, BNB exclusive, `allowed = min(requested, remaining)`); `StrategyConfig.validate` (cluster budget <= largest single-pair cap, all three in the cluster, BNB exclusive); `invariants.cross_trade_violations` (backtest and testnet journals) | `test_correlation.py::test_btc_open_at_full_size_denies_eth`<br>`test_correlation.py::test_bnb_open_denies_btc_and_eth`<br>`test_correlation.py::test_btc_or_eth_open_denies_bnb_even_with_budget_left`<br>`test_correlation.py::test_two_full_size_positions_are_impossible_by_config`<br>`test_config.py::test_loosening_is_rejected`<br>`test_backtester.py::test_bnb_never_stacks_with_another_cluster_position`<br>`test_invariants.py::test_disabled_correlation_cap_is_caught`<br>`test_invariants.py::test_live_r6_overlaps_and_budget` |
| **R7** risk <= 1% BTC/ETH, <= 0.5% BNB, ALL-IN (fees + slippage); size derived from the stop distance | `sizing.size_for_pair` / `size_position` (`qty = equity * risk% / L_u`; notional and free-cash caps only shrink), called by `Gatekeeper.plan_fill` on the actual fill; `StrategyConfig.validate` (caps) | `test_sizing.py::test_basic_size_is_cost_aware_hand_arithmetic`<br>`test_sizing.py::test_stop_fill_with_both_fees_loses_exactly_risk_pct`<br>`test_sizing.py::test_wider_stop_means_smaller_position_same_all_in_risk`<br>`test_sizing.py::test_size_for_pair_enforces_the_pair_cap`<br>`test_config.py::test_pair_risk_caps`<br>`test_gatekeeper.py::test_plan_fill_applies_the_a1_cost_aware_arithmetic_by_hand`<br>`test_backtester.py::test_risk_caps_and_size_derived_from_the_stop`<br>`test_invariants.py::test_price_only_sizing_is_caught` |
| **R8** stop behind the swing low with a buffer (BNB 0.5-0.8%) | `structure.find_stop` / `latest_confirmed_pivot` (a pivot is usable only k candles after it; fallback = lowest low of the last N); `Gatekeeper.plan_fill` (gap-through refusal, distance re-check from the fill); `StrategyConfig.validate` (BNB buffer range, buffer > 0, stop-distance bounds) | `test_structure.py::test_confirmed_pivot_with_btc_buffer`<br>`test_structure.py::test_unconfirmed_pivot_is_not_used`<br>`test_structure.py::test_find_stop_never_reads_candles_after_i`<br>`test_structure.py::test_config_keeps_the_bnb_buffer_in_the_mandated_range`<br>`test_config.py::test_bnb_buffer_range`<br>`test_backtester.py::test_entry_that_gaps_through_the_stop_is_skipped`<br>`test_backtester.py::test_gaps_world_exercises_gap_through_exits_and_the_entry_skip`<br>`test_invariants.py::test_stop_not_behind_structure_is_caught` |
| **R9** 3 consecutive SLs bench a pair 24h; 7-day realized loss limit halts all entries; exits never paused | `circuit_breakers.CircuitBreakers` (`on_trade_closed`, `can_enter`, `from_journal`; deliberately no exit API); step 3 of `Gatekeeper.evaluate`; `StrategyConfig.validate` (limit <= 3, bench >= 24h, window >= 7 days, loss limit in (0, 10]%); `invariants.cross_trade_violations` | `test_circuit_breakers.py::test_three_consecutive_stop_losses_bench_the_pair_for_24h`<br>`test_circuit_breakers.py::test_weekly_loss_halts_all_pairs_and_recovers_when_losses_age_out`<br>`test_circuit_breakers.py::test_there_is_no_exit_related_api`<br>`test_config.py::test_loosening_is_rejected`<br>`test_backtester.py::test_exits_are_processed_while_the_halt_is_active`<br>`test_invariants.py::test_paused_exits_are_caught`<br>`test_invariants.py::test_live_bench_uses_real_exit_times`<br>`test_invariants.py::test_live_weekly_halt_and_starting_equity` |
| **R9 timing** (A2): exits count from `exit_ts + 4h` in backtests, real fill time live | `CircuitBreakers(cfg, exit_time_uncertainty_ms)`; `Gatekeeper` (one shared offset); `journal_rules --backtest-journal`; `invariants` bench-duration check | `test_circuit_breakers.py::test_backtest_convention_bench_lasts_24h_of_real_time_from_the_candle_close`<br>`test_circuit_breakers.py::test_backtest_convention_loss_window_counts_from_the_candle_close`<br>`test_gatekeeper.py::test_breakers_and_guard_share_one_exit_offset`<br>`test_backtester.py::test_bench_lasts_24h_from_the_exit_candle_close`<br>`test_journal_rules.py::test_cli_backtest_journal_flag`<br>`test_invariants.py::test_bench_counted_from_the_exit_candle_open_is_caught` |
| **Minimum RR 2:1, net of costs** (A1) | `StrategyConfig.validate` (`reward_risk >= 2.0`); `sizing.cost_aware_target` via `Gatekeeper.plan_fill` (target from the actual fill); discovery grid {2, 2.5, 3}; `review_sheet.auto_flags` `rr_below_min` (price or net); `adoption.RULE_VIOLATION_FLAGS` | `test_sizing.py::test_cost_aware_target_nets_exactly_reward_risk`<br>`test_backtester.py::test_every_trade_has_rr_at_least_2_and_stop_below_structure`<br>`test_backtester.py::test_outcomes_in_r_are_exact_on_a_synthetic_run`<br>`test_invariants.py::test_price_only_target_is_caught`<br>`test_review_sheet.py::test_rr_is_checked_by_price_and_net_of_costs`<br>`test_review_sheet.py::test_cli_rejects_a_loosened_reward_risk`<br>`test_strategy_discovery.py::test_default_grid_is_twelve_legal_tightenings`<br>`test_config.py::test_loosening_is_rejected` |
| **Cost floors and bounds** (A4); costs reach every trade and every report | `StrategyConfig._harness_errors`; `run_research.config_from_args` (CLI ceilings); `report` cost lines (report section 2, Coinbase warning) | `test_config.py::test_loosening_is_rejected`<br>`test_config.py::test_tightening_is_allowed`<br>`test_sizing.py::test_zero_cost_configs_are_rejected_so_sizing_always_carries_costs`<br>`test_run_research.py::test_cli_usage_errors`<br>`test_run_research.py::test_cost_flags_default_to_the_config_defaults`<br>`test_run_research.py::test_provenance_prints_default_costs`<br>`test_run_research.py::test_cost_lines_warn_when_a_non_binance_run_keeps_the_binance_fee`<br>`test_run_research.py::test_data_dir_without_events_flags_r5_and_costs_reach_every_trade` |
| **Layers veto** (C1): a veto only removes a rule-compliant entry; the trades it admits are counted both ways | `models.EntryFilter`, `config.RULE_IDS["L_ml_filter"]`; step 6 of `Gatekeeper.evaluate`; `walkforward.layer_diff` (by `(pair, signal_ts)`, per window); REPORT.md section 5 | `test_gatekeeper.py::test_entry_filter_can_only_remove_and_reports_its_probability`<br>`test_ml_filter.py::test_veto_wording_follows_contract_c1`<br>`test_walkforward.py::test_layer_diff_counts_both_directions`<br>`test_run_research.py::test_layer_section_uses_the_c1_wording`<br>`test_run_research.py::test_layer_counts_are_recomputed_from_the_written_journals` |
| **Layer `L_ml_filter`**: fitted on purged TRAIN candidates only | `ml_filter.MLFilter` (6-feature L2 logistic, break-even threshold); `walkforward.walk_forward` fits on purged TRAIN candidates and applies the filter unchanged to TEST | `test_walkforward.py::test_layer_is_fit_on_purged_train_candidates_only`<br>`test_walkforward.py::test_ml_fit_is_invariant_to_test_period_candles`<br>`test_walkforward.py::test_insufficient_data_is_untested_without_fallback`<br>`test_ml_filter.py::test_filter_learns_hour_edge_and_improves_test_expectancy` |
| **ML model fingerprint and live loader** (A3): the model promoted is the model tested | `MLFilter.fingerprint` (sha256 of the canonical model JSON); `MLFilter.to_json` / `from_json(text, expected_fingerprint)`; `AdoptionRecord.model_fingerprint`; `adoption.check_promotion(..., model_fingerprint)` (`ADOPT_fingerprint`) | `test_ml_filter.py::test_fingerprint_is_a_deterministic_sha256_of_the_canonical_model`<br>`test_ml_filter.py::test_fingerprint_changes_whenever_the_model_changes`<br>`test_ml_filter.py::test_to_json_from_json_round_trip_is_exact`<br>`test_ml_filter.py::test_from_json_refuses_a_model_that_is_not_the_fingerprinted_one`<br>`test_adoption.py::test_model_fingerprint_mismatch_blocks_every_stage`<br>`test_adoption.py::test_recorded_model_but_none_supplied_blocks_every_stage`<br>`test_adoption.py::test_ml_variant_without_recorded_model_fingerprint_blocks`<br>`test_run_research.py::test_ml_adoption_record_pins_the_fitted_model` |
| **Layer `L_expectancy_guard`** and the `base+guard` candidate: off by default; only shrinks risk | `journal_rules.risk_multiplier`; `Gatekeeper.guard_multiplier` (only trades whose exit is certain at the decision); `run_research` pre-registers `base+guard` with its own config and record | `test_journal_rules.py::test_guard_is_off_by_default`<br>`test_journal_rules.py::test_guard_triggers_on_negative_last_window_expectancy`<br>`test_journal_rules.py::test_guard_backtest_journal_uses_only_certain_exits`<br>`test_journal_rules.py::test_cli_expectancy_guard_prints_the_adoption_note`<br>`test_gatekeeper.py::test_risk_is_min_of_cap_and_budget_times_guard`<br>`test_run_research.py::test_guard_is_a_preregistered_candidate_with_its_own_record` |
| Single entry path, restart-safe | `Gatekeeper.evaluate` + `plan_fill`; `CircuitBreakers.from_journal` | `test_backtester.py::test_every_signal_candle_goes_through_the_gatekeeper`<br>`test_gatekeeper.py::test_rules_are_applied_in_the_documented_order`<br>`test_gatekeeper.py::test_journal_restart_reproduces_the_backtest_decision` |
| No look-ahead | causal indicators; `find_stop` reads `candles[0..i]` only; the backtester cuts its input at `end_ts`; unscheduled news blocks only from its own time (C2) | `test_gatekeeper.py::test_decisions_never_depend_on_later_candles`<br>`test_backtester.py::test_perturbing_the_future_never_changes_the_past`<br>`test_backtester.py::test_end_ts_is_identical_to_truncated_data`<br>`test_indicators.py::test_indicators_never_change_when_future_values_are_removed` |
| Selection on TRAIN only | `strategy_discovery.discover` (selection frozen before any TEST backtest; only the selected variant is judged) | `test_strategy_discovery.py::test_selection_is_recorded_before_any_test_backtest`<br>`test_strategy_discovery.py::test_selection_is_invariant_to_test_period_candles`<br>`test_strategy_discovery.py::test_only_the_selected_variant_is_judged` |
| **Label rule** (C4): m = 4, one-sided 1 - 0.05/m, iid AND calendar-month block bootstrap, the more conservative bound | `metrics.summarize` / `metrics.label` (`M_CANDIDATES`, `ALPHA`, `N_BOOT` = 4000, `ROBUST_MIN_N` = 30); `walkforward` stats; `run_research.verdict_line` | `test_metrics.py::test_label_branches`<br>`test_metrics.py::test_robust_needs_30_trades_in_each_window_whatever_min_says`<br>`test_metrics.py::test_label_multiplicity_is_fixed_by_m_and_alpha`<br>`test_metrics.py::test_iid_bounds_follow_the_documented_algorithm`<br>`test_metrics.py::test_month_blocks_group_by_the_utc_month_of_the_exit`<br>`test_metrics.py::test_iid_passes_but_calendar_month_block_bootstrap_fails`<br>`test_walkforward.py::test_stats_fix_m_alpha_and_resamples`<br>`test_walkforward.py::test_label_params_reproduce_the_verdict_from_the_journals`<br>`test_run_research.py::test_null_world_reports_no_robust_result` |
| **Drawdown** (C5): realised and mark-to-market, `dd_ok` rule | `metrics.mtm_equity_curve` / `mtm_max_dd_pct`, `metrics.train_dd_quantile`, `metrics.dd_limit` / `dd_check` | `test_metrics.py::test_dd_check`<br>`test_metrics.py::test_train_dd_quantile_follows_the_documented_algorithm`<br>`test_metrics.py::test_mark_to_market_curve_values_open_positions_at_each_close`<br>`test_metrics.py::test_mark_to_market_realises_exits_and_carries_the_last_close`<br>`test_run_research.py::test_every_result_reports_both_drawdowns_and_the_rule` |
| **Calibration and power** (C6): zero_edge world, effect strength, opt-in gaps, Wilson intervals, MDE | `synthetic.make_world(..., effect_strength, gaps)`; `run_research --calibrate-seeds`, `--power-strengths` | `test_synthetic.py::test_zero_edge_world_is_the_planted_mechanism_at_the_calibrated_strength`<br>`test_synthetic.py::test_effect_strength_overrides_the_planted_drift`<br>`test_synthetic.py::test_gaps_are_opt_in_rare_sized_and_otherwise_share_the_noise`<br>`test_synthetic.py::test_default_worlds_are_byte_identical_to_the_committed_ones`<br>`test_run_research.py::test_wilson_interval`<br>`test_run_research.py::test_minimum_detectable_effect_interpolates_between_strengths`<br>`test_run_research.py::test_power_curve_cli_writes_power_md`<br>`test_run_research.py::test_calibration_row_describes_every_candidate` |
| **Evidence-bound adoption** (C3) | `adoption.check_promotion` / `require_stage` (hashes, journal recomputation, provenance, review pack, testnet cross-trade audit); `run_research` writes the fields | `test_adoption.py::test_skipping_any_stage_blocks_every_later_stage`<br>`test_adoption.py::test_testnet_of_exactly_14_days_passes_to_live`<br>`test_adoption.py::test_fully_valid_real_record_with_an_all_y_pack_passes_every_stage`<br>`test_adoption.py::test_fingerprint_mismatch_blocks_every_stage`<br>`test_adoption.py::test_synthetic_or_missing_provenance_blocks_after_walk_forward`<br>`test_adoption.py::test_real_provenance_needs_data_file_hashes`<br>`test_adoption.py::test_report_hash_mismatch_blocks`<br>`test_adoption.py::test_journal_hash_mismatch_blocks`<br>`test_adoption.py::test_typed_label_differs_from_recomputed_blocks`<br>`test_adoption.py::test_typed_numbers_must_match_the_recomputed_ones`<br>`test_adoption.py::test_cycle1_forged_record_is_blocked`<br>`test_adoption.py::test_review_pack_hash_mismatch_blocks`<br>`test_adoption.py::test_blank_reviewer_ok_row_blocks`<br>`test_adoption.py::test_one_rejected_row_blocks_and_states_the_policy`<br>`test_adoption.py::test_review_pack_must_describe_the_journal_trades`<br>`test_adoption.py::test_testnet_journal_is_cross_checked`<br>`test_adoption.py::test_testnet_r6_r9_violations_block_live`<br>`test_adoption.py::test_testnet_r5_is_checked_when_the_calendar_is_recorded`<br>`test_run_research.py::test_adoption_records_are_bound_to_their_evidence`<br>`test_run_research.py::test_adoption_check_on_written_synthetic_records` |
| **Importing the existing bot's journal** | `journal.import_external`, `python -m research.trendbot.journal convert` | `test_journal.py::test_import_external_maps_columns_and_derives_a1_risk_and_r`<br>`test_journal.py::test_cli_convert_writes_a_journal_that_round_trips`<br>`test_journal.py::test_cli_convert_errors`<br>`test_adoption.py::test_converted_bot_journal_feeds_the_testnet_audit` |
| Independent audit (library and CLI, `--config` aware) | `invariants.check_invariants`, `invariants.cross_trade_violations`; `python -m research.trendbot.invariants` | `test_invariants.py::test_full_synthetic_runs_are_clean`<br>`test_invariants.py::test_cross_trade_checks_are_clean_on_real_runs`<br>`test_invariants.py::test_cli_audits_a_journal_and_flags_a_tampered_one`<br>`test_invariants.py::test_cli_config_audits_a_discovery_variant_journal`<br>`test_invariants.py::test_cli_fee_rate_audits_a_high_fee_journal`<br>`test_invariants.py::test_cli_rejects_an_illegal_config`<br>`test_invariants.py::test_cli_runs_and_audits_a_synthetic_world` |

## 4. How to run

Run everything from the repository root. Only the `ccxt` download needs a network.

### 4.1 Tests and lint

```bash
python -m pytest research/trendbot/tests -q      # needs pytest; research/pytest.ini is used
ruff check research/
ruff format --check research/
```

In the build sandbox (a venv with pytest and ruff, 4 CPUs) the suite ran 1,158 tests in 115 s
in this cycle: 1,157 passed and 1 failed (section 9 names it). Both ruff commands passed.

### 4.2 Synthetic worlds, calibration and the power curve (offline)

The synthetic worlds have a KNOWN ground truth. They test the *methodology* and are never
evidence about real markets. The generative model, as it is in `synthetic.py` now (every
parameter was picked by hand to look roughly crypto-like; none was estimated from market
data):

- 6 years of 4H candles from 2019-01-01T00:00Z (13,140 per pair). Each candle opens at the
  previous close, so there are no price gaps, unless the opt-in `gaps=True` mode is used (1%
  of candles open 1-3 sigma away from the previous close, mean-preserving: jump risk, no
  edge; it exercises gap-through stops and the R8 gap-through entry skip).
- Log returns share one market factor. Per-candle volatility: BTC 0.8%, ETH 1.0%, BNB 0.9%.
  Factor share of variance: BTC 0.80, ETH 0.70, BNB 0.55 (return correlations about
  0.62-0.75). Shocks follow a two-state scale mixture (10% of candles at 2.5x the scale),
  which gives fat-ish tails. The `-v/2` convexity term makes the price an exact martingale
  when no drift is planted, so no rule can have an edge before costs.
- Volume is lognormal around a slow AR(1) level (phi 0.998, sd 0.35, noise sd 0.25). 7% of
  candles are volume spikes, multiplied by U(2, 4) and drawn independently of returns.
- BNB gets an extra 1-3% downside "news wick" on 2% of candles.
- The planted effect: a spike candle that closes up triggers extra drift of 0.7 sigma per
  candle (`EFFECT_MU_SIGMAS`) for the next 12 candles (`EFFECT_HORIZON`). The drift is
  offset everywhere else, so it sums to about zero and buy-and-hold gains nothing from it.
  `make_world(..., effect_strength=X)` sets the strength in sigma units (power curves).
- Worlds: `null` (no trigger ever qualifies); `planted` (every up-closing spike, over the
  whole history); `decay` (triggers only before the 70% split; after it the world is
  exactly `null`); `hour_edge` (only spikes whose candle CLOSES 12:00-20:00 UTC);
  `zero_edge` (the planted mechanism at 0.15 sigma: a real PRE-cost edge that fees and
  slippage eat, so the baseline's expected NET R is about 0 in both windows, the boundary
  of the null hypothesis).
- Synthetic news with no price impact, to exercise R5: 1-3 high-impact macro events (scope
  `ALL`) per month, one high-impact regulatory event (`EXCHANGE:binance`, unscheduled) per
  quarter, one BNB burn per quarter, one BNB launchpool per month, one ETH unlock per year.
- All worlds share the same noise for a given seed; only the planted drift differs.

Strength check, re-measured in this cycle with `backtester.run_backtest` on the full 6-year
history (default config, seeds 1-5, the C2 news semantics; section 9): the baseline averaged
-0.10R in `null` (707 trades), +0.42R in `planted` (816), +0.26R in `decay` (805), +0.15R in
`hour_edge` (833) and +0.02R in `zero_edge` (740). In `hour_edge`, its trades whose signal
candle closes at 12-20 UTC averaged +0.53R (389 trades) and the others -0.18R (444).

```bash
# one full pipeline run (baseline, 13-variant discovery, ML layer, expectancy guard) on one
# world and seed; --now fixes the time stamped on the adoption records, so a re-run with
# the same --out-dir is byte-identical
python -m research.trendbot.run_research --synthetic planted --seed 1 \
    --now 2026-09-27T12:00:00Z --out-dir research/results/synthetic_planted_s1
# the same for the other worlds
python -m research.trendbot.run_research --synthetic null --seed 1 \
    --now 2026-09-27T12:00:00Z --out-dir research/results/synthetic_null_s1
python -m research.trendbot.run_research --synthetic decay --seed 1 \
    --now 2026-09-27T12:00:00Z --out-dir research/results/synthetic_decay_s1
python -m research.trendbot.run_research --synthetic hour_edge --seed 1 \
    --now 2026-09-27T12:00:00Z --out-dir research/results/synthetic_hour_edge_s1
python -m research.trendbot.run_research --synthetic zero_edge --seed 1 \
    --now 2026-09-27T12:00:00Z --out-dir research/results/synthetic_zero_edge_s1

# calibration: label frequencies over seeds 1..N (writes CALIBRATION.md + calibration_runs.csv)
python -m research.trendbot.run_research --synthetic null --calibrate-seeds 20 --workers 4 \
    --out-dir research/results/calibration_null
python -m research.trendbot.run_research --synthetic planted --calibrate-seeds 20 --workers 4 \
    --out-dir research/results/calibration_planted
python -m research.trendbot.run_research --synthetic hour_edge --calibrate-seeds 20 --workers 4 \
    --out-dir research/results/calibration_hour_edge
python -m research.trendbot.run_research --synthetic zero_edge --calibrate-seeds 50 --workers 4 \
    --out-dir research/results/calibration_zero_edge
python -m research.trendbot.run_research --synthetic decay --calibrate-seeds 50 --workers 4 \
    --out-dir research/results/calibration_decay

# power curve: 20 seeds at each of 7 planted strengths (writes POWER.md + power_runs.csv)
python -m research.trendbot.run_research --synthetic planted --calibrate-seeds 20 \
    --power-strengths 0.15 0.3 0.45 0.6 0.75 0.9 1.05 --workers 4 \
    --out-dir research/results/power_planted

# cost sensitivity (not committed): the planted world at a 0.6%-per-side fee
python -m research.trendbot.run_research --synthetic planted --seed 1 \
    --fee-rate 0.006 --exchange-id coinbase --out-dir /tmp/planted_fee0006

# run and independently audit one synthetic backtest (default config, full history)
python -m research.trendbot.invariants --synthetic planted --seed 1
python -m research.trendbot.invariants --synthetic planted --seed 1 --gaps
```

Wall times in this cycle on 4 CPUs are listed in section 9 (a seed-1 run takes about 35 s,
the 50-seed calibrations about 6 minutes each and the power curve about 16 minutes).

Each seed-1 run writes `REPORT.md` (12 sections), a TRAIN and a TEST journal for each of the
four pre-registered candidates under `journals/`, and a review pack per candidate. It writes
one `adoption_<variant>.json` per candidate: the baseline, the selected variant if it is
not the baseline, the ML layer if it was fitted, and `base+guard`. It also writes
`model_base_plus_ml.json` when the ML layer was fitted, `config_<variant>.json` for every
non-default config (a discovery variant, `base+guard`, other costs), and `run.log` (the
exact command and the console summary). The process exits with 0 on success, 1 if any
backtest broke an invariant, and 2 on a usage or data error.

### 4.3 Real data (on a machine with network access)

**Nothing below has been run against a live exchange.** The build sandbox has no network
and no ccxt. On a networked machine, install it into the project's virtual environment with
`pip install ccxt` (following your organisation's software-installation policy).
`fetch_data` has only been run against a fake exchange module (section 9). Treat the first
real download as untested code: read the gap report it prints, and spot-check a few candles
against the exchange's own chart.

```bash
# Binance spot: all three pairs
python -m research.trendbot.fetch_data --exchange binance \
    --pairs BTC/USDT ETH/USDT BNB/USDT --timeframe 4h --since 2019-01-01 --out research/data

# Coinbase Advanced Trade (ccxt id "coinbase"): BNB is not listed there, so leave it out
# (if requested it is skipped as "not listed" and the command exits 1). No 4h candles:
# fetch_data requests the largest supported divisor (2h per ccxt's timeframe table, not
# verified live), aggregates COMPLETE buckets only, and uses 300-candle pages.
python -m research.trendbot.fetch_data --exchange coinbase \
    --pairs BTC/USDT ETH/USDT --timeframe 4h --since 2019-01-01 --out research/data/coinbase
```

Files are written as `<out>/<BASE>_<QUOTE>-4h.csv` with the header
`ts,open,high,low,close,volume`. `data.load_dataset` reads this layout and applies the 4H
spacing check of section 2.1.

**News calendar (R5).** Build `research/data/events.csv` with the header and semantics of
section 2.4:

- `time_utc` and `known_from_utc` are ISO-8601 with an explicit offset (`Z` or `+00:00`) or
  integer epoch ms. Offset-less times are rejected.
- `scope` is `ALL`, a base asset (`BTC`, `BNB`, ...) or `EXCHANGE:<ccxt id>`.
- `impact` is `high`, `medium` or `low`.
- `kind` is one of `macro`, `unlock`, `bnb_burn`, `launchpool` (scheduled) or `regulatory`,
  `legal`, `other` (unscheduled).
- `known_from_utc` (optional column): when the event became public. Leave it empty for
  scheduled events known well in advance and for true surprises; fill it when a scheduled
  event was announced late, or when an "unscheduled" kind was in fact announced ahead.
- A malformed row raises an error that names the line number.

`EXCHANGE:<id>` events block every pair, but only when `<id>` equals `cfg.exchange_id`
(`run_research --exchange-id`, default `binance`), so a Coinbase run with
`--exchange-id coinbase` honours `EXCHANGE:coinbase` rows.

> **Warning: without a historical calendar, R5 is not exercised in backtests.** The
> backtest can then enter around news that the live bot would skip, so the results are
> not news-filtered. The report says so in its provenance section. A partial calendar
> (for example the example file) makes the report say "loaded: yes" while R5 is only
> exercised on the covered dates. Source the rows from a real economic calendar
> (high-impact macro, scope `ALL`) and from Binance announcements (BNB burns and
> launchpools, scope `BNB`, with `known_from_utc` = the announcement time).

**Costs: pass your real fee tier.** `run_research` takes `--fee-rate` (a FRACTION of
notional per side; default 0.001 = 0.10%, the Binance spot taker default without
discounts), `--slippage-pct` (PERCENT on market fills; default 0.05) and `--exchange-id`
(default `binance`). The costs are printed on the console and in section 2 of the report,
together with the measured average fee cost per trade in R.

> **Coinbase warning.** Coinbase Advanced Trade taker fees at low volume tiers are several
> times Binance's. A Coinbase run must pass the account's REAL tier with `--fee-rate`
> (look it up in your account; this README quotes no Coinbase fee) and
> `--exchange-id coinbase`, or every number is optimistic. The report adds a WARNING line
> when a non-Binance exchange is run with the Binance default fee. How much fees matter:
> the planted seed-1 world at an illustrative 0.6% per side (the cost-sensitivity command
> in 4.2, re-run in this cycle). Baseline TRAIN | TEST: +0.296R (n=115) | +0.412R (n=51)
> at the default fee against +0.127R (n=100) | +0.043R (n=46) at 0.6%. Fees alone then
> cost 0.25R per trade instead of 0.06R. Discovery selected `rr2_vol2_rsi55-70` (TRAIN
> +0.423R, n=75 | TEST +0.364R, n=33, UNTESTED) instead of the ROBUST `rr3_vol2_rsi50-70`,
> and the verdict became "No robust result found."

**Research run, audit, review, journal rules and adoption on the fetched data.** The
`0.006` below is a placeholder for your own tier, not a quoted Coinbase fee:

```bash
python -m research.trendbot.run_research --data-dir research/data \
    --events research/data/events.csv \
    --fee-rate 0.001 --slippage-pct 0.05 --exchange-id binance \
    --out-dir research/results/real_binance
python -m research.trendbot.run_research --data-dir research/data/coinbase \
    --pairs BTC/USDT ETH/USDT --events research/data/events.csv \
    --fee-rate 0.006 --slippage-pct 0.05 --exchange-id coinbase \
    --out-dir research/results/real_coinbase

# the walk-forward split printed in REPORT.md section 2 (this value is only an example)
SPLIT_ISO=2023-03-14T00:00:00Z
SPLIT_MS=$(python -c "from research.trendbot.journal import iso_to_ms; print(iso_to_ms('$SPLIT_ISO'))")

# independent re-audit of a journal against the candles, with the config the run used (a
# TEST journal needs --start-ts = the split in ms; a TRAIN journal needs --end-ts instead)
python -m research.trendbot.invariants \
    --journal research/results/real_binance/journals/base_test.csv \
    --data-dir research/data --events research/data/events.csv --start-ts "$SPLIT_MS"
python -m research.trendbot.invariants \
    --journal research/results/real_coinbase/journals/base_test.csv \
    --data-dir research/data/coinbase --events research/data/events.csv --start-ts "$SPLIT_MS" \
    --config research/results/real_coinbase/config_base.json

# trade-by-trade review pack for one journal (run_research already writes packs covering
# TRAIN and TEST for the four candidates); a non-default run passes its costs
python -m research.trendbot.review_sheet \
    --journal research/results/real_coinbase/journals/base_test.csv \
    --out-dir research/results/real_coinbase/review_base_test --split "$SPLIT_ISO" \
    --fee-rate 0.006 --slippage-pct 0.05

# every adaptation the bot would apply at the end of TEST because of past trades (R9
# benches / halt, and the expectancy guard if requested), each with the journal rows that
# caused it. Backtest journals need --backtest-journal (section 2.3). REPORT.md section 9
# prints this command with the right --equity and --now.
python -m research.trendbot.journal_rules \
    --journal research/results/real_binance/journals/base_test.csv --equity 10000 \
    --now 2024-12-30T00:00:00Z --backtest-journal --expectancy-guard

# adoption gate. REPORT.md section 11 prints the exact command for every record: a
# non-default config (other costs, a discovery variant, base+guard) needs --config, and the
# ML record needs the model fingerprint printed in REPORT.md section 5.
python -m research.trendbot.adoption check \
    --record research/results/real_binance/adoption_base.json --stage HUMAN_REVIEW
python -m research.trendbot.adoption check \
    --record research/results/real_binance/adoption_base_plus_guard.json --stage HUMAN_REVIEW \
    --config research/results/real_binance/config_base_plus_guard.json
python -m research.trendbot.adoption check \
    --record research/results/real_coinbase/adoption_base.json --stage HUMAN_REVIEW \
    --config research/results/real_coinbase/config_base.json
```

The offline equivalents on the committed synthetic outputs are runnable today, and they
printed what is shown in this cycle:

```bash
python -m research.trendbot.journal_rules \
    --journal research/results/synthetic_planted_s1/journals/base_test.csv \
    --equity 12030.61 --now 2024-12-30T00:00:00Z --backtest-journal   # No active adaptations.
python -m research.trendbot.adoption check \
    --record research/results/synthetic_planted_s1/adoption_base.json --stage WALK_FORWARD  # PASS, exit 0
python -m research.trendbot.adoption check \
    --record research/results/synthetic_planted_s1/adoption_rr3_vol2_rsi50-70.json \
    --stage HUMAN_REVIEW \
    --config research/results/synthetic_planted_s1/config_rr3_vol2_rsi50-70.json  # BLOCKED: ADOPT_provenance
python -m research.trendbot.adoption check \
    --record research/results/synthetic_planted_s1/adoption_base.json --stage HUMAN_REVIEW  # BLOCKED: ADOPT_provenance, ADOPT_walk_forward
python -m research.trendbot.adoption check \
    --record research/results/synthetic_planted_s1/adoption_base_plus_ml.json \
    --stage WALK_FORWARD \
    --model-fingerprint 0986e8bdf0e5206e179c52b257b8872e4d740c0438bee6e8f4f175aa8d17f736  # PASS, exit 0
python -m research.trendbot.adoption check \
    --record research/results/synthetic_planted_s1/adoption_base_plus_guard.json \
    --stage WALK_FORWARD \
    --config research/results/synthetic_planted_s1/config_base_plus_guard.json      # PASS, exit 0
python -m research.trendbot.adoption hash \
    research/results/synthetic_planted_s1/review_base/trades_review.csv   # = human_review.review_sha256
python -m research.trendbot.adoption fingerprint          # canonical config JSON + sha256
python -m research.trendbot.adoption init --variant base --out rec.json   # empty record
```

Every committed record passes `--stage WALK_FORWARD` with its `--config` /
`--model-fingerprint` (its report, journals and review pack hashes match, and the
walk-forward numbers recomputed from the journals equal the typed ones) and is BLOCKED
at `--stage HUMAN_REVIEW` by `ADOPT_provenance`, whatever its label: a synthetic world is
never evidence about real markets. The selected planted variant is the one ROBUST record,
so provenance is its only blocking reason. Without `--config`, the `rr3_vol2_rsi50-70` and
`base+guard` checks are also BLOCKED by `ADOPT_fingerprint` (the default config is not the
one that was tested); without `--model-fingerprint`, so is the ML check. `adoption --config
c.json` takes a JSON object of `StrategyConfig` overrides, validated, so a loosening is
refused. `check` prints `PASS` or every blocking reason, then what it could not verify, and
exits with 0 or 1.

### 4.4 Importing the existing bot's journal (`journal convert`)

The live bot keeps its own CSV journal with its own column names. `journal convert` maps it
onto this package's journal format, so `journal_rules`, the adoption TESTNET audit and
`invariants.cross_trade_violations` can read it. There is no universal default mapping: a
wrong map converts silently wrong numbers, so write `map.json` for YOUR journal's real
columns and compare a few converted rows with the bot's own figures.

```text
bot_trades.csv (a made-up format; use your bot's real header):
Ticket,Symbol,Opened (UTC),Entry Px,SL Px,TP Px,Size,Closed (UTC),Exit Px,Close Reason,Account Equity

map.json:
{"columns": {"Ticket": "trade_id", "Symbol": "pair", "Opened (UTC)": "entry_ts",
             "Entry Px": "entry_price", "SL Px": "stop", "TP Px": "target", "Size": "qty",
             "Closed (UTC)": "exit_ts", "Exit Px": "exit_price",
             "Close Reason": "exit_reason", "Account Equity": "equity"},
 "exit_reason_values": {"take_profit": "TP", "stop loss": "SL", "manual": "END"},
 "time_format": "iso", "defaults": {"variant": "base"}}
```

```bash
python -m research.trendbot.journal convert --in bot_trades.csv --map map.json \
    --out research/data/bot_journal.csv --fee-rate 0.001 --slippage-pct 0.05
python -m research.trendbot.journal_rules --journal research/data/bot_journal.csv \
    --equity 10000 --now 2024-04-04T00:00:00Z   # a live journal: no --backtest-journal
```

- Required: `pair`, `entry_ts`, `entry_price`, `stop`, `target`, `qty`, `exit_ts`,
  `exit_price`, `exit_reason` (a column or a `defaults` value); every row must be a CLOSED
  trade. `risk_pct` needs a column or an `equity` column/default.
- Derived when not mapped: `risk_amount = qty * L_u` (A1, with the `--fee-rate` and
  `--slippage-pct` you pass: use the bot's REAL costs), `fees`, `pnl` (a mapped pnl must be
  net of fees), `signal_ts` (the 4H candle before the fill candle), `trade_id`. `r_multiple`
  is always `pnl / risk_amount`.
- `time_format` is `iso` (naive = UTC), `ms` or `s`. Pairs such as `BTCUSDT`, `BTC-USDT` or
  `btc_usdt` become `BTC/USDT`. Set `defaults.variant` to the adopted variant id, because
  the TESTNET check requires every trade to be of the record's variant.
- A converted journal holds REAL fill times: use it with `journal_rules` WITHOUT
  `--backtest-journal`, and as the adoption `testnet.journal_path` (audited with exit offset
  0). The `invariants --journal` CLI of section 4.5 is for BACKTEST journals: it also checks
  per-candle facts of the backtester (fill = next open plus slippage, exit on the first
  touch), which a live journal does not follow.

### 4.5 The invariants CLI

`python -m research.trendbot.invariants` has two modes, and it exits 1 if it finds any
violation:

- `--journal J --data-dir D [--events E] [--start-ts MS] [--end-ts MS] [--config C]
  [--fee-rate F] [--slippage-pct S]` audits a backtester journal against the candles it
  traded on. The journal must hold every trade of the run, because the breakers and the
  correlation cap are recomputed from it.
- `--synthetic WORLD --seed N [--years Y] [--gaps] [--config C]` runs a synthetic backtest
  and audits it.

Both use the backtest exit-time convention of section 2.3. The config is the default
`StrategyConfig`, then the JSON overrides of `--config` (validated: a loosened rule is
refused and exits 2), then `--fee-rate` / `--slippage-pct`. A journal written by a discovery
variant or with non-default costs must be audited with the config that produced it, or its
exact -1R / +RR outcomes and fill prices are (correctly) reported as inconsistent. In this
cycle, on the committed planted seed-1 `rr3_vol2_rsi50-70` TEST journal (35 trades) and the
exported planted candles and events: with `--config
research/results/synthetic_planted_s1/config_rr3_vol2_rsi50-70.json` it found 0 violations;
without it, 16, one per take-profit (they make +3R, not the default +2R). REPORT.md section
8 is the in-process audit of every backtest, run with each backtest's own config.

## 5. How to read results

Every results table puts **TRAIN and TEST side by side**. The split is chronological at 70%
of the common time range of all pairs. TEST starts with fresh equity and fresh circuit
breakers, and indicators warm up on earlier candles only. Fitted and selected things
(discovery selection, ML coefficients) see TRAIN only. Section 2 of every report states the
costs used and the measured fee cost per trade in R.

**The label rule (CONTRACT.md v3 C4), fixed in advance and never tuned on TEST outcomes.**
Four pre-registered candidates get one TEST look each: `base`, the discovery variant
selected on TRAIN, `base+ml` and `base+guard`, so **m = 4**. Every other discovery variant
and the regime-OFF test variant is reported as `context: <label>, not judged`.
`metrics.label(train, test)` applies these rules in order (`min_train = min_test = 30`
closed trades):

| order | condition | label | meaning |
|---|---|---|---|
| 1 | TRAIN n < 30 or TEST n < 30 | `UNTESTED` | too few trades to judge (ROBUST needs 30 + 30 whatever `min_*` say) |
| 2 | TRAIN avg R <= 0 | `NO-EDGE` | nothing to validate |
| 3 | TEST avg R <= 0 | `TRAIN-ONLY` | the edge did not hold out of sample (likely curve-fit) |
| 4 | adjusted lower bound of the TEST mean <= 0 | `UNTESTED` | positive but not distinguishable from zero after multiplicity correction |
| 5 | otherwise | `ROBUST` | positive expectancy on TRAIN and TEST, TEST bound above zero |

The adjusted lower bound is a one-sided bound at confidence **1 - 0.05/m = 98.75%**
(Bonferroni over the four looks, so that when no candidate has an edge the chance that ANY
is called ROBUST is at most about 5%). It is computed twice on the TEST R sequence, with
4000 seeded resamples each: an **iid bootstrap** of the trades, and a **calendar-month
block bootstrap** that resamples whole UTC months of exits, so trades that cluster in one
regime count as one piece of evidence instead of many. The label uses the **more
conservative (smaller)** of the two bounds. The reports also print the ordinary 90% iid and
block intervals as context. Example, planted seed 1 baseline: TRAIN +0.296R (n=115) | TEST
+0.412R (n=51), with 98.75% TEST bounds -0.059R (iid) and -0.125R (block over 20 months), so
UNTESTED; the selected `rr3_vol2_rsi50-70`: TRAIN +0.836R (n=73) | TEST +0.829R (n=35), bounds
+0.029R (iid) and +0.167R (block), so ROBUST.

A candidate "reaches the TEST gate" when TRAIN avg R > 0 and both windows hold >= 30
trades; from there the bootstrap bound decides. The ML layer is also `UNTESTED` when it
cannot be fitted: fewer than 100 TRAIN candidates, or fewer than 20 wins or 20 losses. In
that case no backtest runs and nothing silently falls back to the unfiltered rules.

**Drawdown (CONTRACT.md v3 C5).** Every result reports two TEST (and TRAIN) max drawdowns,
in percent of equity:

- **realised**: the peak-to-trough drop of the closed-trade equity curve;
- **mark-to-market (MTM)**: the equity at every 4H close, with each open position valued at
  that close (`qty * (close - entry) - entry fee`; the exit fee and slippage are charged when
  the trade is realised). It sees the intra-trade dips that the closed-trade curve hides.

**`dd_ok` = TEST MTM max drawdown <= min(20%, p95)**, where p95 is the 95th percentile of the
max drawdown of 4000 TRAIN trade sequences bootstrapped at the TEST trade count, compounding
each trade's `r_multiple * risk_pct` (the same percent-of-equity units). The 20% cap always
applies, and a TEST drawdown that is unusual for the TRAIN trades fails even below it.
`adoption.py` requires both `ROBUST` and `dd_ok` to leave WALK_FORWARD. Example, planted seed
1 baseline: TEST MTM 5.08% (realised 4.90%) against the limit 9.58%, so passed; decay seed 1
baseline: TEST MTM 13.76% against 9.60%, so failed.

The verdict considers only the four pre-registered candidates. It prints the ROBUST ones,
or exactly **"No robust result found."** That is a valid result, and often the correct one.
UNTESTED means "not demonstrated", not "no edge".

**Do not re-select on TEST.** The report shows the TEST label of all 13 discovery variants
for context. Picking one of them *because* of its TEST numbers turns TEST into TRAIN and
invalidates the result. The TRAIN column of the selected variant is biased upward (it is
the best of 12), and the ML layer's TRAIN column is in-sample. Only TEST columns are
out-of-sample. A new idea is a new variant: it starts again at BACKTEST, and its config
fingerprint changes.

## 6. Honest status

A summary of [`research/results/RESULTS_SUMMARY.md`](../results/RESULTS_SUMMARY.md) and the
committed REPORT.md, CALIBRATION.md and POWER.md files, all regenerated from the v3 code and
re-run byte-identically in this cycle (section 9).

- **No real-data result exists yet.** The sandbox has no network and no ccxt, so every
  number comes from synthetic worlds. These verify the methodology and are not evidence
  about BTC/ETH/BNB. No variant may be adopted on their strength, and `adoption check`
  blocks every committed synthetic record at HUMAN_REVIEW (`ADOPT_provenance`). (The same
  candles fed in through `--data-dir` would be recorded as "real": section 11, step 2.)
- Scope: 305 pipeline runs (5 seed-1 runs, 160 calibration seeds, 140 power-curve seeds),
  each on 6 years of 4H candles and 3 pairs at the default costs (0.10% fee per side, 0.05%
  slippage). The invariant audit found 0 violations in every backtest of every run. Fees
  alone cost about 0.06R per baseline trade (median stop distance 3.48-3.79%).

**Seed 1, ground truth vs labels.** Each cell is `label: TRAIN avg R (n) | TEST avg R (n)`.

| world (truth) | baseline | discovery-selected | base+ml | base+guard | verdict |
|---|---|---|---|---|---|
| null (no edge) | NO-EDGE: -0.138 (98) \| -0.121 (58) | `rr2.5_vol2_rsi50-70` NO-EDGE: -0.086 (81) \| -0.143 (49) | UNTESTED: +0.267 (45) \| -0.294 (17) | NO-EDGE: -0.183 (108) \| -0.121 (58) | No robust result (correct) |
| zero_edge (net ~0) | NO-EDGE: -0.004 (94) \| -0.050 (60) | `rr2_vol2_rsi55-70` TRAIN-ONLY: +0.048 (75) \| -0.020 (49) | UNTESTED: +0.333 (9) \| +0.500 (6) | NO-EDGE: -0.044 (102) \| -0.050 (60) | No robust result (correct) |
| decay (edge in TRAIN only) | TRAIN-ONLY: +0.307 (114) \| -0.143 (56) | `rr3_vol2_rsi50-70` TRAIN-ONLY: +0.742 (70) \| -0.043 (46) | TRAIN-ONLY: +0.590 (88) \| -0.073 (55) | TRAIN-ONLY: +0.307 (114) \| -0.143 (56) | No robust result (correct) |
| planted (edge everywhere) | UNTESTED: +0.296 (115) \| +0.412 (51) | `rr3_vol2_rsi50-70` **ROBUST**: +0.836 (73) \| +0.829 (35) | UNTESTED: +0.489 (94) \| +0.235 (34) | UNTESTED: +0.296 (115) \| +0.412 (51) | ROBUST (selected variant; correct) |
| hour_edge (edge at 12-20 UTC) | NO-EDGE: -0.044 (100) \| +0.050 (60) | `rr3_vol2_rsi50-70` UNTESTED: +0.203 (72) \| +0.436 (39) | UNTESTED: +0.299 (69) \| +0.295 (44) | NO-EDGE: -0.057 (111) \| +0.082 (61) | No robust result (a miss) |

In hour_edge the ML layer raised expectancy from TRAIN -0.044R | TEST +0.050R (baseline) to
TRAIN +0.299R (in-sample) | TEST +0.295R, as the ground truth predicts, but its 98.75% TEST
bounds (-0.182R iid, -0.057R block, n=44) include zero, so a real edge went undemonstrated.

**Calibration (seeds 1..N, 90% Wilson intervals).** "Verdict ROBUST" means any of the four
candidates was ROBUST (the family-wise rate); it is the FALSE-POSITIVE rate where no edge
survives costs in TEST, and the detection rate where one does.

| world (truth) | seeds | verdict ROBUST | baseline ROBUST | baseline reached the TEST gate | baseline ROBUST given the gate | baseline mean avg R TRAIN \| TEST | baseline dd_ok |
|---|---:|---|---|---|---|---|---|
| null (no edge) | 20 | **0/20 = 0% (0-12%)** FP | 0/20 (0-12%) | 3/20 (6-32%) | 0/3 (0-47%) | -0.107 \| -0.021 | 19/20 |
| zero_edge (net ~0, the H0 boundary) | 50 | **1/50 = 2% (0-8%)** FP | 0/50 (0-5%) | 31/50 (50-72%) | 0/31 (0-8%) | +0.035 \| +0.027 | 46/50 |
| decay (no edge in TEST) | 50 | **0/50 = 0% (0-5%)** FP | 0/50 (0-5%) | 49/50 (92-100%) | 0/49 (0-5%) | +0.458 \| -0.095 | 9/50 |
| planted (edge, 0.7 sigma) | 20 | 10/20 = 50% (33-67%) detection | 8/20 = 40% (24-58%) | 20/20 (88-100%) | 8/20 (24-58%) | +0.442 \| +0.445 | 20/20 |
| hour_edge (edge at 12-20 UTC) | 20 | 5/20 = 25% (13-43%) detection | 0/20 (0-12%) | 18/20 (74-97%) | 0/18 (0-13%) | +0.198 \| +0.201 | 18/20 |

Per candidate, `ROBUST over all seeds; ROBUST given that candidate reached the TEST gate`:

| world | discovery-selected | base+ml | base+guard |
|---|---|---|---|
| null | 0/20; 0/8 | 0/20; 0/3 | 0/20; 0/3 |
| zero_edge | 0/50; 0/39 | 1/50 (0-8%); 1/32 (1-13%) | 0/50; 0/31 |
| decay | 0/50; 0/46 | 0/50; 0/49 | 0/50; 0/49 |
| planted | 9/20 (28-63%); 9/18 | 7/20 (20-53%); 7/20 | 8/20 (24-58%); 8/20 |
| hour_edge | 3/20 (6-32%); 3/16 | 5/20 (13-43%); 5/18 | 0/20; 0/19 |

- **False positives are controlled where it is hardest:** zero_edge (a real pre-cost edge
  that costs eat) and decay (a TRAIN edge that vanishes at the split) reach the TEST gate in
  most seeds (31/50 and 49/50 for the baseline), and still produced 1/50 and 0/50 ROBUST
  verdicts. The one zero_edge ROBUST is `base+ml` in seed 38 (TEST +0.552R, n=58; bounds
  +0.138R iid, +0.125R block). The cycle-1 rule (a 90% two-sided iid CI per look) had called
  decay seed 3's baseline ROBUST (TRAIN +0.360R, n=111 | TEST +0.501R, n=39); under the m = 4
  rule its bounds are -0.038R (iid) and -0.013R (block), so it is UNTESTED.
- **Decay is caught twice:** the baseline was TRAIN-ONLY in 30/50 seeds and UNTESTED in 20/50
  (never ROBUST), and `dd_ok` failed in 41/50 seeds, because the TEST (null) drawdowns (MTM
  mean 10.9%) exceed what the TRAIN sequences with an edge predict (limit mean 8.3%). In
  planted `dd_ok` passed 20/20 (MTM mean 5.4% vs limit 8.6%).
- **Power is the price of that control.** Planted: the baseline's TEST expectancy averaged
  +0.445R (TRAIN +0.442R), yet it was ROBUST in only 8/20 seeds; all 12 misses are UNTESTED
  with TEST avg R +0.14R to +0.51R. The block bootstrap was the stricter bound in 13/20
  planted seeds, and decided the label alone (iid above zero, block not) in 1.
- **The ML layer helps only where there is something to learn.** hour_edge: mean TRAIN
  +0.198R -> +0.420R (in-sample) | TEST +0.201R -> +0.399R, better than the baseline in 18/20
  seeds, and ROBUST in each of the 5 seeds with a ROBUST verdict (the hour-blind baseline in
  none). null: TRAIN -0.107R -> -0.014R (in-sample) | TEST -0.021R -> -0.207R:
  the in-sample gain was fitting noise. planted: TRAIN +0.442R -> +0.483R | TEST +0.445R ->
  +0.435R. Vetoes do admit other trades: in null the ML journal held 26.4 | 11.0 trades per
  seed (TRAIN | TEST) that the base never took.
- **The guard adds nothing measurable:** its mean TEST avg R equals the baseline's within
  0.002R in every world (for example planted TRAIN +0.441R | TEST +0.445R vs +0.442R |
  +0.445R), and it was never ROBUST where the baseline was not. It acts mostly in TRAIN
  (29.8 half-risk entries per null seed, 0.5 in TEST), because a fresh TEST journal needs 20
  trades per pair first.

**Power curve and the minimum detectable effect** (`power_planted/POWER.md`, 20 seeds per
strength; "true mean TEST expectancy" is the mean over seeds of the baseline's TEST avg R,
with its 90% interval):

| strength (sigma/candle) | mean TRAIN avg R, base | true mean TEST expectancy, base | mean TEST n | base ROBUST = detection | any of 4 ROBUST |
|---:|---:|---|---:|---|---|
| 0.15 | +0.037 | +0.066 [-0.009, +0.141] | 45.8 | 0/20 = 0% (0-12%) | 0/20 (0-12%) |
| 0.30 | +0.163 | +0.145 [+0.070, +0.221] | 45.9 | 1/20 = 5% (1-20%) | 1/20 (1-20%) |
| 0.45 | +0.272 | +0.252 [+0.193, +0.311] | 50.3 | 2/20 = 10% (3-26%) | 4/20 (9-38%) |
| 0.60 | +0.388 | +0.351 [+0.278, +0.425] | 50.1 | 4/20 = 20% (9-38%) | 7/20 (20-53%) |
| 0.75 | +0.471 | +0.422 [+0.357, +0.486] | 48.6 | 6/20 = 30% (16-48%) | 11/20 (37-72%) |
| 0.90 | +0.590 | +0.582 [+0.508, +0.656] | 45.9 | 14/20 = 70% (52-84%) | 17/20 (68-94%) |
| 1.05 | +0.672 | +0.628 [+0.554, +0.701] | 40.0 | 15/20 = 75% (57-87%) | 16/20 (62-91%) |

- **Minimum detectable effect of the baseline at this sample size (about 40-50 TEST
  trades): 50% power at about +0.50R per trade** (strength about 0.83 sigma, interpolated
  between 0.75 and 0.9); **80% power is not reached** at any tested strength (75% at
  +0.63R). Stronger drift pushes RSI above 70, so R2 admits fewer signals and TEST n falls
  (40 at 1.05 sigma), which caps detection.
- **Smaller real edges will be labelled UNTESTED.** A true edge of about +0.15R to +0.42R
  per trade (strengths 0.30-0.75 sigma; plausible for a real market, and larger than most)
  was detected by the baseline in only 5-30% of runs here, and +0.07R in none. UNTESTED
  means "not shown", never "shown to be absent". More history (or a longer TEST window) is
  the only honest way to raise power; the label rule is fixed and will not be loosened to
  find more.

Other weaknesses:

- Calibration samples are small and paired: 20 null seeds only bound the false-positive
  rate below about 12%, and all worlds share the noise of a seed (decay seed k's TEST window
  is null seed k's), so null and decay are not independent draws.
- The drawdown rule compares a MTM drawdown with a bootstrap of closed-trade returns, which
  makes `dd_ok` conservative; it is only as good as the TRAIN window is representative.
- The planted effect is a positive-control fixture, not an estimate of any real edge, and
  realistic Coinbase low-tier fees can remove an edge of this size (section 4.3).
- Every label rests on one chronological 70/30 split. **On real data, expect UNTESTED far
  more often than ROBUST.** A ROBUST label is followed by a human review of every trade and
  at least 2 weeks of testnet, never directly by money.

## 7. Model-complexity policy and tools

- **Now: L2 logistic regression on 6 features** (`rsi`, `vol_ratio`, `ema_gap_pct`,
  `dist_regime_pct`, `hour_sin`, `hour_cos`). The penalty is fixed (`l2 = 1`), the threshold
  is the break-even probability, and nothing is tuned. It is fitted by Newton/IRLS in pure
  Python on a few hundred purged TRAIN candidates (249-407 in the five seed-1 runs; 268 in
  planted).
- **The intended tool on a networked machine is scikit-learn**, the usual Python library for
  small-sample linear models. The pure-Python model is the same model as

  ```python
  sklearn.linear_model.LogisticRegression(penalty="l2", C=1.0)  # C = 1 / l2
  ```

  fitted on features standardised with the TRAIN mean and population standard deviation
  (what `sklearn.preprocessing.StandardScaler` computes; a zero-variance column is divided
  by 1 in both), followed by the break-even threshold `p* = -avg_loss_R / (avg_win_R -
  avg_loss_R)` from TRAIN: an entry is allowed only if `predict_proba(x)[:, 1] > p*`, so
  no threshold is searched. Both minimise `sum(log-loss) + ||w||^2 / (2C)` with an
  unpenalised intercept (true for scikit-learn's default `lbfgs` solver; `liblinear`
  penalises the intercept, so do not use it). Newer scikit-learn releases may spell the L2
  penalty differently; check the installed version's documentation.
- **Why scikit-learn was not used here:** the build sandbox is standard-library only (no
  numpy, scipy or scikit-learn, and no network to install them), which is a hard constraint
  of CONTRACT.md. The mapping above is therefore by objective and has NOT been checked
  numerically here. On a networked machine, compare the two before relying on it; the
  first half of this snippet ran in this cycle and reproduced the committed hour_edge
  seed-1 model (fingerprint `b38d6af2...`), the sklearn half has not been run:

  ```python
  from research.trendbot.backtester import enumerate_candidates
  from research.trendbot.config import StrategyConfig
  from research.trendbot.ml_filter import MLFilter, feature_vector
  from research.trendbot.synthetic import make_world
  from research.trendbot.walkforward import split_ts

  cfg = StrategyConfig()
  data, events = make_world("hour_edge", 1)
  cands = enumerate_candidates(data, cfg, events, end_ts=split_ts(data, 0.7, cfg.timeframe_ms))
  ours = MLFilter.fit(cands)  # l2 = 1, TRAIN-standardised, break-even threshold
  print(ours.fingerprint(), ours.model.intercept, ours.explain(), ours.threshold)
  rows = [(feature_vector(c.features), c.r_multiple > 0) for c in cands]
  X = [x for x, _ in rows if x is not None]
  y = [int(win) for x, win in rows if x is not None]

  # networked machine only (pip install scikit-learn); NOT run in the sandbox
  from sklearn.linear_model import LogisticRegression
  from sklearn.preprocessing import StandardScaler

  skl = LogisticRegression(penalty="l2", C=1.0, tol=1e-10, max_iter=10_000)
  skl.fit(StandardScaler().fit_transform(X), y)
  print(skl.intercept_, skl.coef_)  # expected to match ours.model.intercept / ours.explain()
  ```

- **Escalation policy: LightGBM, CatBoost, other gradient boosting and neural networks
  (PyTorch, which FreqAI also hosts: `docs/freqai-configuration.md`, "PyTorch Module") are
  not used.** They become admissible only when BOTH conditions hold:
  1. there is materially more labelled history than today's few hundred TRAIN candidates;
  2. the simple logistic layer's walk-forward result is already positive (ROBUST, `dd_ok`)
     on real data.

  Even then, the flexible model is a NEW pre-registered candidate with its own model
  fingerprint, walk-forward and adoption path. A flexible model on a small sample memorises
  noise: the calibration shows that even the 6-feature logistic layer's in-sample TRAIN
  numbers overstate TEST in a world with no edge (null: TRAIN -0.014R vs TEST -0.207R).

## 8. Graduation to freqtrade and FreqAI

**freqtrade (the execution host).** This repository is freqtrade, which already provides
exchange connectivity, order handling, persistence and a dry-run mode. This package
deliberately has none of that. Graduate a variant into a freqtrade strategy only once its
adoption record has a REAL-data ROBUST walk-forward and a signed-off HUMAN_REVIEW, and do
it *before* the TESTNET stage, so that the 2-week testnet run exercises the executor that
will trade live. Constraints on the port:

- It must call `Gatekeeper.evaluate` / `plan_fill` for entries. Re-expressing R1-R9
  inside freqtrade's dataframe logic would give two code paths that can diverge.
- It must write `journal.write_journal`-format journals with real fill times (or a CSV that
  `journal convert` maps, section 4.4), so that `journal_rules` and the TESTNET audit in
  `adoption.py` can check it.
- freqtrade's dry-run is a simulated local wallet (`docs/configuration.md`). The TESTNET
  stage requires real orders on `binance-testnet`, so a dry-run does not replace it.
- Re-tuning the variant with freqtrade hyperopt is a new search, and so a new variant. Its
  config fingerprint changes and it starts again at BACKTEST.

Neither the port nor a freqtrade journal exporter exists yet.

**FreqAI (the model host).** Move to FreqAI only when the model-complexity policy above
allows a more flexible model. FreqAI's value is sliding-window retraining
(`train_period_days`, `backtest_period_days`, `live_retrain_hours`) with model families
such as LightGBM, CatBoost or PyTorch. That is only worth having with enough labelled
history per window, and each retrain is another fit whose out-of-sample result must be
measured. Required settings:

1. Keep chronological order in every split. In
   [`docs/freqai-parameter-table.md`](../../docs/freqai-parameter-table.md):
   - `shuffle` (under `freqai.data_split_parameters`, line 59): "Shuffle the training data
     points during training. Typically, to not remove the chronological order of data in
     time-series forecasting, this is set to `False`." Default `False`: keep it.
   - `shuffle_after_split` (under `freqai.feature_parameters`, line 49): "Split the data
     into train and test sets, and then shuffle both sets individually." Default `False`:
     keep it.
   - `reverse_train_test_order` (line 48): it would "use the latest data split for training
     and test on historical split of the data". Default `False` (no reversal): keep it,
     since a walk-forward tests on data AFTER the training window.
2. Keep `continual_learning` off. The maintainers' own warning, in
   [`docs/freqai-running.md`](../../docs/freqai-running.md) lines 141-142:

   > ???+ danger "Experimental functionality"
   >
   > Beware that this is currently a naive approach to incremental learning, and it has a
   > high probability of overfitting/getting stuck in local minima while the market moves
   > away from your model. We have the mechanics available in FreqAI primarily for
   > experimental purposes and so that it is ready for more mature approaches to continual
   > learning in chaotic systems like the crypto market.

**Model fingerprints (CONTRACT.md v2 A3).** An ML variant is adopted as a (config, model)
pair. `MLFilter.fingerprint()` is the sha256 of a canonical JSON of the features, TRAIN
means and standard deviations, intercept, coefficients, threshold and `l2` (floats written
as `repr` strings, so the digest pins the model bit for bit). `run_research` writes
`model_base_plus_ml.json` (`MLFilter.to_json()`: the canonical model, its fingerprint and
the TRAIN statistics) and an `adoption_base_plus_ml.json` record carrying the fingerprint.
The live bot loads the file with `MLFilter.from_json(text, expected_fingerprint=...)`
(section 2.2), which recomputes the fingerprint and raises on any mismatch. `adoption check`
blocks (`ADOPT_fingerprint`) when the supplied fingerprint differs from the recorded one,
when none is supplied for a record that has one, when one is supplied for a record without
one, and when a `+ml` variant has no recorded fingerprint. **Refitting the model (new TRAIN
data, a later split, another `l2`) changes the fingerprint, so a refitted model is a new
candidate and restarts the adoption path at BACKTEST.** FreqAI's periodic retraining would
therefore restart the path on every retrain; nothing pins a FreqAI model yet.

## 9. Verification notes

Every command in this file except `pip install ccxt` / `pip install scikit-learn` and the
scikit-learn half of the section 7 snippet was run in this cycle in the build sandbox
(Python 3.11.15, 4 CPUs, no network, no ccxt, no numpy or scikit-learn), either from the
repository root (commands that only read committed files) or from a scratch copy of
`research/` (commands that write, so that they could run exactly as written with the same
relative paths). Nothing was written into the repository; the committed files were only
read.

- **Determinism.** The five seed-1 commands, the five calibration commands and the
  power-curve command of section 4.2 were re-run as written from the scratch copy.
  `diff -r` against the committed folders found no difference in any file: 5 x 25 seed-1
  files (REPORT.md, journals, review packs, adoption records with their sha256 fields,
  config and model files, run.log), 5 x 3 calibration files and the 3 power-curve files.
  Wall times: 34-35 s per seed-1 run (five in parallel), 132-134 s per 20-seed calibration,
  341 s (zero_edge) and 371 s (decay) for 50 seeds, and 958 s for the power curve (140 seeds)
  (the last three shared the CPUs with the other checks below).
- **Section 1 and 2.5 numbers** were recounted from the 40 committed seed-1 journals
  (`journal.read_journal`) and REPORT.md section 5 of each world.
- **Section 2.1.** The three refusals shown exited 2 with the errors shown (argparse
  prefixes them with `python -m research.trendbot.run_research: error:`), and so did
  `--slippage-pct 0` and `--exchange-id 'bin_ance!'`; no out-dir was created.
  `data.load_dataset` refused a 1d file and a 1h file saved as `BTC_USDT-4h.csv`, naming
  lines 2-3 and both timestamps.
- **Section 2.2.** The live-bot block was extracted from this file and executed three ways.
  (a) Base variant, in a scratch directory holding a LIVE-valid real record built with the
  adoption test fixtures (`tests/test_adoption.py::build_evidence`: hash-bound data file,
  report and journals, an all-`Y` review pack, 14 days of testnet), `trades.csv` and the
  example calendar as `events.csv`: `require_stage` passed, the gatekeeper used exit offset
  0, and a BTC/USDT entry on the planted seed-1 candles was allowed at 1% risk. With a fill
  0.2% worse than expected, the actual-fill plan held 2.723514 units instead of the
  2.968770 ordered, still at 1.000000% risk; at that plan a clean stop is -1.000000000000R
  and a take-profit +2.000000000000R. (b) ML variant (`RECORD` and `MODEL` set to a
  LIVE-valid `base+ml` record and the committed planted model file): `from_json` loaded
  model `0986e8bd...`, `require_stage` passed with its fingerprint, and the filter allowed
  an entry at win probability 0.417 (break-even 0.333). After `"l2"` was edited in the model
  file, `from_json` raised `ValueError` (the edited model hashes to `fd0944b9...`). (c) With
  the committed synthetic `adoption_base.json`, `require_stage` raised `AdoptionBlocked`
  (9 reasons: `ADOPT_provenance`, `ADOPT_walk_forward`, `ADOPT_human_review`,
  `ADOPT_testnet`). All five committed model files load with `from_json` and the record's
  fingerprint as `expected_fingerprint`; none of their file sha256 equals the fingerprint.
- **Sections 2.3 and 2.4.** The three `journal_rules` runs printed what is shown. The
  example-calendar boundaries were checked with `NewsCalendar.check` at the times given.
- **Section 4.2.** The strength check used `backtester.run_backtest` on seeds 1-5 of each
  world. `invariants --synthetic planted --seed 1` audited 166 trades and `--gaps` 164, both
  with 0 violations. The cost-sensitivity run (scratch `--out-dir`) gave the numbers quoted
  in section 4.3.
- **Section 4.3, fetch.** `pip install ccxt` was NOT run (no network). Without ccxt,
  `fetch_data` exits 2 with the install hint. The two `fetch_data` commands were run as
  written against a fake `ccxt` module on `PYTHONPATH` that serves the planted seed-1
  candles (Binance as 4h; Coinbase as 2h halves only). Both exited 0, the Coinbase one
  printed "coinbase has no 4h candles; aggregating 2h", and every fetched file was
  byte-identical to the planted candles (the aggregated Coinbase files to the Binance
  ones). Adding BNB/USDT to the Coinbase command skipped it ("not listed on coinbase (BNB is
  a Binance pair)") and exited 1.
- **Section 4.3, research run and follow-ups**, with the planted world's 273 events written
  to `research/data/events.csv` (with the `known_from_utc` column). The Binance run
  reproduced sections 1, 3-8, 10 and 12 of the committed `synthetic_planted_s1` REPORT.md
  exactly (sections 2, 9 and 11 print file paths, provenance and commands); its records say
  `provenance: "real"` with 3 data-file hashes and the events file, so its ROBUST
  `rr3_vol2_rsi50-70` record passed `--stage HUMAN_REVIEW` (section 11, step 2). The
  Coinbase run (0.6%, BTC and ETH) gave the baseline TRAIN +0.274R (n=72) | TEST +0.083R
  (n=36), labelled all four candidates UNTESTED ("No robust result found.") and wrote
  `config_base.json`. `SPLIT_MS` printed 1678752000000 (the report's split). `invariants`
  found 0 violations in the Binance baseline TEST journal (51 trades) and, with `--config
  config_base.json`, in the Coinbase one (36 trades); without `--config` the Coinbase
  journal gave 108 violations (it was traded at 0.6%, not the default fee). `review_sheet`
  flagged 0 of 36 trades. `journal_rules --expectancy-guard` printed the guard's adoption
  note and "No active adaptations." The three `adoption check` commands were each BLOCKED
  by `ADOPT_walk_forward` only (UNTESTED).
- **Section 4.3, offline commands** on the committed outputs printed what is shown. Each of
  the 20 committed records passes `--stage WALK_FORWARD` (with its `--config` /
  `--model-fingerprint`) and is BLOCKED at HUMAN_REVIEW by `ADOPT_provenance` (plus
  `ADOPT_walk_forward` for the 19 that are not ROBUST).
- **Section 4.4.** The header and `map.json` were extracted from this file and three
  made-up rows written (a take-profit, a clean stop-loss, a manual close). `journal convert`
  exited 0; the converted TP is +2.000000000000R and the SL -1.000000000000R;
  `journal_rules` printed "No active adaptations."; `cross_trade_violations` with offset 0
  found nothing, while `invariants --journal` reported per-candle mismatches on it, as
  expected for a journal that is not the backtester's.
- **Section 4.5.** On the exported planted seed-1 candles and events: the `rr3_vol2_rsi50-70`
  TEST journal gave 0 violations with its `--config` and 16 without it (one per take-profit,
  "made 3.000000000000R, not exactly +2R"); the baseline TEST (51 trades, `--start-ts`) and
  TRAIN (115 trades, `--end-ts`) journals gave 0; `--config` with `{"reward_risk": 1.5}` exited
  2 ("reward_risk must be >= 2.0:1").
- **Section 7.** The first half of the snippet reproduced the committed hour_edge seed-1
  model (249 candidates, 81 wins, fingerprint `b38d6af2...`). The scikit-learn half was
  NOT run: there is no scikit-learn here.
- **Section 8.** The quotes were checked against `docs/freqai-parameter-table.md` lines 48,
  49 and 59 and `docs/freqai-running.md` lines 141-142.
- **Section 3.** The 172 distinct test functions named in the map each appear in
  `pytest --collect-only`. Selected together with `pytest -k` (and separately by node id)
  they ran as 388 cases, and all passed.
- **Full suite and lint:** `python -m pytest research/trendbot/tests -q` ran 1,158 tests in
  115 s: 1,157 passed and 1 FAILED,
  `test_adoption.py::test_forgery_with_hashes_and_one_trade_windows_is_still_blocked`. That
  test asserts that `metrics.label` with `min_train = min_test = 1` calls 1 + 1 trades
  ROBUST (so that it can show the adoption check refusing it), but the current C4 rule
  already returns UNTESTED below 30 trades per window whatever `min_*` say. The label rule is
  the stricter side of that disagreement; the test is not cited in section 3. `ruff check
  research/` ("All checks passed!") and `ruff format --check research/` ("52 files already
  formatted") passed.

## 10. Where the outputs live

- `research/results/synthetic_<world>_s1/REPORT.md`: the 12-section report. It starts with
  the win-rate statement, then data provenance, costs and the C4 statistics used, TRAIN |
  TEST tables with both drawdowns, discovery, entry layers with veto counts, verdict,
  rule-denial counts, invariant audit, journal rules, review packs and adoption stage.
- `research/results/<run>/journals/*.csv`: a TRAIN and a TEST journal for each of the four
  pre-registered candidates.
- `research/results/<run>/review_*/trades_review.{md,csv}`: the human review packs (blank
  `reviewer_ok` / `reviewer_note` columns).
- `research/results/<run>/adoption_<variant>.json`: the evidence-bound adoption records. The
  ML one carries the model fingerprint, and `model_base_plus_ml.json` is the model it pins.
- `research/results/<run>/config_<variant>.json`: the `StrategyConfig` overrides of a
  non-default config (a discovery variant, `base+guard`, other costs), for
  `adoption check --config` and `invariants --config`.
- `research/results/<run>/run.log`: the exact command and the console summary.
- `research/results/calibration_<world>/`: `CALIBRATION.md`, `calibration_runs.csv`,
  `run.log`.
- `research/results/power_planted/`: `POWER.md` (power curve and MDE), `power_runs.csv`,
  `run.log`.
- `research/results/RESULTS_SUMMARY.md`: cross-run summary and the honest list of
  weaknesses.

## 11. Adoption path (always)

`adoption.py` enforces this order, with no shortcuts. A good backtest number never skips a
step:

```text
BACKTEST -> WALK_FORWARD -> HUMAN_REVIEW -> TESTNET (>= 14 days) -> LIVE
```

`adoption check --record R --stage S` (and `require_stage` in the bot) verifies that every
stage BEFORE `S` is complete and consistent with its evidence files, and prints every
blocking reason with its rule id. Relative paths in a record are resolved against the
record's directory (the CLI) or `base_dir` (`require_stage`; `None` means the working
directory), and a "hash-bound" file must exist and have exactly its recorded sha256
(`python -m research.trendbot.adoption hash FILE` prints it). What each check verifies:

**On every check** (`ADOPT_record`, `ADOPT_fingerprint`): the record names its variant; the
config being promoted (the defaults, or `--config`) has exactly the recorded config
fingerprint (sha256 of the canonical config JSON); for an ML variant the supplied model
fingerprint equals the recorded one, a `+ml` variant must have one, and none may be supplied
for a record without one.

1. **Backtest** (checked when promoting to WALK_FORWARD or later; `ADOPT_backtest`,
   `ADOPT_provenance`): `REPORT.md` is hash-bound by `backtest.report_sha256` (the record is
   written after the report, so the hash matches); `completed_utc` is a valid time, not in
   the future; every `data_files` entry (`{path: sha256}`) and the `events_file`, if
   recorded, are hash-bound.
2. **Walk-forward, recomputed from the journals** (checked when promoting to HUMAN_REVIEW
   or later; `ADOPT_provenance`, `ADOPT_walk_forward`):
   - **synthetic provenance is blocked**: `provenance` must be exactly `"real"` with at least
     one data-file hash; `synthetic:<world>:<seed>` or a missing value blocks;
   - the TRAIN and TEST journals are hash-bound by sha256 and re-parsed (closed trades only,
     unique ids, every trade of the record's variant, TRAIN signals before `split_utc`, TEST
     signals at or after it);
   - `metrics.summarize`, `metrics.label` and `metrics.dd_check` are recomputed from those
     journals with the recorded `min_train` / `min_test` and `label_params` (m, alpha,
     n_boot, seed, `max_dd_pct` at most 20, and the C5 inputs `train_dd_p95_pct` and
     `mtm_max_dd_pct`); the typed label, `train_n`, `test_n`, `train_avg_r`, `test_avg_r`
     (to 1e-9) and `dd_ok` must equal the recomputed values;
   - the label must be exactly `ROBUST` with `train_n` and `test_n` >= 30, `test_avg_r > 0`
     and `dd_ok` true.

   `run_research` writes all these fields for every candidate (`base`, the selected
   variant, `base+ml`, `base+guard`). Not verifiable from the record: the MTM drawdown and
   the TRAIN p95 need the candles, so they are recorded as measured and re-applied, not
   recomputed; the hashes bind the journals to the data files, they do not prove the
   journals were produced from them (re-run `run_research` or `invariants` on the data);
   and `provenance: "real"` only means the run read candle files with `--data-dir`. The
   check cannot tell an exchange download from any other CSV: in this cycle the
   fake-exchange download of synthetic candles (section 9) was recorded as `"real"`, and its
   ROBUST variant passed `--stage HUMAN_REVIEW`. Use only files that `fetch_data` downloaded
   from the exchange, and let the reviewer confirm where they came from.
3. **Human review of the trade-by-trade list** (checked when promoting to TESTNET or later;
   `ADOPT_human_review`). A named reviewer opens the review pack
   (`review_<variant>/trades_review.csv` / `.md`: every TRAIN and TEST trade with machine
   flags and blank `reviewer_ok` / `reviewer_note` columns), enters `Y` or `N` in
   `reviewer_ok` on EVERY row, re-hashes the filled-in CSV with `adoption hash` into
   `human_review.review_sha256`, and fills `reviewer`, `date_utc`, `trades_reviewed`,
   `trades_total` and `approved`. The check verifies: the **review-pack hash**; the pack
   parses with `review_sheet.load_review`; its row count equals `train_n + test_n` and its
   `(window, trade_id)` keys equal the journals' trades, each row's pair and signal time
   matching its journal trade; **`reviewer_ok` is `Y` or `N` on every row** (a blank
   blocks); `trades_reviewed == trades_total == train_n + test_n`; `approved` is true; the
   review is dated no earlier than the backtest. **Any `N` blocks**, and the policy is that
   the variant is revised and restarts at BACKTEST under a new record. An unflagged trade is
   not an approved trade, and the pack proves only that every row got an explicit verdict.
4. **At least 2 weeks on the Binance spot testnet** (testnet.binance.vision; checked when
   promoting to LIVE; `ADOPT_testnet`): `exchange: "binance-testnet"`; `end_utc - start_utc
   >= 14 days`, starting no earlier than the review sign-off and not ending in the future;
   at least 1 trade and `rule_violations: 0`; and the testnet journal (`journal_path`, the
   bot's journal, converted with `journal convert` if needed) is re-audited:
   - its trade count, entry window and variant match the record, and no trade carries a
     mandatory-rule flag of `review_sheet.auto_flags` (R7 size/cap, 2:1 by price or net of
     costs, stop and target levels, look-ahead);
   - **cross-trade R6 and R9 invariants** are recomputed from its rows with exit offset 0
     (real fill times; `invariants.cross_trade_violations`): no pyramiding, BNB never
     stacks, no concurrent full-size cluster risk, the shared budget respected; no entry
     inside a 3-SL bench, every bench >= 24h, no entry during a 7-day loss halt;
   - **R5 only with a testnet events file**: if `testnet.events_path` and `events_sha256`
     are recorded (the news calendar the bot traded under, hash-bound), R5 is recomputed the
     same way; without them R5 is not journal-verifiable, and the check says so.

   With ccxt the testnet is selected like this (run in this cycle only against the fake
   ccxt module of section 9; there is no network here):

   ```python
   import os
   import ccxt

   exchange = ccxt.binance({"apiKey": os.environ["BINANCE_TESTNET_API_KEY"],
                            "secret": os.environ["BINANCE_TESTNET_SECRET"]})
   exchange.set_sandbox_mode(True)  # route requests to the Binance spot testnet
   ```

   Keep keys in the environment, never in code, configs or journals.
5. **Only then the live config** (`ADOPT_live`). Test-only configs (R4 off) never go LIVE,
   and `live.enabled_utc` may not precede the end of the testnet run. The live bot calls
   `adoption.require_stage(record, "LIVE", cfg, now_ms, base_dir)` at startup (plus
   `model_fingerprint=` for an ML variant, section 2.2) and refuses to trade if it raises.
   Not checkable from a record: that the testnet account really was a testnet, per-candle
   rules of testnet trades (R1-R4, R8, fill prices), and tick rounding.

## 12. Disclaimer

This is a research and testing tool, not financial advice and not a recommendation to trade.
Backtests and synthetic worlds are simplified models: past or simulated results do not
predict future results, and no result in this repository comes from real market data.
Crypto trading can result in the total loss of the capital used.
