from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer
from rich import box
from rich.columns import Columns
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .alerts import send_test_alert
from .client import DexScreenerClient
from .config import DEFAULT_CHAINS, ScanFilters
from .models import HotTokenCandidate
from .scanner import HotScanner
from .state import ScanPreset, ScanTask, StateStore
from .task_runner import execute_task_once, select_due_tasks, task_filters as runner_task_filters
from .ui import (
    build_header,
    render_chain_heat_table,
    render_distribution_panel,
    render_flow_panel,
    render_hot_table,
    render_rank_movers_table,
    render_new_runner_spotlight,
    render_new_runners_table,
    render_top_runner_cards,
    render_pair_detail,
    render_search_table,
)

app = typer.Typer(
    add_completion=False,
    help="Visual Dexscreener scanner CLI. Spot hot runners and inspect pair flow from the terminal.",
)
preset_app = typer.Typer(help="Save and reuse named scan filter presets.")
task_app = typer.Typer(help="Manage repeatable scan tasks.")
state_app = typer.Typer(help="Import/export local presets, tasks, and run history.")
app.add_typer(preset_app, name="preset")
app.add_typer(task_app, name="task")
app.add_typer(state_app, name="state")
console = Console()


def _status_badge(status: str) -> Text:
    style_map = {
        "todo": "bold yellow",
        "running": "bold bright_cyan",
        "done": "bold bright_green",
        "blocked": "bold bright_red",
        "ok": "bold bright_green",
        "error": "bold bright_red",
    }
    return Text(status, style=style_map.get(status, "white"))


def _alert_badge(enabled: bool) -> Text:
    return Text("yes", style="bold bright_green") if enabled else Text("no", style="dim")


def _parse_chains(raw: str) -> tuple[str, ...]:
    values = tuple(c.strip().lower() for c in raw.split(",") if c.strip())
    return values or DEFAULT_CHAINS


def _candidate_json(c: HotTokenCandidate) -> dict[str, object]:
    p = c.pair
    return {
        "chainId": p.chain_id,
        "tokenAddress": p.base_address,
        "tokenSymbol": p.base_symbol,
        "tokenName": p.base_name,
        "dexId": p.dex_id,
        "pairAddress": p.pair_address,
        "pairUrl": p.pair_url,
        "priceUsd": p.price_usd,
        "priceChangeH1": p.price_change_h1,
        "priceChangeH24": p.price_change_h24,
        "volumeH24": p.volume_h24,
        "txnsH1": p.txns_h1,
        "liquidityUsd": p.liquidity_usd,
        "marketCap": p.market_cap,
        "fdv": p.fdv,
        "boostTotal": c.boost_total,
        "boostCount": c.boost_count,
        "hasProfile": c.has_profile,
        "score": c.score,
        "tags": c.tags,
    }


def _resolved_filters(
    *,
    chains: str | None,
    limit: int | None,
    min_liquidity_usd: float | None,
    min_volume_h24_usd: float | None,
    min_txns_h1: int | None,
    min_price_change_h1: float | None,
    preset_name: str | None,
) -> ScanFilters:
    default_filters = ScanFilters(chains=DEFAULT_CHAINS)
    resolved = default_filters

    if preset_name:
        store = StateStore()
        preset = store.get_preset(preset_name)
        if not preset:
            console.print(f"[red]Preset '{preset_name}' not found.[/red]")
            raise typer.Exit(code=1)
        resolved = preset.to_filters()

    if chains:
        resolved.chains = _parse_chains(chains)
    if limit is not None:
        resolved.limit = limit
    if min_liquidity_usd is not None:
        resolved.min_liquidity_usd = min_liquidity_usd
    if min_volume_h24_usd is not None:
        resolved.min_volume_h24_usd = min_volume_h24_usd
    if min_txns_h1 is not None:
        resolved.min_txns_h1 = min_txns_h1
    if min_price_change_h1 is not None:
        resolved.min_price_change_h1 = min_price_change_h1
    return resolved


def _task_filters(task_name_or_id: str) -> tuple[ScanFilters, str]:
    store = StateStore()
    task = store.get_task(task_name_or_id)
    if not task:
        console.print(f"[red]Task '{task_name_or_id}' not found.[/red]")
        raise typer.Exit(code=1)

    return _filters_for_task(task, store), task.id


def _filters_for_task(task: ScanTask, store: StateStore) -> ScanFilters:
    return runner_task_filters(task, store)


def _build_task_overrides(
    *,
    chains: str | None,
    limit: int | None,
    min_liquidity_usd: float | None,
    min_volume_h24_usd: float | None,
    min_txns_h1: int | None,
    min_price_change_h1: float | None,
    from_existing: dict[str, object] | None = None,
) -> dict[str, object] | None:
    overrides: dict[str, object] = dict(from_existing or {})
    if chains is not None:
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
    return overrides or None


def _build_alert_config(
    *,
    webhook_url: str | None,
    discord_webhook_url: str | None,
    telegram_bot_token: str | None,
    telegram_chat_id: str | None,
    alert_min_score: float | None,
    alert_cooldown_seconds: int | None,
    alert_template: str | None = None,
    alert_top_n: int | None = None,
    alert_min_liquidity_usd: float | None = None,
    alert_max_vol_liq_ratio: float | None = None,
    alert_blocked_terms: str | None = None,
    alert_blocked_chains: str | None = None,
    webhook_extra_json: str | None = None,
    from_existing: dict[str, object] | None = None,
) -> dict[str, object] | None:
    alerts: dict[str, object] = dict(from_existing or {})
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
    if alert_blocked_terms is not None:
        alerts["blocked_terms"] = [t.strip() for t in alert_blocked_terms.split(",") if t.strip()]
    if alert_blocked_chains is not None:
        alerts["blocked_chains"] = [c.strip().lower() for c in alert_blocked_chains.split(",") if c.strip()]
    if webhook_extra_json is not None:
        parsed = json.loads(webhook_extra_json) if webhook_extra_json.strip() else {}
        if not isinstance(parsed, dict):
            raise ValueError("--webhook-extra-json must be a JSON object")
        alerts["webhook_extra"] = parsed
    return alerts or None


async def _scan(filters: ScanFilters) -> list[HotTokenCandidate]:
    async with DexScreenerClient() as client:
        scanner = HotScanner(client)
        return await scanner.scan(filters)


def _render_scan_board(candidates: list[HotTokenCandidate], filters: ScanFilters) -> None:
    console.print(build_header())
    console.print(
        render_hot_table(
            candidates,
            chains=filters.chains,
            limit=filters.limit,
            min_liquidity_usd=filters.min_liquidity_usd,
            min_volume_h24_usd=filters.min_volume_h24_usd,
            min_txns_h1=filters.min_txns_h1,
        )
    )
    console.print(Columns([render_chain_heat_table(candidates), render_flow_panel(candidates)]))


def _new_runner_rank(candidate: HotTokenCandidate) -> tuple[float, float, int, float]:
    age = candidate.pair.age_hours
    freshness_bonus = 0.0 if age is None else max(0.0, (24.0 - age) / 24.0) * 8.0
    return (
        candidate.score + freshness_bonus,
        candidate.pair.volume_h1,
        candidate.pair.txns_h1,
        candidate.pair.price_change_h1,
    )


def _select_new_runners(
    *,
    candidates: list[HotTokenCandidate],
    max_age_hours: float,
    include_unknown_age: bool,
    limit: int,
) -> list[HotTokenCandidate]:
    fresh: list[HotTokenCandidate] = []
    for candidate in candidates:
        age = candidate.pair.age_hours
        if age is None and not include_unknown_age:
            continue
        if age is not None and age > max_age_hours:
            continue
        fresh.append(candidate)
    return sorted(fresh, key=_new_runner_rank, reverse=True)[:limit]


@app.command("hot")
def hot(
    chains: Annotated[str | None, typer.Option(help="Comma-separated chain IDs")] = None,
    limit: Annotated[int | None, typer.Option(help="Number of rows")] = None,
    min_liquidity_usd: Annotated[float | None, typer.Option(help="Minimum pair liquidity in USD")] = None,
    min_volume_h24_usd: Annotated[float | None, typer.Option(help="Minimum 24h volume in USD")] = None,
    min_txns_h1: Annotated[int | None, typer.Option(help="Minimum 1h transactions")] = None,
    min_price_change_h1: Annotated[float | None, typer.Option(help="Minimum 1h price change percent")] = None,
    preset: Annotated[str | None, typer.Option(help="Named preset to load before overrides")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Output machine-readable JSON")] = False,
) -> None:
    """One-shot hot runner scan."""
    filters = _resolved_filters(
        chains=chains,
        limit=limit,
        min_liquidity_usd=min_liquidity_usd,
        min_volume_h24_usd=min_volume_h24_usd,
        min_txns_h1=min_txns_h1,
        min_price_change_h1=min_price_change_h1,
        preset_name=preset,
    )
    candidates = asyncio.run(_scan(filters))
    if as_json:
        typer.echo(json.dumps([_candidate_json(c) for c in candidates], indent=2, ensure_ascii=True))
        return
    _render_scan_board(candidates, filters)


@app.command("new-runners")
def new_runners(
    chain: Annotated[str, typer.Option(help="Chain ID, defaults to base")] = "base",
    limit: Annotated[int, typer.Option(help="Number of fresh runners to show")] = 10,
    max_age_hours: Annotated[float, typer.Option(help="Maximum token age in hours")] = 24.0,
    min_liquidity_usd: Annotated[float, typer.Option(help="Minimum pair liquidity in USD")] = 20_000.0,
    min_volume_h24_usd: Annotated[float, typer.Option(help="Minimum 24h volume in USD")] = 50_000.0,
    min_txns_h1: Annotated[int, typer.Option(help="Minimum 1h transactions")] = 25,
    min_price_change_h1: Annotated[float, typer.Option(help="Minimum 1h price change percent")] = 0.0,
    include_unknown_age: Annotated[bool, typer.Option(help="Include tokens with unknown pair age")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Output machine-readable JSON")] = False,
) -> None:
    """Show best new runners for a chain (optimized for day-trading discovery)."""
    chain = chain.lower().strip()
    fetch_limit = min(max(limit * 6, 60), 72)
    filters = ScanFilters(
        chains=(chain,),
        limit=fetch_limit,
        min_liquidity_usd=min_liquidity_usd,
        min_volume_h24_usd=min_volume_h24_usd,
        min_txns_h1=min_txns_h1,
        min_price_change_h1=min_price_change_h1,
    )

    candidates = asyncio.run(_scan(filters))
    ranked = _select_new_runners(
        candidates=candidates,
        max_age_hours=max_age_hours,
        include_unknown_age=include_unknown_age,
        limit=limit,
    )
    if as_json:
        typer.echo(json.dumps([_candidate_json(c) for c in ranked], indent=2, ensure_ascii=True))
        return

    console.print(build_header())
    console.print(
        Columns(
            [
                render_new_runner_spotlight(ranked, chain=chain, max_age_hours=max_age_hours, limit=limit),
                render_flow_panel(ranked),
            ]
        )
    )
    console.print(
        render_new_runners_table(
            ranked,
            chain=chain,
            max_age_hours=max_age_hours,
            limit=limit,
        )
    )
    if len(ranked) < limit:
        console.print(
            f"[yellow]Only found {len(ranked)} new runners under {max_age_hours:.0f}h. "
            "Try lowering min liquidity/volume/txns filters.[/yellow]"
        )


@app.command("new-runners-watch")
def new_runners_watch(
    chain: Annotated[str, typer.Option(help="Chain ID, defaults to base")] = "base",
    limit: Annotated[int, typer.Option(help="Number of fresh runners to show")] = 10,
    max_age_hours: Annotated[float, typer.Option(help="Maximum token age in hours")] = 24.0,
    interval: Annotated[float, typer.Option(help="Refresh interval seconds")] = 7.0,
    min_liquidity_usd: Annotated[float, typer.Option(help="Minimum pair liquidity in USD")] = 20_000.0,
    min_volume_h24_usd: Annotated[float, typer.Option(help="Minimum 24h volume in USD")] = 50_000.0,
    min_txns_h1: Annotated[int, typer.Option(help="Minimum 1h transactions")] = 25,
    min_price_change_h1: Annotated[float, typer.Option(help="Minimum 1h price change percent")] = 0.0,
    include_unknown_age: Annotated[bool, typer.Option(help="Include tokens with unknown pair age")] = False,
    cycles: Annotated[int, typer.Option(help="Stop after N refreshes (0 = infinite)")] = 0,
    screen: Annotated[bool, typer.Option(help="Use fullscreen alternate buffer")] = True,
) -> None:
    """Full-screen live board for tracking new runner rotations."""
    chain = chain.lower().strip()
    fetch_limit = min(max(limit * 6, 60), 72)
    filters = ScanFilters(
        chains=(chain,),
        limit=fetch_limit,
        min_liquidity_usd=min_liquidity_usd,
        min_volume_h24_usd=min_volume_h24_usd,
        min_txns_h1=min_txns_h1,
        min_price_change_h1=min_price_change_h1,
    )

    async def loop() -> None:
        async with DexScreenerClient() as client:
            scanner = HotScanner(client)
            previous_ranks: dict[tuple[str, str], int] = {}
            cycle = 0
            with Live(console=console, screen=screen, refresh_per_second=6) as live:
                while True:
                    cycle += 1
                    raw = await scanner.scan(filters)
                    ranked = _select_new_runners(
                        candidates=raw,
                        max_age_hours=max_age_hours,
                        include_unknown_age=include_unknown_age,
                        limit=limit,
                    )
                    view = Group(
                        build_header(),
                        Columns(
                            [
                                render_new_runner_spotlight(
                                    ranked,
                                    chain=chain,
                                    max_age_hours=max_age_hours,
                                    limit=limit,
                                ),
                                render_flow_panel(ranked),
                            ]
                        ),
                        render_top_runner_cards(ranked, pulse=(cycle % 2 == 0)),
                        render_new_runners_table(
                            ranked,
                            chain=chain,
                            max_age_hours=max_age_hours,
                            limit=limit,
                        ),
                        render_rank_movers_table(
                            ranked,
                            previous_ranks=previous_ranks,
                            limit=limit,
                        ),
                        Panel(
                            f"Refreshing every {interval:.1f}s | cycle={cycle} | Press Ctrl+C to exit",
                            border_style="dim",
                            box=box.ROUNDED,
                        ),
                    )
                    live.update(view)
                    previous_ranks = {candidate.key: idx for idx, candidate in enumerate(ranked, start=1)}
                    if cycles > 0 and cycle >= cycles:
                        return
                    await asyncio.sleep(interval)

    try:
        asyncio.run(loop())
    except KeyboardInterrupt:
        console.print("[dim]Stopped new-runners watch mode.[/dim]")


@app.command("watch")
def watch(
    chains: Annotated[str | None, typer.Option(help="Comma-separated chain IDs")] = None,
    limit: Annotated[int | None, typer.Option(help="Number of rows")] = None,
    interval: Annotated[float, typer.Option(help="Refresh interval seconds")] = 7.0,
    min_liquidity_usd: Annotated[float | None, typer.Option(help="Minimum pair liquidity in USD")] = None,
    min_volume_h24_usd: Annotated[float | None, typer.Option(help="Minimum 24h volume in USD")] = None,
    min_txns_h1: Annotated[int | None, typer.Option(help="Minimum 1h transactions")] = None,
    min_price_change_h1: Annotated[float | None, typer.Option(help="Minimum 1h price change percent")] = None,
    preset: Annotated[str | None, typer.Option(help="Named preset to load before overrides")] = None,
) -> None:
    """Live visual hot runner board for terminal workflows."""
    filters = _resolved_filters(
        chains=chains,
        limit=limit if limit is not None else 16,
        min_liquidity_usd=min_liquidity_usd,
        min_volume_h24_usd=min_volume_h24_usd,
        min_txns_h1=min_txns_h1,
        min_price_change_h1=min_price_change_h1,
        preset_name=preset,
    )

    async def loop() -> None:
        async with DexScreenerClient() as client:
            scanner = HotScanner(client)
            with Live(console=console, screen=True, refresh_per_second=6) as live:
                while True:
                    candidates = await scanner.scan(filters)
                    view = Group(
                        build_header(),
                        render_hot_table(
                            candidates,
                            chains=filters.chains,
                            limit=filters.limit,
                            min_liquidity_usd=filters.min_liquidity_usd,
                            min_volume_h24_usd=filters.min_volume_h24_usd,
                            min_txns_h1=filters.min_txns_h1,
                        ),
                        Columns([render_chain_heat_table(candidates), render_flow_panel(candidates)]),
                        Panel(
                            f"Refreshing every {interval:.1f}s. Press Ctrl+C to exit.",
                            border_style="dim",
                        ),
                    )
                    live.update(view)
                    await asyncio.sleep(interval)

    try:
        asyncio.run(loop())
    except KeyboardInterrupt:
        console.print("[dim]Stopped watch mode.[/dim]")


@app.command("inspect")
def inspect(
    address: Annotated[str, typer.Argument(help="Token address or pair address")],
    chain: Annotated[str, typer.Option("--chain", "-c", help="Chain ID, e.g. solana/base/ethereum")] = "solana",
    pair: Annotated[bool, typer.Option("--pair", help="Treat address as pair address")] = False,
) -> None:
    """Inspect a token or specific pair with concentration proxies."""

    async def run_inspect() -> None:
        async with DexScreenerClient() as client:
            scanner = HotScanner(client)
            if pair:
                p = await scanner.inspect_pair(chain, address)
                if not p:
                    console.print("[red]Pair not found.[/red]")
                    raise typer.Exit(code=1)
                console.print(build_header())
                console.print(render_pair_detail(p))
                return

            pairs = await scanner.inspect_token(chain, address)
            if not pairs:
                console.print("[red]Token not found or no pairs available.[/red]")
                raise typer.Exit(code=1)

            primary = pairs[0]
            orders = await client.get_orders(chain, address)
            boosts = orders.get("boosts", [])
            boost_total = float(sum(float(b.get("amount", 0) or 0) for b in boosts))
            candidate = HotTokenCandidate(
                pair=primary,
                score=0.0,
                boost_total=boost_total,
                boost_count=len(boosts),
                has_profile=any(o.get("type") == "tokenProfile" for o in orders.get("orders", [])),
                discovery="inspect",
                tags=[],
            )

            console.print(build_header())
            console.print(render_pair_detail(primary, boost_total=boost_total, boost_count=len(boosts)))
            console.print(render_distribution_panel(candidate))
            if len(pairs) > 1:
                console.print(f"[dim]Additional pairs found: {len(pairs) - 1}[/dim]")

    asyncio.run(run_inspect())


@app.command("search")
def search(
    query: Annotated[str, typer.Argument(help="Search query (symbol, token, pair) ")],
    limit: Annotated[int, typer.Option(help="Max result rows")] = 20,
) -> None:
    """Search across Dexscreener pairs."""

    async def run_search() -> None:
        async with DexScreenerClient() as client:
            scanner = HotScanner(client)
            pairs = await scanner.search(query=query, limit=limit)
            console.print(build_header())
            console.print(render_search_table(pairs))

    asyncio.run(run_search())


@app.command("god-prompt")
def god_prompt() -> None:
    """Print the God prompt for extending this tool."""
    path = Path(__file__).resolve().parents[1] / "GOD_PROMPT.md"
    if not path.exists():
        console.print("[red]GOD_PROMPT.md not found.[/red]")
        raise typer.Exit(code=1)
    console.print(path.read_text(encoding="utf-8"))


@app.command("why")
def why() -> None:
    """Explain why Dexscreener is used and what this CLI optimizes."""
    payload = {
        "top_use_cases": [
            "Fast discovery of active pools and cross-chain momentum.",
            "Liquidity/volume/transaction context for early signal validation.",
            "Trend-aware ranking and boost/profile visibility.",
        ],
        "dexscreener_api_constraints": {
            "60_rpm": [
                "/token-profiles/latest/v1",
                "/token-boosts/latest/v1",
                "/token-boosts/top/v1",
                "/orders/v1/{chainId}/{tokenAddress}",
            ],
            "300_rpm": [
                "/latest/dex/search",
                "/latest/dex/pairs/{chainId}/{pairId}",
                "/token-pairs/v1/{chainId}/{tokenAddress}",
            ],
            "holder_distribution": "Not exposed by public Dexscreener API endpoints.",
        },
    }
    console.print(json.dumps(payload, indent=2))


@preset_app.command("save")
def preset_save(
    name: Annotated[str, typer.Argument(help="Preset name")],
    chains: Annotated[str | None, typer.Option(help="Comma-separated chain IDs")] = None,
    limit: Annotated[int | None, typer.Option(help="Number of rows")] = None,
    min_liquidity_usd: Annotated[float | None, typer.Option(help="Minimum pair liquidity in USD")] = None,
    min_volume_h24_usd: Annotated[float | None, typer.Option(help="Minimum 24h volume in USD")] = None,
    min_txns_h1: Annotated[int | None, typer.Option(help="Minimum 1h transactions")] = None,
    min_price_change_h1: Annotated[float | None, typer.Option(help="Minimum 1h price change percent")] = None,
    from_preset: Annotated[str | None, typer.Option(help="Use this preset as a base")] = None,
) -> None:
    """Save a named preset from filters."""
    filters = _resolved_filters(
        chains=chains,
        limit=limit,
        min_liquidity_usd=min_liquidity_usd,
        min_volume_h24_usd=min_volume_h24_usd,
        min_txns_h1=min_txns_h1,
        min_price_change_h1=min_price_change_h1,
        preset_name=from_preset,
    )
    store = StateStore()
    preset = ScanPreset.from_filters(name=name, filters=filters)
    store.save_preset(preset)
    console.print(f"[green]Saved preset '{name}'.[/green]")


@preset_app.command("list")
def preset_list() -> None:
    """List saved presets."""
    store = StateStore()
    presets = store.list_presets()
    if not presets:
        console.print("[yellow]No presets found.[/yellow]")
        return
    table = Table(
        title="[bold bright_white]Presets[/bold bright_white]",
        box=box.ROUNDED,
        header_style="bold bright_white",
        row_styles=["none", "dim"],
    )
    table.add_column("Name", style="bold cyan")
    table.add_column("Chains")
    table.add_column("Limit", justify="right")
    table.add_column("MinLiq", justify="right")
    table.add_column("MinVol24", justify="right")
    table.add_column("MinTx1h", justify="right")
    table.add_column("Updated", style="dim")
    for p in presets:
        table.add_row(
            p.name,
            ",".join(p.chains),
            str(p.limit),
            f"{p.min_liquidity_usd:.0f}",
            f"{p.min_volume_h24_usd:.0f}",
            str(p.min_txns_h1),
            p.updated_at,
        )
    console.print(table)


@preset_app.command("show")
def preset_show(name: Annotated[str, typer.Argument(help="Preset name")]) -> None:
    """Show a preset as JSON."""
    store = StateStore()
    preset = store.get_preset(name)
    if not preset:
        console.print(f"[red]Preset '{name}' not found.[/red]")
        raise typer.Exit(code=1)
    typer.echo(json.dumps(preset.to_dict(), indent=2, ensure_ascii=True))


@preset_app.command("delete")
def preset_delete(name: Annotated[str, typer.Argument(help="Preset name")]) -> None:
    """Delete a preset."""
    store = StateStore()
    deleted = store.delete_preset(name)
    if not deleted:
        console.print(f"[red]Preset '{name}' not found.[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]Deleted preset '{name}'.[/green]")


@task_app.command("create")
def task_create(
    name: Annotated[str, typer.Argument(help="Task name")],
    preset: Annotated[str | None, typer.Option(help="Preset name to base this task on")] = None,
    chains: Annotated[str | None, typer.Option(help="Inline chain override")] = None,
    limit: Annotated[int | None, typer.Option(help="Inline limit override")] = None,
    min_liquidity_usd: Annotated[float | None, typer.Option(help="Inline min liquidity override")] = None,
    min_volume_h24_usd: Annotated[float | None, typer.Option(help="Inline min volume override")] = None,
    min_txns_h1: Annotated[int | None, typer.Option(help="Inline min txns override")] = None,
    min_price_change_h1: Annotated[float | None, typer.Option(help="Inline min 1h % override")] = None,
    interval_seconds: Annotated[int | None, typer.Option(help="Run interval seconds for daemon mode")] = None,
    webhook_url: Annotated[str | None, typer.Option(help="Generic JSON webhook URL")] = None,
    discord_webhook_url: Annotated[str | None, typer.Option(help="Discord webhook URL")] = None,
    telegram_bot_token: Annotated[str | None, typer.Option(help="Telegram bot token")] = None,
    telegram_chat_id: Annotated[str | None, typer.Option(help="Telegram chat id")] = None,
    alert_min_score: Annotated[float | None, typer.Option(help="Alert threshold on top score")] = None,
    alert_cooldown_seconds: Annotated[int | None, typer.Option(help="Alert cooldown seconds")] = None,
    alert_template: Annotated[str | None, typer.Option(help="Alert text template")] = None,
    alert_top_n: Annotated[int | None, typer.Option(help="How many top candidates in message")] = None,
    alert_min_liquidity_usd: Annotated[float | None, typer.Option(help="Alert gate: minimum liquidity")] = None,
    alert_max_vol_liq_ratio: Annotated[float | None, typer.Option(help="Alert gate: maximum volume/liquidity ratio")] = None,
    alert_blocked_terms: Annotated[str | None, typer.Option(help="Alert gate: blocked token terms (comma-separated)")] = None,
    alert_blocked_chains: Annotated[str | None, typer.Option(help="Alert gate: blocked chains (comma-separated)")] = None,
    webhook_extra_json: Annotated[str | None, typer.Option(help="Extra webhook JSON object")] = None,
    notes: Annotated[str, typer.Option(help="Task notes")] = "",
) -> None:
    """Create a new scan task."""
    store = StateStore()
    if preset and not store.get_preset(preset):
        console.print(f"[red]Preset '{preset}' not found.[/red]")
        raise typer.Exit(code=1)

    overrides = _build_task_overrides(
        chains=chains,
        limit=limit,
        min_liquidity_usd=min_liquidity_usd,
        min_volume_h24_usd=min_volume_h24_usd,
        min_txns_h1=min_txns_h1,
        min_price_change_h1=min_price_change_h1,
    )
    try:
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
            webhook_extra_json=webhook_extra_json,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    try:
        task = store.create_task(
            name=name,
            preset=preset,
            filters=overrides,
            interval_seconds=interval_seconds,
            alerts=alerts,
            notes=notes,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]Created task '{task.name}' ({task.id}).[/green]")


@task_app.command("list")
def task_list(
    status: Annotated[str | None, typer.Option(help="Filter by status: todo/running/done/blocked")] = None,
) -> None:
    """List tasks."""
    store = StateStore()
    if status and status not in {"todo", "running", "done", "blocked"}:
        console.print("[red]Invalid status. Use todo/running/done/blocked.[/red]")
        raise typer.Exit(code=1)
    tasks = store.list_tasks(status=status)  # type: ignore[arg-type]
    if not tasks:
        console.print("[yellow]No tasks found.[/yellow]")
        return
    table = Table(
        title="[bold bright_white]Scan Tasks[/bold bright_white]",
        box=box.ROUNDED,
        header_style="bold bright_white",
        row_styles=["none", "dim"],
    )
    table.add_column("ID", style="bold cyan")
    table.add_column("Name")
    table.add_column("Status")
    table.add_column("Preset")
    table.add_column("Interval", justify="right")
    table.add_column("Alerts")
    table.add_column("Last Run")
    table.add_column("Last Alert")
    table.add_column("Updated", style="dim")
    for task in tasks:
        table.add_row(
            task.id,
            task.name,
            _status_badge(task.status),
            task.preset or "-",
            str(task.interval_seconds) if task.interval_seconds else "-",
            _alert_badge(bool(task.alerts)),
            Text(task.last_run_at or "-", style="dim"),
            Text(task.last_alert_at or "-", style="dim"),
            Text(task.updated_at, style="dim"),
        )
    console.print(table)


@task_app.command("show")
def task_show(task: Annotated[str, typer.Argument(help="Task id or name")]) -> None:
    """Show task JSON."""
    store = StateStore()
    row = store.get_task(task)
    if not row:
        console.print(f"[red]Task '{task}' not found.[/red]")
        raise typer.Exit(code=1)
    typer.echo(json.dumps(row.to_dict(), indent=2, ensure_ascii=True))


@task_app.command("status")
def task_status(
    task: Annotated[str, typer.Argument(help="Task id or name")],
    status: Annotated[str, typer.Argument(help="todo/running/done/blocked")],
) -> None:
    """Update task status."""
    if status not in {"todo", "running", "done", "blocked"}:
        console.print("[red]Invalid status. Use todo/running/done/blocked.[/red]")
        raise typer.Exit(code=1)
    store = StateStore()
    try:
        row = store.update_task_status(task, status=status)  # type: ignore[arg-type]
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]Task '{row.name}' status -> {row.status}.[/green]")


@task_app.command("delete")
def task_delete(task: Annotated[str, typer.Argument(help="Task id or name")]) -> None:
    """Delete a task."""
    store = StateStore()
    deleted = store.delete_task(task)
    if not deleted:
        console.print(f"[red]Task '{task}' not found.[/red]")
        raise typer.Exit(code=1)
    console.print(f"[green]Deleted task '{task}'.[/green]")


@task_app.command("configure")
def task_configure(
    task: Annotated[str, typer.Argument(help="Task id or name")],
    preset: Annotated[str | None, typer.Option(help="Set preset name")] = None,
    clear_preset: Annotated[bool, typer.Option(help="Remove preset from task")] = False,
    chains: Annotated[str | None, typer.Option(help="Inline chain override")] = None,
    limit: Annotated[int | None, typer.Option(help="Inline limit override")] = None,
    min_liquidity_usd: Annotated[float | None, typer.Option(help="Inline min liquidity override")] = None,
    min_volume_h24_usd: Annotated[float | None, typer.Option(help="Inline min volume override")] = None,
    min_txns_h1: Annotated[int | None, typer.Option(help="Inline min txns override")] = None,
    min_price_change_h1: Annotated[float | None, typer.Option(help="Inline min 1h %% override")] = None,
    clear_overrides: Annotated[bool, typer.Option(help="Clear inline filter overrides")] = False,
    interval_seconds: Annotated[int | None, typer.Option(help="Run interval seconds for daemon mode")] = None,
    clear_interval: Annotated[bool, typer.Option(help="Clear daemon interval")] = False,
    webhook_url: Annotated[str | None, typer.Option(help="Generic JSON webhook URL")] = None,
    discord_webhook_url: Annotated[str | None, typer.Option(help="Discord webhook URL")] = None,
    telegram_bot_token: Annotated[str | None, typer.Option(help="Telegram bot token")] = None,
    telegram_chat_id: Annotated[str | None, typer.Option(help="Telegram chat id")] = None,
    alert_min_score: Annotated[float | None, typer.Option(help="Alert threshold on top score")] = None,
    alert_cooldown_seconds: Annotated[int | None, typer.Option(help="Alert cooldown seconds")] = None,
    alert_template: Annotated[str | None, typer.Option(help="Alert text template")] = None,
    alert_top_n: Annotated[int | None, typer.Option(help="How many top candidates in message")] = None,
    alert_min_liquidity_usd: Annotated[float | None, typer.Option(help="Alert gate: minimum liquidity")] = None,
    alert_max_vol_liq_ratio: Annotated[float | None, typer.Option(help="Alert gate: maximum volume/liquidity ratio")] = None,
    alert_blocked_terms: Annotated[str | None, typer.Option(help="Alert gate: blocked token terms (comma-separated)")] = None,
    alert_blocked_chains: Annotated[str | None, typer.Option(help="Alert gate: blocked chains (comma-separated)")] = None,
    webhook_extra_json: Annotated[str | None, typer.Option(help="Extra webhook JSON object")] = None,
    clear_alerts: Annotated[bool, typer.Option(help="Remove all alerts from task")] = False,
    notes: Annotated[str | None, typer.Option(help="Replace task notes")] = None,
) -> None:
    """Configure a task's schedule, overrides, and alert channels."""
    store = StateStore()
    current = store.get_task(task)
    if not current:
        console.print(f"[red]Task '{task}' not found.[/red]")
        raise typer.Exit(code=1)
    if preset and not store.get_preset(preset):
        console.print(f"[red]Preset '{preset}' not found.[/red]")
        raise typer.Exit(code=1)

    next_preset = None if clear_preset else (preset if preset is not None else current.preset)
    current_overrides = None if clear_overrides else current.filters
    next_overrides = _build_task_overrides(
        chains=chains,
        limit=limit,
        min_liquidity_usd=min_liquidity_usd,
        min_volume_h24_usd=min_volume_h24_usd,
        min_txns_h1=min_txns_h1,
        min_price_change_h1=min_price_change_h1,
        from_existing=current_overrides,
    )
    next_interval = None if clear_interval else (interval_seconds if interval_seconds is not None else current.interval_seconds)
    current_alerts = None if clear_alerts else current.alerts
    try:
        next_alerts = _build_alert_config(
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
            webhook_extra_json=webhook_extra_json,
            from_existing=current_alerts,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    try:
        updated = store.update_task(
            current.id,
            preset=next_preset,
            filters=next_overrides,
            interval_seconds=next_interval,
            alerts=next_alerts,
            notes=notes if notes is not None else current.notes,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc
    console.print(f"[green]Updated task '{updated.name}'.[/green]")


@task_app.command("run")
def task_run(
    task: Annotated[str, typer.Argument(help="Task id or name")],
    no_alerts: Annotated[bool, typer.Option(help="Skip alert delivery for this run")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Output machine-readable JSON")] = False,
) -> None:
    """Execute a task scan now."""
    store = StateStore()
    row = store.get_task(task)
    if not row:
        console.print(f"[red]Task '{task}' not found.[/red]")
        raise typer.Exit(code=1)

    async def _run_once() -> dict[str, object]:
        async with DexScreenerClient() as client:
            scanner = HotScanner(client)
            return await execute_task_once(
                store=store,
                scanner=scanner,
                task=row,
                mode="manual",
                fire_alerts=not no_alerts,
                mark_running=False,
                block_on_error=False,
            )

    result = asyncio.run(_run_once())
    candidates = result.get("candidates", [])
    if not isinstance(candidates, list):
        candidates = []
    filters_data = result.get("filters") or {}
    filters = _filters_for_task(row, store)
    if isinstance(filters_data, dict) and filters_data:
        filters = ScanFilters(
            chains=tuple(filters_data.get("chains", list(filters.chains))),
            limit=int(filters_data.get("limit", filters.limit)),
            min_liquidity_usd=float(filters_data.get("min_liquidity_usd", filters.min_liquidity_usd)),
            min_volume_h24_usd=float(filters_data.get("min_volume_h24_usd", filters.min_volume_h24_usd)),
            min_txns_h1=int(filters_data.get("min_txns_h1", filters.min_txns_h1)),
            min_price_change_h1=float(filters_data.get("min_price_change_h1", filters.min_price_change_h1)),
        )
    alert_result = result.get("alert", {"sent": False, "reason": "unknown", "channels": {}})

    if as_json:
        typer.echo(
            json.dumps(
                {
                    "task": result.get("task", row.to_dict()),
                    "results": [_candidate_json(c) for c in candidates if isinstance(c, HotTokenCandidate)],
                    "alert": alert_result,
                    "run": result.get("run"),
                    "ok": result.get("ok", False),
                    "error": result.get("error"),
                },
                indent=2,
                ensure_ascii=True,
            )
        )
        return
    if not result.get("ok", False):
        console.print(f"[red]Task run failed: {result.get('error')}[/red]")
        raise typer.Exit(code=1)
    _render_scan_board([c for c in candidates if isinstance(c, HotTokenCandidate)], filters)
    if alert_result.get("sent"):
        console.print(f"[green]Alerts sent: {json.dumps(alert_result['channels'])}[/green]")
    else:
        console.print(f"[dim]Alerts: {alert_result.get('reason')}[/dim]")


@task_app.command("daemon")
def task_daemon(
    task: Annotated[str | None, typer.Option(help="Run only this task id/name")] = None,
    all_tasks: Annotated[bool, typer.Option("--all", help="Run all non-blocked tasks")] = False,
    poll_seconds: Annotated[float, typer.Option(help="Scheduler polling interval seconds")] = 5.0,
    default_interval_seconds: Annotated[int, typer.Option(help="Default interval for tasks without one")] = 120,
    once: Annotated[bool, typer.Option(help="Run one due cycle and exit")] = False,
    no_alerts: Annotated[bool, typer.Option(help="Disable alert delivery in daemon runs")] = False,
) -> None:
    """Continuously execute due scan tasks on a schedule."""
    if not task and not all_tasks:
        console.print("[red]Provide --task <id|name> or --all.[/red]")
        raise typer.Exit(code=1)

    async def loop() -> None:
        async with DexScreenerClient() as client:
            scanner = HotScanner(client)
            cycle = 0
            while True:
                cycle += 1
                store = StateStore()
                due_rows = select_due_tasks(
                    store=store,
                    task_name_or_id=task,
                    all_tasks=all_tasks,
                    default_interval_seconds=default_interval_seconds,
                )

                if not due_rows:
                    console.print(f"[dim]Cycle {cycle}: no due tasks.[/dim]")
                for row in due_rows:
                    result = await execute_task_once(
                        store=store,
                        scanner=scanner,
                        task=row,
                        mode="daemon",
                        fire_alerts=not no_alerts,
                        mark_running=True,
                        block_on_error=True,
                    )
                    if result.get("ok"):
                        candidates = result.get("candidates", [])
                        top = "none"
                        if isinstance(candidates, list) and candidates and isinstance(candidates[0], HotTokenCandidate):
                            top = candidates[0].pair.base_symbol
                        alert_reason = "unknown"
                        alert = result.get("alert")
                        if isinstance(alert, dict):
                            alert_reason = str(alert.get("reason", "unknown"))
                        console.print(
                            f"[cyan]task={row.name}[/cyan] results={len(candidates) if isinstance(candidates, list) else 0} "
                            f"top={top} alerts={alert_reason}"
                        )
                    else:
                        console.print(f"[red]task={row.name} failed and was blocked: {result.get('error')}[/red]")

                if once:
                    return
                await asyncio.sleep(poll_seconds)

    try:
        asyncio.run(loop())
    except KeyboardInterrupt:
        console.print("[dim]Stopped task daemon.[/dim]")


@task_app.command("test-alert")
def task_test_alert(
    task: Annotated[str, typer.Argument(help="Task id or name")],
    with_scan: Annotated[bool, typer.Option(help="Run a fresh scan and include top candidates in alert")] = True,
) -> None:
    """Send a test alert through configured task channels."""
    store = StateStore()
    row = store.get_task(task)
    if not row:
        console.print(f"[red]Task '{task}' not found.[/red]")
        raise typer.Exit(code=1)

    async def _run() -> dict[str, Any]:
        candidates: list[HotTokenCandidate] = []
        if with_scan:
            async with DexScreenerClient() as client:
                scanner = HotScanner(client)
                candidates = await scanner.scan(_filters_for_task(row, store))
        return await send_test_alert(row, candidates=candidates)

    result = asyncio.run(_run())
    if result.get("sent"):
        store.touch_task_alert(row.id)
        console.print(f"[green]Test alert sent: {json.dumps(result.get('channels', {}))}[/green]")
        return
    console.print(f"[yellow]Test alert not sent: {result.get('reason')}[/yellow]")


@task_app.command("runs")
def task_runs(
    task: Annotated[str | None, typer.Option(help="Filter by task id or name")] = None,
    limit: Annotated[int, typer.Option(help="Max run rows")] = 50,
) -> None:
    """List task execution history."""
    store = StateStore()
    rows = store.list_runs(task=task, limit=limit)
    if not rows:
        console.print("[yellow]No run history found.[/yellow]")
        return
    table = Table(
        title="[bold bright_white]Task Run History[/bold bright_white]",
        box=box.ROUNDED,
        header_style="bold bright_white",
        row_styles=["none", "dim"],
    )
    table.add_column("Finished", style="dim")
    table.add_column("Task")
    table.add_column("Mode")
    table.add_column("Status")
    table.add_column("Results", justify="right")
    table.add_column("Top")
    table.add_column("Score", justify="right")
    table.add_column("Alert")
    table.add_column("Ms", justify="right")
    for r in rows:
        table.add_row(
            r.finished_at,
            r.task_name,
            r.mode,
            _status_badge(r.status),
            str(r.result_count),
            f"{r.top_chain}:{r.top_token}" if r.top_token else "-",
            f"{r.top_score:.2f}" if r.top_score is not None else "-",
            Text(r.alert_reason, style="yellow" if r.alert_reason != "sent" else "green"),
            str(r.duration_ms),
        )
    console.print(table)


@state_app.command("export")
def state_export(
    path: Annotated[str, typer.Option(help="Output file path")] = "dexscreener-state-export.json",
) -> None:
    """Export presets/tasks/runs into one JSON file."""
    store = StateStore()
    bundle = store.export_bundle()
    out = Path(path).expanduser().resolve()
    out.write_text(json.dumps(bundle, indent=2, ensure_ascii=True), encoding="utf-8")
    console.print(f"[green]Exported state to {out}[/green]")


@state_app.command("import")
def state_import(
    path: Annotated[str, typer.Option(help="Input file path")] = "dexscreener-state-export.json",
    mode: Annotated[str, typer.Option(help="merge or replace")] = "merge",
) -> None:
    """Import presets/tasks/runs from a JSON export."""
    if mode not in {"merge", "replace"}:
        console.print("[red]Invalid mode. Use merge or replace.[/red]")
        raise typer.Exit(code=1)
    src = Path(path).expanduser().resolve()
    if not src.exists():
        console.print(f"[red]File not found: {src}[/red]")
        raise typer.Exit(code=1)
    try:
        bundle = json.loads(src.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        console.print(f"[red]Invalid JSON: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    store = StateStore()
    counts = store.import_bundle(bundle, mode=mode)  # type: ignore[arg-type]
    console.print(
        f"[green]Imported state ({mode}). presets={counts['presets']} tasks={counts['tasks']} runs={counts['runs']}[/green]"
    )


if __name__ == "__main__":
    app()
