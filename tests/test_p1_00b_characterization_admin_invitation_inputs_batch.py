"""Hermetic characterization tests for admin invitation input normalization handlers."""

from __future__ import annotations

import os
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from bot.handlers.admin import (
    process_invitation_account_name,
    process_invitation_mobile,
)
from bot.states import InvitationCreation


class MinimalFSMContext:
    """Minimal in-memory FSMContext fixture supporting get_data, update_data, set_state, get_state."""

    def __init__(self, initial_data: dict[str, Any] | None = None) -> None:
        self._data: dict[str, Any] = dict(initial_data or {})
        self._state: Any = None

    async def get_data(self) -> dict[str, Any]:
        return dict(self._data)

    async def update_data(self, **kwargs: Any) -> dict[str, Any]:
        self._data.update(kwargs)
        return dict(self._data)

    async def set_state(self, state: Any) -> None:
        self._state = state

    async def get_state(self) -> Any:
        return self._state


def _make_shared_invitation_fixture(
    text: str,
    *,
    initial_data: dict[str, Any] | None = None,
) -> tuple[SimpleNamespace, MinimalFSMContext]:
    message = SimpleNamespace(
        text=text,
        chat=SimpleNamespace(id=1001),
        bot=AsyncMock(),
    )
    state = MinimalFSMContext(initial_data=initial_data)
    return message, state


@contextmanager
def _invitation_collaborators_harness(prompt_message_id: int):
    prompt_message = SimpleNamespace(message_id=prompt_message_id)
    sentinel_cancel_kb = object()
    sentinel_role_kb = object()
    with (
        patch(
            "bot.handlers.admin.answer_incoming_message_via_runtime",
            new_callable=AsyncMock,
            return_value=prompt_message,
        ) as mock_answer,
        patch(
            "bot.handlers.admin.get_commodity_fsm_cancel_keyboard",
            return_value=sentinel_cancel_kb,
        ) as mock_cancel_kb,
        patch(
            "bot.handlers.admin.get_role_selection_keyboard",
            return_value=sentinel_role_kb,
        ) as mock_role_kb,
    ):
        yield mock_answer, sentinel_cancel_kb, sentinel_role_kb


class AdminInvitationInputsCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_account_name_normalizes_and_advances_to_mobile(self) -> None:
        raw_account_input = "   NEW   ACCOUNT_NAME   "
        expected_canonical_name = "new account_name"
        expected_prompt_id = 9001

        message, state = _make_shared_invitation_fixture(raw_account_input)

        with _invitation_collaborators_harness(prompt_message_id=expected_prompt_id) as (
            mock_answer,
            sentinel_cancel_kb,
            _,
        ):
            await process_invitation_account_name(message, state)

            stored_data = await state.get_data()
            self.assertEqual(
                stored_data.get("account_name"),
                expected_canonical_name,
            )
            self.assertIs(
                await state.get_state(),
                InvitationCreation.awaiting_mobile_number,
            )
            self.assertEqual(
                stored_data.get("last_prompt_message_id"),
                expected_prompt_id,
            )
            mock_answer.assert_awaited_once()
            self.assertEqual(
                mock_answer.await_args.kwargs.get("reply_markup"),
                sentinel_cancel_kb,
            )

    async def test_mobile_normalizes_persian_digits_and_advances_to_role(self) -> None:
        persian_mobile_input = "۰۹۱۲۳۴۵۶۷۸۹"
        expected_ascii_mobile = "09123456789"
        expected_prompt_id = 9002

        message, state = _make_shared_invitation_fixture(persian_mobile_input)

        with _invitation_collaborators_harness(prompt_message_id=expected_prompt_id) as (
            mock_answer,
            _,
            sentinel_role_kb,
        ):
            await process_invitation_mobile(message, state)

            stored_data = await state.get_data()
            self.assertEqual(
                stored_data.get("mobile_number"),
                expected_ascii_mobile,
            )
            self.assertIs(
                await state.get_state(),
                InvitationCreation.awaiting_role,
            )
            self.assertEqual(
                stored_data.get("last_prompt_message_id"),
                expected_prompt_id,
            )
            mock_answer.assert_awaited_once()
            self.assertEqual(
                mock_answer.await_args.kwargs.get("reply_markup"),
                sentinel_role_kb,
            )


if __name__ == "__main__":
    unittest.main()
