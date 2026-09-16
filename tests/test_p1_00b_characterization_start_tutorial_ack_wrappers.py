"""Hermetic characterization tests for start tutorial acknowledgment callback wrappers."""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from bot.handlers.start import (
    handle_customer_tutorial_ack,
    handle_offer_tutorial_ack,
)
from bot.onboarding import (
    CUSTOMER_TUTORIAL_STEP,
    OFFER_TUTORIAL_STEP,
)


class StartTutorialAckWrappersCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_offer_tutorial_ack_delegates_with_offer_step(self) -> None:
        callback_sentinel = object()
        user_sentinel = object()

        with patch(
            "bot.handlers.start._handle_bot_onboarding_ack",
            new_callable=AsyncMock,
        ) as mock_handle_ack:
            result = await handle_offer_tutorial_ack(callback_sentinel, user_sentinel)

            self.assertIsNone(result)
            mock_handle_ack.assert_awaited_once_with(
                callback_sentinel,
                user_sentinel,
                acknowledged_step=OFFER_TUTORIAL_STEP,
            )
            self.assertEqual(len(mock_handle_ack.await_args.args), 2)
            self.assertIs(mock_handle_ack.await_args.args[0], callback_sentinel)
            self.assertIs(mock_handle_ack.await_args.args[1], user_sentinel)
            self.assertEqual(
                mock_handle_ack.await_args.kwargs,
                {"acknowledged_step": OFFER_TUTORIAL_STEP},
            )
            self.assertEqual(OFFER_TUTORIAL_STEP, 1)

    async def test_customer_tutorial_ack_delegates_with_customer_step(self) -> None:
        callback_sentinel = object()
        user_sentinel = object()

        with patch(
            "bot.handlers.start._handle_bot_onboarding_ack",
            new_callable=AsyncMock,
        ) as mock_handle_ack:
            result = await handle_customer_tutorial_ack(callback_sentinel, user_sentinel)

            self.assertIsNone(result)
            mock_handle_ack.assert_awaited_once_with(
                callback_sentinel,
                user_sentinel,
                acknowledged_step=CUSTOMER_TUTORIAL_STEP,
            )
            self.assertEqual(len(mock_handle_ack.await_args.args), 2)
            self.assertIs(mock_handle_ack.await_args.args[0], callback_sentinel)
            self.assertIs(mock_handle_ack.await_args.args[1], user_sentinel)
            self.assertEqual(
                mock_handle_ack.await_args.kwargs,
                {"acknowledged_step": CUSTOMER_TUTORIAL_STEP},
            )
            self.assertEqual(CUSTOMER_TUTORIAL_STEP, 2)


if __name__ == "__main__":
    unittest.main()
