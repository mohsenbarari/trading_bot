"""Direct P1-00b characterization under Assignment WA-P1-00B-FREEZE-20260904-001.

This direct schema characterization neither updates executed-seed registry nor
reduces canonical blockers, promotes evidence, or completes the Stage.
"""

import unittest

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import ConversationPinReorderUpdateRequest


class TestConversationPinReorderNormalization(unittest.TestCase):
    """Characterize direction normalization in conversation pin reorder update requests."""

    def test_direction_normalization(self):
        cases = [
            (" UP ", "up"),
            ("UP", "up"),
            ("DOWN", "down"),
        ]
        for raw_direction, expected_direction in cases:
            with self.subTest(direction=raw_direction):
                request = ConversationPinReorderUpdateRequest(direction=raw_direction)
                self.assertEqual(request.direction, expected_direction)


if __name__ == "__main__":
    unittest.main()
