"""Construction of source-authoritative Telegram queue runtime adapters."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from core.services.telegram_admin_broadcast_queue_feedback import (
    TelegramAdminBroadcastQueueLifecycleFeedback,
)
from core.services.telegram_market_notice_queue_feedback import (
    TelegramMarketNoticeQueueLifecycleFeedback,
)
from core.services.telegram_notification_outbox_queue_feedback import (
    TelegramNotificationOutboxQueueLifecycleFeedback,
)
from core.services.telegram_offer_queue_feedback import (
    TelegramOfferQueueLifecycleFeedback,
)
from core.services.telegram_trade_result_queue_feedback import (
    TradeResultQueueLifecycleFeedback,
)
from core.telegram_delivery_admin_broadcast_freshness import (
    ADMIN_BROADCAST_FRESHNESS_ACTIONS,
    AdminBroadcastTelegramDeliveryFreshnessValidator,
)
from core.telegram_delivery_freshness_router import (
    TelegramDeliveryFreshnessRegistry,
    TelegramDeliveryFreshnessRouter,
    admin_broadcast_freshness_routes,
    market_freshness_routes,
    new_user_membership_freshness_routes,
    notification_action_freshness_routes,
    offer_freshness_routes,
    offer_success_freshness_routes,
    repeat_offer_response_freshness_routes,
    trade_result_freshness_routes,
)
from core.telegram_delivery_notification_action_freshness import (
    NOTIFICATION_ACTION_FRESHNESS_ACTIONS,
    NotificationActionTelegramDeliveryFreshnessValidator,
)
from core.telegram_delivery_offer_success_freshness import (
    OFFER_SUCCESS_FRESHNESS_ACTIONS,
    OfferSuccessTelegramDeliveryFreshnessValidator,
)
from core.telegram_delivery_lifecycle_router import (
    TelegramDeliveryLifecycleRegistry,
    TelegramDeliveryLifecycleRouter,
    lifecycle_routes,
)
from core.telegram_delivery_market_freshness import (
    MARKET_NOTICE_FRESHNESS_ACTIONS,
    MarketTelegramDeliveryFreshnessValidator,
)
from core.telegram_delivery_new_user_membership_freshness import (
    NEW_USER_MEMBERSHIP_FRESHNESS_ACTIONS,
    NewUserMembershipTelegramDeliveryFreshnessValidator,
)
from core.telegram_delivery_offer_freshness import (
    OFFER_FRESHNESS_ACTIONS,
    OfferTelegramDeliveryFreshnessValidator,
)
from core.telegram_delivery_queue_contract import TelegramDeliveryAction
from core.telegram_delivery_repeat_offer_freshness import (
    REPEAT_OFFER_RESPONSE_FRESHNESS_ACTIONS,
    validate_repeat_offer_response_telegram_delivery_freshness,
)
from core.telegram_delivery_trade_freshness import (
    TRADE_RESULT_FRESHNESS_ACTIONS,
    TradeResultTelegramDeliveryFreshnessValidator,
)


class TelegramDeliveryRuntimeCompositionError(RuntimeError):
    """Raised when two source adapters claim one action or configuration is invalid."""


@dataclass(frozen=True, slots=True)
class TelegramDeliveryLaneAdapters:
    freshness: TelegramDeliveryFreshnessRouter
    lifecycle: TelegramDeliveryLifecycleRouter


def _channel_id(value: Any) -> int:
    if isinstance(value, bool):
        raise TelegramDeliveryRuntimeCompositionError(
            "telegram_runtime_channel_id_invalid"
        )
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TelegramDeliveryRuntimeCompositionError(
            "telegram_runtime_channel_id_invalid"
        ) from exc
    if parsed == 0:
        raise TelegramDeliveryRuntimeCompositionError(
            "telegram_runtime_channel_id_invalid"
        )
    return parsed


def _merge_unique(
    target: dict[TelegramDeliveryAction, Any],
    routes: Mapping[TelegramDeliveryAction, Any],
) -> None:
    overlap = set(target).intersection(routes)
    if overlap:
        actions = ",".join(sorted(action.value for action in overlap))
        raise TelegramDeliveryRuntimeCompositionError(
            f"telegram_runtime_action_route_conflict:{actions}"
        )
    target.update(routes)


def configured_telegram_delivery_freshness_registry(
    *,
    channel_id: Any,
) -> TelegramDeliveryFreshnessRegistry:
    expected_channel_id = _channel_id(channel_id)
    routes: dict[TelegramDeliveryAction, Any] = {}
    _merge_unique(
        routes,
        offer_freshness_routes(
            OfferTelegramDeliveryFreshnessValidator(expected_channel_id)
        ),
    )
    _merge_unique(
        routes,
        market_freshness_routes(
            MarketTelegramDeliveryFreshnessValidator(expected_channel_id)
        ),
    )
    _merge_unique(
        routes,
        trade_result_freshness_routes(
            TradeResultTelegramDeliveryFreshnessValidator()
        ),
    )
    _merge_unique(
        routes,
        admin_broadcast_freshness_routes(
            AdminBroadcastTelegramDeliveryFreshnessValidator()
        ),
    )
    _merge_unique(
        routes,
        new_user_membership_freshness_routes(
            NewUserMembershipTelegramDeliveryFreshnessValidator()
        ),
    )
    _merge_unique(
        routes,
        repeat_offer_response_freshness_routes(
            validate_repeat_offer_response_telegram_delivery_freshness
        ),
    )
    _merge_unique(
        routes,
        notification_action_freshness_routes(
            NOTIFICATION_ACTION_FRESHNESS_ACTIONS,
            NotificationActionTelegramDeliveryFreshnessValidator(),
        ),
    )
    _merge_unique(
        routes,
        offer_success_freshness_routes(
            OfferSuccessTelegramDeliveryFreshnessValidator()
        ),
    )
    return TelegramDeliveryFreshnessRegistry(routes)


def configured_telegram_delivery_lifecycle_registry(
    *,
    channel_id: Any,
) -> TelegramDeliveryLifecycleRegistry:
    expected_channel_id = _channel_id(channel_id)
    routes: dict[TelegramDeliveryAction, Any] = {}
    offer_feedback = TelegramOfferQueueLifecycleFeedback()
    market_feedback = TelegramMarketNoticeQueueLifecycleFeedback(
        expected_channel_id=expected_channel_id
    )
    trade_feedback = TradeResultQueueLifecycleFeedback()
    admin_feedback = TelegramAdminBroadcastQueueLifecycleFeedback()
    notification_feedback = TelegramNotificationOutboxQueueLifecycleFeedback()

    for actions, feedback in (
        (OFFER_FRESHNESS_ACTIONS, offer_feedback),
        (MARKET_NOTICE_FRESHNESS_ACTIONS, market_feedback),
        (TRADE_RESULT_FRESHNESS_ACTIONS, trade_feedback),
        (ADMIN_BROADCAST_FRESHNESS_ACTIONS, admin_feedback),
        (NEW_USER_MEMBERSHIP_FRESHNESS_ACTIONS, notification_feedback),
        (REPEAT_OFFER_RESPONSE_FRESHNESS_ACTIONS, notification_feedback),
        (NOTIFICATION_ACTION_FRESHNESS_ACTIONS, notification_feedback),
        (OFFER_SUCCESS_FRESHNESS_ACTIONS, notification_feedback),
    ):
        _merge_unique(routes, lifecycle_routes(actions, feedback))
    return TelegramDeliveryLifecycleRegistry(routes)


def build_configured_telegram_delivery_lane_adapters(
    *,
    channel_id: Any,
    bot_identity: str,
) -> TelegramDeliveryLaneAdapters:
    freshness_registry = configured_telegram_delivery_freshness_registry(
        channel_id=channel_id
    )
    lifecycle_registry = configured_telegram_delivery_lifecycle_registry(
        channel_id=channel_id
    )
    return TelegramDeliveryLaneAdapters(
        freshness=freshness_registry.build_lane_router(bot_identity),
        lifecycle=lifecycle_registry.build_lane_router(bot_identity),
    )
