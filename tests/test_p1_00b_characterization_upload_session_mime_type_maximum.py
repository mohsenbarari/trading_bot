"""Characterization test for UploadSessionCreateRequest mime_type maximum length boundary."""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadSessionCreateRequest
from models.upload_session import UploadMediaType, UploadRoomKind


class TestUploadSessionMimeTypeMaximumCharacterization(unittest.TestCase):
    """Characterize acceptance and preservation of 100-character mime_type for UploadSessionCreateRequest."""

    def test_upload_session_mime_type_maximum_accepted(self):
        mime_type = " " + ("a" * 98) + " "
        self.assertEqual(len(mime_type), 100)

        request = UploadSessionCreateRequest(
            room_kind=UploadRoomKind.DIRECT,
            target_id=10,
            media_type=UploadMediaType.DOCUMENT,
            file_name="report.pdf",
            mime_type=mime_type,
            total_bytes=1024,
            chunk_size=512,
        )

        self.assertEqual(request.mime_type, mime_type)
        self.assertEqual(len(request.mime_type), 100)
        self.assertEqual(request.room_kind, UploadRoomKind.DIRECT)
        self.assertEqual(request.target_id, 10)
        self.assertEqual(request.media_type, UploadMediaType.DOCUMENT)
        self.assertEqual(request.file_name, "report.pdf")
        self.assertEqual(request.total_bytes, 1024)
        self.assertEqual(request.chunk_size, 512)
        self.assertIsNone(request.batch_id)
        self.assertIsNone(request.sha256_full)


if __name__ == "__main__":
    unittest.main()
