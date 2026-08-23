import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.default import (
    STALE_PANEL_BUTTON_MESSAGE,
    acknowledge_noop_callback,
    handle_unauthorized_messages,
    handle_unmatched_callback,
)


class BotDefaultUnauthorizedTests(unittest.IsolatedAsyncioTestCase):
    async def test_noop_callback_is_acknowledged_without_visible_copy(self):
        callback = SimpleNamespace(answer=AsyncMock())

        await acknowledge_noop_callback(callback)

        callback.answer.assert_awaited_once_with()

    async def test_unmatched_callback_explains_that_stale_button_is_inactive(self):
        callback = SimpleNamespace(data="old_panel:action", answer=AsyncMock())

        await handle_unmatched_callback(callback, user=SimpleNamespace(id=5))

        callback.answer.assert_awaited_once_with(
            STALE_PANEL_BUTTON_MESSAGE,
            show_alert=True,
        )

    async def test_handle_unauthorized_messages_ignores_deleted_telegram_users(self):
        message = SimpleNamespace(from_user=SimpleNamespace(id=10), answer=AsyncMock())

        with patch("bot.handlers.default.is_deleted_telegram_user", new=AsyncMock(return_value=True)):
            await handle_unauthorized_messages(message, user=None)

        message.answer.assert_not_awaited()

    async def test_handle_unauthorized_messages_warns_only_for_missing_user(self):
        message = SimpleNamespace(from_user=SimpleNamespace(id=10), answer=AsyncMock())

        with patch("bot.handlers.default.is_deleted_telegram_user", new=AsyncMock(return_value=False)):
            await handle_unauthorized_messages(message, user=None)

        self.assertIn("فعال نشده", message.answer.await_args.args[0])
        self.assertIn("/link", message.answer.await_args.args[0])
        self.assertIn("لینک دعوت", message.answer.await_args.args[0])

        message = SimpleNamespace(from_user=SimpleNamespace(id=10), answer=AsyncMock())
        await handle_unauthorized_messages(message, user=SimpleNamespace(id=5))
        message.answer.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
