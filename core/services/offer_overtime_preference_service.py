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

#: Shown when the submitted value is not a whole number in range. Inventory M8.
INVALID_OVERTIME_VALUE_MESSAGE = "لطفاً فقط یک عدد بین ۰ تا ۱۰ بفرستید."

#: Defensive refusal for accounts that cannot own offers. Not yet in the
#: approved inventory; surfaces only if an ineligible account reaches a save
#: path that the UI should already have hidden.
OVERTIME_NOT_AVAILABLE_MESSAGE = "این تنظیم برای حساب شما در دسترس نیست."

#: Inventory M4. ``{minutes}`` is the saved whole number.
SAVE_SUCCESS_NONZERO_MESSAGE = "✅ وقت اضافه لفظ‌های جدید شما روی {minutes} دقیقه تنظیم شد."

#: Inventory M5.
SAVE_SUCCESS_ZERO_MESSAGE = "✅ وقت اضافه برای لفظ‌های جدید شما غیرفعال شد."

#: Inventory M6. Returned with every successful nonzero save.
REACHABILITY_WARNING_MESSAGE = (
    "تأیید هر لفظ فقط در همان محل ثبت لفظ نمایش داده می‌شود: "
    "لفظ وب در وب‌اپ و لفظ بات در بات."
)

#: Inventory M7. Bot save failed because Iran did not persist the value.
BOT_SAVE_UNAVAILABLE_MESSAGE = "تنظیم شما ذخیره نشد. لطفاً کمی بعد دوباره تلاش کنید."


class OfferOvertimePreferenceError(ValueError):
    """Raised when a submitted preference value cannot be accepted."""

    def __init__(self, message: str = INVALID_OVERTIME_VALUE_MESSAGE) -> None:
        super().__init__(message)
        self.message = message


class OfferOvertimePreferenceNotAllowedError(PermissionError):
    """Raised when the account may not hold an overtime preference."""

    def __init__(self, message: str = OVERTIME_NOT_AVAILABLE_MESSAGE) -> None:
        super().__init__(message)
        self.message = message


class OfferOvertimePreferenceTransportError(RuntimeError):
    """Raised when a bot save could not be persisted on Iran."""

    def __init__(self, message: str = BOT_SAVE_UNAVAILABLE_MESSAGE) -> None:
        super().__init__(message)
        self.message = message


@dataclass(frozen=True)
class OvertimePreferenceEligibility:
    """Whether an account may hold an overtime preference, and why not."""

    allowed: bool
    reason: str | None = None


@dataclass(frozen=True)
class OvertimePreferenceSaveResult:
    """Outcome of a successful Iran-authoritative preference write."""

    offer_overtime_minutes: int
    detail: str
    warning: str | None = None


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


def format_overtime_preference_save_messages(minutes: int) -> tuple[str, str | None]:
    """Return the approved success detail and optional reachability warning."""
    if minutes == 0:
        return SAVE_SUCCESS_ZERO_MESSAGE, None
    return (
        SAVE_SUCCESS_NONZERO_MESSAGE.format(minutes=minutes),
        REACHABILITY_WARNING_MESSAGE,
    )


async def persist_overtime_preference(
    db: AsyncSession,
    user: User,
    value: object,
) -> OvertimePreferenceSaveResult:
    """Validate and assign the preference on the Iran-local user row.

    Does not commit. Callers on the foreign server must not use this helper;
    they forward to Iran through ``save_overtime_preference_from_bot`` instead.
    """
    minutes = normalize_overtime_minutes(value)
    eligibility = await evaluate_overtime_preference_eligibility(db, user)
    if not eligibility.allowed:
        raise OfferOvertimePreferenceNotAllowedError()

    user.offer_overtime_minutes = minutes
    detail, warning = format_overtime_preference_save_messages(minutes)
    return OvertimePreferenceSaveResult(
        offer_overtime_minutes=minutes,
        detail=detail,
        warning=warning,
    )


async def save_overtime_preference_from_bot(
    db: AsyncSession,
    user: User,
    value: object,
) -> OvertimePreferenceSaveResult:
    """Persist a bot-origin preference through Iran, never through a local write.

    On the foreign server the value is validated and eligibility-checked locally,
    then sent as a signed internal command. Success is reported only from Iran's
    response. A transport failure raises with the approved unavailable copy and
    leaves the local row untouched so an outage cannot create a false success
    or a divergent foreign write of an Iran-authoritative field.
    """
    from core.server_routing import SERVER_IRAN, current_server

    minutes = normalize_overtime_minutes(value)
    eligibility = await evaluate_overtime_preference_eligibility(db, user)
    if not eligibility.allowed:
        raise OfferOvertimePreferenceNotAllowedError()

    if current_server() == SERVER_IRAN:
        result = await persist_overtime_preference(db, user, minutes)
        await db.commit()
        await db.refresh(user)
        return result

    from core.offer_overtime_preference_transport import (
        forward_offer_overtime_preference_to_iran,
    )

    status_code, body = await forward_offer_overtime_preference_to_iran(
        {
            "user_id": int(user.id),
            "offer_overtime_minutes": minutes,
        }
    )
    if not isinstance(body, dict):
        raise OfferOvertimePreferenceTransportError()

    if status_code < 200 or status_code >= 300:
        remote_detail = body.get("detail")
        if status_code == 400 and isinstance(remote_detail, str) and remote_detail:
            raise OfferOvertimePreferenceError(remote_detail)
        if status_code == 403 and isinstance(remote_detail, str) and remote_detail:
            raise OfferOvertimePreferenceNotAllowedError(remote_detail)
        raise OfferOvertimePreferenceTransportError()

    saved = body.get("offer_overtime_minutes", minutes)
    try:
        saved_minutes = int(saved)
    except (TypeError, ValueError) as exc:
        raise OfferOvertimePreferenceTransportError() from exc

    detail = body.get("detail")
    warning = body.get("warning")
    if not isinstance(detail, str) or not detail:
        detail, derived_warning = format_overtime_preference_save_messages(saved_minutes)
        if warning is None:
            warning = derived_warning
    if warning is not None and not isinstance(warning, str):
        warning = None

    return OvertimePreferenceSaveResult(
        offer_overtime_minutes=saved_minutes,
        detail=detail,
        warning=warning,
    )
