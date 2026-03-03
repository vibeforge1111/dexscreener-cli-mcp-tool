from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from .client import DexScreenerClient
from .config import ScanFilters
from .models import HotTokenCandidate, PairSnapshot
from .scoring import score_hotness


@dataclass(slots=True)
class _SeedToken:
    chain_id: str
    token_address: str
    boost_total: float = 0.0
    boost_count: int = 0
    has_profile: bool = False
    discovery: str = "seed"


class HotScanner:
    def __init__(self, client: DexScreenerClient) -> None:
        self._client = client

    async def _collect_seeds(self, chains: tuple[str, ...]) -> dict[tuple[str, str], _SeedToken]:
        boosts_top, boosts_latest, profiles = await asyncio.gather(
            self._client.get_token_boosts_top(),
            self._client.get_token_boosts_latest(),
            self._client.get_token_profiles_latest(),
        )

        seeds: dict[tuple[str, str], _SeedToken] = {}

        def upsert(
            chain_id: str,
            token_address: str,
            *,
            boost_total: float = 0.0,
            boost_count: int = 0,
            has_profile: bool = False,
            discovery: str = "seed",
        ) -> None:
            key = (chain_id, token_address)
            current = seeds.get(key)
            if current is None:
                seeds[key] = _SeedToken(
                    chain_id=chain_id,
                    token_address=token_address,
                    boost_total=boost_total,
                    boost_count=boost_count,
                    has_profile=has_profile,
                    discovery=discovery,
                )
                return
            current.boost_total += boost_total
            current.boost_count += boost_count
            current.has_profile = current.has_profile or has_profile
            if current.discovery == "seed" and discovery != "seed":
                current.discovery = discovery

        for row in boosts_top:
            chain_id = str(row.get("chainId", ""))
            token = str(row.get("tokenAddress", ""))
            if chain_id not in chains or not token:
                continue
            upsert(
                chain_id,
                token,
                boost_total=float(row.get("totalAmount", 0) or 0),
                boost_count=1,
                discovery="top-boosts",
            )

        latest_counter: dict[tuple[str, str], int] = defaultdict(int)
        for row in boosts_latest:
            chain_id = str(row.get("chainId", ""))
            token = str(row.get("tokenAddress", ""))
            if chain_id not in chains or not token:
                continue
            latest_counter[(chain_id, token)] += 1
            upsert(
                chain_id,
                token,
                boost_total=float(row.get("totalAmount", 0) or 0),
                boost_count=1,
                discovery="latest-boosts",
            )

        for row in profiles:
            chain_id = str(row.get("chainId", ""))
            token = str(row.get("tokenAddress", ""))
            if chain_id not in chains or not token:
                continue
            upsert(
                chain_id,
                token,
                has_profile=True,
                discovery="profiles",
            )

        for key, count in latest_counter.items():
            seeds[key].boost_count = max(seeds[key].boost_count, count)

        return seeds

    async def _best_pair_for_token(self, chain_id: str, token_address: str) -> PairSnapshot | None:
        rows = await self._client.get_token_pairs(chain_id, token_address)
        if not rows:
            return None
        pairs = [PairSnapshot.from_api(p) for p in rows]
        pairs.sort(
            key=lambda p: (
                p.liquidity_usd * 0.45
                + p.volume_h24 * 0.45
                + p.txns_h1 * 150.0
                + (p.price_change_h1 * 1500.0),
            ),
            reverse=True,
        )
        return pairs[0]

    def _passes_filters(self, pair: PairSnapshot, filters: ScanFilters) -> bool:
        if pair.liquidity_usd < filters.min_liquidity_usd:
            return False
        if pair.volume_h24 < filters.min_volume_h24_usd:
            return False
        if pair.txns_h1 < filters.min_txns_h1:
            return False
        if pair.price_change_h1 < filters.min_price_change_h1:
            return False
        return True

    async def scan(self, filters: ScanFilters) -> list[HotTokenCandidate]:
        seeds = await self._collect_seeds(filters.chains)
        ordered_seeds = sorted(
            seeds.values(),
            key=lambda s: (s.boost_total, s.boost_count, s.has_profile),
            reverse=True,
        )
        # Keep fast-endpoint pressure bounded for watch mode.
        target = min(max(filters.limit * 4, 12), 72)
        ordered_seeds = ordered_seeds[:target]

        semaphore = asyncio.Semaphore(20)
        results: list[HotTokenCandidate] = []

        async def worker(seed: _SeedToken) -> None:
            async with semaphore:
                pair = await self._best_pair_for_token(seed.chain_id, seed.token_address)
                if not pair:
                    return
                if not self._passes_filters(pair, filters):
                    return
                score, tags = score_hotness(
                    pair=pair,
                    boost_total=seed.boost_total,
                    boost_count=seed.boost_count,
                    has_profile=seed.has_profile,
                )
                results.append(
                    HotTokenCandidate(
                        pair=pair,
                        score=score,
                        boost_total=seed.boost_total,
                        boost_count=seed.boost_count,
                        has_profile=seed.has_profile,
                        discovery=seed.discovery,
                        tags=tags,
                    )
                )

        await asyncio.gather(*(worker(seed) for seed in ordered_seeds))

        # De-duplicate per token and keep strongest listing.
        dedup: dict[tuple[str, str], HotTokenCandidate] = {}
        for candidate in results:
            key = candidate.key
            existing = dedup.get(key)
            if existing is None or candidate.score > existing.score:
                dedup[key] = candidate

        ranked = sorted(
            dedup.values(),
            key=lambda c: (
                c.score,
                c.pair.volume_h24,
                c.pair.txns_h1,
                c.pair.liquidity_usd,
            ),
            reverse=True,
        )
        return ranked[: filters.limit]

    async def inspect_token(self, chain_id: str, token_address: str) -> list[PairSnapshot]:
        pairs = await self._client.get_token_pairs(chain_id, token_address)
        snapshots = [PairSnapshot.from_api(row) for row in pairs]
        snapshots.sort(
            key=lambda p: (p.liquidity_usd, p.volume_h24, p.txns_h1),
            reverse=True,
        )
        return snapshots

    async def inspect_pair(self, chain_id: str, pair_address: str) -> PairSnapshot | None:
        row = await self._client.get_pair(chain_id, pair_address)
        if not row:
            return None
        return PairSnapshot.from_api(row)

    async def search(self, query: str, limit: int = 20) -> list[PairSnapshot]:
        rows = await self._client.search_pairs(query)
        snapshots = [PairSnapshot.from_api(row) for row in rows[:limit]]
        return snapshots
