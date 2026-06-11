import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from api.routers.sessions import InternalSessionAuthorityCheck, internal_session_authority_check
from core import session_authority
from core.session_authority import (
    ACTIVE_SESSION_ON_HOME_SERVER_MESSAGE,
    SESSION_AUTHORITY_UNAVAILABLE_MESSAGE,
    assert_login_allowed_for_server,
)


class FakeExecuteResult:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class FakeDB:
    def __init__(self, execute_results=None):
        self.execute_results = list(execute_results or [])

    async def execute(self, _stmt):
        if not self.execute_results:
            raise AssertionError("Unexpected execute() call")
        return self.execute_results.pop(0)


class SessionAuthorityTests(unittest.IsolatedAsyncioTestCase):
    async def test_assert_login_allowed_skips_current_home_server(self):
        user = SimpleNamespace(id=5, home_server="iran")

        with patch(
            "core.session_authority.fetch_remote_session_authority",
            new=AsyncMock(side_effect=AssertionError("remote check is not needed")),
        ):
            await assert_login_allowed_for_server(FakeDB(), user, requested_server="iran")

    async def test_assert_login_allowed_blocks_remote_active_sessions(self):
        user = SimpleNamespace(id=5, home_server="iran")

        with patch(
            "core.session_authority.fetch_remote_session_authority",
            new=AsyncMock(return_value=(200, {"active_session_count": 1, "can_transfer_home": False})),
        ) as remote_mock:
            with self.assertRaises(HTTPException) as exc_info:
                await assert_login_allowed_for_server(FakeDB(), user, requested_server="foreign")

        self.assertEqual(exc_info.exception.status_code, 409)
        self.assertEqual(exc_info.exception.detail, ACTIVE_SESSION_ON_HOME_SERVER_MESSAGE)
        remote_mock.assert_awaited_once_with("iran", 5)

    async def test_assert_login_allowed_allows_remote_transfer_without_active_sessions(self):
        user = SimpleNamespace(id=5, home_server="iran")

        with patch(
            "core.session_authority.fetch_remote_session_authority",
            new=AsyncMock(return_value=(200, {"active_session_count": 0, "can_transfer_home": True})),
        ):
            await assert_login_allowed_for_server(FakeDB(), user, requested_server="foreign")

    async def test_assert_login_allowed_fails_closed_when_home_server_unavailable(self):
        user = SimpleNamespace(id=5, home_server="iran")

        with patch(
            "core.session_authority.fetch_remote_session_authority",
            new=AsyncMock(return_value=(503, {"detail": "down"})),
        ):
            with self.assertRaises(HTTPException) as exc_info:
                await assert_login_allowed_for_server(FakeDB(), user, requested_server="foreign")

        self.assertEqual(exc_info.exception.status_code, 503)
        self.assertEqual(exc_info.exception.detail, SESSION_AUTHORITY_UNAVAILABLE_MESSAGE)

    async def test_internal_authority_check_requires_signature_and_returns_local_snapshot(self):
        payload_body = session_authority._json_body({"source_server": "foreign", "user_id": 5})
        timestamp = 1_700_000_000
        user = SimpleNamespace(id=5, home_server="iran")
        snapshot = {
            "user_id": 5,
            "home_server": "iran",
            "active_session_count": 0,
            "can_transfer_home": True,
        }

        async def request_body():
            return payload_body.encode()

        with patch.object(session_authority.settings, "sync_api_key", "secret"), patch(
            "core.session_authority.time.time",
            return_value=timestamp,
        ), patch(
            "api.routers.sessions.inspect_local_session_authority",
            new=AsyncMock(return_value=snapshot),
        ) as inspect_mock:
            request = SimpleNamespace(
                headers={
                    "X-API-Key": "secret",
                    "X-Timestamp": str(timestamp),
                    "X-Signature": session_authority.sign_internal_payload(payload_body, timestamp),
                },
                body=request_body,
            )
            result = await internal_session_authority_check(
                InternalSessionAuthorityCheck(user_id=5, source_server="foreign"),
                request=request,
                db=FakeDB([FakeExecuteResult(user)]),
            )

        self.assertEqual(result, snapshot)
        inspect_mock.assert_awaited_once_with(unittest.mock.ANY, user)

        bad_request = SimpleNamespace(headers={}, body=request_body)
        with self.assertRaises(HTTPException) as exc_info:
            await internal_session_authority_check(
                InternalSessionAuthorityCheck(user_id=5, source_server="foreign"),
                request=bad_request,
                db=FakeDB([FakeExecuteResult(user)]),
            )
        self.assertEqual(exc_info.exception.status_code, 401)


if __name__ == "__main__":
    unittest.main()
