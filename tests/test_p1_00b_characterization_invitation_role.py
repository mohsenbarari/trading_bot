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
    async def test_successful_standard_role_creation_backed_by_sqlite_row(
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
        expected_token = "INV-test-token-standard-42"
        expected_short_code = "STD00042"
        deterministic_bot_link = f"https://t.me/test_bot?start={expected_token}"
        deterministic_web_link = f"https://web.example.com/i/{expected_short_code}"

        captured_payloads: list[dict[str, Any]] = []
        validated_requests: list[InternalInvitationCreateRequest] = []

        async def fake_forward_adapter(payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
            captured_payloads.append(payload)
            validated = InternalInvitationCreateRequest.model_validate(payload)
            validated_requests.append(validated)

            with Session(self.engine) as session:
                invitation = Invitation(
                    account_name=validated.account_name,
                    mobile_number=validated.mobile_number,
                    token=expected_token,
                    short_code=expected_short_code,
                    role=validated.role,
                    kind=InvitationKind.STANDARD,
                    created_by_id=inviter.id,
                    is_used=False,
                    expires_at=datetime.utcnow() + timedelta(days=7),
                )
                session.add(invitation)
                session.commit()

            return 200, {
                "bot_link": deterministic_bot_link,
                "web_link": deterministic_web_link,
            }

        mock_forward.side_effect = fake_forward_adapter

        state = FakeFSMContext({
            "account_name": target_account_name,
            "mobile_number": target_mobile_number,
        })
        callback = FakeCallback(data="set_role_STANDARD")
        bot = object()

        await process_invitation_role(callback, state, inviter, bot)

        self.assertEqual(len(captured_payloads), 1)
        self.assertEqual(len(validated_requests), 1)
        validated_req = validated_requests[0]

        self.assertEqual(validated_req.requester_identity.account_name, inviter.account_name)
        self.assertEqual(validated_req.requester_identity.mobile_number, inviter.mobile_number)
        self.assertEqual(validated_req.requester_identity.telegram_id, inviter.telegram_id)
        self.assertEqual(validated_req.source_server, SERVER_FOREIGN)
        self.assertEqual(validated_req.role, UserRole.STANDARD)

        expected_idempotency_key = build_standard_invitation_idempotency_key(
            requester_identity=InvitationRequesterIdentity(
                account_name=inviter.account_name,
                mobile_number=inviter.mobile_number,
                telegram_id=inviter.telegram_id,
            ),
            account_name=target_account_name,
            mobile_number=target_mobile_number,
            role=UserRole.STANDARD,
        )
        self.assertEqual(validated_req.idempotency_key, expected_idempotency_key)

        self.assertTrue(state.cleared)
        self.assertEqual(await state.get_data(), {})

        mock_answer_callback_message.assert_awaited_once()
        message_text = mock_answer_callback_message.await_args.args[2]
        self.assertIn(deterministic_bot_link, message_text)
        self.assertIn(deterministic_web_link, message_text)

        mock_answer_callback_query.assert_awaited_once_with(callback)
        mock_return_to_admin_panel.assert_awaited_once_with(
            callback,
            state,
            bot,
            user=inviter,
            user_role=inviter.role,
        )

        with Session(self.engine) as session:
            stmt = select(Invitation).where(Invitation.token == expected_token)
            persisted = session.execute(stmt).scalar_one_or_none()
            self.assertIsNotNone(persisted)
            self.assertEqual(persisted.account_name, target_account_name)
            self.assertEqual(persisted.mobile_number, target_mobile_number)
            self.assertEqual(persisted.role, UserRole.STANDARD)
            self.assertEqual(persisted.kind, InvitationKind.STANDARD)
            self.assertEqual(persisted.created_by_id, inviter.id)
            self.assertFalse(persisted.is_used)

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
