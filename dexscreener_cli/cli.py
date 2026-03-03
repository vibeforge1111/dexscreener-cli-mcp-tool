from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel

from .client import DexScreenerClient
from .config import DEFAULT_CHAINS, ScanFilters
from .models import HotTokenCandidate
from .scanner import HotScanner
from .ui import (
    build_header,
    render_distribution_panel,
    render_hot_table,
    render_pair_detail,
    render_search_table,
)

app = typer.Typer(
    add_completion=False,
    help="Visual Dexscreener scanner CLI. Spot hot runners and inspect pair flow from the terminal.",
)
console = Console()


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


async def _scan(filters: ScanFilters) -> list[HotTokenCandidate]:
    async with DexScreenerClient() as client:
        scanner = HotScanner(client)
        return await scanner.scan(filters)


@app.command("hot")
def hot(
    chains: Annotated[str, typer.Option(help="Comma-separated chain IDs")] = ",".join(DEFAULT_CHAINS),
    limit: Annotated[int, typer.Option(help="Number of rows")] = 20,
    min_liquidity_usd: Annotated[float, typer.Option(help="Minimum pair liquidity in USD")] = 35_000.0,
    min_volume_h24_usd: Annotated[float, typer.Option(help="Minimum 24h volume in USD")] = 90_000.0,
    min_txns_h1: Annotated[int, typer.Option(help="Minimum 1h transactions")] = 80,
    min_price_change_h1: Annotated[float, typer.Option(help="Minimum 1h price change percent")] = 0.0,
    as_json: Annotated[bool, typer.Option("--json", help="Output machine-readable JSON")] = False,
) -> None:
    """One-shot hot runner scan."""
    filters = ScanFilters(
        chains=_parse_chains(chains),
        limit=limit,
        min_liquidity_usd=min_liquidity_usd,
        min_volume_h24_usd=min_volume_h24_usd,
        min_txns_h1=min_txns_h1,
        min_price_change_h1=min_price_change_h1,
    )
    candidates = asyncio.run(_scan(filters))
    if as_json:
        typer.echo(json.dumps([_candidate_json(c) for c in candidates], indent=2, ensure_ascii=True))
        return

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


@app.command("watch")
def watch(
    chains: Annotated[str, typer.Option(help="Comma-separated chain IDs")] = ",".join(DEFAULT_CHAINS),
    limit: Annotated[int, typer.Option(help="Number of rows")] = 16,
    interval: Annotated[float, typer.Option(help="Refresh interval seconds")] = 7.0,
    min_liquidity_usd: Annotated[float, typer.Option(help="Minimum pair liquidity in USD")] = 35_000.0,
    min_volume_h24_usd: Annotated[float, typer.Option(help="Minimum 24h volume in USD")] = 90_000.0,
    min_txns_h1: Annotated[int, typer.Option(help="Minimum 1h transactions")] = 80,
    min_price_change_h1: Annotated[float, typer.Option(help="Minimum 1h price change percent")] = 0.0,
) -> None:
    """Live visual hot runner board for terminal workflows."""
    filters = ScanFilters(
        chains=_parse_chains(chains),
        limit=limit,
        min_liquidity_usd=min_liquidity_usd,
        min_volume_h24_usd=min_volume_h24_usd,
        min_txns_h1=min_txns_h1,
        min_price_change_h1=min_price_change_h1,
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


if __name__ == "__main__":
    app()
