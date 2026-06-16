import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core import web_push


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

    async def test_send_returns_zero_summary_when_unconfigured(self):
        with patch.object(web_push, "is_web_push_configured", return_value=False):
            result = await web_push.send_web_push_to_user(object(), 1, {"title": "x"})

        self.assertEqual(result, {"total": 0, "sent": 0, "failed": 0, "disabled": 0})


if __name__ == "__main__":
    unittest.main()
