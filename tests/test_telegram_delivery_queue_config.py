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
    }
    values.update(overrides)
    return Settings(**values)


class TelegramDeliveryQueueConfigTests(unittest.TestCase):
    def test_three_site_protocol_flags_are_atomic_and_replay_bounds_are_positive(self):
        enabled = _settings(
            three_site_dr_enabled=True,
            dr_event_protocol_enabled=True,
            dr_event_protocol_strict=True,
        )
        self.assertTrue(enabled.dr_event_protocol_strict)

        invalid = (
            {"three_site_dr_enabled": True},
            {"dr_event_protocol_enabled": True},
            {"dr_event_protocol_strict": True},
            {
                "three_site_dr_enabled": True,
                "dr_event_protocol_enabled": True,
                "dr_event_protocol_strict": False,
            },
            {"dr_sync_request_max_age_seconds": 0},
            {"dr_replay_nonce_retention_seconds": 0},
        )
        for values in invalid:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                _settings(**values)

    def test_default_queue_retry_and_lease_config_is_valid(self):
        settings = _settings()
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

    def test_nonfinite_negative_and_inverted_retry_config_fail_startup(self):
        invalid = (
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
