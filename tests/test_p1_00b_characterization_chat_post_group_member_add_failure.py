"""P1-00b / WA-P1-00B-FREEZE-20260904-001 direct-handler failure characterization.

Characterizes post_group_member behavior when group lookup and active-admin
authorization succeed but add_group_member raises an unexpected mutation failure.
Scope is strictly limited to direct route handler exception propagation and call order
under mocked service dependencies; this test does not provide service transaction,
persistence, concurrency, reload, seed-registry, or Stage acceptance evidence.
"""

from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from api.routers.chat import post_group_member
from api.routers.chat_schemas import GroupMemberAddRequest


class TestPostGroupMemberAddFailure(unittest.IsolatedAsyncioTestCase):
    async def test_post_group_member_add_failure_propagates_sentinel_error(self) -> None:
        actor_id = 101
        target_user_id = 202
        chat_id = 303

        actor = SimpleNamespace(id=actor_id)
        request_data = GroupMemberAddRequest(user_id=target_user_id)

        db_sentinel = object()
        group_sentinel = object()
        admin_sentinel = object()
        sentinel_error = RuntimeError("add_group_member mutation failed")

        awaited_order: list[str] = []

        async def fake_get_group_or_404(*args: object, **kwargs: object) -> object:
            awaited_order.append("get_group_or_404")
            return group_sentinel

        async def fake_get_active_group_admin_or_403(*args: object, **kwargs: object) -> object:
            awaited_order.append("get_active_group_admin_or_403")
            return admin_sentinel

        async def fake_add_group_member(*args: object, **kwargs: object) -> object:
            awaited_order.append("add_group_member")
            raise sentinel_error

        with patch(
            "api.routers.chat.get_group_or_404",
            new_callable=AsyncMock,
            side_effect=fake_get_group_or_404,
        ) as mock_get_group, patch(
            "api.routers.chat.get_active_group_admin_or_403",
            new_callable=AsyncMock,
            side_effect=fake_get_active_group_admin_or_403,
        ) as mock_get_admin, patch(
            "api.routers.chat.add_group_member",
            new_callable=AsyncMock,
            side_effect=fake_add_group_member,
        ) as mock_add_member, patch(
            "api.routers.chat.GroupMemberMutationResponse",
        ) as mock_response:
            with self.assertRaises(RuntimeError) as context:
                await post_group_member(
                    chat_id=chat_id,
                    data=request_data,
                    current_user=actor,  # type: ignore[arg-type]
                    db=db_sentinel,  # type: ignore[arg-type]
                )

            self.assertIs(context.exception, sentinel_error)

            mock_get_group.assert_awaited_once_with(db_sentinel, chat_id)
            mock_get_admin.assert_awaited_once_with(
                db_sentinel,
                chat=group_sentinel,
                user_id=actor_id,
            )
            mock_add_member.assert_awaited_once_with(
                db_sentinel,
                chat=group_sentinel,
                user_id=target_user_id,
            )

            self.assertEqual(
                mock_add_member.await_args.kwargs["user_id"],
                target_user_id,
            )
            self.assertNotEqual(
                mock_add_member.await_args.kwargs["user_id"],
                actor_id,
            )

            self.assertEqual(
                awaited_order,
                [
                    "get_group_or_404",
                    "get_active_group_admin_or_403",
                    "add_group_member",
                ],
            )
            mock_response.assert_not_called()


if __name__ == "__main__":
    unittest.main()
