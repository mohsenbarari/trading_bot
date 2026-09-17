"""Hermetic characterization tests for start account panel entry handlers."""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    _FENCE,
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from aiogram.filters.command import CommandObject
from bot.handlers.start import (
    handle_start_with_token,
    handle_start_without_token,
)
from bot.message_manager import DeleteDelay


class StartAccountPanelEntryCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.call_order: list[str] = []

        async def _clear():
            self.call_order.append("state.clear")

        async def _delete_previous_anchor(*args, **kwargs):
            self.call_order.append("delete_previous_anchor")

        async def _panel_builder(*args, **kwargs):
            self.call_order.append("build_returning_account_panel_message")
            return self.panel_text

        async def _keyboard_builder(*args, **kwargs):
            self.call_order.append("build_persistent_navigation_keyboard")
            return self.sentinel_keyboard

        async def _pre_auth_answer(*args, **kwargs):
            self.call_order.append("answer_pre_auth_message_via_runtime")
            return self.anchor_msg

        def _set_anchor(*args, **kwargs):
            self.call_order.append("set_anchor")

        self.user = SimpleNamespace(
            id=42,
            full_name="Returning User",
            account_name="returning_42",
            role="standard",
        )
        self.bot = SimpleNamespace()
        self.chat_id = 7001
        self.message = SimpleNamespace(
            bot=self.bot,
            chat=SimpleNamespace(id=self.chat_id),
        )
        self.state = SimpleNamespace(
            clear=AsyncMock(side_effect=_clear),
        )
        self.panel_text = "SENTINEL_RETURNING_PANEL_TEXT"
        self.sentinel_keyboard = SimpleNamespace(keyboard="persistent_menu_sentinel")
        self.anchor_msg = SimpleNamespace(message_id=313)

        self.mock_panel_builder = AsyncMock(side_effect=_panel_builder)
        self.mock_keyboard_builder = AsyncMock(side_effect=_keyboard_builder)
        self.mock_pre_auth_answer = AsyncMock(side_effect=_pre_auth_answer)
        self.mock_delete_previous_anchor = AsyncMock(side_effect=_delete_previous_anchor)
        self.mock_set_anchor = Mock(side_effect=_set_anchor)
        self.mock_neutral_builder = Mock()

        def _fail_session(*args, **kwargs):
            raise AssertionError("AsyncSessionLocal should never be called")

        self.fail_session = Mock(side_effect=_fail_session)

        patchers = [
            patch("bot.handlers.start.build_returning_account_panel_message", self.mock_panel_builder),
            patch("bot.handlers.start.build_persistent_navigation_keyboard", self.mock_keyboard_builder),
            patch("bot.handlers.start.answer_pre_auth_message_via_runtime", self.mock_pre_auth_answer),
            patch("bot.handlers.start.delete_previous_anchor", self.mock_delete_previous_anchor),
            patch("bot.handlers.start.set_anchor", self.mock_set_anchor),
            patch("bot.handlers.start.build_neutral_account_link_message", self.mock_neutral_builder),
            patch("bot.handlers.start._direct_registration_runtime_ready", return_value=False),
            patch("bot.handlers.start._user_facing_webapp_url", return_value="https://example.invalid/app"),
            patch("bot.handlers.start.AsyncSessionLocal", self.fail_session),
        ]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)

    async def test_handle_start_with_token_returns_existing_account_panel_without_clearing_state(self):
        denied_before = len(_FENCE["denied_connects"])
        redis_before = len(_FENCE["live_redis_constructs"])

        command = CommandObject(command="start", args="link_existing-account")
        await handle_start_with_token(self.message, command, state=self.state, user=self.user)

        self.assertEqual(
            self.call_order,
            [
                "delete_previous_anchor",
                "build_returning_account_panel_message",
                "build_persistent_navigation_keyboard",
                "answer_pre_auth_message_via_runtime",
                "set_anchor",
            ],
        )
        self.mock_delete_previous_anchor.assert_awaited_once_with(
            self.bot,
            self.chat_id,
            delay=DeleteDelay.DEFAULT.value,
        )
        self.mock_panel_builder.assert_awaited_once_with(self.bot, self.user)
        self.mock_keyboard_builder.assert_awaited_once_with(
            self.user,
            "https://example.invalid/app",
        )
        self.mock_pre_auth_answer.assert_awaited_once_with(
            self.message,
            self.panel_text,
            reply_markup=self.sentinel_keyboard,
        )
        self.mock_set_anchor.assert_called_once_with(self.chat_id, 313)
        self.state.clear.assert_not_awaited()
        self.fail_session.assert_not_called()
        self.mock_neutral_builder.assert_not_called()

        self.assertEqual(len(_FENCE["denied_connects"]), denied_before)
        self.assertEqual(len(_FENCE["live_redis_constructs"]), redis_before)

    async def test_handle_start_without_token_clears_state_then_returns_existing_account_panel(self):
        denied_before = len(_FENCE["denied_connects"])
        redis_before = len(_FENCE["live_redis_constructs"])

        await handle_start_without_token(self.message, state=self.state, user=self.user)

        self.assertEqual(
            self.call_order,
            [
                "state.clear",
                "delete_previous_anchor",
                "build_returning_account_panel_message",
                "build_persistent_navigation_keyboard",
                "answer_pre_auth_message_via_runtime",
                "set_anchor",
            ],
        )
        self.state.clear.assert_awaited_once_with()
        self.mock_delete_previous_anchor.assert_awaited_once_with(
            self.bot,
            self.chat_id,
            delay=DeleteDelay.DEFAULT.value,
        )
        self.mock_panel_builder.assert_awaited_once_with(self.bot, self.user)
        self.mock_keyboard_builder.assert_awaited_once_with(
            self.user,
            "https://example.invalid/app",
        )
        self.mock_pre_auth_answer.assert_awaited_once_with(
            self.message,
            self.panel_text,
            reply_markup=self.sentinel_keyboard,
        )
        self.mock_set_anchor.assert_called_once_with(self.chat_id, 313)
        self.fail_session.assert_not_called()
        self.mock_neutral_builder.assert_not_called()

        self.assertEqual(len(_FENCE["denied_connects"]), denied_before)
        self.assertEqual(len(_FENCE["live_redis_constructs"]), redis_before)


if __name__ == "__main__":
    unittest.main()
