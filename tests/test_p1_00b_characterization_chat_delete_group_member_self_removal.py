"""Direct-characterization tests for api.routers.chat.delete_group_member under P1-00b.

Characterizes remove_group_member self-removal early failure boundary under WA-P1-00B-FREEZE-20260904-001.
This test does not prove persistence, concurrency, or process reload, does not update
seed-registry evidence, reduce canonical blockers, or mark Stage P1-00b complete.
Run only with APP_ENV_FILE=config/unit-test.env.example and PYTHONDONTWRITEBYTECODE=1.
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

from api.routers.chat import delete_group_member  # noqa: E402


class CharacterizeChatDeleteGroupMemberSelfRemovalTests(
    unittest.IsolatedAsyncioTestCase
):
    """Hermetic unit test characterizing delete_group_member self-removal failure boundary."""

    @patch("api.routers.chat.get_active_group_admin_or_403", new_callable=AsyncMock)
    @patch("api.routers.chat.get_group_or_404", new_callable=AsyncMock)
    async def test_delete_group_member_raises_400_on_self_removal(
        self,
        mock_get_group_or_404: AsyncMock,
        mock_get_active_group_admin_or_403: AsyncMock,
    ) -> None:
        group_sentinel = object()
        admin_sentinel = object()
        call_order: list[str] = []

        def record_get_group(*args: object, **kwargs: object) -> object:
            call_order.append("get_group_or_404")
            return group_sentinel

        def record_get_admin(*args: object, **kwargs: object) -> object:
            call_order.append("get_active_group_admin_or_403")
            return admin_sentinel

        mock_get_group_or_404.side_effect = record_get_group
        mock_get_group_or_404.return_value = group_sentinel
        mock_get_active_group_admin_or_403.side_effect = record_get_admin
        mock_get_active_group_admin_or_403.return_value = admin_sentinel

        current_user = SimpleNamespace(id=42)
        chat_id = 321
        user_id = 42
        db = SimpleNamespace(
            execute=AsyncMock(),
            commit=AsyncMock(),
        )

        with self.assertRaises(HTTPException) as context:
            await delete_group_member(
                chat_id=chat_id,
                user_id=user_id,
                current_user=current_user,
                db=db,
            )

        self.assertEqual(context.exception.status_code, 400)
        self.assertEqual(
            context.exception.detail,
            "Use the leave endpoint to leave a group",
        )
        mock_get_group_or_404.assert_awaited_once_with(db, 321)
        mock_get_active_group_admin_or_403.assert_awaited_once_with(
            db,
            chat=group_sentinel,
            user_id=42,
        )
        self.assertEqual(
            call_order,
            ["get_group_or_404", "get_active_group_admin_or_403"],
        )
        db.execute.assert_not_called()
        db.execute.assert_not_awaited()
        db.commit.assert_not_called()
        db.commit.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
