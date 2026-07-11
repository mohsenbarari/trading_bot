"""Shared lock order for every Invitation terminal-state writer."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.services.invitation_identity_reservation_service import (
    NormalizedInvitationIdentity,
    acquire_invitation_transition_locks,
    invitation_transition_lock_keys,
    normalize_invitation_identity,
)
from models.invitation import Invitation


async def lock_invitation_row_for_transition(
    db: AsyncSession,
    *,
    invitation_id: int | None = None,
    invitation_token: str | None = None,
) -> Invitation | None:
    if (invitation_id is None) == (invitation_token is None):
        raise ValueError("exactly_one_invitation_identity_required")
    stmt = select(Invitation)
    if invitation_id is not None:
        stmt = stmt.where(Invitation.id == int(invitation_id))
    else:
        stmt = stmt.where(Invitation.token == str(invitation_token))
    return (
        await db.execute(
            stmt.with_for_update().execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


def normalized_invitation_identity_or_none(
    invitation: Invitation,
) -> NormalizedInvitationIdentity | None:
    try:
        return normalize_invitation_identity(
            mobile_number=getattr(invitation, "mobile_number", ""),
            account_name=getattr(invitation, "account_name", ""),
        )
    except ValueError:
        return None


async def acquire_locked_invitation_transition_locks(
    db: AsyncSession,
    invitation: Invitation,
    *,
    telegram_id: int | None = None,
    identity: NormalizedInvitationIdentity | None = None,
    invitation_token: str | None = None,
) -> None:
    await acquire_invitation_transition_locks(
        db,
        invitation_token=(invitation_token or getattr(invitation, "token", None)),
        identity=(identity if identity is not None else normalized_invitation_identity_or_none(invitation)),
        telegram_id=telegram_id,
    )


async def lock_invitation_for_transition(
    db: AsyncSession,
    *,
    invitation_id: int | None = None,
    invitation_token: str | None = None,
    telegram_id: int | None = None,
) -> Invitation | None:
    # Pre-read only to derive the complete advisory key set. Every writer then
    # takes sorted advisory locks before the row lock, matching exact-create
    # retries and eliminating identity-lock/row-lock inversion.
    stmt = select(Invitation)
    if invitation_id is not None:
        stmt = stmt.where(Invitation.id == int(invitation_id))
    else:
        stmt = stmt.where(Invitation.token == str(invitation_token))
    probe = (await db.execute(stmt)).scalar_one_or_none()
    probe_keys = invitation_transition_lock_keys(
        invitation_token=(invitation_token or getattr(probe, "token", None)),
        identity=(normalized_invitation_identity_or_none(probe) if probe is not None else None),
        telegram_id=telegram_id,
    )
    if probe is not None:
        await acquire_locked_invitation_transition_locks(
            db,
            probe,
            telegram_id=telegram_id,
            invitation_token=invitation_token,
        )
    elif invitation_token is not None:
        await acquire_invitation_transition_locks(
            db,
            invitation_token=invitation_token,
            identity=None,
            telegram_id=telegram_id,
        )

    invitation = await lock_invitation_row_for_transition(
        db,
        invitation_id=invitation_id,
        invitation_token=invitation_token,
    )
    if invitation is not None:
        current_keys = invitation_transition_lock_keys(
            invitation_token=(invitation_token or invitation.token),
            identity=normalized_invitation_identity_or_none(invitation),
            telegram_id=telegram_id,
        )
        if probe is None or current_keys != probe_keys:
            raise RuntimeError("invitation_identity_changed_during_transition_lock")
    return invitation
