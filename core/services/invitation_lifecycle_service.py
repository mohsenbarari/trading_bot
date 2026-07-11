"""Invitation policy and lifecycle primitives with no transaction ownership."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core.registration_contracts import (
    InvitationDerivedState,
    REGISTRATION_ADDRESS_MIN_LENGTH,
    REGISTRATION_ADDRESS_MIN_LENGTH_MESSAGE,
)
from core.trading_settings import get_trading_settings_async
from core.utils import utc_now_naive
from models.invitation import Invitation, InvitationCompletionSurface, InvitationKind


INVITATION_POLICY_VERSION = 1


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def validate_registration_address(value: str) -> str:
    address = str(value or "")
    if len(address) < REGISTRATION_ADDRESS_MIN_LENGTH:
        raise ValueError(REGISTRATION_ADDRESS_MIN_LENGTH_MESSAGE)
    return address


async def get_new_invitation_expiry(*, now: datetime | None = None) -> datetime:
    trading_settings = await get_trading_settings_async()
    try:
        lifetime_days = int(trading_settings.invitation_expiry_days)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Central invitation lifetime is invalid") from exc
    if lifetime_days <= 0:
        raise RuntimeError("Central invitation lifetime must be positive")
    return _utc_naive(now or utc_now_naive()) + timedelta(days=lifetime_days)


def derive_invitation_state(
    invitation: Invitation | object,
    *,
    now: datetime | None = None,
) -> InvitationDerivedState:
    registered_user_id = getattr(invitation, "registered_user_id", None)
    completed_at = getattr(invitation, "completed_at", None)
    completed_via = getattr(invitation, "completed_via", None)
    if bool(getattr(invitation, "is_used", False)) and registered_user_id and completed_at and completed_via:
        return InvitationDerivedState.COMPLETED
    if getattr(invitation, "revoked_at", None) is not None:
        return InvitationDerivedState.REVOKED
    expires_at = getattr(invitation, "expires_at", None)
    if expires_at is not None and _utc_naive(now or utc_now_naive()) > _utc_naive(expires_at):
        return InvitationDerivedState.EXPIRED
    return InvitationDerivedState.PENDING


def complete_invitation(
    invitation: Invitation,
    *,
    registered_user_id: int,
    completed_via: InvitationCompletionSurface,
    completed_at: datetime | None = None,
) -> None:
    if getattr(invitation, "revoked_at", None) is not None:
        raise ValueError("A revoked invitation cannot be completed")
    if int(registered_user_id) <= 0:
        raise ValueError("registered_user_id must be positive")
    invitation.is_used = True
    invitation.registered_user_id = int(registered_user_id)
    invitation.completed_at = completed_at or datetime.now(timezone.utc)
    invitation.completed_via = completed_via


def soft_revoke_invitation(invitation: Invitation, *, revoked_at: datetime | None = None) -> None:
    if derive_invitation_state(invitation) == InvitationDerivedState.COMPLETED:
        raise ValueError("A completed invitation cannot be revoked")
    moment = revoked_at or datetime.now(timezone.utc)
    if moment.tzinfo is None or moment.utcoffset() is None:
        moment = moment.replace(tzinfo=timezone.utc)
    invitation.revoked_at = moment.astimezone(timezone.utc)


def invitation_kind_from_token(token: str | None) -> InvitationKind:
    normalized = str(token or "")
    if normalized.startswith("INV-"):
        return InvitationKind.STANDARD
    if normalized.startswith("ACCT-"):
        return InvitationKind.ACCOUNTANT
    if normalized.startswith("CUST-"):
        return InvitationKind.CUSTOMER
    return InvitationKind.LEGACY_UNKNOWN


def is_post_expiry_reconciliation_allowed(
    invitation: Invitation | object,
    *,
    proof_completed_at: datetime,
    received_at: datetime,
    grace_seconds: int,
) -> bool:
    expires_at = getattr(invitation, "expires_at", None)
    if expires_at is None or getattr(invitation, "revoked_at", None) is not None:
        return False
    expiry = _utc_naive(expires_at)
    proof = _utc_naive(proof_completed_at)
    received = _utc_naive(received_at)
    return proof <= expiry and received <= expiry + timedelta(seconds=max(int(grace_seconds), 0))
