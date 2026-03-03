from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
import sys

from rich import box
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .models import HotTokenCandidate, PairSnapshot
from .scoring import build_distribution_heuristics

CHAIN_STYLES = {
    "solana": "bright_green",
    "base": "bright_blue",
    "ethereum": "bright_white",
    "bsc": "bright_yellow",
    "arbitrum": "bright_cyan",
}


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
    if value >= 12:
        return "bold bright_green"
    if value > 0:
        return "green"
    if value <= -12:
        return "bold bright_red"
    if value < 0:
        return "red"
    return "white"


def _safe_text(value: str) -> str:
    encoding = sys.stdout.encoding or "utf-8"
    try:
        value.encode(encoding)
        return value
    except UnicodeEncodeError:
        return value.encode(encoding, errors="replace").decode(encoding, errors="replace")


def _age_label(hours: float | None) -> str:
    if hours is None:
        return "n/a"
    if hours < 1:
        return "<1h"
    if hours < 24:
        return f"{int(hours)}h"
    days = int(hours // 24)
    return f"{days}d"


def _chain_text(chain_id: str) -> Text:
    return Text(_safe_text(chain_id), style=CHAIN_STYLES.get(chain_id, "cyan"))


def _score_style(score: float) -> str:
    if score >= 85:
        return "bold bright_green"
    if score >= 75:
        return "bold bright_yellow"
    return "bold white"


def _flow_meter(buys: int, sells: int, width: int = 12) -> Text:
    total = max(buys + sells, 1)
    buy_ratio = max(0.0, min(1.0, buys / total))
    buy_width = int(round(width * buy_ratio))
    sell_width = max(width - buy_width, 0)

    meter = Text()
    meter.append("B", style="bold green")
    meter.append("[" + ("#" * buy_width), style="green")
    meter.append("." * sell_width + "]", style="red")
    meter.append("S", style="bold red")
    meter.append(f" {buy_ratio * 100:>3.0f}/{(1 - buy_ratio) * 100:>3.0f}", style="dim")
    return meter


def _signal_style(tags: list[str], discovery: str) -> str:
    normalized = {t.lower() for t in tags}
    if "transaction-spike" in normalized or "momentum" in normalized:
        return "bold bright_magenta"
    if "buy-pressure" in normalized:
        return "magenta"
    if "fresh-pair" in normalized:
        return "bright_cyan"
    if discovery == "boost":
        return "bright_yellow"
    return "white"


def build_header() -> Panel:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    title = Text("Dexscreener CLI MCP Tool", style="bold bright_cyan")
    subtitle = Text(f"Live signal terminal  |  {now}", style="cyan")
    return Panel(
        Text.assemble(title, "\n", subtitle),
        border_style="bright_blue",
        box=box.ROUNDED,
    )


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
            f"[bold bright_white]Hot Runner Scan[/bold bright_white]  "
            f"[cyan]chains={','.join(chains)}[/cyan]  "
            f"[yellow]top={limit}[/yellow]  "
            f"[green]liq>={fmt_usd(min_liquidity_usd)}[/green]  "
            f"[green]vol24>={fmt_usd(min_volume_h24_usd)}[/green]  "
            f"[magenta]tx1h>={min_txns_h1}[/magenta]"
        ),
        box=box.ROUNDED,
        header_style="bold bright_white",
        show_edge=True,
        row_styles=["none", "dim"],
    )
    table.add_column("#", justify="right", style="bold")
    table.add_column("Chain")
    table.add_column("Token", style="bold yellow")
    table.add_column("Price", justify="right")
    table.add_column("1h", justify="right")
    table.add_column("24h Vol", justify="right")
    table.add_column("1h Txns", justify="right")
    table.add_column("Liquidity", justify="right")
    table.add_column("MCap", justify="right")
    table.add_column("Boost", justify="right")
    table.add_column("Flow", no_wrap=True)
    table.add_column("Age", justify="right")
    table.add_column("Signal")

    for i, candidate in enumerate(candidates, start=1):
        p = candidate.pair
        h1 = Text(fmt_pct(p.price_change_h1), style=_pct_style(p.price_change_h1))
        vol_style = "bold bright_cyan" if p.volume_h24 >= 1_000_000 else "cyan"
        liq_style = "bright_green" if p.liquidity_usd >= 100_000 else "green"
        signal = ", ".join(candidate.tags[:3]) if candidate.tags else candidate.discovery
        signal_text = Text(_safe_text(signal), style=_signal_style(candidate.tags, candidate.discovery))
        boost = f"{candidate.boost_total:.0f}/{candidate.boost_count}"
        token_text = Text.assemble(
            (f"{_safe_text(p.base_symbol)} ", "bold yellow"),
            (f"({candidate.score:.1f})", _score_style(candidate.score)),
        )
        age = _age_label(p.age_hours)
        age_style = "bright_cyan" if p.age_hours is not None and p.age_hours < 24 else "white"
        table.add_row(
            str(i),
            _chain_text(p.chain_id),
            token_text,
            fmt_price(p.price_usd),
            h1,
            Text(fmt_usd(p.volume_h24), style=vol_style),
            str(p.txns_h1),
            Text(fmt_usd(p.liquidity_usd), style=liq_style),
            fmt_usd(p.market_cap if p.market_cap > 0 else p.fdv),
            boost,
            _flow_meter(p.buys_h1, p.sells_h1),
            Text(age, style=age_style),
            signal_text,
        )
    if not candidates:
        table.add_row("-", "-", "No candidates matched current filters", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-")
    return table


def render_search_table(pairs: list[PairSnapshot]) -> Table:
    table = Table(
        title="[bold bright_white]Search Results[/bold bright_white]",
        box=box.ROUNDED,
        header_style="bold bright_white",
        row_styles=["none", "dim"],
    )
    table.add_column("Chain")
    table.add_column("Token", style="bold yellow")
    table.add_column("Pair", style="white")
    table.add_column("Price", justify="right")
    table.add_column("24h Vol", justify="right")
    table.add_column("1h Txns", justify="right")
    table.add_column("Liquidity", justify="right")
    table.add_column("1h", justify="right")
    for pair in pairs:
        table.add_row(
            _chain_text(pair.chain_id),
            _safe_text(pair.base_symbol),
            _safe_text(pair.pair_address),
            fmt_price(pair.price_usd),
            fmt_usd(pair.volume_h24),
            str(pair.txns_h1),
            fmt_usd(pair.liquidity_usd),
            Text(fmt_pct(pair.price_change_h1), style=_pct_style(pair.price_change_h1)),
        )
    if not pairs:
        table.add_row("-", "No matches", "-", "-", "-", "-", "-", "-")
    return table


def render_pair_detail(pair: PairSnapshot, boost_total: float = 0.0, boost_count: int = 0) -> Panel:
    mcap = pair.market_cap if pair.market_cap > 0 else pair.fdv
    content = Text()
    content.append(f"{_safe_text(pair.base_name)} ({_safe_text(pair.base_symbol)})", style="bold bright_white")
    content.append(" on ", style="dim")
    content.append(_safe_text(pair.chain_id), style=CHAIN_STYLES.get(pair.chain_id, "cyan"))
    content.append(f"/{_safe_text(pair.dex_id)}\n", style="cyan")
    content.append("Pair: ", style="dim")
    content.append(f"{_safe_text(pair.pair_address)}\n", style="white")

    content.append("Price: ", style="dim")
    content.append(fmt_price(pair.price_usd), style="bold bright_white")
    content.append(" | 1h: ", style="dim")
    content.append(fmt_pct(pair.price_change_h1), style=_pct_style(pair.price_change_h1))
    content.append(" | 24h: ", style="dim")
    content.append(fmt_pct(pair.price_change_h24), style=_pct_style(pair.price_change_h24))
    content.append("\n")
    content.append(
        f"Volume: 24h {fmt_usd(pair.volume_h24)} | 6h {fmt_usd(pair.volume_h6)} | 1h {fmt_usd(pair.volume_h1)}\n",
        style="bright_cyan",
    )
    content.append(
        f"Txns: 1h {pair.txns_h1} (B{pair.buys_h1}/S{pair.sells_h1}) | 24h {pair.txns_h24} (B{pair.buys_h24}/S{pair.sells_h24})\n",
        style="white",
    )
    content.append("Flow: ", style="dim")
    content.append_text(_flow_meter(pair.buys_h1, pair.sells_h1))
    content.append("\n")
    content.append("Liquidity: ", style="dim")
    content.append(fmt_usd(pair.liquidity_usd), style="bold green")
    content.append(" | MCap/FDV: ", style="dim")
    content.append(fmt_usd(mcap), style="white")
    content.append("\n")
    if boost_total or boost_count:
        content.append(f"Boosts observed: total={boost_total:.0f}, count={boost_count}\n", style="bright_yellow")
    if pair.pair_url:
        content.append("Dexscreener: ", style="dim")
        content.append(_safe_text(pair.pair_url), style="bright_blue")
    return Panel(
        content,
        title="[bold bright_cyan]Pair Insight[/bold bright_cyan]",
        border_style="bright_blue",
        box=box.ROUNDED,
    )


def render_distribution_panel(candidate: HotTokenCandidate) -> Panel:
    heuristics = build_distribution_heuristics(candidate)
    txt = Text()
    txt.append("Dexscreener API does not expose holder distribution in public endpoints.\n", style="bold yellow")
    txt.append("Proxy concentration signals from market structure:\n", style="bright_white")
    txt.append(
        f"- liquidity/market_cap: {heuristics['liquidity_to_market_cap']}\n"
        f"- volume/liquidity (24h): {heuristics['volume_to_liquidity_24h']}\n"
        f"- buy/sell imbalance (1h): {heuristics['buy_sell_imbalance_1h']}\n"
        f"- status: {heuristics['status']}"
    )
    return Panel(
        txt,
        title="[bold bright_magenta]Distribution Proxy[/bold bright_magenta]",
        border_style="magenta",
        box=box.ROUNDED,
    )


def render_chain_heat_table(candidates: list[HotTokenCandidate]) -> Table:
    table = Table(
        title="[bold bright_white]Chain Heat[/bold bright_white]",
        box=box.ROUNDED,
        expand=True,
        row_styles=["none", "dim"],
    )
    table.add_column("Chain")
    table.add_column("Tokens", justify="right")
    table.add_column("Avg 1h", justify="right")
    table.add_column("24h Vol", justify="right")
    table.add_column("1h Txns", justify="right")

    agg: dict[str, dict[str, float]] = defaultdict(lambda: {"count": 0, "h1": 0, "vol": 0, "txns": 0})
    for c in candidates:
        bucket = agg[c.pair.chain_id]
        bucket["count"] += 1
        bucket["h1"] += c.pair.price_change_h1
        bucket["vol"] += c.pair.volume_h24
        bucket["txns"] += c.pair.txns_h1

    for chain, data in sorted(agg.items(), key=lambda kv: kv[1]["vol"], reverse=True):
        count = int(data["count"])
        avg_h1 = (data["h1"] / count) if count else 0.0
        table.add_row(
            _chain_text(chain),
            str(count),
            Text(fmt_pct(avg_h1), style=_pct_style(avg_h1)),
            fmt_usd(data["vol"]),
            str(int(data["txns"])),
        )
    if not agg:
        table.add_row("-", "0", "0%", "$0", "0")
    return table


def render_flow_panel(candidates: list[HotTokenCandidate]) -> Panel:
    if not candidates:
        return Panel(
            "No candidates in current filter set.",
            title="[bold bright_white]Flow Summary[/bold bright_white]",
            border_style="yellow",
            box=box.ROUNDED,
        )

    total_vol = sum(c.pair.volume_h24 for c in candidates)
    total_liq = sum(c.pair.liquidity_usd for c in candidates)
    avg_h1 = sum(c.pair.price_change_h1 for c in candidates) / max(len(candidates), 1)
    avg_imbalance = sum(
        (c.pair.buys_h1 - c.pair.sells_h1) / max(c.pair.txns_h1, 1)
        for c in candidates
    ) / max(len(candidates), 1)

    risk_flags: list[str] = []
    if total_liq > 0 and (total_vol / total_liq) > 6:
        risk_flags.append("speculative-flow")
    if avg_imbalance < -0.25:
        risk_flags.append("sell-pressure")
    if avg_h1 > 20:
        risk_flags.append("high-volatility")
    if not risk_flags:
        risk_flags.append("balanced")

    regime = "risk-on" if avg_h1 > 10 and avg_imbalance > 0 else "risk-off" if avg_imbalance < -0.2 else "mixed"
    flag_style = "bold magenta"
    if "sell-pressure" in risk_flags:
        flag_style = "bold bright_red"
    elif "balanced" in risk_flags:
        flag_style = "bold bright_green"

    text = Text()
    text.append("24h volume: ", style="dim")
    text.append(f"{fmt_usd(total_vol)}\n", style="bright_cyan")
    text.append("Liquidity sum: ", style="dim")
    text.append(f"{fmt_usd(total_liq)}\n", style="green")
    text.append("Average 1h move: ", style="dim")
    text.append(f"{fmt_pct(avg_h1)}\n", style=_pct_style(avg_h1))
    text.append("Average buy/sell imbalance: ", style="dim")
    text.append(f"{avg_imbalance:+.2f}\n", style="bold white")
    text.append("Regime: ", style="dim")
    text.append(f"{regime}\n", style="bold bright_white")
    text.append(f"Flags: {', '.join(risk_flags)}", style=flag_style)
    return Panel(
        text,
        title="[bold bright_white]Flow Summary[/bold bright_white]",
        border_style="bright_blue",
        box=box.ROUNDED,
    )
