"""Direct-characterization tests for bot.handlers.admin.process_invitation_role.

This module characterizes this handler seed only and does not claim the 24
origin-matrix cells, update canonical evidence counts, or mark P1-00b COMPLETE.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from bot.handlers.admin import process_invitation_role  # noqa: E402
from core.enums import UserRole  # noqa: E402
from core.invitation_creation_contracts import (  # noqa: E402
    InternalInvitationCreateRequest,
    InvitationRequesterIdentity,
    build_standard_invitation_idempotency_key,
)
from core.server_routing import SERVER_FOREIGN  # noqa: E402
from models.database import Base  # noqa: E402
from models.invitation import Invitation, InvitationKind  # noqa: E402


class FakeCallback:
    """Minimal callback double providing only the attributes used by process_invitation_role."""

    def __init__(self, data: str) -> None:
        self.data = data


class FakeFSMContext:
    """Minimal FSMContext double providing only get_data and clear."""

    def __init__(self, data: dict[str, Any] | None = None) -> None:
        self._data = dict(data or {})
        self.cleared = False

    async def get_data(self) -> dict[str, Any]:
        return dict(self._data)

    async def clear(self) -> None:
        self.cleared = True
        self._data.clear()


class FakeUser:
    """Minimal User double providing only the attributes used by process_invitation_role."""

    def __init__(
        self,
        *,
        id: int,
        account_name: str,
        mobile_number: str,
        telegram_id: int,
        role: UserRole,
    ) -> None:
        self.id = id
        self.account_name = account_name
        self.mobile_number = mobile_number
        self.telegram_id = telegram_id
        self.role = role


class CharacterizeInvitationRoleTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit tests characterizing process_invitation_role."""

    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self._temp_dir.name, "characterization_invitations.db")
        self.engine = create_engine(f"sqlite:///{self.db_path}")
        Base.metadata.create_all(bind=self.engine, tables=[Invitation.__table__])

    def tearDown(self) -> None:
        self.engine.dispose()
        self._temp_dir.cleanup()

    @patch("bot.handlers.admin._return_to_admin_panel", new_callable=AsyncMock)
    @patch("bot.handlers.admin.answer_callback_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.admin.answer_callback_query_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.admin.forward_standard_invitation_to_iran")
    async def test_successful_standard_role_forwards_without_local_invitation_row(
        self,
        mock_forward: AsyncMock,
        mock_answer_callback_query: AsyncMock,
        mock_answer_callback_message: AsyncMock,
        mock_return_to_admin_panel: AsyncMock,
    ) -> None:
        inviter = FakeUser(
            id=42,
            account_name="admin_creator",
            mobile_number="09120000000",
            telegram_id=1234567,
            role=UserRole.SUPER_ADMIN,
        )
        target_account_name = "standard_user"
        target_mobile_number = "09123456789"
        deterministic_bot_link = "https://t.me/test_bot?start=INV-test-token-standard-42"
        deterministic_web_link = "https://web.example.com/i/STD00042"

        expected_payload = {
            "requester_identity": {
                "account_name": "admin_creator",
                "mobile_number": "09120000000",
                "telegram_id": 1234567,
            },
            "account_name": "standard_user",
            "mobile_number": "09123456789",
            "role": "عادی",
            "source_server": "foreign",
            "idempotency_key": "standard-invitation:f5ea0a21864f9508b9bfffae012713bcb2b9abfb",
        }
        expected_message_text = (
            "✅ لینک دعوت برای نقش **عادی** آماده است:\n\n"
            f"**نام کاربری:** `{target_account_name}`\n"
            f"**موبایل:** `{target_mobile_number}`\n\n"
            "لینک تلگرام:\n"
            f"`{deterministic_bot_link}`\n\n"
            "لینک وب‌اپ:\n"
            f"`{deterministic_web_link}`"
        )

        captured_payloads: list[dict[str, Any]] = []
        validated_requests: list[InternalInvitationCreateRequest] = []
        execution_order: list[str] = []

        state = FakeFSMContext({
            "account_name": target_account_name,
            "mobile_number": target_mobile_number,
        })
        original_clear = state.clear

        async def recording_clear() -> None:
            execution_order.append("state_clear")
            await original_clear()

        state.clear = recording_clear

        async def fake_forward_adapter(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            execution_order.append("forward")
            captured_payloads.append(payload)
            validated_requests.append(InternalInvitationCreateRequest.model_validate(payload))
            return 200, {
                "bot_link": deterministic_bot_link,
                "web_link": deterministic_web_link,
            }

        mock_forward.side_effect = fake_forward_adapter

        async def recording_answer_callback_message(*args: Any, **kwargs: Any) -> None:
            execution_order.append("success_message")

        mock_answer_callback_message.side_effect = recording_answer_callback_message

        async def recording_return_to_admin_panel(*args: Any, **kwargs: Any) -> None:
            execution_order.append("return_to_admin_panel")

        mock_return_to_admin_panel.side_effect = recording_return_to_admin_panel

        async def recording_answer_callback_query(*args: Any, **kwargs: Any) -> None:
            execution_order.append("callback_ack")

        mock_answer_callback_query.side_effect = recording_answer_callback_query

        callback = FakeCallback(data="set_role_STANDARD")
        bot = object()

        with Session(self.engine) as session:
            rows_before = session.execute(select(Invitation)).scalars().all()
            self.assertEqual(len(rows_before), 0)

        await process_invitation_role(callback, state, inviter, bot)

        with Session(self.engine) as session:
            rows_after = session.execute(select(Invitation)).scalars().all()
            self.assertEqual(len(rows_after), 0)

        self.assertEqual(
            execution_order,
            [
                "state_clear",
                "forward",
                "success_message",
                "return_to_admin_panel",
                "callback_ack",
            ],
        )

        self.assertTrue(state.cleared)
        self.assertEqual(await state.get_data(), {})

        self.assertEqual(len(captured_payloads), 1)
        self.assertEqual(captured_payloads[0], expected_payload)
        mock_forward.assert_awaited_once_with(expected_payload)

        self.assertEqual(len(validated_requests), 1)
        validated_req = validated_requests[0]
        self.assertEqual(validated_req.requester_identity.account_name, inviter.account_name)
        self.assertEqual(validated_req.requester_identity.mobile_number, inviter.mobile_number)
        self.assertEqual(validated_req.requester_identity.telegram_id, inviter.telegram_id)
        self.assertEqual(validated_req.account_name, target_account_name)
        self.assertEqual(validated_req.mobile_number, target_mobile_number)
        self.assertEqual(validated_req.source_server, SERVER_FOREIGN)
        self.assertEqual(validated_req.role, UserRole.STANDARD)
        self.assertEqual(
            validated_req.idempotency_key,
            "standard-invitation:f5ea0a21864f9508b9bfffae012713bcb2b9abfb",
        )

        mock_answer_callback_message.assert_awaited_once_with(
            callback,
            inviter,
            expected_message_text,
            parse_mode="Markdown",
            reply_markup=None,
        )
        mock_return_to_admin_panel.assert_awaited_once_with(
            callback,
            state,
            bot,
            user=inviter,
            user_role=inviter.role,
        )
        mock_answer_callback_query.assert_awaited_once_with(callback)

    @patch("bot.handlers.admin._return_to_admin_panel", new_callable=AsyncMock)
    @patch("bot.handlers.admin.answer_callback_message_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.admin.answer_callback_query_via_runtime", new_callable=AsyncMock)
    @patch("bot.handlers.admin.forward_standard_invitation_to_iran", new_callable=AsyncMock)
    async def test_middle_manager_cannot_select_police_role_fail_closed(
        self,
        mock_forward: AsyncMock,
        mock_answer_callback_query: AsyncMock,
        mock_answer_callback_message: AsyncMock,
        mock_return_to_admin_panel: AsyncMock,
    ) -> None:
        middle_manager = FakeUser(
            id=99,
            account_name="middle_manager",
            mobile_number="09129999999",
            telegram_id=99001,
            role=UserRole.MIDDLE_MANAGER,
        )
        initial_fsm_data = {
            "account_name": "target_user",
            "mobile_number": "09123456789",
        }
        state = FakeFSMContext(initial_fsm_data)
        callback = FakeCallback(data="set_role_POLICE")
        bot = object()

        await process_invitation_role(callback, state, middle_manager, bot)

        mock_answer_callback_query.assert_awaited_once_with(
            callback,
            "این نقش برای شما مجاز نیست.",
            show_alert=True,
        )
        mock_forward.assert_not_called()
        mock_answer_callback_message.assert_not_called()
        mock_return_to_admin_panel.assert_not_called()

        self.assertFalse(state.cleared)
        self.assertEqual(await state.get_data(), initial_fsm_data)

        with Session(self.engine) as session:
            rows = session.execute(select(Invitation)).scalars().all()
            self.assertEqual(len(rows), 0)


if __name__ == "__main__":
    unittest.main()
