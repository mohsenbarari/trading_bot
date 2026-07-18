import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiogram import types as aiogram_types

from bot.callbacks import (
    AcceptLotsCallback,
    CommodityCallback,
    LotTypeCallback,
    PageCallback,
    QuantityCallback,
    SkipNotesCallback,
    TextOfferActionCallback,
    TradeActionCallback,
    TradeSettlementCallback,
    TradeTypeCallback,
    TradeWizardActionCallback,
    TradeWizardEditCallback,
)
from bot.handlers.trade_create import (
    _TEXT_OFFER_EXISTING_HANDOFF_STATES,
    _TEXT_OFFER_RECOVERY_STATES,
    _disable_pending_text_offer_confirmation,
    _reject_stale_text_offer_callback,
    handle_stale_trade_creation_callback,
    handle_text_offer,
    handle_text_offer_from_stale_trade_state,
    handle_text_offer_while_confirmation_pending,
    handle_unexpected_trade_builder_message,
    handle_lot_sizes_input,
    handle_manual_quantity,
    handle_notes_input,
    handle_price_input,
)
from bot.handlers import trade_create
from bot.states import AdminBroadcast, Trade


class BotTradeCreateStaleStateTextOfferTests(unittest.IsolatedAsyncioTestCase):
    async def test_every_trade_state_has_a_text_offer_recovery_path(self):
        covered_states = {
            state.state
            for state in (
                *_TEXT_OFFER_EXISTING_HANDOFF_STATES,
                *_TEXT_OFFER_RECOVERY_STATES,
                Trade.awaiting_text_confirm,
            )
        }

        self.assertEqual(
            {state.state for state in Trade.__all_states__},
            covered_states,
        )

    async def test_text_offer_router_does_not_consume_broadcast_message_state(self):
        handler = next(
            item for item in trade_create.router.message.handlers
            if item.callback is handle_text_offer
        )
        message = SimpleNamespace(text="خرید امام 20 عدد نقد حاضر 176000")

        idle_match, _ = await handler.check(message, raw_state=None)
        broadcast_match, _ = await handler.check(
            message,
            raw_state=AdminBroadcast.awaiting_message_text,
        )

        self.assertTrue(idle_match)
        self.assertFalse(broadcast_match)

    async def test_pending_confirmation_router_accepts_replacement_offer_text(self):
        handler = next(
            item for item in trade_create.router.message.handlers
            if item.callback is handle_text_offer_while_confirmation_pending
        )
        message = SimpleNamespace(text="خرید امام 20 عدد نقد حاضر 176000")

        pending_match, _ = await handler.check(
            message,
            raw_state=Trade.awaiting_text_confirm.state,
        )
        idle_match, _ = await handler.check(message, raw_state=None)

        self.assertTrue(pending_match)
        self.assertFalse(idle_match)

    async def test_recovery_router_accepts_offer_text_only_in_missing_trade_states(self):
        handler = next(
            item for item in trade_create.router.message.handlers
            if item.callback is handle_text_offer_from_stale_trade_state
        )
        message = SimpleNamespace(text="خرید امام 20 عدد نقد حاضر 176000")

        for trade_state in _TEXT_OFFER_RECOVERY_STATES:
            matched, _ = await handler.check(message, raw_state=trade_state.state)
            self.assertTrue(matched, trade_state.state)

        for excluded_state in (
            None,
            AdminBroadcast.awaiting_message_text.state,
            Trade.awaiting_quantity.state,
            Trade.awaiting_text_confirm.state,
        ):
            matched, _ = await handler.check(message, raw_state=excluded_state)
            self.assertFalse(matched, excluded_state)

    async def test_stale_trade_state_is_cleared_before_normal_text_offer_flow(self):
        bot = SimpleNamespace(name="bot")
        message = SimpleNamespace(text="خ امام 12تا 176000", bot=bot)
        state = SimpleNamespace(
            get_state=AsyncMock(return_value=Trade.awaiting_commodity.state),
            clear=AsyncMock(),
        )
        user = SimpleNamespace(id=276)

        with patch("bot.handlers.trade_create.handle_text_offer", new=AsyncMock()) as text_offer:
            await handle_text_offer_from_stale_trade_state(message, state, user, bot)

        state.clear.assert_awaited_once_with()
        text_offer.assert_awaited_once_with(message, state, user, bot)

    async def test_unexpected_message_in_callback_only_trade_state_gets_guidance(self):
        message = SimpleNamespace(text="متن نامعتبر", answer=AsyncMock())
        state = SimpleNamespace(
            get_state=AsyncMock(return_value=Trade.awaiting_trade_type.state),
            clear=AsyncMock(),
        )

        await handle_unexpected_trade_builder_message(
            message,
            state,
            user=SimpleNamespace(id=276),
        )

        message.answer.assert_awaited_once()
        self.assertIn("دکمه", message.answer.await_args.args[0])
        state.clear.assert_not_awaited()

    async def test_stale_creation_callback_only_alerts_and_never_mutates_state(self):
        callback = SimpleNamespace(data="trade_type:buy", answer=AsyncMock())
        state = SimpleNamespace(
            get_state=AsyncMock(return_value=None),
            clear=AsyncMock(),
            set_state=AsyncMock(),
        )

        await handle_stale_trade_creation_callback(
            callback,
            state,
            user=SimpleNamespace(id=276),
        )

        callback.answer.assert_awaited_once_with(
            "این دکمه دیگر فعال نیست. ثبت آفر را دوباره شروع کنید.",
            show_alert=True,
        )
        state.clear.assert_not_awaited()
        state.set_state.assert_not_awaited()

    async def test_stale_callback_fallback_is_limited_to_creation_prefixes(self):
        fallback_handlers = [
            item for item in trade_create.router.callback_query.handlers
            if item.callback is handle_stale_trade_creation_callback
        ]
        creation_callbacks = (
            TradeTypeCallback(type="buy").pack(),
            TradeSettlementCallback(type="cash").pack(),
            CommodityCallback(id=1).pack(),
            PageCallback(trade_type="buy", page=1).pack(),
            QuantityCallback(value="10").pack(),
            LotTypeCallback(type="wholesale").pack(),
            AcceptLotsCallback(lots="10_10").pack(),
            TradeActionCallback(action="back_to_type").pack(),
            SkipNotesCallback(target="notes").pack(),
            TextOfferActionCallback(action="confirm").pack(),
            TradeWizardActionCallback(action="continue").pack(),
            TradeWizardEditCallback(field="commodity").pack(),
        )

        self.assertEqual(len(fallback_handlers), len(creation_callbacks))
        for callback_data in creation_callbacks:
            event = aiogram_types.CallbackQuery(
                id="stale-offer-builder",
                from_user=aiogram_types.User(id=276, is_bot=False, first_name="Test"),
                chat_instance="test",
                data=callback_data,
            )
            matches = [
                bool((await handler.check(event, raw_state=None))[0])
                for handler in fallback_handlers
            ]
            self.assertEqual(sum(matches), 1, callback_data)

        channel_trade = aiogram_types.CallbackQuery(
            id="channel-trade",
            from_user=aiogram_types.User(id=276, is_bot=False, first_name="Test"),
            chat_instance="test",
            data="ct2:ofr_public:10",
        )
        channel_matches = [
            bool((await handler.check(channel_trade, raw_state=None))[0])
            for handler in fallback_handlers
        ]
        self.assertFalse(any(channel_matches))

    async def test_replacement_offer_disables_old_preview_and_restarts_text_flow(self):
        bot = SimpleNamespace(edit_message_reply_markup=AsyncMock())
        message = SimpleNamespace(
            text="ف امام 12تا 176000",
            bot=bot,
        )
        state = SimpleNamespace(
            get_data=AsyncMock(return_value={
                "text_offer_confirmation_chat_id": 95989674,
                "text_offer_confirmation_message_id": 41,
            }),
            clear=AsyncMock(),
        )
        user = SimpleNamespace(id=276)

        with patch("bot.handlers.trade_create.handle_text_offer", new=AsyncMock()) as text_offer:
            await handle_text_offer_while_confirmation_pending(message, state, user, bot)

        bot.edit_message_reply_markup.assert_awaited_once_with(
            chat_id=95989674,
            message_id=41,
            reply_markup=None,
        )
        state.clear.assert_awaited_once_with()
        text_offer.assert_awaited_once_with(message, state, user, bot)

    async def test_stale_confirmation_callback_cannot_confirm_current_draft(self):
        state = SimpleNamespace(
            get_data=AsyncMock(return_value={"text_offer_confirmation_message_id": 42})
        )
        stale_callback = SimpleNamespace(
            message=SimpleNamespace(message_id=41),
            answer=AsyncMock(),
        )
        current_callback = SimpleNamespace(
            message=SimpleNamespace(message_id=42),
            answer=AsyncMock(),
        )

        self.assertTrue(await _reject_stale_text_offer_callback(stale_callback, state))
        stale_callback.answer.assert_awaited_once_with(
            "این پیش\u200cنمایش قدیمی است.",
            show_alert=True,
        )
        self.assertFalse(await _reject_stale_text_offer_callback(current_callback, state))
        current_callback.answer.assert_not_awaited()

    async def test_disable_pending_confirmation_is_noop_without_stored_message(self):
        bot = SimpleNamespace(edit_message_reply_markup=AsyncMock())
        state = SimpleNamespace(get_data=AsyncMock(return_value={}))

        await _disable_pending_text_offer_confirmation(state, bot)

        bot.edit_message_reply_markup.assert_not_awaited()

    async def test_navigation_button_handoffs_before_quantity_validation(self):
        user = SimpleNamespace(id=1)
        state = SimpleNamespace(clear=AsyncMock(), get_data=AsyncMock(return_value={"quantity": 30}))

        with patch("bot.handlers.panel.handoff_navigation_button", new=AsyncMock(return_value=True)) as nav_handoff:
            message = SimpleNamespace(text="👤 پنل کاربر", answer=AsyncMock())
            await handle_manual_quantity(message, state, user=user)

        nav_handoff.assert_awaited_once_with(message, state, user)
        message.answer.assert_not_awaited()

    async def test_offer_like_text_handoffs_from_stale_wizard_states(self):
        user = SimpleNamespace(id=1)

        state = SimpleNamespace(clear=AsyncMock(), get_data=AsyncMock(return_value={"quantity": 30}))
        with patch("bot.handlers.trade_create.handle_text_offer", new=AsyncMock()) as handoff_mock:
            message = SimpleNamespace(text="خ امام 30تا 75800", answer=AsyncMock())
            await handle_manual_quantity(message, state, user=user)
            state.clear.assert_awaited_once_with()
            handoff_mock.assert_awaited_once_with(message, state, user, None)
            message.answer.assert_not_awaited()

        state = SimpleNamespace(clear=AsyncMock(), get_data=AsyncMock(return_value={"quantity": 30}))
        with patch("bot.handlers.trade_create.handle_text_offer", new=AsyncMock()) as handoff_mock:
            message = SimpleNamespace(text="ف ربع 20تا 765000", answer=AsyncMock())
            await handle_lot_sizes_input(message, state, user=user)
            state.clear.assert_awaited_once_with()
            handoff_mock.assert_awaited_once_with(message, state, user, None)
            message.answer.assert_not_awaited()

        bot = SimpleNamespace(name="bot")
        state = SimpleNamespace(clear=AsyncMock())
        with patch("bot.handlers.trade_create.handle_text_offer", new=AsyncMock()) as handoff_mock:
            message = SimpleNamespace(text="خ نیم 12تا 123456", answer=AsyncMock())
            await handle_price_input(message, state, user=user, bot=bot)
            state.clear.assert_awaited_once_with()
            handoff_mock.assert_awaited_once_with(message, state, user, bot)
            message.answer.assert_not_awaited()

        state = SimpleNamespace(clear=AsyncMock())
        with patch("bot.handlers.trade_create.handle_text_offer", new=AsyncMock()) as handoff_mock:
            message = SimpleNamespace(text="ف سکه 15تا 123456", answer=AsyncMock())
            await handle_notes_input(message, state, user=user)
            state.clear.assert_awaited_once_with()
            handoff_mock.assert_awaited_once_with(message, state, user, None)
            message.answer.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
