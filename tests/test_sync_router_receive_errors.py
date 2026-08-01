import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from fastapi import HTTPException
from httpx import ASGITransport

from api.routers import sync as sync_router
from api.routers.sync import (
    RETIRED_LEGACY_DIRECT_SYNC_HTTP_DETAIL,
    receive_sync_data,
)


class FakeDB:
    def __init__(self, commit_results=None):
        self.execute_calls = []
        self.commit_results = list(commit_results or [])
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, stmt, *args, **kwargs):
        self.execute_calls.append((stmt, args, kwargs))
        return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: None))

    async def commit(self):
        self.commits += 1
        if self.commit_results:
            next_result = self.commit_results.pop(0)
            if isinstance(next_result, Exception):
                raise next_result

    async def rollback(self):
        self.rollbacks += 1

    def begin_nested(self):
        class _Ctx:
            async def __aenter__(self_inner):
                return None

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        return _Ctx()


class SyncRouterReceiveErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_fake_db_helper_paths(self):
        db = FakeDB()
        await db.rollback()
        self.assertEqual(db.rollbacks, 1)

        async with db.begin_nested() as nested:
            self.assertIsNone(nested)

    @unittest.skip("legacy direct FI<->IR HTTP receiver is permanently retired")
    async def test_receive_sync_data_returns_partial_when_items_fail(self):
        db = FakeDB()
        items = [{"table": "users", "operation": "INSERT", "id": 1, "data": {"full_name": "User"}}]

        with patch("api.routers.sync._apply_item", new=AsyncMock(return_value="error")), patch(
            "api.routers.sync.settings.server_mode", "iran"
        ):
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["processed"], 0)
        self.assertEqual(result["errors"], 1)
        self.assertEqual(result["error_items"][0]["reason"], "apply_failed")
        self.assertGreaterEqual(db.commits, 2)

    @unittest.skip("legacy direct FI<->IR HTTP receiver is permanently retired")
    async def test_receive_sync_data_rolls_back_and_raises_http_500_on_outer_failure(self):
        db = FakeDB(commit_results=[RuntimeError("commit failed")])
        items = [{"table": "users", "operation": "INSERT", "id": 1, "data": {"full_name": "User"}}]

        with patch("api.routers.sync._apply_item", new=AsyncMock(return_value="ok")), patch(
            "api.routers.sync.settings.server_mode", "iran"
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        self.assertEqual(exc_info.exception.status_code, 500)
        self.assertEqual(exc_info.exception.detail, "Sync batch processing failed")
        self.assertEqual(db.rollbacks, 1)

    async def test_receive_permanently_rejects_legacy_direct_sync_before_any_database_work(self):
        db = FakeDB()

        with self.assertRaises(HTTPException) as exc_info:
            await receive_sync_data(items=[], request=SimpleNamespace(), db=db, _=None)

        self.assertEqual(exc_info.exception.status_code, 410)
        self.assertEqual(exc_info.exception.detail, RETIRED_LEGACY_DIRECT_SYNC_HTTP_DETAIL)
        self.assertEqual([], db.execute_calls)
        self.assertEqual(0, db.commits)
        self.assertEqual(0, db.rollbacks)

    async def test_receive_route_guard_precedes_signature_and_database_dependencies(self):
        """FastAPI must apply the retirement fence before dependency setup."""

        app = FastAPI()
        app.include_router(sync_router.router)
        db_attempts: list[str] = []
        signature_attempts: list[str] = []

        async def should_not_create_database_session():
            db_attempts.append("get_db")
            raise AssertionError("permanent retirement fence must run before get_db")

        async def should_not_validate_legacy_signature(_request):
            signature_attempts.append("verify_signature")
            raise AssertionError("permanent retirement fence must run before verify_signature")

        app.dependency_overrides[sync_router.get_db] = should_not_create_database_session
        app.dependency_overrides[sync_router.verify_signature] = should_not_validate_legacy_signature
        transport = ASGITransport(app=app)

        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            response = await client.post("/receive", json=[])

        self.assertEqual(410, response.status_code)
        self.assertEqual(
            {"detail": RETIRED_LEGACY_DIRECT_SYNC_HTTP_DETAIL},
            response.json(),
        )
        self.assertEqual([], db_attempts)
        self.assertEqual([], signature_attempts)


if __name__ == "__main__":
    unittest.main()
