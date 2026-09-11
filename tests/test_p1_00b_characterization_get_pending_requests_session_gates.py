"""Characterization test for get_pending_requests session gates.

Stage card: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment: /root/trading-bot/trading_bot/.local/two-site-refactor/assignments/WA-P1-00B-FREEZE-20260904-001.md
Tracked gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Surface seed: docs/refactor/two-server/section-1/inventory/surface-api.json#API-AUTH-GET-SESSIONS-GET-PENDING-REQUESTS-L619

This module provides bounded characterization only and explicitly disclaims seed promotion and Stage closure.
"""

from __future__ import annotations

import copy
import operator
import unittest
import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

FENCE = install_p1_00_network_fence()

from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import (
    BinaryExpression,
    BindParameter,
    BooleanClauseList,
    True_,
)

from api.routers.sessions import get_pending_requests
from core.config import settings
from models.session import LoginRequestStatus, SessionLoginRequest, UserSession

PINNED_UTC_NOW = datetime(2026, 9, 11, 12, 0, 0)
_UNSET = object()


class PinnedDateTime(datetime):
    @classmethod
    def utcnow(cls) -> datetime:
        return PINNED_UTC_NOW


class StableJWTDecodeError(Exception):
    """Stable exception for simulating JWT decode failure."""


class StrictFakeHeaders:
    def __init__(self, headers: dict[str, str]) -> None:
        self._headers = dict(headers)
        self.get_calls: list[tuple[str, object]] = []

    def get(self, key: str, default: object = None) -> object:
        self.get_calls.append((key, default))
        if key not in self._headers:
            return default
        return self._headers[key]

    def __getitem__(self, key: str) -> str:
        raise AssertionError(f"Unexpected direct header lookup: {key}")


class StrictFakeRequest:
    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = StrictFakeHeaders(headers)

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"Unexpected request attribute access: {name}")


class StrictFakeScalars:
    def __init__(self, items: list[object]) -> None:
        self._items = list(items)
        self._all_called = 0

    def all(self) -> list[object]:
        if self._all_called > 0:
            raise AssertionError("scalars().all() called more than once")
        self._all_called += 1
        return list(self._items)

    def first(self) -> object:
        raise AssertionError("Unexpected call to scalars().first()")

    def one(self) -> object:
        raise AssertionError("Unexpected call to scalars().one()")

    def one_or_none(self) -> object:
        raise AssertionError("Unexpected call to scalars().one_or_none()")


class StrictFakeExecuteResult:
    def __init__(
        self,
        *,
        scalar_val: object = _UNSET,
        items: list[object] | None = None,
    ) -> None:
        self._scalar_val = scalar_val
        self._items = items
        self.scalar_one_or_none_called = 0
        self.scalars_called = 0
        self.scalars_instance: StrictFakeScalars | None = None

    def scalar_one_or_none(self) -> object:
        if self._scalar_val is _UNSET:
            raise AssertionError("Unexpected call to scalar_one_or_none() on this result")
        if self.scalar_one_or_none_called > 0:
            raise AssertionError("scalar_one_or_none() called more than once")
        self.scalar_one_or_none_called += 1
        return self._scalar_val

    def scalars(self) -> StrictFakeScalars:
        if self._items is None:
            raise AssertionError("Unexpected call to scalars() on this result")
        if self.scalars_called > 0:
            raise AssertionError("result.scalars() called more than once")
        self.scalars_called += 1
        self.scalars_instance = StrictFakeScalars(self._items)
        return self.scalars_instance

    def scalar(self) -> object:
        raise AssertionError("Unexpected call to result.scalar()")

    def all(self) -> list[object]:
        raise AssertionError("Unexpected direct call to result.all() instead of result.scalars().all()")

    def first(self) -> object:
        raise AssertionError("Unexpected call to result.first()")

    def one(self) -> object:
        raise AssertionError("Unexpected call to result.one()")

    def one_or_none(self) -> object:
        raise AssertionError("Unexpected call to result.one_or_none()")


class StrictFakeAsyncDB:
    def __init__(
        self,
        queued_results: list[StrictFakeExecuteResult],
        on_execute: object = None,
    ) -> None:
        self.queued_results = list(queued_results)
        self.executed_statements: list[object] = []
        self.executed_results: list[StrictFakeExecuteResult] = []
        self._on_execute = on_execute
        self.add_count = 0
        self.delete_count = 0
        self.flush_count = 0
        self.commit_count = 0
        self.rollback_count = 0
        self.refresh_count = 0

    async def execute(self, stmt: object) -> StrictFakeExecuteResult:
        if not self.queued_results:
            raise AssertionError("Unexpected db.execute() call: no queued results remaining")
        if callable(self._on_execute):
            self._on_execute(stmt)
        self.executed_statements.append(stmt)
        res = self.queued_results.pop(0)
        self.executed_results.append(res)
        return res

    def add(self, *args: object, **kwargs: object) -> None:
        self.add_count += 1
        raise AssertionError("db.add must not be called during read-only query")

    async def delete(self, *args: object, **kwargs: object) -> None:
        self.delete_count += 1
        raise AssertionError("db.delete must not be called during read-only query")

    async def flush(self, *args: object, **kwargs: object) -> None:
        self.flush_count += 1
        raise AssertionError("db.flush must not be called during read-only query")

    async def commit(self, *args: object, **kwargs: object) -> None:
        self.commit_count += 1
        raise AssertionError("db.commit must not be called during read-only query")

    async def rollback(self, *args: object, **kwargs: object) -> None:
        self.rollback_count += 1
        raise AssertionError("db.rollback must not be called during read-only query")

    async def refresh(self, *args: object, **kwargs: object) -> None:
        self.refresh_count += 1
        raise AssertionError("db.refresh must not be called during read-only query")


class CharacterizationGetPendingRequestsSessionGatesTests(unittest.IsolatedAsyncioTestCase):
    def _assert_pending_login_requests_select(
        self, stmt: object, expected_user_id: int, expected_pinned_now: datetime
    ) -> None:
        self.assertIsInstance(stmt, Select)

        # Target entity: SessionLoginRequest only, never UserSession
        self.assertEqual(len(stmt.column_descriptions), 1)
        self.assertIs(stmt.column_descriptions[0]["entity"], SessionLoginRequest)
        self.assertIs(stmt.column_descriptions[0]["type"], SessionLoginRequest)
        self.assertIsNot(stmt.column_descriptions[0]["entity"], UserSession)

        # WHERE condition must be a BooleanClauseList formed by and_
        whereclause = stmt.whereclause
        self.assertIsNotNone(whereclause)
        self.assertIsInstance(whereclause, BooleanClauseList)
        self.assertIs(whereclause.operator, operator.and_)
        self.assertEqual(len(whereclause.clauses), 3)

        # 1. session_login_requests.user_id == current_user.id
        pred_user = whereclause.clauses[0]
        self.assertIsInstance(pred_user, BinaryExpression)
        self.assertEqual(pred_user.left.name, "user_id")
        self.assertEqual(pred_user.left.table.name, "session_login_requests")
        self.assertIs(pred_user.left.table, SessionLoginRequest.__table__)
        self.assertIs(pred_user.operator, operator.eq)
        self.assertIsInstance(pred_user.right, BindParameter)
        self.assertEqual(pred_user.right.value, expected_user_id)

        # 2. status == LoginRequestStatus.PENDING
        pred_status = whereclause.clauses[1]
        self.assertIsInstance(pred_status, BinaryExpression)
        self.assertEqual(pred_status.left.name, "status")
        self.assertEqual(pred_status.left.table.name, "session_login_requests")
        self.assertIs(pred_status.left.table, SessionLoginRequest.__table__)
        self.assertIs(pred_status.operator, operator.eq)
        self.assertIsInstance(pred_status.right, BindParameter)
        self.assertIs(pred_status.right.value, LoginRequestStatus.PENDING)

        # 3. expires_at > pinned clock
        pred_expires = whereclause.clauses[2]
        self.assertIsInstance(pred_expires, BinaryExpression)
        self.assertEqual(pred_expires.left.name, "expires_at")
        self.assertEqual(pred_expires.left.table.name, "session_login_requests")
        self.assertIs(pred_expires.left.table, SessionLoginRequest.__table__)
        self.assertIs(pred_expires.operator, operator.gt)
        self.assertIsInstance(pred_expires.right, BindParameter)
        self.assertEqual(pred_expires.right.value, expected_pinned_now)

        # By column name mapping: reject hidden or missing predicates
        pred_by_col = {c.left.name: c for c in whereclause.clauses}
        self.assertEqual(set(pred_by_col.keys()), {"user_id", "status", "expires_at"})

        # ORDER BY SessionLoginRequest.created_at descending
        self.assertEqual(len(stmt._order_by_clauses), 1)
        order_clause = stmt._order_by_clauses[0]
        self.assertEqual(order_clause.element.name, "created_at")
        self.assertIs(order_clause.element.table, SessionLoginRequest.__table__)
        self.assertTrue(str(order_clause).lower().endswith("desc"))
        self.assertIn("order by session_login_requests.created_at desc", str(stmt).lower())

        # Compiled params check
        compiled = stmt.compile()
        self.assertEqual(len(compiled.params), 3)
        param_values = list(compiled.params.values())
        self.assertIn(expected_user_id, param_values)
        self.assertIn(expected_pinned_now, param_values)
        self.assertTrue(
            LoginRequestStatus.PENDING in param_values
            or LoginRequestStatus.PENDING.value in param_values
        )

    def _assert_user_session_select(
        self, stmt: object, expected_session_id: uuid.UUID
    ) -> None:
        self.assertIsInstance(stmt, Select)

        # Target entity: UserSession only, never SessionLoginRequest
        self.assertEqual(len(stmt.column_descriptions), 1)
        self.assertIs(stmt.column_descriptions[0]["entity"], UserSession)
        self.assertIs(stmt.column_descriptions[0]["type"], UserSession)
        self.assertIsNot(stmt.column_descriptions[0]["entity"], SessionLoginRequest)

        # WHERE condition must be a BooleanClauseList formed by and_
        whereclause = stmt.whereclause
        self.assertIsNotNone(whereclause)
        self.assertIsInstance(whereclause, BooleanClauseList)
        self.assertIs(whereclause.operator, operator.and_)
        self.assertEqual(len(whereclause.clauses), 2)

        # 1. user_sessions.id == uuid.UUID(current_session_id)
        pred_id = whereclause.clauses[0]
        self.assertIsInstance(pred_id, BinaryExpression)
        self.assertEqual(pred_id.left.name, "id")
        self.assertEqual(pred_id.left.table.name, "user_sessions")
        self.assertIs(pred_id.left.table, UserSession.__table__)
        self.assertIs(pred_id.operator, operator.eq)
        self.assertIsInstance(pred_id.right, BindParameter)
        self.assertEqual(pred_id.right.value, expected_session_id)

        # 2. user_sessions.is_active == True
        pred_active = whereclause.clauses[1]
        self.assertIsInstance(pred_active, BinaryExpression)
        self.assertEqual(pred_active.left.name, "is_active")
        self.assertEqual(pred_active.left.table.name, "user_sessions")
        self.assertIs(pred_active.left.table, UserSession.__table__)
        self.assertIs(pred_active.operator, operator.eq)
        self.assertIsInstance(pred_active.right, True_)

        # By column name mapping: reject hidden or missing predicates
        pred_by_col = {c.left.name: c for c in whereclause.clauses}
        self.assertEqual(set(pred_by_col.keys()), {"id", "is_active"})

        # Compiled params check (single UUID parameter, boolean literal True_ is inlined)
        compiled = stmt.compile()
        self.assertEqual(len(compiled.params), 1)
        param_values = list(compiled.params.values())
        self.assertEqual(param_values, [expected_session_id])

    async def test_get_pending_requests_decode_failure_falls_back_to_query(self) -> None:
        denied_before = copy.copy(FENCE["denied_connects"])
        redis_before = copy.copy(FENCE["live_redis_constructs"])

        user_id = 77
        current_user = SimpleNamespace(id=user_id)
        raw_token = "synthetic-invalid-token-decode-failure-c16"
        request = StrictFakeRequest({"authorization": f"Bearer {raw_token}"})

        observed_events: list[str] = []

        async def fake_guard(db_arg: object, user_arg: object) -> None:
            observed_events.append("ensure_non_accountant_session_management")

        def fake_jwt_decode(*args: object, **kwargs: object) -> dict[str, object]:
            observed_events.append("jwt_decode")
            raise StableJWTDecodeError("JWT decoding failed due to malformed token")

        def on_db_execute(stmt: object) -> None:
            observed_events.append("db_execute")

        execute_result = StrictFakeExecuteResult(items=[])
        db = StrictFakeAsyncDB([execute_result], on_execute=on_db_execute)
        guard_mock = AsyncMock(side_effect=fake_guard)

        with (
            patch("api.routers.sessions.datetime", PinnedDateTime),
            patch(
                "api.routers.sessions.ensure_non_accountant_session_management",
                new=guard_mock,
            ),
            patch("jose.jwt.decode", side_effect=fake_jwt_decode) as decode_mock,
        ):
            result = await get_pending_requests(
                request=request,
                db=db,
                current_user=current_user,
            )

        # 1. Result is exactly an empty list
        self.assertEqual(result, [])
        self.assertIsInstance(result, list)

        # 2. JWT decode signature assertion with authoritative settings
        decode_mock.assert_called_once_with(
            raw_token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        self.assertEqual(
            decode_mock.call_args[0],
            (raw_token, settings.jwt_secret_key),
        )
        self.assertEqual(
            decode_mock.call_args[1],
            {"algorithms": [settings.jwt_algorithm]},
        )

        # 3. Guard assertions
        guard_mock.assert_awaited_once_with(db, current_user)
        self.assertIs(guard_mock.await_args[0][0], db)
        self.assertIs(guard_mock.await_args[0][1], current_user)

        # 4. Strict execution ordering: guard completes before JWT decode and before any DB execute
        self.assertEqual(
            observed_events,
            [
                "ensure_non_accountant_session_management",
                "jwt_decode",
                "db_execute",
            ],
        )
        guard_idx = observed_events.index("ensure_non_accountant_session_management")
        jwt_idx = observed_events.index("jwt_decode")
        db_idx = observed_events.index("db_execute")
        self.assertLess(guard_idx, jwt_idx)
        self.assertLess(guard_idx, db_idx)
        self.assertLess(jwt_idx, db_idx)

        # 5. DB statement execution and result consumption:
        # Exactly one execute call, no UserSession SELECT, exactly one SessionLoginRequest SELECT
        self.assertEqual(len(db.executed_statements), 1)
        self.assertEqual(len(db.queued_results), 0)
        self.assertEqual(execute_result.scalars_called, 1)
        self.assertIsNotNone(execute_result.scalars_instance)
        self.assertEqual(execute_result.scalars_instance._all_called, 1)
        self.assertEqual(execute_result.scalar_one_or_none_called, 0)

        # 6. Verify SessionLoginRequest statement
        self._assert_pending_login_requests_select(
            db.executed_statements[0], user_id, PINNED_UTC_NOW
        )

        # 7. Strict header double assertion
        self.assertEqual(
            request.headers.get_calls,
            [("authorization", "")],
        )

        # 8. Zero mutations and network fence unchanged
        self.assertEqual(db.add_count, 0)
        self.assertEqual(db.delete_count, 0)
        self.assertEqual(db.flush_count, 0)
        self.assertEqual(db.commit_count, 0)
        self.assertEqual(db.rollback_count, 0)
        self.assertEqual(db.refresh_count, 0)
        self.assertEqual(FENCE["denied_connects"], denied_before)
        self.assertEqual(FENCE["live_redis_constructs"], redis_before)

    async def test_get_pending_requests_missing_caller_session_short_circuits(self) -> None:
        denied_before = copy.copy(FENCE["denied_connects"])
        redis_before = copy.copy(FENCE["live_redis_constructs"])

        user_id = 88
        current_user = SimpleNamespace(id=user_id)
        caller_session_id = uuid.uuid4()
        sid_str = str(caller_session_id)
        raw_token = "synthetic-valid-token-missing-session-c16"
        request = StrictFakeRequest({"authorization": f"Bearer {raw_token}"})

        observed_events: list[str] = []

        async def fake_guard(db_arg: object, user_arg: object) -> None:
            observed_events.append("ensure_non_accountant_session_management")

        def fake_jwt_decode(*args: object, **kwargs: object) -> dict[str, object]:
            observed_events.append("jwt_decode")
            return {"sid": sid_str}

        def on_db_execute(stmt: object) -> None:
            observed_events.append("db_execute")

        execute_result = StrictFakeExecuteResult(scalar_val=None)
        db = StrictFakeAsyncDB([execute_result], on_execute=on_db_execute)
        guard_mock = AsyncMock(side_effect=fake_guard)

        with (
            patch(
                "api.routers.sessions.ensure_non_accountant_session_management",
                new=guard_mock,
            ),
            patch("jose.jwt.decode", side_effect=fake_jwt_decode) as decode_mock,
        ):
            result = await get_pending_requests(
                request=request,
                db=db,
                current_user=current_user,
            )

        # 1. Result is exactly an empty list
        self.assertEqual(result, [])
        self.assertIsInstance(result, list)

        # 2. JWT decode signature assertion with authoritative settings
        decode_mock.assert_called_once_with(
            raw_token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        self.assertEqual(
            decode_mock.call_args[0],
            (raw_token, settings.jwt_secret_key),
        )
        self.assertEqual(
            decode_mock.call_args[1],
            {"algorithms": [settings.jwt_algorithm]},
        )

        # 3. Guard assertions
        guard_mock.assert_awaited_once_with(db, current_user)
        self.assertIs(guard_mock.await_args[0][0], db)
        self.assertIs(guard_mock.await_args[0][1], current_user)

        # 4. Strict execution ordering: guard completes before JWT decode and before any DB execute
        self.assertEqual(
            observed_events,
            [
                "ensure_non_accountant_session_management",
                "jwt_decode",
                "db_execute",
            ],
        )
        guard_idx = observed_events.index("ensure_non_accountant_session_management")
        jwt_idx = observed_events.index("jwt_decode")
        db_idx = observed_events.index("db_execute")
        self.assertLess(guard_idx, jwt_idx)
        self.assertLess(guard_idx, db_idx)
        self.assertLess(jwt_idx, db_idx)

        # 5. DB statement execution and short-circuit:
        # Exactly one execute call (UserSession SELECT), zero SessionLoginRequest SELECTs
        self.assertEqual(len(db.executed_statements), 1)
        self.assertEqual(len(db.queued_results), 0)
        self.assertEqual(execute_result.scalar_one_or_none_called, 1)
        self.assertEqual(execute_result.scalars_called, 0)

        # 6. Verify UserSession statement
        self._assert_user_session_select(db.executed_statements[0], caller_session_id)

        # 7. Strict header double assertion
        self.assertEqual(
            request.headers.get_calls,
            [("authorization", "")],
        )

        # 8. Zero mutations and network fence unchanged
        self.assertEqual(db.add_count, 0)
        self.assertEqual(db.delete_count, 0)
        self.assertEqual(db.flush_count, 0)
        self.assertEqual(db.commit_count, 0)
        self.assertEqual(db.rollback_count, 0)
        self.assertEqual(db.refresh_count, 0)
        self.assertEqual(FENCE["denied_connects"], denied_before)
        self.assertEqual(FENCE["live_redis_constructs"], redis_before)


if __name__ == "__main__":
    unittest.main()
