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
