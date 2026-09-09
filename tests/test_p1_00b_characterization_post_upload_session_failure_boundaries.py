"""Characterization tests for post_upload_session failure boundaries.

Seed: API-MSG-POST-CHAT-POST-UPLOAD-SESSION-L2540
Tracked inventory gap: docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE
Stage card: docs/refactor/two-server/section-1/stages/P1-00b.md
Assignment ID: WA-P1-00B-FREEZE-20260904-001

This module provides handler boundary evidence using mocked service/publication boundaries,
not durable persistence, event delivery, concurrency/reload or Stage/gap closure.
Preserves P1-00 COMPLETE, P1-00b IN_PROGRESS and the canonical BLOCKED_EXPLICIT blocker.
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


class PostUploadSessionFailureBoundariesCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    """Characterizes exception boundaries in api.routers.chat.post_upload_session."""

    async def test_creation_failure_boundary(self) -> None:
        execution_order: list[str] = []
        creation_error = RuntimeError("unique-creation-failure-boundary-error-1")

        async def fake_create(*args: object, **kwargs: object) -> object:
            execution_order.append("create_upload_session")
            raise creation_error

        async def fake_publish(*args: object, **kwargs: object) -> None:
            execution_order.append("_publish_upload_session_runtime_event")

        mock_create = mock.AsyncMock(side_effect=fake_create)
        mock_publish = mock.AsyncMock(side_effect=fake_publish)
        response_spy = mock.MagicMock(wraps=UploadSessionCreateResponse)

        fake_user = SimpleNamespace(id=42)
        fake_db = SimpleNamespace()

        preview = UploadPreviewMetadata(
            width=1920,
            height=1080,
            caption="  photo caption  ",
            album_index=0,
            duration_ms=None,
            thumbnail=None,
            waveform=None,
        )
        request_data = UploadSessionCreateRequest(
            batch_id="batch-non-default-9876",
            room_kind=UploadRoomKind.DIRECT,
            target_id=777,
            media_type=UploadMediaType.IMAGE,
            file_name="   avatar_image.png   ",
            mime_type="image/png",
            total_bytes=2097152,
            chunk_size=524288,
            preview_metadata=preview,
            sha256_full="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )

        expected_preview_metadata = {
            "width": 1920,
            "height": 1080,
            "caption": "photo caption",
            "album_index": 0,
        }

        with mock.patch("api.routers.chat.create_upload_session", mock_create), \
             mock.patch("api.routers.chat._publish_upload_session_runtime_event", mock_publish), \
             mock.patch("api.routers.chat.UploadSessionCreateResponse", response_spy):
            with self.assertRaises(RuntimeError) as ctx:
                await post_upload_session(request_data, current_user=fake_user, db=fake_db)

        self.assertIs(ctx.exception, creation_error)
        self.assertEqual(execution_order, ["create_upload_session"])

        self.assertEqual(mock_create.call_count, 1)
        self.assertEqual(mock_create.await_count, 1)
        self.assertEqual(mock_create.call_args, mock_create.await_args)

        create_args, create_kwargs = mock_create.call_args
        self.assertEqual(len(create_args), 1)
        self.assertIs(create_args[0], fake_db)
        self.assertIs(create_kwargs["current_user"], fake_user)
        self.assertEqual(create_kwargs["batch_id"], "batch-non-default-9876")
        self.assertIs(create_kwargs["room_kind"], UploadRoomKind.DIRECT)
        self.assertEqual(create_kwargs["target_id"], 777)
        self.assertNotEqual(create_kwargs["target_id"], fake_user.id)
        self.assertIs(create_kwargs["media_type"], UploadMediaType.IMAGE)
        self.assertEqual(create_kwargs["file_name"], "avatar_image.png")
        self.assertEqual(create_kwargs["mime_type"], "image/png")
        self.assertEqual(create_kwargs["total_bytes"], 2097152)
        self.assertEqual(create_kwargs["chunk_size"], 524288)
        self.assertEqual(
            create_kwargs["sha256_full"],
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )
        self.assertEqual(create_kwargs["preview_metadata"], expected_preview_metadata)
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

        mock_publish.assert_not_called()
        mock_publish.assert_not_awaited()
        response_spy.assert_not_called()

    async def test_publication_failure_boundary(self) -> None:
        execution_order: list[str] = []
        publication_error = RuntimeError("unique-publication-failure-boundary-error-2")

        fake_user = SimpleNamespace(id=42)
        fake_db = SimpleNamespace()

        expected_preview_metadata = {
            "width": 1920,
            "height": 1080,
            "caption": "photo caption",
            "album_index": 0,
        }

        fake_session = SimpleNamespace(
            id="session-uuid-test-1234",
            resume_token="resume-token-test-5678",
            next_offset=0,
            chunk_size=524288,
            expires_at=datetime(2026, 9, 10, 12, 0, 0, tzinfo=timezone.utc),
            status=UploadSessionStatus.CREATED,
            batch_id="batch-non-default-9876",
            room_kind=UploadRoomKind.DIRECT,
            target_id=777,
            media_type=UploadMediaType.IMAGE,
            file_name="avatar_image.png",
            mime_type="image/png",
            total_bytes=2097152,
            preview_metadata=expected_preview_metadata,
            sha256_full="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )

        async def fake_create(*args: object, **kwargs: object) -> object:
            execution_order.append("create_upload_session")
            return fake_session

        async def fake_publish(*args: object, **kwargs: object) -> None:
            execution_order.append("_publish_upload_session_runtime_event")
            raise publication_error

        mock_create = mock.AsyncMock(side_effect=fake_create)
        mock_publish = mock.AsyncMock(side_effect=fake_publish)
        response_spy = mock.MagicMock(wraps=UploadSessionCreateResponse)

        preview = UploadPreviewMetadata(
            width=1920,
            height=1080,
            caption="  photo caption  ",
            album_index=0,
            duration_ms=None,
            thumbnail=None,
            waveform=None,
        )
        request_data = UploadSessionCreateRequest(
            batch_id="batch-non-default-9876",
            room_kind=UploadRoomKind.DIRECT,
            target_id=777,
            media_type=UploadMediaType.IMAGE,
            file_name="   avatar_image.png   ",
            mime_type="image/png",
            total_bytes=2097152,
            chunk_size=524288,
            preview_metadata=preview,
            sha256_full="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )

        with mock.patch("api.routers.chat.create_upload_session", mock_create), \
             mock.patch("api.routers.chat._publish_upload_session_runtime_event", mock_publish), \
             mock.patch("api.routers.chat.UploadSessionCreateResponse", response_spy):
            with self.assertRaises(RuntimeError) as ctx:
                await post_upload_session(request_data, current_user=fake_user, db=fake_db)

        self.assertIs(ctx.exception, publication_error)
        self.assertEqual(execution_order, ["create_upload_session", "_publish_upload_session_runtime_event"])

        self.assertEqual(mock_create.call_count, 1)
        self.assertEqual(mock_create.await_count, 1)
        self.assertEqual(mock_create.call_args, mock_create.await_args)

        create_args, create_kwargs = mock_create.call_args
        self.assertEqual(len(create_args), 1)
        self.assertIs(create_args[0], fake_db)
        self.assertIs(create_kwargs["current_user"], fake_user)
        self.assertEqual(create_kwargs["batch_id"], "batch-non-default-9876")
        self.assertIs(create_kwargs["room_kind"], UploadRoomKind.DIRECT)
        self.assertEqual(create_kwargs["target_id"], 777)
        self.assertNotEqual(create_kwargs["target_id"], fake_user.id)
        self.assertIs(create_kwargs["media_type"], UploadMediaType.IMAGE)
        self.assertEqual(create_kwargs["file_name"], "avatar_image.png")
        self.assertEqual(create_kwargs["mime_type"], "image/png")
        self.assertEqual(create_kwargs["total_bytes"], 2097152)
        self.assertEqual(create_kwargs["chunk_size"], 524288)
        self.assertEqual(
            create_kwargs["sha256_full"],
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        )
        self.assertEqual(create_kwargs["preview_metadata"], expected_preview_metadata)
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

        self.assertEqual(mock_publish.call_count, 1)
        self.assertEqual(mock_publish.await_count, 1)
        self.assertEqual(mock_publish.call_args, mock_publish.await_args)

        pub_args, pub_kwargs = mock_publish.call_args
        self.assertEqual(pub_args, ())
        self.assertIs(pub_kwargs["db"], fake_db)
        self.assertIs(pub_kwargs["current_user"], fake_user)
        self.assertIs(pub_kwargs["session"], fake_session)
        self.assertEqual(pub_kwargs["event_name"], "created")
        self.assertEqual(set(pub_kwargs.keys()), {"db", "current_user", "session", "event_name"})

        response_spy.assert_not_called()


if __name__ == "__main__":
    unittest.main()
