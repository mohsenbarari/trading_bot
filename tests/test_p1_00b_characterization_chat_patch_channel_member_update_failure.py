"""Direct P1-00b characterization under Assignment WA-P1-00B-FREEZE-20260904-001.

Direct-handler failure-only evidence characterizing patch_channel_member when
update_channel_member raises following successful get_channel_or_404 lookup.
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
from api.routers.chat_schemas import ChannelMemberUpdateRequest
from core.enums import ChatMemberRole


class TestPatchChannelMemberUpdateFailure(unittest.IsolatedAsyncioTestCase):
    """Direct-handler failure boundary test for patch_channel_member."""

    async def test_patch_channel_member_update_failure(self) -> None:
        channel_sentinel = object()
        injected_error = RuntimeError("synthetic update failure")
        chat_id = 42
        user_id = 99
        current_user = object()
        db = object()
        data = ChannelMemberUpdateRequest(role="admin")
        self.assertIs(data.remove_member, False)

        service_failure_reached = False

        with patch(
            "api.routers.chat.get_channel_or_404", new_callable=AsyncMock
        ) as mock_get_channel, patch(
            "api.routers.chat.update_channel_member", new_callable=AsyncMock
        ) as mock_update_member, patch(
            "api.routers.chat.ChannelMemberMutationResponse"
        ) as mock_response_cls:
            mock_get_channel.return_value = channel_sentinel

            def update_side_effect(*_args: object, **_kwargs: object) -> None:
                nonlocal service_failure_reached
                service_failure_reached = True
                mock_get_channel.assert_awaited_once_with(db, chat_id)
                mock_response_cls.assert_not_called()
                raise injected_error

            mock_update_member.side_effect = update_side_effect

            with self.assertRaises(RuntimeError) as ctx:
                await patch_channel_member(
                    chat_id=chat_id,
                    user_id=user_id,
                    data=data,
                    current_user=current_user,
                    db=db,
                )

            self.assertTrue(service_failure_reached)
            self.assertIs(ctx.exception, injected_error)
            mock_get_channel.assert_awaited_once_with(db, chat_id)
            mock_update_member.assert_awaited_once_with(
                db,
                chat=channel_sentinel,
                user_id=user_id,
                role=ChatMemberRole.ADMIN,
                remove_member=False,
            )
            mock_response_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
