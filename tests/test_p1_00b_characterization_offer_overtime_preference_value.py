"""Characterization tests for offer overtime preference value message handler."""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import unittest
from unittest import mock

from bot.handlers.offer_overtime_preference import process_offer_overtime_preference_value
from bot.states import OfferOvertimePreference


class TestOfferOvertimePreferenceValueCharacterization(unittest.IsolatedAsyncioTestCase):
    async def test_process_value_navigation_handoff_true_short_circuits(self):
        message = mock.Mock()
        message.text = "__nav_button_text__"
        user = object()
        state = mock.AsyncMock()

        with mock.patch(
            "bot.handlers.panel.handoff_navigation_button",
            new_callable=mock.AsyncMock,
            return_value=True,
        ) as mock_handoff, mock.patch(
            "bot.handlers.offer_overtime_preference._overtime_preference_allowed",
            new_callable=mock.AsyncMock,
        ) as mock_allowed, mock.patch(
            "bot.handlers.offer_overtime_preference.normalize_overtime_minutes",
        ) as mock_normalize, mock.patch(
            "bot.handlers.offer_overtime_preference.build_overtime_preference_confirm_prompt",
        ) as mock_build_prompt, mock.patch(
            "bot.handlers.offer_overtime_preference._overtime_preference_confirm_keyboard",
        ) as mock_confirm_keyboard, mock.patch(
            "bot.handlers.offer_overtime_preference._overtime_preference_cancel_keyboard",
        ) as mock_cancel_keyboard, mock.patch(
            "bot.handlers.offer_overtime_preference.answer_incoming_message_via_runtime",
            new_callable=mock.AsyncMock,
        ) as mock_answer_message:
            result = await process_offer_overtime_preference_value(message, state, user)

        self.assertIsNone(result)
        mock_handoff.assert_awaited_once_with(message, state, user)
        self.assertEqual(mock_handoff.mock_calls, [mock.call(message, state, user)])

        mock_allowed.assert_not_called()
        mock_normalize.assert_not_called()
        mock_build_prompt.assert_not_called()
        mock_confirm_keyboard.assert_not_called()
        mock_cancel_keyboard.assert_not_called()
        mock_answer_message.assert_not_called()
        self.assertEqual(state.mock_calls, [])

    async def test_process_value_access_denied_clears_state_and_emits_reason(self):
        message = mock.Mock()
        message.text = "__typed_value__"
        user = object()
        denied_reason_sentinel = "__access_denied_reason_sentinel__"
        observed_order: list[str] = []

        async def fake_handoff(*args, **kwargs):
            observed_order.append("navigation handoff")
            return False

        async def fake_allowed(u):
            observed_order.append("eligibility")
            return False, denied_reason_sentinel

        async def fake_clear():
            observed_order.append("state.clear")
            return None

        async def fake_answer_incoming_message(*args, **kwargs):
            observed_order.append("incoming-message delivery")
            return None

        state = mock.AsyncMock()
        state.clear.side_effect = fake_clear

        with mock.patch(
            "bot.handlers.panel.handoff_navigation_button",
            new_callable=mock.AsyncMock,
            side_effect=fake_handoff,
        ) as mock_handoff, mock.patch(
            "bot.handlers.offer_overtime_preference._overtime_preference_allowed",
            new_callable=mock.AsyncMock,
            side_effect=fake_allowed,
        ) as mock_allowed, mock.patch(
            "bot.handlers.offer_overtime_preference.normalize_overtime_minutes",
        ) as mock_normalize, mock.patch(
            "bot.handlers.offer_overtime_preference.build_overtime_preference_confirm_prompt",
        ) as mock_build_prompt, mock.patch(
            "bot.handlers.offer_overtime_preference._overtime_preference_confirm_keyboard",
        ) as mock_confirm_keyboard, mock.patch(
            "bot.handlers.offer_overtime_preference._overtime_preference_cancel_keyboard",
        ) as mock_cancel_keyboard, mock.patch(
            "bot.handlers.offer_overtime_preference.answer_incoming_message_via_runtime",
            new_callable=mock.AsyncMock,
            side_effect=fake_answer_incoming_message,
        ) as mock_answer_message:
            result = await process_offer_overtime_preference_value(message, state, user)

        self.assertIsNone(result)

        self.assertEqual(
            observed_order,
            [
                "navigation handoff",
                "eligibility",
                "state.clear",
                "incoming-message delivery",
            ],
        )

        mock_handoff.assert_awaited_once_with(message, state, user)
        mock_allowed.assert_awaited_once_with(user)
        state.clear.assert_awaited_once_with()
        mock_answer_message.assert_awaited_once_with(
            message,
            user,
            denied_reason_sentinel,
            source_key="overtime-preference-value-access-denied",
        )

        mock_normalize.assert_not_called()
        state.update_data.assert_not_called()
        state.set_state.assert_not_called()
        mock_build_prompt.assert_not_called()
        mock_confirm_keyboard.assert_not_called()
        mock_cancel_keyboard.assert_not_called()

        self.assertEqual(mock_handoff.mock_calls, [mock.call(message, state, user)])
        self.assertEqual(mock_allowed.mock_calls, [mock.call(user)])
        self.assertEqual(state.mock_calls, [mock.call.clear()])
        self.assertEqual(
            mock_answer_message.mock_calls,
            [
                mock.call(
                    message,
                    user,
                    denied_reason_sentinel,
                    source_key="overtime-preference-value-access-denied",
                )
            ],
        )

    async def test_process_value_allowed_valid_value_updates_state_and_emits_confirm_prompt(self):
        message_text_sentinel = "__typed_overtime_minutes_sentinel__"
        message = mock.Mock()
        message.text = message_text_sentinel
        user = object()
        normalized_minutes_sentinel = 7
        prompt_sentinel = "__overtime_preference_confirm_prompt_sentinel__"
        reply_markup_sentinel = object()
        observed_order: list[str] = []

        async def fake_handoff(*args, **kwargs):
            observed_order.append("navigation handoff")
            return False

        async def fake_allowed(u):
            observed_order.append("eligibility")
            return True, None

        def fake_normalize(text):
            observed_order.append("normalization")
            return normalized_minutes_sentinel

        async def fake_update_data(**kwargs):
            observed_order.append("pending-data update")
            return None

        async def fake_set_state(s):
            observed_order.append("state transition")
            return None

        def fake_build_prompt(mins):
            observed_order.append("prompt construction")
            return prompt_sentinel

        def fake_confirm_keyboard():
            observed_order.append("keyboard construction")
            return reply_markup_sentinel

        async def fake_answer_incoming_message(*args, **kwargs):
            observed_order.append("delivery")
            return None

        state = mock.AsyncMock()
        state.update_data.side_effect = fake_update_data
        state.set_state.side_effect = fake_set_state

        with mock.patch(
            "bot.handlers.panel.handoff_navigation_button",
            new_callable=mock.AsyncMock,
            side_effect=fake_handoff,
        ) as mock_handoff, mock.patch(
            "bot.handlers.offer_overtime_preference._overtime_preference_allowed",
            new_callable=mock.AsyncMock,
            side_effect=fake_allowed,
        ) as mock_allowed, mock.patch(
            "bot.handlers.offer_overtime_preference.normalize_overtime_minutes",
            side_effect=fake_normalize,
        ) as mock_normalize, mock.patch(
            "bot.handlers.offer_overtime_preference.build_overtime_preference_confirm_prompt",
            side_effect=fake_build_prompt,
        ) as mock_build_prompt, mock.patch(
            "bot.handlers.offer_overtime_preference._overtime_preference_confirm_keyboard",
            side_effect=fake_confirm_keyboard,
        ) as mock_confirm_keyboard, mock.patch(
            "bot.handlers.offer_overtime_preference._overtime_preference_cancel_keyboard",
        ) as mock_cancel_keyboard, mock.patch(
            "bot.handlers.offer_overtime_preference.answer_incoming_message_via_runtime",
            new_callable=mock.AsyncMock,
            side_effect=fake_answer_incoming_message,
        ) as mock_answer_message:
            result = await process_offer_overtime_preference_value(message, state, user)

        self.assertIsNone(result)

        self.assertEqual(
            observed_order,
            [
                "navigation handoff",
                "eligibility",
                "normalization",
                "pending-data update",
                "state transition",
                "prompt construction",
                "keyboard construction",
                "delivery",
            ],
        )

        mock_handoff.assert_awaited_once_with(message, state, user)
        mock_allowed.assert_awaited_once_with(user)
        mock_normalize.assert_called_once_with(message_text_sentinel)
        state.update_data.assert_awaited_once_with(
            overtime_preference_pending_minutes=normalized_minutes_sentinel
        )
        state.set_state.assert_awaited_once_with(OfferOvertimePreference.awaiting_confirmation)
        mock_build_prompt.assert_called_once_with(normalized_minutes_sentinel)
        mock_confirm_keyboard.assert_called_once_with()
        mock_answer_message.assert_awaited_once_with(
            message,
            user,
            prompt_sentinel,
            source_key="overtime-preference-confirm-prompt",
            reply_markup=reply_markup_sentinel,
            temporary_context_keyboard=True,
        )

        state.clear.assert_not_called()
        mock_cancel_keyboard.assert_not_called()

        self.assertEqual(mock_handoff.mock_calls, [mock.call(message, state, user)])
        self.assertEqual(mock_allowed.mock_calls, [mock.call(user)])
        self.assertEqual(mock_normalize.mock_calls, [mock.call(message_text_sentinel)])
        self.assertEqual(
            state.mock_calls,
            [
                mock.call.update_data(
                    overtime_preference_pending_minutes=normalized_minutes_sentinel
                ),
                mock.call.set_state(OfferOvertimePreference.awaiting_confirmation),
            ],
        )
        self.assertEqual(
            mock_build_prompt.mock_calls,
            [mock.call(normalized_minutes_sentinel)],
        )
        self.assertEqual(mock_confirm_keyboard.mock_calls, [mock.call()])
        self.assertEqual(
            mock_answer_message.mock_calls,
            [
                mock.call(
                    message,
                    user,
                    prompt_sentinel,
                    source_key="overtime-preference-confirm-prompt",
                    reply_markup=reply_markup_sentinel,
                    temporary_context_keyboard=True,
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
