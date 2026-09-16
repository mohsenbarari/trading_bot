"""Hermetic characterization tests for start registration guidance handlers."""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from bot.handlers.start import (
    handle_contact_non_contact,
    handle_registration_confirmation_message,
)
from bot.states import Registration

EXPECTED_CONTACT_GUIDANCE_TEXT = (
    "شماره تماس باید مستقیماً از حساب تلگرام خودتان ارسال شود. "
    "از دکمه «ارسال شماره همراه» استفاده کنید."
)
EXPECTED_CONFIRMATION_GUIDANCE_TEXT = (
    "برای ادامه از دکمه «تایید و تکمیل ثبت\u200cنام» یا «اصلاح آدرس» استفاده کنید."
)


class MinimalFSMContext:
    """Minimal in-memory FSMContext fixture supporting get_data, update_data, set_state, get_state."""

    def __init__(
        self,
        initial_data: dict[str, Any] | None = None,
        initial_state: Any = None,
    ) -> None:
        self._data: dict[str, Any] = dict(initial_data or {})
        self._state: Any = initial_state

    async def get_data(self) -> dict[str, Any]:
        return dict(self._data)

    async def update_data(self, **kwargs: Any) -> dict[str, Any]:
        self._data.update(kwargs)
        return dict(self._data)

    async def set_state(self, state: Any) -> None:
        self._state = state

    async def get_state(self) -> Any:
        return self._state


def _make_shared_registration_fixture(
    *,
    initial_state: Any,
    text: str = "guidance-trigger",
) -> tuple[SimpleNamespace, MinimalFSMContext]:
    message = SimpleNamespace(
        text=text,
        chat=SimpleNamespace(id=1001, type="private"),
        bot=AsyncMock(),
    )
    state = MinimalFSMContext(initial_state=initial_state)
    return message, state


class StartRegistrationGuidanceCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_contact_direct_registration_guides_contact_button(self) -> None:
        message, state = _make_shared_registration_fixture(
            initial_state=Registration.awaiting_contact,
        )

        with (
            patch(
                "bot.handlers.start._reject_non_private_registration",
                new_callable=AsyncMock,
                return_value=False,
            ) as mock_reject,
            patch(
                "bot.handlers.start._direct_registration_runtime_ready",
                return_value=True,
            ) as mock_ready,
            patch(
                "bot.handlers.start.answer_pre_auth_message_via_runtime",
                new_callable=AsyncMock,
            ) as mock_answer,
        ):
            await handle_contact_non_contact(message, state)

            mock_reject.assert_awaited_once_with(message, state)
            mock_ready.assert_called_once_with()
            self.assertIs(await state.get_state(), Registration.awaiting_contact)
            self.assertEqual(await state.get_data(), {})
            mock_answer.assert_awaited_once_with(message, EXPECTED_CONTACT_GUIDANCE_TEXT)
            self.assertEqual(
                mock_answer.await_args.args,
                (message, EXPECTED_CONTACT_GUIDANCE_TEXT),
            )
            self.assertEqual(mock_answer.await_args.kwargs, {})

    async def test_confirmation_state_message_guides_action_buttons(self) -> None:
        message, state = _make_shared_registration_fixture(
            initial_state=Registration.awaiting_confirmation,
        )

        with (
            patch(
                "bot.handlers.start._reject_non_private_registration",
                new_callable=AsyncMock,
                return_value=False,
            ) as mock_reject,
            patch(
                "bot.handlers.start.answer_pre_auth_message_via_runtime",
                new_callable=AsyncMock,
            ) as mock_answer,
        ):
            await handle_registration_confirmation_message(message, state)

            mock_reject.assert_awaited_once_with(message, state)
            self.assertIs(await state.get_state(), Registration.awaiting_confirmation)
            self.assertEqual(await state.get_data(), {})
            mock_answer.assert_awaited_once_with(message, EXPECTED_CONFIRMATION_GUIDANCE_TEXT)
            self.assertEqual(
                mock_answer.await_args.args,
                (message, EXPECTED_CONFIRMATION_GUIDANCE_TEXT),
            )
            self.assertEqual(mock_answer.await_args.kwargs, {})


if __name__ == "__main__":
    unittest.main()
