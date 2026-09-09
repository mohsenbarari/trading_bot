"""Direct P1-00b characterization under Assignment WA-P1-00B-FREEZE-20260904-001.

Direct-handler failure-only evidence characterizing bulk_invite_channel_members
propagating a bulk_add_channel_members service failure after successful lookup.
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

from api.routers.chat import bulk_invite_channel_members
from api.routers.chat_schemas import ChannelBulkMemberAddRequest


class TestBulkInviteChannelMembersServiceFailure(unittest.IsolatedAsyncioTestCase):
    """Direct-handler failure boundary test for bulk_invite_channel_members service failure."""

    async def test_bulk_invite_channel_members_service_failure(self) -> None:
        chat_id = 42
        current_user = object()
        db = object()
        channel = object()
        service_error = RuntimeError("synthetic bulk member service failure")

        data = ChannelBulkMemberAddRequest(user_ids=[99])
        self.assertEqual(data.user_ids, [99])
        self.assertIs(data.select_all_active_users, False)

        with patch(
            "api.routers.chat.get_channel_or_404", new_callable=AsyncMock
        ) as mock_get_channel, patch(
            "api.routers.chat.bulk_add_channel_members", new_callable=AsyncMock
        ) as mock_bulk_add, patch(
            "api.routers.chat.ChannelBulkMemberAddResponse"
        ) as mock_response_cls:
            mock_get_channel.return_value = channel

            def fake_bulk_add(*args: object, **kwargs: object) -> None:
                mock_get_channel.assert_awaited_once_with(db, 42)
                mock_response_cls.assert_not_called()
                raise service_error

            mock_bulk_add.side_effect = fake_bulk_add

            with self.assertRaises(RuntimeError) as ctx:
                await bulk_invite_channel_members(
                    chat_id=chat_id,
                    data=data,
                    current_user=current_user,
                    db=db,
                )

            self.assertIs(ctx.exception, service_error)
            mock_get_channel.assert_awaited_once_with(db, 42)
            mock_bulk_add.assert_awaited_once_with(
                db,
                chat=channel,
                user_ids=[99],
                select_all_active_users=False,
            )
            mock_response_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
