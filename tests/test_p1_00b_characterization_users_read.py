"""Direct characterization tests for api.routers.users.read_user.

This module directly characterizes only api.routers.users.read_user and does not
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

from api.routers.users import read_user
from core.enums import UserRole
import models.user


class MinimalAsyncDB:
    def __init__(self, target_user=None):
        self.target_user = target_user
        self.get_calls = []

    async def get(self, model, key):
        self.get_calls.append((model, key))
        return self.target_user


class UsersReadCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_user_success(self):
        target = SimpleNamespace(id=101, role=UserRole.STANDARD)
        sentinel_read = object()
        db = MinimalAsyncDB(target_user=target)

        attach_mock = AsyncMock(return_value=target)
        serialize_mock = Mock(return_value=sentinel_read)

        with patch(
            "api.routers.users.attach_customer_user_context",
            new=attach_mock,
        ), patch(
            "api.routers.users.serialize_user_read",
            new=serialize_mock,
        ):
            result = await read_user(101, db=db, actor=None)

        self.assertIs(result, sentinel_read)
        self.assertEqual(db.get_calls, [(models.user.User, 101)])
        attach_mock.assert_awaited_once_with(db, target)
        serialize_mock.assert_called_once_with(target)

    async def test_read_user_not_found(self):
        db = MinimalAsyncDB(target_user=None)
        attach_mock = AsyncMock()
        serialize_mock = Mock()

        with patch(
            "api.routers.users.attach_customer_user_context",
            new=attach_mock,
        ), patch(
            "api.routers.users.serialize_user_read",
            new=serialize_mock,
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await read_user(404, db=db, actor=None)

        self.assertEqual(exc_info.exception.status_code, 404)
        self.assertEqual(exc_info.exception.detail, "User not found")
        self.assertEqual(db.get_calls, [(models.user.User, 404)])
        attach_mock.assert_not_awaited()
        serialize_mock.assert_not_called()

    async def test_read_user_middle_manager_cannot_manage_super_admin(self):
        target = SimpleNamespace(id=200, role=UserRole.SUPER_ADMIN)
        actor = SimpleNamespace(id=100, role=UserRole.MIDDLE_MANAGER)
        db = MinimalAsyncDB(target_user=target)

        attach_mock = AsyncMock()
        serialize_mock = Mock()

        with patch(
            "api.routers.users.attach_customer_user_context",
            new=attach_mock,
        ), patch(
            "api.routers.users.serialize_user_read",
            new=serialize_mock,
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await read_user(200, db=db, actor=actor)

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(
            exc_info.exception.detail,
            "مدیر میانی فقط می‌تواند کاربران غیرادمین را مدیریت کند",
        )
        self.assertEqual(db.get_calls, [(models.user.User, 200)])
        attach_mock.assert_not_awaited()
        serialize_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
