"""Characterization test for UploadSessionCreateRequest omitted media_type failure boundary."""

import unittest

import pydantic

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadSessionCreateRequest
from models.upload_session import UploadRoomKind


class TestUploadSessionMediaTypeMissingCharacterization(unittest.TestCase):
    """Characterize UploadSessionCreateRequest rejection of omitted media_type."""

    def test_omitted_media_type_is_rejected(self):
        """Characterize ValidationError when media_type is omitted from UploadSessionCreateRequest."""
        with self.assertRaises(pydantic.ValidationError) as ctx:
            UploadSessionCreateRequest(
                room_kind=UploadRoomKind.DIRECT,
                target_id=10,
                file_name="report.pdf",
                mime_type="application/pdf",
                total_bytes=1024,
                chunk_size=512,
            )

        errors = ctx.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["loc"], ("media_type",))
        self.assertEqual(errors[0]["type"], "missing")


if __name__ == "__main__":
    unittest.main()
