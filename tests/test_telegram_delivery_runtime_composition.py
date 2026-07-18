import unittest
from types import SimpleNamespace

from core.telegram_delivery_freshness_router import (
    TelegramDeliveryFreshnessRoutingError,
)
from core.telegram_delivery_lifecycle_router import (
    TelegramDeliveryLifecycleRoutingError,
)
from core.telegram_delivery_runtime_composition import (
    TelegramDeliveryRuntimeCompositionError,
    build_configured_telegram_delivery_lane_adapters,
    build_configured_telegram_delivery_runtime,
    configured_telegram_delivery_freshness_registry,
    configured_telegram_delivery_lifecycle_registry,
)


class TelegramDeliveryRuntimeCompositionTests(unittest.TestCase):
    def test_full_runtime_composition_binds_only_credential_enabled_lanes(self):
        runtime = build_configured_telegram_delivery_runtime(
            settings=SimpleNamespace(
                bot_token="primary:test-token",
                telegram_delivery_queue_channel_editor_enabled=True,
                telegram_delivery_queue_channel_editor_bot_token="editor:test-token",
                channel_id=-1001234567890,
            )
        )

        self.assertEqual(runtime.bot_identities, ("primary", "channel_editor"))
        self.assertEqual(set(runtime.freshness_validators), set(runtime.bot_identities))
        self.assertEqual(set(runtime.lifecycle_feedbacks), set(runtime.bot_identities))
        self.assertEqual(
            runtime.credential_registry.bot_identities,
            runtime.bot_identities,
        )

    def test_current_editor_lane_has_complete_source_authoritative_adapters(self):
        adapters = build_configured_telegram_delivery_lane_adapters(
            channel_id=-1001234567890,
            bot_identity="channel_editor",
        )

        self.assertEqual(adapters.freshness.bot_identity, "channel_editor")
        self.assertEqual(adapters.lifecycle.bot_identity, "channel_editor")

    def test_primary_freshness_and_lifecycle_are_complete(self):
        freshness = configured_telegram_delivery_freshness_registry(
            channel_id=-1001234567890
        ).coverage("primary")
        lifecycle = configured_telegram_delivery_lifecycle_registry(
            channel_id=-1001234567890
        ).coverage("primary")

        self.assertTrue(freshness.complete)
        self.assertTrue(lifecycle.complete)
        self.assertEqual(freshness.missing_actions, lifecycle.missing_actions)

    def test_primary_lane_builds_only_after_complete_source_coverage(self):
        adapters = build_configured_telegram_delivery_lane_adapters(
            channel_id=-1001234567890,
            bot_identity="primary",
        )
        self.assertEqual(adapters.freshness.bot_identity, "primary")
        self.assertEqual(adapters.lifecycle.bot_identity, "primary")

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
