from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass
from statistics import median
import time
from typing import Any

from .client import DexScreenerClient
from .config import ScanFilters
from .models import CandidateAnalytics, HotTokenCandidate, PairSnapshot
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
        self._boost_history: dict[tuple[str, str], tuple[float, float]] = {}
        self._momentum_history: dict[tuple[str, str], list[tuple[float, float]]] = {}
        self._max_history_points = 20
        self._history_ttl_seconds = 2 * 60 * 60

    @staticmethod
    def _clip(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    @staticmethod
    def _buy_pressure(pair: PairSnapshot) -> float:
        if pair.txns_h1 <= 0:
            return 0.0
        return (pair.buys_h1 - pair.sells_h1) / pair.txns_h1

    @staticmethod
    def _velocity_components(pair: PairSnapshot) -> tuple[float, float]:
        vol_baseline = ((pair.volume_h6 - pair.volume_h1) / 5.0) if pair.volume_h6 > pair.volume_h1 else (pair.volume_h24 / 24.0)
        vol_baseline = max(vol_baseline, 1.0)
        volume_velocity = pair.volume_h1 / vol_baseline

        tx_h24 = pair.txns_h24
        tx_baseline = ((tx_h24 - pair.txns_h1) / 23.0) if tx_h24 > pair.txns_h1 else (tx_h24 / 24.0)
        tx_baseline = max(tx_baseline, 1.0)
        txn_velocity = pair.txns_h1 / tx_baseline
        return volume_velocity, txn_velocity

    def _compression_and_readiness(self, pair: PairSnapshot, *, buy_pressure: float, volume_velocity: float, txn_velocity: float) -> tuple[float, float]:
        price_noise = abs(pair.price_change_h1)
        compression_price = self._clip((9.0 - price_noise) / 9.0, 0.0, 1.0)
        flow_build = self._clip((min(volume_velocity, 3.0) / 3.0 + min(txn_velocity, 3.0) / 3.0) / 2.0, 0.0, 1.0)
        pressure = self._clip((buy_pressure + 0.15) / 0.85, 0.0, 1.0)

        compression_score = self._clip(compression_price * 0.55 + flow_build * 0.30 + pressure * 0.15, 0.0, 1.0)
        breakout_readiness = self._clip(
            compression_score * 0.45
            + (min(volume_velocity, 3.0) / 3.0) * 0.30
            + (min(txn_velocity, 3.0) / 3.0) * 0.15
            + pressure * 0.10,
            0.0,
            1.0,
        )
        return compression_score * 100.0, breakout_readiness * 100.0

    def _boost_velocity(self, key: tuple[str, str], boost_total: float, now_s: float) -> float:
        previous = self._boost_history.get(key)
        self._boost_history[key] = (now_s, boost_total)
        if previous is None:
            return 0.0
        dt_min = (now_s - previous[0]) / 60.0
        if dt_min <= 0:
            return 0.0
        return (boost_total - previous[1]) / dt_min

    def _momentum_metrics(self, key: tuple[str, str], price_change_h1: float, now_s: float) -> tuple[float | None, float | None, bool]:
        history = self._momentum_history.setdefault(key, [])
        history.append((now_s, max(price_change_h1, 0.0)))
        cutoff = now_s - self._history_ttl_seconds
        history = [entry for entry in history if entry[0] >= cutoff]
        if len(history) > self._max_history_points:
            history = history[-self._max_history_points :]
        self._momentum_history[key] = history

        if len(history) < 2:
            return None, None, False

        peak_idx = max(range(len(history)), key=lambda idx: history[idx][1])
        peak_ts, peak_val = history[peak_idx]
        if peak_val <= 0:
            return None, None, False

        current_val = history[-1][1]
        decay_ratio = current_val / peak_val
        half_life_min: float | None = None
        half_level = peak_val * 0.5
        for ts, value in history[peak_idx:]:
            if value <= half_level:
                half_life_min = (ts - peak_ts) / 60.0
                break

        fast_decay = half_life_min is not None and half_life_min <= 12.0 and decay_ratio <= 0.45
        return half_life_min, decay_ratio, fast_decay

    def _enrich_candidates(self, candidates: list[HotTokenCandidate]) -> None:
        if not candidates:
            return

        now_s = time.time()
        chain_momentum: dict[str, list[float]] = defaultdict(list)
        chain_velocity: dict[str, list[float]] = defaultdict(list)
        metrics: dict[tuple[str, str], tuple[float, float, float, float, float]] = {}

        for candidate in candidates:
            pair = candidate.pair
            buy_pressure = self._buy_pressure(pair)
            volume_velocity, txn_velocity = self._velocity_components(pair)
            compression_score, breakout_readiness = self._compression_and_readiness(
                pair,
                buy_pressure=buy_pressure,
                volume_velocity=volume_velocity,
                txn_velocity=txn_velocity,
            )
            metrics[candidate.key] = (buy_pressure, volume_velocity, txn_velocity, compression_score, breakout_readiness)
            chain_momentum[pair.chain_id].append(pair.price_change_h1)
            chain_velocity[pair.chain_id].append(volume_velocity)

        chain_baseline_h1 = {chain: median(values) for chain, values in chain_momentum.items() if values}
        chain_baseline_velocity = {chain: median(values) for chain, values in chain_velocity.items() if values}

        for candidate in candidates:
            pair = candidate.pair
            key = candidate.key
            buy_pressure, volume_velocity, txn_velocity, compression_score, breakout_readiness = metrics[key]
            baseline_h1 = chain_baseline_h1.get(pair.chain_id, 0.0)
            baseline_velocity = chain_baseline_velocity.get(pair.chain_id, 1.0)
            relative_strength = (pair.price_change_h1 - baseline_h1) + ((volume_velocity - baseline_velocity) * 5.0)

            boost_velocity = self._boost_velocity(key, candidate.boost_total, now_s)
            half_life_min, decay_ratio, fast_decay = self._momentum_metrics(key, pair.price_change_h1, now_s)
            candidate.analytics = CandidateAnalytics(
                compression_score=round(compression_score, 2),
                breakout_readiness=round(breakout_readiness, 2),
                volume_velocity=round(volume_velocity, 3),
                txn_velocity=round(txn_velocity, 3),
                relative_strength=round(relative_strength, 2),
                chain_baseline_h1=round(baseline_h1, 2),
                boost_velocity=round(boost_velocity, 3),
                momentum_half_life_min=round(half_life_min, 2) if half_life_min is not None else None,
                momentum_decay_ratio=round(decay_ratio, 3) if decay_ratio is not None else None,
                fast_decay=fast_decay,
            )

            adjusted = candidate.score
            adjusted += (breakout_readiness - 50.0) * 0.08
            adjusted += self._clip(relative_strength, -25.0, 25.0) * 0.15
            adjusted += self._clip(boost_velocity, -10.0, 10.0) * 0.2
            if fast_decay:
                adjusted -= 12.0
            candidate.score = round(max(0.0, adjusted), 2)

            tags = list(candidate.tags)
            if compression_score >= 72 and volume_velocity >= 1.15 and txn_velocity >= 1.15:
                tags.append("volatility-compression")
            if breakout_readiness >= 68:
                tags.append("breakout-ready")
            if relative_strength >= 8:
                tags.append("rs-leader")
            elif relative_strength <= -8:
                tags.append("rs-laggard")
            if boost_velocity >= 3:
                tags.append("boost-accel")
            elif boost_velocity <= -1:
                tags.append("boost-decay")
            if fast_decay:
                tags.append("momentum-decay")
            elif half_life_min is not None and half_life_min >= 25 and (decay_ratio or 0.0) > 0.65:
                tags.append("momentum-persistent")
            # keep stable order while dropping duplicates
            candidate.tags = list(dict.fromkeys(tags))

    async def _collect_seeds(self, chains: tuple[str, ...]) -> dict[tuple[str, str], _SeedToken]:
        boosts_top, boosts_latest, profiles, takeovers = await asyncio.gather(
            self._client.get_token_boosts_top(),
            self._client.get_token_boosts_latest(),
            self._client.get_token_profiles_latest(),
            self._client.get_community_takeovers_latest(),
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

        for row in takeovers:
            chain_id = str(row.get("chainId", ""))
            token = str(row.get("tokenAddress", ""))
            if chain_id not in chains or not token:
                continue
            upsert(
                chain_id,
                token,
                boost_total=45.0,
                boost_count=1,
                has_profile=True,
                discovery="community",
            )

        for key, count in latest_counter.items():
            seeds[key].boost_count = max(seeds[key].boost_count, count)

        # Add a bounded search-based discovery layer so chains like Base are not
        # under-covered when boosts/profiles are Solana-heavy.
        now_ms = int(time.time() * 1000)
        query_set: set[str] = set()
        for chain_id in chains:
            query_set.update(
                {
                    chain_id,
                    f"{chain_id} new",
                    f"{chain_id} launch",
                    f"{chain_id} meme",
                    f"{chain_id} ai",
                    f"{chain_id} degen",
                }
            )
        search_queries = tuple(sorted(query_set))
        def as_float(value: Any) -> float:
            try:
                return float(value or 0)
            except (TypeError, ValueError):
                return 0.0

        def as_int(value: Any) -> int:
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        if search_queries:
            search_rows_batches = await asyncio.gather(
                *(self._client.search_pairs(query) for query in search_queries),
                return_exceptions=True,
            )
            for batch in search_rows_batches:
                if isinstance(batch, Exception):
                    continue
                for row in batch:
                    chain_id = str(row.get("chainId", ""))
                    if chain_id not in chains:
                        continue
                    base = row.get("baseToken", {}) or {}
                    token = str(base.get("address", ""))
                    if not token:
                        continue

                    tx_h1 = row.get("txns", {}).get("h1", {}) if isinstance(row.get("txns"), dict) else {}
                    buys_h1 = as_int((tx_h1 or {}).get("buys", 0))
                    sells_h1 = as_int((tx_h1 or {}).get("sells", 0))
                    txns_h1 = buys_h1 + sells_h1
                    volume_h24 = as_float((row.get("volume", {}) or {}).get("h24", 0))
                    liquidity_usd = as_float((row.get("liquidity", {}) or {}).get("usd", 0))
                    pair_created_at = row.get("pairCreatedAt")
                    freshness_bonus = 0.0
                    if pair_created_at:
                        age_h = max((now_ms - as_int(pair_created_at)) / 3_600_000.0, 0.0)
                        freshness_bonus = max(0.0, (168.0 - age_h) / 168.0) * 60.0

                    search_weight = (
                        min(volume_h24 / 100_000.0, 25.0)
                        + min(liquidity_usd / 50_000.0, 15.0)
                        + min(txns_h1 / 25.0, 20.0)
                        + freshness_bonus
                    )
                    upsert(
                        chain_id,
                        token,
                        boost_total=search_weight,
                        boost_count=1,
                        discovery="search",
                    )

        return seeds

    @staticmethod
    def _pair_rank(pair: PairSnapshot) -> float:
        return (
            pair.liquidity_usd * 0.45
            + pair.volume_h24 * 0.45
            + pair.txns_h1 * 150.0
            + (pair.price_change_h1 * 1500.0)
        )

    def _best_pair_from_rows(self, rows: list[dict[str, Any]]) -> dict[tuple[str, str], PairSnapshot]:
        best: dict[tuple[str, str], PairSnapshot] = {}
        for row in rows:
            pair = PairSnapshot.from_api(row)
            key = (pair.chain_id, pair.base_address)
            existing = best.get(key)
            if existing is None or self._pair_rank(pair) > self._pair_rank(existing):
                best[key] = pair
        return best

    async def _prefetch_pairs_for_seeds(self, seeds: list[_SeedToken]) -> dict[tuple[str, str], PairSnapshot]:
        by_chain: dict[str, list[str]] = defaultdict(list)
        for seed in seeds:
            by_chain[seed.chain_id].append(seed.token_address)

        aggregated_rows: list[dict[str, Any]] = []
        for chain_id, token_addresses in by_chain.items():
            try:
                rows = await self._client.get_pairs_for_tokens(chain_id, token_addresses)
            except Exception:
                continue
            aggregated_rows.extend(rows)

        return self._best_pair_from_rows(aggregated_rows)

    async def _best_pair_for_token(self, chain_id: str, token_address: str) -> PairSnapshot | None:
        rows = await self._client.get_token_pairs(chain_id, token_address)
        if not rows:
            return None
        pairs = [PairSnapshot.from_api(p) for p in rows]
        pairs.sort(key=self._pair_rank, reverse=True)
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
        prefetch = await self._prefetch_pairs_for_seeds(ordered_seeds)

        semaphore = asyncio.Semaphore(20)
        results: list[HotTokenCandidate] = []

        async def worker(seed: _SeedToken) -> None:
            async with semaphore:
                pair = prefetch.get((seed.chain_id, seed.token_address))
                if pair is None:
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

        enriched = list(dedup.values())
        self._enrich_candidates(enriched)

        ranked = sorted(
            enriched,
            key=lambda c: (
                c.score,
                c.analytics.breakout_readiness,
                c.analytics.relative_strength,
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
