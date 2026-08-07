"""Unit tests for Telegram channel token-bucket pacing."""
from __future__ import annotations

import unittest

from core.telegram_channel_pace import TelegramChannelPace


class TelegramChannelPaceTests(unittest.TestCase):
    def test_full_bucket_allows_immediate_burst(self):
        pace = TelegramChannelPace(rate_per_minute=20.0, capacity=5.0)
        waits = [pace.wait_seconds_before_send() for _ in range(5)]
        self.assertEqual(waits, [0.0, 0.0, 0.0, 0.0, 0.0])
        self.assertGreater(pace.wait_seconds_before_send(), 0.0)

    def test_rate_limit_drains_bucket(self):
        pace = TelegramChannelPace(rate_per_minute=20.0, capacity=5.0)
        self.assertEqual(pace.wait_seconds_before_send(), 0.0)
        pace.note_rate_limited()
        self.assertGreater(pace.wait_seconds_before_send(), 2.0)


if __name__ == "__main__":
    unittest.main()
