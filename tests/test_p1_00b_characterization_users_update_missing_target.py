"""Direct characterization tests for api.routers.users.update_user.

This test directly characterizes only the update_user missing-target branch and
does not complete P1-00b, close the 465-seed blocker, establish
Offer/Request-origin interaction evidence, or prove production readiness.
"""

import unittest

from fastapi import HTTPException

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.users import update_user
import models.user
import schemas


class MinimalAsyncDB:
    def __init__(self):
        self.get_calls = []

    async def get(self, model, key):
        self.get_calls.append((model, key))
        return None


class UsersUpdateMissingTargetCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_update_user_missing_target_raises_not_found(self):
        db = MinimalAsyncDB()

        with self.assertRaises(HTTPException) as exc_info:
            await update_user(
                404,
                schemas.UserUpdate(),
                db=db,
                actor=None,
                _admin_authority=None,
            )

        self.assertEqual(exc_info.exception.status_code, 404)
        self.assertEqual(exc_info.exception.detail, "User not found")
        self.assertEqual(db.get_calls, [(models.user.User, 404)])


if __name__ == "__main__":
    unittest.main()
