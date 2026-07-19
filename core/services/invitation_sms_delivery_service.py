"""Durable, replay-stable Invitation SMS claim and result handling."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.registration_contracts import InvitationSMSStatus
from core.dr_effects import execute_claimed_inline_effect
from core.server_routing import SERVER_IRAN, current_server
from core.sms import SMSDeliveryOutcome
from core.utils import utc_now
from models.invitation import Invitation
from models.invitation_sms_delivery import InvitationSMSDelivery


_CLAIM_LEASE_SECONDS = 30


async def load_invitation_sms_status_map(
    db: AsyncSession,
    invitation_ids: list[int] | tuple[int, ...],
) -> dict[int, InvitationSMSStatus]:
    normalized_ids = sorted({int(value) for value in invitation_ids if value is not None})
    if not normalized_ids:
        return {}
    deliveries = list(
        (
            await db.execute(
                select(InvitationSMSDelivery).where(
                    InvitationSMSDelivery.invitation_id.in_(normalized_ids)
                )
            )
        )
        .scalars()
        .all()
    )
    result: dict[int, InvitationSMSStatus] = {}
    for delivery in deliveries:
        try:
            result[int(delivery.invitation_id)] = InvitationSMSStatus(delivery.status)
        except (TypeError, ValueError):
            result[int(delivery.invitation_id)] = InvitationSMSStatus.AMBIGUOUS
    return result


async def prepare_invitation_sms_delivery(
    db: AsyncSession,
    *,
    invitation: Invitation,
    enabled: bool,
    newly_created: bool,
) -> InvitationSMSDelivery:
    if current_server() != SERVER_IRAN:
        raise RuntimeError("invitation_sms_delivery_requires_iran")
    if invitation.id is None:
        await db.flush()
    delivery = (
        await db.execute(
            select(InvitationSMSDelivery)
            .where(InvitationSMSDelivery.invitation_id == invitation.id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if delivery is not None:
        return delivery
    status = (
        InvitationSMSStatus.PENDING
        if enabled and newly_created
        else InvitationSMSStatus.AMBIGUOUS
        if enabled
        else InvitationSMSStatus.DISABLED
    )
    delivery = InvitationSMSDelivery(
        invitation_id=invitation.id,
        status=status.value,
        attempt_count=0,
        completed_at=(utc_now() if status != InvitationSMSStatus.PENDING else None),
    )
    db.add(delivery)
    await db.flush()
    return delivery


async def deliver_invitation_sms_once(
    db: AsyncSession,
    *,
    invitation_id: int,
    newly_created: bool,
    sender: Callable[[], SMSDeliveryOutcome | bool],
) -> InvitationSMSStatus:
    """Claim once before provider I/O; a lost result becomes ambiguous, never a resend."""

    if current_server() != SERVER_IRAN:
        raise RuntimeError("invitation_sms_delivery_requires_iran")
    delivery = (
        await db.execute(
            select(InvitationSMSDelivery)
            .where(InvitationSMSDelivery.invitation_id == int(invitation_id))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if delivery is None:
        raise RuntimeError("invitation_sms_delivery_missing")
    status = InvitationSMSStatus(delivery.status)
    if status != InvitationSMSStatus.PENDING:
        await db.commit()
        return status
    # A committed pending row with no claim is still safe to claim after a
    # process restart. `newly_created` describes the caller, not provider I/O.
    if delivery.attempt_count != 0 or delivery.claimed_at is not None:
        claimed_at = delivery.claimed_at
        if claimed_at is not None and claimed_at.tzinfo is None:
            claimed_at = claimed_at.replace(tzinfo=utc_now().tzinfo)
        if claimed_at is not None and claimed_at + timedelta(seconds=_CLAIM_LEASE_SECONDS) > utc_now():
            await db.commit()
            return InvitationSMSStatus.PENDING
        delivery.status = InvitationSMSStatus.AMBIGUOUS.value
        delivery.completed_at = utc_now()
        await db.commit()
        return InvitationSMSStatus.AMBIGUOUS

    delivery.attempt_count = 1
    claim_generation = int(delivery.attempt_count)
    delivery.claimed_at = utc_now()
    await db.commit()

    try:
        sender_outcome = await execute_claimed_inline_effect(
            effect_type="invitation_sms",
            provider="smsir",
            idempotency_key=f"invitation-sms:{int(invitation_id)}:{claim_generation}",
            handler=lambda: asyncio.to_thread(sender),
        )
    except Exception:
        sender_outcome = SMSDeliveryOutcome.AMBIGUOUS

    if isinstance(sender_outcome, SMSDeliveryOutcome):
        result_status = {
            SMSDeliveryOutcome.ACCEPTED: InvitationSMSStatus.ACCEPTED,
            SMSDeliveryOutcome.FAILED: InvitationSMSStatus.FAILED,
            SMSDeliveryOutcome.AMBIGUOUS: InvitationSMSStatus.AMBIGUOUS,
        }[sender_outcome]
    else:
        result_status = (
            InvitationSMSStatus.ACCEPTED
            if bool(sender_outcome)
            else InvitationSMSStatus.AMBIGUOUS
        )

    delivery = (
        await db.execute(
            select(InvitationSMSDelivery)
            .where(InvitationSMSDelivery.invitation_id == int(invitation_id))
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    if int(delivery.attempt_count or 0) != claim_generation:
        status = InvitationSMSStatus(delivery.status)
        await db.commit()
        return status
    if delivery.status not in {
        InvitationSMSStatus.PENDING.value,
        InvitationSMSStatus.AMBIGUOUS.value,
    }:
        status = InvitationSMSStatus(delivery.status)
        await db.commit()
        return status
    if (
        delivery.status == InvitationSMSStatus.AMBIGUOUS.value
        and result_status == InvitationSMSStatus.AMBIGUOUS
    ):
        await db.commit()
        return InvitationSMSStatus.AMBIGUOUS
    delivery.status = result_status.value
    delivery.completed_at = utc_now()
    await db.commit()
    return InvitationSMSStatus(delivery.status)
