from datetime import datetime, timedelta, timezone
import unittest

from core.services.telegram_notification_outbox_queue_service import (
    _dependency_wait_retry_seconds,
)
from models.telegram_notification_outbox import TelegramNotificationOutbox


NOW = datetime(2026, 8, 29, 10, 30, tzinfo=timezone.utc)
REASON = "notification_action_result_target_pending"


class TelegramNotificationOutboxDependencyBackoffTests(unittest.TestCase):
    def test_first_wait_keeps_the_one_second_fast_retry(self):
        outbox = TelegramNotificationOutbox(
            reason="different_state",
            created_at=NOW - timedelta(days=2),
        )

        self.assertEqual(
            _dependency_wait_retry_seconds(outbox, reason=REASON, now=NOW),
            1,
        )

    def test_repeated_wait_uses_bounded_age_based_backoff(self):
        cases = (
            (timedelta(seconds=59), 1),
            (timedelta(seconds=60), 5),
            (timedelta(minutes=5), 15),
            (timedelta(hours=1), 60),
            (timedelta(days=4), 60),
        )

        for age, expected_seconds in cases:
            with self.subTest(age=age):
                outbox = TelegramNotificationOutbox(
                    reason=REASON,
                    created_at=NOW - age,
                )
                self.assertEqual(
                    _dependency_wait_retry_seconds(
                        outbox,
                        reason=REASON,
                        now=NOW,
                    ),
                    expected_seconds,
                )

    def test_naive_database_timestamp_is_compared_as_the_current_timezone(self):
        outbox = TelegramNotificationOutbox(
            reason=REASON,
            created_at=(NOW - timedelta(minutes=6)).replace(tzinfo=None),
        )

        self.assertEqual(
            _dependency_wait_retry_seconds(outbox, reason=REASON, now=NOW),
            15,
        )


if __name__ == "__main__":
    unittest.main()
