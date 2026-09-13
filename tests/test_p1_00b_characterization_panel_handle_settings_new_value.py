"""Direct-characterization tests for bot.handlers.panel.handle_settings_new_value.

This characterization alone does not update the seed registry,
reduce the canonical blocker, promote evidence, or close P1-00b.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from bot.handlers.panel import (  # noqa: E402
    handle_settings_new_value,
)
from core.enums import UserRole  # noqa: E402


class CharacterizePanelHandleSettingsNewValueTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing bot.handlers.panel.handle_settings_new_value early exits."""

    @patch("core.trading_settings.refresh_settings_cache_async", new_callable=AsyncMock)
    @patch("core.trading_settings.save_trading_settings_async", new_callable=AsyncMock)
    @patch("core.trading_settings.load_trading_settings_async", new_callable=AsyncMock)
    @patch("bot.handlers.panel.get_settings_keyboard")
    @patch("bot.handlers.panel.get_settings_text", new_callable=AsyncMock)
    @patch("bot.handlers.panel.answer_incoming_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel._reject_settings_message_if_not_authoritative", new_callable=AsyncMock)
    @patch("bot.handlers.panel.handoff_navigation_button", new_callable=AsyncMock)
    async def test_handle_settings_new_value_unauthenticated_early_exit(
        self,
        mock_handoff: AsyncMock,
        mock_reject_authority: AsyncMock,
        mock_answer_incoming: AsyncMock,
        mock_get_text: AsyncMock,
        mock_get_keyboard: MagicMock,
        mock_load_settings: AsyncMock,
        mock_save_settings: AsyncMock,
        mock_refresh_settings: AsyncMock,
    ) -> None:
        sentinel_message = SimpleNamespace(
            text="SENTINEL_UNAUTH_TEXT",
            answer=AsyncMock(),
        )
        sentinel_state = SimpleNamespace(
            get_data=AsyncMock(),
            clear=AsyncMock(),
        )

        result = await handle_settings_new_value(
            sentinel_message, sentinel_state, user=None
        )

        self.assertIsNone(result)

        mock_handoff.assert_not_called()
        mock_handoff.assert_not_awaited()

        sentinel_state.get_data.assert_not_called()
        sentinel_state.get_data.assert_not_awaited()
        sentinel_state.clear.assert_not_called()
        sentinel_state.clear.assert_not_awaited()

        mock_reject_authority.assert_not_called()
        mock_reject_authority.assert_not_awaited()

        mock_load_settings.assert_not_called()
        mock_load_settings.assert_not_awaited()
        mock_save_settings.assert_not_called()
        mock_save_settings.assert_not_awaited()
        mock_refresh_settings.assert_not_called()
        mock_refresh_settings.assert_not_awaited()

        mock_answer_incoming.assert_not_called()
        mock_answer_incoming.assert_not_awaited()
        sentinel_message.answer.assert_not_called()
        sentinel_message.answer.assert_not_awaited()

        mock_get_text.assert_not_called()
        mock_get_text.assert_not_awaited()
        mock_get_keyboard.assert_not_called()

    @patch("core.trading_settings.refresh_settings_cache_async", new_callable=AsyncMock)
    @patch("core.trading_settings.save_trading_settings_async", new_callable=AsyncMock)
    @patch("core.trading_settings.load_trading_settings_async", new_callable=AsyncMock)
    @patch("bot.handlers.panel.get_settings_keyboard")
    @patch("bot.handlers.panel.get_settings_text", new_callable=AsyncMock)
    @patch("bot.handlers.panel.answer_incoming_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel._reject_settings_message_if_not_authoritative", new_callable=AsyncMock)
    @patch("bot.handlers.panel.handoff_navigation_button", new_callable=AsyncMock)
    async def test_handle_settings_new_value_super_admin_navigation_handoff_early_exit(
        self,
        mock_handoff: AsyncMock,
        mock_reject_authority: AsyncMock,
        mock_answer_incoming: AsyncMock,
        mock_get_text: AsyncMock,
        mock_get_keyboard: MagicMock,
        mock_load_settings: AsyncMock,
        mock_save_settings: AsyncMock,
        mock_refresh_settings: AsyncMock,
    ) -> None:
        sentinel_message = SimpleNamespace(
            text="👤 پنل کاربر",
            answer=AsyncMock(),
        )
        sentinel_state = SimpleNamespace(
            get_data=AsyncMock(),
            clear=AsyncMock(),
        )
        sentinel_user = SimpleNamespace(
            id=77,
            role=UserRole.SUPER_ADMIN,
            username="super_admin_handoff",
        )

        call_order: list[str] = []

        async def record_handoff(*args: object, **kwargs: object) -> bool:
            call_order.append("handoff_navigation_button")
            return True

        mock_handoff.side_effect = record_handoff

        result = await handle_settings_new_value(
            sentinel_message, sentinel_state, sentinel_user
        )

        self.assertIsNone(result)

        self.assertEqual(call_order, ["handoff_navigation_button"])
        mock_handoff.assert_awaited_once_with(
            sentinel_message, sentinel_state, sentinel_user
        )
        call_args, call_kwargs = mock_handoff.await_args
        self.assertEqual(len(call_args), 3)
        self.assertIs(call_args[0], sentinel_message)
        self.assertIs(call_args[1], sentinel_state)
        self.assertIs(call_args[2], sentinel_user)
        self.assertEqual(call_kwargs, {})

        sentinel_state.get_data.assert_not_called()
        sentinel_state.get_data.assert_not_awaited()
        sentinel_state.clear.assert_not_called()
        sentinel_state.clear.assert_not_awaited()

        mock_reject_authority.assert_not_called()
        mock_reject_authority.assert_not_awaited()

        mock_load_settings.assert_not_called()
        mock_load_settings.assert_not_awaited()
        mock_save_settings.assert_not_called()
        mock_save_settings.assert_not_awaited()
        mock_refresh_settings.assert_not_called()
        mock_refresh_settings.assert_not_awaited()

        mock_answer_incoming.assert_not_called()
        mock_answer_incoming.assert_not_awaited()
        sentinel_message.answer.assert_not_called()
        sentinel_message.answer.assert_not_awaited()

        mock_get_text.assert_not_called()
        mock_get_text.assert_not_awaited()
        mock_get_keyboard.assert_not_called()

    @patch("core.trading_settings.refresh_settings_cache_async", new_callable=AsyncMock)
    @patch("core.trading_settings.save_trading_settings_async", new_callable=AsyncMock)
    @patch("core.trading_settings.load_trading_settings_async", new_callable=AsyncMock)
    @patch("bot.handlers.panel.get_settings_keyboard")
    @patch("bot.handlers.panel.get_settings_text", new_callable=AsyncMock)
    @patch("bot.handlers.panel.answer_incoming_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel._reject_settings_message_if_not_authoritative", new_callable=AsyncMock)
    @patch("bot.handlers.panel.handoff_navigation_button", new_callable=AsyncMock)
    async def test_handle_settings_new_value_super_admin_missing_editing_setting_early_exit(
        self,
        mock_handoff: AsyncMock,
        mock_reject_authority: AsyncMock,
        mock_answer_incoming: AsyncMock,
        mock_get_text: AsyncMock,
        mock_get_keyboard: MagicMock,
        mock_load_settings: AsyncMock,
        mock_save_settings: AsyncMock,
        mock_refresh_settings: AsyncMock,
    ) -> None:
        sentinel_message = SimpleNamespace(
            text="42",
            answer=AsyncMock(),
        )
        sentinel_state = SimpleNamespace(
            get_data=AsyncMock(),
            clear=AsyncMock(),
        )
        sentinel_user = SimpleNamespace(
            id=88,
            role=UserRole.SUPER_ADMIN,
            username="super_admin_missing_setting",
        )

        call_order: list[str] = []

        async def record_handoff(*args: object, **kwargs: object) -> bool:
            call_order.append("handoff_navigation_button")
            return False

        async def record_get_data(*args: object, **kwargs: object) -> dict[str, str]:
            call_order.append("state.get_data")
            return {"unrelated_state_key": "unrelated_value"}

        async def record_clear(*args: object, **kwargs: object) -> None:
            call_order.append("state.clear")

        mock_handoff.side_effect = record_handoff
        sentinel_state.get_data.side_effect = record_get_data
        sentinel_state.clear.side_effect = record_clear

        result = await handle_settings_new_value(
            sentinel_message, sentinel_state, sentinel_user
        )

        self.assertIsNone(result)

        self.assertEqual(
            call_order,
            ["handoff_navigation_button", "state.get_data", "state.clear"],
        )

        mock_handoff.assert_awaited_once_with(
            sentinel_message, sentinel_state, sentinel_user
        )
        handoff_call_args, handoff_call_kwargs = mock_handoff.await_args
        self.assertEqual(len(handoff_call_args), 3)
        self.assertIs(handoff_call_args[0], sentinel_message)
        self.assertIs(handoff_call_args[1], sentinel_state)
        self.assertIs(handoff_call_args[2], sentinel_user)
        self.assertEqual(handoff_call_kwargs, {})

        sentinel_state.get_data.assert_awaited_once_with()
        self.assertEqual(sentinel_state.get_data.await_args.args, ())
        self.assertEqual(sentinel_state.get_data.await_args.kwargs, {})

        sentinel_state.clear.assert_awaited_once_with()
        self.assertEqual(sentinel_state.clear.await_args.args, ())
        self.assertEqual(sentinel_state.clear.await_args.kwargs, {})

        mock_reject_authority.assert_not_called()
        mock_reject_authority.assert_not_awaited()

        mock_load_settings.assert_not_called()
        mock_load_settings.assert_not_awaited()
        mock_save_settings.assert_not_called()
        mock_save_settings.assert_not_awaited()
        mock_refresh_settings.assert_not_called()
        mock_refresh_settings.assert_not_awaited()

        mock_answer_incoming.assert_not_called()
        mock_answer_incoming.assert_not_awaited()
        sentinel_message.answer.assert_not_called()
        sentinel_message.answer.assert_not_awaited()

        mock_get_text.assert_not_called()
        mock_get_text.assert_not_awaited()
        mock_get_keyboard.assert_not_called()


if __name__ == "__main__":
    unittest.main()
