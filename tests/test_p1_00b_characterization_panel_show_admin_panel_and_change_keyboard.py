"""Hermetic characterization tests for show_admin_panel_and_change_keyboard.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Seed: BOT-TG-MESSAGE-PANEL-SHOW-ADMIN-PANEL-AND-CHANGE-KEYBOARD-L294
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Blocks: P1-01, P1-02, P1-04, P1-06, P2-00, MKT-0, DPL-1, DOC-2, DOC-5
Gap Reference: docs/refactor/two-server/section-1/stages/P1-00b.md#work-item-2-remaining-seed-handler-assertions

Disclaimer:
This test module provides hermetic characterization assertions freezing the current
behavior of show_admin_panel_and_change_keyboard under Stage P1-00b and Assignment
WA-P1-00B-FREEZE-20260904-001. It does not claim seed closure or Stage P1-00b closure.
Stage P1-00b remains IN_PROGRESS under execution authority CODEX_FINAL_REVIEWER.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

# Install P1-00 network fence before importing any bot handler or product code
install_p1_00_network_fence()

from bot.handlers.panel import show_admin_panel_and_change_keyboard
from bot.message_manager import DeleteDelay
from core.enums import UserRole
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeDecision,
    TelegramDeliveryRuntimeMode,
)

STAGE_ID = "P1-00b"
ASSIGNMENT_ID = "WA-P1-00B-FREEZE-20260904-001"
SEED_ID = "BOT-TG-MESSAGE-PANEL-SHOW-ADMIN-PANEL-AND-CHANGE-KEYBOARD-L294"
BLOCKER_ID = "P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE"
BLOCKS = ("P1-01", "P1-02", "P1-04", "P1-06", "P2-00", "MKT-0", "DPL-1", "DOC-2", "DOC-5")
CLOSURE_CLAIMED = False


class StrictUninspectedSentinel:
    """Sentinel object that fails on any attribute access, proving no inspection occurred."""

    def __init__(self, name: str) -> None:
        self._name = name

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(
            f"StrictUninspectedSentinel({self._name!r}) was inspected: attribute {name!r} accessed"
        )


class ShowAdminPanelCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_ineligible_standard_user_returns_none_without_side_effects_or_sentinel_inspection(self):
        """Ineligible UserRole.STANDARD returns None without calling downstream helpers or inspecting message/state."""
        sentinel_message = StrictUninspectedSentinel("message")
        sentinel_state = StrictUninspectedSentinel("state")
        ineligible_user = SimpleNamespace(id=101, role=UserRole.STANDARD)

        with (
            patch("bot.handlers.panel.configured_telegram_delivery_runtime") as mock_runtime,
            patch("bot.handlers.panel.delete_previous_anchor", new=AsyncMock()) as mock_delete,
            patch("bot.handlers.panel.build_admin_panel_navigation_keyboard", new=AsyncMock()) as mock_keyboard,
            patch("bot.handlers.panel.answer_incoming_message_via_runtime", new=AsyncMock()) as mock_answer,
            patch("bot.handlers.panel.set_anchor") as mock_set,
        ):
            result = await show_admin_panel_and_change_keyboard(
                sentinel_message,
                sentinel_state,
                ineligible_user,
            )

        self.assertIsNone(result)
        mock_runtime.assert_not_called()
        mock_delete.assert_not_called()
        mock_keyboard.assert_not_called()
        mock_answer.assert_not_called()
        mock_set.assert_not_called()

    async def test_eligible_super_admin_queue_v1_freezes_response_contract_and_suppresses_anchors(self):
        """Super Admin in Queue-v1 mode executes response contract, skips anchor delete/set, leaves state unused."""
        bot_sentinel = object()
        chat_sentinel = SimpleNamespace(id=7701)
        message = SimpleNamespace(
            bot=bot_sentinel,
            chat=chat_sentinel,
            message_id=404,
        )
        sentinel_state = StrictUninspectedSentinel("state")
        admin_user = SimpleNamespace(
            id=50,
            telegram_id=7701,
            role=UserRole.SUPER_ADMIN,
        )
        runtime_decision = TelegramDeliveryRuntimeDecision(
            mode=TelegramDeliveryRuntimeMode.QUEUE_V1,
            legacy_workers_enabled=False,
            queue_worker_enabled=True,
        )
        mock_keyboard = SimpleNamespace(keyboard=[[SimpleNamespace(text="admin-panel")]])
        mock_anchor_msg = SimpleNamespace(message_id=9001)

        call_order: list[str] = []

        def tracked_runtime():
            call_order.append("configured_telegram_delivery_runtime")
            return runtime_decision

        async def tracked_build_keyboard(u):
            call_order.append("build_admin_panel_navigation_keyboard")
            return mock_keyboard

        async def tracked_answer(*args, **kwargs):
            call_order.append("answer_incoming_message_via_runtime")
            return mock_anchor_msg

        with (
            patch(
                "bot.handlers.panel.configured_telegram_delivery_runtime",
                side_effect=tracked_runtime,
            ) as mock_runtime,
            patch(
                "bot.handlers.panel.delete_previous_anchor",
                new=AsyncMock(),
            ) as mock_delete,
            patch(
                "bot.handlers.panel.build_admin_panel_navigation_keyboard",
                side_effect=tracked_build_keyboard,
            ) as mock_keyboard_func,
            patch(
                "bot.handlers.panel.answer_incoming_message_via_runtime",
                side_effect=tracked_answer,
            ) as mock_answer_func,
            patch("bot.handlers.panel.set_anchor") as mock_set,
        ):
            result = await show_admin_panel_and_change_keyboard(
                message,
                sentinel_state,
                admin_user,
            )

        self.assertIsNone(result)
        mock_runtime.assert_called_once_with()
        mock_keyboard_func.assert_awaited_once_with(admin_user)
        self.assertIs(mock_keyboard_func.await_args.args[0], admin_user)

        mock_answer_func.assert_awaited_once_with(
            message,
            admin_user,
            "وارد پنل مدیریت شدید.",
            source_key="panel-admin-main-menu",
            reply_markup=mock_keyboard,
            set_persistent_anchor=True,
        )
        self.assertIs(mock_answer_func.await_args.args[0], message)
        self.assertIs(mock_answer_func.await_args.args[1], admin_user)
        self.assertEqual(mock_answer_func.await_args.args[2], "وارد پنل مدیریت شدید.")
        self.assertEqual(mock_answer_func.await_args.kwargs["source_key"], "panel-admin-main-menu")
        self.assertIs(mock_answer_func.await_args.kwargs["reply_markup"], mock_keyboard)
        self.assertIs(mock_answer_func.await_args.kwargs["set_persistent_anchor"], True)

        mock_delete.assert_not_called()
        mock_set.assert_not_called()

        self.assertEqual(
            call_order,
            [
                "configured_telegram_delivery_runtime",
                "build_admin_panel_navigation_keyboard",
                "answer_incoming_message_via_runtime",
            ],
        )

    async def test_eligible_middle_manager_legacy_mode_freezes_anchors_and_response_contract(self):
        """Middle Manager in Legacy mode deletes previous anchor before response, sets new anchor, leaves state unused."""
        bot_sentinel = object()
        chat_id = 8802
        chat_sentinel = SimpleNamespace(id=chat_id)
        message = SimpleNamespace(
            bot=bot_sentinel,
            chat=chat_sentinel,
            message_id=606,
        )
        sentinel_state = StrictUninspectedSentinel("state")
        manager_user = SimpleNamespace(
            id=60,
            telegram_id=chat_id,
            role=UserRole.MIDDLE_MANAGER,
        )
        runtime_decision = TelegramDeliveryRuntimeDecision(
            mode=TelegramDeliveryRuntimeMode.LEGACY,
            legacy_workers_enabled=True,
            queue_worker_enabled=False,
        )
        mock_keyboard = SimpleNamespace(keyboard=[[SimpleNamespace(text="manager-panel")]])
        expected_anchor_message_id = 9123
        mock_anchor_msg = SimpleNamespace(message_id=expected_anchor_message_id)

        call_order: list[str] = []

        def tracked_runtime():
            call_order.append("configured_telegram_delivery_runtime")
            return runtime_decision

        async def tracked_delete_anchor(*args, **kwargs):
            call_order.append("delete_previous_anchor")

        async def tracked_build_keyboard(u):
            call_order.append("build_admin_panel_navigation_keyboard")
            return mock_keyboard

        async def tracked_answer(*args, **kwargs):
            call_order.append("answer_incoming_message_via_runtime")
            return mock_anchor_msg

        def tracked_set_anchor(*args, **kwargs):
            call_order.append("set_anchor")

        with (
            patch(
                "bot.handlers.panel.configured_telegram_delivery_runtime",
                side_effect=tracked_runtime,
            ) as mock_runtime,
            patch(
                "bot.handlers.panel.delete_previous_anchor",
                side_effect=tracked_delete_anchor,
            ) as mock_delete,
            patch(
                "bot.handlers.panel.build_admin_panel_navigation_keyboard",
                side_effect=tracked_build_keyboard,
            ) as mock_keyboard_func,
            patch(
                "bot.handlers.panel.answer_incoming_message_via_runtime",
                side_effect=tracked_answer,
            ) as mock_answer_func,
            patch(
                "bot.handlers.panel.set_anchor",
                side_effect=tracked_set_anchor,
            ) as mock_set,
        ):
            result = await show_admin_panel_and_change_keyboard(
                message,
                sentinel_state,
                manager_user,
            )

        self.assertIsNone(result)
        mock_runtime.assert_called_once_with()

        mock_delete.assert_awaited_once_with(
            bot_sentinel,
            chat_id,
            delay=DeleteDelay.DEFAULT.value,
        )
        self.assertIs(mock_delete.await_args.args[0], message.bot)
        self.assertEqual(mock_delete.await_args.args[1], message.chat.id)
        self.assertEqual(mock_delete.await_args.kwargs["delay"], DeleteDelay.DEFAULT.value)
        self.assertEqual(DeleteDelay.DEFAULT.value, 120)

        mock_keyboard_func.assert_awaited_once_with(manager_user)
        self.assertIs(mock_keyboard_func.await_args.args[0], manager_user)

        mock_answer_func.assert_awaited_once_with(
            message,
            manager_user,
            "وارد پنل مدیریت شدید.",
            source_key="panel-admin-main-menu",
            reply_markup=mock_keyboard,
            set_persistent_anchor=True,
        )
        self.assertIs(mock_answer_func.await_args.args[0], message)
        self.assertIs(mock_answer_func.await_args.args[1], manager_user)
        self.assertEqual(mock_answer_func.await_args.args[2], "وارد پنل مدیریت شدید.")
        self.assertEqual(mock_answer_func.await_args.kwargs["source_key"], "panel-admin-main-menu")
        self.assertIs(mock_answer_func.await_args.kwargs["reply_markup"], mock_keyboard)
        self.assertIs(mock_answer_func.await_args.kwargs["set_persistent_anchor"], True)

        mock_set.assert_called_once_with(chat_id, expected_anchor_message_id)
        self.assertEqual(mock_set.call_args.args[0], message.chat.id)
        self.assertEqual(mock_set.call_args.args[1], expected_anchor_message_id)

        self.assertEqual(
            call_order,
            [
                "configured_telegram_delivery_runtime",
                "delete_previous_anchor",
                "build_admin_panel_navigation_keyboard",
                "answer_incoming_message_via_runtime",
                "set_anchor",
            ],
        )


if __name__ == "__main__":
    unittest.main()
