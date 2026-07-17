"""Authoritative pre-dispatch freshness checks for trade-result messages."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.services.bot_access_policy import evaluate_bot_access
from core.services.telegram_delivery_queue_service import (
    TelegramDeliveryQueueValidationError,
    canonical_telegram_delivery_payload,
)
from core.services.trade_delivery_receipt_service import (
    TELEGRAM_DESTINATION_SERVER,
    TRADE_COMPLETED_EVENT_TYPE,
    classify_receipt_outage_for_delivery,
    trade_completed_receipt_dedupe_key,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDestinationClass,
    TelegramFeederKind,
    TelegramFreshnessDecision,
    TelegramFreshnessOutcome,
)
from core.telegram_delivery_trade_result_binding import (
    trade_result_receipt_is_bound_to_job,
)
from models.trade import Trade, TradeStatus
from models.trade_delivery_receipt import (
    TradeDeliveryChannel,
    TradeDeliveryReceipt,
    TradeDeliveryReceiptStatus,
)
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from models.user import User


TRADE_RESULT_FRESHNESS_ACTIONS = frozenset(
    {TelegramDeliveryAction.TRADE_RESULT}
)
TRADE_RESULT_DELIVERY_DEADLINE_SECONDS = 5
TRADE_RESULT_TEMPLATE_VERSION = "trade-receipt-snapshot-v1"
_TRADE_RESULT_SOURCE_SNAPSHOT_SEPARATOR = ":snapshot-v1:"

_ACTIVE_RECEIPT_STATUSES = frozenset(
    {
        TradeDeliveryReceiptStatus.PENDING.value,
        TradeDeliveryReceiptStatus.RETRY_PENDING.value,
    }
)
_TERMINAL_SUPERSEDED_RECEIPT_STATUSES = frozenset(
    {
        TradeDeliveryReceiptStatus.SKIPPED.value,
        TradeDeliveryReceiptStatus.NOT_REQUIRED.value,
        TradeDeliveryReceiptStatus.PERMANENT_FAILED.value,
    }
)
_ALL_RECEIPT_STATUSES = frozenset(item.value for item in TradeDeliveryReceiptStatus)


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _strict_positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _normalized_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


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


def telegram_private_user_destination_key(recipient_user_id: int) -> str:
    normalized = _strict_positive_int(recipient_user_id)
    if normalized is None:
        raise ValueError("telegram_trade_recipient_user_id_invalid")
    return f"private:user:{normalized}"


def trade_result_delivery_deadline(event_created_at: datetime) -> datetime:
    if not isinstance(event_created_at, datetime):
        raise ValueError("telegram_trade_event_created_at_invalid")
    return _normalized_time(event_created_at) + timedelta(
        seconds=TRADE_RESULT_DELIVERY_DEADLINE_SECONDS
    )


def trade_result_delivery_source_version(user: User | Any) -> int:
    version = _strict_positive_int(getattr(user, "sync_version", None))
    if version is None:
        raise ValueError("telegram_trade_recipient_source_version_invalid")
    return version


def trade_result_source_natural_id(
    receipt: TradeDeliveryReceipt | Any,
) -> str:
    dedupe_key = str(getattr(receipt, "dedupe_key", "") or "").strip()
    audit_payload = getattr(receipt, "audit_payload", None)
    message = (
        audit_payload.get("message")
        if isinstance(audit_payload, Mapping)
        else None
    )
    if not dedupe_key or not isinstance(message, str) or not message.strip():
        raise ValueError("telegram_trade_source_snapshot_invalid")
    return _trade_result_source_natural_id_from_message(
        dedupe_key=dedupe_key,
        message=message,
    )


def _trade_result_source_natural_id_from_message(
    *,
    dedupe_key: str,
    message: str,
) -> str:
    snapshot = json.dumps(
        {
            "dedupe_key": dedupe_key,
            "message": message.strip(),
            "template_version": TRADE_RESULT_TEMPLATE_VERSION,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    fingerprint = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()[:24]
    source_identity = (
        f"{dedupe_key}{_TRADE_RESULT_SOURCE_SNAPSHOT_SEPARATOR}{fingerprint}"
    )
    if len(source_identity) > 256:
        raise ValueError("telegram_trade_source_snapshot_identity_too_long")
    return source_identity


def trade_result_receipt_dedupe_from_source_natural_id(
    source_natural_id: Any,
) -> str | None:
    identity = str(source_natural_id or "").strip()
    if _TRADE_RESULT_SOURCE_SNAPSHOT_SEPARATOR not in identity:
        return None
    dedupe_key, fingerprint = identity.rsplit(
        _TRADE_RESULT_SOURCE_SNAPSHOT_SEPARATOR,
        1,
    )
    if not dedupe_key or len(fingerprint) != 24:
        return None
    if any(character not in "0123456789abcdef" for character in fingerprint):
        return None
    return dedupe_key


def build_trade_result_delivery_payload(
    receipt: TradeDeliveryReceipt | Any,
    user: User | Any,
) -> dict[str, Any]:
    audit_payload = getattr(receipt, "audit_payload", None)
    if not isinstance(audit_payload, Mapping):
        raise ValueError("telegram_trade_receipt_audit_payload_invalid")
    message = audit_payload.get("message")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("telegram_trade_receipt_message_invalid")
    telegram_id = _strict_positive_int(getattr(user, "telegram_id", None))
    if telegram_id is None:
        raise ValueError("telegram_trade_current_chat_id_invalid")
    return {
        "chat_id": telegram_id,
        "text": message.strip(),
        "parse_mode": "HTML",
    }


async def _load_receipt(
    db: AsyncSession,
    *,
    dedupe_key: str,
) -> TradeDeliveryReceipt | None:
    result = await db.execute(
        select(TradeDeliveryReceipt)
        .where(TradeDeliveryReceipt.dedupe_key == dedupe_key)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _load_trade(
    db: AsyncSession,
    *,
    trade_number: int,
) -> Trade | None:
    result = await db.execute(
        select(Trade).where(Trade.trade_number == trade_number).limit(1)
    )
    return result.scalar_one_or_none()


async def _load_user(
    db: AsyncSession,
    *,
    recipient_user_id: int,
) -> User | None:
    result = await db.execute(
        select(User).where(User.id == recipient_user_id).limit(1)
    )
    return result.scalar_one_or_none()


def _validate_static_route(
    job: TelegramDeliveryJobRecord,
) -> TelegramFreshnessDecision | None:
    if _enum_value(job.action_kind) != TelegramDeliveryAction.TRADE_RESULT.value:
        return _quarantined("trade_freshness_action_mismatch")
    if _enum_value(job.feeder_kind) != TelegramFeederKind.TRADE.value:
        return _quarantined("trade_freshness_feeder_mismatch")
    if _enum_value(job.destination_class) != TelegramDestinationClass.PRIVATE.value:
        return _quarantined("trade_freshness_destination_class_mismatch")
    if str(job.method or "") != "sendMessage":
        return _quarantined("trade_freshness_method_mismatch")
    if str(job.bot_identity or "") != "primary":
        return _quarantined("trade_freshness_bot_identity_mismatch")
    if str(job.template_version or "") != TRADE_RESULT_TEMPLATE_VERSION:
        return _quarantined("trade_freshness_template_version_mismatch")
    if getattr(job, "freshness_deadline_at", None) is not None:
        return _quarantined("trade_freshness_deadline_forbidden")
    return None


def _receipt_shape_decision(
    job: TelegramDeliveryJobRecord,
    receipt: TradeDeliveryReceipt | Any,
) -> TelegramFreshnessDecision | None:
    trade_number = _strict_positive_int(getattr(receipt, "trade_number", None))
    recipient_user_id = _strict_positive_int(
        getattr(receipt, "recipient_user_id", None)
    )
    event_created_at = getattr(receipt, "event_created_at", None)
    status = _enum_value(getattr(receipt, "status", None))
    recipient_role = str(getattr(receipt, "recipient_role", "") or "").strip()
    if (
        str(getattr(receipt, "event_type", "") or "")
        != TRADE_COMPLETED_EVENT_TYPE
        or _enum_value(getattr(receipt, "channel", None))
        != TradeDeliveryChannel.TELEGRAM.value
        or str(getattr(receipt, "destination_server", "") or "")
        != TELEGRAM_DESTINATION_SERVER
        or trade_number is None
        or recipient_user_id is None
        or not recipient_role
        or not isinstance(event_created_at, datetime)
        or status not in _ALL_RECEIPT_STATUSES
    ):
        return _quarantined("trade_freshness_receipt_shape_invalid")

    expected_dedupe = trade_completed_receipt_dedupe_key(
        channel=TradeDeliveryChannel.TELEGRAM,
        trade_number=trade_number,
        recipient_user_id=recipient_user_id,
    )
    if str(getattr(receipt, "dedupe_key", "") or "") != expected_dedupe:
        return _quarantined("trade_freshness_receipt_identity_invalid")
    job_payload = getattr(job, "payload", None)
    job_message = job_payload.get("text") if isinstance(job_payload, Mapping) else None
    if not isinstance(job_message, str) or not job_message.strip():
        return _quarantined("trade_freshness_payload_snapshot_invalid")
    try:
        job_bound_source_natural_id = _trade_result_source_natural_id_from_message(
            dedupe_key=expected_dedupe,
            message=job_message,
        )
    except ValueError:
        return _quarantined("trade_freshness_source_snapshot_invalid")
    if str(job.source_natural_id or "") != job_bound_source_natural_id:
        return _quarantined("trade_freshness_source_snapshot_fingerprint_invalid")
    try:
        expected_source_natural_id = trade_result_source_natural_id(receipt)
    except ValueError:
        return _quarantined("trade_freshness_source_snapshot_invalid")
    if _strict_positive_int(getattr(job, "source_version", None)) is None:
        return _quarantined("trade_freshness_source_version_invalid")
    if str(job.destination_key or "") != telegram_private_user_destination_key(
        recipient_user_id
    ):
        return _quarantined("trade_freshness_destination_key_mismatch")
    actual_deadline = getattr(job, "delivery_deadline_at", None)
    if not isinstance(actual_deadline, datetime):
        return _quarantined("trade_freshness_delivery_deadline_missing")
    if _normalized_time(actual_deadline) != trade_result_delivery_deadline(
        event_created_at
    ):
        return _quarantined("trade_freshness_delivery_deadline_mismatch")

    audit_payload = getattr(receipt, "audit_payload", None)
    if not isinstance(audit_payload, Mapping):
        return _quarantined("trade_freshness_receipt_payload_invalid")
    message = audit_payload.get("message")
    if not isinstance(message, str) or not message.strip():
        return _quarantined("trade_freshness_receipt_payload_invalid")
    extra_payload = audit_payload.get("extra_payload")
    if not isinstance(extra_payload, Mapping):
        return _quarantined("trade_freshness_receipt_extra_payload_invalid")
    if (
        _strict_positive_int(extra_payload.get("trade_number")) != trade_number
        or _strict_positive_int(extra_payload.get("recipient_user_id"))
        != recipient_user_id
        or str(extra_payload.get("recipient_role") or "").strip()
        != recipient_role
    ):
        return _quarantined("trade_freshness_receipt_extra_identity_invalid")
    if str(job.source_natural_id or "") != expected_source_natural_id:
        return _decision(
            TelegramFreshnessOutcome.RECLASSIFY,
            replacement_action=TelegramDeliveryAction.TRADE_RESULT,
            reason="trade_freshness_source_snapshot_changed",
        )
    return None


def _stored_payload_shape_decision(
    job: TelegramDeliveryJobRecord,
    receipt: TradeDeliveryReceipt | Any,
) -> TelegramFreshnessDecision | None:
    try:
        normalized_payload, payload_hash = canonical_telegram_delivery_payload(
            job.payload
        )
    except (TelegramDeliveryQueueValidationError, TypeError, ValueError):
        return _quarantined("trade_freshness_payload_not_strict_json")
    if str(getattr(job, "payload_hash", "") or "") != payload_hash:
        return _quarantined("trade_freshness_payload_hash_mismatch")
    audit_payload = getattr(receipt, "audit_payload", None)
    message = (
        audit_payload.get("message")
        if isinstance(audit_payload, Mapping)
        else None
    )
    if (
        set(normalized_payload) != {"chat_id", "text", "parse_mode"}
        or _strict_positive_int(normalized_payload.get("chat_id")) is None
        or normalized_payload.get("text") != str(message).strip()
        or normalized_payload.get("parse_mode") != "HTML"
    ):
        return _quarantined("trade_freshness_payload_snapshot_invalid")
    return None


def _trade_shape_decision(
    receipt: TradeDeliveryReceipt | Any,
    trade: Trade | Any,
) -> TelegramFreshnessDecision | None:
    receipt_trade_number = _strict_positive_int(
        getattr(receipt, "trade_number", None)
    )
    trade_number = _strict_positive_int(getattr(trade, "trade_number", None))
    if trade_number != receipt_trade_number:
        return _quarantined("trade_freshness_trade_identity_invalid")
    if _enum_value(getattr(trade, "status", None)) != TradeStatus.COMPLETED.value:
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="trade_freshness_trade_not_completed",
        )

    trade_created_at = getattr(trade, "created_at", None)
    receipt_created_at = getattr(receipt, "event_created_at", None)
    if (
        not isinstance(trade_created_at, datetime)
        or not isinstance(receipt_created_at, datetime)
        or _normalized_time(trade_created_at) != _normalized_time(receipt_created_at)
    ):
        return _quarantined("trade_freshness_commit_time_mismatch")

    receipt_trade_id = getattr(receipt, "trade_id", None)
    trade_id = _strict_positive_int(getattr(trade, "id", None))
    if (
        receipt_trade_id is not None
        and _strict_positive_int(receipt_trade_id) != trade_id
    ):
        return _quarantined("trade_freshness_local_trade_id_mismatch")
    receipt_offer_id = getattr(receipt, "offer_id", None)
    trade_offer_id = getattr(trade, "offer_id", None)
    if receipt_offer_id is not None and receipt_offer_id != trade_offer_id:
        return _quarantined("trade_freshness_local_offer_id_mismatch")
    return None


def _source_version_decision(
    job: TelegramDeliveryJobRecord,
    *,
    user: User | Any,
) -> TelegramFreshnessDecision | None:
    source_version = _strict_positive_int(getattr(job, "source_version", None))
    try:
        current_version = trade_result_delivery_source_version(user)
    except ValueError:
        return _quarantined("trade_freshness_recipient_source_version_invalid")
    if source_version is None:
        return _quarantined("trade_freshness_source_version_invalid")
    if source_version > current_version:
        return _decision(
            TelegramFreshnessOutcome.WAIT_DEPENDENCY,
            reason="trade_freshness_recipient_version_ahead",
        )
    if source_version < current_version:
        return _decision(
            TelegramFreshnessOutcome.RECLASSIFY,
            replacement_action=TelegramDeliveryAction.TRADE_RESULT,
            reason="trade_freshness_recipient_version_changed",
        )
    return None


def _authoritative_payload_decision(
    job: TelegramDeliveryJobRecord,
    receipt: TradeDeliveryReceipt | Any,
    user: User | Any,
) -> TelegramFreshnessDecision | None:
    recipient_user_id = _strict_positive_int(
        getattr(receipt, "recipient_user_id", None)
    )
    if recipient_user_id is None:
        return _quarantined("trade_freshness_recipient_identity_invalid")
    try:
        normalized_payload, payload_hash = canonical_telegram_delivery_payload(
            job.payload
        )
        expected_payload = build_trade_result_delivery_payload(receipt, user)
    except (TelegramDeliveryQueueValidationError, TypeError, ValueError):
        return _quarantined("trade_freshness_payload_not_strict_json")
    if str(getattr(job, "payload_hash", "") or "") != payload_hash:
        return _quarantined("trade_freshness_payload_hash_mismatch")
    if normalized_payload != expected_payload:
        return _quarantined("trade_freshness_payload_not_authoritative")
    return None


async def validate_trade_result_telegram_delivery_freshness(
    db: AsyncSession,
    job: TelegramDeliveryJobRecord,
    now: datetime,
) -> TelegramFreshnessDecision:
    """Revalidate one recipient's immutable trade-result receipt before send."""
    static_decision = _validate_static_route(job)
    if static_decision is not None:
        return static_decision

    source_natural_id = str(job.source_natural_id or "").strip()
    if not source_natural_id:
        return _quarantined("trade_freshness_source_identity_missing")
    receipt_dedupe = trade_result_receipt_dedupe_from_source_natural_id(
        source_natural_id
    )
    if receipt_dedupe is None:
        return _quarantined("trade_freshness_source_identity_invalid")
    receipt = await _load_receipt(db, dedupe_key=receipt_dedupe)
    if receipt is None:
        return _decision(
            TelegramFreshnessOutcome.WAIT_DEPENDENCY,
            reason="trade_freshness_receipt_missing",
        )
    receipt_decision = _receipt_shape_decision(job, receipt)
    if receipt_decision is not None:
        return receipt_decision
    payload_shape_decision = _stored_payload_shape_decision(job, receipt)
    if payload_shape_decision is not None:
        return payload_shape_decision

    status = _enum_value(getattr(receipt, "status", None))
    if status == TradeDeliveryReceiptStatus.SENT.value:
        if _strict_positive_int(getattr(receipt, "telegram_message_id", None)) is None:
            return _quarantined("trade_freshness_sent_evidence_missing")
        return _decision(
            TelegramFreshnessOutcome.SENT_NOOP,
            reason="trade_freshness_already_sent",
        )
    if getattr(receipt, "telegram_message_id", None) is not None:
        return _quarantined("trade_freshness_unsent_message_evidence_present")
    if status in _TERMINAL_SUPERSEDED_RECEIPT_STATUSES:
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="trade_freshness_receipt_terminal",
        )
    if status == TradeDeliveryReceiptStatus.PROCESSING.value:
        return _decision(
            TelegramFreshnessOutcome.WAIT_DEPENDENCY,
            reason="trade_freshness_legacy_receipt_processing",
        )
    if status not in _ACTIVE_RECEIPT_STATUSES:
        return _quarantined("trade_freshness_receipt_status_invalid")
    job_id = _strict_positive_int(getattr(job, "id", None))
    if job_id is None:
        return _quarantined("trade_freshness_job_identity_invalid")
    if not trade_result_receipt_is_bound_to_job(receipt, job_id):
        return _quarantined("trade_freshness_receipt_queue_owner_mismatch")
    if status == TradeDeliveryReceiptStatus.RETRY_PENDING.value:
        next_retry_at = getattr(receipt, "next_retry_at", None)
        if not isinstance(next_retry_at, datetime):
            return _quarantined("trade_freshness_receipt_retry_at_missing")
        if _normalized_time(now) < _normalized_time(next_retry_at):
            return _decision(
                TelegramFreshnessOutcome.WAIT_DEPENDENCY,
                reason="trade_freshness_receipt_retry_not_due",
            )

    outage = classify_receipt_outage_for_delivery(receipt, now=now)
    if outage.should_skip_remote_delivery:
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="trade_freshness_expired_after_outage",
        )

    trade_number = int(receipt.trade_number)
    trade = await _load_trade(db, trade_number=trade_number)
    if trade is None:
        return _decision(
            TelegramFreshnessOutcome.WAIT_DEPENDENCY,
            reason="trade_freshness_trade_missing",
        )
    trade_decision = _trade_shape_decision(receipt, trade)
    if trade_decision is not None:
        return trade_decision
    recipient_user_id = int(receipt.recipient_user_id)
    user = await _load_user(db, recipient_user_id=recipient_user_id)
    if user is None:
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="trade_freshness_recipient_missing",
        )
    version_decision = _source_version_decision(job, user=user)
    if version_decision is not None:
        return version_decision
    if _strict_positive_int(getattr(user, "telegram_id", None)) is None:
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="trade_freshness_recipient_unlinked",
        )
    access_decision = await evaluate_bot_access(db, user)
    if not access_decision.allowed:
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="trade_freshness_recipient_access_denied",
        )

    payload_decision = _authoritative_payload_decision(job, receipt, user)
    if payload_decision is not None:
        return payload_decision
    return _decision(
        TelegramFreshnessOutcome.SEND,
        reason="trade_freshness_current",
    )


@dataclass(frozen=True, slots=True)
class TradeResultTelegramDeliveryFreshnessValidator:
    async def __call__(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        now: datetime,
    ) -> TelegramFreshnessDecision:
        return await validate_trade_result_telegram_delivery_freshness(
            db,
            job,
            now,
        )
