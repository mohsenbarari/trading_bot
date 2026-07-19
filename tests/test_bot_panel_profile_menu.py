import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.panel import show_my_profile_and_change_keyboard
from core.enums import UserAccountStatus


class BotPanelProfileMenuTests(unittest.IsolatedAsyncioTestCase):
    async def test_show_my_profile_requires_access_and_renders_profile(self):
        message = SimpleNamespace(bot=SimpleNamespace(), chat=SimpleNamespace(id=10), answer=AsyncMock())
        await show_my_profile_and_change_keyboard(message, state=SimpleNamespace(), user=None)
        message.answer.assert_not_awaited()

        inactive_user = SimpleNamespace(
            has_bot_access=True,
            account_status=UserAccountStatus.INACTIVE,
            messenger_blocked_at=object(),
            messenger_grace_expires_at=None,
        )
        await show_my_profile_and_change_keyboard(message, state=SimpleNamespace(), user=inactive_user)
        self.assertIn("غیرفعال", message.answer.await_args.args[0])

        message = SimpleNamespace(bot=SimpleNamespace(), chat=SimpleNamespace(id=10), answer=AsyncMock(return_value=SimpleNamespace(message_id=77)))
        user = SimpleNamespace(
            id=5,
            has_bot_access=False,
            account_status=UserAccountStatus.ACTIVE,
            messenger_blocked_at=None,
            messenger_grace_expires_at=None,
            account_name="acc",
            full_name="Ali",
            telegram_id=111,
            role=SimpleNamespace(value="مدیر ارشد"),
        )
        with patch("bot.handlers.panel.delete_previous_anchor", new=AsyncMock()) as delete_anchor, patch(
            "bot.handlers.panel._can_use_customer_panel",
            new=AsyncMock(return_value=False),
        ), patch(
            "bot.handlers.panel.attach_customer_management_names",
            new=AsyncMock(),
        ), patch(
            "bot.handlers.panel.build_user_panel_navigation_keyboard",
            new=AsyncMock(return_value="KB"),
        ), patch("bot.handlers.panel.set_anchor") as set_anchor, patch(
            "bot.handlers.panel.settings", SimpleNamespace(bot_username="botname")
        ):
            await show_my_profile_and_change_keyboard(message, state=SimpleNamespace(), user=user)

        delete_anchor.assert_awaited_once()
        self.assertIn("پروفایل شما", message.answer.await_args.args[0])
        set_anchor.assert_called_once_with(10, 77)


if __name__ == "__main__":
    unittest.main()
