import socket
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException

from api.routers.sessions import (
    ACCOUNTANT_SESSION_MANAGEMENT_DETAIL,
    approve_single_session_recovery,
    ensure_non_accountant_session_management,
)
from models.session import SingleSessionRecoveryStatus
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


class TestP100bCharacterizationApproveSingleSessionRecoveryFailures(unittest.IsolatedAsyncioTestCase):
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

    async def test_approve_single_session_recovery_fails_for_accountant_actor(self):
        fake_db = FakeDB()
        fake_request = SimpleNamespace()
        accountant_user = SimpleNamespace(id=15, is_accountant=True, role=UserRole.STANDARD)

        mock_is_user_accountant = AsyncMock(name="is_user_accountant")
        mock_ensure_admin = AsyncMock(name="_ensure_recovery_admin_access")
        mock_create_refresh_token = Mock(name="create_refresh_token")
        mock_approve_recovery = Mock(name="approve_recovery_request")
        mock_clear_action_messages = AsyncMock(name="_clear_recovery_admin_action_messages")
        mock_provision_session = AsyncMock(name="provision_session_for_login_request")
        mock_store_token = AsyncMock(name="_store_temporary_refresh_token")
        mock_send_sms = Mock(name="send_sms")
        mock_publish_prompt = AsyncMock(name="_publish_recovery_prompt_updates")
        mock_session_to_dict = Mock(name="session_to_dict")
        mock_recovery_to_dict = Mock(name="recovery_request_to_dict")

        with patch.object(uuid, "UUID", wraps=uuid.UUID) as uuid_spy, \
             patch("api.routers.sessions.is_user_accountant", mock_is_user_accountant), \
             patch("api.routers.sessions._ensure_recovery_admin_access", mock_ensure_admin), \
             patch("api.routers.sessions.create_refresh_token", mock_create_refresh_token), \
             patch("api.routers.sessions.approve_recovery_request", mock_approve_recovery), \
             patch("api.routers.sessions._clear_recovery_admin_action_messages", mock_clear_action_messages), \
             patch("api.routers.sessions.provision_session_for_login_request", mock_provision_session), \
             patch("api.routers.sessions._store_temporary_refresh_token", mock_store_token), \
             patch("api.routers.sessions.send_sms", mock_send_sms), \
             patch("api.routers.sessions._publish_recovery_prompt_updates", mock_publish_prompt), \
             patch("api.routers.sessions.session_to_dict", mock_session_to_dict), \
             patch("api.routers.sessions.recovery_request_to_dict", mock_recovery_to_dict):

            with self.assertRaises(HTTPException) as exc_info:
                await approve_single_session_recovery(
                    "not-a-valid-recovery-uuid",
                    request=fake_request,
                    db=fake_db,
                    current_user=accountant_user,
                )

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, ACCOUNTANT_SESSION_MANAGEMENT_DETAIL)
        self.assertEqual(
            exc_info.exception.detail,
            "حسابداران به مدیریت نشست و خروج از حساب کاربری دسترسی ندارند.",
        )

        uuid_spy.assert_not_called()
        mock_ensure_admin.assert_not_called()
        mock_is_user_accountant.assert_not_called()
        mock_create_refresh_token.assert_not_called()
        mock_approve_recovery.assert_not_called()
        mock_clear_action_messages.assert_not_called()
        mock_provision_session.assert_not_called()
        fake_db.commit.assert_not_called()
        mock_store_token.assert_not_called()
        mock_send_sms.assert_not_called()
        mock_publish_prompt.assert_not_called()
        mock_session_to_dict.assert_not_called()
        mock_recovery_to_dict.assert_not_called()
        fake_db.execute.assert_not_called()
        fake_db.add.assert_not_called()
        self.assertEqual(len(fake_db.added), 0)

    async def test_approve_single_session_recovery_fails_for_malformed_recovery_uuid(self):
        fake_db = FakeDB()
        fake_request = SimpleNamespace()
        non_accountant_user = SimpleNamespace(id=25, is_accountant=False, role=UserRole.SUPER_ADMIN)

        mock_is_user_accountant = AsyncMock(name="is_user_accountant")
        mock_ensure_admin = AsyncMock(name="_ensure_recovery_admin_access")
        mock_create_refresh_token = Mock(name="create_refresh_token")
        mock_approve_recovery = Mock(name="approve_recovery_request")
        mock_clear_action_messages = AsyncMock(name="_clear_recovery_admin_action_messages")
        mock_provision_session = AsyncMock(name="provision_session_for_login_request")
        mock_store_token = AsyncMock(name="_store_temporary_refresh_token")
        mock_send_sms = Mock(name="send_sms")
        mock_publish_prompt = AsyncMock(name="_publish_recovery_prompt_updates")
        mock_session_to_dict = Mock(name="session_to_dict")
        mock_recovery_to_dict = Mock(name="recovery_request_to_dict")

        with patch("api.routers.sessions.ensure_non_accountant_session_management", wraps=ensure_non_accountant_session_management) as guard_spy, \
             patch("api.routers.sessions.is_user_accountant", mock_is_user_accountant), \
             patch("api.routers.sessions._ensure_recovery_admin_access", mock_ensure_admin), \
             patch("api.routers.sessions.create_refresh_token", mock_create_refresh_token), \
             patch("api.routers.sessions.approve_recovery_request", mock_approve_recovery), \
             patch("api.routers.sessions._clear_recovery_admin_action_messages", mock_clear_action_messages), \
             patch("api.routers.sessions.provision_session_for_login_request", mock_provision_session), \
             patch("api.routers.sessions._store_temporary_refresh_token", mock_store_token), \
             patch("api.routers.sessions.send_sms", mock_send_sms), \
             patch("api.routers.sessions._publish_recovery_prompt_updates", mock_publish_prompt), \
             patch("api.routers.sessions.session_to_dict", mock_session_to_dict), \
             patch("api.routers.sessions.recovery_request_to_dict", mock_recovery_to_dict):

            with self.assertRaises(HTTPException) as exc_info:
                await approve_single_session_recovery(
                    "bad-recovery-uuid-string",
                    request=fake_request,
                    db=fake_db,
                    current_user=non_accountant_user,
                )

        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "شناسه بازیابی نامعتبر است")

        guard_spy.assert_awaited_once_with(fake_db, non_accountant_user)
        mock_ensure_admin.assert_not_called()
        mock_is_user_accountant.assert_not_called()
        mock_create_refresh_token.assert_not_called()
        mock_approve_recovery.assert_not_called()
        mock_clear_action_messages.assert_not_called()
        mock_provision_session.assert_not_called()
        fake_db.commit.assert_not_called()
        mock_store_token.assert_not_called()
        mock_send_sms.assert_not_called()
        mock_publish_prompt.assert_not_called()
        mock_session_to_dict.assert_not_called()
        mock_recovery_to_dict.assert_not_called()
        fake_db.execute.assert_not_called()
        fake_db.add.assert_not_called()
        self.assertEqual(len(fake_db.added), 0)

    async def test_approve_single_session_recovery_fails_for_disallowed_recovery_status(self):
        fake_db = FakeDB()
        fake_request = SimpleNamespace()
        admin_user = SimpleNamespace(id=35, is_accountant=False, role=UserRole.SUPER_ADMIN)
        parsed_recovery_uuid = uuid.UUID("3fa85f64-5717-4562-b3fc-2c963f66afa6")
        recovery_id_str = str(parsed_recovery_uuid)

        disallowed_recovery = SimpleNamespace(
            id=parsed_recovery_uuid,
            status=SingleSessionRecoveryStatus.APPROVED,
        )
        admin_target = SimpleNamespace(admin_user_id=35, recovery_request=disallowed_recovery)
        requester_user = SimpleNamespace(id=45, mobile_number="09120000000")

        mock_ensure_admin = AsyncMock(
            name="_ensure_recovery_admin_access",
            return_value=(admin_target, disallowed_recovery, requester_user),
        )
        mock_is_user_accountant = AsyncMock(name="is_user_accountant")
        mock_create_refresh_token = Mock(name="create_refresh_token")
        mock_approve_recovery = Mock(name="approve_recovery_request")
        mock_clear_action_messages = AsyncMock(name="_clear_recovery_admin_action_messages")
        mock_provision_session = AsyncMock(name="provision_session_for_login_request")
        mock_store_token = AsyncMock(name="_store_temporary_refresh_token")
        mock_send_sms = Mock(name="send_sms")
        mock_publish_prompt = AsyncMock(name="_publish_recovery_prompt_updates")
        mock_session_to_dict = Mock(name="session_to_dict")
        mock_recovery_to_dict = Mock(name="recovery_request_to_dict")

        with patch("api.routers.sessions.ensure_non_accountant_session_management", wraps=ensure_non_accountant_session_management) as guard_spy, \
             patch("api.routers.sessions._ensure_recovery_admin_access", mock_ensure_admin), \
             patch("api.routers.sessions.is_user_accountant", mock_is_user_accountant), \
             patch("api.routers.sessions.create_refresh_token", mock_create_refresh_token), \
             patch("api.routers.sessions.approve_recovery_request", mock_approve_recovery), \
             patch("api.routers.sessions._clear_recovery_admin_action_messages", mock_clear_action_messages), \
             patch("api.routers.sessions.provision_session_for_login_request", mock_provision_session), \
             patch("api.routers.sessions._store_temporary_refresh_token", mock_store_token), \
             patch("api.routers.sessions.send_sms", mock_send_sms), \
             patch("api.routers.sessions._publish_recovery_prompt_updates", mock_publish_prompt), \
             patch("api.routers.sessions.session_to_dict", mock_session_to_dict), \
             patch("api.routers.sessions.recovery_request_to_dict", mock_recovery_to_dict):

            with self.assertRaises(HTTPException) as exc_info:
                await approve_single_session_recovery(
                    recovery_id_str,
                    request=fake_request,
                    db=fake_db,
                    current_user=admin_user,
                )

        self.assertEqual(exc_info.exception.status_code, 400)
        self.assertEqual(exc_info.exception.detail, "در وضعیت فعلی امکان تایید این درخواست وجود ندارد")

        guard_spy.assert_awaited_once_with(fake_db, admin_user)
        mock_ensure_admin.assert_awaited_once_with(
            fake_db,
            recovery_id=parsed_recovery_uuid,
            current_user=admin_user,
        )

        self.assertIs(disallowed_recovery.status, SingleSessionRecoveryStatus.APPROVED)
        self.assertEqual(disallowed_recovery.status.value, "approved")

        mock_is_user_accountant.assert_not_called()
        mock_create_refresh_token.assert_not_called()
        mock_approve_recovery.assert_not_called()
        mock_clear_action_messages.assert_not_called()
        mock_provision_session.assert_not_called()
        fake_db.commit.assert_not_called()
        mock_store_token.assert_not_called()
        mock_send_sms.assert_not_called()
        mock_publish_prompt.assert_not_called()
        mock_session_to_dict.assert_not_called()
        mock_recovery_to_dict.assert_not_called()
        fake_db.execute.assert_not_called()
        fake_db.add.assert_not_called()
        self.assertEqual(len(fake_db.added), 0)

    async def test_approve_single_session_recovery_fails_when_session_login_request_lookup_returns_none(self):
        from models.session import SessionLoginRequest

        fake_db = FakeDB()
        mock_execute_result = Mock(name="execute_result")
        mock_execute_result.scalar_one_or_none.return_value = None
        fake_db.execute.return_value = mock_execute_result

        fake_request = SimpleNamespace()
        admin_user = SimpleNamespace(id=35, is_accountant=False, role=UserRole.SUPER_ADMIN)
        recovery_uuid = uuid.UUID("11111111-1111-4111-8111-111111111111")
        recovery_id_str = str(recovery_uuid)
        session_login_request_id = uuid.UUID("22222222-2222-4222-8222-222222222222")

        recovery_request = SimpleNamespace(
            id=recovery_uuid,
            user_id=45,
            status=SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW,
            session_login_request=None,
            session_login_request_id=session_login_request_id,
        )
        admin_target = SimpleNamespace(admin_user_id=35, recovery_request=recovery_request)
        requester_user = SimpleNamespace(id=45, mobile_number="09120000000")

        mock_ensure_admin = AsyncMock(
            name="_ensure_recovery_admin_access",
            return_value=(admin_target, recovery_request, requester_user),
        )
        mock_is_user_accountant = AsyncMock(name="is_user_accountant")
        mock_create_refresh_token = Mock(name="create_refresh_token")
        mock_approve_recovery = Mock(name="approve_recovery_request")
        mock_clear_action_messages = AsyncMock(name="_clear_recovery_admin_action_messages")
        mock_provision_session = AsyncMock(name="provision_session_for_login_request")
        mock_store_token = AsyncMock(name="_store_temporary_refresh_token")
        mock_send_sms = Mock(name="send_sms")
        mock_publish_prompt = AsyncMock(name="_publish_recovery_prompt_updates")
        mock_session_to_dict = Mock(name="session_to_dict")
        mock_recovery_to_dict = Mock(name="recovery_request_to_dict")

        with patch("api.routers.sessions.ensure_non_accountant_session_management", wraps=ensure_non_accountant_session_management) as guard_spy, \
             patch("api.routers.sessions._ensure_recovery_admin_access", mock_ensure_admin), \
             patch("api.routers.sessions.is_user_accountant", mock_is_user_accountant), \
             patch("api.routers.sessions.create_refresh_token", mock_create_refresh_token), \
             patch("api.routers.sessions.approve_recovery_request", mock_approve_recovery), \
             patch("api.routers.sessions._clear_recovery_admin_action_messages", mock_clear_action_messages), \
             patch("api.routers.sessions.provision_session_for_login_request", mock_provision_session), \
             patch("api.routers.sessions._store_temporary_refresh_token", mock_store_token), \
             patch("api.routers.sessions.send_sms", mock_send_sms), \
             patch("api.routers.sessions._publish_recovery_prompt_updates", mock_publish_prompt), \
             patch("api.routers.sessions.session_to_dict", mock_session_to_dict), \
             patch("api.routers.sessions.recovery_request_to_dict", mock_recovery_to_dict):

            with self.assertRaises(HTTPException) as exc_info:
                await approve_single_session_recovery(
                    recovery_id_str,
                    request=fake_request,
                    db=fake_db,
                    current_user=admin_user,
                )

        self.assertEqual(exc_info.exception.status_code, 404)
        self.assertEqual(exc_info.exception.detail, "درخواست ورود اولیه یافت نشد")

        guard_spy.assert_awaited_once_with(fake_db, admin_user)
        mock_ensure_admin.assert_awaited_once_with(
            fake_db,
            recovery_id=recovery_uuid,
            current_user=admin_user,
        )

        fake_db.execute.assert_awaited_once()
        executed_stmt = fake_db.execute.await_args.args[0]
        self.assertIs(executed_stmt.column_descriptions[0]["entity"], SessionLoginRequest)
        self.assertIsNotNone(executed_stmt.whereclause)
        self.assertEqual(executed_stmt.whereclause.left.table.name, "session_login_requests")
        self.assertEqual(executed_stmt.whereclause.left.name, "id")
        self.assertEqual(executed_stmt.whereclause.right.value, session_login_request_id)
        self.assertEqual(list(executed_stmt.compile().params.values()), [session_login_request_id])
        mock_execute_result.scalar_one_or_none.assert_called_once_with()

        self.assertIs(recovery_request.status, SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW)
        self.assertEqual(recovery_request.status.value, "pending_admin_review")
        self.assertIsNone(recovery_request.session_login_request)
        self.assertEqual(recovery_request.session_login_request_id, session_login_request_id)
        self.assertEqual(recovery_request.id, recovery_uuid)

        mock_is_user_accountant.assert_not_called()
        mock_create_refresh_token.assert_not_called()
        mock_approve_recovery.assert_not_called()
        mock_clear_action_messages.assert_not_called()
        mock_provision_session.assert_not_called()
        fake_db.commit.assert_not_called()
        fake_db.rollback.assert_not_called()
        mock_store_token.assert_not_called()
        mock_send_sms.assert_not_called()
        mock_publish_prompt.assert_not_called()
        mock_session_to_dict.assert_not_called()
        mock_recovery_to_dict.assert_not_called()
        fake_db.add.assert_not_called()
        self.assertEqual(len(fake_db.added), 0)


if __name__ == "__main__":
    unittest.main()
