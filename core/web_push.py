"""Web Push notification helpers."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.enums import NotificationCategory, NotificationLevel
from models.notification import Notification
from models.push_subscription import PushSubscription

try:
    from pywebpush import WebPushException, webpush
except ImportError:  # pragma: no cover - exercised in environments without optional dependency
    WebPushException = None  # type: ignore[assignment]
    webpush = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

TERMINAL_PUSH_STATUS_CODES = {404, 410}


def hash_endpoint(endpoint: str) -> str:
    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()


def is_web_push_dependency_available() -> bool:
    return webpush is not None


def is_web_push_configured() -> bool:
    return bool(
        settings.web_push_enabled
        and settings.web_push_vapid_public_key
        and settings.web_push_vapid_private_key
        and settings.web_push_vapid_subject
        and is_web_push_dependency_available()
    )


def web_push_config_status() -> dict[str, Any]:
    missing: list[str] = []
    if not settings.web_push_enabled:
        missing.append("WEB_PUSH_ENABLED")
    if not settings.web_push_vapid_public_key:
        missing.append("WEB_PUSH_VAPID_PUBLIC_KEY")
    if not settings.web_push_vapid_private_key:
        missing.append("WEB_PUSH_VAPID_PRIVATE_KEY")
    if not settings.web_push_vapid_subject:
        missing.append("WEB_PUSH_VAPID_SUBJECT")
    if not is_web_push_dependency_available():
        missing.append("pywebpush")

    return {
        "enabled": len(missing) == 0,
        "public_key": settings.web_push_vapid_public_key if len(missing) == 0 else None,
        "missing": missing,
    }


def _enum_payload_value(value: Any) -> str:
    if isinstance(value, (NotificationCategory, NotificationLevel)):
        return value.value.lower()
    if isinstance(value, str):
        return value.lower()
    return ""


def _notification_title(category: Any) -> str:
    normalized = _enum_payload_value(category)
    if normalized == "trade":
        return "اعلان معامله"
    if normalized == "user":
        return "اعلان کاربری"
    if normalized == "system":
        return "پیام مدیریت"
    return "اعلان جدید"


def build_push_payload(
    *,
    title: str,
    body: str,
    route: str | None = None,
    tag: str | None = None,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload_data = dict(data or {})
    if route:
        payload_data["route"] = route

    return {
        "title": title[:120] or "اعلان جدید",
        "body": body[:500],
        "icon": "/pwa-192x192.png",
        "badge": "/pwa-192x192.png",
        "tag": tag,
        "route": route,
        "data": payload_data,
    }


def build_notification_push_payload(
    notification: Notification,
    extra_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    extra = dict(extra_payload or {})
    route = extra.get("route") if isinstance(extra.get("route"), str) else "/notifications"
    category = _enum_payload_value(notification.category)
    level = _enum_payload_value(notification.level)
    title = extra.get("title") if isinstance(extra.get("title"), str) else _notification_title(notification.category)

    return build_push_payload(
        title=title,
        body=notification.message,
        route=route,
        tag=f"notification:{notification.id}",
        data={
            "notification_id": notification.id,
            "category": category,
            "level": level,
        },
    )


def _subscription_info(subscription: PushSubscription) -> dict[str, Any]:
    return {
        "endpoint": subscription.endpoint,
        "keys": {
            "p256dh": subscription.p256dh,
            "auth": subscription.auth,
        },
    }


def _response_status_code(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    return int(status_code) if isinstance(status_code, int) else None


def _response_error_text(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    text = getattr(response, "text", None)
    if isinstance(text, str) and text.strip():
        return text.strip()[:500]
    return str(exc)[:500]


async def send_web_push_to_user(
    db: AsyncSession,
    user_id: int,
    payload: dict[str, Any],
) -> dict[str, int]:
    if not is_web_push_configured():
        return {"total": 0, "sent": 0, "failed": 0, "disabled": 0}

    result = await db.execute(
        select(PushSubscription).where(
            PushSubscription.user_id == user_id,
            PushSubscription.enabled == True,
        )
    )
    subscriptions = list(result.scalars().all())
    if not subscriptions:
        return {"total": 0, "sent": 0, "failed": 0, "disabled": 0}

    now = datetime.now(timezone.utc)
    sent = 0
    failed = 0
    disabled = 0
    data = json.dumps(payload, ensure_ascii=False)
    vapid_claims = {"sub": settings.web_push_vapid_subject}

    for subscription in subscriptions:
        try:
            await asyncio.to_thread(
                webpush,
                subscription_info=_subscription_info(subscription),
                data=data,
                vapid_private_key=settings.web_push_vapid_private_key,
                vapid_claims=dict(vapid_claims),
                ttl=max(0, int(settings.web_push_ttl_seconds)),
                timeout=max(1.0, float(settings.web_push_timeout_seconds)),
            )
        except Exception as exc:
            failed += 1
            status_code = _response_status_code(exc)
            subscription.last_failure_at = now
            subscription.failure_count = int(subscription.failure_count or 0) + 1
            subscription.last_error = _response_error_text(exc)
            if status_code in TERMINAL_PUSH_STATUS_CODES:
                subscription.enabled = False
                disabled += 1
            logger.warning(
                "Web Push delivery failed",
                extra={
                    "event": "web_push.delivery_failed",
                    "user_id": user_id,
                    "subscription_id": subscription.id,
                    "status_code": status_code,
                },
            )
            continue

        sent += 1
        subscription.last_success_at = now
        subscription.failure_count = 0
        subscription.last_error = None

    await db.commit()
    return {"total": len(subscriptions), "sent": sent, "failed": failed, "disabled": disabled}


async def send_notification_web_push(
    notification_id: int,
    extra_payload: dict[str, Any] | None = None,
) -> None:
    if not is_web_push_configured():
        return

    from core.db import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        notification = await db.get(Notification, notification_id)
        if notification is None:
            return

        payload = build_notification_push_payload(notification, extra_payload)
        await send_web_push_to_user(db, notification.user_id, payload)


def schedule_notification_web_push(
    notification_id: int,
    extra_payload: dict[str, Any] | None = None,
) -> None:
    if not is_web_push_configured():
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    task = loop.create_task(send_notification_web_push(notification_id, extra_payload))

    def _log_task_error(done_task: asyncio.Task) -> None:
        try:
            done_task.result()
        except Exception:
            logger.exception(
                "Web Push background delivery task failed",
                extra={"event": "web_push.background_task_failed", "notification_id": notification_id},
            )

    task.add_done_callback(_log_task_error)
