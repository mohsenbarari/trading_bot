from __future__ import annotations
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, call, patch

from sqlalchemy import select
from sqlalchemy.dialects import postgresql

import api.routers.sessions as sessions_router
from api.routers.sessions import (
    InternalSessionResetRequest,
    internal_replace_user_sessions,
    internal_reset_user_sessions,
)
from core.enums import UserAccountStatus
from models.user import User, UserRole


_PATCHED_ROUTER_ATTRS: tuple[str, ...] = (
    "verify_session_authority_signature",
    "reset_user_session_state",
    "force_clear_sessions",
    "inspect_local_session_authority",
    "audit_log",
    "publish_user_event",
    "_store_temporary_refresh_token",
    "create_access_token",
    "create_refresh_token",
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


class InternalSessionServiceFailuresCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _build_matching_user(*, user_id: int, mobile_number: str) -> User:
        account_name = f"acct_{user_id}"
        return User(
            id=user_id,
            account_name=account_name,
            mobile_number=mobile_number,
            normalized_account_name=account_name,
            normalized_mobile_number=mobile_number,
            full_name=f"User {user_id}",
            address="Tehran",
            role=UserRole.STANDARD,
            account_status=UserAccountStatus.ACTIVE,
            is_deleted=False,
            max_sessions=1,
            home_server="iran",
        )

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

    def _build_harness(
        self,
        *,
        user_id: int,
        mobile_number: str,
        source_server: str,
        raw_body: bytes,
        header_values: dict[str, str],
        selected_service_attr: str,
        untouched_service_attr: str,
        service_error: RuntimeError,
    ) -> SimpleNamespace:
        events: list[object] = []
        user = self._build_matching_user(user_id=user_id, mobile_number=mobile_number)
        payload = InternalSessionResetRequest(
            user_id=user_id,
            mobile_number=mobile_number,
            source_server=source_server,
        )
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

        def _scalar_one_or_none(*args: object, **kwargs: object) -> User:
            if args or kwargs:
                raise AssertionError(
                    f"Unexpected scalar_one_or_none args={args!r}, kwargs={kwargs!r}"
                )
            events.append("result.scalar_one_or_none")
            return user

        execute_result = SimpleNamespace(
            scalar_one_or_none=MagicMock(side_effect=_scalar_one_or_none),
            scalars=MagicMock(
                side_effect=AssertionError("Unexpected result.scalars() call")
            ),
        )
        captured_statements: list[object] = []

        async def _db_execute(*args: object, **kwargs: object) -> object:
            if len(args) != 1 or kwargs:
                raise AssertionError(f"Unexpected db.execute args={args!r}, kwargs={kwargs!r}")
            if captured_statements:
                raise AssertionError("Unexpected second db.execute call")
            stmt = args[0]
            captured_statements.append(stmt)
            events.append("db.execute")
            return execute_result

        db = SimpleNamespace(
            execute=AsyncMock(side_effect=_db_execute),
            commit=AsyncMock(side_effect=AssertionError("Unexpected db.commit")),
            rollback=AsyncMock(side_effect=AssertionError("Unexpected db.rollback")),
            flush=AsyncMock(side_effect=AssertionError("Unexpected db.flush")),
            add=MagicMock(side_effect=AssertionError("Unexpected db.add")),
            delete=AsyncMock(side_effect=AssertionError("Unexpected db.delete")),
        )

        def _verify_signature(*args: object, **kwargs: object) -> bool:
            events.append("verify_session_authority_signature")
            return True

        async def _raise_selected_service(*args: object, **kwargs: object) -> object:
            events.append(selected_service_attr)
            raise service_error

        traps = SimpleNamespace(
            untouched_service=AsyncMock(
                side_effect=AssertionError(f"Unexpected {untouched_service_attr}")
            ),
            inspect_authority=AsyncMock(
                side_effect=AssertionError("Unexpected inspect_local_session_authority")
            ),
            audit_log=MagicMock(side_effect=AssertionError("Unexpected audit_log")),
            publish_event=AsyncMock(
                side_effect=AssertionError("Unexpected publish_user_event")
            ),
            store_refresh=AsyncMock(
                side_effect=AssertionError("Unexpected _store_temporary_refresh_token")
            ),
            create_access_token=MagicMock(
                side_effect=AssertionError("Unexpected create_access_token")
            ),
            create_refresh_token=MagicMock(
                side_effect=AssertionError("Unexpected create_refresh_token")
            ),
        )
        original_bindings = {
            "internal_reset_user_sessions": sessions_router.internal_reset_user_sessions,
            "internal_replace_user_sessions": sessions_router.internal_replace_user_sessions,
            **{attr: getattr(sessions_router, attr) for attr in _PATCHED_ROUTER_ATTRS},
        }

        return SimpleNamespace(
            events=events,
            user=user,
            payload=payload,
            headers=headers,
            request=request,
            execute_result=execute_result,
            captured_statements=captured_statements,
            db=db,
            verify_mock=MagicMock(side_effect=_verify_signature),
            selected_service_mock=AsyncMock(side_effect=_raise_selected_service),
            selected_service_attr=selected_service_attr,
            untouched_service_attr=untouched_service_attr,
            traps=traps,
            original_bindings=original_bindings,
        )

    @staticmethod
    def _enter_router_patches(stack: ExitStack, harness: SimpleNamespace) -> None:
        patch_map = {
            "verify_session_authority_signature": harness.verify_mock,
            harness.selected_service_attr: harness.selected_service_mock,
            harness.untouched_service_attr: harness.traps.untouched_service,
            "inspect_local_session_authority": harness.traps.inspect_authority,
            "audit_log": harness.traps.audit_log,
            "publish_user_event": harness.traps.publish_event,
            "_store_temporary_refresh_token": harness.traps.store_refresh,
            "create_access_token": harness.traps.create_access_token,
            "create_refresh_token": harness.traps.create_refresh_token,
        }
        for attr, replacement in patch_map.items():
            stack.enter_context(patch.object(sessions_router, attr, replacement))

    def _assert_post_exception_invariants(
        self,
        *,
        harness: SimpleNamespace,
        expected_user_id: int,
        expected_mobile_number: str,
        expected_source_server: str,
        raw_body: bytes,
        expected_headers: dict[str, str],
        fence: dict[str, object],
        denied_before: int,
        redis_before: int,
    ) -> None:
        self.assertEqual(harness.request.body.call_count, 1)
        self.assertEqual(harness.request.body.await_count, 1)
        self.assertEqual(harness.request.body.call_args_list, [call()])
        self.assertEqual(
            harness.headers.get_calls,
            [
                (("X-Timestamp",), {}),
                (("X-Signature",), {}),
                (("X-API-Key",), {}),
            ],
        )
        self.assertEqual(harness.verify_mock.call_count, 1)
        self.assertEqual(
            harness.verify_mock.call_args_list,
            [
                call(
                    raw_body,
                    timestamp=expected_headers["X-Timestamp"],
                    signature=expected_headers["X-Signature"],
                    api_key=expected_headers["X-API-Key"],
                )
            ],
        )
        self.assertEqual(harness.db.execute.call_count, 1)
        self.assertEqual(harness.db.execute.await_count, 1)
        self.assertEqual(len(harness.captured_statements), 1)
        self._assert_user_select_statement(harness.captured_statements[0], expected_user_id)
        self.assertEqual(harness.execute_result.scalar_one_or_none.call_count, 1)
        self.assertEqual(harness.execute_result.scalar_one_or_none.call_args_list, [call()])
        self.assertEqual(harness.execute_result.scalars.call_count, 0)

        self.assertEqual(harness.selected_service_mock.call_count, 1)
        self.assertEqual(harness.selected_service_mock.await_count, 1)
        self.assertEqual(
            harness.events,
            [
                "request.body",
                ("header.get", "X-Timestamp"),
                ("header.get", "X-Signature"),
                ("header.get", "X-API-Key"),
                "verify_session_authority_signature",
                "db.execute",
                "result.scalar_one_or_none",
                harness.selected_service_attr,
            ],
        )

        for async_trap in (
            harness.traps.untouched_service,
            harness.traps.inspect_authority,
            harness.traps.publish_event,
            harness.traps.store_refresh,
            harness.db.commit,
            harness.db.rollback,
            harness.db.flush,
            harness.db.delete,
        ):
            self.assertEqual(async_trap.call_count, 0)
            self.assertEqual(async_trap.await_count, 0)

        for sync_trap in (
            harness.traps.audit_log,
            harness.traps.create_access_token,
            harness.traps.create_refresh_token,
            harness.db.add,
        ):
            self.assertEqual(sync_trap.call_count, 0)

        self.assertIs(
            sessions_router.internal_reset_user_sessions,
            internal_reset_user_sessions,
        )
        self.assertIs(
            sessions_router.internal_replace_user_sessions,
            internal_replace_user_sessions,
        )
        for attr, orig_obj in harness.original_bindings.items():
            self.assertIs(getattr(sessions_router, attr), orig_obj)

        self.assertEqual(harness.user.id, expected_user_id)
        self.assertEqual(harness.user.mobile_number, expected_mobile_number)
        self.assertEqual(harness.user.account_status, UserAccountStatus.ACTIVE)
        self.assertEqual(harness.user.role, UserRole.STANDARD)
        self.assertIs(harness.user.is_deleted, False)
        self.assertEqual(harness.payload.user_id, expected_user_id)
        self.assertEqual(harness.payload.mobile_number, expected_mobile_number)
        self.assertEqual(harness.payload.source_server, expected_source_server)

        self.assertEqual(len(fence["denied_connects"]), denied_before)
        self.assertEqual(len(fence["live_redis_constructs"]), redis_before)

    async def test_reset_service_exception_propagates_after_matching_user(self) -> None:
        fence = install_p1_00_network_fence()
        denied_before = len(fence["denied_connects"])
        redis_before = len(fence["live_redis_constructs"])

        raw_body = b'{"user_id":4101,"mobile_number":"09124101001","source_server":"foreign"}'
        header_values = {
            "X-Timestamp": "1762120001",
            "X-Signature": "sig-reset-4101",
            "X-API-Key": "key-reset-4101",
        }
        expected_error = RuntimeError("reset_user_session_state downstream failure")
        harness = self._build_harness(
            user_id=4101,
            mobile_number="09124101001",
            source_server="foreign",
            raw_body=raw_body,
            header_values=header_values,
            selected_service_attr="reset_user_session_state",
            untouched_service_attr="force_clear_sessions",
            service_error=expected_error,
        )

        with ExitStack() as stack:
            self._enter_router_patches(stack, harness)
            with self.assertRaises(RuntimeError) as cm:
                await internal_reset_user_sessions(
                    payload=harness.payload,
                    request=harness.request,
                    db=harness.db,
                )

        self.assertIs(cm.exception, expected_error)
        svc_args, svc_kwargs = harness.selected_service_mock.call_args
        self.assertEqual(len(svc_args), 1)
        self.assertIs(svc_args[0], harness.db)
        self.assertEqual(
            svc_kwargs,
            {
                "user_id": 4101,
                "mobile_number": "09124101001",
                "delete_session_rows": True,
                "clear_login_limits": True,
            },
        )
        self._assert_post_exception_invariants(
            harness=harness,
            expected_user_id=4101,
            expected_mobile_number="09124101001",
            expected_source_server="foreign",
            raw_body=raw_body,
            expected_headers=header_values,
            fence=fence,
            denied_before=denied_before,
            redis_before=redis_before,
        )

    async def test_replace_service_exception_propagates_after_matching_user(self) -> None:
        fence = install_p1_00_network_fence()
        denied_before = len(fence["denied_connects"])
        redis_before = len(fence["live_redis_constructs"])

        raw_body = b'{"user_id":4202,"mobile_number":"09124202002","source_server":"foreign"}'
        header_values = {
            "X-Timestamp": "1762120002",
            "X-Signature": "sig-replace-4202",
            "X-API-Key": "key-replace-4202",
        }
        expected_error = RuntimeError("force_clear_sessions downstream failure")
        harness = self._build_harness(
            user_id=4202,
            mobile_number="09124202002",
            source_server="foreign",
            raw_body=raw_body,
            header_values=header_values,
            selected_service_attr="force_clear_sessions",
            untouched_service_attr="reset_user_session_state",
            service_error=expected_error,
        )

        with ExitStack() as stack:
            self._enter_router_patches(stack, harness)
            with self.assertRaises(RuntimeError) as cm:
                await internal_replace_user_sessions(
                    payload=harness.payload,
                    request=harness.request,
                    db=harness.db,
                )

        self.assertIs(cm.exception, expected_error)
        svc_args, svc_kwargs = harness.selected_service_mock.call_args
        self.assertEqual(len(svc_args), 2)
        self.assertIs(svc_args[0], harness.db)
        self.assertEqual(svc_args[1], 4202)
        self.assertEqual(svc_kwargs, {})
        self._assert_post_exception_invariants(
            harness=harness,
            expected_user_id=4202,
            expected_mobile_number="09124202002",
            expected_source_server="foreign",
            raw_body=raw_body,
            expected_headers=header_values,
            fence=fence,
            denied_before=denied_before,
            redis_before=redis_before,
        )


if __name__ == "__main__":
    unittest.main()
