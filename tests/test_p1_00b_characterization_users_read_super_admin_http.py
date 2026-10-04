"""Hermetic HTTP-boundary characterization for api.routers.users.read_user middle-manager super-admin rejection.

Stage: P1-00b
Assignment: WA-P1-00B-CANONICAL-CLOSEOUT-20261004-265
Parent: WA-P1-00B-FREEZE-20260904-001
Seed: API-IDENTITY-GET-USERS-READ-USER-L440 (zero distinct new seeds; seed already canonical)
Canonical gap: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

Observed HTTP-boundary scope:
- FastAPI router mounting of api.routers.users.router at /api/users
- Dependency override wiring for core.db.get_db and api.deps.verify_admin_or_dev_key
- Authenticated middle-manager actor id 7 requesting target super-admin id 8
- Path parameter binding for integer user_id 8
- Single db.get(models.user.User, 8) invocation returning target user with UserRole.SUPER_ADMIN
- Middle-manager managing super-admin rejection in _ensure_actor_can_manage_target raising HTTPException(403)
- Serialized HTTP 403 response with exact source-backed Persian JSON detail "مدیر میانی فقط می\u200cتواند کاربران غیرادمین را مدیریت کند"
- Verification that downstream relation loading and serialization are not invoked

Single-unit custom-Session reason:
- Custom Session adapter testing cannot batch because it enforces a single exact get(User, 8) query and fails closed on any other operation.

Exclusions:
- Excludes missing-target path, peer outcomes already accepted, successful read, self-read success (still unmet), token/dev-key authentication, relation/serialization success, permission-policy acceptance, persistence/retry/reload/subprocess/custom tables, product changes, inventory/manifest/ledger changes, origin matrix, seed/blocker/Stage closure, receipts/counts, external/network/Redis, credentials, Git and deploy.
- Both High blockers remain open (P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE, P1-00-HIGH-ORIGIN-MATRIX-UNPROVEN).
- Stage P1-00b remains IN_PROGRESS; P1-00 remains COMPLETE.
"""

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.deps
import api.routers.users
import core.db
import models.user
from tests.test_p1_00b_characterization_users_read_manager_peer_http import (
    FailClosedAsyncSession,
)


class UsersReadSuperAdminHttpCharacterizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fence = install_p1_00_network_fence()

    def test_middle_manager_super_admin_read_returns_http_403_before_relation_loading(self) -> None:
        target_user = SimpleNamespace(
            id=8,
            role=models.user.UserRole.SUPER_ADMIN,
        )

        # Verify probe adapter rejects unexpected keys and forbidden operations
        probe_db = FailClosedAsyncSession(target_user=target_user)
        with self.assertRaises(AssertionError):
            asyncio.run(probe_db.get(models.user.User, 999))
        with self.assertRaises(AssertionError):
            asyncio.run(probe_db.get(object, 8))
        with self.assertRaises(AssertionError):
            asyncio.run(probe_db.execute())
        with self.assertRaises(AssertionError):
            asyncio.run(probe_db.commit())
        with self.assertRaises(AssertionError):
            asyncio.run(probe_db.rollback())
        with self.assertRaises(AssertionError):
            asyncio.run(probe_db.refresh(object()))
        with self.assertRaises(AssertionError):
            asyncio.run(probe_db.scalar())
        with self.assertRaises(AssertionError):
            asyncio.run(probe_db.scalars())

        # Verify multi-call rejection
        probe_multicall = FailClosedAsyncSession(target_user=target_user)
        self.assertIs(asyncio.run(probe_multicall.get(models.user.User, 8)), target_user)
        with self.assertRaises(AssertionError):
            asyncio.run(probe_multicall.get(models.user.User, 8))

        app = FastAPI()
        app.include_router(api.routers.users.router, prefix="/api/users")

        fake_db = FailClosedAsyncSession(target_user=target_user)
        actor = SimpleNamespace(
            id=7,
            role=models.user.UserRole.MIDDLE_MANAGER,
        )

        db_override_entries = 0
        auth_override_calls = 0

        async def override_get_db():
            nonlocal db_override_entries
            db_override_entries += 1
            yield fake_db

        async def override_verify_admin_or_dev_key():
            nonlocal auth_override_calls
            auth_override_calls += 1
            return actor

        app.dependency_overrides[core.db.get_db] = override_get_db
        app.dependency_overrides[api.deps.verify_admin_or_dev_key] = (
            override_verify_admin_or_dev_key
        )

        attach_mock = AsyncMock(
            side_effect=AssertionError(
                "attach_customer_user_context should not be called on super-admin 403"
            )
        )
        serialize_mock = Mock(
            side_effect=AssertionError(
                "serialize_user_read should not be called on super-admin 403"
            )
        )

        denied_connects_before = len(self.fence["denied_connects"])
        live_redis_before = len(self.fence["live_redis_constructs"])

        with patch(
            "api.routers.users.attach_customer_user_context",
            new=attach_mock,
        ), patch(
            "api.routers.users.serialize_user_read",
            new=serialize_mock,
        ):
            try:
                with TestClient(app) as client:
                    response = client.get("/api/users/8")
            finally:
                app.dependency_overrides.clear()

        self.assertEqual(len(self.fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(self.fence["live_redis_constructs"]), live_redis_before)

        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json(),
            {"detail": "مدیر میانی فقط می\u200cتواند کاربران غیرادمین را مدیریت کند"},
        )
        self.assertEqual(fake_db.get_calls, [(models.user.User, 8)])
        self.assertEqual(db_override_entries, 1)
        self.assertEqual(auth_override_calls, 1)
        self.assertEqual(attach_mock.await_count, 0)
        self.assertEqual(serialize_mock.call_count, 0)
        attach_mock.assert_not_awaited()
        serialize_mock.assert_not_called()
        self.assertEqual(app.dependency_overrides, {})


if __name__ == "__main__":
    unittest.main()
