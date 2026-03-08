# Dexscreener Unofficial CLI + MCP + Skills

![Dexscreener CLI Screenshot](assets/screenshot.png)

**100% free to use.** All APIs included are public and free - no Dexscreener API key required to get started. Optional free Moralis key unlocks holder data.

A visual terminal scanner, MCP server, and AI skill for Dexscreener token signals. **Unofficial** - not affiliated with or endorsed by Dexscreener.

Scans hot tokens across every chain Dexscreener supports. Scores them by volume, liquidity, momentum, and flow pressure. Use it from the terminal, connect it to AI agents via MCP, or load it as a skill in Claude/Codex/OpenClaw.

**Free APIs used:**
- [Dexscreener API](https://docs.dexscreener.com/) - token data, pairs, profiles, boosts
- [GeckoTerminal API](https://www.geckoterminal.com/) - trending pools, new tokens
- [Blockscout API](https://docs.blockscout.com/) - holder counts (Base chain)
- [Honeypot.is API](https://honeypot.is/) - holder counts (Solana, ETH, BSC)
- [Moralis API](https://moralis.io/) - holder counts (optional, requires free key)

---

## Quick Install

You need **Python 3.11+** and **Git** installed. Then follow these 3 steps.

If you are on Windows and do not usually use terminals, use **Command Prompt** first. It is the simplest path for this project.

### Windows: how to open Command Prompt

1. Press the `Windows` key
2. Type `Command Prompt`
3. Click the app named `Command Prompt`

You should see a window with a prompt like:

```text
C:\Users\YOUR_NAME>
```

All Windows examples below work in that window.

### Step 1: Clone the repo

Open a terminal and paste this:

```bash
git clone https://github.com/vibeforge1111/dexscreener-cli-mcp-tool.git
cd dexscreener-cli-mcp-tool
```

### Step 2: Run the installer

**Windows** - paste this in the same terminal:
```
install.bat
```

**Mac / Linux** - paste this instead:
```bash
chmod +x install.sh && ./install.sh
```

This creates a virtual environment and installs everything. Takes about 30 seconds.

### Step 3: First run

If you are on Windows Command Prompt, paste these exactly:

```cmd
cd /d C:\path\to\dexscreener-cli-mcp-tool
.\.venv\Scripts\ds.exe doctor
.\.venv\Scripts\ds.exe setup
.\.venv\Scripts\ds.exe hot --chains=solana,base --limit=10
```

If you are on Mac / Linux or you activated the environment already, the same flow is:

```bash
ds doctor
ds setup
ds hot --chains=solana,base --limit=10
```

`ds setup` is important on a fresh install. It creates your local default preset so scans feel sensible immediately.

### Common Windows mistake

If you see:

```text
Option '--profile' requires an argument.
```

you pressed `Enter` too early. Run the whole command on **one line**, for example:

```cmd
.\.venv\Scripts\ds.exe new-runners-watch --chain=solana --watch-chains=solana,base --profile=discovery --interval=2
```

Using `=` is the safest way to paste commands in Windows because the option and value stay attached.

<details>
<summary>Manual install (if the script doesn't work)</summary>

```bash
python -m venv .venv
```

Activate the environment:
```bash
# Mac / Linux:
source .venv/bin/activate

# Windows Command Prompt:
.venv\Scripts\activate.bat

# Windows PowerShell:
.venv\Scripts\Activate.ps1
```

Then install:
```bash
pip install -e .
```
</details>

That's it. The setup wizard saves your choices and auto-loads them on every scan.

## Windows Quick Start

If you just want a copy-paste path that works in `cmd.exe`, use this:

```cmd
cd /d C:\path\to\dexscreener-cli-mcp-tool
.\.venv\Scripts\ds.exe doctor
.\.venv\Scripts\ds.exe new-runners-watch --chain=solana --watch-chains=solana,base --profile=discovery --max-age-hours=48 --include-unknown-age --interval=2
```

If you want a shorter non-live test first:

```cmd
cd /d C:\path\to\dexscreener-cli-mcp-tool
.\.venv\Scripts\ds.exe hot --chains=solana,base --limit=10
```

---

## Commands

### One-Shot Scans

| Command | What it does |
|---------|-------------|
| `ds hot` | Scan hot tokens across your configured chains |
| `ds search <query>` | Search tokens by name, symbol, or address |
| `ds top-new` | Top new tokens by 24h volume |
| `ds new-runners` | Fresh token runners with momentum scoring |
| `ds alpha-drops` | Alpha-grade new drops with breakout scoring |
| `ds ai-top` | AI-themed token leaderboard |
| `ds inspect <address>` | Deep-dive on a specific token |

### Real-Time Live Dashboards

Three live modes that auto-refresh and keep your terminal updated. Press `Ctrl+C` to stop any of them.

Important: this project uses **live polling** of public APIs, not websocket streaming. The CLI now uses a rate-aware default Dex cache of **10 seconds**, which is tuned to Dexscreener's documented free limits. That means the screen can repaint faster than new upstream data arrives.

**`ds watch`** - Live hot runner board

The simplest live mode. Shows the same hot runner table as `ds hot`, but refreshes automatically.

```bash
ds watch                                        # All chains, refreshes every 7s
ds watch --chains solana --limit 10 --interval 5  # Solana only, 5s refresh
ds watch --preset my-degen                       # Use your custom profile
```

**`ds new-runners-watch`** - Live new runner tracker

Full-screen dashboard with keyboard controls. Shows new tokens with rank movers, flow panels, and spotlight cards.

```bash
ds new-runners-watch --chain solana              # Watch Solana runners
ds new-runners-watch --chain base --interval 6   # Watch Base, 6s refresh
ds new-runners-watch --chain solana --watch-chains solana,base,ethereum  # Enable chain switching
```

Recommended right now:

```bash
ds new-runners-watch --chain=solana --watch-chains=solana,base --profile=discovery --max-age-hours=48 --include-unknown-age --interval=2
```

This is the most useful live board in the current build. It is more informative than `ds watch` and usually surfaces more names than `alpha-drops-watch`.

Keyboard shortcuts while running:
- `1-9` - Switch between chains (if `--watch-chains` is set)
- `s` - Cycle sort mode (score / readiness / relative strength / volume / momentum)
- `j/k` - Select a row up/down
- `c` - Copy selected token address to clipboard

**`ds alpha-drops-watch`** - Live alpha drop scanner with alerts

Same as new-runners-watch but focused on breakout-ready tokens. Can send alerts directly to Discord/Telegram as it finds them.

```bash
ds alpha-drops-watch --chains solana,base        # Watch for alpha drops
ds alpha-drops-watch --chains solana --discord-webhook-url https://discord.com/api/webhooks/YOUR/WEBHOOK  # With Discord alerts
ds alpha-drops-watch --chains solana --alert-min-score 75 --alert-cooldown-seconds 120  # Alert only high scores, 2min cooldown
```

**Tips for all live modes:**
- Use `--interval 5` for faster updates (default is 6-7 seconds)
- Use `--limit` to control how many tokens show (fewer = faster scans)
- Use `--profile discovery` to cast a wider net and see more tokens
- Use `--no-screen` (on new-runners-watch and alpha-drops-watch) to avoid fullscreen mode
- If Solana is quiet, switch to Base with `1` / `2` hotkeys or start on Base directly
- If a live board shows nothing, widen it with `--max-age-hours=48 --include-unknown-age --profile=discovery`

### Custom Scan Profiles

Create your own scan profiles with any combination of chains and filters. They persist across sessions.

```bash
# Create a custom profile
ds preset save my-degen --chains solana,base --limit 15 --min-liquidity-usd 8000 --min-txns-h1 5

# Use it in any scan
ds hot --preset my-degen
ds watch --preset my-degen

# List / inspect / delete profiles
ds preset list
ds preset show my-degen
ds preset delete my-degen
```

The 3 built-in profiles (strict / balanced / discovery) are always available. Your custom profiles sit on top.

### Setup & Maintenance

| Command | What it does |
|---------|-------------|
| `ds setup` | Interactive wizard - builds a "default" profile from 5 questions |
| `ds doctor` | Diagnose issues and verify your setup |
| `ds update` | Pull latest code and reinstall |
| `ds profiles` | Show built-in filter thresholds per chain |

### Tasks & Alerts

Set up automated scans that run on a schedule and alert you via Discord, Telegram, or webhooks.

| Command | What it does |
|---------|-------------|
| `ds task create <name>` | Create a scheduled scan task |
| `ds task list` | List all tasks |
| `ds task run <name>` | Run a task once |
| `ds task daemon` | Run scheduler for all due tasks |
| `ds task configure <name>` | Add alerts (Discord, Telegram, webhook) |
| `ds task test-alert <name>` | Send a test alert |

### Output

Use `--json` on supported one-shot commands for machine-readable output. The main JSON-friendly commands are `ds hot`, `ds search`, `ds inspect`, `ds task run`, and `ds rate-stats`.

```bash
ds hot --json
ds search pepe --json
ds inspect So11111111111111111111111111111111111111112 --chain solana --json
```

---

## What Can I Do With This?

### "I just want to see what's hot right now"

```bash
ds hot
```

Shows the top trending tokens across all chains, scored and ranked. Done.

### "I only care about Solana"

```bash
ds hot --chains solana --limit 10
```

### "Show me tokens on Base too"

```bash
ds hot --chains solana,base --limit 15
```

### "I want a live feed that updates automatically"

```bash
ds new-runners-watch --chain=solana --watch-chains=solana,base --profile=discovery --max-age-hours=48 --include-unknown-age --interval=2
```

This is the best live mode for most users. It refreshes the board continuously and gives chain switching, rank movers, spotlight cards, and change cues.

### "Show me brand new tokens that just launched"

```bash
ds new-runners --chain solana
ds top-new --chain base
```

### "I want a live feed of new launches only"

```bash
ds new-runners-watch --chain=solana --watch-chains=solana,base --profile=discovery --max-age-hours=48 --include-unknown-age --interval=2
```

### "Find me alpha - new drops with breakout potential"

```bash
ds alpha-drops --chains solana,base
```

Or live with auto-refresh:
```bash
ds alpha-drops-watch --chains solana,base
```

### "Search for a specific token"

```bash
ds search pepe
ds search 0x6982508145454ce325ddbe47a25d4ec3d2311933    # by address
```

### "I found a token, give me everything on it"

```bash
ds inspect So11111111111111111111111111111111111111112 --chain solana
```

### "I want to filter differently than the defaults"

Save your own profile and reuse it everywhere:

```bash
ds preset save my-degen --chains solana,base --limit 20 --min-liquidity-usd 5000 --min-txns-h1 3

ds hot --preset my-degen
ds watch --preset my-degen
```

### "Alert me on Discord when something hot appears"

```bash
# 1. Save your filter profile
ds preset save scout --chains solana --limit 10 --min-liquidity-usd 10000

# 2. Create a task that scans every 60 seconds
ds task create my-alerts --preset scout --interval-seconds 60

# 3. Add your Discord webhook
ds task configure my-alerts --discord-webhook-url https://discord.com/api/webhooks/YOUR/WEBHOOK

# 4. Test it
ds task test-alert my-alerts

# 5. Start the scanner
ds task daemon --all
```

Works the same with Telegram:
```bash
ds task configure my-alerts --telegram-bot-token YOUR_BOT_TOKEN --telegram-chat-id YOUR_CHAT_ID
```

### "I want JSON output for my own scripts"

```bash
ds hot --json
ds search pepe --json
ds inspect So11111111111111111111111111111111111111112 --chain solana --json
ds hot --chains solana --limit 5 --json > tokens.json
```

### "I want an AI agent to use this"

Start the MCP server and connect it to Claude, Codex, or any MCP-compatible agent:
```bash
dexscreener-mcp
```

On Windows, if you have **not** activated the virtual environment, run the full path instead:

```cmd
cd /d C:\path\to\dexscreener-cli-mcp-tool
.\.venv\Scripts\dexscreener-mcp.exe
```

Then ask in natural language: "What's hot on Solana?" or "Find new tokens on Base with high volume."

---

## MCP Server - Use It With AI Agents

### Why use the MCP server?

The MCP server lets you talk to your scanner in plain English through any AI agent. Instead of remembering CLI flags, you just ask:

- "What's pumping on Solana right now?"
- "Find me degen plays on Base with low liquidity"
- "Save a preset called sol-degen for Solana discovery mode"
- "Set up alerts for when something scores above 80"

The agent calls the right MCP tool with the right parameters. You get the same data as the CLI, but through conversation.

### How to set it up

**Step 1:** Make sure the CLI is installed (see [Quick Install](#quick-install) above).

**Step 2:** Add the MCP server to your AI agent's config.

**Claude Desktop** - add to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "dexscreener": {
      "command": "path/to/dexscreener-cli-mcp-tool/.venv/Scripts/dexscreener-mcp",
      "args": []
    }
  }
}
```

On Mac/Linux use `.venv/bin/dexscreener-mcp` instead of `.venv/Scripts/dexscreener-mcp`.

If you want an exact Windows example, replace the command path with your real local path:

```json
{
  "mcpServers": {
    "dexscreener": {
      "command": "C:\\path\\to\\dexscreener-cli-mcp-tool\\.venv\\Scripts\\dexscreener-mcp.exe"
    }
  }
}
```

**Claude Code** - add to your `.mcp.json` or project settings:

```json
{
  "mcpServers": {
    "dexscreener": {
      "command": "path/to/dexscreener-cli-mcp-tool/.venv/Scripts/dexscreener-mcp"
    }
  }
}
```

**Any MCP-compatible agent** (Codex, OpenClaw, etc.) - point it at the `dexscreener-mcp` binary in the `.venv` folder. It communicates over stdio.

**Step 3:** Start talking.

### Natural language examples

Once connected, just ask in plain English:

| You say | What happens |
|---------|-------------|
| "What's hot right now?" | Scans all chains and returns top scored tokens |
| "Show me Solana tokens" | Scans Solana only |
| "Find tokens on Base with high volume" | Scans Base with volume-focused filters |
| "Search for pepe" | Searches Dexscreener for pepe tokens |
| "Tell me about this token: 0x..." | Inspects the specific token address |
| "What's the score breakdown for BONK?" | Searches + inspects with analytics |
| "Save my current settings as degen-mode" | Creates a named preset |
| "Show me my presets" | Lists all saved presets |
| "Set up a task that scans Solana every minute" | Creates a scheduled task |
| "Add Discord alerts to my task" | Configures alert channels on a task |
| "Test my alerts" | Sends a test alert through configured channels |
| "What are the API limits looking like?" | Shows rate budget and usage stats |
| "Export my config" | Exports all presets, tasks, and history as JSON |

The agent figures out which MCP tool to call and what parameters to use. You don't need to know the tool names.

### All 14 MCP tools

For reference, these are the tools the agent has access to:

| Tool | What it does |
|------|-------------|
| `scan_hot_tokens` | Scan and rank hot tokens by chain with scoring |
| `search_pairs` | Search pairs by name, symbol, or address |
| `inspect_token` | Deep-dive on a specific token |
| `save_preset` | Save a named filter preset |
| `list_presets` | List saved presets |
| `create_task` | Create a scheduled scan task with alerts |
| `list_tasks` | List all scan tasks |
| `run_task_scan` | Run a task scan manually |
| `run_due_tasks` | Run all due scheduled tasks |
| `test_task_alert` | Test alert delivery |
| `list_task_runs` | View task run history |
| `export_state_bundle` | Export all config as JSON |
| `import_state_bundle` | Import a config bundle |
| `get_rate_budget_stats` | Check API rate limits and usage |

Plus 3 resources (`dexscreener://profiles`, `dexscreener://presets`, `dexscreener://tasks`) and 2 prompts (`alpha_scan_plan`, `runner_triage`).

---

## AI Skill File

For AI coding agents that use skill files (Claude Code, Codex, OpenClaw), load `SKILL.md` from this repo. It teaches the agent:

- When to activate (trigger phrases like "what's hot", "find me alpha", etc.)
- How to map natural language to the right tool calls
- Chain identification ("Solana" -> `solana`, "BSC" -> `bsc`)
- Filter profile selection based on user intent
- How to explain scores and present results
- Error handling and troubleshooting

```bash
# Point your agent at the skill file:
SKILL.md
```

On Windows, the actual path is:

```text
C:\path\to\dexscreener-cli-mcp-tool\SKILL.md
```

See `SKILL.md` for the full specification.

---

## APIs & Data Sources

Everything works out of the box with zero API keys. You can optionally add keys to unlock more data.

### What's included for free (no keys needed)

| API | What it provides | Rate Limit |
|-----|-----------------|------------|
| [Dexscreener](https://docs.dexscreener.com/) | All token/pair data, prices, volume, liquidity, boosts, profiles | 60-300 rpm |
| [GeckoTerminal](https://www.geckoterminal.com/) | Holder counts, trending pools, new token discovery | Free tier |
| [Blockscout](https://docs.blockscout.com/) | Holder counts for Ethereum and Base | Unlimited |
| [Honeypot.is](https://honeypot.is/) | Holder counts for all EVM chains | Free tier |

### Optional APIs you can add

| API | What it unlocks | How to get a key | Cost |
|-----|----------------|-----------------|------|
| [Moralis](https://moralis.io/) | Better holder data for all chains (EVM + Solana) | Sign up at moralis.io | Free tier available (40K requests/month) |

To add an optional key, create a `.env` file in the project root:
```
MORALIS_API_KEY=your_key_here
```

### Holder data coverage per chain

The scanner tries multiple providers in order until it gets a result:

| Chain | GeckoTerminal | Moralis (optional) | Blockscout | Honeypot.is |
|-------|:---:|:---:|:---:|:---:|
| **Solana** | yes | yes (with key) | - | - |
| **Ethereum** | yes | yes (with key) | yes | yes |
| **Base** | yes | yes (with key) | yes | yes |
| **BSC** | yes | yes (with key) | - | yes |
| **Arbitrum** | yes | yes (with key) | - | yes |
| **Polygon** | yes | yes (with key) | - | yes |
| **Optimism** | yes | yes (with key) | - | yes |
| **Avalanche** | yes | yes (with key) | - | yes |

Without any API keys, you still get holder counts on every chain through GeckoTerminal, Blockscout, and Honeypot.is. Adding a Moralis key gives you a more reliable fallback.

### How rate limiting works

Dexscreener enforces:
- **60 rpm** for token profiles, boosts, orders
- **300 rpm** for search, pairs, token-pairs

The scanner handles this automatically with separate rate-limit buckets, 20-second caching, and retry/backoff. Holder data is cached for 15 minutes. You don't need to worry about hitting limits.

---

## Extend & Customize

### Combine with free tools

Use scan results as a starting point, then layer on these complementary tools for a full workflow:

| Use Case | Tools | Free? |
|----------|-------|-------|
| Safety check before buying | [RugCheck.xyz](https://rugcheck.xyz/), [GoPlus](https://gopluslabs.io/), [Token Sniffer](https://tokensniffer.com/) | Yes |
| Whale watching | [Arkham](https://www.arkhamintelligence.com/), [DeBank](https://debank.com/) | Freemium |
| Execute trades | [Jupiter](https://jup.ag/) (Solana), [1inch](https://1inch.io/) (EVM), [Paraswap](https://www.paraswap.io/) | Yes |
| Chart analysis | [TradingView](https://www.tradingview.com/) | Yes |
| Social sentiment | [LunarCrush](https://lunarcrush.com/), Twitter/X search | Freemium |
| Portfolio tracking | [Zapper](https://zapper.xyz/), [DeBank](https://debank.com/) | Yes |
| Deeper analytics | [Defined.fi](https://www.defined.fi/), [DexTools](https://www.dextools.io/) | Freemium |

### Per-chain block explorers

| Chain | Explorer |
|-------|---------|
| Solana | [Solscan](https://solscan.io/), [Solana FM](https://solana.fm/) |
| Base | [BaseScan](https://basescan.org/) |
| Ethereum | [Etherscan](https://etherscan.io/) |
| BSC | [BscScan](https://bscscan.com/) |
| Arbitrum | [Arbiscan](https://arbiscan.io/) |

### Build your own workflow

**Scan, check, trade:**
```bash
# 1. Find hot tokens
ds hot --chains solana --json > tokens.json

# 2. Check safety on RugCheck.xyz or GoPlus
# 3. Trade via Jupiter (jup.ag) or 1inch
```

**Pipe to your bot or dashboard:**
```bash
# JSON output for scripts
ds hot --chains solana --limit 5 --json | your-script.py

# Webhook alerts to a custom bot
ds task create my-bot --chains solana --interval-seconds 60
ds task configure my-bot --webhook-url https://your-server.com/hook
```

**No-code automations:**
- Use the webhook URL with [n8n](https://n8n.io/) or [Zapier](https://zapier.com/) to pipe alerts into spreadsheets, databases, or messaging apps.

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `MORALIS_API_KEY` | No | Enables Moralis holder data (free tier: 40K req/month) |
| `DS_TABLE_MODE` | No | Set to `compact` for narrow terminals |
| `DS_TABLE_WIDTH` | No | Override auto-detected terminal width |

---

## Scan Profiles

Three built-in filter profiles, applied with chain-specific multipliers:

| Profile | Min Liquidity | Min 24h Volume | Min Txns/h | Good for |
|---------|--------------|----------------|------------|----------|
| **discovery** | $8,000 | $10,000 | 5 | Finding early gems, degen plays, micro-caps |
| **balanced** | $20,000 | $40,000 | 25 | General scanning, mix of safety and opportunity |
| **strict** | $35,000 | $90,000 | 50 | Established tokens only, blue-chip filtering |

Use `ds profiles --chains solana,base` to see chain-adjusted values.

You can also create your own profiles with `ds preset save` (see [Custom Scan Profiles](#custom-scan-profiles) above).

---

## How Scoring Works

Each token gets a 0-100 score based on 8 weighted components:

| Component | What it measures |
|-----------|-----------------|
| **Volume velocity** | How fast trading volume is growing |
| **Transaction velocity** | Rate of transaction count increase |
| **Relative strength** | Performance compared to the chain average |
| **Breakout readiness** | Price compression patterns (coiling before a move) |
| **Boost velocity** | Rate of Dexscreener boost activity |
| **Momentum decay** | Whether momentum is sustaining or fading |
| **Liquidity depth** | Health and depth of the liquidity pool |
| **Flow pressure** | Buy vs sell transaction imbalance |

**What the scores mean:** 80+ = very hot, 60-80 = interesting, 40-60 = moderate, below 40 = weak

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| No tokens found | Lower filters: `--min-liquidity-usd 10000 --min-txns-h1 5` |
| Only Solana results | Expected when Solana dominates Dexscreener boosts. Try `--chains base` |
| Unicode garbled | Run `chcp 65001` (Windows) or use a modern terminal |
| `Option '--profile' requires an argument` | You pressed Enter too early. Run `--profile=discovery` on the same line |
| `ds` is not recognized | Use `.\.venv\Scripts\ds.exe` on Windows if you did not activate the environment |
| Import errors | Run `ds doctor` then `ds update` |
| API timeouts | Check internet, run `ds doctor` to verify API connectivity |

Run `ds doctor` anytime to check your setup.

---

## Updating

```bash
ds update
```

Or manually:
```bash
git pull
pip install -e .
```

---

## Project Structure

```
dexscreener_cli/
  cli.py          - All CLI commands (Typer)
  ui.py           - Terminal rendering (Rich)
  scanner.py      - Token discovery and scanning
  scoring.py      - 8-component scoring engine
  models.py       - Data models (PairSnapshot, HotTokenCandidate)
  holders.py      - Multi-provider holder count fetching
  client.py       - Dexscreener API client with rate limiting
  config.py       - Constants and filter defaults
  state.py        - Preset/task persistence (JSON files)
  mcp_server.py   - MCP server exposing all tools
  alerts.py       - Discord/Telegram/webhook alerts
  task_runner.py   - Task execution and scheduling
  watch_controls.py - Keyboard controls for live mode
```

---

## License

MIT
