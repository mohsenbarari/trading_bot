"""Characterization test for UploadSessionCreateRequest unsupported media_type boundary."""

import unittest

import pydantic

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadSessionCreateRequest
from models.upload_session import UploadRoomKind


class TestUploadSessionMediaTypeUnsupportedCharacterization(unittest.TestCase):
    """Characterize rejection of unsupported media_type for UploadSessionCreateRequest."""

    def test_upload_session_media_type_unsupported_rejected(self):
        with self.assertRaises(pydantic.ValidationError) as ctx:
            UploadSessionCreateRequest(
                room_kind=UploadRoomKind.DIRECT,
                target_id=10,
                media_type="audio",
                file_name="report.pdf",
                mime_type="application/pdf",
                total_bytes=1024,
                chunk_size=512,
            )

        errors = ctx.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["loc"], ("media_type",))
        self.assertEqual(errors[0]["type"], "enum")
        self.assertEqual(errors[0]["input"], "audio")


if __name__ == "__main__":
    unittest.main()
