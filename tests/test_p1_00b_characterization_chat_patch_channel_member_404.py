"""Direct P1-00b characterization under Assignment WA-P1-00B-FREEZE-20260904-001.

Direct-handler failure-only evidence characterizing patch_channel_member when
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

from api.routers.chat import patch_channel_member
from api.routers.chat_schemas import ChannelMemberUpdateRequest


class TestPatchChannelMemberLookup404(unittest.IsolatedAsyncioTestCase):
    """Direct-handler failure boundary test for patch_channel_member lookup 404."""

    async def test_patch_channel_member_lookup_404(self) -> None:
        chat_id = 42
        user_id = 99
        current_user = object()
        db = object()
        data = ChannelMemberUpdateRequest(role="admin")

        lookup_error = HTTPException(status_code=404, detail="Channel not found")

        with patch(
            "api.routers.chat.get_channel_or_404", new_callable=AsyncMock
        ) as mock_get_channel, patch(
            "api.routers.chat.update_channel_member", new_callable=AsyncMock
        ) as mock_update_member, patch(
            "api.routers.chat.ChannelMemberMutationResponse"
        ) as mock_response_cls:
            mock_get_channel.side_effect = lookup_error

            with self.assertRaises(HTTPException) as ctx:
                await patch_channel_member(
                    chat_id=chat_id,
                    user_id=user_id,
                    data=data,
                    current_user=current_user,
                    db=db,
                )

            self.assertIs(ctx.exception, lookup_error)
            self.assertEqual(ctx.exception.status_code, 404)
            self.assertEqual(ctx.exception.detail, "Channel not found")
            mock_get_channel.assert_awaited_once_with(db, chat_id)
            mock_update_member.assert_not_called()
            mock_update_member.assert_not_awaited()
            mock_response_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
