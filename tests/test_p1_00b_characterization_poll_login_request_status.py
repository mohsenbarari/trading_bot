"""Characterization test for poll_login_request_status contract.

Stage card: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment: /root/trading-bot/trading_bot/.local/two-site-refactor/assignments/WA-P1-00B-FREEZE-20260904-001.md
Surface seed: docs/refactor/two-server/section-1/inventory/surface-api.json#API-AUTH-GET-SESSIONS-POLL-LOGIN-REQUEST-STATUS-L1311
Gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

This module provides bounded characterization only and explicitly disclaims seed promotion and Stage closure.
"""

from __future__ import annotations

import operator
import unittest
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from fastapi import HTTPException
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import BinaryExpression, BindParameter

from api.routers.sessions import poll_login_request_status
from models.session import LoginRequestStatus, SessionLoginRequest

EXPECTED_NOT_FOUND_DETAIL = "درخواست یافت نشد"
FIXED_NOW = datetime(2026, 9, 11, 12, 0, 0)


class FixedDatetime(datetime):
    @classmethod
    def utcnow(cls) -> datetime:
        return FIXED_NOW

    @classmethod
    def now(cls, tz: object = None) -> datetime:
        return FIXED_NOW


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


class PollLoginRequestStatusContractCharacterizationTests(unittest.IsolatedAsyncioTestCase):
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

    def _assert_login_request_select(
        self, stmt: object, expected_request_id: uuid.UUID
    ) -> None:
        self.assertIsInstance(stmt, Select)

        # Entity check
        self.assertEqual(len(stmt.column_descriptions), 1)
        self.assertIs(stmt.column_descriptions[0]["entity"], SessionLoginRequest)
        self.assertIs(stmt.column_descriptions[0]["type"], SessionLoginRequest)

        # WHERE condition must be SessionLoginRequest.id == parsed_uuid
        whereclause = stmt.whereclause
        self.assertIsNotNone(whereclause)
        self.assertIsInstance(whereclause, BinaryExpression)
        self.assertEqual(whereclause.left.name, "id")
        self.assertEqual(whereclause.left.table.name, "session_login_requests")
        self.assertIs(whereclause.left.table, SessionLoginRequest.__table__)
        self.assertIs(whereclause.operator, operator.eq)
        self.assertIsInstance(whereclause.right, BindParameter)
        self.assertEqual(whereclause.right.value, expected_request_id)

        # Compiled params check: single bound parameter id
        compiled = stmt.compile()
        self.assertEqual(len(compiled.params), 1)
        param_values = list(compiled.params.values())
        self.assertIn(expected_request_id, param_values)

    def _assert_readonly_db_state(self, db: StrictFakeAsyncDB) -> None:
        self.assertEqual(db.add_count, 0)
        self.assertEqual(db.delete_count, 0)
        self.assertEqual(db.flush_count, 0)
        self.assertEqual(db.commit_count, 0)
        self.assertEqual(db.rollback_count, 0)
        self.assertEqual(db.refresh_count, 0)
        db.assert_no_unconsumed_results()

    async def test_poll_login_request_status_missing_request(self) -> None:
        request_id_uuid = uuid.UUID("11111111-1111-1111-1111-111111111111")
        request_id_str = str(request_id_uuid)

        db = StrictFakeAsyncDB(
            results=[StrictFakeExecuteResult(value=None)]
        )
        create_access_token_mock = MagicMock()
        pop_token_mock = AsyncMock()

        with (
            patch("api.routers.sessions.datetime", FixedDatetime),
            patch("api.routers.sessions.create_access_token", new=create_access_token_mock),
            patch("api.routers.sessions._pop_temporary_refresh_token", new=pop_token_mock),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await poll_login_request_status(
                    request_id=request_id_str,
                    db=db,
                )

        # Exact HTTP status and Persian message
        self.assertEqual(exc_info.exception.status_code, 404)
        self.assertEqual(exc_info.exception.detail, EXPECTED_NOT_FOUND_DETAIL)

        # Exactly one execute on SessionLoginRequest and one scalar_one_or_none consumption
        self.assertEqual(len(db.executed_statements), 1)
        self._assert_login_request_select(db.executed_statements[0], request_id_uuid)
        self.assertEqual(len(db.executed_results), 1)
        self.assertEqual(db.executed_results[0].scalar_one_or_none_calls, 1)

        # Token generation and Redis refresh pop remain untouched
        create_access_token_mock.assert_not_called()
        pop_token_mock.assert_not_called()

        # Enforce read-only invariants and no unconsumed results
        self._assert_readonly_db_state(db)

    async def test_poll_login_request_status_pending_at_exact_expiration_boundary(self) -> None:
        request_id_uuid = uuid.UUID("22222222-2222-2222-2222-222222222222")
        request_id_str = str(request_id_uuid)

        login_req = SessionLoginRequest(
            id=request_id_uuid,
            user_id=42,
            requester_device_name="Galaxy S24",
            requester_ip="192.168.1.50",
            requester_home_server="foreign",
            status=LoginRequestStatus.PENDING,
            created_at=datetime(2026, 9, 11, 11, 55, 0),
            expires_at=FIXED_NOW,
            resolved_by_session_id=None,
        )
        login_req_snapshot = self._snapshot_login_request(login_req)

        db = StrictFakeAsyncDB(
            results=[StrictFakeExecuteResult(value=login_req)]
        )
        create_access_token_mock = MagicMock()
        pop_token_mock = AsyncMock()

        with (
            patch("api.routers.sessions.datetime", FixedDatetime),
            patch("api.routers.sessions.create_access_token", new=create_access_token_mock),
            patch("api.routers.sessions._pop_temporary_refresh_token", new=pop_token_mock),
        ):
            response = await poll_login_request_status(
                request_id=request_id_str,
                db=db,
            )

        # Exact boundary response: expires_at == utcnow is not strictly earlier, so status remains pending
        self.assertEqual(response, {"status": "pending"})

        # Request entity remains unchanged
        self.assertEqual(self._snapshot_login_request(login_req), login_req_snapshot)
        self.assertIs(login_req.status, LoginRequestStatus.PENDING)
        self.assertEqual(login_req.expires_at, FIXED_NOW)

        # Exactly one execute on SessionLoginRequest and no second UserSession query
        self.assertEqual(len(db.executed_statements), 1)
        self._assert_login_request_select(db.executed_statements[0], request_id_uuid)
        self.assertEqual(len(db.executed_results), 1)
        self.assertEqual(db.executed_results[0].scalar_one_or_none_calls, 1)

        # Token generation and Redis refresh pop remain untouched
        create_access_token_mock.assert_not_called()
        pop_token_mock.assert_not_called()

        # Enforce read-only invariants and no unconsumed results
        self._assert_readonly_db_state(db)

    async def test_poll_login_request_status_pending_expired_minimally_before_fixed_clock(self) -> None:
        request_id_uuid = uuid.UUID("33333333-3333-3333-3333-333333333333")
        request_id_str = str(request_id_uuid)
        minimal_delta = timedelta(microseconds=1)
        expired_at = FIXED_NOW - minimal_delta

        login_req = SessionLoginRequest(
            id=request_id_uuid,
            user_id=42,
            requester_device_name="Galaxy S24",
            requester_ip="192.168.1.50",
            requester_home_server="foreign",
            status=LoginRequestStatus.PENDING,
            created_at=datetime(2026, 9, 11, 11, 55, 0),
            expires_at=expired_at,
            resolved_by_session_id=None,
        )
        login_req_snapshot = self._snapshot_login_request(login_req)

        db = StrictFakeAsyncDB(
            results=[StrictFakeExecuteResult(value=login_req)]
        )
        create_access_token_mock = MagicMock()
        pop_token_mock = AsyncMock()

        with (
            patch("api.routers.sessions.datetime", FixedDatetime),
            patch("api.routers.sessions.create_access_token", new=create_access_token_mock),
            patch("api.routers.sessions._pop_temporary_refresh_token", new=pop_token_mock),
        ):
            response = await poll_login_request_status(
                request_id=request_id_str,
                db=db,
            )

        # Minimally earlier expiration returns expired status
        self.assertEqual(response, {"status": "expired"})

        # Request entity remains unchanged
        self.assertEqual(self._snapshot_login_request(login_req), login_req_snapshot)
        self.assertIs(login_req.status, LoginRequestStatus.PENDING)
        self.assertEqual(login_req.expires_at, expired_at)

        # Exactly one execute on SessionLoginRequest and no second UserSession query
        self.assertEqual(len(db.executed_statements), 1)
        self._assert_login_request_select(db.executed_statements[0], request_id_uuid)
        self.assertEqual(len(db.executed_results), 1)
        self.assertEqual(db.executed_results[0].scalar_one_or_none_calls, 1)

        # Token generation and Redis refresh pop remain untouched
        create_access_token_mock.assert_not_called()
        pop_token_mock.assert_not_called()

        # Enforce read-only invariants and no unconsumed results
        self._assert_readonly_db_state(db)


if __name__ == "__main__":
    unittest.main()
