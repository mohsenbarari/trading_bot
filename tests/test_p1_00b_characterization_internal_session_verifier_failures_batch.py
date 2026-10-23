"""Bounded P1-00b characterization for internal session signature-verifier exceptions.

Stage: P1-00b (IN_PROGRESS; P1-00 COMPLETE)
Assignment: WA-P1-00B-BUSINESS-LIVE-20261023-799B (parent WA-P1-00B-FREEZE-20260904-001)
Gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Scenario key: P1-00B-BATCH-V1.internal-session-signature-verifier-exceptions
Referenced seeds (NOT promoted):
- API-AUTH-POST-SESSIONS-INTERNAL-RESET-USER-SESSIONS-L562
- API-AUTH-POST-SESSIONS-INTERNAL-REPLACE-USER-SESSIONS-L593

Bounded scope and explicit exclusions:
Characterizes only synchronous `verify_session_authority_signature` exception propagation
after `await request.body()` and `request.headers.get` reads in
`internal_reset_user_sessions` and `internal_replace_user_sessions`, stopping before SQL
construction or session reset/revocation side effects. Explicit exclusions: actual
signature/HMAC validation, body parsing/failure, HTTP/Depends/authorization wiring,
missing-user/mobile-mismatch/success paths already covered, SQL execution and all
persistence/retry/reload/origin interactions; whole-handler/seed/HIGH/Stage closure.
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
_EXPECTED_HEADER_KEYS = ["X-Timestamp", "X-Signature", "X-API-Key"]


class _RecordedHeaders:
    def __init__(
        self,
        header_values: dict[str, str],
        event_log: list[tuple[object, ...]],
    ) -> None:
        self._header_values = dict(header_values)
        self._event_log = event_log
        self.get_keys: list[str] = []
        self.get_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def get(self, *args: object, **kwargs: object) -> str:
        self.get_calls.append((args, dict(kwargs)))
        if len(args) != 1 or kwargs or not isinstance(args[0], str):
            raise AssertionError(
                f"Unexpected headers.get signature: args={args!r}, kwargs={kwargs!r}"
            )
        key = args[0]
        if key not in self._header_values:
            raise AssertionError(f"Unexpected header key requested: {key!r}")
        self.get_keys.append(key)
        self._event_log.append(("headers.get", key))
        return self._header_values[key]

    def __getitem__(self, key: str) -> str:
        raise AssertionError(f"Unexpected headers[{key!r}] indexing access")


class _VerifierFailureRequest:
    def __init__(
        self,
        *,
        body_bytes: bytes,
        header_values: dict[str, str],
        event_log: list[tuple[object, ...]],
    ) -> None:
        self._body_bytes = body_bytes
        self._event_log = event_log
        self.headers_property_access_count = 0
        self.recorded_headers = _RecordedHeaders(header_values, event_log)
        self.body = AsyncMock(side_effect=self._read_body)

    async def _read_body(self) -> bytes:
        self._event_log.append(("request.body",))
        return self._body_bytes

    @property
    def headers(self) -> _RecordedHeaders:
        self.headers_property_access_count += 1
        return self.recorded_headers


class InternalSessionVerifierFailuresCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    async def _assert_verifier_exception_stops_before_sql_and_side_effects(
        self,
        handler: Callable[..., Awaitable[object]],
        *,
        user_id: int,
        mobile_number: str,
        source_server: str,
        expected_body: bytes,
        expected_timestamp: str,
        expected_signature: str,
        expected_api_key: str,
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
        event_log: list[tuple[object, ...]] = []
        header_map = {
            "X-Timestamp": expected_timestamp,
            "X-Signature": expected_signature,
            "X-API-Key": expected_api_key,
        }
        request = _VerifierFailureRequest(
            body_bytes=expected_body,
            header_values=header_map,
            event_log=event_log,
        )
        inert_db = object()
        verifier_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

        def _raising_verifier(*args: object, **kwargs: object) -> bool:
            verifier_calls.append((args, dict(kwargs)))
            event_log.append(("verify_session_authority_signature", args, dict(kwargs)))
            raise expected_error

        verify_mock = MagicMock(side_effect=_raising_verifier)
        select_mock = MagicMock(
            side_effect=AssertionError(
                "select must not be called when verify_session_authority_signature raises"
            )
        )
        reset_mock = AsyncMock(
            side_effect=AssertionError(
                "reset_user_session_state must not be called when verify_session_authority_signature raises"
            )
        )
        clear_mock = AsyncMock(
            side_effect=AssertionError(
                "force_clear_sessions must not be called when verify_session_authority_signature raises"
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
                    db=inert_db,
                )

        self.assertIs(exc_info.exception, expected_error)
        request.body.assert_called_once_with()
        request.body.assert_awaited_once_with()
        self.assertEqual(request.body.call_count, 1)
        self.assertEqual(request.body.await_count, 1)

        self.assertEqual(request.headers_property_access_count, 3)
        self.assertEqual(request.recorded_headers.get_keys, _EXPECTED_HEADER_KEYS)
        self.assertEqual(
            request.recorded_headers.get_calls,
            [
                (("X-Timestamp",), {}),
                (("X-Signature",), {}),
                (("X-API-Key",), {}),
            ],
        )

        expected_kwargs = {
            "timestamp": expected_timestamp,
            "signature": expected_signature,
            "api_key": expected_api_key,
        }
        verify_mock.assert_called_once_with(
            expected_body,
            timestamp=expected_timestamp,
            signature=expected_signature,
            api_key=expected_api_key,
        )
        self.assertEqual(len(verifier_calls), 1)
        recorded_args, recorded_kwargs = verifier_calls[0]
        self.assertEqual(len(recorded_args), 1)
        self.assertIs(recorded_args[0], expected_body)
        self.assertEqual(recorded_args, (expected_body,))
        self.assertEqual(set(recorded_kwargs.keys()), {"timestamp", "signature", "api_key"})
        self.assertEqual(recorded_kwargs, expected_kwargs)

        self.assertEqual(
            event_log,
            [
                ("request.body",),
                ("headers.get", "X-Timestamp"),
                ("headers.get", "X-Signature"),
                ("headers.get", "X-API-Key"),
                (
                    "verify_session_authority_signature",
                    (expected_body,),
                    expected_kwargs,
                ),
            ],
        )

        select_mock.assert_not_called()
        reset_mock.assert_not_called()
        reset_mock.assert_not_awaited()
        clear_mock.assert_not_called()
        clear_mock.assert_not_awaited()

        self.assertEqual(len(_FENCE["denied_connects"]), denied_before)
        self.assertEqual(len(_FENCE["live_redis_constructs"]), live_redis_before)
        self.assertIs(sessions_router.internal_reset_user_sessions, _ORIGINAL_RESET_HANDLER)
        self.assertIs(sessions_router.internal_replace_user_sessions, _ORIGINAL_REPLACE_HANDLER)

    async def test_reset_verifier_exception_stops_before_sql_and_reset(self) -> None:
        await self._assert_verifier_exception_stops_before_sql_and_side_effects(
            internal_reset_user_sessions,
            user_id=9301,
            mobile_number="09120009301",
            source_server="synthetic-peer-reset-verifier-exception",
            expected_body=b'{"user_id":9301,"mobile_number":"09120009301","kind":"synthetic-reset"}',
            expected_timestamp="1700009301",
            expected_signature="synthetic-nonsecret-signature-reset-9301",
            expected_api_key="synthetic-nonsecret-api-key-reset-9301",
            error_message="synthetic-reset-verifier-runtime-failure",
        )

    async def test_replace_verifier_exception_stops_before_sql_and_revoke(self) -> None:
        await self._assert_verifier_exception_stops_before_sql_and_side_effects(
            internal_replace_user_sessions,
            user_id=9402,
            mobile_number="09120009402",
            source_server="synthetic-peer-replace-verifier-exception",
            expected_body=b'{"user_id":9402,"mobile_number":"09120009402","kind":"synthetic-replace"}',
            expected_timestamp="1700009402",
            expected_signature="synthetic-nonsecret-signature-replace-9402",
            expected_api_key="synthetic-nonsecret-api-key-replace-9402",
            error_message="synthetic-replace-verifier-runtime-failure",
        )


if __name__ == "__main__":
    unittest.main()
