"""Characterization unit freezing early-return guards of send_delayed_removal_notification_api.

This unit covers only delayed-removal early-return guards, does not cover successful delivery,
does not complete P1-00b, and does not establish Offer/Request-origin evidence.
"""

from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.users import send_delayed_removal_notification_api
from models.user import User


def _create_test_user(
    *,
    trading_restricted_until: datetime | None,
    max_daily_trades: int | None,
    max_active_commodities: int | None,
    max_daily_requests: int | None,
) -> Mock:
    user = Mock()
    user.trading_restricted_until = trading_restricted_until
    user.max_daily_trades = max_daily_trades
    user.max_active_commodities = max_active_commodities
    user.max_daily_requests = max_daily_requests
    return user


def _make_session_factory(session: AsyncMock) -> Mock:
    async def _session_gen():
        yield session

    return Mock(side_effect=_session_gen)


class UsersDelayedRemovalGuardsCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization tests freezing only delayed-removal early-return guards.

    This unit covers only delayed-removal early-return guards, does not cover successful delivery,
    does not complete P1-00b, and does not establish Offer/Request-origin evidence.
    """

    async def _run_guard_case(
        self,
        *,
        user_double: object | None,
        is_block: bool,
        delay_seconds: int,
        user_id: int = 101,
        telegram_id: int | None = 9001,
        include_telegram: bool = True,
    ) -> None:
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
                is_block=is_block,
                delay_seconds=delay_seconds,
                include_telegram=include_telegram,
            )

            mock_sleep.assert_awaited_once_with(delay_seconds)
            mock_factory.assert_called_once_with()
            mock_session.get.assert_awaited_once_with(User, user_id)
            self.assertIsNone(result)
            mock_create_notification.assert_not_awaited()
            mock_send_telegram.assert_not_awaited()

    async def test_missing_user_guard_early_returns_none(self):
        """Freeze missing-user guard: session.get returns None, early returns None."""
        await self._run_guard_case(
            user_double=None,
            is_block=True,
            delay_seconds=45,
            user_id=101,
            telegram_id=9001,
            include_telegram=True,
        )

    async def test_active_block_guard_early_returns_none(self):
        """Freeze active-block guard: trading_restricted_until in far future causes early return."""
        user = _create_test_user(
            trading_restricted_until=datetime(2099, 1, 1, 12, 0, 0),
            max_daily_trades=None,
            max_active_commodities=None,
            max_daily_requests=None,
        )
        await self._run_guard_case(
            user_double=user,
            is_block=True,
            delay_seconds=75,
            user_id=102,
            telegram_id=9002,
            include_telegram=True,
        )

    async def test_limitations_guard_max_daily_trades_early_returns_none(self):
        """Freeze limitations guard for is_block=False when max_daily_trades is set."""
        user = _create_test_user(
            trading_restricted_until=None,
            max_daily_trades=5,
            max_active_commodities=None,
            max_daily_requests=None,
        )
        await self._run_guard_case(
            user_double=user,
            is_block=False,
            delay_seconds=30,
            user_id=103,
            telegram_id=9003,
            include_telegram=True,
        )

    async def test_limitations_guard_max_active_commodities_early_returns_none(self):
        """Freeze limitations guard for is_block=False when max_active_commodities is set."""
        user = _create_test_user(
            trading_restricted_until=None,
            max_daily_trades=None,
            max_active_commodities=3,
            max_daily_requests=None,
        )
        await self._run_guard_case(
            user_double=user,
            is_block=False,
            delay_seconds=60,
            user_id=104,
            telegram_id=9004,
            include_telegram=True,
        )

    async def test_limitations_guard_max_daily_requests_early_returns_none(self):
        """Freeze limitations guard for is_block=False when max_daily_requests is set."""
        user = _create_test_user(
            trading_restricted_until=None,
            max_daily_trades=None,
            max_active_commodities=None,
            max_daily_requests=10,
        )
        await self._run_guard_case(
            user_double=user,
            is_block=False,
            delay_seconds=90,
            user_id=105,
            telegram_id=9005,
            include_telegram=True,
        )


if __name__ == "__main__":
    unittest.main()
