"""Shared helpers for Telegram-origin customer invitations."""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from core.config import settings
from core.log_redaction import mask_mobile
from core.redis import get_redis_client
from core.server_routing import SERVER_FOREIGN, SERVER_IRAN, current_server, peer_server_url_for
from core.trade_forwarding import _tls_verify_setting
from core.utils import normalize_persian_numerals
from models.customer_relation import CustomerTier


logger = logging.getLogger(__name__)

CUSTOMER_INVITE_REQUIRED_SYNC_TABLES = (
    "users",
    "customer_relations",
    "accountant_relations",
    "invitations",
)
CUSTOMER_INVITE_SYNC_GRACE_SECONDS = 8.0
CUSTOMER_INVITE_SYNC_POLL_SECONDS = 1.0
CUSTOMER_INVITE_LOCK_TTL_SECONDS = 20


@dataclass(frozen=True)
class CustomerInviteSyncGateResult:
    ready: bool
    reason: str | None = None
    message: str | None = None


def normalize_customer_invite_mobile(value: object) -> str:
    normalized = normalize_persian_numerals(str(value or "").strip())
    if not normalized.startswith("09") or len(normalized) != 11 or not normalized.isdigit():
        raise ValueError("شماره موبایل نامعتبر است.")
    return normalized


def build_customer_invite_account_name(mobile_number: object) -> str:
    normalized_mobile = normalize_customer_invite_mobile(mobile_number)
    return f"customer_{normalized_mobile}"


def normalize_customer_invite_management_name(value: object) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("نام مشتری الزامی است.")
    if len(normalized) > 120:
        raise ValueError("نام مشتری نباید بیشتر از ۱۲۰ کاراکتر باشد.")
    return normalized


def normalize_customer_invite_tier(value: CustomerTier | str | None = None) -> CustomerTier:
    raw = getattr(value, "value", value) or CustomerTier.TIER_1.value
    try:
        tier = CustomerTier(str(raw).strip().lower())
    except ValueError as exc:
        raise ValueError("سطح مشتری نامعتبر است.") from exc
    if tier != CustomerTier.TIER_1:
        raise ValueError("دعوت مشتری از بات فقط برای مشتری سطح۱ فعال است.")
    return tier


def build_customer_invite_idempotency_key(
    *,
    source_server: str,
    owner_user_id: int,
    mobile_number: object,
    customer_tier: CustomerTier | str | None = None,
) -> str:
    normalized_mobile = normalize_customer_invite_mobile(mobile_number)
    tier = normalize_customer_invite_tier(customer_tier)
    material = f"{source_server}:{owner_user_id}:{normalized_mobile}:{tier.value}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return f"customer-invite:{digest[:40]}"


def customer_invite_lock_key(idempotency_key: str) -> str:
    digest = hashlib.sha256(str(idempotency_key or "").encode("utf-8")).hexdigest()
    return f"lock:customer-invite:{digest[:40]}"


def _safe_gate_message(reason: str) -> str:
    if reason == "not_foreign":
        return "دعوت مشتری از بات فقط روی سرور تلگرام فعال است."
    if reason == "missing_peer_url":
        return "ارتباط با سرور ایران برای دعوت مشتری تنظیم نشده است."
    if reason == "missing_observability_key":
        return "وضعیت همگام‌سازی برای دعوت مشتری قابل بررسی نیست."
    if reason == "iran_health_unreachable":
        return "ارتباط با سرور ایران برقرار نیست. کمی بعد دوباره تلاش کنید."
    if reason in {"iran_sync_dirty", "foreign_queue_dirty", "redis_unavailable"}:
        return "همگام‌سازی دو سرور کامل نیست. کمی بعد دوباره تلاش کنید."
    return "دعوت مشتری فعلاً در دسترس نیست. کمی بعد دوباره تلاش کنید."


def _health_required_tables_clean(payload: dict[str, Any]) -> bool:
    by_table = payload.get("unsynced_by_table")
    if not isinstance(by_table, dict):
        return False
    return all(int(by_table.get(table) or 0) == 0 for table in CUSTOMER_INVITE_REQUIRED_SYNC_TABLES)


def _health_queues_clean(payload: dict[str, Any]) -> bool:
    queues = payload.get("redis_queues")
    if not isinstance(queues, dict):
        return False
    return int(queues.get("sync:outbound") or 0) == 0 and int(queues.get("sync:retry") or 0) == 0


async def _foreign_local_sync_queues_clean() -> bool:
    try:
        redis_client = get_redis_client()
        outbound = int(await redis_client.llen("sync:outbound") or 0)
        retry = int(await redis_client.llen("sync:retry") or 0)
        return outbound == 0 and retry == 0
    except Exception as exc:
        logger.warning(
            "Could not inspect local foreign sync queues for customer invite",
            extra={
                "event": "customer_invite.sync_gate.local_queue_error",
                "error_type": type(exc).__name__,
                "server_mode": current_server(),
            },
        )
        return False


async def _fetch_iran_sync_health() -> tuple[dict[str, Any] | None, str | None]:
    target_url = peer_server_url_for(SERVER_IRAN)
    if not target_url:
        return None, "missing_peer_url"
    observability_key = (settings.observability_api_key or "").strip()
    if not observability_key:
        return None, "missing_observability_key"

    try:
        async with httpx.AsyncClient(
            timeout=settings.trade_forward_timeout_seconds,
            verify=_tls_verify_setting(),
        ) as client:
            response = await client.get(
                f"{target_url}/api/sync/health",
                headers={"X-Observability-Api-Key": observability_key},
            )
    except httpx.RequestError as exc:
        logger.warning(
            "Could not reach Iran sync health for customer invite",
            extra={
                "event": "customer_invite.sync_gate.iran_health_unreachable",
                "error_type": type(exc).__name__,
            },
        )
        return None, "iran_health_unreachable"

    if response.status_code != 200:
        logger.warning(
            "Iran sync health rejected customer invite gate check",
            extra={
                "event": "customer_invite.sync_gate.iran_health_status",
                "status_code": response.status_code,
            },
        )
        return None, "iran_health_unreachable"
    try:
        payload = response.json()
    except ValueError:
        return None, "iran_health_unreachable"
    return payload if isinstance(payload, dict) else None, None


async def check_customer_invite_sync_ready(
    *,
    wait_seconds: float = CUSTOMER_INVITE_SYNC_GRACE_SECONDS,
    poll_seconds: float = CUSTOMER_INVITE_SYNC_POLL_SECONDS,
) -> CustomerInviteSyncGateResult:
    if current_server() != SERVER_FOREIGN:
        return CustomerInviteSyncGateResult(False, "not_foreign", _safe_gate_message("not_foreign"))

    deadline = time.monotonic() + max(wait_seconds, 0.0)
    last_reason = "unknown"
    while True:
        if not await _foreign_local_sync_queues_clean():
            last_reason = "foreign_queue_dirty"
        else:
            iran_health, reason = await _fetch_iran_sync_health()
            if reason:
                last_reason = reason
            elif not iran_health or not bool(iran_health.get("redis_ok")):
                last_reason = "redis_unavailable"
            elif not _health_queues_clean(iran_health) or not _health_required_tables_clean(iran_health):
                last_reason = "iran_sync_dirty"
            else:
                return CustomerInviteSyncGateResult(True)

        if time.monotonic() >= deadline:
            return CustomerInviteSyncGateResult(False, last_reason, _safe_gate_message(last_reason))

        import asyncio

        await asyncio.sleep(max(poll_seconds, 0.1))


def log_customer_invite_input(owner_user_id: int, mobile_number: str) -> dict[str, Any]:
    return {
        "owner_user_id": owner_user_id,
        "mobile_masked": mask_mobile(mobile_number),
    }
