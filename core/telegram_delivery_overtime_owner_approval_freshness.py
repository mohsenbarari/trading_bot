"""Fail-closed freshness for private overtime owner-approval delivery."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy.ext.asyncio import AsyncSession

from core.offer_lifecycle import (
    compute_lifecycle_deadlines,
    read_normal_lifetime_minutes,
    read_overtime_minutes_snapshot,
)
from core.services.bot_access_policy import evaluate_bot_access
from core.services.offer_overtime_request_service import (
    load_overtime_request_by_public_id,
)
from core.services.offer_request_ledger_service import (
    normalize_offer_request_status,
)
from core.server_routing import SERVER_FOREIGN, normalize_server
from core.services.telegram_delivery_queue_service import (
    TelegramDeliveryQueueValidationError,
    canonical_telegram_delivery_payload,
)
from core.telegram_delivery_overtime_owner_approval_contract import (
    OVERTIME_OWNER_APPROVAL_FRESHNESS_ACTIONS,
    OVERTIME_OWNER_APPROVAL_TEMPLATE_VERSION,
    overtime_owner_approval_destination_key,
    overtime_owner_approval_feeder,
    overtime_owner_approval_source_natural_id,
    payload_request_public_id,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDestinationClass,
    TelegramFreshnessDecision,
    TelegramFreshnessOutcome,
)
from core.trading_settings import get_trading_settings_async
from models.offer import Offer, OfferStatus
from models.offer_request import (
    OfferRequestStatus,
    OfferRequestWorkflow,
)
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from models.user import User


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


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed > 0 else None


def _decision(
    outcome: TelegramFreshnessOutcome,
    *,
    reason: str,
) -> TelegramFreshnessDecision:
    return TelegramFreshnessDecision(outcome=outcome, reason=reason)


def _quarantined(reason: str) -> TelegramFreshnessDecision:
    return _decision(TelegramFreshnessOutcome.QUARANTINED, reason=reason)


def _expired(reason: str) -> TelegramFreshnessDecision:
    return _decision(TelegramFreshnessOutcome.EXPIRED_INTERACTION, reason=reason)


def _action(job: TelegramDeliveryJobRecord) -> TelegramDeliveryAction | None:
    try:
        action = TelegramDeliveryAction(_enum_value(job.action_kind))
    except ValueError:
        return None
    return action if action in OVERTIME_OWNER_APPROVAL_FRESHNESS_ACTIONS else None


def validate_overtime_owner_approval_job_contract(
    job: TelegramDeliveryJobRecord,
) -> TelegramFreshnessDecision | None:
    action = _action(job)
    if action is None:
        return _quarantined("overtime_owner_approval_freshness_action_mismatch")
    if _enum_value(job.feeder_kind) != overtime_owner_approval_feeder().value:
        return _quarantined("overtime_owner_approval_freshness_feeder_mismatch")
    if _enum_value(job.destination_class) != TelegramDestinationClass.PRIVATE.value:
        return _quarantined(
            "overtime_owner_approval_freshness_destination_class_mismatch"
        )
    if str(job.method or "") != "sendMessage":
        return _quarantined("overtime_owner_approval_freshness_method_mismatch")
    if str(job.bot_identity or "") != "primary":
        return _quarantined("overtime_owner_approval_freshness_bot_identity_mismatch")
    if str(job.template_version or "") != OVERTIME_OWNER_APPROVAL_TEMPLATE_VERSION:
        return _quarantined("overtime_owner_approval_freshness_template_mismatch")
    if (
        job.eligible_at is not None
        or job.freshness_deadline_at is not None
        or job.campaign_id is not None
        or job.run_id is not None
    ):
        return _quarantined("overtime_owner_approval_freshness_scope_forbidden")
    if _normalize_datetime(job.delivery_deadline_at) is None:
        return _quarantined("overtime_owner_approval_freshness_deadline_missing")

    payload = getattr(job, "payload", None)
    if not isinstance(payload, Mapping):
        return _quarantined("overtime_owner_approval_freshness_payload_invalid")
    request_public_id = payload_request_public_id(payload)
    if request_public_id is None:
        return _quarantined("overtime_owner_approval_freshness_request_id_invalid")
    try:
        expected_source = overtime_owner_approval_source_natural_id(request_public_id)
    except ValueError:
        return _quarantined("overtime_owner_approval_freshness_request_id_invalid")
    if str(job.source_natural_id or "") != expected_source:
        return _quarantined("overtime_owner_approval_freshness_source_mismatch")

    chat_id = _positive_int(payload.get("chat_id"))
    text = payload.get("text")
    reply_markup = payload.get("reply_markup")
    if (
        chat_id is None
        or not isinstance(text, str)
        or not text.strip()
        or not isinstance(reply_markup, Mapping)
    ):
        return _quarantined("overtime_owner_approval_freshness_payload_invalid")
    try:
        canonical_telegram_delivery_payload(payload)
    except (TelegramDeliveryQueueValidationError, TypeError, ValueError):
        return _quarantined("overtime_owner_approval_freshness_payload_invalid")
    return None


async def validate_overtime_owner_approval_delivery_freshness(
    db: AsyncSession,
    job: TelegramDeliveryJobRecord,
    now: datetime,
) -> TelegramFreshnessDecision:
    contract = validate_overtime_owner_approval_job_contract(job)
    if contract is not None:
        return contract

    current = _normalize_datetime(now)
    deadline = _normalize_datetime(job.delivery_deadline_at)
    if current is None or deadline is None:
        return _quarantined("overtime_owner_approval_freshness_clock_invalid")
    if current >= deadline:
        return _expired("overtime_owner_approval_freshness_delivery_deadline_passed")

    payload = job.payload
    assert isinstance(payload, Mapping)
    request_public_id = payload_request_public_id(payload)
    assert request_public_id is not None

    ledger = await load_overtime_request_by_public_id(
        db,
        request_public_id,
        for_update=False,
    )
    if ledger is None:
        return _expired("overtime_owner_approval_freshness_request_missing")
    if _enum_value(ledger.workflow_kind) != OfferRequestWorkflow.OVERTIME.value:
        return _quarantined("overtime_owner_approval_freshness_workflow_mismatch")
    status = normalize_offer_request_status(ledger.result_status)
    if status != OfferRequestStatus.OVERTIME_DELIVERING:
        # Already presented or terminal — do not send a second approval prompt.
        return _expired("overtime_owner_approval_freshness_request_not_delivering")
    owner_user_id = _positive_int(ledger.offer_owner_user_id)
    if owner_user_id is None:
        return _quarantined("overtime_owner_approval_freshness_owner_missing")
    try:
        expected_destination = overtime_owner_approval_destination_key(owner_user_id)
    except ValueError:
        return _quarantined("overtime_owner_approval_freshness_owner_missing")
    if str(job.destination_key or "") != expected_destination:
        return _quarantined("overtime_owner_approval_freshness_destination_mismatch")

    owner = await db.get(User, owner_user_id)
    if owner is None:
        return _expired("overtime_owner_approval_freshness_owner_user_missing")
    if _positive_int(getattr(owner, "telegram_id", None)) is None:
        return _expired("overtime_owner_approval_freshness_owner_unlinked")
    if _positive_int(payload.get("chat_id")) != _positive_int(owner.telegram_id):
        return _expired("overtime_owner_approval_freshness_owner_relinked")
    access = await evaluate_bot_access(db, owner)
    if not access.allowed:
        return _expired("overtime_owner_approval_freshness_owner_access_denied")

    local_offer_id = _positive_int(ledger.local_offer_id)
    offer = await db.get(Offer, local_offer_id) if local_offer_id is not None else None
    if offer is None:
        return _expired("overtime_owner_approval_freshness_offer_missing")
    request_home = normalize_server(
        getattr(ledger, "request_home_server", None),
        default="",
    )
    offer_home = normalize_server(getattr(offer, "home_server", None), default="")
    if request_home != SERVER_FOREIGN or offer_home != SERVER_FOREIGN:
        return _quarantined("overtime_owner_approval_freshness_surface_mismatch")
    if _enum_value(offer.status) != OfferStatus.ACTIVE.value:
        return _expired("overtime_owner_approval_freshness_offer_inactive")

    settings = await get_trading_settings_async()
    normal_minutes = read_normal_lifetime_minutes(settings)
    _normal, final = compute_lifecycle_deadlines(
        getattr(offer, "created_at", None),
        normal_lifetime_minutes=normal_minutes,
        overtime_minutes_snapshot=read_overtime_minutes_snapshot(offer),
    )
    final_aware = _normalize_datetime(final)
    if final_aware is None or current >= final_aware:
        return _expired("overtime_owner_approval_freshness_offer_final_deadline_passed")
    # The job deadline is the enqueue-time final end; never deliver past either
    # that stamp or the current authoritative final end.
    effective_deadline = min(deadline, final_aware)
    if current >= effective_deadline:
        return _expired("overtime_owner_approval_freshness_delivery_deadline_passed")

    return _decision(
        TelegramFreshnessOutcome.SEND,
        reason="overtime_owner_approval_freshness_current",
    )


@dataclass(frozen=True, slots=True)
class OvertimeOwnerApprovalTelegramDeliveryFreshnessValidator:
    async def __call__(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        now: datetime,
    ) -> TelegramFreshnessDecision:
        return await validate_overtime_owner_approval_delivery_freshness(db, job, now)


def overtime_owner_approval_freshness_routes(
    validator: OvertimeOwnerApprovalTelegramDeliveryFreshnessValidator | Any,
) -> dict[TelegramDeliveryAction, Any]:
    if not callable(validator):
        raise ValueError("overtime_owner_approval_freshness_validator_invalid")
    return {action: validator for action in OVERTIME_OWNER_APPROVAL_FRESHNESS_ACTIONS}
