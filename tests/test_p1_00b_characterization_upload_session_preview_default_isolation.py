"""Direct P1-00b characterization test for UploadSessionCreateRequest default preview metadata isolation."""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import (
    UploadPreviewMetadata,
    UploadSessionCreateRequest,
)
from models.upload_session import UploadMediaType, UploadRoomKind


class TestUploadSessionPreviewDefaultIsolationCharacterization(unittest.TestCase):
    """Characterize UploadSessionCreateRequest independent default preview metadata."""

    def test_default_preview_metadata_isolated_per_request(self):
        first_request = UploadSessionCreateRequest(
            room_kind=UploadRoomKind.DIRECT,
            target_id=10,
            media_type=UploadMediaType.DOCUMENT,
            file_name="report.pdf",
            mime_type="application/pdf",
            total_bytes=1024,
            chunk_size=512,
        )
        second_request = UploadSessionCreateRequest(
            room_kind=UploadRoomKind.DIRECT,
            target_id=10,
            media_type=UploadMediaType.DOCUMENT,
            file_name="report.pdf",
            mime_type="application/pdf",
            total_bytes=1024,
            chunk_size=512,
        )

        self.assertIsInstance(first_request.preview_metadata, UploadPreviewMetadata)
        self.assertIsInstance(second_request.preview_metadata, UploadPreviewMetadata)
        self.assertIsNot(
            first_request.preview_metadata,
            second_request.preview_metadata,
        )

        self.assertIsNone(first_request.preview_metadata.width)
        self.assertIsNone(first_request.preview_metadata.height)
        self.assertIsNone(first_request.preview_metadata.duration_ms)
        self.assertIsNone(first_request.preview_metadata.thumbnail)
        self.assertIsNone(first_request.preview_metadata.caption)
        self.assertIsNone(first_request.preview_metadata.album_index)
        self.assertIsNone(first_request.preview_metadata.waveform)

        self.assertIsNone(second_request.preview_metadata.width)
        self.assertIsNone(second_request.preview_metadata.height)
        self.assertIsNone(second_request.preview_metadata.duration_ms)
        self.assertIsNone(second_request.preview_metadata.thumbnail)
        self.assertIsNone(second_request.preview_metadata.caption)
        self.assertIsNone(second_request.preview_metadata.album_index)
        self.assertIsNone(second_request.preview_metadata.waveform)

        first_request.preview_metadata.width = 1920
        first_request.preview_metadata.caption = "first report preview"

        self.assertEqual(first_request.preview_metadata.width, 1920)
        self.assertEqual(first_request.preview_metadata.caption, "first report preview")
        self.assertIsNone(second_request.preview_metadata.width)
        self.assertIsNone(second_request.preview_metadata.caption)


if __name__ == "__main__":
    unittest.main()
