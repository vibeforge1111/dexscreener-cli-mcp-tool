from __future__ import annotations

import asyncio
import os
from time import monotonic
from typing import Any

import httpx

from .client import SlidingWindowLimiter
from .models import PairSnapshot

# ---------------------------------------------------------------------------
# Provider config
# ---------------------------------------------------------------------------

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

# Blockscout (ETH + Base, completely free, no key)
BLOCKSCOUT_URLS: dict[str, str] = {
    "ethereum": "https://eth.blockscout.com",
    "base": "https://base.blockscout.com",
}

# Moralis (all chains, free key from moralis.com)
MORALIS_EVM_CHAINS: dict[str, str] = {
    "ethereum": "0x1",
    "bsc": "0x38",
    "base": "0x2105",
    "polygon": "0x89",
    "arbitrum": "0xa4b1",
    "optimism": "0xa",
    "avalanche": "0xa86a",
}
MORALIS_SOLANA_NETWORKS = {"solana": "mainnet"}

# ---------------------------------------------------------------------------
# Shared config
# ---------------------------------------------------------------------------

HOLDER_CACHE_TTL_SECONDS = 15 * 60
HOLDER_REQUEST_TIMEOUT_SECONDS = 8.0
HOLDER_REQUESTS_PER_MINUTE = 45

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


def _get_moralis_key() -> str | None:
    return os.environ.get("MORALIS_API_KEY", "").strip() or None


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
        resp = await client.get(
            f"{base_url}/api/v2/tokens/{token_address}",
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
# Provider: Moralis (all chains, free API key)
# ---------------------------------------------------------------------------

async def _fetch_moralis(
    chain_id: str,
    token_address: str,
    api_key: str,
    client: httpx.AsyncClient,
) -> int | None:
    headers = {"Accept": "application/json", "X-API-Key": api_key}

    # Solana
    network = MORALIS_SOLANA_NETWORKS.get(chain_id)
    if network:
        try:
            resp = await client.get(
                f"https://solana-gateway.moralis.io/token/{network}/{token_address}/holders",
                headers=headers,
            )
            if resp.status_code >= 400:
                return None
            data = resp.json()
            raw = data.get("totalHolders") or data.get("total_holders") or data.get("total")
            if raw is not None:
                return int(raw)
        except Exception:
            pass
        return None

    # EVM
    chain_hex = MORALIS_EVM_CHAINS.get(chain_id)
    if not chain_hex:
        return None
    try:
        resp = await client.get(
            f"https://deep-index.moralis.io/api/v2.2/erc20/{token_address}/owners",
            params={"chain": chain_hex, "limit": "1"},
            headers=headers,
        )
        if resp.status_code >= 400:
            return None
        data = resp.json()
        raw = data.get("total")
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
    )

    try:
        await _holder_limiter.acquire()

        moralis_key = _get_moralis_key()

        # 1. Moralis (if key set) — covers all chains including Solana
        if moralis_key:
            count = await _fetch_moralis(normalized_chain, normalized_token, moralis_key, http_client)
            if count is not None and count > 0:
                await _cache_set(normalized_chain, normalized_token, count, "moralis")
                return count, "moralis"

        # 2. Blockscout (ETH + Base, free, no key)
        if normalized_chain in BLOCKSCOUT_URLS:
            count = await _fetch_blockscout(normalized_chain, normalized_token, http_client)
            if count is not None and count > 0:
                await _cache_set(normalized_chain, normalized_token, count, "blockscout")
                return count, "blockscout"

        # 3. Honeypot.is (EVM only, no key)
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

    grouped: dict[tuple[str, str], list[PairSnapshot]] = {}
    for pair in pairs:
        if pair.holders_count is not None:
            continue
        token = pair.base_address.strip()
        chain = pair.chain_id.strip().lower()
        if not token:
            continue
        grouped.setdefault((chain, token.lower()), []).append(pair)

    if not grouped:
        return

    ordered = sorted(
        grouped.items(),
        key=lambda item: max(
            (p.volume_h1 + p.volume_h24 * 0.1 + p.liquidity_usd * 0.01 + p.txns_h1 * 10.0)
            for p in item[1]
        ),
        reverse=True,
    )
    if max_pairs is not None and max_pairs > 0:
        ordered = ordered[:max_pairs]

    semaphore = asyncio.Semaphore(8)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(HOLDER_REQUEST_TIMEOUT_SECONDS),
    ) as client:
        async def worker(chain: str, token: str, bucket: list[PairSnapshot]) -> None:
            async with semaphore:
                holders_count, holders_source = await fetch_holder_count(chain, token, client=client)
                for pair in bucket:
                    pair.holders_count = holders_count
                    pair.holders_source = holders_source

        await asyncio.gather(*(worker(chain, token, rows) for (chain, token), rows in ordered))


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

    unique: dict[tuple[str, str], list[dict[str, object]]] = {}
    for row in rows:
        chain = str(row.get(chain_field, "")).strip().lower()
        token = str(row.get(token_field, "")).strip().lower()
        if not chain or not token:
            continue
        unique.setdefault((chain, token), []).append(row)

    ordered = list(unique.items())
    if max_rows is not None and max_rows > 0:
        ordered = ordered[:max_rows]

    semaphore = asyncio.Semaphore(8)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(HOLDER_REQUEST_TIMEOUT_SECONDS),
    ) as client:
        async def worker(chain: str, token: str, bucket: list[dict[str, object]]) -> None:
            async with semaphore:
                holders_count, holders_source = await fetch_holder_count(chain, token, client=client)
                for row in bucket:
                    row[holders_field] = holders_count
                    row[source_field] = holders_source

        await asyncio.gather(*(worker(chain, token, bucket) for (chain, token), bucket in ordered))
