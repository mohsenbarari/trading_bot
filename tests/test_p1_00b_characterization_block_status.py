"""Direct-characterization tests for api.routers.blocks.get_my_block_status.

This direct characterization does not update the executed-seed registry,
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

from api.routers.blocks import get_my_block_status  # noqa: E402


class CharacterizeGetMyBlockStatusTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing get_my_block_status."""

    @patch("api.routers.blocks.get_block_status", new_callable=AsyncMock)
    async def test_get_my_block_status_success(
        self, mock_get_block_status: AsyncMock
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42)
        expected_status = {
            "can_block": True,
            "can_block_now": True,
            "max_blocked": 10,
            "current_blocked": 2,
            "remaining": 8,
            "reason_code": "OK",
            "reason_message": "Allowed",
        }
        mock_get_block_status.return_value = expected_status

        result = await get_my_block_status(db=fake_db, current_user=current_user)

        self.assertIs(result, expected_status)
        mock_get_block_status.assert_awaited_once_with(fake_db, current_user.id)

    @patch("api.routers.blocks.get_block_status", new_callable=AsyncMock)
    async def test_get_my_block_status_dependency_failure_propagation(
        self, mock_get_block_status: AsyncMock
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42)
        sentinel_error = RuntimeError("sentinel get_block_status failure")
        mock_get_block_status.side_effect = sentinel_error

        with self.assertRaises(RuntimeError) as context:
            await get_my_block_status(db=fake_db, current_user=current_user)

        self.assertIs(context.exception, sentinel_error)
        mock_get_block_status.assert_awaited_once_with(fake_db, current_user.id)


if __name__ == "__main__":
    unittest.main()
