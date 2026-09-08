"""Direct-characterization tests for api.routers.chat.post_group_leave under P1-00b.

Characterizes get_group_or_404 404 failure boundary under WA-P1-00B-FREEZE-20260904-001.
This is direct characterization only and does not update the executed-seed registry,
reduce canonical blockers, promote evidence, or complete P1-00b.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from fastapi import HTTPException  # noqa: E402

from api.routers.chat import post_group_leave  # noqa: E402


class CharacterizeChatPostGroupLeave404Tests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing post_group_leave 404 failure boundary."""

    @patch("api.routers.chat.leave_group_chat", new_callable=AsyncMock)
    @patch("api.routers.chat.get_group_or_404", new_callable=AsyncMock)
    async def test_post_group_leave_raises_404_when_group_not_found(
        self,
        mock_get_group_or_404: AsyncMock,
        mock_leave_group_chat: AsyncMock,
    ) -> None:
        db_sentinel = object()
        sentinel_error = HTTPException(
            status_code=404,
            detail="Group not found",
        )
        mock_get_group_or_404.side_effect = sentinel_error

        with self.assertRaises(HTTPException) as context:
            await post_group_leave(
                chat_id=321,
                current_user=SimpleNamespace(id=42),
                db=db_sentinel,
            )

        self.assertIs(context.exception, sentinel_error)
        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(context.exception.detail, "Group not found")
        mock_get_group_or_404.assert_awaited_once_with(db_sentinel, 321)
        mock_leave_group_chat.assert_not_called()
        mock_leave_group_chat.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
