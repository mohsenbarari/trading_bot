"""Characterization tests for POST /sessions/logout-all caller authorization branch.

Stage card: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment: /root/trading-bot/trading_bot/.local/two-site-refactor/assignments/WA-P1-00B-FREEZE-20260904-001.md
Surface behavior ID: docs/refactor/two-server/section-1/inventory/surface-api.json#API-AUTH-POST-SESSIONS-LOGOUT-ALL-SESSIONS-L1146
Seed gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

This module characterises the caller UserSession authorization checks in
api.routers.sessions.logout_all_sessions after a valid bearer JWT sid is decoded:
1. Absent caller session row (scalar_one_or_none -> None) produces HTTP 403.
2. Non-primary caller session row (is_primary=False) produces HTTP 403.
Both cases emit the exact Persian detail containing U+200C and protect against
calling force_clear_sessions or audit_log.
"""

from __future__ import annotations

from datetime import datetime
import operator
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

# Install hermetic network fence prior to importing application modules
install_p1_00_network_fence()

from fastapi import HTTPException
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import BinaryExpression, BindParameter, BooleanClauseList

from api.routers.sessions import logout_all_sessions
from models.session import UserSession
from models.user import UserRole

FIXED_CALLER_SID = uuid.UUID("3fa85f64-5717-4562-b3fc-2c963f66afa6")
FIXED_USER_ID = 42
EXPECTED_FORBIDDEN_DETAIL = (
    "شما اجازه خروج از سایر نشست\u200cها را ندارید. (فقط دستگاه اصلی مجاز است)"
)


class RecordingExecuteResult:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object:
        return self._value


class RecordingAsyncDB:
    """Async DB test double exposing only execute to detect unintended calls."""

    def __init__(self, execute_result: object) -> None:
        self._result = execute_result
        self.executed_statements: list[object] = []

    async def execute(self, stmt: object) -> RecordingExecuteResult:
        self.executed_statements.append(stmt)
        return RecordingExecuteResult(self._result)


class SessionsLogoutAllCallerAuthorizationCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    def _assert_caller_query(
        self, db: RecordingAsyncDB, caller_sid: uuid.UUID
    ) -> None:
        self.assertEqual(len(db.executed_statements), 1)
        stmt = db.executed_statements[0]
        self.assertIsInstance(stmt, Select)

        # Entity checks
        self.assertEqual(len(stmt.column_descriptions), 1)
        self.assertIs(stmt.column_descriptions[0]["entity"], UserSession)
        self.assertIs(stmt.column_descriptions[0]["type"], UserSession)

        # Predicate checks: solitary equality on UserSession.id
        whereclause = stmt.whereclause
        self.assertIsNotNone(whereclause)
        self.assertIsInstance(whereclause, BinaryExpression)
        self.assertNotIsInstance(whereclause, BooleanClauseList)

        self.assertEqual(whereclause.left.name, "id")
        self.assertEqual(whereclause.left.table.name, "user_sessions")
        self.assertIs(whereclause.left.table, UserSession.__table__)

        self.assertIs(whereclause.operator, operator.eq)

        self.assertIsInstance(whereclause.right, BindParameter)
        self.assertEqual(whereclause.right.value, caller_sid)

        # Parameter cardinality and ensure no hidden filter
        compiled = stmt.compile()
        self.assertEqual(len(compiled.params), 1)
        self.assertEqual(list(compiled.params.values()), [caller_sid])
        self.assertNotIn("user_id", compiled.params)
        self.assertNotIn("is_active", compiled.params)

    async def test_logout_all_sessions_caller_absent_raises_403_and_protects_sinks(
        self,
    ) -> None:
        current_user = SimpleNamespace(
            id=FIXED_USER_ID, role=UserRole.STANDARD, is_accountant=False
        )
        request = SimpleNamespace(headers={"authorization": "Bearer deterministic-token"})
        db = RecordingAsyncDB(execute_result=None)

        guard_mock = AsyncMock(return_value=None)
        clear_mock = AsyncMock()
        audit_mock = MagicMock()

        with (
            patch("jose.jwt.decode", return_value={"sid": str(FIXED_CALLER_SID)}),
            patch(
                "api.routers.sessions.ensure_non_accountant_session_management",
                new=guard_mock,
            ),
            patch("api.routers.sessions.force_clear_sessions", new=clear_mock),
            patch("api.routers.sessions.audit_log", new=audit_mock),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await logout_all_sessions(
                    request=request,
                    db=db,
                    current_user=current_user,
                )

        self._assert_caller_query(db, FIXED_CALLER_SID)

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, EXPECTED_FORBIDDEN_DETAIL)
        self.assertEqual(exc_info.exception.detail.count("\u200c"), 1)
        self.assertNotEqual(
            exc_info.exception.detail,
            exc_info.exception.detail.replace("\u200c", ""),
        )

        guard_mock.assert_awaited_once_with(db, current_user)
        self.assertIs(guard_mock.await_args[0][0], db)
        self.assertIs(guard_mock.await_args[0][1], current_user)

        clear_mock.assert_not_awaited()
        audit_mock.assert_not_called()

    async def test_logout_all_sessions_caller_non_primary_raises_403_and_protects_sinks(
        self,
    ) -> None:
        current_user = SimpleNamespace(
            id=FIXED_USER_ID, role=UserRole.STANDARD, is_accountant=False
        )
        caller_session = SimpleNamespace(
            id=FIXED_CALLER_SID,
            user_id=FIXED_USER_ID,
            device_name="Pixel",
            device_ip="10.0.0.8",
            platform="web",
            home_server="foreign",
            refresh_token_hash=None,
            is_primary=False,
            is_active=True,
            created_at=datetime(2026, 1, 1, 12, 0, 0),
            last_active_at=datetime(2026, 1, 1, 13, 0, 0),
            expires_at=None,
        )
        request = SimpleNamespace(headers={"authorization": "Bearer deterministic-token"})
        db = RecordingAsyncDB(execute_result=caller_session)

        guard_mock = AsyncMock(return_value=None)
        clear_mock = AsyncMock()
        audit_mock = MagicMock()

        with (
            patch("jose.jwt.decode", return_value={"sid": str(FIXED_CALLER_SID)}),
            patch(
                "api.routers.sessions.ensure_non_accountant_session_management",
                new=guard_mock,
            ),
            patch("api.routers.sessions.force_clear_sessions", new=clear_mock),
            patch("api.routers.sessions.audit_log", new=audit_mock),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await logout_all_sessions(
                    request=request,
                    db=db,
                    current_user=current_user,
                )

        self._assert_caller_query(db, FIXED_CALLER_SID)

        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(exc_info.exception.detail, EXPECTED_FORBIDDEN_DETAIL)
        self.assertEqual(exc_info.exception.detail.count("\u200c"), 1)
        self.assertNotEqual(
            exc_info.exception.detail,
            exc_info.exception.detail.replace("\u200c", ""),
        )

        guard_mock.assert_awaited_once_with(db, current_user)
        self.assertIs(guard_mock.await_args[0][0], db)
        self.assertIs(guard_mock.await_args[0][1], current_user)

        clear_mock.assert_not_awaited()
        audit_mock.assert_not_called()
