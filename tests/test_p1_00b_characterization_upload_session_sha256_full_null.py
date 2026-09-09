"""Characterization test for UploadSessionCreateRequest sha256_full explicit None acceptance."""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadSessionCreateRequest
from models.upload_session import UploadMediaType, UploadRoomKind


class TestUploadSessionSha256FullNullCharacterization(unittest.TestCase):
    """Characterize acceptance and None value when sha256_full is explicitly None for UploadSessionCreateRequest."""

    def test_upload_session_sha256_full_null_accepted(self):
        request = UploadSessionCreateRequest(
            room_kind=UploadRoomKind.DIRECT,
            target_id=10,
            media_type=UploadMediaType.DOCUMENT,
            file_name="report.pdf",
            mime_type="application/pdf",
            total_bytes=1024,
            chunk_size=512,
            sha256_full=None,
        )
        self.assertIsNone(request.sha256_full)


if __name__ == "__main__":
    unittest.main()
