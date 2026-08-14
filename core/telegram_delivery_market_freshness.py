"""Authoritative pre-dispatch freshness checks for market channel notices."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.services.market_transition_service import (
    MARKET_CLOSED_CHANNEL_NOTICE,
    MARKET_NOTICE_STATUS_FAILED,
    MARKET_NOTICE_STATUS_PENDING,
    MARKET_NOTICE_STATUS_SENT,
    MARKET_NOTICE_STATUS_SKIPPED,
    MARKET_NOTICE_STATUS_SUPPRESSED_STALE,
    MARKET_NOTICE_TRANSITION_CLOSED,
    MARKET_NOTICE_TRANSITION_OPENED,
    MARKET_OPENED_CHANNEL_NOTICE,
    market_channel_notice_dedupe_key,
    market_channel_notice_freshness_deadline,
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
from models.market_channel_notice_receipt import MarketChannelNoticeReceipt
from models.market_runtime_state import MarketRuntimeState
from models.telegram_delivery_job import TelegramDeliveryJobRecord


MARKET_NOTICE_FRESHNESS_ACTIONS = frozenset(
    {
        TelegramDeliveryAction.MARKET_TRANSITION,
        TelegramDeliveryAction.MARKET_STATUS_CORRECTION,
    }
)
MARKET_NOTICE_DELIVERY_SOURCE_VERSION = 1
MARKET_NOTICE_TEMPLATE_VERSION = "market-channel-notice-v1"
MARKET_NOTICE_CAMPAIGN_ID = "market-status"
_MARKET_NOTICE_TEXT_BY_TRANSITION = {
    MARKET_NOTICE_TRANSITION_OPENED: MARKET_OPENED_CHANNEL_NOTICE,
    MARKET_NOTICE_TRANSITION_CLOSED: MARKET_CLOSED_CHANNEL_NOTICE,
}
_MARKET_NOTICE_ALLOWED_STATUSES = frozenset(
    {
        MARKET_NOTICE_STATUS_PENDING,
        MARKET_NOTICE_STATUS_FAILED,
        MARKET_NOTICE_STATUS_SENT,
        MARKET_NOTICE_STATUS_SKIPPED,
        MARKET_NOTICE_STATUS_SUPPRESSED_STALE,
    }
)
_MARKET_NOTICE_TERMINAL_SUPERSEDED_STATUSES = frozenset(
    {
        MARKET_NOTICE_STATUS_SKIPPED,
        MARKET_NOTICE_STATUS_SUPPRESSED_STALE,
    }
)


class TelegramMarketFreshnessConfigurationError(ValueError):
    """Raised when the validator lacks a safe channel contract."""


def _strict_nonzero_int(value: Any, *, reason: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value == 0:
        raise TelegramMarketFreshnessConfigurationError(reason)
    return value


def _strict_positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


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


def telegram_market_notice_destination_key(channel_id: int) -> str:
    normalized = _strict_nonzero_int(
        channel_id,
        reason="telegram_market_freshness_expected_channel_invalid",
    )
    return f"channel:{normalized}"


def build_market_notice_payload(
    receipt: MarketChannelNoticeReceipt | Any,
    *,
    channel_id: int,
) -> dict[str, Any]:
    normalized_channel = _strict_nonzero_int(
        channel_id,
        reason="telegram_market_freshness_expected_channel_invalid",
    )
    text = getattr(receipt, "notice_text", None)
    if not isinstance(text, str) or not text:
        raise ValueError("market_notice_text_invalid")
    return {"chat_id": normalized_channel, "text": text}


def market_notice_action_for_status(status: Any) -> TelegramDeliveryAction:
    normalized = str(status or "").strip().lower()
    if normalized == MARKET_NOTICE_STATUS_PENDING:
        return TelegramDeliveryAction.MARKET_TRANSITION
    if normalized == MARKET_NOTICE_STATUS_FAILED:
        return TelegramDeliveryAction.MARKET_STATUS_CORRECTION
    raise ValueError("market_notice_status_not_handoffable")


async def _load_notice_receipt(
    db: AsyncSession,
    *,
    dedupe_key: str,
) -> MarketChannelNoticeReceipt | None:
    result = await db.execute(
        select(MarketChannelNoticeReceipt)
        .where(MarketChannelNoticeReceipt.dedupe_key == dedupe_key)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def _load_market_runtime_state(
    db: AsyncSession,
) -> MarketRuntimeState | None:
    result = await db.execute(
        select(MarketRuntimeState)
        .order_by(MarketRuntimeState.id.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _validate_static_route(
    job: TelegramDeliveryJobRecord,
    *,
    expected_channel_id: int,
) -> TelegramFreshnessDecision | None:
    if _enum_value(job.feeder_kind) != TelegramFeederKind.MARKET_STATUS.value:
        return _quarantined("market_freshness_feeder_mismatch")
    if _enum_value(job.destination_class) != TelegramDestinationClass.CHANNEL.value:
        return _quarantined("market_freshness_destination_class_mismatch")
    if str(job.destination_key) != telegram_market_notice_destination_key(
        expected_channel_id
    ):
        return _quarantined("market_freshness_destination_key_mismatch")
    if str(job.method or "") != "sendMessage":
        return _quarantined("market_freshness_method_mismatch")
    if str(job.bot_identity or "") != "primary":
        return _quarantined("market_freshness_bot_identity_mismatch")
    if str(job.template_version or "") != MARKET_NOTICE_TEMPLATE_VERSION:
        return _quarantined("market_freshness_template_version_mismatch")
    if str(job.campaign_id or "") != MARKET_NOTICE_CAMPAIGN_ID:
        return _quarantined("market_freshness_campaign_mismatch")
    if job.delivery_deadline_at is not None or job.run_id is not None:
        return _quarantined("market_freshness_deadline_contract_invalid")
    return None


def _receipt_shape_decision(
    job: TelegramDeliveryJobRecord,
    receipt: MarketChannelNoticeReceipt,
    *,
    expected_channel_id: int,
) -> TelegramFreshnessDecision | None:
    raw_transition = getattr(receipt, "transition", None)
    transition = str(raw_transition or "").strip().lower()
    current_text = _MARKET_NOTICE_TEXT_BY_TRANSITION.get(transition)
    transition_at = getattr(receipt, "transition_at", None)
    receipt_text = getattr(receipt, "notice_text", None)
    if (
        not isinstance(raw_transition, str)
        or raw_transition != transition
        or current_text is None
        or not isinstance(transition_at, datetime)
        or not isinstance(receipt_text, str)
        or not receipt_text
    ):
        return _quarantined("market_freshness_receipt_transition_invalid")
    expected_dedupe = market_channel_notice_dedupe_key(
        transition=transition,
        transition_at=transition_at,
        notice_text=receipt_text,
    )
    if (
        str(getattr(receipt, "dedupe_key", "") or "") != expected_dedupe
        or str(job.source_natural_id or "") != expected_dedupe
    ):
        return _quarantined("market_freshness_receipt_identity_invalid")
    if (
        _strict_positive_int(getattr(job, "source_version", None))
        != MARKET_NOTICE_DELIVERY_SOURCE_VERSION
    ):
        return _quarantined("market_freshness_source_version_invalid")
    if (
        _strict_positive_int(getattr(job, "id", None)) is None
        or _strict_positive_int(getattr(receipt, "id", None)) is None
    ):
        return _quarantined("market_freshness_binding_shape_invalid")
    if _strict_positive_int(getattr(receipt, "queue_job_id", None)) != int(job.id):
        return _quarantined("market_freshness_queue_owner_mismatch")
    if not isinstance(getattr(receipt, "queue_handed_off_at", None), datetime):
        return _quarantined("market_freshness_queue_handoff_missing")
    if getattr(receipt, "queue_reconciliation_required_at", None) is not None:
        return _quarantined("market_freshness_reconciliation_owner_conflict")

    raw_status = getattr(receipt, "status", None)
    status = str(raw_status or "").strip().lower()
    if (
        not isinstance(raw_status, str)
        or raw_status != status
        or status not in _MARKET_NOTICE_ALLOWED_STATUSES
    ):
        return _quarantined("market_freshness_receipt_status_invalid")
    receipt_channel = getattr(receipt, "channel_id", None)
    if status in {
        MARKET_NOTICE_STATUS_PENDING,
        MARKET_NOTICE_STATUS_FAILED,
        MARKET_NOTICE_STATUS_SENT,
    } and str(receipt_channel or "") != str(expected_channel_id):
        return _quarantined("market_freshness_receipt_channel_mismatch")
    if (
        status in _MARKET_NOTICE_TERMINAL_SUPERSEDED_STATUSES
        and receipt_channel not in {None, ""}
        and str(receipt_channel) != str(expected_channel_id)
    ):
        return _quarantined("market_freshness_receipt_channel_mismatch")
    message_id = getattr(receipt, "telegram_message_id", None)
    if status == MARKET_NOTICE_STATUS_SENT:
        if _strict_positive_int(message_id) is None:
            return _quarantined("market_freshness_sent_evidence_missing")
    elif message_id is not None:
        return _quarantined("market_freshness_unsent_message_evidence_present")

    expected_deadline = market_channel_notice_freshness_deadline(transition_at)
    actual_deadline = getattr(job, "freshness_deadline_at", None)
    if actual_deadline is not None and not isinstance(actual_deadline, datetime):
        return _quarantined("market_freshness_deadline_mismatch")
    if expected_deadline is not None and actual_deadline is None:
        return _quarantined("market_freshness_deadline_mismatch")
    return None


def _authoritative_payload_decision(
    job: TelegramDeliveryJobRecord,
    receipt: MarketChannelNoticeReceipt,
    *,
    expected_channel_id: int,
) -> TelegramFreshnessDecision | None:
    try:
        normalized_payload, payload_hash = canonical_telegram_delivery_payload(
            job.payload
        )
    except (TelegramDeliveryQueueValidationError, TypeError, ValueError):
        return _quarantined("market_freshness_payload_not_strict_json")
    if str(getattr(job, "payload_hash", "") or "") != payload_hash:
        return _quarantined("market_freshness_payload_hash_mismatch")
    try:
        expected_payload = build_market_notice_payload(
            receipt,
            channel_id=expected_channel_id,
        )
    except (TypeError, ValueError):
        return _quarantined("market_freshness_payload_not_authoritative")
    if normalized_payload != expected_payload:
        return _quarantined("market_freshness_payload_not_authoritative")
    return None


def _runtime_state_decision(
    state: MarketRuntimeState,
    receipt: MarketChannelNoticeReceipt,
) -> TelegramFreshnessDecision | None:
    is_open = getattr(state, "is_open", None)
    last_transition_at = getattr(state, "last_transition_at", None)
    if not isinstance(is_open, bool) or not isinstance(last_transition_at, datetime):
        return _quarantined("market_freshness_runtime_state_invalid")
    current_transition = (
        MARKET_NOTICE_TRANSITION_OPENED
        if is_open
        else MARKET_NOTICE_TRANSITION_CLOSED
    )
    if (
        str(receipt.transition or "").strip().lower() != current_transition
        or _normalized_time(receipt.transition_at)
        != _normalized_time(last_transition_at)
    ):
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="market_freshness_transition_no_longer_current",
        )
    return None


async def validate_market_telegram_delivery_freshness(
    db: AsyncSession,
    job: TelegramDeliveryJobRecord,
    now: datetime,
    *,
    expected_channel_id: int,
) -> TelegramFreshnessDecision:
    channel_id = _strict_nonzero_int(
        expected_channel_id,
        reason="telegram_market_freshness_expected_channel_invalid",
    )
    try:
        action = TelegramDeliveryAction(_enum_value(job.action_kind))
    except ValueError:
        return _quarantined("market_freshness_action_invalid")
    if action not in MARKET_NOTICE_FRESHNESS_ACTIONS:
        return _quarantined("market_freshness_action_not_supported")
    route_decision = _validate_static_route(job, expected_channel_id=channel_id)
    if route_decision is not None:
        return route_decision

    source_identity = str(job.source_natural_id or "").strip()
    if not source_identity:
        return _quarantined("market_freshness_source_identity_missing")
    receipt = await _load_notice_receipt(db, dedupe_key=source_identity)
    if receipt is None:
        return _quarantined("market_freshness_receipt_missing")
    shape_decision = _receipt_shape_decision(
        job,
        receipt,
        expected_channel_id=channel_id,
    )
    if shape_decision is not None:
        return shape_decision
    payload_decision = _authoritative_payload_decision(
        job,
        receipt,
        expected_channel_id=channel_id,
    )
    if payload_decision is not None:
        return payload_decision

    status = str(receipt.status).strip().lower()
    if status == MARKET_NOTICE_STATUS_SENT:
        return _decision(
            TelegramFreshnessOutcome.SENT_NOOP,
            reason="market_freshness_already_sent",
        )
    if status in _MARKET_NOTICE_TERMINAL_SUPERSEDED_STATUSES:
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="market_freshness_receipt_terminal",
        )
    transition = str(receipt.transition or "").strip().lower()
    current_text = _MARKET_NOTICE_TEXT_BY_TRANSITION.get(transition)
    if current_text is None:
        return _quarantined("market_freshness_receipt_transition_invalid")
    if str(receipt.notice_text) != current_text:
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="market_freshness_notice_template_changed",
        )

    state = await _load_market_runtime_state(db)
    if state is None:
        return _decision(
            TelegramFreshnessOutcome.WAIT_DEPENDENCY,
            reason="market_freshness_runtime_state_missing",
        )
    state_decision = _runtime_state_decision(state, receipt)
    if state_decision is not None:
        return state_decision

    deadlines = [
        _normalized_time(value)
        for value in (
            market_channel_notice_freshness_deadline(receipt.transition_at),
            getattr(job, "freshness_deadline_at", None),
        )
        if isinstance(value, datetime)
    ]
    if deadlines and _normalized_time(now) > min(deadlines):
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            reason="market_freshness_deadline_passed",
        )

    try:
        expected_action = market_notice_action_for_status(status)
    except ValueError:
        return _quarantined("market_freshness_receipt_status_invalid")
    if action != expected_action:
        return _decision(
            TelegramFreshnessOutcome.RECLASSIFY,
            replacement_action=expected_action,
            reason="market_freshness_action_changed",
        )
    return _decision(
        TelegramFreshnessOutcome.SEND,
        reason="market_freshness_current",
    )


@dataclass(frozen=True, slots=True)
class MarketTelegramDeliveryFreshnessValidator:
    expected_channel_id: int

    def __post_init__(self) -> None:
        _strict_nonzero_int(
            self.expected_channel_id,
            reason="telegram_market_freshness_expected_channel_invalid",
        )

    async def __call__(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        now: datetime,
    ) -> TelegramFreshnessDecision:
        return await validate_market_telegram_delivery_freshness(
            db,
            job,
            now,
            expected_channel_id=self.expected_channel_id,
        )
