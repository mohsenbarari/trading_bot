"""Characterization tests for submit_single_session_recovery_identity endpoint.

Stage: P1-00b
Assignment: WA-P1-00B-FREEZE-20260904-001
Tracked High Gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Exact API Seed: docs/refactor/two-server/section-1/inventory/surface-api.json#API-AUTH-POST-SESSIONS-SUBMIT-SINGLE-SESSION-RECOVERY-IDENTITY-L955
API Seed Reference: API-AUTH-POST-SESSIONS-SUBMIT-SINGLE-SESSION-RECOVERY-IDENTITY-L955

Exercises the real api.routers.sessions.submit_single_session_recovery_identity handler for:
1. Closely related image success behavior with caption suppression during direct delivery
2. Closely related document success behavior with MIME parameter normalization and caption preservation
3. Payload-builder failure behavior with exception propagation, file closure, and absence of mutations
"""

from __future__ import annotations

import operator
import unittest
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

# Enforce hermetic network fence prior to importing router under test
install_p1_00_network_fence()

from api.routers.sessions import (
    recovery_request_to_dict,
    submit_single_session_recovery_identity,
)
from core.enums import MessageType
from core.services.single_session_recovery_service import (
    CHAT_ACTION_WINDOW_SECONDS,
    INLINE_ACTION_WINDOW_SECONDS,
    build_identity_submitted_sms_text,
    submit_identity_material,
)
from models.session import SingleSessionRecoveryRequest, SingleSessionRecoveryStatus
from models.user import User, UserRole
from tests.test_sessions_router_single_session_recovery import (
    FakeDB,
    FakeExecuteResult,
    FakeUploadFile,
    make_recovery,
    make_user,
)


class CharacterizationFakeDB(FakeDB):
    """Extended fake database session tracking statements and asserting forbidden calls."""

    def __init__(self, execute_results=None):
        super().__init__(execute_results=execute_results)
        self.executed_statements = []
        self.flush = AsyncMock()
        self.refresh = AsyncMock()
        self.delete = AsyncMock()

    async def execute(self, stmt):
        self.executed_statements.append(stmt)
        return await super().execute(stmt)


class CharacterizationUploadFile(FakeUploadFile):
    """Extended fake upload tracking await counts for read and close operations."""

    def __init__(
        self,
        content: bytes,
        *,
        filename: str = "identity.jpg",
        content_type: str = "image/jpeg",
    ):
        super().__init__(content, filename=filename, content_type=content_type)
        self.read = AsyncMock(return_value=content)
        self.close = AsyncMock()


class TestSubmitSingleSessionRecoveryIdentityCharacterization(unittest.IsolatedAsyncioTestCase):
    """Characterization tests for submit_single_session_recovery_identity endpoint."""

    def assert_requester_user_lookup_statement(self, stmt, expected_user_id: int) -> None:
        """Assert recorded statement selects User constrained by User.id == expected_user_id."""
        self.assertTrue(hasattr(stmt, "column_descriptions") and len(stmt.column_descriptions) > 0)
        selected_entities = [
            desc.get("entity") for desc in stmt.column_descriptions if isinstance(desc, dict)
        ]
        self.assertIn(User, selected_entities)

        whereclause = getattr(stmt, "whereclause", None)
        self.assertIsNotNone(whereclause, "SQL statement must have a WHERE clause")
        left = getattr(whereclause, "left", None)
        self.assertIsNotNone(left)
        left_name = getattr(left, "key", getattr(left, "name", None))
        self.assertEqual(left_name, "id")
        left_entity = getattr(left, "class_", getattr(getattr(left, "table", None), "name", None))
        self.assertIn(left_entity, (User, "users"))

        op = getattr(whereclause, "operator", None)
        self.assertTrue(op is operator.eq or getattr(op, "__name__", None) in ("eq", "__eq__"))

        right = getattr(whereclause, "right", None)
        self.assertIsNotNone(right)
        self.assertEqual(getattr(right, "value", None), expected_user_id)

        compiled = stmt.compile()
        self.assertIn(expected_user_id, compiled.params.values())

    async def test_submit_identity_image_success(self):
        recovery_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
        request_id_str = str(recovery_id)
        now = datetime(2026, 9, 12, 10, 0, 0)
        created_at = now - timedelta(hours=1)
        inline_expires = now + timedelta(seconds=30)
        chat_expires = now + timedelta(hours=2)
        identity_requested = now - timedelta(minutes=15)

        recovery = make_recovery(
            id=recovery_id,
            user_id=7,
            status=SingleSessionRecoveryStatus.IDENTITY_VERIFICATION_REQUESTED,
            created_at=created_at,
            inline_action_expires_at=inline_expires,
            chat_action_expires_at=chat_expires,
            identity_requested_at=identity_requested,
            identity_submitted_at=None,
            decided_at=None,
            cancelled_at=None,
        )
        requester_user = make_user(
            id=7,
            mobile_number="09123456789",
            is_deleted=False,
        )
        db = CharacterizationFakeDB(
            execute_results=[FakeExecuteResult(value=requester_user)]
        )

        image_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00-fake-jpeg-image-bytes"
        raw_content_type = 'IMAGE/JPEG; charset=utf-8; name="id_card.jpeg"'
        upload_file = CharacterizationUploadFile(
            image_bytes,
            filename="national_identity_card.jpg",
            content_type=raw_content_type,
        )
        raw_caption = "  Valid government national ID  "
        built_image_payload = (
            '{"file_id": "img-file-777", "mime_type": "image/jpeg", "thumbnail": "thumb-b64"}'
        )

        events: list[str] = []

        async def tracked_build_payload(*args, **kwargs):
            events.append("payload-build")
            return (
                MessageType.IMAGE,
                built_image_payload,
                "Builder returned caption that should be suppressed for images",
            )

        def tracked_submit_identity_material(rec, *args, **kwargs):
            events.append("transition")
            return submit_identity_material(rec, *args, **kwargs)

        async def tracked_clear_admin(db_arg, recovery_id_arg):
            events.append("clear-admin-actions")

        async def tracked_commit():
            events.append("commit")

        async def tracked_deliver(*args, **kwargs):
            events.append("delivery")

        async def tracked_file_close():
            events.append("file-close")

        def tracked_send_sms(*args, **kwargs):
            events.append("SMS")

        def tracked_recovery_request_to_dict(rec, *args, **kwargs):
            events.append("serialization")
            return recovery_request_to_dict(rec, *args, **kwargs)

        db.commit.side_effect = tracked_commit
        upload_file.close.side_effect = tracked_file_close

        with patch(
            "api.routers.sessions.get_active_recovery_request_for_login_request",
            new_callable=AsyncMock,
            return_value=recovery,
        ) as mock_get_active, \
        patch(
            "api.routers.sessions._expire_recovery_if_needed",
            new_callable=AsyncMock,
            return_value=False,
        ) as mock_expire_recovery, \
        patch(
            "api.routers.sessions._build_identity_message_payload",
            new_callable=AsyncMock,
            side_effect=tracked_build_payload,
        ) as mock_build_payload, \
        patch(
            "api.routers.sessions.submit_identity_material",
            side_effect=tracked_submit_identity_material,
        ) as mock_submit_transition, \
        patch(
            "api.routers.sessions._clear_recovery_admin_action_messages",
            new_callable=AsyncMock,
            side_effect=tracked_clear_admin,
        ) as mock_clear_admin, \
        patch(
            "api.routers.sessions._deliver_identity_submission_messages",
            new_callable=AsyncMock,
            side_effect=tracked_deliver,
        ) as mock_deliver, \
        patch(
            "api.routers.sessions.send_sms",
            side_effect=tracked_send_sms,
        ) as mock_send_sms, \
        patch(
            "api.routers.sessions.recovery_request_to_dict",
            side_effect=tracked_recovery_request_to_dict,
        ) as mock_to_dict:
            result = await submit_single_session_recovery_identity(
                request_id=request_id_str,
                file=upload_file,
                caption=raw_caption,
                db=db,
            )

        # Requirement 2 assertions: active recovery lookup receives UUID
        mock_get_active.assert_awaited_once_with(db, recovery_id)

        # User query resolves matching non-deleted requester with exact entity and WHERE constraint
        self.assertEqual(len(db.executed_statements), 1)
        self.assert_requester_user_lookup_statement(db.executed_statements[0], recovery.user_id)
        self.assertEqual(requester_user.id, recovery.user_id)
        self.assertFalse(requester_user.is_deleted)

        # Expiry check returns false
        mock_expire_recovery.assert_awaited_once_with(db, recovery, requester_user)

        # Upload read exactly once
        self.assertEqual(upload_file.read.await_count, 1)
        upload_file.read.assert_awaited_once()

        # Payload builder receives exact filename, normalized declared image MIME, bytes, uploader ID, raw caption, DB object
        mock_build_payload.assert_awaited_once_with(
            file_name="national_identity_card.jpg",
            declared_content_type="image/jpeg",
            contents=image_bytes,
            uploader_id=7,
            caption=raw_caption,
            db=db,
        )

        # Requirement 3 assertions: real submit_identity_material transition tracked and state updated
        mock_submit_transition.assert_called_once_with(recovery)
        self.assertEqual(recovery.status, SingleSessionRecoveryStatus.IDENTITY_SUBMITTED)
        self.assertIsNotNone(recovery.identity_submitted_at)
        self.assertIsInstance(recovery.identity_submitted_at, datetime)
        self.assertEqual(
            recovery.inline_action_expires_at,
            recovery.identity_submitted_at + timedelta(seconds=INLINE_ACTION_WINDOW_SECONDS),
        )
        self.assertEqual(
            recovery.chat_action_expires_at,
            recovery.identity_submitted_at + timedelta(seconds=CHAT_ACTION_WINDOW_SECONDS),
        )

        # Clear admin actions called with (db, recovery.id)
        mock_clear_admin.assert_awaited_once_with(db, recovery.id)

        # DB committed exactly once
        self.assertEqual(db.commit.await_count, 1)
        db.commit.assert_awaited_once()

        # Delivery receives MessageType.IMAGE, exact built content, and caption_text=None
        mock_deliver.assert_awaited_once_with(
            db,
            recovery_request=recovery,
            requester_user=requester_user,
            message_type=MessageType.IMAGE,
            message_content=built_image_payload,
            caption_text=None,
        )

        # Exact file closure
        self.assertEqual(upload_file.close.await_count, 1)
        upload_file.close.assert_awaited_once()

        # Exactly one SMS dispatched to requester with canonical template
        mock_send_sms.assert_called_once_with(
            requester_user.mobile_number,
            build_identity_submitted_sms_text(),
        )
        self.assertEqual(mock_send_sms.call_count, 1)

        # Serialization called with recovery
        mock_to_dict.assert_called_once_with(recovery)

        # Exact sequence verified: payload-build -> transition -> clear-admin-actions -> commit -> delivery -> file-close -> SMS -> serialization
        expected_order = [
            "payload-build",
            "transition",
            "clear-admin-actions",
            "commit",
            "delivery",
            "file-close",
            "SMS",
            "serialization",
        ]
        self.assertEqual(events, expected_order)

        # No rollback occurred
        self.assertEqual(db.rollback.await_count, 0)
        db.rollback.assert_not_called()

        # Complete real serialized response matches recovery_request_to_dict
        self.assertEqual(result["detail"], "مدرک برای بررسی ارسال شد")
        expected_recovery_dict = recovery_request_to_dict(recovery)
        self.assertEqual(result["recovery"], expected_recovery_dict)
        self.assertEqual(result["recovery"]["id"], str(recovery_id))
        self.assertEqual(result["recovery"]["status"], "identity_submitted")
        self.assertEqual(result["recovery"]["requester_device_name"], "Chrome on Windows")
        self.assertEqual(result["recovery"]["requester_ip"], "8.8.8.8")
        self.assertEqual(result["recovery"]["created_at"], recovery.created_at.isoformat())
        self.assertEqual(
            result["recovery"]["inline_action_expires_at"],
            recovery.inline_action_expires_at.isoformat(),
        )
        self.assertEqual(
            result["recovery"]["chat_action_expires_at"],
            recovery.chat_action_expires_at.isoformat(),
        )
        self.assertEqual(
            result["recovery"]["identity_requested_at"],
            recovery.identity_requested_at.isoformat(),
        )
        self.assertEqual(
            result["recovery"]["identity_submitted_at"],
            recovery.identity_submitted_at.isoformat(),
        )
        self.assertIsNone(result["recovery"]["decided_at"])
        self.assertIsNone(result["recovery"]["cancelled_at"])

    async def test_submit_identity_document_success(self):
        recovery_id = uuid.UUID("22222222-2222-2222-2222-222222222222")
        request_id_str = str(recovery_id)
        now = datetime(2026, 9, 12, 11, 0, 0)
        created_at = now - timedelta(hours=2)
        inline_expires = now + timedelta(seconds=30)
        chat_expires = now + timedelta(hours=2)
        identity_requested = now - timedelta(minutes=30)

        recovery = make_recovery(
            id=recovery_id,
            user_id=7,
            status=SingleSessionRecoveryStatus.IDENTITY_VERIFICATION_REQUESTED,
            created_at=created_at,
            inline_action_expires_at=inline_expires,
            chat_action_expires_at=chat_expires,
            identity_requested_at=identity_requested,
            identity_submitted_at=None,
            decided_at=None,
            cancelled_at=None,
        )
        requester_user = make_user(
            id=7,
            mobile_number="09198765432",
            is_deleted=False,
        )
        db = CharacterizationFakeDB(
            execute_results=[FakeExecuteResult(value=requester_user)]
        )

        doc_bytes = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\nfake-pdf-data"
        raw_content_type = 'APPLICATION/PDF; charset=binary; name="identity_proof.pdf"'
        upload_file = CharacterizationUploadFile(
            doc_bytes,
            filename="identity_proof.pdf",
            content_type=raw_content_type,
        )
        raw_caption = "  Official identity documentation PDF  "
        builder_caption = "Official identity documentation PDF"
        built_doc_payload = (
            '{"file_id": "doc-file-888", "file_name": "identity_proof.pdf", '
            '"mime_type": "application/pdf", "size": 1024}'
        )

        events: list[str] = []

        async def tracked_build_payload(*args, **kwargs):
            events.append("payload-build")
            return (MessageType.DOCUMENT, built_doc_payload, builder_caption)

        def tracked_submit_identity_material(rec, *args, **kwargs):
            events.append("transition")
            return submit_identity_material(rec, *args, **kwargs)

        async def tracked_clear_admin(db_arg, recovery_id_arg):
            events.append("clear-admin-actions")

        async def tracked_commit():
            events.append("commit")

        async def tracked_deliver(*args, **kwargs):
            events.append("delivery")

        async def tracked_file_close():
            events.append("file-close")

        def tracked_send_sms(*args, **kwargs):
            events.append("SMS")

        def tracked_recovery_request_to_dict(rec, *args, **kwargs):
            events.append("serialization")
            return recovery_request_to_dict(rec, *args, **kwargs)

        db.commit.side_effect = tracked_commit
        upload_file.close.side_effect = tracked_file_close

        with patch(
            "api.routers.sessions.get_active_recovery_request_for_login_request",
            new_callable=AsyncMock,
            return_value=recovery,
        ) as mock_get_active, \
        patch(
            "api.routers.sessions._expire_recovery_if_needed",
            new_callable=AsyncMock,
            return_value=False,
        ) as mock_expire_recovery, \
        patch(
            "api.routers.sessions._build_identity_message_payload",
            new_callable=AsyncMock,
            side_effect=tracked_build_payload,
        ) as mock_build_payload, \
        patch(
            "api.routers.sessions.submit_identity_material",
            side_effect=tracked_submit_identity_material,
        ) as mock_submit_transition, \
        patch(
            "api.routers.sessions._clear_recovery_admin_action_messages",
            new_callable=AsyncMock,
            side_effect=tracked_clear_admin,
        ) as mock_clear_admin, \
        patch(
            "api.routers.sessions._deliver_identity_submission_messages",
            new_callable=AsyncMock,
            side_effect=tracked_deliver,
        ) as mock_deliver, \
        patch(
            "api.routers.sessions.send_sms",
            side_effect=tracked_send_sms,
        ) as mock_send_sms, \
        patch(
            "api.routers.sessions.recovery_request_to_dict",
            side_effect=tracked_recovery_request_to_dict,
        ) as mock_to_dict:
            result = await submit_single_session_recovery_identity(
                request_id=request_id_str,
                file=upload_file,
                caption=raw_caption,
                db=db,
            )

        # Requirement 4 assertions: active recovery lookup receives UUID
        mock_get_active.assert_awaited_once_with(db, recovery_id)

        # User query resolves matching non-deleted requester with exact entity and WHERE constraint
        self.assertEqual(len(db.executed_statements), 1)
        self.assert_requester_user_lookup_statement(db.executed_statements[0], recovery.user_id)
        self.assertEqual(requester_user.id, recovery.user_id)
        self.assertFalse(requester_user.is_deleted)

        # Expiry check returns false
        mock_expire_recovery.assert_awaited_once_with(db, recovery, requester_user)

        # Upload read exactly once
        self.assertEqual(upload_file.read.await_count, 1)
        upload_file.read.assert_awaited_once()

        # Payload builder receives normalized MIME "application/pdf" (parameters stripped and lowercased)
        mock_build_payload.assert_awaited_once_with(
            file_name="identity_proof.pdf",
            declared_content_type="application/pdf",
            contents=doc_bytes,
            uploader_id=7,
            caption=raw_caption,
            db=db,
        )

        # Real submit_identity_material transition tracked and state updated
        mock_submit_transition.assert_called_once_with(recovery)
        self.assertEqual(recovery.status, SingleSessionRecoveryStatus.IDENTITY_SUBMITTED)
        self.assertIsNotNone(recovery.identity_submitted_at)
        self.assertIsInstance(recovery.identity_submitted_at, datetime)
        self.assertEqual(
            recovery.inline_action_expires_at,
            recovery.identity_submitted_at + timedelta(seconds=INLINE_ACTION_WINDOW_SECONDS),
        )
        self.assertEqual(
            recovery.chat_action_expires_at,
            recovery.identity_submitted_at + timedelta(seconds=CHAT_ACTION_WINDOW_SECONDS),
        )

        # Clear admin actions called with (db, recovery.id)
        mock_clear_admin.assert_awaited_once_with(db, recovery.id)

        # DB committed exactly once
        self.assertEqual(db.commit.await_count, 1)
        db.commit.assert_awaited_once()

        # Delivery receives MessageType.DOCUMENT and preserves normalized caption
        mock_deliver.assert_awaited_once_with(
            db,
            recovery_request=recovery,
            requester_user=requester_user,
            message_type=MessageType.DOCUMENT,
            message_content=built_doc_payload,
            caption_text=builder_caption,
        )
        self.assertEqual(mock_deliver.call_args.kwargs["caption_text"], builder_caption)
        self.assertIsNotNone(mock_deliver.call_args.kwargs["caption_text"])

        # File closed exactly once
        self.assertEqual(upload_file.close.await_count, 1)
        upload_file.close.assert_awaited_once()

        # SMS dispatched to requester
        mock_send_sms.assert_called_once_with(
            requester_user.mobile_number,
            build_identity_submitted_sms_text(),
        )

        # Serialization called with recovery
        mock_to_dict.assert_called_once_with(recovery)

        # Exact order verified
        expected_order = [
            "payload-build",
            "transition",
            "clear-admin-actions",
            "commit",
            "delivery",
            "file-close",
            "SMS",
            "serialization",
        ]
        self.assertEqual(events, expected_order)

        # No rollback occurred
        self.assertEqual(db.rollback.await_count, 0)
        db.rollback.assert_not_called()

        # Exact success detail and complete serialization asserted
        self.assertEqual(result["detail"], "مدرک برای بررسی ارسال شد")
        expected_recovery_dict = recovery_request_to_dict(recovery)
        self.assertEqual(result["recovery"], expected_recovery_dict)
        self.assertEqual(result["recovery"]["id"], str(recovery_id))
        self.assertEqual(result["recovery"]["status"], "identity_submitted")
        self.assertEqual(result["recovery"]["requester_device_name"], "Chrome on Windows")
        self.assertEqual(result["recovery"]["requester_ip"], "8.8.8.8")
        self.assertEqual(result["recovery"]["created_at"], recovery.created_at.isoformat())
        self.assertEqual(
            result["recovery"]["inline_action_expires_at"],
            recovery.inline_action_expires_at.isoformat(),
        )
        self.assertEqual(
            result["recovery"]["chat_action_expires_at"],
            recovery.chat_action_expires_at.isoformat(),
        )
        self.assertEqual(
            result["recovery"]["identity_requested_at"],
            recovery.identity_requested_at.isoformat(),
        )
        self.assertEqual(
            result["recovery"]["identity_submitted_at"],
            recovery.identity_submitted_at.isoformat(),
        )
        self.assertIsNone(result["recovery"]["decided_at"])
        self.assertIsNone(result["recovery"]["cancelled_at"])

    async def test_submit_identity_payload_builder_failure(self):
        recovery_id = uuid.UUID("33333333-3333-3333-3333-333333333333")
        request_id_str = str(recovery_id)
        now = datetime(2026, 9, 12, 12, 0, 0)
        original_created_at = now - timedelta(hours=3)
        original_inline_expires = now + timedelta(seconds=30)
        original_chat_expires = now + timedelta(hours=2)
        original_identity_requested = now - timedelta(minutes=45)

        recovery = make_recovery(
            id=recovery_id,
            user_id=7,
            status=SingleSessionRecoveryStatus.IDENTITY_VERIFICATION_REQUESTED,
            created_at=original_created_at,
            inline_action_expires_at=original_inline_expires,
            chat_action_expires_at=original_chat_expires,
            identity_requested_at=original_identity_requested,
            identity_submitted_at=None,
            decided_at=None,
            cancelled_at=None,
        )
        requester_user = make_user(
            id=7,
            mobile_number="09121112233",
            is_deleted=False,
        )
        db = CharacterizationFakeDB(
            execute_results=[FakeExecuteResult(value=requester_user)]
        )

        test_bytes = b"corrupted-or-failing-file-content"
        upload_file = CharacterizationUploadFile(
            test_bytes,
            filename="corrupt_proof.pdf",
            content_type="application/pdf",
        )

        sentinel_error = RuntimeError("Sentinel payload-builder error")

        with patch(
            "api.routers.sessions.get_active_recovery_request_for_login_request",
            new_callable=AsyncMock,
            return_value=recovery,
        ) as mock_get_active, \
        patch(
            "api.routers.sessions._expire_recovery_if_needed",
            new_callable=AsyncMock,
            return_value=False,
        ) as mock_expire_recovery, \
        patch(
            "api.routers.sessions._build_identity_message_payload",
            new_callable=AsyncMock,
            side_effect=sentinel_error,
        ) as mock_build_payload, \
        patch(
            "api.routers.sessions.submit_identity_material",
            side_effect=submit_identity_material,
        ) as mock_submit_transition, \
        patch(
            "api.routers.sessions._clear_recovery_admin_action_messages",
            new_callable=AsyncMock,
        ) as mock_clear_admin, \
        patch(
            "api.routers.sessions._deliver_identity_submission_messages",
            new_callable=AsyncMock,
        ) as mock_deliver, \
        patch(
            "api.routers.sessions.send_sms",
        ) as mock_send_sms, \
        patch(
            "api.routers.sessions.recovery_request_to_dict",
        ) as mock_to_dict:
            with self.assertRaises(RuntimeError) as ctx:
                await submit_single_session_recovery_identity(
                    request_id=request_id_str,
                    file=upload_file,
                    caption="Caption for failing payload build",
                    db=db,
                )

            # Requirement 5 assertions: original exception propagates
            self.assertIs(ctx.exception, sentinel_error)

        # Active recovery lookup receives UUID
        mock_get_active.assert_awaited_once_with(db, recovery_id)

        # User query resolves matching non-deleted requester with exact entity and WHERE constraint
        self.assertEqual(len(db.executed_statements), 1)
        self.assert_requester_user_lookup_statement(db.executed_statements[0], recovery.user_id)
        self.assertEqual(requester_user.id, recovery.user_id)
        self.assertFalse(requester_user.is_deleted)

        # Expiry check executed and returned false
        mock_expire_recovery.assert_awaited_once_with(db, recovery, requester_user)

        # Upload read exactly once
        self.assertEqual(upload_file.read.await_count, 1)
        upload_file.read.assert_awaited_once()

        # Payload builder called with exact arguments before raising
        mock_build_payload.assert_awaited_once_with(
            file_name="corrupt_proof.pdf",
            declared_content_type="application/pdf",
            contents=test_bytes,
            uploader_id=7,
            caption="Caption for failing payload build",
            db=db,
        )

        # File closed exactly once in finally block
        self.assertEqual(upload_file.close.await_count, 1)
        upload_file.close.assert_awaited_once()

        # Real recovery remains in IDENTITY_VERIFICATION_REQUESTED with original timestamps and windows
        self.assertEqual(recovery.status, SingleSessionRecoveryStatus.IDENTITY_VERIFICATION_REQUESTED)
        self.assertIsNone(recovery.identity_submitted_at)
        self.assertEqual(recovery.created_at, original_created_at)
        self.assertEqual(recovery.inline_action_expires_at, original_inline_expires)
        self.assertEqual(recovery.chat_action_expires_at, original_chat_expires)
        self.assertEqual(recovery.identity_requested_at, original_identity_requested)
        self.assertIsNone(recovery.decided_at)
        self.assertIsNone(recovery.cancelled_at)

        # Forbidden side effects asserted NOT called:
        # no transition
        mock_submit_transition.assert_not_called()
        # no clear-admin-actions call
        mock_clear_admin.assert_not_called()
        # no commit
        self.assertEqual(db.commit.await_count, 0)
        db.commit.assert_not_called()
        # no delivery
        mock_deliver.assert_not_called()
        # no SMS
        mock_send_sms.assert_not_called()
        # no serialization
        mock_to_dict.assert_not_called()
        # no rollback
        self.assertEqual(db.rollback.await_count, 0)
        db.rollback.assert_not_called()
        # no add
        self.assertEqual(db.added, [])
        # no flush
        self.assertEqual(db.flush.await_count, 0)
        db.flush.assert_not_called()
        # no refresh
        self.assertEqual(db.refresh.await_count, 0)
        db.refresh.assert_not_called()
        # no delete
        self.assertEqual(db.delete.await_count, 0)
        db.delete.assert_not_called()


if __name__ == "__main__":
    unittest.main()
