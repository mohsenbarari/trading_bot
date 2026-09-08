"""Direct P1-00b characterization test for UploadPreviewMetadata canonical null precedence."""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadPreviewMetadata


class TestUploadPreviewMetadataNullPrecedenceCharacterization(unittest.TestCase):
    """Characterize UploadPreviewMetadata canonical null precedence over populated camelCase aliases."""

    def test_canonical_null_takes_precedence_over_populated_camel_case_aliases(self):
        payload = {
            "duration_ms": None,
            "durationMs": 4500,
            "album_index": None,
            "albumIndex": 9,
        }
        pre_construction_copy = dict(payload)

        preview = UploadPreviewMetadata(**payload)

        self.assertIsNone(preview.duration_ms)
        self.assertIsNone(preview.album_index)
        self.assertEqual(payload, pre_construction_copy)


if __name__ == "__main__":
    unittest.main()
