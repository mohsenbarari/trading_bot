"""Characterization test for PATCH upload-session chunk append failure."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import patch_upload_session_chunk


class TestUploadChunkAppendFailureCharacterization(unittest.IsolatedAsyncioTestCase):
    async def test_chunk_append_failure_propagates_and_prevents_publish(self) -> None:
        append_error = RuntimeError("Synthetic chunk append failure")
        chunk_payload = b"synthetic-append-chunk-data"

        synthetic_chunk = SimpleNamespace(
            read=AsyncMock(return_value=chunk_payload),
            close=AsyncMock(return_value=None),
        )
        fake_db = object()
        fake_current_user = object()
        sentinel_session = object()

        session_id = "session-test-id-001"
        resume_token = "token-test-resume-001"
        offset = 4096
        is_last_chunk = True

        async def fake_append_upload_chunk(*args: object, **kwargs: object) -> object:
            self.assertEqual(synthetic_chunk.read.await_count, 1)
            self.assertEqual(synthetic_chunk.close.await_count, 1)
            synthetic_chunk.read.assert_awaited_once_with()
            synthetic_chunk.close.assert_awaited_once_with()
            raise append_error

        with patch(
            "api.routers.chat.get_upload_session_for_current_user",
            new_callable=AsyncMock,
            return_value=sentinel_session,
        ) as mock_get_upload_session, patch(
            "api.routers.chat.append_upload_chunk",
            new_callable=AsyncMock,
            side_effect=fake_append_upload_chunk,
        ) as mock_append_upload_chunk, patch(
            "api.routers.chat._publish_upload_session_runtime_event",
            new_callable=AsyncMock,
        ) as mock_publish_event:
            with self.assertRaises(RuntimeError) as exc_info:
                await patch_upload_session_chunk(
                    session_id=session_id,
                    resume_token=resume_token,
                    offset=offset,
                    is_last_chunk=is_last_chunk,
                    chunk=synthetic_chunk,
                    current_user=fake_current_user,
                    db=fake_db,
                )

            self.assertIs(exc_info.exception, append_error)
            mock_get_upload_session.assert_awaited_once_with(
                fake_db,
                session_id=session_id,
                current_user=fake_current_user,
            )
            synthetic_chunk.read.assert_awaited_once_with()
            synthetic_chunk.close.assert_awaited_once_with()

            mock_append_upload_chunk.assert_awaited_once_with(
                fake_db,
                session=sentinel_session,
                resume_token=resume_token,
                offset=offset,
                chunk_bytes=chunk_payload,
                is_last_chunk=is_last_chunk,
            )
            mock_publish_event.assert_not_called()
            mock_publish_event.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
