"""Hermetic characterization tests for Bot invitation admin-command guards.

Characterizes the distinction between ordinary Bot eligibility and admin-command
authority for bot.handlers.admin.start_invitation_creation, start_invitation_creation_inline,
and process_invitation_role.

Evidenced exclusions:
- Authorized SUPER_ADMIN and MIDDLE_MANAGER invitation entry coverage is characterized in
  tests/test_p1_00b_characterization_seed_slice.py, while SUPER_ADMIN standard-role success
  and MIDDLE_MANAGER forbidden-role rejection are characterized separately in
  tests/test_p1_00b_characterization_invitation_role.py; neither flow is duplicated here.
- This test suite does not characterize trade execution, remaining Telegram
  personas/double-click confirmation, notification audience behavior, side-effect
  retry paths, all 24 origin cells, seed closure, blocker closure, or Stage closure.
- Canonical inventories and product code are intentionally not mutated.
"""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from bot.handlers.admin import (  # noqa: E402
    process_invitation_role,
    start_invitation_creation,
    start_invitation_creation_inline,
)
from core.enums import UserAccountStatus, UserRole  # noqa: E402
from core.services.bot_access_policy import evaluate_bot_access_local_state  # noqa: E402


class FakeMessage:
    """Minimal Message double providing attributes expected by message handlers."""

    def __init__(self, text: str = "➕ ارسال لینک دعوت", message_id: int = 1001) -> None:
        self.text = text
        self.message_id = message_id


class FakeCallbackMessage:
    """Minimal Message double for callback.message."""

    def __init__(self, message_id: int = 2002) -> None:
        self.message_id = message_id
        self.delete_calls: int = 0

    async def delete(self) -> None:
        self.delete_calls += 1


class FakeCallback:
    """Minimal CallbackQuery double providing attributes expected by callback handlers."""

    def __init__(self, data: str, message: FakeCallbackMessage | None = None) -> None:
        self.data = data
        self.message = message or FakeCallbackMessage()


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


class CharacterizeBotInvitationAdminGuardTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing admin-command guard rejection of ordinary eligible roles."""

    def _build_eligible_user(self, role: UserRole) -> FakeUser:
        return FakeUser(
            id=501,
            account_name=f"{role.name.lower()}_tester",
            mobile_number="09121112233",
            telegram_id=987654321,
            role=role,
            account_status=UserAccountStatus.ACTIVE,
            is_deleted=False,
        )

    @patch("bot.handlers.admin.answer_incoming_message_via_runtime", new_callable=AsyncMock)
    async def test_start_invitation_creation_message_silent_denial_for_standard_role(
        self,
        mock_answer_incoming: AsyncMock,
    ) -> None:
        user = self._build_eligible_user(UserRole.STANDARD)
        access_decision = evaluate_bot_access_local_state(user)
        self.assertTrue(access_decision.allowed)
        self.assertIsNone(access_decision.reason)

        message = FakeMessage(text="➕ ارسال لینک دعوت")
        state = RecordingFSMContext()

        result = await start_invitation_creation(message, state, user)

        self.assertIsNone(result)
        mock_answer_incoming.assert_not_called()
        self.assertEqual(state.set_state_calls, [])
        self.assertEqual(state.update_data_calls, [])
        self.assertEqual(state._data, {})

    @patch("bot.handlers.admin.answer_incoming_message_via_runtime", new_callable=AsyncMock)
    async def test_start_invitation_creation_message_silent_denial_for_police_role(
        self,
        mock_answer_incoming: AsyncMock,
    ) -> None:
        user = self._build_eligible_user(UserRole.POLICE)
        access_decision = evaluate_bot_access_local_state(user)
        self.assertTrue(access_decision.allowed)
        self.assertIsNone(access_decision.reason)

        message = FakeMessage(text="➕ ارسال لینک دعوت")
        state = RecordingFSMContext()

        result = await start_invitation_creation(message, state, user)

        self.assertIsNone(result)
        mock_answer_incoming.assert_not_called()
        self.assertEqual(state.set_state_calls, [])
        self.assertEqual(state.update_data_calls, [])
        self.assertEqual(state._data, {})

    @patch("bot.handlers.admin.edit_callback_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.admin.answer_callback_query_via_runtime", new_callable=AsyncMock)
    async def test_start_invitation_creation_inline_denial_for_standard_role(
        self,
        mock_answer_callback_query: AsyncMock,
        mock_edit_callback_message: AsyncMock,
    ) -> None:
        user = self._build_eligible_user(UserRole.STANDARD)
        access_decision = evaluate_bot_access_local_state(user)
        self.assertTrue(access_decision.allowed)
        self.assertIsNone(access_decision.reason)

        callback = FakeCallback(data="create_invitation_inline")
        state = RecordingFSMContext()

        result = await start_invitation_creation_inline(callback, state, user)

        self.assertIsNone(result)
        mock_answer_callback_query.assert_awaited_once_with(
            callback,
            "شما مجاز به این کار نیستید.",
            show_alert=True,
        )
        mock_edit_callback_message.assert_not_called()
        self.assertEqual(state.set_state_calls, [])
        self.assertEqual(state.update_data_calls, [])
        self.assertEqual(state._data, {})

    @patch("bot.handlers.admin.edit_callback_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.admin.answer_callback_query_via_runtime", new_callable=AsyncMock)
    async def test_start_invitation_creation_inline_denial_for_police_role(
        self,
        mock_answer_callback_query: AsyncMock,
        mock_edit_callback_message: AsyncMock,
    ) -> None:
        user = self._build_eligible_user(UserRole.POLICE)
        access_decision = evaluate_bot_access_local_state(user)
        self.assertTrue(access_decision.allowed)
        self.assertIsNone(access_decision.reason)

        callback = FakeCallback(data="create_invitation_inline")
        state = RecordingFSMContext()

        result = await start_invitation_creation_inline(callback, state, user)

        self.assertIsNone(result)
        mock_answer_callback_query.assert_awaited_once_with(
            callback,
            "شما مجاز به این کار نیستید.",
            show_alert=True,
        )
        mock_edit_callback_message.assert_not_called()
        self.assertEqual(state.set_state_calls, [])
        self.assertEqual(state.update_data_calls, [])
        self.assertEqual(state._data, {})

    @patch("bot.handlers.admin._return_to_admin_panel", new_callable=AsyncMock)
    @patch("bot.handlers.admin.answer_callback_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.admin.forward_standard_invitation_to_iran", new_callable=AsyncMock)
    @patch("bot.handlers.admin.edit_callback_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.admin.answer_callback_query_via_runtime", new_callable=AsyncMock)
    async def test_process_invitation_role_denial_for_standard_role(
        self,
        mock_answer_callback_query: AsyncMock,
        mock_edit_callback_message: AsyncMock,
        mock_forward: AsyncMock,
        mock_answer_callback_message: AsyncMock,
        mock_return_to_admin_panel: AsyncMock,
    ) -> None:
        user = self._build_eligible_user(UserRole.STANDARD)
        access_decision = evaluate_bot_access_local_state(user)
        self.assertTrue(access_decision.allowed)
        self.assertIsNone(access_decision.reason)

        initial_fsm_data = {
            "account_name": "invited_target_user",
            "mobile_number": "09129876543",
            "last_prompt_message_id": 777,
        }
        state = RecordingFSMContext(initial_fsm_data)
        callback = FakeCallback(data="set_role_STANDARD")
        bot = object()

        result = await process_invitation_role(callback, state, user, bot)

        self.assertIsNone(result)
        mock_answer_callback_query.assert_awaited_once_with(
            callback,
            "عدم دسترسی",
            show_alert=True,
        )
        self.assertEqual(state.get_data_calls, 0)
        self.assertEqual(state.clear_calls, 0)
        self.assertEqual(state._data, initial_fsm_data)
        self.assertEqual(callback.message.delete_calls, 0)
        mock_edit_callback_message.assert_not_called()
        mock_forward.assert_not_called()
        mock_answer_callback_message.assert_not_called()
        mock_return_to_admin_panel.assert_not_called()

    @patch("bot.handlers.admin._return_to_admin_panel", new_callable=AsyncMock)
    @patch("bot.handlers.admin.answer_callback_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.admin.forward_standard_invitation_to_iran", new_callable=AsyncMock)
    @patch("bot.handlers.admin.edit_callback_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.admin.answer_callback_query_via_runtime", new_callable=AsyncMock)
    async def test_process_invitation_role_denial_for_police_role(
        self,
        mock_answer_callback_query: AsyncMock,
        mock_edit_callback_message: AsyncMock,
        mock_forward: AsyncMock,
        mock_answer_callback_message: AsyncMock,
        mock_return_to_admin_panel: AsyncMock,
    ) -> None:
        user = self._build_eligible_user(UserRole.POLICE)
        access_decision = evaluate_bot_access_local_state(user)
        self.assertTrue(access_decision.allowed)
        self.assertIsNone(access_decision.reason)

        initial_fsm_data = {
            "account_name": "invited_target_user",
            "mobile_number": "09129876543",
            "last_prompt_message_id": 777,
        }
        state = RecordingFSMContext(initial_fsm_data)
        callback = FakeCallback(data="set_role_STANDARD")
        bot = object()

        result = await process_invitation_role(callback, state, user, bot)

        self.assertIsNone(result)
        mock_answer_callback_query.assert_awaited_once_with(
            callback,
            "عدم دسترسی",
            show_alert=True,
        )
        self.assertEqual(state.get_data_calls, 0)
        self.assertEqual(state.clear_calls, 0)
        self.assertEqual(state._data, initial_fsm_data)
        self.assertEqual(callback.message.delete_calls, 0)
        mock_edit_callback_message.assert_not_called()
        mock_forward.assert_not_called()
        mock_answer_callback_message.assert_not_called()
        mock_return_to_admin_panel.assert_not_called()


if __name__ == "__main__":
    unittest.main()
