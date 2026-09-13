"""Direct side-effect-free characterization for panel back-to-main-menu handler.

Seed: BOT-TG-MESSAGE-PANEL-HANDLE-BACK-TO-MAIN-MENU-L1450
Gap: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001

Standard Disclaimer:
This characterization alone does not update the seed registry, reduce the canonical
blocker, promote evidence, or close P1-00b. It characterizes only the current behavior
of bot.handlers.panel.handle_back_to_main_menu without mutating product code or runtime
configuration.
"""

from __future__ import annotations

import unittest
from unittest import mock

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from bot.handlers.panel import handle_back_to_main_menu, settings as panel_settings
from bot.message_manager import DeleteDelay
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeDecision,
    TelegramDeliveryRuntimeMode,
)


class TestPanelHandleBackToMainMenuCharacterization(unittest.IsolatedAsyncioTestCase):
    """Characterize handle_back_to_main_menu behavior across delivery modes.

    Disclaimer:
    This characterization test suite does not update the seed registry, reduce the
    canonical blocker, promote evidence, or close Stage P1-00b.
    """

    @mock.patch("bot.handlers.panel.set_anchor")
    @mock.patch(
        "bot.handlers.panel.answer_incoming_message_via_runtime",
        new_callable=mock.AsyncMock,
    )
    @mock.patch(
        "bot.handlers.panel.build_persistent_navigation_keyboard",
        new_callable=mock.AsyncMock,
    )
    @mock.patch("bot.handlers.panel.user_facing_webapp_url")
    @mock.patch(
        "bot.handlers.panel.delete_previous_anchor",
        new_callable=mock.AsyncMock,
    )
    @mock.patch("bot.handlers.panel.configured_telegram_delivery_runtime")
    async def test_unauthenticated_user_is_complete_noop(
        self,
        mock_configured_runtime: mock.MagicMock,
        mock_delete_previous_anchor: mock.AsyncMock,
        mock_user_facing_webapp_url: mock.MagicMock,
        mock_build_persistent_navigation_keyboard: mock.AsyncMock,
        mock_answer_incoming_message_via_runtime: mock.AsyncMock,
        mock_set_anchor: mock.MagicMock,
    ) -> None:
        """user=None returns None immediately without calling any runtime dependencies."""
        message = mock.MagicMock(name="unauth_message")
        state = mock.MagicMock(name="unauth_state")

        result = await handle_back_to_main_menu(message, state, None)

        self.assertIsNone(result)
        mock_configured_runtime.assert_not_called()
        mock_delete_previous_anchor.assert_not_called()
        self.assertEqual(mock_delete_previous_anchor.await_count, 0)
        mock_user_facing_webapp_url.assert_not_called()
        mock_build_persistent_navigation_keyboard.assert_not_called()
        self.assertEqual(mock_build_persistent_navigation_keyboard.await_count, 0)
        mock_answer_incoming_message_via_runtime.assert_not_called()
        self.assertEqual(mock_answer_incoming_message_via_runtime.await_count, 0)
        mock_set_anchor.assert_not_called()

    @mock.patch("bot.handlers.panel.set_anchor")
    @mock.patch(
        "bot.handlers.panel.answer_incoming_message_via_runtime",
        new_callable=mock.AsyncMock,
    )
    @mock.patch(
        "bot.handlers.panel.build_persistent_navigation_keyboard",
        new_callable=mock.AsyncMock,
    )
    @mock.patch("bot.handlers.panel.user_facing_webapp_url")
    @mock.patch(
        "bot.handlers.panel.delete_previous_anchor",
        new_callable=mock.AsyncMock,
    )
    @mock.patch("bot.handlers.panel.configured_telegram_delivery_runtime")
    async def test_authenticated_user_queue_v1_path(
        self,
        mock_configured_runtime: mock.MagicMock,
        mock_delete_previous_anchor: mock.AsyncMock,
        mock_user_facing_webapp_url: mock.MagicMock,
        mock_build_persistent_navigation_keyboard: mock.AsyncMock,
        mock_answer_incoming_message_via_runtime: mock.AsyncMock,
        mock_set_anchor: mock.MagicMock,
    ) -> None:
        """QUEUE_V1 skips in-memory anchor ops and dispatches durable reply."""
        parent = mock.MagicMock(name="parent_order")
        parent.attach_mock(
            mock_configured_runtime, "configured_telegram_delivery_runtime"
        )
        parent.attach_mock(
            mock_delete_previous_anchor, "delete_previous_anchor"
        )
        parent.attach_mock(
            mock_user_facing_webapp_url, "user_facing_webapp_url"
        )
        parent.attach_mock(
            mock_build_persistent_navigation_keyboard,
            "build_persistent_navigation_keyboard",
        )
        parent.attach_mock(
            mock_answer_incoming_message_via_runtime,
            "answer_incoming_message_via_runtime",
        )
        parent.attach_mock(mock_set_anchor, "set_anchor")

        sentinel_bot = object()
        sentinel_chat_id = object()
        sentinel_user = object()
        sentinel_url = "https://queue.test.example/app"
        sentinel_keyboard = object()
        sentinel_anchor_id = object()
        sentinel_anchor_message = mock.MagicMock(name="sentinel_anchor_message")
        sentinel_anchor_message.message_id = sentinel_anchor_id

        message = mock.MagicMock(name="queue_message")
        message.bot = sentinel_bot
        message.chat.id = sentinel_chat_id
        state = mock.MagicMock(name="queue_state")

        runtime_decision = TelegramDeliveryRuntimeDecision(
            mode=TelegramDeliveryRuntimeMode.QUEUE_V1,
            legacy_workers_enabled=False,
            queue_worker_enabled=True,
        )
        mock_configured_runtime.return_value = runtime_decision
        mock_user_facing_webapp_url.return_value = sentinel_url
        mock_build_persistent_navigation_keyboard.return_value = sentinel_keyboard
        mock_answer_incoming_message_via_runtime.return_value = (
            sentinel_anchor_message
        )

        result = await handle_back_to_main_menu(message, state, sentinel_user)

        self.assertIsNone(result)

        # Negative assertions: no anchor manipulation in Queue-V1 mode
        mock_delete_previous_anchor.assert_not_called()
        self.assertEqual(mock_delete_previous_anchor.await_count, 0)
        mock_set_anchor.assert_not_called()

        # Cardinality and argument assertions
        mock_configured_runtime.assert_called_once_with()

        mock_user_facing_webapp_url.assert_called_once_with(
            settings_obj=panel_settings
        )
        self.assertIs(
            mock_user_facing_webapp_url.call_args.kwargs["settings_obj"],
            panel_settings,
        )

        mock_build_persistent_navigation_keyboard.assert_awaited_once_with(
            sentinel_user,
            sentinel_url,
        )
        self.assertIs(
            mock_build_persistent_navigation_keyboard.call_args[0][0],
            sentinel_user,
        )
        self.assertIs(
            mock_build_persistent_navigation_keyboard.call_args[0][1],
            sentinel_url,
        )

        mock_answer_incoming_message_via_runtime.assert_awaited_once_with(
            message,
            sentinel_user,
            "به منوی اصلی بازگشتید.",
            source_key="panel-back-main-menu",
            reply_markup=sentinel_keyboard,
            set_persistent_anchor=True,
        )
        self.assertIs(
            mock_answer_incoming_message_via_runtime.call_args[0][0], message
        )
        self.assertIs(
            mock_answer_incoming_message_via_runtime.call_args[0][1],
            sentinel_user,
        )
        self.assertEqual(
            mock_answer_incoming_message_via_runtime.call_args[0][2],
            "به منوی اصلی بازگشتید.",
        )
        self.assertEqual(
            mock_answer_incoming_message_via_runtime.call_args.kwargs[
                "source_key"
            ],
            "panel-back-main-menu",
        )
        self.assertIs(
            mock_answer_incoming_message_via_runtime.call_args.kwargs[
                "reply_markup"
            ],
            sentinel_keyboard,
        )
        self.assertIs(
            mock_answer_incoming_message_via_runtime.call_args.kwargs[
                "set_persistent_anchor"
            ],
            True,
        )

        expected_order = [
            mock.call.configured_telegram_delivery_runtime(),
            mock.call.user_facing_webapp_url(settings_obj=panel_settings),
            mock.call.build_persistent_navigation_keyboard(
                sentinel_user, sentinel_url
            ),
            mock.call.answer_incoming_message_via_runtime(
                message,
                sentinel_user,
                "به منوی اصلی بازگشتید.",
                source_key="panel-back-main-menu",
                reply_markup=sentinel_keyboard,
                set_persistent_anchor=True,
            ),
        ]
        self.assertEqual(parent.mock_calls, expected_order)

    @mock.patch("bot.handlers.panel.set_anchor")
    @mock.patch(
        "bot.handlers.panel.answer_incoming_message_via_runtime",
        new_callable=mock.AsyncMock,
    )
    @mock.patch(
        "bot.handlers.panel.build_persistent_navigation_keyboard",
        new_callable=mock.AsyncMock,
    )
    @mock.patch("bot.handlers.panel.user_facing_webapp_url")
    @mock.patch(
        "bot.handlers.panel.delete_previous_anchor",
        new_callable=mock.AsyncMock,
    )
    @mock.patch("bot.handlers.panel.configured_telegram_delivery_runtime")
    async def test_authenticated_user_legacy_path(
        self,
        mock_configured_runtime: mock.MagicMock,
        mock_delete_previous_anchor: mock.AsyncMock,
        mock_user_facing_webapp_url: mock.MagicMock,
        mock_build_persistent_navigation_keyboard: mock.AsyncMock,
        mock_answer_incoming_message_via_runtime: mock.AsyncMock,
        mock_set_anchor: mock.MagicMock,
    ) -> None:
        """LEGACY mode deletes previous anchor, builds keyboard, answers, and sets anchor."""
        parent = mock.MagicMock(name="parent_order")
        parent.attach_mock(
            mock_configured_runtime, "configured_telegram_delivery_runtime"
        )
        parent.attach_mock(
            mock_delete_previous_anchor, "delete_previous_anchor"
        )
        parent.attach_mock(
            mock_user_facing_webapp_url, "user_facing_webapp_url"
        )
        parent.attach_mock(
            mock_build_persistent_navigation_keyboard,
            "build_persistent_navigation_keyboard",
        )
        parent.attach_mock(
            mock_answer_incoming_message_via_runtime,
            "answer_incoming_message_via_runtime",
        )
        parent.attach_mock(mock_set_anchor, "set_anchor")

        sentinel_bot = object()
        sentinel_chat_id = object()
        sentinel_user = object()
        sentinel_url = "https://legacy.test.example/app"
        sentinel_keyboard = object()
        sentinel_anchor_id = object()
        sentinel_anchor_message = mock.MagicMock(name="sentinel_anchor_message")
        sentinel_anchor_message.message_id = sentinel_anchor_id

        message = mock.MagicMock(name="legacy_message")
        message.bot = sentinel_bot
        message.chat.id = sentinel_chat_id
        state = mock.MagicMock(name="legacy_state")

        runtime_decision = TelegramDeliveryRuntimeDecision(
            mode=TelegramDeliveryRuntimeMode.LEGACY,
            legacy_workers_enabled=True,
            queue_worker_enabled=False,
        )
        mock_configured_runtime.return_value = runtime_decision
        mock_user_facing_webapp_url.return_value = sentinel_url
        mock_build_persistent_navigation_keyboard.return_value = sentinel_keyboard
        mock_answer_incoming_message_via_runtime.return_value = (
            sentinel_anchor_message
        )

        result = await handle_back_to_main_menu(message, state, sentinel_user)

        self.assertIsNone(result)

        # Assert single-call cardinality and exact arguments
        mock_configured_runtime.assert_called_once_with()

        mock_delete_previous_anchor.assert_awaited_once_with(
            sentinel_bot,
            sentinel_chat_id,
            delay=DeleteDelay.DEFAULT.value,
        )
        self.assertIs(
            mock_delete_previous_anchor.call_args[0][0], sentinel_bot
        )
        self.assertIs(
            mock_delete_previous_anchor.call_args[0][1], sentinel_chat_id
        )
        self.assertEqual(
            mock_delete_previous_anchor.call_args.kwargs["delay"],
            DeleteDelay.DEFAULT.value,
        )

        mock_user_facing_webapp_url.assert_called_once_with(
            settings_obj=panel_settings
        )
        self.assertIs(
            mock_user_facing_webapp_url.call_args.kwargs["settings_obj"],
            panel_settings,
        )

        mock_build_persistent_navigation_keyboard.assert_awaited_once_with(
            sentinel_user,
            sentinel_url,
        )
        self.assertIs(
            mock_build_persistent_navigation_keyboard.call_args[0][0],
            sentinel_user,
        )
        self.assertIs(
            mock_build_persistent_navigation_keyboard.call_args[0][1],
            sentinel_url,
        )

        mock_answer_incoming_message_via_runtime.assert_awaited_once_with(
            message,
            sentinel_user,
            "به منوی اصلی بازگشتید.",
            source_key="panel-back-main-menu",
            reply_markup=sentinel_keyboard,
            set_persistent_anchor=True,
        )
        self.assertIs(
            mock_answer_incoming_message_via_runtime.call_args[0][0], message
        )
        self.assertIs(
            mock_answer_incoming_message_via_runtime.call_args[0][1],
            sentinel_user,
        )
        self.assertEqual(
            mock_answer_incoming_message_via_runtime.call_args[0][2],
            "به منوی اصلی بازگشتید.",
        )
        self.assertEqual(
            mock_answer_incoming_message_via_runtime.call_args.kwargs[
                "source_key"
            ],
            "panel-back-main-menu",
        )
        self.assertIs(
            mock_answer_incoming_message_via_runtime.call_args.kwargs[
                "reply_markup"
            ],
            sentinel_keyboard,
        )
        self.assertIs(
            mock_answer_incoming_message_via_runtime.call_args.kwargs[
                "set_persistent_anchor"
            ],
            True,
        )

        mock_set_anchor.assert_called_once_with(
            sentinel_chat_id, sentinel_anchor_id
        )
        self.assertIs(mock_set_anchor.call_args[0][0], sentinel_chat_id)
        self.assertIs(mock_set_anchor.call_args[0][1], sentinel_anchor_id)

        expected_order = [
            mock.call.configured_telegram_delivery_runtime(),
            mock.call.delete_previous_anchor(
                sentinel_bot,
                sentinel_chat_id,
                delay=DeleteDelay.DEFAULT.value,
            ),
            mock.call.user_facing_webapp_url(settings_obj=panel_settings),
            mock.call.build_persistent_navigation_keyboard(
                sentinel_user, sentinel_url
            ),
            mock.call.answer_incoming_message_via_runtime(
                message,
                sentinel_user,
                "به منوی اصلی بازگشتید.",
                source_key="panel-back-main-menu",
                reply_markup=sentinel_keyboard,
                set_persistent_anchor=True,
            ),
            mock.call.set_anchor(sentinel_chat_id, sentinel_anchor_id),
        ]
        self.assertEqual(parent.mock_calls, expected_order)


if __name__ == "__main__":
    unittest.main()
