import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from bot.handlers.trade_create import (
    handle_lot_sizes_input,
    handle_manual_quantity,
    handle_notes_input,
    handle_price_input,
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
