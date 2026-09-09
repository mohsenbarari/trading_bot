"""Direct-characterization tests for api.routers.chat.get_group_member_candidates.

This is direct characterization only and does not update the executed-seed registry,
reduce a canonical blocker, promote evidence, or mark P1-00b complete.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import get_group_member_candidates  # noqa: E402
from api.routers.chat_schemas import (  # noqa: E402
    ChannelInviteCandidateListResponse,
    ChannelInviteCandidateRead,
)


class CharacterizeChatGetGroupMemberCandidatesTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing get_group_member_candidates."""

    @patch("api.routers.chat.load_chat_role_badges", new_callable=AsyncMock)
    @patch("api.routers.chat.list_group_member_candidates", new_callable=AsyncMock)
    @patch("api.routers.chat.get_active_group_admin_or_403", new_callable=AsyncMock)
    @patch("api.routers.chat.get_group_or_404", new_callable=AsyncMock)
    @patch("api.routers.chat.is_user_customer", new_callable=AsyncMock)
    async def test_get_group_member_candidates_customer_denied(
        self,
        mock_is_user_customer: AsyncMock,
        mock_get_group_or_404: AsyncMock,
        mock_get_active_group_admin_or_403: AsyncMock,
        mock_list_group_member_candidates: AsyncMock,
        mock_load_chat_role_badges: AsyncMock,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42)
        mock_is_user_customer.return_value = True

        with self.assertRaises(HTTPException) as ctx:
            await get_group_member_candidates(
                query_text="search_term",
                limit=20,
                offset=5,
                exclude_chat_id=99,
                selected_user_ids=[1, 2],
                current_user=current_user,
                db=fake_db,
            )

        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(
            ctx.exception.detail,
            "مشتری در این فاز اجازه ساخت یا مدیریت اعضای گروه را ندارد",
        )
        mock_is_user_customer.assert_awaited_once_with(fake_db, current_user.id)
        mock_get_group_or_404.assert_not_awaited()
        mock_get_active_group_admin_or_403.assert_not_awaited()
        mock_list_group_member_candidates.assert_not_awaited()
        mock_load_chat_role_badges.assert_not_awaited()

    @patch("api.routers.chat.load_chat_role_badges", new_callable=AsyncMock)
    @patch("api.routers.chat.list_group_member_candidates", new_callable=AsyncMock)
    @patch("api.routers.chat.get_active_group_admin_or_403", new_callable=AsyncMock)
    @patch("api.routers.chat.get_group_or_404", new_callable=AsyncMock)
    @patch("api.routers.chat.is_user_customer", new_callable=AsyncMock)
    async def test_get_group_member_candidates_success_with_exclude_chat_id(
        self,
        mock_is_user_customer: AsyncMock,
        mock_get_group_or_404: AsyncMock,
        mock_get_active_group_admin_or_403: AsyncMock,
        mock_list_group_member_candidates: AsyncMock,
        mock_load_chat_role_badges: AsyncMock,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42)
        mock_is_user_customer.return_value = False

        fake_group = SimpleNamespace(id=77, title="Sample Group")
        fake_admin = SimpleNamespace(user_id=42, role="admin")
        mock_get_group_or_404.return_value = fake_group
        mock_get_active_group_admin_or_403.return_value = fake_admin

        badge1 = SimpleNamespace(
            kind="accountant",
            label="حسابدار رسمی",
            accountant_owner_name="Alice Accountant",
            accountant_owner_label="دفتر تهران",
        )
        item1 = SimpleNamespace(
            user_id=101,
            account_name="alice_account",
            full_name="Alice Smith",
            mobile_number="+989121111111",
            avatar_file_id="avatar-alice-123",
            is_already_member=True,
            customer_management_name="VIP Client Alice",
        )
        item2 = SimpleNamespace(
            user_id=102,
            account_name="bob_account",
            full_name="Bob Jones",
            mobile_number="+989122222222",
            # avatar_file_id omitted to test missing optional mapping to None
            is_already_member=False,
            # customer_management_name omitted to test missing optional mapping to None
        )
        page = SimpleNamespace(
            items=[item1, item2],
            total=45,
            active_total=12,
        )
        mock_list_group_member_candidates.return_value = page
        mock_load_chat_role_badges.return_value = {101: badge1}

        result = await get_group_member_candidates(
            query_text="alice",
            limit=25,
            offset=10,
            exclude_chat_id=77,
            selected_user_ids=[101],
            current_user=current_user,
            db=fake_db,
        )

        mock_is_user_customer.assert_awaited_once_with(fake_db, current_user.id)
        mock_get_group_or_404.assert_awaited_once_with(fake_db, 77)
        mock_get_active_group_admin_or_403.assert_awaited_once_with(
            fake_db, chat=fake_group, user_id=current_user.id
        )
        mock_list_group_member_candidates.assert_awaited_once_with(
            fake_db,
            current_user=current_user,
            query_text="alice",
            limit=25,
            offset=10,
            exclude_chat_id=77,
            selected_user_ids=[101],
        )
        mock_load_chat_role_badges.assert_awaited_once_with(fake_db, {101, 102})

        self.assertIsInstance(result, ChannelInviteCandidateListResponse)
        self.assertEqual(result.total, 45)
        self.assertEqual(result.active_total, 12)
        self.assertEqual(len(result.items), 2)

        # item 1: present badge, present optional avatar and customer_management_name
        self.assertIsInstance(result.items[0], ChannelInviteCandidateRead)
        self.assertEqual(result.items[0].user_id, 101)
        self.assertEqual(result.items[0].account_name, "alice_account")
        self.assertEqual(result.items[0].full_name, "Alice Smith")
        self.assertEqual(result.items[0].mobile_number, "+989121111111")
        self.assertEqual(result.items[0].avatar_file_id, "avatar-alice-123")
        self.assertTrue(result.items[0].is_already_member)
        self.assertEqual(result.items[0].chat_role_kind, "accountant")
        self.assertEqual(result.items[0].chat_role_label, "حسابدار رسمی")
        self.assertEqual(result.items[0].chat_accountant_owner_name, "Alice Accountant")
        self.assertEqual(result.items[0].chat_accountant_owner_label, "دفتر تهران")
        self.assertEqual(result.items[0].customer_management_name, "VIP Client Alice")

        # item 2: absent badge, omitted avatar and customer_management_name mapping to None
        self.assertIsInstance(result.items[1], ChannelInviteCandidateRead)
        self.assertEqual(result.items[1].user_id, 102)
        self.assertEqual(result.items[1].account_name, "bob_account")
        self.assertEqual(result.items[1].full_name, "Bob Jones")
        self.assertEqual(result.items[1].mobile_number, "+989122222222")
        self.assertIsNone(result.items[1].avatar_file_id)
        self.assertFalse(result.items[1].is_already_member)
        self.assertIsNone(result.items[1].chat_role_kind)
        self.assertIsNone(result.items[1].chat_role_label)
        self.assertIsNone(result.items[1].chat_accountant_owner_name)
        self.assertIsNone(result.items[1].chat_accountant_owner_label)
        self.assertIsNone(result.items[1].customer_management_name)

    @patch("api.routers.chat.load_chat_role_badges", new_callable=AsyncMock)
    @patch("api.routers.chat.list_group_member_candidates", new_callable=AsyncMock)
    @patch("api.routers.chat.get_active_group_admin_or_403", new_callable=AsyncMock)
    @patch("api.routers.chat.get_group_or_404", new_callable=AsyncMock)
    @patch("api.routers.chat.is_user_customer", new_callable=AsyncMock)
    async def test_get_group_member_candidates_success_without_exclude_chat_id(
        self,
        mock_is_user_customer: AsyncMock,
        mock_get_group_or_404: AsyncMock,
        mock_get_active_group_admin_or_403: AsyncMock,
        mock_list_group_member_candidates: AsyncMock,
        mock_load_chat_role_badges: AsyncMock,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42)
        mock_is_user_customer.return_value = False

        page = SimpleNamespace(
            items=[],
            total=0,
            active_total=0,
        )
        mock_list_group_member_candidates.return_value = page
        mock_load_chat_role_badges.return_value = {}

        result = await get_group_member_candidates(
            query_text=None,
            limit=50,
            offset=0,
            exclude_chat_id=None,
            selected_user_ids=[],
            current_user=current_user,
            db=fake_db,
        )

        mock_is_user_customer.assert_awaited_once_with(fake_db, current_user.id)
        mock_get_group_or_404.assert_not_awaited()
        mock_get_active_group_admin_or_403.assert_not_awaited()
        mock_list_group_member_candidates.assert_awaited_once_with(
            fake_db,
            current_user=current_user,
            query_text=None,
            limit=50,
            offset=0,
            exclude_chat_id=None,
            selected_user_ids=[],
        )
        mock_load_chat_role_badges.assert_awaited_once_with(fake_db, set())

        self.assertIsInstance(result, ChannelInviteCandidateListResponse)
        self.assertEqual(result.items, [])
        self.assertEqual(result.total, 0)
        self.assertEqual(result.active_total, 0)

    @patch("api.routers.chat.load_chat_role_badges", new_callable=AsyncMock)
    @patch("api.routers.chat.list_group_member_candidates", new_callable=AsyncMock)
    @patch("api.routers.chat.get_active_group_admin_or_403", new_callable=AsyncMock)
    @patch("api.routers.chat.get_group_or_404", new_callable=AsyncMock)
    @patch("api.routers.chat.is_user_customer", new_callable=AsyncMock)
    async def test_get_group_member_candidates_dependency_failure(
        self,
        mock_is_user_customer: AsyncMock,
        mock_get_group_or_404: AsyncMock,
        mock_get_active_group_admin_or_403: AsyncMock,
        mock_list_group_member_candidates: AsyncMock,
        mock_load_chat_role_badges: AsyncMock,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42)
        mock_is_user_customer.return_value = False
        sentinel_error = RuntimeError("candidate listing failed")
        mock_list_group_member_candidates.side_effect = sentinel_error

        with self.assertRaises(RuntimeError) as ctx:
            await get_group_member_candidates(
                query_text="bob",
                limit=50,
                offset=0,
                exclude_chat_id=None,
                selected_user_ids=[],
                current_user=current_user,
                db=fake_db,
            )

        self.assertIs(ctx.exception, sentinel_error)
        mock_is_user_customer.assert_awaited_once_with(fake_db, current_user.id)
        mock_get_group_or_404.assert_not_awaited()
        mock_get_active_group_admin_or_403.assert_not_awaited()
        mock_list_group_member_candidates.assert_awaited_once_with(
            fake_db,
            current_user=current_user,
            query_text="bob",
            limit=50,
            offset=0,
            exclude_chat_id=None,
            selected_user_ids=[],
        )
        mock_load_chat_role_badges.assert_not_awaited()

    @patch("api.routers.chat.load_chat_role_badges", new_callable=AsyncMock)
    @patch("api.routers.chat.list_group_member_candidates", new_callable=AsyncMock)
    @patch("api.routers.chat.get_active_group_admin_or_403", new_callable=AsyncMock)
    @patch("api.routers.chat.get_group_or_404", new_callable=AsyncMock)
    @patch("api.routers.chat.is_user_customer", new_callable=AsyncMock)
    async def test_get_group_member_candidates_authorized_listing_failure(
        self,
        mock_is_user_customer: AsyncMock,
        mock_get_group_or_404: AsyncMock,
        mock_get_active_group_admin_or_403: AsyncMock,
        mock_list_group_member_candidates: AsyncMock,
        mock_load_chat_role_badges: AsyncMock,
    ) -> None:
        fake_db = object()
        current_user = SimpleNamespace(id=42)
        fake_group = SimpleNamespace(id=77, title="Sample Group")
        fake_admin = SimpleNamespace(user_id=42, role="admin")
        sentinel_error = RuntimeError("candidate listing failed after authorization")

        mock_is_user_customer.return_value = False
        mock_get_group_or_404.return_value = fake_group
        mock_get_active_group_admin_or_403.return_value = fake_admin

        execution_order: list[str] = []

        async def _record_is_user_customer(*_args: object, **_kwargs: object) -> bool:
            execution_order.append("is_user_customer")
            return False

        async def _record_get_group_or_404(*_args: object, **_kwargs: object) -> object:
            execution_order.append("get_group_or_404")
            return fake_group

        async def _record_get_active_group_admin_or_403(
            *_args: object, **_kwargs: object
        ) -> object:
            execution_order.append("get_active_group_admin_or_403")
            return fake_admin

        async def _record_list_group_member_candidates(
            *_args: object, **_kwargs: object
        ) -> object:
            execution_order.append("list_group_member_candidates")
            raise sentinel_error

        mock_is_user_customer.side_effect = _record_is_user_customer
        mock_get_group_or_404.side_effect = _record_get_group_or_404
        mock_get_active_group_admin_or_403.side_effect = (
            _record_get_active_group_admin_or_403
        )
        mock_list_group_member_candidates.side_effect = (
            _record_list_group_member_candidates
        )

        with self.assertRaises(RuntimeError) as ctx:
            await get_group_member_candidates(
                query_text="search_term",
                limit=25,
                offset=10,
                exclude_chat_id=77,
                selected_user_ids=[101, 102],
                current_user=current_user,
                db=fake_db,
            )

        self.assertIs(ctx.exception, sentinel_error)
        mock_is_user_customer.assert_awaited_once_with(fake_db, current_user.id)
        mock_get_group_or_404.assert_awaited_once_with(fake_db, 77)
        mock_get_active_group_admin_or_403.assert_awaited_once_with(
            fake_db, chat=fake_group, user_id=current_user.id
        )
        mock_list_group_member_candidates.assert_awaited_once_with(
            fake_db,
            current_user=current_user,
            query_text="search_term",
            limit=25,
            offset=10,
            exclude_chat_id=77,
            selected_user_ids=[101, 102],
        )
        mock_load_chat_role_badges.assert_not_called()
        mock_load_chat_role_badges.assert_not_awaited()

        self.assertEqual(
            execution_order,
            [
                "is_user_customer",
                "get_group_or_404",
                "get_active_group_admin_or_403",
                "list_group_member_candidates",
            ],
        )


if __name__ == "__main__":
    unittest.main()
