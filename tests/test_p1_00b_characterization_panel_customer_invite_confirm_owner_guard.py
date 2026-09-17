"""Hermetic characterization test for owner mismatch guard in bot.handlers.panel.confirm_customer_invite_tier1."""

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

from bot.handlers.panel import confirm_customer_invite_tier1
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
        self.get_data_calls: int = 0
        self.clear_calls: int = 0
        self.call_order: list[str] = []

    async def get_data(self) -> dict[str, Any]:
        self.get_data_calls += 1
        self.call_order.append("get_data")
        return dict(self.data)

    async def clear(self) -> None:
        self.clear_calls += 1
        self.call_order.append("clear")
        self.data.clear()
        self.state = None


class PanelCustomerInviteConfirmOwnerGuardCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirm_owner_mismatch_clears_state_and_stops_before_forwarding(self) -> None:
        fence = install_p1_00_network_fence()
        denied_before = len(fence["denied_connects"])
        redis_constructs_before = len(fence["live_redis_constructs"])

        state = RecordingFSMContext(
            initial_data={
                "customer_invite_owner_id": 701,
                "customer_invite_management_name": "مشتری آزمایشی",
                "customer_invite_mobile_number": "09123456789",
            },
            initial_state=CustomerInvite.awaiting_confirmation,
        )
        callback = MagicMock(name="callback")
        user = MagicMock(name="user", id=700)

        with (
            patch(
                "bot.handlers.panel._customer_invite_access_allowed",
                new_callable=AsyncMock,
                return_value=(True, None),
            ) as mock_access,
            patch(
                "bot.handlers.panel.answer_callback_query_via_runtime",
                new_callable=AsyncMock,
            ) as mock_query_answer,
            patch(
                "bot.handlers.panel.check_customer_invite_sync_ready",
                new_callable=AsyncMock,
            ) as mock_sync_gate,
            patch(
                "bot.handlers.panel.forward_customer_invite_to_iran",
                new_callable=AsyncMock,
            ) as mock_forwarder,
            patch(
                "bot.handlers.panel.answer_callback_message_via_runtime",
                new_callable=AsyncMock,
            ) as mock_message_answer,
        ):
            await confirm_customer_invite_tier1(callback, state, user)

            mock_access.assert_awaited_once_with(user)
            self.assertIs(mock_access.await_args.args[0], user)

            self.assertEqual(state.get_data_calls, 1)
            self.assertEqual(state.clear_calls, 1)
            self.assertEqual(state.call_order, ["get_data", "clear"])
            self.assertEqual(state.data, {})
            self.assertIsNone(state.state)

            mock_query_answer.assert_awaited_once_with(
                callback,
                "اطلاعات دعوت ناقص است.",
                show_alert=True,
            )
            self.assertIs(mock_query_answer.await_args.args[0], callback)
            self.assertEqual(mock_query_answer.await_args.args[1], "اطلاعات دعوت ناقص است.")
            self.assertIs(mock_query_answer.await_args.kwargs.get("show_alert"), True)

            mock_sync_gate.assert_not_awaited()
            mock_forwarder.assert_not_awaited()
            mock_message_answer.assert_not_awaited()

        self.assertEqual(len(fence["denied_connects"]), denied_before)
        self.assertEqual(len(fence["live_redis_constructs"]), redis_constructs_before)


if __name__ == "__main__":
    unittest.main()
