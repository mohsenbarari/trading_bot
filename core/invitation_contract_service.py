"""Pure builder for the additive invitation API contract v2."""

from __future__ import annotations

from dataclasses import dataclass
import re

from core.public_webapp_url import public_webapp_url_for_links
from core.log_redaction import mask_mobile
from core.registration_contracts import (
    InvitationContractV2,
    InvitationDerivedState,
    InvitationSMSStatus,
    PublicInvitationContractV2,
)
from core.services.invitation_lifecycle_service import derive_invitation_state
from core.services.bot_access_policy import evaluate_invitation_bot_access
from models.customer_relation import CustomerTier
from models.invitation import Invitation, InvitationKind


@dataclass(frozen=True)
class InvitationSurfaceAvailability:
    bot: bool
    web: bool


_INVITATION_SHORT_CODE_PATTERN = re.compile(r"^[A-Za-z0-9]{8}$")


def is_canonical_invitation_short_code(short_code: object) -> bool:
    return isinstance(short_code, str) and bool(
        _INVITATION_SHORT_CODE_PATTERN.fullmatch(short_code)
    )


def build_canonical_invitation_web_link(
    short_code: object,
    *,
    settings_obj=None,
    web_origin: str | None = None,
) -> str | None:
    """Build the only active Web invitation URL, failing closed on malformed codes."""

    if not is_canonical_invitation_short_code(short_code):
        return None
    normalized_short_code = short_code
    canonical_origin = web_origin or public_webapp_url_for_links(
        settings_obj=settings_obj
    )
    return f"{canonical_origin.rstrip('/')}/i/{normalized_short_code}"


def invitation_surface_availability(
    kind: InvitationKind | str,
    *,
    role: object,
    customer_tier: CustomerTier | str | None = None,
) -> InvitationSurfaceAvailability:
    kind_value = str(getattr(kind, "value", kind) or "").strip().lower()
    if kind_value == InvitationKind.LEGACY_UNKNOWN.value:
        return InvitationSurfaceAvailability(bot=False, web=False)
    decision = evaluate_invitation_bot_access(
        role=role,
        invitation_kind=kind,
        customer_tier=customer_tier,
    )
    web_available = kind_value in {
        InvitationKind.STANDARD.value,
        InvitationKind.ACCOUNTANT.value,
        InvitationKind.CUSTOMER.value,
    }
    return InvitationSurfaceAvailability(bot=decision.allowed, web=web_available)


def build_invitation_contract_v2(
    invitation: Invitation,
    *,
    bot_username: str | None,
    sms_status: InvitationSMSStatus,
    customer_tier: CustomerTier | str | None = None,
    settings_obj=None,
) -> InvitationContractV2:
    if settings_obj is None:
        from core.config import settings as settings_obj

    state = derive_invitation_state(invitation)
    availability = invitation_surface_availability(
        invitation.kind,
        role=invitation.role,
        customer_tier=customer_tier,
    )
    if state != InvitationDerivedState.PENDING:
        availability = InvitationSurfaceAvailability(bot=False, web=False)
    bot_name = str(bot_username or "").strip().lstrip("@")
    bot_link = (
        f"https://t.me/{bot_name}?start={invitation.token}"
        if availability.bot and bot_name
        else None
    )
    canonical_web_link = (
        build_canonical_invitation_web_link(
            getattr(invitation, "short_code", None),
            settings_obj=settings_obj,
        )
        if availability.web
        else None
    )
    if availability.web and canonical_web_link is None:
        availability = InvitationSurfaceAvailability(bot=availability.bot, web=False)
    web_link = canonical_web_link or ""
    web_short_link = canonical_web_link
    return InvitationContractV2(
        short_code=(invitation.short_code if canonical_web_link else None),
        bot_link=bot_link,
        web_link=web_link,
        web_short_link=web_short_link,
        bot_available=bool(bot_link),
        web_available=availability.web,
        state=state,
        kind=str(getattr(invitation.kind, "value", invitation.kind)),
        expires_at=invitation.expires_at,
        sms_status=sms_status,
        link=bot_link,
        short_link=web_short_link,
    )


def build_public_invitation_contract_v2(
    invitation: Invitation,
    *,
    customer_tier: CustomerTier | str | None = None,
) -> PublicInvitationContractV2:
    state = derive_invitation_state(invitation)
    availability = invitation_surface_availability(
        invitation.kind,
        role=invitation.role,
        customer_tier=customer_tier,
    )
    if state != InvitationDerivedState.PENDING:
        availability = InvitationSurfaceAvailability(bot=False, web=False)
    is_pending = state == InvitationDerivedState.PENDING
    return PublicInvitationContractV2(
        token=(invitation.token if is_pending else None),
        valid=is_pending,
        account_name=(invitation.account_name if is_pending else None),
        mobile_number=(mask_mobile(invitation.mobile_number) if is_pending else None),
        role=(
            str(getattr(invitation.role, "value", invitation.role))
            if is_pending
            else None
        ),
        bot_available=availability.bot,
        web_available=availability.web,
        state=state,
        kind=str(getattr(invitation.kind, "value", invitation.kind)),
        expires_at=invitation.expires_at,
    )
