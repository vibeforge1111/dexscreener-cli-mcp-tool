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
5. [Daemon Deployment](docs/DAEMON_DEPLOYMENT.md)
6. [Desk Alpha Opinions](docs/DESK_ALPHA_OPINIONS.md)
7. [Ship Priority Status](docs/SHIP_PRIORITY_STATUS.md)

## Install
```bash
cd dexscreener-cli-mcp-tool
python -m venv .venv
. .venv/Scripts/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e .
```

## CLI usage
One-command local showcase (runs alpha drops, watch demo, and top-new):
```bash
powershell -ExecutionPolicy Bypass -File .\showcase.ps1
```

If your terminal is narrow and tables truncate with `...`, force compact layout:
```bash
$env:DS_TABLE_MODE="compact"   # PowerShell
ds alpha-drops --chains base,solana --profile balanced
```

Optional width override for auto layout:
```bash
$env:DS_TABLE_WIDTH="120"
```

One-shot scan:
```bash
ds hot --chains solana,base --limit 20
```

Top AI tokens on Base (color leaderboard):
```bash
ds ai-top --chain base --limit 10
```

Top new coins by 24h volume in the last 7 days:
```bash
ds top-new --chain base --days 7 --limit 10
```

Stricter new-coin leaderboard (more tradable and activity-aware):
```bash
ds top-new --chain base --days 7 --limit 10 --min-liquidity-usd 25000 --min-volume-h24-usd 1000 --min-txns-h24 50
```

Stricter tradable AI leaderboard:
```bash
ds ai-top --chain base --limit 10 --min-liquidity-usd 25000 --min-volume-h24-usd 10000 --min-txns-h1 5
```

Best 10 new runners of the day on Base:
```bash
ds new-runners --chain base --limit 10 --max-age-hours 24
```

Higher-signal profile (quality gates + sort by readiness):
```bash
ds new-runners \
  --chain base \
  --profile balanced \
  --limit 10 \
  --max-age-hours 24 \
  --sort-by readiness \
  --max-vol-liq-ratio 60 \
  --min-breakout-readiness 55 \
  --min-relative-strength -10 \
  --decay-filter \
  --min-half-life-minutes 6 \
  --min-decay-ratio 0.35
```

Realtime alpha-drop board (Base + Solana):
```bash
ds alpha-drops --chains base,solana --profile balanced --limit 10
```

Realtime alpha-drop watch with notifications:
```bash
ds alpha-drops-watch \
  --chains base,solana \
  --profile balanced \
  --interval 6 \
  --alert-cooldown-seconds 300 \
  --alert-max-per-hour 8 \
  --discord-webhook-url https://discord.com/api/webhooks/...
```

Show profile thresholds by chain:
```bash
ds profiles --chains base,solana
```

Inspect runtime rate-budget stats:
```bash
ds rate-stats --json
```

Live new-runner rotation board (cards + rank movers):
```bash
ds new-runners-watch --chain base --limit 10 --max-age-hours 24 --interval 6
```

Multi-chain live board with keyboard controls:
```bash
ds new-runners-watch --chain base --watch-chains base,solana,ethereum --limit 10 --interval 6
```

Watch hotkeys:
1. `1-9` switch active chain from `--watch-chains`
2. `s` cycle sort mode (`score`, `readiness`, `rs`, `volume`, `momentum`)
3. `j` / `k` change selected row
4. `c` copy selected token/pair/url to clipboard

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
ds task create scout-sol --preset fast-sol --interval-seconds 60 --notes "baseline runner scan"
ds task list
ds task run scout-sol
ds task configure scout-sol --alert-min-score 78 --discord-webhook-url https://discord.com/api/webhooks/...
ds task test-alert scout-sol --no-with-scan
ds task runs --task scout-sol --limit 30
ds task status scout-sol running
ds task status scout-sol done
```

Run scheduler daemon:
```bash
ds task daemon --all --poll-seconds 5 --default-interval-seconds 120
```

Run one scheduler cycle and exit:
```bash
ds task daemon --all --once
```

Task alert channel options:
1. `--webhook-url` (generic JSON webhook)
2. `--discord-webhook-url`
3. `--telegram-bot-token` + `--telegram-chat-id`
4. `--alert-min-score`
5. `--alert-cooldown-seconds`
6. `--alert-template`
7. `--alert-top-n`
8. `--alert-min-liquidity-usd`
9. `--alert-max-vol-liq-ratio`
10. `--alert-blocked-terms`
11. `--alert-blocked-chains`
12. `--webhook-extra-json` (JSON object)

State backup/restore:
```bash
ds state export --path ./dexscreener-state-export.json
ds state import --path ./dexscreener-state-export.json --mode merge
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
4. `get_rate_budget_stats`
5. `save_preset`
6. `list_presets`
7. `create_task`
8. `list_tasks`
9. `run_task_scan`
10. `run_due_tasks`
11. `test_task_alert`
12. `list_task_runs`
13. `export_state_bundle`
14. `import_state_bundle`

MCP resources and prompts:
1. resources: `dexscreener://profiles`, `dexscreener://presets`, `dexscreener://tasks`
2. prompts: `alpha_scan_plan`, `runner_triage`

## Recommended default profile
For spotting new runners with manageable noise:
1. `--chains solana,base,bsc`
2. `--min-liquidity-usd 35000`
3. `--min-volume-h24-usd 90000`
4. `--min-txns-h1 80`
5. `--max-vol-liq-ratio 60`
6. `--limit 20`
