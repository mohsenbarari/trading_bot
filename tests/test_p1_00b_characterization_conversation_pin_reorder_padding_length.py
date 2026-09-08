"""Direct P1-00b characterization under Assignment WA-P1-00B-FREEZE-20260904-001.

This direct schema characterization does not update the executed-seed registry,
reduce canonical blockers, promote evidence, or complete the Stage.
"""

import unittest

from pydantic import ValidationError

from tests.test_p1_00_characterization_network_isolation import (
    install_p1_00_network_fence,
)

install_p1_00_network_fence()

from api.routers.chat_schemas import ConversationPinReorderUpdateRequest


class TestConversationPinReorderPaddingLength(unittest.TestCase):
    """Characterize raw direction length rejection before whitespace normalization."""

    def test_padded_direction_rejected_by_max_length(self):
        with self.assertRaises(ValidationError) as ctx:
            ConversationPinReorderUpdateRequest(direction=" down ")

        errors = ctx.exception.errors()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["loc"], ("direction",))
        self.assertEqual(errors[0]["type"], "string_too_long")
        self.assertEqual(errors[0]["input"], " down ")
        self.assertEqual(errors[0]["ctx"]["max_length"], 4)
        self.assertEqual(errors[0]["ctx"], {"max_length": 4})

        valid_request = ConversationPinReorderUpdateRequest(direction="down")
        self.assertEqual(valid_request.direction, "down")


if __name__ == "__main__":
    unittest.main()
