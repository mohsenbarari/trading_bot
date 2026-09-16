"""Hermetic characterization tests for customer invite input handlers in bot.handlers.panel."""

from __future__ import annotations

import os
import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from bot.handlers.panel import (
    process_customer_invite_management_name,
    process_customer_invite_mobile,
)
from bot.states import CustomerInvite


class RecordingFSMContext:
    """Hermetic in-memory recording FSM context without database or session dependencies."""

    def __init__(
        self,
        initial_data: dict[str, Any] | None = None,
        initial_state: Any = None,
    ) -> None:
        self.state: Any = initial_state
        self.data: dict[str, Any] = dict(initial_data or {})

    async def get_data(self) -> dict[str, Any]:
        return dict(self.data)

    async def update_data(self, **kwargs: Any) -> dict[str, Any]:
        self.data.update(kwargs)
        return dict(self.data)

    async def set_state(self, state: Any = None) -> None:
        self.state = state

    async def clear(self) -> None:
        self.data.clear()
        self.state = None


class PanelCustomerInviteInputsBatchCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_management_name_valid_input_advances_to_mobile_state(self) -> None:
        state = RecordingFSMContext(initial_state=CustomerInvite.awaiting_management_name)
        message = MagicMock(name="message")
        message.text = "   شرکت پارس   "
        user_sentinel = object()
        cancel_keyboard_sentinel = object()

        with (
            patch(
                "bot.handlers.panel._customer_invite_access_allowed",
                new_callable=AsyncMock,
            ) as mock_access,
            patch(
                "bot.handlers.panel._customer_invite_cancel_keyboard",
                return_value=cancel_keyboard_sentinel,
            ) as mock_cancel_keyboard,
            patch(
                "bot.handlers.panel.answer_incoming_message_via_runtime",
                new_callable=AsyncMock,
            ) as mock_answer,
        ):
            mock_access.return_value = (True, None)

            await process_customer_invite_management_name(message, state, user_sentinel)

            mock_access.assert_awaited_once_with(user_sentinel)
            mock_cancel_keyboard.assert_called_once_with()
            self.assertEqual(
                state.data.get("customer_invite_management_name"),
                "شرکت پارس",
            )
            self.assertEqual(state.state, CustomerInvite.awaiting_mobile_number)

            mock_answer.assert_awaited_once_with(
                message,
                user_sentinel,
                "شماره موبایل مشتری را با فرمت 09123456789 وارد کنید:",
                source_key="panel-invite-mobile-prompt",
                reply_markup=cancel_keyboard_sentinel,
                temporary_context_keyboard=True,
            )
            self.assertIs(mock_answer.await_args.args[0], message)
            self.assertIs(mock_answer.await_args.args[1], user_sentinel)

    async def test_mobile_valid_input_advances_to_confirmation_state(self) -> None:
        state = RecordingFSMContext(
            initial_data={"customer_invite_management_name": "شرکت پارس"},
            initial_state=CustomerInvite.awaiting_mobile_number,
        )
        message = MagicMock(name="message")
        message.text = "۰۹۱۲۳۴۵۶۷۸۹"
        user_sentinel = object()
        confirm_keyboard_sentinel = object()

        with (
            patch(
                "bot.handlers.panel._customer_invite_access_allowed",
                new_callable=AsyncMock,
            ) as mock_access,
            patch(
                "bot.handlers.panel._customer_invite_confirm_keyboard",
                return_value=confirm_keyboard_sentinel,
            ) as mock_confirm_keyboard,
            patch(
                "bot.handlers.panel.answer_incoming_message_via_runtime",
                new_callable=AsyncMock,
            ) as mock_answer,
        ):
            mock_access.return_value = (True, None)

            await process_customer_invite_mobile(message, state, user_sentinel)

            mock_access.assert_awaited_once_with(user_sentinel)
            mock_confirm_keyboard.assert_called_once_with()
            self.assertEqual(
                state.data.get("customer_invite_management_name"),
                "شرکت پارس",
            )
            self.assertEqual(
                state.data.get("customer_invite_mobile_number"),
                "09123456789",
            )
            self.assertEqual(state.state, CustomerInvite.awaiting_confirmation)

            expected_prompt = (
                "لطفاً اطلاعات دعوت مشتری سطح۱ را تایید کنید:\n\n"
                "نام مشتری: شرکت پارس\n"
                "شماره موبایل: `09123456789`"
            )
            mock_answer.assert_awaited_once_with(
                message,
                user_sentinel,
                expected_prompt,
                source_key="panel-invite-confirm-prompt",
                parse_mode="Markdown",
                reply_markup=confirm_keyboard_sentinel,
                temporary_context_keyboard=True,
            )
            self.assertIs(mock_answer.await_args.args[0], message)
            self.assertIs(mock_answer.await_args.args[1], user_sentinel)


if __name__ == "__main__":
    unittest.main()
