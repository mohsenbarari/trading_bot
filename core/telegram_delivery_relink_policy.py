"""Explicit privacy policy for Telegram route changes after enqueue."""
from __future__ import annotations

from enum import Enum
from types import MappingProxyType

from core.telegram_delivery_queue_contract import TelegramDeliveryAction


class TelegramRelinkBehavior(str, Enum):
    SUPPRESS_ON_RELINK = "suppress_on_relink"
    ROUTE_INDEPENDENT = "route_independent"
    PROVIDER_TARGET_BOUND = "provider_target_bound"
    SCOPED_TARGET_REVALIDATE = "scoped_target_revalidate"
    FORBIDDEN_DURABLE = "forbidden_durable"


_SUPPRESS = {
    TelegramDeliveryAction.OFFER_SUCCESS,
    TelegramDeliveryAction.OFFER_VALIDATION_RESPONSE,
    TelegramDeliveryAction.OFFER_REPEAT_RESPONSE,
    TelegramDeliveryAction.GENERAL_IMMEDIATE,
    TelegramDeliveryAction.TRADE_RESULT,
    TelegramDeliveryAction.TRADE_RESPONSE,
    TelegramDeliveryAction.TRADE_ALTERNATIVE,
    TelegramDeliveryAction.TRADE_UNAVAILABLE,
    TelegramDeliveryAction.TRADE_NONCRITICAL,
    TelegramDeliveryAction.NEW_USER_MEMBERSHIP,
    TelegramDeliveryAction.ACCOUNT_STATUS,
    TelegramDeliveryAction.CHANNEL_MEMBER_BAN,
    TelegramDeliveryAction.CHANNEL_MEMBER_UNBAN,
    TelegramDeliveryAction.TARGETED_ADMIN_MESSAGE,
    TelegramDeliveryAction.ADMIN_BROADCAST,
    TelegramDeliveryAction.GENERAL_ANNOUNCEMENT,
    TelegramDeliveryAction.TIMED_SECURITY,
    TelegramDeliveryAction.DELAYED_RESTRICTION,
}
_ROUTE_INDEPENDENT = {
    TelegramDeliveryAction.OFFER_PUBLISH,
    TelegramDeliveryAction.PARTIAL_OFFER_EDIT,
    TelegramDeliveryAction.TRADED_OFFER_EDIT,
    TelegramDeliveryAction.EXPIRED_OFFER_EDIT,
    TelegramDeliveryAction.CANCELLED_OFFER_EDIT,
    TelegramDeliveryAction.OTHER_ACTIVE_OFFER_EDIT,
    TelegramDeliveryAction.INVALID_ACTION_BUTTON_EDIT,
    TelegramDeliveryAction.RECONCILIATION_EDIT,
    TelegramDeliveryAction.MARKET_TRANSITION,
    TelegramDeliveryAction.MARKET_STATUS_CORRECTION,
    TelegramDeliveryAction.NONCRITICAL_MARKET,
}
_PROVIDER_BOUND = {
    TelegramDeliveryAction.CALLBACK_DEADLINE,
    TelegramDeliveryAction.OFFER_EXPIRY_CALLBACK,
}
_SCOPED_REVALIDATE = {
    TelegramDeliveryAction.TEMPORARY_CLEANUP,
    TelegramDeliveryAction.COSMETIC_CLEANUP,
}

TELEGRAM_DELIVERY_RELINK_POLICIES = MappingProxyType(
    {
        **{
            action: TelegramRelinkBehavior.SUPPRESS_ON_RELINK
            for action in _SUPPRESS
        },
        **{
            action: TelegramRelinkBehavior.ROUTE_INDEPENDENT
            for action in _ROUTE_INDEPENDENT
        },
        **{
            action: TelegramRelinkBehavior.PROVIDER_TARGET_BOUND
            for action in _PROVIDER_BOUND
        },
        **{
            action: TelegramRelinkBehavior.SCOPED_TARGET_REVALIDATE
            for action in _SCOPED_REVALIDATE
        },
        TelegramDeliveryAction.OTP_DEADLINE: TelegramRelinkBehavior.FORBIDDEN_DURABLE,
    }
)


def telegram_delivery_relink_behavior(
    action: TelegramDeliveryAction | str,
) -> TelegramRelinkBehavior:
    normalized = (
        action
        if isinstance(action, TelegramDeliveryAction)
        else TelegramDeliveryAction(str(action or "").strip())
    )
    return TELEGRAM_DELIVERY_RELINK_POLICIES[normalized]
