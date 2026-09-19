"""Hermetic characterization batch for api.routers.blocks read handlers.

Characterizes:
- api.routers.blocks.get_my_block_status
- api.routers.blocks.check_block_status
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.blocks import check_block_status, get_my_block_status  # noqa: E402


class StrictDatabaseSentinel:
    """Strict database sentinel that rejects any attribute access or mutation."""

    def __getattr__(self, name: str) -> None:
        raise AssertionError(f"Strict database sentinel accessed attribute: {name}")

    def __setattr__(self, name: str, value: object) -> None:
        raise AssertionError(f"Strict database sentinel mutated attribute: {name}")

    def __delattr__(self, name: str) -> None:
        raise AssertionError(f"Strict database sentinel deleted attribute: {name}")

    def __call__(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("Strict database sentinel called unexpectedly")

    def __eq__(self, other: object) -> bool:
        return self is other


class BlocksReadStatusCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization tests for blocks read handlers."""

    def setUp(self) -> None:
        self.fence = install_p1_00_network_fence()
        self.db = StrictDatabaseSentinel()
        self.current_user = SimpleNamespace(id=7101)
        self.target_user_id = 8202

    @patch("api.routers.blocks.get_block_status", new_callable=AsyncMock)
    async def test_get_my_block_status_delegates_identity_and_returns_service_payload(
        self, mock_get_block_status: AsyncMock
    ) -> None:
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

        denied_before = len(self.fence["denied_connects"])
        redis_before = len(self.fence["live_redis_constructs"])

        result = await get_my_block_status(db=self.db, current_user=self.current_user)

        self.assertEqual(len(self.fence["denied_connects"]), denied_before)
        self.assertEqual(len(self.fence["live_redis_constructs"]), redis_before)

        self.assertIs(result, expected_status)
        mock_get_block_status.assert_awaited_once_with(self.db, self.current_user.id)
        call_args = mock_get_block_status.await_args
        self.assertIs(call_args[0][0], self.db)
        self.assertEqual(call_args[0][1], self.current_user.id)

    @patch("api.routers.blocks.is_blocked_by", new_callable=AsyncMock)
    async def test_check_block_status_returns_directional_identity_result(
        self, mock_is_blocked_by: AsyncMock
    ) -> None:
        mock_is_blocked_by.return_value = True

        denied_before = len(self.fence["denied_connects"])
        redis_before = len(self.fence["live_redis_constructs"])

        result = await check_block_status(
            user_id=self.target_user_id,
            db=self.db,
            current_user=self.current_user,
        )

        self.assertEqual(len(self.fence["denied_connects"]), denied_before)
        self.assertEqual(len(self.fence["live_redis_constructs"]), redis_before)

        self.assertEqual(
            result,
            {
                "user_id": self.target_user_id,
                "is_blocked_by_me": True,
            },
        )
        mock_is_blocked_by.assert_awaited_once_with(
            self.db, self.current_user.id, self.target_user_id
        )
        call_args = mock_is_blocked_by.await_args
        self.assertIs(call_args[0][0], self.db)
        self.assertEqual(call_args[0][1], self.current_user.id)
        self.assertEqual(call_args[0][2], self.target_user_id)


if __name__ == "__main__":
    unittest.main()
