"""Authoritative freshness checks for queued Telegram admin broadcasts."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.services.bot_access_policy import evaluate_bot_access
from core.services.telegram_admin_broadcast_service import (
    telegram_admin_broadcast_dedupe_key,
    validate_telegram_admin_broadcast_content,
)
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
from models.telegram_admin_broadcast import (
    TelegramAdminBroadcast,
    TelegramAdminBroadcastReceipt,
    TelegramAdminBroadcastReceiptStatus,
    TelegramAdminBroadcastStatus,
)
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from models.user import User


ADMIN_BROADCAST_FRESHNESS_ACTIONS = frozenset(
    {TelegramDeliveryAction.ADMIN_BROADCAST}
)
ADMIN_BROADCAST_TEMPLATE_VERSION = "admin-broadcast-v1"
_ADMIN_BROADCAST_SOURCE_SEPARATOR = ":content-v1:"
_ACTIVE_RECEIPT_STATUSES = frozenset(
    {
        TelegramAdminBroadcastReceiptStatus.PENDING.value,
        TelegramAdminBroadcastReceiptStatus.RETRYABLE_FAILED.value,
    }
)
_TERMINAL_RECEIPT_STATUSES = frozenset(
    {
        TelegramAdminBroadcastReceiptStatus.SENT.value,
        TelegramAdminBroadcastReceiptStatus.SKIPPED.value,
        TelegramAdminBroadcastReceiptStatus.TERMINAL_FAILED.value,
    }
)
_ACTIVE_BROADCAST_STATUSES = frozenset(
    {
        TelegramAdminBroadcastStatus.QUEUED.value,
        TelegramAdminBroadcastStatus.RUNNING.value,
    }
)


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _strict_positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _decision(
    outcome: TelegramFreshnessOutcome,
    *,
    reason: str,
    replacement_action: TelegramDeliveryAction | None = None,
) -> TelegramFreshnessDecision:
    return TelegramFreshnessDecision(
        outcome=outcome,
        reason=reason,
        replacement_action=replacement_action,
    )


def _quarantined(reason: str) -> TelegramFreshnessDecision:
    return _decision(TelegramFreshnessOutcome.QUARANTINED, reason=reason)


def telegram_admin_broadcast_destination_key(recipient_user_id: int) -> str:
    normalized = _strict_positive_int(recipient_user_id)
    if normalized is None:
        raise ValueError("telegram_admin_broadcast_recipient_user_id_invalid")
    return f"private:user:{normalized}"


def telegram_admin_broadcast_source_version(user: User | Any) -> int:
    version = _strict_positive_int(getattr(user, "sync_version", None))
    if version is None:
        raise ValueError("telegram_admin_broadcast_recipient_version_invalid")
    return version


def _source_natural_id_from_content(*, dedupe_key: str, content: str) -> str:
    normalized_content = validate_telegram_admin_broadcast_content(content)
    snapshot = json.dumps(
        {
            "content": normalized_content,
            "dedupe_key": dedupe_key,
            "template_version": ADMIN_BROADCAST_TEMPLATE_VERSION,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    fingerprint = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()[:24]
    identity = f"{dedupe_key}{_ADMIN_BROADCAST_SOURCE_SEPARATOR}{fingerprint}"
    if len(identity) > 256:
        raise ValueError("telegram_admin_broadcast_source_identity_too_long")
    return identity


def telegram_admin_broadcast_source_natural_id(
    receipt: TelegramAdminBroadcastReceipt | Any,
    broadcast: TelegramAdminBroadcast | Any,
) -> str:
    dedupe_key = str(getattr(receipt, "dedupe_key", "") or "").strip()
    if not dedupe_key:
        raise ValueError("telegram_admin_broadcast_receipt_dedupe_missing")
    return _source_natural_id_from_content(
        dedupe_key=dedupe_key,
        content=str(getattr(broadcast, "content", "") or ""),
    )


def telegram_admin_broadcast_receipt_dedupe_from_source(
    source_natural_id: Any,
) -> str | None:
    identity = str(source_natural_id or "").strip()
    if _ADMIN_BROADCAST_SOURCE_SEPARATOR not in identity:
        return None
    dedupe_key, fingerprint = identity.rsplit(
        _ADMIN_BROADCAST_SOURCE_SEPARATOR,
        1,
    )
    if not dedupe_key or len(fingerprint) != 24:
        return None
    if any(character not in "0123456789abcdef" for character in fingerprint):
        return None
    return dedupe_key


def build_telegram_admin_broadcast_payload(
    broadcast: TelegramAdminBroadcast | Any,
    user: User | Any,
) -> dict[str, Any]:
    content = validate_telegram_admin_broadcast_content(
        str(getattr(broadcast, "content", "") or "")
    )
    telegram_id = _strict_positive_int(getattr(user, "telegram_id", None))
    if telegram_id is None:
        raise ValueError("telegram_admin_broadcast_current_chat_id_invalid")
    return {
        "chat_id": telegram_id,
        "text": content,
        "parse_mode": None,
    }


def telegram_admin_broadcast_campaign_id(broadcast_id: int) -> str:
    normalized = _strict_positive_int(broadcast_id)
    if normalized is None:
        raise ValueError("telegram_admin_broadcast_id_invalid")
    return f"admin-broadcast:{normalized}"


def _validate_static_route(
    job: TelegramDeliveryJobRecord,
) -> TelegramFreshnessDecision | None:
    if _enum_value(job.action_kind) != TelegramDeliveryAction.ADMIN_BROADCAST.value:
        return _quarantined("admin_broadcast_freshness_action_mismatch")
    if _enum_value(job.feeder_kind) != TelegramFeederKind.ADMIN_SYSTEM.value:
        return _quarantined("admin_broadcast_freshness_feeder_mismatch")
    if _enum_value(job.destination_class) != TelegramDestinationClass.PRIVATE.value:
        return _quarantined("admin_broadcast_freshness_destination_class_mismatch")
    if str(job.method or "") != "sendMessage":
        return _quarantined("admin_broadcast_freshness_method_mismatch")
    if str(job.bot_identity or "") != "primary":
        return _quarantined("admin_broadcast_freshness_bot_identity_mismatch")
    if str(job.template_version or "") != ADMIN_BROADCAST_TEMPLATE_VERSION:
        return _quarantined("admin_broadcast_freshness_template_version_mismatch")
    if (
        job.delivery_deadline_at is not None
        or job.freshness_deadline_at is not None
        or job.run_id is not None
    ):
        return _quarantined("admin_broadcast_freshness_deadline_or_run_forbidden")
    return None


async def _load_receipt(
    db: AsyncSession,
    *,
    dedupe_key: str,
) -> TelegramAdminBroadcastReceipt | None:
    return (
        await db.execute(
            select(TelegramAdminBroadcastReceipt)
            .where(TelegramAdminBroadcastReceipt.dedupe_key == dedupe_key)
            .limit(1)
        )
    ).scalar_one_or_none()


def _receipt_shape_decision(
    job: TelegramDeliveryJobRecord,
    receipt: TelegramAdminBroadcastReceipt | Any,
) -> TelegramFreshnessDecision | None:
    broadcast_id = _strict_positive_int(getattr(receipt, "broadcast_id", None))
    recipient_user_id = _strict_positive_int(
        getattr(receipt, "recipient_user_id", None)
    )
    receipt_id = _strict_positive_int(getattr(receipt, "id", None))
    job_id = _strict_positive_int(getattr(job, "id", None))
    if (
        broadcast_id is None
        or recipient_user_id is None
        or receipt_id is None
        or job_id is None
    ):
        return _quarantined("admin_broadcast_freshness_receipt_shape_invalid")
    expected_dedupe = telegram_admin_broadcast_dedupe_key(
        broadcast_id=broadcast_id,
        recipient_user_id=recipient_user_id,
    )
    if str(receipt.dedupe_key or "") != expected_dedupe:
        return _quarantined("admin_broadcast_freshness_receipt_identity_invalid")
    if _strict_positive_int(getattr(receipt, "telegram_id_at_enqueue", None)) is None:
        return _quarantined("admin_broadcast_freshness_enqueue_identity_invalid")
    if _strict_positive_int(getattr(receipt, "queue_job_id", None)) != job_id:
        return _quarantined("admin_broadcast_freshness_queue_owner_mismatch")
    if not isinstance(getattr(receipt, "queue_handed_off_at", None), datetime):
        return _quarantined("admin_broadcast_freshness_handoff_evidence_missing")
    if str(job.destination_key or "") != telegram_admin_broadcast_destination_key(
        recipient_user_id
    ):
        return _quarantined("admin_broadcast_freshness_destination_key_mismatch")
    if str(job.campaign_id or "") != telegram_admin_broadcast_campaign_id(
        broadcast_id
    ):
        return _quarantined("admin_broadcast_freshness_campaign_mismatch")
    return None


def _stored_snapshot_decision(
    job: TelegramDeliveryJobRecord,
    receipt: TelegramAdminBroadcastReceipt | Any,
    broadcast: TelegramAdminBroadcast | Any,
) -> TelegramFreshnessDecision | None:
    try:
        normalized_payload, payload_hash = canonical_telegram_delivery_payload(
            job.payload
        )
    except (TelegramDeliveryQueueValidationError, TypeError, ValueError):
        return _quarantined("admin_broadcast_freshness_payload_not_strict_json")
    if str(job.payload_hash or "") != payload_hash:
        return _quarantined("admin_broadcast_freshness_payload_hash_mismatch")
    job_text = normalized_payload.get("text")
    if (
        set(normalized_payload) != {"chat_id", "text", "parse_mode"}
        or _strict_positive_int(normalized_payload.get("chat_id")) is None
        or not isinstance(job_text, str)
        or not job_text.strip()
        or normalized_payload.get("parse_mode") is not None
    ):
        return _quarantined("admin_broadcast_freshness_payload_shape_invalid")
    try:
        snapshot_source = _source_natural_id_from_content(
            dedupe_key=str(receipt.dedupe_key),
            content=job_text,
        )
        current_source = telegram_admin_broadcast_source_natural_id(
            receipt,
            broadcast,
        )
    except ValueError:
        return _quarantined("admin_broadcast_freshness_source_snapshot_invalid")
    if str(job.source_natural_id or "") != snapshot_source:
        return _quarantined(
            "admin_broadcast_freshness_source_snapshot_fingerprint_invalid"
        )
    if snapshot_source != current_source:
        return _decision(
            TelegramFreshnessOutcome.RECLASSIFY,
            replacement_action=TelegramDeliveryAction.ADMIN_BROADCAST,
            reason="admin_broadcast_freshness_content_changed",
        )
    return None


async def validate_admin_broadcast_telegram_delivery_freshness(
    db: AsyncSession,
    job: TelegramDeliveryJobRecord,
    now: datetime,
) -> TelegramFreshnessDecision:
    """Rebuild one broadcast recipient send from current durable state."""
    del now
    static_decision = _validate_static_route(job)
    if static_decision is not None:
        return static_decision
    dedupe_key = telegram_admin_broadcast_receipt_dedupe_from_source(
        job.source_natural_id
    )
    if dedupe_key is None:
        return _quarantined("admin_broadcast_freshness_source_identity_invalid")
    receipt = await _load_receipt(db, dedupe_key=dedupe_key)
    if receipt is None:
        return _decision(
            TelegramFreshnessOutcome.WAIT_DEPENDENCY,
            reason="admin_broadcast_freshness_receipt_missing",
        )
    shape_decision = _receipt_shape_decision(job, receipt)
    if shape_decision is not None:
        return shape_decision

    status = _enum_value(receipt.status)
    if status == TelegramAdminBroadcastReceiptStatus.SENT.value:
        if _strict_positive_int(receipt.telegram_message_id) is None:
            return _quarantined("admin_broadcast_freshness_sent_evidence_missing")
        return _decision(
            TelegramFreshnessOutcome.SENT_NOOP,
            reason="admin_broadcast_freshness_already_sent",
        )
    if receipt.telegram_message_id is not None:
        return _quarantined(
            "admin_broadcast_freshness_unsent_message_evidence_present"
        )
    if status in _TERMINAL_RECEIPT_STATUSES:
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="admin_broadcast_freshness_receipt_terminal",
        )
    if status == TelegramAdminBroadcastReceiptStatus.SENDING.value:
        return _decision(
            TelegramFreshnessOutcome.WAIT_DEPENDENCY,
            reason="admin_broadcast_freshness_legacy_receipt_sending",
        )
    if status not in _ACTIVE_RECEIPT_STATUSES:
        return _quarantined("admin_broadcast_freshness_receipt_status_invalid")

    broadcast = await db.get(TelegramAdminBroadcast, int(receipt.broadcast_id))
    if broadcast is None:
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="admin_broadcast_freshness_broadcast_missing",
        )
    if _enum_value(broadcast.status) not in _ACTIVE_BROADCAST_STATUSES:
        return _quarantined("admin_broadcast_freshness_broadcast_state_invalid")
    snapshot_decision = _stored_snapshot_decision(job, receipt, broadcast)
    if snapshot_decision is not None:
        return snapshot_decision

    user = await db.get(User, int(receipt.recipient_user_id))
    if user is None:
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="admin_broadcast_freshness_recipient_missing",
        )
    try:
        current_version = telegram_admin_broadcast_source_version(user)
    except ValueError:
        return _quarantined("admin_broadcast_freshness_recipient_version_invalid")
    source_version = _strict_positive_int(job.source_version)
    if source_version is None:
        return _quarantined("admin_broadcast_freshness_source_version_invalid")
    if source_version > current_version:
        return _decision(
            TelegramFreshnessOutcome.WAIT_DEPENDENCY,
            reason="admin_broadcast_freshness_recipient_version_ahead",
        )
    if source_version < current_version:
        return _decision(
            TelegramFreshnessOutcome.RECLASSIFY,
            replacement_action=TelegramDeliveryAction.ADMIN_BROADCAST,
            reason="admin_broadcast_freshness_recipient_version_changed",
        )
    if _strict_positive_int(user.telegram_id) is None:
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="admin_broadcast_freshness_recipient_unlinked",
        )
    access = await evaluate_bot_access(db, user)
    if not access.allowed:
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="admin_broadcast_freshness_recipient_access_denied",
        )
    try:
        expected_payload = build_telegram_admin_broadcast_payload(broadcast, user)
        normalized_payload, _ = canonical_telegram_delivery_payload(job.payload)
    except (TelegramDeliveryQueueValidationError, TypeError, ValueError):
        return _quarantined("admin_broadcast_freshness_payload_not_authoritative")
    if normalized_payload != expected_payload:
        return _quarantined("admin_broadcast_freshness_payload_not_authoritative")
    return _decision(
        TelegramFreshnessOutcome.SEND,
        reason="admin_broadcast_freshness_current",
    )


@dataclass(frozen=True, slots=True)
class AdminBroadcastTelegramDeliveryFreshnessValidator:
    async def __call__(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        now: datetime,
    ) -> TelegramFreshnessDecision:
        return await validate_admin_broadcast_telegram_delivery_freshness(
            db,
            job,
            now,
        )
