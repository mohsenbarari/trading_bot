"""Focused current-behavior boundary characterization for get_upload_session_state.

Traceability:
- Seed: API-MSG-GET-CHAT-GET-UPLOAD-SESSION-STATE-L2614
- Gap Reference: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
- Stage: P1-00b
- Assignment ID: WA-P1-00B-FREEZE-20260904-001

Limitations:
- Characterizes only the direct handler boundary and schema mapping of
  api.routers.chat.get_upload_session_state with an isolated service double.
- Provides evidence for route handler behavior and schema normalization only;
  not durable persistence, concurrency, transaction isolation, filesystem state,
  Redis pub/sub, or event delivery.
- Does not represent Stage P1-00b completion or canonical gap closure.
"""

from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from api.routers.chat import get_upload_session_state
from api.routers.chat_schemas import UploadPreviewMetadata, UploadSessionStateRead
from models.upload_session import UploadSessionStatus


class GetUploadSessionStateBoundaryCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_upload_session_state_populated_preview_success(self):
        db = SimpleNamespace(name="mock_db_session")
        current_user = SimpleNamespace(id=42, username="characterization_user")
        request_session_id = "upload-session-req-alpha"
        returned_session_id = "upload-session-ret-beta"
        final_chat_file_id = "chat-file-uuid-789"

        raw_preview_metadata = {
            "width": 1920,
            "height": 1080,
            "duration_ms": 36000,
            "thumbnail": "https://example.com/media/preview.jpg",
            "caption": "   Sample video caption with whitespace padding   ",
            "album_index": 2,
            "waveform": [15, 30, 45, 60, 75],
        }

        session_double = SimpleNamespace(
            id=returned_session_id,
            status=UploadSessionStatus.READY,
            next_offset=1024,
            received_bytes=2048,
            total_bytes=4096,
            preview_metadata=raw_preview_metadata,
            final_chat_file_id=final_chat_file_id,
        )

        mock_lookup = AsyncMock(return_value=session_double)

        with patch(
            "api.routers.chat.get_upload_session_for_current_user",
            mock_lookup,
        ):
            response = await get_upload_session_state(
                session_id=request_session_id,
                current_user=current_user,
                db=db,
            )

        mock_lookup.assert_awaited_once_with(
            db,
            session_id=request_session_id,
            current_user=current_user,
        )
        self.assertEqual(mock_lookup.call_count, 1)
        self.assertEqual(mock_lookup.await_count, 1)
        lookup_args, lookup_kwargs = mock_lookup.await_args
        self.assertEqual(len(lookup_args), 1)
        self.assertIs(lookup_args[0], db)
        self.assertEqual(
            lookup_kwargs,
            {"session_id": request_session_id, "current_user": current_user},
        )
        self.assertIs(lookup_kwargs["current_user"], current_user)

        self.assertIsInstance(response, UploadSessionStateRead)
        self.assertIsInstance(response.preview_metadata, UploadPreviewMetadata)

        self.assertEqual(response.session_id, "upload-session-ret-beta")
        self.assertNotEqual(response.session_id, "upload-session-req-alpha")
        self.assertIs(response.status, UploadSessionStatus.READY)
        self.assertEqual(response.next_offset, 1024)
        self.assertEqual(response.received_bytes, 2048)
        self.assertEqual(response.total_bytes, 4096)
        self.assertEqual(response.final_chat_file_id, "chat-file-uuid-789")

        self.assertEqual(response.preview_metadata.width, 1920)
        self.assertEqual(response.preview_metadata.height, 1080)
        self.assertEqual(response.preview_metadata.duration_ms, 36000)
        self.assertEqual(
            response.preview_metadata.thumbnail,
            "https://example.com/media/preview.jpg",
        )
        self.assertEqual(
            response.preview_metadata.caption,
            "Sample video caption with whitespace padding",
        )
        self.assertEqual(response.preview_metadata.album_index, 2)
        self.assertEqual(response.preview_metadata.waveform, [15, 30, 45, 60, 75])

        self.assertEqual(
            raw_preview_metadata,
            {
                "width": 1920,
                "height": 1080,
                "duration_ms": 36000,
                "thumbnail": "https://example.com/media/preview.jpg",
                "caption": "   Sample video caption with whitespace padding   ",
                "album_index": 2,
                "waveform": [15, 30, 45, 60, 75],
            },
        )
        self.assertIs(session_double.preview_metadata, raw_preview_metadata)

    async def test_get_upload_session_state_none_preview_defaults_to_none_fields(self):
        db = SimpleNamespace(name="mock_db_session")
        current_user = SimpleNamespace(id=42, username="characterization_user")
        request_session_id = "upload-session-none-req-301"
        returned_session_id = "upload-session-none-ret-302"

        session_double = SimpleNamespace(
            id=returned_session_id,
            status=UploadSessionStatus.COMMITTED,
            next_offset=4096,
            received_bytes=4096,
            total_bytes=8192,
            preview_metadata=None,
            final_chat_file_id=None,
        )

        mock_lookup = AsyncMock(return_value=session_double)

        with patch(
            "api.routers.chat.get_upload_session_for_current_user",
            mock_lookup,
        ):
            response = await get_upload_session_state(
                session_id=request_session_id,
                current_user=current_user,
                db=db,
            )

        mock_lookup.assert_awaited_once_with(
            db,
            session_id=request_session_id,
            current_user=current_user,
        )
        self.assertEqual(mock_lookup.call_count, 1)
        self.assertEqual(mock_lookup.await_count, 1)
        lookup_args, lookup_kwargs = mock_lookup.await_args
        self.assertEqual(len(lookup_args), 1)
        self.assertIs(lookup_args[0], db)
        self.assertEqual(
            lookup_kwargs,
            {"session_id": request_session_id, "current_user": current_user},
        )
        self.assertIs(lookup_kwargs["current_user"], current_user)

        self.assertIsInstance(response, UploadSessionStateRead)
        self.assertIsInstance(response.preview_metadata, UploadPreviewMetadata)

        self.assertEqual(response.session_id, "upload-session-none-ret-302")
        self.assertNotEqual(response.session_id, "upload-session-none-req-301")
        self.assertIs(response.status, UploadSessionStatus.COMMITTED)
        self.assertEqual(response.next_offset, 4096)
        self.assertEqual(response.received_bytes, 4096)
        self.assertEqual(response.total_bytes, 8192)
        self.assertIsNone(response.final_chat_file_id)

        self.assertIsNone(response.preview_metadata.width)
        self.assertIsNone(response.preview_metadata.height)
        self.assertIsNone(response.preview_metadata.duration_ms)
        self.assertIsNone(response.preview_metadata.thumbnail)
        self.assertIsNone(response.preview_metadata.caption)
        self.assertIsNone(response.preview_metadata.album_index)
        self.assertIsNone(response.preview_metadata.waveform)
        self.assertEqual(
            response.preview_metadata.model_dump(),
            {
                "width": None,
                "height": None,
                "duration_ms": None,
                "thumbnail": None,
                "caption": None,
                "album_index": None,
                "waveform": None,
            },
        )

        self.assertIsNone(session_double.preview_metadata)

    async def test_get_upload_session_state_lookup_failure_propagates_without_response_construction(self):
        db = SimpleNamespace(name="mock_db_session")
        current_user = SimpleNamespace(id=42, username="characterization_user")
        request_session_id = "upload-session-fail-req-606"
        lookup_error = RuntimeError("Simulated service failure during upload session lookup")

        mock_lookup = AsyncMock(side_effect=lookup_error)

        with patch(
            "api.routers.chat.get_upload_session_for_current_user",
            mock_lookup,
        ):
            with patch(
                "api.routers.chat.UploadSessionStateRead",
                wraps=UploadSessionStateRead,
            ) as response_spy:
                with self.assertRaises(RuntimeError) as context:
                    await get_upload_session_state(
                        session_id=request_session_id,
                        current_user=current_user,
                        db=db,
                    )

        self.assertIs(context.exception, lookup_error)
        response_spy.assert_not_called()
        self.assertEqual(response_spy.call_count, 0)

        mock_lookup.assert_awaited_once_with(
            db,
            session_id=request_session_id,
            current_user=current_user,
        )
        self.assertEqual(mock_lookup.call_count, 1)
        self.assertEqual(mock_lookup.await_count, 1)
        lookup_args, lookup_kwargs = mock_lookup.await_args
        self.assertEqual(len(lookup_args), 1)
        self.assertIs(lookup_args[0], db)
        self.assertEqual(
            lookup_kwargs,
            {"session_id": request_session_id, "current_user": current_user},
        )
        self.assertIs(lookup_kwargs["current_user"], current_user)


if __name__ == "__main__":
    unittest.main()
