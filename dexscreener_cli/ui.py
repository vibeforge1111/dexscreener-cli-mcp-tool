from __future__ import annotations

from datetime import UTC, datetime

from rich import box
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .models import HotTokenCandidate, PairSnapshot
from .scoring import build_distribution_heuristics


def fmt_usd(value: float) -> str:
    if value >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if value >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"${value / 1_000:.1f}K"
    return f"${value:.2f}"


def fmt_price(value: float) -> str:
    if value >= 1:
        return f"${value:,.4f}"
    if value >= 0.01:
        return f"${value:,.6f}"
    return f"${value:,.8f}"


def fmt_pct(value: float) -> str:
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


def _pct_style(value: float) -> str:
    if value > 0:
        return "green"
    if value < 0:
        return "red"
    return "white"


def _age_label(hours: float | None) -> str:
    if hours is None:
        return "n/a"
    if hours < 1:
        return "<1h"
    if hours < 24:
        return f"{int(hours)}h"
    days = int(hours // 24)
    return f"{days}d"


def build_header() -> Panel:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    title = Text("Dexscreener CLI MCP Tool", style="bold bright_cyan")
    subtitle = Text(f"Live signal terminal  |  {now}", style="dim")
    return Panel(Text.assemble(title, "\n", subtitle), border_style="bright_blue")


def render_hot_table(
    candidates: list[HotTokenCandidate],
    *,
    chains: tuple[str, ...],
    limit: int,
    min_liquidity_usd: float,
    min_volume_h24_usd: float,
    min_txns_h1: int,
) -> Table:
    table = Table(
        title=(
            f"[bold]Hot Runner Scan[/bold]  chains={','.join(chains)}  "
            f"top={limit}  liq>={fmt_usd(min_liquidity_usd)}  "
            f"vol24>={fmt_usd(min_volume_h24_usd)}  tx1h>={min_txns_h1}"
        ),
        box=box.SIMPLE_HEAVY,
        header_style="bold bright_white",
        show_edge=True,
    )
    table.add_column("#", justify="right", style="bold")
    table.add_column("Chain", style="cyan")
    table.add_column("Token", style="bold yellow")
    table.add_column("Price", justify="right")
    table.add_column("1h", justify="right")
    table.add_column("24h Vol", justify="right")
    table.add_column("1h Txns", justify="right")
    table.add_column("Liquidity", justify="right")
    table.add_column("MCap", justify="right")
    table.add_column("Boost", justify="right")
    table.add_column("Age", justify="right")
    table.add_column("Signal", style="magenta")

    for i, candidate in enumerate(candidates, start=1):
        p = candidate.pair
        h1 = Text(fmt_pct(p.price_change_h1), style=_pct_style(p.price_change_h1))
        signal = ", ".join(candidate.tags[:3]) if candidate.tags else candidate.discovery
        boost = f"{candidate.boost_total:.0f}/{candidate.boost_count}"
        token = f"{p.base_symbol} ({candidate.score:.1f})"
        table.add_row(
            str(i),
            p.chain_id,
            token,
            fmt_price(p.price_usd),
            h1,
            fmt_usd(p.volume_h24),
            str(p.txns_h1),
            fmt_usd(p.liquidity_usd),
            fmt_usd(p.market_cap if p.market_cap > 0 else p.fdv),
            boost,
            _age_label(p.age_hours),
            signal,
        )
    return table


def render_search_table(pairs: list[PairSnapshot]) -> Table:
    table = Table(title="[bold]Search Results[/bold]", box=box.SIMPLE_HEAVY)
    table.add_column("Chain", style="cyan")
    table.add_column("Token", style="bold yellow")
    table.add_column("Pair", style="white")
    table.add_column("Price", justify="right")
    table.add_column("24h Vol", justify="right")
    table.add_column("1h Txns", justify="right")
    table.add_column("Liquidity", justify="right")
    table.add_column("1h", justify="right")
    for pair in pairs:
        table.add_row(
            pair.chain_id,
            pair.base_symbol,
            pair.pair_address,
            fmt_price(pair.price_usd),
            fmt_usd(pair.volume_h24),
            str(pair.txns_h1),
            fmt_usd(pair.liquidity_usd),
            fmt_pct(pair.price_change_h1),
        )
    return table


def render_pair_detail(pair: PairSnapshot, boost_total: float = 0.0, boost_count: int = 0) -> Panel:
    mcap = pair.market_cap if pair.market_cap > 0 else pair.fdv
    content = Text()
    content.append(f"{pair.base_name} ({pair.base_symbol}) on {pair.chain_id}/{pair.dex_id}\n", style="bold")
    content.append(f"Pair: {pair.pair_address}\n", style="dim")
    content.append(f"Price: {fmt_price(pair.price_usd)} | 1h: {fmt_pct(pair.price_change_h1)} | 24h: {fmt_pct(pair.price_change_h24)}\n")
    content.append(
        f"Volume: 24h {fmt_usd(pair.volume_h24)} | 6h {fmt_usd(pair.volume_h6)} | 1h {fmt_usd(pair.volume_h1)}\n"
    )
    content.append(
        f"Txns: 1h {pair.txns_h1} (B{pair.buys_h1}/S{pair.sells_h1}) | 24h {pair.txns_h24} (B{pair.buys_h24}/S{pair.sells_h24})\n"
    )
    content.append(f"Liquidity: {fmt_usd(pair.liquidity_usd)} | MCap/FDV: {fmt_usd(mcap)}\n")
    if boost_total or boost_count:
        content.append(f"Boosts observed: total={boost_total:.0f}, count={boost_count}\n")
    if pair.pair_url:
        content.append(f"Dexscreener: {pair.pair_url}")
    return Panel(content, title="[bold bright_cyan]Pair Insight[/bold bright_cyan]", border_style="bright_blue")


def render_distribution_panel(candidate: HotTokenCandidate) -> Panel:
    heuristics = build_distribution_heuristics(candidate)
    txt = Text()
    txt.append("Dexscreener API does not expose holder distribution in public endpoints.\n", style="bold yellow")
    txt.append("Proxy concentration signals from market structure:\n")
    txt.append(
        f"- liquidity/market_cap: {heuristics['liquidity_to_market_cap']}\n"
        f"- volume/liquidity (24h): {heuristics['volume_to_liquidity_24h']}\n"
        f"- buy/sell imbalance (1h): {heuristics['buy_sell_imbalance_1h']}\n"
        f"- status: {heuristics['status']}"
    )
    return Panel(txt, title="[bold bright_magenta]Distribution Proxy[/bold bright_magenta]", border_style="magenta")
