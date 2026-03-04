from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from .alerts import send_test_alert
from .client import DexScreenerClient
from .config import DEFAULT_CHAINS, ScanFilters
from .models import HotTokenCandidate
from .scanner import HotScanner
from .scoring import build_distribution_heuristics
from .state import ScanPreset, StateStore
from .task_runner import execute_task_once, select_due_tasks

mcp = FastMCP("dexscreener-cli-mcp-tool")
SCAN_PROFILE_NAMES: tuple[str, ...] = ("strict", "balanced", "discovery")
SCAN_PROFILE_BASELINES: dict[str, dict[str, float]] = {
    "strict": {"min_liquidity_usd": 40_000.0, "min_volume_h24_usd": 120_000.0, "min_txns_h1": 110.0},
    "balanced": {"min_liquidity_usd": 28_000.0, "min_volume_h24_usd": 70_000.0, "min_txns_h1": 55.0},
    "discovery": {"min_liquidity_usd": 15_000.0, "min_volume_h24_usd": 20_000.0, "min_txns_h1": 12.0},
}


def _serialize_candidate(candidate: HotTokenCandidate) -> dict[str, Any]:
    pair = candidate.pair
    analytics = candidate.analytics
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
        "analytics": {
            "compressionScore": analytics.compression_score,
            "breakoutReadiness": analytics.breakout_readiness,
            "volumeVelocity": analytics.volume_velocity,
            "txnVelocity": analytics.txn_velocity,
            "relativeStrength": analytics.relative_strength,
            "chainBaselineH1": analytics.chain_baseline_h1,
            "boostVelocityPerMin": analytics.boost_velocity,
            "momentumHalfLifeMin": analytics.momentum_half_life_min,
            "momentumDecayRatio": analytics.momentum_decay_ratio,
            "fastDecay": analytics.fast_decay,
            "baseScore": analytics.base_score,
            "riskScore": analytics.risk_score,
            "riskPenalty": analytics.risk_penalty,
            "riskFlags": analytics.risk_flags,
            "scoreComponents": analytics.score_components,
        },
    }


def _parse_chains(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return DEFAULT_CHAINS
    values = tuple(c.strip().lower() for c in raw.split(",") if c.strip())
    return values or DEFAULT_CHAINS


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [str(value)]


def _build_alert_config(
    *,
    webhook_url: str | None = None,
    discord_webhook_url: str | None = None,
    telegram_bot_token: str | None = None,
    telegram_chat_id: str | None = None,
    alert_min_score: float | None = None,
    alert_cooldown_seconds: int | None = None,
    alert_template: str | None = None,
    alert_top_n: int | None = None,
    alert_min_liquidity_usd: float | None = None,
    alert_max_vol_liq_ratio: float | None = None,
    alert_blocked_terms: str | list[str] | None = None,
    alert_blocked_chains: str | list[str] | None = None,
    webhook_extra: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
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
    if alert_template is not None:
        alerts["template"] = alert_template
    if alert_top_n is not None:
        alerts["top_n"] = alert_top_n
    if alert_min_liquidity_usd is not None:
        alerts["min_liquidity_usd"] = alert_min_liquidity_usd
    if alert_max_vol_liq_ratio is not None:
        alerts["max_vol_liq_ratio"] = alert_max_vol_liq_ratio
    terms = _as_list(alert_blocked_terms)
    if terms:
        alerts["blocked_terms"] = terms
    chains = [c.lower() for c in _as_list(alert_blocked_chains)]
    if chains:
        alerts["blocked_chains"] = chains
    if webhook_extra:
        alerts["webhook_extra"] = webhook_extra
    return alerts or None


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
    alert_template: str | None = None,
    alert_top_n: int | None = None,
    alert_min_liquidity_usd: float | None = None,
    alert_max_vol_liq_ratio: float | None = None,
    alert_blocked_terms: str | list[str] | None = None,
    alert_blocked_chains: str | list[str] | None = None,
    webhook_extra: dict[str, Any] | None = None,
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
    alerts = _build_alert_config(
        webhook_url=webhook_url,
        discord_webhook_url=discord_webhook_url,
        telegram_bot_token=telegram_bot_token,
        telegram_chat_id=telegram_chat_id,
        alert_min_score=alert_min_score,
        alert_cooldown_seconds=alert_cooldown_seconds,
        alert_template=alert_template,
        alert_top_n=alert_top_n,
        alert_min_liquidity_usd=alert_min_liquidity_usd,
        alert_max_vol_liq_ratio=alert_max_vol_liq_ratio,
        alert_blocked_terms=alert_blocked_terms,
        alert_blocked_chains=alert_blocked_chains,
        webhook_extra=webhook_extra,
    )

    task = store.create_task(
        name=name,
        preset=preset,
        filters=overrides or None,
        interval_seconds=interval_seconds,
        alerts=alerts,
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

    async with DexScreenerClient() as client:
        scanner = HotScanner(client)
        result = await execute_task_once(
            store=store,
            scanner=scanner,
            task=row,
            mode="mcp-manual",
            fire_alerts=fire_alerts,
            mark_running=False,
            block_on_error=False,
        )
    candidates = result.get("candidates", [])
    if not isinstance(candidates, list):
        candidates = []
    return {
        "ok": result.get("ok", False),
        "error": result.get("error"),
        "task": result.get("task", row.to_dict()),
        "filters": result.get("filters"),
        "results": [_serialize_candidate(c) for c in candidates if isinstance(c, HotTokenCandidate)],
        "alert": result.get("alert"),
        "run": result.get("run"),
    }


@mcp.tool()
async def run_due_tasks(
    default_interval_seconds: int = 120,
    fire_alerts: bool = True,
) -> dict[str, Any]:
    """Run one scheduler cycle for all due non-blocked tasks."""
    store = StateStore()
    due = select_due_tasks(
        store=store,
        task_name_or_id=None,
        all_tasks=True,
        default_interval_seconds=default_interval_seconds,
    )

    cycle_results: list[dict[str, Any]] = []
    async with DexScreenerClient() as client:
        scanner = HotScanner(client)
        for task in due:
            result = await execute_task_once(
                store=store,
                scanner=scanner,
                task=task,
                mode="mcp-daemon",
                fire_alerts=fire_alerts,
                mark_running=True,
                block_on_error=True,
            )
            candidates = result.get("candidates", [])
            if not isinstance(candidates, list):
                candidates = []
            cycle_results.append(
                {
                    "ok": result.get("ok", False),
                    "error": result.get("error"),
                    "task": result.get("task", task.to_dict()),
                    "resultCount": len(candidates),
                    "top": _serialize_candidate(candidates[0]) if candidates and isinstance(candidates[0], HotTokenCandidate) else None,
                    "alert": result.get("alert"),
                    "run": result.get("run"),
                }
            )
    return {"dueTasks": len(due), "runs": cycle_results}


@mcp.tool()
async def test_task_alert(task: str, with_scan: bool = False) -> dict[str, Any]:
    """Send a test alert through task-configured channels."""
    store = StateStore()
    row = store.get_task(task)
    if not row:
        return {"error": f"Task '{task}' not found"}
    candidates: list[HotTokenCandidate] = []
    if with_scan:
        async with DexScreenerClient() as client:
            scanner = HotScanner(client)
            result = await execute_task_once(
                store=store,
                scanner=scanner,
                task=row,
                mode="mcp-test-scan",
                fire_alerts=False,
                mark_running=False,
                block_on_error=False,
            )
            raw_candidates = result.get("candidates", [])
            if isinstance(raw_candidates, list):
                candidates = [c for c in raw_candidates if isinstance(c, HotTokenCandidate)]
    alert_result = await send_test_alert(row, candidates=candidates)
    if alert_result.get("sent"):
        store.touch_task_alert(row.id)
    return {"task": row.to_dict(), "alert": alert_result}


@mcp.tool()
async def list_task_runs(task: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """List task run history."""
    store = StateStore()
    return [r.to_dict() for r in store.list_runs(task=task, limit=limit)]


@mcp.tool()
async def export_state_bundle() -> dict[str, Any]:
    """Export presets/tasks/runs into one JSON-compatible object."""
    store = StateStore()
    return store.export_bundle()


@mcp.tool()
async def import_state_bundle(bundle: dict[str, Any], mode: str = "merge") -> dict[str, Any]:
    """Import presets/tasks/runs from a bundle object."""
    if mode not in {"merge", "replace"}:
        return {"error": "Invalid mode. Use merge or replace."}
    store = StateStore()
    counts = store.import_bundle(bundle, mode=mode)  # type: ignore[arg-type]
    return {"ok": True, "mode": mode, "counts": counts}


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


@mcp.resource("dexscreener://profiles", name="profiles", description="Recommended scan profiles.")
async def resource_profiles() -> dict[str, Any]:
    return {"profiles": SCAN_PROFILE_BASELINES, "names": list(SCAN_PROFILE_NAMES)}


@mcp.resource("dexscreener://presets", name="presets", description="Current saved scan presets.")
async def resource_presets() -> dict[str, Any]:
    store = StateStore()
    return {"count": len(store.list_presets()), "items": [p.to_dict() for p in store.list_presets()]}


@mcp.resource("dexscreener://tasks", name="tasks", description="Current saved scan tasks.")
async def resource_tasks() -> dict[str, Any]:
    store = StateStore()
    return {"count": len(store.list_tasks()), "items": [t.to_dict() for t in store.list_tasks()]}


@mcp.prompt("alpha_scan_plan")
def prompt_alpha_scan_plan(
    chains: str = "base,solana",
    profile: str = "balanced",
    objective: str = "Spot high-quality new runners with manageable risk",
) -> str:
    selected_profile = profile if profile in SCAN_PROFILE_NAMES else "balanced"
    baseline = SCAN_PROFILE_BASELINES[selected_profile]
    return (
        "Build an execution-first scan plan for dexscreener-cli-mcp-tool.\n"
        f"Objective: {objective}\n"
        f"Chains: {chains}\n"
        f"Profile: {selected_profile}\n"
        "Threshold baseline:\n"
        f"- min_liquidity_usd={baseline['min_liquidity_usd']}\n"
        f"- min_volume_h24_usd={baseline['min_volume_h24_usd']}\n"
        f"- min_txns_h1={int(baseline['min_txns_h1'])}\n"
        "Return:\n"
        "1) exact CLI commands,\n"
        "2) alert setup recommendation,\n"
        "3) fallback profile if no rows are found,\n"
        "4) operational risk checklist."
    )


@mcp.prompt("runner_triage")
def prompt_runner_triage(
    token_symbol: str,
    chain_id: str,
    score: float,
    risk_score: float,
    volume_h24: float,
    liquidity_usd: float,
) -> str:
    return (
        "Triage this candidate for short-term momentum trading.\n"
        f"Token: {chain_id}:{token_symbol}\n"
        f"score={score:.2f}, risk_score={risk_score:.2f}, "
        f"volume_h24={volume_h24:.0f}, liquidity_usd={liquidity_usd:.0f}\n"
        "Provide:\n"
        "1) quality verdict (A/B/C),\n"
        "2) failure mode to watch,\n"
        "3) invalidation trigger,\n"
        "4) whether to alert now or wait one cycle."
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
