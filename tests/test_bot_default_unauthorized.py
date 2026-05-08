import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.default import handle_unauthorized_messages


class BotDefaultUnauthorizedTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_unauthorized_messages_ignores_deleted_telegram_users(self):
        message = SimpleNamespace(from_user=SimpleNamespace(id=10), answer=AsyncMock())

        with patch("bot.handlers.default.is_deleted_telegram_user", new=AsyncMock(return_value=True)):
            await handle_unauthorized_messages(message, user=None)

        message.answer.assert_not_awaited()

    async def test_handle_unauthorized_messages_warns_only_for_missing_user(self):
        message = SimpleNamespace(from_user=SimpleNamespace(id=10), answer=AsyncMock())

        with patch("bot.handlers.default.is_deleted_telegram_user", new=AsyncMock(return_value=False)):
            await handle_unauthorized_messages(message, user=None)

        self.assertIn("غیرفعال است", message.answer.await_args.args[0])

        message = SimpleNamespace(from_user=SimpleNamespace(id=10), answer=AsyncMock())
        await handle_unauthorized_messages(message, user=SimpleNamespace(id=5))
        message.answer.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()