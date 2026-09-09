"""Direct-characterization tests for api.routers.chat.get_group_member_candidates under P1-00b.

Characterizes is_user_customer failure boundary under WA-P1-00B-FREEZE-20260904-001.
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

from api.routers.chat import get_group_member_candidates  # noqa: E402


class CharacterizeChatGetGroupMemberCandidatesCustomerFailureTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing get_group_member_candidates customer lookup failure boundary."""

    @patch("api.routers.chat.load_chat_role_badges", new_callable=AsyncMock)
    @patch("api.routers.chat.list_group_member_candidates", new_callable=AsyncMock)
    @patch("api.routers.chat.get_active_group_admin_or_403", new_callable=AsyncMock)
    @patch("api.routers.chat.get_group_or_404", new_callable=AsyncMock)
    @patch("api.routers.chat.is_user_customer", new_callable=AsyncMock)
    async def test_get_group_member_candidates_raises_when_customer_lookup_fails(
        self,
        mock_is_user_customer: AsyncMock,
        mock_get_group_or_404: AsyncMock,
        mock_get_active_group_admin_or_403: AsyncMock,
        mock_list_group_member_candidates: AsyncMock,
        mock_load_chat_role_badges: AsyncMock,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42)
        sentinel_error = RuntimeError("customer-lookup-failure-sentinel")
        mock_is_user_customer.side_effect = sentinel_error

        with self.assertRaises(RuntimeError) as context:
            await get_group_member_candidates(
                query_text="candidate-query",
                limit=50,
                offset=0,
                exclude_chat_id=123,
                selected_user_ids=[10, 20],
                current_user=current_user,
                db=fake_db,
            )

        self.assertIs(context.exception, sentinel_error)
        mock_is_user_customer.assert_awaited_once_with(fake_db, current_user.id)
        mock_get_group_or_404.assert_not_called()
        mock_get_group_or_404.assert_not_awaited()
        mock_get_active_group_admin_or_403.assert_not_called()
        mock_get_active_group_admin_or_403.assert_not_awaited()
        mock_list_group_member_candidates.assert_not_called()
        mock_list_group_member_candidates.assert_not_awaited()
        mock_load_chat_role_badges.assert_not_called()
        mock_load_chat_role_badges.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
