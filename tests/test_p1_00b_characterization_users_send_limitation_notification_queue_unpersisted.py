"""Characterization tests for api.routers.users.send_limitation_notification in Queue-v1 unpersisted mode.

This module characterizes only the unpersisted Queue-v1 limitation-notification branch
and does not complete P1-00b, establish Offer/Request-origin interaction evidence,
prove provider delivery, establish Stage completion, or verify production readiness.
"""

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.users import send_limitation_notification
from core.enums import NotificationCategory, NotificationLevel
from core.telegram_delivery_runtime_policy import TelegramDeliveryRuntimeMode


class TestUsersSendLimitationNotificationQueueUnpersisted(unittest.IsolatedAsyncioTestCase):
    async def test_send_limitation_notification_queue_v1_unpersisted(self) -> None:
        db_sentinel = object()
        user = SimpleNamespace(
            id=101,
            telegram_id=987654321,
        )
        limitations_changed = ["مجموع تعداد معاملات: 5"]
        message_sentinel = "deterministic-limitation-message-sentinel"

        with patch(
            "api.routers.users._limitation_notification_message",
            return_value=message_sentinel,
        ) as mock_limitation_message, patch(
            "api.routers.users.configured_telegram_delivery_producer_mode",
            return_value=TelegramDeliveryRuntimeMode.QUEUE_V1,
        ) as mock_producer_mode, patch(
            "api.routers.users._enqueue_limitation_notification_intent",
            new_callable=AsyncMock,
        ) as mock_enqueue, patch(
            "api.routers.users.create_user_notification",
            new_callable=AsyncMock,
        ) as mock_create_notification, patch(
            "api.routers.users.send_telegram_notification",
            new_callable=AsyncMock,
        ) as mock_send_telegram:
            result = await send_limitation_notification(
                db_sentinel,  # type: ignore[arg-type]
                user,  # type: ignore[arg-type]
                limitations_changed,
            )

            self.assertIsNone(result)
            mock_limitation_message.assert_called_once_with(user, limitations_changed)
            self.assertIs(mock_limitation_message.call_args[0][0], user)
            self.assertIs(mock_limitation_message.call_args[0][1], limitations_changed)
            mock_producer_mode.assert_called_once_with()
            mock_enqueue.assert_awaited_once_with(
                db_sentinel,
                user=user,
                message=message_sentinel,
            )
            self.assertIs(mock_enqueue.await_args.args[0], db_sentinel)
            self.assertIs(mock_enqueue.await_args.kwargs["user"], user)
            self.assertIs(mock_enqueue.await_args.kwargs["message"], message_sentinel)
            mock_create_notification.assert_awaited_once_with(
                db_sentinel,
                user.id,
                message_sentinel,
                NotificationLevel.WARNING,
                NotificationCategory.SYSTEM,
            )
            self.assertIs(mock_create_notification.await_args.args[0], db_sentinel)
            mock_send_telegram.assert_not_called()
            mock_send_telegram.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
