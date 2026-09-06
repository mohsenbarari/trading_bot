"""Direct characterization tests for api.routers.users.delete_user.

This module directly characterizes only api.routers.users.delete_user and does not
complete P1-00b or establish origin-interaction evidence.
"""

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.users import delete_user
from core.enums import UserAccountStatus, UserRole
import models.user


class MinimalAsyncDB:
    def __init__(self, target_user=None):
        self.target_user = target_user
        self.get_calls = []

    async def get(self, model, key):
        self.get_calls.append((model, key))
        return self.target_user


class UsersDeleteCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_delete_user_success(self):
        target = SimpleNamespace(
            id=101,
            role=UserRole.STANDARD,
            account_status=UserAccountStatus.ACTIVE,
            is_deleted=False,
            trading_restricted_until=None,
            max_sessions=1,
            can_block_users=True,
            max_blocked_users=10,
            max_accountants=3,
            max_customers=5,
        )
        db = MinimalAsyncDB(target_user=target)
        delete_account_mock = AsyncMock()
        audit_mock = Mock()

        with patch(
            "api.routers.users.delete_user_account",
            new=delete_account_mock,
        ), patch(
            "api.routers.users.audit_log",
            new=audit_mock,
        ):
            result = await delete_user(101, db=db, actor=None)

        self.assertEqual(result, {"message": "User deleted successfully"})
        self.assertEqual(db.get_calls, [(models.user.User, 101)])
        delete_account_mock.assert_awaited_once_with(db, target)
        audit_mock.assert_called_once_with(
            "user.delete",
            target_type="user",
            target_id=101,
            before_summary={
                "role": UserRole.STANDARD.value,
                "account_status": UserAccountStatus.ACTIVE.value,
                "is_deleted": False,
                "trading_restricted": False,
                "max_sessions": 1,
                "can_block_users": True,
                "max_blocked_users": 10,
                "max_accountants": 3,
                "max_customers": 5,
            },
            after_summary={"is_deleted": True},
            actor_id=None,
            actor_role=None,
        )

    async def test_delete_user_not_found(self):
        db = MinimalAsyncDB(target_user=None)
        delete_account_mock = AsyncMock()
        audit_mock = Mock()

        with patch(
            "api.routers.users.delete_user_account",
            new=delete_account_mock,
        ), patch(
            "api.routers.users.audit_log",
            new=audit_mock,
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await delete_user(404, db=db, actor=None)

        self.assertEqual(exc_info.exception.status_code, 404)
        self.assertEqual(exc_info.exception.detail, "User not found")
        self.assertEqual(db.get_calls, [(models.user.User, 404)])
        delete_account_mock.assert_not_awaited()
        audit_mock.assert_not_called()

    async def test_delete_user_already_soft_deleted(self):
        target = SimpleNamespace(id=102, is_deleted=True)
        db = MinimalAsyncDB(target_user=target)
        delete_account_mock = AsyncMock()
        audit_mock = Mock()

        with patch(
            "api.routers.users.delete_user_account",
            new=delete_account_mock,
        ), patch(
            "api.routers.users.audit_log",
            new=audit_mock,
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await delete_user(102, db=db, actor=None)

        self.assertEqual(exc_info.exception.status_code, 404)
        self.assertEqual(exc_info.exception.detail, "User not found")
        self.assertEqual(db.get_calls, [(models.user.User, 102)])
        delete_account_mock.assert_not_awaited()
        audit_mock.assert_not_called()

    async def test_delete_user_middle_manager_cannot_mutate_super_admin(self):
        actor = SimpleNamespace(id=100, role=UserRole.MIDDLE_MANAGER)
        target = SimpleNamespace(
            id=200,
            role=UserRole.SUPER_ADMIN,
            is_deleted=False,
        )
        db = MinimalAsyncDB(target_user=target)
        delete_account_mock = AsyncMock()
        audit_mock = Mock()

        with patch(
            "api.routers.users.delete_user_account",
            new=delete_account_mock,
        ), patch(
            "api.routers.users.audit_log",
            new=audit_mock,
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await delete_user(200, db=db, actor=actor)

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(
            exc_info.exception.detail,
            "مدیر میانی فقط می‌تواند کاربران غیرادمین را مدیریت کند",
        )
        self.assertEqual(db.get_calls, [(models.user.User, 200)])
        delete_account_mock.assert_not_awaited()
        audit_mock.assert_not_called()

    async def test_delete_user_account_failure_raises_500(self):
        target = SimpleNamespace(
            id=103,
            role=UserRole.STANDARD,
            account_status=UserAccountStatus.ACTIVE,
            is_deleted=False,
            trading_restricted_until=None,
            max_sessions=1,
            can_block_users=True,
            max_blocked_users=10,
            max_accountants=3,
            max_customers=5,
        )
        db = MinimalAsyncDB(target_user=target)
        delete_account_mock = AsyncMock(side_effect=RuntimeError("delete failed"))
        audit_mock = Mock()

        with patch(
            "api.routers.users.delete_user_account",
            new=delete_account_mock,
        ), patch(
            "api.routers.users.audit_log",
            new=audit_mock,
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await delete_user(103, db=db, actor=None)

        self.assertEqual(exc_info.exception.status_code, 500)
        self.assertEqual(
            exc_info.exception.detail,
            "خطا در حذف کاربر: delete failed",
        )
        self.assertEqual(db.get_calls, [(models.user.User, 103)])
        delete_account_mock.assert_awaited_once_with(db, target)
        audit_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
