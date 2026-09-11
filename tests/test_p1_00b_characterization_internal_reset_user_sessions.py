"""Characterization test for internal_reset_user_sessions handler contract.

Stage card: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment: /root/trading-bot/trading_bot/.local/two-site-refactor/assignments/WA-P1-00B-FREEZE-20260904-001.md
Surface seed: docs/refactor/two-server/section-1/inventory/surface-api.json#API-AUTH-POST-SESSIONS-INTERNAL-RESET-USER-SESSIONS-L562
Gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

This module provides bounded characterization only and explicitly disclaims seed promotion and Stage closure.
"""

from __future__ import annotations

import operator
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

FENCE = install_p1_00_network_fence()

from fastapi import HTTPException
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import BinaryExpression, BindParameter

from api.routers.sessions import (
    InternalSessionResetRequest,
    internal_reset_user_sessions,
)
from models.user import User, UserRole


class StrictFakeHeaders:
    def __init__(self, headers: dict[str, str]) -> None:
        self._headers = dict(headers)
        self.accessed_keys: list[str] = []

    def get(self, key: str, default: str | None = None) -> str | None:
        self.accessed_keys.append(key)
        return self._headers.get(key, default)

    def __getitem__(self, key: str) -> str:
        return self._headers[key]


class StrictFakeRequest:
    def __init__(self, body_bytes: bytes, headers: dict[str, str]) -> None:
        self._body_bytes = body_bytes
        self.headers = StrictFakeHeaders(headers)
        self.body_calls = 0

    async def body(self) -> bytes:
        if self.body_calls > 0:
            raise AssertionError("request.body() called more than once")
        self.body_calls += 1
        return self._body_bytes


class StrictFakeExecuteResult:
    def __init__(self, value: object = None) -> None:
        self._value = value
        self.scalar_one_or_none_calls = 0

    def scalar_one_or_none(self) -> object:
        if self.scalar_one_or_none_calls > 0:
            raise AssertionError(
                "scalar_one_or_none called more than once on execute result"
            )
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
        raise AssertionError(
            "db.delete must not be called during read-only operation"
        )

    async def flush(self, *args: object, **kwargs: object) -> None:
        self.flush_count += 1
        raise AssertionError(
            "db.flush must not be called during read-only operation"
        )

    async def commit(self, *args: object, **kwargs: object) -> None:
        self.commit_count += 1
        raise AssertionError(
            "db.commit must not be called during read-only operation"
        )

    async def rollback(self, *args: object, **kwargs: object) -> None:
        self.rollback_count += 1
        raise AssertionError(
            "db.rollback must not be called during read-only operation"
        )

    async def refresh(self, *args: object, **kwargs: object) -> None:
        self.refresh_count += 1
        raise AssertionError(
            "db.refresh must not be called during read-only operation"
        )

    def assert_no_unconsumed_results(self) -> None:
        if self._queued_results:
            raise AssertionError(
                f"{len(self._queued_results)} queued execute result(s) remained silently unused"
            )


class InternalResetUserSessionsCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    def _assert_user_select(self, stmt: object, expected_user_id: int) -> None:
        self.assertIsInstance(stmt, Select)

        # Entity check: targeting only User
        self.assertEqual(len(stmt.column_descriptions), 1)
        self.assertIs(stmt.column_descriptions[0]["entity"], User)
        self.assertIs(stmt.column_descriptions[0]["type"], User)

        # WHERE condition must be User.id == payload.user_id
        whereclause = stmt.whereclause
        self.assertIsNotNone(whereclause)
        self.assertIsInstance(whereclause, BinaryExpression)
        self.assertEqual(whereclause.left.name, "id")
        self.assertEqual(whereclause.left.table.name, "users")
        self.assertIs(whereclause.left.table, User.__table__)
        self.assertIs(whereclause.operator, operator.eq)
        self.assertIsInstance(whereclause.right, BindParameter)
        self.assertEqual(whereclause.right.value, expected_user_id)

        # Compiled params check: single bound parameter id
        compiled = stmt.compile()
        self.assertEqual(len(compiled.params), 1)
        param_values = list(compiled.params.values())
        self.assertIn(expected_user_id, param_values)

    def _assert_readonly_db_state(self, db: StrictFakeAsyncDB) -> None:
        self.assertEqual(db.add_count, 0)
        self.assertEqual(db.delete_count, 0)
        self.assertEqual(db.flush_count, 0)
        self.assertEqual(db.commit_count, 0)
        self.assertEqual(db.rollback_count, 0)
        self.assertEqual(db.refresh_count, 0)
        db.assert_no_unconsumed_results()

    async def test_internal_reset_user_sessions_invalid_signature(self) -> None:
        denied_before = len(FENCE["denied_connects"])
        live_before = len(FENCE["live_redis_constructs"])

        raw_body = b'{"user_id": 42, "mobile_number": "09121111111", "source_server": "test-source-invalid", "sentinel": "char-sig-invalid-401"}'
        timestamp_val = "1726056789"
        signature_val = "characterization-fake-signature-invalid-401"
        api_key_val = "characterization-fake-api-key-test-abc"
        headers = {
            "X-Timestamp": timestamp_val,
            "X-Signature": signature_val,
            "X-API-Key": api_key_val,
        }
        request = StrictFakeRequest(body_bytes=raw_body, headers=headers)
        payload = InternalSessionResetRequest(
            user_id=42,
            mobile_number="09121111111",
            source_server="test-source-invalid",
        )
        db = StrictFakeAsyncDB(results=[])

        verify_mock = MagicMock(return_value=False)
        reset_mock = AsyncMock()

        with (
            patch(
                "api.routers.sessions.verify_session_authority_signature",
                new=verify_mock,
            ),
            patch(
                "api.routers.sessions.reset_user_session_state",
                new=reset_mock,
            ),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await internal_reset_user_sessions(
                    payload=payload,
                    request=request,
                    db=db,
                )

        self.assertEqual(exc_info.exception.status_code, 401)
        self.assertEqual(exc_info.exception.detail, "Invalid internal signature")
        self.assertEqual(request.body_calls, 1)
        self.assertEqual(
            request.headers.accessed_keys,
            ["X-Timestamp", "X-Signature", "X-API-Key"],
        )
        verify_mock.assert_called_once_with(
            raw_body,
            timestamp=timestamp_val,
            signature=signature_val,
            api_key=api_key_val,
        )
        self.assertEqual(len(db.executed_statements), 0)
        reset_mock.assert_not_called()
        self._assert_readonly_db_state(db)

        self.assertEqual(len(FENCE["denied_connects"]), denied_before)
        self.assertEqual(len(FENCE["live_redis_constructs"]), live_before)

    async def test_internal_reset_user_sessions_valid_signature_missing_user(
        self,
    ) -> None:
        denied_before = len(FENCE["denied_connects"])
        live_before = len(FENCE["live_redis_constructs"])

        user_id = 1001
        mobile_number = "09122222222"
        raw_body = b'{"user_id": 1001, "mobile_number": "09122222222", "source_server": "source-alpha-missing", "sentinel": "char-sig-valid-404"}'
        timestamp_val = "1726056790"
        signature_val = "characterization-fake-signature-valid-404"
        api_key_val = "characterization-fake-api-key-test-xyz"
        headers = {
            "X-Timestamp": timestamp_val,
            "X-Signature": signature_val,
            "X-API-Key": api_key_val,
        }
        request = StrictFakeRequest(body_bytes=raw_body, headers=headers)
        payload = InternalSessionResetRequest(
            user_id=user_id,
            mobile_number=mobile_number,
            source_server="source-alpha-missing",
        )

        result_holder = StrictFakeExecuteResult(value=None)
        db = StrictFakeAsyncDB(results=[result_holder])

        verify_mock = MagicMock(return_value=True)
        reset_mock = AsyncMock()

        with (
            patch(
                "api.routers.sessions.verify_session_authority_signature",
                new=verify_mock,
            ),
            patch(
                "api.routers.sessions.reset_user_session_state",
                new=reset_mock,
            ),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await internal_reset_user_sessions(
                    payload=payload,
                    request=request,
                    db=db,
                )

        self.assertEqual(exc_info.exception.status_code, 404)
        self.assertEqual(exc_info.exception.detail, "User not found")
        self.assertEqual(request.body_calls, 1)
        self.assertEqual(
            request.headers.accessed_keys,
            ["X-Timestamp", "X-Signature", "X-API-Key"],
        )
        verify_mock.assert_called_once_with(
            raw_body,
            timestamp=timestamp_val,
            signature=signature_val,
            api_key=api_key_val,
        )
        self.assertEqual(len(db.executed_statements), 1)
        self._assert_user_select(
            db.executed_statements[0], expected_user_id=user_id
        )
        self.assertEqual(len(db.executed_results), 1)
        self.assertEqual(result_holder.scalar_one_or_none_calls, 1)
        reset_mock.assert_not_called()
        self._assert_readonly_db_state(db)

        self.assertEqual(len(FENCE["denied_connects"]), denied_before)
        self.assertEqual(len(FENCE["live_redis_constructs"]), live_before)

    async def test_internal_reset_user_sessions_valid_signature_mobile_mismatch(
        self,
    ) -> None:
        denied_before = len(FENCE["denied_connects"])
        live_before = len(FENCE["live_redis_constructs"])

        user_id = 2002
        payload_mobile = "09123333333"
        user_mobile = "09124444444"
        raw_body = b'{"user_id": 2002, "mobile_number": "09123333333", "source_server": "source-beta-mismatch", "sentinel": "char-sig-valid-409"}'
        timestamp_val = "1726056791"
        signature_val = "characterization-fake-signature-valid-409"
        api_key_val = "characterization-fake-api-key-test-mismatch"
        headers = {
            "X-Timestamp": timestamp_val,
            "X-Signature": signature_val,
            "X-API-Key": api_key_val,
        }
        request = StrictFakeRequest(body_bytes=raw_body, headers=headers)
        payload = InternalSessionResetRequest(
            user_id=user_id,
            mobile_number=payload_mobile,
            source_server="source-beta-mismatch",
        )

        user = User(
            id=user_id,
            account_name="char_user_2002",
            mobile_number=user_mobile,
            full_name="Characterization User 2002",
            address="Characterization Street 2002",
            home_server="foreign",
            role=UserRole.STANDARD,
        )
        result_holder = StrictFakeExecuteResult(value=user)
        db = StrictFakeAsyncDB(results=[result_holder])

        verify_mock = MagicMock(return_value=True)
        reset_mock = AsyncMock()

        with (
            patch(
                "api.routers.sessions.verify_session_authority_signature",
                new=verify_mock,
            ),
            patch(
                "api.routers.sessions.reset_user_session_state",
                new=reset_mock,
            ),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await internal_reset_user_sessions(
                    payload=payload,
                    request=request,
                    db=db,
                )

        self.assertEqual(exc_info.exception.status_code, 409)
        self.assertEqual(exc_info.exception.detail, "User identity mismatch")
        self.assertEqual(request.body_calls, 1)
        self.assertEqual(
            request.headers.accessed_keys,
            ["X-Timestamp", "X-Signature", "X-API-Key"],
        )
        verify_mock.assert_called_once_with(
            raw_body,
            timestamp=timestamp_val,
            signature=signature_val,
            api_key=api_key_val,
        )
        self.assertEqual(len(db.executed_statements), 1)
        self._assert_user_select(
            db.executed_statements[0], expected_user_id=user_id
        )
        self.assertEqual(len(db.executed_results), 1)
        self.assertEqual(result_holder.scalar_one_or_none_calls, 1)
        reset_mock.assert_not_called()
        self._assert_readonly_db_state(db)

        self.assertEqual(len(FENCE["denied_connects"]), denied_before)
        self.assertEqual(len(FENCE["live_redis_constructs"]), live_before)


if __name__ == "__main__":
    unittest.main()
