"""Characterization test for UploadSessionCreateRequest omitted room_kind failure boundary."""

import unittest

import pydantic

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadSessionCreateRequest
from models.upload_session import UploadMediaType


class TestUploadSessionRoomKindMissingCharacterization(unittest.TestCase):
    """Characterize UploadSessionCreateRequest rejection of omitted room_kind."""

    def test_omitted_room_kind_is_rejected(self):
        """Characterize ValidationError when room_kind is omitted from UploadSessionCreateRequest."""
        with self.assertRaises(pydantic.ValidationError) as ctx:
            UploadSessionCreateRequest(
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
        self.assertEqual(errors[0]["type"], "missing")


if __name__ == "__main__":
    unittest.main()
