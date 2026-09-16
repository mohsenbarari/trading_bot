"""Hermetic characterization tests for start legacy respond terminal handlers."""

from __future__ import annotations

import os
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from bot.handlers.start import (
    LEGACY_RESPOND_PATH_DISABLED_MESSAGE,
    handle_cancel_respond,
    handle_confirm_trade,
)
from core.enums import UserAccountStatus, UserRole


@contextmanager
def _delivery_harness():
    callback = object()
    with (
        patch(
            "bot.handlers.start.edit_pre_auth_callback_message_via_runtime",
            new_callable=AsyncMock,
        ) as mock_edit,
        patch(
            "bot.handlers.start.answer_callback_query_via_runtime",
            new_callable=AsyncMock,
        ) as mock_answer,
    ):
        yield callback, mock_edit, mock_answer


class StartLegacyRespondTerminalCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirm_trade_allowed_user_returns_disabled_terminal_response(
        self,
    ) -> None:
        user = SimpleNamespace(
            role=UserRole.STANDARD,
            account_status=UserAccountStatus.ACTIVE,
            is_deleted=False,
        )
        with _delivery_harness() as (callback, mock_edit, mock_answer):
            result = await handle_confirm_trade(callback, user)

            self.assertIsNone(result)
            mock_edit.assert_awaited_once_with(
                callback,
                LEGACY_RESPOND_PATH_DISABLED_MESSAGE,
            )
            self.assertEqual(len(mock_edit.await_args.args), 2)
            self.assertIs(mock_edit.await_args.args[0], callback)
            self.assertEqual(
                mock_edit.await_args.args[1],
                LEGACY_RESPOND_PATH_DISABLED_MESSAGE,
            )
            self.assertEqual(mock_edit.await_args.kwargs, {})

            mock_answer.assert_awaited_once_with(
                callback,
                "این مسیر دیگر فعال نیست.",
                show_alert=True,
            )
            self.assertEqual(len(mock_answer.await_args.args), 2)
            self.assertIs(mock_answer.await_args.args[0], callback)
            self.assertEqual(mock_answer.await_args.args[1], "این مسیر دیگر فعال نیست.")
            self.assertEqual(mock_answer.await_args.kwargs, {"show_alert": True})

    async def test_cancel_respond_returns_exact_terminal_response(self) -> None:
        with _delivery_harness() as (callback, mock_edit, mock_answer):
            result = await handle_cancel_respond(callback)

            self.assertIsNone(result)
            mock_edit.assert_awaited_once_with(
                callback,
                "❌ انصراف از معامله.",
            )
            self.assertEqual(len(mock_edit.await_args.args), 2)
            self.assertIs(mock_edit.await_args.args[0], callback)
            self.assertEqual(mock_edit.await_args.args[1], "❌ انصراف از معامله.")
            self.assertEqual(mock_edit.await_args.kwargs, {})

            mock_answer.assert_awaited_once_with(callback)
            self.assertEqual(len(mock_answer.await_args.args), 1)
            self.assertIs(mock_answer.await_args.args[0], callback)
            self.assertEqual(mock_answer.await_args.kwargs, {})


if __name__ == "__main__":
    unittest.main()
