# Rate-Limit Strategy (Dexscreener)

## Official limits used
As of March 3, 2026:
1. `60 rpm` endpoints:
   - `/token-profiles/latest/v1`
   - `/token-boosts/latest/v1`
   - `/token-boosts/top/v1`
   - `/orders/v1/{chainId}/{tokenAddress}`
2. `300 rpm` endpoints:
   - `/latest/dex/search`
   - `/latest/dex/pairs/{chainId}/{pairId}`
   - `/token-pairs/v1/{chainId}/{tokenAddress}`

## Implementation choices in this repo
1. Two independent sliding-window buckets: `slow=60`, `fast=300`.
2. TTL cache (20s) over all GET endpoints.
3. Retry with exponential backoff for `429/5xx`.
4. Discovery fanout capped to avoid watch-mode stampede:
   - seed token fanout `min(max(limit*4,12),72)`.

## Practical watch-mode budget
Assume:
1. `limit=20`
2. seed fanout capped at `72`
3. refresh interval `7s`
4. cache TTL `20s`

Then:
1. Slow endpoints called about once every 20s: ~9 rpm total.
2. Fast endpoints burst every ~20s at max ~72 calls: ~216 rpm effective average.
3. Remaining headroom supports inspect/search commands without breaking 300 rpm.

## Why this is "free-tier safe"
1. No paid Dexscreener key required.
2. Rate-limited and cached for local use.
3. Works standalone in terminal and via MCP clients.

## Holder distribution reality
Dexscreener public API does not return direct holder distribution tables.

Recommended free-compatible extension path:
1. Keep Dexscreener for discovery/scoring.
2. Add optional chain adapters for holder data:
   - Solana adapter (RPC + parsed token accounts)
   - EVM adapter (RPC + ERC20 `Transfer` snapshots / third-party free endpoint)
3. Merge into a `distribution` sub-score with explicit confidence labels.
