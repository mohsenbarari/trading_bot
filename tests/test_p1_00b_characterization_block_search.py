"""Direct-characterization tests for api.routers.blocks.search_users.

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

from api.routers.blocks import search_users  # noqa: E402


class CharacterizeSearchUsersTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing search_users."""

    @patch("api.routers.blocks.search_users_for_block", new_callable=AsyncMock)
    @patch("api.routers.blocks.ensure_block_management_allowed", new_callable=AsyncMock)
    async def test_search_users_success(
        self,
        mock_ensure_block_management_allowed: AsyncMock,
        mock_search_users_for_block: AsyncMock,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42)
        sentinel_result = [
            SimpleNamespace(
                id=101,
                account_name="test_account",
                mobile_number="09120000000",
                full_name="Test User",
                is_blocked=False,
            )
        ]
        mock_ensure_block_management_allowed.return_value = None
        mock_search_users_for_block.return_value = sentinel_result

        result = await search_users(
            q="test",
            limit=10,
            db=fake_db,
            current_user=current_user,
        )

        self.assertIs(result, sentinel_result)
        mock_ensure_block_management_allowed.assert_awaited_once_with(
            fake_db, current_user
        )
        mock_search_users_for_block.assert_awaited_once_with(
            fake_db, "test", current_user.id, 10
        )

    @patch("api.routers.blocks.search_users_for_block", new_callable=AsyncMock)
    @patch("api.routers.blocks.ensure_block_management_allowed", new_callable=AsyncMock)
    async def test_search_users_guard_failure(
        self,
        mock_ensure_block_management_allowed: AsyncMock,
        mock_search_users_for_block: AsyncMock,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42)
        sentinel_error = RuntimeError("sentinel guard failure")
        mock_ensure_block_management_allowed.side_effect = sentinel_error

        with self.assertRaises(RuntimeError) as context:
            await search_users(
                q="test",
                limit=10,
                db=fake_db,
                current_user=current_user,
            )

        self.assertIs(context.exception, sentinel_error)
        mock_ensure_block_management_allowed.assert_awaited_once_with(
            fake_db, current_user
        )
        mock_search_users_for_block.assert_not_awaited()

    @patch("api.routers.blocks.search_users_for_block", new_callable=AsyncMock)
    @patch("api.routers.blocks.ensure_block_management_allowed", new_callable=AsyncMock)
    async def test_search_users_service_failure(
        self,
        mock_ensure_block_management_allowed: AsyncMock,
        mock_search_users_for_block: AsyncMock,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42)
        sentinel_error = RuntimeError("sentinel service failure")
        mock_ensure_block_management_allowed.return_value = None
        mock_search_users_for_block.side_effect = sentinel_error

        with self.assertRaises(RuntimeError) as context:
            await search_users(
                q="test",
                limit=10,
                db=fake_db,
                current_user=current_user,
            )

        self.assertIs(context.exception, sentinel_error)
        mock_ensure_block_management_allowed.assert_awaited_once_with(
            fake_db, current_user
        )
        mock_search_users_for_block.assert_awaited_once_with(
            fake_db, "test", current_user.id, 10
        )


if __name__ == "__main__":
    unittest.main()
