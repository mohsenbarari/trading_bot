import unittest
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeConfigurationError,
    TelegramDeliveryRuntimeDecision,
    TelegramDeliveryRuntimeMode,
)
from run_bot import (
    offer_telegram_publication_loop,
    telegram_admin_broadcast_delivery_loop,
    telegram_delivery_queue_loop,
    configured_telegram_delivery_queue_worker_factory,
    telegram_execution_worker_factories,
    telegram_notification_outbox_delivery_loop,
    telegram_trade_delivery_loop,
)


class BotTelegramExecutionOwnershipTests(unittest.TestCase):
    @staticmethod
    def _queue_settings(**overrides):
        values = {
            "bot_token": "primary:test-token",
            "channel_id": -1001234567890,
            "redis_url": "redis://queue.test/15",
            "telegram_delivery_queue_channel_editor_enabled": False,
            "telegram_delivery_queue_channel_editor_bot_token": None,
            "telegram_delivery_queue_bot_min_interval_seconds": 0.035,
            "telegram_delivery_queue_destination_min_interval_seconds": 1.05,
            "telegram_delivery_queue_rate_limit_probe_delay_seconds": 0.1,
            "telegram_delivery_queue_global_rate_limit_window_seconds": 2.0,
            "telegram_delivery_queue_worker_lease_seconds": 30.0,
            "telegram_delivery_queue_worker_request_timeout_seconds": 10.0,
            "telegram_delivery_queue_limiter_key_ttl_seconds": 86400,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

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
            ),
            settings_obj=self._queue_settings(),
        )
        self.assertEqual(len(factories), 1)
        self.assertIsNot(factories[0], telegram_delivery_queue_loop)
        self.assertEqual(
            factories[0].__name__,
            "run_configured_telegram_delivery_queue",
        )
        self.assertNotIn(offer_telegram_publication_loop, factories)
        self.assertNotIn(telegram_trade_delivery_loop, factories)
        self.assertNotIn(telegram_admin_broadcast_delivery_loop, factories)
        self.assertNotIn(telegram_notification_outbox_delivery_loop, factories)

    def test_configured_queue_factory_binds_registry_adapters_and_shared_limiter(self):
        settings_obj = self._queue_settings(
            telegram_delivery_queue_channel_editor_enabled=True,
            telegram_delivery_queue_channel_editor_bot_token="editor:test-token",
        )
        redis_client = Mock()
        redis_client.aclose = AsyncMock()
        limiter = object()

        with (
            patch("run_bot.redis.Redis.from_url", return_value=redis_client),
            patch(
                "run_bot.configured_redis_telegram_delivery_limiter",
                return_value=limiter,
            ),
            patch(
                "run_bot.telegram_delivery_queue_loop",
                new=AsyncMock(),
            ) as queue_loop,
        ):
            runner = configured_telegram_delivery_queue_worker_factory(settings_obj)
            asyncio.run(runner())

        kwargs = queue_loop.await_args.kwargs
        self.assertEqual(kwargs["bot_identities"], ("primary", "channel_editor"))
        self.assertEqual(set(kwargs["freshness_validators"]), {"primary", "channel_editor"})
        self.assertEqual(set(kwargs["lifecycle_feedbacks"]), {"primary", "channel_editor"})
        self.assertIs(kwargs["dispatch_limiter"], limiter)
        self.assertEqual(
            kwargs["credential_registry"].bot_identities,
            ("primary", "channel_editor"),
        )
        redis_client.aclose.assert_awaited_once()

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
