"""Characterization test for UploadSessionCreateRequest mime_type empty boundary."""

import unittest

import pydantic

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadSessionCreateRequest
from models.upload_session import UploadMediaType, UploadRoomKind


class TestUploadSessionMimeTypeEmptyCharacterization(unittest.TestCase):
    """Characterize rejection of empty mime_type for UploadSessionCreateRequest."""

    def test_upload_session_mime_type_empty_rejected(self):
        with self.assertRaises(pydantic.ValidationError) as ctx:
            UploadSessionCreateRequest(
                room_kind=UploadRoomKind.DIRECT,
                target_id=10,
                media_type=UploadMediaType.DOCUMENT,
                file_name="report.pdf",
                mime_type="",
                total_bytes=1024,
                chunk_size=512,
            )

        errors = ctx.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["loc"], ("mime_type",))
        self.assertEqual(errors[0]["type"], "string_too_short")
        self.assertEqual(errors[0]["ctx"]["min_length"], 1)
        self.assertEqual(errors[0]["input"], "")


if __name__ == "__main__":
    unittest.main()
