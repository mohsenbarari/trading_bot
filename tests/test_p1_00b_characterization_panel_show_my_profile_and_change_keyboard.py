"""Direct hermetic characterization for show_my_profile_and_change_keyboard handler.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Seed: BOT-TG-MESSAGE-PANEL-SHOW-MY-PROFILE-AND-CHANGE-KEYBOARD-L210
Gap: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

Disclaimer:
This test module provides bounded characterization evidence for current behavior.
It explicitly does not update the seed registry, reduce canonical blockers, close the
seed BOT-TG-MESSAGE-PANEL-SHOW-MY-PROFILE-AND-CHANGE-KEYBOARD-L210, or close Stage P1-00b.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
import unittest
from unittest import mock

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from bot.handlers.panel import show_my_profile_and_change_keyboard
from bot.message_manager import DeleteDelay
from bot.utils.customer_display import user_display_name
from core.config import settings
from core.enums import UserRole
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeDecision,
    TelegramDeliveryRuntimeMode,
)


class StrictFakeAsyncSessionContext:
    """Hermetic async session context manager for AsyncSessionLocal."""

    def __init__(self, session_obj: Any) -> None:
        self.session_obj = session_obj
        self.entered = 0
        self.exited = 0

    async def __aenter__(self) -> Any:
        self.entered += 1
        return self.session_obj

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        self.exited += 1
        return None


class TestPanelShowMyProfileAndChangeKeyboardCharacterization(
    unittest.IsolatedAsyncioTestCase
):
    """Characterize show_my_profile_and_change_keyboard behavior.

    Disclaimer:
    This test suite provides bounded characterization evidence only and does not update
    the seed registry, reduce canonical blockers, close the handler seed, or close Stage P1-00b.
    """

    @mock.patch("bot.handlers.panel.set_anchor")
    @mock.patch(
        "bot.handlers.panel.answer_incoming_message_via_runtime",
        new_callable=mock.AsyncMock,
    )
    @mock.patch(
        "bot.handlers.panel.build_user_panel_navigation_keyboard",
        new_callable=mock.AsyncMock,
    )
    @mock.patch(
        "bot.handlers.panel._user_panel_show_overtime_preference",
        new_callable=mock.AsyncMock,
    )
    @mock.patch(
        "bot.handlers.panel._can_use_customer_panel",
        new_callable=mock.AsyncMock,
    )
    @mock.patch("bot.handlers.panel.AsyncSessionLocal")
    @mock.patch(
        "bot.handlers.panel.delete_previous_anchor",
        new_callable=mock.AsyncMock,
    )
    @mock.patch("bot.handlers.panel.configured_telegram_delivery_runtime")
    @mock.patch("bot.handlers.panel.is_user_global_web_locked")
    async def test_locked_account_response_returns_none_and_answers_runtime(
        self,
        mock_is_user_global_web_locked: mock.MagicMock,
        mock_configured_runtime: mock.MagicMock,
        mock_delete_previous_anchor: mock.AsyncMock,
        mock_async_session_local: mock.MagicMock,
        mock_can_use_customer_panel: mock.AsyncMock,
        mock_user_panel_show_overtime_preference: mock.AsyncMock,
        mock_build_user_panel_navigation_keyboard: mock.AsyncMock,
        mock_answer_incoming_message_via_runtime: mock.AsyncMock,
        mock_set_anchor: mock.MagicMock,
    ) -> None:
        """Locked-account branch returns None and sends locked notification via runtime."""
        parent = mock.MagicMock(name="parent_order")
        parent.attach_mock(
            mock_is_user_global_web_locked, "is_user_global_web_locked"
        )
        parent.attach_mock(
            mock_configured_runtime, "configured_telegram_delivery_runtime"
        )
        parent.attach_mock(
            mock_delete_previous_anchor, "delete_previous_anchor"
        )
        parent.attach_mock(
            mock_async_session_local, "AsyncSessionLocal"
        )
        parent.attach_mock(
            mock_can_use_customer_panel, "_can_use_customer_panel"
        )
        parent.attach_mock(
            mock_user_panel_show_overtime_preference,
            "_user_panel_show_overtime_preference",
        )
        parent.attach_mock(
            mock_build_user_panel_navigation_keyboard,
            "build_user_panel_navigation_keyboard",
        )
        parent.attach_mock(
            mock_answer_incoming_message_via_runtime,
            "answer_incoming_message_via_runtime",
        )
        parent.attach_mock(mock_set_anchor, "set_anchor")

        user = SimpleNamespace(
            id=42,
            role=UserRole.STANDARD,
            full_name="Locked Standard User",
            telegram_id=12345678,
        )
        message = mock.MagicMock(name="locked_message")
        state = mock.MagicMock(name="locked_state")

        mock_is_user_global_web_locked.return_value = True

        result = await show_my_profile_and_change_keyboard(message, state, user)

        self.assertIsNone(result)

        # Single call verification on lock check
        mock_is_user_global_web_locked.assert_called_once_with(user)
        self.assertIs(mock_is_user_global_web_locked.call_args[0][0], user)

        # Runtime reply assertion
        mock_answer_incoming_message_via_runtime.assert_awaited_once_with(
            message,
            user,
            "دسترسی شما به دلیل غیرفعال بودن حساب بسته شده است.",
            source_key="panel-profile-account-locked",
        )
        self.assertIs(
            mock_answer_incoming_message_via_runtime.call_args[0][0], message
        )
        self.assertIs(
            mock_answer_incoming_message_via_runtime.call_args[0][1], user
        )
        self.assertEqual(
            mock_answer_incoming_message_via_runtime.call_args[0][2],
            "دسترسی شما به دلیل غیرفعال بودن حساب بسته شده است.",
        )
        self.assertEqual(
            mock_answer_incoming_message_via_runtime.call_args.kwargs["source_key"],
            "panel-profile-account-locked",
        )

        # Negative assertions: early exit before runtime query, anchors, DB or keyboards
        mock_configured_runtime.assert_not_called()
        mock_delete_previous_anchor.assert_not_called()
        self.assertEqual(mock_delete_previous_anchor.await_count, 0)
        mock_async_session_local.assert_not_called()
        mock_can_use_customer_panel.assert_not_called()
        self.assertEqual(mock_can_use_customer_panel.await_count, 0)
        mock_user_panel_show_overtime_preference.assert_not_called()
        self.assertEqual(mock_user_panel_show_overtime_preference.await_count, 0)
        mock_build_user_panel_navigation_keyboard.assert_not_called()
        self.assertEqual(mock_build_user_panel_navigation_keyboard.await_count, 0)
        mock_set_anchor.assert_not_called()

        expected_order = [
            mock.call.is_user_global_web_locked(user),
            mock.call.answer_incoming_message_via_runtime(
                message,
                user,
                "دسترسی شما به دلیل غیرفعال بودن حساب بسته شده است.",
                source_key="panel-profile-account-locked",
            ),
        ]
        self.assertEqual(parent.mock_calls, expected_order)

    @mock.patch("bot.handlers.panel.set_anchor")
    @mock.patch(
        "bot.handlers.panel.answer_incoming_message_via_runtime",
        new_callable=mock.AsyncMock,
    )
    @mock.patch(
        "bot.handlers.panel.build_user_panel_navigation_keyboard",
        new_callable=mock.AsyncMock,
    )
    @mock.patch(
        "bot.handlers.panel._user_panel_show_overtime_preference",
        new_callable=mock.AsyncMock,
    )
    @mock.patch(
        "bot.handlers.panel._can_use_customer_panel",
        new_callable=mock.AsyncMock,
    )
    @mock.patch("bot.handlers.panel.AsyncSessionLocal")
    @mock.patch(
        "bot.handlers.panel.delete_previous_anchor",
        new_callable=mock.AsyncMock,
    )
    @mock.patch("bot.handlers.panel.configured_telegram_delivery_runtime")
    @mock.patch("bot.handlers.panel.is_user_global_web_locked")
    async def test_eligible_standard_user_customer_menu_queue_v1(
        self,
        mock_is_user_global_web_locked: mock.MagicMock,
        mock_configured_runtime: mock.MagicMock,
        mock_delete_previous_anchor: mock.AsyncMock,
        mock_async_session_local: mock.MagicMock,
        mock_can_use_customer_panel: mock.AsyncMock,
        mock_user_panel_show_overtime_preference: mock.AsyncMock,
        mock_build_user_panel_navigation_keyboard: mock.AsyncMock,
        mock_answer_incoming_message_via_runtime: mock.AsyncMock,
        mock_set_anchor: mock.MagicMock,
    ) -> None:
        """QUEUE_V1 mode dispatches durable customer menu and skips anchor mutations."""
        parent = mock.MagicMock(name="parent_order")
        parent.attach_mock(
            mock_is_user_global_web_locked, "is_user_global_web_locked"
        )
        parent.attach_mock(
            mock_configured_runtime, "configured_telegram_delivery_runtime"
        )
        parent.attach_mock(
            mock_delete_previous_anchor, "delete_previous_anchor"
        )
        parent.attach_mock(
            mock_async_session_local, "AsyncSessionLocal"
        )
        parent.attach_mock(
            mock_can_use_customer_panel, "_can_use_customer_panel"
        )
        parent.attach_mock(
            mock_user_panel_show_overtime_preference,
            "_user_panel_show_overtime_preference",
        )
        parent.attach_mock(
            mock_build_user_panel_navigation_keyboard,
            "build_user_panel_navigation_keyboard",
        )
        parent.attach_mock(
            mock_answer_incoming_message_via_runtime,
            "answer_incoming_message_via_runtime",
        )
        parent.attach_mock(mock_set_anchor, "set_anchor")

        sentinel_bot = object()
        sentinel_chat_id = 112233
        message = mock.MagicMock(name="queue_message")
        message.bot = sentinel_bot
        message.chat.id = sentinel_chat_id
        state = mock.MagicMock(name="queue_state")

        user = SimpleNamespace(
            id=101,
            role=UserRole.STANDARD,
            full_name="Queue Standard User",
            telegram_id=987654321,
        )

        sentinel_session = object()
        fake_session_ctx = StrictFakeAsyncSessionContext(sentinel_session)
        sentinel_keyboard = object()
        sentinel_anchor_id = 55501
        sentinel_anchor_msg = mock.MagicMock(name="sentinel_anchor_msg")
        sentinel_anchor_msg.message_id = sentinel_anchor_id

        mock_is_user_global_web_locked.return_value = False
        mock_configured_runtime.return_value = TelegramDeliveryRuntimeDecision(
            mode=TelegramDeliveryRuntimeMode.QUEUE_V1,
            legacy_workers_enabled=False,
            queue_worker_enabled=True,
        )
        mock_async_session_local.return_value = fake_session_ctx
        mock_can_use_customer_panel.return_value = True
        mock_user_panel_show_overtime_preference.return_value = True
        mock_build_user_panel_navigation_keyboard.return_value = sentinel_keyboard
        mock_answer_incoming_message_via_runtime.return_value = sentinel_anchor_msg

        result = await show_my_profile_and_change_keyboard(message, state, user)

        self.assertIsNone(result)

        # Check call cardinality and arguments
        mock_is_user_global_web_locked.assert_called_once_with(user)
        self.assertIs(mock_is_user_global_web_locked.call_args[0][0], user)

        mock_configured_runtime.assert_called_once_with()

        mock_async_session_local.assert_called_once_with()
        self.assertEqual(fake_session_ctx.entered, 1)
        self.assertEqual(fake_session_ctx.exited, 1)

        mock_can_use_customer_panel.assert_awaited_once_with(sentinel_session, user)
        self.assertIs(mock_can_use_customer_panel.call_args[0][0], sentinel_session)
        self.assertIs(mock_can_use_customer_panel.call_args[0][1], user)

        mock_user_panel_show_overtime_preference.assert_awaited_once_with(user)
        self.assertIs(mock_user_panel_show_overtime_preference.call_args[0][0], user)

        mock_build_user_panel_navigation_keyboard.assert_awaited_once_with(
            user,
            standard_actions=True,
            show_support=True,
            show_overtime_preference=True,
        )
        self.assertIs(
            mock_build_user_panel_navigation_keyboard.call_args[0][0], user
        )
        self.assertEqual(
            mock_build_user_panel_navigation_keyboard.call_args.kwargs,
            {
                "standard_actions": True,
                "show_support": True,
                "show_overtime_preference": True,
            },
        )

        mock_answer_incoming_message_via_runtime.assert_awaited_once_with(
            message,
            user,
            "👤 **پنل کاربر**\n\nگزینه مورد نظر را انتخاب کنید:",
            source_key="panel-user-main-menu",
            parse_mode="Markdown",
            reply_markup=sentinel_keyboard,
            set_persistent_anchor=True,
        )
        self.assertIs(
            mock_answer_incoming_message_via_runtime.call_args[0][0], message
        )
        self.assertIs(
            mock_answer_incoming_message_via_runtime.call_args[0][1], user
        )
        self.assertEqual(
            mock_answer_incoming_message_via_runtime.call_args[0][2],
            "👤 **پنل کاربر**\n\nگزینه مورد نظر را انتخاب کنید:",
        )
        self.assertEqual(
            mock_answer_incoming_message_via_runtime.call_args.kwargs["source_key"],
            "panel-user-main-menu",
        )
        self.assertEqual(
            mock_answer_incoming_message_via_runtime.call_args.kwargs["parse_mode"],
            "Markdown",
        )
        self.assertIs(
            mock_answer_incoming_message_via_runtime.call_args.kwargs["reply_markup"],
            sentinel_keyboard,
        )
        self.assertIs(
            mock_answer_incoming_message_via_runtime.call_args.kwargs[
                "set_persistent_anchor"
            ],
            True,
        )

        # Negative assertions: queue mode skips in-memory anchor ops
        mock_delete_previous_anchor.assert_not_called()
        self.assertEqual(mock_delete_previous_anchor.await_count, 0)
        mock_set_anchor.assert_not_called()

        expected_order = [
            mock.call.is_user_global_web_locked(user),
            mock.call.configured_telegram_delivery_runtime(),
            mock.call.AsyncSessionLocal(),
            mock.call._can_use_customer_panel(sentinel_session, user),
            mock.call._user_panel_show_overtime_preference(user),
            mock.call.build_user_panel_navigation_keyboard(
                user,
                standard_actions=True,
                show_support=True,
                show_overtime_preference=True,
            ),
            mock.call.answer_incoming_message_via_runtime(
                message,
                user,
                "👤 **پنل کاربر**\n\nگزینه مورد نظر را انتخاب کنید:",
                source_key="panel-user-main-menu",
                parse_mode="Markdown",
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
        "bot.handlers.panel.build_user_panel_navigation_keyboard",
        new_callable=mock.AsyncMock,
    )
    @mock.patch(
        "bot.handlers.panel._user_panel_show_overtime_preference",
        new_callable=mock.AsyncMock,
    )
    @mock.patch(
        "bot.handlers.panel._can_use_customer_panel",
        new_callable=mock.AsyncMock,
    )
    @mock.patch("bot.handlers.panel.AsyncSessionLocal")
    @mock.patch(
        "bot.handlers.panel.delete_previous_anchor",
        new_callable=mock.AsyncMock,
    )
    @mock.patch("bot.handlers.panel.configured_telegram_delivery_runtime")
    @mock.patch("bot.handlers.panel.is_user_global_web_locked")
    async def test_eligible_standard_user_customer_menu_legacy(
        self,
        mock_is_user_global_web_locked: mock.MagicMock,
        mock_configured_runtime: mock.MagicMock,
        mock_delete_previous_anchor: mock.AsyncMock,
        mock_async_session_local: mock.MagicMock,
        mock_can_use_customer_panel: mock.AsyncMock,
        mock_user_panel_show_overtime_preference: mock.AsyncMock,
        mock_build_user_panel_navigation_keyboard: mock.AsyncMock,
        mock_answer_incoming_message_via_runtime: mock.AsyncMock,
        mock_set_anchor: mock.MagicMock,
    ) -> None:
        """LEGACY mode deletes previous anchor, builds customer menu, and sets anchor."""
        parent = mock.MagicMock(name="parent_order")
        parent.attach_mock(
            mock_is_user_global_web_locked, "is_user_global_web_locked"
        )
        parent.attach_mock(
            mock_configured_runtime, "configured_telegram_delivery_runtime"
        )
        parent.attach_mock(
            mock_delete_previous_anchor, "delete_previous_anchor"
        )
        parent.attach_mock(
            mock_async_session_local, "AsyncSessionLocal"
        )
        parent.attach_mock(
            mock_can_use_customer_panel, "_can_use_customer_panel"
        )
        parent.attach_mock(
            mock_user_panel_show_overtime_preference,
            "_user_panel_show_overtime_preference",
        )
        parent.attach_mock(
            mock_build_user_panel_navigation_keyboard,
            "build_user_panel_navigation_keyboard",
        )
        parent.attach_mock(
            mock_answer_incoming_message_via_runtime,
            "answer_incoming_message_via_runtime",
        )
        parent.attach_mock(mock_set_anchor, "set_anchor")

        sentinel_bot = object()
        sentinel_chat_id = 778899
        message = mock.MagicMock(name="legacy_message")
        message.bot = sentinel_bot
        message.chat.id = sentinel_chat_id
        state = mock.MagicMock(name="legacy_state")

        user = SimpleNamespace(
            id=202,
            role=UserRole.STANDARD,
            full_name="Legacy Standard User",
            telegram_id=555666777,
        )

        sentinel_session = object()
        fake_session_ctx = StrictFakeAsyncSessionContext(sentinel_session)
        sentinel_keyboard = object()
        sentinel_anchor_id = 77701
        sentinel_anchor_msg = mock.MagicMock(name="sentinel_anchor_msg")
        sentinel_anchor_msg.message_id = sentinel_anchor_id

        mock_is_user_global_web_locked.return_value = False
        mock_configured_runtime.return_value = TelegramDeliveryRuntimeDecision(
            mode=TelegramDeliveryRuntimeMode.LEGACY,
            legacy_workers_enabled=True,
            queue_worker_enabled=False,
        )
        mock_async_session_local.return_value = fake_session_ctx
        mock_can_use_customer_panel.return_value = True
        # Distinct overtime-preference result from QUEUE_V1 scenario
        mock_user_panel_show_overtime_preference.return_value = False
        mock_build_user_panel_navigation_keyboard.return_value = sentinel_keyboard
        mock_answer_incoming_message_via_runtime.return_value = sentinel_anchor_msg

        result = await show_my_profile_and_change_keyboard(message, state, user)

        self.assertIsNone(result)

        # Assert calls, arguments and cardinality
        mock_is_user_global_web_locked.assert_called_once_with(user)
        self.assertIs(mock_is_user_global_web_locked.call_args[0][0], user)

        mock_configured_runtime.assert_called_once_with()

        mock_delete_previous_anchor.assert_awaited_once_with(
            sentinel_bot,
            sentinel_chat_id,
            delay=DeleteDelay.DEFAULT.value,
        )
        self.assertIs(mock_delete_previous_anchor.call_args[0][0], sentinel_bot)
        self.assertEqual(mock_delete_previous_anchor.call_args[0][1], sentinel_chat_id)
        self.assertEqual(
            mock_delete_previous_anchor.call_args.kwargs["delay"],
            DeleteDelay.DEFAULT.value,
        )

        mock_async_session_local.assert_called_once_with()
        self.assertEqual(fake_session_ctx.entered, 1)
        self.assertEqual(fake_session_ctx.exited, 1)

        mock_can_use_customer_panel.assert_awaited_once_with(sentinel_session, user)
        self.assertIs(mock_can_use_customer_panel.call_args[0][0], sentinel_session)
        self.assertIs(mock_can_use_customer_panel.call_args[0][1], user)

        mock_user_panel_show_overtime_preference.assert_awaited_once_with(user)
        self.assertIs(mock_user_panel_show_overtime_preference.call_args[0][0], user)

        mock_build_user_panel_navigation_keyboard.assert_awaited_once_with(
            user,
            standard_actions=True,
            show_support=True,
            show_overtime_preference=False,
        )
        self.assertIs(
            mock_build_user_panel_navigation_keyboard.call_args[0][0], user
        )
        self.assertEqual(
            mock_build_user_panel_navigation_keyboard.call_args.kwargs,
            {
                "standard_actions": True,
                "show_support": True,
                "show_overtime_preference": False,
            },
        )

        mock_answer_incoming_message_via_runtime.assert_awaited_once_with(
            message,
            user,
            "👤 **پنل کاربر**\n\nگزینه مورد نظر را انتخاب کنید:",
            source_key="panel-user-main-menu",
            parse_mode="Markdown",
            reply_markup=sentinel_keyboard,
            set_persistent_anchor=True,
        )
        self.assertIs(
            mock_answer_incoming_message_via_runtime.call_args[0][0], message
        )
        self.assertIs(
            mock_answer_incoming_message_via_runtime.call_args[0][1], user
        )
        self.assertEqual(
            mock_answer_incoming_message_via_runtime.call_args[0][2],
            "👤 **پنل کاربر**\n\nگزینه مورد نظر را انتخاب کنید:",
        )
        self.assertEqual(
            mock_answer_incoming_message_via_runtime.call_args.kwargs["source_key"],
            "panel-user-main-menu",
        )
        self.assertEqual(
            mock_answer_incoming_message_via_runtime.call_args.kwargs["parse_mode"],
            "Markdown",
        )
        self.assertIs(
            mock_answer_incoming_message_via_runtime.call_args.kwargs["reply_markup"],
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
        self.assertEqual(mock_set_anchor.call_args[0][0], sentinel_chat_id)
        self.assertEqual(mock_set_anchor.call_args[0][1], sentinel_anchor_id)

        expected_order = [
            mock.call.is_user_global_web_locked(user),
            mock.call.configured_telegram_delivery_runtime(),
            mock.call.delete_previous_anchor(
                sentinel_bot,
                sentinel_chat_id,
                delay=DeleteDelay.DEFAULT.value,
            ),
            mock.call.AsyncSessionLocal(),
            mock.call._can_use_customer_panel(sentinel_session, user),
            mock.call._user_panel_show_overtime_preference(user),
            mock.call.build_user_panel_navigation_keyboard(
                user,
                standard_actions=True,
                show_support=True,
                show_overtime_preference=False,
            ),
            mock.call.answer_incoming_message_via_runtime(
                message,
                user,
                "👤 **پنل کاربر**\n\nگزینه مورد نظر را انتخاب کنید:",
                source_key="panel-user-main-menu",
                parse_mode="Markdown",
                reply_markup=sentinel_keyboard,
                set_persistent_anchor=True,
            ),
            mock.call.set_anchor(sentinel_chat_id, sentinel_anchor_id),
        ]
        self.assertEqual(parent.mock_calls, expected_order)

    @mock.patch("bot.handlers.panel.set_anchor")
    @mock.patch(
        "bot.handlers.panel.answer_incoming_message_via_runtime",
        new_callable=mock.AsyncMock,
    )
    @mock.patch(
        "bot.handlers.panel.build_user_panel_navigation_keyboard",
        new_callable=mock.AsyncMock,
    )
    @mock.patch(
        "bot.handlers.panel.attach_customer_management_names",
        new_callable=mock.AsyncMock,
    )
    @mock.patch(
        "bot.handlers.panel._user_panel_show_overtime_preference",
        new_callable=mock.AsyncMock,
    )
    @mock.patch(
        "bot.handlers.panel._can_use_customer_panel",
        new_callable=mock.AsyncMock,
    )
    @mock.patch("bot.handlers.panel.AsyncSessionLocal")
    @mock.patch(
        "bot.handlers.panel.delete_previous_anchor",
        new_callable=mock.AsyncMock,
    )
    @mock.patch("bot.handlers.panel.configured_telegram_delivery_runtime")
    @mock.patch("bot.handlers.panel.is_user_global_web_locked")
    async def test_non_customer_user_profile_menu_queue_v1(
        self,
        mock_is_user_global_web_locked: mock.MagicMock,
        mock_configured_runtime: mock.MagicMock,
        mock_delete_previous_anchor: mock.AsyncMock,
        mock_async_session_local: mock.MagicMock,
        mock_can_use_customer_panel: mock.AsyncMock,
        mock_user_panel_show_overtime_preference: mock.AsyncMock,
        mock_attach_customer_management_names: mock.AsyncMock,
        mock_build_user_panel_navigation_keyboard: mock.AsyncMock,
        mock_answer_incoming_message_via_runtime: mock.AsyncMock,
        mock_set_anchor: mock.MagicMock,
    ) -> None:
        """QUEUE_V1 mode enriches non-customer profile and skips anchor mutations."""
        parent = mock.MagicMock(name="parent_order")
        parent.attach_mock(
            mock_is_user_global_web_locked, "is_user_global_web_locked"
        )
        parent.attach_mock(
            mock_configured_runtime, "configured_telegram_delivery_runtime"
        )
        parent.attach_mock(
            mock_delete_previous_anchor, "delete_previous_anchor"
        )
        parent.attach_mock(
            mock_async_session_local, "AsyncSessionLocal"
        )
        parent.attach_mock(
            mock_can_use_customer_panel, "_can_use_customer_panel"
        )
        parent.attach_mock(
            mock_user_panel_show_overtime_preference,
            "_user_panel_show_overtime_preference",
        )
        parent.attach_mock(
            mock_attach_customer_management_names,
            "attach_customer_management_names",
        )
        parent.attach_mock(
            mock_build_user_panel_navigation_keyboard,
            "build_user_panel_navigation_keyboard",
        )
        parent.attach_mock(
            mock_answer_incoming_message_via_runtime,
            "answer_incoming_message_via_runtime",
        )
        parent.attach_mock(mock_set_anchor, "set_anchor")

        sentinel_bot = object()
        sentinel_chat_id = 334455
        message = mock.MagicMock(name="queue_noncustomer_message")
        message.bot = sentinel_bot
        message.chat.id = sentinel_chat_id
        state = mock.MagicMock(name="queue_noncustomer_state")

        user = SimpleNamespace(
            id=301,
            role=UserRole.STANDARD,
            account_name="queue_std_account",
            full_name="Queue NonCustomer Standard User",
            telegram_id=987123456,
        )

        sentinel_session_1 = object()
        sentinel_session_2 = object()
        fake_session_ctx_1 = StrictFakeAsyncSessionContext(sentinel_session_1)
        fake_session_ctx_2 = StrictFakeAsyncSessionContext(sentinel_session_2)

        sentinel_keyboard = object()
        sentinel_anchor_id = 66501
        sentinel_anchor_msg = mock.MagicMock(name="sentinel_anchor_msg")
        sentinel_anchor_msg.message_id = sentinel_anchor_id

        enriched_management_name = "مشتری نقره‌ای اختصاصی کیو"

        async def fake_attach(session_arg: Any, users_arg: Any) -> None:
            for u in users_arg:
                setattr(u, "customer_management_name", enriched_management_name)

        mock_attach_customer_management_names.side_effect = fake_attach

        mock_is_user_global_web_locked.return_value = False
        mock_configured_runtime.return_value = TelegramDeliveryRuntimeDecision(
            mode=TelegramDeliveryRuntimeMode.QUEUE_V1,
            legacy_workers_enabled=False,
            queue_worker_enabled=True,
        )
        mock_async_session_local.side_effect = [
            fake_session_ctx_1,
            fake_session_ctx_2,
        ]
        mock_can_use_customer_panel.return_value = False
        mock_user_panel_show_overtime_preference.side_effect = [False, True]
        mock_build_user_panel_navigation_keyboard.return_value = sentinel_keyboard
        mock_answer_incoming_message_via_runtime.return_value = sentinel_anchor_msg

        self.assertIsNone(getattr(user, "customer_management_name", None))
        self.assertEqual(user_display_name(user), "queue_std_account")

        result = await show_my_profile_and_change_keyboard(message, state, user)

        self.assertIsNone(result)

        mock_is_user_global_web_locked.assert_called_once_with(user)
        self.assertIs(mock_is_user_global_web_locked.call_args[0][0], user)
        mock_configured_runtime.assert_called_once_with()

        self.assertEqual(mock_async_session_local.call_count, 2)
        self.assertEqual(fake_session_ctx_1.entered, 1)
        self.assertEqual(fake_session_ctx_1.exited, 1)
        self.assertEqual(fake_session_ctx_2.entered, 1)
        self.assertEqual(fake_session_ctx_2.exited, 1)

        mock_can_use_customer_panel.assert_awaited_once_with(
            sentinel_session_1, user
        )
        self.assertIs(
            mock_can_use_customer_panel.call_args[0][0], sentinel_session_1
        )
        self.assertIs(mock_can_use_customer_panel.call_args[0][1], user)

        mock_attach_customer_management_names.assert_awaited_once_with(
            sentinel_session_2, [user]
        )
        self.assertIs(
            mock_attach_customer_management_names.call_args[0][0],
            sentinel_session_2,
        )
        self.assertEqual(
            mock_attach_customer_management_names.call_args[0][1], [user]
        )
        self.assertIs(
            mock_attach_customer_management_names.call_args[0][1][0], user
        )

        self.assertEqual(
            getattr(user, "customer_management_name", None),
            enriched_management_name,
        )
        self.assertEqual(user_display_name(user), enriched_management_name)

        self.assertEqual(mock_user_panel_show_overtime_preference.await_count, 2)
        mock_user_panel_show_overtime_preference.assert_has_awaits(
            [mock.call(user), mock.call(user)]
        )
        mock_build_user_panel_navigation_keyboard.assert_awaited_once_with(
            user,
            show_overtime_preference=True,
        )
        self.assertIs(
            mock_build_user_panel_navigation_keyboard.call_args[0][0], user
        )
        self.assertEqual(
            mock_build_user_panel_navigation_keyboard.call_args.kwargs,
            {"show_overtime_preference": True},
        )
        self.assertNotIn(
            "standard_actions",
            mock_build_user_panel_navigation_keyboard.call_args.kwargs,
        )
        self.assertNotIn(
            "show_support",
            mock_build_user_panel_navigation_keyboard.call_args.kwargs,
        )

        expected_profile_link = (
            f"https://t.me/{settings.bot_username}?start=profile_{user.id}"
        )
        expected_profile_text = (
            "👤 **پروفایل شما**\n\n"
            f"🔸 **نام کاربری:** `{enriched_management_name}`\n"
            f"🔹 **نام تلگرام:** {user.full_name}\n"
            f"🔹 **آیدی تلگرام:** `{user.telegram_id}`\n"
            f"🔹 **سطح دسترسی:** {user.role.value}\n\n"
            "🔗 **لینک پروفایل عمومی:**\n"
            f"`{expected_profile_link}`"
        )
        mock_answer_incoming_message_via_runtime.assert_awaited_once_with(
            message,
            user,
            expected_profile_text,
            source_key="panel-user-profile-menu",
            parse_mode="Markdown",
            reply_markup=sentinel_keyboard,
            set_persistent_anchor=True,
        )
        self.assertIs(
            mock_answer_incoming_message_via_runtime.call_args[0][0], message
        )
        self.assertIs(
            mock_answer_incoming_message_via_runtime.call_args[0][1], user
        )
        self.assertEqual(
            mock_answer_incoming_message_via_runtime.call_args[0][2],
            expected_profile_text,
        )
        self.assertEqual(
            mock_answer_incoming_message_via_runtime.call_args.kwargs[
                "source_key"
            ],
            "panel-user-profile-menu",
        )
        self.assertEqual(
            mock_answer_incoming_message_via_runtime.call_args.kwargs[
                "parse_mode"
            ],
            "Markdown",
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

        mock_delete_previous_anchor.assert_not_called()
        self.assertEqual(mock_delete_previous_anchor.await_count, 0)
        mock_set_anchor.assert_not_called()

        expected_order = [
            mock.call.is_user_global_web_locked(user),
            mock.call.configured_telegram_delivery_runtime(),
            mock.call.AsyncSessionLocal(),
            mock.call._can_use_customer_panel(sentinel_session_1, user),
            mock.call._user_panel_show_overtime_preference(user),
            mock.call.AsyncSessionLocal(),
            mock.call.attach_customer_management_names(
                sentinel_session_2, [user]
            ),
            mock.call._user_panel_show_overtime_preference(user),
            mock.call.build_user_panel_navigation_keyboard(
                user,
                show_overtime_preference=True,
            ),
            mock.call.answer_incoming_message_via_runtime(
                message,
                user,
                expected_profile_text,
                source_key="panel-user-profile-menu",
                parse_mode="Markdown",
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
        "bot.handlers.panel.build_user_panel_navigation_keyboard",
        new_callable=mock.AsyncMock,
    )
    @mock.patch(
        "bot.handlers.panel.attach_customer_management_names",
        new_callable=mock.AsyncMock,
    )
    @mock.patch(
        "bot.handlers.panel._user_panel_show_overtime_preference",
        new_callable=mock.AsyncMock,
    )
    @mock.patch(
        "bot.handlers.panel._can_use_customer_panel",
        new_callable=mock.AsyncMock,
    )
    @mock.patch("bot.handlers.panel.AsyncSessionLocal")
    @mock.patch(
        "bot.handlers.panel.delete_previous_anchor",
        new_callable=mock.AsyncMock,
    )
    @mock.patch("bot.handlers.panel.configured_telegram_delivery_runtime")
    @mock.patch("bot.handlers.panel.is_user_global_web_locked")
    async def test_non_customer_user_profile_menu_legacy(
        self,
        mock_is_user_global_web_locked: mock.MagicMock,
        mock_configured_runtime: mock.MagicMock,
        mock_delete_previous_anchor: mock.AsyncMock,
        mock_async_session_local: mock.MagicMock,
        mock_can_use_customer_panel: mock.AsyncMock,
        mock_user_panel_show_overtime_preference: mock.AsyncMock,
        mock_attach_customer_management_names: mock.AsyncMock,
        mock_build_user_panel_navigation_keyboard: mock.AsyncMock,
        mock_answer_incoming_message_via_runtime: mock.AsyncMock,
        mock_set_anchor: mock.MagicMock,
    ) -> None:
        """LEGACY mode deletes anchor before sessions, enriches profile, and sets anchor."""
        parent = mock.MagicMock(name="parent_order")
        parent.attach_mock(
            mock_is_user_global_web_locked, "is_user_global_web_locked"
        )
        parent.attach_mock(
            mock_configured_runtime, "configured_telegram_delivery_runtime"
        )
        parent.attach_mock(
            mock_delete_previous_anchor, "delete_previous_anchor"
        )
        parent.attach_mock(
            mock_async_session_local, "AsyncSessionLocal"
        )
        parent.attach_mock(
            mock_can_use_customer_panel, "_can_use_customer_panel"
        )
        parent.attach_mock(
            mock_user_panel_show_overtime_preference,
            "_user_panel_show_overtime_preference",
        )
        parent.attach_mock(
            mock_attach_customer_management_names,
            "attach_customer_management_names",
        )
        parent.attach_mock(
            mock_build_user_panel_navigation_keyboard,
            "build_user_panel_navigation_keyboard",
        )
        parent.attach_mock(
            mock_answer_incoming_message_via_runtime,
            "answer_incoming_message_via_runtime",
        )
        parent.attach_mock(mock_set_anchor, "set_anchor")

        sentinel_bot = object()
        sentinel_chat_id = 889900
        message = mock.MagicMock(name="legacy_noncustomer_message")
        message.bot = sentinel_bot
        message.chat.id = sentinel_chat_id
        state = mock.MagicMock(name="legacy_noncustomer_state")

        user = SimpleNamespace(
            id=402,
            role=UserRole.MIDDLE_MANAGER,
            account_name="legacy_mgr_account",
            full_name="Legacy NonCustomer Manager User",
            telegram_id=555444333,
        )

        sentinel_session_1 = object()
        sentinel_session_2 = object()
        fake_session_ctx_1 = StrictFakeAsyncSessionContext(sentinel_session_1)
        fake_session_ctx_2 = StrictFakeAsyncSessionContext(sentinel_session_2)

        sentinel_keyboard = object()
        sentinel_anchor_id = 88501
        sentinel_anchor_msg = mock.MagicMock(name="sentinel_anchor_msg")
        sentinel_anchor_msg.message_id = sentinel_anchor_id

        enriched_management_name = "مدیر بازرگانی ارشد همکار"

        async def fake_attach(session_arg: Any, users_arg: Any) -> None:
            for u in users_arg:
                setattr(u, "customer_management_name", enriched_management_name)

        mock_attach_customer_management_names.side_effect = fake_attach

        mock_is_user_global_web_locked.return_value = False
        mock_configured_runtime.return_value = TelegramDeliveryRuntimeDecision(
            mode=TelegramDeliveryRuntimeMode.LEGACY,
            legacy_workers_enabled=True,
            queue_worker_enabled=False,
        )
        mock_async_session_local.side_effect = [
            fake_session_ctx_1,
            fake_session_ctx_2,
        ]
        mock_can_use_customer_panel.return_value = False
        mock_user_panel_show_overtime_preference.side_effect = [True, False]
        mock_build_user_panel_navigation_keyboard.return_value = sentinel_keyboard
        mock_answer_incoming_message_via_runtime.return_value = sentinel_anchor_msg

        self.assertIsNone(getattr(user, "customer_management_name", None))
        self.assertEqual(user_display_name(user), "legacy_mgr_account")

        result = await show_my_profile_and_change_keyboard(message, state, user)

        self.assertIsNone(result)

        mock_is_user_global_web_locked.assert_called_once_with(user)
        self.assertIs(mock_is_user_global_web_locked.call_args[0][0], user)
        mock_configured_runtime.assert_called_once_with()

        mock_delete_previous_anchor.assert_awaited_once_with(
            sentinel_bot,
            sentinel_chat_id,
            delay=DeleteDelay.DEFAULT.value,
        )
        self.assertIs(mock_delete_previous_anchor.call_args[0][0], sentinel_bot)
        self.assertEqual(
            mock_delete_previous_anchor.call_args[0][1], sentinel_chat_id
        )
        self.assertEqual(
            mock_delete_previous_anchor.call_args.kwargs["delay"],
            DeleteDelay.DEFAULT.value,
        )

        self.assertEqual(mock_async_session_local.call_count, 2)
        self.assertEqual(fake_session_ctx_1.entered, 1)
        self.assertEqual(fake_session_ctx_1.exited, 1)
        self.assertEqual(fake_session_ctx_2.entered, 1)
        self.assertEqual(fake_session_ctx_2.exited, 1)

        mock_can_use_customer_panel.assert_awaited_once_with(
            sentinel_session_1, user
        )
        self.assertIs(
            mock_can_use_customer_panel.call_args[0][0], sentinel_session_1
        )
        self.assertIs(mock_can_use_customer_panel.call_args[0][1], user)

        mock_attach_customer_management_names.assert_awaited_once_with(
            sentinel_session_2, [user]
        )
        self.assertIs(
            mock_attach_customer_management_names.call_args[0][0],
            sentinel_session_2,
        )
        self.assertEqual(
            mock_attach_customer_management_names.call_args[0][1], [user]
        )
        self.assertIs(
            mock_attach_customer_management_names.call_args[0][1][0], user
        )

        self.assertEqual(
            getattr(user, "customer_management_name", None),
            enriched_management_name,
        )
        self.assertEqual(user_display_name(user), enriched_management_name)

        self.assertEqual(mock_user_panel_show_overtime_preference.await_count, 2)
        mock_user_panel_show_overtime_preference.assert_has_awaits(
            [mock.call(user), mock.call(user)]
        )
        mock_build_user_panel_navigation_keyboard.assert_awaited_once_with(
            user,
            show_overtime_preference=False,
        )
        self.assertIs(
            mock_build_user_panel_navigation_keyboard.call_args[0][0], user
        )
        self.assertEqual(
            mock_build_user_panel_navigation_keyboard.call_args.kwargs,
            {"show_overtime_preference": False},
        )
        self.assertNotIn(
            "standard_actions",
            mock_build_user_panel_navigation_keyboard.call_args.kwargs,
        )
        self.assertNotIn(
            "show_support",
            mock_build_user_panel_navigation_keyboard.call_args.kwargs,
        )

        expected_profile_link = (
            f"https://t.me/{settings.bot_username}?start=profile_{user.id}"
        )
        expected_profile_text = (
            "👤 **پروفایل شما**\n\n"
            f"🔸 **نام کاربری:** `{enriched_management_name}`\n"
            f"🔹 **نام تلگرام:** {user.full_name}\n"
            f"🔹 **آیدی تلگرام:** `{user.telegram_id}`\n"
            f"🔹 **سطح دسترسی:** {user.role.value}\n\n"
            "🔗 **لینک پروفایل عمومی:**\n"
            f"`{expected_profile_link}`"
        )
        mock_answer_incoming_message_via_runtime.assert_awaited_once_with(
            message,
            user,
            expected_profile_text,
            source_key="panel-user-profile-menu",
            parse_mode="Markdown",
            reply_markup=sentinel_keyboard,
            set_persistent_anchor=True,
        )
        self.assertIs(
            mock_answer_incoming_message_via_runtime.call_args[0][0], message
        )
        self.assertIs(
            mock_answer_incoming_message_via_runtime.call_args[0][1], user
        )
        self.assertEqual(
            mock_answer_incoming_message_via_runtime.call_args[0][2],
            expected_profile_text,
        )
        self.assertEqual(
            mock_answer_incoming_message_via_runtime.call_args.kwargs[
                "source_key"
            ],
            "panel-user-profile-menu",
        )
        self.assertEqual(
            mock_answer_incoming_message_via_runtime.call_args.kwargs[
                "parse_mode"
            ],
            "Markdown",
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
        self.assertEqual(mock_set_anchor.call_args[0][0], sentinel_chat_id)
        self.assertEqual(mock_set_anchor.call_args[0][1], sentinel_anchor_id)

        expected_order = [
            mock.call.is_user_global_web_locked(user),
            mock.call.configured_telegram_delivery_runtime(),
            mock.call.delete_previous_anchor(
                sentinel_bot,
                sentinel_chat_id,
                delay=DeleteDelay.DEFAULT.value,
            ),
            mock.call.AsyncSessionLocal(),
            mock.call._can_use_customer_panel(sentinel_session_1, user),
            mock.call._user_panel_show_overtime_preference(user),
            mock.call.AsyncSessionLocal(),
            mock.call.attach_customer_management_names(
                sentinel_session_2, [user]
            ),
            mock.call._user_panel_show_overtime_preference(user),
            mock.call.build_user_panel_navigation_keyboard(
                user,
                show_overtime_preference=False,
            ),
            mock.call.answer_incoming_message_via_runtime(
                message,
                user,
                expected_profile_text,
                source_key="panel-user-profile-menu",
                parse_mode="Markdown",
                reply_markup=sentinel_keyboard,
                set_persistent_anchor=True,
            ),
            mock.call.set_anchor(sentinel_chat_id, sentinel_anchor_id),
        ]
        self.assertEqual(parent.mock_calls, expected_order)


if __name__ == "__main__":
    unittest.main()
