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

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Visual identity
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

CHAIN_STYLES = {
    "solana": "bright_green",
    "base": "bright_blue",
    "ethereum": "bright_white",
    "bsc": "bright_yellow",
    "arbitrum": "bright_cyan",
}

CHAIN_LABEL = {
    "solana": "SOL",
    "base": "BASE",
    "ethereum": "ETH",
    "bsc": "BSC",
    "arbitrum": "ARB",
}

# Unicode visual elements
SPARK = "\u2581\u2582\u2583\u2584\u2585\u2586\u2587\u2588"
BAR_FILL = "\u2588"
BAR_EMPTY = "\u2591"
DOT = "\u25cf"
ARROW_UP = "\u25b2"
ARROW_DOWN = "\u25bc"
DIAMOND = "\u25c6"
DIAMOND_SM = "\u25c8"
SEPARATOR = "\u2501"
VLINE = "\u2502"

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Formatting
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Style helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _pct_style(value: float) -> str:
    if value >= 100:
        return "bold bright_green"
    if value >= 12:
        return "bold bright_green"
    if value > 0:
        return "green"
    if value <= -30:
        return "bold bright_red"
    if value <= -12:
        return "bold bright_red"
    if value < 0:
        return "red"
    return "dim"


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
    label = CHAIN_LABEL.get(chain_id, chain_id.upper()[:4])
    style = CHAIN_STYLES.get(chain_id, "cyan")
    txt = Text()
    txt.append(_safe_text(DOT), style=f"bold {style}")
    txt.append(f" {_safe_text(label)}", style=style)
    return txt


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


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Visual gauges & meters
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _score_gauge(score: float, width: int = 8) -> Text:
    """Visual gauge bar for scores: ████████░░ 82"""
    filled = int(round((min(score, 100) / 100) * width))
    empty = width - filled
    if score >= 85:
        fill_style = "bright_green"
    elif score >= 75:
        fill_style = "bright_yellow"
    elif score >= 60:
        fill_style = "white"
    else:
        fill_style = "bright_red"
    txt = Text()
    txt.append(_safe_text(BAR_FILL * filled), style=fill_style)
    txt.append(_safe_text(BAR_EMPTY * empty), style="dim")
    txt.append(f" {score:.0f}", style=f"bold {fill_style}")
    return txt


def _momentum_text(value: float) -> Text:
    """Arrow-prefixed percentage: ▲ +25.3% or ▼ -12.1%"""
    txt = Text()
    if value > 0:
        style = _pct_style(value)
        txt.append(_safe_text(f"{ARROW_UP} "), style=style)
        txt.append(fmt_pct(value), style=style)
    elif value < 0:
        style = _pct_style(value)
        txt.append(_safe_text(f"{ARROW_DOWN} "), style=style)
        txt.append(fmt_pct(value), style=style)
    else:
        txt.append(f"  {fmt_pct(value)}", style="dim")
    return txt


def _vol_heat(value: float) -> Text:
    """Volume with heat-level styling."""
    if value >= 10_000_000:
        return Text(fmt_usd(value), style="bold bright_cyan")
    if value >= 1_000_000:
        return Text(fmt_usd(value), style="bright_cyan")
    if value >= 100_000:
        return Text(fmt_usd(value), style="cyan")
    return Text(fmt_usd(value), style="dim cyan")


def _age_badge(hours: float | None) -> Text:
    """Styled age with freshness indicator."""
    label = _age_label(hours)
    if hours is None:
        return Text(label, style="dim")
    if hours < 1:
        return Text(_safe_text(f"{DOT} {label}"), style="bold bright_cyan")
    if hours < 6:
        return Text(label, style="bright_cyan")
    if hours < 24:
        return Text(label, style="cyan")
    if hours < 72:
        return Text(label, style="white")
    return Text(label, style="dim")


def _rank_badge(rank: int) -> Text:
    """Medal-styled rank for top positions."""
    if rank == 1:
        return Text(_safe_text(f"{DIAMOND} {rank}"), style="bold bright_yellow")
    if rank == 2:
        return Text(_safe_text(f"{DIAMOND} {rank}"), style="bold bright_white")
    if rank == 3:
        return Text(_safe_text(f"{DIAMOND} {rank}"), style="bold bright_cyan")
    return Text(str(rank), style="bold")


def _flow_meter(buys: int, sells: int, width: int = 12) -> Text:
    """Block-character flow meter with colored buy/sell segments."""
    total = max(buys + sells, 1)
    buy_ratio = max(0.0, min(1.0, buys / total))
    buy_width = int(round(width * buy_ratio))
    sell_width = max(width - buy_width, 0)
    buy_pct = buy_ratio * 100
    sell_pct = (1 - buy_ratio) * 100

    if buy_ratio >= 0.65:
        buy_style = "bold bright_green"
    elif buy_ratio >= 0.5:
        buy_style = "green"
    else:
        buy_style = "yellow"

    if buy_ratio <= 0.35:
        sell_style = "bold bright_red"
    elif buy_ratio <= 0.5:
        sell_style = "red"
    else:
        sell_style = "bright_red"

    meter = Text()
    meter.append(_safe_text(BAR_FILL * buy_width), style=buy_style)
    meter.append(_safe_text(BAR_FILL * sell_width), style=sell_style)
    meter.append(f" {buy_pct:>2.0f}/{sell_pct:>2.0f}", style="dim")
    return meter


def _pulse_meter(pair: PairSnapshot) -> Text:
    """Sparkline volume pulse using Unicode block elements."""
    points = [
        max(pair.volume_h24 / 24.0, 0.0),
        max(pair.volume_h6 / 6.0, 0.0),
        max(pair.volume_h1, 0.0),
        max(pair.volume_m5 * 12.0, 0.0),
    ]
    low = min(points)
    high = max(points)
    span = max(high - low, 1e-9)
    spark = "".join(SPARK[int(((point - low) / span) * (len(SPARK) - 1))] for point in points)
    rising = points[-1] >= points[0]
    style = "bold bright_green" if rising else "bright_yellow"
    return Text(_safe_text(spark), style=style)


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
        if not sys.stdout.isatty():
            width = shutil.get_terminal_size((110, 40)).columns
        else:
            width = shutil.get_terminal_size((140, 40)).columns
    if width < 100:
        return 2
    if width < 140:
        return 1
    return 0


def _signal_badge(tags: list[str], discovery: str) -> Text:
    """Compact signal badge with dot indicator."""
    signal = ", ".join(tags[:3]) if tags else discovery
    style = _signal_style(tags, discovery)
    txt = Text()
    normalized = {t.lower() for t in tags}
    if "transaction-spike" in normalized or "momentum" in normalized:
        txt.append(_safe_text(f"{DOT} "), style="bold bright_magenta")
    elif "buy-pressure" in normalized:
        txt.append(_safe_text(f"{DOT} "), style="magenta")
    elif "fresh-pair" in normalized:
        txt.append(_safe_text(f"{DOT} "), style="bright_cyan")
    elif discovery == "boost":
        txt.append(_safe_text(f"{DOT} "), style="bright_yellow")
    txt.append(_safe_text(signal), style=style)
    return txt


def _holders_gauge(value: int | None) -> Text:
    """Holder count with mini visual tier bar."""
    if value is None:
        return Text("n/a", style="dim")
    tier_bar_w = 3
    if value >= 25_000:
        bar = BAR_FILL * tier_bar_w
        style = "bold bright_green"
    elif value >= 5_000:
        bar = BAR_FILL * 2 + BAR_EMPTY * 1
        style = "green"
    elif value >= 1_000:
        bar = BAR_FILL * 1 + BAR_EMPTY * 2
        style = "yellow"
    else:
        bar = BAR_EMPTY * tier_bar_w
        style = "bright_red"
    txt = Text()
    txt.append(_safe_text(bar), style=style)
    txt.append(f" {fmt_holders(value)}", style=style)
    return txt


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Header
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def build_header() -> Panel:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

    content = Text()
    # Gradient-styled logo
    logo_chars = "DEX SCANNER"
    gradient = [
        "bold bright_cyan",
        "bold bright_cyan",
        "bold bright_cyan",
        "bold bright_cyan",
        "bold bright_blue",
        "bold bright_magenta",
        "bold bright_magenta",
        "bold bright_blue",
        "bold bright_cyan",
        "bold bright_cyan",
        "bold bright_cyan",
    ]
    for ch, sty in zip(logo_chars, gradient):
        content.append(ch, style=sty)
    content.append("\n")

    content.append(_safe_text(SEPARATOR * 30) + "\n", style="dim bright_blue")
    content.append("Live Signal Terminal", style="dim bright_white")
    content.append(_safe_text(f"  {DOT}  "), style="dim bright_blue")
    content.append(now, style="dim cyan")

    return Panel(
        content,
        border_style="bright_blue",
        box=box.HEAVY,
        padding=(0, 1),
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Hot runner table
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


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

    chain_labels = ",".join(CHAIN_LABEL.get(c, c.upper()[:4]) for c in chains)
    if compact:
        title = (
            f"[bold bright_cyan]{_safe_text(DIAMOND)} Hot Runner Scan[/bold bright_cyan] "
            f"[bright_blue]{chain_labels}[/bright_blue]"
        )
    else:
        title = (
            f"[bold bright_cyan]{_safe_text(DIAMOND)} Hot Runner Scan[/bold bright_cyan]  "
            f"[bright_blue]{chain_labels}[/bright_blue]  "
            f"[dim]top={limit}  liq>={fmt_usd(min_liquidity_usd)}  "
            f"vol>={fmt_usd(min_volume_h24_usd)}  tx>={min_txns_h1}[/dim]"
        )

    table = Table(
        title=title,
        box=box.HEAVY,
        header_style="bold bright_white",
        show_edge=True,
        row_styles=["", "dim"],
        border_style="bright_blue",
        title_style="",
    )
    table.add_column(_safe_text(f" {DIAMOND}"), justify="right", style="bold bright_white", width=4)
    table.add_column("Chain", min_width=6)
    table.add_column("Token", style="bold bright_yellow", min_width=8)
    if not compact:
        table.add_column("Score", justify="center", min_width=12)
    table.add_column("1h", justify="right", min_width=10)
    table.add_column("24h Vol", justify="right")
    table.add_column("1h Txns", justify="right")
    table.add_column("Liq", justify="right")
    table.add_column("Holders", justify="right")
    if not compact:
        table.add_column("Price", justify="right")
        table.add_column("MCap", justify="right")
        table.add_column("Boost", justify="right")
        table.add_column("Flow", no_wrap=True, min_width=18)
        table.add_column("Age", justify="right")
        table.add_column("Signal")

    for i, candidate in enumerate(candidates, start=1):
        p = candidate.pair
        h1 = _momentum_text(p.price_change_h1)
        signal_text = _signal_badge(candidate.tags, candidate.discovery)
        boost = f"{candidate.boost_total:.0f}/{candidate.boost_count}"
        liq_style = "bold bright_green" if p.liquidity_usd >= 100_000 else "green"

        if compact:
            token_text = Text(_safe_text(p.base_symbol), style="bold bright_yellow")
            table.add_row(
                _rank_badge(i),
                _chain_text(p.chain_id),
                token_text,
                h1,
                _vol_heat(p.volume_h24),
                str(p.txns_h1),
                Text(fmt_usd(p.liquidity_usd), style=liq_style),
                holders_text(p.holders_count),
            )
        else:
            token_text = Text(_safe_text(p.base_symbol), style="bold bright_yellow")
            table.add_row(
                _rank_badge(i),
                _chain_text(p.chain_id),
                token_text,
                _score_gauge(candidate.score),
                h1,
                _vol_heat(p.volume_h24),
                str(p.txns_h1),
                Text(fmt_usd(p.liquidity_usd), style=liq_style),
                holders_text(p.holders_count),
                fmt_price(p.price_usd),
                fmt_usd(p.market_cap if p.market_cap > 0 else p.fdv),
                boost,
                _flow_meter(p.buys_h1, p.sells_h1),
                _age_badge(p.age_hours),
                signal_text,
            )

    if not candidates:
        cols = len(table.columns)
        fallback = ["-"] * cols
        fallback[min(2, cols - 1)] = "No candidates matched current filters"
        table.add_row(*fallback)

    return table


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# New runner spotlight
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def render_new_runner_spotlight(
    candidates: list[HotTokenCandidate],
    *,
    chain: str,
    max_age_hours: float,
    limit: int,
) -> Panel:
    chain_lbl = CHAIN_LABEL.get(chain, chain.upper()[:4])
    chain_style = CHAIN_STYLES.get(chain, "cyan")

    txt = Text()
    txt.append(_safe_text(f"{DOT} "), style=f"bold {chain_style}")
    txt.append(f"Chain: {chain_lbl}", style=f"bold {chain_style}")
    txt.append(_safe_text(f"  {VLINE}  "), style="dim")
    txt.append(f"Window: <={max_age_hours:.0f}h", style="dim")
    txt.append(_safe_text(f"  {VLINE}  "), style="dim")
    txt.append(f"Target: top {limit}\n", style="dim")
    txt.append(_safe_text(SEPARATOR * 36) + "\n", style="dim bright_blue")

    if not candidates:
        txt.append("No fresh runners found with this filter set.", style="yellow")
        return Panel(
            txt,
            title=f"[bold bright_cyan]{_safe_text(DIAMOND)} New Runner Radar[/bold bright_cyan]",
            border_style="bright_blue",
            box=box.HEAVY,
        )

    for i, candidate in enumerate(candidates[:3], start=1):
        p = candidate.pair
        age = _age_label(p.age_hours)

        txt.append_text(_rank_badge(i))
        txt.append("  ", style="")
        txt.append(f"{_safe_text(p.base_symbol)} ", style="bold bright_cyan")
        txt.append_text(_momentum_text(p.price_change_h1))
        txt.append(_safe_text(f" {VLINE} "), style="dim")
        txt.append("score ", style="dim")
        txt.append(f"{candidate.score:.1f}", style=_score_style(candidate.score))
        txt.append(_safe_text(f" {VLINE} "), style="dim")
        txt.append("ready ", style="dim")
        txt.append(f"{candidate.analytics.breakout_readiness:.0f}", style="bright_magenta")
        txt.append(_safe_text(f" {VLINE} "), style="dim")
        txt.append(f"age {age}\n", style="cyan")

    return Panel(
        txt,
        title=f"[bold bright_cyan]{_safe_text(DIAMOND)} New Runner Radar[/bold bright_cyan]",
        border_style="bright_blue",
        box=box.HEAVY,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# New runners table
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


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
    chain_lbl = CHAIN_LABEL.get(chain, chain.upper()[:4])

    if compact_level >= 1:
        title = (
            f"[bold bright_cyan]{_safe_text(DIAMOND)} Best New Runners[/bold bright_cyan] "
            f"[bright_blue]{chain_lbl}[/bright_blue]"
        )
    else:
        title = (
            f"[bold bright_cyan]{_safe_text(DIAMOND)} Best New Runners[/bold bright_cyan]  "
            f"[bright_blue]{chain_lbl}[/bright_blue]  "
            f"[dim]top={limit}  age<={max_age_hours:.0f}h[/dim]"
        )

    table = Table(
        title=title,
        box=box.HEAVY,
        header_style="bold bright_white",
        row_styles=["", "dim"],
        border_style="bright_blue",
        title_style="",
    )
    table.add_column(_safe_text(f" {DIAMOND}"), justify="right", width=4)
    if show_chain:
        table.add_column("Ch", min_width=6)
    table.add_column("Token", style="bold bright_yellow", min_width=8)
    if compact_level == 0:
        table.add_column("Score", justify="center", min_width=12)
        table.add_column("Ready", justify="right")
        table.add_column("RS", justify="right")
    if compact_level <= 1:
        table.add_column("Age", justify="right")
    table.add_column("1h", justify="right", min_width=10)
    table.add_column("24h Vol", justify="right")
    table.add_column("Tx1h", justify="right")
    table.add_column("Liq", justify="right")
    table.add_column("Holders", justify="right")
    if compact_level == 0:
        table.add_column("Pulse", justify="right")
        table.add_column("Flow", no_wrap=True, min_width=18)

    for i, candidate in enumerate(candidates[:limit], start=1):
        p = candidate.pair
        a = candidate.analytics
        is_selected = selected_index is not None and (i - 1) == selected_index
        token_style = "bold black on bright_cyan" if is_selected else "bold bright_yellow"
        score_style_sel = "bold black on bright_cyan" if is_selected else _score_style(candidate.score)

        rs_style = (
            "bold bright_green" if a.relative_strength >= 8
            else "bold bright_red" if a.relative_strength <= -8
            else "white"
        )
        readiness_style = (
            "bold bright_green" if a.breakout_readiness >= 70
            else "yellow" if a.breakout_readiness >= 55
            else "dim"
        )

        row: list[object] = [_rank_badge(i)]
        if show_chain:
            row.append(_chain_text(p.chain_id))
        row.append(Text(_safe_text(p.base_symbol), style=token_style))

        if compact_level == 0:
            if is_selected:
                score_txt = Text(f"{candidate.score:.1f}", style=score_style_sel)
            else:
                score_txt = _score_gauge(candidate.score)
            row.extend([
                score_txt,
                Text(f"{a.breakout_readiness:.0f}", style=readiness_style),
                Text(f"{a.relative_strength:+.1f}", style=rs_style),
                _age_badge(p.age_hours),
                _momentum_text(p.price_change_h1),
                _vol_heat(p.volume_h24),
                str(p.txns_h1),
                fmt_usd(p.liquidity_usd),
                holders_text(p.holders_count),
                _pulse_meter(p),
                _flow_meter(p.buys_h1, p.sells_h1),
            ])
        elif compact_level == 1:
            row.extend([
                _age_badge(p.age_hours),
                _momentum_text(p.price_change_h1),
                _vol_heat(p.volume_h24),
                str(p.txns_h1),
                fmt_usd(p.liquidity_usd),
                holders_text(p.holders_count),
            ])
        else:
            row.extend([
                _momentum_text(p.price_change_h1),
                _vol_heat(p.volume_h24),
                str(p.txns_h1),
                fmt_usd(p.liquidity_usd),
                holders_text(p.holders_count),
            ])
        table.add_row(*row)

    if not candidates:
        fallback = ["-"] * len(table.columns)
        idx = 2 if show_chain else 1
        fallback[idx] = "No fresh runners found"
        table.add_row(*fallback)

    return table


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Top runner cards
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def render_top_runner_cards(candidates: list[HotTokenCandidate], *, pulse: bool = False) -> Columns:
    rank_borders = ["bright_yellow", "bright_white", "bright_cyan"]
    rank_titles = [
        f"[bold bright_yellow]{_safe_text(DIAMOND)} #1  GOLD[/bold bright_yellow]",
        f"[bold bright_white]{_safe_text(DIAMOND)} #2  SILVER[/bold bright_white]",
        f"[bold bright_cyan]{_safe_text(DIAMOND)} #3  BRONZE[/bold bright_cyan]",
    ]

    cards: list[Panel] = []
    for rank in range(1, 4):
        if rank <= len(candidates):
            candidate = candidates[rank - 1]
            p = candidate.pair
            border = rank_borders[rank - 1]
            if pulse and rank == 1:
                border = "bold bright_green"

            txt = Text()
            # Token name
            txt.append(f"{_safe_text(p.base_symbol)}\n", style="bold bright_white")

            # Score gauge
            txt.append("Score  ", style="dim")
            txt.append_text(_score_gauge(candidate.score, width=10))
            txt.append("\n")

            # Ready & RS
            ready = candidate.analytics.breakout_readiness
            ready_style = (
                "bold bright_green" if ready >= 70
                else "bright_yellow" if ready >= 55
                else "dim"
            )
            txt.append("Ready  ", style="dim")
            txt.append(f"{ready:.0f}", style=ready_style)
            rs = candidate.analytics.relative_strength
            txt.append(_safe_text(f"    {VLINE}    "), style="dim")
            txt.append("RS ", style="dim")
            txt.append(f"{rs:+.1f}\n", style="bold bright_green" if rs >= 5 else "bold bright_red" if rs <= -5 else "white")

            # Separator
            txt.append(_safe_text(SEPARATOR * 22) + "\n", style="dim")

            # Holders
            txt.append("Holders  ", style="dim")
            txt.append_text(holders_text(p.holders_count))
            txt.append("\n")

            # 1h momentum
            txt.append("1h       ", style="dim")
            txt.append_text(_momentum_text(p.price_change_h1))
            txt.append("\n")

            # Volume
            txt.append("24h Vol  ", style="dim")
            txt.append_text(_vol_heat(p.volume_h24))
            txt.append("\n")

            # Flow
            txt.append("Flow     ", style="dim")
            txt.append_text(_flow_meter(p.buys_h1, p.sells_h1))
            txt.append("\n")

            # Age
            txt.append("Age      ", style="dim")
            txt.append_text(_age_badge(p.age_hours))

            cards.append(
                Panel(
                    txt,
                    title=rank_titles[rank - 1],
                    border_style=border,
                    box=box.HEAVY,
                )
            )
            continue

        cards.append(
            Panel(
                Text(_safe_text(f"{BAR_EMPTY * 8} Waiting for data..."), style="dim"),
                title=f"[dim]{_safe_text(DIAMOND)} #{rank}[/dim]",
                border_style="dim",
                box=box.HEAVY,
            )
        )
    return Columns(cards, equal=True, expand=True)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Rank movers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


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
        return Text(_safe_text(f"{ARROW_UP}{delta}"), style="bold bright_green")
    if delta < 0:
        return Text(_safe_text(f"{ARROW_DOWN}{abs(delta)}"), style="bold bright_red")
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
        title=f"[bold bright_cyan]{_safe_text(DIAMOND)} Rank Movers[/bold bright_cyan]",
        box=box.HEAVY,
        header_style="bold bright_white",
        row_styles=["", "dim"],
        border_style="bright_blue",
        title_style="",
    )
    table.add_column("Rank", justify="right", width=4)
    table.add_column("Move", justify="right")
    if show_chain:
        table.add_column("Ch", min_width=6)
    table.add_column("Token", style="bold bright_yellow")
    if compact_level == 0:
        table.add_column("Score", justify="center", min_width=12)
        table.add_column("Ready", justify="right")
        table.add_column("RS", justify="right")
    table.add_column("1h", justify="right", min_width=10)
    table.add_column("Vol1h", justify="right")
    table.add_column("Tx1h", justify="right")
    table.add_column("Holders", justify="right")
    table.add_column("Age", justify="right")

    for rank, candidate in enumerate(candidates[:limit], start=1):
        p = candidate.pair
        row: list[object] = [
            _rank_badge(rank),
            _move_text(key=candidate.key, rank=rank, previous_ranks=previous_ranks),
        ]
        if show_chain:
            row.append(_chain_text(p.chain_id))
        row.append(Text(_safe_text(p.base_symbol), style="bold bright_yellow"))
        if compact_level == 0:
            row.extend([
                _score_gauge(candidate.score),
                Text(f"{candidate.analytics.breakout_readiness:.0f}", style="bright_magenta"),
                Text(f"{candidate.analytics.relative_strength:+.1f}", style="white"),
                _momentum_text(p.price_change_h1),
                _vol_heat(p.volume_h1),
                str(p.txns_h1),
                holders_text(p.holders_count),
                _age_badge(p.age_hours),
            ])
        else:
            row.extend([
                _momentum_text(p.price_change_h1),
                _vol_heat(p.volume_h1),
                str(p.txns_h1),
                holders_text(p.holders_count),
                _age_badge(p.age_hours),
            ])
        table.add_row(*row)

    if not candidates:
        fallback = ["-"] * len(table.columns)
        fallback[3 if show_chain else 2] = "No movers yet"
        table.add_row(*fallback)
    return table


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Search results
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def render_search_table(pairs: list[PairSnapshot]) -> Table:
    compact = _compact_level() >= 1
    table = Table(
        title=f"[bold bright_cyan]{_safe_text(DIAMOND)} Search Results[/bold bright_cyan]",
        box=box.HEAVY,
        header_style="bold bright_white",
        row_styles=["", "dim"],
        border_style="bright_blue",
        title_style="",
    )
    table.add_column("Chain", min_width=6)
    table.add_column("Token", style="bold bright_yellow")
    table.add_column("Price", justify="right")
    table.add_column("Vol24", justify="right")
    table.add_column("Tx1h", justify="right")
    table.add_column("Liq", justify="right")
    table.add_column("Holders", justify="right")
    table.add_column("1h", justify="right", min_width=10)
    if not compact:
        table.add_column("Pair", style="dim white")

    for pair in pairs:
        if pair.price_usd >= 0.01:
            price = f"${pair.price_usd:,.4f}"
        else:
            price = f"${pair.price_usd:,.6f}"
        table.add_row(
            _chain_text(pair.chain_id),
            Text(_safe_text(pair.base_symbol), style="bold bright_yellow"),
            price,
            _vol_heat(pair.volume_h24),
            str(pair.txns_h1),
            fmt_usd(pair.liquidity_usd),
            holders_text(pair.holders_count),
            _momentum_text(pair.price_change_h1),
            *((_safe_text(pair.pair_address),) if not compact else ()),
        )
    if not pairs:
        cols = len(table.columns)
        fallback = ["-"] * cols
        fallback[1] = "No matches"
        table.add_row(*fallback)
    return table


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Pair detail
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def render_pair_detail(pair: PairSnapshot, boost_total: float = 0.0, boost_count: int = 0) -> Panel:
    mcap = pair.market_cap if pair.market_cap > 0 else pair.fdv
    chain_style = CHAIN_STYLES.get(pair.chain_id, "cyan")
    chain_lbl = CHAIN_LABEL.get(pair.chain_id, pair.chain_id.upper()[:4])

    content = Text()

    # Token identity
    content.append(f"{_safe_text(pair.base_name)} ", style="bold bright_white")
    content.append(f"({_safe_text(pair.base_symbol)})", style="bold bright_yellow")
    content.append("  on  ", style="dim")
    content.append(_safe_text(DOT), style=f"bold {chain_style}")
    content.append(f" {_safe_text(chain_lbl)}", style=chain_style)
    content.append(f" / {_safe_text(pair.dex_id)}\n", style="dim cyan")
    content.append("Pair: ", style="dim")
    content.append(f"{_safe_text(pair.pair_address)}\n", style="dim white")

    # Separator
    content.append(_safe_text(SEPARATOR * 48) + "\n", style="dim bright_blue")

    # Price section
    content.append("Price   ", style="dim")
    content.append(fmt_price(pair.price_usd), style="bold bright_white")
    content.append("\n")
    content.append("  1h    ", style="dim")
    content.append_text(_momentum_text(pair.price_change_h1))
    content.append("    24h   ", style="dim")
    content.append_text(_momentum_text(pair.price_change_h24))
    content.append("\n")

    # Separator
    content.append(_safe_text(SEPARATOR * 48) + "\n", style="dim bright_blue")

    # Volume section
    content.append("Volume\n", style="bold bright_white")
    content.append("  24h   ", style="dim")
    content.append_text(_vol_heat(pair.volume_h24))
    content.append("    6h  ", style="dim")
    content.append_text(_vol_heat(pair.volume_h6))
    content.append("    1h  ", style="dim")
    content.append_text(_vol_heat(pair.volume_h1))
    content.append("\n")

    # Transaction section
    content.append("Txns\n", style="bold bright_white")
    content.append(f"  1h    {pair.txns_h1}", style="white")
    content.append(f"  (B{pair.buys_h1}/S{pair.sells_h1})", style="dim")
    content.append(f"    24h   {pair.txns_h24}", style="white")
    content.append(f"  (B{pair.buys_h24}/S{pair.sells_h24})\n", style="dim")

    # Flow
    content.append("Flow    ", style="dim")
    content.append_text(_flow_meter(pair.buys_h1, pair.sells_h1, width=16))
    content.append("\n")

    # Separator
    content.append(_safe_text(SEPARATOR * 48) + "\n", style="dim bright_blue")

    # Liquidity & market cap
    content.append("Liq     ", style="dim")
    content.append(fmt_usd(pair.liquidity_usd), style="bold bright_green")
    content.append("    MCap/FDV  ", style="dim")
    content.append(fmt_usd(mcap), style="white")
    content.append("\n")

    # Holders
    content.append("Holders ", style="dim")
    content.append_text(_holders_gauge(pair.holders_count))
    if pair.holders_source:
        content.append(f"  ({pair.holders_source})", style="dim")
    content.append("\n")

    # Boosts
    if boost_total or boost_count:
        content.append("Boosts  ", style="dim")
        content.append(f"total={boost_total:.0f}  count={boost_count}\n", style="bright_yellow")

    # Link
    if pair.pair_url:
        content.append("\n")
        content.append("Dexscreener  ", style="dim")
        content.append(_safe_text(pair.pair_url), style="bright_blue underline")

    return Panel(
        content,
        title=f"[bold bright_cyan]{_safe_text(DIAMOND)} Pair Insight[/bold bright_cyan]",
        border_style="bright_blue",
        box=box.HEAVY,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Distribution panel
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def render_distribution_panel(candidate: HotTokenCandidate) -> Panel:
    heuristics = build_distribution_heuristics(candidate)
    txt = Text()

    if candidate.pair.holders_count is not None:
        txt.append("Observed holders  ", style="dim")
        txt.append_text(_holders_gauge(candidate.pair.holders_count))
        if candidate.pair.holders_source:
            txt.append(f"  ({candidate.pair.holders_source})", style="dim")
        txt.append("\n")
    else:
        txt.append("Holder count unavailable for this token/chain via public adapters.\n", style="bold yellow")

    txt.append(_safe_text(SEPARATOR * 40) + "\n", style="dim magenta")
    txt.append("Market-structure concentration signals\n", style="bold bright_white")

    # Liquidity to market cap
    liq_to_cap = heuristics["liquidity_to_market_cap"]
    txt.append(f"  {_safe_text(DOT)} liq/mcap       ", style="dim magenta")
    liq_val = float(liq_to_cap) if isinstance(liq_to_cap, (int, float)) else 0.0
    liq_style = "bold bright_green" if liq_val >= 0.1 else "yellow" if liq_val >= 0.03 else "bright_red"
    txt.append(f"{liq_to_cap}\n", style=liq_style)

    # Volume to liquidity
    vol_to_liq = heuristics["volume_to_liquidity_24h"]
    txt.append(f"  {_safe_text(DOT)} vol/liq 24h    ", style="dim magenta")
    vol_val = float(vol_to_liq) if isinstance(vol_to_liq, (int, float)) else 0.0
    vol_style = "bright_red" if vol_val > 5 else "yellow" if vol_val > 2 else "bright_green"
    txt.append(f"{vol_to_liq}\n", style=vol_style)

    # Buy/sell imbalance
    imbalance = heuristics["buy_sell_imbalance_1h"]
    txt.append(f"  {_safe_text(DOT)} buy/sell 1h    ", style="dim magenta")
    imb_val = float(imbalance) if isinstance(imbalance, (int, float)) else 0.0
    imb_style = "bold bright_green" if imb_val > 0.2 else "bright_red" if imb_val < -0.2 else "white"
    txt.append(f"{imbalance}\n", style=imb_style)

    # Status
    status = str(heuristics["status"])
    txt.append(f"  {_safe_text(DOT)} status         ", style="dim magenta")
    status_style = (
        "bold bright_green" if status == "balanced"
        else "bold bright_red" if status == "concentrated-liquidity"
        else "bold bright_yellow"
    )
    txt.append(status, style=status_style)

    return Panel(
        txt,
        title=f"[bold bright_magenta]{_safe_text(DIAMOND)} Distribution Proxy[/bold bright_magenta]",
        border_style="magenta",
        box=box.HEAVY,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chain heat table
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def render_chain_heat_table(candidates: list[HotTokenCandidate]) -> Table:
    table = Table(
        title=f"[bold bright_cyan]{_safe_text(DIAMOND)} Chain Heat[/bold bright_cyan]",
        box=box.HEAVY,
        expand=True,
        row_styles=["", "dim"],
        border_style="bright_blue",
        header_style="bold bright_white",
        title_style="",
    )
    table.add_column("Chain", min_width=6)
    table.add_column("Tokens", justify="right")
    table.add_column("Avg 1h", justify="right", min_width=10)
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

        # Heat bar for token count
        heat_bar_w = min(count, 10)
        heat_txt = Text()
        heat_txt.append(_safe_text(BAR_FILL * heat_bar_w), style="bright_cyan")
        heat_txt.append(f" {count}", style="bold bright_white")

        table.add_row(
            _chain_text(chain),
            heat_txt,
            _momentum_text(avg_h1),
            _vol_heat(data["vol"]),
            str(int(data["txns"])),
        )
    if not agg:
        table.add_row("-", "0", "0%", "$0", "0")
    return table


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Flow summary panel
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def render_flow_panel(candidates: list[HotTokenCandidate]) -> Panel:
    if not candidates:
        return Panel(
            "No candidates in current filter set.",
            title=f"[bold bright_cyan]{_safe_text(DIAMOND)} Flow Summary[/bold bright_cyan]",
            border_style="yellow",
            box=box.HEAVY,
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

    regime = (
        "trend-up" if avg_h1 > 10 and avg_imbalance > 0
        else "trend-down" if avg_imbalance < -0.2
        else "mixed"
    )

    text = Text()

    # Volume bar
    text.append("24h Volume    ", style="dim")
    text.append_text(_vol_heat(total_vol))
    text.append("\n")

    # Liquidity
    text.append("Liquidity     ", style="dim")
    text.append(f"{fmt_usd(total_liq)}", style="bold bright_green")
    text.append("\n")

    # Average 1h with arrow
    text.append("Avg 1h Move   ", style="dim")
    text.append_text(_momentum_text(avg_h1))
    text.append("\n")

    # Imbalance with visual bar
    text.append("Buy/Sell Imb  ", style="dim")
    imb_style = (
        "bold bright_green" if avg_imbalance > 0.1
        else "bold bright_red" if avg_imbalance < -0.1
        else "white"
    )
    # Visual imbalance bar
    bar_w = 10
    center = bar_w // 2
    fill_pos = int(round(abs(avg_imbalance) * center))
    fill_pos = min(fill_pos, center)
    if avg_imbalance >= 0:
        bar_txt = Text()
        bar_txt.append(_safe_text(BAR_EMPTY * center), style="dim")
        bar_txt.append(_safe_text(BAR_FILL * fill_pos), style="bright_green")
        bar_txt.append(_safe_text(BAR_EMPTY * (center - fill_pos)), style="dim")
    else:
        bar_txt = Text()
        bar_txt.append(_safe_text(BAR_EMPTY * (center - fill_pos)), style="dim")
        bar_txt.append(_safe_text(BAR_FILL * fill_pos), style="bright_red")
        bar_txt.append(_safe_text(BAR_EMPTY * center), style="dim")
    text.append_text(bar_txt)
    text.append(f" {avg_imbalance:+.2f}\n", style=imb_style)

    text.append(_safe_text(SEPARATOR * 36) + "\n", style="dim bright_blue")

    # Regime
    text.append("Regime        ", style="dim")
    regime_style = (
        "bold bright_green" if regime == "trend-up"
        else "bold bright_red" if regime == "trend-down"
        else "bold bright_yellow"
    )
    text.append(f"{regime}\n", style=regime_style)

    # Flags with dot indicators
    flag_style = "bold magenta"
    if "sell-pressure" in market_flags:
        flag_style = "bold bright_red"
    elif "balanced" in market_flags:
        flag_style = "bold bright_green"

    text.append("Flags         ", style="dim")
    for i, flag in enumerate(market_flags):
        if i > 0:
            text.append("  ", style="")
        text.append(_safe_text(f"{DOT} "), style=flag_style)
        text.append(flag, style=flag_style)

    return Panel(
        text,
        title=f"[bold bright_cyan]{_safe_text(DIAMOND)} Flow Summary[/bold bright_cyan]",
        border_style="bright_blue",
        box=box.HEAVY,
    )
