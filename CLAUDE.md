# CLAUDE.md - Dex Scanner

## What This Is

A Dexscreener CLI scanner + MCP server for discovering hot tokens across Solana, Base, Ethereum, BSC, and Arbitrum. Scores tokens 0-100 based on volume, liquidity, momentum, and flow pressure.

## Quick Commands

```bash
ds setup                           # First-run calibration wizard
ds hot --chains solana --limit 10  # Scan hot tokens
ds watch --interval 7              # Live dashboard
ds search pepe                     # Search tokens
ds doctor                          # Diagnose setup issues
ds update                          # Pull latest and reinstall
dexscreener-mcp                    # Start MCP server
```

## Project Structure

```
dexscreener_cli/
  cli.py          - CLI commands (Typer). Entry point: ds
  ui.py           - Terminal rendering (Rich). All visual code here.
  scanner.py      - Token discovery, scoring pipeline
  scoring.py      - 8-component scoring engine (0-100)
  models.py       - PairSnapshot, HotTokenCandidate, CandidateAnalytics
  holders.py      - Multi-provider holder counts (GeckoTerminal -> Moralis -> Blockscout -> Honeypot)
  client.py       - Dexscreener API client with rate limiting
  config.py       - Constants, ScanFilters dataclass
  state.py        - Presets/tasks persistence (~/.dexscreener-cli/)
  mcp_server.py   - MCP server (FastMCP). Entry point: dexscreener-mcp
  alerts.py       - Discord/Telegram/webhook alert delivery
  task_runner.py   - Task execution and scheduling
  watch_controls.py - Keyboard controls for live mode
```

## Key Architecture

- **Filter cascade**: `_resolved_filters()` in cli.py resolves: hardcoded defaults -> "default" preset -> explicit preset -> CLI flags
- **Scoring**: 8 weighted components in scoring.py produce a 0-100 score per token
- **Scan profiles**: strict/balanced/discovery baselines in cli.py with chain multipliers
- **UI separation**: Only cli.py imports from ui.py. All rendering in ui.py.
- **MCP server**: Mirrors CLI functionality via FastMCP tools in mcp_server.py
- **State**: JSON files in ~/.dexscreener-cli/ (presets.json, tasks.json, runs.json)

## Testing

```bash
ds hot --chains solana --limit 5    # Quick scan test
ds doctor                           # Health check
python -m dexscreener_cli hot --json  # JSON output test
```

## Dependencies

Python 3.11+, httpx, rich, typer, mcp, python-dotenv. Optional: MORALIS_API_KEY env var for holder data.
