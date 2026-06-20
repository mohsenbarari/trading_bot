import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from api.routers.realtime import REALTIME_SOURCE_SYNC_APPLY
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


class FakeTerminalOfferRows:
    def __init__(self, offers):
        self._offers = offers

    def scalars(self):
        return SimpleNamespace(all=lambda: list(self._offers))


class TerminalOfferRealtimeDB(FakeDB):
    def __init__(self, offers):
        super().__init__()
        self._offers = offers

    async def execute(self, stmt, *args, **kwargs):
        self.execute_calls.append((stmt, args, kwargs))
        if "setval(" in str(stmt):
            return SimpleNamespace()
        return FakeTerminalOfferRows(self._offers)


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
        items = [
            {"table": "commodities", "operation": "INSERT", "id": 3, "data": {"name": "gold"}},
            {"table": "admin_market_messages", "operation": "INSERT", "id": 7, "data": {"content": "market"}},
        ]
        deferred_tables = set()

        async def fake_apply_item(db_arg, table, operation, record_id, data, model, new_offers, terminal_offers=None):
            if table == "commodities" and table not in deferred_tables:
                deferred_tables.add(table)
                return "deferred"
            return "ok"

        with patch("api.routers.sync._apply_item", new=AsyncMock(side_effect=fake_apply_item)) as apply_mock, patch(
            "api.routers.sync.settings.server_mode", "iran"
        ), patch("api.routers.sync.ensure_mandatory_channel_rollout", new=AsyncMock()) as rollout_mock, patch(
            "core.cache.invalidate_commodities_cache", new=AsyncMock()) as invalidate_cache, patch(
            "core.cache.invalidate_admin_market_current_cache", new=AsyncMock()
        ) as invalidate_admin_market_cache, patch(
            "bot.utils.redis_helpers.invalidate_commodity_cache", new=AsyncMock()
        ) as invalidate_bot_cache:
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        self.assertEqual(apply_mock.await_count, 3)
        rollout_mock.assert_not_awaited()
        invalidate_cache.assert_awaited_once()
        invalidate_admin_market_cache.assert_awaited_once()
        invalidate_bot_cache.assert_awaited_once()
        self.assertEqual(result, {"status": "success", "processed": 2})

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

        async def fake_apply_item(db_arg, table, operation, record_id, data, model, new_offers, terminal_offers=None):
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

    async def test_receive_sync_data_orders_customer_relations_and_preserves_user_max_customers(self):
        db = FakeDB()
        items = [
            {"table": "trades", "operation": "INSERT", "id": 44, "data": {"offer_id": 90, "quantity": 2, "price": 125000}},
            {
                "table": "customer_relations",
                "operation": "UPDATE",
                "id": 31,
                "data": {
                    "owner_user_id": 7,
                    "customer_user_id": 12,
                    "management_name": "مشتری ویژه",
                    "customer_tier": "tier2",
                    "status": "deleted",
                    "deleted_at": "2026-05-21T09:00:00",
                },
            },
            {"table": "users", "operation": "UPDATE", "id": 7, "data": {"telegram_id": 7001, "max_customers": 9}},
        ]
        seen_calls = []

        async def fake_apply_item(db_arg, table, operation, record_id, data, model, new_offers, terminal_offers=None):
            seen_calls.append((table, record_id, dict(data)))
            return "ok"

        with patch("api.routers.sync._apply_item", new=AsyncMock(side_effect=fake_apply_item)) as apply_mock, patch(
            "api.routers.sync.ensure_mandatory_channel_rollout", new=AsyncMock()
        ) as rollout_mock, patch("api.routers.sync.settings.server_mode", "iran"):
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        self.assertEqual(result, {"status": "success", "processed": 3})
        self.assertEqual(apply_mock.await_count, 3)
        self.assertEqual([call[0] for call in seen_calls], ["users", "customer_relations", "trades"])
        self.assertEqual(seen_calls[0][2]["max_customers"], 9)
        self.assertEqual(seen_calls[1][2]["status"], "deleted")
        self.assertEqual(seen_calls[1][2]["management_name"], "مشتری ویژه")
        rollout_mock.assert_awaited_once_with(db)

    async def test_receive_sync_data_publishes_local_realtime_for_synced_terminal_offers(self):
        completed_offer = SimpleNamespace(id=41, status="completed", remaining_quantity=0, lot_sizes=None)
        expired_offer = SimpleNamespace(id=42, status="expired", remaining_quantity=6, lot_sizes=[6])
        db = TerminalOfferRealtimeDB([completed_offer, expired_offer])
        items = [
            {"table": "offers", "operation": "UPDATE", "id": 41, "data": {"status": "completed"}},
            {"table": "offers", "operation": "UPDATE", "id": 42, "data": {"status": "expired", "expire_reason": "time_limit"}},
        ]

        async def fake_apply_item(db_arg, table, operation, record_id, data, model, new_offers, terminal_offers=None):
            terminal_offers.append(record_id)
            return "ok"

        with patch("api.routers.sync._apply_item", new=AsyncMock(side_effect=fake_apply_item)), patch(
            "api.routers.sync.settings.server_mode", "iran"
        ), patch("api.routers.sync.ensure_mandatory_channel_rollout", new=AsyncMock()), patch(
            "api.routers.realtime.publish_event", new=AsyncMock()
        ) as publish_mock:
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        self.assertEqual(result, {"status": "success", "processed": 2})
        self.assertEqual(publish_mock.await_count, 2)
        publish_mock.assert_any_await(
            "offer:updated",
            {
                "id": 41,
                "status": "completed",
                "remaining_quantity": 0,
                "lot_sizes": None,
            },
            source=REALTIME_SOURCE_SYNC_APPLY,
        )
        publish_mock.assert_any_await("offer:expired", {"id": 42}, source=REALTIME_SOURCE_SYNC_APPLY)

    async def test_receive_sync_data_publishes_terminal_realtime_when_completed_trade_sync_arrives_later(self):
        completed_offer = SimpleNamespace(id=51, status="completed", remaining_quantity=0, lot_sizes=None)
        db = TerminalOfferRealtimeDB([completed_offer])
        items = [
            {
                "table": "trades",
                "operation": "INSERT",
                "id": 701,
                "data": {"offer_id": 51, "status": "completed", "quantity": 4},
            },
        ]

        with patch("api.routers.sync._apply_item", new=AsyncMock(return_value="ok")), patch(
            "api.routers.sync.settings.server_mode", "iran"
        ), patch("api.routers.sync.ensure_mandatory_channel_rollout", new=AsyncMock()), patch(
            "api.routers.realtime.publish_event", new=AsyncMock()
        ) as publish_mock:
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        self.assertEqual(result, {"status": "success", "processed": 1})
        publish_mock.assert_awaited_once_with(
            "offer:updated",
            {
                "id": 51,
                "status": "completed",
                "remaining_quantity": 0,
                "lot_sizes": None,
            },
            source=REALTIME_SOURCE_SYNC_APPLY,
        )

    async def test_receive_sync_data_publishes_created_realtime_for_synced_iran_offer(self):
        created_offer = SimpleNamespace(
            id=61,
            offer_public_id="ofr_synced_public_61",
            status="active",
            offer_type="sell",
            commodity_id=3,
            commodity=SimpleNamespace(name="gold"),
            quantity=40,
            remaining_quantity=40,
            price=125000,
            created_at=datetime(2026, 6, 19, 10, 0, tzinfo=timezone.utc),
            notes="sync create",
            is_wholesale=False,
            lot_sizes=[20, 20],
            original_lot_sizes=[20, 20],
        )
        db = TerminalOfferRealtimeDB([created_offer])
        items = [
            {
                "table": "offers",
                "operation": "INSERT",
                "id": 61,
                "data": {"status": "active", "offer_public_id": "ofr_synced_public_61"},
            },
        ]

        async def fake_apply_item(db_arg, table, operation, record_id, data, model, new_offers, terminal_offers=None):
            new_offers.append(record_id)
            return "ok"

        with patch("api.routers.sync._apply_item", new=AsyncMock(side_effect=fake_apply_item)), patch(
            "api.routers.sync.settings.server_mode", "iran"
        ), patch("api.routers.sync.ensure_mandatory_channel_rollout", new=AsyncMock()), patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(return_value=SimpleNamespace(offer_expiry_minutes=20)),
        ), patch("api.routers.realtime.publish_event", new=AsyncMock()) as publish_mock:
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        self.assertEqual(result, {"status": "success", "processed": 1})
        publish_mock.assert_awaited_once()
        event_name, payload = publish_mock.await_args.args
        self.assertEqual(event_name, "offer:created")
        self.assertEqual(payload["offer_public_id"], "ofr_synced_public_61")
        self.assertEqual(payload["public_link"], "/market?offer=ofr_synced_public_61")
        self.assertIsNone(payload["user_id"])
        self.assertEqual(payload["remaining_quantity"], 40)
        self.assertEqual(publish_mock.await_args.kwargs["source"], REALTIME_SOURCE_SYNC_APPLY)

    async def test_receive_sync_data_publishes_active_update_when_partial_trade_sync_arrives(self):
        active_offer = SimpleNamespace(id=52, status="active", remaining_quantity=6, lot_sizes=[6])
        db = TerminalOfferRealtimeDB([active_offer])
        items = [
            {
                "table": "trades",
                "operation": "INSERT",
                "id": 702,
                "data": {"offer_id": 52, "status": "completed", "quantity": 4},
            },
        ]

        with patch("api.routers.sync._apply_item", new=AsyncMock(return_value="ok")), patch(
            "api.routers.sync.settings.server_mode", "iran"
        ), patch("api.routers.sync.ensure_mandatory_channel_rollout", new=AsyncMock()), patch(
            "api.routers.realtime.publish_event", new=AsyncMock()
        ) as publish_mock:
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        self.assertEqual(result, {"status": "success", "processed": 1})
        publish_mock.assert_awaited_once_with(
            "offer:updated",
            {
                "id": 52,
                "status": "active",
                "remaining_quantity": 6,
                "lot_sizes": [6],
            },
            source=REALTIME_SOURCE_SYNC_APPLY,
        )

    async def test_receive_sync_data_applies_telegram_state_when_completed_trade_sync_arrives_later(self):
        completed_offer = SimpleNamespace(
            id=52,
            status="completed",
            remaining_quantity=0,
            lot_sizes=None,
            channel_message_id=777,
            commodity=SimpleNamespace(id=2),
        )
        db = TerminalOfferRealtimeDB([completed_offer])
        items = [
            {
                "table": "trades",
                "operation": "INSERT",
                "id": 702,
                "data": {"offer_id": 52, "status": "completed", "quantity": 4},
            },
        ]
        publication_state = SimpleNamespace(message_id=777)

        with patch("api.routers.sync._apply_item", new=AsyncMock(return_value="ok")), patch(
            "api.routers.sync.settings.server_mode", "foreign"
        ), patch("api.routers.sync.ensure_mandatory_channel_rollout", new=AsyncMock()), patch(
            "api.routers.realtime.publish_event", new=AsyncMock()
        ), patch(
            "core.services.telegram_offer_publication_service.load_telegram_publication_state_for_update",
            new=AsyncMock(return_value=publication_state),
        ), patch(
            "core.services.telegram_offer_channel_service.apply_offer_channel_state",
            new=AsyncMock(return_value=True),
        ) as apply_state_mock:
            result = await receive_sync_data(items=items, request=SimpleNamespace(), db=db, _=None)

        self.assertEqual(result, {"status": "success", "processed": 1})
        apply_state_mock.assert_awaited_once_with(
            completed_offer,
            publication_state=publication_state,
            reason="sync_terminal_offer",
        )


if __name__ == "__main__":
    unittest.main()
