"""Hermetic characterization tests for customer invite entry and cancel handlers in bot.handlers.panel."""

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
    cancel_customer_invite_tier1,
    start_user_panel_customer_invite_tier1,
)
from bot.states import CustomerInvite
from core.customer_invite import CustomerInviteSyncGateResult


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


class PanelCustomerInviteEntryCancelBatchCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_sync_ready_initializes_invite_fsm_and_delivers_prompt(self) -> None:
        state = RecordingFSMContext(
            initial_data={"stale_key": "stale_value"},
            initial_state=CustomerInvite.awaiting_confirmation,
        )
        callback = MagicMock(name="callback")
        user = MagicMock(name="user", id=1001)
        cancel_keyboard_sentinel = object()

        with (
            patch(
                "bot.handlers.panel._customer_invite_access_allowed",
                new_callable=AsyncMock,
            ) as mock_access,
            patch(
                "bot.handlers.panel.check_customer_invite_sync_ready",
                new_callable=AsyncMock,
            ) as mock_sync_gate,
            patch(
                "bot.handlers.panel.answer_callback_query_via_runtime",
                new_callable=AsyncMock,
            ) as mock_query_answer,
            patch(
                "bot.handlers.panel.answer_callback_message_via_runtime",
                new_callable=AsyncMock,
            ) as mock_message_answer,
            patch(
                "bot.handlers.panel._customer_invite_cancel_keyboard",
                return_value=cancel_keyboard_sentinel,
            ) as mock_cancel_keyboard,
        ):
            mock_access.return_value = (True, None)
            mock_sync_gate.return_value = CustomerInviteSyncGateResult(ready=True)

            await start_user_panel_customer_invite_tier1(callback, state, user)

            mock_access.assert_awaited_once_with(user)
            self.assertIs(mock_access.await_args.args[0], user)
            mock_sync_gate.assert_awaited_once_with()
            mock_cancel_keyboard.assert_called_once_with()

            mock_query_answer.assert_awaited_once_with(
                callback,
                "در حال بررسی وضعیت اتصال دو سرور...",
            )
            self.assertIs(mock_query_answer.await_args.args[0], callback)

            self.assertEqual(state.data, {"customer_invite_owner_id": 1001})
            self.assertEqual(state.state, CustomerInvite.awaiting_management_name)

            mock_message_answer.assert_awaited_once_with(
                callback,
                user,
                "نام مشتری سطح۱ را وارد کنید:",
                source_key="panel-invite-name-prompt",
                reply_markup=cancel_keyboard_sentinel,
                temporary_context_keyboard=True,
            )
            self.assertIs(mock_message_answer.await_args.args[0], callback)
            self.assertIs(mock_message_answer.await_args.args[1], user)

    async def test_cancel_clears_invite_fsm_and_delivers_cancellation(self) -> None:
        state = RecordingFSMContext(
            initial_data={
                "customer_invite_owner_id": 1001,
                "customer_invite_management_name": "شرکت تست",
                "customer_invite_mobile_number": "09123456789",
            },
            initial_state=CustomerInvite.awaiting_confirmation,
        )
        callback = MagicMock(name="callback")
        user = MagicMock(name="user", id=1001)

        with (
            patch(
                "bot.handlers.panel.answer_callback_query_via_runtime",
                new_callable=AsyncMock,
            ) as mock_query_answer,
            patch(
                "bot.handlers.panel.answer_callback_message_via_runtime",
                new_callable=AsyncMock,
            ) as mock_message_answer,
        ):
            await cancel_customer_invite_tier1(callback, state, user)

            self.assertEqual(state.data, {})
            self.assertIsNone(state.state)

            mock_query_answer.assert_awaited_once_with(callback, "لغو شد")
            self.assertIs(mock_query_answer.await_args.args[0], callback)

            mock_message_answer.assert_awaited_once_with(
                callback,
                user,
                "دعوت مشتری لغو شد.",
                source_key="panel-invite-cancelled",
            )
            self.assertIs(mock_message_answer.await_args.args[0], callback)
            self.assertIs(mock_message_answer.await_args.args[1], user)


if __name__ == "__main__":
    unittest.main()
