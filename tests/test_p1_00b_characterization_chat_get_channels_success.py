"""Direct-characterization tests for api.routers.chat.get_channels success path under P1-00b.

Characterizes populated get_channels success path under WA-P1-00B-FREEZE-20260904-001.
This is direct characterization only: no authentication/HTTP, concurrency,
process-reload, executed-seed promotion, canonical blocker reduction,
or Stage-completion claims.
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

from api.routers.chat import get_channels  # noqa: E402
from api.routers.chat_schemas import ChannelRoomRead  # noqa: E402
from core.enums import ChatType  # noqa: E402


class CharacterizeChatGetChannelsSuccessTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing get_channels populated success path."""

    @patch("api.routers.chat.list_manageable_channels", new_callable=AsyncMock)
    async def test_get_channels_populated_success(
        self,
        mock_list_manageable_channels: AsyncMock,
    ) -> None:
        sentinel_db = object()
        synthetic_actor = SimpleNamespace(id=999)

        fixed_created_at_alpha = datetime(2026, 9, 4, 10, 30, 0, tzinfo=timezone.utc)
        fixed_created_at_beta = datetime(2026, 9, 4, 11, 45, 0, tzinfo=timezone.utc)

        channel_alpha = SimpleNamespace(
            id=202,
            type=ChatType.CHANNEL,
            title="Alpha Populated Channel",
            description="Alpha Channel Description",
            avatar_file_id="avatar-alpha-distinct-uuid",
            created_by_id=1001,
            is_system=True,
            is_mandatory=False,
            member_count=42,
            created_at=fixed_created_at_alpha,
        )

        channel_beta = SimpleNamespace(
            id=101,
            type=ChatType.CHANNEL,
            title="Beta Populated Channel",
            description=None,
            avatar_file_id=None,
            created_by_id=None,
            is_system=False,
            is_mandatory=True,
            member_count=0,
            created_at=fixed_created_at_beta,
        )

        mock_list_manageable_channels.return_value = [channel_alpha, channel_beta]

        result = await get_channels(
            current_user=synthetic_actor,
            db=sentinel_db,
        )

        mock_list_manageable_channels.assert_awaited_once_with(sentinel_db)

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)

        first = result[0]
        second = result[1]

        self.assertIsInstance(first, ChannelRoomRead)
        self.assertIsInstance(second, ChannelRoomRead)

        # Assert all fields of the first row (non-null avatar, nonzero member_count)
        self.assertEqual(first.id, 202)
        self.assertEqual(first.type, ChatType.CHANNEL)
        self.assertIs(first.type, ChatType.CHANNEL)
        self.assertEqual(first.title, "Alpha Populated Channel")
        self.assertEqual(first.description, "Alpha Channel Description")
        self.assertEqual(first.avatar_file_id, "avatar-alpha-distinct-uuid")
        self.assertEqual(first.created_by_id, 1001)
        self.assertIs(first.is_system, True)
        self.assertIs(first.is_mandatory, False)
        self.assertEqual(first.member_count, 42)
        self.assertEqual(first.created_at, fixed_created_at_alpha)

        # Assert all fields of the second row (deliberately non-ID-sorted, nullable fields None, different flags)
        self.assertEqual(second.id, 101)
        self.assertEqual(second.type, ChatType.CHANNEL)
        self.assertIs(second.type, ChatType.CHANNEL)
        self.assertEqual(second.title, "Beta Populated Channel")
        self.assertIsNone(second.description)
        self.assertIsNone(second.avatar_file_id)
        self.assertIsNone(second.created_by_id)
        self.assertIs(second.is_system, False)
        self.assertIs(second.is_mandatory, True)
        self.assertEqual(second.member_count, 0)
        self.assertEqual(second.created_at, fixed_created_at_beta)


if __name__ == "__main__":
    unittest.main()
