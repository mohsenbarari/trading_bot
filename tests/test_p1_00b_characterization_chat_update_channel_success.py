"""Direct-characterization tests for api.routers.chat.update_channel under P1-00b.

Characterizes update_channel success path under WA-P1-00B-FREEZE-20260904-001.
This is direct characterization only: no HTTP/authentication, durable overlap,
reload, executed-seed promotion, blocker reduction, Stage acceptance or
completion evidence. Preserves P1-00b IN_PROGRESS and all canonical registries
and project sources.
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

from api.routers.chat import update_channel  # noqa: E402
from api.routers.chat_schemas import (  # noqa: E402
    ChannelRoomRead,
    ChannelUpdateRequest,
)
from core.enums import ChatType  # noqa: E402


class CharacterizeChatUpdateChannelSuccessTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing update_channel success path."""

    @patch("api.routers.chat.count_active_chat_members", new_callable=AsyncMock)
    @patch("api.routers.chat.update_manageable_channel_metadata", new_callable=AsyncMock)
    @patch("api.routers.chat.resolve_owned_avatar_file_id", new_callable=AsyncMock)
    @patch("api.routers.chat.get_channel_or_404", new_callable=AsyncMock)
    async def test_update_channel_success(
        self,
        mock_get_channel_or_404: AsyncMock,
        mock_resolve_owned_avatar_file_id: AsyncMock,
        mock_update_manageable_channel_metadata: AsyncMock,
        mock_count_active_chat_members: AsyncMock,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42)
        chat_id = 99

        raw_title = "   Request Title Padded   "
        raw_description = "   Request Description Padded   "
        raw_avatar_file_id = "   avatar-file-request-123   "

        request_data = ChannelUpdateRequest(
            title=raw_title,
            description=raw_description,
            avatar_file_id=raw_avatar_file_id,
        )
        self.assertEqual(request_data.title, "Request Title Padded")
        self.assertEqual(request_data.description, "Request Description Padded")
        self.assertEqual(request_data.avatar_file_id, "avatar-file-request-123")

        resolved_avatar = "avatar-file-resolved-456"
        self.assertNotEqual(resolved_avatar, request_data.avatar_file_id)

        lookup_channel = SimpleNamespace(
            id=chat_id,
            type=ChatType.CHANNEL,
            title="Lookup Channel Title",
            description="Lookup Channel Description",
            avatar_file_id="avatar-file-lookup-000",
            created_by_id=50,
            is_system=True,
            is_mandatory=False,
            created_at=datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
        )

        persisted_created_at = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
        updated_channel = SimpleNamespace(
            id=777,
            type=ChatType.CHANNEL,
            title="Persisted Channel Title",
            description="Persisted Channel Description",
            avatar_file_id="avatar-file-persisted-789",
            created_by_id=101,
            is_system=False,
            is_mandatory=True,
            created_at=persisted_created_at,
        )

        mock_member_count = 15
        self.assertGreater(mock_member_count, 0)

        self.assertNotEqual(lookup_channel.id, updated_channel.id)
        self.assertNotEqual(chat_id, updated_channel.id)
        self.assertNotEqual(updated_channel.avatar_file_id, request_data.avatar_file_id)
        self.assertNotEqual(updated_channel.avatar_file_id, resolved_avatar)
        self.assertNotEqual(updated_channel.avatar_file_id, lookup_channel.avatar_file_id)
        self.assertNotEqual(updated_channel.title, request_data.title)
        self.assertNotEqual(updated_channel.title, lookup_channel.title)
        self.assertNotEqual(updated_channel.description, request_data.description)
        self.assertNotEqual(updated_channel.description, lookup_channel.description)
        self.assertTrue(updated_channel.title)

        call_order: list[str] = []

        async def fake_get_channel(*args: object, **kwargs: object) -> object:
            call_order.append("get_channel_or_404")
            return lookup_channel

        async def fake_resolve_avatar(*args: object, **kwargs: object) -> str:
            call_order.append("resolve_owned_avatar_file_id")
            return resolved_avatar

        async def fake_update_metadata(*args: object, **kwargs: object) -> object:
            call_order.append("update_manageable_channel_metadata")
            return updated_channel

        async def fake_count_members(*args: object, **kwargs: object) -> int:
            call_order.append("count_active_chat_members")
            return mock_member_count

        mock_get_channel_or_404.side_effect = fake_get_channel
        mock_resolve_owned_avatar_file_id.side_effect = fake_resolve_avatar
        mock_update_manageable_channel_metadata.side_effect = fake_update_metadata
        mock_count_active_chat_members.side_effect = fake_count_members

        result = await update_channel(
            chat_id=chat_id,
            data=request_data,
            current_user=current_user,
            db=fake_db,
        )

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
            chat=lookup_channel,
            title=request_data.title,
            description=request_data.description,
            avatar_file_id=resolved_avatar,
        )
        mock_count_active_chat_members.assert_awaited_once_with(
            fake_db,
            updated_channel.id,
        )
        self.assertIs(
            mock_update_manageable_channel_metadata.await_args.kwargs["chat"],
            lookup_channel,
        )

        self.assertIsInstance(result, ChannelRoomRead)
        self.assertEqual(result.id, 777)
        self.assertEqual(result.id, updated_channel.id)
        self.assertEqual(result.type, ChatType.CHANNEL)
        self.assertEqual(result.type, updated_channel.type)
        self.assertEqual(result.title, "Persisted Channel Title")
        self.assertEqual(result.title, updated_channel.title)
        self.assertEqual(result.description, "Persisted Channel Description")
        self.assertEqual(result.description, updated_channel.description)
        self.assertEqual(result.avatar_file_id, "avatar-file-persisted-789")
        self.assertEqual(result.avatar_file_id, updated_channel.avatar_file_id)
        self.assertEqual(result.created_by_id, 101)
        self.assertEqual(result.created_by_id, updated_channel.created_by_id)
        self.assertFalse(result.is_system)
        self.assertEqual(result.is_system, updated_channel.is_system)
        self.assertTrue(result.is_mandatory)
        self.assertEqual(result.is_mandatory, updated_channel.is_mandatory)
        self.assertEqual(result.member_count, 15)
        self.assertEqual(result.member_count, mock_member_count)
        self.assertEqual(result.created_at, persisted_created_at)
        self.assertEqual(result.created_at, updated_channel.created_at)


if __name__ == "__main__":
    unittest.main()
