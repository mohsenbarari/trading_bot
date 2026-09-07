"""Direct-characterization tests for api.routers.blocks.check_block_status.

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

from api.routers.blocks import check_block_status  # noqa: E402


class CharacterizeCheckBlockStatusTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing check_block_status."""

    @patch("api.routers.blocks.is_blocked_by", new_callable=AsyncMock)
    async def test_check_block_status_success(
        self,
        mock_is_blocked_by: AsyncMock,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42)
        target_user_id = 99
        sentinel_blocked_status = object()
        mock_is_blocked_by.return_value = sentinel_blocked_status

        result = await check_block_status(
            user_id=target_user_id,
            db=fake_db,
            current_user=current_user,
        )

        self.assertEqual(
            result,
            {
                "user_id": target_user_id,
                "is_blocked_by_me": sentinel_blocked_status,
            },
        )
        self.assertIs(result["is_blocked_by_me"], sentinel_blocked_status)
        mock_is_blocked_by.assert_awaited_once_with(
            fake_db, current_user.id, target_user_id
        )

    @patch("api.routers.blocks.is_blocked_by", new_callable=AsyncMock)
    async def test_check_block_status_dependency_failure(
        self,
        mock_is_blocked_by: AsyncMock,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42)
        target_user_id = 99
        sentinel_error = RuntimeError("sentinel dependency failure")
        mock_is_blocked_by.side_effect = sentinel_error

        with self.assertRaises(RuntimeError) as context:
            await check_block_status(
                user_id=target_user_id,
                db=fake_db,
                current_user=current_user,
            )

        self.assertIs(context.exception, sentinel_error)
        mock_is_blocked_by.assert_awaited_once_with(
            fake_db, current_user.id, target_user_id
        )


if __name__ == "__main__":
    unittest.main()
