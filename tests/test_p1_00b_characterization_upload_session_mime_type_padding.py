"""Direct P1-00b characterization test for UploadSessionCreateRequest MIME type padding preservation."""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadSessionCreateRequest
from models.upload_session import UploadMediaType, UploadRoomKind


class TestUploadSessionMimeTypePaddingCharacterization(unittest.TestCase):
    """Characterize UploadSessionCreateRequest preservation of padded mime_type."""

    def test_padded_mime_type_is_preserved(self):
        request = UploadSessionCreateRequest(
            room_kind=UploadRoomKind.DIRECT,
            target_id=10,
            media_type=UploadMediaType.DOCUMENT,
            file_name="  quarterly report.pdf  ",
            mime_type="  application/pdf \t",
            total_bytes=1024,
            chunk_size=512,
        )
        self.assertEqual(request.mime_type, "  application/pdf \t")
        self.assertEqual(request.file_name, "quarterly report.pdf")
        self.assertEqual(request.room_kind, UploadRoomKind.DIRECT)
        self.assertEqual(request.target_id, 10)
        self.assertEqual(request.media_type, UploadMediaType.DOCUMENT)
        self.assertEqual(request.total_bytes, 1024)
        self.assertEqual(request.chunk_size, 512)


if __name__ == "__main__":
    unittest.main()
