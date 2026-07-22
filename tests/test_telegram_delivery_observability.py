import unittest
from pathlib import Path

from core.metrics import registry
from core.services.telegram_delivery_observability_service import (
    TelegramQueueHealthThresholds,
    publish_telegram_delivery_health_metrics,
)
from scripts.report_telegram_delivery_queue_health import (
    TelegramQueueObservabilityConfigurationError,
    parse_args,
    validate_observability_environment,
)


class TelegramDeliveryObservabilityUnitTests(unittest.TestCase):
    def tearDown(self):
        registry.reset()

    def test_thresholds_reject_negative_or_inverted_values(self):
        with self.assertRaisesRegex(ValueError, "threshold_invalid"):
            TelegramQueueHealthThresholds(stop_ready_depth=-1)
        with self.assertRaisesRegex(ValueError, "ready_warning_exceeds_stop"):
            TelegramQueueHealthThresholds(
                warning_ready_depth=11,
                stop_ready_depth=10,
            )
        with self.assertRaisesRegex(ValueError, "age_warning_exceeds_stop"):
            TelegramQueueHealthThresholds(
                warning_oldest_ready_age_seconds=31,
                stop_oldest_ready_age_seconds=30,
            )

    def test_runner_requires_explicit_non_production_environment(self):
        with self.assertRaises(SystemExit):
            parse_args([])
        with self.assertRaisesRegex(
            TelegramQueueObservabilityConfigurationError,
            "production_environment_is_forbidden",
        ):
            validate_observability_environment("production", "production")
        with self.assertRaisesRegex(
            TelegramQueueObservabilityConfigurationError,
            "synthetic_test_database_name_invalid",
        ):
            validate_observability_environment("synthetic-test", "production")
        validate_observability_environment(
            "synthetic-test",
            "telegram_queue_stage3_observability_test",
        )
        validate_observability_environment("staging", "trading_bot_staging")

    def test_metrics_have_only_bounded_labels_and_no_identity(self):
        snapshot = {
            "ready_depth": 3,
            "ready_by_priority_destination": [
                {
                    "priority": 0,
                    "destination_class": "channel",
                    "depth": 3,
                    "oldest_age_seconds": 2.5,
                }
            ],
            "decision": "stop",
            "goodput_per_second": 2.0,
            "ingress_rate_per_second": 3.0,
        }
        publish_telegram_delivery_health_metrics(snapshot)
        rendered = registry.render_prometheus()
        self.assertIn("telegram_delivery_queue_ready_depth", rendered)
        self.assertIn('priority="0"', rendered)
        self.assertIn('destination_class="channel"', rendered)
        self.assertNotIn("chat_id", rendered)
        self.assertNotIn("dedupe", rendered)
        self.assertNotIn("destination_key", rendered)

    def test_observer_has_no_gateway_http_client_or_row_lock_surface(self):
        repo = Path(__file__).resolve().parents[1]
        for relative in (
            "core/services/telegram_delivery_observability_service.py",
            "scripts/report_telegram_delivery_queue_health.py",
        ):
            source = (repo / relative).read_text(encoding="utf-8")
            self.assertNotIn("telegram_gateway", source)
            self.assertNotIn("httpx", source)
            self.assertNotIn("with_for_update", source)


if __name__ == "__main__":
    unittest.main()
