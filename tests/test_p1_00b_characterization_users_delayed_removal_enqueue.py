"""Characterization tests for api.routers.users.enqueue_delayed_removal_telegram_notification_api delegation.

This unit freezes only API-to-outbox delegation intent for non-null Telegram IDs; it does not prove persistence, provider delivery, Offer/Request-origin behavior, Stage completion, production readiness, or any external behavior.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.users import enqueue_delayed_removal_telegram_notification_api
from core.services.telegram_notification_outbox_service import (
    TelegramNotificationRecipient,
)


class TestUsersDelayedRemovalEnqueue(unittest.IsolatedAsyncioTestCase):
    """Freeze API-to-outbox delegation intent for non-null Telegram IDs.

    This unit freezes only API-to-outbox delegation intent for non-null Telegram IDs; it does not prove persistence, provider delivery, Offer/Request-origin behavior, Stage completion, production readiness, or any external behavior.
    """

    async def test_enqueue_delayed_removal_block_positive_delay(self):
        """Freeze delegation for is_block=True with a positive delay.

        Freezes only API-to-outbox delegation intent; does not prove persistence, provider delivery, Offer/Request-origin behavior, Stage completion, production readiness, or any external behavior.
        """
        user = SimpleNamespace(id=42, telegram_id=1001, sync_version=5)
        db_sentinel = object()
        fixed_now = datetime(2026, 9, 6, 12, 0, 0, tzinfo=timezone.utc)
        delay_seconds = 120
        expected_due_at = fixed_now + timedelta(seconds=delay_seconds)
        expected_recipient = TelegramNotificationRecipient(
            user_id=42,
            telegram_id=1001,
        )
        expected_source_id = (
            f"delayed-restriction:block:42:{expected_due_at.isoformat()}"
        )
        expected_text = (
            "ℹ️ *رفع مسدودیت توسط مدیر*\n\nمسدودیت حساب شما توسط مدیر رفع شد."
        )

        with patch(
            "api.routers.users.datetime"
        ) as mock_datetime, patch(
            "core.services.telegram_notification_outbox_service.enqueue_delayed_restriction_telegram_notification_once",
            new_callable=AsyncMock,
        ) as mock_enqueue:
            mock_datetime.now.return_value = fixed_now

            result = await enqueue_delayed_removal_telegram_notification_api(
                db_sentinel,  # type: ignore[arg-type]
                user=user,  # type: ignore[arg-type]
                is_block=True,
                delay_seconds=delay_seconds,
            )

            self.assertIsNone(result)
            mock_datetime.now.assert_called_once_with(timezone.utc)
            mock_enqueue.assert_awaited_once_with(
                db_sentinel,
                recipient=expected_recipient,
                source_id=expected_source_id,
                text=expected_text,
                restriction_kind="block",
                not_before=expected_due_at,
                user_sync_version=5,
            )
            self.assertNotIn("parse_mode", mock_enqueue.await_args.kwargs)

    async def test_enqueue_delayed_removal_limitations_negative_delay_clamped(self):
        """Freeze delegation for is_block=False with a negative delay clamped to zero.

        Freezes only API-to-outbox delegation intent; does not prove persistence, provider delivery, Offer/Request-origin behavior, Stage completion, production readiness, or any external behavior.
        """
        user = SimpleNamespace(id=77, telegram_id=2002, sync_version=9)
        db_sentinel = object()
        fixed_now = datetime(2026, 9, 6, 15, 30, 0, tzinfo=timezone.utc)
        delay_seconds = -45
        expected_due_at = fixed_now
        expected_recipient = TelegramNotificationRecipient(
            user_id=77,
            telegram_id=2002,
        )
        expected_source_id = (
            f"delayed-restriction:limitations:77:{expected_due_at.isoformat()}"
        )
        expected_text = (
            "ℹ️ *رفع محدودیت توسط مدیر*\n\nمحدودیت‌های حساب شما توسط مدیر رفع شد."
        )

        with patch(
            "api.routers.users.datetime"
        ) as mock_datetime, patch(
            "core.services.telegram_notification_outbox_service.enqueue_delayed_restriction_telegram_notification_once",
            new_callable=AsyncMock,
        ) as mock_enqueue:
            mock_datetime.now.return_value = fixed_now

            result = await enqueue_delayed_removal_telegram_notification_api(
                db_sentinel,  # type: ignore[arg-type]
                user=user,  # type: ignore[arg-type]
                is_block=False,
                delay_seconds=delay_seconds,
            )

            self.assertIsNone(result)
            mock_datetime.now.assert_called_once_with(timezone.utc)
            mock_enqueue.assert_awaited_once_with(
                db_sentinel,
                recipient=expected_recipient,
                source_id=expected_source_id,
                text=expected_text,
                restriction_kind="limitations",
                not_before=expected_due_at,
                user_sync_version=9,
            )
            self.assertNotIn("parse_mode", mock_enqueue.await_args.kwargs)


if __name__ == "__main__":
    unittest.main()
