"""Direct characterization test for api.routers.users.update_user role-change authority.

This file covers only the update_user middle-manager role-change rejection
and does not complete P1-00b, the 465-seed blocker, Offer/Request-origin
evidence, or production readiness.
"""

from types import SimpleNamespace
import unittest

from fastapi import HTTPException

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.users import update_user
from core.enums import UserRole
import models.user
from models.user import User
import schemas
from schemas import UserUpdate


class MinimalAsyncDB:
    def __init__(self, target):
        self.target = target
        self.get_calls = []

    async def get(self, model, key):
        self.get_calls.append((model, key))
        return self.target


class UsersUpdateRoleAuthorityCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_middle_manager_cannot_change_user_role(self):
        requested_user_id = 42
        target = SimpleNamespace(
            id=requested_user_id,
            role=UserRole.STANDARD,
            is_deleted=False,
        )
        actor = SimpleNamespace(
            id=10,
            role=UserRole.MIDDLE_MANAGER,
        )
        db = MinimalAsyncDB(target)
        user_update = UserUpdate(role=UserRole.POLICE)

        with self.assertRaises(HTTPException) as exc_info:
            await update_user(
                requested_user_id,
                user_update,
                db=db,
                actor=actor,
                _admin_authority=None,
            )

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(
            exc_info.exception.detail,
            "فقط مدیر ارشد می‌تواند نقش کاربر را تغییر دهد",
        )
        self.assertEqual(db.get_calls, [(models.user.User, requested_user_id)])
        self.assertEqual(target.role, UserRole.STANDARD)


if __name__ == "__main__":
    unittest.main()
