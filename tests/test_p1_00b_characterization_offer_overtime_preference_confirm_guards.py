"""Hermetic P1-00b characterization tests for offer overtime preference confirm guards.

Canonical seed: BOT-OFFER-CALLBACK-QUERY-OFFER-OVERTIME-PREFERENCE-CONFIRM-OFFER-OVERTIME-PREFERENCE-L191
Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

This module directly characterizes the three pre-persistence guard branches of
confirm_offer_overtime_preference:
  (A) Eligibility denial
  (B) Missing pending minutes in FSM state
  (C) Invalid pending minutes raising OfferOvertimePreferenceError

This unit is partial seed evidence and does not close the seed blocker,
inventory, or Stage P1-00b.
"""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import unittest
from unittest import mock

from bot.handlers.offer_overtime_preference import confirm_offer_overtime_preference
from core.offer_overtime_bot_copy import M8_INVALID_OVERTIME_VALUE_MESSAGE
from core.services.offer_overtime_preference_service import OfferOvertimePreferenceError


class TestOfferOvertimePreferenceConfirmGuardsCharacterization(
    unittest.IsolatedAsyncioTestCase
):
    async def test_confirm_guards_eligibility_denied_clears_state_and_alerts_reason(
        self,
    ):
        callback = mock.NonCallableMock(name="CallbackQueryFixture")
        user = mock.NonCallableMock(name="UserFixture", spec=["id", "offer_overtime_minutes"])
        user.id = 101
        user.offer_overtime_minutes = 3
        denied_reason_sentinel = "__access_denied_distinct_reason_sentinel__"
        observed_order: list[str] = []

        async def fake_allowed(u):
            observed_order.append("eligibility")
            return False, denied_reason_sentinel

        async def fake_clear():
            observed_order.append("state.clear")
            return None

        async def fake_answer_callback(cb, *args, **kwargs):
            observed_order.append("answer_callback_query")
            return None

        state = mock.AsyncMock()
        state.clear.side_effect = fake_clear
        state.get_data.side_effect = AssertionError(
            "state.get_data must not be called when eligibility is denied"
        )

        with mock.patch(
            "bot.handlers.offer_overtime_preference._overtime_preference_allowed",
            new_callable=mock.AsyncMock,
            side_effect=fake_allowed,
        ) as mock_allowed, mock.patch(
            "bot.handlers.offer_overtime_preference.answer_callback_query_via_runtime",
            new_callable=mock.AsyncMock,
            side_effect=fake_answer_callback,
        ) as mock_answer_callback, mock.patch(
            "bot.handlers.offer_overtime_preference.normalize_overtime_minutes",
            side_effect=AssertionError(
                "normalize_overtime_minutes must not be called when eligibility is denied"
            ),
        ) as mock_normalize, mock.patch(
            "bot.handlers.offer_overtime_preference.AsyncSessionLocal",
            side_effect=AssertionError(
                "AsyncSessionLocal must not be opened when eligibility is denied"
            ),
        ) as mock_session_local, mock.patch(
            "bot.handlers.offer_overtime_preference.save_overtime_preference_from_bot",
            new_callable=mock.AsyncMock,
            side_effect=AssertionError(
                "save_overtime_preference_from_bot must not be called when eligibility is denied"
            ),
        ) as mock_save, mock.patch(
            "bot.handlers.offer_overtime_preference.answer_callback_message_via_runtime",
            new_callable=mock.AsyncMock,
            side_effect=AssertionError(
                "answer_callback_message_via_runtime must not be called when eligibility is denied"
            ),
        ) as mock_answer_message, mock.patch(
            "bot.handlers.offer_overtime_preference._user_panel_reply_markup",
            new_callable=mock.AsyncMock,
            side_effect=AssertionError(
                "_user_panel_reply_markup must not be called when eligibility is denied"
            ),
        ) as mock_panel_markup:
            result = await confirm_offer_overtime_preference(callback, state, user)

        self.assertIsNone(result)
        self.assertEqual(
            observed_order,
            [
                "eligibility",
                "state.clear",
                "answer_callback_query",
            ],
        )

        mock_allowed.assert_awaited_once_with(user)
        state.clear.assert_awaited_once_with()
        mock_answer_callback.assert_awaited_once_with(
            callback,
            denied_reason_sentinel,
            show_alert=True,
        )

        self.assertEqual(user.offer_overtime_minutes, 3)
        state.get_data.assert_not_called()
        mock_normalize.assert_not_called()
        mock_session_local.assert_not_called()
        mock_save.assert_not_called()
        mock_answer_message.assert_not_called()
        mock_panel_markup.assert_not_called()

        self.assertEqual(mock_allowed.mock_calls, [mock.call(user)])
        self.assertEqual(state.mock_calls, [mock.call.clear()])
        self.assertEqual(
            mock_answer_callback.mock_calls,
            [mock.call(callback, denied_reason_sentinel, show_alert=True)],
        )

    async def test_confirm_guards_missing_pending_clears_state_and_alerts_incomplete(
        self,
    ):
        callback = mock.NonCallableMock(name="CallbackQueryFixture")
        user = mock.NonCallableMock(name="UserFixture", spec=["id", "offer_overtime_minutes"])
        user.id = 101
        user.offer_overtime_minutes = 3
        data_without_pending: dict[str, object] = {"other_pending_key": "unrelated"}
        observed_order: list[str] = []

        async def fake_allowed(u):
            observed_order.append("eligibility")
            return True, None

        async def fake_get_data():
            observed_order.append("state.get_data")
            return dict(data_without_pending)

        async def fake_clear():
            observed_order.append("state.clear")
            return None

        async def fake_answer_callback(cb, *args, **kwargs):
            observed_order.append("answer_callback_query")
            return None

        state = mock.AsyncMock()
        state.get_data.side_effect = fake_get_data
        state.clear.side_effect = fake_clear

        with mock.patch(
            "bot.handlers.offer_overtime_preference._overtime_preference_allowed",
            new_callable=mock.AsyncMock,
            side_effect=fake_allowed,
        ) as mock_allowed, mock.patch(
            "bot.handlers.offer_overtime_preference.answer_callback_query_via_runtime",
            new_callable=mock.AsyncMock,
            side_effect=fake_answer_callback,
        ) as mock_answer_callback, mock.patch(
            "bot.handlers.offer_overtime_preference.normalize_overtime_minutes",
            side_effect=AssertionError(
                "normalize_overtime_minutes must not be called when pending minutes is missing"
            ),
        ) as mock_normalize, mock.patch(
            "bot.handlers.offer_overtime_preference.AsyncSessionLocal",
            side_effect=AssertionError(
                "AsyncSessionLocal must not be opened when pending minutes is missing"
            ),
        ) as mock_session_local, mock.patch(
            "bot.handlers.offer_overtime_preference.save_overtime_preference_from_bot",
            new_callable=mock.AsyncMock,
            side_effect=AssertionError(
                "save_overtime_preference_from_bot must not be called when pending minutes is missing"
            ),
        ) as mock_save, mock.patch(
            "bot.handlers.offer_overtime_preference.answer_callback_message_via_runtime",
            new_callable=mock.AsyncMock,
            side_effect=AssertionError(
                "answer_callback_message_via_runtime must not be called when pending minutes is missing"
            ),
        ) as mock_answer_message, mock.patch(
            "bot.handlers.offer_overtime_preference._user_panel_reply_markup",
            new_callable=mock.AsyncMock,
            side_effect=AssertionError(
                "_user_panel_reply_markup must not be called when pending minutes is missing"
            ),
        ) as mock_panel_markup:
            result = await confirm_offer_overtime_preference(callback, state, user)

        self.assertIsNone(result)
        self.assertEqual(
            observed_order,
            [
                "eligibility",
                "state.get_data",
                "state.clear",
                "answer_callback_query",
            ],
        )

        mock_allowed.assert_awaited_once_with(user)
        state.get_data.assert_awaited_once_with()
        state.clear.assert_awaited_once_with()
        mock_answer_callback.assert_awaited_once_with(
            callback,
            "اطلاعات تنظیم ناقص است.",
            show_alert=True,
        )

        self.assertEqual(user.offer_overtime_minutes, 3)
        mock_normalize.assert_not_called()
        mock_session_local.assert_not_called()
        mock_save.assert_not_called()
        mock_answer_message.assert_not_called()
        mock_panel_markup.assert_not_called()

        self.assertEqual(mock_allowed.mock_calls, [mock.call(user)])
        self.assertEqual(state.mock_calls, [mock.call.get_data(), mock.call.clear()])
        self.assertEqual(
            mock_answer_callback.mock_calls,
            [mock.call(callback, "اطلاعات تنظیم ناقص است.", show_alert=True)],
        )

    async def test_confirm_guards_invalid_pending_clears_state_and_alerts_m8(
        self,
    ):
        callback = mock.NonCallableMock(name="CallbackQueryFixture")
        user = mock.NonCallableMock(name="UserFixture", spec=["id", "offer_overtime_minutes"])
        user.id = 101
        user.offer_overtime_minutes = 3
        pending_sentinel = "__invalid_pending_minutes_sentinel__"
        observed_order: list[str] = []

        async def fake_allowed(u):
            observed_order.append("eligibility")
            return True, None

        async def fake_get_data():
            observed_order.append("state.get_data")
            return {"overtime_preference_pending_minutes": pending_sentinel}

        def fake_normalize(val):
            observed_order.append("normalization")
            raise OfferOvertimePreferenceError()

        async def fake_clear():
            observed_order.append("state.clear")
            return None

        async def fake_answer_callback(cb, *args, **kwargs):
            observed_order.append("answer_callback_query")
            return None

        state = mock.AsyncMock()
        state.get_data.side_effect = fake_get_data
        state.clear.side_effect = fake_clear

        with mock.patch(
            "bot.handlers.offer_overtime_preference._overtime_preference_allowed",
            new_callable=mock.AsyncMock,
            side_effect=fake_allowed,
        ) as mock_allowed, mock.patch(
            "bot.handlers.offer_overtime_preference.answer_callback_query_via_runtime",
            new_callable=mock.AsyncMock,
            side_effect=fake_answer_callback,
        ) as mock_answer_callback, mock.patch(
            "bot.handlers.offer_overtime_preference.normalize_overtime_minutes",
            side_effect=fake_normalize,
        ) as mock_normalize, mock.patch(
            "bot.handlers.offer_overtime_preference.AsyncSessionLocal",
            side_effect=AssertionError(
                "AsyncSessionLocal must not be opened when normalization fails"
            ),
        ) as mock_session_local, mock.patch(
            "bot.handlers.offer_overtime_preference.save_overtime_preference_from_bot",
            new_callable=mock.AsyncMock,
            side_effect=AssertionError(
                "save_overtime_preference_from_bot must not be called when normalization fails"
            ),
        ) as mock_save, mock.patch(
            "bot.handlers.offer_overtime_preference.answer_callback_message_via_runtime",
            new_callable=mock.AsyncMock,
            side_effect=AssertionError(
                "answer_callback_message_via_runtime must not be called when normalization fails"
            ),
        ) as mock_answer_message, mock.patch(
            "bot.handlers.offer_overtime_preference._user_panel_reply_markup",
            new_callable=mock.AsyncMock,
            side_effect=AssertionError(
                "_user_panel_reply_markup must not be called when normalization fails"
            ),
        ) as mock_panel_markup:
            result = await confirm_offer_overtime_preference(callback, state, user)

        self.assertIsNone(result)
        self.assertEqual(
            observed_order,
            [
                "eligibility",
                "state.get_data",
                "normalization",
                "state.clear",
                "answer_callback_query",
            ],
        )

        mock_allowed.assert_awaited_once_with(user)
        state.get_data.assert_awaited_once_with()
        mock_normalize.assert_called_once_with(pending_sentinel)
        state.clear.assert_awaited_once_with()
        mock_answer_callback.assert_awaited_once_with(
            callback,
            M8_INVALID_OVERTIME_VALUE_MESSAGE,
            show_alert=True,
        )

        self.assertEqual(user.offer_overtime_minutes, 3)
        mock_session_local.assert_not_called()
        mock_save.assert_not_called()
        mock_answer_message.assert_not_called()
        mock_panel_markup.assert_not_called()

        self.assertEqual(mock_allowed.mock_calls, [mock.call(user)])
        self.assertEqual(state.mock_calls, [mock.call.get_data(), mock.call.clear()])
        self.assertEqual(mock_normalize.mock_calls, [mock.call(pending_sentinel)])
        self.assertEqual(
            mock_answer_callback.mock_calls,
            [mock.call(callback, M8_INVALID_OVERTIME_VALUE_MESSAGE, show_alert=True)],
        )


if __name__ == "__main__":
    unittest.main()
