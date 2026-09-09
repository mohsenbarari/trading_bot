"""Direct-handler characterization tests for api.routers.chat.demote_group_admin under P1-00b.

Characterizes update_group_admin_status unexpected mutation failure boundary under WA-P1-00B-FREEZE-20260904-001.
This is direct-handler characterization only and does not update the executed-seed registry,
close canonical blockers, claim durable/concurrent/process-reload proof, promote evidence,
complete the Stage, or represent actual test execution. Mocked service failure is not
service-transaction/persistence, concurrent/reload, executed-seed, or Stage-completion evidence.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import demote_group_admin  # noqa: E402


class CharacterizeChatDemoteGroupAdminUpdateFailureTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing demote_group_admin update_group_admin_status failure boundary."""

    @patch("api.routers.chat.GroupMemberMutationResponse")
    @patch("api.routers.chat.update_group_admin_status", new_callable=AsyncMock)
    @patch("api.routers.chat.get_active_group_admin_or_403", new_callable=AsyncMock)
    @patch("api.routers.chat.get_group_or_404", new_callable=AsyncMock)
    async def test_demote_group_admin_propagates_mutation_runtime_error(
        self,
        mock_get_group_or_404: AsyncMock,
        mock_get_active_group_admin_or_403: AsyncMock,
        mock_update_group_admin_status: AsyncMock,
        mock_group_member_mutation_response: MagicMock,
    ) -> None:
        db_sentinel = object()
        group_sentinel = object()
        admin_member_sentinel = object()
        chat_id = 901
        actor_user_id = 101
        target_user_id = 202
        current_user = SimpleNamespace(id=actor_user_id)

        sentinel_error = RuntimeError("unexpected mutation failure in update_group_admin_status")

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

        with self.assertRaises(RuntimeError) as context:
            await demote_group_admin(
                chat_id=chat_id,
                user_id=target_user_id,
                current_user=current_user,
                db=db_sentinel,
            )

        self.assertIs(context.exception, sentinel_error)
        self.assertEqual(str(context.exception), "unexpected mutation failure in update_group_admin_status")

        mock_get_group_or_404.assert_awaited_once_with(db_sentinel, chat_id)
        mock_get_active_group_admin_or_403.assert_awaited_once_with(
            db_sentinel,
            chat=group_sentinel,
            user_id=actor_user_id,
        )
        mock_update_group_admin_status.assert_awaited_once_with(
            db_sentinel,
            chat=group_sentinel,
            user_id=target_user_id,
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

        mock_group_member_mutation_response.assert_not_called()
        self.assertEqual(mock_group_member_mutation_response.call_count, 0)

        self.assertNotEqual(actor_user_id, target_user_id)
        self.assertNotEqual(chat_id, actor_user_id)
        self.assertNotEqual(chat_id, target_user_id)
        self.assertEqual(current_user.id, actor_user_id)


if __name__ == "__main__":
    unittest.main()
