"""Direct P1-00b characterization under Assignment WA-P1-00B-FREEZE-20260904-001.

Direct-handler success-only evidence characterizing bulk_invite_channel_members
when channel lookup and bulk member addition succeed.
Contains no HTTP/auth, persistence, concurrency/reload, executed-seed promotion,
canonical blocker reduction, or Stage completion claims.
P1-00b remains IN_PROGRESS; existing tests/source/docs remain unchanged.
"""

from __future__ import annotations

import os
import types
import unittest
from unittest.mock import AsyncMock, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import bulk_invite_channel_members
from api.routers.chat_schemas import (
    ChannelBulkMemberAddRequest,
    ChannelBulkMemberAddResponse,
)


class TestBulkInviteChannelMembersSuccess(unittest.IsolatedAsyncioTestCase):
    """Direct-handler success boundary test for bulk_invite_channel_members."""

    async def test_bulk_invite_channel_members_success(self) -> None:
        chat_id = 42
        current_user = object()
        db = object()
        channel = object()

        data = ChannelBulkMemberAddRequest(select_all_active_users=True)
        self.assertEqual(data.user_ids, [])
        self.assertIs(data.select_all_active_users, True)

        processed_user_ids = [101, 102, 103, 104, 105, 106, 107, 108, 109, 110]
        summary = types.SimpleNamespace(
            chat_id=chat_id,
            processed_user_ids=processed_user_ids,
            added_count=5,
            reactivated_count=3,
            already_member_count=2,
            member_count=25,
            select_all_active_users=True,
        )

        with patch(
            "api.routers.chat.get_channel_or_404", new_callable=AsyncMock
        ) as mock_get_channel, patch(
            "api.routers.chat.bulk_add_channel_members", new_callable=AsyncMock
        ) as mock_bulk_add:
            mock_get_channel.return_value = channel

            def fake_bulk_add(*args: object, **kwargs: object) -> types.SimpleNamespace:
                mock_get_channel.assert_awaited_once_with(db, chat_id)
                return summary

            mock_bulk_add.side_effect = fake_bulk_add

            response = await bulk_invite_channel_members(
                chat_id=chat_id,
                data=data,
                current_user=current_user,
                db=db,
            )

            mock_get_channel.assert_awaited_once_with(db, chat_id)
            mock_bulk_add.assert_awaited_once_with(
                db,
                chat=channel,
                user_ids=[],
                select_all_active_users=True,
            )

            self.assertIsInstance(response, ChannelBulkMemberAddResponse)
            self.assertEqual(response.chat_id, summary.chat_id)
            self.assertEqual(response.processed_user_ids, summary.processed_user_ids)
            self.assertEqual(
                response.processed_user_ids,
                [101, 102, 103, 104, 105, 106, 107, 108, 109, 110],
            )
            self.assertEqual(response.added_count, summary.added_count)
            self.assertEqual(response.reactivated_count, summary.reactivated_count)
            self.assertEqual(response.already_member_count, summary.already_member_count)
            self.assertEqual(response.member_count, summary.member_count)
            self.assertEqual(
                response.select_all_active_users, summary.select_all_active_users
            )
            self.assertIs(response.select_all_active_users, True)


if __name__ == "__main__":
    unittest.main()
