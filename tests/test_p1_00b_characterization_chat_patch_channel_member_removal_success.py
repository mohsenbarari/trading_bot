"""Direct P1-00b characterization under Assignment WA-P1-00B-FREEZE-20260904-001.

Direct-handler success-only evidence characterizing patch_channel_member when
update_channel_member successfully removes a member following get_channel_or_404 lookup.
Contains no HTTP/auth, persistence, concurrency/reload, executed-seed promotion,
canonical blocker reduction, or Stage completion claims.
P1-00b remains IN_PROGRESS; existing tests/source/docs remain unchanged.
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

from api.routers.chat import patch_channel_member
from api.routers.chat_schemas import (
    ChannelMemberMutationResponse,
    ChannelMemberUpdateRequest,
)
from core.services.chat_room_service import ChannelMemberMutationSummary


class TestPatchChannelMemberRemovalSuccess(unittest.IsolatedAsyncioTestCase):
    """Direct-handler success boundary test for patch_channel_member removal."""

    async def test_patch_channel_member_removal_success(self) -> None:
        chat_id = 42
        user_id = 99
        member_count = 5
        current_user = object()
        db = object()
        channel_sentinel = object()

        data = ChannelMemberUpdateRequest(remove_member=True)
        self.assertIsNone(data.role)
        self.assertIs(data.remove_member, True)

        mutation_summary = ChannelMemberMutationSummary(
            chat_id=chat_id,
            user_id=user_id,
            role=None,
            removed=True,
            member_count=member_count,
        )

        service_executed = False

        with patch(
            "api.routers.chat.get_channel_or_404", new_callable=AsyncMock
        ) as mock_get_channel, patch(
            "api.routers.chat.update_channel_member", new_callable=AsyncMock
        ) as mock_update_member:
            mock_get_channel.return_value = channel_sentinel
            mock_update_member.return_value = mutation_summary

            def update_side_effect(*_args: object, **_kwargs: object) -> ChannelMemberMutationSummary:
                nonlocal service_executed
                service_executed = True
                mock_get_channel.assert_awaited_once_with(db, chat_id)
                return mutation_summary

            mock_update_member.side_effect = update_side_effect

            response = await patch_channel_member(
                chat_id=chat_id,
                user_id=user_id,
                data=data,
                current_user=current_user,
                db=db,
            )

            self.assertTrue(service_executed)
            mock_get_channel.assert_awaited_once_with(db, chat_id)
            mock_update_member.assert_awaited_once_with(
                db,
                chat=channel_sentinel,
                user_id=user_id,
                role=None,
                remove_member=True,
            )

            self.assertIs(type(response), ChannelMemberMutationResponse)
            self.assertIsInstance(response, ChannelMemberMutationResponse)
            self.assertEqual(response.chat_id, chat_id)
            self.assertEqual(response.user_id, user_id)
            self.assertIsNone(response.role)
            self.assertIs(response.removed, True)
            self.assertEqual(response.member_count, member_count)
            self.assertIs(response.left, False)
            self.assertIs(response.unchanged, False)
            self.assertEqual(
                response.model_dump(),
                {
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "role": None,
                    "removed": True,
                    "member_count": member_count,
                    "left": False,
                    "unchanged": False,
                },
            )


if __name__ == "__main__":
    unittest.main()
