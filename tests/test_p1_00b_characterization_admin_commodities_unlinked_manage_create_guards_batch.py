"""Stage P1-00b characterization tests for admin commodities message unlinked-user manage and create guards batch.

Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

This test module characterizes unlinked-user guards (user=None) returning None at the first
guard before reading any inbound-event attribute, awaiting or mutating FSM state,
deleting the incoming event, constructing an HTTP client, calling commodity URL/auth helpers,
performing runtime delivery, updating anchor, navigating to list, sleeping, Redis, or network work for
handle_manage_commodities and handle_add_aliases_and_create.
"""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from bot.handlers.admin_commodities import (
    handle_add_aliases_and_create,
    handle_manage_commodities,
)


class _StrictMessageSentinel:
    def __getattr__(self, name: str):
        raise AssertionError(f"Unexpected Message attribute access: {name}")


class _StrictStateSentinel:
    def __init__(self):
        self.set_state = AsyncMock()
        self.get_data = AsyncMock()
        self.update_data = AsyncMock()
        self.clear = AsyncMock()

    def __getattr__(self, name: str):
        raise AssertionError(f"Unexpected FSMContext attribute access: {name}")


class AdminCommoditiesUnlinkedManageCreateGuardsCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    def setUp(self):
        super().setUp()
        self.strict_message = _StrictMessageSentinel()
        self.strict_state = _StrictStateSentinel()

    async def test_manage_commodities_without_user_stops_before_message_state_navigation_or_delivery(
        self,
    ):
        spy_delete_user_message = AsyncMock()
        spy_show_commodity_list = AsyncMock()

        fence = install_p1_00_network_fence()
        denied_connects_before = len(fence["denied_connects"])
        live_redis_before = len(fence["live_redis_constructs"])

        with patch(
            "bot.handlers.admin_commodities.delete_user_message",
            new=spy_delete_user_message,
        ), patch(
            "bot.handlers.admin_commodities.show_commodity_list",
            new=spy_show_commodity_list,
        ):
            result = await handle_manage_commodities(
                message=self.strict_message,  # type: ignore[arg-type]
                user=None,
                state=self.strict_state,      # type: ignore[arg-type]
            )

        self.assertIsNone(result)

        spy_delete_user_message.assert_not_called()
        spy_delete_user_message.assert_not_awaited()
        spy_show_commodity_list.assert_not_called()
        spy_show_commodity_list.assert_not_awaited()

        self.strict_state.set_state.assert_not_awaited()
        self.strict_state.get_data.assert_not_awaited()
        self.strict_state.update_data.assert_not_awaited()
        self.strict_state.clear.assert_not_awaited()

        self.assertEqual(len(fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(fence["live_redis_constructs"]), live_redis_before)

    async def test_add_aliases_and_create_without_user_stops_before_message_state_http_or_delivery(
        self,
    ):
        spy_delete_user_message = AsyncMock()
        spy_answer_incoming = AsyncMock()
        spy_update_anchor = AsyncMock()
        spy_async_client = MagicMock()
        spy_edit_interaction = AsyncMock()
        spy_clear_state_retain_anchor = AsyncMock()
        spy_sleep = AsyncMock()
        spy_show_commodity_list = AsyncMock()

        fence = install_p1_00_network_fence()
        denied_connects_before = len(fence["denied_connects"])
        live_redis_before = len(fence["live_redis_constructs"])

        with patch(
            "bot.handlers.admin_commodities.delete_user_message",
            new=spy_delete_user_message,
        ), patch(
            "bot.handlers.admin_commodities.answer_incoming_message_via_runtime",
            new=spy_answer_incoming,
        ), patch(
            "bot.handlers.admin_commodities.update_anchor",
            new=spy_update_anchor,
        ), patch(
            "bot.handlers.admin_commodities.httpx.AsyncClient",
            new=spy_async_client,
        ), patch(
            "bot.handlers.admin_commodities.edit_interaction_result_via_runtime",
            new=spy_edit_interaction,
        ), patch(
            "bot.handlers.admin_commodities.clear_state_retain_anchor",
            new=spy_clear_state_retain_anchor,
        ), patch(
            "bot.handlers.admin_commodities.asyncio.sleep",
            new=spy_sleep,
        ), patch(
            "bot.handlers.admin_commodities.show_commodity_list",
            new=spy_show_commodity_list,
        ):
            result = await handle_add_aliases_and_create(
                message=self.strict_message,  # type: ignore[arg-type]
                state=self.strict_state,      # type: ignore[arg-type]
                user=None,
            )

        self.assertIsNone(result)

        spy_delete_user_message.assert_not_called()
        spy_delete_user_message.assert_not_awaited()
        spy_answer_incoming.assert_not_called()
        spy_answer_incoming.assert_not_awaited()
        spy_update_anchor.assert_not_called()
        spy_update_anchor.assert_not_awaited()
        spy_async_client.assert_not_called()
        spy_edit_interaction.assert_not_called()
        spy_edit_interaction.assert_not_awaited()
        spy_clear_state_retain_anchor.assert_not_called()
        spy_clear_state_retain_anchor.assert_not_awaited()
        spy_sleep.assert_not_called()
        spy_sleep.assert_not_awaited()
        spy_show_commodity_list.assert_not_called()
        spy_show_commodity_list.assert_not_awaited()

        self.strict_state.set_state.assert_not_awaited()
        self.strict_state.get_data.assert_not_awaited()
        self.strict_state.update_data.assert_not_awaited()
        self.strict_state.clear.assert_not_awaited()

        self.assertEqual(len(fence["denied_connects"]), denied_connects_before)
        self.assertEqual(len(fence["live_redis_constructs"]), live_redis_before)


if __name__ == "__main__":
    unittest.main()
