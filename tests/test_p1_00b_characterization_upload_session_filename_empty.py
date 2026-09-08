"""Characterization test for UploadSessionCreateRequest empty file_name failure boundary."""

import unittest

import pydantic

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadSessionCreateRequest
from models.upload_session import UploadMediaType, UploadRoomKind


class TestUploadSessionFilenameEmptyCharacterization(unittest.TestCase):
    """Characterize UploadSessionCreateRequest rejection of empty file_name."""

    def test_empty_filename_is_rejected(self):
        with self.assertRaises(pydantic.ValidationError) as ctx:
            UploadSessionCreateRequest(
                file_name="",
                room_kind=UploadRoomKind.DIRECT,
                target_id=10,
                media_type=UploadMediaType.DOCUMENT,
                mime_type="application/pdf",
                total_bytes=1024,
                chunk_size=512,
            )

        errors = ctx.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["loc"], ("file_name",))
        self.assertEqual(errors[0]["type"], "string_too_short")
        self.assertEqual(errors[0]["ctx"]["min_length"], 1)
        self.assertEqual(errors[0]["input"], "")


if __name__ == "__main__":
    unittest.main()
