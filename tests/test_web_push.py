import unittest
import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from core import web_push
from models.offer import OfferStatus


class FakeAsyncSessionContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_exc):
        return False


class WebPushHelpersTests(unittest.IsolatedAsyncioTestCase):
    def test_endpoint_hash_is_stable_and_non_plaintext(self):
        endpoint = "https://push.example.test/subscription/abc"

        digest = web_push.hash_endpoint(endpoint)

        self.assertEqual(len(digest), 64)
        self.assertEqual(digest, web_push.hash_endpoint(endpoint))
        self.assertNotIn("subscription", digest)

    def test_config_status_does_not_expose_private_key(self):
        with patch.object(web_push.settings, "web_push_enabled", True), patch.object(
            web_push.settings, "web_push_vapid_public_key", "public"
        ), patch.object(web_push.settings, "web_push_vapid_private_key", "private"), patch.object(
            web_push.settings, "web_push_vapid_subject", "mailto:test@example.com"
        ), patch.object(
            web_push, "is_web_push_dependency_available", return_value=True
        ):
            status = web_push.web_push_config_status()

        self.assertTrue(status["enabled"])
        self.assertEqual(status["public_key"], "public")
        self.assertNotIn("private", repr(status))

    def test_notification_payload_defaults_to_notification_center_route(self):
        notification = type(
            "NotificationLike",
            (),
            {
                "id": 12,
                "message": "پیام تست",
                "category": "SYSTEM",
                "level": "INFO",
            },
        )()

        payload = web_push.build_notification_push_payload(notification)

        self.assertEqual(payload["title"], "پیام مدیریت")
        self.assertEqual(payload["body"], "پیام تست")
        self.assertEqual(payload["route"], "/notifications")
        self.assertEqual(payload["data"]["notification_id"], 12)

    def test_market_offer_payload_routes_to_market_without_owner_identity(self):
        offer = SimpleNamespace(
            id=42,
            offer_type=SimpleNamespace(value="buy"),
            commodity_id=3,
            commodity=SimpleNamespace(name="سکه"),
            quantity=12,
            price=345678,
        )

        payload = web_push.build_market_offer_push_payload(offer)

        self.assertEqual(payload["title"], "آفر جدید بازار")
        self.assertEqual(payload["route"], "/market")
        self.assertEqual(payload["tag"], "market-offer:42")
        self.assertEqual(payload["data"]["kind"], "market_offer")
        self.assertEqual(payload["data"]["offer_id"], 42)
        self.assertEqual(payload["data"]["offer_type"], "buy")
        self.assertEqual(payload["data"]["commodity_id"], 3)
        self.assertEqual(payload["data"]["commodity_name"], "سکه")
        self.assertIn("خرید سکه", payload["body"])
        self.assertIn("12", payload["body"])
        self.assertNotIn("091", repr(payload))

    async def test_market_offer_targets_require_enabled_subscription_and_preference(self):
        class FakeExecuteResult:
            def scalars(self):
                return SimpleNamespace(all=lambda: [2, 3])

        class FakeDB:
            def __init__(self):
                self.statement = None

            async def execute(self, stmt):
                self.statement = stmt
                return FakeExecuteResult()

        db = FakeDB()
        with patch.object(web_push, "load_market_page_user_ids", new=AsyncMock(return_value=set())):
            target_user_ids = await web_push.load_market_offer_push_target_user_ids(
                db,
                excluded_user_ids={1, 4},
            )

        self.assertEqual(target_user_ids, [2, 3])
        compiled = str(db.statement.compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("push_subscriptions", compiled)
        self.assertIn("user_notification_preferences", compiled)
        self.assertIn("market_offer_push_enabled", compiled)
        self.assertIn("users.id NOT IN", compiled)

    async def test_market_offer_targets_skip_users_currently_viewing_market(self):
        class FakeExecuteResult:
            def scalars(self):
                return SimpleNamespace(all=lambda: [2, 3, 4])

        class FakeDB:
            async def execute(self, _stmt):
                return FakeExecuteResult()

        with patch.object(web_push, "load_market_page_user_ids", new=AsyncMock(return_value={3})):
            target_user_ids = await web_push.load_market_offer_push_target_user_ids(FakeDB())

        self.assertEqual(target_user_ids, [2, 4])

    async def test_market_offer_push_only_treats_offer_as_first_when_no_other_active_offer_exists(self):
        class FakeDB:
            def __init__(self, scalar_result):
                self.scalar_result = scalar_result
                self.statement = None

            async def scalar(self, stmt):
                self.statement = stmt
                return self.scalar_result

        empty_market_db = FakeDB(0)
        busy_market_db = FakeDB(2)

        with patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(return_value=SimpleNamespace(offer_expiry_minutes=2)),
        ), patch("core.utils.utc_now_naive", return_value=datetime(2026, 6, 18, 7, 20, 0)):
            self.assertTrue(await web_push.is_first_active_market_offer(empty_market_db, 42))
            self.assertFalse(await web_push.is_first_active_market_offer(busy_market_db, 42))

        compiled = str(empty_market_db.statement.compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("offers.status = 'ACTIVE'", compiled)
        self.assertIn("offers.id !=", compiled)
        self.assertIn("offers.created_at >", compiled)

    async def test_market_offer_push_ignores_time_expired_active_offers_when_detecting_first_live_offer(self):
        class FakeDB:
            statement = None

            async def scalar(self, stmt):
                self.statement = stmt
                return 0

        db = FakeDB()
        with patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(return_value=SimpleNamespace(offer_expiry_minutes=2)),
        ), patch("core.utils.utc_now_naive", return_value=datetime(2026, 6, 18, 7, 20, 0)):
            self.assertTrue(await web_push.is_first_active_market_offer(db, 42))

        compiled = str(db.statement.compile(compile_kwargs={"literal_binds": True}))
        self.assertIn("offers.status = 'ACTIVE'", compiled)
        self.assertIn("offers.id !=", compiled)
        self.assertIn("offers.created_at > '2026-06-18 07:18:00'", compiled)

    async def test_market_offer_push_logs_skip_when_offer_is_not_first_live_offer(self):
        offer = SimpleNamespace(
            id=42,
            user_id=1,
            actor_user_id=1,
            status=OfferStatus.ACTIVE,
            commodity=SimpleNamespace(name="سکه"),
            offer_type=SimpleNamespace(value="sell"),
            quantity=10,
            price=123,
        )

        class FakeExecuteResult:
            def scalar_one_or_none(self):
                return offer

        class FakeDB:
            async def execute(self, _stmt):
                return FakeExecuteResult()

        with patch.object(web_push, "is_web_push_configured", return_value=True), patch(
            "core.db.AsyncSessionLocal",
            return_value=FakeAsyncSessionContext(FakeDB()),
        ), patch.object(
            web_push, "is_first_active_market_offer", new=AsyncMock(return_value=False)
        ), patch.object(
            web_push.logger, "info"
        ) as info_mock:
            await web_push.send_market_offer_web_push(42)

        info_mock.assert_called_once()
        self.assertEqual(info_mock.call_args.kwargs["extra"]["event"], "web_push.market_offer.skipped")
        self.assertEqual(info_mock.call_args.kwargs["extra"]["reason"], "not_first_live_offer")
        self.assertEqual(info_mock.call_args.kwargs["extra"]["offer_id"], 42)

    async def test_market_offer_push_logs_delivery_summary(self):
        offer = SimpleNamespace(
            id=42,
            user_id=1,
            actor_user_id=1,
            status=OfferStatus.ACTIVE,
            commodity=SimpleNamespace(name="سکه"),
            offer_type=SimpleNamespace(value="sell"),
            commodity_id=3,
            quantity=10,
            price=123,
        )

        class FakeExecuteResult:
            def scalar_one_or_none(self):
                return offer

        class FakeDB:
            async def execute(self, _stmt):
                return FakeExecuteResult()

        with patch.object(web_push, "is_web_push_configured", return_value=True), patch(
            "core.db.AsyncSessionLocal",
            return_value=FakeAsyncSessionContext(FakeDB()),
        ), patch.object(
            web_push, "is_first_active_market_offer", new=AsyncMock(return_value=True)
        ), patch.object(
            web_push, "load_market_offer_push_target_user_ids", new=AsyncMock(return_value=[2, 3])
        ), patch.object(
            web_push,
            "send_web_push_to_user",
            new=AsyncMock(
                side_effect=[
                    {"total": 1, "sent": 1, "failed": 0, "disabled": 0},
                    {"total": 2, "sent": 1, "failed": 1, "disabled": 1},
                ]
            ),
        ), patch.object(web_push.logger, "info") as info_mock:
            await web_push.send_market_offer_web_push(42)

        info_mock.assert_called_once()
        extra = info_mock.call_args.kwargs["extra"]
        self.assertEqual(extra["event"], "web_push.market_offer.completed")
        self.assertEqual(extra["offer_id"], 42)
        self.assertEqual(extra["target_user_count"], 2)
        self.assertEqual(extra["subscription_total"], 3)
        self.assertEqual(extra["sent"], 2)
        self.assertEqual(extra["failed"], 1)
        self.assertEqual(extra["disabled"], 1)

    async def test_send_returns_zero_summary_when_unconfigured(self):
        with patch.object(web_push, "is_web_push_configured", return_value=False):
            result = await web_push.send_web_push_to_user(object(), 1, {"title": "x"})

        self.assertEqual(result, {"total": 0, "sent": 0, "failed": 0, "disabled": 0})

    def test_market_offer_scheduler_ignores_cancelled_background_task(self):
        class FakeTask:
            callback = None

            def add_done_callback(self, callback):
                self.callback = callback

        class FakeDoneTask:
            def result(self):
                raise asyncio.CancelledError()

        task = FakeTask()
        loop = SimpleNamespace(create_task=Mock(side_effect=lambda coro: (coro.close(), task)[1]))

        with patch.object(web_push, "is_web_push_configured", return_value=True), patch.object(
            web_push.asyncio, "get_running_loop", return_value=loop
        ), patch.object(web_push.logger, "exception") as exception_mock:
            web_push.schedule_market_offer_web_push(42)
            task.callback(FakeDoneTask())

        exception_mock.assert_not_called()

    def test_notification_scheduler_ignores_cancelled_background_task(self):
        class FakeTask:
            callback = None

            def add_done_callback(self, callback):
                self.callback = callback

        class FakeDoneTask:
            def result(self):
                raise asyncio.CancelledError()

        task = FakeTask()
        loop = SimpleNamespace(create_task=Mock(side_effect=lambda coro: (coro.close(), task)[1]))

        with patch.object(web_push, "is_web_push_configured", return_value=True), patch.object(
            web_push.asyncio, "get_running_loop", return_value=loop
        ), patch.object(web_push.logger, "exception") as exception_mock:
            web_push.schedule_notification_web_push(9)
            task.callback(FakeDoneTask())

        exception_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
