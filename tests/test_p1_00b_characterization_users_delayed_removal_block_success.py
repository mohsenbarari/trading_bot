"""Characterization unit freezing the successful block-removal path of send_delayed_removal_notification_api.

This unit is a P1-00b current-behavior characterization only and does not prove provider delivery,
Offer/Request-origin behavior, Stage completion, production readiness, or external behavior.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.users import send_delayed_removal_notification_api
from core.enums import NotificationCategory, NotificationLevel
from models.user import User


def _make_session_factory(session: AsyncMock) -> Mock:
    async def _session_gen():
        yield session

    return Mock(side_effect=_session_gen)


class UsersDelayedRemovalBlockSuccessCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization tests freezing the successful block-removal notification branch.

    This unit is a P1-00b current-behavior characterization only and does not prove provider delivery,
    Offer/Request-origin behavior, Stage completion, production readiness, or external behavior.
    """

    async def test_send_delayed_removal_notification_block_success(self) -> None:
        user_id = 101
        telegram_id = 9001
        delay_seconds = 45
        expected_message = "ℹ️ *رفع مسدودیت توسط مدیر*\n\nمسدودیت حساب شما توسط مدیر رفع شد."
        fixed_now = datetime(2026, 9, 6, 10, 0, 0, tzinfo=timezone.utc)

        user_double = Mock()
        user_double.trading_restricted_until = None

        mock_session = AsyncMock()
        mock_session.get.return_value = user_double
        mock_factory = _make_session_factory(mock_session)

        with patch(
            "api.routers.users.asyncio.sleep",
            new_callable=AsyncMock,
        ) as mock_sleep, patch(
            "api.routers.users.datetime",
        ) as mock_datetime, patch(
            "api.routers.users.create_user_notification",
            new_callable=AsyncMock,
        ) as mock_create_notification, patch(
            "api.routers.users.send_telegram_notification",
            new_callable=AsyncMock,
        ) as mock_send_telegram:
            mock_datetime.now.return_value = fixed_now
            mock_send_telegram.return_value = True

            result = await send_delayed_removal_notification_api(
                db_session_factory=mock_factory,
                user_id=user_id,
                telegram_id=telegram_id,
                is_block=True,
                delay_seconds=delay_seconds,
                include_telegram=True,
            )

            mock_sleep.assert_awaited_once_with(delay_seconds)
            mock_factory.assert_called_once_with()
            mock_session.get.assert_awaited_once_with(User, user_id)
            mock_datetime.now.assert_called_once_with(timezone.utc)
            mock_create_notification.assert_awaited_once_with(
                mock_session,
                user_id,
                expected_message,
                NotificationLevel.INFO,
                NotificationCategory.SYSTEM,
            )
            mock_send_telegram.assert_awaited_once_with(
                telegram_id,
                expected_message,
            )
            self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
