import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.sync import receive_sync_data


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

    async def test_receive_sync_data_returns_partial_when_items_fail(self):
        db = FakeDB()
        items = [{"table": "users", "operation": "INSERT", "id": 1, "data": {"full_name": "User"}}]

        with patch("api.routers.sync._apply_item", new=AsyncMock(return_value="error")), patch(
            "api.routers.sync.settings.server_mode", "iran"
        ):
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        self.assertEqual(result, {"status": "partial", "processed": 0, "errors": 1})
        self.assertGreaterEqual(db.commits, 2)

    async def test_receive_sync_data_rolls_back_and_raises_http_500_on_outer_failure(self):
        db = FakeDB(commit_results=[RuntimeError("commit failed")])
        items = [{"table": "users", "operation": "INSERT", "id": 1, "data": {"full_name": "User"}}]

        with patch("api.routers.sync._apply_item", new=AsyncMock(return_value="ok")), patch(
            "api.routers.sync.settings.server_mode", "iran"
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        self.assertEqual(exc_info.exception.status_code, 500)
        self.assertEqual(exc_info.exception.detail, "commit failed")
        self.assertEqual(db.rollbacks, 1)


if __name__ == "__main__":
    unittest.main()