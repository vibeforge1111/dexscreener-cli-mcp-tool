from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from collections import UserDict

import httpx

from .models import HotTokenCandidate
from .state import ScanTask


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _candidate_line(candidate: HotTokenCandidate) -> str:
    p = candidate.pair
    return (
        f"{p.chain_id}:{p.base_symbol} score={candidate.score:.1f} "
        f"1h={p.price_change_h1:+.2f}% vol24=${p.volume_h24:,.0f} "
        f"liq=${p.liquidity_usd:,.0f} {p.pair_url}"
    )


class _SafeFormatDict(UserDict[str, str]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [str(value).strip()]


def _has_channels(alerts: dict[str, Any]) -> bool:
    return any(
        [
            alerts.get("webhook_url"),
            alerts.get("discord_webhook_url"),
            alerts.get("telegram_bot_token") and alerts.get("telegram_chat_id"),
        ]
    )


def _alert_context(task: ScanTask, candidates: list[HotTokenCandidate], now: datetime, top_n: int) -> dict[str, str]:
    top = candidates[0] if candidates else None
    top_lines = "\n".join(_candidate_line(c) for c in candidates[:top_n]) if candidates else "No candidates."
    top_chain = top.pair.chain_id if top else "n/a"
    top_token = top.pair.base_symbol if top else "n/a"
    top_score = f"{top.score:.2f}" if top else "0.00"
    top_h1 = f"{top.pair.price_change_h1:+.2f}%" if top else "0.00%"
    top_vol = f"${top.pair.volume_h24:,.0f}" if top else "$0"
    top_liq = f"${top.pair.liquidity_usd:,.0f}" if top else "$0"
    top_url = top.pair.pair_url if top else ""

    return {
        "timestamp": now.isoformat(),
        "task_name": task.name,
        "task_id": task.id,
        "result_count": str(len(candidates)),
        "top_chain": top_chain,
        "top_token": top_token,
        "top_score": top_score,
        "top_h1": top_h1,
        "top_vol": top_vol,
        "top_liq": top_liq,
        "top_url": top_url,
        "top_lines": top_lines,
    }


def _render_message(task: ScanTask, alerts: dict[str, Any], candidates: list[HotTokenCandidate], now: datetime) -> str:
    top_n = int(alerts.get("top_n", 3))
    context = _alert_context(task, candidates, now, top_n)
    default_template = (
        "[{task_name}] Hot token alert\n"
        "Top: {top_chain}:{top_token} score={top_score} 1h={top_h1} vol24={top_vol} liq={top_liq}\n"
        "{top_url}\n"
        "{top_lines}"
    )
    template = str(alerts.get("template", default_template))
    return template.format_map(_SafeFormatDict(context))


def _risk_gate(alerts: dict[str, Any], candidates: list[HotTokenCandidate]) -> tuple[bool, str]:
    if not candidates:
        return False, "no-candidates"
    top = candidates[0]

    min_liq = float(alerts.get("min_liquidity_usd", 0) or 0)
    if min_liq > 0 and top.pair.liquidity_usd < min_liq:
        return False, "risk:min-liquidity"

    max_ratio = float(alerts.get("max_vol_liq_ratio", 0) or 0)
    if max_ratio > 0:
        ratio = top.pair.volume_h24 / max(top.pair.liquidity_usd, 1.0)
        if ratio > max_ratio:
            return False, "risk:vol-liq-ratio"

    blocked_terms = [t.lower() for t in _as_list(alerts.get("blocked_terms"))]
    if blocked_terms:
        hay = f"{top.pair.base_symbol} {top.pair.base_name}".lower()
        if any(term in hay for term in blocked_terms):
            return False, "risk:blocked-term"

    blocked_chains = {c.lower() for c in _as_list(alerts.get("blocked_chains"))}
    if blocked_chains and top.pair.chain_id.lower() in blocked_chains:
        return False, "risk:blocked-chain"

    return True, "ok"


def should_send_alert(task: ScanTask, candidates: list[HotTokenCandidate], now: datetime) -> tuple[bool, str]:
    if not candidates:
        return False, "no-candidates"
    if not task.alerts:
        return False, "alerts-not-configured"

    alerts = task.alerts
    if not _has_channels(alerts):
        return False, "no-channel"

    min_score = float(alerts.get("min_score", 75.0))
    cooldown = int(alerts.get("cooldown_seconds", 900))
    top = candidates[0]

    if top.score < min_score:
        return False, "below-threshold"

    last_alert = _parse_iso(task.last_alert_at)
    if last_alert:
        elapsed = (now - last_alert).total_seconds()
        if elapsed < cooldown:
            return False, "cooldown"

    passes_risk, risk_reason = _risk_gate(alerts, candidates)
    if not passes_risk:
        return False, risk_reason

    return True, "ok"


async def _dispatch_channels(
    *,
    task: ScanTask,
    alerts: dict[str, Any],
    candidates: list[HotTokenCandidate],
    message: str,
    now: datetime,
    is_test: bool,
) -> dict[str, Any]:
    channels: dict[str, dict[str, Any]] = {}
    top = candidates[0] if candidates else None
    webhook_extra = alerts.get("webhook_extra")
    if not isinstance(webhook_extra, dict):
        webhook_extra = {}

    timeout = httpx.Timeout(10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        webhook = alerts.get("webhook_url")
        if webhook:
            try:
                resp = await client.post(
                    webhook,
                    json={
                        "event": "dexscreener.task.alert",
                        "test": is_test,
                        "task": {"id": task.id, "name": task.name},
                        "timestamp": now.isoformat(),
                        "message": message,
                        "top": {
                            "chainId": top.pair.chain_id if top else None,
                            "token": top.pair.base_symbol if top else None,
                            "score": top.score if top else None,
                            "priceChangeH1": top.pair.price_change_h1 if top else None,
                            "volumeH24": top.pair.volume_h24 if top else None,
                            "liquidityUsd": top.pair.liquidity_usd if top else None,
                            "pairUrl": top.pair.pair_url if top else None,
                        },
                        "results": [
                            {
                                "chainId": c.pair.chain_id,
                                "token": c.pair.base_symbol,
                                "tokenName": c.pair.base_name,
                                "score": c.score,
                                "priceChangeH1": c.pair.price_change_h1,
                                "volumeH24": c.pair.volume_h24,
                                "liquidityUsd": c.pair.liquidity_usd,
                                "pairUrl": c.pair.pair_url,
                            }
                            for c in candidates[:5]
                        ],
                        "extra": webhook_extra,
                    },
                )
                channels["webhook"] = {"ok": resp.is_success, "status": resp.status_code}
            except Exception as exc:
                channels["webhook"] = {"ok": False, "error": str(exc)}

        discord = alerts.get("discord_webhook_url")
        if discord:
            try:
                fields = []
                for c in candidates[:3]:
                    fields.append(
                        {
                            "name": f"{c.pair.chain_id}:{c.pair.base_symbol} ({c.score:.1f})",
                            "value": (
                                f"1h {c.pair.price_change_h1:+.2f}% | "
                                f"Vol24 ${c.pair.volume_h24:,.0f} | "
                                f"Liq ${c.pair.liquidity_usd:,.0f}\n{c.pair.pair_url}"
                            ),
                            "inline": False,
                        }
                    )
                resp = await client.post(
                    discord,
                    json={
                        "content": f"[{'TEST' if is_test else 'ALERT'}] {task.name}",
                        "embeds": [
                            {
                                "title": "Dexscreener Signal",
                                "description": message[:3000],
                                "color": 3066993 if not is_test else 3447003,
                                "fields": fields,
                                "timestamp": now.isoformat(),
                            }
                        ],
                    },
                )
                channels["discord"] = {"ok": resp.is_success, "status": resp.status_code}
            except Exception as exc:
                channels["discord"] = {"ok": False, "error": str(exc)}

        tg_token = alerts.get("telegram_bot_token")
        tg_chat = alerts.get("telegram_chat_id")
        if tg_token and tg_chat:
            try:
                url = f"https://api.telegram.org/bot{tg_token}/sendMessage"
                resp = await client.post(
                    url,
                    json={
                        "chat_id": str(tg_chat),
                        "text": message,
                        "parse_mode": "Markdown",
                        "disable_web_page_preview": True,
                    },
                )
                channels["telegram"] = {"ok": resp.is_success, "status": resp.status_code}
            except Exception as exc:
                channels["telegram"] = {"ok": False, "error": str(exc)}

    sent = any(v.get("ok") for v in channels.values()) if channels else False
    return {"sent": sent, "reason": "ok" if sent else "all-channels-failed", "channels": channels}


async def send_alerts(task: ScanTask, candidates: list[HotTokenCandidate]) -> dict[str, Any]:
    now = datetime.now(UTC)
    should, reason = should_send_alert(task, candidates, now)
    if not should:
        return {"sent": False, "reason": reason, "channels": {}}

    alerts = task.alerts or {}
    message = _render_message(task, alerts, candidates, now)
    return await _dispatch_channels(
        task=task,
        alerts=alerts,
        candidates=candidates,
        message=message,
        now=now,
        is_test=False,
    )


async def send_test_alert(task: ScanTask, candidates: list[HotTokenCandidate] | None = None) -> dict[str, Any]:
    now = datetime.now(UTC)
    alerts = task.alerts or {}
    if not alerts:
        return {"sent": False, "reason": "alerts-not-configured", "channels": {}}
    if not _has_channels(alerts):
        return {"sent": False, "reason": "no-channel", "channels": {}}
    message = f"[TEST] {task.name}\n" + _render_message(task, alerts, candidates or [], now)
    return await _dispatch_channels(
        task=task,
        alerts=alerts,
        candidates=candidates or [],
        message=message,
        now=now,
        is_test=True,
    )
