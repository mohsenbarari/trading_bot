"""Hermetic characterization tests for cancel_invitation_creation in QUEUE_V1 delivery mode."""

from __future__ import annotations

import os
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from bot.handlers.admin import cancel_invitation_creation
from core.enums import UserRole
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeDecision,
    TelegramDeliveryRuntimeMode,
)


class RecordingFSMContext:
    """In-memory FSMContext fixture recording ordered get_data, clear, and update_data effects."""

    def __init__(
        self,
        initial_data: dict[str, Any] | None = None,
        timeline: list[str] | None = None,
    ) -> None:
        self._data: dict[str, Any] = dict(initial_data or {})
        self.operations: list[tuple[str, Any]] = []
        self._timeline: list[str] | None = timeline

    async def get_data(self) -> dict[str, Any]:
        snapshot = dict(self._data)
        self.operations.append(("get_data", snapshot))
        if self._timeline is not None:
            self._timeline.append("fsm.get_data")
        return snapshot

    async def clear(self) -> None:
        snapshot = dict(self._data)
        self.operations.append(("clear", snapshot))
        if self._timeline is not None:
            self._timeline.append("fsm.clear")
        self._data.clear()

    async def update_data(self, **kwargs: Any) -> dict[str, Any]:
        self.operations.append(("update_data", dict(kwargs)))
        if self._timeline is not None:
            self._timeline.append("fsm.update_data")
        self._data.update(kwargs)
        return dict(self._data)


class RecordingCallbackQuery:
    """CallbackQuery test double recognized by _return_to_admin_panel isinstance check."""

    def __init__(self, chat_id: int = 1001, message_id: int = 2001) -> None:
        self.id = "cb_cancel_test_query_id"
        self.data = "comm_fsm_cancel"
        self.message = SimpleNamespace(
            chat=SimpleNamespace(id=chat_id),
            message_id=message_id,
            delete=AsyncMock(),
        )
        self.answer = AsyncMock()


@contextmanager
def _cancel_queue_collaborators_harness(
    *,
    timeline: list[str],
    cancellation_receipt_id: int = 9001,
    panel_receipt_id: int = 9005,
    fallback_keyboard: Any = None,
    navigation_keyboard: Any = None,
):
    sentinel_fallback_kb = fallback_keyboard if fallback_keyboard is not None else object()
    sentinel_nav_kb = navigation_keyboard if navigation_keyboard is not None else object()

    async def _mock_answer_callback_message(
        callback: Any,
        user: Any,
        text: str,
        *,
        reply_markup: Any = None,
        set_persistent_anchor: bool = False,
        **kwargs: Any,
    ) -> SimpleNamespace:
        if text == "عملیات لغو شد.":
            timeline.append("answer_callback_message.cancel")
            return SimpleNamespace(message_id=cancellation_receipt_id)
        if text == "...بازگشت به پنل مدیریت":
            timeline.append("answer_callback_message.panel")
            return SimpleNamespace(message_id=panel_receipt_id)
        timeline.append(f"answer_callback_message.other:{text}")
        return SimpleNamespace(message_id=9999)

    async def _mock_answer_callback_query(
        callback: Any,
        text: Any = None,
        **kwargs: Any,
    ) -> None:
        timeline.append(f"answer_callback_query:{text}")

    async def _mock_build_admin_panel_nav(user: Any) -> Any:
        timeline.append("build_admin_panel_navigation_keyboard")
        return sentinel_nav_kb

    def _mock_get_admin_panel_kb(user_role: Any) -> Any:
        timeline.append("get_admin_panel_keyboard")
        return sentinel_fallback_kb

    mock_answer_message = AsyncMock(side_effect=_mock_answer_callback_message)
    mock_answer_query = AsyncMock(side_effect=_mock_answer_callback_query)
    mock_build_nav_kb = AsyncMock(side_effect=_mock_build_admin_panel_nav)
    mock_get_admin_kb = MagicMock(side_effect=_mock_get_admin_panel_kb)

    runtime_decision = TelegramDeliveryRuntimeDecision(
        mode=TelegramDeliveryRuntimeMode.QUEUE_V1,
        legacy_workers_enabled=False,
        queue_worker_enabled=True,
    )

    with (
        patch(
            "bot.handlers.admin.configured_telegram_delivery_runtime",
            return_value=runtime_decision,
        ),
        patch("bot.handlers.admin.types.CallbackQuery", RecordingCallbackQuery),
        patch(
            "bot.handlers.admin.answer_callback_message_via_runtime",
            mock_answer_message,
        ),
        patch(
            "bot.handlers.admin.answer_callback_query_via_runtime",
            mock_answer_query,
        ),
        patch(
            "bot.handlers.admin.build_admin_panel_navigation_keyboard",
            mock_build_nav_kb,
        ),
        patch(
            "bot.handlers.admin.get_admin_panel_keyboard",
            mock_get_admin_kb,
        ),
    ):
        yield SimpleNamespace(
            answer_message=mock_answer_message,
            answer_query=mock_answer_query,
            build_nav_kb=mock_build_nav_kb,
            get_admin_kb=mock_get_admin_kb,
            sentinel_fallback_kb=sentinel_fallback_kb,
            sentinel_nav_kb=sentinel_nav_kb,
        )


class AdminInvitationCancelQueueCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_queue_without_user_clears_state_suppresses_deletes_and_returns_fallback_panel(
        self,
    ) -> None:
        timeline: list[str] = []
        initial_data = {
            "inviter_role": UserRole.SUPER_ADMIN.value,
            "last_prompt_message_id": 7001,
            "anchor_message_id": 8001,
        }
        state = RecordingFSMContext(initial_data=initial_data, timeline=timeline)
        callback = RecordingCallbackQuery(chat_id=1001, message_id=2001)
        bot = SimpleNamespace(delete_message=AsyncMock(), send_message=AsyncMock())

        new_anchor_id = 9005
        with _cancel_queue_collaborators_harness(
            timeline=timeline,
            cancellation_receipt_id=9001,
            panel_receipt_id=new_anchor_id,
        ) as harness:
            await cancel_invitation_creation(callback, state, bot=bot, user=None)

            # 1. State lifecycle verification
            self.assertEqual(
                timeline,
                [
                    "fsm.get_data",
                    "fsm.clear",
                    "answer_callback_message.cancel",
                    "fsm.get_data",
                    "get_admin_panel_keyboard",
                    "answer_callback_message.panel",
                    "fsm.update_data",
                    "answer_callback_query:لغو شد",
                ],
            )
            clear_index = timeline.index("fsm.clear")
            self.assertLess(clear_index, timeline.index("answer_callback_message.cancel"))
            self.assertLess(clear_index, timeline.index("answer_callback_message.panel"))
            self.assertLess(clear_index, timeline.index("answer_callback_query:لغو شد"))

            # Stale keys are wiped, only the new anchor receipt ID is persisted
            final_data = await state.get_data()
            self.assertEqual(final_data, {"anchor_message_id": new_anchor_id})
            self.assertNotIn("last_prompt_message_id", final_data)
            self.assertNotIn("inviter_role", final_data)

            # 2. Deletion suppression and no bot.send_message
            callback.message.delete.assert_not_awaited()
            bot.delete_message.assert_not_awaited()
            bot.send_message.assert_not_awaited()

            # 3. Exact collaborator call assertions
            self.assertEqual(harness.answer_message.await_count, 2)
            first_call = harness.answer_message.await_args_list[0]
            self.assertEqual(first_call.args, (callback, None, "عملیات لغو شد."))
            self.assertEqual(first_call.kwargs, {})

            harness.get_admin_kb.assert_called_once_with(UserRole.SUPER_ADMIN)
            harness.build_nav_kb.assert_not_awaited()

            second_call = harness.answer_message.await_args_list[1]
            self.assertEqual(second_call.args, (callback, None, "...بازگشت به پنل مدیریت"))
            self.assertEqual(
                second_call.kwargs,
                {
                    "reply_markup": harness.sentinel_fallback_kb,
                    "set_persistent_anchor": True,
                },
            )

            harness.answer_query.assert_awaited_once_with(callback, "لغو شد")

    async def test_cancel_queue_with_user_uses_navigation_panel_and_exact_callback_response(
        self,
    ) -> None:
        timeline: list[str] = []
        user = SimpleNamespace(
            id=42,
            role=UserRole.SUPER_ADMIN,
            account_name="super_admin_user",
        )
        initial_data = {
            "inviter_role": UserRole.SUPER_ADMIN.value,
            "last_prompt_message_id": 7002,
            "anchor_message_id": 8002,
        }
        state = RecordingFSMContext(initial_data=initial_data, timeline=timeline)
        callback = RecordingCallbackQuery(chat_id=1001, message_id=2002)
        bot = SimpleNamespace(delete_message=AsyncMock(), send_message=AsyncMock())

        new_anchor_id = 9006
        with _cancel_queue_collaborators_harness(
            timeline=timeline,
            cancellation_receipt_id=9002,
            panel_receipt_id=new_anchor_id,
        ) as harness:
            await cancel_invitation_creation(callback, state, bot=bot, user=user)

            # 1. State lifecycle verification
            self.assertEqual(
                timeline,
                [
                    "fsm.get_data",
                    "fsm.clear",
                    "answer_callback_message.cancel",
                    "fsm.get_data",
                    "build_admin_panel_navigation_keyboard",
                    "answer_callback_message.panel",
                    "fsm.update_data",
                    "answer_callback_query:لغو شد",
                ],
            )
            clear_index = timeline.index("fsm.clear")
            self.assertLess(clear_index, timeline.index("answer_callback_message.cancel"))
            self.assertLess(clear_index, timeline.index("answer_callback_message.panel"))
            self.assertLess(clear_index, timeline.index("answer_callback_query:لغو شد"))

            # Stale keys are wiped, only the new anchor receipt ID is persisted
            final_data = await state.get_data()
            self.assertEqual(final_data, {"anchor_message_id": new_anchor_id})
            self.assertNotIn("last_prompt_message_id", final_data)
            self.assertNotIn("inviter_role", final_data)

            # 2. Deletion suppression and no bot.send_message
            callback.message.delete.assert_not_awaited()
            bot.delete_message.assert_not_awaited()
            bot.send_message.assert_not_awaited()

            # 3. Exact collaborator call assertions
            self.assertEqual(harness.answer_message.await_count, 2)
            first_call = harness.answer_message.await_args_list[0]
            self.assertEqual(first_call.args, (callback, user, "عملیات لغو شد."))
            self.assertEqual(first_call.kwargs, {})

            harness.build_nav_kb.assert_awaited_once_with(user)
            harness.get_admin_kb.assert_not_called()

            second_call = harness.answer_message.await_args_list[1]
            self.assertEqual(second_call.args, (callback, user, "...بازگشت به پنل مدیریت"))
            self.assertEqual(
                second_call.kwargs,
                {
                    "reply_markup": harness.sentinel_nav_kb,
                    "set_persistent_anchor": True,
                },
            )

            harness.answer_query.assert_awaited_once_with(callback, "لغو شد")


if __name__ == "__main__":
    unittest.main()
