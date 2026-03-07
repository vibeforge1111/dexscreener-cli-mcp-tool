from __future__ import annotations

import asyncio
import os
from time import monotonic
from typing import Any
from urllib.parse import quote

import httpx
from dotenv import load_dotenv

from .client import SlidingWindowLimiter
from .models import PairSnapshot

load_dotenv()

# ---------------------------------------------------------------------------
# Provider config
# ---------------------------------------------------------------------------

# GeckoTerminal chain IDs (free, no key, all chains)
GECKO_CHAIN_IDS: dict[str, str] = {
    "solana": "solana",
    "ethereum": "eth",
    "base": "base",
    "bsc": "bsc",
    "polygon": "polygon_pos",
    "arbitrum": "arbitrum",
    "optimism": "optimism",
    "avalanche": "avax",
}

# Blockscout (ETH + Base, completely free, no key)
BLOCKSCOUT_URLS: dict[str, str] = {
    "ethereum": "https://eth.blockscout.com",
    "base": "https://base.blockscout.com",
}

# Honeypot.is (EVM only, no key needed)
HONEYPOT_CHAIN_IDS: dict[str, int] = {
    "ethereum": 1,
    "bsc": 56,
    "polygon": 137,
    "avalanche": 43114,
    "arbitrum": 42161,
    "base": 8453,
    "optimism": 10,
    "linea": 59144,
    "blast": 81457,
    "zksync": 324,
    "mantle": 5000,
}

# Moralis (EVM + Solana, requires API key)
MORALIS_EVM_CHAINS: dict[str, str] = {
    "ethereum": "eth",
    "bsc": "bsc",
    "polygon": "polygon",
    "arbitrum": "arbitrum",
    "base": "base",
    "optimism": "optimism",
    "avalanche": "avalanche",
}

_moralis_api_key: str = os.environ.get("MORALIS_API_KEY", "").strip()

# ---------------------------------------------------------------------------
# Shared config
# ---------------------------------------------------------------------------

HOLDER_CACHE_TTL_SECONDS = 15 * 60
HOLDER_REQUEST_TIMEOUT_SECONDS = 8.0
HOLDER_REQUESTS_PER_MINUTE = 28

_holder_limiter = SlidingWindowLimiter(HOLDER_REQUESTS_PER_MINUTE)
_holder_cache_lock = asyncio.Lock()
_holder_cache: dict[tuple[str, str], tuple[float, int | None, str | None]] = {}


def _cache_key(chain_id: str, token_address: str) -> tuple[str, str]:
    return chain_id.strip().lower(), token_address.strip().lower()


async def _cache_get(chain_id: str, token_address: str) -> tuple[int | None, str | None] | None:
    key = _cache_key(chain_id, token_address)
    async with _holder_cache_lock:
        item = _holder_cache.get(key)
        if not item:
            return None
        expires_at, holders_count, holders_source = item
        if monotonic() >= expires_at:
            _holder_cache.pop(key, None)
            return None
        return holders_count, holders_source


async def _cache_set(chain_id: str, token_address: str, holders_count: int | None, holders_source: str | None) -> None:
    key = _cache_key(chain_id, token_address)
    async with _holder_cache_lock:
        _holder_cache[key] = (
            monotonic() + HOLDER_CACHE_TTL_SECONDS,
            holders_count,
            holders_source,
        )


# ---------------------------------------------------------------------------
# Provider: GeckoTerminal (all chains, free, no key)
# ---------------------------------------------------------------------------

async def _fetch_gecko(
    chain_id: str,
    token_address: str,
    client: httpx.AsyncClient,
) -> int | None:
    network = GECKO_CHAIN_IDS.get(chain_id)
    if not network:
        return None
    safe_token = quote(token_address, safe="")
    url = f"https://api.geckoterminal.com/api/v2/networks/{network}/tokens/{safe_token}/info"
    for attempt in range(3):
        try:
            resp = await client.get(url, headers={"Accept": "application/json"})
            if resp.status_code == 429:
                # Rate limited — back off and retry
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            if resp.status_code >= 400:
                return None
            data = resp.json()
            attrs = data.get("data", {}).get("attributes", {})
            holders = attrs.get("holders")
            if isinstance(holders, dict):
                count = holders.get("count")
                if count is not None:
                    return int(count)
            return None
        except Exception:
            if attempt < 2:
                await asyncio.sleep(1.0)
                continue
            return None
    return None


# ---------------------------------------------------------------------------
# Provider: Blockscout (ETH + Base, free, no key)
# ---------------------------------------------------------------------------

async def _fetch_blockscout(
    chain_id: str,
    token_address: str,
    client: httpx.AsyncClient,
) -> int | None:
    base_url = BLOCKSCOUT_URLS.get(chain_id)
    if not base_url:
        return None
    try:
        safe_token = quote(token_address, safe="")
        resp = await client.get(
            f"{base_url}/api/v2/tokens/{safe_token}",
            headers={"Accept": "application/json"},
        )
        if resp.status_code >= 400:
            return None
        data = resp.json()
        raw = data.get("holders_count") or data.get("holders")
        if raw is not None:
            return int(raw)
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Provider: Honeypot.is (EVM only, no key)
# ---------------------------------------------------------------------------

def _parse_honeypot_holders(payload: dict[str, Any]) -> int | None:
    token = payload.get("token")
    if isinstance(token, dict):
        raw = token.get("totalHolders")
        try:
            if raw is None:
                return None
            return int(raw)
        except (TypeError, ValueError):
            return None
    return None


async def _fetch_honeypot(
    chain_id: str,
    token_address: str,
    client: httpx.AsyncClient,
) -> int | None:
    chain_numeric = HONEYPOT_CHAIN_IDS.get(chain_id)
    if chain_numeric is None:
        return None
    try:
        resp = await client.get(
            "https://api.honeypot.is/v2/IsHoneypot",
            params={"address": token_address, "chainID": str(chain_numeric)},
            headers={"Accept": "application/json"},
        )
        if resp.status_code >= 400:
            return None
        payload = resp.json()
        return _parse_honeypot_holders(payload if isinstance(payload, dict) else {})
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Provider: Moralis (EVM + Solana, requires API key)
# ---------------------------------------------------------------------------

async def _fetch_moralis(
    chain_id: str,
    token_address: str,
    client: httpx.AsyncClient,
) -> int | None:
    if not _moralis_api_key:
        return None
    headers = {"Accept": "application/json", "X-API-Key": _moralis_api_key}

    if chain_id == "solana":
        safe_token = quote(token_address, safe="")
        url = f"https://solana-gateway.moralis.io/token/mainnet/{safe_token}/holders"
    elif chain_id in MORALIS_EVM_CHAINS:
        moralis_chain = MORALIS_EVM_CHAINS[chain_id]
        safe_token = quote(token_address, safe="")
        url = f"https://deep-index.moralis.io/api/v2.2/erc20/{safe_token}/holders"
    else:
        return None

    try:
        resp = await client.get(url, headers=headers, params={"chain": moralis_chain} if chain_id in MORALIS_EVM_CHAINS else None)
        if resp.status_code >= 400:
            return None
        data = resp.json()
        raw = data.get("totalHolders")
        if raw is not None:
            return int(raw)
    except Exception:
        pass
    return None



# ---------------------------------------------------------------------------
# Main fetch: tries providers in order
# ---------------------------------------------------------------------------

async def fetch_holder_count(
    chain_id: str,
    token_address: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> tuple[int | None, str | None]:
    normalized_chain = chain_id.strip().lower()
    normalized_token = token_address.strip()
    if not normalized_token:
        return None, None

    cached = await _cache_get(normalized_chain, normalized_token)
    if cached is not None:
        return cached

    own_client = client is None
    http_client = client or httpx.AsyncClient(
        timeout=httpx.Timeout(HOLDER_REQUEST_TIMEOUT_SECONDS),
        trust_env=False,
    )

    try:
        await _holder_limiter.acquire()

        # 1. GeckoTerminal (free, no key, all chains)
        if normalized_chain in GECKO_CHAIN_IDS:
            count = await _fetch_gecko(normalized_chain, normalized_token, http_client)
            if count is not None and count > 0:
                await _cache_set(normalized_chain, normalized_token, count, "geckoterminal")
                return count, "geckoterminal"

        # 2. Moralis (EVM + Solana, requires API key)
        if _moralis_api_key and (normalized_chain in MORALIS_EVM_CHAINS or normalized_chain == "solana"):
            count = await _fetch_moralis(normalized_chain, normalized_token, http_client)
            if count is not None and count > 0:
                await _cache_set(normalized_chain, normalized_token, count, "moralis")
                return count, "moralis"

        # 3. Blockscout (ETH + Base, free, no key)
        if normalized_chain in BLOCKSCOUT_URLS:
            count = await _fetch_blockscout(normalized_chain, normalized_token, http_client)
            if count is not None and count > 0:
                await _cache_set(normalized_chain, normalized_token, count, "blockscout")
                return count, "blockscout"

        # 4. Honeypot.is (EVM only, no key)
        if normalized_chain in HONEYPOT_CHAIN_IDS:
            count = await _fetch_honeypot(normalized_chain, normalized_token, http_client)
            if count is not None and count > 0:
                await _cache_set(normalized_chain, normalized_token, count, "honeypot.is")
                return count, "honeypot.is"

        # No provider returned data
        await _cache_set(normalized_chain, normalized_token, None, None)
        return None, None

    except Exception:
        await _cache_set(normalized_chain, normalized_token, None, "error")
        return None, "error"
    finally:
        if own_client:
            await http_client.aclose()


# ---------------------------------------------------------------------------
# Hydrate helpers (unchanged interface)
# ---------------------------------------------------------------------------

async def hydrate_pair_holders(pairs: list[PairSnapshot], *, max_pairs: int | None = None) -> None:
    if not pairs:
        return

    # Group by (chain, lowercase_token) for dedup, but keep original-case address for API
    grouped: dict[tuple[str, str], tuple[str, list[PairSnapshot]]] = {}
    for pair in pairs:
        if pair.holders_count is not None:
            continue
        token = pair.base_address.strip()
        chain = pair.chain_id.strip().lower()
        if not token:
            continue
        key = (chain, token.lower())
        if key not in grouped:
            grouped[key] = (token, [])  # preserve original-case address
        grouped[key][1].append(pair)

    if not grouped:
        return

    ordered = sorted(
        grouped.items(),
        key=lambda item: max(
            (p.volume_h1 + p.volume_h24 * 0.1 + p.liquidity_usd * 0.01 + p.txns_h1 * 10.0)
            for p in item[1][1]
        ),
        reverse=True,
    )
    if max_pairs is not None and max_pairs > 0:
        ordered = ordered[:max_pairs]

    semaphore = asyncio.Semaphore(3)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(HOLDER_REQUEST_TIMEOUT_SECONDS),
        trust_env=False,
    ) as client:
        async def worker(chain: str, original_token: str, bucket: list[PairSnapshot]) -> None:
            async with semaphore:
                holders_count, holders_source = await fetch_holder_count(chain, original_token, client=client)
                for pair in bucket:
                    pair.holders_count = holders_count
                    pair.holders_source = holders_source

        await asyncio.gather(*(
            worker(chain, orig_token, rows)
            for (chain, _), (orig_token, rows) in ordered
        ))


async def hydrate_token_rows_with_holders(
    rows: list[dict[str, object]],
    *,
    chain_field: str = "chainId",
    token_field: str = "tokenAddress",
    holders_field: str = "holdersCount",
    source_field: str = "holdersSource",
    max_rows: int | None = None,
) -> None:
    if not rows:
        return

    # Group by lowercase key for dedup, preserve original-case token for API
    unique: dict[tuple[str, str], tuple[str, list[dict[str, object]]]] = {}
    for row in rows:
        chain = str(row.get(chain_field, "")).strip().lower()
        token = str(row.get(token_field, "")).strip()
        if not chain or not token:
            continue
        key = (chain, token.lower())
        if key not in unique:
            unique[key] = (token, [])
        unique[key][1].append(row)

    ordered = list(unique.items())
    if max_rows is not None and max_rows > 0:
        ordered = ordered[:max_rows]

    semaphore = asyncio.Semaphore(3)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(HOLDER_REQUEST_TIMEOUT_SECONDS),
        trust_env=False,
    ) as client:
        async def worker(chain: str, original_token: str, bucket: list[dict[str, object]]) -> None:
            async with semaphore:
                holders_count, holders_source = await fetch_holder_count(chain, original_token, client=client)
                for row in bucket:
                    row[holders_field] = holders_count
                    row[source_field] = holders_source

        await asyncio.gather(*(
            worker(chain, orig_token, bucket)
            for (chain, _), (orig_token, bucket) in ordered
        ))
