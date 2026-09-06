"""Characterization unit freezing missing-user branch of send_delayed_removal_notification_api.

This unit freezes only the missing-user local branch and does not prove provider delivery,
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
from models.user import User


class UsersDelayedRemovalMissingUserCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Characterization test freezing only the missing-user local branch.

    This unit freezes only the missing-user local branch and does not prove provider delivery,
    Offer/Request-origin behavior, Stage completion, production readiness, or external behavior.
    """

    async def test_missing_user_lookup_returns_none_early_returns_none(
        self,
    ) -> None:
        """Freeze missing-user branch where session.get returns None.

        This test freezes only the missing-user local branch and does not prove provider delivery,
        Offer/Request-origin behavior, Stage completion, production readiness, or external behavior.
        """
        mock_session = AsyncMock()
        mock_session.get.return_value = None

        async def _session_gen():
            yield mock_session

        mock_factory = Mock(side_effect=_session_gen)

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
                user_id=101,
                telegram_id=9001,
                is_block=True,
                delay_seconds=45,
                include_telegram=True,
            )

            mock_sleep.assert_awaited_once_with(45)
            mock_factory.assert_called_once_with()
            mock_session.get.assert_awaited_once_with(User, 101)
            self.assertIsNone(result)
            mock_create_notification.assert_not_awaited()
            mock_create_notification.assert_not_called()
            mock_send_telegram.assert_not_awaited()
            mock_send_telegram.assert_not_called()


if __name__ == "__main__":
    unittest.main()
