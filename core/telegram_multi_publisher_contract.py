"""Pure, fail-closed contract for staged Telegram multi-publisher delivery.

This module deliberately has no database, Telegram, Redis, credential, or
configuration side effects.  It fixes the narrow B2B envelope and lifecycle
vocabulary before the runtime starts dispatching commands in later stages.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any
from uuid import UUID

from core.telegram_delivery_queue_contract import TelegramDeliveryAction


TELEGRAM_B2B_PROTOCOL_VERSION = "tbq1"
TELEGRAM_B2B_ENVELOPE_SEPARATOR = "|"
TELEGRAM_PUBLISHER_IDENTITIES = tuple(
    f"publisher_{index}" for index in range(1, 6)
)
TELEGRAM_PUBLISHER_PRE_FLIGHT_CAPABILITIES = frozenset(
    {
        "b2b_enabled",
        "channel_post",
        "channel_edit_own_post",
        "channel_delete_own_post",
        "callback_receive",
    }
)
TELEGRAM_PUBLISHER_OWNER_REQUIRED_METHODS = frozenset(
    {
        "answerCallbackQuery",
        "deleteMessage",
        "editMessageReplyMarkup",
        "editMessageText",
        "sendMessage",
    }
)
TELEGRAM_MULTI_PUBLISHER_METRIC_FIELDS = frozenset(
    {
        "command_id",
        "command_lag_ms",
        "destination_key",
        "event",
        "health_state",
        "http_status",
        "job_id",
        "lane",
        "method",
        "occurred_at",
        "queue_depth",
        "reason_code",
        "receipt_lag_ms",
        "retry_after_seconds",
    }
)

_ULID_PATTERN = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


class TelegramMultiPublisherContractError(ValueError):
    """Raised when a B2B envelope or lifecycle contract is invalid."""


class TelegramPublisherB2BMessageType(str, Enum):
    DISPATCH = "dispatch"
    ACK = "ack"


class TelegramPublisherDispatchState(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    ACKNOWLEDGED = "acknowledged"
    RETRY_DUE = "retry_due"
    FAILED = "failed"
    SUPERSEDED = "superseded"


class TelegramPublisherLifecycleOperation(str, Enum):
    PUBLISH = "publish"
    ACTIVE_EDIT = "active_edit"
    TERMINAL_EDIT = "terminal_edit"
    RECONCILIATION_EDIT = "reconciliation_edit"


class TelegramPublisherReasonCode(str, Enum):
    B2B_DISABLED = "b2b_disabled"
    SENDER_NOT_ALLOWLISTED = "sender_not_allowlisted"
    MALFORMED_ENVELOPE = "malformed_envelope"
    UNSUPPORTED_PROTOCOL = "unsupported_protocol"
    COMMAND_NOT_FOUND = "command_not_found"
    COMMAND_NOT_ASSIGNED = "command_not_assigned"
    COMMAND_STALE = "command_stale"
    COMMAND_DUPLICATE = "command_duplicate"
    ACK_SENDER_MISMATCH = "ack_sender_mismatch"
    LANE_UNHEALTHY = "lane_unhealthy"
    CROSS_OWNER_LIFECYCLE = "cross_owner_lifecycle"


_OFFER_ACTION_OPERATIONS = {
    TelegramDeliveryAction.OFFER_PUBLISH: TelegramPublisherLifecycleOperation.PUBLISH,
    TelegramDeliveryAction.PARTIAL_OFFER_EDIT: TelegramPublisherLifecycleOperation.ACTIVE_EDIT,
    TelegramDeliveryAction.OTHER_ACTIVE_OFFER_EDIT: TelegramPublisherLifecycleOperation.ACTIVE_EDIT,
    TelegramDeliveryAction.OVERTIME_CHANNEL_EDIT: TelegramPublisherLifecycleOperation.ACTIVE_EDIT,
    TelegramDeliveryAction.FINAL_TAIL_CHANNEL_EDIT: TelegramPublisherLifecycleOperation.ACTIVE_EDIT,
    TelegramDeliveryAction.INVALID_ACTION_BUTTON_EDIT: TelegramPublisherLifecycleOperation.ACTIVE_EDIT,
    TelegramDeliveryAction.TRADED_OFFER_EDIT: TelegramPublisherLifecycleOperation.TERMINAL_EDIT,
    TelegramDeliveryAction.EXPIRED_OFFER_EDIT: TelegramPublisherLifecycleOperation.TERMINAL_EDIT,
    TelegramDeliveryAction.CANCELLED_OFFER_EDIT: TelegramPublisherLifecycleOperation.TERMINAL_EDIT,
    TelegramDeliveryAction.RECONCILIATION_EDIT: TelegramPublisherLifecycleOperation.RECONCILIATION_EDIT,
}
TELEGRAM_PUBLISHER_OWNED_OFFER_ACTIONS = frozenset(_OFFER_ACTION_OPERATIONS)

_ALLOWED_DISPATCH_TRANSITIONS = {
    TelegramPublisherDispatchState.PENDING: frozenset(
        {
            TelegramPublisherDispatchState.SENT,
            TelegramPublisherDispatchState.ACKNOWLEDGED,
            TelegramPublisherDispatchState.RETRY_DUE,
            TelegramPublisherDispatchState.FAILED,
            TelegramPublisherDispatchState.SUPERSEDED,
        }
    ),
    TelegramPublisherDispatchState.SENT: frozenset(
        {
            TelegramPublisherDispatchState.ACKNOWLEDGED,
            TelegramPublisherDispatchState.RETRY_DUE,
            TelegramPublisherDispatchState.FAILED,
            TelegramPublisherDispatchState.SUPERSEDED,
        }
    ),
    TelegramPublisherDispatchState.RETRY_DUE: frozenset(
        {
            TelegramPublisherDispatchState.SENT,
            TelegramPublisherDispatchState.ACKNOWLEDGED,
            TelegramPublisherDispatchState.FAILED,
            TelegramPublisherDispatchState.SUPERSEDED,
        }
    ),
    TelegramPublisherDispatchState.ACKNOWLEDGED: frozenset(),
    TelegramPublisherDispatchState.FAILED: frozenset(),
    TelegramPublisherDispatchState.SUPERSEDED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class TelegramPublisherB2BEnvelope:
    message_type: TelegramPublisherB2BMessageType
    command_id: str
    sequence: int
    enqueued_at: datetime
    ack_sent_at: datetime | None = None


def _normalized_command_id(value: Any) -> str:
    command_id = str(value or "").strip()
    if not command_id:
        raise TelegramMultiPublisherContractError("telegram_b2b_command_id_invalid")
    try:
        UUID(command_id)
    except (TypeError, ValueError, AttributeError):
        if not _ULID_PATTERN.fullmatch(command_id):
            raise TelegramMultiPublisherContractError(
                "telegram_b2b_command_id_invalid"
            ) from None
    return command_id


def _positive_sequence(value: Any) -> int:
    if isinstance(value, bool):
        raise TelegramMultiPublisherContractError("telegram_b2b_sequence_invalid")
    try:
        sequence = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TelegramMultiPublisherContractError(
            "telegram_b2b_sequence_invalid"
        ) from exc
    if sequence <= 0 or str(sequence) != str(value).strip():
        raise TelegramMultiPublisherContractError("telegram_b2b_sequence_invalid")
    return sequence


def _normalized_utc_timestamp(value: Any) -> datetime:
    if not isinstance(value, datetime):
        raise TelegramMultiPublisherContractError("telegram_b2b_timestamp_invalid")
    if value.tzinfo is None or value.utcoffset() is None:
        raise TelegramMultiPublisherContractError("telegram_b2b_timestamp_invalid")
    return value.astimezone(timezone.utc)


def _parse_utc_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise TelegramMultiPublisherContractError(
            "telegram_b2b_timestamp_invalid"
        ) from exc
    return _normalized_utc_timestamp(parsed)


def _render_utc_timestamp(value: datetime) -> str:
    return _normalized_utc_timestamp(value).isoformat().replace("+00:00", "Z")


def classify_telegram_publisher_offer_action(
    action: TelegramDeliveryAction | str,
) -> TelegramPublisherLifecycleOperation:
    try:
        normalized = TelegramDeliveryAction(str(getattr(action, "value", action)))
    except ValueError as exc:
        raise TelegramMultiPublisherContractError(
            "telegram_publisher_offer_action_unsupported"
        ) from exc
    operation = _OFFER_ACTION_OPERATIONS.get(normalized)
    if operation is None:
        raise TelegramMultiPublisherContractError(
            "telegram_publisher_offer_action_unsupported"
        )
    return operation


def is_allowed_telegram_publisher_dispatch_transition(
    current: TelegramPublisherDispatchState | str,
    target: TelegramPublisherDispatchState | str,
) -> bool:
    try:
        current_state = TelegramPublisherDispatchState(current)
        target_state = TelegramPublisherDispatchState(target)
    except ValueError as exc:
        raise TelegramMultiPublisherContractError(
            "telegram_publisher_dispatch_state_invalid"
        ) from exc
    return target_state == current_state or target_state in _ALLOWED_DISPATCH_TRANSITIONS[
        current_state
    ]


def render_telegram_publisher_b2b_envelope(
    envelope: TelegramPublisherB2BEnvelope,
) -> str:
    command_id = _normalized_command_id(envelope.command_id)
    sequence = _positive_sequence(envelope.sequence)
    enqueued_at = _render_utc_timestamp(envelope.enqueued_at)
    fields = (
        TELEGRAM_B2B_PROTOCOL_VERSION,
        envelope.message_type.value,
        command_id,
        str(sequence),
        enqueued_at,
    )
    if envelope.message_type == TelegramPublisherB2BMessageType.DISPATCH:
        if envelope.ack_sent_at is not None:
            raise TelegramMultiPublisherContractError(
                "telegram_b2b_dispatch_ack_time_forbidden"
            )
        return TELEGRAM_B2B_ENVELOPE_SEPARATOR.join(fields)
    if envelope.message_type == TelegramPublisherB2BMessageType.ACK:
        if envelope.ack_sent_at is None:
            raise TelegramMultiPublisherContractError("telegram_b2b_ack_time_missing")
        return TELEGRAM_B2B_ENVELOPE_SEPARATOR.join(
            (*fields, _render_utc_timestamp(envelope.ack_sent_at))
        )
    raise TelegramMultiPublisherContractError("telegram_b2b_message_type_invalid")


def parse_telegram_publisher_b2b_envelope(value: Any) -> TelegramPublisherB2BEnvelope:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise TelegramMultiPublisherContractError("telegram_b2b_envelope_invalid")
    fields = value.split(TELEGRAM_B2B_ENVELOPE_SEPARATOR)
    if not fields or fields[0] != TELEGRAM_B2B_PROTOCOL_VERSION:
        raise TelegramMultiPublisherContractError("telegram_b2b_protocol_unsupported")
    try:
        message_type = TelegramPublisherB2BMessageType(fields[1])
    except (IndexError, ValueError) as exc:
        raise TelegramMultiPublisherContractError(
            "telegram_b2b_message_type_invalid"
        ) from exc
    expected_length = 5 if message_type == TelegramPublisherB2BMessageType.DISPATCH else 6
    if len(fields) != expected_length:
        raise TelegramMultiPublisherContractError("telegram_b2b_envelope_shape_invalid")
    return TelegramPublisherB2BEnvelope(
        message_type=message_type,
        command_id=_normalized_command_id(fields[2]),
        sequence=_positive_sequence(fields[3]),
        enqueued_at=_parse_utc_timestamp(fields[4]),
        ack_sent_at=(
            _parse_utc_timestamp(fields[5])
            if message_type == TelegramPublisherB2BMessageType.ACK
            else None
        ),
    )
