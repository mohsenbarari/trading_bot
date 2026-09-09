"""Direct characterization of patch_group avatar resolution failure under P1-00b.

This is direct characterization only under P1-00b and WA-P1-00B-FREEZE-20260904-001,
and does not update executed-seed registry, reduce canonical blockers,
promote evidence, or complete the Stage.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import patch_group  # noqa: E402
from api.routers.chat_schemas import GroupUpdateRequest  # noqa: E402


class CharacterizeChatPatchGroupAvatarFailureTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit test characterizing patch_group avatar resolution failure boundary."""

    @patch("api.routers.chat.count_active_chat_members", new_callable=AsyncMock)
    @patch("api.routers.chat.update_group_chat", new_callable=AsyncMock)
    @patch("api.routers.chat.resolve_owned_avatar_file_id", new_callable=AsyncMock)
    @patch("api.routers.chat.get_active_group_admin_or_403", new_callable=AsyncMock)
    @patch("api.routers.chat.get_group_or_404", new_callable=AsyncMock)
    async def test_patch_group_resolve_owned_avatar_file_id_failure(
        self,
        mock_get_group_or_404: AsyncMock,
        mock_get_active_group_admin_or_403: AsyncMock,
        mock_resolve_owned_avatar_file_id: AsyncMock,
        mock_update_group_chat: AsyncMock,
        mock_count_active_chat_members: AsyncMock,
    ) -> None:
        fake_db = object()
        fake_group = object()
        fake_admin = object()
        current_user = SimpleNamespace(id=42)
        sentinel_exc = RuntimeError("resolve_owned_avatar_file_id failure during patch_group")

        call_order: list[str] = []

        async def _fake_get_group(db: object, chat_id: int) -> object:
            call_order.append("get_group_or_404")
            return fake_group

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
            raise sentinel_exc

        async def _fake_update_group(
            db: object,
            *,
            chat: object,
            title: str,
            description: str | None = None,
            avatar_file_id: str | None = None,
        ) -> object:
            call_order.append("update_group_chat")
            return fake_group

        async def _fake_count_members(db: object, chat_id: int) -> int:
            call_order.append("count_active_chat_members")
            return 1

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
                chat_id=321,
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
            ],
        )
        mock_get_group_or_404.assert_awaited_once_with(fake_db, 321)
        mock_get_active_group_admin_or_403.assert_awaited_once_with(
            fake_db,
            chat=fake_group,
            user_id=42,
        )
        mock_resolve_owned_avatar_file_id.assert_awaited_once_with(
            fake_db,
            actor_id=42,
            avatar_file_id="avatar-raw-uuid-boundary",
        )
        mock_update_group_chat.assert_not_called()
        mock_update_group_chat.assert_not_awaited()
        mock_count_active_chat_members.assert_not_called()
        mock_count_active_chat_members.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
