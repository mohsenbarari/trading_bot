"""Canonical rules for the per-user offer overtime preference.

The preference is how long an offer stays open for owner-approved trades after
its normal lifetime ends. Two rules matter more than the rest and are enforced
here rather than at each call site:

Iran is the only writer. The field is declared Iran-authoritative in the user
sync policy, so a foreign write is rejected by the existing authority guard at
the ORM layer. Callers on the foreign server must forward to Iran and report
success only once Iran has persisted the value.

An offer takes its value from the owner's persisted row, never from whatever a
browser or bot happened to send. That is why the snapshot helper reads a User
loaded from the database and there is no way to pass a value in.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from models.customer_relation import CustomerTier
from models.user import User

OVERTIME_MIN_MINUTES = 0
OVERTIME_MAX_MINUTES = 10

#: Shown when the submitted value is not a whole number in range.
INVALID_OVERTIME_VALUE_MESSAGE = "لطفاً فقط یک عدد بین ۰ تا ۱۰ بفرستید."

#: Shown to an account that may not own offers at all.
OVERTIME_NOT_AVAILABLE_MESSAGE = "این تنظیم برای حساب شما در دسترس نیست."


class OfferOvertimePreferenceError(ValueError):
    """Raised when a submitted preference value cannot be accepted."""

    def __init__(self, message: str = INVALID_OVERTIME_VALUE_MESSAGE) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class OvertimePreferenceEligibility:
    """Whether an account may hold an overtime preference, and why not."""

    allowed: bool
    reason: str | None = None


def normalize_overtime_minutes(value: object) -> int:
    """Return a whole number of minutes in range, or raise.

    Booleans are rejected explicitly: ``True`` is an ``int`` in Python and would
    otherwise be silently accepted as one minute.
    """
    if isinstance(value, bool):
        raise OfferOvertimePreferenceError()
    if isinstance(value, int):
        minutes = value
    elif isinstance(value, str):
        stripped = value.strip()
        # Accept Persian and Arabic-Indic digits, which a bot user will type.
        translated = stripped.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))
        if not translated.lstrip("+").isdigit():
            raise OfferOvertimePreferenceError()
        minutes = int(translated)
    else:
        raise OfferOvertimePreferenceError()

    if minutes < OVERTIME_MIN_MINUTES or minutes > OVERTIME_MAX_MINUTES:
        raise OfferOvertimePreferenceError()
    return minutes


def read_persisted_overtime_minutes(owner: User | object | None) -> int:
    """Return the owner's stored preference, clamped to the supported range.

    Clamping rather than raising is deliberate: a row that somehow holds an
    out-of-range value must not be able to block offer creation.
    """
    raw = getattr(owner, "offer_overtime_minutes", None)
    if isinstance(raw, bool) or not isinstance(raw, int):
        return OVERTIME_MIN_MINUTES
    return max(OVERTIME_MIN_MINUTES, min(OVERTIME_MAX_MINUTES, raw))


def snapshot_overtime_minutes_for_new_offer(owner: User | object | None) -> int:
    """Value to freeze onto a newly created offer.

    Takes a ``User`` loaded from the database on purpose. A later change to the
    preference must not move an offer that already exists, and a caller must not
    be able to inject a value the owner never saved. A republished offer goes
    through here too, so it takes the owner's current preference rather than
    inheriting the source offer's snapshot.
    """
    return read_persisted_overtime_minutes(owner)


async def evaluate_overtime_preference_eligibility(
    db: AsyncSession,
    user: User,
) -> OvertimePreferenceEligibility:
    """Who may see and set the preference.

    Only accounts that can own their own offers. Accountants have no market
    access anywhere in the product, and tier-2 customers are refused offer
    creation and may only request against other people's offers, so for both the
    setting would be inert.
    """
    from core.services.accountant_relation_service import is_user_accountant
    from core.services.customer_relation_service import (
        get_active_customer_relation_for_customer,
    )

    user_id = getattr(user, "id", None)
    if not user_id:
        return OvertimePreferenceEligibility(False, "unknown_user")

    if await is_user_accountant(db, user_id):
        return OvertimePreferenceEligibility(False, "accountant_has_no_market_access")

    relation = await get_active_customer_relation_for_customer(db, user_id)
    if relation is not None:
        tier = getattr(relation, "customer_tier", None)
        tier_value = getattr(tier, "value", tier)
        if tier_value == CustomerTier.TIER_2.value:
            return OvertimePreferenceEligibility(False, "tier2_customer_cannot_own_offers")

    return OvertimePreferenceEligibility(True)
