"""Direct-characterization tests for api.routers.chat.create_group.

This is direct characterization only under P1-00b and WA-P1-00B-FREEZE-20260904-001,
and does not update the executed-seed registry, reduce a canonical blocker, promote evidence,
or mark P1-00b complete.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException, status

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import (  # noqa: E402
    count_active_chat_members,
    create_group,
    create_group_chat,
    is_user_customer,
    resolve_owned_avatar_file_id,
)
from api.routers.chat_schemas import (  # noqa: E402
    GroupCreateRequest,
    GroupCreateResponse,
    GroupRoomRead,
)
from core.enums import ChatType  # noqa: E402


class CharacterizeChatCreateGroupTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing create_group."""

    @patch("api.routers.chat.count_active_chat_members", new_callable=AsyncMock)
    @patch("api.routers.chat.create_group_chat", new_callable=AsyncMock)
    @patch("api.routers.chat.resolve_owned_avatar_file_id", new_callable=AsyncMock)
    @patch("api.routers.chat.is_user_customer", new_callable=AsyncMock)
    async def test_create_group_customer_forbidden(
        self,
        mock_is_user_customer: AsyncMock,
        mock_resolve_owned_avatar_file_id: AsyncMock,
        mock_create_group_chat: AsyncMock,
        mock_count_active_chat_members: AsyncMock,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42)
        mock_is_user_customer.return_value = True

        request_data = GroupCreateRequest(
            title="   Denied Customer Group   ",
            description="   Customer creation attempt   ",
            avatar_file_id="   avatar-raw-uuid-403   ",
            member_ids=[1, 2, 3],
        )

        with self.assertRaises(HTTPException) as ctx:
            await create_group(
                data=request_data,
                current_user=current_user,
                db=fake_db,
            )

        self.assertEqual(ctx.exception.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(
            ctx.exception.detail,
            "مشتری در این فاز اجازه ساخت گروه جدید را ندارد",
        )
        mock_is_user_customer.assert_awaited_once_with(fake_db, current_user.id)
        mock_resolve_owned_avatar_file_id.assert_not_awaited()
        mock_create_group_chat.assert_not_awaited()
        mock_count_active_chat_members.assert_not_awaited()

    @patch("api.routers.chat.count_active_chat_members", new_callable=AsyncMock)
    @patch("api.routers.chat.create_group_chat", new_callable=AsyncMock)
    @patch("api.routers.chat.resolve_owned_avatar_file_id", new_callable=AsyncMock)
    @patch("api.routers.chat.is_user_customer", new_callable=AsyncMock)
    async def test_create_group_success_with_input_normalization_and_fallback_max_members(
        self,
        mock_is_user_customer: AsyncMock,
        mock_resolve_owned_avatar_file_id: AsyncMock,
        mock_create_group_chat: AsyncMock,
        mock_count_active_chat_members: AsyncMock,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42)
        created_dt = datetime(2026, 9, 7, 14, 30, 0, tzinfo=timezone.utc)

        group = SimpleNamespace(
            id=101,
            title="Core Engineering Team",
            description="Special project discussions",
            avatar_file_id="resolved-avatar-uuid-1",
            created_by_id=current_user.id,
            max_members=None,
            created_at=created_dt,
        )

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
            return "resolved-avatar-uuid-1"

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

        async def _fake_count_members(db: object, group_id: int) -> int:
            call_order.append("count_active_chat_members")
            return 4

        mock_is_user_customer.side_effect = _fake_customer
        mock_resolve_owned_avatar_file_id.side_effect = _fake_avatar
        mock_create_group_chat.side_effect = _fake_create_group
        mock_count_active_chat_members.side_effect = _fake_count_members

        request_data = GroupCreateRequest(
            title="   Core Engineering Team   ",
            description="   Special project discussions   ",
            avatar_file_id="   avatar-raw-uuid-1   ",
            member_ids=[10, 0, -5, 20, 10, 30, -1],
        )

        self.assertEqual(request_data.title, "Core Engineering Team")
        self.assertEqual(request_data.description, "Special project discussions")
        self.assertEqual(request_data.avatar_file_id, "avatar-raw-uuid-1")
        self.assertEqual(request_data.member_ids, [10, 20, 30])

        result = await create_group(
            data=request_data,
            current_user=current_user,
            db=fake_db,
        )

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
            avatar_file_id="avatar-raw-uuid-1",
        )
        mock_create_group_chat.assert_awaited_once_with(
            fake_db,
            creator=current_user,
            title="Core Engineering Team",
            member_ids=[10, 20, 30],
            description="Special project discussions",
            avatar_file_id="resolved-avatar-uuid-1",
        )
        mock_count_active_chat_members.assert_awaited_once_with(fake_db, 101)

        self.assertIsInstance(result, GroupCreateResponse)
        self.assertIsInstance(result.group, GroupRoomRead)
        self.assertEqual(result.group.id, 101)
        self.assertEqual(result.group.type, ChatType.GROUP)
        self.assertEqual(result.group.title, "Core Engineering Team")
        self.assertEqual(result.group.description, "Special project discussions")
        self.assertEqual(result.group.avatar_file_id, "resolved-avatar-uuid-1")
        self.assertEqual(result.group.created_by_id, current_user.id)
        self.assertEqual(result.group.member_count, 4)
        self.assertEqual(result.group.max_members, 50)
        self.assertEqual(result.group.created_at, created_dt)
        self.assertIs(result.group.created_at, created_dt)
        self.assertEqual(result.group.current_user_role, "admin")

    @patch("api.routers.chat.count_active_chat_members", new_callable=AsyncMock)
    @patch("api.routers.chat.create_group_chat", new_callable=AsyncMock)
    @patch("api.routers.chat.resolve_owned_avatar_file_id", new_callable=AsyncMock)
    @patch("api.routers.chat.is_user_customer", new_callable=AsyncMock)
    async def test_create_group_success_with_explicit_max_members(
        self,
        mock_is_user_customer: AsyncMock,
        mock_resolve_owned_avatar_file_id: AsyncMock,
        mock_create_group_chat: AsyncMock,
        mock_count_active_chat_members: AsyncMock,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42)
        created_dt = datetime(2026, 9, 7, 15, 0, 0, tzinfo=timezone.utc)

        group = SimpleNamespace(
            id=102,
            title="Design Team",
            description=None,
            created_by_id=current_user.id,
            max_members=120,
            created_at=created_dt,
        )

        mock_is_user_customer.return_value = False
        mock_resolve_owned_avatar_file_id.return_value = None
        mock_create_group_chat.return_value = group
        mock_count_active_chat_members.return_value = 1

        request_data = GroupCreateRequest(
            title="Design Team",
            description=None,
            avatar_file_id=None,
            member_ids=[99],
        )

        result = await create_group(
            data=request_data,
            current_user=current_user,
            db=fake_db,
        )

        mock_is_user_customer.assert_awaited_once_with(fake_db, current_user.id)
        mock_resolve_owned_avatar_file_id.assert_awaited_once_with(
            fake_db,
            actor_id=current_user.id,
            avatar_file_id=None,
        )
        mock_create_group_chat.assert_awaited_once_with(
            fake_db,
            creator=current_user,
            title="Design Team",
            member_ids=[99],
            description=None,
            avatar_file_id=None,
        )
        mock_count_active_chat_members.assert_awaited_once_with(fake_db, 102)

        self.assertIsInstance(result, GroupCreateResponse)
        self.assertIsInstance(result.group, GroupRoomRead)
        self.assertEqual(result.group.id, 102)
        self.assertEqual(result.group.type, ChatType.GROUP)
        self.assertEqual(result.group.title, "Design Team")
        self.assertIsNone(result.group.description)
        self.assertIsNone(result.group.avatar_file_id)
        self.assertEqual(result.group.created_by_id, current_user.id)
        self.assertEqual(result.group.member_count, 1)
        self.assertEqual(result.group.max_members, 120)
        self.assertEqual(result.group.created_at, created_dt)
        self.assertIs(result.group.created_at, created_dt)
        self.assertEqual(result.group.current_user_role, "admin")

    @patch("api.routers.chat.count_active_chat_members", new_callable=AsyncMock)
    @patch("api.routers.chat.create_group_chat", new_callable=AsyncMock)
    @patch("api.routers.chat.resolve_owned_avatar_file_id", new_callable=AsyncMock)
    @patch("api.routers.chat.is_user_customer", new_callable=AsyncMock)
    async def test_create_group_avatar_resolution_failure(
        self,
        mock_is_user_customer: AsyncMock,
        mock_resolve_owned_avatar_file_id: AsyncMock,
        mock_create_group_chat: AsyncMock,
        mock_count_active_chat_members: AsyncMock,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42)
        mock_is_user_customer.return_value = False
        sentinel_exc = HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="فقط میتوانید از تصویر آپلودشده توسط خودتان استفاده کنید",
        )
        mock_resolve_owned_avatar_file_id.side_effect = sentinel_exc

        request_data = GroupCreateRequest(
            title="Engineering Group",
            description="Team discussion",
            avatar_file_id="unowned-avatar-file-id",
            member_ids=[10, 20],
        )

        with self.assertRaises(HTTPException) as ctx:
            await create_group(
                data=request_data,
                current_user=current_user,
                db=fake_db,
            )

        self.assertIs(ctx.exception, sentinel_exc)
        mock_is_user_customer.assert_awaited_once_with(fake_db, current_user.id)
        mock_resolve_owned_avatar_file_id.assert_awaited_once_with(
            fake_db,
            actor_id=current_user.id,
            avatar_file_id="unowned-avatar-file-id",
        )
        mock_create_group_chat.assert_not_awaited()
        mock_count_active_chat_members.assert_not_awaited()

    @patch("api.routers.chat.count_active_chat_members", new_callable=AsyncMock)
    @patch("api.routers.chat.create_group_chat", new_callable=AsyncMock)
    @patch("api.routers.chat.resolve_owned_avatar_file_id", new_callable=AsyncMock)
    @patch("api.routers.chat.is_user_customer", new_callable=AsyncMock)
    async def test_create_group_create_group_chat_failure(
        self,
        mock_is_user_customer: AsyncMock,
        mock_resolve_owned_avatar_file_id: AsyncMock,
        mock_create_group_chat: AsyncMock,
        mock_count_active_chat_members: AsyncMock,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42)
        resolved_avatar_id = "resolved-avatar-file-uuid-1"
        sentinel_exc = RuntimeError("create_group_chat internal failure")

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
            raise sentinel_exc

        mock_is_user_customer.side_effect = _fake_customer
        mock_resolve_owned_avatar_file_id.side_effect = _fake_avatar
        mock_create_group_chat.side_effect = _fake_create_group

        request_data = GroupCreateRequest(
            title="   Engineering Group   ",
            description="   Team discussion   ",
            avatar_file_id="   avatar-file-uuid-1   ",
            member_ids=[10, 0, -5, 20, 10, 30, -1],
        )

        self.assertEqual(request_data.title, "Engineering Group")
        self.assertEqual(request_data.description, "Team discussion")
        self.assertEqual(request_data.avatar_file_id, "avatar-file-uuid-1")
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
            ],
        )
        mock_is_user_customer.assert_awaited_once_with(fake_db, current_user.id)
        mock_resolve_owned_avatar_file_id.assert_awaited_once_with(
            fake_db,
            actor_id=current_user.id,
            avatar_file_id="avatar-file-uuid-1",
        )
        mock_create_group_chat.assert_awaited_once_with(
            fake_db,
            creator=current_user,
            title="Engineering Group",
            member_ids=[10, 20, 30],
            description="Team discussion",
            avatar_file_id=resolved_avatar_id,
        )
        self.assertIs(mock_create_group_chat.call_args.args[0], fake_db)
        self.assertIs(mock_create_group_chat.call_args.kwargs["creator"], current_user)
        mock_count_active_chat_members.assert_not_called()
        mock_count_active_chat_members.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
