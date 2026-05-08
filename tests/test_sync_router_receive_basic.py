import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.routers.sync import receive_sync_data


class FakeDB:
    def __init__(self):
        self.execute_calls = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, stmt, *args, **kwargs):
        self.execute_calls.append((stmt, args, kwargs))
        return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: None))

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    def begin_nested(self):
        class _Ctx:
            async def __aenter__(self_inner):
                return None

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

        return _Ctx()


class SyncRouterReceiveBasicTests(unittest.IsolatedAsyncioTestCase):
    async def test_receive_sync_data_relays_notifications_and_refreshes_unread_counts(self):
        db = FakeDB()
        items = [
            {"type": "notification", "chat_id": 123, "text": "hi", "parse_mode": "HTML"},
            {"table": "notifications", "operation": "INSERT", "id": 9, "data": {"user_id": 5, "message": "x"}},
        ]

        with patch("core.notifications.send_telegram_message", new=AsyncMock()) as send_mock, patch(
            "api.routers.sync._apply_item", new=AsyncMock(return_value="ok")
        ) as apply_mock, patch(
            "api.routers.sync._refresh_notification_unread_counts", new=AsyncMock()
        ) as refresh_mock, patch("api.routers.sync.settings.server_mode", "iran"):
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        send_mock.assert_awaited_once_with(chat_id=123, text="hi", parse_mode="HTML")
        apply_mock.assert_awaited_once()
        refresh_mock.assert_awaited_once_with(db, {5})
        self.assertEqual(result, {"status": "success", "processed": 2})
        self.assertGreaterEqual(db.commits, 2)

    async def test_receive_sync_data_retries_deferred_items_and_invalidates_commodity_caches(self):
        db = FakeDB()
        items = [{"table": "commodities", "operation": "INSERT", "id": 3, "data": {"name": "gold"}}]

        with patch("api.routers.sync._apply_item", new=AsyncMock(side_effect=["deferred", "ok"])) as apply_mock, patch(
            "api.routers.sync.settings.server_mode", "iran"
        ), patch("core.cache.invalidate_commodities_cache", new=AsyncMock()) as invalidate_cache, patch(
            "bot.utils.redis_helpers.invalidate_commodity_cache", new=AsyncMock()
        ) as invalidate_bot_cache:
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        self.assertEqual(apply_mock.await_count, 2)
        invalidate_cache.assert_awaited_once()
        invalidate_bot_cache.assert_awaited_once()
        self.assertEqual(result, {"status": "success", "processed": 1})


if __name__ == "__main__":
    unittest.main()