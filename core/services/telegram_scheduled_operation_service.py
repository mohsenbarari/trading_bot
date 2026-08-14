"""Durable producers and atomic handoff for scheduled Telegram operations."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.server_routing import SERVER_FOREIGN
from core.services.telegram_delivery_queue_service import (
    canonical_telegram_delivery_payload,
    enqueue_telegram_delivery_job,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDestinationClass,
)
from core.telegram_delivery_scheduled_operation_freshness import (
    scheduled_operation_policy,
    telegram_scheduled_operation_destination_key,
    telegram_scheduled_operation_source_natural_id,
    validate_telegram_scheduled_operation_contract,
)
from core.utils import utc_now
from models.telegram_scheduled_operation import TelegramScheduledOperation
from models.user import User


SCHEDULED_OPERATION_HANDOFF = "handed_off"
SCHEDULED_OPERATION_REQUIRES_RECONCILIATION = "requires_reconciliation"
SCHEDULED_OPERATION_SKIPPED = "skipped"
DEFAULT_NONCRITICAL_MARKET_TTL = timedelta(minutes=5)


class TelegramScheduledOperationError(RuntimeError):
    """Raised before an unsafe scheduled source mutation or handoff."""


@dataclass(frozen=True, slots=True)
class TelegramScheduledOperationEnqueueResult:
    operation: TelegramScheduledOperation
    created: bool

    @property
    def message_id(self) -> int | None:
        value = getattr(self.operation, "telegram_message_id", None)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            return None
        return value


@dataclass(frozen=True, slots=True)
class TelegramScheduledOperationHandoffResult:
    operation_id: int
    disposition: str
    job_id: int | None = None
    job_created: bool = False
    reason: str | None = None


def _require_foreign(value: str) -> None:
    if str(value or "").strip().lower() != SERVER_FOREIGN:
        raise TelegramScheduledOperationError(
            "telegram_scheduled_operation_is_foreign_only"
        )


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("telegram_scheduled_operation_datetime_invalid")
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _source_id(value: Any) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 120:
        raise ValueError("telegram_scheduled_operation_source_id_invalid")
    return normalized


def _run_id(value: Any) -> str | None:
    normalized = str(value or "").strip() or None
    if normalized is not None and len(normalized) > 192:
        raise ValueError("telegram_scheduled_operation_run_id_invalid")
    return normalized


def _dedupe_key(
    *,
    action: TelegramDeliveryAction,
    source_id: str,
) -> str:
    key = f"telegram-scheduled:{action.value}:{source_id}"
    if len(key) > 192:
        raise ValueError("telegram_scheduled_operation_dedupe_too_long")
    return key


def _same_source(
    operation: TelegramScheduledOperation,
    *,
    action: TelegramDeliveryAction,
    source_id: str,
    source_version: int,
    recipient_user_id: int | None,
    destination_class: TelegramDestinationClass,
    chat_id: int,
    message_id: int | None,
    method: str,
    payload: Mapping[str, Any],
    payload_hash: str,
    template_version: str,
    due_at: datetime,
    freshness_deadline_at: datetime | None,
    run_id: str | None,
    scope_allowed: bool,
) -> bool:
    return (
        str(operation.action_kind or "") == action.value
        and str(operation.source_id or "") == source_id
        and int(operation.source_version or 0) == source_version
        and operation.recipient_user_id == recipient_user_id
        and str(operation.destination_class or "") == destination_class.value
        and int(operation.chat_id or 0) == chat_id
        and operation.message_id == message_id
        and str(operation.method or "") == method
        and dict(operation.payload or {}) == dict(payload)
        and str(operation.payload_hash or "") == payload_hash
        and str(operation.template_version or "") == template_version
        and _utc(operation.due_at) == due_at
        and (
            _utc(operation.freshness_deadline_at)
            if operation.freshness_deadline_at is not None
            else None
        )
        == freshness_deadline_at
        and (str(operation.run_id or "").strip() or None) == run_id
        and bool(operation.scope_allowed) is scope_allowed
    )


async def enqueue_telegram_scheduled_operation_once(
    db: AsyncSession,
    *,
    current_server: str,
    action: TelegramDeliveryAction,
    source_id: str,
    source_version: int,
    recipient_user_id: int | None,
    destination_class: TelegramDestinationClass,
    chat_id: int,
    message_id: int | None,
    payload: Mapping[str, Any],
    due_at: datetime,
    freshness_deadline_at: datetime | None = None,
    run_id: str | None = None,
    scope_allowed: bool = False,
) -> TelegramScheduledOperationEnqueueResult:
    _require_foreign(current_server)
    policy = scheduled_operation_policy(action)
    normalized_source_id = _source_id(source_id)
    if isinstance(source_version, bool) or not isinstance(source_version, int) or source_version <= 0:
        raise ValueError("telegram_scheduled_operation_source_version_invalid")
    if isinstance(chat_id, bool) or not isinstance(chat_id, int) or chat_id == 0:
        raise ValueError("telegram_scheduled_operation_chat_id_invalid")
    if message_id is not None and (
        isinstance(message_id, bool)
        or not isinstance(message_id, int)
        or message_id <= 0
    ):
        raise ValueError("telegram_scheduled_operation_message_id_invalid")
    preauth_shared_chat = (
        action == TelegramDeliveryAction.PREAUTH_INTERACTION
        and destination_class == TelegramDestinationClass.CHANNEL
    )
    if destination_class != policy.destination_class and not preauth_shared_chat:
        raise ValueError("telegram_scheduled_operation_destination_invalid")
    if policy.cleanup and (
        not isinstance(recipient_user_id, int)
        or isinstance(recipient_user_id, bool)
        or recipient_user_id <= 0
    ):
        raise ValueError("telegram_scheduled_operation_recipient_invalid")
    if not policy.cleanup and recipient_user_id is not None:
        raise ValueError("telegram_scheduled_operation_recipient_forbidden")
    normalized_due = _utc(due_at)
    normalized_deadline = (
        _utc(freshness_deadline_at)
        if freshness_deadline_at is not None
        else None
    )
    if normalized_deadline is not None and normalized_deadline < normalized_due:
        raise ValueError("telegram_scheduled_operation_deadline_before_due")
    normalized_run_id = _run_id(run_id)
    normalized_payload, payload_hash = canonical_telegram_delivery_payload(payload)
    dedupe_key = _dedupe_key(action=action, source_id=normalized_source_id)
    values = {
        "dedupe_key": dedupe_key,
        "action_kind": action.value,
        "source_id": normalized_source_id,
        "source_version": source_version,
        "recipient_user_id": recipient_user_id,
        "destination_class": destination_class.value,
        "chat_id": chat_id,
        "message_id": message_id,
        "method": policy.method,
        "payload": normalized_payload,
        "payload_hash": payload_hash,
        "template_version": policy.template_version,
        "due_at": normalized_due,
        "freshness_deadline_at": normalized_deadline,
        "run_id": normalized_run_id,
        "scope_allowed": bool(scope_allowed),
        "status": "pending",
        "attempt_count": 0,
    }
    inserted_id = (
        await db.execute(
            pg_insert(TelegramScheduledOperation)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["dedupe_key"])
            .returning(TelegramScheduledOperation.id)
        )
    ).scalar_one_or_none()
    created = inserted_id is not None
    operation = (
        await db.execute(
            select(TelegramScheduledOperation).where(
                TelegramScheduledOperation.id == inserted_id
                if created
                else TelegramScheduledOperation.dedupe_key == dedupe_key
            )
        )
    ).scalar_one()
    if not _same_source(
        operation,
        action=action,
        source_id=normalized_source_id,
        source_version=source_version,
        recipient_user_id=recipient_user_id,
        destination_class=destination_class,
        chat_id=chat_id,
        message_id=message_id,
        method=policy.method,
        payload=normalized_payload,
        payload_hash=payload_hash,
        template_version=policy.template_version,
        due_at=normalized_due,
        freshness_deadline_at=normalized_deadline,
        run_id=normalized_run_id,
        scope_allowed=bool(scope_allowed),
    ):
        raise ValueError("telegram_scheduled_operation_dedupe_conflict")
    return TelegramScheduledOperationEnqueueResult(operation=operation, created=created)


async def _recipient_for_chat(db: AsyncSession, chat_id: int) -> User:
    user = (
        await db.execute(
            select(User).where(User.telegram_id == chat_id).limit(1)
        )
    ).scalar_one_or_none()
    if user is None:
        raise ValueError("telegram_scheduled_operation_recipient_not_found")
    return user


async def enqueue_noncritical_market_notice_once(
    db: AsyncSession,
    *,
    current_server: str,
    channel_id: int,
    source_id: str,
    text: str,
    due_at: datetime | None = None,
    freshness_deadline_at: datetime | None = None,
) -> TelegramScheduledOperationEnqueueResult:
    normalized_due = _utc(due_at or utc_now())
    deadline = _utc(
        freshness_deadline_at or normalized_due + DEFAULT_NONCRITICAL_MARKET_TTL
    )
    clean_text = str(text or "").strip()
    if not clean_text or len(clean_text) > 4096:
        raise ValueError("telegram_noncritical_market_text_invalid")
    return await enqueue_telegram_scheduled_operation_once(
        db,
        current_server=current_server,
        action=TelegramDeliveryAction.NONCRITICAL_MARKET,
        source_id=source_id,
        source_version=1,
        recipient_user_id=None,
        destination_class=TelegramDestinationClass.CHANNEL,
        chat_id=channel_id,
        message_id=None,
        payload={"chat_id": channel_id, "text": clean_text},
        due_at=normalized_due,
        freshness_deadline_at=deadline,
        scope_allowed=False,
    )


async def enqueue_pre_auth_interaction_once(
    db: AsyncSession,
    *,
    current_server: str,
    chat_id: int,
    source_id: str,
    text: str,
    method: str = "sendMessage",
    destination_class: TelegramDestinationClass | str = TelegramDestinationClass.PRIVATE,
    message_id: int | None = None,
    parse_mode: str | None = None,
    reply_markup: Mapping[str, Any] | None = None,
    due_at: datetime | None = None,
    ttl_seconds: int = 120,
) -> TelegramScheduledOperationEnqueueResult:
    """Persist a short-lived private response before a local User exists."""

    normalized_method = str(method or "").strip()
    if normalized_method == "sendMessage":
        action = TelegramDeliveryAction.PREAUTH_INTERACTION
        if message_id is not None:
            raise ValueError("telegram_preauth_send_target_forbidden")
    elif normalized_method == "editMessageText":
        action = TelegramDeliveryAction.PREAUTH_INTERACTION_EDIT
        if (
            isinstance(message_id, bool)
            or not isinstance(message_id, int)
            or message_id <= 0
        ):
            raise ValueError("telegram_preauth_edit_target_invalid")
    else:
        raise ValueError("telegram_preauth_method_unsupported")
    try:
        normalized_destination = TelegramDestinationClass(
            str(getattr(destination_class, "value", destination_class))
        )
    except ValueError as exc:
        raise ValueError("telegram_preauth_destination_invalid") from exc
    if normalized_destination not in {
        TelegramDestinationClass.PRIVATE,
        TelegramDestinationClass.CHANNEL,
    }:
        raise ValueError("telegram_preauth_destination_invalid")
    if action == TelegramDeliveryAction.PREAUTH_INTERACTION_EDIT and (
        normalized_destination != TelegramDestinationClass.PRIVATE
    ):
        raise ValueError("telegram_preauth_edit_destination_invalid")
    if (
        isinstance(chat_id, bool)
        or not isinstance(chat_id, int)
        or (
            normalized_destination == TelegramDestinationClass.PRIVATE
            and chat_id <= 0
        )
        or (
            normalized_destination == TelegramDestinationClass.CHANNEL
            and chat_id >= 0
        )
    ):
        raise ValueError("telegram_preauth_chat_invalid")
    clean_text = str(text or "").strip()
    if not clean_text or len(clean_text) > 4096:
        raise ValueError("telegram_preauth_text_invalid")
    if parse_mode is not None and parse_mode not in {
        "HTML",
        "Markdown",
        "MarkdownV2",
    }:
        raise ValueError("telegram_preauth_parse_mode_invalid")
    if reply_markup is not None and not isinstance(reply_markup, Mapping):
        raise ValueError("telegram_preauth_reply_markup_invalid")
    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int):
        raise ValueError("telegram_preauth_ttl_invalid")
    bounded_ttl = max(5, min(ttl_seconds, 300))
    normalized_due = _utc(due_at or utc_now())
    payload: dict[str, Any] = {"chat_id": chat_id, "text": clean_text}
    if message_id is not None:
        payload["message_id"] = message_id
    if parse_mode is not None:
        payload["parse_mode"] = parse_mode
    if reply_markup is not None:
        payload["reply_markup"] = dict(reply_markup)
    return await enqueue_telegram_scheduled_operation_once(
        db,
        current_server=current_server,
        action=action,
        source_id=source_id,
        source_version=1,
        recipient_user_id=None,
        destination_class=normalized_destination,
        chat_id=chat_id,
        message_id=message_id,
        payload=payload,
        due_at=normalized_due,
        freshness_deadline_at=(
            normalized_due + timedelta(seconds=bounded_ttl)
        ),
        scope_allowed=True,
    )


async def enqueue_temporary_message_cleanup_once(
    db: AsyncSession,
    *,
    current_server: str,
    chat_id: int,
    message_id: int,
    source_id: str,
    due_at: datetime,
    run_id: str | None = None,
) -> TelegramScheduledOperationEnqueueResult:
    _require_foreign(current_server)
    user = await _recipient_for_chat(db, chat_id)
    return await enqueue_telegram_scheduled_operation_once(
        db,
        current_server=current_server,
        action=TelegramDeliveryAction.TEMPORARY_CLEANUP,
        source_id=source_id,
        source_version=1,
        recipient_user_id=int(user.id),
        destination_class=TelegramDestinationClass.PRIVATE,
        chat_id=chat_id,
        message_id=message_id,
        payload={"chat_id": chat_id, "message_id": message_id},
        due_at=due_at,
        run_id=run_id,
        scope_allowed=True,
    )


async def enqueue_cosmetic_markup_cleanup_once(
    db: AsyncSession,
    *,
    current_server: str,
    chat_id: int,
    message_id: int,
    source_id: str,
    due_at: datetime,
    run_id: str | None = None,
) -> TelegramScheduledOperationEnqueueResult:
    _require_foreign(current_server)
    user = await _recipient_for_chat(db, chat_id)
    return await enqueue_telegram_scheduled_operation_once(
        db,
        current_server=current_server,
        action=TelegramDeliveryAction.COSMETIC_CLEANUP,
        source_id=source_id,
        source_version=1,
        recipient_user_id=int(user.id),
        destination_class=TelegramDestinationClass.PRIVATE,
        chat_id=chat_id,
        message_id=message_id,
        payload={"chat_id": chat_id, "message_id": message_id},
        due_at=due_at,
        run_id=run_id,
        scope_allowed=True,
    )


async def cancel_telegram_scheduled_operation(
    db: AsyncSession,
    *,
    current_server: str,
    dedupe_key: str,
    reason: str,
    now: datetime | None = None,
) -> bool:
    _require_foreign(current_server)
    operation = (
        await db.execute(
            select(TelegramScheduledOperation)
            .where(TelegramScheduledOperation.dedupe_key == str(dedupe_key or ""))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if operation is None or str(operation.status or "") != "pending":
        return False
    operation.status = "cancelled"
    operation.scope_allowed = False
    operation.reason = str(reason or "scheduled_operation_cancelled")[:160]
    operation.terminal_at = _utc(now or utc_now())
    operation.updated_at = operation.terminal_at
    await db.flush()
    return True


async def _terminalize_invalid(
    db: AsyncSession,
    *,
    operation: TelegramScheduledOperation,
    reason: str,
    now: datetime,
) -> TelegramScheduledOperationHandoffResult:
    operation.status = "terminal_failed"
    operation.reason = str(reason or "scheduled_operation_invalid")[:160]
    operation.last_error_class = "TelegramScheduledOperationPayloadError"
    operation.last_error_message = str(reason or "scheduled_operation_invalid")[:500]
    operation.terminal_at = now
    operation.updated_at = now
    await db.flush()
    return TelegramScheduledOperationHandoffResult(
        operation_id=int(operation.id),
        disposition=SCHEDULED_OPERATION_SKIPPED,
        reason=operation.reason,
    )


async def handoff_next_due_telegram_scheduled_operation(
    db: AsyncSession,
    *,
    current_server: str,
    expected_channel_id: int,
    now: datetime | None = None,
) -> TelegramScheduledOperationHandoffResult | None:
    _require_foreign(current_server)
    current_time = _utc(now or utc_now())
    operation = (
        await db.execute(
            select(TelegramScheduledOperation)
            .where(
                TelegramScheduledOperation.status == "pending",
                TelegramScheduledOperation.queue_job_id.is_(None),
                TelegramScheduledOperation.reconciliation_required_at.is_(None),
                TelegramScheduledOperation.due_at <= current_time,
            )
            .order_by(
                TelegramScheduledOperation.due_at.asc(),
                TelegramScheduledOperation.id.asc(),
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
    ).scalar_one_or_none()
    if operation is None:
        return None
    try:
        policy = validate_telegram_scheduled_operation_contract(
            operation,
            expected_channel_id=expected_channel_id,
        )
        if policy.cleanup and not bool(operation.scope_allowed):
            raise ValueError("scheduled_operation_cleanup_scope_invalid")
        if (
            operation.freshness_deadline_at is not None
            and current_time > _utc(operation.freshness_deadline_at)
        ):
            operation.status = "skipped"
            operation.reason = "scheduled_operation_freshness_deadline_passed"
            operation.terminal_at = current_time
            operation.updated_at = current_time
            await db.flush()
            return TelegramScheduledOperationHandoffResult(
                operation_id=int(operation.id),
                disposition=SCHEDULED_OPERATION_SKIPPED,
                reason=operation.reason,
            )
        source_natural_id = telegram_scheduled_operation_source_natural_id(
            operation
        )
        destination_key = telegram_scheduled_operation_destination_key(operation)
    except (TypeError, ValueError, OverflowError) as exc:
        return await _terminalize_invalid(
            db,
            operation=operation,
            reason=str(exc) or "scheduled_operation_invalid",
            now=current_time,
        )
    enqueue_result = await enqueue_telegram_delivery_job(
        db,
        current_server=current_server,
        feeder=policy.feeder,
        source_natural_id=source_natural_id,
        source_version=int(operation.source_version),
        action=policy.action,
        bot_identity="primary",
        destination_key=destination_key,
        destination_class=TelegramDestinationClass(
            str(operation.destination_class)
        ),
        method=policy.method,
        payload=operation.payload,
        template_version=policy.template_version,
        eligible_at=operation.due_at,
        freshness_deadline_at=operation.freshness_deadline_at,
        run_id=operation.run_id,
    )
    job_id = int(enqueue_result.job.id)
    if not enqueue_result.created:
        operation.reconciliation_required_at = current_time
        operation.reason = "scheduled_operation_queue_orphan_requires_reconciliation"
        operation.last_error_class = "TelegramDeliveryReconciliationRequired"
        operation.updated_at = current_time
        await db.flush()
        return TelegramScheduledOperationHandoffResult(
            operation_id=int(operation.id),
            disposition=SCHEDULED_OPERATION_REQUIRES_RECONCILIATION,
            job_id=job_id,
            job_created=False,
            reason=operation.reason,
        )
    operation.queue_job_id = job_id
    operation.queue_handed_off_at = current_time
    operation.reason = "scheduled_operation_handed_to_main_queue"
    operation.updated_at = current_time
    await db.flush()
    return TelegramScheduledOperationHandoffResult(
        operation_id=int(operation.id),
        disposition=SCHEDULED_OPERATION_HANDOFF,
        job_id=job_id,
        job_created=True,
        reason=operation.reason,
    )
