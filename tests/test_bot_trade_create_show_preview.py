import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_create import Trade, show_trade_preview


class BotTradeCreateShowPreviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_show_trade_preview_answers_and_edits_with_confirm_keyboard(self):
        state = SimpleNamespace(
            get_data=AsyncMock(
                return_value={
                    "trade_type": "sell",
                    "settlement_type": "tomorrow",
                    "commodity_name": "سکه",
                    "quantity": 15,
                    "price": 123456,
                    "is_wholesale": True,
                    "notes": "فقط نقدی",
                }
            ),
            update_data=AsyncMock(),
            set_state=AsyncMock(),
        )
        message = SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock())

        with patch("bot.handlers.trade_create.get_wizard_review_keyboard", return_value="WK"):
            await show_trade_preview(message, state, edit=False)
        preview_text = message.answer.await_args.args[0]
        self.assertIn("ف ن ف سکه 15 عدد 123456: فقط نقدی", preview_text)
        self.assertEqual(message.answer.await_args.kwargs["reply_markup"], "WK")
        state.set_state.assert_awaited_with(Trade.awaiting_wizard_review)

        message = SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock())
        with patch("bot.handlers.trade_create.get_wizard_review_keyboard", return_value="WK"):
            await show_trade_preview(message, state, edit=True)
        self.assertEqual(message.edit_text.await_args.kwargs["reply_markup"], "WK")


if __name__ == "__main__":
    unittest.main()
