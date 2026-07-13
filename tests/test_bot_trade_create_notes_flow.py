import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_create import handle_notes_input, handle_skip_notes


class BotTradeCreateNotesFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_skip_notes_sets_none_and_shows_preview(self):
        callback = SimpleNamespace(message=SimpleNamespace(), answer=AsyncMock())
        state = SimpleNamespace(update_data=AsyncMock())

        with patch("bot.handlers.trade_create._show_wizard_review", new=AsyncMock()) as preview_mock:
            await handle_skip_notes(callback, state, user=SimpleNamespace(id=1))

        state.update_data.assert_awaited_once_with(notes=None)
        preview_mock.assert_awaited_once_with(callback.message, state, edit=True)
        callback.answer.assert_awaited_once_with()

    async def test_handle_notes_input_handles_too_long_and_success(self):
        state = SimpleNamespace(update_data=AsyncMock())
        long_message = SimpleNamespace(text="x" * 201, answer=AsyncMock())
        await handle_notes_input(long_message, state, user=SimpleNamespace(id=1))
        self.assertIn("بیش از 200 کاراکتر", long_message.answer.await_args.args[0])

        message = SimpleNamespace(text="فقط نقدی", answer=AsyncMock())
        with patch("bot.handlers.trade_create._show_wizard_review", new=AsyncMock()) as preview_mock:
            await handle_notes_input(message, state, user=SimpleNamespace(id=1))
        state.update_data.assert_awaited_once_with(notes="فقط نقدی")
        preview_mock.assert_awaited_once_with(message, state, edit=False)


if __name__ == "__main__":
    unittest.main()
