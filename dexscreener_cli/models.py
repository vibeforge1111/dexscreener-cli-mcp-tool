from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass(slots=True)
class PairSnapshot:
    chain_id: str
    dex_id: str
    pair_address: str
    pair_url: str
    base_address: str
    base_symbol: str
    base_name: str
    quote_symbol: str
    price_usd: float
    volume_h24: float
    volume_h6: float
    volume_h1: float
    volume_m5: float
    buys_h1: int
    sells_h1: int
    buys_h24: int
    sells_h24: int
    price_change_h1: float
    price_change_h24: float
    liquidity_usd: float
    market_cap: float
    fdv: float
    pair_created_at_ms: int | None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def txns_h1(self) -> int:
        return self.buys_h1 + self.sells_h1

    @property
    def txns_h24(self) -> int:
        return self.buys_h24 + self.sells_h24

    @property
    def age_hours(self) -> float | None:
        if not self.pair_created_at_ms:
            return None
        then = datetime.fromtimestamp(self.pair_created_at_ms / 1000, tz=UTC)
        return max((datetime.now(UTC) - then).total_seconds() / 3600, 0.0)

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "PairSnapshot":
        base = payload.get("baseToken", {})
        quote = payload.get("quoteToken", {})
        txns = payload.get("txns", {})
        h1_txns = txns.get("h1", {})
        h24_txns = txns.get("h24", {})
        volume = payload.get("volume", {})
        p_change = payload.get("priceChange", {})
        liquidity = payload.get("liquidity", {})

        return cls(
            chain_id=str(payload.get("chainId", "")),
            dex_id=str(payload.get("dexId", "")),
            pair_address=str(payload.get("pairAddress", "")),
            pair_url=str(payload.get("url", "")),
            base_address=str(base.get("address", "")),
            base_symbol=str(base.get("symbol", "")),
            base_name=str(base.get("name", "")),
            quote_symbol=str(quote.get("symbol", "")),
            price_usd=_as_float(payload.get("priceUsd")),
            volume_h24=_as_float(volume.get("h24")),
            volume_h6=_as_float(volume.get("h6")),
            volume_h1=_as_float(volume.get("h1")),
            volume_m5=_as_float(volume.get("m5")),
            buys_h1=_as_int(h1_txns.get("buys")),
            sells_h1=_as_int(h1_txns.get("sells")),
            buys_h24=_as_int(h24_txns.get("buys")),
            sells_h24=_as_int(h24_txns.get("sells")),
            price_change_h1=_as_float(p_change.get("h1")),
            price_change_h24=_as_float(p_change.get("h24")),
            liquidity_usd=_as_float(liquidity.get("usd")),
            market_cap=_as_float(payload.get("marketCap")),
            fdv=_as_float(payload.get("fdv")),
            pair_created_at_ms=payload.get("pairCreatedAt"),
            raw=payload,
        )


@dataclass(slots=True)
class HotTokenCandidate:
    pair: PairSnapshot
    score: float
    boost_total: float
    boost_count: int
    has_profile: bool
    discovery: str
    tags: list[str] = field(default_factory=list)

    @property
    def key(self) -> tuple[str, str]:
        return self.pair.chain_id, self.pair.base_address
