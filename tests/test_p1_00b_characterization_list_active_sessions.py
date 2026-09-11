"""Hermetic direct-handler characterization unit for GET /active (list_active_sessions).

Under Stage card docs/refactor/two-server/section-1/stages/P1-00b.md and Assignment
WA-P1-00B-FREEZE-20260904-001, this test module provides bounded characterization
evidence for docs/refactor/two-server/section-1/inventory/surface-api.json#API-AUTH-GET-SESSIONS-LIST-ACTIVE-SESSIONS-L1046
under docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE.

Evidence promotion and Stage closure remain separate work; this test does not close
the seed blocker or complete Stage P1-00b.
"""

from __future__ import annotations

from datetime import datetime, timezone
import unittest
from unittest.mock import AsyncMock, patch
import uuid

from starlette.requests import Request

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

# Install the existing P1-00 fail-closed network fence before importing the sessions router.
install_p1_00_network_fence()

from jose import jwt  # noqa: E402

from api.routers.sessions import list_active_sessions  # noqa: E402
from core.config import settings  # noqa: E402
from models.session import Platform, UserSession  # noqa: E402
from models.user import User, UserRole  # noqa: E402


SESSION_1_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
SESSION_2_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")


def _build_runtime_user(user_id: int = 42) -> User:
    return User(
        id=user_id,
        account_name="test_characterization_user",
        mobile_number="09123456789",
        role=UserRole.STANDARD,
    )


def _build_runtime_sessions() -> tuple[UserSession, UserSession]:
    s1 = UserSession(
        id=SESSION_1_ID,
        user_id=42,
        device_name="Chrome on Linux",
        device_ip="192.168.1.50",
        platform=Platform.WEB,
        home_server="iran",
        is_primary=True,
        is_active=True,
        created_at=datetime(2026, 9, 11, 10, 0, 0, tzinfo=timezone.utc),
        last_active_at=datetime(2026, 9, 11, 11, 30, 0, tzinfo=timezone.utc),
    )
    s2 = UserSession(
        id=SESSION_2_ID,
        user_id=42,
        device_name="Firefox on Android",
        device_ip=None,
        platform="desktop_custom",
        home_server=None,
        is_primary=False,
        is_active=True,
        created_at=None,
        last_active_at=None,
    )
    s2.device_ip = None
    s2.platform = "desktop_custom"
    s2.home_server = None
    s2.created_at = None
    s2.last_active_at = None
    return s1, s2


def _make_request(auth_header: str | None = None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if auth_header is not None:
        headers.append((b"authorization", auth_header.encode("ascii")))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/active",
        "headers": headers,
    }
    return Request(scope)


class ListActiveSessionsCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic direct-handler characterization for api.routers.sessions.list_active_sessions."""

    async def test_list_active_sessions_valid_bearer_token_marks_current_session(
        self,
    ) -> None:
        """Characterize valid bearer token: sid matches first session and is_current is true only for that row."""
        sentinel_db = object()
        current_user = _build_runtime_user(user_id=42)
        s1, s2 = _build_runtime_sessions()

        token = jwt.encode(
            {"sid": str(SESSION_1_ID)},
            settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )
        request = _make_request(auth_header=f"Bearer {token}")

        expected_sessions = [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "device_name": "Chrome on Linux",
                "device_ip": "192.168.1.50",
                "platform": "web",
                "home_server": "iran",
                "is_primary": True,
                "is_active": True,
                "created_at": "2026-09-11T10:00:00+00:00",
                "last_active_at": "2026-09-11T11:30:00+00:00",
                "is_current": True,
            },
            {
                "id": "22222222-2222-2222-2222-222222222222",
                "device_name": "Firefox on Android",
                "device_ip": None,
                "platform": "desktop_custom",
                "home_server": "foreign",
                "is_primary": False,
                "is_active": True,
                "created_at": None,
                "last_active_at": None,
                "is_current": False,
            },
        ]

        with patch(
            "api.routers.sessions.ensure_non_accountant_session_management",
            new_callable=AsyncMock,
        ) as mock_guard, patch(
            "api.routers.sessions.get_active_sessions",
            new_callable=AsyncMock,
        ) as mock_get_active_sessions:
            mock_get_active_sessions.return_value = [s1, s2]

            result = await list_active_sessions(
                request=request,
                db=sentinel_db,
                current_user=current_user,
            )

            mock_guard.assert_awaited_once_with(sentinel_db, current_user)
            mock_get_active_sessions.assert_awaited_once_with(
                sentinel_db, current_user.id
            )
            self.assertEqual(len(result), 2)
            self.assertTrue(result[0]["is_current"])
            self.assertFalse(result[1]["is_current"])
            self.assertEqual(result, expected_sessions)

    async def test_list_active_sessions_missing_authorization_header(self) -> None:
        """Characterize missing authorization header: sessions returned with is_current false."""
        sentinel_db = object()
        current_user = _build_runtime_user(user_id=42)
        s1, s2 = _build_runtime_sessions()
        request = _make_request(auth_header=None)

        expected_sessions = [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "device_name": "Chrome on Linux",
                "device_ip": "192.168.1.50",
                "platform": "web",
                "home_server": "iran",
                "is_primary": True,
                "is_active": True,
                "created_at": "2026-09-11T10:00:00+00:00",
                "last_active_at": "2026-09-11T11:30:00+00:00",
                "is_current": False,
            },
            {
                "id": "22222222-2222-2222-2222-222222222222",
                "device_name": "Firefox on Android",
                "device_ip": None,
                "platform": "desktop_custom",
                "home_server": "foreign",
                "is_primary": False,
                "is_active": True,
                "created_at": None,
                "last_active_at": None,
                "is_current": False,
            },
        ]

        with patch(
            "api.routers.sessions.ensure_non_accountant_session_management",
            new_callable=AsyncMock,
        ) as mock_guard, patch(
            "api.routers.sessions.get_active_sessions",
            new_callable=AsyncMock,
        ) as mock_get_active_sessions:
            mock_get_active_sessions.return_value = [s1, s2]

            result = await list_active_sessions(
                request=request,
                db=sentinel_db,
                current_user=current_user,
            )

            mock_guard.assert_awaited_once_with(sentinel_db, current_user)
            mock_get_active_sessions.assert_awaited_once_with(
                sentinel_db, current_user.id
            )
            self.assertEqual(len(result), 2)
            self.assertFalse(result[0]["is_current"])
            self.assertFalse(result[1]["is_current"])
            self.assertEqual(result, expected_sessions)

    async def test_list_active_sessions_undecodable_bearer_token(self) -> None:
        """Characterize undecodable Bearer token: decode error swallowed and is_current is false."""
        sentinel_db = object()
        current_user = _build_runtime_user(user_id=42)
        s1, s2 = _build_runtime_sessions()
        undecodable_token = "actually-undecodable-jwt-token"
        request = _make_request(auth_header=f"Bearer {undecodable_token}")

        expected_sessions = [
            {
                "id": "11111111-1111-1111-1111-111111111111",
                "device_name": "Chrome on Linux",
                "device_ip": "192.168.1.50",
                "platform": "web",
                "home_server": "iran",
                "is_primary": True,
                "is_active": True,
                "created_at": "2026-09-11T10:00:00+00:00",
                "last_active_at": "2026-09-11T11:30:00+00:00",
                "is_current": False,
            },
            {
                "id": "22222222-2222-2222-2222-222222222222",
                "device_name": "Firefox on Android",
                "device_ip": None,
                "platform": "desktop_custom",
                "home_server": "foreign",
                "is_primary": False,
                "is_active": True,
                "created_at": None,
                "last_active_at": None,
                "is_current": False,
            },
        ]

        with patch(
            "api.routers.sessions.ensure_non_accountant_session_management",
            new_callable=AsyncMock,
        ) as mock_guard, patch(
            "api.routers.sessions.get_active_sessions",
            new_callable=AsyncMock,
        ) as mock_get_active_sessions:
            mock_get_active_sessions.return_value = [s1, s2]

            result = await list_active_sessions(
                request=request,
                db=sentinel_db,
                current_user=current_user,
            )

            mock_guard.assert_awaited_once_with(sentinel_db, current_user)
            mock_get_active_sessions.assert_awaited_once_with(
                sentinel_db, current_user.id
            )
            self.assertEqual(len(result), 2)
            self.assertFalse(result[0]["is_current"])
            self.assertFalse(result[1]["is_current"])
            self.assertEqual(result, expected_sessions)


if __name__ == "__main__":
    unittest.main()
