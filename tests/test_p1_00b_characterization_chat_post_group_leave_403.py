"""Direct-characterization tests for api.routers.chat.post_group_leave under P1-00b.

Characterizes leave_group_chat 403 missing-active-membership rejection under WA-P1-00B-FREEZE-20260904-001.
This is direct router exception-propagation characterization with a mocked service;
it does not prove the actual membership guard, persistence, concurrency/reload,
execute a new seed, promote evidence, reduce blockers or complete P1-00b.
No Stage, registry, project-document or runtime changes.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from fastapi import HTTPException  # noqa: E402

from api.routers.chat import post_group_leave  # noqa: E402


class CharacterizeChatPostGroupLeave403Tests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing post_group_leave 403 missing-active-membership failure boundary."""

    @patch("api.routers.chat.GroupMemberMutationResponse")
    @patch("api.routers.chat.leave_group_chat", new_callable=AsyncMock)
    @patch("api.routers.chat.get_group_or_404", new_callable=AsyncMock)
    async def test_post_group_leave_propagates_missing_active_membership_403(
        self,
        mock_get_group_or_404: AsyncMock,
        mock_leave_group_chat: AsyncMock,
        mock_response_cls: MagicMock,
    ) -> None:
        db_sentinel = object()
        group_sentinel = object()
        sentinel_error = HTTPException(
            status_code=403,
            detail="You are not a member of this group",
        )

        recorded_events: list[str] = []

        async def fake_get_group_or_404(*args: object, **kwargs: object) -> object:
            recorded_events.append("get_group_or_404")
            return group_sentinel

        async def fake_leave_group_chat(
            *args: object, **kwargs: object
        ) -> None:
            recorded_events.append("leave_group_chat")
            raise sentinel_error

        mock_get_group_or_404.side_effect = fake_get_group_or_404
        mock_leave_group_chat.side_effect = fake_leave_group_chat

        with self.assertRaises(HTTPException) as context:
            await post_group_leave(
                chat_id=321,
                current_user=SimpleNamespace(id=42),
                db=db_sentinel,
            )

        self.assertIs(context.exception, sentinel_error)
        self.assertEqual(context.exception.status_code, 403)
        self.assertEqual(
            context.exception.detail,
            "You are not a member of this group",
        )

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
        mock_response_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
