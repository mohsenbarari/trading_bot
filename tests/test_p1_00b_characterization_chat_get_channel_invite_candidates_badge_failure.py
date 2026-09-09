"""Direct-characterization tests for api.routers.chat.get_channel_invite_candidates under P1-00b.

Characterizes load_chat_role_badges failure boundary under WA-P1-00B-FREEZE-20260904-001.
Direct-handler badge-failure-only scope: no HTTP/auth, persistence,
concurrency/reload, executed-seed promotion, canonical-blocker reduction or Stage
acceptance/completion claims. P1-00b remains IN_PROGRESS.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import get_channel_invite_candidates  # noqa: E402
from core.services.chat_room_service import (  # noqa: E402
    ChannelInviteCandidate,
    ChannelInviteCandidatePage,
)


class CharacterizeChatGetChannelInviteCandidatesBadgeFailureTests(
    unittest.IsolatedAsyncioTestCase
):
    """Hermetic unit tests characterizing get_channel_invite_candidates badge lookup failure boundary."""

    @patch("api.routers.chat.ChannelInviteCandidateListResponse")
    @patch("api.routers.chat.load_chat_role_badges", new_callable=AsyncMock)
    @patch("api.routers.chat.list_channel_invite_candidates", new_callable=AsyncMock)
    async def test_get_channel_invite_candidates_raises_when_badge_lookup_fails(
        self,
        mock_list_channel_invite_candidates: AsyncMock,
        mock_load_chat_role_badges: AsyncMock,
        mock_response_cls: MagicMock,
    ) -> None:
        db_sentinel = object()
        current_user_sentinel = object()
        sentinel_error = RuntimeError("load-chat-role-badges-failure-sentinel")

        candidate_1 = ChannelInviteCandidate(
            user_id=101,
            account_name="synthetic_user_one",
            full_name="Synthetic User One",
            mobile_number="",
        )
        candidate_2 = ChannelInviteCandidate(
            user_id=202,
            account_name="synthetic_user_two",
            full_name="Synthetic User Two",
            mobile_number="",
        )
        candidate_page = ChannelInviteCandidatePage(
            items=[candidate_1, candidate_2],
            total=42,
            active_total=35,
        )

        call_order: list[str] = []

        async def fake_list_candidates(
            *args: object, **kwargs: object
        ) -> ChannelInviteCandidatePage:
            call_order.append("list_channel_invite_candidates")
            return candidate_page

        async def fake_load_badges(*args: object, **kwargs: object) -> None:
            self.assertEqual(
                call_order,
                ["list_channel_invite_candidates"],
                "Listing must be awaited exactly once before badge lookup is called",
            )
            self.assertEqual(
                mock_list_channel_invite_candidates.await_count,
                1,
                "Listing must be awaited exactly once before badge lookup is called",
            )
            call_order.append("load_chat_role_badges")
            raise sentinel_error

        mock_list_channel_invite_candidates.side_effect = fake_list_candidates
        mock_load_chat_role_badges.side_effect = fake_load_badges

        q_value = "candidate-query"
        limit_value = 25
        offset_value = 10
        exclude_chat_id_value = 77

        with self.assertRaises(RuntimeError) as context:
            await get_channel_invite_candidates(
                q=q_value,
                limit=limit_value,
                offset=offset_value,
                exclude_chat_id=exclude_chat_id_value,
                current_user=current_user_sentinel,
                db=db_sentinel,
            )

        self.assertIs(context.exception, sentinel_error)
        self.assertEqual(
            call_order,
            ["list_channel_invite_candidates", "load_chat_role_badges"],
        )

        mock_list_channel_invite_candidates.assert_awaited_once_with(
            db_sentinel,
            query_text=q_value,
            limit=limit_value,
            offset=offset_value,
            exclude_chat_id=exclude_chat_id_value,
        )
        self.assertIs(
            mock_list_channel_invite_candidates.await_args.args[0],
            db_sentinel,
        )

        expected_user_ids = {candidate_1.user_id, candidate_2.user_id}
        mock_load_chat_role_badges.assert_awaited_once_with(
            db_sentinel,
            expected_user_ids,
        )
        self.assertIs(
            mock_load_chat_role_badges.await_args.args[0],
            db_sentinel,
        )
        self.assertEqual(
            mock_load_chat_role_badges.await_args.args[1],
            expected_user_ids,
        )

        mock_response_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
