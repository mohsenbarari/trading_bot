"""Characterization test for PATCH upload-session chunk lookup failure."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat import patch_upload_session_chunk


class TestUploadChunkLookupFailureCharacterization(unittest.IsolatedAsyncioTestCase):
    async def test_chunk_lookup_failure_propagates_and_short_circuits(self) -> None:
        lookup_error = RuntimeError("Synthetic session lookup failure")

        synthetic_chunk = SimpleNamespace(
            read=AsyncMock(return_value=b"unused-chunk-data"),
            close=AsyncMock(return_value=None),
        )
        fake_db = object()
        fake_current_user = object()

        session_id = "session-test-id-lookup-001"
        resume_token = "token-test-resume-lookup-001"
        offset = 0
        is_last_chunk = False

        with patch(
            "api.routers.chat.get_upload_session_for_current_user",
            new_callable=AsyncMock,
            side_effect=lookup_error,
        ) as mock_get_upload_session, patch(
            "api.routers.chat.append_upload_chunk",
            new_callable=AsyncMock,
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

            self.assertIs(exc_info.exception, lookup_error)
            mock_get_upload_session.assert_awaited_once_with(
                fake_db,
                session_id=session_id,
                current_user=fake_current_user,
            )
            mock_append_upload_chunk.assert_not_called()
            mock_append_upload_chunk.assert_not_awaited()
            mock_publish_event.assert_not_called()
            mock_publish_event.assert_not_awaited()
            synthetic_chunk.read.assert_not_called()
            synthetic_chunk.read.assert_not_awaited()
            synthetic_chunk.close.assert_not_called()
            synthetic_chunk.close.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
