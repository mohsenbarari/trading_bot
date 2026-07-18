"""Authoritative freshness for queued repeat-offer bot responses."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards import get_persistent_menu_keyboard
from bot.repeat_offer import (
    load_latest_bot_repeat_offer_candidate,
    prepend_repeat_offer_button,
)
from core.config import settings
from core.public_webapp_url import user_facing_webapp_url
from core.services.bot_access_policy import evaluate_bot_access
from core.services.telegram_delivery_queue_service import (
    TelegramDeliveryQueueValidationError,
    canonical_telegram_delivery_payload,
)
from core.services.telegram_notification_outbox_service import (
    TELEGRAM_NOTIFICATION_SOURCE_OFFER_REPEAT_RESPONSE,
    telegram_notification_dedupe_key,
    validate_offer_repeat_response_contract,
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


REPEAT_OFFER_RESPONSE_FRESHNESS_ACTIONS = frozenset(
    {TelegramDeliveryAction.OFFER_REPEAT_RESPONSE}
)
REPEAT_OFFER_RESPONSE_TEMPLATE_VERSION = "offer-repeat-response-v1"
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


@dataclass(frozen=True, slots=True)
class RepeatOfferResponseSnapshot:
    payload: dict[str, Any]
    source_version: int


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


def telegram_repeat_offer_response_destination_key(recipient_user_id: int) -> str:
    normalized = _strict_positive_int(recipient_user_id)
    if normalized is None:
        raise ValueError("repeat_offer_response_recipient_user_id_invalid")
    return f"private:user:{normalized}"


def _validate_source_contract(
    outbox: TelegramNotificationOutbox | Any,
) -> tuple[str, int, str, str]:
    source_type = str(getattr(outbox, "source_type", "") or "").strip()
    if source_type != TELEGRAM_NOTIFICATION_SOURCE_OFFER_REPEAT_RESPONSE:
        raise ValueError("repeat_offer_response_source_type_unsupported")
    source_id = str(getattr(outbox, "source_id", "") or "").strip()
    if not source_id or len(source_id) > 120:
        raise ValueError("repeat_offer_response_source_id_invalid")
    recipient_user_id = _strict_positive_int(
        getattr(outbox, "recipient_user_id", None)
    )
    if recipient_user_id is None:
        raise ValueError("repeat_offer_response_recipient_invalid")
    expected_dedupe = telegram_notification_dedupe_key(
        source_type=source_type,
        source_id=source_id,
        recipient_user_id=recipient_user_id,
    )
    dedupe_key = str(getattr(outbox, "dedupe_key", "") or "").strip()
    if dedupe_key != expected_dedupe:
        raise ValueError("repeat_offer_response_dedupe_invalid")
    if getattr(outbox, "parse_mode", None) is not None:
        raise ValueError("repeat_offer_response_parse_mode_forbidden")
    extra_payload = getattr(outbox, "extra_payload", None)
    if not isinstance(extra_payload, Mapping) or set(extra_payload) != {
        "response_kind"
    }:
        raise ValueError("repeat_offer_response_extra_payload_invalid")
    kind, text = validate_offer_repeat_response_contract(
        text=str(getattr(outbox, "text", "") or ""),
        response_kind=str(extra_payload.get("response_kind") or ""),
    )
    return dedupe_key, recipient_user_id, kind, text


def _source_natural_id_from_snapshot(
    *,
    dedupe_key: str,
    response_kind: str,
    text: str,
) -> str:
    snapshot = json.dumps(
        {
            "dedupe_key": dedupe_key,
            "parse_mode": None,
            "response_kind": response_kind,
            "template_version": REPEAT_OFFER_RESPONSE_TEMPLATE_VERSION,
            "text": text,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    fingerprint = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()[:24]
    identity = f"{dedupe_key}{_SOURCE_SEPARATOR}{fingerprint}"
    if len(identity) > 256:
        raise ValueError("repeat_offer_response_source_identity_too_long")
    return identity


def telegram_repeat_offer_response_source_natural_id(
    outbox: TelegramNotificationOutbox | Any,
) -> str:
    dedupe_key, _recipient_user_id, response_kind, text = (
        _validate_source_contract(outbox)
    )
    return _source_natural_id_from_snapshot(
        dedupe_key=dedupe_key,
        response_kind=response_kind,
        text=text,
    )


def telegram_repeat_offer_outbox_dedupe_from_source(
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


async def build_repeat_offer_response_snapshot(
    db: AsyncSession,
    outbox: TelegramNotificationOutbox | Any,
    user: User | Any,
) -> RepeatOfferResponseSnapshot:
    _dedupe, recipient_user_id, _response_kind, text = _validate_source_contract(
        outbox
    )
    if _strict_positive_int(getattr(user, "id", None)) != recipient_user_id:
        raise ValueError("repeat_offer_response_user_mismatch")
    telegram_id = _strict_positive_int(getattr(user, "telegram_id", None))
    if telegram_id is None:
        raise ValueError("repeat_offer_response_current_chat_id_invalid")
    user_sync_version = _strict_positive_int(getattr(user, "sync_version", None))
    if user_sync_version is None:
        raise ValueError("repeat_offer_response_recipient_version_invalid")
    candidate = await load_latest_bot_repeat_offer_candidate(
        db,
        owner_user_id=recipient_user_id,
    )
    keyboard = prepend_repeat_offer_button(
        get_persistent_menu_keyboard(
            getattr(user, "role", None),
            user_facing_webapp_url(settings_obj=settings),
        ),
        candidate,
    )
    payload = {
        "chat_id": telegram_id,
        "text": text,
        "parse_mode": None,
        "reply_markup": keyboard.model_dump(mode="json", exclude_none=True),
    }
    _normalized_payload, payload_hash = canonical_telegram_delivery_payload(payload)
    version_snapshot = json.dumps(
        {
            "candidate_public_id": (
                str(candidate.source_offer_public_id) if candidate is not None else None
            ),
            "candidate_source_id": (
                int(candidate.source_offer_id) if candidate is not None else None
            ),
            "payload_hash": payload_hash,
            "recipient_user_id": recipient_user_id,
            "role": str(
                getattr(getattr(user, "role", None), "value", getattr(user, "role", ""))
                or ""
            ),
            "telegram_id": telegram_id,
            "user_sync_version": user_sync_version,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    source_version = int.from_bytes(
        hashlib.sha256(version_snapshot.encode("utf-8")).digest()[:8],
        "big",
    ) & ((1 << 63) - 1)
    return RepeatOfferResponseSnapshot(
        payload=payload,
        source_version=source_version or 1,
    )


async def build_repeat_offer_response_payload(
    db: AsyncSession,
    outbox: TelegramNotificationOutbox | Any,
    user: User | Any,
) -> dict[str, Any]:
    snapshot = await build_repeat_offer_response_snapshot(db, outbox, user)
    return snapshot.payload


def _validate_static_route(
    job: TelegramDeliveryJobRecord,
) -> TelegramFreshnessDecision | None:
    if _enum_value(job.action_kind) != TelegramDeliveryAction.OFFER_REPEAT_RESPONSE.value:
        return _quarantined("repeat_offer_response_freshness_action_mismatch")
    if _enum_value(job.feeder_kind) != TelegramFeederKind.OFFER_CONTROL.value:
        return _quarantined("repeat_offer_response_freshness_feeder_mismatch")
    if _enum_value(job.destination_class) != TelegramDestinationClass.PRIVATE.value:
        return _quarantined("repeat_offer_response_freshness_destination_class_mismatch")
    if str(job.method or "") != "sendMessage":
        return _quarantined("repeat_offer_response_freshness_method_mismatch")
    if str(job.bot_identity or "") != "primary":
        return _quarantined("repeat_offer_response_freshness_bot_identity_mismatch")
    if str(job.template_version or "") != REPEAT_OFFER_RESPONSE_TEMPLATE_VERSION:
        return _quarantined("repeat_offer_response_freshness_template_mismatch")
    if (
        job.delivery_deadline_at is not None
        or job.freshness_deadline_at is not None
        or job.campaign_id is not None
        or job.run_id is not None
    ):
        return _quarantined("repeat_offer_response_freshness_deadline_forbidden")
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


def _binding_decision(
    job: TelegramDeliveryJobRecord,
    outbox: TelegramNotificationOutbox | Any,
) -> TelegramFreshnessDecision | None:
    job_id = _strict_positive_int(getattr(job, "id", None))
    outbox_id = _strict_positive_int(getattr(outbox, "id", None))
    try:
        _dedupe, recipient_user_id, _kind, _text = _validate_source_contract(outbox)
    except ValueError:
        return _quarantined("repeat_offer_response_freshness_source_contract_invalid")
    if job_id is None or outbox_id is None:
        return _quarantined("repeat_offer_response_freshness_outbox_shape_invalid")
    if _strict_positive_int(getattr(outbox, "telegram_id_at_enqueue", None)) is None:
        return _quarantined("repeat_offer_response_freshness_enqueue_identity_invalid")
    if _strict_positive_int(getattr(outbox, "queue_job_id", None)) != job_id:
        return _quarantined("repeat_offer_response_freshness_queue_owner_mismatch")
    if not isinstance(getattr(outbox, "queue_handed_off_at", None), datetime):
        return _quarantined("repeat_offer_response_freshness_handoff_missing")
    if str(job.destination_key or "") != telegram_repeat_offer_response_destination_key(
        recipient_user_id
    ):
        return _quarantined("repeat_offer_response_freshness_destination_mismatch")
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
        return _quarantined("repeat_offer_response_freshness_payload_not_json")
    if str(job.payload_hash or "") != payload_hash:
        return _quarantined("repeat_offer_response_freshness_payload_hash_mismatch")
    if (
        set(normalized_payload)
        != {"chat_id", "text", "parse_mode", "reply_markup"}
        or _strict_positive_int(normalized_payload.get("chat_id")) is None
        or normalized_payload.get("parse_mode") is not None
        or not isinstance(normalized_payload.get("text"), str)
        or not isinstance(normalized_payload.get("reply_markup"), Mapping)
    ):
        return _quarantined("repeat_offer_response_freshness_payload_shape_invalid")
    try:
        snapshot_source = telegram_repeat_offer_response_source_natural_id(outbox)
    except ValueError:
        return _quarantined("repeat_offer_response_freshness_snapshot_invalid")
    if str(job.source_natural_id or "") != snapshot_source:
        return _quarantined("repeat_offer_response_freshness_fingerprint_invalid")
    return None


async def validate_repeat_offer_response_telegram_delivery_freshness(
    db: AsyncSession,
    job: TelegramDeliveryJobRecord,
    now: datetime,
) -> TelegramFreshnessDecision:
    del now
    route_decision = _validate_static_route(job)
    if route_decision is not None:
        return route_decision
    dedupe_key = telegram_repeat_offer_outbox_dedupe_from_source(
        job.source_natural_id
    )
    if dedupe_key is None:
        return _quarantined("repeat_offer_response_freshness_source_invalid")
    outbox = await _load_outbox(db, dedupe_key=dedupe_key)
    if outbox is None:
        return _quarantined("repeat_offer_response_freshness_outbox_missing")

    status = _enum_value(outbox.status)
    if status in _TERMINAL_STATUSES:
        if status == TelegramNotificationOutboxStatus.SENT.value:
            if _strict_positive_int(outbox.telegram_message_id) is None:
                return _quarantined(
                    "repeat_offer_response_freshness_sent_without_evidence"
                )
            return _decision(
                TelegramFreshnessOutcome.SENT_NOOP,
                reason="repeat_offer_response_freshness_already_sent",
            )
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="repeat_offer_response_freshness_outbox_terminal",
        )
    if status not in _ACTIVE_STATUSES:
        return _quarantined("repeat_offer_response_freshness_outbox_state_invalid")

    binding_decision = _binding_decision(job, outbox)
    if binding_decision is not None:
        return binding_decision
    snapshot_decision = _stored_snapshot_decision(job, outbox)
    if snapshot_decision is not None:
        return snapshot_decision

    try:
        _dedupe, recipient_user_id, _kind, _text = _validate_source_contract(outbox)
    except ValueError:
        return _quarantined("repeat_offer_response_freshness_source_contract_invalid")
    user = await db.get(User, recipient_user_id)
    if user is None:
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="repeat_offer_response_freshness_recipient_missing",
        )
    source_version = _strict_positive_int(getattr(job, "source_version", None))
    if source_version is None:
        return _quarantined("repeat_offer_response_freshness_version_invalid")
    if _strict_positive_int(getattr(user, "telegram_id", None)) is None:
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="repeat_offer_response_freshness_recipient_unlinked",
        )
    access = await evaluate_bot_access(db, user)
    if not access.allowed:
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="repeat_offer_response_freshness_recipient_access_denied",
        )

    try:
        current_snapshot = await build_repeat_offer_response_snapshot(db, outbox, user)
        current_payload = current_snapshot.payload
        normalized_current, current_hash = canonical_telegram_delivery_payload(
            current_payload
        )
        normalized_stored, stored_hash = canonical_telegram_delivery_payload(
            job.payload
        )
    except (TelegramDeliveryQueueValidationError, TypeError, ValueError):
        return _quarantined("repeat_offer_response_freshness_current_payload_invalid")
    if (
        source_version != current_snapshot.source_version
        or current_hash != stored_hash
        or normalized_current != normalized_stored
    ):
        return _decision(
            TelegramFreshnessOutcome.RECLASSIFY,
            replacement_action=TelegramDeliveryAction.OFFER_REPEAT_RESPONSE,
            reason="repeat_offer_response_freshness_current_state_changed",
        )
    return _decision(
        TelegramFreshnessOutcome.SEND,
        reason="repeat_offer_response_freshness_current",
    )
