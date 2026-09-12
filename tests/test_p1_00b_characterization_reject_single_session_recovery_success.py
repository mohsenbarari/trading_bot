import socket
import unittest
import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, Mock, call, patch

from api.routers.sessions import (
    recovery_request_to_dict as real_recovery_request_to_dict,
    reject_single_session_recovery,
)
from core.services.single_session_recovery_service import (
    build_recovery_rejected_sms_text as real_build_recovery_rejected_sms_text,
    reject_recovery_request as real_reject_recovery_request,
)
from models.session import (
    LoginRequestStatus,
    SingleSessionRecoveryStatus,
)
from models.user import UserRole


class FakeDB:
    def __init__(self):
        self.commit = AsyncMock(name="FakeDB.commit")
        self.rollback = AsyncMock(name="FakeDB.rollback")
        self.execute = AsyncMock(name="FakeDB.execute")
        self.add = Mock(name="FakeDB.add", side_effect=self._record_add)
        self.added = []

    def _record_add(self, obj):
        self.added.append(obj)


class TestP100bCharacterizationRejectSingleSessionRecoverySuccess(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()

        def _fail_network(*_args, **_kwargs):
            raise AssertionError("Hermetic test attempted socket/network connection")

        def _fail_connect(*args, **kwargs):
            if args:
                sock = args[0]
                if getattr(sock, "family", None) == getattr(socket, "AF_UNIX", object()):
                    return None
                addr = args[1] if len(args) > 1 else args[0]
                if isinstance(addr, (str, bytes)):
                    return None
            raise AssertionError("Hermetic test attempted socket/network connection")

        patchers = [
            patch("socket.socket.connect", side_effect=_fail_connect),
            patch("socket.socket.connect_ex", side_effect=_fail_connect),
            patch("socket.create_connection", side_effect=_fail_network),
            patch("socket.getaddrinfo", side_effect=_fail_network),
        ]
        for patcher in patchers:
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_reject_single_session_recovery_pending_admin_review_success(self):
        fixed_now = datetime(2026, 9, 12, 10, 0, 0)
        recovery_uuid = uuid.UUID("a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d")
        recovery_id_str = str(recovery_uuid)
        session_login_request_id = uuid.UUID("12345678-1234-4234-8234-123456789abc")

        fake_db = FakeDB()
        admin_user = SimpleNamespace(
            id=77,
            is_accountant=False,
            role=UserRole.SUPER_ADMIN,
        )

        loaded_login_request = SimpleNamespace(
            id=session_login_request_id,
            user_id=101,
            requester_device_name="Chrome on Linux",
            requester_ip="192.168.1.50",
            requester_home_server="ir-teh-1",
            status=LoginRequestStatus.PENDING,
            resolved_by_session_id=uuid.UUID("ffffffff-ffff-4fff-8fff-ffffffffffff"),
        )

        recovery_request = SimpleNamespace(
            id=recovery_uuid,
            user_id=101,
            session_login_request_id=session_login_request_id,
            session_login_request=loaded_login_request,
            requester_device_name="Chrome on Linux",
            requester_ip="192.168.1.50",
            status=SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW,
            created_at=datetime(2026, 9, 12, 9, 0, 0),
            inline_action_expires_at=datetime(2026, 9, 12, 11, 0, 0),
            chat_action_expires_at=datetime(2026, 9, 12, 12, 0, 0),
            identity_requested_at=None,
            identity_submitted_at=None,
            decided_at=None,
            decided_by_user_id=None,
            cancelled_at=None,
        )

        requester_user = SimpleNamespace(
            id=101,
            mobile_number="09123456789",
            account_name="requester_user",
        )

        admin_target = SimpleNamespace(
            id=uuid.UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
            recovery_request_id=recovery_uuid,
            admin_user_id=admin_user.id,
            current_action_message_id=555,
            recovery_request=recovery_request,
        )

        expected_recovery_dict = {
            "id": str(recovery_uuid),
            "status": "rejected",
            "requester_device_name": recovery_request.requester_device_name,
            "requester_ip": recovery_request.requester_ip,
            "created_at": recovery_request.created_at.isoformat(),
            "inline_action_expires_at": fixed_now.isoformat(),
            "chat_action_expires_at": fixed_now.isoformat(),
            "identity_requested_at": None,
            "identity_submitted_at": None,
            "decided_at": fixed_now.isoformat(),
            "cancelled_at": None,
        }

        mock_ensure_non_accountant = AsyncMock(name="ensure_non_accountant_session_management", return_value=None)
        mock_ensure_admin = AsyncMock(
            name="_ensure_recovery_admin_access",
            return_value=(admin_target, recovery_request, requester_user),
        )
        mock_reject_recovery = Mock(
            name="reject_recovery_request",
            wraps=real_reject_recovery_request,
        )
        mock_clear_action_messages = AsyncMock(name="_clear_recovery_admin_action_messages", return_value=None)
        mock_build_sms = Mock(
            name="build_recovery_rejected_sms_text",
            wraps=real_build_recovery_rejected_sms_text,
        )
        mock_send_sms = Mock(name="send_sms", return_value=True)
        mock_publish_prompt = AsyncMock(name="_publish_recovery_prompt_updates", return_value=None)
        mock_recovery_to_dict = Mock(
            name="recovery_request_to_dict",
            wraps=real_recovery_request_to_dict,
        )

        mock_inappropriate_approve = Mock(name="approve_recovery_request")
        mock_inappropriate_provision = AsyncMock(name="provision_session_for_login_request")
        mock_inappropriate_create_token = Mock(name="create_refresh_token")
        mock_inappropriate_store_token = AsyncMock(name="_store_temporary_refresh_token")
        mock_inappropriate_session_to_dict = Mock(name="session_to_dict")

        ledger = Mock(name="call_ledger")
        ledger.attach_mock(mock_ensure_non_accountant, "ensure_non_accountant_session_management")
        ledger.attach_mock(mock_ensure_admin, "_ensure_recovery_admin_access")
        ledger.attach_mock(mock_reject_recovery, "reject_recovery_request")
        ledger.attach_mock(mock_clear_action_messages, "_clear_recovery_admin_action_messages")
        ledger.attach_mock(fake_db.commit, "commit")
        ledger.attach_mock(mock_build_sms, "build_recovery_rejected_sms_text")
        ledger.attach_mock(mock_send_sms, "send_sms")
        ledger.attach_mock(mock_publish_prompt, "_publish_recovery_prompt_updates")
        ledger.attach_mock(mock_recovery_to_dict, "recovery_request_to_dict")

        # Initial state assertions
        self.assertIs(recovery_request.status, SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW)
        self.assertEqual(recovery_request.status.value, "pending_admin_review")
        self.assertIsNotNone(recovery_request.session_login_request)
        self.assertIs(recovery_request.session_login_request, loaded_login_request)
        self.assertIs(loaded_login_request.status, LoginRequestStatus.PENDING)
        self.assertIsNone(recovery_request.decided_at)
        self.assertIsNone(recovery_request.decided_by_user_id)
        self.assertIsNone(recovery_request.identity_requested_at)
        self.assertIsNone(recovery_request.identity_submitted_at)
        self.assertIsNone(recovery_request.cancelled_at)

        with patch("api.routers.sessions.ensure_non_accountant_session_management", mock_ensure_non_accountant), \
             patch("api.routers.sessions._ensure_recovery_admin_access", mock_ensure_admin), \
             patch("api.routers.sessions.reject_recovery_request", mock_reject_recovery), \
             patch("api.routers.sessions._clear_recovery_admin_action_messages", mock_clear_action_messages), \
             patch("api.routers.sessions.build_recovery_rejected_sms_text", mock_build_sms), \
             patch("api.routers.sessions.send_sms", mock_send_sms), \
             patch("api.routers.sessions._publish_recovery_prompt_updates", mock_publish_prompt), \
             patch("api.routers.sessions.recovery_request_to_dict", mock_recovery_to_dict), \
             patch("api.routers.sessions.approve_recovery_request", mock_inappropriate_approve), \
             patch("api.routers.sessions.provision_session_for_login_request", mock_inappropriate_provision), \
             patch("api.routers.sessions.create_refresh_token", mock_inappropriate_create_token), \
             patch("api.routers.sessions._store_temporary_refresh_token", mock_inappropriate_store_token), \
             patch("api.routers.sessions.session_to_dict", mock_inappropriate_session_to_dict), \
             patch("core.services.single_session_recovery_service.utc_now_naive", return_value=fixed_now), \
             patch("core.utils.utc_now_naive", return_value=fixed_now):

            result = await reject_single_session_recovery(
                recovery_id_str,
                db=fake_db,
                current_user=admin_user,
            )

        expected_result = {
            "detail": "درخواست بازیابی رد شد",
            "recovery": expected_recovery_dict,
        }
        self.assertEqual(result, expected_result)

        # Assert recovery request mutations
        self.assertIs(recovery_request.status, SingleSessionRecoveryStatus.REJECTED)
        self.assertEqual(recovery_request.status.value, "rejected")
        self.assertEqual(recovery_request.decided_by_user_id, admin_user.id)
        self.assertEqual(recovery_request.decided_at, fixed_now)
        self.assertEqual(recovery_request.inline_action_expires_at, fixed_now)
        self.assertEqual(recovery_request.chat_action_expires_at, fixed_now)
        self.assertIsNone(recovery_request.identity_requested_at)
        self.assertIsNone(recovery_request.identity_submitted_at)
        self.assertIsNone(recovery_request.cancelled_at)
        self.assertEqual(recovery_request.id, recovery_uuid)
        self.assertEqual(recovery_request.user_id, 101)
        self.assertEqual(recovery_request.session_login_request_id, session_login_request_id)
        self.assertEqual(recovery_request.requester_device_name, "Chrome on Linux")
        self.assertEqual(recovery_request.requester_ip, "192.168.1.50")

        # Assert login request mutations
        self.assertIs(loaded_login_request.status, LoginRequestStatus.REJECTED)
        self.assertEqual(loaded_login_request.status.value, "rejected")
        self.assertEqual(loaded_login_request.requester_device_name, "Chrome on Linux")
        self.assertEqual(loaded_login_request.requester_ip, "192.168.1.50")
        self.assertEqual(loaded_login_request.requester_home_server, "ir-teh-1")

        # SMS assertions: normal non-identity rejection SMS
        expected_sms_text = "درخواست شما برای ورود از دستگاه جدید توسط تیم مدیریت رد شد. در صورت نیاز میتوانید دوباره درخواست خود را ثبت کنید."
        self.assertNotIn("مدارک احراز هویت", expected_sms_text)
        self.assertIn("ورود از دستگاه جدید", expected_sms_text)

        # Collaborator call checks
        mock_ensure_non_accountant.assert_awaited_once_with(fake_db, admin_user)
        mock_ensure_admin.assert_awaited_once_with(
            fake_db,
            recovery_id=recovery_uuid,
            current_user=admin_user,
        )
        mock_reject_recovery.assert_called_once_with(
            recovery_request,
            decided_by_user_id=admin_user.id,
        )
        mock_clear_action_messages.assert_awaited_once_with(fake_db, recovery_request.id)
        fake_db.commit.assert_awaited_once_with()
        mock_build_sms.assert_called_once_with(after_identity_review=False)
        mock_send_sms.assert_called_once_with(
            requester_user.mobile_number,
            expected_sms_text,
        )
        mock_publish_prompt.assert_awaited_once_with(
            fake_db,
            recovery_request,
            requester_user,
        )
        mock_recovery_to_dict.assert_called_once_with(recovery_request)

        # Total call order via mock ledger
        expected_calls = [
            call.ensure_non_accountant_session_management(fake_db, admin_user),
            call._ensure_recovery_admin_access(
                fake_db,
                recovery_id=recovery_uuid,
                current_user=admin_user,
            ),
            call.reject_recovery_request(
                recovery_request,
                decided_by_user_id=admin_user.id,
            ),
            call._clear_recovery_admin_action_messages(fake_db, recovery_request.id),
            call.commit(),
            call.build_recovery_rejected_sms_text(after_identity_review=False),
            call.send_sms(
                requester_user.mobile_number,
                expected_sms_text,
            ),
            call._publish_recovery_prompt_updates(fake_db, recovery_request, requester_user),
            call.recovery_request_to_dict(recovery_request),
        ]
        self.assertEqual(ledger.mock_calls, expected_calls)
        self.assertEqual(
            [c[0] for c in ledger.mock_calls],
            [
                "ensure_non_accountant_session_management",
                "_ensure_recovery_admin_access",
                "reject_recovery_request",
                "_clear_recovery_admin_action_messages",
                "commit",
                "build_recovery_rejected_sms_text",
                "send_sms",
                "_publish_recovery_prompt_updates",
                "recovery_request_to_dict",
            ],
        )

        # Assert no DB lookup, add, or rollback
        fake_db.execute.assert_not_called()
        fake_db.rollback.assert_not_called()
        fake_db.add.assert_not_called()
        self.assertEqual(len(fake_db.added), 0)

        # Assert inappropriate helpers were not called
        mock_inappropriate_approve.assert_not_called()
        mock_inappropriate_provision.assert_not_called()
        mock_inappropriate_create_token.assert_not_called()
        mock_inappropriate_store_token.assert_not_called()
        mock_inappropriate_session_to_dict.assert_not_called()

    async def test_reject_single_session_recovery_identity_submitted_success(self):
        fixed_now = datetime(2026, 9, 12, 10, 0, 0)
        recovery_uuid = uuid.UUID("b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e")
        recovery_id_str = str(recovery_uuid)
        session_login_request_id = uuid.UUID("87654321-4321-4234-8234-987654321cba")

        fake_db = FakeDB()
        admin_user = SimpleNamespace(
            id=77,
            is_accountant=False,
            role=UserRole.SUPER_ADMIN,
        )

        queried_login_request = SimpleNamespace(
            id=session_login_request_id,
            user_id=101,
            requester_device_name="Firefox on Android",
            requester_ip="10.0.0.12",
            requester_home_server="ir-thr-2",
            status=LoginRequestStatus.PENDING,
            resolved_by_session_id=None,
        )

        identity_requested_ts = datetime(2026, 9, 12, 8, 45, 0)
        identity_submitted_ts = datetime(2026, 9, 12, 9, 15, 0)

        recovery_request = SimpleNamespace(
            id=recovery_uuid,
            user_id=101,
            session_login_request_id=session_login_request_id,
            session_login_request=None,
            requester_device_name="Firefox on Android",
            requester_ip="10.0.0.12",
            status=SingleSessionRecoveryStatus.IDENTITY_SUBMITTED,
            created_at=datetime(2026, 9, 12, 8, 30, 0),
            inline_action_expires_at=datetime(2026, 9, 12, 10, 30, 0),
            chat_action_expires_at=datetime(2026, 9, 12, 11, 30, 0),
            identity_requested_at=identity_requested_ts,
            identity_submitted_at=identity_submitted_ts,
            decided_at=None,
            decided_by_user_id=None,
            cancelled_at=None,
        )

        requester_user = SimpleNamespace(
            id=101,
            mobile_number="09129876543",
            account_name="requester_user",
        )

        admin_target = SimpleNamespace(
            id=uuid.UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
            recovery_request_id=recovery_uuid,
            admin_user_id=admin_user.id,
            current_action_message_id=666,
            recovery_request=recovery_request,
        )

        expected_recovery_dict = {
            "id": str(recovery_uuid),
            "status": "rejected",
            "requester_device_name": recovery_request.requester_device_name,
            "requester_ip": recovery_request.requester_ip,
            "created_at": recovery_request.created_at.isoformat(),
            "inline_action_expires_at": fixed_now.isoformat(),
            "chat_action_expires_at": fixed_now.isoformat(),
            "identity_requested_at": identity_requested_ts.isoformat(),
            "identity_submitted_at": identity_submitted_ts.isoformat(),
            "decided_at": fixed_now.isoformat(),
            "cancelled_at": None,
        }

        mock_execute_result = Mock(name="db_execute_result")
        mock_execute_result.scalar_one_or_none = Mock(
            name="scalar_one_or_none",
            return_value=queried_login_request,
        )
        fake_db.execute = AsyncMock(name="FakeDB.execute", return_value=mock_execute_result)

        mock_ensure_non_accountant = AsyncMock(name="ensure_non_accountant_session_management", return_value=None)
        mock_ensure_admin = AsyncMock(
            name="_ensure_recovery_admin_access",
            return_value=(admin_target, recovery_request, requester_user),
        )
        mock_reject_recovery = Mock(
            name="reject_recovery_request",
            wraps=real_reject_recovery_request,
        )
        mock_clear_action_messages = AsyncMock(name="_clear_recovery_admin_action_messages", return_value=None)
        mock_build_sms = Mock(
            name="build_recovery_rejected_sms_text",
            wraps=real_build_recovery_rejected_sms_text,
        )
        mock_send_sms = Mock(name="send_sms", return_value=True)
        mock_publish_prompt = AsyncMock(name="_publish_recovery_prompt_updates", return_value=None)
        mock_recovery_to_dict = Mock(
            name="recovery_request_to_dict",
            wraps=real_recovery_request_to_dict,
        )

        mock_inappropriate_approve = Mock(name="approve_recovery_request")
        mock_inappropriate_provision = AsyncMock(name="provision_session_for_login_request")
        mock_inappropriate_create_token = Mock(name="create_refresh_token")
        mock_inappropriate_store_token = AsyncMock(name="_store_temporary_refresh_token")
        mock_inappropriate_session_to_dict = Mock(name="session_to_dict")

        ledger = Mock(name="call_ledger")
        ledger.attach_mock(mock_ensure_non_accountant, "ensure_non_accountant_session_management")
        ledger.attach_mock(mock_ensure_admin, "_ensure_recovery_admin_access")
        ledger.attach_mock(mock_reject_recovery, "reject_recovery_request")
        ledger.attach_mock(fake_db.execute, "execute")
        ledger.attach_mock(mock_clear_action_messages, "_clear_recovery_admin_action_messages")
        ledger.attach_mock(fake_db.commit, "commit")
        ledger.attach_mock(mock_build_sms, "build_recovery_rejected_sms_text")
        ledger.attach_mock(mock_send_sms, "send_sms")
        ledger.attach_mock(mock_publish_prompt, "_publish_recovery_prompt_updates")
        ledger.attach_mock(mock_recovery_to_dict, "recovery_request_to_dict")

        # Initial state assertions
        self.assertIs(recovery_request.status, SingleSessionRecoveryStatus.IDENTITY_SUBMITTED)
        self.assertEqual(recovery_request.status.value, "identity_submitted")
        self.assertIsNone(recovery_request.session_login_request)
        self.assertIs(queried_login_request.status, LoginRequestStatus.PENDING)
        self.assertIsNone(recovery_request.decided_at)
        self.assertIsNone(recovery_request.decided_by_user_id)
        self.assertEqual(recovery_request.identity_requested_at, identity_requested_ts)
        self.assertEqual(recovery_request.identity_submitted_at, identity_submitted_ts)
        self.assertIsNone(recovery_request.cancelled_at)

        with patch("api.routers.sessions.ensure_non_accountant_session_management", mock_ensure_non_accountant), \
             patch("api.routers.sessions._ensure_recovery_admin_access", mock_ensure_admin), \
             patch("api.routers.sessions.reject_recovery_request", mock_reject_recovery), \
             patch("api.routers.sessions._clear_recovery_admin_action_messages", mock_clear_action_messages), \
             patch("api.routers.sessions.build_recovery_rejected_sms_text", mock_build_sms), \
             patch("api.routers.sessions.send_sms", mock_send_sms), \
             patch("api.routers.sessions._publish_recovery_prompt_updates", mock_publish_prompt), \
             patch("api.routers.sessions.recovery_request_to_dict", mock_recovery_to_dict), \
             patch("api.routers.sessions.approve_recovery_request", mock_inappropriate_approve), \
             patch("api.routers.sessions.provision_session_for_login_request", mock_inappropriate_provision), \
             patch("api.routers.sessions.create_refresh_token", mock_inappropriate_create_token), \
             patch("api.routers.sessions._store_temporary_refresh_token", mock_inappropriate_store_token), \
             patch("api.routers.sessions.session_to_dict", mock_inappropriate_session_to_dict), \
             patch("core.services.single_session_recovery_service.utc_now_naive", return_value=fixed_now), \
             patch("core.utils.utc_now_naive", return_value=fixed_now):

            result = await reject_single_session_recovery(
                recovery_id_str,
                db=fake_db,
                current_user=admin_user,
            )

        expected_result = {
            "detail": "درخواست بازیابی رد شد",
            "recovery": expected_recovery_dict,
        }
        self.assertEqual(result, expected_result)

        # Assert recovery request mutations
        self.assertIs(recovery_request.status, SingleSessionRecoveryStatus.REJECTED)
        self.assertEqual(recovery_request.status.value, "rejected")
        self.assertEqual(recovery_request.decided_by_user_id, admin_user.id)
        self.assertEqual(recovery_request.decided_at, fixed_now)
        self.assertEqual(recovery_request.inline_action_expires_at, fixed_now)
        self.assertEqual(recovery_request.chat_action_expires_at, fixed_now)
        self.assertEqual(recovery_request.identity_requested_at, identity_requested_ts)
        self.assertEqual(recovery_request.identity_submitted_at, identity_submitted_ts)
        self.assertIsNone(recovery_request.cancelled_at)
        self.assertEqual(recovery_request.id, recovery_uuid)
        self.assertEqual(recovery_request.user_id, 101)
        self.assertEqual(recovery_request.session_login_request_id, session_login_request_id)
        self.assertEqual(recovery_request.requester_device_name, "Firefox on Android")
        self.assertEqual(recovery_request.requester_ip, "10.0.0.12")
        self.assertIsNone(recovery_request.session_login_request)

        # Assert queried login request mutations
        self.assertIs(queried_login_request.status, LoginRequestStatus.REJECTED)
        self.assertEqual(queried_login_request.status.value, "rejected")
        self.assertEqual(queried_login_request.requester_device_name, "Firefox on Android")
        self.assertEqual(queried_login_request.requester_ip, "10.0.0.12")
        self.assertEqual(queried_login_request.requester_home_server, "ir-thr-2")

        # Database lookup assertions: lookup occurs exactly once
        fake_db.execute.assert_awaited_once()
        executed_stmt = fake_db.execute.await_args.args[0]
        self.assertIn("session_login_requests", str(executed_stmt))
        mock_execute_result.scalar_one_or_none.assert_called_once_with()

        # SMS assertions: identity-review rejection SMS variant
        expected_sms_text = "درخواست شما پس از بررسی مدارک احراز هویت توسط تیم مدیریت رد شد. در صورت نیاز میتوانید دوباره درخواست خود را ثبت کنید."
        self.assertIn("مدارک احراز هویت", expected_sms_text)
        self.assertNotIn("ورود از دستگاه جدید", expected_sms_text)

        # Collaborator call checks
        mock_ensure_non_accountant.assert_awaited_once_with(fake_db, admin_user)
        mock_ensure_admin.assert_awaited_once_with(
            fake_db,
            recovery_id=recovery_uuid,
            current_user=admin_user,
        )
        mock_reject_recovery.assert_called_once_with(
            recovery_request,
            decided_by_user_id=admin_user.id,
        )
        mock_clear_action_messages.assert_awaited_once_with(fake_db, recovery_request.id)
        fake_db.commit.assert_awaited_once_with()
        mock_build_sms.assert_called_once_with(after_identity_review=True)
        mock_send_sms.assert_called_once_with(
            requester_user.mobile_number,
            expected_sms_text,
        )
        mock_publish_prompt.assert_awaited_once_with(
            fake_db,
            recovery_request,
            requester_user,
        )
        mock_recovery_to_dict.assert_called_once_with(recovery_request)

        # Total call order via mock ledger
        expected_calls = [
            call.ensure_non_accountant_session_management(fake_db, admin_user),
            call._ensure_recovery_admin_access(
                fake_db,
                recovery_id=recovery_uuid,
                current_user=admin_user,
            ),
            call.reject_recovery_request(
                recovery_request,
                decided_by_user_id=admin_user.id,
            ),
            call.execute(ANY),
            call._clear_recovery_admin_action_messages(fake_db, recovery_request.id),
            call.commit(),
            call.build_recovery_rejected_sms_text(after_identity_review=True),
            call.send_sms(
                requester_user.mobile_number,
                expected_sms_text,
            ),
            call._publish_recovery_prompt_updates(fake_db, recovery_request, requester_user),
            call.recovery_request_to_dict(recovery_request),
        ]
        self.assertEqual(ledger.mock_calls, expected_calls)
        self.assertEqual(
            [c[0] for c in ledger.mock_calls],
            [
                "ensure_non_accountant_session_management",
                "_ensure_recovery_admin_access",
                "reject_recovery_request",
                "execute",
                "_clear_recovery_admin_action_messages",
                "commit",
                "build_recovery_rejected_sms_text",
                "send_sms",
                "_publish_recovery_prompt_updates",
                "recovery_request_to_dict",
            ],
        )

        # Assert no add or rollback
        fake_db.rollback.assert_not_called()
        fake_db.add.assert_not_called()
        self.assertEqual(len(fake_db.added), 0)

        # Assert inappropriate helpers were not called
        mock_inappropriate_approve.assert_not_called()
        mock_inappropriate_provision.assert_not_called()
        mock_inappropriate_create_token.assert_not_called()
        mock_inappropriate_store_token.assert_not_called()
        mock_inappropriate_session_to_dict.assert_not_called()


if __name__ == "__main__":
    unittest.main()
