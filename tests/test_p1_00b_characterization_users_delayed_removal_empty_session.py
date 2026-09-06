"""Characterization unit freezing empty-session-factory branch of send_delayed_removal_notification_api.

This unit freezes only the empty-session-factory local branch and does not prove provider delivery,
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


def _make_empty_session_factory() -> Mock:
    async def _empty_session_gen():
        if False:
            yield

    return Mock(side_effect=_empty_session_gen)


class UsersDelayedRemovalEmptySessionCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Characterization test freezing only the empty-session-factory local branch.

    This unit freezes only the empty-session-factory local branch and does not prove provider delivery,
    Offer/Request-origin behavior, Stage completion, production readiness, or external behavior.
    """

    async def test_empty_session_factory_yields_no_session_early_returns_none(
        self,
    ) -> None:
        """Freeze empty-session-factory branch where factory yields no session.

        This test freezes only the empty-session-factory local branch and does not prove provider delivery,
        Offer/Request-origin behavior, Stage completion, production readiness, or external behavior.
        """
        mock_factory = _make_empty_session_factory()

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
            self.assertIsNone(result)
            mock_create_notification.assert_not_awaited()
            mock_create_notification.assert_not_called()
            mock_send_telegram.assert_not_awaited()
            mock_send_telegram.assert_not_called()


if __name__ == "__main__":
    unittest.main()
