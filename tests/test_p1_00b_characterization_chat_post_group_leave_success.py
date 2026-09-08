"""Direct-characterization tests for api.routers.chat.post_group_leave under P1-00b.

Characterizes post_group_leave successful group leave under WA-P1-00B-FREEZE-20260904-001.
This is direct characterization under the active Assignment only, without executed-seed registry changes,
evidence promotion, blocker reduction or P1-00b completion.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import post_group_leave  # noqa: E402
from api.routers.chat_schemas import (  # noqa: E402
    GroupMemberMutationResponse,
)
from core.services.chat_room_service import (  # noqa: E402
    GroupMemberMutationSummary,
)


class CharacterizeChatPostGroupLeaveSuccessTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing post_group_leave success path."""

    @patch("api.routers.chat.leave_group_chat", new_callable=AsyncMock)
    @patch("api.routers.chat.get_group_or_404", new_callable=AsyncMock)
    async def test_post_group_leave_success(
        self,
        mock_get_group_or_404: AsyncMock,
        mock_leave_group_chat: AsyncMock,
    ) -> None:
        db_sentinel = object()
        group_sentinel = object()

        fake_summary = GroupMemberMutationSummary(
            chat_id=321,
            user_id=42,
            role=None,
            removed=False,
            left=True,
            member_count=4,
            unchanged=False,
        )

        recorded_events: list[str] = []

        async def fake_get_group_or_404(*args: object, **kwargs: object) -> object:
            recorded_events.append("get_group_or_404")
            return group_sentinel

        async def fake_leave_group_chat(
            *args: object, **kwargs: object
        ) -> GroupMemberMutationSummary:
            recorded_events.append("leave_group_chat")
            return fake_summary

        mock_get_group_or_404.side_effect = fake_get_group_or_404
        mock_leave_group_chat.side_effect = fake_leave_group_chat

        response = await post_group_leave(
            chat_id=321,
            current_user=SimpleNamespace(id=42),
            db=db_sentinel,
        )

        self.assertIsInstance(response, GroupMemberMutationResponse)
        self.assertEqual(response.chat_id, 321)
        self.assertEqual(response.user_id, 42)
        self.assertIsNone(response.role)
        self.assertEqual(response.removed, False)
        self.assertEqual(response.left, True)
        self.assertEqual(response.member_count, 4)
        self.assertEqual(response.unchanged, False)

        self.assertEqual(
            recorded_events,
            [
                "get_group_or_404",
                "leave_group_chat",
            ],
        )

        mock_get_group_or_404.assert_awaited_once_with(db_sentinel, 321)
        mock_leave_group_chat.assert_awaited_once_with(
            db_sentinel,
            chat=group_sentinel,
            user_id=42,
        )


if __name__ == "__main__":
    unittest.main()
