"""Hermetic characterization batch tests for accountant-entry denials in api.routers.blocks.

Covers:
- get_my_blocked_users
- block_a_user
- unblock_a_user
- search_users
"""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, call, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from fastapi import HTTPException  # noqa: E402

from api.routers.blocks import (  # noqa: E402
    ACCOUNTANT_BLOCK_MANAGEMENT_DETAIL,
    block_a_user,
    get_my_blocked_users,
    search_users,
    unblock_a_user,
)
from models.user import UserRole  # noqa: E402


class _StrictDatabaseSentinel:
    """Strict database sentinel exposing execute and get while rejecting all other attributes."""

    def __init__(self, execute: AsyncMock, get: AsyncMock) -> None:
        object.__setattr__(self, "execute", execute)
        object.__setattr__(self, "get", get)

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(
            f"Strict database sentinel rejects attribute access: {name!r}"
        )

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(
            f"Strict database sentinel rejects attribute mutation: {name!r}"
        )


class BlocksAccountantEntryDenialsCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Hermetic batch characterization for blocks accountant-entry denials."""

    def setUp(self) -> None:
        super().setUp()
        self.fence = install_p1_00_network_fence()
        self.current_user = SimpleNamespace(
            id=9102,
            role=UserRole.STANDARD,
        )
        self.db_execute = AsyncMock(
            side_effect=AssertionError("db.execute must not be awaited")
        )
        self.db_get = AsyncMock(
            side_effect=AssertionError("db.get must not be awaited")
        )
        self.strict_db = _StrictDatabaseSentinel(self.db_execute, self.db_get)

    @patch("api.routers.blocks.get_blocked_users", new_callable=AsyncMock)
    @patch("api.routers.blocks.audit_log")
    @patch("api.routers.blocks.is_user_accountant", new_callable=AsyncMock)
    @patch("api.routers.blocks.is_user_customer", new_callable=AsyncMock)
    async def test_blocked_users_denies_accountant_before_service_or_database_work(
        self,
        mock_is_user_customer: AsyncMock,
        mock_is_user_accountant: AsyncMock,
        mock_audit_log: Mock,
        mock_get_blocked_users: AsyncMock,
    ) -> None:
        mock_is_user_customer.return_value = False
        mock_is_user_accountant.return_value = True
        mock_get_blocked_users.side_effect = AssertionError(
            "get_blocked_users must not be awaited"
        )

        call_tracker = Mock()
        call_tracker.attach_mock(mock_is_user_customer, "is_user_customer")
        call_tracker.attach_mock(mock_is_user_accountant, "is_user_accountant")
        call_tracker.attach_mock(mock_audit_log, "audit_log")

        denied_connects_before = len(self.fence["denied_connects"])
        live_redis_before = len(self.fence["live_redis_constructs"])

        with self.assertRaises(HTTPException) as context:
            await get_my_blocked_users(
                db=self.strict_db,
                current_user=self.current_user,
            )

        self.assertEqual(len(self.fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(self.fence["live_redis_constructs"]), live_redis_before)

        self.assertEqual(context.exception.status_code, 403)
        self.assertEqual(context.exception.detail, ACCOUNTANT_BLOCK_MANAGEMENT_DETAIL)
        self.assertEqual(
            context.exception.detail, "قابلیت بلاک کاربران فقط در اختیار سرگروه است."
        )

        mock_is_user_customer.assert_awaited_once_with(
            self.strict_db, self.current_user.id
        )
        mock_is_user_accountant.assert_awaited_once_with(
            self.strict_db, self.current_user.id
        )
        self.assertEqual(
            call_tracker.mock_calls,
            [
                call.is_user_customer(self.strict_db, self.current_user.id),
                call.is_user_accountant(self.strict_db, self.current_user.id),
                call.audit_log(
                    "user.block_management",
                    target_type="block_management",
                    target_id=self.current_user.id,
                    result="denied",
                    actor_id=self.current_user.id,
                    actor_role=UserRole.STANDARD.value,
                    reason="accountant_cannot_manage_blocks",
                ),
            ],
        )

        mock_audit_log.assert_called_once_with(
            "user.block_management",
            target_type="block_management",
            target_id=self.current_user.id,
            result="denied",
            actor_id=self.current_user.id,
            actor_role=UserRole.STANDARD.value,
            reason="accountant_cannot_manage_blocks",
        )

        mock_get_blocked_users.assert_not_awaited()
        mock_get_blocked_users.assert_not_called()
        self.db_execute.assert_not_awaited()
        self.db_execute.assert_not_called()
        self.db_get.assert_not_awaited()
        self.db_get.assert_not_called()

    @patch("api.routers.blocks.block_user", new_callable=AsyncMock)
    @patch("api.routers.blocks.audit_log")
    @patch("api.routers.blocks.is_user_accountant", new_callable=AsyncMock)
    @patch("api.routers.blocks.is_user_customer", new_callable=AsyncMock)
    async def test_block_user_denies_accountant_before_target_lookup_or_mutation(
        self,
        mock_is_user_customer: AsyncMock,
        mock_is_user_accountant: AsyncMock,
        mock_audit_log: Mock,
        mock_block_user: AsyncMock,
    ) -> None:
        target_user_id = 4096
        mock_is_user_customer.return_value = False
        mock_is_user_accountant.return_value = True
        mock_block_user.side_effect = AssertionError(
            "block_user must not be awaited"
        )

        call_tracker = Mock()
        call_tracker.attach_mock(mock_is_user_customer, "is_user_customer")
        call_tracker.attach_mock(mock_is_user_accountant, "is_user_accountant")
        call_tracker.attach_mock(mock_audit_log, "audit_log")

        denied_connects_before = len(self.fence["denied_connects"])
        live_redis_before = len(self.fence["live_redis_constructs"])

        with self.assertRaises(HTTPException) as context:
            await block_a_user(
                user_id=target_user_id,
                db=self.strict_db,
                current_user=self.current_user,
            )

        self.assertEqual(len(self.fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(self.fence["live_redis_constructs"]), live_redis_before)

        self.assertEqual(context.exception.status_code, 403)
        self.assertEqual(context.exception.detail, ACCOUNTANT_BLOCK_MANAGEMENT_DETAIL)
        self.assertEqual(
            context.exception.detail, "قابلیت بلاک کاربران فقط در اختیار سرگروه است."
        )

        mock_is_user_customer.assert_awaited_once_with(
            self.strict_db, self.current_user.id
        )
        mock_is_user_accountant.assert_awaited_once_with(
            self.strict_db, self.current_user.id
        )
        self.assertEqual(
            call_tracker.mock_calls,
            [
                call.is_user_customer(self.strict_db, self.current_user.id),
                call.is_user_accountant(self.strict_db, self.current_user.id),
                call.audit_log(
                    "user.block_management",
                    target_type="block_management",
                    target_id=self.current_user.id,
                    result="denied",
                    actor_id=self.current_user.id,
                    actor_role=UserRole.STANDARD.value,
                    reason="accountant_cannot_manage_blocks",
                ),
            ],
        )

        mock_audit_log.assert_called_once_with(
            "user.block_management",
            target_type="block_management",
            target_id=self.current_user.id,
            result="denied",
            actor_id=self.current_user.id,
            actor_role=UserRole.STANDARD.value,
            reason="accountant_cannot_manage_blocks",
        )

        self.db_get.assert_not_awaited()
        self.db_get.assert_not_called()
        mock_block_user.assert_not_awaited()
        mock_block_user.assert_not_called()
        self.db_execute.assert_not_awaited()
        self.db_execute.assert_not_called()

    @patch("api.routers.blocks.unblock_user", new_callable=AsyncMock)
    @patch("api.routers.blocks.audit_log")
    @patch("api.routers.blocks.is_user_accountant", new_callable=AsyncMock)
    @patch("api.routers.blocks.is_user_customer", new_callable=AsyncMock)
    async def test_unblock_user_denies_accountant_before_mutation(
        self,
        mock_is_user_customer: AsyncMock,
        mock_is_user_accountant: AsyncMock,
        mock_audit_log: Mock,
        mock_unblock_user: AsyncMock,
    ) -> None:
        target_user_id = 4096
        mock_is_user_customer.return_value = False
        mock_is_user_accountant.return_value = True
        mock_unblock_user.side_effect = AssertionError(
            "unblock_user must not be awaited"
        )

        call_tracker = Mock()
        call_tracker.attach_mock(mock_is_user_customer, "is_user_customer")
        call_tracker.attach_mock(mock_is_user_accountant, "is_user_accountant")
        call_tracker.attach_mock(mock_audit_log, "audit_log")

        denied_connects_before = len(self.fence["denied_connects"])
        live_redis_before = len(self.fence["live_redis_constructs"])

        with self.assertRaises(HTTPException) as context:
            await unblock_a_user(
                user_id=target_user_id,
                db=self.strict_db,
                current_user=self.current_user,
            )

        self.assertEqual(len(self.fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(self.fence["live_redis_constructs"]), live_redis_before)

        self.assertEqual(context.exception.status_code, 403)
        self.assertEqual(context.exception.detail, ACCOUNTANT_BLOCK_MANAGEMENT_DETAIL)
        self.assertEqual(
            context.exception.detail, "قابلیت بلاک کاربران فقط در اختیار سرگروه است."
        )

        mock_is_user_customer.assert_awaited_once_with(
            self.strict_db, self.current_user.id
        )
        mock_is_user_accountant.assert_awaited_once_with(
            self.strict_db, self.current_user.id
        )
        self.assertEqual(
            call_tracker.mock_calls,
            [
                call.is_user_customer(self.strict_db, self.current_user.id),
                call.is_user_accountant(self.strict_db, self.current_user.id),
                call.audit_log(
                    "user.block_management",
                    target_type="block_management",
                    target_id=self.current_user.id,
                    result="denied",
                    actor_id=self.current_user.id,
                    actor_role=UserRole.STANDARD.value,
                    reason="accountant_cannot_manage_blocks",
                ),
            ],
        )

        mock_audit_log.assert_called_once_with(
            "user.block_management",
            target_type="block_management",
            target_id=self.current_user.id,
            result="denied",
            actor_id=self.current_user.id,
            actor_role=UserRole.STANDARD.value,
            reason="accountant_cannot_manage_blocks",
        )

        mock_unblock_user.assert_not_awaited()
        mock_unblock_user.assert_not_called()
        self.db_execute.assert_not_awaited()
        self.db_execute.assert_not_called()
        self.db_get.assert_not_awaited()
        self.db_get.assert_not_called()

    @patch("api.routers.blocks.search_users_for_block", new_callable=AsyncMock)
    @patch("api.routers.blocks.audit_log")
    @patch("api.routers.blocks.is_user_accountant", new_callable=AsyncMock)
    @patch("api.routers.blocks.is_user_customer", new_callable=AsyncMock)
    async def test_search_users_denies_accountant_before_search(
        self,
        mock_is_user_customer: AsyncMock,
        mock_is_user_accountant: AsyncMock,
        mock_audit_log: Mock,
        mock_search_users_for_block: AsyncMock,
    ) -> None:
        search_query = "distinctive_accountant_query"
        nondefault_limit = 15
        mock_is_user_customer.return_value = False
        mock_is_user_accountant.return_value = True
        mock_search_users_for_block.side_effect = AssertionError(
            "search_users_for_block must not be awaited"
        )

        call_tracker = Mock()
        call_tracker.attach_mock(mock_is_user_customer, "is_user_customer")
        call_tracker.attach_mock(mock_is_user_accountant, "is_user_accountant")
        call_tracker.attach_mock(mock_audit_log, "audit_log")

        denied_connects_before = len(self.fence["denied_connects"])
        live_redis_before = len(self.fence["live_redis_constructs"])

        with self.assertRaises(HTTPException) as context:
            await search_users(
                q=search_query,
                limit=nondefault_limit,
                db=self.strict_db,
                current_user=self.current_user,
            )

        self.assertEqual(len(self.fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(self.fence["live_redis_constructs"]), live_redis_before)

        self.assertEqual(context.exception.status_code, 403)
        self.assertEqual(context.exception.detail, ACCOUNTANT_BLOCK_MANAGEMENT_DETAIL)
        self.assertEqual(
            context.exception.detail, "قابلیت بلاک کاربران فقط در اختیار سرگروه است."
        )

        mock_is_user_customer.assert_awaited_once_with(
            self.strict_db, self.current_user.id
        )
        mock_is_user_accountant.assert_awaited_once_with(
            self.strict_db, self.current_user.id
        )
        self.assertEqual(
            call_tracker.mock_calls,
            [
                call.is_user_customer(self.strict_db, self.current_user.id),
                call.is_user_accountant(self.strict_db, self.current_user.id),
                call.audit_log(
                    "user.block_management",
                    target_type="block_management",
                    target_id=self.current_user.id,
                    result="denied",
                    actor_id=self.current_user.id,
                    actor_role=UserRole.STANDARD.value,
                    reason="accountant_cannot_manage_blocks",
                ),
            ],
        )

        mock_audit_log.assert_called_once_with(
            "user.block_management",
            target_type="block_management",
            target_id=self.current_user.id,
            result="denied",
            actor_id=self.current_user.id,
            actor_role=UserRole.STANDARD.value,
            reason="accountant_cannot_manage_blocks",
        )

        mock_search_users_for_block.assert_not_awaited()
        mock_search_users_for_block.assert_not_called()
        self.db_execute.assert_not_awaited()
        self.db_execute.assert_not_called()
        self.db_get.assert_not_awaited()
        self.db_get.assert_not_called()


if __name__ == "__main__":
    unittest.main()
