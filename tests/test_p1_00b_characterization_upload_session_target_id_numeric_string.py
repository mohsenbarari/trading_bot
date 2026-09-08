"""Characterization test for UploadSessionCreateRequest numeric-string target_id coercion."""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadSessionCreateRequest
from models.upload_session import UploadMediaType, UploadRoomKind


class TestUploadSessionTargetIdNumericStringCharacterization(unittest.TestCase):
    """Characterize UploadSessionCreateRequest acceptance and coercion of numeric-string target_id."""

    def test_upload_session_target_id_numeric_string_coerced_to_int(self):
        request = UploadSessionCreateRequest(
            room_kind=UploadRoomKind.DIRECT,
            target_id="42",
            media_type=UploadMediaType.DOCUMENT,
            file_name="report.pdf",
            mime_type="application/pdf",
            total_bytes=1024,
            chunk_size=512,
        )

        self.assertEqual(request.target_id, 42)
        self.assertIs(type(request.target_id), int)


if __name__ == "__main__":
    unittest.main()
