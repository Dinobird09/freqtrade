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
- **`.env`:** only for `testnet` and `live`:
  - set `TRENDBOT_API_KEY` and `TRENDBOT_API_SECRET`;
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

## 7. Tests (optional)

```bash
.venv/bin/pip install pytest
.venv/bin/python -m pytest research/trendbot/tests -q
```

The suite runs offline against synthetic data and a fake exchange. It passes on Python 3.11,
3.12 and 3.13.
