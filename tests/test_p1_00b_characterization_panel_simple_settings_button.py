"""Direct-characterization tests for bot.handlers.panel.handle_simple_settings_button.

This is direct characterization only and does not update the executed-seed registry,
reduce a canonical blocker, promote evidence, change product behavior, or mark P1-00b complete.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from bot.handlers.panel import handle_simple_settings_button  # noqa: E402


class CharacterizePanelSimpleSettingsButtonTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing bot.handlers.panel.handle_simple_settings_button."""

    @patch("bot.handlers.panel.answer_incoming_message_via_runtime", new_callable=AsyncMock)
    async def test_handle_simple_settings_button_no_user_early_returns(
        self,
        mock_answer_incoming_message_via_runtime: AsyncMock,
    ) -> None:
        message = SimpleNamespace(answer=AsyncMock())

        await handle_simple_settings_button(message, user=None)

        mock_answer_incoming_message_via_runtime.assert_not_called()
        mock_answer_incoming_message_via_runtime.assert_not_awaited()
        message.answer.assert_not_called()
        message.answer.assert_not_awaited()

    @patch("bot.handlers.panel.answer_incoming_message_via_runtime", new_callable=AsyncMock)
    async def test_handle_simple_settings_button_eligible_user_delivers_development_message(
        self,
        mock_answer_incoming_message_via_runtime: AsyncMock,
    ) -> None:
        message = SimpleNamespace(
            message_id=101,
            chat=SimpleNamespace(id=5001),
            answer=AsyncMock(),
        )
        user = SimpleNamespace(
            id=42,
            telegram_id=5001,
            sync_version=1,
        )

        await handle_simple_settings_button(message, user)

        mock_answer_incoming_message_via_runtime.assert_awaited_once_with(
            message,
            user,
            "🚧 بخش تنظیمات کاربری در حال توسعه است.",
            source_key="panel-simple-settings",
        )

        call_args, call_kwargs = mock_answer_incoming_message_via_runtime.await_args

        self.assertEqual(len(call_args), 3)
        self.assertIs(call_args[0], message)
        self.assertIs(call_args[1], user)
        self.assertEqual(call_args[2], "🚧 بخش تنظیمات کاربری در حال توسعه است.")

        self.assertEqual(set(call_kwargs.keys()), {"source_key"})
        self.assertEqual(call_kwargs["source_key"], "panel-simple-settings")

        message.answer.assert_not_called()
        message.answer.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
