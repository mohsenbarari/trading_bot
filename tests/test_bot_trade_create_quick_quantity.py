import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.callbacks import QuantityCallback
from bot.handlers.trade_create import Trade, handle_quick_quantity


class BotTradeCreateQuickQuantityTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_quick_quantity_handles_manual_and_numeric_paths(self):
        callback = SimpleNamespace(message=SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock()), answer=AsyncMock())
        state = SimpleNamespace(get_data=AsyncMock(return_value={"trade_type_fa": "🟢 خرید", "commodity_name": "سکه"}), update_data=AsyncMock(), set_state=AsyncMock())

        await handle_quick_quantity(
            callback,
            state,
            user=SimpleNamespace(id=1),
            callback_data=QuantityCallback(value="manual"),
        )
        callback.message.answer.assert_awaited_once()
        callback.answer.assert_awaited_once_with()
        state.update_data.assert_not_awaited()

        callback = SimpleNamespace(message=SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock()), answer=AsyncMock())
        state = SimpleNamespace(get_data=AsyncMock(return_value={"trade_type_fa": "🟢 خرید", "commodity_name": "سکه"}), update_data=AsyncMock(), set_state=AsyncMock())
        with patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    offer_min_quantity=5,
                    offer_max_quantity=100,
                )
            ),
        ), patch("bot.handlers.trade_create.get_lot_type_keyboard", return_value="LK"):
            await handle_quick_quantity(
                callback,
                state,
                user=SimpleNamespace(id=1),
                callback_data=QuantityCallback(value="20"),
            )
        state.update_data.assert_awaited_once_with(quantity=20)
        state.set_state.assert_awaited_once_with(Trade.awaiting_lot_type)
        callback.message.edit_text.assert_awaited_once()
        self.assertEqual(callback.message.edit_text.await_args.kwargs["reply_markup"], "LK")
        callback.answer.assert_awaited_once_with()

    async def test_handle_quick_quantity_rejects_out_of_range_values(self):
        callback = SimpleNamespace(message=SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock()), answer=AsyncMock())
        state = SimpleNamespace(get_data=AsyncMock(return_value={}), update_data=AsyncMock(), set_state=AsyncMock())

        with patch("core.trading_settings.get_trading_settings_async", new=AsyncMock(return_value=SimpleNamespace(offer_min_quantity=5, offer_max_quantity=100))):
            await handle_quick_quantity(
                callback,
                state,
                user=SimpleNamespace(id=1),
                callback_data=QuantityCallback(value="3"),
            )

        callback.answer.assert_awaited_once_with("❌ تعداد مجاز باید بین 5 تا 100 باشد.", show_alert=True)
        state.update_data.assert_not_awaited()
        state.set_state.assert_not_awaited()
        callback.message.edit_text.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
