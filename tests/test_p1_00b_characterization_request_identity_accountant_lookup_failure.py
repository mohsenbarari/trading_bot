from __future__ import annotations
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

from contextlib import ExitStack
import unittest
from unittest.mock import AsyncMock, Mock, patch

from api.routers import sessions
from models.user import User, UserRole

DOWNSTREAM_ASYNC_TARGETS = (
    "_ensure_recovery_admin_access",
    "_clear_recovery_admin_action_messages",
    "_publish_recovery_prompt_updates",
)

DOWNSTREAM_SYNC_TARGETS = (
    "request_identity_verification",
    "build_identity_requested_sms_text",
    "send_sms",
    "recovery_request_to_dict",
)


class RequestIdentityAccountantLookupFailureCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterization tests for recovery identity request accountant lookup failure propagation.

    Provenance:
    - Stage: P1-00b (parent WA-P1-00B-FREEZE-20260904-001)
    - Assignment: WA-P1-00B-NORMAL-FOUNDATION-RETURN-20261006-346
    - Handler: api/routers/sessions.py:request_single_session_recovery_identity
    - Seed: API-AUTH-POST-SESSIONS-REQUEST-SINGLE-SESSION-RECOVERY-IDENTITY-L807
    - Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
    - Scenario: request-identity-accountant-lookup-exception-before-uuid

    Scenario exclusions:
    - Excludes successful identity request, real accountant query, real admin authorization,
      HTTP/auth/Depends, real DB/SQL/session/transaction/persistence, SMS/publication behavior,
      concurrency/retry/reload, origin cells, seed/blocker/Stage closure, fresh historical
      tests, ledger reconciliation, source changes, or coverage promotion.
    """

    async def test_lookup_exception_propagates_before_uuid_and_admin_access(self) -> None:
        db = object()
        self.assertIs(type(db), object)

        user = User(
            id=42001,
            account_name="fixture_identity_operator",
            mobile_number="09120000001",
            normalized_account_name="fixture_identity_operator",
            normalized_mobile_number="09120000001",
            full_name="Fixture Identity Operator",
            address="Tehran, Province Tehran, Test Office 42",
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

        sentinel = RuntimeError("simulated-accountant-lookup-failure-request-identity")
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

            with self.assertRaises(RuntimeError) as cm:
                await sessions.request_single_session_recovery_identity(
                    recovery_id="malformed-recovery-id-not-a-uuid",
                    db=db,
                    current_user=user,
                )

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

        # Selected-field immutability check (never full-row or persistence proof)
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

        # Assert zero-call and zero-await across all downstream boundaries
        for target, m in downstream_async_mocks.items():
            self.assertEqual(m.call_count, 0, f"{target} should not have been called")
            self.assertEqual(m.await_count, 0, f"{target} should not have been awaited")
        for target, m in downstream_sync_mocks.items():
            self.assertEqual(m.call_count, 0, f"{target} should not have been called")


if __name__ == "__main__":
    unittest.main()
