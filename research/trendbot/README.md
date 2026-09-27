# trendbot: research, validation and adoption layer for the 4H trend bot

Research and testing tool only. Not financial advice. Crypto trading can result in the total
loss of the capital used. See [Disclaimer](#12-disclaimer).

The binding interface spec is [`CONTRACT.md`](CONTRACT.md): v1, the v2 amendments A1-A4, the
v3 amendments C1-C6 and the v4 amendments D1-D8 at its end. This README describes the code
and the committed results in [`research/results/`](../results/) as they are after v4 (gate
cycle 3). Every command shown was run in this cycle, and every number comes from a committed
artifact or from a script whose output is shown (section 9).

Contents: 1 win rate; 2 what this is (2.1 costs and 4H, 2.2 gatekeeper and the live bot
through `LiveSession`, 2.3 R9 timing, 2.4 R5 without look-ahead, 2.5 layers veto); 3
rule-to-test map; 4 how to run (4.1 tests, 4.2 synthetic worlds and the strength check, 4.3
real data, 4.4 journal convert, 4.5 invariants CLI, 4.6 review pack); 5 how to read results;
6 honest status (seed 1, stop-fill stress, calibration, power); 7 model complexity and tools;
8 freqtrade and FreqAI; 9 verification notes; 10 where the outputs live; 11 adoption path;
12 disclaimer.

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
through (the exit candle opens below the stop and fills at `open * (1 - s)`), or the opt-in
stop-fill stress of section 6 (`stop_fill_wick_k > 0`), can lose more than 1R. Because
costs are in `L_u`, the price ratio `(T - E) / (E - S)` is always above `reward_risk`.

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
costs, and on testnet that flag counts as a rule violation. In the 45 committed seed-1
journals (5 worlds x 4 pre-registered candidates x TRAIN/TEST, plus the ML layer's in-sample
TRAIN journal), all 1,003 take-profits are +`reward_risk` R (2, 2.5 or 3) within 3e-14 and all
1,741 stop-losses are -1R within 4e-16 (none gapped: the default synthetic candles open at
the previous close). The other 23 trades were force-closed at the end of a window.

## 2. What this is

- **A layer on top of the existing bot's rules. R1-R9 are unchanged.** `config.py` checks
  every mandate when a config is built and raises `ConfigError` on any loosening, so a
  discovery variant can only TIGHTEN a rule. An entry layer (`L_ml_filter`,
  `L_expectancy_guard`) can only VETO an entry that passed every mandatory rule, or (the
  guard) shrink its risk; it never approves an entry a rule denies. A veto can free R6
  budget or change R9 state, which can admit OTHER rule-compliant trades the base never
  took, so a layer's journal is not a subset of the base's (section 2.5). Two documented
  research-only settings are flagged `StrategyConfig.is_test_only` (CONTRACT.md v4 D1):
  `regime_filter=False` (R4 off, "when explicitly testing it off") and any
  `stop_fill_wick_k > 0` (the stop-fill stress of section 6). Discovery never selects them,
  they get no holdout-ledger line and no adoption record, and `adoption.py` blocks them from
  HUMAN_REVIEW on (`ADOPT_test_only`).
- **Python 3.11 standard library only.** The one exception is `ccxt`, imported lazily
  inside `fetch_data.main`. It is only needed to download real candles. scikit-learn is the
  intended tool for the ML layer on a networked machine, but it is not used here (section 7).
- **Deterministic.** All randomness goes through `random.Random(seed)` and the bootstrap
  seeds are fixed. Re-running a committed command with the same argv (same `--now`, same
  relative `--out-dir`) reproduces the committed files byte for byte
  ([`RESULTS_SUMMARY.md`](../results/RESULTS_SUMMARY.md) section 9 records the check).
- **Auditable.** Every denied signal candle is logged with its rule id
  (`config.RULE_IDS`) and a one-sentence reason. `invariants.check_invariants` re-derives
  every rule from the trade list and the candles. It does not reuse the gatekeeper's,
  breakers' or news calendar's code; only `compute_features` (and the list of scheduled
  news kinds) is shared.
- **Not included.** Order execution and exchange account handling. The live bot keeps
  those and calls `gatekeeper.LiveSession` (section 2.2) for every entry decision and fill.

| module | role |
|---|---|
| `models.py`, `config.py` | shared types; validated config (mandate floors and ceilings, 4H only, harness cost floors, `is_test_only`) |
| `indicators.py`, `signals.py`, `structure.py` | EMA / Wilder RSI / previous-20 volume mean; R1-R4 gates; R8 stop |
| `sizing.py`, `correlation.py`, `news.py` | R7 cost-aware sizing and target; R6 shared cluster budget; R5 calendar (scheduled vs unscheduled) |
| `circuit_breakers.py`, `journal.py`, `journal_rules.py` | R9; CSV trade journal and `journal convert` for the existing bot's CSV; "learn from past trades" as explicit rules |
| `gatekeeper.py` | **the single entry-decision path** (`Gatekeeper.evaluate` / `plan_fill`) and `LiveSession`, the live / testnet / replay adapter (D5) |
| `backtester.py`, `invariants.py` | event-driven backtest (optional stop-fill stress, D4); independent rule auditor for backtest and live journals (library and CLI) |
| `data.py`, `fetch_data.py`, `synthetic.py` | candle CSVs with 4H spacing and epoch-alignment checks, `verify_manifest` (D2); ccxt download writing `manifest.json`; synthetic worlds with known truth |
| `metrics.py`, `walkforward.py`, `strategy_discovery.py`, `ml_filter.py`, `ledger.py` | stats, C4 labels and the C5/D7 drawdown rule; 70/30 walk-forward, layer diffs and the D8 inner split; TRAIN-only selection; logistic veto layer with a model fingerprint; holdout ledger (D3) |
| `run_research.py`, `report*.py`, `review_sheet.py` | one-command pipeline, stress, calibration and power runs; Markdown rendering; human review pack with candle context |
| `adoption.py` (+ `adoption_record.py`, `adoption_evidence.py`, `adoption_stages.py`, `adoption_testnet.py`) | evidence-bound adoption gate: record schema, recomputation from the evidence files, per-stage checks, TESTNET replay |

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
silently traded as 4H candles. Since v4 D2, every candle must also be epoch-aligned
(`ts % 4h == 0`); a shifted file is refused as "not epoch-aligned", and `fetch_data` never
saves such a download.

`run_research` adds CLI ceilings that catch a percent typed where a fraction is expected:
`--fee-rate` at most 0.02 (so `0.6` is refused; `0.006` means 0.6%) and `--slippage-pct` at
most 5. `--exchange-id` must look like a ccxt id. A refused value exits 2 with the reason
(argparse prefixes it with `python -m research.trendbot.run_research: error:`) and creates
no out-dir:

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
the CLI ceilings and the `--timeframe` refusal, and `test_data.py` the spacing and
alignment checks (section 3).

### 2.2 The gatekeeper is the single entry-decision path; the live bot uses `LiveSession`

The backtester calls `Gatekeeper.evaluate` for every signal candle and `Gatekeeper.plan_fill`
for every fill (`test_backtester.py::test_every_signal_candle_goes_through_the_gatekeeper`).
Order inside `evaluate` (the first failure wins and names its rule):

```text
R1-R4 signals.check_entry -> R5 NewsCalendar.check -> R9 CircuitBreakers.can_enter
-> R6 CorrelationGuard.check -> R8 structure.find_stop -> L_ml_filter veto (if given)
-> L_expectancy_guard multiplier -> R7 risk = min(pair cap, R6 allowance) x guard
```

`plan_fill(pair, decision, market_price, fill_price, equity, free_cash)` then refuses a fill
at or below the stop (R8 "gapped through stop"), re-checks the stop distance against the
actual fill (R8), sizes the position with the A1 formula (`qty = equity * risk% / L_u`) and
the cost-aware target `T`, caps the notional plus entry fee by the free cash (the quantity
only shrinks, and `risk_amount = qty * L_u` is recomputed, so the risk only goes down), and
returns `X_capital` if less than `MIN_NOTIONAL` (10 quote units) can be bought.

**The live bot does not call those two methods by hand. It drives
`gatekeeper.LiveSession` (CONTRACT.md v4 D5)**, which owns the state a bot must keep between
candles (open positions, the R6 budget each one reserved, realized equity, the closed trades
for the guard, the R9 breakers rebuilt with `CircuitBreakers.from_journal`) and calls only
`Gatekeeper.evaluate` / `plan_fill`:

```text
LiveSession(cfg, events=(), journal_trades=(), starting_equity=None, *,
            exit_time_uncertainty_ms=0, entry_filter=None, variant="base", reserved_risk=None)

at every CLOSED 4H candle (decision time d = that candle's close):
  1. exits first, in time order: session.on_exit(trade_id, exit_ts, exit_price, reason, fees=None)
     (reason SL / TP / END; never refused for a rule: exits are never paused)
  2. then, pairs in SORTED order:
     dec = session.on_candle_close(pair, candles)     # candles[-1] = the candle that just closed
     if dec.allowed: market buy, then
         trade = session.on_fill(pair, dec, market_price, fill_price, qty=None, fill_ts=None)
         (None: the ACTUAL fill broke R8 or the capital limit, so flatten it)
       or session.on_fill_skipped(pair, dec, reason) if no order was filled
state: equity, open_positions, open_risk, free_cash(), closed, journal_trades(),
       reservations(), decision_log, breaker_log, equity_curve, last_fill_plan
```

A decision must be resolved (`on_fill` / `on_fill_skipped`) before the next
`on_candle_close`, so a position opened for one pair is visible to the pairs after it (R6),
exactly as in the backtest. `on_fill(..., qty=...)` accepts an actual filled quantity of at
most the R7 size (an exchange may round down, never up); if the exchange filled MORE than
`trade.qty`, sell the excess, which keeps R7 true at the actual fill. `fill_ts` must lie in
the fill candle `[d, d + 4h)`. Round quantities DOWN and take-profit prices UP to the
exchange tick, so that R7 and the 2:1 minimum still hold after rounding.

**Startup** (run in this cycle from `research/results/synthetic_planted_s1/`, with the
committed ML record, its model file and its TEST journal standing in for the bot's own
journal):

```python
import time
from pathlib import Path

from research.trendbot.adoption import AdoptionBlocked, load_config, load_record, require_stage
from research.trendbot.gatekeeper import LiveSession
from research.trendbot.journal import read_journal
from research.trendbot.ml_filter import MLFilter

RECORD, CONFIG, MODEL = "adoption_base_plus_ml.json", None, "model_base_plus_ml.json"
cfg = load_config(CONFIG)  # the tested overrides, validated: a loosening raises ConfigError
record = load_record(RECORD)
ml = MLFilter.from_json(Path(MODEL).read_text(encoding="utf-8"),
                        expected_fingerprint=record.model_fingerprint)  # ValueError if not it
try:
    require_stage(record, "LIVE", cfg, time.time_ns() // 1_000_000, base_dir=".",
                  model_fingerprint=ml.fingerprint())
except AdoptionBlocked as exc:
    print("refusing to trade:", sorted({d.rule for d in exc.decisions}))
journal = read_journal("journals/base_plus_ml_test.csv")  # the bot's own journal on restart
session = LiveSession(cfg, events=(), journal_trades=journal, starting_equity=10_000.0,
                      entry_filter=ml.entry_filter(), variant=record.variant)
print(len(session.closed), "closed trades rebuilt; equity", round(session.equity, 2),
      "; open", sorted(session.open_positions))
```

```text
refusing to trade: ['ADOPT_human_review', 'ADOPT_provenance', 'ADOPT_testnet', 'ADOPT_walk_forward']
34 closed trades rebuilt; equity 10799.34 ; open []
```

A real bot re-raises instead of printing: the committed record is synthetic, so it must
never trade. For a non-ML variant `MODEL = None` and `entry_filter=None`; for `base+guard`
`CONFIG = "config_base_plus_guard.json"` (the guard then runs inside `evaluate` from
`session.closed`). The model file is loaded, never refitted: `from_json` raises
`ValueError` if the model inside does not hash to the fingerprint stored in the file or to
the record's `model_fingerprint`. The file's own sha256 is NOT the fingerprint; the
fingerprint is the sha256 of the canonical model JSON inside it.

**The per-candle loop**, as a runnable replay: the planted seed-1 candles stand in for the
exchange's closed 4H candles, and the backtest's fill model (next open plus slippage, exit
on the first touch) stands in for the exchange. Run in this cycle from a scratch directory:

```python
from research.trendbot.adoption_evidence import write_decisions_log
from research.trendbot.backtester import exit_on_candle, run_backtest
from research.trendbot.config import StrategyConfig
from research.trendbot.gatekeeper import LiveSession
from research.trendbot.indicators import compute_features
from research.trendbot.journal import write_journal
from research.trendbot.models import EXIT_END
from research.trendbot.synthetic import make_world
from research.trendbot.walkforward import split_ts

cfg = StrategyConfig()
data, events = make_world("planted", 1)  # stands in for the exchange's closed 4H candles
split = split_ts(data, 0.7, cfg.timeframe_ms)
slip = cfg.slippage_pct / 100
rows = {p: compute_features(c, cfg) for p, c in data.items()}  # causal: prefix-exact

# Backtest-convention exit times (exit_ts = exit candle open), so the offset is the
# timeframe (A2). A live / testnet bot journals REAL fill times and keeps the default 0.
session = LiveSession(cfg, events, journal_trades=(), starting_equity=cfg.starting_capital,
                      exit_time_uncertainty_ms=cfg.timeframe_ms)
n = len(data["BTC/USDT"])
for k in range(n):
    if data["BTC/USDT"][k].ts < split:
        continue
    # 1) exits first (never gated): the "exchange" reports stop / take-profit fills
    for pair in sorted(session.open_positions):
        t, c = session.open_positions[pair], data[pair][k]
        hit = exit_on_candle(c, t.stop, t.target, slip)
        if hit is None and k == n - 1:
            hit = (c.close * (1 - slip), EXIT_END)  # window end: flatten at the close
        if hit is not None:
            session.on_exit(t.trade_id, c.ts, hit[0], hit[1])
    # 2) entries, pairs in sorted order, on the candle that just closed
    for pair in sorted(data):
        dec = session.on_candle_close(pair, data[pair][: k + 1], rows[pair][: k + 1])
        if not dec.allowed:
            continue  # dec.rule / dec.reason are already in session.decision_log
        if k + 1 >= n:
            session.on_fill_skipped(pair, dec, "no fill candle: the data ends here")
            continue
        market = data[pair][k + 1].open  # market buy at the next open ...
        trade = session.on_fill(pair, dec, market, market * (1 + slip))  # ... plus slippage
        # trade None: the ACTUAL fill broke R8 or the capital limit, so flatten it.
        # Otherwise place the stop at trade.stop and the take-profit at trade.target.

write_journal(session.journal_trades(), "trades.csv")
write_decisions_log(session.decision_log, "decisions.csv")
bt = run_backtest(data, cfg, events, start_ts=split)
key = lambda t: (t.pair, t.signal_ts, t.entry_ts, t.exit_ts, t.exit_reason, t.qty, t.pnl)
print(len(session.closed), "trades; identical to run_backtest:",
      [key(t) for t in session.closed] == [key(t) for t in bt.trades])
print("equity", round(session.equity, 2), "decisions logged", len(session.decision_log),
      "allowed", sum(d.allowed for d in session.decision_log))
```

```text
51 trades; identical to run_backtest: True
equity 12030.61 decisions logged 11826 allowed 51
```

Those 51 trades are the committed planted seed-1 baseline TEST journal (TEST +0.412R), trade
for trade, quantities and pnl exactly equal. `trades.csv` is the journal and
`decisions.csv` the D6 decisions log (`pair, signal_ts, signal_utc, allowed, rule, reason`,
one row per evaluated signal candle) that the TESTNET stage needs (section 11). The
whole-run parity test goes further: six years, candle by candle, with mid-run restarts from
the written journal (`test_gatekeeper.py::test_live_session_reproduces_six_years_of_backtest_across_restarts`).

### 2.3 R9 timing: an exit counts from when it is certain (CONTRACT.md v2 A2)

A backtest records `Trade.exit_ts` as the OPEN of the exit candle, but the fill happens
somewhere in `[exit_ts, exit_ts + 4h)`. R9 and the expectancy guard therefore use the
effective exit time `t_e = exit_ts + exit_time_uncertainty_ms`:

- the bench runs `[t_e, t_e + bench_hours)`;
- the 7-day loss window counts trades with `t_e` in `(ts - loss_window_days, ts]`.

The backtester and the gatekeeper pass `cfg.timeframe_ms` (4h), so in a backtest a bench
starts at the close of the exit candle and lasts at least 24h of real time after the fill.
A live bot journals real fill times and passes 0 (the `LiveSession` default).
`journal_rules` takes the same parameter, and its CLI flag `--backtest-journal` sets it to
the timeframe. `invariants --journal` recomputes the benches and the halt with the backtest
convention; `invariants --live-journal` and the adoption TESTNET check use offset 0.

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
journals need the flag. At `--now 2023-06-16T08:00:00Z --backtest-journal` it prints "No
active adaptations.", because the third loss is not yet certain at that time.

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

The `events.csv` header has an optional last column, and the old 5-column header is still
accepted (an empty cell means the kind default):

```text
time_utc,scope,impact,kind,note,known_from_utc
2024-02-13T13:30:00Z,ALL,high,macro,CPI-style inflation print,
2024-02-20T08:00:00+00:00,EXCHANGE:binance,high,regulatory,unscheduled headline,
2024-03-20T00:00:00Z,EXCHANGE:binance,medium,launchpool,announced 12h ahead,2024-03-19T12:00:00Z
```

[`events_example.csv`](events_example.csv) shows the format only (every row "EXAMPLE ONLY -
not a real calendar"); `test_news.py::test_example_calendar_semantics` pins its boundaries.
The synthetic worlds leave `known_from_ts` empty on every event, so the kind defaults apply:
their quarterly `regulatory` events block every pair only for `[ts, ts + 2h]`. `invariants`
recomputes R5 from the raw events with the same interval, and the adoption TESTNET check
applies it only when the testnet news calendar is recorded (section 11).

### 2.5 Layers veto, and what a veto can admit (CONTRACT.md v3 C1, v4 D8)

Two pre-registered layers run on the baseline rules:

- `base+ml` adds `L_ml_filter`: a logistic model fitted on purged TRAIN candidates only
  (section 7) vetoes a rule-passing signal whose predicted win probability is at or below
  the TRAIN break-even probability. **Its TRAIN gate is out of sample (D8):** the purged
  TRAIN candidates are split 70/30 in time order, an inner model is fitted on the first
  70% (purged at the inner boundary), and that inner filter is backtested on the last 30%
  of TRAIN. That window (only the last ~1.1-1.4 years of TRAIN) is the TRAIN the label judges
  and the TRAIN journal the adoption record binds. The in-sample full-TRAIN figure is
  context only, and the model applied to TEST is still fitted on ALL purged TRAIN candidates.
- `base+guard` is the baseline with `expectancy_guard=True` (`L_expectancy_guard`): a pair's
  risk is multiplied by 0.5 while its last 20 closed trades average below 0R. Nothing is
  fitted. It is a separate candidate with its own config (`config_base_plus_guard.json`),
  config fingerprint, journals, review pack and adoption record.

The C1 wording, which the reports, `models.EntryFilter` and `config.RULE_IDS["L_ml_filter"]`
follow: **an entry layer can only VETO an entry that passed every mandatory rule; it never
approves an entry a rule denies. Because a veto can free R6 budget or change R9 state, the
layer's trade list may contain other rule-compliant trades the base never took.** Every
report therefore counts, per window (TRAIN and TEST), matched by `(pair, signal_ts)`:
signals vetoed, entries at reduced risk, base trades absent from the layer's journal, and
layer trades absent from the base journal (`walkforward.layer_diff`, REPORT.md section 5).
Planted seed 1, TRAIN (in-sample model) | TEST: `base+ml` vetoed 60 | 28 signals, 44 | 20 base
trades are missing from its journal, and 23 | 3 of its trades are not in the base journal.

## 3. Rule-to-code-to-test map

Each test named here exists and passed in this cycle: every name was checked against
`pytest --collect-only`, and all of them were run together with `pytest -k` (section 9).
The file prefix is `research/trendbot/tests/`.

| rule | enforced by | proven by |
|---|---|---|
| **R1** trend: close > EMA9, close > EMA21, EMA9 > EMA21, on 4H candles only | `signals.check_entry` (trend gate) on `indicators.compute_features`; `StrategyConfig.validate` fixes EMA 9/21/200 and `timeframe_ms` = 4h; `run_research --timeframe` must be `4h`; `data.load_dataset` spacing and alignment checks | `test_signals.py::test_trend_gate_fails`<br>`test_signals.py::test_trend_detail_lists_every_broken_condition`<br>`test_signals.py::test_config_refuses_loosened_entry_gates`<br>`test_config.py::test_loosening_is_rejected`<br>`test_data.py::test_load_dataset_rejects_wrong_timeframe_and_empty_files`<br>`test_data.py::test_load_dataset_rejects_a_daily_file_saved_as_4h`<br>`test_data.py::test_minimum_spacing_must_equal_the_timeframe`<br>`test_data.py::test_a_shifted_4h_file_is_refused_as_not_epoch_aligned`<br>`test_run_research.py::test_cli_usage_errors` |
| **R2** RSI(14) in [50, 70] | `signals.check_entry` (momentum gate, both ends inclusive); `indicators.rsi_wilder`; `StrategyConfig.validate` (period 14, window inside [50, 70]) | `test_signals.py::test_momentum_bounds_inclusive`<br>`test_signals.py::test_momentum_fails_outside_window`<br>`test_indicators.py::test_rsi_matches_published_wilder_worksheet`<br>`test_config.py::test_loosening_is_rejected` |
| **R3** volume >= 1.5x previous-20 average | `signals.check_entry` (volume gate); `indicators.prev_mean` (current candle excluded); `StrategyConfig.validate` (multiple >= 1.5, lookback 20) | `test_signals.py::test_volume_exactly_at_multiple_passes`<br>`test_signals.py::test_volume_below_multiple_fails`<br>`test_indicators.py::test_prev_mean_excludes_current_value`<br>`test_config.py::test_loosening_is_rejected` |
| **R4** close > EMA200 unless explicitly tested off | `signals.check_entry` (regime gate); `StrategyConfig.is_test_only`; `strategy_discovery.select_on_train` never selects it; `adoption` blocks it from HUMAN_REVIEW on (`ADOPT_test_only`) | `test_signals.py::test_regime_close_not_above_ema200_fails`<br>`test_signals.py::test_regime_disabled_is_an_explicit_pass`<br>`test_config.py::test_regime_off_is_flagged_test_only`<br>`test_strategy_discovery.py::test_select_on_train_rules`<br>`test_strategy_discovery.py::test_regime_off_is_the_one_test_only_variant`<br>`test_adoption.py::test_test_only_config_never_goes_past_walk_forward` |
| **R5** no entries +/-2h of high-impact news; BNB +/-24h around burns / launchpools; scheduled vs unscheduled without look-ahead (C2) | `news.NewsCalendar.check` (block interval `[max(ts - w, known_from), ts + w]`, both ends inclusive; `known_from_utc` column), step 2 of `Gatekeeper.evaluate`; `StrategyConfig.validate` (windows may widen, never narrow); `invariants` recomputes it from the raw events (backtest and live journals) | `test_news.py::test_high_impact_all_boundaries_are_inclusive`<br>`test_news.py::test_bnb_events_block_bnb_for_24h_any_impact`<br>`test_news.py::test_bnb_burn_does_not_block_btc_or_eth`<br>`test_news.py::test_exchange_scope_matches_configured_exchange_only`<br>`test_news.py::test_unscheduled_headline_after_the_decision_does_not_block`<br>`test_news.py::test_unscheduled_headline_before_the_decision_blocks_its_tail`<br>`test_news.py::test_scheduled_high_impact_still_blocks_before_the_print`<br>`test_news.py::test_explicit_known_from_narrows_a_scheduled_pre_window`<br>`test_news.py::test_late_known_event_never_blocks`<br>`test_news.py::test_example_calendar_semantics`<br>`test_news.py::test_check_matches_a_brute_force_oracle`<br>`test_news.py::test_loader_reads_the_known_from_column`<br>`test_news.py::test_loader_header_without_known_from_is_still_accepted`<br>`test_synthetic.py::test_synthetic_events_leave_known_from_to_the_kind_default`<br>`test_config.py::test_loosening_is_rejected`<br>`test_backtester.py::test_news_blackout_blocks_entries_end_to_end`<br>`test_invariants.py::test_disabled_news_rule_is_caught`<br>`test_invariants.py::test_unscheduled_news_blocks_only_after_it_happened_end_to_end`<br>`test_invariants.py::test_r5_recomputation_agrees_with_the_news_calendar`<br>`test_adoption.py::test_testnet_r5_is_checked_when_the_calendar_is_recorded` |
| **R6** BTC/ETH/BNB share one risk budget, never full size on more than one, BNB never stacks | `correlation.CorrelationGuard.check` (no pyramiding, BNB exclusive, `allowed = min(requested, remaining)`); `StrategyConfig.validate` (cluster budget <= largest single-pair cap, all three in the cluster, BNB exclusive); `LiveSession.open_risk` (the reserved budget); `invariants.cross_trade_violations` (backtest, live and testnet journals) | `test_correlation.py::test_btc_open_at_full_size_denies_eth`<br>`test_correlation.py::test_bnb_open_denies_btc_and_eth`<br>`test_correlation.py::test_btc_or_eth_open_denies_bnb_even_with_budget_left`<br>`test_correlation.py::test_two_full_size_positions_are_impossible_by_config`<br>`test_config.py::test_loosening_is_rejected`<br>`test_backtester.py::test_bnb_never_stacks_with_another_cluster_position`<br>`test_invariants.py::test_disabled_correlation_cap_is_caught`<br>`test_invariants.py::test_live_r6_overlaps_and_budget`<br>`test_adoption.py::test_testnet_r6_r9_violations_block_live` |
| **R7** risk <= 1% BTC/ETH, <= 0.5% BNB, ALL-IN (fees + slippage); size derived from the stop distance | `sizing.size_for_pair` / `size_position` (`qty = equity * risk% / L_u`; notional and free-cash caps only shrink), called by `Gatekeeper.plan_fill` on the actual fill; `LiveSession.on_fill(qty=...)` accepts at most the R7 size; `StrategyConfig.validate` (caps) | `test_sizing.py::test_basic_size_is_cost_aware_hand_arithmetic`<br>`test_sizing.py::test_stop_fill_with_both_fees_loses_exactly_risk_pct`<br>`test_sizing.py::test_wider_stop_means_smaller_position_same_all_in_risk`<br>`test_sizing.py::test_size_for_pair_enforces_the_pair_cap`<br>`test_config.py::test_pair_risk_caps`<br>`test_gatekeeper.py::test_plan_fill_applies_the_a1_cost_aware_arithmetic_by_hand`<br>`test_gatekeeper.py::test_live_session_accepts_actual_fills_and_exits`<br>`test_backtester.py::test_risk_caps_and_size_derived_from_the_stop`<br>`test_invariants.py::test_price_only_sizing_is_caught` |
| **R8** stop behind the swing low with a buffer (BNB 0.5-0.8%) | `structure.find_stop` / `latest_confirmed_pivot` (a pivot is usable only k candles after it; fallback = lowest low of the last N); `Gatekeeper.plan_fill` (gap-through refusal, distance re-check from the fill); `StrategyConfig.validate` (BNB buffer range, buffer > 0, stop-distance bounds); `review_sheet` re-derives the structure candle (`stop_mismatch`) | `test_structure.py::test_confirmed_pivot_with_btc_buffer`<br>`test_structure.py::test_unconfirmed_pivot_is_not_used`<br>`test_structure.py::test_find_stop_never_reads_candles_after_i`<br>`test_structure.py::test_config_keeps_the_bnb_buffer_in_the_mandated_range`<br>`test_config.py::test_bnb_buffer_range`<br>`test_backtester.py::test_entry_that_gaps_through_the_stop_is_skipped`<br>`test_backtester.py::test_gaps_world_exercises_gap_through_exits_and_the_entry_skip`<br>`test_invariants.py::test_stop_not_behind_structure_is_caught` |
| **R9** 3 consecutive SLs bench a pair 24h; 7-day realized loss limit halts all entries; exits never paused | `circuit_breakers.CircuitBreakers` (`on_trade_closed`, `can_enter`, `from_journal`; deliberately no exit API); step 3 of `Gatekeeper.evaluate`; `LiveSession.on_exit` is never refused; `StrategyConfig.validate` (limit <= 3, bench >= 24h, window >= 7 days, loss limit in (0, 10]%); `invariants.cross_trade_violations` | `test_circuit_breakers.py::test_three_consecutive_stop_losses_bench_the_pair_for_24h`<br>`test_circuit_breakers.py::test_weekly_loss_halts_all_pairs_and_recovers_when_losses_age_out`<br>`test_circuit_breakers.py::test_there_is_no_exit_related_api`<br>`test_config.py::test_loosening_is_rejected`<br>`test_backtester.py::test_exits_are_processed_while_the_halt_is_active`<br>`test_invariants.py::test_paused_exits_are_caught`<br>`test_invariants.py::test_live_bench_uses_real_exit_times`<br>`test_invariants.py::test_live_weekly_halt_and_starting_equity`<br>`test_invariants.py::test_live_mode_bench_runs_24h_from_the_real_fill` |
| **R9 timing** (A2): exits count from `exit_ts + 4h` in backtests, real fill time live | `CircuitBreakers(cfg, exit_time_uncertainty_ms)`; `Gatekeeper` (one shared offset); `LiveSession(exit_time_uncertainty_ms=0)`; `journal_rules --backtest-journal`; `invariants` bench-duration check | `test_circuit_breakers.py::test_backtest_convention_bench_lasts_24h_of_real_time_from_the_candle_close`<br>`test_circuit_breakers.py::test_backtest_convention_loss_window_counts_from_the_candle_close`<br>`test_gatekeeper.py::test_breakers_and_guard_share_one_exit_offset`<br>`test_gatekeeper.py::test_live_time_convention_takes_the_same_decisions`<br>`test_backtester.py::test_bench_lasts_24h_from_the_exit_candle_close`<br>`test_journal_rules.py::test_cli_backtest_journal_flag`<br>`test_invariants.py::test_bench_counted_from_the_exit_candle_open_is_caught` |
| **Minimum RR 2:1, net of costs** (A1) | `StrategyConfig.validate` (`reward_risk >= 2.0`); `sizing.cost_aware_target` via `Gatekeeper.plan_fill` (target from the actual fill); discovery grid {2, 2.5, 3}; `review_sheet.auto_flags` `rr_below_min` (price or net); `adoption.RULE_VIOLATION_FLAGS` | `test_sizing.py::test_cost_aware_target_nets_exactly_reward_risk`<br>`test_backtester.py::test_every_trade_has_rr_at_least_2_and_stop_below_structure`<br>`test_backtester.py::test_outcomes_in_r_are_exact_on_a_synthetic_run`<br>`test_invariants.py::test_price_only_target_is_caught`<br>`test_review_sheet.py::test_rr_is_checked_by_price_and_net_of_costs`<br>`test_review_sheet.py::test_cli_rejects_a_loosened_reward_risk`<br>`test_strategy_discovery.py::test_default_grid_is_twelve_legal_tightenings`<br>`test_config.py::test_loosening_is_rejected` |
| **Cost floors and bounds** (A4); costs reach every trade and every report | `StrategyConfig._harness_errors`; `run_research.config_from_args` (CLI ceilings); `report` cost lines (report section 2, Coinbase warning) | `test_config.py::test_loosening_is_rejected`<br>`test_config.py::test_tightening_is_allowed`<br>`test_sizing.py::test_zero_cost_configs_are_rejected_so_sizing_always_carries_costs`<br>`test_run_research.py::test_cli_usage_errors`<br>`test_run_research.py::test_cost_flags_default_to_the_config_defaults`<br>`test_run_research.py::test_provenance_prints_default_costs`<br>`test_run_research.py::test_cost_lines_warn_when_a_non_binance_run_keeps_the_binance_fee`<br>`test_run_research.py::test_data_dir_without_events_flags_r5_and_costs_reach_every_trade` |
| **Layers veto** (C1): a veto only removes a rule-compliant entry; the trades it admits are counted both ways | `models.EntryFilter`, `config.RULE_IDS["L_ml_filter"]`; step 6 of `Gatekeeper.evaluate`; `walkforward.layer_diff` (by `(pair, signal_ts)`, per window); REPORT.md section 5 | `test_gatekeeper.py::test_entry_filter_can_only_veto_and_reports_its_probability`<br>`test_ml_filter.py::test_veto_wording_follows_contract_c1`<br>`test_walkforward.py::test_layer_diff_counts_both_directions`<br>`test_run_research.py::test_layer_section_uses_the_c1_wording`<br>`test_run_research.py::test_layer_counts_are_recomputed_from_the_written_journals` |
| **Layer `L_ml_filter`**: fitted on purged TRAIN candidates only; TRAIN gate out of sample (D8) | `ml_filter.MLFilter` (6-feature L2 logistic, break-even threshold); `walkforward.run_train` (inner chronological split, purged; final model on all TRAIN, applied unchanged to TEST) | `test_walkforward.py::test_layer_is_fit_on_purged_train_candidates_only`<br>`test_walkforward.py::test_ml_fit_is_invariant_to_test_period_candles`<br>`test_walkforward.py::test_insufficient_data_is_untested_without_fallback`<br>`test_walkforward.py::test_inner_split_is_chronological_by_count_and_purged`<br>`test_walkforward.py::test_gate_inner_fit_failure_is_untested_not_a_fallback`<br>`test_ml_filter.py::test_filter_learns_hour_edge_and_improves_test_expectancy` |
| **ML model fingerprint and live loader** (A3): the model promoted is the model tested | `MLFilter.fingerprint` (sha256 of the canonical model JSON); `MLFilter.to_json` / `from_json(text, expected_fingerprint)`; `AdoptionRecord.model_fingerprint`; `adoption.check_promotion(..., model_fingerprint)` (`ADOPT_fingerprint`) | `test_ml_filter.py::test_fingerprint_is_a_deterministic_sha256_of_the_canonical_model`<br>`test_ml_filter.py::test_fingerprint_changes_whenever_the_model_changes`<br>`test_ml_filter.py::test_to_json_from_json_round_trip_is_exact`<br>`test_ml_filter.py::test_from_json_refuses_a_model_that_is_not_the_fingerprinted_one`<br>`test_adoption.py::test_model_fingerprint_mismatch_blocks_every_stage`<br>`test_adoption.py::test_recorded_model_but_none_supplied_blocks_every_stage`<br>`test_adoption.py::test_ml_variant_without_recorded_model_fingerprint_blocks`<br>`test_run_research.py::test_ml_adoption_record_pins_the_fitted_model` |
| **Layer `L_expectancy_guard`** and the `base+guard` candidate: off by default; only shrinks risk | `journal_rules.risk_multiplier`; `Gatekeeper.guard_multiplier` (only trades whose exit is certain at the decision); `run_research` pre-registers `base+guard` with its own config and record | `test_journal_rules.py::test_guard_is_off_by_default`<br>`test_journal_rules.py::test_guard_triggers_on_negative_last_window_expectancy`<br>`test_journal_rules.py::test_guard_backtest_journal_uses_only_certain_exits`<br>`test_journal_rules.py::test_cli_expectancy_guard_prints_the_adoption_note`<br>`test_gatekeeper.py::test_risk_is_min_of_cap_and_budget_times_guard`<br>`test_run_research.py::test_guard_is_a_preregistered_candidate_with_its_own_record` |
| **Single entry path, live adapter and parity** (D5) | `Gatekeeper.evaluate` + `plan_fill`; `LiveSession` (`on_candle_close`, `on_fill`, `on_fill_skipped`, `on_exit`; restart via `CircuitBreakers.from_journal`) | `test_backtester.py::test_every_signal_candle_goes_through_the_gatekeeper`<br>`test_gatekeeper.py::test_rules_are_applied_in_the_documented_order`<br>`test_gatekeeper.py::test_journal_restart_reproduces_the_backtest_decision`<br>`test_gatekeeper.py::test_live_session_reproduces_six_years_of_backtest_across_restarts`<br>`test_gatekeeper.py::test_live_session_takes_the_decision_on_the_candle_that_just_closed`<br>`test_gatekeeper.py::test_live_session_protocol_is_enforced`<br>`test_circuit_breakers.py::test_from_journal_reproduces_live_state` |
| No look-ahead | causal indicators; `find_stop` reads `candles[0..i]` only; the backtester cuts its input at `end_ts`; unscheduled news blocks only from its own time (C2) | `test_gatekeeper.py::test_decisions_never_depend_on_later_candles`<br>`test_backtester.py::test_perturbing_the_future_never_changes_the_past`<br>`test_backtester.py::test_end_ts_is_identical_to_truncated_data`<br>`test_indicators.py::test_indicators_never_change_when_future_values_are_removed` |
| Selection on TRAIN only | `strategy_discovery.discover` (selection frozen before any TEST backtest; only the selected variant is judged) | `test_strategy_discovery.py::test_selection_is_recorded_before_any_test_backtest`<br>`test_strategy_discovery.py::test_selection_is_invariant_to_test_period_candles`<br>`test_strategy_discovery.py::test_only_the_selected_variant_is_judged` |
| **Label rule** (C4): m = 4, one-sided 1 - 0.05/m, iid AND calendar-month block bootstrap, the more conservative bound | `metrics.summarize` / `metrics.label` (`M_CANDIDATES`, `ALPHA`, `N_BOOT` = 4000, `ROBUST_MIN_N` = 30); `walkforward` stats; `run_research.verdict_line` | `test_metrics.py::test_label_branches`<br>`test_metrics.py::test_robust_needs_30_trades_in_each_window_whatever_min_says`<br>`test_metrics.py::test_label_multiplicity_is_fixed_by_m_and_alpha`<br>`test_metrics.py::test_iid_bounds_follow_the_documented_algorithm`<br>`test_metrics.py::test_month_blocks_group_by_the_utc_month_of_the_exit`<br>`test_metrics.py::test_iid_passes_but_calendar_month_block_bootstrap_fails`<br>`test_walkforward.py::test_stats_fix_m_alpha_and_resamples`<br>`test_walkforward.py::test_label_params_reproduce_the_verdict_from_the_journals`<br>`test_run_research.py::test_null_world_reports_no_robust_result` |
| **Drawdown** (C5, D7): realised and mark-to-market, `dd_ok` rule, 15% cap | `metrics.mtm_equity_curve` / `mtm_max_dd_pct`, `metrics.train_dd_quantile`, `metrics.dd_limit` / `dd_check`, `metrics.DD_CAP_PCT = 15.0` | `test_metrics.py::test_dd_check`<br>`test_metrics.py::test_dd_cap_rationale_is_stated`<br>`test_metrics.py::test_train_dd_quantile_follows_the_documented_algorithm`<br>`test_metrics.py::test_mark_to_market_curve_values_open_positions_at_each_close`<br>`test_metrics.py::test_mark_to_market_realises_exits_and_carries_the_last_close`<br>`test_run_research.py::test_every_result_reports_both_drawdowns_and_the_rule` |
| **Stop-fill stress** (D4): `stop - k*(stop - low)`, then slippage; test-only; k = 0 unchanged | `backtester.exit_on_candle(..., wick_k)`; `StrategyConfig.stop_fill_wick_k`, `is_test_only`; `run_research --stop-fill-wick-k` (with the k = 0 reference); `invariants` accepts r < -1 on non-gap stops only when k > 0 | `test_backtester.py::test_wick_parameter_of_the_exit_primitive`<br>`test_backtester.py::test_stop_fill_stress_moves_only_the_non_gap_stop_fill`<br>`test_backtester.py::test_default_run_is_byte_identical_to_the_pinned_digest`<br>`test_config.py::test_stop_fill_stress_configs_are_test_only`<br>`test_strategy_discovery.py::test_a_stop_fill_stress_base_stresses_the_whole_grid`<br>`test_invariants.py::test_stop_fill_stress_run_is_clean_only_under_its_own_config`<br>`test_invariants.py::test_stressed_stop_reported_at_the_touch_price_is_caught`<br>`test_run_research.py::test_stop_fill_stress_run_is_test_only_with_the_k0_reference`<br>`test_run_research.py::test_stress_flag_reaches_the_config` |
| **Calibration and power** (C6): zero_edge world, effect strength, opt-in gaps, Wilson intervals, MDE | `synthetic.make_world(..., effect_strength, gaps)`; `run_research --calibrate-seeds`, `--seed-offset`, `--power-strengths` | `test_synthetic.py::test_zero_edge_world_is_the_planted_mechanism_at_the_calibrated_strength`<br>`test_synthetic.py::test_effect_strength_overrides_the_planted_drift`<br>`test_synthetic.py::test_gaps_are_opt_in_rare_sized_and_otherwise_share_the_noise`<br>`test_synthetic.py::test_default_worlds_are_byte_identical_to_the_committed_ones`<br>`test_run_research.py::test_wilson_interval`<br>`test_run_research.py::test_minimum_detectable_effect_interpolates_between_strengths`<br>`test_run_research.py::test_power_curve_cli_writes_power_md`<br>`test_run_research.py::test_calibration_row_describes_every_candidate` |
| **Pinned label rule in the gate** (D1) | `adoption_evidence` closed `label_params` schema; `m == M_CANDIDATES`, `alpha == ALPHA`, `n_boot >= N_BOOT`, `seed == SUMMARY_SEED`; C5 inputs recomputed (TRAIN p95 from the TRAIN journal; MTM DD from the hash-bound candles) | `test_adoption.py::test_label_params_are_a_pinned_closed_schema`<br>`test_adoption.py::test_label_params_are_passed_by_name_not_by_signature`<br>`test_adoption.py::test_typed_label_differs_from_recomputed_blocks`<br>`test_adoption.py::test_typed_numbers_must_match_the_recomputed_ones`<br>`test_adoption.py::test_forgery_with_hashes_and_one_trade_windows_is_still_blocked`<br>`test_adoption.py::test_cycle1_forged_record_is_blocked` |
| **Manifest-backed provenance** (D2) | `fetch_data.write_manifest`; `data.verify_manifest` (`real` only if every file is listed with a matching sha256); `run_research --data-dir` records it; `adoption` (`ADOPT_provenance`) | `test_fetch_data.py::test_main_writes_a_manifest_that_verifies_as_real`<br>`test_fetch_data.py::test_misaligned_exchange_candles_are_never_saved`<br>`test_data.py::test_verify_manifest_accepts_only_hash_matching_listed_files`<br>`test_data.py::test_verify_manifest_otherwise_says_unverified`<br>`test_run_research.py::test_manifest_verified_data_dir_is_real`<br>`test_adoption.py::test_manifest_must_verify_every_data_file`<br>`test_adoption.py::test_manifest_hash_mismatch_and_foreign_directory_block`<br>`test_adoption.py::test_real_provenance_without_a_manifest_is_an_unverified_csv`<br>`test_adoption.py::test_synthetic_or_missing_provenance_blocks_after_walk_forward`<br>`test_adoption.py::test_real_provenance_needs_data_file_hashes` |
| **Holdout ledger** (D3): at most m distinct looks per TEST window | `ledger.py` (append-only JSON lines); `run_research --ledger`; `adoption` (`ADOPT_holdout`) | `test_ledger.py::test_line_format_has_exactly_the_d3_fields`<br>`test_ledger.py::test_default_paths_follow_d3`<br>`test_ledger.py::test_distinct_looks_count_identities_on_overlapping_windows`<br>`test_ledger.py::test_append_only_and_missing_file_is_empty`<br>`test_ledger.py::test_record_looks_reports_the_cumulative_counts`<br>`test_run_research.py::test_synthetic_run_appends_one_ledger_line_per_adoptable_look`<br>`test_run_research.py::test_records_carry_the_ledger_path`<br>`test_adoption.py::test_holdout_ledger_counts_distinct_looks_per_pair`<br>`test_adoption.py::test_record_without_its_own_ledger_look_is_a_context_variant`<br>`test_adoption.py::test_missing_ledger_path_blocks`<br>`test_adoption.py::test_gate_parses_the_ledger_run_research_writes` |
| **Evidence-bound adoption** (C3) | `adoption.check_promotion` / `require_stage` (hashes, journal recomputation, provenance, review pack); `run_research` writes the fields | `test_adoption.py::test_skipping_any_stage_blocks_every_later_stage`<br>`test_adoption.py::test_fully_valid_real_record_with_an_all_y_pack_passes_every_stage`<br>`test_adoption.py::test_fingerprint_mismatch_blocks_every_stage`<br>`test_adoption.py::test_report_hash_mismatch_blocks`<br>`test_adoption.py::test_journal_hash_mismatch_blocks`<br>`test_adoption.py::test_review_pack_hash_mismatch_blocks`<br>`test_adoption.py::test_blank_reviewer_ok_row_blocks`<br>`test_adoption.py::test_one_rejected_row_blocks_and_states_the_policy`<br>`test_adoption.py::test_review_pack_must_describe_the_journal_trades`<br>`test_adoption.py::test_test_only_config_never_goes_past_walk_forward`<br>`test_run_research.py::test_adoption_records_are_bound_to_their_evidence`<br>`test_run_research.py::test_adoption_check_on_written_synthetic_records` |
| **TESTNET evidence** (D6): >= 14 days, decisions log, `LiveSession` replay, live-journal audit | `adoption_testnet.testnet_problems`; `adoption_evidence.replay_testnet` / `read_decisions_log`; `invariants.live_journal_violations` | `test_adoption.py::test_testnet_of_exactly_14_days_passes_to_live`<br>`test_adoption.py::test_consistent_testnet_window_passes_and_replays_exactly`<br>`test_adoption.py::test_fixture_testnet_window_is_a_real_liveSession_run`<br>`test_adoption.py::test_decisions_log_mismatch_blocks`<br>`test_adoption.py::test_fill_refusal_is_injected_from_the_decisions_log`<br>`test_adoption.py::test_ml_vetoes_are_taken_from_the_decisions_log_only_for_an_ml_record`<br>`test_adoption.py::test_unreadable_testnet_journal_or_decisions_log_blocks`<br>`test_adoption.py::test_testnet_journal_is_cross_checked`<br>`test_adoption.py::test_testnet_r6_r9_violations_block_live`<br>`test_adoption.py::test_live_enabled_before_testnet_ended_blocks` |
| **Review pack context** (MAE/MFE, structure candle, wick depth, context CSVs, `deep_wick_stop`) | `review_sheet.write_review_pack(..., data=...)`, CLI `--data-dir` / `--synthetic`; `load_review` re-checks every context CSV's sha256 | `test_review_sheet.py::test_mae_mfe_and_wick_by_hand`<br>`test_review_sheet.py::test_tp_trade_has_no_wick_cells_and_mfe_reaches_the_target`<br>`test_review_sheet.py::test_deep_wick_flag_fires_only_on_deep_non_gap_stop_outs`<br>`test_review_sheet.py::test_deep_wick_threshold_is_in_stop_distances`<br>`test_review_sheet.py::test_test_only_banner_names_the_reason` |
| **Importing the existing bot's journal** | `journal.import_external`, `python -m research.trendbot.journal convert` | `test_journal.py::test_import_external_maps_columns_and_derives_a1_risk_and_r`<br>`test_journal.py::test_import_external_epoch_time_formats`<br>`test_journal.py::test_cli_convert_writes_a_journal_that_round_trips`<br>`test_journal.py::test_cli_convert_errors`<br>`test_adoption.py::test_converted_bot_journal_feeds_the_testnet_audit` |
| Independent audit (library and CLI, backtest and live journals) | `invariants.check_invariants`, `cross_trade_violations`, `live_journal_violations`; `python -m research.trendbot.invariants` (`--journal`, `--live-journal`, `--synthetic`) | `test_invariants.py::test_full_synthetic_runs_are_clean`<br>`test_invariants.py::test_cross_trade_checks_are_clean_on_real_runs`<br>`test_invariants.py::test_cli_audits_a_journal_and_flags_a_tampered_one`<br>`test_invariants.py::test_cli_config_audits_a_discovery_variant_journal`<br>`test_invariants.py::test_cli_fee_rate_audits_a_high_fee_journal`<br>`test_invariants.py::test_cli_rejects_an_illegal_config`<br>`test_invariants.py::test_cli_runs_and_audits_a_synthetic_world`<br>`test_invariants.py::test_cli_live_journal_mode`<br>`test_invariants.py::test_live_mode_catches_each_violation`<br>`test_invariants.py::test_live_session_journal_is_clean_in_live_mode` |

## 4. How to run

Run everything from the repository root. Only the `ccxt` download needs a network.

### 4.1 Tests and lint

```bash
python -m pytest research/trendbot/tests -q      # needs pytest; research/pytest.ini is used
ruff check research/
ruff format --check research/
```

In the build sandbox (a venv with pytest and ruff, 4 CPUs) the full suite ran **1,337
tests, all passed, in 207 s** in this cycle, and both ruff commands passed (section 9).

### 4.2 Synthetic worlds, the strength check, stress, calibration and power (offline)

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
  slippage eat, so the baseline's expected NET R is about 0, the boundary of the null
  hypothesis).
- Synthetic news with no price impact, to exercise R5: 1-3 high-impact macro events (scope
  `ALL`) per month, one high-impact regulatory event (`EXCHANGE:binance`, unscheduled) per
  quarter, one BNB burn per quarter, one BNB launchpool per month, one ETH unlock per year.
- All worlds share the same noise for a given seed; only the planted drift differs.

**Strength check: what the baseline sees in each world, TRAIN | TEST.** Measured in this
cycle with the script below (`walkforward.walk_forward` with the default config, so the
70/30 split at 2023-03-14T00:00Z, a fresh TEST equity and breakers; seeds 1-5; n and avg R
pooled over the five seeds' trades). A hour_edge trade is "in the window" when its signal
candle closes 12:00-20:00 UTC (`synthetic.HOUR_EDGE_CLOSE_HOURS`):

| world (truth) | baseline TRAIN avg R (n) | baseline TEST avg R (n) |
|---|---|---|
| null (no edge) | -0.134 (480) | -0.008 (230) |
| zero_edge (pre-cost edge that costs eat) | +0.001 (502) | +0.048 (238) |
| decay (edge before the split only) | +0.382 (571) | **-0.029 (232), ~null** |
| planted (edge everywhere) | +0.385 (576) | +0.488 (240) |
| hour_edge, all trades | +0.146 (569) | +0.146 (264) |
| hour_edge, signal closes 12-20 UTC (in window) | +0.493 (262) | +0.570 (128) |
| hour_edge, other hours (out of window) | -0.150 (307) | -0.254 (136) |

Decay's TEST is the null world's TEST (the same noise, no drift after the split): per seed
its TEST avg R is -0.143, -0.325, +0.501, -0.176, +0.081 against null's -0.121, -0.231,
+0.543, -0.176, +0.081. The per-seed TEST figures swing from -0.33R to +0.54R on 36-61
trades even where the truth is zero, which is why a label needs a multiplicity-corrected
bound and not a positive mean (section 5). Seed 1 of each world reproduces the committed
seed-1 baseline numbers exactly (section 6).

```python
# strength.py (run with PYTHONPATH=<repo root>); full output in section 9
from concurrent.futures import ProcessPoolExecutor

from research.trendbot.config import StrategyConfig
from research.trendbot.synthetic import HOUR_EDGE_CLOSE_HOURS, WORLDS, make_world
from research.trendbot.walkforward import walk_forward

H = 3_600_000
LO, HI = HOUR_EDGE_CLOSE_HOURS


def run(job):
    world, seed = job
    cfg = StrategyConfig()
    data, events = make_world(world, seed)
    wf = walk_forward(data, cfg, events)
    return world, seed, [t.r_multiple for t in wf.train.trades], [t.r_multiple for t in wf.test.trades], [
        (w, LO <= ((t.signal_ts + cfg.timeframe_ms) // H) % 24 <= HI, t.r_multiple)
        for w, ts in (("TRAIN", wf.train.trades), ("TEST", wf.test.trades))
        for t in ts
    ]


def fmt(rs):
    return f"{sum(rs) / len(rs):+.3f}R (n={len(rs)})" if rs else "n=0"


if __name__ == "__main__":
    jobs = [(w, s) for w in WORLDS for s in range(1, 6)]
    with ProcessPoolExecutor(4) as ex:
        res = list(ex.map(run, jobs))
    for world in WORLDS:
        rows = [r for r in res if r[0] == world]
        tr = [x for r in rows for x in r[2]]
        te = [x for r in rows for x in r[3]]
        print(f"{world:10s} TRAIN {fmt(tr)} | TEST {fmt(te)}")
        for r in rows:
            print(f"    seed {r[1]}: TRAIN {fmt(r[2])} | TEST {fmt(r[3])}")
        if world == "hour_edge":
            tag = [x for r in rows for x in r[4]]
            for inside in (True, False):
                name = "in 12-20 UTC close" if inside else "outside"
                a = [x for w, i, x in tag if w == "TRAIN" and i == inside]
                b = [x for w, i, x in tag if w == "TEST" and i == inside]
                print(f"    {name:20s} TRAIN {fmt(a)} | TEST {fmt(b)}")
```

The committed runs ([`RESULTS_SUMMARY.md`](../results/RESULTS_SUMMARY.md) section 2 lists
every argv and wall time; each folder's `run.log` holds its own):

```bash
# one full pipeline run (baseline, 13-variant discovery, ML layer, expectancy guard) on one
# world and seed; --now fixes the time stamped on the adoption records
python -m research.trendbot.run_research --synthetic planted --seed 1 \
    --now 2026-09-27T12:00:00Z --out-dir research/results/synthetic_planted_s1
#   (the same for null, zero_edge, decay, hour_edge)

# cost sensitivity: the planted world at a 0.6%-per-side fee (committed)
python -m research.trendbot.run_research --synthetic planted --seed 1 --fee-rate 0.006 \
    --exchange-id coinbase --now 2026-09-27T12:00:00Z \
    --out-dir research/results/cost_planted_s1_fee0006

# stop-fill stress (D4; test-only; also runs the k = 0 reference on the same data)
python -m research.trendbot.run_research --synthetic planted --seed 1 --stop-fill-wick-k 0.5 \
    --now 2026-09-27T12:00:00Z --out-dir research/results/stress_k0.5/synthetic_planted_s1
#   (k = 0.5 and 1.0, all five worlds)

# calibration: label frequencies over seeds (writes CALIBRATION.md + calibration_runs.csv)
python -m research.trendbot.run_research --synthetic null --calibrate-seeds 50 \
    --seed-offset 1000 --workers 4 --out-dir research/results/calibration_null_seeds1001-1050
#   (also null / zero_edge / decay seeds 1-50, zero_edge seeds 2001-2050, planted and
#   hour_edge seeds 1-20)

# power curve: 20 seeds at each of 9 planted strengths (writes POWER.md + power_runs.csv)
python -m research.trendbot.run_research --synthetic planted --calibrate-seeds 20 \
    --power-strengths 0.15 0.3 0.45 0.6 0.75 0.9 1.05 1.2 1.4 --workers 4 \
    --out-dir research/results/power_planted

# run and independently audit one synthetic backtest (default config, full history)
python -m research.trendbot.invariants --synthetic planted --seed 1          # 166 trades, 0 violations
python -m research.trendbot.invariants --synthetic planted --seed 1 --gaps   # 164 trades, 0 violations
```

Each seed-1 run writes `REPORT.md` (12 sections), a TRAIN and a TEST journal for each of the
four pre-registered candidates under `journals/` (plus the ML layer's in-sample TRAIN
journal), a review pack and an adoption record per adoptable candidate,
`model_base_plus_ml.json` when the ML layer was fitted, `config_<variant>.json` for every
non-default config, the holdout ledger `test_looks.jsonl` and `run.log`. A stress run holds
journals, a report and its ledger (the k = 0 reference looks), but no adoption record and no
review pack: it is test-only. The process exits with 0 on success, 1 if any backtest broke
an invariant, and 2 on a usage or data error.

### 4.3 Real data (on a machine with network access)

**Nothing below has been run against a live exchange.** The build sandbox has no network
and no ccxt (`fetch_data` then exits 2 with "error: ccxt is not installed. Install it with
`pip install ccxt` on a machine with network access, then re-run this command."). On a
networked machine, install it into the project's virtual environment with
`pip install ccxt` (following your organisation's software-installation policy). In this
cycle `fetch_data` ran only against a fake `ccxt` module (section 9). Treat the first real
download as untested code: read the gap report it prints, and spot-check a few candles
against the exchange's own chart.

**Use the longest history available.** The minimum detectable effect falls roughly as
1/sqrt(number of TEST trades) (section 6). Binance spot serves BTC/USDT and ETH/USDT 4H
candles from mid-August 2017 and BNB/USDT from November 2017 (check `first_utc` per file in
`manifest.json`); that is about 1.5x the six synthetic years.

```bash
# 1) candles + manifest.json (D2), Binance spot, all three pairs
python -m research.trendbot.fetch_data --exchange binance \
    --pairs BTC/USDT ETH/USDT BNB/USDT --timeframe 4h --since 2017-08-01 --out research/data
#    -> research/data/BTC_USDT-4h.csv, ETH_USDT-4h.csv, BNB_USDT-4h.csv, manifest.json

# Coinbase Advanced Trade (ccxt id "coinbase"): BNB is not listed there, so leave it out
# (if requested it is skipped as "not listed" and the command exits 1). No 4h candles:
# fetch_data requests the largest supported divisor (2h per ccxt's timeframe table, not
# verified live), aggregates COMPLETE buckets only, and uses 300-candle pages.
python -m research.trendbot.fetch_data --exchange coinbase \
    --pairs BTC/USDT ETH/USDT --timeframe 4h --since 2017-08-01 --out research/data/coinbase
```

Files are written as `<out>/<BASE>_<QUOTE>-4h.csv` with the header
`ts,open,high,low,close,volume`, and `<out>/manifest.json` is rewritten after every saved
file. The manifest records the run (`exchange_id`, `ccxt_version`, `symbols`, `timeframe`,
`since`/`until`, `fetched_at_utc`) and per file its `name`, `symbol`, `fetch_timeframe`,
`rows`, `first_ts`/`last_ts` and `sha256`. `data.verify_manifest(data_dir, pairs, "4h")`
returns `"real"` only when every loaded file is listed with a matching sha256 (and matching
symbol, timeframe, rows and first/last ts), otherwise `"unverified-csv"`, naming each
problem. **Its limit: an offline check cannot authenticate an exchange download.** The
manifest only shows the files are byte-identical to what it lists; it makes a laundered or
edited CSV a deliberate act (the manifest must be rewritten too) instead of an accident. In
this cycle the fake-ccxt download of synthetic candles verified as `"real"` (section 9), so
only use files `fetch_data` downloaded from the exchange, and let the reviewer confirm it.

**News calendar (R5).** Build `research/data/events.csv` for the WHOLE history with the
header and semantics of section 2.4:

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
(`run_research --exchange-id`, default `binance`).

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
> when a non-Binance exchange is run with the Binance default fee. How much fees matter,
> from the committed `cost_planted_s1_fee0006` run (planted seed 1 at an illustrative 0.6%
> per side): baseline TRAIN | TEST +0.296R (n=115) | +0.412R (n=51) at the default fee
> against +0.127R (n=100) | +0.043R (n=46) at 0.6%, and fees alone cost 0.25R per trade
> instead of 0.06R. The only ROBUST result disappeared: discovery selected
> `rr2_vol2_rsi55-70` (TRAIN +0.423R, n=75 | TEST +0.364R, n=33, UNTESTED) and the verdict
> became "No robust result found."

**Research run, audit, review and adoption on the fetched data.** `--ledger` is the holdout
ledger (keep it with the data and never delete it; section 11). The `0.006` below is a
placeholder for your own tier, not a quoted Coinbase fee:

```bash
python -m research.trendbot.run_research --data-dir research/data \
    --events research/data/events.csv \
    --fee-rate 0.001 --slippage-pct 0.05 --exchange-id binance \
    --ledger research/data/.test_looks.jsonl --out-dir research/results/real_binance
#    judge any candidate with the k = 0.5 stress beside it (test-only, another --out-dir):
#    the same command plus --stop-fill-wick-k 0.5
python -m research.trendbot.run_research --data-dir research/data/coinbase \
    --pairs BTC/USDT ETH/USDT --events research/data/events.csv \
    --fee-rate 0.006 --slippage-pct 0.05 --exchange-id coinbase \
    --ledger research/data/coinbase/.test_looks.jsonl --out-dir research/results/real_coinbase

# independent re-audit of a BACKTEST journal against the candles, with the config the run
# used (config_<variant>.json exists only for a non-default config). A TEST journal needs
# --start-ts = the split in ms; a TRAIN journal needs --end-ts instead.
SPLIT_MS=$(python -c "from research.trendbot.journal import iso_to_ms; print(iso_to_ms('2023-03-14T00:00:00Z'))")
python -m research.trendbot.invariants \
    --journal research/results/real_binance/journals/base_test.csv \
    --data-dir research/data --events research/data/events.csv --start-ts "$SPLIT_MS"

# adoption gate. REPORT.md section 11 prints the exact command for every record: a
# non-default config needs --config, the ML record --model-fingerprint.
python -m research.trendbot.adoption check \
    --record research/results/real_binance/adoption_base.json --stage HUMAN_REVIEW
python -m research.trendbot.adoption check \
    --record research/results/real_binance/adoption_rr3_vol2_rsi50-70.json --stage HUMAN_REVIEW \
    --config research/results/real_binance/config_rr3_vol2_rsi50-70.json
```

`2023-03-14T00:00:00Z` is the split of the six-year synthetic data (and of the fake
download of section 9); a real run prints its own split in REPORT.md section 2 and in the
ledger's `test_start_ts`. These commands ran in this cycle on the fake-ccxt download of the
planted candles plus its 273 events: the run reproduced the committed planted verdict
(`rr3_vol2_rsi50-70` ROBUST, the other three UNTESTED), its records say `provenance: "real"`
with the three data-file hashes, the manifest and the events file, the invariant audit of
`base_test.csv` found 51 trades and 0 violations, `adoption_base.json` was BLOCKED at
HUMAN_REVIEW by `ADOPT_walk_forward` (UNTESTED), and the ROBUST record PASSED HUMAN_REVIEW.
That pass is the provenance limit above made concrete: synthetic candles laundered through a
fake exchange are indistinguishable offline from a download.

The offline checks on the committed synthetic outputs, run in this cycle:

```bash
python -m research.trendbot.adoption check \
    --record research/results/synthetic_planted_s1/adoption_base.json --stage WALK_FORWARD  # PASS, exit 0
python -m research.trendbot.adoption check \
    --record research/results/synthetic_planted_s1/adoption_base.json --stage HUMAN_REVIEW  # BLOCKED: ADOPT_provenance, ADOPT_walk_forward; exit 1
python -m research.trendbot.adoption check \
    --record research/results/synthetic_planted_s1/adoption_rr3_vol2_rsi50-70.json \
    --stage HUMAN_REVIEW \
    --config research/results/synthetic_planted_s1/config_rr3_vol2_rsi50-70.json  # BLOCKED: ADOPT_provenance; exit 1
python -m research.trendbot.adoption check \
    --record research/results/synthetic_planted_s1/adoption_base_plus_ml.json \
    --stage WALK_FORWARD \
    --model-fingerprint 0986e8bdf0e5206e179c52b257b8872e4d740c0438bee6e8f4f175aa8d17f736  # PASS, exit 0
python -m research.trendbot.adoption check \
    --record research/results/synthetic_planted_s1/adoption_base_plus_guard.json \
    --stage WALK_FORWARD \
    --config research/results/synthetic_planted_s1/config_base_plus_guard.json      # PASS, exit 0
python -m research.trendbot.journal_rules \
    --journal research/results/synthetic_planted_s1/journals/base_test.csv \
    --equity 12030.61 --now 2024-12-30T00:00:00Z --backtest-journal   # No active adaptations.
```

All 24 committed adoption records (4 per seed-1 world and 4 in `cost_planted_s1_fee0006`)
pass `--stage WALK_FORWARD` with their `--config` / `--model-fingerprint` and are BLOCKED at
`--stage HUMAN_REVIEW` by `ADOPT_provenance` (plus `ADOPT_walk_forward` where not ROBUST): a
synthetic world is never evidence about real markets. `check` prints `PASS` or every
blocking reason, then what it could not verify, and exits with 0 or 1.

### 4.4 Importing the existing bot's journal (`journal convert`)

The live bot keeps its own CSV journal with its own column names. `journal convert` maps it
onto this package's journal format, so `journal_rules`, `invariants --live-journal` and the
adoption TESTNET audit can read it. There is no universal default mapping: a wrong map
converts silently wrong numbers, so write `map.json` for YOUR journal's real columns and
compare a few converted rows with the bot's own figures.

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
  `--backtest-journal`, with `invariants --live-journal` (section 4.5), and as the adoption
  `testnet.journal_path`.

### 4.5 The invariants CLI

`python -m research.trendbot.invariants` has three modes, and it exits 1 if it finds any
violation:

- `--journal J --data-dir D [--events E] [--start-ts MS] [--end-ts MS]` audits a BACKTEST
  journal against the candles it traded on, including the backtester's fill-price
  identities (fill = next open plus slippage, exit on the first touch) and the backtest
  exit-time convention. The journal must hold every trade of the run.
- `--live-journal J --data-dir D [--events E] [--starting-equity X]` audits a LIVE or
  testnet journal of real fill times (D5): exit offset 0; R6 and R9 across trades; per trade
  R1-R4 and R8 re-derived at its `signal_ts` from the candles, fill timing, planned
  geometry, R7 on the realized equity from `--starting-equity`; R5 only with `--events`
  (without it the CLI says "R5 NOT checked"). The backtest fill-price identities are not
  asserted.
- `--synthetic WORLD --seed N [--years Y] [--gaps]` runs a synthetic backtest and audits it.

Every mode takes `--config C` (JSON `StrategyConfig` overrides, validated: a loosened rule
exits 2) and `--fee-rate` / `--slippage-pct`. A journal written by a discovery variant or
with non-default costs must be audited with the config that produced it, or its exact -1R /
+RR outcomes are (correctly) reported as inconsistent. A stop-fill stress journal is clean
only under its own config (`{"stop_fill_wick_k": 1.0}` audits the k = 1.0 planted history:
166 trades, 0 violations). Run in this cycle on the fake-download data directory:

```bash
python -m research.trendbot.invariants --live-journal \
    research/results/synthetic_planted_s1/journals/base_test.csv \
    --data-dir research/data --events research/data/events.csv --starting-equity 10000
# live journal ... (exit offset 0; R5 checked against 273 event(s)): 51 trades audited, 0 violation(s).
```

### 4.6 The human review pack

`run_research` writes one pack per adoptable candidate, `review_<variant>/trades_review.csv`
and `.md`: every TRAIN and TEST trade with planned RR by price AND net of costs, the pair's
risk cap, notional, hold time, features, machine flags, and blank `reviewer_ok` /
`reviewer_note` columns. With the candles (`review_sheet --data-dir D` or `--synthetic W`),
every row also gets the chart context a reviewer needs (R = the planned all-in risk per
unit):

- `mae_r` / `mfe_r`: maximum adverse / favourable excursion from the fill candle through
  the exit candle, in R;
- `stop_ref_time_utc`, `stop_ref_low`, `stop_ref_bar`: the candle whose low the R8 stop
  sits behind (a confirmed pivot, or the lookback low), re-derived at the signal candle;
  `structure.find_stop` is re-run and must reproduce the recorded stop (`stop_mismatch`
  otherwise);
- `exit_low` and `wick_depth_r = (stop - exit_low) / R` for stop-outs;
- `context_csv` / `context_sha256`: `context/<window>_<trade_id>.csv`, the OHLCV candles
  from 30 candles before the fill through the exit, with the structure, signal, entry and
  exit candles marked. `load_review` re-checks every context file's sha256, so the pack's
  hash also covers them;
- flag `deep_wick_stop`: a non-gap stop-out whose exit-candle low lies more than 0.5 stop
  distances below the stop, where the touch fill is optimistic; the flag states the extra
  loss under the D4 wick fill for k = 0.5 and 1 (also `context_missing`,
  `context_mismatch`).

```bash
python -m research.trendbot.review_sheet \
    --journal research/results/synthetic_planted_s1/journals/base_test.csv \
    --out-dir review_ctx --split 2023-03-14T00:00:00Z --data-dir research/data
# 51 trades (5 flagged, candle context from 4h candle files in research/data (data.load_dataset)) -> ...
```

In that run all 5 flags were `deep_wick_stop`, for example ETH #129: "exit-candle low
8.458938 is 0.775 stop distances (0.720R) below the stop 8.682199, so the touch fill at the
stop is optimistic here, extra loss under the CONTRACT v4 D4 wick fill: k=0.5 0.359R, k=1
0.719R". The 27 stop-outs had a mean `wick_depth_r` of 0.250R, consistent with the k = 1.0
stress moving that journal's mean stop-loss from -1.000R to -1.249R (section 6).

**Limitation:** the packs `run_research` writes (the ones the adoption records bind) carry
these columns but leave them blank: it does not pass the candles to `write_review_pack`.
Until it does, a reviewer should also generate the context pack for each journal with the
command above and read it next to the bound pack.

## 5. How to read results

Every results table puts **TRAIN and TEST side by side**. The split is chronological at 70%
of the common time range of all pairs. TEST starts with fresh equity and fresh circuit
breakers, and indicators warm up on earlier candles only. Fitted and selected things
(discovery selection, ML coefficients) see TRAIN only. Section 2 of every report states the
costs used and the measured fee cost per trade in R.

**The label rule (CONTRACT.md v3 C4), fixed in advance and never tuned on TEST outcomes.**
Four pre-registered candidates get one TEST look each: `base`, the discovery variant
selected on TRAIN, `base+ml` and `base+guard`, so **m = 4**. Every other discovery variant
and the regime-OFF test variant is reported as `context: <label>, not judged`, and gets no
ledger line and no adoption record. `metrics.label(train, test)` applies these rules in
order (`min_train = min_test = 30` closed trades):

| order | condition | label | meaning |
|---|---|---|---|
| 1 | TRAIN n < 30 or TEST n < 30 | `UNTESTED` | too few trades to judge (ROBUST needs 30 + 30 whatever `min_*` say) |
| 2 | TRAIN avg R <= 0 | `NO-EDGE` | nothing to validate |
| 3 | TEST avg R <= 0 | `TRAIN-ONLY` | the edge did not hold out of sample (likely curve-fit) |
| 4 | adjusted lower bound of the TEST mean <= 0 | `UNTESTED` | positive but not distinguishable from zero after multiplicity correction |
| 5 | otherwise | `ROBUST` | positive expectancy on TRAIN and TEST, TEST bound above zero |

For `base+ml`, "TRAIN" is its out-of-sample D8 gate (section 2.5). The adjusted lower bound
is a one-sided bound at confidence **1 - 0.05/m = 98.75%** (Bonferroni over the four looks,
so that when no candidate has an edge the chance that ANY is called ROBUST is at most about
5%). It is computed twice on the TEST R sequence, with 4000 seeded resamples each: an **iid
bootstrap** of the trades, and a **calendar-month block bootstrap** that resamples whole UTC
months of exits, so trades that cluster in one regime count as one piece of evidence
instead of many. The label uses the **more conservative (smaller)** of the two bounds.
Example, planted seed 1 baseline: TRAIN +0.296R (n=115) | TEST +0.412R (n=51), with 98.75%
TEST bounds -0.059R (iid) and -0.125R (block over 20 months), so UNTESTED; the selected
`rr3_vol2_rsi50-70`: TRAIN +0.836R (n=73) | TEST +0.829R (n=35), bounds +0.029R (iid) and
+0.167R (block), so ROBUST.

A candidate "reaches the TEST gate" when its judged TRAIN avg R > 0 and both windows hold
>= 30 trades; from there the bootstrap bound decides. The ML layer is also `UNTESTED` when
it cannot be fitted (fewer than 100 TRAIN candidates, or fewer than 20 wins or 20 losses, in
the full or the inner fit). In that case nothing silently falls back to the unfiltered rules.

**Drawdown (CONTRACT.md v3 C5, revised by v4 D7).** Every result reports two TEST (and
TRAIN) max drawdowns, in percent of equity:

- **realised**: the peak-to-trough drop of the closed-trade equity curve;
- **mark-to-market (MTM)**: the equity at every 4H close, with each open position valued at
  that close (`qty * (close - entry) - entry fee`). It sees the intra-trade dips that the
  closed-trade curve hides.

**`dd_ok` = TEST MTM max drawdown <= min(15%, p95)**, where p95 is the 95th percentile of the
max drawdown of 4000 TRAIN trade sequences bootstrapped at the TEST trade count, compounding
each trade's `r_multiple * risk_pct` (the same percent-of-equity units).

- **Why the cap is 15% (`metrics.DD_CAP_PCT`):** at the mandated 1% cluster risk budget,
  15% is 15 consecutive full-size losses. At the 2:1 break-even win probability p* = 1/3,
  that streak has probability (2/3)^15 = 0.23%, so a TEST drawdown beyond it is inconsistent
  with even a break-even strategy at the mandated risk.
- The TRAIN-bootstrap p95 usually binds first where TRAIN has an edge (all four planted
  seed-1 limits are p95 values, 7.26-11.36%), so a TEST drawdown that is unusual for the
  TRAIN trades fails even below the cap. The cap binds where TRAIN sequences without an
  edge bootstrap deeper drawdowns (for example the null, zero_edge and hour_edge seed-1
  baselines, limit 15.00%).
- The 3% weekly-loss halt (R9) limits how fast a drawdown accrues, not how deep it goes.

`adoption.py` requires both `ROBUST` and `dd_ok` to leave WALK_FORWARD. Example, planted seed
1 baseline: TEST MTM 5.08% (realised 4.90%) against the limit 9.58%, so passed; decay seed 1
baseline: TEST MTM 13.76% against 9.60%, so failed.

The verdict considers only the four pre-registered candidates. It prints the ROBUST ones,
or exactly **"No robust result found."** That is a valid result, and often the correct one.
UNTESTED means "not demonstrated", not "no edge".

**Do not re-select on TEST.** The report shows the TEST label of all 13 discovery variants
for context. Picking one of them *because* of its TEST numbers turns TEST into TRAIN and
invalidates the result. The TRAIN column of the selected variant is biased upward (it is
the best of 12). Only TEST columns are out-of-sample. A new idea is a new variant: it
starts again at BACKTEST, its config fingerprint changes, and it spends another TEST look
(section 11).

## 6. Honest status

A summary of [`research/results/RESULTS_SUMMARY.md`](../results/RESULTS_SUMMARY.md) and the
committed REPORT.md, CALIBRATION.md and POWER.md files, all regenerated from the v4 code.

- **No real-data result exists yet.** The sandbox has no network and no ccxt, so every
  number comes from synthetic worlds. These verify the methodology and are not evidence
  about BTC/ETH/BNB. No variant may be adopted on their strength, and `adoption check`
  blocks every committed synthetic record at HUMAN_REVIEW (`ADOPT_provenance`).
- Scope: 16 single pipeline runs (5 seed-1 worlds, the 0.6%-fee run, 10 stress runs), 290
  calibration seeds and 180 power-curve seeds, each on 6 years of 4H candles and 3 pairs.
  The invariant audit found 0 violations in every backtest of every run. At the default
  costs fees alone cost about 0.06R per baseline trade (median stop distance 3.48-3.79%).

**Seed 1, ground truth vs labels.** Each cell is `label: TRAIN avg R (n) | TEST avg R (n)`;
`base+ml`'s TRAIN is its judged D8 gate, with the in-sample full-TRAIN figure in brackets.

| world (truth) | baseline | discovery-selected | base+ml | base+guard | verdict |
|---|---|---|---|---|---|
| null (no edge) | NO-EDGE: -0.138 (98) \| -0.121 (58) | `rr2.5_vol2_rsi50-70` NO-EDGE: -0.086 (81) \| -0.143 (49) | UNTESTED: -1.000 (8) [+0.267 (45)] \| -0.294 (17) | NO-EDGE: -0.183 (108) \| -0.121 (58) | No robust result (correct) |
| zero_edge (net ~0) | NO-EDGE: -0.004 (94) \| -0.050 (60) | `rr2_vol2_rsi55-70` TRAIN-ONLY: +0.048 (75) \| -0.020 (49) | UNTESTED: +0.000 (0) [+0.333 (9)] \| +0.500 (6) | NO-EDGE: -0.044 (102) \| -0.050 (60) | No robust result (correct) |
| decay (edge in TRAIN only) | TRAIN-ONLY: +0.307 (114) \| -0.143 (56) | `rr3_vol2_rsi50-70` TRAIN-ONLY: +0.742 (70) \| -0.043 (46) | UNTESTED: +0.129 (23) [+0.590 (88)] \| -0.073 (55) | TRAIN-ONLY: +0.307 (114) \| -0.143 (56) | No robust result (correct) |
| planted (edge everywhere) | UNTESTED: +0.296 (115) \| +0.412 (51) | `rr3_vol2_rsi50-70` **ROBUST**: +0.836 (73) \| +0.829 (35) | UNTESTED: +0.278 (18) [+0.489 (94)] \| +0.235 (34) | UNTESTED: +0.296 (115) \| +0.412 (51) | ROBUST (selected variant; correct) |
| hour_edge (edge at 12-20 UTC) | NO-EDGE: -0.044 (100) \| +0.050 (60) | `rr3_vol2_rsi50-70` UNTESTED: +0.203 (72) \| +0.436 (39) | UNTESTED: +0.371 (26) [+0.299 (69)] \| +0.295 (44) | NO-EDGE: -0.057 (111) \| +0.082 (61) | No robust result (a miss) |

**Stop-fill stress (CONTRACT.md v4 D4): how optimistic is the touch fill?** With
`--stop-fill-wick-k k`, a non-gap stop fills at `stop - k*(stop - exit candle low)`, then
slippage (k = 0 is the default touch fill, byte-identical to before). Stressed configs are
test-only. From `stress_k0.5/` and `stress_k1.0/` (REPORT.md section 6 of each; the k = 0
reference inside each stress run reproduces the committed seed-1 run), TRAIN | TEST avg R
(n), labels k = 0 / 0.5 / 1.0:

| world | candidate | k = 0 | k = 0.5 | k = 1.0 | labels |
|---|---|---|---|---|---|
| null | baseline | -0.138 (98) \| -0.121 (58) | -0.189 (98) \| -0.174 (58) | -0.241 (98) \| -0.227 (58) | NO-EDGE / NO-EDGE / NO-EDGE |
| zero_edge | baseline | -0.004 (94) \| -0.050 (60) | -0.048 (94) \| -0.108 (60) | -0.091 (94) \| -0.167 (60) | NO-EDGE / NO-EDGE / NO-EDGE |
| zero_edge | selected `rr2_vol2_rsi55-70` | +0.048 (75) \| -0.020 (49) | +0.010 (75) \| -0.087 (49) | -0.029 (75) \| -0.153 (49) | **TRAIN-ONLY / TRAIN-ONLY / NO-EDGE** |
| decay | baseline | +0.307 (114) \| -0.143 (56) | +0.261 (114) \| -0.198 (56) | +0.215 (114) \| -0.252 (56) | TRAIN-ONLY x3 |
| planted | baseline | +0.296 (115) \| +0.412 (51) | +0.251 (115) \| +0.346 (51) | +0.207 (115) \| +0.280 (51) | UNTESTED x3 |
| planted | selected `rr3_vol2_rsi50-70` | +0.836 (73) \| +0.829 (35) | +0.798 (73) \| +0.773 (35) | +0.760 (73) \| +0.717 (35) | **ROBUST / UNTESTED / UNTESTED** |
| hour_edge | baseline | -0.044 (100) \| +0.050 (60) | -0.103 (100) \| -0.023 (60) | -0.163 (100) \| -0.095 (60) | NO-EDGE x3 |
| hour_edge | selected `rr3_vol2_rsi50-70` | +0.203 (72) \| +0.436 (39) | +0.148 (72) \| +0.361 (39) | +0.093 (72) \| +0.287 (39) | UNTESTED x3 |

- For the baseline, selected and guard rows, k = 0.5 costs 0.04-0.06R per trade in TRAIN
  and 0.05-0.08R in TEST; k = 1.0 (a stop filled at the candle's low) costs 0.08-0.12R in
  TRAIN and 0.11-0.15R in TEST. The planted baseline's mean TEST stop-loss outcome goes
  from exactly -1.000R to -1.125R (k = 0.5) and -1.249R (k = 1.0).
- **The only seed-1 ROBUST result does not survive a harsher fill:** planted
  `rr3_vol2_rsi50-70` falls to UNTESTED at k = 0.5 (TEST +0.773R, n=35, adjusted lower bound
  -0.019R). No stressed candidate became ROBUST.
- `base+ml` rows move more and in both directions (the model is refitted on the stressed
  candidates and its windows hold 0-55 trades); `RESULTS_SUMMARY.md` section 5 has all 20
  rows. **Reading for real data:** an edge whose corrected lower bound is only a few
  hundredths of an R above zero under the touch fill is not robust to execution; judge real
  candidates with the k = 0.5 stress beside the default.

**Calibration (90% Wilson intervals).** "Verdict ROBUST" means any of the four candidates
was ROBUST (the family-wise rate): the FALSE-POSITIVE rate where no edge survives costs in
TEST, and the detection rate where one does. The headline false-positive rates use disjoint
seed ranges (null 1001-1050, zero_edge 2001-2050, decay 1-50), because the worlds share the
noise of a seed and decay seed k's TEST window is null seed k's.

| folder | world | seeds | verdict ROBUST (any of 4) | baseline ROBUST | baseline reached the TEST gate | baseline mean avg R TRAIN \| TEST | baseline dd_ok |
|---|---|---|---|---|---|---|---|
| `calibration_null_seeds1001-1050` | null | 50 | **0/50 = 0% (0-5%)** FP | 0/50 | 5/50 | -0.146 \| -0.046 | 42/50 |
| `calibration_zero_edge_seeds2001-2050` | zero_edge | 50 | **2/50 = 4% (1-11%)** FP | 2/50 | 33/50 | +0.024 \| +0.047 | 46/50 |
| `calibration_decay` | decay | 50 | **0/50 = 0% (0-5%)** FP | 0/50 | 49/50 | +0.458 \| -0.095 | 9/50 |
| `calibration_null` | null | 1-50 | 0/50 (0-5%) | 0/50 | 9/50 | -0.110 \| -0.086 | 43/50 |
| `calibration_zero_edge` | zero_edge | 1-50 | 0/50 (0-5%) | 0/50 | 31/50 | +0.035 \| +0.027 | 46/50 |
| `calibration_planted` | planted | 1-20 | 10/20 = 50% (33-67%) detection | 8/20 | 20/20 | +0.442 \| +0.445 | 20/20 |
| `calibration_hour_edge` | hour_edge | 1-20 | 5/20 = 25% (13-43%) detection | 0/20 | 18/20 | +0.198 \| +0.201 | 18/20 |

- All three no-edge worlds together: 2/150 = 1.3% (0.4-3.9%) false positives. The two
  zero_edge false positives (seeds 2016 and 2041) are the baseline on a barely positive
  TRAIN with a lucky TEST window; 4% is consistent with the ~5% design bound, but not far
  below it.
- Decay is caught twice: never ROBUST (TRAIN-ONLY 30/50, UNTESTED 20/50), and `dd_ok` failed
  in 41/50 seeds because its TEST (null) MTM drawdowns exceed what the TRAIN sequences with
  an edge predict.
- The ML layer helps only where there is something to learn: in hour_edge it doubled TEST
  expectancy (+0.201R -> +0.399R) and beat the base in 18/20 seeds; in null its in-sample
  TRAIN looked better than the base (+0.055R vs -0.110R) but its out-of-sample D8 gate was
  -0.068R and its TEST -0.150R was worse than the base's -0.086R. The D8 gate makes it hard
  to validate at this sample size: its judged TRAIN window rarely holds 30 trades.
- The guard adds nothing measurable: its mean TEST avg R is within 0.002R of the base's in
  every calibration, and it was never ROBUST where the base was not.

**Power curve and the minimum detectable effect** (`power_planted/POWER.md`, 20 seeds per
strength; the TEST column is the mean over seeds of the baseline's observed TEST avg R,
with its 90% interval):

| strength (sigma/candle) | base mean avg R TRAIN \| TEST [90% CI] | base mean n TRAIN \| TEST | base ROBUST = detection | any of 4 ROBUST |
|---:|---|---|---|---|
| 0.15 | +0.037 \| +0.066 [-0.009, +0.141] | 103.1 \| 45.8 | 0/20 = 0% (0-12%) | 0/20 |
| 0.30 | +0.163 \| +0.145 [+0.070, +0.221] | 105.5 \| 45.9 | 1/20 = 5% (1-20%) | 1/20 |
| 0.45 | +0.272 \| +0.252 [+0.193, +0.311] | 113.0 \| 50.3 | 2/20 = 10% (3-26%) | 4/20 |
| 0.60 | +0.388 \| +0.351 [+0.278, +0.425] | 117.5 \| 50.1 | 4/20 = 20% (9-38%) | 7/20 |
| 0.75 | +0.471 \| +0.422 [+0.357, +0.486] | 114.0 \| 48.6 | 6/20 = 30% (16-48%) | 11/20 |
| 0.90 | +0.590 \| +0.582 [+0.508, +0.656] | 102.8 \| 45.9 | 14/20 = 70% (52-84%) | 17/20 |
| 1.05 | +0.672 \| +0.628 [+0.554, +0.701] | 93.3 \| 40.0 | 15/20 = 75% (57-87%) | 16/20 |
| 1.20 | +0.748 \| +0.620 [+0.514, +0.727] | 81.2 \| 33.9 | 8/20 = 40% (24-58%) | 8/20 |
| 1.40 | +0.840 \| +0.727 [+0.654, +0.800] | 66.5 \| 27.9 | 3/20 = 15% (6-32%) | 3/20 |

- **MDE of the baseline at this sample size (6 years, ~46-50 TEST trades): 50% power at
  about +0.50R per trade** (strength ~0.83 sigma, interpolated between 0.75 and 0.9);
  **80% power is never reached** (at most 75%, at +0.63R). Beyond 1.05 sigma detection
  FALLS, because stronger drift pushes RSI above 70, R2 admits fewer signals and TEST n
  drops to 34 and 28. A stronger edge cannot buy power here; only more trades can.
- **Smaller real edges will be labelled UNTESTED.** Edges of +0.15R to +0.42R per trade
  (0.3-0.75 sigma) were detected by the baseline in 5-30% of runs, +0.07R never. UNTESTED
  means "not shown", never "shown to be absent". The label rule is fixed and will not be
  loosened to find more.
- **Recommendation: use the longest history.** The standard error of the TEST mean shrinks
  roughly with sqrt(n), so the MDE falls roughly as 1/sqrt(n). Binance's history from
  2017-08 is about 9 years instead of 6, about 1.5x the TEST trades, which lowers the MDE by
  about 1/sqrt(1.5) = 0.82, to roughly +0.41R at 50% power, and only if the edge is stable
  over those years. Most plausible real edges would still sit below it.

Other weaknesses:

- Calibration samples are modest (0/50, 2/50 and 0/50 bound the per-world false-positive
  rate below about 5-11%), and the paired seed 1-50 runs are not independent across worlds.
- The drawdown rule compares an MTM drawdown with a bootstrap of closed-trade returns,
  which makes `dd_ok` conservative; it is only as good as the TRAIN window is representative.
- The planted effect is a positive-control fixture, not an estimate of any real edge, and
  realistic Coinbase low-tier fees remove an edge of this size (section 4.3).
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
  seed-1 model (249 candidates, 81 wins, fingerprint `b38d6af2...`), the sklearn half has
  not been run:

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
  fingerprint, walk-forward, TEST look and adoption path. A flexible model on a small
  sample memorises noise: the calibration shows that even the 6-feature logistic layer's
  in-sample TRAIN numbers overstate its out-of-sample results in a world with no edge
  (null seeds 1-50: in-sample TRAIN +0.055R, out-of-sample D8 gate -0.068R, TEST -0.150R).

## 8. Graduation to freqtrade and FreqAI

**freqtrade (the execution host).** This repository is freqtrade, which already provides
exchange connectivity, order handling, persistence and a dry-run mode. This package
deliberately has none of that. Graduate a variant into a freqtrade strategy only once its
adoption record has a REAL-data ROBUST walk-forward and a signed-off HUMAN_REVIEW, and do
it *before* the TESTNET stage, so that the 2-week testnet run exercises the executor that
will trade live. Constraints on the port:

- It must drive `gatekeeper.LiveSession` (section 2.2) for entries. Re-expressing R1-R9
  inside freqtrade's dataframe logic would give two code paths that can diverge.
- It must write `journal.write_journal`-format journals with real fill times (or a CSV that
  `journal convert` maps, section 4.4) and the decisions log
  (`adoption_evidence.write_decisions_log(session.decision_log, ...)`), so that
  `journal_rules`, `invariants --live-journal` and the TESTNET audit can check it.
- freqtrade's dry-run is a simulated local wallet (`docs/configuration.md`). The TESTNET
  stage requires real orders on `binance-testnet`, so a dry-run does not replace it.
- Re-tuning the variant with freqtrade hyperopt is a new search, and so a new variant. Its
  config fingerprint changes and it starts again at BACKTEST on unseen TEST data.

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
`model_base_plus_ml.json` and an `adoption_base_plus_ml.json` record carrying the
fingerprint; the live bot loads the file with `MLFilter.from_json(text,
expected_fingerprint=...)` (section 2.2). `adoption check` blocks (`ADOPT_fingerprint`) when
the supplied fingerprint differs from the recorded one, when none is supplied for a record
that has one, when one is supplied for a record without one, and when a `+ml` variant has
no recorded fingerprint. **Refitting the model (new TRAIN data, a later split, another
`l2`) changes the fingerprint, so a refitted model is a new candidate and restarts the
adoption path at BACKTEST**, with a new TEST look. FreqAI's periodic retraining would
therefore restart the path on every retrain; nothing pins a FreqAI model yet.

## 9. Verification notes

Every command in this file except `pip install ccxt` / `pip install scikit-learn`, the
scikit-learn half of the section 7 snippet and the real-exchange `fetch_data` commands was
run in this cycle in the build sandbox (Python 3.11, 4 CPUs, no network, no ccxt, no numpy
or scikit-learn). Commands that only read committed files ran from the repository root;
commands that write ran in a scratch copy of `research/` (`diff -r` against the repository:
identical), so that they could run with the same relative paths. Nothing was written into
the repository except this README.

- **Full suite and lint:** `python -m pytest research/trendbot/tests -q` -> "1337 passed in
  206.53s (0:03:26)"; `ruff check research/` -> "All checks passed!"; `ruff format --check
  research/` -> "61 files already formatted".
- **Section 3.** The 231 distinct test functions named in the map each appear in
  `pytest --collect-only`. Selected together with `pytest -k` they ran as 504 cases
  ("504 passed, 833 deselected in 127.43s").
- **Section 1** was recounted from the 45 committed seed-1 journals (`journal.read_journal`):
  2,767 trades, 1,003 TP (max deviation from +RR 2.3e-14), 1,741 SL (max |r + 1| 3.3e-16, none
  below -1R), 23 END. **Section 2.5** from REPORT.md section 5 of `synthetic_planted_s1`.
- **Section 2.1.** The three refusals exited 2 with the errors shown; no out-dir was created.
- **Section 2.2.** The startup snippet ran from `research/results/synthetic_planted_s1/` and
  printed what is shown (`from_json` accepted the committed model with the record's
  fingerprint; `require_stage` raised `AdoptionBlocked`). The per-candle loop ran in scratch
  in 3.6 s and printed what is shown.
- **Sections 2.3, 4.3 (offline), 4.5.** The `journal_rules`, `adoption check` and
  `invariants` commands printed what is shown. All 24 committed records were checked at
  WALK_FORWARD and HUMAN_REVIEW with `adoption.check_promotion` (each with its config file
  and recorded model fingerprint): 24 PASS, then only `ADOPT_provenance` (plus
  `ADOPT_walk_forward`) at HUMAN_REVIEW.
- **Section 4.2, strength check.** The script above printed (13 s on 4 CPUs):

  ```text
  null       TRAIN -0.134R (n=480) | TEST -0.008R (n=230)
  planted    TRAIN +0.385R (n=576) | TEST +0.488R (n=240)
  decay      TRAIN +0.382R (n=571) | TEST -0.029R (n=232)
  hour_edge  TRAIN +0.146R (n=569) | TEST +0.146R (n=264)
      in 12-20 UTC close   TRAIN +0.493R (n=262) | TEST +0.570R (n=128)
      outside              TRAIN -0.150R (n=307) | TEST -0.254R (n=136)
  zero_edge  TRAIN +0.001R (n=502) | TEST +0.048R (n=238)
  ```

  (plus one line per seed; seed 1 of each world equals the committed seed-1 baseline).
  `invariants --synthetic planted --seed 1` audited 166 trades, `--gaps` 164, and `--config`
  `{"stop_fill_wick_k": 1.0}` 166, all with 0 violations.
- **Section 4.3, fetch and research run.** A fake `ccxt` module on `PYTHONPATH` (a `binance`
  class serving the planted seed-1 candles as 4h, `__version__ = "0.0-fake"`) stood in for
  the exchange. `fetch_data --exchange binance --pairs BTC/USDT ETH/USDT BNB/USDT --since
  2017-08-01 --out research/data` exited 0, wrote 13,140 candles per pair and
  `manifest.json`, and `verify_manifest` returned `real`. After one volume cell of
  `BTC_USDT-4h.csv` was changed in a copy, it returned `unverified-csv` ("sha256 ... does
  not match the manifest's ... (file changed after download, or not the downloaded
  file)"). The planted world's 273 events were written to `research/data/events.csv`. The
  `run_research --data-dir` run (31 s) and its follow-ups gave the results stated in 4.3.
- **Section 11, pinned rule and holdout.** On that scratch ROBUST record: editing
  `label_params.mtm_max_dd_pct` to 4.0 blocked ("... gives 5.361207364922934 (tolerance
  1e-09)"); `m = 3` blocked ("pinned to metrics.M_CANDIDATES = 4"); an extra key blocked
  (closed schema); `--config` with `stop_fill_wick_k` 0.5 blocked with `ADOPT_fingerprint`,
  `ADOPT_test_only` and `ADOPT_holdout`. Re-running the identical command into another
  out-dir appended 4 ledger lines and kept the distinct count at 4; re-running with
  `--fee-rate 0.0012` on the same TEST window raised it to 8 and blocked every record,
  the original ROBUST one included, with `ADOPT_holdout` ("a revised variant needs a TEST
  window starting at or after 2024-12-30T00:00:00Z").
- **Section 11, testnet.** With those 4 revised-run lines deleted from the scratch ledger
  (which the gate cannot detect: the ledger is not hash-bound), the scratch ROBUST record
  was given an all-`Y` review pack (108 rows, re-hashed) and a fabricated 14-day "testnet"
  run: the planted candles shifted to 2026-10-02..2026-10-16, driven through
  `LiveSession` (offset 0, mid-candle exit fills, starting equity 10,000). It took 1 trade
  (BNB, TP, +3.0R) out of 255 evaluated signal candles. `check_promotion` at a check time of
  2026-10-17 returned PASS for TESTNET and LIVE, and `invariants --live-journal` on its
  journal and candles found 1 trade and 0 violations (R5 checked against 273 events).
  Removing the trade's allowed row from the decisions log (re-hashed) blocked LIVE with the
  replay difference "the replay allows BNB/USDT 2026-10-04T08:00:00Z (no row in the
  decisions log) but the decisions log does not"; ending the run a day early blocked it with
  "testnet ran 13.00 days ..., short of the required 14 days". The CLI at the real time
  blocked the same record because the review and testnet dates lie in the future.
- **Section 4.4.** The header and `map.json` were written as shown with three made-up rows
  (`BTCUSDT` TP, `ETH-USDT` SL, `bnb_usdt` manual close). `journal convert` exited 0; the
  converted TP is +2.0R, the SL -1.0R, the manual close +0.2399R, and `journal_rules` printed
  "No active adaptations.".
- **Section 4.6.** The `review_sheet --data-dir` command ran on the fake-download data and
  printed what is shown; it wrote 51 context CSVs.
- **Section 7.** The first half of the snippet printed fingerprint `b38d6af2d40d...`, 249
  candidates and 81 wins, the committed hour_edge model. The scikit-learn half was NOT run.
- **Section 8.** The quotes were checked against `docs/freqai-parameter-table.md` lines 48,
  49 and 59 and `docs/freqai-running.md` lines 141-142.

## 10. Where the outputs live

- `research/results/synthetic_<world>_s1/REPORT.md`: the 12-section report. It starts with
  the win-rate statement, then data provenance, costs and the C4 statistics used, TRAIN |
  TEST tables with both drawdowns, discovery, entry layers with veto counts and the D8
  gate, verdict with the holdout-ledger counts, rule-denial counts, invariant audit, journal
  rules, review packs and adoption stage.
- `research/results/<run>/journals/*.csv`: a TRAIN and a TEST journal for each of the four
  pre-registered candidates, plus `base_plus_ml_train_insample.csv` (context only).
- `research/results/<run>/review_*/trades_review.{md,csv}`: the human review packs (blank
  `reviewer_ok` / `reviewer_note` columns).
- `research/results/<run>/adoption_<variant>.json`: the evidence-bound adoption records. The
  ML one carries the model fingerprint, and `model_base_plus_ml.json` is the model it pins.
- `research/results/<run>/config_<variant>.json`: the `StrategyConfig` overrides of a
  non-default config, for `adoption check --config` and `invariants --config`.
- `research/results/<run>/test_looks.jsonl`: the holdout ledger of a synthetic run (a real
  run defaults to `<data-dir>/.test_looks.jsonl`).
- `research/results/<run>/run.log`: the exact command and the console summary.
- `research/results/cost_planted_s1_fee0006/`: the planted world at 0.6% per side.
- `research/results/stress_k0.5/`, `stress_k1.0/`: stop-fill stress runs of the five
  worlds (test-only: journals, report, ledger; no records, no packs).
- `research/results/calibration_<world>[_seeds...]/`: `CALIBRATION.md`,
  `calibration_runs.csv`, `run.log`.
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
blocking reason with its rule id, then what it could not verify. Relative paths in a record
are resolved against the record's directory (the CLI) or `base_dir` (`require_stage`), and a
"hash-bound" file must exist and have exactly its recorded sha256
(`python -m research.trendbot.adoption hash FILE` prints it). What each check verifies:

**On every check** (`ADOPT_record`, `ADOPT_fingerprint`): the record names its variant; the
config being promoted (the defaults, or `--config`) has exactly the recorded config
fingerprint; for an ML variant the supplied model fingerprint equals the recorded one, a
`+ml` variant must have one, and none may be supplied for a record without one.

1. **Backtest** (checked when promoting to WALK_FORWARD or later; `ADOPT_backtest`,
   `ADOPT_provenance`): `REPORT.md` is hash-bound; `completed_utc` is a valid time, not in
   the future; every `data_files` entry, the `events_file` and the `manifest`, if recorded,
   are hash-bound.
2. **Walk-forward, recomputed from the journals with the pinned label rule** (checked when
   promoting to HUMAN_REVIEW or later):
   - **provenance (`ADOPT_provenance`, D2):** `provenance` must be exactly `"real"` with at
     least one data-file hash, a recorded `manifest` that is the `manifest.json` of the
     directory holding every data file, `data.verify_manifest` returning `"real"`, and each
     data-file hash equal to the one it measured. `synthetic:<world>:<seed>`,
     `unverified-csv` or a missing value blocks. **Limit:** an offline check cannot
     authenticate an exchange download; the manifest makes a laundered CSV a deliberate act
     instead of an accident (section 9: a fake-exchange download passed).
   - **test-only configs (`ADOPT_test_only`, D1):** R4 off or any `stop_fill_wick_k > 0`
     never goes past WALK_FORWARD, and such a context variant also gets `ADOPT_holdout`.
   - **pinned label parameters (`ADOPT_walk_forward`, D1):** `label_params` is the closed
     schema `{seed, n_boot, m, alpha, max_dd_pct, train_dd_p95_pct, mtm_max_dd_pct}` (any
     other key blocks), with `m == metrics.M_CANDIDATES` (4), `alpha == metrics.ALPHA`
     (0.05), `n_boot >= metrics.N_BOOT` (4000), `seed == walkforward.SUMMARY_SEED` (7) and
     `max_dd_pct` capped by `metrics.DD_CAP_PCT` (15). The parameters are passed by name,
     never by signature introspection. The committed and scratch records carry `{"seed":
     7, "n_boot": 4000, "m": 4, "alpha": 0.05, "max_dd_pct": 15.0, ...}`.
   - **recomputed numbers:** both journals are hash-bound and re-parsed (closed trades
     only, unique ids, the record's variant, TRAIN signals before `split_utc`, TEST at or
     after it); `metrics.summarize`, `metrics.label` and `metrics.dd_check` are recomputed,
     and the typed label, `train_n`, `test_n`, `train_avg_r`, `test_avg_r` (to 1e-9) and
     `dd_ok` must equal them. **The C5 inputs are recomputed too:** `train_dd_p95_pct` as
     `metrics.train_dd_quantile(TRAIN journal, TEST n, n_boot, seed)`, and for `"real"`
     provenance `mtm_max_dd_pct` from the TEST journal and the hash-bound candle files; a
     difference above 1e-9 blocks.
   - the label must be exactly `ROBUST` with `train_n` and `test_n` >= 30, `test_avg_r > 0`
     and `dd_ok` true.
   - **the TEST-look ledger (`ADOPT_holdout`, D3): holdout reuse is controlled.** Every
     re-run on the same data is another TEST look: each time a candidate is backtested on a
     TEST window, its TEST numbers are seen, and a candidate revised and re-run there is
     judged on data that already shaped it. `run_research` appends one JSON line per
     adoptable pre-registered candidate that got a TEST backtest (`run_utc`, `argv`,
     `variant`, `config_fingerprint`, `model_fingerprint`, `split_utc`, and per pair the
     file sha256 and TEST window) to `--ledger` (default `<data-dir>/.test_looks.jsonl` for
     real data). The gate requires the record's own look in the ledger at
     `record.ledger_path` (else it is a context variant), and blocks if the DISTINCT
     `(config_fingerprint, model_fingerprint)` whose TEST window overlaps the record's
     exceed m = 4 on any pair. Re-running the identical candidates is not a new look.
     **After a reviewer N, or any other revision, the variant needs TEST data it has never
     seen:** a TEST window starting at or after the latest `test_end_ts` of every earlier
     look; it is never re-run on the same TEST window. **Limit:** the ledger is append-only
     and not hash-bound, so a deleted line, or looks written to another ledger, cannot be
     detected offline (section 9 deleted four). It makes reuse visible, not impossible.
3. **Human review of the trade-by-trade list** (checked when promoting to TESTNET or later;
   `ADOPT_human_review`). A named reviewer opens the review pack (section 4.6), enters `Y`
   or `N` in `reviewer_ok` on EVERY row, re-hashes the filled-in CSV with `adoption hash`
   into `human_review.review_sha256`, and fills `reviewer`, `date_utc`, `trades_reviewed`,
   `trades_total` and `approved`. The check verifies the hash, that the pack parses (and
   its context CSVs match their hashes), that its `(window, trade_id)` keys, pairs and
   signal times equal the journals' trades, that **every `reviewer_ok` is `Y` or `N`** (a
   blank blocks), the counts, `approved` true, and a review date no earlier than the
   backtest. **Any `N` blocks**, and the policy is that the variant is revised and restarts
   at BACKTEST under a new record, on unseen TEST data (above). The pack proves only that
   every row got an explicit verdict, not that the reviewer looked.
4. **At least 14 days on the Binance spot testnet** (testnet.binance.vision; checked when
   promoting to LIVE; `ADOPT_testnet`, D6). The record's `testnet` section holds:
   `exchange: "binance-testnet"`, `start_utc` / `end_utc` (>= 14 days, starting no earlier
   than the review sign-off, not ending in the future), `trades` (>= 1),
   `rule_violations: 0`, `starting_equity`, and hash-bound evidence files: `journal_path`
   (the bot's journal, real fill times, converted with `journal convert` if needed),
   `decisions_path` (the **decision log**: a CSV of every evaluated signal, `pair,
   signal_ts, allowed, rule, reason`, i.e. `write_decisions_log(session.decision_log)`),
   `candles` (the 4H candle files the bot evaluated) and optionally `events_path` (the news
   calendar it traded under). The check then:
   - **replays `gatekeeper.LiveSession` over the candles** whose close lies in the window,
     injecting the journal's actual fills and exits; the replayed allowed `(pair,
     signal_ts)` set must equal the journal's entries AND the decision log's allowed rows,
     with every difference listed;
   - **audits the journal in live-journal mode** (`invariants.live_journal_violations`,
     exit offset 0: per trade R1-R4 and R8 from the candles, fill timing, geometry, R7 on
     the realized equity; R6 and R9 across trades; R5 only with `events_path`, otherwise
     the reason text says R5 was not verified), and rejects any trade with a
     mandatory-rule flag of `review_sheet.auto_flags`;
   - **states what to expect and what it cannot show:** at the measured baseline rate of
     about 1 trade per 14 days (154-170 baseline trades per 6 years in the seed-1 worlds),
     a 14-day window is expected to hold about **1 trade** (with a Poisson rate of 1 there
     is a 37% chance of none, and a run without a trade never passes). **Binance spot
     testnet prices and liquidity are not mainnet, so this stage verifies execution and
     rule compliance, not edge.**

   Section 9 drove a fabricated 14-day run through all of this: consistent evidence
   passed, a removed decision-log row or a 13-day window blocked. A consistent fabrication
   passing is the point: the checks prove the evidence is internally consistent and
   rule-compliant, not that it came from the testnet.

   With ccxt the testnet is selected like this (not run: there is no network or ccxt here):

   ```python
   import os
   import ccxt

   exchange = ccxt.binance({"apiKey": os.environ["BINANCE_TESTNET_API_KEY"],
                            "secret": os.environ["BINANCE_TESTNET_SECRET"]})
   exchange.set_sandbox_mode(True)  # route requests to the Binance spot testnet
   ```

   Keep keys in the environment, never in code, configs or journals.
5. **Only then the live config** (`ADOPT_live`). Test-only configs never go LIVE, and
   `live.enabled_utc` may not precede the end of the testnet run. The live bot calls
   `adoption.require_stage(record, "LIVE", cfg, now_ms, base_dir)` at startup (plus
   `model_fingerprint=` for an ML variant, section 2.2) and refuses to trade if it raises.
   Not checkable from a record: that the testnet account really was a testnet, that the
   candles were the exchange's, and tick rounding.

## 12. Disclaimer

This is a research and testing tool, not financial advice and not a recommendation to trade.
Backtests and synthetic worlds are simplified models: past or simulated results do not
predict future results, and no result in this repository comes from real market data.
Crypto trading can result in the total loss of the capital used.
