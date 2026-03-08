# CLAUDE.md - Dexscreener Unofficial CLI + MCP + Skills

## What This Is

An **unofficial** CLI scanner, MCP server, and AI skill for Dexscreener token signals. Not affiliated with or endorsed by Dexscreener. The CLI is the primary product and the best live experience. MCP and `SKILL.md` exist to help agents guide users and operate the scanner in natural language.

All APIs used are free and public. The scanner scores tokens 0-100 based on volume, liquidity, momentum, and flow pressure across the main supported chains.

## Quick Commands

```bash
ds quickstart --shell bash --goal live
ds doctor
ds hot --chains solana,base --limit 10
ds new-runners-watch --chain=solana --watch-chains=solana,base --profile=discovery --max-age-hours=48 --include-unknown-age --interval=2
./.venv/bin/dexscreener-mcp
```

Windows `cmd.exe` equivalents:

```cmd
.\.venv\Scripts\ds.exe quickstart --shell cmd --goal live
.\.venv\Scripts\ds.exe doctor
.\.venv\Scripts\ds.exe hot --chains=solana,base --limit=10
.\.venv\Scripts\ds.exe new-runners-watch --chain=solana --watch-chains=solana,base --profile=discovery --max-age-hours=48 --include-unknown-age --interval=2
.\.venv\Scripts\dexscreener-mcp.exe
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
- **Quickstart surfaces**: use `ds quickstart`, `get_cli_quickstart`, `cli_quickstart_guide`, and `dexscreener://cli-guide`
- **State**: JSON files in ~/.dexscreener-cli/ (presets.json, tasks.json, runs.json)
- **Live mode**: polling live public APIs, not websocket streaming
- **Cache default**: Dex cache TTL defaults to 10 seconds and can be overridden with `DS_CACHE_TTL_SECONDS`

## UI Style Rules (read docs/UI_UX_SPEC.md for full guide)

- **All visual code in ui.py** - cli.py only imports, never hardcodes colors or Unicode
- **Tables**: `box.SIMPLE_HEAVY`, `row_styles=["", f"on {C_ROW_ALT}"]`, `border_style=C_BORDER`
- **Panels**: `box.HEAVY`, `border_style=C_BORDER`
- **Row alternation**: black / `C_ROW_ALT` (`#1e2029`). Never override with per-row `style=` in one-shot scans
- **Use ui.py helpers**: `_rank_badge()`, `_momentum_text()`, `_age_badge()`, `_vol_heat()`, etc.
- **Wrap Unicode** in `_safe_text()`

## Testing

```bash
./.venv/bin/ds doctor
./.venv/bin/ds hot --chains=solana,base --limit=5
./.venv/bin/ds quickstart --shell bash --goal live
```

## Dependencies

Python 3.11+, httpx, rich, typer, mcp, python-dotenv. Optional: MORALIS_API_KEY env var for holder data.
