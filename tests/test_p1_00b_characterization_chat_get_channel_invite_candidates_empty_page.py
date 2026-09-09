"""Direct-characterization tests for api.routers.chat.get_channel_invite_candidates under P1-00b.

Characterizes direct-handler empty-page success path under WA-P1-00B-FREEZE-20260904-001.
P1-00b remains IN_PROGRESS.
This is direct characterization only, without executed-seed promotion,
blocker reduction, Stage acceptance/completion or Astra measurement completion.
Does not claim HTTP/auth, persistence, concurrency/reload or Stage acceptance.
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
)
from core.services.chat_room_service import (  # noqa: E402
    ChannelInviteCandidatePage,
)


class CharacterizeChatGetChannelInviteCandidatesEmptyPageTests(
    unittest.IsolatedAsyncioTestCase
):
    """Hermetic unit tests characterizing get_channel_invite_candidates empty page success path."""

    @patch("api.routers.chat.load_chat_role_badges", new_callable=AsyncMock)
    @patch("api.routers.chat.list_channel_invite_candidates", new_callable=AsyncMock)
    async def test_get_channel_invite_candidates_empty_page_success(
        self,
        mock_list_channel_invite_candidates: AsyncMock,
        mock_load_chat_role_badges: AsyncMock,
    ) -> None:
        db_sentinel = object()
        current_user_sentinel = object()
        mock_list_channel_invite_candidates.return_value = ChannelInviteCandidatePage(
            items=[],
            total=7,
            active_total=11,
        )

        async def _fake_load_chat_role_badges(
            lookup_db: object,
            lookup_user_ids: set[int],
        ) -> dict[int, object]:
            self.assertEqual(mock_list_channel_invite_candidates.await_count, 1)
            mock_list_channel_invite_candidates.assert_awaited_once()
            return {}

        mock_load_chat_role_badges.side_effect = _fake_load_chat_role_badges

        response = await get_channel_invite_candidates(
            q=None,
            limit=5,
            offset=20,
            exclude_chat_id=None,
            current_user=current_user_sentinel,
            db=db_sentinel,
        )

        mock_list_channel_invite_candidates.assert_awaited_once_with(
            db_sentinel,
            query_text=None,
            limit=5,
            offset=20,
            exclude_chat_id=None,
        )
        mock_load_chat_role_badges.assert_called_once_with(db_sentinel, set())
        mock_load_chat_role_badges.assert_awaited_once_with(db_sentinel, set())

        self.assertIsInstance(response, ChannelInviteCandidateListResponse)
        self.assertEqual(response.items, [])
        self.assertEqual(response.total, 7)
        self.assertEqual(response.active_total, 11)
        self.assertNotEqual(response.total, len(response.items))
        self.assertNotEqual(response.active_total, len(response.items))


if __name__ == "__main__":
    unittest.main()
