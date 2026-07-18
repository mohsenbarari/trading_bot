"""Authoritative freshness for bounded scheduled Telegram source receipts."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.services.telegram_delivery_queue_service import (
    TelegramDeliveryQueueValidationError,
    canonical_telegram_delivery_payload,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDestinationClass,
    TelegramFeederKind,
    TelegramFreshnessDecision,
    TelegramFreshnessOutcome,
)
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from models.telegram_scheduled_operation import TelegramScheduledOperation
from models.user import User


SCHEDULED_OPERATION_FRESHNESS_ACTIONS = frozenset(
    {
        TelegramDeliveryAction.NONCRITICAL_MARKET,
        TelegramDeliveryAction.TEMPORARY_CLEANUP,
        TelegramDeliveryAction.COSMETIC_CLEANUP,
    }
)
SCHEDULED_OPERATION_SOURCE_SEPARATOR = ":scheduled-v1:"


@dataclass(frozen=True, slots=True)
class TelegramScheduledOperationPolicy:
    action: TelegramDeliveryAction
    feeder: TelegramFeederKind
    destination_class: TelegramDestinationClass
    method: str
    template_version: str
    cleanup: bool = False


SCHEDULED_OPERATION_POLICIES = MappingProxyType(
    {
        TelegramDeliveryAction.NONCRITICAL_MARKET: TelegramScheduledOperationPolicy(
            TelegramDeliveryAction.NONCRITICAL_MARKET,
            TelegramFeederKind.MARKET_STATUS,
            TelegramDestinationClass.CHANNEL,
            "sendMessage",
            "noncritical-market-v1",
        ),
        TelegramDeliveryAction.TEMPORARY_CLEANUP: TelegramScheduledOperationPolicy(
            TelegramDeliveryAction.TEMPORARY_CLEANUP,
            TelegramFeederKind.TIMED_BOT,
            TelegramDestinationClass.PRIVATE,
            "deleteMessage",
            "temporary-cleanup-v1",
            cleanup=True,
        ),
        TelegramDeliveryAction.COSMETIC_CLEANUP: TelegramScheduledOperationPolicy(
            TelegramDeliveryAction.COSMETIC_CLEANUP,
            TelegramFeederKind.TIMED_BOT,
            TelegramDestinationClass.PRIVATE,
            "editMessageReplyMarkup",
            "cosmetic-cleanup-v1",
            cleanup=True,
        ),
    }
)

_ACTIVE_STATUS = "pending"
_TERMINAL_STATUSES = {"sent", "skipped", "terminal_failed", "cancelled"}


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _nonzero_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value == 0:
        return None
    return value


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _decision(
    outcome: TelegramFreshnessOutcome,
    *,
    reason: str,
) -> TelegramFreshnessDecision:
    return TelegramFreshnessDecision(outcome=outcome, reason=reason)


def _quarantined(reason: str) -> TelegramFreshnessDecision:
    return _decision(TelegramFreshnessOutcome.QUARANTINED, reason=reason)


def scheduled_operation_policy(
    value: TelegramDeliveryAction | str,
) -> TelegramScheduledOperationPolicy:
    try:
        action = TelegramDeliveryAction(_enum_value(value))
    except ValueError as exc:
        raise ValueError("scheduled_operation_action_invalid") from exc
    policy = SCHEDULED_OPERATION_POLICIES.get(action)
    if policy is None:
        raise ValueError("scheduled_operation_action_unsupported")
    return policy


def telegram_scheduled_operation_destination_key(
    operation: TelegramScheduledOperation | Any,
) -> str:
    policy = scheduled_operation_policy(getattr(operation, "action_kind", None))
    if policy.destination_class == TelegramDestinationClass.CHANNEL:
        chat_id = _nonzero_int(getattr(operation, "chat_id", None))
        if chat_id is None:
            raise ValueError("scheduled_operation_channel_invalid")
        return f"channel:{chat_id}"
    recipient_user_id = _positive_int(
        getattr(operation, "recipient_user_id", None)
    )
    if recipient_user_id is None:
        raise ValueError("scheduled_operation_recipient_invalid")
    return f"private:user:{recipient_user_id}"


def telegram_scheduled_operation_source_natural_id(
    operation: TelegramScheduledOperation | Any,
) -> str:
    dedupe_key = str(getattr(operation, "dedupe_key", "") or "").strip()
    if not dedupe_key or len(dedupe_key) > 192:
        raise ValueError("scheduled_operation_dedupe_invalid")
    payload_hash = str(getattr(operation, "payload_hash", "") or "").strip()
    if len(payload_hash) != 64:
        raise ValueError("scheduled_operation_payload_hash_invalid")
    fingerprint = hashlib.sha256(
        f"{dedupe_key}:{payload_hash}".encode("utf-8")
    ).hexdigest()[:24]
    identity = f"{dedupe_key}{SCHEDULED_OPERATION_SOURCE_SEPARATOR}{fingerprint}"
    if len(identity) > 256:
        raise ValueError("scheduled_operation_source_identity_too_long")
    return identity


def telegram_scheduled_operation_dedupe_from_source(value: Any) -> str | None:
    identity = str(value or "").strip()
    if SCHEDULED_OPERATION_SOURCE_SEPARATOR not in identity:
        return None
    dedupe_key, fingerprint = identity.rsplit(
        SCHEDULED_OPERATION_SOURCE_SEPARATOR,
        1,
    )
    if not dedupe_key or len(fingerprint) != 24:
        return None
    if any(char not in "0123456789abcdef" for char in fingerprint):
        return None
    return dedupe_key


def validate_telegram_scheduled_operation_contract(
    operation: TelegramScheduledOperation | Any,
    *,
    expected_channel_id: int,
) -> TelegramScheduledOperationPolicy:
    policy = scheduled_operation_policy(getattr(operation, "action_kind", None))
    source_id = str(getattr(operation, "source_id", "") or "").strip()
    if not source_id or len(source_id) > 120:
        raise ValueError("scheduled_operation_source_id_invalid")
    if _positive_int(getattr(operation, "source_version", None)) is None:
        raise ValueError("scheduled_operation_source_version_invalid")
    if _enum_value(getattr(operation, "destination_class", None)) != policy.destination_class.value:
        raise ValueError("scheduled_operation_destination_class_invalid")
    if str(getattr(operation, "method", "") or "") != policy.method:
        raise ValueError("scheduled_operation_method_invalid")
    if str(getattr(operation, "template_version", "") or "") != policy.template_version:
        raise ValueError("scheduled_operation_template_invalid")
    chat_id = _nonzero_int(getattr(operation, "chat_id", None))
    if chat_id is None:
        raise ValueError("scheduled_operation_chat_invalid")
    message_id = _positive_int(getattr(operation, "message_id", None))
    if policy.cleanup:
        if message_id is None:
            raise ValueError("scheduled_operation_cleanup_scope_invalid")
        if _positive_int(getattr(operation, "recipient_user_id", None)) is None:
            raise ValueError("scheduled_operation_recipient_invalid")
    else:
        if chat_id != expected_channel_id or message_id is not None:
            raise ValueError("scheduled_operation_market_route_invalid")
        if getattr(operation, "recipient_user_id", None) is not None:
            raise ValueError("scheduled_operation_market_recipient_forbidden")
        if not isinstance(getattr(operation, "freshness_deadline_at", None), datetime):
            raise ValueError("scheduled_operation_market_deadline_required")
    if not isinstance(getattr(operation, "due_at", None), datetime):
        raise ValueError("scheduled_operation_due_invalid")
    normalized_payload, payload_hash = canonical_telegram_delivery_payload(
        getattr(operation, "payload", None)
    )
    if normalized_payload != getattr(operation, "payload", None):
        raise ValueError("scheduled_operation_payload_not_canonical")
    if str(getattr(operation, "payload_hash", "") or "") != payload_hash:
        raise ValueError("scheduled_operation_payload_hash_mismatch")
    expected_payload: dict[str, Any] = {"chat_id": chat_id}
    if message_id is not None:
        expected_payload["message_id"] = message_id
    if policy.action == TelegramDeliveryAction.NONCRITICAL_MARKET:
        text = str(normalized_payload.get("text") or "").strip()
        if not text or len(text) > 4096:
            raise ValueError("scheduled_operation_market_text_invalid")
        expected_payload["text"] = text
    if normalized_payload != expected_payload:
        raise ValueError("scheduled_operation_payload_shape_invalid")
    return policy


def _validate_static_job_route(
    job: TelegramDeliveryJobRecord,
    *,
    operation: TelegramScheduledOperation,
    policy: TelegramScheduledOperationPolicy,
) -> TelegramFreshnessDecision | None:
    if _enum_value(job.feeder_kind) != policy.feeder.value:
        return _quarantined("scheduled_operation_freshness_feeder_mismatch")
    if _enum_value(job.destination_class) != policy.destination_class.value:
        return _quarantined("scheduled_operation_freshness_destination_mismatch")
    if str(job.method or "") != policy.method:
        return _quarantined("scheduled_operation_freshness_method_mismatch")
    if str(job.bot_identity or "") != "primary":
        return _quarantined("scheduled_operation_freshness_bot_mismatch")
    if str(job.template_version or "") != policy.template_version:
        return _quarantined("scheduled_operation_freshness_template_mismatch")
    try:
        destination_key = telegram_scheduled_operation_destination_key(operation)
    except ValueError:
        return _quarantined("scheduled_operation_freshness_destination_invalid")
    if str(job.destination_key or "") != destination_key:
        return _quarantined("scheduled_operation_freshness_destination_key_mismatch")
    if job.delivery_deadline_at is not None or job.campaign_id is not None:
        return _quarantined("scheduled_operation_freshness_deadline_shape_invalid")
    if job.eligible_at != operation.due_at:
        return _quarantined("scheduled_operation_freshness_due_mismatch")
    if job.freshness_deadline_at != operation.freshness_deadline_at:
        return _quarantined("scheduled_operation_freshness_deadline_mismatch")
    if (str(job.run_id or "").strip() or None) != (
        str(operation.run_id or "").strip() or None
    ):
        return _quarantined("scheduled_operation_freshness_run_mismatch")
    return None


async def validate_scheduled_operation_telegram_delivery_freshness(
    db: AsyncSession,
    job: TelegramDeliveryJobRecord,
    now: datetime,
    *,
    expected_channel_id: int,
) -> TelegramFreshnessDecision:
    try:
        policy = scheduled_operation_policy(job.action_kind)
    except ValueError:
        return _quarantined("scheduled_operation_freshness_action_invalid")
    dedupe_key = telegram_scheduled_operation_dedupe_from_source(
        job.source_natural_id
    )
    if dedupe_key is None:
        return _quarantined("scheduled_operation_freshness_source_invalid")
    operation = (
        await db.execute(
            select(TelegramScheduledOperation)
            .where(TelegramScheduledOperation.dedupe_key == dedupe_key)
            .limit(1)
        )
    ).scalar_one_or_none()
    if operation is None:
        return _quarantined("scheduled_operation_freshness_source_missing")
    try:
        source_policy = validate_telegram_scheduled_operation_contract(
            operation,
            expected_channel_id=expected_channel_id,
        )
    except (TelegramDeliveryQueueValidationError, TypeError, ValueError):
        return _quarantined("scheduled_operation_freshness_source_contract_invalid")
    if source_policy != policy:
        return _quarantined("scheduled_operation_freshness_policy_mismatch")
    static_decision = _validate_static_job_route(
        job,
        operation=operation,
        policy=policy,
    )
    if static_decision is not None:
        return static_decision
    if _positive_int(operation.queue_job_id) != _positive_int(job.id):
        return _quarantined("scheduled_operation_freshness_queue_owner_mismatch")
    if not isinstance(operation.queue_handed_off_at, datetime):
        return _quarantined("scheduled_operation_freshness_handoff_missing")
    status = str(operation.status or "").strip().lower()
    if status == "sent":
        return _decision(
            TelegramFreshnessOutcome.SENT_NOOP,
            reason="scheduled_operation_freshness_already_sent",
        )
    if status in _TERMINAL_STATUSES:
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="scheduled_operation_freshness_source_terminal",
        )
    if status != _ACTIVE_STATUS:
        return _quarantined("scheduled_operation_freshness_source_status_invalid")
    if _utc(now) < _utc(operation.due_at):
        return _decision(
            TelegramFreshnessOutcome.WAIT_DEPENDENCY,
            reason="scheduled_operation_freshness_not_due",
        )
    if (
        operation.freshness_deadline_at is not None
        and _utc(now) > _utc(operation.freshness_deadline_at)
    ):
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="scheduled_operation_freshness_deadline_passed",
        )
    if policy.cleanup and not bool(operation.scope_allowed):
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="scheduled_operation_freshness_scope_revoked",
        )
    if policy.cleanup:
        user = await db.get(User, int(operation.recipient_user_id))
        if user is None or _positive_int(getattr(user, "telegram_id", None)) != int(
            operation.chat_id
        ):
            return _decision(
                TelegramFreshnessOutcome.SUPERSEDED,
                reason="scheduled_operation_freshness_recipient_changed",
            )
    try:
        normalized_payload, payload_hash = canonical_telegram_delivery_payload(
            job.payload
        )
        source_identity = telegram_scheduled_operation_source_natural_id(
            operation
        )
    except (TelegramDeliveryQueueValidationError, TypeError, ValueError):
        return _quarantined("scheduled_operation_freshness_job_payload_invalid")
    if normalized_payload != operation.payload or str(job.payload_hash or "") != payload_hash:
        return _quarantined("scheduled_operation_freshness_payload_mismatch")
    if str(job.source_natural_id or "") != source_identity:
        return _quarantined("scheduled_operation_freshness_source_identity_mismatch")
    if _positive_int(job.source_version) != int(operation.source_version):
        return _quarantined("scheduled_operation_freshness_source_version_mismatch")
    return _decision(
        TelegramFreshnessOutcome.SEND,
        reason="scheduled_operation_freshness_current",
    )


@dataclass(frozen=True, slots=True)
class ScheduledOperationTelegramDeliveryFreshnessValidator:
    expected_channel_id: int

    def __post_init__(self) -> None:
        if _nonzero_int(self.expected_channel_id) is None:
            raise ValueError("scheduled_operation_expected_channel_invalid")

    async def __call__(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        now: datetime,
    ) -> TelegramFreshnessDecision:
        return await validate_scheduled_operation_telegram_delivery_freshness(
            db,
            job,
            now,
            expected_channel_id=self.expected_channel_id,
        )
