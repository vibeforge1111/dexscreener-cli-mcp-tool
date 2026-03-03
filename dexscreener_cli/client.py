from __future__ import annotations

import asyncio
import logging
from collections import deque
from time import monotonic
from typing import Any

import httpx

from .config import (
    API_BASE,
    CACHE_TTL_SECONDS,
    MAX_RETRIES,
    REQUEST_TIMEOUT_SECONDS,
    RETRY_BACKOFF_SECONDS,
    RATE_LIMITS_RPM,
)

logging.getLogger("httpx").setLevel(logging.WARNING)


class SlidingWindowLimiter:
    def __init__(self, rpm: int) -> None:
        self._window_seconds = 60.0
        self._max_calls = rpm
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = monotonic()
                while self._calls and now - self._calls[0] >= self._window_seconds:
                    self._calls.popleft()
                if len(self._calls) < self._max_calls:
                    self._calls.append(now)
                    return
                wait_for = self._window_seconds - (now - self._calls[0])
            await asyncio.sleep(max(wait_for, 0.05))


class DexScreenerClient:
    def __init__(self, cache_ttl_seconds: int = CACHE_TTL_SECONDS) -> None:
        self._client = httpx.AsyncClient(
            base_url=API_BASE,
            timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS),
            headers={"Accept": "application/json"},
        )
        self._limiters = {
            "slow": SlidingWindowLimiter(RATE_LIMITS_RPM["slow"]),
            "fast": SlidingWindowLimiter(RATE_LIMITS_RPM["fast"]),
        }
        self._cache_ttl = cache_ttl_seconds
        self._cache: dict[str, tuple[float, Any]] = {}
        self._cache_lock = asyncio.Lock()

    async def __aenter__(self) -> "DexScreenerClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _cache_get(self, key: str) -> Any | None:
        async with self._cache_lock:
            item = self._cache.get(key)
            if not item:
                return None
            expires_at, payload = item
            if monotonic() >= expires_at:
                self._cache.pop(key, None)
                return None
            return payload

    async def _cache_set(self, key: str, payload: Any) -> None:
        async with self._cache_lock:
            self._cache[key] = (monotonic() + self._cache_ttl, payload)

    async def _get_json(self, path: str, bucket: str) -> Any:
        cached = await self._cache_get(path)
        if cached is not None:
            return cached

        limiter = self._limiters[bucket]
        attempt = 0
        while True:
            await limiter.acquire()
            response = await self._client.get(path)
            if response.status_code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES:
                sleep_for = RETRY_BACKOFF_SECONDS * (2**attempt)
                await asyncio.sleep(sleep_for)
                attempt += 1
                continue
            response.raise_for_status()
            payload = response.json()
            await self._cache_set(path, payload)
            return payload

    async def get_token_profiles_latest(self) -> list[dict[str, Any]]:
        data = await self._get_json("/token-profiles/latest/v1", bucket="slow")
        return list(data)

    async def get_token_boosts_latest(self) -> list[dict[str, Any]]:
        data = await self._get_json("/token-boosts/latest/v1", bucket="slow")
        return list(data)

    async def get_token_boosts_top(self) -> list[dict[str, Any]]:
        data = await self._get_json("/token-boosts/top/v1", bucket="slow")
        return list(data)

    async def get_orders(self, chain_id: str, token_address: str) -> dict[str, Any]:
        return await self._get_json(f"/orders/v1/{chain_id}/{token_address}", bucket="slow")

    async def search_pairs(self, query: str) -> list[dict[str, Any]]:
        data = await self._get_json(f"/latest/dex/search?q={query}", bucket="fast")
        return list(data.get("pairs", []))

    async def get_pair(self, chain_id: str, pair_address: str) -> dict[str, Any]:
        data = await self._get_json(
            f"/latest/dex/pairs/{chain_id}/{pair_address}",
            bucket="fast",
        )
        pair = data.get("pair")
        if pair:
            return pair
        pairs = data.get("pairs", [])
        if pairs:
            return pairs[0]
        return {}

    async def get_token_pairs(self, chain_id: str, token_address: str) -> list[dict[str, Any]]:
        data = await self._get_json(
            f"/token-pairs/v1/{chain_id}/{token_address}",
            bucket="fast",
        )
        return list(data)
