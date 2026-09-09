"""Direct-characterization tests for api.routers.chat.get_channel_members under P1-00b.

Characterizes list_channel_members failure boundary under WA-P1-00B-FREEZE-20260904-001.
Direct-handler characterization only: no HTTP/authentication, durable overlap/reload,
executed-seed promotion, canonical blocker reduction or Stage acceptance/completion
claims; P1-00b stays IN_PROGRESS.
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()
os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

from api.routers.chat import get_channel_members  # noqa: E402


class CharacterizeChatGetChannelMembersListFailureTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing get_channel_members list_channel_members failure boundary."""

    @patch("api.routers.chat.load_chat_role_badges", new_callable=AsyncMock)
    @patch("api.routers.chat.list_channel_members", new_callable=AsyncMock)
    @patch("api.routers.chat.get_channel_or_404", new_callable=AsyncMock)
    async def test_get_channel_members_propagates_list_channel_members_failure(
        self,
        mock_get_channel_or_404: AsyncMock,
        mock_list_channel_members: AsyncMock,
        mock_load_chat_role_badges: AsyncMock,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42)
        chat_id = 99
        channel_sentinel = object()
        sentinel_error = RuntimeError("Channel member listing failure sentinel")

        mock_get_channel_or_404.return_value = channel_sentinel

        def list_channel_members_side_effect(*args: Any, **kwargs: Any) -> Any:
            # Verify lookup was already completed when member listing fails
            mock_get_channel_or_404.assert_awaited_once_with(fake_db, chat_id)
            mock_load_chat_role_badges.assert_not_called()
            mock_load_chat_role_badges.assert_not_awaited()
            raise sentinel_error

        mock_list_channel_members.side_effect = list_channel_members_side_effect

        with self.assertRaises(RuntimeError) as context:
            await get_channel_members(
                chat_id=chat_id,
                current_user=current_user,
                db=fake_db,
            )

        self.assertIs(context.exception, sentinel_error)
        mock_get_channel_or_404.assert_awaited_once_with(fake_db, chat_id)
        mock_list_channel_members.assert_awaited_once_with(fake_db, chat=channel_sentinel)
        mock_load_chat_role_badges.assert_not_called()
        mock_load_chat_role_badges.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
