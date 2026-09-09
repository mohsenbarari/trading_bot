"""Supplemental characterization tests for PATCH upload-session chunk success.

Provides handler-only seam characterization evidence under Stage P1-00b and
Assignment WA-P1-00B-FREEZE-20260904-001 for seed:
API-MSG-PATCH-CHAT-PATCH-UPLOAD-SESSION-CHUNK-L2576
associated with tracked gap:
docs/refactor/two-server/section-1/inventory/scenario-records.json#P1-00-HIGH-SEED-HANDLER-ASSERTIONS-INCOMPLETE

This module characterizes the router handler success seam (partial and final chunks)
using synthetic in-memory objects and mocked dependencies. It does not execute
database persistence or realtime delivery, and does not claim the High gap or
Stage P1-00b closed.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import (
    UploadSessionChunkAppendResponse,
    patch_upload_session_chunk,
)
from models.upload_session import UploadSessionStatus


class TestUploadChunkSuccessCharacterization(unittest.IsolatedAsyncioTestCase):
    async def test_patch_upload_session_chunk_partial_success(self) -> None:
        target_session_id = "req-session-partial-001"
        target_resume_token = "resume-token-partial-001"
        target_offset = 1024
        target_is_last_chunk = False
        target_chunk_bytes = b"chunk-data-partial-payload-1024"

        fake_db = object()
        fake_current_user = object()

        looked_up_session = SimpleNamespace(
            id="looked-up-session-partial-aaa",
            received_bytes=512,
            next_offset=512,
            status=UploadSessionStatus.CREATED,
        )

        # Deliberate seam probe: The appended session uses distinct received_bytes (1536)
        # and next_offset (2048) sentinels to detect response-field aliasing, an appended id
        # distinct from both requested session_id and looked_up_session.id, and conflicting status.
        # This deliberate seam probe does not represent a persisted service state.
        appended_session = SimpleNamespace(
            id="appended-session-partial-bbb",
            received_bytes=1536,
            next_offset=2048,
            status=UploadSessionStatus.UPLOADING,
        )

        self.assertIsNot(looked_up_session, appended_session)
        self.assertNotEqual(target_session_id, appended_session.id)
        self.assertNotEqual(looked_up_session.id, appended_session.id)
        self.assertNotEqual(appended_session.received_bytes, appended_session.next_offset)
        self.assertNotEqual(looked_up_session.received_bytes, appended_session.received_bytes)
        self.assertNotEqual(looked_up_session.next_offset, appended_session.next_offset)
        self.assertNotEqual(looked_up_session.status, appended_session.status)

        events: list[str] = []

        async def fake_get_upload_session(
            db: object,
            *,
            session_id: str,
            current_user: object,
        ) -> object:
            self.assertEqual(events, [])
            self.assertIs(db, fake_db)
            self.assertEqual(session_id, target_session_id)
            self.assertIs(current_user, fake_current_user)
            events.append("lookup")
            return looked_up_session

        async def fake_read() -> bytes:
            self.assertEqual(events, ["lookup"])
            events.append("chunk.read")
            return target_chunk_bytes

        async def fake_close() -> None:
            self.assertEqual(events, ["lookup", "chunk.read"])
            events.append("chunk.close")
            return None

        synthetic_chunk = SimpleNamespace(
            read=AsyncMock(side_effect=fake_read, return_value=target_chunk_bytes),
            close=AsyncMock(side_effect=fake_close, return_value=None),
        )

        async def fake_append_upload_chunk(
            db: object,
            *,
            session: object,
            resume_token: str,
            offset: int,
            chunk_bytes: bytes,
            is_last_chunk: bool = False,
        ) -> object:
            self.assertEqual(events, ["lookup", "chunk.read", "chunk.close"])
            self.assertIs(db, fake_db)
            self.assertIs(session, looked_up_session)
            self.assertEqual(resume_token, target_resume_token)
            self.assertEqual(offset, target_offset)
            self.assertEqual(chunk_bytes, target_chunk_bytes)
            self.assertIs(is_last_chunk, False)
            events.append("append")
            return appended_session

        async def fake_publish_event(
            *,
            db: object,
            current_user: object,
            session: object,
            event_name: str,
            batch_status: object | None = None,
        ) -> None:
            self.assertEqual(events, ["lookup", "chunk.read", "chunk.close", "append"])
            self.assertIs(db, fake_db)
            self.assertIs(current_user, fake_current_user)
            self.assertIs(session, appended_session)
            self.assertEqual(event_name, "progress")
            events.append("progress_publication")
            return None

        real_response_cls = UploadSessionChunkAppendResponse

        def response_spy(*args: Any, **kwargs: Any) -> UploadSessionChunkAppendResponse:
            self.assertEqual(
                events,
                [
                    "lookup",
                    "chunk.read",
                    "chunk.close",
                    "append",
                    "progress_publication",
                ],
            )
            events.append("response_construction")
            return real_response_cls(*args, **kwargs)

        with patch(
            "api.routers.chat.get_upload_session_for_current_user",
            new_callable=AsyncMock,
            side_effect=fake_get_upload_session,
        ) as mock_get_upload_session, patch(
            "api.routers.chat.append_upload_chunk",
            new_callable=AsyncMock,
            side_effect=fake_append_upload_chunk,
        ) as mock_append_upload_chunk, patch(
            "api.routers.chat._publish_upload_session_runtime_event",
            new_callable=AsyncMock,
            side_effect=fake_publish_event,
        ) as mock_publish_event, patch(
            "api.routers.chat.UploadSessionChunkAppendResponse",
            side_effect=response_spy,
        ) as mock_response_constructor:
            response = await patch_upload_session_chunk(
                session_id=target_session_id,
                resume_token=target_resume_token,
                offset=target_offset,
                is_last_chunk=target_is_last_chunk,
                chunk=synthetic_chunk,
                current_user=fake_current_user,
                db=fake_db,
            )

        self.assertEqual(
            events,
            [
                "lookup",
                "chunk.read",
                "chunk.close",
                "append",
                "progress_publication",
                "response_construction",
            ],
        )

        self.assertEqual(mock_get_upload_session.await_count, 1)
        mock_get_upload_session.assert_awaited_once_with(
            fake_db,
            session_id=target_session_id,
            current_user=fake_current_user,
        )

        self.assertEqual(synthetic_chunk.read.await_count, 1)
        synthetic_chunk.read.assert_awaited_once_with()

        self.assertEqual(synthetic_chunk.close.await_count, 1)
        synthetic_chunk.close.assert_awaited_once_with()

        self.assertEqual(mock_append_upload_chunk.await_count, 1)
        mock_append_upload_chunk.assert_awaited_once_with(
            fake_db,
            session=looked_up_session,
            resume_token=target_resume_token,
            offset=target_offset,
            chunk_bytes=target_chunk_bytes,
            is_last_chunk=False,
        )

        self.assertEqual(mock_publish_event.await_count, 1)
        mock_publish_event.assert_awaited_once_with(
            db=fake_db,
            current_user=fake_current_user,
            session=appended_session,
            event_name="progress",
        )

        self.assertEqual(mock_response_constructor.call_count, 1)
        mock_response_constructor.assert_called_once_with(
            session_id="appended-session-partial-bbb",
            received_bytes=1536,
            next_offset=2048,
            status=UploadSessionStatus.UPLOADING,
        )

        self.assertIsInstance(response, UploadSessionChunkAppendResponse)
        self.assertIs(type(response), UploadSessionChunkAppendResponse)

        self.assertEqual(response.session_id, "appended-session-partial-bbb")
        self.assertEqual(response.received_bytes, 1536)
        self.assertEqual(response.next_offset, 2048)
        self.assertIs(response.status, UploadSessionStatus.UPLOADING)

        serialized = response.model_dump()
        self.assertEqual(
            set(serialized.keys()),
            {"session_id", "received_bytes", "next_offset", "status"},
        )
        self.assertEqual(
            serialized,
            {
                "session_id": "appended-session-partial-bbb",
                "received_bytes": 1536,
                "next_offset": 2048,
                "status": UploadSessionStatus.UPLOADING,
            },
        )
        self.assertEqual(serialized["session_id"], "appended-session-partial-bbb")
        self.assertEqual(serialized["received_bytes"], 1536)
        self.assertEqual(serialized["next_offset"], 2048)
        self.assertEqual(serialized["status"], UploadSessionStatus.UPLOADING)

    async def test_patch_upload_session_chunk_final_success(self) -> None:
        target_session_id = "req-session-final-002"
        target_resume_token = "resume-token-final-002"
        target_offset = 4096
        target_is_last_chunk = True
        target_chunk_bytes = b"chunk-data-final-payload-4096"

        fake_db = object()
        fake_current_user = object()

        looked_up_session = SimpleNamespace(
            id="looked-up-session-final-ccc",
            received_bytes=4096,
            next_offset=4096,
            status=UploadSessionStatus.UPLOADING,
        )

        # Deliberate seam probe: The appended session uses distinct received_bytes (7168)
        # and next_offset (8192) sentinels to detect response-field aliasing, an appended id
        # distinct from both requested session_id and looked_up_session.id, and conflicting status.
        # This deliberate seam probe does not represent a persisted service state.
        appended_session = SimpleNamespace(
            id="appended-session-final-ddd",
            received_bytes=7168,
            next_offset=8192,
            status=UploadSessionStatus.UPLOADED,
        )

        self.assertIsNot(looked_up_session, appended_session)
        self.assertNotEqual(target_session_id, appended_session.id)
        self.assertNotEqual(looked_up_session.id, appended_session.id)
        self.assertNotEqual(appended_session.received_bytes, appended_session.next_offset)
        self.assertNotEqual(looked_up_session.received_bytes, appended_session.received_bytes)
        self.assertNotEqual(looked_up_session.next_offset, appended_session.next_offset)
        self.assertNotEqual(looked_up_session.status, appended_session.status)

        events: list[str] = []

        async def fake_get_upload_session(
            db: object,
            *,
            session_id: str,
            current_user: object,
        ) -> object:
            self.assertEqual(events, [])
            self.assertIs(db, fake_db)
            self.assertEqual(session_id, target_session_id)
            self.assertIs(current_user, fake_current_user)
            events.append("lookup")
            return looked_up_session

        async def fake_read() -> bytes:
            self.assertEqual(events, ["lookup"])
            events.append("chunk.read")
            return target_chunk_bytes

        async def fake_close() -> None:
            self.assertEqual(events, ["lookup", "chunk.read"])
            events.append("chunk.close")
            return None

        synthetic_chunk = SimpleNamespace(
            read=AsyncMock(side_effect=fake_read, return_value=target_chunk_bytes),
            close=AsyncMock(side_effect=fake_close, return_value=None),
        )

        async def fake_append_upload_chunk(
            db: object,
            *,
            session: object,
            resume_token: str,
            offset: int,
            chunk_bytes: bytes,
            is_last_chunk: bool = False,
        ) -> object:
            self.assertEqual(events, ["lookup", "chunk.read", "chunk.close"])
            self.assertIs(db, fake_db)
            self.assertIs(session, looked_up_session)
            self.assertEqual(resume_token, target_resume_token)
            self.assertEqual(offset, target_offset)
            self.assertEqual(chunk_bytes, target_chunk_bytes)
            self.assertIs(is_last_chunk, True)
            events.append("append")
            return appended_session

        async def fake_publish_event(
            *,
            db: object,
            current_user: object,
            session: object,
            event_name: str,
            batch_status: object | None = None,
        ) -> None:
            self.assertEqual(events, ["lookup", "chunk.read", "chunk.close", "append"])
            self.assertIs(db, fake_db)
            self.assertIs(current_user, fake_current_user)
            self.assertIs(session, appended_session)
            self.assertEqual(event_name, "progress")
            events.append("progress_publication")
            return None

        real_response_cls = UploadSessionChunkAppendResponse

        def response_spy(*args: Any, **kwargs: Any) -> UploadSessionChunkAppendResponse:
            self.assertEqual(
                events,
                [
                    "lookup",
                    "chunk.read",
                    "chunk.close",
                    "append",
                    "progress_publication",
                ],
            )
            events.append("response_construction")
            return real_response_cls(*args, **kwargs)

        with patch(
            "api.routers.chat.get_upload_session_for_current_user",
            new_callable=AsyncMock,
            side_effect=fake_get_upload_session,
        ) as mock_get_upload_session, patch(
            "api.routers.chat.append_upload_chunk",
            new_callable=AsyncMock,
            side_effect=fake_append_upload_chunk,
        ) as mock_append_upload_chunk, patch(
            "api.routers.chat._publish_upload_session_runtime_event",
            new_callable=AsyncMock,
            side_effect=fake_publish_event,
        ) as mock_publish_event, patch(
            "api.routers.chat.UploadSessionChunkAppendResponse",
            side_effect=response_spy,
        ) as mock_response_constructor:
            response = await patch_upload_session_chunk(
                session_id=target_session_id,
                resume_token=target_resume_token,
                offset=target_offset,
                is_last_chunk=target_is_last_chunk,
                chunk=synthetic_chunk,
                current_user=fake_current_user,
                db=fake_db,
            )

        self.assertEqual(
            events,
            [
                "lookup",
                "chunk.read",
                "chunk.close",
                "append",
                "progress_publication",
                "response_construction",
            ],
        )

        self.assertEqual(mock_get_upload_session.await_count, 1)
        mock_get_upload_session.assert_awaited_once_with(
            fake_db,
            session_id=target_session_id,
            current_user=fake_current_user,
        )

        self.assertEqual(synthetic_chunk.read.await_count, 1)
        synthetic_chunk.read.assert_awaited_once_with()

        self.assertEqual(synthetic_chunk.close.await_count, 1)
        synthetic_chunk.close.assert_awaited_once_with()

        self.assertEqual(mock_append_upload_chunk.await_count, 1)
        mock_append_upload_chunk.assert_awaited_once_with(
            fake_db,
            session=looked_up_session,
            resume_token=target_resume_token,
            offset=target_offset,
            chunk_bytes=target_chunk_bytes,
            is_last_chunk=True,
        )

        self.assertEqual(mock_publish_event.await_count, 1)
        mock_publish_event.assert_awaited_once_with(
            db=fake_db,
            current_user=fake_current_user,
            session=appended_session,
            event_name="progress",
        )

        self.assertEqual(mock_response_constructor.call_count, 1)
        mock_response_constructor.assert_called_once_with(
            session_id="appended-session-final-ddd",
            received_bytes=7168,
            next_offset=8192,
            status=UploadSessionStatus.UPLOADED,
        )

        self.assertIsInstance(response, UploadSessionChunkAppendResponse)
        self.assertIs(type(response), UploadSessionChunkAppendResponse)

        self.assertEqual(response.session_id, "appended-session-final-ddd")
        self.assertEqual(response.received_bytes, 7168)
        self.assertEqual(response.next_offset, 8192)
        self.assertIs(response.status, UploadSessionStatus.UPLOADED)

        serialized = response.model_dump()
        self.assertEqual(
            set(serialized.keys()),
            {"session_id", "received_bytes", "next_offset", "status"},
        )
        self.assertEqual(
            serialized,
            {
                "session_id": "appended-session-final-ddd",
                "received_bytes": 7168,
                "next_offset": 8192,
                "status": UploadSessionStatus.UPLOADED,
            },
        )
        self.assertEqual(serialized["session_id"], "appended-session-final-ddd")
        self.assertEqual(serialized["received_bytes"], 7168)
        self.assertEqual(serialized["next_offset"], 8192)
        self.assertEqual(serialized["status"], UploadSessionStatus.UPLOADED)


if __name__ == "__main__":
    unittest.main()
