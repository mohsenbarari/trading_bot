import unittest
import uuid
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from core.services.single_session_recovery_service import (
    CHAT_ACTION_WINDOW_SECONDS,
    INLINE_ACTION_WINDOW_SECONDS,
    InvalidSingleSessionRecoveryTransition,
    build_identity_requested_sms_text,
    build_identity_submitted_sms_text,
    build_identity_submitted_text,
    build_initial_recovery_request_text,
    build_recovery_action_map_for_admin_messages,
    build_recovery_approved_sms_text,
    build_recovery_expired_sms_text,
    build_recovery_message_action_payload,
    build_recovery_rejected_sms_text,
    approve_recovery_request,
    cancel_recovery_request,
    create_recovery_request,
    expire_recovery_request,
    get_recovery_admin_target,
    list_recovery_admin_targets,
    get_active_recovery_request_for_login_request,
    get_latest_recovery_request_for_login_request,
    get_recovery_requester_display_name,
    is_active_recovery_status,
    is_terminal_recovery_status,
    list_pending_admin_recovery_targets,
    list_recovery_admin_users,
    request_identity_verification,
    reject_recovery_request,
    should_show_inline_recovery_prompt,
    submit_identity_material,
)
from models.session import SingleSessionRecoveryStatus
from models.user import UserRole


def scalar_one_or_none_result(value):
    result = Mock()
    result.scalar_one_or_none.return_value = value
    return result


class FakeScalarResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return list(self.rows)

    def first(self):
        return self.rows[0] if self.rows else None


class FakeExecuteResult:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return FakeScalarResult(self.rows)

    def all(self):
        return list(self.rows)


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

        active_string = SimpleNamespace(id=uuid.uuid4(), status=SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW.value)
        latest_string = SimpleNamespace(id=uuid.uuid4(), status=SingleSessionRecoveryStatus.CANCELLED.value)
        db.execute = AsyncMock(
            side_effect=[
                scalar_one_or_none_result(active_string),
                scalar_one_or_none_result(latest_string),
            ]
        )

        self.assertIs(await get_active_recovery_request_for_login_request(db, request_id), active_string)
        self.assertIs(await get_latest_recovery_request_for_login_request(db, request_id), latest_string)

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

    def test_display_text_and_terminal_helpers_cover_fallbacks(self):
        self.assertEqual(
            get_recovery_requester_display_name(
                SimpleNamespace(account_name="project_owner", full_name="Telegram Owner")
            ),
            "project_owner",
        )
        self.assertEqual(get_recovery_requester_display_name(SimpleNamespace(full_name="  Owner Name  ")), "Owner Name")
        self.assertEqual(
            get_recovery_requester_display_name(SimpleNamespace(full_name="", account_name="acct-1")),
            "acct-1",
        )
        self.assertEqual(
            get_recovery_requester_display_name(SimpleNamespace(account_name="", mobile_number="0912")),
            "0912",
        )
        self.assertEqual(get_recovery_requester_display_name(SimpleNamespace(id=42)), "کاربر 42")
        self.assertEqual(get_recovery_requester_display_name(SimpleNamespace()), "کاربر")

        self.assertIn("درخواست", build_initial_recovery_request_text("کاربر تست"))
        self.assertIn("مدارک", build_identity_submitted_text("کاربر تست"))
        self.assertIn("کارت", build_identity_requested_sms_text())
        self.assertIn("در حال بررسی", build_identity_submitted_sms_text())
        self.assertIn("تایید شد", build_recovery_approved_sms_text(after_identity_review=True))
        self.assertIn("تایید شد", build_recovery_approved_sms_text(after_identity_review=False))
        self.assertIn("رد شد", build_recovery_rejected_sms_text(after_identity_review=True))
        self.assertIn("رد شد", build_recovery_rejected_sms_text(after_identity_review=False))
        self.assertIn("مهلت", build_recovery_expired_sms_text())
        self.assertTrue(is_terminal_recovery_status(SingleSessionRecoveryStatus.APPROVED))
        self.assertFalse(is_terminal_recovery_status(SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW))

    def test_inline_prompt_and_action_payload_variants(self):
        now = datetime(2026, 5, 16, 12, 0, 0)
        pending = SimpleNamespace(
            id=uuid.uuid4(),
            status=SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW,
            inline_action_expires_at=now + timedelta(seconds=10),
            chat_action_expires_at=now + timedelta(hours=1),
        )
        submitted = SimpleNamespace(
            id=uuid.uuid4(),
            status=SingleSessionRecoveryStatus.IDENTITY_SUBMITTED,
            inline_action_expires_at=now + timedelta(seconds=10),
            chat_action_expires_at=now + timedelta(hours=1),
        )
        expired = SimpleNamespace(
            status=SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW,
            inline_action_expires_at=now - timedelta(seconds=1),
        )
        admin_target = SimpleNamespace(current_action_message_id=77)
        requester = SimpleNamespace(id=9, full_name="Requester")

        self.assertTrue(should_show_inline_recovery_prompt(pending, admin_target, now=now))
        self.assertTrue(should_show_inline_recovery_prompt(submitted, admin_target, now=now))
        self.assertFalse(should_show_inline_recovery_prompt(submitted, SimpleNamespace(current_action_message_id=None), now=now))
        self.assertFalse(should_show_inline_recovery_prompt(expired, admin_target, now=now))
        self.assertFalse(
            should_show_inline_recovery_prompt(
                SimpleNamespace(
                    status=SingleSessionRecoveryStatus.APPROVED,
                    inline_action_expires_at=now + timedelta(seconds=10),
                ),
                admin_target,
                now=now,
            )
        )

        pending_payload = build_recovery_message_action_payload(
            recovery_request=pending,
            requester_user=requester,
            current_action_message_id=77,
        )
        self.assertEqual(pending_payload["prompt_type"], "initial_request")
        self.assertTrue(pending_payload["can_request_identity"])

        submitted_payload = build_recovery_message_action_payload(
            recovery_request=submitted,
            requester_user=requester,
            current_action_message_id=78,
        )
        self.assertEqual(submitted_payload["prompt_type"], "identity_submitted")
        self.assertFalse(submitted_payload["can_request_identity"])

    async def test_lookup_helpers_ignore_invalid_status_results(self):
        invalid = SimpleNamespace(id=uuid.uuid4(), status="unknown-status")
        db = SimpleNamespace(
            execute=AsyncMock(return_value=scalar_one_or_none_result(invalid))
        )

        self.assertIsNone(await get_active_recovery_request_for_login_request(db, uuid.uuid4()))
        self.assertIsNone(await get_latest_recovery_request_for_login_request(db, uuid.uuid4()))

    async def test_admin_user_and_pending_target_query_helpers_shape_results(self):
        super_admin = SimpleNamespace(id=10, role=UserRole.SUPER_ADMIN, is_deleted=False)
        middle_manager = SimpleNamespace(id=5, role=UserRole.MIDDLE_MANAGER, is_deleted=False)
        db = SimpleNamespace(
            execute=AsyncMock(return_value=FakeExecuteResult([middle_manager, super_admin]))
        )

        admins = await list_recovery_admin_users(db)
        self.assertEqual([admin.id for admin in admins], [10, 5])

        now = datetime(2026, 5, 16, 12, 0, 0)
        visible_request = SimpleNamespace(
            id=uuid.uuid4(),
            status=SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW,
            inline_action_expires_at=now + timedelta(seconds=5),
            chat_action_expires_at=now + timedelta(hours=1),
        )
        hidden_request = SimpleNamespace(
            id=uuid.uuid4(),
            status=SingleSessionRecoveryStatus.IDENTITY_SUBMITTED,
            inline_action_expires_at=now + timedelta(seconds=5),
            chat_action_expires_at=now + timedelta(hours=1),
        )
        visible_target = SimpleNamespace(current_action_message_id=None)
        hidden_target = SimpleNamespace(current_action_message_id=None)
        requester = SimpleNamespace(id=7, full_name="Requester")
        db.execute = AsyncMock(
            return_value=FakeExecuteResult([
                (visible_target, visible_request, requester),
                (hidden_target, hidden_request, requester),
            ])
        )

        rows = await list_pending_admin_recovery_targets(db, admin_user_id=10, now=now)
        self.assertEqual(rows, [(visible_target, visible_request, requester)])

        db.execute = AsyncMock(return_value=FakeExecuteResult([visible_target]))
        self.assertEqual(await list_recovery_admin_targets(db, visible_request.id), [visible_target])

        db.execute = AsyncMock(return_value=FakeExecuteResult([visible_target]))
        self.assertIs(
            await get_recovery_admin_target(db, recovery_id=visible_request.id, admin_user_id=10),
            visible_target,
        )

    async def test_action_map_helper_normalizes_message_ids_and_skips_missing_target_message(self):
        now = datetime(2026, 5, 16, 12, 0, 0)
        recovery = SimpleNamespace(
            id=uuid.uuid4(),
            status=SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW,
            chat_action_expires_at=now + timedelta(hours=1),
        )
        requester = SimpleNamespace(id=7, full_name="Requester")
        target_with_message = SimpleNamespace(current_action_message_id=101)
        target_without_message = SimpleNamespace(current_action_message_id=None)
        db = SimpleNamespace(
            execute=AsyncMock(
                return_value=FakeExecuteResult([
                    (target_with_message, recovery, requester),
                    (target_without_message, recovery, requester),
                ])
            )
        )

        self.assertEqual(await build_recovery_action_map_for_admin_messages(db, admin_user_id=10, message_ids=[0, -1]), {})
        action_map = await build_recovery_action_map_for_admin_messages(
            db,
            admin_user_id=10,
            message_ids=[101, 101, "102"],
            now=now,
        )
        self.assertEqual(list(action_map), [101])
        self.assertEqual(action_map[101]["prompt_type"], "initial_request")

    async def test_create_recovery_request_skips_optional_flush_when_unavailable(self):
        login_request = SimpleNamespace(
            id=uuid.uuid4(),
            user_id=12,
            requester_device_name="Galaxy S24",
            requester_ip="1.2.3.4",
        )
        db = SimpleNamespace(
            execute=AsyncMock(return_value=scalar_one_or_none_result(None)),
            add=Mock(),
        )

        recovery = await create_recovery_request(db, login_request)

        self.assertEqual(recovery.session_login_request_id, login_request.id)
        db.add.assert_called_once_with(recovery)
