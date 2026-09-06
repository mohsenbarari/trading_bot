"""Direct characterization tests for api.routers.users_public.read_public_user.

This module directly characterizes only read_public_user and does not
establish the 24 origin-interaction cells, update canonical evidence counts,
or complete P1-00b.
"""

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.users_public import read_public_user
import models.user
from models.user import UserRole


class MinimalAsyncDB:
    def __init__(self, target_user=None, *, fail_on_get: bool = False):
        self.target_user = target_user
        self.fail_on_get = fail_on_get
        self.get_calls = []

    async def get(self, model, user_id):
        self.get_calls.append((model, user_id))
        if self.fail_on_get:
            raise AssertionError(f"db.get unexpectedly awaited with {(model, user_id)}")
        return self.target_user


class UsersPublicReadCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_read_public_user_self_fast_path(self):
        current_user = SimpleNamespace(
            id=42,
            account_name="self_user",
            mobile_number="09121112233",
            avatar_file_id="avatar_self_42",
            address="Tehran, District 1",
            role=UserRole.STANDARD,
        )
        db = MinimalAsyncDB(fail_on_get=True)

        accountant_lookup = AsyncMock(side_effect=AssertionError("accountant relation unexpectedly looked up"))
        customer_lookup = AsyncMock(side_effect=AssertionError("customer relation unexpectedly looked up"))
        chat_targets_mock = AsyncMock(side_effect=AssertionError("chat targets unexpectedly built"))

        with patch(
            "api.routers.users_public.get_active_accountant_relation_for_accountant",
            new=accountant_lookup,
        ), patch(
            "api.routers.users_public.get_active_customer_relation_for_customer",
            new=customer_lookup,
        ), patch(
            "api.routers.users_public.build_allowed_customer_chat_targets",
            new=chat_targets_mock,
        ):
            result = await read_public_user(42, db=db, current_user=current_user)

        self.assertEqual(result.id, current_user.id)
        self.assertEqual(result.account_name, current_user.account_name)
        self.assertEqual(result.mobile_number, current_user.mobile_number)
        self.assertEqual(result.avatar_file_id, current_user.avatar_file_id)
        self.assertEqual(result.address, current_user.address)
        self.assertEqual(
            result.model_dump(),
            {
                "id": 42,
                "account_name": "self_user",
                "mobile_number": "09121112233",
                "avatar_file_id": "avatar_self_42",
                "address": "Tehran, District 1",
            },
        )
        self.assertEqual(db.get_calls, [])
        accountant_lookup.assert_not_awaited()
        customer_lookup.assert_not_awaited()
        chat_targets_mock.assert_not_awaited()

    async def test_read_public_user_missing_ordinary_target_fail_closed(self):
        current_user = SimpleNamespace(
            id=42,
            account_name="viewer_user",
            mobile_number="09121112233",
            role=UserRole.STANDARD,
        )
        target_user_id = 99
        db = MinimalAsyncDB(target_user=None)

        accountant_lookup = AsyncMock(return_value=None)
        customer_lookup = AsyncMock(return_value=None)
        chat_targets_mock = AsyncMock()

        with patch(
            "api.routers.users_public.get_active_accountant_relation_for_accountant",
            new=accountant_lookup,
        ), patch(
            "api.routers.users_public.get_active_customer_relation_for_customer",
            new=customer_lookup,
        ), patch(
            "api.routers.users_public.build_allowed_customer_chat_targets",
            new=chat_targets_mock,
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await read_public_user(
                    target_user_id,
                    db=db,
                    current_user=current_user,
                )

        self.assertEqual(exc_info.exception.status_code, 404)
        self.assertEqual(exc_info.exception.detail, "User not found")
        accountant_lookup.assert_awaited_once_with(db, target_user_id)
        customer_lookup.assert_awaited_once_with(db, target_user_id)
        self.assertEqual(db.get_calls, [(models.user.User, target_user_id)])
        chat_targets_mock.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
