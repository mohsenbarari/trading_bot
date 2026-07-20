import unittest
from unittest.mock import patch

from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeConfigurationError,
    TelegramDeliveryRuntimeMode,
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

    def test_environment_cannot_activate_incomplete_queue_implementation(self):
        with self.assertRaisesRegex(
            TelegramDeliveryRuntimeConfigurationError,
            "queue_implementation_not_cutover_ready",
        ):
            resolve_telegram_delivery_runtime(
                execution_owner="queue-v1",
                queue_worker_enabled=True,
                cutover_ready=True,
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
