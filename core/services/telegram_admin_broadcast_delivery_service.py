"""Foreign-only delivery primitives for Telegram admin broadcasts."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core import telegram_gateway
from core.server_routing import SERVER_FOREIGN
from core.services.bot_access_policy import evaluate_bot_access
from core.services.telegram_admin_broadcast_service import validate_telegram_admin_broadcast_content
from core.utils import utc_now
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeConfigurationError,
    assert_telegram_provider_execution_authority,
    configured_telegram_delivery_runtime,
)
from models.telegram_admin_broadcast import (
    TERMINAL_TELEGRAM_ADMIN_BROADCAST_RECEIPT_STATUSES,
    TelegramAdminBroadcast,
    TelegramAdminBroadcastReceipt,
    TelegramAdminBroadcastReceiptStatus,
    TelegramAdminBroadcastStatus,
)
from models.user import User


TELEGRAM_ADMIN_BROADCAST_WORKER_ID = "telegram-admin-broadcast"

TELEGRAM_ADMIN_BROADCAST_DELIVERY_STATUS_NO_RECEIPT = "no_receipt"
TELEGRAM_ADMIN_BROADCAST_DELIVERY_STATUS_SENT = "sent"
TELEGRAM_ADMIN_BROADCAST_DELIVERY_STATUS_RETRY_PENDING = "retry_pending"
TELEGRAM_ADMIN_BROADCAST_DELIVERY_STATUS_SKIPPED = "skipped"
TELEGRAM_ADMIN_BROADCAST_DELIVERY_STATUS_TERMINAL_FAILED = "terminal_failed"
TELEGRAM_ADMIN_BROADCAST_DELIVERY_STATUS_ALREADY_TERMINAL = "already_terminal"
TELEGRAM_ADMIN_BROADCAST_DELIVERY_STATUS_BLOCKED_WRONG_SERVER = "blocked_wrong_server"

MIN_RETRY_DELAY_SECONDS = 1
MAX_RETRY_DELAY_SECONDS = 300
MAX_RETRY_JITTER_SECONDS = 5
MAX_RETRY_ATTEMPTS = 8

_RETRYABLE_ERROR_CLASSES = {
    "connecterror",
    "connecttimeout",
    "networkerror",
    "pooltimeout",
    "readerror",
    "readtimeout",
    "remoteprotocolerror",
    "timeouterror",
    "writeerror",
    "writetimeout",
}

_USER_UNREACHABLE_PATTERNS = (
    "bot was blocked",
    "blocked by the user",
    "bot can't initiate conversation",
    "bot cannot initiate conversation",
    "chat not found",
    "user is deactivated",
    "user not found",
    "bot was kicked",
    "have no rights",
    "forbidden: bot",
)

_MALFORMED_PAYLOAD_PATTERNS = (
    "message text is empty",
    "message is too long",
    "text must be non-empty",
    "can't parse entities",
    "parse mode",
    "entity",
)


TelegramSendCallable = Callable[..., Awaitable[telegram_gateway.TelegramGatewayResult]]


def _assert_legacy_direct_delivery_owner() -> None:
    assert_telegram_provider_execution_authority()
    runtime = configured_telegram_delivery_runtime()
    if not runtime.legacy_workers_enabled or runtime.queue_worker_enabled:
        raise TelegramDeliveryRuntimeConfigurationError(
            "legacy_admin_broadcast_direct_sender_is_not_runtime_owner"
        )


@dataclass(frozen=True, slots=True)
class TelegramAdminBroadcastFailureClassification:
    status: TelegramAdminBroadcastReceiptStatus
    reason: str
    retry_after_seconds: int | None = None
    error_class: str | None = None
    error_message: str | None = None
    alert_required: bool = False


@dataclass(frozen=True, slots=True)
class TelegramAdminBroadcastDeliveryResult:
    status: str
    current_server: str
    receipt: TelegramAdminBroadcastReceipt | None = None
    broadcast_id: int | None = None
    recipient_user_id: int | None = None
    telegram_message_id: int | None = None
    reason: str | None = None
    retry_after_seconds: int | None = None
    alert_required: bool = False


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _coerce_int(value: Any) -> int | None:
    try:
        coerced = int(value)
    except (TypeError, ValueError):
        return None
    return coerced if coerced > 0 else None


def _is_terminal(receipt: TelegramAdminBroadcastReceipt | Any | None) -> bool:
    return _enum_value(getattr(receipt, "status", None)) in TERMINAL_TELEGRAM_ADMIN_BROADCAST_RECEIPT_STATUSES


def _sanitize_error_message(value: Any, *, max_length: int = 500) -> str | None:
    text = " ".join(str(value or "").split())
    return text[:max_length] if text else None


def _telegram_error_text(result: telegram_gateway.TelegramGatewayResult) -> str:
    parts: list[str] = []
    if result.response_text:
        parts.append(str(result.response_text))
    if result.response_json:
        for key in ("description", "message", "error"):
            raw = result.response_json.get(key)
            if raw:
                parts.append(str(raw))
    if result.error:
        parts.append(str(result.error))
    return " ".join(parts).lower()


def _retry_after_from_result(result: telegram_gateway.TelegramGatewayResult) -> int | None:
    raw_retry_after = None
    if isinstance(result.response_json, Mapping):
        parameters = result.response_json.get("parameters")
        if isinstance(parameters, Mapping):
            raw_retry_after = parameters.get("retry_after")
    retry_after = _coerce_int(raw_retry_after)
    if retry_after is None:
        return None
    return min(MAX_RETRY_DELAY_SECONDS, max(MIN_RETRY_DELAY_SECONDS, retry_after))


def _stable_retry_jitter_seconds(
    *,
    receipt: TelegramAdminBroadcastReceipt | Any,
    attempt_count: int,
    max_jitter_seconds: int = MAX_RETRY_JITTER_SECONDS,
) -> int:
    if max_jitter_seconds <= 0:
        return 0
    seed = (
        f"{getattr(receipt, 'id', '')}:"
        f"{getattr(receipt, 'broadcast_id', '')}:"
        f"{getattr(receipt, 'recipient_user_id', '')}:"
        f"{attempt_count}"
    )
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % (max_jitter_seconds + 1)


def bounded_retry_delay_seconds(
    classification: TelegramAdminBroadcastFailureClassification,
    *,
    receipt: TelegramAdminBroadcastReceipt | Any,
    max_jitter_seconds: int = MAX_RETRY_JITTER_SECONDS,
) -> int:
    if classification.retry_after_seconds is not None:
        base_delay = classification.retry_after_seconds
    else:
        attempt_count = max(1, int(getattr(receipt, "attempt_count", 0) or 0))
        base_delay = min(MAX_RETRY_DELAY_SECONDS, 2 ** min(attempt_count, 8))

    jitter = _stable_retry_jitter_seconds(
        receipt=receipt,
        attempt_count=max(1, int(getattr(receipt, "attempt_count", 0) or 0)),
        max_jitter_seconds=max_jitter_seconds,
    )
    return min(MAX_RETRY_DELAY_SECONDS, max(MIN_RETRY_DELAY_SECONDS, base_delay + jitter))


def retry_attempts_exhausted(
    receipt: TelegramAdminBroadcastReceipt | Any,
    *,
    max_attempts: int = MAX_RETRY_ATTEMPTS,
) -> bool:
    return int(getattr(receipt, "attempt_count", 0) or 0) >= max(1, int(max_attempts))


def classify_telegram_admin_broadcast_failure(
    result: telegram_gateway.TelegramGatewayResult,
) -> TelegramAdminBroadcastFailureClassification:
    status_code = result.status_code
    if status_code is None and isinstance(result.response_json, Mapping):
        status_code = _coerce_int(result.response_json.get("error_code"))

    error_text = _telegram_error_text(result)
    error_class = result.error or (f"HTTP{status_code}" if status_code is not None else "TelegramUnknownError")
    error_message = _sanitize_error_message(result.response_text or error_text or result.error)

    if result.error == "missing_bot_token":
        return TelegramAdminBroadcastFailureClassification(
            status=TelegramAdminBroadcastReceiptStatus.TERMINAL_FAILED,
            reason="telegram_missing_bot_token",
            error_class=error_class,
            error_message=error_message,
            alert_required=True,
        )
    if status_code == 429:
        return TelegramAdminBroadcastFailureClassification(
            status=TelegramAdminBroadcastReceiptStatus.RETRYABLE_FAILED,
            reason="telegram_rate_limited",
            retry_after_seconds=_retry_after_from_result(result),
            error_class=error_class,
            error_message=error_message,
        )
    if status_code is not None and 500 <= status_code <= 599:
        return TelegramAdminBroadcastFailureClassification(
            status=TelegramAdminBroadcastReceiptStatus.RETRYABLE_FAILED,
            reason="telegram_server_error",
            error_class=error_class,
            error_message=error_message,
        )
    if str(result.error or "").lower() in _RETRYABLE_ERROR_CLASSES:
        return TelegramAdminBroadcastFailureClassification(
            status=TelegramAdminBroadcastReceiptStatus.RETRYABLE_FAILED,
            reason="telegram_transport_error",
            error_class=error_class,
            error_message=error_message,
        )
    if any(pattern in error_text for pattern in _USER_UNREACHABLE_PATTERNS):
        return TelegramAdminBroadcastFailureClassification(
            status=TelegramAdminBroadcastReceiptStatus.SKIPPED,
            reason="telegram_user_unreachable",
            error_class=error_class,
            error_message=error_message,
        )
    if status_code in {401, 404} or "unauthorized" in error_text or "invalid token" in error_text:
        return TelegramAdminBroadcastFailureClassification(
            status=TelegramAdminBroadcastReceiptStatus.TERMINAL_FAILED,
            reason="telegram_invalid_bot_config",
            error_class=error_class,
            error_message=error_message,
            alert_required=True,
        )
    if status_code == 400 and any(pattern in error_text for pattern in _MALFORMED_PAYLOAD_PATTERNS):
        return TelegramAdminBroadcastFailureClassification(
            status=TelegramAdminBroadcastReceiptStatus.TERMINAL_FAILED,
            reason="telegram_malformed_payload",
            error_class=error_class,
            error_message=error_message,
            alert_required=True,
        )
    if status_code is None:
        return TelegramAdminBroadcastFailureClassification(
            status=TelegramAdminBroadcastReceiptStatus.RETRYABLE_FAILED,
            reason="telegram_unknown_transport_failure",
            error_class=error_class,
            error_message=error_message,
        )
    return TelegramAdminBroadcastFailureClassification(
        status=TelegramAdminBroadcastReceiptStatus.TERMINAL_FAILED,
        reason="telegram_unhandled_error",
        error_class=error_class,
        error_message=error_message,
        alert_required=True,
    )


async def _mark_receipt(
    db: AsyncSession,
    *,
    receipt: TelegramAdminBroadcastReceipt,
    status: TelegramAdminBroadcastReceiptStatus,
    current_time: datetime,
    reason: str,
    telegram_id_at_send: int | None = None,
    telegram_message_id: int | None = None,
    retry_after_seconds: int | None = None,
    error_class: str | None = None,
    error_message: str | None = None,
) -> None:
    receipt.status = status
    receipt.reason = reason
    receipt.telegram_id_at_send = telegram_id_at_send
    receipt.telegram_message_id = telegram_message_id
    receipt.last_error_class = error_class
    receipt.last_error_message = error_message
    receipt.worker_id = None
    receipt.lease_until = None
    receipt.updated_at = current_time
    if status == TelegramAdminBroadcastReceiptStatus.RETRYABLE_FAILED:
        delay = max(MIN_RETRY_DELAY_SECONDS, int(retry_after_seconds or MIN_RETRY_DELAY_SECONDS))
        receipt.next_retry_at = current_time + timedelta(seconds=min(MAX_RETRY_DELAY_SECONDS, delay))
        receipt.terminal_at = None
    else:
        receipt.next_retry_at = None
        receipt.terminal_at = current_time
        if status == TelegramAdminBroadcastReceiptStatus.SENT:
            receipt.sent_at = current_time
    await db.flush()


async def claim_next_telegram_admin_broadcast_receipt(
    db: AsyncSession,
    *,
    current_server: str,
    lease_seconds: int,
    worker_id: str = TELEGRAM_ADMIN_BROADCAST_WORKER_ID,
    now: datetime | None = None,
) -> TelegramAdminBroadcastReceipt | None:
    if str(current_server or "").strip().lower() != SERVER_FOREIGN:
        return None

    current_time = now or utc_now()
    stmt = (
        select(TelegramAdminBroadcastReceipt)
        .where(
            TelegramAdminBroadcastReceipt.status.in_(
                [
                    TelegramAdminBroadcastReceiptStatus.PENDING,
                    TelegramAdminBroadcastReceiptStatus.RETRYABLE_FAILED,
                ]
            ),
            TelegramAdminBroadcastReceipt.queue_job_id.is_(None),
            TelegramAdminBroadcastReceipt.queue_handed_off_at.is_(None),
            TelegramAdminBroadcastReceipt.worker_id.is_(None),
            TelegramAdminBroadcastReceipt.lease_until.is_(None),
            or_(
                TelegramAdminBroadcastReceipt.next_retry_at.is_(None),
                TelegramAdminBroadcastReceipt.next_retry_at <= current_time,
            ),
        )
        .order_by(TelegramAdminBroadcastReceipt.next_retry_at.asc().nullsfirst(), TelegramAdminBroadcastReceipt.id.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    result = await db.execute(stmt)
    receipt = result.scalar_one_or_none()
    if receipt is None:
        return None
    receipt.status = TelegramAdminBroadcastReceiptStatus.SENDING
    receipt.worker_id = worker_id
    receipt.lease_until = current_time + timedelta(seconds=max(1, int(lease_seconds)))
    receipt.attempt_count = int(receipt.attempt_count or 0) + 1
    receipt.updated_at = current_time

    broadcast = await db.get(TelegramAdminBroadcast, int(receipt.broadcast_id))
    if broadcast is not None and broadcast.status == TelegramAdminBroadcastStatus.QUEUED:
        broadcast.status = TelegramAdminBroadcastStatus.RUNNING
        broadcast.updated_at = current_time
    await db.flush()
    return receipt


async def recover_expired_telegram_admin_broadcast_leases(
    db: AsyncSession,
    *,
    current_server: str,
    max_rows: int = 100,
    now: datetime | None = None,
) -> list[TelegramAdminBroadcastReceipt]:
    if str(current_server or "").strip().lower() != SERVER_FOREIGN:
        return []
    current_time = now or utc_now()
    stmt = (
        select(TelegramAdminBroadcastReceipt)
        .where(
            TelegramAdminBroadcastReceipt.status == TelegramAdminBroadcastReceiptStatus.SENDING,
            TelegramAdminBroadcastReceipt.lease_until.is_not(None),
            TelegramAdminBroadcastReceipt.lease_until <= current_time,
        )
        .order_by(TelegramAdminBroadcastReceipt.lease_until.asc(), TelegramAdminBroadcastReceipt.id.asc())
        .with_for_update(skip_locked=True)
        .limit(max(1, int(max_rows)))
    )
    result = await db.execute(stmt)
    receipts = list(result.scalars().all())
    for receipt in receipts:
        receipt.status = TelegramAdminBroadcastReceiptStatus.RETRYABLE_FAILED
        receipt.reason = "lease_expired"
        receipt.worker_id = None
        receipt.lease_until = None
        receipt.next_retry_at = current_time
        receipt.updated_at = current_time
    if receipts:
        await db.flush()
    return receipts


async def finalize_telegram_admin_broadcast_status(
    db: AsyncSession,
    *,
    broadcast_id: int,
    now: datetime | None = None,
) -> TelegramAdminBroadcastStatus | None:
    broadcast = await db.get(TelegramAdminBroadcast, int(broadcast_id))
    if broadcast is None:
        return None

    count_result = await db.execute(
        select(TelegramAdminBroadcastReceipt.status, func.count(TelegramAdminBroadcastReceipt.id))
        .where(TelegramAdminBroadcastReceipt.broadcast_id == int(broadcast_id))
        .group_by(TelegramAdminBroadcastReceipt.status)
    )
    counts = {_enum_value(status): int(count) for status, count in count_result.all()}
    total = sum(counts.values())
    current_time = now or utc_now()
    terminal_count = sum(counts.get(status, 0) for status in TERMINAL_TELEGRAM_ADMIN_BROADCAST_RECEIPT_STATUSES)

    if total == 0:
        new_status = TelegramAdminBroadcastStatus.COMPLETED
    elif terminal_count < total:
        new_status = TelegramAdminBroadcastStatus.RUNNING
    else:
        failed_count = counts.get(TelegramAdminBroadcastReceiptStatus.TERMINAL_FAILED.value, 0)
        skipped_count = counts.get(TelegramAdminBroadcastReceiptStatus.SKIPPED.value, 0)
        sent_count = counts.get(TelegramAdminBroadcastReceiptStatus.SENT.value, 0)
        if sent_count == 0 and failed_count > 0:
            new_status = TelegramAdminBroadcastStatus.FAILED
        elif failed_count or skipped_count:
            new_status = TelegramAdminBroadcastStatus.COMPLETED_WITH_ERRORS
        else:
            new_status = TelegramAdminBroadcastStatus.COMPLETED

    if broadcast.status != new_status:
        broadcast.status = new_status
        broadcast.updated_at = current_time
        if new_status in {
            TelegramAdminBroadcastStatus.COMPLETED,
            TelegramAdminBroadcastStatus.COMPLETED_WITH_ERRORS,
            TelegramAdminBroadcastStatus.FAILED,
        }:
            broadcast.completed_at = current_time
        await db.flush()
    return new_status


async def deliver_claimed_telegram_admin_broadcast_receipt(
    db: AsyncSession,
    receipt: TelegramAdminBroadcastReceipt,
    *,
    current_server: str,
    gateway_send: TelegramSendCallable = telegram_gateway.send_message,
    bot_token: str | None = None,
    now: datetime | None = None,
) -> TelegramAdminBroadcastDeliveryResult:
    _assert_legacy_direct_delivery_owner()
    normalized_server = str(current_server or "").strip().lower()
    broadcast_id = _coerce_int(getattr(receipt, "broadcast_id", None))
    recipient_user_id = _coerce_int(getattr(receipt, "recipient_user_id", None))
    if normalized_server != SERVER_FOREIGN:
        return TelegramAdminBroadcastDeliveryResult(
            status=TELEGRAM_ADMIN_BROADCAST_DELIVERY_STATUS_BLOCKED_WRONG_SERVER,
            current_server=normalized_server,
            receipt=receipt,
            broadcast_id=broadcast_id,
            recipient_user_id=recipient_user_id,
            reason="telegram_foreign_only",
        )

    current_time = now or utc_now()
    if _is_terminal(receipt):
        return TelegramAdminBroadcastDeliveryResult(
            status=TELEGRAM_ADMIN_BROADCAST_DELIVERY_STATUS_ALREADY_TERMINAL,
            current_server=normalized_server,
            receipt=receipt,
            broadcast_id=broadcast_id,
            recipient_user_id=recipient_user_id,
            telegram_message_id=_coerce_int(getattr(receipt, "telegram_message_id", None)),
            reason=getattr(receipt, "reason", None),
        )

    broadcast = await db.get(TelegramAdminBroadcast, int(receipt.broadcast_id))
    if broadcast is None:
        await _mark_receipt(
            db,
            receipt=receipt,
            status=TelegramAdminBroadcastReceiptStatus.TERMINAL_FAILED,
            current_time=current_time,
            reason="broadcast_missing",
            error_class="BroadcastMissing",
            error_message="broadcast_missing",
        )
        return TelegramAdminBroadcastDeliveryResult(
            status=TELEGRAM_ADMIN_BROADCAST_DELIVERY_STATUS_TERMINAL_FAILED,
            current_server=normalized_server,
            receipt=receipt,
            broadcast_id=broadcast_id,
            recipient_user_id=recipient_user_id,
            reason="broadcast_missing",
            alert_required=True,
        )

    try:
        message = validate_telegram_admin_broadcast_content(broadcast.content)
    except Exception:
        await _mark_receipt(
            db,
            receipt=receipt,
            status=TelegramAdminBroadcastReceiptStatus.TERMINAL_FAILED,
            current_time=current_time,
            reason="broadcast_invalid_content",
            error_class="BroadcastPayloadError",
            error_message="broadcast_invalid_content",
        )
        await finalize_telegram_admin_broadcast_status(db, broadcast_id=int(receipt.broadcast_id), now=current_time)
        return TelegramAdminBroadcastDeliveryResult(
            status=TELEGRAM_ADMIN_BROADCAST_DELIVERY_STATUS_TERMINAL_FAILED,
            current_server=normalized_server,
            receipt=receipt,
            broadcast_id=broadcast_id,
            recipient_user_id=recipient_user_id,
            reason="broadcast_invalid_content",
            alert_required=True,
        )

    user = await db.get(User, int(receipt.recipient_user_id))
    telegram_id = _coerce_int(getattr(user, "telegram_id", None))
    if user is None or telegram_id is None:
        reason = "telegram_user_missing_current" if user is None else "telegram_unlinked_current"
        await _mark_receipt(
            db,
            receipt=receipt,
            status=TelegramAdminBroadcastReceiptStatus.SKIPPED,
            current_time=current_time,
            reason=reason,
            error_class="TelegramUserUnavailable",
            error_message=reason,
        )
        await finalize_telegram_admin_broadcast_status(db, broadcast_id=int(receipt.broadcast_id), now=current_time)
        return TelegramAdminBroadcastDeliveryResult(
            status=TELEGRAM_ADMIN_BROADCAST_DELIVERY_STATUS_SKIPPED,
            current_server=normalized_server,
            receipt=receipt,
            broadcast_id=broadcast_id,
            recipient_user_id=recipient_user_id,
            reason=reason,
        )

    access_decision = await evaluate_bot_access(db, user)
    if not access_decision.allowed:
        reason = access_decision.reason or "bot_access_denied_current"
        await _mark_receipt(
            db,
            receipt=receipt,
            status=TelegramAdminBroadcastReceiptStatus.SKIPPED,
            current_time=current_time,
            reason=reason,
            error_class="BotAccessDenied",
            error_message=reason,
        )
        await finalize_telegram_admin_broadcast_status(db, broadcast_id=int(receipt.broadcast_id), now=current_time)
        return TelegramAdminBroadcastDeliveryResult(
            status=TELEGRAM_ADMIN_BROADCAST_DELIVERY_STATUS_SKIPPED,
            current_server=normalized_server,
            receipt=receipt,
            broadcast_id=broadcast_id,
            recipient_user_id=recipient_user_id,
            reason=reason,
        )

    correlation_key = (
        f"telegram-admin-broadcast:{int(receipt.broadcast_id)}:{int(receipt.recipient_user_id)}:"
        f"attempt:{int(receipt.attempt_count or 0)}"
    )
    gateway_result = await gateway_send(
        telegram_id,
        message,
        parse_mode=None,
        bot_token=bot_token,
        idempotency_key=correlation_key,
    )
    if gateway_result.ok:
        telegram_message_id = gateway_result.message_id
        await _mark_receipt(
            db,
            receipt=receipt,
            status=TelegramAdminBroadcastReceiptStatus.SENT,
            current_time=current_time,
            reason="sent",
            telegram_id_at_send=telegram_id,
            telegram_message_id=telegram_message_id,
        )
        await finalize_telegram_admin_broadcast_status(db, broadcast_id=int(receipt.broadcast_id), now=current_time)
        return TelegramAdminBroadcastDeliveryResult(
            status=TELEGRAM_ADMIN_BROADCAST_DELIVERY_STATUS_SENT,
            current_server=normalized_server,
            receipt=receipt,
            broadcast_id=broadcast_id,
            recipient_user_id=recipient_user_id,
            telegram_message_id=telegram_message_id,
            reason="sent",
        )

    classification = classify_telegram_admin_broadcast_failure(gateway_result)
    if classification.status == TelegramAdminBroadcastReceiptStatus.RETRYABLE_FAILED and retry_attempts_exhausted(receipt):
        await _mark_receipt(
            db,
            receipt=receipt,
            status=TelegramAdminBroadcastReceiptStatus.TERMINAL_FAILED,
            current_time=current_time,
            reason="telegram_retry_exhausted",
            telegram_id_at_send=telegram_id,
            error_class=classification.error_class,
            error_message=classification.error_message,
        )
        await finalize_telegram_admin_broadcast_status(db, broadcast_id=int(receipt.broadcast_id), now=current_time)
        return TelegramAdminBroadcastDeliveryResult(
            status=TELEGRAM_ADMIN_BROADCAST_DELIVERY_STATUS_TERMINAL_FAILED,
            current_server=normalized_server,
            receipt=receipt,
            broadcast_id=broadcast_id,
            recipient_user_id=recipient_user_id,
            reason="telegram_retry_exhausted",
            alert_required=True,
        )

    retry_delay_seconds = (
        bounded_retry_delay_seconds(classification, receipt=receipt)
        if classification.status == TelegramAdminBroadcastReceiptStatus.RETRYABLE_FAILED
        else classification.retry_after_seconds
    )
    await _mark_receipt(
        db,
        receipt=receipt,
        status=classification.status,
        current_time=current_time,
        reason=classification.reason,
        telegram_id_at_send=telegram_id,
        retry_after_seconds=retry_delay_seconds,
        error_class=classification.error_class,
        error_message=classification.error_message,
    )
    await finalize_telegram_admin_broadcast_status(db, broadcast_id=int(receipt.broadcast_id), now=current_time)
    if classification.status == TelegramAdminBroadcastReceiptStatus.RETRYABLE_FAILED:
        result_status = TELEGRAM_ADMIN_BROADCAST_DELIVERY_STATUS_RETRY_PENDING
    elif classification.status == TelegramAdminBroadcastReceiptStatus.SKIPPED:
        result_status = TELEGRAM_ADMIN_BROADCAST_DELIVERY_STATUS_SKIPPED
    else:
        result_status = TELEGRAM_ADMIN_BROADCAST_DELIVERY_STATUS_TERMINAL_FAILED
    return TelegramAdminBroadcastDeliveryResult(
        status=result_status,
        current_server=normalized_server,
        receipt=receipt,
        broadcast_id=broadcast_id,
        recipient_user_id=recipient_user_id,
        reason=classification.reason,
        retry_after_seconds=retry_delay_seconds,
        alert_required=classification.alert_required,
    )


async def claim_and_deliver_next_telegram_admin_broadcast_receipt(
    db: AsyncSession,
    *,
    current_server: str,
    lease_seconds: int,
    gateway_send: TelegramSendCallable = telegram_gateway.send_message,
    bot_token: str | None = None,
) -> TelegramAdminBroadcastDeliveryResult:
    receipt = await claim_next_telegram_admin_broadcast_receipt(
        db,
        current_server=current_server,
        lease_seconds=lease_seconds,
    )
    if receipt is None:
        return TelegramAdminBroadcastDeliveryResult(
            status=TELEGRAM_ADMIN_BROADCAST_DELIVERY_STATUS_NO_RECEIPT,
            current_server=str(current_server or "").strip().lower(),
        )
    return await deliver_claimed_telegram_admin_broadcast_receipt(
        db,
        receipt,
        current_server=current_server,
        gateway_send=gateway_send,
        bot_token=bot_token,
    )
