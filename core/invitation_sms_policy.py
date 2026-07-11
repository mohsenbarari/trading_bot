"""Category-specific invitation SMS policy; OTP fallback is intentionally separate."""

from __future__ import annotations

from core.config import settings
from core.registration_contracts import InvitationSMSStatus
from models.customer_relation import CustomerTier
from models.invitation import InvitationKind


def invitation_sms_enabled(
    kind: InvitationKind | str,
    *,
    customer_tier: CustomerTier | str | None = None,
    settings_obj=settings,
) -> bool:
    kind_value = str(getattr(kind, "value", kind) or "").strip().lower()
    if kind_value == InvitationKind.STANDARD.value:
        return bool(settings_obj.invitation_sms_standard_enabled)
    if kind_value == InvitationKind.ACCOUNTANT.value:
        return bool(settings_obj.invitation_sms_accountant_enabled)
    if kind_value == InvitationKind.CUSTOMER.value:
        tier_value = str(getattr(customer_tier, "value", customer_tier) or "").strip().lower()
        if tier_value == CustomerTier.TIER_1.value:
            return bool(settings_obj.invitation_sms_customer_tier1_enabled)
        if tier_value == CustomerTier.TIER_2.value:
            return bool(settings_obj.invitation_sms_customer_tier2_enabled)
    return False


def invitation_sms_status(*, enabled: bool, accepted: bool | None) -> InvitationSMSStatus:
    if not enabled:
        return InvitationSMSStatus.DISABLED
    return InvitationSMSStatus.ACCEPTED if accepted else InvitationSMSStatus.FAILED
