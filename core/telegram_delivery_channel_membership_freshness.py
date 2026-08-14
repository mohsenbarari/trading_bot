"""Authoritative freshness and dependency checks for membership removal sagas."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.services.telegram_delivery_queue_service import (
    canonical_telegram_delivery_payload,
)
from core.services.telegram_channel_membership_saga_service import (
    MEMBERSHIP_SAGA_TEMPLATE_VERSION,
)
from core.telegram_delivery_notification_action_freshness import (
    telegram_notification_action_channel_removal_kind,
    telegram_notification_action_deleted_route_is_reassigned,
    telegram_notification_action_outbox_matches_current_user,
)
from core.telegram_delivery_queue_contract import (
    FINAL_DELIVERY_STATES,
    TelegramDeliveryAction,
    TelegramDeliveryState,
    TelegramDestinationClass,
    TelegramFeederKind,
    TelegramFreshnessDecision,
    TelegramFreshnessOutcome,
)
from models.telegram_channel_membership_saga import TelegramChannelMembershipSaga
from models.telegram_delivery_job import TelegramDeliveryJobRecord
from models.telegram_notification_outbox import TelegramNotificationOutbox
from models.user import User


CHANNEL_MEMBERSHIP_FRESHNESS_ACTIONS = frozenset(
    {
        TelegramDeliveryAction.CHANNEL_MEMBER_BAN,
        TelegramDeliveryAction.CHANNEL_MEMBER_UNBAN,
    }
)


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _decision(
    outcome: TelegramFreshnessOutcome,
    reason: str,
) -> TelegramFreshnessDecision:
    return TelegramFreshnessDecision(outcome=outcome, reason=reason)


def _quarantined(reason: str) -> TelegramFreshnessDecision:
    return _decision(TelegramFreshnessOutcome.QUARANTINED, reason)


async def _load_saga(
    db: AsyncSession,
    job: TelegramDeliveryJobRecord,
) -> TelegramChannelMembershipSaga | None:
    return (
        await db.execute(
            select(TelegramChannelMembershipSaga)
            .where(
                or_(
                    TelegramChannelMembershipSaga.ban_job_id == job.id,
                    TelegramChannelMembershipSaga.unban_job_id == job.id,
                )
            )
            .limit(1)
        )
    ).scalar_one_or_none()


def _expected_route(
    saga: TelegramChannelMembershipSaga,
    action: TelegramDeliveryAction,
) -> tuple[str, dict[str, Any]]:
    if action == TelegramDeliveryAction.CHANNEL_MEMBER_BAN:
        return "banChatMember", {
            "chat_id": int(saga.channel_id),
            "user_id": int(saga.telegram_id),
            "revoke_messages": False,
        }
    return "unbanChatMember", {
        "chat_id": int(saga.channel_id),
        "user_id": int(saga.telegram_id),
        "only_if_banned": True,
    }


async def validate_channel_membership_telegram_delivery_freshness(
    db: AsyncSession,
    job: TelegramDeliveryJobRecord,
    now: datetime,
    *,
    expected_channel_id: int,
) -> TelegramFreshnessDecision:
    try:
        action = TelegramDeliveryAction(_enum_value(job.action_kind))
    except ValueError:
        return _quarantined("membership_freshness_action_invalid")
    if action not in CHANNEL_MEMBERSHIP_FRESHNESS_ACTIONS:
        return _quarantined("membership_freshness_action_invalid")
    saga = await _load_saga(db, job)
    if saga is None:
        return _quarantined("membership_freshness_saga_missing")
    expected_job_id = (
        saga.ban_job_id
        if action == TelegramDeliveryAction.CHANNEL_MEMBER_BAN
        else saga.unban_job_id
    )
    if int(expected_job_id or 0) != int(job.id):
        return _quarantined("membership_freshness_job_binding_mismatch")
    if int(saga.channel_id) != int(expected_channel_id):
        return _quarantined("membership_freshness_channel_mismatch")
    if _enum_value(job.feeder_kind) != TelegramFeederKind.ADMIN_SYSTEM.value:
        return _quarantined("membership_freshness_feeder_mismatch")
    if _enum_value(job.destination_class) != TelegramDestinationClass.CHANNEL.value:
        return _quarantined("membership_freshness_destination_class_mismatch")
    if str(job.bot_identity or "") != "primary":
        return _quarantined("membership_freshness_bot_mismatch")
    if str(job.destination_key or "") != f"channel:{int(saga.channel_id)}":
        return _quarantined("membership_freshness_destination_mismatch")
    if str(job.template_version or "") != MEMBERSHIP_SAGA_TEMPLATE_VERSION:
        return _quarantined("membership_freshness_template_mismatch")
    phase = "ban" if action == TelegramDeliveryAction.CHANNEL_MEMBER_BAN else "unban"
    if str(job.source_natural_id or "") != f"channel-membership:{int(saga.id)}:{phase}":
        return _quarantined("membership_freshness_source_identity_mismatch")
    if int(job.source_version or 0) != int(saga.source_version):
        return _quarantined("membership_freshness_source_version_mismatch")
    expected_method, expected_payload = _expected_route(saga, action)
    if str(job.method or "") != expected_method:
        return _quarantined("membership_freshness_method_mismatch")
    try:
        normalized_payload, payload_hash = canonical_telegram_delivery_payload(
            expected_payload
        )
    except (TypeError, ValueError):
        return _quarantined("membership_freshness_payload_invalid")
    if (
        dict(job.payload or {}) != normalized_payload
        or str(job.payload_hash or "") != payload_hash
    ):
        return _quarantined("membership_freshness_payload_mismatch")
    saga_state = str(saga.state or "")
    if saga_state == "complete":
        return _decision(
            TelegramFreshnessOutcome.SENT_NOOP,
            "membership_freshness_saga_complete",
        )

    # Once Telegram has durably accepted the ban, unban is a compensating
    # action rather than a stale business notification.  It must not be
    # cancelled by a later account-state or route change, otherwise a short
    # kick can turn into a permanent channel ban.
    if action == TelegramDeliveryAction.CHANNEL_MEMBER_UNBAN:
        ban_job = await db.get(
            TelegramDeliveryJobRecord,
            int(saga.ban_job_id or 0),
        )
        if ban_job is None:
            return _quarantined("membership_freshness_ban_job_missing")
        try:
            ban_state = TelegramDeliveryState(_enum_value(ban_job.state))
        except ValueError:
            return _quarantined("membership_freshness_ban_state_invalid")
        if ban_state in {
            TelegramDeliveryState.SENT,
            TelegramDeliveryState.SENT_NOOP,
        }:
            return _decision(
                TelegramFreshnessOutcome.SEND,
                "membership_freshness_unban_compensation_required",
            )
        if ban_state in FINAL_DELIVERY_STATES:
            return _decision(
                TelegramFreshnessOutcome.SUPERSEDED,
                "membership_freshness_ban_dependency_failed",
            )

    if saga_state in {"terminal_failed", "superseded"}:
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            "membership_freshness_saga_terminal",
        )

    outbox = (
        await db.execute(
            select(TelegramNotificationOutbox)
            .where(
                TelegramNotificationOutbox.dedupe_key
                == saga.source_dedupe_key
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    user = await db.get(User, int(saga.source_user_id or 0))
    if outbox is None or user is None:
        return _quarantined("membership_freshness_source_missing")
    try:
        source_kind = telegram_notification_action_channel_removal_kind(outbox)
        source_matches = telegram_notification_action_outbox_matches_current_user(
            outbox,
            user,
            now=now,
        )
        route_reassigned = (
            await telegram_notification_action_deleted_route_is_reassigned(
                db,
                outbox,
            )
        )
    except (TypeError, ValueError, OverflowError):
        return _quarantined("membership_freshness_source_contract_invalid")
    if (
        source_kind != str(saga.source_kind)
        or not source_matches
        or route_reassigned
    ):
        return _decision(
            TelegramFreshnessOutcome.SUPERSEDED,
            "membership_freshness_source_changed_or_relinked",
        )

    if action == TelegramDeliveryAction.CHANNEL_MEMBER_BAN:
        if str(saga.state or "") == "ban_succeeded":
            return _decision(
                TelegramFreshnessOutcome.SENT_NOOP,
                "membership_freshness_ban_already_succeeded",
            )
        return _decision(
            TelegramFreshnessOutcome.SEND,
            "membership_freshness_ban_current",
        )

    return _decision(
        TelegramFreshnessOutcome.WAIT_DEPENDENCY,
        "membership_freshness_waiting_for_ban",
    )


class ChannelMembershipTelegramDeliveryFreshnessValidator:
    def __init__(self, *, expected_channel_id: int) -> None:
        if isinstance(expected_channel_id, bool) or int(expected_channel_id) == 0:
            raise ValueError("membership_freshness_expected_channel_invalid")
        self.expected_channel_id = int(expected_channel_id)

    async def __call__(
        self,
        db: AsyncSession,
        job: TelegramDeliveryJobRecord,
        now: datetime,
    ) -> TelegramFreshnessDecision:
        return await validate_channel_membership_telegram_delivery_freshness(
            db,
            job,
            now,
            expected_channel_id=self.expected_channel_id,
        )
