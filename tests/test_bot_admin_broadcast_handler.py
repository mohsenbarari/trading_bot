import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.handlers import admin_broadcast
from core.enums import UserRole


class BotAdminBroadcastHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_ignores_non_superadmin_without_clearing_state(self):
        message = SimpleNamespace(answer=AsyncMock())
        state = SimpleNamespace(clear=AsyncMock())
        user = SimpleNamespace(role=UserRole.MIDDLE_MANAGER)

        await admin_broadcast.start_telegram_admin_broadcast(message, state, user)

        state.clear.assert_not_awaited()
        message.answer.assert_not_awaited()

    async def test_start_opens_menu_for_superadmin(self):
        message = SimpleNamespace(answer=AsyncMock())
        state = SimpleNamespace(clear=AsyncMock())
        user = SimpleNamespace(role=UserRole.SUPER_ADMIN)

        await admin_broadcast.start_telegram_admin_broadcast(message, state, user)

        state.clear.assert_awaited_once()
        message.answer.assert_awaited_once()
        _args, kwargs = message.answer.await_args
        reply_markup = kwargs.get("reply_markup")
        texts = [button.text for row in reply_markup.inline_keyboard for button in row]
        self.assertIn("ارسال برای همه کاربران بات", texts)
        self.assertIn("ارسال برای کاربران خاص", texts)

    async def test_callback_guard_rejects_forged_non_superadmin_callbacks(self):
        callback = SimpleNamespace(answer=AsyncMock())
        rejected = await admin_broadcast._reject_if_not_superadmin_callback(
            callback,
            SimpleNamespace(role=UserRole.STANDARD),
        )

        self.assertTrue(rejected)
        callback.answer.assert_awaited_once_with("عدم دسترسی", show_alert=True)

    async def test_callback_guard_allows_superadmin_callbacks(self):
        callback = SimpleNamespace(answer=AsyncMock())
        rejected = await admin_broadcast._reject_if_not_superadmin_callback(
            callback,
            SimpleNamespace(role=UserRole.SUPER_ADMIN),
        )

        self.assertFalse(rejected)
        callback.answer.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
