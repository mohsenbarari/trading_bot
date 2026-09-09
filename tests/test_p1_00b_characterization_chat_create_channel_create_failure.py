"""Direct-characterization tests for api.routers.chat.create_channel under P1-00b.

Directly characterizes only the creation-dependency failure,
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


class CharacterizeChatCreateChannelCreateFailureTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing create_channel creation failure boundary."""

    @patch("api.routers.chat.ChannelCreateResponse")
    @patch("api.routers.chat.count_active_chat_members", new_callable=AsyncMock)
    @patch("api.routers.chat.create_optional_channel", new_callable=AsyncMock)
    async def test_create_channel_propagates_create_failure_without_downstream_work(
        self,
        mock_create_optional_channel: AsyncMock,
        mock_count_active_chat_members: AsyncMock,
        mock_channel_create_response: MagicMock,
    ) -> None:
        db = object()
        current_user = object()
        data = types.SimpleNamespace(
            title="synthetic title",
            description="synthetic description",
        )
        sentinel_error = RuntimeError("create-optional-channel-failure-sentinel")
        mock_create_optional_channel.side_effect = sentinel_error

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
        mock_count_active_chat_members.assert_not_called()
        mock_channel_create_response.assert_not_called()


if __name__ == "__main__":
    unittest.main()
