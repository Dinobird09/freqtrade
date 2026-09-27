# trendbot: research, validation and adoption layer for the 4H trend bot

Research and testing tool only. Not financial advice. Crypto trading can result in the total
loss of the capital used. See [Disclaimer](#12-disclaimer).

## 1. No win rate is promised or targeted

Nothing in this package promises, targets or optimises a win rate:

- A win rate tells you nothing without the payoff it was earned at. At the mandatory 2:1
  minimum a strategy breaks even at 1/(1+2) = 33.3% winners before costs. A 90% win rate
  with a 1:9 payoff only breaks even before costs, and loses money after them.
- Optimising win rate pushes towards near targets and far stops. That is the opposite of
  the 2:1 rule, and it rewards curve-fitting. A backtest win rate near 90% is a red flag
  for look-ahead or overfitting, not a sign of skill.
- In the code: discovery selects on the TRAIN t-stat of R-multiples
  (`strategy_discovery.select_on_train`). The ML threshold is the break-even probability
  implied by the TRAIN average win and loss in R (`ml_filter`, no threshold search). The
  walk-forward labels use average R and its bootstrap CI (`metrics.label`). Reports show
  win rate as context only, and the human review pack (`review_sheet`) leaves it out.

**Target metric: positive expectancy.** That is the average R per closed trade, net of fees
(0.10% per side) and slippage (0.05% on market fills). It must survive a 70/30
chronological walk-forward (label `ROBUST`) with a controlled TEST max drawdown (at most
20%: `metrics.dd_check`, `walkforward.DEFAULT_MAX_DD_PCT`).

**Reward:risk >= 2:1, no exceptions.** `config.StrategyConfig` raises `ConfigError` for
`reward_risk < 2.0`. The take-profit is computed from the actual fill:
`target = fill + reward_risk * (fill - stop)` (`Gatekeeper.plan_fill`). The invariant
auditor and the review pack flag any trade planned below the configured RR, and on testnet
that flag counts as a rule violation. 2:1 is the *planned* reward:risk. Fees are charged
on the notional, so a take-profit trade realises less than 2R: +1.62R to +1.98R across the
299 planned-2:1 take-profits in the committed review packs, with tighter stops losing more
to fees.

## 2. What this is

- **A layer on top of the existing bot's rules. R1-R9 are unchanged.** `config.py` checks
  every mandate when a config is built and raises `ConfigError` on any loosening. A
  search or ML layer can therefore only *tighten* rules or *remove* trades, never add them.
  There is one documented exception: `regime_filter=False` (R4 off, "when explicitly
  testing it off"). It is flagged `is_test_only`, can never be selected by discovery, and
  `adoption.py` never lets it go LIVE.
- **Python 3.11 standard library only.** The one exception is `ccxt`, imported lazily
  inside `fetch_data.main`. It is only needed to download real candles.
- **Deterministic.** All randomness goes through `random.Random(seed)` and the bootstrap
  seed is fixed. Re-running the committed synthetic commands reproduces the committed
  reports and calibration files byte for byte, apart from the out-dir path line and the
  run timestamp in the adoption records.
- **Auditable.** Every denied signal candle is logged with its rule id
  (`config.RULE_IDS`) and a one-sentence reason. `invariants.check_invariants` re-derives
  every rule from the trade list and the candles. It does not reuse the gatekeeper's,
  breakers' or news calendar's code; only `compute_features` is shared.
- **Not included.** Live order execution, exchange account handling and a testnet runner.
  The live bot is responsible for these and must use the gatekeeper below.

| module | role |
|---|---|
| `models.py`, `config.py` | shared types; validated config (mandate floors and ceilings) |
| `indicators.py`, `signals.py`, `structure.py` | EMA / Wilder RSI / previous-20 volume mean; R1-R4 gates; R8 stop |
| `sizing.py`, `correlation.py`, `news.py` | R7 sizing; R6 shared cluster budget; R5 calendar |
| `circuit_breakers.py`, `journal.py`, `journal_rules.py` | R9; CSV trade journal; "learn from past trades" as explicit rules |
| `gatekeeper.py` | **the single entry-decision path** (backtest and live) |
| `backtester.py`, `invariants.py` | event-driven backtest; independent rule auditor |
| `data.py`, `fetch_data.py`, `synthetic.py` | candle CSVs; ccxt download; synthetic worlds with known truth |
| `metrics.py`, `walkforward.py`, `strategy_discovery.py`, `ml_filter.py` | stats and labels; 70/30 walk-forward; TRAIN-only selection; logistic filter |
| `run_research.py`, `review_sheet.py`, `adoption.py` | one-command report; human review pack; adoption-path gate |

### The gatekeeper is the single entry-decision path

The backtester calls `Gatekeeper.evaluate` for every signal candle and `Gatekeeper.plan_fill`
for every fill (`test_backtester.py::test_every_signal_candle_goes_through_the_gatekeeper`).
A live bot must call the same two methods, so a backtest and the bot cannot diverge on a
mandatory rule. Order inside `evaluate` (the first failure wins and names its rule):

```text
R1-R4 signals.check_entry -> R5 NewsCalendar.check -> R9 CircuitBreakers.can_enter
-> R6 CorrelationGuard.check -> R8 structure.find_stop -> L_ml_filter (if given)
-> L_expectancy_guard multiplier -> R7 risk = min(pair cap, R6 allowance) x guard
```

`plan_fill` then refuses a fill at or below the stop (R8 "gapped through stop"). It
re-checks the stop distance against the actual fill (R8), sets the 2:1+ target, and sizes
the position from the stop distance on realized equity, capped by free cash (R7). If less
than the minimum order can be bought it returns `X_capital`.

What the live bot calls on **each closed 4H candle**. The calls, argument order and
attribute names below were executed offline against synthetic candles. Exchange I/O is
left as comments.

```python
import time

from research.trendbot.adoption import load_record, require_stage
from research.trendbot.circuit_breakers import CircuitBreakers
from research.trendbot.config import StrategyConfig
from research.trendbot.gatekeeper import Gatekeeper
from research.trendbot.indicators import compute_features
from research.trendbot.journal import read_journal
from research.trendbot.news import load_events

cfg = StrategyConfig()  # the adopted variant's overrides; any loosening raises ConfigError
# At startup: refuse to trade unless the adoption record allows LIVE (raises AdoptionBlocked).
require_stage(load_record("adoption_base.json"), "LIVE", cfg, time.time_ns() // 1_000_000,
              base_dir=".")
journal = read_journal("trades.csv")  # every trade the bot has taken (journal.write_journal)
gk = Gatekeeper(cfg, events=load_events("events.csv"),
                breakers=CircuitBreakers.from_journal(journal, cfg))  # R9 state after restart

# When the exchange reports an exit (SL/TP): journal it, then feed R9. Exits are never gated.
gk.on_trade_closed(trade, equity=equity_after_close)

# At each 4H close, for every pair in sorted order:
candles = closed_4h_candles(pair)  # oldest first, ending with the candle that just closed
rows = compute_features(candles, cfg)
closed = [t for t in journal if t.is_closed]
dec = gk.evaluate(pair, candles, rows, len(candles) - 1, open_risk, equity, closed, None)
if not dec.allowed:
    record_denial(pair, dec.rule, dec.reason)  # audit trail: rule id + one sentence
else:
    # market buy -> market_price (price met) and fill_price (executed, incl. slippage)
    plan = gk.plan_fill(pair, dec, market_price, fill_price, equity, free_cash)
    if plan.ok:
        # stop order at plan.stop, limit take-profit at plan.target, quantity plan.sizing.qty
        open_risk[pair] = dec.risk_pct  # the share of the R6 budget this trade reserves
    else:
        pass  # flatten immediately: the fill broke R8 or the capital limit (plan.rule/.reason)
```

The live bot must also round quantities DOWN and take-profit prices UP to the exchange
tick, so that R7 and the 2:1 minimum still hold after rounding (see `adoption.py`).

## 3. Rule-to-code-to-test map

Each test named here exists and passes. All of them were run by node id (see the
verification notes at the end). The file prefix is `research/trendbot/tests/`.

| rule | enforced by | proven by |
|---|---|---|
| **R1** trend: close > EMA9, close > EMA21, EMA9 > EMA21, 4H | `signals.check_entry` (trend gate) on `indicators.compute_features`; `StrategyConfig.validate` fixes EMA 9/21/200; `data.load_dataset` refuses files that are not 4H apart | `test_signals.py::test_trend_gate_fails`<br>`test_signals.py::test_trend_detail_lists_every_broken_condition`<br>`test_signals.py::test_config_refuses_loosened_entry_gates`<br>`test_data.py::test_load_dataset_rejects_wrong_timeframe_and_empty_files` |
| **R2** RSI(14) in [50, 70] | `signals.check_entry` (momentum gate, both ends inclusive); `indicators.rsi_wilder`; `StrategyConfig.validate` (period 14, window inside [50, 70]) | `test_signals.py::test_momentum_bounds_inclusive`<br>`test_signals.py::test_momentum_fails_outside_window`<br>`test_indicators.py::test_rsi_matches_published_wilder_worksheet`<br>`test_adoption.py::test_config_from_overrides_rejects_bad_or_loosening_values` |
| **R3** volume >= 1.5x previous-20 average | `signals.check_entry` (volume gate); `indicators.prev_mean` (current candle excluded); `StrategyConfig.validate` (multiple >= 1.5, lookback 20) | `test_signals.py::test_volume_exactly_at_multiple_passes`<br>`test_signals.py::test_volume_below_multiple_fails`<br>`test_indicators.py::test_prev_mean_excludes_current_value`<br>`test_signals.py::test_config_refuses_loosened_entry_gates` |
| **R4** close > EMA200 unless explicitly tested off | `signals.check_entry` (regime gate); `StrategyConfig.is_test_only`; `strategy_discovery.select_on_train` never selects it; `adoption.check_promotion` blocks LIVE | `test_signals.py::test_regime_close_not_above_ema200_fails`<br>`test_signals.py::test_regime_disabled_is_an_explicit_pass`<br>`test_strategy_discovery.py::test_select_on_train_rules`<br>`test_adoption.py::test_test_only_config_may_reach_testnet_but_never_live` |
| **R5** no entries +/-2h of high-impact news; BNB +/-24h around burns / launchpools | `news.NewsCalendar.check` (bisect over sorted events, both bounds inclusive), step 2 of `Gatekeeper.evaluate`; `StrategyConfig.validate` (windows may widen, never narrow) | `test_news.py::test_high_impact_all_boundaries_are_inclusive`<br>`test_news.py::test_bnb_events_block_bnb_for_24h_any_impact`<br>`test_news.py::test_bnb_burn_does_not_block_btc_or_eth`<br>`test_backtester.py::test_news_blackout_blocks_entries_end_to_end`<br>`test_invariants.py::test_disabled_news_rule_is_caught` |
| **R6** BTC/ETH/BNB share one risk budget, never full size on more than one, BNB never stacks | `correlation.CorrelationGuard.check` (no pyramiding, BNB exclusive, `allowed = min(requested, remaining)`); `StrategyConfig.validate` (cluster budget <= largest single-pair cap, BNB exclusive) | `test_correlation.py::test_btc_open_at_full_size_denies_eth`<br>`test_correlation.py::test_bnb_open_denies_btc_and_eth`<br>`test_correlation.py::test_btc_or_eth_open_denies_bnb_even_with_budget_left`<br>`test_correlation.py::test_two_full_size_positions_are_impossible_by_config`<br>`test_backtester.py::test_bnb_never_stacks_with_another_cluster_position`<br>`test_invariants.py::test_disabled_correlation_cap_is_caught` |
| **R7** risk <= 1% BTC/ETH, <= 0.5% BNB; size derived from the stop distance | `sizing.size_for_pair` / `size_position` (`qty = equity * risk% / (entry - stop)`, notional cap only shrinks), called by `Gatekeeper.plan_fill` on the actual fill; `StrategyConfig.validate` (caps) | `test_sizing.py::test_basic_size_is_derived_from_stop_distance`<br>`test_sizing.py::test_wider_stop_means_smaller_position_same_risk`<br>`test_sizing.py::test_size_for_pair_enforces_the_pair_cap`<br>`test_gatekeeper.py::test_plan_fill_rechecks_the_fill_and_sizes_from_the_stop`<br>`test_backtester.py::test_risk_caps_and_size_derived_from_the_stop` |
| **R8** stop behind the swing low with a buffer (BNB 0.5-0.8%) | `structure.find_stop` / `latest_confirmed_pivot` (a pivot is usable only k candles after it; fallback = lowest low of the last N); `Gatekeeper.plan_fill` (gap-through refusal, distance re-check from the fill); `StrategyConfig.validate` (BNB buffer range, buffer > 0) | `test_structure.py::test_confirmed_pivot_with_btc_buffer`<br>`test_structure.py::test_unconfirmed_pivot_is_not_used`<br>`test_structure.py::test_find_stop_never_reads_candles_after_i`<br>`test_structure.py::test_config_keeps_the_bnb_buffer_in_the_mandated_range`<br>`test_backtester.py::test_entry_that_gaps_through_the_stop_is_skipped`<br>`test_invariants.py::test_stop_not_behind_structure_is_caught` |
| **R9** 3 consecutive SLs bench a pair 24h; 7-day realized loss limit halts all entries; exits never paused | `circuit_breakers.CircuitBreakers` (`on_trade_closed`, `can_enter`, `from_journal`; deliberately no exit API); step 3 of `Gatekeeper.evaluate` | `test_circuit_breakers.py::test_three_consecutive_stop_losses_bench_the_pair_for_24h`<br>`test_circuit_breakers.py::test_weekly_loss_halts_all_pairs_and_recovers_when_losses_age_out`<br>`test_circuit_breakers.py::test_there_is_no_exit_related_api`<br>`test_backtester.py::test_exits_are_processed_while_the_halt_is_active`<br>`test_invariants.py::test_paused_exits_are_caught` |
| **Minimum RR 2:1** | `StrategyConfig.validate` (`reward_risk >= 2.0`); `Gatekeeper.plan_fill` (target from the actual fill); discovery grid {2, 2.5, 3}; `review_sheet.auto_flags` `rr_below_min`; `adoption.RULE_VIOLATION_FLAGS` | `test_backtester.py::test_every_trade_has_rr_at_least_2_and_stop_below_structure`<br>`test_adoption.py::test_config_from_overrides_rejects_bad_or_loosening_values`<br>`test_review_sheet.py::test_cli_rejects_a_loosened_reward_risk`<br>`test_strategy_discovery.py::test_default_grid_is_twelve_legal_tightenings` |
| **Layer `L_ml_filter`**: can only remove trades; fitted on TRAIN only | `ml_filter.MLFilter` (6-feature L2 logistic, break-even threshold); step 6 of `Gatekeeper.evaluate`; `walkforward.walk_forward` fits on purged TRAIN candidates | `test_gatekeeper.py::test_entry_filter_can_only_remove_and_reports_its_probability`<br>`test_walkforward.py::test_layer_is_fit_on_purged_train_candidates_only`<br>`test_walkforward.py::test_ml_fit_is_invariant_to_test_period_candles`<br>`test_walkforward.py::test_insufficient_data_is_untested_without_fallback` |
| **Layer `L_expectancy_guard`**: off by default; only shrinks risk | `journal_rules.risk_multiplier`; `Gatekeeper.guard_multiplier` (trades closed before the decision only) | `test_journal_rules.py::test_guard_is_off_by_default`<br>`test_journal_rules.py::test_guard_triggers_on_negative_last_window_expectancy`<br>`test_gatekeeper.py::test_risk_is_min_of_cap_and_budget_times_guard` |
| Single entry path, restart-safe | `Gatekeeper.evaluate` + `plan_fill`; `CircuitBreakers.from_journal` | `test_backtester.py::test_every_signal_candle_goes_through_the_gatekeeper`<br>`test_gatekeeper.py::test_rules_are_applied_in_the_documented_order`<br>`test_gatekeeper.py::test_journal_restart_reproduces_the_backtest_decision` |
| No look-ahead | causal indicators; `find_stop` reads `candles[0..i]` only; the backtester cuts its input at `end_ts` | `test_gatekeeper.py::test_decisions_never_depend_on_later_candles`<br>`test_backtester.py::test_perturbing_the_future_never_changes_the_past`<br>`test_backtester.py::test_end_ts_is_identical_to_truncated_data`<br>`test_indicators.py::test_indicators_never_change_when_future_values_are_removed` |
| Selection on TRAIN only | `strategy_discovery.discover` (selection frozen before any TEST backtest) | `test_strategy_discovery.py::test_selection_is_recorded_before_any_test_backtest`<br>`test_strategy_discovery.py::test_selection_is_invariant_to_test_period_candles` |
| Labels and verdict | `metrics.label`, `metrics.dd_check`, `run_research.verdict_line` | `test_metrics.py::test_label_branches`<br>`test_metrics.py::test_dd_check`<br>`test_run_research.py::test_null_world_reports_no_robust_result` |
| Adoption path | `adoption.check_promotion` / `require_stage` | `test_adoption.py::test_skipping_any_stage_blocks_every_later_stage`<br>`test_adoption.py::test_testnet_of_exactly_14_days_passes_to_live`<br>`test_adoption.py::test_fingerprint_mismatch_blocks_every_stage`<br>`test_adoption.py::test_testnet_journal_is_cross_checked` |

Known test gap: `StrategyConfig.validate` also enforces several things that no test asserts yet,
because there is no `tests/test_config.py`:

- the R5 window floors (2h / 24h);
- the R9 limits (at most 3 consecutive SLs, a bench of at least 24h, a loss window of at
  least 7 days, a positive loss limit);
- BTC, ETH and BNB all being in the cluster, with BNB exclusive.

They were checked by hand: each loosening raises `ConfigError`.

## 4. How to run

Run everything from the repository root. Only the `ccxt` download needs a network.

### 4.1 Tests and lint

```bash
python -m pytest research/trendbot/tests -q      # needs pytest; research/pytest.ini is used
ruff check research/
ruff format --check research/
```

In the build sandbox the suite ran in about 90 s (629 tests).

### 4.2 Synthetic worlds and calibration (offline)

The synthetic worlds have a KNOWN ground truth. They test the *methodology* and are never
evidence about real markets:

- `null`: martingale prices, no edge.
- `planted`: a volume-momentum edge across the whole history.
- `decay`: the edge exists only before the 70% split.
- `hour_edge`: the edge exists only for spike candles closing 12:00-20:00 UTC.

```bash
# one full pipeline run (baseline, 13-variant discovery, ML layer) on one world and seed
python -m research.trendbot.run_research --synthetic planted --seed 1 \
    --out-dir research/results/synthetic_planted_s1
# the same for the other worlds
python -m research.trendbot.run_research --synthetic null --seed 1 --out-dir research/results/synthetic_null_s1
python -m research.trendbot.run_research --synthetic decay --seed 1 --out-dir research/results/synthetic_decay_s1
python -m research.trendbot.run_research --synthetic hour_edge --seed 1 --out-dir research/results/synthetic_hour_edge_s1

# calibration: label frequencies over seeds 1..N (writes CALIBRATION.md + calibration_runs.csv)
python -m research.trendbot.run_research --synthetic null --calibrate-seeds 20 --workers 4 \
    --out-dir research/results/calibration_null
python -m research.trendbot.run_research --synthetic planted --calibrate-seeds 10 --workers 4 \
    --out-dir research/results/calibration_planted
python -m research.trendbot.run_research --synthetic decay --calibrate-seeds 10 --workers 4 \
    --out-dir research/results/calibration_decay
python -m research.trendbot.run_research --synthetic hour_edge --calibrate-seeds 10 --workers 4 \
    --out-dir research/results/calibration_hour_edge

# run and independently audit one synthetic backtest
python -m research.trendbot.invariants --synthetic planted --seed 1
```

Measured on 4 CPUs: one run takes about 23 s. The calibrations with `--workers 4` took
136 s (null, 20 seeds), 69 s (planted), 80 s (decay) and 74 s (hour_edge). Each run writes
`REPORT.md` (12 sections), a TRAIN and a TEST journal per variant under `journals/`, review
packs for the baseline and the selected variant, and one `adoption_<variant>.json` per
adoptable candidate. The process exits with 0 on success, 1 if any backtest broke an
invariant, and 2 on a usage or data error.

### 4.3 Real data (on a machine with network access)

**Nothing below has been run against a live exchange.** The build sandbox has no network
and no ccxt. `fetch_data` has only been tested with a fake exchange. Treat the first real
download as untested code: read the gap report it prints, and spot-check a few candles
against the exchange's own chart.

```bash
pip install ccxt

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
`ts,open,high,low,close,volume`. `data.load_dataset` reads this layout.

**News calendar (R5).** Build `research/data/events.csv` with this header:

```text
time_utc,scope,impact,kind,note
2024-03-12T12:30:00Z,ALL,high,macro,US CPI
2024-04-15T00:00:00Z,BNB,low,bnb_burn,quarterly BNB burn
```

- `time_utc` is either ISO-8601 with an explicit offset (`Z` or `+00:00`) or integer epoch
  ms. Offset-less times are rejected.
- `scope` is `ALL`, a base asset (`BTC`, `BNB`, ...) or `EXCHANGE:<ccxt id>`.
- `impact` is `high`, `medium` or `low`.
- `kind` is one of `macro`, `regulatory`, `legal`, `unlock`, `bnb_burn`, `launchpool`,
  `other`.
- A malformed row raises an error that names the line number.

Blocking rules:

- High-impact events block the pairs in their scope for +/-2h.
- `bnb_burn` / `launchpool` events of any impact (scope `ALL`, `BNB` or `EXCHANGE:binance`)
  block BNB for +/-24h.
- `EXCHANGE:<id>` events apply only when `<id>` equals `cfg.exchange_id`, which defaults to
  `binance` and is not exposed by `run_research`. For Coinbase-wide events, use scope `ALL`.

[`events_example.csv`](events_example.csv) shows the format only. Every row says
"EXAMPLE ONLY - not a real calendar" and it covers only a few days in 2024.

> **Warning: without a historical calendar, R5 is not exercised in backtests.** The
> backtest can then enter around news that the live bot would skip, so the results are
> not news-filtered. The report says so in its provenance section. A partial calendar
> (for example the example file) makes the report say "loaded: yes" while R5 is only
> exercised on the covered dates. Source the rows from a real economic calendar
> (high-impact macro, scope `ALL`) and from Binance announcements (BNB burns and
> launchpools, scope `BNB`).

**Research run, audit, review, journal rules and adoption on the fetched data:**

```bash
python -m research.trendbot.run_research --data-dir research/data \
    --events research/data/events.csv --out-dir research/results/real_binance
python -m research.trendbot.run_research --data-dir research/data/coinbase \
    --pairs BTC/USDT ETH/USDT --events research/data/events.csv \
    --out-dir research/results/real_coinbase

# the walk-forward split printed in REPORT.md section 2 (this value is only an example)
SPLIT_ISO=2023-03-14T00:00:00Z
SPLIT_MS=$(python -c "from research.trendbot.journal import iso_to_ms; print(iso_to_ms('$SPLIT_ISO'))")

# independent re-audit of a journal (a TEST journal needs --start-ts = the split in ms;
# a TRAIN journal needs --end-ts instead)
python -m research.trendbot.invariants \
    --journal research/results/real_binance/journals/base_test.csv \
    --data-dir research/data --events research/data/events.csv --start-ts "$SPLIT_MS"

# trade-by-trade review pack for one journal (run_research already writes packs covering
# TRAIN and TEST for the baseline and the selected variant)
python -m research.trendbot.review_sheet \
    --journal research/results/real_binance/journals/base_test.csv \
    --out-dir research/results/real_binance/review_base_test --split "$SPLIT_ISO"

# every adaptation the bot would apply because of past trades (R9 benches / halt, and
# the expectancy guard if requested), each with the journal rows that caused it.
# --now defaults to the current UTC time; --expectancy-guard is optional.
python -m research.trendbot.journal_rules \
    --journal research/results/real_binance/journals/base_test.csv --equity 10000 \
    --now 2024-12-30T00:00:00Z --expectancy-guard

# adoption gate
python -m research.trendbot.adoption check \
    --record research/results/real_binance/adoption_base.json --stage HUMAN_REVIEW
```

The offline equivalents of these commands were run on committed synthetic outputs, and
they are runnable today:

```bash
python -m research.trendbot.journal_rules \
    --journal research/results/synthetic_null_s1/journals/base_test.csv \
    --equity 10000 --now 2023-06-16T12:00:00Z
#  | R9_circuit_breaker | BTC/USDT | no entries until 2023-06-17T08:00:00Z | BTC/USDT takes no
#    new entries until 2023-06-17T08:00:00Z because trades #107, #108, #109 were 3
#    consecutive stop-losses (limit 3, bench 24h from 2023-06-16T08:00:00Z). | #107, #108, #109 |
python -m research.trendbot.adoption check \
    --record research/results/synthetic_planted_s1/adoption_base.json --stage HUMAN_REVIEW  # PASS, exit 0
python -m research.trendbot.adoption check \
    --record research/results/synthetic_planted_s1/adoption_base.json --stage TESTNET       # BLOCKED, exit 1
python -m research.trendbot.adoption check \
    --record research/results/synthetic_null_s1/adoption_base.json --stage HUMAN_REVIEW     # BLOCKED: NO-EDGE
python -m research.trendbot.adoption fingerprint          # canonical config JSON + sha256
python -m research.trendbot.adoption init --variant base --out rec.json   # empty record
```

`adoption --config c.json` takes a JSON object of `StrategyConfig` overrides. The overrides
are validated, so a loosening is refused. `check` prints `PASS` or every blocking reason,
and exits with 0 or 1.

## 5. How to read results

Every results table puts **TRAIN and TEST side by side**. The split is chronological at 70%
of the common time range of all pairs. TEST starts with fresh equity and fresh circuit
breakers, and indicators warm up on earlier candles only. Fitted and selected things
(discovery selection, ML coefficients) see TRAIN only.

`metrics.label(train, test)` applies these rules in order (`min_train = min_test = 30`
closed trades; the CI is a seeded percentile bootstrap of avg R):

| order | condition | label | meaning |
|---|---|---|---|
| 1 | TRAIN n < 30 or TEST n < 30 | `UNTESTED` | too few trades to judge |
| 2 | TRAIN avg R <= 0 | `NO-EDGE` | nothing to validate |
| 3 | TEST avg R <= 0 | `TRAIN-ONLY` | the edge did not hold out of sample (likely curve-fit) |
| 4 | TEST 90% CI lower bound <= 0 | `UNTESTED` | positive, but not distinguishable from zero |
| 5 | otherwise | `ROBUST` | positive expectancy on TRAIN and TEST, TEST CI above zero |

The ML layer is also `UNTESTED` when it cannot be fitted: fewer than 100 TRAIN candidates,
or fewer than 20 wins or 20 losses. In that case no backtest runs and nothing silently
falls back to the unfiltered rules. The drawdown check (TEST max drawdown at most 20%) is
reported next to the label. `adoption.py` requires both `ROBUST` and `dd_ok` before
HUMAN_REVIEW.

The verdict considers only the three pre-registered candidates: the baseline, the
discovery variant selected on TRAIN, and the ML layer. It prints either the ROBUST
candidates or exactly **"No robust result found."** That is a valid result, and often the
correct one: the null and decay worlds produce it by design. UNTESTED means "not
demonstrated", not "no edge".

**Do not re-select on TEST.** The report shows the TEST label of all 13 discovery variants
for context. Picking one of them *because* of its TEST numbers turns TEST into TRAIN and
invalidates the result. The TRAIN column of the selected variant is biased upward (it is
the best of 12), and the ML layer's TRAIN column is in-sample. Only TEST columns are
out-of-sample. A new idea is a new variant: it starts again at BACKTEST, and its config
fingerprint changes.

## 6. Honest status

A summary of [`research/results/RESULTS_SUMMARY.md`](../results/RESULTS_SUMMARY.md).

- **No real-data result exists yet.** The sandbox has no network and no ccxt, so every
  number comes from synthetic worlds. These verify the methodology and are not evidence
  about BTC/ETH/BNB. No variant may be adopted on their strength.
- Scope: 54 pipeline runs (4 seed-1 runs plus 50 calibration seeds), each with 30
  backtests, 6 years of 4H candles and 3 pairs. The invariant audit found 0 violations.
- **Seed 1 matched ground truth in all four worlds.** null: "No robust result found."
  planted: baseline and selected variant ROBUST (baseline TEST +0.405R, n=53, 90% CI
  [+0.066, +0.746]). decay: "No robust result found." (baseline TRAIN-ONLY, +0.279R TRAIN
  vs -0.201R TEST). hour_edge: only the ML layer ROBUST (TEST +0.575R, n=42).
- **Calibration:**
  - null: 0/20 ROBUST false positives (90% Wilson CI 0-12%).
  - decay: 0/10 (0-21%).
  - planted: the verdict detected the edge in 9/10 seeds (65-98%), the baseline alone in 8/10.
  - hour_edge: the verdict found the edge in 6/10, and the ML layer's TEST expectancy beat
    the baseline's in 8/10 (mean +0.170R to +0.368R). The fitted `hour_sin` coefficient
    had the planted sign in 10/10 hour_edge seeds.
- **Weaknesses shown by the calibration:**
  - Power is limited by about 40-60 TEST trades. A real +0.4R edge was labelled UNTESTED
    in 2 of 10 planted seeds.
  - The ML layer costs power when there is nothing extra to learn. In null it was worse
    than the baseline on TEST (-0.166R vs -0.037R) while its in-sample TRAIN numbers
    looked good (seed 1: +0.430R TRAIN vs -0.246R TEST).
  - Taking three looks at TEST inflates the false-positive rate by up to about 3x.
  - 20 null seeds only bound the false-positive rate below about 12%.
  - The planted effect is deliberately strong. **On real data, expect UNTESTED far more
    often than ROBUST.**

## 7. Model-complexity policy

- **Now: L2 logistic regression on 6 features** (`rsi`, `vol_ratio`, `ema_gap_pct`,
  `dist_regime_pct`, `hour_sin`, `hour_cos`). The penalty is fixed (`l2=1`), the threshold
  is the break-even probability, and nothing is tuned. It is fitted by Newton/IRLS in pure
  Python, on a few hundred purged TRAIN candidates (268 in planted seed 1).
- **LightGBM, CatBoost, other gradient boosting and neural networks are not used.** They
  become admissible only when BOTH conditions hold:
  1. there is materially more labelled history than today's few hundred TRAIN candidates;
  2. the simple logistic layer's walk-forward result is already positive (ROBUST, drawdown
     check passed) on real data.

  A flexible model on a small sample memorises noise. The calibration shows that even the
  6-feature logistic layer's in-sample TRAIN numbers overstate TEST in a world with no edge.

## 8. Graduation to freqtrade and FreqAI

**freqtrade (the execution host).** This repository is freqtrade, which already provides
exchange connectivity, order handling, persistence and a dry-run mode. This package
deliberately has none of that. Graduate a variant into a freqtrade strategy only once its
adoption record has a REAL-data ROBUST walk-forward and a signed-off HUMAN_REVIEW, and do
it *before* the TESTNET stage, so that the 2-week testnet run exercises the executor that
will trade live. Constraints on the port:

- It must call `Gatekeeper.evaluate` / `plan_fill` for entries. Re-expressing R1-R9
  inside freqtrade's dataframe logic would give two code paths that can diverge.
- It must write `journal.write_journal`-format journals, so that `invariants`,
  `journal_rules` and the TESTNET journal cross-check in `adoption.py` can audit it.
- freqtrade's dry-run is a simulated local wallet (`docs/configuration.md`). The TESTNET
  stage requires real orders on `binance-testnet`, so a dry-run does not replace it.
- Re-tuning the variant with freqtrade hyperopt is a new search, and so a new variant. Its
  config fingerprint changes and it starts again at BACKTEST.

Neither the port nor a journal exporter exists yet.

**FreqAI (the model host).** Move to FreqAI only when the model-complexity policy above
allows a more flexible model. FreqAI's value is sliding-window retraining
(`train_period_days`, `backtest_period_days`, `live_retrain_hours`) with model families
such as LightGBM. That is only worth having with enough labelled history per window, and
each retrain is another fit whose out-of-sample result must be measured. Two things are
required:

1. Keep FreqAI's `shuffle` data-split parameter at its documented default `False`, which
   preserves chronological order.
2. Keep `continual_learning` off. The maintainers' own warning, in
   [`docs/freqai-running.md`](../../docs/freqai-running.md) lines 141-142:

   > ???+ danger "Experimental functionality"
   >
   > Beware that this is currently a naive approach to incremental learning, and it has a
   > high probability of overfitting/getting stuck in local minima while the market moves
   > away from your model. We have the mechanics available in FreqAI primarily for
   > experimental purposes and so that it is ready for more mature approaches to continual
   > learning in chaotic systems like the crypto market.

Also, the adoption fingerprint covers `StrategyConfig` only. Fitted model parameters are
not pinned, which is why even the current `base+ml` layer gets no adoption record. Before
any model (logistic or FreqAI) can be adopted, the adoption record must be extended to pin
the model artefact.

## 9. Adoption path (always)

`adoption.py` enforces this order, with no shortcuts. A good backtest number never skips a
step, and the config promoted must be exactly the config tested (sha256 of the canonical
config JSON):

```text
BACKTEST -> WALK_FORWARD -> HUMAN_REVIEW -> TESTNET (>= 14 days) -> LIVE
```

1. **Backtest.** `run_research` records the report path and completion time.
2. **Walk-forward (TRAIN/TEST).** Requires label exactly `ROBUST`, `dd_ok` true, positive
   TEST avg R, and positive TRAIN and TEST counts. `run_research` writes these fields.
3. **Human review of the trade-by-trade list.** A named reviewer inspects every row of the
   review pack (`trades_review.md` / `.csv`, TRAIN and TEST, with machine flags and empty
   `reviewer_ok` / `reviewer_note` columns) and fills `human_review`. The record must show
   `trades_reviewed == trades_total == train_n + test_n` and `approved: true`, dated no
   earlier than the backtest. An unflagged trade is not an approved trade.
4. **At least 2 weeks on the Binance spot testnet** (testnet.binance.vision). Requirements:
   - `exchange: "binance-testnet"`;
   - `end_utc - start_utc >= 14 days`, starting no earlier than the review sign-off;
   - at least 1 trade, and `rule_violations: 0`;
   - a testnet journal whose trade count, entry window and variant match the record, and
     in which no trade carries a mandatory-rule flag (R7 size/cap, 2:1, stop and target
     levels, look-ahead).

   With ccxt the testnet is selected like this (not run here: no network):

   ```python
   import os
   import ccxt

   exchange = ccxt.binance({"apiKey": os.environ["BINANCE_TESTNET_API_KEY"],
                            "secret": os.environ["BINANCE_TESTNET_SECRET"]})
   exchange.set_sandbox_mode(True)  # route requests to the Binance spot testnet
   ```

   Keep keys in the environment, never in code, configs or journals.
5. **Only then the live config.** Test-only configs (R4 off) never go LIVE, and
   `live.enabled_utc` may not precede the end of the testnet run. The live bot calls
   `adoption.require_stage(record, "LIVE", cfg, now_ms, base_dir)` at startup and refuses
   to trade if it raises.

## 10. Verification notes

Every offline command in this file was run in the build sandbox (Python 3.11.15, 4 CPUs,
no network):

- The seed-1 and calibration commands were run with the same arguments but a scratch
  `--out-dir`, so the committed outputs were not overwritten. They reproduced the committed
  `REPORT.md`, journals, `CALIBRATION.md` and `calibration_runs.csv` exactly, apart from
  the out-dir path line and the run timestamp.
- The `--data-dir` path (with and without `--events`, 3 pairs and the Coinbase-style 2
  pairs) was run on a synthetic world exported to candle and event CSVs. The 3-pair run
  with events reproduced sections 3-6 of the committed `synthetic_planted_s1` report
  exactly. Without `--events`, the report flagged that R5 was not exercised.
- `invariants --journal` was run on the TRAIN and TEST journals of that run: 0 violations.
- The two `fetch_data` command lines were run against an in-memory fake `ccxt` module and
  both exited 0. The Coinbase path printed "aggregating 2h". Adding BNB/USDT to the
  Coinbase command makes it skip BNB as not listed and exit 1. Without ccxt, both
  commands exit 2 with the install hint.
- The live-bot snippet's calls were executed against synthetic candles.

## 11. Where the outputs live

- `research/results/<run>/REPORT.md`: the 12-section report. It starts with the win-rate
  statement, then data provenance, TRAIN | TEST tables, verdict, rule-denial counts,
  invariant audit, journal rules, review packs and adoption stage.
- `research/results/<run>/journals/*.csv`: one journal per variant and window.
- `research/results/<run>/review_*/trades_review.{md,csv}`: the human review packs.
- `research/results/<run>/adoption_<variant>.json`: the adoption record.
- `research/results/RESULTS_SUMMARY.md`: cross-run summary and the honest list of
  weaknesses.

## 12. Disclaimer

This is a research and testing tool, not financial advice and not a recommendation to trade.
Backtests and synthetic worlds are simplified models: past or simulated results do not
predict future results, and no result in this repository comes from real market data.
Crypto trading can result in the total loss of the capital used.
