"""Characterization tests for offer overtime preference start message handler."""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import unittest
from unittest import mock

from bot.handlers.offer_overtime_preference import start_offer_overtime_preference
from bot.states import OfferOvertimePreference


class TestOfferOvertimePreferenceStartCharacterization(unittest.IsolatedAsyncioTestCase):
    async def test_start_offer_overtime_preference_identified_allowed_prompts_value(self):
        message = object()
        user = object()
        persisted_minutes_sentinel = 45
        prompt_sentinel = "__overtime_preference_value_prompt_sentinel__"
        reply_markup_sentinel = object()
        observed_order: list[str] = []

        async def fake_allowed(u):
            observed_order.append("eligibility")
            return True, None

        def fake_read_persisted(u):
            observed_order.append("persisted-minute read")
            return persisted_minutes_sentinel

        async def fake_clear():
            observed_order.append("state.clear")
            return None

        async def fake_set_state(s):
            observed_order.append("state.set_state")
            return None

        def fake_build_prompt(mins):
            observed_order.append("prompt construction")
            return prompt_sentinel

        def fake_cancel_keyboard():
            observed_order.append("cancel-keyboard construction")
            return reply_markup_sentinel

        async def fake_answer_incoming_message(*args, **kwargs):
            observed_order.append("incoming-message delivery")
            return None

        state = mock.AsyncMock()
        state.clear.side_effect = fake_clear
        state.set_state.side_effect = fake_set_state

        with mock.patch(
            "bot.handlers.offer_overtime_preference._overtime_preference_allowed",
            new_callable=mock.AsyncMock,
            side_effect=fake_allowed,
        ) as mock_allowed, mock.patch(
            "bot.handlers.offer_overtime_preference.read_persisted_overtime_minutes",
            side_effect=fake_read_persisted,
        ) as mock_read_persisted, mock.patch(
            "bot.handlers.offer_overtime_preference.build_overtime_preference_value_prompt",
            side_effect=fake_build_prompt,
        ) as mock_build_prompt, mock.patch(
            "bot.handlers.offer_overtime_preference._overtime_preference_cancel_keyboard",
            side_effect=fake_cancel_keyboard,
        ) as mock_cancel_keyboard, mock.patch(
            "bot.handlers.offer_overtime_preference.answer_incoming_message_via_runtime",
            new_callable=mock.AsyncMock,
            side_effect=fake_answer_incoming_message,
        ) as mock_answer_message:
            result = await start_offer_overtime_preference(message, state, user)

        self.assertIsNone(result)

        self.assertEqual(
            observed_order,
            [
                "eligibility",
                "persisted-minute read",
                "state.clear",
                "state.set_state",
                "prompt construction",
                "cancel-keyboard construction",
                "incoming-message delivery",
            ],
        )

        mock_allowed.assert_awaited_once_with(user)
        mock_read_persisted.assert_called_once_with(user)
        state.clear.assert_awaited_once_with()
        state.set_state.assert_awaited_once_with(OfferOvertimePreference.awaiting_value)
        mock_build_prompt.assert_called_once_with(persisted_minutes_sentinel)
        mock_cancel_keyboard.assert_called_once_with()
        mock_answer_message.assert_awaited_once_with(
            message,
            user,
            prompt_sentinel,
            source_key="overtime-preference-value-prompt",
            reply_markup=reply_markup_sentinel,
            temporary_context_keyboard=True,
        )

        self.assertEqual(mock_allowed.mock_calls, [mock.call(user)])
        self.assertEqual(mock_read_persisted.mock_calls, [mock.call(user)])
        self.assertEqual(
            state.mock_calls,
            [
                mock.call.clear(),
                mock.call.set_state(OfferOvertimePreference.awaiting_value),
            ],
        )
        self.assertEqual(
            mock_build_prompt.mock_calls,
            [mock.call(persisted_minutes_sentinel)],
        )
        self.assertEqual(mock_cancel_keyboard.mock_calls, [mock.call()])
        self.assertEqual(
            mock_answer_message.mock_calls,
            [
                mock.call(
                    message,
                    user,
                    prompt_sentinel,
                    source_key="overtime-preference-value-prompt",
                    reply_markup=reply_markup_sentinel,
                    temporary_context_keyboard=True,
                )
            ],
        )


if __name__ == "__main__":
    unittest.main()
