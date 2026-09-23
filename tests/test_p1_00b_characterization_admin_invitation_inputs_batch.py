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

import bot.handlers.admin as admin_handlers
from bot.handlers.admin import (
    process_invitation_account_name,
    process_invitation_mobile,
)
from bot.keyboards import get_invitable_roles_for_admin
from bot.states import InvitationCreation
from core.enums import UserRole
from core.telegram_delivery_runtime_policy import (
    TelegramDeliveryRuntimeDecision,
    TelegramDeliveryRuntimeMode,
)


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

    async def test_account_name_invalid_queue_suppresses_stale_prompt_delete(self) -> None:
        raw_invalid_account_input = "ab"
        previous_prompt_id = 7001
        expected_error_prompt_id = 9011
        expected_error_text = (
            "❌ **نام کاربری نامعتبر است.**\n"
            "لطفاً فقط از حروف، اعداد، آندرلاین و فاصله بین کلمات استفاده کنید (۳ تا ۳۲ کاراکتر)."
        )

        message, state = _make_shared_invitation_fixture(
            raw_invalid_account_input,
            initial_data={"last_prompt_message_id": previous_prompt_id},
        )
        await state.set_state(InvitationCreation.awaiting_account_name)

        with (
            patch(
                "bot.handlers.admin.configured_telegram_delivery_runtime",
                return_value=TelegramDeliveryRuntimeDecision(
                    mode=TelegramDeliveryRuntimeMode.QUEUE_V1,
                    legacy_workers_enabled=False,
                    queue_worker_enabled=True,
                ),
            ),
            _invitation_collaborators_harness(
                prompt_message_id=expected_error_prompt_id,
            ) as (
                mock_answer,
                sentinel_cancel_kb,
                _,
            ),
        ):
            await process_invitation_account_name(message, state)

            message.bot.delete_message.assert_not_awaited()
            mock_answer.assert_awaited_once_with(
                message,
                None,
                expected_error_text,
                reply_markup=sentinel_cancel_kb,
                parse_mode="Markdown",
            )
            stored_data = await state.get_data()
            self.assertEqual(
                stored_data.get("last_prompt_message_id"),
                expected_error_prompt_id,
            )
            self.assertIs(
                await state.get_state(),
                InvitationCreation.awaiting_account_name,
            )
            self.assertNotIn("account_name", stored_data)

    async def test_mobile_invalid_queue_suppresses_stale_prompt_delete(self) -> None:
        raw_invalid_mobile_input = "invalid-mobile"
        previous_prompt_id = 7002
        expected_error_prompt_id = 9012
        expected_error_text = (
            "❌ **شماره موبایل نامعتبر است.**\n"
            "لطفاً شماره را با فرمت 09123456789 (فارسی یا انگلیسی) وارد کنید."
        )

        message, state = _make_shared_invitation_fixture(
            raw_invalid_mobile_input,
            initial_data={"last_prompt_message_id": previous_prompt_id},
        )
        await state.set_state(InvitationCreation.awaiting_mobile_number)

        with (
            patch(
                "bot.handlers.admin.configured_telegram_delivery_runtime",
                return_value=TelegramDeliveryRuntimeDecision(
                    mode=TelegramDeliveryRuntimeMode.QUEUE_V1,
                    legacy_workers_enabled=False,
                    queue_worker_enabled=True,
                ),
            ),
            _invitation_collaborators_harness(
                prompt_message_id=expected_error_prompt_id,
            ) as (
                mock_answer,
                sentinel_cancel_kb,
                _,
            ),
        ):
            await process_invitation_mobile(message, state)

            message.bot.delete_message.assert_not_awaited()
            mock_answer.assert_awaited_once_with(
                message,
                None,
                expected_error_text,
                reply_markup=sentinel_cancel_kb,
                parse_mode="Markdown",
            )
            stored_data = await state.get_data()
            self.assertEqual(
                stored_data.get("last_prompt_message_id"),
                expected_error_prompt_id,
            )
            self.assertIs(
                await state.get_state(),
                InvitationCreation.awaiting_mobile_number,
            )
            self.assertNotIn("mobile_number", stored_data)

    async def test_account_name_valid_queue_suppresses_stale_delete_and_advances(
        self,
    ) -> None:
        raw_account_input = "   VALID   ACCOUNT_NAME   "
        expected_canonical_name = "valid account_name"
        previous_prompt_id = 7001
        expected_prompt_id = 9001
        expected_prompt_text = (
            f"✅ نام کاربری `{expected_canonical_name}` ثبت شد.\n"
            "حالا **شماره موبایل** کاربر را وارد کنید (مثال: 09123456789):"
        )

        message, state = _make_shared_invitation_fixture(
            raw_account_input,
            initial_data={"last_prompt_message_id": previous_prompt_id},
        )
        await state.set_state(InvitationCreation.awaiting_account_name)

        with (
            patch(
                "bot.handlers.admin.configured_telegram_delivery_runtime",
                return_value=TelegramDeliveryRuntimeDecision(
                    mode=TelegramDeliveryRuntimeMode.QUEUE_V1,
                    legacy_workers_enabled=False,
                    queue_worker_enabled=True,
                ),
            ),
            _invitation_collaborators_harness(
                prompt_message_id=expected_prompt_id,
            ) as (
                mock_answer,
                sentinel_cancel_kb,
                _,
            ),
        ):
            await process_invitation_account_name(message, state)

            message.bot.delete_message.assert_not_awaited()
            mock_answer.assert_awaited_once_with(
                message,
                None,
                expected_prompt_text,
                reply_markup=sentinel_cancel_kb,
                parse_mode="Markdown",
            )
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

    async def test_mobile_valid_queue_suppresses_stale_delete_and_advances(
        self,
    ) -> None:
        persian_mobile_input = "۰۹۱۲۳۴۵۶۷۸۹"
        expected_ascii_mobile = "09123456789"
        inviter_role_value = UserRole.SUPER_ADMIN.value
        previous_prompt_id = 7002
        expected_prompt_id = 9002
        expected_prompt_text = (
            f"✅ شماره موبایل `{expected_ascii_mobile}` ثبت شد.\n"
            "لطفاً **نقش (سطح دسترسی)** کاربر را انتخاب کنید:"
        )
        expected_invitable_roles = get_invitable_roles_for_admin(UserRole.SUPER_ADMIN)

        message, state = _make_shared_invitation_fixture(
            persian_mobile_input,
            initial_data={
                "last_prompt_message_id": previous_prompt_id,
                "inviter_role": inviter_role_value,
            },
        )
        await state.set_state(InvitationCreation.awaiting_mobile_number)

        with (
            patch(
                "bot.handlers.admin.configured_telegram_delivery_runtime",
                return_value=TelegramDeliveryRuntimeDecision(
                    mode=TelegramDeliveryRuntimeMode.QUEUE_V1,
                    legacy_workers_enabled=False,
                    queue_worker_enabled=True,
                ),
            ),
            patch(
                "bot.handlers.admin.get_invitable_roles_for_admin",
                wraps=get_invitable_roles_for_admin,
            ) as mock_get_invitable_roles,
            _invitation_collaborators_harness(
                prompt_message_id=expected_prompt_id,
            ) as (
                mock_answer,
                _,
                sentinel_role_kb,
            ),
        ):
            await process_invitation_mobile(message, state)

            message.bot.delete_message.assert_not_awaited()
            mock_get_invitable_roles.assert_called_once_with(UserRole.SUPER_ADMIN)
            admin_handlers.get_role_selection_keyboard.assert_called_once_with(
                expected_invitable_roles,
            )
            mock_answer.assert_awaited_once_with(
                message,
                None,
                expected_prompt_text,
                reply_markup=sentinel_role_kb,
                parse_mode="Markdown",
            )
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


if __name__ == "__main__":
    unittest.main()
