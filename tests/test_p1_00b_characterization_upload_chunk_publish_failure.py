"""Characterization test for PATCH upload-session chunk publish failure."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import (
    UploadSessionChunkAppendResponse,
    patch_upload_session_chunk,
)


class TestUploadChunkPublishFailureCharacterization(unittest.IsolatedAsyncioTestCase):
    async def test_chunk_publish_failure_propagates_after_successful_append(self) -> None:
        publish_error = RuntimeError("Synthetic upload session chunk publish failure")
        chunk_payload = b"synthetic-publish-chunk-data"

        looked_up_session = object()
        appended_session = object()
        self.assertIsNot(looked_up_session, appended_session)

        fake_db = object()
        fake_current_user = object()

        target_session_id = "session-test-id-publish-001"
        target_resume_token = "token-test-resume-publish-001"
        target_offset = 4096
        target_is_last_chunk = True

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
            events.append("read")
            return chunk_payload

        async def fake_close() -> None:
            self.assertEqual(events, ["lookup", "read"])
            events.append("close")
            return None

        synthetic_chunk = SimpleNamespace(
            read=AsyncMock(side_effect=fake_read, return_value=chunk_payload),
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
            self.assertEqual(events, ["lookup", "read", "close"])
            self.assertIs(db, fake_db)
            self.assertIs(session, looked_up_session)
            self.assertEqual(resume_token, target_resume_token)
            self.assertEqual(offset, target_offset)
            self.assertEqual(chunk_bytes, chunk_payload)
            self.assertEqual(is_last_chunk, target_is_last_chunk)
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
            self.assertEqual(events, ["lookup", "read", "close", "append"])
            self.assertIs(db, fake_db)
            self.assertIs(current_user, fake_current_user)
            self.assertIs(session, appended_session)
            self.assertEqual(event_name, "progress")
            events.append("publication failure")
            raise publish_error

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
            wraps=UploadSessionChunkAppendResponse,
        ) as mock_response_constructor:
            with self.assertRaises(RuntimeError) as exc_info:
                await patch_upload_session_chunk(
                    session_id=target_session_id,
                    resume_token=target_resume_token,
                    offset=target_offset,
                    is_last_chunk=target_is_last_chunk,
                    chunk=synthetic_chunk,
                    current_user=fake_current_user,
                    db=fake_db,
                )

            self.assertIs(exc_info.exception, publish_error)
            mock_response_constructor.assert_not_called()
            self.assertEqual(
                events,
                ["lookup", "read", "close", "append", "publication failure"],
            )

            mock_get_upload_session.assert_awaited_once_with(
                fake_db,
                session_id=target_session_id,
                current_user=fake_current_user,
            )
            synthetic_chunk.read.assert_awaited_once_with()
            synthetic_chunk.close.assert_awaited_once_with()
            mock_append_upload_chunk.assert_awaited_once_with(
                fake_db,
                session=looked_up_session,
                resume_token=target_resume_token,
                offset=target_offset,
                chunk_bytes=chunk_payload,
                is_last_chunk=target_is_last_chunk,
            )
            mock_publish_event.assert_awaited_once_with(
                db=fake_db,
                current_user=fake_current_user,
                session=appended_session,
                event_name="progress",
            )


if __name__ == "__main__":
    unittest.main()
