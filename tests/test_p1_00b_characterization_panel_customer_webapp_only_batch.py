import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import unittest
from unittest.mock import AsyncMock, patch

from bot.handlers.panel import (
    CUSTOMER_RELATION_MANAGE_WEBAPP_ONLY_TEXT,
    USER_PANEL_INVITE_TIER2_WEBAPP_ONLY_TEXT,
    ask_unlink_user_panel_customer,
    confirm_unlink_user_panel_customer,
    user_panel_customer_invite_tier2_webapp_only,
)


class _PoisonInput:
    """Poison input sentinel that raises on attribute access to prove handlers do not inspect inputs."""

    def __getattr__(self, name: str) -> None:
        raise AssertionError(f"Unexpected access to attribute: {name}")


class PanelCustomerWebappOnlyBatchCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization tests for webapp-only customer panel callback handlers."""

    async def test_ask_unlink_reports_webapp_only_without_input_access(self):
        callback = object()
        poison_data = _PoisonInput()
        poison_user = _PoisonInput()

        with patch(
            "bot.handlers.panel.answer_callback_query_via_runtime",
            new_callable=AsyncMock,
        ) as mock_answer:
            result = await ask_unlink_user_panel_customer(
                callback,
                poison_data,
                poison_user,
            )

        self.assertIsNone(result)
        mock_answer.assert_awaited_once_with(
            callback,
            CUSTOMER_RELATION_MANAGE_WEBAPP_ONLY_TEXT,
            show_alert=True,
        )
        self.assertIs(mock_answer.await_args.args[0], callback)

    async def test_confirm_unlink_reports_webapp_only_without_input_access(self):
        callback = object()
        poison_data = _PoisonInput()
        poison_user = _PoisonInput()

        with patch(
            "bot.handlers.panel.answer_callback_query_via_runtime",
            new_callable=AsyncMock,
        ) as mock_answer:
            result = await confirm_unlink_user_panel_customer(
                callback,
                poison_data,
                poison_user,
            )

        self.assertIsNone(result)
        mock_answer.assert_awaited_once_with(
            callback,
            CUSTOMER_RELATION_MANAGE_WEBAPP_ONLY_TEXT,
            show_alert=True,
        )
        self.assertIs(mock_answer.await_args.args[0], callback)

    async def test_tier2_invite_reports_webapp_only_without_user_access(self):
        callback = object()
        poison_user = _PoisonInput()

        with patch(
            "bot.handlers.panel.answer_callback_query_via_runtime",
            new_callable=AsyncMock,
        ) as mock_answer:
            result = await user_panel_customer_invite_tier2_webapp_only(
                callback,
                poison_user,
            )

        self.assertIsNone(result)
        mock_answer.assert_awaited_once_with(
            callback,
            USER_PANEL_INVITE_TIER2_WEBAPP_ONLY_TEXT,
            show_alert=True,
        )
        self.assertIs(mock_answer.await_args.args[0], callback)


if __name__ == "__main__":
    unittest.main()
