from __future__ import annotations

from math import log1p

from .models import HotTokenCandidate, PairSnapshot


def _clip(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def score_hotness(
    pair: PairSnapshot,
    boost_total: float = 0.0,
    boost_count: int = 0,
    has_profile: bool = False,
) -> tuple[float, list[str]]:
    score, tags, _ = score_hotness_detail(
        pair=pair,
        boost_total=boost_total,
        boost_count=boost_count,
        has_profile=has_profile,
    )
    return score, tags


def score_hotness_detail(
    pair: PairSnapshot,
    boost_total: float = 0.0,
    boost_count: int = 0,
    has_profile: bool = False,
) -> tuple[float, list[str], dict[str, float]]:
    vol_component = _clip(log1p(pair.volume_h24) / log1p(7_500_000.0), 0.0, 1.0)
    txn_component = _clip(log1p(pair.txns_h1) / log1p(4_000.0), 0.0, 1.0)
    liq_component = _clip(log1p(pair.liquidity_usd) / log1p(3_000_000.0), 0.0, 1.0)
    momentum_component = _clip((pair.price_change_h1 + 20.0) / 70.0, 0.0, 1.0)

    total_h1 = pair.txns_h1
    buy_pressure = 0.0
    if total_h1 > 0:
        buy_pressure = (pair.buys_h1 - pair.sells_h1) / total_h1
    pressure_component = _clip((buy_pressure + 1.0) / 2.0, 0.0, 1.0)

    boost_component = _clip(log1p(boost_total) / log1p(600.0), 0.0, 1.0)
    recency_component = 0.2
    if pair.age_hours is not None:
        if pair.age_hours <= 24:
            recency_component = 1.0
        elif pair.age_hours <= 72:
            recency_component = 0.65
        elif pair.age_hours <= 168:
            recency_component = 0.35

    profile_component = 1.0 if has_profile else 0.0

    score = (
        vol_component * 30.0
        + txn_component * 20.0
        + liq_component * 18.0
        + momentum_component * 12.0
        + pressure_component * 8.0
        + boost_component * 7.0
        + recency_component * 3.0
        + profile_component * 2.0
    )

    tags: list[str] = []
    if pair.volume_h24 >= 1_000_000:
        tags.append("high-volume")
    if pair.txns_h1 >= 500:
        tags.append("transaction-spike")
    if pair.price_change_h1 >= 8:
        tags.append("momentum")
    if buy_pressure >= 0.35:
        tags.append("buy-pressure")
    if pair.age_hours is not None and pair.age_hours < 48:
        tags.append("fresh-pair")
    if boost_total >= 100:
        tags.append("boosted")
    if boost_count >= 3:
        tags.append("repeat-boosts")
    if has_profile:
        tags.append("listed-profile")

    weighted_components = {
        "volume": round(vol_component * 30.0, 3),
        "transactions": round(txn_component * 20.0, 3),
        "liquidity": round(liq_component * 18.0, 3),
        "momentum": round(momentum_component * 12.0, 3),
        "flow": round(pressure_component * 8.0, 3),
        "boost": round(boost_component * 7.0, 3),
        "recency": round(recency_component * 3.0, 3),
        "profile": round(profile_component * 2.0, 3),
    }

    return round(score, 2), tags, weighted_components


def build_distribution_heuristics(candidate: HotTokenCandidate) -> dict[str, float | str]:
    pair = candidate.pair
    mcap = pair.market_cap if pair.market_cap > 0 else pair.fdv
    liq_to_cap = (pair.liquidity_usd / mcap) if mcap > 0 else 0.0
    vol_to_liq = (pair.volume_h24 / pair.liquidity_usd) if pair.liquidity_usd > 0 else 0.0

    status = "balanced"
    if liq_to_cap < 0.03:
        status = "concentrated-liquidity"
    elif vol_to_liq > 5:
        status = "speculative-flow"

    return {
        "liquidity_to_market_cap": round(liq_to_cap, 4),
        "volume_to_liquidity_24h": round(vol_to_liq, 4),
        "buy_sell_imbalance_1h": round(
            (pair.buys_h1 - pair.sells_h1) / max(pair.txns_h1, 1),
            4,
        ),
        "status": status,
    }
