"""Direct-characterization tests for api.routers.chat.patch_group under P1-00b.

Characterizes get_group_or_404 failure boundary under WA-P1-00B-FREEZE-20260904-001.
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

from api.routers.chat import patch_group  # noqa: E402


class CharacterizeChatPatchGroup404Tests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing patch_group 404 failure boundary."""

    @patch("api.routers.chat.count_active_chat_members", new_callable=AsyncMock)
    @patch("api.routers.chat.update_group_chat", new_callable=AsyncMock)
    @patch("api.routers.chat.resolve_owned_avatar_file_id", new_callable=AsyncMock)
    @patch("api.routers.chat.get_active_group_admin_or_403", new_callable=AsyncMock)
    @patch("api.routers.chat.get_group_or_404", new_callable=AsyncMock)
    async def test_patch_group_raises_404_when_group_not_found(
        self,
        mock_get_group_or_404: AsyncMock,
        mock_get_active_group_admin_or_403: AsyncMock,
        mock_resolve_owned_avatar_file_id: AsyncMock,
        mock_update_group_chat: AsyncMock,
        mock_count_active_chat_members: AsyncMock,
    ) -> None:
        fake_db = object()
        fake_data = object()
        current_user = SimpleNamespace(id=42)
        sentinel_error = HTTPException(status_code=404, detail="Group not found")
        mock_get_group_or_404.side_effect = sentinel_error

        with self.assertRaises(HTTPException) as context:
            await patch_group(
                chat_id=321,
                data=fake_data,
                current_user=current_user,
                db=fake_db,
            )

        self.assertIs(context.exception, sentinel_error)
        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(context.exception.detail, "Group not found")
        mock_get_group_or_404.assert_awaited_once_with(fake_db, 321)
        mock_get_active_group_admin_or_403.assert_not_awaited()
        mock_resolve_owned_avatar_file_id.assert_not_awaited()
        mock_update_group_chat.assert_not_awaited()
        mock_count_active_chat_members.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
