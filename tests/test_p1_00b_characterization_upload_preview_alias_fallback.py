"""Direct P1-00b characterization test for UploadPreviewMetadata camelCase alias fallback."""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadPreviewMetadata


class TestUploadPreviewMetadataAliasFallbackCharacterization(unittest.TestCase):
    """Characterize UploadPreviewMetadata camelCase alias fallback when canonical keys are absent."""

    def test_camel_case_aliases_fallback_when_canonical_keys_absent(self):
        payload = {
            "durationMs": 1250,
            "albumIndex": 2,
        }
        pre_construction_copy = dict(payload)

        preview = UploadPreviewMetadata(**payload)

        self.assertEqual(preview.duration_ms, 1250)
        self.assertEqual(preview.album_index, 2)
        self.assertEqual(payload, pre_construction_copy)


if __name__ == "__main__":
    unittest.main()
