"""Direct-characterization tests for api.routers.chat.promote_group_admin under P1-00b.

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

from api.routers.chat import promote_group_admin  # noqa: E402


class CharacterizeChatPromoteGroupAdmin404Tests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing promote_group_admin 404 failure boundary."""

    @patch("api.routers.chat.update_group_admin_status", new_callable=AsyncMock)
    @patch("api.routers.chat.get_active_group_admin_or_403", new_callable=AsyncMock)
    @patch("api.routers.chat.get_group_or_404", new_callable=AsyncMock)
    async def test_promote_group_admin_raises_404_when_group_not_found(
        self,
        mock_get_group_or_404: AsyncMock,
        mock_get_active_group_admin_or_403: AsyncMock,
        mock_update_group_admin_status: AsyncMock,
    ) -> None:
        sentinel_db = object()
        current_user = SimpleNamespace(id=42)
        sentinel_error = HTTPException(
            status_code=404,
            detail="Group not found",
        )
        mock_get_group_or_404.side_effect = sentinel_error

        with self.assertRaises(HTTPException) as context:
            await promote_group_admin(
                chat_id=321,
                user_id=77,
                current_user=current_user,
                db=sentinel_db,
            )

        self.assertIs(context.exception, sentinel_error)
        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(context.exception.detail, "Group not found")
        mock_get_group_or_404.assert_awaited_once_with(sentinel_db, 321)
        mock_get_active_group_admin_or_403.assert_not_called()
        mock_get_active_group_admin_or_403.assert_not_awaited()
        mock_update_group_admin_status.assert_not_called()
        mock_update_group_admin_status.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
