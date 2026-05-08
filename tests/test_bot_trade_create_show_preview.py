import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_create import show_trade_preview


class BotTradeCreateShowPreviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_show_trade_preview_answers_and_edits_with_confirm_keyboard(self):
        state = SimpleNamespace(
            get_data=AsyncMock(
                return_value={
                    "trade_type": "sell",
                    "commodity_name": "سکه",
                    "quantity": 15,
                    "price": 123456,
                    "notes": "فقط نقدی",
                }
            )
        )
        message = SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock())

        with patch("bot.handlers.trade_create.get_confirm_keyboard", return_value="CK"):
            await show_trade_preview(message, state, edit=False)
        preview_text = message.answer.await_args.args[0]
        self.assertIn("🔴فروش سکه 15 عدد 123,456", preview_text)
        self.assertIn("توضیحات: فقط نقدی", preview_text)
        self.assertEqual(message.answer.await_args.kwargs["reply_markup"], "CK")

        message = SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock())
        with patch("bot.handlers.trade_create.get_confirm_keyboard", return_value="CK"):
            await show_trade_preview(message, state, edit=True)
        self.assertEqual(message.edit_text.await_args.kwargs["reply_markup"], "CK")


if __name__ == "__main__":
    unittest.main()