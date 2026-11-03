from __future__ import annotations
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

"""Hermetic characterization of sessions.internal_reset_user_sessions scalar result failure.

Stage: P1-00b (IN_PROGRESS; P1-00 remains COMPLETE)
Active assignment: WA-P1-00B-COMPLETION-20261102-001
Scope SHA256: 80208105800094b4904f1d57dfaf3c9873bac20e1fd5a4083366e72f466e4dd1
Parent assignment: WA-P1-00B-AUTHORITY-SECURITY-RESUME-20261102-001
Original parent assignment: WA-P1-00B-FREEZE-20260904-001
Canonical open blocker: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: API-AUTH-POST-SESSIONS-INTERNAL-RESET-USER-SESSIONS-L562
Handler: api/routers/sessions.py::internal_reset_user_sessions (POST /api/sessions/internal/reset-user-sessions)
Scenario key: P1-00B-INTERNAL-RESET-SCALAR-FAILURE-V1.synchronous-result-exception-before-reset

Explicit exclusions:
- Bounded synthetic result-consumption exception witness only; not actual database failure, persistence, or cryptographic HMAC proof.
- Excludes downstream service failure, signature rejection, missing user, identity mismatch, success path, HTTP/Depends transport, retry, concurrency, reload, origin matrix, and whole-seed/High/Stage closure.
"""

from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, call, patch

from sqlalchemy import create_engine, event, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine.result import IteratorResult, SimpleResultMetaData
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import api.routers.sessions as sessions_router
from api.routers.sessions import (
    InternalSessionResetRequest,
    internal_reset_user_sessions,
)
from models.user import User
from tests.test_p1_00b_characterization_real_async_session_helper import real_async_session


_PATCHED_ROUTER_ATTRS: tuple[str, ...] = (
    "verify_session_authority_signature",
    "reset_user_session_state",
    "force_clear_sessions",
    "inspect_local_session_authority",
    "publish_user_event",
    "_store_temporary_refresh_token",
    "audit_log",
    "create_access_token",
    "create_refresh_token",
)

_ASYNC_SESSION_SYNC_MUTATION_ATTRS: tuple[str, ...] = (
    "add",
    "add_all",
)

_ASYNC_SESSION_ASYNC_MUTATION_ATTRS: tuple[str, ...] = (
    "delete",
    "flush",
    "commit",
    "rollback",
    "refresh",
)

_SYNC_SESSION_MUTATION_ATTRS: tuple[str, ...] = (
    "add",
    "add_all",
    "delete",
    "flush",
    "commit",
    "rollback",
    "refresh",
)

_TRAPPED_RESULT_ATTRS: tuple[str, ...] = (
    "scalars",
    "scalar_one",
    "first",
    "all",
)


class _RecordingHeaders:
    def __init__(self, values: dict[str, str], events: list[object]) -> None:
        self._values = dict(values)
        self._events = events
        self.get_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def get(self, *args: object, **kwargs: object) -> str | None:
        self.get_calls.append((args, kwargs))
        if len(args) != 1 or kwargs or not isinstance(args[0], str):
            raise AssertionError(f"Unexpected header get call: args={args!r}, kwargs={kwargs!r}")
        key = args[0]
        self._events.append(("header.get", key))
        return self._values.get(key)


class InternalResetScalarFailureCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _assert_user_select_statement(captured_stmt: object, expected_user_id: int) -> None:
        expected_stmt = select(User).where(User.id == expected_user_id)
        if not hasattr(captured_stmt, "compare") or not captured_stmt.compare(
            expected_stmt, use_proxies=True
        ):
            raise AssertionError(
                f"Captured statement {captured_stmt!r} did not structurally match {expected_stmt!r}"
            )
        dialect = postgresql.dialect()
        compiled_actual = captured_stmt.compile(dialect=dialect)
        compiled_expected = expected_stmt.compile(dialect=dialect)
        if str(compiled_actual) != str(compiled_expected):
            raise AssertionError(
                f"Compiled SQL mismatch: {compiled_actual!s} != {compiled_expected!s}"
            )
        if compiled_actual.params != {"id_1": expected_user_id}:
            raise AssertionError(
                f"Unexpected compiled params: {compiled_actual.params!r}"
            )

    @staticmethod
    def _assert_bound_method_restored(
        instance: object,
        attr: str,
        original_bound: object,
        expected_owner_cls: type,
    ) -> None:
        restored = getattr(instance, attr)
        if restored != original_bound:
            raise AssertionError(f"Bound method {attr} was not restored on {instance!r}")
        if getattr(restored, "__self__", None) is not instance:
            raise AssertionError(f"Restored {attr}.__self__ is not {instance!r}")
        expected_func = getattr(expected_owner_cls, attr)
        if getattr(restored, "__func__", None) is not expected_func:
            raise AssertionError(
                f"Restored {attr}.__func__ {getattr(restored, '__func__', None)!r} != {expected_func!r}"
            )

    async def test_scalar_exception_propagates_before_reset_side_effects(self) -> None:
        fence = install_p1_00_network_fence()
        denied_before = len(fence["denied_connects"])
        redis_before = len(fence["live_redis_constructs"])

        expected_user_id = 4301
        expected_mobile_number = "09124301001"
        expected_source_server = "foreign"
        raw_body = b'{"user_id":4301,"mobile_number":"09124301001","source_server":"foreign"}'
        header_values = {
            "X-Timestamp": "1762130001",
            "X-Signature": "sig-reset-scalar-4301",
            "X-API-Key": "key-reset-scalar-4301",
        }
        sentinel_error = RuntimeError("synthetic scalar_one_or_none extraction failure")

        payload = InternalSessionResetRequest(
            user_id=expected_user_id,
            mobile_number=expected_mobile_number,
            source_server=expected_source_server,
        )
        events: list[object] = []
        headers = _RecordingHeaders(header_values, events)

        async def _read_body(*args: object, **kwargs: object) -> bytes:
            if args or kwargs:
                raise AssertionError(f"Unexpected request.body args={args!r}, kwargs={kwargs!r}")
            events.append("request.body")
            return raw_body

        request = SimpleNamespace(
            body=AsyncMock(side_effect=_read_body),
            headers=headers,
        )

        def _verify_signature(*args: object, **kwargs: object) -> bool:
            events.append("verify_session_authority_signature")
            return True

        verify_mock = MagicMock(side_effect=_verify_signature)
        router_traps = SimpleNamespace(
            reset_user_session_state=AsyncMock(
                side_effect=AssertionError("Unexpected reset_user_session_state")
            ),
            force_clear_sessions=AsyncMock(
                side_effect=AssertionError("Unexpected force_clear_sessions")
            ),
            inspect_local_session_authority=AsyncMock(
                side_effect=AssertionError("Unexpected inspect_local_session_authority")
            ),
            publish_user_event=AsyncMock(
                side_effect=AssertionError("Unexpected publish_user_event")
            ),
            _store_temporary_refresh_token=AsyncMock(
                side_effect=AssertionError("Unexpected _store_temporary_refresh_token")
            ),
            audit_log=MagicMock(
                side_effect=AssertionError("Unexpected audit_log")
            ),
            create_access_token=MagicMock(
                side_effect=AssertionError("Unexpected create_access_token")
            ),
            create_refresh_token=MagicMock(
                side_effect=AssertionError("Unexpected create_refresh_token")
            ),
        )
        original_router_bindings = {
            "internal_reset_user_sessions": sessions_router.internal_reset_user_sessions,
            **{attr: getattr(sessions_router, attr) for attr in _PATCHED_ROUTER_ATTRS},
        }

        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        driver_sql_statements: list[str] = []

        def _on_before_cursor_execute(
            conn: object,
            cursor: object,
            statement: str,
            parameters: object,
            context: object,
            executemany: object,
        ) -> None:
            driver_sql_statements.append(statement)

        event.listen(engine, "before_cursor_execute", _on_before_cursor_execute)

        captured_statements: list[object] = []
        orm_execute_states: list[object] = []
        created_results: list[IteratorResult[object]] = []
        original_result_bindings: dict[str, object] = {}
        result_scalar_mock: MagicMock | None = None
        result_traps: dict[str, MagicMock] = {}
        sync_session_ref: Session | None = None
        on_do_orm_execute_ref = None

        try:
            async with real_async_session(engine) as session:
                self.assertIsInstance(session, AsyncSession)
                sync_session = session.sync_session
                self.assertIsInstance(sync_session, Session)
                sync_session_ref = sync_session

                original_async_session_bindings = {
                    attr: getattr(session, attr)
                    for attr in (
                        "execute",
                        *_ASYNC_SESSION_SYNC_MUTATION_ATTRS,
                        *_ASYNC_SESSION_ASYNC_MUTATION_ATTRS,
                    )
                }
                original_sync_session_bindings = {
                    attr: getattr(sync_session, attr)
                    for attr in _SYNC_SESSION_MUTATION_ATTRS
                }

                async def _forward_session_execute(*args: object, **kwargs: object) -> object:
                    if len(args) != 1 or kwargs:
                        raise AssertionError(
                            f"Unexpected session.execute args={args!r}, kwargs={kwargs!r}"
                        )
                    return await original_async_session_bindings["execute"](*args, **kwargs)

                execute_spy = AsyncMock(side_effect=_forward_session_execute)
                async_session_sync_traps = {
                    attr: MagicMock(side_effect=AssertionError(f"Unexpected AsyncSession.{attr}"))
                    for attr in _ASYNC_SESSION_SYNC_MUTATION_ATTRS
                }
                async_session_async_traps = {
                    attr: AsyncMock(side_effect=AssertionError(f"Unexpected AsyncSession.{attr}"))
                    for attr in _ASYNC_SESSION_ASYNC_MUTATION_ATTRS
                }
                sync_session_traps = {
                    attr: MagicMock(side_effect=AssertionError(f"Unexpected Session.{attr}"))
                    for attr in _SYNC_SESSION_MUTATION_ATTRS
                }

                with ExitStack() as handler_stack:
                    def _on_do_orm_execute(orm_execute_state: object) -> IteratorResult[object]:
                        nonlocal result_scalar_mock
                        if orm_execute_states:
                            raise AssertionError("Unexpected second do_orm_execute call")
                        if not getattr(orm_execute_state, "is_select", False):
                            raise AssertionError("Expected SELECT statement in do_orm_execute")
                        orm_execute_states.append(orm_execute_state)
                        captured_statements.append(getattr(orm_execute_state, "statement"))
                        events.append("db.execute")

                        raw_result: IteratorResult[object] = IteratorResult(
                            SimpleResultMetaData(["User"]),
                            iter(()),
                        )
                        created_results.append(raw_result)
                        for attr in ("scalar_one_or_none", *_TRAPPED_RESULT_ATTRS):
                            original_result_bindings[attr] = getattr(raw_result, attr)

                        def _poisoned_scalar_one_or_none(*args: object, **kwargs: object) -> object:
                            if args or kwargs:
                                raise AssertionError(
                                    f"Unexpected scalar_one_or_none args={args!r}, kwargs={kwargs!r}"
                                )
                            events.append("result.scalar_one_or_none")
                            raise sentinel_error

                        result_scalar_mock = MagicMock(side_effect=_poisoned_scalar_one_or_none)
                        handler_stack.enter_context(
                            patch.object(raw_result, "scalar_one_or_none", result_scalar_mock)
                        )
                        for attr in _TRAPPED_RESULT_ATTRS:
                            trap = MagicMock(
                                side_effect=AssertionError(f"Unexpected IteratorResult.{attr}")
                            )
                            result_traps[attr] = trap
                            handler_stack.enter_context(patch.object(raw_result, attr, trap))
                        return raw_result

                    on_do_orm_execute_ref = _on_do_orm_execute
                    event.listen(sync_session, "do_orm_execute", _on_do_orm_execute)
                    handler_stack.callback(
                        lambda: event.remove(sync_session, "do_orm_execute", _on_do_orm_execute)
                        if event.contains(sync_session, "do_orm_execute", _on_do_orm_execute)
                        else None
                    )

                    handler_stack.enter_context(
                        patch.object(
                            sessions_router,
                            "verify_session_authority_signature",
                            verify_mock,
                        )
                    )
                    for attr in _PATCHED_ROUTER_ATTRS:
                        if attr == "verify_session_authority_signature":
                            continue
                        handler_stack.enter_context(
                            patch.object(sessions_router, attr, getattr(router_traps, attr))
                        )

                    handler_stack.enter_context(
                        patch.object(session, "execute", execute_spy)
                    )
                    for attr, trap in async_session_sync_traps.items():
                        handler_stack.enter_context(patch.object(session, attr, trap))
                    for attr, trap in async_session_async_traps.items():
                        handler_stack.enter_context(patch.object(session, attr, trap))
                    for attr, trap in sync_session_traps.items():
                        handler_stack.enter_context(patch.object(sync_session, attr, trap))

                    with self.assertRaises(RuntimeError) as cm:
                        await internal_reset_user_sessions(
                            payload=payload,
                            request=request,
                            db=session,
                        )

                self.assertFalse(
                    event.contains(sync_session, "do_orm_execute", _on_do_orm_execute)
                )
                for attr, orig_bound in original_async_session_bindings.items():
                    self._assert_bound_method_restored(
                        session, attr, orig_bound, AsyncSession
                    )
                for attr, orig_bound in original_sync_session_bindings.items():
                    self._assert_bound_method_restored(
                        sync_session, attr, orig_bound, Session
                    )
                self.assertEqual(len(created_results), 1)
                intercepted_result = created_results[0]
                for attr, orig_bound in original_result_bindings.items():
                    self._assert_bound_method_restored(
                        intercepted_result, attr, orig_bound, IteratorResult
                    )
                intercepted_result.close()

            event.remove(engine, "before_cursor_execute", _on_before_cursor_execute)
            self.assertFalse(
                event.contains(engine, "before_cursor_execute", _on_before_cursor_execute)
            )
        finally:
            for res in created_results:
                try:
                    res.close()
                except Exception:
                    pass
            if (
                sync_session_ref is not None
                and on_do_orm_execute_ref is not None
                and event.contains(sync_session_ref, "do_orm_execute", on_do_orm_execute_ref)
            ):
                event.remove(sync_session_ref, "do_orm_execute", on_do_orm_execute_ref)
            if event.contains(engine, "before_cursor_execute", _on_before_cursor_execute):
                event.remove(engine, "before_cursor_execute", _on_before_cursor_execute)
            engine.dispose()

        self.assertIs(cm.exception, sentinel_error)
        self.assertIs(
            sessions_router.internal_reset_user_sessions,
            internal_reset_user_sessions,
        )
        for attr, orig_obj in original_router_bindings.items():
            self.assertIs(getattr(sessions_router, attr), orig_obj)

        self.assertEqual(request.body.call_count, 1)
        self.assertEqual(request.body.await_count, 1)
        self.assertEqual(request.body.call_args_list, [call()])
        self.assertEqual(
            headers.get_calls,
            [
                (("X-Timestamp",), {}),
                (("X-Signature",), {}),
                (("X-API-Key",), {}),
            ],
        )
        self.assertEqual(verify_mock.call_count, 1)
        self.assertEqual(
            verify_mock.call_args_list,
            [
                call(
                    raw_body,
                    timestamp=header_values["X-Timestamp"],
                    signature=header_values["X-Signature"],
                    api_key=header_values["X-API-Key"],
                )
            ],
        )

        self.assertEqual(execute_spy.call_count, 1)
        self.assertEqual(execute_spy.await_count, 1)
        self.assertEqual(len(orm_execute_states), 1)
        self.assertEqual(len(captured_statements), 1)
        self._assert_user_select_statement(captured_statements[0], expected_user_id)
        self.assertIsNotNone(result_scalar_mock)
        assert result_scalar_mock is not None
        self.assertEqual(result_scalar_mock.call_count, 1)
        self.assertEqual(result_scalar_mock.call_args_list, [call()])

        self.assertEqual(
            events,
            [
                "request.body",
                ("header.get", "X-Timestamp"),
                ("header.get", "X-Signature"),
                ("header.get", "X-API-Key"),
                "verify_session_authority_signature",
                "db.execute",
                "result.scalar_one_or_none",
            ],
        )

        for attr in _TRAPPED_RESULT_ATTRS:
            self.assertEqual(result_traps[attr].call_count, 0)

        for async_router_trap in (
            router_traps.reset_user_session_state,
            router_traps.force_clear_sessions,
            router_traps.inspect_local_session_authority,
            router_traps.publish_user_event,
            router_traps._store_temporary_refresh_token,
        ):
            self.assertEqual(async_router_trap.call_count, 0)
            self.assertEqual(async_router_trap.await_count, 0)

        for sync_router_trap in (
            router_traps.audit_log,
            router_traps.create_access_token,
            router_traps.create_refresh_token,
        ):
            self.assertEqual(sync_router_trap.call_count, 0)

        for trap in async_session_async_traps.values():
            self.assertEqual(trap.call_count, 0)
            self.assertEqual(trap.await_count, 0)
        for trap in async_session_sync_traps.values():
            self.assertEqual(trap.call_count, 0)
        for trap in sync_session_traps.values():
            self.assertEqual(trap.call_count, 0)

        self.assertEqual(driver_sql_statements, [])
        self.assertEqual(payload.user_id, expected_user_id)
        self.assertEqual(payload.mobile_number, expected_mobile_number)
        self.assertEqual(payload.source_server, expected_source_server)
        self.assertEqual(len(fence["denied_connects"]), denied_before)
        self.assertEqual(len(fence["live_redis_constructs"]), redis_before)


if __name__ == "__main__":
    unittest.main()
