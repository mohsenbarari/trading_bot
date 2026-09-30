"""Hermetic characterization tests for api.routers.sessions.terminate_session.

Stage card: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment: WA-P1-00B-EVIDENCE-RECONCILE-20260930-051
Surface behavior ID: docs/refactor/two-server/section-1/inventory/surface-api.json#API-AUTH-DELETE-SESSIONS-TERMINATE-SESSION-L1075
Seed-gap reference: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Scenario key: P1-00B-SEED-AUTH-TERMINATE-MALFORMED-REAL-GUARD-V1

This module provides bounded characterization evidence only. It does NOT claim seed
promotion, P1-00b Stage closure, or completion of any other stage.
"""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from fastapi import HTTPException
from jose import jwt as jose_jwt

from api.routers.sessions import (
    ACCOUNTANT_SESSION_MANAGEMENT_DETAIL,
    terminate_session,
)
from core.config import settings
from models.user import User, UserRole


class FailingAsyncDB:
    """Async database double whose operations fail if reached."""

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("DB execute boundary should not have been reached")

    async def commit(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("DB commit boundary should not have been reached")

    async def flush(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("DB flush boundary should not have been reached")

    async def refresh(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("DB refresh boundary should not have been reached")

    async def rollback(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("DB rollback boundary should not have been reached")

    def add(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("DB add boundary should not have been reached")

    def delete(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("DB delete boundary should not have been reached")


def make_request(token: str | None = None) -> SimpleNamespace:
    """Construct a minimal request double with authorization headers."""
    headers: dict[str, str] = {}
    if token is not None:
        headers["authorization"] = f"Bearer {token}"
    return SimpleNamespace(headers=headers)


class TerminateSessionMalformedGuardCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic characterization tests for terminate_session guard precedence and malformed UUID."""

    def setUp(self) -> None:
        if not getattr(settings, "jwt_secret_key", None):
            settings.jwt_secret_key = "characterization-test-secret-key"
        if not getattr(settings, "jwt_algorithm", None):
            settings.jwt_algorithm = "HS256"

    async def test_valid_bearer_and_real_guard_precede_malformed_uuid_rejection(self) -> None:
        """Verify valid bearer parsing and real guard execution precede malformed UUID rejection."""
        user_id = 42
        user = User(
            id=user_id,
            account_name="test_standard_user",
            mobile_number="+989123456789",
            normalized_account_name="test_standard_user",
            normalized_mobile_number="+989123456789",
            full_name="Standard Test User",
            address="Tehran, District 3",
            role=UserRole.STANDARD,
            is_deleted=False,
            has_bot_access=True,
        )

        caller_sid = uuid.uuid4()
        token = jose_jwt.encode(
            {"sid": str(caller_sid)},
            settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )
        request = make_request(token=token)
        db = FailingAsyncDB()
        malformed_session_id = "malformed-not-a-uuid-999"

        is_accountant_mock = AsyncMock(return_value=False)
        logout_mock = AsyncMock()
        get_active_mock = AsyncMock()
        audit_mock = MagicMock()

        with patch(
            "api.routers.sessions.is_user_accountant",
            is_accountant_mock,
        ), patch(
            "api.routers.sessions.logout_session",
            logout_mock,
        ), patch(
            "api.routers.sessions.get_active_sessions",
            get_active_mock,
        ), patch(
            "api.routers.sessions.audit_log",
            audit_mock,
        ):
            with self.assertRaises(HTTPException) as exc_ctx:
                await terminate_session(
                    session_id=malformed_session_id,
                    request=request,
                    db=db,
                    current_user=user,
                )

        self.assertEqual(exc_ctx.exception.status_code, 400)
        self.assertEqual(exc_ctx.exception.detail, "شناسه نشست نامعتبر است")

        is_accountant_mock.assert_awaited_once_with(db, user_id)
        logout_mock.assert_not_called()
        get_active_mock.assert_not_called()
        audit_mock.assert_not_called()

    async def test_accountant_guard_rejects_before_malformed_uuid_path(self) -> None:
        """Verify runtime User.is_accountant=True triggers 403 before lookup, UUID check, or DB work."""
        user_id = 42
        user = User(
            id=user_id,
            account_name="test_standard_user",
            mobile_number="+989123456789",
            normalized_account_name="test_standard_user",
            normalized_mobile_number="+989123456789",
            full_name="Standard Test User",
            address="Tehran, District 3",
            role=UserRole.STANDARD,
            is_deleted=False,
            has_bot_access=True,
        )
        user.is_accountant = True

        caller_sid = uuid.uuid4()
        token = jose_jwt.encode(
            {"sid": str(caller_sid)},
            settings.jwt_secret_key,
            algorithm=settings.jwt_algorithm,
        )
        request = make_request(token=token)
        db = FailingAsyncDB()
        malformed_session_id = "malformed-not-a-uuid-999"

        is_accountant_mock = AsyncMock(return_value=False)
        logout_mock = AsyncMock()
        get_active_mock = AsyncMock()
        audit_mock = MagicMock()

        with patch(
            "api.routers.sessions.is_user_accountant",
            is_accountant_mock,
        ), patch(
            "api.routers.sessions.logout_session",
            logout_mock,
        ), patch(
            "api.routers.sessions.get_active_sessions",
            get_active_mock,
        ), patch(
            "api.routers.sessions.audit_log",
            audit_mock,
        ):
            with self.assertRaises(HTTPException) as exc_ctx:
                await terminate_session(
                    session_id=malformed_session_id,
                    request=request,
                    db=db,
                    current_user=user,
                )

        self.assertEqual(exc_ctx.exception.status_code, 403)
        self.assertEqual(exc_ctx.exception.detail, ACCOUNTANT_SESSION_MANAGEMENT_DETAIL)
        self.assertEqual(
            exc_ctx.exception.detail,
            "حسابداران به مدیریت نشست و خروج از حساب کاربری دسترسی ندارند.",
        )

        is_accountant_mock.assert_not_called()
        logout_mock.assert_not_called()
        get_active_mock.assert_not_called()
        audit_mock.assert_not_called()


if __name__ == "__main__":
    unittest.main()
