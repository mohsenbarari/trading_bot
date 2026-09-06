"""Direct characterization test for api.routers.users.update_user distinct super-admin peer rejection.

This file covers only the distinct-super-admin peer rejection and does not complete
P1-00b, the 465-seed blocker, Offer/Request-origin evidence, or production readiness.
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
from schemas import UserUpdate


class MinimalAsyncDB:
    def __init__(self, target):
        self.target = target
        self.get_calls = []

    async def get(self, model, key):
        self.get_calls.append((model, key))
        return self.target


class UsersUpdateSuperAdminPeerCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_super_admin_cannot_mutate_super_admin_peer(self):
        actor_id = 7
        requested_user_id = 42
        target = SimpleNamespace(
            id=requested_user_id,
            role=UserRole.SUPER_ADMIN,
            max_sessions=1,
        )
        actor = SimpleNamespace(
            id=actor_id,
            role=UserRole.SUPER_ADMIN,
        )
        db = MinimalAsyncDB(target)
        user_update = UserUpdate(max_sessions=2)

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
            "مدیر ارشد نمی‌تواند عملیات حساس مدیریتی را روی حساب مدیر ارشد انجام دهد",
        )
        self.assertEqual(db.get_calls, [(models.user.User, requested_user_id)])
        self.assertEqual(target.max_sessions, 1)


if __name__ == "__main__":
    unittest.main()
