"""Characterization test for UploadSessionCreateRequest sha256_full omitted acceptance."""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadSessionCreateRequest
from models.upload_session import UploadMediaType, UploadRoomKind


class TestUploadSessionSha256FullOmittedCharacterization(unittest.TestCase):
    """Characterize acceptance and None default when sha256_full is omitted for UploadSessionCreateRequest."""

    def test_upload_session_sha256_full_omitted(self):
        request = UploadSessionCreateRequest(
            room_kind=UploadRoomKind.DIRECT,
            target_id=10,
            media_type=UploadMediaType.DOCUMENT,
            file_name="report.pdf",
            mime_type="application/pdf",
            total_bytes=1024,
            chunk_size=512,
        )
        self.assertIsNone(request.sha256_full)


if __name__ == "__main__":
    unittest.main()
