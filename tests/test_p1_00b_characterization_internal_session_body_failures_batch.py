"""Bounded P1-00b characterization for internal session request.body() failures.

Stage: P1-00b (IN_PROGRESS; P1-00 COMPLETE)
Assignment: WA-P1-00B-BUSINESS-LIVE-20261023-799B (parent WA-P1-00B-FREEZE-20260904-001)
Gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Scenario key: P1-00B-BATCH-V1.internal-session-body-read-failures
Referenced seeds (NOT promoted):
- API-AUTH-POST-SESSIONS-INTERNAL-RESET-USER-SESSIONS-L562
- API-AUTH-POST-SESSIONS-INTERNAL-REPLACE-USER-SESSIONS-L593

Bounded scope and exclusions:
Characterizes only `await request.body()` raising in `internal_reset_user_sessions` and
`internal_replace_user_sessions` before headers access, signature verification, SQL select,
and session reset/revocation side effects. Excludes body-read success, malformed-body
parsing, signature/authorization validation, HTTP/Depends wiring, missing-user (404) or
mobile-mismatch (409) branches already covered, SQL execution, reset/revoke success,
persistence/retry/reload/origin interactions, seed promotion, HIGH blocker closure, and
Stage closure.
"""

from __future__ import annotations
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

from collections.abc import Awaitable, Callable
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import api.routers.sessions as sessions_router
from api.routers.sessions import (
    InternalSessionResetRequest,
    internal_replace_user_sessions,
    internal_reset_user_sessions,
)

_FENCE = install_p1_00_network_fence()
_ORIGINAL_RESET_HANDLER = internal_reset_user_sessions
_ORIGINAL_REPLACE_HANDLER = internal_replace_user_sessions


class _PoisonHeaders:
    def __init__(self) -> None:
        self.access_count = 0

    def get(self, key: str, default: object = None) -> object:
        self.access_count += 1
        raise AssertionError(f"Unexpected headers.get({key!r}) after request.body() failure")

    def __getitem__(self, key: str) -> object:
        self.access_count += 1
        raise AssertionError(f"Unexpected headers[{key!r}] after request.body() failure")


class _BodyFailureRequest:
    def __init__(self, body_error: RuntimeError) -> None:
        self.body = AsyncMock(side_effect=body_error)
        self.headers_property_access_count = 0
        self.poison_headers = _PoisonHeaders()

    @property
    def headers(self) -> _PoisonHeaders:
        self.headers_property_access_count += 1
        raise AssertionError("request.headers must not be accessed when request.body() raises")


class InternalSessionBodyFailuresCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    async def _assert_body_failure_stops_before_headers_and_db(
        self,
        handler: Callable[..., Awaitable[object]],
        *,
        user_id: int,
        mobile_number: str,
        source_server: str,
        error_message: str,
    ) -> None:
        self.assertIs(sessions_router.internal_reset_user_sessions, _ORIGINAL_RESET_HANDLER)
        self.assertIs(sessions_router.internal_replace_user_sessions, _ORIGINAL_REPLACE_HANDLER)
        self.assertIn(handler, (_ORIGINAL_RESET_HANDLER, _ORIGINAL_REPLACE_HANDLER))

        denied_before = len(_FENCE["denied_connects"])
        live_redis_before = len(_FENCE["live_redis_constructs"])

        payload = InternalSessionResetRequest(
            user_id=user_id,
            mobile_number=mobile_number,
            source_server=source_server,
        )
        expected_error = RuntimeError(error_message)
        request = _BodyFailureRequest(expected_error)
        unused_db = object()

        verify_mock = MagicMock(
            side_effect=AssertionError(
                "verify_session_authority_signature must not be called when request.body() raises"
            )
        )
        select_mock = MagicMock(
            side_effect=AssertionError("select must not be called when request.body() raises")
        )
        reset_mock = AsyncMock(
            side_effect=AssertionError(
                "reset_user_session_state must not be called when request.body() raises"
            )
        )
        clear_mock = AsyncMock(
            side_effect=AssertionError(
                "force_clear_sessions must not be called when request.body() raises"
            )
        )

        with (
            patch(
                "api.routers.sessions.verify_session_authority_signature",
                new=verify_mock,
            ),
            patch(
                "api.routers.sessions.select",
                new=select_mock,
            ),
            patch(
                "api.routers.sessions.reset_user_session_state",
                new=reset_mock,
            ),
            patch(
                "api.routers.sessions.force_clear_sessions",
                new=clear_mock,
            ),
        ):
            with self.assertRaises(RuntimeError) as exc_info:
                await handler(
                    payload=payload,
                    request=request,
                    db=unused_db,
                )

        self.assertIs(exc_info.exception, expected_error)
        request.body.assert_called_once_with()
        request.body.assert_awaited_once_with()
        self.assertEqual(request.body.call_count, 1)
        self.assertEqual(request.body.await_count, 1)
        self.assertEqual(request.headers_property_access_count, 0)
        self.assertEqual(request.poison_headers.access_count, 0)

        verify_mock.assert_not_called()
        select_mock.assert_not_called()
        reset_mock.assert_not_called()
        reset_mock.assert_not_awaited()
        clear_mock.assert_not_called()
        clear_mock.assert_not_awaited()

        self.assertEqual(len(_FENCE["denied_connects"]), denied_before)
        self.assertEqual(len(_FENCE["live_redis_constructs"]), live_redis_before)
        self.assertIs(sessions_router.internal_reset_user_sessions, _ORIGINAL_RESET_HANDLER)
        self.assertIs(sessions_router.internal_replace_user_sessions, _ORIGINAL_REPLACE_HANDLER)

    async def test_reset_body_failure_stops_before_headers_and_db(self) -> None:
        await self._assert_body_failure_stops_before_headers_and_db(
            internal_reset_user_sessions,
            user_id=9101,
            mobile_number="09120009101",
            source_server="synthetic-peer-reset-body-failure",
            error_message="synthetic-reset-request-body-read-failure",
        )

    async def test_replace_body_failure_stops_before_headers_and_db(self) -> None:
        await self._assert_body_failure_stops_before_headers_and_db(
            internal_replace_user_sessions,
            user_id=9202,
            mobile_number="09120009202",
            source_server="synthetic-peer-replace-body-failure",
            error_message="synthetic-replace-request-body-read-failure",
        )


if __name__ == "__main__":
    unittest.main()
