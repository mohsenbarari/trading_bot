"""Characterization tests for POST /sessions/recovery/{recovery_id}/request-identity.

P1-00b Stage card: docs/refactor/two-server/section-1/stages/P1-00b.md
Shared Assignment: /root/trading-bot/trading_bot/.local/two-site-refactor/assignments/WA-P1-00B-FREEZE-20260904-001.md
Tracked High gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Exact API seed: docs/refactor/two-server/section-1/inventory/surface-api.json#API-AUTH-POST-SESSIONS-REQUEST-SINGLE-SESSION-RECOVERY-IDENTITY-L807

This partial characterization neither promotes the seed nor closes P1-00b.
"""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

import unittest
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

from api.routers.sessions import (
    recovery_request_to_dict,
    request_single_session_recovery_identity,
)
from core.services.single_session_recovery_service import (
    build_identity_requested_sms_text,
    request_identity_verification,
)
from models.session import SingleSessionRecoveryStatus
from models.user import UserRole


class FakeDB:
    """Hermetic in-memory test double for AsyncSession without live dependencies."""

    def __init__(self, *, on_commit=None) -> None:
        self.execute_results: list[object] = []
        self._on_commit = on_commit
        self.commit = AsyncMock(side_effect=self._commit_impl)
        self.rollback = AsyncMock()
        self.execute = AsyncMock(side_effect=self._unexpected_execute)
        self.add = Mock()
        self.flush = AsyncMock()
        self.refresh = AsyncMock()
        self.delete = AsyncMock()

    async def _commit_impl(self) -> None:
        if self._on_commit is not None:
            self._on_commit()

    async def _unexpected_execute(self, _stmt: object) -> object:
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)


def make_user(
    *,
    user_id: int = 7,
    role: UserRole = UserRole.STANDARD,
    mobile_number: str = "09120000000",
    **overrides: object,
) -> SimpleNamespace:
    data: dict[str, object] = {
        "id": user_id,
        "role": role,
        "mobile_number": mobile_number,
        "max_sessions": 1,
        "is_deleted": False,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def make_recovery(
    *,
    recovery_id: uuid.UUID | None = None,
    user_id: int = 7,
    status: SingleSessionRecoveryStatus = SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW,
    created_at: datetime | None = None,
    inline_action_expires_at: datetime | None = None,
    chat_action_expires_at: datetime | None = None,
    **overrides: object,
) -> SimpleNamespace:
    now = datetime(2026, 9, 4, 10, 0, 0)
    data: dict[str, object] = {
        "id": recovery_id or uuid.UUID("11111111-1111-1111-1111-111111111111"),
        "user_id": user_id,
        "requester_device_name": "Firefox on Linux",
        "requester_ip": "198.51.100.42",
        "status": status,
        "created_at": created_at or now,
        "inline_action_expires_at": inline_action_expires_at or (now + timedelta(seconds=30)),
        "chat_action_expires_at": chat_action_expires_at or (now + timedelta(hours=2)),
        "identity_requested_at": None,
        "identity_submitted_at": None,
        "decided_at": None,
        "cancelled_at": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class TestRequestSingleSessionRecoveryIdentityCharacterization(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_uuid_aborts_after_non_accountant_guard(self) -> None:
        db = FakeDB()
        admin_user = make_user(user_id=99, role=UserRole.SUPER_ADMIN)
        invalid_recovery_id = "invalid-not-a-uuid"
        order_trail: list[str] = []

        async def tracked_guard(db_arg, user_arg):
            order_trail.append("guard")
            self.assertIs(db_arg, db)
            self.assertIs(user_arg, admin_user)

        with patch(
            "api.routers.sessions.ensure_non_accountant_session_management",
            new_callable=AsyncMock,
            side_effect=tracked_guard,
        ) as mock_guard, patch(
            "api.routers.sessions._ensure_recovery_admin_access",
            new_callable=AsyncMock,
        ) as mock_access, patch(
            "api.routers.sessions.request_identity_verification",
        ) as mock_transition, patch(
            "api.routers.sessions._clear_recovery_admin_action_messages",
            new_callable=AsyncMock,
        ) as mock_clear, patch(
            "api.routers.sessions.send_sms",
        ) as mock_sms, patch(
            "api.routers.sessions._publish_recovery_prompt_updates",
            new_callable=AsyncMock,
        ) as mock_publish, patch(
            "api.routers.sessions.recovery_request_to_dict",
        ) as mock_serialize:
            with self.assertRaises(HTTPException) as exc_cm:
                await request_single_session_recovery_identity(
                    recovery_id=invalid_recovery_id,
                    db=db,
                    current_user=admin_user,
                )

        self.assertEqual(exc_cm.exception.status_code, 400)
        self.assertEqual(exc_cm.exception.detail, "شناسه بازیابی نامعتبر است")
        self.assertEqual(order_trail, ["guard"])

        mock_guard.assert_awaited_once_with(db, admin_user)
        self.assertIs(mock_guard.await_args.args[0], db)
        self.assertIs(mock_guard.await_args.args[1], admin_user)

        mock_access.assert_not_called()
        mock_transition.assert_not_called()
        mock_clear.assert_not_called()
        mock_sms.assert_not_called()
        mock_publish.assert_not_called()
        mock_serialize.assert_not_called()

        db.commit.assert_not_called()
        db.rollback.assert_not_called()
        db.execute.assert_not_called()
        db.add.assert_not_called()
        db.flush.assert_not_called()
        db.refresh.assert_not_called()
        db.delete.assert_not_called()
        self.assertEqual(len(db.execute_results), 0)

    async def test_valid_uuid_non_pending_status_rejected_after_admin_access(self) -> None:
        db = FakeDB()
        admin_user = make_user(user_id=99, role=UserRole.SUPER_ADMIN)
        valid_recovery_uuid = uuid.UUID("44444444-4444-4444-4444-444444444444")
        valid_recovery_id_str = str(valid_recovery_uuid)

        recovery_request = make_recovery(
            recovery_id=valid_recovery_uuid,
            status=SingleSessionRecoveryStatus.IDENTITY_SUBMITTED,
        )
        requester_user = make_user(user_id=7, role=UserRole.STANDARD, mobile_number="09121112233")
        admin_target = SimpleNamespace(
            id=uuid.UUID("33333333-3333-3333-3333-333333333333"),
            recovery_request=recovery_request,
            admin_user=admin_user,
        )

        order_trail: list[str] = []

        async def tracked_guard(db_arg, user_arg):
            order_trail.append("guard")
            self.assertIs(db_arg, db)
            self.assertIs(user_arg, admin_user)

        async def tracked_access(db_arg, *, recovery_id, current_user):
            order_trail.append("access")
            self.assertIs(db_arg, db)
            self.assertEqual(recovery_id, valid_recovery_uuid)
            self.assertIsInstance(recovery_id, uuid.UUID)
            self.assertIs(current_user, admin_user)
            return (admin_target, recovery_request, requester_user)

        with patch(
            "api.routers.sessions.ensure_non_accountant_session_management",
            new_callable=AsyncMock,
            side_effect=tracked_guard,
        ) as mock_guard, patch(
            "api.routers.sessions._ensure_recovery_admin_access",
            new_callable=AsyncMock,
            side_effect=tracked_access,
        ) as mock_access, patch(
            "api.routers.sessions.request_identity_verification",
        ) as mock_transition, patch(
            "api.routers.sessions._clear_recovery_admin_action_messages",
            new_callable=AsyncMock,
        ) as mock_clear, patch(
            "api.routers.sessions.send_sms",
        ) as mock_sms, patch(
            "api.routers.sessions._publish_recovery_prompt_updates",
            new_callable=AsyncMock,
        ) as mock_publish, patch(
            "api.routers.sessions.recovery_request_to_dict",
        ) as mock_serialize:
            with self.assertRaises(HTTPException) as exc_cm:
                await request_single_session_recovery_identity(
                    recovery_id=valid_recovery_id_str,
                    db=db,
                    current_user=admin_user,
                )

        self.assertEqual(exc_cm.exception.status_code, 400)
        self.assertEqual(exc_cm.exception.detail, "در وضعیت فعلی امکان درخواست مدرک وجود ندارد")

        self.assertEqual(order_trail, ["guard", "access"])
        mock_guard.assert_awaited_once_with(db, admin_user)
        self.assertIs(mock_guard.await_args.args[0], db)
        self.assertIs(mock_guard.await_args.args[1], admin_user)

        mock_access.assert_awaited_once_with(db, recovery_id=valid_recovery_uuid, current_user=admin_user)
        self.assertIs(mock_access.await_args.args[0], db)
        self.assertEqual(mock_access.await_args.kwargs["recovery_id"], valid_recovery_uuid)
        self.assertIsInstance(mock_access.await_args.kwargs["recovery_id"], uuid.UUID)
        self.assertIs(mock_access.await_args.kwargs["current_user"], admin_user)

        mock_transition.assert_not_called()
        mock_clear.assert_not_called()
        mock_sms.assert_not_called()
        mock_publish.assert_not_called()
        mock_serialize.assert_not_called()

        db.commit.assert_not_called()
        db.rollback.assert_not_called()
        db.execute.assert_not_called()
        db.add.assert_not_called()
        db.flush.assert_not_called()
        db.refresh.assert_not_called()
        db.delete.assert_not_called()
        self.assertEqual(len(db.execute_results), 0)

    async def test_pending_review_success_transitions_state_commits_warns_sms_and_serializes(self) -> None:
        order_trail: list[str] = []

        db = FakeDB(on_commit=lambda: order_trail.append("commit"))
        admin_user = make_user(user_id=99, role=UserRole.SUPER_ADMIN)
        requester_user = make_user(
            user_id=7,
            role=UserRole.STANDARD,
            mobile_number="09123456789",
        )

        recovery_uuid = uuid.UUID("11111111-1111-1111-1111-111111111111")
        recovery_id_str = str(recovery_uuid)

        fixed_service_time = datetime(2026, 9, 4, 10, 5, 0)
        created_at = datetime(2026, 9, 4, 10, 0, 0)
        initial_inline_expires_at = datetime(2026, 9, 4, 10, 0, 30)
        initial_chat_expires_at = datetime(2026, 9, 4, 12, 0, 0)

        recovery_request = make_recovery(
            recovery_id=recovery_uuid,
            user_id=requester_user.id,
            status=SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW,
            created_at=created_at,
            inline_action_expires_at=initial_inline_expires_at,
            chat_action_expires_at=initial_chat_expires_at,
            requester_device_name="Firefox on Linux",
            requester_ip="198.51.100.42",
        )

        admin_target = SimpleNamespace(
            id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
            recovery_request=recovery_request,
            admin_user=admin_user,
            current_action_message_id=505,
        )

        # 1. Guard tracking
        async def tracked_guard(db_arg, user_arg):
            order_trail.append("guard")
            self.assertIs(db_arg, db)
            self.assertIs(user_arg, admin_user)

        # 2. Access tracking
        async def tracked_access(db_arg, *, recovery_id, current_user):
            order_trail.append("access")
            self.assertIs(db_arg, db)
            self.assertEqual(recovery_id, recovery_uuid)
            self.assertIsInstance(recovery_id, uuid.UUID)
            self.assertIs(current_user, admin_user)
            return (admin_target, recovery_request, requester_user)

        # 3. Transition tracking: executes real request_identity_verification
        def tracked_transition(rec_req, *args, **kwargs):
            order_trail.append("transition")
            self.assertIs(rec_req, recovery_request)
            return request_identity_verification(rec_req, *args, **kwargs)

        # 4. Clear tracking
        async def tracked_clear(db_arg, rec_id_arg):
            order_trail.append("clear")
            self.assertIs(db_arg, db)
            self.assertEqual(rec_id_arg, recovery_request.id)
            self.assertIs(rec_id_arg, recovery_request.id)

        # 5. Commit is tracked via db._on_commit

        # 6. SMS attempt: fail with deterministic exception
        sms_error = RuntimeError("deterministic SMS network failure")
        expected_sms_text = build_identity_requested_sms_text()

        def tracked_send_sms(to_mobile, sms_body):
            order_trail.append("sms_attempt")
            self.assertEqual(to_mobile, requester_user.mobile_number)
            self.assertEqual(sms_body, expected_sms_text)
            raise sms_error

        # 7. Publish tracking
        async def tracked_publish(db_arg, req_arg, requester_arg):
            order_trail.append("publish")
            self.assertIs(db_arg, db)
            self.assertIs(req_arg, recovery_request)
            self.assertIs(requester_arg, requester_user)

        # 8. Serialization tracking: executes real recovery_request_to_dict
        def tracked_serialize(rec_req):
            order_trail.append("serialization")
            self.assertIs(rec_req, recovery_request)
            return recovery_request_to_dict(rec_req)

        with patch(
            "core.services.single_session_recovery_service.utc_now_naive",
            return_value=fixed_service_time,
        ), patch(
            "core.utils.utc_now_naive",
            return_value=fixed_service_time,
        ), patch(
            "api.routers.sessions.ensure_non_accountant_session_management",
            new_callable=AsyncMock,
            side_effect=tracked_guard,
        ) as mock_guard, patch(
            "api.routers.sessions._ensure_recovery_admin_access",
            new_callable=AsyncMock,
            side_effect=tracked_access,
        ) as mock_access, patch(
            "api.routers.sessions.request_identity_verification",
            side_effect=tracked_transition,
        ) as mock_transition, patch(
            "api.routers.sessions._clear_recovery_admin_action_messages",
            new_callable=AsyncMock,
            side_effect=tracked_clear,
        ) as mock_clear, patch(
            "api.routers.sessions.send_sms",
            side_effect=tracked_send_sms,
        ) as mock_sms, patch(
            "api.routers.sessions.logger.warning",
        ) as mock_logger_warning, patch(
            "api.routers.sessions._publish_recovery_prompt_updates",
            new_callable=AsyncMock,
            side_effect=tracked_publish,
        ) as mock_publish, patch(
            "api.routers.sessions.recovery_request_to_dict",
            side_effect=tracked_serialize,
        ) as mock_serialize:
            result = await request_single_session_recovery_identity(
                recovery_id=recovery_id_str,
                db=db,
                current_user=admin_user,
            )

        # Order of execution
        self.assertEqual(
            order_trail,
            [
                "guard",
                "access",
                "transition",
                "clear",
                "commit",
                "sms_attempt",
                "publish",
                "serialization",
            ],
        )

        # 1. Guard boundary
        mock_guard.assert_awaited_once_with(db, admin_user)
        self.assertIs(mock_guard.await_args.args[0], db)
        self.assertIs(mock_guard.await_args.args[1], admin_user)

        # 2. Access boundary
        mock_access.assert_awaited_once_with(db, recovery_id=recovery_uuid, current_user=admin_user)
        self.assertIs(mock_access.await_args.args[0], db)
        self.assertEqual(mock_access.await_args.kwargs["recovery_id"], recovery_uuid)
        self.assertIsInstance(mock_access.await_args.kwargs["recovery_id"], uuid.UUID)
        self.assertIs(mock_access.await_args.kwargs["current_user"], admin_user)

        # 3. Transition boundary
        mock_transition.assert_called_once_with(recovery_request)
        self.assertIs(mock_transition.call_args.args[0], recovery_request)

        # 4. Clear boundary
        mock_clear.assert_awaited_once_with(db, recovery_request.id)
        self.assertIs(mock_clear.await_args.args[0], db)
        self.assertEqual(mock_clear.await_args.args[1], recovery_request.id)
        self.assertIs(mock_clear.await_args.args[1], recovery_request.id)

        # 5. Commit boundary
        db.commit.assert_awaited_once()

        # 6. SMS attempt boundary
        mock_sms.assert_called_once_with(requester_user.mobile_number, expected_sms_text)
        self.assertEqual(mock_sms.call_args.args[0], requester_user.mobile_number)
        self.assertEqual(mock_sms.call_args.args[1], expected_sms_text)

        # Warning path exercised
        mock_logger_warning.assert_called_once_with(
            "Failed to send recovery identity-request SMS user=%s: %s",
            requester_user.id,
            sms_error,
        )
        self.assertEqual(mock_logger_warning.call_args.args[1], requester_user.id)
        self.assertIs(mock_logger_warning.call_args.args[2], sms_error)

        # 7. Publish boundary
        mock_publish.assert_awaited_once_with(db, recovery_request, requester_user)
        self.assertIs(mock_publish.await_args.args[0], db)
        self.assertIs(mock_publish.await_args.args[1], recovery_request)
        self.assertIs(mock_publish.await_args.args[2], requester_user)

        # 8. Serialization boundary
        mock_serialize.assert_called_once_with(recovery_request)
        self.assertIs(mock_serialize.call_args.args[0], recovery_request)

        # State transition verification
        expected_inline_expires_at = fixed_service_time + timedelta(seconds=30)
        expected_chat_expires_at = fixed_service_time + timedelta(seconds=7200)

        self.assertIs(recovery_request.status, SingleSessionRecoveryStatus.IDENTITY_VERIFICATION_REQUESTED)
        self.assertEqual(recovery_request.status.value, "identity_verification_requested")
        self.assertEqual(recovery_request.identity_requested_at, fixed_service_time)
        self.assertEqual(recovery_request.inline_action_expires_at, expected_inline_expires_at)
        self.assertEqual(recovery_request.chat_action_expires_at, expected_chat_expires_at)

        # Serialized response verification byte-for-value
        expected_response = {
            "detail": "درخواست ارسال مدرک برای کاربر ثبت شد",
            "recovery": {
                "id": "11111111-1111-1111-1111-111111111111",
                "status": "identity_verification_requested",
                "requester_device_name": "Firefox on Linux",
                "requester_ip": "198.51.100.42",
                "created_at": "2026-09-04T10:00:00",
                "inline_action_expires_at": "2026-09-04T10:05:30",
                "chat_action_expires_at": "2026-09-04T12:05:00",
                "identity_requested_at": "2026-09-04T10:05:00",
                "identity_submitted_at": None,
                "decided_at": None,
                "cancelled_at": None,
            },
        }
        self.assertEqual(result, expected_response)

        # FakeDB assertions: one commit, no rollback, no execute/add/flush/refresh/delete
        self.assertEqual(db.commit.await_count, 1)
        db.rollback.assert_not_called()
        db.execute.assert_not_called()
        db.add.assert_not_called()
        db.flush.assert_not_called()
        db.refresh.assert_not_called()
        db.delete.assert_not_called()
        self.assertEqual(len(db.execute_results), 0)

    async def test_pending_review_sms_success_after_commit_and_publishes(self) -> None:
        order_trail: list[str] = []

        db = FakeDB(on_commit=lambda: order_trail.append("commit"))
        admin_user = make_user(user_id=99, role=UserRole.SUPER_ADMIN)
        requester_user = make_user(
            user_id=7,
            role=UserRole.STANDARD,
            mobile_number="09123456789",
        )

        recovery_uuid = uuid.UUID("11111111-1111-1111-1111-111111111111")
        recovery_id_str = str(recovery_uuid)

        fixed_service_time = datetime(2026, 9, 4, 10, 5, 0)
        created_at = datetime(2026, 9, 4, 10, 0, 0)
        initial_inline_expires_at = datetime(2026, 9, 4, 10, 0, 30)
        initial_chat_expires_at = datetime(2026, 9, 4, 12, 0, 0)

        recovery_request = make_recovery(
            recovery_id=recovery_uuid,
            user_id=requester_user.id,
            status=SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW,
            created_at=created_at,
            inline_action_expires_at=initial_inline_expires_at,
            chat_action_expires_at=initial_chat_expires_at,
            requester_device_name="Firefox on Linux",
            requester_ip="198.51.100.42",
        )

        admin_target = SimpleNamespace(
            id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
            recovery_request=recovery_request,
            admin_user=admin_user,
            current_action_message_id=505,
        )

        async def tracked_guard(db_arg, user_arg):
            order_trail.append("guard")
            self.assertIs(db_arg, db)
            self.assertIs(user_arg, admin_user)

        async def tracked_access(db_arg, *, recovery_id, current_user):
            order_trail.append("access")
            self.assertIs(db_arg, db)
            self.assertEqual(recovery_id, recovery_uuid)
            self.assertIsInstance(recovery_id, uuid.UUID)
            self.assertIs(current_user, admin_user)
            return (admin_target, recovery_request, requester_user)

        def tracked_transition(rec_req, *args, **kwargs):
            order_trail.append("transition")
            self.assertIs(rec_req, recovery_request)
            return request_identity_verification(rec_req, *args, **kwargs)

        async def tracked_clear(db_arg, rec_id_arg):
            order_trail.append("clear")
            self.assertIs(db_arg, db)
            self.assertEqual(rec_id_arg, recovery_request.id)
            self.assertIs(rec_id_arg, recovery_request.id)

        expected_sms_text = build_identity_requested_sms_text()

        def tracked_send_sms(to_mobile, sms_body):
            order_trail.append("sms")
            self.assertEqual(to_mobile, requester_user.mobile_number)
            self.assertEqual(sms_body, expected_sms_text)

        async def tracked_publish(db_arg, req_arg, requester_arg):
            order_trail.append("publish")
            self.assertIs(db_arg, db)
            self.assertIs(req_arg, recovery_request)
            self.assertIs(requester_arg, requester_user)

        def tracked_serialize(rec_req):
            order_trail.append("serialization")
            self.assertIs(rec_req, recovery_request)
            return recovery_request_to_dict(rec_req)

        with patch(
            "core.services.single_session_recovery_service.utc_now_naive",
            return_value=fixed_service_time,
        ), patch(
            "core.utils.utc_now_naive",
            return_value=fixed_service_time,
        ), patch(
            "api.routers.sessions.ensure_non_accountant_session_management",
            new_callable=AsyncMock,
            side_effect=tracked_guard,
        ) as mock_guard, patch(
            "api.routers.sessions._ensure_recovery_admin_access",
            new_callable=AsyncMock,
            side_effect=tracked_access,
        ) as mock_access, patch(
            "api.routers.sessions.request_identity_verification",
            side_effect=tracked_transition,
        ) as mock_transition, patch(
            "api.routers.sessions._clear_recovery_admin_action_messages",
            new_callable=AsyncMock,
            side_effect=tracked_clear,
        ) as mock_clear, patch(
            "api.routers.sessions.send_sms",
            side_effect=tracked_send_sms,
        ) as mock_sms, patch(
            "api.routers.sessions.logger.warning",
        ) as mock_logger_warning, patch(
            "api.routers.sessions._publish_recovery_prompt_updates",
            new_callable=AsyncMock,
            side_effect=tracked_publish,
        ) as mock_publish, patch(
            "api.routers.sessions.recovery_request_to_dict",
            side_effect=tracked_serialize,
        ) as mock_serialize:
            result = await request_single_session_recovery_identity(
                recovery_id=recovery_id_str,
                db=db,
                current_user=admin_user,
            )

        self.assertEqual(
            order_trail,
            [
                "guard",
                "access",
                "transition",
                "clear",
                "commit",
                "sms",
                "publish",
                "serialization",
            ],
        )

        mock_guard.assert_awaited_once_with(db, admin_user)
        self.assertIs(mock_guard.await_args.args[0], db)
        self.assertIs(mock_guard.await_args.args[1], admin_user)

        mock_access.assert_awaited_once_with(db, recovery_id=recovery_uuid, current_user=admin_user)
        self.assertIs(mock_access.await_args.args[0], db)
        self.assertEqual(mock_access.await_args.kwargs["recovery_id"], recovery_uuid)
        self.assertIsInstance(mock_access.await_args.kwargs["recovery_id"], uuid.UUID)
        self.assertIs(mock_access.await_args.kwargs["current_user"], admin_user)

        mock_transition.assert_called_once_with(recovery_request)
        self.assertIs(mock_transition.call_args.args[0], recovery_request)

        mock_clear.assert_awaited_once_with(db, recovery_request.id)
        self.assertIs(mock_clear.await_args.args[0], db)
        self.assertEqual(mock_clear.await_args.args[1], recovery_request.id)
        self.assertIs(mock_clear.await_args.args[1], recovery_request.id)

        db.commit.assert_awaited_once()

        mock_sms.assert_called_once_with(requester_user.mobile_number, expected_sms_text)
        self.assertEqual(mock_sms.call_args.args[0], requester_user.mobile_number)
        self.assertEqual(mock_sms.call_args.args[1], expected_sms_text)

        mock_logger_warning.assert_not_called()

        mock_publish.assert_awaited_once_with(db, recovery_request, requester_user)
        self.assertIs(mock_publish.await_args.args[0], db)
        self.assertIs(mock_publish.await_args.args[1], recovery_request)
        self.assertIs(mock_publish.await_args.args[2], requester_user)

        mock_serialize.assert_called_once_with(recovery_request)
        self.assertIs(mock_serialize.call_args.args[0], recovery_request)

        expected_inline_expires_at = fixed_service_time + timedelta(seconds=30)
        expected_chat_expires_at = fixed_service_time + timedelta(seconds=7200)

        self.assertIs(recovery_request.status, SingleSessionRecoveryStatus.IDENTITY_VERIFICATION_REQUESTED)
        self.assertEqual(recovery_request.status.value, "identity_verification_requested")
        self.assertEqual(recovery_request.identity_requested_at, fixed_service_time)
        self.assertEqual(recovery_request.inline_action_expires_at, expected_inline_expires_at)
        self.assertEqual(recovery_request.chat_action_expires_at, expected_chat_expires_at)

        expected_response = {
            "detail": "درخواست ارسال مدرک برای کاربر ثبت شد",
            "recovery": {
                "id": "11111111-1111-1111-1111-111111111111",
                "status": "identity_verification_requested",
                "requester_device_name": "Firefox on Linux",
                "requester_ip": "198.51.100.42",
                "created_at": "2026-09-04T10:00:00",
                "inline_action_expires_at": "2026-09-04T10:05:30",
                "chat_action_expires_at": "2026-09-04T12:05:00",
                "identity_requested_at": "2026-09-04T10:05:00",
                "identity_submitted_at": None,
                "decided_at": None,
                "cancelled_at": None,
            },
        }
        self.assertEqual(result, expected_response)

        self.assertEqual(db.commit.await_count, 1)
        db.rollback.assert_not_called()
        db.execute.assert_not_called()
        db.add.assert_not_called()
        db.flush.assert_not_called()
        db.refresh.assert_not_called()
        db.delete.assert_not_called()
        self.assertEqual(len(db.execute_results), 0)

    async def test_pending_review_publish_failure_propagates_after_commit(self) -> None:
        order_trail: list[str] = []

        db = FakeDB(on_commit=lambda: order_trail.append("commit"))
        admin_user = make_user(user_id=99, role=UserRole.SUPER_ADMIN)
        requester_user = make_user(
            user_id=7,
            role=UserRole.STANDARD,
            mobile_number="09123456789",
        )

        recovery_uuid = uuid.UUID("11111111-1111-1111-1111-111111111111")
        recovery_id_str = str(recovery_uuid)

        fixed_service_time = datetime(2026, 9, 4, 10, 5, 0)
        created_at = datetime(2026, 9, 4, 10, 0, 0)
        initial_inline_expires_at = datetime(2026, 9, 4, 10, 0, 30)
        initial_chat_expires_at = datetime(2026, 9, 4, 12, 0, 0)

        recovery_request = make_recovery(
            recovery_id=recovery_uuid,
            user_id=requester_user.id,
            status=SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW,
            created_at=created_at,
            inline_action_expires_at=initial_inline_expires_at,
            chat_action_expires_at=initial_chat_expires_at,
            requester_device_name="Firefox on Linux",
            requester_ip="198.51.100.42",
        )

        admin_target = SimpleNamespace(
            id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
            recovery_request=recovery_request,
            admin_user=admin_user,
            current_action_message_id=505,
        )

        async def tracked_guard(db_arg, user_arg):
            order_trail.append("guard")
            self.assertIs(db_arg, db)
            self.assertIs(user_arg, admin_user)

        async def tracked_access(db_arg, *, recovery_id, current_user):
            order_trail.append("access")
            self.assertIs(db_arg, db)
            self.assertEqual(recovery_id, recovery_uuid)
            self.assertIsInstance(recovery_id, uuid.UUID)
            self.assertIs(current_user, admin_user)
            return (admin_target, recovery_request, requester_user)

        def tracked_transition(rec_req, *args, **kwargs):
            order_trail.append("transition")
            self.assertIs(rec_req, recovery_request)
            return request_identity_verification(rec_req, *args, **kwargs)

        async def tracked_clear(db_arg, rec_id_arg):
            order_trail.append("clear")
            self.assertIs(db_arg, db)
            self.assertEqual(rec_id_arg, recovery_request.id)
            self.assertIs(rec_id_arg, recovery_request.id)

        expected_sms_text = build_identity_requested_sms_text()

        def tracked_send_sms(to_mobile, sms_body):
            order_trail.append("sms")
            self.assertEqual(to_mobile, requester_user.mobile_number)
            self.assertEqual(sms_body, expected_sms_text)

        publish_error = RuntimeError("deterministic publication failure after commit")

        async def tracked_publish(db_arg, req_arg, requester_arg):
            order_trail.append("publish")
            self.assertIs(db_arg, db)
            self.assertIs(req_arg, recovery_request)
            self.assertIs(requester_arg, requester_user)
            raise publish_error

        def tracked_serialize(rec_req):
            order_trail.append("serialization")
            return recovery_request_to_dict(rec_req)

        with patch(
            "core.services.single_session_recovery_service.utc_now_naive",
            return_value=fixed_service_time,
        ), patch(
            "core.utils.utc_now_naive",
            return_value=fixed_service_time,
        ), patch(
            "api.routers.sessions.ensure_non_accountant_session_management",
            new_callable=AsyncMock,
            side_effect=tracked_guard,
        ) as mock_guard, patch(
            "api.routers.sessions._ensure_recovery_admin_access",
            new_callable=AsyncMock,
            side_effect=tracked_access,
        ) as mock_access, patch(
            "api.routers.sessions.request_identity_verification",
            side_effect=tracked_transition,
        ) as mock_transition, patch(
            "api.routers.sessions._clear_recovery_admin_action_messages",
            new_callable=AsyncMock,
            side_effect=tracked_clear,
        ) as mock_clear, patch(
            "api.routers.sessions.send_sms",
            side_effect=tracked_send_sms,
        ) as mock_sms, patch(
            "api.routers.sessions.logger.warning",
        ) as mock_logger_warning, patch(
            "api.routers.sessions._publish_recovery_prompt_updates",
            new_callable=AsyncMock,
            side_effect=tracked_publish,
        ) as mock_publish, patch(
            "api.routers.sessions.recovery_request_to_dict",
            side_effect=tracked_serialize,
        ) as mock_serialize:
            with self.assertRaises(RuntimeError) as exc_cm:
                await request_single_session_recovery_identity(
                    recovery_id=recovery_id_str,
                    db=db,
                    current_user=admin_user,
                )

        self.assertIs(exc_cm.exception, publish_error)

        self.assertEqual(
            order_trail,
            [
                "guard",
                "access",
                "transition",
                "clear",
                "commit",
                "sms",
                "publish",
            ],
        )

        mock_guard.assert_awaited_once_with(db, admin_user)
        self.assertIs(mock_guard.await_args.args[0], db)
        self.assertIs(mock_guard.await_args.args[1], admin_user)

        mock_access.assert_awaited_once_with(db, recovery_id=recovery_uuid, current_user=admin_user)
        self.assertIs(mock_access.await_args.args[0], db)
        self.assertEqual(mock_access.await_args.kwargs["recovery_id"], recovery_uuid)
        self.assertIsInstance(mock_access.await_args.kwargs["recovery_id"], uuid.UUID)
        self.assertIs(mock_access.await_args.kwargs["current_user"], admin_user)

        mock_transition.assert_called_once_with(recovery_request)
        self.assertIs(mock_transition.call_args.args[0], recovery_request)

        mock_clear.assert_awaited_once_with(db, recovery_request.id)
        self.assertIs(mock_clear.await_args.args[0], db)
        self.assertEqual(mock_clear.await_args.args[1], recovery_request.id)
        self.assertIs(mock_clear.await_args.args[1], recovery_request.id)

        db.commit.assert_awaited_once()

        mock_sms.assert_called_once_with(requester_user.mobile_number, expected_sms_text)
        self.assertEqual(mock_sms.call_args.args[0], requester_user.mobile_number)
        self.assertEqual(mock_sms.call_args.args[1], expected_sms_text)

        mock_logger_warning.assert_not_called()

        mock_publish.assert_awaited_once_with(db, recovery_request, requester_user)
        self.assertIs(mock_publish.await_args.args[0], db)
        self.assertIs(mock_publish.await_args.args[1], recovery_request)
        self.assertIs(mock_publish.await_args.args[2], requester_user)

        mock_serialize.assert_not_called()

        expected_inline_expires_at = fixed_service_time + timedelta(seconds=30)
        expected_chat_expires_at = fixed_service_time + timedelta(seconds=7200)

        self.assertIs(recovery_request.status, SingleSessionRecoveryStatus.IDENTITY_VERIFICATION_REQUESTED)
        self.assertEqual(recovery_request.status.value, "identity_verification_requested")
        self.assertEqual(recovery_request.identity_requested_at, fixed_service_time)
        self.assertEqual(recovery_request.inline_action_expires_at, expected_inline_expires_at)
        self.assertEqual(recovery_request.chat_action_expires_at, expected_chat_expires_at)

        self.assertEqual(db.commit.await_count, 1)
        db.rollback.assert_not_called()
        db.execute.assert_not_called()
        db.add.assert_not_called()
        db.flush.assert_not_called()
        db.refresh.assert_not_called()
        db.delete.assert_not_called()
        self.assertEqual(len(db.execute_results), 0)


if __name__ == "__main__":
    unittest.main()
