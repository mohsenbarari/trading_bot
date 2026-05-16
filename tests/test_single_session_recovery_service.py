import unittest
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from core.services.single_session_recovery_service import (
    CHAT_ACTION_WINDOW_SECONDS,
    INLINE_ACTION_WINDOW_SECONDS,
    InvalidSingleSessionRecoveryTransition,
    approve_recovery_request,
    cancel_recovery_request,
    create_recovery_request,
    expire_recovery_request,
    get_active_recovery_request_for_login_request,
    get_latest_recovery_request_for_login_request,
    is_active_recovery_status,
    request_identity_verification,
    reject_recovery_request,
    submit_identity_material,
)
from models.session import SingleSessionRecoveryStatus


def scalar_one_or_none_result(value):
    result = Mock()
    result.scalar_one_or_none.return_value = value
    return result


class SingleSessionRecoveryServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_recovery_request_sets_snapshot_and_time_windows(self):
        login_request = SimpleNamespace(
            id=uuid.uuid4(),
            user_id=12,
            requester_device_name="Galaxy S24",
            requester_ip="1.2.3.4",
        )
        db = SimpleNamespace(
            execute=AsyncMock(return_value=scalar_one_or_none_result(None)),
            add=Mock(),
            flush=AsyncMock(),
        )
        now = datetime(2026, 5, 16, 12, 0, 0)

        recovery = await create_recovery_request(db, login_request, now=now)

        self.assertEqual(recovery.user_id, 12)
        self.assertEqual(recovery.session_login_request_id, login_request.id)
        self.assertEqual(recovery.requester_device_name, "Galaxy S24")
        self.assertEqual(recovery.requester_ip, "1.2.3.4")
        self.assertEqual(recovery.status, SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW)
        self.assertEqual(
            recovery.inline_action_expires_at,
            now + timedelta(seconds=INLINE_ACTION_WINDOW_SECONDS),
        )
        self.assertEqual(
            recovery.chat_action_expires_at,
            now + timedelta(seconds=CHAT_ACTION_WINDOW_SECONDS),
        )
        db.add.assert_called_once_with(recovery)
        db.flush.assert_awaited_once()

    async def test_create_recovery_request_reuses_existing_active_request(self):
        existing = SimpleNamespace(
            id=uuid.uuid4(),
            status=SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW,
        )
        login_request = SimpleNamespace(id=uuid.uuid4())
        db = SimpleNamespace(
            execute=AsyncMock(return_value=scalar_one_or_none_result(existing)),
            add=Mock(),
        )

        result = await create_recovery_request(db, login_request)

        self.assertIs(result, existing)
        db.add.assert_not_called()

    async def test_lookup_helpers_return_execute_result(self):
        request_id = uuid.uuid4()
        active = SimpleNamespace(id=uuid.uuid4(), status=SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW)
        latest = SimpleNamespace(id=uuid.uuid4(), status=SingleSessionRecoveryStatus.CANCELLED)

        db = SimpleNamespace(
            execute=AsyncMock(
                side_effect=[
                    scalar_one_or_none_result(active),
                    scalar_one_or_none_result(latest),
                ]
            )
        )

        self.assertIs(await get_active_recovery_request_for_login_request(db, request_id), active)
        self.assertIs(await get_latest_recovery_request_for_login_request(db, request_id), latest)

    def test_identity_request_and_submission_refresh_windows(self):
        now = datetime(2026, 5, 16, 12, 0, 0)
        recovery = SimpleNamespace(
            status=SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW,
            inline_action_expires_at=None,
            chat_action_expires_at=None,
            identity_requested_at=None,
            identity_submitted_at=None,
        )

        request_identity_verification(recovery, now=now)
        self.assertEqual(recovery.status, SingleSessionRecoveryStatus.IDENTITY_VERIFICATION_REQUESTED)
        self.assertEqual(recovery.identity_requested_at, now)

        submitted_at = now + timedelta(minutes=5)
        submit_identity_material(recovery, now=submitted_at)
        self.assertEqual(recovery.status, SingleSessionRecoveryStatus.IDENTITY_SUBMITTED)
        self.assertEqual(recovery.identity_submitted_at, submitted_at)
        self.assertEqual(
            recovery.inline_action_expires_at,
            submitted_at + timedelta(seconds=INLINE_ACTION_WINDOW_SECONDS),
        )

    def test_approve_reject_cancel_and_expire_close_actions(self):
        base = datetime(2026, 5, 16, 12, 0, 0)
        recovery = SimpleNamespace(
            status=SingleSessionRecoveryStatus.IDENTITY_SUBMITTED,
            inline_action_expires_at=base + timedelta(seconds=30),
            chat_action_expires_at=base + timedelta(hours=2),
            decided_at=None,
            decided_by_user_id=None,
            cancelled_at=None,
        )

        approve_recovery_request(recovery, decided_by_user_id=44, now=base)
        self.assertEqual(recovery.status, SingleSessionRecoveryStatus.APPROVED)
        self.assertEqual(recovery.decided_by_user_id, 44)
        self.assertEqual(recovery.decided_at, base)
        self.assertEqual(recovery.inline_action_expires_at, base)
        self.assertEqual(recovery.chat_action_expires_at, base)

        rejected = SimpleNamespace(
            status=SingleSessionRecoveryStatus.IDENTITY_VERIFICATION_REQUESTED,
            inline_action_expires_at=base + timedelta(seconds=30),
            chat_action_expires_at=base + timedelta(hours=2),
            decided_at=None,
            decided_by_user_id=None,
        )
        reject_recovery_request(rejected, decided_by_user_id=55, now=base)
        self.assertEqual(rejected.status, SingleSessionRecoveryStatus.REJECTED)

        cancelled = SimpleNamespace(
            status=SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW,
            inline_action_expires_at=base + timedelta(seconds=30),
            chat_action_expires_at=base + timedelta(hours=2),
            cancelled_at=None,
        )
        cancel_recovery_request(cancelled, now=base)
        self.assertEqual(cancelled.status, SingleSessionRecoveryStatus.CANCELLED)
        self.assertEqual(cancelled.cancelled_at, base)

        expired = SimpleNamespace(
            status=SingleSessionRecoveryStatus.IDENTITY_SUBMITTED,
            inline_action_expires_at=base + timedelta(seconds=30),
            chat_action_expires_at=base + timedelta(hours=2),
        )
        expire_recovery_request(expired, now=base)
        self.assertEqual(expired.status, SingleSessionRecoveryStatus.EXPIRED)

    def test_invalid_transitions_raise(self):
        recovery = SimpleNamespace(status=SingleSessionRecoveryStatus.CANCELLED)

        with self.assertRaises(InvalidSingleSessionRecoveryTransition):
            request_identity_verification(recovery)
        with self.assertRaises(InvalidSingleSessionRecoveryTransition):
            submit_identity_material(recovery)
        with self.assertRaises(InvalidSingleSessionRecoveryTransition):
            approve_recovery_request(recovery, decided_by_user_id=1)

    def test_active_status_helper(self):
        self.assertTrue(is_active_recovery_status(SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW))
        self.assertTrue(is_active_recovery_status(SingleSessionRecoveryStatus.IDENTITY_SUBMITTED))
        self.assertFalse(is_active_recovery_status(SingleSessionRecoveryStatus.APPROVED))