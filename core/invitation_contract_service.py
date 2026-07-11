"""Pure builder for the additive invitation API contract v2."""

from __future__ import annotations

from dataclasses import dataclass

from core.public_webapp_url import public_webapp_url_for_links
from core.registration_contracts import InvitationContractV2, InvitationSMSStatus
from core.services.invitation_lifecycle_service import derive_invitation_state
from models.customer_relation import CustomerTier
from models.invitation import Invitation, InvitationKind


@dataclass(frozen=True)
class InvitationSurfaceAvailability:
    bot: bool
    web: bool


def invitation_surface_availability(
    kind: InvitationKind | str,
    *,
    customer_tier: CustomerTier | str | None = None,
) -> InvitationSurfaceAvailability:
    kind_value = str(getattr(kind, "value", kind) or "").strip().lower()
    tier_value = str(getattr(customer_tier, "value", customer_tier) or "").strip().lower()
    if kind_value == InvitationKind.LEGACY_UNKNOWN.value:
        return InvitationSurfaceAvailability(bot=False, web=False)
    if kind_value == InvitationKind.ACCOUNTANT.value:
        return InvitationSurfaceAvailability(bot=False, web=True)
    if kind_value == InvitationKind.CUSTOMER.value and tier_value == CustomerTier.TIER_2.value:
        return InvitationSurfaceAvailability(bot=False, web=True)
    if kind_value in {InvitationKind.STANDARD.value, InvitationKind.CUSTOMER.value}:
        return InvitationSurfaceAvailability(bot=True, web=True)
    return InvitationSurfaceAvailability(bot=False, web=False)


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

    web_origin = public_webapp_url_for_links(settings_obj=settings_obj)
    availability = invitation_surface_availability(
        invitation.kind,
        customer_tier=customer_tier,
    )
    bot_name = str(bot_username or "").strip().lstrip("@")
    bot_link = (
        f"https://t.me/{bot_name}?start={invitation.token}"
        if availability.bot and bot_name
        else None
    )
    web_link = f"{web_origin}/register?token={invitation.token}"
    web_short_link = (
        f"{web_origin}/i/{invitation.short_code}"
        if invitation.short_code
        else None
    )
    return InvitationContractV2(
        token=invitation.token,
        bot_link=bot_link,
        web_link=web_link,
        web_short_link=web_short_link,
        bot_available=bool(bot_link),
        web_available=availability.web,
        state=derive_invitation_state(invitation),
        kind=str(getattr(invitation.kind, "value", invitation.kind)),
        expires_at=invitation.expires_at,
        sms_status=sms_status,
        link=bot_link,
        short_link=web_short_link,
    )
