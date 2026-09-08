"""Characterization test for UploadSessionCreateRequest nonnumeric target_id failure boundary."""

import unittest

import pydantic

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadSessionCreateRequest
from models.upload_session import UploadMediaType, UploadRoomKind


class TestUploadSessionTargetIdNonnumericCharacterization(unittest.TestCase):
    """Characterize UploadSessionCreateRequest rejection of nonnumeric target_id."""

    def test_upload_session_target_id_nonnumeric_rejected(self):
        with self.assertRaises(pydantic.ValidationError) as ctx:
            UploadSessionCreateRequest(
                room_kind=UploadRoomKind.DIRECT,
                target_id="not-an-integer",
                media_type=UploadMediaType.DOCUMENT,
                file_name="report.pdf",
                mime_type="application/pdf",
                total_bytes=1024,
                chunk_size=512,
            )

        errors = ctx.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["loc"], ("target_id",))
        self.assertEqual(errors[0]["type"], "int_parsing")
        self.assertEqual(errors[0]["input"], "not-an-integer")


if __name__ == "__main__":
    unittest.main()
