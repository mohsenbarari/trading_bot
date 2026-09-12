import socket
import unittest
import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call, patch

from api.routers.sessions import approve_single_session_recovery
from core.services.single_session_recovery_service import (
    approve_recovery_request as real_approve_recovery_request,
)
from models.session import (
    LoginRequestStatus,
    Platform,
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


class TestP100bCharacterizationApproveSingleSessionRecoveryPendingSuccess(unittest.IsolatedAsyncioTestCase):
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

    async def test_approve_single_session_recovery_pending_admin_review_success(self):
        fixed_now = datetime(2026, 9, 12, 10, 0, 0)
        recovery_uuid = uuid.UUID("a1b2c3d4-e5f6-4a5b-8c9d-0e1f2a3b4c5d")
        recovery_id_str = str(recovery_uuid)
        session_login_request_id = uuid.UUID("12345678-1234-4234-8234-123456789abc")

        fake_db = FakeDB()
        fake_request = SimpleNamespace()
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

        fake_refresh_token = "mocked-refresh-token-xyz"
        fake_provisioned_session = SimpleNamespace(
            id=uuid.UUID("99999999-9999-4999-8999-999999999999"),
            user_id=101,
            device_name="Chrome on Linux",
            device_ip="192.168.1.50",
            platform=Platform.WEB,
            home_server="ir-teh-1",
            is_primary=True,
            is_active=True,
            created_at=fixed_now,
            last_active_at=fixed_now,
        )

        session_dict_sentinel = {
            "id": str(fake_provisioned_session.id),
            "device_name": fake_provisioned_session.device_name,
            "device_ip": fake_provisioned_session.device_ip,
            "platform": "web",
            "home_server": "ir-teh-1",
            "is_primary": True,
            "is_active": True,
            "created_at": fixed_now.isoformat(),
            "last_active_at": fixed_now.isoformat(),
        }

        recovery_dict_sentinel = {
            "id": str(recovery_uuid),
            "status": "approved",
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
        mock_create_refresh_token = Mock(name="create_refresh_token", return_value=fake_refresh_token)
        mock_approve_recovery = Mock(
            name="approve_recovery_request",
            wraps=real_approve_recovery_request,
        )
        mock_clear_action_messages = AsyncMock(name="_clear_recovery_admin_action_messages", return_value=None)
        mock_provision_session = AsyncMock(
            name="provision_session_for_login_request",
            return_value=fake_provisioned_session,
        )
        mock_store_token = AsyncMock(name="_store_temporary_refresh_token", return_value=None)
        mock_send_sms = Mock(name="send_sms", return_value=True)
        mock_publish_prompt = AsyncMock(name="_publish_recovery_prompt_updates", return_value=None)
        mock_session_to_dict = Mock(name="session_to_dict", return_value=session_dict_sentinel)
        mock_recovery_to_dict = Mock(name="recovery_request_to_dict", return_value=recovery_dict_sentinel)

        ledger = Mock(name="call_ledger")
        ledger.attach_mock(mock_ensure_non_accountant, "ensure_non_accountant_session_management")
        ledger.attach_mock(mock_ensure_admin, "_ensure_recovery_admin_access")
        ledger.attach_mock(mock_create_refresh_token, "create_refresh_token")
        ledger.attach_mock(mock_approve_recovery, "approve_recovery_request")
        ledger.attach_mock(mock_clear_action_messages, "_clear_recovery_admin_action_messages")
        ledger.attach_mock(mock_provision_session, "provision_session_for_login_request")
        ledger.attach_mock(fake_db.commit, "commit")
        ledger.attach_mock(mock_store_token, "_store_temporary_refresh_token")
        ledger.attach_mock(mock_send_sms, "send_sms")
        ledger.attach_mock(mock_publish_prompt, "_publish_recovery_prompt_updates")
        ledger.attach_mock(mock_session_to_dict, "session_to_dict")
        ledger.attach_mock(mock_recovery_to_dict, "recovery_request_to_dict")

        # Initial state checks before invoking the handler
        self.assertIs(recovery_request.status, SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW)
        self.assertEqual(recovery_request.status.value, "pending_admin_review")
        self.assertIsNotNone(recovery_request.session_login_request)
        self.assertIs(loaded_login_request.status, LoginRequestStatus.PENDING)
        self.assertIsNotNone(loaded_login_request.resolved_by_session_id)

        with patch("api.routers.sessions.ensure_non_accountant_session_management", mock_ensure_non_accountant), \
             patch("api.routers.sessions._ensure_recovery_admin_access", mock_ensure_admin), \
             patch("api.routers.sessions.create_refresh_token", mock_create_refresh_token), \
             patch("api.routers.sessions.approve_recovery_request", mock_approve_recovery), \
             patch("api.routers.sessions._clear_recovery_admin_action_messages", mock_clear_action_messages), \
             patch("api.routers.sessions.provision_session_for_login_request", mock_provision_session), \
             patch("api.routers.sessions._store_temporary_refresh_token", mock_store_token), \
             patch("api.routers.sessions.send_sms", mock_send_sms), \
             patch("api.routers.sessions._publish_recovery_prompt_updates", mock_publish_prompt), \
             patch("api.routers.sessions.session_to_dict", mock_session_to_dict), \
             patch("api.routers.sessions.recovery_request_to_dict", mock_recovery_to_dict), \
             patch("core.services.single_session_recovery_service.utc_now_naive", return_value=fixed_now), \
             patch("core.utils.utc_now_naive", return_value=fixed_now):

            result = await approve_single_session_recovery(
                recovery_id_str,
                request=fake_request,
                db=fake_db,
                current_user=admin_user,
            )

        # Requirement 5: assert exact returned mapping
        expected_result = {
            "detail": "درخواست بازیابی تایید شد",
            "session": session_dict_sentinel,
            "recovery": recovery_dict_sentinel,
        }
        self.assertEqual(result, expected_result)

        # Requirement 2: assert deterministic transition and state mutations
        self.assertIs(recovery_request.status, SingleSessionRecoveryStatus.APPROVED)
        self.assertEqual(recovery_request.decided_by_user_id, admin_user.id)
        self.assertEqual(recovery_request.decided_at, fixed_now)
        self.assertEqual(recovery_request.inline_action_expires_at, fixed_now)
        self.assertEqual(recovery_request.chat_action_expires_at, fixed_now)
        self.assertIs(loaded_login_request.status, LoginRequestStatus.APPROVED)
        self.assertIsNone(loaded_login_request.resolved_by_session_id)

        # Requirement 3 & 5: assert exact arguments for all boundary collaborators and SMS content
        expected_sms_text = "درخواست شما برای ورود از دستگاه جدید تایید شد. اطلاعات لاگین دستگاه قبلی شما منقضی شد و اکنون میتوانید وارد سامانه شوید."
        self.assertNotIn("مدارک احراز هویت", expected_sms_text)
        self.assertIn("ورود از دستگاه جدید", expected_sms_text)

        mock_ensure_non_accountant.assert_awaited_once_with(fake_db, admin_user)
        mock_ensure_admin.assert_awaited_once_with(
            fake_db,
            recovery_id=recovery_uuid,
            current_user=admin_user,
        )
        mock_create_refresh_token.assert_called_once_with(subject=recovery_request.user_id)
        mock_approve_recovery.assert_called_once_with(
            recovery_request,
            decided_by_user_id=admin_user.id,
        )
        mock_clear_action_messages.assert_awaited_once_with(fake_db, recovery_request.id)
        mock_provision_session.assert_awaited_once_with(
            fake_db,
            login_request=loaded_login_request,
            refresh_token=fake_refresh_token,
            platform=Platform.WEB,
            home_server=loaded_login_request.requester_home_server,
        )
        fake_db.commit.assert_awaited_once_with()
        mock_store_token.assert_awaited_once_with(
            f"single_session_recovery_token:{recovery_request.id}",
            fake_refresh_token,
        )
        mock_send_sms.assert_called_once_with(
            requester_user.mobile_number,
            expected_sms_text,
        )
        mock_publish_prompt.assert_awaited_once_with(
            fake_db,
            recovery_request,
            requester_user,
        )
        mock_session_to_dict.assert_called_once_with(fake_provisioned_session)
        mock_recovery_to_dict.assert_called_once_with(recovery_request)

        # Requirement 4: shared mock call ledger order and asserting db.execute, db.rollback, db.add are never called
        expected_calls = [
            call.ensure_non_accountant_session_management(fake_db, admin_user),
            call._ensure_recovery_admin_access(
                fake_db,
                recovery_id=recovery_uuid,
                current_user=admin_user,
            ),
            call.create_refresh_token(subject=recovery_request.user_id),
            call.approve_recovery_request(
                recovery_request,
                decided_by_user_id=admin_user.id,
            ),
            call._clear_recovery_admin_action_messages(fake_db, recovery_request.id),
            call.provision_session_for_login_request(
                fake_db,
                login_request=loaded_login_request,
                refresh_token=fake_refresh_token,
                platform=Platform.WEB,
                home_server=loaded_login_request.requester_home_server,
            ),
            call.commit(),
            call._store_temporary_refresh_token(
                f"single_session_recovery_token:{recovery_request.id}",
                fake_refresh_token,
            ),
            call.send_sms(
                requester_user.mobile_number,
                expected_sms_text,
            ),
            call._publish_recovery_prompt_updates(fake_db, recovery_request, requester_user),
            call.session_to_dict(fake_provisioned_session),
            call.recovery_request_to_dict(recovery_request),
        ]
        self.assertEqual(ledger.mock_calls, expected_calls)
        self.assertEqual(
            [c[0] for c in ledger.mock_calls],
            [
                "ensure_non_accountant_session_management",
                "_ensure_recovery_admin_access",
                "create_refresh_token",
                "approve_recovery_request",
                "_clear_recovery_admin_action_messages",
                "provision_session_for_login_request",
                "commit",
                "_store_temporary_refresh_token",
                "send_sms",
                "_publish_recovery_prompt_updates",
                "session_to_dict",
                "recovery_request_to_dict",
            ],
        )

        fake_db.execute.assert_not_called()
        fake_db.rollback.assert_not_called()
        fake_db.add.assert_not_called()
        self.assertEqual(len(fake_db.added), 0)


if __name__ == "__main__":
    unittest.main()
