# trendbot: module contract

This is the binding interface spec every module is built against. Shared types live in
`models.py`. The validated config, which rejects any loosening of a mandatory rule,
lives in `config.py`. Neither file may be changed without the orchestrator's sign-off.

Hard constraints for all code:
- **Python 3.11 standard library only.** No numpy, pandas, sklearn, ccxt or TA-Lib at import
  time. The sandbox has none of them and no network. `fetch_data.py` may import `ccxt`
  lazily inside a function.
- Deterministic: any randomness uses a `random.Random(seed)` instance, never the global RNG.
- No look-ahead: a value computed from candle `i` may only drive an action at or after
  `candles[i].ts + timeframe_ms`. Fills happen at the NEXT candle's open.
- Lint: `ruff check research/` and `ruff format --check research/` must pass (line length
  100, mccabe max complexity 12). Use relative imports inside the package.
- Tests: pytest in `research/trendbot/tests/test_<module>.py`. Run from the repo root with
  `python -m pytest research/trendbot/tests -q`.
- Run from the repo root as `python -m research.trendbot.<module>`.

Rule ids (`config.RULE_IDS`) must be used in every `Decision.rule` string.

---------------------------------------------------------------------------------------

## W1: Signal & Structure Engineer (`indicators.py`, `signals.py`, `structure.py`)

```python
# indicators.py
def ema(values: Sequence[float], period: int) -> list[float | None]
    # Seeded with the SMA of the first `period` values; None before index period-1.
    # alpha = 2/(period+1).
def rsi_wilder(closes: Sequence[float], period: int = 14) -> list[float | None]
    # Wilder smoothing; first value at index `period`; RSI=100 when avg loss == 0 and
    # avg gain > 0; 50 when both are 0.
def prev_mean(values: Sequence[float], n: int) -> list[float | None]
    # out[i] = mean(values[i-n:i]) (PREVIOUS n values, current excluded); None if i < n.
def compute_features(candles: Sequence[Candle], cfg: StrategyConfig) -> list[FeatureRow]
    # One FeatureRow per candle, same order. close_ts = ts + cfg.timeframe_ms.
    # hour_utc = UTC hour of close_ts.

# signals.py
def check_entry(pair: str, row: FeatureRow, cfg: StrategyConfig) -> SignalCheck
    # Gates in this order, all always evaluated (for audit):
    #  "trend"    R1: close > ema_fast and close > ema_slow and ema_fast > ema_slow
    #  "momentum" R2: cfg.rsi_min <= rsi <= cfg.rsi_max
    #  "volume"   R3: vol_ratio >= cfg.vol_mult
    #  "regime"   R4: close > ema_regime; if cfg.regime_filter is False the gate PASSES
    #             with detail "disabled (explicit test variant)".
    # Any None input -> that gate fails with detail "insufficient history".

# structure.py
def find_stop(candles: Sequence[Candle], i: int, pair: str,
              cfg: StrategyConfig) -> tuple[StopPlan | None, str]
    # R8. Stop for a long whose signal candle is candles[i] (known at its close).
    # 1) Most recent CONFIRMED pivot low j: low[j] < low of the k=cfg.swing_pivot_k candles
    #    on each side, with j + k <= i (confirmed by close of i), j >= i - cfg.swing_lookback,
    #    and low[j] < candles[i].close. method="pivot".
    # 2) Else fallback: min low of candles[i-fallback_lookback+1 .. i]. method="lookback_low".
    # stop = structure_level * (1 - buffer_pct/100), buffer = cfg.risk_for(pair).stop_buffer_pct
    # Reject (None, reason) if stop distance vs candles[i].close is < min_stop_distance_pct
    # or > max_stop_distance_pct, or if stop >= close.
```

## W2: Risk & Portfolio Controls Engineer (`sizing.py`, `news.py`, `correlation.py`)

```python
# sizing.py
def size_position(equity: float, entry: float, stop: float, risk_pct: float,
                  cfg: StrategyConfig) -> SizingResult
    # R7. qty = equity*risk_pct/100 / (entry - stop). Raise ValueError if stop >= entry,
    # equity <= 0, or risk_pct <= 0. If qty*entry*(1+fee_rate) > equity (no leverage),
    # shrink qty to fit and set capped_by="notional" (actual risk is then lower, never higher).
def max_risk_pct(pair: str, cfg: StrategyConfig) -> float   # cfg.risk_for(pair).max_risk_pct

# news.py
def load_events(path: str | Path) -> list[NewsEvent]
    # CSV header: time_utc,scope,impact,kind,note. time_utc is ISO-8601 ("2024-03-12T12:30:00Z")
    # or integer ms. Raise ValueError with the line number on malformed rows.
class NewsCalendar:
    def __init__(self, events: Iterable[NewsEvent], cfg: StrategyConfig) -> None
    def check(self, pair: str, ts: int) -> Decision   # rule "R5_news_blackout"
    # Blocked if ANY:
    #  - impact=="high" and scope in {"ALL", base_of(pair), f"EXCHANGE:{cfg.exchange_id}"}
    #    and |event.ts - ts| <= news_blackout_hours
    #  - base_of(pair)=="BNB" and kind in cfg.bnb_event_kinds (any impact, scope ALL/BNB/
    #    EXCHANGE:binance) and |event.ts - ts| <= bnb_event_blackout_hours
    # reason names the event (kind, time, note). Must use bisect (sorted events), O(log n).
    def loaded(self) -> bool  # False if built with zero events (reports must flag this)

# correlation.py
class CorrelationGuard:
    def __init__(self, cfg: StrategyConfig) -> None
    def check(self, pair: str, requested_risk_pct: float,
              open_risk: Mapping[str, float]) -> tuple[Decision, float]
    # R6. open_risk maps open pair -> planned risk pct. Returns (decision, allowed_risk_pct).
    # Pairs outside cfg.correlated_cluster: allowed at requested risk.
    # Deny if the pair already has an open position (no pyramiding).
    # Deny if base(pair) in exclusive_bases and any other cluster position is open.
    # Deny if any open cluster position's base is in exclusive_bases.
    # remaining = cluster_risk_budget_pct - sum(open cluster risk);
    # allowed = min(requested, remaining); deny if allowed < cfg.min_trade_risk_pct.
```

## W3: Journal & Circuit-Breaker Engineer (`journal.py`, `circuit_breakers.py`, `journal_rules.py`)

```python
# journal.py
JOURNAL_COLUMNS: tuple[str, ...]   # every Trade field; features flattened as f_<name>;
                                    # timestamps written as ISO-8601 UTC AND raw ms columns
def write_journal(trades: Iterable[Trade], path: str | Path) -> None
def read_journal(path: str | Path) -> list[Trade]     # exact round-trip of write_journal

# circuit_breakers.py
class CircuitBreakers:
    def __init__(self, cfg: StrategyConfig) -> None
    def on_trade_closed(self, trade: Trade) -> list[str]
        # Update state; return one-sentence explanations of any breaker that just tripped.
        # Consecutive-SL streak per pair: +1 on EXIT_SL, reset to 0 on any other exit.
        # When streak reaches cfg.consecutive_sl_limit: bench pair until
        # trade.exit_ts + bench_hours, then reset streak to 0.
    def can_enter(self, pair: str, ts: int, equity: float) -> Decision  # "R9_circuit_breaker"
        # Deny if pair benched at ts. Deny ALL pairs if the sum of pnl of trades with
        # exit_ts in (ts - loss_window_days, ts] is < -weekly_loss_limit_pct/100 * equity.
    @classmethod
    def from_journal(cls, trades: Iterable[Trade], cfg: StrategyConfig) -> CircuitBreakers
        # Replay closed trades in exit_ts order (the live bot rebuilds state on restart).
    # There is deliberately NO exit-related API: breakers can never block an exit.

# journal_rules.py : "learn from previous trades" as transparent rules over the journal
@dataclass(frozen=True) class Adaptation:
    rule: str; scope: str; action: str; explanation: str; evidence_trade_ids: tuple[int, ...]
def audit(trades: Sequence[Trade], cfg: StrategyConfig, now_ts: int,
          equity: float) -> list[Adaptation]
    # Every active adaptation, each explained in ONE sentence a human can check against the
    # journal rows listed in evidence_trade_ids:
    #  - R9 bench (pair, until time), R9 weekly halt (ALL),
    #  - L_expectancy_guard (only if cfg.expectancy_guard): the pair's last guard_window
    #    closed trades have avg R < 0 -> risk multiplier guard_risk_mult.
def risk_multiplier(trades: Sequence[Trade], pair: str, cfg: StrategyConfig) -> float
    # 1.0, or guard_risk_mult when the expectancy guard is on and triggered.
def main(argv: list[str] | None = None) -> int
    # CLI: --journal trades.csv --equity 10000 [--now ISO]; prints a markdown table.
```

## W4: Backtest & Data Engineer (`data.py`, `synthetic.py`, `fetch_data.py`, `backtester.py`)

```python
# data.py
def load_candles_csv(path: str | Path) -> list[Candle]
    # Header: ts,open,high,low,close,volume. ts = ms or ISO-8601. Sorted ascending, no
    # duplicate ts, high >= max(open, close), low <= min(open, close); raise with line no.
def save_candles_csv(candles: Iterable[Candle], path: str | Path) -> None
def load_dataset(data_dir: str | Path, pairs: Sequence[str],
                 timeframe: str = "4h") -> dict[str, list[Candle]]
    # File name per pair: "BTC_USDT-4h.csv" (pair with "/" -> "_").
def gap_report(candles: Sequence[Candle], timeframe_ms: int) -> list[tuple[int, int]]

# synthetic.py : controlled worlds with KNOWN ground truth (verification only, never
# evidence about real markets)
WORLDS = ("null", "planted", "decay", "hour_edge")
def make_world(world: str, seed: int, years: float = 6.0,
               pairs: Sequence[str] = ("BTC/USDT", "ETH/USDT", "BNB/USDT"),
               split_frac: float = 0.7) -> tuple[dict[str, list[Candle]], list[NewsEvent]]
    # Correlated pairs (common factor), BNB fatter wicks, lognormal volume with spikes.
    # null     : martingale log-price (zero drift), so NO strategy has true edge net of costs.
    # planted  : after a volume-spike up-candle the next K candles get positive drift
    #            (volume-momentum continuation) over the WHOLE history.
    # decay    : like planted but the effect exists only before split_frac of the history.
    # hour_edge: effect exists only for spikes whose candle closes 12:00-20:00 UTC, so an
    #            hour-aware filter has something real to learn.
    # Also emits synthetic news events: ~2 high-impact ALL/month, BNB burns quarterly,
    # launchpools monthly.

# fetch_data.py : real data (needs network + `pip install ccxt`; NOT runnable in the sandbox)
def fetch_ohlcv(exchange, symbol: str, timeframe: str, since_ms: int,
                until_ms: int | None = None, limit: int = 1000) -> list[Candle]
    # Paginates exchange.fetch_ohlcv; de-duplicates; drops the still-open last candle.
    # `exchange` is any object with a ccxt-compatible fetch_ohlcv (unit-tested with a fake).
def main(argv: list[str] | None = None) -> int
    # --exchange binance|coinbase --pairs BTC/USDT ETH/USDT BNB/USDT --timeframe 4h
    # --since 2019-01-01 --out research/data

# backtester.py
# EntryFilter is defined in models.py: (pair, row, check) -> (allow, prob, reason).
@dataclass class BacktestResult:
    trades: list[Trade]                # closed trades, in exit order
    decisions: Counter[str]            # rule id -> number of denied signal candles
    decision_log: list[tuple[int, str, str, str]]  # (ts, pair, rule, reason) per denial
    equity_curve: list[tuple[int, float]]          # (exit_ts, equity) after each close
    final_equity: float
    cfg: StrategyConfig
    news_calendar_loaded: bool
    window: tuple[int | None, int | None]
def run_backtest(data: Mapping[str, Sequence[Candle]], cfg: StrategyConfig,
                 events: Iterable[NewsEvent] = (), start_ts: int | None = None,
                 end_ts: int | None = None, entry_filter: EntryFilter | None = None,
                 variant: str = "base") -> BacktestResult
    # Entries only for signal candles with start_ts <= ts < end_ts; indicators use ALL
    # earlier data (warm-up). NO data at or after end_ts is used: open trades are force-
    # closed (EXIT_END) at the close of the last candle before end_ts.
    # Per timestamp, process all exits first, then entries, pairs in sorted order.
    # Entry pipeline for each signal candle i (first failure is logged, stop there):
    #   R1-R4 check_entry -> R5 news -> R9 breakers -> R6 correlation -> R8 find_stop
    #   -> entry_filter layer -> L_expectancy_guard multiplier -> R7 size at fill.
    # Fill at candles[i+1].open * (1 + slippage). If that open <= stop: skip (X_capital
    # is wrong here; log rule "R8_structure_stop" with reason "gapped through stop").
    # target = entry + reward_risk * (entry - stop).
    # Exit model per candle j >= fill candle (use simulate_exit):
    #   open <= stop -> SL at open*(1-slip) (gap-through);  low <= stop and high >= target
    #   -> SL (conservative, stop assumed first); low <= stop -> SL at stop*(1-slip);
    #   high >= target -> TP at target (limit, no slippage).
    # Fees: fee_rate * notional on entry and exit. pnl net; r = pnl / risk_amount.
    # Circuit breakers and correlation only ever gate ENTRIES; exits are always processed.
def simulate_exit(candles: Sequence[Candle], fill_idx: int, entry: float, stop: float,
                  target: float, cfg: StrategyConfig,
                  last_idx: int | None = None) -> tuple[int, float, str] | None
    # Shared by the backtester and enumerate_candidates so labels use the exact same model.
    # Returns (exit_idx, exit_price, reason) or None if unresolved by last_idx.
def enumerate_candidates(data, cfg, events=(), start_ts=None, end_ts=None
                         ) -> list[CandidateOutcome]
    # Every signal passing R1-R5 and R8 in [start_ts, end_ts), outcome simulated in
    # isolation; candidates whose exit is not resolved strictly before end_ts are DROPPED
    # (purging). r_multiple includes fees and slippage exactly like run_backtest.
```

## W5: Validation & ML Engineer (`metrics.py`, `walkforward.py`, `strategy_discovery.py`, `ml_filter.py`, `run_research.py`)

```python
# metrics.py
@dataclass(frozen=True) class Summary:
    n: int; wins: int; win_rate: float; avg_r: float; median_r: float; total_r: float
    profit_factor: float | None; max_dd_r: float; max_dd_pct: float; t_stat: float
    ci90_low: float; ci90_high: float      # bootstrap percentile CI of avg_r, seeded
    exit_counts: dict[str, int]; avg_hold_h: float
def summarize(trades: Sequence[Trade], starting_equity: float, seed: int = 7,
              n_boot: int = 2000) -> Summary
LABELS = ("ROBUST", "TRAIN-ONLY", "UNTESTED", "NO-EDGE")
def label(train: Summary, test: Summary, min_train: int = 30,
          min_test: int = 30) -> tuple[str, str]
    # (label, one-sentence reason):
    #  train.n < min_train or test.n < min_test          -> UNTESTED
    #  train.avg_r <= 0                                  -> NO-EDGE (nothing to validate)
    #  test.avg_r <= 0                                   -> TRAIN-ONLY (likely curve-fit)
    #  test.ci90_low <= 0                                -> UNTESTED (positive but not
    #                                                        distinguishable from zero)
    #  else                                              -> ROBUST
def dd_check(test: Summary, max_dd_pct: float) -> tuple[bool, str]

# walkforward.py
def split_ts(data: Mapping[str, Sequence[Candle]], train_frac: float = 0.7) -> int
    # Chronological split on the COMMON time range of all pairs.
@dataclass class WalkForwardResult:
    variant: str; split_ts: int; train: BacktestResult; test: BacktestResult
    train_summary: Summary; test_summary: Summary; label: str; label_reason: str
    dd_ok: bool; dd_reason: str
def walk_forward(data, cfg, events=(), train_frac=0.7, entry_filter_factory=None,
                 variant="base", max_dd_pct=20.0) -> WalkForwardResult
    # entry_filter_factory(train_candidates) -> EntryFilter is FIT ON TRAIN ONLY and then
    # applied unchanged to TEST.

# strategy_discovery.py : small grid of rule TIGHTENINGS, selected on TRAIN only
def default_grid(base: StrategyConfig) -> list[StrategyConfig]
    # reward_risk in {2.0, 2.5, 3.0} x vol_mult in {1.5, 2.0} x rsi in {(50,70), (55,70)}
    # (12 variants, all legal by construction; plus regime-off as ONE explicit test variant)
def discover(data, base, events=(), train_frac=0.7, min_train=30) -> DiscoveryResult
    # Select the variant with the highest TRAIN t-stat among those with >= min_train TRAIN
    # trades. TEST is evaluated for every variant for side-by-side reporting, but the
    # selection is recorded before any TEST number is computed.

# ml_filter.py : pure-python L2 logistic regression (IRLS/Newton), no tuned hyperparams
FEATURES = ("rsi", "vol_ratio", "ema_gap_pct", "dist_regime_pct", "hour_sin", "hour_cos")
class LogisticModel: fit(X, y, l2=1.0), predict_proba(X), coefficients()
class MLFilter:
    @classmethod fit(cls, candidates: Sequence[CandidateOutcome], l2: float = 1.0,
                     min_samples: int = 100, min_per_class: int = 20) -> MLFilter
        # Standardise with TRAIN mean/std; label = r_multiple > 0.
        # Threshold = break-even win probability from TRAIN avg win R / avg loss R:
        # p* = -avg_loss_r / (avg_win_r - avg_loss_r). No threshold search.
        # Raises InsufficientData if the sample is too small.
    def entry_filter(self) -> EntryFilter
    def explain(self) -> list[tuple[str, float]]   # standardised coefficients
def make_factory(l2=1.0) -> Callable[[Sequence[CandidateOutcome]], EntryFilter]

# run_research.py : one command, writes REPORT.md + trade-by-trade CSVs for human review
def main(argv: list[str] | None = None) -> int
    # --data-dir DIR [--events events.csv]  | --synthetic WORLD --seed N
    # --out-dir research/results/<name>  [--calibrate-seeds N]
```

---------------------------------------------------------------------------------------

## v2 amendments (orchestrator, before gate cycle 1). These are binding and override v1.

### A1. Cost-aware risk and reward (R7 and the 2:1 minimum hold NET of costs)
With fill price `E` (entry slippage already included), stop `S`, `f = cfg.fee_rate` and
`s = cfg.slippage_pct / 100`:
- Assumed stop exit price: `S_x = S * (1 - s)`.
- All-in loss per unit at the stop: `L_u = (E - S_x) + f*E + f*S_x`.
- `qty = equity * risk_pct / 100 / L_u`. Notional or free-cash caps may only shrink qty.
- `risk_amount = qty * L_u` and `risk_pct = risk_amount / equity * 100`: the planned all-in loss.
- Target, so that the net win equals `reward_risk` times the all-in risk:
  `T = (E*(1+f) + reward_risk*L_u) / (1 - f)`.
- So a clean stop fill gives exactly `r = -1`, and a TP gives exactly `r = +reward_risk`.
  Only gap-through stops can be worse than -1R. The price-distance ratio
  `(T-E)/(E-S)` is then automatically > reward_risk.
- `SizingResult.stop_distance` stays `E - S`.
- Owners: W2 `sizing.size_position` / `size_for_pair`, W4 `gatekeeper.plan_fill` and
  `invariants`. Invariants check: net RR >= reward_risk, price RR >= reward_risk,
  `risk_amount == qty*L_u`, a clean SL has r == -1, and a TP has r == +reward_risk
  (tolerance 1e-9).
- A live bot sizes with the expected fill (last price * (1+s)), then recomputes the target
  from the actual fill.

### A2. R9 timing is measured from when an exit is certain
Backtest `Trade.exit_ts` is the OPEN of the exit candle, but the fill happens somewhere in
`[exit_ts, exit_ts + tf)`.
- `CircuitBreakers(cfg, exit_time_uncertainty_ms: int = 0)` uses the effective exit time
  `t_e = exit_ts + exit_time_uncertainty_ms`. The bench runs `[t_e, t_e + bench_hours)`.
  The 7-day window counts trades with `t_e` in `(ts - loss_window_days, ts]`.
- The backtester and gatekeeper pass `cfg.timeframe_ms`. A live bot journals real fill
  times and passes 0.
- `journal_rules.audit` / `risk_multiplier` and the CLI take the same parameter
  (CLI flag `--backtest-journal`, which sets it to the timeframe). `invariants` recomputes
  with the same convention.
- Result: a bench always lasts >= 24h of real time.

### A3. ML model fingerprint in the adoption path
- `MLFilter.fingerprint() -> str`: sha256 of a canonical JSON of features, means, stds,
  intercept, coefficients, threshold and l2.
- `AdoptionRecord` gains `model_fingerprint: str | None`.
- `check_promotion(..., model_fingerprint: str | None = None)`:
  - if the record has a model fingerprint, the supplied current one must match it;
  - a variant whose id contains "+ml" but has no recorded model fingerprint is blocked
    (rule `ADOPT_fingerprint`).
- run_research writes an adoption record for the ML variant, carrying its fingerprint.

### A4. Harness cost floors and bounds (config.py, done)
- Enforced by config: `fee_rate >= 0.0005`, `slippage_pct >= 0.01`, a 7-day loss limit in
  `(0, 10]`%, and `0.05 <= min_stop_distance_pct < max_stop_distance_pct <= 25`. Tests may
  not use zero-cost configs.
- run_research gains `--fee-rate`, `--slippage-pct` and `--exchange-id`. Coinbase Advanced
  Trade taker fees at low tiers are several times Binance's: pass the user's real tier.
  Reports print the costs used.
- `config.RULE_IDS` now includes the `ADOPT_*` ids.

---------------------------------------------------------------------------------------

## v3 amendments (orchestrator, before gate cycle 2). These are binding and override v1/v2.

### C1. Layers veto, they do not "only remove trades" (wording + reporting)
- An entry layer (ML filter, expectancy guard) can only VETO an entry that passed every
  mandatory rule. It never approves an entry that a rule denies.
- Because a veto can free R6 budget or change R9 state, the layer's trade list may contain
  other rule-compliant trades the base never took. Every claim must use this wording
  (config.RULE_IDS["L_ml_filter"], models.EntryFilter).
- Reports show, per window (TRAIN and TEST): signals vetoed, base trades absent from the
  layer's journal, and layer trades absent from the base journal, matched by
  (pair, signal_ts).

### C2. R5 without look-ahead: scheduled vs unscheduled news
- `NewsEvent.known_from_ts` (models.py, done). The block interval is
  `[max(ts - w, known_from), ts + w]`.
- The default known_from is -inf for scheduled kinds (macro, unlock, bnb_burn, launchpool)
  and `ts` for unscheduled kinds (regulatory, legal, other), so an unscheduled headline
  blocks only from the moment it happened.
- events.csv gains an optional `known_from_utc` column. It is backward compatible, and an
  empty cell means the kind default.
- Owner: W2 (news.py, events_example.csv). synthetic.make_events must set known_from
  consistently (W4).

### C3. The adoption record is bound to its evidence (W3 adoption.py; W5 writes the fields)
Record fields:
- `provenance`: "synthetic:<world>:<seed>" or "real".
- `data_files`: {path: sha256}, plus `events_file` {path, sha256} or null.
- `backtest.report_sha256`.
- `walk_forward.train_journal_path` / `_sha256`, `walk_forward.test_journal_path` /
  `_sha256`, `walk_forward.min_train` / `min_test`, `walk_forward.train_avg_r`, and the
  label parameters (C4).
- `human_review.review_path` / `review_sha256` (the trades_review.csv of the review pack).

`check_promotion`:
- From WALK_FORWARD on:
  - every recorded file must exist and its sha256 must match;
  - both journals are re-parsed and `metrics.summarize` + `metrics.label` are recomputed
    with the recorded parameters; the check blocks unless label, n, avg R and dd_ok match
    the typed fields;
  - ROBUST additionally requires train_n and test_n >= 30.
- From HUMAN_REVIEW on:
  - provenance must be "real" and data file hashes must be present;
  - synthetic or missing provenance is blocked with `ADOPT_provenance`
    (add it to ADOPTION_RULE_IDS; ORCH mirrors it into config.RULE_IDS).
- HUMAN_REVIEW:
  - trades_review.csv row count must equal train_n + test_n;
  - the (window, trade_id) set must equal the journals';
  - every reviewer_ok must be Y or N;
  - any N blocks. Policy: the variant is revised and restarts at BACKTEST.
- TESTNET:
  - run the invariants cross-trade checks on the testnet journal with exit offset 0: R6 (no
    BNB stacking, no concurrent full-size cluster risk, budget respected) and R9 (no entry
    inside a bench, no entry during a 7-day halt);
  - R5 is checked only if `testnet.events_path` (+ sha256) is given; otherwise the reason
    text and the README say R5 is not journal-verifiable.
- ML variants keep the v2 A3 model fingerprint.
- `expectancy_guard=True` configs need the same walk-forward evidence as any other variant
  (W5 pre-registers `base+guard`).

### C4. Label rule with multiplicity control and a conservative CI (W5 metrics.py)
- The pre-registered candidates that get a TEST look are: base, discovery-selected,
  base+ml, base+guard, so `m = 4`.
- ROBUST requires ALL of:
  - n >= 30 in both windows;
  - TRAIN avg R > 0;
  - TEST avg R > 0;
  - the one-sided lower bound at confidence `1 - 0.05/m` is > 0 for BOTH the iid bootstrap
    AND a calendar-month block bootstrap of the TEST R sequence (use the more conservative
    of the two; n_boot >= 4000; seeded).
- A positive TEST mean that fails only the CI is UNTESTED ("positive but not
  distinguishable from zero after multiplicity correction").
- The rule is fixed now, from the FWER argument. It is never tuned on TEST outcomes.
- Non-selected discovery variants and the regime-OFF test variant are reported as
  "context: <label>, not judged".

### C5. Drawdown
- Report both the realised (closed-trade) and the mark-to-market (4H close) max drawdown.
- `dd_ok` requires TEST MTM max DD <= min(20%, the 95th percentile of max DD from
  bootstrapping TRAIN R sequences at TEST length, in the same risk units).
- Both numbers and the rule appear in every report.

### C6. Calibration and power (W4 synthetic.py, W5 runs)
- A new `zero_edge` world: planted pre-cost drift sized so the base strategy's expected net
  R is ~0 in both windows.
- `make_world(..., effect_strength=None)` exposes EFFECT_MU_SIGMAS for power curves.
- An opt-in `gaps=True` mode (occasional opens away from the previous close).
- Defaults stay byte-identical to the current committed worlds.
- Runs, all reported with 90% Wilson intervals, TRAIN and TEST side by side:
  - calibration at >= 50 seeds for zero_edge and decay;
  - the existing null/planted/hour_edge calibrations;
  - a power curve (>= 4 strengths x >= 20 seeds) with the minimum detectable effect at
    50% / 80% power;
  - per candidate, how many seeds reached the TEST gate.

---------------------------------------------------------------------------------------

## v4 amendments (orchestrator, before gate cycle 3, the final cycle). Binding.

### D1. Pinned label rule in the adoption gate (W3)
- `label_params` in a record is a closed schema: `{seed, n_boot, m, alpha, max_dd_pct,
  train_dd_p95_pct, mtm_max_dd_pct}`. Any other key blocks the check.
- `m` must equal `metrics.M_CANDIDATES` (4) and `alpha` must equal `metrics.ALPHA` (0.05).
- `n_boot` must be >= `metrics.N_BOOT` (4000) and `seed` must equal
  `walkforward.SUMMARY_SEED`. Otherwise the check blocks with `ADOPT_walk_forward`.
- Do not route parameters by introspecting signatures.
- From HUMAN_REVIEW on, the C5 inputs are REQUIRED and RECOMPUTED:
  - `train_dd_p95_pct` is recomputed from the hash-bound TRAIN journal with the pinned
    seed and n_boot;
  - `mtm_max_dd_pct` is recomputed from the hash-bound candle files for `real` provenance;
  - a mismatch greater than 1e-9 blocks.
- `StrategyConfig.is_test_only` is True for regime-off and for any `stop_fill_wick_k > 0`.
  Such configs are blocked from HUMAN_REVIEW on with `ADOPT_test_only`.

### D2. Manifest-backed provenance (W4 fetch_data/data, W5 run_research, W3 adoption)
- `fetch_data` writes `manifest.json` next to the candles. It records the exchange id,
  ccxt version, symbol, timeframe, since/until, fetched_at_utc, and per file its name,
  rows, first/last ts and sha256.
- `data.verify_manifest(data_dir, pairs, timeframe) -> (provenance, details)` returns
  `"real"` only when every loaded file is listed with a matching sha256. Otherwise it
  returns `"unverified-csv"`.
- `run_research --data-dir` records that provenance plus the manifest path and sha256.
- adoption allows only `"real"` with a hash-matching manifest past WALK_FORWARD. Anything
  else is blocked with `ADOPT_provenance`.
- An offline check cannot authenticate an exchange download. The manifest makes a
  laundered CSV a deliberate act instead of an accident; docs must say so.
- `data.load_candles_csv` / `load_dataset` reject any candle with `ts % timeframe_ms != 0`
  ("not epoch-aligned").

### D3. Holdout ledger: holdout reuse is controlled (W5 writes, W3 checks)
- run_research appends one JSON line per pre-registered (adoptable) candidate that got a
  TEST backtest. The ledger is `--ledger PATH`; the default is `<data-dir>/.test_looks.jsonl`
  for real data and `<out-dir>/test_looks.jsonl` for synthetic data.
- Each line records: run_utc, argv, variant, config_fingerprint, model_fingerprint or
  null, split_utc, and per pair `{pair, file_sha256, test_start_ts, test_end_ts}`.
- Context (non-selected) discovery variants and test-only variants are NOT adoptable. They
  get no promotable record; any record for one is blocked with `ADOPT_holdout`, reason
  "context variant".
- From HUMAN_REVIEW on, check_promotion reads the ledger at `record.ledger_path` (required,
  hash not bound because it is append-only). For each pair of the record it counts the
  DISTINCT `(config_fingerprint, model_fingerprint)` among ledger lines for that pair whose
  TEST window overlaps the record's TEST window. It blocks with `ADOPT_holdout` if the
  count exceeds `metrics.M_CANDIDATES`.
- Re-running the identical candidates is not a new look.
- After a reviewer N, or any other revision, the variant needs TEST data it has never
  seen: a TEST window starting at or after the latest `test_end_ts` of every earlier look.
  It is never re-run on the same TEST window.
- REPORT section 6 and the console print the cumulative distinct-look count per pair for
  the run's TEST window.

### D4. Execution realism
- Stop-fill stress: with `cfg.stop_fill_wick_k = k > 0`, a non-gap stop fills at
  `stop - k*(stop - exit_candle.low)`, then slippage.
- invariants accepts r < -1 on non-gap stops only when k > 0.
- The default (k = 0) must stay byte-identical.
- Stress configs are test-only (D1).
- W5 runs k = 0.5 and 1.0 on the five seed-1 worlds and reports the R shift and any label
  change, TRAIN and TEST side by side.

### D5. Live adapter and parity (W4)
- `gatekeeper.LiveSession(cfg, events, journal_trades=(), starting_equity)` owns open
  positions, open_risk, equity and breakers (rebuilt with `CircuitBreakers.from_journal`).
- It exposes `on_candle_close(pair, candles) -> EntryDecision`,
  `on_fill(pair, decision, market_price, fill_price) -> Trade|None` and
  `on_exit(trade_id, exit_ts, exit_price, reason) -> Trade`, calling only
  `Gatekeeper.evaluate` / `plan_fill`.
- It must accept ACTUAL fills and exits injected from a journal so a replay can reproduce
  a live or testnet run.
- A whole-run parity test drives LiveSession candle by candle over a 6-year synthetic world,
  including a mid-run restart from the written journal. The trade list must equal
  run_backtest's exactly.
- `invariants --live-journal`: exit offset 0; cross-trade R6/R9; per-candle R1-R5/R8
  re-derived at each trade's signal_ts from supplied candles and events. The backtest
  fill-price identities are not asserted.

### D6. TESTNET evidence (W3; uses D5)
- The record's testnet section requires:
  - `journal_path`, `decisions_path` (a CSV of every evaluated signal:
    pair, signal_ts, allowed, rule, reason) and `candles` (the 4H candle files the testnet
    bot evaluated), each sha256-bound;
  - `starting_equity`, `events_path` (optional; without it R5 is stated as not verified).
- The check:
  - duration >= 14 days;
  - replay LiveSession over the candles, injecting the journal's actual fills and exits;
    the replayed allowed `(pair, signal_ts)` set must equal the journal's entries AND the
    decisions log's allowed rows, with every difference listed;
  - `invariants` live-journal mode is clean.
- The reason text states the expected trade count for the window at the measured baseline
  rate, about 1 per 14 days. It also says Binance spot testnet prices and liquidity are not
  mainnet, so this stage verifies execution and rule compliance, not edge.

### D7. C5 revision
- `metrics.DD_CAP_PCT = 15.0`.
- Rationale: at the mandated 1% cluster risk budget, 15% is 15 consecutive full-size
  losses. At the 2:1 break-even win probability p* = 1/3, that streak has probability
  (2/3)^15 ≈ 0.23%, so a TEST drawdown beyond it is inconsistent with even a break-even
  strategy at the mandated risk.
- The TRAIN-bootstrap p95 bound usually binds first.
- The 3% weekly-loss halt limits how fast the drawdown can accrue, not how deep it can go.

### D8. Fitted layers' TRAIN gate is out of sample
- For base+ml, the NO-EDGE TRAIN gate uses a purged chronological inner split: fit on the
  first 70% of TRAIN candidates, evaluate the filtered backtest on the last 30% of TRAIN,
  purged at the inner boundary.
- The in-sample TRAIN figure is shown as context only.
- The final model for TEST is still fit on all TRAIN candidates.
