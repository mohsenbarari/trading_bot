import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.panel import handle_back_to_main_menu


class BotPanelBackToMainTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_back_to_main_menu_requires_user_and_restores_persistent_menu(self):
        message = SimpleNamespace(bot=SimpleNamespace(), chat=SimpleNamespace(id=10), answer=AsyncMock())
        await handle_back_to_main_menu(message, state=SimpleNamespace(), user=None)
        message.answer.assert_not_awaited()

        message = SimpleNamespace(bot=SimpleNamespace(), chat=SimpleNamespace(id=10), answer=AsyncMock(return_value=SimpleNamespace(message_id=71)))
        user = SimpleNamespace(role="standard")
        with patch("bot.handlers.panel.delete_previous_anchor", new=AsyncMock()) as delete_anchor, patch(
            "bot.handlers.panel.get_persistent_menu_keyboard", return_value="KB"
        ), patch("bot.handlers.panel.set_anchor") as set_anchor, patch(
            "bot.handlers.panel.settings", SimpleNamespace(frontend_url="https://app")
        ):
            await handle_back_to_main_menu(message, state=SimpleNamespace(), user=user)

        delete_anchor.assert_awaited_once()
        self.assertIn("منوی اصلی", message.answer.await_args.args[0])
        set_anchor.assert_called_once_with(10, 71)


if __name__ == "__main__":
    unittest.main()