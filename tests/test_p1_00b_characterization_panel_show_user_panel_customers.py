"""Direct-characterization tests for bot.handlers.panel.show_user_panel_customers.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Seed: BOT-TG-MESSAGE-PANEL-SHOW-USER-PANEL-CUSTOMERS-L789
Canonical gap: docs/refactor/two-server/section-1/inventory/surface-bot.json#BOT-TG-MESSAGE-PANEL-SHOW-USER-PANEL-CUSTOMERS-L789

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
    show_user_panel_customers,
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


class CharacterizePanelShowUserPanelCustomersTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing bot.handlers.panel.show_user_panel_customers."""

    @patch("bot.handlers.panel._edit_or_answer_customers_panel", new_callable=AsyncMock)
    @patch("bot.handlers.panel.answer_incoming_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel._can_use_customer_panel", new_callable=AsyncMock)
    @patch("bot.handlers.panel.AsyncSessionLocal")
    async def test_show_user_panel_customers_no_user_suppressed(
        self,
        mock_async_session_local: MagicMock,
        mock_can_use_customer_panel: AsyncMock,
        mock_answer_incoming_message_via_runtime: AsyncMock,
        mock_edit_or_answer_customers_panel: AsyncMock,
    ) -> None:
        message = SimpleNamespace(answer=AsyncMock(spec=[]))
        state = SimpleNamespace()

        initial_denied = len(FENCE["denied_connects"])
        initial_redis = len(FENCE["live_redis_constructs"])

        result = await show_user_panel_customers(message, state, None)

        self.assertIsNone(result)

        mock_async_session_local.assert_not_called()

        mock_can_use_customer_panel.assert_not_called()
        mock_can_use_customer_panel.assert_not_awaited()

        mock_answer_incoming_message_via_runtime.assert_not_called()
        mock_answer_incoming_message_via_runtime.assert_not_awaited()

        mock_edit_or_answer_customers_panel.assert_not_called()
        mock_edit_or_answer_customers_panel.assert_not_awaited()

        message.answer.assert_not_called()
        message.answer.assert_not_awaited()

        self.assertEqual(len(FENCE["denied_connects"]), initial_denied)
        self.assertEqual(len(FENCE["live_redis_constructs"]), initial_redis)

    @patch("bot.handlers.panel._edit_or_answer_customers_panel", new_callable=AsyncMock)
    @patch("bot.handlers.panel.answer_incoming_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel._can_use_customer_panel", new_callable=AsyncMock)
    @patch("bot.handlers.panel.AsyncSessionLocal")
    async def test_show_user_panel_customers_linked_user_access_denied(
        self,
        mock_async_session_local: MagicMock,
        mock_can_use_customer_panel: AsyncMock,
        mock_answer_incoming_message_via_runtime: AsyncMock,
        mock_edit_or_answer_customers_panel: AsyncMock,
    ) -> None:
        sentinel_session = SimpleNamespace(name="sentinel_session_denied")
        session_context = _SessionContext(sentinel_session)
        mock_async_session_local.return_value = session_context
        mock_can_use_customer_panel.return_value = False

        message = SimpleNamespace(answer=AsyncMock(spec=[]))
        state = SimpleNamespace()
        user = SimpleNamespace(id=101)

        initial_denied = len(FENCE["denied_connects"])
        initial_redis = len(FENCE["live_redis_constructs"])

        result = await show_user_panel_customers(message, state, user)

        self.assertIsNone(result)

        mock_async_session_local.assert_called_once_with()
        session_context.aenter.assert_awaited_once_with()
        session_context.aexit.assert_awaited_once_with(None, None, None)

        mock_can_use_customer_panel.assert_awaited_once_with(sentinel_session, user)
        can_args, can_kwargs = mock_can_use_customer_panel.await_args
        self.assertEqual(can_args, (sentinel_session, user))
        self.assertEqual(can_kwargs, {})

        expected_denied_text = "این بخش برای حساب شما فعال نیست."
        mock_answer_incoming_message_via_runtime.assert_awaited_once_with(
            message,
            user,
            expected_denied_text,
            source_key="panel-customers-not-available",
        )
        denied_args, denied_kwargs = mock_answer_incoming_message_via_runtime.await_args
        self.assertEqual(denied_args, (message, user, expected_denied_text))
        self.assertEqual(
            denied_kwargs,
            {"source_key": "panel-customers-not-available"},
        )

        mock_edit_or_answer_customers_panel.assert_not_called()
        mock_edit_or_answer_customers_panel.assert_not_awaited()

        message.answer.assert_not_called()
        message.answer.assert_not_awaited()

        self.assertEqual(len(FENCE["denied_connects"]), initial_denied)
        self.assertEqual(len(FENCE["live_redis_constructs"]), initial_redis)

    @patch("bot.handlers.panel._edit_or_answer_customers_panel", new_callable=AsyncMock)
    @patch("bot.handlers.panel.answer_incoming_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.panel._can_use_customer_panel", new_callable=AsyncMock)
    @patch("bot.handlers.panel.AsyncSessionLocal")
    async def test_show_user_panel_customers_access_allowed(
        self,
        mock_async_session_local: MagicMock,
        mock_can_use_customer_panel: AsyncMock,
        mock_answer_incoming_message_via_runtime: AsyncMock,
        mock_edit_or_answer_customers_panel: AsyncMock,
    ) -> None:
        sentinel_session = SimpleNamespace(name="sentinel_session_allowed")
        session_context = _SessionContext(sentinel_session)
        mock_async_session_local.return_value = session_context
        mock_can_use_customer_panel.return_value = True

        message = SimpleNamespace(answer=AsyncMock(spec=[]))
        state = SimpleNamespace()
        user = SimpleNamespace(id=202)

        initial_denied = len(FENCE["denied_connects"])
        initial_redis = len(FENCE["live_redis_constructs"])

        result = await show_user_panel_customers(message, state, user)

        self.assertIsNone(result)

        mock_async_session_local.assert_called_once_with()
        session_context.aenter.assert_awaited_once_with()
        session_context.aexit.assert_awaited_once_with(None, None, None)

        mock_can_use_customer_panel.assert_awaited_once_with(sentinel_session, user)
        can_args, can_kwargs = mock_can_use_customer_panel.await_args
        self.assertEqual(can_args, (sentinel_session, user))
        self.assertEqual(can_kwargs, {})

        mock_edit_or_answer_customers_panel.assert_awaited_once_with(
            message,
            user.id,
            edit=False,
            user=user,
        )
        edit_args, edit_kwargs = mock_edit_or_answer_customers_panel.await_args
        self.assertEqual(edit_args, (message, user.id))
        self.assertEqual(edit_kwargs, {"edit": False, "user": user})

        mock_answer_incoming_message_via_runtime.assert_not_called()
        mock_answer_incoming_message_via_runtime.assert_not_awaited()

        message.answer.assert_not_called()
        message.answer.assert_not_awaited()

        self.assertEqual(len(FENCE["denied_connects"]), initial_denied)
        self.assertEqual(len(FENCE["live_redis_constructs"]), initial_redis)


if __name__ == "__main__":
    unittest.main()
