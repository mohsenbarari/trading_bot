import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from api.routers.admin_messages import (
    AdminMarketMessageRead,
    clear_current_market_message,
    get_current_market_message,
    get_market_message_history,
)


class AdminMessagesCurrentHistoryClearCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.fence = install_p1_00_network_fence()
        self.initial_denied_connects = len(self.fence["denied_connects"])
        self.initial_live_redis = len(self.fence["live_redis_constructs"])
        self.current_user = SimpleNamespace(id=42, role="super_admin")
        self.db = SimpleNamespace(
            execute=AsyncMock(side_effect=AssertionError("db.execute awaited unexpectedly")),
            commit=AsyncMock(side_effect=AssertionError("db.commit awaited unexpectedly")),
            rollback=AsyncMock(side_effect=AssertionError("db.rollback awaited unexpectedly")),
        )

    async def test_get_current_market_message_valid_cache_hit_returns_exact_payload_without_downstream_work(
        self,
    ) -> None:
        published_at = datetime(2026, 6, 1, 10, 0, 0)
        created_at = datetime(2026, 6, 1, 9, 30, 0)
        cached_payload = {
            "id": 88,
            "content": "پیام کش‌شده بازار",
            "created_by_id": 9,
            "created_by_name": "market_admin",
            "reused_from_id": 12,
            "is_active": True,
            "notified_recipients_count": 5,
            "published_at": published_at.isoformat(),
            "created_at": created_at.isoformat(),
        }

        get_cached_mock = AsyncMock(return_value=(True, cached_payload))
        get_current_service_mock = AsyncMock(
            side_effect=AssertionError(
                "api.routers.admin_messages.get_current_market_management_message awaited unexpectedly"
            )
        )
        set_cached_mock = AsyncMock(
            side_effect=AssertionError(
                "api.routers.admin_messages.set_cached_admin_market_current awaited unexpectedly"
            )
        )
        invalidate_cache_mock = AsyncMock(
            side_effect=AssertionError(
                "api.routers.admin_messages.invalidate_admin_market_current_cache awaited unexpectedly"
            )
        )

        with patch(
            "api.routers.admin_messages.get_cached_admin_market_current",
            new=get_cached_mock,
        ), patch(
            "api.routers.admin_messages.get_current_market_management_message",
            new=get_current_service_mock,
        ), patch(
            "api.routers.admin_messages.set_cached_admin_market_current",
            new=set_cached_mock,
        ), patch(
            "api.routers.admin_messages.invalidate_admin_market_current_cache",
            new=invalidate_cache_mock,
        ):
            result = await get_current_market_message(
                current_user=self.current_user,
                db=self.db,
            )

        get_cached_mock.assert_awaited_once_with()
        get_current_service_mock.assert_not_awaited()
        set_cached_mock.assert_not_awaited()
        invalidate_cache_mock.assert_not_awaited()
        self.db.execute.assert_not_awaited()
        self.db.commit.assert_not_awaited()
        self.db.rollback.assert_not_awaited()

        self.assertIsInstance(result, AdminMarketMessageRead)
        self.assertEqual(result.id, 88)
        self.assertEqual(result.content, "پیام کش‌شده بازار")
        self.assertEqual(result.created_by_id, 9)
        self.assertEqual(result.created_by_name, "market_admin")
        self.assertEqual(result.reused_from_id, 12)
        self.assertIs(result.is_active, True)
        self.assertEqual(result.notified_recipients_count, 5)
        self.assertEqual(result.published_at, published_at)
        self.assertEqual(result.created_at, created_at)

        self.assertEqual(len(self.fence["denied_connects"]), self.initial_denied_connects)
        self.assertEqual(len(self.fence["live_redis_constructs"]), self.initial_live_redis)

    async def test_get_market_message_history_explicit_limit_preserves_order_and_serializes_exactly(
        self,
    ) -> None:
        pub1 = datetime(2026, 6, 1, 10, 0, 0)
        created1 = datetime(2026, 6, 1, 9, 30, 0)
        pub2 = datetime(2026, 6, 2, 11, 0, 0)
        created2 = datetime(2026, 6, 2, 10, 30, 0)
        pub3 = datetime(2026, 6, 3, 12, 0, 0)
        created3 = datetime(2026, 6, 3, 11, 30, 0)

        raw_messages = [
            SimpleNamespace(
                id=101,
                content="پیام اول بازار",
                created_by_id=10,
                created_by=SimpleNamespace(account_name="author_alice"),
                reused_from_id=42,
                is_active=1,
                notified_recipients_count=17,
                published_at=pub1,
                created_at=created1,
            ),
            SimpleNamespace(
                id=102,
                content="پیام دوم بازار",
                created_by_id=11,
                created_by=None,
                reused_from_id=None,
                is_active=0,
                notified_recipients_count=None,
                published_at=pub2,
                created_at=created2,
            ),
            SimpleNamespace(
                id=103,
                content="پیام سوم بازار",
                created_by_id=12,
                created_by=SimpleNamespace(),
                reused_from_id=99,
                is_active=False,
                notified_recipients_count=0,
                published_at=pub3,
                created_at=created3,
            ),
        ]

        list_history_mock = AsyncMock(return_value=raw_messages)

        with patch(
            "api.routers.admin_messages.list_market_management_history",
            new=list_history_mock,
        ):
            result = await get_market_message_history(
                limit=7,
                current_user=self.current_user,
                db=self.db,
            )

        list_history_mock.assert_awaited_once_with(self.db, limit=7)
        self.assertIs(list_history_mock.await_args.args[0], self.db)
        self.assertEqual(list_history_mock.await_args.kwargs["limit"], 7)
        self.db.execute.assert_not_awaited()
        self.db.commit.assert_not_awaited()
        self.db.rollback.assert_not_awaited()

        self.assertEqual(len(result), 3)
        self.assertEqual([item.id for item in result], [101, 102, 103])
        for item in result:
            self.assertIsInstance(item, AdminMarketMessageRead)

        self.assertEqual(result[0].id, 101)
        self.assertEqual(result[0].content, "پیام اول بازار")
        self.assertEqual(result[0].created_by_id, 10)
        self.assertEqual(result[0].created_by_name, "author_alice")
        self.assertEqual(result[0].reused_from_id, 42)
        self.assertIs(result[0].is_active, True)
        self.assertEqual(result[0].notified_recipients_count, 17)
        self.assertEqual(result[0].published_at, pub1)
        self.assertEqual(result[0].created_at, created1)

        self.assertEqual(result[1].id, 102)
        self.assertEqual(result[1].content, "پیام دوم بازار")
        self.assertEqual(result[1].created_by_id, 11)
        self.assertIsNone(result[1].created_by_name)
        self.assertIsNone(result[1].reused_from_id)
        self.assertIs(result[1].is_active, False)
        self.assertEqual(result[1].notified_recipients_count, 0)
        self.assertEqual(result[1].published_at, pub2)
        self.assertEqual(result[1].created_at, created2)

        self.assertEqual(result[2].id, 103)
        self.assertEqual(result[2].content, "پیام سوم بازار")
        self.assertEqual(result[2].created_by_id, 12)
        self.assertIsNone(result[2].created_by_name)
        self.assertEqual(result[2].reused_from_id, 99)
        self.assertIs(result[2].is_active, False)
        self.assertEqual(result[2].notified_recipients_count, 0)
        self.assertEqual(result[2].published_at, pub3)
        self.assertEqual(result[2].created_at, created3)

        self.assertEqual(len(self.fence["denied_connects"]), self.initial_denied_connects)
        self.assertEqual(len(self.fence["live_redis_constructs"]), self.initial_live_redis)

    async def test_clear_current_market_message_missing_current_clears_cache_and_publishes_in_order(
        self,
    ) -> None:
        call_order: list[str] = []

        async def _fake_deactivate(db):
            call_order.append("deactivate_current_market_management_message")
            return None

        async def _fake_set_cache(payload):
            call_order.append("set_cached_admin_market_current")
            return True

        def _fake_publish_event(event_name, payload):
            call_order.append("publish_event_sync")

        deactivate_mock = AsyncMock(side_effect=_fake_deactivate)
        set_cache_mock = AsyncMock(side_effect=_fake_set_cache)
        publish_event_mock = Mock(side_effect=_fake_publish_event)

        with patch(
            "api.routers.admin_messages.deactivate_current_market_management_message",
            new=deactivate_mock,
        ), patch(
            "api.routers.admin_messages.set_cached_admin_market_current",
            new=set_cache_mock,
        ), patch(
            "api.routers.admin_messages.publish_event_sync",
            new=publish_event_mock,
        ):
            result = await clear_current_market_message(
                current_user=self.current_user,
                db=self.db,
            )

        self.assertEqual(
            call_order,
            [
                "deactivate_current_market_management_message",
                "set_cached_admin_market_current",
                "publish_event_sync",
            ],
        )
        deactivate_mock.assert_awaited_once_with(self.db)
        self.assertIs(deactivate_mock.await_args.args[0], self.db)
        set_cache_mock.assert_awaited_once_with(None)
        publish_event_mock.assert_called_once_with("market:admin_message_published", None)
        self.assertIsNone(result)

        self.db.execute.assert_not_awaited()
        self.db.commit.assert_not_awaited()
        self.db.rollback.assert_not_awaited()

        self.assertEqual(len(self.fence["denied_connects"]), self.initial_denied_connects)
        self.assertEqual(len(self.fence["live_redis_constructs"]), self.initial_live_redis)


if __name__ == "__main__":
    unittest.main()
