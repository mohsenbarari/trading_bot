import unittest
from unittest.mock import patch

from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeConfigurationError,
    TelegramDeliveryRuntimeMode,
    TelegramProviderAuthorityError,
    assert_telegram_provider_execution_authority,
    configured_telegram_delivery_runtime,
    resolve_telegram_delivery_runtime,
)


class TelegramDeliveryRuntimePolicyTests(unittest.TestCase):
    def test_default_configuration_preserves_legacy_ownership(self):
        with patch("core.telegram_delivery_runtime_policy.settings") as configured:
            configured.telegram_delivery_execution_owner = "legacy"
            configured.telegram_delivery_queue_worker_enabled = False
            configured.telegram_delivery_queue_cutover_ready = False
            decision = configured_telegram_delivery_runtime()

        self.assertEqual(decision.mode, TelegramDeliveryRuntimeMode.LEGACY)
        self.assertTrue(decision.legacy_workers_enabled)
        self.assertFalse(decision.queue_worker_enabled)

    def test_legacy_owner_rejects_any_queue_enablement(self):
        for enabled, cutover_ready in ((True, False), (False, True), (True, True)):
            with self.subTest(enabled=enabled, cutover_ready=cutover_ready):
                with self.assertRaisesRegex(
                    TelegramDeliveryRuntimeConfigurationError,
                    "legacy_owner_rejects_queue_enablement",
                ):
                    resolve_telegram_delivery_runtime(
                        execution_owner="legacy",
                        queue_worker_enabled=enabled,
                        cutover_ready=cutover_ready,
                    )

    def test_queue_owner_requires_both_environment_guards(self):
        cases = (
            (False, False, "queue_owner_requires_worker_enabled"),
            (False, True, "queue_owner_requires_worker_enabled"),
            (True, False, "queue_owner_requires_cutover_ready"),
        )
        for enabled, cutover_ready, reason in cases:
            with self.subTest(enabled=enabled, cutover_ready=cutover_ready):
                with self.assertRaisesRegex(TelegramDeliveryRuntimeConfigurationError, reason):
                    resolve_telegram_delivery_runtime(
                        execution_owner="queue-v1",
                        queue_worker_enabled=enabled,
                        cutover_ready=cutover_ready,
                    )

    def test_queue_activation_requires_the_reviewed_code_capability(self):
        with self.assertRaisesRegex(
            TelegramDeliveryRuntimeConfigurationError,
            "queue_implementation_not_cutover_ready",
        ):
            resolve_telegram_delivery_runtime(
                execution_owner="queue-v1",
                queue_worker_enabled=True,
                cutover_ready=True,
                implementation_ready=False,
            )

    def test_queue_mode_is_reachable_only_with_explicit_code_capability(self):
        decision = resolve_telegram_delivery_runtime(
            execution_owner="queue-v1",
            queue_worker_enabled=True,
            cutover_ready=True,
            implementation_ready=True,
        )

        self.assertEqual(decision.mode, TelegramDeliveryRuntimeMode.QUEUE_V1)
        self.assertFalse(decision.legacy_workers_enabled)
        self.assertTrue(decision.queue_worker_enabled)

    def test_producer_only_disables_both_executors(self):
        decision = resolve_telegram_delivery_runtime(
            execution_owner="producer-only",
            queue_worker_enabled=False,
            cutover_ready=False,
        )
        self.assertEqual(decision.mode, TelegramDeliveryRuntimeMode.PRODUCER_ONLY)
        self.assertFalse(decision.legacy_workers_enabled)
        self.assertFalse(decision.queue_worker_enabled)
        with self.assertRaisesRegex(
            TelegramDeliveryRuntimeConfigurationError,
            "producer_only_rejects_execution_enablement",
        ):
            resolve_telegram_delivery_runtime(
                execution_owner="producer-only",
                queue_worker_enabled=True,
                cutover_ready=False,
            )

    def test_queue_v1_api_cannot_call_provider(self):
        with patch("core.telegram_delivery_runtime_policy.settings") as configured, patch(
            "core.telegram_delivery_runtime_policy.current_server",
            return_value="foreign",
        ):
            configured.telegram_provider_test_authority = False
            configured.trading_bot_service = "api"
            configured.telegram_delivery_producer_mode = "queue-v1"
            configured.telegram_delivery_execution_owner = "producer-only"
            configured.telegram_delivery_queue_worker_enabled = False
            configured.telegram_delivery_queue_cutover_ready = False
            with self.assertRaisesRegex(
                TelegramProviderAuthorityError,
                "producer_only_forbidden_provider_execution",
            ):
                assert_telegram_provider_execution_authority()

    def test_queue_v1_bot_can_call_provider(self):
        with patch("core.telegram_delivery_runtime_policy.settings") as configured, patch(
            "core.telegram_delivery_runtime_policy.current_server",
            return_value="foreign",
        ):
            configured.telegram_provider_test_authority = False
            configured.trading_bot_service = "bot"
            configured.telegram_delivery_producer_mode = "queue-v1"
            configured.telegram_delivery_execution_owner = "queue-v1"
            configured.telegram_delivery_queue_worker_enabled = True
            configured.telegram_delivery_queue_cutover_ready = True
            assert_telegram_provider_execution_authority()

    def test_iran_never_has_provider_authority(self):
        with patch("core.telegram_delivery_runtime_policy.settings") as configured, patch(
            "core.telegram_delivery_runtime_policy.current_server",
            return_value="iran",
        ):
            configured.telegram_provider_test_authority = False
            configured.trading_bot_service = "bot"
            configured.telegram_delivery_producer_mode = "queue-v1"
            configured.telegram_delivery_execution_owner = "queue-v1"
            configured.telegram_delivery_queue_worker_enabled = True
            configured.telegram_delivery_queue_cutover_ready = True
            with self.assertRaisesRegex(
                TelegramProviderAuthorityError,
                "telegram_provider_forbidden_outside_foreign",
            ):
                assert_telegram_provider_execution_authority()

    def test_legacy_foreign_api_keeps_rollback_authority(self):
        with patch("core.telegram_delivery_runtime_policy.settings") as configured, patch(
            "core.telegram_delivery_runtime_policy.current_server",
            return_value="foreign",
        ):
            configured.telegram_provider_test_authority = False
            configured.trading_bot_service = "api"
            configured.telegram_delivery_producer_mode = "legacy"
            configured.telegram_delivery_execution_owner = "legacy"
            configured.telegram_delivery_queue_worker_enabled = False
            configured.telegram_delivery_queue_cutover_ready = False
            assert_telegram_provider_execution_authority()

    def test_unknown_or_blank_owner_fails_closed(self):
        for owner in ("", "queue", "legacy-v2", "unexpected"):
            with self.subTest(owner=owner):
                with self.assertRaisesRegex(
                    TelegramDeliveryRuntimeConfigurationError,
                    "unknown_telegram_execution_owner",
                ):
                    resolve_telegram_delivery_runtime(
                        execution_owner=owner,
                        queue_worker_enabled=False,
                        cutover_ready=False,
                    )


if __name__ == "__main__":
    unittest.main()
