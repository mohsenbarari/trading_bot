import unittest

from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeConfigurationError,
    TelegramDeliveryRuntimeDecision,
    TelegramDeliveryRuntimeMode,
)
from run_bot import (
    offer_telegram_publication_loop,
    telegram_admin_broadcast_delivery_loop,
    telegram_delivery_queue_loop,
    telegram_execution_worker_factories,
    telegram_notification_outbox_delivery_loop,
    telegram_trade_delivery_loop,
)


class BotTelegramExecutionOwnershipTests(unittest.TestCase):
    def test_legacy_mode_starts_all_and_only_legacy_execution_workers(self):
        factories = telegram_execution_worker_factories(
            TelegramDeliveryRuntimeDecision(
                mode=TelegramDeliveryRuntimeMode.LEGACY,
                legacy_workers_enabled=True,
                queue_worker_enabled=False,
            )
        )
        self.assertEqual(
            factories,
            (
                offer_telegram_publication_loop,
                telegram_trade_delivery_loop,
                telegram_admin_broadcast_delivery_loop,
                telegram_notification_outbox_delivery_loop,
            ),
        )
        self.assertNotIn(telegram_delivery_queue_loop, factories)

    def test_queue_mode_starts_only_the_shared_queue_worker(self):
        factories = telegram_execution_worker_factories(
            TelegramDeliveryRuntimeDecision(
                mode=TelegramDeliveryRuntimeMode.QUEUE_V1,
                legacy_workers_enabled=False,
                queue_worker_enabled=True,
            )
        )
        self.assertEqual(factories, (telegram_delivery_queue_loop,))
        self.assertNotIn(offer_telegram_publication_loop, factories)
        self.assertNotIn(telegram_trade_delivery_loop, factories)
        self.assertNotIn(telegram_admin_broadcast_delivery_loop, factories)
        self.assertNotIn(telegram_notification_outbox_delivery_loop, factories)

    def test_inconsistent_runtime_decisions_fail_closed(self):
        decisions = (
            TelegramDeliveryRuntimeDecision(
                mode=TelegramDeliveryRuntimeMode.LEGACY,
                legacy_workers_enabled=True,
                queue_worker_enabled=True,
            ),
            TelegramDeliveryRuntimeDecision(
                mode=TelegramDeliveryRuntimeMode.QUEUE_V1,
                legacy_workers_enabled=True,
                queue_worker_enabled=True,
            ),
            TelegramDeliveryRuntimeDecision(
                mode=TelegramDeliveryRuntimeMode.QUEUE_V1,
                legacy_workers_enabled=False,
                queue_worker_enabled=False,
            ),
        )
        for decision in decisions:
            with self.subTest(decision=decision), self.assertRaises(
                TelegramDeliveryRuntimeConfigurationError
            ):
                telegram_execution_worker_factories(decision)


if __name__ == "__main__":
    unittest.main()
