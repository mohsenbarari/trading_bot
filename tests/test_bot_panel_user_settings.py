import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.panel import handle_user_settings_button


class FakeSessionContext:
    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class BotPanelUserSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_user_settings_button_renders_block_status_summary(self):
        message = SimpleNamespace(answer=AsyncMock())
        await handle_user_settings_button(message, state=SimpleNamespace(), user=None)
        message.answer.assert_not_awaited()

        status = {"can_block": True, "current_blocked": 1, "max_blocked": 4}
        with patch("core.db.AsyncSessionLocal", return_value=FakeSessionContext()), patch(
            "core.services.block_service.get_block_status", new=AsyncMock(return_value=status)
        ):
            await handle_user_settings_button(message, state=SimpleNamespace(), user=SimpleNamespace(id=5))

        self.assertIn("تنظیمات کاربری", message.answer.await_args.args[0])
        self.assertEqual(message.answer.await_args.kwargs["parse_mode"], "Markdown")


if __name__ == "__main__":
    unittest.main()