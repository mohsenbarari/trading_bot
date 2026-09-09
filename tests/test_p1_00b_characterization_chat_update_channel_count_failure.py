"""Direct-characterization tests for api.routers.chat.update_channel under P1-00b.

Characterizes count_active_chat_members failure boundary under WA-P1-00B-FREEZE-20260904-001.
This is direct characterization only and does not update the executed-seed registry,
reduce a canonical blocker, promote evidence, or mark P1-00b complete.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import update_channel  # noqa: E402
from api.routers.chat_schemas import ChannelUpdateRequest  # noqa: E402


class CharacterizeChatUpdateChannelCountFailureTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing update_channel member-count failure boundary."""

    @patch("api.routers.chat.ChannelRoomRead")
    @patch("api.routers.chat.count_active_chat_members", new_callable=AsyncMock)
    @patch("api.routers.chat.update_manageable_channel_metadata", new_callable=AsyncMock)
    @patch("api.routers.chat.resolve_owned_avatar_file_id", new_callable=AsyncMock)
    @patch("api.routers.chat.get_channel_or_404", new_callable=AsyncMock)
    async def test_update_channel_raises_when_count_active_chat_members_fails(
        self,
        mock_get_channel_or_404: AsyncMock,
        mock_resolve_owned_avatar_file_id: AsyncMock,
        mock_update_manageable_channel_metadata: AsyncMock,
        mock_count_active_chat_members: AsyncMock,
        mock_channel_room_read: MagicMock,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42)
        chat_id = 99
        original_channel = SimpleNamespace(id=chat_id)
        updated_channel = SimpleNamespace(id=777)
        resolved_avatar = "avatar-file-resolved-789"

        self.assertNotEqual(original_channel.id, updated_channel.id)
        self.assertNotEqual(chat_id, updated_channel.id)

        raw_title = "   Padded Channel Title   "
        raw_description = "   Padded Channel Description   "
        raw_avatar_file_id = "   avatar-file-synthetic-123   "
        request_data = ChannelUpdateRequest(
            title=raw_title,
            description=raw_description,
            avatar_file_id=raw_avatar_file_id,
        )
        self.assertEqual(request_data.title, "Padded Channel Title")
        self.assertEqual(request_data.description, "Padded Channel Description")
        self.assertEqual(request_data.avatar_file_id, "avatar-file-synthetic-123")
        self.assertNotEqual(resolved_avatar, request_data.avatar_file_id)

        call_order: list[str] = []
        sentinel_error = RuntimeError("member count failed sentinel")

        async def fake_get_channel(*args: object, **kwargs: object) -> object:
            call_order.append("get_channel_or_404")
            return original_channel

        async def fake_resolve_avatar(*args: object, **kwargs: object) -> str:
            call_order.append("resolve_owned_avatar_file_id")
            return resolved_avatar

        async def fake_update_metadata(*args: object, **kwargs: object) -> object:
            call_order.append("update_manageable_channel_metadata")
            return updated_channel

        async def fake_count_members(*args: object, **kwargs: object) -> int:
            call_order.append("count_active_chat_members")
            raise sentinel_error

        mock_get_channel_or_404.side_effect = fake_get_channel
        mock_resolve_owned_avatar_file_id.side_effect = fake_resolve_avatar
        mock_update_manageable_channel_metadata.side_effect = fake_update_metadata
        mock_count_active_chat_members.side_effect = fake_count_members

        with self.assertRaises(RuntimeError) as context:
            await update_channel(
                chat_id=chat_id,
                data=request_data,
                current_user=current_user,
                db=fake_db,
            )

        self.assertIs(context.exception, sentinel_error)
        self.assertEqual(
            call_order,
            [
                "get_channel_or_404",
                "resolve_owned_avatar_file_id",
                "update_manageable_channel_metadata",
                "count_active_chat_members",
            ],
        )

        mock_get_channel_or_404.assert_awaited_once_with(fake_db, chat_id)
        mock_resolve_owned_avatar_file_id.assert_awaited_once_with(
            fake_db,
            actor_id=current_user.id,
            avatar_file_id=request_data.avatar_file_id,
        )
        mock_update_manageable_channel_metadata.assert_awaited_once_with(
            fake_db,
            chat=original_channel,
            title=request_data.title,
            description=request_data.description,
            avatar_file_id=resolved_avatar,
        )
        mock_count_active_chat_members.assert_awaited_once_with(
            fake_db,
            updated_channel.id,
        )

        mock_channel_room_read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
