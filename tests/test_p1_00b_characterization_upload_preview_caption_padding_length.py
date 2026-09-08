"""Characterization test for UploadPreviewMetadata caption padding length failure boundary."""

import unittest

import pydantic

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import UploadPreviewMetadata


class TestUploadPreviewMetadataCaptionPaddingLengthCharacterization(unittest.TestCase):
    """Characterize UploadPreviewMetadata caption raw length validation before trimming."""

    def test_caption_with_padding_exceeding_max_length_is_rejected(self):
        caption = " " + ("a" * 3999) + " "
        self.assertEqual(len(caption), 4001)
        self.assertEqual(len(caption.strip()), 3999)

        with self.assertRaises(pydantic.ValidationError) as ctx:
            UploadPreviewMetadata(caption=caption)

        errors = ctx.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["loc"], ("caption",))
        self.assertEqual(errors[0]["type"], "string_too_long")
        self.assertEqual(errors[0]["ctx"]["max_length"], 4000)
        self.assertEqual(errors[0]["input"], caption)


if __name__ == "__main__":
    unittest.main()
