"""Characterization test for UploadSessionCreateRequest explicit null chunk_size failure boundary."""

import unittest

import pydantic

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadSessionCreateRequest
from models.upload_session import UploadMediaType, UploadRoomKind


class TestUploadSessionChunkSizeNullCharacterization(unittest.TestCase):
    """Characterize UploadSessionCreateRequest rejection of chunk_size=None."""

    def test_upload_session_chunk_size_null_rejected(self):
        with self.assertRaises(pydantic.ValidationError) as ctx:
            UploadSessionCreateRequest(
                room_kind=UploadRoomKind.DIRECT,
                target_id=10,
                media_type=UploadMediaType.DOCUMENT,
                file_name="report.pdf",
                mime_type="application/pdf",
                total_bytes=1024,
                chunk_size=None,
            )

        errors = ctx.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["loc"], ("chunk_size",))
        self.assertEqual(errors[0]["type"], "int_type")
        self.assertIsNone(errors[0]["input"])


if __name__ == "__main__":
    unittest.main()
