"""Direct-characterization tests for api.routers.chat.get_group_message_seen_members non-GROUP rejection.

This module characterizes the direct-handler rejection behavior for non-GROUP rooms:
- Validates exact HTTPException status code 404 and detail 'Message seen list is available for groups only'
- Confirms get_room_or_404 is awaited with the opaque db session and chat_id
- Asserts downstream list_group_message_seen_members is neither called nor awaited

Exclusions:
- Excludes HTTP/ASGI routing, auth/Depends execution or policy, membership/sender service validation
- Excludes real DB/session/persistence, concurrency/retry/reload, and service-failure outcomes
- Excludes canonical registration/promotion, ledger/count changes, Stage/seed/origin/blocker closure,
  scripts/runtime/control edits, and all external actions

This is direct characterization only and does not perform seed registry/promotion/closure changes,
leaving canonical registration, GROUP service-failure scenarios, and all remaining Stage gates open.
"""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.chat import get_group_message_seen_members
from core.enums import ChatType


class ChatSeenMembersNonGroupCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization tests for non-GROUP rejections in get_group_message_seen_members."""

    @patch("api.routers.chat.list_group_message_seen_members", new_callable=AsyncMock)
    @patch("api.routers.chat.get_room_or_404", new_callable=AsyncMock)
    async def test_channel_room_rejected_before_seen_service(
        self,
        mock_get_room_or_404: AsyncMock,
        mock_list_group_message_seen_members: AsyncMock,
    ) -> None:
        """Assert CHANNEL room raises 404 with exact detail and suppresses seen service invocation."""
        opaque_db = object()
        current_user = SimpleNamespace(id=301)
        chat_id = 101
        message_id = 201
        channel_room = SimpleNamespace(id=chat_id, type=ChatType.CHANNEL)

        mock_get_room_or_404.return_value = channel_room

        with self.assertRaises(HTTPException) as ctx:
            await get_group_message_seen_members(
                chat_id=chat_id,
                message_id=message_id,
                current_user=current_user,
                db=opaque_db,
            )

        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(
            ctx.exception.detail,
            "Message seen list is available for groups only",
        )
        mock_get_room_or_404.assert_awaited_once_with(opaque_db, chat_id)
        mock_list_group_message_seen_members.assert_not_called()
        mock_list_group_message_seen_members.assert_not_awaited()

    @patch("api.routers.chat.list_group_message_seen_members", new_callable=AsyncMock)
    @patch("api.routers.chat.get_room_or_404", new_callable=AsyncMock)
    async def test_direct_boundary_input_rejected_before_seen_service(
        self,
        mock_get_room_or_404: AsyncMock,
        mock_list_group_message_seen_members: AsyncMock,
    ) -> None:
        """Assert DIRECT room boundary input raises 404 with exact detail and suppresses seen service.

        Note: ChatType.DIRECT is a mocked room-boundary input only. In production,
        authoritative get_room_or_404 queries only CHANNEL/GROUP rooms and does not
        return DIRECT on its actual query path.
        """
        opaque_db = object()
        current_user = SimpleNamespace(id=302)
        chat_id = 102
        message_id = 202
        direct_room = SimpleNamespace(id=chat_id, type=ChatType.DIRECT)

        mock_get_room_or_404.return_value = direct_room

        with self.assertRaises(HTTPException) as ctx:
            await get_group_message_seen_members(
                chat_id=chat_id,
                message_id=message_id,
                current_user=current_user,
                db=opaque_db,
            )

        self.assertEqual(ctx.exception.status_code, 404)
        self.assertEqual(
            ctx.exception.detail,
            "Message seen list is available for groups only",
        )
        mock_get_room_or_404.assert_awaited_once_with(opaque_db, chat_id)
        mock_list_group_message_seen_members.assert_not_called()
        mock_list_group_message_seen_members.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
