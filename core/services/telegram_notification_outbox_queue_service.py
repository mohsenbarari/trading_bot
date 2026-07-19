"""Atomic feeder handoff from the private notification outbox."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.server_routing import SERVER_FOREIGN
from core.services.bot_access_policy import evaluate_bot_access
from core.services.customer_relation_service import (
    get_active_customer_relation_for_user,
)
from core.services.telegram_delivery_queue_service import (
    enqueue_telegram_delivery_job,
)
from core.services.telegram_channel_membership_saga_service import (
    ensure_telegram_channel_membership_removal_saga,
)
from core.services.telegram_notification_outbox_service import (
    TELEGRAM_NOTIFICATION_SOURCE_OFFER_REPEAT_RESPONSE,
    TELEGRAM_NOTIFICATION_SOURCE_PROJECT_USER_JOINED,
)
from core.telegram_delivery_new_user_membership_freshness import (
    NEW_USER_MEMBERSHIP_TEMPLATE_VERSION,
    build_new_user_membership_payload,
    telegram_new_user_membership_campaign_id,
    telegram_new_user_membership_destination_key,
    telegram_new_user_membership_source_natural_id,
    telegram_new_user_membership_source_version,
)
from core.telegram_delivery_notification_action_contract import (
    TELEGRAM_NOTIFICATION_ACTION_SOURCE_TYPES,
    telegram_notification_action_policy_from_source,
)
from core.telegram_delivery_notification_action_freshness import (
    build_telegram_notification_action_snapshot,
    resolve_telegram_notification_action_interaction_target,
    telegram_notification_action_deleted_route_is_reassigned,
    telegram_notification_action_destination_key,
    telegram_notification_action_outbox_is_deleted_account_notice,
    telegram_notification_action_outbox_matches_current_user,
    telegram_notification_action_outbox_waits_for_current_user,
    telegram_notification_action_source_natural_id,
)
from core.telegram_delivery_offer_success_contract import (
    OFFER_SUCCESS_TEMPLATE_VERSION,
    TELEGRAM_NOTIFICATION_SOURCE_OFFER_SUCCESS,
)
from core.telegram_delivery_offer_success_freshness import (
    build_telegram_offer_success_snapshot,
    load_offer_success_offer,
    telegram_offer_success_destination_key,
    telegram_offer_success_outbox_matches_current_state,
    telegram_offer_success_outbox_waits_for_state,
    telegram_offer_success_source_natural_id,
)
from core.telegram_delivery_repeat_offer_freshness import (
    REPEAT_OFFER_RESPONSE_TEMPLATE_VERSION,
    build_repeat_offer_response_snapshot,
    telegram_repeat_offer_response_destination_key,
    telegram_repeat_offer_response_source_natural_id,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDestinationClass,
    TelegramFeederKind,
)
from core.telegram_delivery_interaction_result_contract import (
    TelegramInteractionDependencyOutcome,
)
from core.utils import utc_now
from models.telegram_notification_outbox import (
    TelegramNotificationOutbox,
    TelegramNotificationOutboxStatus,
)
from models.user import User


NOTIFICATION_OUTBOX_QUEUE_HANDOFF = "handed_off"
NOTIFICATION_OUTBOX_QUEUE_SKIPPED = "skipped"
NOTIFICATION_OUTBOX_QUEUE_TERMINAL_FAILED = "terminal_failed"
NOTIFICATION_OUTBOX_QUEUE_DEFERRED = "deferred"
NOTIFICATION_OUTBOX_QUEUE_REQUIRES_RECONCILIATION = "requires_reconciliation"

_ACTIVE_STATUSES = (
    TelegramNotificationOutboxStatus.PENDING,
    TelegramNotificationOutboxStatus.RETRYABLE_FAILED,
)
_QUEUE_SOURCE_TYPES = (
    TELEGRAM_NOTIFICATION_SOURCE_PROJECT_USER_JOINED,
    TELEGRAM_NOTIFICATION_SOURCE_OFFER_REPEAT_RESPONSE,
    TELEGRAM_NOTIFICATION_SOURCE_OFFER_SUCCESS,
    *sorted(TELEGRAM_NOTIFICATION_ACTION_SOURCE_TYPES),
)


class TelegramNotificationOutboxQueueHandoffError(RuntimeError):
    """Raised before cross-server or unsafe queue handoff."""


@dataclass(frozen=True, slots=True)
class TelegramNotificationOutboxQueueHandoffResult:
    outbox_id: int
    disposition: str
    job_id: int | None = None
    job_created: bool = False
    reason: str | None = None


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed > 0 else None


async def _flush(db: AsyncSession | Any) -> None:
    flush = getattr(db, "flush", None)
    if callable(flush):
        await flush()


async def _select_next_due_outbox(
    db: AsyncSession,
    *,
    now: datetime,
) -> TelegramNotificationOutbox | None:
    return (
        await db.execute(
            select(TelegramNotificationOutbox)
            .where(
                TelegramNotificationOutbox.source_type.in_(_QUEUE_SOURCE_TYPES),
                TelegramNotificationOutbox.status.in_(_ACTIVE_STATUSES),
                TelegramNotificationOutbox.queue_job_id.is_(None),
                TelegramNotificationOutbox.queue_handed_off_at.is_(None),
                TelegramNotificationOutbox.worker_id.is_(None),
                TelegramNotificationOutbox.lease_until.is_(None),
                or_(
                    TelegramNotificationOutbox.next_retry_at.is_(None),
                    TelegramNotificationOutbox.next_retry_at <= now,
                ),
            )
            .order_by(
                TelegramNotificationOutbox.next_retry_at.asc().nullsfirst(),
                TelegramNotificationOutbox.id.asc(),
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
    ).scalar_one_or_none()


async def _finalize_unhandoffable_outbox(
    db: AsyncSession,
    *,
    outbox: TelegramNotificationOutbox,
    status: TelegramNotificationOutboxStatus,
    reason: str,
    error_class: str,
    now: datetime,
) -> TelegramNotificationOutboxQueueHandoffResult:
    outbox.status = status
    outbox.reason = reason[:120]
    outbox.telegram_id_at_send = None
    outbox.telegram_message_id = None
    outbox.next_retry_at = None
    outbox.last_error_class = error_class[:120]
    outbox.last_error_message = reason[:500]
    outbox.worker_id = None
    outbox.lease_until = None
    outbox.queue_job_id = None
    outbox.queue_handed_off_at = None
    outbox.sent_at = None
    outbox.terminal_at = now
    outbox.updated_at = now
    await _flush(db)
    return TelegramNotificationOutboxQueueHandoffResult(
        outbox_id=int(outbox.id),
        disposition=(
            NOTIFICATION_OUTBOX_QUEUE_SKIPPED
            if status == TelegramNotificationOutboxStatus.SKIPPED
            else NOTIFICATION_OUTBOX_QUEUE_TERMINAL_FAILED
        ),
        reason=reason,
    )


async def handoff_next_due_telegram_notification_outbox(
    db: AsyncSession,
    *,
    current_server: str,
    expected_channel_id: int | None = None,
    now: datetime | None = None,
) -> TelegramNotificationOutboxQueueHandoffResult | None:
    """Bind one eligible allowlisted outbox row to one main-queue job."""
    if str(current_server or "").strip().lower() != SERVER_FOREIGN:
        raise TelegramNotificationOutboxQueueHandoffError(
            "telegram_notification_outbox_queue_handoff_is_foreign_only"
        )
    current_time = now or utc_now()
    outbox = await _select_next_due_outbox(db, now=current_time)
    if outbox is None:
        return None

    recipient_user_id = _positive_int(outbox.recipient_user_id)
    if recipient_user_id is None:
        return await _finalize_unhandoffable_outbox(
            db,
            outbox=outbox,
            status=TelegramNotificationOutboxStatus.SKIPPED,
            reason="telegram_user_missing_current",
            error_class="TelegramUserUnavailable",
            now=current_time,
        )
    user = await db.get(User, recipient_user_id)
    if user is None:
        return await _finalize_unhandoffable_outbox(
            db,
            outbox=outbox,
            status=TelegramNotificationOutboxStatus.SKIPPED,
            reason="telegram_user_missing_current",
            error_class="TelegramUserUnavailable",
            now=current_time,
        )
    source_type = str(outbox.source_type or "").strip()
    try:
        action_policy = telegram_notification_action_policy_from_source(source_type)
    except ValueError:
        action_policy = None
    deleted_account_notice = False
    if action_policy is not None:
        try:
            deleted_account_notice = (
                telegram_notification_action_outbox_is_deleted_account_notice(
                    outbox
                )
            )
        except (TypeError, ValueError, OverflowError) as exc:
            return await _finalize_unhandoffable_outbox(
                db,
                outbox=outbox,
                status=TelegramNotificationOutboxStatus.TERMINAL_FAILED,
                reason=str(exc)[:120] or "notification_action_payload_invalid",
                error_class="TelegramPayloadError",
                now=current_time,
            )
    if _positive_int(user.telegram_id) is None and not deleted_account_notice:
        return await _finalize_unhandoffable_outbox(
            db,
            outbox=outbox,
            status=TelegramNotificationOutboxStatus.SKIPPED,
            reason="telegram_unlinked_current",
            error_class="TelegramUserUnavailable",
            now=current_time,
        )
    if (
        deleted_account_notice
        and await telegram_notification_action_deleted_route_is_reassigned(
            db,
            outbox,
        )
    ):
        return await _finalize_unhandoffable_outbox(
            db,
            outbox=outbox,
            status=TelegramNotificationOutboxStatus.SKIPPED,
            reason="notification_action_deleted_route_reassigned",
            error_class="TelegramNotificationSuperseded",
            now=current_time,
        )
    if (
        action_policy is not None
        and not deleted_account_notice
        and _positive_int(user.telegram_id)
        != _positive_int(outbox.telegram_id_at_enqueue)
    ):
        return await _finalize_unhandoffable_outbox(
            db,
            outbox=outbox,
            status=TelegramNotificationOutboxStatus.SKIPPED,
            reason="notification_action_recipient_relinked",
            error_class="TelegramNotificationSuperseded",
            now=current_time,
        )
    offer_success_offer = None
    if source_type == TELEGRAM_NOTIFICATION_SOURCE_OFFER_SUCCESS:
        try:
            offer_success_offer = await load_offer_success_offer(
                db,
                str(outbox.source_id or "").strip(),
            )
            waits_for_offer_success = (
                telegram_offer_success_outbox_waits_for_state(
                    outbox,
                    user,
                    offer_success_offer,
                )
            )
        except (TypeError, ValueError, OverflowError) as exc:
            return await _finalize_unhandoffable_outbox(
                db,
                outbox=outbox,
                status=TelegramNotificationOutboxStatus.TERMINAL_FAILED,
                reason=str(exc)[:120] or "offer_success_payload_invalid",
                error_class="TelegramPayloadError",
                now=current_time,
            )
        if waits_for_offer_success:
            outbox.reason = "offer_success_source_version_pending"
            outbox.next_retry_at = current_time + timedelta(seconds=1)
            outbox.updated_at = current_time
            await _flush(db)
            return TelegramNotificationOutboxQueueHandoffResult(
                outbox_id=int(outbox.id),
                disposition=NOTIFICATION_OUTBOX_QUEUE_DEFERRED,
                reason="offer_success_source_version_pending",
            )
    if action_policy is not None:
        try:
            waits_for_recipient = (
                telegram_notification_action_outbox_waits_for_current_user(
                    outbox,
                    user,
                )
            )
        except (TypeError, ValueError, OverflowError) as exc:
            return await _finalize_unhandoffable_outbox(
                db,
                outbox=outbox,
                status=TelegramNotificationOutboxStatus.TERMINAL_FAILED,
                reason=str(exc)[:120] or "notification_action_payload_invalid",
                error_class="TelegramPayloadError",
                now=current_time,
            )
        if waits_for_recipient:
            outbox.reason = "notification_action_recipient_version_pending"
            outbox.next_retry_at = current_time + timedelta(seconds=1)
            outbox.updated_at = current_time
            await _flush(db)
            return TelegramNotificationOutboxQueueHandoffResult(
                outbox_id=int(outbox.id),
                disposition=NOTIFICATION_OUTBOX_QUEUE_DEFERRED,
                reason="notification_action_recipient_version_pending",
            )
        try:
            target_decision = (
                await resolve_telegram_notification_action_interaction_target(
                    db,
                    outbox,
                )
            )
        except (TypeError, ValueError, OverflowError) as exc:
            return await _finalize_unhandoffable_outbox(
                db,
                outbox=outbox,
                status=TelegramNotificationOutboxStatus.TERMINAL_FAILED,
                reason=str(exc)[:120] or "notification_action_target_invalid",
                error_class="TelegramPayloadError",
                now=current_time,
            )
        if target_decision is not None:
            if (
                target_decision.outcome
                == TelegramInteractionDependencyOutcome.WAIT_DEPENDENCY
            ):
                outbox.reason = "notification_action_result_target_pending"
                outbox.next_retry_at = current_time + timedelta(seconds=1)
                outbox.updated_at = current_time
                await _flush(db)
                return TelegramNotificationOutboxQueueHandoffResult(
                    outbox_id=int(outbox.id),
                    disposition=NOTIFICATION_OUTBOX_QUEUE_DEFERRED,
                    reason="notification_action_result_target_pending",
                )
            if (
                target_decision.outcome
                == TelegramInteractionDependencyOutcome.SUPERSEDED
            ):
                return await _finalize_unhandoffable_outbox(
                    db,
                    outbox=outbox,
                    status=TelegramNotificationOutboxStatus.SKIPPED,
                    reason=target_decision.reason
                    or "notification_action_result_target_superseded",
                    error_class="TelegramNotificationSuperseded",
                    now=current_time,
                )
            if (
                target_decision.outcome
                != TelegramInteractionDependencyOutcome.READY
            ):
                return await _finalize_unhandoffable_outbox(
                    db,
                    outbox=outbox,
                    status=TelegramNotificationOutboxStatus.TERMINAL_FAILED,
                    reason=target_decision.reason
                    or "notification_action_result_target_quarantined",
                    error_class="TelegramPayloadError",
                    now=current_time,
                )
        await ensure_telegram_channel_membership_removal_saga(
            db,
            current_server=current_server,
            outbox=outbox,
            user=user,
            expected_channel_id=(
                expected_channel_id
                if expected_channel_id is not None
                else getattr(settings, "channel_id", None)
            ),
            now=current_time,
        )
    access = await evaluate_bot_access(db, user)
    if (
        not deleted_account_notice
        and (action_policy is None or action_policy.require_bot_access)
        and not access.allowed
    ):
        return await _finalize_unhandoffable_outbox(
            db,
            outbox=outbox,
            status=TelegramNotificationOutboxStatus.SKIPPED,
            reason=str(access.reason or "bot_access_denied_current")[:120],
            error_class="BotAccessDenied",
            now=current_time,
        )
    if (
        source_type == TELEGRAM_NOTIFICATION_SOURCE_PROJECT_USER_JOINED
        and await get_active_customer_relation_for_user(db, recipient_user_id)
        is not None
    ):
        return await _finalize_unhandoffable_outbox(
            db,
            outbox=outbox,
            status=TelegramNotificationOutboxStatus.SKIPPED,
            reason="customer_excluded_current",
            error_class="CustomerExcluded",
            now=current_time,
        )

    method = "sendMessage"
    try:
        if source_type == TELEGRAM_NOTIFICATION_SOURCE_PROJECT_USER_JOINED:
            source_natural_id = telegram_new_user_membership_source_natural_id(
                outbox
            )
            source_version = telegram_new_user_membership_source_version(user)
            destination_key = telegram_new_user_membership_destination_key(
                recipient_user_id
            )
            payload = build_new_user_membership_payload(outbox, user)
            source_id = int(str(outbox.source_id or "").strip())
            campaign_id = telegram_new_user_membership_campaign_id(source_id)
            feeder = TelegramFeederKind.ADMIN_SYSTEM
            action = TelegramDeliveryAction.NEW_USER_MEMBERSHIP
            template_version = NEW_USER_MEMBERSHIP_TEMPLATE_VERSION
        elif source_type == TELEGRAM_NOTIFICATION_SOURCE_OFFER_REPEAT_RESPONSE:
            source_natural_id = telegram_repeat_offer_response_source_natural_id(
                outbox
            )
            destination_key = telegram_repeat_offer_response_destination_key(
                recipient_user_id
            )
            snapshot = await build_repeat_offer_response_snapshot(db, outbox, user)
            source_version = snapshot.source_version
            payload = snapshot.payload
            campaign_id = None
            feeder = TelegramFeederKind.OFFER_CONTROL
            action = TelegramDeliveryAction.OFFER_REPEAT_RESPONSE
            template_version = REPEAT_OFFER_RESPONSE_TEMPLATE_VERSION
        elif source_type == TELEGRAM_NOTIFICATION_SOURCE_OFFER_SUCCESS:
            source_natural_id = telegram_offer_success_source_natural_id(outbox)
            destination_key = telegram_offer_success_destination_key(
                recipient_user_id
            )
            if not telegram_offer_success_outbox_matches_current_state(
                outbox,
                user,
                offer_success_offer,
            ):
                return await _finalize_unhandoffable_outbox(
                    db,
                    outbox=outbox,
                    status=TelegramNotificationOutboxStatus.SKIPPED,
                    reason="offer_success_source_state_changed",
                    error_class="TelegramNotificationSuperseded",
                    now=current_time,
                )
            snapshot = build_telegram_offer_success_snapshot(
                outbox,
                user,
                offer_success_offer,
            )
            source_version = snapshot.source_version
            payload = snapshot.payload
            campaign_id = None
            feeder = TelegramFeederKind.OFFER_CONTROL
            action = TelegramDeliveryAction.OFFER_SUCCESS
            template_version = OFFER_SUCCESS_TEMPLATE_VERSION
            method = "editMessageText"
        elif action_policy is not None:
            source_natural_id = telegram_notification_action_source_natural_id(
                outbox
            )
            destination_key = telegram_notification_action_destination_key(
                recipient_user_id
            )
            if not telegram_notification_action_outbox_matches_current_user(
                outbox,
                user,
                now=current_time,
            ):
                return await _finalize_unhandoffable_outbox(
                    db,
                    outbox=outbox,
                    status=TelegramNotificationOutboxStatus.SKIPPED,
                    reason="notification_action_source_state_changed",
                    error_class="TelegramNotificationSuperseded",
                    now=current_time,
                )
            snapshot = build_telegram_notification_action_snapshot(
                outbox,
                user,
                resolved_target_message_id=(
                    target_decision.message_id
                    if target_decision is not None
                    else None
                ),
            )
            source_version = snapshot.source_version
            payload = snapshot.payload
            method = snapshot.method
            campaign_id = None
            feeder = action_policy.feeder
            action = action_policy.action
            template_version = action_policy.template_version
        else:
            raise ValueError("telegram_notification_queue_source_unsupported")
    except (TypeError, ValueError, OverflowError) as exc:
        return await _finalize_unhandoffable_outbox(
            db,
            outbox=outbox,
            status=TelegramNotificationOutboxStatus.TERMINAL_FAILED,
            reason=str(exc)[:120] or "new_user_membership_payload_invalid",
            error_class="TelegramPayloadError",
            now=current_time,
        )

    enqueue_result = await enqueue_telegram_delivery_job(
        db,
        current_server=current_server,
        feeder=feeder,
        source_natural_id=source_natural_id,
        source_version=source_version,
        action=action,
        bot_identity="primary",
        destination_key=destination_key,
        destination_class=TelegramDestinationClass.PRIVATE,
        method=method,
        payload=payload,
        template_version=template_version,
        campaign_id=campaign_id,
    )
    job_id = int(enqueue_result.job.id)
    if not enqueue_result.created:
        outbox.worker_id = f"telegram-delivery-reconcile:membership:{job_id}"[:128]
        outbox.lease_until = None
        outbox.reason = "notification_outbox_queue_orphan_requires_reconciliation"
        outbox.next_retry_at = None
        outbox.updated_at = current_time
        await _flush(db)
        return TelegramNotificationOutboxQueueHandoffResult(
            outbox_id=int(outbox.id),
            disposition=NOTIFICATION_OUTBOX_QUEUE_REQUIRES_RECONCILIATION,
            job_id=job_id,
            job_created=False,
            reason="notification_outbox_queue_orphan_requires_reconciliation",
        )

    outbox.queue_job_id = job_id
    outbox.queue_handed_off_at = current_time
    outbox.worker_id = None
    outbox.lease_until = None
    outbox.reason = "notification_outbox_handed_to_main_queue"
    outbox.updated_at = current_time
    await _flush(db)
    return TelegramNotificationOutboxQueueHandoffResult(
        outbox_id=int(outbox.id),
        disposition=NOTIFICATION_OUTBOX_QUEUE_HANDOFF,
        job_id=job_id,
        job_created=True,
        reason="notification_outbox_handed_to_main_queue",
    )
