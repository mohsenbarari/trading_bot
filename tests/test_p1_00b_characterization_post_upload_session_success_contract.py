"""Characterization tests for post_upload_session success contract.

Seed: API-MSG-POST-CHAT-POST-UPLOAD-SESSION-L2540
Tracked inventory gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Stage card: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment ID: WA-P1-00B-FREEZE-20260904-001

This module provides handler success contract evidence using mocked collaborator boundaries
(create_upload_session and _publish_upload_session_runtime_event), not durable persistence,
event delivery, concurrency, process reload, seed closure, or Stage acceptance.
Preserves P1-00 COMPLETE, P1-00b IN_PROGRESS and the canonical BLOCKED_EXPLICIT blocker state.
Tests are proposed for independent review and controller execution; no tests are claimed to have run.
"""

from __future__ import annotations

# Install fail-closed network fence before importing application code
from tests.test_p1_00_characterization_network_isolation import install_p1_00_network_fence

install_p1_00_network_fence()

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest import mock

from api.routers.chat import post_upload_session
from api.routers.chat_schemas import (
    UploadPreviewMetadata,
    UploadSessionCreateRequest,
    UploadSessionCreateResponse,
)
from models.upload_session import (
    UploadMediaType,
    UploadRoomKind,
    UploadSessionStatus,
)


class PostUploadSessionSuccessContractCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterizes the success contract of api.routers.chat.post_upload_session."""

    async def test_post_upload_session_success_populated_scenario(self) -> None:
        execution_order: list[str] = []

        fake_user = SimpleNamespace(id=42)
        fake_db = SimpleNamespace()

        fake_session = SimpleNamespace(
            id="synthetic-session-uuid-populated-101",
            resume_token="synthetic-resume-token-populated-202",
            next_offset=1048576,
            chunk_size=1048576,
            expires_at=datetime(2026, 9, 10, 18, 0, 0, tzinfo=timezone.utc),
            status=UploadSessionStatus.READY,
        )

        async def fake_create(*args: object, **kwargs: object) -> object:
            execution_order.append("service_completed")
            return fake_session

        async def fake_publish(*args: object, **kwargs: object) -> None:
            self.assertEqual(execution_order, ["service_completed"])
            execution_order.append("publication_completed")

        def response_spy_constructor(*args: object, **kwargs: object) -> UploadSessionCreateResponse:
            self.assertEqual(execution_order, ["service_completed", "publication_completed"])
            execution_order.append("response_constructed")
            return UploadSessionCreateResponse(*args, **kwargs)

        mock_create = mock.AsyncMock(side_effect=fake_create)
        mock_publish = mock.AsyncMock(side_effect=fake_publish)
        response_spy = mock.MagicMock(side_effect=response_spy_constructor)

        preview = UploadPreviewMetadata(
            width=1920,
            height=1080,
            duration_ms=None,
            thumbnail="thumbnail-sample-base64",
            caption="   team offsite presentation slides   ",
            album_index=0,
            waveform=None,
        )
        request_data = UploadSessionCreateRequest(
            batch_id="batch-populated-xyz-987",
            room_kind=UploadRoomKind.GROUP,
            target_id=2026,
            media_type=UploadMediaType.VIDEO,
            file_name="   presentation_video_final.mp4   ",
            mime_type="video/mp4",
            total_bytes=15728640,
            chunk_size=2097152,
            preview_metadata=preview,
            sha256_full="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        )

        expected_preview_metadata = {
            "width": 1920,
            "height": 1080,
            "thumbnail": "thumbnail-sample-base64",
            "caption": "team offsite presentation slides",
            "album_index": 0,
        }

        with mock.patch("api.routers.chat.create_upload_session", mock_create), \
             mock.patch("api.routers.chat._publish_upload_session_runtime_event", mock_publish), \
             mock.patch("api.routers.chat.UploadSessionCreateResponse", response_spy):
            result = await post_upload_session(request_data, current_user=fake_user, db=fake_db)

        self.assertEqual(
            execution_order,
            ["service_completed", "publication_completed", "response_constructed"],
        )

        self.assertEqual(mock_create.call_count, 1)
        self.assertEqual(mock_create.await_count, 1)
        self.assertEqual(mock_create.call_args, mock_create.await_args)

        create_args, create_kwargs = mock_create.call_args
        self.assertEqual(len(create_args), 1)
        self.assertIs(create_args[0], fake_db)
        self.assertEqual(
            set(create_kwargs.keys()),
            {
                "current_user",
                "batch_id",
                "room_kind",
                "target_id",
                "media_type",
                "file_name",
                "mime_type",
                "total_bytes",
                "chunk_size",
                "preview_metadata",
                "sha256_full",
            },
        )
        self.assertIs(create_kwargs["current_user"], fake_user)
        self.assertEqual(create_kwargs["batch_id"], "batch-populated-xyz-987")
        self.assertIs(create_kwargs["room_kind"], UploadRoomKind.GROUP)
        self.assertEqual(create_kwargs["target_id"], 2026)
        self.assertNotEqual(create_kwargs["target_id"], fake_user.id)
        self.assertIs(create_kwargs["media_type"], UploadMediaType.VIDEO)
        self.assertEqual(create_kwargs["file_name"], "presentation_video_final.mp4")
        self.assertEqual(create_kwargs["mime_type"], "video/mp4")
        self.assertEqual(create_kwargs["total_bytes"], 15728640)
        self.assertEqual(create_kwargs["chunk_size"], 2097152)
        self.assertEqual(create_kwargs["preview_metadata"], expected_preview_metadata)
        self.assertEqual(
            create_kwargs["sha256_full"],
            "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        )

        self.assertNotIn("duration_ms", create_kwargs["preview_metadata"])
        self.assertNotIn("waveform", create_kwargs["preview_metadata"])
        self.assertIn("album_index", create_kwargs["preview_metadata"])
        self.assertEqual(create_kwargs["preview_metadata"]["album_index"], 0)

        self.assertEqual(mock_publish.call_count, 1)
        self.assertEqual(mock_publish.await_count, 1)
        self.assertEqual(mock_publish.call_args, mock_publish.await_args)

        pub_args, pub_kwargs = mock_publish.call_args
        self.assertEqual(pub_args, ())
        self.assertEqual(set(pub_kwargs.keys()), {"db", "current_user", "session", "event_name"})
        self.assertIs(pub_kwargs["db"], fake_db)
        self.assertIs(pub_kwargs["current_user"], fake_user)
        self.assertIs(pub_kwargs["session"], fake_session)
        self.assertEqual(pub_kwargs["event_name"], "created")

        self.assertEqual(response_spy.call_count, 1)
        resp_args, resp_kwargs = response_spy.call_args
        self.assertEqual(resp_args, ())
        self.assertEqual(
            set(resp_kwargs.keys()),
            {"session_id", "resume_token", "next_offset", "chunk_size", "expires_at", "status"},
        )
        self.assertEqual(resp_kwargs["session_id"], fake_session.id)
        self.assertEqual(resp_kwargs["resume_token"], fake_session.resume_token)
        self.assertEqual(resp_kwargs["next_offset"], fake_session.next_offset)
        self.assertEqual(resp_kwargs["chunk_size"], fake_session.chunk_size)
        self.assertEqual(resp_kwargs["expires_at"], fake_session.expires_at)
        self.assertEqual(resp_kwargs["status"], fake_session.status)

        self.assertIsInstance(result, UploadSessionCreateResponse)
        self.assertEqual(result.session_id, "synthetic-session-uuid-populated-101")
        self.assertEqual(result.resume_token, "synthetic-resume-token-populated-202")
        self.assertEqual(result.next_offset, 1048576)
        self.assertEqual(result.chunk_size, 1048576)
        self.assertNotEqual(result.chunk_size, request_data.chunk_size)
        self.assertEqual(result.expires_at, datetime(2026, 9, 10, 18, 0, 0, tzinfo=timezone.utc))
        self.assertIs(result.status, UploadSessionStatus.READY)

        expected_response_dump = {
            "session_id": "synthetic-session-uuid-populated-101",
            "resume_token": "synthetic-resume-token-populated-202",
            "next_offset": 1048576,
            "chunk_size": 1048576,
            "expires_at": datetime(2026, 9, 10, 18, 0, 0, tzinfo=timezone.utc),
            "status": UploadSessionStatus.READY,
        }
        self.assertEqual(result.model_dump(), expected_response_dump)

    async def test_post_upload_session_success_default_scenario(self) -> None:
        execution_order: list[str] = []

        fake_user = SimpleNamespace(id=99)
        fake_db = SimpleNamespace()

        fake_session = SimpleNamespace(
            id="synthetic-session-uuid-default-303",
            resume_token="synthetic-resume-token-default-404",
            next_offset=262144,
            chunk_size=524288,
            expires_at=datetime(2026, 9, 11, 9, 0, 0, tzinfo=timezone.utc),
            status=UploadSessionStatus.CREATED,
        )

        async def fake_create(*args: object, **kwargs: object) -> object:
            execution_order.append("service_completed")
            return fake_session

        async def fake_publish(*args: object, **kwargs: object) -> None:
            self.assertEqual(execution_order, ["service_completed"])
            execution_order.append("publication_completed")

        def response_spy_constructor(*args: object, **kwargs: object) -> UploadSessionCreateResponse:
            self.assertEqual(execution_order, ["service_completed", "publication_completed"])
            execution_order.append("response_constructed")
            return UploadSessionCreateResponse(*args, **kwargs)

        mock_create = mock.AsyncMock(side_effect=fake_create)
        mock_publish = mock.AsyncMock(side_effect=fake_publish)
        response_spy = mock.MagicMock(side_effect=response_spy_constructor)

        request_data = UploadSessionCreateRequest(
            room_kind=UploadRoomKind.DIRECT,
            target_id=888,
            media_type=UploadMediaType.DOCUMENT,
            file_name="standard_document.pdf",
            mime_type="application/pdf",
            total_bytes=5242880,
            chunk_size=1048576,
        )

        with mock.patch("api.routers.chat.create_upload_session", mock_create), \
             mock.patch("api.routers.chat._publish_upload_session_runtime_event", mock_publish), \
             mock.patch("api.routers.chat.UploadSessionCreateResponse", response_spy):
            result = await post_upload_session(request_data, current_user=fake_user, db=fake_db)

        self.assertEqual(
            execution_order,
            ["service_completed", "publication_completed", "response_constructed"],
        )

        self.assertEqual(mock_create.call_count, 1)
        self.assertEqual(mock_create.await_count, 1)
        self.assertEqual(mock_create.call_args, mock_create.await_args)

        create_args, create_kwargs = mock_create.call_args
        self.assertEqual(len(create_args), 1)
        self.assertIs(create_args[0], fake_db)
        self.assertEqual(
            set(create_kwargs.keys()),
            {
                "current_user",
                "batch_id",
                "room_kind",
                "target_id",
                "media_type",
                "file_name",
                "mime_type",
                "total_bytes",
                "chunk_size",
                "preview_metadata",
                "sha256_full",
            },
        )
        self.assertIs(create_kwargs["current_user"], fake_user)
        self.assertIsNone(create_kwargs["batch_id"])
        self.assertIs(create_kwargs["room_kind"], UploadRoomKind.DIRECT)
        self.assertEqual(create_kwargs["target_id"], 888)
        self.assertNotEqual(create_kwargs["target_id"], fake_user.id)
        self.assertIs(create_kwargs["media_type"], UploadMediaType.DOCUMENT)
        self.assertEqual(create_kwargs["file_name"], "standard_document.pdf")
        self.assertEqual(create_kwargs["mime_type"], "application/pdf")
        self.assertEqual(create_kwargs["total_bytes"], 5242880)
        self.assertEqual(create_kwargs["chunk_size"], 1048576)
        self.assertEqual(create_kwargs["preview_metadata"], {})
        self.assertIsNone(create_kwargs["sha256_full"])

        self.assertEqual(mock_publish.call_count, 1)
        self.assertEqual(mock_publish.await_count, 1)
        self.assertEqual(mock_publish.call_args, mock_publish.await_args)

        pub_args, pub_kwargs = mock_publish.call_args
        self.assertEqual(pub_args, ())
        self.assertEqual(set(pub_kwargs.keys()), {"db", "current_user", "session", "event_name"})
        self.assertIs(pub_kwargs["db"], fake_db)
        self.assertIs(pub_kwargs["current_user"], fake_user)
        self.assertIs(pub_kwargs["session"], fake_session)
        self.assertEqual(pub_kwargs["event_name"], "created")

        self.assertEqual(response_spy.call_count, 1)
        resp_args, resp_kwargs = response_spy.call_args
        self.assertEqual(resp_args, ())
        self.assertEqual(
            set(resp_kwargs.keys()),
            {"session_id", "resume_token", "next_offset", "chunk_size", "expires_at", "status"},
        )
        self.assertEqual(resp_kwargs["session_id"], fake_session.id)
        self.assertEqual(resp_kwargs["resume_token"], fake_session.resume_token)
        self.assertEqual(resp_kwargs["next_offset"], fake_session.next_offset)
        self.assertEqual(resp_kwargs["chunk_size"], fake_session.chunk_size)
        self.assertEqual(resp_kwargs["expires_at"], fake_session.expires_at)
        self.assertEqual(resp_kwargs["status"], fake_session.status)

        self.assertIsInstance(result, UploadSessionCreateResponse)
        self.assertEqual(result.session_id, "synthetic-session-uuid-default-303")
        self.assertEqual(result.resume_token, "synthetic-resume-token-default-404")
        self.assertEqual(result.next_offset, 262144)
        self.assertEqual(result.chunk_size, 524288)
        self.assertNotEqual(result.chunk_size, request_data.chunk_size)
        self.assertEqual(result.expires_at, datetime(2026, 9, 11, 9, 0, 0, tzinfo=timezone.utc))
        self.assertIs(result.status, UploadSessionStatus.CREATED)

        expected_response_dump = {
            "session_id": "synthetic-session-uuid-default-303",
            "resume_token": "synthetic-resume-token-default-404",
            "next_offset": 262144,
            "chunk_size": 524288,
            "expires_at": datetime(2026, 9, 11, 9, 0, 0, tzinfo=timezone.utc),
            "status": UploadSessionStatus.CREATED,
        }
        self.assertEqual(result.model_dump(), expected_response_dump)


if __name__ == "__main__":
    unittest.main()
