"""Direct-characterization tests for api.routers.chat.get_channel_members under P1-00b.

Characterizes get_channel_or_404 failure boundary under WA-P1-00B-FREEZE-20260904-001.
This is direct-handler characterization only, without HTTP/auth, durable overlap/reload,
executed-seed promotion, canonical blocker reduction or Stage acceptance/completion
evidence; P1-00b stays IN_PROGRESS.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from fastapi import HTTPException  # noqa: E402

from api.routers.chat import get_channel_members  # noqa: E402


class CharacterizeChatGetChannelMembers404Tests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing get_channel_members 404 failure boundary."""

    @patch("api.routers.chat.load_chat_role_badges", new_callable=AsyncMock)
    @patch("api.routers.chat.list_channel_members", new_callable=AsyncMock)
    @patch("api.routers.chat.get_channel_or_404", new_callable=AsyncMock)
    async def test_get_channel_members_raises_404_when_channel_not_found(
        self,
        mock_get_channel_or_404: AsyncMock,
        mock_list_channel_members: AsyncMock,
        mock_load_chat_role_badges: AsyncMock,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42)
        chat_id = 99
        sentinel_error = HTTPException(status_code=404, detail="Channel not found")
        mock_get_channel_or_404.side_effect = sentinel_error

        with self.assertRaises(HTTPException) as context:
            await get_channel_members(
                chat_id=chat_id,
                current_user=current_user,
                db=fake_db,
            )

        self.assertIs(context.exception, sentinel_error)
        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(context.exception.detail, "Channel not found")
        mock_get_channel_or_404.assert_awaited_once_with(fake_db, chat_id)
        mock_list_channel_members.assert_not_called()
        mock_list_channel_members.assert_not_awaited()
        mock_load_chat_role_badges.assert_not_called()
        mock_load_chat_role_badges.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
