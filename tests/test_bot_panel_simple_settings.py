import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.handlers.panel import handle_simple_settings_button


class BotPanelSimpleSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_simple_settings_button_requires_user_and_shows_placeholder(self):
        message = SimpleNamespace(answer=AsyncMock())
        await handle_simple_settings_button(message, user=None)
        message.answer.assert_not_awaited()

        await handle_simple_settings_button(message, user=SimpleNamespace(id=5))
        self.assertIn("در حال توسعه", message.answer.await_args.args[0])


if __name__ == "__main__":
    unittest.main()