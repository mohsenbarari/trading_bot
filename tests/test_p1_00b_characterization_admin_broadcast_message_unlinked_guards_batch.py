"""Stage P1-00b characterization tests for admin broadcast message unlinked-user guards batch.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

This test module characterizes the unlinked-user guards (user=None) returning None before
Message attribute access; FSM get, set, update, or clear; menu/confirmation keyboard construction;
content/caption/video validation; creation-key generation; recipient estimation;
AsyncSessionLocal creation; runtime message delivery; Redis creation; or network connection
for both start_telegram_admin_broadcast and process_broadcast_message_text.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from bot.handlers.admin_broadcast import (
    process_broadcast_message_text,
    start_telegram_admin_broadcast,
)


class _StrictMessageSentinel:
    def __getattr__(self, name: str):
        raise AssertionError(f"Unexpected Message attribute access: {name}")


class _StrictStateSentinel:
    def __init__(self):
        self.set_state = AsyncMock()
        self.get_data = AsyncMock()
        self.update_data = AsyncMock()
        self.clear = AsyncMock()

    def __getattr__(self, name: str):
        raise AssertionError(f"Unexpected FSMContext attribute access: {name}")


class AdminBroadcastMessageUnlinkedGuardsCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        super().setUp()
        self.strict_message = _StrictMessageSentinel()
        self.strict_state = _StrictStateSentinel()

    async def test_start_telegram_admin_broadcast_without_user_returns_before_message_state_keyboard_or_delivery(
        self,
    ):
        spy_main_menu_keyboard = MagicMock()
        spy_answer_incoming = AsyncMock()

        fence = install_p1_00_network_fence()
        denied_connects_before = len(fence["denied_connects"])
        live_redis_before = len(fence["live_redis_constructs"])

        with patch(
            "bot.handlers.admin_broadcast._main_menu_keyboard",
            new=spy_main_menu_keyboard,
        ), patch(
            "bot.handlers.admin_broadcast.answer_incoming_message_via_runtime",
            new=spy_answer_incoming,
        ):
            result = await start_telegram_admin_broadcast(
                message=self.strict_message,  # type: ignore[arg-type]
                state=self.strict_state,      # type: ignore[arg-type]
                user=None,
            )

        self.assertIsNone(result)

        spy_main_menu_keyboard.assert_not_called()
        spy_answer_incoming.assert_not_called()
        spy_answer_incoming.assert_not_awaited()

        self.strict_state.set_state.assert_not_awaited()
        self.strict_state.get_data.assert_not_awaited()
        self.strict_state.update_data.assert_not_awaited()
        self.strict_state.clear.assert_not_awaited()

        self.assertEqual(len(fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(fence["live_redis_constructs"]), live_redis_before)

    async def test_process_broadcast_message_text_without_user_returns_before_message_state_validation_session_or_delivery(
        self,
    ):
        spy_session_local = MagicMock()
        spy_estimate_recipients = AsyncMock()
        spy_creation_key = MagicMock()
        spy_confirmation_keyboard = MagicMock()
        spy_validate_content = MagicMock()
        spy_validate_caption = MagicMock()
        spy_validate_video = MagicMock()
        spy_answer_incoming = AsyncMock()

        fence = install_p1_00_network_fence()
        denied_connects_before = len(fence["denied_connects"])
        live_redis_before = len(fence["live_redis_constructs"])

        with patch(
            "bot.handlers.admin_broadcast.AsyncSessionLocal",
            new=spy_session_local,
        ), patch(
            "bot.handlers.admin_broadcast._estimate_recipient_count",
            new=spy_estimate_recipients,
        ), patch(
            "bot.handlers.admin_broadcast.new_telegram_admin_broadcast_creation_key",
            new=spy_creation_key,
        ), patch(
            "bot.handlers.admin_broadcast._confirmation_keyboard",
            new=spy_confirmation_keyboard,
        ), patch(
            "bot.handlers.admin_broadcast.validate_telegram_admin_broadcast_content",
            new=spy_validate_content,
        ), patch(
            "bot.handlers.admin_broadcast.validate_telegram_admin_broadcast_caption",
            new=spy_validate_caption,
        ), patch(
            "bot.handlers.admin_broadcast.validate_telegram_admin_broadcast_video_media",
            new=spy_validate_video,
        ), patch(
            "bot.handlers.admin_broadcast.answer_incoming_message_via_runtime",
            new=spy_answer_incoming,
        ):
            result = await process_broadcast_message_text(
                message=self.strict_message,  # type: ignore[arg-type]
                state=self.strict_state,      # type: ignore[arg-type]
                user=None,
            )

        self.assertIsNone(result)

        spy_session_local.assert_not_called()
        spy_estimate_recipients.assert_not_called()
        spy_estimate_recipients.assert_not_awaited()
        spy_creation_key.assert_not_called()
        spy_confirmation_keyboard.assert_not_called()
        spy_validate_content.assert_not_called()
        spy_validate_caption.assert_not_called()
        spy_validate_video.assert_not_called()
        spy_answer_incoming.assert_not_called()
        spy_answer_incoming.assert_not_awaited()

        self.strict_state.set_state.assert_not_awaited()
        self.strict_state.get_data.assert_not_awaited()
        self.strict_state.update_data.assert_not_awaited()
        self.strict_state.clear.assert_not_awaited()

        self.assertEqual(len(fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(fence["live_redis_constructs"]), live_redis_before)


if __name__ == "__main__":
    unittest.main()
