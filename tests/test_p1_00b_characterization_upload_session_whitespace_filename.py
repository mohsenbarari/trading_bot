"""Characterization test for UploadSessionCreateRequest whitespace file_name boundary."""

import unittest

import pydantic

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadSessionCreateRequest
from models.upload_session import UploadMediaType, UploadRoomKind


class TestUploadSessionWhitespaceFilenameCharacterization(unittest.TestCase):
    """Verify UploadSessionCreateRequest rejects whitespace-only file_name after trimming."""

    def test_whitespace_only_filename_is_rejected(self):
        with self.assertRaisesRegex(
            pydantic.ValidationError,
            "file_name is required",
        ):
            UploadSessionCreateRequest(
                room_kind=UploadRoomKind.DIRECT,
                target_id=10,
                media_type=UploadMediaType.DOCUMENT,
                file_name="   ",
                mime_type="application/pdf",
                total_bytes=1024,
                chunk_size=512,
            )


if __name__ == "__main__":
    unittest.main()
