"""Direct-characterization tests for api.routers.chat.get_channel_members under P1-00b.

Characterizes load_chat_role_badges failure boundary under WA-P1-00B-FREEZE-20260904-001.
Direct-handler characterization only: no HTTP/auth, durable overlap/reload,
executed-seed promotion, canonical blocker reduction, or Stage acceptance/completion
claim; P1-00b stays IN_PROGRESS.
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import get_channel_members  # noqa: E402


class CharacterizeChatGetChannelMembersBadgeFailureTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing get_channel_members load_chat_role_badges failure boundary."""

    @patch("api.routers.chat.ChannelMemberRead")
    @patch("api.routers.chat.load_chat_role_badges", new_callable=AsyncMock)
    @patch("api.routers.chat.list_channel_members", new_callable=AsyncMock)
    @patch("api.routers.chat.get_channel_or_404", new_callable=AsyncMock)
    async def test_get_channel_members_propagates_load_chat_role_badges_failure(
        self,
        mock_get_channel_or_404: AsyncMock,
        mock_list_channel_members: AsyncMock,
        mock_load_chat_role_badges: AsyncMock,
        mock_channel_member_read: MagicMock,
    ) -> None:
        fake_db = object()
        channel_sentinel = object()
        current_user = SimpleNamespace(id=42)
        chat_id = 99
        member_rows = [
            SimpleNamespace(user_id=101),
            SimpleNamespace(user_id=202),
            SimpleNamespace(user_id=101),
        ]
        sentinel_error = RuntimeError("load_chat_role_badges sentinel failure")

        call_order: list[str] = []

        def get_channel_side_effect(*args: Any, **kwargs: Any) -> Any:
            call_order.append("get_channel_or_404")
            mock_list_channel_members.assert_not_called()
            mock_load_chat_role_badges.assert_not_called()
            return channel_sentinel

        def list_channel_members_side_effect(*args: Any, **kwargs: Any) -> Any:
            call_order.append("list_channel_members")
            mock_get_channel_or_404.assert_awaited_once_with(fake_db, chat_id)
            mock_load_chat_role_badges.assert_not_called()
            return member_rows

        def load_chat_role_badges_side_effect(*args: Any, **kwargs: Any) -> Any:
            call_order.append("load_chat_role_badges")
            mock_get_channel_or_404.assert_awaited_once_with(fake_db, chat_id)
            mock_list_channel_members.assert_awaited_once_with(
                fake_db, chat=channel_sentinel
            )
            mock_channel_member_read.assert_not_called()
            raise sentinel_error

        mock_get_channel_or_404.side_effect = get_channel_side_effect
        mock_list_channel_members.side_effect = list_channel_members_side_effect
        mock_load_chat_role_badges.side_effect = load_chat_role_badges_side_effect

        with self.assertRaises(RuntimeError) as context:
            await get_channel_members(
                chat_id=chat_id,
                current_user=current_user,
                db=fake_db,
            )

        self.assertIs(context.exception, sentinel_error)
        self.assertEqual(
            call_order,
            ["get_channel_or_404", "list_channel_members", "load_chat_role_badges"],
        )
        mock_get_channel_or_404.assert_awaited_once_with(fake_db, chat_id)
        mock_list_channel_members.assert_awaited_once_with(
            fake_db, chat=channel_sentinel
        )
        mock_load_chat_role_badges.assert_awaited_once_with(fake_db, {101, 202})
        mock_channel_member_read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
