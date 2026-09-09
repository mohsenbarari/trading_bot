"""Direct P1-00b characterization only, without canonical evidence promotion, executed-seed registry changes or Stage completion.

Characterization test for UploadSessionCreateRequest numeric-string total_bytes coercion.
"""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadSessionCreateRequest
from models.upload_session import UploadMediaType, UploadRoomKind


class TestUploadSessionTotalBytesNumericStringCharacterization(unittest.TestCase):
    """Characterize UploadSessionCreateRequest acceptance and coercion of numeric-string total_bytes."""

    def test_upload_session_total_bytes_numeric_string_coerced_to_int(self):
        request = UploadSessionCreateRequest(
            room_kind=UploadRoomKind.DIRECT,
            target_id=10,
            media_type=UploadMediaType.DOCUMENT,
            file_name="report.pdf",
            mime_type="application/pdf",
            total_bytes="1024",
            chunk_size=512,
        )

        self.assertEqual(request.total_bytes, 1024)
        self.assertIs(type(request.total_bytes), int)


if __name__ == "__main__":
    unittest.main()
