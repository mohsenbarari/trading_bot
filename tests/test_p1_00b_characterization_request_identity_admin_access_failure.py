from __future__ import annotations
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

import unittest
import uuid
from unittest.mock import AsyncMock, patch

from api.routers.sessions import request_single_session_recovery_identity


class RequestIdentityAdminAccessFailureCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterize request_single_session_recovery_identity at admin-access failure boundary.

    Stage: P1-00b
    Assignment: WA-P1-00B-NORMAL-FOUNDATION-RETURN-20261006-346
    Parent Assignment: WA-P1-00B-FREEZE-20260904-001
    Handler: api/routers/sessions.py:request_single_session_recovery_identity
    Seed: API-AUTH-POST-SESSIONS-REQUEST-SINGLE-SESSION-RECOVERY-IDENTITY-L807
    Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
    Scenario: request-identity-admin-access-exception-before-side-effects
    Exclusions: real guard/admin authorization, HTTP/Depends,
        real queries/models/session/persistence/transaction,
        SMS/publication success/failure after access,
        auth/concurrency/retry/reload, origin cells, full-row immutability,
        seed/blocker/Stage closure.
    """

    async def test_access_exception_follows_guard_and_uuid_before_side_effects(self) -> None:
        valid_recovery_id_str = "e9a0c644-b04f-4d92-91a0-cb03f47c0e62"
        expected_recovery_uuid = uuid.UUID(valid_recovery_id_str)
        opaque_db = object()
        opaque_current_user = object()
        access_error = RuntimeError("admin-access check failure")

        call_events: list[str] = []

        async def fake_guard(db: object, current_user: object) -> None:
            call_events.append("guard")
            return None

        async def fake_access(
            db: object,
            *,
            recovery_id: uuid.UUID,
            current_user: object,
        ):
            call_events.append("access")
            raise access_error

        with (
            patch(
                "api.routers.sessions.ensure_non_accountant_session_management",
                new_callable=AsyncMock,
            ) as mock_guard,
            patch(
                "api.routers.sessions._ensure_recovery_admin_access",
                new_callable=AsyncMock,
            ) as mock_access,
            patch(
                "api.routers.sessions.request_identity_verification",
            ) as mock_request_identity_verification,
            patch(
                "api.routers.sessions._clear_recovery_admin_action_messages",
                new_callable=AsyncMock,
            ) as mock_clear_action_messages,
            patch(
                "api.routers.sessions.build_identity_requested_sms_text",
            ) as mock_build_sms_text,
            patch(
                "api.routers.sessions.send_sms",
            ) as mock_send_sms,
            patch(
                "api.routers.sessions._publish_recovery_prompt_updates",
                new_callable=AsyncMock,
            ) as mock_publish_prompt_updates,
            patch(
                "api.routers.sessions.recovery_request_to_dict",
            ) as mock_recovery_to_dict,
        ):
            mock_guard.side_effect = fake_guard
            mock_access.side_effect = fake_access

            with self.assertRaises(RuntimeError) as exc_context:
                await request_single_session_recovery_identity(
                    recovery_id=valid_recovery_id_str,
                    db=opaque_db,
                    current_user=opaque_current_user,
                )

            self.assertIs(exc_context.exception, access_error)

            self.assertEqual(call_events, ["guard", "access"])

            self.assertEqual(mock_guard.call_count, 1)
            self.assertEqual(mock_guard.await_count, 1)
            mock_guard.assert_awaited_once_with(opaque_db, opaque_current_user)
            guard_await_args = mock_guard.await_args
            self.assertIsNotNone(guard_await_args)
            self.assertEqual(len(guard_await_args.args), 2)
            self.assertEqual(guard_await_args.kwargs, {})
            self.assertIs(guard_await_args.args[0], opaque_db)
            self.assertIs(guard_await_args.args[1], opaque_current_user)

            self.assertEqual(mock_access.call_count, 1)
            self.assertEqual(mock_access.await_count, 1)
            mock_access.assert_awaited_once_with(
                opaque_db,
                recovery_id=expected_recovery_uuid,
                current_user=opaque_current_user,
            )
            access_await_args = mock_access.await_args
            self.assertIsNotNone(access_await_args)
            self.assertEqual(len(access_await_args.args), 1)
            self.assertIs(access_await_args.args[0], opaque_db)
            self.assertEqual(set(access_await_args.kwargs.keys()), {"recovery_id", "current_user"})
            self.assertIs(access_await_args.kwargs["current_user"], opaque_current_user)
            self.assertIsInstance(access_await_args.kwargs["recovery_id"], uuid.UUID)
            self.assertIs(type(access_await_args.kwargs["recovery_id"]), uuid.UUID)
            self.assertEqual(access_await_args.kwargs["recovery_id"], expected_recovery_uuid)
            self.assertEqual(str(access_await_args.kwargs["recovery_id"]), valid_recovery_id_str)

            mock_request_identity_verification.assert_not_called()
            self.assertEqual(mock_request_identity_verification.call_count, 0)

            mock_build_sms_text.assert_not_called()
            self.assertEqual(mock_build_sms_text.call_count, 0)

            mock_send_sms.assert_not_called()
            self.assertEqual(mock_send_sms.call_count, 0)

            mock_recovery_to_dict.assert_not_called()
            self.assertEqual(mock_recovery_to_dict.call_count, 0)

            mock_clear_action_messages.assert_not_called()
            mock_clear_action_messages.assert_not_awaited()
            self.assertEqual(mock_clear_action_messages.call_count, 0)
            self.assertEqual(mock_clear_action_messages.await_count, 0)

            mock_publish_prompt_updates.assert_not_called()
            mock_publish_prompt_updates.assert_not_awaited()
            self.assertEqual(mock_publish_prompt_updates.call_count, 0)
            self.assertEqual(mock_publish_prompt_updates.await_count, 0)


if __name__ == "__main__":
    unittest.main()
