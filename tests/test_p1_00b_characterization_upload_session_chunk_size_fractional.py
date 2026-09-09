"""Characterization test for UploadSessionCreateRequest fractional chunk_size failure boundary."""

import unittest

import pydantic

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadSessionCreateRequest
from models.upload_session import UploadMediaType, UploadRoomKind


class TestUploadSessionChunkSizeFractionalCharacterization(unittest.TestCase):
    """Characterize UploadSessionCreateRequest rejection of fractional chunk_size."""

    def test_upload_session_chunk_size_fractional_rejected(self):
        with self.assertRaises(pydantic.ValidationError) as ctx:
            UploadSessionCreateRequest(
                room_kind=UploadRoomKind.DIRECT,
                target_id=10,
                media_type=UploadMediaType.DOCUMENT,
                file_name="report.pdf",
                mime_type="application/pdf",
                total_bytes=1024,
                chunk_size=512.5,
            )

        errors = ctx.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["loc"], ("chunk_size",))
        self.assertEqual(errors[0]["type"], "int_from_float")
        self.assertEqual(errors[0]["input"], 512.5)


if __name__ == "__main__":
    unittest.main()
