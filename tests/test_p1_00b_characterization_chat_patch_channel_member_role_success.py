"""Direct P1-00b characterization under Assignment WA-P1-00B-FREEZE-20260904-001.

Direct-handler success-only evidence characterizing patch_channel_member when
update_channel_member successfully updates a member role to member following get_channel_or_404 lookup.
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
from core.enums import ChatMemberRole
from core.services.chat_room_service import ChannelMemberMutationSummary


class TestPatchChannelMemberRoleSuccess(unittest.IsolatedAsyncioTestCase):
    """Direct-handler success boundary test for patch_channel_member role update to member."""

    async def test_patch_channel_member_role_success(self) -> None:
        input_chat_id = 42
        input_user_id = 99
        summary_chat_id = 1042
        summary_user_id = 1099
        summary_member_count = 7
        current_user = object()
        db = object()
        channel_sentinel = object()

        data = ChannelMemberUpdateRequest(role="member")
        self.assertEqual(data.role, "member")
        self.assertIs(data.remove_member, False)

        mutation_summary = ChannelMemberMutationSummary(
            chat_id=summary_chat_id,
            user_id=summary_user_id,
            role=ChatMemberRole.MEMBER,
            removed=False,
            member_count=summary_member_count,
        )

        service_executed = False

        with patch(
            "api.routers.chat.get_channel_or_404", new_callable=AsyncMock
        ) as mock_get_channel, patch(
            "api.routers.chat.update_channel_member", new_callable=AsyncMock
        ) as mock_update_member:
            mock_get_channel.return_value = channel_sentinel
            mock_update_member.return_value = mutation_summary

            def update_side_effect(*_args: object, **kwargs: object) -> ChannelMemberMutationSummary:
                nonlocal service_executed
                service_executed = True
                mock_get_channel.assert_awaited_once_with(db, input_chat_id)
                self.assertIs(kwargs.get("role"), ChatMemberRole.MEMBER)
                return mutation_summary

            mock_update_member.side_effect = update_side_effect

            response = await patch_channel_member(
                chat_id=input_chat_id,
                user_id=input_user_id,
                data=data,
                current_user=current_user,
                db=db,
            )

            self.assertTrue(service_executed)
            mock_get_channel.assert_awaited_once_with(db, input_chat_id)
            mock_update_member.assert_awaited_once_with(
                db,
                chat=channel_sentinel,
                user_id=input_user_id,
                role=ChatMemberRole.MEMBER,
                remove_member=False,
            )
            self.assertIs(
                mock_update_member.call_args.kwargs["role"], ChatMemberRole.MEMBER
            )

            self.assertIs(type(response), ChannelMemberMutationResponse)
            self.assertIsInstance(response, ChannelMemberMutationResponse)
            self.assertEqual(response.chat_id, summary_chat_id)
            self.assertEqual(response.user_id, summary_user_id)
            self.assertNotEqual(response.chat_id, input_chat_id)
            self.assertNotEqual(response.user_id, input_user_id)
            self.assertEqual(response.role, "member")
            self.assertIs(response.removed, False)
            self.assertEqual(response.member_count, summary_member_count)
            self.assertIs(response.left, False)
            self.assertIs(response.unchanged, False)
            self.assertEqual(
                response.model_dump(),
                {
                    "chat_id": summary_chat_id,
                    "user_id": summary_user_id,
                    "role": "member",
                    "removed": False,
                    "member_count": summary_member_count,
                    "left": False,
                    "unchanged": False,
                },
            )


if __name__ == "__main__":
    unittest.main()
