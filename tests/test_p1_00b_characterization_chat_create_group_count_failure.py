"""Direct-characterization tests for api.routers.chat.create_group member count failure.

This is direct characterization only under P1-00b and WA-P1-00B-FREEZE-20260904-001,
and does not update the executed-seed registry, reduce a canonical blocker, promote evidence,
or mark P1-00b complete.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import create_group  # noqa: E402
from api.routers.chat_schemas import GroupCreateRequest  # noqa: E402


class CharacterizeChatCreateGroupCountFailureTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit test characterizing create_group member count failure boundary."""

    @patch("api.routers.chat.count_active_chat_members", new_callable=AsyncMock)
    @patch("api.routers.chat.create_group_chat", new_callable=AsyncMock)
    @patch("api.routers.chat.resolve_owned_avatar_file_id", new_callable=AsyncMock)
    @patch("api.routers.chat.is_user_customer", new_callable=AsyncMock)
    async def test_create_group_count_active_chat_members_failure(
        self,
        mock_is_user_customer: AsyncMock,
        mock_resolve_owned_avatar_file_id: AsyncMock,
        mock_create_group_chat: AsyncMock,
        mock_count_active_chat_members: AsyncMock,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42)
        group = SimpleNamespace(id=101)
        resolved_avatar_id = "resolved-avatar-uuid-boundary-42"
        sentinel_exc = RuntimeError("count_active_chat_members failure after group creation")

        call_order: list[str] = []

        async def _fake_customer(db: object, user_id: int) -> bool:
            call_order.append("is_user_customer")
            return False

        async def _fake_avatar(
            db: object,
            *,
            actor_id: int,
            avatar_file_id: str | None,
        ) -> str | None:
            call_order.append("resolve_owned_avatar_file_id")
            return resolved_avatar_id

        async def _fake_create_group(
            db: object,
            *,
            creator: object,
            title: str,
            member_ids: list[int],
            description: str | None = None,
            avatar_file_id: str | None = None,
        ) -> object:
            call_order.append("create_group_chat")
            return group

        async def _fake_count_members(db: object, chat_id: int) -> int:
            call_order.append("count_active_chat_members")
            raise sentinel_exc

        mock_is_user_customer.side_effect = _fake_customer
        mock_resolve_owned_avatar_file_id.side_effect = _fake_avatar
        mock_create_group_chat.side_effect = _fake_create_group
        mock_count_active_chat_members.side_effect = _fake_count_members

        request_data = GroupCreateRequest(
            title="   Boundary Engineering Team   ",
            description="   Boundary group description   ",
            avatar_file_id="   avatar-raw-uuid-boundary   ",
            member_ids=[10, 0, -5, 20, 10, 30, -1],
        )

        self.assertEqual(request_data.title, "Boundary Engineering Team")
        self.assertEqual(request_data.description, "Boundary group description")
        self.assertEqual(request_data.avatar_file_id, "avatar-raw-uuid-boundary")
        self.assertEqual(request_data.member_ids, [10, 20, 30])

        with self.assertRaises(RuntimeError) as ctx:
            await create_group(
                data=request_data,
                current_user=current_user,
                db=fake_db,
            )

        self.assertIs(ctx.exception, sentinel_exc)
        self.assertEqual(
            call_order,
            [
                "is_user_customer",
                "resolve_owned_avatar_file_id",
                "create_group_chat",
                "count_active_chat_members",
            ],
        )
        mock_is_user_customer.assert_awaited_once_with(fake_db, current_user.id)
        mock_resolve_owned_avatar_file_id.assert_awaited_once_with(
            fake_db,
            actor_id=current_user.id,
            avatar_file_id="avatar-raw-uuid-boundary",
        )
        mock_create_group_chat.assert_awaited_once_with(
            fake_db,
            creator=current_user,
            title="Boundary Engineering Team",
            member_ids=[10, 20, 30],
            description="Boundary group description",
            avatar_file_id=resolved_avatar_id,
        )
        self.assertIs(mock_create_group_chat.call_args.kwargs["creator"], current_user)
        mock_count_active_chat_members.assert_awaited_once_with(fake_db, 101)


if __name__ == "__main__":
    unittest.main()
