"""Authoritative freshness for project-user membership notifications."""
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
from core.services.customer_relation_service import (
    get_active_customer_relation_for_user,
)
from core.services.telegram_delivery_queue_service import (
    TelegramDeliveryQueueValidationError,
    canonical_telegram_delivery_payload,
)
from core.services.telegram_notification_outbox_service import (
    TELEGRAM_NOTIFICATION_SOURCE_PROJECT_USER_JOINED,
    telegram_notification_dedupe_key,
    validate_telegram_notification_text,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDestinationClass,
    TelegramFeederKind,
    TelegramFreshnessDecision,
    TelegramFreshnessOutcome,
)
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from models.telegram_notification_outbox import (
    TelegramNotificationOutbox,
    TelegramNotificationOutboxStatus,
)
from models.user import User


NEW_USER_MEMBERSHIP_FRESHNESS_ACTIONS = frozenset(
    {TelegramDeliveryAction.NEW_USER_MEMBERSHIP}
)
NEW_USER_MEMBERSHIP_TEMPLATE_VERSION = "project-user-joined-v1"
_SOURCE_SEPARATOR = ":payload-v1:"
_ACTIVE_STATUSES = frozenset(
    {
        TelegramNotificationOutboxStatus.PENDING.value,
        TelegramNotificationOutboxStatus.RETRYABLE_FAILED.value,
    }
)
_TERMINAL_STATUSES = frozenset(
    {
        TelegramNotificationOutboxStatus.SENT.value,
        TelegramNotificationOutboxStatus.SKIPPED.value,
        TelegramNotificationOutboxStatus.TERMINAL_FAILED.value,
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


def telegram_new_user_membership_destination_key(recipient_user_id: int) -> str:
    normalized = _strict_positive_int(recipient_user_id)
    if normalized is None:
        raise ValueError("new_user_membership_recipient_user_id_invalid")
    return f"private:user:{normalized}"


def telegram_new_user_membership_source_version(user: User | Any) -> int:
    version = _strict_positive_int(getattr(user, "sync_version", None))
    if version is None:
        raise ValueError("new_user_membership_recipient_version_invalid")
    return version


def _validate_source_contract(
    outbox: TelegramNotificationOutbox | Any,
) -> tuple[str, int, int, str]:
    source_type = str(getattr(outbox, "source_type", "") or "").strip()
    if source_type != TELEGRAM_NOTIFICATION_SOURCE_PROJECT_USER_JOINED:
        raise ValueError("new_user_membership_source_type_unsupported")
    try:
        source_id = int(str(getattr(outbox, "source_id", "") or "").strip())
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("new_user_membership_source_id_invalid") from exc
    recipient_user_id = _strict_positive_int(
        getattr(outbox, "recipient_user_id", None)
    )
    if source_id <= 0 or recipient_user_id is None or source_id == recipient_user_id:
        raise ValueError("new_user_membership_source_or_recipient_invalid")
    expected_dedupe = telegram_notification_dedupe_key(
        source_type=source_type,
        source_id=source_id,
        recipient_user_id=recipient_user_id,
    )
    dedupe_key = str(getattr(outbox, "dedupe_key", "") or "").strip()
    if dedupe_key != expected_dedupe:
        raise ValueError("new_user_membership_dedupe_invalid")
    if getattr(outbox, "parse_mode", None) is not None:
        raise ValueError("new_user_membership_parse_mode_forbidden")
    extra_payload = getattr(outbox, "extra_payload", None)
    if not isinstance(extra_payload, Mapping) or extra_payload.get(
        "exclude_customers"
    ) is not True:
        raise ValueError("new_user_membership_exclusion_policy_missing")
    text = validate_telegram_notification_text(
        str(getattr(outbox, "text", "") or "")
    )
    return dedupe_key, source_id, recipient_user_id, text


def _source_natural_id_from_snapshot(
    *,
    dedupe_key: str,
    text: str,
) -> str:
    normalized_text = validate_telegram_notification_text(text)
    snapshot = json.dumps(
        {
            "dedupe_key": dedupe_key,
            "exclude_customers": True,
            "parse_mode": None,
            "template_version": NEW_USER_MEMBERSHIP_TEMPLATE_VERSION,
            "text": normalized_text,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    fingerprint = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()[:24]
    identity = f"{dedupe_key}{_SOURCE_SEPARATOR}{fingerprint}"
    if len(identity) > 256:
        raise ValueError("new_user_membership_source_identity_too_long")
    return identity


def telegram_new_user_membership_source_natural_id(
    outbox: TelegramNotificationOutbox | Any,
) -> str:
    dedupe_key, _source_id, _recipient_user_id, text = _validate_source_contract(
        outbox
    )
    return _source_natural_id_from_snapshot(
        dedupe_key=dedupe_key,
        text=text,
    )


def telegram_notification_outbox_dedupe_from_source(
    source_natural_id: Any,
) -> str | None:
    identity = str(source_natural_id or "").strip()
    if _SOURCE_SEPARATOR not in identity:
        return None
    dedupe_key, fingerprint = identity.rsplit(_SOURCE_SEPARATOR, 1)
    if not dedupe_key or len(fingerprint) != 24:
        return None
    if any(character not in "0123456789abcdef" for character in fingerprint):
        return None
    return dedupe_key


def build_new_user_membership_payload(
    outbox: TelegramNotificationOutbox | Any,
    user: User | Any,
) -> dict[str, Any]:
    _dedupe, _source_id, _recipient_user_id, text = _validate_source_contract(
        outbox
    )
    telegram_id = _strict_positive_int(getattr(user, "telegram_id", None))
    if telegram_id is None:
        raise ValueError("new_user_membership_current_chat_id_invalid")
    return {"chat_id": telegram_id, "text": text, "parse_mode": None}


def telegram_new_user_membership_campaign_id(source_id: int) -> str:
    normalized = _strict_positive_int(source_id)
    if normalized is None:
        raise ValueError("new_user_membership_campaign_source_invalid")
    return f"new-user-membership:{normalized}"


def _validate_static_route(
    job: TelegramDeliveryJobRecord,
) -> TelegramFreshnessDecision | None:
    if _enum_value(job.action_kind) != TelegramDeliveryAction.NEW_USER_MEMBERSHIP.value:
        return _quarantined("new_user_membership_freshness_action_mismatch")
    if _enum_value(job.feeder_kind) != TelegramFeederKind.ADMIN_SYSTEM.value:
        return _quarantined("new_user_membership_freshness_feeder_mismatch")
    if _enum_value(job.destination_class) != TelegramDestinationClass.PRIVATE.value:
        return _quarantined(
            "new_user_membership_freshness_destination_class_mismatch"
        )
    if str(job.method or "") != "sendMessage":
        return _quarantined("new_user_membership_freshness_method_mismatch")
    if str(job.bot_identity or "") != "primary":
        return _quarantined("new_user_membership_freshness_bot_identity_mismatch")
    if str(job.template_version or "") != NEW_USER_MEMBERSHIP_TEMPLATE_VERSION:
        return _quarantined("new_user_membership_freshness_template_mismatch")
    if (
        job.delivery_deadline_at is not None
        or job.freshness_deadline_at is not None
        or job.run_id is not None
    ):
        return _quarantined("new_user_membership_freshness_deadline_forbidden")
    return None


async def _load_outbox(
    db: AsyncSession,
    *,
    dedupe_key: str,
) -> TelegramNotificationOutbox | None:
    return (
        await db.execute(
            select(TelegramNotificationOutbox)
            .where(TelegramNotificationOutbox.dedupe_key == dedupe_key)
            .limit(1)
        )
    ).scalar_one_or_none()


def _outbox_shape_decision(
    job: TelegramDeliveryJobRecord,
    outbox: TelegramNotificationOutbox | Any,
) -> TelegramFreshnessDecision | None:
    job_id = _strict_positive_int(getattr(job, "id", None))
    outbox_id = _strict_positive_int(getattr(outbox, "id", None))
    try:
        _dedupe, source_id, recipient_user_id, _text = _validate_source_contract(
            outbox
        )
    except ValueError:
        return _quarantined("new_user_membership_freshness_source_contract_invalid")
    if job_id is None or outbox_id is None:
        return _quarantined("new_user_membership_freshness_outbox_shape_invalid")
    if _strict_positive_int(getattr(outbox, "telegram_id_at_enqueue", None)) is None:
        return _quarantined("new_user_membership_freshness_enqueue_identity_invalid")
    if _strict_positive_int(getattr(outbox, "queue_job_id", None)) != job_id:
        return _quarantined("new_user_membership_freshness_queue_owner_mismatch")
    if not isinstance(getattr(outbox, "queue_handed_off_at", None), datetime):
        return _quarantined("new_user_membership_freshness_handoff_missing")
    if str(job.destination_key or "") != telegram_new_user_membership_destination_key(
        recipient_user_id
    ):
        return _quarantined("new_user_membership_freshness_destination_mismatch")
    if str(job.campaign_id or "") != telegram_new_user_membership_campaign_id(
        source_id
    ):
        return _quarantined("new_user_membership_freshness_campaign_mismatch")
    return None


def _stored_snapshot_decision(
    job: TelegramDeliveryJobRecord,
    outbox: TelegramNotificationOutbox | Any,
) -> TelegramFreshnessDecision | None:
    try:
        normalized_payload, payload_hash = canonical_telegram_delivery_payload(
            job.payload
        )
    except (TelegramDeliveryQueueValidationError, TypeError, ValueError):
        return _quarantined("new_user_membership_freshness_payload_not_json")
    if str(job.payload_hash or "") != payload_hash:
        return _quarantined("new_user_membership_freshness_payload_hash_mismatch")
    job_text = normalized_payload.get("text")
    if (
        set(normalized_payload) != {"chat_id", "text", "parse_mode"}
        or _strict_positive_int(normalized_payload.get("chat_id")) is None
        or not isinstance(job_text, str)
        or not job_text.strip()
        or normalized_payload.get("parse_mode") is not None
    ):
        return _quarantined("new_user_membership_freshness_payload_shape_invalid")
    try:
        snapshot_source = _source_natural_id_from_snapshot(
            dedupe_key=str(outbox.dedupe_key),
            text=job_text,
        )
        current_source = telegram_new_user_membership_source_natural_id(outbox)
    except ValueError:
        return _quarantined("new_user_membership_freshness_snapshot_invalid")
    if str(job.source_natural_id or "") != snapshot_source:
        return _quarantined("new_user_membership_freshness_fingerprint_invalid")
    if snapshot_source != current_source:
        return _decision(
            TelegramFreshnessOutcome.RECLASSIFY,
            replacement_action=TelegramDeliveryAction.NEW_USER_MEMBERSHIP,
            reason="new_user_membership_freshness_content_changed",
        )
    return None


async def validate_new_user_membership_telegram_delivery_freshness(
    db: AsyncSession,
    job: TelegramDeliveryJobRecord,
    now: datetime,
) -> TelegramFreshnessDecision:
    """Rebuild a membership announcement from its durable outbox intent."""
    del now
    static_decision = _validate_static_route(job)
    if static_decision is not None:
        return static_decision
    dedupe_key = telegram_notification_outbox_dedupe_from_source(
        job.source_natural_id
    )
    if dedupe_key is None:
        return _quarantined("new_user_membership_freshness_source_identity_invalid")
    outbox = await _load_outbox(db, dedupe_key=dedupe_key)
    if outbox is None:
        return _decision(
            TelegramFreshnessOutcome.WAIT_DEPENDENCY,
            reason="new_user_membership_freshness_outbox_missing",
        )
    shape_decision = _outbox_shape_decision(job, outbox)
    if shape_decision is not None:
        return shape_decision

    status = _enum_value(outbox.status)
    if status == TelegramNotificationOutboxStatus.SENT.value:
        if _strict_positive_int(outbox.telegram_message_id) is None:
            return _quarantined(
                "new_user_membership_freshness_sent_evidence_missing"
            )
        return _decision(
            TelegramFreshnessOutcome.SENT_NOOP,
            reason="new_user_membership_freshness_already_sent",
        )
    if outbox.telegram_message_id is not None:
        return _quarantined(
            "new_user_membership_freshness_unsent_message_evidence"
        )
    if status in _TERMINAL_STATUSES:
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="new_user_membership_freshness_outbox_terminal",
        )
    if status == TelegramNotificationOutboxStatus.SENDING.value:
        return _decision(
            TelegramFreshnessOutcome.WAIT_DEPENDENCY,
            reason="new_user_membership_freshness_legacy_sending",
        )
    if status not in _ACTIVE_STATUSES:
        return _quarantined("new_user_membership_freshness_status_invalid")

    snapshot_decision = _stored_snapshot_decision(job, outbox)
    if snapshot_decision is not None:
        return snapshot_decision

    recipient_user_id = _strict_positive_int(outbox.recipient_user_id)
    if recipient_user_id is None:
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="new_user_membership_freshness_recipient_missing",
        )
    user = await db.get(User, recipient_user_id)
    if user is None:
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="new_user_membership_freshness_recipient_missing",
        )
    try:
        current_version = telegram_new_user_membership_source_version(user)
    except ValueError:
        return _quarantined("new_user_membership_freshness_version_invalid")
    source_version = _strict_positive_int(job.source_version)
    if source_version is None:
        return _quarantined("new_user_membership_freshness_source_version_invalid")
    if source_version > current_version:
        return _decision(
            TelegramFreshnessOutcome.WAIT_DEPENDENCY,
            reason="new_user_membership_freshness_version_ahead",
        )
    if source_version < current_version:
        return _decision(
            TelegramFreshnessOutcome.RECLASSIFY,
            replacement_action=TelegramDeliveryAction.NEW_USER_MEMBERSHIP,
            reason="new_user_membership_freshness_recipient_version_changed",
        )
    if _strict_positive_int(user.telegram_id) is None:
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="new_user_membership_freshness_recipient_unlinked",
        )
    access = await evaluate_bot_access(db, user)
    if not access.allowed:
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="new_user_membership_freshness_recipient_access_denied",
        )
    if await get_active_customer_relation_for_user(db, recipient_user_id) is not None:
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="new_user_membership_freshness_customer_excluded",
        )
    try:
        expected_payload = build_new_user_membership_payload(outbox, user)
        normalized_payload, _ = canonical_telegram_delivery_payload(job.payload)
    except (TelegramDeliveryQueueValidationError, TypeError, ValueError):
        return _quarantined(
            "new_user_membership_freshness_payload_not_authoritative"
        )
    if normalized_payload != expected_payload:
        return _quarantined(
            "new_user_membership_freshness_payload_not_authoritative"
        )
    return _decision(
        TelegramFreshnessOutcome.SEND,
        reason="new_user_membership_freshness_current",
    )


@dataclass(frozen=True, slots=True)
class NewUserMembershipTelegramDeliveryFreshnessValidator:
    async def __call__(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        now: datetime,
    ) -> TelegramFreshnessDecision:
        return await validate_new_user_membership_telegram_delivery_freshness(
            db,
            job,
            now,
        )
