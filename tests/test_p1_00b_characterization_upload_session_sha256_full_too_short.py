"""Characterization test for UploadSessionCreateRequest sha256_full minimum-length boundary."""

import unittest

import pydantic

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadSessionCreateRequest
from models.upload_session import UploadMediaType, UploadRoomKind


class TestUploadSessionSha256FullTooShortCharacterization(unittest.TestCase):
    """Characterize rejection of sha256_full shorter than 16 characters for UploadSessionCreateRequest."""

    def test_upload_session_sha256_full_too_short_rejected(self):
        too_short_sha256 = "a" * 15
        with self.assertRaises(pydantic.ValidationError) as ctx:
            UploadSessionCreateRequest(
                room_kind=UploadRoomKind.DIRECT,
                target_id=10,
                media_type=UploadMediaType.DOCUMENT,
                file_name="report.pdf",
                mime_type="application/pdf",
                total_bytes=1024,
                chunk_size=512,
                sha256_full=too_short_sha256,
            )

        errors = ctx.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["loc"], ("sha256_full",))
        self.assertEqual(errors[0]["type"], "string_too_short")
        self.assertEqual(errors[0]["ctx"]["min_length"], 16)
        self.assertEqual(errors[0]["input"], too_short_sha256)


if __name__ == "__main__":
    unittest.main()
