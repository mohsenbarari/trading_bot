"""Transactional invitation natural-key reservation primitives."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.services.invitation_lifecycle_service import derive_invitation_state
from core.registration_contracts import InvitationDerivedState
from core.utils import normalize_account_name, normalize_persian_numerals
from models.invitation import Invitation
from models.invitation_identity_reservation import InvitationIdentityReservation


@dataclass(frozen=True)
class NormalizedInvitationIdentity:
    mobile_number: str
    account_name: str


class InvitationIdentityReservationConflict(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def normalize_invitation_identity(*, mobile_number: str, account_name: str) -> NormalizedInvitationIdentity:
    mobile = normalize_persian_numerals(str(mobile_number or "")).strip()
    account = normalize_account_name(str(account_name or "").strip())
    if len(mobile) != 11 or not mobile.startswith("09") or not mobile.isdigit():
        raise ValueError("شماره موبایل نامعتبر است")
    if not account:
        raise ValueError("نام کاربری نامعتبر است")
    return NormalizedInvitationIdentity(mobile_number=mobile, account_name=account)


def invitation_identity_lock_keys(identity: NormalizedInvitationIdentity) -> tuple[str, str]:
    return tuple(sorted((f"invitation:account:{identity.account_name}", f"invitation:mobile:{identity.mobile_number}")))


async def acquire_invitation_identity_locks(
    db: AsyncSession,
    identity: NormalizedInvitationIdentity,
) -> None:
    for lock_key in invitation_identity_lock_keys(identity):
        await db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": lock_key},
        )


async def prune_terminal_identity_reservations(
    db: AsyncSession,
    identity: NormalizedInvitationIdentity,
) -> None:
    stmt = (
        select(InvitationIdentityReservation, Invitation)
        .join(Invitation, Invitation.id == InvitationIdentityReservation.invitation_id)
        .where(
            or_(
                InvitationIdentityReservation.normalized_mobile == identity.mobile_number,
                InvitationIdentityReservation.normalized_account_name == identity.account_name,
            )
        )
    )
    rows = (await db.execute(stmt)).all()
    terminal_ids = [
        reservation.id
        for reservation, invitation in rows
        if derive_invitation_state(invitation) != InvitationDerivedState.PENDING
    ]
    if terminal_ids:
        await db.execute(
            delete(InvitationIdentityReservation).where(InvitationIdentityReservation.id.in_(terminal_ids))
        )


async def find_identity_reservation(
    db: AsyncSession,
    identity: NormalizedInvitationIdentity,
) -> InvitationIdentityReservation | None:
    stmt = select(InvitationIdentityReservation).where(
        or_(
            InvitationIdentityReservation.normalized_mobile == identity.mobile_number,
            InvitationIdentityReservation.normalized_account_name == identity.account_name,
        )
    )
    reservations = list((await db.execute(stmt)).scalars().all())
    if len(reservations) > 1:
        raise InvitationIdentityReservationConflict("identity_split_reserved")
    return reservations[0] if reservations else None


async def reserve_invitation_identity(
    db: AsyncSession,
    *,
    invitation: Invitation,
    identity: NormalizedInvitationIdentity,
) -> InvitationIdentityReservation:
    if getattr(invitation, "id", None) is None:
        raise ValueError("Invitation must be flushed before reserving its identity")
    existing = await find_identity_reservation(db, identity)
    if existing is not None:
        if (
            existing.invitation_id == invitation.id
            and existing.normalized_mobile == identity.mobile_number
            and existing.normalized_account_name == identity.account_name
        ):
            return existing
        if existing.normalized_mobile == identity.mobile_number and existing.normalized_account_name == identity.account_name:
            raise InvitationIdentityReservationConflict("identity_reserved")
        if existing.normalized_mobile == identity.mobile_number:
            raise InvitationIdentityReservationConflict("mobile_reserved")
        raise InvitationIdentityReservationConflict("account_name_reserved")
    reservation = InvitationIdentityReservation(
        invitation_id=invitation.id,
        normalized_mobile=identity.mobile_number,
        normalized_account_name=identity.account_name,
    )
    db.add(reservation)
    await db.flush()
    return reservation


async def release_invitation_identity(
    db: AsyncSession,
    *,
    invitation_id: int,
) -> None:
    await db.execute(
        delete(InvitationIdentityReservation).where(
            InvitationIdentityReservation.invitation_id == int(invitation_id)
        )
    )


async def release_invitation_identities_for_tokens(
    db: AsyncSession,
    *,
    invitation_tokens: list[str] | tuple[str, ...] | set[str],
) -> None:
    tokens = sorted({str(token).strip() for token in invitation_tokens if str(token).strip()})
    if not tokens:
        return
    invitation_ids = select(Invitation.id).where(Invitation.token.in_(tokens))
    await db.execute(
        delete(InvitationIdentityReservation).where(
            InvitationIdentityReservation.invitation_id.in_(invitation_ids)
        )
    )
