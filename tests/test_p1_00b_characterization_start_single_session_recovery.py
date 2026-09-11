"""Characterization tests for start_single_session_recovery handler.

P1-00b Stage card: docs/refactor/two-server/section-1/stages/P1-00b.md
Shared Assignment: /root/trading-bot/trading_bot/.local/two-site-refactor/assignments/WA-P1-00B-FREEZE-20260904-001.md
Tracked gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Exact surface seed: API-AUTH-POST-SESSIONS-START-SINGLE-SESSION-RECOVERY-L673

Disclaimer: This characterization module does not promote the seed
API-AUTH-POST-SESSIONS-START-SINGLE-SESSION-RECOVERY-L673 and does not close Stage P1-00b.
"""

from __future__ import annotations

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import unittest
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

from api.routers.sessions import start_single_session_recovery
from models.session import (
    LoginRequestStatus,
    SessionLoginRequest,
    SingleSessionRecoveryRequest,
    SingleSessionRecoveryStatus,
)
from models.user import User, UserRole


class _FakeExecuteResult:
    def __init__(self, value=None):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeCharacterizationDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])
        self.executed_statements = []
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self.add = Mock()
        self.flush = AsyncMock()
        self.refresh = AsyncMock()
        self.delete = Mock()

    async def execute(self, stmt):
        self.executed_statements.append(stmt)
        if not self.execute_results:
            raise AssertionError("Unexpected db.execute() call: result queue exhausted")
        return _FakeExecuteResult(self.execute_results.pop(0))


class P100bCharacterizationStartSingleSessionRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_latest_recovery_is_reused_without_side_effects(self):
        request_id = uuid.UUID("a1111111-1111-1111-1111-111111111111")
        user_id = 101
        now = datetime(2026, 9, 11, 10, 0, 0)

        login_request = SessionLoginRequest(
            id=request_id,
            user_id=user_id,
            requester_device_name="Chrome on macOS",
            requester_ip="192.168.1.50",
            requester_home_server="foreign",
            status=LoginRequestStatus.PENDING,
            expires_at=now + timedelta(minutes=15),
        )

        active_recovery_id = uuid.UUID("b1111111-1111-1111-1111-111111111111")
        active_recovery = SingleSessionRecoveryRequest(
            id=active_recovery_id,
            user_id=user_id,
            session_login_request_id=request_id,
            requester_device_name="Chrome on macOS",
            requester_ip="192.168.1.50",
            status=SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW,
            created_at=now,
            inline_action_expires_at=now + timedelta(seconds=30),
            chat_action_expires_at=now + timedelta(hours=2),
            identity_requested_at=None,
            identity_submitted_at=None,
            decided_at=None,
            cancelled_at=None,
        )

        db = FakeCharacterizationDB(execute_results=[login_request])

        class DeterministicDateTime(datetime):
            @classmethod
            def utcnow(cls):
                return now

        with patch(
            "api.routers.sessions.datetime", DeterministicDateTime
        ), patch(
            "api.routers.sessions.get_latest_recovery_request_for_login_request",
            new_callable=AsyncMock,
        ) as mock_get_latest, patch(
            "api.routers.sessions.list_recovery_admin_users",
            new_callable=AsyncMock,
        ) as mock_list_admins, patch(
            "api.routers.sessions.create_recovery_request",
            new_callable=AsyncMock,
        ) as mock_create_recovery, patch(
            "api.routers.sessions._deliver_initial_recovery_messages",
            new_callable=AsyncMock,
        ) as mock_deliver:
            mock_get_latest.return_value = active_recovery

            result = await start_single_session_recovery(str(request_id), db=db)

            expected_payload = {
                "id": "b1111111-1111-1111-1111-111111111111",
                "status": "pending_admin_review",
                "requester_device_name": "Chrome on macOS",
                "requester_ip": "192.168.1.50",
                "created_at": "2026-09-11T10:00:00",
                "inline_action_expires_at": "2026-09-11T10:00:30",
                "chat_action_expires_at": "2026-09-11T12:00:00",
                "identity_requested_at": None,
                "identity_submitted_at": None,
                "decided_at": None,
                "cancelled_at": None,
            }
            self.assertEqual(result, expected_payload)

            self.assertEqual(len(db.executed_statements), 1)
            stmt = db.executed_statements[0]
            self.assertIn(SessionLoginRequest, [cd["expr"] for cd in stmt.column_descriptions])
            self.assertEqual(stmt.whereclause.left.key, "id")
            self.assertEqual(stmt.whereclause.right.value, request_id)

            mock_get_latest.assert_awaited_once_with(db, request_id)
            mock_list_admins.assert_not_called()
            mock_create_recovery.assert_not_called()
            mock_deliver.assert_not_called()

            db.commit.assert_not_called()
            db.rollback.assert_not_called()
            db.add.assert_not_called()
            db.flush.assert_not_called()
            db.refresh.assert_not_called()
            db.delete.assert_not_called()
            self.assertEqual(len(db.execute_results), 0)

    async def test_inactive_latest_recovery_creates_new_and_commits_before_delivery(self):
        request_id = uuid.UUID("a2222222-2222-2222-2222-222222222222")
        user_id = 202
        now = datetime(2026, 9, 11, 11, 0, 0)

        login_request = SessionLoginRequest(
            id=request_id,
            user_id=user_id,
            requester_device_name="Firefox on Linux",
            requester_ip="10.0.0.5",
            requester_home_server="foreign",
            status=LoginRequestStatus.PENDING,
            expires_at=now + timedelta(minutes=15),
        )

        inactive_recovery_id = uuid.UUID("d9999999-9999-9999-9999-999999999999")
        inactive_recovery = SingleSessionRecoveryRequest(
            id=inactive_recovery_id,
            user_id=user_id,
            session_login_request_id=request_id,
            requester_device_name="Old Terminal Device",
            requester_ip="1.1.1.1",
            status=SingleSessionRecoveryStatus.EXPIRED,
            created_at=now - timedelta(hours=3),
            inline_action_expires_at=now - timedelta(hours=2),
            chat_action_expires_at=now - timedelta(hours=1),
            identity_requested_at=None,
            identity_submitted_at=None,
            decided_at=None,
            cancelled_at=None,
        )

        user = User(
            id=user_id,
            account_name="alice",
            mobile_number="+989123456789",
            full_name="Alice Trader",
            address="123 Trading St",
            role=UserRole.STANDARD,
            max_sessions=1,
            is_deleted=False,
        )

        admin_user = User(
            id=999,
            account_name="admin",
            mobile_number="+989123456780",
            full_name="Super Admin",
            address="Admin Office",
            role=UserRole.SUPER_ADMIN,
            max_sessions=1,
            is_deleted=False,
        )
        admin_users = [admin_user]

        created_recovery_id = uuid.UUID("c2222222-2222-2222-2222-222222222222")
        created_recovery = SingleSessionRecoveryRequest(
            id=created_recovery_id,
            user_id=user_id,
            session_login_request_id=request_id,
            requester_device_name="Firefox on Linux",
            requester_ip="10.0.0.5",
            status=SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW,
            created_at=now,
            inline_action_expires_at=now + timedelta(seconds=30),
            chat_action_expires_at=now + timedelta(hours=2),
            identity_requested_at=None,
            identity_submitted_at=None,
            decided_at=None,
            cancelled_at=None,
        )

        db = FakeCharacterizationDB(execute_results=[login_request, user])

        class DeterministicDateTime(datetime):
            @classmethod
            def utcnow(cls):
                return now

        call_order = []

        async def fake_list_admins(_db):
            call_order.append("list_admins")
            return admin_users

        async def fake_create_recovery(_db, _login_req):
            call_order.append("create")
            return created_recovery

        async def fake_commit():
            call_order.append("commit")

        async def fake_deliver(_db, _rec, _user, _admins):
            call_order.append("deliver")

        db.commit.side_effect = fake_commit

        with patch(
            "api.routers.sessions.datetime", DeterministicDateTime
        ), patch(
            "api.routers.sessions.get_latest_recovery_request_for_login_request",
            new_callable=AsyncMock,
        ) as mock_get_latest, patch(
            "api.routers.sessions.list_recovery_admin_users",
            new_callable=AsyncMock,
            side_effect=fake_list_admins,
        ) as mock_list_admins, patch(
            "api.routers.sessions.create_recovery_request",
            new_callable=AsyncMock,
            side_effect=fake_create_recovery,
        ) as mock_create_recovery, patch(
            "api.routers.sessions._deliver_initial_recovery_messages",
            new_callable=AsyncMock,
            side_effect=fake_deliver,
        ) as mock_deliver:
            mock_get_latest.return_value = inactive_recovery

            result = await start_single_session_recovery(str(request_id), db=db)

            self.assertIsNot(created_recovery, inactive_recovery)
            self.assertNotEqual(result["id"], str(inactive_recovery.id))
            self.assertEqual(result["id"], str(created_recovery.id))
            self.assertNotEqual(result["status"], inactive_recovery.status.value)
            self.assertEqual(result["status"], created_recovery.status.value)

            expected_payload = {
                "id": "c2222222-2222-2222-2222-222222222222",
                "status": "pending_admin_review",
                "requester_device_name": "Firefox on Linux",
                "requester_ip": "10.0.0.5",
                "created_at": "2026-09-11T11:00:00",
                "inline_action_expires_at": "2026-09-11T11:00:30",
                "chat_action_expires_at": "2026-09-11T13:00:00",
                "identity_requested_at": None,
                "identity_submitted_at": None,
                "decided_at": None,
                "cancelled_at": None,
            }
            self.assertEqual(result, expected_payload)

            self.assertEqual(len(db.executed_statements), 2)
            stmt1 = db.executed_statements[0]
            self.assertIn(SessionLoginRequest, [cd["expr"] for cd in stmt1.column_descriptions])
            self.assertEqual(stmt1.whereclause.left.key, "id")
            self.assertEqual(stmt1.whereclause.right.value, request_id)

            stmt2 = db.executed_statements[1]
            self.assertIn(User, [cd["expr"] for cd in stmt2.column_descriptions])
            self.assertEqual(stmt2.whereclause.left.key, "id")
            self.assertEqual(stmt2.whereclause.right.value, user_id)

            self.assertEqual(len(db.execute_results), 0)

            mock_get_latest.assert_awaited_once_with(db, request_id)
            mock_list_admins.assert_awaited_once_with(db)
            mock_create_recovery.assert_awaited_once_with(db, login_request)
            self.assertIs(mock_create_recovery.await_args[0][0], db)
            self.assertIs(mock_create_recovery.await_args[0][1], login_request)

            db.commit.assert_awaited_once()

            mock_deliver.assert_awaited_once_with(db, created_recovery, user, admin_users)
            self.assertIs(mock_deliver.await_args[0][0], db)
            self.assertIs(mock_deliver.await_args[0][1], created_recovery)
            self.assertIs(mock_deliver.await_args[0][2], user)
            self.assertIs(mock_deliver.await_args[0][3], admin_users)

            self.assertEqual(call_order, ["list_admins", "create", "commit", "deliver"])
            self.assertLess(call_order.index("commit"), call_order.index("deliver"))

            db.rollback.assert_not_called()
            db.add.assert_not_called()
            db.flush.assert_not_called()
            db.refresh.assert_not_called()
            db.delete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
