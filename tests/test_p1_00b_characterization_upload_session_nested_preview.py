"""Direct P1-00b characterization test for UploadSessionCreateRequest nested preview metadata."""

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


class TestUploadSessionNestedPreviewCharacterization(unittest.TestCase):
    """Characterize UploadSessionCreateRequest nested preview metadata alias mapping and validation."""

    def test_nested_preview_metadata_aliases_and_caption_normalization(self):
        preview_input = {
            "durationMs": 1250,
            "albumIndex": 2,
            "caption": "   clip caption   ",
            "width": 640,
        }
        pre_construction_copy = dict(preview_input)

        request = UploadSessionCreateRequest(
            room_kind=UploadRoomKind.DIRECT,
            target_id=10,
            media_type=UploadMediaType.VIDEO,
            file_name="clip.mp4",
            mime_type="video/mp4",
            total_bytes=1024,
            chunk_size=512,
            preview_metadata=preview_input,
        )

        self.assertIsInstance(request.preview_metadata, UploadPreviewMetadata)
        self.assertEqual(request.preview_metadata.duration_ms, 1250)
        self.assertEqual(request.preview_metadata.album_index, 2)
        self.assertEqual(request.preview_metadata.caption, "clip caption")
        self.assertEqual(request.preview_metadata.width, 640)

        self.assertEqual(preview_input, pre_construction_copy)
        self.assertEqual(preview_input["caption"], "   clip caption   ")
        self.assertIn("durationMs", preview_input)
        self.assertIn("albumIndex", preview_input)
        self.assertNotIn("duration_ms", preview_input)
        self.assertNotIn("album_index", preview_input)


if __name__ == "__main__":
    unittest.main()
