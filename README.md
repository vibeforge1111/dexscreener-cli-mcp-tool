# dexscreener-cli-mcp-tool

Visual terminal scanner + MCP server for Dexscreener signals, with local presets and task workflows.

## Why this exists
Dexscreener is mostly used for:
1. Fast discovery of hot tokens and active pools.
2. Reading momentum and liquidity context before entries.
3. Tracking trend shifts across chains without opening many tabs.

This tool replicates that flow in terminal form with chain filters, scoring, and live watch mode.

## What it does
1. Pulls discovery seeds from:
   - `/token-boosts/top/v1`
   - `/token-boosts/latest/v1`
   - `/token-profiles/latest/v1`
2. Expands each token into tradable pair data via:
   - `/token-pairs/v1/{chainId}/{tokenAddress}`
3. Scores candidates by volume, activity, liquidity, momentum, and flow pressure.
4. Renders a clean board in terminal with:
   - leaderboard
   - chain heat summary
   - flow/risk summary
5. Supports named presets and reusable scan tasks.
6. Exposes same scan + task flows as MCP tools.

## Important API constraints
Dexscreener official limits:
1. `60 rpm`: token profiles, boosts, orders endpoints
2. `300 rpm`: search, pairs, token-pairs endpoints

This project includes:
1. Separate rate-limit buckets (60/300).
2. Short TTL cache.
3. Retry/backoff for transient API failures.

## Holder distribution note
Public Dexscreener API does not expose direct holder breakdown data.  
This tool shows **proxy concentration signals** (liquidity-to-market-cap, volume-to-liquidity, buy/sell imbalance) and labels them clearly as heuristic.

## Product docs
1. [PRD](docs/PRD.md)
2. [System Architecture](docs/SYSTEM_ARCHITECTURE.md)
3. [UI/UX Spec](docs/UI_UX_SPEC.md)
4. [Implementation Plan](docs/IMPLEMENTATION_PLAN.md)

## Install
```bash
cd dexscreener-cli-mcp-tool
python -m venv .venv
. .venv/Scripts/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e .
```

## CLI usage
One-shot scan:
```bash
ds hot --chains solana,base --limit 20
```

Live watch board:
```bash
ds watch --chains solana,base,ethereum --interval 7
```

Inspect token:
```bash
ds inspect DRLNhjM7jusYFPF1qade1dBD1qhgds7oAfdKs51Vpump --chain solana
```

Inspect pair:
```bash
ds inspect 3p4oBgqdrpYUVjeGgkv58BPfZ4MDMRXPUYEvdrpXfhLK --chain solana --pair
```

Search:
```bash
ds search solana --limit 10
```

God prompt:
```bash
ds god-prompt
```

Machine output:
```bash
ds hot --json
```

Use a preset:
```bash
ds hot --preset fast-sol
```

Preset lifecycle:
```bash
ds preset save fast-sol --chains solana,base --limit 8 --min-liquidity-usd 25000 --min-volume-h24-usd 70000 --min-txns-h1 40
ds preset list
ds preset show fast-sol
```

Task lifecycle:
```bash
ds task create scout-sol --preset fast-sol --notes "baseline runner scan"
ds task list
ds task run scout-sol
ds task status scout-sol running
ds task status scout-sol done
```

## MCP usage
Run MCP server on stdio:
```bash
dexscreener-mcp
```

Exposed tools:
1. `scan_hot_tokens`
2. `search_pairs`
3. `inspect_token`
4. `save_preset`
5. `list_presets`
6. `create_task`
7. `list_tasks`
8. `run_task_scan`

## Recommended default profile
For spotting new runners with manageable noise:
1. `--chains solana,base,bsc`
2. `--min-liquidity-usd 35000`
3. `--min-volume-h24-usd 90000`
4. `--min-txns-h1 80`
5. `--limit 20`
