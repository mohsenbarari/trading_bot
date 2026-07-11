"""Resolve signed Invitation requesters against current Iran User truth."""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.enums import UserAccountStatus
from core.invitation_creation_contracts import InvitationRequesterIdentity
from core.registration_identity import normalize_account_name, normalize_mobile_number
from models.user import User


class InvitationRequesterResolutionError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


async def resolve_current_invitation_requester(
    db: AsyncSession,
    *,
    identity: InvitationRequesterIdentity,
) -> User:
    """Lock and return exactly one current User matching every canonical identity field."""

    candidates = list(
        (
            await db.execute(
                select(User)
                .where(
                    or_(
                        User.normalized_account_name == identity.account_name,
                        User.normalized_mobile_number == identity.mobile_number,
                        User.telegram_id == identity.telegram_id,
                    )
                )
                .order_by(User.id.asc())
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalars().all()
    )
    exact = [
        user
        for user in candidates
        if normalize_account_name(user.account_name) == identity.account_name
        and normalize_mobile_number(user.mobile_number) == identity.mobile_number
        and user.telegram_id == identity.telegram_id
    ]
    if len(exact) != 1 or len(candidates) != 1:
        raise InvitationRequesterResolutionError(
            "requester_missing" if not candidates else "requester_identity_conflict"
        )
    requester = exact[0]
    if bool(getattr(requester, "is_deleted", False)):
        raise InvitationRequesterResolutionError("requester_deleted")
    status = getattr(requester, "account_status", UserAccountStatus.ACTIVE)
    if str(getattr(status, "value", status)) != UserAccountStatus.ACTIVE.value:
        raise InvitationRequesterResolutionError("requester_inactive")
    return requester
