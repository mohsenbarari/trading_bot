"""Direct-characterization tests for api.routers.blocks.get_my_blocked_users.

This is direct characterization only and does not update the executed-seed registry,
reduce a canonical blocker, promote evidence, or mark P1-00b complete.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.blocks import get_my_blocked_users  # noqa: E402


class CharacterizeGetMyBlockedUsersTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing get_my_blocked_users."""

    @patch("api.routers.blocks.get_blocked_users", new_callable=AsyncMock)
    @patch("api.routers.blocks.ensure_block_management_allowed", new_callable=AsyncMock)
    async def test_get_my_blocked_users_success(
        self,
        mock_ensure_block_management_allowed: AsyncMock,
        mock_get_blocked_users: AsyncMock,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42)
        sentinel_result = [
            {
                "id": 101,
                "account_name": "test_account",
                "mobile_number": "09120000000",
                "created_at": "2026-01-01T00:00:00",
            }
        ]
        mock_ensure_block_management_allowed.return_value = None
        mock_get_blocked_users.return_value = sentinel_result

        result = await get_my_blocked_users(
            db=fake_db,
            current_user=current_user,
        )

        self.assertIs(result, sentinel_result)
        mock_ensure_block_management_allowed.assert_awaited_once_with(
            fake_db, current_user
        )
        mock_get_blocked_users.assert_awaited_once_with(
            fake_db, current_user.id
        )

    @patch("api.routers.blocks.get_blocked_users", new_callable=AsyncMock)
    @patch("api.routers.blocks.ensure_block_management_allowed", new_callable=AsyncMock)
    async def test_get_my_blocked_users_guard_failure(
        self,
        mock_ensure_block_management_allowed: AsyncMock,
        mock_get_blocked_users: AsyncMock,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42)
        sentinel_error = RuntimeError("sentinel guard failure")
        mock_ensure_block_management_allowed.side_effect = sentinel_error

        with self.assertRaises(RuntimeError) as context:
            await get_my_blocked_users(
                db=fake_db,
                current_user=current_user,
            )

        self.assertIs(context.exception, sentinel_error)
        mock_ensure_block_management_allowed.assert_awaited_once_with(
            fake_db, current_user
        )
        mock_get_blocked_users.assert_not_awaited()

    @patch("api.routers.blocks.get_blocked_users", new_callable=AsyncMock)
    @patch("api.routers.blocks.ensure_block_management_allowed", new_callable=AsyncMock)
    async def test_get_my_blocked_users_service_failure(
        self,
        mock_ensure_block_management_allowed: AsyncMock,
        mock_get_blocked_users: AsyncMock,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42)
        sentinel_error = RuntimeError("sentinel service failure")
        mock_ensure_block_management_allowed.return_value = None
        mock_get_blocked_users.side_effect = sentinel_error

        with self.assertRaises(RuntimeError) as context:
            await get_my_blocked_users(
                db=fake_db,
                current_user=current_user,
            )

        self.assertIs(context.exception, sentinel_error)
        mock_ensure_block_management_allowed.assert_awaited_once_with(
            fake_db, current_user
        )
        mock_get_blocked_users.assert_awaited_once_with(
            fake_db, current_user.id
        )


if __name__ == "__main__":
    unittest.main()
