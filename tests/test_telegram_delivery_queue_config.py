import unittest

from pydantic import ValidationError

from core.config import Settings


def _settings(**overrides):
    values = {
        "database_url": "postgresql+asyncpg://test:test@127.0.0.1/test",
        "sync_database_url": "postgresql+psycopg2://test:test@127.0.0.1/test",
        "postgres_db": "test",
        "postgres_user": "test",
        "postgres_password": "test",
        "frontend_url": "http://localhost:3000",
        "redis_url": "redis://127.0.0.1:6379/15",
        "jwt_secret_key": "test-only-not-production",
        "telegram_provider_test_authority": False,
    }
    values.update(overrides)
    return Settings(**values)


class TelegramDeliveryQueueConfigTests(unittest.TestCase):
    def test_default_queue_retry_and_lease_config_is_valid(self):
        settings = _settings()
        self.assertEqual(
            settings.telegram_delivery_queue_primary_idle_poll_interval_seconds,
            0.2,
        )
        self.assertEqual(
            settings.telegram_delivery_queue_publisher_idle_poll_interval_seconds,
            0.5,
        )
        self.assertEqual(
            settings.telegram_notification_outbox_queue_feeder_interval_seconds,
            0.2,
        )
        self.assertLessEqual(
            settings.telegram_delivery_queue_retry_base_seconds,
            settings.telegram_delivery_queue_retry_max_seconds,
        )
        self.assertGreaterEqual(
            settings.telegram_delivery_queue_worker_lease_seconds,
            settings.telegram_delivery_queue_worker_request_timeout_seconds + 15,
        )
        self.assertGreater(
            settings.telegram_delivery_queue_primary_concurrency,
            settings.telegram_delivery_queue_primary_m0_reserved_concurrency,
        )

    def test_producer_and_expected_executor_split_brain_fails_startup(self):
        invalid = (
            {
                "telegram_delivery_producer_mode": "queue-v1",
                "telegram_delivery_expected_execution_owner": "legacy",
            },
            {
                "telegram_delivery_producer_mode": "legacy",
                "telegram_delivery_expected_execution_owner": "queue-v1",
            },
            {
                "trading_bot_service": "bot",
                "telegram_delivery_producer_mode": "queue-v1",
                "telegram_delivery_expected_execution_owner": "queue-v1",
                "telegram_delivery_execution_owner": "legacy",
            },
        )
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                _settings(**values)

        api = _settings(
            trading_bot_service="api",
            telegram_delivery_producer_mode="queue-v1",
            telegram_delivery_expected_execution_owner="queue-v1",
            telegram_delivery_execution_owner="producer-only",
        )
        self.assertEqual(api.telegram_delivery_producer_mode, "queue-v1")
        self.assertEqual(api.telegram_delivery_execution_owner, "producer-only")

        with self.assertRaises(ValidationError):
            _settings(
                trading_bot_service="bot",
                telegram_delivery_producer_mode="queue-v1",
                telegram_delivery_expected_execution_owner="queue-v1",
                telegram_delivery_execution_owner="producer-only",
            )
        with self.assertRaises(ValidationError):
            _settings(
                trading_bot_service="api",
                telegram_provider_test_authority=True,
            )

        inherited = _settings(
            trading_bot_service="api",
            telegram_delivery_execution_owner="producer-only",
        )
        self.assertEqual(inherited.telegram_delivery_producer_mode, "queue-v1")
        self.assertEqual(
            inherited.telegram_delivery_expected_execution_owner, "queue-v1"
        )
        self.assertIsNone(_settings(smsir_line_number="").smsir_line_number)

    def test_b2b_dispatch_is_fail_closed_behind_multi_publisher_flag(self):
        defaults = _settings()
        self.assertFalse(defaults.telegram_multi_publisher_enabled)
        self.assertFalse(defaults.telegram_b2b_dispatch_enabled)

        with self.assertRaises(ValidationError):
            _settings(telegram_b2b_dispatch_enabled=True)

        configured = _settings(
            telegram_multi_publisher_enabled=True,
            telegram_b2b_dispatch_enabled=True,
        )
        self.assertTrue(configured.telegram_multi_publisher_enabled)
        self.assertTrue(configured.telegram_b2b_dispatch_enabled)

    def test_nonfinite_negative_and_inverted_retry_config_fail_startup(self):
        invalid = (
            {"telegram_delivery_queue_primary_idle_poll_interval_seconds": 0},
            {"telegram_delivery_queue_publisher_idle_poll_interval_seconds": 0},
            {"telegram_notification_outbox_queue_feeder_interval_seconds": 0},
            {
                "telegram_delivery_queue_primary_idle_poll_interval_seconds": float(
                    "nan"
                )
            },
            {
                "telegram_notification_outbox_queue_feeder_interval_seconds": float(
                    "inf"
                )
            },
            {"telegram_delivery_queue_retry_base_seconds": float("nan")},
            {"telegram_delivery_queue_retry_max_seconds": float("inf")},
            {"telegram_delivery_queue_retry_after_safety_seconds": -0.1},
            {"telegram_delivery_queue_retry_jitter_ratio": float("nan")},
            {"telegram_delivery_queue_retry_jitter_ratio": 1.01},
            {
                "telegram_delivery_queue_retry_base_seconds": 10,
                "telegram_delivery_queue_retry_max_seconds": 1,
            },
        )
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                _settings(**values)

    def test_lease_and_integer_bounds_fail_startup(self):
        invalid = (
            {
                "telegram_delivery_queue_worker_request_timeout_seconds": 20,
                "telegram_delivery_queue_worker_lease_seconds": 30,
            },
            {"telegram_delivery_queue_worker_batch_limit": 0},
            {"telegram_delivery_queue_limiter_key_ttl_seconds": -1},
            {"telegram_delivery_queue_destination_min_interval_seconds": 0},
            {"telegram_delivery_queue_primary_concurrency": 0},
            {"telegram_delivery_queue_primary_m0_reserved_concurrency": 0},
            {"telegram_multi_publisher_lane_concurrency": 0},
            {
                "telegram_delivery_queue_primary_concurrency": 2,
                "telegram_delivery_queue_primary_m0_reserved_concurrency": 2,
            },
        )
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                _settings(**values)


if __name__ == "__main__":
    unittest.main()
