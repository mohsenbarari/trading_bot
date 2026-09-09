"""Direct-characterization tests for api.routers.chat.create_channel under P1-00b.

Directly characterizes only the post-creation member-count dependency failure,
with no authentication, HTTP dispatch, concurrency, process-reload,
executed-seed promotion, canonical-blocker reduction, or Stage completion claims.
P1-00b remains IN_PROGRESS and all product source is unchanged.
"""

from __future__ import annotations

import types
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import create_channel  # noqa: E402


class CharacterizeChatCreateChannelCountFailureTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing create_channel count failure boundary."""

    @patch("api.routers.chat.ChannelCreateResponse")
    @patch("api.routers.chat.count_active_chat_members", new_callable=AsyncMock)
    @patch("api.routers.chat.create_optional_channel", new_callable=AsyncMock)
    async def test_create_channel_raises_when_count_active_chat_members_fails(
        self,
        mock_create_optional_channel: AsyncMock,
        mock_count_active_chat_members: AsyncMock,
        mock_channel_create_response: MagicMock,
    ) -> None:
        db = object()
        current_user = object()
        data = types.SimpleNamespace(
            title="count-boundary-channel",
            description="synthetic description",
        )
        mock_create_optional_channel.return_value = types.SimpleNamespace(id=88)
        sentinel_error = RuntimeError("count-active-chat-members-failure-sentinel")

        async def _count_side_effect(*args: object, **kwargs: object) -> None:
            self.assertEqual(mock_create_optional_channel.await_count, 1)
            raise sentinel_error

        mock_count_active_chat_members.side_effect = _count_side_effect

        with self.assertRaises(RuntimeError) as context:
            await create_channel(
                data=data,
                current_user=current_user,
                db=db,
            )

        self.assertIs(context.exception, sentinel_error)
        mock_create_optional_channel.assert_awaited_once_with(
            db,
            creator=current_user,
            title=data.title,
            description=data.description,
        )
        mock_count_active_chat_members.assert_awaited_once_with(db, 88)
        mock_channel_create_response.assert_not_called()


if __name__ == "__main__":
    unittest.main()
