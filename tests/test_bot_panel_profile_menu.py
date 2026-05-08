import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.panel import show_my_profile_and_change_keyboard


class BotPanelProfileMenuTests(unittest.IsolatedAsyncioTestCase):
    async def test_show_my_profile_requires_access_and_renders_profile(self):
        message = SimpleNamespace(bot=SimpleNamespace(), chat=SimpleNamespace(id=10), answer=AsyncMock())
        await show_my_profile_and_change_keyboard(message, state=SimpleNamespace(), user=None)
        message.answer.assert_not_awaited()

        user = SimpleNamespace(has_bot_access=False)
        await show_my_profile_and_change_keyboard(message, state=SimpleNamespace(), user=user)
        self.assertIn("دسترسی لازم", message.answer.await_args.args[0])

        message = SimpleNamespace(bot=SimpleNamespace(), chat=SimpleNamespace(id=10), answer=AsyncMock(return_value=SimpleNamespace(message_id=77)))
        user = SimpleNamespace(
            id=5,
            has_bot_access=True,
            account_name="acc",
            full_name="Ali",
            telegram_id=111,
            role=SimpleNamespace(value="مدیر ارشد"),
        )
        with patch("bot.handlers.panel.delete_previous_anchor", new=AsyncMock()) as delete_anchor, patch(
            "bot.handlers.panel.get_user_panel_keyboard", return_value="KB"
        ), patch("bot.handlers.panel.set_anchor") as set_anchor, patch(
            "bot.handlers.panel.settings", SimpleNamespace(bot_username="botname")
        ):
            await show_my_profile_and_change_keyboard(message, state=SimpleNamespace(), user=user)

        delete_anchor.assert_awaited_once()
        self.assertIn("پروفایل شما", message.answer.await_args.args[0])
        set_anchor.assert_called_once_with(10, 77)


if __name__ == "__main__":
    unittest.main()