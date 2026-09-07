"""Direct-characterization tests for api.routers.chat.get_groups.

This is direct characterization only and does not update the executed-seed registry,
reduce a canonical blocker, promote evidence, or mark P1-00b complete.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import get_groups  # noqa: E402
from api.routers.chat_schemas import GroupRoomRead  # noqa: E402
from core.enums import ChatMemberRole, ChatType  # noqa: E402


class CharacterizeChatGetGroupsTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing get_groups."""

    @patch("api.routers.chat.list_groups_for_user", new_callable=AsyncMock)
    async def test_get_groups_success(
        self,
        mock_list_groups_for_user: AsyncMock,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42)
        dt1 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        dt2 = datetime(2026, 1, 2, 15, 30, 0, tzinfo=timezone.utc)

        group1 = SimpleNamespace(
            id=101,
            type=ChatType.GROUP,
            title="Alpha Group",
            description="Alpha description",
            avatar_file_id="avatar-abc-123",
            created_by_id=7,
            member_count=3,
            max_members=50,
            created_at=dt1,
            current_user_role=ChatMemberRole.ADMIN,
        )
        group2 = SimpleNamespace(
            id=102,
            type=ChatType.GROUP,
            title="Beta Group",
            description=None,
            # avatar_file_id intentionally omitted to test _optional_attr handling
            created_by_id=8,
            member_count=10,
            max_members=100,
            created_at=dt2,
            current_user_role=None,
        )

        mock_list_groups_for_user.return_value = [group1, group2]

        result = await get_groups(
            current_user=current_user,
            db=fake_db,
        )

        mock_list_groups_for_user.assert_awaited_once_with(
            fake_db, user_id=current_user.id
        )
        self.assertEqual(len(result), 2)

        self.assertIsInstance(result[0], GroupRoomRead)
        self.assertEqual(result[0].id, 101)
        self.assertEqual(result[0].type, ChatType.GROUP)
        self.assertEqual(result[0].title, "Alpha Group")
        self.assertEqual(result[0].description, "Alpha description")
        self.assertEqual(result[0].avatar_file_id, "avatar-abc-123")
        self.assertEqual(result[0].created_by_id, 7)
        self.assertEqual(result[0].member_count, 3)
        self.assertEqual(result[0].max_members, 50)
        self.assertEqual(result[0].created_at, dt1)
        self.assertEqual(result[0].current_user_role, "admin")

        self.assertIsInstance(result[1], GroupRoomRead)
        self.assertEqual(result[1].id, 102)
        self.assertEqual(result[1].type, ChatType.GROUP)
        self.assertEqual(result[1].title, "Beta Group")
        self.assertIsNone(result[1].description)
        self.assertIsNone(result[1].avatar_file_id)
        self.assertEqual(result[1].created_by_id, 8)
        self.assertEqual(result[1].member_count, 10)
        self.assertEqual(result[1].max_members, 100)
        self.assertEqual(result[1].created_at, dt2)
        self.assertIsNone(result[1].current_user_role)

    @patch("api.routers.chat.list_groups_for_user", new_callable=AsyncMock)
    async def test_get_groups_empty(
        self,
        mock_list_groups_for_user: AsyncMock,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42)
        mock_list_groups_for_user.return_value = []

        result = await get_groups(
            current_user=current_user,
            db=fake_db,
        )

        self.assertEqual(result, [])
        mock_list_groups_for_user.assert_awaited_once_with(
            fake_db, user_id=current_user.id
        )

    @patch("api.routers.chat.list_groups_for_user", new_callable=AsyncMock)
    async def test_get_groups_dependency_failure(
        self,
        mock_list_groups_for_user: AsyncMock,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42)
        sentinel_error = RuntimeError("service lookup failed")
        mock_list_groups_for_user.side_effect = sentinel_error

        with self.assertRaises(RuntimeError) as context:
            await get_groups(
                current_user=current_user,
                db=fake_db,
            )

        self.assertIs(context.exception, sentinel_error)
        mock_list_groups_for_user.assert_awaited_once_with(
            fake_db, user_id=current_user.id
        )


if __name__ == "__main__":
    unittest.main()
