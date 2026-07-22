import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from core.services.telegram_delivery_queue_service import CHANNEL_EDITOR_ACTIONS
from core.telegram_delivery_freshness_router import DURABLE_TELEGRAM_DELIVERY_ACTIONS
from core.telegram_delivery_lifecycle_router import (
    TelegramDeliveryLifecycleRegistry,
    TelegramDeliveryLifecycleRouter,
    TelegramDeliveryLifecycleRoutingError,
    lifecycle_routes,
)
from core.telegram_delivery_queue_contract import (
    TelegramDeliveryAction,
    TelegramDeliveryDecision,
    TelegramDeliveryOutcome,
    TelegramFreshnessDecision,
    TelegramFreshnessOutcome,
)
from core.utils import utc_now


def feedback():
    return SimpleNamespace(
        assert_dispatchable=AsyncMock(),
        apply_freshness=AsyncMock(),
        apply_delivery_result=AsyncMock(),
    )


class TelegramDeliveryLifecycleRouterTests(unittest.IsolatedAsyncioTestCase):
    def test_editor_registry_requires_exact_editor_actions(self):
        adapter = feedback()
        registry = TelegramDeliveryLifecycleRegistry(
            lifecycle_routes(CHANNEL_EDITOR_ACTIONS, adapter)
        )

        self.assertTrue(registry.coverage("channel_editor").complete)
        self.assertFalse(registry.coverage("primary").complete)
        self.assertEqual(
            set(registry.coverage("primary").missing_actions),
            DURABLE_TELEGRAM_DELIVERY_ACTIONS - CHANNEL_EDITOR_ACTIONS,
        )

    def test_incomplete_primary_registry_refuses_router(self):
        registry = TelegramDeliveryLifecycleRegistry(
            lifecycle_routes(CHANNEL_EDITOR_ACTIONS, feedback())
        )

        with self.assertRaisesRegex(
            TelegramDeliveryLifecycleRoutingError,
            "telegram_lifecycle_lane_incomplete:primary:",
        ):
            registry.build_lane_router("primary")

    async def test_complete_router_dispatches_each_lifecycle_boundary(self):
        adapter = feedback()
        router = TelegramDeliveryLifecycleRegistry(
            lifecycle_routes(CHANNEL_EDITOR_ACTIONS, adapter)
        ).build_lane_router("channel_editor")
        job = SimpleNamespace(
            bot_identity="channel_editor",
            action_kind=TelegramDeliveryAction.PARTIAL_OFFER_EDIT,
        )
        db = object()
        now = utc_now()
        freshness = TelegramFreshnessDecision(TelegramFreshnessOutcome.SEND)
        delivery = TelegramDeliveryDecision(TelegramDeliveryOutcome.SENT)

        await router.assert_dispatchable(db, job, now)
        await router.apply_freshness(db, job, freshness, now)
        await router.apply_delivery_result(db, job, delivery, now)

        adapter.assert_dispatchable.assert_awaited_once_with(db, job, now)
        adapter.apply_freshness.assert_awaited_once_with(db, job, freshness, now)
        adapter.apply_delivery_result.assert_awaited_once_with(
            db, job, delivery, now
        )

    async def test_cross_lane_job_and_unknown_action_fail_closed(self):
        router = TelegramDeliveryLifecycleRegistry(
            lifecycle_routes(CHANNEL_EDITOR_ACTIONS, feedback())
        ).build_lane_router("channel_editor")

        with self.assertRaisesRegex(
            TelegramDeliveryLifecycleRoutingError,
            "telegram_lifecycle_job_lane_mismatch",
        ):
            await router.assert_dispatchable(
                object(),
                SimpleNamespace(
                    bot_identity="primary",
                    action_kind=TelegramDeliveryAction.PARTIAL_OFFER_EDIT,
                ),
                utc_now(),
            )
        with self.assertRaisesRegex(
            TelegramDeliveryLifecycleRoutingError,
            "telegram_lifecycle_action_invalid",
        ):
            await router.assert_dispatchable(
                object(),
                SimpleNamespace(
                    bot_identity="channel_editor",
                    action_kind="future_action",
                ),
                utc_now(),
            )

    def test_direct_construction_and_malformed_feedback_are_rejected(self):
        with self.assertRaisesRegex(
            TelegramDeliveryLifecycleRoutingError,
            "direct_construction_forbidden",
        ):
            TelegramDeliveryLifecycleRouter(
                bot_identity="channel_editor",
                routes={},
            )
        with self.assertRaisesRegex(
            TelegramDeliveryLifecycleRoutingError,
            "feedback_invalid",
        ):
            TelegramDeliveryLifecycleRegistry(
                {TelegramDeliveryAction.OFFER_PUBLISH: object()}
            )


if __name__ == "__main__":
    unittest.main()
