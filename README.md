# Dexscreener Unofficial CLI + MCP + Skills

**100% free to use.** All APIs included are public and free - no API keys required to get started. Optional Moralis key unlocks holder data.

A visual terminal scanner, MCP server, and AI skill for Dexscreener token signals. **Unofficial** - not affiliated with or endorsed by Dexscreener.

Scans hot tokens across Solana, Base, Ethereum, BSC, and Arbitrum. Scores them by volume, liquidity, momentum, and flow pressure. Use it from the terminal, connect it to AI agents via MCP, or load it as a skill in Claude/Codex/OpenClaw.

**Free APIs used:**
- [Dexscreener API](https://docs.dexscreener.com/) - token data, pairs, profiles, boosts
- [GeckoTerminal API](https://www.geckoterminal.com/) - trending pools, new tokens
- [Blockscout API](https://docs.blockscout.com/) - holder counts (Base chain)
- [Honeypot.is API](https://honeypot.is/) - holder counts (Solana, ETH, BSC)
- [Moralis API](https://moralis.io/) - holder counts (optional, requires free key)

---

## Quick Install

```bash
git clone https://github.com/vibeforge1111/dexscreener-cli-mcp-tool.git
cd dexscreener-cli-mcp-tool
```

**Windows:**
```
install.bat
```

**Mac/Linux:**
```bash
chmod +x install.sh && ./install.sh
```

**Manual:**
```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .
```

---

## First Run

```bash
ds setup       # 5-question wizard to calibrate your scanner
ds hot         # Scan hot tokens with your settings
```

The setup wizard asks about your chains, trading style, and filter preferences. Your choices are saved and auto-loaded on every scan.

---

## Commands

### Scanning

| Command | What it does |
|---------|-------------|
| `ds hot` | Scan hot tokens across your configured chains |
| `ds watch` | Live auto-refreshing dashboard |
| `ds search <query>` | Search tokens by name, symbol, or address |
| `ds top-new` | Top new tokens by 24h volume |
| `ds new-runners` | Fresh token runners with momentum scoring |
| `ds alpha-drops` | Alpha-grade new drops with breakout scoring |
| `ds ai-top` | AI-themed token leaderboard |
| `ds inspect <address>` | Deep-dive on a specific token |

### Configuration

| Command | What it does |
|---------|-------------|
| `ds setup` | Interactive onboarding wizard |
| `ds doctor` | Diagnose issues and verify your setup |
| `ds update` | Pull latest code and reinstall |
| `ds profiles` | Show filter thresholds per profile |
| `ds preset save <name>` | Save current filters as a named preset |
| `ds preset list` | List all saved presets |
| `ds preset show <name>` | Show preset details as JSON |
| `ds preset delete <name>` | Delete a preset |

### Tasks & Alerts

| Command | What it does |
|---------|-------------|
| `ds task create <name>` | Create a scheduled scan task |
| `ds task list` | List all tasks |
| `ds task run <name>` | Run a task once |
| `ds task daemon` | Run scheduler for all due tasks |
| `ds task configure <name>` | Add alerts (Discord, Telegram, webhook) |
| `ds task test-alert <name>` | Send a test alert |

### Output

Add `--json` to any scan command for machine-readable JSON output.

```bash
ds hot --json
ds search pepe --json
```

---

## Examples

**Solana-only scan, 10 results:**
```bash
ds hot --chains solana --limit 10
```

**Multi-chain live dashboard, refresh every 7 seconds:**
```bash
ds watch --chains solana,base,ethereum --interval 7
```

**Discovery mode (loose filters to find more tokens):**
```bash
ds hot --chains solana --limit 20 --min-liquidity-usd 10000 --min-volume-h24-usd 10000 --min-txns-h1 5
```

**Use a saved preset:**
```bash
ds preset save degen --chains solana,base --limit 15 --min-liquidity-usd 10000
ds hot --preset degen
```

**Set up Discord alerts:**
```bash
ds task create scout --preset degen --interval-seconds 60
ds task configure scout --discord-webhook-url https://discord.com/api/webhooks/...
ds task daemon --all
```

---

## MCP Server

The MCP server exposes all scanning functionality to AI agents (Claude, Codex, etc.).

### Start the server

```bash
dexscreener-mcp
```

### Claude Desktop config

Add to your `claude_desktop_config.json`:

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

On Mac/Linux use `.venv/bin/dexscreener-mcp` instead.

### MCP Tools

| Tool | Description |
|------|-------------|
| `scan_hot_tokens` | Scan and rank hot tokens by chain with scoring |
| `search_pairs` | Search Dexscreener pairs by name/symbol/address |
| `inspect_token` | Deep-dive on a token with concentration proxies |
| `save_preset` | Save a named scan preset |
| `list_presets` | List all saved presets |
| `create_task` | Create a scheduled scan task with alerts |
| `list_tasks` | List all scan tasks |
| `run_task_scan` | Run a task scan and return ranked results |
| `run_due_tasks` | Run one scheduler cycle for all due tasks |
| `test_task_alert` | Send a test alert through task channels |
| `list_task_runs` | List task run history |
| `export_state_bundle` | Export all presets/tasks/runs as JSON |
| `import_state_bundle` | Import a state bundle |
| `get_rate_budget_stats` | Get API rate limit and budget stats |

### MCP Resources

| URI | Content |
|-----|---------|
| `dexscreener://profiles` | Available scan profiles with thresholds |
| `dexscreener://presets` | Saved scan presets |
| `dexscreener://tasks` | Saved scan tasks |

### MCP Prompts

| Prompt | Purpose |
|--------|---------|
| `alpha_scan_plan` | Generate an execution-ready scan plan |
| `runner_triage` | Triage a token candidate for momentum trading |

---

## AI Skill Usage

This tool works as a skill for AI coding agents. Load the `SKILL.md` file to teach any agent how to use the CLI and MCP tools with natural language.

**Example natural language queries an agent can handle:**
- "What are the hottest tokens on Solana right now?"
- "Find me new tokens on Base with high volume"
- "Set up a scan task with Discord alerts for Solana alpha"
- "Search for pepe tokens and show me the top results"
- "What's the liquidity and volume for this token address?"

See `SKILL.md` for the full skill specification.

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `MORALIS_API_KEY` | No | Enables Moralis as fallback holder data provider |
| `DS_TABLE_MODE` | No | Set to `compact` for narrow terminals |
| `DS_TABLE_WIDTH` | No | Override auto-detected terminal width |

Create a `.env` file in the project root:
```
MORALIS_API_KEY=your_key_here
```

---

## Scan Profiles

Three built-in filter profiles, applied with chain-specific multipliers:

| Profile | Min Liquidity | Min 24h Volume | Min Txns/h |
|---------|--------------|----------------|------------|
| **strict** | $40,000 | $120,000 | 110 |
| **balanced** | $28,000 | $70,000 | 55 |
| **discovery** | $15,000 | $20,000 | 12 |

Use `ds profiles --chains solana,base` to see chain-adjusted values.

---

## How Scoring Works

Each token gets a 0-100 score based on 8 weighted components:

1. **Volume velocity** - How fast volume is growing
2. **Transaction velocity** - Transaction count momentum
3. **Relative strength** - Performance vs chain average
4. **Breakout readiness** - Compression pattern detection
5. **Boost velocity** - Dexscreener boost activity rate
6. **Momentum decay** - How well momentum sustains
7. **Liquidity depth** - Pair liquidity health
8. **Flow pressure** - Buy vs sell imbalance

---

## Holder Data

Holder counts are fetched from multiple providers in order:

1. **GeckoTerminal** - Free, no key, all chains
2. **Moralis** - EVM chains (requires `MORALIS_API_KEY`)
3. **Blockscout** - ETH + Base, free
4. **Honeypot.is** - EVM chains, free

Results are cached for 15 minutes to reduce API load.

---

## API Rate Limits

Dexscreener enforces:
- **60 rpm** for token profiles, boosts, orders
- **300 rpm** for search, pairs, token-pairs

The scanner uses separate rate-limit buckets, short TTL caching, and retry/backoff to stay within limits.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| No tokens found | Lower filters: `--min-liquidity-usd 10000 --min-txns-h1 5` |
| Only Solana results | Expected when Solana dominates Dexscreener boosts. Try `--chains base` |
| Unicode garbled | Run `chcp 65001` (Windows) or use a modern terminal |
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
