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

1. **Recalls** the 12 most similar past trades, by RSI, volume, EMA gap, distance to EMA200,
   hour and pair, and what happened to them.
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
