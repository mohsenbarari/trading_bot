import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.routers.sync import receive_sync_data


class FakeDB:
    def __init__(self):
        self.commits = 0

    async def execute(self, stmt, *args, **kwargs):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: None))

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        raise AssertionError("rollback should not be called")

    def begin_nested(self):
        class _Ctx:
            async def __aenter__(self_inner):
                return None

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        return _Ctx()


class SyncRouterReceiveSettingsCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_fake_db_helper_paths(self):
        db = FakeDB()
        async with db.begin_nested() as nested:
            self.assertIsNone(nested)

        with self.assertRaisesRegex(AssertionError, "rollback should not be called"):
            await db.rollback()

    async def test_receive_sync_data_refreshes_trading_settings_cache(self):
        db = FakeDB()
        items = [{"table": "trading_settings", "operation": "INSERT", "id": 1, "data": {"key": "x", "value": "1"}}]

        with patch("api.routers.sync._apply_item", new=AsyncMock(return_value="ok")), patch(
            "api.routers.sync.settings.server_mode", "iran"
        ), patch("core.trading_settings.refresh_settings_cache_async", new=AsyncMock()) as refresh_mock:
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        refresh_mock.assert_awaited_once()
        self.assertEqual(result, {"status": "success", "processed": 1})

    async def test_receive_sync_data_tolerates_trading_settings_cache_refresh_failure(self):
        db = FakeDB()
        items = [{"table": "trading_settings", "operation": "INSERT", "id": 1, "data": {"key": "x", "value": "1"}}]

        with patch("api.routers.sync._apply_item", new=AsyncMock(return_value="ok")), patch(
            "api.routers.sync.settings.server_mode", "iran"
        ), patch(
            "core.trading_settings.refresh_settings_cache_async", new=AsyncMock(side_effect=RuntimeError("cache fail"))
        ):
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        self.assertEqual(result, {"status": "success", "processed": 1})


if __name__ == "__main__":
    unittest.main()