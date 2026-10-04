"""Direct-characterization tests for api.routers.chat.get_group_message_seen_members service-exception propagation.

This module characterizes GROUP handler-boundary service-exception propagation only:
- For a GROUP room returned by get_room_or_404, when list_group_message_seen_members raises a RuntimeError,
  the exact exception instance propagates unchanged.
- get_room_or_404 is awaited exactly once with the identical opaque DB and chat_id.
- list_group_message_seen_members is awaited exactly once with the identical opaque DB,
  chat=returned GROUP room, message_id=supplied message_id, requester_id=supplied current_user.id.

Mocked boundary inputs do not prove real lookup reachability.
Exclusions:
- Excludes real-service authorization, sender/membership validation, and persistence/query contracts.
- Excludes HTTP/ASGI routing and authentication/Depends execution.
- Excludes non-GROUP and prior success/empty/schema outcomes.
- Excludes concurrency/retry/reload.
- Excludes canonical registration/promotion/count or receipt changes.
- Excludes seed/origin/blocker/Stage closure, external actions, secrets, runtime/scripts/controls,
  and rejected artifacts.
"""

from __future__ import annotations
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.routers.chat import get_group_message_seen_members
from core.enums import ChatType


class ChatSeenMembersServiceFailureCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization tests for service exceptions in get_group_message_seen_members for GROUP rooms."""

    @patch("api.routers.chat.list_group_message_seen_members", new_callable=AsyncMock)
    @patch("api.routers.chat.get_room_or_404", new_callable=AsyncMock)
    async def test_group_seen_service_exception_propagates_unchanged(
        self,
        mock_get_room_or_404: AsyncMock,
        mock_list_group_message_seen_members: AsyncMock,
    ) -> None:
        """Assert service RuntimeError propagates unchanged and mocks are awaited with exact boundary inputs."""
        opaque_db = object()
        chat_id = 9101
        message_id = 9202
        current_user_id = 9303
        current_user = SimpleNamespace(id=current_user_id)
        group_room = SimpleNamespace(id=chat_id, type=ChatType.GROUP)
        synthetic_error = RuntimeError("synthetic service failure")

        mock_get_room_or_404.return_value = group_room
        mock_list_group_message_seen_members.side_effect = synthetic_error

        with self.assertRaises(RuntimeError) as ctx:
            await get_group_message_seen_members(
                chat_id=chat_id,
                message_id=message_id,
                current_user=current_user,
                db=opaque_db,
            )

        self.assertIs(ctx.exception, synthetic_error)
        mock_get_room_or_404.assert_awaited_once_with(opaque_db, chat_id)
        mock_list_group_message_seen_members.assert_awaited_once_with(
            opaque_db,
            chat=group_room,
            message_id=message_id,
            requester_id=current_user.id,
        )
        self.assertEqual(mock_get_room_or_404.await_count, 1)
        self.assertEqual(mock_list_group_message_seen_members.await_count, 1)


if __name__ == "__main__":
    unittest.main()
