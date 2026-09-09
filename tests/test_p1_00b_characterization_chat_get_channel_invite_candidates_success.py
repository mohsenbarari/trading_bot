"""Direct-characterization tests for api.routers.chat.get_channel_invite_candidates under P1-00b.

Characterizes get_channel_invite_candidates populated success path under WA-P1-00B-FREEZE-20260904-001.
This is direct-handler success characterization only: no HTTP/auth, persistence,
concurrency/reload, executed-seed promotion, canonical-blocker reduction or
Stage acceptance/completion claims.
P1-00b stays IN_PROGRESS.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import get_channel_invite_candidates  # noqa: E402
from api.routers.chat_schemas import (  # noqa: E402
    ChannelInviteCandidateListResponse,
    ChannelInviteCandidateRead,
)
from core.services.chat_role_badge_service import (  # noqa: E402
    CHAT_ROLE_ACCOUNTANT,
    CHAT_ROLE_LABELS,
    ChatRoleBadge,
)
from core.services.chat_room_service import (  # noqa: E402
    ChannelInviteCandidate,
    ChannelInviteCandidatePage,
)


class CharacterizeChatGetChannelInviteCandidatesSuccessTests(
    unittest.IsolatedAsyncioTestCase
):
    """Hermetic unit test characterizing get_channel_invite_candidates success path."""

    @patch("api.routers.chat.load_chat_role_badges", new_callable=AsyncMock)
    @patch("api.routers.chat.list_channel_invite_candidates", new_callable=AsyncMock)
    async def test_get_channel_invite_candidates_success(
        self,
        mock_list_channel_invite_candidates: AsyncMock,
        mock_load_chat_role_badges: AsyncMock,
    ) -> None:
        db_sentinel = object()
        current_user_sentinel = object()

        candidate_1 = ChannelInviteCandidate(
            user_id=202,
            account_name="candidate_bravo",
            full_name="Bravo Candidate",
            mobile_number="+989120000002",
            avatar_file_id="avatar_bravo_001",
            is_already_member=False,
            customer_management_name="Corporate Accounts",
        )
        candidate_2 = ChannelInviteCandidate(
            user_id=101,
            account_name="candidate_alpha",
            full_name="Alpha Candidate",
            mobile_number="+989120000001",
            is_already_member=True,
        )
        fake_page = ChannelInviteCandidatePage(
            items=[candidate_1, candidate_2],
            total=25,
            active_total=18,
        )
        mock_list_channel_invite_candidates.return_value = fake_page

        badge_1 = ChatRoleBadge(
            kind=CHAT_ROLE_ACCOUNTANT,
            label=CHAT_ROLE_LABELS[CHAT_ROLE_ACCOUNTANT],
            accountant_owner_name="head_accountant",
            accountant_owner_label="سرحسابدار",
        )

        async def _load_badges_side_effect(
            db: object, user_ids: set[int] | list[int]
        ) -> dict[int, ChatRoleBadge]:
            self.assertEqual(mock_list_channel_invite_candidates.await_count, 1)
            self.assertEqual(user_ids, {candidate_1.user_id, candidate_2.user_id})
            return {candidate_1.user_id: badge_1}

        mock_load_chat_role_badges.side_effect = _load_badges_side_effect

        q_value = "candidate-search"
        limit_value = 10
        offset_value = 5
        exclude_chat_id_value = 99

        response = await get_channel_invite_candidates(
            q=q_value,
            limit=limit_value,
            offset=offset_value,
            exclude_chat_id=exclude_chat_id_value,
            current_user=current_user_sentinel,
            db=db_sentinel,
        )

        mock_list_channel_invite_candidates.assert_awaited_once_with(
            db_sentinel,
            query_text=q_value,
            limit=limit_value,
            offset=offset_value,
            exclude_chat_id=exclude_chat_id_value,
        )
        mock_load_chat_role_badges.assert_awaited_once_with(
            db_sentinel,
            {candidate_1.user_id, candidate_2.user_id},
        )

        self.assertIsInstance(response, ChannelInviteCandidateListResponse)
        self.assertEqual(response.total, 25)
        self.assertEqual(response.active_total, 18)
        self.assertEqual(len(response.items), 2)

        item_0 = response.items[0]
        self.assertIsInstance(item_0, ChannelInviteCandidateRead)
        self.assertEqual(item_0.user_id, 202)
        self.assertEqual(item_0.account_name, "candidate_bravo")
        self.assertEqual(item_0.full_name, "Bravo Candidate")
        self.assertEqual(item_0.mobile_number, "+989120000002")
        self.assertEqual(item_0.avatar_file_id, "avatar_bravo_001")
        self.assertIs(item_0.is_already_member, False)
        self.assertEqual(item_0.chat_role_kind, "accountant")
        self.assertEqual(item_0.chat_role_label, "حسابدار")
        self.assertEqual(item_0.chat_accountant_owner_name, "head_accountant")
        self.assertEqual(item_0.chat_accountant_owner_label, "سرحسابدار")
        self.assertEqual(item_0.customer_management_name, "Corporate Accounts")

        item_1 = response.items[1]
        self.assertIsInstance(item_1, ChannelInviteCandidateRead)
        self.assertEqual(item_1.user_id, 101)
        self.assertEqual(item_1.account_name, "candidate_alpha")
        self.assertEqual(item_1.full_name, "Alpha Candidate")
        self.assertEqual(item_1.mobile_number, "+989120000001")
        self.assertIsNone(item_1.avatar_file_id)
        self.assertIs(item_1.is_already_member, True)
        self.assertIsNone(item_1.chat_role_kind)
        self.assertIsNone(item_1.chat_role_label)
        self.assertIsNone(item_1.chat_accountant_owner_name)
        self.assertIsNone(item_1.chat_accountant_owner_label)
        self.assertIsNone(item_1.customer_management_name)


if __name__ == "__main__":
    unittest.main()
