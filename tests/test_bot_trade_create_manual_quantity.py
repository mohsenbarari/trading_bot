import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_create import Trade, handle_manual_quantity


class BotTradeCreateManualQuantityTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_manual_quantity_handles_invalid_min_max_and_success(self):
        state = SimpleNamespace(get_data=AsyncMock(return_value={"trade_type_fa": "🟢 خرید", "commodity_name": "سکه"}), update_data=AsyncMock(), set_state=AsyncMock())

        message = SimpleNamespace(text="abc", answer=AsyncMock())
        with patch("core.trading_settings.get_trading_settings", return_value=SimpleNamespace(offer_min_quantity=5, offer_max_quantity=50)):
            await handle_manual_quantity(message, state, user=SimpleNamespace(id=1))
        self.assertIn("عدد صحیح مثبت", message.answer.await_args.args[0])

        message = SimpleNamespace(text="3", answer=AsyncMock())
        with patch("core.trading_settings.get_trading_settings", return_value=SimpleNamespace(offer_min_quantity=5, offer_max_quantity=50)):
            await handle_manual_quantity(message, state, user=SimpleNamespace(id=1))
        self.assertIn("حداقل تعداد", message.answer.await_args.args[0])

        message = SimpleNamespace(text="60", answer=AsyncMock())
        with patch("core.trading_settings.get_trading_settings", return_value=SimpleNamespace(offer_min_quantity=5, offer_max_quantity=50)):
            await handle_manual_quantity(message, state, user=SimpleNamespace(id=1))
        self.assertIn("حداکثر تعداد", message.answer.await_args.args[0])

        message = SimpleNamespace(text="15", answer=AsyncMock())
        with patch("core.trading_settings.get_trading_settings", return_value=SimpleNamespace(offer_min_quantity=5, offer_max_quantity=50)), patch(
            "bot.handlers.trade_create.get_lot_type_keyboard", return_value="LK"
        ):
            await handle_manual_quantity(message, state, user=SimpleNamespace(id=1))
        state.update_data.assert_awaited_once_with(quantity=15)
        state.set_state.assert_awaited_once_with(Trade.awaiting_lot_type)
        self.assertEqual(message.answer.await_args.kwargs["reply_markup"], "LK")


if __name__ == "__main__":
    unittest.main()