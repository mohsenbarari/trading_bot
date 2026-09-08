"""Characterization test for UploadSessionCreateRequest omitted target_id failure boundary."""

import unittest

import pydantic

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadSessionCreateRequest
from models.upload_session import UploadMediaType, UploadRoomKind


class TestUploadSessionTargetIdMissingCharacterization(unittest.TestCase):
    """Characterize UploadSessionCreateRequest rejection of omitted target_id."""

    def test_omitted_target_id_is_rejected(self):
        """Characterize ValidationError when target_id is omitted from UploadSessionCreateRequest."""
        with self.assertRaises(pydantic.ValidationError) as ctx:
            UploadSessionCreateRequest(
                room_kind=UploadRoomKind.DIRECT,
                media_type=UploadMediaType.DOCUMENT,
                file_name="report.pdf",
                mime_type="application/pdf",
                total_bytes=1024,
                chunk_size=512,
            )

        errors = ctx.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["loc"], ("target_id",))
        self.assertEqual(errors[0]["type"], "missing")


if __name__ == "__main__":
    unittest.main()
