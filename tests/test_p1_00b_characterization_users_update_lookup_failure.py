"""Direct characterization test for api.routers.users.update_user database lookup failure boundary.

This test directly characterizes only the update_user database lookup failure
boundary when db.get raises an exception, and does not complete P1-00b, close
the 465-seed blocker, establish Offer/Request-origin interaction evidence, or
prove Stage acceptance or production readiness.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("APP_ENV_FILE", "config/unit-test.env.example")

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.users import update_user
import models.user
import schemas


class MinimalAsyncDB:
    def __init__(self, error: Exception):
        self._error = error
        self.get_calls = []
        self.commit = AsyncMock()
        self.refresh = AsyncMock()
        self.flush = AsyncMock()

    async def get(self, model, key):
        self.get_calls.append((model, key))
        raise self._error


class UsersUpdateLookupFailureCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_update_user_lookup_failure_propagates_unchanged(self):
        lookup_error = RuntimeError("Database lookup failed during user lookup")
        db = MinimalAsyncDB(lookup_error)
        user_update = schemas.UserUpdate(max_sessions=2)
        user_id = 42

        with patch(
            "api.routers.users._ensure_actor_can_mutate_target"
        ) as mock_ensure_actor, patch(
            "api.routers.users._audit_user_summary"
        ) as mock_audit_summary, patch.object(
            schemas.UserUpdate, "model_dump"
        ) as mock_model_dump:
            with self.assertRaises(RuntimeError) as exc_info:
                await update_user(
                    user_id,
                    user_update,
                    db=db,
                    actor=None,
                    _admin_authority=None,
                )

        self.assertIs(exc_info.exception, lookup_error)
        self.assertEqual(str(exc_info.exception), "Database lookup failed during user lookup")
        self.assertEqual(db.get_calls, [(models.user.User, user_id)])

        mock_ensure_actor.assert_not_called()
        mock_audit_summary.assert_not_called()
        mock_model_dump.assert_not_called()

        db.commit.assert_not_called()
        db.commit.assert_not_awaited()
        db.refresh.assert_not_called()
        db.refresh.assert_not_awaited()
        db.flush.assert_not_called()
        db.flush.assert_not_awaited()

        self.assertEqual(db.commit.call_count, 0)
        self.assertEqual(db.commit.await_count, 0)
        self.assertEqual(db.refresh.call_count, 0)
        self.assertEqual(db.refresh.await_count, 0)
        self.assertEqual(db.flush.call_count, 0)
        self.assertEqual(db.flush.await_count, 0)


if __name__ == "__main__":
    unittest.main()
