import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_create import (
    _show_lot_sizes_prompt,
    _show_notes_prompt,
    _show_price_prompt,
    _show_wizard_review,
    handle_cancel_all_offers_bot,
    handle_lot_sizes_input,
    handle_manual_quantity,
    handle_notes_input,
    handle_price_input,
    handle_quick_quantity,
    handle_trade_button,
)
from core.enums import UserRole
from core.telegram_delivery_queue_contract import TelegramDeliveryAction


def _message(text=""):
    return SimpleNamespace(text=text, answer=AsyncMock())


def _assert_offer_validation_calls(testcase, enqueue, expected_source_keys):
    testcase.assertEqual(
        [call.kwargs["source_key"] for call in enqueue.await_args_list],
        expected_source_keys,
    )
    testcase.assertTrue(
        all(
            call.kwargs["action"]
            == TelegramDeliveryAction.OFFER_VALIDATION_RESPONSE
            for call in enqueue.await_args_list
        )
    )


class BotTradeCreateValidationInteractionQueueTests(unittest.IsolatedAsyncioTestCase):
    async def test_wizard_send_prompts_use_offer_control_interaction_lane(self):
        state = SimpleNamespace(
            clear=AsyncMock(),
            get_data=AsyncMock(
                return_value={
                    "trade_type": "buy",
                    "trade_type_fa": "🟢 خرید",
                    "settlement_type": "cash",
                    "commodity_id": 1,
                    "commodity_name": "سکه",
                    "quantity": 10,
                    "price": 12345,
                    "is_wholesale": True,
                    "lot_sizes": None,
                    "notes": None,
                    "wizard_return_to_review": False,
                }
            ),
            update_data=AsyncMock(),
            set_state=AsyncMock(),
        )
        user = SimpleNamespace(
            id=7,
            role=UserRole.STANDARD,
            trading_restricted_until=None,
        )
        message = _message("10")
        callback = SimpleNamespace(
            message=SimpleNamespace(answer=AsyncMock()),
            answer=AsyncMock(),
        )

        with (
            patch(
                "bot.handlers.trade_create.answer_incoming_message_via_runtime",
                new=AsyncMock(),
            ) as enqueue_incoming,
            patch(
                "bot.handlers.trade_create.answer_callback_message_via_runtime",
                new=AsyncMock(),
            ) as enqueue_callback,
            patch(
                "bot.handlers.trade_create.answer_callback_query_via_runtime",
                new=AsyncMock(),
            ),
            patch(
                "bot.handlers.trade_create._bot_trade_access_denial_reason",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "bot.handlers.trade_create._bot_market_is_open",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "bot.handlers.trade_create._handoff_stale_wizard_state_to_text_offer",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "core.trading_settings.get_trading_settings",
                return_value=SimpleNamespace(
                    offer_min_quantity=1,
                    offer_max_quantity=50,
                ),
            ),
        ):
            await handle_trade_button(message, state, user)
            await _show_price_prompt(message, state, edit=False, user=user)
            await _show_lot_sizes_prompt(message, state, edit=False, user=user)
            await _show_notes_prompt(message, state, edit=False, user=user)
            await _show_wizard_review(message, state, edit=False, user=user)
            await handle_manual_quantity(message, state, user)
            await handle_quick_quantity(
                callback,
                state,
                user,
                SimpleNamespace(value="manual"),
            )

        _assert_offer_validation_calls(
            self,
            enqueue_incoming,
            [
                "offer-wizard-start",
                "offer-price-prompt",
                "offer-lot-sizes-prompt",
                "offer-notes-prompt",
                "offer-review-prompt",
                "offer-lot-type-prompt",
            ],
        )
        _assert_offer_validation_calls(
            self,
            enqueue_callback,
            ["offer-manual-quantity-prompt"],
        )
        self.assertTrue(
            all(
                call.kwargs.get("temporary_context_keyboard") is True
                for call in enqueue_incoming.await_args_list
            )
        )
        message.answer.assert_not_awaited()
        callback.message.answer.assert_not_awaited()

    async def test_cancel_all_result_uses_offer_control_interaction_lane(self):
        message = _message("نشد")
        state = SimpleNamespace()
        user = SimpleNamespace(id=7)
        result = SimpleNamespace(
            locally_cancelled_offers=(),
            total_count=0,
            remaining_active_count=None,
        )

        with (
            patch(
                "bot.handlers.trade_create.cancel_all_active_offers_authoritatively",
                new=AsyncMock(return_value=result),
            ),
            patch(
                "bot.handlers.trade_create.answer_incoming_message_via_runtime",
                new=AsyncMock(),
            ) as enqueue,
        ):
            await handle_cancel_all_offers_bot(message, state, user)

        _assert_offer_validation_calls(
            self,
            enqueue,
            ["offer-cancel-all-result"],
        )
        self.assertEqual(enqueue.await_args.args[2], "شما هیچ لفظ فعالی ندارید.")
        message.answer.assert_not_awaited()

    async def test_start_rejections_use_offer_validation_lane(self):
        state = SimpleNamespace(
            clear=AsyncMock(),
            update_data=AsyncMock(),
            set_state=AsyncMock(),
        )
        denied = SimpleNamespace(
            role=UserRole.WATCH,
            trading_restricted_until=None,
        )
        restricted = SimpleNamespace(
            role=UserRole.STANDARD,
            trading_restricted_until=datetime.utcnow() + timedelta(minutes=15),
        )
        closed = SimpleNamespace(
            role=UserRole.STANDARD,
            trading_restricted_until=None,
        )

        with patch(
            "bot.handlers.trade_create.answer_incoming_message_via_runtime",
            new=AsyncMock(),
        ) as enqueue:
            await handle_trade_button(_message(), state, denied)
            with patch(
                "bot.handlers.trade_create.to_jalali_str",
                return_value="1405/04/27 - 12:00",
            ):
                await handle_trade_button(_message(), state, restricted)
            with patch(
                "bot.handlers.trade_create._bot_market_is_open",
                new=AsyncMock(return_value=False),
            ):
                await handle_trade_button(_message(), state, closed)

        _assert_offer_validation_calls(
            self,
            enqueue,
            [
                "offer-start-access-denied",
                "offer-start-restricted",
                "offer-start-market-closed",
            ],
        )
        state.clear.assert_not_awaited()

    async def test_quantity_rejections_use_distinct_idempotent_sources(self):
        state = SimpleNamespace(
            get_data=AsyncMock(),
            update_data=AsyncMock(),
            set_state=AsyncMock(),
        )
        user = SimpleNamespace(id=7)
        settings = SimpleNamespace(offer_min_quantity=5, offer_max_quantity=50)

        with (
            patch(
                "bot.handlers.trade_create._handoff_stale_wizard_state_to_text_offer",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "core.trading_settings.get_trading_settings",
                return_value=settings,
            ),
            patch(
                "bot.handlers.trade_create.answer_incoming_message_via_runtime",
                new=AsyncMock(),
            ) as enqueue,
        ):
            await handle_manual_quantity(_message("abc"), state, user)
            await handle_manual_quantity(_message("3"), state, user)
            await handle_manual_quantity(_message("51"), state, user)

        _assert_offer_validation_calls(
            self,
            enqueue,
            [
                "offer-quantity-invalid",
                "offer-quantity-below-min",
                "offer-quantity-above-max",
            ],
        )
        state.update_data.assert_not_awaited()

    async def test_lot_price_and_notes_rejections_use_offer_validation_lane(self):
        state = SimpleNamespace(
            get_data=AsyncMock(return_value={"quantity": 30}),
            update_data=AsyncMock(),
            set_state=AsyncMock(),
        )
        user = SimpleNamespace(id=7)

        with (
            patch(
                "bot.handlers.trade_create._handoff_stale_wizard_state_to_text_offer",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "bot.handlers.trade_create.answer_incoming_message_via_runtime",
                new=AsyncMock(),
            ) as enqueue,
        ):
            await handle_lot_sizes_input(_message("x y"), state, user)
            with patch(
                "bot.handlers.trade_create.validate_lot_sizes",
                return_value=(False, "خطای لات", [15, 15]),
            ):
                await handle_lot_sizes_input(_message("10 10"), state, user)
            await handle_price_input(
                _message("12ab"),
                state,
                user,
                bot=SimpleNamespace(),
            )
            await handle_notes_input(_message("x" * 201), state, user)

        _assert_offer_validation_calls(
            self,
            enqueue,
            [
                "offer-lots-invalid-format",
                "offer-lots-invalid-allocation",
                "offer-price-invalid",
                "offer-notes-too-long",
            ],
        )
        lot_call = enqueue.await_args_list[1]
        self.assertIsNotNone(lot_call.kwargs["reply_markup"])
        state.update_data.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
