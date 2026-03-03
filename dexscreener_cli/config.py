from __future__ import annotations

from dataclasses import dataclass
from typing import Final

API_BASE: Final[str] = "https://api.dexscreener.com"

DEFAULT_CHAINS: Final[tuple[str, ...]] = ("solana", "base", "ethereum", "bsc")

RATE_LIMITS_RPM: Final[dict[str, int]] = {
    "slow": 60,
    "fast": 300,
}

CACHE_TTL_SECONDS: Final[int] = 20
REQUEST_TIMEOUT_SECONDS: Final[float] = 15.0
MAX_RETRIES: Final[int] = 3
RETRY_BACKOFF_SECONDS: Final[float] = 0.5


@dataclass(slots=True)
class ScanFilters:
    chains: tuple[str, ...]
    limit: int = 20
    min_liquidity_usd: float = 35_000.0
    min_volume_h24_usd: float = 90_000.0
    min_txns_h1: int = 80
    min_price_change_h1: float = 0.0
