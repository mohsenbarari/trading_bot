import unittest
from types import SimpleNamespace

from aiogram.types import TelegramObject

from bot.middlewares.logging_context import BotLoggingContextMiddleware
from core.request_context import get_request_context


class _FakeTelegramUser:
    id = 12345


class _FakeEvent(TelegramObject):
    from_user = _FakeTelegramUser()


class BotLoggingContextMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def test_middleware_sets_context_for_handler_and_clears_afterward(self):
        middleware = BotLoggingContextMiddleware()
        captured_context = {}
        db_user = SimpleNamespace(id=7, role=SimpleNamespace(value="user"))

        async def handler(event, data):
            captured_context.update(get_request_context())
            return "ok"

        result = await middleware(handler, _FakeEvent(), {"user": db_user})

        self.assertEqual(result, "ok")
        self.assertEqual(captured_context["log_class"], "bot")
        self.assertEqual(captured_context["bot_event_type"], "_FakeEvent")
        self.assertEqual(captured_context["telegram_user_id"], 12345)
        self.assertEqual(captured_context["actor_id"], 7)
        self.assertEqual(captured_context["actor_role"], "user")
        self.assertIn("bot_update_id", captured_context)
        self.assertEqual(get_request_context(), {})


if __name__ == "__main__":
    unittest.main()
