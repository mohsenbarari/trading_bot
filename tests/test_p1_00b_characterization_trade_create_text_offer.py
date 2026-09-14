"""Characterization tests for trade_create handle_text_offer entrypoint.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: BOT-OFFER-MESSAGE-TRADE-CREATE-HANDLE-TEXT-OFFER-L3047
Seed: BOT-OFFER-MESSAGE-TRADE-CREATE-HANDLE-TEXT-OFFER-WHILE-CONFIRMATION-PENDING-L3066
Seed: BOT-OFFER-MESSAGE-TRADE-CREATE-HANDLE-TEXT-OFFER-FROM-STALE-TRADE-STATE-L3167
"""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import unittest
from types import SimpleNamespace
from unittest import mock

from bot.handlers.trade_create import (
    handle_text_offer,
    handle_text_offer_from_stale_trade_state,
    handle_text_offer_while_confirmation_pending,
)


class TestTradeCreateTextOfferCharacterization(unittest.IsolatedAsyncioTestCase):
    async def test_unauthenticated_guard(self) -> None:
        message = SimpleNamespace(
            text="فروش آبشده ۱۰ مثقال نقدی",
            message_id=101,
            chat=SimpleNamespace(id=555),
        )
        state = mock.AsyncMock()

        with mock.patch(
            "bot.handlers.trade_create._prepare_text_offer",
            new_callable=mock.AsyncMock,
        ) as mock_prepare:
            result = await handle_text_offer(message, state=state, user=None)

        self.assertIsNone(result)
        self.assertEqual(state.mock_calls, [])
        mock_prepare.assert_not_called()

    async def test_authenticated_non_idle_suppression(self) -> None:
        principal = SimpleNamespace(id=37, telegram_id=777)
        message = SimpleNamespace(
            text="فروش آبشده ۱۰ مثقال نقدی",
            message_id=102,
            chat=SimpleNamespace(id=555),
        )
        state = mock.AsyncMock()
        state.get_state.return_value = "Trade:awaiting_custom_price"

        with mock.patch(
            "bot.handlers.trade_create._prepare_text_offer",
            new_callable=mock.AsyncMock,
        ) as mock_prepare:
            result = await handle_text_offer(message, state=state, user=principal)

        self.assertIsNone(result)
        state.get_state.assert_awaited_once_with()
        self.assertEqual(state.mock_calls, [mock.call.get_state()])
        mock_prepare.assert_not_called()

    async def test_authenticated_idle_delegation_empty_text_normalization(self) -> None:
        principal = SimpleNamespace(id=37, telegram_id=777)
        message = SimpleNamespace(
            text=None,
            message_id=103,
            chat=SimpleNamespace(id=555),
        )
        state = mock.AsyncMock()
        state.get_state.return_value = None

        with mock.patch(
            "bot.handlers.trade_create._prepare_text_offer",
            new_callable=mock.AsyncMock,
        ) as mock_prepare:
            result = await handle_text_offer(message, state=state, user=principal)

        self.assertIsNone(result)
        state.get_state.assert_awaited_once_with()
        self.assertEqual(state.mock_calls, [mock.call.get_state()])
        mock_prepare.assert_awaited_once_with(
            message,
            state,
            principal,
            "",
            edit_response=False,
            wizard_source=False,
        )
        self.assertIs(mock_prepare.await_args.args[0], message)
        self.assertIs(mock_prepare.await_args.args[1], state)
        self.assertIs(mock_prepare.await_args.args[2], principal)
        self.assertEqual(mock_prepare.await_args.args[3], "")
        self.assertIs(mock_prepare.await_args.kwargs["edit_response"], False)
        self.assertIs(mock_prepare.await_args.kwargs["wizard_source"], False)

    async def test_unauthenticated_guard_pending_confirmation_and_stale_recovery(self) -> None:
        class UnreadBotMessage:
            def __init__(self, text: str) -> None:
                self.text = text
                self.message_id = 201
                self.chat = SimpleNamespace(id=555)

            @property
            def bot(self) -> object:
                raise AssertionError("message.bot must not be accessed before unauthenticated guard")

        message_pending = UnreadBotMessage("فروش آبشده ۱۰ مثقال نقدی")
        message_stale = UnreadBotMessage("خرید آبشده ۱۰ مثقال نقدی")
        state_pending = mock.AsyncMock()
        state_stale = mock.AsyncMock()

        with mock.patch(
            "bot.handlers.trade_create._disable_pending_text_offer_confirmation",
            new_callable=mock.AsyncMock,
        ) as mock_disable, mock.patch(
            "bot.handlers.trade_create.handle_text_offer",
            new_callable=mock.AsyncMock,
        ) as mock_handle, mock.patch(
            "bot.handlers.trade_create.logger.info",
        ) as mock_logger_info:
            res_pending = await handle_text_offer_while_confirmation_pending(
                message_pending,
                state=state_pending,
                user=None,
            )
            res_stale = await handle_text_offer_from_stale_trade_state(
                message_stale,
                state=state_stale,
                user=None,
            )

        self.assertIsNone(res_pending)
        self.assertIsNone(res_stale)
        self.assertEqual(state_pending.mock_calls, [])
        self.assertEqual(state_stale.mock_calls, [])
        mock_disable.assert_not_called()
        mock_handle.assert_not_called()
        mock_logger_info.assert_not_called()

    async def test_authenticated_pending_confirmation_bot_fallback_and_ordering(self) -> None:
        bot_sentinel = SimpleNamespace(name="pending_confirmation_bot_sentinel")
        principal = SimpleNamespace(
            id=276,
            account_name="trader_276",
            mobile_number="+989123456789",
            normalized_account_name="trader_276",
            normalized_mobile_number="09123456789",
            full_name="Trader 276",
            address="Tehran, Iran",
            telegram_id=98276,
        )
        message = SimpleNamespace(
            text="فروش آبشده ۱۰ مثقال نقدی",
            message_id=301,
            chat=SimpleNamespace(id=555),
            bot=bot_sentinel,
        )

        call_order: list[str] = []

        async def record_disable(*args: object, **kwargs: object) -> None:
            call_order.append("disable")

        async def record_clear() -> None:
            call_order.append("clear")

        async def record_handle(*args: object, **kwargs: object) -> None:
            call_order.append("delegate")

        state = mock.AsyncMock()
        state.clear.side_effect = record_clear

        with mock.patch(
            "bot.handlers.trade_create._disable_pending_text_offer_confirmation",
            new_callable=mock.AsyncMock,
            side_effect=record_disable,
        ) as mock_disable, mock.patch(
            "bot.handlers.trade_create.handle_text_offer",
            new_callable=mock.AsyncMock,
            side_effect=record_handle,
        ) as mock_handle:
            result = await handle_text_offer_while_confirmation_pending(
                message,
                state=state,
                user=principal,
            )

        self.assertIsNone(result)
        mock_disable.assert_awaited_once_with(
            state,
            bot_sentinel,
            message=message,
            user=principal,
        )
        self.assertIs(mock_disable.await_args.args[0], state)
        self.assertIs(mock_disable.await_args.args[1], bot_sentinel)
        self.assertIs(mock_disable.await_args.kwargs["message"], message)
        self.assertIs(mock_disable.await_args.kwargs["user"], principal)

        state.clear.assert_awaited_once_with()
        self.assertEqual(state.mock_calls, [mock.call.clear()])

        mock_handle.assert_awaited_once_with(
            message,
            state,
            principal,
            bot_sentinel,
        )
        self.assertIs(mock_handle.await_args.args[0], message)
        self.assertIs(mock_handle.await_args.args[1], state)
        self.assertIs(mock_handle.await_args.args[2], principal)
        self.assertIs(mock_handle.await_args.args[3], bot_sentinel)

        self.assertEqual(call_order, ["disable", "clear", "delegate"])

    async def test_authenticated_stale_recovery_bot_fallback_logging_and_ordering(self) -> None:
        bot_sentinel = SimpleNamespace(name="stale_recovery_bot_sentinel")
        principal = SimpleNamespace(
            id=276,
            account_name="trader_276",
            mobile_number="+989123456789",
            normalized_account_name="trader_276",
            normalized_mobile_number="09123456789",
            full_name="Trader 276",
            address="Tehran, Iran",
            telegram_id=98276,
        )
        message = SimpleNamespace(
            text="خرید آبشده ۱۰ مثقال نقدی",
            message_id=401,
            chat=SimpleNamespace(id=555),
            bot=bot_sentinel,
        )
        opaque_previous_state = "Trade:awaiting_custom_recovery_state_276"

        call_order: list[str] = []

        async def record_get_state() -> str:
            call_order.append("get_state")
            return opaque_previous_state

        async def record_clear() -> None:
            call_order.append("clear")

        def record_log(*args: object, **kwargs: object) -> None:
            call_order.append("log")

        async def record_handle(*args: object, **kwargs: object) -> None:
            call_order.append("delegate")

        state = mock.AsyncMock()
        state.get_state.side_effect = record_get_state
        state.clear.side_effect = record_clear

        with mock.patch(
            "bot.handlers.trade_create.handle_text_offer",
            new_callable=mock.AsyncMock,
            side_effect=record_handle,
        ) as mock_handle, mock.patch(
            "bot.handlers.trade_create.logger.info",
            side_effect=record_log,
        ) as mock_logger_info:
            result = await handle_text_offer_from_stale_trade_state(
                message,
                state=state,
                user=principal,
            )

        self.assertIsNone(result)
        state.get_state.assert_awaited_once_with()
        state.clear.assert_awaited_once_with()
        self.assertEqual(state.mock_calls, [mock.call.get_state(), mock.call.clear()])

        mock_logger_info.assert_called_once_with(
            "Recovering text offer from stale trade state",
            extra={
                "event": "telegram.offer_fsm_recovered",
                "previous_state": opaque_previous_state,
                "user_id": 276,
            },
        )

        mock_handle.assert_awaited_once_with(
            message,
            state,
            principal,
            bot_sentinel,
        )
        self.assertIs(mock_handle.await_args.args[0], message)
        self.assertIs(mock_handle.await_args.args[1], state)
        self.assertIs(mock_handle.await_args.args[2], principal)
        self.assertIs(mock_handle.await_args.args[3], bot_sentinel)

        self.assertEqual(call_order, ["get_state", "clear", "log", "delegate"])


if __name__ == "__main__":
    unittest.main()
