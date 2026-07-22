"""Strict contract for deadline-bound Telegram callback answers."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramFeederKind,
)


TELEGRAM_CALLBACK_ANSWER_DEADLINE_SECONDS = 10.0
TELEGRAM_CALLBACK_QUERY_ID_MAX_LENGTH = 256
TELEGRAM_CALLBACK_ANSWER_TEXT_MAX_LENGTH = 200

CALLBACK_DEADLINE_TEMPLATE_VERSION = "callback-answer-v1"
OFFER_EXPIRY_CALLBACK_TEMPLATE_VERSION = "offer-expiry-callback-v1"

CALLBACK_FRESHNESS_ACTIONS = frozenset(
    {
        TelegramDeliveryAction.CALLBACK_DEADLINE,
        TelegramDeliveryAction.OFFER_EXPIRY_CALLBACK,
    }
)


def _normalize_action(value: Any) -> TelegramDeliveryAction:
    try:
        action = TelegramDeliveryAction(
            str(getattr(value, "value", value) or "").strip().lower()
        )
    except ValueError as exc:
        raise ValueError("telegram_callback_action_invalid") from exc
    if action not in CALLBACK_FRESHNESS_ACTIONS:
        raise ValueError("telegram_callback_action_invalid")
    return action


def validate_telegram_callback_query_id(value: Any) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > TELEGRAM_CALLBACK_QUERY_ID_MAX_LENGTH:
        raise ValueError("telegram_callback_query_id_invalid")
    return normalized


def validate_telegram_callback_answer_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value)
    if not normalized:
        return None
    if len(normalized) > TELEGRAM_CALLBACK_ANSWER_TEXT_MAX_LENGTH:
        raise ValueError("telegram_callback_answer_text_too_long")
    return normalized


def build_telegram_callback_answer_payload(
    *,
    callback_query_id: Any,
    text: Any = None,
    show_alert: bool = False,
) -> dict[str, Any]:
    if not isinstance(show_alert, bool):
        raise ValueError("telegram_callback_show_alert_invalid")
    payload: dict[str, Any] = {
        "callback_query_id": validate_telegram_callback_query_id(
            callback_query_id
        ),
        "show_alert": show_alert,
    }
    normalized_text = validate_telegram_callback_answer_text(text)
    if normalized_text is not None:
        payload["text"] = normalized_text
    return payload


def telegram_callback_query_fingerprint(callback_query_id: Any) -> str:
    normalized = validate_telegram_callback_query_id(callback_query_id)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def telegram_callback_source_natural_id(callback_query_id: Any) -> str:
    return f"telegram-callback:{telegram_callback_query_fingerprint(callback_query_id)}"


def telegram_callback_destination_key(callback_query_id: Any) -> str:
    return f"callback-query:{telegram_callback_query_fingerprint(callback_query_id)}"


def telegram_callback_feeder(action: Any) -> TelegramFeederKind:
    normalized = _normalize_action(action)
    if normalized == TelegramDeliveryAction.OFFER_EXPIRY_CALLBACK:
        return TelegramFeederKind.OFFER_CONTROL
    return TelegramFeederKind.DIRECT


def telegram_callback_template_version(action: Any) -> str:
    normalized = _normalize_action(action)
    if normalized == TelegramDeliveryAction.OFFER_EXPIRY_CALLBACK:
        return OFFER_EXPIRY_CALLBACK_TEMPLATE_VERSION
    return CALLBACK_DEADLINE_TEMPLATE_VERSION


def telegram_callback_delivery_deadline(received_at: datetime) -> datetime:
    if not isinstance(received_at, datetime):
        raise ValueError("telegram_callback_received_at_invalid")
    normalized = (
        received_at.replace(tzinfo=timezone.utc)
        if received_at.tzinfo is None
        else received_at.astimezone(timezone.utc)
    )
    return normalized + timedelta(
        seconds=TELEGRAM_CALLBACK_ANSWER_DEADLINE_SECONDS
    )
