import unittest
from types import SimpleNamespace

from aiogram.types import Update

from bot.middlewares.logging_context import BotLoggingContextMiddleware
from core.request_context import get_request_context


class _FakeTelegramUser:
    id = 12345


class BotLoggingContextMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_middleware_sets_context_for_handler_and_clears_afterward(self):
        middleware = BotLoggingContextMiddleware()
        captured_context = {}
        db_user = SimpleNamespace(id=7, role=SimpleNamespace(value="user"))

        async def handler(event, data):
            captured_context.update(get_request_context())
            return "ok"

        result = await middleware(handler, SimpleNamespace(from_user=_FakeTelegramUser()), {"user": db_user})

        self.assertEqual(result, "ok")
        self.assertEqual(captured_context["log_class"], "bot")
        self.assertEqual(captured_context["bot_event_type"], "SimpleNamespace")
        self.assertTrue(captured_context["telegram_user_id"].startswith("tg:"))
        self.assertEqual(captured_context["actor_id"], 7)
        self.assertEqual(captured_context["actor_role"], "user")
        self.assertIn("bot_correlation_id", captured_context)
        self.assertEqual(get_request_context(), {})

    async def test_update_events_store_telegram_update_id_and_hashed_user_id(self):
        middleware = BotLoggingContextMiddleware()
        captured_context = {}
        db_user = SimpleNamespace(id=7, role=SimpleNamespace(value="user"))
        update = Update.model_validate(
            {
                "update_id": 987654321,
                "message": {
                    "message_id": 1,
                    "date": 1710000000,
                    "chat": {"id": 1, "type": "private"},
                    "from": {"id": 12345, "is_bot": False, "first_name": "Test"},
                    "text": "hello",
                },
            }
        )

        async def handler(event, data):
            captured_context.update(get_request_context())
            return "ok"

        result = await middleware(handler, update, {"user": db_user})

        self.assertEqual(result, "ok")
        self.assertEqual(captured_context["bot_update_id"], "987654321")
        self.assertTrue(captured_context["telegram_user_id"].startswith("tg:"))
        self.assertNotEqual(captured_context["telegram_user_id"], "12345")


if __name__ == "__main__":
    unittest.main()
