from __future__ import annotations
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

"""Hermetic bounded router characterization for internal_replace_user_sessions success response.

Stage: P1-00b (IN_PROGRESS; P1-00 COMPLETE)
Active Assignment: WA-P1-00B-COMPLETION-20261102-001
Assignment SHA256: 80208105800094b4904f1d57dfaf3c9873bac20e1fd5a4083366e72f466e4dd1
Parent Assignment: WA-P1-00B-AUTHORITY-SECURITY-RESUME-20261102-001
Original Parent Assignment: WA-P1-00B-FREEZE-20260904-001
Prior Completed Group: 5953f799f737780e8bcdfb57e8621030cb520422 (remains completed provenance)
Canonical Gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed: API-AUTH-POST-SESSIONS-INTERNAL-REPLACE-USER-SESSIONS-L593
Handler: api/routers/sessions.py:internal_replace_user_sessions
Scenario: P1-00B-INTERNAL-REPLACE-SUCCESS-RESPONSE-V1.stubbed-count-conversion

Scope and limits:
- Characterizes only the router-level successful lookup, positional delegation to
  force_clear_sessions(db, user.id), explicit int(revoked_count) conversion, and
  exact {"revoked_active_sessions": 7} response shape.
- Uses tests/test_p1_00b_characterization_real_async_session_helper.py:real_async_session
  over a tableless in-memory StaticPool SQLite engine with a per-session
  Session.do_orm_execute listener returning a synthetic IteratorResult.
- The synthetic count object returning 7 from __int__ witnesses the router's explicit
  int(revoked_count) conversion operation and does not claim force_clear_sessions
  returns non-int objects in production.

Explicit exclusions:
- Persistence/revocation correctness, deactivate_active_sessions, publish_session_revocation,
  blacklist_session, and Redis side effects.
- Cryptographic signature verification internals (verify_session_authority_signature is stubbed).
- FastAPI HTTP/Depends transport routing.
- Service exceptions, int conversion exceptions, and other service-result shapes.
- Retry, concurrency, reload durability, and origin cells.
- Whole-seed, High-gap, or Stage P1-00b closure, and canonical count/ledger reconciliation.
"""

import inspect
import json
from typing import Any
import unittest
from unittest.mock import AsyncMock
import uuid

from sqlalchemy import create_engine, event
from sqlalchemy.engine.result import IteratorResult, SimpleResultMetaData
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.pool import StaticPool

import api.routers.sessions as sessions_router
from api.routers.sessions import (
    InternalSessionResetRequest,
    internal_replace_user_sessions,
)
from core.enums import UserAccountStatus
from core.registration_identity import (
    normalize_account_name,
    normalize_mobile_number,
)
from models.user import User, UserRole
from tests.test_p1_00b_characterization_real_async_session_helper import (
    real_async_session,
)


class _SyntheticRevokedCount:
    """Witness object proving the router performs explicit int(revoked_count) conversion."""

    def __init__(self, value: int, trace: list[str]) -> None:
        self._value = value
        self._trace = trace
        self.int_calls = 0

    def __int__(self) -> int:
        self.int_calls += 1
        if self.int_calls != 1:
            raise AssertionError(f"Expected exactly one __int__ conversion, got {self.int_calls}")
        self._trace.append("conversion")
        return self._value


class _TrackedHeaders:
    """Minimal headers mapping recording exact router header lookups."""

    def __init__(self, values: dict[str, str], trace: list[str]) -> None:
        self._values = dict(values)
        self._trace = trace
        self.calls: list[tuple[str, Any]] = []

    def get(self, key: str, default: Any = None) -> Any:
        self.calls.append((key, default))
        self._trace.append(f"header:{key}")
        return self._values.get(key, default)


class _TrackedRequest:
    """Minimal Request stand-in recording body() and header accesses in order."""

    def __init__(self, raw_body: bytes, headers: dict[str, str], trace: list[str]) -> None:
        self._raw_body = raw_body
        self._trace = trace
        self.body_calls = 0
        self.headers = _TrackedHeaders(headers, trace)

    async def body(self) -> bytes:
        self.body_calls += 1
        if self.body_calls != 1:
            raise AssertionError(f"Expected request.body() once, got {self.body_calls}")
        self._trace.append("request.body")
        return self._raw_body


class InternalReplaceSuccessResponseCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Bounded characterization for internal_replace_user_sessions int conversion and response."""

    async def test_successful_service_result_is_converted_to_integer_response(self) -> None:
        fence_state = install_p1_00_network_fence()
        self.assertTrue(fence_state["installed"])
        denied_before = len(fence_state["denied_connects"])
        redis_before = len(fence_state["live_redis_constructs"])

        user_id = 4201
        account_name = "replace_target_4201"
        mobile_number = "09125554201"
        payload = InternalSessionResetRequest(
            user_id=user_id,
            mobile_number=mobile_number,
            source_server="iran",
        )
        user = User(
            id=user_id,
            account_name=account_name,
            mobile_number=mobile_number,
            normalized_account_name=normalize_account_name(account_name),
            normalized_mobile_number=normalize_mobile_number(mobile_number),
            full_name="Replace Target User",
            address="Tehran Characterization Street 1",
            role=UserRole.STANDARD,
            account_status=UserAccountStatus.ACTIVE,
        )

        raw_body = json.dumps(
            {
                "user_id": payload.user_id,
                "mobile_number": payload.mobile_number,
                "source_server": payload.source_server,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        expected_headers = {
            "X-Timestamp": "1762139400",
            "X-Signature": "stubbed-valid-signature-hex",
            "X-API-Key": "stubbed-sync-api-key",
        }

        trace: list[str] = []
        request = _TrackedRequest(raw_body=raw_body, headers=expected_headers, trace=trace)
        synthetic_count = _SyntheticRevokedCount(7, trace)

        verifier_calls: list[dict[str, Any]] = []
        service_calls: list[dict[str, Any]] = []
        negative_seam_calls: list[str] = []
        driver_sql_calls: list[str] = []
        session_mutation_calls: list[str] = []
        async_mutation_mocks: dict[str, AsyncMock] = {}
        query_evaluations: list[dict[str, Any]] = []
        scalar_calls: list[Any] = []

        def _stub_verify_session_authority_signature(
            body: bytes,
            *,
            timestamp: str | None,
            signature: str | None,
            api_key: str | None,
        ) -> bool:
            verifier_calls.append(
                {
                    "body": body,
                    "timestamp": timestamp,
                    "signature": signature,
                    "api_key": api_key,
                }
            )
            trace.append("verifier")
            return True

        async def _stub_force_clear_sessions(
            db: AsyncSession,
            user_id: int,
            exclude_session_id: uuid.UUID | None = None,
        ) -> int:
            raise AssertionError("Direct _stub_force_clear_sessions body should be wrapped by positional checker")

        async def _checked_force_clear_sessions(*args: Any, **kwargs: Any) -> Any:
            if len(args) != 2 or kwargs:
                raise AssertionError(
                    f"force_clear_sessions requires exact positional (db, user.id) with no kwargs; got args={args!r}, kwargs={kwargs!r}"
                )
            bound = inspect.signature(_stub_force_clear_sessions).bind(*args, **kwargs)
            service_calls.append(
                {
                    "args": args,
                    "kwargs": dict(kwargs),
                    "bound_arguments": dict(bound.arguments),
                }
            )
            trace.append("service")
            return synthetic_count

        def _make_forbidden_seam(name: str):
            def _forbidden(*args: Any, **kwargs: Any) -> Any:
                negative_seam_calls.append(name)
                raise AssertionError(f"Unexpected call to negative router seam {name}")

            return _forbidden

        original_router_bindings = {
            "verify_session_authority_signature": sessions_router.verify_session_authority_signature,
            "force_clear_sessions": sessions_router.force_clear_sessions,
            "publish_user_event": sessions_router.publish_user_event,
            "audit_log": sessions_router.audit_log,
            "create_access_token": sessions_router.create_access_token,
            "create_refresh_token": sessions_router.create_refresh_token,
        }

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
            driver_sql_calls.append(statement)
            raise AssertionError(f"Unexpected driver SQL execution: {statement}")

        event.listen(engine, "before_cursor_execute", _on_before_cursor_execute)
        engine_disposed = False
        active_sync_session: Any = None
        do_orm_listener_ref: Any = None
        created_result_holder: list[IteratorResult[Any]] = []

        orig_scalar_in_dict = "scalar_one_or_none" in IteratorResult.__dict__
        orig_scalar_attr = getattr(IteratorResult, "scalar_one_or_none")

        def _spied_scalar_one_or_none(result_self: IteratorResult[Any], *args: Any, **kwargs: Any) -> Any:
            if created_result_holder and result_self is created_result_holder[0]:
                if args or kwargs:
                    raise AssertionError(
                        f"scalar_one_or_none expected no arguments, got args={args!r}, kwargs={kwargs!r}"
                    )
                trace.append("scalar")
                out = orig_scalar_attr(result_self, *args, **kwargs)
                scalar_calls.append(out)
                return out
            return orig_scalar_attr(result_self, *args, **kwargs)

        try:
            sessions_router.verify_session_authority_signature = _stub_verify_session_authority_signature
            sessions_router.force_clear_sessions = _checked_force_clear_sessions
            for seam_name in (
                "publish_user_event",
                "audit_log",
                "create_access_token",
                "create_refresh_token",
            ):
                setattr(sessions_router, seam_name, _make_forbidden_seam(seam_name))

            setattr(IteratorResult, "scalar_one_or_none", _spied_scalar_one_or_none)

            async with real_async_session(engine) as session:
                self.assertIsInstance(session, AsyncSession)
                sync_session = session.sync_session
                active_sync_session = sync_session

                mutation_names = (
                    "add",
                    "add_all",
                    "delete",
                    "flush",
                    "commit",
                    "rollback",
                    "refresh",
                )
                saved_async_mutations = {
                    name: getattr(session, name) for name in mutation_names
                }
                saved_sync_mutations = {
                    name: getattr(sync_session, name) for name in mutation_names
                }

                def _make_sync_mutation_guard(target_label: str, method_name: str):
                    def _guard(*args: Any, **kwargs: Any) -> Any:
                        session_mutation_calls.append(f"{target_label}.{method_name}")
                        raise AssertionError(f"Forbidden session mutation {target_label}.{method_name}")

                    return _guard

                def _make_async_mutation_guard(target_label: str, method_name: str, is_coro: bool):
                    if is_coro:
                        mock_guard = AsyncMock(
                            side_effect=_make_sync_mutation_guard(target_label, method_name)
                        )
                        async_mutation_mocks[method_name] = mock_guard
                        return mock_guard

                    return _make_sync_mutation_guard(target_label, method_name)

                def _on_do_orm_execute(orm_execute_state: Any) -> IteratorResult[Any]:
                    if len(query_evaluations) != 0:
                        raise AssertionError("Expected exactly one do_orm_execute call")
                    if not orm_execute_state.is_select:
                        raise AssertionError("Expected a SELECT ORM execute state")
                    if orm_execute_state.is_column_load or orm_execute_state.is_relationship_load:
                        raise AssertionError("Unexpected column or relationship load")

                    stmt = orm_execute_state.statement
                    descriptions = list(getattr(stmt, "column_descriptions", ()))
                    if len(descriptions) != 1 or descriptions[0].get("entity") is not User:
                        raise AssertionError(f"Expected select(User), got descriptions={descriptions!r}")
                    if stmt.whereclause is None:
                        raise AssertionError("Expected WHERE clause on select(User) statement")

                    compiled = stmt.compile()
                    normalized_sql = " ".join(str(compiled).split())
                    where_compiled = stmt.whereclause.compile()
                    normalized_where = " ".join(str(where_compiled).split())
                    if (
                        not normalized_sql.startswith("SELECT ")
                        or not normalized_sql.endswith(" FROM users WHERE users.id = :id_1")
                        or normalized_where != "users.id = :id_1"
                    ):
                        raise AssertionError(f"Unexpected compiled SQL: {normalized_sql}")
                    if dict(compiled.params) != {"id_1": payload.user_id} or dict(where_compiled.params) != {"id_1": payload.user_id}:
                        raise AssertionError(f"Unexpected compiled params: {compiled.params!r}")

                    query_evaluations.append(
                        {
                            "entity": descriptions[0]["entity"],
                            "sql": normalized_sql,
                            "where_sql": normalized_where,
                            "params": dict(compiled.params),
                        }
                    )
                    trace.append("query")
                    iterator_result = IteratorResult(
                        SimpleResultMetaData(["User"]),
                        iter([(user,)]),
                    )
                    created_result_holder.append(iterator_result)
                    return iterator_result

                do_orm_listener_ref = _on_do_orm_execute
                event.listen(sync_session, "do_orm_execute", _on_do_orm_execute)
                try:
                    for name, orig in saved_async_mutations.items():
                        setattr(
                            session,
                            name,
                            _make_async_mutation_guard(
                                "AsyncSession",
                                name,
                                inspect.iscoroutinefunction(orig),
                            ),
                        )
                    for name in saved_sync_mutations:
                        setattr(
                            sync_session,
                            name,
                            _make_sync_mutation_guard("Session", name),
                        )

                    response = await internal_replace_user_sessions(
                        payload=payload,
                        request=request,  # type: ignore[arg-type]
                        db=session,
                    )
                finally:
                    for name, orig in saved_async_mutations.items():
                        setattr(session, name, orig)
                    for name, orig in saved_sync_mutations.items():
                        setattr(sync_session, name, orig)
                    if event.contains(sync_session, "do_orm_execute", _on_do_orm_execute):
                        event.remove(sync_session, "do_orm_execute", _on_do_orm_execute)
                    self.assertFalse(
                        event.contains(sync_session, "do_orm_execute", _on_do_orm_execute)
                    )
                for name, orig in saved_async_mutations.items():
                    self.assertIs(getattr(session, name), orig)
                for name, orig in saved_sync_mutations.items():
                    self.assertIs(getattr(sync_session, name), orig)
        finally:
            if orig_scalar_in_dict:
                setattr(IteratorResult, "scalar_one_or_none", orig_scalar_attr)
            else:
                delattr(IteratorResult, "scalar_one_or_none")
            for attr_name, orig_val in original_router_bindings.items():
                setattr(sessions_router, attr_name, orig_val)
            if event.contains(engine, "before_cursor_execute", _on_before_cursor_execute):
                event.remove(engine, "before_cursor_execute", _on_before_cursor_execute)
            engine.dispose()
            engine_disposed = True

        self.assertTrue(engine_disposed)
        self.assertFalse(event.contains(engine, "before_cursor_execute", _on_before_cursor_execute))
        self.assertIsNotNone(active_sync_session)
        self.assertIsNotNone(do_orm_listener_ref)
        self.assertFalse(event.contains(active_sync_session, "do_orm_execute", do_orm_listener_ref))
        for attr_name, orig_val in original_router_bindings.items():
            self.assertIs(getattr(sessions_router, attr_name), orig_val)
        self.assertIs(getattr(IteratorResult, "scalar_one_or_none"), orig_scalar_attr)

        self.assertEqual(response, {"revoked_active_sessions": 7})
        self.assertEqual(list(response.keys()), ["revoked_active_sessions"])
        self.assertIs(type(response["revoked_active_sessions"]), int)
        self.assertEqual(synthetic_count.int_calls, 1)

        self.assertEqual(
            trace,
            [
                "request.body",
                "header:X-Timestamp",
                "header:X-Signature",
                "header:X-API-Key",
                "verifier",
                "query",
                "scalar",
                "service",
                "conversion",
            ],
        )
        self.assertEqual(request.body_calls, 1)
        self.assertEqual(
            request.headers.calls,
            [
                ("X-Timestamp", None),
                ("X-Signature", None),
                ("X-API-Key", None),
            ],
        )
        self.assertEqual(
            verifier_calls,
            [
                {
                    "body": raw_body,
                    "timestamp": expected_headers["X-Timestamp"],
                    "signature": expected_headers["X-Signature"],
                    "api_key": expected_headers["X-API-Key"],
                }
            ],
        )
        self.assertEqual(len(query_evaluations), 1)
        self.assertIs(query_evaluations[0]["entity"], User)
        self.assertEqual(query_evaluations[0]["where_sql"], "users.id = :id_1")
        self.assertEqual(query_evaluations[0]["params"], {"id_1": user_id})
        self.assertEqual(len(created_result_holder), 1)
        self.assertIs(type(created_result_holder[0]), IteratorResult)
        self.assertEqual(scalar_calls, [user])
        self.assertIs(scalar_calls[0], user)

        self.assertEqual(len(service_calls), 1)
        self.assertIs(service_calls[0]["args"][0], session)
        self.assertEqual(service_calls[0]["args"][1], user.id)
        self.assertEqual(len(service_calls[0]["args"]), 2)
        self.assertEqual(service_calls[0]["kwargs"], {})
        self.assertEqual(
            service_calls[0]["bound_arguments"],
            {"db": session, "user_id": user.id},
        )

        self.assertEqual(
            set(async_mutation_mocks.keys()),
            {"delete", "flush", "commit", "rollback", "refresh"},
        )
        for name, mock_guard in async_mutation_mocks.items():
            self.assertEqual(mock_guard.call_count, 0, name)
            self.assertEqual(mock_guard.await_count, 0, name)
        self.assertEqual(session_mutation_calls, [])
        self.assertEqual(driver_sql_calls, [])
        self.assertEqual(negative_seam_calls, [])
        self.assertEqual(len(fence_state["denied_connects"]), denied_before)
        self.assertEqual(len(fence_state["live_redis_constructs"]), redis_before)


if __name__ == "__main__":
    unittest.main()
