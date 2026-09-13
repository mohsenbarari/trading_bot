"""Direct characterization tests for bot.handlers.panel.show_user_panel_blocked_users.

Stage P1-00b
Assignment WA-P1-00B-FREEZE-20260904-001
seed BOT-TG-MESSAGE-PANEL-SHOW-USER-PANEL-BLOCKED-USERS-L597
gap P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

This unit does not update registries, reduce blockers, change product behavior, close the seed or close P1-00b.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from bot.handlers.panel import show_user_panel_blocked_users  # noqa: E402


class CharacterizePanelShowUserPanelBlockedUsersTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing bot.handlers.panel.show_user_panel_blocked_users."""

    @patch("bot.handlers.panel.answer_incoming_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.AsyncSessionLocal")
    @patch("bot.handlers.panel.AsyncSessionLocal")
    @patch("bot.handlers.block_manage.send_block_menu_message", new_callable=AsyncMock)
    async def test_show_user_panel_blocked_users_no_user_suppressed(
        self,
        mock_send_block_menu: AsyncMock,
        mock_panel_session: MagicMock,
        mock_block_session: MagicMock,
        mock_panel_runtime_answer: AsyncMock,
    ) -> None:
        message = SimpleNamespace(answer=AsyncMock())
        state = MagicMock()

        result = await show_user_panel_blocked_users(message, state, user=None)

        self.assertIsNone(result)

        mock_send_block_menu.assert_not_called()
        mock_send_block_menu.assert_not_awaited()

        mock_panel_session.assert_not_called()
        mock_block_session.assert_not_called()

        mock_panel_runtime_answer.assert_not_called()
        mock_panel_runtime_answer.assert_not_awaited()

        message.answer.assert_not_called()
        message.answer.assert_not_awaited()

        self.assertEqual(state.mock_calls, [])

    @patch("bot.handlers.panel.answer_incoming_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.block_manage.AsyncSessionLocal")
    @patch("bot.handlers.panel.AsyncSessionLocal")
    @patch("bot.handlers.block_manage.send_block_menu_message", new_callable=AsyncMock)
    async def test_show_user_panel_blocked_users_linked_user_delegates_to_block_menu(
        self,
        mock_send_block_menu: AsyncMock,
        mock_panel_session: MagicMock,
        mock_block_session: MagicMock,
        mock_panel_runtime_answer: AsyncMock,
    ) -> None:
        message = SimpleNamespace(
            message_id=701,
            chat=SimpleNamespace(id=5001),
            answer=AsyncMock(),
        )
        user = SimpleNamespace(
            id=42,
            telegram_id=5001,
            account_name="test_user",
        )
        state = MagicMock()

        downstream_sentinel = SimpleNamespace(message_id=801)
        mock_send_block_menu.return_value = downstream_sentinel

        result = await show_user_panel_blocked_users(message, state, user=user)

        self.assertIsNone(result)

        self.assertEqual(mock_send_block_menu.await_count, 1)
        mock_send_block_menu.assert_awaited_once()

        call_args, call_kwargs = mock_send_block_menu.await_args
        self.assertEqual(len(call_args), 2)
        self.assertIs(call_args[0], message)
        self.assertIs(call_args[1], user)
        self.assertEqual(call_kwargs, {})
        mock_send_block_menu.assert_awaited_once_with(message, user)

        self.assertEqual(state.mock_calls, [])

        mock_panel_session.assert_not_called()
        mock_block_session.assert_not_called()

        mock_panel_runtime_answer.assert_not_called()
        mock_panel_runtime_answer.assert_not_awaited()

        message.answer.assert_not_called()
        message.answer.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
