"""Characterization test for UploadSessionCreateRequest total_bytes maximum accepted boundary."""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadSessionCreateRequest
from models.upload_session import UploadMediaType, UploadRoomKind


class TestUploadSessionTotalBytesMaximumCharacterization(unittest.TestCase):
    """Characterize acceptance of total_bytes at 50 MB maximum for UploadSessionCreateRequest."""

    def test_upload_session_total_bytes_maximum_accepted(self):
        request = UploadSessionCreateRequest(
            room_kind=UploadRoomKind.DIRECT,
            target_id=10,
            media_type=UploadMediaType.DOCUMENT,
            file_name="report.pdf",
            mime_type="application/pdf",
            total_bytes=50 * 1024 * 1024,
            chunk_size=512,
        )
        self.assertEqual(request.total_bytes, 50 * 1024 * 1024)


if __name__ == "__main__":
    unittest.main()
