import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.block_manage import safe_edit_text


class FakeBadRequest(Exception):
    pass


class BotBlockManageSafeEditTests(unittest.IsolatedAsyncioTestCase):
    async def test_safe_edit_text_ignores_not_modified_and_reraises_other_errors(self):
        callback = SimpleNamespace()
        user = SimpleNamespace(id=5)
        with patch(
            "bot.handlers.block_manage.edit_callback_message_via_runtime",
            new=AsyncMock(side_effect=FakeBadRequest("message is not modified")),
        ), patch("bot.handlers.block_manage.TelegramBadRequest", FakeBadRequest):
            await safe_edit_text(
                callback,
                user,
                "TEXT",
                source_key="block-safe-edit-test",
            )

        with patch(
            "bot.handlers.block_manage.edit_callback_message_via_runtime",
            new=AsyncMock(side_effect=FakeBadRequest("other")),
        ), patch("bot.handlers.block_manage.TelegramBadRequest", FakeBadRequest):
            with self.assertRaises(FakeBadRequest):
                await safe_edit_text(
                    callback,
                    user,
                    "TEXT",
                    source_key="block-safe-edit-test",
                )


if __name__ == "__main__":
    unittest.main()
