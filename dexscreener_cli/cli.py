from __future__ import annotations

import asyncio
from collections import deque
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Annotated, Any

import typer
from rich import box
from rich.columns import Columns
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .alerts import send_alerts, send_test_alert
from .client import DexScreenerClient
from .config import DEFAULT_CHAINS, ScanFilters
from .models import HotTokenCandidate, PairSnapshot
from .scanner import HotScanner
from .state import ScanPreset, ScanTask, StateStore, utc_now_iso
from .task_runner import execute_task_once, select_due_tasks, task_filters as runner_task_filters
from .ui import (
    build_header,
    fmt_pct,
    fmt_usd,
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
from .watch_controls import WatchKeyboardController, copy_to_clipboard

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
NEW_RUNNER_SORT_MODES: tuple[str, ...] = ("score", "readiness", "rs", "volume", "momentum")
AI_SEARCH_QUERIES: tuple[str, ...] = ("virtual", "aixbt", "agent", "ai", "gpt", "llm", "bot", "neural", "inference")
AI_KEYWORDS: tuple[str, ...] = (
    "ai",
    "agent",
    "gpt",
    "llm",
    "neural",
    "model",
    "intelligence",
    "bot",
    "oracle",
    "assistant",
    "auton",
    "compute",
    "inference",
    "virtual",
    "aixbt",
)
NEW_TOKEN_SEARCH_QUERIES: tuple[str, ...] = (
    "new",
    "launch",
    "launched",
    "base",
    "coin",
    "token",
    "meme",
    "pump",
    "moon",
    "cat",
    "dog",
    "pepe",
    "inu",
    "ai",
    "agent",
    "gpt",
    "eth",
    "sol",
    "alpha",
    "beta",
    "gem",
    "degen",
    "official",
    "2026",
    "2025",
    "x",
    "z",
    "a",
    "e",
    "i",
    "o",
    "u",
)


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


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except Exception:
        return default


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except Exception:
        return default


def _pct_text(value: float) -> Text:
    if value >= 10:
        return Text(fmt_pct(value), style="bold bright_green")
    if value > 0:
        return Text(fmt_pct(value), style="green")
    if value <= -10:
        return Text(fmt_pct(value), style="bold bright_red")
    if value < 0:
        return Text(fmt_pct(value), style="red")
    return Text(fmt_pct(value), style="white")


def _pct_or_na(value: float, *, txns_h1: int) -> Text:
    if txns_h1 <= 0:
        return Text("N/A", style="dim")
    return _pct_text(value)


def _ai_rows_json(rows: list[dict[str, object]]) -> str:
    return json.dumps(rows, indent=2, ensure_ascii=True)


def _parse_chains(raw: str) -> tuple[str, ...]:
    values = tuple(c.strip().lower() for c in raw.split(",") if c.strip())
    return values or DEFAULT_CHAINS


def _candidate_json(c: HotTokenCandidate) -> dict[str, object]:
    p = c.pair
    a = c.analytics
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
        "analytics": {
            "compressionScore": a.compression_score,
            "breakoutReadiness": a.breakout_readiness,
            "volumeVelocity": a.volume_velocity,
            "txnVelocity": a.txn_velocity,
            "relativeStrength": a.relative_strength,
            "chainBaselineH1": a.chain_baseline_h1,
            "boostVelocityPerMin": a.boost_velocity,
            "momentumHalfLifeMin": a.momentum_half_life_min,
            "momentumDecayRatio": a.momentum_decay_ratio,
            "fastDecay": a.fast_decay,
        },
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


async def _scan_alpha_drops(
    *,
    chains: tuple[str, ...],
    limit: int,
    max_age_hours: float,
    min_liquidity_usd: float,
    min_volume_h24_usd: float,
    min_txns_h1: int,
    min_price_change_h1: float,
    sort_by: str,
    min_breakout_readiness: float,
    min_relative_strength: float,
    decay_filter: bool,
    min_half_life_minutes: float,
    min_decay_ratio: float,
    max_vol_liq_ratio: float,
) -> list[HotTokenCandidate]:
    fetch_limit = min(max(limit * 6, 60), 150)
    filters = ScanFilters(
        chains=chains,
        limit=fetch_limit,
        min_liquidity_usd=min_liquidity_usd,
        min_volume_h24_usd=min_volume_h24_usd,
        min_txns_h1=min_txns_h1,
        min_price_change_h1=min_price_change_h1,
    )
    async with DexScreenerClient() as client:
        scanner = HotScanner(client)
        raw = await scanner.scan(filters)
    return _select_new_runners(
        candidates=raw,
        max_age_hours=max_age_hours,
        include_unknown_age=False,
        sort_by=sort_by,
        min_breakout_readiness=min_breakout_readiness,
        min_relative_strength=min_relative_strength,
        decay_filter=decay_filter,
        min_half_life_minutes=min_half_life_minutes,
        min_decay_ratio=min_decay_ratio,
        max_vol_liq_ratio=max_vol_liq_ratio,
        limit=limit,
    )


async def _scan_ai_tokens(
    *,
    chain: str,
    limit: int,
    min_liquidity_usd: float,
    min_volume_h24_usd: float,
    min_txns_h1: int,
) -> list[dict[str, object]]:
    chain = chain.lower().strip()
    all_pairs: list[dict[str, object]] = []
    async with DexScreenerClient() as client:
        for query in AI_SEARCH_QUERIES:
            rows = await client.search_pairs(query)
            all_pairs.extend(rows)

    pairs = [p for p in all_pairs if str(p.get("chainId", "")).lower() == chain]
    filtered: list[dict[str, object]] = []
    for p in pairs:
        base = p.get("baseToken", {})
        symbol = str((base or {}).get("symbol", ""))
        name = str((base or {}).get("name", ""))
        labels = " ".join(str(x) for x in (p.get("labels", []) or []))
        hay = f"{symbol} {name} {labels}".lower()
        if any(keyword in hay for keyword in AI_KEYWORDS):
            filtered.append(p)

    dedup: dict[str, dict[str, object]] = {}
    for p in filtered:
        base = p.get("baseToken", {})
        token_address = str((base or {}).get("address", ""))
        if not token_address:
            continue
        prev = dedup.get(token_address)
        current_vol = _as_float(((p.get("volume", {}) or {}).get("h24")))
        if prev is None or current_vol > _as_float(((prev.get("volume", {}) or {}).get("h24"))):
            dedup[token_address] = p

    rows: list[dict[str, object]] = []
    for p in dedup.values():
        base = p.get("baseToken", {})
        tx_h1 = ((p.get("txns", {}) or {}).get("h1", {}) or {})
        buys_h1 = _as_int(tx_h1.get("buys"))
        sells_h1 = _as_int(tx_h1.get("sells"))
        tx1h = buys_h1 + sells_h1
        vol24 = _as_float(((p.get("volume", {}) or {}).get("h24")))
        liq = _as_float(((p.get("liquidity", {}) or {}).get("usd")))
        if vol24 < min_volume_h24_usd:
            continue
        if liq < min_liquidity_usd:
            continue
        if tx1h < min_txns_h1:
            continue
        rows.append(
            {
                "chainId": chain,
                "symbol": str((base or {}).get("symbol", "?")),
                "name": str((base or {}).get("name", "?")),
                "tokenAddress": str((base or {}).get("address", "")),
                "dexId": str(p.get("dexId", "")),
                "pairAddress": str(p.get("pairAddress", "")),
                "priceUsd": _as_float(p.get("priceUsd")),
                "priceChangeH1": _as_float(((p.get("priceChange", {}) or {}).get("h1"))),
                "priceChangeH24": _as_float(((p.get("priceChange", {}) or {}).get("h24"))),
                "volumeH24": vol24,
                "liquidityUsd": liq,
                "txnsH1": tx1h,
                "pairUrl": str(p.get("url", "")),
            }
        )

    rows.sort(
        key=lambda r: (
            _as_float(r.get("volumeH24")),
            _as_int(r.get("txnsH1")),
            _as_float(r.get("liquidityUsd")),
        ),
        reverse=True,
    )
    return rows[:limit]


async def _scan_new_launches(
    *,
    chain: str,
    days: int,
    limit: int,
    min_liquidity_usd: float,
    min_volume_h24_usd: float,
    min_txns_h1: int,
    min_txns_h24: int,
) -> list[dict[str, object]]:
    chain = chain.lower().strip()
    window_ms = max(days, 1) * 24 * 3600 * 1000
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    cutoff_ms = now_ms - window_ms

    all_rows: list[dict[str, object]] = []
    async with DexScreenerClient() as client:
        for query in NEW_TOKEN_SEARCH_QUERIES:
            try:
                rows = await client.search_pairs(query)
            except Exception:
                continue
            all_rows.extend(rows)

    pair_dedup: dict[str, PairSnapshot] = {}
    for row in all_rows:
        if str(row.get("chainId", "")).lower() != chain:
            continue
        pair = PairSnapshot.from_api(row)
        if pair.pair_created_at_ms is None:
            continue
        if pair.pair_created_at_ms < cutoff_ms:
            continue
        key = pair.pair_address.lower()
        prev = pair_dedup.get(key)
        if prev is None or pair.volume_h24 > prev.volume_h24:
            pair_dedup[key] = pair

    token_dedup: dict[str, PairSnapshot] = {}
    for pair in pair_dedup.values():
        token_key = pair.base_address.lower()
        prev = token_dedup.get(token_key)
        if prev is None or pair.volume_h24 > prev.volume_h24:
            token_dedup[token_key] = pair

    rows: list[dict[str, object]] = []
    for pair in token_dedup.values():
        if pair.volume_h24 < min_volume_h24_usd:
            continue
        if pair.liquidity_usd < min_liquidity_usd:
            continue
        if pair.txns_h1 < min_txns_h1:
            continue
        if pair.txns_h24 < min_txns_h24:
            continue
        age_hours = (now_ms - pair.pair_created_at_ms) / 3600000 if pair.pair_created_at_ms else None
        rows.append(
            {
                "chainId": chain,
                "symbol": pair.base_symbol,
                "name": pair.base_name,
                "tokenAddress": pair.base_address,
                "pairAddress": pair.pair_address,
                "priceUsd": pair.price_usd,
                "priceChangeH1": pair.price_change_h1,
                "priceChangeH24": pair.price_change_h24,
                "volumeH24": pair.volume_h24,
                "liquidityUsd": pair.liquidity_usd,
                "txnsH1": pair.txns_h1,
                "txnsH24": pair.txns_h24,
                "ageHours": age_hours,
                "dexId": pair.dex_id,
                "pairUrl": pair.pair_url,
            }
        )

    rows.sort(
        key=lambda r: (
            _as_float(r.get("volumeH24")),
            _as_int(r.get("txnsH24")),
            _as_int(r.get("txnsH1")),
            _as_float(r.get("liquidityUsd")),
        ),
        reverse=True,
    )
    return rows[:limit]


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


def _render_ai_board(
    *,
    chain: str,
    rows: list[dict[str, object]],
    min_liquidity_usd: float,
    min_volume_h24_usd: float,
    min_txns_h1: int,
) -> None:
    table = Table(
        title=(
            f"[bold bright_white]Top AI Tokens[/bold bright_white]  "
            f"[cyan]chain={chain}[/cyan]  "
            f"[green]liq>={fmt_usd(min_liquidity_usd)}[/green]  "
            f"[green]vol24>={fmt_usd(min_volume_h24_usd)}[/green]  "
            f"[magenta]tx1h>={min_txns_h1}[/magenta]"
        ),
        box=box.ROUNDED,
        header_style="bold bright_white",
        row_styles=["none", "dim"],
    )
    table.add_column("#", justify="right")
    table.add_column("Token", style="bold yellow")
    table.add_column("Price", justify="right")
    table.add_column("1h", justify="right")
    table.add_column("24h", justify="right")
    table.add_column("24h Vol", justify="right")
    table.add_column("1h Txns", justify="right")
    table.add_column("Liquidity", justify="right")
    table.add_column("Dex")

    for i, row in enumerate(rows, start=1):
        symbol = str(row.get("symbol", "?"))
        price = _as_float(row.get("priceUsd"))
        h1 = _as_float(row.get("priceChangeH1"))
        h24 = _as_float(row.get("priceChangeH24"))
        vol24 = _as_float(row.get("volumeH24"))
        tx1h = _as_int(row.get("txnsH1"))
        liq = _as_float(row.get("liquidityUsd"))
        dex = str(row.get("dexId", ""))
        table.add_row(
            str(i),
            symbol,
            f"${price:,.8f}" if price < 0.01 else f"${price:,.6f}",
            _pct_or_na(h1, txns_h1=tx1h),
            _pct_text(h24),
            Text(fmt_usd(vol24), style="bright_cyan" if vol24 >= 100_000 else "cyan"),
            str(tx1h),
            Text(fmt_usd(liq), style="green" if liq >= 50_000 else "yellow"),
            dex,
        )
    if not rows:
        table.add_row("-", "No AI tokens matched filters", "-", "-", "-", "-", "-", "-", "-")

    total_vol = sum(_as_float(r.get("volumeH24")) for r in rows)
    total_liq = sum(_as_float(r.get("liquidityUsd")) for r in rows)
    avg_h1 = (
        sum(_as_float(r.get("priceChangeH1")) for r in rows) / len(rows)
        if rows
        else 0.0
    )
    summary = Panel(
        Text(
            f"Rows: {len(rows)}\n"
            f"24h volume sum: {fmt_usd(total_vol)}\n"
            f"Liquidity sum: {fmt_usd(total_liq)}\n"
            f"Average 1h move: {fmt_pct(avg_h1)}"
        ),
        title="[bold bright_white]AI Market Snapshot[/bold bright_white]",
        border_style="bright_blue",
        box=box.ROUNDED,
    )
    console.print(build_header())
    console.print(table)
    console.print(summary)


def _render_new_launches_board(
    *,
    chain: str,
    days: int,
    rows: list[dict[str, object]],
    min_liquidity_usd: float,
    min_volume_h24_usd: float,
    min_txns_h1: int,
    min_txns_h24: int,
) -> None:
    table = Table(
        title=(
            f"[bold bright_white]Top New Coins[/bold bright_white]  "
            f"[cyan]chain={chain}[/cyan]  "
            f"[yellow]window={days}d[/yellow]  "
            f"[green]liq>={fmt_usd(min_liquidity_usd)}[/green]  "
            f"[green]vol24>={fmt_usd(min_volume_h24_usd)}[/green]  "
            f"[magenta]tx1h>={min_txns_h1}[/magenta]  "
            f"[magenta]tx24h>={min_txns_h24}[/magenta]"
        ),
        box=box.ROUNDED,
        header_style="bold bright_white",
        row_styles=["none", "dim"],
    )
    table.add_column("#", justify="right")
    table.add_column("Token", style="bold yellow")
    table.add_column("Age", justify="right")
    table.add_column("Price", justify="right")
    table.add_column("1h", justify="right")
    table.add_column("24h", justify="right")
    table.add_column("24h Vol", justify="right")
    table.add_column("1h Txns", justify="right")
    table.add_column("24h Txns", justify="right")
    table.add_column("Liquidity", justify="right")
    table.add_column("Dex")

    for idx, row in enumerate(rows, start=1):
        symbol = str(row.get("symbol", "?"))
        age_hours = _as_float(row.get("ageHours"), 0.0)
        age_style = "bright_cyan" if age_hours <= 24 else "yellow" if age_hours <= 72 else "white"
        price = _as_float(row.get("priceUsd"))
        vol24 = _as_float(row.get("volumeH24"))
        tx1h = _as_int(row.get("txnsH1"))
        tx24h = _as_int(row.get("txnsH24"))
        liq = _as_float(row.get("liquidityUsd"))
        table.add_row(
            str(idx),
            symbol,
            Text(f"{age_hours:.1f}h", style=age_style),
            Text(f"${price:,.8f}" if price < 0.01 else f"${price:,.6f}", style="white"),
            _pct_or_na(_as_float(row.get("priceChangeH1")), txns_h1=tx1h),
            _pct_text(_as_float(row.get("priceChangeH24"))),
            Text(fmt_usd(vol24), style="bright_cyan" if vol24 >= 100_000 else "cyan"),
            Text(str(tx1h), style="bright_white" if tx1h >= 100 else "white"),
            Text(str(tx24h), style="bright_white" if tx24h >= 250 else "white"),
            Text(fmt_usd(liq), style="green" if liq >= 50_000 else "yellow" if liq >= 10_000 else "red"),
            str(row.get("dexId", "")),
        )

    if not rows:
        table.add_row("-", "No new coins matched filters", "-", "-", "-", "-", "-", "-", "-", "-", "-")

    total_vol = sum(_as_float(r.get("volumeH24")) for r in rows)
    total_liq = sum(_as_float(r.get("liquidityUsd")) for r in rows)
    avg_age = sum(_as_float(r.get("ageHours")) for r in rows) / len(rows) if rows else 0.0
    summary = Panel(
        Text(
            f"Rows: {len(rows)}\n"
            f"24h volume sum: {fmt_usd(total_vol)}\n"
            f"Liquidity sum: {fmt_usd(total_liq)}\n"
            f"Average age: {avg_age:.1f}h"
        ),
        title="[bold bright_white]New Coin Snapshot[/bold bright_white]",
        border_style="bright_blue",
        box=box.ROUNDED,
    )
    console.print(build_header())
    console.print(table)
    console.print(summary)


def _new_runner_rank(candidate: HotTokenCandidate) -> tuple[float, float, int, float]:
    age = candidate.pair.age_hours
    freshness_bonus = 0.0 if age is None else max(0.0, (24.0 - age) / 24.0) * 8.0
    return (
        candidate.score + freshness_bonus,
        candidate.pair.volume_h1,
        candidate.pair.txns_h1,
        candidate.pair.price_change_h1,
    )


def _new_runner_sort_key(candidate: HotTokenCandidate, mode: str) -> tuple[float, ...]:
    if mode == "readiness":
        return (
            candidate.analytics.breakout_readiness,
            candidate.analytics.compression_score,
            candidate.analytics.relative_strength,
            candidate.score,
            candidate.pair.volume_h1,
        )
    if mode == "rs":
        return (
            candidate.analytics.relative_strength,
            candidate.analytics.breakout_readiness,
            candidate.score,
            candidate.pair.volume_h1,
        )
    if mode == "volume":
        return (
            candidate.pair.volume_h1,
            candidate.pair.txns_h1,
            candidate.score,
            candidate.analytics.breakout_readiness,
        )
    if mode == "momentum":
        return (
            candidate.pair.price_change_h1,
            candidate.analytics.relative_strength,
            candidate.score,
            candidate.pair.volume_h1,
        )
    return (
        candidate.score,
        candidate.analytics.breakout_readiness,
        candidate.analytics.relative_strength,
        candidate.pair.volume_h1,
    )


def _passes_new_runner_quality(
    candidate: HotTokenCandidate,
    *,
    min_breakout_readiness: float,
    min_relative_strength: float,
    decay_filter: bool,
    min_half_life_minutes: float,
    min_decay_ratio: float,
    max_vol_liq_ratio: float,
) -> bool:
    analytics = candidate.analytics
    vol_liq_ratio = candidate.pair.volume_h24 / max(candidate.pair.liquidity_usd, 1.0)
    if max_vol_liq_ratio > 0 and vol_liq_ratio > max_vol_liq_ratio:
        return False
    if analytics.breakout_readiness < min_breakout_readiness:
        return False
    if analytics.relative_strength < min_relative_strength:
        return False
    if not decay_filter:
        return True
    if analytics.fast_decay:
        return False
    if analytics.momentum_half_life_min is not None and analytics.momentum_half_life_min < min_half_life_minutes:
        return False
    if analytics.momentum_decay_ratio is not None and analytics.momentum_decay_ratio < min_decay_ratio:
        return False
    return True


def _select_new_runners(
    *,
    candidates: list[HotTokenCandidate],
    max_age_hours: float,
    include_unknown_age: bool,
    sort_by: str,
    min_breakout_readiness: float,
    min_relative_strength: float,
    decay_filter: bool,
    min_half_life_minutes: float,
    min_decay_ratio: float,
    max_vol_liq_ratio: float,
    limit: int,
) -> list[HotTokenCandidate]:
    selected_sort = sort_by if sort_by in NEW_RUNNER_SORT_MODES else "score"
    fresh: list[HotTokenCandidate] = []
    for candidate in candidates:
        age = candidate.pair.age_hours
        if age is None and not include_unknown_age:
            continue
        if age is not None and age > max_age_hours:
            continue
        if not _passes_new_runner_quality(
            candidate,
            min_breakout_readiness=min_breakout_readiness,
            min_relative_strength=min_relative_strength,
            decay_filter=decay_filter,
            min_half_life_minutes=min_half_life_minutes,
            min_decay_ratio=min_decay_ratio,
            max_vol_liq_ratio=max_vol_liq_ratio,
        ):
            continue
        fresh.append(candidate)
    return sorted(fresh, key=lambda c: _new_runner_sort_key(c, selected_sort), reverse=True)[:limit]


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


@app.command("ai-top")
def ai_top(
    chain: Annotated[str, typer.Option(help="Chain ID, defaults to base")] = "base",
    limit: Annotated[int, typer.Option(help="Max rows to show")] = 10,
    min_liquidity_usd: Annotated[float, typer.Option(help="Minimum pair liquidity in USD")] = 0.0,
    min_volume_h24_usd: Annotated[float, typer.Option(help="Minimum 24h volume in USD")] = 0.0,
    min_txns_h1: Annotated[int, typer.Option(help="Minimum 1h transactions")] = 0,
    as_json: Annotated[bool, typer.Option("--json", help="Output machine-readable JSON")] = False,
) -> None:
    """Show top AI-themed tokens on a chain with a cleaner leaderboard."""
    rows = asyncio.run(
        _scan_ai_tokens(
            chain=chain,
            limit=limit,
            min_liquidity_usd=min_liquidity_usd,
            min_volume_h24_usd=min_volume_h24_usd,
            min_txns_h1=min_txns_h1,
        )
    )
    if as_json:
        typer.echo(_ai_rows_json(rows))
        return
    _render_ai_board(
        chain=chain.lower().strip(),
        rows=rows,
        min_liquidity_usd=min_liquidity_usd,
        min_volume_h24_usd=min_volume_h24_usd,
        min_txns_h1=min_txns_h1,
    )


@app.command("top-new")
def top_new(
    chain: Annotated[str, typer.Option(help="Chain ID, defaults to base")] = "base",
    days: Annotated[int, typer.Option(help="Lookback window in days")] = 7,
    limit: Annotated[int, typer.Option(help="Max rows to show")] = 10,
    min_liquidity_usd: Annotated[float, typer.Option(help="Minimum pair liquidity in USD")] = 25_000.0,
    min_volume_h24_usd: Annotated[float, typer.Option(help="Minimum 24h volume in USD")] = 1_000.0,
    min_txns_h1: Annotated[int, typer.Option(help="Minimum 1h transactions")] = 0,
    min_txns_h24: Annotated[int, typer.Option(help="Minimum 24h transactions")] = 50,
    as_json: Annotated[bool, typer.Option("--json", help="Output machine-readable JSON")] = False,
) -> None:
    """Show top new coins by 24h volume for a rolling time window."""
    rows = asyncio.run(
        _scan_new_launches(
            chain=chain,
            days=days,
            limit=limit,
            min_liquidity_usd=min_liquidity_usd,
            min_volume_h24_usd=min_volume_h24_usd,
            min_txns_h1=min_txns_h1,
            min_txns_h24=min_txns_h24,
        )
    )
    if as_json:
        typer.echo(_ai_rows_json(rows))
        return
    _render_new_launches_board(
        chain=chain.lower().strip(),
        days=max(days, 1),
        rows=rows,
        min_liquidity_usd=min_liquidity_usd,
        min_volume_h24_usd=min_volume_h24_usd,
        min_txns_h1=min_txns_h1,
        min_txns_h24=min_txns_h24,
    )


@app.command("alpha-drops")
def alpha_drops(
    chains: Annotated[str, typer.Option(help="Comma-separated chain IDs")] = "base,solana",
    limit: Annotated[int, typer.Option(help="Max rows")] = 15,
    max_age_hours: Annotated[float, typer.Option(help="Only include pairs newer than this age")] = 6.0,
    min_liquidity_usd: Annotated[float, typer.Option(help="Minimum pair liquidity in USD")] = 35_000.0,
    min_volume_h24_usd: Annotated[float, typer.Option(help="Minimum 24h volume in USD")] = 90_000.0,
    min_txns_h1: Annotated[int, typer.Option(help="Minimum 1h transactions")] = 80,
    min_price_change_h1: Annotated[float, typer.Option(help="Minimum 1h price change percent")] = 0.0,
    sort_by: Annotated[str, typer.Option(help="Sort mode: score/readiness/rs/volume/momentum")] = "readiness",
    min_breakout_readiness: Annotated[float, typer.Option(help="Minimum breakout readiness (0-100)")] = 55.0,
    min_relative_strength: Annotated[float, typer.Option(help="Minimum relative strength vs chain baseline")] = 0.0,
    decay_filter: Annotated[bool, typer.Option(help="Filter fast-decay momentum profiles")] = True,
    min_half_life_minutes: Annotated[float, typer.Option(help="Minimum momentum half-life in minutes (if known)")] = 6.0,
    min_decay_ratio: Annotated[float, typer.Option(help="Minimum momentum decay ratio (if known)")] = 0.35,
    max_vol_liq_ratio: Annotated[float, typer.Option(help="Maximum 24h volume/liquidity ratio (anti-thin filter)")] = 60.0,
    as_json: Annotated[bool, typer.Option("--json", help="Output machine-readable JSON")] = False,
) -> None:
    """One-shot alpha drop scan across configured chains with quality gates."""
    scan_chains = _parse_chains(chains)
    selected_sort = sort_by if sort_by in NEW_RUNNER_SORT_MODES else "readiness"
    candidates = asyncio.run(
        _scan_alpha_drops(
            chains=scan_chains,
            limit=limit,
            max_age_hours=max_age_hours,
            min_liquidity_usd=min_liquidity_usd,
            min_volume_h24_usd=min_volume_h24_usd,
            min_txns_h1=min_txns_h1,
            min_price_change_h1=min_price_change_h1,
            sort_by=selected_sort,
            min_breakout_readiness=min_breakout_readiness,
            min_relative_strength=min_relative_strength,
            decay_filter=decay_filter,
            min_half_life_minutes=min_half_life_minutes,
            min_decay_ratio=min_decay_ratio,
            max_vol_liq_ratio=max_vol_liq_ratio,
        )
    )
    if as_json:
        typer.echo(json.dumps([_candidate_json(c) for c in candidates], indent=2, ensure_ascii=True))
        return
    console.print(build_header())
    console.print(
        render_new_runners_table(
            candidates,
            chain=",".join(scan_chains),
            max_age_hours=max_age_hours,
            limit=limit,
        )
    )
    console.print(Columns([render_chain_heat_table(candidates), render_flow_panel(candidates)]))
    if len(candidates) < limit:
        console.print(
            f"[yellow]Only found {len(candidates)} alpha drops with current gates. "
            "Lower min-liquidity/min-volume/min-txns or min-breakout-readiness to widen coverage.[/yellow]"
        )


@app.command("alpha-drops-watch")
def alpha_drops_watch(
    chains: Annotated[str, typer.Option(help="Comma-separated chain IDs")] = "base,solana",
    limit: Annotated[int, typer.Option(help="Max rows")] = 15,
    max_age_hours: Annotated[float, typer.Option(help="Only include pairs newer than this age")] = 6.0,
    interval: Annotated[float, typer.Option(help="Refresh interval seconds")] = 6.0,
    min_liquidity_usd: Annotated[float, typer.Option(help="Minimum pair liquidity in USD")] = 35_000.0,
    min_volume_h24_usd: Annotated[float, typer.Option(help="Minimum 24h volume in USD")] = 90_000.0,
    min_txns_h1: Annotated[int, typer.Option(help="Minimum 1h transactions")] = 80,
    min_price_change_h1: Annotated[float, typer.Option(help="Minimum 1h price change percent")] = 0.0,
    sort_by: Annotated[str, typer.Option(help="Sort mode: score/readiness/rs/volume/momentum")] = "readiness",
    min_breakout_readiness: Annotated[float, typer.Option(help="Minimum breakout readiness (0-100)")] = 55.0,
    min_relative_strength: Annotated[float, typer.Option(help="Minimum relative strength vs chain baseline")] = 0.0,
    decay_filter: Annotated[bool, typer.Option(help="Filter fast-decay momentum profiles")] = True,
    min_half_life_minutes: Annotated[float, typer.Option(help="Minimum momentum half-life in minutes (if known)")] = 6.0,
    min_decay_ratio: Annotated[float, typer.Option(help="Minimum momentum decay ratio (if known)")] = 0.35,
    max_vol_liq_ratio: Annotated[float, typer.Option(help="Maximum 24h volume/liquidity ratio (anti-thin filter)")] = 60.0,
    webhook_url: Annotated[str | None, typer.Option(help="Generic JSON webhook URL")] = None,
    discord_webhook_url: Annotated[str | None, typer.Option(help="Discord webhook URL")] = None,
    telegram_bot_token: Annotated[str | None, typer.Option(help="Telegram bot token")] = None,
    telegram_chat_id: Annotated[str | None, typer.Option(help="Telegram chat id")] = None,
    alert_min_score: Annotated[float, typer.Option(help="Alert threshold on top score")] = 72.0,
    alert_cooldown_seconds: Annotated[int, typer.Option(help="Alert cooldown seconds")] = 300,
    alert_template: Annotated[str | None, typer.Option(help="Alert text template")] = None,
    alert_top_n: Annotated[int, typer.Option(help="How many top candidates in message")] = 3,
    alert_min_liquidity_usd: Annotated[float | None, typer.Option(help="Alert gate: minimum liquidity")] = None,
    alert_max_vol_liq_ratio: Annotated[float | None, typer.Option(help="Alert gate: maximum volume/liquidity ratio")] = None,
    alert_blocked_terms: Annotated[str | None, typer.Option(help="Alert gate: blocked token terms (comma-separated)")] = None,
    alert_blocked_chains: Annotated[str | None, typer.Option(help="Alert gate: blocked chains (comma-separated)")] = None,
    webhook_extra_json: Annotated[str | None, typer.Option(help="Extra webhook JSON object")] = None,
    alert_max_per_hour: Annotated[int, typer.Option(help="Hard cap on sent alerts per hour (0 disables cap)")] = 8,
    no_alerts: Annotated[bool, typer.Option(help="Disable alert delivery")] = False,
    cycles: Annotated[int, typer.Option(help="Stop after N refreshes (0 = infinite)")] = 0,
    screen: Annotated[bool, typer.Option(help="Use fullscreen alternate buffer")] = True,
) -> None:
    """Live alpha-drop scanner with optional realtime notifications."""
    scan_chains = _parse_chains(chains)
    selected_sort = sort_by if sort_by in NEW_RUNNER_SORT_MODES else "readiness"
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
    runtime_task = ScanTask.create(name=f"alpha-drops:{','.join(scan_chains)}", alerts=alerts)

    async def loop() -> None:
        seen: set[tuple[str, str]] = set()
        previous_ranks: dict[tuple[str, str], int] = {}
        sent_alerts: deque[datetime] = deque()
        cycle = 0
        status_message = "watching for new alpha drops"
        with Live(console=console, screen=screen, refresh_per_second=6) as live:
            while True:
                cycle += 1
                candidates = await _scan_alpha_drops(
                    chains=scan_chains,
                    limit=limit,
                    max_age_hours=max_age_hours,
                    min_liquidity_usd=min_liquidity_usd,
                    min_volume_h24_usd=min_volume_h24_usd,
                    min_txns_h1=min_txns_h1,
                    min_price_change_h1=min_price_change_h1,
                    sort_by=selected_sort,
                    min_breakout_readiness=min_breakout_readiness,
                    min_relative_strength=min_relative_strength,
                    decay_filter=decay_filter,
                    min_half_life_minutes=min_half_life_minutes,
                    min_decay_ratio=min_decay_ratio,
                    max_vol_liq_ratio=max_vol_liq_ratio,
                )

                new_events = [c for c in candidates if c.key not in seen]
                seen.update(c.key for c in candidates)

                if not no_alerts and runtime_task.alerts and new_events:
                    now = datetime.now(UTC)
                    while sent_alerts and (now - sent_alerts[0]).total_seconds() >= 3600:
                        sent_alerts.popleft()
                    if alert_max_per_hour > 0 and len(sent_alerts) >= alert_max_per_hour:
                        status_message = (
                            f"alert cap reached ({alert_max_per_hour}/h), "
                            f"{len(new_events)} new drops queued visually only"
                        )
                    else:
                        alert_result = await send_alerts(runtime_task, new_events)
                        if alert_result.get("sent"):
                            runtime_task.last_alert_at = utc_now_iso()
                            sent_alerts.append(now)
                            status_message = f"alerts sent for {len(new_events)} new drops"
                        else:
                            status_message = f"alerts not sent: {alert_result.get('reason')}"
                elif new_events:
                    status_message = f"{len(new_events)} new alpha drops detected (alerts disabled)"
                else:
                    status_message = "no new alpha drops this cycle"

                view = Group(
                    build_header(),
                    render_new_runners_table(
                        candidates,
                        chain=",".join(scan_chains),
                        max_age_hours=max_age_hours,
                        limit=limit,
                    ),
                    Columns([render_chain_heat_table(candidates), render_flow_panel(candidates)]),
                    render_rank_movers_table(
                        candidates,
                        previous_ranks=previous_ranks,
                        limit=limit,
                    ),
                    Panel(
                        (
                            f"refresh={interval:.1f}s | cycle={cycle} | chains={','.join(scan_chains)} | sort={selected_sort}\n"
                            f"{status_message}\n"
                            "Ctrl+C to exit"
                        ),
                        border_style="dim",
                        box=box.ROUNDED,
                    ),
                )
                live.update(view)
                previous_ranks = {candidate.key: idx for idx, candidate in enumerate(candidates, start=1)}
                if cycles > 0 and cycle >= cycles:
                    return
                await asyncio.sleep(interval)

    try:
        asyncio.run(loop())
    except KeyboardInterrupt:
        console.print("[dim]Stopped alpha-drops watch mode.[/dim]")


@app.command("new-runners")
def new_runners(
    chain: Annotated[str, typer.Option(help="Chain ID, defaults to base")] = "base",
    limit: Annotated[int, typer.Option(help="Number of fresh runners to show")] = 10,
    max_age_hours: Annotated[float, typer.Option(help="Maximum token age in hours")] = 24.0,
    min_liquidity_usd: Annotated[float, typer.Option(help="Minimum pair liquidity in USD")] = 25_000.0,
    min_volume_h24_usd: Annotated[float, typer.Option(help="Minimum 24h volume in USD")] = 50_000.0,
    min_txns_h1: Annotated[int, typer.Option(help="Minimum 1h transactions")] = 25,
    min_price_change_h1: Annotated[float, typer.Option(help="Minimum 1h price change percent")] = 0.0,
    sort_by: Annotated[str, typer.Option(help="Sort mode: score/readiness/rs/volume/momentum")] = "score",
    min_breakout_readiness: Annotated[float, typer.Option(help="Minimum breakout readiness (0-100)")] = 0.0,
    min_relative_strength: Annotated[float, typer.Option(help="Minimum relative strength vs chain baseline")] = -999.0,
    decay_filter: Annotated[bool, typer.Option(help="Filter fast-decay momentum profiles")] = True,
    min_half_life_minutes: Annotated[float, typer.Option(help="Minimum momentum half-life in minutes (if known)")] = 6.0,
    min_decay_ratio: Annotated[float, typer.Option(help="Minimum momentum decay ratio (if known)")] = 0.35,
    max_vol_liq_ratio: Annotated[float, typer.Option(help="Maximum 24h volume/liquidity ratio (anti-thin filter)")] = 60.0,
    include_unknown_age: Annotated[bool, typer.Option(help="Include tokens with unknown pair age")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Output machine-readable JSON")] = False,
) -> None:
    """Show best new runners for a chain (optimized for day-trading discovery)."""
    chain = chain.lower().strip()
    sort_by = sort_by if sort_by in NEW_RUNNER_SORT_MODES else "score"
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
        sort_by=sort_by,
        min_breakout_readiness=min_breakout_readiness,
        min_relative_strength=min_relative_strength,
        decay_filter=decay_filter,
        min_half_life_minutes=min_half_life_minutes,
        min_decay_ratio=min_decay_ratio,
        max_vol_liq_ratio=max_vol_liq_ratio,
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
    watch_chains: Annotated[str | None, typer.Option(help="Comma-separated watch chain list for keyboard switching")] = None,
    limit: Annotated[int, typer.Option(help="Number of fresh runners to show")] = 10,
    max_age_hours: Annotated[float, typer.Option(help="Maximum token age in hours")] = 24.0,
    interval: Annotated[float, typer.Option(help="Refresh interval seconds")] = 7.0,
    min_liquidity_usd: Annotated[float, typer.Option(help="Minimum pair liquidity in USD")] = 25_000.0,
    min_volume_h24_usd: Annotated[float, typer.Option(help="Minimum 24h volume in USD")] = 50_000.0,
    min_txns_h1: Annotated[int, typer.Option(help="Minimum 1h transactions")] = 25,
    min_price_change_h1: Annotated[float, typer.Option(help="Minimum 1h price change percent")] = 0.0,
    sort_by: Annotated[str, typer.Option(help="Sort mode: score/readiness/rs/volume/momentum")] = "score",
    min_breakout_readiness: Annotated[float, typer.Option(help="Minimum breakout readiness (0-100)")] = 0.0,
    min_relative_strength: Annotated[float, typer.Option(help="Minimum relative strength vs chain baseline")] = -999.0,
    decay_filter: Annotated[bool, typer.Option(help="Filter fast-decay momentum profiles")] = True,
    min_half_life_minutes: Annotated[float, typer.Option(help="Minimum momentum half-life in minutes (if known)")] = 6.0,
    min_decay_ratio: Annotated[float, typer.Option(help="Minimum momentum decay ratio (if known)")] = 0.35,
    max_vol_liq_ratio: Annotated[float, typer.Option(help="Maximum 24h volume/liquidity ratio (anti-thin filter)")] = 60.0,
    include_unknown_age: Annotated[bool, typer.Option(help="Include tokens with unknown pair age")] = False,
    cycles: Annotated[int, typer.Option(help="Stop after N refreshes (0 = infinite)")] = 0,
    screen: Annotated[bool, typer.Option(help="Use fullscreen alternate buffer")] = True,
) -> None:
    """Full-screen live board for tracking new runner rotations."""
    chain = chain.lower().strip()
    sort_by = sort_by if sort_by in NEW_RUNNER_SORT_MODES else "score"
    chain_pool = (chain,)
    if watch_chains:
        parsed = _parse_chains(watch_chains)
        if chain in parsed:
            chain_pool = parsed
        else:
            chain_pool = (chain, *tuple(c for c in parsed if c != chain))

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
            controller = WatchKeyboardController(
                chains=chain_pool,
                sort_modes=NEW_RUNNER_SORT_MODES,
                initial_chain=chain,
                initial_sort_mode=sort_by,
            )
            cycle = 0
            status_message = "keys: 1-9 chain | s sort | j/k select | c copy"
            ranked: list[HotTokenCandidate] = []
            with Live(console=console, screen=screen, refresh_per_second=6) as live:
                while True:
                    cycle += 1
                    action = controller.poll(row_count=len(ranked))
                    if action:
                        if action["type"] == "chain":
                            status_message = f"active chain -> {action['value']}"
                        elif action["type"] == "sort":
                            status_message = f"sort mode -> {action['value']}"
                        elif action["type"] == "select":
                            status_message = f"selected row -> {int(action['value']) + 1}"

                    active_chain = controller.chain
                    active_sort_mode = controller.sort_mode
                    filters.chains = (active_chain,)
                    raw = await scanner.scan(filters)
                    ranked = _select_new_runners(
                        candidates=raw,
                        max_age_hours=max_age_hours,
                        include_unknown_age=include_unknown_age,
                        sort_by=active_sort_mode,
                        min_breakout_readiness=min_breakout_readiness,
                        min_relative_strength=min_relative_strength,
                        decay_filter=decay_filter,
                        min_half_life_minutes=min_half_life_minutes,
                        min_decay_ratio=min_decay_ratio,
                        max_vol_liq_ratio=max_vol_liq_ratio,
                        limit=limit,
                    )
                    controller.clamp_selection(row_count=len(ranked))

                    if action and action["type"] == "copy":
                        if ranked:
                            target = ranked[controller.selected_index]
                            payload = (
                                f"{target.pair.base_symbol}\n"
                                f"token={target.pair.base_address}\n"
                                f"pair={target.pair.pair_address}\n"
                                f"url={target.pair.pair_url}"
                            )
                            copied = copy_to_clipboard(payload)
                            status_message = (
                                f"copied {target.pair.base_symbol} ({target.pair.base_address[:8]}...)"
                                if copied
                                else "clipboard copy failed in this environment"
                            )
                        else:
                            status_message = "nothing to copy (no ranked rows)"

                    view = Group(
                        build_header(),
                        Columns(
                            [
                                render_new_runner_spotlight(
                                    ranked,
                                    chain=active_chain,
                                    max_age_hours=max_age_hours,
                                    limit=limit,
                                ),
                                render_flow_panel(ranked),
                            ]
                        ),
                        render_top_runner_cards(ranked, pulse=(cycle % 2 == 0)),
                        render_new_runners_table(
                            ranked,
                            chain=active_chain,
                            max_age_hours=max_age_hours,
                            limit=limit,
                            selected_index=controller.selected_index,
                        ),
                        render_rank_movers_table(
                            ranked,
                            previous_ranks=previous_ranks,
                            limit=limit,
                        ),
                        Panel(
                            (
                                f"refresh={interval:.1f}s | cycle={cycle} | chain={active_chain} "
                                f"| sort={active_sort_mode} | selected={controller.selected_index + 1 if ranked else '-'}\n"
                                f"{status_message}\n"
                                f"hotkeys: 1-9 chain switch ({','.join(chain_pool)}) | s sort | j/k select | c copy | Ctrl+C exit"
                            ),
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
