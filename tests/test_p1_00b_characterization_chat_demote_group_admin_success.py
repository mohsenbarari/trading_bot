"""Direct-characterization tests for api.routers.chat.demote_group_admin under P1-00b.

Characterizes demote_group_admin successful admin demotion under WA-P1-00B-FREEZE-20260904-001.
This is direct characterization only under the active Assignment and does not update
executed-seed registry, reduce canonical blockers, promote evidence or complete P1-00b.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import demote_group_admin  # noqa: E402
from api.routers.chat_schemas import (  # noqa: E402
    GroupMemberMutationResponse,
)
from core.enums import ChatMemberRole  # noqa: E402
from core.services.chat_room_service import (  # noqa: E402
    GroupMemberMutationSummary,
)


class CharacterizeChatDemoteGroupAdminSuccessTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing demote_group_admin admin demotion success path."""

    @patch("api.routers.chat.update_group_admin_status", new_callable=AsyncMock)
    @patch("api.routers.chat.get_active_group_admin_or_403", new_callable=AsyncMock)
    @patch("api.routers.chat.get_group_or_404", new_callable=AsyncMock)
    async def test_demote_group_admin_successfully_demotes_admin(
        self,
        mock_get_group_or_404: AsyncMock,
        mock_get_active_group_admin_or_403: AsyncMock,
        mock_update_group_admin_status: AsyncMock,
    ) -> None:
        fake_db = object()
        fake_group = object()
        fake_admin_member = object()
        current_user = SimpleNamespace(id=42)

        fake_summary = GroupMemberMutationSummary(
            chat_id=321,
            user_id=77,
            role=ChatMemberRole.MEMBER,
            removed=False,
            left=False,
            member_count=5,
            unchanged=False,
        )

        recorded_events: list[str] = []

        async def fake_get_group(*args: object, **kwargs: object) -> object:
            recorded_events.append("get_group_or_404")
            return fake_group

        async def fake_get_active_admin(*args: object, **kwargs: object) -> object:
            recorded_events.append("get_active_group_admin_or_403")
            return fake_admin_member

        async def fake_update_admin_status(
            *args: object, **kwargs: object
        ) -> GroupMemberMutationSummary:
            recorded_events.append("update_group_admin_status")
            return fake_summary

        mock_get_group_or_404.side_effect = fake_get_group
        mock_get_active_group_admin_or_403.side_effect = fake_get_active_admin
        mock_update_group_admin_status.side_effect = fake_update_admin_status

        response = await demote_group_admin(
            chat_id=321,
            user_id=77,
            current_user=current_user,
            db=fake_db,
        )

        self.assertIsInstance(response, GroupMemberMutationResponse)
        self.assertEqual(response.chat_id, 321)
        self.assertEqual(response.user_id, 77)
        self.assertEqual(response.role, "member")
        self.assertEqual(response.removed, False)
        self.assertEqual(response.left, False)
        self.assertEqual(response.member_count, 5)
        self.assertEqual(response.unchanged, False)
        self.assertNotEqual(response.user_id, current_user.id)

        self.assertEqual(
            recorded_events,
            [
                "get_group_or_404",
                "get_active_group_admin_or_403",
                "update_group_admin_status",
            ],
        )

        mock_get_group_or_404.assert_awaited_once_with(fake_db, 321)
        mock_get_active_group_admin_or_403.assert_awaited_once_with(
            fake_db,
            chat=fake_group,
            user_id=42,
        )
        mock_update_group_admin_status.assert_awaited_once_with(
            fake_db,
            chat=fake_group,
            user_id=77,
            make_admin=False,
        )


if __name__ == "__main__":
    unittest.main()
