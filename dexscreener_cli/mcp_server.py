from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from .client import DexScreenerClient
from .config import DEFAULT_CHAINS, ScanFilters
from .models import HotTokenCandidate
from .scanner import HotScanner
from .scoring import build_distribution_heuristics

mcp = FastMCP("dexscreener-cli-mcp-tool")


def _serialize_candidate(candidate: HotTokenCandidate) -> dict[str, Any]:
    pair = candidate.pair
    return {
        "chainId": pair.chain_id,
        "tokenAddress": pair.base_address,
        "tokenSymbol": pair.base_symbol,
        "tokenName": pair.base_name,
        "pairAddress": pair.pair_address,
        "pairUrl": pair.pair_url,
        "dexId": pair.dex_id,
        "priceUsd": pair.price_usd,
        "priceChangeH1": pair.price_change_h1,
        "priceChangeH24": pair.price_change_h24,
        "volumeH24": pair.volume_h24,
        "volumeH1": pair.volume_h1,
        "txnsH1": pair.txns_h1,
        "liquidityUsd": pair.liquidity_usd,
        "marketCap": pair.market_cap,
        "fdv": pair.fdv,
        "ageHours": pair.age_hours,
        "boostTotal": candidate.boost_total,
        "boostCount": candidate.boost_count,
        "hasProfile": candidate.has_profile,
        "discovery": candidate.discovery,
        "score": candidate.score,
        "tags": candidate.tags,
    }


@mcp.tool()
async def scan_hot_tokens(
    chains: str = ",".join(DEFAULT_CHAINS),
    limit: int = 20,
    min_liquidity_usd: float = 35_000.0,
    min_volume_h24_usd: float = 90_000.0,
    min_txns_h1: int = 80,
    min_price_change_h1: float = 0.0,
) -> list[dict[str, Any]]:
    """Scan hot tokens by chain using Dexscreener boosts/profiles + pair activity scoring."""
    chain_ids = tuple(c.strip().lower() for c in chains.split(",") if c.strip())
    async with DexScreenerClient() as client:
        scanner = HotScanner(client)
        filters = ScanFilters(
            chains=chain_ids or DEFAULT_CHAINS,
            limit=limit,
            min_liquidity_usd=min_liquidity_usd,
            min_volume_h24_usd=min_volume_h24_usd,
            min_txns_h1=min_txns_h1,
            min_price_change_h1=min_price_change_h1,
        )
        rows = await scanner.scan(filters)
        return [_serialize_candidate(c) for c in rows]


@mcp.tool()
async def search_pairs(query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search Dexscreener pairs by token name/symbol/address."""
    async with DexScreenerClient() as client:
        scanner = HotScanner(client)
        pairs = await scanner.search(query=query, limit=limit)
        return [
            {
                "chainId": p.chain_id,
                "pairAddress": p.pair_address,
                "tokenAddress": p.base_address,
                "tokenSymbol": p.base_symbol,
                "dexId": p.dex_id,
                "priceUsd": p.price_usd,
                "volumeH24": p.volume_h24,
                "txnsH1": p.txns_h1,
                "liquidityUsd": p.liquidity_usd,
                "pairUrl": p.pair_url,
            }
            for p in pairs
        ]


@mcp.tool()
async def inspect_token(chain_id: str, token_address: str) -> dict[str, Any]:
    """Inspect a token and return best pair + concentration proxies."""
    async with DexScreenerClient() as client:
        scanner = HotScanner(client)
        pairs = await scanner.inspect_token(chain_id=chain_id, token_address=token_address)
        if not pairs:
            return {"error": "Token not found or no pairs available"}
        best = pairs[0]
        candidate = HotTokenCandidate(
            pair=best,
            score=0.0,
            boost_total=0.0,
            boost_count=0,
            has_profile=False,
            discovery="inspect",
            tags=[],
        )
        return {
            "bestPair": {
                "chainId": best.chain_id,
                "pairAddress": best.pair_address,
                "pairUrl": best.pair_url,
                "tokenAddress": best.base_address,
                "tokenSymbol": best.base_symbol,
                "priceUsd": best.price_usd,
                "volumeH24": best.volume_h24,
                "txnsH1": best.txns_h1,
                "liquidityUsd": best.liquidity_usd,
                "marketCap": best.market_cap,
                "fdv": best.fdv,
                "priceChangeH1": best.price_change_h1,
                "priceChangeH24": best.price_change_h24,
            },
            "distributionProxy": build_distribution_heuristics(candidate),
            "note": "Public Dexscreener API does not expose holder-level distribution directly.",
            "additionalPairCount": max(len(pairs) - 1, 0),
        }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
