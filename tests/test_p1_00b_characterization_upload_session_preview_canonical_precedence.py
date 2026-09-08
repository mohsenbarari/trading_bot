"""Direct P1-00b characterization test for UploadSessionCreateRequest nested preview metadata canonical key precedence."""

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


class TestUploadSessionPreviewCanonicalPrecedenceCharacterization(unittest.TestCase):
    """Characterize UploadSessionCreateRequest nested preview metadata canonical key precedence over aliases."""

    def test_nested_preview_metadata_canonical_key_precedence(self):
        preview_input = {
            "duration_ms": 0,
            "durationMs": 1250,
            "album_index": 0,
            "albumIndex": 2,
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
        self.assertEqual(request.preview_metadata.duration_ms, 0)
        self.assertEqual(request.preview_metadata.album_index, 0)

        self.assertEqual(preview_input, pre_construction_copy)
        self.assertEqual(preview_input["duration_ms"], 0)
        self.assertEqual(preview_input["durationMs"], 1250)
        self.assertEqual(preview_input["album_index"], 0)
        self.assertEqual(preview_input["albumIndex"], 2)


if __name__ == "__main__":
    unittest.main()
