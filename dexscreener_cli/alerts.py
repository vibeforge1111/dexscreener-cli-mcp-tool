from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

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


def should_send_alert(task: ScanTask, candidates: list[HotTokenCandidate], now: datetime) -> tuple[bool, str]:
    if not candidates:
        return False, "no-candidates"
    if not task.alerts:
        return False, "alerts-not-configured"

    alerts = task.alerts
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

    has_channel = any(
        [
            alerts.get("webhook_url"),
            alerts.get("discord_webhook_url"),
            alerts.get("telegram_bot_token") and alerts.get("telegram_chat_id"),
        ]
    )
    if not has_channel:
        return False, "no-channel"

    return True, "ok"


async def send_alerts(task: ScanTask, candidates: list[HotTokenCandidate]) -> dict[str, Any]:
    now = datetime.now(UTC)
    should, reason = should_send_alert(task, candidates, now)
    if not should:
        return {"sent": False, "reason": reason, "channels": {}}

    alerts = task.alerts or {}
    top = candidates[0]
    top_lines = "\n".join(_candidate_line(c) for c in candidates[:3])
    message = f"[{task.name}] Hot token alert\n{top_lines}"

    channels: dict[str, dict[str, Any]] = {}
    timeout = httpx.Timeout(10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        webhook = alerts.get("webhook_url")
        if webhook:
            try:
                resp = await client.post(
                    webhook,
                    json={
                        "task": task.name,
                        "timestamp": now.isoformat(),
                        "topScore": top.score,
                        "topToken": top.pair.base_symbol,
                        "results": [
                            {
                                "chainId": c.pair.chain_id,
                                "token": c.pair.base_symbol,
                                "score": c.score,
                                "priceChangeH1": c.pair.price_change_h1,
                                "volumeH24": c.pair.volume_h24,
                                "liquidityUsd": c.pair.liquidity_usd,
                                "pairUrl": c.pair.pair_url,
                            }
                            for c in candidates[:5]
                        ],
                    },
                )
                channels["webhook"] = {"ok": resp.is_success, "status": resp.status_code}
            except Exception as exc:
                channels["webhook"] = {"ok": False, "error": str(exc)}

        discord = alerts.get("discord_webhook_url")
        if discord:
            try:
                resp = await client.post(discord, json={"content": message})
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
                        "disable_web_page_preview": True,
                    },
                )
                channels["telegram"] = {"ok": resp.is_success, "status": resp.status_code}
            except Exception as exc:
                channels["telegram"] = {"ok": False, "error": str(exc)}

    sent = any(v.get("ok") for v in channels.values()) if channels else False
    return {"sent": sent, "reason": "ok" if sent else "all-channels-failed", "channels": channels}
