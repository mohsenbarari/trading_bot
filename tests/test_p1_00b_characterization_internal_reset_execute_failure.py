"""Bounded characterization test for internal_reset_user_sessions ORM execute failure.

Stage: P1-00b (docs/refactor/two-server/section-1/stages/P1-00b.md)
Assignment: WA-P1-00B-BUSINESS-LIVE-20261023-799B
Parent assignment: WA-P1-00B-FREEZE-20260904-001
Canonical blocker: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Surface seed: docs/refactor/two-server/section-1/inventory/surface-api.json#API-AUTH-POST-SESSIONS-INTERNAL-RESET-USER-SESSIONS-L562
Scenario: internal_reset_execute_exception_before_reset

Evidence limits and explicit exclusions:
- Characterizes only the pre-driver ORM execute exception path in
  api/routers/sessions.py:internal_reset_user_sessions after a valid synthetic
  signature check and before result consumption or session reset side effects.
- Excludes actual HMAC/timestamp/API-key verification internals inside
  verify_session_authority_signature.
- Excludes FastAPI HTTP routing and Depends(get_db) wiring.
- Excludes success/reset/revoke/persistence execution in reset_user_session_state,
  force_clear_sessions, deactivate_active_sessions, get_active_sessions,
  publish_session_revocation, blacklist_session, collect_login_limit_keys, and
  redis_delete_keys.
- Excludes post-execute scalar_one_or_none failures, 401 invalid-signature,
  404 missing-user, and 409 mobile-mismatch outcomes.
- Excludes retry, process-reload, concurrency, and origin-matrix interactions.
- Claims no whole-handler, seed, origin-cell, HIGH blocker, or Stage (P1-00b) closure.
"""

from __future__ import annotations
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

from contextlib import ExitStack
import operator
from typing import Any
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import create_engine, event
from sqlalchemy.dialects import sqlite
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import ORMExecuteState, Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import BinaryExpression, BindParameter

from api.routers.sessions import (
    InternalSessionResetRequest,
    internal_reset_user_sessions,
)
from models.user import User
from tests.test_p1_00b_characterization_real_async_session_helper import (
    real_async_session,
)

FENCE = install_p1_00_network_fence()


class _RecordingHeaders:
    def __init__(self, headers: dict[str, str], events: list[str]) -> None:
        self._headers = dict(headers)
        self._events = events
        self.get_calls: list[tuple[str, str | None]] = []

    def get(self, key: str, default: str | None = None) -> str | None:
        self.get_calls.append((key, default))
        self._events.append(f"header:{key}")
        return self._headers.get(key, default)


class _RecordingRequest:
    def __init__(
        self,
        body_bytes: bytes,
        headers: dict[str, str],
        events: list[str],
    ) -> None:
        self._body_bytes = body_bytes
        self._events = events
        self.headers = _RecordingHeaders(headers, events)
        self.body_call_count = 0
        self.body_await_count = 0

    def body(self) -> Any:
        self.body_call_count += 1
        self._events.append("body_call")
        if self.body_call_count > 1:
            raise AssertionError("request.body() called more than once")

        async def _await_body() -> bytes:
            self.body_await_count += 1
            self._events.append("body_await")
            if self.body_await_count > 1:
                raise AssertionError("request.body() awaited more than once")
            return self._body_bytes

        return _await_body()


class InternalResetExecuteFailureCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    def _assert_user_select_statement(
        self,
        stmt: object,
        expected_user_id: int,
    ) -> None:
        self.assertIsInstance(stmt, Select)
        assert isinstance(stmt, Select)

        self.assertEqual(len(stmt.column_descriptions), 1)
        self.assertIs(stmt.column_descriptions[0]["entity"], User)
        self.assertIs(stmt.column_descriptions[0]["type"], User)

        final_froms = list(stmt.get_final_froms())
        self.assertEqual(len(final_froms), 1)
        self.assertIs(final_froms[0], User.__table__)

        whereclause = stmt.whereclause
        self.assertIsNotNone(whereclause)
        self.assertIsInstance(whereclause, BinaryExpression)
        assert isinstance(whereclause, BinaryExpression)
        self.assertIs(whereclause.operator, operator.eq)
        self.assertEqual(whereclause.left.name, "id")
        self.assertEqual(whereclause.left.table.name, "users")
        self.assertIs(whereclause.left.table, User.__table__)
        self.assertIsInstance(whereclause.right, BindParameter)
        assert isinstance(whereclause.right, BindParameter)
        self.assertEqual(whereclause.right.value, expected_user_id)

        compiled = stmt.compile(dialect=sqlite.dialect(paramstyle="named"))
        compiled_sql = str(compiled)
        self.assertIn("FROM users", compiled_sql)
        self.assertIn("WHERE users.id = :id_1", compiled_sql)
        where_tail = compiled_sql.split("WHERE ", 1)[1].strip()
        self.assertEqual(where_tail, "users.id = :id_1")
        self.assertEqual(dict(compiled.params), {"id_1": expected_user_id})

    async def test_execute_exception_propagates_before_reset_side_effects(
        self,
    ) -> None:
        denied_before = len(FENCE["denied_connects"])
        live_before = len(FENCE["live_redis_constructs"])

        target_user_id = 3079
        target_mobile = "09125553079"
        raw_body = (
            b'{"user_id": 3079, "mobile_number": "09125553079", '
            b'"source_server": "peer-alpha", '
            b'"sentinel": "char-internal-reset-execute-failure-799b"}'
        )
        timestamp_val = "1729651200"
        signature_val = "char-valid-signature-799b"
        api_key_val = "char-valid-api-key-799b"
        headers = {
            "X-Timestamp": timestamp_val,
            "X-Signature": signature_val,
            "X-API-Key": api_key_val,
        }

        events: list[str] = []
        request = _RecordingRequest(
            body_bytes=raw_body,
            headers=headers,
            events=events,
        )
        payload = InternalSessionResetRequest(
            user_id=target_user_id,
            mobile_number=target_mobile,
            source_server="peer-alpha",
        )

        sentinel_error = RuntimeError(
            "characterization-internal-reset-db-execute-failure-sentinel"
        )
        verifier_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        orm_statements: list[object] = []
        orm_states: list[tuple[bool, bool, bool]] = []
        driver_sql_statements: list[str] = []

        def _fake_verify_signature(*args: Any, **kwargs: Any) -> bool:
            verifier_calls.append((args, dict(kwargs)))
            events.append("verifier")
            return True

        reset_poison = AsyncMock(
            side_effect=AssertionError(
                "reset_user_session_state must not be called when db.execute raises"
            )
        )

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )

        def _on_before_cursor_execute(
            conn: Any,
            cursor: Any,
            statement: str,
            parameters: Any,
            context: Any,
            executemany: bool,
        ) -> None:
            driver_sql_statements.append(statement)

        event.listen(engine, "before_cursor_execute", _on_before_cursor_execute)
        try:
            async with real_async_session(engine) as db:
                self.assertIsInstance(db, AsyncSession)
                self.assertIsInstance(db.sync_session, Session)
                self.assertIs(db.sync_session.bind, engine)

                def _on_do_orm_execute(orm_execute_state: ORMExecuteState) -> None:
                    events.append("orm_execute")
                    stmt = orm_execute_state.statement
                    orm_statements.append(stmt)
                    orm_states.append(
                        (
                            bool(orm_execute_state.is_select),
                            bool(orm_execute_state.is_column_load),
                            bool(orm_execute_state.is_relationship_load),
                        )
                    )
                    self._assert_user_select_statement(
                        stmt,
                        expected_user_id=target_user_id,
                    )
                    raise sentinel_error

                event.listen(db.sync_session, "do_orm_execute", _on_do_orm_execute)
                async_sync_mocks: dict[str, MagicMock] = {
                    name: MagicMock(
                        side_effect=AssertionError(
                            f"Unexpected AsyncSession.{name} call"
                        )
                    )
                    for name in ("add", "add_all")
                }
                async_coro_mocks: dict[str, AsyncMock] = {
                    name: AsyncMock(
                        side_effect=AssertionError(
                            f"Unexpected AsyncSession.{name} call"
                        )
                    )
                    for name in ("delete", "flush", "commit", "rollback", "refresh")
                }
                sync_mocks: dict[str, MagicMock] = {
                    name: MagicMock(
                        side_effect=AssertionError(
                            f"Unexpected Session.{name} call"
                        )
                    )
                    for name in (
                        "add",
                        "add_all",
                        "delete",
                        "flush",
                        "commit",
                        "rollback",
                        "refresh",
                    )
                }

                try:
                    with ExitStack() as guard_stack:
                        for name, mock_obj in async_sync_mocks.items():
                            guard_stack.enter_context(
                                patch.object(db, name, new=mock_obj)
                            )
                        for name, mock_obj in async_coro_mocks.items():
                            guard_stack.enter_context(
                                patch.object(db, name, new=mock_obj)
                            )
                        for name, mock_obj in sync_mocks.items():
                            guard_stack.enter_context(
                                patch.object(db.sync_session, name, new=mock_obj)
                            )
                        guard_stack.enter_context(
                            patch(
                                "api.routers.sessions.verify_session_authority_signature",
                                side_effect=_fake_verify_signature,
                            )
                        )
                        guard_stack.enter_context(
                            patch(
                                "api.routers.sessions.reset_user_session_state",
                                new=reset_poison,
                            )
                        )

                        with self.assertRaises(RuntimeError) as exc_info:
                            await internal_reset_user_sessions(
                                payload=payload,
                                request=request,  # type: ignore[arg-type]
                                db=db,
                            )
                finally:
                    if event.contains(
                        db.sync_session, "do_orm_execute", _on_do_orm_execute
                    ):
                        event.remove(
                            db.sync_session, "do_orm_execute", _on_do_orm_execute
                        )

                self.assertIs(exc_info.exception, sentinel_error)
                self.assertEqual(len(db.sync_session.new), 0)
                self.assertEqual(len(db.sync_session.dirty), 0)
                self.assertEqual(len(db.sync_session.deleted), 0)
        finally:
            if event.contains(
                engine, "before_cursor_execute", _on_before_cursor_execute
            ):
                event.remove(
                    engine, "before_cursor_execute", _on_before_cursor_execute
                )
            engine.dispose()

        self.assertEqual(request.body_call_count, 1)
        self.assertEqual(request.body_await_count, 1)
        self.assertEqual(
            request.headers.get_calls,
            [
                ("X-Timestamp", None),
                ("X-Signature", None),
                ("X-API-Key", None),
            ],
        )
        self.assertEqual(len(verifier_calls), 1)
        verifier_pos_args, verifier_kwargs = verifier_calls[0]
        self.assertEqual(verifier_pos_args, (raw_body,))
        self.assertEqual(
            set(verifier_kwargs.keys()),
            {"timestamp", "signature", "api_key"},
        )
        self.assertEqual(
            verifier_kwargs,
            {
                "timestamp": timestamp_val,
                "signature": signature_val,
                "api_key": api_key_val,
            },
        )
        self.assertEqual(
            events,
            [
                "body_call",
                "body_await",
                "header:X-Timestamp",
                "header:X-Signature",
                "header:X-API-Key",
                "verifier",
                "orm_execute",
            ],
        )

        self.assertEqual(len(orm_statements), 1)
        self.assertEqual(orm_states, [(True, False, False)])
        self._assert_user_select_statement(
            orm_statements[0],
            expected_user_id=target_user_id,
        )

        reset_poison.assert_not_called()
        reset_poison.assert_not_awaited()
        self.assertEqual(reset_poison.call_count, 0)
        self.assertEqual(reset_poison.await_count, 0)

        for name, mock_obj in async_sync_mocks.items():
            mock_obj.assert_not_called()
            self.assertEqual(mock_obj.call_count, 0, msg=f"AsyncSession.{name}")
        for name, mock_obj in async_coro_mocks.items():
            mock_obj.assert_not_called()
            mock_obj.assert_not_awaited()
            self.assertEqual(mock_obj.call_count, 0, msg=f"AsyncSession.{name}")
            self.assertEqual(mock_obj.await_count, 0, msg=f"AsyncSession.{name}")
        sync_mocks["flush"].assert_not_called()
        for name, mock_obj in sync_mocks.items():
            mock_obj.assert_not_called()
            self.assertEqual(mock_obj.call_count, 0, msg=f"Session.{name}")

        self.assertEqual(driver_sql_statements, [])
        self.assertEqual(len(FENCE["denied_connects"]), denied_before)
        self.assertEqual(len(FENCE["live_redis_constructs"]), live_before)


if __name__ == "__main__":
    unittest.main()
