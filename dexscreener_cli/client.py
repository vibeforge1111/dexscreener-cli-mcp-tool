from __future__ import annotations

import asyncio
import logging
import random
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
        self._bucket_pause_until: dict[str, float] = {"slow": 0.0, "fast": 0.0}
        self._bucket_penalty_seconds: dict[str, float] = {"slow": 0.0, "fast": 0.0}
        self._stats_lock = asyncio.Lock()
        self._stats: dict[str, Any] = {
            "requests_total": 0,
            "cache_hits": 0,
            "retries": 0,
            "throttled_429": 0,
            "errors": 0,
            "status_counts": {},
            "bucket_wait_seconds": {"slow": 0.0, "fast": 0.0},
        }

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

    async def _bump_stat(self, key: str, value: int = 1) -> None:
        async with self._stats_lock:
            self._stats[key] = int(self._stats.get(key, 0)) + value

    async def _bump_status(self, status_code: int) -> None:
        async with self._stats_lock:
            status = self._stats.get("status_counts", {})
            if not isinstance(status, dict):
                status = {}
                self._stats["status_counts"] = status
            sk = str(status_code)
            status[sk] = int(status.get(sk, 0)) + 1

    async def _add_bucket_wait(self, bucket: str, seconds: float) -> None:
        async with self._stats_lock:
            waits = self._stats.get("bucket_wait_seconds", {})
            if not isinstance(waits, dict):
                waits = {"slow": 0.0, "fast": 0.0}
                self._stats["bucket_wait_seconds"] = waits
            waits[bucket] = float(waits.get(bucket, 0.0)) + max(seconds, 0.0)

    def _retry_after_seconds(self, response: httpx.Response) -> float | None:
        value = response.headers.get("Retry-After")
        if not value:
            return None
        try:
            return max(float(value), 0.0)
        except (TypeError, ValueError):
            return None

    async def get_runtime_stats(self) -> dict[str, Any]:
        async with self._stats_lock:
            return {
                "requests_total": int(self._stats.get("requests_total", 0)),
                "cache_hits": int(self._stats.get("cache_hits", 0)),
                "retries": int(self._stats.get("retries", 0)),
                "throttled_429": int(self._stats.get("throttled_429", 0)),
                "errors": int(self._stats.get("errors", 0)),
                "status_counts": dict(self._stats.get("status_counts", {})),
                "bucket_wait_seconds": dict(self._stats.get("bucket_wait_seconds", {})),
                "bucket_penalty_seconds": dict(self._bucket_penalty_seconds),
            }

    async def _cache_set(self, key: str, payload: Any) -> None:
        async with self._cache_lock:
            self._cache[key] = (monotonic() + self._cache_ttl, payload)

    async def _get_json(self, path: str, bucket: str) -> Any:
        cached = await self._cache_get(path)
        if cached is not None:
            await self._bump_stat("cache_hits")
            return cached

        limiter = self._limiters[bucket]
        attempt = 0
        while True:
            now = monotonic()
            pause_until = self._bucket_pause_until.get(bucket, 0.0)
            if now < pause_until:
                wait_for = pause_until - now
                await self._add_bucket_wait(bucket, wait_for)
                await asyncio.sleep(wait_for)
            await limiter.acquire()
            await self._bump_stat("requests_total")
            response = await self._client.get(path)
            await self._bump_status(response.status_code)
            if response.status_code == 429:
                await self._bump_stat("throttled_429")
                retry_after = self._retry_after_seconds(response)
                base_penalty = self._bucket_penalty_seconds.get(bucket, 0.0)
                next_penalty = max(base_penalty * 2.0, 1.5)
                next_penalty = min(next_penalty, 30.0)
                self._bucket_penalty_seconds[bucket] = next_penalty
                cooldown = max(retry_after or 0.0, next_penalty)
                # Jitter avoids synchronized retry bursts.
                cooldown += random.uniform(0.05, 0.35)
                self._bucket_pause_until[bucket] = max(self._bucket_pause_until.get(bucket, 0.0), monotonic() + cooldown)
            if response.status_code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES:
                await self._bump_stat("retries")
                sleep_for = RETRY_BACKOFF_SECONDS * (2**attempt)
                sleep_for += random.uniform(0.02, 0.2)
                await asyncio.sleep(sleep_for)
                attempt += 1
                continue
            if response.status_code >= 400:
                await self._bump_stat("errors")
            response.raise_for_status()
            # Decay bucket penalty after healthy responses.
            self._bucket_penalty_seconds[bucket] = max(self._bucket_penalty_seconds.get(bucket, 0.0) * 0.65, 0.0)
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
