import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.block_manage import safe_edit_text


class FakeBadRequest(Exception):
    pass


class BotBlockManageSafeEditTests(unittest.IsolatedAsyncioTestCase):
    async def test_safe_edit_text_ignores_not_modified_and_reraises_other_errors(self):
        message = SimpleNamespace(edit_text=AsyncMock(side_effect=FakeBadRequest("message is not modified")))
        with patch("bot.handlers.block_manage.TelegramBadRequest", FakeBadRequest):
            await safe_edit_text(message, "TEXT")

        message = SimpleNamespace(edit_text=AsyncMock(side_effect=FakeBadRequest("other")))
        with patch("bot.handlers.block_manage.TelegramBadRequest", FakeBadRequest):
            with self.assertRaises(FakeBadRequest):
                await safe_edit_text(message, "TEXT")


if __name__ == "__main__":
    unittest.main()