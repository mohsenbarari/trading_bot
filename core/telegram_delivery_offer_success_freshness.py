"""Authoritative freshness for the private Offer success preview edit."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.services.bot_access_policy import evaluate_bot_access
from core.services.telegram_delivery_queue_service import (
    TelegramDeliveryQueueValidationError,
    canonical_telegram_delivery_payload,
)
from core.services.telegram_notification_outbox_service import (
    telegram_notification_dedupe_key,
    validate_telegram_notification_text,
)
from core.services.telegram_offer_channel_service import build_offer_channel_message
from core.telegram_delivery_offer_success_contract import (
    OFFER_SUCCESS_TEMPLATE_VERSION,
    TELEGRAM_NOTIFICATION_SOURCE_OFFER_SUCCESS,
    build_offer_success_reply_markup,
    build_offer_success_text,
    validate_offer_success_copy,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDestinationClass,
    TelegramFeederKind,
    TelegramFreshnessDecision,
    TelegramFreshnessOutcome,
)
from models.offer import Offer, OfferStatus
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from models.telegram_notification_outbox import (
    TelegramNotificationOutbox,
    TelegramNotificationOutboxStatus,
)
from models.user import User


OFFER_SUCCESS_FRESHNESS_ACTIONS = frozenset(
    {TelegramDeliveryAction.OFFER_SUCCESS}
)
_SOURCE_SEPARATOR = ":offer-success-v1:"
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


@dataclass(frozen=True, slots=True)
class TelegramOfferSuccessSource:
    dedupe_key: str
    offer_public_id: str
    expected_offer_version: int
    preview_message_id: int
    recipient_user_id: int
    success_copy: str
    text_at_enqueue: str
    telegram_id_at_enqueue: int
    expected_user_sync_version: int


@dataclass(frozen=True, slots=True)
class TelegramOfferSuccessSnapshot:
    payload: dict[str, Any]
    source_version: int


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _positive_int(value: Any) -> int | None:
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


def telegram_offer_success_destination_key(recipient_user_id: Any) -> str:
    normalized = _positive_int(recipient_user_id)
    if normalized is None:
        raise ValueError("offer_success_recipient_user_id_invalid")
    return f"private:user:{normalized}"


def _validate_source_contract(
    outbox: TelegramNotificationOutbox | Any,
) -> TelegramOfferSuccessSource:
    if (
        str(getattr(outbox, "source_type", "") or "").strip()
        != TELEGRAM_NOTIFICATION_SOURCE_OFFER_SUCCESS
    ):
        raise ValueError("offer_success_source_type_invalid")
    offer_public_id = str(getattr(outbox, "source_id", "") or "").strip()
    if not offer_public_id or len(offer_public_id) > 40:
        raise ValueError("offer_success_public_id_invalid")
    recipient_user_id = _positive_int(
        getattr(outbox, "recipient_user_id", None)
    )
    telegram_id_at_enqueue = _positive_int(
        getattr(outbox, "telegram_id_at_enqueue", None)
    )
    if recipient_user_id is None or telegram_id_at_enqueue is None:
        raise ValueError("offer_success_recipient_invalid")
    dedupe_key = str(getattr(outbox, "dedupe_key", "") or "").strip()
    expected_dedupe = telegram_notification_dedupe_key(
        source_type=TELEGRAM_NOTIFICATION_SOURCE_OFFER_SUCCESS,
        source_id=offer_public_id,
        recipient_user_id=recipient_user_id,
    )
    if dedupe_key != expected_dedupe:
        raise ValueError("offer_success_dedupe_invalid")
    if str(getattr(outbox, "parse_mode", "") or "") != "Markdown":
        raise ValueError("offer_success_parse_mode_invalid")
    text_at_enqueue = validate_telegram_notification_text(
        str(getattr(outbox, "text", "") or "")
    )
    extra_payload = getattr(outbox, "extra_payload", None)
    if not isinstance(extra_payload, Mapping) or set(extra_payload) != {
        "offer_public_id",
        "offer_version",
        "preview_message_id",
        "success_copy",
        "user_sync_version",
    }:
        raise ValueError("offer_success_extra_payload_invalid")
    if str(extra_payload.get("offer_public_id") or "").strip() != offer_public_id:
        raise ValueError("offer_success_public_id_mismatch")
    expected_offer_version = _positive_int(extra_payload.get("offer_version"))
    preview_message_id = _positive_int(extra_payload.get("preview_message_id"))
    expected_user_sync_version = _positive_int(
        extra_payload.get("user_sync_version")
    )
    if expected_offer_version is None:
        raise ValueError("offer_success_offer_version_invalid")
    if preview_message_id is None:
        raise ValueError("offer_success_message_id_invalid")
    if expected_user_sync_version is None:
        raise ValueError("offer_success_user_version_invalid")
    success_copy = validate_offer_success_copy(extra_payload.get("success_copy"))
    return TelegramOfferSuccessSource(
        dedupe_key=dedupe_key,
        offer_public_id=offer_public_id,
        expected_offer_version=expected_offer_version,
        preview_message_id=preview_message_id,
        recipient_user_id=recipient_user_id,
        success_copy=success_copy,
        text_at_enqueue=text_at_enqueue,
        telegram_id_at_enqueue=telegram_id_at_enqueue,
        expected_user_sync_version=expected_user_sync_version,
    )


def telegram_offer_success_source_natural_id(
    outbox: TelegramNotificationOutbox | Any,
) -> str:
    source = _validate_source_contract(outbox)
    snapshot = json.dumps(
        {
            "dedupe_key": source.dedupe_key,
            "offer_version": source.expected_offer_version,
            "preview_message_id": source.preview_message_id,
            "success_copy": source.success_copy,
            "telegram_id_at_enqueue": source.telegram_id_at_enqueue,
            "text_at_enqueue": source.text_at_enqueue,
            "user_sync_version": source.expected_user_sync_version,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    fingerprint = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()[:24]
    identity = f"{source.dedupe_key}{_SOURCE_SEPARATOR}{fingerprint}"
    if len(identity) > 256:
        raise ValueError("offer_success_source_identity_too_long")
    return identity


def telegram_offer_success_outbox_dedupe_from_source(
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


async def load_offer_success_offer(
    db: AsyncSession,
    offer_public_id: str,
) -> Offer | None:
    return (
        await db.execute(
            select(Offer)
            .options(selectinload(Offer.commodity))
            .where(Offer.offer_public_id == offer_public_id)
            .limit(1)
        )
    ).scalar_one_or_none()


def telegram_offer_success_outbox_waits_for_state(
    outbox: TelegramNotificationOutbox | Any,
    user: User | Any,
    offer: Offer | Any | None,
) -> bool:
    source = _validate_source_contract(outbox)
    user_version = _positive_int(getattr(user, "sync_version", None))
    if user_version is not None and user_version < source.expected_user_sync_version:
        return True
    if offer is None:
        # This source is created in the same foreign-local transaction as the
        # Telegram-origin Offer. Missing source data is not cross-server lag.
        return False
    offer_version = _positive_int(getattr(offer, "version_id", None))
    return bool(
        offer_version is not None
        and offer_version < source.expected_offer_version
    )


def telegram_offer_success_outbox_matches_current_state(
    outbox: TelegramNotificationOutbox | Any,
    user: User | Any,
    offer: Offer | Any | None,
) -> bool:
    if offer is None:
        return False
    source = _validate_source_contract(outbox)
    return bool(
        _positive_int(getattr(user, "id", None)) == source.recipient_user_id
        and _positive_int(getattr(user, "telegram_id", None))
        == source.telegram_id_at_enqueue
        and _positive_int(getattr(offer, "user_id", None))
        == source.recipient_user_id
        and str(getattr(offer, "offer_public_id", "") or "").strip()
        == source.offer_public_id
        and _enum_value(getattr(offer, "status", None))
        == OfferStatus.ACTIVE.value
    )


def build_telegram_offer_success_snapshot(
    outbox: TelegramNotificationOutbox | Any,
    user: User | Any,
    offer: Offer | Any,
) -> TelegramOfferSuccessSnapshot:
    source = _validate_source_contract(outbox)
    if not telegram_offer_success_outbox_matches_current_state(outbox, user, offer):
        raise ValueError("offer_success_current_state_invalid")
    user_sync_version = _positive_int(getattr(user, "sync_version", None))
    offer_version = _positive_int(getattr(offer, "version_id", None))
    offer_id = _positive_int(getattr(offer, "id", None))
    if user_sync_version is None or offer_version is None or offer_id is None:
        raise ValueError("offer_success_current_version_invalid")
    text = build_offer_success_text(
        success_copy=source.success_copy,
        offer_text=build_offer_channel_message(offer),
    )
    if source.text_at_enqueue != text:
        raise ValueError("offer_success_authoritative_text_mismatch")
    payload, payload_hash = canonical_telegram_delivery_payload(
        {
            "chat_id": source.telegram_id_at_enqueue,
            "message_id": source.preview_message_id,
            "text": text,
            "parse_mode": "Markdown",
            "reply_markup": build_offer_success_reply_markup(offer_id),
        }
    )
    version_snapshot = json.dumps(
        {
            "offer_version": offer_version,
            "payload_hash": payload_hash,
            "user_sync_version": user_sync_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    source_version = int.from_bytes(
        hashlib.sha256(version_snapshot.encode("utf-8")).digest()[:8],
        "big",
    ) & ((1 << 63) - 1)
    return TelegramOfferSuccessSnapshot(
        payload=payload,
        source_version=source_version or 1,
    )


def _validate_static_route(
    job: TelegramDeliveryJobRecord,
) -> TelegramFreshnessDecision | None:
    if _enum_value(job.action_kind) != TelegramDeliveryAction.OFFER_SUCCESS.value:
        return _quarantined("offer_success_freshness_action_mismatch")
    if _enum_value(job.feeder_kind) != TelegramFeederKind.OFFER_CONTROL.value:
        return _quarantined("offer_success_freshness_feeder_mismatch")
    if _enum_value(job.destination_class) != TelegramDestinationClass.PRIVATE.value:
        return _quarantined("offer_success_freshness_destination_class_mismatch")
    if str(job.method or "") != "editMessageText":
        return _quarantined("offer_success_freshness_method_mismatch")
    if str(job.bot_identity or "") != "primary":
        return _quarantined("offer_success_freshness_bot_identity_mismatch")
    if str(job.template_version or "") != OFFER_SUCCESS_TEMPLATE_VERSION:
        return _quarantined("offer_success_freshness_template_mismatch")
    if (
        job.delivery_deadline_at is not None
        or job.freshness_deadline_at is not None
        or job.campaign_id is not None
        or job.run_id is not None
    ):
        return _quarantined("offer_success_freshness_deadline_forbidden")
    return None


async def validate_offer_success_telegram_delivery_freshness(
    db: AsyncSession,
    job: TelegramDeliveryJobRecord,
    now: datetime,
) -> TelegramFreshnessDecision:
    del now
    route_decision = _validate_static_route(job)
    if route_decision is not None:
        return route_decision
    dedupe_key = telegram_offer_success_outbox_dedupe_from_source(
        job.source_natural_id
    )
    if dedupe_key is None:
        return _quarantined("offer_success_freshness_source_invalid")
    outbox = (
        await db.execute(
            select(TelegramNotificationOutbox)
            .where(TelegramNotificationOutbox.dedupe_key == dedupe_key)
            .limit(1)
        )
    ).scalar_one_or_none()
    if outbox is None:
        return _quarantined("offer_success_freshness_outbox_missing")
    try:
        source = _validate_source_contract(outbox)
        expected_source = telegram_offer_success_source_natural_id(outbox)
        normalized_stored, stored_hash = canonical_telegram_delivery_payload(
            job.payload
        )
    except (TelegramDeliveryQueueValidationError, TypeError, ValueError):
        return _quarantined("offer_success_freshness_source_contract_invalid")
    if str(job.source_natural_id or "") != expected_source:
        return _quarantined("offer_success_freshness_source_identity_mismatch")
    if str(job.payload_hash or "") != stored_hash:
        return _quarantined("offer_success_freshness_payload_hash_mismatch")
    if _positive_int(getattr(outbox, "queue_job_id", None)) != _positive_int(
        getattr(job, "id", None)
    ):
        return _quarantined("offer_success_freshness_queue_owner_mismatch")
    if not isinstance(getattr(outbox, "queue_handed_off_at", None), datetime):
        return _quarantined("offer_success_freshness_handoff_missing")
    if str(job.destination_key or "") != telegram_offer_success_destination_key(
        source.recipient_user_id
    ):
        return _quarantined("offer_success_freshness_destination_mismatch")
    status = _enum_value(outbox.status)
    if status == TelegramNotificationOutboxStatus.SENT.value:
        if _positive_int(outbox.telegram_message_id) is None:
            return _quarantined("offer_success_freshness_sent_without_evidence")
        return _decision(
            TelegramFreshnessOutcome.SENT_NOOP,
            reason="offer_success_freshness_already_sent",
        )
    if status in _TERMINAL_STATUSES:
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="offer_success_freshness_outbox_terminal",
        )
    if status not in _ACTIVE_STATUSES:
        return _quarantined("offer_success_freshness_outbox_state_invalid")
    user = await db.get(User, source.recipient_user_id)
    offer = await load_offer_success_offer(db, source.offer_public_id)
    if user is None:
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="offer_success_freshness_recipient_missing",
        )
    if telegram_offer_success_outbox_waits_for_state(outbox, user, offer):
        return _decision(
            TelegramFreshnessOutcome.WAIT_DEPENDENCY,
            reason="offer_success_freshness_source_version_pending",
        )
    if not telegram_offer_success_outbox_matches_current_state(outbox, user, offer):
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="offer_success_freshness_source_state_changed",
        )
    access = await evaluate_bot_access(db, user)
    if not access.allowed:
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="offer_success_freshness_recipient_access_denied",
        )
    try:
        current = build_telegram_offer_success_snapshot(outbox, user, offer)
    except (TelegramDeliveryQueueValidationError, TypeError, ValueError):
        return _quarantined("offer_success_freshness_current_payload_invalid")
    if (
        _positive_int(job.source_version) != current.source_version
        or normalized_stored != current.payload
    ):
        return _decision(
            TelegramFreshnessOutcome.RECLASSIFY,
            replacement_action=TelegramDeliveryAction.OFFER_SUCCESS,
            reason="offer_success_freshness_source_version_changed",
        )
    return _decision(
        TelegramFreshnessOutcome.SEND,
        reason="offer_success_freshness_current",
    )


@dataclass(frozen=True, slots=True)
class OfferSuccessTelegramDeliveryFreshnessValidator:
    async def __call__(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        now: datetime,
    ) -> TelegramFreshnessDecision:
        return await validate_offer_success_telegram_delivery_freshness(
            db,
            job,
            now,
        )
