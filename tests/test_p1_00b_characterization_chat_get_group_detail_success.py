"""Direct-characterization tests for api.routers.chat.get_group_detail success path under P1-00b.

Characterizes successful response-mapping path under WA-P1-00B-FREEZE-20260904-001.
This is direct characterization only and does not update the executed-seed registry,
reduce a canonical blocker, promote evidence, or mark P1-00b complete.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import get_group_detail  # noqa: E402
from api.routers.chat_schemas import (  # noqa: E402
    GroupDetailRead,
    GroupMemberRead,
    GroupRoomRead,
)
from core.enums import ChatMemberRole, ChatType  # noqa: E402


class CharacterizeChatGetGroupDetailSuccessTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing get_group_detail success response mapping."""

    @patch("api.routers.chat.load_chat_role_badges", new_callable=AsyncMock)
    @patch("api.routers.chat.list_group_members", new_callable=AsyncMock)
    @patch("api.routers.chat.count_active_chat_members", new_callable=AsyncMock)
    @patch("api.routers.chat.get_active_group_member_or_403", new_callable=AsyncMock)
    @patch("api.routers.chat.get_group_or_404", new_callable=AsyncMock)
    async def test_get_group_detail_success_mapping(
        self,
        mock_get_group_or_404: AsyncMock,
        mock_get_active_group_member_or_403: AsyncMock,
        mock_count_active_chat_members: AsyncMock,
        mock_list_group_members: AsyncMock,
        mock_load_chat_role_badges: AsyncMock,
    ) -> None:
        fake_db = object()
        group_created_at = datetime(2026, 9, 1, 12, 0, 0, tzinfo=timezone.utc)
        member_joined_at = datetime(2026, 9, 2, 8, 30, 0, tzinfo=timezone.utc)

        fake_group = SimpleNamespace(
            id=321,
            type=ChatType.GROUP,
            title=None,
            description="Hermetic test group description",
            created_by_id=10,
            max_members=None,
            created_at=group_created_at,
        )

        current_membership = SimpleNamespace(
            role=ChatMemberRole.ADMIN,
        )

        fake_badge = SimpleNamespace(
            kind="accountant",
            label="Official Accountant",
            accountant_owner_name="Owner Company",
            accountant_owner_label="Account Owner",
        )

        member_user = SimpleNamespace(
            id=77,
            account_name="alice_member",
            full_name="Alice Member",
            mobile_number="+989123456789",
            avatar_file_id="avatar_member_001",
            customer_management_name="Client Support Specialist",
        )

        fake_member = SimpleNamespace(
            user_id=77,
            account_name="alice_member",
            full_name="Alice Member",
            mobile_number="+989123456789",
            avatar_file_id="avatar_member_001",
            role=ChatMemberRole.MEMBER,
            joined_at=member_joined_at,
            is_group_creator=False,
            chat_role_kind="accountant",
            chat_role_label="Official Accountant",
            chat_accountant_owner_name="Owner Company",
            chat_accountant_owner_label="Account Owner",
            customer_management_name="Client Support Specialist",
            user=member_user,
        )

        mock_get_group_or_404.return_value = fake_group
        mock_get_active_group_member_or_403.return_value = current_membership
        mock_count_active_chat_members.return_value = 1
        mock_list_group_members.return_value = [fake_member]
        mock_load_chat_role_badges.return_value = {77: fake_badge}

        result = await get_group_detail(
            chat_id=321,
            current_user=SimpleNamespace(id=42),
            db=fake_db,
        )

        mock_get_group_or_404.assert_awaited_once_with(fake_db, 321)
        mock_get_active_group_member_or_403.assert_awaited_once_with(
            fake_db, chat=fake_group, user_id=42
        )
        mock_count_active_chat_members.assert_awaited_once_with(fake_db, 321)
        mock_list_group_members.assert_awaited_once_with(fake_db, chat=fake_group)
        mock_load_chat_role_badges.assert_awaited_once_with(fake_db, {77})

        self.assertIsInstance(result, GroupDetailRead)
        self.assertIsInstance(result.group, GroupRoomRead)
        self.assertEqual(result.group.id, 321)
        self.assertEqual(result.group.type, ChatType.GROUP)
        self.assertEqual(result.group.title, "")
        self.assertEqual(result.group.description, "Hermetic test group description")
        self.assertIsNone(result.group.avatar_file_id)
        self.assertEqual(result.group.created_by_id, 10)
        self.assertEqual(result.group.member_count, 1)
        self.assertEqual(result.group.max_members, 50)
        self.assertEqual(result.group.created_at, group_created_at)
        self.assertEqual(result.group.current_user_role, "admin")

        self.assertEqual(len(result.members), 1)
        member = result.members[0]
        self.assertIsInstance(member, GroupMemberRead)
        self.assertEqual(member.user_id, 77)
        self.assertEqual(member.account_name, "alice_member")
        self.assertEqual(member.full_name, "Alice Member")
        self.assertEqual(member.mobile_number, "+989123456789")
        self.assertEqual(member.avatar_file_id, "avatar_member_001")
        self.assertEqual(member.role, "member")
        self.assertEqual(member.joined_at, member_joined_at)
        self.assertFalse(member.is_group_creator)
        self.assertEqual(member.chat_role_kind, "accountant")
        self.assertEqual(member.chat_role_label, "Official Accountant")
        self.assertEqual(member.chat_accountant_owner_name, "Owner Company")
        self.assertEqual(member.chat_accountant_owner_label, "Account Owner")
        self.assertEqual(member.customer_management_name, "Client Support Specialist")


if __name__ == "__main__":
    unittest.main()
