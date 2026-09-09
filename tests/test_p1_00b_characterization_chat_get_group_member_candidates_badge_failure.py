"""Direct-characterization tests for api.routers.chat.get_group_member_candidates under P1-00b.

Characterizes load_chat_role_badges failure boundary under WA-P1-00B-FREEZE-20260904-001.
This is direct characterization under P1-00b / WA-P1-00B-FREEZE-20260904-001 only;
it does not promote executed-seed evidence, reduce canonical blockers or complete the Stage.
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


class CharacterizeChatGetGroupMemberCandidatesBadgeFailureTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing get_group_member_candidates badge lookup failure boundary."""

    @patch("api.routers.chat.load_chat_role_badges", new_callable=AsyncMock)
    @patch("api.routers.chat.list_group_member_candidates", new_callable=AsyncMock)
    @patch("api.routers.chat.get_active_group_admin_or_403", new_callable=AsyncMock)
    @patch("api.routers.chat.get_group_or_404", new_callable=AsyncMock)
    @patch("api.routers.chat.is_user_customer", new_callable=AsyncMock)
    async def test_get_group_member_candidates_propagates_load_chat_role_badges_failure(
        self,
        mock_is_user_customer: AsyncMock,
        mock_get_group_or_404: AsyncMock,
        mock_get_active_group_admin_or_403: AsyncMock,
        mock_list_group_member_candidates: AsyncMock,
        mock_load_chat_role_badges: AsyncMock,
    ) -> None:
        fake_db = object()
        synthetic_group = object()
        synthetic_admin = object()
        current_user = SimpleNamespace(id=42)
        candidate_page = SimpleNamespace(
            items=[
                SimpleNamespace(user_id=101),
                SimpleNamespace(user_id=202),
                SimpleNamespace(user_id=101),
            ]
        )
        sentinel_error = RuntimeError("unique-sentinel-badge-failure-42")

        call_order: list[str] = []

        async def fake_is_user_customer(*args: object, **kwargs: object) -> bool:
            call_order.append("customer-check")
            return False

        async def fake_get_group_or_404(*args: object, **kwargs: object) -> object:
            call_order.append("group lookup")
            return synthetic_group

        async def fake_get_active_group_admin_or_403(*args: object, **kwargs: object) -> object:
            call_order.append("admin check")
            return synthetic_admin

        async def fake_list_group_member_candidates(*args: object, **kwargs: object) -> SimpleNamespace:
            call_order.append("candidate lookup")
            return candidate_page

        async def fake_load_chat_role_badges(*args: object, **kwargs: object) -> dict[int, object]:
            call_order.append("badge lookup")
            raise sentinel_error

        mock_is_user_customer.side_effect = fake_is_user_customer
        mock_get_group_or_404.side_effect = fake_get_group_or_404
        mock_get_active_group_admin_or_403.side_effect = fake_get_active_group_admin_or_403
        mock_list_group_member_candidates.side_effect = fake_list_group_member_candidates
        mock_load_chat_role_badges.side_effect = fake_load_chat_role_badges

        with self.assertRaises(RuntimeError) as context:
            await get_group_member_candidates(
                query_text="candidate-search",
                limit=25,
                offset=10,
                exclude_chat_id=777,
                selected_user_ids=[101, 202],
                current_user=current_user,
                db=fake_db,
            )

        self.assertIs(context.exception, sentinel_error)
        self.assertEqual(
            call_order,
            [
                "customer-check",
                "group lookup",
                "admin check",
                "candidate lookup",
                "badge lookup",
            ],
        )
        mock_is_user_customer.assert_awaited_once_with(fake_db, current_user.id)
        mock_get_group_or_404.assert_awaited_once_with(fake_db, 777)
        mock_get_active_group_admin_or_403.assert_awaited_once_with(
            fake_db,
            chat=synthetic_group,
            user_id=current_user.id,
        )
        mock_list_group_member_candidates.assert_awaited_once_with(
            fake_db,
            current_user=current_user,
            query_text="candidate-search",
            limit=25,
            offset=10,
            exclude_chat_id=777,
            selected_user_ids=[101, 202],
        )
        mock_load_chat_role_badges.assert_awaited_once_with(fake_db, {101, 202})


if __name__ == "__main__":
    unittest.main()
