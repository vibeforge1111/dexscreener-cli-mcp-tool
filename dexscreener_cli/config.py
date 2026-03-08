from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final

API_BASE: Final[str] = "https://api.dexscreener.com"

DEFAULT_CHAINS: Final[tuple[str, ...]] = ("solana", "base", "ethereum", "bsc")

RATE_LIMITS_RPM: Final[dict[str, int]] = {
    "slow": 60,
    "fast": 300,
}

# Default cache tuned to Dexscreener's documented free limits:
# - slow bucket: 4 discovery endpoints / 10s ~= 24 rpm under the 60 rpm cap
# - fast bucket: ~27 search calls + a handful of pair fetches / 10s stays well
#   under the 300 rpm cap for the live watch workflows in this repo
#
# Users can still override this locally, but 10s is the rate-aware default.
def _cache_ttl_seconds() -> int:
    raw = os.environ.get("DS_CACHE_TTL_SECONDS", "").strip()
    if not raw:
        return 10
    try:
        value = int(raw)
    except ValueError:
        return 10
    return max(1, value)


CACHE_TTL_SECONDS: Final[int] = _cache_ttl_seconds()
REQUEST_TIMEOUT_SECONDS: Final[float] = 15.0
MAX_RETRIES: Final[int] = 3
RETRY_BACKOFF_SECONDS: Final[float] = 0.5


@dataclass(slots=True)
class ScanFilters:
    chains: tuple[str, ...]
    limit: int = 20
    min_liquidity_usd: float = 20_000.0
    min_volume_h24_usd: float = 40_000.0
    min_txns_h1: int = 30
    min_price_change_h1: float = -5.0
