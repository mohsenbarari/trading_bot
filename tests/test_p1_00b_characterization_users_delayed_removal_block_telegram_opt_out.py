"""Characterization unit freezing the include_telegram=False successful block-removal branch of send_delayed_removal_notification_api.

This unit is a P1-00b current-behavior characterization only and does not prove provider delivery,
Offer/Request-origin behavior, Stage completion, production readiness, or external behavior.
"""

from __future__ import annotations

import unittest
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


class UsersDelayedRemovalBlockTelegramOptOutCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization tests freezing the include_telegram=False successful block-removal branch.

    This unit is a P1-00b current-behavior characterization only and does not prove provider delivery,
    Offer/Request-origin behavior, Stage completion, production readiness, or external behavior.
    """

    async def test_send_delayed_removal_notification_block_telegram_opt_out(self) -> None:
        """Freeze successful block-removal behavior when include_telegram is False.

        This test freezes only local current behavior for the include_telegram=False successful
        block-removal branch and does not prove provider delivery, Offer/Request-origin behavior,
        Stage completion, production readiness, or external behavior.
        """
        user_id = 101
        telegram_id = 9001
        delay_seconds = 45
        expected_message = "ℹ️ *رفع مسدودیت توسط مدیر*\n\nمسدودیت حساب شما توسط مدیر رفع شد."

        user_double = Mock()
        user_double.trading_restricted_until = None

        mock_session = AsyncMock()
        mock_session.get.return_value = user_double
        mock_factory = _make_session_factory(mock_session)

        with patch(
            "api.routers.users.asyncio.sleep",
            new_callable=AsyncMock,
        ) as mock_sleep, patch(
            "api.routers.users.create_user_notification",
            new_callable=AsyncMock,
        ) as mock_create_notification, patch(
            "api.routers.users.send_telegram_notification",
            new_callable=AsyncMock,
        ) as mock_send_telegram:
            result = await send_delayed_removal_notification_api(
                db_session_factory=mock_factory,
                user_id=user_id,
                telegram_id=telegram_id,
                is_block=True,
                delay_seconds=delay_seconds,
                include_telegram=False,
            )

            mock_sleep.assert_awaited_once_with(delay_seconds)
            mock_factory.assert_called_once_with()
            mock_session.get.assert_awaited_once_with(User, user_id)
            mock_create_notification.assert_awaited_once_with(
                mock_session,
                user_id,
                expected_message,
                NotificationLevel.INFO,
                NotificationCategory.SYSTEM,
            )
            mock_send_telegram.assert_not_awaited()
            mock_send_telegram.assert_not_called()
            self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
