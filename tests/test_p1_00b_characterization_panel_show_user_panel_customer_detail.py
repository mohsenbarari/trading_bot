"""Direct-characterization tests for bot.handlers.panel.show_user_panel_customer_detail.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Seed: BOT-TG-CALLBACK-QUERY-PANEL-SHOW-USER-PANEL-CUSTOMER-DETAIL-L823
Canonical gap: docs/refactor/two-server/section-1/inventory/surface-bot.json#BOT-TG-CALLBACK-QUERY-PANEL-SHOW-USER-PANEL-CUSTOMER-DETAIL-L823

This characterization test suite does not update registries, reduce blockers,
change product behavior, close the seed, or close P1-00b.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

FENCE = install_p1_00_network_fence()

from bot.handlers.panel import (  # noqa: E402
    UserPanelCustomerCallback,
    show_user_panel_customer_detail,
)


class CharacterizePanelShowUserPanelCustomerDetailTests(
    unittest.IsolatedAsyncioTestCase
):
    """Hermetic unit tests characterizing bot.handlers.panel.show_user_panel_customer_detail."""

    @patch("bot.handlers.panel.get_customer_detail_keyboard")
    @patch("bot.handlers.panel._customer_relation_detail_text")
    @patch("bot.handlers.panel.edit_callback_message_via_runtime", new_callable=AsyncMock)
    @patch(
        "bot.handlers.panel._load_user_panel_customer_relation",
        new_callable=AsyncMock,
    )
    @patch(
        "bot.handlers.panel.answer_callback_query_via_runtime",
        new_callable=AsyncMock,
    )
    async def test_show_user_panel_customer_detail_no_user(
        self,
        mock_answer_callback_query_via_runtime: AsyncMock,
        mock_load_user_panel_customer_relation: AsyncMock,
        mock_edit_callback_message_via_runtime: AsyncMock,
        mock_customer_relation_detail_text: MagicMock,
        mock_get_customer_detail_keyboard: MagicMock,
    ) -> None:
        callback = SimpleNamespace(answer=AsyncMock(spec=[]))
        callback_data = UserPanelCustomerCallback(action="detail", relation_id=42)

        initial_denied = len(FENCE["denied_connects"])
        initial_redis = len(FENCE["live_redis_constructs"])

        result = await show_user_panel_customer_detail(callback, callback_data, None)

        self.assertIsNone(result)

        mock_load_user_panel_customer_relation.assert_not_called()
        mock_load_user_panel_customer_relation.assert_not_awaited()

        mock_customer_relation_detail_text.assert_not_called()
        mock_get_customer_detail_keyboard.assert_not_called()

        mock_edit_callback_message_via_runtime.assert_not_called()
        mock_edit_callback_message_via_runtime.assert_not_awaited()

        mock_answer_callback_query_via_runtime.assert_awaited_once_with(callback)
        ack_args, ack_kwargs = mock_answer_callback_query_via_runtime.await_args
        self.assertEqual(ack_args, (callback,))
        self.assertEqual(ack_kwargs, {})

        callback.answer.assert_not_called()
        callback.answer.assert_not_awaited()

        self.assertEqual(len(FENCE["denied_connects"]), initial_denied)
        self.assertEqual(len(FENCE["live_redis_constructs"]), initial_redis)

    @patch("bot.handlers.panel.get_customer_detail_keyboard")
    @patch("bot.handlers.panel._customer_relation_detail_text")
    @patch("bot.handlers.panel.edit_callback_message_via_runtime", new_callable=AsyncMock)
    @patch(
        "bot.handlers.panel._load_user_panel_customer_relation",
        new_callable=AsyncMock,
    )
    @patch(
        "bot.handlers.panel.answer_callback_query_via_runtime",
        new_callable=AsyncMock,
    )
    async def test_show_user_panel_customer_detail_linked_user_missing_relation(
        self,
        mock_answer_callback_query_via_runtime: AsyncMock,
        mock_load_user_panel_customer_relation: AsyncMock,
        mock_edit_callback_message_via_runtime: AsyncMock,
        mock_customer_relation_detail_text: MagicMock,
        mock_get_customer_detail_keyboard: MagicMock,
    ) -> None:
        mock_load_user_panel_customer_relation.return_value = None

        callback = SimpleNamespace(answer=AsyncMock(spec=[]))
        callback_data = UserPanelCustomerCallback(action="detail", relation_id=55)
        user = SimpleNamespace(id=101)

        initial_denied = len(FENCE["denied_connects"])
        initial_redis = len(FENCE["live_redis_constructs"])

        result = await show_user_panel_customer_detail(callback, callback_data, user)

        self.assertIsNone(result)

        mock_load_user_panel_customer_relation.assert_awaited_once_with(
            user.id, callback_data.relation_id
        )
        load_args, load_kwargs = mock_load_user_panel_customer_relation.await_args
        self.assertEqual(load_args, (user.id, callback_data.relation_id))
        self.assertEqual(load_kwargs, {})

        expected_missing_text = "مشتری یافت نشد."
        mock_answer_callback_query_via_runtime.assert_awaited_once_with(
            callback,
            expected_missing_text,
            show_alert=True,
        )
        ack_args, ack_kwargs = mock_answer_callback_query_via_runtime.await_args
        self.assertEqual(ack_args, (callback, expected_missing_text))
        self.assertEqual(ack_kwargs, {"show_alert": True})

        mock_customer_relation_detail_text.assert_not_called()
        mock_get_customer_detail_keyboard.assert_not_called()

        mock_edit_callback_message_via_runtime.assert_not_called()
        mock_edit_callback_message_via_runtime.assert_not_awaited()

        callback.answer.assert_not_called()
        callback.answer.assert_not_awaited()

        self.assertEqual(len(FENCE["denied_connects"]), initial_denied)
        self.assertEqual(len(FENCE["live_redis_constructs"]), initial_redis)

    @patch("bot.handlers.panel.get_customer_detail_keyboard")
    @patch("bot.handlers.panel._customer_relation_detail_text")
    @patch("bot.handlers.panel.edit_callback_message_via_runtime", new_callable=AsyncMock)
    @patch(
        "bot.handlers.panel._load_user_panel_customer_relation",
        new_callable=AsyncMock,
    )
    @patch(
        "bot.handlers.panel.answer_callback_query_via_runtime",
        new_callable=AsyncMock,
    )
    async def test_show_user_panel_customer_detail_found_relation(
        self,
        mock_answer_callback_query_via_runtime: AsyncMock,
        mock_load_user_panel_customer_relation: AsyncMock,
        mock_edit_callback_message_via_runtime: AsyncMock,
        mock_customer_relation_detail_text: MagicMock,
        mock_get_customer_detail_keyboard: MagicMock,
    ) -> None:
        relation = SimpleNamespace(
            id=77,
            owner_user_id=202,
            customer_user_id=303,
            status="ACTIVE",
            customer_tier="TIER_1",
        )
        mock_load_user_panel_customer_relation.return_value = relation
        mock_customer_relation_detail_text.return_value = (
            "👤 **مشتری نمونه**\n\nوضعیت: فعال\nسطح مشتری: سطح ۱"
        )
        sentinel_keyboard = SimpleNamespace(name="mock_customer_detail_keyboard")
        mock_get_customer_detail_keyboard.return_value = sentinel_keyboard

        call_order: list[str] = []

        async def _record_edit(*args: Any, **kwargs: Any) -> None:
            call_order.append("edit_callback_message")

        async def _record_answer(*args: Any, **kwargs: Any) -> None:
            call_order.append("answer_callback_query")

        mock_edit_callback_message_via_runtime.side_effect = _record_edit
        mock_answer_callback_query_via_runtime.side_effect = _record_answer

        callback = SimpleNamespace(answer=AsyncMock(spec=[]))
        callback_data = UserPanelCustomerCallback(action="detail", relation_id=77)
        user = SimpleNamespace(id=202)

        initial_denied = len(FENCE["denied_connects"])
        initial_redis = len(FENCE["live_redis_constructs"])

        result = await show_user_panel_customer_detail(callback, callback_data, user)

        self.assertIsNone(result)

        mock_load_user_panel_customer_relation.assert_awaited_once_with(
            user.id, callback_data.relation_id
        )
        load_args, load_kwargs = mock_load_user_panel_customer_relation.await_args
        self.assertEqual(load_args, (user.id, callback_data.relation_id))
        self.assertEqual(load_kwargs, {})

        mock_customer_relation_detail_text.assert_called_once_with(relation)
        detail_args, detail_kwargs = mock_customer_relation_detail_text.call_args
        self.assertEqual(detail_args, (relation,))
        self.assertEqual(detail_kwargs, {})

        mock_get_customer_detail_keyboard.assert_called_once_with(relation)
        kb_args, kb_kwargs = mock_get_customer_detail_keyboard.call_args
        self.assertEqual(kb_args, (relation,))
        self.assertEqual(kb_kwargs, {})

        mock_edit_callback_message_via_runtime.assert_awaited_once_with(
            callback,
            user,
            mock_customer_relation_detail_text.return_value,
            parse_mode="Markdown",
            reply_markup=mock_get_customer_detail_keyboard.return_value,
        )
        edit_args, edit_kwargs = mock_edit_callback_message_via_runtime.await_args
        self.assertEqual(
            edit_args,
            (callback, user, mock_customer_relation_detail_text.return_value),
        )
        self.assertEqual(
            edit_kwargs,
            {
                "parse_mode": "Markdown",
                "reply_markup": mock_get_customer_detail_keyboard.return_value,
            },
        )

        mock_answer_callback_query_via_runtime.assert_awaited_once_with(callback)
        ack_args, ack_kwargs = mock_answer_callback_query_via_runtime.await_args
        self.assertEqual(ack_args, (callback,))
        self.assertEqual(ack_kwargs, {})

        self.assertEqual(call_order, ["edit_callback_message", "answer_callback_query"])

        callback.answer.assert_not_called()
        callback.answer.assert_not_awaited()

        self.assertEqual(len(FENCE["denied_connects"]), initial_denied)
        self.assertEqual(len(FENCE["live_redis_constructs"]), initial_redis)


if __name__ == "__main__":
    unittest.main()
