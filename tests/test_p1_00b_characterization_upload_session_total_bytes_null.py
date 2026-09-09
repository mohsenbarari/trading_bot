"""Characterization test for UploadSessionCreateRequest explicit null total_bytes failure boundary."""

import unittest

import pydantic

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadSessionCreateRequest
from models.upload_session import UploadMediaType, UploadRoomKind


class TestUploadSessionTotalBytesNullCharacterization(unittest.TestCase):
    """Characterize UploadSessionCreateRequest rejection of total_bytes=None."""

    def test_upload_session_total_bytes_null_rejected(self):
        with self.assertRaises(pydantic.ValidationError) as ctx:
            UploadSessionCreateRequest(
                room_kind=UploadRoomKind.DIRECT,
                target_id=10,
                media_type=UploadMediaType.DOCUMENT,
                file_name="report.pdf",
                mime_type="application/pdf",
                total_bytes=None,
                chunk_size=512,
            )

        errors = ctx.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["loc"], ("total_bytes",))
        self.assertEqual(errors[0]["type"], "int_type")
        self.assertIsNone(errors[0]["input"])


if __name__ == "__main__":
    unittest.main()
