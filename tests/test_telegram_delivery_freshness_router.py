import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core.services.telegram_delivery_queue_service import (
    CHANNEL_EDITOR_ACTIONS,
)
from core.telegram_delivery_freshness_router import (
    DURABLE_TELEGRAM_DELIVERY_ACTIONS,
    TelegramDeliveryFreshnessRegistry,
    TelegramDeliveryFreshnessRouter,
    TelegramDeliveryFreshnessRoutingError,
    admin_broadcast_freshness_routes,
    market_freshness_routes,
    new_user_membership_freshness_routes,
    offer_freshness_routes,
    repeat_offer_response_freshness_routes,
    required_freshness_actions_for_lane,
    trade_result_freshness_routes,
)
from core.telegram_delivery_market_freshness import MARKET_NOTICE_FRESHNESS_ACTIONS
from core.telegram_delivery_offer_freshness import OFFER_FRESHNESS_ACTIONS
from core.telegram_delivery_trade_freshness import TRADE_RESULT_FRESHNESS_ACTIONS
from core.telegram_delivery_admin_broadcast_freshness import (
    ADMIN_BROADCAST_FRESHNESS_ACTIONS,
)
from core.telegram_delivery_new_user_membership_freshness import (
    NEW_USER_MEMBERSHIP_FRESHNESS_ACTIONS,
)
from core.telegram_delivery_repeat_offer_freshness import (
    REPEAT_OFFER_RESPONSE_FRESHNESS_ACTIONS,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramFreshnessDecision,
    TelegramFreshnessOutcome,
)


NOW = datetime(2026, 7, 17, 5, 15, tzinfo=timezone.utc)


def make_job(action, *, bot_identity="primary"):
    return SimpleNamespace(
        action_kind=action,
        bot_identity=bot_identity,
    )


def send_validator():
    return AsyncMock(
        return_value=TelegramFreshnessDecision(
            TelegramFreshnessOutcome.SEND,
            reason="current",
        )
    )


class TelegramDeliveryFreshnessRouterTests(unittest.IsolatedAsyncioTestCase):
    def test_lane_requirements_exclude_otp_and_bound_editor_scope(self):
        primary = required_freshness_actions_for_lane("primary")
        editor = required_freshness_actions_for_lane("channel_editor")

        self.assertEqual(primary, DURABLE_TELEGRAM_DELIVERY_ACTIONS)
        self.assertNotIn(TelegramDeliveryAction.OTP_DEADLINE, primary)
        self.assertEqual(editor, CHANNEL_EDITOR_ACTIONS)
        self.assertNotIn(TelegramDeliveryAction.OFFER_PUBLISH, editor)

    def test_offer_registry_reports_exact_remaining_lane_coverage(self):
        validator = send_validator()
        registry = TelegramDeliveryFreshnessRegistry(
            offer_freshness_routes(validator)
        )

        primary = registry.coverage("primary")
        editor = registry.coverage("channel_editor")

        self.assertFalse(primary.complete)
        self.assertEqual(
            set(primary.configured_actions),
            OFFER_FRESHNESS_ACTIONS,
        )
        self.assertEqual(
            set(primary.missing_actions),
            DURABLE_TELEGRAM_DELIVERY_ACTIONS - OFFER_FRESHNESS_ACTIONS,
        )
        self.assertTrue(editor.complete)
        self.assertEqual(editor.missing_actions, ())

    def test_offer_and_market_routes_report_remaining_primary_coverage(self):
        validator = send_validator()
        routes = offer_freshness_routes(validator)
        routes.update(market_freshness_routes(validator))
        registry = TelegramDeliveryFreshnessRegistry(routes)

        primary = registry.coverage("primary")

        self.assertEqual(
            set(primary.configured_actions),
            OFFER_FRESHNESS_ACTIONS | MARKET_NOTICE_FRESHNESS_ACTIONS,
        )
        self.assertEqual(
            set(primary.missing_actions),
            DURABLE_TELEGRAM_DELIVERY_ACTIONS
            - OFFER_FRESHNESS_ACTIONS
            - MARKET_NOTICE_FRESHNESS_ACTIONS,
        )

    def test_trade_result_route_reduces_primary_gap_without_covering_other_trade_actions(self):
        validator = send_validator()
        routes = offer_freshness_routes(validator)
        routes.update(market_freshness_routes(validator))
        routes.update(trade_result_freshness_routes(validator))
        registry = TelegramDeliveryFreshnessRegistry(routes)

        primary = registry.coverage("primary")

        self.assertEqual(
            set(primary.configured_actions),
            OFFER_FRESHNESS_ACTIONS
            | MARKET_NOTICE_FRESHNESS_ACTIONS
            | TRADE_RESULT_FRESHNESS_ACTIONS,
        )
        for action in (
            TelegramDeliveryAction.TRADE_RESPONSE,
            TelegramDeliveryAction.TRADE_ALTERNATIVE,
            TelegramDeliveryAction.TRADE_UNAVAILABLE,
            TelegramDeliveryAction.TRADE_NONCRITICAL,
        ):
            self.assertIn(action, primary.missing_actions)

    def test_admin_broadcast_route_covers_only_durable_broadcast_action(self):
        validator = send_validator()
        routes = offer_freshness_routes(validator)
        routes.update(market_freshness_routes(validator))
        routes.update(trade_result_freshness_routes(validator))
        routes.update(admin_broadcast_freshness_routes(validator))

        primary = TelegramDeliveryFreshnessRegistry(routes).coverage("primary")

        self.assertEqual(
            set(primary.configured_actions),
            OFFER_FRESHNESS_ACTIONS
            | MARKET_NOTICE_FRESHNESS_ACTIONS
            | TRADE_RESULT_FRESHNESS_ACTIONS
            | ADMIN_BROADCAST_FRESHNESS_ACTIONS,
        )
        self.assertNotIn(TelegramDeliveryAction.ADMIN_BROADCAST, primary.missing_actions)
        self.assertIn(TelegramDeliveryAction.TARGETED_ADMIN_MESSAGE, primary.missing_actions)

    def test_new_user_membership_route_covers_only_membership_action(self):
        validator = send_validator()
        routes = offer_freshness_routes(validator)
        routes.update(market_freshness_routes(validator))
        routes.update(trade_result_freshness_routes(validator))
        routes.update(admin_broadcast_freshness_routes(validator))
        routes.update(new_user_membership_freshness_routes(validator))

        primary = TelegramDeliveryFreshnessRegistry(routes).coverage("primary")

        self.assertEqual(
            set(primary.configured_actions),
            OFFER_FRESHNESS_ACTIONS
            | MARKET_NOTICE_FRESHNESS_ACTIONS
            | TRADE_RESULT_FRESHNESS_ACTIONS
            | ADMIN_BROADCAST_FRESHNESS_ACTIONS
            | NEW_USER_MEMBERSHIP_FRESHNESS_ACTIONS,
        )
        self.assertNotIn(
            TelegramDeliveryAction.NEW_USER_MEMBERSHIP,
            primary.missing_actions,
        )
        self.assertIn(
            TelegramDeliveryAction.ACCOUNT_STATUS,
            primary.missing_actions,
        )

    def test_repeat_offer_response_route_covers_only_repeat_response_action(self):
        validator = send_validator()
        routes = repeat_offer_response_freshness_routes(validator)

        primary = TelegramDeliveryFreshnessRegistry(routes).coverage("primary")

        self.assertEqual(
            set(primary.configured_actions),
            REPEAT_OFFER_RESPONSE_FRESHNESS_ACTIONS,
        )
        self.assertNotIn(
            TelegramDeliveryAction.OFFER_REPEAT_RESPONSE,
            primary.missing_actions,
        )
        self.assertIn(
            TelegramDeliveryAction.OFFER_VALIDATION_RESPONSE,
            primary.missing_actions,
        )

    def test_incomplete_primary_lane_refuses_router_construction(self):
        registry = TelegramDeliveryFreshnessRegistry(
            offer_freshness_routes(send_validator())
        )

        with self.assertRaisesRegex(
            TelegramDeliveryFreshnessRoutingError,
            "telegram_freshness_lane_incomplete:primary:",
        ):
            registry.build_lane_router("primary")

    async def test_complete_editor_router_dispatches_only_matching_lane(self):
        validator = send_validator()
        registry = TelegramDeliveryFreshnessRegistry(
            {action: validator for action in CHANNEL_EDITOR_ACTIONS}
        )
        router = registry.build_lane_router("channel_editor")
        job = make_job(
            TelegramDeliveryAction.PARTIAL_OFFER_EDIT,
            bot_identity="channel_editor",
        )
        db = object()

        result = await router(db, job, NOW)

        self.assertEqual(result.outcome, TelegramFreshnessOutcome.SEND)
        validator.assert_awaited_once_with(db, job, NOW)
        with self.assertRaisesRegex(
            TelegramDeliveryFreshnessRoutingError,
            "telegram_freshness_job_lane_mismatch",
        ):
            await router(
                object(),
                make_job(
                    TelegramDeliveryAction.PARTIAL_OFFER_EDIT,
                    bot_identity="primary",
                ),
                NOW,
            )

    async def test_complete_primary_registry_builds_and_routes_every_durable_action(self):
        validator = send_validator()
        registry = TelegramDeliveryFreshnessRegistry(
            {action: validator for action in DURABLE_TELEGRAM_DELIVERY_ACTIONS}
        )
        router = registry.build_lane_router("primary")

        self.assertIsInstance(router, TelegramDeliveryFreshnessRouter)
        self.assertTrue(registry.coverage("primary").complete)
        result = await router(
            object(),
            make_job(TelegramDeliveryAction.MARKET_TRANSITION),
            NOW,
        )
        self.assertEqual(result.outcome, TelegramFreshnessOutcome.SEND)

    async def test_router_rejects_unknown_action_and_invalid_decision(self):
        bad_validator = AsyncMock(return_value=SimpleNamespace(outcome="send"))
        good_validator = send_validator()
        routes = {
            action: good_validator
            for action in DURABLE_TELEGRAM_DELIVERY_ACTIONS
        }
        routes[TelegramDeliveryAction.GENERAL_IMMEDIATE] = bad_validator
        router = TelegramDeliveryFreshnessRegistry(routes).build_lane_router("primary")

        with self.assertRaisesRegex(
            TelegramDeliveryFreshnessRoutingError,
            "telegram_freshness_action_invalid",
        ):
            await router(object(), make_job("future_action"), NOW)
        with self.assertRaisesRegex(
            TelegramDeliveryFreshnessRoutingError,
            "telegram_freshness_decision_invalid:general_immediate",
        ):
            await router(
                object(),
                make_job(TelegramDeliveryAction.GENERAL_IMMEDIATE),
                NOW,
            )

    def test_non_durable_and_direct_router_construction_are_rejected(self):
        validator = send_validator()
        with self.assertRaisesRegex(
            TelegramDeliveryFreshnessRoutingError,
            "telegram_freshness_non_durable_action_forbidden",
        ):
            TelegramDeliveryFreshnessRegistry(
                {TelegramDeliveryAction.OTP_DEADLINE: validator}
            )
        with self.assertRaisesRegex(
            TelegramDeliveryFreshnessRoutingError,
            "telegram_freshness_router_direct_construction_forbidden",
        ):
            TelegramDeliveryFreshnessRouter(
                bot_identity="channel_editor",
                routes={TelegramDeliveryAction.OFFER_PUBLISH: validator},
            )

    def test_invalid_identity_and_validator_fail_closed(self):
        with self.assertRaisesRegex(
            TelegramDeliveryFreshnessRoutingError,
            "telegram_freshness_bot_identity_invalid",
        ):
            required_freshness_actions_for_lane("unknown")
        with patch(
            "core.telegram_delivery_freshness_router."
            "SUPPORTED_TELEGRAM_BOT_IDENTITIES",
            {"primary", "channel_editor", "future"},
        ), self.assertRaisesRegex(
            TelegramDeliveryFreshnessRoutingError,
            "telegram_freshness_bot_identity_unsupported",
        ):
            required_freshness_actions_for_lane("future")
        with self.assertRaisesRegex(
            TelegramDeliveryFreshnessRoutingError,
            "telegram_freshness_validator_invalid:general_immediate",
        ):
            TelegramDeliveryFreshnessRegistry(
                {TelegramDeliveryAction.GENERAL_IMMEDIATE: None}
            )
        with self.assertRaisesRegex(
            TelegramDeliveryFreshnessRoutingError,
            "telegram_trade_result_freshness_validator_invalid",
        ):
            trade_result_freshness_routes(None)
        with self.assertRaisesRegex(
            TelegramDeliveryFreshnessRoutingError,
            "telegram_market_freshness_validator_invalid",
        ):
            market_freshness_routes(None)


if __name__ == "__main__":
    unittest.main()
