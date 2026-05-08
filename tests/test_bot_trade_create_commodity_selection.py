import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.callbacks import CommodityCallback
from bot.handlers.trade_create import Trade, handle_commodity_selection


class FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeSession:
    def __init__(self, commodity):
        self.commodity = commodity

    async def execute(self, stmt):
        return FakeExecuteResult(self.commodity)


class FakeSessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class BotTradeCreateCommoditySelectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_handle_commodity_selection_handles_missing_and_found_commodity(self):
        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        state = SimpleNamespace(get_data=AsyncMock(return_value={"trade_type_fa": "🟢 خرید"}), update_data=AsyncMock(), set_state=AsyncMock())

        with patch("bot.handlers.trade_create.AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(None))):
            await handle_commodity_selection(
                callback,
                state,
                user=SimpleNamespace(id=1),
                callback_data=CommodityCallback(id=7),
            )
        callback.answer.assert_awaited_once_with("❌ کالا یافت نشد!", show_alert=True)

        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        state = SimpleNamespace(get_data=AsyncMock(return_value={"trade_type_fa": "🟢 خرید"}), update_data=AsyncMock(), set_state=AsyncMock())
        commodity = SimpleNamespace(id=7, name="سکه")
        with patch("bot.handlers.trade_create.AsyncSessionLocal", return_value=FakeSessionContext(FakeSession(commodity))), patch(
            "bot.handlers.trade_create.get_quantity_keyboard", return_value="QK"
        ):
            await handle_commodity_selection(
                callback,
                state,
                user=SimpleNamespace(id=1),
                callback_data=CommodityCallback(id=7),
            )
        state.update_data.assert_awaited_once_with(commodity_id=7, commodity_name="سکه")
        state.set_state.assert_awaited_once_with(Trade.awaiting_quantity)
        callback.message.edit_text.assert_awaited_once()
        self.assertEqual(callback.message.edit_text.await_args.kwargs["reply_markup"], "QK")
        callback.answer.assert_awaited_once_with()


if __name__ == "__main__":
    unittest.main()