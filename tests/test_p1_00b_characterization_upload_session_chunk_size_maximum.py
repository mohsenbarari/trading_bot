"""Characterization test for UploadSessionCreateRequest chunk_size maximum accepted boundary."""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadSessionCreateRequest
from models.upload_session import UploadMediaType, UploadRoomKind


class TestUploadSessionChunkSizeMaximumCharacterization(unittest.TestCase):
    """Characterize acceptance of chunk_size at 10 MiB maximum for UploadSessionCreateRequest."""

    def test_upload_session_chunk_size_maximum_accepted(self):
        request = UploadSessionCreateRequest(
            room_kind=UploadRoomKind.DIRECT,
            target_id=10,
            media_type=UploadMediaType.DOCUMENT,
            file_name="report.pdf",
            mime_type="application/pdf",
            total_bytes=20 * 1024 * 1024,
            chunk_size=10 * 1024 * 1024,
        )
        self.assertEqual(request.chunk_size, 10 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
