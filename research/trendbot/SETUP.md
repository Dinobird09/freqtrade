# Running trendbot on your own computer

Nothing here depends on Claude. The bot, the dashboard and the learning files are plain Python
(3.11 or newer) plus one library, `ccxt`, for the exchange connection. It runs on Windows,
macOS and Linux.

## 1. Get the code

```bash
git clone https://github.com/Dinobird09/freqtrade.git
cd freqtrade
git checkout claude/blissful-gates-qkkz1i
```

Install **Python 3.11+** from python.org if you don't have it. On Windows, tick "Add
python.exe to PATH".

## 2. One-time setup

| OS | command (run from the repo folder) |
|---|---|
| macOS / Linux | `research/trendbot/scripts/setup.sh` |
| Windows (PowerShell) | `powershell -ExecutionPolicy Bypass -File research\trendbot\scripts\setup.ps1` |

The script does four things:

1. creates `.venv/`;
2. installs `ccxt`;
3. copies `bot_settings.example.json` to `bot.json`;
4. copies `.env.example` to `.env`.

`bot.json`, `.env` and the state folder are git-ignored, so your settings and keys never end
up in the repository.

## 3. Configure

- **`bot.json`:**
  - `exchange`: `binance`, or `coinbase` for Coinbase Advanced Trade (no BNB there).
  - `pairs`.
  - `mode`: `paper`, `testnet` or `live`.
  - `state_dir`: use one folder per exchange/mode.
  - `strategy.fee_rate`: your fee tier.
  - `learning`: see section 6.
- **API keys:** only for `testnet` and `live`. Add them in the dashboard (**Connections**,
  see section 4a); it writes them to `.env` for you. Or edit `.env` by hand:
  - `TRENDBOT_BINANCE_TESTNET_API_KEY` / `_API_SECRET`, `TRENDBOT_BINANCE_API_KEY` /
    `_API_SECRET`, `TRENDBOT_COINBASE_API_KEY` / `_API_SECRET`;
  - the generic `TRENDBOT_API_KEY` / `TRENDBOT_API_SECRET` are used for any account without
    its own keys;
  - use keys with trade permission only, no withdrawals, and IP-restricted if your exchange
    offers it;
  - Binance testnet keys come from testnet.binance.vision.

## 4. Start the dashboard and press Start

| OS | command |
|---|---|
| macOS / Linux | `research/trendbot/scripts/start.sh` |
| Windows | `powershell -ExecutionPolicy Bypass -File research\trendbot\scripts\start.ps1` |

Open **http://127.0.0.1:8050/**. The control bar has:

| button | what it does |
|---|---|
| ▶ Start bot | launches the bot in the background with `bot.json` (asks for confirmation in `live` mode) |
| ■ Stop bot | the bot finishes its current step and exits; open positions stay open and are managed again on the next start |
| ❚❚ Pause new entries / ▶ Resume | stops or allows NEW entries; stops and targets of open trades keep running |
| Close all positions | market-sells every open position (with confirmation) |
| Close (per position row) | market-sells that one position |
| Disable / Force on / Automatic (per learned rule) | switches a learned rule off, forces it on, or hands it back to the evidence checks |

The bot keeps running if you close the dashboard, and you can reopen the dashboard at any
time. Everything is also shown in tables:

- open positions;
- the trade ledger (why each trade was taken, what was expected, the result, the lesson);
- learned rules;
- closed trades;
- recent decisions with reasons;
- the log.

The dashboard only listens on your own machine (127.0.0.1). Every button needs a per-session
secret, and requests from other hosts are refused. To watch it from another device, use an
SSH tunnel, e.g. `ssh -L 8050:127.0.0.1:8050 you@that-machine`. Don't bind it to `0.0.0.0`.

Command-line equivalents, if you prefer:

```bash
.venv/bin/python -m research.trendbot.live_bot run     --settings bot.json
.venv/bin/python -m research.trendbot.live_bot status  --settings bot.json
.venv/bin/python -m research.trendbot.live_bot flatten --settings bot.json
```

On Windows use `.venv\Scripts\python.exe` in place of `.venv/bin/python`.

## 4a. Connections: exchange keys and MCP servers (TradingView)

The **Connections** card at the top of the dashboard:

| part | what you can do |
|---|---|
| Exchange accounts | Binance and Coinbase, testnet and live. **Add keys** opens a pop-up for the key, secret and (optional) passphrase. **Test** logs in and reads your balance; nothing is traded. **Remove** deletes the keys. |
| MCP servers | Pick **TradingView** (or Custom), optionally paste an access token, press **Add server**. The dashboard connects, lists the server's tools, lets you **Try** a tool with arguments, and **Collect hourly** stores its replies for every pair (shown under Market data). |

How the values are kept:

- keys and tokens go to your local `.env` (file permissions 0600) and are never shown again,
  not even to the dashboard;
- the server list goes to `connections.json`;
- both files are git-ignored;
- restart a running bot after changing its keys.

Tool arguments may use placeholders:

| placeholder | example value |
|---|---|
| `{symbol}` | `BINANCE:BTCUSDT` |
| `{pair}` | `BTC/USDT` |
| `{base}` | `BTC` |
| `{quote}` | `USDT` |
| `{exchange}` | `BINANCE` |

MCP replies are free-form text. The bot records and shows them, but they never open or block
a trade on their own.

If a server answers "needs a sign-in", paste the access token it gives you and add the server
again.

There is no Binance MCP to add: the bot talks to Binance directly through its API, using the
keys under Exchange accounts.

## 4b. The terminal: talk to the bot

The **Terminal** card at the top of the dashboard takes plain-word commands. It works
offline; no API key is needed. Type `help` or `pull out commands` to list them all:

| you type | what happens |
|---|---|
| `status` | running or stopped, entries open or paused, equity, today's P&L, open positions |
| `earnings today` / `yesterday` / `week` / `month` / `all` | realized P&L for the period, per pair, plus unrealized P&L of open positions (add `all bots` in a fleet) |
| `positions`, `trades` | open positions, and the last closed trades |
| `start day` | starts the bot if it is stopped and opens new entries |
| `end day` | pauses new entries (stops and targets keep running) and prints the day's summary; `end day and close all` also sells every position |
| `execute trade BTC` | takes BTC's next signal that passes all nine rules, even after `end day`; stays armed for 24h |
| `close BTC` / `close all` | market-sells a position |
| `why BTC` | BTC's latest decision and the rule behind it |
| `brain` | what Chantisimo has learned |
| `pause`, `resume`, `start bot`, `stop bot`, `bots`, `use <bot>`, `retrain`, `collect` | as the buttons |

Commands that sell, stop a bot, arm a trade or start a live bot ask first: type `yes` (or
press **Yes**) to go ahead, or `no` to cancel. `execute trade` never skips the nine rules: if
the latest candle failed them, the bot waits for a candle that passes. Times are UTC, and
`today` starts at 00:00 UTC.

## 4c. Live data: the 1-second dashboard

While the bot runs it reads the market every second (`poll_seconds`, default 1):

- **Live prices:** one request fetches the price of every pair. Stops and targets are
  checked against the live bid every second.
- **Forming candle:** the 4H candle that is still open is re-read every 5 seconds and kept
  up to date with each price in between.
- **Dashboard:** it shows the forming candle on the price chart, a live price line, and
  **Open P&L (live)** in the tiles. Open positions show their unrealized P&L at the live bid.

The page refreshes every second (`--refresh`, default 1). The server rebuilds the heavy
parts (candles, journal, decisions) only when a file changes, so each refresh takes a few
milliseconds. The page redraws only the sections whose data changed, so tables keep their
scroll position. New entries are still decided at each 4H close: that is the strategy's
timeframe.

Tune it in the bot settings: `"live": {"price_seconds": 1, "candle_seconds": 5}`. Set
`"enabled": false` to turn the feed off.

## 4d. Voice and the Jarvis look

The dashboard opens in a dark HUD theme; **Theme** switches to auto, light or dark. To talk
to the bot:

- **Push to talk:** click the glowing reactor (top left) or **🎙 Push to talk**, then say a
  command, e.g. "earnings today", "end day", "execute trade bitcoin", or "why ethereum".
- **Hands-free:** tick *hands-free* and start each command with "Jarvis" or "Chantisimo",
  e.g. "Jarvis, status".
- **Replies:** they are spoken aloud (untick *speak replies* to turn that off).
- **Confirming:** a command that needs confirmation waits for you to say "yes" or "no".

Voice uses your browser's speech recognition, so use **Chrome or Edge**, and allow the
microphone the first time. It works on `http://127.0.0.1` or over an SSH tunnel to
`localhost`. In Chrome and Edge, the browser sends your speech to its own speech service
(Google or Microsoft) to turn it into text. Only the recognized words reach the bot.

## 5. Keeping it running 24/7

The bot trades 4H candles, so it has to be running at each 4H close. On your own machine,
set up an automatic start:

- **Windows:** Task Scheduler → "At log on" → run `start.ps1`, then press Start once; or
  schedule `live_bot run` directly.
- **Linux:** a systemd user service running `live_bot run --settings bot.json`.
- **macOS:** a launchd agent running the same command.

You can restart safely at any time: the journal and state files rebuild open positions, the
risk budget, the circuit breakers and the learned rules.

For hosting on a server, the company-approved route is GCP. Check the code into GitHub and
message Freddy in Slack for deployment help.

## 6. The learning loop (what the bot learns and how to control it)

Files the bot writes in `state_dir`:

| file | contents |
|---|---|
| `ledger.json` | every trade: signal time, entry, stop, target, expected outcome (+2R / −1R and how similar past trades did), why it was triggered (every gate's detail), exit, R, lesson |
| `learnings.md` | a plain-English lesson per closed trade, plus the learned rules |
| `learnings.json` | the same rules in machine-readable form, plus your on/off overrides |

Context injection: before every new entry, the active learned rules are checked. A matching
signal is vetoed and logged with rule `L_learned_rule`.

A rule is only enforced when all of these hold:

1. **Enough trades:** at least `min_trades` (default 8) closed trades match it.
2. **Clearly losing:** they average ≤ `max_avg_r` (−0.25R), with a 95% upper bound below 0R.
3. **Confirmed by backtest:** vetoing it raises average R in BOTH the older and the newer
   part of the cached candle history.
4. **Within limits:** no more than 3 active rules, together vetoing at most 50% of past
   entries.

Set `"learning": {"mode": "advisory"}` to log rules without blocking anything. Learning can
only veto trades that already passed the nine strategy rules; it never loosens a rule or
raises risk.

## 6a. Chantisimo: the brain

Chantisimo is the part of the bot that decides whether to take a signal that passed the nine
rules, and that learns from every trade. Before each entry it:

1. **Recalls** the 100 most similar past trades, by RSI, volume, EMA gap, distance to EMA200,
   hour and pair, and what happened to them. It searches its own trades, the paper bots'
   and followed traders' trades, and every simulated signal in the cached history (written
   by the weekly retrain, up to 6,000), so 100 neighbours are available from the first day.
2. **Checks graduation.** A testnet or live bot trades a pair only after its paper bots have
   at least 20 closed trades on that pair averaging ≥ 0R.
3. **Applies the learned rules and the validated signal layers.** This includes
   `chantisimo_recall`, a nearest-neighbour veto that is switched on only after it helps on
   unseen data.
4. **Writes its verdict** (TAKE or SKIP, with the reason and the recall) to
   `chantisimo_thoughts.jsonl`.

After each close it **reflects**:

- what the recall predicted, compared with what happened;
- which **mistakes** caused a loss: entry conditions unlike the winners' (weak volume,
  stretched RSI, late entry far above EMA200, …) or a stop-out within 2 candles;
- the lesson it takes from the trade.

The mistake book counts every mistake. A mistake that keeps losing becomes a blocking rule
through the learned-rule checks in section 6.

**Learning from paper before real money.** Point a testnet or live bot at your paper bots:

```json
"brain": {"learn_from": ["trendbot_state/binance-core-paper"], "min_paper_trades": 20}
```

The bot then remembers the paper bots' trades, including their losses, and learns its rules
and layers from them. It has lost nothing of its own at that point. Memory is causal: a
decision only sees trades that had closed by then.

The dashboard's **Chantisimo** card shows:

- its memory;
- the graduation table;
- the mistakes and what it does about each;
- its recent thoughts;
- a reflection per closed trade.

`chantisimo.md` holds the same in plain English.

Chantisimo only ever skips. It never opens a trade, loosens a rule or raises risk. It lowers
the number of losing trades, but no rule set removes losses entirely.

## 6b. Smart money: learn from and copy other traders

The **Smart money** card ranks other traders and lets you follow them. Add a trader by name
and source:

| source | what to give it |
|---|---|
| trade history file | a CSV or JSON of their fills; a Binance "Trade History" export works as is (Date, Pair, Side, Price, Executed, Fee) |
| feed URL | an `https://` address serving the same CSV/JSON, re-read every 5 minutes |
| another trendbot | the other bot's state folder |
| Solana wallet | a public wallet address; its swaps are read from the chain (public RPC or `SOLANA_RPC_URL`) |

Each trader's fills become round trips (flat to long to flat), and the card shows:

- trades, win rate and its **95% lower bound**, so 3 lucky wins don't count as 100%;
- average return, profit factor, max drawdown and style (scalper, intraday, swing,
  position).

A trader is **qualified** with at least 30 trades, a win-rate lower bound of at least 45%,
a profit factor of at least 1.3, a max drawdown of at most 35%, and a style a 4-hour bot
can follow (scalpers' entries are gone by the next close). Change the limits in
`traders.json` under `"rules"`.

**Copying.** When a qualified trader you **follow** opens a position on a pair your bot
trades (within the last 12 hours), the bot **arms** that pair. It then buys at the next 4H
close where the signal passes all nine rules, sized by its own stop and the 1% risk limit.
It never mirrors a trade blindly, and it acts on each copy signal once. Solana wallets are
watch-only: the bot holds no wallet keys.

**Learning.** Every trader's closed trades (followed or not) join Chantisimo's memory, with
this bot's own entry features at their entry time and R estimated from their typical loss.
Their losses show up in the mistake book and in recall ("other people's mistakes").

There is no official API for exchange copy-trading leaderboards, so the bot doesn't scrape
them. Add a leaderboard trader's shared history or feed instead. Terminal: `traders`,
`follow <name>`, `unfollow <name>`.

## 6c. Verification agents

Seven independent checkers re-derive what the charts and the bot show. They run every 15
minutes while the bot runs; press **Verify now** or type `verify` to run them at once.

| agent | checks |
|---|---|
| data | cached candles: no gaps or duplicates, sane OHLC, not stale |
| exchange | re-downloads the latest 60 candles and compares them with the cache |
| indicator | recomputes EMA9/21/200, RSI(14) and the volume ratio with its own code |
| chart | the dashboard's price chart equals those independent values, candle by candle |
| price | the live price against a second exchange: a bad tick shows as a large gap |
| ledger | dashboard equity, trade count and open positions equal the journal; P&L and R add up |
| audit | every live trade re-checked against the nine rules on the candles it traded on |

If the data, exchange, indicator or price agent **fails** a pair, the bot takes no new
entries on it (rule `X_data_check`) until a later check passes. Exits are never blocked.

## 6d. Capital protection

On top of the 1% risk per trade (R7) and the circuit breakers (R9), the bot watches its
**mark-to-market** equity (realized plus open positions at the live bid) every second:

| condition | what happens |
|---|---|
| today's drawdown > 2% (UTC day) | every new entry's risk is halved for the rest of the day |
| today's drawdown > 3% | every open position is sold and new entries freeze for 24 hours |
| drawdown from the equity peak > 10% | **kill switch**: sells everything, pauses entries, writes `trading_halted.lock` and stops |
| 3 losing trades in a row today | no new entries until 00:00 UTC |
| fee rate above 0.1% | a warning in the log and on the dashboard |

`trading_halted.lock` sits in the bot's state folder and says why and when trading stopped.
While it exists, neither the dashboard nor the command line will start the bot. Review what
happened, then **delete the file by hand** to allow a restart.

**Maker orders.** On testnet and live, entries go in as a post-only limit order at the bid.
That pays the lower maker fee and has no slippage. Whatever hasn't filled after 15 seconds
is cancelled and bought at market. Stops always exit at market.

Change the limits in the bot settings:

```json
"capital": {"daily_reduce_pct": 2, "daily_flatten_pct": 3, "freeze_hours": 24,
            "peak_kill_pct": 10, "max_consecutive_losses": 3, "max_fee_rate": 0.001},
"execution": {"maker_first": true, "maker_wait_seconds": 15}
```

## 6e. Market regime and allocation

A 5-state Gaussian HMM (**crash, bear, neutral, bull, euphoria**) reads BTC/USDT's 4H
returns and volatility. BTC is the market leader, so there is one regime for every pair. It
uses only **forward-filtered** probabilities (P(state now | data up to now)): no
`.predict()` over the whole series and no smoothing, so no look-ahead. A new regime counts
only once it has been the most likely state for **3 bars in a row**.

The regime caps how much of your equity open positions may use:

| regime | exposure cap |
|---|---|
| bull, euphoria | 95% |
| neutral | 50% |
| bear | 25% |
| crash | 0% (no new entries) |

The cap only shrinks or skips new entries; each trade's size still comes from its stop and
the 1% risk limit. The model is fitted on first start and refitted every week, and each new
candle updates the probabilities. The **Market regime** card shows:

- the regime, the probability of each state, and the last 60 bars;
- a correlation matrix of the pairs' returns, so assets moving together are visible.

Terminal: `regime`. Settings:
`"allocation": {"enabled": true, "persistence": 3, "caps": {"neutral": 50}}`.

## 6f. Market scanner (today's movers)

The **Market scanner** card, the `scan` command and the hourly collect job look at every
USDT ticker on the exchange and keep coins that pass all four filters:

| filter | rule |
|---|---|
| price expansion | up at least 10% since today's 00:00 UTC open |
| relative volume | today's volume at least 5x the average of the previous 50 days |
| catalyst | a headline, Reddit post or token-unlock mention of the coin in the last 24 hours (from the collected news) |
| float | fewer than 10 million tokens circulating (CoinGecko's free API; `COINGECKO_API_KEY` in `.env` is optional) |

It is a **watchlist**. To trade a coin, add its pair to a bot: the nine rules, the 1% risk
limit and every guard still apply. Change the limits with
`"scanner": {"min_day_change_pct": 10, "min_rvol": 5, "max_float": 10000000}`.

**News for sentiment and catalysts.** Besides the RSS feeds, Reddit and CryptoPanic, you can
scrape news sites that have no feed: `"sentiment": {"pages": [{"name": "site", "url":
"https://…", "selector": "h2 a"}]}`. It uses BeautifulSoup if installed
(`pip install beautifulsoup4`), otherwise it reads the page's headings. Headlines are scored
by FinBERT if installed, else VADER (`pip install vaderSentiment`), else the built-in
lexicon. Set `"scorer": "textblob"` for TextBlob. The score is a number in [-1, 1] per coin
and 4H bucket; the `news_sentiment` layer and the dashboard use it.

New DEX contracts: the DEX scanner follows DEXScreener's newest token profiles and boosts,
and scores their volume, liquidity and holder concentration (section 8).

## 6g. Strategy lab: new strategies, researched overnight

Besides the main nine-rule strategy, the lab researches three more:

| strategy | timeframe | idea |
|---|---|---|
| `nnfx` | 4H | 50 SMA baseline; MACD and a Range Filter must turn up together; a volatility / volume gate against chop; stop 1.5x ATR (never a fixed %); half sold at 2R, then the stop goes to break-even and the rest trails 1.5x ATR below the best close |
| `sneaky_pivot` | 15m | Range High / Low = yesterday's high / low, Swing High / Low = the next structural pivot beyond them; buys only at the lower boundary: an impulse drop taps the Range / Swing Low, the next candle closes green, a buy-stop sits at its high; stop at the pullback's lowest wick; target the Range High |
| `fib_fvg` | 4H | after an impulse (swing low to swing high), entries only in the 0.705-0.886 discount zone, on a liquidity grab (a wick below 0.886 or the prior swing low that closes back inside) or a re-test of an unfilled fair value gap; a close below 0.886 cancels the setup. Optional: footprint absorption + a 400% delta expansion, and Elliott's hard rules |

All three keep the 2:1 minimum reward-to-risk.

**Every night at 02:00 UTC** (or **Run research now**) the lab:

1. **Prepares** the cached candles.
2. **Trains:** runs a *rolling walk-forward*. It picks the best of a dozen parameter variants
   on 252 days, trades it on the next 6 months, then rolls on 6 months. Only the unseen
   months count.
3. **Checks for look-ahead.** Each sampled signal must reappear when the history ends at
   its own bar, and signals must not change when later data is cut off. A variant earning
   more than +1,000%/year or a Sharpe above 6 is treated as a leak and discarded.
4. **Stress-tests** each survivor with 20 random single-day crashes of -5% to -15%.
5. **Reports:** writes `lab/leaderboard.json` and a plain-English `lab/program.md`.

A variant becomes a **proposal** only if its out-of-sample Sharpe beats 1.5 and the version
you already approved. Nothing changes by itself: press **Approve for paper**, then run it on
a paper bot:

```json
{"engine": "lab", "mode": "paper", "pairs": ["BTC/USDT"], "state_dir": "trendbot_state/nnfx-paper",
 "lab": {"strategy": "nnfx", "params": {}}}
```

(Use a pair no other bot on the same account trades.) **Incubation:** a testnet or live lab
bot refuses to start until that strategy has paper-traded for at least 30 days. The card
shows the days so far. Lab bots use the same capital protection, kill switch, dashboard and
terminal as the main bot. Terminal: `lab`.

**Learnings file.** Besides `learnings.md`, the bot writes `learnings.txt`: one plain line
per stopped-out trade plus the learned rules. It checks them before every entry (through the
learned rules and Chantisimo's recall), so a mistake that keeps costing money is not
repeated.

## 7. Running up to 10 bots (fleet)

`setup` also creates `fleet.json` and a `bots/` folder with three example bots: two paper bots
and a testnet bot whose brain learns from the paper core bot (section 6a).
Add up to 10 bots, one settings file each, then start the fleet dashboard:

| OS | command |
|---|---|
| macOS / Linux | `research/trendbot/scripts/start_fleet.sh` |
| Windows | `powershell -ExecutionPolicy Bypass -File research\trendbot\scripts\start_fleet.ps1` |

The **Bots** table:

- lists every bot with its status, mode, equity, return, trades, avg R and open positions;
- each row has **Start / Stop** and **View** (the rest of the page then shows that bot);
- the header has **Start all / Pause all / Stop all**.

**Approval beyond 5:**

- when 5 bots are already running, starting another does not start it;
- instead a pop-up lists exactly which bots would start, their modes and pairs, and flags LIVE ones;
- you must type **APPROVE** within 2 minutes;
- every approval is logged to `fleet_approvals.jsonl`;
- the server enforces this, so it cannot be bypassed from outside the page;
- 10 is the hard maximum.

**Safety checks when the fleet loads:**

- **Separate state:** every bot needs its own state folder.
- **One bot per pair per account:** bots on the same exchange and mode may not trade the same
  pair. They would fight over one balance.
- **No split cluster:** BTC/ETH/BNB must stay in one bot, because their shared risk budget
  (R6) is enforced inside a bot.
- **Share of the balance:** each bot has its own `starting_equity`. On a shared exchange
  account, set it to that bot's share of the balance.

Coins other than BTC/ETH/BNB must be configured in the bot's `strategy.pair_risk`, at most
1% risk per trade (see `bots/binance-sol-paper.json`).

## 8. Signal layers, data sources and weekly retraining

Every layer can only **block** an entry that already passed the nine strategy rules. It is
switched on only after it helps on data it never saw:

1. It is trained on the older 70% of the price history, plus the paper, testnet and live
   trades that closed in that period.
2. It is then tested on the newer 30%.
3. It must raise average R there by at least 0.05R and beat 95% of random vetoes that remove
   the same number of trades (p ≤ 0.05).

Otherwise it stays off.

| layer | what it is | extra install (optional) |
|---|---|---|
| `gbm` | gradient-boosted trees on the entry features | XGBoost is used if installed, built-in otherwise |
| `lstm` | LSTM over the last 32 candles | `torch` |
| `rl` | reinforcement-learning agent: learns take/skip values from every signal's outcome, including vetoed and paper-trade signals, and updates after every close | none |
| `hmm_regime` | Gaussian hidden Markov model of crash/bear/neutral/bull regimes, forward-filtered (no look-ahead) | none |
| `fear_greed` | Fear & Greed index | none |
| `news_sentiment` | news RSS, Reddit and CryptoPanic headlines scored by FinBERT or a built-in lexicon | `transformers` for FinBERT |
| `orderflow` | footprint: buy/sell delta, 400% imbalances, absorption | none |

The DEX scanner (`dex_scan.py`) is separate:

- it checks DEXScreener, RugCheck and Solana holder concentration;
- it ranks memecoins into a watchlist with risk flags: liquidity, can't-exit, top-10 holders
  > 20%, pair age, honeypot hints;
- it is **analysis only**: the bot never trades DEX tokens and holds no wallet keys;
- enable it with `"dex": {"enabled": true}`, or run
  `python -m research.trendbot.dex_scan --chain solana --from-boosts`.

The optional ML libraries are listed in `research/trendbot/requirements-ml.txt`.

**Automatic jobs.** While the bot runs, it starts two background jobs on its own. You can
also trigger them from the dashboard with **Collect data now** and **Retrain now**.

| job | when | what it does |
|---|---|---|
| **collect** | hourly | Fear & Greed, news, Reddit, CryptoPanic, exchange trades for order flow, DEX scan |
| **traders** | every 5 minutes (if `traders.json` exists) | re-reads every trader, re-ranks them, lists copy signals |
| **verify** | every 15 minutes | runs the verification agents |
| **retrain** | weekly, Sunday 01:00 UTC | retrains and re-validates every layer on the latest candles and journal (paper trades count), switches layers on or off, writes `layers.json` + `models/`; the bot reloads them |

Any layer can be switched off from the **Signal layers** table.

Some layers only start working once their data exists:

- **`news_sentiment`:** needs 30+ days of collected headlines, because news that was never
  collected can't be backtested.
- **`orderflow`:** needs order-flow history. Backfill it from Binance's public trade files
  with `"orderflow": {"history_months": 6}`, or let the bot collect it live.

**API keys:**

| source | key needed? |
|---|---|
| exchange (testnet / live trading) | yes: add them in the dashboard (Connections), or in `.env` |
| TradingView or another MCP server | only if the server asks for a sign-in: paste its token in Connections |
| Fear & Greed (alternative.me) | no |
| news RSS feeds, Reddit public JSON | no |
| CryptoPanic news | optional free key: `CRYPTOPANIC_TOKEN=` in `.env` |
| DEXScreener, RugCheck | no |
| Solana holder data | no; optionally `SOLANA_RPC_URL=` a Helius/QuickNode URL for higher limits |
| Binance aggTrades history (order flow) | no |

**TradingView MCP:** add it in the dashboard, under Connections → MCP servers → TradingView
(section 4a). The bot then calls its tools itself; it does not need Claude. Separately,
`claude mcp add --transport http mcp-tradingview https://mcp.tradingview.com/mcp` connects
it to **Claude Code**, if you also want Claude to read TradingView while you work.

## 9. Tests (optional)

```bash
.venv/bin/pip install pytest
.venv/bin/python -m pytest research/trendbot/tests -q
```

The suite runs offline against synthetic data and a fake exchange. It passes on Python 3.11,
3.12 and 3.13.
