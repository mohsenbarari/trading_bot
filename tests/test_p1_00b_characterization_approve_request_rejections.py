"""Characterization test for approve_request rejections.

Stage card: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment: /root/trading-bot/trading_bot/.local/two-site-refactor/assignments/WA-P1-00B-FREEZE-20260904-001.md
Surface seed: docs/refactor/two-server/section-1/inventory/surface-api.json#API-AUTH-POST-SESSIONS-APPROVE-REQUEST-L1210
Gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

This module provides bounded characterization only and explicitly disclaims seed promotion and Stage closure.
"""

from __future__ import annotations

import operator
import unittest
import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from fastapi import HTTPException
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import BinaryExpression, BindParameter, BooleanClauseList, True_

from api.routers.sessions import approve_request
from models.session import LoginRequestStatus, Platform, SessionLoginRequest, UserSession
from models.user import User, UserRole

EXPECTED_MISSING_PRIMARY_DETAIL = "فقط از نشست اصلی مجاز به تایید هستید"
EXPECTED_MISSING_OR_FOREIGN_REQUEST_DETAIL = "درخواست یافت نشد"


class StrictFakeExecuteResult:
    def __init__(self, value: object = None) -> None:
        self._value = value
        self.scalar_one_or_none_calls = 0

    def scalar_one_or_none(self) -> object:
        if self.scalar_one_or_none_calls > 0:
            raise AssertionError("scalar_one_or_none called more than once on execute result")
        self.scalar_one_or_none_calls += 1
        return self._value

    def scalar(self) -> object:
        raise AssertionError("Unexpected call to result.scalar()")

    def scalars(self) -> object:
        raise AssertionError("Unexpected call to result.scalars()")

    def all(self) -> object:
        raise AssertionError("Unexpected direct call to result.all()")

    def first(self) -> object:
        raise AssertionError("Unexpected call to result.first()")

    def one(self) -> object:
        raise AssertionError("Unexpected call to result.one()")

    def one_or_none(self) -> object:
        raise AssertionError("Unexpected call to result.one_or_none()")


class StrictFakeAsyncDB:
    def __init__(
        self,
        results: list[StrictFakeExecuteResult] | None = None,
        on_execute: object = None,
    ) -> None:
        self._queued_results: list[StrictFakeExecuteResult] = list(results or [])
        self.executed_results: list[StrictFakeExecuteResult] = []
        self.executed_statements: list[object] = []
        self._on_execute = on_execute
        self.add_count = 0
        self.delete_count = 0
        self.flush_count = 0
        self.commit_count = 0
        self.rollback_count = 0
        self.refresh_count = 0

    async def execute(self, stmt: object) -> StrictFakeExecuteResult:
        if not self._queued_results:
            raise AssertionError(
                f"Unexpected db.execute() call; no queued results remaining. Statement: {stmt}"
            )
        if callable(self._on_execute):
            self._on_execute(stmt)
        res = self._queued_results.pop(0)
        self.executed_statements.append(stmt)
        self.executed_results.append(res)
        return res

    def add(self, *args: object, **kwargs: object) -> None:
        self.add_count += 1
        raise AssertionError("db.add must not be called during read-only operation")

    async def delete(self, *args: object, **kwargs: object) -> None:
        self.delete_count += 1
        raise AssertionError("db.delete must not be called during read-only operation")

    async def flush(self, *args: object, **kwargs: object) -> None:
        self.flush_count += 1
        raise AssertionError("db.flush must not be called during read-only operation")

    async def commit(self, *args: object, **kwargs: object) -> None:
        self.commit_count += 1
        raise AssertionError("db.commit must not be called during read-only operation")

    async def rollback(self, *args: object, **kwargs: object) -> None:
        self.rollback_count += 1
        raise AssertionError("db.rollback must not be called during read-only operation")

    async def refresh(self, *args: object, **kwargs: object) -> None:
        self.refresh_count += 1
        raise AssertionError("db.refresh must not be called during read-only operation")

    def assert_no_unconsumed_results(self) -> None:
        if self._queued_results:
            raise AssertionError(
                f"{len(self._queued_results)} queued execute result(s) remained silently unused"
            )


class ApproveRequestRejectionsCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def _snapshot_session(self, s: UserSession) -> dict[str, object]:
        return {
            "id": s.id,
            "user_id": s.user_id,
            "device_name": s.device_name,
            "device_ip": s.device_ip,
            "home_server": s.home_server,
            "platform": s.platform,
            "refresh_token_hash": s.refresh_token_hash,
            "is_primary": s.is_primary,
            "is_active": s.is_active,
            "created_at": s.created_at,
            "last_active_at": s.last_active_at,
            "expires_at": s.expires_at,
        }

    def _snapshot_login_request(self, req: SessionLoginRequest) -> dict[str, object]:
        return {
            "id": req.id,
            "user_id": req.user_id,
            "requester_device_name": req.requester_device_name,
            "requester_ip": req.requester_ip,
            "requester_home_server": req.requester_home_server,
            "status": req.status,
            "created_at": req.created_at,
            "expires_at": req.expires_at,
            "resolved_by_session_id": req.resolved_by_session_id,
        }

    def _assert_primary_user_session_select(
        self, stmt: object, expected_user_id: int
    ) -> None:
        self.assertIsInstance(stmt, Select)

        # Entity check
        self.assertEqual(len(stmt.column_descriptions), 1)
        self.assertIs(stmt.column_descriptions[0]["entity"], UserSession)
        self.assertIs(stmt.column_descriptions[0]["type"], UserSession)

        # WHERE condition must be a BooleanClauseList formed by and_
        whereclause = stmt.whereclause
        self.assertIsNotNone(whereclause)
        self.assertIsInstance(whereclause, BooleanClauseList)
        self.assertIs(whereclause.operator, operator.and_)
        self.assertEqual(len(whereclause.clauses), 3)

        # 1. UserSession.user_id == current_user.id
        pred_user = whereclause.clauses[0]
        self.assertIsInstance(pred_user, BinaryExpression)
        self.assertEqual(pred_user.left.name, "user_id")
        self.assertEqual(pred_user.left.table.name, "user_sessions")
        self.assertIs(pred_user.left.table, UserSession.__table__)
        self.assertIs(pred_user.operator, operator.eq)
        self.assertIsInstance(pred_user.right, BindParameter)
        self.assertEqual(pred_user.right.value, expected_user_id)

        # 2. UserSession.is_primary == True
        pred_primary = whereclause.clauses[1]
        self.assertIsInstance(pred_primary, BinaryExpression)
        self.assertEqual(pred_primary.left.name, "is_primary")
        self.assertEqual(pred_primary.left.table.name, "user_sessions")
        self.assertIs(pred_primary.left.table, UserSession.__table__)
        self.assertIs(pred_primary.operator, operator.eq)
        self.assertIsInstance(pred_primary.right, True_)
        self.assertEqual(type(pred_primary.right).__name__, "True_")

        # 3. UserSession.is_active == True
        pred_active = whereclause.clauses[2]
        self.assertIsInstance(pred_active, BinaryExpression)
        self.assertEqual(pred_active.left.name, "is_active")
        self.assertEqual(pred_active.left.table.name, "user_sessions")
        self.assertIs(pred_active.left.table, UserSession.__table__)
        self.assertIs(pred_active.operator, operator.eq)
        self.assertIsInstance(pred_active.right, True_)
        self.assertEqual(type(pred_active.right).__name__, "True_")

        # Handler order check
        self.assertEqual(
            [c.left.name for c in whereclause.clauses],
            ["user_id", "is_primary", "is_active"],
        )

        pred_by_col = {c.left.name: c for c in whereclause.clauses}
        self.assertEqual(set(pred_by_col.keys()), {"user_id", "is_primary", "is_active"})

        # Compiled params check: single bound parameter user_id
        compiled = stmt.compile()
        self.assertEqual(len(compiled.params), 1)
        param_values = list(compiled.params.values())
        self.assertIn(expected_user_id, param_values)

    def _assert_session_login_request_select(
        self, stmt: object, expected_request_id: uuid.UUID
    ) -> None:
        self.assertIsInstance(stmt, Select)

        # Entity check
        self.assertEqual(len(stmt.column_descriptions), 1)
        self.assertIs(stmt.column_descriptions[0]["entity"], SessionLoginRequest)
        self.assertIs(stmt.column_descriptions[0]["type"], SessionLoginRequest)

        # WHERE condition must be a single BinaryExpression: SessionLoginRequest.id == rid
        whereclause = stmt.whereclause
        self.assertIsNotNone(whereclause)
        self.assertIsInstance(whereclause, BinaryExpression)
        self.assertEqual(whereclause.left.name, "id")
        self.assertEqual(whereclause.left.table.name, "session_login_requests")
        self.assertIs(whereclause.left.table, SessionLoginRequest.__table__)
        self.assertIs(whereclause.operator, operator.eq)
        self.assertIsInstance(whereclause.right, BindParameter)
        self.assertEqual(whereclause.right.value, expected_request_id)

        # Compiled params check
        compiled = stmt.compile()
        self.assertEqual(len(compiled.params), 1)
        self.assertIn(expected_request_id, compiled.params.values())

    async def test_approve_request_rejects_missing_primary_session(self) -> None:
        caller_user_id = 42
        current_user = User(
            id=caller_user_id,
            account_name="primary_caller_42",
            mobile_number="+989120000042",
            role=UserRole.STANDARD,
            full_name="Caller Forty-Two",
            address="Tehran",
        )
        current_user.is_accountant = False

        request_id_uuid = uuid.UUID("11111111-1111-1111-1111-111111111111")
        request_id_str = str(request_id_uuid)
        request_obj = SimpleNamespace(
            client=SimpleNamespace(host="192.168.1.100"),
            headers={},
        )

        observed_events: list[str] = []

        async def fake_guard(db_arg: object, user_arg: object) -> None:
            observed_events.append("ensure_non_accountant_session_management")

        def on_db_execute(stmt: object) -> None:
            observed_events.append("execute_primary_select")

        db = StrictFakeAsyncDB(
            results=[StrictFakeExecuteResult(value=None)],
            on_execute=on_db_execute,
        )
        guard_mock = AsyncMock(side_effect=fake_guard)

        with (
            patch(
                "api.routers.sessions.ensure_non_accountant_session_management",
                new=guard_mock,
            ),
            patch("api.routers.sessions.create_refresh_token") as mock_create_token,
            patch(
                "api.routers.sessions.approve_login_request",
                new_callable=AsyncMock,
            ) as mock_approve,
            patch(
                "api.routers.sessions._store_temporary_refresh_token",
                new_callable=AsyncMock,
            ) as mock_store,
            patch("api.routers.sessions.audit_log") as mock_audit,
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await approve_request(
                    request_id=request_id_str,
                    request=request_obj,
                    db=db,
                    current_user=current_user,
                )

        # Exact status code and Persian detail
        self.assertEqual(exc_info.exception.status_code, 403)
        self.assertEqual(
            exc_info.exception.detail,
            EXPECTED_MISSING_PRIMARY_DETAIL,
        )

        # Ordering: accountant guard completes before execute
        self.assertEqual(
            observed_events,
            ["ensure_non_accountant_session_management", "execute_primary_select"],
        )
        guard_mock.assert_awaited_once_with(db, current_user)
        self.assertIs(guard_mock.await_args[0][0], db)
        self.assertIs(guard_mock.await_args[0][1], current_user)

        # Exactly one UserSession SELECT, no login-request SELECT
        self.assertEqual(len(db.executed_statements), 1)
        self._assert_primary_user_session_select(
            db.executed_statements[0], caller_user_id
        )

        # Single scalar_one_or_none consumption
        self.assertEqual(len(db.executed_results), 1)
        self.assertEqual(db.executed_results[0].scalar_one_or_none_calls, 1)

        # Downstream operations remain untouched
        mock_create_token.assert_not_called()
        mock_approve.assert_not_called()
        mock_store.assert_not_called()
        mock_audit.assert_not_called()

        # Mutation counts and unconsumed result assertions
        self.assertEqual(db.add_count, 0)
        self.assertEqual(db.delete_count, 0)
        self.assertEqual(db.flush_count, 0)
        self.assertEqual(db.commit_count, 0)
        self.assertEqual(db.rollback_count, 0)
        self.assertEqual(db.refresh_count, 0)
        db.assert_no_unconsumed_results()

    async def test_approve_request_rejects_missing_login_request(self) -> None:
        caller_user_id = 42
        current_user = User(
            id=caller_user_id,
            account_name="primary_caller_42",
            mobile_number="+989120000042",
            role=UserRole.STANDARD,
            full_name="Caller Forty-Two",
            address="Tehran",
        )
        current_user.is_accountant = False

        primary_session_id = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        primary_session = UserSession(
            id=primary_session_id,
            user_id=caller_user_id,
            device_name="Primary Desktop",
            device_ip="192.168.1.10",
            home_server="foreign",
            platform=Platform.WEB,
            refresh_token_hash="primary_token_hash",
            is_primary=True,
            is_active=True,
            created_at=datetime(2026, 9, 11, 8, 0, 0),
            last_active_at=datetime(2026, 9, 11, 12, 0, 0),
            expires_at=datetime(2026, 10, 11, 12, 0, 0),
        )
        primary_session_snapshot = self._snapshot_session(primary_session)

        request_id_uuid = uuid.UUID("22222222-2222-2222-2222-222222222222")
        request_id_str = str(request_id_uuid)
        request_obj = SimpleNamespace(
            client=SimpleNamespace(host="192.168.1.100"),
            headers={},
        )

        observed_events: list[str] = []

        async def fake_guard(db_arg: object, user_arg: object) -> None:
            observed_events.append("ensure_non_accountant_session_management")

        def on_db_execute(stmt: object) -> None:
            if hasattr(stmt, "column_descriptions") and stmt.column_descriptions:
                entity = stmt.column_descriptions[0]["entity"]
                if entity is UserSession:
                    observed_events.append("execute_primary_select")
                    return
                if entity is SessionLoginRequest:
                    observed_events.append("execute_login_request_select")
                    return
            observed_events.append("execute_unknown")

        db = StrictFakeAsyncDB(
            results=[
                StrictFakeExecuteResult(value=primary_session),
                StrictFakeExecuteResult(value=None),
            ],
            on_execute=on_db_execute,
        )
        guard_mock = AsyncMock(side_effect=fake_guard)

        with (
            patch(
                "api.routers.sessions.ensure_non_accountant_session_management",
                new=guard_mock,
            ),
            patch("api.routers.sessions.create_refresh_token") as mock_create_token,
            patch(
                "api.routers.sessions.approve_login_request",
                new_callable=AsyncMock,
            ) as mock_approve,
            patch(
                "api.routers.sessions._store_temporary_refresh_token",
                new_callable=AsyncMock,
            ) as mock_store,
            patch("api.routers.sessions.audit_log") as mock_audit,
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await approve_request(
                    request_id=request_id_str,
                    request=request_obj,
                    db=db,
                    current_user=current_user,
                )

        # Exact status code and Persian detail
        self.assertEqual(exc_info.exception.status_code, 404)
        self.assertEqual(
            exc_info.exception.detail,
            EXPECTED_MISSING_OR_FOREIGN_REQUEST_DETAIL,
        )

        # Strict ordering: guard, primary select, then login request select
        self.assertEqual(
            observed_events,
            [
                "ensure_non_accountant_session_management",
                "execute_primary_select",
                "execute_login_request_select",
            ],
        )
        guard_mock.assert_awaited_once_with(db, current_user)
        self.assertIs(guard_mock.await_args[0][0], db)
        self.assertIs(guard_mock.await_args[0][1], current_user)

        # Statement verification
        self.assertEqual(len(db.executed_statements), 2)
        self._assert_primary_user_session_select(
            db.executed_statements[0], caller_user_id
        )
        self._assert_session_login_request_select(
            db.executed_statements[1], request_id_uuid
        )

        # One scalar_one_or_none consumption per result
        self.assertEqual(len(db.executed_results), 2)
        self.assertEqual(db.executed_results[0].scalar_one_or_none_calls, 1)
        self.assertEqual(db.executed_results[1].scalar_one_or_none_calls, 1)

        # Downstream operations remain untouched
        mock_create_token.assert_not_called()
        mock_approve.assert_not_called()
        mock_store.assert_not_called()
        mock_audit.assert_not_called()

        # Mutation counts, unconsumed result assertions, and entity immutability
        self.assertEqual(db.add_count, 0)
        self.assertEqual(db.delete_count, 0)
        self.assertEqual(db.flush_count, 0)
        self.assertEqual(db.commit_count, 0)
        self.assertEqual(db.rollback_count, 0)
        self.assertEqual(db.refresh_count, 0)
        db.assert_no_unconsumed_results()
        self.assertEqual(self._snapshot_session(primary_session), primary_session_snapshot)

    async def test_approve_request_rejects_foreign_account_login_request(self) -> None:
        caller_user_id = 42
        foreign_user_id = 99

        current_user = User(
            id=caller_user_id,
            account_name="primary_caller_42",
            mobile_number="+989120000042",
            role=UserRole.STANDARD,
            full_name="Caller Forty-Two",
            address="Tehran",
        )
        current_user.is_accountant = False

        primary_session_id = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
        primary_session = UserSession(
            id=primary_session_id,
            user_id=caller_user_id,
            device_name="Primary Desktop",
            device_ip="192.168.1.10",
            home_server="foreign",
            platform=Platform.WEB,
            refresh_token_hash="primary_token_hash",
            is_primary=True,
            is_active=True,
            created_at=datetime(2026, 9, 11, 8, 0, 0),
            last_active_at=datetime(2026, 9, 11, 12, 0, 0),
            expires_at=datetime(2026, 10, 11, 12, 0, 0),
        )
        primary_session_snapshot = self._snapshot_session(primary_session)

        request_id_uuid = uuid.UUID("33333333-3333-3333-3333-333333333333")
        request_id_str = str(request_id_uuid)
        foreign_login_request = SessionLoginRequest(
            id=request_id_uuid,
            user_id=foreign_user_id,
            requester_device_name="Foreign Phone",
            requester_ip="10.0.0.5",
            requester_home_server="iran",
            status=LoginRequestStatus.PENDING,
            created_at=datetime(2026, 9, 11, 11, 0, 0),
            expires_at=datetime(2026, 9, 11, 12, 30, 0),
            resolved_by_session_id=None,
        )
        foreign_login_request_snapshot = self._snapshot_login_request(foreign_login_request)

        request_obj = SimpleNamespace(
            client=SimpleNamespace(host="192.168.1.100"),
            headers={},
        )

        observed_events: list[str] = []

        async def fake_guard(db_arg: object, user_arg: object) -> None:
            observed_events.append("ensure_non_accountant_session_management")

        def on_db_execute(stmt: object) -> None:
            if hasattr(stmt, "column_descriptions") and stmt.column_descriptions:
                entity = stmt.column_descriptions[0]["entity"]
                if entity is UserSession:
                    observed_events.append("execute_primary_select")
                    return
                if entity is SessionLoginRequest:
                    observed_events.append("execute_login_request_select")
                    return
            observed_events.append("execute_unknown")

        db = StrictFakeAsyncDB(
            results=[
                StrictFakeExecuteResult(value=primary_session),
                StrictFakeExecuteResult(value=foreign_login_request),
            ],
            on_execute=on_db_execute,
        )
        guard_mock = AsyncMock(side_effect=fake_guard)

        with (
            patch(
                "api.routers.sessions.ensure_non_accountant_session_management",
                new=guard_mock,
            ),
            patch("api.routers.sessions.create_refresh_token") as mock_create_token,
            patch(
                "api.routers.sessions.approve_login_request",
                new_callable=AsyncMock,
            ) as mock_approve,
            patch(
                "api.routers.sessions._store_temporary_refresh_token",
                new_callable=AsyncMock,
            ) as mock_store,
            patch("api.routers.sessions.audit_log") as mock_audit,
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await approve_request(
                    request_id=request_id_str,
                    request=request_obj,
                    db=db,
                    current_user=current_user,
                )

        # Exact status code and Persian detail
        self.assertEqual(exc_info.exception.status_code, 404)
        self.assertEqual(
            exc_info.exception.detail,
            EXPECTED_MISSING_OR_FOREIGN_REQUEST_DETAIL,
        )

        # Same-response non-disclosure contract: identical status code and detail to missing request
        self.assertEqual(
            (exc_info.exception.status_code, exc_info.exception.detail),
            (404, EXPECTED_MISSING_OR_FOREIGN_REQUEST_DETAIL),
        )

        # Strict ordering: guard, primary select, then login request select
        self.assertEqual(
            observed_events,
            [
                "ensure_non_accountant_session_management",
                "execute_primary_select",
                "execute_login_request_select",
            ],
        )
        guard_mock.assert_awaited_once_with(db, current_user)
        self.assertIs(guard_mock.await_args[0][0], db)
        self.assertIs(guard_mock.await_args[0][1], current_user)

        # Statement verification: same two exact SELECT contracts
        self.assertEqual(len(db.executed_statements), 2)
        self._assert_primary_user_session_select(
            db.executed_statements[0], caller_user_id
        )
        self._assert_session_login_request_select(
            db.executed_statements[1], request_id_uuid
        )

        # One scalar_one_or_none consumption per result
        self.assertEqual(len(db.executed_results), 2)
        self.assertEqual(db.executed_results[0].scalar_one_or_none_calls, 1)
        self.assertEqual(db.executed_results[1].scalar_one_or_none_calls, 1)

        # Downstream operations remain untouched
        mock_create_token.assert_not_called()
        mock_approve.assert_not_called()
        mock_store.assert_not_called()
        mock_audit.assert_not_called()

        # Mutation counts, unconsumed result assertions, and entity immutability
        self.assertEqual(db.add_count, 0)
        self.assertEqual(db.delete_count, 0)
        self.assertEqual(db.flush_count, 0)
        self.assertEqual(db.commit_count, 0)
        self.assertEqual(db.rollback_count, 0)
        self.assertEqual(db.refresh_count, 0)
        db.assert_no_unconsumed_results()
        self.assertEqual(self._snapshot_session(primary_session), primary_session_snapshot)
        self.assertEqual(
            self._snapshot_login_request(foreign_login_request),
            foreign_login_request_snapshot,
        )


if __name__ == "__main__":
    unittest.main()
