import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from bot.handlers.start import handle_cancel_respond


class BotStartCancelRespondTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_cancel_respond_edits_message_and_answers_callback(self):
        callback = SimpleNamespace(
            answer=AsyncMock(),
            message=SimpleNamespace(edit_text=AsyncMock()),
        )

        await handle_cancel_respond(callback)

        callback.message.edit_text.assert_awaited_once_with("❌ انصراف از معامله.")
        callback.answer.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()