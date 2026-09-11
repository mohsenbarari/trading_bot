"""Characterization tests for cancel_single_session_recovery handler.

P1-00b Stage card: docs/refactor/two-server/section-1/stages/P1-00b.md
Shared Assignment: /root/trading-bot/trading_bot/.local/two-site-refactor/assignments/WA-P1-00B-FREEZE-20260904-001.md
Tracked gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Exact surface seed: docs/refactor/two-server/section-1/inventory/surface-api.json#API-AUTH-POST-SESSIONS-CANCEL-SINGLE-SESSION-RECOVERY-L717

Disclaimer: This partial characterization neither promotes the seed
API-AUTH-POST-SESSIONS-CANCEL-SINGLE-SESSION-RECOVERY-L717 nor closes P1-00b.
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

from api.routers.sessions import cancel_single_session_recovery
from models.session import (
    LoginRequestStatus,
    SessionLoginRequest,
    SingleSessionRecoveryRequest,
    SingleSessionRecoveryStatus,
)


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


class P100bCharacterizationCancelSingleSessionRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_with_already_loaded_relationship_avoids_fallback_select(self):
        request_id = uuid.UUID("a1111111-1111-1111-1111-111111111111")
        recovery_id = uuid.UUID("b1111111-1111-1111-1111-111111111111")
        user_id = 101
        now = datetime(2026, 9, 11, 10, 0, 0)
        created_at = now - timedelta(minutes=10)
        inline_action_expires_at = now + timedelta(seconds=30)
        chat_action_expires_at = now + timedelta(hours=2)

        login_request = SessionLoginRequest(
            id=request_id,
            user_id=user_id,
            requester_device_name="Chrome on macOS",
            requester_ip="192.168.1.50",
            requester_home_server="foreign",
            status=LoginRequestStatus.PENDING,
            expires_at=now + timedelta(minutes=15),
        )

        active_recovery = SingleSessionRecoveryRequest(
            id=recovery_id,
            user_id=user_id,
            session_login_request_id=request_id,
            requester_device_name="Chrome on macOS",
            requester_ip="192.168.1.50",
            status=SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW,
            created_at=created_at,
            inline_action_expires_at=inline_action_expires_at,
            chat_action_expires_at=chat_action_expires_at,
            identity_requested_at=None,
            identity_submitted_at=None,
            decided_at=None,
            cancelled_at=None,
        )
        active_recovery.session_login_request = login_request

        db = FakeCharacterizationDB()

        call_order = []

        async def fake_clear(db_arg, recovery_request_id_arg):
            self.assertIs(db_arg, db)
            self.assertEqual(recovery_request_id_arg, recovery_id)
            self.assertEqual(active_recovery.status, SingleSessionRecoveryStatus.CANCELLED)
            self.assertEqual(active_recovery.cancelled_at, now)
            self.assertEqual(active_recovery.inline_action_expires_at, now)
            self.assertEqual(active_recovery.chat_action_expires_at, now)
            self.assertEqual(login_request.status, LoginRequestStatus.REJECTED)
            call_order.append("clear")

        async def fake_commit():
            call_order.append("commit")

        async def fake_publish(db_arg, recovery_request_arg):
            self.assertIs(db_arg, db)
            self.assertIs(recovery_request_arg, active_recovery)
            call_order.append("publish")

        db.commit.side_effect = fake_commit

        with patch(
            "core.services.single_session_recovery_service.utc_now_naive",
            return_value=now,
        ), patch(
            "api.routers.sessions.get_active_recovery_request_for_login_request",
            new_callable=AsyncMock,
        ) as mock_get_active, patch(
            "api.routers.sessions._clear_recovery_admin_action_messages",
            new_callable=AsyncMock,
            side_effect=fake_clear,
        ) as mock_clear, patch(
            "api.routers.sessions._publish_recovery_prompt_updates",
            new_callable=AsyncMock,
            side_effect=fake_publish,
        ) as mock_publish:
            mock_get_active.return_value = active_recovery

            result = await cancel_single_session_recovery(str(request_id), db=db)

            expected_payload = {
                "detail": "درخواست بازیابی لغو شد",
                "recovery": {
                    "id": str(recovery_id),
                    "status": "cancelled",
                    "requester_device_name": "Chrome on macOS",
                    "requester_ip": "192.168.1.50",
                    "created_at": created_at.isoformat(),
                    "inline_action_expires_at": now.isoformat(),
                    "chat_action_expires_at": now.isoformat(),
                    "identity_requested_at": None,
                    "identity_submitted_at": None,
                    "decided_at": None,
                    "cancelled_at": now.isoformat(),
                },
            }
            self.assertEqual(result, expected_payload)

            mock_get_active.assert_awaited_once_with(db, request_id)
            self.assertIs(mock_get_active.await_args[0][0], db)
            self.assertEqual(mock_get_active.await_args[0][1], request_id)

            self.assertEqual(len(db.executed_statements), 0)
            self.assertEqual(len(db.execute_results), 0)

            self.assertEqual(active_recovery.status, SingleSessionRecoveryStatus.CANCELLED)
            self.assertEqual(active_recovery.cancelled_at, now)
            self.assertEqual(active_recovery.inline_action_expires_at, now)
            self.assertEqual(active_recovery.chat_action_expires_at, now)
            self.assertEqual(login_request.status, LoginRequestStatus.REJECTED)

            mock_clear.assert_awaited_once_with(db, recovery_id)
            self.assertIs(mock_clear.await_args[0][0], db)
            self.assertEqual(mock_clear.await_args[0][1], recovery_id)

            db.commit.assert_awaited_once()

            mock_publish.assert_awaited_once_with(db, active_recovery)
            self.assertIs(mock_publish.await_args[0][0], db)
            self.assertIs(mock_publish.await_args[0][1], active_recovery)

            self.assertEqual(call_order, ["clear", "commit", "publish"])

            db.rollback.assert_not_called()
            db.add.assert_not_called()
            db.flush.assert_not_called()
            db.refresh.assert_not_called()
            db.delete.assert_not_called()

    async def test_cancel_with_missing_relationship_executes_fallback_select_and_rejects_login_request(self):
        request_id = uuid.UUID("a2222222-2222-2222-2222-222222222222")
        recovery_id = uuid.UUID("b2222222-2222-2222-2222-222222222222")
        user_id = 202
        now = datetime(2026, 9, 11, 11, 30, 0)
        created_at = now - timedelta(minutes=20)
        identity_requested_at = now - timedelta(minutes=10)
        inline_action_expires_at = now + timedelta(seconds=45)
        chat_action_expires_at = now + timedelta(hours=1)

        login_request = SessionLoginRequest(
            id=request_id,
            user_id=user_id,
            requester_device_name="Firefox on Linux",
            requester_ip="10.0.0.5",
            requester_home_server="foreign",
            status=LoginRequestStatus.PENDING,
            expires_at=now + timedelta(minutes=15),
        )

        active_recovery = SingleSessionRecoveryRequest(
            id=recovery_id,
            user_id=user_id,
            session_login_request_id=request_id,
            requester_device_name="Firefox on Linux",
            requester_ip="10.0.0.5",
            status=SingleSessionRecoveryStatus.IDENTITY_VERIFICATION_REQUESTED,
            created_at=created_at,
            inline_action_expires_at=inline_action_expires_at,
            chat_action_expires_at=chat_action_expires_at,
            identity_requested_at=identity_requested_at,
            identity_submitted_at=None,
            decided_at=None,
            cancelled_at=None,
        )
        active_recovery.session_login_request = None

        db = FakeCharacterizationDB(execute_results=[login_request])

        call_order = []

        async def fake_clear(db_arg, recovery_request_id_arg):
            self.assertIs(db_arg, db)
            self.assertEqual(recovery_request_id_arg, recovery_id)
            self.assertEqual(active_recovery.status, SingleSessionRecoveryStatus.CANCELLED)
            self.assertEqual(active_recovery.cancelled_at, now)
            self.assertEqual(active_recovery.inline_action_expires_at, now)
            self.assertEqual(active_recovery.chat_action_expires_at, now)
            self.assertEqual(login_request.status, LoginRequestStatus.REJECTED)
            call_order.append("clear")

        async def fake_commit():
            call_order.append("commit")

        async def fake_publish(db_arg, recovery_request_arg):
            self.assertIs(db_arg, db)
            self.assertIs(recovery_request_arg, active_recovery)
            call_order.append("publish")

        db.commit.side_effect = fake_commit

        with patch(
            "core.services.single_session_recovery_service.utc_now_naive",
            return_value=now,
        ), patch(
            "api.routers.sessions.get_active_recovery_request_for_login_request",
            new_callable=AsyncMock,
        ) as mock_get_active, patch(
            "api.routers.sessions._clear_recovery_admin_action_messages",
            new_callable=AsyncMock,
            side_effect=fake_clear,
        ) as mock_clear, patch(
            "api.routers.sessions._publish_recovery_prompt_updates",
            new_callable=AsyncMock,
            side_effect=fake_publish,
        ) as mock_publish:
            mock_get_active.return_value = active_recovery

            result = await cancel_single_session_recovery(str(request_id), db=db)

            expected_payload = {
                "detail": "درخواست بازیابی لغو شد",
                "recovery": {
                    "id": str(recovery_id),
                    "status": "cancelled",
                    "requester_device_name": "Firefox on Linux",
                    "requester_ip": "10.0.0.5",
                    "created_at": created_at.isoformat(),
                    "inline_action_expires_at": now.isoformat(),
                    "chat_action_expires_at": now.isoformat(),
                    "identity_requested_at": identity_requested_at.isoformat(),
                    "identity_submitted_at": None,
                    "decided_at": None,
                    "cancelled_at": now.isoformat(),
                },
            }
            self.assertEqual(result, expected_payload)

            mock_get_active.assert_awaited_once_with(db, request_id)
            self.assertIs(mock_get_active.await_args[0][0], db)
            self.assertEqual(mock_get_active.await_args[0][1], request_id)

            self.assertEqual(len(db.executed_statements), 1)
            stmt = db.executed_statements[0]
            self.assertIn(SessionLoginRequest, [cd["expr"] for cd in stmt.column_descriptions])
            self.assertEqual(stmt.whereclause.left.key, "id")
            self.assertEqual(stmt.whereclause.right.value, request_id)
            self.assertEqual(len(db.execute_results), 0)

            self.assertEqual(active_recovery.status, SingleSessionRecoveryStatus.CANCELLED)
            self.assertEqual(active_recovery.cancelled_at, now)
            self.assertEqual(active_recovery.inline_action_expires_at, now)
            self.assertEqual(active_recovery.chat_action_expires_at, now)
            self.assertEqual(login_request.status, LoginRequestStatus.REJECTED)

            mock_clear.assert_awaited_once_with(db, recovery_id)
            self.assertIs(mock_clear.await_args[0][0], db)
            self.assertEqual(mock_clear.await_args[0][1], recovery_id)

            db.commit.assert_awaited_once()

            mock_publish.assert_awaited_once_with(db, active_recovery)
            self.assertIs(mock_publish.await_args[0][0], db)
            self.assertIs(mock_publish.await_args[0][1], active_recovery)

            self.assertEqual(call_order, ["clear", "commit", "publish"])

            db.rollback.assert_not_called()
            db.add.assert_not_called()
            db.flush.assert_not_called()
            db.refresh.assert_not_called()
            db.delete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
