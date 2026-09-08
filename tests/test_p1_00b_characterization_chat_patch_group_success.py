"""Direct P1-00b handler characterization under active Assignment WA-P1-00B-FREEZE-20260904-001.

This is direct P1-00b handler characterization under the active Assignment,
not durable persistence, origin interaction, executed-seed registration,
evidence promotion or Stage completion.
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

from api.routers.chat import patch_group  # noqa: E402
from api.routers.chat_schemas import (  # noqa: E402
    GroupRoomRead,
    GroupUpdateRequest,
)
from core.enums import ChatMemberRole, ChatType  # noqa: E402


class CharacterizeChatPatchGroupSuccessTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing patch_group success path."""

    @patch("api.routers.chat.count_active_chat_members", new_callable=AsyncMock)
    @patch("api.routers.chat.update_group_chat", new_callable=AsyncMock)
    @patch("api.routers.chat.resolve_owned_avatar_file_id", new_callable=AsyncMock)
    @patch("api.routers.chat.get_active_group_admin_or_403", new_callable=AsyncMock)
    @patch("api.routers.chat.get_group_or_404", new_callable=AsyncMock)
    async def test_patch_group_success_updates_and_returns_group_room_read(
        self,
        mock_get_group_or_404: AsyncMock,
        mock_get_active_group_admin_or_403: AsyncMock,
        mock_resolve_owned_avatar_file_id: AsyncMock,
        mock_update_group_chat: AsyncMock,
        mock_count_active_chat_members: AsyncMock,
    ) -> None:
        chat_id = 321
        actor_id = 42
        creator_id = 99
        non_default_max_members = 150
        active_member_count = 12
        fixed_created_at = datetime(2026, 9, 8, 10, 30, 0, tzinfo=timezone.utc)

        raw_avatar_id = "avatar-raw-request-123"
        resolved_avatar_id = "avatar-resolved-uuid-456"

        fake_db = object()
        current_user = SimpleNamespace(id=actor_id)
        admin_member = SimpleNamespace(role=ChatMemberRole.ADMIN)

        request_data = GroupUpdateRequest(
            title="Updated Group Title",
            description="Updated group description",
            avatar_file_id=raw_avatar_id,
        )

        original_group = SimpleNamespace(
            id=chat_id,
            type=ChatType.GROUP,
            title="Original Group Title",
            description="Original group description",
            avatar_file_id="avatar-original-789",
            created_by_id=creator_id,
            max_members=non_default_max_members,
            created_at=fixed_created_at,
        )

        updated_group = SimpleNamespace(
            id=chat_id,
            type=ChatType.GROUP,
            title=request_data.title,
            description=request_data.description,
            avatar_file_id=resolved_avatar_id,
            created_by_id=creator_id,
            max_members=non_default_max_members,
            created_at=fixed_created_at,
        )

        mock_get_group_or_404.return_value = original_group
        mock_get_active_group_admin_or_403.return_value = admin_member
        mock_resolve_owned_avatar_file_id.return_value = resolved_avatar_id
        mock_update_group_chat.return_value = updated_group
        mock_count_active_chat_members.return_value = active_member_count

        result = await patch_group(
            chat_id=chat_id,
            data=request_data,
            current_user=current_user,
            db=fake_db,
        )

        mock_get_group_or_404.assert_awaited_once_with(fake_db, chat_id)
        mock_get_active_group_admin_or_403.assert_awaited_once_with(
            fake_db,
            chat=original_group,
            user_id=actor_id,
        )
        mock_resolve_owned_avatar_file_id.assert_awaited_once_with(
            fake_db,
            actor_id=actor_id,
            avatar_file_id=request_data.avatar_file_id,
        )
        mock_update_group_chat.assert_awaited_once_with(
            fake_db,
            chat=original_group,
            title=request_data.title,
            description=request_data.description,
            avatar_file_id=resolved_avatar_id,
        )
        mock_count_active_chat_members.assert_awaited_once_with(
            fake_db,
            updated_group.id,
        )

        self.assertIsInstance(result, GroupRoomRead)
        self.assertEqual(result.id, chat_id)
        self.assertEqual(result.type, ChatType.GROUP)
        self.assertEqual(result.title, "Updated Group Title")
        self.assertEqual(result.description, "Updated group description")
        self.assertEqual(result.avatar_file_id, resolved_avatar_id)
        self.assertEqual(result.created_by_id, creator_id)
        self.assertEqual(result.member_count, active_member_count)
        self.assertEqual(result.max_members, non_default_max_members)
        self.assertEqual(result.created_at, fixed_created_at)
        self.assertEqual(result.current_user_role, "admin")

        self.assertNotEqual(result.title, original_group.title)
        self.assertNotEqual(result.description, original_group.description)
        self.assertNotEqual(result.avatar_file_id, original_group.avatar_file_id)
        self.assertNotEqual(result.avatar_file_id, request_data.avatar_file_id)


if __name__ == "__main__":
    unittest.main()
