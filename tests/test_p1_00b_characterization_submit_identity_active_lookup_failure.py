from __future__ import annotations
import os
os.environ["APP_ENV_FILE"] = "config/unit-test.env.example"
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence
install_p1_00_network_fence()

import unittest
import uuid
from unittest.mock import AsyncMock, patch

from api.routers.sessions import submit_single_session_recovery_identity


class UploadFileGuard:
    """Guarded upload stand-in recording attribute reads and awaiting read/close."""

    def __init__(self) -> None:
        self.attribute_reads: list[str] = []
        self.read = AsyncMock()
        self.close = AsyncMock()

    @property
    def content_type(self) -> str:
        self.attribute_reads.append("content_type")
        raise AssertionError("UploadFile.content_type was accessed unexpectedly")

    @property
    def filename(self) -> str:
        self.attribute_reads.append("filename")
        raise AssertionError("UploadFile.filename was accessed unexpectedly")


class SubmitIdentityActiveLookupFailureCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterize active-lookup failure escaping before SQL or upload access.

    Stage: P1-00b
    Assignment: WA-P1-00B-NORMAL-FOUNDATION-RETURN-20261006-346
    Parent Assignment: WA-P1-00B-FREEZE-20260904-001
    Seed: API-AUTH-POST-SESSIONS-SUBMIT-SINGLE-SESSION-RECOVERY-IDENTITY-L955
    Blocker: P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
    Scenario: submit-identity-active-lookup-exception-before-sql-and-upload
    Exclusions: Excludes image success, document success, and later payload-builder
        failure scenarios from tests/test_p1_00b_characterization_submit_single_session_recovery_identity.py.
        Excludes database queries, model instances, persistence, HTTP/Depends middleware,
        Request attributes, concurrency/retry/reload, full-row immutability, origin cells,
        and seed/blocker/Stage closure.
    """

    async def test_active_lookup_exception_precedes_sql_and_upload_access(self) -> None:
        fixed_request_id_str = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
        expected_request_uuid = uuid.UUID(fixed_request_id_str)
        opaque_db = object()
        upload_guard = UploadFileGuard()
        caption = "Identity verification document"
        lookup_error = RuntimeError("active recovery lookup backend failure")

        with (
            patch(
                "api.routers.sessions.get_active_recovery_request_for_login_request",
                new_callable=AsyncMock,
            ) as mock_get_active,
            patch("api.routers.sessions.select") as mock_select,
            patch(
                "api.routers.sessions._expire_recovery_if_needed",
                new_callable=AsyncMock,
            ) as mock_expire,
            patch(
                "api.routers.sessions._build_identity_message_payload",
                new_callable=AsyncMock,
            ) as mock_build_payload,
            patch("api.routers.sessions.submit_identity_material") as mock_submit_identity,
            patch(
                "api.routers.sessions._clear_recovery_admin_action_messages",
                new_callable=AsyncMock,
            ) as mock_clear_admin,
            patch(
                "api.routers.sessions._deliver_identity_submission_messages",
                new_callable=AsyncMock,
            ) as mock_deliver,
            patch("api.routers.sessions.send_sms") as mock_send_sms,
            patch("api.routers.sessions.recovery_request_to_dict") as mock_recovery_to_dict,
        ):
            mock_get_active.side_effect = lookup_error

            with self.assertRaises(RuntimeError) as exc_info:
                await submit_single_session_recovery_identity(
                    request_id=fixed_request_id_str,
                    file=upload_guard,
                    caption=caption,
                    db=opaque_db,
                )

            self.assertIs(exc_info.exception, lookup_error)

            self.assertEqual(mock_get_active.call_count, 1)
            self.assertEqual(mock_get_active.await_count, 1)
            mock_get_active.assert_awaited_once_with(opaque_db, expected_request_uuid)
            await_args = mock_get_active.await_args
            self.assertIsNotNone(await_args)
            self.assertEqual(len(await_args.args), 2)
            self.assertIs(await_args.args[0], opaque_db)
            self.assertIsInstance(await_args.args[1], uuid.UUID)
            self.assertIs(type(await_args.args[1]), uuid.UUID)
            self.assertEqual(await_args.args[1], expected_request_uuid)
            self.assertEqual(str(await_args.args[1]), fixed_request_id_str)

            mock_select.assert_not_called()
            self.assertEqual(mock_select.call_count, 0)

            self.assertEqual(upload_guard.attribute_reads, [])
            self.assertEqual(upload_guard.read.call_count, 0)
            self.assertEqual(upload_guard.read.await_count, 0)
            upload_guard.read.assert_not_called()
            upload_guard.read.assert_not_awaited()
            self.assertEqual(upload_guard.close.call_count, 0)
            self.assertEqual(upload_guard.close.await_count, 0)
            upload_guard.close.assert_not_called()
            upload_guard.close.assert_not_awaited()

            mock_expire.assert_not_called()
            self.assertEqual(mock_expire.call_count, 0)
            self.assertEqual(mock_expire.await_count, 0)

            mock_build_payload.assert_not_called()
            self.assertEqual(mock_build_payload.call_count, 0)
            self.assertEqual(mock_build_payload.await_count, 0)

            mock_submit_identity.assert_not_called()
            self.assertEqual(mock_submit_identity.call_count, 0)

            mock_clear_admin.assert_not_called()
            self.assertEqual(mock_clear_admin.call_count, 0)
            self.assertEqual(mock_clear_admin.await_count, 0)

            mock_deliver.assert_not_called()
            self.assertEqual(mock_deliver.call_count, 0)
            self.assertEqual(mock_deliver.await_count, 0)

            mock_send_sms.assert_not_called()
            self.assertEqual(mock_send_sms.call_count, 0)

            mock_recovery_to_dict.assert_not_called()
            self.assertEqual(mock_recovery_to_dict.call_count, 0)


if __name__ == "__main__":
    unittest.main()
