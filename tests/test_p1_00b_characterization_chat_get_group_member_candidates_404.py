"""Direct-characterization tests for api.routers.chat.get_group_member_candidates under P1-00b.

Characterizes get_group_or_404 failure boundary under WA-P1-00B-FREEZE-20260904-001.
This is direct characterization only, without executed-seed promotion,
canonical-blocker reduction or Stage completion.
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

from api.routers.chat import get_group_member_candidates  # noqa: E402


class CharacterizeChatGetGroupMemberCandidates404Tests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing get_group_member_candidates 404 failure boundary."""

    @patch("api.routers.chat.load_chat_role_badges", new_callable=AsyncMock)
    @patch("api.routers.chat.list_group_member_candidates", new_callable=AsyncMock)
    @patch("api.routers.chat.get_active_group_admin_or_403", new_callable=AsyncMock)
    @patch("api.routers.chat.get_group_or_404", new_callable=AsyncMock)
    @patch("api.routers.chat.is_user_customer", new_callable=AsyncMock)
    async def test_get_group_member_candidates_raises_404_when_excluded_group_not_found(
        self,
        mock_is_user_customer: AsyncMock,
        mock_get_group_or_404: AsyncMock,
        mock_get_active_group_admin_or_403: AsyncMock,
        mock_list_group_member_candidates: AsyncMock,
        mock_load_chat_role_badges: AsyncMock,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42)
        sentinel_error = HTTPException(status_code=404, detail="Group not found")
        mock_is_user_customer.return_value = False
        mock_get_group_or_404.side_effect = sentinel_error

        with self.assertRaises(HTTPException) as context:
            await get_group_member_candidates(
                query_text=None,
                limit=50,
                offset=0,
                exclude_chat_id=321,
                selected_user_ids=[],
                current_user=current_user,
                db=fake_db,
            )

        self.assertIs(context.exception, sentinel_error)
        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(context.exception.detail, "Group not found")
        mock_is_user_customer.assert_awaited_once_with(fake_db, current_user.id)
        mock_get_group_or_404.assert_awaited_once_with(fake_db, 321)
        mock_get_active_group_admin_or_403.assert_not_called()
        mock_get_active_group_admin_or_403.assert_not_awaited()
        mock_list_group_member_candidates.assert_not_called()
        mock_list_group_member_candidates.assert_not_awaited()
        mock_load_chat_role_badges.assert_not_called()
        mock_load_chat_role_badges.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
