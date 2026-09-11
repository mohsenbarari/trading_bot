"""Hermetic characterization tests for api.routers.sessions.terminate_session.

Stage card: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment: /root/trading-bot/trading_bot/.local/two-site-refactor/assignments/WA-P1-00B-FREEZE-20260904-001.md
Surface behavior ID: docs/refactor/two-server/section-1/inventory/surface-api.json#API-AUTH-DELETE-SESSIONS-TERMINATE-SESSION-L1075
Seed-gap reference: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

This module provides bounded characterization evidence only. It does NOT claim seed
promotion, P1-00b Stage closure, or completion of any other stage.
"""

from __future__ import annotations

import operator
import unittest
from datetime import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch
import uuid

from fastapi import HTTPException
from jose import jwt as jose_jwt

# Network fence must be installed before importing api.routers.sessions
from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.sessions import terminate_session  # noqa: E402
from core.config import settings  # noqa: E402
from models.session import Platform, UserSession  # noqa: E402
from models.user import User, UserRole  # noqa: E402


class FakeExecuteResult:
    """Mock scalar result wrapper for SQLAlchemy execute statements."""

    def __init__(self, value: Any) -> None:
        self._value = value

    def scalar_one_or_none(self) -> Any:
        return self._value


class RecordingAsyncDB:
    """Async database stub that records executed statements and returns queued results."""

    def __init__(
        self,
        execute_results: list[Any] | None = None,
        on_execute: Any | None = None,
    ) -> None:
        self.execute_results = list(execute_results or [])
        self.executed_statements: list[Any] = []
        self.on_execute = on_execute

    async def execute(self, stmt: Any) -> Any:
        self.executed_statements.append(stmt)
        if self.on_execute is not None:
            self.on_execute(stmt)
        if not self.execute_results:
            raise AssertionError(f"Unexpected execute() call with statement: {stmt}")
        return self.execute_results.pop(0)


def make_request(token: str | None = None) -> SimpleNamespace:
    """Construct a minimal request with headers."""
    headers: dict[str, str] = {}
    if token is not None:
        headers["authorization"] = f"Bearer {token}"
    return SimpleNamespace(headers=headers)


def assert_user_session_query(
    test_case: unittest.TestCase,
    stmt: Any,
    expected_session_id: uuid.UUID,
    expected_user_id: int,
) -> None:
    """Independently verify SQLAlchemy statement meaning for target UserSession query.

    Asserts:
    1. Entity queried is UserSession.
    2. WHERE criteria has exactly 3 equality clauses for id, user_id, and is_active.
    3. Target session id matches expected_session_id.
    4. User id matches expected_user_id.
    5. is_active represents SQL True constant.
    6. Compiled parameters contain the expected target bound values (id and user_id).
    """
    entities = [
        desc.get("entity") or desc.get("type")
        for desc in stmt.column_descriptions
    ]
    test_case.assertIn(UserSession, entities)

    whereclause = stmt.whereclause
    test_case.assertIsNotNone(whereclause)
    clauses = getattr(whereclause, "clauses", [whereclause])
    test_case.assertEqual(len(clauses), 3)

    found_id = None
    found_user_id = None
    found_is_active_true = False

    for clause in clauses:
        left = getattr(clause, "left", None)
        right = getattr(clause, "right", None)
        col_name = getattr(left, "name", None)
        test_case.assertIsNotNone(col_name)
        op = getattr(clause, "operator", None)
        test_case.assertTrue(
            op == operator.eq or getattr(op, "__name__", "") in ("eq", "is_"),
            f"Expected equality operator on {col_name}, got {op}",
        )
        if col_name == "id":
            found_id = getattr(right, "value", None)
        elif col_name == "user_id":
            found_user_id = getattr(right, "value", None)
        elif col_name == "is_active":
            is_true_constant = (
                right.__class__.__name__ == "True_"
                or str(right).strip().lower() in ("true", "true()")
                or getattr(right, "name", None) == "true"
                or getattr(right, "value", None) is True
            )
            test_case.assertTrue(
                is_true_constant,
                f"Expected True SQL constant for is_active, got {right!r} (type: {type(right)})",
            )
            found_is_active_true = is_true_constant

    test_case.assertEqual(found_id, expected_session_id)
    test_case.assertEqual(found_user_id, expected_user_id)
    test_case.assertTrue(found_is_active_true)

    compiled_params = stmt.compile().params
    test_case.assertIn(expected_session_id, compiled_params.values())
    test_case.assertIn(expected_user_id, compiled_params.values())


class CharacterizeTerminateSessionTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic characterization tests for terminate_session under P1-00b."""

    def setUp(self) -> None:
        if not getattr(settings, "jwt_secret_key", None):
            settings.jwt_secret_key = "characterization-test-secret-key"

    async def test_terminate_session_current_non_primary_success(self) -> None:
        """Requirement 2: Caller terminates own non-primary session with valid token.

        Verifies:
        - Accountant guard completes first.
        - Exactly one target query is executed for that UUID, current user, and active session.
        - No caller-session or active-session lookup occurs.
        - logout_session is awaited with exact db and target session.
        - audit_log follows logout with action session.terminate and exact args.
        - Response is {"detail": "نشست با موفقیت پایان یافت"}.
        """
        user_id = 42
        target_id = uuid.uuid4()

        user = User(
            id=user_id,
            account_name="test_standard_user",
            mobile_number="+989123456789",
            full_name="Standard Test User",
            address="Tehran, District 3",
            role=UserRole.STANDARD,
            is_deleted=False,
            has_bot_access=True,
        )

        target_session = UserSession(
            id=target_id,
            user_id=user_id,
            device_name="Chrome on Linux",
            device_ip="192.168.1.100",
            home_server="foreign",
            platform=Platform.WEB,
            refresh_token_hash="hash_standard_session",
            is_primary=False,
            is_active=True,
            created_at=datetime(2026, 1, 1, 10, 0, 0),
            last_active_at=datetime(2026, 1, 1, 12, 0, 0),
        )

        token = jose_jwt.encode(
            {"sid": str(target_id)},
            settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )
        request = make_request(token=token)

        events: list[tuple[str, Any]] = []

        async def tracked_guard(d: Any, u: Any) -> None:
            events.append(("guard", d, u))

        def on_execute(stmt: Any) -> None:
            events.append(("db.execute", stmt))

        db = RecordingAsyncDB(
            execute_results=[FakeExecuteResult(target_session)],
            on_execute=on_execute,
        )

        async def tracked_logout(d: Any, s: Any) -> None:
            events.append(("logout_session", d, s))
            return None

        def tracked_audit(action: str, **kwargs: Any) -> None:
            events.append(("audit_log", action, kwargs))

        async def tracked_get_active(d: Any, u_id: int) -> list[Any]:
            events.append(("get_active_sessions", d, u_id))
            return [target_session]

        with patch(
            "api.routers.sessions.ensure_non_accountant_session_management",
            side_effect=tracked_guard,
        ) as guard_mock, patch(
            "api.routers.sessions.logout_session",
            side_effect=tracked_logout,
        ) as logout_mock, patch(
            "api.routers.sessions.audit_log",
            side_effect=tracked_audit,
        ) as audit_mock, patch(
            "api.routers.sessions.get_active_sessions",
            side_effect=tracked_get_active,
        ) as get_active_mock:
            response = await terminate_session(
                session_id=str(target_id),
                request=request,
                db=db,
                current_user=user,
            )

        # 1. Exact response text
        self.assertEqual(response, {"detail": "نشست با موفقیت پایان یافت"})

        # 2. Strict call ordering: guard -> target query -> logout -> audit
        self.assertEqual(
            [e[0] for e in events],
            ["guard", "db.execute", "logout_session", "audit_log"],
        )

        # 3. Guard assertions
        guard_mock.assert_awaited_once_with(db, user)
        self.assertIs(events[0][1], db)
        self.assertIs(events[0][2], user)

        # 4. Target query assertions: exactly one query, correct criteria
        self.assertEqual(len(db.executed_statements), 1)
        assert_user_session_query(self, db.executed_statements[0], target_id, user_id)

        # 5. Skipped boundaries: no caller query, no active sessions lookup
        get_active_mock.assert_not_called()

        # 6. Logout assertions: awaited with exact db and session instance
        logout_mock.assert_awaited_once_with(db, target_session)
        self.assertIs(events[2][1], db)
        self.assertIs(events[2][2], target_session)

        # 7. Audit assertions: action, target, actor, role, after_summary
        audit_mock.assert_called_once_with(
            "session.terminate",
            target_type="session",
            target_id=target_id,
            actor_id=user_id,
            actor_role="عادی",
            after_summary={"target_user_id": user_id, "is_current": True},
        )

    async def test_terminate_session_malformed_session_id_rejection(self) -> None:
        """Requirement 3: Rejection of malformed session_id with HTTP 400.

        Verifies:
        - Guard is awaited before the exception is raised.
        - Exact HTTP 400 detail "شناسه نشست نامعتبر است".
        - No db execute, JWT-dependent session lookup, get_active_sessions,
          logout_session, or audit_log boundary is reached.
        """
        user_id = 42
        malformed_id = "malformed-not-a-uuid-999"

        user = User(
            id=user_id,
            account_name="test_standard_user",
            mobile_number="+989123456789",
            full_name="Standard Test User",
            address="Tehran, District 3",
            role=UserRole.STANDARD,
            is_deleted=False,
            has_bot_access=True,
        )

        token = jose_jwt.encode(
            {"sid": str(uuid.uuid4())},
            settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )
        request = make_request(token=token)

        events: list[tuple[str, Any]] = []

        async def tracked_guard(d: Any, u: Any) -> None:
            events.append(("guard", d, u))

        def on_execute(stmt: Any) -> None:
            events.append(("db.execute", stmt))

        db = RecordingAsyncDB(on_execute=on_execute)

        async def tracked_logout(d: Any, s: Any) -> None:
            events.append(("logout_session", d, s))

        def tracked_audit(action: str, **kwargs: Any) -> None:
            events.append(("audit_log", action, kwargs))

        async def tracked_get_active(d: Any, u_id: int) -> list[Any]:
            events.append(("get_active_sessions", d, u_id))
            return []

        with patch(
            "api.routers.sessions.ensure_non_accountant_session_management",
            side_effect=tracked_guard,
        ) as guard_mock, patch(
            "api.routers.sessions.logout_session",
            side_effect=tracked_logout,
        ) as logout_mock, patch(
            "api.routers.sessions.audit_log",
            side_effect=tracked_audit,
        ) as audit_mock, patch(
            "api.routers.sessions.get_active_sessions",
            side_effect=tracked_get_active,
        ) as get_active_mock:
            with self.assertRaises(HTTPException) as exc_ctx:
                await terminate_session(
                    session_id=malformed_id,
                    request=request,
                    db=db,
                    current_user=user,
                )

        # 1. Exact HTTP 400 detail
        self.assertEqual(exc_ctx.exception.status_code, 400)
        self.assertEqual(exc_ctx.exception.detail, "شناسه نشست نامعتبر است")

        # 2. Guard completed before exception
        guard_mock.assert_awaited_once_with(db, user)
        self.assertEqual([e[0] for e in events], ["guard"])

        # 3. No downstream database or service boundaries reached
        self.assertEqual(len(db.executed_statements), 0)
        get_active_mock.assert_not_called()
        logout_mock.assert_not_called()
        audit_mock.assert_not_called()

    async def test_terminate_session_missing_current_sid_refusal(self) -> None:
        """Requirement 4: Existing target with missing current sid raises HTTP 403.

        Verifies:
        - Guard and target query occur before HTTP 403 detail "شناسه نشست شما مشخص نیست".
        - No caller-session query, get_active_sessions, logout_session, or audit_log occurs.
        - Tested with both missing authorization header and authorization header without sid.
        """
        user_id = 42
        target_id = uuid.uuid4()

        user = User(
            id=user_id,
            account_name="test_standard_user",
            mobile_number="+989123456789",
            full_name="Standard Test User",
            address="Tehran, District 3",
            role=UserRole.STANDARD,
            is_deleted=False,
            has_bot_access=True,
        )

        target_session = UserSession(
            id=target_id,
            user_id=user_id,
            device_name="Target Device",
            device_ip="10.0.0.5",
            home_server="foreign",
            platform=Platform.WEB,
            refresh_token_hash="hash_target",
            is_primary=False,
            is_active=True,
            created_at=datetime(2026, 1, 1, 10, 0, 0),
            last_active_at=datetime(2026, 1, 1, 11, 0, 0),
        )

        for case_label, req in [
            ("no_auth_header", make_request(token=None)),
            (
                "token_without_sid",
                make_request(
                    token=jose_jwt.encode(
                        {},
                        settings.jwt_secret_key,
                        algorithm=settings.jwt_algorithm,
                    )
                ),
            ),
        ]:
            with self.subTest(case=case_label):
                events: list[tuple[str, Any]] = []

                async def tracked_guard(d: Any, u: Any) -> None:
                    events.append(("guard", d, u))

                def on_execute(stmt: Any) -> None:
                    events.append(("db.execute", stmt))

                db = RecordingAsyncDB(
                    execute_results=[FakeExecuteResult(target_session)],
                    on_execute=on_execute,
                )

                async def tracked_logout(d: Any, s: Any) -> None:
                    events.append(("logout_session", d, s))

                def tracked_audit(action: str, **kwargs: Any) -> None:
                    events.append(("audit_log", action, kwargs))

                async def tracked_get_active(d: Any, u_id: int) -> list[Any]:
                    events.append(("get_active_sessions", d, u_id))
                    return [target_session]

                with patch(
                    "api.routers.sessions.ensure_non_accountant_session_management",
                    side_effect=tracked_guard,
                ) as guard_mock, patch(
                    "api.routers.sessions.logout_session",
                    side_effect=tracked_logout,
                ) as logout_mock, patch(
                    "api.routers.sessions.audit_log",
                    side_effect=tracked_audit,
                ) as audit_mock, patch(
                    "api.routers.sessions.get_active_sessions",
                    side_effect=tracked_get_active,
                ) as get_active_mock:
                    with self.assertRaises(HTTPException) as exc_ctx:
                        await terminate_session(
                            session_id=str(target_id),
                            request=req,
                            db=db,
                            current_user=user,
                        )

                # 1. Exact HTTP 403 detail
                self.assertEqual(exc_ctx.exception.status_code, 403)
                self.assertEqual(
                    exc_ctx.exception.detail,
                    "شناسه نشست شما مشخص نیست",
                )

                # 2. Observable call ordering: guard followed by target query
                self.assertEqual(
                    [e[0] for e in events],
                    ["guard", "db.execute"],
                )

                # 3. Guard assertions
                guard_mock.assert_awaited_once_with(db, user)

                # 4. Exactly one target query executed with correct criteria
                self.assertEqual(len(db.executed_statements), 1)
                assert_user_session_query(
                    self,
                    db.executed_statements[0],
                    target_id,
                    user_id,
                )

                # 5. Skipped boundaries: no caller lookup, get_active, logout, or audit
                get_active_mock.assert_not_called()
                logout_mock.assert_not_called()
                audit_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
