"""Authoritative pre-dispatch freshness checks for Offer Telegram jobs."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.services.offer_publication_state_service import (
    CanonicalPublicationIdentityError,
    canonical_telegram_publication_identity,
    publication_dedupe_key,
)
from core.services.telegram_delivery_queue_service import (
    TelegramDeliveryQueueValidationError,
    canonical_telegram_delivery_payload,
)
from core.services.telegram_offer_channel_service import (
    build_offer_channel_message,
    build_offer_channel_reply_markup,
    get_offer_channel_history_tag,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDestinationClass,
    TelegramFreshnessDecision,
    TelegramFreshnessOutcome,
)
from models.offer import Offer, OfferStatus
from models.offer_publication_state import (
    OfferPublicationState,
    OfferPublicationSurface,
)
from models.telegram_delivery_job import TelegramDeliveryJobRecord


OFFER_PUBLISH_ACTIONS = frozenset({TelegramDeliveryAction.OFFER_PUBLISH})
OFFER_ACTIVE_EDIT_ACTIONS = frozenset(
    {
        TelegramDeliveryAction.PARTIAL_OFFER_EDIT,
        TelegramDeliveryAction.OTHER_ACTIVE_OFFER_EDIT,
        TelegramDeliveryAction.RECONCILIATION_EDIT,
    }
)
OFFER_TERMINAL_EDIT_ACTIONS = frozenset(
    {
        TelegramDeliveryAction.TRADED_OFFER_EDIT,
        TelegramDeliveryAction.EXPIRED_OFFER_EDIT,
        TelegramDeliveryAction.CANCELLED_OFFER_EDIT,
    }
)
OFFER_FRESHNESS_ACTIONS = frozenset(
    OFFER_PUBLISH_ACTIONS
    | OFFER_ACTIVE_EDIT_ACTIONS
    | OFFER_TERMINAL_EDIT_ACTIONS
)

_TERMINAL_ACTION_BY_STATUS = {
    OfferStatus.COMPLETED.value: TelegramDeliveryAction.TRADED_OFFER_EDIT,
    OfferStatus.EXPIRED.value: TelegramDeliveryAction.EXPIRED_OFFER_EDIT,
    OfferStatus.CANCELLED.value: TelegramDeliveryAction.CANCELLED_OFFER_EDIT,
}


class TelegramOfferFreshnessConfigurationError(ValueError):
    """Raised when the validator itself lacks a safe channel contract."""


def _strict_nonzero_int(value: Any, *, reason: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value == 0:
        raise TelegramOfferFreshnessConfigurationError(reason)
    return value


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def telegram_channel_destination_key(chat_id: int) -> str:
    normalized = _strict_nonzero_int(
        chat_id,
        reason="telegram_offer_freshness_expected_channel_invalid",
    )
    return f"channel:{normalized}"


def _decision(
    outcome: TelegramFreshnessOutcome,
    *,
    reason: str,
    replacement_action: TelegramDeliveryAction | None = None,
) -> TelegramFreshnessDecision:
    return TelegramFreshnessDecision(
        outcome=outcome,
        replacement_action=replacement_action,
        reason=reason,
    )


def _quarantined(reason: str) -> TelegramFreshnessDecision:
    return _decision(TelegramFreshnessOutcome.QUARANTINED, reason=reason)


def _strict_payload_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value == 0:
        return None
    return value


def _normalized_time(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _validate_static_route(
    job: TelegramDeliveryJobRecord,
    *,
    action: TelegramDeliveryAction,
    expected_channel_id: int,
) -> TelegramFreshnessDecision | None:
    if _enum_value(job.destination_class) != TelegramDestinationClass.CHANNEL.value:
        return _quarantined("offer_freshness_destination_class_mismatch")
    if str(job.destination_key) != telegram_channel_destination_key(
        expected_channel_id
    ):
        return _quarantined("offer_freshness_destination_key_mismatch")

    payload = job.payload
    if not isinstance(payload, dict):
        return _quarantined("offer_freshness_payload_not_mapping")
    if _strict_payload_int(payload.get("chat_id")) != expected_channel_id:
        return _quarantined("offer_freshness_payload_channel_mismatch")
    if not isinstance(payload.get("text"), str) or not payload["text"]:
        return _quarantined("offer_freshness_payload_text_missing")

    method = str(job.method or "")
    bot_identity = str(job.bot_identity or "")
    if action in OFFER_PUBLISH_ACTIONS:
        if method != "sendMessage" or bot_identity != "primary":
            return _quarantined("offer_freshness_publish_route_mismatch")
        if payload.get("message_id") is not None:
            return _quarantined("offer_freshness_publish_message_id_forbidden")
        return None

    if method != "editMessageText" or bot_identity not in {
        "primary",
        "channel_editor",
    }:
        return _quarantined("offer_freshness_edit_route_mismatch")
    if _strict_payload_int(payload.get("message_id")) is None:
        return _quarantined("offer_freshness_edit_message_id_missing")
    if action in OFFER_TERMINAL_EDIT_ACTIONS and payload.get("reply_markup") != {
        "inline_keyboard": []
    }:
        return _quarantined("offer_freshness_terminal_keyboard_not_removed")
    return None


async def _load_offer(
    db: AsyncSession,
    *,
    offer_public_id: str,
) -> Offer | None:
    result = await db.execute(
        select(Offer)
        .options(selectinload(Offer.commodity))
        .where(Offer.offer_public_id == offer_public_id)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _load_publication_state(
    db: AsyncSession,
    *,
    offer_public_id: str,
) -> OfferPublicationState | None:
    dedupe_key = publication_dedupe_key(
        offer_public_id,
        OfferPublicationSurface.TELEGRAM_CHANNEL,
    )
    result = await db.execute(
        select(OfferPublicationState)
        .where(OfferPublicationState.dedupe_key == dedupe_key)
        .limit(1)
    )
    return result.scalar_one_or_none()


def _source_version_decision(
    job: TelegramDeliveryJobRecord,
    offer: Offer,
    *,
    action: TelegramDeliveryAction,
) -> TelegramFreshnessDecision | None:
    current_version = getattr(offer, "version_id", None)
    source_version = getattr(job, "source_version", None)
    if (
        isinstance(current_version, bool)
        or not isinstance(current_version, int)
        or current_version <= 0
        or isinstance(source_version, bool)
        or not isinstance(source_version, int)
        or source_version <= 0
    ):
        return _quarantined("offer_freshness_source_version_invalid")
    if source_version > current_version:
        return _decision(
            TelegramFreshnessOutcome.WAIT_DEPENDENCY,
            reason="offer_freshness_source_version_ahead",
        )
    if source_version < current_version:
        return _decision(
            TelegramFreshnessOutcome.RECLASSIFY,
            replacement_action=action,
            reason="offer_freshness_source_version_changed",
        )
    return None


def _terminal_reclassification(
    *,
    offer_status: str,
) -> TelegramFreshnessDecision | None:
    replacement = _TERMINAL_ACTION_BY_STATUS.get(offer_status)
    if replacement is None:
        return None
    return _decision(
        TelegramFreshnessOutcome.RECLASSIFY,
        replacement_action=replacement,
        reason="offer_freshness_terminal_state_changed",
    )


def _publication_identity_shape_decision(
    state: OfferPublicationState | None,
    *,
    expected_channel_id: int,
) -> TelegramFreshnessDecision | None:
    if state is None:
        return None
    if (
        _enum_value(getattr(state, "surface", None))
        != OfferPublicationSurface.TELEGRAM_CHANNEL.value
        or str(getattr(state, "publisher_bot_identity", "") or "").strip()
        != "primary"
    ):
        return _quarantined("offer_freshness_publisher_identity_invalid")

    raw_chat_id = getattr(state, "telegram_chat_id", None)
    if (
        raw_chat_id is not None
        and _strict_payload_int(raw_chat_id) != expected_channel_id
    ):
        return _quarantined("offer_freshness_canonical_channel_mismatch")
    raw_message_id = getattr(state, "telegram_message_id", None)
    if raw_message_id is not None and (
        isinstance(raw_message_id, bool)
        or not isinstance(raw_message_id, int)
        or raw_message_id <= 0
    ):
        return _quarantined("offer_freshness_canonical_message_invalid")
    raw_resource_id = getattr(state, "surface_resource_id", None)
    if raw_message_id is None and raw_resource_id not in {None, ""}:
        return _quarantined("offer_freshness_orphan_surface_resource")
    if (
        raw_message_id is not None
        and raw_resource_id not in {None, ""}
        and str(raw_resource_id) != str(raw_message_id)
    ):
        return _quarantined("offer_freshness_surface_resource_mismatch")
    return None


def _expected_payload(
    offer: Offer,
    *,
    action: TelegramDeliveryAction,
    expected_channel_id: int,
    message_id: int | None = None,
) -> dict[str, Any]:
    if action in OFFER_PUBLISH_ACTIONS:
        payload: dict[str, Any] = {
            "chat_id": expected_channel_id,
            "text": build_offer_channel_message(offer),
        }
        reply_markup = build_offer_channel_reply_markup(offer)
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return payload

    payload = {
        "chat_id": expected_channel_id,
        "message_id": message_id,
        "text": build_offer_channel_message(
            offer,
            history_tag=get_offer_channel_history_tag(offer),
        ),
        "reply_markup": (
            {"inline_keyboard": []}
            if action in OFFER_TERMINAL_EDIT_ACTIONS
            else build_offer_channel_reply_markup(offer)
        ),
    }
    return payload


def _authoritative_payload_decision(
    job: TelegramDeliveryJobRecord,
    offer: Offer,
    *,
    action: TelegramDeliveryAction,
    expected_channel_id: int,
) -> TelegramFreshnessDecision | None:
    payload = job.payload
    try:
        normalized_payload, payload_hash = canonical_telegram_delivery_payload(
            payload
        )
    except (TelegramDeliveryQueueValidationError, TypeError, ValueError):
        return _quarantined("offer_freshness_payload_not_strict_json")
    stored_hash = str(getattr(job, "payload_hash", "") or "").strip()
    if stored_hash != payload_hash:
        return _quarantined("offer_freshness_payload_hash_mismatch")

    message_id = (
        _strict_payload_int(payload.get("message_id"))
        if action not in OFFER_PUBLISH_ACTIONS
        else None
    )
    expected_payload = _expected_payload(
        offer,
        action=action,
        expected_channel_id=expected_channel_id,
        message_id=message_id,
    )
    if normalized_payload != expected_payload:
        return _quarantined("offer_freshness_payload_not_authoritative")
    return None


def _canonical_edit_identity_decision(
    job: TelegramDeliveryJobRecord,
    state: OfferPublicationState | None,
    *,
    action: TelegramDeliveryAction,
    offer_status: str,
    expected_channel_id: int,
) -> TelegramFreshnessDecision | None:
    shape_decision = _publication_identity_shape_decision(
        state,
        expected_channel_id=expected_channel_id,
    )
    if shape_decision is not None:
        return shape_decision
    if state is None or getattr(state, "telegram_message_id", None) is None:
        if offer_status in _TERMINAL_ACTION_BY_STATUS:
            return _decision(
                TelegramFreshnessOutcome.SENT_NOOP,
                reason="offer_freshness_terminal_never_published",
            )
        if action in OFFER_TERMINAL_EDIT_ACTIONS:
            return _decision(
                TelegramFreshnessOutcome.SUPERSEDED,
                reason="offer_freshness_terminal_action_no_longer_valid",
            )
        return _decision(
            TelegramFreshnessOutcome.WAIT_DEPENDENCY,
            reason="offer_freshness_publication_dependency_missing",
        )
    try:
        identity = canonical_telegram_publication_identity(state)
    except (CanonicalPublicationIdentityError, TypeError, ValueError):
        return _quarantined("offer_freshness_canonical_identity_invalid")
    payload_message_id = _strict_payload_int(job.payload.get("message_id"))
    if (
        identity.destination_chat_id != expected_channel_id
        or payload_message_id != identity.message_id
    ):
        return _quarantined("offer_freshness_canonical_identity_mismatch")
    if action in OFFER_TERMINAL_EDIT_ACTIONS:
        expected_action = _TERMINAL_ACTION_BY_STATUS.get(offer_status)
        if expected_action is None:
            return _decision(
                TelegramFreshnessOutcome.SUPERSEDED,
                reason="offer_freshness_terminal_action_no_longer_valid",
            )
        if expected_action != action:
            return _decision(
                TelegramFreshnessOutcome.RECLASSIFY,
                replacement_action=expected_action,
                reason="offer_freshness_terminal_action_changed",
            )
    elif offer_status in _TERMINAL_ACTION_BY_STATUS:
        return _terminal_reclassification(offer_status=offer_status)
    return None


async def validate_offer_telegram_delivery_freshness(
    db: AsyncSession,
    job: TelegramDeliveryJobRecord,
    now: datetime,
    *,
    expected_channel_id: int,
) -> TelegramFreshnessDecision:
    channel_id = _strict_nonzero_int(
        expected_channel_id,
        reason="telegram_offer_freshness_expected_channel_invalid",
    )
    try:
        action = TelegramDeliveryAction(_enum_value(job.action_kind))
    except ValueError:
        return _quarantined("offer_freshness_action_invalid")
    if action not in OFFER_FRESHNESS_ACTIONS:
        return _quarantined("offer_freshness_action_not_supported")
    route_decision = _validate_static_route(
        job,
        action=action,
        expected_channel_id=channel_id,
    )
    if route_decision is not None:
        return route_decision

    source_natural_id = str(job.source_natural_id or "").strip()
    if not source_natural_id:
        return _quarantined("offer_freshness_source_identity_missing")
    offer = await _load_offer(db, offer_public_id=source_natural_id)
    state = await _load_publication_state(
        db,
        offer_public_id=source_natural_id,
    )
    if offer is None:
        if state is not None:
            return _quarantined("offer_freshness_source_missing_with_publication")
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="offer_freshness_source_missing",
        )

    offer_status = _enum_value(getattr(offer, "status", None))
    if offer_status not in {
        status.value for status in OfferStatus
    }:
        return _quarantined("offer_freshness_offer_status_invalid")

    if action in OFFER_PUBLISH_ACTIONS:
        shape_decision = _publication_identity_shape_decision(
            state,
            expected_channel_id=channel_id,
        )
        if shape_decision is not None:
            return shape_decision
        if state is not None and getattr(state, "telegram_message_id", None):
            try:
                identity = canonical_telegram_publication_identity(state)
            except (CanonicalPublicationIdentityError, TypeError, ValueError):
                return _quarantined(
                    "offer_freshness_canonical_identity_invalid"
                )
            if identity.destination_chat_id != channel_id:
                return _quarantined(
                    "offer_freshness_canonical_identity_mismatch"
                )
            return _decision(
                TelegramFreshnessOutcome.SENT_NOOP,
                reason="offer_freshness_already_published",
            )
        if (
            _strict_payload_int(getattr(offer, "channel_message_id", None))
            is not None
        ):
            return _decision(
                TelegramFreshnessOutcome.WAIT_DEPENDENCY,
                reason="offer_freshness_legacy_identity_requires_reconciliation",
            )
        if offer_status != OfferStatus.ACTIVE.value:
            return _decision(
                TelegramFreshnessOutcome.SUPERSEDED,
                reason="offer_freshness_not_publishable",
            )
        deadline = getattr(job, "freshness_deadline_at", None)
        if isinstance(deadline, datetime) and _normalized_time(now) >= _normalized_time(
            deadline
        ):
            return _decision(
                TelegramFreshnessOutcome.SUPERSEDED,
                reason="offer_freshness_publish_deadline_passed",
            )
        version_decision = _source_version_decision(job, offer, action=action)
        if version_decision is not None:
            return version_decision
        payload_decision = _authoritative_payload_decision(
            job,
            offer,
            action=action,
            expected_channel_id=channel_id,
        )
        if payload_decision is not None:
            return payload_decision
        return _decision(
            TelegramFreshnessOutcome.SEND,
            reason="offer_freshness_current",
        )

    identity_decision = _canonical_edit_identity_decision(
        job,
        state,
        action=action,
        offer_status=offer_status,
        expected_channel_id=channel_id,
    )
    if identity_decision is not None:
        return identity_decision
    version_decision = _source_version_decision(job, offer, action=action)
    if version_decision is not None:
        return version_decision
    payload_decision = _authoritative_payload_decision(
        job,
        offer,
        action=action,
        expected_channel_id=channel_id,
    )
    if payload_decision is not None:
        return payload_decision
    return _decision(
        TelegramFreshnessOutcome.SEND,
        reason="offer_freshness_current",
    )


@dataclass(frozen=True, slots=True)
class OfferTelegramDeliveryFreshnessValidator:
    expected_channel_id: int

    def __post_init__(self) -> None:
        _strict_nonzero_int(
            self.expected_channel_id,
            reason="telegram_offer_freshness_expected_channel_invalid",
        )

    async def __call__(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        now: datetime,
    ) -> TelegramFreshnessDecision:
        return await validate_offer_telegram_delivery_freshness(
            db,
            job,
            now,
            expected_channel_id=self.expected_channel_id,
        )
