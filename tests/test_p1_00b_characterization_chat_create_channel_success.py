"""Direct-characterization tests for api.routers.chat.create_channel success path.

This is direct characterization only under P1-00b and WA-P1-00B-FREEZE-20260904-001;
no authentication/HTTP, concurrency, process-reload, executed-seed promotion,
canonical blocker reduction or Stage-completion claims.
P1-00b remains IN_PROGRESS and all product source is unchanged.
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

from api.routers.chat import create_channel  # noqa: E402
from api.routers.chat_schemas import (  # noqa: E402
    ChannelCreateRequest,
    ChannelCreateResponse,
    ChannelRoomRead,
)
from core.enums import ChatType  # noqa: E402


class CharacterizeChatCreateChannelSuccessTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing create_channel success path."""

    @patch("api.routers.chat.count_active_chat_members", new_callable=AsyncMock)
    @patch("api.routers.chat.create_optional_channel", new_callable=AsyncMock)
    @patch("api.routers.chat.resolve_owned_avatar_file_id", new_callable=AsyncMock)
    async def test_create_channel_success(
        self,
        mock_resolve_owned_avatar_file_id: AsyncMock,
        mock_create_optional_channel: AsyncMock,
        mock_count_active_chat_members: AsyncMock,
    ) -> None:
        fake_db = object()
        actor = SimpleNamespace(id=101)
        fixed_created_at = datetime(2026, 9, 4, 12, 0, 0, tzinfo=timezone.utc)
        resolved_avatar_id = "resolved-avatar-distinct-uuid-999"
        synthetic_member_count = 5

        synthetic_channel = SimpleNamespace(
            id=777,
            type=ChatType.CHANNEL,
            title="Persisted Distinct Channel Title",
            description="Persisted Distinct Channel Description",
            avatar_file_id=resolved_avatar_id,
            created_by_id=actor.id,
            is_system=False,
            is_mandatory=False,
            created_at=fixed_created_at,
        )

        call_order: list[str] = []

        async def _mock_resolve_owned_avatar(*args: object, **kwargs: object) -> str:
            call_order.append("resolve_owned_avatar_file_id")
            return resolved_avatar_id

        async def _mock_create_optional_channel(
            *args: object, **kwargs: object
        ) -> SimpleNamespace:
            self.assertEqual(call_order, ["resolve_owned_avatar_file_id"])
            call_order.append("create_optional_channel")
            return synthetic_channel

        async def _mock_count_active_chat_members(
            *args: object, **kwargs: object
        ) -> int:
            self.assertEqual(
                call_order,
                ["resolve_owned_avatar_file_id", "create_optional_channel"],
            )
            call_order.append("count_active_chat_members")
            return synthetic_member_count

        mock_resolve_owned_avatar_file_id.side_effect = _mock_resolve_owned_avatar
        mock_create_optional_channel.side_effect = _mock_create_optional_channel
        mock_count_active_chat_members.side_effect = _mock_count_active_chat_members

        raw_title = "   Synthetic Channel Padded Title   "
        raw_description = "   Synthetic Channel Padded Description   "
        raw_avatar_id = "   synth-avatar-padded-id   "

        request_data = ChannelCreateRequest(
            title=raw_title,
            description=raw_description,
            avatar_file_id=raw_avatar_id,
        )

        self.assertEqual(request_data.title, "Synthetic Channel Padded Title")
        self.assertEqual(request_data.description, "Synthetic Channel Padded Description")
        self.assertEqual(request_data.avatar_file_id, "synth-avatar-padded-id")

        response = await create_channel(
            data=request_data,
            current_user=actor,
            db=fake_db,
        )

        self.assertEqual(
            call_order,
            [
                "resolve_owned_avatar_file_id",
                "create_optional_channel",
                "count_active_chat_members",
            ],
        )

        self.assertEqual(mock_resolve_owned_avatar_file_id.await_count, 1)
        mock_resolve_owned_avatar_file_id.assert_awaited_once_with(
            fake_db,
            actor_id=actor.id,
            avatar_file_id=request_data.avatar_file_id,
        )

        self.assertEqual(mock_create_optional_channel.await_count, 1)
        mock_create_optional_channel.assert_awaited_once_with(
            fake_db,
            creator=actor,
            title=request_data.title,
            description=request_data.description,
            avatar_file_id=resolved_avatar_id,
        )

        self.assertEqual(mock_count_active_chat_members.await_count, 1)
        mock_count_active_chat_members.assert_awaited_once_with(
            fake_db,
            synthetic_channel.id,
        )

        self.assertIsInstance(response, ChannelCreateResponse)
        self.assertIs(response.member_picker_required, True)
        self.assertIsInstance(response.channel, ChannelRoomRead)

        self.assertEqual(response.channel.id, 777)
        self.assertEqual(response.channel.type, ChatType.CHANNEL)
        self.assertIs(response.channel.type, ChatType.CHANNEL)
        self.assertEqual(response.channel.title, "Persisted Distinct Channel Title")
        self.assertNotEqual(response.channel.title, request_data.title)
        self.assertEqual(
            response.channel.description, "Persisted Distinct Channel Description"
        )
        self.assertNotEqual(response.channel.description, request_data.description)
        self.assertEqual(
            response.channel.avatar_file_id, "resolved-avatar-distinct-uuid-999"
        )
        self.assertNotEqual(response.channel.avatar_file_id, request_data.avatar_file_id)
        self.assertEqual(response.channel.created_by_id, actor.id)
        self.assertIs(response.channel.is_system, False)
        self.assertIs(response.channel.is_mandatory, False)
        self.assertEqual(response.channel.member_count, 5)
        self.assertEqual(response.channel.created_at, fixed_created_at)


if __name__ == "__main__":
    unittest.main()
