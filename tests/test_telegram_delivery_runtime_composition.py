import unittest

from core.telegram_delivery_freshness_router import (
    TelegramDeliveryFreshnessRoutingError,
)
from core.telegram_delivery_lifecycle_router import (
    TelegramDeliveryLifecycleRoutingError,
)
from core.telegram_delivery_runtime_composition import (
    TelegramDeliveryRuntimeCompositionError,
    build_configured_telegram_delivery_lane_adapters,
    configured_telegram_delivery_freshness_registry,
    configured_telegram_delivery_lifecycle_registry,
)


class TelegramDeliveryRuntimeCompositionTests(unittest.TestCase):
    def test_current_editor_lane_has_complete_source_authoritative_adapters(self):
        adapters = build_configured_telegram_delivery_lane_adapters(
            channel_id=-1001234567890,
            bot_identity="channel_editor",
        )

        self.assertEqual(adapters.freshness.bot_identity, "channel_editor")
        self.assertEqual(adapters.lifecycle.bot_identity, "channel_editor")

    def test_primary_freshness_and_lifecycle_report_the_same_open_actions(self):
        freshness = configured_telegram_delivery_freshness_registry(
            channel_id=-1001234567890
        ).coverage("primary")
        lifecycle = configured_telegram_delivery_lifecycle_registry(
            channel_id=-1001234567890
        ).coverage("primary")

        self.assertFalse(freshness.complete)
        self.assertFalse(lifecycle.complete)
        self.assertEqual(freshness.missing_actions, lifecycle.missing_actions)

    def test_primary_lane_cannot_build_while_any_source_family_is_open(self):
        with self.assertRaises(TelegramDeliveryFreshnessRoutingError):
            build_configured_telegram_delivery_lane_adapters(
                channel_id=-1001234567890,
                bot_identity="primary",
            )

    def test_bad_channel_and_unknown_lane_fail_before_runtime(self):
        with self.assertRaisesRegex(
            TelegramDeliveryRuntimeCompositionError,
            "channel_id_invalid",
        ):
            configured_telegram_delivery_freshness_registry(channel_id=0)
        with self.assertRaises(
            (TelegramDeliveryFreshnessRoutingError, TelegramDeliveryLifecycleRoutingError)
        ):
            build_configured_telegram_delivery_lane_adapters(
                channel_id=-1001234567890,
                bot_identity="future_lane",
            )


if __name__ == "__main__":
    unittest.main()
