"""Characterization test for UploadSessionCreateRequest sha256_full lower-boundary acceptance."""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadSessionCreateRequest
from models.upload_session import UploadMediaType, UploadRoomKind


class TestUploadSessionSha256FullMinimumCharacterization(unittest.TestCase):
    """Characterize acceptance of sha256_full at the 16-character lower boundary for UploadSessionCreateRequest."""

    def test_upload_session_sha256_full_minimum_accepted(self):
        min_sha256 = "a" * 16
        request = UploadSessionCreateRequest(
            room_kind=UploadRoomKind.DIRECT,
            target_id=10,
            media_type=UploadMediaType.DOCUMENT,
            file_name="report.pdf",
            mime_type="application/pdf",
            total_bytes=1024,
            chunk_size=512,
            sha256_full=min_sha256,
        )
        self.assertEqual(request.sha256_full, min_sha256)


if __name__ == "__main__":
    unittest.main()
