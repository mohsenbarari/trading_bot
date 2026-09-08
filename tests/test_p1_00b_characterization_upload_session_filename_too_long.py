"""Characterization test for UploadSessionCreateRequest too-long file_name boundary."""

import unittest

import pydantic

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadSessionCreateRequest
from models.upload_session import UploadMediaType, UploadRoomKind


class TestUploadSessionFilenameTooLongCharacterization(unittest.TestCase):
    """Characterize rejection of file_name exceeding 255 characters for UploadSessionCreateRequest."""

    def test_upload_session_filename_too_long_rejected(self):
        with self.assertRaises(pydantic.ValidationError) as ctx:
            UploadSessionCreateRequest(
                room_kind=UploadRoomKind.DIRECT,
                target_id=10,
                media_type=UploadMediaType.DOCUMENT,
                file_name="a" * 256,
                mime_type="application/pdf",
                total_bytes=1024,
                chunk_size=512,
            )

        errors = ctx.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["loc"], ("file_name",))
        self.assertIn("255", errors[0]["msg"])


if __name__ == "__main__":
    unittest.main()
