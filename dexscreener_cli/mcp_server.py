from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

from .alerts import send_alerts
from .client import DexScreenerClient
from .config import DEFAULT_CHAINS, ScanFilters
from .models import HotTokenCandidate
from .scanner import HotScanner
from .scoring import build_distribution_heuristics
from .state import ScanPreset, StateStore

mcp = FastMCP("dexscreener-cli-mcp-tool")


def _serialize_candidate(candidate: HotTokenCandidate) -> dict[str, Any]:
    pair = candidate.pair
    return {
        "chainId": pair.chain_id,
        "tokenAddress": pair.base_address,
        "tokenSymbol": pair.base_symbol,
        "tokenName": pair.base_name,
        "pairAddress": pair.pair_address,
        "pairUrl": pair.pair_url,
        "dexId": pair.dex_id,
        "priceUsd": pair.price_usd,
        "priceChangeH1": pair.price_change_h1,
        "priceChangeH24": pair.price_change_h24,
        "volumeH24": pair.volume_h24,
        "volumeH1": pair.volume_h1,
        "txnsH1": pair.txns_h1,
        "liquidityUsd": pair.liquidity_usd,
        "marketCap": pair.market_cap,
        "fdv": pair.fdv,
        "ageHours": pair.age_hours,
        "boostTotal": candidate.boost_total,
        "boostCount": candidate.boost_count,
        "hasProfile": candidate.has_profile,
        "discovery": candidate.discovery,
        "score": candidate.score,
        "tags": candidate.tags,
    }


def _parse_chains(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return DEFAULT_CHAINS
    values = tuple(c.strip().lower() for c in raw.split(",") if c.strip())
    return values or DEFAULT_CHAINS


def _apply_task_overrides(filters: ScanFilters, payload: dict[str, Any] | None) -> ScanFilters:
    if not payload:
        return filters
    if payload.get("chains"):
        filters.chains = tuple(payload["chains"])
    if payload.get("limit") is not None:
        filters.limit = int(payload["limit"])
    if payload.get("min_liquidity_usd") is not None:
        filters.min_liquidity_usd = float(payload["min_liquidity_usd"])
    if payload.get("min_volume_h24_usd") is not None:
        filters.min_volume_h24_usd = float(payload["min_volume_h24_usd"])
    if payload.get("min_txns_h1") is not None:
        filters.min_txns_h1 = int(payload["min_txns_h1"])
    if payload.get("min_price_change_h1") is not None:
        filters.min_price_change_h1 = float(payload["min_price_change_h1"])
    return filters


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


@mcp.tool()
async def scan_hot_tokens(
    chains: str = ",".join(DEFAULT_CHAINS),
    limit: int = 20,
    min_liquidity_usd: float = 35_000.0,
    min_volume_h24_usd: float = 90_000.0,
    min_txns_h1: int = 80,
    min_price_change_h1: float = 0.0,
) -> list[dict[str, Any]]:
    """Scan hot tokens by chain using Dexscreener boosts/profiles + pair activity scoring."""
    chain_ids = tuple(c.strip().lower() for c in chains.split(",") if c.strip())
    async with DexScreenerClient() as client:
        scanner = HotScanner(client)
        filters = ScanFilters(
            chains=chain_ids or DEFAULT_CHAINS,
            limit=limit,
            min_liquidity_usd=min_liquidity_usd,
            min_volume_h24_usd=min_volume_h24_usd,
            min_txns_h1=min_txns_h1,
            min_price_change_h1=min_price_change_h1,
        )
        rows = await scanner.scan(filters)
        return [_serialize_candidate(c) for c in rows]


@mcp.tool()
async def save_preset(
    name: str,
    chains: str = ",".join(DEFAULT_CHAINS),
    limit: int = 20,
    min_liquidity_usd: float = 35_000.0,
    min_volume_h24_usd: float = 90_000.0,
    min_txns_h1: int = 80,
    min_price_change_h1: float = 0.0,
) -> dict[str, Any]:
    """Save a named scan preset."""
    filters = ScanFilters(
        chains=_parse_chains(chains),
        limit=limit,
        min_liquidity_usd=min_liquidity_usd,
        min_volume_h24_usd=min_volume_h24_usd,
        min_txns_h1=min_txns_h1,
        min_price_change_h1=min_price_change_h1,
    )
    store = StateStore()
    preset = store.save_preset(ScanPreset.from_filters(name=name, filters=filters))
    return preset.to_dict()


@mcp.tool()
async def list_presets() -> list[dict[str, Any]]:
    """List saved presets."""
    store = StateStore()
    return [p.to_dict() for p in store.list_presets()]


@mcp.tool()
async def create_task(
    name: str,
    preset: str | None = None,
    chains: str | None = None,
    limit: int | None = None,
    min_liquidity_usd: float | None = None,
    min_volume_h24_usd: float | None = None,
    min_txns_h1: int | None = None,
    min_price_change_h1: float | None = None,
    interval_seconds: int | None = None,
    webhook_url: str | None = None,
    discord_webhook_url: str | None = None,
    telegram_bot_token: str | None = None,
    telegram_chat_id: str | None = None,
    alert_min_score: float | None = None,
    alert_cooldown_seconds: int | None = None,
    notes: str = "",
) -> dict[str, Any]:
    """Create a new scan task."""
    store = StateStore()
    overrides: dict[str, Any] = {}
    if chains:
        overrides["chains"] = list(_parse_chains(chains))
    if limit is not None:
        overrides["limit"] = limit
    if min_liquidity_usd is not None:
        overrides["min_liquidity_usd"] = min_liquidity_usd
    if min_volume_h24_usd is not None:
        overrides["min_volume_h24_usd"] = min_volume_h24_usd
    if min_txns_h1 is not None:
        overrides["min_txns_h1"] = min_txns_h1
    if min_price_change_h1 is not None:
        overrides["min_price_change_h1"] = min_price_change_h1
    alerts: dict[str, Any] = {}
    if webhook_url is not None:
        alerts["webhook_url"] = webhook_url
    if discord_webhook_url is not None:
        alerts["discord_webhook_url"] = discord_webhook_url
    if telegram_bot_token is not None:
        alerts["telegram_bot_token"] = telegram_bot_token
    if telegram_chat_id is not None:
        alerts["telegram_chat_id"] = telegram_chat_id
    if alert_min_score is not None:
        alerts["min_score"] = alert_min_score
    if alert_cooldown_seconds is not None:
        alerts["cooldown_seconds"] = alert_cooldown_seconds

    task = store.create_task(
        name=name,
        preset=preset,
        filters=overrides or None,
        interval_seconds=interval_seconds,
        alerts=alerts or None,
        notes=notes,
    )
    return task.to_dict()


@mcp.tool()
async def list_tasks(status: str | None = None) -> list[dict[str, Any]]:
    """List scan tasks."""
    store = StateStore()
    if status and status not in {"todo", "running", "done", "blocked"}:
        return [{"error": "Invalid status. Use todo/running/done/blocked"}]
    rows = store.list_tasks(status=status) if status else store.list_tasks()
    return [r.to_dict() for r in rows]


@mcp.tool()
async def run_task_scan(task: str, fire_alerts: bool = True) -> dict[str, Any]:
    """Run a saved task scan and return ranked candidates."""
    store = StateStore()
    row = store.get_task(task)
    if not row:
        return {"error": f"Task '{task}' not found"}

    filters = ScanFilters(chains=DEFAULT_CHAINS)
    if row.preset:
        preset = store.get_preset(row.preset)
        if not preset:
            return {"error": f"Task preset '{row.preset}' not found"}
        filters = preset.to_filters()
    filters = _apply_task_overrides(filters, row.filters)

    async with DexScreenerClient() as client:
        scanner = HotScanner(client)
        rows = await scanner.scan(filters)
    store.touch_task_run(row.id)
    alert_result: dict[str, Any] = {"sent": False, "reason": "disabled", "channels": {}}
    if fire_alerts:
        refreshed = store.get_task(row.id) or row
        alert_result = await send_alerts(refreshed, rows)
        if alert_result.get("sent"):
            store.touch_task_alert(row.id)
    return {
        "task": row.to_dict(),
        "filters": {
            "chains": list(filters.chains),
            "limit": filters.limit,
            "min_liquidity_usd": filters.min_liquidity_usd,
            "min_volume_h24_usd": filters.min_volume_h24_usd,
            "min_txns_h1": filters.min_txns_h1,
            "min_price_change_h1": filters.min_price_change_h1,
        },
        "results": [_serialize_candidate(c) for c in rows],
        "alert": alert_result,
    }


@mcp.tool()
async def run_due_tasks(
    default_interval_seconds: int = 120,
    fire_alerts: bool = True,
) -> dict[str, Any]:
    """Run one scheduler cycle for all due non-blocked tasks."""
    store = StateStore()
    tasks = [t for t in store.list_tasks() if t.status not in {"blocked", "done"}]
    due: list[Any] = []
    now = datetime.now(UTC)
    for task in tasks:
        interval = task.interval_seconds or default_interval_seconds
        last_run = _parse_iso(task.last_run_at)
        if (last_run is None) or ((now - last_run).total_seconds() >= interval):
            due.append(task)

    cycle_results: list[dict[str, Any]] = []
    async with DexScreenerClient() as client:
        scanner = HotScanner(client)
        for task in due:
            filters = ScanFilters(chains=DEFAULT_CHAINS)
            if task.preset:
                preset = store.get_preset(task.preset)
                if preset:
                    filters = preset.to_filters()
            filters = _apply_task_overrides(filters, task.filters)
            rows = await scanner.scan(filters)
            store.touch_task_run(task.id)
            alert_result: dict[str, Any] = {"sent": False, "reason": "disabled", "channels": {}}
            if fire_alerts:
                refreshed = store.get_task(task.id) or task
                alert_result = await send_alerts(refreshed, rows)
                if alert_result.get("sent"):
                    store.touch_task_alert(task.id)
            cycle_results.append(
                {
                    "task": task.to_dict(),
                    "resultCount": len(rows),
                    "top": _serialize_candidate(rows[0]) if rows else None,
                    "alert": alert_result,
                }
            )
    return {"dueTasks": len(due), "runs": cycle_results}


@mcp.tool()
async def search_pairs(query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search Dexscreener pairs by token name/symbol/address."""
    async with DexScreenerClient() as client:
        scanner = HotScanner(client)
        pairs = await scanner.search(query=query, limit=limit)
        return [
            {
                "chainId": p.chain_id,
                "pairAddress": p.pair_address,
                "tokenAddress": p.base_address,
                "tokenSymbol": p.base_symbol,
                "dexId": p.dex_id,
                "priceUsd": p.price_usd,
                "volumeH24": p.volume_h24,
                "txnsH1": p.txns_h1,
                "liquidityUsd": p.liquidity_usd,
                "pairUrl": p.pair_url,
            }
            for p in pairs
        ]


@mcp.tool()
async def inspect_token(chain_id: str, token_address: str) -> dict[str, Any]:
    """Inspect a token and return best pair + concentration proxies."""
    async with DexScreenerClient() as client:
        scanner = HotScanner(client)
        pairs = await scanner.inspect_token(chain_id=chain_id, token_address=token_address)
        if not pairs:
            return {"error": "Token not found or no pairs available"}
        best = pairs[0]
        candidate = HotTokenCandidate(
            pair=best,
            score=0.0,
            boost_total=0.0,
            boost_count=0,
            has_profile=False,
            discovery="inspect",
            tags=[],
        )
        return {
            "bestPair": {
                "chainId": best.chain_id,
                "pairAddress": best.pair_address,
                "pairUrl": best.pair_url,
                "tokenAddress": best.base_address,
                "tokenSymbol": best.base_symbol,
                "priceUsd": best.price_usd,
                "volumeH24": best.volume_h24,
                "txnsH1": best.txns_h1,
                "liquidityUsd": best.liquidity_usd,
                "marketCap": best.market_cap,
                "fdv": best.fdv,
                "priceChangeH1": best.price_change_h1,
                "priceChangeH24": best.price_change_h24,
            },
            "distributionProxy": build_distribution_heuristics(candidate),
            "note": "Public Dexscreener API does not expose holder-level distribution directly.",
            "additionalPairCount": max(len(pairs) - 1, 0),
        }


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
