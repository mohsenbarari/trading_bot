"""Characterization test for UploadSessionCreateRequest unsupported room_kind boundary."""

import unittest

import pydantic

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadSessionCreateRequest
from models.upload_session import UploadMediaType


class TestUploadSessionRoomKindUnsupportedCharacterization(unittest.TestCase):
    """Characterize rejection of unsupported room_kind for UploadSessionCreateRequest."""

    def test_upload_session_room_kind_unsupported_rejected(self):
        with self.assertRaises(pydantic.ValidationError) as ctx:
            UploadSessionCreateRequest(
                room_kind="channel",
                target_id=10,
                media_type=UploadMediaType.DOCUMENT,
                file_name="report.pdf",
                mime_type="application/pdf",
                total_bytes=1024,
                chunk_size=512,
            )

        errors = ctx.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["loc"], ("room_kind",))
        self.assertEqual(errors[0]["type"], "enum")
        self.assertEqual(errors[0]["input"], "channel")


if __name__ == "__main__":
    unittest.main()
