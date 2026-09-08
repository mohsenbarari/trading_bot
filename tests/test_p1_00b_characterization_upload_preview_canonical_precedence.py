"""Direct P1-00b characterization test for UploadPreviewMetadata canonical precedence."""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadPreviewMetadata


class TestUploadPreviewMetadataCanonicalPrecedenceCharacterization(unittest.TestCase):
    """Characterize UploadPreviewMetadata canonical precedence over conflicting camelCase aliases."""

    def test_canonical_fields_take_precedence_over_conflicting_camel_case_aliases(self):
        payload = {
            "duration_ms": 1200,
            "durationMs": 4500,
            "album_index": 2,
            "albumIndex": 9,
        }
        pre_construction_copy = dict(payload)

        preview = UploadPreviewMetadata(**payload)

        self.assertEqual(preview.duration_ms, 1200)
        self.assertEqual(preview.album_index, 2)
        self.assertEqual(payload, pre_construction_copy)


if __name__ == "__main__":
    unittest.main()
