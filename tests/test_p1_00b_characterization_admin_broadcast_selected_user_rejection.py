"""Stage P1-00b characterization tests for admin broadcast toggle_selected_user rejections.

Seed: BOT-ADMIN-CALLBACK-QUERY-ADMIN-BROADCAST-TOGGLE-SELECTED-USER-L334
Assignment: WA-P1-00B-EVIDENCE-RECONCILE-20261001-064 / WA-P1-00B-FREEZE-20260904-001
Tracked High Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

This module characterizes two previously unasserted rejection branches of
bot.handlers.admin_broadcast.toggle_selected_user under an authenticated SUPER_ADMIN:
1. Malformed callback target user ID ('tgb:tu:not-an-int') rejected before any FSM access or mutation.
2. Valid new target user ID when state reports exactly TELEGRAM_BROADCAST_SELECTED_RECIPIENT_CAP (500)
   distinct synthetic IDs, rejected after get_data() without any FSM mutation.

Boundary mocks:
- bot.handlers.admin_broadcast.answer_callback_query_via_runtime (runtime alert verification)
- bot.handlers.admin_broadcast.edit_callback_message_via_runtime (downstream UI edit suppression)
- bot.handlers.admin_broadcast.edit_callback_reply_markup_via_runtime (downstream markup edit suppression)
- bot.handlers.admin_broadcast._search_results_keyboard (downstream keyboard building suppression)
- bot.handlers.admin_broadcast.search_telegram_admin_broadcast_recipients (downstream recipient search suppression)
- bot.handlers.admin_broadcast.AsyncSessionLocal (downstream database session suppression)

Exclusions:
- Existing success toggle path (characterized in test_bot_admin_broadcast_interaction_queue.py)
- Unlinked user guard (user=None) path (characterized in test_p1_00b_characterization_admin_broadcast_unlinked_selection_mutation_guards_batch.py)
- Group recipient selection paths (characterized in separate suites)
- No durable delivery is executed or claimed; callback answering is mocked at the runtime boundary.
- No persistence proof is claimed; no database sessions or Redis connections are constructed.
- Neither test claims seed closure, durable delivery, or Stage acceptance. Stage P1-00b remains IN_PROGRESS.
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

from bot.handlers.admin_broadcast import toggle_selected_user
from core.enums import UserRole
from core.services.telegram_admin_broadcast_service import (
    TELEGRAM_BROADCAST_SELECTED_RECIPIENT_CAP,
)


class AdminBroadcastSelectedUserRejectionCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        self.spy_answer_callback = AsyncMock()
        self.spy_edit_callback = AsyncMock()
        self.spy_edit_reply_markup = AsyncMock()
        self.spy_search_results_keyboard = MagicMock()
        self.spy_search_recipients = AsyncMock()
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
                "bot.handlers.admin_broadcast.edit_callback_reply_markup_via_runtime",
                self.spy_edit_reply_markup,
            ),
            (
                "bot.handlers.admin_broadcast._search_results_keyboard",
                self.spy_search_results_keyboard,
            ),
            (
                "bot.handlers.admin_broadcast.search_telegram_admin_broadcast_recipients",
                self.spy_search_recipients,
            ),
            (
                "bot.handlers.admin_broadcast.AsyncSessionLocal",
                self.spy_session_local,
            ),
        ):
            patcher = patch(target, new=mock_obj)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_malformed_target_alerts_without_state_or_delivery(self):
        fence = install_p1_00_network_fence()
        denied_connects_before = len(fence["denied_connects"])
        live_redis_before = len(fence["live_redis_constructs"])

        user = SimpleNamespace(id=77, role=UserRole.SUPER_ADMIN)
        callback = SimpleNamespace(
            id="cb-malformed-001",
            data="tgb:tu:not-an-int",
        )
        state = SimpleNamespace(
            get_data=AsyncMock(),
            update_data=AsyncMock(),
            set_state=AsyncMock(),
            clear=AsyncMock(),
        )

        result = await toggle_selected_user(
            callback=callback,  # type: ignore[arg-type]
            state=state,        # type: ignore[arg-type]
            user=user,
        )

        self.assertIsNone(result)

        self.spy_answer_callback.assert_awaited_once_with(
            callback,
            "کاربر نامعتبر است.",
            show_alert=True,
        )

        state.get_data.assert_not_awaited()
        state.update_data.assert_not_awaited()
        state.set_state.assert_not_awaited()
        state.clear.assert_not_awaited()

        self.spy_session_local.assert_not_called()
        self.spy_search_recipients.assert_not_called()
        self.spy_search_results_keyboard.assert_not_called()
        self.spy_edit_callback.assert_not_called()
        self.spy_edit_reply_markup.assert_not_called()

        self.assertEqual(len(fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(fence["live_redis_constructs"]), live_redis_before)

    async def test_new_target_at_cap_alerts_without_mutation_or_delivery(self):
        fence = install_p1_00_network_fence()
        denied_connects_before = len(fence["denied_connects"])
        live_redis_before = len(fence["live_redis_constructs"])

        user = SimpleNamespace(id=77, role=UserRole.SUPER_ADMIN)
        target_id = 99999
        existing_selected_ids = list(range(1, TELEGRAM_BROADCAST_SELECTED_RECIPIENT_CAP + 1))
        self.assertEqual(len(existing_selected_ids), 500)
        self.assertEqual(len(set(existing_selected_ids)), 500)
        self.assertNotIn(target_id, existing_selected_ids)

        callback = SimpleNamespace(
            id="cb-cap-002",
            data=f"tgb:tu:{target_id}",
        )
        state = SimpleNamespace(
            get_data=AsyncMock(return_value={"selected_user_ids": existing_selected_ids, "last_search_query": ""}),
            update_data=AsyncMock(),
            set_state=AsyncMock(),
            clear=AsyncMock(),
        )

        result = await toggle_selected_user(
            callback=callback,  # type: ignore[arg-type]
            state=state,        # type: ignore[arg-type]
            user=user,
        )

        self.assertIsNone(result)

        self.spy_answer_callback.assert_awaited_once_with(
            callback,
            "تعداد کاربران انتخابی از سقف مجاز بیشتر است.",
            show_alert=True,
        )

        state.get_data.assert_awaited_once_with()
        state.update_data.assert_not_awaited()
        state.set_state.assert_not_awaited()
        state.clear.assert_not_awaited()

        self.spy_session_local.assert_not_called()
        self.spy_search_recipients.assert_not_called()
        self.spy_search_results_keyboard.assert_not_called()
        self.spy_edit_callback.assert_not_called()
        self.spy_edit_reply_markup.assert_not_called()

        self.assertEqual(len(fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(fence["live_redis_constructs"]), live_redis_before)


if __name__ == "__main__":
    unittest.main()
