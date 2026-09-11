"""Characterization tests for get_single_session_recovery_status handler.

P1-00b Stage card: docs/refactor/two-server/section-1/stages/P1-00b.md
Shared Assignment: /root/trading-bot/trading_bot/.local/two-site-refactor/assignments/WA-P1-00B-FREEZE-20260904-001.md
Tracked gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Exact surface seed: docs/refactor/two-server/section-1/inventory/surface-api.json#API-AUTH-GET-SESSIONS-GET-SINGLE-SESSION-RECOVERY-STATUS-L749

Disclaimer: This partial characterization neither promotes the seed
API-AUTH-GET-SESSIONS-GET-SINGLE-SESSION-RECOVERY-STATUS-L749 nor closes P1-00b.
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

from fastapi import HTTPException

from api.routers.sessions import (
    _get_recovery_token_cache_key,
    get_single_session_recovery_status,
)
from models.session import (
    Platform,
    SingleSessionRecoveryRequest,
    SingleSessionRecoveryStatus,
    UserSession,
)


class _FakeExecuteResult:
    def __init__(self, value=None):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeCharacterizationDB:
    def __init__(self, execute_results=None, call_order=None):
        self.execute_results = list(execute_results or [])
        self.executed_statements = []
        self.call_order = call_order
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self.add = Mock()
        self.flush = AsyncMock()
        self.refresh = AsyncMock()
        self.delete = Mock()

    async def execute(self, stmt):
        self.executed_statements.append(stmt)
        if self.call_order is not None:
            self.call_order.append("execute")
        if not self.execute_results:
            raise AssertionError("Unexpected db.execute() call: result queue exhausted")
        return _FakeExecuteResult(self.execute_results.pop(0))


class P100bCharacterizationGetSingleSessionRecoveryStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_status_invalid_uuid_raises_http_400(self):
        db = FakeCharacterizationDB()

        with patch(
            "api.routers.sessions.get_latest_recovery_request_for_login_request",
            new_callable=AsyncMock,
        ) as mock_get_latest, patch(
            "api.routers.sessions._expire_recovery_if_needed",
            new_callable=AsyncMock,
        ) as mock_expire, patch(
            "api.routers.sessions.create_access_token",
        ) as mock_create_access_token, patch(
            "api.routers.sessions._pop_temporary_refresh_token",
            new_callable=AsyncMock,
        ) as mock_pop_token:
            with self.assertRaises(HTTPException) as ctx:
                await get_single_session_recovery_status("not-a-valid-uuid", db=db)

            self.assertEqual(ctx.exception.status_code, 400)
            self.assertEqual(ctx.exception.detail, "شناسه درخواست نامعتبر است")

            mock_get_latest.assert_not_called()
            mock_expire.assert_not_called()
            mock_create_access_token.assert_not_called()
            mock_pop_token.assert_not_called()

            self.assertEqual(len(db.executed_statements), 0)
            self.assertEqual(len(db.execute_results), 0)

            db.commit.assert_not_called()
            db.rollback.assert_not_called()
            db.add.assert_not_called()
            db.flush.assert_not_called()
            db.refresh.assert_not_called()
            db.delete.assert_not_called()

    async def test_get_status_valid_uuid_no_latest_recovery_returns_not_started(self):
        request_id = uuid.UUID("a1111111-1111-1111-1111-111111111111")
        db = FakeCharacterizationDB()

        with patch(
            "api.routers.sessions.get_latest_recovery_request_for_login_request",
            new_callable=AsyncMock,
        ) as mock_get_latest, patch(
            "api.routers.sessions._expire_recovery_if_needed",
            new_callable=AsyncMock,
        ) as mock_expire, patch(
            "api.routers.sessions.create_access_token",
        ) as mock_create_access_token, patch(
            "api.routers.sessions._pop_temporary_refresh_token",
            new_callable=AsyncMock,
        ) as mock_pop_token:
            mock_get_latest.return_value = None

            result = await get_single_session_recovery_status(str(request_id), db=db)

            self.assertEqual(result, {"status": "not_started"})

            mock_get_latest.assert_awaited_once_with(db, request_id)
            self.assertIs(mock_get_latest.await_args[0][0], db)
            self.assertEqual(mock_get_latest.await_args[0][1], request_id)

            mock_expire.assert_not_called()
            mock_create_access_token.assert_not_called()
            mock_pop_token.assert_not_called()

            self.assertEqual(len(db.executed_statements), 0)
            self.assertEqual(len(db.execute_results), 0)

            db.commit.assert_not_called()
            db.rollback.assert_not_called()
            db.add.assert_not_called()
            db.flush.assert_not_called()
            db.refresh.assert_not_called()
            db.delete.assert_not_called()

    async def test_get_status_approved_recovery_with_active_session_and_refresh_token(self):
        request_id = uuid.UUID("a1111111-1111-1111-1111-111111111111")
        recovery_id = uuid.UUID("b2222222-2222-2222-2222-222222222222")
        session_id = uuid.UUID("c3333333-3333-3333-3333-333333333333")
        user_id = 101

        now = datetime(2026, 9, 11, 12, 0, 0)
        created_at = now - timedelta(minutes=20)
        inline_action_expires_at = now + timedelta(minutes=5)
        chat_action_expires_at = now + timedelta(hours=1)
        identity_requested_at = now - timedelta(minutes=15)
        identity_submitted_at = now - timedelta(minutes=10)
        decided_at = now - timedelta(minutes=2)

        recovery_request = SingleSessionRecoveryRequest(
            id=recovery_id,
            user_id=user_id,
            session_login_request_id=request_id,
            requester_device_name="Safari on macOS",
            requester_ip="198.51.100.25",
            status=SingleSessionRecoveryStatus.APPROVED,
            created_at=created_at,
            inline_action_expires_at=inline_action_expires_at,
            chat_action_expires_at=chat_action_expires_at,
            identity_requested_at=identity_requested_at,
            identity_submitted_at=identity_submitted_at,
            decided_at=decided_at,
            cancelled_at=None,
        )

        user_session = UserSession(
            id=session_id,
            user_id=user_id,
            device_name="Safari on macOS",
            device_ip="198.51.100.25",
            home_server="ir-primary",
            platform=Platform.WEB,
            is_active=True,
            created_at=decided_at,
            last_active_at=decided_at,
        )

        call_order = []
        db = FakeCharacterizationDB(execute_results=[user_session], call_order=call_order)
        access_token_sentinel = "sentinel-access-token-jwt"
        refresh_token_sentinel = "sentinel-refresh-token-value"
        expected_cache_key = _get_recovery_token_cache_key(recovery_id)

        async def fake_expire(db_arg, recovery_arg):
            self.assertIs(db_arg, db)
            self.assertIs(recovery_arg, recovery_request)
            call_order.append("expire")
            return False

        def fake_create_access_token(**kwargs):
            call_order.append("create_access_token")
            return access_token_sentinel

        async def fake_pop_token(cache_key_arg):
            self.assertEqual(cache_key_arg, expected_cache_key)
            call_order.append("pop_token")
            return refresh_token_sentinel

        with patch(
            "api.routers.sessions.get_latest_recovery_request_for_login_request",
            new_callable=AsyncMock,
        ) as mock_get_latest, patch(
            "api.routers.sessions._expire_recovery_if_needed",
            new_callable=AsyncMock,
            side_effect=fake_expire,
        ) as mock_expire, patch(
            "api.routers.sessions.create_access_token",
            side_effect=fake_create_access_token,
        ) as mock_create_access_token, patch(
            "api.routers.sessions._pop_temporary_refresh_token",
            new_callable=AsyncMock,
            side_effect=fake_pop_token,
        ) as mock_pop_token:
            mock_get_latest.return_value = recovery_request

            result = await get_single_session_recovery_status(str(request_id), db=db)

            expected_response = {
                "id": str(recovery_id),
                "status": "approved",
                "requester_device_name": "Safari on macOS",
                "requester_ip": "198.51.100.25",
                "created_at": created_at.isoformat(),
                "inline_action_expires_at": inline_action_expires_at.isoformat(),
                "chat_action_expires_at": chat_action_expires_at.isoformat(),
                "identity_requested_at": identity_requested_at.isoformat(),
                "identity_submitted_at": identity_submitted_at.isoformat(),
                "decided_at": decided_at.isoformat(),
                "cancelled_at": None,
                "access_token": access_token_sentinel,
                "refresh_token": refresh_token_sentinel,
                "token_type": "bearer",
            }
            self.assertEqual(result, expected_response)

            mock_get_latest.assert_awaited_once_with(db, request_id)
            self.assertIs(mock_get_latest.await_args[0][0], db)
            self.assertEqual(mock_get_latest.await_args[0][1], request_id)

            mock_expire.assert_awaited_once_with(db, recovery_request)
            self.assertIs(mock_expire.await_args[0][0], db)
            self.assertIs(mock_expire.await_args[0][1], recovery_request)

            self.assertEqual(len(db.executed_statements), 1)
            stmt = db.executed_statements[0]
            self.assertIn(UserSession, [cd["expr"] for cd in stmt.column_descriptions])
            where_clauses = list(stmt.whereclause.clauses)
            self.assertEqual(len(where_clauses), 2)
            clauses_by_col = {
                c.left.key: getattr(c.right, "value", c.right)
                for c in where_clauses
            }
            self.assertEqual(clauses_by_col["user_id"], user_id)
            self.assertEqual(clauses_by_col["is_active"], True)
            self.assertEqual(len(stmt._order_by_clauses), 1)
            self.assertEqual(stmt._order_by_clauses[0].element.key, "created_at")
            self.assertTrue("desc" in str(stmt._order_by_clauses[0]).lower())

            limit_val = getattr(stmt, "_limit", None)
            if hasattr(limit_val, "value"):
                limit_val = limit_val.value
            if limit_val is None and hasattr(stmt, "_limit_clause"):
                limit_val = getattr(stmt._limit_clause, "value", None)
            self.assertEqual(limit_val, 1)

            self.assertEqual(len(db.execute_results), 0)

            mock_create_access_token.assert_called_once_with(
                subject=user_id,
                session_id=str(session_id),
                server_id="ir-primary",
            )
            mock_pop_token.assert_awaited_once_with(expected_cache_key)

            self.assertEqual(
                call_order,
                ["expire", "execute", "create_access_token", "pop_token"],
            )

            db.commit.assert_not_called()
            db.rollback.assert_not_called()
            db.add.assert_not_called()
            db.flush.assert_not_called()
            db.refresh.assert_not_called()
            db.delete.assert_not_called()

    async def test_get_status_approved_recovery_with_no_active_session(self):
        request_id = uuid.UUID("a1111111-1111-1111-1111-111111111111")
        recovery_id = uuid.UUID("b2222222-2222-2222-2222-222222222222")
        user_id = 101

        now = datetime(2026, 9, 11, 12, 0, 0)
        created_at = now - timedelta(minutes=20)
        inline_action_expires_at = now + timedelta(minutes=5)
        chat_action_expires_at = now + timedelta(hours=1)
        identity_requested_at = now - timedelta(minutes=15)
        identity_submitted_at = now - timedelta(minutes=10)
        decided_at = now - timedelta(minutes=2)

        recovery_request = SingleSessionRecoveryRequest(
            id=recovery_id,
            user_id=user_id,
            session_login_request_id=request_id,
            requester_device_name="Safari on macOS",
            requester_ip="198.51.100.25",
            status=SingleSessionRecoveryStatus.APPROVED,
            created_at=created_at,
            inline_action_expires_at=inline_action_expires_at,
            chat_action_expires_at=chat_action_expires_at,
            identity_requested_at=identity_requested_at,
            identity_submitted_at=identity_submitted_at,
            decided_at=decided_at,
            cancelled_at=None,
        )

        call_order = []
        db = FakeCharacterizationDB(execute_results=[None], call_order=call_order)

        async def fake_expire(db_arg, recovery_arg):
            self.assertIs(db_arg, db)
            self.assertIs(recovery_arg, recovery_request)
            call_order.append("expire")
            return False

        with patch(
            "api.routers.sessions.get_latest_recovery_request_for_login_request",
            new_callable=AsyncMock,
        ) as mock_get_latest, patch(
            "api.routers.sessions._expire_recovery_if_needed",
            new_callable=AsyncMock,
            side_effect=fake_expire,
        ) as mock_expire, patch(
            "api.routers.sessions.create_access_token",
        ) as mock_create_access_token, patch(
            "api.routers.sessions._pop_temporary_refresh_token",
            new_callable=AsyncMock,
        ) as mock_pop_token:
            mock_get_latest.return_value = recovery_request

            result = await get_single_session_recovery_status(str(request_id), db=db)

            expected_response = {
                "id": str(recovery_id),
                "status": "approved",
                "requester_device_name": "Safari on macOS",
                "requester_ip": "198.51.100.25",
                "created_at": created_at.isoformat(),
                "inline_action_expires_at": inline_action_expires_at.isoformat(),
                "chat_action_expires_at": chat_action_expires_at.isoformat(),
                "identity_requested_at": identity_requested_at.isoformat(),
                "identity_submitted_at": identity_submitted_at.isoformat(),
                "decided_at": decided_at.isoformat(),
                "cancelled_at": None,
            }
            self.assertEqual(result, expected_response)
            self.assertNotIn("access_token", result)
            self.assertNotIn("refresh_token", result)
            self.assertNotIn("token_type", result)

            mock_get_latest.assert_awaited_once_with(db, request_id)
            self.assertIs(mock_get_latest.await_args[0][0], db)
            self.assertEqual(mock_get_latest.await_args[0][1], request_id)

            mock_expire.assert_awaited_once_with(db, recovery_request)
            self.assertIs(mock_expire.await_args[0][0], db)
            self.assertIs(mock_expire.await_args[0][1], recovery_request)

            self.assertEqual(len(db.executed_statements), 1)
            stmt = db.executed_statements[0]
            self.assertIn(UserSession, [cd["expr"] for cd in stmt.column_descriptions])
            where_clauses = list(stmt.whereclause.clauses)
            self.assertEqual(len(where_clauses), 2)
            clauses_by_col = {
                c.left.key: getattr(c.right, "value", c.right)
                for c in where_clauses
            }
            self.assertEqual(clauses_by_col["user_id"], user_id)
            self.assertEqual(clauses_by_col["is_active"], True)
            self.assertEqual(len(stmt._order_by_clauses), 1)
            self.assertEqual(stmt._order_by_clauses[0].element.key, "created_at")
            self.assertTrue("desc" in str(stmt._order_by_clauses[0]).lower())

            limit_val = getattr(stmt, "_limit", None)
            if hasattr(limit_val, "value"):
                limit_val = limit_val.value
            if limit_val is None and hasattr(stmt, "_limit_clause"):
                limit_val = getattr(stmt._limit_clause, "value", None)
            self.assertEqual(limit_val, 1)

            self.assertEqual(len(db.execute_results), 0)

            mock_create_access_token.assert_not_called()
            mock_pop_token.assert_not_called()

            self.assertEqual(call_order, ["expire", "execute"])

            db.commit.assert_not_called()
            db.rollback.assert_not_called()
            db.add.assert_not_called()
            db.flush.assert_not_called()
            db.refresh.assert_not_called()
            db.delete.assert_not_called()

    async def test_get_status_pending_recovery_returns_base_serialization_without_session_lookup(self):
        request_id = uuid.UUID("a1111111-1111-1111-1111-111111111111")
        recovery_id = uuid.UUID("b2222222-2222-2222-2222-222222222222")
        user_id = 101

        now = datetime(2026, 9, 11, 12, 0, 0)
        created_at = now - timedelta(minutes=20)
        inline_action_expires_at = now + timedelta(minutes=5)
        chat_action_expires_at = now + timedelta(hours=1)

        recovery_request = SingleSessionRecoveryRequest(
            id=recovery_id,
            user_id=user_id,
            session_login_request_id=request_id,
            requester_device_name="Chrome on Windows",
            requester_ip="198.51.100.50",
            status=SingleSessionRecoveryStatus.PENDING_ADMIN_REVIEW,
            created_at=created_at,
            inline_action_expires_at=inline_action_expires_at,
            chat_action_expires_at=chat_action_expires_at,
            identity_requested_at=None,
            identity_submitted_at=None,
            decided_at=None,
            cancelled_at=None,
        )

        call_order = []
        db = FakeCharacterizationDB(call_order=call_order)

        async def fake_expire(db_arg, recovery_arg):
            self.assertIs(db_arg, db)
            self.assertIs(recovery_arg, recovery_request)
            call_order.append("expire")
            return False

        with patch(
            "api.routers.sessions.get_latest_recovery_request_for_login_request",
            new_callable=AsyncMock,
        ) as mock_get_latest, patch(
            "api.routers.sessions._expire_recovery_if_needed",
            new_callable=AsyncMock,
            side_effect=fake_expire,
        ) as mock_expire, patch(
            "api.routers.sessions.create_access_token",
        ) as mock_create_access_token, patch(
            "api.routers.sessions._pop_temporary_refresh_token",
            new_callable=AsyncMock,
        ) as mock_pop_token:
            mock_get_latest.return_value = recovery_request

            result = await get_single_session_recovery_status(str(request_id), db=db)

            expected_response = {
                "id": str(recovery_id),
                "status": "pending_admin_review",
                "requester_device_name": "Chrome on Windows",
                "requester_ip": "198.51.100.50",
                "created_at": created_at.isoformat(),
                "inline_action_expires_at": inline_action_expires_at.isoformat(),
                "chat_action_expires_at": chat_action_expires_at.isoformat(),
                "identity_requested_at": None,
                "identity_submitted_at": None,
                "decided_at": None,
                "cancelled_at": None,
            }
            self.assertEqual(result, expected_response)
            self.assertNotIn("access_token", result)
            self.assertNotIn("refresh_token", result)
            self.assertNotIn("token_type", result)

            mock_get_latest.assert_awaited_once_with(db, request_id)
            self.assertIs(mock_get_latest.await_args[0][0], db)
            self.assertEqual(mock_get_latest.await_args[0][1], request_id)

            mock_expire.assert_awaited_once_with(db, recovery_request)
            self.assertIs(mock_expire.await_args[0][0], db)
            self.assertIs(mock_expire.await_args[0][1], recovery_request)

            self.assertEqual(len(db.executed_statements), 0)
            self.assertEqual(len(db.execute_results), 0)

            mock_create_access_token.assert_not_called()
            mock_pop_token.assert_not_called()

            self.assertEqual(call_order, ["expire"])

            db.commit.assert_not_called()
            db.rollback.assert_not_called()
            db.add.assert_not_called()
            db.flush.assert_not_called()
            db.refresh.assert_not_called()
            db.delete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
