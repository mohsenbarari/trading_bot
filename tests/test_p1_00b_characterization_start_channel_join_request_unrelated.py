"""Stage P1-00b characterization: start channel join request unrelated channel guard.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: BOT-IDENTITY-CHAT-JOIN-REQUEST-START-HANDLE-CHANNEL-JOIN-REQUEST-L1524
Scenario: Configured-but-unrelated channel early-return outcome.

Explicitly excluded:
- channel_id-is-None guard
- matching-channel lookup
- user existence/account/activation decisions
- onboarding mutation/commit
- approval
- decline
- notification success/failure
- database query/result behavior
- persistence
- retry/reload
- origin-matrix behavior
- runtime source changes
- existing-test edits
- project-document or inventory changes
- blocker/seed closure
- reconciliation
- Stage closure
- production
- deploy
- DNS
- Web Writer
- secrets
- Git

This partial characterization unit closes no seed, blocker, reconciliation, or Stage.
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (  # noqa: E402
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from bot.handlers.start import handle_channel_join_request  # noqa: E402


class StartChannelJoinRequestCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_unrelated_channel_returns_before_session_or_telegram_side_effects(self):
        bot = SimpleNamespace(
            approve_chat_join_request=AsyncMock(),
            decline_chat_join_request=AsyncMock(),
        )
        join_request = SimpleNamespace(
            chat=SimpleNamespace(id=-1009999999999),
            from_user=SimpleNamespace(id=424242),
            user_chat_id=424242,
            bot=bot,
        )

        configured_channel_id = -1001234567890
        self.assertNotEqual(join_request.chat.id, configured_channel_id)

        with patch(
            "bot.handlers.start.settings.channel_id", configured_channel_id
        ), patch(
            "bot.handlers.start.AsyncSessionLocal"
        ) as mock_session_local, patch(
            "bot.handlers.start.bot_account_access_denial_reason"
        ) as mock_denial_reason, patch(
            "bot.handlers.start._direct_registration_runtime_ready"
        ) as mock_direct_reg_ready, patch(
            "bot.handlers.start.registration_activation_block_for_user",
            new_callable=AsyncMock,
        ) as mock_reg_activation_block, patch(
            "bot.handlers.start.evaluate_bot_access",
            new_callable=AsyncMock,
        ) as mock_evaluate_access, patch(
            "bot.handlers.start.answer_pre_auth_chat_via_runtime",
            new_callable=AsyncMock,
        ) as mock_answer_pre_auth:
            result = await handle_channel_join_request(join_request)

        self.assertIsNone(result)

        # Synchronous dependencies were not called
        mock_session_local.assert_not_called()
        mock_denial_reason.assert_not_called()
        mock_direct_reg_ready.assert_not_called()

        # Asynchronous dependencies were not awaited or called
        mock_reg_activation_block.assert_not_awaited()
        mock_reg_activation_block.assert_not_called()
        mock_evaluate_access.assert_not_awaited()
        mock_evaluate_access.assert_not_called()
        mock_answer_pre_auth.assert_not_awaited()
        mock_answer_pre_auth.assert_not_called()

        # Request bot Telegram surfaces were not awaited or called
        bot.approve_chat_join_request.assert_not_awaited()
        bot.approve_chat_join_request.assert_not_called()
        bot.decline_chat_join_request.assert_not_awaited()
        bot.decline_chat_join_request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
