from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from .alerts import send_test_alert, validate_webhook_url
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
    "strict": {"min_liquidity_usd": 35_000.0, "min_volume_h24_usd": 90_000.0, "min_txns_h1": 50.0},
    "balanced": {"min_liquidity_usd": 20_000.0, "min_volume_h24_usd": 40_000.0, "min_txns_h1": 25.0},
    "discovery": {"min_liquidity_usd": 8_000.0, "min_volume_h24_usd": 10_000.0, "min_txns_h1": 5.0},
}

_QUICKSTART_PLATFORMS: tuple[str, ...] = ("windows-cmd", "windows-powershell", "mac-linux")
_QUICKSTART_GOALS: tuple[str, ...] = ("live", "hot", "mcp", "all")


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _quickstart_platform(platform: str) -> str:
    normalized = platform.strip().lower()
    return normalized if normalized in _QUICKSTART_PLATFORMS else "windows-cmd"


def _quickstart_goal(goal: str) -> str:
    normalized = goal.strip().lower()
    return normalized if normalized in _QUICKSTART_GOALS else "live"


def _quickstart_paths(platform: str) -> tuple[str, str]:
    root = _repo_root()
    if platform == "mac-linux":
        return (str(root / ".venv" / "bin" / "ds"), str(root / ".venv" / "bin" / "dexscreener-mcp"))
    return (str(root / ".venv" / "Scripts" / "ds.exe"), str(root / ".venv" / "Scripts" / "dexscreener-mcp.exe"))


def _quickstart_cd(platform: str) -> str:
    root = str(_repo_root())
    if platform == "windows-cmd":
        return f"cd /d {root}"
    return f"cd {root}"


def _quickstart_prefix(platform: str) -> str:
    if platform == "windows-cmd":
        return r".\.venv\Scripts\ds.exe"
    if platform == "windows-powershell":
        return r".\.venv\Scripts\ds.exe"
    return "ds"


def _quickstart_commands(platform: str, goal: str) -> list[str]:
    selected_platform = _quickstart_platform(platform)
    selected_goal = _quickstart_goal(goal)
    prefix = _quickstart_prefix(selected_platform)
    _cli_path, mcp_path = _quickstart_paths(selected_platform)
    live = (
        f"{prefix} new-runners-watch --chain=solana --watch-chains=solana,base "
        "--profile=discovery --max-age-hours=48 --include-unknown-age --interval=2"
    )
    hot = f"{prefix} hot --chains=solana,base --limit=10"
    if selected_goal == "hot":
        return [_quickstart_cd(selected_platform), f"{prefix} doctor", hot]
    if selected_goal == "mcp":
        return [_quickstart_cd(selected_platform), f"{prefix} doctor", mcp_path]
    if selected_goal == "all":
        return [_quickstart_cd(selected_platform), f"{prefix} doctor", f"{prefix} setup", hot, live]
    return [_quickstart_cd(selected_platform), f"{prefix} doctor", live]


def _quickstart_terminal(platform: str) -> str:
    selected_platform = _quickstart_platform(platform)
    if selected_platform == "windows-cmd":
        return "Command Prompt"
    if selected_platform == "windows-powershell":
        return "PowerShell"
    return "Terminal"


def _quickstart_expectation(goal: str) -> str:
    selected_goal = _quickstart_goal(goal)
    if selected_goal == "mcp":
        return "The MCP server binary will start and wait for an MCP-compatible agent connection over stdio."
    if selected_goal == "hot":
        return "You should see a populated one-shot hot-token scan across Solana and Base."
    if selected_goal == "all":
        return "You should complete setup, see a one-shot hot scan, then enter the live new-runners board."
    return "You should enter the live new-runners board with Solana active and Base available via hotkey switching."


def _quickstart_common_mistakes(platform: str) -> list[str]:
    selected_platform = _quickstart_platform(platform)
    if selected_platform == "windows-cmd":
        return [
            "Do not press Enter in the middle of a command after an option like --profile.",
            "Prefer --flag=value style on Windows for copy-paste safety.",
            "If ds is not recognized, run .\\.venv\\Scripts\\ds.exe from the repo root.",
        ]
    if selected_platform == "windows-powershell":
        return [
            "If ds is not recognized, run .\\.venv\\Scripts\\ds.exe from the repo root.",
            "Use backtick (`) for PowerShell line continuation only if you really need multi-line commands.",
        ]
    return [
        "If ds is not recognized, activate the virtual environment or run the binary from .venv/bin.",
    ]


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
        "holdersCount": pair.holders_count,
        "holdersSource": pair.holders_source,
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
            "scoreComponents": analytics.score_components,
        },
    }


_MAX_NAME_LEN = 200
_MAX_TEMPLATE_LEN = 2000
_MAX_CHAINS_LEN = 500
_MAX_NOTES_LEN = 1000
_MAX_LIMIT = 100
_MAX_INTERVAL_SECONDS = 86_400
_MAX_TASK_RUNS = 500
_MAX_IMPORT_PRESETS = 100
_MAX_IMPORT_TASKS = 500
_MAX_IMPORT_RUNS = 5_000


def _clamp_str(value: str, max_len: int, label: str = "value") -> str:
    """Truncate a string to max_len and warn if it was too long."""
    if len(value) > max_len:
        return value[:max_len]
    return value


def _bounded_int(value: int, *, minimum: int, maximum: int, label: str) -> int:
    if value < minimum or value > maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
    return value


def _bounded_float(value: float, *, minimum: float, maximum: float | None = None, label: str) -> float:
    if value < minimum:
        raise ValueError(f"{label} must be >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{label} must be <= {maximum}")
    return value


def _parse_chains(raw: str | None) -> tuple[str, ...]:
    if not raw:
        return DEFAULT_CHAINS
    raw = _clamp_str(raw, _MAX_CHAINS_LEN, "chains")
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
    min_liquidity_usd: float = 20_000.0,
    min_volume_h24_usd: float = 40_000.0,
    min_txns_h1: int = 30,
    min_price_change_h1: float = -10.0,
) -> list[dict[str, Any]]:
    """Scan and rank the hottest tokens on Dexscreener right now.

    Discovers tokens from Dexscreener boosts and profiles, scores them by volume,
    liquidity, momentum, and flow pressure, then returns ranked results.
    All data comes from free public APIs (Dexscreener, GeckoTerminal, Blockscout, Honeypot.is).

    Use this when a user asks: "what's hot", "show me trending tokens",
    "find tokens on solana", "what should I look at", "find degen plays", etc.

    Built-in profile presets for quick filtering:
    - Discovery (degen/alpha): min_liquidity_usd=8000, min_volume_h24_usd=10000, min_txns_h1=5
    - Balanced (standard): min_liquidity_usd=20000, min_volume_h24_usd=40000, min_txns_h1=25
    - Strict (conservative): min_liquidity_usd=35000, min_volume_h24_usd=90000, min_txns_h1=50

    Args:
        chains: Comma-separated chain IDs (solana, base, ethereum, bsc, arbitrum).
        limit: Max number of tokens to return (default 20).
        min_liquidity_usd: Minimum pair liquidity in USD.
        min_volume_h24_usd: Minimum 24h trading volume in USD.
        min_txns_h1: Minimum transactions in the last hour.
        min_price_change_h1: Minimum 1h price change percent (use negative to allow dips).

    Returns a list of scored token objects with price, volume, liquidity,
    holder count, score (0-100), tags, and detailed analytics.
    Score ranges: 80+ = very hot, 60-80 = interesting, 40-60 = moderate, <40 = weak.

    Tip: Combine scan results with safety checkers (RugCheck, GoPlus), DEX aggregators
    (Jupiter, 1inch), or chart tools (TradingView) for a complete discovery-to-trade workflow.
    """
    limit = _bounded_int(limit, minimum=1, maximum=_MAX_LIMIT, label="limit")
    min_liquidity_usd = _bounded_float(min_liquidity_usd, minimum=0.0, label="min_liquidity_usd")
    min_volume_h24_usd = _bounded_float(min_volume_h24_usd, minimum=0.0, label="min_volume_h24_usd")
    min_txns_h1 = _bounded_int(min_txns_h1, minimum=0, maximum=1_000_000, label="min_txns_h1")
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
async def get_rate_budget_stats(
    query: str = "solana",
    chain_id: str = "solana",
    token_address: str | None = None,
) -> dict[str, Any]:
    """Check API rate limit usage and remaining budget.

    Use this to verify API health or debug rate limiting issues.
    Returns request counts, remaining budget, and timing info.
    """
    async with DexScreenerClient() as client:
        if query.strip():
            try:
                await client.search_pairs(query.strip())
            except Exception:
                pass
        if token_address:
            try:
                await client.get_token_pairs(chain_id.strip().lower(), token_address.strip())
            except Exception:
                pass
        return await client.get_runtime_stats()


@mcp.tool()
async def save_preset(
    name: str,
    chains: str = ",".join(DEFAULT_CHAINS),
    limit: int = 20,
    min_liquidity_usd: float = 20_000.0,
    min_volume_h24_usd: float = 40_000.0,
    min_txns_h1: int = 30,
    min_price_change_h1: float = -10.0,
) -> dict[str, Any]:
    """Save a named scan preset with custom filter thresholds.

    Presets let you save and reuse filter configurations.
    Use this when a user says "save these settings", "create a preset", etc.
    The preset named "default" is auto-loaded on every scan.
    """
    name = _clamp_str(name, _MAX_NAME_LEN, "name")
    limit = _bounded_int(limit, minimum=1, maximum=_MAX_LIMIT, label="limit")
    min_liquidity_usd = _bounded_float(min_liquidity_usd, minimum=0.0, label="min_liquidity_usd")
    min_volume_h24_usd = _bounded_float(min_volume_h24_usd, minimum=0.0, label="min_volume_h24_usd")
    min_txns_h1 = _bounded_int(min_txns_h1, minimum=0, maximum=1_000_000, label="min_txns_h1")
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
    """List all saved scan presets with their filter configurations.

    Use this to see what presets are available before scanning.
    """
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
    """Create a scheduled scan task with optional alert channels.

    Tasks run on a schedule and can send alerts to Discord, Telegram, or webhooks
    when they find tokens above a score threshold.

    Use this when a user says "set up alerts", "monitor for new tokens",
    "notify me when something hot appears", etc.
    """
    name = _clamp_str(name, _MAX_NAME_LEN, "name")
    notes = _clamp_str(notes, _MAX_NOTES_LEN, "notes")
    if preset:
        preset = _clamp_str(preset, _MAX_NAME_LEN, "preset")
    if alert_template:
        alert_template = _clamp_str(alert_template, _MAX_TEMPLATE_LEN, "alert_template")
    # Validate webhook URLs at storage time to prevent SSRF.
    if webhook_url:
        validate_webhook_url(webhook_url)
    if discord_webhook_url:
        validate_webhook_url(discord_webhook_url)
    store = StateStore()
    if preset and not store.get_preset(preset):
        raise ValueError(f"Preset '{preset}' not found")
    overrides: dict[str, Any] = {}
    if chains:
        overrides["chains"] = list(_parse_chains(chains))
    if limit is not None:
        limit = _bounded_int(limit, minimum=1, maximum=_MAX_LIMIT, label="limit")
        overrides["limit"] = limit
    if min_liquidity_usd is not None:
        min_liquidity_usd = _bounded_float(min_liquidity_usd, minimum=0.0, label="min_liquidity_usd")
        overrides["min_liquidity_usd"] = min_liquidity_usd
    if min_volume_h24_usd is not None:
        min_volume_h24_usd = _bounded_float(min_volume_h24_usd, minimum=0.0, label="min_volume_h24_usd")
        overrides["min_volume_h24_usd"] = min_volume_h24_usd
    if min_txns_h1 is not None:
        min_txns_h1 = _bounded_int(min_txns_h1, minimum=0, maximum=1_000_000, label="min_txns_h1")
        overrides["min_txns_h1"] = min_txns_h1
    if min_price_change_h1 is not None:
        overrides["min_price_change_h1"] = min_price_change_h1
    if interval_seconds is not None:
        interval_seconds = _bounded_int(
            interval_seconds,
            minimum=15,
            maximum=_MAX_INTERVAL_SECONDS,
            label="interval_seconds",
        )
    if alert_cooldown_seconds is not None:
        alert_cooldown_seconds = _bounded_int(
            alert_cooldown_seconds,
            minimum=0,
            maximum=_MAX_INTERVAL_SECONDS,
            label="alert_cooldown_seconds",
        )
    if alert_top_n is not None:
        alert_top_n = _bounded_int(alert_top_n, minimum=1, maximum=10, label="alert_top_n")
    if alert_min_score is not None:
        alert_min_score = _bounded_float(alert_min_score, minimum=0.0, maximum=100.0, label="alert_min_score")
    if alert_min_liquidity_usd is not None:
        alert_min_liquidity_usd = _bounded_float(
            alert_min_liquidity_usd,
            minimum=0.0,
            label="alert_min_liquidity_usd",
        )
    if alert_max_vol_liq_ratio is not None:
        alert_max_vol_liq_ratio = _bounded_float(
            alert_max_vol_liq_ratio,
            minimum=0.0,
            label="alert_max_vol_liq_ratio",
        )
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
    """List all scan tasks and their current status.

    Shows task name, preset, interval, alert config, and status (todo/running/done/blocked).
    """
    store = StateStore()
    if status and status not in {"todo", "running", "done", "blocked"}:
        return [{"error": "Invalid status. Use todo/running/done/blocked"}]
    rows = store.list_tasks(status=status) if status else store.list_tasks()
    return [r.to_dict() for r in rows]


@mcp.tool()
async def run_task_scan(task: str, fire_alerts: bool = True) -> dict[str, Any]:
    """Run a scan task once and return scored token results.

    Executes the task's filters, scores tokens, optionally fires alerts,
    and records the run. Use this to manually trigger a task scan.
    """
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
    """Run one scheduler cycle - executes all tasks that are due.

    Checks each task's interval and last run time, runs due tasks,
    fires alerts if thresholds are met, and records results.
    """
    default_interval_seconds = _bounded_int(
        default_interval_seconds,
        minimum=15,
        maximum=_MAX_INTERVAL_SECONDS,
        label="default_interval_seconds",
    )
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
                    "top": (
                        _serialize_candidate(candidates[0])
                        if candidates and isinstance(candidates[0], HotTokenCandidate)
                        else None
                    ),
                    "alert": result.get("alert"),
                    "run": result.get("run"),
                }
            )
    return {"dueTasks": len(due), "runs": cycle_results}


@mcp.tool()
async def test_task_alert(task: str, with_scan: bool = False) -> dict[str, Any]:
    """Send a test alert through a task's configured channels (Discord/Telegram/webhook).

    Use this to verify alert delivery before relying on automated alerts.
    Set with_scan=True to include real scan data in the test alert.
    """
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
    """List historical task run records with results and timing.

    Shows when each task ran, how many tokens were found, top score, and alert status.
    """
    limit = _bounded_int(limit, minimum=1, maximum=_MAX_TASK_RUNS, label="limit")
    store = StateStore()
    return [r.to_dict() for r in store.list_runs(task=task, limit=limit)]


@mcp.tool()
async def export_state_bundle() -> dict[str, Any]:
    """Export all presets, tasks, and run history as a single JSON bundle.

    Use this for backup, sharing configurations, or migrating to another machine.
    """
    store = StateStore()
    return store.export_bundle()


@mcp.tool()
async def import_state_bundle(bundle: dict[str, Any], mode: str = "merge") -> dict[str, Any]:
    """Import presets, tasks, and runs from a previously exported bundle.

    Mode 'merge' adds new items without removing existing ones.
    Mode 'replace' overwrites everything with the bundle contents.
    """
    if mode not in {"merge", "replace"}:
        return {"error": "Invalid mode. Use merge or replace."}
    if not isinstance(bundle, dict):
        return {"error": "Bundle must be a JSON object"}
    presets = bundle.get("presets", [])
    tasks = bundle.get("tasks", [])
    runs = bundle.get("runs", [])
    if not isinstance(presets, list) or not isinstance(tasks, list) or not isinstance(runs, list):
        return {"error": "Bundle presets/tasks/runs must be arrays"}
    # Bound imported items to prevent resource exhaustion.
    if len(presets) > _MAX_IMPORT_PRESETS:
        return {"error": f"Bundle exceeds max {_MAX_IMPORT_PRESETS} presets"}
    if len(tasks) > _MAX_IMPORT_TASKS:
        return {"error": f"Bundle exceeds max {_MAX_IMPORT_TASKS} tasks"}
    if len(runs) > _MAX_IMPORT_RUNS:
        return {"error": f"Bundle exceeds max {_MAX_IMPORT_RUNS} runs"}
    store = StateStore()
    counts = store.import_bundle(bundle, mode=mode)  # type: ignore[arg-type]
    return {"ok": True, "mode": mode, "counts": counts}


@mcp.tool()
async def search_pairs(query: str, limit: int = 20) -> list[dict[str, Any]]:
    """Search for tokens on Dexscreener by name, symbol, or contract address.

    Use this when a user asks "find pepe", "search for <token>",
    "look up this address", etc. Returns matching pairs with price,
    volume, liquidity, and pair URL.
    """
    limit = _bounded_int(limit, minimum=1, maximum=_MAX_LIMIT, label="limit")
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
    """Deep-dive inspection of a specific token by chain and address.

    Returns the best trading pair, price data, volume, liquidity, market cap,
    and concentration proxy analysis. Use this when a user provides a specific
    token address and wants detailed information.
    """
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
            "note": (
                "Dexscreener public API does not expose holder-level ownership tables. "
                "Use holdersCount/holdersSource when available."
            ),
            "additionalPairCount": max(len(pairs) - 1, 0),
        }


@mcp.tool()
async def get_cli_quickstart(
    platform: str = "windows-cmd",
    goal: str = "live",
) -> dict[str, Any]:
    """Return exact CLI commands for a user's platform and goal.

    Use this when a user asks "how do I run this?", "give me copy-paste commands",
    "what should I run on Windows?", or "how do I start the live terminal?".

    Platforms:
    - windows-cmd
    - windows-powershell
    - mac-linux

    Goals:
    - live
    - hot
    - mcp
    - all
    """
    selected_platform = _quickstart_platform(platform)
    selected_goal = _quickstart_goal(goal)
    cli_path, mcp_path = _quickstart_paths(selected_platform)
    return {
        "platform": selected_platform,
        "goal": selected_goal,
        "terminalToOpen": _quickstart_terminal(selected_platform),
        "repoRoot": str(_repo_root()),
        "cliPath": cli_path,
        "mcpPath": mcp_path,
        "commands": _quickstart_commands(selected_platform, selected_goal),
        "whatToExpect": _quickstart_expectation(selected_goal),
        "commonMistakes": _quickstart_common_mistakes(selected_platform),
        "notes": [
            "Prefer --flag=value style on Windows for copy-paste safety.",
            "Live boards are CLI-only and use live API polling, not websocket streaming.",
            "The current default Dex cache TTL is 10 seconds and can be overridden with DS_CACHE_TTL_SECONDS.",
        ],
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


@mcp.resource("dexscreener://cli-guide", name="cli-guide", description="CLI-first onboarding and troubleshooting guide.")
async def resource_cli_guide() -> dict[str, Any]:
    return {
        "recommendedLiveCommand": (
            r".\.venv\Scripts\ds.exe new-runners-watch --chain=solana --watch-chains=solana,base "
            r"--profile=discovery --max-age-hours=48 --include-unknown-age --interval=2"
        ),
        "recommendedHotCommand": r".\.venv\Scripts\ds.exe hot --chains=solana,base --limit=10",
        "recommendedQuickstartCommand": r".\.venv\Scripts\ds.exe quickstart --shell cmd --goal live",
        "windowsFirstTerminal": "Command Prompt",
        "liveModeNotes": [
            "Live boards are CLI-only and use timed polling, not websocket streaming.",
            "The default Dex cache TTL is 10 seconds and can be overridden with DS_CACHE_TTL_SECONDS.",
            "If a live board is sparse, widen it with --profile=discovery --max-age-hours=48 --include-unknown-age.",
        ],
        "commonMistakes": [
            "Pressing Enter too early after --profile or another option that needs a value.",
            "Using ds before activating the environment or before using the .venv path.",
            "Using PowerShell backticks in Command Prompt.",
        ],
    }


@mcp.prompt("alpha_scan_plan")
def prompt_alpha_scan_plan(
    chains: str = "base,solana",
    profile: str = "balanced",
    objective: str = "Spot high-quality new runners with strong flow and participation",
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
        "4) operational execution checklist."
    )


@mcp.prompt("runner_triage")
def prompt_runner_triage(
    token_symbol: str,
    chain_id: str,
    score: float,
    holders_count: int | None,
    volume_h24: float,
    liquidity_usd: float,
) -> str:
    holders_label = str(holders_count) if holders_count is not None else "n/a"
    return (
        "Triage this candidate for short-term momentum trading.\n"
        f"Token: {chain_id}:{token_symbol}\n"
        f"score={score:.2f}, holders={holders_label}, "
        f"volume_h24={volume_h24:.0f}, liquidity_usd={liquidity_usd:.0f}\n"
        "Provide:\n"
        "1) quality verdict (A/B/C),\n"
        "2) failure mode to watch,\n"
        "3) invalidation trigger,\n"
        "4) whether to alert now or wait one cycle."
    )


@mcp.prompt("cli_quickstart_guide")
def prompt_cli_quickstart_guide(
    platform: str = "windows-cmd",
    goal: str = "live",
) -> str:
    selected_platform = _quickstart_platform(platform)
    selected_goal = _quickstart_goal(goal)
    commands = "\n".join(f"- {command}" for command in _quickstart_commands(selected_platform, selected_goal))
    return (
        "Give a zero-assumption quickstart for dexscreener-cli-mcp-tool.\n"
        f"Platform: {selected_platform}\n"
        f"Goal: {selected_goal}\n"
        "Return:\n"
        "1) which terminal/app to open first,\n"
        "2) exact copy-paste commands,\n"
        "3) one common mistake to avoid,\n"
        "4) what the user should expect to see.\n"
        "Commands:\n"
        f"{commands}"
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
