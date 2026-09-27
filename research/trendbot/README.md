# trendbot: research, validation and adoption layer for the 4H trend bot

Research and testing tool only. Not financial advice. Crypto trading can result in the total
loss of the capital used. See [Disclaimer](#12-disclaimer).

The binding interface spec is [`CONTRACT.md`](CONTRACT.md) (v1 plus the v2 amendments A1-A4
at its end). This README describes the code as it is after v2.

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
  walk-forward labels use average R and its bootstrap CI (`metrics.label`). Reports show
  win rate as context only, and the human review pack (`review_sheet`) leaves it out.

**Target metric: positive expectancy.** That is the average R per closed trade, net of fees
and slippage (defaults: 0.10% of notional per side, 0.05% slippage on market fills). It must
survive a 70/30 chronological walk-forward (label `ROBUST`) with a controlled TEST max
drawdown (at most 20%: `metrics.dd_check`, `walkforward.DEFAULT_MAX_DD_PCT`).

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
fill with no slippage) wins exactly `reward_risk` R after both fees. Only a stop that gaps through
(the exit candle opens below the stop and fills at `open * (1 - s)`) can lose more than 1R.
Because costs are in `L_u`, the price ratio `(T - E) / (E - S)` is always above
`reward_risk`.

Worked example with the default costs (equity 10,000, BTC at its 1% cap): fill `E = 100`,
stop `S = 96.50` (3.5% below, close to the median stop distance of the committed runs). Then
`S_x = 96.45175`, `L_u = 3.74470`, `qty = 26.704` and `T = 107.697`, which is 2.199:1 in
price. A clean stop loses 100.00 (-1R) and a take-profit wins 200.00 (+2R). Sizing on the
price distance alone would have bought 28.571 units, and the same stop would have lost about
107 (1.07% of equity).

Where it lives: `sizing.loss_per_unit`, `sizing.cost_aware_target` and `sizing.size_for_pair`,
called by `Gatekeeper.plan_fill` on the ACTUAL fill. `invariants` re-derives, from the
trade row and the config alone, that net RR >= `reward_risk`, price RR >= `reward_risk`,
`risk_amount == qty * L_u`, a clean SL is -1R and a TP is +`reward_risk` R (tolerance
1e-9). The review pack flags a trade whose RR is below the configured value by price OR
net of costs, and on testnet that flag counts as a rule violation. In the 24 committed
seed-1 journals, all 587 take-profits are +`reward_risk` R and all 965 stop-losses are -1R,
each within 1e-13 (none gapped: synthetic candles open at the previous close). The other
11 trades were force-closed at the end of a window.

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
  seed is fixed. Re-running the committed synthetic commands (with the same `--now`)
  reproduces every committed journal, review pack, adoption record, config file, model file,
  `CALIBRATION.md` and `calibration_runs.csv` byte for byte. Each `REPORT.md` differs only in
  the 4 lines that print the out-dir path inside a command (checked in this cycle, see
  section 10).
- **Auditable.** Every denied signal candle is logged with its rule id
  (`config.RULE_IDS`) and a one-sentence reason. `invariants.check_invariants` re-derives
  every rule from the trade list and the candles. It does not reuse the gatekeeper's,
  breakers' or news calendar's code; only `compute_features` is shared.
- **Not included.** Live order execution, exchange account handling and a testnet runner.
  The live bot is responsible for these and must use the gatekeeper below.

| module | role |
|---|---|
| `models.py`, `config.py` | shared types; validated config (mandate floors and ceilings, harness cost floors) |
| `indicators.py`, `signals.py`, `structure.py` | EMA / Wilder RSI / previous-20 volume mean; R1-R4 gates; R8 stop |
| `sizing.py`, `correlation.py`, `news.py` | R7 cost-aware sizing and target; R6 shared cluster budget; R5 calendar |
| `circuit_breakers.py`, `journal.py`, `journal_rules.py` | R9; CSV trade journal; "learn from past trades" as explicit rules |
| `gatekeeper.py` | **the single entry-decision path** (backtest and live) |
| `backtester.py`, `invariants.py` | event-driven backtest; independent rule auditor (library and CLI) |
| `data.py`, `fetch_data.py`, `synthetic.py` | candle CSVs; ccxt download; synthetic worlds with known truth |
| `metrics.py`, `walkforward.py`, `strategy_discovery.py`, `ml_filter.py` | stats and labels; 70/30 walk-forward; TRAIN-only selection; logistic filter with a model fingerprint |
| `run_research.py`, `review_sheet.py`, `adoption.py` | one-command report; human review pack; adoption-path gate |

### 2.1 Harness cost floors and bounds (CONTRACT.md v2 A4)

On top of the mandate, `StrategyConfig` refuses unrealistic research settings, so no
backtest in this package can run cost-free:

- `fee_rate >= 0.0005` per side (`MIN_FEE_RATE`) and `slippage_pct >= 0.01` (`MIN_SLIPPAGE_PCT`);
- a 7-day realized loss limit in `(0, 10]` percent (`MAX_WEEKLY_LOSS_LIMIT_PCT`);
- `0.05 <= min_stop_distance_pct < max_stop_distance_pct <= 25` (`STOP_DISTANCE_BOUNDS_PCT`);
- no leverage.

`run_research` adds CLI ceilings that catch a percent typed where a fraction is expected:
`--fee-rate` at most 0.02 (so `0.6` is refused; `0.006` means 0.6%) and `--slippage-pct` at
most 5. `--exchange-id` must look like a ccxt id. A refused value exits 2 with the reason:

```bash
python -m research.trendbot.run_research --synthetic null --fee-rate 0 --out-dir /tmp/x
# error: fee_rate must be >= 0.0005 per side (realistic costs)                     [exit 2]
python -m research.trendbot.run_research --synthetic null --fee-rate 0.6 --out-dir /tmp/x
# error: --fee-rate 0.6 is not a plausible fraction of notional per side (at most 0.02; 0.006 means 0.6%)
```

`tests/test_config.py` (orchestrator-owned, new in v2) asserts each config refusal above,
together with every mandate floor and ceiling, including the R5 windows, the R9 limits and
the R6 cluster membership that the previous README listed as untested.
`test_run_research.py::test_cli_usage_errors` covers
the CLI ceilings (see section 3).

### 2.2 The gatekeeper is the single entry-decision path

The backtester calls `Gatekeeper.evaluate` for every signal candle and `Gatekeeper.plan_fill`
for every fill (`test_backtester.py::test_every_signal_candle_goes_through_the_gatekeeper`).
A live bot must call the same two methods, so a backtest and the bot cannot diverge on a
mandatory rule. Order inside `evaluate` (the first failure wins and names its rule):

```text
R1-R4 signals.check_entry -> R5 NewsCalendar.check -> R9 CircuitBreakers.can_enter
-> R6 CorrelationGuard.check -> R8 structure.find_stop -> L_ml_filter (if given)
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

What the live bot calls on **each closed 4H candle**. The block below was executed as
written in this cycle (section 10), offline against synthetic candles, with the exchange
I/O (`closed_4h_candles`, the order calls, `record_denial`) replaced by stubs. The other
names it uses without defining (`trade`, `equity_after_close`, `pair`, `open_risk`,
`equity`, `free_cash`, `last_price`, `market_price`, `fill_price`) are the bot's own state.

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
dec = gk.evaluate(pair, candles, rows, len(candles) - 1, open_risk, equity, closed, None)
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
- An ML variant also passes `model_fingerprint=` (the running model's
  `MLFilter.fingerprint()`) to `require_stage`, and its `MLFilter.entry_filter()` as the
  last argument of `evaluate`. The package has no loader that rebuilds an `MLFilter` from
  `model_base_plus_ml.json`, so a live ML variant needs one first (see section 8).

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
benches and the halt with the backtest convention.

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

## 3. Rule-to-code-to-test map

Each test named here exists and passed in this cycle. The 101 test functions were run with
`pytest -k` (254 cases once parametrised tests are expanded; see section 10). The file
prefix is `research/trendbot/tests/`.

| rule | enforced by | proven by |
|---|---|---|
| **R1** trend: close > EMA9, close > EMA21, EMA9 > EMA21, 4H | `signals.check_entry` (trend gate) on `indicators.compute_features`; `StrategyConfig.validate` fixes EMA 9/21/200; `data.load_dataset` refuses files that are not 4H apart | `test_signals.py::test_trend_gate_fails`<br>`test_signals.py::test_trend_detail_lists_every_broken_condition`<br>`test_signals.py::test_config_refuses_loosened_entry_gates`<br>`test_config.py::test_loosening_is_rejected`<br>`test_data.py::test_load_dataset_rejects_wrong_timeframe_and_empty_files` |
| **R2** RSI(14) in [50, 70] | `signals.check_entry` (momentum gate, both ends inclusive); `indicators.rsi_wilder`; `StrategyConfig.validate` (period 14, window inside [50, 70]) | `test_signals.py::test_momentum_bounds_inclusive`<br>`test_signals.py::test_momentum_fails_outside_window`<br>`test_indicators.py::test_rsi_matches_published_wilder_worksheet`<br>`test_config.py::test_loosening_is_rejected` |
| **R3** volume >= 1.5x previous-20 average | `signals.check_entry` (volume gate); `indicators.prev_mean` (current candle excluded); `StrategyConfig.validate` (multiple >= 1.5, lookback 20) | `test_signals.py::test_volume_exactly_at_multiple_passes`<br>`test_signals.py::test_volume_below_multiple_fails`<br>`test_indicators.py::test_prev_mean_excludes_current_value`<br>`test_config.py::test_loosening_is_rejected` |
| **R4** close > EMA200 unless explicitly tested off | `signals.check_entry` (regime gate); `StrategyConfig.is_test_only`; `strategy_discovery.select_on_train` never selects it; `adoption.check_promotion` blocks LIVE | `test_signals.py::test_regime_close_not_above_ema200_fails`<br>`test_signals.py::test_regime_disabled_is_an_explicit_pass`<br>`test_config.py::test_regime_off_is_flagged_test_only`<br>`test_strategy_discovery.py::test_select_on_train_rules`<br>`test_adoption.py::test_test_only_config_may_reach_testnet_but_never_live` |
| **R5** no entries +/-2h of high-impact news; BNB +/-24h around burns / launchpools | `news.NewsCalendar.check` (bisect over sorted events, both bounds inclusive), step 2 of `Gatekeeper.evaluate`; `StrategyConfig.validate` (windows may widen, never narrow) | `test_news.py::test_high_impact_all_boundaries_are_inclusive`<br>`test_news.py::test_bnb_events_block_bnb_for_24h_any_impact`<br>`test_news.py::test_bnb_burn_does_not_block_btc_or_eth`<br>`test_news.py::test_exchange_scope_matches_configured_exchange_only`<br>`test_config.py::test_loosening_is_rejected`<br>`test_backtester.py::test_news_blackout_blocks_entries_end_to_end`<br>`test_invariants.py::test_disabled_news_rule_is_caught` |
| **R6** BTC/ETH/BNB share one risk budget, never full size on more than one, BNB never stacks | `correlation.CorrelationGuard.check` (no pyramiding, BNB exclusive, `allowed = min(requested, remaining)`); `StrategyConfig.validate` (cluster budget <= largest single-pair cap, all three in the cluster, BNB exclusive) | `test_correlation.py::test_btc_open_at_full_size_denies_eth`<br>`test_correlation.py::test_bnb_open_denies_btc_and_eth`<br>`test_correlation.py::test_btc_or_eth_open_denies_bnb_even_with_budget_left`<br>`test_correlation.py::test_two_full_size_positions_are_impossible_by_config`<br>`test_config.py::test_loosening_is_rejected`<br>`test_backtester.py::test_bnb_never_stacks_with_another_cluster_position`<br>`test_invariants.py::test_disabled_correlation_cap_is_caught` |
| **R7** risk <= 1% BTC/ETH, <= 0.5% BNB, ALL-IN (fees + slippage); size derived from the stop distance | `sizing.size_for_pair` / `size_position` (`qty = equity * risk% / L_u`; notional and free-cash caps only shrink), called by `Gatekeeper.plan_fill` on the actual fill; `StrategyConfig.validate` (caps) | `test_sizing.py::test_basic_size_is_cost_aware_hand_arithmetic`<br>`test_sizing.py::test_stop_fill_with_both_fees_loses_exactly_risk_pct`<br>`test_sizing.py::test_wider_stop_means_smaller_position_same_all_in_risk`<br>`test_sizing.py::test_size_for_pair_enforces_the_pair_cap`<br>`test_config.py::test_pair_risk_caps`<br>`test_gatekeeper.py::test_plan_fill_applies_the_a1_cost_aware_arithmetic_by_hand`<br>`test_backtester.py::test_risk_caps_and_size_derived_from_the_stop`<br>`test_invariants.py::test_price_only_sizing_is_caught` |
| **R8** stop behind the swing low with a buffer (BNB 0.5-0.8%) | `structure.find_stop` / `latest_confirmed_pivot` (a pivot is usable only k candles after it; fallback = lowest low of the last N); `Gatekeeper.plan_fill` (gap-through refusal, distance re-check from the fill); `StrategyConfig.validate` (BNB buffer range, buffer > 0, stop-distance bounds) | `test_structure.py::test_confirmed_pivot_with_btc_buffer`<br>`test_structure.py::test_unconfirmed_pivot_is_not_used`<br>`test_structure.py::test_find_stop_never_reads_candles_after_i`<br>`test_structure.py::test_config_keeps_the_bnb_buffer_in_the_mandated_range`<br>`test_config.py::test_bnb_buffer_range`<br>`test_backtester.py::test_entry_that_gaps_through_the_stop_is_skipped`<br>`test_invariants.py::test_stop_not_behind_structure_is_caught` |
| **R9** 3 consecutive SLs bench a pair 24h; 7-day realized loss limit halts all entries; exits never paused | `circuit_breakers.CircuitBreakers` (`on_trade_closed`, `can_enter`, `from_journal`; deliberately no exit API); step 3 of `Gatekeeper.evaluate`; `StrategyConfig.validate` (limit <= 3, bench >= 24h, window >= 7 days, loss limit in (0, 10]%) | `test_circuit_breakers.py::test_three_consecutive_stop_losses_bench_the_pair_for_24h`<br>`test_circuit_breakers.py::test_weekly_loss_halts_all_pairs_and_recovers_when_losses_age_out`<br>`test_circuit_breakers.py::test_there_is_no_exit_related_api`<br>`test_config.py::test_loosening_is_rejected`<br>`test_backtester.py::test_exits_are_processed_while_the_halt_is_active`<br>`test_invariants.py::test_paused_exits_are_caught` |
| **R9 timing** (A2): exits count from `exit_ts + 4h` in backtests, real fill time live | `CircuitBreakers(cfg, exit_time_uncertainty_ms)`; `Gatekeeper` (one shared offset); `journal_rules --backtest-journal`; `invariants` bench-duration check | `test_circuit_breakers.py::test_backtest_convention_bench_lasts_24h_of_real_time_from_the_candle_close`<br>`test_circuit_breakers.py::test_backtest_convention_loss_window_counts_from_the_candle_close`<br>`test_gatekeeper.py::test_breakers_and_guard_share_one_exit_offset`<br>`test_backtester.py::test_bench_lasts_24h_from_the_exit_candle_close`<br>`test_journal_rules.py::test_cli_backtest_journal_flag`<br>`test_invariants.py::test_bench_counted_from_the_exit_candle_open_is_caught` |
| **Minimum RR 2:1, net of costs** (A1) | `StrategyConfig.validate` (`reward_risk >= 2.0`); `sizing.cost_aware_target` via `Gatekeeper.plan_fill` (target from the actual fill); discovery grid {2, 2.5, 3}; `review_sheet.auto_flags` `rr_below_min` (price or net); `adoption.RULE_VIOLATION_FLAGS` | `test_sizing.py::test_cost_aware_target_nets_exactly_reward_risk`<br>`test_backtester.py::test_every_trade_has_rr_at_least_2_and_stop_below_structure`<br>`test_backtester.py::test_outcomes_in_r_are_exact_on_a_synthetic_run`<br>`test_invariants.py::test_price_only_target_is_caught`<br>`test_review_sheet.py::test_rr_is_checked_by_price_and_net_of_costs`<br>`test_review_sheet.py::test_cli_rejects_a_loosened_reward_risk`<br>`test_strategy_discovery.py::test_default_grid_is_twelve_legal_tightenings`<br>`test_config.py::test_loosening_is_rejected` |
| **Cost floors and bounds** (A4); costs reach every trade and every report | `StrategyConfig._harness_errors`; `run_research.config_from_args` (CLI ceilings); `run_research.cost_lines` (report section 2, Coinbase warning) | `test_config.py::test_loosening_is_rejected`<br>`test_config.py::test_tightening_is_allowed`<br>`test_sizing.py::test_zero_cost_configs_are_rejected_so_sizing_always_carries_costs`<br>`test_run_research.py::test_cli_usage_errors`<br>`test_run_research.py::test_cost_flags_default_to_the_config_defaults`<br>`test_run_research.py::test_provenance_prints_default_costs`<br>`test_run_research.py::test_cost_lines_warn_when_a_non_binance_run_keeps_the_binance_fee`<br>`test_run_research.py::test_data_dir_without_events_flags_r5_and_costs_reach_every_trade` |
| **Layer `L_ml_filter`**: can only remove trades; fitted on TRAIN only | `ml_filter.MLFilter` (6-feature L2 logistic, break-even threshold); step 6 of `Gatekeeper.evaluate`; `walkforward.walk_forward` fits on purged TRAIN candidates | `test_gatekeeper.py::test_entry_filter_can_only_remove_and_reports_its_probability`<br>`test_walkforward.py::test_layer_is_fit_on_purged_train_candidates_only`<br>`test_walkforward.py::test_ml_fit_is_invariant_to_test_period_candles`<br>`test_walkforward.py::test_insufficient_data_is_untested_without_fallback` |
| **ML model fingerprint** (A3): the model promoted is the model tested | `MLFilter.fingerprint` (sha256 of the canonical model JSON); `AdoptionRecord.model_fingerprint`; `adoption.check_promotion(..., model_fingerprint)` (`ADOPT_fingerprint`); `run_research` writes the ML record and `model_base_plus_ml.json` | `test_ml_filter.py::test_fingerprint_is_a_deterministic_sha256_of_the_canonical_model`<br>`test_ml_filter.py::test_fingerprint_changes_whenever_the_model_changes`<br>`test_adoption.py::test_model_fingerprint_mismatch_blocks_every_stage`<br>`test_adoption.py::test_recorded_model_but_none_supplied_blocks_every_stage`<br>`test_adoption.py::test_ml_variant_without_recorded_model_fingerprint_blocks`<br>`test_run_research.py::test_ml_adoption_record_pins_the_fitted_model` |
| **Layer `L_expectancy_guard`**: off by default; only shrinks risk | `journal_rules.risk_multiplier`; `Gatekeeper.guard_multiplier` (only trades whose exit is certain at the decision) | `test_journal_rules.py::test_guard_is_off_by_default`<br>`test_journal_rules.py::test_guard_triggers_on_negative_last_window_expectancy`<br>`test_journal_rules.py::test_guard_backtest_journal_uses_only_certain_exits`<br>`test_gatekeeper.py::test_risk_is_min_of_cap_and_budget_times_guard` |
| Single entry path, restart-safe | `Gatekeeper.evaluate` + `plan_fill`; `CircuitBreakers.from_journal` | `test_backtester.py::test_every_signal_candle_goes_through_the_gatekeeper`<br>`test_gatekeeper.py::test_rules_are_applied_in_the_documented_order`<br>`test_gatekeeper.py::test_journal_restart_reproduces_the_backtest_decision` |
| No look-ahead | causal indicators; `find_stop` reads `candles[0..i]` only; the backtester cuts its input at `end_ts` | `test_gatekeeper.py::test_decisions_never_depend_on_later_candles`<br>`test_backtester.py::test_perturbing_the_future_never_changes_the_past`<br>`test_backtester.py::test_end_ts_is_identical_to_truncated_data`<br>`test_indicators.py::test_indicators_never_change_when_future_values_are_removed` |
| Selection on TRAIN only | `strategy_discovery.discover` (selection frozen before any TEST backtest) | `test_strategy_discovery.py::test_selection_is_recorded_before_any_test_backtest`<br>`test_strategy_discovery.py::test_selection_is_invariant_to_test_period_candles` |
| Labels and verdict | `metrics.label`, `metrics.dd_check`, `run_research.verdict_line` | `test_metrics.py::test_label_branches`<br>`test_metrics.py::test_dd_check`<br>`test_run_research.py::test_null_world_reports_no_robust_result` |
| Adoption path | `adoption.check_promotion` / `require_stage` | `test_adoption.py::test_skipping_any_stage_blocks_every_later_stage`<br>`test_adoption.py::test_testnet_of_exactly_14_days_passes_to_live`<br>`test_adoption.py::test_fingerprint_mismatch_blocks_every_stage`<br>`test_adoption.py::test_testnet_journal_is_cross_checked` |
| Independent audit (library and CLI) | `invariants.check_invariants`; `python -m research.trendbot.invariants` | `test_invariants.py::test_full_synthetic_runs_are_clean`<br>`test_invariants.py::test_cli_audits_a_journal_and_flags_a_tampered_one`<br>`test_invariants.py::test_cli_runs_and_audits_a_synthetic_world` |

## 4. How to run

Run everything from the repository root. Only the `ccxt` download needs a network.

### 4.1 Tests and lint

```bash
python -m pytest research/trendbot/tests -q      # needs pytest; research/pytest.ini is used
ruff check research/
ruff format --check research/
```

In the build sandbox (a venv with pytest and ruff, 4 CPUs) the suite ran 806 tests in 92-93 s
(two runs in this cycle).

### 4.2 Synthetic worlds and calibration (offline)

The synthetic worlds have a KNOWN ground truth. They test the *methodology* and are never
evidence about real markets. The generative model, as it is in `synthetic.py` now (every
parameter was picked by hand to look roughly crypto-like; none was estimated from market
data):

- 6 years of 4H candles from 2019-01-01T00:00Z (13,140 per pair). Each candle opens at the
  previous close, so there are no price gaps.
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
- Worlds: `null` (no trigger ever qualifies); `planted` (every up-closing spike, over the
  whole history); `decay` (triggers only before the 70% split; after it the world is
  exactly `null`); `hour_edge` (only spikes whose candle CLOSES 12:00-20:00 UTC).
- Synthetic news with no price impact, to exercise R5: 1-3 high-impact macro events (scope
  `ALL`) per month, one high-impact regulatory event (`EXCHANGE:binance`) per quarter, one
  BNB burn per quarter, one BNB launchpool per month, one ETH unlock per year.
- All four worlds share the same noise for a given seed; only the planted drift differs.

The strength was recalibrated for the v2 cost-aware R: on the full 6-year history (default
config, seeds 1-5) the baseline averaged -0.10R in `null`, +0.42R in `planted`, +0.26R in
`decay` and +0.15R in `hour_edge`. In `hour_edge`, its trades whose signal candle closes at
12-20 UTC averaged +0.53R and the others -0.19R. These numbers were re-measured in this
cycle (section 10).

```bash
# one full pipeline run (baseline, 13-variant discovery, ML layer) on one world and seed;
# --now fixes the time stamped on the adoption records, so a re-run is byte-identical
python -m research.trendbot.run_research --synthetic planted --seed 1 \
    --now 2026-09-27T12:00:00Z --out-dir research/results/synthetic_planted_s1
# the same for the other worlds
python -m research.trendbot.run_research --synthetic null --seed 1 \
    --now 2026-09-27T12:00:00Z --out-dir research/results/synthetic_null_s1
python -m research.trendbot.run_research --synthetic decay --seed 1 \
    --now 2026-09-27T12:00:00Z --out-dir research/results/synthetic_decay_s1
python -m research.trendbot.run_research --synthetic hour_edge --seed 1 \
    --now 2026-09-27T12:00:00Z --out-dir research/results/synthetic_hour_edge_s1

# calibration: label frequencies over seeds 1..N (writes CALIBRATION.md + calibration_runs.csv)
python -m research.trendbot.run_research --synthetic null --calibrate-seeds 20 --workers 4 \
    --out-dir research/results/calibration_null
python -m research.trendbot.run_research --synthetic planted --calibrate-seeds 10 --workers 4 \
    --out-dir research/results/calibration_planted
python -m research.trendbot.run_research --synthetic decay --calibrate-seeds 10 --workers 4 \
    --out-dir research/results/calibration_decay
python -m research.trendbot.run_research --synthetic hour_edge --calibrate-seeds 10 --workers 4 \
    --out-dir research/results/calibration_hour_edge

# cost sensitivity (not committed): the planted world at a 0.6%-per-side fee
python -m research.trendbot.run_research --synthetic planted --seed 1 \
    --fee-rate 0.006 --exchange-id coinbase --out-dir /tmp/planted_fee0006

# run and independently audit one synthetic backtest (default config, full history)
python -m research.trendbot.invariants --synthetic planted --seed 1
```

Measured in this cycle on 4 CPUs: one seed-1 run took 27-28 s (four in parallel). The
calibrations with `--workers 4` took 129 s (null, 20 seeds), 76 s (planted), 77 s (decay)
and 76 s (hour_edge).

Each run writes `REPORT.md` (12 sections), a TRAIN and a TEST journal for each of the three
pre-registered candidates under `journals/`, and a review pack per candidate. It writes one
`adoption_<variant>.json` per adoptable candidate: the baseline, the selected variant if it
is not the baseline, and the ML layer if it was fitted. It also writes
`model_base_plus_ml.json` when the ML layer was fitted, and `config_<variant>.json` for every
non-default config (other costs, or a discovery variant). The process exits with 0 on
success, 1 if any backtest broke an invariant, and 2 on a usage or data error.

### 4.3 Real data (on a machine with network access)

**Nothing below has been run against a live exchange.** The build sandbox has no network
and no ccxt. Install it first with `pip install ccxt` on a networked machine. `fetch_data`
has only been run against a fake exchange (section 10). Treat the first real download as
untested code: read the gap report it prints, and spot-check a few candles against the
exchange's own chart.

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
- `EXCHANGE:<id>` events block every pair, but only when `<id>` equals `cfg.exchange_id`.
  `run_research --exchange-id` sets it (default `binance`), so a Coinbase run with
  `--exchange-id coinbase` honours `EXCHANGE:coinbase` rows.

[`events_example.csv`](events_example.csv) shows the format only. Every row says
"EXAMPLE ONLY - not a real calendar" and it covers only a few days in 2024.

> **Warning: without a historical calendar, R5 is not exercised in backtests.** The
> backtest can then enter around news that the live bot would skip, so the results are
> not news-filtered. The report says so in its provenance section. A partial calendar
> (for example the example file) makes the report say "loaded: yes" while R5 is only
> exercised on the covered dates. Source the rows from a real economic calendar
> (high-impact macro, scope `ALL`) and from Binance announcements (BNB burns and
> launchpools, scope `BNB`).

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
> in 4.2) cut the baseline from ROBUST (TRAIN +0.296R, TEST +0.412R) to UNTESTED (TRAIN
> +0.127R, TEST +0.043R, n=46). Fees alone then cost 0.25R per trade, and the verdict
> became "No robust result found."

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

# independent re-audit of the BASELINE journal (a TEST journal needs --start-ts = the
# split in ms; a TRAIN journal needs --end-ts instead)
python -m research.trendbot.invariants \
    --journal research/results/real_binance/journals/base_test.csv \
    --data-dir research/data --events research/data/events.csv --start-ts "$SPLIT_MS"

# trade-by-trade review pack for one journal (run_research already writes packs covering
# TRAIN and TEST for the three candidates); a non-default run passes its costs
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
# non-default config (other costs, a discovery variant) needs --config, and the ML record
# needs the model fingerprint printed in REPORT.md section 5.
python -m research.trendbot.adoption check \
    --record research/results/real_binance/adoption_base.json --stage HUMAN_REVIEW
python -m research.trendbot.adoption check \
    --record research/results/real_coinbase/adoption_base.json --stage HUMAN_REVIEW \
    --config research/results/real_coinbase/config_base.json
```

**The invariants CLI.** `python -m research.trendbot.invariants` has two modes, and it exits
1 if it finds any violation:

- `--journal J --data-dir D [--events E] [--start-ts MS] [--end-ts MS]` audits a
  backtester journal against the candles it traded on. The journal must hold every trade
  of the run, because the breakers and the correlation cap are recomputed from it.
- `--synthetic WORLD --seed N [--years Y]` runs a synthetic backtest and audits it.

Both modes use the DEFAULT `StrategyConfig` and the backtest exit-time convention of
section 2.3. The CLI has no cost or config flags. For a run with non-default `--fee-rate` /
`--slippage-pct`, or for a discovery variant's journal, it reports sizing, fee or RR
mismatches that are not real. In this cycle it reported 108 violations on the 36 clean
trades of the scratch 0.6%-fee Coinbase run's baseline TEST journal, and 16 on the scratch
Binance run's `rr3_vol2_rsi50-70` TEST journal (its take-profits make +3R, not +2R). Both
runs' in-process audits had found 0. Use the in-process audit in REPORT.md section 8 for
those: it ran with each backtest's own config. `review_sheet` does take `--reward-risk`,
`--fee-rate` and `--slippage-pct`. Without them the same Coinbase journal had 36 of 36
trades flagged, and with them 0.

The offline equivalents of these commands were run on committed synthetic outputs, and
they are runnable today:

```bash
python -m research.trendbot.journal_rules \
    --journal research/results/synthetic_planted_s1/journals/base_test.csv \
    --equity 12030.61 --now 2024-12-30T00:00:00Z --backtest-journal   # No active adaptations.
python -m research.trendbot.adoption check \
    --record research/results/synthetic_planted_s1/adoption_base.json --stage HUMAN_REVIEW  # PASS, exit 0
python -m research.trendbot.adoption check \
    --record research/results/synthetic_planted_s1/adoption_base.json --stage TESTNET       # BLOCKED, exit 1
python -m research.trendbot.adoption check \
    --record research/results/synthetic_null_s1/adoption_base.json --stage HUMAN_REVIEW     # BLOCKED: NO-EDGE
python -m research.trendbot.adoption check \
    --record research/results/synthetic_planted_s1/adoption_rr3_vol2_rsi50-70.json \
    --stage HUMAN_REVIEW \
    --config research/results/synthetic_planted_s1/config_rr3_vol2_rsi50-70.json          # PASS, exit 0
python -m research.trendbot.adoption check \
    --record research/results/synthetic_planted_s1/adoption_base_plus_ml.json \
    --stage HUMAN_REVIEW \
    --model-fingerprint 0986e8bdf0e5206e179c52b257b8872e4d740c0438bee6e8f4f175aa8d17f736  # BLOCKED: UNTESTED
python -m research.trendbot.adoption fingerprint          # canonical config JSON + sha256
python -m research.trendbot.adoption init --variant base --out rec.json   # empty record
```

Without `--config`, the `rr3_vol2_rsi50-70` check is BLOCKED by `ADOPT_fingerprint`: the
default config is not the one that was tested. Without `--model-fingerprint`, the ML check
is BLOCKED by `ADOPT_fingerprint` as well as by its label. `adoption --config c.json` takes a
JSON object of `StrategyConfig` overrides. The overrides are validated, so a loosening is
refused. `check` prints `PASS` or every blocking reason, and exits with 0 or 1.

## 5. How to read results

Every results table puts **TRAIN and TEST side by side**. The split is chronological at 70%
of the common time range of all pairs. TEST starts with fresh equity and fresh circuit
breakers, and indicators warm up on earlier candles only. Fitted and selected things
(discovery selection, ML coefficients) see TRAIN only. Section 2 of every report states the
costs used and the measured fee cost per trade in R.

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

A summary of [`research/results/RESULTS_SUMMARY.md`](../results/RESULTS_SUMMARY.md), which
was regenerated from the v2 code.

- **No real-data result exists yet.** The sandbox has no network and no ccxt, so every
  number comes from synthetic worlds. These verify the methodology and are not evidence
  about BTC/ETH/BNB. No variant may be adopted on their strength.
- Scope: 54 pipeline runs (4 seed-1 runs plus 50 calibration seeds), each with 30
  backtests, 6 years of 4H candles and 3 pairs, at the default costs (0.10% fee per side,
  0.05% slippage). The invariant audit found 0 violations. Fees alone cost about 0.06R per
  baseline trade (median stop distance 3.5-3.8% of the entry price).
- **Seed 1 matched ground truth in three of four worlds, and partly in the fourth.**
  - null: "No robust result found."
  - planted: baseline and selected variant ROBUST (baseline TRAIN +0.296R, n=115; TEST
    +0.412R, n=51, 90% CI [+0.059, +0.765]).
  - decay: "No robust result found." (baseline TRAIN-ONLY, +0.307R TRAIN vs -0.143R TEST).
  - hour_edge: "No robust result found." The ML layer raised TEST expectancy from +0.050R
    (baseline) to +0.295R (n=44), as predicted, but its CI [-0.045, +0.636] includes zero,
    so a real edge went undemonstrated (a false negative).
- **Calibration:**
  - null: 0/20 ROBUST false positives (90% Wilson CI 0-12%).
  - decay: 1/10 false positive (10%, CI 2-35%), from the baseline in seed 3.
  - planted: the verdict detected the edge in 8/10 seeds (54-93%), the baseline alone
    in 8/10 too.
  - hour_edge: the verdict found the edge in 4/10 seeds (19-65%). The ML layer's TEST
    expectancy beat the baseline's in 9/10 (mean +0.185R to +0.342R). The fitted
    `hour_sin` coefficient had the planted (negative) sign in 10/10 hour_edge seeds,
    against 11/20 null seeds.
- **Weaknesses shown by the calibration:**
  - One ROBUST false positive: decay seed 3's baseline (TRAIN +0.360R, n=111; TEST
    +0.501R, n=39, CI low +0.116), although its TEST period is exactly the null world.
    Once a genuine TRAIN edge disappears, the TEST CI is the only guard, which is why a
    ROBUST walk-forward is followed by a human review of every trade and at least 2 weeks
    of testnet.
  - Power is limited by about 33-61 baseline TEST trades per seed. A real edge (mean
    +0.46R) was labelled UNTESTED in 2 of 10 planted seeds (seeds 6 and 10).
  - The ML layer costs power when there is nothing extra to learn. In null it was worse
    than the baseline on TEST (-0.204R vs -0.021R on average) while its in-sample TRAIN
    numbers looked better (seed 1: +0.267R TRAIN vs -0.294R TEST, n=17).
  - Taking three looks at TEST inflates the false-positive rate by up to about 3x.
  - 20 null seeds only bound the false-positive rate below about 12%.
  - The planted effect is deliberately strong, and realistic Coinbase low-tier fees can
    remove an edge of this size entirely (section 4.3). **On real data, expect UNTESTED
    far more often than ROBUST.**

## 7. Model-complexity policy

- **Now: L2 logistic regression on 6 features** (`rsi`, `vol_ratio`, `ema_gap_pct`,
  `dist_regime_pct`, `hour_sin`, `hour_cos`). The penalty is fixed (`l2=1`), the threshold
  is the break-even probability, and nothing is tuned. It is fitted by Newton/IRLS in pure
  Python, on a few hundred purged TRAIN candidates (249-407 in the four seed-1 runs; 268 in
  planted).
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
- It must write `journal.write_journal`-format journals with real fill times, so that
  `invariants`, `journal_rules` and the TESTNET journal cross-check in `adoption.py` can
  audit it.
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

**Model fingerprints (CONTRACT.md v2 A3).** An ML variant is adopted as a (config, model)
pair. `MLFilter.fingerprint()` is the sha256 of a canonical JSON of the features, TRAIN
means and standard deviations, intercept, coefficients, threshold and `l2` (floats written as
`repr` strings, so the digest pins the model bit for bit). `run_research` writes that exact
JSON as `model_base_plus_ml.json` (its `sha256sum` equals the fingerprint) and an
`adoption_base_plus_ml.json` record carrying it. `adoption check` then blocks
(`ADOPT_fingerprint`) when the supplied fingerprint differs from the recorded one, when none
is supplied for a record that has one, when one is supplied for a record without one, and
when a `+ml` variant has no recorded fingerprint. **Refitting the model (new TRAIN data, a
later split, another `l2`) changes the fingerprint, so a refitted model is a new candidate
and restarts the adoption path at BACKTEST.** FreqAI's periodic retraining would therefore
restart the path on every retrain, so its model artefacts would first need the same
pinning. Two gaps remain: there is no loader that rebuilds an `MLFilter` from
`model_base_plus_ml.json` for a live bot, and nothing yet pins a FreqAI model.

## 9. Adoption path (always)

`adoption.py` enforces this order, with no shortcuts. A good backtest number never skips a
step. The config promoted must be exactly the config tested (sha256 of the canonical config
JSON), and for an ML variant the model promoted must be exactly the model tested (section 8):

```text
BACKTEST -> WALK_FORWARD -> HUMAN_REVIEW -> TESTNET (>= 14 days) -> LIVE
```

1. **Backtest.** `run_research` records the report path and completion time (`--now`, or
   the current time).
2. **Walk-forward (TRAIN/TEST).** Requires label exactly `ROBUST`, `dd_ok` true, positive
   TEST avg R, and positive TRAIN and TEST counts. `run_research` writes these fields for
   every adoptable candidate, the ML layer included when it was fitted.
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
     in which no trade carries a mandatory-rule flag (R7 size/cap, 2:1 by price or net of
     costs, stop and target levels, look-ahead).

   With ccxt the testnet is selected like this (run in this cycle only against the fake
   ccxt module of section 10; there is no network here):

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
   `adoption.require_stage(record, "LIVE", cfg, now_ms, base_dir)` at startup (plus
   `model_fingerprint=` for an ML variant) and refuses to trade if it raises.

## 10. Verification notes

Every command in this file except `pip install ccxt` was run in this cycle in the build
sandbox (Python 3.11.15, 4 CPUs, no network, no ccxt), from the repository root. Outputs
went to a scratch directory instead of the `--out` / `--out-dir` paths shown (and
`rec.json`), never into the repository; the committed files were only read.

- The four seed-1 commands (with `--now 2026-09-27T12:00:00Z`) and the four calibration
  commands were run with a scratch `--out-dir`. `diff -r` against the committed folders
  showed only the out-dir path, which `REPORT.md` prints in 4 command lines. Every journal,
  review pack, adoption record, config file, model file, `CALIBRATION.md` and
  `calibration_runs.csv` was byte-identical.
- The cost-sensitivity command gave the numbers quoted in section 4.3. The two refused
  cost settings shown in section 2.1 exited 2 with the errors shown, and so did
  `--slippage-pct 0` and `--exchange-id 'bin_ance!'`.
- `fetch_data`: `pip install ccxt` was NOT run (no network). The two `fetch_data` command
  lines were run with a scratch `--out` against a fake `ccxt` module on `PYTHONPATH`. It
  served the planted seed-1 candles as Binance 4h, and as Coinbase 2h halves only. Both
  exited 0, and the Coinbase path printed "coinbase has no 4h candles; aggregating 2h". Its
  aggregated 4h file was byte-identical to the Binance one. Adding BNB/USDT to the Coinbase
  command skipped BNB as "not listed" and exited 1. Without ccxt, the command exits 2 with
  the install hint.
- The two `run_research --data-dir` commands were run on those fetched files, with the
  planted world's 273 events exported to `events.csv` (scratch `--out-dir`). The Binance
  run reproduced sections 1, 3-8, 10 and 12 of the committed `synthetic_planted_s1` report
  exactly. Sections 9 and 11 differed only in the printed paths and the synthetic-data
  note, and section 2 (provenance) names the files. The Coinbase run (0.6% fee) printed its
  costs, labelled all three candidates UNTESTED ("No robust result found.") and wrote
  `config_base.json`.
- On those scratch outputs: `SPLIT_MS` printed 1678752000000. `invariants --journal` found
  0 violations in the Binance baseline TEST journal (51 trades, `--start-ts`) and in its
  TRAIN journal (115 trades, `--end-ts`). `review_sheet` flagged 0 of 36 trades, and
  `journal_rules` reported no active adaptations. The Binance `adoption check` PASSed
  HUMAN_REVIEW. The Coinbase check with `--config` was BLOCKED only by
  `ADOPT_walk_forward` (UNTESTED). `invariants --synthetic planted --seed 1` audited 166
  trades and found 0 violations.
- The offline commands on the committed outputs, including section 2.3, printed what is
  shown. `sha256sum` of each committed `model_base_plus_ml.json` equals the fingerprint in
  its record.
- The live-bot block of section 2.2 was extracted from this file and executed, with a
  scratch working directory holding a valid LIVE-stage record built from the adoption
  test fixtures, a journal and an events file. It allowed a BTC/USDT entry at 1% all-in
  risk. A clean stop at that plan's size and target works out to -1R and a take-profit to
  +2R, and the gatekeeper used exit offset 0. With a fill 0.2% worse than expected, the
  actual-fill plan held 2.325 units instead of the 2.516 ordered, at the same 1% risk. With
  the committed synthetic `adoption_base.json` instead, `require_stage` raised
  `AdoptionBlocked`, as it must. The testnet snippet ran against the fake ccxt module
  (`set_sandbox_mode(True)` was called; the keys were dummy environment values).
- The synthetic calibration numbers in section 4.2 were re-measured with
  `backtester.run_backtest` (seeds 1-5, full history, default config).
- The 101 test functions of the section 3 map were each checked against
  `pytest --collect-only`. Selected together with `pytest -k`, they ran as 254 cases, and
  all passed. The same 254 passed when selected by node id. The full suite and both ruff
  commands passed.

## 11. Where the outputs live

- `research/results/<run>/REPORT.md`: the 12-section report. It starts with the win-rate
  statement, then data provenance and costs, TRAIN | TEST tables, verdict, rule-denial
  counts, invariant audit, journal rules, review packs and adoption stage.
- `research/results/<run>/journals/*.csv`: a TRAIN and a TEST journal for each of the three
  pre-registered candidates.
- `research/results/<run>/review_*/trades_review.{md,csv}`: the human review packs.
- `research/results/<run>/adoption_<variant>.json`: the adoption records. The ML one
  carries the model fingerprint, and `model_base_plus_ml.json` is the exact JSON it hashes.
- `research/results/<run>/config_<variant>.json`: the `StrategyConfig` overrides of a
  non-default config, for `adoption check --config`.
- `research/results/<run>/run.log`: the console output of the committed run.
- `research/results/calibration_<world>/`: `CALIBRATION.md` and `calibration_runs.csv`.
- `research/results/RESULTS_SUMMARY.md`: cross-run summary and the honest list of
  weaknesses.

## 12. Disclaimer

This is a research and testing tool, not financial advice and not a recommendation to trade.
Backtests and synthetic worlds are simplified models: past or simulated results do not
predict future results, and no result in this repository comes from real market data.
Crypto trading can result in the total loss of the capital used.
