"""Hermetic HTTP-boundary characterization for api.routers.users.read_user missing target.

Stage: P1-00b
Assignment: WA-P1-00B-EVIDENCE-RECONCILE-20261001-091
Seed: API-IDENTITY-GET-USERS-READ-USER-L440
Canonical gap: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

Observed HTTP-boundary scope:
- FastAPI router mounting of api.routers.users.router at /api/users
- Dependency override wiring for core.db.get_db and api.deps.verify_admin_or_dev_key
- Path parameter binding for integer user_id 404
- Single db.get(models.user.User, 404) invocation returning None
- Serialized HTTP 404 response with JSON detail "User not found"

Exclusions:
- Excludes direct-call replay, authorization policy itself, success path, and relation/serialization branches.
- Excludes source or product changes, inventory/reconciliation/count changes, and origin matrix updates.
- Excludes seed/blocker/Stage closure, external/network/Redis calls, credentials, Git and deploy.
- Does not claim that the handler seed or Stage is fully evidenced by this one scenario.
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

from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.deps
import api.routers.users
import core.db
import models.user


class FailClosedAsyncSession:
    """Fail-closed custom async Session adapter for read_user missing lookup."""

    def __init__(self) -> None:
        self.get_calls: list[tuple[object, object]] = []

    async def get(
        self,
        entity: object,
        ident: object,
        *args: object,
        **kwargs: object,
    ) -> object:
        self.get_calls.append((entity, ident))
        if entity is not models.user.User or ident != 404 or len(self.get_calls) > 1:
            raise AssertionError(
                f"Unexpected db.get call (calls={self.get_calls!r}); expected single {(models.user.User, 404)!r}"
            )
        return None

    async def execute(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("db.execute unexpectedly called on FailClosedAsyncSession")

    async def commit(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("db.commit unexpectedly called on FailClosedAsyncSession")

    async def rollback(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("db.rollback unexpectedly called on FailClosedAsyncSession")

    async def refresh(self, *args: object, **kwargs: object) -> None:
        raise AssertionError("db.refresh unexpectedly called on FailClosedAsyncSession")

    async def scalar(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("db.scalar unexpectedly called on FailClosedAsyncSession")

    async def scalars(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("db.scalars unexpectedly called on FailClosedAsyncSession")


class UsersReadMissingHttpCharacterizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fence = install_p1_00_network_fence()

    def test_authorized_missing_target_returns_http_404_after_single_get(self) -> None:
        # Verify probe adapter rejects unexpected keys and forbidden operations
        probe_db = FailClosedAsyncSession()
        with self.assertRaises(AssertionError):
            asyncio.run(probe_db.get(models.user.User, 999))
        with self.assertRaises(AssertionError):
            asyncio.run(probe_db.get(object, 404))
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
        probe_multicall = FailClosedAsyncSession()
        self.assertIsNone(asyncio.run(probe_multicall.get(models.user.User, 404)))
        with self.assertRaises(AssertionError):
            asyncio.run(probe_multicall.get(models.user.User, 404))

        app = FastAPI()
        app.include_router(api.routers.users.router, prefix="/api/users")

        fake_db = FailClosedAsyncSession()
        actor = SimpleNamespace(
            id=1,
            role=models.user.UserRole.SUPER_ADMIN,
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

        denied_connects_before = len(self.fence["denied_connects"])
        live_redis_before = len(self.fence["live_redis_constructs"])

        try:
            with TestClient(app) as client:
                response = client.get("/api/users/404")
        finally:
            app.dependency_overrides.clear()

        self.assertEqual(len(self.fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(self.fence["live_redis_constructs"]), live_redis_before)

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "User not found"})
        self.assertEqual(fake_db.get_calls, [(models.user.User, 404)])
        self.assertEqual(db_override_entries, 1)
        self.assertEqual(auth_override_calls, 1)
        self.assertEqual(app.dependency_overrides, {})


if __name__ == "__main__":
    unittest.main()
