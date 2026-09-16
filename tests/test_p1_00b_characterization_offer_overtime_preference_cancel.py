"""Characterization tests for offer overtime preference cancel callback handler."""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import unittest
from unittest import mock

from bot.handlers.offer_overtime_preference import cancel_offer_overtime_preference
from core.telegram_delivery_queue_contract import TelegramFlowExit


class TestOfferOvertimePreferenceCancelCharacterization(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_offer_overtime_preference_identified_user_success(self):
        callback = object()
        user = object()
        reply_markup_sentinel = object()
        observed_order: list[str] = []

        async def fake_clear():
            observed_order.append("state.clear")
            return None

        async def fake_answer_callback_query(*args, **kwargs):
            observed_order.append("callback acknowledgement")
            return None

        async def fake_user_panel_reply_markup(*args, **kwargs):
            observed_order.append("panel-reply-markup resolution")
            return reply_markup_sentinel

        async def fake_answer_callback_message(*args, **kwargs):
            observed_order.append("callback-message delivery")
            return None

        state = mock.AsyncMock()
        state.clear.side_effect = fake_clear

        with mock.patch(
            "bot.handlers.offer_overtime_preference._user_panel_reply_markup",
            new_callable=mock.AsyncMock,
            side_effect=fake_user_panel_reply_markup,
        ) as mock_panel_markup, mock.patch(
            "bot.handlers.offer_overtime_preference.answer_callback_query_via_runtime",
            new_callable=mock.AsyncMock,
            side_effect=fake_answer_callback_query,
        ) as mock_answer_query, mock.patch(
            "bot.handlers.offer_overtime_preference.answer_callback_message_via_runtime",
            new_callable=mock.AsyncMock,
            side_effect=fake_answer_callback_message,
        ) as mock_answer_message:
            result = await cancel_offer_overtime_preference(callback, state, user)

        self.assertIsNone(result)

        self.assertEqual(
            observed_order,
            [
                "state.clear",
                "callback acknowledgement",
                "panel-reply-markup resolution",
                "callback-message delivery",
            ],
        )

        state.clear.assert_awaited_once_with()
        mock_panel_markup.assert_awaited_once_with(user)
        mock_answer_query.assert_awaited_once_with(callback, "لغو شد")
        mock_answer_message.assert_awaited_once_with(
            callback,
            user,
            "تنظیم وقت اضافه لغو شد.",
            source_key="overtime-preference-cancelled",
            reply_markup=reply_markup_sentinel,
            set_persistent_anchor=True,
            flow_exit=TelegramFlowExit.CANCEL,
        )

        self.assertEqual(state.mock_calls, [mock.call.clear()])
        self.assertEqual(mock_panel_markup.mock_calls, [mock.call(user)])
        self.assertEqual(mock_answer_query.mock_calls, [mock.call(callback, "لغو شد")])
        self.assertEqual(
            mock_answer_message.mock_calls,
            [
                mock.call(
                    callback,
                    user,
                    "تنظیم وقت اضافه لغو شد.",
                    source_key="overtime-preference-cancelled",
                    reply_markup=reply_markup_sentinel,
                    set_persistent_anchor=True,
                    flow_exit=TelegramFlowExit.CANCEL,
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
