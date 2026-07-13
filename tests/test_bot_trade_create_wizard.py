import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, call, patch

from bot.callbacks import (
    QuantityCallback,
    TradeSettlementCallback,
    TradeWizardEditCallback,
)
from bot.handlers.trade_create import (
    Trade,
    handle_quick_quantity,
    handle_settlement_type_selection,
    handle_wizard_continue,
    handle_wizard_edit,
    handle_wizard_edit_field,
    handle_wizard_return_to_review,
)


class BotTradeCreateWizardTests(unittest.IsolatedAsyncioTestCase):
    async def test_settlement_selection_advances_to_commodity(self):
        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        state = SimpleNamespace(
            get_data=AsyncMock(
                return_value={
                    "trade_type": "sell",
                    "trade_type_fa": "🔴 فروش",
                    "wizard_return_to_review": False,
                }
            ),
            update_data=AsyncMock(),
            set_state=AsyncMock(),
        )

        with patch(
            "bot.handlers.trade_create.get_commodities_keyboard",
            new=AsyncMock(return_value="COMMODITIES"),
        ) as keyboard_mock:
            await handle_settlement_type_selection(
                callback,
                state,
                user=SimpleNamespace(id=1),
                callback_data=TradeSettlementCallback(type="tomorrow"),
            )

        state.update_data.assert_awaited_once_with(settlement_type="tomorrow")
        state.set_state.assert_awaited_once_with(Trade.awaiting_commodity)
        keyboard_mock.assert_awaited_once_with("sell")
        self.assertIn("تسویه: فردایی", callback.message.edit_text.await_args.args[0])
        self.assertEqual(callback.message.edit_text.await_args.kwargs["reply_markup"], "COMMODITIES")

    async def test_invalid_settlement_is_rejected_without_mutating_state(self):
        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        state = SimpleNamespace(get_data=AsyncMock(), update_data=AsyncMock(), set_state=AsyncMock())

        await handle_settlement_type_selection(
            callback,
            state,
            user=SimpleNamespace(id=1),
            callback_data=TradeSettlementCallback(type="invalid"),
        )

        state.get_data.assert_not_awaited()
        state.update_data.assert_not_awaited()
        state.set_state.assert_not_awaited()
        callback.answer.assert_awaited_once_with("انتخاب نامعتبر است.", show_alert=True)

    async def test_retail_quantity_edit_invalidates_previous_lots(self):
        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        state = SimpleNamespace(
            get_data=AsyncMock(
                side_effect=[
                    {
                        "wizard_return_to_review": True,
                        "is_wholesale": False,
                        "quantity": 30,
                        "lot_sizes": [20, 10],
                    },
                    {
                        "wizard_return_to_review": True,
                        "is_wholesale": False,
                        "quantity": 40,
                        "lot_sizes": None,
                    },
                ]
            ),
            update_data=AsyncMock(),
            set_state=AsyncMock(),
        )
        settings = SimpleNamespace(offer_min_quantity=5, offer_max_quantity=50)

        with patch(
            "core.trading_settings.get_trading_settings_async",
            new=AsyncMock(return_value=settings),
        ):
            await handle_quick_quantity(
                callback,
                state,
                user=SimpleNamespace(id=1),
                callback_data=QuantityCallback(value="40"),
            )

        self.assertEqual(
            state.update_data.await_args_list,
            [
                call(quantity=40),
                call(lot_sizes=None, wizard_edit_field="lot_sizes"),
            ],
        )
        state.set_state.assert_awaited_once_with(Trade.awaiting_lot_sizes)
        self.assertIn("جمع باید برابر 40 باشد", callback.message.edit_text.await_args.args[0])

    async def test_edit_menu_exposes_retail_fields_and_routes_settlement_edit(self):
        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        state = SimpleNamespace(
            get_data=AsyncMock(return_value={"is_wholesale": False}),
            set_state=AsyncMock(),
        )

        await handle_wizard_edit(callback, state, user=SimpleNamespace(id=1))

        state.set_state.assert_awaited_once_with(Trade.awaiting_wizard_edit)
        edit_keyboard = callback.message.edit_text.await_args.kwargs["reply_markup"]
        edit_labels = [button.text for row in edit_keyboard.inline_keyboard for button in row]
        self.assertIn("ترکیب بخش‌بندی", edit_labels)
        self.assertIn("توضیحات", edit_labels)

        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        state = SimpleNamespace(
            get_data=AsyncMock(return_value={"is_wholesale": False}),
            update_data=AsyncMock(),
            set_state=AsyncMock(),
        )
        await handle_wizard_edit_field(
            callback,
            state,
            user=SimpleNamespace(id=1),
            callback_data=TradeWizardEditCallback(field="settlement_type"),
        )

        state.update_data.assert_awaited_once_with(
            wizard_return_to_review=True,
            wizard_edit_field="settlement_type",
        )
        state.set_state.assert_awaited_once_with(Trade.awaiting_settlement_type)
        settlement_keyboard = callback.message.edit_text.await_args.kwargs["reply_markup"]
        self.assertEqual(settlement_keyboard.inline_keyboard[-1][0].text, "🔙 بازگشت به خلاصه")

    async def test_continue_delegates_to_text_offer_pipeline(self):
        callback = SimpleNamespace(message=SimpleNamespace(), answer=AsyncMock())
        state = SimpleNamespace(
            get_data=AsyncMock(return_value={"generated_offer_text": "خ ن امام 30 عدد 75800"}),
        )
        user = SimpleNamespace(id=7)

        with patch("bot.handlers.trade_create._prepare_text_offer", new=AsyncMock(return_value=True)) as prepare_mock:
            await handle_wizard_continue(callback, state, user=user)

        prepare_mock.assert_awaited_once_with(
            callback.message,
            state,
            user,
            "خ ن امام 30 عدد 75800",
            edit_response=True,
            wizard_source=True,
        )
        callback.answer.assert_awaited_once_with()

    async def test_stale_review_callback_is_rejected(self):
        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        state = SimpleNamespace(
            get_state=AsyncMock(return_value=None),
            get_data=AsyncMock(),
            update_data=AsyncMock(),
            set_state=AsyncMock(),
        )

        await handle_wizard_return_to_review(callback, state, user=SimpleNamespace(id=1))

        callback.answer.assert_awaited_once_with("این فرآیند دیگر فعال نیست.", show_alert=True)
        state.get_data.assert_not_awaited()
        callback.message.edit_text.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
