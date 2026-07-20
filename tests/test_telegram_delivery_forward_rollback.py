from pathlib import Path
import unittest

from scripts.check_telegram_delivery_forward_rollback import (
    TelegramForwardRollbackConfigurationError,
    validate_environment,
)


class TelegramDeliveryForwardRollbackUnitTests(unittest.TestCase):
    def test_environment_guard_requires_explicit_production_read_only_ack(self):
        validate_environment(
            "synthetic-test",
            "telegram_queue_stage3_rollback_test",
            acknowledge_production_read_only=False,
        )
        validate_environment(
            "staging",
            "trading_bot_staging",
            acknowledge_production_read_only=False,
        )
        with self.assertRaisesRegex(
            TelegramForwardRollbackConfigurationError,
            "production_requires_explicit_read_only_acknowledgement",
        ):
            validate_environment(
                "production",
                "trading_bot",
                acknowledge_production_read_only=False,
            )
        validate_environment(
            "production",
            "trading_bot",
            acknowledge_production_read_only=True,
        )

    def test_checker_has_no_gateway_http_client_row_lock_or_downgrade_execution(self):
        repo = Path(__file__).resolve().parents[1]
        for relative in (
            "core/services/telegram_delivery_rollback_service.py",
            "scripts/check_telegram_delivery_forward_rollback.py",
        ):
            source = (repo / relative).read_text(encoding="utf-8")
            self.assertNotIn("telegram_gateway", source)
            self.assertNotIn("httpx", source)
            self.assertNotIn("with_for_update", source)
            self.assertNotIn("command.downgrade", source)


if __name__ == "__main__":
    unittest.main()
