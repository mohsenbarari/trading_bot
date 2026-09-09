"""Direct-characterization tests for api.routers.chat.update_channel under P1-00b.

Characterizes resolve_owned_avatar_file_id failure boundary under WA-P1-00B-FREEZE-20260904-001.
This is direct characterization only and does not update the executed-seed registry,
reduce a canonical blocker, promote evidence, or mark P1-00b complete.
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

from api.routers.chat import update_channel  # noqa: E402
from api.routers.chat_schemas import ChannelUpdateRequest  # noqa: E402


class CharacterizeChatUpdateChannelAvatarFailureTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing update_channel avatar resolution failure boundary."""

    @patch("api.routers.chat.count_active_chat_members", new_callable=AsyncMock)
    @patch("api.routers.chat.update_manageable_channel_metadata", new_callable=AsyncMock)
    @patch("api.routers.chat.resolve_owned_avatar_file_id", new_callable=AsyncMock)
    @patch("api.routers.chat.get_channel_or_404", new_callable=AsyncMock)
    async def test_update_channel_raises_when_avatar_resolution_fails(
        self,
        mock_get_channel_or_404: AsyncMock,
        mock_resolve_owned_avatar_file_id: AsyncMock,
        mock_update_manageable_channel_metadata: AsyncMock,
        mock_count_active_chat_members: AsyncMock,
    ) -> None:
        fake_db = object()
        fake_channel = object()
        mock_get_channel_or_404.return_value = fake_channel

        current_user = SimpleNamespace(id=42)
        request_data = ChannelUpdateRequest(
            title="Test Channel",
            description="Test Description",
            avatar_file_id="avatar-file-123",
        )
        chat_id = 99
        sentinel_error = HTTPException(
            status_code=403, detail="avatar ownership denied"
        )
        mock_resolve_owned_avatar_file_id.side_effect = sentinel_error

        with self.assertRaises(HTTPException) as context:
            await update_channel(
                chat_id=chat_id,
                data=request_data,
                current_user=current_user,
                db=fake_db,
            )

        self.assertIs(context.exception, sentinel_error)
        self.assertEqual(context.exception.status_code, 403)
        self.assertEqual(context.exception.detail, "avatar ownership denied")
        mock_get_channel_or_404.assert_awaited_once_with(fake_db, chat_id)
        mock_resolve_owned_avatar_file_id.assert_awaited_once_with(
            fake_db,
            actor_id=current_user.id,
            avatar_file_id=request_data.avatar_file_id,
        )
        mock_update_manageable_channel_metadata.assert_not_called()
        mock_update_manageable_channel_metadata.assert_not_awaited()
        mock_count_active_chat_members.assert_not_called()
        mock_count_active_chat_members.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
