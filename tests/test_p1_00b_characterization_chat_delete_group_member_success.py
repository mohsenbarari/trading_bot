"""Direct-characterization tests for api.routers.chat.delete_group_member under P1-00b.

Characterizes delete_group_member direct success path under WA-P1-00B-FREEZE-20260904-001.
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

from api.routers.chat import delete_group_member  # noqa: E402
from api.routers.chat_schemas import (  # noqa: E402
    GroupMemberMutationResponse,
)
from core.services.chat_room_service import (  # noqa: E402
    GroupMemberMutationSummary,
)


class CharacterizeChatDeleteGroupMemberSuccessTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing delete_group_member direct success path."""

    @patch("api.routers.chat.remove_group_member", new_callable=AsyncMock)
    @patch("api.routers.chat.get_active_group_admin_or_403", new_callable=AsyncMock)
    @patch("api.routers.chat.get_group_or_404", new_callable=AsyncMock)
    async def test_delete_group_member_success(
        self,
        mock_get_group_or_404: AsyncMock,
        mock_get_active_group_admin_or_403: AsyncMock,
        mock_remove_group_member: AsyncMock,
    ) -> None:
        fake_db = object()
        fake_group = object()
        fake_admin_member = object()
        current_user = SimpleNamespace(id=42)

        fake_summary = GroupMemberMutationSummary(
            chat_id=321,
            user_id=77,
            role=None,
            removed=True,
            left=False,
            member_count=4,
            unchanged=False,
        )

        recorded_events: list[str] = []

        async def fake_get_group(*args: object, **kwargs: object) -> object:
            recorded_events.append("get_group_or_404")
            return fake_group

        async def fake_get_active_admin(*args: object, **kwargs: object) -> object:
            recorded_events.append("get_active_group_admin_or_403")
            return fake_admin_member

        async def fake_remove_member(
            *args: object, **kwargs: object
        ) -> GroupMemberMutationSummary:
            recorded_events.append("remove_group_member")
            return fake_summary

        mock_get_group_or_404.side_effect = fake_get_group
        mock_get_active_group_admin_or_403.side_effect = fake_get_active_admin
        mock_remove_group_member.side_effect = fake_remove_member

        response = await delete_group_member(
            chat_id=321,
            user_id=77,
            current_user=current_user,
            db=fake_db,
        )

        self.assertIsInstance(response, GroupMemberMutationResponse)
        self.assertEqual(response.chat_id, 321)
        self.assertEqual(response.user_id, 77)
        self.assertIsNone(response.role)
        self.assertEqual(response.role, None)
        self.assertIs(response.removed, True)
        self.assertEqual(response.removed, True)
        self.assertIs(response.left, False)
        self.assertEqual(response.left, False)
        self.assertEqual(response.member_count, 4)
        self.assertIs(response.unchanged, False)
        self.assertEqual(response.unchanged, False)
        self.assertNotEqual(response.user_id, current_user.id)

        self.assertEqual(
            recorded_events,
            [
                "get_group_or_404",
                "get_active_group_admin_or_403",
                "remove_group_member",
            ],
        )

        mock_get_group_or_404.assert_awaited_once_with(fake_db, 321)
        mock_get_active_group_admin_or_403.assert_awaited_once_with(
            fake_db,
            chat=fake_group,
            user_id=42,
        )
        mock_remove_group_member.assert_awaited_once_with(
            fake_db,
            chat=fake_group,
            acting_user_id=42,
            user_id=77,
        )


if __name__ == "__main__":
    unittest.main()
