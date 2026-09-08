"""Direct-characterization tests for api.routers.chat.demote_group_admin under P1-00b.

Characterizes update_group_admin_status last-admin HTTP 400 propagation under WA-P1-00B-FREEZE-20260904-001.
This is direct characterization of router error propagation with mocked service only and does not
execute service guards, prove persistence, update the executed-seed registry, reduce blockers,
claim durable/concurrent/reload evidence, promote evidence, or complete Stage P1-00b.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from fastapi import HTTPException  # noqa: E402

from api.routers.chat import demote_group_admin  # noqa: E402


class CharacterizeChatDemoteGroupAdminLastAdminTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing demote_group_admin last-admin 400 failure boundary."""

    @patch("api.routers.chat.update_group_admin_status", new_callable=AsyncMock)
    @patch("api.routers.chat.get_active_group_admin_or_403", new_callable=AsyncMock)
    @patch("api.routers.chat.get_group_or_404", new_callable=AsyncMock)
    async def test_demote_group_admin_raises_400_when_last_admin(
        self,
        mock_get_group_or_404: AsyncMock,
        mock_get_active_group_admin_or_403: AsyncMock,
        mock_update_group_admin_status: AsyncMock,
    ) -> None:
        db_sentinel = object()
        group_sentinel = object()
        admin_member_sentinel = object()
        current_user = SimpleNamespace(id=42)
        sentinel_error = HTTPException(
            status_code=400,
            detail="Group must keep at least one active admin",
        )

        call_order: list[str] = []

        async def fake_get_group_or_404(*args: Any, **kwargs: Any) -> Any:
            call_order.append("get_group_or_404")
            return group_sentinel

        async def fake_get_active_group_admin_or_403(*args: Any, **kwargs: Any) -> Any:
            call_order.append("get_active_group_admin_or_403")
            return admin_member_sentinel

        async def fake_update_group_admin_status(*args: Any, **kwargs: Any) -> Any:
            call_order.append("update_group_admin_status")
            raise sentinel_error

        mock_get_group_or_404.side_effect = fake_get_group_or_404
        mock_get_active_group_admin_or_403.side_effect = fake_get_active_group_admin_or_403
        mock_update_group_admin_status.side_effect = fake_update_group_admin_status

        with self.assertRaises(HTTPException) as context:
            await demote_group_admin(
                chat_id=321,
                user_id=77,
                current_user=current_user,
                db=db_sentinel,
            )

        self.assertIs(context.exception, sentinel_error)
        self.assertEqual(context.exception.status_code, 400)
        self.assertEqual(
            context.exception.detail,
            "Group must keep at least one active admin",
        )

        mock_get_group_or_404.assert_awaited_once_with(db_sentinel, 321)
        mock_get_active_group_admin_or_403.assert_awaited_once_with(
            db_sentinel,
            chat=group_sentinel,
            user_id=42,
        )
        mock_update_group_admin_status.assert_awaited_once_with(
            db_sentinel,
            chat=group_sentinel,
            user_id=77,
            make_admin=False,
        )

        self.assertEqual(
            call_order,
            [
                "get_group_or_404",
                "get_active_group_admin_or_403",
                "update_group_admin_status",
            ],
        )
        self.assertEqual(len(call_order), 3)
        self.assertEqual(mock_get_group_or_404.await_count, 1)
        self.assertEqual(mock_get_active_group_admin_or_403.await_count, 1)
        self.assertEqual(mock_update_group_admin_status.await_count, 1)

        self.assertNotEqual(current_user.id, 77)
        self.assertEqual(current_user.id, 42)


if __name__ == "__main__":
    unittest.main()
