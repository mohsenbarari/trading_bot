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
    async def test_fake_db_helper_paths(self):
        db = FakeDB()
        await db.rollback()
        self.assertEqual(db.rollbacks, 1)

        async with db.begin_nested() as nested:
            self.assertIsNone(nested)

    async def test_receive_sync_data_relays_notifications_and_refreshes_unread_counts(self):
        db = FakeDB()
        items = [
            {"type": "notification", "chat_id": 123, "text": "hi", "parse_mode": "HTML"},
            {"table": "notifications", "operation": "INSERT", "id": 9, "data": {"user_id": 5, "message": "x"}},
        ]

        with patch("core.notifications.send_telegram_message", new=AsyncMock()) as send_mock, patch(
            "api.routers.sync._apply_item", new=AsyncMock(return_value="ok")
        ) as apply_mock, patch(
            "api.routers.sync.ensure_mandatory_channel_rollout", new=AsyncMock()
        ) as rollout_mock, patch(
            "api.routers.sync._refresh_notification_unread_counts", new=AsyncMock()
        ) as refresh_mock, patch("api.routers.sync.settings.server_mode", "iran"):
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        send_mock.assert_awaited_once_with(chat_id=123, text="hi", parse_mode="HTML")
        apply_mock.assert_awaited_once()
        rollout_mock.assert_not_awaited()
        refresh_mock.assert_awaited_once_with(db, {5})
        self.assertEqual(result, {"status": "success", "processed": 2})
        self.assertGreaterEqual(db.commits, 2)

    async def test_receive_sync_data_rolls_out_mandatory_channel_after_user_sync(self):
        db = FakeDB()
        items = [{"table": "users", "operation": "UPDATE", "id": 4, "data": {"telegram_id": 1234, "role": "عادی"}}]

        with patch("api.routers.sync._apply_item", new=AsyncMock(return_value="ok")) as apply_mock, patch(
            "api.routers.sync.ensure_mandatory_channel_rollout", new=AsyncMock()
        ) as rollout_mock, patch("api.routers.sync.settings.server_mode", "iran"):
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        apply_mock.assert_awaited_once()
        rollout_mock.assert_awaited_once_with(db)
        self.assertEqual(result, {"status": "success", "processed": 1})

    async def test_receive_sync_data_retries_deferred_items_and_invalidates_commodity_caches(self):
        db = FakeDB()
        items = [{"table": "commodities", "operation": "INSERT", "id": 3, "data": {"name": "gold"}}]

        with patch("api.routers.sync._apply_item", new=AsyncMock(side_effect=["deferred", "ok"])) as apply_mock, patch(
            "api.routers.sync.settings.server_mode", "iran"
        ), patch("api.routers.sync.ensure_mandatory_channel_rollout", new=AsyncMock()) as rollout_mock, patch(
            "core.cache.invalidate_commodities_cache", new=AsyncMock()) as invalidate_cache, patch(
            "bot.utils.redis_helpers.invalidate_commodity_cache", new=AsyncMock()
        ) as invalidate_bot_cache:
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        self.assertEqual(apply_mock.await_count, 2)
        rollout_mock.assert_not_awaited()
        invalidate_cache.assert_awaited_once()
        invalidate_bot_cache.assert_awaited_once()
        self.assertEqual(result, {"status": "success", "processed": 1})

    async def test_receive_sync_data_orders_accountant_relations_before_actor_stamped_offers_and_trades(self):
        db = FakeDB()
        items = [
            {"table": "trades", "operation": "INSERT", "id": 33, "data": {"offer_id": 22, "actor_user_id": 71}},
            {
                "table": "accountant_relations",
                "operation": "INSERT",
                "id": 21,
                "data": {"owner_user_id": 7, "accountant_user_id": 71, "status": "active"},
            },
            {"table": "offers", "operation": "INSERT", "id": 22, "data": {"user_id": 7, "actor_user_id": 71}},
            {"table": "users", "operation": "INSERT", "id": 7, "data": {"telegram_id": 7001}},
        ]
        seen_calls = []

        async def fake_apply_item(db_arg, table, operation, record_id, data, model, new_offers):
            seen_calls.append((table, record_id, dict(data)))
            return "ok"

        with patch("api.routers.sync._apply_item", new=AsyncMock(side_effect=fake_apply_item)) as apply_mock, patch(
            "api.routers.sync.ensure_mandatory_channel_rollout", new=AsyncMock()
        ) as rollout_mock, patch("api.routers.sync.settings.server_mode", "iran"):
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        self.assertEqual(result, {"status": "success", "processed": 4})
        self.assertEqual(apply_mock.await_count, 4)
        self.assertEqual([call[0] for call in seen_calls], ["users", "accountant_relations", "offers", "trades"])
        self.assertEqual(seen_calls[2][2]["actor_user_id"], 71)
        self.assertEqual(seen_calls[3][2]["actor_user_id"], 71)
        rollout_mock.assert_awaited_once_with(db)


if __name__ == "__main__":
    unittest.main()