"""Direct P1-00b characterization test for UploadSessionCreateRequest filename normalization."""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadSessionCreateRequest
from models.upload_session import UploadMediaType, UploadRoomKind


class TestUploadSessionFilenameNormalizationCharacterization(unittest.TestCase):
    """Characterize UploadSessionCreateRequest success path for padded file_name normalization."""

    def test_padded_filename_is_normalized_preserving_interior_space(self):
        request = UploadSessionCreateRequest(
            room_kind=UploadRoomKind.DIRECT,
            target_id=10,
            media_type=UploadMediaType.DOCUMENT,
            file_name="  quarterly report.pdf  ",
            mime_type="application/pdf",
            total_bytes=1024,
            chunk_size=512,
        )
        self.assertEqual(request.file_name, "quarterly report.pdf")
        self.assertEqual(request.room_kind, UploadRoomKind.DIRECT)
        self.assertEqual(request.target_id, 10)
        self.assertEqual(request.media_type, UploadMediaType.DOCUMENT)
        self.assertEqual(request.mime_type, "application/pdf")
        self.assertEqual(request.total_bytes, 1024)
        self.assertEqual(request.chunk_size, 512)


if __name__ == "__main__":
    unittest.main()
