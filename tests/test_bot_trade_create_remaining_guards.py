import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_create import (
    _get_offer_suggestion,
    handle_back_to_type,
    handle_commodity_page,
    handle_commodity_selection,
    handle_lot_sizes_input,
    handle_lot_split,
    handle_lot_wholesale,
    handle_manual_quantity,
    handle_notes_input,
    handle_quick_quantity,
    handle_skip_notes,
    handle_trade_cancel,
    handle_trade_type_selection,
    handle_trade_warning_confirm,
)


class BotTradeCreateRemainingGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_core_manual_flow_handlers_cover_missing_user_and_invalid_branches(self):
        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock()), answer=AsyncMock())

        await handle_trade_type_selection(callback, state=SimpleNamespace(update_data=AsyncMock()), user=None, callback_data=SimpleNamespace(type="buy"))
        callback.message.edit_text.assert_not_awaited()

        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        await handle_commodity_page(callback, state=SimpleNamespace(), user=None, callback_data=SimpleNamespace(trade_type="buy", page=2))
        callback.answer.assert_not_awaited()

        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        await handle_commodity_selection(callback, state=SimpleNamespace(), user=None, callback_data=SimpleNamespace(id=7))
        callback.answer.assert_not_awaited()

        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock(), answer=AsyncMock()), answer=AsyncMock())
        await handle_quick_quantity(callback, state=SimpleNamespace(get_data=AsyncMock(), update_data=AsyncMock(), set_state=AsyncMock()), user=None, callback_data=SimpleNamespace(value="10"))
        callback.answer.assert_not_awaited()

        message = SimpleNamespace(text="10", answer=AsyncMock())
        await handle_manual_quantity(message, state=SimpleNamespace(), user=None)
        message.answer.assert_not_awaited()

        invalid_message = SimpleNamespace(text="0", answer=AsyncMock())
        await handle_manual_quantity(invalid_message, state=SimpleNamespace(), user=SimpleNamespace(id=1))
        invalid_message.answer.assert_awaited_once_with("❌ لطفاً یک عدد صحیح مثبت وارد کنید.")

        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        await handle_lot_wholesale(callback, state=SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock()), user=None)
        callback.answer.assert_awaited_once()

        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        await handle_lot_split(callback, state=SimpleNamespace(get_data=AsyncMock(), update_data=AsyncMock(), set_state=AsyncMock()), user=None)
        callback.answer.assert_awaited_once()

        message = SimpleNamespace(text="10 20", answer=AsyncMock())
        await handle_lot_sizes_input(message, state=SimpleNamespace(get_data=AsyncMock(return_value={"quantity": 30})), user=None)
        message.answer.assert_not_awaited()

        invalid_lots_message = SimpleNamespace(text="   ", answer=AsyncMock())
        await handle_lot_sizes_input(invalid_lots_message, state=SimpleNamespace(get_data=AsyncMock(return_value={"quantity": 30})), user=SimpleNamespace(id=1))
        invalid_lots_message.answer.assert_awaited_once_with("❌ لطفاً اعداد را با فاصله وارد کنید (مثال: 10 15 25)")

        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        await handle_skip_notes(callback, state=SimpleNamespace(update_data=AsyncMock()), user=None)
        callback.answer.assert_awaited_once()

        notes_message = SimpleNamespace(text="hi", answer=AsyncMock())
        await handle_notes_input(notes_message, state=SimpleNamespace(), user=None)
        notes_message.answer.assert_not_awaited()

    async def test_navigation_and_warning_confirm_guards_cover_remaining_callback_paths(self):
        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        state = SimpleNamespace(clear=AsyncMock())

        await handle_back_to_type(callback, state=state, user=None)
        callback.answer.assert_awaited_once()
        state.clear.assert_not_awaited()

        callback = SimpleNamespace(message=SimpleNamespace(edit_text=AsyncMock()), answer=AsyncMock())
        state = SimpleNamespace(clear=AsyncMock())
        await handle_trade_cancel(callback, state=state, user=None)
        callback.answer.assert_awaited_once()
        state.clear.assert_not_awaited()

        callback = SimpleNamespace()
        with patch("bot.handlers.trade_create._handle_trade_confirm_core", new=AsyncMock()) as core_mock:
            await handle_trade_warning_confirm(callback, state=SimpleNamespace(), user=SimpleNamespace(id=1), bot=SimpleNamespace())
        core_mock.assert_awaited_once()
        self.assertTrue(core_mock.await_args.kwargs["warning_acknowledged"])


class BotTradeCreateSuggestionHelperTests(unittest.TestCase):
    def test_get_offer_suggestion_covers_remaining_contextual_hint_branches(self):
        multiple_price_hint = _get_offer_suggestion("خ ربع 30تا 75800 75900", "چندین قیمت وارد شده است")
        self.assertIn("فقط یک عدد 5 یا 6 رقمی", multiple_price_hint)

        single_price_hint = _get_offer_suggestion("خ ربع 30تا 1234", "قیمت نامعتبر است")
        self.assertIn("قیمت باید 5 یا 6 رقم باشد", single_price_hint)

        trade_indicator_hint = _get_offer_suggestion("خرید فروش ربع 30تا 75800", "خرید و فروش همزمان مجاز نیست")
        self.assertIn("نوع معامله و تسویه باید یک بلوک کامل و فقط یک بار باشند", trade_indicator_hint)
        self.assertIn("جای بلوک آزاد است", trade_indicator_hint)

        invalid_prefix_hint = _get_offer_suggestion("خ امام 30تا 75800", "نوع معامله و تسویه نامعتبر است")
        self.assertIn("نقد حاضر: `خ ن` یا `ف ن`", invalid_prefix_hint)
        self.assertIn("فردایی: `خ ن ف` یا `ف ن ف`", invalid_prefix_hint)

        split_hint = _get_offer_suggestion("خ 30تا 75800 10 10 10 5", "جمع بخش‌ها نامعتبر است")
        self.assertIn("حداکثر 3 بخش", split_hint)

        character_hint = _get_offer_suggestion("خ ربع 30تا 75800 @", "کاراکتر نامعتبر است")
        self.assertIn("از علائم خاص استفاده نکنید", character_hint)

        default_hint = _get_offer_suggestion("something else", "خطای ناشناخته")
        self.assertIn("نمونه‌های صحیح", default_hint)
        self.assertIn("خ ن ربع 30تا 75800", default_hint)


if __name__ == "__main__":
    unittest.main()
