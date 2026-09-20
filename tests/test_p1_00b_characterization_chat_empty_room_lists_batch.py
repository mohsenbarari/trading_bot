"""Hermetic characterization batch tests for chat seed handlers empty room lists.

Characterizes the empty success paths of api.routers.chat.get_groups and
api.routers.chat.get_channels under active P1-00b Assignment WA-P1-00B-FREEZE-20260904-001.

Disclaimer:
This direct characterization does not update the seed registry or inventories,
resolve either canonical High blocker, promote evidence, change product behavior,
or complete P1-00b. P1-00b remains IN_PROGRESS and
P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE plus
P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN remain open.
"""

from __future__ import annotations

import contextlib
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (  # noqa: E402
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import get_channels, get_groups  # noqa: E402


@contextlib.contextmanager
def empty_room_lists_lifecycle(user_id: int = 42):
    """Shared fixture lifecycle for empty group and channel room listing scenarios."""
    current_user = SimpleNamespace(id=user_id)
    db_sentinel = object()

    fence = install_p1_00_network_fence()
    denied_before = len(fence["denied_connects"])
    redis_before = len(fence["live_redis_constructs"])

    with (
        patch(
            "api.routers.chat.list_groups_for_user",
            new_callable=AsyncMock,
        ) as mock_list_groups,
        patch(
            "api.routers.chat.list_manageable_channels",
            new_callable=AsyncMock,
        ) as mock_list_channels,
    ):
        mock_list_groups.return_value = []
        mock_list_channels.return_value = []

        fixture = SimpleNamespace(
            current_user=current_user,
            db=db_sentinel,
            mock_list_groups_for_user=mock_list_groups,
            mock_list_manageable_channels=mock_list_channels,
            fence=fence,
            denied_before=denied_before,
            redis_before=redis_before,
        )
        yield fixture


class ChatEmptyRoomListsBatchCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization test suite for chat empty room listing seed handlers."""

    empty_room_lists_lifecycle = staticmethod(empty_room_lists_lifecycle)

    async def test_get_groups_empty_list_seed(self) -> None:
        with self.empty_room_lists_lifecycle() as fixture:
            result = await get_groups(
                current_user=fixture.current_user,
                db=fixture.db,
            )

            fixture.mock_list_groups_for_user.assert_awaited_once_with(
                fixture.db,
                user_id=42,
            )
            self.assertIs(fixture.mock_list_groups_for_user.call_args[0][0], fixture.db)
            fixture.mock_list_manageable_channels.assert_not_called()

            self.assertIsInstance(result, list)
            self.assertEqual(result, [])

            self.assertEqual(
                len(fixture.fence["denied_connects"]),
                fixture.denied_before,
            )
            self.assertEqual(
                len(fixture.fence["live_redis_constructs"]),
                fixture.redis_before,
            )

    async def test_get_channels_empty_list_seed(self) -> None:
        with self.empty_room_lists_lifecycle() as fixture:
            result = await get_channels(
                current_user=fixture.current_user,
                db=fixture.db,
            )

            fixture.mock_list_manageable_channels.assert_awaited_once_with(
                fixture.db,
            )
            self.assertIs(fixture.mock_list_manageable_channels.call_args[0][0], fixture.db)
            fixture.mock_list_groups_for_user.assert_not_called()

            self.assertIsInstance(result, list)
            self.assertEqual(result, [])

            self.assertEqual(
                len(fixture.fence["denied_connects"]),
                fixture.denied_before,
            )
            self.assertEqual(
                len(fixture.fence["live_redis_constructs"]),
                fixture.redis_before,
            )


if __name__ == "__main__":
    unittest.main()
