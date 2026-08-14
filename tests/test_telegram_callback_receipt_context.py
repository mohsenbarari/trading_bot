import unittest
from datetime import datetime, timezone

from core.telegram_callback_receipt_context import (
    bind_telegram_callback_received_at,
    current_telegram_callback_received_at,
    reset_telegram_callback_received_at,
)


class TelegramCallbackReceiptContextTests(unittest.TestCase):
    def test_binding_normalizes_utc_and_resets_without_leakage(self):
        self.assertIsNone(current_telegram_callback_received_at())
        token = bind_telegram_callback_received_at(datetime(2026, 7, 18, 12, 30))
        try:
            self.assertEqual(
                current_telegram_callback_received_at(),
                datetime(2026, 7, 18, 12, 30, tzinfo=timezone.utc),
            )
        finally:
            reset_telegram_callback_received_at(token)
        self.assertIsNone(current_telegram_callback_received_at())


if __name__ == "__main__":
    unittest.main()
