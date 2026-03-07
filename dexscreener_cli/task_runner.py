from __future__ import annotations

import re
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from .alerts import send_alerts
from .config import DEFAULT_CHAINS, ScanFilters
from .models import HotTokenCandidate
from .scanner import HotScanner
from .state import ScanTask, StateStore, TaskRunRecord, utc_now_iso

_MAX_ERROR_LEN = 500


def _sanitize_error(msg: str) -> str:
    """Strip file paths and truncate error messages for safe storage."""
    # Remove Windows and Unix file paths.
    cleaned = re.sub(r"[A-Za-z]:\\[\w\\\-. ]+", "<path>", msg)
    cleaned = re.sub(r"/(?:home|tmp|var|usr|etc|Users)/[\w/\\\-. ]+", "<path>", cleaned)
    return cleaned[:_MAX_ERROR_LEN]


def parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def task_due(task: ScanTask, now: datetime, default_interval_seconds: int) -> bool:
    interval = task.interval_seconds or default_interval_seconds
    last_run = parse_iso(task.last_run_at)
    if last_run is None:
        return True
    return (now - last_run).total_seconds() >= interval


def task_filters(task: ScanTask, store: StateStore) -> ScanFilters:
    filters = ScanFilters(chains=DEFAULT_CHAINS)
    if task.preset:
        preset = store.get_preset(task.preset)
        if preset:
            filters = preset.to_filters()
    if task.filters:
        payload = task.filters
        if payload.get("chains"):
            filters.chains = tuple(payload["chains"])
        if payload.get("limit") is not None:
            filters.limit = int(payload["limit"])
        if payload.get("min_liquidity_usd") is not None:
            filters.min_liquidity_usd = float(payload["min_liquidity_usd"])
        if payload.get("min_volume_h24_usd") is not None:
            filters.min_volume_h24_usd = float(payload["min_volume_h24_usd"])
        if payload.get("min_txns_h1") is not None:
            filters.min_txns_h1 = int(payload["min_txns_h1"])
        if payload.get("min_price_change_h1") is not None:
            filters.min_price_change_h1 = float(payload["min_price_change_h1"])
    return filters


def _record_run(
    *,
    store: StateStore,
    task: ScanTask,
    mode: str,
    started_at: str,
    elapsed_ms: int,
    status: str,
    candidates: list[HotTokenCandidate],
    alert_result: dict[str, Any],
    error: str | None = None,
) -> TaskRunRecord:
    top = candidates[0] if candidates else None
    run = TaskRunRecord.create(
        task_id=task.id,
        task_name=task.name,
        mode=mode,
        started_at=started_at,
        finished_at=utc_now_iso(),
        duration_ms=elapsed_ms,
        status=status,
        result_count=len(candidates),
        top_chain=top.pair.chain_id if top else None,
        top_token=top.pair.base_symbol if top else None,
        top_score=top.score if top else None,
        alert_sent=bool(alert_result.get("sent", False)),
        alert_reason=str(alert_result.get("reason", "n/a")),
        error=error,
    )
    store.append_run(run)
    return run


async def execute_task_once(
    *,
    store: StateStore,
    scanner: HotScanner,
    task: ScanTask,
    mode: str,
    fire_alerts: bool = True,
    mark_running: bool = False,
    block_on_error: bool = False,
) -> dict[str, Any]:
    started_at = utc_now_iso()
    t0 = perf_counter()
    candidates: list[HotTokenCandidate] = []
    alert_result: dict[str, Any] = {"sent": False, "reason": "disabled", "channels": {}}

    if mark_running:
        store.update_task_status(task.id, status="running")

    try:
        filters = task_filters(task, store)
        candidates = await scanner.scan(filters)
        store.touch_task_run(task.id)

        if fire_alerts:
            refreshed = store.get_task(task.id) or task
            alert_result = await send_alerts(refreshed, candidates)
            if alert_result.get("sent"):
                store.touch_task_alert(task.id)

        if mark_running:
            store.update_task_status(task.id, status="todo")

        run = _record_run(
            store=store,
            task=task,
            mode=mode,
            started_at=started_at,
            elapsed_ms=int((perf_counter() - t0) * 1000),
            status="ok",
            candidates=candidates,
            alert_result=alert_result,
        )
        return {
            "ok": True,
            "task": (store.get_task(task.id) or task).to_dict(),
            "filters": {
                "chains": list(filters.chains),
                "limit": filters.limit,
                "min_liquidity_usd": filters.min_liquidity_usd,
                "min_volume_h24_usd": filters.min_volume_h24_usd,
                "min_txns_h1": filters.min_txns_h1,
                "min_price_change_h1": filters.min_price_change_h1,
            },
            "candidates": candidates,
            "alert": alert_result,
            "run": run.to_dict(),
        }
    except Exception as exc:
        if mark_running:
            if block_on_error:
                store.update_task_status(task.id, status="blocked")
            else:
                store.update_task_status(task.id, status="todo")
        run = _record_run(
            store=store,
            task=task,
            mode=mode,
            started_at=started_at,
            elapsed_ms=int((perf_counter() - t0) * 1000),
            status="error",
            candidates=candidates,
            alert_result=alert_result,
            error=_sanitize_error(str(exc)),
        )
        return {
            "ok": False,
            "task": (store.get_task(task.id) or task).to_dict(),
            "filters": None,
            "candidates": candidates,
            "alert": alert_result,
            "run": run.to_dict(),
            "error": _sanitize_error(str(exc)),
        }


def select_due_tasks(
    *,
    store: StateStore,
    task_name_or_id: str | None,
    all_tasks: bool,
    default_interval_seconds: int,
) -> list[ScanTask]:
    rows = store.list_tasks()
    if task_name_or_id:
        key = task_name_or_id.lower()
        rows = [t for t in rows if t.id.lower() == key or t.name.lower() == key]
    if all_tasks:
        rows = [t for t in rows if t.status != "blocked"]
    now = datetime.now(UTC)
    due: list[ScanTask] = []
    for row in rows:
        if row.status in {"blocked", "done"}:
            continue
        if task_due(row, now, default_interval_seconds):
            due.append(row)
    return due
