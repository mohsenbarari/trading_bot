"""Characterization test for UploadSessionCreateRequest file_name maximum length boundary."""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadSessionCreateRequest
from models.upload_session import UploadMediaType, UploadRoomKind


class TestUploadSessionFilenameMaximumCharacterization(unittest.TestCase):
    """Characterize acceptance and preservation of 255-character Unicode file_name for UploadSessionCreateRequest."""

    def test_upload_session_filename_maximum_accepted(self):
        file_name = ("ژ" * 251) + ".pdf"
        self.assertEqual(len(file_name), 255)
        self.assertGreater(len(file_name.encode("utf-8")), 255)

        request = UploadSessionCreateRequest(
            room_kind=UploadRoomKind.DIRECT,
            target_id=10,
            media_type=UploadMediaType.DOCUMENT,
            file_name=file_name,
            mime_type="application/pdf",
            total_bytes=1024,
            chunk_size=512,
        )

        self.assertEqual(request.file_name, file_name)
        self.assertEqual(len(request.file_name), 255)
        self.assertEqual(request.room_kind, UploadRoomKind.DIRECT)
        self.assertEqual(request.target_id, 10)
        self.assertEqual(request.media_type, UploadMediaType.DOCUMENT)
        self.assertEqual(request.mime_type, "application/pdf")
        self.assertEqual(request.total_bytes, 1024)
        self.assertEqual(request.chunk_size, 512)
        self.assertIsNone(request.batch_id)
        self.assertIsNone(request.sha256_full)


if __name__ == "__main__":
    unittest.main()
