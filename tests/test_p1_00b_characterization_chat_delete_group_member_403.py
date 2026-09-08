"""Direct-characterization tests for api.routers.chat.delete_group_member under P1-00b.

Characterizes get_active_group_admin_or_403 failure boundary under WA-P1-00B-FREEZE-20260904-001.
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

from api.routers.chat import delete_group_member  # noqa: E402


class CharacterizeChatDeleteGroupMember403Tests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing delete_group_member 403 failure boundary."""

    @patch("api.routers.chat.remove_group_member", new_callable=AsyncMock)
    @patch("api.routers.chat.get_active_group_admin_or_403", new_callable=AsyncMock)
    @patch("api.routers.chat.get_group_or_404", new_callable=AsyncMock)
    async def test_delete_group_member_raises_403_when_not_active_admin(
        self,
        mock_get_group_or_404: AsyncMock,
        mock_get_active_group_admin_or_403: AsyncMock,
        mock_remove_group_member: AsyncMock,
    ) -> None:
        fake_db = object()
        fake_group = object()
        current_user = SimpleNamespace(id=42)
        sentinel_error = HTTPException(
            status_code=403,
            detail="Forbidden",
        )
        mock_get_group_or_404.return_value = fake_group
        mock_get_active_group_admin_or_403.side_effect = sentinel_error

        with self.assertRaises(HTTPException) as context:
            await delete_group_member(
                chat_id=321,
                user_id=77,
                current_user=current_user,
                db=fake_db,
            )

        self.assertIs(context.exception, sentinel_error)
        self.assertEqual(context.exception.status_code, 403)
        self.assertEqual(context.exception.detail, "Forbidden")
        mock_get_group_or_404.assert_awaited_once_with(fake_db, 321)
        mock_get_active_group_admin_or_403.assert_awaited_once_with(
            fake_db,
            chat=fake_group,
            user_id=42,
        )
        mock_remove_group_member.assert_not_called()
        mock_remove_group_member.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
