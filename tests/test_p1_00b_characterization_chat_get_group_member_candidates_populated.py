"""Direct characterization only under P1-00b / WA-P1-00B-FREEZE-20260904-001.

Characterizes get_group_member_candidates populated success path with exclude_chat_id=None.
No executed-seed promotion, canonical blocker reduction, Stage completion, concurrency or reload proof.
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
from api.routers.chat_schemas import (  # noqa: E402
    ChannelInviteCandidateListResponse,
    ChannelInviteCandidateRead,
)
from core.services.chat_role_badge_service import ChatRoleBadge  # noqa: E402
from core.services.chat_room_service import (  # noqa: E402
    ChannelInviteCandidate,
    ChannelInviteCandidatePage,
)


class CharacterizeChatGetGroupMemberCandidatesPopulatedTests(
    unittest.IsolatedAsyncioTestCase
):
    """Direct-characterization test for populated get_group_member_candidates."""

    @patch("api.routers.chat.load_chat_role_badges", new_callable=AsyncMock)
    @patch("api.routers.chat.list_group_member_candidates", new_callable=AsyncMock)
    @patch("api.routers.chat.get_active_group_admin_or_403", new_callable=AsyncMock)
    @patch("api.routers.chat.get_group_or_404", new_callable=AsyncMock)
    @patch("api.routers.chat.is_user_customer", new_callable=AsyncMock)
    async def test_get_group_member_candidates_populated_success_without_exclude_chat(
        self,
        mock_is_user_customer: AsyncMock,
        mock_get_group_or_404: AsyncMock,
        mock_get_active_group_admin_or_403: AsyncMock,
        mock_list_group_member_candidates: AsyncMock,
        mock_load_chat_role_badges: AsyncMock,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42)

        badge1 = ChatRoleBadge(
            kind="accountant",
            label="حسابدار",
            accountant_owner_name="Synthetic Owner Alpha",
            accountant_owner_label="سرگروه: Synthetic Owner Alpha",
        )
        candidate1 = ChannelInviteCandidate(
            user_id=501,
            account_name="synthetic_candidate_one",
            full_name="Synthetic Candidate One",
            mobile_number="+989000000001",
            avatar_file_id="synthetic_avatar_file_001",
            is_already_member=True,
            customer_management_name="Synthetic Customer Management One",
        )
        candidate2 = ChannelInviteCandidate(
            user_id=502,
            account_name="synthetic_candidate_two",
            full_name="Synthetic Candidate Two",
            mobile_number="+989000000002",
            avatar_file_id=None,
            is_already_member=False,
            customer_management_name=None,
        )
        page = ChannelInviteCandidatePage(
            items=[candidate1, candidate2],
            total=40,
            active_total=40,
        )

        execution_order: list[str] = []

        async def _record_is_user_customer(*_args: object, **_kwargs: object) -> bool:
            execution_order.append("is_user_customer")
            return False

        async def _record_get_group_or_404(*_args: object, **_kwargs: object) -> object:
            execution_order.append("get_group_or_404")
            return None

        async def _record_get_active_group_admin_or_403(
            *_args: object, **_kwargs: object
        ) -> object:
            execution_order.append("get_active_group_admin_or_403")
            return None

        async def _record_list_group_member_candidates(
            *_args: object, **_kwargs: object
        ) -> object:
            execution_order.append("list_group_member_candidates")
            return page

        async def _record_load_chat_role_badges(
            *_args: object, **_kwargs: object
        ) -> object:
            execution_order.append("load_chat_role_badges")
            return {candidate1.user_id: badge1}

        mock_is_user_customer.side_effect = _record_is_user_customer
        mock_get_group_or_404.side_effect = _record_get_group_or_404
        mock_get_active_group_admin_or_403.side_effect = (
            _record_get_active_group_admin_or_403
        )
        mock_list_group_member_candidates.side_effect = (
            _record_list_group_member_candidates
        )
        mock_load_chat_role_badges.side_effect = _record_load_chat_role_badges

        result = await get_group_member_candidates(
            query_text="synthetic_search",
            limit=25,
            offset=10,
            exclude_chat_id=None,
            selected_user_ids=[101, 102],
            current_user=current_user,
            db=fake_db,
        )

        mock_is_user_customer.assert_awaited_once_with(fake_db, current_user.id)
        mock_get_group_or_404.assert_not_called()
        mock_get_group_or_404.assert_not_awaited()
        mock_get_active_group_admin_or_403.assert_not_called()
        mock_get_active_group_admin_or_403.assert_not_awaited()
        mock_list_group_member_candidates.assert_awaited_once_with(
            fake_db,
            current_user=current_user,
            query_text="synthetic_search",
            limit=25,
            offset=10,
            exclude_chat_id=None,
            selected_user_ids=[101, 102],
        )
        mock_load_chat_role_badges.assert_awaited_once_with(
            fake_db, {candidate1.user_id, candidate2.user_id}
        )

        self.assertEqual(
            execution_order,
            [
                "is_user_customer",
                "list_group_member_candidates",
                "load_chat_role_badges",
            ],
        )

        self.assertIsInstance(result, ChannelInviteCandidateListResponse)
        self.assertEqual(result.total, 40)
        self.assertEqual(result.active_total, 40)
        self.assertEqual(len(result.items), 2)

        item1 = result.items[0]
        self.assertIsInstance(item1, ChannelInviteCandidateRead)
        self.assertEqual(item1.user_id, 501)
        self.assertEqual(item1.account_name, "synthetic_candidate_one")
        self.assertEqual(item1.full_name, "Synthetic Candidate One")
        self.assertEqual(item1.mobile_number, "+989000000001")
        self.assertEqual(item1.avatar_file_id, "synthetic_avatar_file_001")
        self.assertTrue(item1.is_already_member)
        self.assertEqual(item1.chat_role_kind, "accountant")
        self.assertEqual(item1.chat_role_label, "حسابدار")
        self.assertEqual(item1.chat_accountant_owner_name, "Synthetic Owner Alpha")
        self.assertEqual(
            item1.chat_accountant_owner_label,
            "سرگروه: Synthetic Owner Alpha",
        )
        self.assertEqual(
            item1.customer_management_name,
            "Synthetic Customer Management One",
        )

        item2 = result.items[1]
        self.assertIsInstance(item2, ChannelInviteCandidateRead)
        self.assertEqual(item2.user_id, 502)
        self.assertEqual(item2.account_name, "synthetic_candidate_two")
        self.assertEqual(item2.full_name, "Synthetic Candidate Two")
        self.assertEqual(item2.mobile_number, "+989000000002")
        self.assertIsNone(item2.avatar_file_id)
        self.assertFalse(item2.is_already_member)
        self.assertIsNone(item2.chat_role_kind)
        self.assertIsNone(item2.chat_role_label)
        self.assertIsNone(item2.chat_accountant_owner_name)
        self.assertIsNone(item2.chat_accountant_owner_label)
        self.assertIsNone(item2.customer_management_name)


if __name__ == "__main__":
    unittest.main()
