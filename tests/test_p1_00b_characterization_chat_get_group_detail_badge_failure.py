"""Direct-characterization tests for api.routers.chat.get_group_detail under P1-00b.

Characterizes load_chat_role_badges failure boundary under WA-P1-00B-FREEZE-20260904-001.
This is direct characterization only and does not update the executed-seed registry,
reduce a canonical blocker, promote evidence, or mark P1-00b complete.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import get_group_detail  # noqa: E402


class CharacterizeChatGetGroupDetailBadgeFailureTests(
    unittest.IsolatedAsyncioTestCase
):
    """Hermetic unit tests characterizing get_group_detail load_chat_role_badges failure boundary."""

    @patch("api.routers.chat.load_chat_role_badges", new_callable=AsyncMock)
    @patch("api.routers.chat.list_group_members", new_callable=AsyncMock)
    @patch("api.routers.chat.count_active_chat_members", new_callable=AsyncMock)
    @patch("api.routers.chat.get_active_group_member_or_403", new_callable=AsyncMock)
    @patch("api.routers.chat.get_group_or_404", new_callable=AsyncMock)
    async def test_get_group_detail_load_chat_role_badges_failure(
        self,
        mock_get_group_or_404: AsyncMock,
        mock_get_active_group_member_or_403: AsyncMock,
        mock_count_active_chat_members: AsyncMock,
        mock_list_group_members: AsyncMock,
        mock_load_chat_role_badges: AsyncMock,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42)
        returned_group = SimpleNamespace(id=654)
        opaque_member = object()
        returned_members = [
            SimpleNamespace(user_id=77),
            SimpleNamespace(user_id=88),
        ]
        sentinel_error = RuntimeError("load_chat_role_badges sentinel failure")

        call_order: list[str] = []

        async def fake_get_group_or_404(
            *_args: object, **_kwargs: object
        ) -> SimpleNamespace:
            call_order.append("get_group_or_404")
            return returned_group

        async def fake_get_active_group_member_or_403(
            *_args: object, **_kwargs: object
        ) -> object:
            call_order.append("get_active_group_member_or_403")
            return opaque_member

        async def fake_count_active_chat_members(
            *_args: object, **_kwargs: object
        ) -> int:
            call_order.append("count_active_chat_members")
            return 7

        async def fake_list_group_members(
            *_args: object, **_kwargs: object
        ) -> list[SimpleNamespace]:
            call_order.append("list_group_members")
            return returned_members

        async def fake_load_chat_role_badges(
            *_args: object, **_kwargs: object
        ) -> dict[int, object]:
            call_order.append("load_chat_role_badges")
            raise sentinel_error

        mock_get_group_or_404.side_effect = fake_get_group_or_404
        mock_get_active_group_member_or_403.side_effect = (
            fake_get_active_group_member_or_403
        )
        mock_count_active_chat_members.side_effect = (
            fake_count_active_chat_members
        )
        mock_list_group_members.side_effect = fake_list_group_members
        mock_load_chat_role_badges.side_effect = fake_load_chat_role_badges

        with self.assertRaises(RuntimeError) as context:
            await get_group_detail(
                chat_id=321,
                current_user=current_user,
                db=fake_db,
            )

        self.assertIs(context.exception, sentinel_error)
        self.assertEqual(
            call_order,
            [
                "get_group_or_404",
                "get_active_group_member_or_403",
                "count_active_chat_members",
                "list_group_members",
                "load_chat_role_badges",
            ],
        )
        mock_get_group_or_404.assert_awaited_once_with(fake_db, 321)
        mock_get_active_group_member_or_403.assert_awaited_once_with(
            fake_db,
            chat=returned_group,
            user_id=42,
        )
        mock_count_active_chat_members.assert_awaited_once_with(fake_db, 654)
        mock_list_group_members.assert_awaited_once_with(
            fake_db,
            chat=returned_group,
        )
        mock_load_chat_role_badges.assert_awaited_once_with(
            fake_db,
            {77, 88},
        )


if __name__ == "__main__":
    unittest.main()
