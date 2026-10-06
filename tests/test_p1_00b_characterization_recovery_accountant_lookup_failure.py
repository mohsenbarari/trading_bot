from __future__ import annotations
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

from contextlib import ExitStack
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import Request

from api.routers import sessions
from models.user import User, UserRole

DOWNSTREAM_ASYNC_TARGETS = (
    "_ensure_recovery_admin_access",
    "_clear_recovery_admin_action_messages",
    "provision_session_for_login_request",
    "_store_temporary_refresh_token",
    "_publish_recovery_prompt_updates",
)

DOWNSTREAM_SYNC_TARGETS = (
    "select",
    "approve_recovery_request",
    "reject_recovery_request",
    "create_refresh_token",
    "_get_recovery_token_cache_key",
    "send_sms",
    "build_recovery_approved_sms_text",
    "build_recovery_rejected_sms_text",
    "session_to_dict",
    "recovery_request_to_dict",
)


class RecoveryAccountantLookupFailureCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization tests for recovery accountant lookup failure propagation.

    Seeds:
    - API-AUTH-POST-SESSIONS-APPROVE-SINGLE-SESSION-RECOVERY-L845
    - API-AUTH-POST-SESSIONS-REJECT-SINGLE-SESSION-RECOVERY-L907

    Canonical symbols:
    - api.routers.sessions.approve_single_session_recovery
    - api.routers.sessions.reject_single_session_recovery
    - api.routers.sessions.ensure_non_accountant_session_management
    - core.services.accountant_relation_service.is_user_accountant

    Scenario limits:
    - Excludes successful approval/rejection, HTTP/Depends/authentication/authorization,
      actual accountant queries, SQL, persistence, transaction behavior, concurrency,
      retry, reload, origin cells, canonical seed promotion/blocker/Stage closure,
      production/deploy/DNS/Writer/secrets/network. No auxiliary acceptance inflation.

    Prior units:
    - defer_prior_unit=normal346-login-lookup-ledger-association-20261006-c002
    - defer_prior_unit=normal331-login-request-guard-ordering-20261006-c003
    """

    async def _assert_recovery_handler_accountant_lookup_failure(
        self,
        *,
        action: str,
    ) -> None:
        db = object()
        self.assertIs(type(db), object)

        user = User(
            id=1001,
            account_name="fixture_recovery_operator",
            mobile_number="09121112233",
            normalized_account_name="fixture_recovery_operator",
            normalized_mobile_number="09121112233",
            full_name="Fixture Recovery Operator",
            address="Tehran, District 1, Demo Street",
            role=UserRole.STANDARD,
        )
        user.is_accountant = False

        actor_before = {
            "id": user.id,
            "account_name": user.account_name,
            "mobile_number": user.mobile_number,
            "normalized_account_name": user.normalized_account_name,
            "normalized_mobile_number": user.normalized_mobile_number,
            "full_name": user.full_name,
            "address": user.address,
            "role": user.role,
            "is_accountant": getattr(user, "is_accountant", None),
        }

        sentinel = RuntimeError(f"simulated-accountant-lookup-failure-{action}")
        lookup_mock = AsyncMock(side_effect=sentinel)
        real_guard = sessions.ensure_non_accountant_session_management
        guard_spy = AsyncMock(wraps=real_guard)

        uuid_spy = Mock()
        uuid_spy.UUID = Mock(
            side_effect=AssertionError("sessions.uuid.UUID fail-fast: must not be called")
        )

        downstream_async_mocks: dict[str, AsyncMock] = {}
        downstream_sync_mocks: dict[str, Mock] = {}

        with ExitStack() as stack:
            stack.enter_context(patch.object(sessions, "is_user_accountant", lookup_mock))
            stack.enter_context(
                patch.object(sessions, "ensure_non_accountant_session_management", guard_spy)
            )
            stack.enter_context(patch.object(sessions, "uuid", uuid_spy))

            for target in DOWNSTREAM_ASYNC_TARGETS:
                m = AsyncMock(
                    side_effect=AssertionError(f"{target} fail-fast: must not be called")
                )
                downstream_async_mocks[target] = m
                stack.enter_context(patch.object(sessions, target, m))

            for target in DOWNSTREAM_SYNC_TARGETS:
                m = Mock(
                    side_effect=AssertionError(f"{target} fail-fast: must not be called")
                )
                downstream_sync_mocks[target] = m
                stack.enter_context(patch.object(sessions, target, m))

            dummy_request: Mock | None = None
            if action == "approve":
                dummy_request = Mock(spec=Request)
                with self.assertRaises(RuntimeError) as cm:
                    await sessions.approve_single_session_recovery(
                        recovery_id="malformed-recovery-id",
                        request=dummy_request,
                        db=db,
                        current_user=user,
                    )
            elif action == "reject":
                with self.assertRaises(RuntimeError) as cm:
                    await sessions.reject_single_session_recovery(
                        recovery_id="malformed-recovery-id",
                        db=db,
                        current_user=user,
                    )
            else:
                raise ValueError(f"Unknown action: {action}")

            self.assertIs(cm.exception, sentinel)

        # Assert guard call and await contract
        self.assertEqual(guard_spy.call_count, 1)
        self.assertEqual(guard_spy.await_count, 1)
        guard_spy.assert_awaited_once_with(db, user)
        guard_args, _ = guard_spy.await_args
        self.assertIs(guard_args[0], db)
        self.assertIs(guard_args[1], user)

        # Assert accountant lookup call and await contract
        self.assertEqual(lookup_mock.call_count, 1)
        self.assertEqual(lookup_mock.await_count, 1)
        lookup_mock.assert_awaited_once_with(db, user.id)
        lookup_args, _ = lookup_mock.await_args
        self.assertIs(lookup_args[0], db)
        self.assertEqual(lookup_args[1], user.id)

        # Assert actor state is completely unchanged
        actor_after = {
            "id": user.id,
            "account_name": user.account_name,
            "mobile_number": user.mobile_number,
            "normalized_account_name": user.normalized_account_name,
            "normalized_mobile_number": user.normalized_mobile_number,
            "full_name": user.full_name,
            "address": user.address,
            "role": user.role,
            "is_accountant": getattr(user, "is_accountant", None),
        }
        self.assertEqual(actor_after, actor_before)
        self.assertIs(user.is_accountant, False)
        self.assertEqual(user.role, UserRole.STANDARD)

        # Assert UUID parsing was never attempted
        self.assertEqual(uuid_spy.UUID.call_count, 0)

        # Assert dummy request was never read if supplied
        if dummy_request is not None:
            self.assertEqual(len(dummy_request.mock_calls), 0)

        # Assert zero-call and zero-await across all downstream boundaries
        for target, m in downstream_async_mocks.items():
            self.assertEqual(m.call_count, 0, f"{target} should not have been called")
            self.assertEqual(m.await_count, 0, f"{target} should not have been awaited")
        for target, m in downstream_sync_mocks.items():
            self.assertEqual(m.call_count, 0, f"{target} should not have been called")

    async def test_approve_recovery_propagates_accountant_lookup_exception_before_uuid(
        self,
    ) -> None:
        await self._assert_recovery_handler_accountant_lookup_failure(action="approve")

    async def test_reject_recovery_propagates_accountant_lookup_exception_before_uuid(
        self,
    ) -> None:
        await self._assert_recovery_handler_accountant_lookup_failure(action="reject")


if __name__ == "__main__":
    unittest.main()
