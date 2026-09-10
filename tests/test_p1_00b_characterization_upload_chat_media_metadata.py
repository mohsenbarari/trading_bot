"""Characterization tests for upload_chat_media handler metadata and fallback behavior.

Seed: API-MSG-POST-CHAT-UPLOAD-CHAT-MEDIA-L2338
Tracked gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Stage card: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment: WA-P1-00B-FREEZE-20260904-001

Evidence scope:
- Mocked handler forwarding and response metadata contract for api.routers.chat.upload_chat_media.
- Characterizes fallback filename/content-type generation, preservation of explicit metadata byte-for-byte,
  and all-or-neither dimension response metadata mapping.
- This evidence covers mocked handler unit boundaries only; it does not provide durable persistence,
  concurrency/reload, rollback, seed closure, or Stage acceptance.

Lifecycle status:
- Preserves P1-00 COMPLETE.
- Preserves P1-00b IN_PROGRESS.
- Preserves open HIGH BLOCKED_EXPLICIT gap P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE.
- No tracked inventory, state, or ledger changes; no tests claimed executed by author.
"""

from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from api.routers.chat import upload_chat_media
from core.services.chat_upload_session_service import UploadFilePersistenceResult


class UploadChatMediaMetadataCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Hermetic unit characterization tests for upload_chat_media metadata and fallbacks."""

    @patch("api.routers.chat.uuid.uuid4")
    @patch("api.routers.chat.persist_chat_media_file_bytes", new_callable=AsyncMock)
    async def test_upload_chat_media_metadata_fallbacks(
        self,
        mock_persist: AsyncMock,
        mock_uuid4: MagicMock,
    ) -> None:
        """Characterize fallback values for None and empty strings across filename and content_type."""
        pinned_uuid = uuid.UUID("11111111-2222-3333-4444-555555555555")
        mock_uuid4.return_value = pinned_uuid
        expected_fallback_filename = f"upload_{pinned_uuid}"
        fallback_content_type = "application/octet-stream"

        fallback_cases = [
            (
                "none_filename_explicit_content_type",
                None,
                "image/png",
                expected_fallback_filename,
                "image/png",
                True,
            ),
            (
                "empty_filename_explicit_content_type",
                "",
                "image/png",
                expected_fallback_filename,
                "image/png",
                True,
            ),
            (
                "explicit_filename_none_content_type",
                "sample.pdf",
                None,
                "sample.pdf",
                fallback_content_type,
                False,
            ),
            (
                "explicit_filename_empty_content_type",
                "sample.pdf",
                "",
                "sample.pdf",
                fallback_content_type,
                False,
            ),
            (
                "both_none",
                None,
                None,
                expected_fallback_filename,
                fallback_content_type,
                True,
            ),
            (
                "both_empty",
                "",
                "",
                expected_fallback_filename,
                fallback_content_type,
                True,
            ),
            (
                "none_filename_empty_content_type",
                None,
                "",
                expected_fallback_filename,
                fallback_content_type,
                True,
            ),
            (
                "empty_filename_none_content_type",
                "",
                None,
                expected_fallback_filename,
                fallback_content_type,
                True,
            ),
        ]

        for (
            case_name,
            raw_filename,
            raw_content_type,
            expected_filename,
            expected_declared_content_type,
            expects_uuid,
        ) in fallback_cases:
            with self.subTest(case=case_name):
                mock_uuid4.reset_mock()
                mock_persist.reset_mock()

                synthetic_db = SimpleNamespace(commit=AsyncMock())
                synthetic_user = SimpleNamespace(id=101)
                raw_bytes = b"fallback-test-payload-bytes"
                synthetic_thumbnail = "thumb-fallback-input"

                synthetic_file = SimpleNamespace(
                    filename=raw_filename,
                    content_type=raw_content_type,
                    read=AsyncMock(return_value=raw_bytes),
                    close=AsyncMock(),
                )

                persisted_chat_file = SimpleNamespace(
                    id="cf-fallback-id-123",
                    thumbnail="cf-persisted-thumb",
                    file_name="cf-persisted-name.png",
                    mime_type="image/png",
                    size=2048,
                )
                mock_persist.return_value = UploadFilePersistenceResult(
                    chat_file=persisted_chat_file,
                    width=800,
                    height=600,
                    mime_type="wrapper/ignored-mime",
                    size=9999,
                )

                result = await upload_chat_media(
                    file=synthetic_file,
                    thumbnail=synthetic_thumbnail,
                    current_user=synthetic_user,
                    db=synthetic_db,
                )

                # Forwarding assertions
                synthetic_file.read.assert_awaited_once_with()
                mock_persist.assert_awaited_once_with(
                    synthetic_db,
                    uploader_id=101,
                    file_name=expected_filename,
                    declared_content_type=expected_declared_content_type,
                    contents=raw_bytes,
                    thumbnail=synthetic_thumbnail,
                )
                self.assertIs(mock_persist.await_args.args[0], synthetic_db)
                synthetic_db.commit.assert_awaited_once_with()
                synthetic_file.close.assert_awaited_once_with()

                if expects_uuid:
                    self.assertEqual(mock_uuid4.call_count, 1)
                else:
                    mock_uuid4.assert_not_called()

                # Response assertions (positive dimensions control)
                expected_response = {
                    "file_id": "cf-fallback-id-123",
                    "thumbnail": "cf-persisted-thumb",
                    "file_name": "cf-persisted-name.png",
                    "mime_type": "image/png",
                    "size": 2048,
                    "width": 800,
                    "height": 600,
                }
                self.assertEqual(result, expected_response)

    @patch("api.routers.chat.uuid.uuid4")
    @patch("api.routers.chat.persist_chat_media_file_bytes", new_callable=AsyncMock)
    async def test_upload_chat_media_explicit_metadata_preservation(
        self,
        mock_persist: AsyncMock,
        mock_uuid4: MagicMock,
    ) -> None:
        """Characterize byte-for-byte preservation of explicit metadata and preference for ChatFile values."""
        synthetic_db = SimpleNamespace(commit=AsyncMock())
        synthetic_user = SimpleNamespace(id=202)

        explicit_filename = "   spaced invoice document name.PDF   "
        explicit_content_type = "application/pdf; charset=utf-8; boundary=something"
        raw_bytes = b"%PDF-1.4 explicit byte content 12345"
        request_thumbnail = "req-thumb-base64-payload"

        synthetic_file = SimpleNamespace(
            filename=explicit_filename,
            content_type=explicit_content_type,
            read=AsyncMock(return_value=raw_bytes),
            close=AsyncMock(),
        )

        persisted_chat_file = SimpleNamespace(
            id="persisted-chat-file-uuid-777",
            thumbnail="persisted-processed-thumbnail-base64",
            file_name="canonical_persisted_name.pdf",
            mime_type="application/pdf",
            size=1048576,
        )
        wrapper_mime_type = "wrapper/ignored-mime-type"
        wrapper_size = 987654
        persistence_result = UploadFilePersistenceResult(
            chat_file=persisted_chat_file,
            width=1920,
            height=1080,
            mime_type=wrapper_mime_type,
            size=wrapper_size,
        )
        mock_persist.return_value = persistence_result

        result = await upload_chat_media(
            file=synthetic_file,
            thumbnail=request_thumbnail,
            current_user=synthetic_user,
            db=synthetic_db,
        )

        # 1. Forwarding expectations
        synthetic_file.read.assert_awaited_once_with()
        mock_persist.assert_awaited_once_with(
            synthetic_db,
            uploader_id=202,
            file_name=explicit_filename,
            declared_content_type=explicit_content_type,
            contents=raw_bytes,
            thumbnail=request_thumbnail,
        )
        self.assertIs(mock_persist.await_args.args[0], synthetic_db)
        synthetic_db.commit.assert_awaited_once_with()
        synthetic_file.close.assert_awaited_once_with()
        mock_uuid4.assert_not_called()

        # 2. Response expectations sourced from chat_file + persistence_result dimensions
        expected_response = {
            "file_id": "persisted-chat-file-uuid-777",
            "thumbnail": "persisted-processed-thumbnail-base64",
            "file_name": "canonical_persisted_name.pdf",
            "mime_type": "application/pdf",
            "size": 1048576,
            "width": 1920,
            "height": 1080,
        }
        self.assertEqual(result, expected_response)

        # 3. Explicit negative checks confirming response is not sourced from request or wrapper
        self.assertNotEqual(result["file_name"], explicit_filename)
        self.assertNotEqual(result["mime_type"], explicit_content_type)
        self.assertNotEqual(result["mime_type"], wrapper_mime_type)
        self.assertNotEqual(result["size"], len(raw_bytes))
        self.assertNotEqual(result["size"], wrapper_size)
        self.assertNotEqual(result["thumbnail"], request_thumbnail)

    @patch("api.routers.chat.uuid.uuid4")
    @patch("api.routers.chat.persist_chat_media_file_bytes", new_callable=AsyncMock)
    async def test_upload_chat_media_dimension_inclusion_matrix(
        self,
        mock_persist: AsyncMock,
        mock_uuid4: MagicMock,
    ) -> None:
        """Characterize all-or-neither dimension inclusion for falsy pairs and positive control."""
        dimension_matrix_cases = [
            ("none_width", None, 480, False),
            ("none_height", 640, None, False),
            ("zero_width", 0, 480, False),
            ("zero_height", 640, 0, False),
            ("both_none", None, None, False),
            ("both_zero", 0, 0, False),
            ("positive_control", 640, 480, True),
        ]

        synthetic_user = SimpleNamespace(id=303)
        raw_contents = b"\xff\xd8\xff\xe0dimension-test-bytes"
        request_thumbnail = "thumb-dim-req"

        persisted_chat_file = SimpleNamespace(
            id="cf-dim-id-456",
            thumbnail="thumb-dim-persisted",
            file_name="image_dim.png",
            mime_type="image/png",
            size=4096,
        )

        expected_five_key_response = {
            "file_id": "cf-dim-id-456",
            "thumbnail": "thumb-dim-persisted",
            "file_name": "image_dim.png",
            "mime_type": "image/png",
            "size": 4096,
        }

        expected_seven_key_response = {
            "file_id": "cf-dim-id-456",
            "thumbnail": "thumb-dim-persisted",
            "file_name": "image_dim.png",
            "mime_type": "image/png",
            "size": 4096,
            "width": 640,
            "height": 480,
        }

        for case_name, width, height, is_positive_control in dimension_matrix_cases:
            with self.subTest(case=case_name, width=width, height=height):
                mock_uuid4.reset_mock()
                mock_persist.reset_mock()

                synthetic_db = SimpleNamespace(commit=AsyncMock())
                synthetic_file = SimpleNamespace(
                    filename="image_dim.png",
                    content_type="image/png",
                    read=AsyncMock(return_value=raw_contents),
                    close=AsyncMock(),
                )

                persistence_result = UploadFilePersistenceResult(
                    chat_file=persisted_chat_file,
                    width=width,
                    height=height,
                    mime_type="ignored/mime",
                    size=9999,
                )
                mock_persist.return_value = persistence_result

                result = await upload_chat_media(
                    file=synthetic_file,
                    thumbnail=request_thumbnail,
                    current_user=synthetic_user,
                    db=synthetic_db,
                )

                # Awaited lifecycle assertions
                synthetic_file.read.assert_awaited_once_with()
                mock_persist.assert_awaited_once_with(
                    synthetic_db,
                    uploader_id=303,
                    file_name="image_dim.png",
                    declared_content_type="image/png",
                    contents=raw_contents,
                    thumbnail=request_thumbnail,
                )
                self.assertIs(mock_persist.await_args.args[0], synthetic_db)
                synthetic_db.commit.assert_awaited_once_with()
                synthetic_file.close.assert_awaited_once_with()
                mock_uuid4.assert_not_called()

                if is_positive_control:
                    self.assertEqual(result, expected_seven_key_response)
                    self.assertIn("width", result)
                    self.assertIn("height", result)
                    self.assertEqual(result["width"], 640)
                    self.assertEqual(result["height"], 480)
                    self.assertEqual(len(result), 7)
                else:
                    self.assertEqual(result, expected_five_key_response)
                    self.assertNotIn("width", result)
                    self.assertNotIn("height", result)
                    self.assertEqual(len(result), 5)


if __name__ == "__main__":
    unittest.main()
