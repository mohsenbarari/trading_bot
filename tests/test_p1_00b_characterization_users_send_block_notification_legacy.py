"""Characterization tests for api.routers.users.send_block_notification in LEGACY mode.

This module characterizes only the existing LEGACY Telegram-delivery branch
and does not complete P1-00b, establish Offer/Request-origin interaction evidence,
prove provider delivery, establish Stage completion, or verify production readiness.
"""

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.users import send_block_notification
from core.enums import NotificationCategory, NotificationLevel
from core.telegram_delivery_runtime_policy import TelegramDeliveryRuntimeMode


class TestUsersSendBlockNotificationLegacy(unittest.IsolatedAsyncioTestCase):
    async def test_send_block_notification_legacy(self) -> None:
        db_sentinel = object()
        user = SimpleNamespace(id=101, telegram_id=987654321)
        restricted_until = datetime(2026, 9, 6, 12, 0, 0, tzinfo=timezone.utc)
        message_sentinel = "deterministic-block-message-sentinel"

        with patch(
            "api.routers.users._block_notification_message",
            return_value=message_sentinel,
        ) as mock_block_message, patch(
            "api.routers.users.configured_telegram_delivery_producer_mode",
            return_value=TelegramDeliveryRuntimeMode.LEGACY,
        ) as mock_producer_mode, patch(
            "api.routers.users._enqueue_block_notification_intent",
            new_callable=AsyncMock,
        ) as mock_enqueue, patch(
            "api.routers.users.create_user_notification",
            new_callable=AsyncMock,
        ) as mock_create_notification, patch(
            "api.routers.users.send_telegram_notification",
            new_callable=AsyncMock,
        ) as mock_send_telegram:
            result = await send_block_notification(
                db_sentinel,  # type: ignore[arg-type]
                user,  # type: ignore[arg-type]
                restricted_until,
            )

            self.assertIsNone(result)
            mock_block_message.assert_called_once_with(restricted_until)
            mock_producer_mode.assert_called_once_with()
            mock_enqueue.assert_not_called()
            mock_enqueue.assert_not_awaited()
            mock_create_notification.assert_awaited_once_with(
                db_sentinel,
                user.id,
                message_sentinel,
                NotificationLevel.WARNING,
                NotificationCategory.SYSTEM,
            )
            self.assertIs(mock_create_notification.await_args.args[0], db_sentinel)
            mock_send_telegram.assert_awaited_once_with(
                user.telegram_id,
                message_sentinel,
            )


if __name__ == "__main__":
    unittest.main()
