"""Direct-characterization tests for api.routers.chat.get_group_message_seen_members.

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

from pydantic import ValidationError  # noqa: E402
from api.routers.chat import get_group_message_seen_members  # noqa: E402
from api.routers.chat_schemas import GroupMessageSeenRead  # noqa: E402
from core.enums import ChatType  # noqa: E402
from core.services.chat_room_service import GroupMessageSeenSummary  # noqa: E402


class CharacterizeChatGetGroupMessageSeenMembersTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing get_group_message_seen_members."""

    @patch("api.routers.chat.list_group_message_seen_members", new_callable=AsyncMock)
    @patch("api.routers.chat.get_room_or_404", new_callable=AsyncMock)
    async def test_get_group_message_seen_members_two_row_success(
        self,
        mock_get_room_or_404: AsyncMock,
        mock_list_group_message_seen_members: AsyncMock,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=5)
        chat_id = 70
        message_id = 42
        chat = SimpleNamespace(id=chat_id, type=ChatType.GROUP)

        dt1 = datetime(2026, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
        dt2 = datetime(2026, 6, 1, 11, 30, 0, tzinfo=timezone.utc)

        item1 = GroupMessageSeenSummary(
            user_id=101,
            account_name="alice_reader",
            full_name="Alice Reader",
            avatar_file_id="avatar-alice-101",
            customer_management_name="Client Alpha",
            seen_at=dt1,
        )
        item2 = GroupMessageSeenSummary(
            user_id=102,
            account_name="bob_reader",
            full_name=None,
            avatar_file_id=None,
            customer_management_name=None,
            seen_at=dt2,
        )

        mock_get_room_or_404.return_value = chat
        mock_list_group_message_seen_members.return_value = [item1, item2]

        result = await get_group_message_seen_members(
            chat_id=chat_id,
            message_id=message_id,
            current_user=current_user,
            db=fake_db,
        )

        mock_get_room_or_404.assert_awaited_once_with(fake_db, chat_id)
        mock_list_group_message_seen_members.assert_awaited_once_with(
            fake_db,
            chat=chat,
            message_id=message_id,
            requester_id=current_user.id,
        )

        self.assertEqual(len(result), 2)
        self.assertEqual([item.user_id for item in result], [101, 102])
        self.assertEqual([item.account_name for item in result], ["alice_reader", "bob_reader"])

        self.assertIsInstance(result[0], GroupMessageSeenRead)
        self.assertEqual(result[0].user_id, 101)
        self.assertIsInstance(result[0].user_id, int)
        self.assertEqual(result[0].account_name, "alice_reader")
        self.assertIsInstance(result[0].account_name, str)
        self.assertEqual(result[0].full_name, "Alice Reader")
        self.assertIsInstance(result[0].full_name, str)
        self.assertEqual(result[0].avatar_file_id, "avatar-alice-101")
        self.assertIsInstance(result[0].avatar_file_id, str)
        self.assertEqual(result[0].customer_management_name, "Client Alpha")
        self.assertIsInstance(result[0].customer_management_name, str)
        self.assertEqual(result[0].seen_at, dt1)
        self.assertIsInstance(result[0].seen_at, datetime)
        self.assertIsNotNone(result[0].seen_at.tzinfo)
        self.assertEqual(result[0].seen_at.tzinfo, timezone.utc)

        self.assertIsInstance(result[1], GroupMessageSeenRead)
        self.assertEqual(result[1].user_id, 102)
        self.assertIsInstance(result[1].user_id, int)
        self.assertEqual(result[1].account_name, "bob_reader")
        self.assertIsInstance(result[1].account_name, str)
        self.assertIsNone(result[1].full_name)
        self.assertIsNone(result[1].avatar_file_id)
        self.assertIsNone(result[1].customer_management_name)
        self.assertEqual(result[1].seen_at, dt2)
        self.assertIsInstance(result[1].seen_at, datetime)
        self.assertIsNotNone(result[1].seen_at.tzinfo)
        self.assertEqual(result[1].seen_at.tzinfo, timezone.utc)

    @patch("api.routers.chat.list_group_message_seen_members", new_callable=AsyncMock)
    @patch("api.routers.chat.get_room_or_404", new_callable=AsyncMock)
    async def test_get_group_message_seen_members_empty(
        self,
        mock_get_room_or_404: AsyncMock,
        mock_list_group_message_seen_members: AsyncMock,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=9)
        chat_id = 80
        message_id = 43
        chat = SimpleNamespace(id=chat_id, type=ChatType.GROUP)

        mock_get_room_or_404.return_value = chat
        mock_list_group_message_seen_members.return_value = []

        result = await get_group_message_seen_members(
            chat_id=chat_id,
            message_id=message_id,
            current_user=current_user,
            db=fake_db,
        )

        mock_get_room_or_404.assert_awaited_once_with(fake_db, chat_id)
        mock_list_group_message_seen_members.assert_awaited_once_with(
            fake_db,
            chat=chat,
            message_id=message_id,
            requester_id=current_user.id,
        )

        self.assertEqual(result, [])
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 0)

    @patch("api.routers.chat.list_group_message_seen_members", new_callable=AsyncMock)
    @patch("api.routers.chat.get_room_or_404", new_callable=AsyncMock)
    async def test_get_group_message_seen_members_invalid_service_item_propagates_validation_error(
        self,
        mock_get_room_or_404: AsyncMock,
        mock_list_group_message_seen_members: AsyncMock,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=15)
        chat_id = 90
        message_id = 44
        chat = SimpleNamespace(id=chat_id, type=ChatType.GROUP)

        invalid_item = SimpleNamespace(
            user_id="invalid-user-id-not-int",
            account_name="charlie_reader",
            full_name=None,
            avatar_file_id=None,
            customer_management_name=None,
            seen_at=datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
        )

        mock_get_room_or_404.return_value = chat
        mock_list_group_message_seen_members.return_value = [invalid_item]

        with self.assertRaises(ValidationError) as context:
            await get_group_message_seen_members(
                chat_id=chat_id,
                message_id=message_id,
                current_user=current_user,
                db=fake_db,
            )

        mock_get_room_or_404.assert_awaited_once_with(fake_db, chat_id)
        mock_list_group_message_seen_members.assert_awaited_once_with(
            fake_db,
            chat=chat,
            message_id=message_id,
            requester_id=current_user.id,
        )

        errors = context.exception.errors()
        self.assertTrue(any("user_id" in err.get("loc", ()) for err in errors))
        self.assertIn("user_id", str(context.exception))


if __name__ == "__main__":
    unittest.main()
