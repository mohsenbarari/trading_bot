"""Characterization tests for accountant lookup exception propagation in session login actions.

Seeds:
- API-AUTH-POST-SESSIONS-APPROVE-REQUEST-L1210
- API-AUTH-POST-SESSIONS-REJECT-REQUEST-L1270

Stage: P1-00b
Gap: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
defer_prior_unit=normal331-login-request-guard-ordering-20261006-c003

Scenario limits:
- Authoritative unpersisted User with is_accountant explicitly False and valid UserRole.STANDARD
- Real ensure_non_accountant_session_management executed via AsyncMock wraps spy
- Only sessions.is_user_accountant replaced with AsyncMock raising fresh RuntimeError
- Actual approve_request and reject_request handlers invoked with malformed request_id
- Escaped exception asserted identical before UUID parsing, SQL construction, DB access, service calls, tokens, audit
- Characterization only: zero seed promotion or Stage/blocker/origin closure.
"""

from __future__ import annotations

import os

os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

import contextlib
from dataclasses import dataclass
from typing import Any
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

import api.routers.sessions as sessions_router
from core.enums import UserAccountStatus
from models.user import User, UserRole


class _UnreadRequest:
    """Dummy request for approve_request whose attributes must remain unread."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"Request attribute {name!r} was read unexpectedly")

    def __getitem__(self, key: Any) -> Any:
        raise AssertionError(f"Request item {key!r} was read unexpectedly")


@dataclass
class _HandlerHarness:
    db: object
    user: User
    lookup_exc: RuntimeError
    guard_spy: AsyncMock
    lookup_mock: AsyncMock
    mock_select: MagicMock
    mock_uuid_cls: MagicMock
    mock_uuid_module: MagicMock
    mock_approve_login: AsyncMock
    mock_reject_login: AsyncMock
    mock_create_refresh: MagicMock
    mock_store_refresh: AsyncMock
    mock_session_to_dict: MagicMock
    mock_audit_log: MagicMock
    mock_audit_role: MagicMock


class LoginActionsAccountantLookupFailureCharacterizationTests(
    unittest.IsolatedAsyncioTestCase
):
    """Characterization test suite for login actions accountant lookup failure."""

    @contextlib.contextmanager
    def _mount_handler_harness(self):
        db = object()
        user = User(
            id=7701,
            account_name="char_test_user_7701",
            mobile_number="+989120007701",
            normalized_account_name="char_test_user_7701",
            normalized_mobile_number="09120007701",
            full_name="Characterization Test User",
            address="Tehran, District 1",
            role=UserRole.STANDARD,
            account_status=UserAccountStatus.ACTIVE,
        )
        user.is_accountant = False

        lookup_exc = RuntimeError(f"accountant-lookup-failed-{uuid.uuid4()}")

        real_guard = sessions_router.ensure_non_accountant_session_management
        guard_spy = AsyncMock(wraps=real_guard)
        lookup_mock = AsyncMock(side_effect=lookup_exc)

        mock_select = MagicMock(
            side_effect=AssertionError("sessions.select was invoked unexpectedly")
        )
        mock_uuid_cls = MagicMock(
            side_effect=AssertionError("uuid.UUID was invoked unexpectedly")
        )
        mock_uuid_module = MagicMock()
        mock_uuid_module.UUID = mock_uuid_cls

        mock_approve_login = AsyncMock(
            side_effect=AssertionError(
                "approve_login_request was invoked unexpectedly"
            )
        )
        mock_reject_login = AsyncMock(
            side_effect=AssertionError(
                "reject_login_request was invoked unexpectedly"
            )
        )
        mock_create_refresh = MagicMock(
            side_effect=AssertionError(
                "create_refresh_token was invoked unexpectedly"
            )
        )
        mock_store_refresh = AsyncMock(
            side_effect=AssertionError(
                "_store_temporary_refresh_token was invoked unexpectedly"
            )
        )
        mock_session_to_dict = MagicMock(
            side_effect=AssertionError("session_to_dict was invoked unexpectedly")
        )
        mock_audit_log = MagicMock(
            side_effect=AssertionError("audit_log was invoked unexpectedly")
        )
        mock_audit_role = MagicMock(
            side_effect=AssertionError(
                "_audit_actor_role was invoked unexpectedly"
            )
        )

        with (
            patch.object(
                sessions_router,
                "ensure_non_accountant_session_management",
                guard_spy,
            ),
            patch.object(sessions_router, "is_user_accountant", lookup_mock),
            patch.object(sessions_router, "select", mock_select),
            patch.object(sessions_router, "uuid", mock_uuid_module),
            patch.object(
                sessions_router, "approve_login_request", mock_approve_login
            ),
            patch.object(
                sessions_router, "reject_login_request", mock_reject_login
            ),
            patch.object(
                sessions_router, "create_refresh_token", mock_create_refresh
            ),
            patch.object(
                sessions_router,
                "_store_temporary_refresh_token",
                mock_store_refresh,
            ),
            patch.object(
                sessions_router, "session_to_dict", mock_session_to_dict
            ),
            patch.object(sessions_router, "audit_log", mock_audit_log),
            patch.object(sessions_router, "_audit_actor_role", mock_audit_role),
        ):
            yield _HandlerHarness(
                db=db,
                user=user,
                lookup_exc=lookup_exc,
                guard_spy=guard_spy,
                lookup_mock=lookup_mock,
                mock_select=mock_select,
                mock_uuid_cls=mock_uuid_cls,
                mock_uuid_module=mock_uuid_module,
                mock_approve_login=mock_approve_login,
                mock_reject_login=mock_reject_login,
                mock_create_refresh=mock_create_refresh,
                mock_store_refresh=mock_store_refresh,
                mock_session_to_dict=mock_session_to_dict,
                mock_audit_log=mock_audit_log,
                mock_audit_role=mock_audit_role,
            )

    def _assert_isolated_failure_proof(self, harness: _HandlerHarness) -> None:
        # Exactly one call and one await with exact arguments and object identity on real guard spy
        harness.guard_spy.assert_called_once_with(harness.db, harness.user)
        harness.guard_spy.assert_awaited_once_with(harness.db, harness.user)
        self.assertEqual(harness.guard_spy.call_count, 1)
        self.assertEqual(harness.guard_spy.await_count, 1)
        self.assertIs(harness.guard_spy.call_args[0][0], harness.db)
        self.assertIs(harness.guard_spy.call_args[0][1], harness.user)
        self.assertIs(harness.guard_spy.await_args[0][0], harness.db)
        self.assertIs(harness.guard_spy.await_args[0][1], harness.user)

        # Exactly one call and one await with exact arguments and db/user id identity on lookup mock
        harness.lookup_mock.assert_called_once_with(harness.db, harness.user.id)
        harness.lookup_mock.assert_awaited_once_with(harness.db, harness.user.id)
        self.assertEqual(harness.lookup_mock.call_count, 1)
        self.assertEqual(harness.lookup_mock.await_count, 1)
        self.assertIs(harness.lookup_mock.call_args[0][0], harness.db)
        self.assertEqual(harness.lookup_mock.call_args[0][1], harness.user.id)
        self.assertIs(harness.lookup_mock.await_args[0][0], harness.db)
        self.assertEqual(harness.lookup_mock.await_args[0][1], harness.user.id)

        # Downstream async mocks must have zero calls and zero awaits
        for async_mock in (
            harness.mock_approve_login,
            harness.mock_reject_login,
            harness.mock_store_refresh,
        ):
            async_mock.assert_not_called()
            async_mock.assert_not_awaited()
            self.assertEqual(async_mock.call_count, 0)
            self.assertEqual(async_mock.await_count, 0)

        # Downstream sync mocks must have zero calls
        for sync_mock in (
            harness.mock_select,
            harness.mock_uuid_cls,
            harness.mock_uuid_module,
            harness.mock_create_refresh,
            harness.mock_session_to_dict,
            harness.mock_audit_log,
            harness.mock_audit_role,
        ):
            sync_mock.assert_not_called()
            self.assertEqual(sync_mock.call_count, 0)

    async def test_approve_request_propagates_accountant_lookup_exception_before_uuid(
        self,
    ) -> None:
        with self._mount_handler_harness() as harness:
            request = _UnreadRequest()
            malformed_request_id = "malformed-approve-request-id-not-a-uuid"

            with self.assertRaises(RuntimeError) as ctx:
                await sessions_router.approve_request(
                    request_id=malformed_request_id,
                    request=request,  # type: ignore[arg-type]
                    db=harness.db,  # type: ignore[arg-type]
                    current_user=harness.user,
                )

            self.assertIs(ctx.exception, harness.lookup_exc)
            self._assert_isolated_failure_proof(harness)

    async def test_reject_request_propagates_accountant_lookup_exception_before_uuid(
        self,
    ) -> None:
        with self._mount_handler_harness() as harness:
            malformed_request_id = "malformed-reject-request-id-not-a-uuid"

            with self.assertRaises(RuntimeError) as ctx:
                await sessions_router.reject_request(
                    request_id=malformed_request_id,
                    db=harness.db,  # type: ignore[arg-type]
                    current_user=harness.user,
                )

            self.assertIs(ctx.exception, harness.lookup_exc)
            self._assert_isolated_failure_proof(harness)


if __name__ == "__main__":
    unittest.main()
