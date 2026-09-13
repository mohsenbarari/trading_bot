"""Hermetic characterization tests for Bot admin-broadcast guards on MIDDLE_MANAGER role.

Freezes the current superadmin-only Telegram admin-broadcast guards for an ACTIVE,
Bot-eligible UserRole.MIDDLE_MANAGER across three closely related entry points:
- start_telegram_admin_broadcast
- cancel_telegram_admin_broadcast
- choose_all_recipients

Included scenarios:
- start_telegram_admin_broadcast silently returns None without clearing state or
  sending the broadcast menu message for an active MIDDLE_MANAGER.
- cancel_telegram_admin_broadcast rejects an active MIDDLE_MANAGER with a denial
  alert ("عدم دسترسی") and terminates without clearing state or editing the message.
- choose_all_recipients rejects an active MIDDLE_MANAGER with a denial alert
  ("عدم دسترسی") without updating state or prompting for broadcast message text.

Evidenced exclusions:
- Invitation success (covered under invitation-admin guard characterizations).
- SUPER_ADMIN success flows.
- STANDARD, POLICE, and WATCH role behavior.
- Recipient resolution, broadcast creation, persistence, and delivery.
- Trade execution.
- Notification-audience delivery.
- Live-path side-effect retry.
- Origin-cell or seed closure, blocker closure, reconciliation, and Stage closure.
- Product code, existing tests, and canonical inventories are intentionally unmodified.
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from bot.handlers.admin_broadcast import (  # noqa: E402
    ADMIN_BROADCAST_BUTTON_TEXT,
    CALLBACK_PREFIX,
    cancel_telegram_admin_broadcast,
    choose_all_recipients,
    start_telegram_admin_broadcast,
)
from core.enums import UserAccountStatus, UserRole  # noqa: E402
from core.services.bot_access_policy import evaluate_bot_access_local_state  # noqa: E402


class FakeMessage:
    """Minimal Message double providing attributes expected by message handlers."""

    def __init__(self, text: str = ADMIN_BROADCAST_BUTTON_TEXT, message_id: int = 1001) -> None:
        self.text = text
        self.message_id = message_id


class FakeCallback:
    """Minimal CallbackQuery double providing attributes expected by callback handlers."""

    def __init__(self, data: str) -> None:
        self.data = data


class RecordingFSMContext:
    """FSMContext double that records method calls and data mutations."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self._data = dict(data or {})
        self.set_state_calls: list[Any] = []
        self.update_data_calls: list[dict[str, Any]] = []
        self.get_data_calls: int = 0
        self.clear_calls: int = 0

    async def set_state(self, state: Any = None) -> None:
        self.set_state_calls.append(state)

    async def update_data(self, **kwargs: Any) -> dict[str, Any]:
        self.update_data_calls.append(kwargs)
        self._data.update(kwargs)
        return dict(self._data)

    async def get_data(self) -> dict[str, Any]:
        self.get_data_calls += 1
        return dict(self._data)

    async def clear(self) -> None:
        self.clear_calls += 1
        self._data.clear()


class FakeUser:
    """User fixture double matching runtime-shaped attributes."""

    def __init__(
        self,
        *,
        id: int,
        account_name: str,
        mobile_number: str,
        telegram_id: int,
        role: UserRole,
        account_status: UserAccountStatus = UserAccountStatus.ACTIVE,
        is_deleted: bool = False,
    ) -> None:
        self.id = id
        self.account_name = account_name
        self.mobile_number = mobile_number
        self.telegram_id = telegram_id
        self.role = role
        self.account_status = account_status
        self.is_deleted = is_deleted


class CharacterizeBotBroadcastMiddleManagerGuardTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing admin-broadcast guard rejection of active middle managers."""

    def _build_middle_manager_actor(self) -> FakeUser:
        return FakeUser(
            id=501,
            account_name="middle_manager_tester",
            mobile_number="09121112233",
            telegram_id=987654321,
            role=UserRole.MIDDLE_MANAGER,
            account_status=UserAccountStatus.ACTIVE,
            is_deleted=False,
        )

    @patch("bot.handlers.admin_broadcast.answer_incoming_message_via_runtime", new_callable=AsyncMock)
    async def test_start_telegram_admin_broadcast_silently_denies_bot_eligible_middle_manager(
        self,
        mock_answer_incoming: AsyncMock,
    ) -> None:
        actor = self._build_middle_manager_actor()
        access_decision = evaluate_bot_access_local_state(actor)
        self.assertTrue(access_decision.allowed)
        self.assertIsNone(access_decision.reason)

        message = FakeMessage(text=ADMIN_BROADCAST_BUTTON_TEXT)
        initial_data = {"existing_key": "preserved_val"}
        state = RecordingFSMContext(data=initial_data)

        result = await start_telegram_admin_broadcast(message, state, actor)

        self.assertIsNone(result)
        self.assertEqual(state.clear_calls, 0)
        mock_answer_incoming.assert_not_called()
        self.assertEqual(state.set_state_calls, [])
        self.assertEqual(state.update_data_calls, [])
        self.assertEqual(state._data, initial_data)

    @patch("bot.handlers.admin_broadcast.edit_callback_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.admin_broadcast.answer_callback_query_via_runtime", new_callable=AsyncMock)
    async def test_cancel_telegram_admin_broadcast_alert_denies_bot_eligible_middle_manager(
        self,
        mock_answer_callback_query: AsyncMock,
        mock_edit_callback_message: AsyncMock,
    ) -> None:
        actor = self._build_middle_manager_actor()
        access_decision = evaluate_bot_access_local_state(actor)
        self.assertTrue(access_decision.allowed)
        self.assertIsNone(access_decision.reason)

        callback = FakeCallback(data=f"{CALLBACK_PREFIX}:cancel")
        initial_data = {"existing_key": "preserved_val"}
        state = RecordingFSMContext(data=initial_data)

        result = await cancel_telegram_admin_broadcast(callback, state, actor)

        self.assertIsNone(result)
        mock_answer_callback_query.assert_awaited_once_with(
            callback,
            "عدم دسترسی",
            show_alert=True,
        )
        self.assertEqual(state.clear_calls, 0)
        mock_edit_callback_message.assert_not_called()
        self.assertEqual(state.set_state_calls, [])
        self.assertEqual(state.update_data_calls, [])
        self.assertEqual(state._data, initial_data)

    @patch("bot.handlers.admin_broadcast._ask_for_message_text", new_callable=AsyncMock)
    @patch("bot.handlers.admin_broadcast.answer_callback_query_via_runtime", new_callable=AsyncMock)
    async def test_choose_all_recipients_alert_denies_bot_eligible_middle_manager(
        self,
        mock_answer_callback_query: AsyncMock,
        mock_ask_for_message_text: AsyncMock,
    ) -> None:
        actor = self._build_middle_manager_actor()
        access_decision = evaluate_bot_access_local_state(actor)
        self.assertTrue(access_decision.allowed)
        self.assertIsNone(access_decision.reason)

        callback = FakeCallback(data=f"{CALLBACK_PREFIX}:all")
        initial_data = {"existing_key": "preserved_val"}
        state = RecordingFSMContext(data=initial_data)

        result = await choose_all_recipients(callback, state, actor)

        self.assertIsNone(result)
        mock_answer_callback_query.assert_awaited_once_with(
            callback,
            "عدم دسترسی",
            show_alert=True,
        )
        self.assertEqual(mock_answer_callback_query.await_count, 1)
        self.assertEqual(len(state.update_data_calls), 0)
        mock_ask_for_message_text.assert_not_called()
        self.assertEqual(state.set_state_calls, [])
        self.assertEqual(state.clear_calls, 0)
        self.assertEqual(state._data, initial_data)


if __name__ == "__main__":
    unittest.main()
