"""Iran-authoritative Invitation creation with global identity reservation."""

from __future__ import annotations

from dataclasses import dataclass
import secrets
import string

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.registration_identity import normalize_account_name, normalize_mobile_number
from core.server_routing import SERVER_IRAN, current_server
from core.services.invitation_identity_reservation_service import (
    InvitationIdentityReservationConflict,
    acquire_invitation_creation_locks,
    find_identity_reservation,
    normalize_invitation_identity,
    prune_terminal_identity_reservations,
    reserve_invitation_identity,
)
from core.registration_contracts import InvitationDerivedState
from core.services.invitation_lifecycle_service import derive_invitation_state, get_new_invitation_expiry
from models.invitation import Invitation, InvitationKind
from models.user import User, UserRole


class CanonicalInvitationCreationError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class CanonicalInvitationCreationResult:
    invitation: Invitation
    created: bool


def generate_invitation_token(kind: InvitationKind) -> str:
    prefixes = {
        InvitationKind.STANDARD: "INV-",
        InvitationKind.ACCOUNTANT: "ACCT-",
        InvitationKind.CUSTOMER: "CUST-",
    }
    try:
        prefix = prefixes[InvitationKind(kind)]
    except (KeyError, ValueError) as exc:
        raise CanonicalInvitationCreationError("invitation_kind_not_creatable") from exc
    return prefix + secrets.token_hex(16)


def generate_invitation_short_code() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(8))


def _enum_value(value: object) -> str:
    return str(getattr(value, "value", value) or "")


def _is_exact_base_retry(
    invitation: Invitation,
    *,
    creator_user_id: int,
    kind: InvitationKind,
    role: UserRole,
    normalized_mobile: str,
    normalized_account_name: str,
) -> bool:
    return (
        derive_invitation_state(invitation) == InvitationDerivedState.PENDING
        and int(getattr(invitation, "created_by_id", 0) or 0) == int(creator_user_id)
        and _enum_value(getattr(invitation, "kind", None)) == kind.value
        and _enum_value(getattr(invitation, "role", None)) == role.value
        and normalize_mobile_number(getattr(invitation, "mobile_number", "")) == normalized_mobile
        and normalize_account_name(getattr(invitation, "account_name", "")) == normalized_account_name
    )


async def create_or_reuse_canonical_invitation(
    db: AsyncSession,
    *,
    creator_user_id: int,
    account_name: str,
    mobile_number: str,
    role: UserRole,
    kind: InvitationKind,
) -> CanonicalInvitationCreationResult:
    """Create or return an exact pending retry without committing the transaction."""

    if current_server() != SERVER_IRAN:
        raise CanonicalInvitationCreationError("iran_authority_required")
    if int(creator_user_id) <= 0:
        raise CanonicalInvitationCreationError("invalid_creator")
    try:
        normalized_role = UserRole(role)
        normalized_kind = InvitationKind(kind)
    except ValueError as exc:
        raise CanonicalInvitationCreationError("invalid_invitation_policy") from exc
    if normalized_kind == InvitationKind.LEGACY_UNKNOWN:
        raise CanonicalInvitationCreationError("invitation_kind_not_creatable")

    try:
        identity = normalize_invitation_identity(
            mobile_number=mobile_number,
            account_name=account_name,
        )
    except ValueError as exc:
        raise CanonicalInvitationCreationError("invalid_identity") from exc

    await acquire_invitation_creation_locks(
        db,
        creator_user_id=creator_user_id,
        identity=identity,
    )
    await prune_terminal_identity_reservations(db, identity)

    existing_user_stmt = (
        select(User.id)
        .where(
            or_(
                User.normalized_mobile_number == identity.mobile_number,
                User.normalized_account_name == identity.account_name,
            )
        )
        .limit(1)
    )
    if (await db.execute(existing_user_stmt)).scalar_one_or_none() is not None:
        raise CanonicalInvitationCreationError("user_identity_exists")

    reservation = await find_identity_reservation(db, identity)
    if reservation is not None:
        invitation_stmt = (
            select(Invitation)
            .where(Invitation.id == reservation.invitation_id)
            .with_for_update()
        )
        invitation = (await db.execute(invitation_stmt)).scalar_one_or_none()
        if invitation is None:
            raise CanonicalInvitationCreationError("reservation_integrity_error")
        if _is_exact_base_retry(
            invitation,
            creator_user_id=creator_user_id,
            kind=normalized_kind,
            role=normalized_role,
            normalized_mobile=identity.mobile_number,
            normalized_account_name=identity.account_name,
        ):
            return CanonicalInvitationCreationResult(invitation=invitation, created=False)
        raise CanonicalInvitationCreationError("invitation_identity_conflict")

    invitation = Invitation(
        account_name=identity.account_name,
        mobile_number=identity.mobile_number,
        role=normalized_role,
        kind=normalized_kind,
        token=generate_invitation_token(normalized_kind),
        short_code=generate_invitation_short_code(),
        created_by_id=int(creator_user_id),
        expires_at=await get_new_invitation_expiry(),
    )
    db.add(invitation)
    await db.flush()
    try:
        await reserve_invitation_identity(db, invitation=invitation, identity=identity)
    except InvitationIdentityReservationConflict as exc:
        raise CanonicalInvitationCreationError(exc.code) from exc
    return CanonicalInvitationCreationResult(invitation=invitation, created=True)
