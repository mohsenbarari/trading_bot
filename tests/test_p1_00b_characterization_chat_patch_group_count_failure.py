"""Direct characterization of patch_group count_active_chat_members failure under P1-00b.

This is direct characterization only under P1-00b and WA-P1-00B-FREEZE-20260904-001:
no executed-seed registration, canonical blocker reduction, evidence promotion,
durable persistence, origin-interaction proof or Stage completion.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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


class CharacterizeChatPatchGroupCountFailureTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit test characterizing patch_group count_active_chat_members failure boundary."""

    @patch("api.routers.chat.GroupRoomRead")
    @patch("api.routers.chat.count_active_chat_members", new_callable=AsyncMock)
    @patch("api.routers.chat.update_group_chat", new_callable=AsyncMock)
    @patch("api.routers.chat.resolve_owned_avatar_file_id", new_callable=AsyncMock)
    @patch("api.routers.chat.get_active_group_admin_or_403", new_callable=AsyncMock)
    @patch("api.routers.chat.get_group_or_404", new_callable=AsyncMock)
    async def test_patch_group_count_active_chat_members_failure(
        self,
        mock_get_group_or_404: AsyncMock,
        mock_get_active_group_admin_or_403: AsyncMock,
        mock_resolve_owned_avatar_file_id: AsyncMock,
        mock_update_group_chat: AsyncMock,
        mock_count_active_chat_members: AsyncMock,
        mock_group_room_read: MagicMock,
    ) -> None:
        # Note: update_group_chat is mocked here; this characterizes count_active_chat_members
        # failure handling and does not prove real-database commit, rollback, or durability.
        chat_id = 321
        returned_chat_id = 654
        actor_id = 42
        resolved_avatar_id = "avatar-resolved-uuid-boundary"

        fake_db = object()
        current_user = SimpleNamespace(id=actor_id)
        fake_admin = SimpleNamespace(role=ChatMemberRole.ADMIN)

        original_group = SimpleNamespace(
            id=chat_id,
            type=ChatType.GROUP,
            title="Original Group Title",
            description="Original group description",
            avatar_file_id="avatar-original-789",
        )
        updated_group = SimpleNamespace(
            id=returned_chat_id,
            type=ChatType.GROUP,
            title="Boundary Engineering Team",
            description="Internal engineering coordination channel",
            avatar_file_id=resolved_avatar_id,
        )

        self.assertIsNot(original_group, updated_group)
        self.assertNotEqual(original_group.id, updated_group.id)

        sentinel_exc = RuntimeError("count_active_chat_members failure during patch_group")

        call_order: list[str] = []

        async def _fake_get_group(db: object, chat_id_arg: int) -> object:
            call_order.append("get_group_or_404")
            return original_group

        async def _fake_get_admin(db: object, *, chat: object, user_id: int) -> object:
            call_order.append("get_active_group_admin_or_403")
            return fake_admin

        async def _fake_resolve_avatar(
            db: object,
            *,
            actor_id: int,
            avatar_file_id: str | None,
        ) -> str | None:
            call_order.append("resolve_owned_avatar_file_id")
            return resolved_avatar_id

        async def _fake_update_group(
            db: object,
            *,
            chat: object,
            title: str,
            description: str | None = None,
            avatar_file_id: str | None = None,
        ) -> object:
            call_order.append("update_group_chat")
            return updated_group

        async def _fake_count_members(db: object, chat_id_arg: int) -> int:
            call_order.append("count_active_chat_members")
            raise sentinel_exc

        mock_get_group_or_404.side_effect = _fake_get_group
        mock_get_active_group_admin_or_403.side_effect = _fake_get_admin
        mock_resolve_owned_avatar_file_id.side_effect = _fake_resolve_avatar
        mock_update_group_chat.side_effect = _fake_update_group
        mock_count_active_chat_members.side_effect = _fake_count_members

        request_data = GroupUpdateRequest(
            title="   Boundary Engineering Team   ",
            description="   Internal engineering coordination channel   ",
            avatar_file_id="   avatar-raw-uuid-boundary   ",
        )

        self.assertEqual(request_data.title, "Boundary Engineering Team")
        self.assertEqual(request_data.description, "Internal engineering coordination channel")
        self.assertEqual(request_data.avatar_file_id, "avatar-raw-uuid-boundary")

        with self.assertRaises(RuntimeError) as ctx:
            await patch_group(
                chat_id=chat_id,
                data=request_data,
                current_user=current_user,
                db=fake_db,
            )

        self.assertIs(ctx.exception, sentinel_exc)
        self.assertEqual(
            call_order,
            [
                "get_group_or_404",
                "get_active_group_admin_or_403",
                "resolve_owned_avatar_file_id",
                "update_group_chat",
                "count_active_chat_members",
            ],
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
            avatar_file_id="avatar-raw-uuid-boundary",
        )
        mock_update_group_chat.assert_awaited_once_with(
            fake_db,
            chat=original_group,
            title="Boundary Engineering Team",
            description="Internal engineering coordination channel",
            avatar_file_id=resolved_avatar_id,
        )
        mock_count_active_chat_members.assert_awaited_once_with(
            fake_db,
            updated_group.id,
        )
        self.assertEqual(mock_count_active_chat_members.await_count, 1)
        mock_group_room_read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
