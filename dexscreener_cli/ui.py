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
# Dexscreener-inspired color palette
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# Core palette - dark backgrounds, muted borders, punchy data colors
C_BORDER = "#3a3d4a"           # dark grey border (subtle, not blue)
C_BORDER_DIM = "#2a2d3a"       # even subtler border
C_TITLE = "#e5e7eb"            # off-white for titles
C_LABEL = "#6b7280"            # medium grey for labels
C_DIM = "#4b5563"              # dark grey for dim text
C_TEXT = "#d1d5db"             # light grey primary text
C_GREEN = "#4ade80"            # dexscreener lime green (positive)
C_GREEN_BRIGHT = "#22c55e"     # brighter green for strong positive
C_RED = "#f87171"              # dexscreener coral red (negative)
C_RED_BRIGHT = "#ef4444"       # brighter red for strong negative
C_GOLD = "#fbbf24"             # amber/gold for token symbols, highlights
C_AMBER = "#f59e0b"            # deeper amber
C_BLUE = "#60a5fa"             # muted blue accent (links, chain)
C_CYAN = "#67e8f9"             # light cyan (freshness)
C_PURPLE = "#a78bfa"           # muted purple (signals)
C_WHITE = "#f9fafb"            # near-white for emphasis

CHAIN_STYLES = {
    "solana": C_GREEN,
    "base": C_BLUE,
    "ethereum": "#9ca3af",
    "bsc": C_GOLD,
    "arbitrum": C_CYAN,
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
        return f"bold {C_GREEN_BRIGHT}"
    if value >= 12:
        return f"bold {C_GREEN}"
    if value > 0:
        return C_GREEN
    if value <= -30:
        return f"bold {C_RED_BRIGHT}"
    if value <= -12:
        return f"bold {C_RED}"
    if value < 0:
        return C_RED
    return C_DIM


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
    style = CHAIN_STYLES.get(chain_id, C_LABEL)
    txt = Text()
    txt.append(_safe_text(DOT), style=f"bold {style}")
    txt.append(f" {_safe_text(label)}", style=style)
    return txt


def _score_style(score: float) -> str:
    if score >= 85:
        return f"bold {C_GREEN}"
    if score >= 75:
        return f"bold {C_GOLD}"
    return f"bold {C_TEXT}"


def holders_text(value: int | None) -> Text:
    if value is None:
        return Text("n/a", style=C_DIM)
    if value >= 25_000:
        return Text(fmt_holders(value), style=f"bold {C_GREEN}")
    if value >= 5_000:
        return Text(fmt_holders(value), style=C_GREEN)
    if value >= 1_000:
        return Text(fmt_holders(value), style=C_GOLD)
    return Text(fmt_holders(value), style=C_RED)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Visual gauges & meters
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _score_gauge(score: float, width: int = 8) -> Text:
    """Visual gauge bar for scores: ████████░░ 82"""
    filled = int(round((min(score, 100) / 100) * width))
    empty = width - filled
    if score >= 85:
        fill_style = C_GREEN
    elif score >= 75:
        fill_style = C_GOLD
    elif score >= 60:
        fill_style = C_TEXT
    else:
        fill_style = C_RED
    txt = Text()
    txt.append(_safe_text(BAR_FILL * filled), style=fill_style)
    txt.append(_safe_text(BAR_EMPTY * empty), style=C_DIM)
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
        txt.append(f"  {fmt_pct(value)}", style=C_DIM)
    return txt


def _vol_heat(value: float, *, mini_bar: bool = False) -> Text:
    """Volume with heat-level styling and optional mini-bar prefix."""
    if value >= 10_000_000:
        style = f"bold {C_WHITE}"
        tier = 3
    elif value >= 1_000_000:
        style = C_TEXT
        tier = 2
    elif value >= 100_000:
        style = C_LABEL
        tier = 1
    else:
        style = C_DIM
        tier = 0
    txt = Text()
    if mini_bar:
        bar = BAR_FILL * tier + BAR_EMPTY * (3 - tier)
        txt.append(_safe_text(bar), style=style)
        txt.append(" ", style="")
    txt.append(fmt_usd(value), style=style)
    return txt


def _age_badge(hours: float | None) -> Text:
    """Styled age with freshness indicator."""
    label = _age_label(hours)
    if hours is None:
        return Text(label, style=C_DIM)
    if hours < 1:
        return Text(_safe_text(f"{DOT} {label}"), style=f"bold {C_CYAN}")
    if hours < 6:
        return Text(label, style=C_CYAN)
    if hours < 24:
        return Text(label, style=C_LABEL)
    if hours < 72:
        return Text(label, style=C_TEXT)
    return Text(label, style=C_DIM)


def _rank_badge(rank: int) -> Text:
    """Medal-styled rank for top positions."""
    if rank == 1:
        return Text(_safe_text(f"{DIAMOND} {rank}"), style=f"bold {C_GOLD}")
    if rank == 2:
        return Text(_safe_text(f"{DIAMOND} {rank}"), style=f"bold {C_TEXT}")
    if rank == 3:
        return Text(_safe_text(f"{DIAMOND} {rank}"), style=f"bold {C_LABEL}")
    return Text(str(rank), style=C_LABEL)


def _flow_meter(buys: int, sells: int, width: int = 12) -> Text:
    """Block-character flow meter with colored buy/sell segments."""
    total = max(buys + sells, 1)
    buy_ratio = max(0.0, min(1.0, buys / total))
    buy_width = int(round(width * buy_ratio))
    sell_width = max(width - buy_width, 0)
    buy_pct = buy_ratio * 100
    sell_pct = (1 - buy_ratio) * 100

    if buy_ratio >= 0.65:
        buy_style = f"bold {C_GREEN}"
    else:
        buy_style = C_GREEN

    if buy_ratio <= 0.35:
        sell_style = f"bold {C_RED}"
    elif buy_ratio <= 0.5:
        sell_style = C_RED
    else:
        sell_style = C_RED

    meter = Text()
    meter.append(_safe_text(BAR_FILL * buy_width), style=buy_style)
    meter.append(_safe_text(BAR_FILL * sell_width), style=sell_style)
    meter.append(f" {buy_pct:>2.0f}/{sell_pct:>2.0f}", style=C_DIM)
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
    style = f"bold {C_GREEN}" if rising else C_GOLD
    return Text(_safe_text(spark), style=style)


def _signal_style(tags: list[str], discovery: str) -> str:
    normalized = {t.lower() for t in tags}
    if "transaction-spike" in normalized or "momentum" in normalized:
        return f"bold {C_PURPLE}"
    if "buy-pressure" in normalized:
        return C_PURPLE
    if "fresh-pair" in normalized:
        return C_CYAN
    if discovery == "boost":
        return C_GOLD
    return C_TEXT


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
        txt.append(_safe_text(f"{DOT} "), style=f"bold {C_PURPLE}")
    elif "buy-pressure" in normalized:
        txt.append(_safe_text(f"{DOT} "), style=C_PURPLE)
    elif "fresh-pair" in normalized:
        txt.append(_safe_text(f"{DOT} "), style=C_CYAN)
    elif discovery == "boost":
        txt.append(_safe_text(f"{DOT} "), style=C_GOLD)
    txt.append(_safe_text(signal), style=style)
    return txt


def _liq_bar(value: float) -> Text:
    """Liquidity with mini tier bar prefix."""
    tier_bar_w = 3
    if value >= 500_000:
        bar = BAR_FILL * tier_bar_w
        style = f"bold {C_GREEN}"
    elif value >= 100_000:
        bar = BAR_FILL * 2 + BAR_EMPTY * 1
        style = C_GREEN
    elif value >= 30_000:
        bar = BAR_FILL * 1 + BAR_EMPTY * 2
        style = C_GOLD
    else:
        bar = BAR_EMPTY * tier_bar_w
        style = C_RED
    txt = Text()
    txt.append(_safe_text(bar), style=style)
    txt.append(f" {fmt_usd(value)}", style=style)
    return txt


def _holders_gauge(value: int | None) -> Text:
    """Holder count with mini visual tier bar."""
    if value is None:
        return Text("n/a", style=C_DIM)
    tier_bar_w = 3
    if value >= 25_000:
        bar = BAR_FILL * tier_bar_w
        style = f"bold {C_GREEN}"
    elif value >= 5_000:
        bar = BAR_FILL * 2 + BAR_EMPTY * 1
        style = C_GREEN
    elif value >= 1_000:
        bar = BAR_FILL * 1 + BAR_EMPTY * 2
        style = C_GOLD
    else:
        bar = BAR_EMPTY * tier_bar_w
        style = C_RED
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
    content.append("DEX SCANNER", style=f"bold {C_WHITE}")
    content.append("\n")
    content.append("Live Signal Terminal", style=C_LABEL)
    content.append(_safe_text(f"  {DOT}  "), style=C_BORDER)
    content.append(now, style=C_DIM)

    return Panel(
        content,
        border_style=C_BORDER,
        box=box.HEAVY,
        padding=(0, 1),
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Scan summary (Performance KPI panel)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def render_scan_summary(candidates: list[HotTokenCandidate]) -> Panel:
    """Performance-style KPI grid inspired by bankroll tracker reference."""
    if not candidates:
        return Panel(
            Text("Waiting for scan data...", style=C_DIM),
            title=f"[bold {C_TEXT}]Performance[/bold {C_TEXT}]",
            border_style=C_BORDER,
            box=box.HEAVY,
        )

    total_vol = sum(c.pair.volume_h24 for c in candidates)
    total_liq = sum(c.pair.liquidity_usd for c in candidates)
    avg_score = sum(c.score for c in candidates) / len(candidates)
    avg_h1 = sum(c.pair.price_change_h1 for c in candidates) / len(candidates)

    # Top mover
    top = max(candidates, key=lambda c: abs(c.pair.price_change_h1))

    # Buy pressure aggregate
    total_buys = sum(c.pair.buys_h1 for c in candidates)
    total_sells = sum(c.pair.sells_h1 for c in candidates)
    total_txns = max(total_buys + total_sells, 1)
    buy_ratio = total_buys / total_txns

    # Hot chain
    chain_counts: dict[str, int] = defaultdict(int)
    for c in candidates:
        chain_counts[c.pair.chain_id] += 1
    hot_chain = max(chain_counts, key=chain_counts.get)  # type: ignore[arg-type]

    # Build two-column grid
    grid = Table(
        show_header=False,
        box=None,
        padding=(0, 2),
        expand=True,
        show_edge=False,
    )
    grid.add_column("lbl1", style=C_LABEL, min_width=14)
    grid.add_column("val1", min_width=18)
    grid.add_column("sep", width=1, style=C_BORDER)
    grid.add_column("lbl2", style=C_LABEL, min_width=14)
    grid.add_column("val2", min_width=18)

    # Row 1: Tokens Found | Top Mover
    tokens_txt = Text()
    count = len(candidates)
    if count >= 15:
        tokens_txt.append(str(count), style=f"bold {C_GREEN}")
    elif count >= 5:
        tokens_txt.append(str(count), style=f"bold {C_GOLD}")
    else:
        tokens_txt.append(str(count), style=f"bold {C_RED}")

    top_txt = Text()
    top_txt.append(_safe_text(top.pair.base_symbol), style=f"bold {C_GOLD}")
    top_txt.append("  ", style="")
    top_txt.append_text(_momentum_text(top.pair.price_change_h1))

    grid.add_row("Tokens Found", tokens_txt, _safe_text(VLINE), "Top Mover 1h", top_txt)

    # Row 2: Total 24h Vol | Buy Pressure
    buy_txt = Text()
    buy_bar_w = 8
    buy_filled = int(round(buy_ratio * buy_bar_w))
    sell_filled = buy_bar_w - buy_filled
    buy_style = C_GREEN if buy_ratio >= 0.55 else C_GREEN if buy_ratio >= 0.45 else C_RED
    sell_style = C_RED
    buy_txt.append(_safe_text(BAR_FILL * buy_filled), style=buy_style)
    buy_txt.append(_safe_text(BAR_FILL * sell_filled), style=sell_style)
    buy_txt.append(f" {buy_ratio * 100:.0f}%", style=f"bold {buy_style}")

    grid.add_row("Total 24h Vol", _vol_heat(total_vol), _safe_text(VLINE), "Buy Pressure", buy_txt)

    # Row 3: Total Liquidity | Hot Chain
    liq_txt = Text(fmt_usd(total_liq), style=f"bold {C_GREEN}")
    hot_chain_txt = _chain_text(hot_chain)
    chain_count_txt = Text()
    chain_count_txt.append_text(hot_chain_txt)
    chain_count_txt.append(f"  ({chain_counts[hot_chain]} tokens)", style=C_DIM)

    grid.add_row("Total Liquidity", liq_txt, _safe_text(VLINE), "Hot Chain", chain_count_txt)

    # Row 4: Avg Score | Avg 1h Move
    grid.add_row("Avg Score", _score_gauge(avg_score, width=8), _safe_text(VLINE), "Avg 1h Move", _momentum_text(avg_h1))

    return Panel(
        grid,
        title=f"[bold {C_TEXT}]Performance[/bold {C_TEXT}]",
        border_style=C_BORDER,
        box=box.HEAVY,
        padding=(0, 1),
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Status footer
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def render_status_footer(
    *,
    interval: float | None = None,
    chains: tuple[str, ...] = (),
    profile: str = "",
) -> Panel:
    """Styled status footer with scan metadata."""
    now = datetime.now(UTC).strftime("%H:%M:%S")
    txt = Text()

    # Left: profile + chains
    if profile:
        txt.append(_safe_text(f"{DOT} "), style=C_GREEN)
        txt.append(profile.upper(), style=f"bold {C_GREEN}")
        txt.append("  ", style="")
    if chains:
        chain_labels = ", ".join(CHAIN_LABEL.get(c, c.upper()[:4]) for c in chains)
        txt.append(_safe_text(f"{VLINE} "), style=C_BORDER)
        txt.append(chain_labels, style=C_LABEL)

    # Center: timestamp
    txt.append(_safe_text(f"  {VLINE}  "), style=C_BORDER)
    txt.append(now, style=C_DIM)

    # Right: interval or static
    txt.append(_safe_text(f"  {VLINE}  "), style=C_BORDER)
    if interval is not None:
        txt.append(f"refresh {interval:.0f}s", style=C_DIM)
        txt.append(_safe_text(f"  {DOT}  "), style=C_BORDER)
        txt.append("Ctrl+C to exit", style=C_GOLD)
    else:
        txt.append("one-shot scan", style=C_DIM)

    return Panel(
        txt,
        border_style=C_BORDER_DIM,
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
            f"[bold {C_TEXT}]{_safe_text(DIAMOND)} Hot Runner Scan[/bold {C_TEXT}] "
            f"[{C_LABEL}]{chain_labels}[/{C_LABEL}]"
        )
    else:
        title = (
            f"[bold {C_TEXT}]{_safe_text(DIAMOND)} Hot Runner Scan[/bold {C_TEXT}]  "
            f"[{C_LABEL}]{chain_labels}[/{C_LABEL}]  "
            f"[{C_DIM}]top={limit}  liq>={fmt_usd(min_liquidity_usd)}  "
            f"vol>={fmt_usd(min_volume_h24_usd)}  tx>={min_txns_h1}[/{C_DIM}]"
        )

    table = Table(
        title=title,
        box=box.SIMPLE_HEAVY,
        header_style=f"bold {C_TEXT}",
        show_edge=True,
        row_styles=["", "on #1e2029"],
        border_style=C_BORDER,
        title_style="",
    )
    table.add_column("#", justify="right", style=f"bold {C_TEXT}", width=3)
    table.add_column("Chain", min_width=5)
    table.add_column("Token", style=f"bold {C_GOLD}", min_width=8)
    table.add_column("Score", justify="right", width=5)
    table.add_column("1h", justify="right", min_width=10)
    table.add_column("24h", justify="right", min_width=10)
    table.add_column("24h Vol", justify="right", min_width=9)
    table.add_column("Txns", justify="right")
    table.add_column("Liquidity", justify="right", min_width=9)
    table.add_column("Holders", justify="right")
    table.add_column("Age", justify="right")
    if not compact:
        table.add_column("MCap", justify="right", min_width=9)

    for i, candidate in enumerate(candidates, start=1):
        p = candidate.pair
        h1 = _momentum_text(p.price_change_h1)
        h24 = _momentum_text(p.price_change_h24)

        # Score as plain colored number
        sc = candidate.score
        if sc >= 80:
            score_text = Text(f"{sc:.0f}", style=f"bold {C_GREEN_BRIGHT}")
        elif sc >= 65:
            score_text = Text(f"{sc:.0f}", style=f"bold {C_GOLD}")
        elif sc >= 50:
            score_text = Text(f"{sc:.0f}", style=C_TEXT)
        else:
            score_text = Text(f"{sc:.0f}", style=C_DIM)

        if compact:
            table.add_row(
                str(i),
                _chain_text(p.chain_id),
                Text(_safe_text(p.base_symbol), style=f"bold {C_GOLD}"),
                score_text,
                h1,
                h24,
                fmt_usd(p.volume_h24),
                str(p.txns_h1),
                fmt_usd(p.liquidity_usd),
                holders_text(p.holders_count),
                _age_badge(p.age_hours),
            )
        else:
            table.add_row(
                str(i),
                _chain_text(p.chain_id),
                Text(_safe_text(p.base_symbol), style=f"bold {C_GOLD}"),
                score_text,
                h1,
                h24,
                fmt_usd(p.volume_h24),
                str(p.txns_h1),
                fmt_usd(p.liquidity_usd),
                holders_text(p.holders_count),
                _age_badge(p.age_hours),
                fmt_usd(p.market_cap if p.market_cap > 0 else p.fdv),
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
    chain_style = CHAIN_STYLES.get(chain, C_LABEL)

    txt = Text()
    txt.append(_safe_text(f"{DOT} "), style=f"bold {chain_style}")
    txt.append(f"Chain: {chain_lbl}", style=f"bold {chain_style}")
    txt.append(_safe_text(f"  {VLINE}  "), style=C_BORDER)
    txt.append(f"Window: <={max_age_hours:.0f}h", style=C_LABEL)
    txt.append(_safe_text(f"  {VLINE}  "), style=C_BORDER)
    txt.append(f"Target: top {limit}\n", style=C_LABEL)
    txt.append(_safe_text(SEPARATOR * 36) + "\n", style=C_BORDER)

    if not candidates:
        txt.append("No fresh runners found with this filter set.", style=C_GOLD)
        return Panel(
            txt,
            title=f"[bold {C_TEXT}]{_safe_text(DIAMOND)} New Runner Radar[/bold {C_TEXT}]",
            border_style=C_BORDER,
            box=box.HEAVY,
        )

    for i, candidate in enumerate(candidates[:3], start=1):
        p = candidate.pair
        age = _age_label(p.age_hours)

        txt.append_text(_rank_badge(i))
        txt.append("  ", style="")
        txt.append(f"{_safe_text(p.base_symbol)} ", style=f"bold {C_GOLD}")
        txt.append_text(_momentum_text(p.price_change_h1))
        txt.append(_safe_text(f" {VLINE} "), style=C_BORDER)
        txt.append("score ", style=C_LABEL)
        txt.append(f"{candidate.score:.1f}", style=_score_style(candidate.score))
        txt.append(_safe_text(f" {VLINE} "), style=C_BORDER)
        txt.append("ready ", style=C_LABEL)
        txt.append(f"{candidate.analytics.breakout_readiness:.0f}", style=C_PURPLE)
        txt.append(_safe_text(f" {VLINE} "), style=C_BORDER)
        txt.append(f"age {age}\n", style=C_LABEL)

    return Panel(
        txt,
        title=f"[bold {C_TEXT}]{_safe_text(DIAMOND)} New Runner Radar[/bold {C_TEXT}]",
        border_style=C_BORDER,
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
            f"[bold {C_TEXT}]{_safe_text(DIAMOND)} Best New Runners[/bold {C_TEXT}] "
            f"[{C_LABEL}]{chain_lbl}[/{C_LABEL}]"
        )
    else:
        title = (
            f"[bold {C_TEXT}]{_safe_text(DIAMOND)} Best New Runners[/bold {C_TEXT}]  "
            f"[{C_LABEL}]{chain_lbl}[/{C_LABEL}]  "
            f"[{C_DIM}]top={limit}  age<={max_age_hours:.0f}h[/{C_DIM}]"
        )

    table = Table(
        title=title,
        box=box.SIMPLE_HEAVY,
        header_style=f"bold {C_TEXT}",
        row_styles=["", "on #1e2029"],
        border_style=C_BORDER,
        title_style="",
    )
    table.add_column(_safe_text(f" {DIAMOND}"), justify="right", width=4)
    if show_chain:
        table.add_column("Ch", min_width=6)
    table.add_column("Token", style=f"bold {C_GOLD}", min_width=8)
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
        token_style = f"bold black on {C_GREEN}" if is_selected else f"bold {C_GOLD}"
        score_style_sel = f"bold black on {C_GREEN}" if is_selected else _score_style(candidate.score)

        rs_style = (
            f"bold {C_GREEN}" if a.relative_strength >= 8
            else f"bold {C_RED}" if a.relative_strength <= -8
            else C_TEXT
        )
        readiness_style = (
            f"bold {C_GREEN}" if a.breakout_readiness >= 70
            else C_GOLD if a.breakout_readiness >= 55
            else C_DIM
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
    rank_borders = [C_GOLD, C_TEXT, C_LABEL]
    rank_titles = [
        f"[bold {C_GOLD}]{_safe_text(DIAMOND)} #1  GOLD[/bold {C_GOLD}]",
        f"[bold {C_TEXT}]{_safe_text(DIAMOND)} #2  SILVER[/bold {C_TEXT}]",
        f"[bold {C_LABEL}]{_safe_text(DIAMOND)} #3  BRONZE[/bold {C_LABEL}]",
    ]

    cards: list[Panel] = []
    for rank in range(1, 4):
        if rank <= len(candidates):
            candidate = candidates[rank - 1]
            p = candidate.pair
            border = rank_borders[rank - 1]
            if pulse and rank == 1:
                border = f"bold {C_GREEN}"

            txt = Text()
            # Token name
            txt.append(f"{_safe_text(p.base_symbol)}\n", style=f"bold {C_WHITE}")

            # Score gauge
            txt.append("Score  ", style=C_LABEL)
            txt.append_text(_score_gauge(candidate.score, width=10))
            txt.append("\n")

            # Ready & RS
            ready = candidate.analytics.breakout_readiness
            ready_style = (
                f"bold {C_GREEN}" if ready >= 70
                else C_GOLD if ready >= 55
                else C_DIM
            )
            txt.append("Ready  ", style=C_LABEL)
            txt.append(f"{ready:.0f}", style=ready_style)
            rs = candidate.analytics.relative_strength
            txt.append(_safe_text(f"    {VLINE}    "), style=C_BORDER)
            txt.append("RS ", style=C_LABEL)
            txt.append(f"{rs:+.1f}\n", style=f"bold {C_GREEN}" if rs >= 5 else f"bold {C_RED}" if rs <= -5 else C_TEXT)

            # Separator
            txt.append(_safe_text(SEPARATOR * 22) + "\n", style=C_BORDER)

            # Holders
            txt.append("Holders  ", style=C_LABEL)
            txt.append_text(holders_text(p.holders_count))
            txt.append("\n")

            # 1h momentum
            txt.append("1h       ", style=C_LABEL)
            txt.append_text(_momentum_text(p.price_change_h1))
            txt.append("\n")

            # Volume
            txt.append("24h Vol  ", style=C_LABEL)
            txt.append_text(_vol_heat(p.volume_h24))
            txt.append("\n")

            # Flow
            txt.append("Flow     ", style=C_LABEL)
            txt.append_text(_flow_meter(p.buys_h1, p.sells_h1))
            txt.append("\n")

            # Age
            txt.append("Age      ", style=C_LABEL)
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
                Text(_safe_text(f"{BAR_EMPTY * 8} Waiting for data..."), style=C_DIM),
                title=f"[{C_DIM}]{_safe_text(DIAMOND)} #{rank}[/{C_DIM}]",
                border_style=C_BORDER_DIM,
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
        return Text("new", style=f"bold {C_CYAN}")
    delta = prev - rank
    if delta > 0:
        return Text(_safe_text(f"{ARROW_UP}{delta}"), style=f"bold {C_GREEN}")
    if delta < 0:
        return Text(_safe_text(f"{ARROW_DOWN}{abs(delta)}"), style=f"bold {C_RED}")
    return Text("=", style=C_DIM)


def render_rank_movers_table(
    candidates: list[HotTokenCandidate],
    *,
    previous_ranks: dict[tuple[str, str], int],
    limit: int,
) -> Table:
    compact_level = _compact_level()
    show_chain = len({c.pair.chain_id for c in candidates}) > 1
    table = Table(
        title=f"[bold {C_TEXT}]{_safe_text(DIAMOND)} Rank Movers[/bold {C_TEXT}]",
        box=box.SIMPLE_HEAVY,
        header_style=f"bold {C_TEXT}",
        row_styles=["", "on #1e2029"],
        border_style=C_BORDER,
        title_style="",
    )
    table.add_column("Rank", justify="right", width=4)
    table.add_column("Move", justify="right")
    if show_chain:
        table.add_column("Ch", min_width=6)
    table.add_column("Token", style=f"bold {C_GOLD}")
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
        row.append(Text(_safe_text(p.base_symbol), style=f"bold {C_GOLD}"))
        if compact_level == 0:
            row.extend([
                _score_gauge(candidate.score),
                Text(f"{candidate.analytics.breakout_readiness:.0f}", style=C_PURPLE),
                Text(f"{candidate.analytics.relative_strength:+.1f}", style=C_TEXT),
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


def _trust_badge(pair: PairSnapshot) -> Text:
    """Heuristic trust indicator based on holders, liquidity, and volume."""
    holders = pair.holders_count
    liq = pair.liquidity_usd
    vol = pair.volume_h24
    txns = pair.txns_h24

    # Strong signals of legitimacy
    if holders is not None and holders >= 10_000 and liq >= 100_000:
        return Text(_safe_text(f"{DOT} looks legit"), style=f"bold {C_GREEN}")
    if holders is not None and holders >= 1_000 and liq >= 50_000 and txns >= 100:
        return Text(_safe_text(f"{DOT} likely ok"), style=C_GREEN)

    # Warning signals
    warnings: list[str] = []
    if liq < 5_000:
        warnings.append("low-liq")
    if holders is not None and holders < 50:
        warnings.append("few-hold")
    if vol < 100 and txns < 5:
        warnings.append("no-activity")
    if liq > 0 and vol > liq * 20:
        warnings.append("wash")

    if len(warnings) >= 2:
        return Text(_safe_text(f"{DOT} ") + ",".join(warnings[:2]), style=f"bold {C_RED}")
    if warnings:
        return Text(_safe_text(f"{DOT} ") + warnings[0], style=C_GOLD)

    # Neutral
    if holders is None and liq < 50_000:
        return Text(_safe_text(f"{DOT} unverified"), style=C_DIM)
    return Text(_safe_text(f"{DOT} ok"), style=C_TEXT)


def _truncate_addr(addr: str, length: int = 8) -> str:
    """Truncate address to first..last chars."""
    if len(addr) <= length * 2 + 2:
        return addr
    return f"{addr[:length]}..{addr[-length:]}"


def _addr_trust_style(pair: PairSnapshot) -> str:
    """Return white for legit-looking tokens, dim grey for others."""
    holders = pair.holders_count
    liq = pair.liquidity_usd
    txns = pair.txns_h24
    if holders is not None and holders >= 10_000 and liq >= 100_000:
        return C_WHITE
    if holders is not None and holders >= 1_000 and liq >= 50_000 and txns >= 100:
        return C_WHITE
    return C_DIM


def render_search_table(pairs: list[PairSnapshot]) -> Table:
    table = Table(
        title=f"[bold {C_TEXT}]{_safe_text(DIAMOND)} Search Results[/bold {C_TEXT}]",
        box=box.SIMPLE_HEAVY,
        header_style=f"bold {C_TEXT}",
        row_styles=["", "on #1e2029"],
        border_style=C_BORDER,
        title_style="",
    )
    table.add_column("Chain", min_width=5)
    table.add_column("Token", style=f"bold {C_GOLD}")
    table.add_column("Address", no_wrap=True)
    table.add_column("24h", justify="right", min_width=10)
    table.add_column("Vol 24h", justify="right")
    table.add_column("Liq", justify="right")
    table.add_column("Holders", justify="right")
    table.add_column("Age", justify="right")

    for pair in pairs:
        addr_style = _addr_trust_style(pair)
        table.add_row(
            _chain_text(pair.chain_id),
            Text(_safe_text(pair.base_symbol), style=f"bold {C_GOLD}"),
            Text(_safe_text(pair.base_address), style=addr_style, no_wrap=True),
            _momentum_text(pair.price_change_h24),
            fmt_usd(pair.volume_h24),
            fmt_usd(pair.liquidity_usd),
            holders_text(pair.holders_count),
            _age_badge(pair.age_hours),
        )
    if not pairs:
        cols = len(table.columns)
        fallback = ["-"] * cols
        fallback[1] = "No matches"
        table.add_row(*fallback)
    return table


def render_search_disclaimer() -> Panel:
    """Disclaimer footer for search results."""
    txt = Text()
    txt.append("Disclaimer: ", style=f"bold {C_GOLD}")
    txt.append(
        "Trust indicators are heuristic estimates only. "
        "Always DYOR - any token can be rugged or exploited. "
        "Do not blindly ape. We take no responsibility for accuracy.",
        style=C_DIM,
    )
    return Panel(txt, border_style=C_BORDER_DIM, box=box.HEAVY, padding=(0, 1))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Pair detail
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def render_inspect_view(
    pair: PairSnapshot,
    heuristics: dict[str, object] | None = None,
    *,
    boost_total: float = 0.0,
    boost_count: int = 0,
    extra_pairs: int = 0,
) -> Table:
    """Unified inspect view as a clean table — matches the style of other CLI views."""
    mcap = pair.market_cap if pair.market_cap > 0 else pair.fdv
    chain_style = CHAIN_STYLES.get(pair.chain_id, C_LABEL)
    chain_lbl = CHAIN_LABEL.get(pair.chain_id, pair.chain_id.upper()[:4])

    # Title row
    title = (
        f"[bold {C_WHITE}]{_safe_text(pair.base_name)}[/bold {C_WHITE}] "
        f"[bold {C_GOLD}]({_safe_text(pair.base_symbol)})[/bold {C_GOLD}]  "
        f"[{chain_style}]{_safe_text(chain_lbl)}[/{chain_style}]  "
        f"[{C_DIM}]{_safe_text(pair.dex_id)}[/{C_DIM}]"
    )

    table = Table(
        title=title,
        box=box.SIMPLE_HEAVY,
        header_style=f"bold {C_TEXT}",
        row_styles=["", "on #1e2029"],
        border_style=C_BORDER,
        title_style="",
        show_header=True,
    )
    table.add_column("Metric", style=f"bold {C_LABEL}", width=14, no_wrap=True)
    table.add_column("Value", ratio=1)

    # -- Token info --
    table.add_row("Token Addr", Text(_safe_text(pair.base_address), style=C_DIM))
    table.add_row("Pair Addr", Text(_safe_text(pair.pair_address), style=C_DIM))

    # -- Price --
    price_txt = Text()
    price_txt.append(fmt_price(pair.price_usd), style=f"bold {C_WHITE}")
    table.add_row("Price", price_txt)

    # 1h / 24h changes
    chg_txt = Text()
    chg_txt.append("1h ", style=C_LABEL)
    chg_txt.append_text(_momentum_text(pair.price_change_h1))
    chg_txt.append("    24h ", style=C_LABEL)
    chg_txt.append_text(_momentum_text(pair.price_change_h24))
    table.add_row("Change", chg_txt)

    # -- Volume --
    vol_txt = Text()
    vol_txt.append_text(_vol_heat(pair.volume_h24))
    vol_txt.append("    6h ", style=C_LABEL)
    vol_txt.append_text(_vol_heat(pair.volume_h6))
    vol_txt.append("    1h ", style=C_LABEL)
    vol_txt.append_text(_vol_heat(pair.volume_h1))
    table.add_row("Vol 24h", vol_txt)

    # -- Transactions --
    txn_txt = Text()
    txn_txt.append(f"{pair.txns_h1}", style=f"bold {C_TEXT}")
    txn_txt.append(f" (B{pair.buys_h1}/S{pair.sells_h1})", style=C_LABEL)
    txn_txt.append("    24h ", style=C_LABEL)
    txn_txt.append(f"{pair.txns_h24}", style=f"bold {C_TEXT}")
    txn_txt.append(f" (B{pair.buys_h24}/S{pair.sells_h24})", style=C_LABEL)
    table.add_row("Txns 1h", txn_txt)

    # -- Flow --
    table.add_row("Flow", _flow_meter(pair.buys_h1, pair.sells_h1, width=16))

    # -- Liquidity + MCap --
    liq_txt = Text()
    liq_txt.append(fmt_usd(pair.liquidity_usd), style=f"bold {C_GREEN}")
    liq_txt.append("    MCap/FDV ", style=C_LABEL)
    liq_txt.append(fmt_usd(mcap), style=C_TEXT)
    table.add_row("Liquidity", liq_txt)

    # -- Holders --
    holders_txt = Text()
    holders_txt.append_text(_holders_gauge(pair.holders_count))
    if pair.holders_source:
        holders_txt.append(f"  ({pair.holders_source})", style=C_DIM)
    table.add_row("Holders", holders_txt)

    # -- Boosts --
    if boost_total or boost_count:
        boost_txt = Text()
        boost_txt.append(f"{boost_total:.0f} total", style=C_GOLD)
        boost_txt.append(f"  {boost_count} boosts", style=C_LABEL)
        table.add_row("Boosts", boost_txt)

    # -- Distribution heuristics (if provided) --
    if heuristics:
        # Liq/MCap ratio
        liq_to_cap = heuristics.get("liquidity_to_market_cap", 0)
        liq_val = float(liq_to_cap) if isinstance(liq_to_cap, (int, float)) else 0.0
        liq_r_style = f"bold {C_GREEN}" if liq_val >= 0.1 else C_GOLD if liq_val >= 0.03 else C_RED
        table.add_row("Liq/MCap", Text(str(liq_to_cap), style=liq_r_style))

        # Vol/Liq ratio
        vol_to_liq = heuristics.get("volume_to_liquidity_24h", 0)
        vol_val = float(vol_to_liq) if isinstance(vol_to_liq, (int, float)) else 0.0
        vol_r_style = C_RED if vol_val > 5 else C_GOLD if vol_val > 2 else C_GREEN
        table.add_row("Vol/Liq 24h", Text(str(vol_to_liq), style=vol_r_style))

        # Buy/Sell imbalance
        imbalance = heuristics.get("buy_sell_imbalance_1h", 0)
        imb_val = float(imbalance) if isinstance(imbalance, (int, float)) else 0.0
        imb_style = f"bold {C_GREEN}" if imb_val > 0.2 else C_RED if imb_val < -0.2 else C_TEXT
        table.add_row("Buy/Sell 1h", Text(str(imbalance), style=imb_style))

        # Status
        status = str(heuristics.get("status", ""))
        status_style = (
            f"bold {C_GREEN}" if status == "balanced"
            else f"bold {C_RED}" if status == "concentrated-liquidity"
            else f"bold {C_GOLD}"
        )
        table.add_row("Status", Text(status, style=status_style))

    # -- Dexscreener link --
    if pair.pair_url:
        table.add_row("Dexscreener", Text(_safe_text(pair.pair_url), style=f"{C_BLUE} underline"))

    # -- Extra pairs hint --
    if extra_pairs > 0:
        table.add_row("", Text(f"{extra_pairs} additional pairs found", style=C_DIM))

    return table


# Keep legacy wrappers for backward compatibility
def render_pair_detail(pair: PairSnapshot, boost_total: float = 0.0, boost_count: int = 0) -> Table:
    return render_inspect_view(pair, boost_total=boost_total, boost_count=boost_count)


def render_distribution_panel(candidate: HotTokenCandidate) -> Table:
    heuristics = build_distribution_heuristics(candidate)
    return render_inspect_view(
        candidate.pair,
        heuristics=heuristics,
        boost_total=candidate.boost_total,
        boost_count=candidate.boost_count,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Chain heat table
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def render_chain_heat_table(candidates: list[HotTokenCandidate]) -> Table:
    table = Table(
        title=f"[bold {C_TEXT}]{_safe_text(DIAMOND)} Chain Heat[/bold {C_TEXT}]",
        box=box.SIMPLE_HEAVY,
        expand=True,
        row_styles=["", "on #1e2029"],
        border_style=C_BORDER,
        header_style=f"bold {C_TEXT}",
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
        heat_txt.append(_safe_text(BAR_FILL * heat_bar_w), style=C_GREEN)
        heat_txt.append(f" {count}", style=f"bold {C_TEXT}")

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
            title=f"[bold {C_TEXT}]{_safe_text(DIAMOND)} Flow Summary[/bold {C_TEXT}]",
            border_style=C_BORDER,
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
    text.append("24h Volume    ", style=C_LABEL)
    text.append_text(_vol_heat(total_vol))
    text.append("\n")

    # Liquidity
    text.append("Liquidity     ", style=C_LABEL)
    text.append(f"{fmt_usd(total_liq)}", style=f"bold {C_GREEN}")
    text.append("\n")

    # Average 1h with arrow
    text.append("Avg 1h Move   ", style=C_LABEL)
    text.append_text(_momentum_text(avg_h1))
    text.append("\n")

    # Imbalance with visual bar
    text.append("Buy/Sell Imb  ", style=C_LABEL)
    imb_style = (
        f"bold {C_GREEN}" if avg_imbalance > 0.1
        else f"bold {C_RED}" if avg_imbalance < -0.1
        else C_TEXT
    )
    # Visual imbalance bar
    bar_w = 10
    center = bar_w // 2
    fill_pos = int(round(abs(avg_imbalance) * center))
    fill_pos = min(fill_pos, center)
    if avg_imbalance >= 0:
        bar_txt = Text()
        bar_txt.append(_safe_text(BAR_EMPTY * center), style=C_DIM)
        bar_txt.append(_safe_text(BAR_FILL * fill_pos), style=C_GREEN)
        bar_txt.append(_safe_text(BAR_EMPTY * (center - fill_pos)), style=C_DIM)
    else:
        bar_txt = Text()
        bar_txt.append(_safe_text(BAR_EMPTY * (center - fill_pos)), style=C_DIM)
        bar_txt.append(_safe_text(BAR_FILL * fill_pos), style=C_RED)
        bar_txt.append(_safe_text(BAR_EMPTY * center), style=C_DIM)
    text.append_text(bar_txt)
    text.append(f" {avg_imbalance:+.2f}\n", style=imb_style)

    text.append(_safe_text(SEPARATOR * 36) + "\n", style=C_BORDER)

    # Regime
    text.append("Regime        ", style=C_LABEL)
    regime_style = (
        f"bold {C_GREEN}" if regime == "trend-up"
        else f"bold {C_RED}" if regime == "trend-down"
        else f"bold {C_GOLD}"
    )
    text.append(f"{regime}\n", style=regime_style)

    # Flags with dot indicators
    flag_style = C_PURPLE
    if "sell-pressure" in market_flags:
        flag_style = f"bold {C_RED}"
    elif "balanced" in market_flags:
        flag_style = f"bold {C_GREEN}"

    text.append("Flags         ", style=C_LABEL)
    for i, flag in enumerate(market_flags):
        if i > 0:
            text.append("  ", style="")
        text.append(_safe_text(f"{DOT} "), style=flag_style)
        text.append(flag, style=flag_style)

    return Panel(
        text,
        title=f"[bold {C_TEXT}]{_safe_text(DIAMOND)} Flow Summary[/bold {C_TEXT}]",
        border_style=C_BORDER,
        box=box.HEAVY,
    )


def render_setup_summary(
    *,
    chains: tuple[str, ...],
    style_name: str,
    limit: int,
    min_liquidity_usd: float,
    min_volume_h24_usd: float,
    min_txns_h1: int,
    min_price_change_h1: float,
) -> Panel:
    """Styled summary panel shown after completing the setup wizard."""
    grid = Table(show_header=False, box=None, padding=(0, 2), expand=True)
    grid.add_column("label", style=f"bold {C_LABEL}", width=18)
    grid.add_column("value")

    # Chains row
    chain_parts = Text()
    for i, ch in enumerate(chains):
        if i > 0:
            chain_parts.append("  ", style="")
        chain_parts.append_text(_chain_text(ch))
    grid.add_row("Chains", chain_parts)

    # Style
    style_colors = {"alpha hunter": C_RED, "balanced": C_GOLD, "conservative": C_GREEN}
    grid.add_row("Trading Style", Text(style_name, style=f"bold {style_colors.get(style_name, C_TEXT)}"))

    # Limit
    grid.add_row("Tokens / Scan", Text(str(limit), style=f"bold {C_TEXT}"))

    # Liquidity
    grid.add_row("Min Liquidity", Text(fmt_usd(min_liquidity_usd), style=f"bold {C_GREEN}"))

    # Volume
    grid.add_row("Min 24h Volume", Text(fmt_usd(min_volume_h24_usd), style=f"bold {C_CYAN}"))

    # Txns
    grid.add_row("Min Txns / 1h", Text(str(min_txns_h1), style=f"bold {C_TEXT}"))

    # Price change
    pch_style = C_GREEN if min_price_change_h1 >= 0 else C_GOLD if min_price_change_h1 > -15 else C_RED
    pch_label = f"{min_price_change_h1:+.0f}%"
    grid.add_row("Min 1h Change", Text(pch_label, style=f"bold {pch_style}"))

    return Panel(
        grid,
        title=f"[bold {C_TEXT}]{_safe_text(DIAMOND)} Your Scanner Config[/bold {C_TEXT}]",
        border_style=C_BORDER,
        box=box.HEAVY,
        padding=(1, 2),
    )
