from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
import os
import shutil
import sys

from rich import box
from rich.columns import Columns
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


def fmt_holders(value: int | None) -> str:
    if value is None:
        return "n/a"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value}"


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


def holders_text(value: int | None) -> Text:
    if value is None:
        return Text("n/a", style="dim")
    if value >= 25_000:
        return Text(fmt_holders(value), style="bold bright_green")
    if value >= 5_000:
        return Text(fmt_holders(value), style="green")
    if value >= 1_000:
        return Text(fmt_holders(value), style="yellow")
    return Text(fmt_holders(value), style="bright_red")


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


def _pulse_meter(pair: PairSnapshot) -> Text:
    points = [
        max(pair.volume_h24 / 24.0, 0.0),
        max(pair.volume_h6 / 6.0, 0.0),
        max(pair.volume_h1, 0.0),
        max(pair.volume_m5 * 12.0, 0.0),
    ]
    chars = " .:-=+*#%@"
    low = min(points)
    high = max(points)
    span = max(high - low, 1e-9)
    spark = "".join(chars[int(((point - low) / span) * (len(chars) - 1))] for point in points)
    rising = points[-1] >= points[0]
    return Text(spark, style="bright_green" if rising else "yellow")


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


def _compact_level() -> int:
    mode = os.environ.get("DS_TABLE_MODE", "").strip().lower()
    if mode == "compact":
        return 2
    if mode == "full":
        return 0

    forced_width = os.environ.get("DS_TABLE_WIDTH", "").strip()
    if forced_width:
        try:
            width = int(forced_width)
        except ValueError:
            width = shutil.get_terminal_size((110, 40)).columns
    else:
        # In non-interactive captures, defaulting too wide causes heavy truncation.
        if not sys.stdout.isatty():
            width = shutil.get_terminal_size((110, 40)).columns
        else:
            width = shutil.get_terminal_size((140, 40)).columns
    if width < 100:
        return 2
    if width < 140:
        return 1
    return 0


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
    compact_level = _compact_level()
    compact = compact_level >= 1
    title = (
        f"[bold bright_white]Hot Runner Scan[/bold bright_white]  "
        f"[cyan]chains={','.join(chains)}[/cyan]  "
        f"[yellow]top={limit}[/yellow]  "
        f"[green]liq>={fmt_usd(min_liquidity_usd)}[/green]  "
        f"[green]vol24>={fmt_usd(min_volume_h24_usd)}[/green]  "
        f"[magenta]tx1h>={min_txns_h1}[/magenta]"
    )
    if compact:
        title = f"[bold bright_white]Hot Runner Scan[/bold bright_white] [cyan]{','.join(chains)}[/cyan]"
    table = Table(
        title=title,
        box=box.ROUNDED,
        header_style="bold bright_white",
        show_edge=True,
        row_styles=["none", "dim"],
    )
    table.add_column("#", justify="right", style="bold")
    table.add_column("Chain")
    table.add_column("Token", style="bold yellow")
    table.add_column("1h", justify="right")
    table.add_column("24h Vol", justify="right")
    table.add_column("1h Txns", justify="right")
    table.add_column("Liquidity", justify="right")
    table.add_column("Holders", justify="right")
    if not compact:
        table.add_column("Price", justify="right")
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
        if compact:
            token_text = Text(_safe_text(p.base_symbol), style="bold yellow")
        else:
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
            h1,
            Text(fmt_usd(p.volume_h24), style=vol_style),
            str(p.txns_h1),
            Text(fmt_usd(p.liquidity_usd), style=liq_style),
            holders_text(p.holders_count),
            *((
                fmt_price(p.price_usd),
                fmt_usd(p.market_cap if p.market_cap > 0 else p.fdv),
                boost,
                _flow_meter(p.buys_h1, p.sells_h1),
                Text(age, style=age_style),
                signal_text,
            ) if not compact else ()),
        )
    if not candidates:
        if compact:
            table.add_row("-", "-", "No candidates matched current filters", "-", "-", "-", "-", "-")
        else:
            table.add_row("-", "-", "No candidates matched current filters", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-")
    return table


def render_new_runner_spotlight(
    candidates: list[HotTokenCandidate],
    *,
    chain: str,
    max_age_hours: float,
    limit: int,
) -> Panel:
    txt = Text()
    txt.append(f"Chain: {chain}\n", style="bold bright_blue")
    txt.append(f"Window: <= {max_age_hours:.0f}h\n", style="dim")
    txt.append(f"Target: top {limit}\n", style="dim")
    txt.append("\n")
    txt.append("Top Movers\n", style="bold bright_white")

    if not candidates:
        txt.append("No fresh runners found with this filter set.", style="yellow")
        return Panel(
            txt,
            title="[bold bright_white]New Runner Radar[/bold bright_white]",
            border_style="bright_blue",
            box=box.ROUNDED,
        )

    for i, candidate in enumerate(candidates[:3], start=1):
        p = candidate.pair
        rank_style = "bold bright_yellow" if i == 1 else "bold bright_white"
        age = _age_label(p.age_hours)
        txt.append(f"{i}. ", style=rank_style)
        txt.append(f"{_safe_text(p.base_symbol)} ", style="bold cyan")
        txt.append(f"{fmt_pct(p.price_change_h1)}", style=_pct_style(p.price_change_h1))
        txt.append(" | ", style="dim")
        txt.append(f"score {candidate.score:.1f}", style=_score_style(candidate.score))
        txt.append(" | ", style="dim")
        txt.append(f"ready {candidate.analytics.breakout_readiness:.0f}", style="bright_magenta")
        txt.append(" | ", style="dim")
        txt.append(f"age {age}\n", style="white")
    return Panel(
        txt,
        title="[bold bright_white]New Runner Radar[/bold bright_white]",
        border_style="bright_blue",
        box=box.ROUNDED,
    )


def render_new_runners_table(
    candidates: list[HotTokenCandidate],
    *,
    chain: str,
    max_age_hours: float,
    limit: int,
    selected_index: int | None = None,
) -> Table:
    compact_level = _compact_level()
    show_chain = len({c.pair.chain_id for c in candidates}) > 1
    title = (
        f"[bold bright_white]Best New Runners[/bold bright_white]  "
        f"[cyan]chain={chain}[/cyan]  "
        f"[yellow]top={limit}[/yellow]  "
        f"[magenta]age<={max_age_hours:.0f}h[/magenta]"
    )
    if compact_level >= 1:
        title = f"[bold bright_white]Best New Runners[/bold bright_white] [cyan]{chain}[/cyan]"
    table = Table(
        title=title,
        box=box.ROUNDED,
        header_style="bold bright_white",
        row_styles=["none", "dim"],
    )
    table.add_column("#", justify="right")
    if show_chain:
        table.add_column("Ch")
    table.add_column("Token", style="bold yellow")
    if compact_level == 0:
        table.add_column("Score", justify="right")
        table.add_column("Ready", justify="right")
        table.add_column("RS", justify="right")
    if compact_level <= 1:
        table.add_column("Age", justify="right")
    table.add_column("1h", justify="right")
    table.add_column("24h Vol", justify="right")
    table.add_column("Tx1h", justify="right")
    table.add_column("Liq", justify="right")
    table.add_column("Holders", justify="right")
    if compact_level == 0:
        table.add_column("Pulse", justify="right")
        table.add_column("Flow", no_wrap=True)

    for i, candidate in enumerate(candidates[:limit], start=1):
        p = candidate.pair
        a = candidate.analytics
        is_selected = selected_index is not None and (i - 1) == selected_index
        token_style = "bold black on bright_cyan" if is_selected else "bold yellow"
        score_style = "bold black on bright_cyan" if is_selected else _score_style(candidate.score)
        score = Text(f"{candidate.score:.1f}", style=score_style)
        age = Text(_age_label(p.age_hours), style="bright_cyan" if p.age_hours is not None and p.age_hours < 24 else "white")
        rs_style = "bold bright_green" if a.relative_strength >= 8 else "bold bright_red" if a.relative_strength <= -8 else "white"
        readiness_style = "bold bright_green" if a.breakout_readiness >= 70 else "yellow" if a.breakout_readiness >= 55 else "dim"
        row: list[object] = [str(i)]
        if show_chain:
            row.append(_chain_text(p.chain_id))
        row.append(Text(_safe_text(p.base_symbol), style=token_style))
        if compact_level == 0:
            row.extend(
                [
                    score,
                    Text(f"{a.breakout_readiness:.0f}", style=readiness_style),
                    Text(f"{a.relative_strength:+.1f}", style=rs_style),
                    age,
                    Text(fmt_pct(p.price_change_h1), style=_pct_style(p.price_change_h1)),
                    fmt_usd(p.volume_h24),
                    str(p.txns_h1),
                    fmt_usd(p.liquidity_usd),
                    holders_text(p.holders_count),
                    _pulse_meter(p),
                    _flow_meter(p.buys_h1, p.sells_h1),
                ]
            )
        elif compact_level == 1:
            row.extend(
                [
                    age,
                    Text(fmt_pct(p.price_change_h1), style=_pct_style(p.price_change_h1)),
                    fmt_usd(p.volume_h24),
                    str(p.txns_h1),
                    fmt_usd(p.liquidity_usd),
                    holders_text(p.holders_count),
                ]
            )
        else:
            row.extend(
                [
                    Text(fmt_pct(p.price_change_h1), style=_pct_style(p.price_change_h1)),
                    fmt_usd(p.volume_h24),
                    str(p.txns_h1),
                    fmt_usd(p.liquidity_usd),
                    holders_text(p.holders_count),
                ]
            )
        table.add_row(*row)
    if not candidates:
        fallback = ["-"] * len(table.columns)
        fallback[2 if show_chain else 1] = "No fresh runners found"
        table.add_row(*fallback)
    return table


def render_top_runner_cards(candidates: list[HotTokenCandidate], *, pulse: bool = False) -> Columns:
    cards: list[Panel] = []
    for rank in range(1, 4):
        if rank <= len(candidates):
            candidate = candidates[rank - 1]
            p = candidate.pair
            border = "bright_green" if pulse and rank == 1 else "bright_blue"
            txt = Text()
            txt.append(f"#{rank} ", style="bold bright_white")
            txt.append(f"{_safe_text(p.base_symbol)}\n", style="bold bright_cyan")
            txt.append(f"Score {candidate.score:.1f}\n", style=_score_style(candidate.score))
            txt.append("Ready: ", style="dim")
            txt.append(f"{candidate.analytics.breakout_readiness:.0f}\n", style="bright_magenta")
            txt.append("RS: ", style="dim")
            txt.append(f"{candidate.analytics.relative_strength:+.1f}\n", style="white")
            txt.append("Holders: ", style="dim")
            txt.append_text(holders_text(p.holders_count))
            txt.append("\n")
            txt.append("1h: ", style="dim")
            txt.append(f"{fmt_pct(p.price_change_h1)}\n", style=_pct_style(p.price_change_h1))
            txt.append("24h Vol: ", style="dim")
            txt.append(f"{fmt_usd(p.volume_h24)}\n", style="bright_cyan")
            txt.append("Flow: ", style="dim")
            txt.append_text(_flow_meter(p.buys_h1, p.sells_h1))
            txt.append("\n")
            txt.append(f"Age: {_age_label(p.age_hours)}", style="bright_cyan")
            cards.append(
                Panel(
                    txt,
                    title=f"[bold bright_white]Top {rank}[/bold bright_white]",
                    border_style=border,
                    box=box.ROUNDED,
                )
            )
            continue

        cards.append(
            Panel(
                Text("Waiting for runner data...", style="dim"),
                title=f"[bold]Top {rank}[/bold]",
                border_style="dim",
                box=box.ROUNDED,
            )
        )
    return Columns(cards, equal=True, expand=True)


def _move_text(
    *,
    key: tuple[str, str],
    rank: int,
    previous_ranks: dict[tuple[str, str], int],
) -> Text:
    prev = previous_ranks.get(key)
    if prev is None:
        return Text("new", style="bold bright_cyan")
    delta = prev - rank
    if delta > 0:
        return Text(f"^{delta}", style="bold bright_green")
    if delta < 0:
        return Text(f"v{abs(delta)}", style="bold bright_red")
    return Text("=", style="dim")


def render_rank_movers_table(
    candidates: list[HotTokenCandidate],
    *,
    previous_ranks: dict[tuple[str, str], int],
    limit: int,
) -> Table:
    compact_level = _compact_level()
    show_chain = len({c.pair.chain_id for c in candidates}) > 1
    table = Table(
        title="[bold bright_white]Rank Movers[/bold bright_white]",
        box=box.ROUNDED,
        header_style="bold bright_white",
        row_styles=["none", "dim"],
    )
    table.add_column("Rank", justify="right")
    table.add_column("Move", justify="right")
    if show_chain:
        table.add_column("Ch")
    table.add_column("Token", style="bold yellow")
    if compact_level == 0:
        table.add_column("Score", justify="right")
        table.add_column("Ready", justify="right")
        table.add_column("RS", justify="right")
    table.add_column("1h", justify="right")
    table.add_column("Vol1h", justify="right")
    table.add_column("Tx1h", justify="right")
    table.add_column("Holders", justify="right")
    table.add_column("Age", justify="right")

    for rank, candidate in enumerate(candidates[:limit], start=1):
        p = candidate.pair
        row: list[object] = [
            str(rank),
            _move_text(key=candidate.key, rank=rank, previous_ranks=previous_ranks),
        ]
        if show_chain:
            row.append(_chain_text(p.chain_id))
        row.append(_safe_text(p.base_symbol))
        if compact_level == 0:
            row.extend(
                [
                    Text(f"{candidate.score:.1f}", style=_score_style(candidate.score)),
                    Text(f"{candidate.analytics.breakout_readiness:.0f}", style="bright_magenta"),
                    Text(f"{candidate.analytics.relative_strength:+.1f}", style="white"),
                    Text(fmt_pct(p.price_change_h1), style=_pct_style(p.price_change_h1)),
                    fmt_usd(p.volume_h1),
                    str(p.txns_h1),
                    holders_text(p.holders_count),
                    _age_label(p.age_hours),
                ]
            )
        else:
            row.extend(
                [
                    Text(fmt_pct(p.price_change_h1), style=_pct_style(p.price_change_h1)),
                    fmt_usd(p.volume_h1),
                    str(p.txns_h1),
                    holders_text(p.holders_count),
                    _age_label(p.age_hours),
                ]
            )
        table.add_row(*row)

    if not candidates:
        fallback = ["-"] * len(table.columns)
        fallback[3 if show_chain else 2] = "No movers yet"
        table.add_row(*fallback)
    return table


def render_search_table(pairs: list[PairSnapshot]) -> Table:
    compact = _compact_level() >= 1
    table = Table(
        title="[bold bright_white]Search Results[/bold bright_white]",
        box=box.ROUNDED,
        header_style="bold bright_white",
        row_styles=["none", "dim"],
    )
    table.add_column("Chain")
    table.add_column("Token", style="bold yellow")
    table.add_column("Price", justify="right")
    table.add_column("Vol24", justify="right")
    table.add_column("Tx1h", justify="right")
    table.add_column("Liq", justify="right")
    table.add_column("Holders", justify="right")
    table.add_column("1h", justify="right")
    if not compact:
        table.add_column("Pair", style="white")
    for pair in pairs:
        if pair.price_usd >= 0.01:
            price = f"${pair.price_usd:,.4f}"
        else:
            price = f"${pair.price_usd:,.6f}"
        table.add_row(
            _chain_text(pair.chain_id),
            _safe_text(pair.base_symbol),
            price,
            fmt_usd(pair.volume_h24),
            str(pair.txns_h1),
            fmt_usd(pair.liquidity_usd),
            holders_text(pair.holders_count),
            Text(fmt_pct(pair.price_change_h1), style=_pct_style(pair.price_change_h1)),
            *((_safe_text(pair.pair_address),) if not compact else ()),
        )
    if not pairs:
        if compact:
            table.add_row("-", "No matches", "-", "-", "-", "-", "-", "-")
        else:
            table.add_row("-", "No matches", "-", "-", "-", "-", "-", "-", "-")
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
    content.append("Holders: ", style="dim")
    content.append_text(holders_text(pair.holders_count))
    if pair.holders_source:
        content.append(f" ({pair.holders_source})", style="dim")
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
    if candidate.pair.holders_count is not None:
        txt.append("Observed holders: ", style="dim")
        txt.append_text(holders_text(candidate.pair.holders_count))
        if candidate.pair.holders_source:
            txt.append(f" ({candidate.pair.holders_source})", style="dim")
        txt.append("\n")
    else:
        txt.append("Holder count unavailable for this token/chain via public adapters.\n", style="bold yellow")
    txt.append("Market-structure concentration signals:\n", style="bright_white")
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

    market_flags: list[str] = []
    if total_liq > 0 and (total_vol / total_liq) > 6:
        market_flags.append("speculative-flow")
    if avg_imbalance < -0.25:
        market_flags.append("sell-pressure")
    if avg_h1 > 20:
        market_flags.append("high-volatility")
    if not market_flags:
        market_flags.append("balanced")

    regime = "trend-up" if avg_h1 > 10 and avg_imbalance > 0 else "trend-down" if avg_imbalance < -0.2 else "mixed"
    flag_style = "bold magenta"
    if "sell-pressure" in market_flags:
        flag_style = "bold bright_red"
    elif "balanced" in market_flags:
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
    text.append(f"Flags: {', '.join(market_flags)}", style=flag_style)
    return Panel(
        text,
        title="[bold bright_white]Flow Summary[/bold bright_white]",
        border_style="bright_blue",
        box=box.ROUNDED,
    )
