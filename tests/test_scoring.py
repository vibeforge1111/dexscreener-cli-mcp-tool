"""Tests for the scoring engine."""
from __future__ import annotations

from dexscreener_cli.models import CandidateAnalytics, HotTokenCandidate, PairSnapshot
from dexscreener_cli.scoring import build_distribution_heuristics, score_hotness, score_hotness_detail


def _make_pair(**overrides: object) -> PairSnapshot:
    defaults = dict(
        chain_id="solana",
        dex_id="raydium",
        pair_address="PAIR1",
        pair_url="https://dexscreener.com/solana/PAIR1",
        base_address="TOKEN1",
        base_symbol="TEST",
        base_name="Test Token",
        quote_symbol="SOL",
        price_usd=0.01,
        volume_h24=500_000.0,
        volume_h6=100_000.0,
        volume_h1=20_000.0,
        volume_m5=1_000.0,
        buys_h1=150,
        sells_h1=100,
        buys_h24=2000,
        sells_h24=1500,
        price_change_h1=5.0,
        price_change_h24=12.0,
        liquidity_usd=200_000.0,
        market_cap=1_000_000.0,
        fdv=2_000_000.0,
        holders_count=500,
        holders_source="geckoterminal",
        pair_created_at_ms=None,
        raw={},
    )
    defaults.update(overrides)
    return PairSnapshot(**defaults)  # type: ignore[arg-type]


class TestScoreHotness:
    def test_returns_score_and_tags(self) -> None:
        pair = _make_pair()
        score, tags = score_hotness(pair)
        assert isinstance(score, float)
        assert 0 <= score <= 100
        assert isinstance(tags, list)

    def test_score_bounded_0_100(self) -> None:
        # Minimal pair should have low but positive score.
        low = _make_pair(volume_h24=0, liquidity_usd=0, buys_h1=0, sells_h1=0, price_change_h1=-20.0)
        score_low, _ = score_hotness(low)
        assert score_low >= 0

        # Maxed-out pair should not exceed 100.
        high = _make_pair(
            volume_h24=10_000_000,
            liquidity_usd=5_000_000,
            buys_h1=5000,
            sells_h1=0,
            price_change_h1=50.0,
        )
        score_high, _ = score_hotness(high, boost_total=1000, has_profile=True)
        assert score_high <= 100

    def test_higher_volume_means_higher_score(self) -> None:
        low_vol = _make_pair(volume_h24=10_000)
        high_vol = _make_pair(volume_h24=5_000_000)
        s_low, _ = score_hotness(low_vol)
        s_high, _ = score_hotness(high_vol)
        assert s_high > s_low

    def test_tags_high_volume(self) -> None:
        pair = _make_pair(volume_h24=2_000_000)
        _, tags = score_hotness(pair)
        assert "high-volume" in tags

    def test_tags_momentum(self) -> None:
        pair = _make_pair(price_change_h1=15.0)
        _, tags = score_hotness(pair)
        assert "momentum" in tags

    def test_tags_buy_pressure(self) -> None:
        pair = _make_pair(buys_h1=200, sells_h1=10)
        _, tags = score_hotness(pair)
        assert "buy-pressure" in tags

    def test_tags_boosted(self) -> None:
        pair = _make_pair()
        _, tags = score_hotness(pair, boost_total=200, boost_count=5)
        assert "boosted" in tags
        assert "repeat-boosts" in tags

    def test_tags_listed_profile(self) -> None:
        pair = _make_pair()
        _, tags = score_hotness(pair, has_profile=True)
        assert "listed-profile" in tags

    def test_detail_returns_components(self) -> None:
        pair = _make_pair()
        score, tags, components = score_hotness_detail(pair)
        assert "volume" in components
        assert "transactions" in components
        assert "liquidity" in components
        assert "momentum" in components
        assert "flow" in components
        assert "boost" in components
        assert "recency" in components
        assert "profile" in components
        # Components should sum to the total score.
        assert abs(sum(components.values()) - score) < 0.1

    def test_deterministic(self) -> None:
        pair = _make_pair()
        s1, t1 = score_hotness(pair, boost_total=50)
        s2, t2 = score_hotness(pair, boost_total=50)
        assert s1 == s2
        assert t1 == t2


class TestDistributionHeuristics:
    def test_balanced_status(self) -> None:
        pair = _make_pair(liquidity_usd=100_000, market_cap=1_000_000, volume_h24=200_000)
        candidate = HotTokenCandidate(
            pair=pair, score=50.0, boost_total=0, boost_count=0,
            has_profile=False, discovery="boosts",
        )
        result = build_distribution_heuristics(candidate)
        assert result["status"] == "balanced"

    def test_concentrated_liquidity(self) -> None:
        pair = _make_pair(liquidity_usd=10_000, market_cap=1_000_000, volume_h24=5_000)
        candidate = HotTokenCandidate(
            pair=pair, score=50.0, boost_total=0, boost_count=0,
            has_profile=False, discovery="boosts",
        )
        result = build_distribution_heuristics(candidate)
        assert result["status"] == "concentrated-liquidity"

    def test_speculative_flow(self) -> None:
        pair = _make_pair(liquidity_usd=50_000, market_cap=500_000, volume_h24=500_000)
        candidate = HotTokenCandidate(
            pair=pair, score=50.0, boost_total=0, boost_count=0,
            has_profile=False, discovery="boosts",
        )
        result = build_distribution_heuristics(candidate)
        assert result["status"] == "speculative-flow"
