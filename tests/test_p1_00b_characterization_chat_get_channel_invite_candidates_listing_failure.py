"""Direct-characterization tests for api.routers.chat.get_channel_invite_candidates under P1-00b.

Characterizes list_channel_invite_candidates failure boundary under WA-P1-00B-FREEZE-20260904-001.
This is direct characterization only, without executed-seed promotion,
canonical-blocker reduction or Stage completion.
Does not claim HTTP/auth, persistence, concurrency/reload or Stage acceptance.
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


class CharacterizeChatGetChannelInviteCandidatesListingFailureTests(
    unittest.IsolatedAsyncioTestCase
):
    """Hermetic unit tests characterizing get_channel_invite_candidates dependency failure boundary."""

    @patch("api.routers.chat.ChannelInviteCandidateListResponse")
    @patch("api.routers.chat.load_chat_role_badges", new_callable=AsyncMock)
    @patch("api.routers.chat.list_channel_invite_candidates", new_callable=AsyncMock)
    async def test_get_channel_invite_candidates_raises_when_listing_fails(
        self,
        mock_list_channel_invite_candidates: AsyncMock,
        mock_load_chat_role_badges: AsyncMock,
        mock_response_cls: MagicMock,
    ) -> None:
        db_sentinel = object()
        current_user_sentinel = object()
        sentinel_error = RuntimeError("list-channel-invite-candidates-failure-sentinel")
        mock_list_channel_invite_candidates.side_effect = sentinel_error

        q_value = "invite-query"
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
        mock_list_channel_invite_candidates.assert_awaited_once_with(
            db_sentinel,
            query_text=q_value,
            limit=limit_value,
            offset=offset_value,
            exclude_chat_id=exclude_chat_id_value,
        )
        mock_load_chat_role_badges.assert_not_called()
        mock_load_chat_role_badges.assert_not_awaited()
        mock_response_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
