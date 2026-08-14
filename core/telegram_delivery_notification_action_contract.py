"""Strict contracts for private notifications backed by the durable outbox.

Only actions listed here may use the generic ``queue_action:*`` source shape.
Interactive callbacks, Offer preview edits, channel messages, and timed
cleanup require dedicated source contracts even when they reuse the durable
notification outbox storage.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramFeederKind,
)


TELEGRAM_NOTIFICATION_ACTION_SOURCE_PREFIX = "queue_action:"


@dataclass(frozen=True, slots=True)
class TelegramNotificationActionPolicy:
    action: TelegramDeliveryAction
    feeder: TelegramFeederKind
    template_version: str
    require_bot_access: bool = True
    state_contract: str = "user_route"

    @property
    def source_type(self) -> str:
        return f"{TELEGRAM_NOTIFICATION_ACTION_SOURCE_PREFIX}{self.action.value}"


_POLICIES = (
    TelegramNotificationActionPolicy(
        TelegramDeliveryAction.OFFER_VALIDATION_RESPONSE,
        TelegramFeederKind.OFFER_CONTROL,
        "offer-validation-response-v1",
    ),
    TelegramNotificationActionPolicy(
        TelegramDeliveryAction.TRADE_RESPONSE,
        TelegramFeederKind.TRADE,
        "trade-response-v1",
    ),
    TelegramNotificationActionPolicy(
        TelegramDeliveryAction.TRADE_ALTERNATIVE,
        TelegramFeederKind.TRADE,
        "trade-alternative-v1",
    ),
    TelegramNotificationActionPolicy(
        TelegramDeliveryAction.TRADE_UNAVAILABLE,
        TelegramFeederKind.TRADE,
        "trade-unavailable-v1",
    ),
    TelegramNotificationActionPolicy(
        TelegramDeliveryAction.TRADE_NONCRITICAL,
        TelegramFeederKind.TRADE,
        "trade-noncritical-v1",
    ),
    TelegramNotificationActionPolicy(
        TelegramDeliveryAction.ACCOUNT_STATUS,
        TelegramFeederKind.ADMIN_SYSTEM,
        "account-status-v1",
        # Deactivation and grace-expiry notices must still reach the linked
        # user after market access has been disabled.
        require_bot_access=False,
        state_contract="account_status",
    ),
    TelegramNotificationActionPolicy(
        TelegramDeliveryAction.TARGETED_ADMIN_MESSAGE,
        TelegramFeederKind.ADMIN_SYSTEM,
        "targeted-admin-message-v1",
    ),
    TelegramNotificationActionPolicy(
        TelegramDeliveryAction.GENERAL_ANNOUNCEMENT,
        TelegramFeederKind.ADMIN_SYSTEM,
        "general-announcement-v1",
    ),
    TelegramNotificationActionPolicy(
        TelegramDeliveryAction.GENERAL_IMMEDIATE,
        TelegramFeederKind.DIRECT,
        "general-immediate-v1",
    ),
    TelegramNotificationActionPolicy(
        TelegramDeliveryAction.TIMED_SECURITY,
        TelegramFeederKind.TIMED_BOT,
        "timed-security-v1",
        # A grace-expiry security notice must remain deliverable after the
        # account lock it describes has taken effect.
        require_bot_access=False,
        state_contract="account_status",
    ),
    TelegramNotificationActionPolicy(
        TelegramDeliveryAction.DELAYED_RESTRICTION,
        TelegramFeederKind.TIMED_BOT,
        "delayed-restriction-v1",
        state_contract="restriction_clear",
    ),
)

TELEGRAM_NOTIFICATION_ACTION_POLICIES = MappingProxyType(
    {policy.action: policy for policy in _POLICIES}
)
TELEGRAM_NOTIFICATION_ACTION_POLICIES_BY_SOURCE = MappingProxyType(
    {policy.source_type: policy for policy in _POLICIES}
)
TELEGRAM_NOTIFICATION_ACTIONS = frozenset(
    TELEGRAM_NOTIFICATION_ACTION_POLICIES
)
TELEGRAM_NOTIFICATION_ACTION_VALUES = frozenset(
    action.value for action in TELEGRAM_NOTIFICATION_ACTIONS
)
TELEGRAM_NOTIFICATION_ACTION_SOURCE_TYPES = frozenset(
    TELEGRAM_NOTIFICATION_ACTION_POLICIES_BY_SOURCE
)


def _action_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def telegram_notification_action_policy(
    action: TelegramDeliveryAction | str,
) -> TelegramNotificationActionPolicy:
    try:
        normalized = TelegramDeliveryAction(_action_value(action))
    except ValueError as exc:
        raise ValueError("telegram_notification_action_invalid") from exc
    policy = TELEGRAM_NOTIFICATION_ACTION_POLICIES.get(normalized)
    if policy is None:
        raise ValueError("telegram_notification_action_unsupported")
    return policy


def telegram_notification_action_policy_from_source(
    source_type: Any,
) -> TelegramNotificationActionPolicy:
    policy = TELEGRAM_NOTIFICATION_ACTION_POLICIES_BY_SOURCE.get(
        str(source_type or "").strip()
    )
    if policy is None:
        raise ValueError("telegram_notification_action_source_unsupported")
    return policy
