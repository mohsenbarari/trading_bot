"""Authoritative deadline freshness for direct callback-answer jobs."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from core.services.telegram_delivery_queue_service import (
    TelegramDeliveryQueueValidationError,
    canonical_telegram_delivery_payload,
)
from core.telegram_delivery_callback_contract import (
    CALLBACK_FRESHNESS_ACTIONS,
    TELEGRAM_CALLBACK_ANSWER_DEADLINE_SECONDS,
    build_telegram_callback_answer_payload,
    telegram_callback_destination_key,
    telegram_callback_feeder,
    telegram_callback_source_natural_id,
    telegram_callback_template_version,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDestinationClass,
    TelegramFreshnessDecision,
    TelegramFreshnessOutcome,
)
from models.telegram_delivery_job import TelegramDeliveryJobRecord


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _normalize_datetime(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _decision(
    outcome: TelegramFreshnessOutcome,
    *,
    reason: str,
) -> TelegramFreshnessDecision:
    return TelegramFreshnessDecision(outcome=outcome, reason=reason)


def _quarantined(reason: str) -> TelegramFreshnessDecision:
    return _decision(TelegramFreshnessOutcome.QUARANTINED, reason=reason)


def _callback_action(job: TelegramDeliveryJobRecord) -> TelegramDeliveryAction | None:
    try:
        action = TelegramDeliveryAction(_enum_value(job.action_kind))
    except ValueError:
        return None
    return action if action in CALLBACK_FRESHNESS_ACTIONS else None


def validate_telegram_callback_job_contract(
    job: TelegramDeliveryJobRecord,
) -> TelegramFreshnessDecision | None:
    action = _callback_action(job)
    if action is None:
        return _quarantined("telegram_callback_freshness_action_mismatch")
    if _enum_value(job.feeder_kind) != telegram_callback_feeder(action).value:
        return _quarantined("telegram_callback_freshness_feeder_mismatch")
    if _enum_value(job.destination_class) != TelegramDestinationClass.PRIVATE.value:
        return _quarantined(
            "telegram_callback_freshness_destination_class_mismatch"
        )
    if str(job.method or "") != "answerCallbackQuery":
        return _quarantined("telegram_callback_freshness_method_mismatch")
    if str(job.bot_identity or "") != "primary":
        return _quarantined("telegram_callback_freshness_bot_identity_mismatch")
    if str(job.template_version or "") != telegram_callback_template_version(action):
        return _quarantined("telegram_callback_freshness_template_mismatch")
    try:
        source_version = int(getattr(job, "source_version", -1))
    except (TypeError, ValueError, OverflowError):
        source_version = -1
    if source_version != 1:
        return _quarantined("telegram_callback_freshness_source_version_mismatch")
    if (
        job.eligible_at is not None
        or job.freshness_deadline_at is not None
        or job.campaign_id is not None
        or job.run_id is not None
    ):
        return _quarantined("telegram_callback_freshness_scope_forbidden")
    deadline = _normalize_datetime(job.delivery_deadline_at)
    if deadline is None:
        return _quarantined("telegram_callback_freshness_deadline_missing")
    created_at = _normalize_datetime(getattr(job, "created_at", None))
    if created_at is not None and deadline > created_at + timedelta(
        seconds=TELEGRAM_CALLBACK_ANSWER_DEADLINE_SECONDS
    ):
        return _quarantined("telegram_callback_freshness_deadline_too_long")

    payload = getattr(job, "payload", None)
    if not isinstance(payload, Mapping):
        return _quarantined("telegram_callback_freshness_payload_invalid")
    allowed_keys = {"callback_query_id", "show_alert", "text"}
    if not set(payload).issubset(allowed_keys):
        return _quarantined("telegram_callback_freshness_payload_invalid")
    try:
        expected_payload = build_telegram_callback_answer_payload(
            callback_query_id=payload.get("callback_query_id"),
            text=payload.get("text"),
            show_alert=payload.get("show_alert"),
        )
        normalized_payload, payload_hash = canonical_telegram_delivery_payload(
            payload
        )
    except (TelegramDeliveryQueueValidationError, TypeError, ValueError):
        return _quarantined("telegram_callback_freshness_payload_invalid")
    if normalized_payload != expected_payload:
        return _quarantined("telegram_callback_freshness_payload_shape_mismatch")
    callback_query_id = expected_payload["callback_query_id"]
    if str(job.source_natural_id or "") != telegram_callback_source_natural_id(
        callback_query_id
    ):
        return _quarantined("telegram_callback_freshness_source_identity_mismatch")
    if str(job.destination_key or "") != telegram_callback_destination_key(
        callback_query_id
    ):
        return _quarantined("telegram_callback_freshness_destination_mismatch")
    if str(job.payload_hash or "") != payload_hash:
        return _quarantined("telegram_callback_freshness_payload_hash_mismatch")
    return None


async def validate_telegram_callback_delivery_freshness(
    db: Any,
    job: TelegramDeliveryJobRecord,
    now: datetime,
) -> TelegramFreshnessDecision:
    del db
    contract_decision = validate_telegram_callback_job_contract(job)
    if contract_decision is not None:
        return contract_decision
    current_time = _normalize_datetime(now)
    deadline = _normalize_datetime(job.delivery_deadline_at)
    if current_time is None or deadline is None:
        return _quarantined("telegram_callback_freshness_clock_invalid")
    if current_time >= deadline:
        return _decision(
            TelegramFreshnessOutcome.EXPIRED_INTERACTION,
            reason="telegram_callback_freshness_deadline_passed",
        )
    return _decision(
        TelegramFreshnessOutcome.SEND,
        reason="telegram_callback_freshness_current",
    )


@dataclass(frozen=True, slots=True)
class TelegramCallbackDeliveryFreshnessValidator:
    async def __call__(
        self,
        db: Any,
        job: TelegramDeliveryJobRecord,
        now: datetime,
    ) -> TelegramFreshnessDecision:
        return await validate_telegram_callback_delivery_freshness(db, job, now)
