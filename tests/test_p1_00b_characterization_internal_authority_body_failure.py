"""Bounded P1-00b characterization for internal_session_authority_check request.body() failure.

Stage: P1-00b (IN_PROGRESS; P1-00 COMPLETE)
Assignment: WA-P1-00B-COMPLETION-20261102-001 (scope SHA256 80208105800094b4904f1d57dfaf3c9873bac20e1fd5a4083366e72f466e4dd1; parent WA-P1-00B-FREEZE-20260904-001)
Canonical blocker: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Seed (NOT promoted): API-AUTH-POST-SESSIONS-INTERNAL-SESSION-AUTHORITY-CHECK-L539
Scenario: P1-00B-AUTHORITY-BODY-FAILURE-V1.before-headers-verifier-query

Singleton rationale:
Only the `internal_session_authority_check` `await request.body()` RuntimeError boundary
remains untested among internal session body-read boundaries; `internal_reset_user_sessions`
and `internal_replace_user_sessions` body-read failures are already covered in
`tests/test_p1_00b_characterization_internal_session_body_failures_batch.py` and must not be
repeated as new evidence. The separate authority inspector-failure path requires a
matching-user query fixture and occurs after body read, signature verification, and user
lookup, so it is excluded from this pre-body boundary; no shared-fixture batch is justified.

Explicit exclusions:
Excludes body-read success, signature/HMAC verification, HTTP/Depends wiring, query/result
failures, missing/present-user outcomes, inspection internals/failure, persistence/retry/
reload/concurrency/origin interactions, and whole-seed/High/Stage acceptance.
No runtime, documentation, or inventory changes are made, and no counts or seed promotion
are claimed; raw 398/67 remaining, strict 288/177, 24 origin cells, and 2 High OPEN
remain unchanged.
"""

from __future__ import annotations
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import api.routers.sessions as sessions_router
from api.routers.sessions import (
    InternalSessionAuthorityCheck,
    internal_session_authority_check,
)
from tests.test_p1_00b_characterization_internal_session_body_failures_batch import (
    _BodyFailureRequest,
    _PoisonHeaders,
)

_FENCE = install_p1_00_network_fence()
_ORIGINAL_AUTHORITY_CHECK_HANDLER = internal_session_authority_check

_SYNC_ROUTER_TRAP_NAMES: tuple[str, ...] = (
    "verify_session_authority_signature",
    "select",
    "audit_log",
    "create_access_token",
    "create_refresh_token",
)
_ASYNC_ROUTER_TRAP_NAMES: tuple[str, ...] = (
    "inspect_local_session_authority",
    "reset_user_session_state",
    "force_clear_sessions",
    "_store_temporary_refresh_token",
    "publish_user_event",
)
_ASYNC_DB_SEAM_NAMES: tuple[str, ...] = (
    "execute",
    "commit",
    "rollback",
    "flush",
    "delete",
    "refresh",
)
_SYNC_DB_SEAM_NAMES: tuple[str, ...] = (
    "add",
    "add_all",
)


def _build_poison_db() -> tuple[SimpleNamespace, dict[str, AsyncMock], dict[str, MagicMock]]:
    async_seams: dict[str, AsyncMock] = {}
    sync_seams: dict[str, MagicMock] = {}
    attrs: dict[str, object] = {}
    for name in _ASYNC_DB_SEAM_NAMES:
        mock = AsyncMock(
            side_effect=AssertionError(
                f"db.{name} must not be called when request.body() raises"
            )
        )
        async_seams[name] = mock
        attrs[name] = mock
    for name in _SYNC_DB_SEAM_NAMES:
        mock = MagicMock(
            side_effect=AssertionError(
                f"db.{name} must not be called when request.body() raises"
            )
        )
        sync_seams[name] = mock
        attrs[name] = mock
    return SimpleNamespace(**attrs), async_seams, sync_seams


class InternalAuthorityBodyFailureCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    async def test_body_error_propagates_before_headers_verifier_and_query(self) -> None:
        self.assertIs(
            internal_session_authority_check,
            _ORIGINAL_AUTHORITY_CHECK_HANDLER,
        )
        self.assertIs(
            sessions_router.internal_session_authority_check,
            _ORIGINAL_AUTHORITY_CHECK_HANDLER,
        )

        denied_before = len(_FENCE["denied_connects"])
        live_redis_before = len(_FENCE["live_redis_constructs"])

        original_router_bindings: dict[str, object] = {
            name: getattr(sessions_router, name)
            for name in (*_SYNC_ROUTER_TRAP_NAMES, *_ASYNC_ROUTER_TRAP_NAMES)
        }
        sync_traps: dict[str, MagicMock] = {
            name: MagicMock(
                side_effect=AssertionError(
                    f"{name} must not be called when request.body() raises"
                )
            )
            for name in _SYNC_ROUTER_TRAP_NAMES
        }
        async_traps: dict[str, AsyncMock] = {
            name: AsyncMock(
                side_effect=AssertionError(
                    f"{name} must not be called when request.body() raises"
                )
            )
            for name in _ASYNC_ROUTER_TRAP_NAMES
        }

        payload = InternalSessionAuthorityCheck(
            user_id=9303,
            source_server="synthetic-authority-body-failure",
        )
        expected_error = RuntimeError("synthetic-authority-request-body-read-failure")
        request = _BodyFailureRequest(expected_error)
        self.assertIsInstance(request.poison_headers, _PoisonHeaders)
        poison_db, db_async_seams, db_sync_seams = _build_poison_db()

        with ExitStack() as stack:
            for name, mock in sync_traps.items():
                stack.enter_context(
                    patch.object(sessions_router, name, new=mock)
                )
            for name, mock in async_traps.items():
                stack.enter_context(
                    patch.object(sessions_router, name, new=mock)
                )

            for name, mock in sync_traps.items():
                self.assertIs(getattr(sessions_router, name), mock)
            for name, mock in async_traps.items():
                self.assertIs(getattr(sessions_router, name), mock)

            with self.assertRaises(RuntimeError) as exc_info:
                await internal_session_authority_check(
                    payload=payload,
                    request=request,
                    db=poison_db,
                )

        self.assertIs(exc_info.exception, expected_error)
        request.body.assert_called_once_with()
        request.body.assert_awaited_once_with()
        self.assertEqual(request.body.call_count, 1)
        self.assertEqual(request.body.await_count, 1)
        self.assertEqual(request.headers_property_access_count, 0)
        self.assertEqual(request.poison_headers.access_count, 0)

        self.assertIs(type(payload.user_id), int)
        self.assertEqual(payload.user_id, 9303)
        self.assertEqual(payload.source_server, "synthetic-authority-body-failure")

        for name, mock in sync_traps.items():
            mock.assert_not_called()
            self.assertEqual(mock.call_count, 0, msg=f"unexpected call to {name}")
        for name, mock in async_traps.items():
            mock.assert_not_called()
            mock.assert_not_awaited()
            self.assertEqual(mock.call_count, 0, msg=f"unexpected call to {name}")
            self.assertEqual(mock.await_count, 0, msg=f"unexpected await on {name}")

        for name, mock in db_async_seams.items():
            mock.assert_not_called()
            mock.assert_not_awaited()
            self.assertEqual(mock.call_count, 0, msg=f"unexpected call to db.{name}")
            self.assertEqual(mock.await_count, 0, msg=f"unexpected await on db.{name}")
        for name, mock in db_sync_seams.items():
            mock.assert_not_called()
            self.assertEqual(mock.call_count, 0, msg=f"unexpected call to db.{name}")

        for name, original_binding in original_router_bindings.items():
            self.assertIs(getattr(sessions_router, name), original_binding)
        self.assertIs(
            internal_session_authority_check,
            _ORIGINAL_AUTHORITY_CHECK_HANDLER,
        )
        self.assertIs(
            sessions_router.internal_session_authority_check,
            _ORIGINAL_AUTHORITY_CHECK_HANDLER,
        )
        self.assertEqual(len(_FENCE["denied_connects"]), denied_before)
        self.assertEqual(len(_FENCE["live_redis_constructs"]), live_redis_before)


if __name__ == "__main__":
    unittest.main()
