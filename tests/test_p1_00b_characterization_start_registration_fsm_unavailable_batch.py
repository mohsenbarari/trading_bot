"""Hermetic characterization tests for start registration unavailable FSM handlers."""

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
    handle_address,
    handle_registration_edit_address,
)
from bot.message_manager import DeleteDelay
from bot.states import Registration

EXPECTED_ADDRESS_UNAVAILABLE_TEXT = (
    "ثبت\u200cنام مستقیم تلگرام موقتاً در دسترس نیست. کمی بعد دوباره تلاش کنید."
)
EXPECTED_EDIT_ADDRESS_UNAVAILABLE_TEXT = (
    "ثبت\u200cنام مستقیم تلگرام موقتاً در دسترس نیست."
)


class MinimalFSMContext:
    """Minimal in-memory FSMContext fixture supporting get_data, update_data, set_state, get_state, clear."""

    def __init__(
        self,
        initial_data: dict[str, Any] | None = None,
        initial_state: Any = None,
    ) -> None:
        self._data: dict[str, Any] = dict(initial_data or {})
        self._state: Any = initial_state
        self.clear_call_count: int = 0

    async def get_data(self) -> dict[str, Any]:
        return dict(self._data)

    async def update_data(self, **kwargs: Any) -> dict[str, Any]:
        self._data.update(kwargs)
        return dict(self._data)

    async def set_state(self, state: Any) -> None:
        self._state = state

    async def get_state(self) -> Any:
        return self._state

    async def clear(self) -> None:
        self.clear_call_count += 1
        self._data.clear()
        self._state = None


def _make_shared_registration_fixture(
    event_type: str,
    *,
    initial_state: Any,
    initial_data: dict[str, Any] | None = None,
) -> tuple[SimpleNamespace, MinimalFSMContext]:
    bot = AsyncMock()
    chat = SimpleNamespace(id=1001, type="private")
    from_user = SimpleNamespace(id=2001, username="test_user")
    message = SimpleNamespace(
        bot=bot,
        chat=chat,
        from_user=from_user,
        text="تهران، خیابان آزادی",
        answer=AsyncMock(),
    )
    sentinel_data = (
        {"flow": "registration_sentinel", "step": "address"}
        if initial_data is None
        else initial_data
    )
    state = MinimalFSMContext(
        initial_data=sentinel_data,
        initial_state=initial_state,
    )
    if event_type == "message":
        return message, state
    if event_type == "callback":
        callback = SimpleNamespace(
            bot=bot,
            id="cb_query_1001",
            from_user=from_user,
            message=message,
            data="reg:edit_address",
        )
        return callback, state
    raise ValueError(f"Unsupported event_type: {event_type}")


class StartRegistrationFSMUnavailableCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_handle_address_reports_unavailable_when_fsm_read_fails(self) -> None:
        message, state = _make_shared_registration_fixture(
            "message",
            initial_state=Registration.awaiting_address,
        )
        expected_data = await state.get_data()
        call_order: list[str] = []

        with (
            patch(
                "bot.handlers.start._reject_non_private_registration",
                new_callable=AsyncMock,
                return_value=False,
            ) as mock_reject,
            patch(
                "bot.handlers.start.delete_previous_anchor",
                new_callable=AsyncMock,
            ) as mock_delete_anchor,
            patch(
                "bot.handlers.start._read_registration_fsm",
                new_callable=AsyncMock,
                return_value=None,
            ) as mock_read_fsm,
            patch(
                "bot.handlers.start.answer_pre_auth_message_via_runtime",
                new_callable=AsyncMock,
            ) as mock_answer_msg,
            patch(
                "bot.handlers.start._direct_registration_runtime_ready",
                return_value=True,
            ) as mock_ready,
            patch(
                "bot.handlers.start._write_registration_fsm",
                new_callable=AsyncMock,
            ) as mock_write,
            patch(
                "bot.handlers.start.set_anchor",
            ) as mock_set_anchor,
        ):
            mock_delete_anchor.side_effect = lambda *a, **kw: call_order.append(
                "delete_previous_anchor"
            )
            mock_answer_msg.side_effect = lambda *a, **kw: call_order.append(
                "answer_pre_auth_message_via_runtime"
            )

            result = await handle_address(message, state)

            self.assertIsNone(result)
            mock_reject.assert_awaited_once_with(message, state)
            self.assertIs(mock_reject.await_args.args[0], message)
            self.assertIs(mock_reject.await_args.args[1], state)
            mock_read_fsm.assert_awaited_once_with(state)
            self.assertIs(mock_read_fsm.await_args.args[0], state)

            mock_delete_anchor.assert_awaited_once_with(
                message.bot, message.chat.id, delay=DeleteDelay.DEFAULT.value
            )
            mock_answer_msg.assert_awaited_once_with(
                message, EXPECTED_ADDRESS_UNAVAILABLE_TEXT
            )
            self.assertEqual(
                mock_answer_msg.await_args.args,
                (message, EXPECTED_ADDRESS_UNAVAILABLE_TEXT),
            )
            self.assertEqual(mock_answer_msg.await_args.kwargs, {})

            self.assertEqual(
                call_order,
                ["delete_previous_anchor", "answer_pre_auth_message_via_runtime"],
            )
            mock_ready.assert_not_called()
            mock_write.assert_not_called()
            mock_set_anchor.assert_not_called()
            self.assertEqual(state.clear_call_count, 0)
            self.assertEqual(await state.get_data(), expected_data)
            self.assertIs(await state.get_state(), Registration.awaiting_address)

    async def test_handle_registration_edit_address_alerts_unavailable_when_fsm_read_fails(
        self,
    ) -> None:
        callback, state = _make_shared_registration_fixture(
            "callback",
            initial_state=Registration.awaiting_confirmation,
        )
        expected_data = await state.get_data()

        with (
            patch(
                "bot.handlers.start._reject_non_private_registration",
                new_callable=AsyncMock,
                return_value=False,
            ) as mock_reject,
            patch(
                "bot.handlers.start._read_registration_fsm",
                new_callable=AsyncMock,
                return_value=None,
            ) as mock_read_fsm,
            patch(
                "bot.handlers.start.answer_callback_query_via_runtime",
                new_callable=AsyncMock,
            ) as mock_answer_cb,
            patch(
                "bot.handlers.start._direct_registration_runtime_ready",
                return_value=True,
            ) as mock_ready,
            patch(
                "bot.handlers.start._write_registration_fsm",
                new_callable=AsyncMock,
            ) as mock_write,
            patch(
                "bot.handlers.start.answer_pre_auth_callback_message_via_runtime",
                new_callable=AsyncMock,
            ) as mock_callback_msg,
        ):
            result = await handle_registration_edit_address(callback, state)

            self.assertIsNone(result)
            mock_reject.assert_awaited_once_with(callback, state)
            self.assertIs(mock_reject.await_args.args[0], callback)
            self.assertIs(mock_reject.await_args.args[1], state)
            mock_read_fsm.assert_awaited_once_with(state)
            self.assertIs(mock_read_fsm.await_args.args[0], state)

            mock_answer_cb.assert_awaited_once_with(
                callback, EXPECTED_EDIT_ADDRESS_UNAVAILABLE_TEXT, show_alert=True
            )
            self.assertEqual(
                mock_answer_cb.await_args.args,
                (callback, EXPECTED_EDIT_ADDRESS_UNAVAILABLE_TEXT),
            )
            self.assertEqual(mock_answer_cb.await_args.kwargs, {"show_alert": True})

            mock_ready.assert_not_called()
            mock_write.assert_not_called()
            mock_callback_msg.assert_not_called()
            callback.message.answer.assert_not_called()
            self.assertEqual(state.clear_call_count, 0)
            self.assertEqual(await state.get_data(), expected_data)
            self.assertIs(
                await state.get_state(), Registration.awaiting_confirmation
            )


if __name__ == "__main__":
    unittest.main()
