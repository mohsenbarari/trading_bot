"""Characterization test for get_pending_login_requests.

Stage card: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment: /root/trading-bot/trading_bot/.local/two-site-refactor/assignments/WA-P1-00B-FREEZE-20260904-001.md
Surface seed: docs/refactor/two-server/section-1/inventory/surface-api.json#API-AUTH-GET-SESSIONS-GET-PENDING-LOGIN-REQUESTS-L1191
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

from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import BinaryExpression, BindParameter, BooleanClauseList

from api.routers.sessions import get_pending_login_requests
from models.session import LoginRequestStatus, SessionLoginRequest

PINNED_UTC_NOW = datetime(2026, 9, 11, 12, 0, 0)


class PinnedDateTime(datetime):
    @classmethod
    def utcnow(cls) -> datetime:
        return PINNED_UTC_NOW


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
    def __init__(self, items: list[object]) -> None:
        self._items = items
        self._scalars_called = 0
        self._scalars_instance: StrictFakeScalars | None = None

    def scalars(self) -> StrictFakeScalars:
        if self._scalars_called > 0:
            raise AssertionError("result.scalars() called more than once")
        self._scalars_called += 1
        self._scalars_instance = StrictFakeScalars(self._items)
        return self._scalars_instance

    def scalar_one_or_none(self) -> object:
        raise AssertionError("Unexpected call to result.scalar_one_or_none()")

    def scalar(self) -> object:
        raise AssertionError("Unexpected call to result.scalar()")

    def all(self) -> list[object]:
        raise AssertionError("Unexpected direct call to result.all() instead of result.scalars().all()")

    def first(self) -> object:
        raise AssertionError("Unexpected call to result.first()")


class StrictFakeAsyncDB:
    def __init__(self, items: list[object], on_execute: object = None) -> None:
        self._items = items
        self._on_execute = on_execute
        self.executed_statements: list[object] = []
        self.result: StrictFakeExecuteResult | None = None
        self.add_count = 0
        self.delete_count = 0
        self.flush_count = 0
        self.commit_count = 0
        self.rollback_count = 0

    async def execute(self, stmt: object) -> StrictFakeExecuteResult:
        if len(self.executed_statements) >= 1:
            raise AssertionError("db.execute called more than once")
        if callable(self._on_execute):
            self._on_execute()
        self.executed_statements.append(stmt)
        self.result = StrictFakeExecuteResult(self._items)
        return self.result

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


class CharacterizationPendingLoginRequestsTests(unittest.IsolatedAsyncioTestCase):
    def _snapshot_request(self, req: SessionLoginRequest) -> dict[str, object]:
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

    def _assert_pending_login_requests_select(
        self, stmt: object, expected_user_id: int, expected_pinned_now: datetime
    ) -> None:
        self.assertIsInstance(stmt, Select)

        # Statement entity
        self.assertEqual(len(stmt.column_descriptions), 1)
        self.assertIs(stmt.column_descriptions[0]["entity"], SessionLoginRequest)
        self.assertIs(stmt.column_descriptions[0]["type"], SessionLoginRequest)

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

    async def test_get_pending_login_requests_empty_result(self) -> None:
        user_id = 42
        current_user = SimpleNamespace(id=user_id)
        observed_events: list[str] = []

        async def fake_guard(db_arg: object, user_arg: object) -> None:
            observed_events.append("ensure_non_accountant_session_management")

        def on_db_execute() -> None:
            observed_events.append("db_execute")

        db = StrictFakeAsyncDB(items=[], on_execute=on_db_execute)
        guard_mock = AsyncMock(side_effect=fake_guard)

        with (
            patch("api.routers.sessions.datetime", PinnedDateTime),
            patch("api.routers.sessions.ensure_non_accountant_session_management", new=guard_mock),
        ):
            result = await get_pending_login_requests(db=db, current_user=current_user)

        self.assertEqual(result, [])
        self.assertIsInstance(result, list)

        # Strict ordering: guard completes before execute
        self.assertEqual(
            observed_events,
            ["ensure_non_accountant_session_management", "db_execute"],
        )
        guard_mock.assert_awaited_once_with(db, current_user)
        self.assertIs(guard_mock.await_args[0][0], db)
        self.assertIs(guard_mock.await_args[0][1], current_user)

        # Result consumption
        self.assertEqual(len(db.executed_statements), 1)
        self.assertIsNotNone(db.result)
        self.assertEqual(db.result._scalars_called, 1)
        self.assertIsNotNone(db.result._scalars_instance)
        self.assertEqual(db.result._scalars_instance._all_called, 1)

        # Predicate inspection
        self._assert_pending_login_requests_select(
            db.executed_statements[0], user_id, PINNED_UTC_NOW
        )

        # Mutation counts
        self.assertEqual(db.add_count, 0)
        self.assertEqual(db.delete_count, 0)
        self.assertEqual(db.flush_count, 0)
        self.assertEqual(db.commit_count, 0)
        self.assertEqual(db.rollback_count, 0)

    async def test_get_pending_login_requests_populated_result(self) -> None:
        user_id = 42
        current_user = SimpleNamespace(id=user_id)

        req1_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
        req2_id = uuid.UUID("22222222-2222-2222-2222-222222222222")

        req1 = SessionLoginRequest(
            id=req1_id,
            user_id=user_id,
            requester_device_name="Desktop Firefox",
            requester_ip="192.168.1.100",
            requester_home_server="foreign",
            status=LoginRequestStatus.PENDING,
            created_at=datetime(2026, 9, 11, 10, 0, 0),
            expires_at=datetime(2026, 9, 11, 12, 30, 0),
            resolved_by_session_id=None,
        )
        req2 = SessionLoginRequest(
            id=req2_id,
            user_id=user_id,
            requester_device_name="Mobile Safari",
            requester_ip=None,
            requester_home_server="iran",
            status=LoginRequestStatus.PENDING,
            created_at=datetime(2026, 9, 11, 11, 15, 0),
            expires_at=datetime(2026, 9, 11, 12, 45, 0),
            resolved_by_session_id=None,
        )

        req1_snapshot = self._snapshot_request(req1)
        req2_snapshot = self._snapshot_request(req2)

        observed_events: list[str] = []

        async def fake_guard(db_arg: object, user_arg: object) -> None:
            observed_events.append("ensure_non_accountant_session_management")

        def on_db_execute() -> None:
            observed_events.append("db_execute")

        db = StrictFakeAsyncDB(items=[req1, req2], on_execute=on_db_execute)
        guard_mock = AsyncMock(side_effect=fake_guard)

        with (
            patch("api.routers.sessions.datetime", PinnedDateTime),
            patch("api.routers.sessions.ensure_non_accountant_session_management", new=guard_mock),
        ):
            result = await get_pending_login_requests(db=db, current_user=current_user)

        expected = [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "requester_device_name": "Desktop Firefox",
                "requester_ip": "192.168.1.100",
                "status": "pending",
                "created_at": "2026-09-11T10:00:00",
                "expires_at": "2026-09-11T12:30:00",
            },
            {
                "id": "22222222-2222-2222-2222-222222222222",
                "requester_device_name": "Mobile Safari",
                "requester_ip": None,
                "status": "pending",
                "created_at": "2026-09-11T11:15:00",
                "expires_at": "2026-09-11T12:45:00",
            },
        ]
        self.assertEqual(result, expected)

        # Strict ordering: guard completes before execute
        self.assertEqual(
            observed_events,
            ["ensure_non_accountant_session_management", "db_execute"],
        )
        guard_mock.assert_awaited_once_with(db, current_user)
        self.assertIs(guard_mock.await_args[0][0], db)
        self.assertIs(guard_mock.await_args[0][1], current_user)

        # Result consumption
        self.assertEqual(len(db.executed_statements), 1)
        self.assertIsNotNone(db.result)
        self.assertEqual(db.result._scalars_called, 1)
        self.assertIsNotNone(db.result._scalars_instance)
        self.assertEqual(db.result._scalars_instance._all_called, 1)

        # Predicate inspection
        self._assert_pending_login_requests_select(
            db.executed_statements[0], user_id, PINNED_UTC_NOW
        )

        # Mutation counts and field snapshot assertions
        self.assertEqual(db.add_count, 0)
        self.assertEqual(db.delete_count, 0)
        self.assertEqual(db.flush_count, 0)
        self.assertEqual(db.commit_count, 0)
        self.assertEqual(db.rollback_count, 0)
        self.assertEqual(self._snapshot_request(req1), req1_snapshot)
        self.assertEqual(self._snapshot_request(req2), req2_snapshot)


if __name__ == "__main__":
    unittest.main()
