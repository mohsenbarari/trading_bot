"""Stage P1-00b characterization tests for linked admin broadcast finish selected recipients handler.

Assignment: WA-P1-00B-EVIDENCE-RECONCILE-20261001-064
Seed: BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-FINISH-SELECTED-RECIPIENTS-L395

Characterizes the real linked SUPER_ADMIN finish_selected_recipients callback handler
and live _ask_for_message_text helper under both empty and nonempty selection outcomes.
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from aiogram.types import InlineKeyboardMarkup
from bot.handlers.admin_broadcast import (
    CALLBACK_PREFIX,
    finish_selected_recipients,
)
from bot.states import AdminBroadcast
from core.enums import UserRole
from models.user import User


class AdminBroadcastFinishSelectedLinkedCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self) -> None:
        self.user = User(
            id=7,
            telegram_id=70001,
            role=UserRole.SUPER_ADMIN,
            sync_version=1,
        )

        self.spy_answer_callback = AsyncMock()
        self.spy_edit_callback = AsyncMock()
        self.spy_session_local = MagicMock()

        for target, mock_obj in (
            (
                "bot.handlers.admin_broadcast.answer_callback_query_via_runtime",
                self.spy_answer_callback,
            ),
            (
                "bot.handlers.admin_broadcast.edit_callback_message_via_runtime",
                self.spy_edit_callback,
            ),
            (
                "bot.handlers.admin_broadcast.AsyncSessionLocal",
                self.spy_session_local,
            ),
        ):
            patcher = patch(target, new=mock_obj)
            patcher.start()
            self.addCleanup(patcher.stop)

    def _make_callback(
        self, data: str = f"{CALLBACK_PREFIX}:selected_done"
    ) -> SimpleNamespace:
        return SimpleNamespace(
            id="callback-finish-selected-701",
            data=data,
            answer=AsyncMock(),
            message=SimpleNamespace(
                message_id=901,
                edit_text=AsyncMock(),
                edit_reply_markup=AsyncMock(),
            ),
        )

    def _make_state(self, data: dict | None = None) -> SimpleNamespace:
        return SimpleNamespace(
            get_data=AsyncMock(return_value=dict(data or {})),
            set_state=AsyncMock(),
            update_data=AsyncMock(),
            clear=AsyncMock(),
        )

    async def test_empty_selection_alerts_without_transition_or_edit(self) -> None:
        fence = install_p1_00_network_fence()
        denied_connects_before = len(fence["denied_connects"])
        live_redis_before = len(fence["live_redis_constructs"])

        callback = self._make_callback()
        state = self._make_state({"selected_user_ids": []})

        call_order: list[str] = []

        async def _tracked_get_data() -> dict:
            call_order.append("state.get_data")
            return {"selected_user_ids": []}

        state.get_data.side_effect = _tracked_get_data

        async def _tracked_answer_callback(*args, **kwargs) -> None:
            call_order.append("answer_callback_query_via_runtime")

        self.spy_answer_callback.side_effect = _tracked_answer_callback

        result = await finish_selected_recipients(
            callback=callback,  # type: ignore[arg-type]
            state=state,  # type: ignore[arg-type]
            user=self.user,
        )

        self.assertIsNone(result)
        self.assertEqual(
            call_order,
            ["state.get_data", "answer_callback_query_via_runtime"],
        )

        self.spy_answer_callback.assert_awaited_once_with(
            callback,
            "حداقل یک کاربر انتخاب کنید.",
            show_alert=True,
        )

        state.get_data.assert_awaited_once()
        state.set_state.assert_not_awaited()
        state.update_data.assert_not_awaited()
        state.clear.assert_not_awaited()

        self.spy_edit_callback.assert_not_awaited()
        callback.message.edit_text.assert_not_awaited()
        callback.message.edit_reply_markup.assert_not_awaited()
        self.spy_session_local.assert_not_called()

        self.assertEqual(len(fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(fence["live_redis_constructs"]), live_redis_before)

    async def test_nonempty_selection_prompts_then_acknowledges(self) -> None:
        fence = install_p1_00_network_fence()
        denied_connects_before = len(fence["denied_connects"])
        live_redis_before = len(fence["live_redis_constructs"])

        callback = self._make_callback()
        state = self._make_state({"selected_user_ids": [42]})

        call_order: list[str] = []

        async def _tracked_get_data() -> dict:
            call_order.append("state.get_data")
            return {"selected_user_ids": [42]}

        state.get_data.side_effect = _tracked_get_data

        async def _tracked_set_state(*args, **kwargs) -> None:
            call_order.append("state.set_state")

        state.set_state.side_effect = _tracked_set_state

        async def _tracked_edit_callback(*args, **kwargs) -> None:
            call_order.append("edit_callback_message_via_runtime")

        self.spy_edit_callback.side_effect = _tracked_edit_callback

        async def _tracked_answer_callback(*args, **kwargs) -> None:
            call_order.append("answer_callback_query_via_runtime")

        self.spy_answer_callback.side_effect = _tracked_answer_callback

        result = await finish_selected_recipients(
            callback=callback,  # type: ignore[arg-type]
            state=state,  # type: ignore[arg-type]
            user=self.user,
        )

        self.assertIsNone(result)
        self.assertEqual(
            call_order,
            [
                "state.get_data",
                "state.set_state",
                "edit_callback_message_via_runtime",
                "answer_callback_query_via_runtime",
            ],
        )

        state.get_data.assert_awaited_once()
        state.set_state.assert_awaited_once_with(
            AdminBroadcast.awaiting_message_text
        )
        state.update_data.assert_not_awaited()
        state.clear.assert_not_awaited()

        self.spy_edit_callback.assert_awaited_once()
        self.assertEqual(
            self.spy_edit_callback.await_args.args[:2],
            (callback, self.user),
        )
        expected_prompt = (
            "متن یا ویدئوی دارای کپشن را ارسال کنید.\n"
            "حداکثر متن: ۴۰۹۶ | حداکثر کپشن: ۱۰۲۴"
        )
        self.assertEqual(
            self.spy_edit_callback.await_args.args[2],
            expected_prompt,
        )
        self.assertEqual(
            self.spy_edit_callback.await_args.kwargs.get("source_key"),
            "admin-broadcast-ask-text",
        )

        reply_markup = self.spy_edit_callback.await_args.kwargs.get("reply_markup")
        self.assertIsInstance(reply_markup, InlineKeyboardMarkup)
        self.assertEqual(len(reply_markup.inline_keyboard), 1)
        self.assertEqual(len(reply_markup.inline_keyboard[0]), 1)
        cancel_button = reply_markup.inline_keyboard[0][0]
        self.assertEqual(cancel_button.text, "لغو")
        self.assertEqual(
            cancel_button.callback_data,
            f"{CALLBACK_PREFIX}:cancel",
        )
        self.assertEqual(cancel_button.callback_data, "tgb:cancel")

        callback.message.edit_text.assert_not_awaited()
        callback.message.edit_reply_markup.assert_not_awaited()

        self.spy_answer_callback.assert_awaited_once_with(callback)
        self.spy_session_local.assert_not_called()

        self.assertEqual(len(fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(fence["live_redis_constructs"]), live_redis_before)


if __name__ == "__main__":
    unittest.main()
