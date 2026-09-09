"""Direct P1-00b characterization under Assignment WA-P1-00B-FREEZE-20260904-001.

Direct-handler failure-only evidence characterizing bulk_invite_channel_members when
get_channel_or_404 lookup fails with 404.
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

from fastapi import HTTPException

from api.routers.chat import bulk_invite_channel_members
from api.routers.chat_schemas import ChannelBulkMemberAddRequest


class TestBulkInviteChannelMembersLookup404(unittest.IsolatedAsyncioTestCase):
    """Direct-handler failure boundary test for bulk_invite_channel_members lookup 404."""

    async def test_bulk_invite_channel_members_lookup_404(self) -> None:
        chat_id = 42
        current_user = object()
        db = object()
        data = ChannelBulkMemberAddRequest(user_ids=[99])

        lookup_error = HTTPException(status_code=404, detail="Channel not found")

        with patch(
            "api.routers.chat.get_channel_or_404", new_callable=AsyncMock
        ) as mock_get_channel, patch(
            "api.routers.chat.bulk_add_channel_members", new_callable=AsyncMock
        ) as mock_bulk_add, patch(
            "api.routers.chat.ChannelBulkMemberAddResponse"
        ) as mock_response_cls:
            mock_get_channel.side_effect = lookup_error

            with self.assertRaises(HTTPException) as ctx:
                await bulk_invite_channel_members(
                    chat_id=chat_id,
                    data=data,
                    current_user=current_user,
                    db=db,
                )

            self.assertIs(ctx.exception, lookup_error)
            self.assertEqual(ctx.exception.status_code, 404)
            self.assertEqual(ctx.exception.detail, "Channel not found")
            mock_get_channel.assert_awaited_once_with(db, chat_id)
            mock_bulk_add.assert_not_called()
            mock_bulk_add.assert_not_awaited()
            mock_response_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
