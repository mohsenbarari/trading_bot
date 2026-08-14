"""Materialize ordered channel-removal work from an account-status outbox."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.server_routing import SERVER_FOREIGN
from core.services.telegram_delivery_queue_service import (
    enqueue_telegram_delivery_job,
)
from core.telegram_delivery_notification_action_freshness import (
    telegram_notification_action_channel_removal_kind,
    telegram_notification_action_deleted_route_is_reassigned,
    telegram_notification_action_outbox_matches_current_user,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDestinationClass,
    TelegramFeederKind,
)
from models.telegram_channel_membership_saga import (
    TelegramChannelMembershipSaga,
)
from models.telegram_notification_outbox import TelegramNotificationOutbox
from models.user import User


MEMBERSHIP_SAGA_TEMPLATE_VERSION = "channel-membership-removal-v1"


class TelegramChannelMembershipSagaError(RuntimeError):
    """Reject an unsafe or contradictory membership execution intent."""


@dataclass(frozen=True, slots=True)
class TelegramChannelMembershipSagaResult:
    saga: TelegramChannelMembershipSaga
    created: bool


def _positive_int(value: Any, *, reason: str) -> int:
    if isinstance(value, bool):
        raise TelegramChannelMembershipSagaError(reason)
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TelegramChannelMembershipSagaError(reason) from exc
    if parsed <= 0:
        raise TelegramChannelMembershipSagaError(reason)
    return parsed


def _channel_id(value: Any) -> int:
    if isinstance(value, bool):
        raise TelegramChannelMembershipSagaError(
            "telegram_membership_saga_channel_invalid"
        )
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TelegramChannelMembershipSagaError(
            "telegram_membership_saga_channel_invalid"
        ) from exc
    if parsed == 0:
        raise TelegramChannelMembershipSagaError(
            "telegram_membership_saga_channel_invalid"
        )
    return parsed


def _same_source(
    saga: TelegramChannelMembershipSaga,
    *,
    outbox: TelegramNotificationOutbox,
    source_kind: str,
    source_version: int,
    telegram_id: int,
    channel_id: int,
) -> bool:
    return (
        str(saga.source_dedupe_key) == str(outbox.dedupe_key)
        and saga.source_outbox_id in {None, int(outbox.id)}
        and saga.source_user_id in {None, int(outbox.recipient_user_id)}
        and str(saga.source_kind) == source_kind
        and int(saga.source_version) == source_version
        and int(saga.telegram_id) == telegram_id
        and int(saga.channel_id) == channel_id
    )


async def ensure_telegram_channel_membership_removal_saga(
    db: AsyncSession,
    *,
    current_server: str,
    outbox: TelegramNotificationOutbox,
    user: User,
    expected_channel_id: int,
    now: datetime,
) -> TelegramChannelMembershipSagaResult | None:
    """Create both idempotent steps in the outbox handoff transaction."""
    if str(current_server or "").strip().lower() != SERVER_FOREIGN:
        raise TelegramChannelMembershipSagaError(
            "telegram_membership_saga_is_foreign_only"
        )
    source_kind = telegram_notification_action_channel_removal_kind(outbox)
    if source_kind is None:
        return None
    if not telegram_notification_action_outbox_matches_current_user(
        outbox,
        user,
        now=now,
    ):
        raise TelegramChannelMembershipSagaError(
            "telegram_membership_saga_source_state_changed"
        )
    if await telegram_notification_action_deleted_route_is_reassigned(db, outbox):
        raise TelegramChannelMembershipSagaError(
            "telegram_membership_saga_route_reassigned"
        )

    outbox_id = _positive_int(
        getattr(outbox, "id", None),
        reason="telegram_membership_saga_outbox_invalid",
    )
    user_id = _positive_int(
        getattr(outbox, "recipient_user_id", None),
        reason="telegram_membership_saga_user_invalid",
    )
    telegram_id = _positive_int(
        getattr(outbox, "telegram_id_at_enqueue", None),
        reason="telegram_membership_saga_telegram_id_invalid",
    )
    channel_id = _channel_id(expected_channel_id)
    extra_payload = getattr(outbox, "extra_payload", None)
    if not isinstance(extra_payload, dict):
        raise TelegramChannelMembershipSagaError(
            "telegram_membership_saga_source_payload_invalid"
        )
    source_version = _positive_int(
        extra_payload.get("user_sync_version"),
        reason="telegram_membership_saga_source_version_invalid",
    )
    source_dedupe_key = str(getattr(outbox, "dedupe_key", "") or "").strip()
    if not source_dedupe_key or len(source_dedupe_key) > 192:
        raise TelegramChannelMembershipSagaError(
            "telegram_membership_saga_source_dedupe_invalid"
        )

    inserted_id = (
        await db.execute(
            pg_insert(TelegramChannelMembershipSaga)
            .values(
                source_dedupe_key=source_dedupe_key,
                source_outbox_id=outbox_id,
                source_user_id=user_id,
                source_kind=source_kind,
                source_version=source_version,
                telegram_id=telegram_id,
                channel_id=channel_id,
                state="ban_pending",
                reason="membership_saga_materializing",
                created_at=now,
                updated_at=now,
            )
            .on_conflict_do_nothing(index_elements=["source_dedupe_key"])
            .returning(TelegramChannelMembershipSaga.id)
        )
    ).scalar_one_or_none()
    created = inserted_id is not None
    saga = (
        await db.execute(
            select(TelegramChannelMembershipSaga)
            .where(
                TelegramChannelMembershipSaga.id == inserted_id
                if created
                else TelegramChannelMembershipSaga.source_dedupe_key
                == source_dedupe_key
            )
            .with_for_update()
        )
    ).scalar_one()
    if not _same_source(
        saga,
        outbox=outbox,
        source_kind=source_kind,
        source_version=source_version,
        telegram_id=telegram_id,
        channel_id=channel_id,
    ):
        raise TelegramChannelMembershipSagaError(
            "telegram_membership_saga_dedupe_conflict"
        )
    if saga.ban_job_id is not None or saga.unban_job_id is not None:
        if saga.ban_job_id is None or saga.unban_job_id is None:
            raise TelegramChannelMembershipSagaError(
                "telegram_membership_saga_partial_job_binding"
            )
        return TelegramChannelMembershipSagaResult(saga=saga, created=False)

    destination_key = f"channel:{channel_id}"
    source_prefix = f"channel-membership:{int(saga.id)}"
    ban = await enqueue_telegram_delivery_job(
        db,
        current_server=current_server,
        feeder=TelegramFeederKind.ADMIN_SYSTEM,
        source_natural_id=f"{source_prefix}:ban",
        source_version=source_version,
        action=TelegramDeliveryAction.CHANNEL_MEMBER_BAN,
        bot_identity="primary",
        destination_key=destination_key,
        destination_class=TelegramDestinationClass.CHANNEL,
        method="banChatMember",
        payload={
            "chat_id": channel_id,
            "user_id": telegram_id,
            "revoke_messages": False,
        },
        template_version=MEMBERSHIP_SAGA_TEMPLATE_VERSION,
    )
    unban = await enqueue_telegram_delivery_job(
        db,
        current_server=current_server,
        feeder=TelegramFeederKind.ADMIN_SYSTEM,
        source_natural_id=f"{source_prefix}:unban",
        source_version=source_version,
        action=TelegramDeliveryAction.CHANNEL_MEMBER_UNBAN,
        bot_identity="primary",
        destination_key=destination_key,
        destination_class=TelegramDestinationClass.CHANNEL,
        method="unbanChatMember",
        payload={
            "chat_id": channel_id,
            "user_id": telegram_id,
            "only_if_banned": True,
        },
        template_version=MEMBERSHIP_SAGA_TEMPLATE_VERSION,
    )
    if not ban.created or not unban.created:
        raise TelegramChannelMembershipSagaError(
            "telegram_membership_saga_orphan_job_conflict"
        )
    saga.ban_job_id = int(ban.job.id)
    saga.unban_job_id = int(unban.job.id)
    saga.reason = "membership_ban_pending"
    saga.updated_at = now
    await db.flush()
    return TelegramChannelMembershipSagaResult(saga=saga, created=created)
