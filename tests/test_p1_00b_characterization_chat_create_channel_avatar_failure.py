"""Direct-characterization tests for api.routers.chat.create_channel avatar resolution failure.

This is direct characterization only under P1-00b and WA-P1-00B-FREEZE-20260904-001,
with no authentication, HTTP dispatch, concurrency, process-reload,
executed-seed promotion, canonical-blocker reduction, or Stage completion claims.
P1-00b remains IN_PROGRESS and all product source is unchanged.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import create_channel  # noqa: E402
from api.routers.chat_schemas import ChannelCreateRequest  # noqa: E402


class CharacterizeChatCreateChannelAvatarFailureTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit test characterizing create_channel avatar resolution failure boundary."""

    @patch("api.routers.chat.count_active_chat_members", new_callable=AsyncMock)
    @patch("api.routers.chat.create_optional_channel", new_callable=AsyncMock)
    @patch("api.routers.chat.resolve_owned_avatar_file_id", new_callable=AsyncMock)
    async def test_create_channel_resolve_owned_avatar_file_id_failure(
        self,
        mock_resolve_owned_avatar_file_id: AsyncMock,
        mock_create_optional_channel: AsyncMock,
        mock_count_active_chat_members: AsyncMock,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42)
        sentinel_exc = RuntimeError(
            "resolve_owned_avatar_file_id failure during channel creation"
        )

        mock_resolve_owned_avatar_file_id.side_effect = sentinel_exc

        request_data = ChannelCreateRequest(
            title="   Boundary Channel Title   ",
            description="   Boundary Channel Description   ",
            avatar_file_id="   avatar-raw-uuid-boundary   ",
        )

        self.assertEqual(request_data.title, "Boundary Channel Title")
        self.assertEqual(request_data.description, "Boundary Channel Description")
        self.assertEqual(request_data.avatar_file_id, "avatar-raw-uuid-boundary")

        with self.assertRaises(RuntimeError) as ctx:
            await create_channel(
                data=request_data,
                current_user=current_user,
                db=fake_db,
            )

        self.assertIs(ctx.exception, sentinel_exc)
        mock_resolve_owned_avatar_file_id.assert_awaited_once_with(
            fake_db,
            actor_id=current_user.id,
            avatar_file_id="avatar-raw-uuid-boundary",
        )
        mock_create_optional_channel.assert_not_called()
        mock_create_optional_channel.assert_not_awaited()
        mock_count_active_chat_members.assert_not_called()
        mock_count_active_chat_members.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
