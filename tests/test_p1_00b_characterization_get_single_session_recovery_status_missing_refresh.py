"""Hermetic characterization test for get_single_session_recovery_status without refresh token.

Stage: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment: WA-P1-00B-EVIDENCE-RECONCILE-20260930-045
Tracked gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Exact surface seed: docs/refactor/two-server/section-1/inventory/surface-api.json#API-AUTH-GET-SESSIONS-GET-SINGLE-SESSION-RECOVERY-STATUS-L749
Scenario: P1-00B-SEED-AUTH-RECOVERY-STATUS-APPROVED-MISSING-REFRESH-V1

Disclaimer: This partial characterization neither promotes the seed
API-AUTH-GET-SESSIONS-GET-SINGLE-SESSION-RECOVERY-STATUS-L749, resolves the High blocker
P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE, nor closes P1-00b.
"""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import unittest
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

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


class _SingleUseQueryResult:
    def __init__(self, session: UserSession):
        self._session = session
        self.scalar_one_or_none_call_count = 0

    def scalar_one_or_none(self) -> UserSession:
        if self.scalar_one_or_none_call_count > 0:
            raise AssertionError("scalar_one_or_none called more than once on single-use result")
        self.scalar_one_or_none_call_count += 1
        return self._session

    def scalars(self):
        raise AssertionError("Unexpected result method call: scalars")

    def all(self):
        raise AssertionError("Unexpected result method call: all")

    def first(self):
        raise AssertionError("Unexpected result method call: first")

    def one(self):
        raise AssertionError("Unexpected result method call: one")


class _QueryAwareCharacterizationDB:
    def __init__(
        self,
        expected_user_id: int,
        matching_session: UserSession,
        call_order: list[str] | None = None,
    ):
        self.expected_user_id = expected_user_id
        self.matching_session = matching_session
        self.call_order = call_order
        self.executed_statements = []
        self.result = None
        self.execute_count = 0
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self.add = Mock()
        self.flush = AsyncMock()
        self.refresh = AsyncMock()
        self.delete = Mock()

    async def execute(self, stmt):
        if self.execute_count > 0:
            raise AssertionError(f"Unexpected additional db.execute() call: {stmt}")
        self.execute_count += 1
        self.executed_statements.append(stmt)
        if self.call_order is not None:
            self.call_order.append("execute")

        column_descriptions = [cd["expr"] for cd in getattr(stmt, "column_descriptions", [])]
        if UserSession not in column_descriptions:
            raise AssertionError(f"db.execute statement does not target UserSession: {stmt}")

        whereclause = getattr(stmt, "whereclause", None)
        if whereclause is None:
            raise AssertionError(f"db.execute statement has no whereclause: {stmt}")

        where_clauses = list(getattr(whereclause, "clauses", [whereclause]))
        clauses_by_col = {
            c.left.key: getattr(c.right, "value", c.right)
            for c in where_clauses
            if hasattr(c, "left") and hasattr(c.left, "key")
        }
        if clauses_by_col.get("user_id") != self.expected_user_id:
            raise AssertionError(
                f"Expected UserSession.user_id == {self.expected_user_id}, got {clauses_by_col.get('user_id')}"
            )
        is_active_val = clauses_by_col.get("is_active")
        if not (is_active_val == True or str(is_active_val).lower() == "true"):
            raise AssertionError(
                f"Expected UserSession.is_active == True, got {is_active_val}"
            )

        order_by = getattr(stmt, "_order_by_clauses", [])
        if (
            len(order_by) != 1
            or getattr(order_by[0].element, "key", None) != "created_at"
            or "desc" not in str(order_by[0]).lower()
        ):
            raise AssertionError(f"Expected order_by(UserSession.created_at.desc()), got: {order_by}")

        limit_val = getattr(stmt, "_limit", None)
        if hasattr(limit_val, "value"):
            limit_val = limit_val.value
        if limit_val is None and hasattr(stmt, "_limit_clause"):
            limit_val = getattr(stmt._limit_clause, "value", None)
        if limit_val != 1:
            raise AssertionError(f"Expected limit 1, got {limit_val}")

        self.result = _SingleUseQueryResult(self.matching_session)
        return self.result


class RecoveryStatusMissingRefreshCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_approved_active_session_without_refresh_token_returns_access_only(self):
        fence = install_p1_00_network_fence()
        denied_before = len(fence["denied_connects"])
        redis_before = len(fence["live_redis_constructs"])

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
        db = _QueryAwareCharacterizationDB(
            expected_user_id=user_id,
            matching_session=user_session,
            call_order=call_order,
        )
        access_token_sentinel = "sentinel-access-token-jwt-missing-refresh"
        expected_cache_key = _get_recovery_token_cache_key(recovery_id)

        async def fake_get_latest(db_arg, req_id_arg):
            self.assertIs(db_arg, db)
            self.assertEqual(req_id_arg, request_id)
            call_order.append("get_latest")
            return recovery_request

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
            return None

        with patch(
            "api.routers.sessions.get_latest_recovery_request_for_login_request",
            new_callable=AsyncMock,
            side_effect=fake_get_latest,
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
            }
            self.assertEqual(result, expected_response)
            self.assertIn("access_token", result)
            self.assertEqual(result["access_token"], access_token_sentinel)
            self.assertNotIn("refresh_token", result)
            self.assertNotIn("token_type", result)

            mock_get_latest.assert_awaited_once_with(db, request_id)
            mock_expire.assert_awaited_once_with(db, recovery_request)
            mock_create_access_token.assert_called_once_with(
                subject=user_id,
                session_id=str(session_id),
                server_id="ir-primary",
            )
            mock_pop_token.assert_awaited_once_with(expected_cache_key)

            self.assertEqual(
                call_order,
                ["get_latest", "expire", "execute", "create_access_token", "pop_token"],
            )

            self.assertEqual(db.execute_count, 1)
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
            self.assertTrue(
                clauses_by_col["is_active"] == True
                or str(clauses_by_col["is_active"]).lower() == "true"
            )
            self.assertEqual(len(stmt._order_by_clauses), 1)
            self.assertEqual(stmt._order_by_clauses[0].element.key, "created_at")
            self.assertTrue("desc" in str(stmt._order_by_clauses[0]).lower())

            limit_val = getattr(stmt, "_limit", None)
            if hasattr(limit_val, "value"):
                limit_val = limit_val.value
            if limit_val is None and hasattr(stmt, "_limit_clause"):
                limit_val = getattr(stmt._limit_clause, "value", None)
            self.assertEqual(limit_val, 1)

            self.assertIsNotNone(db.result)
            self.assertEqual(db.result.scalar_one_or_none_call_count, 1)

            with self.assertRaises(AssertionError):
                await db.execute(stmt)
            with self.assertRaises(AssertionError):
                db.result.scalar_one_or_none()
            with self.assertRaises(AssertionError):
                db.result.scalars()

            db.commit.assert_not_called()
            db.rollback.assert_not_called()
            db.add.assert_not_called()
            db.flush.assert_not_called()
            db.refresh.assert_not_called()
            db.delete.assert_not_called()

            self.assertEqual(len(fence["denied_connects"]), denied_before)
            self.assertEqual(len(fence["live_redis_constructs"]), redis_before)


if __name__ == "__main__":
    unittest.main()
