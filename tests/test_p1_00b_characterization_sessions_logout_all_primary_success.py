"""Characterization test for logout_all_sessions primary success.

Stage: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment: /root/trading-bot/trading_bot/.local/two-site-refactor/assignments/WA-P1-00B-FREEZE-20260904-001.md
Surface behavior ID: docs/refactor/two-server/section-1/inventory/surface-api.json#API-AUTH-POST-SESSIONS-LOGOUT-ALL-SESSIONS-L1146
Seed gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

This module provides bounded characterization only and does not promote the seed
or close P1-00b.
"""

from __future__ import annotations

import operator
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import BinaryExpression, BindParameter, BooleanClauseList

from api.routers.sessions import logout_all_sessions
from models.session import UserSession
from models.user import UserRole


class RecordingExecuteResult:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object:
        return self._value


class RecordingAsyncDB:
    def __init__(self, execute_result: object, on_execute: object = None) -> None:
        self._result = execute_result
        self._on_execute = on_execute
        self.executed_statements: list[object] = []

    async def execute(self, stmt: object) -> RecordingExecuteResult:
        if callable(self._on_execute):
            self._on_execute()
        self.executed_statements.append(stmt)
        return RecordingExecuteResult(self._result)


class SessionsLogoutAllPrimarySuccessCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_logout_all_sessions_valid_primary_success(self) -> None:
        caller_sid = uuid.UUID("3fa85f64-5717-4562-b3fc-2c963f66afa6")
        user_id = 42
        current_user = SimpleNamespace(id=user_id, role=UserRole.STANDARD, is_accountant=False)
        caller_session = SimpleNamespace(
            id=caller_sid,
            user_id=user_id,
            is_primary=True,
            is_active=True,
        )
        request = SimpleNamespace(headers={"authorization": "Bearer valid-token"})

        observed_events: list[str] = []

        async def fake_guard(db_arg: object, user_arg: object) -> None:
            observed_events.append("accountant_guard")

        def on_db_execute() -> None:
            observed_events.append("db_execute")

        db = RecordingAsyncDB(execute_result=caller_session, on_execute=on_db_execute)

        async def fake_force_clear_sessions(
            db_arg: object, user_id_arg: object, exclude_session_id: object = None
        ) -> int:
            observed_events.append("force_clear_sessions")
            return 3

        def fake_audit_log(*args: object, **kwargs: object) -> None:
            observed_events.append("audit_log")

        guard_mock = AsyncMock(side_effect=fake_guard)
        clear_mock = AsyncMock(side_effect=fake_force_clear_sessions)
        audit_mock = MagicMock(side_effect=fake_audit_log)

        with (
            patch("jose.jwt.decode", return_value={"sid": str(caller_sid)}),
            patch("api.routers.sessions.ensure_non_accountant_session_management", new=guard_mock),
            patch("api.routers.sessions.force_clear_sessions", new=clear_mock),
            patch("api.routers.sessions.audit_log", new=audit_mock),
        ):
            response = await logout_all_sessions(
                request=request,
                db=db,
                current_user=current_user,
            )

        # Requirement 4: strict observable ordering
        self.assertEqual(
            observed_events,
            ["accountant_guard", "db_execute", "force_clear_sessions", "audit_log"],
        )

        # Requirement 4: guard receives exact db and current user
        guard_mock.assert_awaited_once_with(db, current_user)
        self.assertIs(guard_mock.await_args[0][0], db)
        self.assertIs(guard_mock.await_args[0][1], current_user)

        # Requirement 4: force_clear_sessions receives exact db, user id, and caller UUID
        clear_mock.assert_awaited_once_with(db, user_id, exclude_session_id=caller_sid)
        self.assertIs(clear_mock.await_args[0][0], db)
        self.assertEqual(clear_mock.await_args[0][1], user_id)
        self.assertEqual(clear_mock.await_args[1]["exclude_session_id"], caller_sid)

        # Requirement 3: inspect SQLAlchemy SELECT and its WHERE condition
        self.assertEqual(len(db.executed_statements), 1)
        stmt = db.executed_statements[0]
        self.assertIsInstance(stmt, Select)

        # Statement entity
        self.assertEqual(len(stmt.column_descriptions), 1)
        self.assertIs(stmt.column_descriptions[0]["entity"], UserSession)
        self.assertIs(stmt.column_descriptions[0]["type"], UserSession)

        # WHERE condition: single equality predicate
        whereclause = stmt.whereclause
        self.assertIsNotNone(whereclause)
        self.assertIsInstance(whereclause, BinaryExpression)
        self.assertNotIsInstance(whereclause, BooleanClauseList)

        # Left column and table
        self.assertEqual(whereclause.left.name, "id")
        self.assertEqual(whereclause.left.table.name, "user_sessions")
        self.assertIs(whereclause.left.table, UserSession.__table__)

        # Equality operator
        self.assertIs(whereclause.operator, operator.eq)

        # Right bound value
        self.assertIsInstance(whereclause.right, BindParameter)
        self.assertEqual(whereclause.right.value, caller_sid)

        # Compiled parameter values and absence of hidden predicates
        compiled = stmt.compile()
        self.assertEqual(len(compiled.params), 1)
        self.assertEqual(list(compiled.params.values()), [caller_sid])
        self.assertNotIn("user_id", compiled.params)
        self.assertNotIn("is_active", compiled.params)

        # Requirement 5: audit_log called exactly once after force_clear_sessions
        audit_mock.assert_called_once_with(
            "session.logout_all",
            target_type="user",
            target_id=user_id,
            actor_id=user_id,
            actor_role=UserRole.STANDARD.value,
            after_summary={"terminated_sessions": 3, "excluded_session_id": str(caller_sid)},
        )

        # Requirement 5: exact Persian response
        self.assertEqual(response, {"detail": "3 نشست پایان یافت"})


if __name__ == "__main__":
    unittest.main()
