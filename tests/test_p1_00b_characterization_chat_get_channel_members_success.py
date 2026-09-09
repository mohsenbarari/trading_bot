"""Direct-characterization tests for api.routers.chat.get_channel_members under P1-00b.

Characterizes get_channel_members success response mapping under WA-P1-00B-FREEZE-20260904-001.
Direct-handler characterization only: no HTTP/auth, durable overlap/reload,
executed-seed promotion, canonical blocker reduction, or Stage acceptance/completion
claim; P1-00b stays IN_PROGRESS.
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import get_channel_members  # noqa: E402
from api.routers.chat_schemas import ChannelMemberRead  # noqa: E402
from core.enums import ChatMemberRole  # noqa: E402
from core.services.chat_role_badge_service import ChatRoleBadge  # noqa: E402


class CharacterizeChatGetChannelMembersSuccessTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing get_channel_members success response mapping."""

    @patch("api.routers.chat.load_chat_role_badges", new_callable=AsyncMock)
    @patch("api.routers.chat.list_channel_members", new_callable=AsyncMock)
    @patch("api.routers.chat.get_channel_or_404", new_callable=AsyncMock)
    async def test_get_channel_members_success_mapping(
        self,
        mock_get_channel_or_404: AsyncMock,
        mock_list_channel_members: AsyncMock,
        mock_load_chat_role_badges: AsyncMock,
    ) -> None:
        fake_db = object()
        channel_sentinel = object()
        current_user = SimpleNamespace(id=42)
        chat_id = 99

        joined_at_1 = datetime(2026, 1, 15, 8, 30, tzinfo=timezone.utc)
        joined_at_2 = datetime(2026, 2, 20, 14, 45, tzinfo=timezone.utc)

        member_rows = [
            SimpleNamespace(
                user_id=2002,
                account_name="admin_synthetic",
                full_name="Admin Synthetic",
                mobile_number="+989120000001",
                avatar_file_id="avatar_synthetic_2002",
                role=ChatMemberRole.ADMIN,
                joined_at=joined_at_1,
                is_channel_creator=True,
                customer_management_name="synthetic_management_name",
            ),
            SimpleNamespace(
                user_id=1001,
                account_name="member_synthetic",
                full_name="Member Synthetic",
                mobile_number="+989120000002",
                role=ChatMemberRole.MEMBER,
                joined_at=joined_at_2,
                is_channel_creator=False,
            ),
        ]

        role_badges = {
            2002: ChatRoleBadge(
                kind="accountant",
                label="حسابدار",
                accountant_owner_name="Owner Company",
                accountant_owner_label="مالک",
            )
        }

        call_order: list[str] = []

        def get_channel_side_effect(*args: Any, **kwargs: Any) -> Any:
            call_order.append("get_channel_or_404")
            mock_list_channel_members.assert_not_called()
            mock_load_chat_role_badges.assert_not_called()
            return channel_sentinel

        def list_channel_members_side_effect(*args: Any, **kwargs: Any) -> Any:
            call_order.append("list_channel_members")
            mock_get_channel_or_404.assert_awaited_once_with(fake_db, chat_id)
            mock_load_chat_role_badges.assert_not_called()
            return member_rows

        def load_chat_role_badges_side_effect(*args: Any, **kwargs: Any) -> Any:
            call_order.append("load_chat_role_badges")
            mock_get_channel_or_404.assert_awaited_once_with(fake_db, chat_id)
            mock_list_channel_members.assert_awaited_once_with(
                fake_db, chat=channel_sentinel
            )
            return role_badges

        mock_get_channel_or_404.side_effect = get_channel_side_effect
        mock_list_channel_members.side_effect = list_channel_members_side_effect
        mock_load_chat_role_badges.side_effect = load_chat_role_badges_side_effect

        results = await get_channel_members(
            chat_id=chat_id,
            current_user=current_user,
            db=fake_db,
        )

        self.assertEqual(
            call_order,
            ["get_channel_or_404", "list_channel_members", "load_chat_role_badges"],
        )
        mock_get_channel_or_404.assert_awaited_once_with(fake_db, chat_id)
        mock_list_channel_members.assert_awaited_once_with(
            fake_db, chat=channel_sentinel
        )
        mock_load_chat_role_badges.assert_awaited_once_with(fake_db, {2002, 1001})

        self.assertEqual(len(results), 2)
        self.assertIsInstance(results[0], ChannelMemberRead)
        self.assertIsInstance(results[1], ChannelMemberRead)

        self.assertEqual(results[0].user_id, 2002)
        self.assertEqual(results[0].account_name, "admin_synthetic")
        self.assertEqual(results[0].full_name, "Admin Synthetic")
        self.assertEqual(results[0].mobile_number, "+989120000001")
        self.assertEqual(results[0].avatar_file_id, "avatar_synthetic_2002")
        self.assertEqual(results[0].role, "admin")
        self.assertEqual(results[0].joined_at, joined_at_1)
        self.assertIs(results[0].is_channel_creator, True)
        self.assertEqual(results[0].chat_role_kind, "accountant")
        self.assertEqual(results[0].chat_role_label, "حسابدار")
        self.assertEqual(results[0].chat_accountant_owner_name, "Owner Company")
        self.assertEqual(results[0].chat_accountant_owner_label, "مالک")
        self.assertEqual(
            results[0].customer_management_name, "synthetic_management_name"
        )

        self.assertEqual(results[1].user_id, 1001)
        self.assertEqual(results[1].account_name, "member_synthetic")
        self.assertEqual(results[1].full_name, "Member Synthetic")
        self.assertEqual(results[1].mobile_number, "+989120000002")
        self.assertIsNone(results[1].avatar_file_id)
        self.assertEqual(results[1].role, "member")
        self.assertEqual(results[1].joined_at, joined_at_2)
        self.assertIs(results[1].is_channel_creator, False)
        self.assertIsNone(results[1].chat_role_kind)
        self.assertIsNone(results[1].chat_role_label)
        self.assertIsNone(results[1].chat_accountant_owner_name)
        self.assertIsNone(results[1].chat_accountant_owner_label)
        self.assertIsNone(results[1].customer_management_name)

        expected_member_0 = ChannelMemberRead(
            user_id=2002,
            account_name="admin_synthetic",
            full_name="Admin Synthetic",
            mobile_number="+989120000001",
            avatar_file_id="avatar_synthetic_2002",
            role="admin",
            joined_at=joined_at_1,
            is_channel_creator=True,
            chat_role_kind="accountant",
            chat_role_label="حسابدار",
            chat_accountant_owner_name="Owner Company",
            chat_accountant_owner_label="مالک",
            customer_management_name="synthetic_management_name",
        )
        expected_member_1 = ChannelMemberRead(
            user_id=1001,
            account_name="member_synthetic",
            full_name="Member Synthetic",
            mobile_number="+989120000002",
            avatar_file_id=None,
            role="member",
            joined_at=joined_at_2,
            is_channel_creator=False,
            chat_role_kind=None,
            chat_role_label=None,
            chat_accountant_owner_name=None,
            chat_accountant_owner_label=None,
            customer_management_name=None,
        )
        self.assertEqual(results, [expected_member_0, expected_member_1])


if __name__ == "__main__":
    unittest.main()
