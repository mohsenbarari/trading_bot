"""Direct-characterization tests for bot.handlers.panel.show_user_panel_customers_callback.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Seed: BOT-TG-CALLBACK-QUERY-PANEL-SHOW-USER-PANEL-CUSTOMERS-CALLBACK-L805
Canonical gap: docs/refactor/two-server/section-1/inventory/surface-bot.json#BOT-TG-CALLBACK-QUERY-PANEL-SHOW-USER-PANEL-CUSTOMERS-CALLBACK-L805

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
    show_user_panel_customers_callback,
)


class _SessionContext:
    """Bounded async context manager stub returning a sentinel session."""

    def __init__(self, session: Any) -> None:
        self.session = session
        self.aenter = AsyncMock(return_value=session)
        self.aexit = AsyncMock(return_value=False)

    async def __aenter__(self) -> Any:
        return await self.aenter()

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return await self.aexit(exc_type, exc, tb)


class CharacterizePanelShowUserPanelCustomersCallbackTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing bot.handlers.panel.show_user_panel_customers_callback."""

    @patch("bot.handlers.panel._edit_or_answer_customers_panel", new_callable=AsyncMock)
    @patch("bot.handlers.panel.answer_callback_query_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel._can_use_customer_panel", new_callable=AsyncMock)
    @patch("bot.handlers.panel.AsyncSessionLocal")
    async def test_show_user_panel_customers_callback_no_user(
        self,
        mock_async_session_local: MagicMock,
        mock_can_use_customer_panel: AsyncMock,
        mock_answer_callback_query_via_runtime: AsyncMock,
        mock_edit_or_answer_customers_panel: AsyncMock,
    ) -> None:
        callback = SimpleNamespace(answer=AsyncMock(spec=[]))

        initial_denied = len(FENCE["denied_connects"])
        initial_redis = len(FENCE["live_redis_constructs"])

        result = await show_user_panel_customers_callback(callback, None)

        self.assertIsNone(result)

        mock_async_session_local.assert_not_called()

        mock_can_use_customer_panel.assert_not_called()
        mock_can_use_customer_panel.assert_not_awaited()

        mock_edit_or_answer_customers_panel.assert_not_called()
        mock_edit_or_answer_customers_panel.assert_not_awaited()

        mock_answer_callback_query_via_runtime.assert_awaited_once_with(callback)
        ack_args, ack_kwargs = mock_answer_callback_query_via_runtime.await_args
        self.assertEqual(ack_args, (callback,))
        self.assertEqual(ack_kwargs, {})

        callback.answer.assert_not_called()
        callback.answer.assert_not_awaited()

        self.assertEqual(len(FENCE["denied_connects"]), initial_denied)
        self.assertEqual(len(FENCE["live_redis_constructs"]), initial_redis)

    @patch("bot.handlers.panel._edit_or_answer_customers_panel", new_callable=AsyncMock)
    @patch("bot.handlers.panel.answer_callback_query_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel._can_use_customer_panel", new_callable=AsyncMock)
    @patch("bot.handlers.panel.AsyncSessionLocal")
    async def test_show_user_panel_customers_callback_linked_user_access_denied(
        self,
        mock_async_session_local: MagicMock,
        mock_can_use_customer_panel: AsyncMock,
        mock_answer_callback_query_via_runtime: AsyncMock,
        mock_edit_or_answer_customers_panel: AsyncMock,
    ) -> None:
        sentinel_session = SimpleNamespace(name="sentinel_session_denied")
        session_context = _SessionContext(sentinel_session)
        mock_async_session_local.return_value = session_context
        mock_can_use_customer_panel.return_value = False

        callback = SimpleNamespace(answer=AsyncMock(spec=[]))
        user = SimpleNamespace(id=101)

        initial_denied = len(FENCE["denied_connects"])
        initial_redis = len(FENCE["live_redis_constructs"])

        result = await show_user_panel_customers_callback(callback, user)

        self.assertIsNone(result)

        mock_async_session_local.assert_called_once_with()
        session_context.aenter.assert_awaited_once_with()
        session_context.aexit.assert_awaited_once_with(None, None, None)

        mock_can_use_customer_panel.assert_awaited_once_with(sentinel_session, user)
        can_args, can_kwargs = mock_can_use_customer_panel.await_args
        self.assertEqual(can_args, (sentinel_session, user))
        self.assertEqual(can_kwargs, {})

        expected_denied_text = "این بخش برای حساب شما فعال نیست."
        mock_answer_callback_query_via_runtime.assert_awaited_once_with(
            callback,
            expected_denied_text,
            show_alert=True,
        )
        denied_args, denied_kwargs = mock_answer_callback_query_via_runtime.await_args
        self.assertEqual(denied_args, (callback, expected_denied_text))
        self.assertEqual(denied_kwargs, {"show_alert": True})

        mock_edit_or_answer_customers_panel.assert_not_called()
        mock_edit_or_answer_customers_panel.assert_not_awaited()

        callback.answer.assert_not_called()
        callback.answer.assert_not_awaited()

        self.assertEqual(len(FENCE["denied_connects"]), initial_denied)
        self.assertEqual(len(FENCE["live_redis_constructs"]), initial_redis)

    @patch("bot.handlers.panel._edit_or_answer_customers_panel", new_callable=AsyncMock)
    @patch("bot.handlers.panel.answer_callback_query_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel._can_use_customer_panel", new_callable=AsyncMock)
    @patch("bot.handlers.panel.AsyncSessionLocal")
    async def test_show_user_panel_customers_callback_access_allowed(
        self,
        mock_async_session_local: MagicMock,
        mock_can_use_customer_panel: AsyncMock,
        mock_answer_callback_query_via_runtime: AsyncMock,
        mock_edit_or_answer_customers_panel: AsyncMock,
    ) -> None:
        sentinel_session = SimpleNamespace(name="sentinel_session_allowed")
        session_context = _SessionContext(sentinel_session)
        mock_async_session_local.return_value = session_context
        mock_can_use_customer_panel.return_value = True

        call_order: list[str] = []

        async def _record_edit(*args: Any, **kwargs: Any) -> None:
            call_order.append("edit_or_answer")

        async def _record_answer(*args: Any, **kwargs: Any) -> None:
            call_order.append("answer_callback")

        mock_edit_or_answer_customers_panel.side_effect = _record_edit
        mock_answer_callback_query_via_runtime.side_effect = _record_answer

        callback = SimpleNamespace(answer=AsyncMock(spec=[]))
        user = SimpleNamespace(id=202)

        initial_denied = len(FENCE["denied_connects"])
        initial_redis = len(FENCE["live_redis_constructs"])

        result = await show_user_panel_customers_callback(callback, user)

        self.assertIsNone(result)

        mock_async_session_local.assert_called_once_with()
        session_context.aenter.assert_awaited_once_with()
        session_context.aexit.assert_awaited_once_with(None, None, None)

        mock_can_use_customer_panel.assert_awaited_once_with(sentinel_session, user)
        can_args, can_kwargs = mock_can_use_customer_panel.await_args
        self.assertEqual(can_args, (sentinel_session, user))
        self.assertEqual(can_kwargs, {})

        mock_edit_or_answer_customers_panel.assert_awaited_once_with(
            callback,
            user.id,
            edit=True,
            user=user,
        )
        edit_args, edit_kwargs = mock_edit_or_answer_customers_panel.await_args
        self.assertEqual(edit_args, (callback, user.id))
        self.assertEqual(edit_kwargs, {"edit": True, "user": user})

        mock_answer_callback_query_via_runtime.assert_awaited_once_with(callback)
        ack_args, ack_kwargs = mock_answer_callback_query_via_runtime.await_args
        self.assertEqual(ack_args, (callback,))
        self.assertEqual(ack_kwargs, {})

        self.assertEqual(call_order, ["edit_or_answer", "answer_callback"])

        callback.answer.assert_not_called()
        callback.answer.assert_not_awaited()

        self.assertEqual(len(FENCE["denied_connects"]), initial_denied)
        self.assertEqual(len(FENCE["live_redis_constructs"]), initial_redis)


if __name__ == "__main__":
    unittest.main()
