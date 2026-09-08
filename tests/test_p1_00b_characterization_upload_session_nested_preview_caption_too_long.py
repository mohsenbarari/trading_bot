"""Characterization test for UploadSessionCreateRequest nested preview caption length failure boundary."""

import unittest

import pydantic

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadSessionCreateRequest
from models.upload_session import UploadMediaType, UploadRoomKind


class TestUploadSessionNestedPreviewCaptionTooLongCharacterization(unittest.TestCase):
    """Characterize UploadSessionCreateRequest rejection of overlong nested preview caption."""

    def test_nested_preview_caption_exceeding_raw_max_length_is_rejected(self):
        caption = " " + ("a" * 3999) + " "
        self.assertEqual(len(caption), 4001)
        self.assertEqual(len(caption.strip()), 3999)

        preview_input = {
            "caption": caption,
            "durationMs": 1250,
            "albumIndex": 2,
        }
        pre_construction_copy = dict(preview_input)

        with self.assertRaises(pydantic.ValidationError) as ctx:
            UploadSessionCreateRequest(
                room_kind=UploadRoomKind.DIRECT,
                target_id=10,
                media_type=UploadMediaType.VIDEO,
                file_name="clip.mp4",
                mime_type="video/mp4",
                total_bytes=1024,
                chunk_size=512,
                preview_metadata=preview_input,
            )

        errors = ctx.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["loc"], ("preview_metadata", "caption"))
        self.assertEqual(errors[0]["type"], "string_too_long")
        self.assertEqual(errors[0]["ctx"]["max_length"], 4000)
        self.assertEqual(errors[0]["input"], caption)

        self.assertEqual(preview_input, pre_construction_copy)
        self.assertEqual(preview_input["caption"], caption)
        self.assertIn("durationMs", preview_input)
        self.assertIn("albumIndex", preview_input)
        self.assertNotIn("duration_ms", preview_input)
        self.assertNotIn("album_index", preview_input)


if __name__ == "__main__":
    unittest.main()
